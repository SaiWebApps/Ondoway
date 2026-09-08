"""Trip generation routes — run the tour engine (src/tour) and persist itineraries."""

from __future__ import annotations

import functools
import hashlib
import itertools
import json
import logging
import re
import sys
from collections.abc import Callable
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from typing import TypeVar

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from neo4j import Driver, Session
from pydantic import ValidationError

from src.api.auth.dependencies import get_current_user
from src.api.crud.trips import (
    create_trip_with_stops,
    get_trip_compose_inputs,
    list_trips_for_profile,
    replace_trip_stops,
    route_script_to_stops,
    write_trip_session,
)
from src.api.dependencies import (
    get_driver,
    get_faithfulness_checker,
    get_premium_compose_executor,
    get_session,
)
from src.api.models.trips import (
    GeneratedStop,
    SessionContingency,
    SessionPlan,
    SessionPromise,
    SessionReplanRequest,
    TripAuthoredTourResponse,
    TripComposeRequest,
    TripComposeResponse,
    TripGenerateRequest,
    TripGenerateResponse,
    TripPreviewAuthorRequest,
    TripPreviewBasicTour,
    TripPreviewPromise,
    TripPreviewRequest,
    TripPreviewResponse,
    TripPreviewStop,
    TripPreviewTourability,
)
from src.tour.beat_select import select_vignette_beats
from src.tour.candidate_eligibility import (
    CandidateRejection,
    CandidateRejectionCode,
)
from src.tour.compose_gate import ComposeVerificationError
from src.tour.contingency import (
    OWN_PLACE_RADIUS_M,
    RETIME_TOLERANCE_SECONDS,
    SESSION_CLOCK_DIVERGENCE,
    ContingencySet,
    _finish_of,
    at_risk_choice,
    build_contingency_set,
    clock_divergence_seconds,
    finish_clock,
    hhmm,
    own_place_ids,
    question_text,
    stop_clocks,
)
from src.tour.contract import (
    END_B_SENTINEL_PREFIX,
    POI,
    BeatSequence,
    POIBeats,
    ReplanContext,
    RouteOption,
    TourabilityAssessment,
    TourInput,
    resolve_party_axes,
)
from src.tour.degradations import degradation_scope, record, summarize
from src.tour.density import TourabilityRefusedError
from src.tour.generation import _template_nav, generate
from src.tour.narration_quality import score_narration
from src.tour.options import build_route_option, option_eta_seconds
from src.tour.placement import StopSegment, place_anchors, place_day, place_stops
from src.tour.premium_tour import (
    FULL_TELLING_DROPPED_DEGRADATION,
    PREMIUM_MODULE_VERSION,
    EphemeralReceiptSink,
    FullTelling,
    PremiumComposeExecutor,
    PremiumRouteInfeasibleError,
    certification_planning_policy,
    exact_snapshot_sha256,
    execute_premium_plan,
    finalize_premium_full_telling,
    finalize_premium_tour,
    full_telling_majors,
    plan_premium_authoring,
    plan_premium_full_telling,
    plan_premium_tour,
    record_routing_degradations,
    resolve_build_identity,
    resolve_routing_version,
)
from src.tour.quality_rubric import RubricReport, StopMaterial, compose_fixable, score_tour
from src.tour.routing import (
    PACE_KMH,
    haversine_m,
    leg_walk_seconds,
    longest_walk_minutes,
    summarise_route,
)
from src.tour.routing_client import ROUTE_SURFACE_COSTING_OVERRIDES, RoutingClient
from src.tour.selection import (
    AVOID_QUEUES_EXCLUDE_PEAK_MINUTES,
    CertificationPlanningInfeasibleError,
    build_poi_beat_plans_capped,
    build_poi_extra_beats,
    build_poi_extra_narration,
    end_b_sentinel_from_id,
    expanded_interest_lenses,
    load_paris_corpus,
    pick_spine_area,
    planned_audio_by_poi,
    select_route,
)
from src.tour.verify import FaithfulnessChecker

router = APIRouter(tags=["trips"])

# TourInput.duration_min is required; the request field is optional for
# back-compat with pre-engine clients that never sent it.
DEFAULT_DURATION_MIN = 60


# --- Upstream LLM provider failures -> 502/503, never a raw 500 ---------------
# anthropic.RateLimitError / APITimeoutError / APIConnectionError / APIStatusError
# escaping a compose route surfaced as an HTTP 500 with a stack trace and no
# Retry-After, so the workbench and the mobile client could not tell a transient
# upstream throttle from a real server bug and could not back off. feedback.py
# already maps these exact exceptions to 503/502; the far more expensive compose
# paths did not. Imported defensively so the module still imports (and the
# hermetic suite still runs) if the SDK is absent — `except ()` simply never
# matches, leaving behaviour identical to before.
try:  # pragma: no cover - exercised both ways only across environments
    import anthropic as _anthropic

    _PROVIDER_THROTTLE_ERRORS: tuple[type[BaseException], ...] = (_anthropic.RateLimitError,)
    # APITimeoutError / APIConnectionError / APIStatusError all subclass APIError.
    _PROVIDER_ERRORS: tuple[type[BaseException], ...] = (_anthropic.APIError,)
except ImportError:  # pragma: no cover
    _PROVIDER_THROTTLE_ERRORS = ()
    _PROVIDER_ERRORS = ()


@contextmanager
def _upstream_provider_errors():
    """Map Anthropic provider failures onto the HTTP contract the clients expect.

    503 + Retry-After for throttling (retry later, the tour is still buildable),
    502 for any other provider fault (timeout, connection, unexpected status).
    HTTPException raised inside the block passes through untouched.
    """
    try:
        yield
    except _PROVIDER_THROTTLE_ERRORS as exc:
        raise HTTPException(
            status_code=503,
            detail=f"LLM provider rate limited: {exc}",
            headers={"Retry-After": "30"},
        ) from exc
    except (*_PROVIDER_ERRORS, TimeoutError) as exc:
        raise HTTPException(status_code=502, detail=f"LLM provider error: {exc}") from exc


# RATE LIMITING REMOVED 2026-07-31 (owner order, twice stated). The preview and
# compose paths carried four bounds — per-IP window, global window, daily ceiling
# and an in-flight concurrency slot. All four are DELETED, not disabled: no
# constants, no counters, no env knobs, nothing to switch back on by accident.
#
# Stated once, plainly: since the plan/write split of 2026-08-04 the anonymous
# surface that spends money is /trips/preview/author, not /trips/preview — planning
# is free and calls nobody. Writing still makes one paid call per stop with nothing
# in front of it, so nothing bounds what an unauthenticated caller can spend.
# If a bound is wanted later it must key on the AUTHENTICATED user rather than
# the client IP — mobile carriers put thousands of subscribers behind a handful
# of addresses, so the per-IP cap throttled real tour groups sharing a hotel or a
# carrier while barely inconveniencing a determined caller. It also fired on the
# owner's own workbench at three previews an hour.


def _owned_profile_id(session: Session, user_id: str, profile_id: str) -> str | None:
    """The profile id iff `user_id` owns it, else None.

    Ownership-scoped, NOT existence-scoped: the previous
    ``MATCH (p:Profile {id: $pid})`` check let any caller read or mutate any
    profile's trips by guessing a profile id. A miss returns 404 at the call
    site (never 403) so the endpoint does not confirm foreign profile ids.
    """
    record = session.run(
        "MATCH (u:User {id: $uid})-[:HAS_PROFILE]->(p:Profile {id: $pid}) RETURN p.id AS id",
        uid=user_id,
        pid=profile_id,
    ).single()
    return record["id"] if record else None


def _end_point(end_lat: float | None, end_lng: float | None) -> tuple[float, float] | None:
    """Fixed-destination (lat, lng) B for TourInput.end, or None when unset.

    Both coordinates must be present to form an endpoint; a lone end_lat or
    end_lng is treated as no endpoint (TourInput.end stays None — the
    end=None identity path is unchanged).
    """
    if end_lat is None or end_lng is None:
        return None
    return (end_lat, end_lng)


def _resolved_weather(
    weather: str | None, start_datetime: str | None, lat: float, lng: float
) -> str | None:
    """The sky, resolved at the edge: 'auto' fetches the forecast (fail-open).

    design §2.5 ("fetched, never asked") + the W4.2 panel (Aiko: weather stays
    automatic; the day merely SHOWS what the sky did). 'auto' without a dated
    request has nothing to fetch and plans as no signal. A forecast the module
    cannot answer (horizon, outage, garbage) is None — no signal — never an
    error: the fingerprint makes a between-calls forecast flip visible as the
    honest 409 rather than silently authoring a different day.
    """
    if weather != "auto":
        return weather
    if start_datetime is None:
        return None
    from src.tour.weather import fetch_rain_likelihood

    return fetch_rain_likelihood(start_datetime[:10], lat, lng)


def _dial_kwargs(body) -> dict:
    """The Phase-4 request axes, threaded onto TourInput verbatim (W4.2).

    ONE mapping for all three construction sites (generate, preview, author),
    so a dial cannot reach one surface and silently miss another — the exact
    S1.3b lesson, one function instead of three copy-paste blocks.
    """
    return {
        "party": body.party,
        "walking_pace": body.walking_pace,
        "max_leg_minutes": body.max_leg_minutes,
        "rest_cadence_minutes": body.rest_cadence_minutes,
        "weather": _resolved_weather(
            body.weather, body.start_datetime, body.center_lat, body.center_lng
        ),
        "pinned_poi_ids": tuple(body.pinned_poi_ids),
        "stop_density": body.stop_density,
        "narration_density": body.narration_density,
        "avoid_queues": body.avoid_queues,
        "category_minus": tuple(body.category_minus),
    }



def _resolve_persisted_pick(poi_ids: list[str], pois_by_id: dict[str, POI]) -> list[POI]:
    """The compose rebuild's corpus check: every persisted stop id must still
    resolve, or the trip is refused 409 corpus_changed.

    THE ENGINE'S OWN END SENTINEL IS NOT CORPUS DRIFT (Phase 6 W6.12): an A→B
    day whose destination has no corpus POI within snapping distance persists
    a synthesized `__end_b__<lat>_<lng>` stop — its id carries its coordinate,
    so the rebuild re-materializes it. Measured: Sofia's Châtelet day 409'd
    "corpus_changed" on every compose, permanently uncomposable.
    """
    for pid in poi_ids:
        sentinel = end_b_sentinel_from_id(pid)
        if sentinel is not None:
            pois_by_id[pid] = sentinel
    missing = [pid for pid in poi_ids if pid not in pois_by_id]
    if missing:
        raise HTTPException(409, {"reason": "corpus_changed", "missing_poi_ids": missing})
    return [pois_by_id[pid] for pid in poi_ids]

def _restore_tour_input(tour_input_dict: dict) -> TourInput:
    """The persisted request back as a TourInput — every stored axis, fail-open.

    A record written before an axis existed simply lacks the key and lands on the
    axis's identity default (dateless, `firm`, no party, no pins …), which is
    exactly what that trip was planned with. ONE restorer for compose and both
    session endpoints, so a key added to the record cannot reach one and miss
    another (the S1.3b lesson).
    """
    known = set(TourInput.model_fields)
    fields = {k: v for k, v in tour_input_dict.items() if k in known and v is not None}
    fields["start"] = tuple(tour_input_dict["start"])
    fields["end"] = tuple(tour_input_dict["end"]) if tour_input_dict.get("end") else None
    fields["end_hardness"] = tour_input_dict.get("end_hardness") or "firm"
    for tuple_key in ("pinned_poi_ids", "category_minus"):
        if tuple_key in fields:
            fields[tuple_key] = tuple(fields[tuple_key])
    return _build_tour_input(**fields)


def _build_tour_input(**kwargs) -> TourInput:
    """Construct TourInput, mapping its contract ValidationError to a 422.

    TourInput's model_validator (e.g. _end_round_trip_mutex: end + round_trip
    are mutually exclusive, src/tour/contract.py) raises a pydantic
    ValidationError on a contradictory request. Unhandled, that surfaces as an
    opaque HTTP 500 with a stack trace (app.py registers no ValidationError
    handler). It is a CLIENT error, so translate it to a 422 with the field
    messages the caller needs to correct the request.

    RESOLVED, exactly as the harness resolves it (scripts/tour_build.py). The
    party presets and the "more stops" dial are shortcuts over axes, and "the
    axes are what the planner reads" (design §2.4) — but `resolve_party_axes`
    was called by the harness ALONE. On this wire a preset never expanded and
    `stop_density="more"` never became its stop ceiling, so the workbench's
    Party dropdown and its More-stops dial were dead while the identical
    request through the harness worked. MEASURED at the W4.12 close (2026-08-18):
    the wire's "More stops" day was byte-identical to base and kept a 47-minute
    museum, while an explicit 20-minute ceiling on the same request reshaped
    the day. This is the ONE construction door for all three API sites
    (generate, preview, author), so resolving here keeps the surfaces one
    engine (tests/test_workbench_matches_the_app.py's whole reason to exist).
    """
    try:
        return resolve_party_axes(TourInput(**kwargs))
    except ValidationError as exc:
        raise HTTPException(
            422,
            {
                "reason": "invalid_tour_input",
                "errors": [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()],
            },
        ) from exc


def _refusal_detail(
    exc: TourabilityRefusedError | CertificationPlanningInfeasibleError,
) -> dict:
    """Structured 422 body for EITHER refusal a traveller can hit.

    Shape (mirrors the FeasibilityAlternative NamedTuple from src/tour/density.py):
        {cause, reason, gap_minutes, alternatives: [{kind, duration_min, drop_end,
                                                     poi_id, lat, lng}, ...]}
    gap_minutes is None for plain density-RED refusals (no fixed destination);
    alternatives is [] there too. For a fixed-destination overshoot it carries
    the routed-leg gap and the loop/extend/closer_b alternatives (closer_b
    includes its target poi_id/lat/lng).

    BOTH refusals share this shape since 2026-08-04. ``CertificationPlanningInfeasible
    Error`` used to have no alternatives at all and no handler on POST /trips/generate,
    so a traveller whose request simply did not fit the time band got a 500. ``cause``
    lets a surface tell the two apart — "there is not enough to see near here" versus
    "this cannot be walked in the time you asked for" — without parsing prose.
    """
    return {
        "cause": (
            "time_budget"
            if isinstance(exc, CertificationPlanningInfeasibleError)
            else "tourability"
        ),
        # THE SENTENCE A PERSON READS, and the line an operator reads, apart
        # (W4.12, Paulo — the panel's language judge): the workbench prints
        # `reason` straight onto the page, and it carried the whole exception
        # text — "Certification planning infeasible under ondoway-premium-tour-v1:
        # ... required 9720-10800s, best eligible bounded route 4248s." — a
        # product codename, the word "certification", and time in seconds, wrapped
        # around the one plain clause the planner had written for a traveller.
        # `reason` is now that clause alone; `technical` keeps the whole line, so
        # nothing an operator needs is lost, and the AC-24 pin ("the refusal
        # names the budget it could not fill") holds on the field that names it.
        "reason": (
            exc.reason if isinstance(exc, CertificationPlanningInfeasibleError) else str(exc)
        ),
        "technical": str(exc),
        "gap_minutes": exc.gap_minutes,
        "alternatives": [
            {
                "kind": alt.kind,
                "duration_min": alt.duration_min,
                "drop_end": alt.drop_end,
                "poi_id": alt.poi_id,
                "lat": alt.lat,
                "lng": alt.lng,
            }
            for alt in exc.alternatives
        ],
    }


def _infeasible_detail() -> dict:
    """Structured 422 body for a route the street network could not carry.

    The SAME four keys as ``_refusal_detail`` — cause / reason / gap_minutes /
    alternatives — so a client parses one shape whichever refusal it hit. This
    family measures nothing a traveller can trade away (there is no shorter
    duration that makes an unroutable start routable), so gap_minutes is None and
    there are no alternatives to offer.

    PLAIN ENGLISH, never an identifier. This branch used to emit the literal string
    ``premium_route_infeasible`` as ``reason``, and both surfaces render ``reason``
    straight onto the page — so a person was shown an internal symbol and told
    nothing about what to do next.
    """
    return {
        "cause": "routing",
        "reason": (
            "This walk could not be routed on the street network. "
            "Try a different start, or try again in a moment."
        ),
        "technical": "premium_route_infeasible",
        "gap_minutes": None,
        "alternatives": [],
    }


