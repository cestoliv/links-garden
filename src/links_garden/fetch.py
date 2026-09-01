"""HTTP fetch layer: cache-first, budget-capped access to Firecrawl and direct requests."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx

from links_garden.config import Settings

_FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v2/scrape"
_FIRECRAWL_CREDITS_URL = "https://api.firecrawl.dev/v2/team/credit-usage"
_FIRECRAWL_TIMEOUT = 60.0  # Firecrawl renders the page before returning.
_DIRECT_TIMEOUT = 15.0


@dataclass(frozen=True)
class FetchResult:
    """Outcome of one `Fetcher.fetch` call."""

    url: str
    final_url: str
    status: Literal["ok", "failed", "skipped"]
    body: str | None
    content_type: str | None
    error: str | None
    from_cache: bool


class Fetcher:
    """The one place in the codebase that performs HTTP, backed by a content-addressed cache.

    A 1000-fetch monthly Firecrawl plan makes the cache the single most important behavior
    here: every terminal outcome, failures included, is written so a URL is never fetched twice.
    """

    def __init__(self, settings: Settings, *, client: httpx.Client | None = None) -> None:
        self._settings = settings
        # A redirect left unfollowed looks like a successful, empty-bodied fetch and then
        # poisons the cache permanently, since nothing invalidates a cached "success".
        self._client = client if client is not None else httpx.Client(follow_redirects=True)
        self.spent = 0

    @property
    def at_cap(self) -> bool:
        """Whether this run has already spent its `max_fetches_per_run` allowance.

        One place for the comparison, so every caller checking the cap against `spent` reads
        it off the same `Settings` this `Fetcher` was built from, rather than a second copy.
        """
        return self.spent >= self._settings.max_fetches_per_run

    def fetch(self, url: str, *, force_direct: bool = False) -> FetchResult:
        """Fetch `url`, consulting the cache first and never exceeding the per-run cap."""
        cached = self._read_cache(url)
        if cached is not None:
            return cached

        if self.at_cap:
            # Skipped, not failed: nothing was learned about the URL, so it is not cached.
            return FetchResult(
                url=url,
                final_url=url,
                status="skipped",
                body=None,
                content_type=None,
                error=None,
                from_cache=False,
            )

        use_direct = force_direct or self._settings.effective_fetch_backend == "direct"
        result = self._fetch_direct(url) if use_direct else self._fetch_firecrawl(url)
        # Every actual request counts toward the cap, direct or not: a run hammering a site
        # for free is still a problem, even though only Firecrawl requests spend a credit.
        self.spent += 1
        self._write_cache(result)
        return result

    def remaining_credits(self) -> int | None:
        """Return Firecrawl's remaining plan credits, or None when running the direct backend."""
        if self._settings.effective_fetch_backend == "direct":
            return None
        headers = self._firecrawl_headers()
        response = self._client.get(
            _FIRECRAWL_CREDITS_URL, headers=headers, timeout=_DIRECT_TIMEOUT
        )
        payload: Any = response.json()
        return int(payload["data"]["remainingCredits"])

    def _firecrawl_headers(self) -> dict[str, str]:
        token = self._settings.firecrawl_api_key.get_secret_value()
        return {"Authorization": f"Bearer {token}"}

    def _fetch_firecrawl(self, url: str) -> FetchResult:
        try:
            response = self._client.post(
                _FIRECRAWL_SCRAPE_URL,
                json={"url": url, "formats": ["rawHtml"]},
                headers=self._firecrawl_headers(),
                timeout=_FIRECRAWL_TIMEOUT,
            )
            payload: Any = response.json()
            if not payload.get("success"):
                # A refused domain answers HTTP 200 with success=false and an error message;
                # an auth failure has no "success" key at all. Fall back to the status code
                # rather than guessing, and never fold headers into the message.
                error = (
                    payload.get("error")
                    or f"Firecrawl request failed with status {response.status_code}"
                )
                # response.url is the scrape endpoint, never the requested URL, on this
                # backend: fall back to the requested URL rather than persist that.
                return self._failed(url, error)
            body = payload["data"]["rawHtml"]
            # Firecrawl resolves redirects server-side, so httpx never sees them: the client's
            # own response.url is always the scrape endpoint. The resolved target comes back
            # in metadata instead. Its shape is the vendor's to change, so fall back to the
            # requested URL rather than let an absent key surface as a raised exception.
            final_url = payload["data"].get("metadata", {}).get("url") or url
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
            # A malformed reply (a non-JSON error page, a success body missing rawHtml, a
            # payload or data value that isn't even a dict) is as much a failed fetch as a
            # connection error: it must still count against the cap and still get cached, or
            # one bad response retries forever.
            return self._failed(url, str(exc))

        return FetchResult(
            url=url,
            final_url=final_url,
            status="ok",
            body=body,
            content_type=None,
            error=None,
            from_cache=False,
        )

    def _fetch_direct(self, url: str) -> FetchResult:
        try:
            response = self._client.get(url, timeout=_DIRECT_TIMEOUT)
        except httpx.HTTPError as exc:
            return self._failed(url, str(exc))

        if response.status_code >= 400:
            return self._failed(url, f"HTTP {response.status_code}", final_url=str(response.url))

        return FetchResult(
            url=url,
            final_url=str(response.url),
            status="ok",
            body=response.text,
            content_type=response.headers.get("content-type"),
            error=None,
            from_cache=False,
        )

    @staticmethod
    def _failed(url: str, error: str, *, final_url: str | None = None) -> FetchResult:
        return FetchResult(
            url=url,
            final_url=final_url if final_url is not None else url,
            status="failed",
            body=None,
            content_type=None,
            error=error,
            from_cache=False,
        )

    def _cache_path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode()).hexdigest()
        return self._settings.fetch_cache_dir / f"{digest}.json"

    def _read_cache(self, url: str) -> FetchResult | None:
        path = self._cache_path(url)
        if not path.is_file():
            return None
        try:
            payload: Any = json.loads(path.read_text())
            return FetchResult(
                url=payload["url"],
                final_url=payload["final_url"],
                status=payload["status"],
                body=payload["body"],
                content_type=payload["content_type"],
                error=payload["error"],
                from_cache=True,
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            # A file truncated by a kill mid-write, or one holding valid JSON of the wrong
            # shape (a bare string or list), must not brick that URL forever: treat it as a
            # miss so the caller re-fetches and overwrites it.
            return None

    def _write_cache(self, result: FetchResult) -> None:
        path = self._cache_path(result.url)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            # The URL is stored alongside its hash so a cache file is self-describing when
            # debugging, since the filename itself is opaque.
            "url": result.url,
            "final_url": result.final_url,
            "fetched_at": datetime.now(UTC).isoformat(),
            "status": result.status,
            "body": result.body,
            "content_type": result.content_type,
            "error": result.error,
        }
        # Write next to the target and rename, so a process killed mid-write leaves the old
        # cache entry (or nothing) rather than a truncated file that would break every future
        # read of this URL.
        tmp_path = path.with_name(path.name + ".tmp")
        tmp_path.write_text(json.dumps(payload))
        tmp_path.replace(path)
