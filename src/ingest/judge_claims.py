"""P3 judge claims: a second model decides whether each claim is entailed.

Docs/ingestion/rebuild-spec.md §3 (P3) and CONTEXT.md pin the shapes below.

`judge_claims(story, claims, unit, client)` judges the story's own claims
in one Batch API round under the `claim_judge` role (never the author's
model — llm's judge-independence gate refuses that at both construction
and response time) and returns a `JudgedClaim` per surviving claim: the
`ClaimDraft` it judged plus a `model.Verdict` bound by SHA-256 to exactly
the text and span it judged (`model.bind(text, span)` — the same binding
the slice-1 validator recomputes as VERDICT_UNBOUND) and stamped with the
model id the RESPONSE reported, never the configured one.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from typing import Any, NoReturn

from pydantic import BaseModel, ConfigDict

from src.ingest import llm, model, prompts
from src.ingest.decompose import ClaimDraft, UnitHeld
from src.ingest.gates import KIND_RESPONSE_VALUES, claim_gates, default_kind, span_in_unit
from src.ingest.group import Story
from src.ingest.unit import Unit

#: Output tokens requested for one P3 verdict — a boolean and a sentence.
P3_MAX_TOKENS: int = 400

#: Output tokens requested for the author's single restate of one claim.
P3_RESTATE_MAX_TOKENS: int = 1000

#: Output tokens requested for one unit's omission findings.
P3_OMISSIONS_MAX_TOKENS: int = 4000

#: Characters of `Unit.key` kept in a P3 custom_id: the key prefix plus
#: `-{claim_id}-jN` must fit the Batch API's 64-character limit.
_CUSTOM_ID_KEY_CAP = 48

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.S)


class JudgedClaim(BaseModel):
    """A claim that survived P3, with the verdict that let it through."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    draft: ClaimDraft
    verdict: model.Verdict


