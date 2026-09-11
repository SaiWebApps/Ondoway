"""Tests for src/ingest/decompose.py — specs/2026-09-10-ingest-slice-3, step 5.

Step 5 (this step) lands the types decompose() will run on: AC-26
(`parse_claims` turns a raw P1 answer into its list of claim dicts, or
`None` for anything unreadable — surrounding prose is tolerated, but a
single invalid item makes the WHOLE answer unreadable, never a partial
list of only the good ones), AC-27 (`ClaimDraft` is a pydantic model whose
`kind` only ever resolves to `event`/`state`/`belief` — never the LLM's
own `ambiguous` escape hatch — and carries a real `model.Source`;
`UnitHeld` is a plain `Exception`, deliberately NOT a `ValueError`, so a
caller catching `llm.LlmError` does not accidentally swallow it, carrying
`.unit_key`/`.phase`/`.reason`), and AC-28 (`P1_PLAN`'s two rows price the
real `DECOMPOSE_PROMPT`/`REDO_PROMPT` lengths, never a made-up overhead
number).

AC-55 (test_ingest_decompose is one of the five ingest test files with a
self-contained test_no_live_client_in_this_file).

This step writes no test against decompose() itself (that lands in step
6) — only the standalone types above, plus their own $0-spend pricing
arithmetic through a MockClient.

Step 6 adds decompose()'s happy path: AC-29 (a clean one-batch answer
returns ClaimDraft in claim-id order, in exactly one complete_batch()
call, never estimating itself), AC-30 (an unarmed client raises
llm.EstimateNotPrinted with zero calls; an armed client with no '-a1'
answer scripted raises llm.MockScriptExhausted as itself, never wrapped
as UnitHeld), and AC-31 (kind 'ambiguous' resolves to 'state' in one
call; kind 'fact' or a missing kind is a schema-level refusal that
re-asks once under unit.custom_id(2)). AC-58 pins CLEAN_ANSWERS — the
module-level registry of clean P1 claims every later step's tests draw
from — as assertion-free against the real gates, so a claim that a later
step's refusal loop would reject fails here first, never downstream.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import src.ingest.decompose as decompose
import src.ingest.llm as llm
import src.ingest.prompts as prompts
from src.ingest import gates, model

REPO_ROOT = Path(__file__).resolve().parent.parent
BOOKS_ROOT = REPO_ROOT / "Books" / "new_york"
LP_SOURCE = "lonely-planet-new-york-city"
LP_CHUNK = "chunk-07-upper-east-side"

#: AC-58 fixture pins (decisions.fixture_pins, verified against the real
#: chunk text) — the only "clean" P1 claims this file's tests script.
#: Every entry here is proven assertion-free by
#: test_fixture_pins_are_clean below, so a claim that a later step's
#: refusal loop (gates.claim_gates) would reject fails THIS node, never a
#: later one.
CLEAN_ANSWERS: list[dict] = [
    {
        "text": (
            "The Solomon R. Guggenheim Museum stands at 1071 Fifth Avenue "
            "on the corner of East 89th Street."
        ),
        "kind": "state",
        "span": "1071 Fifth Ave, at E 89th St",
    },
    {
        "text": (
            "Solomon Guggenheim's first museum, run by Hilla Rebay, opened "
            "on 54th Street in 1939 under the name Museum of Non-Objective "
            "Painting."
        ),
        "kind": "event",
        "span": (
            "In 1939, with Rebay serving as director, Guggenheim opened a "
            "temporary museum on 54th St titled the Museum of Non-Objective "
            "Painting."
        ),
    },
    {
        "text": (
            "The Guggenheim building was finished in 1959, by which time "
            "both Frank Lloyd Wright and Solomon Guggenheim were dead."
        ),
        "kind": "event",
        "span": (
            "Construction was finally completed in 1959 – after both "  # noqa: RUF001
            "Wright and Guggenheim had passed away."
        ),
    },
    {
        "text": "Admission to the Guggenheim cost fifty cents when it opened in October 1959.",
        "kind": "state",
        "span": (
            "When the Guggenheim opened its doors in October 1959, the "
            "ticket price was 50¢"
        ),
    },
]

#: Gate-tripping P1 claims (decisions.fixture_pins, verified by the planner
#: against the same chunk). Each one pairs a CLEAN_ANSWERS span with a claim
#: text that exactly ONE named gate refuses, so a test asserting on the
#: reason code is pinning that gate rather than an accident of the text.
LIFTED_CLAIM: dict = {
    # 9 consecutive chunk words outside any quotation -> gates.lift.
    "text": "Construction was finally completed in 1959 – after both Wright",  # noqa: RUF001
    "kind": "event",
    "span": CLEAN_ANSWERS[2]["span"],
}
ATTRIBUTED_QUOTE_CLAIM: dict = {
    # The same 9 words, inside a quotation attributed with the cue "wrote"
    # (scripts.verbatim._ATTRIBUTION_CUES) -> exempt, run length 2.
    "text": (
        'The guide wrote "construction was finally completed in 1959 – '  # noqa: RUF001
        'after both Wright" of the museum.'
    ),
    "kind": "belief",
    "span": CLEAN_ANSWERS[2]["span"],
}
LEAKING_PAGE_CLAIM: dict = {
    "text": "See page 139 for the museum hours.",  # gates.leak, APPARATUS_RE
    "kind": "state",
    "span": CLEAN_ANSWERS[0]["span"],
}
LEAKING_PHONE_CLAIM: dict = {
    "text": "Call 212-423-3500 for tickets.",  # gates.leak, LISTING_RE
    "kind": "state",
    "span": CLEAN_ANSWERS[0]["span"],
}
DANGLING_CLAIM: dict = {
    "text": "It opened in 1959.",  # gates.self_contained, ANAPHOR_RE
    "kind": "event",
    "span": CLEAN_ANSWERS[3]["span"],
}
DEMONSTRATIVE_CLAIM: dict = {
    # The ANAPHOR_RE lookahead: a demonstrative before is/are/was/were is a
    # complete sentence, not a dangling reference -> accepted.
    "text": "That is the only Wright building in Manhattan.",
    "kind": "state",
    "span": CLEAN_ANSWERS[0]["span"],
}
BAD_SPAN_CLAIM: dict = {
    # A clean claim text with a span that is NOT in the chunk (Sixth, not
    # Fifth) -> gates.span_in_unit and nothing else.
    "text": CLEAN_ANSWERS[0]["text"],
    "kind": "state",
    "span": "1071 Sixth Ave, at E 89th St",
}


class _RecordingBatchClient:
    """A duck-typed proxy around a real llm.ModelClient (usually a
    MockClient) that records every complete_batch() call's exact keyword
    arguments, so a test can assert decompose() called the transport with
    the pinned shape (AC-29) without MockClient itself needing to track
    more than (role, phase) per call.
    """

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


def _sink_and_events():
    """A recording EventSink: events.append((kind, payload)) per call."""
    events: list[tuple[str, dict]] = []

    def sink(kind: str, payload: dict) -> None:
        events.append((kind, payload))

    return events, sink


def _real_unit():
    from src.ingest import unit as unit_mod

    return unit_mod.load_unit(
        BOOKS_ROOT,
        city="new_york",
        source_id=LP_SOURCE,
        chunk=LP_CHUNK,
        as_of=2023,
        rights_basis="owned_copy",
    )


def test_parse_claims_refuses_any_unreadable_answer_never_partial():
    valid_item = {"text": "The museum stands on Fifth Avenue.", "kind": "event", "span": "s"}

    unreadable = [
        "not json",
        "{}",
        '{"claims": []}',
        '{"claims": [{"text": "x"}]}',  # missing kind/span
        '{"claims": [{"text": "x", "kind": "fact", "span": "s"}]}',
        '{"claims": [{"text": "x", "kind": "event", "span": "s", "extra": 1}]}',
        '{"claims": "not-a-list"}',
    ]
    for answer in unreadable:
        assert decompose.parse_claims(answer) is None, answer

    # A partially valid list (one good item, one bad) is refused whole —
    # never a partial list of only the good claim.
    partially_valid = json.dumps({"claims": [valid_item, {"text": "y"}]})
    assert decompose.parse_claims(partially_valid) is None

    good_answer = json.dumps({"claims": [valid_item, {**valid_item, "kind": "ambiguous"}]})
    result = decompose.parse_claims(good_answer)
    assert result == [valid_item, {**valid_item, "kind": "ambiguous"}]

    # Surrounding prose is stripped: the JSON object is still found and parsed.
    wrapped = f"Sure, here is the answer:\n{good_answer}\nLet me know if you need more."
    assert decompose.parse_claims(wrapped) == result


def test_claim_draft_and_unit_held_shapes():
    source = model.Source(
        source_id=LP_SOURCE,
        chunk=LP_CHUNK,
        span="The museum stands on Fifth Avenue.",
        as_of=2023,
        rights_basis="owned_copy",
    )
    # A complete draft with the OLD vocabulary: only `kind` is wrong, so the
    # refusal proves the Literal, not a missing field.
    with pytest.raises(ValidationError):
        decompose.ClaimDraft(
            claim_id="c01",
            text="The museum stands on Fifth Avenue.",
            kind="fact",
            source=source,
        )
    frozen_draft = decompose.ClaimDraft(
        claim_id="c01",
        text="The museum stands on Fifth Avenue.",
        kind="state",
        source=source,
    )
    with pytest.raises(ValidationError):
        frozen_draft.text = "mutated"

    for kind in ("event", "state", "belief"):
        draft = decompose.ClaimDraft(
            claim_id="c01",
            text="The museum stands on Fifth Avenue.",
            kind=kind,
            source=source,
        )
        assert draft.kind == kind
        assert draft.source == source

    held = decompose.UnitHeld("some-unit-key", "P1", "batch unit expired")
    assert isinstance(held, Exception)
    assert not isinstance(held, ValueError)
    assert held.unit_key == "some-unit-key"
    assert held.phase == "P1"
    assert held.reason == "batch unit expired"


def test_p1_plan_rows_derive_from_the_real_prompt():
    assert decompose.P1_MAX_TOKENS == 8000
    assert isinstance(decompose.P1_PLAN, tuple)
    assert len(decompose.P1_PLAN) == 2

    expected_overheads = [
        max(1, len(prompts.DECOMPOSE_PROMPT) // 4),
        max(1, len(prompts.REDO_PROMPT) // 4),
    ]
    for row, expected_overhead in zip(decompose.P1_PLAN, expected_overheads, strict=True):
        assert row.phase == "P1"
        assert row.role == "author"
        assert row.calls_per_unit == 1
        assert row.overhead_tokens == expected_overhead
        assert row.overhead_tokens > 0
        assert row.expected_output_tokens == decompose.P1_MAX_TOKENS

    events, sink = _sink_and_events()
    mock = llm.MockClient(sink)
    unit = _real_unit()
    estimate = mock.estimate([unit.text], list(decompose.P1_PLAN))
    assert len(estimate.rows) == 2
    for cost_row in estimate.rows:
        assert cost_row.model_id == "claude-opus-5"
        assert cost_row.batch is True
    assert events and events[0][0] == "cost_estimate"


def _armed_mock(batch_answers: dict) -> llm.MockClient:
    """A MockClient scripted with `batch_answers`, its estimate gate
    already armed against the real chunk text and the real P1_PLAN — the
    shape every test below needs before it can call decompose()."""
    _mock_events, mock_sink = _sink_and_events()
    mock = llm.MockClient(mock_sink, batch_answers=batch_answers)
    mock.estimate([_real_unit().text], list(decompose.P1_PLAN))
    return mock


def test_fixture_pins_are_clean():
    """AC-58: every scripted 'clean' P1 answer in CLEAN_ANSWERS passes the
    real gates against the real chunk text — a claim that a later step's
    refusal loop would reject must fail HERE, never downstream."""
    unit = _real_unit()
    for answer in CLEAN_ANSWERS:
        assert gates.claim_gates(answer["text"], answer["span"], unit.text) == []
        assert gates.span_in_unit(answer["span"], unit.text) is None


def test_clean_first_answer_returns_claim_drafts_in_one_call():
    unit = _real_unit()
    clean_claims = CLEAN_ANSWERS[:3]
    mock = _armed_mock(
        {
            unit.custom_id(1): llm.MockAnswer(
                text=json.dumps({"claims": clean_claims}), model_id="claude-opus-5"
            )
        }
    )
    proxy = _RecordingBatchClient(mock)
    decompose_events, decompose_sink = _sink_and_events()

    result = decompose.decompose(unit, proxy, events=decompose_sink)

    assert [draft.claim_id for draft in result] == ["c01", "c02", "c03"]
    for draft, answer in zip(result, clean_claims, strict=True):
        assert draft.text == answer["text"]
        assert draft.kind == gates.default_kind(answer["kind"])
        assert draft.source.span == answer["span"]
        assert draft.source.source_id == unit.source_id
        assert draft.source.chunk == unit.chunk
        assert draft.source.as_of == unit.as_of
        assert draft.source.rights_basis == unit.rights_basis

    assert mock.calls == [("author", "P1")]
    assert len(proxy.batch_calls) == 1
    call = proxy.batch_calls[0]
    assert call["role"] == "author"
    assert call["phase"] == "P1"
    assert call["schema"] == prompts.P1_RESPONSE_SCHEMA
    assert call["max_tokens"] == decompose.P1_MAX_TOKENS
    assert call["prompts"] == [(unit.custom_id(1), call["prompts"][0][1])]
    assert unit.text in call["prompts"][0][1]

    assert not any(kind == "claims_refused" for kind, _ in decompose_events)


def test_decompose_never_arms_the_estimate_itself():
    unit = _real_unit()
    clean_answer = llm.MockAnswer(
        text=json.dumps({"claims": CLEAN_ANSWERS[:1]}), model_id="claude-opus-5"
    )

    # Unarmed: decompose() never calls estimate() itself, so an unarmed
    # client's complete_batch() raises EstimateNotPrinted before any call.
    _mock_events, mock_sink = _sink_and_events()
    unarmed_mock = llm.MockClient(mock_sink, batch_answers={unit.custom_id(1): clean_answer})
    decompose_events, decompose_sink = _sink_and_events()
    with pytest.raises(llm.EstimateNotPrinted):
        decompose.decompose(unit, unarmed_mock, events=decompose_sink)
    assert unarmed_mock.calls == []
    assert decompose_events == []

    # Armed, but with no '-a1' answer scripted at all: MockScriptExhausted
    # propagates as itself, never wrapped as UnitHeld.
    empty_mock = _armed_mock({})
    decompose_events2, decompose_sink2 = _sink_and_events()
    with pytest.raises(llm.MockScriptExhausted):
        decompose.decompose(unit, empty_mock, events=decompose_sink2)
    assert decompose_events2 == []


def test_kind_default_and_missing_kind():
    unit = _real_unit()

    # kind 'ambiguous' -> accepted in one call, resolved to 'state'.
    ambiguous_claim = {**CLEAN_ANSWERS[0], "kind": "ambiguous"}
    mock = _armed_mock(
        {
            unit.custom_id(1): llm.MockAnswer(
                text=json.dumps({"claims": [ambiguous_claim]}), model_id="claude-opus-5"
            )
        }
    )
    decompose_events, decompose_sink = _sink_and_events()
    result = decompose.decompose(unit, mock, events=decompose_sink)
    assert len(result) == 1
    assert result[0].kind == "state"
    assert mock.calls == [("author", "P1")]

    # kind 'fact' (invalid) and a missing 'kind' key are both schema-level
    # refusals on attempt 1, with a clean re-ask under custom_id(2).
    bad_items = (
        {**CLEAN_ANSWERS[0], "kind": "fact"},
        {"text": CLEAN_ANSWERS[0]["text"], "span": CLEAN_ANSWERS[0]["span"]},
    )
    for bad_item in bad_items:
        mock = _armed_mock(
            {
                unit.custom_id(1): llm.MockAnswer(
                    text=json.dumps({"claims": [bad_item]}), model_id="claude-opus-5"
                ),
                unit.custom_id(2): llm.MockAnswer(
                    text=json.dumps({"claims": CLEAN_ANSWERS[:1]}), model_id="claude-opus-5"
                ),
            }
        )
        decompose_events, decompose_sink = _sink_and_events()
        result = decompose.decompose(unit, mock, events=decompose_sink)
        assert len(result) == 1
        assert result[0].text == CLEAN_ANSWERS[0]["text"]
        assert mock.calls == [("author", "P1"), ("author", "P1")]
        assert len(decompose_events) == 1
        kind, payload = decompose_events[0]
        assert kind == "claims_refused"
        assert payload["unit_key"] == unit.key
        assert payload["attempt"] == 1
        assert payload["reasons"][0].startswith("schema:")


def _answer(claims: list[dict]) -> llm.MockAnswer:
    """One scripted P1 MockAnswer carrying `claims`."""
    return llm.MockAnswer(text=json.dumps({"claims": claims}), model_id="claude-opus-5")


def _refused_then_clean(unit, first_claims: list[dict], clean_claims: list[dict]):
    """Script attempt 1 with `first_claims` and attempt 2 with
    `clean_claims`, run decompose() through a recording proxy, and return
    (result, mock, proxy, events)."""
    mock = _armed_mock(
        {
            unit.custom_id(1): _answer(first_claims),
            unit.custom_id(2): _answer(clean_claims),
        }
    )
    proxy = _RecordingBatchClient(mock)
    events, sink = _sink_and_events()
    result = decompose.decompose(unit, proxy, events=sink)
    return result, mock, proxy, events


def test_span_not_in_unit_is_refused():
    """AC-32: a claim whose cited span is not in the chunk is refused on
    attempt 1, the reason names the span, and exactly one re-ask under
    custom_id(2) quotes that reason back."""
    unit = _real_unit()
    result, mock, proxy, events = _refused_then_clean(
        unit, [BAD_SPAN_CLAIM], CLEAN_ANSWERS[:2]
    )

    assert [(kind, payload["attempt"]) for kind, payload in events] == [("claims_refused", 1)]
    payload = events[0][1]
    assert payload["unit_key"] == unit.key
    assert len(payload["reasons"]) == 1
    reason = payload["reasons"][0]
    assert reason.startswith("span_not_in_unit")
    assert BAD_SPAN_CLAIM["span"] in reason

    assert [draft.text for draft in result] == [answer["text"] for answer in CLEAN_ANSWERS[:2]]
    assert [draft.claim_id for draft in result] == ["c01", "c02"]

    assert mock.calls == [("author", "P1"), ("author", "P1")]
    assert len(proxy.batch_calls) == 2
    second_prompts = proxy.batch_calls[1]["prompts"]
    assert [custom_id for custom_id, _ in second_prompts] == [unit.custom_id(2)]
    assert reason in second_prompts[0][1]


def test_lifted_claim_is_refused_attributed_quote_is_not():
    """AC-33: eight-plus consecutive chunk words outside a quotation are a
    lift; the same words inside an ATTRIBUTED quotation are not."""
    unit = _real_unit()
    result, mock, _proxy, events = _refused_then_clean(
        unit, [LIFTED_CLAIM], CLEAN_ANSWERS[:1]
    )
    assert [kind for kind, _ in events] == ["claims_refused"]
    reason = events[0][1]["reasons"][0]
    assert reason.startswith("lift")
    assert "construction was finally completed in 1959 after both wright" in reason.lower()
    assert [draft.text for draft in result] == [CLEAN_ANSWERS[0]["text"]]
    assert mock.calls == [("author", "P1"), ("author", "P1")]

    # The attributed quotation is exempt: accepted in ONE call, no refusal.
    mock2 = _armed_mock({unit.custom_id(1): _answer([ATTRIBUTED_QUOTE_CLAIM])})
    events2, sink2 = _sink_and_events()
    result2 = decompose.decompose(unit, mock2, events=sink2)
    assert [draft.text for draft in result2] == [ATTRIBUTED_QUOTE_CLAIM["text"]]
    assert mock2.calls == [("author", "P1")]
    assert events2 == []


def test_furniture_claim_is_refused():
    """AC-34: guidebook apparatus (a page number) and a listing (a phone
    number) are each refused by gates.leak, with one clean re-ask."""
    unit = _real_unit()
    for furniture in (LEAKING_PAGE_CLAIM, LEAKING_PHONE_CLAIM):
        result, mock, _proxy, events = _refused_then_clean(
            unit, [furniture], CLEAN_ANSWERS[:1]
        )
        assert [kind for kind, _ in events] == ["claims_refused"]
        reason = events[0][1]["reasons"][0]
        assert reason.startswith("leak"), furniture["text"]
        assert [draft.text for draft in result] == [CLEAN_ANSWERS[0]["text"]]
        assert mock.calls == [("author", "P1"), ("author", "P1")]


def test_dangling_claim_is_refused():
    """AC-35: a claim opening on a bare pronoun is refused; a demonstrative
    before a verb ('That is ...') is a complete sentence and is accepted."""
    unit = _real_unit()
    result, mock, _proxy, events = _refused_then_clean(
        unit, [DANGLING_CLAIM], CLEAN_ANSWERS[:1]
    )
    assert [kind for kind, _ in events] == ["claims_refused"]
    assert events[0][1]["reasons"][0].startswith("not_self_contained")
    assert [draft.text for draft in result] == [CLEAN_ANSWERS[0]["text"]]
    assert mock.calls == [("author", "P1"), ("author", "P1")]

    mock2 = _armed_mock({unit.custom_id(1): _answer([DEMONSTRATIVE_CLAIM])})
    events2, sink2 = _sink_and_events()
    result2 = decompose.decompose(unit, mock2, events=sink2)
    assert [draft.text for draft in result2] == [DEMONSTRATIVE_CLAIM["text"]]
    assert mock2.calls == [("author", "P1")]
    assert events2 == []


def test_second_refusal_drops_and_never_asks_a_third_time():
    """AC-36: a claim still failing on attempt 2 is DROPPED and logged —
    never moved, never re-asked a third time — and the surviving claims are
    renumbered contiguously from c01."""
    unit = _real_unit()
    still_dirty = [CLEAN_ANSWERS[0], LEAKING_PHONE_CLAIM, CLEAN_ANSWERS[1]]
    mock = _armed_mock(
        {
            unit.custom_id(1): _answer([LIFTED_CLAIM, LEAKING_PAGE_CLAIM]),
            unit.custom_id(2): _answer(still_dirty),
            # Scripted but never drawn: a third call must not happen.
            unit.key + "-a3": _answer(CLEAN_ANSWERS[:1]),
        }
    )
    proxy = _RecordingBatchClient(mock)
    events, sink = _sink_and_events()

    result = decompose.decompose(unit, proxy, events=sink)

    assert [draft.claim_id for draft in result] == ["c01", "c02"]
    assert [draft.text for draft in result] == [
        CLEAN_ANSWERS[0]["text"],
        CLEAN_ANSWERS[1]["text"],
    ]

    refusals = [payload for kind, payload in events if kind == "claims_refused"]
    assert len(refusals) == 1
    assert refusals[0]["attempt"] == 1
    attempt_one_reasons = refusals[0]["reasons"]
    assert len(attempt_one_reasons) == 2
    assert attempt_one_reasons[0].startswith("lift")
    assert attempt_one_reasons[1].startswith("leak")

    drops = [payload for kind, payload in events if kind == "claim_dropped"]
    assert len(drops) == 1
    assert drops[0]["unit_key"] == unit.key
    assert drops[0]["claim_text"] == LEAKING_PHONE_CLAIM["text"]
    assert drops[0]["reason"].startswith("leak")

    assert len(mock.calls) == 2
    assert len(proxy.batch_calls) == 2
    second_prompts = proxy.batch_calls[1]["prompts"]
    assert [custom_id for custom_id, _ in second_prompts] == [unit.custom_id(2)]
    for quoted in attempt_one_reasons:
        assert quoted in second_prompts[0][1]


def test_unreadable_answer_is_refused_then_held():
    """AC-37: an unreadable answer is a whole-answer refusal; twice
    unreadable holds the unit, and never a third call."""
    unit = _real_unit()
    mock = _armed_mock(
        {
            unit.custom_id(1): llm.MockAnswer(text="not json", model_id="claude-opus-5"),
            unit.custom_id(2): llm.MockAnswer(
                text='{"claims": []}', model_id="claude-opus-5"
            ),
        }
    )
    events, sink = _sink_and_events()
    with pytest.raises(decompose.UnitHeld) as raised:
        decompose.decompose(unit, mock, events=sink)
    assert raised.value.phase == "P1"
    assert raised.value.unit_key == unit.key
    assert len(mock.calls) == 2

    kinds = [kind for kind, _ in events]
    assert kinds == ["claims_refused", "claims_refused", "unit_held"]
    assert events[0][1]["reasons"][0].startswith("schema:")
    held_payload = events[-1][1]
    assert held_payload["unit_key"] == unit.key
    assert held_payload["phase"] == "P1"
    assert held_payload["reason"]

    # Unreadable once, then clean: attempt 2's claims are returned.
    result, mock2, _proxy, _events = _refused_then_clean(unit, [], CLEAN_ANSWERS[:1])
    assert [draft.text for draft in result] == [CLEAN_ANSWERS[0]["text"]]
    assert len(mock2.calls) == 2


def test_batch_failure_holds_the_unit_without_a_second_call():
    """AC-38: a BatchFailure result (not a raised exception) for the
    unit's own custom_id holds the unit immediately — no second call, even
    though a clean '-a2' answer is scripted and would otherwise be drawn."""
    unit = _real_unit()
    mock = _armed_mock(
        {
            unit.custom_id(1): llm.MockFailure("expired"),
            unit.custom_id(2): _answer(CLEAN_ANSWERS[:1]),
        }
    )
    events, sink = _sink_and_events()

    with pytest.raises(decompose.UnitHeld) as raised:
        decompose.decompose(unit, mock, events=sink)

    assert raised.value.phase == "P1"
    assert raised.value.unit_key == unit.key
    assert "expired" in raised.value.reason
    assert mock.calls == [("author", "P1")]

    kinds = [kind for kind, _ in events]
    assert kinds == ["unit_held"]
    payload = events[0][1]
    assert payload["unit_key"] == unit.key
    assert payload["phase"] == "P1"
    assert "expired" in payload["reason"]


def test_llm_error_from_the_transport_holds_the_unit():
    """AC-39: EmptyCompletion and TruncatedCompletion raised by the
    transport hold the unit immediately, in exactly one call — the
    llm.LlmError itself is never leaked to the caller as UnitHeld wraps
    it."""
    unit = _real_unit()

    empty_mock = _armed_mock(
        {unit.custom_id(1): llm.MockAnswer(text="   ", model_id="claude-opus-5")}
    )
    events, sink = _sink_and_events()
    with pytest.raises(decompose.UnitHeld) as raised:
        decompose.decompose(unit, empty_mock, events=sink)
    assert raised.value.phase == "P1"
    assert raised.value.unit_key == unit.key
    assert empty_mock.calls == [("author", "P1")]
    assert [kind for kind, _ in events] == ["unit_held"]

    truncated_mock = _armed_mock(
        {
            unit.custom_id(1): llm.MockAnswer(
                text=json.dumps({"claims": CLEAN_ANSWERS[:1]}),
                model_id="claude-opus-5",
                stop_reason="max_tokens",
            )
        }
    )
    events2, sink2 = _sink_and_events()
    with pytest.raises(decompose.UnitHeld) as raised2:
        decompose.decompose(unit, truncated_mock, events=sink2)
    assert raised2.value.phase == "P1"
    assert raised2.value.unit_key == unit.key
    assert truncated_mock.calls == [("author", "P1")]
    assert [kind for kind, _ in events2] == ["unit_held"]


class _RawValueErrorClient:
    """A duck-typed ModelClient whose `complete_batch` raises a bare
    ValueError — never an `llm.LlmError` — mirroring the src/tour
    empty-succeeded-unit transport path (slice-2 carry-forward 3)."""

    def __init__(self) -> None:
        self.calls = 0

    def complete_batch(self, *, role, prompts, schema, phase, max_tokens):
        self.calls += 1
        raise ValueError("no text")


def test_raw_value_error_from_the_transport_holds_the_unit():
    """AC-40: a bare ValueError (not an llm.LlmError) from the transport
    holds the unit in exactly one call, never a re-submission."""
    unit = _real_unit()
    client = _RawValueErrorClient()
    events, sink = _sink_and_events()

    with pytest.raises(decompose.UnitHeld) as raised:
        decompose.decompose(unit, client, events=sink)

    assert raised.value.phase == "P1"
    assert raised.value.unit_key == unit.key
    assert "no text" in raised.value.reason
    assert client.calls == 1
    assert [kind for kind, _ in events] == ["unit_held"]


def test_no_live_client_in_this_file():
    """AC-55: this $0-spend test file never names a live LLM client or
    reads its API key. Its own body is exempt from the walk below — this
    docstring and the assert messages name those things on purpose to
    describe the rule, which is not the violation the rule guards against.
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
                    "this file must never read ANTHROPIC_API_KEY"
                )
