import sqlite3
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from links_garden.config import Settings
from links_garden.db import connect, initialize, tombstone
from links_garden.embed import pack_vector
from links_garden.search import search


class _FakeEmbedder:
    """Returns a caller-chosen vector for an exact text match, else a fixed default.

    Chunk vectors are written straight into the `chunks` table by these tests, so this fake
    only ever needs to answer `search()`'s one call to embed the query itself.
    """

    def __init__(
        self,
        vectors: dict[str, NDArray[np.float32]] | None = None,
        default: NDArray[np.float32] | None = None,
    ) -> None:
        self._vectors = vectors or {}
        self._default = default if default is not None else np.array([1.0, 0.0], dtype=np.float32)

    def embed(self, texts: Sequence[str]) -> list[NDArray[np.float32]]:
        return [self._vectors.get(text, self._default) for text in texts]


def _vec(*values: float) -> NDArray[np.float32]:
    return np.array(values, dtype=np.float32)


def _open(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "garden.db")
    initialize(conn)
    return conn


def _settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, database_path=tmp_path / "garden.db")


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


def _insert_chunk(
    conn: sqlite3.Connection,
    document_id: int,
    text: str,
    vector: NDArray[np.float32],
    *,
    ordinal: int = 0,
) -> None:
    conn.execute(
        "INSERT INTO chunks (document_id, ordinal, text, token_count, embedding) "
        "VALUES (?, ?, ?, ?, ?)",
        (document_id, ordinal, text, len(text), pack_vector(vector)),
    )
    conn.commit()


