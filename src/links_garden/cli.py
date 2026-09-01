"""`garden` command-line entry point. Standard library `argparse` only."""

import argparse
import dataclasses
import json
import logging
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from links_garden.config import Settings, load_settings
from links_garden.db import connect, initialize
from links_garden.fetch import Fetcher
from links_garden.sync import SyncReport, ingest_url, sync_vault

logger = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    settings = load_settings()
    conn = connect(settings.database_path)
    initialize(conn)
    fetcher = Fetcher(settings)

    if args.command == "sync-vault":
        return _cmd_sync_vault(conn, settings, fetcher, follow_urls=not args.no_follow_urls)
    if args.command == "credits":
        return _cmd_credits(fetcher)
    if args.command == "ingest":
        return _cmd_ingest(conn, settings, fetcher, args.url)
    return _cmd_cache_clear(settings, failed=args.failed, all_=args.all, yes=args.yes)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="garden")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser("sync-vault", help="sync the Obsidian vault into the store")
    sync_parser.add_argument(
        "--no-follow-urls",
        action="store_true",
        help="index note text only; skip fetching URLs found in notes",
    )
    subparsers.add_parser("credits", help="print remaining Firecrawl credits")

    ingest_parser = subparsers.add_parser("ingest", help="fetch and store one URL")
    ingest_parser.add_argument("url")

    cache_parser = subparsers.add_parser("cache", help="manage the fetch cache")
    cache_subparsers = cache_parser.add_subparsers(dest="cache_command", required=True)
    clear_parser = cache_subparsers.add_parser("clear", help="remove cached fetch results")
    scope = clear_parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--failed", action="store_true", help="remove only cached failures")
    scope.add_argument("--all", action="store_true", help="remove every cache entry")
    clear_parser.add_argument(
        "--yes", action="store_true", help="skip the confirmation prompt for --all"
    )

    return parser


def _print_fetch_cost(settings: Settings, fetcher: Fetcher) -> None:
    # A cost banner that fails to draw must never abort the run it's meant to precede: an
    # offline moment or a malformed Firecrawl reply here shouldn't stop a note from syncing.
    try:
        remaining = fetcher.remaining_credits()
        remaining_display = str(remaining) if remaining is not None else "n/a (direct backend)"
    except Exception as exc:
        logger.warning("could not check remaining credits: %s", exc)
        remaining_display = "unknown"
    print(f"backend: {settings.effective_fetch_backend}")
    print(f"cap: {settings.max_fetches_per_run} fetches this run")
    print(f"remaining credits: {remaining_display}")


def _print_report(report: SyncReport) -> None:
    fields = dataclasses.fields(report)
    width = max(len(field.name) for field in fields)
    for field in fields:
        print(f"{field.name:<{width}}  {getattr(report, field.name)}")


def _cmd_sync_vault(
    conn: sqlite3.Connection, settings: Settings, fetcher: Fetcher, *, follow_urls: bool
) -> int:
    if settings.vault_path is None:
        print("VAULT_PATH is not configured", file=sys.stderr)
        return 1
    _print_fetch_cost(settings, fetcher)
    report = sync_vault(conn, settings, fetcher, follow_urls=follow_urls)
    _print_report(report)
    return 0


def _cmd_credits(fetcher: Fetcher) -> int:
    remaining = fetcher.remaining_credits()
    print(remaining if remaining is not None else "n/a (direct backend)")
    return 0


def _cmd_ingest(conn: sqlite3.Connection, settings: Settings, fetcher: Fetcher, url: str) -> int:
    _print_fetch_cost(settings, fetcher)
    extracted = ingest_url(conn, url, fetcher)
    if extracted.error is None:
        print(f"{url}: ok")
        return 0
    print(f"{url}: failed: {extracted.error}", file=sys.stderr)
    return 1


def _cmd_cache_clear(settings: Settings, *, failed: bool, all_: bool, yes: bool) -> int:
    if all_ and not yes and not _confirm_clear_all(settings.fetch_cache_dir):
        print("aborted", file=sys.stderr)
        return 1
    removed = 0
    for path in settings.fetch_cache_dir.glob("*.json"):
        if all_ or (failed and _is_failed_cache_entry(path)):
            path.unlink()
            removed += 1
    print(f"removed {removed} cache entries")
    return 0


def _confirm_clear_all(cache_dir: Path) -> bool:
    """`--all` is the one destructive command here, wiping the cache that guards the budget."""
    if not sys.stdin.isatty():
        print("refusing to clear the entire cache non-interactively; pass --yes", file=sys.stderr)
        return False
    answer = input(f"remove every cache entry in {cache_dir}? [y/N] ")
    return answer.strip().lower() == "y"


def _is_failed_cache_entry(path: Path) -> bool:
    try:
        payload: Any = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("status") == "failed"


if __name__ == "__main__":
    sys.exit(main())
