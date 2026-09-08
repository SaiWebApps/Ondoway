"""Pydantic models for trip generation endpoints."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from src import city_registry
from src.tour.candidate_eligibility import CandidateRejection
from src.tour.contract import RouteOption, normalized_lens_list
from src.tour.placement import PlacementPolicy, StopSegment, StopTrigger

#: The party vocabulary, wire-form tolerant: the workbench (and any URL-ish
#: caller) spells presets with hyphens; the engine's TourInput spells them with
#: underscores. Normalized HERE, at the edge, so exactly one spelling exists
#: inside the engine.
_PARTY_PRESETS = ("solo", "couple", "family", "take_it_easy", "with_luggage")


def _normalize_party(v: str | None) -> str | None:
    if v is None:
        return None
    slug = v.strip().lower().replace("-", "_")
    if not slug:
        return None
    if slug not in _PARTY_PRESETS:
        raise ValueError(
            f"party must be one of {sorted(_PARTY_PRESETS)} "
            f"(hyphens accepted), got {v!r}"
        )
    return slug


def _validate_city_slug(v: str) -> str:
    """Reject an unknown city at the edge (clear 422) instead of loading an
    empty corpus and emitting a broken/thin tour.

    Validates against ``servable_cities()`` (evaluated per-request, not the
    import-time ``SUPPORTED_CITIES``) so the prod cloud-filter takes effect: a
    locally-onboarded but not-yet-deployed city is rejected by the public prod
    /trips API while still being tourable by the local workbench."""
    slug = (v or "").strip().lower()
    allowed = city_registry.servable_cities()
    if slug not in allowed:
        raise ValueError(f"city_slug must be one of {sorted(allowed)}, got {v!r}")
    return slug


class TripGenerateRequest(BaseModel):
    """Input model for POST /trips/generate."""

    profile_id: str = Field(..., description="Profile node whose PREFERS_LENS edges select beats")
    center_lat: float = Field(..., ge=-90, le=90, description="Latitude of search center")
    center_lng: float = Field(..., ge=-180, le=180, description="Longitude of search center")
    end_lat: float | None = Field(
        default=None,
        ge=-90,
        le=90,
        description="Optional fixed-destination latitude (B). With end_lng, threads into "
        "TourInput.end; mutually exclusive with round_trip (rejected by the engine).",
    )
    end_lng: float | None = Field(
        default=None,
        ge=-180,
        le=180,
        description="Optional fixed-destination longitude (B). Pairs with end_lat.",
    )
    radius_m: int = Field(default=3000, le=10000, description="Search radius in meters")
    max_stops: int = Field(default=10, le=30, description="Cap on itinerary items")
    duration_min: int | None = Field(
        default=None,
        ge=1,
        le=600,
        description="Total trip budget in minutes (engine cap: 600)",
    )
    # Bounds how long the party STANDS at any one stop — not how long the tour
    # talks. Five minutes is the floor because generation floors every stop at its
    # own narration (capped at 270 s), so a ceiling under 300 s could not be
    # honoured and would be a promise the tour breaks. Validated HERE rather than
    # only on TourInput so a bad value is a 422 to the caller instead of a
    # pydantic error surfacing as a 500. Trimming narration to fit a shorter
    # ceiling is Release 2.
    max_stop_minutes: int | None = Field(
        default=None,
        ge=5,
        le=600,
        description="Optional ceiling on time spent at any single stop, in minutes (min 5)",
    )
    # THE PLANNER'S CLOCK (redesign §2.2/§2.3). Declared on BOTH request models
    # for the same reason max_stop_minutes records above: neither model sets
    # model_config, so an undeclared field would be silently dropped rather
    # than rejected, and the caller would get a clockless tour with no error
    # saying why. Validation of the string itself lives on TourInput
    # (src/tour/contract.py), the one home of that rule.
    start_datetime: str | None = Field(
        default=None,
        description="When the walk starts, local ISO 8601 (e.g. '2026-08-11T10:00'); "
        "None = dateless planning, exactly as before this field existed",
    )
    end_hardness: Literal["wall", "firm", "open"] = Field(
        default="firm",
        description="How hard the end is: 'wall' keeps visible spare minutes, "
        "'firm' is today's behaviour, 'open' never pads toward the requested length",
    )
    # WHO IS WALKING + THE DIALS (Phase 4, W4.2 panel — declared on BOTH request
    # models for the same silently-dropped-field reason max_stop_minutes records
    # above; every default is today's behaviour). See src/tour/contract.py for
    # each axis's meaning; the route threads them onto TourInput verbatim.
    party: str | None = Field(
        default=None,
        description="Party preset (solo|couple|family|take-it-easy|with-luggage; "
        "hyphens or underscores)",
    )
    walking_pace: float | None = Field(default=None, ge=1.0)
    max_leg_minutes: int | None = Field(default=None, ge=1)
    rest_cadence_minutes: int | None = Field(default=None, ge=1)
    weather: Literal["dry", "rain", "auto"] | None = Field(
        default=None,
        description="'auto' fetches the forecast for start_datetime at the start "
        "point (fail-open: no answer plans as no signal)",
    )
    pinned_poi_ids: list[str] = Field(default_factory=list)
    stop_density: Literal["fewer", "more"] | None = None
    narration_density: Literal["less", "more"] | None = None
    avoid_queues: bool = False
    category_minus: list[str] = Field(default_factory=list)

    _normalize_party_preset = field_validator("party")(_normalize_party)

    start_date: str = Field(..., description="ISO date for the trip start")
    end_date: str = Field(..., description="ISO date for the trip end")
    start_time: str = Field(default="09:00", description="Daily start time (HH:MM)")
    kid_friendly_only: bool = Field(default=False, description="Filter for kid-friendly POIs")
    trip_name: str | None = Field(
        default=None, description="Optional trip name; auto-generated if omitted"
    )
    lenses: list[str] | None = Field(
        default=None,
        description="Lens slugs to bias selection; precedence: request -> profile -> city default",
    )
    round_trip: bool = Field(
        default=False, description="Return to the start point (loops the route)"
    )
    city_slug: str = Field(
        default="paris",
        description="City corpus to tour; validated against city_registry.servable_cities()",
    )

    _validate_city = field_validator("city_slug")(_validate_city_slug)

    @field_validator("start_time")
    @classmethod
    def validate_start_time(cls, v: str) -> str:
        """Validate HH:MM format."""
        parts = v.split(":")
        if len(parts) != 2:
            raise ValueError("start_time must be in HH:MM format")
        try:
            hour, minute = int(parts[0]), int(parts[1])
        except ValueError:
            raise ValueError("start_time must be in HH:MM format") from None
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("start_time must be a valid time (00:00 - 23:59)")
        return v

    @field_validator("lenses")
    @classmethod
    def normalize_lenses(cls, v: list[str] | None) -> list[str] | None:
        """Drop blanks/whitespace; empty -> None — by calling THE one rule in
        src/tour/contract.py (this used to restate it and "mirror" the other)."""
        return normalized_lens_list(v)


class GeneratedStop(BaseModel):
    """Output model for each stop in a generated trip."""

    sort_order: int
    stop_id: str | None = Field(
        default=None,
        description="ItineraryItem id — addresses this stop for per-stop narration audio (Phase 1)",
    )
    poi_id: str
    poi_name: str
    lat: float
    lng: float
    #: The primary story beat. None for a stop that HAS no story — a rest (a bench
    #: seated by the party or the "more breaks" dial carries no beat). Until
    #: 2026-08-18 this was required, so every day that seated a rest 500'd at
    #: generate on the app path (the preview path uses another model and never saw
    #: it). The class: a contract written for story stops applied to a rest.
    beat_id: str | None = None
    lens_name: str | None = None
    lens_display: str | None = None
    duration_min: int
    importance_tier: int
    start_time: str
    script_body: str | None = None
    narration: str | None = Field(
        default=None,
        description="Stitched per-stop narration (cold-open, beats, transit, closing) voiced as "
        "this stop's audio (Phase 1); exposed for web verification (Phase 1.5).",
    )
    audio_url: str | None = None
    audio_duration_sec: float | None = None
    beat_ids: list[str] = Field(
        default_factory=list,
        description="All beats narrated at this stop (engine output); beat_id is the primary/first",
    )
    extra_beat_ids: list[str] = Field(
        default_factory=list,
        description="KE: ordered ids of this stop's beats the tour did NOT voice — the "
        "'keep exploring here' extras (uncapped plan minus voiced), most-important first",
    )
    extra_narration: str | None = Field(
        default=None,
        description="KE: composed 'keep exploring here' narration for this stop's extra beats, "
        "voiced on demand off the tour's time budget; null until /compose has run.",
    )
    #: Phase 6 S6.4 (design §5.3 "closes that land anywhere"): the stop's one-line CLOSE —
    #: the last sentence of its narration, and the line [Head back now] shows and plays
    #: for this stretch. Also on screen (§5.7). None on a legacy item.
    close_text: str | None = None
    #: Phase 6 S6.5 (design §5.4; W6.2 R5): THREADS — one sentence per stop that may come
    #: right BEFORE this one when the day is replanned, keyed by that stop's name. The
    #: phone speaks the pair's line at the departure seam and keeps it on screen through
    #: the leg. None when no replanned pair binds (none rather than glue), and always
    #: None on the stitched-corpus lane (threads are authored at compose time).
    thread_lines: dict[str, str] | None = None
    #: Phase 6 S6.6 (design §5.5; W6.2 R3): a MAJOR stop's FULL TELLING — the second
    #: composed piece from the stop's own second story, with its own close. None on a
    #: minor stop, a legacy item, or when the writer's full telling was dropped by its
    #: gates (the stop keeps the tight telling; the on-demand route still answers).
    full_narration: str | None = None
    full_close_text: str | None = None
    #: Phase 6 S6.8 (owner ruling: the tour's own voice): the pre-recorded files for
    #: the authored session lines — the stop's close, its thread lines (keyed like
    #: thread_lines), the full telling's close. Null until the voicing pass has run;
    #: the phone falls back to the plain spoken line.
    close_audio_url: str | None = None
    thread_audio_urls: dict[str, str] | None = None
    full_close_audio_url: str | None = None
    dwell_seconds: int = Field(default=0, description="Engine-computed time spent at this stop")
    #: Phase 7 S7.3 (design §5.6; W7.2 R1): WHERE this stop's piece plays — the place's
    #: own footprint, placed ONCE by the one rule (src/tour/placement.py) at generate and
    #: compose, persisted on the item, and carried here so the phone SELECTS from it and
    #: draws no circle of its own. None on an item written before the rule existed: no
    #: geometry, so nothing auto-plays there (a tap still does).
    trigger: StopTrigger | None = None
    #: Phase 7 S7.7 (design §5.6 C7; plan defect 7): the stop's LEG piece — its walking
    #: line (and a reflection or walk-past one-liner on that leg), split from the story by
    #: the one leg-vs-stop rule and voiced as its own file, played ON THE LEG: the first
    #: stop's at the start, every later stop's once the walker leaves the previous
    #: footprint. None when the leg carries nothing; the file fields null until voiced.
    leg_narration: str | None = None
    #: The stop this leg piece was written FROM — its line names both ends, so it is
    #: true of ONE pair. A replan keeps the day's stops and re-orders them; a line
    #: whose recorded predecessor is no longer the stop before this one describes a
    #: walk nobody is taking, and is dropped rather than spoken. None on the first
    #: stop (walked to from nowhere) and on an item written before the field existed,
    #: where provenance is unknown rather than known-stale.
    leg_from_poi_id: str | None = None
    leg_audio_url: str | None = None
    leg_audio_duration_sec: float | None = None
    #: Phase 7 S7.7 (B) (design §5.6 "segments"; W7.2 R4): a marquee's CHAPTERS — its
    #: story cut at the reviewed anchors, each its own piece at its own place inside the
    #: footprint (outdoor: auto at a standstill; under a roof: a tap). Empty for every
    #: stop without reviewed anchors; the file fields fill when the voicing pass runs.
    segments: list[StopSegment] = Field(default_factory=list)

    @field_validator("thread_lines", "thread_audio_urls", mode="before")
    @classmethod
    def _thread_lines_from_json(cls, v: object) -> object:
        """The graph stores thread_lines as ONE JSON string (Neo4j properties cannot
        hold maps); the reader's rows hydrate through here. A dict passes untouched."""
        if isinstance(v, str):
            import json

            return json.loads(v) if v else None
        return v
    transit_polyline: str | None = Field(
        default=None,
        description="Encoded polyline (6-digit precision) of the walking leg INTO this stop; "
        "null when routing fell back to haversine (Valhalla not running)",
    )


