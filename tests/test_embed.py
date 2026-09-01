import json
import re
import sqlite3
from collections.abc import Sequence
from pathlib import Path

import httpx
import numpy as np
import pytest
from numpy.typing import NDArray

from links_garden.config import Settings
from links_garden.db import connect, initialize
from links_garden.embed import (
    Embedder,
    IndexReport,
    index_documents,
    pack_vector,
    unpack_vector,
)


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(_env_file=None, database_path=tmp_path / "garden.db", **overrides)  # type: ignore[arg-type]


def _embed_response(count: int, dims: int = 4) -> httpx.Response:
    return httpx.Response(200, json={"embeddings": [[float(i)] * dims for i in range(count)]})


def _client(handler: object) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


def test_embed_posts_configured_model_and_returns_one_vector_per_input(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _embed_response(3)

    settings = _settings(tmp_path, embedding_model="bge-m3")
    embedder = Embedder(settings, client=_client(handler))

    vectors = embedder.embed(["a", "b", "c"])

    assert len(requests) == 1
    assert requests[0].url.path == "/api/embed"
    payload = json.loads(requests[0].content)
    assert payload["model"] == "bge-m3"
    assert payload["input"] == ["a", "b", "c"]
    assert len(vectors) == 3
    assert all(vector.dtype == np.float32 for vector in vectors)


def test_batching_splits_forty_item_input_into_three_calls(tmp_path: Path) -> None:
    batch_sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        batch_sizes.append(len(payload["input"]))
        return _embed_response(len(payload["input"]))

    embedder = Embedder(_settings(tmp_path), client=_client(handler))

    vectors = embedder.embed([f"text {i}" for i in range(40)])

    assert batch_sizes == [16, 16, 8]
    assert len(vectors) == 40


def test_unreachable_ollama_raises_clear_error_naming_url_and_model(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    settings = _settings(tmp_path, ollama_url="http://127.0.0.1:11434", embedding_model="bge-m3")
    embedder = Embedder(settings, client=_client(handler))

    with pytest.raises(RuntimeError, match=re.escape("127.0.0.1:11434")) as exc_info:
        embedder.embed(["a"])
    assert "bge-m3" in str(exc_info.value)


def test_check_returns_false_when_model_absent_true_when_present(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})

    settings = _settings(tmp_path, embedding_model="bge-m3")
    embedder = Embedder(settings, client=_client(handler))
    assert embedder.check() is False

    def handler_present(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "bge-m3:latest"}]})

    embedder = Embedder(settings, client=_client(handler_present))
    assert embedder.check() is True


def test_pack_and_unpack_vector_round_trip_exactly(tmp_path: Path) -> None:
    vector = np.array([0.5, -1.25, 3.0, 0.0, 1e-8], dtype=np.float32)

    blob = pack_vector(vector)
    result = unpack_vector(blob)

    assert result.dtype == np.float32
    assert result.shape == vector.shape
    np.testing.assert_array_equal(result, vector)


# --- index_documents ---


class _FakeEmbedder:
    """Returns a deterministic small vector per input text; never touches the network."""

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.calls: list[str] = []
        self._fail_on = fail_on

    def embed(self, texts: Sequence[str]) -> list[NDArray[np.float32]]:
        vectors = []
        for text in texts:
            if self._fail_on is not None and self._fail_on in text:
                raise RuntimeError("embedding boom")
            self.calls.append(text)
            vectors.append(np.array([float(len(text))], dtype=np.float32))
        return vectors


def _open(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "garden.db")
    initialize(conn)
    return conn


def _insert_document(
    conn: sqlite3.Connection,
    source_ref: str = "ref-1",
    *,
    content: str | None = "some content",
    message_text: str | None = None,
    status: str = "ok",
    deleted: bool = False,
) -> int:
    cursor = conn.execute(
        "INSERT INTO documents (source, source_ref, content, message_text, status, deleted_at) "
        "VALUES ('manual', ?, ?, ?, ?, ?)",
        (source_ref, content, message_text, status, "2026-01-01" if deleted else None),
    )
    conn.commit()
    assert cursor.lastrowid is not None
    return cursor.lastrowid


def _chunk_rows(conn: sqlite3.Connection, document_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM chunks WHERE document_id = ? ORDER BY ordinal", (document_id,)
    ).fetchall()


