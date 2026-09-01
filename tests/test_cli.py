"""CLI-level tests.

`sync_vault`, `sync_signal` and `ingest_url` themselves are covered in test_sync.py and
test_signal_sync.py; this file only covers logic that lives in cli.py: flag wiring, the
credits-banner guard, the sync-signal fail-fast checks, the ingest exit code, and the
cache-clear confirmation gate.
"""

import sqlite3
import sys
from pathlib import Path
from typing import cast

import pytest
from _pytest.monkeypatch import MonkeyPatch

from links_garden import cli
from links_garden.adapters import Extracted
from links_garden.beeper import BeeperClient
from links_garden.config import Settings
from links_garden.embed import Embedder, IndexReport
from links_garden.fetch import Fetcher


def test_sync_vault_no_follow_urls_flag_parses_true() -> None:
    args = cli._build_parser().parse_args(["sync-vault", "--no-follow-urls"])
    assert args.no_follow_urls is True


def test_sync_vault_defaults_to_following_urls() -> None:
    args = cli._build_parser().parse_args(["sync-vault"])
    assert args.no_follow_urls is False


def _settings(
    tmp_path: Path,
    cache_dir: Path | None = None,
    *,
    beeper_access_token: str = "",
    beeper_chat_id: str = "",
) -> Settings:
    return Settings(
        _env_file=None,
        fetch_cache_dir=cache_dir or tmp_path / "cache",
        max_fetches_per_run=5,
        beeper_access_token=beeper_access_token,  # type: ignore[arg-type]
        beeper_chat_id=beeper_chat_id,
    )


class _CheckOnlyBeeper:
    """Stand-in for `BeeperClient` covering only `check()`. Any other call is a test bug."""

    def __init__(self, *, check_result: bool) -> None:
        self._check_result = check_result

    def check(self) -> bool:
        return self._check_result


def test_cmd_sync_signal_fails_fast_when_access_token_is_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = _settings(tmp_path, beeper_access_token="", beeper_chat_id="chat-1")
    beeper = cast(BeeperClient, _CheckOnlyBeeper(check_result=True))

    exit_code = cli._cmd_sync_signal(
        cast(sqlite3.Connection, None),
        settings,
        cast(Fetcher, _CreditlessFetcher()),
        beeper,
        follow_urls=True,
    )

    assert exit_code == 1
    assert "BEEPER_ACCESS_TOKEN" in capsys.readouterr().err


def test_cmd_sync_signal_fails_fast_when_chat_id_is_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = _settings(tmp_path, beeper_access_token="test-token", beeper_chat_id="")
    beeper = cast(BeeperClient, _CheckOnlyBeeper(check_result=True))

    exit_code = cli._cmd_sync_signal(
        cast(sqlite3.Connection, None),
        settings,
        cast(Fetcher, _CreditlessFetcher()),
        beeper,
        follow_urls=True,
    )

    assert exit_code == 1
    assert "BEEPER_CHAT_ID" in capsys.readouterr().err


def test_cmd_sync_signal_fails_fast_when_beeper_is_unreachable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = _settings(tmp_path, beeper_access_token="test-token", beeper_chat_id="chat-1")
    beeper = cast(BeeperClient, _CheckOnlyBeeper(check_result=False))

    exit_code = cli._cmd_sync_signal(
        cast(sqlite3.Connection, None),
        settings,
        cast(Fetcher, _CreditlessFetcher()),
        beeper,
        follow_urls=True,
    )

    assert exit_code == 1
    assert "not reachable" in capsys.readouterr().err


class _RaisingFetcher:
    def remaining_credits(self) -> int | None:
        raise RuntimeError("offline")


