"""One Premium planning and finalization seam for batch and workbench tours.

Planning is deterministic and provider-free. Execution is injected and returns
opaque physical response bytes. Finalization is pure: it reconstructs every
authorized request, verifies the response set, and freezes an immutable
``FinalTourBlueprint``. Paid semantic reviewers are intentionally absent; they
belong to the separate certification workflow.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import logging
import os
import re
import subprocess
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from src.audio.provider import OpenAITTSProvider
from src.tour.degradations import in_current_context, record

from .artifact import (
    BuildFingerprint,
    FinalTourBlueprint,
    build_final_blueprint,
    derive_playback_assignments,
    remap_provider_playback_assignments,
    sentences_payload_sha256,
    validate_llm_composed_blueprint,
)
from .authoring import (
    _COMPOSE_OUTPUT_SCHEMA,
    _COMPOSE_SYSTEM,
    CERTIFICATION_COMPOSE_MAX_OUTPUT_TOKENS,
    COMPOSE_MODEL,
    CertificationComposition,
    CompletedCertificationComposeUnit,
    ComposeRequest,
    _certification_compose_requests,
    _sentences_from_json,
    _threads_from_json,
    candidate_compose_request_envelope,
    compose_input_sha256,
    finalize_certification_composition,
)
from .batch_regression_manifest import _canonical_bytes
from .beat_select import select_vignette_beats
from .candidate_authoring import (
    AuthoringCandidateIdentity,
    AuthoringCandidatePlan,
    AuthoringCandidateResponseSet,
    AuthoringStopRequest,
    AuthoringStopResponse,
)
from .certification_provider import (
    AnthropicCertificationProvider,
    CallPurpose,
    PhysicalProviderResponse,
)
from .compose_gate import ComposeVerificationError
from .contract import (
    BeatRef,
    BeatSequence,
    POIBeats,
    ReplanContext,
    Route,
    Script,
    Sentence,
    TourInput,
)
from .corpus_places import bytes_sha256
from .generation import (
    CONCURRENT_GLUE_LABELS,
    generate,
    split_sentences,
    transit_class_beat_ids,
)
from .glue_client import NO_GLUE_SENTINEL, GlueClient
from .premium_authorities import PREMIUM_AUTHORITIES, PremiumAuthorityHashes
from .routing import MAX_REQUESTED_FRACTION, MIN_REQUESTED_FRACTION, RoutePlanningPolicy
from .routing_client import (
    THIS_BUILDS_ROUTING_CONFIG_SHA256S,
    VALHALLA_ROUTING_CONFIG_SHA256_BY_SURFACE,
    RoutingClient,
)
from .selection import (
    CorpusSnapshot,
    MaterializedCorpusSnapshot,
    build_poi_beat_plans_capped,
    choose_discrete_route,
    select_route,
)
from .verify import FaithfulnessChecker

PREMIUM_MODULE_VERSION = "ondoway-premium-tour-v1"
REPO_ROOT = Path(__file__).resolve().parents[2]


def premium_authoring_policy_sha256() -> str:
    """Hash the exact physical authoring policy shared by every caller."""

    return bytes_sha256(
        _canonical_bytes(
            {
                "model": COMPOSE_MODEL,
                "max_tokens": CERTIFICATION_COMPOSE_MAX_OUTPUT_TOKENS,
                "thinking": {"type": "adaptive"},
                "system": _COMPOSE_SYSTEM,
                "output_schema": _COMPOSE_OUTPUT_SCHEMA,
            }
        )
    )


def exact_snapshot_sha256(snapshot: CorpusSnapshot) -> str:
    """Bind the exact selection input, using the materializer hash when present."""

    if isinstance(snapshot, MaterializedCorpusSnapshot):
        return snapshot.snapshot_sha256
    payload = {
        "pois": sorted(
            (poi.model_dump(mode="json") for poi in snapshot.pois),
            key=lambda item: (str(item.get("canonical_place_id") or ""), str(item["id"])),
        ),
        "beats": sorted(
            (
                beat.model_dump(mode="json")
                for beats in snapshot.beats_by_poi.values()
                for beat in beats
            ),
            key=lambda item: (str(item.get("stable_beat_id") or ""), str(item["id"])),
        ),
        "area_types": sorted((str(k), str(v)) for k, v in snapshot.area_types.items()),
        "adjacent_areas": sorted(
            (str(k), sorted(str(item) for item in values))
            for k, values in snapshot.adjacent_areas.items()
        ),
        "lens_neighbors": sorted(
            (str(k), sorted(str(item) for item in values))
            for k, values in snapshot.lens_neighbors.items()
        ),
    }
    return bytes_sha256(_canonical_bytes(payload))


def route_summary(route: Route) -> dict[str, object]:
    """Canonical public route record used by Premium plan identities."""

    return {
        "route_sha256": bytes_sha256(_canonical_bytes(route.model_dump(mode="json"))),
        "routed": route.routed,
        "total_walk_seconds": route.total_walk_seconds,
        "total_walk_distance_m": route.total_walk_distance_m,
        "pois": [
            {"id": poi.id, "name": poi.name, "lat": poi.lat, "lng": poi.lng} for poi in route.pois
        ],
        "legs": [
            {
                "index": index,
                "seconds": transit.leg_seconds,
                "distance_m": transit.leg_distance_m,
                "request_sha256": transit.valhalla_receipt.request_sha256,
                "response_sha256": transit.valhalla_receipt.response_sha256,
            }
            for index, transit in enumerate(route.transits)
            if transit.valhalla_receipt is not None
        ],
    }


class PremiumRouteInfeasibleError(ValueError):
    """A Premium route lacks complete physical Valhalla evidence."""


@dataclass(frozen=True)
class PremiumComposeUnit:
    stop_index: int
    poi_name: str
    authorized_request: ComposeRequest
    authoring_request: AuthoringStopRequest
    request_sha256: str
    input_byte_count: int
    output_token_ceiling: int
    sdk_request: dict[str, object]

    def public_payload(self) -> dict[str, object]:
        return {
            "stop_index": self.stop_index,
            "poi_name": self.poi_name,
            "request_id": self.authoring_request.request_id,
            "request_sha256": self.request_sha256,
            "input_byte_count": self.input_byte_count,
            "output_token_ceiling": self.output_token_ceiling,
        }


@dataclass(frozen=True)
class PremiumTourPlan:
    tour_input: TourInput
    snapshot: CorpusSnapshot
    snapshot_sha256: str
    route: Route
    route_record: dict[str, object]
    sequence: BeatSequence
    source: Script
    candidate: AuthoringCandidateIdentity
    authoring: AuthoringCandidatePlan
    units: tuple[PremiumComposeUnit, ...]
    routing_version: str
    policy_version: str
    authorities: PremiumAuthorityHashes
    #: A place recipe's plan: its request is a place told on its own. The finalizer
    #: rebuilds the request the same way.
    standalone_place: bool = False

    def batch_payload(
        self, *, case_id: str, archetype: str, request_sha256: str
    ) -> dict[str, object]:
        core = {
            "schema_version": "ondoway-tour-batch-tour-plan-v1",
            "case_id": case_id,
            "archetype": archetype,
            "request_sha256": request_sha256,
            "tour_input": self.tour_input.model_dump(mode="json"),
            "candidate": self.candidate.model_dump(mode="json"),
            "route": self.route_record,
            "grounded_source_sha256": sentences_payload_sha256(self.source.script),
            "model": COMPOSE_MODEL,
            "thinking": {"type": "adaptive"},
            "sdk_max_retries": 0,
            "application_deadline_seconds": None,
            "units": [unit.public_payload() for unit in self.units],
        }
        return {**core, "tour_plan_sha256": bytes_sha256(_canonical_bytes(core))}


def plan_premium_authoring(
    source: Script,
    beat_sequence: BeatSequence,
    route: Route,
    *,
    snapshot: CorpusSnapshot,
    snapshot_sha256: str,
    routing_version: str,
    policy_version: str,
    authorities: PremiumAuthorityHashes = PREMIUM_AUTHORITIES,
    already_told_by_stop: Mapping[int, str] | None = None,
    single_stop: int | None = None,
    standalone_place: bool = False,
) -> PremiumTourPlan:
    """Build every per-stop authoring request for an ALREADY-PLANNED route.

    ``standalone_place`` plans a place recipe: a place told on its own, with no walk
    (see ``_certification_compose_requests``).

    ``single_stop`` + ``already_told_by_stop``: a FULL
    TELLING is a one-stop composition over the stop's SECOND story, planned through
    THIS same builder so the request/verify shape cannot fork — the source script
    holds only that stop's overflow sentences, the coverage bar adapts to the one
    stop, and its request carries the tight telling as ALREADY TOLD.

    THE ONE Block-2 plan builder. Pure and provider-free: no routing client, no
    selection, no LLM. It is called by ``plan_premium_tour`` (which plans the route
    first) and by ``POST /trips/{trip_id}/compose`` (whose route is already persisted),
    so the two surfaces cannot drift into separate per-stop request shapes again.

    ``tour_input`` is taken from ``source.inputs`` rather than accepted separately:
    ``generate`` writes it there verbatim (src/tour/generation.py:424), and
    ``FinalTourBlueprint`` refuses a script whose inputs differ from the blueprint's
    tour_input (src/tour/artifact.py:677-678), so a separate parameter could only ever
    introduce a mismatch.
    """

    _beats_by_id, stops, requests = _certification_compose_requests(
        source,
        beat_sequence,
        route,
        already_told_by_stop=already_told_by_stop,
        standalone_place=standalone_place,
    )
    if stops[-1] >= len(route.pois):
        raise ValueError("the stitched script names a stop the prebuilt route lacks")
    if single_stop is not None and stops != [single_stop]:
        raise ValueError("a single-stop plan's source must hold exactly that stop")
    # Exactly ONE unit per dwell stop. ``stops`` comes from the stitched script's
    # sentences, so a stop the stitch dropped simply would not appear — and a
    # missing TAIL stop passes every other bar: it is in order, it starts at 0,
    # and its highest index is in range. The plan would then hold fewer units
    # than the route has stops, a caller would reserve and spend for those, and
    # the trip would persist with its last stop never authored at all.
    if single_stop is None and len(stops) != len(route.pois):
        raise ValueError("the prebuilt route needs one authoring unit per dwell stop")
    summary = route_summary(route)
    candidate = AuthoringCandidateIdentity.create(
        candidate_slot="A",
        contract_sha256=authorities.contract_sha256,
        reference_manifest_sha256=authorities.reference_manifest_sha256,
        calibration_manifest_sha256=authorities.calibration_manifest_sha256,
        grounded_source_sha256=sentences_payload_sha256(source.script),
        route_sha256=str(summary["route_sha256"]),
        authoring_policy_sha256=premium_authoring_policy_sha256(),
    )
    authoring = AuthoringCandidatePlan(
        candidate=candidate,
        stop_requests=tuple(
            AuthoringStopRequest.create(
                candidate=candidate,
                stop_index=stop_index,
                compose_input_sha256=compose_input_sha256(requests[stop_index]),
            )
            for stop_index in stops
        ),
    )
    units: list[PremiumComposeUnit] = []
    for stop_request in authoring.stop_requests:
        stop_index = stop_request.stop_index
        envelope, sdk_request = candidate_compose_request_envelope(
            requests[stop_index], stop_request, model=COMPOSE_MODEL
        )
        encoded = envelope.encode("utf-8")
        units.append(
            PremiumComposeUnit(
                stop_index=stop_index,
                poi_name=route.pois[stop_index].name,
                authorized_request=requests[stop_index],
                authoring_request=stop_request,
                request_sha256=bytes_sha256(encoded),
                input_byte_count=len(encoded),
                output_token_ceiling=CERTIFICATION_COMPOSE_MAX_OUTPUT_TOKENS,
                sdk_request=sdk_request,
            )
        )
    return PremiumTourPlan(
        tour_input=source.inputs,
        snapshot=snapshot,
        snapshot_sha256=snapshot_sha256,
        route=route,
        route_record=summary,
        sequence=beat_sequence,
        source=source,
        candidate=candidate,
        authoring=authoring,
        units=tuple(units),
        routing_version=routing_version,
        policy_version=policy_version,
        authorities=authorities,
        standalone_place=standalone_place,
    )


def certification_planning_policy(*, policy_id: str) -> RoutePlanningPolicy:
    return RoutePlanningPolicy.certification(
        minimum_requested_fraction=MIN_REQUESTED_FRACTION,
        maximum_requested_fraction=MAX_REQUESTED_FRACTION,
        policy_id=policy_id,
    )


class SilentGlueClient:
    """The glue client BLOCK 1 plans with: it writes nothing and calls nobody.

    Owner ruling, 2026-08-04: during route planning, on both surfaces, the options show
    THE PLACES ALONE — POI names, order, walking times, ETA. No descriptive text of any
    kind. All prose arrives only at script generation, after a route has been picked.

    This is what makes Block 1 genuinely free. ``generate`` defaults to the REAL, paid
    ``HaikuGlueClient`` (src/tour/generation.py), so planning three route options would
    otherwise make three tours' worth of Haiku calls on an endpoint whose whole value
    proposition is that it costs nothing. Returning the NO_GLUE sentinel for every
    category makes ``generate`` fall back to its own deterministic connective template,
    so the stitched source is still complete — it simply contains no LLM-written line.

    NOT a mock and not a test double: it is never substituted for anything, and nothing
    else is standing behind it. It is the literal expression of "planning writes no
    words", and it is what the PRODUCT uses. A caller that wants prose passes a real
    client explicitly, which is what AUTHOR does for the one option the traveller picked.
    """

    def stitch(self, category: str, context: str, request: str) -> str:
        del category, context, request
        return NO_GLUE_SENTINEL


#: A route whose leg times are estimated rather than measured. ONE kind for the whole
#: outage — ``summarize`` collapses repeats into one row with a count
#: (src/tour/degradations.py:152-166), and nobody needs it per leg or per flavour.
ESTIMATED_LEGS_DEGRADATION = "walking_times_estimated"
#: The routing service answered, but with a setup this build was not compiled against.
ROUTING_CONFIG_DEGRADATION = "routing_setup_unexpected"
#: The routing service would not say which version it is running.
ROUTING_VERSION_DEGRADATION = "routing_version_unavailable"
#: Stamped into the build fingerprint when the version above could not be read. It is a
#: deliberately unmistakable word: ``BuildFingerprint.routing_version`` only requires a
#: non-empty string (src/tour/artifact.py:428), so a plausible-looking placeholder would
#: read back years later as a real engine version. Per-leg provenance is unaffected —
#: every measured leg carries its own request, response and config hash in its receipt.
ROUTING_VERSION_UNAVAILABLE = "unavailable"


def _premium_route_refusal(route: Route) -> str | None:
    """Why this route is STRUCTURALLY unplannable, or None.

    Only the two faults in the plan's own shape refuse. A route with no stops is not a
    tour, and a leg count that does not match the stop count breaks the index alignment
    every consumer downstream assumes (``route_summary``'s ``enumerate`` below, and the
    leg-walk map in ``build_route_option``).

    MISSING ROUTE MEASUREMENTS DO NOT REFUSE (owner ruling 2026-08-04). The walking
    engine is a real production service that can cold-start, restart or rebuild its map
    (render.yaml:98-124), and a refusal here would take tour generation down for every
    user for the duration of that. It is labelled instead — see
    ``record_routing_degradations`` — so the silent substitution is gone without the
    outage taking the product with it.

    Still a predicate rather than a raise: the caller owns the decision, and the
    batch runner reads the reason without exception plumbing.
    """

    if not route.pois:
        return "Premium planning requires a route with at least one stop"
    # One leg INTO each stop, plus — on a round trip — the loop-home leg
    # (a live-wire finding, 2026-08-11: the strict equality refused
    # EVERY round-trip day on the API path with "one walking leg per stop";
    # the harness never hit it because it plans without the premium gate, and
    # no test had ever pushed a round trip through this door).
    if len(route.transits) not in (len(route.pois), len(route.pois) + 1):
        return "Premium planning requires one walking leg per stop"
    return None


def record_routing_degradations(
    route: Route, *, component: str = "premium_tour.plan_premium_tour"
) -> None:
    """Label a route whose walking times were estimated rather than measured.

    A no-op outside a degradation scope (src/tour/degradations.py:137-139), so the batch
    runner and unit tests are unaffected; the API surfaces open one per request.

    PUBLIC because planning is not the only place a receiptless route reaches a person.
    Composing a saved trip rebuilds its route directly through ``summarise_route`` and
    never comes back through here, so it must call this itself or ship an unlabelled
    estimate, and neither surface may ship one unlabelled. ``component`` names the caller,
    since that is what the field is for (src/tour/degradations.py:50-51).
    """

    estimated = [
        index for index, transit in enumerate(route.transits) if transit.valhalla_receipt is None
    ]
    if estimated or not route.routed:
        record(
            kind=ESTIMATED_LEGS_DEGRADATION,
            human=(
                "Walking times between stops are estimates, not measured routes, so "
                "the tour may run a little longer or shorter than it says."
            ),
            component=component,
            cause=(
                f"The routing service returned no measured route for {len(estimated)} "
                f"of {len(route.transits)} legs. Those legs fell back to straight-line "
                "distance scaled by HAVERSINE_CORRECTION at PACE_KMH "
                "(src/tour/routing.py:41-53), which cannot see rivers, walls or closed "
                "streets, so a leg across an unbridged gap is understated. Check that "
                "ondoway-valhalla is up and has finished building its tiles."
            ),
            estimated_legs=str(len(estimated)),
            total_legs=str(len(route.transits)),
            fully_measured=str(route.routed).lower(),
        )
    # GUARDED ON A NON-EMPTY SET, and that guard is the whole point. This set is empty
    # when NO transit has a receipt, and an empty set is not equal to the expected one —
    # so an unguarded comparison would report a total outage as a routing-SETUP mismatch
    # as well, two rows describing one fault, one of them wrong.
    receipt_configs = {
        transit.valhalla_receipt.routing_config_sha256
        for transit in route.transits
        if transit.valhalla_receipt is not None
    }
    # THIS build's settings under ANY route-surface override are this build's
    # settings (a step-free day was falsely labelled without this): only a
    # receipt whose configuration matches none of them was routed by something else.
    foreign = receipt_configs - THIS_BUILDS_ROUTING_CONFIG_SHA256S
    if foreign:
        record(
            kind=ROUTING_CONFIG_DEGRADATION,
            human=(
                "Walking times for this tour were worked out with different settings "
                "than this version expects, so they may be a little off."
            ),
            component=component,
            cause=(
                f"{len(foreign)} leg routing-config hash(es) match none of this build's "
                "settings (src/tour/routing_client.py THIS_BUILDS_ROUTING_CONFIG_SHA256S, "
                "the default and every route-surface override). The deployed Valhalla "
                "configuration and the one compiled into this build have diverged."
            ),
            expected_setups=str(len(THIS_BUILDS_ROUTING_CONFIG_SHA256S)),
            observed_setups=str(len(receipt_configs)),
        )


def resolve_routing_version(routing_client: RoutingClient) -> str:
    """The routing engine's version, or a labelled admission that it could not be read.

    THE ONE CALL AN OUTAGE CAN HARD-FAIL, on both the planning and the compose paths.
    Every walking leg independently degrades to a straight-line estimate when the
    container is unreachable (src/tour/routing_client.py:175-181), but
    ``routing_version`` deliberately has no fallback of its own: it does a live
    GET /status and treats a missing or malformed answer as an error
    (src/tour/routing_client.py:183-200).

    Left bare, that escapes as a transport error — a generic 500 on the planning path,
    and on the compose path it used to be swept up and relabelled
    ``compose_verification_failed``, a code meaning "the narrator wrote something
    untraceable", which blames the writer for a container that is merely still booting.

    RESOLVED 2026-08-05 TO A DEGRADATION, NOT A REFUSAL, which is the same answer the
    receipt bar above now gives to the same outage. A hard refusal here would take tour
    generation down app-wide for a cold start, which is exactly what the owner ruled
    against; and the provenance a refusal would be protecting does not exist during an
    outage, because unmeasured legs were measured by no engine. Recording
    ``unavailable`` states that plainly, and the traveller is told on the wire rather
    than in a log file nobody opens.
    """

    try:
        return routing_client.routing_version()
    except Exception as exc:
        record(
            kind=ROUTING_VERSION_DEGRADATION,
            human=(
                "We could not record which version of the map worked out this tour's "
                "walking times, so its record of how it was built is incomplete."
            ),
            component="premium_tour.resolve_routing_version",
            error=exc,
            cause=(
                "A live status read against the routing engine failed or answered "
                "without a version, so the build fingerprint records "
                f"routing_version={ROUTING_VERSION_UNAVAILABLE!r} instead of a real "
                "one. Per-leg provenance is unaffected: every measured leg still "
                "carries its own request, response and routing-config hash. Check that "
                "ondoway-valhalla is up and answering /status."
            ),
        )
        return ROUTING_VERSION_UNAVAILABLE


def _plan_one_premium_route(
    tour_input: TourInput,
    snapshot: CorpusSnapshot,
    route: Route,
    *,
    policy: RoutePlanningPolicy,
    routing_version: str,
    generation_time: dt.datetime | None,
    authorities: PremiumAuthorityHashes,
    glue_client: GlueClient | None,
) -> PremiumTourPlan:
    """Everything one already-chosen route needs to become an authorable plan."""

    capped = build_poi_beat_plans_capped(
        route,
        snapshot,
        lenses=tour_input.lenses,
        end_is_none=tour_input.end is None,
        narration_density=tour_input.narration_density,
    )
    vignette_beats = select_vignette_beats(
        route.vignettes,
        snapshot.beats_by_poi,
        lenses=tour_input.lenses,
    )
    sequence = BeatSequence(
        poi_beats=tuple(plan for plan, _overflow in capped),
        vignette_beats=vignette_beats,
        overflow_by_poi={plan.poi_id: overflow for plan, overflow in capped if overflow},
    )
    source = generate(
        sequence,
        route,
        tour_input,
        glue_client=glue_client,
        now=generation_time or dt.datetime.now(dt.UTC),
        validate_output=False,
    )
    return plan_premium_authoring(
        source,
        sequence,
        route,
        snapshot=snapshot,
        snapshot_sha256=exact_snapshot_sha256(snapshot),
        routing_version=routing_version,
        policy_version=policy.policy_id,
        authorities=authorities,
    )


def plan_premium_tour(
    tour_input: TourInput,
    snapshot: CorpusSnapshot,
    *,
    routing_client: RoutingClient,
    planning_policy: RoutePlanningPolicy | None = None,
    generation_time: dt.datetime | None = None,
    authorities: PremiumAuthorityHashes = PREMIUM_AUTHORITIES,
    glue_client: GlueClient | None = None,
    replan: ReplanContext | None = None,
) -> PremiumTourPlan:
    """BLOCK 1 — THE day of one request, planned, with no per-stop authoring.

    ``replan`` (`contract.ReplanContext`) plans RELATIVE to a day
    that exists — the session's replans and the contingency set hand one
    in; it rides straight through to `select_route`, THE one planner. There is
    no second planner behind this door. A replanned TAIL may be
    the bare walk to its finish (one leg home): the end sentinel is
    its one stop, so the structural refusal below still sees a route with a stop.

    Pick-one-of-three is deleted: a visitor does not want to
    compare routes, so the planner plans ONE day — one ``select_route`` call — and
    the dials on the request are how a person changes it. ``choose_discrete_route``
    stays as the container-identity refusal on the one route: with containers
    already excluded from the dwell pool the raise is a can't-happen guard, kept
    because the one-engine suite pins the primitive as THE planner's own.

    FREE, and free BY DEFAULT. ``glue_client=None`` here means ``SilentGlueClient``,
    not ``generate``'s paid Haiku default: planning a route is a places-and-times
    question and makes no provider call at all. The DEFAULT is the
    part that matters — a free path that is only free when a caller remembers to ask
    is one refactor away from being paid again, on an endpoint that stays anonymous.
    """

    policy = planning_policy or certification_planning_policy(policy_id=PREMIUM_MODULE_VERSION)
    routing_version = resolve_routing_version(routing_client)
    route = select_route(
        tour_input,
        snapshot,
        routing_client=routing_client,
        planning_policy=policy,
        replan=replan,
    )
    chosen = choose_discrete_route([route])
    refusal = _premium_route_refusal(chosen)
    if refusal is not None:
        raise PremiumRouteInfeasibleError(refusal)
    record_routing_degradations(chosen)
    return _plan_one_premium_route(
        tour_input,
        snapshot,
        chosen,
        policy=policy,
        routing_version=routing_version,
        generation_time=generation_time,
        authorities=authorities,
        glue_client=glue_client if glue_client is not None else SilentGlueClient(),
    )


class PremiumComposeExecutor(Protocol):
    cost_bearing: bool
    provider_name: str

    def execute(self, unit: PremiumComposeUnit) -> PhysicalProviderResponse: ...


class PremiumReceiptSink(Protocol):
    def before_call(self, unit: PremiumComposeUnit) -> None: ...

    def after_call(self, unit: PremiumComposeUnit, response: PhysicalProviderResponse) -> None: ...


class EphemeralReceiptSink:
    """Explicit workbench policy: retain receipts only for this response."""

    def before_call(self, unit: PremiumComposeUnit) -> None:
        del unit

    def after_call(self, unit: PremiumComposeUnit, response: PhysicalProviderResponse) -> None:
        del unit, response


class AnthropicPremiumExecutor:
    cost_bearing = True
    provider_name = "anthropic"

    def __init__(self, provider: AnthropicCertificationProvider | None = None) -> None:
        self._provider = provider or AnthropicCertificationProvider()

    def execute(self, unit: PremiumComposeUnit) -> PhysicalProviderResponse:
        return self._provider.invoke(
            purpose=CallPurpose.COMPOSE,
            unit_id=f"stop:{unit.stop_index}",
            request=unit.sdk_request,
        )


class OfflinePremiumExecutor:
    """Explicit $0 adapter for hermetic tests; never selected by product code."""

    cost_bearing = False
    provider_name = "offline"

    def execute(self, unit: PremiumComposeUnit) -> PhysicalProviderResponse:
        body = _canonical_bytes(
            {
                "sentences": [
                    sentence.model_dump(mode="json")
                    for sentence in unit.authorized_request.stitched.script
                ]
            }
        )
        return PhysicalProviderResponse(
            body=body,
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            model=COMPOSE_MODEL,
            provider_request_id=f"offline-{unit.stop_index}",
            stop_reason="end_turn",
        )


def execute_premium_plan(
    plan: PremiumTourPlan,
    *,
    executor: PremiumComposeExecutor,
    receipt_sink: PremiumReceiptSink,
    max_workers: int = 6,
) -> tuple[PhysicalProviderResponse, ...]:
    """Execute each immutable stop request once around an injected receipt sink."""

    if not 1 <= max_workers <= 8:
        raise ValueError("Premium execution supports one to eight workers")
    for unit in plan.units:
        receipt_sink.before_call(unit)

    def invoke(unit: PremiumComposeUnit) -> PhysicalProviderResponse:
        # A unit that fails inside the fan-out must be recorded — exception
        # type, message and stop index, in the CALLER's scope — before it is
        # re-raised. A partial premium tour must abort, never ship
        # half-narrated, so this never swallows the exception.
        try:
            response = executor.execute(unit)
        except Exception as exc:
            record(
                kind="premium_unit_failed",
                human=(
                    "A stop failed to compose, so the tour was stopped rather than "
                    "shipped half-narrated."
                ),
                component="execute_premium_plan.invoke",
                error=exc,
                stop_index=str(unit.stop_index),
            )
            raise
        receipt_sink.after_call(unit, response)
        return response

    # in_current_context: contextvars do NOT cross into pool workers, so without
    # this a degradation recorded inside a compose call is silently dropped.
    with ThreadPoolExecutor(max_workers=min(max_workers, len(plan.units))) as pool:
        return tuple(pool.map(in_current_context(invoke), plan.units))


#: The full telling's HARD length cap — twelve minutes, and at most three times
#: the tight telling — and the words-per-second the
#: engine's own clock arithmetic uses (150 wpm).
FULL_TELLING_CAP_SECONDS: int = 720
FULL_TELLING_MAX_RATIO: int = 3
_WORDS_PER_SECOND: float = 2.5
FULL_TELLING_DROPPED_DEGRADATION = "full_telling_dropped"


@dataclass(frozen=True)
class FullTellingBudget:
    """One major stop's two lengths, in seconds."""

    tight_seconds: int
    full_budget_seconds: int


