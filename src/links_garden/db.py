"""SQLite schema and store. Standard library `sqlite3` only."""

import sqlite3
from pathlib import Path
from typing import Literal

Source = Literal["signal", "obsidian", "manual", "mcp"]

# Bump this and append a matching entry to _MIGRATIONS for any future schema change. Both
# CREATE TABLE IF NOT EXISTS (new tables) and this constant (new columns on existing tables)
# have to move together, or an upgraded database silently keeps missing columns forever.
#
# Version 4 added only the `sessions` table, which needs no _MIGRATIONS entry: `executescript`
# runs the whole _SCHEMA unconditionally on every `initialize`, so `CREATE TABLE IF NOT EXISTS`
# already creates it for an old database too. Only a new column on an existing table needs an
# entry here, since a repeated `ALTER TABLE ADD COLUMN` errors instead of doing nothing.
_SCHEMA_VERSION = 4

# Each entry upgrades a database stamped at (key - 1) up to key, as (column, statement) pairs.
# Never edit a past entry: a database already migrated past it will never see the change.
#
# The column name is checked against PRAGMA table_info before its statement runs. Every build
# between step 1 and this one stamped a fresh database straight to version 1, whatever _SCHEMA
# happened to create that day, so a real version-1 database may already have any subset of
# these columns: the version number alone cannot say which ALTERs still need to run.
_MIGRATIONS: dict[int, tuple[tuple[str, str], ...]] = {
    2: (
        ("extra_json", "ALTER TABLE documents ADD COLUMN extra_json TEXT"),
        ("frontmatter_json", "ALTER TABLE documents ADD COLUMN frontmatter_json TEXT"),
        ("chunks_hash", "ALTER TABLE documents ADD COLUMN chunks_hash TEXT"),
    ),
    3: (("enriched_hash", "ALTER TABLE documents ADD COLUMN enriched_hash TEXT"),),
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id                  INTEGER PRIMARY KEY,
    source              TEXT    NOT NULL CHECK (source IN ('signal','obsidian','manual','mcp')),
    source_ref          TEXT    NOT NULL,
    url                 TEXT,
    parent_document_id  INTEGER REFERENCES documents(id) ON DELETE SET NULL,
    title               TEXT,
    author              TEXT,
    content             TEXT,
    summary             TEXT,
    keywords            TEXT,
    message_text        TEXT,
    content_hash        TEXT,
    chunks_hash         TEXT,
    enriched_hash       TEXT,
    extra_json          TEXT,
    frontmatter_json    TEXT,
    status              TEXT    NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending','ok','failed')),
    error               TEXT,
    fetched_at          TEXT,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    deleted_at          TEXT,
    UNIQUE (source, source_ref)
);

CREATE TABLE IF NOT EXISTS chunks (
    id            INTEGER PRIMARY KEY,
    document_id   INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal       INTEGER NOT NULL,
    text          TEXT    NOT NULL,
    token_count   INTEGER NOT NULL,
    embedding     BLOB,
    UNIQUE (document_id, ordinal)
);

