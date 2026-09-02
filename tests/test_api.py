"""HTTP API tests: the auth boundary, each route's behavior, and that a tombstoned or
otherwise invisible document (per `SELECTOR_SQL`) never surfaces through any of them.

`fastapi.testclient.TestClient` drives every request against a temporary database; a fake
embedder stands in for ollama, and `tests/conftest.py` blocks real sockets so a slip that
would actually call it fails loudly instead of spending money.
"""

import hashlib
import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
from fastapi.testclient import TestClient
from numpy.typing import NDArray

from links_garden.api import create_app
from links_garden.config import Settings
from links_garden.db import connect, create_session
from links_garden.embed import pack_vector
from links_garden.fetch import Fetcher, FetchResult
from links_garden.sets import SetDefinition, create_set

_TOKEN = "s3cr3t-test-token"
_HEADERS = {"Authorization": f"Bearer {_TOKEN}"}
_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"username": {"type": "string"}, "niche": {"type": "string"}},
    "required": ["username", "niche"],
}

# Every path under /api that a credential gates, one representative request each, including
# FastAPI's own docs (the brief calls those out by name as a route a `Depends` could accidentally
# miss) and the session routes other than the login one.
#
# Two paths are deliberately absent, and each has its own test: `POST /api/session` is the login
# route, and `GET /api/health` is the liveness probe. Every other method on those paths is still
# gated, which `test_health_exemption_does_not_leak_to_other_api_routes` checks.
_PROTECTED_REQUESTS: list[tuple[str, str, dict[str, Any]]] = [
    ("GET", "/api/session", {}),
    ("DELETE", "/api/session", {}),
    ("GET", "/api/search", {"params": {"q": "x"}}),
    ("GET", "/api/documents", {}),
    ("GET", "/api/documents/1", {}),
    ("GET", "/api/documents/1/related", {}),
    ("DELETE", "/api/documents/1", {}),
    ("GET", "/api/sets", {}),
    ("GET", "/api/sets/recipe", {}),
    ("GET", "/api/sets/recipe/records", {}),
    ("PATCH", "/api/sets/recipe/records/1", {"json": {}}),
    ("POST", "/api/sets", {"json": {"name": "temp", "description": "d", "schema": _SCHEMA}}),
    ("PATCH", "/api/sets/recipe", {"json": {"description": "d"}}),
    ("DELETE", "/api/sets/recipe", {}),
    ("GET", "/api/review", {}),
    ("POST", "/api/ingest", {"json": {"url": "https://example.test"}}),
    ("GET", "/api/docs", {}),
    ("GET", "/api/openapi.json", {}),
]


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
        self.spent = 0

    @property
    def at_cap(self) -> bool:
        return False

    def fetch(self, url: str, *, force_direct: bool = False) -> FetchResult:
        if url not in self._responses:
            raise AssertionError(f"unexpected fetch: {url}")
        self.spent += 1
        return self._responses[url]


def _as_fetcher(fake: _FakeFetcher) -> Fetcher:
    return cast(Fetcher, fake)


def _settings(tmp_path: Path, *, token: str = _TOKEN) -> Settings:
    return Settings(
        _env_file=None,
        database_path=tmp_path / "garden.db",
        api_token=token,  # type: ignore[arg-type]
    )


def _make_app(
    tmp_path: Path,
    *,
    token: str = _TOKEN,
    fetcher: Fetcher | None = None,
    frontend_dist: Path | None = None,
) -> tuple[TestClient, sqlite3.Connection]:
    settings = _settings(tmp_path, token=token)
    app = create_app(
        settings, fetcher=fetcher, embedder=_FakeEmbedder(), frontend_dist=frontend_dist
    )
    # create_app already ran the schema through its own throwaway connection; this one is only
    # for fixture setup, separate from whatever connection each request opens for itself.
    return TestClient(app), connect(settings.database_path)


def _build_fake_dist(tmp_path: Path) -> Path:
    """A minimal stand-in for `vite build`'s output: an `index.html` plus one built asset."""
    dist_dir = tmp_path / "dist"
    (dist_dir / "assets").mkdir(parents=True)
    (dist_dir / "index.html").write_text("<html>SPA-INDEX</html>")
    (dist_dir / "assets" / "app.js").write_text("console.log('app')")
    return dist_dir


def _insert_document(
    conn: sqlite3.Connection,
    source_ref: str,
    *,
    title: str | None = None,
    url: str | None = None,
    content: str | None = "content",
    source: str = "manual",
    status: str = "ok",
    deleted: bool = False,
) -> int:
    cursor = conn.execute(
        "INSERT INTO documents (source, source_ref, title, url, content, status, deleted_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (source, source_ref, title, url, content, status, "2026-01-01" if deleted else None),
    )
    conn.commit()
    assert cursor.lastrowid is not None
    return cursor.lastrowid


