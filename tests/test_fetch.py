import hashlib
import json
from pathlib import Path
from typing import Literal

import httpx

from links_garden.config import Settings
from links_garden.fetch import Fetcher, FetchResult


def _settings(
    tmp_path: Path,
    *,
    fetch_backend: Literal["firecrawl", "direct"] = "firecrawl",
    firecrawl_api_key: str = "test-firecrawl-key",
    max_fetches_per_run: int = 50,
) -> Settings:
    return Settings(
        _env_file=None,
        fetch_backend=fetch_backend,
        firecrawl_api_key=firecrawl_api_key,  # type: ignore[arg-type]
        fetch_cache_dir=tmp_path / "cache",
        max_fetches_per_run=max_fetches_per_run,
    )


def _cache_path(settings: Settings, url: str) -> Path:
    digest = hashlib.sha256(url.encode()).hexdigest()
    return settings.fetch_cache_dir / f"{digest}.json"


def _firecrawl_ok(body: str = "<html/>") -> httpx.Response:
    return httpx.Response(200, json={"success": True, "data": {"rawHtml": body}})


def test_cache_hit_returns_from_cache_and_skips_transport(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    url = "https://example.com/a"
    cache_path = _cache_path(settings, url)
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(
        json.dumps(
            {
                "url": url,
                "final_url": url,
                "fetched_at": "2026-01-01T00:00:00+00:00",
                "status": "ok",
                "body": "cached body",
                "content_type": "text/html",
                "error": None,
            }
        )
    )

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not call the network on a cache hit")

    fetcher = Fetcher(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))

    result = fetcher.fetch(url)

    assert result == FetchResult(
        url=url,
        final_url=url,
        status="ok",
        body="cached body",
        content_type="text/html",
        error=None,
        from_cache=True,
    )
    assert fetcher.spent == 0


def test_cache_miss_fetches_then_second_call_hits_cache(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    url = "https://example.com/b"
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _firecrawl_ok("<html>b</html>")

    fetcher = Fetcher(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))

    first = fetcher.fetch(url)
    second = fetcher.fetch(url)

    assert calls == 1
    assert first.from_cache is False
    assert first.status == "ok"
    assert first.body == "<html>b</html>"
    assert second.from_cache is True
    assert second.body == "<html>b</html>"


def test_at_cap_reflects_spent_versus_the_configured_max(tmp_path: Path) -> None:
    settings = _settings(tmp_path, max_fetches_per_run=1)
    fetcher = Fetcher(settings)

    assert fetcher.at_cap is False

    fetcher.spent = 1

    assert fetcher.at_cap is True


def test_cap_reached_returns_skipped_without_request(tmp_path: Path) -> None:
    settings = _settings(tmp_path, max_fetches_per_run=1)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _firecrawl_ok()

    fetcher = Fetcher(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))

    first = fetcher.fetch("https://example.com/c1")
    second = fetcher.fetch("https://example.com/c2")

    assert calls == 1
    assert first.status == "ok"
    assert second == FetchResult(
        url="https://example.com/c2",
        final_url="https://example.com/c2",
        status="skipped",
        body=None,
        content_type=None,
        error=None,
        from_cache=False,
    )


def test_skipped_result_is_not_written_to_cache(tmp_path: Path) -> None:
    settings = _settings(tmp_path, max_fetches_per_run=1)

    def handler(request: httpx.Request) -> httpx.Response:
        return _firecrawl_ok()

    fetcher = Fetcher(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))
    fetcher.fetch("https://example.com/d1")

    skipped_url = "https://example.com/d2"
    result = fetcher.fetch(skipped_url)

    assert result.status == "skipped"
    assert not _cache_path(settings, skipped_url).exists()


def test_firecrawl_refusal_at_http_200_becomes_failed(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    refusal = "We apologize for the inconvenience but we do not support this site..."

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "error": refusal})

    fetcher = Fetcher(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))

    result = fetcher.fetch("https://tiktok.com/x")

    assert result.status == "failed"
    assert result.error == refusal
    assert result.body is None


def test_failed_fetch_is_cached(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"success": False, "error": "nope"})

    fetcher = Fetcher(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))
    url = "https://example.com/e"

    first = fetcher.fetch(url)
    second = fetcher.fetch(url)

    assert calls == 1
    assert first.status == "failed"
    assert second.status == "failed"
    assert second.from_cache is True


def test_force_direct_bypasses_firecrawl(tmp_path: Path) -> None:
    settings = _settings(tmp_path, fetch_backend="firecrawl")
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, text="direct body", headers={"content-type": "text/plain"})

    fetcher = Fetcher(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))
    url = "https://example.com/f"

    result = fetcher.fetch(url, force_direct=True)

    assert seen_urls == [url]
    assert result.status == "ok"
    assert result.body == "direct body"


def test_direct_backend_used_automatically_when_effective_backend_is_direct(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, fetch_backend="direct")
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, text="direct body")

    fetcher = Fetcher(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))
    url = "https://example.com/g"

    result = fetcher.fetch(url)

    assert seen_urls == [url]
    assert result.status == "ok"


def test_firecrawl_request_carries_bearer_token_and_raw_html_format(tmp_path: Path) -> None:
    settings = _settings(tmp_path, firecrawl_api_key="my-secret-token")
    captured: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request
        return _firecrawl_ok()

    fetcher = Fetcher(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))
    fetcher.fetch("https://example.com/h")

    assert captured is not None
    assert captured.headers["Authorization"] == "Bearer my-secret-token"
    assert json.loads(captured.content) == {"url": "https://example.com/h", "formats": ["rawHtml"]}


