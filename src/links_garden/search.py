"""Hybrid search: FTS5 lexical matching fused with vector similarity via reciprocal rank fusion.

`bge-m3` is what makes cross-language search work here: measured on the requirements interview's
own example queries, `optimisation du coût des tokens` scores 0.800 against `claude code cost
token optimization`, and `diaporama tiktok` scores 0.773 against `tiktok slideshow`, against 0.445
for an unrelated pair. FTS5 covers what embeddings fumble instead — a name, a package like
`sqlite-vec`, a URL fragment. RRF fuses the two rankings without a tuned weight between them.
"""

import re
import sqlite3
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from links_garden.config import Settings
from links_garden.embed import SELECTOR_SQL, EmbedderLike, unpack_vector

_RRF_K = 60
_SNIPPET_CHARS = 300
# \w+ both tokenizes the query and sanitizes it: punctuation FTS5 would otherwise choke on (a
# bare quote, an unbalanced one) is simply not a token, and a keyword like AND/OR/NEAR becomes a
# quoted string literal below rather than parsed as FTS5 syntax. The query can never fail to
# parse, which matters once it comes from an MCP tool an agent drives rather than a human who can
# retype a typo.
_TERM_RE = re.compile(r"\w+")


@dataclass(frozen=True)
class Hit:
    document_id: int
    title: str | None
    url: str | None
    source: str
    snippet: str
    score: float
    fts_rank: int | None
    vector_rank: int | None


@dataclass
class _RankedDoc:
    """One side's view of a matched document: its 1-based rank on that side, plus enough of the
    document row to build a `Hit` without a second round trip.
    """

    rank: int
    title: str | None
    url: str | None
    source: str
    snippet: str


def search(
    conn: sqlite3.Connection,
    settings: Settings,
    embedder: EmbedderLike,
    query: str,
    *,
    limit: int = 20,
) -> list[Hit]:
    """Fuse FTS5 and vector rankings for `query`, returning up to `limit` hits, best first."""
    terms = _TERM_RE.findall(query)
    if not terms:
        return []
    fts_hits = _fts_search(conn, terms)
    vector_hits = _vector_search(conn, embedder, query)
    return _fuse(fts_hits, vector_hits, limit)


def _fts_search(conn: sqlite3.Connection, terms: list[str]) -> dict[int, _RankedDoc]:
    """Rank documents by FTS5 relevance, each term quoted as a string literal (see `_TERM_RE`).

    Bare quoted tokens default to AND in FTS5, so a well-formed multi-term query is precise:
    a document sharing only one of three terms does not enter fusion and outrank one sharing
    all three. OR is a fallback for when AND finds nothing, trading precision for recall only
    when the precise query would otherwise return an empty FTS side.
    """
    quoted = [f'"{term}"' for term in terms]
    rows = _fts_query(conn, " ".join(quoted))
    if not rows:
        rows = _fts_query(conn, " OR ".join(quoted))
    return {
        row["document_id"]: _RankedDoc(
            rank=rank,
            title=row["title"],
            url=row["url"],
            source=row["source"],
            snippet=row["snippet"],
        )
        for rank, row in enumerate(rows, start=1)
    }


def _fts_query(conn: sqlite3.Connection, match_query: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT d.id AS document_id, d.title, d.url, d.source, "
        "snippet(documents_fts, -1, '', '', '…', 12) AS snippet "
        "FROM documents_fts JOIN documents d ON d.id = documents_fts.rowid "
        f"WHERE documents_fts MATCH ? AND {SELECTOR_SQL} "
        "ORDER BY documents_fts.rank",
        (match_query,),
    ).fetchall()


def _vector_search(
    conn: sqlite3.Connection, embedder: EmbedderLike, query: str
) -> dict[int, _RankedDoc]:
    """Rank documents by their best-matching chunk's cosine similarity to the embedded query.

    `index_documents`'s `SELECTOR_SQL` gates writes, not the `chunks` rows already on disk:
    tombstoning a document or flipping its status to 'failed' leaves its chunks in place with
    nothing to sweep them, so this join re-applies the same selector on read rather than
    trusting `chunks` to be clean.

    ponytail: loads every chunk vector for every query — correct at 162 chunks today and a few
    thousand more; an ANN index (e.g. sqlite-vec) is the upgrade once the corpus outgrows that.
    """
    rows = conn.execute(
        "SELECT c.document_id, c.text, c.embedding, d.title, d.url, d.source "
        "FROM chunks c JOIN documents d ON d.id = c.document_id "
        f"WHERE {SELECTOR_SQL} AND c.embedding IS NOT NULL"
    ).fetchall()
    if not rows:
        return {}
    query_vector = embedder.embed([query])[0]
    matrix: NDArray[np.float32] = np.stack([unpack_vector(row["embedding"]) for row in rows])
    similarities = _cosine_similarities(matrix, query_vector)
    ranked: dict[int, _RankedDoc] = {}
    for idx in np.argsort(-similarities):
        row = rows[int(idx)]
        document_id: int = row["document_id"]
        if document_id in ranked:
            continue  # a later, lower-similarity chunk of a document already ranked
        ranked[document_id] = _RankedDoc(
            rank=len(ranked) + 1,
            title=row["title"],
            url=row["url"],
            source=row["source"],
            snippet=_chunk_snippet(row["text"]),
        )
    return ranked


