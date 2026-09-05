"""The re-author preview shows the work before anyone pays for it.

Lane B rewrites bodies that were copied out of a guidebook rather than authored.
It is expensive and it changes the corpus, so the preview exists to let the owner
read what would happen — which beats, against which source — before a single
paid call is made.

Nothing here writes to `data/`. The preview is read-only by construction.
"""

from __future__ import annotations

import pytest

from scripts.reauthor_preview import grounding_claims, render_preview, worst_copied


def _beat(body: str, source: str, **extra: object) -> dict:
    return {"beat_id": extra.pop("beat_id", "b1"), "poi_name": "Somewhere",
            "script_body": body, "source_passage": source, **extra}


def test_worst_copied_ranks_the_most_copied_first() -> None:
    """The queue is ordered by how copied a beat is, so the worst is fixed first."""
    lifted = "The tower was completed in 1889 for the World's Fair and stood for twenty years."
    beats = [
        _beat("Eiffel raised his frame that year and Paris planned to pull it down.",
              lifted, beat_id="rewritten"),
        _beat(lifted, lifted, beat_id="lifted"),
    ]
    rows = worst_copied(beats, limit=5)

    assert [r["beat_id"] for r in rows] == ["lifted"]
    assert rows[0]["ratio"] == pytest.approx(1.0)


def test_worst_copied_skips_beats_with_no_source() -> None:
    """An untraceable beat is a different defect; Lane B cannot re-ground it."""
    assert worst_copied([_beat("A body citing nothing.", "")], limit=5) == []


def test_worst_copied_honours_the_limit() -> None:
    """A preview is a sample; it never walks the whole corpus."""
    same = "One two three four five six seven eight nine ten eleven twelve."
    beats = [_beat(same, same, beat_id=f"b{i}") for i in range(10)]
    assert len(worst_copied(beats, limit=3)) == 3


def test_grounding_claims_prefer_the_beats_own_key_claims() -> None:
    """Where the extractor recorded claims, those are what the rewrite must entail."""
    beat = _beat("body", "source", key_claims=["Built in 1889.", "Meant to be temporary."])
    assert grounding_claims(beat) == ("Built in 1889.", "Meant to be temporary.")


def test_grounding_claims_fall_back_to_the_source_passage() -> None:
    """London's 553 copied beats carry no key_claims at all.

    The calibrated entailment gate takes claims, and the standard forbids building a
    second gate — so a beat with no claims is grounded against its own source
    passage instead of being skipped, which would exempt the whole city.
    """
    beat = _beat("body", "Sylvia Beach opened the shop in 1919. She published Ulysses.")
    claims = grounding_claims(beat)

    assert claims == (
        "Sylvia Beach opened the shop in 1919.",
        "She published Ulysses.",
    )


def test_grounding_claims_is_empty_when_there_is_nothing_to_ground_against() -> None:
    assert grounding_claims(_beat("body", "")) == ()


def test_preview_shows_the_body_against_its_source() -> None:
    """The owner judges the rewrite by reading both, so both must be on screen."""
    lifted = "The tower was completed in 1889 for the World's Fair."
    rows = worst_copied([_beat(lifted, lifted, beat_id="b7")], limit=1)

    out = render_preview("paris", rows)

    assert "b7" in out
    assert "The tower was completed in 1889" in out
    assert "100%" in out
    # A dry preview never claims a rewrite happened.
    assert "would be re-authored" in out
