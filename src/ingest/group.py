"""P2 group: turn P1's claim drafts into Stories, one per place.

Docs/ingestion/rebuild-spec.md and CONTEXT.md pin the shapes below.

Step 13 landed the types `group()` will run on: `Enrichment`
(the ten free-text/optional beat-enrichment fields, identical in shape
and default to `model.Beat`'s own fields — the P2 response schema forces
every one of them to be present as a string/bool/array so the structured-
output subset never has a bare `null`, so `Enrichment`'s own validators
map the schema's "nothing to say" sentinels — `''` for the free-text
fields, `'unknown'` for `kid_friendly` — back to the real `None` domain
value `model.Beat` uses), `Story` (one story: a title, its computed
`story_slug`, the place it resolved to, whether that place is a
newly-seen POI, its lenses/beat_type/claim_ids, and its `Enrichment`),
`P2_MAX_TOKENS`/`P2_PLAN` (the two-row cost-estimate plan for a P2 call,
priced against the real `GROUP_PROMPT_TEMPLATE`/`GROUP_REDO_TEMPLATE`
lengths — `P2` is not one of `llm.BATCH_PHASES`, so both rows price as
sync calls, unlike `P1_PLAN`'s batch rows), and `parse_stories` (turn one
raw P2 answer into its list of story dicts, or `None` for anything
unreadable).

`parse_stories` tolerates prose wrapped around the JSON object (the same
"Sure, here you go:\\n{...}\\nLet me know!" tolerance `decompose.parse_claims`
gives), but is otherwise strict, matching `prompts.P2_RESPONSE_SCHEMA`
exactly: the top-level payload must be a dict whose only key is
`stories`, `stories` must be a non-empty list, and every item must be a
dict with exactly the keys `title`/`place`/`beat_type`/`lenses`/
`claim_ids`/`enrichment` (`beat_type` one of `model.BEAT_TYPE_VALUES`,
every `lenses` entry one of `TAGGABLE_LENSES`, `enrichment` itself a dict
with exactly its own ten required keys). A single invalid item makes the
WHOLE answer unreadable — never a partial list of only the good stories,
for the same reason `parse_claims` refuses whole: a caller that silently
dropped a bad item would be inventing which stories survived rather than
re-asking the model for a clean answer.

`group()` itself (place resolution, the refusal loop, the transport-
failure handling) is steps 14-16.

Step 14 landed `group()`'s happy path: an empty `claims` list
short-circuits to `[]` before any call (not even a render); otherwise
exactly one `client.complete()` call (never `complete_batch` — P2 is a
sync phase) of `prompts.render_group(...)` over every claim's
`(claim_id, text)` pair, with NO poi list ever entering the prompt (D7:
place resolution happens locally, after the model answers). A resolved
answer's raw stories are turned into `Story` via `gates.resolve_place`
against `pois`: a match keeps the canonical POI name with `new_poi=False`;
no match keeps the place as given, flagged `new_poi=True`, and emits
`place_unresolved` — the story itself is NOT refused for it, since
`gates.resolve_place` has nothing to do with whether a story's claim
partition is otherwise clean. `group()` never calls `client.estimate()`
itself (an unarmed client's `complete()` raises `llm.EstimateNotPrinted`
here, uncaught), and a request with no non-empty tie-break rule to render
(`prompts.render_group`'s own `TieBreakMissing`) is raised before any
call, since the prompt is built before the client is ever touched.

Step 15 landed the refusal loop around that call. Every answer
is graded twice: first by `parse_stories` (schema), then by
`gates.story_problems` over the raw stories and the unit's own claim ids —
the aggregate that enforces the partition (every claim in exactly one
story, no id the unit never had), the structural claim floor (one claim for
stop_orientation/transit/sidebar, two for everything else, never zero), the
lens/beat_type rules and slug uniqueness. Any defect on attempt 1 refuses
the WHOLE answer — never the failing stories alone — emitting one
`stories_refused` carrying every reason, and buys exactly ONE re-ask via
`prompts.render_group_redo`, which quotes each reason back verbatim and
asks for a fresh answer from the start rather than a patch of the flagged
stories. A third call never happens.

Step 16 landed the second-failure drop semantics and the
transport-failure handling.

A transport failure — `llm.EmptyCompletion`/`llm.TruncatedCompletion`
raised by `client.complete()`, or a raw (non-`llm.LlmError`) `ValueError`
from a duck-typed transport — holds the unit immediately (`unit_held` then
`decompose.UnitHeld` with `.phase == 'P2'`), with NO second call, on
either attempt. A programming error (`llm.EstimateNotPrinted`,
`llm.MockScriptExhausted`, or any other `llm.LlmError`) still propagates
as itself, uncaught, exactly like `decompose()`.

An answer still unreadable on attempt 2 (`parse_stories` returns `None`
again) emits its own `stories_refused` and then `unit_held`, and raises
`UnitHeld` — a third call never happens.

A readable attempt-2 answer that still fails `gates.story_problems` is no
longer re-asked (the one-re-ask budget is spent); instead it is pruned in
place, never patched or re-typed: (1) any story with a per-story defect
independent of claim membership — `gates.lenses_valid` — is dropped
whole, emitting `story_dropped` naming its slug; (2) `gates.every_claim_once`
is then run over the SURVIVING stories' claim lists — a claim absent from
all of them (including one whose only story was just dropped in step 1)
is `claim_dropped` as an orphan, and a claim present in more than one
surviving story is `claim_dropped` as double-booked and stripped from
every story that named it (never left in one and pulled from the rest —
no claim is "kept" by a tie-break here, since neither story is more right
than the other); (3) any story that thereby falls under `gates.claim_floor`
is dropped too, emitting `story_dropped` with a `too_few_claims` reason —
and every claim still sitting in a story dropped this way is ALSO
`claim_dropped` (a claim never disappears without a logged reason, even
one that was never itself the defect). The claims and stories that survive
all three passes are what `group()` returns, still going through the same
place-resolution step 14 built.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from src.ingest import llm, model, prompts
from src.ingest.decompose import ClaimDraft, UnitHeld
from src.ingest.gates import (
    claim_floor,
    every_claim_once,
    lenses_valid,
    resolve_place,
    story_problems,
)
from src.ingest.unit import Unit
from src.schema.definitions import TAGGABLE_LENSES

#: Output tokens requested for a P2 call — both the first ask and the
#: re-ask use the same cap.
P2_MAX_TOKENS: int = 64_000  # thinking-inclusive; unbatched, so a truncation is unrecoverable

#: What the estimate prices per P2 call — a PROJECTION until a run
#: measures it: ~44 stories x ~250 tokens of JSON plus thinking at ~1x.
P2_EXPECTED_OUTPUT_TOKENS: int = 1_500  # job 1: P2+P4 sync output 24,503 over 27 calls

#: The exact keys `prompts.P2_RESPONSE_SCHEMA` allows on one story item,
#: and on that story's nested `enrichment` object.
_REQUIRED_STORY_KEYS: frozenset[str] = frozenset(
    {"title", "place", "beat_type", "lenses", "claim_ids", "enrichment"}
)
_REQUIRED_ENRICHMENT_KEYS: frozenset[str] = frozenset(
    {
        "physical_cues",
        "entities",
        "narrative_function",
        "emotional_register",
        "sensory_anchor",
        "inline_foreign_phrases",
        "pronunciation",
        "kid_friendly",
        "sub_location",
        "trigger_address",
    }
)

#: The schema's own `kid_friendly` enum — wider than the domain type
#: (`Literal["yes", "no"] | None`) because the structured-output subset
#: forbids `null`, so `'unknown'` is the schema's stand-in for "not
#: answered", mapped to `None` by `Enrichment` below.
_KID_FRIENDLY_RESPONSE_VALUES: tuple[str, ...] = ("yes", "no", "unknown")

#: A loose "outermost JSON object" match used only to strip prose a model
#: wrapped around its answer; the real validation is `_valid_story_item`
#: and the key checks in `parse_stories`, not this regex.
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.S)


class Enrichment(BaseModel):
    """The ten free-text/optional beat-enrichment fields, identical in
    shape and default to `model.Beat`'s own fields.

    The P2 response schema requires every one of these as a concrete
    string/bool/array (no `null` in the structured-output subset), so the
    validators below map the schema's forced-string sentinels back to the
    real `None` domain value: `''` for every optional free-text field, and
    `'unknown'` (alongside `''`, for the same reason) for `kid_friendly`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    physical_cues: list[str] = []
    entities: list[str] = []
    narrative_function: str | None = None
    emotional_register: str | None = None
    sensory_anchor: bool = False
    inline_foreign_phrases: list[str] = []
    pronunciation: str | None = None
    kid_friendly: Literal["yes", "no"] | None = None
    sub_location: str | None = None
    trigger_address: str | None = None

    @field_validator(
        "narrative_function",
        "emotional_register",
        "pronunciation",
        "sub_location",
        "trigger_address",
        mode="before",
    )
    @classmethod
    def _blank_string_to_none(cls, v: Any) -> Any:
        if isinstance(v, str) and v == "":
            return None
        return v

    @field_validator("kid_friendly", mode="before")
    @classmethod
    def _kid_friendly_sentinel_to_none(cls, v: Any) -> Any:
        if v in ("", "unknown"):
            return None
        return v


