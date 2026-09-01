"""Sync orchestration for the Signal chat to self: walk its messages, follow their URLs, and
react once a message's links have made it into the store.

The vault gives every URL a parent note; a Signal message is a bare URL carrier with no such
parent, so every document here stands alone with `parent_document_id` NULL.
"""

import logging
import sqlite3
from dataclasses import dataclass

from links_garden import sync
from links_garden.adapters import extract
from links_garden.beeper import BeeperClient, Message
from links_garden.config import Settings
from links_garden.db import is_tombstoned
from links_garden.fetch import Fetcher
from links_garden.vault import _URL_PATTERN, find_urls

logger = logging.getLogger(__name__)


@dataclass
class SignalReport:
    """Counts of what one `sync_signal` call actually did."""

    messages_seen: int = 0
    messages_with_links: int = 0
    urls_seen: int = 0
    urls_fetched: int = 0
    urls_cached: int = 0
    urls_failed: int = 0
    urls_skipped: int = 0
    tombstones_respected: int = 0
    reactions_added: int = 0
    reactions_failed: int = 0


def sync_signal(
    conn: sqlite3.Connection,
    settings: Settings,
    fetcher: Fetcher,
    beeper: BeeperClient,
    *,
    follow_urls: bool = True,
) -> SignalReport:
    """Walk the configured Signal chat, storing one document per URL each message carries."""
    report = SignalReport()
    messages = beeper.iter_messages(settings.beeper_chat_id, since=settings.backfill_start_date)
    for message in messages:
        report.messages_seen += 1
        try:
            _sync_message(
                conn,
                message,
                fetcher,
                beeper,
                settings.beeper_chat_id,
                follow_urls=follow_urls,
                report=report,
            )
        except Exception:
            logger.exception("failed to sync message %s", message.id)
            conn.rollback()
    return report


def _sync_message(
    conn: sqlite3.Connection,
    message: Message,
    fetcher: Fetcher,
    beeper: BeeperClient,
    chat_id: str,
    *,
    follow_urls: bool,
    report: SignalReport,
) -> None:
    urls = tuple(find_urls(message.text))
    if not urls:
        return  # 85% of link-carrying messages are a bare URL; one with none is just chat noise
    report.messages_with_links += 1
    if not follow_urls:
        return  # nothing gets ingested, so a reaction here would mark work that never happened

    message_text = _strip_urls(message.text) or None
    any_ok = False
    for url in urls:
        report.urls_seen += 1
        try:
            if _sync_url(conn, message.id, url, message_text, fetcher, report):
                any_ok = True
        except Exception:
            logger.exception("failed to sync url %s in message %s", url, message.id)
            conn.rollback()

    # The mark means captured, not seen: react only once something actually made it in, and
    # only once per message ever, tracked in `signal_reactions` since a failed URL is retried
    # forever and would otherwise re-react on every run.
    if any_ok and not _has_reacted(conn, message.id):
        _react(conn, beeper, chat_id, message.id, report)


def _strip_urls(text: str) -> str:
    return " ".join(_URL_PATTERN.sub("", text).split())


def _sync_url(
    conn: sqlite3.Connection,
    message_id: str,
    url: str,
    message_text: str | None,
    fetcher: Fetcher,
    report: SignalReport,
) -> bool:
    """Sync one URL and report whether its document is (now) `status='ok'`."""
    source_ref = f"{message_id}#{url}"
    if is_tombstoned(conn, "signal", source_ref):
        report.tombstones_respected += 1
        return False

    row = conn.execute(
        "SELECT id, status FROM documents WHERE source = 'signal' AND source_ref = ?",
        (source_ref,),
    ).fetchone()
    if row is not None and row["status"] == "ok":
        report.urls_cached += 1
        return True

    if fetcher.at_cap:
        report.urls_skipped += 1
        _mark_pending(conn, row, source_ref, url, message_text)
        return False

    extracted = extract(url, fetcher)
    status = sync.resolve_status(extracted, fetcher)
    if status == "ok":
        report.urls_fetched += 1
        sync.upsert_extracted(
            conn,
            source="signal",
            source_ref=source_ref,
            url=url,
            parent_document_id=None,
            extracted=extracted,
            status="ok",
        )
        _set_message_text(conn, source_ref, message_text)
        return True

    if status == "skipped":
        report.urls_skipped += 1
        _mark_pending(conn, row, source_ref, url, message_text)
        return False

    report.urls_failed += 1
    sync.upsert_extracted(
        conn,
        source="signal",
        source_ref=source_ref,
        url=url,
        parent_document_id=None,
        extracted=extracted,
        status="failed",
    )
    _set_message_text(conn, source_ref, message_text)
    return False


def _mark_pending(
    conn: sqlite3.Connection,
    row: sqlite3.Row | None,
    source_ref: str,
    url: str,
    message_text: str | None,
) -> None:
    """Insert a placeholder for a URL skipped by the cap. A retried URL already has a row."""
    if row is not None:
        return
    conn.execute(
        "INSERT INTO documents (source, source_ref, url, message_text, status) "
        "VALUES ('signal', ?, ?, ?, 'pending')",
        (source_ref, url, message_text),
    )
    conn.commit()


def _set_message_text(conn: sqlite3.Connection, source_ref: str, message_text: str | None) -> None:
    # upsert_extracted is shared with the vault sync and knows nothing of Signal's own
    # commentary column, so it lands here as a second, separately committed statement.
    conn.execute(
        "UPDATE documents SET message_text = ? WHERE source = 'signal' AND source_ref = ?",
        (message_text, source_ref),
    )
    conn.commit()


def _has_reacted(conn: sqlite3.Connection, message_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM signal_reactions WHERE message_id = ?", (message_id,)
    ).fetchone()
    return row is not None


def _react(
    conn: sqlite3.Connection,
    beeper: BeeperClient,
    chat_id: str,
    message_id: str,
    report: SignalReport,
) -> None:
    # The row is inserted only after a True return, so a reaction the API failed to record is
    # retried on the next run instead of being silently treated as done.
    if beeper.add_reaction(chat_id, message_id):
        conn.execute("INSERT INTO signal_reactions (message_id) VALUES (?)", (message_id,))
        conn.commit()
        report.reactions_added += 1
    else:
        report.reactions_failed += 1