def _set_created_at(conn: sqlite3.Connection, document_id: int, created_at: str) -> None:
    """Force a document's `created_at` so pagination-order tests don't depend on wall-clock
    timing (several inserts inside one test can otherwise land in the same SQLite second)."""
    conn.execute("UPDATE documents SET created_at = ? WHERE id = ?", (created_at, document_id))
    conn.commit()


def _insert_membership(
    conn: sqlite3.Connection,
    document_id: int,
    set_id: int,
    *,
    status: str = "pending",
    extracted_json: dict[str, object] | None = None,
    missing_fields: list[str] | None = None,
    error: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO set_memberships "
        "(document_id, set_id, extracted_json, missing_fields, status, error) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            document_id,
            set_id,
            json.dumps(extracted_json) if extracted_json is not None else None,
            json.dumps(missing_fields) if missing_fields is not None else None,
            status,
            error,
        ),
    )
    conn.commit()


def _create_recipe_set(conn: sqlite3.Connection) -> SetDefinition:
    created = create_set(conn, "recipe", "a cooking recipe", _SCHEMA)
    assert created.id is not None
    return created


# 1. Every /api route except POST /api/session returns 401 without a credential.
def test_every_route_requires_auth_except_session_login(tmp_path: Path) -> None:
    client, _conn = _make_app(tmp_path)
    for method, path, kwargs in _PROTECTED_REQUESTS:
        response = client.request(method, path, **kwargs)
        assert response.status_code == 401, f"{method} {path} should require auth"


# 2. A wrong token returns 401.
def test_wrong_token_returns_401(tmp_path: Path) -> None:
    client, _conn = _make_app(tmp_path)
    response = client.get(
        "/api/search", params={"q": "x"}, headers={"Authorization": "Bearer nope"}
    )
    assert response.status_code == 401


# 3. A correct token succeeds.
def test_correct_token_succeeds(tmp_path: Path) -> None:
    client, _conn = _make_app(tmp_path)
    response = client.get("/api/search", params={"q": "x"}, headers=_HEADERS)
    assert response.status_code == 200


