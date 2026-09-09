"""Scoring the conflict gate on defects it never saw.

The gate was written with two real conflicts on screen, so it catching those two is
not evidence about it. These tests pin the injections themselves: an injection that
silently fails to change the body would score as a catch and inflate recall, which is
the one way this measurement can lie in the flattering direction.
"""

from __future__ import annotations

from scripts.reauthor_triage_calibrate import (
    CHANNELS,
    calibrate,
    conflicted,
    inject_digit,
    inject_name,
    inject_unit_drop,
    inject_word_number,
)

_SOURCE = "The Carpathia landed 60 survivors at Pier 54, where Charles Garnier once stood."
_BEFORE = "The Carpathia landed 60 survivors at Pier 54, where Charles Garnier once stood."


def test_the_baseline_pair_is_clean() -> None:
    """A body identical to its source cannot disagree with it."""
    assert not conflicted(_SOURCE, _BEFORE)


def test_every_injection_actually_changes_the_body() -> None:
    """An injection that no-ops would be counted as caught and inflate recall."""
    for name, inject in CHANNELS.items():
        mutated = inject(_SOURCE, _BEFORE)
        assert mutated is not None and mutated != _BEFORE, name


def test_a_changed_figure_is_caught() -> None:
    assert conflicted(_SOURCE, inject_digit(_SOURCE, _BEFORE) or _BEFORE)


def test_a_substituted_name_token_is_caught() -> None:
    assert conflicted(_SOURCE, inject_name(_SOURCE, _BEFORE) or _BEFORE)


def test_a_spelled_out_figure_is_a_known_miss() -> None:
    """The gate compares digits. This channel exists to size the hole, not to pass."""
    mutated = inject_word_number(_SOURCE, "It has 54 rooms today.")
    assert mutated is not None
    assert not conflicted("It has 12 rooms today.", mutated)


def test_dropping_the_noun_beside_a_figure_removes_the_slot() -> None:
    """A number with no neighbour has nothing to be compared against."""
    assert inject_unit_drop(_SOURCE, _BEFORE) != _BEFORE


def test_recall_is_reported_per_channel_not_as_one_number() -> None:
    """One averaged figure would hide which kind of disagreement escapes."""
    result = calibrate([{"beat_id": "b", "source_passage": _SOURCE, "body_before": _BEFORE}])
    assert set(result["channels"]) == set(CHANNELS)
    assert result["clean_baseline"] == 1