def test_term_present_in_only_one_document_returns_that_document(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    quokka = _insert_document(conn, "a", content="the quokka is a marsupial")
    _insert_document(conn, "b", content="unrelated text about something else")

    hits = search(conn, _settings(tmp_path), _FakeEmbedder(), "quokka")

    assert [hit.document_id for hit in hits] == [quokka]


def test_tombstoned_document_never_appears(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    alive = _insert_document(conn, "a", content="widget catalog")
    gone = _insert_document(conn, "b", content="widget catalog")
    query_vector = _vec(1.0, 0.0)
    embedder = _FakeEmbedder({"widget catalog query": query_vector})
    # Chunk rows are written while both documents are still live, then `gone` is tombstoned
    # afterwards through the real `tombstone()` — reproducing that `index_documents` never
    # revisits a document to clean up its chunks, so a stale chunk row is exactly what a real
    # tombstone leaves behind. A doc created pre-tombstoned would prove the filter works without
    # ever exercising the actual leak.
    _insert_chunk(conn, alive, "widget chunk", query_vector)
    _insert_chunk(conn, gone, "widget chunk", query_vector)
    tombstone(conn, gone)

    hits = search(conn, _settings(tmp_path), embedder, "widget catalog query")

    document_ids = [hit.document_id for hit in hits]
    assert alive in document_ids
    assert gone not in document_ids


def test_stale_chunk_for_failed_status_document_is_excluded(tmp_path: Path) -> None:
    # Same leak as the tombstone case, through `status` instead of `deleted_at`: a re-sync that
    # marks a document 'failed' after it was already indexed leaves both its chunk row and its
    # FTS row in place. Content shares the query term so this exercises both sides' filters, not
    # just the vector side's — the chunk (and FTS row) are written while the document is still
    # 'ok', and status flips afterwards, reproducing the real leak rather than a synthetic one.
    conn = _open(tmp_path)
    document_id = _insert_document(conn, "a", content="zephyr kite festival")
    query_vector = _vec(1.0, 0.0)
    _insert_chunk(conn, document_id, "zephyr kite festival", query_vector)
    conn.execute("UPDATE documents SET status = 'failed' WHERE id = ?", (document_id,))
    conn.commit()
    embedder = _FakeEmbedder({"zephyr": query_vector})

    hits = search(conn, _settings(tmp_path), embedder, "zephyr")

    assert hits == []


def test_stale_fts_row_for_failed_status_document_with_no_chunk_is_excluded(
    tmp_path: Path,
) -> None:
    # No chunk at all, so only the FTS path could leak this document — a partial extraction can
    # leave a title behind even though content stayed NULL, and the vector-side fix alone can't
    # catch that since there's no chunk for it to filter.
    conn = _open(tmp_path)
    document_id = _insert_document(conn, "a", title="octopus garden notes", content=None)
    conn.execute("UPDATE documents SET status = 'failed' WHERE id = ?", (document_id,))
    conn.commit()

    hits = search(conn, _settings(tmp_path), _FakeEmbedder(), "octopus")

    assert hits == []


def test_tied_rrf_score_breaks_by_document_id(tmp_path: Path) -> None:
    # Two documents score identically under RRF: one is FTS rank 1 with no vector candidate at
    # all, the other is vector rank 1 with no FTS term match. RRF itself doesn't order these
    # against each other; only the explicit tie-break in `_fuse` does.
    conn = _open(tmp_path)
    fts_only = _insert_document(conn, "a", content="marmot sighting")
    vector_only = _insert_document(conn, "b", content="no shared terms")
    query_vector = _vec(1.0, 0.0)
    _insert_chunk(conn, vector_only, "no shared terms", query_vector)
    embedder = _FakeEmbedder({"marmot": query_vector})

    hits = search(conn, _settings(tmp_path), embedder, "marmot")

    assert [hit.document_id for hit in hits] == sorted([fts_only, vector_only])


def test_partial_term_match_does_not_outrank_full_term_match(tmp_path: Path) -> None:
    # AND-first FTS: a document matching every query term is found by the precise AND query.
    # A document sharing only one of three terms must not flood in via a looser OR match once
    # the precise query already found something.
    conn = _open(tmp_path)
    full_match = _insert_document(conn, "a", content="muscle training program guide")
    _insert_document(conn, "b", content="training language model benchmark")

    hits = search(conn, _settings(tmp_path), _FakeEmbedder(), "muscle training program")

    assert [hit.document_id for hit in hits] == [full_match]


def test_vector_only_match_ranks_with_no_fts_term_match(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    document_id = _insert_document(conn, "a", content="no shared words here")
    query_vector = _vec(1.0, 0.0)
    _insert_chunk(conn, document_id, "closely related chunk", query_vector)
    embedder = _FakeEmbedder({"zephyr": query_vector})

    hits = search(conn, _settings(tmp_path), embedder, "zephyr")

    assert len(hits) == 1
    assert hits[0].document_id == document_id
    assert hits[0].fts_rank is None
    assert hits[0].vector_rank == 1


def test_fts_only_match_ranks_even_when_vector_is_far(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    document_id = _insert_document(conn, "a", content="kumquat marmalade recipe")
    query_vector = _vec(1.0, 0.0)
    far_vector = _vec(-1.0, 0.0)
    _insert_chunk(conn, document_id, "kumquat marmalade recipe", far_vector)
    embedder = _FakeEmbedder({"kumquat": query_vector})

    hits = search(conn, _settings(tmp_path), embedder, "kumquat")

    assert len(hits) == 1
    assert hits[0].document_id == document_id
    assert hits[0].fts_rank == 1
    assert hits[0].vector_rank == 1  # only chunk in the corpus, however far


def test_document_matching_both_sides_outranks_one_matching_only_one(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    query_vector = _vec(1.0, 0.0)
    far_vector = _vec(-1.0, 0.0)
    both = _insert_document(conn, "both", content="marigold festival guide")
    fts_only = _insert_document(conn, "fts-only", content="marigold planting tips")
    vector_only = _insert_document(conn, "vector-only", content="totally unrelated words")
    _insert_chunk(conn, both, "marigold festival guide", query_vector)
    _insert_chunk(conn, fts_only, "marigold planting tips", far_vector)
    _insert_chunk(conn, vector_only, "totally unrelated words", query_vector)
    embedder = _FakeEmbedder({"marigold": query_vector})

    hits = search(conn, _settings(tmp_path), embedder, "marigold")

    scores = {hit.document_id: hit.score for hit in hits}
    assert scores[both] > scores[fts_only]
    assert scores[both] > scores[vector_only]


def test_rrf_scoring_is_correct_for_a_known_pair_of_ranks(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    query_vector = _vec(1.0, 0.0)
    farther_vector = _vec(0.8, 0.6)
    closer_vector = _vec(0.999, 0.001)
    # "zulu" appears only in doc_a, so doc_a is the sole (and therefore rank-1) FTS match.
    # doc_b's vector is closer to the query, so it takes vector rank 1 and pushes doc_a to
    # vector rank 2 — a known, non-trivial (fts_rank, vector_rank) pair to check the arithmetic.
    doc_a = _insert_document(conn, "a", content="zulu alpha")
    doc_b = _insert_document(conn, "b", content="no matching term")
    _insert_chunk(conn, doc_a, "zulu alpha", farther_vector)
    _insert_chunk(conn, doc_b, "no matching term", closer_vector)
    embedder = _FakeEmbedder({"zulu": query_vector})

    hits = search(conn, _settings(tmp_path), embedder, "zulu")

    hit_a = next(hit for hit in hits if hit.document_id == doc_a)
    assert hit_a.fts_rank == 1
    assert hit_a.vector_rank == 2
    assert hit_a.score == pytest.approx(1 / (60 + 1) + 1 / (60 + 2))


def test_documents_best_chunk_supplies_the_snippet(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    document_id = _insert_document(conn, "a", content="unrelated body text")
    query_vector = _vec(1.0, 0.0)
    far_vector = _vec(-1.0, 0.0)
    _insert_chunk(conn, document_id, "the far, less relevant chunk", far_vector, ordinal=0)
    _insert_chunk(conn, document_id, "the closest, best-matching chunk", query_vector, ordinal=1)
    embedder = _FakeEmbedder({"needle": query_vector})

    hits = search(conn, _settings(tmp_path), embedder, "needle")

    assert hits[0].snippet == "the closest, best-matching chunk"


def test_apostrophe_in_query_does_not_raise(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    document_id = _insert_document(conn, "a", content="editor's pick of the week")

    hits = search(conn, _settings(tmp_path), _FakeEmbedder(), "editor's pick")

    assert [hit.document_id for hit in hits] == [document_id]


def test_unbalanced_quotes_in_query_does_not_raise(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    document_id = _insert_document(conn, "a", content="tiktok slideshow examples")

    hits = search(conn, _settings(tmp_path), _FakeEmbedder(), 'tiktok "slideshow')

    assert [hit.document_id for hit in hits] == [document_id]


def test_empty_query_returns_no_hits(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    document_id = _insert_document(conn, "a", content="anything at all")
    _insert_chunk(conn, document_id, "anything at all", _vec(1.0, 0.0))

    assert search(conn, _settings(tmp_path), _FakeEmbedder(), "") == []
    assert search(conn, _settings(tmp_path), _FakeEmbedder(), "   ") == []


def test_limit_is_honored(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    for i in range(5):
        _insert_document(conn, f"doc-{i}", content="common shared term")

    hits = search(conn, _settings(tmp_path), _FakeEmbedder(), "common", limit=2)

    assert len(hits) == 2


def test_document_with_chunks_but_no_fts_content_still_ranks_by_vector(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    # content must be non-null (index_documents' own selector never chunks a NULL-content
    # document), but it shares no term with the query, so nothing here matches on the FTS side.
    document_id = _insert_document(conn, "a", title=None, content="completely unrelated prose")
    query_vector = _vec(1.0, 0.0)
    _insert_chunk(conn, document_id, "chunk text never indexed by fts", query_vector)
    embedder = _FakeEmbedder({"orphan": query_vector})

    hits = search(conn, _settings(tmp_path), embedder, "orphan")

    assert len(hits) == 1
    assert hits[0].document_id == document_id
    assert hits[0].fts_rank is None
    assert hits[0].vector_rank == 1
