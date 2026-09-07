"""The audit's guarantees, which are structural rather than statistical.

Three things make this check worth its cost, and each is asserted here rather than
described: the extraction cannot see what the writer was given, the judge is not the
model that produced what it judges, and a matcher that loses a statement does not
thereby report it as invented.
"""

from __future__ import annotations

import pytest

from scripts.reauthor_audit import (
    AUDITOR_MODEL,
    audit_record,
    audit_request,
    excludes_the_producer,
    match_request,
    parse_matches,
    summarise,
    unmatched_statements,
    unsupported_claims,
)
from scripts.reauthor_cleanroom import PRODUCER_MODEL

_GIVEN = [
    {
        "claim": "The main stairway is faced with a large quantity of coloured marble.",
        "kind": "fact",
    },
    {"claim": "The author regards the main stairway as grandiose.", "kind": "observation"},
]
_READ_BACK = [
    {"claim": "The staircase is faced with coloured marble.", "kind": "fact"},
    {"claim": "The marble includes red colouring.", "kind": "fact"},
]


def test_the_extraction_cannot_see_what_the_writer_was_given() -> None:
    """The asymmetry, asserted the way the clean room's seam is.

    A model shown the claims and asked to read the body would read the body in their
    light, and report the agreement it was set up to find. There is no parameter for
    them, so nothing downstream can pass them by accident.
    """
    payload = audit_request(body="A body about marble.", poi="Palais Garnier", city="Paris")
    rendered = payload["messages"][0]["content"]
    for claim in _GIVEN:
        assert claim["claim"] not in rendered
    assert "A body about marble." in rendered


def test_the_matcher_is_asked_which_fact_not_whether_it_is_supported() -> None:
    """A yes/no question has an agreeable answer; a numbered list has a checkable one."""
    rendered = match_request(
        read_back=_READ_BACK, given=_GIVEN, poi="Palais Garnier", city="Paris"
    )["messages"][0]["content"]
    assert "1. The staircase is faced with coloured marble." in rendered
    assert "1. The main stairway is faced with a large quantity of coloured marble." in rendered
    assert "matching task, not a judgement" in rendered


def test_the_matcher_is_told_which_place_the_two_lists_are_about() -> None:
    """The extraction is told the place; the matcher must be told it too.

    The decomposer routinely leaves the subject unnamed — "this location", "the city",
    "the church" — where a statement read out of the body names it. A matcher that does
    not know they are the same place can never match those, and reports the writer for
    saying which place its beat is about. That asymmetry was the single largest source
    of false findings.
    """
    rendered = match_request(
        read_back=_READ_BACK, given=_GIVEN, poi="Palais Garnier", city="Paris"
    )["messages"][0]["content"]
    assert "Palais Garnier" in rendered
    assert "Paris" in rendered
    assert "not a different one" in rendered


def test_a_statement_the_body_does_not_contain_is_the_readers_to_know_about() -> None:
    """The first call's blindness buys independence from the claims, not fidelity.

    A body saying "anyone who has watched SoHo may recognise the pattern" came back as a
    statement about "a familiar pattern of gentrification", and the matcher then
    reported a word the writer never wrote as something the writer invented.
    """
    from scripts.reauthor_audit import unfaithful_statements

    body = "Anyone who has watched SoHo may recognise the pattern."
    assert unfaithful_statements([{"claim": "A familiar pattern of gentrification."}], body)
    kept = [{"claim": "The pattern is one anyone who has watched SoHo may recognise."}]
    assert unfaithful_statements(kept, body) == []


def test_two_answers_about_one_statement_make_the_reply_unreadable() -> None:
    """Keeping the last is a silent choice between them that nothing could see."""
    assert (
        parse_matches('{"matches":[{"statement":1,"facts":[2]},{"statement":1,"facts":[]}]}')
        is None
    )


def test_the_summary_reports_a_matcher_that_answered_about_nothing() -> None:
    """A run where nothing was matched looks exactly like a run where nothing was wrong."""
    verdict = audit_record(
        {"beat_id": "b", "body_after": "A square laid out in 1830."},
        read_back=[{"claim": "The square was laid out in 1907.", "kind": "fact"}],
        matches={},
    )
    found = summarise([verdict])
    assert found["statements_never_answered_about"] == 1
    assert found["bodies_carrying_an_unsupported_claim"] == 0
    # Drift is on the record and never in the summary: it cannot tell a reworded
    # statement from an invented one, so a count of it across a corpus says nothing.
    assert verdict["unfaithful_statements"]
    assert not any("drift" in key for key in found)


def test_the_judge_is_not_the_model_that_wrote_what_it_judges() -> None:
    """A model does not flag what it invented, which is what refuted the last check."""
    assert AUDITOR_MODEL != PRODUCER_MODEL
    verdict = audit_record(
        {"beat_id": "b", "written_by": PRODUCER_MODEL, "decomposed_by": PRODUCER_MODEL},
        read_back=_READ_BACK,
        matches={1: [1], 2: []},
    )
    assert excludes_the_producer(verdict)
    assert not excludes_the_producer({**verdict, "audited_by": PRODUCER_MODEL})
    assert not excludes_the_producer({**verdict, "written_by": AUDITOR_MODEL})


def test_a_statement_nothing_states_is_the_finding() -> None:
    """An empty fact list is the answer the prompt asks for, and the defect it names."""
    found = unsupported_claims(_READ_BACK, {1: [1], 2: []})
    assert [f["claim"] for f in found] == ["The marble includes red colouring."]


def test_a_statement_the_matcher_skipped_is_not_a_finding() -> None:
    """A matcher answering about one statement of two has lost one, not found one.

    Reading silence as a defect manufactures exactly what this exists to detect, and
    the count of what went unanswered is reported so a run cannot hide behind it.
    """
    assert unsupported_claims(_READ_BACK, {1: [1]}) == []
    assert unmatched_statements(_READ_BACK, {1: [1]}) == 1
    assert unmatched_statements(_READ_BACK, None) == 2


def test_an_unreadable_reply_is_not_a_clean_verdict() -> None:
    """A body whose audit failed is not a body that passed."""
    verdict = audit_record({"beat_id": "b"}, read_back=None, matches=None)
    assert verdict["unreadable"]
    assert summarise([verdict])["audited"] == 0


@pytest.mark.parametrize(
    "text",
    [
        '{"matches": [{"statement": 1, "facts": [2, 3]}]}',
        'Here you go:\n{"matches": [{"statement": 1, "facts": [2, 3]}]}\nhope that helps',
    ],
)
def test_the_matchers_reply_survives_prose_around_it(text: str) -> None:
    assert parse_matches(text) == {1: [2, 3]}


def test_an_unparseable_reply_reads_as_unreadable_not_as_empty() -> None:
    """`None` and `{}` mean opposite things: nothing was said, and nothing matched."""
    assert parse_matches("the model apologised instead") is None
    assert parse_matches('{"matches": []}') == {}