def test_fetch_cost_banner_prints_unknown_instead_of_raising(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    cli._print_fetch_cost(_settings(tmp_path), cast(Fetcher, _RaisingFetcher()))

    assert "remaining credits: unknown" in capsys.readouterr().out
    # Being offline is routine, not exceptional: no traceback, just the reason as text. A
    # `logger.exception`/`exc_info=True` call here would dump a traceback in front of the sync
    # report that follows, making a normal condition look like a crash.
    (record,) = caplog.records
    assert record.exc_info is None
    assert "offline" in record.getMessage()


class _CreditlessFetcher:
    def remaining_credits(self) -> int | None:
        return None


def test_cmd_ingest_returns_zero_on_success(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        cli,
        "ingest_url",
        lambda conn, url, fetcher: Extracted(
            url=url, title="t", author=None, content="body", extra={}, error=None
        ),
    )

    exit_code = cli._cmd_ingest(
        cast(sqlite3.Connection, None),
        _settings(tmp_path),
        cast(Fetcher, _CreditlessFetcher()),
        "https://example.test/x",
    )

    assert exit_code == 0
    assert "ok" in capsys.readouterr().out


def test_cmd_ingest_returns_nonzero_on_failure(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        cli,
        "ingest_url",
        lambda conn, url, fetcher: Extracted(
            url=url, title=None, author=None, content=None, extra={}, error="boom"
        ),
    )

    exit_code = cli._cmd_ingest(
        cast(sqlite3.Connection, None),
        _settings(tmp_path),
        cast(Fetcher, _CreditlessFetcher()),
        "https://example.test/x",
    )

    assert exit_code == 1
    assert "boom" in capsys.readouterr().err


class _CheckOnlyEmbedder:
    """Stand-in for `Embedder` covering only `check()`. Any other call is a test bug."""

    def check(self) -> bool:
        return True


def test_cmd_index_returns_zero_when_nothing_failed(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        cli, "index_documents", lambda conn, settings, embedder: IndexReport(documents_indexed=2)
    )

    exit_code = cli._cmd_index(
        cast(sqlite3.Connection, None), _settings(tmp_path), cast(Embedder, _CheckOnlyEmbedder())
    )

    assert exit_code == 0


def test_cmd_index_returns_nonzero_when_documents_failed(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A dead embedding backend mid-run must fail the cron job loudly, unlike a sync's
    # urls_failed: a dead link is expected, but a broken embedder means search now answers
    # wrong with nothing to show for it.
    monkeypatch.setattr(
        cli, "index_documents", lambda conn, settings, embedder: IndexReport(documents_failed=3)
    )

    exit_code = cli._cmd_index(
        cast(sqlite3.Connection, None), _settings(tmp_path), cast(Embedder, _CheckOnlyEmbedder())
    )

    assert exit_code == 1


def _write_cache_entry(cache_dir: Path, name: str, status: str) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / name).write_text(f'{{"status": "{status}"}}')


def test_cache_clear_all_without_yes_refuses_when_not_interactive(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    cache_dir = tmp_path / "cache"
    _write_cache_entry(cache_dir, "a.json", "ok")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    exit_code = cli._cmd_cache_clear(
        _settings(tmp_path, cache_dir),
        failed=False,
        all_=True,
        yes=False,
    )

    assert exit_code == 1
    assert (cache_dir / "a.json").exists()


def test_cache_clear_all_with_yes_skips_the_prompt(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    _write_cache_entry(cache_dir, "a.json", "ok")
    _write_cache_entry(cache_dir, "b.json", "failed")

    exit_code = cli._cmd_cache_clear(
        _settings(tmp_path, cache_dir),
        failed=False,
        all_=True,
        yes=True,
    )

    assert exit_code == 0
    assert list(cache_dir.glob("*.json")) == []


def test_cache_clear_all_interactive_confirmation_accepted(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    cache_dir = tmp_path / "cache"
    _write_cache_entry(cache_dir, "a.json", "ok")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")

    exit_code = cli._cmd_cache_clear(
        _settings(tmp_path, cache_dir),
        failed=False,
        all_=True,
        yes=False,
    )

    assert exit_code == 0
    assert list(cache_dir.glob("*.json")) == []
