"""Tests for src/ingest/judge_claims.py — Docs/ingestion/rebuild-spec.md slice 4 (P3).

Every test runs against llm.MockClient over the real Lonely Planet
chunk-07 unit; no live client, no network, no spend
(test_no_live_client_in_this_file, same walker as the slice-3 files).
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import src.ingest.judge_claims as judge_claims
import src.ingest.llm as llm
from src.ingest import decompose, group, model, prompts
from src.ingest import unit as unit_mod

REPO_ROOT = Path(__file__).resolve().parent.parent
BOOKS_ROOT = REPO_ROOT / "Books" / "new_york"
LP_SOURCE = "lonely-planet-new-york-city"
LP_CHUNK = "chunk-07-upper-east-side"

#: A judge model id that differs from the configured ROLE_MODEL entry, so a
#: test can tell a verdict stamped from the RESPONSE apart from one copied
#: out of config.
RESPONSE_JUDGE_MODEL = "claude-haiku-4-5-20251001"

#: Clean P1 claims verified against the real chunk text (the same pins
#: tests/test_ingest_decompose.py proves assertion-free against the gates).
ADDRESS_CLAIM: dict = {
    "text": (
        "The Solomon R. Guggenheim Museum stands at 1071 Fifth Avenue "
        "on the corner of East 89th Street."
    ),
    "kind": "state",
    "span": "1071 Fifth Ave, at E 89th St",
}
COMPLETED_CLAIM: dict = {
    "text": (
        "The Guggenheim building was finished in 1959, by which time "
        "both Frank Lloyd Wright and Solomon Guggenheim were dead."
    ),
    "kind": "event",
    "span": (
        "Construction was finally completed in 1959 – after both "  # noqa: RUF001
        "Wright and Guggenheim had passed away."
    ),
}


def _real_unit() -> unit_mod.Unit:
    return unit_mod.load_unit(
        BOOKS_ROOT,
        city="new_york",
        source_id=LP_SOURCE,
        chunk=LP_CHUNK,
        as_of=2023,
        rights_basis="owned_copy",
    )


def _draft(unit: unit_mod.Unit, claim_id: str, item: dict) -> decompose.ClaimDraft:
    return decompose.ClaimDraft(
        claim_id=claim_id,
        text=item["text"],
        kind=item["kind"],
        source=unit.source(item["span"]),
    )


def _story(claim_ids: list[str]) -> group.Story:
    return group.Story(
        title="How the museum came to be",
        story_slug="how-the-museum-came-to-be",
        place="Guggenheim Museum",
        new_poi=False,
        lenses=["hidden_history"],
        beat_type="anecdote",
        enrichment=group.Enrichment(),
        claim_ids=claim_ids,
    )


def _sink_and_events():
    events: list[tuple[str, dict]] = []

    def sink(kind: str, payload: dict) -> None:
        events.append((kind, payload))

    return events, sink


def _armed_mock(batch_answers: dict, unit: unit_mod.Unit) -> llm.MockClient:
    """A MockClient scripted with `batch_answers`, its estimate gate armed
    against the real unit text and the real P3_PLAN."""
    _events, sink = _sink_and_events()
    mock = llm.MockClient(sink, batch_answers=batch_answers)
    mock.estimate([unit.text], list(judge_claims.P3_PLAN))
    return mock


def _entailed(reason: str = "the span states it", kind: str = "state") -> llm.MockAnswer:
    """c01 is the ADDRESS (state) claim in every test below; pass
    kind="event" for the COMPLETED claim so the judge agrees with the author."""
    return llm.MockAnswer(
        text=json.dumps({"entailed": True, "reason": reason, "kind": kind}),
        model_id=RESPONSE_JUDGE_MODEL,
    )


def test_verdict_is_bound_to_claim_and_span_hash():
    """The spec's proving node: one clean claim, judge says entailed. The
    verdict is bound by SHA-256 to exactly the text and span it judged, and
    carries the model id the RESPONSE reported, not the configured one."""
    unit = _real_unit()
    draft = _draft(unit, "c01", ADDRESS_CLAIM)
    mock = _armed_mock({judge_claims.judge_custom_id(unit, "c01", 1): _entailed()}, unit)

    judged = judge_claims.judge_claims(_story(["c01"]), [draft], unit, mock)

    assert len(judged) == 1
    assert judged[0].draft == draft
    verdict = judged[0].verdict
    assert isinstance(verdict, model.Verdict)
    assert verdict.entailed is True
    # Independent literal: sha256(text NUL span), the binding the slice-1
    # validator recomputes (model.bind with one source span).
    expected = hashlib.sha256(
        f"{ADDRESS_CLAIM['text']}\x00{ADDRESS_CLAIM['span']}".encode()
    ).hexdigest()
    assert verdict.bound_to == expected
    assert verdict.bound_to == model.bind(draft.text, draft.source.span)
    assert verdict.judge_model == RESPONSE_JUDGE_MODEL
    assert verdict.judge_model != llm.ROLE_MODEL["claim_judge"]
    # Exactly one batch round, under the judge role and the P3 phase.
    assert mock.calls == [("claim_judge", "P3")]


def _refused(reason: str, kind: str = "event") -> llm.MockAnswer:
    return llm.MockAnswer(
        text=json.dumps({"entailed": False, "reason": reason, "kind": kind}),
        model_id=RESPONSE_JUDGE_MODEL,
    )


def _restated(item: dict) -> llm.MockAnswer:
    return llm.MockAnswer(text=json.dumps(item), model_id="claude-opus-5")


def test_refused_once_is_restated_by_the_author_with_the_reason_quoted_back():
    """P3's one re-ask: the judge refuses, the AUTHOR is asked once to
    restate that claim with the judge's reason quoted back, and the
    restated claim's round-two verdict is what comes out — bound to the
    NEW text, not the refused one."""
    unit = _real_unit()
    fabricated = {**COMPLETED_CLAIM, "text": "The Guggenheim building was finished in 1961."}
    draft = _draft(unit, "c02", fabricated)
    reason = "the span says 1959, not 1961"
    mock = _armed_mock(
        {
            judge_claims.judge_custom_id(unit, "c02", 1): _refused(reason),
            judge_claims.restate_custom_id(unit, "c02"): _restated(COMPLETED_CLAIM),
            judge_claims.judge_custom_id(unit, "c02", 2): _entailed(kind="event"),
        },
        unit,
    )
    recorder = _RecordingBatchClient(mock)
    events, sink = _sink_and_events()

    judged = judge_claims.judge_claims(_story(["c02"]), [draft], unit, recorder, events=sink)

    assert [call["phase"] for call in recorder.batch_calls] == ["P3", "P1", "P3"]
    assert [call["role"] for call in recorder.batch_calls] == [
        "claim_judge",
        "author",
        "claim_judge",
    ]
    restate_prompt = recorder.batch_calls[1]["prompts"][0][1]
    assert reason in restate_prompt
    assert fabricated["text"] in restate_prompt
    assert COMPLETED_CLAIM["span"] in restate_prompt
    assert (
        "claim_refused",
        {
            "unit_key": unit.key,
            "claim_id": "c02",
            "attempt": 1,
            "reason": reason,
            "claim_text": fabricated["text"],
            "span": fabricated["span"],
        },
    ) in events

    assert len(judged) == 1
    assert judged[0].draft.claim_id == "c02"
    assert judged[0].draft.text == COMPLETED_CLAIM["text"]
    assert judged[0].draft.source.span == COMPLETED_CLAIM["span"]
    assert judged[0].verdict.entailed is True
    assert judged[0].verdict.bound_to == model.bind(
        COMPLETED_CLAIM["text"], COMPLETED_CLAIM["span"]
    )


class _RecordingBatchClient:
    """Records every complete_batch() call's exact keyword arguments."""

    def __init__(self, client) -> None:
        self._client = client
        self.batch_calls: list[dict] = []

    def complete_batch(self, *, role, prompts, schema, phase, max_tokens):
        self.batch_calls.append(
            {
                "role": role,
                "prompts": list(prompts),
                "schema": schema,
                "phase": phase,
                "max_tokens": max_tokens,
            }
        )
        return self._client.complete_batch(
            role=role, prompts=prompts, schema=schema, phase=phase, max_tokens=max_tokens
        )


