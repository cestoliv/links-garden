import sqlite3
from pathlib import Path

import pytest

from links_garden.db import connect, initialize, is_tombstoned, purge, tombstone


def _open(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "garden.db")
    initialize(conn)
    return conn


def _insert_document(conn: sqlite3.Connection, source_ref: str = "ref-1") -> int:
    cursor = conn.execute(
        "INSERT INTO documents (source, source_ref, title, content) VALUES (?, ?, ?, ?)",
        ("signal", source_ref, "a title", "some content"),
    )
    conn.commit()
    assert cursor.lastrowid is not None
    return cursor.lastrowid


def test_initialize_is_idempotent(tmp_path: Path) -> None:
    conn = _open(tmp_path)

    initialize(conn)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1


def test_initialize_is_idempotent_across_a_reopened_connection(tmp_path: Path) -> None:
    db_path = tmp_path / "garden.db"
    conn = connect(db_path)
    initialize(conn)
    conn.close()

    conn = connect(db_path)
    initialize(conn)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1


def test_initialize_does_not_clobber_a_later_migration_version(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    conn.execute("PRAGMA user_version=2")
    conn.commit()

    initialize(conn)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2


def test_connect_creates_missing_parent_directory(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "dir" / "garden.db"

    connect(db_path)

    assert db_path.parent.is_dir()


def test_foreign_keys_is_on_after_connect(tmp_path: Path) -> None:
    conn = connect(tmp_path / "garden.db")

    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_deleting_document_cascades_to_chunks_and_set_memberships(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    document_id = _insert_document(conn)
    conn.execute(
        "INSERT INTO chunks (document_id, ordinal, text, token_count) VALUES (?, 0, 't', 1)",
        (document_id,),
    )
    conn.execute("INSERT INTO sets (name, description, schema_json) VALUES ('s', 'd', '{}')")
    conn.execute("INSERT INTO set_memberships (document_id, set_id) VALUES (?, 1)", (document_id,))
    conn.commit()

    conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
    conn.commit()

    assert conn.execute("SELECT * FROM chunks").fetchall() == []
    assert conn.execute("SELECT * FROM set_memberships").fetchall() == []


def test_deleting_set_cascades_to_set_memberships(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    document_id = _insert_document(conn)
    conn.execute("INSERT INTO sets (name, description, schema_json) VALUES ('s', 'd', '{}')")
    conn.execute("INSERT INTO set_memberships (document_id, set_id) VALUES (?, 1)", (document_id,))
    conn.commit()

    conn.execute("DELETE FROM sets WHERE id = 1")
    conn.commit()

    assert conn.execute("SELECT * FROM set_memberships").fetchall() == []


def test_unique_source_and_source_ref_rejects_duplicate(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    _insert_document(conn, "ref-1")

    with pytest.raises(sqlite3.IntegrityError):
        _insert_document(conn, "ref-1")


def test_status_check_constraint_rejects_unknown_value(tmp_path: Path) -> None:
    conn = _open(tmp_path)

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO documents (source, source_ref, status) VALUES ('signal', 'r', 'bogus')"
        )


def test_source_check_constraint_rejects_unknown_value(tmp_path: Path) -> None:
    conn = _open(tmp_path)

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO documents (source, source_ref) VALUES ('carrier-pigeon', 'r')")


def test_inserting_document_is_findable_through_fts(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    _insert_document(conn)

    rows = conn.execute(
        "SELECT rowid FROM documents_fts WHERE documents_fts MATCH 'content'"
    ).fetchall()

    assert len(rows) == 1


def test_updating_content_updates_fts_index(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    document_id = _insert_document(conn)

    conn.execute("UPDATE documents SET content = 'brand new text' WHERE id = ?", (document_id,))
    conn.commit()

    old_matches = conn.execute(
        "SELECT rowid FROM documents_fts WHERE documents_fts MATCH 'some'"
    ).fetchall()
    new_matches = conn.execute(
        "SELECT rowid FROM documents_fts WHERE documents_fts MATCH 'brand'"
    ).fetchall()

    assert old_matches == []
    assert len(new_matches) == 1


def test_deleting_document_removes_it_from_fts(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    document_id = _insert_document(conn)

    conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
    conn.commit()

    rows = conn.execute(
        "SELECT rowid FROM documents_fts WHERE documents_fts MATCH 'content'"
    ).fetchall()

    assert rows == []


def test_fts_diacritic_folding_matches_unaccented_query(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    conn.execute(
        "INSERT INTO documents (source, source_ref, content) VALUES ('signal', 'r', ?)",
        ("référencement",),
    )
    conn.commit()

    rows = conn.execute(
        "SELECT rowid FROM documents_fts WHERE documents_fts MATCH 'referencement'"
    ).fetchall()

    assert len(rows) == 1


def test_fts_diacritic_folding_level_2_folds_beyond_french_accents(tmp_path: Path) -> None:
    # remove_diacritics 1 only folds French-style accents; level 2 also folds marks like
    # the umlaut below, which is what the mandated setting is actually pinning.
    conn = _open(tmp_path)
    conn.execute(
        "INSERT INTO documents (source, source_ref, content) VALUES ('signal', 'r', ?)",
        ("ǖber",),
    )
    conn.commit()

    rows = conn.execute(
        "SELECT rowid FROM documents_fts WHERE documents_fts MATCH 'uber'"
    ).fetchall()

    assert len(rows) == 1


def test_tombstone_sets_deleted_at_and_keeps_row(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    document_id = _insert_document(conn)

    tombstone(conn, document_id)

    row = conn.execute("SELECT deleted_at FROM documents WHERE id = ?", (document_id,)).fetchone()
    assert row is not None
    assert row["deleted_at"] is not None


def test_tombstone_updates_updated_at(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    cursor = conn.execute(
        "INSERT INTO documents (source, source_ref, created_at, updated_at) "
        "VALUES ('signal', 'ref-1', '2020-01-01 00:00:00', '2020-01-01 00:00:00')"
    )
    conn.commit()
    document_id = cursor.lastrowid
    assert document_id is not None

    tombstone(conn, document_id)

    row = conn.execute(
        "SELECT created_at, updated_at FROM documents WHERE id = ?", (document_id,)
    ).fetchone()
    assert row["updated_at"] != "2020-01-01 00:00:00"
    assert row["updated_at"] >= row["created_at"]


def test_purge_removes_the_row(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    document_id = _insert_document(conn)

    purge(conn, document_id)

    row = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
    assert row is None


def test_purge_leaves_a_tombstoned_row_in_place(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    document_id = _insert_document(conn)
    tombstone(conn, document_id)

    purge(conn, document_id)

    row = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
    assert row is not None
    assert is_tombstoned(conn, "signal", "ref-1") is True


def test_is_tombstoned_reflects_tombstone_and_purge(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    document_id = _insert_document(conn, "ref-1")
    conn.execute("INSERT INTO documents (source, source_ref) VALUES ('signal', 'ref-2')")
    conn.commit()

    assert is_tombstoned(conn, "signal", "ref-2") is False

    tombstone(conn, document_id)
    assert is_tombstoned(conn, "signal", "ref-1") is True

    # purge no longer clears a tombstone: a dashboard delete outranks the file going away.
    purge(conn, document_id)
    assert is_tombstoned(conn, "signal", "ref-1") is True


def test_is_tombstoned_does_not_leak_across_sources(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    conn.execute("INSERT INTO documents (source, source_ref) VALUES ('obsidian', 'ref-1')")
    conn.commit()
    obsidian_id = conn.execute(
        "SELECT id FROM documents WHERE source = 'obsidian' AND source_ref = 'ref-1'"
    ).fetchone()["id"]

    tombstone(conn, obsidian_id)

    assert is_tombstoned(conn, "obsidian", "ref-1") is True
    assert is_tombstoned(conn, "signal", "ref-1") is False
