"""Tests for set definitions: CRUD, schema validation, and the `garden sets` subcommands."""

import json
import sqlite3
from pathlib import Path
from typing import cast

import pytest

from links_garden import cli
from links_garden.config import Settings
from links_garden.db import connect, initialize
from links_garden.sets import create_set, delete_set, get_set, list_sets, update_set

_RECIPE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "duration_in_minutes": {"type": "integer"},
        "steps": {"type": "array", "items": {"type": "string"}},
    },
}


def _open(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "garden.db")
    initialize(conn)
    return conn


def test_create_then_list_and_get_round_trip_the_schema(tmp_path: Path) -> None:
    conn = _open(tmp_path)

    created = create_set(conn, "recipe", "a cooking recipe", _RECIPE_SCHEMA)

    assert list_sets(conn) == [created]
    assert get_set(conn, "recipe") == created
    assert created.schema == _RECIPE_SCHEMA


def test_duplicate_name_is_rejected(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    create_set(conn, "recipe", "a cooking recipe", _RECIPE_SCHEMA)

    with pytest.raises(ValueError, match="recipe"):
        create_set(conn, "recipe", "another description", _RECIPE_SCHEMA)


def test_empty_description_is_rejected(tmp_path: Path) -> None:
    conn = _open(tmp_path)

    with pytest.raises(ValueError, match="description"):
        create_set(conn, "recipe", "  ", _RECIPE_SCHEMA)


def test_schema_that_is_not_an_object_is_rejected(tmp_path: Path) -> None:
    conn = _open(tmp_path)

    with pytest.raises(ValueError, match="object"):
        create_set(
            conn, "recipe", "a cooking recipe", cast(dict[str, object], ["not", "an", "object"])
        )


def test_schema_without_properties_is_rejected(tmp_path: Path) -> None:
    conn = _open(tmp_path)

    with pytest.raises(ValueError, match="properties"):
        create_set(conn, "recipe", "a cooking recipe", {"type": "object"})


def test_schema_with_empty_properties_is_rejected(tmp_path: Path) -> None:
    # An empty `properties` is the same failure mode as an empty description: ollama has
    # nothing to extract, returns {} for every document, and nothing ever flags it as wrong.
    conn = _open(tmp_path)

    with pytest.raises(ValueError, match="properties"):
        create_set(conn, "recipe", "a cooking recipe", {"type": "object", "properties": {}})


def test_empty_name_is_rejected(tmp_path: Path) -> None:
    conn = _open(tmp_path)

    with pytest.raises(ValueError, match="name"):
        create_set(conn, "  ", "a cooking recipe", _RECIPE_SCHEMA)


def test_update_changes_description_and_schema_and_bumps_updated_at(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    create_set(conn, "recipe", "a cooking recipe", _RECIPE_SCHEMA)
    conn.execute("UPDATE sets SET updated_at = '2020-01-01 00:00:00' WHERE name = 'recipe'")
    conn.commit()
    new_schema: dict[str, object] = {"type": "object", "properties": {"name": {"type": "string"}}}

    updated = update_set(conn, "recipe", description="an updated recipe", schema=new_schema)

    assert updated.description == "an updated recipe"
    assert updated.schema == new_schema
    # Re-reading from the store, not just trusting the returned dataclass: `update_set` builds
    # that value from its own arguments, so a query that silently wrote the old values back
    # would still return the right-looking object without this.
    assert get_set(conn, "recipe") == updated
    row = conn.execute("SELECT updated_at FROM sets WHERE name = 'recipe'").fetchone()
    assert row["updated_at"] != "2020-01-01 00:00:00"


def test_delete_removes_the_set_and_cascades_memberships(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    created = create_set(conn, "recipe", "a cooking recipe", _RECIPE_SCHEMA)
    cursor = conn.execute("INSERT INTO documents (source, source_ref) VALUES ('manual', 'ref-1')")
    conn.execute(
        "INSERT INTO set_memberships (document_id, set_id) VALUES (?, ?)",
        (cursor.lastrowid, created.id),
    )
    conn.commit()

    assert delete_set(conn, "recipe") is True

    assert get_set(conn, "recipe") is None
    assert conn.execute("SELECT 1 FROM set_memberships").fetchone() is None


def test_delete_nonexistent_set_returns_false_rather_than_raising(tmp_path: Path) -> None:
    conn = _open(tmp_path)

    assert delete_set(conn, "does-not-exist") is False


def test_schema_json_round_trips_key_order_and_types(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "niche": {"type": "string"},
            "follower_count": {"type": "integer"},
        },
        "additionalProperties": False,
        "required": ["niche", "follower_count"],
    }

    create_set(conn, "tiktok_influenceur", "a TikTok influencer profile", schema)
    fetched = get_set(conn, "tiktok_influenceur")

    assert fetched is not None
    assert list(fetched.schema.keys()) == list(schema.keys())
    # dict equality treats True/False as 1/0, so `is` here confirms the JSON round trip kept
    # this a real bool rather than silently coercing it to an int.
    assert fetched.schema["additionalProperties"] is False
    assert fetched.schema == schema


def test_cli_show_and_remove_exit_nonzero_on_missing_set(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = _open(tmp_path)

    assert cli._cmd_sets_show(conn, "does-not-exist") == 1
    assert "does-not-exist" in capsys.readouterr().err

    assert cli._cmd_sets_remove(conn, "does-not-exist") == 1
    assert "does-not-exist" in capsys.readouterr().err


def test_cli_add_reads_schema_from_a_file_and_creates_the_set(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    schema_path = tmp_path / "recipe.json"
    schema_path.write_text(json.dumps(_RECIPE_SCHEMA))

    exit_code = cli._cmd_sets_add(conn, "recipe", "a cooking recipe", schema_path)

    assert exit_code == 0
    assert get_set(conn, "recipe") is not None


def test_cli_add_exits_nonzero_on_a_malformed_schema_file(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    schema_path = tmp_path / "bad.json"
    schema_path.write_text("not json")

    exit_code = cli._cmd_sets_add(conn, "recipe", "a cooking recipe", schema_path)

    assert exit_code == 1


def test_sets_list_subcommand_parses() -> None:
    args = cli._build_parser().parse_args(["sets", "list"])
    assert args.sets_command == "list"


def test_main_routes_the_sets_command_to_cmd_sets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A prior review broke main()'s `sets` dispatch branch and every existing test still passed,
    # because they all called cli._cmd_sets_* directly. This drives main() itself end to end.
    monkeypatch.setattr(
        cli,
        "load_settings",
        lambda: Settings(_env_file=None, database_path=tmp_path / "garden.db"),
    )

    exit_code = cli.main(["sets", "list"])

    assert exit_code == 0
    assert "no sets defined" in capsys.readouterr().out
