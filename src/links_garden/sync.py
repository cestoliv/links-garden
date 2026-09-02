"""Sync orchestration: walk the vault into the store, follow its URLs, and ingest one-offs.

Every mutation here commits on its own. `extract` never raises, but a bad row or a duplicate
key can, and one bad note or URL must not roll back everything already synced this run.
"""

import json
import logging
import sqlite3
from dataclasses import dataclass
from typing import Literal

from links_garden.adapters import Extracted, extract
from links_garden.config import Settings
from links_garden.db import Source, is_tombstoned, purge
from links_garden.fetch import Fetcher
from links_garden.vault import VaultNote, read_vault

logger = logging.getLogger(__name__)


@dataclass
class SyncReport:
    """Counts of what one `sync_vault` call actually did."""

    notes_seen: int = 0
    notes_added: int = 0
    notes_updated: int = 0
    notes_purged: int = 0
    urls_seen: int = 0
    urls_fetched: int = 0
    urls_cached: int = 0
    urls_failed: int = 0
    urls_skipped: int = 0
    urls_removed: int = 0
    tombstones_respected: int = 0
    urls_orphaned: int = 0


def sync_vault(
    conn: sqlite3.Connection,
    settings: Settings,
    fetcher: Fetcher,
    *,
    follow_urls: bool = True,
) -> SyncReport:
    """Walk the configured vault into `documents`, then purge notes no longer on disk."""
    if settings.vault_path is None:
        raise ValueError("VAULT_PATH is not configured")

    report = SyncReport()
    seen_paths: set[str] = set()
    for note in read_vault(settings.vault_path, settings.vault_exclude):
        report.notes_seen += 1
        seen_paths.add(note.relative_path)
        try:
            _sync_note(conn, note, fetcher, settings, follow_urls=follow_urls, report=report)
        except Exception:
            logger.exception("failed to sync note %s", note.relative_path)
            conn.rollback()

    _purge_missing_notes(conn, seen_paths, report)
    return report


def ingest_url(
    conn: sqlite3.Connection, url: str, fetcher: Fetcher, *, source: Source = "manual"
) -> Extracted:
    """Fetch, extract and store one ad hoc URL as a document under `source`.

    `source` defaults to `manual` for the CLI and dashboard; the API passes `mcp` when the
    caller marks itself as an agent, so DESIGN.md's per-source provenance holds on this path too.
    Shares `resolve_status` with `_sync_url`: a fetch the run never actually attempted,
    because the cap was hit mid-extraction, is left `pending` rather than recorded as `failed`.
    """
    extracted = extract(url, fetcher)
    status = resolve_status(extracted, fetcher)
    if status == "skipped":
        row = conn.execute(
            "SELECT id FROM documents WHERE source = ? AND source_ref = ?", (source, url)
        ).fetchone()
        _mark_pending_if_new(conn, row, source, url, url, None)
        return extracted
    upsert_extracted(
        conn,
        source=source,
        source_ref=url,
        url=url,
        parent_document_id=None,
        extracted=extracted,
        status=status,
    )
    return extracted


def resolve_status(extracted: Extracted, fetcher: Fetcher) -> Literal["ok", "failed", "skipped"]:
    """The terminal status of one extraction: `ok`, a genuine `failed`, or `skipped` when the
    run cap was hit mid-extraction and nothing was actually learned about the URL.

    A multi-hop extraction (shorteners, TikTok) can spend its first hop, then hit the cap on a
    later hop. `Extracted` carries no status field, so a genuine failure whose own fetch happens
    to exhaust the cap is indistinguishable here from a cap-skip: both show up as
    `extracted.error is not None` with `fetcher.at_cap`. Treating that pair as skipped errs
    safe: it is retried next run, where the fetcher's own on-disk cache returns the same failure
    instantly and it is recorded as `failed` correctly then. Shared by `_sync_url` and
    `ingest_url` so both settle a capped extraction the same way.
    """
    if extracted.error is None:
        return "ok"
    if fetcher.at_cap:
        return "skipped"
    return "failed"