@dataclass(frozen=True)
class FullTelling:
    """A major stop's authored second piece: the narration, its own close, and the
    checked sentences they are made of."""

    narration: str
    close_text: str
    sentences: tuple[Sentence, ...] = ()


def full_telling_budget(tight_words: int) -> FullTellingBudget:
    """A stop's two lengths from its tight telling's word count: the full telling may
    run three times the tight one, never past the hard cap."""

    tight_seconds = round(tight_words / _WORDS_PER_SECOND)
    return FullTellingBudget(
        tight_seconds=tight_seconds,
        full_budget_seconds=min(FULL_TELLING_MAX_RATIO * tight_seconds, FULL_TELLING_CAP_SECONDS),
    )


def full_telling_sequence(
    route: Route, snapshot: CorpusSnapshot, sequence: BeatSequence
) -> BeatSequence:
    """The second stories of a route's stops: each stop's overflow beats, the ones the
    cap trimmed, as the beat sequence a full-telling plan is written from."""

    overflow_refs_by_poi = {
        poi.id: tuple(
            beat
            for beat in snapshot.beats_by_poi.get(poi.id, ())
            if beat.id in set(sequence.overflow_by_poi.get(poi.id, ()))
        )
        for poi in route.pois
    }
    return BeatSequence(
        poi_beats=tuple(
            POIBeats(
                poi_id=poi.id,
                poi_name=poi.name,
                ordering_strategy="narrative_function",
                beats=overflow_refs_by_poi[poi.id],
            )
            for poi in route.pois
            if overflow_refs_by_poi[poi.id]
        ),
        overflow_by_poi=sequence.overflow_by_poi,
    )


