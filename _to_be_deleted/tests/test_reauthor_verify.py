"""Automatic verification of a re-authored beat, so a person sees only the doubt.

The project's own D1 is "throughput + triage, not blanket verification". A queue
of 524 rewrites for a human to read is blanket verification. This is the machine
resolving what it can resolve, and escalating only what it cannot.

Two independent models answer the same three questions. Agreement that a rewrite
adds nothing and drops nothing is a decision. Disagreement is, by definition, the
thing a person is for.
"""

from __future__ import annotations

import pytest

from scripts.reauthor_verify import (
    ESCALATE,
    PASS,
    VERIFY_MODELS,
    parse_verdict,
    reconcile,
    verify_request,
)


def _v(added: list[str] | None = None, dropped: list[str] | None = None) -> dict:
    return {"added_facts": added or [], "dropped_facts": dropped or []}


def test_two_different_models_are_asked() -> None:
    """One model checking its own work is not independence."""
    assert len(set(VERIFY_MODELS)) == 2
    assert "claude-opus-5" in VERIFY_MODELS


def test_the_request_carries_all_three_texts_and_no_sampling() -> None:
    """The judge cannot compare what it is not shown; Opus 5 rejects temperature."""
    request = verify_request(
        model="claude-opus-5", source="SRC.", before="OLD.", after="NEW."
    )
    prompt = request["messages"][0]["content"]

    assert "SRC." in prompt and "OLD." in prompt and "NEW." in prompt
    assert "temperature" not in request
    assert request["model"] == "claude-opus-5"


def test_clean_agreement_is_a_decision() -> None:
    """Both models finding nothing added and nothing dropped resolves the beat."""
    outcome = reconcile([_v(), _v()])

    assert outcome["status"] == PASS
    assert outcome["reason"] == ""


def test_an_added_fact_from_either_model_escalates() -> None:
    """Fabrication is the failure this exists to catch; one witness is enough."""
    outcome = reconcile([_v(added=["invented a date"]), _v()])

    assert outcome["status"] == ESCALATE
    assert "invented a date" in outcome["reason"]


def test_a_dropped_fact_escalates() -> None:
    outcome = reconcile([_v(), _v(dropped=["the €4,000 prize"])])

    assert outcome["status"] == ESCALATE
    assert "the €4,000 prize" in outcome["reason"]


def test_disagreement_escalates_even_when_one_model_is_happy() -> None:
    """Two models differing IS the unresolvable case, and it goes to the human."""
    outcome = reconcile([_v(added=["a claim about 1889"]), _v()])
    assert outcome["status"] == ESCALATE


def test_an_unreadable_answer_escalates_rather_than_passing() -> None:
    """A judge that could not be parsed has not cleared anything. Fail closed."""
    outcome = reconcile([_v(), None])

    assert outcome["status"] == ESCALATE
    assert "could not be read" in outcome["reason"]


def test_parse_verdict_reads_json_the_model_wrapped_in_prose() -> None:
    """Models fence JSON. Accept that rather than escalating on punctuation."""
    parsed = parse_verdict(
        'Here is my answer:\n```json\n{"added_facts": ["x"], "dropped_facts": []}\n```'
    )
    assert parsed == {"added_facts": ["x"], "dropped_facts": []}


def test_parse_verdict_returns_none_on_junk() -> None:
    assert parse_verdict("I could not comply.") is None


def test_parse_verdict_rejects_the_wrong_shape() -> None:
    """A JSON object missing the fields is not a verdict."""
    assert parse_verdict('{"verdict": "fine"}') is None


@pytest.mark.parametrize("answer", ['{"added_facts": [], "dropped_facts": []}'])
def test_parse_verdict_reads_a_bare_object(answer: str) -> None:
    assert parse_verdict(answer) == {"added_facts": [], "dropped_facts": []}