# 4. create_app refuses an empty API_TOKEN.
def test_create_app_refuses_empty_token(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        create_app(_settings(tmp_path, token=""), embedder=_FakeEmbedder())


# 5. /search returns hits and honours limit.
def test_search_returns_hits_and_honours_limit(tmp_path: Path) -> None:
    client, conn = _make_app(tmp_path)
    for ref in ("a", "b", "c"):
        _insert_document(conn, ref, content="the quokka is a marsupial")

    response = client.get("/api/search", params={"q": "quokka", "limit": 2}, headers=_HEADERS)

    assert response.status_code == 200
    hits = response.json()
    assert len(hits) == 2
    assert hits[0]["snippet"]
    assert hits[0]["score"] > 0


# 6. A tombstoned document is absent from /search, /documents/{id} and /sets/{name}/records.
def test_tombstoned_document_is_invisible(tmp_path: Path) -> None:
    client, conn = _make_app(tmp_path)
    recipe = _create_recipe_set(conn)
    gone_id = _insert_document(conn, "gone", content="the quokka is a marsupial", deleted=True)
    assert recipe.id is not None
    _insert_membership(
        conn, gone_id, recipe.id, status="ok", extracted_json={"username": "x", "niche": "y"}
    )

    search_response = client.get("/api/search", params={"q": "quokka"}, headers=_HEADERS)
    assert all(hit["document_id"] != gone_id for hit in search_response.json())

    document_response = client.get(f"/api/documents/{gone_id}", headers=_HEADERS)
    assert document_response.status_code == 404

    records_response = client.get("/api/sets/recipe/records", headers=_HEADERS)
    assert all(record["document_id"] != gone_id for record in records_response.json())


# 7. /documents/{id} 404s for an unknown id.
def test_get_document_404_for_unknown_id(tmp_path: Path) -> None:
    client, _conn = _make_app(tmp_path)
    response = client.get("/api/documents/999999", headers=_HEADERS)
    assert response.status_code == 404


# 8. /sets/{name}/records returns parsed extracted_json and filters by status.
def test_set_records_parses_json_and_filters_by_status(tmp_path: Path) -> None:
    client, conn = _make_app(tmp_path)
    recipe = _create_recipe_set(conn)
    assert recipe.id is not None
    ok_id = _insert_document(conn, "ok-doc")
    partial_id = _insert_document(conn, "partial-doc")
    _insert_membership(
        conn, ok_id, recipe.id, status="ok", extracted_json={"username": "a", "niche": "b"}
    )
    _insert_membership(
        conn,
        partial_id,
        recipe.id,
        status="partial",
        extracted_json={"username": "c"},
        missing_fields=["niche"],
    )

    all_records = client.get("/api/sets/recipe/records", headers=_HEADERS).json()
    assert {record["document_id"] for record in all_records} == {ok_id, partial_id}
    by_id = {record["document_id"]: record for record in all_records}
    assert by_id[ok_id]["extracted_json"] == {"username": "a", "niche": "b"}
    assert by_id[partial_id]["missing_fields"] == ["niche"]

    filtered = client.get(
        "/api/sets/recipe/records", params={"status": "partial"}, headers=_HEADERS
    ).json()
    assert [record["document_id"] for record in filtered] == [partial_id]


# 9. /review returns only partial and failed.
def test_review_returns_only_partial_and_failed(tmp_path: Path) -> None:
    client, conn = _make_app(tmp_path)
    recipe = _create_recipe_set(conn)
    assert recipe.id is not None
    ok_id = _insert_document(conn, "ok-doc")
    partial_id = _insert_document(conn, "partial-doc")
    failed_id = _insert_document(conn, "failed-doc")
    _insert_membership(conn, ok_id, recipe.id, status="ok")
    _insert_membership(conn, partial_id, recipe.id, status="partial", missing_fields=["niche"])
    _insert_membership(conn, failed_id, recipe.id, status="failed", error="model error")

    response = client.get("/api/review", headers=_HEADERS)

    assert response.status_code == 200
    reviewed_ids = {item["document_id"] for item in response.json()}
    assert reviewed_ids == {partial_id, failed_id}


# 10. PATCH merges fields, updates missing_fields and status.
def test_patch_merges_fields_and_recomputes_status(tmp_path: Path) -> None:
    client, conn = _make_app(tmp_path)
    recipe = _create_recipe_set(conn)
    assert recipe.id is not None
    document_id = _insert_document(conn, "doc")
    _insert_membership(
        conn,
        document_id,
        recipe.id,
        status="partial",
        extracted_json={"username": "chef"},
        missing_fields=["niche"],
    )

    response = client.patch(
        f"/api/sets/recipe/records/{document_id}", json={"niche": "cooking"}, headers=_HEADERS
    )

    assert response.status_code == 200
    body = response.json()
    assert body["extracted_json"] == {"username": "chef", "niche": "cooking"}
    assert body["missing_fields"] == []
    assert body["status"] == "ok"


# 11. PATCH rejects a key absent from the schema.
def test_patch_rejects_unknown_field(tmp_path: Path) -> None:
    client, conn = _make_app(tmp_path)
    recipe = _create_recipe_set(conn)
    assert recipe.id is not None
    document_id = _insert_document(conn, "doc")
    _insert_membership(conn, document_id, recipe.id, status="pending")

    response = client.patch(
        f"/api/sets/recipe/records/{document_id}", json={"not_a_field": "x"}, headers=_HEADERS
    )

    assert response.status_code == 400


# 12. DELETE tombstones rather than removing the row.
def test_delete_tombstones_rather_than_removes(tmp_path: Path) -> None:
    client, conn = _make_app(tmp_path)
    document_id = _insert_document(conn, "doc")

    response = client.delete(f"/api/documents/{document_id}", headers=_HEADERS)

    assert response.status_code == 200
    row = conn.execute("SELECT deleted_at FROM documents WHERE id = ?", (document_id,)).fetchone()
    assert row is not None
    assert row["deleted_at"] is not None
    assert client.get(f"/api/documents/{document_id}", headers=_HEADERS).status_code == 404


def test_delete_reaches_a_failed_document(tmp_path: Path) -> None:
    # A failed ingest is a real row someone reviewing failed ingests should be able to clear out.
    # GET reaches it too now, so that a listed failure can be opened and read; deleting it is a
    # separate property, and this test is about the delete.
    client, conn = _make_app(tmp_path)
    document_id = _insert_document(conn, "boom", content=None, status="failed")
    assert client.get(f"/api/documents/{document_id}", headers=_HEADERS).status_code == 200

    response = client.delete(f"/api/documents/{document_id}", headers=_HEADERS)

    assert response.status_code == 200
    row = conn.execute("SELECT deleted_at FROM documents WHERE id = ?", (document_id,)).fetchone()
    assert row is not None and row["deleted_at"] is not None
    assert client.get(f"/api/documents/{document_id}", headers=_HEADERS).status_code == 404


# 13. No response body or error contains the token.
def test_no_response_leaks_token(tmp_path: Path) -> None:
    client, conn = _make_app(tmp_path)
    recipe = _create_recipe_set(conn)
    assert recipe.id is not None
    document_id = _insert_document(conn, "doc")
    _insert_membership(conn, document_id, recipe.id, status="pending")

    responses = [
        client.get("/api/search", params={"q": "x"}),
        client.get("/api/search", params={"q": "x"}, headers={"Authorization": "Bearer wrong"}),
        client.get("/api/documents/999999", headers=_HEADERS),
        client.get("/api/sets/missing", headers=_HEADERS),
        client.patch(f"/api/sets/recipe/records/{document_id}", json={"nope": 1}, headers=_HEADERS),
        client.get("/api/documents/1", headers=_HEADERS),
    ]
    for response in responses:
        assert _TOKEN not in response.text


# 14. /health needs no auth. A container healthcheck or uptime monitor cannot carry a credential,
# and the only other unauthenticated path is `/`, which the SPA fallback answers with 200 and the
# shell even when the API is broken. The route reads nothing and returns a fixed status.
def test_health_needs_no_auth(tmp_path: Path) -> None:
    client, _conn = _make_app(tmp_path)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# --- Session cookie auth: Decision 2's replacement for the in-memory bearer token. ---


def test_session_login_creates_a_row_and_sets_the_cookie(tmp_path: Path) -> None:
    client, conn = _make_app(tmp_path)

    response = client.post("/api/session", json={"token": _TOKEN})

    assert response.status_code == 200
    assert "session" in response.cookies
    row = conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()
    assert row["n"] == 1


def test_session_login_rejects_the_wrong_token(tmp_path: Path) -> None:
    client, conn = _make_app(tmp_path)

    response = client.post("/api/session", json={"token": "not-the-token"})

    assert response.status_code == 401
    assert "session" not in response.cookies
    row = conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()
    assert row["n"] == 0


def test_session_cookie_is_httponly_and_samesite_strict(tmp_path: Path) -> None:
    client, _conn = _make_app(tmp_path)

    response = client.post("/api/session", json={"token": _TOKEN})

    cookie_header = response.headers["set-cookie"].lower()
    assert "httponly" in cookie_header
    assert "samesite=strict" in cookie_header


def test_session_cookie_is_secure_only_when_the_request_is_https(tmp_path: Path) -> None:
    client, _conn = _make_app(tmp_path)

    http_response = client.post("/api/session", json={"token": _TOKEN})
    assert "secure" not in http_response.headers["set-cookie"].lower()

    https_client = TestClient(client.app, base_url="https://testserver")
    https_response = https_client.post("/api/session", json={"token": _TOKEN})
    assert "secure" in https_response.headers["set-cookie"].lower()


def test_session_table_stores_only_the_token_hash(tmp_path: Path) -> None:
    client, conn = _make_app(tmp_path)

    response = client.post("/api/session", json={"token": _TOKEN})
    session_token = response.cookies["session"]

    row = conn.execute("SELECT token_hash FROM sessions").fetchone()
    assert row["token_hash"] == hashlib.sha256(session_token.encode()).hexdigest()
    assert row["token_hash"] != session_token
    stored_values = [str(value) for value in row]
    assert session_token not in stored_values


def test_valid_session_cookie_authorizes_requests(tmp_path: Path) -> None:
    client, _conn = _make_app(tmp_path)
    assert client.post("/api/session", json={"token": _TOKEN}).status_code == 200

    # No Authorization header on any of these -- the cookie the client jar now holds carries it.
    assert client.get("/api/session").status_code == 200
    assert client.get("/api/search", params={"q": "x"}).status_code == 200


def test_expired_session_is_rejected_and_deleted(tmp_path: Path) -> None:
    client, conn = _make_app(tmp_path)
    token_hash = hashlib.sha256(b"expired-token").hexdigest()
    create_session(conn, token_hash, "2000-01-01 00:00:00")

    client.cookies.set("session", "expired-token")
    response = client.get("/api/session")

    assert response.status_code == 401
    row = conn.execute("SELECT 1 FROM sessions WHERE token_hash = ?", (token_hash,)).fetchone()
    assert row is None


def test_delete_session_revokes_it(tmp_path: Path) -> None:
    client, conn = _make_app(tmp_path)
    client.post("/api/session", json={"token": _TOKEN})
    assert client.get("/api/session").status_code == 200

    assert client.delete("/api/session").status_code == 200

    row = conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()
    assert row["n"] == 0
    assert client.get("/api/session").status_code == 401


def test_bearer_token_still_works_alongside_sessions(tmp_path: Path) -> None:
    client, _conn = _make_app(tmp_path)

    response = client.get("/api/search", params={"q": "x"}, headers=_HEADERS)

    assert response.status_code == 200


# --- Route coverage beyond the 14 required tests: every remaining route the brief lists. ---


def test_list_sets_returns_created_set(tmp_path: Path) -> None:
    client, conn = _make_app(tmp_path)
    _create_recipe_set(conn)

    response = client.get("/api/sets", headers=_HEADERS)

    assert response.status_code == 200
    names = [set_out["name"] for set_out in response.json()]
    assert names == ["recipe"]


def test_get_one_set_returns_schema(tmp_path: Path) -> None:
    client, conn = _make_app(tmp_path)
    _create_recipe_set(conn)

    response = client.get("/api/sets/recipe", headers=_HEADERS)

    assert response.status_code == 200
    assert response.json()["schema"] == _SCHEMA


def test_create_set_round_trips_and_appears_in_list(tmp_path: Path) -> None:
    client, _conn = _make_app(tmp_path)

    response = client.post(
        "/api/sets",
        json={"name": "recipe", "description": "a cooking recipe", "schema": _SCHEMA},
        headers=_HEADERS,
    )

    assert response.status_code == 201
    body = response.json()
    assert body == {"name": "recipe", "description": "a cooking recipe", "schema": _SCHEMA}
    names = [set_out["name"] for set_out in client.get("/api/sets", headers=_HEADERS).json()]
    assert names == ["recipe"]


def test_create_set_rejects_non_object_schema_with_server_message(tmp_path: Path) -> None:
    client, _conn = _make_app(tmp_path)

    response = client.post(
        "/api/sets",
        json={
            "name": "recipe",
            "description": "a cooking recipe",
            "schema": ["not", "an", "object"],
        },
        headers=_HEADERS,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "schema must be a JSON object"


def test_create_set_rejects_missing_properties_with_server_message(tmp_path: Path) -> None:
    client, _conn = _make_app(tmp_path)

    response = client.post(
        "/api/sets",
        json={"name": "recipe", "description": "a cooking recipe", "schema": {"type": "object"}},
        headers=_HEADERS,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == 'schema must have a non-empty "properties" object'


def test_create_set_rejects_empty_description_with_server_message(tmp_path: Path) -> None:
    client, _conn = _make_app(tmp_path)

    response = client.post(
        "/api/sets",
        json={"name": "recipe", "description": "   ", "schema": _SCHEMA},
        headers=_HEADERS,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "description must not be empty"


def test_create_set_rejects_empty_name_with_server_message(tmp_path: Path) -> None:
    client, _conn = _make_app(tmp_path)

    response = client.post(
        "/api/sets",
        json={"name": "  ", "description": "a cooking recipe", "schema": _SCHEMA},
        headers=_HEADERS,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "name must not be empty"


def test_create_set_duplicate_name_returns_conflict_not_500(tmp_path: Path) -> None:
    client, conn = _make_app(tmp_path)
    _create_recipe_set(conn)

    response = client.post(
        "/api/sets",
        json={"name": "recipe", "description": "a second recipe set", "schema": _SCHEMA},
        headers=_HEADERS,
    )

    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


def test_update_set_changes_description_and_schema(tmp_path: Path) -> None:
    client, conn = _make_app(tmp_path)
    _create_recipe_set(conn)
    new_schema: dict[str, object] = {
        "type": "object",
        "properties": {"username": {"type": "string"}},
    }

    response = client.patch(
        "/api/sets/recipe",
        json={"description": "an updated recipe", "schema": new_schema},
        headers=_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["description"] == "an updated recipe"
    assert body["schema"] == new_schema
    refetched = client.get("/api/sets/recipe", headers=_HEADERS).json()
    assert refetched["description"] == "an updated recipe"
    assert refetched["schema"] == new_schema


def test_update_missing_set_returns_404(tmp_path: Path) -> None:
    client, _conn = _make_app(tmp_path)

    response = client.patch("/api/sets/missing", json={"description": "x"}, headers=_HEADERS)

    assert response.status_code == 404


def test_delete_set_removes_set_and_its_memberships(tmp_path: Path) -> None:
    client, conn = _make_app(tmp_path)
    recipe = _create_recipe_set(conn)
    assert recipe.id is not None
    document_id = _insert_document(conn, "doc")
    _insert_membership(conn, document_id, recipe.id, status="ok")

    response = client.delete("/api/sets/recipe", headers=_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["records_removed"] == 1
    assert client.get("/api/sets/recipe", headers=_HEADERS).status_code == 404
    remaining = conn.execute(
        "SELECT COUNT(*) AS n FROM set_memberships WHERE set_id = ?", (recipe.id,)
    ).fetchone()
    assert remaining["n"] == 0


def test_delete_missing_set_returns_404(tmp_path: Path) -> None:
    client, _conn = _make_app(tmp_path)

    response = client.delete("/api/sets/missing", headers=_HEADERS)

    assert response.status_code == 404


def test_get_document_returns_full_document(tmp_path: Path) -> None:
    client, conn = _make_app(tmp_path)
    document_id = _insert_document(conn, "doc", title="Title", url="https://example.test/x")

    response = client.get(f"/api/documents/{document_id}", headers=_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == document_id
    assert body["title"] == "Title"
    assert body["url"] == "https://example.test/x"


def test_related_documents_uses_stored_embeddings(tmp_path: Path) -> None:
    client, conn = _make_app(tmp_path)
    anchor_id = _insert_document(conn, "anchor")
    neighbor_id = _insert_document(conn, "neighbor")
    same_vector = np.array([1.0, 0.0], dtype=np.float32)
    for document_id in (anchor_id, neighbor_id):
        conn.execute(
            "INSERT INTO chunks (document_id, ordinal, text, token_count, embedding) "
            "VALUES (?, 0, 'chunk text', 2, ?)",
            (document_id, pack_vector(same_vector)),
        )
    conn.commit()

    response = client.get(f"/api/documents/{anchor_id}/related", headers=_HEADERS)

    assert response.status_code == 200
    related_ids = [hit["document_id"] for hit in response.json()]
    assert related_ids == [neighbor_id]


def test_list_documents_orders_newest_first_with_id_tiebreak(tmp_path: Path) -> None:
    # A sync writes several rows inside the same second; created_at alone can't order them.
    client, conn = _make_app(tmp_path)
    a = _insert_document(conn, "a")
    b = _insert_document(conn, "b")
    c = _insert_document(conn, "c")
    for document_id in (a, b, c):
        _set_created_at(conn, document_id, "2026-01-01 09:48:43")

    response = client.get("/api/documents", headers=_HEADERS)

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [c, b, a]


def test_list_documents_pagination_survives_insert_between_page_fetches(tmp_path: Path) -> None:
    client, conn = _make_app(tmp_path)
    doc1 = _insert_document(conn, "doc1")
    doc2 = _insert_document(conn, "doc2")
    doc3 = _insert_document(conn, "doc3")
    _set_created_at(conn, doc1, "2026-01-01 09:48:43")
    _set_created_at(conn, doc2, "2026-01-01 09:48:44")
    _set_created_at(conn, doc3, "2026-01-01 09:48:45")

    first_page = client.get("/api/documents", params={"limit": 2}, headers=_HEADERS).json()
    assert [item["id"] for item in first_page["items"]] == [doc3, doc2]
    cursor = first_page["next_cursor"]
    assert cursor is not None

    # A sync adds a new, newer-than-anything-seen-so-far document while the client is between
    # page fetches. An OFFSET-based query would let this shift doc2 back into view a second
    # time; keyset pagination must not.
    doc4 = _insert_document(conn, "doc4")
    _set_created_at(conn, doc4, "2026-01-01 09:48:46")

    second_page = client.get("/api/documents", params={"cursor": cursor}, headers=_HEADERS).json()
    assert [item["id"] for item in second_page["items"]] == [doc1]
    assert second_page["next_cursor"] is None


def test_list_documents_excludes_tombstoned_rows(tmp_path: Path) -> None:
    client, conn = _make_app(tmp_path)
    alive_id = _insert_document(conn, "alive")
    gone_id = _insert_document(conn, "gone", deleted=True)

    response = client.get("/api/documents", headers=_HEADERS)

    ids = [item["id"] for item in response.json()["items"]]
    assert alive_id in ids
    assert gone_id not in ids


def test_list_documents_includes_failed_and_pending_documents(tmp_path: Path) -> None:
    # SELECTOR_SQL (status = 'ok' AND content IS NOT NULL) would hide both of these; this list
    # exists precisely to surface documents that never finished ingesting.
    client, conn = _make_app(tmp_path)
    failed_id = _insert_document(conn, "failed-doc", content=None, status="failed")
    conn.execute("UPDATE documents SET error = 'boom' WHERE id = ?", (failed_id,))
    conn.commit()
    pending_id = _insert_document(conn, "pending-doc", content=None, status="pending")

    response = client.get("/api/documents", headers=_HEADERS)

    items = {item["id"]: item for item in response.json()["items"]}
    assert items[failed_id]["status"] == "failed"
    assert items[failed_id]["error"] == "boom"
    assert items[pending_id]["status"] == "pending"


def test_list_documents_ceiling_is_rejected_not_clamped(tmp_path: Path) -> None:
    client, _conn = _make_app(tmp_path)

    response = client.get("/api/documents", params={"limit": 201}, headers=_HEADERS)

    assert response.status_code == 400


def test_list_documents_rejects_malformed_cursor(tmp_path: Path) -> None:
    client, _conn = _make_app(tmp_path)

    response = client.get(
        "/api/documents", params={"cursor": "not-a-real-cursor!!"}, headers=_HEADERS
    )

    assert response.status_code == 400


def test_list_documents_computes_embedded_enriched_and_set_names(tmp_path: Path) -> None:
    client, conn = _make_app(tmp_path)
    recipe = _create_recipe_set(conn)
    assert recipe.id is not None
    bare_id = _insert_document(conn, "bare")
    indexed_id = _insert_document(conn, "indexed")
    conn.execute(
        "INSERT INTO chunks (document_id, ordinal, text, token_count, embedding) "
        "VALUES (?, 0, 'chunk text', 2, ?)",
        (indexed_id, pack_vector(np.array([1.0, 0.0], dtype=np.float32))),
    )
    conn.execute("UPDATE documents SET enriched_hash = 'abc123' WHERE id = ?", (indexed_id,))
    conn.commit()
    _insert_membership(conn, indexed_id, recipe.id, status="ok")

    response = client.get("/api/documents", headers=_HEADERS)

    items = {item["id"]: item for item in response.json()["items"]}
    assert items[bare_id]["embedded"] is False
    assert items[bare_id]["enriched"] is False
    assert items[bare_id]["set_names"] == []
    assert items[indexed_id]["embedded"] is True
    assert items[indexed_id]["enriched"] is True
    assert items[indexed_id]["set_names"] == ["recipe"]


def test_get_document_computes_embedded_enriched_and_set_names(tmp_path: Path) -> None:
    client, conn = _make_app(tmp_path)
    recipe = _create_recipe_set(conn)
    assert recipe.id is not None
    bare_id = _insert_document(conn, "bare")
    indexed_id = _insert_document(conn, "indexed")
    conn.execute(
        "INSERT INTO chunks (document_id, ordinal, text, token_count, embedding) "
        "VALUES (?, 0, 'chunk text', 2, ?)",
        (indexed_id, pack_vector(np.array([1.0, 0.0], dtype=np.float32))),
    )
    conn.execute("UPDATE documents SET enriched_hash = 'abc123' WHERE id = ?", (indexed_id,))
    conn.commit()
    _insert_membership(conn, indexed_id, recipe.id, status="ok")

    bare = client.get(f"/api/documents/{bare_id}", headers=_HEADERS).json()
    indexed = client.get(f"/api/documents/{indexed_id}", headers=_HEADERS).json()

    assert bare["embedded"] is False
    assert bare["enriched"] is False
    assert bare["set_names"] == []
    assert indexed["embedded"] is True
    assert indexed["enriched"] is True
    assert indexed["set_names"] == ["recipe"]


def test_ingest_stores_document_under_caller_marker(tmp_path: Path) -> None:
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
    client, conn = _make_app(tmp_path, fetcher=_as_fetcher(fetcher))

    response = client.post(
        "/api/ingest", json={"url": "https://example.test/new", "source": "mcp"}, headers=_HEADERS
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    row = conn.execute(
        "SELECT source FROM documents WHERE id = ?", (body["document_id"],)
    ).fetchone()
    assert row["source"] == "mcp"


# The SPA mount is skipped entirely when there's no build to serve, so a checkout with no
# `npm run build` yet still runs `garden serve` -- an unmatched path just 404s as it always did.
def test_spa_not_mounted_without_frontend_dist(tmp_path: Path) -> None:
    client, _conn = _make_app(tmp_path, frontend_dist=tmp_path / "no-such-dist")
    assert client.get("/", headers=_HEADERS).status_code == 404
    assert client.get("/api/health", headers=_HEADERS).status_code == 200  # the API still works


# /api and the static shell are disjoint namespaces, but an unknown id under /api must still
# reach the document router's own 404, not get swallowed by the SPA catch-all mounted after it.
def test_api_route_wins_over_spa_fallback(tmp_path: Path) -> None:
    dist_dir = _build_fake_dist(tmp_path)
    client, _conn = _make_app(tmp_path, frontend_dist=dist_dir)
    response = client.get("/api/documents/999999", headers=_HEADERS)
    assert response.status_code == 404
    assert response.json() == {"detail": "document not found"}


# The shell and its assets load with no token, because a browser attaches no bearer header to
# its first navigation to the page. They hold no garden data.
def test_spa_serves_shell_and_assets_without_a_token(tmp_path: Path) -> None:
    dist_dir = _build_fake_dist(tmp_path)
    client, _conn = _make_app(tmp_path, frontend_dist=dist_dir)

    shell_response = client.get("/")
    assert shell_response.status_code == 200
    assert shell_response.text == "<html>SPA-INDEX</html>"

    asset_response = client.get("/assets/app.js")
    assert asset_response.status_code == 200
    assert asset_response.text == "console.log('app')"


# Decision: an unmatched path outside /api falls back to the shell, unauthenticated, so the
# client-side router can own it -- a fresh tab's first request for e.g. /documents/42 has no
# server-side route to match, and needs the same index.html `/` gets. This must not turn into a
# way to dodge auth on real data: an unmatched path under /api still requires a credential.
def test_unmatched_path_falls_back_to_the_shell_without_a_token(tmp_path: Path) -> None:
    dist_dir = _build_fake_dist(tmp_path)
    client, _conn = _make_app(tmp_path, frontend_dist=dist_dir)

    response = client.get("/not-a-built-asset")
    assert response.status_code == 200
    assert response.text == "<html>SPA-INDEX</html>"

    deep_link = client.get("/documents/42")
    assert deep_link.status_code == 200
    assert deep_link.text == "<html>SPA-INDEX</html>"

    assert client.get("/api/not-a-real-route").status_code == 401


# `..` in a request path must not escape the built directory and serve the rest of the disk.
def test_spa_refuses_to_serve_files_outside_dist(tmp_path: Path) -> None:
    dist_dir = _build_fake_dist(tmp_path)
    (tmp_path / "secret.txt").write_text("BEEPER_ACCESS_TOKEN=live-token")
    client, _conn = _make_app(tmp_path, frontend_dist=dist_dir)

    # %2e%2e rather than a literal ".." -- httpx's own client collapses a literal dot-segment
    # before the request ever leaves the test process, which would make this pass even with no
    # traversal check at all. Percent-encoding survives that normalization, the same way it would
    # against a real proxy, and still decodes to ".." by the time `_public_file` sees `full_path`.
    for path in ("/%2e%2e/secret.txt", "/assets/%2e%2e/%2e%2e/secret.txt"):
        response = client.get(path, headers=_HEADERS)
        assert response.status_code == 404, path
        assert "live-token" not in response.text


# The Documents page deliberately lists failed and pending documents, so fetching one by id must
# work too. `get_document` used to apply `SELECTOR_SQL` (content not null, status ok) and answered
# 404 "document not found" for a row the list had just shown, dead-ending the one page built to
# surface those rows.
def test_get_document_returns_failed_and_pending_documents(tmp_path: Path) -> None:
    client, conn = _make_app(tmp_path)
    failed_id = _insert_document(conn, "failed-fetch", status="failed", content=None)
    conn.execute("UPDATE documents SET error = 'HTTP 404' WHERE id = ?", (failed_id,))
    pending_id = _insert_document(conn, "not-fetched-yet", status="pending", content=None)
    conn.commit()

    listed = client.get("/api/documents?limit=50", headers=_HEADERS).json()
    assert {failed_id, pending_id} <= {item["id"] for item in listed["items"]}

    failed = client.get(f"/api/documents/{failed_id}", headers=_HEADERS)
    assert failed.status_code == 200
    assert failed.json()["status"] == "failed"
    assert failed.json()["error"] == "HTTP 404"

    assert client.get(f"/api/documents/{pending_id}", headers=_HEADERS).status_code == 200


# A tombstoned document stays gone: dropping SELECTOR_SQL must not also drop the delete filter.
def test_get_document_still_hides_tombstoned_documents(tmp_path: Path) -> None:
    client, conn = _make_app(tmp_path)
    document_id = _insert_document(conn, "to-be-deleted")
    conn.commit()
    assert client.get(f"/api/documents/{document_id}", headers=_HEADERS).status_code == 200

    assert client.delete(f"/api/documents/{document_id}", headers=_HEADERS).status_code == 200
    assert client.get(f"/api/documents/{document_id}", headers=_HEADERS).status_code == 404


# Health being public must not widen anything else, and must not depend on the method.
def test_health_exemption_does_not_leak_to_other_api_routes(tmp_path: Path) -> None:
    client, _conn = _make_app(tmp_path)

    assert client.get("/api/documents").status_code == 401
    assert client.get("/api/search?q=x").status_code == 401
    assert client.get("/api/sets").status_code == 401
    assert client.get("/api/session").status_code == 401
    assert client.post("/api/health").status_code == 401