class TripGenerateResponse(BaseModel):
    """Output model for POST /trips/generate."""

    trip_id: str
    trip_name: str
    profile_id: str
    #: Whether the CALLER's profile captains this trip. profile_id above is the
    #: caller's own profile (the one the request named), never the captain's —
    #: this flag, derived from the relationship the row was matched through, is
    #: how a client tells its own day from one shared with it (crew, ADR 0005).
    #: Defaults True: generate answers only ever describe the caller's own trip.
    captained: bool = True
    total_stops: int
    total_duration_min: int
    anchor_count: int = Field(description="Number of gravity-5 POIs")
    flavour_count: int = Field(description="Number of gravity 1-4 POIs")
    lens_coverage: dict[str, int] = Field(
        default_factory=dict,
        description="Engine lens_coverage: lens slug -> beat count across the tour",
    )
    stops: list[GeneratedStop]
    options: list[RouteOption] = Field(
        default_factory=list,
        description="THE day, as a one-element list (Phase 4, design §8.1 deleted "
        "pick-one-of-three). Stays a LIST because trips persisted before Phase 4 "
        "store multi-option lists and one client parser reads both. options[0] is "
        "the persisted trip. Computed per request, not persisted.",
    )
    # EVERYTHING THAT SILENTLY DEGRADED while building this tour (owner ruling
    # 2026-07-31: "Don't just log errors. Actually show them in the workbench UI.
    # Otherwise, they're invisible."). Each row carries BOTH registers — `human` is
    # plain English with no identifiers, and `error_type`/`error_message`/`component`/
    # `context` are what gets pasted to Claude to fix it. An empty list means nothing
    # degraded, which is a real statement rather than an absence. The preview has
    # carried this since 2026-07-31; the phone's two endpoints dropped it on the floor,
    # so a tour planned on estimated walking legs looked identical to a measured one.
    # Same name, type and default on all three responses, so one client parser reads
    # them all. See src/tour/degradations.py.
    degradations: list[dict] = Field(default_factory=list)