CREATE TABLE IF NOT EXISTS sets (
    id           INTEGER PRIMARY KEY,
    name         TEXT    NOT NULL UNIQUE,
    description  TEXT    NOT NULL,
    schema_json  TEXT    NOT NULL,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS set_memberships (
    document_id     INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    set_id          INTEGER NOT NULL REFERENCES sets(id) ON DELETE CASCADE,
    extracted_json  TEXT,
    missing_fields  TEXT,
    status          TEXT    NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending','ok','partial','failed')),
    error           TEXT,
    extracted_at    TEXT,
    PRIMARY KEY (document_id, set_id)
);

CREATE TABLE IF NOT EXISTS ingest_log (
    id           INTEGER PRIMARY KEY,
    document_id  INTEGER REFERENCES documents(id) ON DELETE CASCADE,
    actor        TEXT    NOT NULL CHECK (actor IN ('cron','cli','dashboard','mcp')),
    url          TEXT,
    at           TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- One row per Signal message that has been reacted to. A message has many URL rows, so the
-- reaction (one per message) can't honestly live as a column on any single one of them.
CREATE TABLE IF NOT EXISTS signal_reactions (
    message_id  TEXT PRIMARY KEY,
    reacted_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Dashboard login sessions behind the HttpOnly cookie. Only the token's hash is stored, same
-- reasoning as a password digest: a copy of this table leaks no usable credential. `token_hash`
-- is UNIQUE, which is also the index a session lookup needs.
CREATE TABLE IF NOT EXISTS sessions (
    id           INTEGER PRIMARY KEY,
    token_hash   TEXT NOT NULL UNIQUE,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_documents_status  ON documents(status);
CREATE INDEX IF NOT EXISTS idx_documents_source  ON documents(source, source_ref);
CREATE INDEX IF NOT EXISTS idx_documents_deleted ON documents(deleted_at);
CREATE INDEX IF NOT EXISTS idx_documents_url     ON documents(url);
CREATE INDEX IF NOT EXISTS idx_chunks_document   ON chunks(document_id);

-- remove_diacritics 2 folds accents so "referencement" matches "référencement";
-- the corpus is French and English.
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5 (
    title, content, summary, keywords,
    content='documents',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS documents_fts_insert AFTER INSERT ON documents BEGIN
    INSERT INTO documents_fts(rowid, title, content, summary, keywords)
    VALUES (new.id, new.title, new.content, new.summary, new.keywords);
END;

CREATE TRIGGER IF NOT EXISTS documents_fts_delete AFTER DELETE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, title, content, summary, keywords)
    VALUES ('delete', old.id, old.title, old.content, old.summary, old.keywords);
END;

CREATE TRIGGER IF NOT EXISTS documents_fts_update AFTER UPDATE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, title, content, summary, keywords)
    VALUES ('delete', old.id, old.title, old.content, old.summary, old.keywords);
    INSERT INTO documents_fts(rowid, title, content, summary, keywords)
    VALUES (new.id, new.title, new.content, new.summary, new.keywords);
END;
"""


def connect(path: Path) -> sqlite3.Connection:
    """Open a connection to the database at `path`, creating its parent directory if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # busy_timeout must be set before journal_mode: on a brand-new database, the mode switch
    # itself can hit a writer lock, and with no timeout yet in place it fails instead of waiting.
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    # foreign_keys is per-connection and off by default; the schema relies on ON DELETE CASCADE.
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def initialize(conn: sqlite3.Connection) -> None:
    """Create the schema if it does not exist yet, and migrate an older database up to date."""
    # A database whose documents table doesn't exist yet is brand new: executescript below
    # builds every column the current schema defines, so there is nothing to migrate. This
    # can't be read off user_version: a build stamped straight to 1 on creation, at any point
    # in _SCHEMA's history, looks identical to a real fresh database from the stamp alone.
    fresh = (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'documents'"
        ).fetchone()
        is None
    )
    conn.executescript(_SCHEMA)
    if fresh:
        conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
    else:
        _migrate(conn)
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """Add whatever `documents` columns the current schema needs that this database lacks.

    The version stamp is only trusted to skip a database that is already current. Which
    columns an unstamped database is missing is read from the table itself, not guessed from
    the version number: see the comment on `_MIGRATIONS`.
    """
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= _SCHEMA_VERSION:
        return
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(documents)")}
    # Looping rather than testing a single `if` matters the moment a database is more than one
    # version behind: skipping straight to the newest migration would leave the versions
    # between unapplied.
    for target in range(version + 1, _SCHEMA_VERSION + 1):
        # .get, not []: a database can now enter here at version 0, and version 1 (the
        # original baseline) added no column of its own, so it has no entry to look up.
        for column, statement in _MIGRATIONS.get(target, ()):
            if column not in columns:
                conn.execute(statement)
                columns.add(column)
    # user_version can't be parameterized.
    conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")


def tombstone(conn: sqlite3.Connection, document_id: int) -> None:
    """Mark a document deleted without removing it, so re-sync skips its `source_ref` forever."""
    conn.execute(
        "UPDATE documents SET deleted_at = datetime('now'), updated_at = datetime('now') "
        "WHERE id = ?",
        (document_id,),
    )
    conn.commit()


def purge(conn: sqlite3.Connection, document_id: int) -> None:
    """Delete a document row outright, so it re-indexes as new if it reappears.

    A tombstoned row is left untouched: a dashboard delete outranks the file going away, so a
    document already marked deleted stays deleted even if `purge` is called on it again.
    """
    conn.execute("DELETE FROM documents WHERE id = ? AND deleted_at IS NULL", (document_id,))
    conn.commit()


def is_tombstoned(conn: sqlite3.Connection, source: Source, source_ref: str) -> bool:
    """Return whether a tombstoned row exists for this `(source, source_ref)` pair."""
    row = conn.execute(
        "SELECT 1 FROM documents WHERE source = ? AND source_ref = ? AND deleted_at IS NOT NULL",
        (source, source_ref),
    ).fetchone()
    return row is not None


def create_session(conn: sqlite3.Connection, token_hash: str, expires_at: str) -> None:
    """Record a new session. `token_hash` and `expires_at` are computed by the caller: this file
    only stores what it's given, matching every other write in this module."""
    conn.execute(
        "INSERT INTO sessions (token_hash, expires_at) VALUES (?, ?)", (token_hash, expires_at)
    )
    conn.commit()


def has_valid_session(conn: sqlite3.Connection, token_hash: str) -> bool:
    """Whether a non-expired session exists for this hash.

    An expired row is deleted here rather than merely ignored, so `sessions` doesn't grow
    forever with rows nothing will ever match again.
    """
    row = conn.execute(
        "SELECT 1 FROM sessions WHERE token_hash = ? AND expires_at > datetime('now')",
        (token_hash,),
    ).fetchone()
    if row is not None:
        return True
    conn.execute(
        "DELETE FROM sessions WHERE token_hash = ? AND expires_at <= datetime('now')",
        (token_hash,),
    )
    conn.commit()
    return False


def delete_session(conn: sqlite3.Connection, token_hash: str) -> None:
    """Revoke a session outright, expired or not -- what signing out calls."""
    conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
    conn.commit()
