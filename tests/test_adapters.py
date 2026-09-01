import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import cast
from urllib.parse import quote

import pytest
from _pytest.monkeypatch import MonkeyPatch

from links_garden import adapters
from links_garden.adapters import Extracted, extract
from links_garden.fetch import Fetcher, FetchResult


@dataclass
class _Call:
    url: str
    force_direct: bool


class FakeFetcher:
    """Canned, in-memory stand-in for `Fetcher`. Records every call; never touches the network.

    Structurally identical to `Fetcher.fetch`, but not a subclass of it, so callers must
    `cast` it to `Fetcher` at the `extract` boundary — see `_extract` below.
    """

    def __init__(
        self,
        responses: dict[str, FetchResult] | None = None,
        *,
        default: FetchResult | None = None,
    ) -> None:
        self._responses = responses or {}
        self._default = default
        self.calls: list[_Call] = []

    def fetch(self, url: str, *, force_direct: bool = False) -> FetchResult:
        self.calls.append(_Call(url=url, force_direct=force_direct))
        if url in self._responses:
            return self._responses[url]
        if self._default is not None:
            return self._default
        raise AssertionError(f"unexpected fetch: {url}")


def _extract(url: str, fake: FakeFetcher) -> Extracted:
    return extract(url, cast(Fetcher, fake))


def _ok(url: str, body: str | None, *, final_url: str | None = None) -> FetchResult:
    return FetchResult(
        url=url,
        final_url=final_url if final_url is not None else url,
        status="ok",
        body=body,
        content_type=None,
        error=None,
        from_cache=False,
    )


def _failed(url: str, error: str) -> FetchResult:
    return FetchResult(
        url=url,
        final_url=url,
        status="failed",
        body=None,
        content_type=None,
        error=error,
        from_cache=False,
    )


@pytest.fixture(autouse=True)
def _tiktok_throttle(monkeypatch: MonkeyPatch) -> Iterator[list[float]]:
    # Every test starts as if no TikTok call has ever been made, sleep is a no-op that just
    # records what it was asked to wait for, and the clock is frozen so "no time elapsed
    # between calls" is the default. DNS is stubbed to a public address so the address guard
    # never triggers here and no test call reaches the network. Without this, tests would
    # bleed throttle state into each other, actually sleep, or make a live DNS lookup.
    calls: list[float] = []
    monkeypatch.setattr(adapters, "_last_tiktok_call", None)
    monkeypatch.setattr(adapters, "_sleep", calls.append)
    monkeypatch.setattr(adapters, "_monotonic", lambda: 0.0)
    monkeypatch.setattr(adapters, "_resolve_addresses", lambda host: ["93.184.216.34"])
    yield calls


# --- Rule 1: shorteners ---


def test_shortener_resolves_and_redispatches_on_resolved_url() -> None:
    short_url = "https://share.google/abc123"
    real_url = "https://example.com/real-article"
    fake = FakeFetcher(
        {
            short_url: _ok(short_url, body=None, final_url=real_url),
            real_url: _ok(real_url, "<html><title>Real</title><body>Body text</body></html>"),
        }
    )

    result = _extract(short_url, fake)

    assert [call.url for call in fake.calls] == [short_url, real_url]
    assert all(call.force_direct is False for call in fake.calls)
    assert result.url == real_url
    assert result.title == "Real"


def test_shortener_redirect_loop_stops_at_hop_limit() -> None:
    a, b = "https://bit.ly/a", "https://t.co/b"
    fake = FakeFetcher({a: _ok(a, body=None, final_url=b), b: _ok(b, body=None, final_url=a)})

    result = _extract(a, fake)

    assert len(fake.calls) == adapters._MAX_SHORTENER_HOPS
    assert result.error is not None


# --- Rule 2 and 3: vm.tiktok.com and TikTok ---


