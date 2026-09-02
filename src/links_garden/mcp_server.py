"""MCP server: the five agent-facing tools DESIGN.md promises, over streamable HTTP.

Runs over streamable-http rather than stdio: the brief requires the identical bearer-token gate
`api.py` enforces ("must not be more permissive than the HTTP API that shares its data"), and a
token check only makes sense for a service another process reaches over a socket, not a
subprocess a host spawns and already owns, which is how MCP's stdio transport is normally used.
Reusing `api.py`'s own `is_authorized`/`auth_middleware` here, rather than the SDK's own
OAuth-shaped `auth=`/`token_verifier=` constructor arguments, keeps both servers making the exact
same access decision the exact same way.

Every tool below calls the same function `api.py`'s matching route calls -- `get_document`,
`list_set_records`, `ingest`, `search.search`, `search.find_related` -- so a fix to any of them
cannot miss an MCP tool. A not-found id or set name is raised as `ToolError`, whose message
reaches the caller unchanged; anything else raised inside a tool body is reported to the client
as a generic tool error without crashing the server, per the SDK's own dispatch loop.
"""

import dataclasses
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware

from links_garden import api
from links_garden import search as search_lib
from links_garden.config import Settings
from links_garden.db import connect, initialize
from links_garden.embed import Embedder, EmbedderLike
from links_garden.fetch import Fetcher

# Distinguishes an MCP-originated ingest from the CLI/dashboard/cron actors `ingest_log`'s
# schema anticipates, per DESIGN.md: "every MCP-originated ingest is logged with the caller
# recorded as mcp, which is what makes [ingest_url shipping] auditable rather than invisible."
_INGEST_ACTOR = "mcp"


@contextmanager
def _conn(settings: Settings) -> Iterator[sqlite3.Connection]:
    # A fresh connection per call, never one shared across the process's lifetime: same reasoning
    # as api.py's per-request Depends(get_conn) -- the SDK can run a sync tool body on a worker
    # thread other than the one that built this server.
    conn = connect(settings.database_path)
    try:
        yield conn
    finally:
        conn.close()


def build_mcp_server(
    settings: Settings, *, fetcher: Fetcher | None = None, embedder: EmbedderLike | None = None
) -> MCPServer:
    """Register the five tools. `fetcher`/`embedder` are injectable so tests never touch ollama
    or the network, same as `api.create_app`.
    """
    # `garden mcp` may run against a database the API server hasn't started against yet, so this
    # can't assume the schema already exists; a throwaway connection, exactly as `create_app` uses.
    init_conn = connect(settings.database_path)
    initialize(init_conn)
    init_conn.close()

    resolved_fetcher = fetcher if fetcher is not None else Fetcher(settings)
    resolved_embedder = embedder if embedder is not None else Embedder(settings)
    mcp = MCPServer("links-garden")

    @mcp.tool()
    def search_garden(query: str, limit: int = 20) -> list[dict[str, object]]:
        """Hybrid full-text and vector search over the garden."""
        with _conn(settings) as conn:
            hits = search_lib.search(conn, settings, resolved_embedder, query, limit=limit)
        return [dataclasses.asdict(hit) for hit in hits]

    @mcp.tool()
    def get_document(document_id: int) -> dict[str, object]:
        """Fetch one document by id."""
        with _conn(settings) as conn:
            document = api.get_document(conn, document_id)
        if document is None:
            raise ToolError(f"no document with id {document_id}")
        return document.model_dump()

    @mcp.tool()
    def list_set_records(
        set_name: str, status: str | None = None, limit: int = 50
    ) -> list[dict[str, object]]:
        """List the typed records for one set, optionally filtered by status."""
        with _conn(settings) as conn:
            records = api.list_set_records(conn, set_name, status=status, limit=limit)
        if records is None:
            raise ToolError(f"no set named {set_name!r}")
        return [record.model_dump() for record in records]

    @mcp.tool()
    def find_related(document_id: int, limit: int = 10) -> list[dict[str, object]]:
        """Nearest other documents to one document, by embedding similarity."""
        with _conn(settings) as conn:
            if api.get_document(conn, document_id) is None:
                raise ToolError(f"no document with id {document_id}")
            hits = search_lib.find_related(conn, document_id, limit=limit)
        return [dataclasses.asdict(hit) for hit in hits]

    @mcp.tool()
    def ingest_url(url: str) -> dict[str, object]:
        """Fetch and store one URL, recorded as an agent-originated ingest."""
        with _conn(settings) as conn:
            result = api.ingest(conn, resolved_fetcher, url, source="mcp")
            conn.execute(
                "INSERT INTO ingest_log (document_id, actor, url) VALUES (?, ?, ?)",
                (result.document_id, _INGEST_ACTOR, url),
            )
            conn.commit()
        return result.model_dump()

    return mcp


def create_mcp_app(
    settings: Settings, *, fetcher: Fetcher | None = None, embedder: EmbedderLike | None = None
) -> Starlette:
    """Build the ASGI app `garden mcp` serves. Refuses an empty `API_TOKEN`, same as
    `api.create_app`: an empty configured token matching an empty header would hand the whole
    garden to anything that can reach the port.
    """
    token = settings.api_token.get_secret_value()
    if not token:
        raise ValueError("API_TOKEN must be set; refusing to start an unauthenticated MCP server")
    mcp = build_mcp_server(settings, fetcher=fetcher, embedder=embedder)
    app = mcp.streamable_http_app()
    app.add_middleware(BaseHTTPMiddleware, dispatch=api.auth_middleware(token))
    return app