def _cosine_similarities(
    matrix: NDArray[np.float32], vector: NDArray[np.float32]
) -> NDArray[np.float32]:
    return (matrix @ vector) / (np.linalg.norm(matrix, axis=1) * np.linalg.norm(vector))


def find_related(conn: sqlite3.Connection, document_id: int, *, limit: int = 10) -> list[Hit]:
    """Nearest other documents by embedding: the "what else did I save about this" view.

    No query embedding call: the anchor document's own already-stored chunk vectors, averaged
    into a centroid, stand in for a query vector. Cheap enough for a graph view to call per
    click, and it means `api.py`'s `/documents/{id}/related` never touches ollama either.
    """
    own_vectors = [
        unpack_vector(row["embedding"])
        for row in conn.execute(
            "SELECT embedding FROM chunks WHERE document_id = ? AND embedding IS NOT NULL",
            (document_id,),
        ).fetchall()
    ]
    if not own_vectors:
        return []
    centroid: NDArray[np.float32] = np.mean(np.stack(own_vectors), axis=0).astype(np.float32)
    rows = conn.execute(
        "SELECT c.document_id, c.text, c.embedding, d.title, d.url, d.source "
        "FROM chunks c JOIN documents d ON d.id = c.document_id "
        f"WHERE {SELECTOR_SQL} AND c.embedding IS NOT NULL AND c.document_id != ?",
        (document_id,),
    ).fetchall()
    if not rows:
        return []
    matrix: NDArray[np.float32] = np.stack([unpack_vector(row["embedding"]) for row in rows])
    similarities = _cosine_similarities(matrix, centroid)
    best: dict[int, tuple[float, sqlite3.Row]] = {}
    for idx in np.argsort(-similarities):
        row = rows[int(idx)]
        doc_id: int = row["document_id"]
        if doc_id not in best:
            best[doc_id] = (float(similarities[int(idx)]), row)
    ranked = sorted(best.values(), key=lambda item: -item[0])[:limit]
    return [
        Hit(
            document_id=row["document_id"],
            title=row["title"],
            url=row["url"],
            source=row["source"],
            snippet=_chunk_snippet(row["text"]),
            score=score,
            fts_rank=None,
            vector_rank=rank,
        )
        for rank, (score, row) in enumerate(ranked, start=1)
    ]


def _chunk_snippet(text: str) -> str:
    text = text.strip()
    if len(text) <= _SNIPPET_CHARS:
        return text
    return text[:_SNIPPET_CHARS].rsplit(" ", 1)[0] + "…"


def _fuse(
    fts_hits: dict[int, _RankedDoc], vector_hits: dict[int, _RankedDoc], limit: int
) -> list[Hit]:
    # Sort key is (-score, document_id): highest score first, ties broken by document_id
    # ascending. Two documents can tie exactly (e.g. one FTS-rank-1-only, one
    # vector-rank-1-only both score 1/61), and Python's sort is otherwise only as stable as
    # set-iteration order over `fts_hits.keys() | vector_hits.keys()` — not a documented
    # guarantee.
    scored = sorted(
        (
            (_rrf_score(fts_hits.get(doc_id), vector_hits.get(doc_id)), doc_id)
            for doc_id in fts_hits.keys() | vector_hits.keys()
        ),
        key=lambda item: (-item[0], item[1]),
    )
    return [
        _build_hit(doc_id, score, fts_hits.get(doc_id), vector_hits.get(doc_id))
        for score, doc_id in scored[:limit]
    ]


def _build_hit(
    document_id: int, score: float, fts: _RankedDoc | None, vector: _RankedDoc | None
) -> Hit:
    # A chunk's text is a more specific snippet than FTS5's summary/keywords excerpt, so the
    # vector side's snippet wins whenever a document matched on both sides.
    meta = vector or fts
    assert meta is not None  # document_id came from the union of both dicts' keys
    return Hit(
        document_id=document_id,
        title=meta.title,
        url=meta.url,
        source=meta.source,
        snippet=meta.snippet,
        score=score,
        fts_rank=fts.rank if fts else None,
        vector_rank=vector.rank if vector else None,
    )


def _rrf_score(fts: _RankedDoc | None, vector: _RankedDoc | None) -> float:
    score = 0.0
    if fts is not None:
        score += 1 / (_RRF_K + fts.rank)
    if vector is not None:
        score += 1 / (_RRF_K + vector.rank)
    return score
