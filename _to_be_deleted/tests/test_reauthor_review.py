"""Recording a human decision about a re-authored beat.

The gate sorts; the person decides. This file pins what a decision IS: bound to
the exact text approved, attributed to a real reviewer, and refusing anything it
cannot honour.
"""

from __future__ import annotations

import json

import pytest

from scripts.reauthor_review import (
    DECISIONS,
    body_hash,
    decision_summary,
    is_stale,
    record_decision,
    review_order,
)


def _candidate(beat_id: str, after: str = "An authored body.", flags: int = 0) -> dict:
    return {
        "beat_id": beat_id,
        "poi_name": "Somewhere",
        "source_passage": "A source passage.",
        "body_before": "A lifted body.",
        "body_after": after,
        "ratio_before": 0.9,
        "ratio_after": 0.0,
        "ungrounded": [],
        "flags": flags,
    }


def test_a_decision_is_bound_to_the_text_that_was_approved() -> None:
    """Approval attaches to a body, not to a beat id.

    This is the pattern `validate_beats` already enforces for fact_check: a
    verified badge may not sit on text that changed underneath it.
    """
    records = [_candidate("b1")]
    record_decision(records, "b1", "approve", decided_by="Ada")

    row = records[0]
    assert row["decision"] == "approve"
    assert row["decided_by"] == "Ada"
    assert row["decided_at"]
    assert row["decided_body_hash"] == body_hash("An authored body.")
    assert is_stale(row) is False


def test_an_approval_goes_stale_when_the_body_moves() -> None:
    """Edit the rewrite after approving it and the approval no longer applies."""
    records = [_candidate("b1")]
    record_decision(records, "b1", "approve", decided_by="Ada")

    records[0]["body_after"] = "A different body entirely."

    assert is_stale(records[0]) is True


def test_an_undecided_candidate_is_not_stale() -> None:
    assert is_stale(_candidate("b1")) is False


def test_only_the_known_decisions_are_accepted() -> None:
    """A typo'd verdict must not silently become a state nothing handles."""
    records = [_candidate("b1")]
    with pytest.raises(ValueError, match="approve"):
        record_decision(records, "b1", "looks-fine", decided_by="Ada")
    assert DECISIONS == ("approve", "reject")


def test_deciding_on_an_unknown_beat_is_refused() -> None:
    with pytest.raises(KeyError, match="nope"):
        record_decision([_candidate("b1")], "nope", "approve", decided_by="Ada")


def test_a_decision_needs_a_named_reviewer() -> None:
    """`decided_by` is the record of who judged; an empty one is not a record."""
    with pytest.raises(ValueError, match="reviewer"):
        record_decision([_candidate("b1")], "b1", "approve", decided_by="  ")


def test_review_order_puts_the_doubted_and_the_undecided_first() -> None:
    """Decided rows sink: the queue is what still needs a person."""
    records = [
        _candidate("clean", flags=0),
        _candidate("doubted", flags=3),
        _candidate("done", flags=5),
    ]
    record_decision(records, "done", "approve", decided_by="Ada")

    assert [r["beat_id"] for r in review_order(records)] == ["doubted", "clean", "done"]


def test_summary_counts_what_is_left_to_do() -> None:
    records = [_candidate("a"), _candidate("b"), _candidate("c")]
    record_decision(records, "a", "approve", decided_by="Ada")
    record_decision(records, "b", "reject", decided_by="Ada")

    summary = decision_summary(records)

    assert summary == {"total": 3, "approved": 1, "rejected": 1, "undecided": 1, "stale": 0}


def test_records_round_trip_as_json() -> None:
    """Decisions persist to the same file the run wrote, so nothing is held in memory."""
    records = [_candidate("b1")]
    record_decision(records, "b1", "approve", decided_by="Ada")
    assert json.loads(json.dumps(records))[0]["decision"] == "approve"
