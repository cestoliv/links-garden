"""Text chunking for embedding. Standard library only."""

import re
from collections.abc import Iterator

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_SENTENCE_BREAK = re.compile(r"[.!?][\"')\]]?\s")


def chunk_text(text: str, *, target_chars: int = 4000, overlap_chars: int = 400) -> list[str]:
    """Split `text` into overlapping chunks sized for embedding.

    4000 characters approximates 1000 tokens for this corpus; `bge-m3` accepts up to 8192, so
    exact tokenization would buy nothing here. Splitting prefers a paragraph break, then a
    sentence break, and only then cuts mid-word, because a chunk that ends mid-word embeds worse
    than one that ends at a natural boundary.
    """
    if target_chars <= 0:
        # A non-positive target never advances `_segment_bounds`'s walk: `limit` would equal
        # `start` on every iteration, looping forever over a real corpus.
        raise ValueError("target_chars must be positive")
    if not text.strip():
        return []
    return [
        text[max(0, start - overlap_chars) : end]
        for start, end in _segment_bounds(text, target_chars)
    ]


def _segment_bounds(text: str, target_chars: int) -> Iterator[tuple[int, int]]:
    """Partition `text` end to end into `(start, end)` spans, each at most `target_chars` long.

    The spans are gapless and cover every character exactly once; `chunk_text` widens each one
    backward by `overlap_chars` afterwards. A span always ends past its `start`, even mid-word,
    so the walk always makes progress and terminates on input with no boundary at all.
    """
    start = 0
    length = len(text)
    while start < length:
        limit = min(start + target_chars, length)
        end = length if limit == length else _find_break(text, start, limit)
        yield start, end
        start = end


def _find_break(text: str, start: int, limit: int) -> int:
    """The best split point in `text[start:limit]`: a paragraph break, else a sentence break,
    else `limit` itself, a hard cut.
    """
    return (
        _last_break_end(_PARAGRAPH_BREAK, text, start, limit)
        or _last_break_end(_SENTENCE_BREAK, text, start, limit)
        or limit
    )


def _last_break_end(pattern: re.Pattern[str], text: str, start: int, limit: int) -> int | None:
    """The end of the last regex match within `text[start:limit]`, or `None` if there is none
    past `start` — a match ending at `start` itself would not move the walk forward.
    """
    end = None
    for match in pattern.finditer(text, start, limit):
        end = match.end()
    return end if end is not None and end > start else None


def build_document_text(message_text: str | None, content: str | None) -> str:
    """Join the user's own commentary and the extracted content, commentary first.

    This is the fix for step 3's whole-branch review finding: `message_text` holds the user's
    own words about why they saved a link, present on roughly 75 of 500 Signal links, but
    `documents_fts` indexes `content` and not this column. Without this join, those words are
    written to disk and never searchable. Either side may be absent.
    """
    parts = [part for part in (message_text, content) if part is not None and part.strip()]
    return "\n\n".join(parts)
