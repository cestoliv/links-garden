from itertools import pairwise

import pytest

from links_garden.chunk import build_document_text, chunk_text


def test_empty_and_whitespace_only_input_yield_no_chunks() -> None:
    assert chunk_text("") == []
    assert chunk_text("   \n\t  ") == []


def test_short_text_yields_one_chunk_equal_to_the_input() -> None:
    text = "a short document, well under the target size."
    assert chunk_text(text, target_chars=4000) == [text]


def test_long_text_splits_into_multiple_chunks_within_the_size_ceiling() -> None:
    paragraph = "This is one sentence. Here is another sentence for good measure.\n\n"
    text = paragraph * 40

    chunks = chunk_text(text, target_chars=200, overlap_chars=20)

    assert len(chunks) > 1
    assert all(len(chunk) <= 220 for chunk in chunks)


def test_consecutive_chunks_overlap_by_the_configured_amount() -> None:
    text = "".join(f"sentence number {i} has some words. " for i in range(400))
    overlap_chars = 40

    chunks = chunk_text(text, target_chars=300, overlap_chars=overlap_chars)
    plain = chunk_text(text, target_chars=300, overlap_chars=0)

    assert len(chunks) > 2
    for first, second in pairwise(chunks):
        assert first[-overlap_chars:] == second[:overlap_chars]

    # The check above is symmetric: it passes just as well if the *previous* chunk were
    # widened forward into the next segment instead. Pin the direction against the
    # zero-overlap split, whose boundaries are the ground truth: only the first chunk is
    # untouched, and every later one is its plain segment with the previous tail prepended.
    assert chunks[0] == plain[0]
    for plain_chunk, widened_chunk in zip(plain[1:], chunks[1:], strict=True):
        assert widened_chunk.endswith(plain_chunk)
        assert len(widened_chunk) == len(plain_chunk) + overlap_chars


def test_splitting_prefers_a_sentence_boundary_over_a_hard_cut() -> None:
    sentence = "Alpha bravo charlie delta echo. "
    text = sentence * 20

    chunks = chunk_text(text, target_chars=100, overlap_chars=0)

    # 3 sentences (96 chars) fit under the 100-char target; a 4th would not. A hard cut at
    # 100 would land mid-word, 4 characters into the next sentence.
    assert chunks[0] == sentence * 3


def test_splitting_prefers_paragraph_boundaries_when_they_exist() -> None:
    first_paragraph = "First paragraph. " * 8
    second_paragraph = "Second paragraph. " * 8
    text = first_paragraph + "\n\n" + second_paragraph

    chunks = chunk_text(text, target_chars=160, overlap_chars=0)

    assert chunks[0] == first_paragraph + "\n\n"
    assert chunks[1] == second_paragraph


def test_no_paragraph_or_sentence_boundary_still_splits_by_hard_cut() -> None:
    text = "x" * 1000

    chunks = chunk_text(text, target_chars=200, overlap_chars=0)

    assert len(chunks) == 5
    assert all(len(chunk) == 200 for chunk in chunks)
    assert "".join(chunks) == text


def test_every_character_of_the_input_appears_in_some_chunk() -> None:
    text = "\n\n".join(
        f"Paragraph number {i} holds content specific to index {i} and nothing else."
        for i in range(50)
    )

    chunks = chunk_text(text, target_chars=120, overlap_chars=20)

    covered = bytearray(len(text))
    search_from = 0
    for chunk in chunks:
        position = text.index(chunk, search_from)
        covered[position : position + len(chunk)] = b"\x01" * len(chunk)
        search_from = position

    assert all(covered)


def test_build_document_text_joins_commentary_and_content_with_a_blank_line() -> None:
    result = build_document_text("why I saved this", "the extracted content")

    assert result == "why I saved this\n\nthe extracted content"


def test_build_document_text_handles_either_side_being_none_or_empty() -> None:
    assert build_document_text(None, "content only") == "content only"
    assert build_document_text("commentary only", None) == "commentary only"
    assert build_document_text("", "") == ""
    assert build_document_text(None, None) == ""


def test_a_very_long_single_word_does_not_loop_forever() -> None:
    text = "x" * 20_000

    chunks = chunk_text(text, target_chars=4000, overlap_chars=400)

    assert len(chunks) == 5
    assert all(len(chunk) <= 4400 for chunk in chunks)


def test_non_positive_target_chars_is_rejected_instead_of_looping_forever() -> None:
    with pytest.raises(ValueError, match="target_chars"):
        chunk_text("some text", target_chars=0)
    with pytest.raises(ValueError, match="target_chars"):
        chunk_text("some text", target_chars=-1)
