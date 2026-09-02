"""MCP server tests: the five tools are registered and callable, return what `api.py`'s and
`search.py`'s own functions return, a tool error surfaces without killing the server, and a
tombstoned document stays invisible through every tool that could otherwise return it.

`InMemoryTransport` drives the SDK's real dispatch loop over in-memory streams -- no socket, no
subprocess, no ollama call -- so schema derivation and exception handling run for real instead
of being re-implemented here. `tests/conftest.py` blocks outbound sockets on top of that.
"""

import sqlite3
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import anyio
import numpy as np
import pytest
from mcp.client._memory import InMemoryTransport
from mcp.client.session import ClientSession
from mcp.types import CallToolResult
from numpy.typing import NDArray

from links_garden.config import Settings
from links_garden.db import connect, initialize
from links_garden.fetch import Fetcher, FetchResult
from links_garden.mcp_server import build_mcp_server, create_mcp_app
from links_garden.search import find_related, search
from links_garden.sets import SetDefinition, create_set

_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"username": {"type": "string"}, "niche": {"type": "string"}},
    "required": ["username", "niche"],
}
_TOOL_NAMES = {"search_garden", "get_document", "list_set_records", "find_related", "ingest_url"}


class _FakeEmbedder:
    """Same vector for every text: no test here exercises embedding similarity directly."""

    def __init__(self, default: NDArray[np.float32] | None = None) -> None:
        self._default = default if default is not None else np.array([1.0, 0.0], dtype=np.float32)

    def embed(self, texts: Sequence[str]) -> list[NDArray[np.float32]]:
        return [self._default for _ in texts]


class _FakeFetcher:
    """Canned, in-memory stand-in for `Fetcher`. Never touches the network."""

    def __init__(self, responses: dict[str, FetchResult] | None = None) -> None:
        self._responses = responses or {}

    @property
    def at_cap(self) -> bool:
        return False

    def fetch(self, url: str, *, force_direct: bool = False) -> FetchResult:
        if url not in self._responses:
            raise AssertionError(f"unexpected fetch: {url}")
        return self._responses[url]


def _as_fetcher(fake: _FakeFetcher) -> Fetcher:
    return cast(Fetcher, fake)


def _settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, database_path=tmp_path / "garden.db")


def _conn(settings: Settings) -> sqlite3.Connection:
    """A fixture-setup connection to a schema that exists. `build_mcp_server` also runs
    `initialize`, once the tool call under test builds the server, but fixtures insert rows
    before that happens.
    """
    conn = connect(settings.database_path)
    initialize(conn)
    return conn


def _insert_document(
    conn: sqlite3.Connection,
    source_ref: str,
    *,
    title: str | None = None,
    content: str | None = "content",
    deleted: bool = False,
) -> int:
    cursor = conn.execute(
        "INSERT INTO documents (source, source_ref, title, content, status, deleted_at) "
        "VALUES ('manual', ?, ?, ?, 'ok', ?)",
        (source_ref, title, content, "2026-01-01" if deleted else None),
    )
    conn.commit()
    assert cursor.lastrowid is not None
    return cursor.lastrowid


def _create_recipe_set(conn: sqlite3.Connection) -> SetDefinition:
    created = create_set(conn, "recipe", "a cooking recipe", _SCHEMA)
    assert created.id is not None
    return created


@asynccontextmanager
async def _session(
    settings: Settings, *, fetcher: Fetcher | None = None, embedder: _FakeEmbedder | None = None
) -> AsyncIterator[ClientSession]:
    server = build_mcp_server(
        settings, fetcher=fetcher, embedder=embedder if embedder is not None else _FakeEmbedder()
    )
    async with InMemoryTransport(server) as (read, write), ClientSession(read, write) as client:
        await client.initialize()
        yield client


def _call(
    settings: Settings,
    name: str,
    arguments: dict[str, object],
    *,
    fetcher: Fetcher | None = None,
) -> CallToolResult:
    async def _run() -> CallToolResult:
        async with _session(settings, fetcher=fetcher) as client:
            return await client.call_tool(name, arguments)

    return anyio.run(_run)