def test_vm_tiktok_resolves_then_extracts_via_tiktok_in_two_fetches(
    _tiktok_throttle: list[float],
) -> None:
    vm_url = "https://vm.tiktok.com/ZMabcdef/"
    resolved_with_tracking = "https://www.tiktok.com/@/video/7552605014624046348?_r=1&_d=xyz"
    stripped = "https://www.tiktok.com/@/video/7552605014624046348"
    oembed_url = f"https://www.tiktok.com/oembed?url={quote(stripped, safe='')}"
    oembed_body = json.dumps(
        {
            "title": "une légende en français",
            "author_name": "kolakelly motivation",
            "author_url": "https://www.tiktok.com/@kola_kelly",
        }
    )
    fake = FakeFetcher(
        {
            vm_url: _ok(vm_url, body=None, final_url=resolved_with_tracking),
            oembed_url: _ok(oembed_url, oembed_body),
        }
    )

    result = _extract(vm_url, fake)

    assert [call.url for call in fake.calls] == [vm_url, oembed_url]
    assert all(call.force_direct is True for call in fake.calls)
    assert result.url == stripped
    assert result.title == "une légende en français"
    assert result.author == "kolakelly motivation"
    assert result.extra == {"author_url": "https://www.tiktok.com/@kola_kelly"}
    # The throttle fires between the two direct calls this single extraction makes, not before.
    assert _tiktok_throttle == [adapters._TIKTOK_THROTTLE_SECONDS]


def test_tiktok_fetches_oembed_with_force_direct() -> None:
    url = "https://www.tiktok.com/@someone/video/123"
    oembed_url = f"https://www.tiktok.com/oembed?url={quote(url, safe='')}"
    body = json.dumps({"title": "caption", "author_name": "someone", "author_url": "u"})
    fake = FakeFetcher({oembed_url: _ok(oembed_url, body)})

    result = _extract(url, fake)

    assert fake.calls == [_Call(url=oembed_url, force_direct=True)]
    assert result.title == "caption"
    assert result.content == "caption"


def test_tiktok_oembed_fields_map_to_title_author_and_extra() -> None:
    url = "https://www.tiktok.com/@kola_kelly/video/9"
    oembed_url = f"https://www.tiktok.com/oembed?url={quote(url, safe='')}"
    body = json.dumps(
        {
            "title": "the caption",
            "author_name": "kola kelly",
            "author_url": "https://www.tiktok.com/@kola_kelly",
        }
    )
    fake = FakeFetcher({oembed_url: _ok(oembed_url, body)})

    result = _extract(url, fake)

    assert result.title == "the caption"
    assert result.author == "kola kelly"
    assert result.content == "the caption"
    assert result.extra == {"author_url": "https://www.tiktok.com/@kola_kelly"}


def test_tiktok_oembed_non_string_fields_are_dropped_not_passed_through() -> None:
    url = "https://www.tiktok.com/@someone/video/1"
    oembed_url = f"https://www.tiktok.com/oembed?url={quote(url, safe='')}"
    body = json.dumps({"title": 1, "author_name": ["a"], "author_url": 2})
    fake = FakeFetcher({oembed_url: _ok(oembed_url, body)})

    result = _extract(url, fake)

    assert result.title is None
    assert result.author is None
    assert result.content is None
    assert result.extra == {}


def test_tiktok_throttle_sleeps_between_calls_but_not_before_the_first(
    _tiktok_throttle: list[float],
) -> None:
    url_1 = "https://www.tiktok.com/@a/video/1"
    url_2 = "https://www.tiktok.com/@b/video/2"
    oembed_1 = f"https://www.tiktok.com/oembed?url={quote(url_1, safe='')}"
    oembed_2 = f"https://www.tiktok.com/oembed?url={quote(url_2, safe='')}"
    body = json.dumps({"title": "t", "author_name": "a"})
    fake = FakeFetcher({oembed_1: _ok(oembed_1, body), oembed_2: _ok(oembed_2, body)})

    _extract(url_1, fake)
    assert _tiktok_throttle == []

    _extract(url_2, fake)
    assert _tiktok_throttle == [adapters._TIKTOK_THROTTLE_SECONDS]


