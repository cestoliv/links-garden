import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

from _pytest.monkeypatch import MonkeyPatch

from links_garden import signal_sync
from links_garden.beeper import BeeperClient, Message
from links_garden.config import Settings
from links_garden.db import connect, initialize, is_tombstoned
from links_garden.fetch import Fetcher, FetchResult
from links_garden.signal_sync import SignalReport, sync_signal


@dataclass
class _FetchCall:
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
        self.calls: list[_FetchCall] = []

    @property
    def at_cap(self) -> bool:
        return self.spent >= self._max_fetches_per_run

    def fetch(self, url: str, *, force_direct: bool = False) -> FetchResult:
        self.calls.append(_FetchCall(url=url, force_direct=force_direct))
        if url not in self._responses:
            raise AssertionError(f"unexpected fetch: {url}")
        self.spent += 1
        return self._responses[url]


@dataclass
class _Reaction:
    chat_id: str
    message_id: str


class FakeBeeper:
    """Canned, in-memory stand-in for `BeeperClient`. Records every reaction; never touches
    Signal.
    """

    def __init__(
        self,
        messages: list[Message],
        *,
        failing_reactions: frozenset[str] = frozenset(),
    ) -> None:
        self._messages = messages
        self._failing_reactions = failing_reactions
        self.reactions: list[_Reaction] = []

    def iter_messages(self, chat_id: str, *, since: date | None = None) -> Iterator[Message]:
        yield from self._messages

    def add_reaction(self, chat_id: str, message_id: str, key: str = "✅") -> bool:
        self.reactions.append(_Reaction(chat_id=chat_id, message_id=message_id))
        return message_id not in self._failing_reactions

    def check(self) -> bool:
        return True


class _OrderCheckingBeeper(FakeBeeper):
    """Records, at the moment a reaction is requested, whether the URL's row already committed."""

    def __init__(self, conn: sqlite3.Connection, messages: list[Message], source_ref: str) -> None:
        super().__init__(messages)
        self._conn = conn
        self._source_ref = source_ref
        self.row_was_ok_at_reaction_time: bool | None = None

    def add_reaction(self, chat_id: str, message_id: str, key: str = "✅") -> bool:
        row = self._conn.execute(
            "SELECT status FROM documents WHERE source_ref = ?", (self._source_ref,)
        ).fetchone()
        self.row_was_ok_at_reaction_time = row is not None and row["status"] == "ok"
        return super().add_reaction(chat_id, message_id, key)


def _as_fetcher(fake: FakeFetcher) -> Fetcher:
    return cast(Fetcher, fake)


def _as_beeper(fake: FakeBeeper) -> BeeperClient:
    return cast(BeeperClient, fake)


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


def _settings(
    tmp_path: Path,
    *,
    max_fetches_per_run: int = 50,
    backfill_start_date: date | None = None,
) -> Settings:
    return Settings(
        _env_file=None,
        beeper_chat_id="chat-1",
        fetch_cache_dir=tmp_path / "cache",
        max_fetches_per_run=max_fetches_per_run,
        backfill_start_date=backfill_start_date,
    )


def _message(message_id: str, text: str) -> Message:
    return Message(
        id=message_id,
        text=text,
        timestamp=datetime(2026, 9, 1, tzinfo=UTC),
        is_sender=True,
        sender_name=None,
    )


# --- 1. one document per URL, with the documented source_ref ---


def test_message_with_link_produces_a_document_with_the_documented_source_ref(
    tmp_path: Path,
) -> None:
    conn = _open(tmp_path)
    url = "https://example.test/x"
    beeper = FakeBeeper([_message("m1", url)])
    fetcher = FakeFetcher({url: _ok(url, "<html/>")})

    report = sync_signal(conn, _settings(tmp_path), _as_fetcher(fetcher), _as_beeper(beeper))

    assert report.urls_seen == 1
    row = conn.execute(
        "SELECT * FROM documents WHERE source = 'signal' AND source_ref = ?", (f"m1#{url}",)
    ).fetchone()
    assert row is not None
    assert row["url"] == url
    assert row["parent_document_id"] is None
    assert row["status"] == "ok"


# --- 2. a message with no URL produces nothing and is not reacted to ---