# 1. All five tools are registered, callable, and declare their parameters.
def test_five_tools_registered_with_parameter_schemas(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async def _run() -> dict[str, Any]:
        async with _session(settings) as client:
            result = await client.list_tools()
            return {tool.name: tool.input_schema for tool in result.tools}

    schemas = anyio.run(_run)
    assert set(schemas) == _TOOL_NAMES
    assert schemas["search_garden"]["properties"].keys() >= {"query", "limit"}
    assert schemas["get_document"]["properties"].keys() == {"document_id"}
    assert schemas["list_set_records"]["properties"].keys() >= {"set_name", "status", "limit"}
    assert schemas["find_related"]["properties"].keys() >= {"document_id", "limit"}
    assert schemas["ingest_url"]["properties"].keys() == {"url"}


# 2. search_garden returns what search.search returns, for the same query.
def test_search_garden_matches_search_module(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    conn = _conn(settings)
    for ref in ("a", "b", "c"):
        _insert_document(conn, ref, content="the quokka is a marsupial")

    result = _call(settings, "search_garden", {"query": "quokka", "limit": 2})
    expected = search(conn, settings, _FakeEmbedder(), "quokka", limit=2)

    assert not result.is_error
    hits = result.structured_content["result"]
    assert [hit["document_id"] for hit in hits] == [hit.document_id for hit in expected]


# 3. get_document returns a document, and 404-equivalents for an unknown id without crashing.
def test_get_document_returns_document_and_errors_for_unknown_id(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    conn = _conn(settings)
    document_id = _insert_document(conn, "doc", title="Title")

    found = _call(settings, "get_document", {"document_id": document_id})
    assert not found.is_error
    assert found.structured_content["title"] == "Title"

    missing = _call(settings, "get_document", {"document_id": 999999})
    assert missing.is_error


# 4. list_set_records filters by status and errors for an unknown set, without crashing.
def test_list_set_records_filters_and_errors_for_unknown_set(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    conn = _conn(settings)
    recipe = _create_recipe_set(conn)
    assert recipe.id is not None
    ok_id = _insert_document(conn, "ok-doc")
    conn.execute(
        "INSERT INTO set_memberships (document_id, set_id, status) VALUES (?, ?, 'ok')",
        (ok_id, recipe.id),
    )
    conn.commit()

    result = _call(settings, "list_set_records", {"set_name": "recipe"})
    assert not result.is_error
    assert [row["document_id"] for row in result.structured_content["result"]] == [ok_id]

    missing = _call(settings, "list_set_records", {"set_name": "no-such-set"})
    assert missing.is_error


# 5. find_related returns what search.find_related returns for the same document.
def test_find_related_matches_search_module(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    conn = _conn(settings)
    anchor_id = _insert_document(conn, "anchor")

    result = _call(settings, "find_related", {"document_id": anchor_id})
    expected = find_related(conn, anchor_id)

    assert not result.is_error
    hits = result.structured_content["result"]
    assert [hit["document_id"] for hit in hits] == [hit.document_id for hit in expected]

    missing = _call(settings, "find_related", {"document_id": 999999})
    assert missing.is_error


# 6. ingest_url stores the document and logs the call with actor 'mcp'.
def test_ingest_url_logs_caller_as_mcp(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    fetcher = _FakeFetcher(
        {
            "https://example.test/new": FetchResult(
                url="https://example.test/new",
                final_url="https://example.test/new",
                status="ok",
                body="<html><head><title>New</title></head><body>hi</body></html>",
                content_type="text/html",
                error=None,
                from_cache=False,
            )
        }
    )

    result = _call(
        settings, "ingest_url", {"url": "https://example.test/new"}, fetcher=_as_fetcher(fetcher)
    )

    assert not result.is_error
    document_id = result.structured_content["document_id"]
    conn = _conn(settings)
    doc_row = conn.execute("SELECT source FROM documents WHERE id = ?", (document_id,)).fetchone()
    assert doc_row["source"] == "mcp"
    log_row = conn.execute(
        "SELECT actor, document_id, url FROM ingest_log WHERE document_id = ?", (document_id,)
    ).fetchone()
    assert log_row is not None
    assert log_row["actor"] == "mcp"
    assert log_row["url"] == "https://example.test/new"


# 7. A tool raising doesn't crash the server: the session keeps answering after an error.
def test_tool_error_does_not_crash_server(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async def _run() -> tuple[CallToolResult, CallToolResult]:
        async with _session(settings) as client:
            first = await client.call_tool("get_document", {"document_id": 999999})
            second = await client.call_tool("search_garden", {"query": "anything"})
            return first, second

    errored, recovered = anyio.run(_run)
    assert errored.is_error
    assert not recovered.is_error


# 8. A tombstoned document is invisible through search_garden, get_document, list_set_records
# and find_related.
def test_tombstoned_document_is_invisible(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    conn = _conn(settings)
    recipe = _create_recipe_set(conn)
    assert recipe.id is not None
    gone_id = _insert_document(conn, "gone", content="the quokka is a marsupial", deleted=True)
    conn.execute(
        "INSERT INTO set_memberships (document_id, set_id, status) VALUES (?, ?, 'ok')",
        (gone_id, recipe.id),
    )
    conn.commit()

    search_result = _call(settings, "search_garden", {"query": "quokka"})
    assert all(hit["document_id"] != gone_id for hit in search_result.structured_content["result"])

    assert _call(settings, "get_document", {"document_id": gone_id}).is_error

    records_result = _call(settings, "list_set_records", {"set_name": "recipe"})
    assert all(row["document_id"] != gone_id for row in records_result.structured_content["result"])

    assert _call(settings, "find_related", {"document_id": gone_id}).is_error


# create_mcp_app refuses an empty API_TOKEN, same as create_app: an MCP server exposing the
# whole garden must not be more permissive than the HTTP API sharing its data.
def test_create_mcp_app_refuses_empty_token(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        create_mcp_app(_settings(tmp_path), embedder=_FakeEmbedder())
