"""HTTP API: bearer-gated read routes, the three writes DESIGN.md allows, and set administration
(create/update/delete) that DESIGN.md's dashboard section names but never itemized as API writes.

The repo is public and `.env` holds live secrets, so the auth check runs as ASGI middleware
rather than a per-route `Depends`: middleware wraps every request before routing decides which
handler (or which of FastAPI's own `/docs`, `/redoc`, `/openapi.json`) would run, so there is no
route, including the generated docs, that a `Depends` could accidentally miss.

Route handlers are thin: each calls a plain, importable function below (`get_document`,
`list_set_records`, `list_review`, `patch_record`, `ingest`) and translates its result into an
HTTP status. `search.search` and `search.find_related` are reused rather than reimplemented.
`mcp_server.py` wraps these same functions, so a fix here cannot miss the MCP tools. It also
imports `is_authorized`/`auth_middleware` rather than keeping its own copy of the bearer check,
since a security check with two independently maintained copies is worse than one shared.

Every route opens its own connection via `Depends(get_conn)` rather than sharing one for the
app's whole lifetime: uvicorn (and `TestClient`) can run request handling on a thread other than
the one that called `create_app`, and a single shared connection would also let two requests'
transactions interleave. WAL mode makes a fresh connection per request cheap.
"""

import dataclasses
import json
import secrets
import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from links_garden.config import Settings
from links_garden.db import Source, connect, initialize, tombstone
from links_garden.embed import SELECTOR_SQL, Embedder, EmbedderLike
from links_garden.fetch import Fetcher
from links_garden.search import Hit, find_related, search
from links_garden.sets import (
    SetDefinition,
    compute_missing_fields,
    create_set,
    delete_set,
    get_set,
    list_sets,
    update_set,
)
from links_garden.sync import ingest_url, resolve_status

_DOCUMENT_COLUMNS = (
    "id, source, source_ref, url, parent_document_id, title, author, content, summary, "
    "keywords, message_text, status, error, fetched_at, created_at, updated_at"
)

# What each route builder receives instead of a live connection: a zero-arg callable FastAPI
# calls per request via Depends(), yielding one connection and closing it when the request ends.
# Async, not a plain generator: FastAPI runs a sync dependency in a threadpool thread, but the
# async route handlers below run on the event loop thread, so a connection opened by a sync
# generator would be used from a different thread than the one that created it.
GetConn = Callable[[], AsyncIterator[sqlite3.Connection]]


class HitOut(BaseModel):
    document_id: int
    title: str | None
    url: str | None
    source: str
    snippet: str
    score: float
    fts_rank: int | None
    vector_rank: int | None


class DocumentOut(BaseModel):
    id: int
    source: str
    source_ref: str
    url: str | None
    parent_document_id: int | None
    title: str | None
    author: str | None
    content: str | None
    summary: str | None
    keywords: str | None
    message_text: str | None
    status: str
    error: str | None
    fetched_at: str | None
    created_at: str
    updated_at: str


class SetOut(BaseModel):
    # `schema_`, not `schema`: a field literally named `schema` shadows a `BaseModel` attribute.
    # The alias keeps the wire format at the name the brief and the dashboard actually want.
    model_config = ConfigDict(populate_by_name=True)

    name: str
    description: str
    schema_: dict[str, object] = Field(alias="schema")


class SetIn(BaseModel):
    # `schema_` is typed `Any`, not `dict`, so a caller who sends a non-object schema reaches
    # `create_set`'s own validation instead of failing FastAPI's generic 422 first -- the whole
    # point being that the server's specific rejection message is what comes back.
    model_config = ConfigDict(populate_by_name=True)

    name: str
    description: str
    schema_: Any = Field(alias="schema")


class SetUpdateIn(BaseModel):
    """Both fields optional: an omitted one keeps the set's current value, per `update_set`."""

    model_config = ConfigDict(populate_by_name=True)

    description: str | None = None
    schema_: Any = Field(default=None, alias="schema")