def full_telling_majors(
    script: Script, beat_sequence: BeatSequence, route: Route
) -> dict[int, FullTellingBudget]:
    """The MAJOR stops of a composed day, with each one's full-telling budget.

    MAJOR = a stop whose PRICED VISIT can
    hold the full telling after the tight one AND whose corpus holds a second
    story. Read as: ``overflow_by_poi`` non-empty (the second story exists) and
    ``planned_visit_seconds >= 2 x tight`` — the floor at which a continuation at
    least as long as the telling itself fits inside the priced dwell. The
    budget is ``min(3 x tight, FULL_TELLING_CAP_SECONDS)``. Where the corpus is
    thin there is no full telling, and that beats water.
    """
    words_by_stop: dict[int, int] = {}
    for sentence in script.script:
        words_by_stop[sentence.stop_idx] = words_by_stop.get(sentence.stop_idx, 0) + len(
            sentence.text.split()
        )
    majors: dict[int, FullTellingBudget] = {}
    for stop_index, poi in enumerate(route.pois):
        if not beat_sequence.overflow_by_poi.get(poi.id):
            continue
        tight_words = words_by_stop.get(stop_index, 0)
        if tight_words == 0:
            continue
        budget = full_telling_budget(tight_words)
        priced = route.planned_visit_seconds.get(poi.id, 0)
        if priced < 2 * budget.tight_seconds:
            continue
        majors[stop_index] = budget
    return majors


