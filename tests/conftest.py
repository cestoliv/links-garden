"""Suite-wide guard: no test may touch the network.

The Firecrawl plan is 1000 fetches a month with 1812 remaining; a test that actually fetches
spends the user's money. This makes offline-by-discipline offline-by-construction, so CI
enforces it on every push too.
"""

import socket
from typing import NoReturn

import pytest

_BLOCKED_MESSAGE = "outbound network access is blocked during tests"


def _blocked(*_args: object, **_kwargs: object) -> NoReturn:
    # OSError, not a bespoke exception: it's what a real socket failure raises, so code that
    # already handles DNS/connect failures (adapters._is_blocked_host's `except OSError`,
    # httpx wrapping connect errors into httpx.HTTPError) keeps behaving the same way.
    raise OSError(_BLOCKED_MESSAGE)


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    # Patching methods on socket.socket, not replacing the class itself: ssl.SSLSocket
    # subclasses socket.socket, and swapping the class out from under it breaks the import.
    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
