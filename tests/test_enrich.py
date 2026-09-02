import json
import re
import sqlite3
from collections.abc import Sequence
from pathlib import Path

import httpx
import pytest

from links_garden.config import Settings
from links_garden.db import connect, initialize
from links_garden.enrich import (
    Enricher,
    Enrichment,
    EnrichReport,
    enrich_documents,
)
from links_garden.sets import SetDefinition, create_set, get_set

_SCHEMA: dict[str, object] = {"type": "object", "properties": {"name": {"type": "string"}}}


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(_env_file=None, database_path=tmp_path / "garden.db", **overrides)  # type: ignore[arg-type]


def _client(handler: object) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


def _chat_response(summary: str, keywords: Sequence[str], sets: Sequence[str]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "message": {
                "content": json.dumps(
                    {
                        "summary": summary,
                        "keywords": list(keywords),
                        "sets": [{"name": name, "evidence": "test evidence"} for name in sets],
                    }
                )
            }
        },
    )


def test_think_false_is_set_in_the_request_body(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _chat_response("a summary", ["k"], [])

    enricher = Enricher(_settings(tmp_path), client=_client(handler))

    enricher.enrich("some text", [])

    payload = json.loads(requests[0].content)
    assert payload["think"] is False


def test_prompt_contains_every_sets_name_and_description(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _chat_response("a summary", ["k"], [])

    sets = [
        SetDefinition(id=1, name="recipe", description="a cooking recipe", schema=_SCHEMA),
        SetDefinition(
            id=2,
            name="tiktok_influenceur",
            description="a TikTok influencer profile",
            schema=_SCHEMA,
        ),
    ]
    enricher = Enricher(_settings(tmp_path), client=_client(handler))

    enricher.enrich("some text", sets)

    payload = json.loads(requests[0].content)
    prompt = payload["messages"][0]["content"]
    for set_ in sets:
        assert set_.name in prompt
        assert set_.description in prompt


def test_response_format_and_non_streaming_are_set_in_the_request_body(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _chat_response("a summary", ["k"], [])

    enricher = Enricher(_settings(tmp_path), client=_client(handler))

    enricher.enrich("some text", [])

    payload = json.loads(requests[0].content)
    assert payload["format"]["required"] == ["summary", "keywords", "sets"]
    assert payload["stream"] is False
    assert payload["options"]["temperature"] == 0


def test_long_document_is_truncated_before_the_request_is_built(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _chat_response("a summary", ["k"], [])

    enricher = Enricher(_settings(tmp_path), client=_client(handler))
    long_text = "start-of-document " + ("filler " * 10_000) + "END-OF-DOCUMENT-MARKER"

    enricher.enrich(long_text, [])

    prompt = json.loads(requests[0].content)["messages"][0]["content"]
    assert "start-of-document" in prompt
    assert "END-OF-DOCUMENT-MARKER" not in prompt


def test_labelled_keyword_groups_are_split_and_unlabelled(tmp_path: Path) -> None:
    # The model sometimes groups keywords under a label instead of returning bare terms, e.g.
    # "English keywords: a, b, c" as one array element; this must not leak "keywords" itself
    # into the stored (and FTS-searchable) keyword list.
    def handler(request: httpx.Request) -> httpx.Response:
        return _chat_response(
            "a summary",
            ["English keywords: a, b, c", "French keywords: d", "Keywords:", "Mots-clés :"],
            [],
        )

    enricher = Enricher(_settings(tmp_path), client=_client(handler))

    enrichment = enricher.enrich("some text", [])

    # "Keywords:" and "Mots-clés :" are bare labels with nothing after them and must vanish
    # entirely, not survive as the literal term "Keywords:".
    assert enrichment.keywords == ("a", "b", "c", "d")


def test_unreachable_ollama_raises_clear_error_naming_url_and_model(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    settings = _settings(tmp_path, ollama_url="http://127.0.0.1:11434", extraction_model="qwen3:8b")
    enricher = Enricher(settings, client=_client(handler))

    with pytest.raises(RuntimeError, match=re.escape("127.0.0.1:11434")) as exc_info:
        enricher.enrich("text", [])
    assert "qwen3:8b" in str(exc_info.value)


def test_check_returns_false_when_model_absent_true_when_present(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "bge-m3"}]})

    settings = _settings(tmp_path, extraction_model="qwen3:8b")
    enricher = Enricher(settings, client=_client(handler))
    assert enricher.check() is False

    def handler_present(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})

    enricher = Enricher(settings, client=_client(handler_present))
    assert enricher.check() is True


# --- Enricher.extract ---
#
# Separate section: `extract` has its own `format` (the set's own schema, not the fixed
# enrichment envelope) and its own prompt, so none of the `enrich` tests above exercise it.


def test_extract_sends_the_sets_schema_as_format_with_think_false_and_stream_false(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    schema: dict[str, object] = {
        "type": "object",
        "properties": {"username": {"type": "string"}},
        "required": ["username"],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"message": {"content": json.dumps({"username": "ana"})}})

    enricher = Enricher(_settings(tmp_path), client=_client(handler))

    enricher.extract("some text", schema)

    payload = json.loads(requests[0].content)
    assert payload["format"] == schema
    assert payload["think"] is False
    assert payload["options"]["temperature"] == 0
    assert payload["stream"] is False


def test_extract_long_document_is_truncated_before_the_request_is_built(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"message": {"content": json.dumps({})}})

    enricher = Enricher(_settings(tmp_path), client=_client(handler))
    long_text = "start-of-document " + ("filler " * 10_000) + "END-OF-DOCUMENT-MARKER"

    enricher.extract(long_text, _SCHEMA)

    prompt = json.loads(requests[0].content)["messages"][0]["content"]
    assert "start-of-document" in prompt
    assert "END-OF-DOCUMENT-MARKER" not in prompt