def one_stop_source(
    script: Script,
    beats: tuple[BeatRef, ...] | list[BeatRef],
    *,
    poi_name: str,
    stop_index: int,
    n_stops: int,
) -> Script:
    """One stop's writer source: the beats' own sentences, then the stop's fallback
    close for the writer to rewrite. It carries no opener, walking line or day close,
    so what the writer returns tells the place and nothing about a walk."""
    from .generation import GLUE_CLOSING, _beat_to_sentences, fallback_close_text

    sentences: list[Sentence] = []
    for beat in beats:
        sentences.extend(_beat_to_sentences(beat, stop_index))
    sentences.append(
        Sentence(
            text=fallback_close_text(
                script.inputs,
                poi_name=poi_name,
                is_rest=False,
                is_last=False,
                n_stops=n_stops,
                next_name=None,
            ),
            source_id=GLUE_CLOSING,
            source_type="glue",
            stop_idx=stop_index,
        )
    )
    return script.model_copy(update={"script": tuple(sentences)})


def plan_premium_full_telling(
    script: Script,
    beat_sequence: BeatSequence,
    route: Route,
    *,
    stop_index: int,
    budget: FullTellingBudget,
    snapshot: CorpusSnapshot | None,
    snapshot_sha256: str,
    routing_version: str,
    policy_version: str,
    standalone_place: bool = False,
) -> PremiumTourPlan:
    """One major stop's FULL TELLING as a one-stop plan through THE seam.

    ``standalone_place`` plans a place recipe's full telling: the place told on its
    own, stored and later played inside walks it cannot see.

    FULL = a second COMPOSED piece from the same
    material — the stop's OVERFLOW beats — never the leftover corpus served raw.
    The mini source script holds the overflow sentences plus the stitch's own
    fallback close (the sentence the writer must rewrite, exactly as on the
    day's stops); the request carries the tight telling as ALREADY TOLD.
    ``beat_sequence`` must contain the overflow beats' bodies (the day's capped
    sequence does not — pass one whose POIBeats include them).
    """
    del budget  # sized at finalize; the plan itself carries the material
    poi = route.pois[stop_index]
    overflow_ids = beat_sequence.overflow_by_poi.get(poi.id, ())
    beats_by_id = {beat.id: beat for pb in beat_sequence.poi_beats for beat in pb.beats}
    overflow = [beats_by_id[bid] for bid in overflow_ids if bid in beats_by_id]
    if not overflow:
        # EXPLICIT, never inferred: the second story is exactly the overflow the
        # cap trimmed. Falling back to "all
        # this stop's beats" would re-compose the TIGHT material as the full
        # telling — the re-telling this rule exists to kill.
        raise ValueError(f"stop {stop_index} has no second story to author")
    mini_source = one_stop_source(
        script, overflow, poi_name=poi.name, stop_index=stop_index, n_stops=len(route.pois)
    )
    mini_seq = BeatSequence(
        poi_beats=(
            POIBeats(
                poi_id=poi.id,
                poi_name=poi.name,
                ordering_strategy="narrative_function",
                beats=tuple(overflow),
            ),
        )
    )
    tight_text = " ".join(s.text for s in script.script if s.stop_idx == stop_index)
    return plan_premium_authoring(
        mini_source,
        mini_seq,
        route,
        snapshot=snapshot,
        snapshot_sha256=snapshot_sha256,
        routing_version=routing_version,
        policy_version=policy_version,
        already_told_by_stop={stop_index: tight_text},
        single_stop=stop_index,
        standalone_place=standalone_place,
    )


