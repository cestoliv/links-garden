"""Obsidian vault reader. Standard library only. The vault is read-only forever."""

import hashlib
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

_FRONTMATTER_DELIMITER = "---"
_URL_PATTERN = re.compile(r"https?://\S+")
_URL_TRAILING_PUNCTUATION = ".,)]>\"'"


@dataclass(frozen=True)
class VaultNote:
    """One markdown note, with its frontmatter parsed and its URLs extracted."""

    relative_path: str
    content_hash: str
    title: str
    frontmatter: dict[str, object]
    body: str
    urls: tuple[str, ...]


def read_vault(root: Path, exclude: Sequence[str]) -> Iterator[VaultNote]:
    """Yield a `VaultNote` for every `*.md` file under `root`, skipping excluded segments.

    A file is skipped when an excluded name is one of its path segments exactly, not a
    substring of one: excluding "wiki" drops `wiki/note.md` but keeps `wikipedia-notes.md`.
    Files are opened for reading only; nothing here ever writes, renames or deletes.
    """
    excluded = set(exclude)
    for path in sorted(root.rglob("*.md")):
        relative = path.relative_to(root)
        if excluded.intersection(relative.parts):
            continue
        # A directory named *.md or a broken symlink is unreadable, not a note; one such
        # path must not stop the walk over the other 134.
        if not path.is_file():
            continue
        yield _read_note(path, relative)


def _read_note(path: Path, relative: Path) -> VaultNote:
    # Read raw bytes once: the hash needs them raw, and decoding them here tolerates a bad
    # byte the same way opening in text mode with errors="replace" would, without a second read.
    raw_bytes = path.read_bytes()
    # A leading BOM hides the "---" fence from _split_frontmatter, silently dropping every
    # frontmatter field; strip it before frontmatter detection, not from the hashed bytes.
    text = raw_bytes.decode("utf-8", errors="replace").removeprefix("﻿")
    frontmatter, body = _read_frontmatter(text)
    return VaultNote(
        relative_path=relative.as_posix(),
        content_hash=hashlib.sha256(raw_bytes).hexdigest(),
        title=_resolve_title(frontmatter, body, path.stem),
        frontmatter=frontmatter,
        body=body,
        urls=_collect_urls(frontmatter, body),
    )


def _read_frontmatter(text: str) -> tuple[dict[str, object], str]:
    raw_block, body = _split_frontmatter(text)
    if raw_block is None:
        return {}, body
    return _parse_frontmatter(raw_block), body


def _split_frontmatter(text: str) -> tuple[str | None, str]:
    """Split a leading `---` ... `---` block from the rest of the file, if one opens and closes."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != _FRONTMATTER_DELIMITER:
        return None, text
    for i in range(1, len(lines)):
        if lines[i].strip() == _FRONTMATTER_DELIMITER:
            return "".join(lines[1:i]), "".join(lines[i + 1 :])
    return None, text  # opening delimiter never closes: not frontmatter, treat it all as body


def _parse_frontmatter(raw_block: str) -> dict[str, object]:
    """Parse `key: value`, one-level `- item` lists, and inline `[a, b]` lists.

    These three shapes cover the whole vault. Anything else means giving up on the block
    rather than guessing at its meaning: a vault note is user data and must never break the
    sync, so the raw text is kept under one key instead.
    """
    try:
        return _parse_frontmatter_lines(raw_block.splitlines())
    except ValueError:
        return {"raw": raw_block}


def _parse_frontmatter_lines(lines: list[str]) -> dict[str, object]:
    result: dict[str, object] = {}
    current_key: str | None = None
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- "):
            current_key = _append_list_item(result, current_key, stripped[2:].strip())
        else:
            current_key = _parse_key_line(result, stripped)
    return result


def _append_list_item(result: dict[str, object], current_key: str | None, item: str) -> str:
    if current_key is None or not isinstance(result.get(current_key), list):
        raise ValueError("list item outside of an open list")
    cast(list[str], result[current_key]).append(item)
    return current_key


def _parse_key_line(result: dict[str, object], stripped: str) -> str | None:
    key, sep, rest = stripped.partition(":")
    key = key.strip()
    if not sep or not key:
        raise ValueError("not a 'key: value' line")
    rest = rest.strip()
    if not rest:
        result[key] = []  # empty value: a nested list follows on the next `- item` lines
        return key
    if rest.startswith("[") and rest.endswith("]"):
        result[key] = [item.strip() for item in rest[1:-1].split(",") if item.strip()]
    else:
        # Kept as a string, deliberately: "false", "no" and numbers stay unparsed so every
        # scalar round-trips the same way once this dict is stored as JSON.
        result[key] = rest
    return None


def _resolve_title(frontmatter: dict[str, object], body: str, filename_stem: str) -> str:
    title = frontmatter.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return filename_stem


def _collect_urls(frontmatter: dict[str, object], body: str) -> tuple[str, ...]:
    """URLs from frontmatter scalars, then the body, de-duplicated in that order.

    A frontmatter URL is the note's subject rather than an aside, so it is not keyed off
    "url" specifically: some notes (the vault's Links inbox) carry their only URL as
    `source:` or another key, never repeated in the body.
    """
    seen: dict[str, None] = {}
    for value in frontmatter.values():
        for url in _urls_in(value):
            seen.setdefault(url, None)
    for url in find_urls(body):
        seen.setdefault(url, None)
    return tuple(seen)


def _urls_in(value: object) -> Iterator[str]:
    """URLs inside one frontmatter value: a scalar string, or a one-level list of them."""
    if isinstance(value, str):
        yield from find_urls(value)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                yield from find_urls(item)


def find_urls(text: str) -> Iterator[str]:
    """URLs found in free text, trailing punctuation stripped. Shared with `signal_sync`, so a
    URL embedded in prose is recognized the same way regardless of source.
    """
    for match in _URL_PATTERN.finditer(text):
        url = match.group(0).rstrip(_URL_TRAILING_PUNCTUATION)
        if url:
            yield url