def _restored_vignettes(
    stored: dict[str, list[str]] | None, pois_by_id: dict[str, POI]
) -> dict[int, tuple[POI, ...]] | None:
    """The walk-past sights of a saved flavour, back as POIs on their own legs.

    JSON object keys are strings, so the leg numbers come back as text and are turned
    into numbers again here. A sight whose POI has since left the corpus is dropped
    rather than faked — the tour simply does not mention it — and a flavour saved
    before this was recorded returns None, which leaves the rebuilt route with no
    sights, exactly as those trips have always composed.
    """
    if not stored:
        return None
    return {
        int(leg_idx): tuple(pois_by_id[pid] for pid in poi_ids if pid in pois_by_id)
        for leg_idx, poi_ids in stored.items()
    }


#: Field names the 2026-08-06 currency move changed, stored -> current.
#:
#: WHY THIS EXISTS AND WHY IT IS NOT OPTIONAL. Every trip saved before that date
#: recorded its disclosure under the old names, and ``TourabilityAssessment`` is
#: ``extra="forbid"``, so validating one of those dicts raises. The function below
#: fails OPEN, so the failure would not be an error anyone sees — the thin-area
#: warning would simply stop appearing, on every trip saved before today, silently.
_RENAMED_TOURABILITY_FIELDS = {
    "audio_capacity_seconds": "dwell_capacity_seconds",  # stored-schema
    "target_audio_seconds": "target_dwell_seconds",  # stored-schema
}


def _restored_tourability(stored: dict | None) -> TourabilityAssessment | None:
    """The thin-area disclosure of a saved flavour, or None when it was fully GREEN.

    A malformed record is treated as no disclosure rather than a 500: the worst case
    is a composed tour that does not repeat a warning the traveller already saw when
    they picked it, which is far better than refusing to compose at all.

    Records written before a field was renamed are translated rather than dropped,
    because "fail open" turns a rename into an invisible loss of a disclosure the
    traveller was shown when they chose the tour.
    """
    if not stored:
        return None
    migrated = {_RENAMED_TOURABILITY_FIELDS.get(key, key): value for key, value in stored.items()}
    try:
        return TourabilityAssessment.model_validate(migrated)
    except ValidationError:
        return None


def _resolve_lenses(session: Session, body: TripGenerateRequest) -> list[str] | None:
    """Lens precedence: request -> profile PREFERS_LENS -> None (engine unbiased).

    Profile lens names are sorted so the engine input is deterministic
    regardless of edge-traversal order. A computed per-city default starter
    set is a future feature (see ondoway-lens-defaults-spec.md); until it
    exists, a no-lens profile runs the engine without lens bias.
    """
    if body.lenses:
        return body.lenses
    records = session.run(
        "MATCH (p:Profile {id: $pid})-[:PREFERS_LENS]->(l:Lens) RETURN l.name AS name",
        pid=body.profile_id,
    )
    names = sorted(r["name"] for r in records)
    return names or None


def _lens_display_map(session: Session, lens_names: set[str]) -> dict[str, str]:
    """Map lens name -> display_label (falling back to the name) for the given lenses."""
    if not lens_names:
        return {}
    records = session.run(
        "MATCH (l:Lens) WHERE l.name IN $names "
        "RETURN l.name AS name, coalesce(l.display_label, l.name) AS display",
        names=sorted(lens_names),
    )
    return {r["name"]: r["display"] for r in records}


def _primary_beat_audio(session: Session, beat_ids: list[str]) -> dict[str, dict]:
    """Per primary beat: script_body/audio_url/audio_duration_sec for the response.

    Mirrors what list_trips_for_profile reads for a stop's primary beat, so the
    generate response keeps the pre-engine contract (mobile skips audio
    generation when audio_url is already populated).
    """
    ids = [b for b in beat_ids if b]
    if not ids:
        return {}
    records = session.run(
        "MATCH (b:NarrativeBeat) WHERE b.id IN $ids "
        "RETURN b.id AS id, b.script_body AS script_body, b.audio_url AS audio_url, "
        "b.duration_sec AS audio_duration_sec",
        ids=ids,
    )
    return {
        r["id"]: {
            "script_body": r["script_body"],
            "audio_url": r["audio_url"],
            "audio_duration_sec": r["audio_duration_sec"],
        }
        for r in records
    }


#: The two responses that gained a degradations list. Constrained rather than open, so
#: a handler returning something with no such field cannot be decorated by mistake.
_ReportsDegradations = TypeVar("_ReportsDegradations", TripGenerateResponse, TripComposeResponse)


def _reports_degradations(
    handler: Callable[..., _ReportsDegradations],
) -> Callable[..., _ReportsDegradations]:
    """Hand back everything that quietly degraded while the handler ran.

    OWNER RULING 2026-07-31: a soft failure that only reaches a log file is
    indistinguishable from success to the person looking at the screen. The preview
    has opened a collection scope inline since then; these two did not, so
    ``degradations.record`` was a silent no-op on the phone's own endpoints
    (src/tour/degradations.py:137-139) and a tour planned on estimated walking legs
    looked exactly like one planned on measured ones.

    A DECORATOR RATHER THAN AN ``_impl`` SPLIT, deliberately. Moving each body into a
    private function and leaving a thin route wrapper behind reads the same at the call
    site, but it breaks three checks that read these handlers' SOURCE to prove what they
    do — the one-engine suite finds the ``generate_trip`` function in the parsed module
    and asserts it catches the band refusal, and two others assert the compose body
    names the shared authoring seam. Wrapping keeps each handler an ordinary named
    function with its body intact: ``functools.wraps`` sets ``__wrapped__``, which
    ``inspect.getsource`` follows, and which is also how FastAPI still reads the real
    signature and builds the same dependencies.

    The scope must be opened OUT HERE, around the whole call, because the per-stop
    authoring fan-out records from worker threads and a scope opened inside one of them
    would not be the caller's (src/tour/degradations.py:86-121).
    """

    @functools.wraps(handler)
    def _wrapped(*args, **kwargs) -> _ReportsDegradations:
        with degradation_scope() as collected:
            result = handler(*args, **kwargs)
            rows = summarize(collected)
        return result.model_copy(update={"degradations": rows}) if rows else result

    return _wrapped


@router.post("/trips/generate", response_model=TripGenerateResponse, status_code=201)
@_reports_degradations
def generate_trip(
    body: TripGenerateRequest,
    current_user: dict = Depends(get_current_user),
    session: Session = Depends(get_session),
    driver: Driver = Depends(get_driver),
):
    """Generate a trip by running the tour engine end to end.

    Pipeline: load_paris_corpus -> select_route (density gate: RED refusal
    -> 422) -> select_poi_beats per route POI -> generate (deterministic
    mock glue) -> route_script_to_stops -> persist Trip + ItineraryItems.

    M0b scope notes:
    - `radius_m`, `max_stops`, and `kid_friendly_only` are accepted for
      request back-compat but are inert: the engine derives its own walk
      radius and stop count from `duration_min`.
    - The Script's narration (sentence stream) is not persisted or returned;
      it belongs to the COMPOSE/audio milestone. The validation report that
      `generate` attaches is non-blocking here for the same reason.
    """
    # OWNERSHIP, not existence: an existence-only check let any caller create trips
    # against any profile id. 404 (not 403) on a foreign profile so the endpoint
    # never confirms someone else's profile id.
    if _owned_profile_id(session, current_user["id"], body.profile_id) is None:
        raise HTTPException(404, f"Profile '{body.profile_id}' not found")

    lenses = _resolve_lenses(session, body)

    tour_input = _build_tour_input(
        start=(body.center_lat, body.center_lng),
        duration_min=body.duration_min or DEFAULT_DURATION_MIN,
        city_slug=body.city_slug,
        lenses=lenses,
        round_trip=body.round_trip,
        max_stop_minutes=body.max_stop_minutes,
        end=_end_point(body.end_lat, body.end_lng),
        # THE DAY HAS A CLOCK (Phase 5 S5.10, defect 13): the phone sends the date
        # and the time as two halves and never the joined field, so every
        # phone-planned day was "dateless" to the planner and the living session
        # could not place NOW. Both halves are always present; join them.
        start_datetime=body.start_datetime or f"{body.start_date}T{body.start_time}",
        end_hardness=body.end_hardness,
        **_dial_kwargs(body),
    )

    snapshot = load_paris_corpus(driver, city_slug=tour_input.city_slug)
    try:
        # ONE ALGORITHM. plan_premium_tour is BLOCK 1, the only planner on either
        # surface: it applies the certification walk budget (0.90-1.10, nominal 1.00)
        # and enforces the Premium receipt bar. Phase 4 (design §8.1) deleted
        # pick-one-of-three: the planner plans THE day, and the dials on the request
        # are how a person changes it.
        #
        # Provider-free: Block 1 defaults to the silent glue client, so planning here
        # costs nothing. The words arrive at /trips/{id}/compose.
        with RoutingClient() as routing_client:
            plan = plan_premium_tour(tour_input, snapshot, routing_client=routing_client)
    except (TourabilityRefusedError, CertificationPlanningInfeasibleError) as exc:
        # The certification band refusal reaches this route for the first time in
        # 2026-08-04's collapse: the timebox repair used to run only on the preview
        # path. Uncaught it is a 500 with a stack trace; caught it is the same
        # structured 422 with gap-minutes and alternatives the fixed-destination
        # refusal already returns.
        raise HTTPException(422, _refusal_detail(exc)) from exc
    except (PremiumRouteInfeasibleError, ValueError) as exc:
        raise HTTPException(422, _infeasible_detail()) from exc

    # ONE-element lists on purpose (deviation i): trips persisted before Phase 4
    # store multi-option lists and the compose reader serves them forever, so the
    # stored shape stays a LIST — new trips simply always store one.
    flavours = [plan.route]
    scripts = [plan.source]
    # RETAINED, not discarded: build_route_option reads the vignette beats and the
    # governor's overflow off these sequences to voice the leg/vignette cards.
    sequences = [plan.sequence]
    route = flavours[0]
    script = scripts[0]

    # A non-RED assessment can still yield an empty route (e.g. YELLOW by fill
    # ratio with no tier-3+ anchor candidates). Refuse before persisting —
    # never create a zero-stop Trip.
    if not route.pois:
        raise HTTPException(
            422,
            "No tourable POIs reachable from this start for the requested duration.",
        )

    beats_by_id = {ref.id: ref for refs in snapshot.beats_by_poi.values() for ref in refs}
    # KE1: per-stop "keep exploring" extras (un-voiced beats), computed from the
    # SAME merged pool the voiced plan used so they are exactly full-minus-voiced.
    extra_by_poi = build_poi_extra_beats(
        flavours[0],
        snapshot,
        {sp.id: sp.beat_ids for sp in script.selected_pois},
        lenses=lenses,
    )
    clocks, _audio = _wire_clocks(
        route, tour_input, snapshot, clock_start=_day_start(tour_input, body.start_time)
    )
    stops = route_script_to_stops(
        script.selected_pois,
        beats_by_id,
        clocks,
        script=script,
        extra_by_poi=extra_by_poi,
        # Phase 7 S7.3: WHERE each stop's piece plays — placed ONCE by the one rule,
        # stored on the item, carried on the wire (design §5.6; W7.2 R1).
        triggers=place_stops(route),
        # Phase 7 S7.7 (B): the marquee's reviewed anchors, off the same route — the
        # story is cut at them into chapters (design §5.6 segments; W7.2 R4).
        anchors=place_anchors(route),
        # Phase 9 S3: the label answers the request — the asked family, expanded
        # the same one hop the matcher and the gate use.
        prefer_lenses=expanded_interest_lenses(tour_input.lenses, snapshot),
    )

    # Step 4.6: persist the RESOLVED engine input + every flavour's ordered
    # poi ids so /compose rebuilds the user's PICK without re-running
    # selection (corpus/routing drift must never swap the picked route).
    tour_input_json = json.dumps(
        {
            # THE WHOLE RESOLVED INPUT (Phase 5 S5.8): every axis and dial the
            # day was planned with — pins, party, pace, cap, cadence, weather,
            # the four dials — because the living session replans FROM this
            # record and the person's protected set (pins, requested rests, a
            # declared finish) lives in it. Additive over the legacy keys below,
            # which stay spelled out so a reader of an older record sees the same
            # shape it always did.
            **tour_input.model_dump(mode="json"),
            "start": list(tour_input.start),
            "end": list(tour_input.end) if tour_input.end else None,
            "duration_min": tour_input.duration_min,
            "city_slug": tour_input.city_slug,
            "lenses": tour_input.lenses,
            "round_trip": tour_input.round_trip,
            "start_time": body.start_time,
            # The planner's clock (redesign §2.2/§2.3): compose rebuilds the
            # pick from THIS record, so a clock not persisted here is a clock
            # the composed tour silently loses.
            "start_datetime": tour_input.start_datetime,
            "end_hardness": tour_input.end_hardness,
        }
    )
    # EVERYTHING ABOUT A FLAVOUR THAT COMPOSE CANNOT WORK OUT AGAIN. Ordered poi ids,
    # the C9 exempt-anchor identity (Held-Karp reorders pois and the compose-rebuilt
    # route has no greedy locals), the walk-past sights, the thin-area disclosure, and
    # the elapsed time this flavour was OFFERED as. Compose hands all of it straight
    # back into the route it rebuilds, so the tour the traveller is given is the tour
    # they picked rather than a re-derivation that agrees only most of the time.
    # Legacy trips stored a bare id list; the compose reader treats those as fail-open.
    options_json = json.dumps(
        [
            {
                "poi_ids": [p.id for p in flavour.pois],
                "start_anchor_poi_id": flavour.start_anchor_poi_id,
                "fixed_end_poi_id": flavour.fixed_end_poi_id,
                # leg index -> the walk-past POIs on that leg, as strings because
                # JSON object keys cannot be integers.
                "vignette_poi_ids": {
                    str(leg_idx): [p.id for p in vignette_pois]
                    for leg_idx, vignette_pois in flavour.vignettes.items()
                },
                "tourability": (
                    flavour.tourability.model_dump(mode="json")
                    if flavour.tourability is not None
                    else None
                ),
                # How long this visitor spends AT each stop. It CANNOT be
                # recomputed on compose: it is priced against a CorpusSnapshot and
                # the visitor's declared interest, and the rebuild has the corpus
                # but not the pricing call. Without it every dwell silently
                # collapses back to the length of its own narration, which is the
                # planned tour quietly becoming a different, shorter tour.
                "planned_visit_seconds": dict(flavour.planned_visit_seconds),
                # The seconds of line priced at each stop's arrival hour (Phase 7
                # S7.5; design §5.6): priced at selection's one site against the
                # snapshot, like the visit above, and read by the audio placement
                # rule — a rebuild without it silently unqueues every stop.
                "planned_queue_seconds": dict(flavour.planned_queue_seconds),
                # The door and the seconds before it (Phase 7 S7.6): which side
                # of the door each visit lives on and the placed outside seconds
                # — priced at the same site; the rule's threshold under a roof.
                "visit_goes_inside": dict(flavour.visit_goes_inside),
                "planned_outside_seconds": dict(flavour.planned_outside_seconds),
                # How far short of the request this tour honestly runs. It cannot
                # be recomputed on compose either: it is the planner's verdict at
                # the gate, and a rebuild has no gate. Dropping it would silently
                # remove the sentence that explains why a five-hour request came
                # back as four hours forty.
                "elapsed_shortfall_seconds": flavour.elapsed_shortfall_seconds,
                "eta_seconds": option_eta_seconds(flavour, fl_script),
            }
            for flavour, fl_script in zip(flavours, scripts, strict=True)
        ]
    )

    trip_name = body.trip_name or f"Trip ({body.start_date})"
    result = create_trip_with_stops(
        session,
        trip_name=trip_name,
        profile_id=body.profile_id,
        start_date=body.start_date,
        end_date=body.end_date,
        stops=stops,
        tour_input_json=tour_input_json,
        options_json=options_json,
    )

    display_map = _lens_display_map(session, {s["lens_name"] for s in stops if s["lens_name"]})
    audio_by_beat = _primary_beat_audio(session, [s["primary_beat_id"] for s in stops])
    # Walking leg INTO each stop (transits[i] arrives at pois[i]); response-only
    # in M2 — not persisted on ItineraryItem until routing runs in production.
    polyline_by_poi = {t.to_poi_id: t.polyline for t in route.transits if t.to_poi_id}
    stops_out = [
        GeneratedStop(
            sort_order=s["sort_order"],
            poi_id=s["poi_id"],
            poi_name=s["poi_name"],
            lat=s["lat"],
            lng=s["lng"],
            beat_id=s["primary_beat_id"],
            beat_ids=s["beat_ids"],
            extra_beat_ids=s["extra_beat_ids"],
            lens_name=s["lens_name"],
            lens_display=display_map.get(s["lens_name"]) if s["lens_name"] else None,
            duration_min=s["duration_min"],
            importance_tier=s["importance_tier"],
            start_time=s["start_time"],
            dwell_seconds=s["dwell_seconds"],
            close_text=s.get("close_text"),
            trigger=s.get("trigger"),
            leg_narration=s.get("leg_narration"),
            leg_from_poi_id=s.get("leg_from_poi_id"),
            segments=s.get("segments") or [],
            transit_polyline=polyline_by_poi.get(s["poi_id"]),
            **audio_by_beat.get(s["primary_beat_id"], {}),
        )
        for s in stops
    ]
    total_duration = sum(s["duration_min"] for s in stops)
    anchor_count = sum(1 for s in stops if s["importance_tier"] == 5)

    options = [
        build_route_option(
            flavour,
            fl_script,
            beats_by_id,
            route_id=f"{result['trip_id']}-opt{i + 1}",
            snapshot=snapshot,
            sequence=fl_sequence,
        )
        for i, (flavour, fl_script, fl_sequence) in enumerate(
            zip(flavours, scripts, sequences, strict=True)
        )
    ]

    return TripGenerateResponse(
        trip_id=result["trip_id"],
        trip_name=result["trip_name"],
        profile_id=body.profile_id,
        total_stops=len(stops),
        total_duration_min=total_duration,
        anchor_count=anchor_count,
        flavour_count=len(stops) - anchor_count,
        lens_coverage=script.lens_coverage,
        stops=stops_out,
        options=options,
    )