def test_refused_twice_is_dropped_and_logged_with_ids_unchanged():
    """The one-re-ask budget: a claim the judge refuses again after the
    author's restate is DROPPED and logged with the judge's second reason;
    the other claims come out with their ids untouched (stories already
    reference them, so P3 never renumbers), and no fourth call happens."""
    unit = _real_unit()
    good = _draft(unit, "c01", ADDRESS_CLAIM)
    fabricated = {**COMPLETED_CLAIM, "text": "The Guggenheim building was finished in 1961."}
    bad = _draft(unit, "c02", fabricated)
    still_bad = {**COMPLETED_CLAIM, "text": "The Guggenheim building was finished in 1960."}
    mock = _armed_mock(
        {
            judge_claims.judge_custom_id(unit, "c01", 1): _entailed(),
            judge_claims.judge_custom_id(unit, "c02", 1): _refused("the span says 1959"),
            judge_claims.restate_custom_id(unit, "c02"): _restated(still_bad),
            judge_claims.judge_custom_id(unit, "c02", 2): _refused("still not 1959"),
        },
        unit,
    )
    events, sink = _sink_and_events()

    judged = judge_claims.judge_claims(_story(["c01", "c02"]), [good, bad], unit, mock, sink)

    assert [claim.draft.claim_id for claim in judged] == ["c01"]
    assert all(claim.verdict.entailed for claim in judged)
    # MockClient records one entry per batch UNIT: two claims judged in
    # round 1, one restated, one re-judged — and nothing after that.
    assert mock.calls == [
        ("claim_judge", "P3"),
        ("claim_judge", "P3"),
        ("author", "P1"),
        ("claim_judge", "P3"),
    ]
    assert (
        "claim_dropped",
        {
            "unit_key": unit.key,
            "claim_id": "c02",
            "claim_text": still_bad["text"],
            "reason": "still not 1959",
        },
    ) in events
    assert [e for e in events if e[0] == "claim_refused"] == [
        (
            "claim_refused",
            {
                "unit_key": unit.key,
                "claim_id": "c02",
                "attempt": 1,
                "reason": "the span says 1959",
                "claim_text": fabricated["text"],
                "span": fabricated["span"],
            },
        ),
        (
            "claim_refused",
            {
                "unit_key": unit.key,
                "claim_id": "c02",
                "attempt": 2,
                "reason": "still not 1959",
                "claim_text": still_bad["text"],
                "span": still_bad["span"],
            },
        ),
    ]


