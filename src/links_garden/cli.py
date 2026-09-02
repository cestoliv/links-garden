"""`garden` command-line entry point, built on standard library `argparse`."""

import argparse
import dataclasses
import json
import logging
import sqlite3
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import uvicorn

from links_garden.api import create_app
from links_garden.beeper import BeeperClient
from links_garden.config import Settings, load_settings
from links_garden.db import connect, initialize
from links_garden.embed import Embedder, IndexReport, index_documents
from links_garden.enrich import Enricher, EnrichReport, enrich_documents
from links_garden.extract_sets import ExtractReport, extract_pending
from links_garden.fetch import Fetcher
from links_garden.mcp_server import create_mcp_app
from links_garden.search import Hit, search
from links_garden.sets import create_set, delete_set, get_set, list_sets
from links_garden.signal_sync import SignalReport, sync_signal
from links_garden.sync import SyncReport, ingest_url, sync_vault

logger = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    settings = load_settings()
    conn = connect(settings.database_path)
    initialize(conn)
    fetcher = Fetcher(settings)
    beeper = BeeperClient(settings)
    embedder = Embedder(settings)
    enricher = Enricher(settings)

    # A dict, not an if-chain: one more command would push the chain's branching past the
    # complexity ceiling for no real benefit, since every branch is a single dispatch call.
    dispatch: dict[str, Callable[[], int]] = {
        "sync-vault": lambda: _cmd_sync_vault(
            conn, settings, fetcher, follow_urls=not args.no_follow_urls
        ),
        "sync-signal": lambda: _cmd_sync_signal(
            conn, settings, fetcher, beeper, follow_urls=not args.no_follow_urls
        ),
        "credits": lambda: _cmd_credits(fetcher),
        "ingest": lambda: _cmd_ingest(conn, settings, fetcher, args.url),
        "index": lambda: _cmd_index(conn, settings, embedder),
        "enrich": lambda: _cmd_enrich(conn, settings, enricher),
        "extract": lambda: _cmd_extract(conn, settings, enricher, args.set_name),
        "search": lambda: _cmd_search(conn, settings, embedder, args.query, limit=args.limit),
        "sets": lambda: _cmd_sets(conn, args),
        "serve": lambda: _cmd_serve(settings, args.host, args.port),
        "mcp": lambda: _cmd_mcp(settings, args.host, args.port),
    }
    if args.command in dispatch:
        return dispatch[args.command]()
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
    sync_signal_parser = subparsers.add_parser(
        "sync-signal", help="sync the Signal chat to self into the store"
    )
    sync_signal_parser.add_argument(
        "--no-follow-urls",
        action="store_true",
        help="count messages only; skip fetching the URLs they carry",
    )

    subparsers.add_parser("credits", help="print remaining Firecrawl credits")

    subparsers.add_parser("index", help="chunk and embed documents that changed since last run")

    subparsers.add_parser(
        "enrich", help="summarize, keyword and classify documents that changed since last run"
    )

    extract_parser = subparsers.add_parser(
        "extract", help="extract per-set fields for pending set memberships"
    )
    extract_parser.add_argument("--set", dest="set_name", help="only fill memberships for this set")

    ingest_parser = subparsers.add_parser("ingest", help="fetch and store one URL")
    ingest_parser.add_argument("url")

    search_parser = subparsers.add_parser("search", help="search the garden")
    search_parser.add_argument("query")
    search_parser.add_argument("--limit", type=int, default=20)

    serve_parser = subparsers.add_parser("serve", help="run the HTTP API")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)

    mcp_parser = subparsers.add_parser("mcp", help="run the MCP server")
    mcp_parser.add_argument("--host", default="127.0.0.1")
    mcp_parser.add_argument("--port", type=int, default=8001)

    sets_parser = subparsers.add_parser("sets", help="manage set definitions")
    sets_subparsers = sets_parser.add_subparsers(dest="sets_command", required=True)
    sets_subparsers.add_parser("list", help="list set definitions")
    add_parser = sets_subparsers.add_parser("add", help="create a set definition")
    add_parser.add_argument("name")
    add_parser.add_argument("--description", required=True)
    add_parser.add_argument("--schema", required=True, type=Path, help="path to a JSON Schema file")
    show_parser = sets_subparsers.add_parser("show", help="show one set definition")
    show_parser.add_argument("name")
    remove_parser = sets_subparsers.add_parser("remove", help="delete a set definition")
    remove_parser.add_argument("name")

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


def _print_report(
    report: SyncReport | SignalReport | IndexReport | EnrichReport | ExtractReport,
) -> None:
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


def _cmd_sync_signal(
    conn: sqlite3.Connection,
    settings: Settings,
    fetcher: Fetcher,
    beeper: BeeperClient,
    *,
    follow_urls: bool,
) -> int:
    if not settings.beeper_access_token.get_secret_value() or not settings.beeper_chat_id:
        print("BEEPER_ACCESS_TOKEN and BEEPER_CHAT_ID must both be set", file=sys.stderr)
        return 1
    if not beeper.check():
        print("Beeper Desktop is not reachable; is it running?", file=sys.stderr)
        return 1
    _print_fetch_cost(settings, fetcher)
    print(f"backfill start date: {settings.backfill_start_date or 'none (full history)'}")
    report = sync_signal(conn, settings, fetcher, beeper, follow_urls=follow_urls)
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


