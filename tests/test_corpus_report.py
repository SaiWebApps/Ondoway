"""The corpus report counts a city's beats from the files that own them.

The report answers two questions the owner cannot otherwise see: how good the
beats are, and where the city is thin. This file pins the quality half — the
fact-check mix, the length-class mix, and the verbatim ratio that says how much
of a beat was copied out of its source rather than authored.

The verbatim ratio is the one number with a threshold behind it: a body sharing
70% of its 8-word shingles with its source passage is copied, not written. An
8-word run is evidence of copying; a 5-word run is ordinary phrasing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.corpus_report import (
    VERBATIM_THRESHOLD,
    fact_check_buckets,
    length_class_buckets,
    quality_report,
    verbatim_ratio,
)

_PASSAGE = (
    "The tower was completed in 1889 for the World's Fair and was intended to "
    "stand for only twenty years before being dismantled."
)


def test_verbatim_ratio_flags_a_copied_body() -> None:
    """A body lifted straight out of the passage scores 1.0."""
    assert verbatim_ratio(_PASSAGE, _PASSAGE) == pytest.approx(1.0)
    assert verbatim_ratio(_PASSAGE, _PASSAGE) >= VERBATIM_THRESHOLD


def test_verbatim_ratio_ignores_a_rewritten_body() -> None:
    """Same facts, new sentences: below the threshold, so Lane B leaves it alone."""
    rewritten = (
        "Gustave Eiffel raised his iron frame for the exposition of 1889, and "
        "Paris fully expected to pull it down again two decades later."
    )
    assert verbatim_ratio(rewritten, _PASSAGE) < VERBATIM_THRESHOLD


def test_verbatim_ratio_of_a_beat_with_no_source_is_zero() -> None:
    """A beat carrying no source passage cannot be judged copied."""
    assert verbatim_ratio(_PASSAGE, "") == 0.0
    assert verbatim_ratio("", _PASSAGE) == 0.0


def test_missing_fact_check_block_buckets_as_never_checked() -> None:
    """London's beats carry no fact_check block at all; they are not 'unverified'."""
    beats = [
        {"fact_check": {"status": "verified"}},
        {"fact_check": {}},
        {},
    ]
    buckets = fact_check_buckets(beats)
    assert buckets["verified"] == 1
    assert buckets["never-checked"] == 2


def test_missing_length_class_buckets_as_not_classified() -> None:
    """Paris carries beat_length_class on 1068 of 1562 beats; the rest are not a class."""
    beats = [
        {"beat_length_class": "mid"},
        {"beat_length_class": ""},
        {},
    ]
    buckets = length_class_buckets(beats)
    assert buckets["mid"] == 1
    assert buckets["not-classified"] == 2


def test_paris_quality_counts_match_the_file() -> None:
    """The report's totals are the corpus's, measured against the real Paris file."""
    beats_path = Path(__file__).resolve().parents[1] / "data" / "paris" / "beats.json"
    if not beats_path.is_file():
        pytest.skip("paris corpus not present on this machine")
    beats = json.loads(beats_path.read_text(encoding="utf-8"))

    report = quality_report(beats)

    assert report["total_beats"] == 1562
    assert report["fact_check"]["verified"] == 407
    assert report["fact_check"]["disputed"] == 21
    assert report["fact_check"]["never-checked"] == 597
    # Measured this session with an 8-word shingle at a 0.70 threshold.
    assert report["verbatim"]["flagged"] == 246
