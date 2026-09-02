"""Per-set schema extraction, filling `set_memberships` rows and the `garden extract` write path.

`enrich.py` decides *which* sets a document belongs to; this decides *what* each matched set's
own fields are. Sits beside enrich.py rather than in it because it walks a different table
(`set_memberships`, not `documents`) with a different write path, even though it reuses
`Enricher.extract` for the actual ollama call.

A missing required field is a data problem for the review queue described in DESIGN.md, not a
pipeline failure: only a model or network error marks a row `failed`.
"""

import json
import logging
import sqlite3
from dataclasses import dataclass
from typing import Protocol

from links_garden.chunk import build_document_text
from links_garden.embed import SELECTOR_SQL
from links_garden.sets import compute_missing_fields, get_set

logger = logging.getLogger(__name__)


class ExtractorLike(Protocol):
    """The one method `extract_pending` calls. Lets tests fake extraction without a live client."""

    def extract(self, text: str, schema: dict[str, object]) -> dict[str, object]: ...


@dataclass
class ExtractReport:
    """Counts of what one `extract_pending` call actually did."""

    memberships_seen: int = 0
    memberships_ok: int = 0
    memberships_partial: int = 0
    memberships_failed: int = 0


def extract_for_membership(
    conn: sqlite3.Connection, enricher: ExtractorLike, document_id: int, set_name: str
) -> None:
    """Extract one set's fields for one document and write the result to its membership row.

    Public wrapper around `_extract_one` for direct, single-row use (e.g. retrying a failed
    row by hand); `extract_pending` calls `_extract_one` itself to get the status back for its
    report without a second query.
    """
    _extract_one(conn, enricher, document_id, set_name)


def extract_pending(
    conn: sqlite3.Connection, enricher: ExtractorLike, *, set_name: str | None = None
) -> ExtractReport:
    """Fill every `set_memberships` row still `pending` or `failed`, optionally limited to one set.

    `failed` is retried alongside `pending`: unlike `enrich`/`index`, extraction has no hash to
    self-heal a transient ollama error on the next run, so `pending` alone would strand a row
    that failed once with no way back except by hand. `SELECTOR_SQL` keeps a soft-deleted or
    otherwise invisible document from burning a model call it can never surface anywhere.

    Per-row transactions, via `_extract_one`: an interrupted run resumes at the next open row
    instead of redoing ones already written, and one bad membership can't stop the rest.
    """
    report = ExtractReport()
    query = (
        "SELECT sm.document_id, s.name FROM set_memberships sm "
        "JOIN sets s ON s.id = sm.set_id "
        "JOIN documents d ON d.id = sm.document_id "
        f"WHERE sm.status IN ('pending', 'failed') AND {SELECTOR_SQL}"
    )
    params: tuple[str, ...] = ()
    if set_name is not None:
        query += " AND s.name = ?"
        params = (set_name,)
    rows = conn.execute(query, params).fetchall()
    for row in rows:
        report.memberships_seen += 1
        status = _extract_one(conn, enricher, row["document_id"], row["name"])
        if status == "ok":
            report.memberships_ok += 1
        elif status == "partial":
            report.memberships_partial += 1
        else:
            report.memberships_failed += 1
    return report


def _extract_one(
    conn: sqlite3.Connection, enricher: ExtractorLike, document_id: int, set_name: str
) -> str:
    set_ = get_set(conn, set_name)
    assert set_ is not None
    doc_row = conn.execute(
        "SELECT message_text, content FROM documents WHERE id = ?", (document_id,)
    ).fetchone()
    text = build_document_text(doc_row["message_text"], doc_row["content"])
    try:
        extracted = enricher.extract(text, set_.schema)
    except Exception as exc:
        logger.exception("failed to extract set %r for document %d", set_name, document_id)
        conn.execute(
            "UPDATE set_memberships SET status = 'failed', error = ?, "
            "extracted_at = datetime('now') WHERE document_id = ? AND set_id = ?",
            (str(exc), document_id, set_.id),
        )
        conn.commit()
        return "failed"
    properties = set_.schema["properties"]
    assert isinstance(properties, dict)
    # The model invents keys outside the schema (Task 2 saw the same behavior with set names);
    # discarded rather than stored, so extracted_json only ever holds what was actually asked for.
    cleaned = {key: value for key, value in extracted.items() if key in properties}
    missing = compute_missing_fields(set_.schema, cleaned)
    status = "partial" if missing else "ok"
    conn.execute(
        "UPDATE set_memberships SET extracted_json = ?, missing_fields = ?, status = ?, "
        "error = NULL, extracted_at = datetime('now') WHERE document_id = ? AND set_id = ?",
        (json.dumps(cleaned), json.dumps(missing), status, document_id, set_.id),
    )
    conn.commit()
    return status
