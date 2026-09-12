"""P5 judge narration: a second model decides whether each sentence of a
narration is entailed by the story's claims.

Docs/ingestion/rebuild-spec.md §3 (P5) and CONTEXT.md ("Narration") pin
the shapes below.

`judge_narration(story, draft, claims, client)` splits the P4 draft into
sentences (`sentences`), judges every one in one Batch API round under
the `narration_judge` role (never the author's model — llm's
judge-independence gate refuses that at construction and response time),
each prompt carrying that one sentence and the claim TEXTS only, and
returns a `JudgedNarration`: the `model.Narration` (text, the draft's
claims_hash and author model, a `model.NarrationVerdict` counting the
entailed sentences and bound by SHA-256 to exactly the text judged —
`model.bind(text)`, the binding the slice-1 validator recomputes as
VERDICT_UNBOUND — stamped with the model id the RESPONSE reported), the
draft's duration, and the beat's `model.Review`.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from src.ingest import llm, model, prompts
from src.ingest.group import Story
from src.ingest.judge_claims import JudgedClaim, _extract_json_object
from src.ingest.narrate import (
    P4_MAX_TOKENS,
    Emit,
    NarrationDraft,
    ask_author,
    claim_spans,
    draft_from,
    emitter,
    hold,
    narration_gates,
    resolved_claim_texts,
)

#: Output tokens requested for one P5 verdict — a boolean and a sentence.
P5_MAX_TOKENS: int = 400

#: Characters of the claims hash that identify a narration inside a Batch
#: API custom_id (`n{hash}-s{index}-j{attempt}` stays far under 64).
_CUSTOM_ID_HASH_CHARS = 16

#: Where one spoken sentence ends: a terminal mark, optionally a closing
#: quote or bracket, whitespace, then an opener. Two fixed-width
#: lookbehinds because Python's `re` allows no variable-width one.
_SENTENCE_END_RE = re.compile(
    r"(?:(?<=[.!?])|(?<=[.!?][\"\u201d\u2019)]))\s+(?=[A-Z0-9\"\u201c\u2018(])"
)

#: Abbreviations whose trailing period ends no sentence.
_ABBREVIATIONS = frozenset(
    {"mr", "mrs", "ms", "dr", "st", "ave", "no", "jr", "sr", "prof", "mt", "ft"}
)


class JudgedNarration(BaseModel):
    """What P5 produced: the record's narration, its duration, and whether
    the beat is held for review."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    narration: model.Narration
    duration_sec: int
    review: model.Review


