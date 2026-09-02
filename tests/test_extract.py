"""Tests for per-set schema extraction: `extract_for_membership` and `extract_pending`."""

import json
import sqlite3
from pathlib import Path
from typing import cast

from links_garden.db import connect, initialize
from links_garden.extract_sets import ExtractReport, extract_for_membership, extract_pending
from links_garden.sets import create_set

_TIKTOK_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "username": {"type": "string"},
        "mail": {"type": "string"},
        "follower_count": {"type": "integer"},
        "like_count": {"type": "integer"},
        "niche": {"type": "string"},
    },
    "required": ["username", "niche"],
}

_FULL_RESULT: dict[str, object] = {"username": "chef_ana", "niche": "cooking"}


def _open(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "garden.db")
    initialize(conn)
    return conn


def _insert_document(
    conn: sqlite3.Connection, source_ref: str = "ref-1", *, content: str = "a tiktok profile"
) -> int:
    cursor = conn.execute(
        "INSERT INTO documents (source, source_ref, content, status) VALUES ('manual', ?, ?, 'ok')",
        (source_ref, content),
    )
    conn.commit()
    assert cursor.lastrowid is not None
    return cursor.lastrowid


def _add_membership(conn: sqlite3.Connection, document_id: int, set_id: int) -> None:
    conn.execute(
        "INSERT INTO set_memberships (document_id, set_id) VALUES (?, ?)", (document_id, set_id)
    )
    conn.commit()


def _membership_row(conn: sqlite3.Connection, document_id: int, set_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM set_memberships WHERE document_id = ? AND set_id = ?",
        (document_id, set_id),
    ).fetchone()
    assert row is not None
    return cast(sqlite3.Row, row)