def test_tiktok_throttle_only_waits_out_the_remaining_time(
    monkeypatch: MonkeyPatch, _tiktok_throttle: list[float]
) -> None:
    url_1 = "https://www.tiktok.com/@a/video/1"
    url_2 = "https://www.tiktok.com/@b/video/2"
    oembed_1 = f"https://www.tiktok.com/oembed?url={quote(url_1, safe='')}"
    oembed_2 = f"https://www.tiktok.com/oembed?url={quote(url_2, safe='')}"
    body = json.dumps({"title": "t", "author_name": "a"})
    fake = FakeFetcher({oembed_1: _ok(oembed_1, body), oembed_2: _ok(oembed_2, body)})
    clock = [0.0]
    monkeypatch.setattr(adapters, "_monotonic", lambda: clock[0])

    _extract(url_1, fake)
    clock[0] = 0.5  # half the throttle window already passed before the second call

    _extract(url_2, fake)

    assert _tiktok_throttle == [adapters._TIKTOK_THROTTLE_SECONDS - 0.5]


# --- Rule 4: X and Twitter ---


def test_x_adapter_rewrites_host_and_parses_json_wrapped_in_html() -> None:
    url = "https://x.com/someuser/status/12345"
    fxtwitter_url = "https://api.fxtwitter.com/someuser/status/12345"
    tweet_json = json.dumps(
        {"tweet": {"text": "hello world", "author": {"screen_name": "someuser"}}}
    )
    body = f"<html><body><pre>{tweet_json}</pre></body></html>"
    fake = FakeFetcher({fxtwitter_url: _ok(fxtwitter_url, body)})

    result = _extract(url, fake)

    assert fake.calls == [_Call(url=fxtwitter_url, force_direct=False)]
    assert result.url == url
    assert result.author == "someuser"
    assert result.content == "hello world"


# --- Rule 5: YouTube ---


def test_youtube_uses_oembed_endpoint_not_forced_direct() -> None:
    url = "https://www.youtube.com/watch?v=abc123"
    oembed_url = f"https://www.youtube.com/oembed?url={quote(url, safe='')}&format=json"
    body = json.dumps({"title": "Cool Video", "author_name": "Some Channel"})
    fake = FakeFetcher({oembed_url: _ok(oembed_url, body)})

    result = _extract(url, fake)

    assert fake.calls == [_Call(url=oembed_url, force_direct=False)]
    assert result.title == "Cool Video"
    assert result.author == "Some Channel"


# --- Rule 6: everything else ---


def test_generic_fetches_original_url_not_forced_direct() -> None:
    url = "https://example.com/some-article"
    body = "<html><head><title>Title</title></head><body><p>Body</p></body></html>"
    fake = FakeFetcher({url: _ok(url, body)})

    result = _extract(url, fake)

    assert fake.calls == [_Call(url=url, force_direct=False)]
    assert result.title == "Title"
    assert result.content == "Body"


def test_generic_extractor_drops_script_and_style_and_collapses_whitespace() -> None:
    url = "https://example.com/messy"
    body = (
        "<html><head><title> My   Title </title>"
        "<style>.a{color:red}</style></head>"
        "<body><script>alert('x')</script>"
        "<nav>Skip me</nav>"
        "<header>Site header</header>"
        "<aside>Sidebar</aside>"
        "<p>Hello   world</p>"
        "<footer>Site footer</footer>"
        "</body></html>"
    )
    fake = FakeFetcher({url: _ok(url, body)})

    result = _extract(url, fake)

    assert result.title == "My Title"
    assert result.content == "Hello world"


# --- Failure handling ---