def _cmd_index(conn: sqlite3.Connection, settings: Settings, embedder: Embedder) -> int:
    print(f"model: {settings.embedding_model}")
    print(f"ollama: {settings.ollama_url}")
    if not embedder.check():
        print(
            f"{settings.embedding_model} is not available at {settings.ollama_url} "
            "(not pulled, or ollama is unreachable)",
            file=sys.stderr,
        )
        return 1
    report = index_documents(conn, settings, embedder)
    _print_report(report)
    # Unlike a sync's urls_failed, a dead link is expected and the row records it for the
    # review queue. documents_failed here means the embedding backend broke mid-run: the
    # corpus now answers searches wrongly with nothing to show for it, so cron must see red.
    return 1 if report.documents_failed else 0


def _cmd_enrich(conn: sqlite3.Connection, settings: Settings, enricher: Enricher) -> int:
    print(f"model: {settings.extraction_model}")
    print(f"ollama: {settings.ollama_url}")
    if not enricher.check():
        print(
            f"{settings.extraction_model} is not available at {settings.ollama_url} "
            "(not pulled, or ollama is unreachable)",
            file=sys.stderr,
        )
        return 1
    report = enrich_documents(conn, settings, enricher)
    _print_report(report)
    # Same reasoning as _cmd_index: documents_failed here means the extraction backend broke
    # mid-run, not a routine per-document miss, so cron must see red.
    return 1 if report.documents_failed else 0


def _cmd_extract(
    conn: sqlite3.Connection, settings: Settings, enricher: Enricher, set_name: str | None
) -> int:
    if set_name is not None and get_set(conn, set_name) is None:
        print(f"no set named {set_name!r}", file=sys.stderr)
        return 1
    print(f"model: {settings.extraction_model}")
    print(f"ollama: {settings.ollama_url}")
    if not enricher.check():
        print(
            f"{settings.extraction_model} is not available at {settings.ollama_url} "
            "(not pulled, or ollama is unreachable)",
            file=sys.stderr,
        )
        return 1
    report = extract_pending(conn, enricher, set_name=set_name)
    _print_report(report)
    # Same reasoning as _cmd_index and _cmd_enrich: memberships_failed means the extraction
    # backend broke mid-run, not a routine missing field, so cron must see red.
    return 1 if report.memberships_failed else 0


def _cmd_search(
    conn: sqlite3.Connection, settings: Settings, embedder: Embedder, query: str, *, limit: int
) -> int:
    hits = search(conn, settings, embedder, query, limit=limit)
    if not hits:
        print("no results")
        return 0
    for rank, hit in enumerate(hits, start=1):
        _print_hit(rank, hit)
    return 0


def _print_hit(rank: int, hit: Hit) -> None:
    print(f"{rank}. {hit.title or '(untitled)'}")
    print(f"   {hit.url or '(no url)'}")
    print(f"   {hit.snippet}")


def _cmd_sets(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    if args.sets_command == "list":
        return _cmd_sets_list(conn)
    if args.sets_command == "add":
        return _cmd_sets_add(conn, args.name, args.description, args.schema)
    if args.sets_command == "show":
        return _cmd_sets_show(conn, args.name)
    return _cmd_sets_remove(conn, args.name)


def _cmd_sets_list(conn: sqlite3.Connection) -> int:
    sets = list_sets(conn)
    if not sets:
        print("no sets defined")
        return 0
    for set_ in sets:
        print(f"{set_.name}: {set_.description}")
    return 0


def _cmd_sets_add(conn: sqlite3.Connection, name: str, description: str, schema_path: Path) -> int:
    try:
        schema: Any = json.loads(schema_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"could not read schema from {schema_path}: {exc}", file=sys.stderr)
        return 1
    try:
        create_set(conn, name, description, schema)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"{name}: created")
    return 0


def _cmd_sets_show(conn: sqlite3.Connection, name: str) -> int:
    definition = get_set(conn, name)
    if definition is None:
        print(f"no set named {name!r}", file=sys.stderr)
        return 1
    print(f"name: {definition.name}")
    print(f"description: {definition.description}")
    print(f"schema: {json.dumps(definition.schema, indent=2)}")
    return 0


def _cmd_sets_remove(conn: sqlite3.Connection, name: str) -> int:
    if not delete_set(conn, name):
        print(f"no set named {name!r}", file=sys.stderr)
        return 1
    print(f"{name}: removed")
    return 0


def _cmd_serve(settings: Settings, host: str, port: int) -> int:
    # create_app opens its own connection and embedder/fetcher; main()'s own conn/fetcher/embedder
    # go unused on this path, same as they already do for e.g. "credits".
    uvicorn.run(create_app(settings), host=host, port=port)
    return 0


def _cmd_mcp(settings: Settings, host: str, port: int) -> int:
    uvicorn.run(create_mcp_app(settings), host=host, port=port)
    return 0


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
