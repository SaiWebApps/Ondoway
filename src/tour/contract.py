"""Tour-builder data contracts.

INPUT: TourInput — the user-supplied request (§3.1 of phase-1-design).
INTERMEDIATE: POI, BeatRef, TransitSegment, Route, POIBeats, BeatSequence,
PromiseShape, Promise.
OUTPUT: Script + Sentence + ValidationReport (§3.6 of phase-1-design).
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src import city_registry
from src.tour.corpus_places import CoordinateProvenance, valid_coordinates

# Cities with a loaded corpus (data/{slug}/ + graph nodes scoped by city_name).
# The API validates request city_slug against this so an unknown city is a clear
# 422 rather than a silent empty-corpus tour. Now DERIVED from the city registry
# (src/cities.json) so runtime onboarding has one writable registration surface;
# equals the former {"paris", "new_york"} literal exactly today.
SUPPORTED_CITIES: frozenset[str] = frozenset(city_registry.supported_cities())

GENERIC_OPEN_TOUR_CLOSING = "And that brings our walk to a close."

#: The doubt a GUESSED closure carries (Docs/adr/0006), in the words a person
#: reads or hears — ONE home for every surface, so the exclusion line, the
#: day's note, the voice at the door and the writer's brief never disagree.
#: A guess opens with "we think", so the doubt is heard before the fact.
GUESSED_CLOSURE_LEAD: str = "We think "
GUESSED_CLOSURE_DOUBT: str = ", but we could not confirm that"
#: The same guess after a walker REPORTED the door shut (rule 5): the doubt
#: is stronger and says why. One report never rewrites hours; it only
#: changes these words.
REPORTED_SHUT: str = "a walker has found it shut"


def closure_doubt(closed_reports: int) -> str:
    """The clause a guessed CLOSURE ends on: "..., and a walker has found it
    shut" once reported, else "..., but we could not confirm that"."""
    return f", and {REPORTED_SHUT}" if closed_reports > 0 else GUESSED_CLOSURE_DOUBT


def opening_doubt(closed_reports: int) -> str:
    """The clause a guessed OPENING TIME ends on ("we think it opens at 10:00,
    but ..."): the report, once made, else that we could not confirm it."""
    return f", but {REPORTED_SHUT}" if closed_reports > 0 else GUESSED_CLOSURE_DOUBT
# GENERIC_TOUR_SIGNOFF ("Thank you for coming along with me today. When you're ready,
# take your time to keep exploring on your own.") was DELETED at Phase 6 S6.4: the W6.2
# panel ruled 11/11 that a close never says "keep exploring on your own" (to someone
# alone it is a remark — Greta, Sofia) and that a thank-you is not a close's job; every
# stop now ends on one close, the day's at the last stop (generation._build_stop_close).
NONPROPOSITIONAL_GLUE_TEMPLATES: frozenset[str] = frozenset(
    {
        "Settle in.",
        "Take a moment.",
        "Take a moment to take it in.",
        GENERIC_OPEN_TOUR_CLOSING,
        "And that closes the loop, back where we started.",
        "Sit as long as you like — that's the walk.",
    }
)


def normalized_lens_list(v: list[str] | None) -> list[str] | None:
    """THE one lens-list normalization: strip blanks/whitespace; empty -> None.

    It was spelled twice — here and in the API request models, whose docstring
    admitted one "mirrors" the other (dedup-review 2026-08-07). Both validators
    now call this; the preview's vocabulary guard adds its own check on top.
    """
    if v is None:
        return None
    cleaned = [s.strip() for s in v if s and s.strip()]
    return cleaned or None