class TripComposeRequest(BaseModel):
    """Input for POST /trips/{trip_id}/compose (Phase 4 Step 4.7)."""

    route_id: str = Field(
        description="The picked flavour: '<trip_id>-optN' from the generate response options"
    )


class TripComposeResponse(BaseModel):
    """Output for POST /trips/{trip_id}/compose: the re-persisted, composed stops.

    stop_id values are FRESH (the pick replaces the trip's items) and audio
    fields are null — per-stop audio is generated afterwards by the existing
    /audio/generate-trip-stops flow, which only ever voices narration that
    passed the compose VERIFY gate.
    """

    trip_id: str
    route_id: str
    attempts: int = Field(description="Compose attempts consumed (1 clean, 2 after recompose)")
    stops: list[GeneratedStop]
    #: Everything that silently degraded while writing this tour — see the identical
    #: field on TripGenerateResponse for why it exists and what a row carries.
    degradations: list[dict] = Field(default_factory=list)
    #: THE LIVING SESSION'S VERSION (Phase 5 S5.8, design §8.2): every write mints one
    #: — a second compose is version 2, never a 409. 1 for the first write.
    plan_version: int = 1


class SessionPromise(BaseModel):
    """One promise of the day as the phone reads it (design §3.1; W5.2 R1.5).

    ``protected`` is the person's own class — a pin, a rest the party or dial asked
    for, a declared finish — the only kinds a session ever asks a question about.
    The planner's anchor rides here for the screen but is never protected on an
    open, unpinned walk.
    """

    promise_id: str
    kind: Literal["anchor", "pinned", "rest", "finish"]
    name: str
    arrives_hhmm: str = ""
    departs_hhmm: str = ""
    protected: bool = False


