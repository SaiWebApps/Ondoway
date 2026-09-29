"""Tests for scripts/ingest_corpus_compare.py — the district-port comparison.

Pinned so the copy count cannot silently report zero: a legacy beat that
copies nine words of its chunk is counted, a new beat that paraphrases is
not, and a new beat is checked against EVERY chunk its claims cite.
"""

from __future__ import annotations

from pathlib import Path

from scripts.ingest_corpus_compare import compare

CHUNK_A = (
    "The museum was designed by a famous architect and finished in the spring of 1959 at last."
)
CHUNK_B = "Critics at the time called the spiral a washing machine and a tea cup and a snail shell."


def _books(tmp_path: Path) -> Path:
    books = tmp_path / "books"
    (books / "book-a").mkdir(parents=True)
    (books / "book-b").mkdir(parents=True)
    (books / "book-a" / "ch1.txt").write_text(CHUNK_A, encoding="utf-8")
    (books / "book-b" / "ch9.txt").write_text(CHUNK_B, encoding="utf-8")
    return books


def _new(text: str, sources: list[tuple[str, str]], *, lenses=("a",), held=False) -> dict:
    return {
        "poi_name": "P", "beat_type": "anecdote", "lenses": list(lenses),
        "review": {"held": held}, "narration": {"text": text},
        "claims": [{"sources": [{"source_id": s, "chunk": c, "span": "x"} for s, c in sources]}],
    }


def test_copying_is_counted_against_each_beats_own_chunks(tmp_path):
    """Mutant: a new beat checked against only its first cited chunk (the
    copy from book-b is then missed)."""
    books = _books(tmp_path)
    legacy = [
        {"poi_name": "P", "lens": "a", "book_slug": "book-a", "source_chunk_slug": "ch1",
         "script_body": "It was designed by a famous architect and finished in the spring.",
         "key_claims": ["one"]},
        {"poi_name": "Elsewhere", "lens": "b", "book_slug": "book-a",
         "source_chunk_slug": "ch1", "script_body": CHUNK_A},
    ]
    new = [
        _new("A well-known architect drew it; it opened in 1959.", [("book-a", "ch1")]),
        _new("People said the spiral a washing machine and a tea cup and a snail.",
             [("book-a", "ch1"), ("book-b", "ch9")], lenses=("a", "b")),
        _new("Held and out of tours.", [("book-a", "ch1")], held=True),
    ]

    out = compare(new, legacy, books)

    assert out["places"] == ["P"]
    assert out["copying"]["legacy_all"] == {
        "beats": 1, "any_8_word_run": 1, "ratio_10pct": 1, "ratio_70pct": 0
    }
    assert out["copying"]["new"]["any_8_word_run"] == 1  # the book-b copy is caught
    assert out["copying"]["legacy_like_for_like"]["beats"] == 1
    assert out["shape"]["new_file"]["beats"] == 3
    assert out["shape"]["new_publishable"]["beats"] == 2
    assert out["shape"]["new_file"]["distinct_lenses"] == 2
    assert out["provenance"]["new_beats_citing_two_books"] == 1
    assert out["provenance"]["legacy_key_claims"] == 1