def test_restated_claim_failing_the_gates_is_dropped_not_rejudged():
    """A restate that trips a P1 gate (here: a span that is not in the
    unit) is dropped with the gate's own reason, logged, and never sent to
    the judge again — the gates stand between the author and the judge on
    the re-ask exactly as they do on the first ask."""
    unit = _real_unit()
    fabricated = {**COMPLETED_CLAIM, "text": "The Guggenheim building was finished in 1961."}
    bad = _draft(unit, "c02", fabricated)
    off_span = {**COMPLETED_CLAIM, "span": "Construction was finally completed in 1961."}
    mock = _armed_mock(
        {
            judge_claims.judge_custom_id(unit, "c02", 1): _refused("the span says 1959"),
            judge_claims.restate_custom_id(unit, "c02"): _restated(off_span),
            # No round-2 judge answer scripted: reaching it would raise
            # MockScriptExhausted, so the pass proves the judge was not asked.
        },
        unit,
    )
    events, sink = _sink_and_events()

    judged = judge_claims.judge_claims(_story(["c02"]), [bad], unit, mock, sink)

    assert judged == []
    assert mock.calls == [("claim_judge", "P3"), ("author", "P1")]
    dropped = [payload for kind, payload in events if kind == "claim_dropped"]
    assert len(dropped) == 1
    assert dropped[0]["claim_id"] == "c02"
    assert dropped[0]["claim_text"] == off_span["text"]
    assert dropped[0]["reason"].startswith("span_not_in_unit")


