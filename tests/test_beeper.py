import json
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import quote

import httpx
import pytest

from links_garden.beeper import BeeperClient
from links_garden.config import Settings

_Handler = Callable[[httpx.Request], httpx.Response]


def _settings(*, beeper_access_token: str = "test-token") -> Settings:
    return Settings(
        _env_file=None,
        beeper_access_token=beeper_access_token,  # type: ignore[arg-type]
        beeper_api_url="http://127.0.0.1:23373",
    )


def _client(handler: _Handler, *, token: str = "test-token") -> BeeperClient:
    transport = httpx.MockTransport(handler)
    return BeeperClient(
        _settings(beeper_access_token=token), client=httpx.Client(transport=transport)
    )


def _item(
    *,
    id: str = "m1",
    text: str | None = "hello",
    timestamp: str = "2026-09-01T05:53:29.801Z",
    is_sender: bool = False,
    sender_name: str | None = "Olivier",
) -> dict[str, Any]:
    return {
        "id": id,
        "text": text,
        "timestamp": timestamp,
        "isSender": is_sender,
        "senderID": "sender-1",
        "senderName": sender_name,
        "chatID": "chat-1",
        "accountID": "account-1",
        "sortKey": "abc",
        "type": "text",
        "seen": True,
        "isDeleted": False,
        "mentions": [],
    }


def _page_response(
    items: list[dict[str, Any]], *, has_more: bool, oldest_cursor: str | None = None
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "items": items,
            "hasMore": has_more,
            "oldestCursor": oldest_cursor,
            "newestCursor": "newest",
        },
    )


def test_single_page_yields_messages_in_order() -> None:
    items = [_item(id="m1", text="first"), _item(id="m2", text="second")]

    def handler(request: httpx.Request) -> httpx.Response:
        return _page_response(items, has_more=False)

    messages = list(_client(handler).iter_messages("chat-1"))

    assert [m.id for m in messages] == ["m1", "m2"]
    assert [m.text for m in messages] == ["first", "second"]


def test_pagination_follows_oldest_cursor_across_three_pages() -> None:
    pages = {
        None: _page_response([_item(id="m1")], has_more=True, oldest_cursor="c1"),
        "c1": _page_response([_item(id="m2")], has_more=True, oldest_cursor="c2"),
        "c2": _page_response([_item(id="m3")], has_more=False),
    }
    seen_cursors: list[str | None] = []
    seen_params: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cursor = request.url.params.get("cursor")
        seen_cursors.append(cursor)
        seen_params.append(dict(request.url.params))
        return pages[cursor]

    messages = list(_client(handler).iter_messages("chat-1"))

    assert [m.id for m in messages] == ["m1", "m2", "m3"]
    assert seen_cursors == [None, "c1", "c2"]
    assert all(params["limit"] == "200" for params in seen_params)
    assert all(params["direction"] == "before" for params in seen_params)


def test_since_stops_walk_and_never_requests_later_pages() -> None:
    newer = _item(id="m1", timestamp="2026-09-01T00:00:00Z")
    older = _item(id="m2", timestamp="2026-08-01T00:00:00Z")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise AssertionError("must not request a later page once since is hit")
        return _page_response([newer, older], has_more=True, oldest_cursor="c1")

    messages = list(_client(handler).iter_messages("chat-1", since=date(2026, 8, 15)))

    assert [m.id for m in messages] == ["m1"]
    assert calls == 1


def test_messages_with_empty_or_missing_text_are_skipped() -> None:
    empty = _item(id="m1", text="")
    missing = _item(id="m2")
    del missing["text"]
    kept = _item(id="m3", text="kept")

    def handler(request: httpx.Request) -> httpx.Response:
        return _page_response([empty, missing, kept], has_more=False)

    messages = list(_client(handler).iter_messages("chat-1"))

    assert [m.id for m in messages] == ["m3"]


def test_timestamps_parse_to_timezone_aware_datetimes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _page_response([_item(timestamp="2026-09-01T05:53:29.801Z")], has_more=False)

    (message,) = list(_client(handler).iter_messages("chat-1"))

    assert message.timestamp.tzinfo is not None
    assert message.timestamp == datetime(2026, 9, 1, 5, 53, 29, 801000, tzinfo=UTC)


