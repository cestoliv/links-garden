from pathlib import Path

from links_garden.vault import read_vault


def _write(root: Path, relative: str, content: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def test_read_vault_yields_every_markdown_file_and_nothing_else(tmp_path: Path) -> None:
    _write(tmp_path, "a.md", "one")
    _write(tmp_path, "sub/b.md", "two")
    _write(tmp_path, "sub/deeper/c.md", "three")
    _write(tmp_path, "notes.txt", "ignored")

    notes = list(read_vault(tmp_path, exclude=()))

    assert {note.relative_path for note in notes} == {"a.md", "sub/b.md", "sub/deeper/c.md"}


def test_exclude_matches_path_segments_not_substrings(tmp_path: Path) -> None:
    _write(tmp_path, "wiki/note.md", "excluded")
    _write(tmp_path, "wikipedia-notes.md", "kept")

    notes = list(read_vault(tmp_path, exclude=("wiki",)))

    assert {note.relative_path for note in notes} == {"wikipedia-notes.md"}


def test_relative_path_is_posix_and_relative_to_root(tmp_path: Path) -> None:
    _write(tmp_path, "sub/dir/note.md", "content")

    [note] = list(read_vault(tmp_path, exclude=()))

    assert note.relative_path == "sub/dir/note.md"


def test_content_hash_changes_with_bytes_and_is_stable_across_reads(tmp_path: Path) -> None:
    path = _write(tmp_path, "note.md", "version one")

    [first] = list(read_vault(tmp_path, exclude=()))
    [first_again] = list(read_vault(tmp_path, exclude=()))
    path.write_text("version two")
    [changed] = list(read_vault(tmp_path, exclude=()))

    assert first.content_hash == first_again.content_hash
    assert first.content_hash != changed.content_hash


def test_title_resolution_prefers_frontmatter_then_heading_then_filename(tmp_path: Path) -> None:
    _write(tmp_path, "has-frontmatter.md", "---\ntitle: From Frontmatter\n---\n# Heading Title\n")
    _write(tmp_path, "has-heading.md", "# Heading Only\n\nbody text\n")
    _write(tmp_path, "bare.md", "just a body, no heading\n")

    notes = {note.relative_path: note for note in read_vault(tmp_path, exclude=())}

    assert notes["has-frontmatter.md"].title == "From Frontmatter"
    assert notes["has-heading.md"].title == "Heading Only"
    assert notes["bare.md"].title == "bare"


def test_frontmatter_scalars_and_one_level_lists_parse(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "note.md",
        "---\nurl: https://example.test/\nread: false\ntags:\n  - ios\n  - swift\n---\nbody\n",
    )

    [note] = list(read_vault(tmp_path, exclude=()))

    assert note.frontmatter == {
        "url": "https://example.test/",
        "read": "false",
        "tags": ["ios", "swift"],
    }


def test_unparseable_frontmatter_does_not_raise_and_keeps_raw_block(tmp_path: Path) -> None:
    raw = "not a key value pair\njust text\n"
    _write(tmp_path, "note.md", f"---\n{raw}---\nbody\n")

    [note] = list(read_vault(tmp_path, exclude=()))

    assert note.frontmatter == {"raw": raw}


def test_note_without_frontmatter_yields_empty_dict_and_full_body(tmp_path: Path) -> None:
    content = "# Title\n\nSome content here.\n"
    _write(tmp_path, "note.md", content)

    [note] = list(read_vault(tmp_path, exclude=()))

    assert note.frontmatter == {}
    assert note.body == content


def test_urls_deduplicate_in_first_appearance_order(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "note.md",
        "See https://b.test/ then https://a.test/ and again https://b.test/.",
    )

    [note] = list(read_vault(tmp_path, exclude=()))

    assert note.urls == ("https://b.test/", "https://a.test/")


def test_markdown_link_url_has_no_trailing_paren(tmp_path: Path) -> None:
    _write(tmp_path, "note.md", "[x](https://a.test/p)")

    [note] = list(read_vault(tmp_path, exclude=()))

    assert note.urls == ("https://a.test/p",)


def test_invalid_utf8_bytes_do_not_raise(tmp_path: Path) -> None:
    path = tmp_path / "note.md"
    path.write_bytes(b"# Title\n\xff\xfe invalid bytes\n")

    notes = list(read_vault(tmp_path, exclude=()))

    assert len(notes) == 1
    assert notes[0].title == "Title"


def test_empty_vault_yields_nothing(tmp_path: Path) -> None:
    assert list(read_vault(tmp_path, exclude=())) == []


def test_frontmatter_only_url_is_extracted(tmp_path: Path) -> None:
    _write(tmp_path, "note.md", "---\nurl: https://asccli.sh/\n---\nno links in the body\n")

    [note] = list(read_vault(tmp_path, exclude=()))

    assert note.urls == ("https://asccli.sh/",)


def test_frontmatter_url_is_not_special_cased_to_the_url_key(tmp_path: Path) -> None:
    _write(tmp_path, "note.md", "---\nsource: https://example.test/page\n---\nbody\n")

    [note] = list(read_vault(tmp_path, exclude=()))

    assert note.urls == ("https://example.test/page",)


def test_frontmatter_list_url_is_extracted(tmp_path: Path) -> None:
    _write(tmp_path, "note.md", "---\nlinks:\n  - https://a.test/\n  - not-a-url\n---\nbody\n")

    [note] = list(read_vault(tmp_path, exclude=()))

    assert note.urls == ("https://a.test/",)


def test_url_in_both_frontmatter_and_body_appears_once_frontmatter_first(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "note.md",
        "---\nurl: https://dup.test/\n---\nSee https://other.test/ and then https://dup.test/.\n",
    )

    [note] = list(read_vault(tmp_path, exclude=()))

    assert note.urls == ("https://dup.test/", "https://other.test/")


def test_directory_named_dot_md_is_skipped_but_sibling_note_is_kept(tmp_path: Path) -> None:
    _write(tmp_path, "real.md", "a real note")
    (tmp_path / "looks-like-a-note.md").mkdir()

    notes = list(read_vault(tmp_path, exclude=()))

    assert {note.relative_path for note in notes} == {"real.md"}


def test_broken_symlink_is_skipped_but_sibling_note_is_kept(tmp_path: Path) -> None:
    _write(tmp_path, "real.md", "a real note")
    (tmp_path / "broken.md").symlink_to(tmp_path / "does-not-exist.md")

    notes = list(read_vault(tmp_path, exclude=()))

    assert {note.relative_path for note in notes} == {"real.md"}


def test_utf8_bom_does_not_hide_frontmatter(tmp_path: Path) -> None:
    path = tmp_path / "note.md"
    path.write_bytes("---\ntitle: With BOM\n---\nbody\n".encode("utf-8-sig"))

    [note] = list(read_vault(tmp_path, exclude=()))

    assert note.frontmatter == {"title": "With BOM"}
    assert note.title == "With BOM"


def test_content_hash_is_over_raw_bytes_not_decoded_text(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_bytes(b"line \xff end")
    (tmp_path / "b.md").write_bytes(b"line \xfe end")

    notes = {note.relative_path: note for note in read_vault(tmp_path, exclude=())}

    # errors="replace" collapses both invalid bytes to the same character, so identical
    # bodies here are exactly what would let a hash-over-decoded-text bug slip through.
    assert notes["a.md"].body == notes["b.md"].body
    assert notes["a.md"].content_hash != notes["b.md"].content_hash