def test_extract_raises_clear_error_on_non_dict_response(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": json.dumps(["not", "a", "dict"])}})

    settings = _settings(tmp_path, ollama_url="http://127.0.0.1:11434", extraction_model="qwen3:8b")
    enricher = Enricher(settings, client=_client(handler))

    with pytest.raises(RuntimeError, match=re.escape("127.0.0.1:11434")) as exc_info:
        enricher.extract("text", _SCHEMA)
    assert "qwen3:8b" in str(exc_info.value)


def test_extract_unreachable_ollama_raises_clear_error_naming_url_and_model(
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    settings = _settings(tmp_path, ollama_url="http://127.0.0.1:11434", extraction_model="qwen3:8b")
    enricher = Enricher(settings, client=_client(handler))

    with pytest.raises(RuntimeError, match=re.escape("127.0.0.1:11434")) as exc_info:
        enricher.extract("text", _SCHEMA)
    assert "qwen3:8b" in str(exc_info.value)


# --- enrich_documents ---


class _FakeEnricher:
    """Deterministic enrichment, never touches the network."""

    def __init__(
        self,
        *,
        set_names: tuple[str, ...] = (),
        fail_on: str | None = None,
        keyword: str = "keyword",
        keywords: Sequence[str] | None = None,
    ) -> None:
        self.calls: list[str] = []
        self._set_names = set_names
        self._fail_on = fail_on
        self._keywords = tuple(keywords) if keywords is not None else (keyword,)

    def enrich(self, text: str, sets: Sequence[SetDefinition]) -> Enrichment:
        if self._fail_on is not None and self._fail_on in text:
            raise RuntimeError("enrichment boom")
        self.calls.append(text)
        return Enrichment(
            summary=f"summary of {len(text)} chars",
            keywords=self._keywords,
            set_names=self._set_names,
        )


def _open(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "garden.db")
    initialize(conn)
    return conn


def _insert_document(
    conn: sqlite3.Connection,
    source_ref: str = "ref-1",
    *,
    content: str | None = "some content",
    status: str = "ok",
    deleted: bool = False,
) -> int:
    cursor = conn.execute(
        "INSERT INTO documents (source, source_ref, content, status, deleted_at) "
        "VALUES ('manual', ?, ?, ?, ?)",
        (source_ref, content, status, "2026-01-01" if deleted else None),
    )
    conn.commit()
    assert cursor.lastrowid is not None
    return cursor.lastrowid


def test_one_document_produces_summary_keywords_and_memberships(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    created = create_set(conn, "recipe", "a cooking recipe", _SCHEMA)
    document_id = _insert_document(conn, content="how to make pasta")
    enricher = _FakeEnricher(set_names=("recipe",), keyword="pasta")

    report = enrich_documents(conn, _settings(tmp_path), enricher)

    row = conn.execute(
        "SELECT summary, keywords, enriched_hash FROM documents WHERE id = ?", (document_id,)
    ).fetchone()
    assert row["summary"]
    assert "pasta" in row["keywords"]
    assert row["enriched_hash"]
    membership = conn.execute(
        "SELECT set_id FROM set_memberships WHERE document_id = ?", (document_id,)
    ).fetchone()
    assert membership["set_id"] == created.id
    assert report.documents_enriched == 1
    assert report.memberships_written == 1


def test_reenriching_preserves_extracted_json_for_a_set_that_stays_matched(
    tmp_path: Path,
) -> None:
    # A blind delete-and-reinsert of set_memberships on every re-enrichment (e.g. after an
    # EXTRACTION_MODEL change) would wipe out whatever Task 3 wrote to extracted_json for a set
    # the document still belongs to, forcing a needless re-extraction.
    conn = _open(tmp_path)
    create_set(conn, "recipe", "a cooking recipe", _SCHEMA)
    create_set(conn, "outreach", "an outreach contact", _SCHEMA)
    document_id = _insert_document(conn)
    enrich_documents(
        conn,
        _settings(tmp_path, extraction_model="qwen3:8b"),
        _FakeEnricher(set_names=("recipe", "outreach")),
    )
    recipe = get_set(conn, "recipe")
    assert recipe is not None
    conn.execute(
        "UPDATE set_memberships SET extracted_json = ? WHERE document_id = ? AND set_id = ?",
        ('{"a": 1}', document_id, recipe.id),
    )
    conn.commit()

    # Re-enrich under a different model, now matching only "recipe".
    enrich_documents(
        conn,
        _settings(tmp_path, extraction_model="other-model"),
        _FakeEnricher(set_names=("recipe",)),
    )

    rows = conn.execute(
        "SELECT set_id, extracted_json FROM set_memberships WHERE document_id = ?",
        (document_id,),
    ).fetchall()
    assert [row["set_id"] for row in rows] == [recipe.id]
    assert rows[0]["extracted_json"] == '{"a": 1}'


def test_invented_set_name_is_discarded_and_counted(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    _insert_document(conn)
    enricher = _FakeEnricher(set_names=("does-not-exist",))

    report = enrich_documents(conn, _settings(tmp_path), enricher)

    assert report.unknown_sets_discarded == 1
    assert report.memberships_written == 0
    assert conn.execute("SELECT * FROM set_memberships").fetchall() == []


def test_multiple_keywords_are_stored_comma_separated(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    document_id = _insert_document(conn)

    enrich_documents(
        conn, _settings(tmp_path), _FakeEnricher(keywords=("pasta", "dinner", "italian"))
    )

    row = conn.execute("SELECT keywords FROM documents WHERE id = ?", (document_id,)).fetchone()
    assert row["keywords"] == "pasta, dinner, italian"


def test_set_name_matching_is_case_sensitive(tmp_path: Path) -> None:
    # A case-insensitive fallback (e.g. looking up name.lower()) would match "Recipe" against
    # the stored "recipe" here; this must fail closed like any other unknown name instead.
    conn = _open(tmp_path)
    create_set(conn, "recipe", "a cooking recipe", _SCHEMA)
    _insert_document(conn)
    enricher = _FakeEnricher(set_names=("Recipe",))

    report = enrich_documents(conn, _settings(tmp_path), enricher)

    assert report.unknown_sets_discarded == 1
    assert report.memberships_written == 0
    assert conn.execute("SELECT * FROM set_memberships").fetchall() == []


def test_empty_set_names_writes_no_memberships_and_is_not_an_error(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    _insert_document(conn)

    report = enrich_documents(conn, _settings(tmp_path), _FakeEnricher(set_names=()))

    assert report.documents_enriched == 1
    assert report.documents_failed == 0
    assert report.memberships_written == 0
    assert conn.execute("SELECT * FROM set_memberships").fetchall() == []


def test_selector_excludes_document_with_null_content(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    _insert_document(conn, content=None)

    report = enrich_documents(conn, _settings(tmp_path), _FakeEnricher())

    assert report.documents_seen == 0


def test_selector_excludes_document_with_failed_status(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    _insert_document(conn, status="failed")

    report = enrich_documents(conn, _settings(tmp_path), _FakeEnricher())

    assert report.documents_seen == 0


def test_selector_excludes_deleted_document(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    _insert_document(conn, deleted=True)

    report = enrich_documents(conn, _settings(tmp_path), _FakeEnricher())

    assert report.documents_seen == 0


def test_rerunning_skips_unchanged_documents(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    _insert_document(conn)
    settings = _settings(tmp_path)
    enrich_documents(conn, settings, _FakeEnricher())

    second = _FakeEnricher()
    report = enrich_documents(conn, settings, second)

    assert report.documents_enriched == 0
    assert report.documents_skipped == 1
    assert second.calls == []


def test_changing_the_model_reenriches_unchanged_content(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    _insert_document(conn)
    enrich_documents(conn, _settings(tmp_path, extraction_model="qwen3:8b"), _FakeEnricher())

    report = enrich_documents(
        conn, _settings(tmp_path, extraction_model="other-model"), _FakeEnricher()
    )

    assert report.documents_enriched == 1
    assert report.documents_skipped == 0


def test_one_document_failing_does_not_stop_later_documents_from_enriching(
    tmp_path: Path,
) -> None:
    # ref-ok is inserted (and so processed) before ref-boom: this catches a missing
    # conn.commit() after a successful document, which the previous ordering could not, since
    # rolling back the later failure would also erase an *uncommitted* earlier success.
    conn = _open(tmp_path)
    _insert_document(conn, "ref-ok", content="fine content")
    _insert_document(conn, "ref-boom", content="boom content")
    enricher = _FakeEnricher(fail_on="boom")

    report = enrich_documents(conn, _settings(tmp_path), enricher)

    assert report.documents_failed == 1
    assert report.documents_enriched == 1
    ok_id = conn.execute("SELECT id FROM documents WHERE source_ref = 'ref-ok'").fetchone()["id"]
    row = conn.execute("SELECT summary FROM documents WHERE id = ?", (ok_id,)).fetchone()
    assert row["summary"] is not None


def test_keywords_reach_fts_and_are_searchable(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    _insert_document(conn, content="some content")

    enrich_documents(conn, _settings(tmp_path), _FakeEnricher(keyword="zzzuniquekeyword"))

    rows = conn.execute(
        "SELECT rowid FROM documents_fts WHERE documents_fts MATCH 'zzzuniquekeyword'"
    ).fetchall()
    assert len(rows) == 1


def test_enrich_report_counts_match_the_operations_performed(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    settings = _settings(tmp_path)
    create_set(conn, "recipe", "a cooking recipe", _SCHEMA)
    _insert_document(conn, "ref-unchanged", content="stable content")
    # Enrich once so 'ref-unchanged' has a current enriched_hash before the run under test; the
    # other three documents are added afterwards so this warm-up run never touches them.
    enrich_documents(conn, settings, _FakeEnricher())
    _insert_document(conn, "ref-fails", content="boom content")
    _insert_document(conn, "ref-new", content="fresh content")
    _insert_document(conn, "ref-excluded", content=None)

    report = enrich_documents(
        conn, settings, _FakeEnricher(fail_on="boom", set_names=("recipe", "not-a-real-set"))
    )

    assert isinstance(report, EnrichReport)
    assert report.documents_seen == 3  # excludes ref-excluded
    assert report.documents_skipped == 1  # ref-unchanged
    assert report.documents_failed == 1  # ref-fails
    assert report.documents_enriched == 1  # ref-new
    assert report.memberships_written == 1
    assert report.unknown_sets_discarded == 1
