import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from urllib.parse import quote

from _pytest.monkeypatch import MonkeyPatch

from links_garden import sync
from links_garden.adapters import Extracted
from links_garden.config import Settings
from links_garden.db import connect, initialize, is_tombstoned, tombstone
from links_garden.fetch import Fetcher, FetchResult
from links_garden.sync import SyncReport, ingest_url, sync_vault
from links_garden.vault import VaultNote


@dataclass
class _Call:
    url: str
    force_direct: bool


class FakeFetcher:
    """Canned, in-memory stand-in for `Fetcher`. Records every call; never touches the network."""

    def __init__(
        self,
        responses: dict[str, FetchResult] | None = None,
        *,
        spent: int = 0,
        max_fetches_per_run: int = 50,
    ) -> None:
        self._responses = responses or {}
        self.spent = spent
        self._max_fetches_per_run = max_fetches_per_run
        self.calls: list[_Call] = []

    @property
    def at_cap(self) -> bool:
        return self.spent >= self._max_fetches_per_run

    def fetch(self, url: str, *, force_direct: bool = False) -> FetchResult:
        self.calls.append(_Call(url=url, force_direct=force_direct))
        if url not in self._responses:
            raise AssertionError(f"unexpected fetch: {url}")
        self.spent += 1
        return self._responses[url]


def _as_fetcher(fake: FakeFetcher) -> Fetcher:
    return cast(Fetcher, fake)


def _ok(url: str, body: str) -> FetchResult:
    return FetchResult(
        url=url,
        final_url=url,
        status="ok",
        body=body,
        content_type="text/html",
        error=None,
        from_cache=False,
    )


def _failed(url: str, error: str) -> FetchResult:
    return FetchResult(
        url=url,
        final_url=url,
        status="failed",
        body=None,
        content_type=None,
        error=error,
        from_cache=False,
    )


def _open(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "garden.db")
    initialize(conn)
    return conn