class TourInput(BaseModel):
    """User-supplied tour request. Mirrors §3.1 of phase-1-design.md."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start: tuple[float, float] = Field(..., description="(lat, lng) origin point")
    duration_min: int = Field(..., gt=0, le=600)
    city_slug: str = Field(..., min_length=1)
    lenses: list[str] | None = None
    round_trip: bool = False
    theme_hint: str | None = None
    start_label: str | None = None
    # The party's ceiling on ANY ONE stop, in minutes. None = no ceiling.
    #
    # WHY A CEILING IS NEEDED AT ALL. The other two terms in a stop's length —
    # what the place absorbs and what the visitor's interest justifies — are both
    # MAXIMISING, so nothing in them can express Nadia's "six minutes is the
    # ceiling here, not the floor" (docs/personas/03-family-with-children.md
    # step 3). The flat five-minute tier cap was protecting her by accident, and
    # removing it without this would hand a family with a five-year-old a
    # 45-minute cathedral.
    #
    # IT BOUNDS STANDING THERE, NOT TALKING. Generation floors a stop at
    # max(planned_visit, planned_audio), so a ceiling below a stop's narration is
    # overridden — Release 1 never truncates narration. The API refuses anything
    # under 5 minutes for that reason: 300 s sits above the 270 s per-stop
    # speaking cap, so an accepted ceiling is always one the tour can honour.
    max_stop_minutes: int | None = Field(default=None, ge=5)
    end: tuple[float, float] | None = Field(
        default=None,
        description=(
            "(lat, lng) destination B; None = no fixed destination (open walk / loop). "
            "Mutually exclusive with round_trip. B-materialization for ORDER (Steps 2.3/2.4): "
            "snap to the nearest in-corridor corpus POI when one is within ~150m, otherwise "
            "synthesize a sentinel POI at this exact coordinate."
        ),
    )
    # WHEN THE WALK STARTS, as a local ISO 8601 string, e.g. "2026-08-11T10:00".
    # None = no clock, which is today's dateless behaviour: nothing is filtered,
    # nothing is priced by hour, and every existing caller and golden stays
    # byte-identical (redesign §2.2). A STRING rather than a datetime because
    # TourInput must survive TourInput(**inp.model_dump()) — only a string
    # round-trips — and because the API and persisted tour_input_json carry it
    # as text anyway.
    start_datetime: str | None = None
    # HOW HARD THE END IS (redesign §2.3). "wall": missing it has a real cost —
    # Marcus's 16:40 airport train — so planning keeps visible spare minutes.
    # "firm": today's behaviour, byte-identical. "open": the duration was a
    # rough intent, never a number to pad toward — Julien's leavable-blank
    # clock. The default is "firm" so an undated request plans exactly as
    # before this field existed.
    end_hardness: Literal["wall", "firm", "open"] = "firm"
    # WHO IS WALKING (redesign §2.4) — a preset is a SHORTCUT over the axes
    # below: `resolve_party_axes` expands it, an explicitly-set axis always
    # wins, and the axes are what the planner reads. None = no party stated,
    # which with every axis at its default is today's behaviour, byte-identical.
    party: Literal["solo", "couple", "family", "take_it_easy", "with_luggage"] | None = None
    # PACE MULTIPLIER on walking time, slow direction only: 2.0 = half speed
    # (Nadia: "Pace drops to roughly half", 03-family-with-children.md step 4).
    # ge=1.0 because the FAST direction is the raised-and-held-back `PACE_KMH`
    # pin's locked half (src/tour/routing.py) — slow is a fact about real
    # walkers; fast would quietly re-price every budget. None = 1.0.
    walking_pace: float | None = Field(default=None, ge=1.0)
    # THE PER-LEG CAP, in minutes. Rosemary's binding constraint is not her
    # 54-minute walking total but her 12-minute per-leg limit — "A walking
    # budget is not one number" (05-step-free-visitor.md, breaks bullet 1).
    # None = uncapped; the banded longest-leg RANK stays as the soft preference.
    max_leg_minutes: int | None = Field(default=None, ge=1)
    # HOW MUCH ACCUMULATED WALKING may pass before a body stop (bench/toilet)
    # is seated (S2.5) — Nadia's "Ten minutes, zero cultural content, entirely
    # non-negotiable" (03, step 5); design §3.1: "a rest window with no bench
    # under it is thirteen minutes standing on a stick". None = no seating rule.
    rest_cadence_minutes: int | None = Field(default=None, ge=1)
    # EVERY CANDIDATE MUST SIT WITHIN THIS DISTANCE OF THE START — a constraint
    # on the whole loop, not on a leg: "a meltdown 25 minutes from the exit
    # means carrying a child for 25 minutes" (design §2.4); panel locked cost
    # D4: "cap DISTANCE FROM EXIT not just leg length" (03-panel-findings.md).
    escape_radius_m: int | None = Field(default=None, ge=1)
    # WHAT THE ROUTE MAY BE MADE OF. "no_stairs" (buggy, wheeled luggage) and
    # "step_free" (Rosemary) ride Valhalla's per-request pedestrian costing —
    # capability-proven live in W2.1 (phase2-ledger.md): step_penalty moves the
    # route off the Rue Foyatier stairs; "step_free" adds the wheelchair
    # profile. "any" = today's requests, byte-identical.
    route_surface: Literal["any", "no_stairs", "step_free"] = "any"
    # HOW THE NARRATION SPEAKS (design §2.4 bottom row) — carried on the input
    # now so presets can set it; consumed by the phase that reworks narration.
    # NEVER set by mobility ("slow the walking, never the talking") and
    # "warm" is warm, never romantic.
    narration_register: Literal["solo", "warm", "family"] | None = None
    # STOPS THE VISITOR PINNED (design §3.2) — corpus POI ids the visitor has
    # promoted into promises: "the visitor may pin any offered stop into a
    # promise, or release one back into fabric". The one mechanism that lets
    # one engine serve Théo (pins one thing absolutely) and Julien (pins
    # nothing, wants an open walk). Empty = nothing pinned = today's
    # behaviour, byte-identical.
    pinned_poi_ids: tuple[str, ...] = ()
    # THE SKY — FETCHED, NEVER ASKED (design §2.5, data row 6.8): the caller
    # fetches the forecast and hands the planner the decision it implies, so
    # the visitor is never questioned about it. Two values because that is
    # the whole decision (Phase 3 demo D3: the same request, dry vs rain).
    # None = no signal fetched, which is today's behaviour, byte-identical
    # planning — the `start_datetime` None-means-no-clock precedent.
    weather: Literal["dry", "rain"] | None = None
    # THE DIALS (Phase 4, W4.2 panel — all four rulings recorded in
    # phase4-ledger.md; every default is today's behaviour, byte-identical).
    # Adding fields moves every request_sha256 — declared, Phase 8 re-seals.
    #
    # "Fewer stops, longer at each" / "More stops, shorter at each". "more"
    # resolves to the proven short-stop ceiling (resolve_party_axes); "fewer"
    # concentrates the day in selection (drop the weakest, keep the anchors
    # whole). The bare stop-minutes ceiling was REJECTED as the fewer-mechanism
    # (a placebo in two of three measured starts).
    stop_density: Literal["fewer", "more"] | None = None
    # "Less talking / More talking" — narration SUPPLY, never topic (the panel
    # retired the topic-narrowing strawman unanimously). Phase 4 consumes it as
    # beats-per-stop; the full point-first mechanics are Phase 6's.
    narration_density: Literal["less", "more"] | None = None
    # "Skip the queues" — queue-heavy stops are DIMMED in selection (the score
    # channel), on top of Phase 3's queues-are-budgeted-never-credited rule.
    # Named plainly because "quieter" is retired as a word (a sound word —
    # Paulo; dangerous after dark — Sofia).
    avoid_queues: bool = False
    # "Less of THIS today" — place kinds excluded from dwell seating (Greta's
    # "not what I did yesterday", Julien's "zero museums today"). Excluded
    # kinds SWAP other places in (the lens behaviour), never starve the day
    # (the shaved-stop behaviour), and each exclusion is DISCLOSED on the
    # day's notes channel like a closed door.
    category_minus: tuple[str, ...] = ()

    @field_validator("start_datetime")
    @classmethod
    def _check_start_datetime(cls, v: str | None) -> str | None:
        # Plain words, because the API surfaces this text to the person who
        # typed the value (plan S1.2). fromisoformat is the whole grammar.
        if v is None:
            return v
        from datetime import datetime

        try:
            datetime.fromisoformat(v)
        except ValueError:
            raise ValueError(
                f"start_datetime {v!r} is not a valid date and time; "
                "use ISO 8601, e.g. '2026-08-11T10:00'"
            ) from None
        return v

    @field_validator("start", "end")
    @classmethod
    def _check_latlng(
        cls, v: tuple[float, float] | None
    ) -> tuple[float, float] | None:
        # `end` is optional; `start` is required so pydantic rejects None before here.
        # The rule itself is `corpus_places.valid_coordinates` — this wrapper exists
        # only for the None, and delegating rather than restating is what stops a
        # tightened bound from applying to a receipt's endpoints but not a request's.
        if v is None:
            return v
        return valid_coordinates(v)

    @field_validator("lenses")
    @classmethod
    def _normalize_lenses(cls, v: list[str] | None) -> list[str] | None:
        return normalized_lens_list(v)

    @model_validator(mode="after")
    def _end_round_trip_mutex(self) -> TourInput:
        # A user-given destination and "return to A" are contradictory (spec §1).
        # A field_validator can't see round_trip, so the cross-field check lives here.
        if self.end is not None and self.round_trip:
            raise ValueError("end and round_trip are mutually exclusive")
        return self


# THE §2.4 TABLE, TRANSCRIBED (01-design.md:118-126) — the one place the
# preset→axes mapping lives. Numbers with no design pin carry their basis here:
#  - family pace 2.0: "~half, variable" (03-family-with-children.md step 4).
#  - rest cadence 20: Nadia's toilet lands after ~19 accumulated walking
#    minutes (03 steps 2+4→5); the table gives the cadence to take-it-easy too
#    (Rosemary's benches, 05 steps 3 and 7 — see phase2-ledger.md).
#  - escape radius 800 m: the design's failure case is "a meltdown 25 minutes
#    from the exit"; 800 m is ~16 min back at the corpus pace, inside it with
#    margin, and matches Nadia's real day (never further than ~700 m from
#    Place des Vosges).
#  - take-it-easy pace 1.2 / with-luggage 1.25: the table says "slow" for
#    both with no number anywhere. take-it-easy was FIRST set to 1.5 (scaled
#    against family's pinned 2.0, "half") and CORRECTED to 1.2 after the W2.10
#    panel: three independent personas (Camille, Rosemary, Paulo) caught the
#    same real defect on the real corpus — 1.5x pace combined with Rosemary's
#    locked 12-minute leg cap made every nearby leg exceed the cap on the
#    FIRST hop, collapsing the day to one stop and 30 of 180 minutes. 1.2x
#    was verified empirically on the same real request (Place des Vosges,
#    180 min): four real stops including a seated rest bench, longest leg 9
#    min, comfortably under the cap — see phase2-ledger.md. The 12-minute cap
#    itself is Rosemary's own number and stays untouched; only the
#    interpolated pace moved.
#  - §2.4's family "short" longest-walk cell and with-luggage's "medium" cell
#    carry NO number in the design, the plan, or any persona — deliberately
#    not wired rather than invented (phase2-ledger.md).
_PARTY_AXES: dict[str, dict[str, object]] = {
    # No ceiling for solo: panel locked cost D6 — "party-preset stop ceiling
    # under 65 min DECAPITATES his day" (Théo, 03-panel-findings.md:193).
    "solo": {"narration_register": "solo"},
    # Warm, NEVER romantic (design §2.4:126).
    "couple": {"narration_register": "warm"},
    "family": {
        "walking_pace": 2.0,
        "rest_cadence_minutes": 20,
        "escape_radius_m": 800,
        "max_stop_minutes": 6,  # "Six minutes is the ceiling here, not the floor"
        "route_surface": "no_stairs",  # buggy
        "narration_register": "family",
    },
    "take_it_easy": {
        "walking_pace": 1.2,
        # The table's one numbered leg cap. 12 produced a ONE-STOP day on
        # Rosemary's own ground; her doctor's measured number is 13, and the
        # 13-minute day (Orangerie -> bench -> Orsay) is the one every demo
        # uses. OWNER RULING 2026-08-19 (Phase 6, ruling 4): raise to 13.
        "max_leg_minutes": 13,
        "rest_cadence_minutes": 20,
        "route_surface": "step_free",
        # register DELIBERATELY absent: "slow the walking, never the talking".
    },
    "with_luggage": {
        "walking_pace": 1.25,  # Marcus: "Slower than his normal pace" (04 step 2)
        "route_surface": "no_stairs",  # the cobbles half rides S2.7's costing
        "narration_register": "solo",
    },
}


def resolve_party_axes(inp: TourInput) -> TourInput:
    """Expand a party preset into concrete axes; an explicit axis ALWAYS wins.

    design §2.4:136-137: "the presets are shortcuts over axes, and the axes
    are what the planner reads". A None-able axis counts as explicit when it
    is non-None; `route_surface` (the one axis with a non-None default) counts
    as explicit when it appears in `model_fields_set`, so an explicitly-passed
    "any" beats the preset too. No party → the input comes back unchanged.
    Idempotent: re-resolving a resolved input moves nothing.
    """
    updates: dict[str, object] = {}
    # THE STOPS DIAL, "more" direction (Phase 4, W4.2): "More stops, shorter at
    # each" resolves to the proven short-stop ceiling — the measured a-stop20
    # shape — unless the caller set a ceiling explicitly (explicit wins, the
    # rule of this whole function). The "fewer" direction has no axis mapping:
    # it concentrates the day inside selection instead.
    if inp.stop_density == "more" and inp.max_stop_minutes is None:
        updates["max_stop_minutes"] = 20
    if inp.party is None:
        return inp.model_copy(update=updates) if updates else inp
    for axis, value in _PARTY_AXES[inp.party].items():
        if axis == "route_surface":
            if "route_surface" not in inp.model_fields_set:
                updates[axis] = value
        elif getattr(inp, axis) is None:
            updates[axis] = value
    return inp.model_copy(update=updates) if updates else inp


class Anchor(BaseModel):
    """ONE REVIEWED ANCHOR of a marquee place (Phase 7 S7.7 B; design §5.6 "segments";
    W7.2 R4, all eleven): a spot inside the place's footprint where a person PLACED the
    coordinates by hand — never derived from the pin — naming the beat sub-locations it
    stands for, its own radius, whether it is under a roof (GPS is useless there: a tap,
    never auto), and the sentence that argues it. The story of the stop is cut at these
    and only these (`src/tour/placement.py::place_segments`). Carried on the corpus record
    (`anchors` on poi-raw.json, JSON-encoded on the graph — the `opening_hours` precedent)
    so a POI with no reviewed anchors has none: the safe default, an uncut story."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str = Field(..., min_length=1)
    sub_locations: tuple[str, ...] = Field(..., min_length=1)
    lat: float
    lng: float
    radius_m: float = Field(..., gt=0)
    indoor: bool = False
    basis: str = ""



