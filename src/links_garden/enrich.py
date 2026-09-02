"""Summary, keyword and set-membership enrichment via ollama, and the `garden enrich` write path.

Mirrors `embed.py`: same client injection, same per-document transactions, same fail-loudly
stance on an unreachable ollama. There is no budget to protect here either, and a half-enriched
corpus would silently rank search results wrongly forever instead of just failing the run.

`Enricher` also does per-set field extraction (`extract`), reusing this client and document
cap. `extract_sets.py` drives that method over `set_memberships`; it lives in its own file
because it walks a different table with a different write path, not because the client differs.
"""

import hashlib
import json
import logging
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from links_garden.chunk import build_document_text
from links_garden.config import Settings
from links_garden.embed import SELECTOR_SQL
from links_garden.sets import SetDefinition, list_sets

logger = logging.getLogger(__name__)

# Measured against real ollama: a short document survives with the default context (3s,
# instructions intact), but a 40k-char document silently drops the instructions and set list
# from the *front* of the context (20s) -- ollama truncates the front on overflow, so the model
# invents a set name from the document's own leftover text instead of raising. Raising num_ctx
# to 16384 fixes it (184s) but that is seven hours over the user's 135 documents for a
# two-sentence summary, ten keywords and a set verdict. Capping the document text is cheaper:
# the user's largest document is 23,891 characters, and neither a summary nor a recipe/not-recipe
# call needs all of it.
_MAX_DOCUMENT_CHARS = 6000

# Measured against ollama at 13.3s/document with think=False on a short document; a long
# document plus a full set list runs longer, and a 4th real document timed out at 120s. 180s
# leaves real headroom without hiding a genuinely dead ollama for too long.
_TIMEOUT = 180.0

_RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "sets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "matches": {"type": "boolean"},
                    "evidence": {"type": "string"},
                },
                "required": ["name", "matches", "evidence"],
            },
        },
    },
    "required": ["summary", "keywords", "sets"],
}

# The model occasionally groups keywords under a label instead of returning bare terms, e.g.
# "English keywords: a, b, c" or a bare "Keywords:" as one array element -- measured on the
# real corpus, roughly a third of documents. A prompt fix alone isn't a guarantee, so this also
# strips the label and splits the group defensively before anything is stored. The leading
# language word is optional (a bare "Keywords:"/"Mots-clés:" is still a label), and "mots-clés"
# is matched alongside "keywords" since the corpus is bilingual.
_KEYWORD_LABEL = re.compile(
    r"^\s*(?:\w[\w /]*)?(?:keywords?|mots?[-\s]cl[ée]s?)\s*:\s*", re.IGNORECASE
)


def _split_keywords(raw: Sequence[str]) -> tuple[str, ...]:
    keywords: list[str] = []
    for item in raw:
        stripped = _KEYWORD_LABEL.sub("", item)
        if stripped == item:
            if item.strip():
                keywords.append(item.strip())
        else:
            # An unlabelled comma still just means one keyword phrase that contains a comma
            # (e.g. "Washington, D.C."); only split when a label showed this was actually a
            # group. Do not "fix" this to split every element on commas.
            keywords.extend(part.strip() for part in stripped.split(",") if part.strip())
    return tuple(keywords)


@dataclass(frozen=True)
class Enrichment:
    summary: str
    keywords: tuple[str, ...]
    set_names: tuple[str, ...]


