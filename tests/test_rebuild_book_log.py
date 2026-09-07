"""The book log is rebuilt from the beats, and has to survive what is on disk.

Three shapes exist in two cities, and the reason to care is not tidiness: the
extractor's duplicate refuse reads `books_processed`, so a city whose log is shaped
differently has no protection against extracting a chunk twice.
"""

from __future__ import annotations

from scripts.rebuild_book_log import (
    check,
    derive_from_beats,
    existing_entries,
    match_old_entry,
    rebuild,
)

_BEATS = [
    {
        "beat_id": "1",
        "book_slug": "lonely-planet-new-york-city",
        "source_chunk_slug": "chunk-07-upper-east-side",
        "poi_name": "Frick Collection",
        "source_attribution": {"book_title": "Lonely Planet New York City", "author": "LP"},
    },
    {
        "beat_id": "2",
        "book_slug": "lonely-planet-new-york-city",
        "source_chunk_slug": "chunk-07-upper-east-side",
        "poi_name": "Neue Galerie New York",
        "source_attribution": {"book_title": "Lonely Planet New York City", "author": "LP"},
    },
    {
        "beat_id": "3",
        "book_slug": "lonely-planet-new-york-city",
        "source_chunk_slug": "legacy_ambiguous",
        "poi_name": "Nowhere",
        "source_attribution": {"book_title": "Lonely Planet New York City", "author": "LP"},
    },
]


def test_the_log_is_derived_from_the_beats_it_describes() -> None:
    """Counts and POI lists come from the corpus, not from what the log claimed."""
    books = derive_from_beats(_BEATS)
    chunk = books["lonely-planet-new-york-city"]["chunks"]["chunk-07-upper-east-side"]
    assert chunk["beats_extracted"] == 2
    assert chunk["pois_touched"] == {"Frick Collection", "Neue Galerie New York"}
    assert books["lonely-planet-new-york-city"]["book_title"] == "Lonely Planet New York City"


def test_a_beat_naming_no_chunk_is_described_by_no_entry() -> None:
    """`legacy_ambiguous` is a sentinel, not a chunk. Inventing an entry for it would
    make the log claim a source that cannot be re-extracted."""
    books = derive_from_beats(_BEATS)
    assert "legacy_ambiguous" not in books["lonely-planet-new-york-city"]["chunks"]


def test_all_three_on_disk_shapes_are_read() -> None:
    """The documented list, New York's book-slug mapping, and one book whose
    `chunks_processed` is an integer count rather than a list."""
    log = {
        "city": "new_york",
        "books_processed": [
            {
                "book_title": "Wikipedia",
                "chunks_processed": [{"chunk": "poi-rev-1", "processed_at": "then"}],
            },
            {"book_title": "The Movie Lover's Guide", "chunks_processed": 12},
        ],
        "lonely-planet-new-york-city": {"chunk-07": {"processed_at": "2026-06-15"}},
    }
    found = {(book, chunk) for book, chunk, _ in existing_entries(log)}
    assert ("Wikipedia", "poi-rev-1") in found
    assert ("lonely-planet-new-york-city", "chunk-07") in found
    # The integer variant names no chunk, so it contributes none rather than crashing.
    assert not any(book == "The Movie Lover's Guide" for book, _ in found)


def test_a_book_is_found_by_its_title_when_the_slug_does_not_match() -> None:
    """The Paris log keys on "Frommer's 24 Great Walks in Paris" where its beats carry
    `frommers-24-great-walks`. No canonicalisation of punctuation bridges a dropped
    "in Paris" and an apostrophe, so both identifiers are compared."""
    existing = [("Frommer's 24 Great Walks in Paris", "chunk-01", {"processed_at": "then"})]
    found, how = match_old_entry(
        "frommers-24-great-walks", "Frommer's 24 Great Walks in Paris", "chunk-01", existing
    )
    assert found == {"processed_at": "then"}
    assert how == "exact"


def test_an_abbreviated_chunk_key_is_matched_by_prefix() -> None:
    """New York logs `chunk-07` for beats that say `chunk-07-upper-east-side`."""
    existing = [("lonely-planet-new-york-city", "chunk-07", {"processed_at": "2026-06-15"})]
    found, how = match_old_entry(
        "lonely-planet-new-york-city", "Lonely Planet", "chunk-07-upper-east-side", existing
    )
    assert found == {"processed_at": "2026-06-15"}
    assert how.startswith("prefix")


def test_an_ambiguous_prefix_carries_nothing_rather_than_guessing() -> None:
    """Stamping one chunk's date onto another's beats is worse than having no date."""
    existing = [
        ("book", "chunk-07", {"processed_at": "a"}),
        ("book", "chunk-07-upper", {"processed_at": "b"}),
    ]
    found, how = match_old_entry("book", "Book", "chunk-07-upper-east-side", existing)
    assert found is None
    assert how.startswith("ambiguous")


def test_the_rebuilt_log_is_checked_against_the_corpus_it_describes() -> None:
    """The reason to rebuild rather than translate a shape: the result is checkable.

    A translation preserves drift between log and corpus faithfully; this catches it.
    """
    rebuilt, _ = rebuild("new_york", _BEATS, {})
    assert check(rebuilt, _BEATS) == []

    rebuilt["books_processed"][0]["chunks_processed"].append(
        {"chunk": "chunk-99-invented", "beats_extracted": 3, "pois_touched": []}
    )
    problems = check(rebuilt, _BEATS)
    assert any("no beats" in p for p in problems)


def test_the_rebuild_carries_what_only_the_log_knows() -> None:
    """`processed_at` and the POI lists a beat cannot prove survive the rebuild."""
    log = {
        "lonely-planet-new-york-city": {
            "chunk-07": {"processed_at": "2026-06-15", "pois_created": ["Neue Galerie New York"]}
        }
    }
    rebuilt, notes = rebuild("new_york", _BEATS, log)
    entry = rebuilt["books_processed"][0]["chunks_processed"][0]
    assert entry["processed_at"] == "2026-06-15"
    assert entry["pois_created"] == ["Neue Galerie New York"]
    # And the count is the corpus's, not the old log's claim about it.
    assert entry["beats_extracted"] == 2
    assert any("carried" in n for n in notes)