class SessionContingency(BaseModel):
    """One precomputed answer (design §4.6). ``trigger`` is a MATCHER, never a policy:
    ``{"kind": "running_late", "stop_id": ..., "band_minutes": [10, 20]}``,
    ``running_early``, ``minutes_left`` (an open day), ``{"kind": "stop_skipped",
    "stop_id": ...}``, ``{"kind": "promise_at_risk", "stop_id": ...}``,
    ``{"kind": "wrap_up_from", "stop_id": ...}``. The phone matches its measured
    divergence against these, takes the FIRST in server order, and applies the
    entry — it decides nothing. ``question`` is non-null only when the entry
    touches a protected thing (§4.2's two tiers), and ``screen_text`` is never
    empty (§4.4.2 — everything spoken is also on screen).
    """

    contingency_id: str
    trigger: dict
    plan_version: int
    stop_ids: list[str] = Field(default_factory=list)
    screen_text: str
    question: str | None = None
    default_arm: Literal["keep", "shorten"] | None = None
    alternate_stop_ids: list[str] = Field(default_factory=list)
    at_risk_stop_id: str | None = None
    finish_hhmm: str = ""

    # THE WIRE ENFORCES WHAT IT CAN (Phase 5 S5.11; design §4.4, W5.2 R2): the
    # screen always carries the line (§4.4.2), a question is ONE sentence
    # (§4.4.4, R2.1) and names its default (R2.4/R2.5). Wording itself is the
    # server's business (`contingency.plain`, `one_sentence`); this is the last
    # door before the phone.
    @model_validator(mode="after")
    def _etiquette(self) -> SessionContingency:
        if not self.screen_text.strip():
            raise ValueError("screen_text is empty: everything spoken is also on screen (§4.4.2)")
        if self.question is not None:
            if not self.question.strip():
                raise ValueError("an empty question is not a question")
            if self.default_arm is None:
                raise ValueError("a question names its default arm (W5.2 R2.4)")
            breaks = [
                m.start()
                for m in re.finditer(r"[.!?](?:\s|$)", self.question.strip())
            ]
            if len(breaks) != 1 or not self.question.strip().endswith("?"):
                raise ValueError(
                    "the question is ONE sentence ending in a question mark (§4.4.4, R2.1): "
                    f"{self.question!r}"
                )
        return self


