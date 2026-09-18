"""P6 merge: a new story meets the beats the corpus already holds at its place.

Docs/ingestion/rebuild-spec.md §3 (P6), §1 D7/D8/D9 and CONTEXT.md ("Stated
value", "Resolved value", "Contested claim", "Event/State/Belief claim",
"Supersedes") pin the rules below.

`merge(new_story, claims, existing, client)` asks the merge judge ONCE, sync,
under the `merge_judge` role (P6 is not one of `llm.BATCH_PHASES`; the role's
model may never be the author's — llm's JudgeIsAuthor gate refuses that at
construction and per response, and this module does not re-implement it),
with the new story's claim TEXTS and every candidate beat's claims — never a
span. Candidate beats are shown and answered by handle (`b1`, and their
claims `b1.c02`): the judge once retyped a beat id wrong, and two beats at
one place can both hold a `c01`. Each new claim may match a claim of ANY
beat at the place, whatever the story verdict — two books cut the same facts
into different stories (slice 10: job 2's contract forbade that and talked
the judge out of correct matches). Every raw answer is emitted as
`merge_answered` before it is checked.

Independently of the judge, `signature_hint` computes each new claim's best
signature match from `src.tour.claim_dedup`'s claim signatures (the same
"same fact" test the tour engine's dedup uses, imported, never copied). The
hint is a one-way tripwire (D7 as amended in slice 10):

- The signature matches a claim the judge called `new`, or a different claim
  than the one the judge named → the new story is HELD: `MergeOutcome.held`,
  a review-queue item (D13), `beat_held` emitted for phase P6, nothing
  applied. The hint is never shown to the judge and the judge is never
  re-asked toward it — that would make the hint the judge.
- The signature matches nothing → no evidence either way; the judge's answer
  applies. A lexical signature is blind to paraphrase (it matched one claim
  across job 2's 12 judged stories).

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

A matched claim FOLDS into the claim that holds it, in whichever beat that
is: `same` → ONE claim with the second source appended (never a sibling
claim, never a sibling beat), `resolution.by = corroborated`; a conflict →
the kind rules above. A `new` claim of a matched story is appended to the
story's beat under a fresh id (the new story's ids would collide). A `new`
story's unmatched claims are not applied here: assembling its record needs
the narration the job runner holds (slice 7), and the runner narrates it
again over those claims alone. Every fold is emitted as `merge_folded` with
both texts and the new span, so a wrong `same` can be audited.

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
from collections.abc import Iterator, Sequence
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

    `claim_id` is the new story's id; `existing_beat_id` + `existing_claim_id`
    name the matched claim at the place, in whichever beat holds it (None
    for `new`). `applied_claim_id` is the id the fact carries afterwards —
    the existing claim's for `same` and `contested`; a fresh id in the
    matched claim's beat for `supersedes` (the new claim) and `superseded`
    (the new claim, appended as a dated belief); for `new`, a fresh id in
    the story's matched beat, or the story's own id when the story is new.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    claim_id: str
    verdict: ClaimVerdict
    existing_beat_id: str | None
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

#: A claim at the place, named by the beat that holds it: claim ids repeat
#: across beats (every story numbers its own), so a bare id is ambiguous.
ClaimRef = tuple[str, str]


class Hint(BaseModel):
    """What the claim signatures say: for each new claim, the claim at the
    place it states the same fact as, (beat id, claim id), or None."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    matches: dict[str, ClaimRef | None]


def _best_match(text: str, existing: Sequence[model.Beat]) -> ClaimRef | None:
    signature = _signature(text)
    best: ClaimRef | None = None
    best_score = 0.0
    for beat in existing:
        for claim in beat.claims:
            other = _signature(claim.text)
            score = _overlap(signature, other)
            if (
                score >= SIGNATURE_MATCH_MIN
                and len(signature & other) >= SIGNATURE_MIN_SHARED
                and score > best_score
            ):
                best, best_score = (beat.beat_id, claim.claim_id), score
    return best