def test_runaway_guard_stops_after_page_cap_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _page_response([_item(id=f"m{calls}")], has_more=True, oldest_cursor=f"c{calls}")

    with caplog.at_level(logging.WARNING, logger="links_garden.beeper"):
        messages = list(_client(handler).iter_messages("chat-1"))

    assert calls == 500
    assert len(messages) == 500
    assert any("500" in record.getMessage() for record in caplog.records)


def test_missing_oldest_cursor_while_has_more_stops_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _page_response([_item(id="m1")], has_more=True, oldest_cursor=None)

    with caplog.at_level(logging.WARNING, logger="links_garden.beeper"):
        messages = list(_client(handler).iter_messages("chat-1"))

    assert [m.id for m in messages] == ["m1"]
    assert any("oldestCursor" in record.getMessage() for record in caplog.records)


def test_item_missing_id_is_skipped_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    missing_id = _item(id="ignored")
    del missing_id["id"]
    kept = _item(id="m2")

    def handler(request: httpx.Request) -> httpx.Response:
        return _page_response([missing_id, kept], has_more=False)

    with caplog.at_level(logging.WARNING, logger="links_garden.beeper"):
        messages = list(_client(handler).iter_messages("chat-1"))

    assert [m.id for m in messages] == ["m2"]
    assert any("id" in record.getMessage().lower() for record in caplog.records)


def test_add_reaction_posts_correct_body_and_url_and_returns_true() -> None:
    captured: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request
        return httpx.Response(
            200,
            json={
                "success": True,
                "chatID": "chat-1",
                "messageID": "msg-1",
                "reactionKey": "✅",
                "transactionID": "tx-1",
            },
        )

    result = _client(handler).add_reaction("chat-1", "msg-1")

    assert result is True
    assert captured is not None
    assert str(captured.url) == "http://127.0.0.1:23373/v1/chats/chat-1/messages/msg-1/reactions"
    assert json.loads(captured.content) == {"reactionKey": "✅"}


def test_add_reaction_returns_false_on_http_error_without_raising() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    assert _client(handler).add_reaction("chat-1", "msg-1") is False


def test_add_reaction_returns_false_on_transport_error_without_raising() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    assert _client(handler).add_reaction("chat-1", "msg-1") is False


def test_add_reaction_returns_false_on_a_closed_client_without_raising() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("a closed client must never reach the transport")

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    http_client.close()
    client = BeeperClient(_settings(), client=http_client)

    assert client.add_reaction("chat-1", "msg-1") is False


def test_check_returns_false_on_a_closed_client_without_raising() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("a closed client must never reach the transport")

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    http_client.close()
    client = BeeperClient(_settings(), client=http_client)

    assert client.check() is False


def test_chat_id_is_url_encoded_in_every_request_path() -> None:
    chat_id = "!sje8CuisVpV6iqz6aXDX:beeper.local"
    encoded = quote(chat_id, safe="")
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        if request.url.path.endswith("/reactions"):
            return httpx.Response(200, json={"success": True})
        return _page_response([], has_more=False)

    client = _client(handler)
    list(client.iter_messages(chat_id))
    client.add_reaction(chat_id, "msg-1")

    assert len(seen_urls) == 2
    assert all(encoded in url for url in seen_urls)


def test_token_appears_only_in_request_header_never_elsewhere(
    caplog: pytest.LogCaptureFixture,
) -> None:
    token = "super-secret-beeper-token"
    requests_seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        if request.url.path.endswith("/reactions"):
            return httpx.Response(500, text="boom")
        return _page_response([_item(id="m1", text="hello")], has_more=False)

    client = _client(handler, token=token)

    with caplog.at_level(logging.WARNING, logger="links_garden.beeper"):
        (message,) = list(client.iter_messages("chat-1"))
        reaction_result = client.add_reaction("chat-1", "m1")

    assert requests_seen
    assert all(req.headers["Authorization"] == f"Bearer {token}" for req in requests_seen)
    assert reaction_result is False
    assert token not in repr(message)
    assert token not in repr(reaction_result)
    assert all(token not in record.getMessage() for record in caplog.records)


def test_check_returns_false_when_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    assert _client(handler).check() is False