def test_fetch_failure_returns_error_without_raising() -> None:
    url = "https://example.com/broken"
    fake = FakeFetcher({url: _failed(url, "HTTP 500")})

    result = _extract(url, fake)

    assert result.error == "HTTP 500"
    assert result.content is None


def test_malformed_json_returns_error_not_a_crash() -> None:
    url = "https://www.youtube.com/watch?v=zzz"
    oembed_url = f"https://www.youtube.com/oembed?url={quote(url, safe='')}&format=json"
    fake = FakeFetcher({oembed_url: _ok(oembed_url, "not json at all")})

    result = _extract(url, fake)

    assert result.error is not None
    assert result.title is None


def _youtube_oembed_url(url: str) -> str:
    return f"https://www.youtube.com/oembed?url={quote(url, safe='')}&format=json"


_BROKEN_YOUTUBE_URL = "https://www.youtube.com/watch?v=broken"


@pytest.mark.parametrize(
    "url,fake",
    [
        ("https://example.com/empty", FakeFetcher(default=_ok("https://example.com/empty", ""))),
        (
            "https://example.com/error",
            FakeFetcher(default=_failed("https://example.com/error", "HTTP 500")),
        ),
        (
            _BROKEN_YOUTUBE_URL,
            FakeFetcher({_youtube_oembed_url(_BROKEN_YOUTUBE_URL): _ok("x", "{not valid json")}),
        ),
        ("not-a-url-at-all", FakeFetcher()),
    ],
)
def test_extract_never_raises_on_hostile_inputs(url: str, fake: FakeFetcher) -> None:
    # The point is that this call returns rather than raises. An empty-but-ok body is a
    # legitimate empty result, not an error, so only assert `error` where one is actually owed.
    result = extract(url, cast(Fetcher, fake))

    assert isinstance(result, Extracted)
    if url != "https://example.com/empty":
        assert result.error is not None
    else:
        assert result.error is None
        assert result.content is None


# --- Private-network guard ---


@pytest.mark.parametrize(
    "blocked_address",
    [
        "127.0.0.1",  # loopback
        "10.0.0.5",  # private
        "169.254.169.254",  # link-local, the cloud metadata address
        "240.0.0.1",  # reserved
        "::ffff:127.0.0.1",  # IPv4-mapped IPv6 loopback
    ],
)
def test_direct_fetch_refused_when_host_resolves_to_a_blocked_address(
    monkeypatch: MonkeyPatch, blocked_address: str
) -> None:
    monkeypatch.setattr(adapters, "_resolve_addresses", lambda host: [blocked_address])
    fake = FakeFetcher()  # must never be called

    result = _extract("https://www.tiktok.com/@someone/video/1", fake)

    assert fake.calls == []
    assert result.error is not None


def test_direct_fetch_refused_when_final_url_redirects_to_a_blocked_address(
    monkeypatch: MonkeyPatch,
) -> None:
    # httpx follows redirects before the adapter sees anything, so a fetch that starts at a
    # public URL can still land on an internal one. The guard must catch that too, not just
    # the pre-fetch host.
    url = "https://www.tiktok.com/@someone/video/1"
    oembed_url = f"https://www.tiktok.com/oembed?url={quote(url, safe='')}"
    metadata_host = "169.254.169.254"
    redirected = f"http://{metadata_host}/latest/meta-data/"
    monkeypatch.setattr(
        adapters,
        "_resolve_addresses",
        lambda host: [host] if host == metadata_host else ["93.184.216.34"],
    )
    fake = FakeFetcher({oembed_url: _ok(oembed_url, "irrelevant", final_url=redirected)})

    result = _extract(url, fake)

    assert result.error is not None
    assert result.content is None


def test_malformed_resolver_answer_is_treated_as_blocked(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_resolve_addresses", lambda host: ["not-an-ip"])
    fake = FakeFetcher()  # must never be called

    result = _extract("https://www.tiktok.com/@someone/video/1", fake)

    assert fake.calls == []
    assert result.error is not None