class SessionPlan(BaseModel):
    """GET /trips/{id}/session and every replan reply — the day the person is
    standing in, versioned (design §8.2), with the contingency set beside it (§4.6).
    """

    trip_id: str
    plan_version: int
    stops: list[GeneratedStop]
    promises: list[SessionPromise] = Field(default_factory=list)
    retime_tolerance_seconds: int
    contingencies: list[SessionContingency] = Field(default_factory=list)
    degradations: list[dict] = Field(default_factory=list)
    #: The walking speed this day was planned at — the preset the phone re-times
    #: with until it has learned the person's own pace (design §4.1; S5.10).
    walking_pace_kmh: float = Field(default=3.0, gt=0)
    #: When the day's clock starts ("HH:MM") — the frame every stop clock and the
    #: phone's own re-timing share (S5.10's seam compares in this frame).
    day_start_hhmm: str = ""
    #: When the day is planned to END ("HH:MM"): the phone's minutes-left on an OPEN
    #: day (W5.2 R1.3 bands by minutes left) count down to this.
    planned_end_hhmm: str = ""
    #: Where the day ends (the declared end, or the start of a round trip) and what
    #: to call it, so the phone re-times the finish itself (W5.13: a precomputed
    #: line's clock is stale by the time it fires; the phone's clock is not). None
    #: on an open walk with no end — the day ends where its last stop does.
    finish_lat: float | None = None
    finish_lng: float | None = None
    finish_name: str = "your finish"
    #: wall | firm | open — the phone says ONE screen line when a firm or wall finish
    #: has moved past the tolerance (W5.14 Q3); an open day's finish moves in silence.
    end_hardness: str = "firm"
    #: The party the day was planned for (solo/couple/family/take_it_easy/
    #: with_luggage or None): the phone's screen-only switch is per PARTY (W5.2 R4).
    party: str | None = None
    #: Phase 7 S7.3 (design §5.6; W7.2 R1): the day's placement POLICY — who starts a
    #: piece at the footprint's edge and who at the first standstill inside it, and the
    #: radius of the person's own place — decided on the server from the party
    #: (src/tour/placement.py), never a branch the phone invents. None from an older
    #: server: the phone then starts at arrival and uses the rule's own-place default.
    placement: PlacementPolicy | None = None


class SessionReplanRequest(BaseModel):
    """What the phone sends to POST /trips/{id}/session/replan — its observations,
    never a decision (design §4.6): where it is, its two clocks, its two learned
    rates, and which planned stop it is heading to."""

    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    #: Real seconds since the day started (a pause does NOT stop this one).
    wall_elapsed_seconds: int = Field(..., ge=0)
    #: The tour clock — seconds since the day started with pauses suspended (§4.3).
    tour_elapsed_seconds: int = Field(..., ge=0)
    #: Learned walking pace as a multiplier on the preset (>= 1.0 slows), or None.
    observed_pace: float | None = Field(default=None, ge=1.0)
    #: Learned listening rate (1.5 = the person spends 1.5x a piece's length).
    listening_rate: float | None = Field(default=None, ge=0.5, le=3.0)
    #: Index into the current plan's stops of the next stop not yet reached.
    next_stop_index: int = Field(default=0, ge=0)
    #: The phone's OWN re-timed clock (HH:MM) for that next stop — an observation
    #: the server compares with its one expression and REPORTS on, never adopts
    #: (S5.10's seam).
    phone_next_stop_hhmm: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    #: The POI id of a planned stop still ahead whose door the walker found SHUT
    #: (Docs/adr/0006 rule 5) — an observation: the server takes that stop out of
    #: the day it replans and counts the report on the place, so the next walker
    #: with a guess there hears it. None: an ordinary replan.
    closed_stop_id: str | None = None