def test_restated_claim_with_a_straightened_dash_is_grounded_and_rejudged():
    """Slice 9 job 1 re-run (2026-09-13): restates that straightened the
    passage's typography were dropped as span_not_in_unit. A restated
    citation `gates.ground_span` locates is stored as the passage's own
    text, passes the gates, and is judged in round two like any restate."""
    unit = _real_unit()
    fabricated = {**COMPLETED_CLAIM, "text": "The Guggenheim building was finished in 1961."}
    draft = _draft(unit, "c02", fabricated)
    ascii_dash = {
        **COMPLETED_CLAIM,
        "span": COMPLETED_CLAIM["span"].replace("–", "-"),  # noqa: RUF001
    }
    assert ascii_dash["span"] != COMPLETED_CLAIM["span"]
    mock = _armed_mock(
        {
            judge_claims.judge_custom_id(unit, "c02", 1): _refused("the span says 1959"),
            judge_claims.restate_custom_id(unit, "c02"): _restated(ascii_dash),
            judge_claims.judge_custom_id(unit, "c02", 2): _entailed(kind="event"),
        },
        unit,
    )
    events, sink = _sink_and_events()

    judged = judge_claims.judge_claims(_story(["c02"]), [draft], unit, mock, sink)

    assert [kind for kind, _ in events if kind == "claim_dropped"] == []
    assert mock.calls == [("claim_judge", "P3"), ("author", "P1"), ("claim_judge", "P3")]
    assert len(judged) == 1
    assert judged[0].draft.source.span == COMPLETED_CLAIM["span"]
    assert judged[0].verdict.bound_to == model.bind(
        COMPLETED_CLAIM["text"], COMPLETED_CLAIM["span"]
    )


def test_transport_failure_or_unreadable_answer_holds_the_unit_in_p3():
    """A batch failure, a truncated completion, an unreadable judge answer,
    or an unreadable restate all hold the unit (`unit_held`, then
    decompose.UnitHeld with phase 'P3') — with no further call. A
    programming error (unarmed estimate gate, missing script) propagates as
    itself, never wrapped as UnitHeld."""
    import pytest

    unit = _real_unit()
    draft = _draft(unit, "c01", ADDRESS_CLAIM)
    story = _story(["c01"])
    j1 = judge_claims.judge_custom_id(unit, "c01", 1)
    r1 = judge_claims.restate_custom_id(unit, "c01")

    scripts = {
        "batch_failure": {j1: llm.MockFailure(result_type="errored", error_message="boom")},
        "truncated": {
            j1: llm.MockAnswer(
                text='{"entailed": tr', model_id=RESPONSE_JUDGE_MODEL, stop_reason="max_tokens"
            )
        },
        "unreadable_verdict": {
            j1: llm.MockAnswer(text='{"verdict": "yes"}', model_id=RESPONSE_JUDGE_MODEL)
        },
        "unreadable_restate": {
            j1: _refused("not in the span"),
            r1: llm.MockAnswer(text="Sure! Here is a better claim.", model_id="claude-opus-5"),
        },
    }
    for label, batch_answers in scripts.items():
        events, sink = _sink_and_events()
        mock = _armed_mock(batch_answers, unit)
        with pytest.raises(decompose.UnitHeld) as held:
            judge_claims.judge_claims(story, [draft], unit, mock, sink)
        assert held.value.phase == "P3", label
        assert held.value.unit_key == unit.key, label
        holds = [payload for kind, payload in events if kind == "unit_held"]
        assert len(holds) == 1, label
        assert holds[0]["phase"] == "P3", label
        assert holds[0]["reason"] == held.value.reason, label
        assert not any(kind == "claim_dropped" for kind, _ in events), label

    # Unarmed: judge_claims never estimates itself.
    _events, sink = _sink_and_events()
    unarmed = llm.MockClient(sink, batch_answers={j1: _entailed()})
    with pytest.raises(llm.EstimateNotPrinted):
        judge_claims.judge_claims(story, [draft], unit, unarmed)
    assert unarmed.calls == []

    # Armed but nothing scripted: the mock's own refusal, as itself.
    with pytest.raises(llm.MockScriptExhausted):
        judge_claims.judge_claims(story, [draft], unit, _armed_mock({}, unit))


def test_only_the_storys_own_claims_are_judged():
    """P3 runs per story: a claim the story does not list is neither sent
    to the judge nor returned (no answer is scripted for it, so a call
    would raise MockScriptExhausted)."""
    unit = _real_unit()
    listed = _draft(unit, "c02", COMPLETED_CLAIM)
    unlisted = _draft(unit, "c01", ADDRESS_CLAIM)
    mock = _armed_mock(
        {judge_claims.judge_custom_id(unit, "c02", 1): _entailed(kind="event")}, unit
    )

    judged = judge_claims.judge_claims(_story(["c02"]), [unlisted, listed], unit, mock)

    assert [claim.draft.claim_id for claim in judged] == ["c02"]
    assert mock.calls == [("claim_judge", "P3")]


