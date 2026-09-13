"""Tests for src/ingest/llm.py — specs/2026-09-10-ingest-slice-2.

Step 1 covers AC-1, AC-2 and AC-3 (see run-context.md for the verbatim
text): the ROLE_MODEL / PHASE_ROLE / BATCH_PHASES tables (read-only), the
PRICES_USD_PER_MTOK table plus its pinned constants, and same_model()'s
dated-snapshot-suffix normalization.

Step 2 covers AC-5 (MockClient half only — the AnthropicClient sibling
lands in step 6), AC-10, AC-13 and AC-14: MockClient construction's
judge-as-author refusal, count_tokens(), and estimate()'s arithmetic +
memoized token counting + cost_estimate event.

Step 3 covers AC-6 (MockClient.complete() sync half — the batch half is
step 4's own node id), AC-9, AC-11 (mock half), AC-12 (mock half), and
AC-18 (mock sync half — the batch half is step 4's own node id):
MockClient.complete()'s validation order (UnknownRole/UnknownPhase/
WrongTransport/PhaseRoleMismatch before the per-instance estimate gate),
the estimate gate itself (EstimateNotPrinted), and the post-response
refusals (EmptyCompletion, TruncatedCompletion, MockScriptExhausted).
estimate() also gains its own UnknownRole check (a plan row naming a role
absent from `roles`), which AC-9 requires and step 2 did not cover.

Step 4 covers AC-6 (batch half), AC-8, AC-16 (mock half), AC-17, and
AC-18 (batch half): MockClient.complete_batch()'s validation order
(the same UnknownRole/UnknownPhase/WrongTransport/PhaseRoleMismatch
checks as complete(), plus the Batch API's own custom_id contract —
format and duplicates — all before the per-instance estimate gate), the
estimate gate itself, MockScriptExhausted naming the custom_id when
`batch_answers` has no entry for it (the mock never fabricates a batch
answer either), and a scripted MockFailure surfacing as a BatchFailure
in the result dict rather than being dropped.

Step 5 covers AC-4 (judge-independence layer (b), the post-response
same_model refusal, asserted on both MockClient.complete_batch() and
MockClient.complete() in one node id) and AC-7's mock half
(Completion.model_id comes from the scripted answer, never from
ROLE_MODEL). Layer (b) itself was built into complete()/complete_batch()
in steps 3-4; this step is the test that pins it down as its own
acceptance criterion, independent of layer (a)'s construction-time
refusal (AC-5).

Step 6 covers AC-5 (the AnthropicClient sibling of step 2's MockClient
half), AC-20 (its step-6 half: the SYNC SDK object comes from
src.tour.anthropic_client.batch_review_client(), built lazily on first
use and exactly once — the batch-submission half is step 8's own node
id) and AC-21 (src/ingest/llm.py imports and constructs no SDK at module
level, asserted over the module's own ast).

Step 7 covers AC-7 (the AnthropicClient sibling of step 5's mock half:
Completion.model_id comes from response.model, never ROLE_MODEL), AC-11
(the AnthropicClient sibling: empty content or a whitespace-only text
block -> EmptyCompletion), AC-12 (the AnthropicClient sibling:
stop_reason == 'max_tokens' -> TruncatedCompletion with the partial text
attached), and AC-19 (AnthropicClient.complete()'s request to
sdk.messages.create matches the pinned shape exactly — no extra keys,
schema=None omits output_config).

Step 8 covers AC-15 (AnthropicClient.complete_batch() runs the whole
round through src.tour.batch_transport — submit_batch with the pinned
per-unit request shape, poll_batch, collect_results — and emits the
'batch_submitted' event after the estimate), AC-16 (the AnthropicClient
sibling of step 4's mock half: an errored unit and a unit the results
stream never mentions both surface as BatchFailure rather than raising
or being dropped, because the result dict is built by iterating the
SUBMITTED prompts) and AC-20's step-8 half (with no submit_sdk injected,
the SUBMISSION client comes from src.tour.batch_transport.batch_client()
with zero retries — monkeypatched on the module, never via a sys.modules
'anthropic' stub, because submit_batch imports anthropic.types at call
time).
"""

from __future__ import annotations

import ast
import json
import pathlib
import sys
import types

import pytest

import src.ingest.llm as llm
from src.tour import anthropic_client


def _sink_and_events():
    """A recording EventSink: events.append((kind, payload)) per call."""
    events: list[tuple[str, dict]] = []

    def sink(kind: str, payload: dict) -> None:
        events.append((kind, payload))

    return events, sink


def _message_stub(
    *,
    text: str = "ok",
    model: str = "claude-opus-5",
    stop_reason: str = "end_turn",
    response_id: str = "msg_1",
    content: list | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
):
    """A Message-like stub response shaped per decisions.stub_sdk_contract
    (types.SimpleNamespace with .id, .model, .stop_reason, .content, and a
    .usage carrying all-int counters)."""
    if content is None:
        content = [types.SimpleNamespace(type="text", text=text)]
    return types.SimpleNamespace(
        id=response_id,
        model=model,
        stop_reason=stop_reason,
        content=content,
        usage=types.SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        ),
    )


def _sdk_stub(create_calls: list[dict], response):
    """A minimal sdk stub whose messages.create(**kwargs) records kwargs
    into `create_calls` and always returns `response`."""

    def _create(**kwargs):
        create_calls.append(kwargs)
        return response

    return types.SimpleNamespace(messages=types.SimpleNamespace(create=_create))


