"""Beeper Desktop client: the only module that talks to Beeper's local HTTP API."""

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import quote

import httpx

from links_garden.config import Settings

logger = logging.getLogger(__name__)

_PAGE_LIMIT = 200  # The server caps pages at 20 regardless; asking for more is free.
_MAX_PAGES = 500  # Guards a server that never sets hasMore false.
_TIMEOUT = 15.0


@dataclass(frozen=True)
class Message:
    """One message read from a Beeper chat."""

    id: str
    text: str
    timestamp: datetime
    is_sender: bool
    sender_name: str | None


@dataclass(frozen=True)
class _Page:
    items: list[dict[str, Any]]
    has_more: bool
    oldest_cursor: str | None


class BeeperClient:
    """Reads a chat's history and writes the one outward-facing side effect: a reaction.

    A reaction cannot be read back through this API, so nothing here is designed to verify
    one after the fact.
    """

    def __init__(self, settings: Settings, *, client: httpx.Client | None = None) -> None:
        self._base_url = settings.beeper_api_url
        self._token = settings.beeper_access_token
        self._client = client if client is not None else httpx.Client()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token.get_secret_value()}"}

    def check(self) -> bool:
        """Whether Beeper Desktop is reachable, so a run can fail fast with a clear message."""
        try:
            response = self._client.get(f"{self._base_url}/v1/info", timeout=_TIMEOUT)
        except Exception:  # a closed client raises RuntimeError, not httpx.HTTPError
            return False
        return response.status_code < 400

    def iter_messages(self, chat_id: str, *, since: date | None = None) -> Iterator[Message]:
        """Walk `chat_id` backwards from newest until `since` or the page cap stops it.

        `since` is what bounds the cost: messages arrive newest-first, so the first one
        older than the bound ends the walk without reading the rest of the chat. Every path
        that stops before `hasMore` goes false is logged: a silent early stop would look like
        a complete backfill.
        """
        cursor: str | None = None
        yielded = 0
        for _ in range(_MAX_PAGES):
            page = self._fetch_page(chat_id, cursor)
            for item in page.items:
                message = _parse_message(item)
                if message is None:
                    continue
                if since is not None and message.timestamp.date() < since:
                    return
                yield message
                yielded += 1
            if not page.has_more:
                return
            if page.oldest_cursor is None:
                logger.warning(
                    "Chat %s: hasMore is true but no oldestCursor was sent; "
                    "stopping after %d messages",
                    chat_id,
                    yielded,
                )
                return
            cursor = page.oldest_cursor
        logger.warning(
            "Chat %s: hit the %d-page cap; stopping after %d messages, walk may be incomplete",
            chat_id,
            _MAX_PAGES,
            yielded,
        )

    def _fetch_page(self, chat_id: str, cursor: str | None) -> _Page:
        params: dict[str, str | int] = {"limit": _PAGE_LIMIT, "direction": "before"}
        if cursor is not None:
            params["cursor"] = cursor
        response = self._client.get(
            f"{self._base_url}/v1/chats/{quote(chat_id, safe='')}/messages",
            params=params,
            headers=self._headers(),
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        payload: Any = response.json()
        return _Page(
            items=payload.get("items", []),
            has_more=bool(payload.get("hasMore")),
            oldest_cursor=payload.get("oldestCursor"),
        )

    def add_reaction(self, chat_id: str, message_id: str, key: str = "✅") -> bool:
        """POST a reaction and report whether it landed. Never raises.

        This runs after ingestion has already committed, so a failure here must not undo
        that work or stop the run.
        """
        url = (
            f"{self._base_url}/v1/chats/{quote(chat_id, safe='')}"
            f"/messages/{quote(message_id, safe='')}/reactions"
        )
        try:
            response = self._client.post(
                url, json={"reactionKey": key}, headers=self._headers(), timeout=_TIMEOUT
            )
            response.raise_for_status()
            payload: Any = response.json()
            return bool(payload.get("success"))
        except Exception as exc:
            # "never raise" is absolute here: this runs after ingestion has committed, so
            # even a closed client (RuntimeError, not httpx.HTTPError) must not escape.
            logger.warning("Failed to add reaction in chat %s: %s", chat_id, exc)
            return False


def _parse_message(item: dict[str, Any]) -> Message | None:
    text = item.get("text")
    if not text:
        return None
    message_id = item.get("id")
    if not isinstance(message_id, str):
        # Unlike an empty text, a missing id is not a normal shape for this API: log it
        # rather than let one bad item end a walk over hundreds of messages.
        logger.warning("Skipping message with missing or invalid id")
        return None
    try:
        timestamp = datetime.fromisoformat(item["timestamp"])
    except (KeyError, ValueError):
        return None
    return Message(
        id=message_id,
        text=text,
        timestamp=timestamp,
        is_sender=bool(item.get("isSender")),
        sender_name=item.get("senderName"),
    )