# The per-stop authoring seam fires exactly ONE physical call per dwell stop with no
# retry of its own (src/tour/premium_tour.py::execute_premium_plan). Phase 8 S8.3/S8.4
# re-derived the attempt count into a real counter again: the compose endpoint may
# spend ONE bounded targeted re-roll of only the failing stops after a VERIFY refusal
# (S8.3's "bounded retry" — measured live 2026-08-23: Camille's day died 422 on one
# bad roll that two clean rolls served) and ONE more after a fixable rubric blocker
# (S8.4, `compose_fixable`), so the count the phone reads out of the 422 detail
# (mobile/lib/services/trip_service.dart:229) is 1, 2 or 3 — honestly counted.
COMPOSE_ATTEMPTS = 1


def _rubric_fixable_stops(rubric: RubricReport, composed, seq: BeatSequence) -> set[int]:
    """The stops whose BLOCKERs one targeted recompose could plausibly fix.

    THE one classifier is ``quality_rubric.compose_fixable`` (standard §7's
    amendment: servable and loop-worthy are different predicates); this helper
    only builds each finding's ``StopMaterial`` from what the composer actually
    HAD (the seated beats' body words) and PRODUCED (the rendered words the
    rubric already counted). Findings with no stop (C3, C7) classify themselves
    unfixable; a stop index outside the roster is skipped, never guessed."""
    beats_by_poi_id = {pb.poi_id: pb.beats for pb in seq.poi_beats}
    words_by_stop = rubric.stats.get("words_by_stop", {})
    out: set[int] = set()
    for finding in rubric.blockers:
        if finding.stop_idx is None or finding.stop_idx >= len(composed.selected_pois):
            continue
        poi = composed.selected_pois[finding.stop_idx]
        material = StopMaterial(
            seated_body_words=sum(
                len((beat.script_body or "").split())
                for beat in beats_by_poi_id.get(poi.id, ())
            ),
            composed_words=int(words_by_stop.get(finding.stop_idx, 0)),
        )
        if compose_fixable(finding, material):
            out.add(finding.stop_idx)
    return out