def signature_hint(claims: Sequence[JudgedClaim], existing: Sequence[model.Beat]) -> Hint:
    """The deterministic answer, claim by claim: each new claim's best
    signature match among every claim at the place (first wins a tie).

    There is no story-level vote. Slice 9 took a beat as the story's only
    when it matched at least half the story's claims, which threw away the
    one real match job 2's signatures found (Frank Lloyd Wright, 1 of 7)."""
    return Hint(matches={c.draft.claim_id: _best_match(c.draft.text, existing) for c in claims})


# ── Judge answer checks ─────────────────────────────────────────────────────


def handles(existing: Sequence[model.Beat]) -> dict[str, model.Beat]:
    """The short handle each candidate beat is shown and answered under:
    `b1`, `b2`, ... in candidate order. Its claims are `b1.c02`."""
    return {f"b{i}": beat for i, beat in enumerate(existing, 1)}


def _claim_ref(ref: str, handled: dict[str, model.Beat]) -> ClaimRef | None:
    """(beat id, claim id) for a `b1.c02` handle the place has, else None."""
    beat_handle, _, claim_id = ref.partition(".")
    beat = handled.get(beat_handle)
    if beat is None or not any(c.claim_id == claim_id for c in beat.claims):
        return None
    return beat.beat_id, claim_id