OMITTED_FACT = {
    "fact": "The museum opened in October 1959 with a fifty-cent ticket.",
    "span": "When the Guggenheim opened its doors in October 1959, the ticket price was 50¢",
}
UNGROUNDED_FINDING = {
    "fact": "Wright designed the building in 1943.",
    "span": "Wright drew the plans in 1943",  # not in the unit
}


def test_omissions_returns_only_facts_grounded_in_the_unit():
    """omissions(): one judge batch over the unit and its claims; every
    finding must cite a span verbatim in the unit (gates.span_in_unit) —
    an ungrounded finding is discarded and logged, never returned. The
    P3 re-ask of P1 for the unit is the job runner's decision (slice 7);
    this only reports."""
    unit = _real_unit()
    claims = [_draft(unit, "c01", ADDRESS_CLAIM), _draft(unit, "c02", COMPLETED_CLAIM)]
    answer = llm.MockAnswer(
        text=json.dumps({"omitted": [OMITTED_FACT, UNGROUNDED_FINDING]}),
        model_id=RESPONSE_JUDGE_MODEL,
    )
    _events, sink = _sink_and_events()
    mock = llm.MockClient(sink, batch_answers={judge_claims.omissions_custom_id(unit): answer})
    mock.estimate([unit.text], list(judge_claims.OMISSIONS_PLAN))
    recorder = _RecordingBatchClient(mock)
    events, sink = _sink_and_events()

    facts = judge_claims.omissions(unit, claims, recorder, sink)

    assert facts == [OMITTED_FACT["fact"]]
    assert [(c["role"], c["phase"]) for c in recorder.batch_calls] == [("claim_judge", "P3")]
    prompt = recorder.batch_calls[0]["prompts"][0][1]
    assert ADDRESS_CLAIM["text"] in prompt
    assert COMPLETED_CLAIM["text"] in prompt
    assert (
        "omissions_found",
        {"unit_key": unit.key, "facts": [OMITTED_FACT["fact"]], "spans": [OMITTED_FACT["span"]]},
    ) in events
    ungrounded = [p for kind, p in events if kind == "omission_ungrounded"]
    assert ungrounded == [
        {
            "unit_key": unit.key,
            "fact": UNGROUNDED_FINDING["fact"],
            "reason": (
                f"span_not_in_unit: {UNGROUNDED_FINDING['span']!r} is not in the unit text"
            ),
        }
    ]

    # Nothing omitted: an empty list and no omissions_found event.
    _events, sink = _sink_and_events()
    quiet = llm.MockClient(
        sink,
        batch_answers={
            judge_claims.omissions_custom_id(unit): llm.MockAnswer(
                text='{"omitted": []}', model_id=RESPONSE_JUDGE_MODEL
            )
        },
    )
    quiet.estimate([unit.text], list(judge_claims.OMISSIONS_PLAN))
    events, sink = _sink_and_events()
    assert judge_claims.omissions(unit, claims, quiet, sink) == []
    assert events == []


