"""Per-platform extraction: dispatch a note-supplied URL to the right handler and normalize
whatever comes back into an `Extracted`, never raising.
"""

import ipaddress
import json
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from links_garden.fetch import Fetcher, FetchResult

_SHORTENER_HOSTS = {"share.google", "goo.gl", "bit.ly", "t.co", "lnkd.in"}
_MAX_SHORTENER_HOPS = 5

_X_HOSTS = {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}

_SKIPPED_TAGS = {"script", "style", "nav", "header", "footer", "aside"}

# TikTok has no Firecrawl proxy, so these calls leave from the home IP directly. Spacing them
# _TIKTOK_THROTTLE_SECONDS apart, measured from the last actual call rather than a flat sleep
# every time, is the mitigation the user accepted in exchange for that.
_TIKTOK_THROTTLE_SECONDS = 2.0
_sleep: Callable[[float], None] = time.sleep
_monotonic: Callable[[], float] = time.monotonic
_last_tiktok_call: float | None = None


@dataclass(frozen=True)
class Extracted:
    """Normalized result of extracting one URL, regardless of which adapter handled it."""

    url: str
    title: str | None
    author: str | None
    content: str | None
    extra: dict[str, str]
    error: str | None


def extract(url: str, fetcher: Fetcher) -> Extracted:
    """Dispatch `url` to the adapter for its host. Never raises: ingesting one bad URL out of
    many must not abort the run.
    """
    try:
        return _dispatch(url, fetcher, hops=0)
    except Exception as exc:  # last-resort net for anything the adapters below didn't catch
        return _error(url, f"unexpected error: {exc}")


def _dispatch(url: str, fetcher: Fetcher, hops: int) -> Extracted:
    host = _hostname(url)
    if host is None:
        return _error(url, "URL has no host")
    if host in _SHORTENER_HOSTS:
        return _extract_shortener(url, fetcher, hops)
    if host == "vm.tiktok.com":
        return _extract_vm_tiktok(url, fetcher)
    if host == "tiktok.com" or host.endswith(".tiktok.com"):
        return _extract_tiktok(url, fetcher)
    if host in _X_HOSTS:
        return _extract_x(url, fetcher)
    if host in _YOUTUBE_HOSTS:
        return _extract_youtube(url, fetcher)
    return _extract_generic(url, fetcher)


def _hostname(url: str) -> str | None:
    return urlsplit(url).hostname


def _error(url: str, message: str) -> Extracted:
    return Extracted(url=url, title=None, author=None, content=None, extra={}, error=message)


# --- Rule 1: shorteners ---


def _extract_shortener(url: str, fetcher: Fetcher, hops: int) -> Extracted:
    if hops >= _MAX_SHORTENER_HOPS:
        return _error(url, f"redirect loop: exceeded {_MAX_SHORTENER_HOPS} shortener hops")
    result = fetcher.fetch(url)
    if result.status != "ok":
        return _error(url, result.error or "fetch failed")
    return _dispatch(result.final_url, fetcher, hops + 1)


# --- Rule 2: vm.tiktok.com ---


def _extract_vm_tiktok(url: str, fetcher: Fetcher) -> Extracted:
    result = _direct_tiktok_fetch(url, fetcher)
    if result.status != "ok":
        return _error(url, result.error or "fetch failed")
    return _extract_tiktok(_strip_query(result.final_url), fetcher)


def _strip_query(url: str) -> str:
    # A resolved vm.tiktok.com link carries tracking params (?_r=1&_d=...); stripping them
    # keeps the oEmbed request and its cache key stable across resolutions of the same video.
    return urlunsplit(urlsplit(url)._replace(query=""))


# --- Rule 3: TikTok ---


def _extract_tiktok(url: str, fetcher: Fetcher) -> Extracted:
    oembed_url = f"https://www.tiktok.com/oembed?url={quote(url, safe='')}"
    result = _direct_tiktok_fetch(oembed_url, fetcher)
    if result.status != "ok" or result.body is None:
        return _error(url, result.error or "fetch failed")
    payload = _parse_json_object(result.body)
    if payload is None:
        return _error(url, "malformed oEmbed response")
    title = _str_or_none(payload.get("title"))
    extra: dict[str, str] = {}
    author_url = _str_or_none(payload.get("author_url"))
    if author_url is not None:
        extra["author_url"] = author_url
    return Extracted(
        url=url,
        title=title,
        author=_str_or_none(payload.get("author_name")),
        content=title,
        extra=extra,
        error=None,
    )


