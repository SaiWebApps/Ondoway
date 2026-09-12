"""P6 merge: a new story meets the beats the corpus already holds at its place.

Docs/ingestion/rebuild-spec.md §3 (P6), §1 D7/D8/D9 and CONTEXT.md ("Stated
value", "Resolved value", "Contested claim", "Event/State/Belief claim",
"Supersedes") pin the rules below.

`merge(new_story, claims, existing, client)` asks the merge judge ONCE, sync,
under the `merge_judge` role (P6 is not one of `llm.BATCH_PHASES`; the role's
model may never be the author's — llm's JudgeIsAuthor gate refuses that at
construction and per response, and this module does not re-implement it),
with the new story's claim TEXTS and every candidate beat's claims — never a
span. Independently of the judge, `signature_hint` computes the deterministic
answer from `src.tour.claim_dedup`'s claim signatures (the same "same fact"
test the tour engine's dedup uses, imported, never copied). The two are then
compared:

- Judge and hint agree on new-vs-matched, on which beat, and on which
  existing claim each new claim matches → the outcome applies.
- They disagree → the new story is HELD: `MergeOutcome.held`, a review-queue
  item (D13), `beat_held` emitted for phase P6, nothing applied. The hint is
  never shown to the judge and the judge is never re-asked toward it — that
  would make the hint the judge.

Whether a matched pair is `same` or `conflict` is the judge's call (the hint
cannot read values). What a conflict BECOMES is the code's, by claim kind
(D9), never the judge's:

- event vs event → both sides `contested`: one claim, the second source
  appended, every source carrying the value it states. A newer source is
  never right by default.
- event or state (new) vs belief (old) → the new claim supersedes: the old
  claim is re-kinded `belief`, status `superseded`, dated by its source's
  as_of; the new claim is appended, resolved.
- state vs state, or belief vs belief → the newer as_of supersedes the older
  (the older is re-kinded `belief` and `superseded`); the same year → contested,
  there is no newer.
- anything else (event vs state, belief against a fact) → contested.

`same` → ONE claim with the second source appended (never a sibling claim,
never a sibling beat), `resolution.by = corroborated`. `new` → the claim is
appended to the matched beat under a fresh id (the new story's ids would
collide). A story the judge and hint both call `new` is not applied here at
all: assembling its record needs the narration the job runner holds (slice 7);
the outcome just says `new`.

Every re-kinded claim keeps its text, sources and verdict byte-for-byte —
kind and status are not in the binding. A claim that gained a source is
re-bound (`model.bind(text, joined spans)`, the binding the slice-1
validator recomputes) with its judge model and entailment unchanged. Any
change to a beat's RESOLVED texts is reported in `MergeOutcome.rerun`: that
beat's `narration.claims_hash` is stale by design and P4/P5 must run again —
the rerun itself is the job runner's (slice 7); nothing here narrates.

Refusal loop, shaped like judge_claims.py / narrate.py: a transport failure
or an unreadable answer holds the beat (`narrate.BeatHeld`, phase P6); an
answer that is JSON but names ids the record does not have, or reports a
conflict without both values, is re-asked ONCE with the problems quoted back
(`prompts.render_merge_redo`), then held; any other `llm.LlmError`
(EstimateNotPrinted, MockScriptExhausted, JudgeIsAuthor, ...) is a programming
error and propagates as itself.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from src.ingest import llm, model, prompts
from src.ingest.group import Story
from src.ingest.judge_claims import JudgedClaim
from src.ingest.narrate import Emit, emitter, hold
from src.tour.claim_dedup import CLAIM_DEDUP_THRESHOLD, MIN_SHARED_TOKENS, _overlap, _signature

#: Output tokens requested for one merge answer (a verdict per new claim).
P6_MAX_TOKENS: int = 16_000  # thinking-inclusive; tests/test_ingest_output_caps.py

#: What the estimate prices per merge verdict — a projection: a verdict
#: per claim plus thinking at ~1x (Sonnet).
P6_EXPECTED_OUTPUT_TOKENS: int = 4_000

#: Two claim signatures state the same fact at or above this overlap
#: coefficient with at least this many shared tokens — the tour engine's
#: own dedup thresholds (src/tour/claim_dedup.py), reused not re-tuned.
SIGNATURE_MATCH_MIN: float = CLAIM_DEDUP_THRESHOLD
SIGNATURE_MIN_SHARED: int = MIN_SHARED_TOKENS

StoryOutcome = Literal["same", "new", "supersedes"]
ClaimVerdict = Literal["new", "same", "conflict"]
ClaimOutcomeKind = Literal["new", "same", "contested", "supersedes", "superseded"]

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.S)
_CLAIM_ID_RE = re.compile(r"^c(\d+)$")


class ClaimOutcome(BaseModel):
    """What happens to one new claim when the outcome is applied.

    `claim_id` is the new story's id; `applied_claim_id` is the id the fact
    carries in the beat afterwards — the existing claim's for `same` and
    `contested`, a fresh one for `new`, `supersedes` (the new claim) and
    `superseded` (the new claim, appended as a dated belief).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    claim_id: str
    verdict: ClaimVerdict
    existing_claim_id: str | None
    outcome: ClaimOutcomeKind
    applied_claim_id: str
    new_value: str | None
    existing_value: str | None
    reason: str