class POI(BaseModel):
    """A POI loaded from the Neo4j corpus, snapshot-frozen for selection."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str
    name: str
    tier: int = Field(..., ge=1, le=5)
    poi_role: str
    lat: float
    lng: float
    areas: tuple[str, ...] = ()
    beat_count: int = 0
    matching_lens_beat_count: int = 0
    # D1 place semantics are additive so legacy corpora remain loadable.  A
    # typed corpus may enter selection only after place_materialization has
    # validated these identities against its frozen manifest.
    canonical_place_id: str | None = Field(default=None, min_length=1)
    aliases: tuple[str, ...] = ()
    coordinate_provenance: CoordinateProvenance | None = None
    requires_dwell: bool = False
    vignette_eligible: bool = True
    # HOW LONG A VISITOR SPENDS AT THIS PLACE — the third hand on the tour clock.
    # Two numbers, because one cannot serve two visitors: Camille gives
    # Sainte-Chapelle 38 minutes inside plus 28 in the queue, while Theo gives the
    # same building 15 from the outside and never goes in
    # (docs/personas/02-dark-history-walker.md:77).
    #
    # `typical_duration_min` is the OUTSIDE number and already existed on the API's
    # POICreate (src/api/models/nodes.py); this is its selection-side twin.
    # `visit_seconds_inside` is None for a place with no interior — a street, a
    # bridge, a square. `visit_basis` is the sentence that argues for both, so a
    # reviewer who has never been to the city can judge the numbers.
    #
    # THE `0` DEFAULT IS LOAD-BEARING, not a placeholder. Until the capacity pass
    # has run over a city, every POI reports 0 seconds of visit time and dwell
    # falls back to the narration floor — i.e. exactly today's behaviour. That is
    # what makes this field safe to add before the data exists, and it is also why
    # a corpus that is only PARTLY priced silently under-books the unpriced stops
    # rather than failing loudly. tests/test_poi_visit_duration.py is the guard
    # that no city ships half-priced.
    visit_seconds_inside: int | None = None
    visit_basis: str = ""
    typical_duration_min: int = 0
    # WHEN THE PLACE CAN BE ENTERED (Docs/adr/0006) — additive, same style as
    # the visit-capacity trio above. `opening_hours` is the place's hours as
    # OpenStreetMap text ("Mo-Su 10:00-18:00; PH off"), read at planning time
    # by the opening-hours library in the city's own country; text the library
    # cannot read counts as unknown hours. None = no hours on record — the
    # same load-bearing null `visit_seconds_inside` uses, and the safe default
    # for a corpus the hours pass has not reached: a POI with no hours is NEVER
    # clock-excluded. `opening_hours_source` is where the text came from:
    # "map" (OpenStreetMap, spoken plainly) or "guess" (spoken as a guess);
    # None with text is treated as a guess. `opening_hours_basis` is the
    # sentence that argues for the text.
    opening_hours: str | None = None
    opening_hours_source: str | None = None
    opening_hours_basis: str = ""
    # THE DOOR (Docs/adr/0006), additive in the same style. `gated` is the
    # explicit door verdict — True: a door, gate or ticket line stands between
    # the street and the experience; False: the whole value stands in the
    # open; None: the gated pass has not reached this POI, the fail-open
    # direction (never clock-excluded on no claim). It ends the overload where
    # null hours meant both "no door" and "door, hours unknown".
    # `hours_closed_reports` counts the walkers who found a guessed door shut
    # (a closed report); it is written at run time, never by the upload, and
    # a count above zero makes the next walker's warning stronger.
    gated: bool | None = None
    hours_closed_reports: int = 0
    # WHAT KIND OF PLACE THIS IS (redesign 6.7): a closed vocabulary (gallery |
    # museum | church | square | arcade | market | park | garden | bridge |
    # street | monument | other) derived deterministically at $0. Phase 3's
    # category-diverse replacement is the consumer; Phase 1 surfaces it in the
    # harness printout. "" = the categoriser has not run.
    place_category: str = ""
    # WHAT THIS PLACE AFFORDS (redesign row 6.4, plan S2.6) — three AI
    # judgements with no external source, additive in the same style as the
    # trios above. False/"" = the pass has not reached this POI, the safe
    # direction: a corpus the pass never priced affords NOTHING rather than
    # everything. `children_can_run` (Nadia: 38 of her 55 place-minutes were
    # valuable because the kids could run — 03-panel-findings.md), `sit_and_talk`
    # (Fiona & Dev's two green chairs — docs/personas/09-couple-who-would-
    # rather-talk.md), `good_after_dark` (design §3.1's finish rule; consumed
    # only in Phase 3). `judgement_basis` is the one sentence arguing all three
    # — the whole audit trail, since this row has no OSM tag or observable fact
    # behind it.
    children_can_run: bool = False
    sit_and_talk: bool = False
    good_after_dark: bool = False
    judgement_basis: str = ""
    # WHAT THE LINE COSTS (redesign row 6.5) — additive, same style as the
    # opening-hours block above. A queue is a FOURTH kind of time (design
    # §3.3): not walking, not being-at-a-place, not narration — priced
    # separately, belonging to the day rather than the building, and excluded
    # entirely under a "wall" end. `queue_class` is the never-priced switch:
    # None = the audited queue pass has not reached this POI, and an unpassed
    # queue is NEVER priced — the safe default, and the reason the numbers
    # below may default to 0 without implying a real zero-minute line ("none"
    # is the audited claim that there IS no line; None is no claim at all).
    # `queue_minutes_peak` / `queue_minutes_offpeak` are the two prices;
    # `queue_peak_hours` is the JSON-encoded hour ranges saying when peak
    # applies (the `opening_hours` encoding precedent); `queue_basis` is the
    # sentence that argues for the values (the `*_basis` precedent).
    queue_class: Literal["none", "short", "long", "unpredictable"] | None = None
    queue_minutes_peak: int = 0
    queue_minutes_offpeak: int = 0
    queue_peak_hours: str = ""
    queue_basis: str = ""
    # HOW BIG THE PLACE IS ON THE GROUND (Phase 7 S7.3/S7.4; design §5.6, W7.2 R1):
    # the radius, in metres, within which a walker is AT this place — the place's
    # FOOTPRINT. The corpus has carried it on every record (`trigger_radius`, written
    # by upload_paris) and nothing in the engine read it: the phone drew one 10 m
    # circle for every place, a 140 m square and a doorway alike. THE one audio
    # placement rule (src/tour/placement.py) reads it and puts it on the wire. None =
    # the record was never measured (or the loader has not yet carried it — the hop is
    # S7.4): the rule falls to its door-sized default, never to a second circle.
    trigger_radius: float | None = Field(default=None, gt=0)
    # THE REVIEWED ANCHORS (Phase 7 S7.7 B; design §5.6 "segments"; W7.2 R4): the spots
    # inside the footprint where a person placed a chapter of the story — marquee
    # places only, Notre-Dame first. Empty = no chapters, the story is told whole.
    anchors: tuple[Anchor, ...] = ()

    @model_validator(mode="after")
    def _playback_flags_are_consistent(self) -> POI:
        if self.requires_dwell and self.vignette_eligible:
            raise ValueError("a dwell-required POI cannot be vignette eligible")
        return self


class PhysicalCue(BaseModel):
    """One physical_cue entry from a beat — Phase 7.5 uses cue + feature_type
    to compose synthesized openers and to gate the Area-cold-open hoist.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    cue: str
    direction: str | None = None
    feature_type: str | None = None


