"""P1 decompose: turn one Unit's passage into claim drafts.

Docs/ingestion/rebuild-spec.md and CONTEXT.md pin the shapes below.

Step 5 landed the types decompose() will run on: `ClaimDraft`
(a claim with its kind already resolved to `event`/`state`/`belief` — the
LLM's own `ambiguous` escape hatch is resolved by `gates.default_kind`
before a ClaimDraft is ever built — bound to a real `model.Source` stamped
from the unit it came from), `UnitHeld` (raised when a unit could not
produce a usable answer after the single re-ask; deliberately a plain
`Exception`, NOT a `ValueError`, so a caller catching `llm.LlmError`
cannot accidentally swallow it — carries `.unit_key`/`.phase`/`.reason` so
an operator sees which unit stalled, in which phase, and why), `P1_PLAN`
(the two-row cost-estimate plan for a P1 call: first-ask priced against
the real `DECOMPOSE_PROMPT` length, re-ask against the real `REDO_PROMPT`
length — never a made-up overhead number), and `parse_claims` (turn one
raw P1 answer into its list of claim dicts, or `None` for anything
unreadable).

`parse_claims` tolerates prose wrapped around the JSON object (a model
that answers "Sure, here you go:\\n{...}\\nLet me know!" is still read),
but is otherwise strict, matching `prompts.P1_RESPONSE_SCHEMA` exactly:
the top-level payload must be a dict whose only key is `claims`, `claims`
must be a non-empty list, and every item must be a dict with exactly the
keys `text`/`kind`/`span` (both `text` and `span` strings, `kind` one of
`gates.KIND_RESPONSE_VALUES`). A single invalid item makes the WHOLE
answer unreadable — never a partial list of only the good claims, because
a caller that silently dropped a bad item would be inventing which claims
survived rather than re-asking the model for a clean answer.

`decompose()` itself (the refusal loop, the transport-failure handling,
the actual ClaimDraft construction) landed in steps 6-8, below.

Slice 7 added `decompose(..., omitted=)`: the job runner's one P1 re-ask
for a unit whose P3 omission check found facts no claim carries. With
`omitted` given, the only ask is the re-ask (`prompts.render_redo` under
`unit.custom_id(2)`, each fact quoted back as a problem), graded with
attempt-2 semantics, so the unit's single re-ask is spent there and a
third ask never happens.

Step 6 added `decompose()`'s happy path: one `complete_batch()`
call of exactly one prompt under `unit.custom_id(1)`, `parse_claims()` over
the answer, `gates.default_kind` resolving each item's `kind`, and a
`ClaimDraft` per item bound to a `model.Source` built via `unit.source()`,
claim ids assigned `c01`, `c02`, ... in answer order. `decompose()` never
calls `client.estimate()` itself — arming the client's estimate gate is the
caller's job, so an unarmed client's `complete_batch()` raises
`llm.EstimateNotPrinted` here, uncaught, and any other transport error
(`MockScriptExhausted` included) propagates untouched.

Step 6 also landed the schema-level half of the refusal loop: an
unreadable answer (`parse_claims` returns None) on attempt 1 emits
`claims_refused` with a `'schema:'` reason and re-asks once, under
`unit.custom_id(2)`, quoting that reason back via `prompts.render_redo`; a
still-unreadable attempt 2 emits `unit_held` and raises `UnitHeld`.

Step 7 added the gate-based half, on the same one-re-ask
budget (decisions.refusal_rules): every parsed claim is run through
`gates.claim_gates` (span, leak, self_contained — lift is a narration gate since
2026-09-13). A claim failing
any gate on attempt 1 refuses the whole answer — `claims_refused` carries
one reason per failing gate per failing claim, each still starting with
its gate's own code token and naming the claim it came from, and all of
them are quoted back in the single re-ask. A claim still failing on
attempt 2 is DROPPED and logged as `claim_dropped`, never moved,
re-worded or re-asked: the passing claims are returned, renumbered
contiguously from `c01`, and a third call never happens.

Step 8 added the transport-failure handling
(decisions.refusal_rules): a `BatchFailure` result for the unit's own
custom_id (a failed batch unit surfaced as a value, never raised), or an
`llm.EmptyCompletion`/`llm.TruncatedCompletion`/non-`llm.LlmError`
`ValueError` raised by the transport, holds the unit immediately —
`unit_held` then `UnitHeld` — with NO second call, regardless of which
attempt it happened on. A programming error (`llm.EstimateNotPrinted`,
`llm.MockScriptExhausted`, or any other `llm.LlmError`) still propagates
as itself, uncaught.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from src.ingest import llm, model, prompts
from src.ingest.gates import KIND_RESPONSE_VALUES, claim_gates, default_kind, ground_span
from src.ingest.unit import Unit

#: Output tokens requested for a P1 call — both the first ask and the
#: re-ask use the same cap. Sized for THINKING: the client sends no
#: `thinking` parameter and claude-opus-5 thinks by default, so the
#: budget is spent on thinking first and the answer gets the rest. Slice
#: 9's first paid job (2026-09-12) stopped at the previous 8,000 having
#: written 4,013 tokens of text (39 claims, 22% of an 8.8k-token chunk);
#: a whole chunk ~ 177 claims ~ 18k text tokens plus thinking at the
#: measured 1.0x ~ 36k (4,013 text + 3,987 thinking in the capped call,
#: `make ingest-batch ARGS=--text-tokens`). tests/test_ingest_output_caps.py
#: holds the floor; P1_EXPECTED_OUTPUT_TOKENS is what the estimate prices.
P1_MAX_TOKENS: int = 64_000

#: What the estimate prices per P1 call: MEASURED on the first real job
#: (2026-09-12, the whole Upper East Side chunk: 5,846 text + 3,110
#: thinking = 8,956 output tokens for 82 claims), not the cap.
P1_EXPECTED_OUTPUT_TOKENS: int = 9_000

#: The exact keys `prompts.P1_RESPONSE_SCHEMA` allows on one claim item.
_REQUIRED_ITEM_KEYS: frozenset[str] = frozenset({"text", "kind", "span"})

#: A loose "outermost JSON object" match used only to strip prose a model
#: wrapped around its answer; the real validation is `_valid_claim_item`
#: and the key checks in `parse_claims`, not this regex.
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.S)


class ClaimDraft(BaseModel):
    """One claim as `decompose()` returns it.

    `kind` only ever resolves to `event`/`state`/`belief` — never the raw
    `ambiguous` the LLM may answer with, since that is mapped to `state`
    by `gates.default_kind` before a ClaimDraft is built. `source` is a
    real `model.Source`, stamped from the unit the claim came from via
    `Unit.source()`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    claim_id: str
    text: str
    kind: Literal["event", "state", "belief"]
    source: model.Source