def test_role_and_phase_tables_match_the_spec():
    assert dict(llm.ROLE_MODEL) == {
        "author": "claude-opus-5",
        "claim_judge": "claude-haiku-4-5",
        "narration_judge": "claude-haiku-4-5",
        "merge_judge": "claude-sonnet-5",
    }
    assert dict(llm.PHASE_ROLE) == {
        "P1": "author",
        "P2": "author",
        "P3": "claim_judge",
        "P4": "author",
        "P5": "narration_judge",
        "P6": "merge_judge",
    }
    assert {"P1", "P3", "P5"} == llm.BATCH_PHASES

    with pytest.raises(TypeError):
        llm.ROLE_MODEL["author"] = "claude-haiku-4-5"
    with pytest.raises(TypeError):
        llm.PHASE_ROLE["P1"] = "claim_judge"


def test_price_table_is_pinned_and_covers_every_role():
    assert dict(llm.PRICES_USD_PER_MTOK) == {
        "claude-opus-5": (5.0, 25.0),
        "claude-sonnet-5": (2.0, 10.0),
        "claude-haiku-4-5": (1.0, 5.0),
    }
    assert llm.PRICES_CACHED_ON == "2026-06-24"
    assert llm.BATCH_DISCOUNT == 0.5
    for model_id in llm.ROLE_MODEL.values():
        assert model_id in llm.PRICES_USD_PER_MTOK


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("claude-opus-5-20260115", "claude-opus-5", True),
        ("claude-opus-5", "claude-opus-5", True),
        ("claude-opus-5-1", "claude-opus-5", False),
        ("claude-haiku-4-5-20251001", "claude-opus-5", False),
        ("x-20260115-20260116", "x", False),
    ],
)
def test_same_model_strips_one_dated_snapshot_suffix(a, b, expected):
    assert llm.same_model(a, b) is expected


def test_judge_configured_as_author_is_refused_at_construction():
    events, sink = _sink_and_events()
    roles = {**llm.ROLE_MODEL, "narration_judge": "claude-opus-5"}
    with pytest.raises(llm.JudgeIsAuthor) as exc_info:
        llm.MockClient(sink, roles=roles)
    assert exc_info.value.code == "JUDGE_IS_AUTHOR"
    assert events == []

    events2, sink2 = _sink_and_events()
    roles2 = {**llm.ROLE_MODEL, "merge_judge": "claude-opus-5-20260301"}
    with pytest.raises(llm.JudgeIsAuthor) as exc_info2:
        llm.MockClient(sink2, roles=roles2)
    assert exc_info2.value.code == "JUDGE_IS_AUTHOR"
    assert events2 == []


def test_unpriced_model_is_refused_before_counting():
    events, sink = _sink_and_events()
    mock = llm.MockClient(sink, count_tokens_fn=lambda _m, _t: 1000)
    plan = [llm.PhaseCall("P2", "author", 1, 0, 1)]
    with pytest.raises(llm.UnknownModelPrice, match="claude-opus-5"):
        mock.estimate(["u1"], plan, prices={"claude-haiku-4-5": (1.0, 5.0)})
    assert mock.count_calls == []
    assert events == []


def test_estimate_arithmetic_matches_worked_example():
    events, sink = _sink_and_events()
    mock = llm.MockClient(sink, count_tokens_fn=lambda _m, _t: 1000)
    units = ["u1", "u2", "u3"]
    plan = [
        llm.PhaseCall("P3", "claim_judge", 1, 200, 400),
        llm.PhaseCall("P2", "author", 1, 200, 400),
    ]
    result = mock.estimate(units, plan)

    p3, p2 = result.rows
    assert p3.phase == "P3"
    assert p3.role == "claim_judge"
    assert p3.model_id == "claude-haiku-4-5"
    assert p3.batch is True
    assert p3.calls == 3
    assert p3.input_tokens == 3600
    assert p3.output_tokens == 1200
    assert round(p3.usd, 6) == 0.0048

    assert p2.phase == "P2"
    assert p2.role == "author"
    assert p2.model_id == "claude-opus-5"
    assert p2.batch is False
    assert p2.calls == 3
    assert p2.input_tokens == 3600
    assert p2.output_tokens == 1200
    assert round(p2.usd, 6) == 0.048

    assert result.units == 3
    assert result.total_input_tokens == 7200
    assert result.total_output_tokens == 2400
    assert round(result.total_usd, 6) == 0.0528
    assert result.prices_cached_on == "2026-06-24"

    payload = result.as_dict()
    assert json.loads(json.dumps(payload)) == payload
    assert events == [("cost_estimate", payload)]

    # calls_per_unit=2 on P3 doubles only that row.
    _events2, sink2 = _sink_and_events()
    mock2 = llm.MockClient(sink2, count_tokens_fn=lambda _m, _t: 1000)
    plan_doubled = [
        llm.PhaseCall("P3", "claim_judge", 2, 200, 400),
        llm.PhaseCall("P2", "author", 1, 200, 400),
    ]
    result2 = mock2.estimate(units, plan_doubled)
    p3_doubled, p2_unchanged = result2.rows
    assert p3_doubled.calls == 6
    assert p3_doubled.input_tokens == 7200
    assert p3_doubled.output_tokens == 2400
    assert round(p3_doubled.usd, 6) == 0.0096
    assert p2_unchanged.calls == 3