def test_the_coverage_check_logs_compound_claims_by_id_and_changes_nothing_else():
    """Slice 10 step 2 (log-only): the same P3 unit call now names every
    claim that states more than one fact. A finding naming a claim id the
    unit has is emitted as `compound_found` with the claim's text and the
    facts it bundles; one naming an id the unit lacks is dropped — the
    judge may not invent a claim. What omissions() RETURNS is untouched (the
    runner still re-asks P1 only for omitted facts), and an answer without
    the compound list still reads as "nothing compound"."""
    unit = _real_unit()
    claims = [_draft(unit, "c01", ADDRESS_CLAIM), _draft(unit, "c02", COMPLETED_CLAIM)]
    bundled = ["The building was finished in 1959.", "Wright had died by then."]
    answer = llm.MockAnswer(
        text=json.dumps({
            "omitted": [],
            "compound": [
                {"claim_id": "c02", "facts": bundled},
                {"claim_id": "c09", "facts": ["a", "b"]},
            ],
        }),
        model_id=RESPONSE_JUDGE_MODEL,
    )
    _events, sink = _sink_and_events()
    mock = llm.MockClient(sink, batch_answers={judge_claims.omissions_custom_id(unit): answer})
    mock.estimate([unit.text], list(judge_claims.OMISSIONS_PLAN))
    recorder = _RecordingBatchClient(mock)
    events, sink = _sink_and_events()

    facts = judge_claims.omissions(unit, claims, recorder, sink)

    assert facts == []
    prompt = recorder.batch_calls[0]["prompts"][0][1]
    assert f"- c02: {COMPLETED_CLAIM['text']}" in prompt
    assert [(k, p) for k, p in events if k == "compound_found"] == [
        (
            "compound_found",
            {
                "unit_key": unit.key,
                "claims": [
                    {"claim_id": "c02", "text": COMPLETED_CLAIM["text"], "facts": bundled}
                ],
            },
        )
    ]
    # A dropped finding leaves a trace, as omission_ungrounded does: a live
    # judge answering "c1" for "c01" must show up, not vanish as a miss.
    assert [(k, p) for k, p in events if k == "compound_unknown"] == [
        ("compound_unknown", {"unit_key": unit.key, "claim_id": "c09", "facts": ["a", "b"]})
    ]