def finalize_premium_full_telling(
    plan: PremiumTourPlan,
    responses: tuple[PhysicalProviderResponse, ...],
    *,
    budget: FullTellingBudget,
    faithfulness_checker: FaithfulnessChecker | None = None,
) -> FullTelling | None:
    """Verify and keep one full telling — or DROP AND REPORT it, never the day.

    The full telling is OPTIONAL enrichment, like a thread: a gate miss is
    recorded on the degradations channel (``full_telling_dropped``) and the stop
    simply keeps only its tight telling — the on-demand route still serves it.
    Gates, beyond the seam's own (traceability, entailment when checked, the
    authored close): the WORD BUDGET (<= 3x the tight, hard cap 12 min)
    and NO REPETITION of the tight telling (a re-telling is cut rather than served).
    """
    from .verify import _normalize_for_verbatim

    def drop(why: str) -> None:
        from .degradations import record

        record(
            kind=FULL_TELLING_DROPPED_DEGRADATION,
            human=(
                "The longer telling written for one of the main stops was dropped; "
                "the stop keeps its normal telling and the tap for more still works."
            ),
            component="premium_tour.finalize_premium_full_telling",
            cause=f"{why}. Never the leftover corpus, never a re-telling, "
            "never past the budget — the stop ships tight-only.",
        )

    try:
        composition = finalize_premium_composition(
            plan,
            responses,
            faithfulness_checker=faithfulness_checker,
            enforce_claim_coverage=True,
            scan_glue_for_invention=True,
            require_closes=True,
        )
    except (ValueError, ComposeVerificationError) as exc:
        drop(f"the seam refused it ({exc})")
        return None
    ordered = composition.script.script
    close = ordered[-1]
    body = [s for s in ordered[:-1]]
    words = sum(len(s.text.split()) for s in ordered)
    budget_words = round(budget.full_budget_seconds * _WORDS_PER_SECOND)
    if words > budget_words:
        drop(
            f"it runs {words} words against a budget of {budget_words} "
            f"({budget.full_budget_seconds} s at the engine's own reading rate)"
        )
        return None
    already_told = plan.units[0].authorized_request.already_told
    told = {_normalize_for_verbatim(t) for t in split_sentences(already_told)}
    for sentence in body:
        if _normalize_for_verbatim(sentence.text) in told:
            drop(f"it repeats the tight telling verbatim: {sentence.text!r}")
            return None
    return FullTelling(
        narration=" ".join(s.text for s in ordered),
        close_text=close.text,
        sentences=ordered,
    )