def test_index_documents_chunks_and_writes_rows_for_a_qualifying_document(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    document_id = _insert_document(conn, content="paragraph one.\n\nparagraph two.")
    embedder = _FakeEmbedder()

    report = index_documents(conn, _settings(tmp_path), embedder)

    rows = _chunk_rows(conn, document_id)
    assert len(rows) >= 1
    assert all(row["embedding"] is not None for row in rows)
    assert report.documents_indexed == 1
    assert report.chunks_written == len(rows)


def test_selector_excludes_document_with_null_content(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    _insert_document(conn, content=None)

    report = index_documents(conn, _settings(tmp_path), _FakeEmbedder())

    assert report.documents_seen == 0
    assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0


def test_selector_excludes_document_with_failed_status(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    _insert_document(conn, status="failed")

    report = index_documents(conn, _settings(tmp_path), _FakeEmbedder())

    assert report.documents_seen == 0
    assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0


def test_selector_excludes_deleted_document(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    _insert_document(conn, deleted=True)

    report = index_documents(conn, _settings(tmp_path), _FakeEmbedder())

    assert report.documents_seen == 0
    assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0


def test_rerunning_indexes_nothing_when_chunks_hash_is_unchanged(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    _insert_document(conn)
    settings = _settings(tmp_path)
    first_embedder = _FakeEmbedder()
    index_documents(conn, settings, first_embedder)

    second_embedder = _FakeEmbedder()
    report = index_documents(conn, settings, second_embedder)

    assert report.documents_indexed == 0
    assert report.documents_skipped == 1
    assert second_embedder.calls == []


def test_changing_content_rechunks_and_leaves_no_orphan_chunks(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    document_id = _insert_document(conn, content="x" * 10)
    settings = _settings(tmp_path)
    index_documents(conn, settings, _FakeEmbedder())
    old_rows = _chunk_rows(conn, document_id)
    assert len(old_rows) == 1
    assert old_rows[0]["text"] == "x" * 10

    conn.execute("UPDATE documents SET content = ? WHERE id = ?", ("y" * 20, document_id))
    conn.commit()
    report = index_documents(conn, settings, _FakeEmbedder())

    new_rows = _chunk_rows(conn, document_id)
    assert report.documents_indexed == 1
    assert all(row["text"] != "x" * 10 for row in new_rows)
    assert any(row["text"] == "y" * 20 for row in new_rows)
    # No orphan left behind: exactly the chunks the new content produces, nothing more.
    assert len(new_rows) == 1


def test_changing_the_embedding_model_reindexes_unchanged_content(tmp_path: Path) -> None:
    # A stale chunks_hash keyed on text alone would skip every document after a model
    # change, leaving old vectors of the wrong dimension mixed in with (or entirely
    # replaced by) new ones and crashing search's matmul instead of just re-embedding.
    conn = _open(tmp_path)
    _insert_document(conn)
    index_documents(conn, _settings(tmp_path, embedding_model="bge-m3"), _FakeEmbedder())

    report = index_documents(
        conn, _settings(tmp_path, embedding_model="nomic-embed-text"), _FakeEmbedder()
    )

    assert report.documents_indexed == 1
    assert report.documents_skipped == 0


def test_one_document_failing_does_not_stop_later_documents_from_indexing(
    tmp_path: Path,
) -> None:
    conn = _open(tmp_path)
    _insert_document(conn, "ref-boom", content="boom content")
    _insert_document(conn, "ref-ok", content="fine content")
    embedder = _FakeEmbedder(fail_on="boom")

    report = index_documents(conn, _settings(tmp_path), embedder)

    assert report.documents_failed == 1
    assert report.documents_indexed == 1
    ok_id = conn.execute("SELECT id FROM documents WHERE source_ref = 'ref-ok'").fetchone()["id"]
    assert len(_chunk_rows(conn, ok_id)) == 1


def test_message_text_appears_in_chunk_text_ahead_of_content(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    document_id = _insert_document(
        conn, content="the extracted content", message_text="why I saved this"
    )

    index_documents(conn, _settings(tmp_path), _FakeEmbedder())

    rows = _chunk_rows(conn, document_id)
    assert len(rows) == 1
    text = rows[0]["text"]
    assert text.index("why I saved this") < text.index("the extracted content")


def test_index_report_counts_match_the_operations_performed(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    settings = _settings(tmp_path)
    _insert_document(conn, "ref-unchanged", content="stable content")
    # Index once so 'ref-unchanged' has a current chunks_hash before the run under test; the
    # other three documents are added afterwards so this warm-up run never touches them.
    index_documents(conn, settings, _FakeEmbedder())
    _insert_document(conn, "ref-fails", content="boom content")
    _insert_document(conn, "ref-new", content="fresh content")
    _insert_document(conn, "ref-excluded", content=None)

    report = index_documents(conn, settings, _FakeEmbedder(fail_on="boom"))

    assert isinstance(report, IndexReport)
    assert report.documents_seen == 3  # excludes ref-excluded
    assert report.documents_skipped == 1  # ref-unchanged
    assert report.documents_failed == 1  # ref-fails
    assert report.documents_indexed == 1  # ref-new
    assert report.chunks_written == 1