class TripPreviewRequest(BaseModel):
    """Web-first preview (Phase 1.5): generate a tour's per-stop narration WITHOUT
    a profile and WITHOUT persisting anything."""

    center_lat: float = Field(..., ge=-90, le=90)
    center_lng: float = Field(..., ge=-180, le=180)
    end_lat: float | None = Field(default=None, ge=-90, le=90)
    end_lng: float | None = Field(default=None, ge=-180, le=180)
    duration_min: int | None = Field(default=None, ge=1, le=600)
    # See TripGenerateRequest.max_stop_minutes. Declared on BOTH request models
    # because neither sets model_config, so both inherit pydantic's default
    # extra="ignore" and would silently DROP an unknown field rather than reject
    # it — the caller would get a tour with no ceiling and no error saying why.
    # TripPreviewAuthorRequest subclasses this and inherits it.
    max_stop_minutes: int | None = Field(default=None, ge=5, le=600)
    # THE PLANNER'S CLOCK — declared here for the same silently-dropped-field
    # reason as max_stop_minutes above; see TripGenerateRequest for the full
    # story. TripPreviewAuthorRequest subclasses this and inherits both.
    start_datetime: str | None = None
    end_hardness: Literal["wall", "firm", "open"] = "firm"
    # WHO IS WALKING + THE DIALS (Phase 4, W4.2 panel — the workbench's whole
    # request surface; TripPreviewAuthorRequest inherits every one, which is
    # what lets the author call replay the exact dialed body). Same fields,
    # same defaults, same normalization as TripGenerateRequest above.
    party: str | None = None
    walking_pace: float | None = Field(default=None, ge=1.0)
    max_leg_minutes: int | None = Field(default=None, ge=1)
    rest_cadence_minutes: int | None = Field(default=None, ge=1)
    weather: Literal["dry", "rain", "auto"] | None = None
    pinned_poi_ids: list[str] = Field(default_factory=list)
    stop_density: Literal["fewer", "more"] | None = None
    narration_density: Literal["less", "more"] | None = None
    avoid_queues: bool = False
    category_minus: list[str] = Field(default_factory=list)

    _normalize_party_preset = field_validator("party")(_normalize_party)

    lenses: list[str] | None = None
    round_trip: bool = False
    city_slug: str = Field(
        default="paris",
        description="City corpus to tour; validated against city_registry.servable_cities()",
    )

    _validate_city = field_validator("city_slug")(_validate_city_slug)

    @field_validator("lenses")
    @classmethod
    def validate_lenses(cls, v: list[str] | None) -> list[str] | None:
        """Reject an unknown lens LOUDLY instead of silently ignoring it.

        MEASURED 2026-07-19: an unrecognised lens string was not an error. It fell
        through ``_lens_relation`` (``selection.py:2451-2477``), which classifies
        every POI as a "miss", so ``lens_relevance`` returned the uniform
        ``LENS_FLOOR`` (0.25) for the whole corpus. Because spotlight is
        MULTIPLICATIVE, a uniform factor leaves relative ranking IDENTICAL to
        passing no lens at all — so a typo produced the un-lensed tour with no
        signal anywhere in the response that the lens had been ignored.

        That is precisely the reported "tours for different lenses don't work":
        the request looked accepted and the tour looked plausible, while the lens
        did nothing. Validating here converts a silent no-op into a 422 that names
        the valid vocabulary.

        Scoped deliberately to the PREVIEW model (the workbench surface where a
        human types free text). ``TripGenerateRequest`` keeps its permissive
        ``normalize_lenses`` so no mobile-app request shape changes.
        """
        cleaned = normalized_lens_list(v)
        if cleaned is None:
            return None
        from src.schema.definitions import TAGGABLE_LENSES

        known = set(TAGGABLE_LENSES)
        unknown = [s for s in cleaned if s not in known]
        if unknown:
            raise ValueError(
                f"Unknown lens(es): {', '.join(sorted(unknown))}. "
                f"Valid lenses: {', '.join(sorted(known))}"
            )
        return cleaned

    # No narration flags: every preview uses the shared Premium certification
    # planner, one immutable zero-retry authoring request per stop, and the pure
    # traced-blueprint finalizer. Certification reviewers run separately.


class TripPreviewAuthorRequest(TripPreviewRequest):
    """The SAME plan inputs, plus which of the offered routes to write.

    It builds on the plan request rather than restating it: writing re-derives the
    plan, so every value that fed the plan has to come back unchanged, and one shared
    definition is what guarantees "unchanged" — including the unknown-lens rejection.
    """

    route_id: str = Field(
        ...,
        description="The chosen route's id, copied exactly from the plan reply: "
        "'preview-<12 hex>-optN'. The hex identifies the plan itself and N picks one "
        "of its routes. An id that no longer matches a freshly derived plan is "
        "refused, never quietly swapped for a different tour.",
    )