class MergeOutcome(BaseModel):
    """The decision P6 reached for one new story; `apply` materializes it.

    `rerun` lists the beat ids whose resolved claim texts change when this
    is applied: their `narration.claims_hash` is stale by design, so P4/P5
    must run again for them (the job runner's job).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    story_slug: str
    place: str
    judge_story: StoryOutcome | None
    judge_model: str | None
    story: StoryOutcome | None
    beat_id: str | None
    held: bool = False
    reason: str | None = None
    claims: list[ClaimOutcome] = []
    judged: list[JudgedClaim] = []
    rerun: list[str] = []


#: The P6 cost-estimate plan, priced PER NEW STORY (estimate it over the
#: story's claim texts): the first ask and the one re-ask — a CEILING, every
#: story priced as if re-asked once, until slice 9 measures live rates.
P6_PLAN: tuple[llm.PhaseCall, ...] = (
    llm.PhaseCall(
        phase="P6",
        role="merge_judge",
        calls_per_unit=1,
        overhead_tokens=max(1, len(prompts.MERGE_PROMPT) // 4),
        expected_output_tokens=P6_EXPECTED_OUTPUT_TOKENS,
    ),
    llm.PhaseCall(
        phase="P6",
        role="merge_judge",
        calls_per_unit=1,
        overhead_tokens=max(1, len(prompts.MERGE_REDO_PROMPT) // 4),
        expected_output_tokens=P6_EXPECTED_OUTPUT_TOKENS,
    ),
)


# ── Parsing ─────────────────────────────────────────────────────────────────


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


_CLAIM_KEYS = frozenset(
    {"claim_id", "verdict", "existing_claim_id", "new_value", "existing_value", "reason"}
)


def parse_merge(text: str) -> dict | None:
    """Turn one raw P6 answer into `{story, beat_id, claims: [...]}`, or None."""
    parsed = _extract_json_object(text)
    if not isinstance(parsed, dict) or set(parsed.keys()) != {"story", "beat_id", "claims"}:
        return None
    if parsed["story"] not in prompts.STORY_VERDICTS or not isinstance(parsed["beat_id"], str):
        return None
    if not isinstance(parsed["claims"], list):
        return None
    for item in parsed["claims"]:
        if not isinstance(item, dict) or set(item.keys()) != _CLAIM_KEYS:
            return None
        if item["verdict"] not in prompts.CLAIM_VERDICTS:
            return None
        if not all(isinstance(item[key], str) for key in _CLAIM_KEYS - {"verdict"}):
            return None
    return parsed


# ── The deterministic signature hint ────────────────────────────────────────


class Hint(BaseModel):
    """What the claim signatures say: which beat (if any) the new story
    matches, and which existing claim of it each new claim matches."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    beat_id: str | None
    matches: dict[str, str | None]