def finalize_premium_composition(
    plan: PremiumTourPlan,
    responses: tuple[PhysicalProviderResponse, ...],
    *,
    faithfulness_checker: FaithfulnessChecker | None = None,
    enforce_claim_coverage: bool = False,
    scan_glue_for_invention: bool = False,
    require_closes: bool = False,
    enforce_placement_floors: bool = False,
) -> CertificationComposition:
    """Purely bind physical response bytes and run the certification finalizer.

    THE GATES ARE FORWARDED, and the defaults are for the CERTIFICATION REPLAY
    ONLY (``scripts/tour_batch_candidate.py``), whose judgement of prose is semantic and
    owned by factual review. Every LIVE surface passes every gate through
    ``finalize_live_composition``, which requires the checker and turns each gate on.

    With the defaults and no checker, nothing checks entailment, claim coverage or
    invented names and years: ``authoring.py`` allows unverified faithfulness and
    ``compose_gate.py`` substitutes ``MockFaithfulnessChecker``, which approves every
    sentence. A live surface that calls this function directly would therefore serve
    narration nothing has checked.
    """

    if len(responses) != len(plan.units):
        raise ValueError("physical responses differ from the planned stop count")
    authoring_responses: list[AuthoringStopResponse] = []
    completed: list[CompletedCertificationComposeUnit] = []
    for unit, response in zip(plan.units, responses, strict=True):
        if response.model != COMPOSE_MODEL:
            raise ValueError("provider response model differs from the authorized model")
        if response.stop_reason == "max_tokens":
            raise ValueError("provider response hit the fixed output ceiling")
        try:
            payload = json.loads(response.body)
            raw_sentences = payload["sentences"]
            if not isinstance(raw_sentences, list):
                raise TypeError("sentences is not a list")
            sentences = _sentences_from_json(raw_sentences, unit.authorized_request)
            threads = _threads_from_json(payload)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError("provider response is not a valid Premium sentence payload") from exc
        parsed_sha256 = sentences_payload_sha256(sentences)
        response_sha256 = bytes_sha256(response.body)
        authoring_responses.append(
            AuthoringStopResponse(
                request=unit.authoring_request,
                raw_response=response.body,
                raw_response_sha256=response_sha256,
                parsed_payload_sha256=parsed_sha256,
                provider_request_id=response.provider_request_id,
            )
        )
        completed.append(
            CompletedCertificationComposeUnit(
                unit_id=f"stop:{unit.stop_index}",
                stop_index=unit.stop_index,
                model=response.model,
                authorized_request=unit.authorized_request,
                authoring_request=unit.authoring_request,
                parsed_provider_sentences=sentences,
                request_sha256=unit.request_sha256,
                response_sha256=response_sha256,
                parsed_payload_sha256=parsed_sha256,
                parsed_threads=threads,
            )
        )
    AuthoringCandidateResponseSet(
        plan=plan.authoring,
        responses=tuple(authoring_responses),
    )
    return finalize_certification_composition(
        plan.source,
        plan.sequence,
        plan.route,
        # A FULL-TELLING plan's requests carry the tight telling;
        # the replay must rebuild them with it or the grounded-source equality
        # check refuses its own plan. Derived from the plan itself — empty on a
        # day plan, byte-identical behaviour.
        already_told_by_stop={
            unit.stop_index: unit.authorized_request.already_told
            for unit in plan.units
            if unit.authorized_request.already_told
        }
        or None,
        standalone_place=plan.standalone_place,
        completed_units=tuple(completed),
        model=COMPOSE_MODEL,
        faithfulness_checker=faithfulness_checker,
        enforce_claim_coverage=enforce_claim_coverage,
        scan_glue_for_invention=scan_glue_for_invention,
        require_closes=require_closes,
        enforce_placement_floors=enforce_placement_floors,
    )


