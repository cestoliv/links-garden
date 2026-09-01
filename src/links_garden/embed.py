"""Embeddings via ollama, vector BLOB packing, and the `garden index` write path.

Unlike `fetch.py`, which caches a failure and moves on because there is a monthly budget to
protect, there is no budget here: an unreachable ollama must raise, not silently produce a
half-embedded corpus that returns wrong search results forever.
"""

import hashlib
import logging
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
import numpy as np
from numpy.typing import NDArray

from links_garden.chunk import build_document_text, chunk_text
from links_garden.config import Settings

logger = logging.getLogger(__name__)

_TIMEOUT = 60.0
# Measured against ollama: 16 inputs in one call take about as long as 16 separate calls, minus
# the per-call overhead each of those would otherwise pay.
_BATCH_SIZE = 16

# Mandatory per step 3's whole-branch review: `content` is NULL for YouTube children and every
# pending placeholder, and a tombstoned document must never be indexed. search.py's readers
# import this rather than keeping their own copy, since a selector that drifts between a writer
# and its readers is worse than one that is merely awkward to share.
SELECTOR_SQL = "d.content IS NOT NULL AND d.status = 'ok' AND d.deleted_at IS NULL"


class Embedder:
    """ollama-backed embedding client, injectable for tests."""

    def __init__(self, settings: Settings, *, client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._client = client if client is not None else httpx.Client()

    def embed(self, texts: Sequence[str]) -> list[NDArray[np.float32]]:
        """Embed `texts` in batches of `_BATCH_SIZE`, preserving input order."""
        vectors: list[NDArray[np.float32]] = []
        for start in range(0, len(texts), _BATCH_SIZE):
            vectors.extend(self._embed_batch(texts[start : start + _BATCH_SIZE]))
        return vectors

    def _embed_batch(self, batch: Sequence[str]) -> list[NDArray[np.float32]]:
        url = f"{self._settings.ollama_url}/api/embed"
        try:
            response = self._client.post(
                url,
                json={"model": self._settings.embedding_model, "input": list(batch)},
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
            payload: Any = response.json()
            embeddings = payload["embeddings"]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"could not get embeddings from ollama at {url} "
                f"for model {self._settings.embedding_model!r}: {exc}"
            ) from exc
        return [np.array(vector, dtype=np.float32) for vector in embeddings]

    def check(self) -> bool:
        """Whether `settings.embedding_model` is pulled, so a run fails fast instead of raising
        mid-batch. Matches names loosely by ignoring any `:tag` suffix, since ollama lists
        pulled models as e.g. `bge-m3:latest`.
        """
        url = f"{self._settings.ollama_url}/api/tags"
        try:
            response = self._client.get(url, timeout=_TIMEOUT)
            response.raise_for_status()
            payload: Any = response.json()
            names = {model["name"].split(":")[0] for model in payload["models"]}
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return False
        return self._settings.embedding_model.split(":")[0] in names


class EmbedderLike(Protocol):
    """The one method `index_documents` calls. Lets tests fake embedding without a live client."""

    def embed(self, texts: Sequence[str]) -> list[NDArray[np.float32]]: ...


def pack_vector(vector: NDArray[np.float32]) -> bytes:
    return vector.astype(np.float32).tobytes()


def unpack_vector(blob: bytes) -> NDArray[np.float32]:
    return np.frombuffer(blob, dtype=np.float32)


@dataclass
class IndexReport:
    """Counts of what one `index_documents` call actually did."""

    documents_seen: int = 0
    documents_indexed: int = 0
    documents_skipped: int = 0
    documents_failed: int = 0
    chunks_written: int = 0


def index_documents(
    conn: sqlite3.Connection, settings: Settings, embedder: EmbedderLike
) -> IndexReport:
    """Chunk and embed every qualifying document, skipping one whose chunks are already current.

    Per-document transactions: one document's failure must not roll back chunks already
    committed for documents processed earlier in this run.
    """
    report = IndexReport()
    rows = conn.execute(
        f"SELECT d.id, d.message_text, d.content, d.chunks_hash FROM documents d "
        f"WHERE {SELECTOR_SQL}"
    ).fetchall()
    for row in rows:
        report.documents_seen += 1
        text = build_document_text(row["message_text"], row["content"])
        digest = _digest(text, settings.embedding_model)
        if row["chunks_hash"] == digest:
            report.documents_skipped += 1
            continue
        try:
            report.chunks_written += _index_document(conn, row["id"], text, digest, embedder)
        except Exception:
            logger.exception("failed to index document %d", row["id"])
            conn.rollback()
            report.documents_failed += 1
            continue
        report.documents_indexed += 1
    return report


def _index_document(
    conn: sqlite3.Connection, document_id: int, text: str, digest: str, embedder: EmbedderLike
) -> int:
    """Replace one document's chunks. Chunking and embedding happen before any write, so a
    failure here leaves the previous chunks in place rather than deleting them for nothing.
    """
    chunks = chunk_text(text)
    vectors = embedder.embed(chunks)
    conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
    conn.executemany(
        "INSERT INTO chunks (document_id, ordinal, text, token_count, embedding) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (document_id, ordinal, chunk, _token_estimate(chunk), pack_vector(vector))
            for ordinal, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True))
        ],
    )
    conn.execute("UPDATE documents SET chunks_hash = ? WHERE id = ?", (digest, document_id))
    conn.commit()
    return len(chunks)


def _digest(text: str, model: str) -> str:
    """A hash of the exact text and model chunks were built from, recorded as `chunks_hash`.

    Not `documents.content_hash`: that column is only ever set for `obsidian`-sourced rows, so
    reusing it here would leave every `signal`/`manual`/`mcp` document's `chunks_hash` at NULL
    forever, matching a brand-new document's own default NULL and skipping it on its very first
    run. Hashing the assembled text sidesteps that and also tracks `message_text` changes, which
    `content_hash` never covers.

    The model name is folded in so changing `EMBEDDING_MODEL` re-embeds every document instead
    of leaving old, differently-dimensioned vectors in place: mixed dimensions crash `search`'s
    `np.stack`, and even a uniform old dimension crashes the query-vector matmul.
    """
    return hashlib.sha256(f"{model}\x00{text}".encode()).hexdigest()


def _token_estimate(chunk: str) -> int:
    # chunk.py's own ratio: ~4000 characters approximates 1000 tokens for this corpus.
    return max(1, len(chunk) // 4)
