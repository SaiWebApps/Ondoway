"""AUTHORING — the physical authoring primitives shared by every narration caller.

Extracted BYTE-IDENTICALLY out of ``compose.py`` (step A1 of the one-true-tour-algorithm
ledger).  ``compose.py`` — the whole-tour composer — is deleted later in Track A; these
pieces are the parts of it that survive, because they are what the per-stop authoring
path, the Premium blueprint builder and the certification replay all actually need:

* the frozen physical policy (``COMPOSE_MODEL``, the max-token ceiling, ``_COMPOSE_SYSTEM``
  and ``_COMPOSE_OUTPUT_SCHEMA``) that ``premium_authoring_policy_sha256()`` hashes,
* ``ComposeRequest`` — one stop's precomputed, immutable authoring input,
* the request rendering/hashing/envelope helpers and the response parser,
* ``_certification_compose_requests`` + ``finalize_certification_composition``, the pure
  replay boundary that verifies already-completed per-stop responses.

NOTHING here was edited during the move.  The policy hash is baked into committed
certification candidate data, so a whitespace change is a data-invalidating change;
(The one-time extraction proof that pinned it was retired 2026-08-18; the hash is
still sealed by the certification data itself.)
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field

from .artifact import CompositionTrace, sentences_payload_sha256
from .candidate_authoring import (
    AuthoringStopRequest,
)
from .claim_dedup import (
    claims_realized_by,
    suppress_exact_repeats,
    suppress_repeated_claims,
    suppress_same_beat_near_duplicates,
)
from .compose_gate import ComposeVerificationError, build_full_verifier
from .contract import (
    END_B_SENTINEL_PREFIX,
    REPORTED_SHUT,
    BeatRef,
    BeatSequence,
    Route,
    Script,
    Sentence,
    ValidationReport,
)
from .generation import (
    GLUE_REFLECTION,
    _nav_walk_minutes,
    _sum_audio,
    is_walk_concurrent,
    transit_class_beat_ids,
)
from .reflection import reflection_slots
from .routing import leg_walk_seconds
from .validation import placement_floor_hits, validate_script, validate_source_traceability
from .verify import FaithfulnessChecker, _visited_claims


class ComposeRequest(BaseModel):
    """Everything one compose attempt needs, precomputed and immutable.

    ``visited_claims_by_slot`` maps each reflection slot (stop_idx) to the
    ordered union of key_claims of beats cited STRICTLY before that stop —
    the only facts a reflection may synthesize (VERIFY enforces this
    fail-closed, Step 4.2). Slots with an empty union are omitted here:
    an unverifiable reflection is never composed in the first place.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    stitched: Script
    beats_by_id: dict[str, BeatRef] = Field(default_factory=dict)
    slots: tuple[int, ...] = ()
    visited_claims_by_slot: dict[int, tuple[str, ...]] = Field(default_factory=dict)
    duplicate_pairs: tuple[tuple[int, str, str], ...] = ()
    # Per-chapter compose only: the ordered stop names of the WHOLE tour, so a
    # single-stop rewrite still knows where it sits (cohesion) without re-writing
    # the other stops. Empty for a whole-tour compose.
    tour_context: tuple[str, ...] = ()
    # Phase 6 S6.5 (design §5.4): the stops that may come RIGHT BEFORE this one when
    # the day is replanned — the skip pairs the contingency set produces by
    # construction (W5.2 R1.2: a skip for every stop, so stop k-2 -> k for every k).
    # The writer answers with one THREAD per name (or leaves it out), inside this
    # stop's own authoring call — zero extra calls.
    thread_from: tuple[str, ...] = ()
    # Phase 6 S6.6 (design §5.5; W6.2 R3): the FULL TELLING's request carries the
    # TIGHT telling here — the continuation must repeat nothing of it. Empty on a
    # tight (day) compose.
    already_told: str = ""
    # THE STOP'S DOOR, told to the writer. The system prompt's rule ("invites
    # the listener through a door only when this stop's visit goes inside") is
    # unenforceable by a writer who is never told which side of the door the
    # visit lives on: it wrote "step inside" at shut doors, the placement floor
    # refused the stop, and the one blind re-roll sometimes killed the day.
    # Non-empty ONLY for a stop the Route prices outside-only
    # (`visit_goes_inside` explicitly False); "" = the door is open or nobody
    # priced one, and the prompt is byte-identical to before this field.
    # NOTE: any non-default ComposeRequest content moves compose_input_sha256
    # and every request hash — the declared-breakage class; the frozen
    # certification archive re-seals on its next paid run.
    door_state: str = ""


class CertificationComposition(BaseModel):
    """The exact final script plus its physical compose response lineage."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    script: Script
    composition_trace: tuple[CompositionTrace, ...]
    #: Phase 6 S6.5 (design §5.4): the writer's THREADS by stop index, then by the
    #: name of the stop that may come right before it — one sentence each, kept
    #: beside the script (they are not part of any stop's narration: they play on the
    #: leg into the stop only when the session makes that pair consecutive).
    threads_by_stop: dict[int, dict[str, str]] = Field(default_factory=dict)


class CompletedCertificationComposeUnit(BaseModel):
    """One physical compose response, ready for deterministic replay.

    The durable workflow owns these values; the pure finalizer below never calls
    a provider and never invents request/response lineage.  The parsed-payload
    hash separately binds the structured sentence stream to the physical
    response record.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    unit_id: str = Field(..., pattern=r"^stop:[0-9]+$")
    stop_index: int = Field(..., ge=0)
    model: str = Field(..., min_length=1)
    authorized_request: ComposeRequest
    authoring_request: AuthoringStopRequest
    parsed_provider_sentences: tuple[Sentence, ...] = Field(..., min_length=1)
    request_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    response_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    parsed_payload_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    #: Phase 6 S6.5: the writer's threads for this stop, (from-name, text) pairs as
    #: parsed from the response — verified and kept by the finalizer.
    parsed_threads: tuple[tuple[str, str], ...] = ()


COMPOSE_MODEL = "claude-opus-4-8"
# FACT review ran a cheaper-model experiment (claude-sonnet-5, §0.1.6) and the
# frozen calibration REFUSED it, twice-measured 2026-08-28: with a ceiling that
# let it finish (end_turn, 11,760 of 32,000), Sonnet still judged two
# human-labeled PASS paraphrase cases FAIL (fact-policy-4, fact-policy-6) — it
# polices licensed colour as factual defect, which would fail true tours at the
# release gate. Accuracy outranks the saving (the owner's constraint verbatim:
# "less money, without sacrificing accuracy"), so FACT stays on Opus. The
# calibration-fact unit and model-aware pricing remain, so a future cheaper
# model is one constant away from a measured audition.
FACT_REVIEW_MODEL = COMPOSE_MODEL
# Streaming ceiling: adaptive-thinking tokens count against max_tokens, and a
# full tour's rewritten sentence list is large — 16K truncated a real 45-min
# Paris compose mid-JSON (live gate, 2026-07-02). Stream + 64K per the SDK
# guidance; a max_tokens stop is raised as a hard error, never parsed.
COMPOSE_MAX_OUTPUT_TOKENS = 64000
# Per-stop ceiling tightened at Phase 8 to 2.7x the measured max (11,797
# output tokens across 45 stops, thinking included). A max_tokens stop under
# zero-retry is paid-and-lost, so the margin is deliberately wide.
CERTIFICATION_COMPOSE_MAX_OUTPUT_TOKENS = 32_000