def test_count_tokens_is_called_once_per_model_and_unit():
    _events, sink = _sink_and_events()
    mock = llm.MockClient(sink, count_tokens_fn=lambda _m, _t: 1000)
    units = ["u1", "u2", "u3"]
    plan = [
        llm.PhaseCall("P3", "claim_judge", 1, 200, 400),
        llm.PhaseCall("P2", "author", 1, 200, 400),
    ]
    mock.estimate(units, plan)
    assert len(mock.count_calls) == 6
    assert set(mock.count_calls) == {
        ("claude-haiku-4-5", "u1"),
        ("claude-haiku-4-5", "u2"),
        ("claude-haiku-4-5", "u3"),
        ("claude-opus-5", "u1"),
        ("claude-opus-5", "u2"),
        ("claude-opus-5", "u3"),
    }

    _events3, sink3 = _sink_and_events()
    mock3 = llm.MockClient(sink3, count_tokens_fn=lambda _m, _t: 1000)
    plan3 = [
        llm.PhaseCall("P1", "author", 1, 0, 1),
        llm.PhaseCall("P2", "author", 1, 0, 1),
        llm.PhaseCall("P4", "author", 1, 0, 1),
    ]
    mock3.estimate(units, plan3)
    assert len(mock3.count_calls) == 3

    events4, sink4 = _sink_and_events()
    mock4 = llm.MockClient(sink4, count_tokens_fn=lambda _m, _t: 1000)
    result4 = mock4.estimate([], plan)
    assert result4.rows == []
    assert result4.total_usd == 0.0
    assert mock4.count_calls == []
    assert len(events4) == 1
    assert events4[0][0] == "cost_estimate"


def test_estimate_is_printed_before_any_completion():
    events, sink = _sink_and_events()
    mock = llm.MockClient(
        sink, answers={"author": [llm.MockAnswer(text="hello", model_id="claude-opus-5")]}
    )
    with pytest.raises(llm.EstimateNotPrinted):
        mock.complete("author", "p", None, phase="P2", max_tokens=8)
    assert mock.calls == []

    plan = [llm.PhaseCall("P2", "author", 1, 0, 1)]
    result = mock.estimate(["u"], plan)
    assert events[0] == ("cost_estimate", result.as_dict())

    completion = mock.complete("author", "p", None, phase="P2", max_tokens=8)
    assert isinstance(completion, llm.Completion)
    assert completion.text == "hello"
    assert completion.model_id == "claude-opus-5"

    # A second MockClient sharing the same sink still gates independently
    # (the estimate flag is per instance, not per sink).
    mock2 = llm.MockClient(
        sink, answers={"author": [llm.MockAnswer(text="hi", model_id="claude-opus-5")]}
    )
    with pytest.raises(llm.EstimateNotPrinted):
        mock2.complete("author", "p", None, phase="P2", max_tokens=8)
    assert mock2.calls == []


def test_unknown_role_or_phase_is_refused_before_any_call():
    events, sink = _sink_and_events()
    mock = llm.MockClient(sink)
    mock.estimate([], [])  # arm the gate with a no-op estimate

    with pytest.raises(llm.UnknownRole):
        mock.complete("reviewer", "p", None, phase="P2", max_tokens=8)
    assert mock.calls == []
    assert mock.count_calls == []

    with pytest.raises(llm.UnknownPhase):
        mock.complete("author", "p", None, phase="P9", max_tokens=8)
    assert mock.calls == []
    assert mock.count_calls == []

    with pytest.raises(llm.UnknownRole):
        mock.estimate(["u"], [llm.PhaseCall("P2", "reviewer", 1, 0, 1)])
    assert mock.calls == []
    assert mock.count_calls == []
    assert len(events) == 1 and events[0][0] == "cost_estimate"  # only the arming call emitted


def test_empty_completion_text_is_refused():
    _events, sink = _sink_and_events()
    mock = llm.MockClient(
        sink, answers={"author": [llm.MockAnswer(text="   ", model_id="claude-opus-5")]}
    )
    mock.estimate(["u"], [llm.PhaseCall("P2", "author", 1, 0, 1)])
    with pytest.raises(llm.EmptyCompletion):
        mock.complete("author", "p", None, phase="P2", max_tokens=8)

    mock2 = llm.MockClient(
        sink, answers={"author": [llm.MockAnswer(text="", model_id="claude-opus-5")]}
    )
    mock2.estimate(["u"], [llm.PhaseCall("P2", "author", 1, 0, 1)])
    with pytest.raises(llm.EmptyCompletion):
        mock2.complete("author", "p", None, phase="P2", max_tokens=8)


def test_truncated_output_is_refused_with_partial_text_attached():
    _events, sink = _sink_and_events()
    mock = llm.MockClient(
        sink,
        answers={
            "author": [
                llm.MockAnswer(
                    text="{'claims': [",
                    model_id="claude-opus-5",
                    stop_reason="max_tokens",
                )
            ]
        },
    )
    mock.estimate(["u"], [llm.PhaseCall("P2", "author", 1, 0, 1)])
    with pytest.raises(llm.TruncatedCompletion) as exc_info:
        mock.complete("author", "p", None, phase="P2", max_tokens=8)
    assert exc_info.value.text == "{'claims': ["

    mock2 = llm.MockClient(
        sink,
        answers={
            "author": [
                llm.MockAnswer(text="ok1", model_id="claude-opus-5", stop_reason="end_turn"),
                llm.MockAnswer(
                    text="ok2", model_id="claude-opus-5", stop_reason="stop_sequence"
                ),
            ]
        },
    )
    mock2.estimate(["u"], [llm.PhaseCall("P2", "author", 1, 0, 1)])
    c1 = mock2.complete("author", "p", None, phase="P2", max_tokens=8)
    c2 = mock2.complete("author", "p", None, phase="P2", max_tokens=8)
    assert c1.stop_reason == "end_turn"
    assert c2.stop_reason == "stop_sequence"