class _FakeExtractor:
    """Deterministic extraction, never touches the network.

    `fail_on`/`missing_field_on` match a substring of the document text, mirroring
    `_FakeEnricher.fail_on` in test_enrich.py, so one instance can drive a whole
    `extract_pending` run with a mix of outcomes.
    """

    def __init__(
        self,
        result: dict[str, object] | None = None,
        *,
        fail_on: str | None = None,
        missing_field_on: str | None = None,
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self._result = dict(result) if result is not None else dict(_FULL_RESULT)
        self._fail_on = fail_on
        self._missing_field_on = missing_field_on

    def extract(self, text: str, schema: dict[str, object]) -> dict[str, object]:
        self.calls.append(schema)
        if self._fail_on is not None and self._fail_on in text:
            raise RuntimeError("extraction boom")
        if self._missing_field_on is not None and self._missing_field_on in text:
            return {key: value for key, value in self._result.items() if key != "niche"}
        return dict(self._result)


def test_pending_membership_is_filled_and_marked_ok(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    created = create_set(conn, "tiktok_influenceur", "a tiktok profile", _TIKTOK_SCHEMA)
    assert created.id is not None
    document_id = _insert_document(conn)
    _add_membership(conn, document_id, created.id)

    extract_for_membership(conn, _FakeExtractor(), document_id, "tiktok_influenceur")

    row = _membership_row(conn, document_id, created.id)
    assert row["status"] == "ok"
    assert json.loads(row["extracted_json"]) == _FULL_RESULT
    assert json.loads(row["missing_fields"]) == []
    assert row["extracted_at"] is not None


def test_missing_required_field_is_marked_partial_and_named(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    created = create_set(conn, "tiktok_influenceur", "a tiktok profile", _TIKTOK_SCHEMA)
    assert created.id is not None
    document_id = _insert_document(conn)
    _add_membership(conn, document_id, created.id)

    extract_for_membership(
        conn, _FakeExtractor(missing_field_on="tiktok"), document_id, "tiktok_influenceur"
    )

    row = _membership_row(conn, document_id, created.id)
    assert row["status"] == "partial"
    assert json.loads(row["missing_fields"]) == ["niche"]


def test_invented_key_is_discarded(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    created = create_set(conn, "tiktok_influenceur", "a tiktok profile", _TIKTOK_SCHEMA)
    assert created.id is not None
    document_id = _insert_document(conn)
    _add_membership(conn, document_id, created.id)
    extractor = _FakeExtractor({**_FULL_RESULT, "made_up_field": "nonsense"})

    extract_for_membership(conn, extractor, document_id, "tiktok_influenceur")

    row = _membership_row(conn, document_id, created.id)
    assert "made_up_field" not in json.loads(row["extracted_json"])
    assert row["status"] == "ok"


def test_model_error_marks_the_row_failed_without_stopping_the_run(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    created = create_set(conn, "recipe", "a cooking recipe", _TIKTOK_SCHEMA)
    assert created.id is not None
    ok_id = _insert_document(conn, "ref-ok", content="fine content")
    boom_id = _insert_document(conn, "ref-boom", content="boom content")
    for document_id in (ok_id, boom_id):
        _add_membership(conn, document_id, created.id)
    extractor = _FakeExtractor(fail_on="boom")

    extract_for_membership(conn, extractor, boom_id, "recipe")

    boom_row = _membership_row(conn, boom_id, created.id)
    assert boom_row["status"] == "failed"
    assert boom_row["error"] == "extraction boom"
    ok_row = _membership_row(conn, ok_id, created.id)
    assert ok_row["status"] == "pending"  # untouched by the other row's failure


def test_extract_pending_filters_to_one_set(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    recipe = create_set(conn, "recipe", "a cooking recipe", _TIKTOK_SCHEMA)
    tiktok = create_set(conn, "tiktok_influenceur", "a tiktok profile", _TIKTOK_SCHEMA)
    assert recipe.id is not None and tiktok.id is not None
    document_id = _insert_document(conn)
    _add_membership(conn, document_id, recipe.id)
    _add_membership(conn, document_id, tiktok.id)

    report = extract_pending(conn, _FakeExtractor(), set_name="tiktok_influenceur")

    assert report.memberships_seen == 1
    assert _membership_row(conn, document_id, tiktok.id)["status"] == "ok"
    assert _membership_row(conn, document_id, recipe.id)["status"] == "pending"


def test_rerunning_does_not_reextract_an_ok_row(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    created = create_set(conn, "tiktok_influenceur", "a tiktok profile", _TIKTOK_SCHEMA)
    assert created.id is not None
    document_id = _insert_document(conn)
    _add_membership(conn, document_id, created.id)
    extract_pending(conn, _FakeExtractor())

    second = _FakeExtractor({"username": "someone_else", "niche": "dance"})
    report = extract_pending(conn, second)

    assert report.memberships_seen == 0
    assert second.calls == []
    row = _membership_row(conn, document_id, created.id)
    assert json.loads(row["extracted_json"]) == _FULL_RESULT


def test_the_sets_own_schema_is_sent_as_format(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    created = create_set(conn, "tiktok_influenceur", "a tiktok profile", _TIKTOK_SCHEMA)
    assert created.id is not None
    document_id = _insert_document(conn)
    _add_membership(conn, document_id, created.id)
    extractor = _FakeExtractor()

    extract_for_membership(conn, extractor, document_id, "tiktok_influenceur")

    assert extractor.calls == [_TIKTOK_SCHEMA]


def test_extract_report_counts_match_the_operations_performed(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    created = create_set(conn, "tiktok_influenceur", "a tiktok profile", _TIKTOK_SCHEMA)
    assert created.id is not None
    ok_id = _insert_document(conn, "ref-ok", content="ok content")
    partial_id = _insert_document(conn, "ref-partial", content="missing-niche content")
    failed_id = _insert_document(conn, "ref-failed", content="boom content")
    already_done_id = _insert_document(conn, "ref-done", content="done content")
    for document_id in (ok_id, partial_id, failed_id, already_done_id):
        _add_membership(conn, document_id, created.id)
    conn.execute(
        "UPDATE set_memberships SET status = 'ok' WHERE document_id = ?", (already_done_id,)
    )
    conn.commit()
    extractor = _FakeExtractor(fail_on="boom", missing_field_on="missing-niche")

    report = extract_pending(conn, extractor)

    assert isinstance(report, ExtractReport)
    assert report.memberships_seen == 3  # excludes ref-done, already ok
    assert report.memberships_ok == 1
    assert report.memberships_partial == 1
    assert report.memberships_failed == 1


def test_failed_row_is_retried_on_the_next_run(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    created = create_set(conn, "tiktok_influenceur", "a tiktok profile", _TIKTOK_SCHEMA)
    assert created.id is not None
    document_id = _insert_document(conn)
    _add_membership(conn, document_id, created.id)
    extract_pending(conn, _FakeExtractor(fail_on="tiktok"))  # first pass fails

    report = extract_pending(conn, _FakeExtractor())  # clean retry, no --set filter needed

    assert report.memberships_seen == 1
    row = _membership_row(conn, document_id, created.id)
    assert row["status"] == "ok"
    assert row["error"] is None  # reset on the row that previously failed


def test_explicit_null_required_field_is_marked_missing(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    created = create_set(conn, "tiktok_influenceur", "a tiktok profile", _TIKTOK_SCHEMA)
    assert created.id is not None
    document_id = _insert_document(conn)
    _add_membership(conn, document_id, created.id)
    extractor = _FakeExtractor({"username": "ana", "niche": None})

    extract_for_membership(conn, extractor, document_id, "tiktok_influenceur")

    row = _membership_row(conn, document_id, created.id)
    assert row["status"] == "partial"
    assert json.loads(row["missing_fields"]) == ["niche"]


def test_soft_deleted_document_is_skipped(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    created = create_set(conn, "tiktok_influenceur", "a tiktok profile", _TIKTOK_SCHEMA)
    assert created.id is not None
    document_id = _insert_document(conn)
    _add_membership(conn, document_id, created.id)
    conn.execute("UPDATE documents SET deleted_at = datetime('now') WHERE id = ?", (document_id,))
    conn.commit()
    extractor = _FakeExtractor()

    report = extract_pending(conn, extractor)

    assert report.memberships_seen == 0
    assert extractor.calls == []