def _best_match(text: str, beat: model.Beat) -> str | None:
    signature = _signature(text)
    best: str | None = None
    best_score = 0.0
    for claim in beat.claims:
        other = _signature(claim.text)
        score = _overlap(signature, other)
        if (
            score >= SIGNATURE_MATCH_MIN
            and len(signature & other) >= SIGNATURE_MIN_SHARED
            and score > best_score
        ):
            best, best_score = claim.claim_id, score
    return best


def signature_hint(claims: Sequence[JudgedClaim], existing: Sequence[model.Beat]) -> Hint:
    """The deterministic answer: the candidate beat matching the most new
    claims (first wins a tie), taken as the story's beat when it matches at
    least half of them; otherwise no beat and no matches."""
    unmatched = Hint(beat_id=None, matches={c.draft.claim_id: None for c in claims})
    best_beat: model.Beat | None = None
    best_pairs: dict[str, str | None] = {}
    best_count = 0
    for beat in existing:
        pairs = {c.draft.claim_id: _best_match(c.draft.text, beat) for c in claims}
        count = sum(1 for match in pairs.values() if match is not None)
        if count > best_count:
            best_beat, best_pairs, best_count = beat, pairs, count
    if best_beat is None or best_count * 2 < len(claims):
        return unmatched
    return Hint(beat_id=best_beat.beat_id, matches=best_pairs)


# ── Judge answer checks ─────────────────────────────────────────────────────


def answer_problems(
    answer: dict, claims: Sequence[JudgedClaim], existing: Sequence[model.Beat]
) -> list[str]:
    """Why a schema-valid answer cannot be applied to THIS record: unknown
    beat or claim ids, a claim answered twice or not at all, a conflict
    without both values. These earn the one re-ask."""
    problems: list[str] = []
    beats = {beat.beat_id: beat for beat in existing}
    beat: model.Beat | None = None
    if answer["story"] == "new":
        if answer["beat_id"]:
            problems.append('story "new" must leave beat_id empty')
    elif answer["beat_id"] not in beats:
        problems.append(f"beat_id {answer['beat_id']!r} is not a beat at this place")
    else:
        beat = beats[answer["beat_id"]]

    expected = [c.draft.claim_id for c in claims]
    answered = [item["claim_id"] for item in answer["claims"]]
    if sorted(answered) != sorted(expected):
        problems.append(
            f"claims must be answered exactly once each: expected {expected}, got {answered}"
        )
    existing_ids = {c.claim_id for c in beat.claims} if beat is not None else set()
    for item in answer["claims"]:
        cid = item["claim_id"]
        if item["verdict"] == "new":
            if item["existing_claim_id"]:
                problems.append(f'claim {cid}: verdict "new" must leave existing_claim_id empty')
            continue
        if answer["story"] == "new":
            problems.append(f'claim {cid}: a "new" story cannot have a {item["verdict"]} claim')
            continue
        if item["existing_claim_id"] not in existing_ids:
            problems.append(
                f"claim {cid}: existing_claim_id {item['existing_claim_id']!r} is not a "
                f"claim of beat {answer['beat_id']}"
            )
        if item["verdict"] == "conflict":
            if not item["new_value"] or not item["existing_value"]:
                problems.append(f"claim {cid}: a conflict must quote both values")
            elif item["new_value"] == item["existing_value"]:
                problems.append(
                    f"claim {cid}: a conflict must quote two DIFFERENT values, "
                    f"got {item['new_value']!r} twice"
                )
    return problems


def disagreements(answer: dict, hint: Hint) -> list[str]:
    """Where the judge and the signature hint part ways; any entry holds
    the new story."""
    found: list[str] = []
    if answer["story"] == "new":
        if hint.beat_id is not None:
            found.append(f"story: judge says new; signature matches beat {hint.beat_id}")
    elif hint.beat_id is None:
        found.append(
            f"story: judge says {answer['story']} as beat {answer['beat_id']}; "
            "signature matches no beat"
        )
    elif hint.beat_id != answer["beat_id"]:
        found.append(
            f"story: judge says beat {answer['beat_id']}; signature matches beat {hint.beat_id}"
        )
    for item in answer["claims"]:
        cid = item["claim_id"]
        hinted = hint.matches.get(cid)
        if item["verdict"] == "new":
            if hinted is not None:
                found.append(f"claim {cid}: judge says new; signature matches {hinted}")
        elif hinted != item["existing_claim_id"]:
            found.append(
                f"claim {cid}: judge says {item['verdict']} with "
                f"{item['existing_claim_id']}; signature matches {hinted or 'nothing'}"
            )
    return found