def finalize_live_composition(
    plan: PremiumTourPlan,
    responses: tuple[PhysicalProviderResponse, ...],
    *,
    faithfulness_checker: FaithfulnessChecker,
) -> CertificationComposition:
    """THE gate set for anything a listener hears: a served tour and a stored place
    recipe. Every gate is on, and the checker has no default, so no caller can ship
    narration that skipped one."""

    return finalize_premium_composition(
        plan,
        responses,
        faithfulness_checker=faithfulness_checker,
        enforce_claim_coverage=True,
        scan_glue_for_invention=True,
        # Every stop ends on its close; the certification replay's
        # default stays OFF — the sealed candidates predate it.
        require_closes=True,
        # Every sentence must be true WHERE IT PLAYS; the certification replay's
        # default stays OFF — the sealed candidates predate the rulings.
        enforce_placement_floors=True,
    )


@dataclass(frozen=True)
class PremiumBuildIdentity:
    commit_sha: str
    tts_provider: str = "openai"
    tts_model: str = OpenAITTSProvider.DEFAULT_MODEL
    tts_voice: str = OpenAITTSProvider.DEFAULT_VOICE
    # True for a LOCAL build authored off a dirty tree. commit_sha then names HEAD,
    # which the working tree does NOT match, so this build is reproducible only by
    # whoever ran it. WHAT READS THIS: the server's WARNING log line, and nothing
    # else — the BuildFingerprint on the blueprint carries only commit_sha, so a
    # reader of the blueprint cannot tell. Certification builds its own candidates through
    # scripts/tour_batch_candidate.py and never comes through this door; stamping
    # this flag onto the fingerprint would change the sealed blueprint hash of every
    # committed certification record, so whether it bites there is for
    # the re-seal to decide.
    local_dirty_tree: bool = False


def resolve_build_identity() -> PremiumBuildIdentity:
    """The commit this build came from: the deployment's commit on Render, else the
    local HEAD — tagged ``local_dirty_tree=True`` when the working tree has
    uncommitted changes, and said out loud in the server log (the blueprint's
    fingerprint carries only the commit — see `PremiumBuildIdentity`).

    A dirty tree is NEVER a refusal and needs no opt-in (2026-08-18). Until then a
    dirty local tree raised unless ``ONDOWAY_ALLOW_DIRTY_LOCAL_BUILD=1`` was set, and
    every entry point had to remember the flag — the workbench script and the test
    conftest did, ``make api`` and ``make flutter-ios`` did not — so a normal local
    run composed a 503 that read as a product fault. The class of that error is an
    environment condition disguised as a product failure; the fix is that the code
    decides from where it runs, not from a flag each caller must repeat. The deploy
    stays strict by construction: on Render the commit is the deployment's and git is
    never consulted.
    """

    deployed = os.getenv("RENDER_GIT_COMMIT") or os.getenv("GIT_COMMIT_SHA")
    if deployed:
        if not re.fullmatch(r"[0-9a-f]{40}", deployed):
            raise ValueError("deployment commit fingerprint is not a full lowercase SHA")
        return PremiumBuildIdentity(commit_sha=deployed)
    status = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ValueError("local commit fingerprint is not a full lowercase SHA")
    if status.strip():
        logging.getLogger("ondoway.api").warning(
            "Premium build authored from a DIRTY local tree at %s. This build is not "
            "reproducible from the commit and is not certifiable.",
            head,
        )
        return PremiumBuildIdentity(commit_sha=head, local_dirty_tree=True)
    return PremiumBuildIdentity(commit_sha=head)