def test_message_without_a_url_produces_nothing_and_is_not_reacted_to(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    beeper = FakeBeeper([_message("m1", "just chatting, no links here")])

    report = sync_signal(conn, _settings(tmp_path), _as_fetcher(FakeFetcher()), _as_beeper(beeper))

    assert report.messages_with_links == 0
    assert beeper.reactions == []
    assert conn.execute("SELECT * FROM documents").fetchone() is None


# --- 3. message_text holds the commentary with the URL stripped out ---


def test_message_text_holds_commentary_with_the_url_stripped_out(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    url = "https://example.test/x"
    beeper = FakeBeeper([_message("m1", f"check this out {url} so good")])
    fetcher = FakeFetcher({url: _ok(url, "<html/>")})

    sync_signal(conn, _settings(tmp_path), _as_fetcher(fetcher), _as_beeper(beeper))

    row = conn.execute(
        "SELECT message_text FROM documents WHERE source_ref = ?", (f"m1#{url}",)
    ).fetchone()
    assert row["message_text"] == "check this out so good"


# --- 4. two URLs produce two documents and exactly one reaction ---


def test_message_with_two_urls_produces_two_documents_and_one_reaction(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    url_a, url_b = "https://example.test/a", "https://example.test/b"
    beeper = FakeBeeper([_message("m1", f"{url_a} {url_b}")])
    fetcher = FakeFetcher({url_a: _ok(url_a, "<html/>"), url_b: _ok(url_b, "<html/>")})

    report = sync_signal(conn, _settings(tmp_path), _as_fetcher(fetcher), _as_beeper(beeper))

    assert report.urls_seen == 2
    assert report.reactions_added == 1
    assert len(beeper.reactions) == 1
    rows = conn.execute("SELECT source_ref FROM documents WHERE source = 'signal'").fetchall()
    assert {row["source_ref"] for row in rows} == {f"m1#{url_a}", f"m1#{url_b}"}


# --- 5. the reaction happens only after the URLs commit ---


def test_reaction_happens_only_after_the_urls_commit(tmp_path: Path) -> None:
    db_path = tmp_path / "garden.db"
    conn = connect(db_path)
    initialize(conn)
    # A second connection to the same file sees only committed writes, unlike `conn` itself,
    # which would see its own uncommitted ones and prove nothing about commit ordering.
    reader = connect(db_path)
    url = "https://example.test/x"
    source_ref = f"m1#{url}"
    beeper = _OrderCheckingBeeper(reader, [_message("m1", url)], source_ref)
    fetcher = FakeFetcher({url: _ok(url, "<html/>")})

    sync_signal(conn, _settings(tmp_path), _as_fetcher(fetcher), _as_beeper(beeper))

    assert beeper.row_was_ok_at_reaction_time is True


# --- 6. a failed reaction increments reactions_failed and does not stop the run ---


def test_failed_reaction_increments_reactions_failed_and_does_not_stop_the_run(
    tmp_path: Path,
) -> None:
    conn = _open(tmp_path)
    url_a, url_b = "https://example.test/a", "https://example.test/b"
    beeper = FakeBeeper(
        [_message("m1", url_a), _message("m2", url_b)], failing_reactions=frozenset({"m1"})
    )
    fetcher = FakeFetcher({url_a: _ok(url_a, "<html/>"), url_b: _ok(url_b, "<html/>")})

    report = sync_signal(conn, _settings(tmp_path), _as_fetcher(fetcher), _as_beeper(beeper))

    assert report.reactions_failed == 1
    assert report.reactions_added == 1
    assert (
        conn.execute("SELECT * FROM documents WHERE source_ref = ?", (f"m2#{url_b}",)).fetchone()
        is not None
    )


# --- 7. follow_urls=False performs no reaction at all ---


def test_follow_urls_false_performs_no_reaction_at_all(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    url = "https://example.test/x"
    beeper = FakeBeeper([_message("m1", url)])
    fetcher = FakeFetcher()

    report = sync_signal(
        conn, _settings(tmp_path), _as_fetcher(fetcher), _as_beeper(beeper), follow_urls=False
    )

    assert beeper.reactions == []
    assert report.reactions_added == 0
    assert fetcher.calls == []
    assert conn.execute("SELECT * FROM documents").fetchone() is None


# --- 8. a tombstoned source_ref is skipped, counted, and never fetched ---


def test_tombstoned_source_ref_is_skipped_counted_and_never_fetched(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    url = "https://example.test/x"
    source_ref = f"m1#{url}"
    conn.execute(
        "INSERT INTO documents (source, source_ref, url, status, deleted_at) "
        "VALUES ('signal', ?, ?, 'ok', datetime('now'))",
        (source_ref, url),
    )
    conn.commit()
    beeper = FakeBeeper([_message("m1", url)])
    fetcher = FakeFetcher()

    report = sync_signal(conn, _settings(tmp_path), _as_fetcher(fetcher), _as_beeper(beeper))

    assert report.tombstones_respected == 1
    assert fetcher.calls == []
    assert is_tombstoned(conn, "signal", source_ref) is True


# --- 9. a URL already ok is not re-fetched ---


def test_url_already_ok_is_not_refetched(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    settings = _settings(tmp_path)
    url = "https://example.test/x"
    sync_signal(
        conn,
        settings,
        _as_fetcher(FakeFetcher({url: _ok(url, "<html/>")})),
        _as_beeper(FakeBeeper([_message("m1", url)])),
    )

    fetcher = FakeFetcher({url: _ok(url, "<html/>")})
    report = sync_signal(
        conn, settings, _as_fetcher(fetcher), _as_beeper(FakeBeeper([_message("m1", url)]))
    )

    assert report.urls_cached == 1
    assert report.urls_fetched == 0
    assert fetcher.calls == []


# --- 10. a cap-skipped URL is left pending, never failed ---


def test_cap_skipped_url_is_left_pending_never_failed(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    url = "https://example.test/x"
    beeper = FakeBeeper([_message("m1", url)])
    fetcher = FakeFetcher(spent=1, max_fetches_per_run=1)

    report = sync_signal(
        conn, _settings(tmp_path, max_fetches_per_run=1), _as_fetcher(fetcher), _as_beeper(beeper)
    )

    assert report.urls_skipped == 1
    assert report.urls_failed == 0
    row = conn.execute(
        "SELECT status FROM documents WHERE source_ref = ?", (f"m1#{url}",)
    ).fetchone()
    assert row["status"] == "pending"
    assert beeper.reactions == []  # the item never committed a terminal outcome


# --- 11. a failed fetch records failed and the run continues to later messages ---


def test_failed_fetch_records_failed_and_run_continues_to_later_messages(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    broken, good = "https://example.test/broken", "https://example.test/good"
    beeper = FakeBeeper([_message("m1", broken), _message("m2", good)])
    fetcher = FakeFetcher({broken: _failed(broken, "boom"), good: _ok(good, "<html/>")})

    report = sync_signal(conn, _settings(tmp_path), _as_fetcher(fetcher), _as_beeper(beeper))

    assert report.urls_failed == 1
    row = conn.execute(
        "SELECT status, error FROM documents WHERE source_ref = ?", (f"m1#{broken}",)
    ).fetchone()
    assert row["status"] == "failed"
    assert row["error"] == "boom"
    assert (
        conn.execute("SELECT * FROM documents WHERE source_ref = ?", (f"m2#{good}",)).fetchone()
        is not None
    )


# --- 12. one message raising does not prevent later messages from syncing ---


def test_one_raising_message_does_not_prevent_later_messages_from_syncing(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    conn = _open(tmp_path)
    bad_url, good_url = "https://example.test/bad", "https://example.test/good"
    beeper = FakeBeeper([_message("bad", bad_url), _message("good", good_url)])
    fetcher = FakeFetcher({good_url: _ok(good_url, "<html/>")})
    real_sync_message = signal_sync._sync_message

    def maybe_raise(
        conn: sqlite3.Connection,
        message: Message,
        fetcher: Fetcher,
        beeper: BeeperClient,
        chat_id: str,
        *,
        follow_urls: bool,
        report: SignalReport,
    ) -> None:
        if message.id == "bad":
            raise RuntimeError("boom")
        real_sync_message(
            conn, message, fetcher, beeper, chat_id, follow_urls=follow_urls, report=report
        )

    monkeypatch.setattr(signal_sync, "_sync_message", maybe_raise)

    report = sync_signal(conn, _settings(tmp_path), _as_fetcher(fetcher), _as_beeper(beeper))

    assert report.messages_seen == 2
    assert (
        conn.execute(
            "SELECT * FROM documents WHERE source_ref = ?", (f"good#{good_url}",)
        ).fetchone()
        is not None
    )
    assert (
        conn.execute("SELECT * FROM documents WHERE source_ref = ?", (f"bad#{bad_url}",)).fetchone()
        is None
    )


# --- 13. re-running ingests nothing new and adds no second reaction ---


def test_rerunning_ingests_nothing_new_and_adds_no_second_reaction(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    settings = _settings(tmp_path)
    url = "https://example.test/x"
    first_report = sync_signal(
        conn,
        settings,
        _as_fetcher(FakeFetcher({url: _ok(url, "<html/>")})),
        _as_beeper(FakeBeeper([_message("m1", url)])),
    )
    assert first_report.reactions_added == 1

    fetcher = FakeFetcher({url: _ok(url, "<html/>")})
    beeper = FakeBeeper([_message("m1", url)])
    second_report = sync_signal(conn, settings, _as_fetcher(fetcher), _as_beeper(beeper))

    assert second_report.urls_cached == 1
    assert fetcher.calls == []
    assert second_report.reactions_added == 0
    assert beeper.reactions == []


# --- 14. SignalReport counts match the operations performed, field by field ---


def test_signal_report_counts_match_operations_performed(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    ok_url = "https://example.test/ok"
    failed_url = "https://example.test/failed"
    tombstoned_url = "https://example.test/tombstoned"
    tombstoned_ref = f"tomb#{tombstoned_url}"
    conn.execute(
        "INSERT INTO documents (source, source_ref, url, status, deleted_at) "
        "VALUES ('signal', ?, ?, 'ok', datetime('now'))",
        (tombstoned_ref, tombstoned_url),
    )
    conn.commit()
    beeper = FakeBeeper(
        [
            _message("no_links", "nothing to see here"),
            _message("ok", ok_url),
            _message("failed", failed_url),
            _message("tomb", tombstoned_url),
        ]
    )
    fetcher = FakeFetcher({ok_url: _ok(ok_url, "<html/>"), failed_url: _failed(failed_url, "boom")})

    report = sync_signal(conn, _settings(tmp_path), _as_fetcher(fetcher), _as_beeper(beeper))

    assert report == SignalReport(
        messages_seen=4,
        messages_with_links=3,
        urls_seen=3,
        urls_fetched=1,
        urls_cached=0,
        urls_failed=1,
        urls_skipped=0,
        tombstones_respected=1,
        reactions_added=1,  # only "ok" reached status='ok'; "failed" and "tomb" earn nothing
        reactions_failed=0,
    )


# --- fix round 1 regressions: dedup via signal_reactions, react only when captured ---


def test_message_whose_only_url_fails_is_never_reacted_to(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    url = "https://example.test/bad"
    beeper = FakeBeeper([_message("m1", url)])
    fetcher = FakeFetcher({url: _failed(url, "boom")})

    report = sync_signal(conn, _settings(tmp_path), _as_fetcher(fetcher), _as_beeper(beeper))

    assert report.reactions_added == 0
    assert report.reactions_failed == 0
    assert beeper.reactions == []


def test_permanently_failing_url_is_never_reacted_to_across_reruns(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    settings = _settings(tmp_path)
    url = "https://example.test/bad"

    for _ in range(2):
        beeper = FakeBeeper([_message("m1", url)])
        report = sync_signal(
            conn,
            settings,
            _as_fetcher(FakeFetcher({url: _failed(url, "boom")})),
            _as_beeper(beeper),
        )
        assert report.reactions_added == 0
        assert beeper.reactions == []


def test_two_url_message_with_one_ok_and_one_perma_failing_reacts_once_not_again(
    tmp_path: Path,
) -> None:
    conn = _open(tmp_path)
    settings = _settings(tmp_path)
    ok_url, bad_url = "https://example.test/ok", "https://example.test/bad"
    text = f"{ok_url} {bad_url}"

    beeper1 = FakeBeeper([_message("m1", text)])
    fetcher1 = FakeFetcher({ok_url: _ok(ok_url, "<html/>"), bad_url: _failed(bad_url, "boom")})
    report1 = sync_signal(conn, settings, _as_fetcher(fetcher1), _as_beeper(beeper1))
    assert report1.reactions_added == 1
    assert len(beeper1.reactions) == 1

    beeper2 = FakeBeeper([_message("m1", text)])
    fetcher2 = FakeFetcher({bad_url: _failed(bad_url, "boom")})  # ok_url is cached, never refetched
    report2 = sync_signal(conn, settings, _as_fetcher(fetcher2), _as_beeper(beeper2))
    assert report2.reactions_added == 0
    assert beeper2.reactions == []


def test_reaction_that_failed_is_retried_on_the_next_run(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    settings = _settings(tmp_path)
    url = "https://example.test/x"

    beeper1 = FakeBeeper([_message("m1", url)], failing_reactions=frozenset({"m1"}))
    report1 = sync_signal(
        conn, settings, _as_fetcher(FakeFetcher({url: _ok(url, "<html/>")})), _as_beeper(beeper1)
    )
    assert report1.reactions_failed == 1
    assert report1.reactions_added == 0

    beeper2 = FakeBeeper([_message("m1", url)])
    report2 = sync_signal(
        conn, settings, _as_fetcher(FakeFetcher({url: _ok(url, "<html/>")})), _as_beeper(beeper2)
    )
    assert report2.reactions_added == 1
    assert len(beeper2.reactions) == 1