def _direct_tiktok_fetch(url: str, fetcher: Fetcher) -> FetchResult:
    blocked = _blocked_fetch_result(url)
    if blocked is not None:
        return blocked
    _throttle_tiktok()
    result = fetcher.fetch(url, force_direct=True)
    if result.status != "ok":
        return result
    # httpx already followed any redirect by the time we see this, so a public URL that
    # redirects to an internal address must be caught here, on the resolved target.
    return _blocked_fetch_result(result.final_url) or result


def _blocked_fetch_result(url: str) -> FetchResult | None:
    host = _hostname(url)
    if host is None or not _is_blocked_host(host):
        return None
    return FetchResult(
        url=url,
        final_url=url,
        status="failed",
        body=None,
        content_type=None,
        error="refused: host resolves to a blocked network address",
        from_cache=False,
    )


def _throttle_tiktok() -> None:
    global _last_tiktok_call
    now = _monotonic()
    if _last_tiktok_call is not None:
        wait = _TIKTOK_THROTTLE_SECONDS - (now - _last_tiktok_call)
        if wait > 0:
            _sleep(wait)
    _last_tiktok_call = _monotonic()


def _is_blocked_host(host: str) -> bool:
    # A DNS failure isn't a private-network hit; the fetch itself will fail on its own. A
    # resolver answer ipaddress can't parse is untrustworthy, so that fails closed instead.
    try:
        addresses = _resolve_addresses(host)
    except OSError:
        return False
    try:
        return any(_is_blocked_address(addr) for addr in addresses)
    except ValueError:
        return True


def _resolve_addresses(host: str) -> list[str]:
    return [str(info[4][0]) for info in socket.getaddrinfo(host, None)]


def _is_blocked_address(addr: str) -> bool:
    ip = ipaddress.ip_address(addr)
    return ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved


# --- Rule 4: X and Twitter ---


def _extract_x(url: str, fetcher: Fetcher) -> Extracted:
    result = fetcher.fetch(_rewrite_x_url(url))
    if result.status != "ok" or result.body is None:
        return _error(url, result.error or "fetch failed")
    json_text = _extract_outer_json(result.body)
    if json_text is None:
        return _error(url, "no JSON object in fxtwitter response")
    try:
        payload: Any = json.loads(json_text)
        tweet = payload["tweet"]
        text = tweet["text"]
        screen_name = tweet["author"]["screen_name"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        return _error(url, f"malformed fxtwitter response: {exc}")
    return Extracted(url=url, title=None, author=screen_name, content=text, extra={}, error=None)


def _rewrite_x_url(url: str) -> str:
    return urlunsplit(urlsplit(url)._replace(netloc="api.fxtwitter.com"))


def _extract_outer_json(body: str) -> str | None:
    # Firecrawl wraps the fxtwitter JSON body in HTML, so pull out the outermost object.
    start = body.find("{")
    end = body.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    return body[start : end + 1]


# --- Rule 5: YouTube ---


def _extract_youtube(url: str, fetcher: Fetcher) -> Extracted:
    oembed_url = f"https://www.youtube.com/oembed?url={quote(url, safe='')}&format=json"
    result = fetcher.fetch(oembed_url)
    if result.status != "ok" or result.body is None:
        return _error(url, result.error or "fetch failed")
    payload = _parse_json_object(result.body)
    if payload is None:
        return _error(url, "malformed oEmbed response")
    return Extracted(
        url=url,
        title=_str_or_none(payload.get("title")),
        author=_str_or_none(payload.get("author_name")),
        content=None,
        extra={},
        error=None,
    )


def _parse_json_object(body: str) -> dict[str, Any] | None:
    try:
        payload: Any = json.loads(body)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


# --- Rule 6: everything else ---


def _extract_generic(url: str, fetcher: Fetcher) -> Extracted:
    result = fetcher.fetch(url)
    if result.status != "ok" or result.body is None:
        return _error(url, result.error or "fetch failed")
    parser = _TextExtractor()
    parser.feed(result.body)
    return Extracted(
        url=url, title=parser.title, author=None, content=parser.content, extra={}, error=None
    )


class _TextExtractor(HTMLParser):
    """Crude readable-text extraction: drop boilerplate tags, collapse whitespace. A real
    readability algorithm is a later step's problem; the LLM summary does the heavy lifting.
    """

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._in_title = False
        self._title_parts: list[str] = []
        self._body_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIPPED_TAGS:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        (self._title_parts if self._in_title else self._body_parts).append(data)

    @property
    def title(self) -> str | None:
        words = " ".join(self._title_parts).split()
        return " ".join(words) if words else None

    @property
    def content(self) -> str | None:
        words = " ".join(self._body_parts).split()
        return " ".join(words) if words else None