def test_mock_never_invents_a_sync_answer():
    _events, sink = _sink_and_events()
    mock = llm.MockClient(
        sink, answers={"author": [llm.MockAnswer(text="only one", model_id="claude-opus-5")]}
    )
    mock.estimate(["u"], [llm.PhaseCall("P2", "author", 1, 0, 1)])
    mock.complete("author", "p", None, phase="P2", max_tokens=8)
    with pytest.raises(llm.MockScriptExhausted, match="author"):
        mock.complete("author", "p", None, phase="P2", max_tokens=8)


def test_batch_phase_on_sync_path_and_vice_versa_are_refused():
    events, sink = _sink_and_events()
    mock = llm.MockClient(sink)
    mock.estimate([], [])  # arm the gate with a no-op estimate

    with pytest.raises(llm.WrongTransport):
        mock.complete("author", "p", None, phase="P1", max_tokens=8)
    assert mock.calls == []

    with pytest.raises(llm.WrongTransport):
        mock.complete_batch("author", [("a", "p")], None, phase="P2", max_tokens=8)
    assert mock.calls == []

    with pytest.raises(llm.PhaseRoleMismatch):
        mock.complete("claim_judge", "p", None, phase="P2", max_tokens=8)
    assert mock.calls == []
    assert len(events) == 1 and events[0][0] == "cost_estimate"  # only the arming call emitted


def test_estimate_is_printed_before_any_batch():
    events, sink = _sink_and_events()
    mock = llm.MockClient(
        sink, batch_answers={"a": llm.MockAnswer(text="hello", model_id="claude-opus-5")}
    )
    with pytest.raises(llm.EstimateNotPrinted):
        mock.complete_batch("author", [("a", "p")], None, phase="P1", max_tokens=8)
    assert mock.calls == []

    plan = [llm.PhaseCall("P1", "author", 1, 0, 1)]
    result = mock.estimate(["u"], plan)
    assert events[0] == ("cost_estimate", result.as_dict())

    results = mock.complete_batch("author", [("a", "p")], None, phase="P1", max_tokens=8)
    completion = results["a"]
    assert isinstance(completion, llm.Completion)
    assert completion.text == "hello"
    assert completion.model_id == "claude-opus-5"
    assert completion.usage.batch is True
    assert completion.usage.batch_id


def test_mock_never_invents_a_batch_answer():
    _events, sink = _sink_and_events()
    mock = llm.MockClient(
        sink, batch_answers={"c1": llm.MockAnswer(text="ok", model_id="claude-haiku-4-5")}
    )
    mock.estimate(["u"], [llm.PhaseCall("P3", "claim_judge", 1, 0, 1)])
    with pytest.raises(llm.MockScriptExhausted, match="c2"):
        mock.complete_batch(
            "claim_judge", [("c1", "p"), ("c2", "p")], None, phase="P3", max_tokens=8
        )


def test_errored_expired_or_missing_batch_units_are_surfaced_never_dropped():
    _events, sink = _sink_and_events()
    mock = llm.MockClient(
        sink,
        batch_answers={
            "c1": llm.MockAnswer(text="ok", model_id="claude-haiku-4-5"),
            "c2": llm.MockFailure("expired"),
        },
    )
    mock.estimate(["u1", "u2"], [llm.PhaseCall("P3", "claim_judge", 1, 0, 1)])
    results = mock.complete_batch(
        "claim_judge", [("c1", "p1"), ("c2", "p2")], None, phase="P3", max_tokens=8
    )

    assert set(results) == {"c1", "c2"}
    assert isinstance(results["c1"], llm.Completion)
    failure = results["c2"]
    assert isinstance(failure, llm.BatchFailure)
    assert failure.custom_id == "c2"
    assert failure.result_type == "expired"
    assert failure.error_message is None
    assert failure.batch_id  # non-empty — surfaced, not silently dropped


def test_mock_batch_refuses_ids_the_batch_api_would_reject():
    _events, sink = _sink_and_events()
    mock = llm.MockClient(sink)
    mock.estimate([], [])  # arm the gate with a no-op estimate

    with pytest.raises(ValueError, match="custom_id"):
        mock.complete_batch("author", [("case:0", "p")], None, phase="P1", max_tokens=8)
    assert mock.calls == []

    with pytest.raises(ValueError, match="duplicate"):
        mock.complete_batch(
            "author", [("c1", "a"), ("c1", "b")], None, phase="P1", max_tokens=8
        )
    assert mock.calls == []


