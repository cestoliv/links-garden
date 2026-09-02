"""Set definitions: the user's own categories for classification and extraction.

A set's schema is handed straight to ollama as its `format` parameter, and its description is
what the classifier reads to decide membership. Neither fails loudly when wrong -- a malformed
schema just produces silent garbage in `extracted_json`, and an empty description silently
disables the set -- so both are validated on every write. The schema check only enforces the
shape ollama actually needs (a JSON object with `type: "object"` and a `properties` mapping);
anything deeper is left for ollama itself to reject.
"""

import json
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class SetDefinition:
    id: int | None
    name: str
    description: str
    schema: dict[str, object]


def _validate(description: str, schema: dict[str, object]) -> None:
    if not description.strip():
        raise ValueError("description must not be empty")
    if not isinstance(schema, dict):
        raise ValueError("schema must be a JSON object")
    if schema.get("type") != "object":
        raise ValueError('schema must have "type": "object"')
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        raise ValueError('schema must have a non-empty "properties" object')


def _row_to_set(row: sqlite3.Row) -> SetDefinition:
    return SetDefinition(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        schema=json.loads(row["schema_json"]),
    )


def list_sets(conn: sqlite3.Connection) -> list[SetDefinition]:
    rows = conn.execute(
        "SELECT id, name, description, schema_json FROM sets ORDER BY name"
    ).fetchall()
    return [_row_to_set(row) for row in rows]


def get_set(conn: sqlite3.Connection, name: str) -> SetDefinition | None:
    row = conn.execute(
        "SELECT id, name, description, schema_json FROM sets WHERE name = ?", (name,)
    ).fetchone()
    return _row_to_set(row) if row is not None else None


def create_set(
    conn: sqlite3.Connection, name: str, description: str, schema: dict[str, object]
) -> SetDefinition:
    if not name.strip():
        raise ValueError("name must not be empty")
    _validate(description, schema)
    try:
        cursor = conn.execute(
            "INSERT INTO sets (name, description, schema_json) VALUES (?, ?, ?)",
            (name, description, json.dumps(schema)),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"a set named {name!r} already exists") from exc
    conn.commit()
    assert cursor.lastrowid is not None
    return SetDefinition(id=cursor.lastrowid, name=name, description=description, schema=schema)


def update_set(
    conn: sqlite3.Connection,
    name: str,
    *,
    description: str | None = None,
    schema: dict[str, object] | None = None,
) -> SetDefinition:
    current = get_set(conn, name)
    if current is None:
        raise ValueError(f"no set named {name!r}")
    new_description = current.description if description is None else description
    new_schema = current.schema if schema is None else schema
    _validate(new_description, new_schema)
    conn.execute(
        "UPDATE sets SET description = ?, schema_json = ?, updated_at = datetime('now') "
        "WHERE name = ?",
        (new_description, json.dumps(new_schema), name),
    )
    conn.commit()
    return SetDefinition(id=current.id, name=name, description=new_description, schema=new_schema)


def delete_set(conn: sqlite3.Connection, name: str) -> bool:
    """Delete a set, cascading to `set_memberships` through its `ON DELETE CASCADE` foreign key."""
    cursor = conn.execute("DELETE FROM sets WHERE name = ?", (name,))
    conn.commit()
    return cursor.rowcount > 0


def compute_missing_fields(schema: dict[str, object], values: dict[str, object]) -> list[str]:
    """Required fields absent or explicitly null in `values`, per `schema`'s own `required` list.

    Shared by extraction (`extract_sets.py`) and a manual `PATCH` (`api.py`) so "required" can't
    drift into two different meanings between the two call sites that decide a membership's
    `status`.
    """
    required = schema.get("required")
    if not isinstance(required, list):
        return []
    return [field for field in required if isinstance(field, str) and values.get(field) is None]