# ── Kind rules (D9) ─────────────────────────────────────────────────────────


def as_of_year(as_of: int | str) -> int:
    """The year of an as-of value: an int year, or an ISO date's year."""
    return as_of if isinstance(as_of, int) else int(str(as_of)[:4])


def claim_year(claim: model.Claim) -> int:
    """A claim's as-of year: its newest source's."""
    return max(as_of_year(source.as_of) for source in claim.sources)


def conflict_outcome(
    new_kind: str, new_year: int, old_kind: str, old_year: int
) -> Literal["contested", "supersedes", "superseded"]:
    """What a judged conflict becomes, by kind and date alone (see the
    module docstring). `supersedes`: the new claim supersedes the old;
    `superseded`: the old, newer claim supersedes the new one."""
    if old_kind == "belief" and new_kind in ("event", "state"):
        return "supersedes"
    if new_kind == old_kind and new_kind in ("state", "belief"):
        if new_year > old_year:
            return "supersedes"
        if old_year > new_year:
            return "superseded"
    return "contested"


# ── The merge itself ────────────────────────────────────────────────────────


def _prompt_claims(claims: Sequence[JudgedClaim]) -> list[dict]:
    return [
        {
            "claim_id": c.draft.claim_id,
            "text": c.draft.text,
            "kind": c.draft.kind,
            "as_of": as_of_year(c.draft.source.as_of),
        }
        for c in claims
    ]


def _prompt_existing(existing: Sequence[model.Beat]) -> list[dict]:
    return [
        {
            "beat_id": beat.beat_id,
            "title": beat.title,
            "claims": [
                {
                    "claim_id": claim.claim_id,
                    "text": claim.text,
                    "kind": claim.kind,
                    "status": claim.status,
                    "as_of": sorted({as_of_year(s.as_of) for s in claim.sources}),
                    "stated_values": [
                        s.stated_value for s in claim.sources if s.stated_value is not None
                    ],
                }
                for claim in beat.claims
            ],
        }
        for beat in existing
    ]


def _ask_judge(
    emit: Emit, story: Story, client: llm.ModelClient, prompt: str
) -> tuple[dict, str, str]:
    """One sync P6 call; returns (parsed answer, raw text, response model id).

    The same failure contract as narrate.ask_author: an
    `llm.EmptyCompletion`/`llm.TruncatedCompletion`, or a raw (non-LlmError)
    `ValueError` from a duck-typed transport, holds the beat in P6, as does
    an unreadable answer; any other `llm.LlmError` propagates as itself.
    """
    try:
        completion = client.complete(
            "merge_judge", prompt, prompts.P6_MERGE_SCHEMA, phase="P6", max_tokens=P6_MAX_TOKENS
        )
    except (llm.EmptyCompletion, llm.TruncatedCompletion) as exc:
        hold(emit, story, "P6", f"transport: {exc}", exc)
    except llm.LlmError:
        raise
    except ValueError as exc:
        hold(emit, story, "P6", f"transport: {exc}", exc)
    parsed = parse_merge(completion.text)
    if parsed is None:
        hold(
            emit,
            story,
            "P6",
            "schema: the merge judge's answer was not valid JSON matching the P6 merge schema",
        )
    return parsed, completion.text, completion.model_id


def _fresh_ids(beat: model.Beat):
    """Claim ids not yet used by `beat`, c{n:02d} from its highest upward."""
    used = {c.claim_id for c in beat.claims}
    numbers = [int(m.group(1)) for c in beat.claims if (m := _CLAIM_ID_RE.match(c.claim_id))]
    n = max(numbers, default=0)
    while True:
        n += 1
        candidate = f"c{n:02d}"
        if candidate not in used:
            used.add(candidate)
            yield candidate