def test_no_secret_leaks_on_auth_failure(tmp_path: Path) -> None:
    secret = "super-secret-firecrawl-token"
    settings = _settings(tmp_path, firecrawl_api_key=secret)
    url = "https://example.com/i"

    # No "error" key, so the status-code fallback branch runs rather than the message
    # Firecrawl itself supplied — that fallback is the one place a header could leak in.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={})

    fetcher = Fetcher(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))

    result = fetcher.fetch(url)

    assert result.status == "failed"
    assert result.error == "Firecrawl request failed with status 401"
    assert secret not in (result.error or "")
    assert secret not in repr(result)
    assert secret not in _cache_path(settings, url).read_text()


def test_remaining_credits_is_none_in_direct_mode(tmp_path: Path) -> None:
    settings = _settings(tmp_path, fetch_backend="direct")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("direct mode must not call the credit-usage endpoint")

    fetcher = Fetcher(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))

    assert fetcher.remaining_credits() is None


def test_cache_file_is_valid_json_containing_the_url(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    url = "https://example.com/j"

    def handler(request: httpx.Request) -> httpx.Response:
        return _firecrawl_ok()

    fetcher = Fetcher(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))
    fetcher.fetch(url)

    payload = json.loads(_cache_path(settings, url).read_text())
    assert payload["url"] == url


def test_default_client_follows_redirects(tmp_path: Path) -> None:
    fetcher = Fetcher(_settings(tmp_path))

    assert fetcher._client.follow_redirects is True


def test_redirect_chain_sets_final_url_while_url_stays_requested(tmp_path: Path) -> None:
    settings = _settings(tmp_path, fetch_backend="direct")
    requested = "https://example.com/short"
    resolved = "https://example.com/resolved"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == requested:
            return httpx.Response(301, headers={"Location": resolved})
        return httpx.Response(200, text="ok")

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    fetcher = Fetcher(settings, client=client)

    result = fetcher.fetch(requested)

    assert result.url == requested
    assert result.final_url == resolved


def test_non_json_firecrawl_response_is_handled_as_failed(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    url = "https://example.com/k"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>rate limited</html>")

    fetcher = Fetcher(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))

    result = fetcher.fetch(url)

    assert result.status == "failed"
    assert fetcher.spent == 1
    assert _cache_path(settings, url).exists()


def test_firecrawl_success_missing_raw_html_is_handled_as_failed(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    url = "https://example.com/l"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": True, "data": {}})

    fetcher = Fetcher(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))

    result = fetcher.fetch(url)

    assert result.status == "failed"
    assert fetcher.spent == 1
    assert _cache_path(settings, url).exists()


def test_firecrawl_metadata_url_becomes_final_url_when_it_differs(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    requested = "https://share.google/abc"
    resolved = "https://example.com/real-article"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {"rawHtml": "<html/>", "metadata": {"url": resolved}},
            },
        )

    fetcher = Fetcher(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))

    result = fetcher.fetch(requested)

    assert result.url == requested
    assert result.final_url == resolved


def test_firecrawl_missing_metadata_falls_back_to_requested_url(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    url = "https://example.com/n"

    def handler(request: httpx.Request) -> httpx.Response:
        return _firecrawl_ok("<html>n</html>")

    fetcher = Fetcher(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))

    result = fetcher.fetch(url)

    assert result.status == "ok"
    assert result.final_url == url


def test_firecrawl_refusal_final_url_is_the_requested_url(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    url = "https://tiktok.com/refused"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "error": "nope"})

    fetcher = Fetcher(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))

    result = fetcher.fetch(url)

    assert result.status == "failed"
    assert result.final_url == url


def test_firecrawl_data_not_a_dict_is_handled_as_failed(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    url = "https://example.com/o"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": True, "data": "not a dict"})

    fetcher = Fetcher(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))

    result = fetcher.fetch(url)

    assert result.status == "failed"
    assert fetcher.spent == 1
    assert _cache_path(settings, url).exists()


def test_firecrawl_payload_not_a_dict_is_handled_as_failed(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    url = "https://example.com/p"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["not", "a", "dict"])

    fetcher = Fetcher(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))

    result = fetcher.fetch(url)

    assert result.status == "failed"
    assert fetcher.spent == 1
    assert _cache_path(settings, url).exists()


def test_corrupt_cache_file_is_treated_as_a_miss_and_overwritten(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    url = "https://example.com/m"
    cache_path = _cache_path(settings, url)
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text('{"url": "https://example.com/m", "status": "ok"')  # truncated

    def handler(request: httpx.Request) -> httpx.Response:
        return _firecrawl_ok("<html>m</html>")

    fetcher = Fetcher(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))

    result = fetcher.fetch(url)

    assert result.status == "ok"
    assert result.from_cache is False
    payload = json.loads(cache_path.read_text())
    assert payload["url"] == url
    assert payload["status"] == "ok"
    assert payload["body"] == "<html>m</html>"


def test_cache_file_holding_a_bare_json_string_is_treated_as_a_miss(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    url = "https://example.com/q"
    cache_path = _cache_path(settings, url)
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text('"just a string"')  # valid JSON, wrong shape

    def handler(request: httpx.Request) -> httpx.Response:
        return _firecrawl_ok("<html>q</html>")

    fetcher = Fetcher(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))

    result = fetcher.fetch(url)

    assert result.status == "ok"
    assert result.from_cache is False
    payload = json.loads(cache_path.read_text())
    assert payload["url"] == url
    assert payload["body"] == "<html>q</html>"
