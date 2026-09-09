"""The re-author preview shows the work before anyone pays for it.

Lane B rewrites bodies that were copied out of a guidebook rather than authored.
It is expensive and it changes the corpus, so the preview exists to let the owner
read what would happen — which beats, against which source — before a single
paid call is made.

Nothing here writes to `data/`. The preview is read-only by construction.
"""

from __future__ import annotations

import pytest

from scripts.reauthor_preview import (
    EXCLUDED_CITIES,
    REAUTHOR_MODEL,
    grounding_claims,
    is_excluded,
    reauthor_request,
    render_preview,
    sentences,
    worst_copied,
)


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


def test_grounding_uses_the_source_passage_not_just_the_recorded_claims() -> None:
    """key_claims is a lossy summary, and grounding on it alone refuses good rewrites.

    Measured on the live sample: a rewrite that mentioned the bracing wind was
    refused because the beat's single claim omitted it — though the source passage
    says it plainly. The permitted fact set is the source, with claims appended.
    """
    beat = _beat(
        "body",
        "The breezes are bracing. Vincennes lies east and Boulogne west.",
        key_claims=["Author's observation: they are the lungs of Paris"],
    )
    claims = grounding_claims(beat)

    assert claims[0] == "The breezes are bracing."
    assert "Vincennes lies east and Boulogne west." in claims
    # The recorded claim survives — it carries framing the passage does not.
    assert "Author's observation: they are the lungs of Paris" in claims


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


def test_london_is_excluded_from_the_work() -> None:
    """London is junk data, not a backlog. Nothing spends money rewriting it."""
    assert "london" in EXCLUDED_CITIES
    assert is_excluded("london") is True
    assert is_excluded("paris") is False


def test_the_writer_is_the_accurate_model_not_the_cheap_one() -> None:
    """Accuracy is the requirement, and the whole backlog costs about $11 to rewrite.

    The judge stays on Haiku deliberately: `HaikuFaithfulnessChecker` was calibrated
    to zero fabricating acceptances on that model, and swapping it would throw the
    calibration away. The writer and the judge are different jobs.
    """
    assert REAUTHOR_MODEL == "claude-opus-5"

    from src.tour.verify import FAITHFULNESS_MODEL

    assert FAITHFULNESS_MODEL != REAUTHOR_MODEL


def test_the_request_sends_no_sampling_parameters() -> None:
    """Opus 5 rejects temperature/top_p/top_k with a 400.

    The re-author request is built here so the shape is pinned by a test rather
    than discovered by a failed paid call.
    """
    request = reauthor_request(source="A source sentence.", body="A copied body.")

    assert request["model"] == "claude-opus-5"
    assert "temperature" not in request
    assert "top_p" not in request
    assert "top_k" not in request
    # Adaptive thinking is on by default for this model; max_tokens must leave room
    # for it or the rewrite truncates mid-sentence.
    assert request["max_tokens"] >= 4000
    prompt = request["messages"][0]["content"]
    assert "A source sentence." in prompt
    assert "A copied body." in prompt


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


def test_sentence_split_does_not_break_on_guidebook_abbreviations() -> None:
    """"No. 46" is an address, not the end of a sentence.

    Splitting there hands the entailment gate a fragment that cannot possibly be
    supported, which surfaces as a refusal the writer never earned.
    """
    text = "Cross rue du Faubourg St. Antoine and walk to No. 46, the restaurant. Then stop."
    assert sentences(text) == [
        "Cross rue du Faubourg St. Antoine and walk to No. 46, the restaurant.",
        "Then stop.",
    ]