class SetDeleteOut(BaseModel):
    status: str
    name: str
    records_removed: int


class SetRecordOut(BaseModel):
    document_id: int
    title: str | None
    url: str | None
    status: str
    extracted_json: dict[str, object] | None
    missing_fields: list[str]


class ReviewItemOut(BaseModel):
    document_id: int
    title: str | None
    url: str | None
    set_name: str
    status: str
    missing_fields: list[str]
    error: str | None


class IngestIn(BaseModel):
    url: str
    # "a caller marker": the dashboard never sends anything but the default, and an HTTP caller
    # that wants the resulting document's provenance to read `mcp` sets this explicitly. Signal
    # and Obsidian sources never flow through this route, so those two are the only valid values.
    source: Literal["manual", "mcp"] = "manual"


class IngestOut(BaseModel):
    document_id: int | None
    url: str
    status: str
    title: str | None
    error: str | None


def _iso(value: str) -> str:
    """SQLite's `datetime('now')` gives `YYYY-MM-DD HH:MM:SS` UTC; make it real ISO 8601."""
    return value.replace(" ", "T") + "Z"


def _iso_opt(value: str | None) -> str | None:
    return _iso(value) if value is not None else None


def _hit_out(hit: Hit) -> HitOut:
    return HitOut(**dataclasses.asdict(hit))


def _set_out(set_: SetDefinition) -> SetOut:
    return SetOut(name=set_.name, description=set_.description, schema_=set_.schema)


def _create_set_status(message: str) -> int:
    """`create_set` raises `ValueError` for both a duplicate name and every validation failure;
    only its message text tells the two apart, since a shared 400/409-picker keeps that check out
    of the route itself."""
    return 409 if "already exists" in message else 400


def _update_set_status(message: str) -> int:
    """Same split for `update_set`: a missing set (404) versus a validation failure (400)."""
    return 404 if message.startswith("no set named") else 400


def _row_to_document(row: sqlite3.Row) -> DocumentOut:
    return DocumentOut(
        id=row["id"],
        source=row["source"],
        source_ref=row["source_ref"],
        url=row["url"],
        parent_document_id=row["parent_document_id"],
        title=row["title"],
        author=row["author"],
        content=row["content"],
        summary=row["summary"],
        keywords=row["keywords"],
        message_text=row["message_text"],
        status=row["status"],
        error=row["error"],
        fetched_at=_iso_opt(row["fetched_at"]),
        created_at=_iso(row["created_at"]),
        updated_at=_iso(row["updated_at"]),
    )


def _row_to_record(row: sqlite3.Row) -> SetRecordOut:
    return SetRecordOut(
        document_id=row["document_id"],
        title=row["title"],
        url=row["url"],
        status=row["status"],
        extracted_json=json.loads(row["extracted_json"]) if row["extracted_json"] else None,
        missing_fields=json.loads(row["missing_fields"]) if row["missing_fields"] else [],
    )


def get_document(conn: sqlite3.Connection, document_id: int) -> DocumentOut | None:
    row = conn.execute(
        f"SELECT {_DOCUMENT_COLUMNS} FROM documents d WHERE d.id = ? AND {SELECTOR_SQL}",
        (document_id,),
    ).fetchone()
    return _row_to_document(row) if row is not None else None


def document_exists(conn: sqlite3.Connection, document_id: int) -> bool:
    """Whether a live row exists, regardless of `status` or `content`.

    Deliberately looser than `get_document`'s full `SELECTOR_SQL`: a failed or still-pending
    ingest is invisible to search and GET, but still a row a caller should be able to tombstone.
    """
    row = conn.execute(
        "SELECT 1 FROM documents WHERE id = ? AND deleted_at IS NULL", (document_id,)
    ).fetchone()
    return row is not None