def _sync_note(
    conn: sqlite3.Connection,
    note: VaultNote,
    fetcher: Fetcher,
    settings: Settings,
    *,
    follow_urls: bool,
    report: SyncReport,
) -> None:
    if is_tombstoned(conn, "obsidian", note.relative_path):
        report.tombstones_respected += 1
        return

    document_id = _upsert_note(conn, note, report)
    if not follow_urls:
        return

    for url in note.urls:
        report.urls_seen += 1
        try:
            _sync_url(conn, document_id, note.relative_path, url, fetcher, settings, report)
        except Exception:
            logger.exception("failed to sync url %s in note %s", url, note.relative_path)
            conn.rollback()

    _purge_removed_urls(conn, document_id, note, report)


def _purge_removed_urls(
    conn: sqlite3.Connection, document_id: int, note: VaultNote, report: SyncReport
) -> None:
    """Purge this note's `obsidian` children whose URL the note no longer contains.

    A child keeps `parent_document_id` set as long as its note exists, so editing a link out
    of a note is otherwise invisible to every purge pass: the child is never an orphan.
    `db.purge` refuses a tombstoned row, so a child the user deleted through the dashboard
    stays deleted.
    """
    current_refs = {f"{note.relative_path}#{url}" for url in note.urls}
    rows = conn.execute(
        "SELECT id, source_ref FROM documents "
        "WHERE source = 'obsidian' AND parent_document_id = ? AND deleted_at IS NULL",
        (document_id,),
    ).fetchall()
    for row in rows:
        if row["source_ref"] not in current_refs:
            purge(conn, row["id"])
            report.urls_removed += 1
            logger.info("note %s no longer links %s", note.relative_path, row["source_ref"])


def _upsert_note(conn: sqlite3.Connection, note: VaultNote, report: SyncReport) -> int:
    row = conn.execute(
        "SELECT id, content_hash FROM documents WHERE source = 'obsidian' AND source_ref = ?",
        (note.relative_path,),
    ).fetchone()
    frontmatter_json = json.dumps(note.frontmatter)

    if row is None:
        # A note's own content is already in hand, never fetched, so it starts at 'ok'
        # rather than the table's 'pending' default.
        cursor = conn.execute(
            "INSERT INTO documents "
            "(source, source_ref, title, content, content_hash, frontmatter_json, status) "
            "VALUES ('obsidian', ?, ?, ?, ?, ?, 'ok')",
            (note.relative_path, note.title, note.body, note.content_hash, frontmatter_json),
        )
        conn.commit()
        report.notes_added += 1
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    document_id: int = row["id"]
    if row["content_hash"] != note.content_hash:
        conn.execute(
            "UPDATE documents SET title = ?, content = ?, content_hash = ?, "
            "frontmatter_json = ?, updated_at = datetime('now') WHERE id = ?",
            (note.title, note.body, note.content_hash, frontmatter_json, document_id),
        )
        conn.commit()
        report.notes_updated += 1
    return document_id


def _sync_url(
    conn: sqlite3.Connection,
    parent_id: int,
    relative_path: str,
    url: str,
    fetcher: Fetcher,
    settings: Settings,
    report: SyncReport,
) -> None:
    source_ref = f"{relative_path}#{url}"
    if is_tombstoned(conn, "obsidian", source_ref):
        report.tombstones_respected += 1
        return

    row = conn.execute(
        "SELECT id, status FROM documents WHERE source = 'obsidian' AND source_ref = ?",
        (source_ref,),
    ).fetchone()
    if row is not None and row["status"] == "ok":
        report.urls_cached += 1
        return

    # ponytail: the run cap is checked here, before calling extract(), rather than letting
    # Fetcher discover "skipped" mid-extraction. Checking first avoids starting a multi-hop
    # extraction (shorteners, TikTok) that would fail partway through once the cap hits.
    # Trade-off: a URL already sitting in Fetcher's on-disk cache is left pending here instead
    # of resolved for free. Revisit if that reuse turns out to matter in practice.
    if fetcher.at_cap:
        report.urls_skipped += 1
        _mark_pending_if_new(conn, row, "obsidian", source_ref, url, parent_id)
        return

    extracted = extract(url, fetcher)
    status = resolve_status(extracted, fetcher)
    if status == "ok":
        report.urls_fetched += 1
        upsert_extracted(
            conn,
            source="obsidian",
            source_ref=source_ref,
            url=url,
            parent_document_id=parent_id,
            extracted=extracted,
            status="ok",
        )
        return

    if status == "skipped":
        report.urls_skipped += 1
        _mark_pending_if_new(conn, row, "obsidian", source_ref, url, parent_id)
        return

    report.urls_failed += 1
    upsert_extracted(
        conn,
        source="obsidian",
        source_ref=source_ref,
        url=url,
        parent_document_id=parent_id,
        extracted=extracted,
        status="failed",
    )