def test_judge_role_refuses_author_model_id():
    # Batch path: claim_judge/P3 answering with the author's model id.
    _events, sink = _sink_and_events()
    mock = llm.MockClient(
        sink,
        batch_answers={
            "c1": llm.MockAnswer(text='{"entailed": true}', model_id="claude-opus-5")
        },
    )
    mock.estimate(["u"], [llm.PhaseCall("P3", "claim_judge", 1, 0, 1)])
    with pytest.raises(llm.JudgeIsAuthor) as exc_info:
        mock.complete_batch("claim_judge", [("c1", "q")], None, phase="P3", max_tokens=64)
    assert exc_info.value.code == "JUDGE_IS_AUTHOR"
    assert "c1" in str(exc_info.value)

    # Sync path: merge_judge/P6 answering with a dated snapshot of the
    # author's model id — caught via same_model.
    _events2, sink2 = _sink_and_events()
    mock2 = llm.MockClient(
        sink2,
        answers={
            "merge_judge": [
                llm.MockAnswer(text="verdict", model_id="claude-opus-5-20260115")
            ]
        },
    )
    mock2.estimate(["u"], [llm.PhaseCall("P6", "merge_judge", 1, 0, 1)])
    with pytest.raises(llm.JudgeIsAuthor) as exc_info2:
        mock2.complete("merge_judge", "q", None, phase="P6", max_tokens=64)
    assert exc_info2.value.code == "JUDGE_IS_AUTHOR"

    # Same setups answering with the judge's OWN model id return a
    # Completion instead — the refusal fires only on the author's model id.
    _events3, sink3 = _sink_and_events()
    mock3 = llm.MockClient(
        sink3,
        batch_answers={
            "c1": llm.MockAnswer(text='{"entailed": true}', model_id="claude-haiku-4-5")
        },
    )
    mock3.estimate(["u"], [llm.PhaseCall("P3", "claim_judge", 1, 0, 1)])
    result3 = mock3.complete_batch("claim_judge", [("c1", "q")], None, phase="P3", max_tokens=64)
    assert isinstance(result3["c1"], llm.Completion)

    _events4, sink4 = _sink_and_events()
    mock4 = llm.MockClient(
        sink4, answers={"merge_judge": [llm.MockAnswer(text="verdict", model_id="claude-sonnet-5")]}
    )
    mock4.estimate(["u"], [llm.PhaseCall("P6", "merge_judge", 1, 0, 1)])
    completion4 = mock4.complete("merge_judge", "q", None, phase="P6", max_tokens=64)
    assert isinstance(completion4, llm.Completion)


def test_completion_model_id_comes_from_the_response():
    _events, sink = _sink_and_events()
    mock = llm.MockClient(
        sink,
        batch_answers={
            "c1": llm.MockAnswer(
                text="t",
                model_id="claude-haiku-4-5-20251001",
                input_tokens=12,
                output_tokens=3,
                stop_reason="end_turn",
            )
        },
    )
    mock.estimate(["u"], [llm.PhaseCall("P3", "claim_judge", 1, 0, 1)])
    results = mock.complete_batch("claim_judge", [("c1", "q")], None, phase="P3", max_tokens=64)
    completion = results["c1"]

    assert isinstance(completion, llm.Completion)
    assert completion.model_id == "claude-haiku-4-5-20251001"
    assert completion.usage == llm.Usage(
        input_tokens=12,
        output_tokens=3,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        batch=True,
        batch_id=completion.usage.batch_id,
        request_id=completion.usage.request_id,
    )
    assert completion.usage.batch_id
    assert completion.usage.request_id is None
    assert completion.stop_reason == "end_turn"


# ── Step 6: AnthropicClient construction, lazy bounded factories ────────────


def test_anthropic_client_refuses_judge_as_author_at_construction():
    """AC-5 (AnthropicClient half): layer (a) of the judge-independence gate
    fires at __init__ for the real client too — before any SDK call and
    before any event reaches the sink."""
    sdk_calls: list[tuple[str, dict]] = []

    def _recorder(kind: str):
        def _call(**kwargs):
            sdk_calls.append((kind, kwargs))
            return None

        return _call

    stub = types.SimpleNamespace(
        messages=types.SimpleNamespace(
            create=_recorder("create"), count_tokens=_recorder("count_tokens")
        )
    )

    events, sink = _sink_and_events()
    roles = {**llm.ROLE_MODEL, "narration_judge": "claude-opus-5"}
    with pytest.raises(llm.JudgeIsAuthor) as exc_info:
        llm.AnthropicClient(sink, sdk=stub, submit_sdk=stub, roles=roles)
    assert exc_info.value.code == "JUDGE_IS_AUTHOR"
    assert events == []
    assert sdk_calls == []

    events2, sink2 = _sink_and_events()
    roles2 = {**llm.ROLE_MODEL, "merge_judge": "claude-opus-5-20260301"}
    with pytest.raises(llm.JudgeIsAuthor) as exc_info2:
        llm.AnthropicClient(sink2, sdk=stub, submit_sdk=stub, roles=roles2)
    assert exc_info2.value.code == "JUDGE_IS_AUTHOR"
    assert events2 == []
    assert sdk_calls == []


def test_default_sdk_objects_come_from_the_bounded_factories(monkeypatch):
    """AC-20 (step-6 half): with no SDK injected, AnthropicClient builds its
    sync SDK object through src.tour.anthropic_client.batch_review_client()
    — LAZILY (nothing at construction), with that factory's bounded ceiling
    (300 s / 3 retries, never judge_client()'s 45 s), and exactly once for
    the life of the client."""
    constructions: list[dict] = []
    count_calls: list[dict] = []

    class _FakeMessages:
        def count_tokens(self, **kwargs):
            count_calls.append(kwargs)
            return types.SimpleNamespace(input_tokens=7)

    class _FakeAnthropic:
        def __init__(self, **kwargs):
            constructions.append(kwargs)
            self.messages = _FakeMessages()

    stub_module = types.ModuleType("anthropic")
    stub_module.Anthropic = _FakeAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", stub_module)

    events, sink = _sink_and_events()
    client = llm.AnthropicClient(sink)

    # Lazy: constructing the client constructs no SDK object at all.
    assert constructions == []
    assert events == []

    assert client.count_tokens("claude-opus-5", "hello") == 7
    assert len(constructions) == 1
    assert constructions[0]["timeout"] == anthropic_client.BATCH_REVIEW_TIMEOUT_S
    assert constructions[0]["max_retries"] == anthropic_client.BATCH_REVIEW_MAX_RETRIES
    assert count_calls == [
        {"model": "claude-opus-5", "messages": [{"role": "user", "content": "hello"}]}
    ]

    # A second call reuses the same bounded object rather than building another.
    assert client.count_tokens("claude-haiku-4-5", "hello again") == 7
    assert len(constructions) == 1
    assert count_calls[1] == {
        "model": "claude-haiku-4-5",
        "messages": [{"role": "user", "content": "hello again"}],
    }