def list_set_records(
    conn: sqlite3.Connection,
    set_name: str,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[SetRecordOut] | None:
    """Typed rows for one set. `None` means the set itself doesn't exist, distinct from empty."""
    set_ = get_set(conn, set_name)
    if set_ is None:
        return None
    query = (
        "SELECT d.id AS document_id, d.title, d.url, sm.status, sm.extracted_json, "
        "sm.missing_fields FROM set_memberships sm JOIN documents d ON d.id = sm.document_id "
        f"WHERE sm.set_id = ? AND {SELECTOR_SQL}"
    )
    params: list[object] = [set_.id]
    if status is not None:
        query += " AND sm.status = ?"
        params.append(status)
    query += " ORDER BY d.id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = conn.execute(query, params).fetchall()
    return [_row_to_record(row) for row in rows]


def list_review(conn: sqlite3.Connection, *, limit: int = 50) -> list[ReviewItemOut]:
    rows = conn.execute(
        "SELECT d.id AS document_id, d.title, d.url, s.name AS set_name, sm.status, "
        "sm.missing_fields, sm.error FROM set_memberships sm "
        "JOIN documents d ON d.id = sm.document_id JOIN sets s ON s.id = sm.set_id "
        f"WHERE sm.status IN ('partial', 'failed') AND {SELECTOR_SQL} "
        "ORDER BY d.id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        ReviewItemOut(
            document_id=row["document_id"],
            title=row["title"],
            url=row["url"],
            set_name=row["set_name"],
            status=row["status"],
            missing_fields=json.loads(row["missing_fields"]) if row["missing_fields"] else [],
            error=row["error"],
        )
        for row in rows
    ]


def patch_record(
    conn: sqlite3.Connection, set_name: str, document_id: int, fields: dict[str, object]
) -> SetRecordOut | None:
    """Merge `fields` into one membership's `extracted_json`.

    Raises `ValueError` for a field outside the set's schema. Returns `None` when the set or
    the membership doesn't exist, distinguished from the validation error by exception vs. value
    so the route layer can tell a 400 from a 404 without parsing a message.
    """
    set_ = get_set(conn, set_name)
    if set_ is None:
        return None
    properties = set_.schema["properties"]
    assert isinstance(properties, dict)
    unknown = sorted(key for key in fields if key not in properties)
    if unknown:
        raise ValueError(f"unknown field(s) for set {set_name!r}: {', '.join(unknown)}")
    row = conn.execute(
        "SELECT sm.extracted_json FROM set_memberships sm "
        "JOIN documents d ON d.id = sm.document_id "
        f"WHERE sm.document_id = ? AND sm.set_id = ? AND {SELECTOR_SQL}",
        (document_id, set_.id),
    ).fetchone()
    if row is None:
        return None
    current = json.loads(row["extracted_json"]) if row["extracted_json"] else {}
    merged = {**current, **fields}
    missing = compute_missing_fields(set_.schema, merged)
    status = "partial" if missing else "ok"
    conn.execute(
        "UPDATE set_memberships SET extracted_json = ?, missing_fields = ?, status = ?, "
        "error = NULL, extracted_at = datetime('now') WHERE document_id = ? AND set_id = ?",
        (json.dumps(merged), json.dumps(missing), status, document_id, set_.id),
    )
    conn.commit()
    result_row = conn.execute(
        "SELECT d.id AS document_id, d.title, d.url, sm.status, sm.extracted_json, "
        "sm.missing_fields FROM set_memberships sm JOIN documents d ON d.id = sm.document_id "
        "WHERE sm.document_id = ? AND sm.set_id = ?",
        (document_id, set_.id),
    ).fetchone()
    assert result_row is not None
    return _row_to_record(result_row)


def ingest(
    conn: sqlite3.Connection, fetcher: Fetcher, url: str, *, source: Source = "manual"
) -> IngestOut:
    extracted = ingest_url(conn, url, fetcher, source=source)
    status = resolve_status(extracted, fetcher)
    row = conn.execute(
        "SELECT id FROM documents WHERE source = ? AND source_ref = ?", (source, url)
    ).fetchone()
    return IngestOut(
        document_id=row["id"] if row is not None else None,
        url=url,
        status=status,
        title=extracted.title,
        error=extracted.error,
    )