def _mark_pending_if_new(
    conn: sqlite3.Connection,
    row: sqlite3.Row | None,
    source: Source,
    source_ref: str,
    url: str,
    parent_document_id: int | None,
) -> None:
    """Insert a placeholder for a URL skipped by the cap. A retried URL already has a row."""
    if row is not None:
        return
    conn.execute(
        "INSERT INTO documents (source, source_ref, url, parent_document_id, status) "
        "VALUES (?, ?, ?, ?, 'pending')",
        (source, source_ref, url, parent_document_id),
    )
    conn.commit()


def upsert_extracted(
    conn: sqlite3.Connection,
    *,
    source: Source,
    source_ref: str,
    url: str,
    parent_document_id: int | None,
    extracted: Extracted,
    status: Literal["ok", "failed"],
) -> None:
    """Insert or update one URL document. Generic over `source` and `parent_document_id`, so
    `signal_sync` shares it rather than reimplementing the same upsert.
    """
    extra_json = json.dumps(extracted.extra) if extracted.extra else None
    row = conn.execute(
        "SELECT id FROM documents WHERE source = ? AND source_ref = ?", (source, source_ref)
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO documents "
            "(source, source_ref, url, parent_document_id, title, author, content, extra_json, "
            " status, error, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            (
                source,
                source_ref,
                url,
                parent_document_id,
                extracted.title,
                extracted.author,
                extracted.content,
                extra_json,
                status,
                extracted.error,
            ),
        )
    else:
        conn.execute(
            "UPDATE documents SET title = ?, author = ?, content = ?, extra_json = ?, "
            "status = ?, error = ?, fetched_at = datetime('now'), updated_at = datetime('now') "
            "WHERE id = ?",
            (
                extracted.title,
                extracted.author,
                extracted.content,
                extra_json,
                status,
                extracted.error,
                row["id"],
            ),
        )
    conn.commit()


def _purge_missing_notes(
    conn: sqlite3.Connection, seen_paths: set[str], report: SyncReport
) -> None:
    # `url IS NULL` is what tells a note row apart from a URL child row here: a note is never
    # given a `url`, and a URL child's parent can itself be NULL once its own note is purged.
    # Filtering on `url` instead of `parent_document_id` keeps an already-orphaned URL out of
    # this purge pass forever, rather than re-matching it as a "missing note" every run.
    rows = conn.execute(
        "SELECT id, source_ref FROM documents "
        "WHERE source = 'obsidian' AND url IS NULL AND deleted_at IS NULL"
    ).fetchall()
    for row in rows:
        if row["source_ref"] in seen_paths:
            continue
        # A missing note is purged along with its URL children rather than leaving them
        # parentless: a note moved or renamed is normal vault workflow, and Fetcher's cache is
        # keyed by URL hash, not source_ref, so re-syncing under the new path spends no extra
        # Firecrawl credit. `deleted_at IS NULL` keeps a tombstoned child, and its count, out
        # of this pass: a dashboard delete outranks the note going away.
        children = conn.execute(
            "SELECT id FROM documents WHERE parent_document_id = ? AND deleted_at IS NULL",
            (row["id"],),
        ).fetchall()
        purge(conn, row["id"])
        report.notes_purged += 1
        for child in children:
            purge(conn, child["id"])
        if children:
            report.urls_orphaned += len(children)
            logger.info(
                "purged note %s purged %d url document(s)", row["source_ref"], len(children)
            )