def test_llm_module_never_imports_or_builds_the_sdk_at_module_level():
    """AC-21: importing src.ingest.llm must never drag in the anthropic SDK,
    and the module must never construct a bare anthropic.Anthropic() itself
    — every SDK object comes from src.tour's bounded factories."""
    source = pathlib.Path(llm.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] != "anthropic", (
                    f"top-level `import {alias.name}` makes the SDK an import-time "
                    "dependency of src/ingest"
                )
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] != "anthropic", (
                f"top-level `from {node.module} import ...` makes the SDK an "
                "import-time dependency of src/ingest"
            )

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            assert not (
                isinstance(func, ast.Attribute)
                and func.attr == "Anthropic"
                and isinstance(func.value, ast.Name)
                and func.value.id == "anthropic"
            ), (
                "src/ingest/llm.py must never construct anthropic.Anthropic() — use "
                "src.tour.anthropic_client's bounded factories"
            )


def test_sync_request_shape_is_exact():
    """AC-19: AnthropicClient.complete()'s request to sdk.messages.create
    matches the pinned shape (decisions.sdk_shapes) exactly — no thinking,
    no system, no prefill, no output_format — and schema=None omits
    output_config entirely rather than sending it as null."""
    create_calls: list[dict] = []
    response = _message_stub(text="hello", model="claude-opus-5")
    stub = _sdk_stub(create_calls, response)

    _events, sink = _sink_and_events()
    client = llm.AnthropicClient(sink, sdk=stub)
    client.estimate([], [])

    schema = {"type": "object", "properties": {}}
    completion = client.complete("author", "hello", schema, phase="P4", max_tokens=1024)

    assert isinstance(completion, llm.Completion)
    assert len(create_calls) == 1
    assert create_calls[0] == {
        "model": "claude-opus-5",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": "hello"}],
        "output_config": {"format": {"type": "json_schema", "schema": schema}},
    }

    create_calls.clear()
    completion2 = client.complete("author", "hello", None, phase="P4", max_tokens=1024)
    assert isinstance(completion2, llm.Completion)
    assert len(create_calls) == 1
    assert create_calls[0] == {
        "model": "claude-opus-5",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": "hello"}],
    }


def test_anthropic_completion_model_id_comes_from_the_response():
    """AC-7 (AnthropicClient sibling): model_id comes from response.model,
    never from ROLE_MODEL; usage.batch is False on the sync path and
    usage.request_id is the response's own .id."""
    create_calls: list[dict] = []
    response = _message_stub(
        text="ok", model="claude-opus-5-20260115", response_id="msg_abc"
    )
    stub = _sdk_stub(create_calls, response)

    _events, sink = _sink_and_events()
    client = llm.AnthropicClient(sink, sdk=stub)
    client.estimate([], [])

    completion = client.complete("author", "p", None, phase="P2", max_tokens=8)
    assert completion.model_id == "claude-opus-5-20260115"
    assert completion.usage.batch is False
    assert completion.usage.request_id == "msg_abc"


def test_anthropic_empty_completion_text_is_refused():
    """AC-11 (AnthropicClient sibling): empty content blocks or a single
    whitespace-only text block both refuse as EmptyCompletion, never a
    Completion."""
    _events, sink = _sink_and_events()

    response_no_content = _message_stub(content=[])
    stub1 = _sdk_stub([], response_no_content)
    client1 = llm.AnthropicClient(sink, sdk=stub1)
    client1.estimate([], [])
    with pytest.raises(llm.EmptyCompletion):
        client1.complete("author", "p", None, phase="P2", max_tokens=8)

    response_whitespace = _message_stub(text="   ")
    stub2 = _sdk_stub([], response_whitespace)
    client2 = llm.AnthropicClient(sink, sdk=stub2)
    client2.estimate([], [])
    with pytest.raises(llm.EmptyCompletion):
        client2.complete("author", "p", None, phase="P2", max_tokens=8)


def test_anthropic_truncated_output_is_refused():
    """AC-12 (AnthropicClient sibling): stop_reason == 'max_tokens' refuses
    as TruncatedCompletion with the partial text attached."""
    _events, sink = _sink_and_events()
    response = _message_stub(text='{"claims": [', stop_reason="max_tokens")
    stub = _sdk_stub([], response)
    client = llm.AnthropicClient(sink, sdk=stub)
    client.estimate([], [])

    with pytest.raises(llm.TruncatedCompletion) as exc_info:
        client.complete("author", "p", None, phase="P2", max_tokens=8)
    assert exc_info.value.text == '{"claims": ['


# ── Step 8: AnthropicClient.complete_batch through src.tour.batch_transport ─


def _batch_succeeded(
    custom_id: str,
    *,
    text: str,
    model: str = "claude-haiku-4-5",
    stop_reason: str = "end_turn",
    input_tokens: int = 11,
    output_tokens: int = 3,
):
    """One succeeded unit of a batches.results() stream, shaped per
    decisions.stub_sdk_contract (mirroring
    tests/test_tour_batch_candidate_runner.py:200-255). The usage counters
    are REAL ints and the text is non-empty: collect_results() runs the
    certification provider's own _response_text/_usage over this message
    and raises ValueError otherwise."""
    return types.SimpleNamespace(
        custom_id=custom_id,
        result=types.SimpleNamespace(
            type="succeeded",
            message=types.SimpleNamespace(
                id=f"msg_{custom_id}",
                model=model,
                stop_reason=stop_reason,
                content=[types.SimpleNamespace(type="text", text=text)],
                usage=types.SimpleNamespace(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_creation_input_tokens=0,
                    cache_read_input_tokens=0,
                ),
            ),
        ),
    )


