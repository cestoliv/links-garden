"""SQLite schema and store. Standard library `sqlite3` only."""

import sqlite3
from pathlib import Path
from typing import Literal

Source = Literal["signal", "obsidian", "manual", "mcp"]

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
    status              TEXT    NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending','ok','failed')),
    error               TEXT,
    retry_count         INTEGER NOT NULL DEFAULT 0,
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
    """Create the schema if it does not exist yet. Idempotent, safe to call on every startup."""
    conn.executescript(_SCHEMA)
    # user_version can't be parameterized; only stamp it on a fresh database so a later
    # migration's bump survives every subsequent startup call to initialize().
    if conn.execute("PRAGMA user_version").fetchone()[0] == 0:
        conn.execute("PRAGMA user_version=1")
    conn.commit()


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