class UnitHeld(Exception):  # noqa: N818 — pinned name is the spec/test contract
    """A unit could not produce a usable answer after the single re-ask.

    Deliberately a plain `Exception`, not a `ValueError` (and so not an
    `llm.LlmError`, which subclasses `ValueError`) — a caller that catches
    `llm.LlmError` to handle a transport failure must not accidentally
    swallow a held unit too; the two are different kinds of stop.
    """

    def __init__(self, unit_key: str, phase: str, reason: str) -> None:
        super().__init__(f"unit {unit_key!r} held in phase {phase!r}: {reason}")
        self.unit_key = unit_key
        self.phase = phase
        self.reason = reason


#: The P1 cost-estimate plan: one row per attempt (first ask, re-ask),
#: each priced against that attempt's own real prompt length rather than a
#: shared guess — the first ask never carries the re-ask's `{problems}`
#: quoting, so its overhead is genuinely smaller.
P1_PLAN: tuple[llm.PhaseCall, ...] = (
    llm.PhaseCall(
        phase="P1",
        role="author",
        calls_per_unit=1,
        overhead_tokens=max(1, len(prompts.DECOMPOSE_PROMPT) // 4),
        expected_output_tokens=P1_EXPECTED_OUTPUT_TOKENS,
    ),
    llm.PhaseCall(
        phase="P1",
        role="author",
        calls_per_unit=1,
        overhead_tokens=max(1, len(prompts.REDO_PROMPT) // 4),
        expected_output_tokens=P1_EXPECTED_OUTPUT_TOKENS,
    ),
)


def _extract_json_object(text: str) -> Any | None:
    """Parse `text` as JSON, tolerating prose wrapped around one object.

    Tries the whole text first; if that fails, falls back to the
    outermost `{...}` span. Returns None (never raises) when nothing
    parses.
    """
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    match = _JSON_OBJECT_RE.search(text)
    if match is None:
        return None
    try:
        return json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return None


def _valid_claim_item(item: Any) -> bool:
    """One claim item matches `prompts.P1_RESPONSE_SCHEMA` exactly."""
    if not isinstance(item, dict):
        return False
    if set(item.keys()) != _REQUIRED_ITEM_KEYS:
        return False
    if not isinstance(item["text"], str) or not isinstance(item["span"], str):
        return False
    return item["kind"] in KIND_RESPONSE_VALUES


def parse_claims(text: str) -> list[dict] | None:
    """Turn one raw P1 answer into its list of claim dicts, or None.

    None for: unparseable text, a top-level payload that is not a dict
    with exactly the key `claims`, a `claims` value that is not a
    non-empty list, or any item that fails `_valid_claim_item` — a single
    bad item refuses the WHOLE answer, never a partial list of only the
    good claims.
    """
    parsed = _extract_json_object(text)
    if not isinstance(parsed, dict):
        return None
    if set(parsed.keys()) != {"claims"}:
        return None
    claims = parsed["claims"]
    if not isinstance(claims, list) or not claims:
        return None
    if not all(_valid_claim_item(item) for item in claims):
        return None
    return claims


def _drafts(unit: Unit, items: list[dict]) -> list[ClaimDraft]:
    """Build one ClaimDraft per item, numbered `c01`, `c02`, ... in order.

    Numbering is applied AFTER any attempt-2 drop, so the ids a caller sees
    are always contiguous — a gap would read as a claim that went missing
    between P1 and P2 rather than one the gates refused on purpose.
    """
    return [
        ClaimDraft(
            claim_id=f"c{index:02d}",
            text=item["text"],
            kind=default_kind(item["kind"]),
            source=unit.source(item["span"]),
        )
        for index, item in enumerate(items, start=1)
    ]


def decompose(
    unit: Unit,
    client: llm.ModelClient,
    *,
    events: llm.EventSink | None = None,
    omitted: Sequence[str] = (),
) -> list[ClaimDraft]:
    """Turn one Unit's passage into a list of ClaimDraft via P1.

    `omitted` is the runner's one P3 omission re-ask (spec §3): the facts
    the judge found no claim carries. When given, the ONLY ask is the
    re-ask — `prompts.render_redo` under `unit.custom_id(2)` with each
    fact quoted back as a problem — and it is graded with attempt-2
    semantics (a still-failing claim is dropped, never asked again), so
    the unit's one P1 re-ask is spent here and a third ask never happens.

    One `client.complete_batch()` call of exactly one prompt under
    `unit.custom_id(1)`; a clean answer (`parse_claims` succeeds) is
    returned in one call, claim ids assigned `c01`, `c02`, ... in answer
    order, each item's `kind` resolved by `gates.default_kind` and its
    `source` built via `unit.source(item['span'])`.

    An unreadable answer (`parse_claims` returns None) emits
    `claims_refused` with a reason starting `'schema:'` and re-asks once
    under `unit.custom_id(2)`, via `prompts.render_redo` quoting that
    reason back; a still-unreadable second answer emits `unit_held` and
    raises `UnitHeld`.

    A readable answer is graded claim by claim with `gates.claim_gates`.
    Any failing claim on attempt 1 refuses the whole answer: every failing
    gate's reason (each still starting with its own code token, and naming
    the claim it came from) goes into one `claims_refused` event and into
    the single re-ask. On attempt 2 the still-failing claims are dropped
    and logged as `claim_dropped` and the rest are returned, renumbered
    contiguously from `c01` — never moved, never re-asked a third time.

    A transport failure — a `BatchFailure` result for the unit's own
    custom_id, or an `llm.EmptyCompletion`/`llm.TruncatedCompletion`/raw
    (non-`llm.LlmError`) `ValueError` raised by the call — emits
    `unit_held` and raises `UnitHeld` immediately, with NO second call,
    on either attempt.

    Never calls `client.estimate()` — an unarmed
    client's `complete_batch()` raises `llm.EstimateNotPrinted` here,
    uncaught, and any other programming error (including
    `llm.MockScriptExhausted`) propagates as itself.
    """

    def emit(kind: str, payload: dict) -> None:
        if events is not None:
            events(kind, payload)

    reasons: list[str] | None = (
        [f"omission: the passage also states {fact!r} and no claim carries it" for fact in omitted]
        or None
    )
    for attempt in (2,) if reasons else (1, 2):
        prompt = (
            prompts.render_decompose(unit.text)
            if attempt == 1
            else prompts.render_redo(unit.text, reasons)
        )
        custom_id = unit.custom_id(attempt)
        try:
            batch_result = client.complete_batch(
                role="author",
                prompts=[(custom_id, prompt)],
                schema=prompts.P1_RESPONSE_SCHEMA,
                phase="P1",
                max_tokens=P1_MAX_TOKENS,
            )
        except (llm.EmptyCompletion, llm.TruncatedCompletion) as exc:
            reason = f"transport: {exc}"
            emit("unit_held", {"unit_key": unit.key, "phase": "P1", "reason": reason})
            raise UnitHeld(unit.key, "P1", reason) from exc
        except llm.LlmError:
            # A programming error (EstimateNotPrinted, MockScriptExhausted,
            # JudgeIsAuthor, ...) is not a transport failure — propagate it
            # untouched, same as decompose() always has.
            raise
        except ValueError as exc:
            # A raw ValueError (not an llm.LlmError) from a duck-typed
            # transport — e.g. the src/tour empty-succeeded-unit path — is
            # still a transport failure, not a programming error.
            reason = f"transport: {exc}"
            emit("unit_held", {"unit_key": unit.key, "phase": "P1", "reason": reason})
            raise UnitHeld(unit.key, "P1", reason) from exc

        answer = batch_result[custom_id]
        if isinstance(answer, llm.BatchFailure):
            detail = f": {answer.error_message}" if answer.error_message else ""
            reason = f"transport: batch unit {custom_id!r} {answer.result_type}{detail}"
            emit("unit_held", {"unit_key": unit.key, "phase": "P1", "reason": reason})
            raise UnitHeld(unit.key, "P1", reason)

        items = parse_claims(answer.text)

        if items is None:
            reason = "schema: the answer was not valid JSON matching the P1 claims schema"
            emit(
                "claims_refused",
                {"unit_key": unit.key, "attempt": attempt, "reasons": [reason]},
            )
            if attempt == 2:
                emit("unit_held", {"unit_key": unit.key, "phase": "P1", "reason": reason})
                raise UnitHeld(unit.key, "P1", reason)
            reasons = [reason]
            continue

        # A citation that only straightened the passage's typography is stored
        # as the passage's own text before grading, so it never spends the
        # re-ask or drops a claim (gates.ground_span).
        items = [{**item, "span": ground_span(item["span"], unit.text)} for item in items]
        graded = [(item, claim_gates(item["text"], item["span"], unit.text)) for item in items]
        failing = [(item, item_reasons) for item, item_reasons in graded if item_reasons]

        if failing and attempt == 1:
            reasons = [
                f"{gate_reason} (in the claim {item['text']!r})"
                for item, item_reasons in failing
                for gate_reason in item_reasons
            ]
            emit("claims_refused", {"unit_key": unit.key, "attempt": 1, "reasons": reasons})
            continue

        for item, item_reasons in failing:
            emit(
                "claim_dropped",
                {
                    "unit_key": unit.key,
                    "claim_text": item["text"],
                    "reason": "; ".join(item_reasons),
                },
            )
        return _drafts(unit, [item for item, item_reasons in graded if not item_reasons])

    raise AssertionError("unreachable: every attempt returns, continues, or raises")