def _batch_errored(custom_id: str, *, error_type: str, message: str):
    """One errored unit of a batches.results() stream."""
    return types.SimpleNamespace(
        custom_id=custom_id,
        result=types.SimpleNamespace(
            type="errored",
            error=types.SimpleNamespace(
                error=types.SimpleNamespace(type=error_type, message=message)
            ),
        ),
    )


def _batch_sdk_stub(unit_results, *, batch_id="msgbatch_stub_01"):
    """An SDK stub covering the Batch API surface batch_transport drives:
    messages.batches.create/retrieve/results, plus a messages.create
    recorder so a test can prove the SYNC endpoint was never touched.
    Returns (stub, record); `record` carries every observed call."""
    record = types.SimpleNamespace(
        created=[], retrieved=[], results_for=[], create_calls=[], batch_id=batch_id
    )

    class _Batches:
        def create(self, *, requests):
            record.created.append(list(requests))
            return types.SimpleNamespace(id=batch_id)

        def retrieve(self, requested_id):
            record.retrieved.append(requested_id)
            return types.SimpleNamespace(id=requested_id, processing_status="ended")

        def results(self, requested_id):
            record.results_for.append(requested_id)
            return iter(unit_results)

    def _create(**kwargs):
        record.create_calls.append(kwargs)
        return _message_stub()

    stub = types.SimpleNamespace(
        messages=types.SimpleNamespace(create=_create, batches=_Batches())
    )
    return stub, record


def test_batch_phase_goes_through_batch_transport():
    """AC-15: a batch phase runs submit_batch -> batch_submitted event ->
    poll_batch -> collect_results, never sdk.messages.create; each unit's
    submitted `params` is the pinned request shape; every returned
    Completion carries usage.batch True and the batch's own id."""
    schema = {"type": "object"}
    stub, record = _batch_sdk_stub(
        [
            _batch_succeeded(
                "c1", text='{"entailed": true}', model="claude-haiku-4-5-20251001"
            ),
            _batch_succeeded(
                "c2", text='{"entailed": false}', model="claude-haiku-4-5-20251001"
            ),
        ]
    )

    events, sink = _sink_and_events()
    client = llm.AnthropicClient(sink, sdk=stub, submit_sdk=stub, poll_interval_s=0)
    client.estimate([], [])

    results = client.complete_batch(
        "claim_judge", [("c1", "p1"), ("c2", "p2")], schema, phase="P3", max_tokens=64
    )

    # Submitted exactly once, through the SUBMISSION sdk, in prompt order.
    assert len(record.created) == 1
    submitted = record.created[0]
    assert [request["custom_id"] for request in submitted] == ["c1", "c2"]
    assert submitted[0]["params"] == {
        "model": "claude-haiku-4-5",
        "max_tokens": 64,
        "messages": [{"role": "user", "content": "p1"}],
        "output_config": {"format": {"type": "json_schema", "schema": schema}},
    }
    assert submitted[1]["params"] == {
        "model": "claude-haiku-4-5",
        "max_tokens": 64,
        "messages": [{"role": "user", "content": "p2"}],
        "output_config": {"format": {"type": "json_schema", "schema": schema}},
    }

    # Polled and collected on the id the submission returned; the sync
    # endpoint was never touched.
    assert record.retrieved == [record.batch_id]
    assert record.results_for == [record.batch_id]
    assert record.create_calls == []

    assert set(results) == {"c1", "c2"}
    for custom_id, expected_text in (
        ("c1", '{"entailed": true}'),
        ("c2", '{"entailed": false}'),
    ):
        completion = results[custom_id]
        assert isinstance(completion, llm.Completion)
        assert completion.text == expected_text
        assert completion.model_id == "claude-haiku-4-5-20251001"
        assert completion.stop_reason == "end_turn"
        assert completion.usage.batch is True
        assert completion.usage.batch_id == record.batch_id

    assert events[0][0] == "cost_estimate"
    assert events[1] == (
        "batch_submitted",
        {
            "phase": "P3",
            "role": "claim_judge",
            "batch_id": record.batch_id,
            "count": 2,
        },
    )


def test_batch_submission_uses_the_zero_retry_client(monkeypatch):
    """AC-20 (step-8 half): with no submit_sdk injected the SUBMISSION
    client comes from src.tour.batch_transport.batch_client() with zero
    retries — a hidden SDK retry on a submission could double-submit the
    batch, i.e. double-spend (batch_transport.py:39-53). Monkeypatched on
    the module (never a sys.modules 'anthropic' stub: submit_batch imports
    anthropic.types at call time)."""
    from src.tour import batch_transport

    stub, record = _batch_sdk_stub([_batch_succeeded("c1", text='{"entailed": true}')])
    factory_calls: list[tuple[tuple, dict]] = []

    def _recorder(*args, **kwargs):
        factory_calls.append((args, kwargs))
        return stub

    monkeypatch.setattr(batch_transport, "batch_client", _recorder)

    _events, sink = _sink_and_events()
    client = llm.AnthropicClient(sink, sdk=stub, poll_interval_s=0)
    client.estimate([], [])

    results = client.complete_batch(
        "claim_judge", [("c1", "p1")], None, phase="P3", max_tokens=64
    )

    assert len(factory_calls) == 1
    args, kwargs = factory_calls[0]
    assert args == ()
    assert kwargs.get("max_retries", 0) == 0
    assert len(record.created) == 1
    assert [request["custom_id"] for request in record.created[0]] == ["c1"]
    assert isinstance(results["c1"], llm.Completion)