def test_p3_plans_price_the_real_prompts():
    """P3_PLAN (per claim: judge, restate ceiling, re-judge ceiling) and
    OMISSIONS_PLAN (per unit) carry the real prompt lengths, never a
    made-up overhead number, and price as batch rows under their roles."""
    import src.ingest.prompts as prompts

    rows = list(judge_claims.P3_PLAN)
    assert [(r.phase, r.role) for r in rows] == [
        ("P3", "claim_judge"),
        ("P1", "author"),
        ("P3", "claim_judge"),
    ]
    assert rows[0].overhead_tokens == max(1, len(prompts.JUDGE_CLAIM_PROMPT) // 4)
    assert rows[1].overhead_tokens == max(1, len(prompts.RESTATE_PROMPT) // 4)
    assert rows[2].overhead_tokens == rows[0].overhead_tokens
    assert all(r.calls_per_unit == 1 for r in rows)

    (omissions_row,) = judge_claims.OMISSIONS_PLAN
    assert (omissions_row.phase, omissions_row.role) == ("P3", "claim_judge")
    assert omissions_row.overhead_tokens == max(1, len(prompts.OMISSIONS_PROMPT) // 4)

    _events, sink = _sink_and_events()
    estimate = llm.MockClient(sink).estimate(
        ["a claim"], [*judge_claims.P3_PLAN, *judge_claims.OMISSIONS_PLAN]
    )
    assert [r.model_id for r in estimate.rows] == [
        "claude-haiku-4-5",
        "claude-opus-5",
        "claude-haiku-4-5",
        "claude-haiku-4-5",
    ]
    assert all(r.batch for r in estimate.rows)


HOLDINGS_CLAIM: dict = {
    "text": (
        "Kandinsky, Picasso and Jackson Pollock are all represented in the museum's collection."
    ),
    "kind": "event",  # the author's mistake: a holding is a STATE (CONTEXT.md), not an event
    "span": "The museum’s holdings include works by Kandinsky, Picasso and Jackson Pollock.",  # noqa: RUF001
}


def test_the_judge_reads_the_kind_and_a_misread_kind_is_rekinded_not_refused():
    """Slice-6 ruling 5 (owner, 2026-09-12): the P3 call carries a KIND
    question — the judge reads the claim's temporal kind from the span in
    the SAME batch as entailment — and a claim the author kinded wrongly
    is RE-KINDED, never refused: entailment is about facts, kind is about
    time, and `state_as_event` (spec §4) is a kind defect. The judged
    claim comes out with the judge's kind, a `claim_rekinded` event names
    the change, and no restate happens. A judge that agrees leaves the
    kind alone and emits nothing. The schema and the prompt's answer shape
    carry the third key, so the live judge always answers it; an answer
    without it, or with a kind outside event/state/belief, is not a
    verdict (the unit is held as for any unreadable answer)."""
    unit = _real_unit()
    misread = _draft(unit, "c01", HOLDINGS_CLAIM)
    completed = _draft(unit, "c02", COMPLETED_CLAIM)
    events, sink = _sink_and_events()
    mock = llm.MockClient(
        sink,
        batch_answers={
            judge_claims.judge_custom_id(unit, "c01", 1): llm.MockAnswer(
                text=json.dumps(
                    {"entailed": True, "reason": "the span lists them", "kind": "state"}
                ),
                model_id=RESPONSE_JUDGE_MODEL,
            ),
            judge_claims.judge_custom_id(unit, "c02", 1): _entailed(kind="event"),
        },
    )
    mock.estimate([unit.text], list(judge_claims.P3_PLAN))

    judged = judge_claims.judge_claims(
        _story(["c01", "c02"]), [misread, completed], unit, mock, events=sink
    )

    by_id = {j.draft.claim_id: j for j in judged}
    assert by_id["c01"].draft.kind == "state"
    assert by_id["c01"].draft.text == HOLDINGS_CLAIM["text"]
    assert by_id["c01"].verdict.entailed is True
    assert by_id["c02"].draft == completed  # the judge agreed: untouched
    assert mock.calls == [("claim_judge", "P3")] * 2  # one round (per-id count), no restate
    assert [(k, p) for k, p in events if k == "claim_rekinded"] == [
        (
            "claim_rekinded",
            {"unit_key": unit.key, "claim_id": "c01", "from": "event", "to": "state"},
        )
    ]

    assert set(prompts.P3_VERDICT_SCHEMA["properties"]) == {"entailed", "reason", "kind"}
    assert prompts.P3_VERDICT_SCHEMA["properties"]["kind"]["enum"] == ["event", "state", "belief"]
    assert '"kind"' in prompts.JUDGE_CLAIM_PROMPT
    assert judge_claims.parse_verdict('{"entailed": true, "reason": "r"}') is None
    assert judge_claims.parse_verdict(
        '{"entailed": true, "reason": "r", "kind": "ambiguous"}'
    ) is None


def test_no_live_client_in_this_file():
    """This $0-spend test file never names a live LLM client or reads its
    API key. Its own body is exempt from the walk below — this docstring
    and the assert messages name those things on purpose to describe the
    rule, which is not the violation the rule guards against.
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    self_name = "test_no_live_client_in_this_file"
    forbidden_names = {"AnthropicClient", "anthropic"}
    forbidden_env_var = "ANTHROPIC_API_KEY"

    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == self_name:
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name):
                assert sub.id not in forbidden_names, f"{sub.id!r} must not appear in this file"
            elif isinstance(sub, ast.Attribute):
                assert sub.attr not in forbidden_names, (
                    f"{sub.attr!r} must not appear in this file"
                )
            elif isinstance(sub, ast.ImportFrom) and sub.module:
                assert sub.module.split(".")[0] not in forbidden_names, (
                    f"from-import of {sub.module!r} must not appear in this file"
                )
            elif isinstance(sub, ast.alias):
                top_level_name = sub.name.split(".")[0]
                assert top_level_name not in forbidden_names, (
                    f"import of {sub.name!r} must not appear in this file"
                )
                assert sub.asname not in forbidden_names, (
                    f"import alias {sub.asname!r} must not appear in this file"
                )
            elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                assert forbidden_env_var not in sub.value, (
                    f"{forbidden_env_var!r} must not appear in this file"
                )


STRAIGHTENED_FINDING: dict = {
    "fact": "Guggenheim's niece Peggy donated key surrealist works to the museum.",
    "span": "key surrealist works donated by Guggenheim's niece Peggy",  # ASCII apostrophe
}


def test_omission_cited_with_straightened_quotes_is_grounded_to_the_units_own_text():
    """The live judge straightens curly apostrophes when it copies a span,
    and the 2026-09-12 live run threw two real omissions away for it. A
    citation that differs from the passage only by typographic quotes,
    dashes or whitespace is grounded (gates.locate_span) and the span
    reported is the passage's OWN text — curly apostrophe and all — never
    the judge's copy; a paraphrase is still discarded and logged."""
    unit = _real_unit()
    claims = [_draft(unit, "c01", ADDRESS_CLAIM), _draft(unit, "c02", COMPLETED_CLAIM)]
    verbatim = "key surrealist works donated by Guggenheim\u2019s niece Peggy"
    assert verbatim in " ".join(unit.text.split())
    assert STRAIGHTENED_FINDING["span"] not in unit.text
    answer = llm.MockAnswer(
        text=json.dumps({"omitted": [STRAIGHTENED_FINDING, UNGROUNDED_FINDING]}),
        model_id=RESPONSE_JUDGE_MODEL,
    )
    mock = _armed_mock({judge_claims.omissions_custom_id(unit): answer}, unit)
    events, sink = _sink_and_events()

    facts = judge_claims.omissions(unit, claims, mock, sink)

    assert facts == [STRAIGHTENED_FINDING["fact"]]
    assert (
        "omissions_found",
        {"unit_key": unit.key, "facts": [STRAIGHTENED_FINDING["fact"]], "spans": [verbatim]},
    ) in events
    assert [p["fact"] for kind, p in events if kind == "omission_ungrounded"] == [
        UNGROUNDED_FINDING["fact"]
    ]


def test_a_refusal_logs_the_claim_text_and_span_it_judged():
    """The proof-chunk panel could not audit job 1's nine refusals from the
    log: `claim_refused` carried only an id and the judge's reason. Every
    refusal now records the claim text and the span the judge read, so a
    false refusal can be called from the log alone."""
    unit = _real_unit()
    draft = _draft(unit, "c02", COMPLETED_CLAIM)
    events, sink = _sink_and_events()
    mock = llm.MockClient(
        sink,
        batch_answers={
            judge_claims.judge_custom_id(unit, "c02", 1): _refused("nope"),
            judge_claims.restate_custom_id(unit, "c02"): _restated(COMPLETED_CLAIM),
            judge_claims.judge_custom_id(unit, "c02", 2): _entailed(kind="event"),
        },
    )
    mock.estimate([unit.text], list(judge_claims.P3_PLAN))
    judge_claims.judge_claims(_story(["c02"]), [draft], unit, mock, events=sink)
    refused = [p for k, p in events if k == "claim_refused"]
    assert refused and refused[0]["claim_text"] == COMPLETED_CLAIM["text"]
    assert refused[0]["span"] == COMPLETED_CLAIM["span"]


def test_an_omission_a_claim_already_states_is_discarded_and_a_near_miss_is_kept():
    """Slice 10 job A: the omission check reported "The Guggenheim Museum has
    a spiral ramp" — a listed claim, word for word — and that one false
    finding re-ran the whole unit (424 claims, the Wright claim regrouped
    into an itinerary, places renamed). A finding whose salient words are
    EXACTLY a listed claim's (claim_dedup's signature, compared for equality
    — never the overlap coefficient, which calls a longer finding "carried"
    by any claim whose words it contains) is dropped and logged
    `omission_already_carried`; a near-miss that adds a fact still returns."""
    unit = _real_unit()
    claims = [_draft(unit, "c01", ADDRESS_CLAIM), _draft(unit, "c02", COMPLETED_CLAIM)]
    carried = {
        "fact": (
            "the guggenheim building was finished in 1959 - by which time both "
            "Frank Lloyd Wright and Solomon Guggenheim were dead"
        ),
        "span": "Construction was finally completed in 1959",
    }
    near_miss = {
        "fact": (
            "The Guggenheim building was finished in 1959, by which time both Frank "
            "Lloyd Wright and Solomon Guggenheim were dead, after a 13-year delay."
        ),
        "span": "Construction was delayed for almost 13 years",
    }
    answer = llm.MockAnswer(
        text=json.dumps({"omitted": [carried, near_miss], "compound": []}),
        model_id=RESPONSE_JUDGE_MODEL,
    )
    _events, sink = _sink_and_events()
    mock = llm.MockClient(sink, batch_answers={judge_claims.omissions_custom_id(unit): answer})
    mock.estimate([unit.text], list(judge_claims.OMISSIONS_PLAN))
    events, sink = _sink_and_events()

    facts = judge_claims.omissions(unit, claims, mock, sink)

    assert facts == [near_miss["fact"]]
    assert [(k, p) for k, p in events if k == "omission_already_carried"] == [
        ("omission_already_carried",
         {"unit_key": unit.key, "fact": carried["fact"], "claim_id": "c02"})
    ]