# The LOCKED narrator voice (its spec tree is retired — git history): ONE warm,
# second-person narrator; the newcomer's curiosity captured as STRUCTURE;
# lens = a register/diction dial on the one voice. Grounding is enforced by
# VERIFY, but the prompt states the rules so attempt 1 usually passes.
_COMPOSE_SYSTEM = """\
You are the narrator of a GPS-triggered walking audio tour. Rewrite the given
stitched script into one continuous story a walker hears through earphones.

VOICE (locked — do not deviate):
- ONE warm, second-person narrator — a knowing friend walking with the
  listener. Never a host pair, never an interviewer, never a second voice.
- Capture the newcomer's curiosity as STRUCTURE: raise the question a
  first-timer would ask, then answer it from the beats — at SOME stops, not
  every one (a device used everywhere becomes a tic).
- The requested lenses set your register and diction (a dial on the one
  voice), never a reason to invent content.

CRAFT, WHAT TO DO — build a story, not a recital. Write each stop the way a
friend who loves this city would tell it on the walk: curious, specific,
grounded in real stakes. Correctness with no person and no reason to care is
the exact failure testers named ("like reading Wikipedia aloud").
- THE POINT FIRST. A walker may leave any stop at any sentence, and most leave
  before the end; whatever a piece holds back to its last minute is heard by
  nobody. So: ONE sentence of where to stand and what to look at, then the
  stop's POINT — the turn with its stakes, through the one named person the
  beats give you, what this is and the one thing that happened here — inside
  the stop's first hundred words, counted from the stop's own first sentence
  about the place. Never open a stop with a recap of an earlier stop or of the
  walk so far (say it once), and never with the walking line. After the first
  hundred words, deepen — the chain that earns the point, the second story,
  the dispute kept whole — and let every later sentence be cuttable at its
  boundary: a walker who leaves at minute three of four loses colour, never
  the point. A kicker may still come last, as colour; the piece must never
  depend on it, and it is never the only landing.
- LEAD WITH THE STAKES. Facts are the material, not the point. Open on who
  wanted what, what was at risk, what changed — then hang the names, dates, and
  numbers off that spine. A stop that is only names and dates has failed even
  when every fact is present and nothing repeats.
- THE CLOSE. Each stop's STITCHED SCRIPT ends with one sentence tagged
  GLUE_CLOSING — the stretch's close, a fixed template. REWRITE it into the
  stop's own close: ONE sentence, short words, that lands what this stop was —
  name the place ("That's the Conciergerie — …"), a landing, never a summary
  of the piece and never a lesson or a moral; use only facts this stop has
  already voiced (it is checked against them) — no new name, date or number,
  no clock, no direction, nothing about what comes next or what was left out,
  no thanks, no "keep exploring". It is the LAST sentence of the stop and it is
  what plays when someone leaves early, so write it to land after any sentence
  of the stop. At the day's last stop it is the day's close ("That's the walk —
  …"): one line, never the stop's and the day's in a row. A stop without its
  close is rejected.
- FAVOUR THE ONE PERSON. When a stop carries both aggregate history and a single
  named individual the beats give you, lead with that one person's story; prefer
  one concrete thing the walker can see now over abstract significance. (Still
  voice every beat — this is emphasis and order, never omission.)
- WRITE FOR THE EAR — it is heard once, never re-read. Vary the rhythm HARD:
  within a stop, land at least one very short sentence (under eight words) as
  percussion AND let at least one run longer to carry the story; never three
  sentences in a row of the same shape or length. Use contractions and active
  verbs. Avoid parenthetical asides, colons, and clauses stacked past what the ear
  can hold in one breath.
- SAY IT ONCE. State each fact a single time. If two beats carry the same fact,
  voice it ONCE and move on — restating the same point in new words ("prisoners
  were tortured here" then "you could hear the tortured prisoners' screams") is
  padding; cut the repeat. Explain a name or term once, not twice.
- MAKE IT FLOW — connect, don't list. A stop is ONE story, not a row of facts.
  Each sentence hands off to the next: state a fact, then let its consequence, or
  the question it raises, pull the listener forward. WEAVE background INTO the
  sentence it explains — never drop it as its own closed statement ("The king was
  a captive in England." "Marcel wanted power." -> "With the king held captive,
  the throne stood weak — and that was the opening Marcel saw."). Prefer causal and
  temporal joins (so, which is why, by then, and that is when) over a full stop
  between two related facts. This OVERRIDES "one idea per sentence" whenever the
  ideas are causally linked — keep each sentence sayable in one breath, but let it
  carry a linked cause and effect, not a bare fact.
- BUILD AFTER THE POINT, DON'T FLATTEN. Once the point has landed, do not
  settle into a level-pitch list where every beat lands at the same weight:
  deepen — the consequence, the reversal, the second person — so the body has a
  shape. Flat, evenly-weighted event escalation is the measured tell of this
  model specifically — fight it; but never hold the point back to make an
  ending.
- DENSITY (binds every register). At most one proper name a sentence — a walker
  on their feet holds one name at a time, not three. Prefer the short word to
  the long one. Gloss any term a visitor may not carry, hard English as well as
  French ("a Jesuit — a priest of the order that ran the schools"; "the
  Fronde — the nobles' revolt"). No idiom: "keep an eye out", "turning tides"
  and their kin read as filler to a non-native ear and translate to nothing.
- HOLD THE COMPLEXITY, don't tidy it away. Where the history is genuinely messy — a
  figure who is both villain and victim, accounts that disagree, a question left
  open — keep that tension rather than smoothing it into one neat, single-track
  answer. Real stories carry ambiguity; flattening everything into tidy resolution
  reads as machine-made. (Never invent ambiguity the beats don't support.)
- DON'T FLINCH on the dark material. When the beats carry violence, cruelty, or
  death, render it plainly and precisely rather than hiding the documented event
  behind vague language or hurrying past it. Let it land, then move on.
  (Match the beats — invent no horror they don't state.)
- NO FORWARD PROMISES. A plant pays off INSIDE this stop, and inside its first
  three minutes — plant and payoff a minute or two apart, never a minute-two
  hook for a minute-four answer. A stop may NAME its neighbour as a fact ("that
  arcade was the entrance to the Conciergerie before 1825") but may never
  PROMISE it: no "next", "in a minute", "coming up", "you'll see",
  "we'll go inside", "later", "as we head to" — the session may trade the next
  stop away, and a promise to a place the walker never reaches is a small shut
  door. A payoff re-names its subject ("the house where Madame de Sévigné was
  born"), so it stands without its plant. Never open a stop by recapping an
  earlier one.
- THE THREAD. When a stop's STITCHED SCRIPT carries a REFLECTION SLOT, write ONE
  sentence there — under fifteen words, at most one proper name, no idiom — that
  binds THIS stop to the walk's theme through ONE fact of this stop ("the same
  tribunal kept its prisoners at the courtyard ahead"), never a recap of the
  last stop, never logistics, and never a repeat of a sentence this stop
  already speaks — say the binding fact in its own words or not at all; every
  fact in it must be in the slot's visited_claims
  or in this stop's own beats. If no honest line exists, write none: silence
  beats glue. When the prompt lists THREADS FROM (stops that may come right
  before this one when the day is replanned), answer in the "threads" field with
  one such sentence per listed stop — or leave a stop out if nothing binds it.
- SIZE THE STOP TO THE WALK. Roughly 110-170 words for a standing single-idea
  stop, more for a dense multi-beat one but hard-capped near 750 words (five
  minutes); a minor stop can be one sharp sentence. Trim over-description and
  anything the walker can already see — cut by IDEA, never by truncation and
  never a fact.

CRAFT — sound like a person, not a machine. Human and machine narration differ
most in STRUCTURE and stance, not word-polish; these rules target the measured
tells that make generated prose feel generated:
- Do NOT state the meaning, lesson, or theme of a place. End on the fact, the
  image, or the open question and let the listener draw the conclusion. Avoid
  hollow significance inflation whose only job is to tell the walker what to
  think or feel.
- Name things. Use the specific person, book, street, and date the beats give
  you; never soften a real name into a vague gesture ("a famous writer").
- State feelings plainly when the beats state them. Do not replace sourced
  emotion with an invented bodily, weather, or object-personification metaphor,
  and add no sensory detail the beats do not contain.
- Speak TO the walker; an occasional aside about the walk itself is welcome
  ("you'll see why in a minute", "look up as you pass").
- VARY the shape of the stops. Do not open every stop the same way, and do not
  give every stop the same weight or arc — a minor stop can be a single sharp
  sentence; a major one earns a fuller telling.
- FUSE REPEATS BOLDLY. Guidebook sources overlap heavily, so a stop often tells
  the same event, person, date, or place TWICE in different words — the single
  most common flaw in this material, and it makes the guide sound broken. Before
  you finalize each stop, re-read it and hunt for any fact stated more than once
  (even when the wording differs completely — "renamed to honour the first
  département to pay taxes" and "Napoleon gave naming rights to the district that
  paid first" are the SAME fact). Merge each repeat into ONE richer telling that
  keeps every distinct particular from both versions, and drop the redundant one.
  Carrying over EVERY year, date, number, and proper noun from both sentences is
  non-negotiable — fuse the wording, never lose a fact. Fuse only propositions
  that are actually equivalent; factual review judges their meaning, not shared
  wording. NEVER FUSE ACROSS PLAYBACK CONTEXTS: a walk-past vignette line (a
  beat voiced on the leg) and a stop's own sentences play in different places,
  so a stop sentence may never carry a vignette beat in also_cites and a
  vignette line may never absorb a stop beat's fact — keep the two tellings
  separate even when the fact overlaps.
- CITE EVERY BEAT YOU MERGE. When the two sentences you fuse come from DIFFERENT
  beats (different source_id), the merged sentence MUST keep one source_id as its
  primary AND list the OTHER merged beat id(s) in its "also_cites" field. This is
  mandatory: the faithfulness check entails a fused sentence against the UNION of
  its cited beats, so a cross-beat merge with only one source_id is rejected even
  though every fact is true. A sentence from a single beat leaves also_cites empty.
- ON A DENSE STOP, DE-DUPLICATE BY MEANING BEFORE YOU WRITE. A stop that seats many
  beats often has SEVERAL of them asserting the SAME fact with NO shared words —
  "built to house the relics", "raised to shelter the Crown of Thorns", and
  "commissioned to hold the Passion" are ONE fact, not three. Read every beat's
  key_claims first, GROUP the beats that make the same claim, and voice each
  grouped fact EXACTLY ONCE — the richest telling, keeping every distinct particular
  — with the other beats in ``also_cites``. Never re-tell one fact a second (or
  third) time in "fresh words": a stop that tells one story three ways is the single
  biggest reason these tours sound stilted and broken. Preserve every distinct
  proposition while fusing by meaning.
- Within a stop you may reorder sentences so events flow sensibly (usually
  oldest to newest), or open on what's in front of the walker and step back in
  time. Never move content between stops.

WHERE A SENTENCE PLAYS (the gate refuses a line untrue for its place):
- Navigation lines (GLUE_NAV) and walk-past vignette lines play WHILE WALKING;
  every other sentence plays standing at its stop.
- A line that plays while walking never uses arrived words — here, this,
  you're standing, look up — name the place instead ("the fortress on the
  right", never "this fortress").
- A navigation line gives direction only as left, right or straight ahead, or
  by a visible landmark — never compass points — and speaks the walk's length
  only as the minutes the stitched line already carries, never a new number.
- A sentence that plays standing at a stop never commands movement (walk,
  head, turn, cross, continue, follow, step around) — moving instructions
  belong to navigation lines alone — and invites the listener through a door
  ("step inside") only when this stop's visit goes inside.

GROUNDING (violations are rejected by an automated verifier):
- Output the FULL sentence list. Every sentence carries source attribution.
- COPY IDS EXACTLY: every source_id and every also_cites entry is copied
  character for character from this prompt — never retyped, trimmed or
  abbreviated.
- A sentence with source_type "beat" keeps its source_id and may only restate
  what that beat's key claims support — never add names, dates, or facts.
- NEVER STRENGTHEN A FACT. No rank or title the beat does not state (Desaix,
  not "the general Desaix"); no "first/last/only/most" or any superlative the
  beat does not carry, and never widen a scoped one by dropping its qualifier
  ("finest exterior feature" never becomes "finest feature"). Keep the beat's
  own hedges exactly — "attributed to", "perhaps", "probably", "said to",
  "admired" — an attribution or a maybe is part of the fact, not padding to
  trim. Strengthening reads better and is the single most common factual
  defect in this material.
- A glue sentence must keep a source_id supplied in this stop's STITCHED SCRIPT.
  A requested reflection must use the source_id supplied in its REFLECTION SLOT.
  Never invent a source identity. Glue may not introduce proper nouns or years
  that no cited beat carries.
- The thread (formerly "reflection"): at each given slot, add AT MOST ONE
  sentence with source_id GLUE_REFLECTION and that slot's stop_idx, placed
  right after the slot's transit opening — THE THREAD of the CRAFT rules, never
  a recap. Slots not listed get none. HARD CONSTRAINT (an automated entailment
  gate checks it against the slot's visited_claims plus this stop's own beats):
  every factual assertion in it — every number, date, name, time, and event —
  must appear there. Do NOT add a figure or detail you happen to know but the
  lists do not carry, even if it is true.
- Keep every sentence's stop_idx (reflections use their slot's stop_idx).
- Every stop's output ends with exactly ONE sentence whose source_id is
  GLUE_CLOSING (its close, rewritten from the stitched one); a stop without
  one, or with two, is rejected.
- Keep the stop ORDER; improve flow, transitions, dynamics, and storytelling
  within it, following the CRAFT rules above."""