def test_anthropic_errored_expired_or_missing_batch_units_are_surfaced():
    """AC-16 (AnthropicClient sibling): an errored unit keeps its provider
    error text, a unit the results stream never mentions becomes a
    'missing' BatchFailure (the result dict iterates the SUBMITTED
    prompts, not the results), and nothing is raised."""
    stub, record = _batch_sdk_stub(
        [
            _batch_succeeded("c1", text='{"entailed": true}'),
            _batch_errored("c2", error_type="invalid_request_error", message="too long"),
        ]
    )

    _events, sink = _sink_and_events()
    client = llm.AnthropicClient(sink, sdk=stub, submit_sdk=stub, poll_interval_s=0)
    client.estimate([], [])

    results = client.complete_batch(
        "claim_judge",
        [("c1", "p1"), ("c2", "p2"), ("c3", "p3")],
        None,
        phase="P3",
        max_tokens=64,
    )

    assert set(results) == {"c1", "c2", "c3"}
    assert isinstance(results["c1"], llm.Completion)
    assert results["c2"] == llm.BatchFailure(
        custom_id="c2",
        result_type="errored",
        error_message="invalid_request_error: too long",
        batch_id=record.batch_id,
    )
    assert results["c3"] == llm.BatchFailure(
        custom_id="c3",
        result_type="missing",
        error_message=None,
        batch_id=record.batch_id,
    )


def test_anthropic_batch_post_response_refusals_apply_per_unit():
    """AC-4/AC-12 (AnthropicClient batch sibling, close-consult step 9): the
    post-response checks also guard the Batch transport that P3/P5 (the
    judge phases) actually run on. A judge-role unit answered by the
    author's model — bare or dated — raises JudgeIsAuthor with the pinned
    code, and an author unit cut at max_tokens raises TruncatedCompletion
    carrying the partial text. Both fire AFTER collect_results (the SDK
    boundary is stubbed; batch_transport runs for real), so a stub with
    real int usage counters and non-empty text is what reaches them."""
    for author_id in ("claude-opus-5", "claude-opus-5-20260115"):
        stub, _record = _batch_sdk_stub(
            [_batch_succeeded("c1", text='{"entailed": true}', model=author_id)]
        )
        _events, sink = _sink_and_events()
        client = llm.AnthropicClient(sink, sdk=stub, submit_sdk=stub, poll_interval_s=0)
        client.estimate([], [])
        with pytest.raises(llm.JudgeIsAuthor, match="c1") as excinfo:
            client.complete_batch("claim_judge", [("c1", "q")], None, phase="P3", max_tokens=64)
        assert excinfo.value.code == "JUDGE_IS_AUTHOR"

    stub, _record = _batch_sdk_stub(
        [
            _batch_succeeded(
                "u1", text='{"claims": [', model="claude-opus-5", stop_reason="max_tokens"
            )
        ]
    )
    _events, sink = _sink_and_events()
    client = llm.AnthropicClient(sink, sdk=stub, submit_sdk=stub, poll_interval_s=0)
    client.estimate([], [])
    with pytest.raises(llm.TruncatedCompletion, match="u1") as truncated:
        client.complete_batch("author", [("u1", "p")], None, phase="P1", max_tokens=8)
    assert truncated.value.text == '{"claims": ['


def test_a_batch_phase_emits_a_heartbeat_on_every_poll():
    """Slice 9 (2026-09-12): between 'batch_submitted' and the phase's
    completion a live client was silent for minutes. Every poll now emits
    `batch_polling` with the phase, role, batch id, seconds elapsed, the
    API's processing_status and its request_counts, so the console, the
    job log and the chat monitor can tell a working job from a stalled
    one. The last heartbeat is the 'ended' poll."""
    import types

    statuses = iter(["in_progress", "ended"])
    stub, _record = _batch_sdk_stub(
        [_batch_succeeded("c1", text='{"entailed": true, "reason": "r", "kind": "state"}',
                          model="claude-haiku-4-5-20251001")]
    )
    real_batches = stub.messages.batches

    def retrieve(requested_id):
        return types.SimpleNamespace(
            id=requested_id,
            processing_status=next(statuses),
            request_counts=types.SimpleNamespace(
                processing=1, succeeded=0, errored=0, canceled=0, expired=0
            ),
        )

    real_batches.retrieve = retrieve
    events: list[tuple[str, dict]] = []
    client = llm.AnthropicClient(
        lambda kind, payload: events.append((kind, payload)),
        sdk=stub, submit_sdk=stub, poll_interval_s=0,
    )
    client.estimate([], [])

    client.complete_batch(
        "claim_judge", [("c1", "p")], None, phase="P3", max_tokens=400
    )

    beats = [payload for kind, payload in events if kind == "batch_polling"]
    assert [b["processing_status"] for b in beats] == ["in_progress", "ended"]
    assert all(
        b["phase"] == "P3" and b["role"] == "claim_judge" and b["batch_id"] == "msgbatch_stub_01"
        for b in beats
    )
    assert all(b["elapsed_s"] >= 0 for b in beats)
    assert beats[0]["request_counts"] == {
        "processing": 1, "succeeded": 0, "errored": 0, "canceled": 0, "expired": 0
    }
    kinds = [kind for kind, _p in events]
    assert kinds.index("batch_submitted") < kinds.index("batch_polling")