@dataclass(frozen=True)
class PremiumTourResult:
    blueprint: FinalTourBlueprint
    candidate: AuthoringCandidateIdentity
    #: The writer's THREADS by stop index, then by the name
    #: of the stop that may come right before it — beside the script, never inside a
    #: stop's narration (they play only when the session makes that pair consecutive).
    threads_by_stop: dict[int, dict[str, str]] = dataclasses.field(default_factory=dict)


def finalize_premium_tour(
    plan: PremiumTourPlan,
    responses: tuple[PhysicalProviderResponse, ...],
    *,
    faithfulness_checker: FaithfulnessChecker,
    build_identity: PremiumBuildIdentity | None = None,
) -> PremiumTourResult:
    """Pure finalization into a validated, certification-eligible blueprint.

    This is the finalizer for a written walking tour: ``POST /trips/{trip_id}/compose``
    calls it for the day's stops and for each standby's piece. A stored place recipe
    has no walk and no blueprint, so the recipe build calls
    ``finalize_live_composition`` directly and passes the same gates.

    ``faithfulness_checker`` is REQUIRED and has NO default. An optional checker lets
    a caller skip the fact checks by never mentioning them, and an omission is invisible
    to code review. A caller that forgets fails loudly at the call, not quietly in the
    output.

    The gates are NOT parameters here for the same reason: a live surface does not
    get to choose whether facts are checked. ``finalize_live_composition`` holds them.
    """

    composition = finalize_live_composition(
        plan, responses, faithfulness_checker=faithfulness_checker
    )
    identity = build_identity or resolve_build_identity()
    vignette_beat_ids = frozenset(
        beat.id for beats in plan.sequence.vignette_beats.values() for beat in beats
    )
    # The walk's own corpus narration: a transit-class beat's sentences
    # freeze as leg playback, so the phone plays them while walking.
    transit_beat_ids = transit_class_beat_ids(
        beat for pb in plan.sequence.poi_beats for beat in pb.beats
    )
    source_assignments = derive_playback_assignments(
        plan.source,
        vignette_beat_ids=vignette_beat_ids,
        transit_beat_ids=transit_beat_ids,
    )
    # The composer legitimately emits a transition sentence the frozen stitch did not
    # contain. Placement for a RECOGNISED source_id is a pure function of that id, so
    # it is derived rather than discarding the whole authored tour; an unrecognised or
    # invented id still fails closed. Certification never reaches this function (it
    # calls finalize_premium_composition directly), so the strict default stands there.
    assignments = remap_provider_playback_assignments(
        source_script=plan.source,
        source_assignments=source_assignments,
        provider_script=composition.script,
        derivable_leg_source_ids=CONCURRENT_GLUE_LABELS | vignette_beat_ids | transit_beat_ids,
    )
    build = BuildFingerprint(
        commit_sha=identity.commit_sha,
        corpus_sha256=plan.snapshot_sha256,
        module_version=PREMIUM_MODULE_VERSION,
        prompt_sha256=premium_authoring_policy_sha256(),
        compose_model=COMPOSE_MODEL,
        policy_version=plan.policy_version,
        routing_engine="valhalla",
        routing_version=plan.routing_version,
        # THE ROUTING IDENTITY THE LEGS WERE ROUTED UNDER. A day with a
        # route-surface axis (take-it-easy step-free, family no-stairs)
        # is routed under that surface's costing and every leg receipt
        # carries THAT config hash; stamping the default here made
        # `FinalTourBlueprint` refuse every such day ("Valhalla receipt routing
        # configuration differs from build fingerprint" — measured 2026-08-19 on
        # a step-free day, three attempts). "any" is the default hash, byte-identical.
        routing_config_sha256=VALHALLA_ROUTING_CONFIG_SHA256_BY_SURFACE[
            plan.tour_input.route_surface
        ],
        tts_provider=identity.tts_provider,
        tts_model=identity.tts_model,
        tts_voice=identity.tts_voice,
    )
    vignette_ids = tuple(
        beat.id for beats in plan.sequence.vignette_beats.values() for beat in beats
    )
    blueprint = build_final_blueprint(
        contract_sha256=plan.authorities.contract_sha256,
        reference_manifest_sha256=plan.authorities.reference_manifest_sha256,
        calibration_manifest_sha256=plan.authorities.calibration_manifest_sha256,
        build=build,
        tour_input=plan.tour_input,
        route=plan.route,
        script=composition.script,
        vignette_beat_ids=vignette_ids,
        playback_assignments=assignments,
        composition_trace=composition.composition_trace,
    )
    ineligibility = validate_llm_composed_blueprint(blueprint)
    if ineligibility is not None:
        raise ValueError(ineligibility)
    return PremiumTourResult(
        blueprint=blueprint,
        candidate=plan.candidate,
        threads_by_stop=composition.threads_by_stop,
    )


__all__ = [
    "ESTIMATED_LEGS_DEGRADATION",
    "ROUTING_CONFIG_DEGRADATION",
    "ROUTING_VERSION_DEGRADATION",
    "ROUTING_VERSION_UNAVAILABLE",
    "AnthropicPremiumExecutor",
    "EphemeralReceiptSink",
    "OfflinePremiumExecutor",
    "PremiumBuildIdentity",
    "PremiumComposeExecutor",
    "PremiumComposeUnit",
    "PremiumReceiptSink",
    "PremiumRouteInfeasibleError",
    "PremiumTourPlan",
    "PremiumTourResult",
    "certification_planning_policy",
    "exact_snapshot_sha256",
    "execute_premium_plan",
    "finalize_live_composition",
    "finalize_premium_composition",
    "finalize_premium_tour",
    "full_telling_budget",
    "full_telling_sequence",
    "one_stop_source",
    "plan_premium_authoring",
    "plan_premium_tour",
    "premium_authoring_policy_sha256",
    "record_routing_degradations",
    "resolve_build_identity",
    "resolve_routing_version",
    "route_summary",
]