def _decide_claims(
    answer: dict, claims: Sequence[JudgedClaim], beat: model.Beat | None
) -> list[ClaimOutcome]:
    by_id = {c.draft.claim_id: c for c in claims}
    existing = {c.claim_id: c for c in beat.claims} if beat is not None else {}
    fresh = _fresh_ids(beat) if beat is not None else None
    outcomes: list[ClaimOutcome] = []
    for item in answer["claims"]:
        judged = by_id[item["claim_id"]]
        verdict = item["verdict"]
        existing_id = item["existing_claim_id"] or None
        new_value = item["new_value"] or None
        existing_value = item["existing_value"] or None
        if verdict == "new":
            outcome: ClaimOutcomeKind = "new"
            applied = next(fresh) if fresh is not None else judged.draft.claim_id
        elif verdict == "same":
            outcome, applied = "same", item["existing_claim_id"]
        else:
            old = existing[item["existing_claim_id"]]
            outcome = conflict_outcome(
                judged.draft.kind,
                as_of_year(judged.draft.source.as_of),
                old.kind,
                claim_year(old),
            )
            applied = item["existing_claim_id"] if outcome == "contested" else next(fresh)
        outcomes.append(
            ClaimOutcome(
                claim_id=item["claim_id"],
                verdict=verdict,
                existing_claim_id=existing_id,
                outcome=outcome,
                applied_claim_id=applied,
                new_value=new_value,
                existing_value=existing_value,
                reason=item["reason"],
            )
        )
    return outcomes


#: Claim outcomes that change the beat's RESOLVED texts, and so its hash.
_RESOLVED_TEXTS_CHANGE: frozenset[str] = frozenset({"new", "contested", "supersedes"})


def merge(
    new_story: Story,
    claims: Sequence[JudgedClaim],
    existing: Sequence[model.Beat],
    client: llm.ModelClient,
    events: llm.EventSink | None = None,
) -> MergeOutcome:
    """Decide how the new story joins the beats at its place; see the module
    docstring. `existing` is the candidate set — the beats already at
    `new_story.place` (D7: small candidate sets per place); the caller
    selects them. Pure: nothing is applied here."""
    emit = emitter(events)
    new_claims = _prompt_claims(claims)
    candidates = _prompt_existing(existing)
    prompt = prompts.render_merge(new_story.place, new_story.title, new_claims, candidates)

    answer, raw, judge_model = _ask_judge(emit, new_story, client, prompt)
    problems = answer_problems(answer, claims, existing)
    if problems:
        emit(
            "merge_reasked",
            {"story_slug": new_story.story_slug, "problems": list(problems)},
        )
        redo = prompts.render_merge_redo(
            new_story.place, new_story.title, new_claims, candidates, raw, problems
        )
        answer, raw, judge_model = _ask_judge(emit, new_story, client, redo)
        problems = answer_problems(answer, claims, existing)
        if problems:
            hold(emit, new_story, "P6", "answer: " + "; ".join(problems))

    hint = signature_hint(claims, existing)
    disagreed = disagreements(answer, hint)
    if disagreed:
        reason = "judge and signature disagree: " + "; ".join(disagreed)
        emit(
            "beat_held",
            {"story_slug": new_story.story_slug, "phase": "P6", "reason": reason},
        )
        return MergeOutcome(
            story_slug=new_story.story_slug,
            place=new_story.place,
            judge_story=answer["story"],
            judge_model=judge_model,
            story=None,
            beat_id=None,
            held=True,
            reason=reason,
            judged=list(claims),
        )

    beat = next((b for b in existing if b.beat_id == answer["beat_id"]), None)
    outcomes = _decide_claims(answer, claims, beat)
    if beat is None:
        story: StoryOutcome = "new"
        rerun: list[str] = []
    else:
        story = "supersedes" if any(o.outcome == "supersedes" for o in outcomes) else "same"
        changes = any(o.outcome in _RESOLVED_TEXTS_CHANGE for o in outcomes)
        rerun = [beat.beat_id] if changes else []
    emit(
        "merge_decided",
        {
            "story_slug": new_story.story_slug,
            "story": story,
            "beat_id": beat.beat_id if beat is not None else None,
            "claims": [
                {
                    "claim_id": o.claim_id,
                    "outcome": o.outcome,
                    "existing_claim_id": o.existing_claim_id,
                }
                for o in outcomes
            ],
            "rerun": list(rerun),
        },
    )
    return MergeOutcome(
        story_slug=new_story.story_slug,
        place=new_story.place,
        judge_story=answer["story"],
        judge_model=judge_model,
        story=story,
        beat_id=beat.beat_id if beat is not None else None,
        claims=outcomes,
        judged=list(claims),
        rerun=rerun,
    )