def answer_problems(
    answer: dict, claims: Sequence[JudgedClaim], handled: dict[str, model.Beat]
) -> list[str]:
    """Why a schema-valid answer cannot be applied to THIS record: unknown
    beat or claim handles, a claim answered twice or not at all, a conflict
    without both values. These earn the one re-ask.

    A claim may match a claim of ANY beat at the place, whatever the story
    verdict: a new story can share a fact with a differently cut existing
    one (slice 9 forbade that, and job 2's re-asks talked the judge out of
    three correct matches)."""
    problems: list[str] = []
    if answer["story"] == "new":
        if answer["beat_id"]:
            problems.append('story "new" must leave beat_id empty')
    elif answer["beat_id"] not in handled:
        problems.append(f"beat_id {answer['beat_id']!r} is not a beat at this place")

    expected = [c.draft.claim_id for c in claims]
    answered = [item["claim_id"] for item in answer["claims"]]
    if sorted(answered) != sorted(expected):
        problems.append(
            f"claims must be answered exactly once each: expected {expected}, got {answered}"
        )
    for item in answer["claims"]:
        cid = item["claim_id"]
        if item["verdict"] == "new":
            if item["existing_claim_id"]:
                problems.append(f'claim {cid}: verdict "new" must leave existing_claim_id empty')
            continue
        if _claim_ref(item["existing_claim_id"], handled) is None:
            problems.append(
                f"claim {cid}: existing_claim_id {item['existing_claim_id']!r} is not a "
                "claim at this place"
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


def resolve_handles(answer: dict, handled: dict[str, model.Beat]) -> dict:
    """The answer with every handle replaced by real ids: `beat_id` the
    story's beat id, and per claim `existing_beat_id` + `existing_claim_id`.
    Call it only on an answer `answer_problems` passed."""
    beat = handled.get(answer["beat_id"])
    claims = []
    for item in answer["claims"]:
        ref = _claim_ref(item["existing_claim_id"], handled) if item["existing_claim_id"] else None
        claims.append(
            {
                **item,
                "existing_beat_id": ref[0] if ref else "",
                "existing_claim_id": ref[1] if ref else "",
            }
        )
    return {**answer, "beat_id": beat.beat_id if beat else "", "claims": claims}


def _ref_text(ref: ClaimRef | None) -> str:
    return f"{ref[1]} of {ref[0]}" if ref else "nothing"


def disagreements(answer: dict, hint: Hint) -> list[str]:
    """Where the signature hint has POSITIVE evidence against the judge
    (handles resolved): a signature match on a claim the judge called
    `new`, or a match to a different claim than the judge named. Any entry
    holds the new story.

    A judge match the signature cannot see is not a disagreement: a lexical
    signature is blind to paraphrase — across job 2's 12 judged stories it
    matched one claim while the judge named four correct matches — so its
    silence is no evidence (D7 as amended in slice 10)."""
    found: list[str] = []
    for item in answer["claims"]:
        cid = item["claim_id"]
        hinted = hint.matches.get(cid)
        if item["verdict"] == "new":
            if hinted is not None:
                found.append(f"claim {cid}: judge says new; signature matches {_ref_text(hinted)}")
            continue
        named = (item["existing_beat_id"], item["existing_claim_id"])
        if hinted is not None and hinted != named:
            found.append(
                f"claim {cid}: judge says {item['verdict']} with {_ref_text(named)}; "
                f"signature matches {_ref_text(hinted)}"
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


def _prompt_existing(handled: dict[str, model.Beat]) -> list[dict]:
    return [
        {
            "handle": handle,
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
        for handle, beat in handled.items()
    ]


def _ask_judge(
    emit: Emit, story: Story, client: llm.ModelClient, prompt: str, attempt: int
) -> tuple[dict, str, str]:
    """One sync P6 call; returns (parsed answer, raw text, response model id).

    The raw answer is emitted as `merge_answered` the moment it arrives,
    before it is parsed or checked, so every verdict the judge gave is in
    the job log whatever happens to it next.

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
    emit(
        "merge_answered",
        {"story_slug": story.story_slug, "attempt": attempt, "model": completion.model_id,
         "answer": completion.text},
    )
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
    answer: dict,
    claims: Sequence[JudgedClaim],
    existing: Sequence[model.Beat],
    story_beat: model.Beat | None,
) -> list[ClaimOutcome]:
    by_id = {c.draft.claim_id: c for c in claims}
    beats = {b.beat_id: b for b in existing}
    fresh: dict[str, Iterator[str]] = {}

    def fresh_id(beat_id: str) -> str:
        if beat_id not in fresh:
            fresh[beat_id] = _fresh_ids(beats[beat_id])
        return next(fresh[beat_id])

    outcomes: list[ClaimOutcome] = []
    for item in answer["claims"]:
        judged = by_id[item["claim_id"]]
        verdict = item["verdict"]
        beat_id = item["existing_beat_id"] or None
        existing_id = item["existing_claim_id"] or None
        new_value = item["new_value"] or None
        existing_value = item["existing_value"] or None
        if verdict == "new":
            outcome: ClaimOutcomeKind = "new"
            applied = (
                fresh_id(story_beat.beat_id) if story_beat is not None else judged.draft.claim_id
            )
        elif verdict == "same":
            outcome, applied = "same", item["existing_claim_id"]
        else:
            old = next(c for c in beats[beat_id].claims if c.claim_id == existing_id)
            outcome = conflict_outcome(
                judged.draft.kind,
                as_of_year(judged.draft.source.as_of),
                old.kind,
                claim_year(old),
            )
            applied = existing_id if outcome == "contested" else fresh_id(beat_id)
        outcomes.append(
            ClaimOutcome(
                claim_id=item["claim_id"],
                verdict=verdict,
                existing_beat_id=beat_id,
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
    handled = handles(existing)
    candidates = _prompt_existing(handled)
    prompt = prompts.render_merge(new_story.place, new_story.title, new_claims, candidates)

    answer, raw, judge_model = _ask_judge(emit, new_story, client, prompt, 1)
    problems = answer_problems(answer, claims, handled)
    if problems:
        emit(
            "merge_reasked",
            {"story_slug": new_story.story_slug, "problems": list(problems)},
        )
        redo = prompts.render_merge_redo(
            new_story.place, new_story.title, new_claims, candidates, raw, problems
        )
        answer, raw, judge_model = _ask_judge(emit, new_story, client, redo, 2)
        problems = answer_problems(answer, claims, handled)
        if problems:
            hold(emit, new_story, "P6", "answer: " + "; ".join(problems))
    answer = resolve_handles(answer, handled)

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
    outcomes = _decide_claims(answer, claims, existing, beat)
    story: StoryOutcome
    if beat is None:
        story = "new"
    else:
        story = "supersedes" if any(o.outcome == "supersedes" for o in outcomes) else "same"
    changed = {
        beat.beat_id if o.outcome == "new" else o.existing_beat_id
        for o in outcomes
        if o.outcome in _RESOLVED_TEXTS_CHANGE and (o.outcome != "new" or beat is not None)
    }
    rerun = [b.beat_id for b in existing if b.beat_id in changed]
    texts = {c.draft.claim_id: c for c in claims}
    for o in outcomes:
        if o.existing_beat_id is None:
            continue
        held_by = next(b for b in existing if b.beat_id == o.existing_beat_id)
        emit(
            "merge_folded",
            {
                "story_slug": new_story.story_slug,
                "claim_id": o.claim_id,
                "text": texts[o.claim_id].draft.text,
                "span": texts[o.claim_id].draft.source.span,
                "outcome": o.outcome,
                "existing_beat_id": o.existing_beat_id,
                "existing_claim_id": o.existing_claim_id,
                "existing_text": next(
                    c.text for c in held_by.claims if c.claim_id == o.existing_claim_id
                ),
            },
        )
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
                    "existing_beat_id": o.existing_beat_id,
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
    outcome cannot be applied (ValueError: it is a queue item). Every
    matched claim lands in the beat that holds its match, whatever the
    story verdict; a `new` claim joins the story's matched beat, or — when
    the story is new — stays with the story, whose record is the runner's
    to assemble. Every beat the outcome names must be among `beats`.
    """
    if outcome.held:
        raise ValueError(f"held outcome for story {outcome.story_slug!r} cannot be applied")
    targets = {o.existing_beat_id for o in outcome.claims if o.existing_beat_id is not None}
    if outcome.beat_id is not None:
        targets.add(outcome.beat_id)
    for beat_id in sorted(targets - {b.beat_id for b in beats}):
        raise ValueError(f"beat {beat_id!r} is not among the beats given")

    judged = {c.draft.claim_id: c for c in outcome.judged}
    claims_of = {
        b.beat_id: [c.model_copy(deep=True) for c in b.claims]
        for b in beats
        if b.beat_id in targets
    }
    for item in outcome.claims:
        new = judged[item.claim_id]
        if item.outcome == "new":
            if outcome.beat_id is not None:
                claims_of[outcome.beat_id].append(
                    _new_claim(new, item.applied_claim_id, item.new_value)
                )
            continue
        claims = claims_of[item.existing_beat_id]
        old = next(c for c in claims if c.claim_id == item.existing_claim_id)
        if item.outcome == "same":
            old.sources.append(new.draft.source.model_copy(update={"stated_value": item.new_value}))
            if old.resolution is None:
                old.resolution = model.Resolution(by="corroborated")
            old.verdict = _rebound(old)
        elif item.outcome == "contested":
            for source in old.sources:
                if source.stated_value is None:
                    source.stated_value = item.existing_value
            old.sources.append(new.draft.source.model_copy(update={"stated_value": item.new_value}))
            old.status = "contested"
            old.resolved_value = None
            old.resolution = None
            old.verdict = _rebound(old)
        elif item.outcome == "supersedes":
            old.kind = "belief"
            old.status = "superseded"
            claims.append(_new_claim(new, item.applied_claim_id, item.new_value))
        else:  # superseded: the new claim arrives as a dated belief
            claims.append(_new_claim(new, item.applied_claim_id, item.new_value, superseded=True))
    return [
        b.model_copy(update={"claims": claims_of[b.beat_id]}) if b.beat_id in claims_of else b
        for b in beats
    ]