def is_authorized(header: str | None, token: str) -> bool:
    if header is None:
        return False
    scheme, _, credential = header.partition(" ")
    if scheme != "Bearer":
        return False
    return secrets.compare_digest(credential, token)


def auth_middleware(
    token: str,
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    async def _require_auth(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path == "/health":
            return await call_next(request)
        if not is_authorized(request.headers.get("authorization"), token):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)

    return _require_auth


def _health_router() -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return router


def _search_router(get_conn: GetConn, settings: Settings, embedder: EmbedderLike) -> APIRouter:
    router = APIRouter()

    @router.get("/search")
    async def get_search(
        q: str, limit: int = 20, conn: sqlite3.Connection = Depends(get_conn)
    ) -> list[HitOut]:
        hits = search(conn, settings, embedder, q, limit=limit)
        return [_hit_out(hit) for hit in hits]

    return router


def _document_router(get_conn: GetConn) -> APIRouter:
    router = APIRouter()

    @router.get("/documents/{document_id}")
    async def get_one_document(
        document_id: int, conn: sqlite3.Connection = Depends(get_conn)
    ) -> DocumentOut:
        document = get_document(conn, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="document not found")
        return document

    @router.get("/documents/{document_id}/related")
    async def get_document_related(
        document_id: int, limit: int = 10, conn: sqlite3.Connection = Depends(get_conn)
    ) -> list[HitOut]:
        if get_document(conn, document_id) is None:
            raise HTTPException(status_code=404, detail="document not found")
        return [_hit_out(hit) for hit in find_related(conn, document_id, limit=limit)]

    @router.delete("/documents/{document_id}")
    async def delete_document(
        document_id: int, conn: sqlite3.Connection = Depends(get_conn)
    ) -> dict[str, str]:
        if not document_exists(conn, document_id):
            raise HTTPException(status_code=404, detail="document not found")
        tombstone(conn, document_id)
        return {"status": "deleted"}

    return router


def _set_router(get_conn: GetConn) -> APIRouter:
    router = APIRouter()

    @router.get("/sets")
    async def get_all_sets(conn: sqlite3.Connection = Depends(get_conn)) -> list[SetOut]:
        return [_set_out(set_) for set_ in list_sets(conn)]

    @router.get("/sets/{name}")
    async def get_one_set(name: str, conn: sqlite3.Connection = Depends(get_conn)) -> SetOut:
        set_ = get_set(conn, name)
        if set_ is None:
            raise HTTPException(status_code=404, detail="no such set")
        return _set_out(set_)

    @router.get("/sets/{name}/records")
    async def get_set_records_route(
        name: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> list[SetRecordOut]:
        records = list_set_records(conn, name, status=status, limit=limit, offset=offset)
        if records is None:
            raise HTTPException(status_code=404, detail="no such set")
        return records

    @router.patch("/sets/{name}/records/{document_id}")
    async def patch_set_record(
        name: str,
        document_id: int,
        fields: dict[str, Any],
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> SetRecordOut:
        try:
            result = patch_record(conn, name, document_id, fields)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=404, detail="no such record")
        return result

    return router


# Split from `_set_router` rather than folded into it: with all seven `/sets` routes as nested
# closures in one function, mccabe's C901 counts their branches against that single function and
# trips the repo's complexity ceiling, even though each route reads simply on its own.
def _set_admin_router(get_conn: GetConn) -> APIRouter:
    router = APIRouter()

    @router.post("/sets", status_code=201)
    async def post_set(body: SetIn, conn: sqlite3.Connection = Depends(get_conn)) -> SetOut:
        try:
            set_ = create_set(conn, body.name, body.description, body.schema_)
        except ValueError as exc:
            raise HTTPException(status_code=_create_set_status(str(exc)), detail=str(exc)) from exc
        return _set_out(set_)

    # PATCH, not PUT: `update_set` already treats an omitted field as "keep the current value"
    # rather than requiring a full replacement, so the route mirrors that partial-update contract
    # instead of pretending it's PUT's whole-resource replace -- the same choice already made for
    # the records route above.
    @router.patch("/sets/{name}")
    async def patch_set(
        name: str, body: SetUpdateIn, conn: sqlite3.Connection = Depends(get_conn)
    ) -> SetOut:
        try:
            set_ = update_set(conn, name, description=body.description, schema=body.schema_)
        except ValueError as exc:
            raise HTTPException(status_code=_update_set_status(str(exc)), detail=str(exc)) from exc
        return _set_out(set_)

    @router.delete("/sets/{name}")
    async def delete_set_route(
        name: str, conn: sqlite3.Connection = Depends(get_conn)
    ) -> SetDeleteOut:
        """Delete a set. Cascades to `set_memberships`: any extraction results for this set's
        documents are deleted with it, not just the set definition."""
        set_ = get_set(conn, name)
        if set_ is None:
            raise HTTPException(status_code=404, detail="no such set")
        count_row = conn.execute(
            "SELECT COUNT(*) AS n FROM set_memberships WHERE set_id = ?", (set_.id,)
        ).fetchone()
        delete_set(conn, name)
        return SetDeleteOut(status="deleted", name=name, records_removed=count_row["n"])

    return router


def _review_router(get_conn: GetConn) -> APIRouter:
    router = APIRouter()

    @router.get("/review")
    async def get_review_route(
        limit: int = 50, conn: sqlite3.Connection = Depends(get_conn)
    ) -> list[ReviewItemOut]:
        return list_review(conn, limit=limit)

    return router


def _ingest_router(get_conn: GetConn, fetcher: Fetcher) -> APIRouter:
    router = APIRouter()

    @router.post("/ingest")
    async def post_ingest(
        body: IngestIn, conn: sqlite3.Connection = Depends(get_conn)
    ) -> IngestOut:
        return ingest(conn, fetcher, body.url, source=body.source)

    return router


def create_app(
    settings: Settings,
    *,
    fetcher: Fetcher | None = None,
    embedder: EmbedderLike | None = None,
) -> FastAPI:
    """Build the API. Refuses to start with an empty `API_TOKEN`: an empty configured token
    matching an empty header would serve the whole garden to anything that can reach the port.
    """
    token = settings.api_token.get_secret_value()
    if not token:
        raise ValueError("API_TOKEN must be set; refusing to start an unauthenticated API")

    # One throwaway connection just to create or migrate the schema; every request opens its own.
    init_conn = connect(settings.database_path)
    initialize(init_conn)
    init_conn.close()

    async def get_conn() -> AsyncIterator[sqlite3.Connection]:
        conn = connect(settings.database_path)
        try:
            yield conn
        finally:
            conn.close()

    # Reassigning `fetcher`/`embedder` in place would leave mypy seeing the original `| None`
    # type inside every router-builder call below, since narrowing doesn't cross function
    # boundaries; binding fresh, definitely-typed names instead avoids that everywhere at once.
    resolved_fetcher: Fetcher = fetcher if fetcher is not None else Fetcher(settings)
    resolved_embedder: EmbedderLike = embedder if embedder is not None else Embedder(settings)

    app = FastAPI(title="Links Garden API")
    app.middleware("http")(auth_middleware(token))
    app.include_router(_health_router())
    app.include_router(_search_router(get_conn, settings, resolved_embedder))
    app.include_router(_document_router(get_conn))
    app.include_router(_set_router(get_conn))
    app.include_router(_set_admin_router(get_conn))
    app.include_router(_review_router(get_conn))
    app.include_router(_ingest_router(get_conn, resolved_fetcher))
    return app