#: The P5 cost-estimate plan, priced PER SENTENCE (estimate it over the
#: narration's sentences): the first judge call, the author's revise, and
#: the re-judge. The last two rows are a CEILING — every sentence is
#: priced as if refused once, and the revise (one per beat, not per
#: sentence) is charged per sentence — until slice 9 measures live
#: refusal rates.
P5_PLAN: tuple[llm.PhaseCall, ...] = (
    llm.PhaseCall(
        phase="P5",
        role="narration_judge",
        calls_per_unit=1,
        overhead_tokens=max(1, len(prompts.JUDGE_SENTENCE_PROMPT) // 4),
        expected_output_tokens=P5_MAX_TOKENS,
    ),
    llm.PhaseCall(
        phase="P4",
        role="author",
        calls_per_unit=1,
        overhead_tokens=max(1, len(prompts.NARRATE_REVISE_PROMPT) // 4),
        expected_output_tokens=P4_MAX_TOKENS,
    ),
    llm.PhaseCall(
        phase="P5",
        role="narration_judge",
        calls_per_unit=1,
        overhead_tokens=max(1, len(prompts.JUDGE_SENTENCE_PROMPT) // 4),
        expected_output_tokens=P5_MAX_TOKENS,
    ),
)


def parse_sentence_verdict(text: str) -> dict | None:
    """Turn one raw P5 answer into `{'entailed': bool, 'reason': str}`, or
    None. Two keys only: P5_VERDICT_SCHEMA carries no kind — a sentence has
    no temporal kind of its own; the P3 verdict (judge_claims.parse_verdict)
    does, since slice 9's KIND question."""
    parsed = _extract_json_object(text)
    if not isinstance(parsed, dict) or set(parsed.keys()) != {"entailed", "reason"}:
        return None
    if not isinstance(parsed["entailed"], bool) or not isinstance(parsed["reason"], str):
        return None
    return parsed


def _ends_with_abbreviation(piece: str) -> bool:
    last = piece.split()[-1] if piece.split() else ""
    return last.endswith(".") and last[:-1].lower() in _ABBREVIATIONS


def sentences(text: str) -> list[str]:
    """The narration's sentences, the units P5 judges one by one.

    Splits at `_SENTENCE_END_RE`, re-gluing a piece that ends on a known
    abbreviation ("St. Patrick's"). Empty or whitespace-only text yields [].
    """
    pieces = [piece.strip() for piece in _SENTENCE_END_RE.split(text.strip()) if piece.strip()]
    out: list[str] = []
    for piece in pieces:
        if out and _ends_with_abbreviation(out[-1]):
            out[-1] = f"{out[-1]} {piece}"
        else:
            out.append(piece)
    return out


def sentence_custom_id(draft: NarrationDraft, index: int, attempt: int) -> str:
    """The Batch API custom_id for judging one sentence on one attempt."""
    return f"n{draft.claims_hash[:_CUSTOM_ID_HASH_CHARS]}-s{index}-j{attempt}"


def _judge_round(
    emit: Emit,
    story: Story,
    draft: NarrationDraft,
    texts: Sequence[str],
    client: llm.ModelClient,
    attempt: int,
) -> list[dict]:
    """One P5 batch round over the draft's sentences; returns each
    sentence's parsed verdict plus the response's model id, in order.

    The same failure contract as judge_claims: an
    `llm.EmptyCompletion`/`llm.TruncatedCompletion`, a raw (non-LlmError)
    `ValueError`, a `BatchFailure` for any sentence, or an unreadable
    verdict holds the beat (a judge that cannot answer the schema is a
    broken judge, not a refused sentence); any other `llm.LlmError` is a
    programming error and propagates as itself.
    """
    split = sentences(draft.text)
    ids = [sentence_custom_id(draft, index, attempt) for index in range(len(split))]
    try:
        completions = client.complete_batch(
            role="narration_judge",
            prompts=[
                (custom_id, prompts.render_judge_sentence(sentence, texts))
                for custom_id, sentence in zip(ids, split, strict=True)
            ],
            schema=prompts.P5_VERDICT_SCHEMA,
            phase="P5",
            max_tokens=P5_MAX_TOKENS,
        )
    except (llm.EmptyCompletion, llm.TruncatedCompletion) as exc:
        hold(emit, story, "P5", f"transport: {exc}", exc)
    except llm.LlmError:
        raise
    except ValueError as exc:
        hold(emit, story, "P5", f"transport: {exc}", exc)

    verdicts: list[dict] = []
    for custom_id, sentence in zip(ids, split, strict=True):
        answer = completions[custom_id]
        if isinstance(answer, llm.BatchFailure):
            detail = f": {answer.error_message}" if answer.error_message else ""
            hold(
                emit,
                story,
                "P5",
                f"transport: batch unit {custom_id!r} {answer.result_type}{detail}",
            )
        parsed = parse_sentence_verdict(answer.text)
        if parsed is None:
            hold(
                emit,
                story,
                "P5",
                f"schema: the judge's answer for sentence {sentence!r} was not valid "
                "JSON matching the P5 verdict schema",
            )
        verdicts.append({**parsed, "sentence": sentence, "model_id": answer.model_id})
    return verdicts


def _refusals(verdicts: Sequence[dict]) -> list[tuple[str, str]]:
    return [(v["sentence"], v["reason"]) for v in verdicts if not v["entailed"]]


def _judged(draft: NarrationDraft, verdicts: Sequence[dict], flags: list[str]) -> JudgedNarration:
    entailed = sum(1 for verdict in verdicts if verdict["entailed"])
    narration = model.Narration(
        text=draft.text,
        claims_hash=draft.claims_hash,
        author_model=draft.author_model,
        verdict=model.NarrationVerdict(
            judge_model=verdicts[0]["model_id"],
            sentences_entailed=entailed,
            sentences_total=len(verdicts),
            bound_to=model.bind(draft.text),
        ),
        flags=list(flags),
    )
    review = model.Review(held=bool(flags), reason="; ".join(flags) if flags else None)
    return JudgedNarration(narration=narration, duration_sec=draft.duration_sec, review=review)


def _emit_refusals(emit: Emit, story: Story, attempt: int, refusals: Sequence[tuple[str, str]]):
    for sentence, reason in refusals:
        emit(
            "sentence_refused",
            {"story_slug": story.story_slug, "attempt": attempt, "sentence": sentence,
             "reason": reason},
        )


def judge_narration(
    story: Story,
    draft: NarrationDraft,
    claims: Sequence[JudgedClaim],
    client: llm.ModelClient,
    events: llm.EventSink | None = None,
    publishers: Sequence[str] = (),
) -> JudgedNarration:
    """Judge the draft sentence by sentence; see the module docstring.

    Round 1 judges every sentence in one P5 batch. Each refused sentence
    emits `sentence_refused`; if there are any, the AUTHOR is asked ONCE
    (a sync P4 call, `prompts.render_narrate_revise`, every refused
    sentence and reason quoted back) to rewrite the narration from the
    same claims; the rewrite must pass the P4 code gate
    (`narrate.narration_gates`), and every sentence of it is judged in a
    second P5 batch. The round-two verdict is what comes out, bound to
    the new text.

    A sentence still refused in round two sets one `not_entailed` flag
    per refused sentence (the judge's second reason quoted), `review.held`
    with those flags as its reason, and emits `beat_held` for phase P5 —
    but RETURNS the judged narration rather than raising: unlike a P4
    hold, there is a judged record for the reviewer to see. A rewrite that
    fails the code gate is never judged: the beat is held the same way
    with a `gate` flag per reason beside the round-one `not_entailed`
    flags, and the narration that comes out is the ROUND-ONE text with
    its round-one verdict — the only text a judge actually saw. The claims
    are never touched: P5 has no claim call to make, and a narration that
    cannot be entailed by its claims is a narration problem.
    """
    emit = emitter(events)
    texts = resolved_claim_texts(claims)

    verdicts = _judge_round(emit, story, draft, texts, client, attempt=1)
    refusals = _refusals(verdicts)
    if not refusals:
        return _judged(draft, verdicts, [])
    _emit_refusals(emit, story, 1, refusals)

    text, author_model = ask_author(
        emit,
        story,
        client,
        prompts.render_narrate_revise(story.place, story.title, texts, draft.text, refusals),
        phase="P5",
    )
    rewrite = draft_from(text, claims, author_model)
    gate_reasons = narration_gates(rewrite.text, claim_spans(claims), publishers)
    if gate_reasons:
        emit(
            "narration_refused",
            {"story_slug": story.story_slug, "attempt": 2, "reasons": list(gate_reasons)},
        )
        flags = [f"not_entailed: {sentence!r}: {reason}" for sentence, reason in refusals]
        flags.extend(f"gate: {reason}" for reason in gate_reasons)
        judged = _judged(draft, verdicts, flags)
        emit(
            "beat_held",
            {"story_slug": story.story_slug, "phase": "P5", "reason": judged.review.reason},
        )
        return judged
    verdicts = _judge_round(emit, story, rewrite, texts, client, attempt=2)
    refusals = _refusals(verdicts)
    if not refusals:
        return _judged(rewrite, verdicts, [])
    _emit_refusals(emit, story, 2, refusals)
    flags = [f"not_entailed: {sentence!r}: {reason}" for sentence, reason in refusals]
    judged = _judged(rewrite, verdicts, flags)
    emit(
        "beat_held",
        {"story_slug": story.story_slug, "phase": "P5", "reason": judged.review.reason},
    )
    return judged
