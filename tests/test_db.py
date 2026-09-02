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

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3


def test_initialize_is_idempotent_across_a_reopened_connection(tmp_path: Path) -> None:
    db_path = tmp_path / "garden.db"
    conn = connect(db_path)
    initialize(conn)
    conn.close()

    conn = connect(db_path)
    initialize(conn)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3


def test_initialize_does_not_clobber_a_later_migration_version(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    conn.execute("PRAGMA user_version=4")
    conn.commit()

    initialize(conn)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 4


def test_fresh_database_ends_at_current_version_with_every_column_present(
    tmp_path: Path,
) -> None:
    conn = _open(tmp_path)

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(documents)")}

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
    assert {"extra_json", "frontmatter_json", "chunks_hash", "enriched_hash"}.issubset(columns)


def _create_version_1_documents_table(
    db_path: Path, already_present: tuple[str, ...] = (), *, version: int = 1
) -> None:
    # Every current column except extra_json, frontmatter_json, chunks_hash and enriched_hash by
    # default: the other columns have to stay, since the FTS triggers and the url index in
    # _SCHEMA reference them on every startup. `already_present` backfills some of the four,
    # because a real version-1 database was stamped 1 at whatever point in _SCHEMA's history it
    # was created, so it may already have any subset of them.
    columns = [
        "id                  INTEGER PRIMARY KEY",
        "source              TEXT    NOT NULL",
        "source_ref          TEXT    NOT NULL",
        "url                 TEXT",
        "parent_document_id  INTEGER",
        "title               TEXT",
        "author              TEXT",
        "content             TEXT",
        "summary             TEXT",
        "keywords            TEXT",
        "message_text        TEXT",
        "content_hash        TEXT",
        "status              TEXT    NOT NULL DEFAULT 'pending'",
        "error               TEXT",
        "fetched_at          TEXT",
        "created_at          TEXT    NOT NULL DEFAULT (datetime('now'))",
        "updated_at          TEXT    NOT NULL DEFAULT (datetime('now'))",
        "deleted_at          TEXT",
        *(f"{name} TEXT" for name in already_present),
        "UNIQUE (source, source_ref)",
    ]
    conn = sqlite3.connect(db_path)
    conn.execute(f"CREATE TABLE documents ({', '.join(columns)})")
    conn.execute(f"PRAGMA user_version={version}")
    conn.commit()
    conn.close()


def test_initialize_migrates_a_version_1_database_to_the_current_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "garden.db"
    _create_version_1_documents_table(db_path)

    conn = connect(db_path)
    initialize(conn)

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(documents)")}
    assert {"extra_json", "frontmatter_json", "chunks_hash", "enriched_hash"}.issubset(columns)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3


def test_initialize_migrates_a_version_0_database_whose_table_already_exists(
    tmp_path: Path,
) -> None:
    # A database that predates this step's first initialize() call: documents exists but
    # user_version was never stamped. Deciding "fresh" from the stamp alone treats this as
    # brand new, no-ops the CREATE TABLE, and stamps version 2 over a table still missing every
    # column added since, permanently, since the stamp then claims the schema is current.
    db_path = tmp_path / "garden.db"
    _create_version_1_documents_table(db_path, version=0)

    conn = connect(db_path)
    initialize(conn)

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(documents)")}
    assert {"extra_json", "frontmatter_json", "chunks_hash", "enriched_hash"}.issubset(columns)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3


def test_initialize_twice_on_a_migrated_database_changes_nothing_and_does_not_raise(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "garden.db"
    _create_version_1_documents_table(db_path)
    conn = connect(db_path)
    initialize(conn)

    initialize(conn)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3


def test_initialize_migrates_a_partially_upgraded_version_1_database(tmp_path: Path) -> None:
    # The shape every step-2 and step-3 database is actually in: user_version=1, but
    # extra_json and frontmatter_json already exist from a later _SCHEMA and only chunks_hash
    # and enriched_hash are missing. Trusting the version number here re-runs an ALTER SQLite
    # already applied.
    db_path = tmp_path / "garden.db"
    _create_version_1_documents_table(db_path, already_present=("extra_json", "frontmatter_json"))

    conn = connect(db_path)
    initialize(conn)
    initialize(conn)  # must not re-attempt the extra_json/frontmatter_json ALTERs either

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(documents)")}
    assert {"extra_json", "frontmatter_json", "chunks_hash", "enriched_hash"}.issubset(columns)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3


def test_initialize_on_a_version_1_database_with_every_column_already_present(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "garden.db"
    _create_version_1_documents_table(
        db_path,
        already_present=("extra_json", "frontmatter_json", "chunks_hash", "enriched_hash"),
    )

    conn = connect(db_path)
    initialize(conn)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3


def test_reinitializing_a_migrated_database_does_not_reapply_the_alters(
    tmp_path: Path,
) -> None:
    # If the version guard ever regressed to running migrations unconditionally, this would
    # raise sqlite3.OperationalError: duplicate column name instead of passing quietly.
    db_path = tmp_path / "garden.db"
    _create_version_1_documents_table(db_path)
    conn = connect(db_path)
    initialize(conn)
    conn.close()

    conn = connect(db_path)
    initialize(conn)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3


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