def _write(root: Path, relative: str, content: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _settings(
    tmp_path: Path,
    vault_path: Path,
    *,
    vault_exclude: tuple[str, ...] = (),
    max_fetches_per_run: int = 50,
) -> Settings:
    return Settings(
        _env_file=None,
        vault_path=vault_path,
        vault_exclude=vault_exclude,
        fetch_cache_dir=tmp_path / "cache",
        max_fetches_per_run=max_fetches_per_run,
    )


def test_first_sync_inserts_every_note(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write(vault, "a.md", "first note")
    _write(vault, "b.md", "second note")
    conn = _open(tmp_path)

    report = sync_vault(conn, _settings(tmp_path, vault), _as_fetcher(FakeFetcher()))

    assert report.notes_seen == 2
    assert report.notes_added == 2
    assert report.notes_updated == 0
    rows = conn.execute(
        "SELECT source_ref, content FROM documents WHERE source = 'obsidian' ORDER BY source_ref"
    ).fetchall()
    assert [(row["source_ref"], row["content"]) for row in rows] == [
        ("a.md", "first note"),
        ("b.md", "second note"),
    ]


def test_second_sync_with_no_changes_is_a_noop(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write(vault, "a.md", "unchanged")
    conn = _open(tmp_path)
    settings = _settings(tmp_path, vault)
    sync_vault(conn, settings, _as_fetcher(FakeFetcher()))

    report = sync_vault(conn, settings, _as_fetcher(FakeFetcher()))

    assert report.notes_seen == 1
    assert report.notes_added == 0
    assert report.notes_updated == 0


def test_changed_note_updates_content_hash_and_updated_at(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    path = _write(vault, "a.md", "version one")
    conn = _open(tmp_path)
    settings = _settings(tmp_path, vault)
    sync_vault(conn, settings, _as_fetcher(FakeFetcher()))
    conn.execute(
        "UPDATE documents SET updated_at = '2020-01-01 00:00:00' WHERE source_ref = 'a.md'"
    )
    conn.commit()
    old_hash = conn.execute(
        "SELECT content_hash FROM documents WHERE source_ref = 'a.md'"
    ).fetchone()["content_hash"]
    path.write_text("version two")

    report = sync_vault(conn, settings, _as_fetcher(FakeFetcher()))

    assert report.notes_updated == 1
    row = conn.execute(
        "SELECT content, content_hash, updated_at FROM documents WHERE source_ref = 'a.md'"
    ).fetchone()
    assert row["content"] == "version two"
    assert row["content_hash"] != old_hash
    assert row["updated_at"] != "2020-01-01 00:00:00"


def test_deleted_note_is_purged(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    path = _write(vault, "a.md", "temporary")
    conn = _open(tmp_path)
    settings = _settings(tmp_path, vault)
    sync_vault(conn, settings, _as_fetcher(FakeFetcher()))
    path.unlink()

    report = sync_vault(conn, settings, _as_fetcher(FakeFetcher()))

    assert report.notes_purged == 1
    assert conn.execute("SELECT * FROM documents WHERE source_ref = 'a.md'").fetchone() is None


def test_tombstoned_note_is_skipped_and_stays_skipped_after_a_file_change(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    path = _write(vault, "a.md", "original")
    conn = _open(tmp_path)
    settings = _settings(tmp_path, vault)
    sync_vault(conn, settings, _as_fetcher(FakeFetcher()))
    document_id = conn.execute("SELECT id FROM documents WHERE source_ref = 'a.md'").fetchone()[
        "id"
    ]
    tombstone(conn, document_id)
    path.write_text("changed after tombstone")

    report = sync_vault(conn, settings, _as_fetcher(FakeFetcher()))

    assert report.tombstones_respected == 1
    row = conn.execute("SELECT content FROM documents WHERE source_ref = 'a.md'").fetchone()
    assert row["content"] == "original"


def test_tombstoned_note_whose_file_disappears_is_not_purged(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    path = _write(vault, "a.md", "original")
    conn = _open(tmp_path)
    settings = _settings(tmp_path, vault)
    sync_vault(conn, settings, _as_fetcher(FakeFetcher()))
    document_id = conn.execute("SELECT id FROM documents WHERE source_ref = 'a.md'").fetchone()[
        "id"
    ]
    tombstone(conn, document_id)
    path.unlink()

    report = sync_vault(conn, settings, _as_fetcher(FakeFetcher()))

    assert report.notes_purged == 0
    assert conn.execute("SELECT * FROM documents WHERE source_ref = 'a.md'").fetchone() is not None
    assert is_tombstoned(conn, "obsidian", "a.md") is True


def test_note_urls_create_child_documents_with_parent_and_source_ref(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write(vault, "a.md", "see https://example.test/x for details")
    conn = _open(tmp_path)
    settings = _settings(tmp_path, vault)
    fetcher = FakeFetcher({"https://example.test/x": _ok("https://example.test/x", "<html/>")})

    sync_vault(conn, settings, _as_fetcher(fetcher))

    note_id = conn.execute("SELECT id FROM documents WHERE source_ref = 'a.md'").fetchone()["id"]
    child = conn.execute(
        "SELECT * FROM documents WHERE source_ref = 'a.md#https://example.test/x'"
    ).fetchone()
    assert child is not None
    assert child["parent_document_id"] == note_id
    assert child["url"] == "https://example.test/x"
    assert child["status"] == "ok"


def test_tiktok_author_url_round_trips_through_extra_json(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    url = "https://www.tiktok.com/@someone/video/123"
    _write(vault, "a.md", url)
    conn = _open(tmp_path)
    settings = _settings(tmp_path, vault)
    oembed_url = f"https://www.tiktok.com/oembed?url={quote(url, safe='')}"
    body = json.dumps(
        {
            "title": "caption",
            "author_name": "someone",
            "author_url": "https://www.tiktok.com/@someone",
        }
    )
    fetcher = FakeFetcher({oembed_url: _ok(oembed_url, body)})

    sync_vault(conn, settings, _as_fetcher(fetcher))

    child = conn.execute(
        f"SELECT extra_json FROM documents WHERE source_ref = 'a.md#{url}'"
    ).fetchone()
    assert json.loads(child["extra_json"]) == {"author_url": "https://www.tiktok.com/@someone"}


def test_extra_json_is_null_when_extraction_yields_no_extra_fields(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write(vault, "a.md", "https://example.test/x")
    conn = _open(tmp_path)
    settings = _settings(tmp_path, vault)
    fetcher = FakeFetcher({"https://example.test/x": _ok("https://example.test/x", "<html/>")})

    sync_vault(conn, settings, _as_fetcher(fetcher))

    child = conn.execute(
        "SELECT extra_json FROM documents WHERE source_ref = 'a.md#https://example.test/x'"
    ).fetchone()
    assert child["extra_json"] is None


def test_note_frontmatter_round_trips_through_the_database(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write(vault, "a.md", "---\ntags:\n  - ios\n  - swift\n---\nbody\n")
    conn = _open(tmp_path)
    settings = _settings(tmp_path, vault)

    sync_vault(conn, settings, _as_fetcher(FakeFetcher()))

    row = conn.execute(
        "SELECT frontmatter_json FROM documents WHERE source_ref = 'a.md'"
    ).fetchone()
    assert json.loads(row["frontmatter_json"]) == {"tags": ["ios", "swift"]}


def test_note_without_frontmatter_stores_empty_object(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write(vault, "a.md", "just a body, no frontmatter\n")
    conn = _open(tmp_path)
    settings = _settings(tmp_path, vault)

    sync_vault(conn, settings, _as_fetcher(FakeFetcher()))

    row = conn.execute(
        "SELECT frontmatter_json FROM documents WHERE source_ref = 'a.md'"
    ).fetchone()
    assert json.loads(row["frontmatter_json"]) == {}


def test_url_already_ok_is_not_refetched(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write(vault, "a.md", "https://example.test/x")
    conn = _open(tmp_path)
    settings = _settings(tmp_path, vault)
    sync_vault(
        conn,
        settings,
        _as_fetcher(
            FakeFetcher({"https://example.test/x": _ok("https://example.test/x", "<html/>")})
        ),
    )

    fetcher = FakeFetcher({"https://example.test/x": _ok("https://example.test/x", "<html/>")})
    report = sync_vault(conn, settings, _as_fetcher(fetcher))

    assert report.urls_cached == 1
    assert report.urls_fetched == 0
    assert fetcher.calls == []


def test_tombstoned_url_child_is_not_refetched(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write(vault, "a.md", "https://example.test/x")
    conn = _open(tmp_path)
    settings = _settings(tmp_path, vault)
    sync_vault(
        conn,
        settings,
        _as_fetcher(
            FakeFetcher({"https://example.test/x": _ok("https://example.test/x", "<html/>")})
        ),
    )
    child_id = conn.execute(
        "SELECT id FROM documents WHERE source_ref = 'a.md#https://example.test/x'"
    ).fetchone()["id"]
    tombstone(conn, child_id)

    fetcher = FakeFetcher({"https://example.test/x": _ok("https://example.test/x", "<html/>")})
    report = sync_vault(conn, settings, _as_fetcher(fetcher))

    assert report.tombstones_respected == 1
    assert fetcher.calls == []
    assert is_tombstoned(conn, "obsidian", "a.md#https://example.test/x") is True


def test_error_after_cap_exhausted_mid_extraction_is_skipped_not_failed(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    _write(vault, "a.md", "https://example.test/x")
    conn = _open(tmp_path)
    settings = _settings(tmp_path, vault, max_fetches_per_run=1)
    fetcher = FakeFetcher(max_fetches_per_run=1)

    def fake_extract(url: str, fetcher: Fetcher) -> Extracted:
        # A multi-hop extractor (shorteners, TikTok) spends its first hop, then the cap catches
        # the next one: `spent` climbs past the cap during the call, same as the real thing.
        cast(FakeFetcher, fetcher).spent += 1
        return Extracted(url=url, title=None, author=None, content=None, extra={}, error="boom")

    monkeypatch.setattr(sync, "extract", fake_extract)

    report = sync_vault(conn, settings, _as_fetcher(fetcher))

    assert report.urls_skipped == 1
    assert report.urls_failed == 0
    row = conn.execute(
        "SELECT status FROM documents WHERE source_ref = 'a.md#https://example.test/x'"
    ).fetchone()
    assert row["status"] == "pending"


def test_skipped_fetch_leaves_pending_and_next_run_picks_it_up(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write(vault, "a.md", "https://example.test/x")
    conn = _open(tmp_path)
    settings = _settings(tmp_path, vault, max_fetches_per_run=1)
    capped_fetcher = FakeFetcher(spent=1, max_fetches_per_run=1)

    report = sync_vault(conn, settings, _as_fetcher(capped_fetcher))

    assert report.urls_skipped == 1
    assert report.urls_fetched == 0
    assert capped_fetcher.calls == []
    row = conn.execute(
        "SELECT status FROM documents WHERE source_ref = 'a.md#https://example.test/x'"
    ).fetchone()
    assert row["status"] == "pending"

    fresh_fetcher = FakeFetcher(
        {"https://example.test/x": _ok("https://example.test/x", "<html/>")}
    )
    second_report = sync_vault(conn, settings, _as_fetcher(fresh_fetcher))

    assert second_report.urls_fetched == 1
    row = conn.execute(
        "SELECT status FROM documents WHERE source_ref = 'a.md#https://example.test/x'"
    ).fetchone()
    assert row["status"] == "ok"


def test_failed_fetch_records_error_status_and_does_not_abort_run(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write(vault, "a.md", "https://example.test/broken")
    _write(vault, "b.md", "no links here")
    conn = _open(tmp_path)
    settings = _settings(tmp_path, vault)
    fetcher = FakeFetcher(
        {"https://example.test/broken": _failed("https://example.test/broken", "boom")}
    )

    report = sync_vault(conn, settings, _as_fetcher(fetcher))

    assert report.urls_failed == 1
    assert report.notes_added == 2
    row = conn.execute(
        "SELECT status, error FROM documents WHERE source_ref = 'a.md#https://example.test/broken'"
    ).fetchone()
    assert row["status"] == "failed"
    assert row["error"] == "boom"
    assert conn.execute("SELECT * FROM documents WHERE source_ref = 'b.md'").fetchone() is not None


def test_follow_urls_false_indexes_note_text_only(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write(vault, "a.md", "https://example.test/x")
    conn = _open(tmp_path)
    settings = _settings(tmp_path, vault)
    fetcher = FakeFetcher()

    report = sync_vault(conn, settings, _as_fetcher(fetcher), follow_urls=False)

    assert report.urls_seen == 0
    assert fetcher.calls == []
    assert (
        conn.execute("SELECT * FROM documents WHERE parent_document_id IS NOT NULL").fetchone()
        is None
    )
    note = conn.execute("SELECT * FROM documents WHERE source_ref = 'a.md'").fetchone()
    assert note is not None
    assert note["content"] == "https://example.test/x"


def test_purging_a_note_purges_its_url_children_too(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    path = _write(vault, "a.md", "https://example.test/x")
    conn = _open(tmp_path)
    settings = _settings(tmp_path, vault)
    sync_vault(
        conn,
        settings,
        _as_fetcher(
            FakeFetcher({"https://example.test/x": _ok("https://example.test/x", "<html/>")})
        ),
    )
    path.unlink()

    report = sync_vault(conn, settings, _as_fetcher(FakeFetcher()))

    assert report.notes_purged == 1
    assert report.urls_orphaned == 1
    assert (
        conn.execute(
            "SELECT * FROM documents WHERE source_ref = 'a.md#https://example.test/x'"
        ).fetchone()
        is None
    )


def test_urls_orphaned_does_not_count_a_tombstoned_child(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    path = _write(vault, "a.md", "https://example.test/x")
    conn = _open(tmp_path)
    settings = _settings(tmp_path, vault)
    sync_vault(
        conn,
        settings,
        _as_fetcher(
            FakeFetcher({"https://example.test/x": _ok("https://example.test/x", "<html/>")})
        ),
    )
    child_id = conn.execute(
        "SELECT id FROM documents WHERE source_ref = 'a.md#https://example.test/x'"
    ).fetchone()["id"]
    tombstone(conn, child_id)
    path.unlink()

    report = sync_vault(conn, settings, _as_fetcher(FakeFetcher()))

    assert report.notes_purged == 1
    assert report.urls_orphaned == 0
    assert is_tombstoned(conn, "obsidian", "a.md#https://example.test/x") is True


def test_renamed_note_leaves_no_unreachable_children(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    path = _write(vault, "a.md", "https://example.test/x")
    conn = _open(tmp_path)
    settings = _settings(tmp_path, vault)
    sync_vault(
        conn,
        settings,
        _as_fetcher(
            FakeFetcher({"https://example.test/x": _ok("https://example.test/x", "<html/>")})
        ),
    )
    path.rename(vault / "b.md")

    report = sync_vault(
        conn,
        settings,
        _as_fetcher(
            FakeFetcher({"https://example.test/x": _ok("https://example.test/x", "<html/>")})
        ),
    )

    assert report.notes_purged == 1
    assert report.urls_orphaned == 1
    assert (
        conn.execute(
            "SELECT * FROM documents WHERE source_ref = 'a.md#https://example.test/x'"
        ).fetchone()
        is None
    )
    assert (
        conn.execute(
            "SELECT * FROM documents WHERE source_ref = 'b.md#https://example.test/x'"
        ).fetchone()
        is not None
    )


def test_url_removed_from_note_purges_child_and_its_fts_entry(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    path = _write(vault, "a.md", "https://example.test/x")
    conn = _open(tmp_path)
    settings = _settings(tmp_path, vault)
    sync_vault(
        conn,
        settings,
        _as_fetcher(
            FakeFetcher(
                {
                    "https://example.test/x": _ok(
                        "https://example.test/x", "<html><body>uniquewordxyz</body></html>"
                    )
                }
            )
        ),
    )
    assert (
        conn.execute(
            "SELECT rowid FROM documents_fts WHERE documents_fts MATCH 'uniquewordxyz'"
        ).fetchall()
        != []
    )
    path.write_text("no links anymore")

    report = sync_vault(conn, settings, _as_fetcher(FakeFetcher()))

    assert report.urls_removed == 1
    assert (
        conn.execute(
            "SELECT * FROM documents WHERE source_ref = 'a.md#https://example.test/x'"
        ).fetchone()
        is None
    )
    assert (
        conn.execute(
            "SELECT rowid FROM documents_fts WHERE documents_fts MATCH 'uniquewordxyz'"
        ).fetchall()
        == []
    )


def test_url_removed_from_note_respects_a_tombstoned_child(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    path = _write(vault, "a.md", "https://example.test/x")
    conn = _open(tmp_path)
    settings = _settings(tmp_path, vault)
    sync_vault(
        conn,
        settings,
        _as_fetcher(
            FakeFetcher({"https://example.test/x": _ok("https://example.test/x", "<html/>")})
        ),
    )
    child_id = conn.execute(
        "SELECT id FROM documents WHERE source_ref = 'a.md#https://example.test/x'"
    ).fetchone()["id"]
    tombstone(conn, child_id)
    path.write_text("no links anymore")

    report = sync_vault(conn, settings, _as_fetcher(FakeFetcher()))

    assert report.urls_removed == 0
    assert is_tombstoned(conn, "obsidian", "a.md#https://example.test/x") is True


def test_one_raising_note_does_not_prevent_later_notes_from_syncing(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    _write(vault, "bad.md", "this one blows up")
    _write(vault, "good.md", "this one is fine")
    conn = _open(tmp_path)
    settings = _settings(tmp_path, vault)
    real_upsert_note = sync._upsert_note

    def maybe_raise(conn: sqlite3.Connection, note: VaultNote, report: SyncReport) -> int:
        if note.relative_path == "bad.md":
            raise RuntimeError("boom")
        return real_upsert_note(conn, note, report)

    monkeypatch.setattr(sync, "_upsert_note", maybe_raise)

    report = sync_vault(conn, settings, _as_fetcher(FakeFetcher()))

    assert report.notes_seen == 2
    assert (
        conn.execute("SELECT * FROM documents WHERE source_ref = 'good.md'").fetchone() is not None
    )
    assert conn.execute("SELECT * FROM documents WHERE source_ref = 'bad.md'").fetchone() is None


def test_sync_report_counts_match_operations_performed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write(vault, "unchanged.md", "nothing to see here")
    _write(vault, "will_update.md", "version one")
    _write(vault, "will_delete.md", "temporary")
    _write(vault, "will_tombstone.md", "to be tombstoned")
    _write(
        vault,
        "with_urls.md",
        "ok: https://example.test/ok fail: https://example.test/fail",
    )
    conn = _open(tmp_path)
    settings = _settings(tmp_path, vault)
    sync_vault(
        conn,
        settings,
        _as_fetcher(
            FakeFetcher(
                {
                    "https://example.test/ok": _ok("https://example.test/ok", "<html/>"),
                    "https://example.test/fail": _failed("https://example.test/fail", "boom"),
                }
            )
        ),
    )
    tombstone_id = conn.execute(
        "SELECT id FROM documents WHERE source_ref = 'will_tombstone.md'"
    ).fetchone()["id"]
    tombstone(conn, tombstone_id)
    (vault / "will_delete.md").unlink()
    (vault / "will_update.md").write_text("version two")
    _write(vault, "brand_new.md", "new: https://example.test/new")

    capped_settings = _settings(tmp_path, vault, max_fetches_per_run=1)
    capped_fetcher = FakeFetcher(spent=1, max_fetches_per_run=1)

    report = sync_vault(conn, capped_settings, _as_fetcher(capped_fetcher))

    assert capped_fetcher.calls == []
    assert report == SyncReport(
        notes_seen=5,
        notes_added=1,
        notes_updated=1,
        notes_purged=1,
        urls_seen=3,
        urls_fetched=0,
        urls_cached=1,
        urls_failed=0,
        urls_skipped=2,
        urls_removed=0,
        tombstones_respected=1,
        urls_orphaned=0,
    )


# --- ingest_url ---


def test_ingest_url_success_stores_an_ok_manual_document(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    fetcher = FakeFetcher(
        {"https://example.test/x": _ok("https://example.test/x", "<html><body>hi</body></html>")}
    )

    extracted = ingest_url(conn, "https://example.test/x", _as_fetcher(fetcher))

    assert extracted.error is None
    row = conn.execute(
        "SELECT status, content FROM documents WHERE source = 'manual' AND source_ref = ?",
        ("https://example.test/x",),
    ).fetchone()
    assert row["status"] == "ok"
    assert row["content"] == "hi"


def test_ingest_url_failure_stores_a_failed_manual_document(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    fetcher = FakeFetcher({"https://example.test/x": _failed("https://example.test/x", "boom")})

    extracted = ingest_url(conn, "https://example.test/x", _as_fetcher(fetcher))

    assert extracted.error == "boom"
    row = conn.execute(
        "SELECT status, error FROM documents WHERE source = 'manual' AND source_ref = ?",
        ("https://example.test/x",),
    ).fetchone()
    assert row["status"] == "failed"
    assert row["error"] == "boom"


def test_ingest_url_cap_skip_leaves_the_row_pending_not_failed(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    conn = _open(tmp_path)
    fetcher = FakeFetcher(max_fetches_per_run=1)

    def fake_extract(url: str, fetcher: Fetcher) -> Extracted:
        # A multi-hop extractor spends its first hop, then the cap catches the next one:
        # `spent` climbs past the cap during the call, same as the real thing.
        cast(FakeFetcher, fetcher).spent += 1
        return Extracted(url=url, title=None, author=None, content=None, extra={}, error="boom")

    monkeypatch.setattr(sync, "extract", fake_extract)

    extracted = ingest_url(conn, "https://example.test/x", _as_fetcher(fetcher))

    assert extracted.error == "boom"
    row = conn.execute(
        "SELECT status FROM documents WHERE source = 'manual' AND source_ref = ?",
        ("https://example.test/x",),
    ).fetchone()
    assert row["status"] == "pending"