@router.post("/trips/{trip_id}/compose", response_model=TripComposeResponse)
@_reports_degradations
def compose_trip(
    request: Request,
    trip_id: str,
    body: TripComposeRequest,
    current_user: dict = Depends(get_current_user),
    session: Session = Depends(get_session),
    driver: Driver = Depends(get_driver),
    premium_executor: PremiumComposeExecutor = Depends(get_premium_compose_executor),
    faithfulness_checker: FaithfulnessChecker | None = Depends(get_faithfulness_checker),
):
    """Second step of the preview/compose split (Phase 4 Step 4.7, spec §5).

    Rebuilds the user's PICK from the poi ids persisted at generate time —
    NEVER re-running selection, so corpus/routing drift cannot swap the
    route — then authors that prebuilt route ONE STOP AT A TIME through the
    same seam /trips/preview and the batch runner use, and re-persists the
    trip's stops with the authored narration (fresh item ids, no audio
    fields). NO TTS here: /audio/generate-trip-stops voices the narration
    afterwards, which by then has passed the gate. A refused flavour is a
    structured 422; the trip is left untouched so the client can offer
    another flavour.

    CAPTAIN-SCOPED: this route destroys the trip's stop ids and burns its
    one-shot compose budget, so it is authenticated and stays a WRITE — one
    writer per living session. Crew (family co-members on the shared day) get
    the typed 403; a trip the caller has no role on is reported as 404, never
    403 — a 403 would confirm the id exists.
    """
    _captain_trip_or_404(session, current_user["id"], trip_id)

    inputs = get_trip_compose_inputs(session, trip_id)
    if inputs is None:
        raise HTTPException(404, f"Trip '{trip_id}' not found")
    # THE FROZEN TRIP IS DELETED (Phase 5 S5.8, design §8.2): a second compose is
    # not a conflict, it is the living session's version N+1 — the trip's stops are
    # re-persisted through the same `replace_trip_stops`, and `composed_route_id`
    # (read for legacy trips by the crud reader) is ignored.
    tour_input_dict, options = inputs["tour_input"], inputs["options"]
    if not tour_input_dict or not options:
        raise HTTPException(
            409,
            {"reason": "missing_compose_inputs", "detail": "trip predates the compose split"},
        )

    match = re.fullmatch(re.escape(trip_id) + r"-opt(\d+)", body.route_id)
    option_n = int(match.group(1)) if match else 0
    if not (1 <= option_n <= len(options)):
        raise HTTPException(404, f"Unknown route_id '{body.route_id}' for trip '{trip_id}'")
    entry = options[option_n - 1]
    if isinstance(entry, dict):  # per-flavour format: everything generate decided
        poi_ids = entry["poi_ids"]
        chosen = entry
    else:  # legacy bare id list (trips generated pre-C9f): fail open (uncapped).
        poi_ids = entry
        chosen = {}

    tour_input = _restore_tour_input(tour_input_dict)

    snapshot = load_paris_corpus(driver, city_slug=tour_input.city_slug)
    pois_by_id = {p.id: p for p in snapshot.pois}
    picked = _resolve_persisted_pick(poi_ids, pois_by_id)

    spine = pick_spine_area(tour_input.start[0], tour_input.start[1], picked, snapshot)
    # The SAME certification walk budget the phone and the workbench plan with
    # (0.90-1.10, nominal 1.00). Rebuilding the persisted pick with no policy is the
    # third route into the legacy 0.83 flat budget, which step 6 deletes outright.
    planning_policy = certification_planning_policy(policy_id=PREMIUM_MODULE_VERSION)
    with RoutingClient() as routing_client:
        # Captured INSIDE the block: RoutingClient is a context manager, and the
        # authoring plan needs this value after it closes.
        #
        # GUARDED, because this is the one call on this path that a Valhalla outage can
        # hard-fail. Every walking leg below independently falls back to a straight-line
        # estimate when the container is unreachable; routing_version() does a live
        # GET /status and treats a missing or malformed answer as an error
        # (routing_client.py:183-200). Left bare it escapes as a generic 500, or worse
        # is swept up by the ValueError branch far below and relabelled
        # compose_verification_failed -- a code meaning "the narrator wrote something
        # untraceable", which blames the writer for a container that is still booting.
        #
        # LABELLED, NOT REFUSED (resolved 2026-08-05). This was a named, retryable 503,
        # which is the same answer PLANNING used to give the same outage and no longer
        # does: ondoway-valhalla is a real network dependency that cold-starts and
        # rebuilds tiles (render.yaml:98-124), and refusing here takes tour writing down
        # app-wide for the duration -- after the traveller has already chosen a route.
        # The provenance a refusal protects does not exist during an outage anyway,
        # because unmeasured legs were measured by no engine; resolve_routing_version
        # records that plainly and stamps an unmistakable value instead. Per-leg
        # provenance is untouched, since every measured leg carries its own receipt.
        routing_version = resolve_routing_version(routing_client)
        # BUILT RIGHT ONCE. Everything generate decided about this flavour and could
        # not be worked out again — the walk-past sights, the thin-area disclosure,
        # which stops were the marquee ones — is handed straight in, so the rebuilt
        # route IS the offered route rather than a fresh derivation patched afterwards.
        # This used to re-run the vignette picker (its only caller anywhere, and it
        # lowercased the lens set where generate did not) and then copy the anchor ids
        # back on: two chances for the composed tour to stop being the chosen one.
        # Empty for legacy trips, which restores exactly the old fail-open behaviour.
        route = summarise_route(
            picked,
            start_lat=tour_input.start[0],
            start_lng=tour_input.start[1],
            round_trip=tour_input.round_trip,
            duration_min=tour_input.duration_min,
            spine_area=spine,
            routing_client=routing_client,
            planning_policy=planning_policy,
            vignettes=_restored_vignettes(chosen.get("vignette_poi_ids"), pois_by_id),
            tourability=_restored_tourability(chosen.get("tourability")),
            start_anchor_poi_id=chosen.get("start_anchor_poi_id"),
            fixed_end_poi_id=chosen.get("fixed_end_poi_id"),
            # The FIFTH extra. A trip saved before this key existed returns None
            # and lands on the empty default, which reproduces the old behaviour
            # rather than raising — the same fail-open the other four use.
            planned_visit_seconds={
                str(poi_id): int(seconds)
                for poi_id, seconds in (chosen.get("planned_visit_seconds") or {}).items()
            },
            # The SIXTH extra, same fail-open shape: a trip saved before this key
            # existed restores at 0, which reads as "not short enough to mention"
            # and reproduces exactly how those trips have always composed.
            elapsed_shortfall_seconds=int(chosen.get("elapsed_shortfall_seconds") or 0),
            # The SEVENTH extra (Phase 7 S7.5): the priced line per stop, so the
            # composed day's triggers carry the queue the planned day's did. A trip
            # saved before this key restores to the empty map — no stop queued,
            # exactly how those trips have always composed.
            planned_queue_seconds={
                str(poi_id): int(seconds)
                for poi_id, seconds in (chosen.get("planned_queue_seconds") or {}).items()
            },
            # The EIGHTH and NINTH (Phase 7 S7.6): the door and the placed outside
            # seconds, the same fail-open shape — an older trip has no door anywhere.
            visit_goes_inside={
                str(poi_id): bool(inside)
                for poi_id, inside in (chosen.get("visit_goes_inside") or {}).items()
            },
            planned_outside_seconds={
                str(poi_id): int(seconds)
                for poi_id, seconds in (chosen.get("planned_outside_seconds") or {}).items()
            },
            # THE SURFACE THE DAY WAS PLANNED UNDER (Phase 6 S6.1a; design §2.4, plan
            # S2.7: "never a route selected under one costing and reported under
            # another"). Selection routes a take-it-easy day step-free; until this
            # line the rebuild here routed the SAME pick on the default surface, so
            # the composed day's legs, clocks and polylines were not the day the
            # person was shown (measured 2026-08-19 on Rosemary's day). "any" = None.
            costing_options_override=ROUTE_SURFACE_COSTING_OVERRIDES[tour_input.route_surface],
        )
        # SAY SO IF THE LEGS WERE ESTIMATED. This route is rebuilt here rather than
        # planned, so it never passes the planner that labels an unmeasured walk — and
        # AC-18 allows no path on which such a route reaches a person unlabelled.
        record_routing_degradations(route, component="trips.compose_trip")

    # C9f-i: compose goes through the SAME shared governor seam as
    # generate/preview (unifies the choke point; the anchor-id restore above lets
    # the cap exempt the marquee at compose too). v4 caps a dominating stop and
    # surfaces its overflow.
    capped = build_poi_beat_plans_capped(
        route,
        snapshot,
        lenses=tour_input.lenses,
        end_is_none=tour_input.end is None,
        narration_density=tour_input.narration_density,
    )
    plans = tuple(pb for pb, _ in capped)
    overflow_by_poi = {pb.poi_id: ov for pb, ov in capped if ov}
    vignette_beats = select_vignette_beats(
        route.vignettes, snapshot.beats_by_poi, lenses=tour_input.lenses
    )
    seq = BeatSequence(
        poi_beats=tuple(plans), vignette_beats=vignette_beats, overflow_by_poi=overflow_by_poi
    )
    stitched = generate(seq, route, tour_input)

    # ONE ALGORITHM, ONE SEAM. This persisted endpoint authors the route it just
    # rebuilt through the SAME Block-2 seam /trips/preview and the batch runner use:
    # plan_premium_authoring -> execute_premium_plan -> finalize_premium_tour. It
    # keeps its fail-before-mutation contract (nothing below this block writes until
    # authoring has passed VERIFY). Planning is provider-free, so the exact physical
    # call count is known before a single call is billed.
    #
    # The three anti-hallucination gates are no longer this call site's to choose:
    # finalize_premium_tour hard-codes enforce_claim_coverage and
    # scan_glue_for_invention ON and gives faithfulness_checker no default, so a live
    # surface cannot silently omit one. Same three gates, same arguments as before.
    #
    # Resolved BEFORE the try and before any spend: an unresolvable build fingerprint
    # (dirty local tree, malformed deploy SHA) is an environment fault, not an
    # authoring failure, and must never be relabelled compose_verification_failed —
    # that code means "the narrator wrote something untraceable" and would blame the
    # writer for an engine fault. Same reasoning as /trips/preview.
    try:
        build_identity = resolve_build_identity()
    except Exception as exc:
        logging.getLogger("ondoway.api").exception(
            "Compose could not resolve a build fingerprint for trip=%s", trip_id
        )
        raise HTTPException(
            503,
            {"reason": "build_fingerprint_unavailable", "detail": str(exc)},
            headers={"Retry-After": "30"},
        ) from exc

    attempts = COMPOSE_ATTEMPTS
    rerolled_for_rubric: set[int] = set()

    def _reroll_stops(stop_indexes: set[int], responses):
        """ONE targeted same-request re-roll (Phase 8 S8.3/S8.4): fresh provider
        rolls for ONLY the named stops — the SAME unit and the SAME envelope, so
        every replay hash still binds — spliced into the response set. Recorded
        on the degradations channel: a re-roll is real spend and a real signal,
        never silent."""
        nonlocal attempts
        attempts += 1
        unit_index = {unit.stop_index: i for i, unit in enumerate(plan.units)}
        patched = list(responses)
        for stop_index in sorted(stop_indexes):
            patched[unit_index[stop_index]] = premium_executor.execute(
                plan.units[unit_index[stop_index]]
            )
        record(
            kind="compose_stop_rerolled",
            human=(
                "One or more stops were written a second time before serving, "
                "because the first attempt failed a quality or grounding check."
            ),
            component="trips.compose_trip",
            stops=",".join(str(s) for s in sorted(stop_indexes)),
        )
        return tuple(patched)

    try:
        plan = plan_premium_authoring(
            stitched,
            seq,
            route,
            snapshot=snapshot,
            snapshot_sha256=exact_snapshot_sha256(snapshot),
            routing_version=routing_version,
            policy_version=planning_policy.policy_id,
        )
        # The physical calls are the REAL number this compose will make (one per
        # dwell stop), and they happen HERE. (Until Phase 5 S5.8 a second compose was
        # a 409 before this point; a second compose is now version N+1 of the session
        # — design §8.2 — and pays for its authoring like the first.)
        with _upstream_provider_errors():
            physical_responses = execute_premium_plan(
                plan,
                executor=premium_executor,
                receipt_sink=EphemeralReceiptSink(),
            )
            try:
                premium_result = finalize_premium_tour(
                    plan,
                    physical_responses,
                    faithfulness_checker=faithfulness_checker,
                    build_identity=build_identity,
                )
            except ComposeVerificationError as refusal:
                # Phase 8 S8.3 — THE BOUNDED RETRY, the step's own words:
                # untraceable writer output "refuses at AUTHORING time with the
                # bounded retry, so her day composes deterministically". Measured
                # live 2026-08-23: Camille's run-1 compose died 422 on ONE bad
                # roll (`fused_across_playback_contexts`, stop 3) while the
                # identical request rolled clean twice after. The failing stops
                # are the ones the report NAMES; a provenance failure is
                # corpus-side and a re-roll cannot converge on it, so it refuses
                # at once. A stop that fails twice still refuses — bounded means
                # bounded.
                failing = {
                    s.stop_idx
                    for s in refusal.report.untraceable_sentences
                    if s.stop_idx is not None
                } | {
                    s.stop_idx
                    for s, _code in refusal.report.forbidden_phrase_hits
                    if s.stop_idx is not None
                }
                if refusal.report.provenance_failures or not failing:
                    raise
                logging.getLogger("ondoway.api").warning(
                    "Compose VERIFY failed on stop(s) %s for trip=%s; spending the "
                    "one targeted re-roll before refusing",
                    sorted(failing),
                    trip_id,
                )
                physical_responses = _reroll_stops(failing, physical_responses)
                premium_result = finalize_premium_tour(
                    plan,
                    physical_responses,
                    faithfulness_checker=faithfulness_checker,
                    build_identity=build_identity,
                )
            composed = premium_result.blueprint.script
    except ComposeVerificationError as exc:
        # NAME WHAT BLOCKED, server-side. The 422 below carries counts only — the wire
        # must not leak provider prose — and a count is not enough to act on (the
        # author path learned this first: see `_author_preview_impl`'s catch-all). Only
        # the three STRUCTURAL classes block (contract.py `ValidationReport.passed`);
        # each is logged with its provenance and truncated text so one refused
        # compose identifies the rule instead of costing another paid run per guess.
        _log = logging.getLogger("ondoway.api")
        _log.error(
            "Compose refused by VERIFY for trip=%s: %d untraceable, %d forbidden, "
            "%d provenance (advisory: %d faithfulness, %d coverage)",
            trip_id,
            len(exc.report.untraceable_sentences),
            len(exc.report.forbidden_phrase_hits),
            len(exc.report.provenance_failures),
            len(exc.report.faithfulness_failures),
            len(exc.report.coverage_failures),
        )
        for _s in exc.report.untraceable_sentences:
            _log.error(
                "  UNTRACEABLE stop=%s source_type=%r source_id=%r cited=%r text=%.120r",
                _s.stop_idx,
                _s.source_type,
                _s.source_id,
                tuple(_s.cited_beat_ids or ()),
                _s.text,
            )
        for _s, _code in exc.report.forbidden_phrase_hits:
            _log.error(
                "  FORBIDDEN %s stop=%s source_id=%r text=%.120r",
                _code,
                _s.stop_idx,
                _s.source_id,
                _s.text,
            )
        raise HTTPException(
            422,
            {
                "reason": "compose_verification_failed",
                # The REAL count this endpoint made (S8.3's bounded retry may
                # have spent a targeted re-roll before this refusal stood).
                "attempts": attempts,
                "untraceable": len(exc.report.untraceable_sentences),
                "forbidden": len(exc.report.forbidden_phrase_hits),
                "provenance": len(exc.report.provenance_failures),
                "faithfulness": len(exc.report.faithfulness_failures),
            },
        ) from exc
    except ValueError as exc:
        # The seam refuses a shape it cannot author (a stop the stitch dropped, a
        # route past the anchor cap) and a provider payload it cannot parse by
        # raising ValueError. On THIS path that is still a refusal, not a server
        # fault: the trip is untouched either way, and a 500 would strand the phone
        # instead of letting it offer another flavour (D2). Logged in full, because
        # the wire deliberately keeps the stable non-leaking shape below.
        logging.getLogger("ondoway.api").exception(
            "Per-stop authoring could not run for trip=%s route=%s stops=%s",
            trip_id,
            body.route_id,
            len(picked),
        )
        raise HTTPException(
            422,
            {
                "reason": "compose_verification_failed",
                "attempts": attempts,
                "untraceable": 0,
                "forbidden": 0,
                "provenance": 0,
                "faithfulness": 0,
            },
        ) from exc

    # ── Phase 8 S8.4 (design §7.2; quality standard §7): THE FLOOR GATES SERVING.
    # W8.1(b) proved the gap live: Greta's served W7.11 day carried `BLOCKER
    # C3-thin` and was composed, persisted, voiced and served, because only the
    # workbench's advisory preview ever ran `score_tour`. The persisted path now
    # refuses a blocker-carrying day BEFORE the full tellings' spend and BEFORE
    # any write — after spending at most ONE targeted recompose on the stops
    # `compose_fixable` says a fresh roll could plausibly fix (the standard §7
    # amendment: a defect that is upstream of compose reproduces identically, so
    # looping there is spend with zero chance of convergence).
    rubric = score_tour(composed, route, snapshot.beats_by_poi, beat_sequence=seq)
    if not rubric.passed:
        rerolled_for_rubric = _rubric_fixable_stops(rubric, composed, seq)
        if rerolled_for_rubric and attempts < 3:
            logging.getLogger("ondoway.api").warning(
                "Compose failed the quality floor on stop(s) %s for trip=%s; spending "
                "the one targeted recompose before refusing",
                sorted(rerolled_for_rubric),
                trip_id,
            )
            try:
                with _upstream_provider_errors():
                    physical_responses = _reroll_stops(rerolled_for_rubric, physical_responses)
                    premium_result = finalize_premium_tour(
                        plan,
                        physical_responses,
                        faithfulness_checker=faithfulness_checker,
                        build_identity=build_identity,
                    )
                composed = premium_result.blueprint.script
                rubric = score_tour(composed, route, snapshot.beats_by_poi, beat_sequence=seq)
            except (ComposeVerificationError, ValueError):
                # The recompose wrote something VERIFY refuses; the day already
                # carries rubric blockers, so the refusal below stands on those.
                logging.getLogger("ondoway.api").exception(
                    "The targeted recompose itself failed VERIFY for trip=%s; refusing "
                    "on the original rubric blockers",
                    trip_id,
                )
        if not rubric.passed:
            _log = logging.getLogger("ondoway.api")
            _log.error(
                "Compose refused by the QUALITY FLOOR for trip=%s: %d blocker(s), "
                "%d warning(s)",
                trip_id,
                len(rubric.blockers),
                len(rubric.warnings),
            )
            # R8 (W8.2): the named gate, the stop, and OUR OWN message with its
            # numbers — logged in full here, and carried on the wire below (the
            # rubric's prose is this engine's, never the provider's).
            for finding in rubric.blockers:
                _log.error(
                    "  BLOCKER %s stop=%s poi=%r: %s",
                    finding.check,
                    finding.stop_idx,
                    finding.poi_name,
                    finding.message,
                )
            raise HTTPException(
                422,
                {
                    "reason": "tour_quality_blocked",
                    "attempts": attempts,
                    "blockers": [
                        {
                            "check": finding.check,
                            "stop_idx": finding.stop_idx,
                            "poi_name": finding.poi_name,
                            "message": finding.message,
                        }
                        for finding in rubric.blockers
                    ],
                    "warnings": len(rubric.warnings),
                    "recomposed_stops": sorted(rerolled_for_rubric),
                },
            )

    beats_by_id = {ref.id: ref for refs in snapshot.beats_by_poi.values() for ref in refs}
    # KE2: recompute the keep-exploring extras HERE (never trust generate-time
    # values) from the composed script's voiced beats, the SAME way generate does
    # (build_poi_extra_beats over the rebuilt route), so extra_beat_ids stays
    # exactly full-minus-voiced. The extras are curated corpus beats — faithful by
    # construction — so their narration is stitched DETERMINISTICALLY from their
    # script_bodies (build_poi_extra_narration), NOT re-composed through the LLM
    # VERIFY gate: there is nothing freely-composed to entail. The stops carry BOTH
    # extra_beat_ids (via route_script_to_stops) and extra_narration, keyed by poi.
    # Phase 6 S6.6 (design §5.5; W6.2 R3): THE FULL TELLING for every MAJOR stop —
    # a second COMPOSED piece from the stop's own second story (the overflow the cap
    # trimmed), through the same one-stop seam, with the tight telling as ALREADY
    # TOLD. Optional enrichment end to end: a gate miss or provider fault drops THAT
    # stop's full telling (reported on the degradations channel) and never the day.
    full_by_stop: dict[int, FullTelling] = {}
    majors = full_telling_majors(composed, seq, route)
    if majors:
        overflow_refs_by_poi = {
            poi.id: tuple(
                beat
                for beat in snapshot.beats_by_poi.get(poi.id, ())
                if beat.id in set(seq.overflow_by_poi.get(poi.id, ()))
            )
            for poi in route.pois
        }
        full_seq = BeatSequence(
            poi_beats=tuple(
                POIBeats(
                    poi_id=poi.id,
                    poi_name=poi.name,
                    ordering_strategy="narrative_function",
                    beats=overflow_refs_by_poi.get(poi.id, ()),
                )
                for poi in route.pois
                if overflow_refs_by_poi.get(poi.id)
            ),
            overflow_by_poi=seq.overflow_by_poi,
        )
        for stop_index, full_budget in majors.items():
            try:
                full_plan = plan_premium_full_telling(
                    composed,
                    full_seq,
                    route,
                    stop_index=stop_index,
                    budget=full_budget,
                    snapshot=snapshot,
                    snapshot_sha256=exact_snapshot_sha256(snapshot),
                    routing_version=routing_version,
                    policy_version=planning_policy.policy_id,
                )
                full_responses = execute_premium_plan(
                    full_plan,
                    executor=premium_executor,
                    receipt_sink=EphemeralReceiptSink(),
                )
                full = finalize_premium_full_telling(
                    full_plan,
                    full_responses,
                    budget=full_budget,
                    faithfulness_checker=faithfulness_checker,
                )
            except Exception as exc:  # provider weather or a plan fault: the stop
                # keeps its tight telling; the on-demand route still answers.
                record(
                    kind=FULL_TELLING_DROPPED_DEGRADATION,
                    human=(
                        "The longer telling written for one of the main stops was "
                        "dropped; the stop keeps its normal telling and the tap for "
                        "more still works."
                    ),
                    component="trips.compose_trip",
                    cause=f"authoring the full telling failed: {type(exc).__name__}: {exc}",
                    stop_index=str(stop_index),
                )
                continue
            if full is not None:
                full_by_stop[stop_index] = full

    extra_by_poi = build_poi_extra_beats(
        route,
        snapshot,
        {sp.id: sp.beat_ids for sp in composed.selected_pois},
        lenses=tour_input.lenses,
    )
    extra_narration_by_poi = build_poi_extra_narration(extra_by_poi, snapshot)
    day_start = _day_start(tour_input, tour_input_dict.get("start_time"))
    clocks, _audio = _wire_clocks(route, tour_input, snapshot, clock_start=day_start)
    stops = route_script_to_stops(
        composed.selected_pois,
        beats_by_id,
        clocks,
        script=composed,
        extra_by_poi=extra_by_poi,
        # Phase 7 S7.3: the rebuilt route's stops placed by the SAME one rule generate
        # used — the composed day's geometry is the day's geometry.
        triggers=place_stops(route),
        # Phase 7 S7.7 (B): the marquee's reviewed anchors, off the same route — the
        # story is cut at them into chapters (design §5.6 segments; W7.2 R4).
        anchors=place_anchors(route),
        # Phase 9 S3: the label answers the request — the asked family, expanded
        # the same one hop the matcher and the gate use.
        prefer_lenses=expanded_interest_lenses(tour_input.lenses, snapshot),
    )
    for idx, stop in enumerate(stops):
        stop["extra_narration"] = extra_narration_by_poi.get(stop["poi_id"])
        # Phase 6 S6.5: the writer's THREADS ride beside the narration, keyed by the
        # name of the stop that may come right before this one when the day replans.
        stop["thread_lines"] = premium_result.threads_by_stop.get(idx) or None
        # Phase 6 S6.6: a major stop's full telling rides beside its tight one.
        full = full_by_stop.get(idx)
        stop["full_narration"] = full.narration if full else None
        stop["full_close_text"] = full.close_text if full else None
    item_ids = replace_trip_stops(session, trip_id, stops)

    display_map = _lens_display_map(session, {s["lens_name"] for s in stops if s["lens_name"]})
    stops_out = [
        GeneratedStop(
            sort_order=s["sort_order"],
            stop_id=item_ids[i],
            poi_id=s["poi_id"],
            poi_name=s["poi_name"],
            lat=s["lat"],
            lng=s["lng"],
            beat_id=s["primary_beat_id"],
            beat_ids=s["beat_ids"],
            extra_beat_ids=s["extra_beat_ids"],
            lens_name=s["lens_name"],
            lens_display=display_map.get(s["lens_name"]) if s["lens_name"] else None,
            duration_min=s["duration_min"],
            importance_tier=s["importance_tier"],
            start_time=s["start_time"],
            narration=s.get("narration"),
            extra_narration=s.get("extra_narration"),
            close_text=s.get("close_text"),
            thread_lines=s.get("thread_lines"),
            full_narration=s.get("full_narration"),
            full_close_text=s.get("full_close_text"),
            close_audio_url=s.get("close_audio_url"),
            thread_audio_urls=s.get("thread_audio_urls"),
            full_close_audio_url=s.get("full_close_audio_url"),
            dwell_seconds=s["dwell_seconds"],
            trigger=s.get("trigger"),
            leg_narration=s.get("leg_narration"),
            leg_from_poi_id=s.get("leg_from_poi_id"),
            segments=s.get("segments") or [],
        )
        for i, s in enumerate(stops)
    ]
    # THE LIVING SESSION, VERSION N+1 (Phase 5 S5.8): the day the person will
    # stand in, with its contingency set beside it (§4.6), computed ONCE here on
    # the server and persisted — the phone SELECTS from it, it never decides.
    plan_version = int(inputs["plan_version"] or 0) + 1
    session_plan = _session_plan(
        trip_id,
        tour_input,
        route,
        stops_out,
        snapshot=snapshot,
        plan_version=plan_version,
        clock_start=day_start,
        day_start_hhmm=hhmm(day_start),
    )
    write_trip_session(
        session,
        trip_id,
        plan_version=plan_version,
        session_json=session_plan.model_dump_json(),
    )
    return TripComposeResponse(
        trip_id=trip_id,
        route_id=body.route_id,
        attempts=attempts,
        stops=stops_out,
        plan_version=plan_version,
    )


# ---------------------------------------------------------------------------
# THE LIVING SESSION (Phase 5 S5.8, design §4.6/§8.2). The frozen trip — a stop list
# written once, narrated once, refusing a second compose — is deleted. A trip's day
# is a SESSION with versions: compose writes version N+1 of the stops and, beside
# them, the CONTINGENCY SET the phone selects from; a replan mints version N+1 over
# the SAME items (no re-authoring, so no audio is lost). One replan brain, here.
# ---------------------------------------------------------------------------