class Story(BaseModel):
    """One story: a single arc anchored to a single place.

    `story_slug` is always computed from the raw title via `model.slug`
    (see `_story_from_raw`) — the P2 response schema does not even offer
    the model a slug field, since story_slug is a structural join key,
    never something worth asking the model to invent. `new_poi` records
    whether `place` is a place `gates.resolve_place` had never seen
    before (step 14).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    story_slug: str
    place: str
    new_poi: bool
    lenses: list[str]
    beat_type: str
    enrichment: Enrichment
    claim_ids: list[str]

    @field_validator("beat_type")
    @classmethod
    def _check_beat_type(cls, v: str) -> str:
        if v not in model.BEAT_TYPE_VALUES:
            raise ValueError(f"beat_type must be one of {model.BEAT_TYPE_VALUES}, got {v!r}")
        return v

    @field_validator("lenses")
    @classmethod
    def _check_lenses(cls, v: list[str]) -> list[str]:
        # Same rule as model.Beat.lenses: non-empty, no duplicates, every
        # entry a real TAGGABLE_LENSES value.
        if not v:
            raise ValueError("lenses must include at least one value")
        if len(set(v)) != len(v):
            raise ValueError("lenses must not contain duplicates")
        invalid = [lens for lens in v if lens not in TAGGABLE_LENSES]
        if invalid:
            raise ValueError(f"lenses outside TAGGABLE_LENSES: {invalid}")
        return v


#: The P2 cost-estimate plan: one row per attempt (first ask, re-ask),
#: each priced against that attempt's own real prompt length rather than a
#: shared guess, exactly like P1_PLAN — the first ask never carries the
#: re-ask's `{problems}` quoting, so its overhead is genuinely smaller.
P2_PLAN: tuple[llm.PhaseCall, ...] = (
    llm.PhaseCall(
        phase="P2",
        role="author",
        calls_per_unit=1,
        overhead_tokens=max(1, len(prompts.GROUP_PROMPT_TEMPLATE) // 4),
        expected_output_tokens=P2_EXPECTED_OUTPUT_TOKENS,
    ),
    llm.PhaseCall(
        phase="P2",
        role="author",
        calls_per_unit=1,
        overhead_tokens=max(1, len(prompts.GROUP_REDO_TEMPLATE) // 4),
        expected_output_tokens=P2_EXPECTED_OUTPUT_TOKENS,
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


def _valid_enrichment_item(item: Any) -> bool:
    """One `enrichment` object matches `prompts.P2_RESPONSE_SCHEMA`'s
    nested enrichment shape exactly."""
    if not isinstance(item, dict):
        return False
    if set(item.keys()) != _REQUIRED_ENRICHMENT_KEYS:
        return False
    for key in ("physical_cues", "entities", "inline_foreign_phrases"):
        value = item[key]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            return False
    for key in ("narrative_function", "emotional_register", "pronunciation"):
        if not isinstance(item[key], str):
            return False
    if not isinstance(item["sensory_anchor"], bool):
        return False
    if item["kid_friendly"] not in _KID_FRIENDLY_RESPONSE_VALUES:
        return False
    return isinstance(item["sub_location"], str) and isinstance(item["trigger_address"], str)


def _valid_story_item(item: Any) -> bool:
    """One `stories` item matches `prompts.P2_RESPONSE_SCHEMA` exactly."""
    if not isinstance(item, dict):
        return False
    if set(item.keys()) != _REQUIRED_STORY_KEYS:
        return False
    if not isinstance(item["title"], str) or not isinstance(item["place"], str):
        return False
    if item["beat_type"] not in model.BEAT_TYPE_VALUES:
        return False
    lenses = item["lenses"]
    if not isinstance(lenses, list) or not all(
        isinstance(lens, str) and lens in TAGGABLE_LENSES for lens in lenses
    ):
        return False
    claim_ids = item["claim_ids"]
    if not isinstance(claim_ids, list) or not all(isinstance(c, str) for c in claim_ids):
        return False
    return _valid_enrichment_item(item["enrichment"])


def parse_stories(text: str) -> list[dict] | None:
    """Turn one raw P2 answer into its list of story dicts, or None.

    None for: unparseable text, a top-level payload that is not a dict
    with exactly the key `stories`, a `stories` value that is not a
    non-empty list, or any item that fails `_valid_story_item` — a single
    bad item refuses the WHOLE answer, never a partial list of only the
    good stories.
    """
    parsed = _extract_json_object(text)
    if not isinstance(parsed, dict):
        return None
    if set(parsed.keys()) != {"stories"}:
        return None
    stories = parsed["stories"]
    if not isinstance(stories, list) or not stories:
        return None
    if not all(_valid_story_item(item) for item in stories):
        return None
    return stories


def _story_from_raw(raw: dict, *, place: str, new_poi: bool) -> Story:
    """Build one Story from a raw (already `_valid_story_item`-checked)
    story dict plus its resolved place.

    `story_slug` is always `model.slug(raw['title'])` — never a slug the
    model answered with, since the response schema does not offer one.
    `place`/`new_poi` come from the caller (step 14's `gates.resolve_place`
    call), never from the raw dict itself.
    """
    return Story(
        title=raw["title"],
        story_slug=model.slug(raw["title"]),
        place=place,
        new_poi=new_poi,
        lenses=list(raw["lenses"]),
        beat_type=raw["beat_type"],
        enrichment=Enrichment(**raw["enrichment"]),
        claim_ids=list(raw["claim_ids"]),
    )


def _drop_second_failure(
    raw_stories: list[dict],
    claim_ids: list[str],
    unit: Unit,
    emit,
) -> list[dict]:
    """Prune a still-failing attempt-2 answer in place — step 16.

    Never patches a story or moves a claim between stories; only drops.
    Every claim that ends up in none of the returned stories is
    `claim_dropped` exactly once, whatever the reason. Runs, in order:

    1. Any story with a per-story defect independent of claim membership
       (`gates.lenses_valid`) is dropped whole (`story_dropped`).
    2. `gates.every_claim_once` over the SURVIVING stories' claim lists:
       a claim in none of them (orphaned — including one whose only story
       was just dropped in step 1) or in more than one (double-booked) is
       `claim_dropped`; a double-booked claim is stripped from every story
       that named it.
    3. Any story that thereby falls under `gates.claim_floor` is dropped
       too (`story_dropped`, `too_few_claims`) — and every claim still in
       it is also `claim_dropped`, so nothing vanishes unlogged.
    """
    already_dropped: set[str] = set()

    def drop_claim(claim_id: str, reason: str) -> None:
        if claim_id in already_dropped:
            return
        already_dropped.add(claim_id)
        emit("claim_dropped", {"unit_key": unit.key, "claim_id": claim_id, "reason": reason})

    surviving: list[dict] = []
    for raw in raw_stories:
        slug = model.slug(raw["title"])
        reason = lenses_valid(raw["lenses"])
        if reason is not None:
            emit("story_dropped", {"unit_key": unit.key, "story_slug": slug, "reason": reason})
            continue
        surviving.append(raw)

    story_claim_ids = [list(raw["claim_ids"]) for raw in surviving]
    double_booked_ids: set[str] = set()
    for reason in every_claim_once(story_claim_ids, claim_ids):
        code, claim_id, _detail = reason.split(":", 2)
        drop_claim(claim_id, reason)
        if code == "double_booked":
            double_booked_ids.add(claim_id)

    if double_booked_ids:
        surviving = [
            {**raw, "claim_ids": [c for c in raw["claim_ids"] if c not in double_booked_ids]}
            for raw in surviving
        ]

    final: list[dict] = []
    for raw in surviving:
        slug = model.slug(raw["title"])
        reason = claim_floor(raw["beat_type"], len(raw["claim_ids"]))
        if reason is not None:
            emit("story_dropped", {"unit_key": unit.key, "story_slug": slug, "reason": reason})
            for claim_id in raw["claim_ids"]:
                drop_claim(claim_id, f"story_dropped:{slug}: {reason}")
            continue
        final.append(raw)
    return final


def group(
    claims: list[ClaimDraft],
    unit: Unit,
    pois: Sequence[Mapping[str, object]],
    client: llm.ModelClient,
    *,
    events: llm.EventSink | None = None,
) -> list[Story]:
    """Turn one Unit's ClaimDraft list into a list of Story via P2.

    An empty `claims` list returns `[]` immediately — no prompt is
    rendered, no tie-break is checked, and the client is never touched.

    Otherwise renders `prompts.render_group` over every claim's
    `(claim_id, text)` pair (never the poi list — D7) and makes exactly
    one `client.complete()` call (never `complete_batch`; P2 is a sync
    phase). `render_group` itself raises `prompts.TieBreakMissing` when
    the resolved tie-break rule is empty or blank — checked before any
    call, since the prompt is built before the client is ever touched.

    Each raw story in the answer is resolved against `pois` via
    `gates.resolve_place`: a match keeps the POI's canonical name with
    `new_poi=False`; no match keeps the place as given, flagged
    `new_poi=True`, and emits `place_unresolved` — the story itself is not
    refused for it.

    Never calls `client.estimate()` — an unarmed client's `complete()`
    raises `llm.EstimateNotPrinted` here, uncaught.

    An unreadable answer (`parse_stories` returns None) emits
    `stories_refused` with a reason starting `'schema:'`; a readable one is
    graded by `gates.story_problems` against this unit's own claim ids, and
    any problem emits `stories_refused` carrying every reason. Either
    refusal on attempt 1 buys exactly ONE re-ask, rendered by
    `prompts.render_group_redo` with those reasons quoted back verbatim.

    Attempt 2 is never re-asked a third time. A still-unreadable attempt-2
    answer emits its own `stories_refused` then `unit_held` and raises
    `UnitHeld` (`.phase == 'P2'`). A readable attempt-2 answer that still
    fails `gates.story_problems` is pruned by `_drop_second_failure`
    instead — bad-lens stories, double-booked claims, and any story that
    falls under its claim floor once a double-booked claim is stripped are
    all dropped and logged, never patched or moved.

    A transport failure — `llm.EmptyCompletion`/`llm.TruncatedCompletion`
    raised by `client.complete()`, or a raw (non-`llm.LlmError`)
    `ValueError` from a duck-typed transport — emits `unit_held` and raises
    `UnitHeld` immediately, with NO second call, on either attempt. Any
    other `llm.LlmError` (including `MockScriptExhausted`) propagates as
    itself, uncaught.
    """

    def emit(kind: str, payload: dict) -> None:
        if events is not None:
            events(kind, payload)

    if not claims:
        return []

    claim_pairs = [(claim.claim_id, claim.text) for claim in claims]
    claim_ids = [claim.claim_id for claim in claims]

    reasons: list[str] | None = None
    raw_stories: list[dict] | None = None
    for attempt in (1, 2):
        prompt = (
            prompts.render_group(claim_pairs)
            if attempt == 1
            else prompts.render_group_redo(claim_pairs, reasons)
        )
        try:
            completion = client.complete(
                role="author",
                prompt=prompt,
                schema=prompts.P2_RESPONSE_SCHEMA,
                phase="P2",
                max_tokens=P2_MAX_TOKENS,
            )
        except (llm.EmptyCompletion, llm.TruncatedCompletion) as exc:
            reason = f"transport: {exc}"
            emit("unit_held", {"unit_key": unit.key, "phase": "P2", "reason": reason})
            raise UnitHeld(unit.key, "P2", reason) from exc
        except llm.LlmError:
            # A programming error (EstimateNotPrinted, MockScriptExhausted,
            # JudgeIsAuthor, ...) is not a transport failure — propagate it
            # untouched, same as decompose() always has.
            raise
        except ValueError as exc:
            # A raw ValueError (not an llm.LlmError) from a duck-typed
            # transport is still a transport failure, not a programming
            # error.
            reason = f"transport: {exc}"
            emit("unit_held", {"unit_key": unit.key, "phase": "P2", "reason": reason})
            raise UnitHeld(unit.key, "P2", reason) from exc

        raw_stories = parse_stories(completion.text)

        if raw_stories is None:
            reason = "schema: the answer was not valid JSON matching the P2 stories schema"
            emit(
                "stories_refused",
                {"unit_key": unit.key, "attempt": attempt, "reasons": [reason]},
            )
            if attempt == 2:
                emit("unit_held", {"unit_key": unit.key, "phase": "P2", "reason": reason})
                raise UnitHeld(unit.key, "P2", reason)
            reasons = [reason]
            continue

        problems = story_problems(raw_stories, claim_ids)
        if problems and attempt == 1:
            emit("stories_refused", {"unit_key": unit.key, "attempt": 1, "reasons": problems})
            reasons = problems
            continue
        if problems:
            raw_stories = _drop_second_failure(raw_stories, claim_ids, unit, emit)
        break

    stories: list[Story] = []
    for raw in raw_stories or []:
        place, new_poi = resolve_place(raw["place"], pois)
        story = _story_from_raw(raw, place=place, new_poi=new_poi)
        if new_poi:
            emit(
                "place_unresolved",
                {"unit_key": unit.key, "story_slug": story.story_slug, "place": place},
            )
        stories.append(story)
    return stories