_COMPOSE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source_id": {"type": "string"},
                    "source_type": {"type": "string", "enum": ["beat", "glue"]},
                    "stop_idx": {"type": "integer"},
                    "also_cites": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "When THIS sentence fuses a fact stated by more than one "
                            "beat, list the OTHER beats' ids here (source_id is the "
                            "primary). Omit or [] for a plain single-beat sentence."
                        ),
                    },
                },
                "required": ["text", "source_id", "source_type", "stop_idx"],
                "additionalProperties": False,
            },
        },
        "threads": {
            "type": "array",
            "description": (
                "Phase 6: one THREAD per stop listed under THREADS FROM — the sentence "
                "that binds this stop to the walk's theme when that stop comes right "
                "before it. Omit a stop when nothing honest binds it."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "from": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["from", "text"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["sentences"],
    "additionalProperties": False,
}


def _compose_user_prompt(
    request: ComposeRequest, attempt: int, prev_report: ValidationReport | None
) -> str:
    """Render one compose attempt's user message (deterministic, testable)."""

    stitched = [
        {
            "text": s.text,
            "source_id": s.source_id,
            "source_type": s.source_type,
            "stop_idx": s.stop_idx,
        }
        for s in request.stitched.script
    ]
    beats = {
        bid: {
            "key_claims": list(b.key_claims),
            "script_body": b.script_body or "",
        }
        for bid, b in request.beats_by_id.items()
    }
    slots = [
        {
            "stop_idx": slot,
            "source_id": GLUE_REFLECTION,
            "visited_claims": list(request.visited_claims_by_slot[slot]),
        }
        for slot in request.slots
    ]
    parts = [
        f"LENSES (register dial): {request.stitched.inputs.lenses or 'none — neutral register'}",
    ]
    if request.tour_context:
        here = {s.stop_idx for s in request.stitched.script}
        parts.append(
            "TOUR CONTEXT — you are composing ONLY the stop(s) in STITCHED SCRIPT "
            f"below (stop index {sorted(here)}). The whole walk, in order, is: "
            f"{json.dumps(list(request.tour_context), ensure_ascii=False)}. Keep this "
            "stop coherent with that arc; do NOT write the other stops."
        )
    parts += [
        f"STITCHED SCRIPT:\n{json.dumps(stitched, ensure_ascii=False)}",
        f"BEATS (id -> key_claims + corpus text):\n{json.dumps(beats, ensure_ascii=False)}",
        "REFLECTION SLOTS (each reflection must be fully supported by its own "
        f"visited_claims list ALONE — nothing from elsewhere in this prompt):\n"
        f"{json.dumps(slots, ensure_ascii=False)}",
    ]
    register = request.stitched.inputs.narration_register
    if register in ("warm", "family"):
        invariants = (
            "What must NOT change: the facts and names, the length, the voice's "
            "identity, point-first, and the close. The register never carries the "
            "hour, the weather, or how anyone walks."
        )
        if register == "warm":
            parts.append(
                "REGISTER — WARM (two walking together): write \"you\" as the plural "
                "it already is in English; NEVER \"you two\", \"both of you\", "
                "\"your partner\", or \"lovers\" as an invitation; never a sentence "
                "that addresses the relationship or stages a scene for it. Warm is "
                "not chattier: a register may take a clause away, never add a "
                f"sentence. {invariants}"
            )
        else:
            parts.append(
                "REGISTER — FAMILY (read aloud over small heads): short declarative "
                "sentences, under about twenty words each. Give one thing to find "
                "with the eyes inside the stop's first minute. You may address the "
                "child directly ONCE per stop — a \"see/look/find\" line or one "
                "question — never \"kids\", never a made-up name. You may LEAD with "
                "the child-friendly true things and leave the rest to the full "
                f"telling. Cushion DOWN from the telling as written. {invariants}"
            )
    if request.door_state:
        parts.append(f"DOOR — {request.door_state}")
    if request.already_told:
        parts.append(
            "ALREADY TOLD (the tight telling of this stop, which the walker has just "
            "heard — you are writing THE FULL TELLING, a continuation from the material "
            "below that must repeat NOTHING of it: no fact, no phrase, no image already "
            "used; point-first, with its own close):\n"
            f"{request.already_told}"
        )
    if request.thread_from:
        parts.append(
            "THREADS FROM (stops that may come right before this one when the day is "
            "replanned — answer in the \"threads\" field, one sentence under fifteen words "
            "per stop, or leave a stop out when nothing honest binds it):\n"
            f"{json.dumps(list(request.thread_from), ensure_ascii=False)}"
        )
    if request.duplicate_pairs:
        dupes = [
            {"stop_idx": stop_idx, "a": a, "b": b} for stop_idx, a, b in request.duplicate_pairs
        ]
        parts.append(
            "CANDIDATE DUPLICATE PAIRS (same-stop sentences a pre-scan found "
            "similar — probably the same fact; fuse each into one telling unless "
            "genuinely distinct, and also fuse repeats not listed here):\n"
            f"{json.dumps(dupes, ensure_ascii=False)}"
        )
    if attempt > 1 and prev_report is not None:
        failures = {
            "untraceable": [s.text for s in prev_report.untraceable_sentences],
            "forbidden_or_invented": [
                [s.text, code] for s, code in prev_report.forbidden_phrase_hits
            ],
            "unfaithful": [[s.text, code] for s, code in prev_report.faithfulness_failures],
            # Facts the previous attempt DROPPED (usually a date or number lost while
            # fusing a repeat). Each MUST reappear — it was in the stitched script.
            "dropped_facts_you_must_restore": [
                claim for _bid, claim in prev_report.coverage_failures
            ],
        }
        parts.append(
            "PREVIOUS ATTEMPT FAILED VERIFICATION — fix exactly these problems "
            "(this is the single allowed recompose). For dropped_facts_you_must_restore, "
            "weave each fact back in (fuse it into the sentence that now covers that "
            "topic; do not re-introduce the repetition):\n"
            f"{json.dumps(failures, ensure_ascii=False)}"
        )
    return "\n\n".join(parts)


def compose_input_sha256(request: ComposeRequest) -> str:
    """Canonical hash of the exact typed input before provider-envelope metadata."""

    payload = json.dumps(
        request.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def candidate_compose_request_envelope(
    request: ComposeRequest,
    authoring_request: AuthoringStopRequest,
    *,
    model: str = COMPOSE_MODEL,
) -> tuple[str, dict[str, object]]:
    """Build one adaptive, 64K, candidate-bound physical authoring request."""

    stops = {sentence.stop_idx for sentence in request.stitched.script}
    if stops != {authoring_request.stop_index}:
        raise ValueError("authoring request stop differs from its compose input")
    if compose_input_sha256(request) != authoring_request.compose_input_sha256:
        raise ValueError("authoring request is bound to different compose input")
    binding = json.dumps(
        {
            "candidate_id": authoring_request.candidate.candidate_id,
            "request_id": authoring_request.request_id,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    user = _compose_user_prompt(request, 1, None) + "\n\nAUTHORING BINDING:\n" + binding
    sdk_request: dict[str, object] = {
        "model": model,
        "max_tokens": CERTIFICATION_COMPOSE_MAX_OUTPUT_TOKENS,
        "thinking": {"type": "adaptive"},
        "system": [
            {
                "type": "text",
                "text": _COMPOSE_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "output_config": {"format": {"type": "json_schema", "schema": _COMPOSE_OUTPUT_SCHEMA}},
        "messages": [{"role": "user", "content": user}],
    }
    return (
        json.dumps(
            sdk_request,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        sdk_request,
    )


def _threads_from_json(payload: dict) -> tuple[tuple[str, str], ...]:
    """The writer's THREADS from a compose response's JSON (Phase 6 S6.5): the optional
    ``threads`` array of ``{"from", "text"}`` pairs, shape-checked here (TypeError on a
    malformed entry, caught by the caller as "not a valid Premium sentence payload");
    the CONTENT rules — one sentence, under fifteen words, a name the request asked for —
    are the finalizer's (``_keep_threads``)."""
    raw = payload.get("threads", [])
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise TypeError("threads is not a list")
    out: list[tuple[str, str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise TypeError("a thread is not an object")
        from_name, text = entry["from"], entry["text"]
        if not isinstance(from_name, str) or not isinstance(text, str):
            raise TypeError("a thread's from/text is not a string")
        out.append((from_name, text.strip()))
    return tuple(out)


def _edit_distance_at_most_1(a: str, b: str) -> bool:
    """One substitution, insertion or deletion apart (equal strings excluded)."""
    if a == b:
        return False
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        return sum(1 for x, y in zip(a, b, strict=True) if x != y) == 1
    shorter, longer = (a, b) if la < lb else (b, a)
    i = 0
    while i < len(shorter) and shorter[i] == longer[i]:
        i += 1
    return shorter[i:] == longer[i + 1 :]


def _corrected_citation(cited_id: str, known_ids: frozenset[str]) -> str:
    """The one deterministic copy-error correction (Phase 8 S8.3e; W8.2 R4).

    A cited id at edit distance 1 from exactly ONE id the request supplied is a
    WRITER'S TYPO, corrected — W7.14's live instance killed Camille's day 422
    three times over one dropped character (`1311cfd-…` for `1311cf7d-…`). A
    known id, an id with no close neighbour, or one with TWO close neighbours
    (never guess) comes back untouched, and traceability still blocks the
    untraceable. The corrected citation flows into every downstream gate —
    entailment judges the sentence against the TRUE beat, never a free pass.
    """
    if cited_id in known_ids:
        return cited_id
    candidates = [known for known in known_ids if _edit_distance_at_most_1(cited_id, known)]
    return candidates[0] if len(candidates) == 1 else cited_id


def _sentences_from_json(sentences: list[dict], request: ComposeRequest) -> tuple[Sentence, ...]:
    """Build ``Sentence`` objects from a compose response's JSON, COERCING each BEAT
    sentence's ``stop_idx`` to its beat's TRUE stitched stop.

    The model echoes ``stop_idx`` in its output; trusting that value verbatim let a
    mis-tagged beat sentence be bucketed into the wrong stop by the compose gate — a
    silent mis-placement / empty-stop / mis-repair class (a stop could ship with zero
    narration while the lenient tour-wide coverage gate still passed). A beat's home stop
    is unambiguous from the stitch, so we take it from ``beat_stop[source_id]`` and ignore
    the model's echo. Non-beat glue/reflection sentences (no source beat) keep their given
    slot ``stop_idx``; an unknown ``source_id`` (a hallucinated beat) is left as-given for
    the traceability gate to reject.

    Phase 8 S8.3e: a beat citation (``source_id`` or an ``also_cites`` entry) one
    character off exactly ONE id of this stop's stitch is corrected first
    (``_corrected_citation``) — the mechanical consequence of W8.2 R4: the
    writer's typos stop killing days, and everything else still fails closed.

    The SAME doctrine covers the TYPE field (live 2026-08-29, v3 batch): a
    sentence typed "beat" whose ``source_id`` is one of THIS request's
    authorized derived (glue/reflection) ids is a mistyped label, not an
    invention — the id is authoritative because ids are copied from the prompt
    and no beat carries a glue id. Coerced to "glue"; a "beat"-typed sentence
    with any OTHER unknown id still fails traceability, and the reflection id
    is honoured only when the request actually asked for a slot."""
    beat_stop = {
        s.source_id: s.stop_idx for s in request.stitched.script if s.source_type == "beat"
    }
    known_ids = frozenset(beat_stop)
    derived_ids = frozenset(
        s.source_id for s in request.stitched.script if s.source_type != "beat"
    ) | ({GLUE_REFLECTION} if request.slots else frozenset())
    out: list[Sentence] = []
    for s in sentences:
        stype = s["source_type"]
        sid = s["source_id"]
        if stype == "beat" and sid in derived_ids and sid not in known_ids:
            stype = "glue"
        if stype == "beat":
            sid = _corrected_citation(sid, known_ids)
        stop_idx = beat_stop.get(sid, s["stop_idx"]) if stype == "beat" else s["stop_idx"]
        out.append(
            Sentence(
                text=s["text"],
                source_id=sid,
                source_type=stype,
                stop_idx=stop_idx,
                # Only beat sentences carry fused citations; ignore any stray also_cites
                # the model attaches to glue.
                also_cites=(
                    tuple(
                        _corrected_citation(cited, known_ids)
                        for cited in (s.get("also_cites") or ())
                    )
                    if stype == "beat"
                    else ()
                ),
            )
        )
    return tuple(out)


def _door_state(route: Route, stop_index: int) -> str:
    """The writer's door instruction for one stop — "" when the door is open
    or nobody priced one (`visit_goes_inside` empty or True: the identity).

    Three different truths get three different sentences, keyed the way the
    wire keys them (`ClockExclusion.kept_outside` and `.guessed`): a door the
    CLOCK voided on MAP hours may be said plainly to be closed today; one
    voided on GUESSED hours (Docs/adr/0006) keeps its hedge — the writer may
    say we think it is closed and could not confirm that, never state it; a
    stop outside-only because the day has no time for its interior is NOT
    closed, and calling it closed would be the checkable lie the
    interior_did_not_fit rule exists to prevent. No sentence hands the writer
    a weekday, an hour or a number — the glue invention scan licenses none of
    those, and the honest line needs none.
    """
    if stop_index >= len(route.pois):
        return ""
    poi = route.pois[stop_index]
    if route.visit_goes_inside.get(poi.id) is not False:
        return ""
    shut = next((e for e in route.clock_exclusions if e.poi_id == poi.id and e.kept_outside), None)
    if shut is not None and shut.guessed:
        report = (
            f" A walker has reported this door shut; you may say '{REPORTED_SHUT}'."
            if shut.closed_reports > 0
            else ""
        )
        return (
            "This stop's door is probably shut while the walker is here — our hours "
            "for it are a GUESS we could not confirm — so the visit stays on the "
            "OUTSIDE. Never invite the listener through the door — no 'step inside', "
            "'go in', 'enter'. If you mention the closure, keep the hedge exactly: "
            "'we think' it is closed and we 'could not confirm' that; never state it "
            f"as a fact.{report} Stage the exterior."
        )
    if shut is not None:
        return (
            "This stop's door is shut while the walker is here: the visit stays "
            "on the OUTSIDE. Never invite the listener through the door — no "
            "'step inside', 'go in', 'enter'. You may say plainly that it is "
            "closed today, and stage the exterior instead."
        )
    return (
        "This stop's visit stays on the OUTSIDE — the day has no time to go in. "
        "Never invite the listener through the door — no 'step inside', 'go "
        "in', 'enter'. Do NOT call the place closed (it is open; the hour is "
        "the constraint); stage the exterior."
    )


def _certification_compose_requests(
    stitched: Script,
    beat_sequence: BeatSequence,
    route: Route,
    *,
    already_told_by_stop: Mapping[int, str] | None = None,
) -> tuple[dict[str, BeatRef], list[int], dict[int, ComposeRequest]]:
    """Rebuild the canonical per-stop requests from the grounded source.

    ``already_told_by_stop`` (Phase 6 S6.6): set only on a FULL-TELLING plan — the
    stop's request carries the tight telling as ALREADY TOLD, and asks for NO
    threads (a full telling plays inside its stop; threads belong to the day's
    tight compose)."""
    beats_by_id = {beat.id: beat for plan in beat_sequence.poi_beats for beat in plan.beats}
    tour_context = tuple(poi.name for poi in route.pois)
    by_stop: dict[int, list[Sentence]] = defaultdict(list)
    for sentence in stitched.script:
        by_stop[sentence.stop_idx].append(sentence)
    stops = sorted(by_stop)
    # The UPPER bound was deleted 2026-08-04 (OWNER RULING 5): duration alone
    # decides how many stops a route has, and refusing to author one the same
    # engine had already planned was a limit with no reason behind it. The LOWER
    # bound stays: ``requests`` below is keyed by stop index, so an empty stitch
    # would silently yield an empty authoring plan that a caller would then
    # "author" for zero stops and persist as a tour.
    if not stops:
        raise ValueError("authoring requires at least one stop")
    all_slots = tuple(
        slot
        for slot in reflection_slots(route, beat_sequence)
        if _visited_claims(stitched, beats_by_id, slot)
    )
    visited = {slot: _visited_claims(stitched, beats_by_id, slot) for slot in all_slots}

    requests: dict[int, ComposeRequest] = {}
    for stop_index in stops:
        stop_sentences = by_stop[stop_index]
        mini = stitched.model_copy(update={"script": tuple(stop_sentences)})
        stop_beats = {
            sentence.source_id: beats_by_id[sentence.source_id]
            for sentence in stop_sentences
            if sentence.source_type == "beat" and sentence.source_id in beats_by_id
        }
        # Phase 6 S6.5: the skip pair. The set skips every stop (R1.2), so the stop
        # two back may come right before this one; its name asks for a thread.
        already_told = (already_told_by_stop or {}).get(stop_index, "")
        thread_from = (
            (route.pois[stop_index - 2].name,)
            if not already_told
            and stop_index >= 2
            and stop_index - 2 < len(route.pois)
            and not route.pois[stop_index - 2].id.startswith(END_B_SENTINEL_PREFIX)
            else ()
        )
        requests[stop_index] = ComposeRequest(
            stitched=mini,
            beats_by_id=stop_beats,
            slots=tuple(slot for slot in all_slots if slot == stop_index),
            visited_claims_by_slot={
                index: claims for index, claims in visited.items() if index == stop_index
            },
            duplicate_pairs=(),
            tour_context=tour_context,
            thread_from=thread_from,
            already_told=already_told,
            door_state=_door_state(route, stop_index),
        )
    return beats_by_id, stops, requests


def _dedup_composed(sentences: list[Sentence], beat_sequence: BeatSequence) -> list[Sentence]:
    """The composed-path de-dup, ported byte-for-byte from ``compose.py`` (one-true-
    tour-algorithm ledger, step A5): collapse a fact voiced twice in the ASSEMBLED
    sentence stream — cross-beat claim repeat (the Île de la Cité three-source-book
    case, now most often cross-STOP since each stop is authored independently),
    same-stop byte-identical, and same-beat near-verbatim. Coverage-safe BY
    CONSTRUCTION: it keeps the FIRST telling / any sentence carrying a novel claim
    and never empties a beat, so a script that covered every claim before still
    does (a dropped twin's fact stays voiced by its survivor). Run inside
    ``finalize_certification_composition`` — the ONE finalizer both the persisted
    ``/trips/{id}/compose`` path (``author_prebuilt_route``) and ``/trips/preview``
    (``premium_tour.finalize_premium_composition``) call — so neither surface ships
    a duplicate the other suppresses. Always run BEFORE verify (with the
    pre-compose coverage baseline, when the caller enables it), so a drop that
    would unexpectedly lose a fact fails closed rather than shipping silently."""
    out = suppress_repeated_claims(sentences, beat_sequence, include_same_beat=True)
    out = suppress_exact_repeats(out, beat_sequence)
    out = suppress_same_beat_near_duplicates(out)
    return out


#: W6.2 R5: a thread is under fifteen words (Paulo, F&D).
THREAD_MAX_WORDS = 15
THREAD_DROPPED_DEGRADATION = "thread_dropped"


def _thread_repeats_the_telling(thread_text: str, own_sentences: list[str]) -> bool:
    """Is this thread a near-verbatim restatement of its stop's own telling?

    The claim_dedup near-dup detector applied to the one pair R3 names: the
    thread against the sentences the same stop already voices. Token-set ratio
    at ``claim_dedup._NEAR_DUP_RATIO`` (90 — the Abelard echo measured 98-100,
    two different facts ~37); very short lines are never judged (the
    ``_repeat_key`` length guard), same as everywhere else."""
    from rapidfuzz import fuzz

    from .claim_dedup import _NEAR_DUP_RATIO, _repeat_key

    key = _repeat_key(thread_text)
    if len(key) < 25:
        return False
    return any(
        fuzz.token_set_ratio(key, other) >= _NEAR_DUP_RATIO
        for sentence in own_sentences
        if len(other := _repeat_key(sentence)) >= 25
    )


def _keep_threads(
    units_by_stop: dict[int, CompletedCertificationComposeUnit],
    stops: list[int],
    requests: dict[int, ComposeRequest],
    *,
    composed: Script,
    beats_by_id: dict[str, BeatRef],
    checker: FaithfulnessChecker | None,
) -> dict[int, dict[str, str]]:
    """The writer's THREADS, kept by stop and predecessor (Phase 6 S6.5; design §5.4;
    W6.2 R5): each ONE sentence and under THREAD_MAX_WORDS words, answering a name the
    request actually asked for, and — when this run carries the real ``checker`` —
    ENTAILED against the same union the in-script thread is gated on (what the walker
    has heard plus the stop's own beats; a thread is content, fact-gated).

    A thread that misses the bar is DROPPED AND REPORTED on the degradations channel
    (``thread_dropped``), and the day ships without it — the panel's own remedy for the
    pair is silence, never glue (R5: "none rather than glue"), and the S6.4 precedent
    holds: a refusal is for missing MANDATORY content (a close), a quality miss on
    OPTIONAL enrichment ships degraded and visibly. MEASURED 2026-08-19 (s65-proof,
    this ledger's S6.5): with these as ValueError, two of three real F&D composes died
    whole — a 16-word thread and one unentailed line each killed a day whose every
    SENTENCE had passed the gate. Answering a name that was never asked for is not a
    quality miss but a protocol violation, and still refuses (ValueError)."""
    from .degradations import record
    from .generation import split_sentences
    from .verify import _own_stop_support

    def drop(stop_index: int, from_name: str, text: str, why: str) -> None:
        record(
            kind=THREAD_DROPPED_DEGRADATION,
            human=(
                "One of the lines written to bridge a re-planned pair of stops was "
                "dropped; if the day is re-planned there, the walk simply continues "
                "without a bridging line."
            ),
            component="authoring._keep_threads",
            cause=(
                f"stop {stop_index}'s thread from {from_name!r} {why}: {text!r}. "
                "R5: none rather than glue — the pair ships with silence."
            ),
            stop_index=str(stop_index),
        )

    out: dict[int, dict[str, str]] = {}
    for stop_index in stops:
        unit = units_by_stop[stop_index]
        asked = set(requests[stop_index].thread_from)
        kept: dict[str, str] = {}
        for from_name, text in unit.parsed_threads:
            if from_name not in asked:
                raise ValueError(
                    f"stop {stop_index} returned a thread from {from_name!r}, which was not "
                    f"asked for (asked: {sorted(asked)})"
                )
            if len(split_sentences(text)) != 1:
                drop(stop_index, from_name, text, "is not ONE sentence")
                continue
            if len(text.split()) > THREAD_MAX_WORDS:
                drop(stop_index, from_name, text, f"is over {THREAD_MAX_WORDS} words")
                continue
            # Phase 8 S8.3d (W8.2 R3, 11/11): ONE FACT, ONE VOICE, PER DAY — a
            # thread that near-verbatim repeats a sentence of its OWN stop's
            # telling is the measured repeat class (Greta's coronation
            # story+thread; Sofia's Templar bridge leg+thread). Same detector as
            # the composed-script near-dup pass (rapidfuzz token_set_ratio at
            # claim_dedup's threshold); the remedy stays R5's — silence.
            own_sentences = [s.text for s in composed.script if s.stop_idx == stop_index]
            if _thread_repeats_the_telling(text, own_sentences):
                drop(stop_index, from_name, text, "repeats the stop's own telling")
                continue
            if checker is not None:
                claims = _visited_claims(composed, beats_by_id, stop_index)
                own = _own_stop_support(composed, beats_by_id, stop_index)
                support = (*claims, *(piece for piece in own if piece not in claims))
                if not support or not checker.entails(support, text):
                    drop(
                        stop_index, from_name, text,
                        "is not entailed by what the walk carries",
                    )
                    continue
            kept[from_name] = text
        if kept:
            out[stop_index] = kept
    return out


LINE_DROPPED_DEGRADATION = "line_dropped_where_untrue"


def _drop_corpus_leg_lines_untrue_where_they_play(
    composed: Script,
    report: ValidationReport,
    beat_sequence: BeatSequence,
    *,
    vignette_beat_ids: frozenset[str],
    transit_beat_ids: frozenset[str],
) -> tuple[int, Script]:
    """ADR 0001's rule at FIRST compose: drop the failing line, say so, never
    refuse the day for it — scoped to the one class where a drop is safe.

    Eligible: a placement/leg-voice hit on a walk-concurrent CORPUS sentence
    (a vignette one-liner, a transit-beat line) — self-contained units, the
    class a sentence-level drop cannot orphan. A sentence inside a stop's
    continuous prose is never dropped here, and writer glue keeps the bounded
    reroll + refusal path. Returns (dropped_count, reduced_script)."""
    from .degradations import record

    flagged: dict[int, tuple[Sentence, list[str]]] = {}
    for sentence, code in report.forbidden_phrase_hits:
        if sentence.source_type != "beat":
            continue
        if not is_walk_concurrent(sentence, vignette_beat_ids, transit_beat_ids):
            continue
        flagged.setdefault(id(sentence), (sentence, []))[1].append(code)
    if not flagged:
        return 0, composed
    kept = tuple(s for s in composed.script if id(s) not in flagged)
    for sentence, codes in flagged.values():
        record(
            kind=LINE_DROPPED_DEGRADATION,
            human=(
                "One line written for a walk was not true where it plays, so it "
                "was left unsaid; the walk simply continues without it."
            ),
            component="authoring.finalize_certification_composition",
            cause=(
                f"{', '.join(sorted(codes))}: {sentence.text!r}. ADR 0001 — "
                "silence over wrongness, never refuse the day."
            ),
            stop_index=str(sentence.stop_idx),
        )
    reduced = composed.model_copy(
        update={
            "script": kept,
            "total_audio_seconds": _sum_audio(kept, beat_sequence),
        }
    )
    return len(flagged), reduced


CLOSE_NOT_AUTHORED_DEGRADATION = "close_not_authored"


def _require_one_close_per_stop(
    sentences: list[Sentence], stitched: Script, stops: list[int], route: Route
) -> None:
    """Every composed stop ENDS on exactly one one-sentence GLUE_CLOSING (Phase 6 S6.4;
    design §5.3, §7.4.5 "every prefix is decent"). Missing, doubled, not last, or more
    than one sentence long → ValueError (the seam's refusal). A close byte-identical to
    the stitch's template is NOT authored: it ships, and is reported."""
    from .degradations import record
    from .generation import GLUE_CLOSING, is_fallback_close, split_sentences

    by_stop: dict[int, list[Sentence]] = defaultdict(list)
    for sentence in sentences:
        by_stop[sentence.stop_idx].append(sentence)
    names = {index: poi.name for index, poi in enumerate(stitched.selected_pois)}
    n_stops = len(stitched.selected_pois)
    for stop_index in stops:
        stop_sentences = by_stop.get(stop_index, [])
        closes = [s for s in stop_sentences if s.source_id == GLUE_CLOSING]
        if len(closes) != 1:
            raise ValueError(
                f"stop {stop_index} has {len(closes)} close(s); every composed stop ends on "
                "exactly one GLUE_CLOSING sentence (design §5.3)"
            )
        if stop_sentences[-1] is not closes[0]:
            raise ValueError(
                f"stop {stop_index}'s close is not its last sentence — the close is what a "
                "wrap-up plays and must land after every other line of the stop"
            )
        if len(split_sentences(closes[0].text)) != 1:
            raise ValueError(
                f"stop {stop_index}'s close is not ONE sentence (design §4.4.4): "
                f"{closes[0].text!r}"
            )
        if is_fallback_close(
            closes[0].text, stitched.inputs, poi_name=names.get(stop_index, ""), n_stops=n_stops
        ):
            record(
                kind=CLOSE_NOT_AUTHORED_DEGRADATION,
                human=(
                    f"The last line at {names.get(stop_index, 'a stop')} is the fixed "
                    "closing line, not one the narrator wrote for it."
                ),
                component="authoring.finalize_certification_composition",
                cause=(
                    "The composed stop kept the stitch's GLUE_CLOSING template verbatim; "
                    "the one writer was asked to rewrite it (THE CLOSE) and did not. It "
                    "ships as the fallback and is never counted as authored (plan S6.4)."
                ),
                stop_index=str(stop_index),
            )


def finalize_certification_composition(
    stitched: Script,
    beat_sequence: BeatSequence,
    route: Route,
    *,
    completed_units: tuple[CompletedCertificationComposeUnit, ...],
    model: str = COMPOSE_MODEL,
    chunk_text_by_slug: dict[str, str] | None = None,
    faithfulness_checker: FaithfulnessChecker | None = None,
    enforce_claim_coverage: bool = False,
    scan_glue_for_invention: bool = False,
    require_closes: bool = False,
    enforce_placement_floors: bool = False,
    already_told_by_stop: Mapping[int, str] | None = None,
) -> CertificationComposition:
    """Purely verify and finalize already-completed per-stop compose responses.

    This is the durable replay boundary: it reconstructs every authorized request
    from the grounded source, rejects incomplete or alternate response sets, binds
    each parsed payload to its recorded hash, then runs VERIFY without editing the
    provider-authored text. Unlike the Basic Tour/live availability path, certification never
    splices or reverts to the grounded source.  It performs no provider calls and
    needs no in-memory call ledger.

    THE THREE GATE KNOBS, and why they are knobs (ledger decision D3).  This
    finalizer was written for the CERTIFICATION replay, whose judgement of prose is
    SEMANTIC: it validates structure only and lets factual review own meaning, so its
    defaults are the trusting offline entailment stub, no coverage baseline and no
    lexical scan.  The persisted ``POST /trips/{id}/compose`` path is the opposite
    case — it writes an unreviewed tour into Neo4j — and the whole-tour composer it
    replaced ran all three.  Rather than re-gate certification (a NEW check, which
    this ledger forbids) each is injectable and defaults OFF:

    * ``faithfulness_checker`` — the real entailment checker; ``None`` keeps the
      trusting ``MockFaithfulnessChecker``.
    * ``enforce_claim_coverage`` — derive the coverage baseline from the PRE-compose
      stitch, so a bold fusion may merge or reword a fact but never DELETE one.
    * ``scan_glue_for_invention`` — add ``validate_script``'s other half (the
      forbidden-phrase / invented-proper-noun / invented-year scan over glue) on top
      of the authorized-sources traceability below.  Structural traceability cannot
      see invention, so without this the count on the compose 422 reads 0 by
      construction rather than by measurement.
    * ``require_closes`` (Phase 6 S6.4; design §5.3, §7.4.5) — every composed stop
      must END on exactly one GLUE_CLOSING sentence, one sentence long: the close is
      what a wrap-up plays, so a stop without one cannot ship (refused here with a
      ValueError, the seam's refusal shape). A close left as the stitch's TEMPLATE
      is not authored: it ships (the fallback beats silence) and is REPORTED on the
      degradations channel as ``close_not_authored``, never counted as the writer's.
      OFF for the certification replay (the sealed candidates predate closes).
    * ``enforce_placement_floors`` (Phase 8 S8.3; W8.2 R1/R2/R5) — a sentence must
      be TRUE WHERE IT PLAYS: ``validation.placement_floor_hits`` over the composed
      script, route-aware through this finalizer's own closure (the legs' routed
      minutes, each stop's door, the tap-only full-telling stops). Hits land on
      ``forbidden_phrase_hits`` — named, blocking, and carried into the bounded
      retry's failure list. OFF for the certification replay (the sealed
      candidates predate the rulings).
    """
    beats_by_id, stops, requests = _certification_compose_requests(
        stitched, beat_sequence, route, already_told_by_stop=already_told_by_stop
    )
    units_by_stop: dict[int, CompletedCertificationComposeUnit] = {}
    for unit in completed_units:
        if unit.unit_id != f"stop:{unit.stop_index}":
            raise ValueError("completed compose unit differs from its stop index")
        if unit.model != model:
            raise ValueError("completed compose unit used an unauthorized model")
        request_stops = {sentence.stop_idx for sentence in unit.authorized_request.stitched.script}
        if request_stops != {unit.stop_index}:
            raise ValueError("completed compose request spans a different stop")
        if unit.stop_index in units_by_stop:
            raise ValueError("completed compose response repeats a stop")
        units_by_stop[unit.stop_index] = unit
    if set(units_by_stop) != set(stops):
        raise ValueError("completed compose responses differ from candidate stops")

    composed_by_stop: dict[int, tuple[Sentence, ...]] = {}
    for stop_index in stops:
        unit = units_by_stop[stop_index]
        expected_request = requests[stop_index]
        if unit.authorized_request != expected_request:
            raise ValueError("completed compose request differs from grounded source")
        if unit.authoring_request.stop_index != stop_index:
            raise ValueError("completed authoring request differs from its stop")
        envelope, _ = candidate_compose_request_envelope(
            unit.authorized_request,
            unit.authoring_request,
            model=model,
        )
        expected_request_sha256 = hashlib.sha256(envelope.encode("utf-8")).hexdigest()
        if unit.request_sha256 != expected_request_sha256:
            raise ValueError("completed compose request hash is inconsistent")
        expected_payload_sha256 = sentences_payload_sha256(unit.parsed_provider_sentences)
        if unit.parsed_payload_sha256 != expected_payload_sha256:
            raise ValueError("completed compose parsed payload hash is inconsistent")
        composed_by_stop[stop_index] = unit.parsed_provider_sentences

    composed_sentences = _dedup_composed(
        [sentence for stop_index in stops for sentence in composed_by_stop[stop_index]],
        beat_sequence,
    )
    if require_closes:
        _require_one_close_per_stop(composed_sentences, stitched, stops, route)
    # ``composed_by_stop`` STAYS the raw provider payload. It is deliberately NOT
    # rebound to the post-dedup grouping.
    #
    # An earlier version of this port did rebind it, reasoning that the raw payload
    # "stays attested regardless" via ``response_sha256``. That was wrong twice over,
    # and a judge consult caught it:
    #   1. It made the fail-closed guard in ``finish`` a tautology. ``final_sentences``
    #      is a stop_idx filter over ``final_script.script``, which IS
    #      ``composed_sentences``; comparing it to the same filter over the same list
    #      can never differ, so a real provenance check silently became dead code.
    #   2. ``CompositionTrace.source_sentences`` / ``source_payload_sha256`` are what a
    #      replay reads as "what the provider returned". Recording post-dedup text there
    #      under ``derivation="provider_response"``, while ``response_sha256`` names the
    #      untouched body, makes the certification artifact claim bytes the provider
    #      never sent — on any tour where de-dup dropped a sentence.
    #
    # De-dup can only DROP sentences, never rewrite or reorder one, so the honest
    # relationship is SUBSEQUENCE: each stop's final text must appear in that stop's raw
    # provider output, in order. ``CompositionTrace`` is already built for exactly this —
    # ``source_sentence_indexes`` must be monotonic, unique and in range, one per final
    # sentence — so ``finish`` below proves the subsequence and records which raw
    # sentences survived, instead of asserting an identity that de-dup makes impossible.
    composed = stitched.model_copy(
        update={
            "script": tuple(composed_sentences),
            "total_audio_seconds": _sum_audio(composed_sentences, beat_sequence),
            "validation": ValidationReport(),
        }
    )
    authorized_derived_source_ids = frozenset(
        sentence.source_id
        for request in requests.values()
        for sentence in request.stitched.script
        if sentence.source_type != "beat"
    ) | ({GLUE_REFLECTION} if any(request.slots for request in requests.values()) else set())

    # Phase 8 S8.3: the route-aware inputs the placement floors need, computed
    # once from what this finalizer already holds — placement itself stays THE
    # frozen rule (is_walk_concurrent + these vignette ids), never re-derived.
    vignette_beat_ids = frozenset(
        beat.id for beats in beat_sequence.vignette_beats.values() for beat in beats
    )
    # The walk's own corpus narration (P9R-S1): transit-class beats play ON the
    # leg, so the floor judges them by the leg's rules, not the stop's.
    transit_beat_ids = transit_class_beat_ids(beats_by_id.values())
    leg_minutes_by_stop = {
        index: minutes
        for index, transit in enumerate(route.transits)
        if (minutes := _nav_walk_minutes(leg_walk_seconds(transit)))
    }
    goes_inside_by_stop = {
        index: bool(route.visit_goes_inside.get(poi.id, False))
        for index, poi in enumerate(route.pois)
    }
    tap_only_stops = frozenset(already_told_by_stop or ())

    def validate_authorized_sources(script: Script, sequence: BeatSequence) -> ValidationReport:
        report = validate_source_traceability(
            script,
            sequence,
            allowed_derived_source_ids=frozenset(authorized_derived_source_ids),
        )
        floor_hits: tuple[tuple[Sentence, str], ...] = ()
        if enforce_placement_floors:
            floor_hits = tuple(
                placement_floor_hits(
                    script,
                    vignette_beat_ids=vignette_beat_ids,
                    leg_minutes_by_stop=leg_minutes_by_stop,
                    goes_inside_by_stop=goes_inside_by_stop,
                    tap_only_stops=tap_only_stops,
                    transit_beat_ids=transit_beat_ids,
                )
            )
        if not scan_glue_for_invention:
            if floor_hits:
                return report.model_copy(update={"forbidden_phrase_hits": floor_hits})
            return report
        # Full ``validate_script`` parity. Only the forbidden-phrase half is taken
        # from it: its traceability half does not know THIS run's authorized derived
        # source ids, so it would reject legitimately-authorized glue.
        return report.model_copy(
            update={
                "forbidden_phrase_hits": (
                    validate_script(
                        script,
                        sequence,
                        spine_area=route.spine_area,
                        disclosed_place_names=tuple(
                            e.name for e in route.clock_exclusions
                        ),
                    ).forbidden_phrase_hits
                    + floor_hits
                )
            }
        )

    verifier = build_full_verifier(
        beat_sequence,
        beats_by_id,
        chunk_text_by_slug=chunk_text_by_slug,
        faithfulness_checker=faithfulness_checker,
        # This function's public signature keeps ``faithfulness_checker=None``
        # (pinned by tests/test_tour_authoring_gates.py), and the offline
        # certification-replay path genuinely runs without one. The gate no
        # longer substitutes a trusting checker behind our back, so state the
        # intent here instead: no checker means the faithfulness pass is SKIPPED,
        # and the report it returns says so via ``faithfulness_checked=False``.
        # The live API never takes this branch — src/api/dependencies.py always
        # injects the real Haiku checker.
        allow_unverified_faithfulness=faithfulness_checker is None,
        # The baseline is what the PRE-compose stitch actually voiced — not every
        # key_claim — so the gate blocks deletion without demanding a beat's prose
        # voice claims it never voiced.
        expected_claim_ids=(
            claims_realized_by(stitched, beats_by_id) if enforce_claim_coverage else None
        ),
        base_validator=validate_authorized_sources,
    )
    report = verifier(composed)
    # THE FIRST-COMPOSE DROP VALVE (P9R-S1; ADR 0001 extended): when the ONLY
    # thing between this day and serving is a corpus leg line untrue where it
    # plays, the line is dropped and disclosed rather than the day refused.
    # The reduced script re-runs the FULL verifier, so a drop that breaks
    # anything else still fails closed; when hits remain, the refusal carries
    # the residual report, which is what the bounded reroll acts on.
    if not report.passed:
        dropped, reduced = _drop_corpus_leg_lines_untrue_where_they_play(
            composed,
            report,
            beat_sequence,
            vignette_beat_ids=vignette_beat_ids,
            transit_beat_ids=transit_beat_ids,
        )
        if dropped:
            retry_report = verifier(reduced)
            composed, report = reduced, retry_report

    def finish(
        final_script: Script, threads_by_stop: dict[int, dict[str, str]]
    ) -> CertificationComposition:
        traces: list[CompositionTrace] = []
        attested: set[int] = set()
        for stop_index in stops:
            unit = units_by_stop[stop_index]
            sentence_indexes = tuple(
                index
                for index, sentence in enumerate(final_script.script)
                if sentence.stop_idx == stop_index
            )
            final_sentences = tuple(final_script.script[index] for index in sentence_indexes)
            source_sentences = composed_by_stop[stop_index]  # the RAW provider payload
            stitched_source = requests[stop_index].stitched.script
            if not final_sentences:
                raise ValueError(
                    f"stop {stop_index} has no composed text left after de-dup — the "
                    "provider response cannot be attested by an empty trace"
                )
            # PROVE the subsequence, and record WHICH raw sentences survived de-dup.
            # De-dup may only drop, so every final sentence must appear in this stop's
            # raw provider output, in order. A sentence that does not — e.g. a glue or
            # reflection line whose stop_idx was mis-echoed by the provider and so
            # re-attributed to a neighbouring stop — raises here instead of shipping
            # inside a trace that says the provider authored it.
            surviving: list[int] = []
            cursor = 0
            for sentence in final_sentences:
                while cursor < len(source_sentences) and source_sentences[cursor] != sentence:
                    cursor += 1
                if cursor >= len(source_sentences):
                    raise ValueError(
                        f"stop {stop_index} composed text is not a subsequence of the "
                        "provider response — a sentence is attributed to a stop the "
                        "provider did not author it for"
                    )
                surviving.append(cursor)
                cursor += 1
            source_indexes = tuple(surviving)
            traces.append(
                CompositionTrace(
                    unit_id=unit.unit_id,
                    stop_index=stop_index,
                    request_sha256=unit.request_sha256,
                    response_sha256=unit.response_sha256,
                    derivation="provider_response",
                    authorized_source_sentences=stitched_source,
                    source_sentences=source_sentences,
                    source_payload_sha256=sentences_payload_sha256(source_sentences),
                    source_sentence_indexes=source_indexes,
                    sentence_indexes=sentence_indexes,
                    sentence_sha256s=tuple(
                        hashlib.sha256(final_script.script[index].text.encode("utf-8")).hexdigest()
                        for index in sentence_indexes
                    ),
                )
            )
            attested.update(sentence_indexes)
        # No sentence may ship unattested. Grouping by stop_idx silently skips any
        # sentence whose stop_idx falls outside `stops`, which would put narration in
        # composed.script that no CompositionTrace covers at all.
        if attested != set(range(len(final_script.script))):
            unattested = sorted(set(range(len(final_script.script))) - attested)
            raise ValueError(
                f"composed script carries {len(unattested)} sentence(s) attested by no "
                f"composition trace (indexes {unattested[:5]}) — their stop_idx falls "
                "outside the authored stop set"
            )
        return CertificationComposition(
            script=final_script,
            composition_trace=tuple(traces),
            threads_by_stop=threads_by_stop,
        )

    threads_by_stop = _keep_threads(
        units_by_stop,
        stops,
        requests,
        composed=composed,
        beats_by_id=beats_by_id,
        checker=faithfulness_checker,
    )
    if report.passed:
        return finish(composed.model_copy(update={"validation": report}), threads_by_stop)
    raise ComposeVerificationError(report, 1)


__all__ = [
    "CERTIFICATION_COMPOSE_MAX_OUTPUT_TOKENS",
    "COMPOSE_MAX_OUTPUT_TOKENS",
    "COMPOSE_MODEL",
    "FACT_REVIEW_MODEL",
    "CertificationComposition",
    "CompletedCertificationComposeUnit",
    "ComposeRequest",
    "candidate_compose_request_envelope",
    "compose_input_sha256",
    "finalize_certification_composition",
]