class Enricher:
    """ollama-backed enrichment client, injectable for tests."""

    def __init__(self, settings: Settings, *, client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._client = client if client is not None else httpx.Client()

    def enrich(self, text: str, sets: Sequence[SetDefinition]) -> Enrichment:
        """One call returning summary, keywords and set membership together, so a 135-document
        pass reads each document's text once rather than twice for no benefit.
        """
        url = f"{self._settings.ollama_url}/api/chat"
        text = text[:_MAX_DOCUMENT_CHARS]
        try:
            response = self._client.post(
                url,
                json={
                    "model": self._settings.extraction_model,
                    "messages": [{"role": "user", "content": _build_prompt(text, sets)}],
                    "format": _RESPONSE_SCHEMA,
                    # Measured against ollama: think=False takes 13.3s and produces clean,
                    # deduplicated keywords. think=True takes 51.9s (4x) for worse output
                    # (duplicate keywords). Do not turn this back on without re-measuring.
                    "think": False,
                    "stream": False,
                    # ollama defaults to temperature 0.8. Classification must give the same
                    # answer for the same document every time -- enriched_hash skips a document
                    # whose text hasn't changed, which assumes the same input always yields the
                    # same output, and set_memberships is a list the user acts on without
                    # re-auditing it. temperature 0 also reads as more conservative on
                    # borderline set-membership calls, which is the direction wanted here.
                    "options": {"temperature": 0},
                },
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
            payload: Any = response.json()
            parsed: Any = json.loads(payload["message"]["content"])
            # The model evaluates every candidate set and reports `matches` explicitly, rather
            # than the caller inferring "matched" from array membership: with array membership
            # as the signal, the model sometimes appended a considered-but-rejected set anyway,
            # with evidence that plainly said it did not match. `evidence` is discarded here;
            # its only job is forcing the model to justify each verdict, not to be kept.
            enrichment = Enrichment(
                summary=parsed["summary"],
                keywords=_split_keywords(parsed["keywords"]),
                set_names=tuple(item["name"] for item in parsed["sets"] if item["matches"]),
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"could not get enrichment from ollama at {url} "
                f"for model {self._settings.extraction_model!r}: {exc}"
            ) from exc
        return enrichment

    def extract(self, text: str, schema: dict[str, object]) -> dict[str, object]:
        """Pull one set's own fields out of a document, constrained to that set's JSON Schema.

        Separate from `enrich`: that call classifies a document against every set's
        description in one shot, this pulls one already-matched set's fields out with a
        different `format` (the set's own schema, not the fixed enrichment envelope) and a
        different prompt. Same client, same document cap, same fail-loudly stance.
        """
        url = f"{self._settings.ollama_url}/api/chat"
        text = text[:_MAX_DOCUMENT_CHARS]
        try:
            response = self._client.post(
                url,
                json={
                    "model": self._settings.extraction_model,
                    "messages": [{"role": "user", "content": _build_extraction_prompt(text)}],
                    "format": schema,
                    "think": False,
                    "stream": False,
                    "options": {"temperature": 0},
                },
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
            payload: Any = response.json()
            parsed: Any = json.loads(payload["message"]["content"])
            if not isinstance(parsed, dict):
                raise ValueError(f"expected a JSON object, got {type(parsed).__name__}")
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"could not extract fields from ollama at {url} "
                f"for model {self._settings.extraction_model!r}: {exc}"
            ) from exc
        return parsed

    def check(self) -> bool:
        """Whether `settings.extraction_model` is pulled. Matches `Embedder.check`."""
        url = f"{self._settings.ollama_url}/api/tags"
        try:
            response = self._client.get(url, timeout=_TIMEOUT)
            response.raise_for_status()
            payload: Any = response.json()
            names = {model["name"].split(":")[0] for model in payload["models"]}
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return False
        return self._settings.extraction_model.split(":")[0] in names


def _build_prompt(text: str, sets: Sequence[SetDefinition]) -> str:
    # Explicitly asking for both languages is what produced cross-language keywords like
    # "automatisation" alongside "automation" in the measured run; the corpus is mixed and the
    # user's own queries are English while their notes are French.
    if sets:
        set_lines = "\n".join(f"- {s.name}: {s.description}" for s in sets)
        sets_instructions = (
            "Evaluate every one of these sets, one entry per set with that exact name:\n"
            f"{set_lines}\n\n"
            "A set describes the KIND OF THING a document IS, not its topic. A document "
            "about a subject is not a member of a set of that subject's instances: an "
            "article about chefs is not a recipe, a guide to making TikTok videos is not a "
            "tiktok_influenceur because it describes a technique rather than being a "
            "specific creator's own profile, and a recipe blog's homepage is not a recipe "
            "because it lists dishes rather than being one.\n\n"
            "Matching zero sets is the normal outcome for most documents, not a failure to "
            "classify. Set matches to true only if the document itself is a specific "
            "instance of that set's subject, not merely because it mentions, resembles, "
            "shares a structure with, or is substantively about that subject. A numbered "
            "how-to or troubleshooting guide (for example, programming steps) is not a "
            "recipe unless it is actually about preparing food. An article that discusses "
            "TikTok, even at length, is not a tiktok_influenceur unless it IS a creator's "
            "own profile. Your evidence must name the specific entity the set is about -- "
            "for a creator set, the creator's own handle or account; for a recipe, the dish "
            "and its steps -- and matches is false if your evidence cannot name that "
            "entity. State the specific sentence or fact in the document that is your "
            "evidence either way, then set matches accordingly."
        )
    else:
        sets_instructions = "No sets are defined. Return an empty list for sets."
    return (
        "You are enriching a document for a personal knowledge base.\n\n"
        f"{sets_instructions}\n\n"
        "Write a concise summary of the document, and keywords describing it in both English "
        "and French since the corpus mixes both languages. Return keywords as a flat list: "
        "each array element is exactly one bare keyword phrase, never a heading, label or "
        "group like 'English keywords:' or 'French keywords:'.\n\n"
        f"Document:\n{text}"
    )


def _build_extraction_prompt(text: str) -> str:
    return (
        "Extract the fields defined by the JSON Schema from this document. Leave a field out, "
        "or set it to null, if the document does not say -- never invent a value.\n\n"
        f"Document:\n{text}"
    )


class EnricherLike(Protocol):
    """The one method `enrich_documents` calls. Lets tests fake enrichment without a live client."""

    def enrich(self, text: str, sets: Sequence[SetDefinition]) -> Enrichment: ...


@dataclass
class EnrichReport:
    """Counts of what one `enrich_documents` call actually did."""

    documents_seen: int = 0
    documents_enriched: int = 0
    documents_skipped: int = 0
    documents_failed: int = 0
    memberships_written: int = 0
    unknown_sets_discarded: int = 0


def enrich_documents(
    conn: sqlite3.Connection, settings: Settings, enricher: EnricherLike
) -> EnrichReport:
    """Summarize, keyword and classify every qualifying document, skipping one already current.

    Per-document transactions: one document's failure must not roll back writes already
    committed for documents processed earlier in this run.
    """
    report = EnrichReport()
    sets = list_sets(conn)
    sets_by_name = {set_.name: set_ for set_ in sets}
    rows = conn.execute(
        f"SELECT d.id, d.message_text, d.content, d.enriched_hash FROM documents d "
        f"WHERE {SELECTOR_SQL}"
    ).fetchall()
    for row in rows:
        report.documents_seen += 1
        text = build_document_text(row["message_text"], row["content"])
        digest = _digest(text, settings.extraction_model)
        if row["enriched_hash"] == digest:
            report.documents_skipped += 1
            continue
        try:
            written, discarded = _enrich_document(
                conn, row["id"], text, digest, enricher, sets, sets_by_name
            )
        except Exception:
            logger.exception("failed to enrich document %d", row["id"])
            conn.rollback()
            report.documents_failed += 1
            continue
        report.documents_enriched += 1
        report.memberships_written += written
        report.unknown_sets_discarded += discarded
    return report


def _enrich_document(
    conn: sqlite3.Connection,
    document_id: int,
    text: str,
    digest: str,
    enricher: EnricherLike,
    sets: Sequence[SetDefinition],
    sets_by_name: dict[str, SetDefinition],
) -> tuple[int, int]:
    """Enrich one document and reconcile its set memberships with the fresh classification.

    Enrichment happens before any write, so a failure here leaves the previous summary, keywords
    and memberships in place rather than deleting them for nothing. A membership that stays
    matched across a re-enrichment (e.g. after an EXTRACTION_MODEL change) is left untouched
    rather than replaced, so it keeps whatever `extracted_json` Task 3 wrote for it instead of
    resetting to pending.
    """
    enrichment = enricher.enrich(text, sets)
    matched_ids: set[int] = set()
    discarded = 0
    for name in enrichment.set_names:
        set_ = sets_by_name.get(name)
        if set_ is None:
            # The model invents a set name that doesn't exist; counted rather than failing the
            # whole document over a single bad field.
            discarded += 1
            continue
        assert set_.id is not None
        matched_ids.add(set_.id)
    existing_ids = {
        row["set_id"]
        for row in conn.execute(
            "SELECT set_id FROM set_memberships WHERE document_id = ?", (document_id,)
        )
    }
    conn.execute(
        "UPDATE documents SET summary = ?, keywords = ?, enriched_hash = ? WHERE id = ?",
        (enrichment.summary, ", ".join(enrichment.keywords), digest, document_id),
    )
    for set_id in existing_ids - matched_ids:
        conn.execute(
            "DELETE FROM set_memberships WHERE document_id = ? AND set_id = ?",
            (document_id, set_id),
        )
    written = matched_ids - existing_ids
    conn.executemany(
        "INSERT INTO set_memberships (document_id, set_id) VALUES (?, ?)",
        [(document_id, set_id) for set_id in written],
    )
    conn.commit()
    return len(written), discarded


def _digest(text: str, model: str) -> str:
    """Mirrors `embed._digest`'s formula exactly, so changing `EXTRACTION_MODEL` re-enriches
    every document instead of leaving stale summaries and keywords from a different model in
    place.
    """
    return hashlib.sha256(f"{model}\x00{text}".encode()).hexdigest()