class BeatRef(BaseModel):
    """A NarrativeBeat reference carrying just what selection/ordering needs.

    Phase 3 added the optional ``script_body`` so generation can emit
    sentence-level traceable records without re-querying Neo4j. Phase 2
    selection/ordering ignores it; tests construct BeatRef without it.

    Phase 7.5 added optional ``physical_cues`` + ``pronunciation`` so the
    refined cold-open hoist (Fix 1) and synthesized opener (Fix 2) can
    compose deterministically without re-querying Neo4j.

    M7 added ``source_passage``/``source_chunk_slug``/``key_claims`` for the
    VERIFY layer: ``source_passage`` is the verbatim span the beat was
    extracted from (rapidfuzz-matched against the source chunk for
    provenance) and ``key_claims`` are the atomic facts a beat-cited
    sentence must follow from (the faithfulness entailment pass). All three
    are optional — the corpus extraction pipeline backfills them; until then
    the provenance/faithfulness checks skip beats that lack them.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str
    poi_id: str
    sub_location: str | None = None
    trigger_address: str | None = None
    narrative_function: str | None = None
    beat_type: str | None = None
    emotional_register: str | None = None
    beat_length_class: str | None = None
    est_spoken_seconds: int = 0
    word_count: int = 0
    entities: tuple[str, ...] = ()
    subject_tag: str | None = None
    lenses: tuple[str, ...] = ()
    active_status: str = "active"
    script_body: str | None = None
    physical_cues: tuple[PhysicalCue, ...] = ()
    pronunciation: str | None = None
    source_passage: str | None = None
    source_chunk_slug: str | None = None
    key_claims: tuple[str, ...] = ()
    # ``id`` remains the graph UUID until the D2 upload/API migration.  These
    # fields carry the durable corpus identity in parallel without breaking
    # legacy fixtures or persisted trip payloads.
    stable_beat_id: str | None = Field(default=None, min_length=1)
    place_plan_id: str | None = Field(default=None, min_length=1)
    requires_dwell: bool = False
    vignette_eligible: bool = True

    @model_validator(mode="after")
    def _playback_flags_are_consistent(self) -> BeatRef:
        if self.requires_dwell and self.vignette_eligible:
            raise ValueError("a dwell-required beat cannot be vignette eligible")
        return self


_SHA256_PATTERN = r"^[0-9a-f]{64}$"


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ValhallaLegReceipt(BaseModel):
    """Canonical request/result evidence for one successful pedestrian leg.

    ``TransitSegment.source == "valhalla"`` is only a label.  This receipt is
    the replayable evidence behind that label: exact requested endpoints, the
    canonical request and response, the routing configuration they used, and
    the measurements copied into the segment.  Legacy route-only objects may
    omit it; full geographic certification may not.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    requested_from: tuple[float, float]
    requested_to: tuple[float, float]
    request_json: str = Field(..., min_length=2)
    request_sha256: str = Field(..., pattern=_SHA256_PATTERN)
    routing_config_json: str = Field(..., min_length=2)
    routing_config_sha256: str = Field(..., pattern=_SHA256_PATTERN)
    response_json: str = Field(..., min_length=2)
    response_sha256: str = Field(..., pattern=_SHA256_PATTERN)
    seconds: int = Field(..., ge=0)
    distance_m: float = Field(..., ge=0)
    polyline: str = Field(..., min_length=1)

    _endpoints_are_real_places = field_validator(
        "requested_from", "requested_to"
    )(valid_coordinates)

    @model_validator(mode="after")
    def _canonical_payloads_match_fields(self) -> ValhallaLegReceipt:
        parsed: dict[str, object] = {}
        for name in ("request_json", "routing_config_json", "response_json"):
            raw = getattr(self, name)
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{name} is not JSON") from exc
            if not isinstance(value, dict) or _canonical_json(value) != raw:
                raise ValueError(f"{name} is not a canonical JSON object")
            parsed[name] = value

        if _text_sha256(self.request_json) != self.request_sha256:
            raise ValueError("request_sha256 does not match request_json")
        if _text_sha256(self.routing_config_json) != self.routing_config_sha256:
            raise ValueError("routing_config_sha256 does not match routing_config_json")
        if _text_sha256(self.response_json) != self.response_sha256:
            raise ValueError("response_sha256 does not match response_json")

        request = parsed["request_json"]
        assert isinstance(request, dict)
        expected_locations = [
            {"lat": self.requested_from[0], "lon": self.requested_from[1]},
            {"lat": self.requested_to[0], "lon": self.requested_to[1]},
        ]
        if request.get("locations") != expected_locations:
            raise ValueError("request_json locations differ from requested endpoints")
        # ``locations`` is the only per-leg material.  Every other top-level
        # request field is routing configuration and must therefore participate
        # in the build-bound hash.  An allowlist here would let a newly added
        # Valhalla option alter routing while retaining the old config identity.
        config = {key: value for key, value in request.items() if key != "locations"}
        if _canonical_json(config) != self.routing_config_json:
            raise ValueError("routing configuration differs from canonical request")

        response = parsed["response_json"]
        assert isinstance(response, dict)
        try:
            leg = response["trip"]["legs"][0]
            response_seconds = round(leg["summary"]["time"])
            response_distance_m = float(leg["summary"]["length"]) * 1000.0
            response_polyline = leg["shape"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ValueError("response_json lacks a complete Valhalla leg") from exc
        if response_seconds != self.seconds:
            raise ValueError("receipt seconds differ from response_json")
        if not math.isclose(response_distance_m, self.distance_m, rel_tol=0, abs_tol=1e-6):
            raise ValueError("receipt distance differs from response_json")
        if response_polyline != self.polyline:
            raise ValueError("receipt polyline differs from response_json")
        return self


class TransitSegment(BaseModel):
    """A walking segment between two ordered points along the route.

    ``walk_seconds``/``distance_m`` retain the planning fallback values;
    ``leg_seconds``/``leg_distance_m``/``polyline`` carry the road-network
    values when a RoutingClient produced them (``source`` records provenance).
    Route totals use the routed values whenever they exist.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    from_poi_id: str | None
    to_poi_id: str | None
    distance_m: float = Field(..., ge=0)
    walk_seconds: int = Field(..., ge=0)
    leg_seconds: int | None = Field(default=None, ge=0)
    leg_distance_m: float | None = Field(default=None, ge=0)
    polyline: str | None = None  # encoded polyline (6-digit precision), routed legs only
    source: Literal["valhalla", "haversine"] = "haversine"
    # HOW this leg is travelled. Walking is the only mode Release 1 plans, and the
    # field exists now so that adding transit later is an ADDITION rather than a
    # rewrite: every consumer that must branch on mode gets a compile-time-visible
    # place to do it, instead of "walking" being an unstated assumption baked into
    # every leg calculation in the codebase. A leg with no mode is a leg whose
    # duration means different things to different readers.
    mode: str = "walk"
    valhalla_receipt: ValhallaLegReceipt | None = None

    @model_validator(mode="after")
    def _receipt_matches_segment(self) -> TransitSegment:
        receipt = self.valhalla_receipt
        if receipt is None:
            return self
        if self.source != "valhalla":
            raise ValueError("a Valhalla receipt cannot back a fallback leg")
        if (
            self.leg_seconds != receipt.seconds
            or self.leg_distance_m is None
            or not math.isclose(
                self.leg_distance_m, receipt.distance_m, rel_tol=0, abs_tol=1e-6
            )
            or self.polyline != receipt.polyline
        ):
            raise ValueError("Valhalla receipt differs from copied segment fields")
        return self


class TourabilityAssessment(BaseModel):
    """Phase 6 density-gate result — §3.7 of phase-1-design.

    Lives on this contract module (not in ``density.py``) so ``Route``
    can carry it as an optional field without an import cycle. The
    formula and thresholds remain in ``src/tour/density.py``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: str  # "GREEN" | "YELLOW" | "RED"
    walk_radius_m: float
    fill_ratio: float
    # The pool's standing-still capacity, against the tour's standing-still
    # target. Both were named "audio" until 2026-08-06, when the planner's
    # currency moved from what a place SAYS to what a visitor SPENDS there.
    # They are a ratio, so both halves have to be the same currency or the
    # gate judges a pool in one unit against a target in another — the exact
    # drift `_target_dwell_seconds`'s docstring in density.py recounts.
    dwell_capacity_seconds: int
    target_dwell_seconds: int
    reachable_poi_count: int
    reachable_beat_count: int
    anchor_candidate_count: int
    cluster_compactness: float
    duration_min: int
    round_trip: bool
    max_supportable_duration_min: int | None = None
    one_way_alternative_destination: str | None = None
    # C11a: a GREEN-density pool can still DELIVER a thin route (the pool is rich
    # but the reachable/walk-affordable dwell POIs are few or beat-thin). When
    # True the route carries this assessment with status GREEN so the surface can
    # disclose "honest but thin" instead of silently reading fully-GREEN.
    delivered_thin: bool = False
    # Lens-specific fill: the audio-fill ratio counting ONLY beats matching the
    # tourist's requested lenses. None when no lenses were requested (the overall
    # fill_ratio is lens-agnostic, so two tourists with different interests at the
    # same start would otherwise see identical "thin" banners). Lets the surface
    # disclose "thin for dark_history" distinctly from "thin for hidden_history".
    on_lens_fill_ratio: float | None = None


class ReachVerdict(BaseModel):
    """REACH output (§2.1, M5): how the reachable area was computed and which
    mode the tour operates in.

    ``mode`` maps the density gate's status: GREEN → standard; YELLOW →
    ambient (thin) or redirect (a denser one-way destination exists); RED →
    refuse (selection raises before a Route exists, so a Route never carries
    refuse in practice). ``degraded`` is True when the Valhalla isochrone was
    unavailable and REACH fell back to the analytic haversine envelope.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: Literal["standard", "ambient", "redirect", "refuse"]
    degraded: bool = False
    walk_minutes: int = Field(..., ge=1)
    reachable_poi_count: int = Field(default=0, ge=0)
    alternative_destination: str | None = None


class ClockExclusion(BaseModel):
    """One POI the clock found closed on a dated tour, and the plain-English fact.

    Recorded so the day can SAY why the museum is missing (redesign 6.1 —
    Aiko's locked door, Rosemary's Tuesday). `reason` is the CLOSURE FACT,
    written for a person ("closed all day Wednesday"), and nothing else.

    `kept_outside` is the planner's DECISION, as a flag rather than as words:
    True when the closed place has an exterior worth standing at and so stayed
    in the pool at its outside price (W1.9 dissent 1 — a locked door is still a
    facade); False when there was nothing to see from the street and it left the
    pool. Two readers need the decision — the harness table marks outside-only
    stops, and the wire writes the traveller's sentence — and both used to
    recover it by string-matching the reason ("outside only"), which broke the
    moment the wording was made plain (W4.12). What the traveller is TOLD
    ("we will see it from the outside" vs "so it is not in your day") depends
    on whether the place actually ended up ON the route, which only the reader
    holding the route can know — so that sentence is composed there, never here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    poi_id: str
    name: str
    reason: str
    kept_outside: bool = False
    #: Whether the place is shut for the WHOLE day the walk happens on — the
    #: planner sets it from the day's own empty window list, so the voice can
    #: say "closed today" (Aiko must not plan a wasted return trip) instead of
    #: "closed at the moment" (true for a door that opens later, or shut for
    #: lunch). A decision in a field, never recovered from the reason's words.
    #: Additive: False keeps every existing exclusion byte-identical.
    all_day: bool = False
    #: Whether the excluded place sits AT the walk's own start — within its
    #: footprint (floored at 50 m) of the input coordinates. The walker can
    #: SEE that shut door, so setting off past it wordlessly is a lie by
    #: omission; the voice names an off-route at_start exclusion once, first.
    #: Set by the planner from coordinates only it holds — never recovered
    #: from the reason's words — and additive: False keeps every existing
    #: exclusion byte-identical.
    at_start: bool = False
    #: Whether the closure rests on GUESSED hours (Docs/adr/0006): any source
    #: but the map. Every voice hedges from this field — "we think", "could
    #: not confirm" — never from the reason's words, and a guessed closure
    #: never removes a place from the day. Additive: False keeps every
    #: existing exclusion byte-identical.
    guessed: bool = False
    #: How many walkers have reported this guessed door shut (rule 5), copied
    #: from the place's `hours_closed_reports` so the voice can say so. Read
    #: only when `guessed` is True; 0 keeps every existing exclusion
    #: byte-identical.
    closed_reports: int = 0


class PromiseShape(BaseModel):
    """The shape of one promised visit — the numbers the promise is made OF.

    design §3.1: a promise includes "the shape of the visit — 65 minutes
    *inside* is a different promise from 15 minutes outside, and the queue is
    a third number again." The three seconds are those three numbers.
    ``goes_inside`` says which side of the door this visitor's visit lives on;
    ``closed_today`` records that the clock has already voided the door, so
    the promise is the outside view and nothing inside is owed. All required:
    a promise never leaves its shape half-stated. Deliberately NO scores,
    weights, or priorities — see ``Promise``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    outside_seconds: int = Field(..., ge=0)
    inside_seconds: int = Field(..., ge=0)
    queue_seconds: int = Field(..., ge=0)
    goes_inside: bool
    closed_today: bool


class Promise(BaseModel):
    """One promised item of the day — OUTPUT, the planner's obligation.

    design §3.1: a planned day is "a small set of PROMISES on a clock,
    connected by FABRIC", and "fabric may change silently; promises may not"
    (§4.3). ``kind`` names the four species the design lists: the interest
    anchor, a stop the visitor pinned (§3.2), a body stop, and the finish.

    A Promise carries NO scoring state — no score, no weight, no priority.
    Promises are what the planner OWES after deciding, not knobs it reads
    while deciding: a §3.2 pin is the visitor's decision, not a preference to
    be traded off, and ``extra="forbid"`` refuses any field that would turn
    an obligation back into a candidate.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["anchor", "pinned", "rest", "finish"]
    poi_id: str
    shape: PromiseShape
    # THE WINDOW (Phase 4, W4.2 deviation v — the pre-commit surface shows
    # timed promise windows; F&D locked them COARSE, a window not a
    # minute-contract). Read off THE one arrival accumulation at assembly;
    # "" on a dateless day — additive, every existing Promise byte-identical.
    arrives_hhmm: str = ""
    departs_hhmm: str = ""


class ReplanContext(BaseModel):
    """What makes a replan RELATIVE — INPUT to THE one planner (Phase 5 S5.6).

    Design §4.6: "the server is the only place a plan decision is made"; a
    replan is `select_route` called again with this beside the request — never a
    second planner (plan §10.8.1, the argument Phase 4 used to collapse
    `plan_premium_options`). It carries what a fresh plan cannot know:

    - ``protected_poi_ids`` — §4.5.2's protected class as the CALLER defines it.
      For the "More breaks" dial (CARRIED 3) that is the base day's anchor and
      promises (W4.2 locked semantics 2: "never fund a rest by shaving an
      anchor"); for a session replan it is what the PERSON asked for — pins, a
      declared finish, the rests/meals/toilets the party or dial requested — and
      NOT the planner's own anchor on an open unpinned walk (W5.2 R1.5, Fiona &
      Dev and Julien by name). The planner seats them, never drops, folds or
      shaves them, and reports rather than refuses when they alone overrun.
    - ``longest_leg_ceiling_seconds`` — §4.5.3: the base day's longest street
      leg; a replan may not mint a longer one (Rosemary's 12 + 9 fusing into 21).
    - ``keep_to_poi_ids`` — the pool. None = any candidate; a tuple = ONLY these
      (a subset of the planned day, in whatever order fits): W5.2 R1.2/R1.4 — a
      skip's or an early band's answer never adds a building or new narration.
    - ``visited_poi_ids`` — already stood at or skipped; never re-seated.
    - ``spent_categories`` — Greta: the category walked away from is spent for
      the day (§4.5.5 read against the day AND the person's refusals).
    - ``floor_zero`` — a TAIL has no floor: one leg home with no stop is a legal
      answer (W5.2 R1.1, 11/11; W5.1 defect 6 — today's planner refuses 5 of 6
      tails). The density gate does not refuse a thin remainder, the underfill
      line does not bind, and a remainder that cannot fit its minutes comes back
      as the protected day with ``Route.overrun_seconds`` REPORTED, never a 422.
    - ``listening_rate`` — §4.1 (Paulo): scales the narration seconds left, never
      planned stop minutes. The learned walking pace rides ``TourInput.walking_pace``,
      the weather ``TourInput.weather``, the clock ``start_datetime``, the minutes
      left ``duration_min``, the terminus ``end`` — the request already has them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    protected_poi_ids: tuple[str, ...] = ()
    longest_leg_ceiling_seconds: int | None = Field(default=None, ge=0)
    keep_to_poi_ids: tuple[str, ...] | None = None
    visited_poi_ids: tuple[str, ...] = ()
    spent_categories: tuple[str, ...] = ()
    floor_zero: bool = True
    listening_rate: float = Field(default=1.0, ge=0.5, le=3.0)
    # MINUTES A DROP OR A SKIP ALREADY HANDED THESE PLACES (poi id -> seconds),
    # granted through the one drop primitive's grant rule before this replan runs
    # (a skipped stop's minutes, an early band's minutes — W5.2 R1.2/R1.4: they
    # LENGTHEN what is there); the planner seeds its extensions with them.
    visit_extension_seconds: dict[str, int] = Field(default_factory=dict)


class Route(BaseModel):
    """Selected POIs in walking order, with transit segments and budgets.

    Phase 6 added the optional ``tourability`` slot so selection can
    surface a YELLOW density assessment to the skill without raising.

    Phase 7.5 (Fix 3) added ``demoted_beats``: when two selected POIs
    sit within ~15m of each other and share an address-overlap signal,
    selection demotes the smaller-tier POI and merges its beats into
    the larger POI's pool. The mapping ``host_poi_id -> tuple[BeatRef]``
    lets the harness pull the demoted beats without re-querying.

    Track B (Step B.2) added ``vignettes``: walk-past vignette POIs along
    the legs — ``leg_idx -> POIs``, where leg ``i`` is the walk INTO stop
    ``i`` (matching ``transits`` indexing). ADDITIVE metadata only:
    selection populates it AFTER ordering, and ``pois``/``transits`` are
    byte-identical to a pre-vignette Route.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    pois: tuple[POI, ...]
    transits: tuple[TransitSegment, ...]
    total_walk_distance_m: float = Field(..., ge=0)
    total_walk_seconds: int = Field(..., ge=0)
    spine_area: str | None = None
    # Seconds this tour aims to spend standing still. Was target_audio_seconds.
    target_dwell_seconds: int = 0
    err_short_total_seconds: int = 0
    tourability: TourabilityAssessment | None = None
    demoted_beats: dict[str, tuple[BeatRef, ...]] = Field(default_factory=dict)
    # poi_id -> seconds this visitor spends AT that stop, priced ONCE in selection
    # against the place's capacity and the visitor's declared interest.
    #
    # WHY IT RIDES ON THE ROUTE. The obvious home is generation, where a stop's
    # length is set — but `_flatten_pois` receives no CorpusSnapshot, and pricing
    # needs one to read a POI's beat lenses. Its caller `generate()` has no
    # snapshot either. So the number is computed where the snapshot is already
    # open, in selection, and carried here. That also means it is priced once
    # rather than twice, so the planner and the served tour cannot disagree
    # about how long a stop lasts.
    #
    # Empty is safe and means "nobody priced this route": generation floors every
    # stop at its narration, which is exactly today's behaviour. Four harnesses
    # build a Route without it (score_saved_tours, score_gold_text,
    # human_reference_tours, and the repair's throwaway trial) and inherit that.
    planned_visit_seconds: Mapping[str, int] = Field(default_factory=dict)
    # THE HONESTY SURFACE'S PER-STOP FACTS (Phase 4, W4.2 deviation v — the
    # panel ruled unanimously that queue minutes and the in/out shape must be
    # visible BEFORE a person commits to a day). Priced at each stop's ordered
    # ARRIVAL hour by the same shape closure the visits use, at the same one
    # site, so the surface and the budget can never disagree. Empty = unpriced
    # (dateless/legacy routes) = nothing rendered; the safe identity default.
    planned_queue_seconds: Mapping[str, int] = Field(default_factory=dict)
    visit_goes_inside: Mapping[str, bool] = Field(default_factory=dict)
    # THE PLACED OUTSIDE SECONDS per stop (Phase 7 S7.6; design §5.6 threshold
    # silence) — the same shape, priced at the same one site and the same arrival
    # hour as the two maps above, carried so the audio placement rule can say how
    # long a piece has before the door: the only threshold a phone can see under a
    # roof. Empty = unpriced = no door anywhere; the safe identity default.
    planned_outside_seconds: Mapping[str, int] = Field(default_factory=dict)
    # HOW MUCH SHORTER THAN ASKED this tour actually runs, in seconds, and 0 when
    # it is not short enough to be worth saying.
    #
    # WHY IT EXISTS. Duration is a CEILING, not a contract to fill. When a corridor
    # cannot honestly reach the requested length, the right answer is a shorter
    # tour with a sentence saying so — "this is 4h40 rather than 5h; the area does
    # not support more" — and not a refusal, and never a route that walks you
    # across the city to pad the clock. That padding is the bug this release
    # exists to remove, so the disclosure is the thing that makes removing it safe.
    #
    # WHY ON THE ROUTE AND NOT THE TOURABILITY PAYLOAD. `tourability` is attached
    # only when the assessment is YELLOW or the delivery is thin
    # (`_attach_tourability_if_yellow`), so it is None on a GREEN, richly-
    # delivered, honestly-short tour — exactly the tour that needs to say this.
    # A disclosure that rides a channel which is null in its own case discloses
    # nothing.
    elapsed_shortfall_seconds: int = Field(default=0, ge=0)
    # HOW FAR OVER ITS MINUTES a REPLANNED remainder runs, in seconds — 0 for every
    # fresh plan (a fresh plan over its ceiling is REFUSED, never served). Under a
    # `ReplanContext` the protected stops alone may not fit the minutes left
    # (Rosemary at her bench, 20.5 minutes from the Orsay with 15 left); the
    # planner then hands back the protected day and SAYS the overrun here, because
    # a tail is not a request the phone can take a 422 for (W5.2 R1.1) — the
    # overrun is the raw material of the ONE question (§4.2), never a refusal.
    overrun_seconds: int = Field(default=0, ge=0)
    # HOW FAR the longest street leg of a REPLANNED remainder runs over the person's
    # per-leg limit, in seconds — 0 for every fresh plan (a fresh plan is refused at
    # the street certification, S5.4). A tail's walk home longer than the limit is a
    # fact to say, not a 422 (W5.2 R1.1; Rosemary: "the entry must say where I sit on
    # the way"): S5.11's one line reads it.
    leg_cap_breach_seconds: int = Field(default=0, ge=0)
    # Track B (Step B.2): leg_idx -> walk-past vignette POIs on that leg.
    vignettes: dict[int, tuple[POI, ...]] = Field(default_factory=dict)
    # WHO THE CLOCK EXCLUDED, AND WHY (redesign 6.1) — additive metadata in the
    # `vignettes` mould: populated only on a dated request, default empty, so
    # every existing Route is byte-identical. ON THE ROUTE for the same reason
    # `elapsed_shortfall_seconds` gives above: `tourability` is None on exactly
    # the GREEN route that still needs to say "the museum is closed today", and
    # a disclosure riding a channel that is null in its own case discloses
    # nothing.
    clock_exclusions: tuple[ClockExclusion, ...] = ()
    # WHAT THIS ROUTE PROMISES (design §3.1) — additive metadata in the
    # `vignettes` mould: populated only by the promise-native planner, default
    # empty, so every existing Route is byte-identical. Output, never input:
    # these are the obligations the planner swore for this day, carried so
    # downstream phases can protect them through every replan ("fabric may
    # change silently; promises may not", §4.3).
    promises: tuple[Promise, ...] = ()
    # M2 routed-metadata slots. ``routed`` is True iff every transit leg came
    # from Valhalla. ``route_polyline`` (stitched whole-route shape) and
    # ``backtrack_ratio``/``flow_score`` keep their defaults until the
    # milestones that compute them (M3/M4) land.
    routed: bool = False
    route_polyline: str | None = None
    backtrack_ratio: float = Field(default=0.0, ge=0)
    flow_score: float = Field(default=0.0, ge=0)
    # M5: the REACH verdict for the request that produced this route.
    reach: ReachVerdict | None = None
    # C9 governor: the fixed-end-B / pulled-endpoint POI id — EXEMPT from the
    # per-stop audio-share cap (it may dominate). None for round trips / open walks.
    fixed_end_poi_id: str | None = None
    # C9 governor: the POSITIONAL start-anchor — the first-seated POI in the greedy
    # (decision 3, "may dominate") — EXEMPT from the cap. Persisted here because
    # Held-Karp reorders ``pois`` after greedy, so ``pois[0]`` is NOT the anchor,
    # and compose/golden harnesses have no access to the greedy locals. None on
    # A→B (no positional start-anchor is seated) and for empty routes.
    start_anchor_poi_id: str | None = None


OrderingStrategy = Literal["sub_location", "trigger_address", "narrative_function"]


class POIBeats(BaseModel):
    """Ordered beats for a single POI under one of the three strategies."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    poi_id: str
    poi_name: str
    ordering_strategy: OrderingStrategy
    beats: tuple[BeatRef, ...]


class BeatSequence(BaseModel):
    """The ordered beat plan for a Route — Phase 3 turns this into a Script.

    Track B (Step B.4, seam locked by the plan's adversarial review m-11)
    added the ADDITIVE ``vignette_beats``: ``leg_idx -> chosen walk-past
    beats`` (leg ``i`` = the walk INTO stop ``i``), ONE best beat per
    vignette POI, built by callers from ``Route.vignettes`` + the snapshot
    (see ``beat_select.select_vignette_beats``). The stitcher voices each as
    a single beat-cited one-liner in that leg's transit stage. They are NOT
    ``POIBeats`` entries — anchor blocks never see them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    poi_beats: tuple[POIBeats, ...]
    vignette_beats: dict[int, tuple[BeatRef, ...]] = Field(default_factory=dict)
    # C9g: per-POI overflow beat ids — the beats the governor trimmed off a
    # dominating stop. Carried here so _flatten_pois can surface them on
    # ScriptPOI.overflow_beat_ids (keep-exploring extras) instead of silently
    # dropping them. ``poi_id -> ordered overflow beat ids``; empty when the
    # governor did not fire (the common case).
    overflow_by_poi: dict[str, tuple[str, ...]] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Phase 3 — Script (final output) + validation
# ---------------------------------------------------------------------------


# The fixed-end sentinel (selection._materialize_fixed_end_b): an A→B request
# whose end point is not within snapping distance of any seated place gets a
# synthetic POI at B's exact coordinate so the ordering can pin it as the last
# stop. It is a WAYPOINT, not a place — no beats, no dwell — and every surface
# that meets it must know that: the narrator never voices its name
# (generation.py), and the wire flags the card so a screen can say "ends at
# your finish point" instead of counting a zero-minute stop (W4.12 closing
# panel: "Destination: 0 min · outside" listed as stop 5 of 5, and promised as
# `finish Destination 12:50-12:50`; Sofia: "a winter tour must never end at a
# nameless place in the dark"). ONE definition, read by selection, generation
# and options — it used to be spelled twice.
END_B_SENTINEL_PREFIX: str = "__end_b__"
END_B_SENTINEL_NAME: str = "Your finish point"


SourceType = Literal["beat", "glue", "arith"]


class Sentence(BaseModel):
    """A single audio sentence with full source attribution.

    ``source_id`` is either a NarrativeBeat UUID (when source_type=='beat')
    or a whitelisted glue/arith label (when source_type in {'glue','arith'}).

    ``also_cites`` (multi-beat citation): the ADDITIONAL beat ids a composed
    sentence draws on when it FUSES two beats' overlapping tellings of one fact
    into a single sentence. Empty for a plain (single-source) sentence. VERIFY
    entails a fused sentence against the UNION of ``source_id`` + ``also_cites``
    beats, so a cross-book merge is faithful instead of failing on the cited
    beat alone. Every id must still trace to a real beat.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    source_id: str
    source_type: SourceType
    stop_idx: int = Field(..., ge=0)
    also_cites: tuple[str, ...] = ()

    @property
    def cited_beat_ids(self) -> tuple[str, ...]:
        """All beat ids this sentence draws on (primary + fused), when it is a
        beat sentence; empty for glue."""
        if self.source_type != "beat":
            return ()
        return (self.source_id, *self.also_cites)


class ScriptPOI(BaseModel):
    """A flattened POI record for the Script's selected_pois roster."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str
    tier: int = Field(..., ge=1, le=5)
    lat: float
    lng: float
    area: str | None = None
    dwell_seconds: int = 0
    beat_ids: tuple[str, ...] = ()
    # C9 governor: ordered overflow beats capped OUT of this stop's plan — the
    # "keep exploring here" extras. Empty for exempt anchors and thin stops.
    overflow_beat_ids: tuple[str, ...] = ()


class RouteOptionStop(BaseModel):
    """One ordered card inside a RouteOption (§2.8).

    THREE bands, and a card is exactly one of them:
    - ``dwell`` — a stop the tourist stands at. ``minutes`` is its dwell time.
    - ``vignette`` — a walk-past one-liner. ``minutes`` is always 0.
    - ``leg`` — narration spoken WHILE WALKING into the next dwell stop (product
      ruling 2026-07-19: "Audio overlaps the walking. It is a part of the tour
      experience."). ``minutes`` is the WALK's duration, so a six-minute walk sits
      next to seven seconds of narration and the gap is visible rather than averaged
      away. Its ``poi_id``/``lat``/``lng`` are the ARRIVAL stop's, and its ``name``
      reads "Walk to <that stop>". A consumer matching POI ids must therefore filter
      to ``band == "dwell"``; a leg card deliberately repeats its arrival stop's id
      rather than inventing one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    poi_id: str
    name: str
    lat: float
    lng: float
    lens: str | None = None  # dominant lens of the stop's beats; None if unlensed
    visit_or_walk_past: Literal["visit", "walk_past"] = "visit"
    minutes: int = Field(default=0, ge=0)
    # Phase 3 spotlight model (spec s7). Additive with behavior-preserving
    # defaults: until Step 3.5 wires the spotlight effect into selection, every
    # stop is a full dwell with a zero score, so nothing downstream changes.
    band: Literal["dwell", "vignette", "leg"] = "dwell"  # spotlight output band (spec s3)
    spotlight: float = Field(default=0.0, ge=0)  # the computed spotlight score
    # The text this card voices. Empty only for a dwell stop whose stop_idx carried no
    # stationary sentence. A leg card is NEVER emitted with empty narration, and a
    # vignette card is never emitted without a voiceable one-liner.
    narration: str = ""
    # KE9: this dwell stop has "keep exploring here" extras — beats the time budget
    # capped out. Always False on vignette and leg cards.
    has_deeper_dive: bool = False
    # THE FIXED-END WAYPOINT, flagged (W4.12). True only on the card for the
    # A→B sentinel (END_B_SENTINEL_PREFIX): the place a person asked to END at
    # when no seated stop is near it. Zero minutes, no beats, not a place — a
    # screen renders it as "ends at your finish point", never as a numbered
    # stop, and never counts it. Additive with a False default, so every earlier
    # payload and every consumer that ignores it is untouched.
    is_finish_point: bool = False
    # THE HONESTY SURFACE (Phase 4, W4.2 deviation v — the panel ruled queue
    # minutes and the in/out side of the door must be visible BEFORE a person
    # commits). Priced at this stop's ordered arrival hour by the planner (the
    # Route's own maps); 0 / None on unpriced (dateless/legacy) plans, on
    # vignette and leg cards, and on every pre-Phase-4 payload — additive.
    queue_minutes: int = Field(default=0, ge=0)
    goes_inside: bool | None = None


class RouteOption(BaseModel):
    """§2.8 output contract — one per flavour, 2-3 per request (M6).

    Fields owned by later milestones keep honest defaults until they land:
    ``stop_audio`` and ``why_this_works`` are M7 (COMPOSE/VERIFY);
    ``route_polyline``/``flow_score``/``backtrack_ratio`` pass through the
    Route slots M3/M4 left default; ``profiles`` is §4 multi-profile;
    ``offline_package`` is the offline-replay bundle, post-MVP-core.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    route_id: str
    stops: tuple[RouteOptionStop, ...]
    stop_audio: dict[int, str] = Field(default_factory=dict)
    route_polyline: str | None = None
    eta_seconds: int = Field(..., ge=0)  # honest routed legs + dwell
    # How much SHORTER than the requested duration this option runs, in seconds,
    # and 0 when it is not short enough to be worth saying. Carried per option
    # because each flavour is its own route with its own length. See
    # Route.elapsed_shortfall_seconds for why this is not on the tourability
    # payload: that payload is null on exactly the tour that needs to say this.
    elapsed_shortfall_seconds: int = Field(default=0, ge=0)
    why_this_works: str | None = None
    lens_summary: dict[str, int] = Field(default_factory=dict)
    flow_score: float = Field(default=0.0, ge=0)
    backtrack_ratio: float = Field(default=0.0, ge=0)
    degraded: bool = False
    profiles: tuple[str, ...] = ()
    offline_package: dict | None = None
    # Phase 3 spotlight model (spec s7): per-corridor lens density surfaced to
    # the user. None until REACH measures and fills it (later in Phase 3), so
    # the default preserves today's behavior.
    lens_coverage_note: str | None = None


class StopCorrectionCounts(BaseModel):
    """Per-stop correct-loop outcome counts (ported from the
    correct-don't-reject branch; its spec tree is retired — git history).

    ``verified`` = shipped beat-cited sentences that came through the gate
    unmodified; ``corrected`` = shipped sentences whose text was rewritten or
    trimmed by the correction ladder; ``floored`` = beats whose verbatim
    post-dedup stitch block was floored in; ``floored_audio_seconds`` = the
    spoken-audio seconds of those floored stitch blocks — how much of the stop's
    audio degraded to raw stitch. Telemetry only — never part of ``passed``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    verified: int = 0
    corrected: int = 0
    floored: int = 0
    floored_audio_seconds: int = 0


class ValidationReport(BaseModel):
    """Source-traceability + forbidden-phrase + (M7) provenance/faithfulness
    result for a Script. ``passed`` gates serving — a failing report blocks
    audio (§2.6).

    M7 added two independent VERIFY "teeth":
    - ``provenance_failures``: (beat_id, rapidfuzz_score) for beats whose
      ``source_passage`` did not match its source chunk above threshold.
    - ``faithfulness_failures``: (sentence, reason) for beat-cited sentences
      that the entailment pass found unsupported by the beat's ``key_claims``.

    The 2026-07 compose-safety addition closes the deletion blind spot the other
    checks share (they all police INVENTION, none policed DROPPED content):
    - ``coverage_failures``: (beat_id, dropped_claim) for a key_claim that the
      pre-compose (stitched) script voiced but NO composed sentence still
      realizes — i.e. compose silently deleted a distinct fact. Empty (a no-op)
      unless the verifier is built with the pre-compose claim set.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Correct-loop telemetry, ported with the corrector. Additive defaults;
    # TELEMETRY ONLY — never part of ``passed``.
    # A TUPLE of (stop_idx, counts) pairs, NOT a dict. ValidationReport is
    # frozen=True and every other field is a tuple; a dict field silently breaks
    # ``hash(ValidationReport())`` with "unhashable type: 'dict'" — and the test
    # bar stays GREEN because nothing in it hashes the model. Use
    # ``.correction_counts_by_stop`` for dict-style access.
    stop_correction_counts: tuple[tuple[int, StopCorrectionCounts], ...] = ()
    glue_dropped: int = 0
    affirm_reject: int = 0
    # Correction-ladder CALLS consumed against the per-tour budget (2 x stop_count).
    # 0 on the offline floor-only path or a clean tour. Telemetry only.
    corrections_spent: int = 0

    @property
    def correction_counts_by_stop(self) -> dict[int, StopCorrectionCounts]:
        """Dict view of ``stop_correction_counts`` for callers that want lookup.
        The stored form is a tuple so the frozen model stays hashable."""
        return dict(self.stop_correction_counts)

    untraceable_sentences: tuple[Sentence, ...] = ()
    forbidden_phrase_hits: tuple[tuple[Sentence, str], ...] = ()
    provenance_failures: tuple[tuple[str, float], ...] = ()
    faithfulness_failures: tuple[tuple[Sentence, str], ...] = ()
    coverage_failures: tuple[tuple[str, str], ...] = ()

    # Did the faithfulness pass actually RUN? An empty ``faithfulness_failures``
    # used to mean both "checked and clean" and "never checked", and
    # ``build_full_verifier`` quietly substituted a checker that approves
    # everything when the caller passed none — so a report could read as
    # verified having verified nothing. ``scripts/tour_build.py`` did exactly
    # that, which is how tours the owner read carried clean-looking reports.
    # Defaults False: a report that does not say it was checked was not.
    # TELEMETRY/HONESTY only — deliberately NOT part of ``passed``, because an
    # unchecked tour is not thereby a FAILING tour; it is an unverified one, and
    # conflating the two would block every offline path.
    faithfulness_checked: bool = False

    @property
    def passed(self) -> bool:
        """Structural failures BLOCK. Entailment and dropped-fact reports ADVISE.

        OWNER RULING 2026-08-03. Measured that day on a real Paris tour, the first ever
        run with these two wired to the workbench: ``0 untraceable, 1 forbidden, 0
        provenance, 28 faithfulness, 6 coverage``. Of the 28, exactly ONE was a verified
        invention. Sampled false alarms included "Look hard at the façade, because it's
        a manifesto of Art Nouveau" — the corpus's own sentence with two words changed —
        and a dropped-fact report for "Square named for Henri IV" on a tour that says
        precisely that in different words, because ``verify_claim_coverage`` counts
        shared WORDS and a paraphrase therefore reads as a deletion.

        A check that fires on a fifth of a good tour cannot decide whether to ship it,
        and while these two gated, EVERY tour was refused. The rule: a check may BLOCK
        only when it can NAME the specific thing that is wrong; a check that can only
        say "I don't know" RECORDS.

        This is a DEMOTION, not a removal. Both still run, both stay on this report, and
        both are logged per-sentence by the preview route, so the evidence a human needs
        is strictly better than before. ``faithfulness_checked`` still distinguishes
        "checked and clean" from "never checked".

        They return to blocking when the scored replacement lands: per-CLAIM scores
        carrying the unsupported SPAN, bands rather than an average, and a repair pass
        before anything is cut (planned in the crash-never-silent run's addendum;
        its spec tree is retired — git history).

        The three that remain are cheap, exact, and name their subject: an unknown cited
        beat id, a banned phrase, a source passage that does not match its chunk.
        """
        return not (
            self.untraceable_sentences
            or self.forbidden_phrase_hits
            or self.provenance_failures
        )


class StopVerifyStatus(BaseModel):
    """Per-stop outcome of the per-chapter compose gate (diagnostic, additive).

    ``status`` is:
    - ``composed`` — the stop kept its AI-voiced (rewritten/fused) narration;
    - ``partially_reverted`` — a SURGICAL repair kept the stop's passing composed
      sentences but spliced ONLY the specific failing beat(s) back to their
      grounded stitch (``restored_beats`` names them). The honest middle: the stop
      still speaks in the AI voice except for the one beat whose fusion the gate
      over-rejected;
    - ``reverted_to_stitched`` — the whole stop rolled back to the grounded stitch
      (all its beat content failed, or the surgical splice itself did not verify and
      the whole-stop safety net fired).
    The reason tuples explain WHY the gate acted on the stop — the exact VERIFY
    categories that fired against that stop's composed sentences, so a caller (or a
    test) can see per-stop reasons WITHOUT re-running the compose. Empty tuples mean
    that category was clean for this stop.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    stop_idx: int = Field(..., ge=0)
    status: Literal["composed", "partially_reverted", "reverted_to_stitched"]
    # beat ids the surgical repair rolled back to their grounded stitch while
    # keeping the rest of the stop composed. Non-empty only for partially_reverted.
    restored_beats: tuple[str, ...] = ()
    # "source_id: reason" for each beat/reflection sentence the entailment gate
    # rejected (the dominant cause of a revert on a bold, faithful fusion).
    faithfulness: tuple[str, ...] = ()
    # "beat_id: dropped_claim" for each pre-compose claim no composed sentence
    # still realized (a fusion that lost a fact, or a dropped fact-carrying line).
    coverage: tuple[str, ...] = ()
    # forbidden-phrase / invented-proper-noun|year codes on this stop's glue.
    forbidden: tuple[str, ...] = ()
    # source_ids of sentences that failed source-traceability.
    untraceable: tuple[str, ...] = ()


class Script(BaseModel):
    """Final tour-builder output. §3.6 of phase-1-design."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    city_slug: str
    generated_at: str
    inputs: TourInput
    total_audio_seconds: int = Field(..., ge=0)
    total_walking_seconds: int = Field(..., ge=0)
    total_walk_distance_m: int = Field(..., ge=0)
    total_planned_seconds: int = Field(..., ge=0)
    selected_pois: tuple[ScriptPOI, ...]
    lens_coverage: dict[str, int]
    script: tuple[Sentence, ...]
    validation: ValidationReport
    # Per-stop compose-gate diagnostic, populated only by
    # ``compose_script_per_chapter`` (empty everywhere else — additive, off the
    # hot path). Lets a caller see which stops kept the AI voice vs reverted to
    # the stitch, and the exact VERIFY reason for each revert, without re-running.
    verify_report: tuple[StopVerifyStatus, ...] = ()