#: The P3 cost-estimate plan, priced PER CLAIM (estimate it over the
#: claims' texts): the first judge call, the author's restate, and the
#: re-judge. The last two rows are a CEILING — every claim is priced as if
#: refused once — until slice 9 measures live refusal rates.
P3_PLAN: tuple[llm.PhaseCall, ...] = (
    llm.PhaseCall(
        phase="P3",
        role="claim_judge",
        calls_per_unit=1,
        overhead_tokens=max(1, len(prompts.JUDGE_CLAIM_PROMPT) // 4),
        expected_output_tokens=P3_MAX_TOKENS,
    ),
    llm.PhaseCall(
        phase="P1",
        role="author",
        calls_per_unit=1,
        overhead_tokens=max(1, len(prompts.RESTATE_PROMPT) // 4),
        expected_output_tokens=P3_RESTATE_MAX_TOKENS,
    ),
    llm.PhaseCall(
        phase="P3",
        role="claim_judge",
        calls_per_unit=1,
        overhead_tokens=max(1, len(prompts.JUDGE_CLAIM_PROMPT) // 4),
        expected_output_tokens=P3_MAX_TOKENS,
    ),
)

#: The omission check's plan, priced PER UNIT (estimate it over unit texts).
OMISSIONS_PLAN: tuple[llm.PhaseCall, ...] = (
    llm.PhaseCall(
        phase="P3",
        role="claim_judge",
        calls_per_unit=1,
        overhead_tokens=max(1, len(prompts.OMISSIONS_PROMPT) // 4),
        expected_output_tokens=P3_OMISSIONS_MAX_TOKENS,
    ),
)


def judge_custom_id(unit: Unit, claim_id: str, attempt: int) -> str:
    """The Batch API custom_id for judging one claim on one attempt."""
    return f"{unit.key[:_CUSTOM_ID_KEY_CAP]}-{claim_id}-j{attempt}"


def restate_custom_id(unit: Unit, claim_id: str) -> str:
    """The Batch API custom_id for the author's single restate of one claim."""
    return f"{unit.key[:_CUSTOM_ID_KEY_CAP]}-{claim_id}-r1"


def omissions_custom_id(unit: Unit) -> str:
    """The Batch API custom_id for the unit's one omission check."""
    return f"{unit.key[:_CUSTOM_ID_KEY_CAP]}-om1"


def _extract_json_object(text: str) -> Any | None:
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


def parse_verdict(text: str) -> dict | None:
    """Turn one raw P3 answer into `{'entailed': bool, 'reason': str}`, or None."""
    parsed = _extract_json_object(text)
    if not isinstance(parsed, dict) or set(parsed.keys()) != {"entailed", "reason"}:
        return None
    if not isinstance(parsed["entailed"], bool) or not isinstance(parsed["reason"], str):
        return None
    return parsed


def parse_omissions(text: str) -> list[dict] | None:
    """Turn one raw omissions answer into its `[{fact, span}, ...]` list, or None.

    An empty list is a valid answer (nothing omitted); a single malformed
    item makes the whole answer unreadable.
    """
    parsed = _extract_json_object(text)
    if not isinstance(parsed, dict) or set(parsed.keys()) != {"omitted"}:
        return None
    omitted = parsed["omitted"]
    if not isinstance(omitted, list):
        return None
    for item in omitted:
        if not isinstance(item, dict) or set(item.keys()) != {"fact", "span"}:
            return None
        if not isinstance(item["fact"], str) or not isinstance(item["span"], str):
            return None
    return omitted


def parse_restated(text: str) -> dict | None:
    """Turn one raw restate answer into a `{text, kind, span}` item, or None."""
    parsed = _extract_json_object(text)
    if not isinstance(parsed, dict) or set(parsed.keys()) != {"text", "kind", "span"}:
        return None
    if not isinstance(parsed["text"], str) or not isinstance(parsed["span"], str):
        return None
    if parsed["kind"] not in KIND_RESPONSE_VALUES:
        return None
    return parsed


Emit = Callable[[str, dict], None]


def _hold(emit: Emit, unit: Unit, reason: str, cause: BaseException | None = None) -> NoReturn:
    """Emit `unit_held` and raise `UnitHeld` for phase P3."""
    emit("unit_held", {"unit_key": unit.key, "phase": "P3", "reason": reason})
    raise UnitHeld(unit.key, "P3", reason) from cause


def _batch(
    emit: Emit,
    unit: Unit,
    client: llm.ModelClient,
    *,
    role: str,
    prompts_: Sequence[tuple[str, str]],
    schema: dict,
    phase: str,
    max_tokens: int,
) -> dict[str, llm.Completion]:
    """One batch round with the same failure contract as decompose(): an
    `llm.EmptyCompletion`/`llm.TruncatedCompletion`, a raw (non-LlmError)
    `ValueError`, or a `BatchFailure` for any unit holds the unit; any other
    `llm.LlmError` (EstimateNotPrinted, MockScriptExhausted, JudgeIsAuthor,
    ...) is a programming error and propagates as itself."""
    try:
        results = client.complete_batch(
            role=role, prompts=prompts_, schema=schema, phase=phase, max_tokens=max_tokens
        )
    except (llm.EmptyCompletion, llm.TruncatedCompletion) as exc:
        _hold(emit, unit, f"transport: {exc}", exc)
    except llm.LlmError:
        raise
    except ValueError as exc:
        _hold(emit, unit, f"transport: {exc}", exc)

    completions: dict[str, llm.Completion] = {}
    for custom_id, _prompt in prompts_:
        answer = results[custom_id]
        if isinstance(answer, llm.BatchFailure):
            detail = f": {answer.error_message}" if answer.error_message else ""
            _hold(emit, unit, f"transport: batch unit {custom_id!r} {answer.result_type}{detail}")
        completions[custom_id] = answer
    return completions


def _judge_round(
    emit: Emit,
    drafts: Sequence[ClaimDraft],
    unit: Unit,
    client: llm.ModelClient,
    attempt: int,
) -> dict[str, dict]:
    """One P3 batch round over `drafts`; returns each claim id's parsed
    verdict (plus the response's model id). An unreadable verdict holds
    the unit: a judge that cannot answer the schema is a broken judge, not
    a refused claim."""
    completions = _batch(
        emit,
        unit,
        client,
        role="claim_judge",
        prompts_=[
            (
                judge_custom_id(unit, draft.claim_id, attempt),
                prompts.render_judge_claim(draft.text, draft.source.span, unit.text),
            )
            for draft in drafts
        ],
        schema=prompts.P3_VERDICT_SCHEMA,
        phase="P3",
        max_tokens=P3_MAX_TOKENS,
    )
    verdicts: dict[str, dict] = {}
    for draft in drafts:
        answer = completions[judge_custom_id(unit, draft.claim_id, attempt)]
        parsed = parse_verdict(answer.text)
        if parsed is None:
            _hold(
                emit,
                unit,
                f"schema: the judge's answer for claim {draft.claim_id} was not valid "
                "JSON matching the P3 verdict schema",
            )
        verdicts[draft.claim_id] = {**parsed, "model_id": answer.model_id}
    return verdicts


def _restate_round(
    emit: Emit,
    refused: Sequence[tuple[ClaimDraft, str]],
    unit: Unit,
    client: llm.ModelClient,
) -> list[ClaimDraft]:
    """The single author re-ask for every refused claim, in one P1 batch.
    An unreadable restate holds the unit, same as an unreadable P1 answer
    on its second attempt would."""
    completions = _batch(
        emit,
        unit,
        client,
        role="author",
        prompts_=[
            (
                restate_custom_id(unit, draft.claim_id),
                prompts.render_restate(draft.text, draft.source.span, reason, unit.text),
            )
            for draft, reason in refused
        ],
        schema=prompts.P3_RESTATE_SCHEMA,
        phase="P1",
        max_tokens=P3_RESTATE_MAX_TOKENS,
    )
    restated: list[ClaimDraft] = []
    for draft, _reason in refused:
        answer = completions[restate_custom_id(unit, draft.claim_id)]
        item = parse_restated(answer.text)
        if item is None:
            _hold(
                emit,
                unit,
                f"schema: the author's restate of claim {draft.claim_id} was not valid "
                "JSON matching the restate schema",
            )
        restated.append(
            ClaimDraft(
                claim_id=draft.claim_id,
                text=item["text"],
                kind=default_kind(item["kind"]),
                source=unit.source(item["span"]),
            )
        )
    return restated


def _judged(draft: ClaimDraft, verdict: dict) -> JudgedClaim:
    return JudgedClaim(
        draft=draft,
        verdict=model.Verdict(
            judge_model=verdict["model_id"],
            entailed=verdict["entailed"],
            bound_to=model.bind(draft.text, draft.source.span),
        ),
    )


def judge_claims(
    story: Story,
    claims: Sequence[ClaimDraft],
    unit: Unit,
    client: llm.ModelClient,
    events: llm.EventSink | None = None,
) -> list[JudgedClaim]:
    """Judge the story's claims; see the module docstring.

    Round 1 judges every claim of the story in one P3 batch. Each refused
    claim emits `claim_refused` and is restated ONCE by the author (one P1
    batch, the judge's reason quoted back via `prompts.render_restate`);
    each restated claim must pass `gates.claim_gates` (a failing one is
    dropped with the gate's reasons and never judged again); the survivors
    are judged in a second P3 batch and the round-two verdict is what comes
    out, bound to the restated text. A
    claim refused twice is dropped and logged (`claim_dropped`, the
    judge's second reason); a third call never happens. Surviving claims
    keep their ids — stories already reference them, so P3 never
    renumbers.
    """

    def emit(kind: str, payload: dict) -> None:
        if events is not None:
            events(kind, payload)

    drafts = [draft for draft in claims if draft.claim_id in story.claim_ids]
    by_id = {draft.claim_id: draft for draft in drafts}
    verdicts = _judge_round(emit, drafts, unit, client, attempt=1)

    refused = [
        (by_id[claim_id], verdict["reason"])
        for claim_id, verdict in verdicts.items()
        if not verdict["entailed"]
    ]
    for draft, reason in refused:
        emit(
            "claim_refused",
            {"unit_key": unit.key, "claim_id": draft.claim_id, "attempt": 1, "reason": reason},
        )
    dropped: set[str] = set()
    if refused:
        restated = []
        for draft in _restate_round(emit, refused, unit, client):
            by_id[draft.claim_id] = draft
            gate_reasons = claim_gates(draft.text, draft.source.span, unit.text)
            if gate_reasons:
                emit(
                    "claim_dropped",
                    {
                        "unit_key": unit.key,
                        "claim_id": draft.claim_id,
                        "claim_text": draft.text,
                        "reason": "; ".join(gate_reasons),
                    },
                )
                dropped.add(draft.claim_id)
                continue
            restated.append(draft)
        if restated:
            verdicts.update(_judge_round(emit, restated, unit, client, attempt=2))
        for draft in restated:
            verdict = verdicts[draft.claim_id]
            if verdict["entailed"]:
                continue
            emit(
                "claim_refused",
                {
                    "unit_key": unit.key,
                    "claim_id": draft.claim_id,
                    "attempt": 2,
                    "reason": verdict["reason"],
                },
            )
            emit(
                "claim_dropped",
                {
                    "unit_key": unit.key,
                    "claim_id": draft.claim_id,
                    "claim_text": draft.text,
                    "reason": verdict["reason"],
                },
            )
            dropped.add(draft.claim_id)

    return [
        _judged(by_id[draft.claim_id], verdicts[draft.claim_id])
        for draft in drafts
        if draft.claim_id not in dropped
    ]


def omissions(
    unit: Unit,
    claims: Sequence[ClaimDraft],
    client: llm.ModelClient,
    events: llm.EventSink | None = None,
) -> list[str]:
    """Facts the unit states that no claim carries, per the judge.

    One P3 batch call under `claim_judge` over the unit and its claims'
    texts. Every finding must cite a span verbatim in the unit
    (`gates.span_in_unit`): an ungrounded finding is discarded and logged
    as `omission_ungrounded`, never returned — the judge may not invent an
    omission any more than the author may invent a claim. Grounded facts
    are returned and emitted once as `omissions_found` with their spans
    (only when there are any). Transport failures and an unreadable answer hold the unit,
    exactly as `judge_claims` does. Re-asking P1 for the unit on a finding
    is the job runner's decision, not this function's.
    """

    def emit(kind: str, payload: dict) -> None:
        if events is not None:
            events(kind, payload)

    custom_id = omissions_custom_id(unit)
    completions = _batch(
        emit,
        unit,
        client,
        role="claim_judge",
        prompts_=[(custom_id, prompts.render_omissions([c.text for c in claims], unit.text))],
        schema=prompts.P3_OMISSIONS_SCHEMA,
        phase="P3",
        max_tokens=P3_OMISSIONS_MAX_TOKENS,
    )
    findings = parse_omissions(completions[custom_id].text)
    if findings is None:
        _hold(
            emit,
            unit,
            "schema: the judge's omission answer was not valid JSON matching the "
            "P3 omissions schema",
        )

    facts: list[str] = []
    spans: list[str] = []
    for finding in findings:
        reason = span_in_unit(finding["span"], unit.text)
        if reason is not None:
            emit(
                "omission_ungrounded",
                {"unit_key": unit.key, "fact": finding["fact"], "reason": reason},
            )
            continue
        facts.append(finding["fact"])
        spans.append(finding["span"])
    if facts:
        emit("omissions_found", {"unit_key": unit.key, "facts": list(facts), "spans": spans})
    return facts