def _finish_name(tour_input: TourInput, route=None) -> str:
    """What the finish is called in a line or an arm. The PLACE when the day ends at
    one the person named (W5.14: "say 'the Orsay' — 'your finish' is your word");
    else "your finish" / "your start"."""
    if route is not None:
        finish = tour_input.end if tour_input.end is not None else tour_input.start
        for poi in route.pois:
            if poi.poi_role == "body" or poi.id.startswith(END_B_SENTINEL_PREFIX):
                continue
            if haversine_m(finish[0], finish[1], poi.lat, poi.lng) <= OWN_PLACE_RADIUS_M:
                return poi.name
    return "your finish" if tour_input.end is not None else "your start"


def _person_protected(tour_input: TourInput, route) -> tuple[str, ...]:
    """W5.2 R1.5 — the person's own promises: the stops they pinned and the rests
    the party or dial asked for (the body stops seated in the day), and — W5.14 —
    the place they NAMED as where the day begins or ends when the day visits it
    (`own_place_ids`). A declared finish is protected by construction (every tail
    ends there); the planner's anchor is NOT protected on an open, unpinned walk —
    a session never asks a question about it (Fiona & Dev, Julien)."""
    rests = sorted(p.id for p in route.pois if p.poi_role == "body")
    return tuple(
        dict.fromkeys([*tour_input.pinned_poi_ids, *own_place_ids(route, tour_input), *rests])
    )


def _day_start(tour_input: TourInput, start_hhmm: str | None) -> datetime | None:
    """When the day's clock starts, for the wire and for NOW: the planner's own
    `start_datetime` when the day has one; a legacy record saved before the clock
    existed (planned dateless, and it still plans dateless — the restorer is
    untouched) is clocked from its HH:MM start on TODAY, the day the walk is
    happening. None only when neither exists."""
    if tour_input.start_datetime is not None:
        return datetime.fromisoformat(tour_input.start_datetime)
    if start_hhmm:
        hh, mm = (int(x) for x in start_hhmm.split(":"))
        return datetime.combine(date.today(), time(hh, mm))
    return None


def _wire_clocks(
    route,
    tour_input: TourInput,
    snapshot,
    *,
    clock_start: datetime | None,
    listening_rate: float = 1.0,
) -> tuple[dict[str, str], dict[str, int]]:
    """Every stop's HH:MM on the wire, from THE one expression (`stop_clocks`, Phase
    5 S5.10) priced with the SAME audio map the final gate used — and that map,
    for the contingency set. Until this helper the CRUD adapter ran a second,
    walk-less clock (dwell only) that the phone showed as arrival times.
    **Extends** `stop_clocks`; nothing here sums."""
    audio = planned_audio_by_poi(
        route,
        snapshot,
        interest=frozenset(tour_input.lenses or []),
        end_is_none=tour_input.end is None,
        narration_density=tour_input.narration_density,
    )
    clocks = {
        poi.id: hhmm(arrival)
        for poi, arrival, _dep in stop_clocks(
            route,
            tour_input,
            clock_start=clock_start,
            listening_rate=listening_rate,
            audio_seconds_by_id=audio,
        )
        if arrival is not None
    }
    return clocks, audio


def _session_promises(
    route,
    tour_input: TourInput,
    protected: tuple[str, ...],
    *,
    clock_start: datetime | None,
    listening_rate: float = 1.0,
    audio_seconds_by_id: dict[str, int] | None = None,
) -> list[SessionPromise]:
    """The day's promises as the phone reads them, off THE one arrival walk
    (`stop_clocks`) with the coarse windows the W4.2 panel locked (F&D)."""
    marquee = route.start_anchor_poi_id
    if marquee is None and route.pois:
        marquee = max(route.pois, key=lambda p: (p.tier, p.id)).id
    # THE PLACE THE PERSON NAMED IS ON THE LIST (Phase 8 S8.5; W5.14, design
    # §3.1 — a promise is "the finish — a place and a time"). `_person_protected`
    # has held it since S5.16 through `own_place_ids`, but this list only ever
    # emitted four shapes, and a declared end that snapped onto a REAL stop is
    # none of them: it is not the A→B sentinel, not pinned, not a rest, and only
    # by luck the marquee. MEASURED 2026-08-24 on Camille's own day
    # (01-architecture-pilgrim.md: "end Notre-Dame"): her end sits 0.0 m from the
    # Notre-Dame POI, the session protects it through every replan — and the wire
    # listed ONE promise, the Arc de Triomphe du Carrousel, marked unprotected.
    # The day protected a promise the phone could not see it had.
    own = set(own_place_ids(route, tour_input))
    out: list[SessionPromise] = []
    for poi, arrival, departure in stop_clocks(
        route,
        tour_input,
        clock_start=clock_start,
        listening_rate=listening_rate,
        audio_seconds_by_id=audio_seconds_by_id,
    ):
        if poi.id.startswith(END_B_SENTINEL_PREFIX):
            kind = "finish"
        elif poi.id in tour_input.pinned_poi_ids:
            kind = "pinned"
        elif poi.poi_role == "body":
            kind = "rest"
        elif poi.id == marquee:
            # A stop that is BOTH the day's anchor and the place the person named
            # (Rosemary's Orsay round trip) keeps the anchor's name, as it has
            # since S5.8 — it is already protected either way, below.
            kind = "anchor"
        elif poi.id in own:
            kind = "finish"
        else:
            continue
        arrives, departs = _coarse_window(hhmm(arrival), hhmm(departure))
        out.append(
            SessionPromise(
                promise_id=poi.id,
                kind=kind,
                name=poi.name,
                arrives_hhmm=arrives,
                departs_hhmm=departs,
                protected=(kind == "finish" or poi.id in protected),
            )
        )
    return out


def _session_plan(
    trip_id: str,
    tour_input: TourInput,
    route,
    stops_out: list[GeneratedStop],
    *,
    snapshot,
    plan_version: int,
    clock_start: datetime | None,
    day_start_hhmm: str,
    listening_rate: float = 1.0,
    defer_set: bool = False,
) -> SessionPlan:
    """Version N of the living session: the stops, the promises, and the contingency
    set — computed ONCE, here, on the server (§4.6). Every entry of the set is THE
    planner (`select_route` under the certification policy — the same call
    `plan_premium_tour` makes, without the premium wrapper's per-entry version read
    and assembly) called with a `ReplanContext`; never a second decision procedure.

    ``defer_set`` (W5.12, the design's own remedy for the LIVE REPLAN bar — "narrow
    the live path"): the day, its clocks and its promises are assembled here and the
    contingency set is left EMPTY for the caller to fill — a live replan answers with
    the day at once and computes the full set right after the reply (see
    `replan_trip_session`), so the set's ~0.4 s per entry never sits between the
    phone's fire and the new day rendered.
    """
    protected = _person_protected(tour_input, route)
    # THE SESSION'S CLOCK (S5.10): every stop's HH:MM re-read off the one
    # expression for THIS version — a replan's stops would otherwise carry the
    # clocks of the day they were composed into.
    clocks, audio_by_id = _wire_clocks(
        route, tour_input, snapshot, clock_start=clock_start, listening_rate=listening_rate
    )
    stops_out = [
        st.model_copy(update={"start_time": clocks.get(st.poi_id, st.start_time)})
        for st in stops_out
    ]
    planned_end = (
        hhmm(clock_start + timedelta(minutes=tour_input.duration_min)) if clock_start else ""
    )
    finish = _finish_of(tour_input)
    finish_fields = {
        "finish_lat": finish[0] if finish else None,
        "finish_lng": finish[1] if finish else None,
        "finish_name": _finish_name(tour_input, route),
        "end_hardness": tour_input.end_hardness,
        # Phase 7 S7.3: the day's placement policy, from the party (design §5.6; R1).
        "placement": place_day(tour_input),
    }
    if defer_set:
        return SessionPlan(
            trip_id=trip_id,
            plan_version=plan_version,
            stops=stops_out,
            promises=_session_promises(
                route,
                tour_input,
                protected,
                clock_start=clock_start,
                listening_rate=listening_rate,
                audio_seconds_by_id=audio_by_id,
            ),
            retime_tolerance_seconds=RETIME_TOLERANCE_SECONDS,
            contingencies=[],
            walking_pace_kmh=PACE_KMH / float(tour_input.walking_pace or 1.0),
            day_start_hhmm=day_start_hhmm,
            planned_end_hhmm=planned_end,
            party=tour_input.party,
            **finish_fields,
        )
    policy = certification_planning_policy(policy_id=PREMIUM_MODULE_VERSION)
    with RoutingClient() as routing_client:

        def plan(tail: TourInput, ctx: ReplanContext):
            return select_route(
                tail, snapshot, routing_client=routing_client, planning_policy=policy, replan=ctx
            )

        cset: ContingencySet = build_contingency_set(
            route,
            tour_input,
            snapshot,
            routing_client=routing_client,
            person=ReplanContext(protected_poi_ids=protected, listening_rate=listening_rate),
            plan_version=plan_version,
            finish_name=_finish_name(tour_input, route),
            plan=plan,
            audio_seconds_by_id=audio_by_id,
            clock_start=clock_start,
        )
    logging.getLogger("ondoway.api").info(
        "session v%s for trip=%s: %d contingencies, %d alternate-authoring units "
        "(stops not on the planned day — the spend, printed before it is billed)",
        plan_version,
        trip_id,
        len(cset.entries),
        cset.authoring_units,
    )
    contingencies = [
        SessionContingency(
            contingency_id=e.contingency_id,
            trigger=e.trigger,
            plan_version=plan_version,
            stop_ids=[sid for sid in e.stop_ids if not sid.startswith(END_B_SENTINEL_PREFIX)],
            screen_text=e.screen_text,
            question=e.question,
            default_arm=e.default_arm,
            alternate_stop_ids=[
                sid for sid in e.alternate_stop_ids if not sid.startswith(END_B_SENTINEL_PREFIX)
            ],
            at_risk_stop_id=e.at_risk_stop_id,
            finish_hhmm=e.finish_hhmm,
        )
        for e in cset.entries
    ]
    return SessionPlan(
        trip_id=trip_id,
        plan_version=plan_version,
        stops=stops_out,
        promises=_session_promises(
            route,
            tour_input,
            protected,
            clock_start=clock_start,
            listening_rate=listening_rate,
            audio_seconds_by_id=audio_by_id,
        ),
        retime_tolerance_seconds=cset.retime_tolerance_seconds,
        contingencies=contingencies,
        # The preset the phone starts from until it has learned its own (§4.1):
        # the speed THIS day was planned at.
        walking_pace_kmh=PACE_KMH / float(tour_input.walking_pace or 1.0),
        day_start_hhmm=day_start_hhmm,
        planned_end_hhmm=planned_end,
        party=tour_input.party,
        **finish_fields,
    )


def _carry_forward_entries(previous: SessionPlan, new_day: SessionPlan) -> list[SessionContingency]:
    """The previous version's answers that are still answers for the NEW day (W5.12,
    "widen the precomputed set"): an entry whose trigger stop is still ahead and whose
    stops (and alternate) all lie on the new day is a replan of a day the person is
    still walking — it stays on the phone under the new version until the full set,
    computed right after the reply, replaces it on the next fetch. Ids are kept (the
    phone selects by id); the version is the new one."""
    ahead = {st.poi_id for st in new_day.stops}
    kept: list[SessionContingency] = []
    for e in previous.contingencies:
        if e.trigger.get("kind") == "live":
            continue  # a live question was asked once, at its moment; it does not carry
        trigger_stop = e.trigger.get("stop_id")
        if trigger_stop is not None and trigger_stop not in ahead:
            continue
        if not set(e.stop_ids) <= ahead or not set(e.alternate_stop_ids) <= ahead:
            continue
        kept.append(e.model_copy(update={"plan_version": new_day.plan_version}))
    return kept


def _finish_session_set(
    driver: Driver,
    trip_id: str,
    day: SessionPlan,
    tour_input: TourInput,
    route,
    *,
    snapshot,
    clock_start: datetime | None,
    listening_rate: float,
) -> None:
    """The second half of a live replan, off the live path: compute version N's FULL
    contingency set through THE planner and persist it as the same version — unless
    a newer version has landed meanwhile, in which case this set is stale and is
    dropped (the newer replan computes its own)."""
    full = _session_plan(
        trip_id,
        tour_input,
        route,
        day.stops,
        snapshot=snapshot,
        plan_version=day.plan_version,
        clock_start=clock_start,
        day_start_hhmm=day.day_start_hhmm,
        listening_rate=listening_rate,
    )
    with driver.session() as session:
        inputs = get_trip_compose_inputs(session, trip_id)
        if inputs is None or int(inputs["plan_version"] or 0) != day.plan_version:
            return
        write_trip_session(
            session, trip_id, plan_version=day.plan_version, session_json=full.model_dump_json()
        )


def _trip_roles(session: Session, user_id: str, trip_id: str) -> set[str]:
    """The caller's roles on the trip, through any of their profiles:
    {"IS_CAPTAIN_OF"}, {"IS_CREW_OF"}, both, or empty (no relationship —
    indistinguishable from a trip that does not exist)."""
    record = session.run(
        "MATCH (u:User {id: $uid})-[:HAS_PROFILE]->(:Profile)"
        "-[r:IS_CAPTAIN_OF|IS_CREW_OF]->(t:Trip {id: $tid}) "
        "RETURN collect(DISTINCT type(r)) AS roles",
        uid=user_id,
        tid=trip_id,
    ).single()
    return set(record["roles"]) if record else set()


def _readable_trip_or_404(session: Session, user_id: str, trip_id: str) -> None:
    """Captain OR crew may READ the shared day (the session GETs). A stranger's
    guess is 404, never 403 — the no-confirmation rule holds for the family
    surface exactly as it does for owners."""
    if not _trip_roles(session, user_id, trip_id):
        raise HTTPException(404, f"Trip '{trip_id}' not found")


def _captain_trip_or_404(session: Session, user_id: str, trip_id: str) -> None:
    """Only the captain WRITES (compose, replan): one writer per living session.
    Crew gets the typed 403 the phone can explain; a stranger still gets 404."""
    roles = _trip_roles(session, user_id, trip_id)
    if "IS_CAPTAIN_OF" in roles:
        return
    if "IS_CREW_OF" in roles:
        raise HTTPException(
            403,
            {
                "reason": "captain_only",
                "detail": "Only the trip's captain can change the day",
            },
        )
    raise HTTPException(404, f"Trip '{trip_id}' not found")


