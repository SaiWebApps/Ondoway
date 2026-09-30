"""`gates.street_addresses` and `make ingest-address-report FILE=` (OWNER RULING
2026-09-30: narration never voices a street address unless the story is about it).
The P4 rule is a prompt rule, not a refusing gate, so this is how a real run shows
whether it holds: job-B replay r2 voiced an address in 6 of its story beats. $0.
"""

from __future__ import annotations

import json

import scripts.ingest_address_report as report
from src.ingest import gates
from tests.ingest_helpers import minimal_beat, stamp


def test_street_addresses_finds_numbered_streets_and_avenues_only():
    """[undo: drop the "at/on <n>th Street" alternative → the Met line loses a match]"""
    assert gates.street_addresses(
        "This is 1000 Fifth Avenue, at East 82nd Street. It was founded in 1870."
    ) == ["1000 Fifth Avenue", "at East 82nd Street"]
    assert gates.street_addresses("It moved from its first home on 54th Street.") == [
        "on 54th Street"
    ]
    assert gates.street_addresses("Frick Madison stands at 945 Madison Ave.") == [
        "945 Madison Ave"
    ]
    for clean in (
        "Fifth Avenue was once called Millionaires' Row.",
        "The museum opened in 1959 after 13 years of delay.",
        "Over six million people come through each year.",
    ):
        assert gates.street_addresses(clean) == [], clean


def _beat(slug: str, text: str, beat_type: str = "anecdote") -> dict:
    beat = stamp(minimal_beat(story_slug=slug, beat_id=f"new_york/test-poi/{slug}",
                              beat_type=beat_type))
    beat["narration"]["text"] = text
    return beat


def test_the_report_counts_story_beats_apart_from_practicalities(tmp_path, capsys):
    beats = [
        _beat("met", "This is 1000 Fifth Avenue, at East 82nd Street."),
        _beat("first-home", "The collection first hung on 54th Street."),
        _beat("clean", "The building was finished in 1959."),
        _beat("visiting", "The museum is at 945 Madison Ave.", "practicalities"),
    ]
    path = tmp_path / "beats.json"
    path.write_text(json.dumps(beats), encoding="utf-8")

    assert report.main(["--file", str(path)]) == 0

    out = capsys.readouterr().out
    assert "STORY met: 1000 Fifth Avenue | at East 82nd Street" in out
    assert "STORY first-home: on 54th Street" in out
    assert "PRACTICAL visiting: 945 Madison Ave" in out
    assert "clean" not in out
    assert "beats=4 story_with_address=2 practicalities_with_address=1" in out