class TripPreviewStop(BaseModel):
    """One stop in one explicitly typed preview artifact.

    ``TripPreviewResponse.stops`` contains only complete LLM-candidate text.
    Deterministic grounded narration, when available, lives under the separate
    ``basic_tour`` object and is never eligible for grading.
    """

    sort_order: int
    poi_name: str
    lat: float
    lng: float
    narration: str
    minutes: int
    # Phase 3 spotlight model (spec s7). Additive with behavior-preserving
    # defaults: a full dwell stop with a zero score until Step 3.5 wires the
    # spotlight effect into selection.
    # "leg" (added 2026-07-19) is narration spoken WHILE WALKING between stops —
    # navigation lines and reflections. It was always emitted, but folded into the
    # following dwell stop's narration, so an editor could not see it, could not
    # play it on its own, and could not tell how much of a 10-minute walk was
    # actually filled. Per the product ruling ("Audio overlaps the walking. It is a
    # part of the tour experience.") this is real tour content and gets its own card.
    band: Literal["dwell", "vignette", "leg"] = "dwell"
    spotlight: float = 0.0
    # KE9: this dwell stop has "keep exploring here" EXTRAS — beats the tour's
    # time budget capped out (poi_id present in overflow_by_poi with non-empty
    # overflow). A bool flag, not the extra_beat_ids list: the workbench only
    # needs to render a badge, and the preview must not leak full beat-id lists.
    # Always False for vignette (walk-past) stops. Default False is
    # behavior-preserving.
    has_deeper_dive: bool = False


class TripPreviewTourability(BaseModel):
    """Density / delivery disclosure shipped alongside a generated preview.

    Phase 6: YELLOW = "generate but WARN". C11a adds GREEN-but-``delivered_thin``:
    a GREEN-density pool that still delivers thin (audio far under the request or
    a single dominating stop) — the engine-side answer to the 2026-07-02
    pool-vs-delivered gap. A fully-GREEN rich tour still ships tourability=null."""

    status: Literal["GREEN", "YELLOW"]
    delivered_thin: bool = False
    fill_ratio: float
    # Lens-specific fill (audio matching the requested lenses / target). None when
    # no lenses were requested. Lets the surface disclose "thin FOR your interest"
    # distinctly from the lens-agnostic overall fill_ratio.
    on_lens_fill_ratio: float | None = None
    anchor_candidates: int
    reachable_poi_count: int
    max_supportable_duration_min: int | None = None
    one_way_alternative_destination: str | None = None


class TripPreviewBasicTour(BaseModel):
    """Emergency grounded guide, deliberately outside the LLM grading lane."""

    kind: Literal["basic_tour"] = "basic_tour"
    reason: Literal["llm_generation_failed", "llm_candidate_ineligible"]
    total_audio_min: int = Field(..., ge=0)
    stops: list[TripPreviewStop]


class TripPreviewPromise(BaseModel):
    """One promised item of the planned day, as the pre-commit surface shows it.

    Phase 4 (W4.2 deviation v — ruled to land this phase): name + a COARSE
    window (F&D: "not six minute-precision timestamps"; empty strings on a
    dateless plan) + which side of the door and the wait, so the person can
    judge the promise before anything is written.
    """

    kind: Literal["anchor", "pinned", "rest", "finish"]
    name: str
    arrives_hhmm: str = ""
    departs_hhmm: str = ""
    goes_inside: bool = False
    queue_minutes: int = 0