@router.get("/trips/{trip_id}/session", response_model=SessionPlan)
def get_trip_session(
    trip_id: str,
    current_user: dict = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """The current version of the living session — the day as it stands and the
    contingency set the phone selects from (design §4.6). A trip that was never
    composed has no session yet: 404 with a reason, never an empty plan.

    Readable by captain OR crew: the family reads its shared day (ADR 0005)."""
    _readable_trip_or_404(session, current_user["id"], trip_id)
    inputs = get_trip_compose_inputs(session, trip_id)
    if inputs is None:
        raise HTTPException(404, f"Trip '{trip_id}' not found")
    if not inputs["session"]:
        raise HTTPException(404, {"reason": "no_session_yet", "detail": "compose the trip first"})
    plan = SessionPlan(**inputs["session"])
    # Phase 6 S6.8: AUDIO FACTS LIVE ON THE ITEMS, THE SESSION HOLDS THE PLAN.
    # The saved payload snapshots its stops at compose time; the voicing pass
    # writes the narration and session-line files onto the ItineraryItems
    # afterwards — so the session's audio fields are re-read from the live items
    # at every GET, or the phone would forever see the nulls of compose time
    # (measured 2026-08-19: five voiced closes in the graph, none on the wire).
    return _with_live_audio(session, plan)


def _with_live_audio(session: Session, plan: SessionPlan) -> SessionPlan:
    """Overlay the items' current audio fields onto a saved session's stops."""
    ids = [stop.stop_id for stop in plan.stops if stop.stop_id]
    if not ids:
        return plan
    rows = session.run(
        """
        MATCH (item:ItineraryItem) WHERE item.id IN $ids
        RETURN item.id AS id,
               item.audio_url AS audio_url,
               item.audio_duration_sec AS audio_duration_sec,
               item.close_audio_url AS close_audio_url,
               item.thread_audio_urls AS thread_audio_urls,
               item.full_close_audio_url AS full_close_audio_url,
               item.leg_audio_url AS leg_audio_url,
               item.leg_audio_duration_sec AS leg_audio_duration_sec,
               item.segments_json AS segments_json
        """,
        ids=ids,
    )
    by_id = {r["id"]: dict(r) for r in rows}
    stops = []
    for stop in plan.stops:
        live = by_id.get(stop.stop_id or "")
        if not live:
            stops.append(stop)
            continue
        stops.append(
            stop.model_copy(
                update={
                    "audio_url": live["audio_url"] or stop.audio_url,
                    "audio_duration_sec": live["audio_duration_sec"] or stop.audio_duration_sec,
                    "close_audio_url": live["close_audio_url"] or stop.close_audio_url,
                    "thread_audio_urls": (
                        json.loads(live["thread_audio_urls"])
                        if live["thread_audio_urls"]
                        else stop.thread_audio_urls
                    ),
                    "full_close_audio_url": live["full_close_audio_url"]
                    or stop.full_close_audio_url,
                    # Phase 7 S7.7: the leg piece's file rides the same overlay.
                    "leg_audio_url": live["leg_audio_url"] or stop.leg_audio_url,
                    "leg_audio_duration_sec": (
                        live["leg_audio_duration_sec"] or stop.leg_audio_duration_sec
                    ),
                    # Phase 7 S7.7 (B): the chapters, with the files the voicing pass
                    # wrote into the item's list, ride the same overlay.
                    "segments": (
                        [StopSegment.model_validate(d) for d in json.loads(live["segments_json"])]
                        if live["segments_json"]
                        else stop.segments
                    ),
                }
            )
        )
    return plan.model_copy(update={"stops": stops})


def _releg_kept_stops(
    stops: list[GeneratedStop], route, *, walked_in_from: GeneratedStop | None
) -> list[GeneratedStop]:
    """Re-leg the kept stops of a replanned day: no line survives a walk it was not
    written for.

    A leg line names both its ends — "From X, make your way on to Y, about a
    nine-minute walk away" — so it is true of one pair and one routed length. A replan
    keeps the day's places and re-orders them, which silently re-points those lines: a
    line written for the walk from Saint-Eustache plays on the walk from the Bourse de
    Commerce, and the phone plays it against whichever stop now comes before.

    Where the pair still holds, the line and its audio are left exactly alone — a
    correct line is never re-voiced and never re-spent. Where it does not, the stale
    words are dropped at once (silence, never the wrong direction) and the line is
    re-derived for the new pair from the deterministic template, which names both ends,
    speaks the routed minutes and cannot say a bearing. Its audio fields are cleared
    with it, so the voicing pass — which skips a line whose text still matches its
    stored hash — re-voices this one on its next run, and until then the leg is quiet.

    ``walked_in_from`` is the stop the walker has already left, so the first kept stop
    is judged against the walk actually under way rather than treated as a fresh start.
    A stop whose ``leg_from_poi_id`` is None carries no provenance — an item written
    before the field existed — and is left untouched: unknown is not the same as
    known-stale, and regenerating every leg of every older day would re-voice the lot.
    """
    seconds_by_stop = {
        transit.to_poi_id: leg_walk_seconds(transit) for transit in route.transits
    }
    out: list[GeneratedStop] = []
    previous_name = walked_in_from.poi_name if walked_in_from else None
    previous_id = walked_in_from.poi_id if walked_in_from else None
    for idx, stop in enumerate(stops):
        written_from = stop.leg_from_poi_id
        if written_from is not None and written_from != previous_id:
            out.append(
                stop.model_copy(
                    update={
                        "leg_narration": (
                            _template_nav(
                                previous_name,
                                stop.poi_name,
                                int(seconds_by_stop.get(stop.poi_id, 0)),
                                idx,
                            )
                            if previous_name
                            else None
                        ),
                        "leg_from_poi_id": previous_id,
                        "leg_audio_url": None,
                        "leg_audio_duration_sec": None,
                    }
                )
            )
        else:
            out.append(stop)
        previous_name, previous_id = stop.poi_name, stop.poi_id
    return out


def _persist_releg_to_items(session, stops: list[GeneratedStop]) -> None:
    """The re-legged lines land on the ITEMS, in the same request. Audio truth lives
    on the items — the session GET overlays their current files onto the stops — so a
    line rewritten only in the session json is un-dropped on the phone's very next
    fetch: the item still holds the OLD text's file and the overlay serves it under
    the new words. The hash is cleared with the url so the voicing pass treats the
    corrected line as unmade rather than skipping it. Skipping this write leaves the
    item on the OLD words — and the background voicing then faithfully re-voices the
    old line, so the day keeps speaking the walk nobody is taking in a fresh file."""
    session.run(
        """
        UNWIND $rows AS row
        MATCH (item:ItineraryItem {id: row.item_id})
        SET item.leg_narration = row.leg_narration,
            item.leg_from_poi_id = row.leg_from_poi_id,
            item.leg_audio_url = null,
            item.leg_audio_duration_sec = null,
            item.leg_audio_hash = null
        """,
        rows=[
            {
                "item_id": stop.stop_id,
                "leg_narration": stop.leg_narration,
                "leg_from_poi_id": stop.leg_from_poi_id,
            }
            for stop in stops
        ],
    )


def _revoice_replanned_legs(driver: Driver, item_ids: list[str]) -> None:
    """Voice the re-legged lines off the live path — the `_finish_session_set`
    mould: a fresh session from the driver, through the one session-line voicing
    pass, so the corrected words have their file before the walker reaches the leg
    when the machine is quick, and the leg stays silent rather than wrong when it
    is not."""
    from src.api.routes.audio import _voice_session_lines  # circular at module load

    with driver.session() as session:
        rows = session.run(
            "MATCH (item:ItineraryItem) WHERE item.id IN $ids "
            "RETURN item.id AS stop_id, item.leg_narration AS leg_narration, "
            "       item.leg_audio_url AS leg_audio_url, "
            "       item.leg_audio_hash AS leg_audio_hash",
            ids=item_ids,
        ).data()
        for row in rows:
            if row["leg_narration"]:
                _voice_session_lines(
                    session, row, provider_name=None, voice_id=None, force=False
                )




def _prefer_keeping_over_a_longer_leg(
    route,
    tail: TourInput,
    snapshot,
    *,
    routing_client,
    policy,
    ctx: ReplanContext,
    base_stops,
    remaining_ids: tuple[str, ...],
):
    """§4.5.3 on the live path: a replan that DROPS a stop may not mint a walking
    leg longer than the day it replans already carried.

    Detection first, spend second: only when the replanned route both dropped a
    stop still ahead AND carries a leg past the base day's own longest (plus one
    displayed minute of rounding) does this retry the ONE planner call with the
    base ceiling as `longest_leg_ceiling_seconds` — and it serves whichever
    answer has the SHORTER longest leg (ties to the fuller day), so the retry
    can only improve the walk. The always-on form of this parameter was tried
    and reverted (the ceiling refused the middle stops and the remainder fused
    longer than anything it rejected); breach-triggered with a
    take-the-better-day comparison is the bounded version of the carried rule:
    prefer KEEPING a stop over minting a longer leg.
    """
    kept_ids = {p.id for p in route.pois}
    dropped = set(remaining_ids) - kept_ids
    if not dropped:
        return route  # nothing dropped; the rule has nothing to re-check
    if len(base_stops) < 2:
        return route

    def longest_leg(points) -> int:
        legs = [
            routing_client.leg_seconds(a[0], a[1], b[0], b[1])
            for a, b in itertools.pairwise(points)
        ]
        return max(legs) if legs else 0

    base_max = longest_leg([(st.lat, st.lng) for st in base_stops])
    replanned_max = longest_leg(
        [tail.start, *((p.lat, p.lng) for p in route.pois)]
    )
    if base_max <= 0 or replanned_max <= base_max + 60:
        return route
    # The lever is PROTECTION, not the ceiling: the ceiling bounds what may be
    # inserted and cannot bound the walk that is left when nothing is (the
    # reverted always-on form proved it). A protected stop force-seats, so the
    # retry holds the dropped stops as promises; a finish that then moves is
    # the disclosed trade (R2.4/R2.5), a fused walk past the day's own longest
    # is the forbidden one (§4.5.3).
    keeping_ctx = ctx.model_copy(
        update={
            "protected_poi_ids": tuple(
                dict.fromkeys([*ctx.protected_poi_ids, *sorted(dropped)])
            ),
            "longest_leg_ceiling_seconds": base_max + 60,
        }
    )
    try:
        retried = select_route(
            tail,
            snapshot,
            routing_client=routing_client,
            planning_policy=policy,
            replan=keeping_ctx,
        )
    except (CertificationPlanningInfeasibleError, TourabilityRefusedError):
        return route  # holding the stops refused outright; the dropped day stands
    retried_max = longest_leg([tail.start, *((p.lat, p.lng) for p in retried.pois)])
    if retried_max < replanned_max or (
        retried_max == replanned_max and len(retried.pois) > len(route.pois)
    ):
        return retried
    return route


@router.post("/trips/{trip_id}/session/replan", response_model=SessionPlan)
@_reports_degradations
def replan_trip_session(
    request: Request,
    trip_id: str,
    body: SessionReplanRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
    session: Session = Depends(get_session),
    driver: Driver = Depends(get_driver),
):
    """The one replan brain (design §4.6): the phone reports where it is, its two
    clocks and its learned rates; the server replans the REMAINDER of the day it is
    standing in — through THE planner with a `ReplanContext` (the person's protected
    set held, the pool the planned stops still ahead, a zero floor, the learned
    pace and listening rate) — and mints version N+1 over the SAME items with a
    fresh contingency set. It never hands back a different day than the one the
    person is standing in (the plan's sabotage line): no new place, no
    re-authoring, the audio already made is kept.

    Captain-only, like compose: a replan WRITES version N+1 over the shared
    items, and the living session has one writer. Crew gets the typed 403.
    """
    _captain_trip_or_404(session, current_user["id"], trip_id)
    inputs = get_trip_compose_inputs(session, trip_id)
    if inputs is None:
        raise HTTPException(404, f"Trip '{trip_id}' not found")
    if not inputs["session"] or not inputs["tour_input"]:
        raise HTTPException(404, {"reason": "no_session_yet", "detail": "compose the trip first"})
    current = SessionPlan(**inputs["session"])
    tour_input = _restore_tour_input(inputs["tour_input"])

    k = min(body.next_stop_index, len(current.stops))
    remaining, visited = current.stops[k:], current.stops[:k]
    finish = (
        tour_input.end
        if tour_input.end is not None
        else (tour_input.start if tour_input.round_trip else None)
    )
    start_clock = _day_start(tour_input, inputs["tour_input"].get("start_time"))
    now = (
        start_clock + timedelta(seconds=body.wall_elapsed_seconds)
        if start_clock is not None
        else None
    )
    minutes_left = max(1, tour_input.duration_min - body.wall_elapsed_seconds // 60)
    tail = tour_input.model_copy(
        update={
            "start": (body.lat, body.lng),
            "end": finish,
            "round_trip": False,
            "duration_min": minutes_left,
            "start_datetime": now.isoformat(timespec="minutes") if now else None,
            "pinned_poi_ids": (),
            "rest_cadence_minutes": None,
            "walking_pace": body.observed_pace or tour_input.walking_pace,
        }
    )
    snapshot = load_paris_corpus(driver, city_slug=tour_input.city_slug)
    remaining_ids = tuple(st.poi_id for st in remaining)
    protected = tuple(
        pid
        for pid in _person_protected(tour_input, _stops_as_route_pois(remaining, snapshot))
        if pid in remaining_ids
    )
    ctx = ReplanContext(
        protected_poi_ids=protected,
        keep_to_poi_ids=remaining_ids,
        visited_poi_ids=tuple(st.poi_id for st in visited),
        # §4.5.3's CEILING IS NOT PASSED ON THE FIRST CALL. The always-on form
        # was tried and reverted (the ceiling refused the middle stops and the
        # remainder fused LONGER than anything it rejected — the Arc straight to
        # Notre-Dame at thirty-nine minutes). The rule lives in
        # `_prefer_keeping_over_a_longer_leg` instead: breach-triggered, one
        # bounded retry WITH the ceiling, and the better day (the shorter
        # longest leg) is the one served.
        floor_zero=True,
        listening_rate=body.listening_rate or 1.0,
    )
    policy = certification_planning_policy(policy_id=PREMIUM_MODULE_VERSION)
    with RoutingClient() as routing_client:
        try:
            route = select_route(
                tail, snapshot, routing_client=routing_client, planning_policy=policy, replan=ctx
            )
        except (CertificationPlanningInfeasibleError, TourabilityRefusedError) as exc:
            raise HTTPException(422, _refusal_detail(exc)) from exc
        route = _prefer_keeping_over_a_longer_leg(
            route,
            tail,
            snapshot,
            routing_client=routing_client,
            policy=policy,
            ctx=ctx,
            base_stops=current.stops,
            remaining_ids=remaining_ids,
        )
        record_routing_degradations(route, component="trips.replan_trip_session")

    by_poi = {st.poi_id: st for st in current.stops}
    kept = [
        by_poi[p.id].model_copy(update={"sort_order": i + 1})
        for i, p in enumerate(route.pois)
        if p.id in by_poi
    ]
    new_stops = _releg_kept_stops(kept, route, walked_in_from=visited[-1] if visited else None)
    relegged = [
        stop
        for stop, was in zip(new_stops, kept, strict=True)
        if stop.stop_id
        and (stop.leg_narration, stop.leg_from_poi_id)
        != (was.leg_narration, was.leg_from_poi_id)
    ]
    plan_version = int(inputs["plan_version"] or 0) + 1
    # THE LIVE PATH IS THE DAY (W5.12, measured: the set is ~0.4 s per entry and the
    # planner ~1 s — the design's remedy is to narrow the live path). The reply
    # carries the replanned day, its clocks and promises, and the previous
    # version's answers that still hold; the full set is computed right after the
    # reply and persisted as this same version for the phone's next fetch.
    session_plan = _session_plan(
        trip_id,
        tail,
        route,
        new_stops,
        snapshot=snapshot,
        plan_version=plan_version,
        clock_start=now,
        day_start_hhmm=hhmm(start_clock),
        listening_rate=body.listening_rate or 1.0,
        defer_set=True,
    )
    live_question = _live_question(
        route,
        tail,
        snapshot,
        protected=protected,
        remaining_ids=remaining_ids,
        clock=now,
        plan_version=plan_version,
        policy=policy,
        at_stop=visited[-1].poi_id if visited else (remaining[0].poi_id if remaining else None),
    )
    session_plan = session_plan.model_copy(
        update={
            "contingencies": [
                *([live_question] if live_question is not None else []),
                *_carry_forward_entries(current, session_plan),
            ]
        }
    )
    _report_phone_clock(body, session_plan, next_planned=remaining[0] if remaining else None)
    write_trip_session(
        session, trip_id, plan_version=plan_version, session_json=session_plan.model_dump_json()
    )
    if relegged:
        _persist_releg_to_items(session, relegged)
        background_tasks.add_task(
            _revoice_replanned_legs, driver, [stop.stop_id for stop in relegged]
        )
    background_tasks.add_task(
        _finish_session_set,
        driver,
        trip_id,
        session_plan,
        tail,
        route,
        snapshot=snapshot,
        clock_start=now,
        listening_rate=body.listening_rate or 1.0,
    )
    return session_plan


def _live_question(
    route,
    tail: TourInput,
    snapshot,
    *,
    protected: tuple[str, ...],
    remaining_ids: tuple[str, ...],
    clock: datetime | None,
    plan_version: int,
    policy,
    at_stop: str | None,
) -> SessionContingency | None:
    """THE PROMISE TIER ON THE LIVE PATH (W5.14, all eleven: the live replan dropped
    Rosemary's Orsay in silence). When the replanned remainder still overruns with the
    protected things kept, the reply carries the ONE question — the same R2 text the
    precomputed set uses (`question_text`): keep the protected thing and arrive later,
    or shorten the rest / go straight on and arrive by the clock. It rides as an entry
    of kind "live" that the phone applies at once (default in force, two big buttons).
    None when nothing overruns or the two arms are the same day."""
    if route is None or route.overrun_seconds <= 0 or clock is None:
        return None
    at_risk_poi = at_risk_choice(route, list(protected))
    if at_risk_poi is None:
        return None
    with RoutingClient() as routing_client:
        alt = select_route(
            tail,
            snapshot,
            routing_client=routing_client,
            planning_policy=policy,
            replan=ReplanContext(
                protected_poi_ids=protected,
                keep_to_poi_ids=tuple(pid for pid in remaining_ids if pid in protected),
                floor_zero=True,
            ),
        )
    keep_clock = finish_clock(route, tail, clock_start=clock)
    alt_clock = finish_clock(alt, tail, clock_start=clock)
    q, default_arm = question_text(
        tail=route,
        alt=alt,
        at_risk_poi=at_risk_poi,
        rest_seconds=int(route.planned_visit_seconds.get(at_risk_poi.id, 0)),
        keep_clock=keep_clock,
        alt_clock=alt_clock,
        finish_name=_finish_name(tail, route),
        end_hardness=tail.end_hardness,
    )
    if q is None:
        return None
    return SessionContingency(
        contingency_id=f"v{plan_version}-live",
        trigger={"kind": "live", "stop_id": at_stop},
        plan_version=plan_version,
        stop_ids=[p.id for p in route.pois if not p.id.startswith(END_B_SENTINEL_PREFIX)],
        screen_text=q,
        question=q,
        default_arm=default_arm,
        alternate_stop_ids=[p.id for p in alt.pois if not p.id.startswith(END_B_SENTINEL_PREFIX)],
        at_risk_stop_id=at_risk_poi.id,
        finish_hhmm=hhmm(keep_clock),
    )


def _report_phone_clock(
    body: SessionReplanRequest, session_plan: SessionPlan, *, next_planned: GeneratedStop | None
) -> None:
    """THE SESSION CLOCK SEAM, server half (Phase 5 S5.10; design §4.6). The phone
    sends its OWN re-timed clock for the stop it is heading to; the server compares
    it with what its one expression says for the same stop in the version it just
    minted, and a gap beyond the session's tolerance is REPORTED — a row on the
    reply's `degradations` channel, both registers — and never corrected in either
    direction: the reply keeps the server's clock, the phone keeps its own, and the
    gap is visible. Nothing here assigns."""
    phone = body.phone_next_stop_hhmm
    if not phone or next_planned is None or not session_plan.stops:
        return
    first = session_plan.stops[0]
    if first.poi_id != next_planned.poi_id:
        return  # the replan re-ordered the day; the two clocks name different stops
    gap = clock_divergence_seconds(phone, first.start_time)
    if abs(gap) <= session_plan.retime_tolerance_seconds:
        return
    minutes = round(abs(gap) / 60)
    ahead = "later" if gap > 0 else "earlier"
    record(
        kind=SESSION_CLOCK_DIVERGENCE,
        human=(
            f"Your phone reckons you reach {first.poi_name} about {minutes} minutes "
            f"{ahead} than our planner does. Neither clock was changed to hide the gap; "
            "the phone keeps its own and the planner keeps its own."
        ),
        component="trips.replan_trip_session",
        stop=first.poi_name,
        phone_hhmm=phone,
        server_hhmm=first.start_time,
        divergence_seconds=str(gap),
        tolerance_seconds=str(session_plan.retime_tolerance_seconds),
    )


def _stops_as_route_pois(stops: list[GeneratedStop], snapshot):
    """The corpus POIs behind a stop list, in stop order — a tiny Route-like view
    for the protected-set rule, which reads `poi_role`."""
    by_id = {p.id: p for p in snapshot.pois}
    return SimpleNamespace(pois=[by_id[st.poi_id] for st in stops if st.poi_id in by_id])


def _preview_cards(option: RouteOption) -> list[TripPreviewStop]:
    """The shared option's cards on the preview wire, one for one.

    THE INTERLEAVE ITSELF IS NOT HERE. It is ``src/tour/options.build_route_option``,
    the one implementation both surfaces use; this only renames the fields the preview
    model spells differently and numbers the cards. ``sort_order`` counts EMITTED cards
    (leg, vignette and dwell alike), which is what the workbench renders in order.
    """
    return [
        TripPreviewStop(
            sort_order=index,
            poi_name=stop.name,
            lat=stop.lat,
            lng=stop.lng,
            narration=stop.narration,
            minutes=stop.minutes,
            band=stop.band,
            spotlight=stop.spotlight,
            has_deeper_dive=stop.has_deeper_dive,
        )
        for index, stop in enumerate(option.stops, start=1)
    ]


def _tourability_payload(
    assessment: TourabilityAssessment | None,
) -> TripPreviewTourability | None:
    """Map the engine's YELLOW assessment onto the preview wire model.

    The engine attaches the assessment only for YELLOW (GREEN carries None,
    RED raised long before a 200). Dropping it here is what made thin-area
    single-stop tours look like silent bugs (hostile-panel finding,
    2026-07-02) — the Phase 6 contract is "generate but WARN"."""
    if assessment is None:
        return None
    return TripPreviewTourability(
        status=assessment.status,  # GREEN (delivered_thin) or YELLOW; RED raised earlier
        delivered_thin=assessment.delivered_thin,
        fill_ratio=round(assessment.fill_ratio, 2),
        on_lens_fill_ratio=(
            round(assessment.on_lens_fill_ratio, 2)
            if assessment.on_lens_fill_ratio is not None
            else None
        ),
        anchor_candidates=assessment.anchor_candidate_count,
        reachable_poi_count=assessment.reachable_poi_count,
        max_supportable_duration_min=assessment.max_supportable_duration_min,
        one_way_alternative_destination=assessment.one_way_alternative_destination,
    )


def _preview_plan_fingerprint(plans) -> str:
    """Stable 12-hex identity of ONE plan result: the options, in order.

    The author route re-derives the plan from the same request body, and this is what
    proves the option it authors is the option the operator was shown. Corpus or
    routing drift between the two calls changes the fingerprint, and the author route
    then refuses rather than silently authoring a different tour. Ordered POI ids are
    the whole identity of an option — everything else (eta, dwell, vignettes) is a
    pure function of them plus the routing answers.
    """
    payload = json.dumps(
        [[poi.id for poi in plan.route.pois] for plan in plans],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _places_only(option: RouteOption) -> RouteOption:
    """The option as it looks while it is still being CHOSEN: places, order, times.

    OWNER RULING 1, 2026-08-04: during route planning, on both surfaces, an option
    shows the POI names, their order, the walking times and the ETA — and no
    descriptive text of any kind. So the cards carry no narration, no "keep exploring"
    flag, and there are no walking-narration cards at all: such a card exists only to
    show what is heard on the way, and at plan time nothing has been written. The words
    arrive at POST /trips/preview/author, once a route has been picked.
    """
    return option.model_copy(
        update={
            "stops": tuple(
                stop.model_copy(update={"narration": "", "has_deeper_dive": False})
                for stop in option.stops
                if stop.band != "leg"
            )
        }
    )


def _plan_options(plans, snapshot, *, fingerprint: str) -> list[RouteOption]:
    """The plans as RouteOptions, through the ONE shared interleave."""
    beats_by_id = {ref.id: ref for refs in snapshot.beats_by_poi.values() for ref in refs}
    return [
        _places_only(
            build_route_option(
                plan.route,
                plan.source,
                beats_by_id,
                route_id=f"preview-{fingerprint}-opt{i + 1}",
                snapshot=snapshot,
                sequence=plan.sequence,
            )
        )
        for i, plan in enumerate(plans)
    ]


def _plan_preview(tour_input: TourInput, driver: Driver):
    """BLOCK 1 for the anonymous surface: plan the ONE day, refuse, or hand it back.

    Shared by the plan-only preview and by the author route, which re-derives the same
    plan before authoring the day it was handed. Provider-free and $0. Returns the
    plan as a ONE-element list (deviation i): the wire keeps ``options`` a list, the
    fingerprint hashes a list, and the author route's opt-N parse stays valid for
    stored ids — there is simply exactly one now.
    """
    snapshot = load_paris_corpus(driver, city_slug=tour_input.city_slug)
    try:
        with RoutingClient() as routing_client:
            plan = plan_premium_tour(tour_input, snapshot, routing_client=routing_client)
    except (TourabilityRefusedError, CertificationPlanningInfeasibleError) as exc:
        raise HTTPException(422, _refusal_detail(exc)) from exc
    except (PremiumRouteInfeasibleError, ValueError) as exc:
        raise HTTPException(422, _infeasible_detail()) from exc
    return snapshot, [plan]


@router.post("/trips/preview", response_model=TripPreviewResponse)
def preview_trip(
    body: TripPreviewRequest,
    driver: Driver = Depends(get_driver),
):
    """Plan the tour. Route options, no narrator, no spend.

    BLOCK 1 of the two-block split: start (required), end (optional), lenses and timing
    in; routed options out. It selects the places, orders and routes them, computes the
    ETA, dwell, vignettes and tourability, and calls no provider — so a person chooses
    among real walks before anything is paid for. Writing the chosen one is a separate
    call, POST /trips/preview/author.

    This endpoint used to plan AND write, one paid call per stop, on every request, with
    no authentication in front of it — so a stranger could spend the project's money by
    typing coordinates, and nobody could look at a route before it was written.

    OWNER RULING 2026-07-31: "Don't just log errors. Actually show them in the workbench
    UI. Otherwise, they're invisible." The degradation scope is opened here so a route
    built on estimated walking legs rather than measured ones is reported on the wire
    rather than in a log file nobody opens.
    """
    with degradation_scope() as collected:
        tour_input = _build_tour_input(
            start=(body.center_lat, body.center_lng),
            duration_min=body.duration_min or DEFAULT_DURATION_MIN,
            city_slug=body.city_slug,
            lenses=body.lenses or None,
            round_trip=body.round_trip,
            max_stop_minutes=body.max_stop_minutes,
            end=_end_point(body.end_lat, body.end_lng),
            start_datetime=body.start_datetime,
            end_hardness=body.end_hardness,
            **_dial_kwargs(body),
        )
        snapshot, plans = _plan_preview(tour_input, driver)
        route = plans[0].route
        options = _plan_options(plans, snapshot, fingerprint=_preview_plan_fingerprint(plans))
        result = TripPreviewResponse(
            spine_area=route.spine_area,
            options=options,
            tourability=_tourability_payload(route.tourability),
            promises=_preview_promises(route),
            day_notes=_preview_day_notes(route, body),
            # THE UNPLANNED MINUTES, NAMED — against what was ASKED FOR, always a
            # number (W4.12 panel, Marcus/Sofia/Aiko/Julien; the W4.2 ruling was
            # "the unaccounted minutes are NAMED"). This used to ride
            # `elapsed_shortfall_seconds`, which the planner sets ONLY when the day
            # dips under its internal band FLOOR — so a 170-of-180 day printed no
            # slack at all, and a 270-of-300 day hid thirty minutes. The field
            # blanked exactly when the margin got tight, which is when a person
            # with a train needs it most. `eta_seconds` is THE one elapsed formula
            # (options.option_eta_seconds), so this is the same clock the day is
            # priced on, not a second opinion. Zero is a real answer ("the day is
            # full") and is sent as 0, never as null.
            slack_minutes=max(0, (tour_input.duration_min * 60 - options[0].eta_seconds) // 60),
            # THE LONGEST SINGLE WALK (W4.12 panel — Rosemary, Sofia, Marcus, Aiko,
            # Julien, F&D): the number the "Shorter walks" dial is NAMED after, and
            # the one number a person with a stick, a bag or a December dusk decides
            # on. The surface printed only a walking TOTAL, so nobody could check
            # the dial ("a walking budget is not one number" — Rosemary's headline
            # breakage). THE one expression (routing.longest_walk_minutes) — the
            # same number, through the same rounding, that the planner certifies
            # the leg cap against (Phase 5 S5.4), so the head line and the cap can
            # never disagree by a rounding.
            longest_walk_minutes=longest_walk_minutes(route.transits),
        )
        rows = summarize(collected)
    return result.model_copy(update={"degradations": rows}) if rows else result


#: A promise window is COARSE on the surface (F&D's ruling, W4.2 deviation v,
#: re-judged at W4.12 on the built thing): the planner records the exact minute
#: — the fact Phase 5's replan will need — and the traveller reads a window
#: rounded to marks a person would say out loud. Arrival rounds DOWN, departure
#: rounds UP, so the spoken window always CONTAINS the planned one and never
#: promises a minute earlier or later than the plan.
PROMISE_WINDOW_GRAIN_MINUTES: int = 5


def _coarse_window(arrives_hhmm: str, departs_hhmm: str) -> tuple[str, str]:
    """ "11:32"-"12:02" -> "11:30"-"12:05". Empty strings (a dateless day) pass through.

    A zero-length window (the A→B finish point: arrival IS departure) rounds
    both ends the same way, so it stays one time and never widens into a stay
    that does not exist.
    """
    if not arrives_hhmm or not departs_hhmm:
        return arrives_hhmm, departs_hhmm
    grain = PROMISE_WINDOW_GRAIN_MINUTES

    def _mins(hhmm: str) -> int:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)

    def _fmt(total: int) -> str:
        total %= 24 * 60
        return f"{total // 60:02d}:{total % 60:02d}"

    a, d = _mins(arrives_hhmm), _mins(departs_hhmm)
    a_r = (a // grain) * grain
    d_r = a_r if d == a else -(-d // grain) * grain
    return _fmt(a_r), _fmt(d_r)


def _preview_promises(route) -> list[TripPreviewPromise]:
    """The day's promises as the pre-commit surface shows them (W4.2 dev. v).

    Names resolved off the route's own stops; shapes carried verbatim from the
    planner's promise assembly. The WINDOW is the planner's exact minute made
    coarse for a person (F&D at W4.12: "11:32 is not a thing anybody says" —
    the model's own docstring quoted their ruling and shipped six
    minute-precision timestamps under it).
    """
    names = {p.id: p.name for p in route.pois}
    out: list[TripPreviewPromise] = []
    for promise in route.promises:
        arrives, departs = _coarse_window(promise.arrives_hhmm, promise.departs_hhmm)
        out.append(
            TripPreviewPromise(
                kind=promise.kind,
                name=names.get(promise.poi_id, promise.poi_id),
                arrives_hhmm=arrives,
                departs_hhmm=departs,
                goes_inside=promise.shape.goes_inside,
                queue_minutes=round(promise.shape.queue_seconds / 60),
            )
        )
    return out


def _preview_day_notes(route, body) -> list[str]:
    """The day's plain-language notes (W4.2 deviation v; Paulo's wordings).

    EVERY SENTENCE HERE IS A STATEMENT ABOUT THE BUILT DAY, never about the
    request. The W4.12 closing panel caught two notes that were printed from the
    request alone — "Left out today, as asked: museum." on a day byte-identical
    to the un-dialled one, and "Places with long waits were left out, as asked."
    above a stop whose wait had gone UP from 3 to 10 minutes — and ruled a
    receipt for work not done the single most damaging string on the surface
    (Julien: "if it will tell me it removed a museum it did not remove, I stop
    believing whatever it tells me at Hôtel Lambert"). So a dial note now says
    what is TRUE of this day: the kinds that are absent, or the ones that are
    not; the longest wait that remains, by name. The exclusion RULE the dial
    applied is stated as a rule ("nothing with a long queue was considered"),
    which is true whether or not it changed anything.

    Closed doors: the planner records the closure FACT and its pool DECISION
    (ClockExclusion.kept_outside); which sentence the traveller reads depends
    on whether the place is actually ON this route, which only this reader
    knows — "we will see it from the outside" was printed for a Montmartre
    cabaret on a Tuileries→Notre-Dame day (W4.12, Julien: "I live here. These
    are checkable, and they are false.").

    Hours that are not the map's NAME the stops (Paulo: "gated" is jargon, an
    unnamed count "is worse than silence").
    """
    on_route = {p.id for p in route.pois}
    notes: list[str] = []
    for ex in route.clock_exclusions:
        if ex.poi_id in on_route:
            if ex.kept_outside:
                notes.append(f"{ex.name} — {ex.reason} — we will see it from the outside")
            else:
                # A disclosure about a stop that STAYED (the after-dusk finish):
                # the planner's sentence is already the whole story.
                notes.append(f"{ex.name} — {ex.reason}")
        else:
            notes.append(f"{ex.name} — {ex.reason}, so it is not in your day")

    if body.category_minus:
        asked = sorted(set(body.category_minus))
        still_in = sorted(
            {p.name for p in route.pois if p.place_category and p.place_category in asked}
        )
        kinds = ", ".join(asked)
        if still_in:
            # A pinned or protected place can outrank the dial; say so by name
            # rather than claim an exclusion the list beneath disproves.
            notes.append(
                f"You asked for no {kinds} stops; still in this day: " + ", ".join(still_in) + "."
            )
        else:
            notes.append(f"No {kinds} stops in this day, as asked.")

    if body.avoid_queues:
        waits = {
            poi_id: seconds
            for poi_id, seconds in (route.planned_queue_seconds or {}).items()
            if seconds and poi_id in on_route
        }
        rule = (
            f"Nothing with a wait over {AVOID_QUEUES_EXCLUDE_PEAK_MINUTES} minutes "
            f"at its busiest was considered, as asked"
        )
        if waits:
            worst_id = max(waits, key=waits.get)
            worst_name = next(p.name for p in route.pois if p.id == worst_id)
            notes.append(
                f"{rule}; the longest wait left in this day is "
                f"{max(1, round(waits[worst_id] / 60))} min, at {worst_name}."
            )
        else:
            notes.append(f"{rule}; no waits in this day.")

    # A kept-closed door's exclusion line above already carries the doubt
    # clause when its hours are a guess (the one hedging function composes
    # every closure reason), so listing it again is the same ignorance said
    # twice. Keyed on the kept_outside FIELD, never on the reason's words.
    # Map hours are the top source and carry no doubt (Docs/adr/0006).
    doubt_carried = {e.poi_id for e in route.clock_exclusions if e.kept_outside}
    doubted = [
        p.name
        for p in route.pois
        if p.opening_hours is not None
        and p.opening_hours_source != "map"
        and p.id not in doubt_carried
    ]
    if doubted:
        notes.append("We could not confirm opening times for " + ", ".join(doubted) + ".")
    # A DOOR with no hours at all is the least-known kind and gets its own
    # sentence (Docs/adr/0006: unknown hours fail open WITH that disclosure).
    # Distinct from the could-not-confirm list, which is about hours somebody
    # wrote down.
    no_record = [p.name for p in route.pois if p.gated is True and p.opening_hours is None]
    if no_record:
        notes.append("No opening times on record for " + ", ".join(no_record) + ".")
    return notes


# The plan's own identity, and which of its routes. The hex is a fingerprint of the
# whole plan, so an id minted for one set of routes cannot select a route out of a
# different set — see _preview_plan_fingerprint.
_PREVIEW_ROUTE_ID = re.compile(r"^preview-([0-9a-f]{12})-opt(\d+)$")


@router.post("/trips/preview/author", response_model=TripAuthoredTourResponse)
def author_preview_tour(
    body: TripPreviewAuthorRequest,
    driver: Driver = Depends(get_driver),
    premium_executor: PremiumComposeExecutor = Depends(get_premium_compose_executor),
    faithfulness_checker: FaithfulnessChecker | None = Depends(get_faithfulness_checker),
):
    """Write EXACTLY the route that was chosen. Never re-plans it.

    The second half of the split. It re-derives the free plan from the same request,
    checks the plan is still the one that was shown, and writes that one route — one
    call to the narrator per stop, no retries. Nothing here chooses a route: the
    choice arrived with the request.

    ANONYMOUS AND PAID, for the Phase-1 window only, approved on 2026-08-04 because
    without it the workbench cannot produce a tour at all once planning stops writing.
    As the note further up this file says, nothing bounds what an unauthenticated
    caller can spend here, and with the stop ceiling gone the requested duration is
    the only thing limiting how many paid calls one request makes. Phase 2 closes it
    by giving the workbench a real identity and moving it onto the saved-trip pair.
    """
    with degradation_scope() as collected:
        result = _author_preview_impl(body, driver, premium_executor, faithfulness_checker)
        rows = summarize(collected)
    return result.model_copy(update={"degradations": rows}) if rows else result


def _author_preview_impl(
    body: TripPreviewAuthorRequest,
    driver: Driver,
    premium_executor: PremiumComposeExecutor,
    faithfulness_checker: FaithfulnessChecker | None,
) -> TripAuthoredTourResponse:
    match = _PREVIEW_ROUTE_ID.match(body.route_id)
    if match is None:
        raise HTTPException(422, {"reason": "invalid_route_id", "route_id": body.route_id})
    chosen_fingerprint, option_n = match.group(1), int(match.group(2))

    tour_input = _build_tour_input(
        start=(body.center_lat, body.center_lng),
        duration_min=body.duration_min or DEFAULT_DURATION_MIN,
        city_slug=body.city_slug,
        lenses=body.lenses or None,
        round_trip=body.round_trip,
        max_stop_minutes=body.max_stop_minutes,
        end=_end_point(body.end_lat, body.end_lng),
        start_datetime=body.start_datetime,
        end_hardness=body.end_hardness,
        **_dial_kwargs(body),
    )
    snapshot, plans = _plan_preview(tour_input, driver)
    if not 1 <= option_n <= len(plans):
        raise HTTPException(
            404,
            {"reason": "unknown_option", "route_id": body.route_id, "options": len(plans)},
        )
    # THE CHOSEN ROUTE, OR NOTHING. Planning is free and repeatable given the same
    # corpus and the same walking times, but neither is frozen between the two calls:
    # a content upload or a routing restart moves the plan. The fingerprint makes that
    # visible instead of writing — and charging for — a tour nobody was shown and
    # nobody could tell apart on screen.
    fingerprint = _preview_plan_fingerprint(plans)
    if fingerprint != chosen_fingerprint:
        raise HTTPException(
            409,
            {
                "reason": "plan_changed",
                "route_id": body.route_id,
                "current_route_id": f"preview-{fingerprint}-opt{option_n}",
            },
        )

    plan = plans[option_n - 1]
    route = plan.route
    seq = plan.sequence
    beats_by_id = {ref.id: ref for refs in snapshot.beats_by_poi.values() for ref in refs}
    provider = premium_executor.provider_name

    def _basic_tour_fallback(
        *, reason: str, rejection: CandidateRejection
    ) -> TripAuthoredTourResponse:
        return TripAuthoredTourResponse(
            route_id=body.route_id,
            option=None,
            spine_area=route.spine_area,
            total_audio_min=0,
            candidate_eligible=False,
            narration_kind="none",
            basic_tour=TripPreviewBasicTour(
                reason=reason,
                total_audio_min=round(plan.source.total_audio_seconds / 60),
                stops=_preview_cards(
                    build_route_option(
                        route,
                        plan.source,
                        beats_by_id,
                        route_id=body.route_id,
                        snapshot=snapshot,
                        sequence=seq,
                    )
                ),
            ),
            tourability=_tourability_payload(route.tourability),
            compose_status="basic_available",
            candidate_rejection=rejection,
            provider=provider,
            narration_quality=None,
            quality=None,
        )

    # Resolved BEFORE any physical call: an unresolvable build fingerprint (dirty
    # local tree, malformed deploy SHA) is an environment/config fault, not an LLM
    # authoring failure — it must never be folded into the generic provider-failure
    # branch below, which would both mislabel the cause and hide that ZERO provider
    # spend happened.
    try:
        build_identity = resolve_build_identity()
    except Exception as exc:
        return _basic_tour_fallback(
            reason="llm_candidate_ineligible",
            rejection=CandidateRejection(
                code=CandidateRejectionCode.BUILD_FINGERPRINT_UNAVAILABLE,
                detail=str(exc),
            ),
        )

    try:
        with _upstream_provider_errors():
            physical_responses = execute_premium_plan(
                plan,
                executor=premium_executor,
                receipt_sink=EphemeralReceiptSink(),
            )
            premium_result = finalize_premium_tour(
                plan,
                physical_responses,
                faithfulness_checker=faithfulness_checker,
                build_identity=build_identity,
            )
    except HTTPException:
        raise
    except Exception:
        # LOG BEFORE SWALLOWING. This catch-all collapses every post-planning failure
        # -- provider error, traceability rejection, verification refusal -- into one
        # opaque string, and the response deliberately does not leak provider prose.
        # With no log record the cause was unrecoverable at every layer at once, so a
        # tester's only channel was to guess. The traceback goes to the server log
        # (/tmp/ondoway-workbench-api.log for the workbench); the wire keeps the
        # stable, non-leaking contract below.
        _log = logging.getLogger("ondoway.api")
        _log.exception(
            "Premium authoring failed after planning; returning the Basic lane "
            "(city=%s duration=%s stops_planned=%s)",
            body.city_slug,
            body.duration_min,
            len(plan.units),
        )
        # A VERIFY refusal names counts only ("1 untraceable"), which is not enough to
        # act on: traceability fails for three structurally different reasons (unknown
        # cited beat id, a glue label outside GLUE_LABELS, or an unrecognised
        # source_type -- src/tour/validation.py:145-157). Log the offending sentences'
        # PROVENANCE so one run identifies the rule instead of costing another paid
        # preview per guess. Sentence TEXT is truncated and stays server-side only.
        _report = getattr(sys.exc_info()[1], "report", None)
        for _s in getattr(_report, "untraceable_sentences", ()) or ():
            _log.error(
                "  UNTRACEABLE stop=%s source_type=%r source_id=%r cited=%r text=%.120r",
                getattr(_s, "stop_idx", None),
                getattr(_s, "source_type", None),
                getattr(_s, "source_id", None),
                tuple(getattr(_s, "cited_beat_ids", ()) or ()),
                getattr(_s, "text", ""),
            )
        # FAITHFULNESS and COVERAGE were counted and then thrown away, for the same
        # reason traceability was: "27 faithfulness" names a number, not a defect, so
        # the only way to see WHICH sentences failed was another paid preview per
        # guess. Both carry a (subject, reason) pair the counts discard.
        for _s, _reason in getattr(_report, "faithfulness_failures", ()) or ():
            _log.error(
                "  UNFAITHFUL stop=%s reason=%r source_id=%r cited=%r text=%.160r",
                getattr(_s, "stop_idx", None),
                _reason,
                getattr(_s, "source_id", None),
                tuple(getattr(_s, "cited_beat_ids", ()) or ()),
                getattr(_s, "text", ""),
            )
        for _bid, _claim in getattr(_report, "coverage_failures", ()) or ():
            _log.error("  DROPPED-FACT beat=%r claim=%.160r", _bid, _claim)
        return _basic_tour_fallback(
            reason="llm_generation_failed",
            rejection=CandidateRejection(
                code=CandidateRejectionCode.GENERATION_FAILED,
                detail="Premium authoring did not produce a complete traced blueprint",
            ),
        )

    script = premium_result.blueprint.script
    narration = " ".join(s.text for s in script.script)
    q = score_narration(narration)
    narration_quality = {
        "stilted_score": q.stilted_score,
        "engagement_score": q.engagement_score,
        "reliable": q.reliable,
        "n_words": q.n_words,
        "burstiness": q.burstiness,
        "mean_sentence_words": q.mean_sentence_words,
        "long_sentence_rate": q.long_sentence_rate,
        "second_person_per_100w": q.second_person_rate,
        "look_prompt_rate": q.look_prompt_rate,
        "tells_per_100w": q.per_100w,
    }

    # THE QUALITY RUBRIC — runs on EVERY tour, deterministic and $0. The mechanical
    # floor of fixtures/tour-quality-standard/01-standard.md: it catches the
    # two failures the corpus-vs-render comparison can prove (a rich POI STARVED to a
    # line, a stop GORGED past the listenable cap) plus repeats, empty stops and
    # imbalance. Surfaced to the editor rather than silently swallowed — a tour that
    # breaches the standard is visible in the workbench with the exact check and stop.
    #
    # Paid semantic FACT/ENJOY/repetition reviewers are certification concerns and never
    # run in this interactive candidate endpoint. The advisory G4 and coverage-omission
    # blocks this response used to carry are GONE rather than empty: their judges are
    # deleted, so a permanently-`[]` "findings" list advertised a check that could not
    # fire. Deterministic quality is the only local diagnostic.
    #
    # Scored ONCE. Two lanes each added this call with their own comment on 2026-08-05 and
    # the duplicate scored the whole tour a second time on every request, discarding the
    # first result. Both comments were worth keeping; the second traversal was not.
    rubric = score_tour(script, route, snapshot.beats_by_poi, beat_sequence=seq)

    # THE CHOSEN ROUTE WITH THE WORDS FILLED IN. Same places, same order, same arrival
    # time, same walk-past sights — it is built by the one shared interleave from the
    # SAME route object the plan produced, so there is nothing to re-derive and nothing
    # to patch back on afterwards.
    return TripAuthoredTourResponse(
        route_id=body.route_id,
        option=build_route_option(
            route,
            script,
            beats_by_id,
            route_id=body.route_id,
            snapshot=snapshot,
            sequence=seq,
        ),
        spine_area=route.spine_area,
        total_audio_min=round(script.total_audio_seconds / 60),
        candidate_eligible=True,
        candidate_status="premium_candidate_eligible_for_certification",
        narration_kind="llm_candidate",
        basic_tour=None,
        tourability=_tourability_payload(route.tourability),
        compose_status="composed",
        candidate_rejection=None,
        provider=provider,
        narration_quality=narration_quality,
        quality={
            # THE DETERMINISTIC, $0 rubric ALONE decides pass/fail.
            "passed": rubric.passed,
            "summary": rubric.summary(),
            "blockers": [
                {
                    "check": f.check,
                    "message": f.message,
                    "stop_idx": f.stop_idx,
                    "poi_name": f.poi_name,
                }
                for f in rubric.blockers
            ],
            "warnings": [
                {
                    "check": f.check,
                    "message": f.message,
                    "stop_idx": f.stop_idx,
                    "poi_name": f.poi_name,
                }
                for f in rubric.warnings
            ],
            "stats": dict(rubric.stats),
        },
    )


@router.get("/trips", response_model=list[TripGenerateResponse])
def list_trips(
    profile_id: str = Query(..., description="Profile ID to list trips for"),
    current_user: dict = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """List all saved trips for a profile, including their stops.

    OWNERSHIP-SCOPED: trip names, stop POIs, coordinates, start times and
    narration of ANY profile used to be readable by guessing a profile id. The
    ownership check runs BEFORE the read, and a profile the caller does not own
    is a 404 — indistinguishable from one that does not exist.
    """
    if _owned_profile_id(session, current_user["id"], profile_id) is None:
        raise HTTPException(404, f"Profile '{profile_id}' not found")

    result = list_trips_for_profile(session, profile_id)
    if result is None:
        raise HTTPException(404, f"Profile '{profile_id}' not found")

    return [
        TripGenerateResponse(
            trip_id=t["trip_id"],
            trip_name=t["trip_name"],
            profile_id=t["profile_id"],
            captained=t["captained"],
            total_stops=t["total_stops"],
            total_duration_min=t["total_duration_min"],
            anchor_count=t["anchor_count"],
            flavour_count=t["flavour_count"],
            stops=[GeneratedStop(**s) for s in t["stops"]],
        )
        for t in result
    ]