# ── Applying an outcome ─────────────────────────────────────────────────────


def _rebound(claim: model.Claim) -> model.Verdict:
    """The claim's verdict re-bound over its current sources — judge model
    and entailment untouched."""
    return model.Verdict(
        judge_model=claim.verdict.judge_model,
        entailed=claim.verdict.entailed,
        bound_to=model.bind(claim.text, "\n".join(s.span for s in claim.sources)),
    )


def _new_claim(
    judged: JudgedClaim, claim_id: str, stated_value: str | None, *, superseded: bool = False
) -> model.Claim:
    source = judged.draft.source.model_copy(update={"stated_value": stated_value})
    return model.Claim(
        claim_id=claim_id,
        text=judged.draft.text,
        kind="belief" if superseded else judged.draft.kind,
        status="superseded" if superseded else "resolved",
        sources=[source],
        resolved_value=None,
        resolution=None,
        verdict=judged.verdict,
    )


def apply(outcome: MergeOutcome, beats: Sequence[model.Beat]) -> list[model.Beat]:
    """Materialize `outcome` over `beats`; see the module docstring.

    Returns a new list — the caller's beats are never mutated. A held
    outcome cannot be applied (ValueError: it is a queue item); a `new`
    story returns the beats unchanged (its record is the runner's to
    assemble). The matched beat must be among `beats`.
    """
    if outcome.held:
        raise ValueError(f"held outcome for story {outcome.story_slug!r} cannot be applied")
    if outcome.story == "new" or outcome.beat_id is None:
        return list(beats)
    if not any(b.beat_id == outcome.beat_id for b in beats):
        raise ValueError(f"beat {outcome.beat_id!r} is not among the beats given")

    judged = {c.draft.claim_id: c for c in outcome.judged}
    result: list[model.Beat] = []
    for beat in beats:
        if beat.beat_id != outcome.beat_id:
            result.append(beat)
            continue
        claims = [c.model_copy(deep=True) for c in beat.claims]
        by_id = {c.claim_id: i for i, c in enumerate(claims)}
        for item in outcome.claims:
            new = judged[item.claim_id]
            if item.outcome == "new":
                claims.append(_new_claim(new, item.applied_claim_id, item.new_value))
                continue
            old = claims[by_id[item.existing_claim_id or item.applied_claim_id]]
            if item.outcome == "same":
                old.sources.append(
                    new.draft.source.model_copy(update={"stated_value": item.new_value})
                )
                if old.resolution is None:
                    old.resolution = model.Resolution(by="corroborated")
                old.verdict = _rebound(old)
            elif item.outcome == "contested":
                for source in old.sources:
                    if source.stated_value is None:
                        source.stated_value = item.existing_value
                old.sources.append(
                    new.draft.source.model_copy(update={"stated_value": item.new_value})
                )
                old.status = "contested"
                old.resolved_value = None
                old.resolution = None
                old.verdict = _rebound(old)
            elif item.outcome == "supersedes":
                old.kind = "belief"
                old.status = "superseded"
                claims.append(_new_claim(new, item.applied_claim_id, item.new_value))
            else:  # superseded: the new claim arrives as a dated belief
                claims.append(
                    _new_claim(new, item.applied_claim_id, item.new_value, superseded=True)
                )
        result.append(beat.model_copy(update={"claims": claims}))
    return result