class TripPreviewResponse(BaseModel):
    """THE PLAN, and nothing else — route options, no narration, no spend.

    The first of the two halves the tour engine was split into on 2026-08-04. This
    endpoint picks the places, orders and routes them, and prices the walk; it calls
    no provider, so its answer is free and it is what a person chooses FROM. Writing
    the chosen route is a separate call, POST /trips/preview/author.

    Everything that described WRITTEN text — the flat stop list, the narrator's name,
    the candidate lane, the Basic fallback, the quality verdicts — moved there with
    the writing. Keeping any of it here would advertise something planning cannot
    produce, on a reply that is now sent before anyone has chosen anything.
    """

    spine_area: str | None = None
    # THE day, as a one-element list (Phase 4, design §8.1). The field stays a list
    # because the author route's opt-N ids and every stored pre-Phase-4 payload are
    # list-shaped; there is simply exactly one entry now, carrying its stops, its
    # arrival time and its per-corridor lens note.
    options: list[RouteOption] = Field(default_factory=list)
    # None = GREEN (no warning needed). RED never reaches a 200 response.
    tourability: TripPreviewTourability | None = None
    # THE HONESTY SURFACE (Phase 4, W4.2 deviation v — ruled to land this
    # phase, all eleven personas): what the day PROMISES, what the clock and
    # the dials excluded (in plain sentences), and where the unplanned minutes
    # live. All additive with empty defaults, so every earlier payload and
    # every consumer that ignores them is untouched.
    promises: list[TripPreviewPromise] = Field(default_factory=list)
    day_notes: list[str] = Field(default_factory=list)
    slack_minutes: int | None = None
    # The longest single walk in the day, in minutes (W4.12 — the number the
    # "Shorter walks" dial is named after; the surface used to print only the
    # total). None only from an older server; 0 on a day with no walking legs.
    longest_walk_minutes: int | None = None
    # EVERYTHING THAT SILENTLY DEGRADED while PLANNING this tour (owner ruling
    # 2026-07-31: "Don't just log errors. Actually show them in the workbench UI.
    # Otherwise, they're invisible."). A route built on estimated walking legs rather
    # than measured ones is labelled here rather than shipped silently. Each row
    # carries BOTH registers — `human` is plain English with no identifiers,
    # `error_type`/`error_message`/`component`/`context` are what gets pasted to
    # Claude to fix it. Empty list means nothing degraded, which is a real statement
    # rather than an absence. See src/tour/degradations.py.
    degradations: list[dict] = Field(default_factory=list)


class TripAuthoredTourResponse(BaseModel):
    """THE WRITING: the one route that was chosen, now narrated.

    The second of the two halves. It never re-plans. Its places, their order, its
    arrival time and its walk-past sights are the chosen route's, unchanged — only
    the words are new. The Basic fallback, the narrator's name and the quality
    verdicts live here because they all describe written text, which the plan-only
    reply no longer has.

    ``stops`` is gradeable only when ``candidate_eligible`` is true. A grounded
    emergency guide is returned under ``basic_tour`` instead of being mixed into the
    written stops or assigned a quality score — and there is deliberately NO
    top-level stop list, so a surface cannot render one tour's places under another
    tour's words.
    """

    # Which route this is, echoed back exactly as it was sent.
    route_id: str
    # THE WRITTEN ROUTE. Same places, same order, same arrival time and same
    # walk-past sights as the option that was chosen, with the narration filled in.
    # None when nothing was written (the Basic lane below).
    option: RouteOption | None = None
    spine_area: str | None = None
    total_audio_min: int
    candidate_eligible: bool = False
    candidate_status: Literal["premium_candidate_eligible_for_certification"] | None = None
    narration_kind: Literal["llm_candidate", "none"] = "none"
    basic_tour: TripPreviewBasicTour | None = None
    # None = GREEN (no warning needed). RED never reaches a 200 response.
    tourability: TripPreviewTourability | None = None
    # ``composed`` is the only gradeable state. ``basic_available`` means no LLM
    # candidate crossed the provenance gate; the separate basic_tour may still be
    # offered to a stranded user. None exists only on legacy serialized payloads.
    compose_status: str | None = None
    # Present only when a generated artifact was deliberately kept out of the
    # LLM-candidate lane. This exposes provenance failure without leaking or
    # grading the discarded mixed narration.
    candidate_rejection: CandidateRejection | None = None
    # Which narrator actually wrote this tour ('anthropic' | 'openai'), so the
    # workbench can label an Opus-vs-ChatGPT comparison. None when not composed.
    provider: str | None = None
    # EVERYTHING THAT SILENTLY DEGRADED while writing this tour. Same channel, same
    # row shape and same rendering as the plan reply's. See src/tour/degradations.py.
    degradations: list[dict] = Field(default_factory=list)
    # Objective spoken-narration quality signals for the composed tour (stilted vs
    # engagement scores + the per-100-word tells), so the comparison is MEASURED,
    # not vibes. None when not composed. See tour.narration_quality.
    narration_quality: dict | None = None
    # THE QUALITY RUBRIC verdict for this tour — the mechanical floor of the tour
    # quality standard (fixtures/tour-quality-standard/01-standard.md), run
    # on EVERY tour at $0. Carries {passed, blockers[], warnings[], stats{}}. A
    # failing verdict means the tour breached the standard (a rich POI starved to a
    # line, a stop gorged past the listenable cap, a repeated sentence) and the
    # workbench shows the editor exactly which check fired and where, instead of
    # leaving them to notice it by reading. See tour.quality_rubric.
    quality: dict | None = None
