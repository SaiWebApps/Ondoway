"""Both premium fan-out sites, pinned under FORCED concurrency.

The outage was traced to TWO call sites that shared the exact same shape — a
callable wrapped ONCE by ``in_current_context`` and handed to a thread pool.
There is now only ONE: ``src/tour/premium_tour.py execute_premium_plan``, which
BOTH the workbench's ``/trips/preview`` and the phone's ``/trips/{id}/compose``
reach, because the second implementation in ``src/tour/authoring.py`` was
deleted on 2026-08-04. The second-site assertions
went with it rather than being pointed at the surviving site twice: a duplicated
assertion is a fake guard, and there is genuinely nothing left to prove about a
function that no longer exists. A first attempt at this test fell into a
trap: a fan-out whose workers do not provably overlap passes for the
wrong reason, exactly the way ``tests/test_degradations.py``'s original thread
test did for three days while every real 2+-stop premium tour crashed 100% of
the time.

So this module asserts TWO things about the production fan-out, and both are
gated on proof the run was actually concurrent:

1. **It survives overlap.** Every worker parks INSIDE the wrapped call until all
   of its siblings have arrived, so N-way overlap is forced by construction and
   is not a hope about thread scheduling. ``shook_hands`` records, per worker,
   whether it was released by its siblings or merely timed out alone; a timed-out
   run is a FAILED run, never a green one. A ``threading.Barrier`` is deliberately
   avoided for the reason recorded in ``test_degradations.py``: under the bug one
   worker holds the shared ``Context`` forever while its siblings die trying to
   enter it, and an unbounded barrier would hang the suite instead of reporting.
2. **The caller still hears what happened inside it.** Each worker records a
   degradation from its pool thread, and the caller's ``degradation_scope``
   collector must contain one row per unit. This is what makes the test sensitive
   to the wrapper's PRESENCE at each call site rather than only to its internals.

MEASURED, and the reason point 2 exists: with only point 1 asserted, deleting
``in_current_context(`` from ``premium_tour.py`` left this file GREEN in 0.03s.
A guard nobody has turned red does not exist. The four mutations below were each
applied to the real source, run, and reverted:

  * ``premium_tour.py``: ``pool.map(invoke, ...)`` (wrapper dropped) → RED, the
    premium propagation assertion, naming ``execute_premium_plan``;
  * ``degradations.py``: ``snapshot.run`` in place of ``snapshot.copy().run`` (the
    production bug restored) → RED with ``RuntimeError: cannot enter context``;
  * ``degradations.py``: ``contextvars.copy_context().run`` inside ``_run`` (a
    rejected shape) → RED on both propagation assertions, because that copies
    the WORKER's empty context and silently drops the caller's collector.

What this file does NOT claim: the single-unit empty-list assertion is a canary, not a
guard. Nothing on the premium path calls ``record`` today, so no
mutation of the current tree turns it red; it exists so that a future degradation
recorded on a healthy single-stop tour — crying wolf — is caught the day it is
written.
"""

from __future__ import annotations

import json
import threading

import pytest

from src.tour.authoring import COMPOSE_MODEL
from src.tour.certification_provider import PhysicalProviderResponse
from src.tour.contract import (
    POI,
    BeatRef,
    BeatSequence,
    POIBeats,
    Route,
    Script,
    ScriptPOI,
    Sentence,
    TourInput,
    TransitSegment,
    ValidationReport,
)
from src.tour.degradations import degradation_scope, record
from src.tour.premium_tour import (
    EphemeralReceiptSink,
    execute_premium_plan,
    plan_premium_authoring,
)

#: Long enough that a healthy handshake never trips it, short enough that a
#: BROKEN wrapper (a worker that dies mid-context-entry and never releases its
#: siblings) reports a failure in seconds instead of hanging the suite.
_HANDSHAKE_TIMEOUT_SECONDS = 3.0


def _prebuilt_route(stop_count: int) -> tuple[Script, BeatSequence, Route]:
    """A minimal but real (route, sequence, stitched script) triple.

    Shaped like ``tests/test_tour_authoring_from_route.py``'s ``_prebuilt`` —
    one beat per stop is enough here; this module's claim is concurrency, not
    stitch-shape tolerance, which that file already pins.
    """
    pois = tuple(
        POI(
            id=f"p{index}",
            name=f"Stop {index}",
            tier=3,
            poi_role="anchor",
            lat=48.85 + index / 1000,
            lng=2.35 + index / 1000,
        )
        for index in range(stop_count)
    )
    transits = tuple(
        TransitSegment(
            from_poi_id=None if index == 0 else pois[index - 1].id,
            to_poi_id=pois[index].id,
            distance_m=120.0,
            walk_seconds=100,
            leg_seconds=110,
            leg_distance_m=130.0,
            source="haversine",
        )
        for index in range(stop_count)
    )
    route = Route(
        pois=pois,
        transits=transits,
        total_walk_distance_m=120.0 * stop_count,
        total_walk_seconds=100 * stop_count,
    )
    beats_by_stop = {
        index: (
            BeatRef(
                id=f"b{index}",
                poi_id=f"p{index}",
                script_body=f"The stone here was cut in the year of the flood, stop {index}.",
                key_claims=(f"stop {index} has cut stone",),
            ),
        )
        for index in range(stop_count)
    }
    sequence = BeatSequence(
        poi_beats=tuple(
            POIBeats(
                poi_id=f"p{index}",
                poi_name=f"Stop {index}",
                ordering_strategy="narrative_function",
                beats=beats_by_stop[index],
            )
            for index in range(stop_count)
        )
    )
    stitched = Script(
        city_slug="paris",
        generated_at="2026-07-30T00:00:00Z",
        inputs=TourInput(start=(48.85, 2.35), duration_min=60, city_slug="paris"),
        total_audio_seconds=stop_count * 12,
        total_walking_seconds=100 * stop_count,
        total_walk_distance_m=120.0 * stop_count,
        total_planned_seconds=3600,
        selected_pois=tuple(
            ScriptPOI(
                id=poi.id,
                name=poi.name,
                tier=poi.tier,
                lat=poi.lat,
                lng=poi.lng,
                beat_ids=tuple(beat.id for beat in beats_by_stop[index]),
            )
            for index, poi in enumerate(pois)
        ),
        lens_coverage={},
        script=tuple(
            Sentence(
                text=beat.script_body or "",
                source_id=beat.id,
                source_type="beat",
                stop_idx=index,
            )
            for index in range(stop_count)
            for beat in beats_by_stop[index]
        ),
        validation=ValidationReport(),
    )
    return stitched, sequence, route


class _OverlapForcingExecutor:
    """Offline echo executor whose workers are forced to overlap by construction.

    Every worker parks — INSIDE the call the fan-out wraps — until every other
    unit's worker has also arrived, then all are released at once. That is what
    makes the overlap provable rather than hoped for: under the original bug, a second
    worker trying to enter the same already-entered ``Context`` raises
    immediately, so genuine N-way arrival can only complete if the fix (a fresh
    context per call) is in place.

    Each worker then records a degradation FROM ITS POOL THREAD. That is the
    second half, and it is what makes this test sensitive to the wrapper being
    present at the production call site: an unwrapped pool worker cannot see the
    caller's collection scope, so its ``record`` is a silent no-op and the
    caller's list comes back short.

    The wait is bounded (``_HANDSHAKE_TIMEOUT_SECONDS``), never an unbounded
    ``threading.Barrier``: under the bug one worker can raise before ever
    reaching the rendezvous, and an unbounded barrier would then hang its
    siblings — and the whole suite — instead of reporting a failure.
    """

    cost_bearing = False
    provider_name = "offline"

    def __init__(self, unit_count: int, *, site: str, records: bool = True) -> None:
        self._unit_count = unit_count
        self._site = site
        self._records = records
        self._lock = threading.Lock()
        self._arrived = 0
        self._release = threading.Event()
        #: True only if this worker was actually released by the rendezvous
        #: (or was the one that completed it) rather than timing out alone.
        self.shook_hands: dict[int, bool] = {}

    def degradation_kind(self, stop_index: int) -> str:
        return f"{self._site}_worker_{stop_index}"

    def execute(self, unit: object) -> PhysicalProviderResponse:
        stop_index = unit.stop_index  # type: ignore[attr-defined]
        with self._lock:
            self._arrived += 1
            is_last = self._arrived == self._unit_count
        if is_last:
            self.shook_hands[stop_index] = True
            self._release.set()
        else:
            self.shook_hands[stop_index] = self._release.wait(timeout=_HANDSHAKE_TIMEOUT_SECONDS)
        if self._records:
            # Recorded from the POOL THREAD, while every sibling is also inside
            # the fan-out. Reaches the caller only through the wrapper at the
            # production call site.
            record(
                kind=self.degradation_kind(stop_index),
                human="A stop was narrated from a degraded source.",
                component=f"{self._site}.execute",
                stop_index=str(stop_index),
            )
        sentences = [
            sentence.model_dump(mode="json")
            for sentence in unit.authorized_request.stitched.script  # type: ignore[attr-defined]
        ]
        body = json.dumps(
            {"sentences": sentences},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return PhysicalProviderResponse(
            body=body,
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            model=COMPOSE_MODEL,
            provider_request_id=f"offline-{stop_index}",
            stop_reason="end_turn",
        )


def _assert_genuine_overlap(shook_hands: dict[int, bool], *, site: str) -> None:
    assert set(shook_hands) and all(shook_hands.values()), (
        f"{site}: the workers never provably overlapped (handshake result "
        f"{shook_hands}); at least one timed out after "
        f"{_HANDSHAKE_TIMEOUT_SECONDS}s instead of being released by its "
        f"siblings, so this run exercised no concurrency and proves nothing "
        f"about the fan-out's re-entrancy"
    )


def _assert_every_worker_reached_the_caller(
    collected: list, executor: _OverlapForcingExecutor, *, unit_count: int, site: str
) -> None:
    """Every pool worker's degradation must be in the CALLER's collector.

    This is the assertion that fails when ``in_current_context`` is missing from
    ``{site}``'s ``pool.map`` — an unwrapped worker records into no scope at all,
    so the tour reports itself clean while failing inside.
    """
    expected = {executor.degradation_kind(index) for index in range(unit_count)}
    seen = {degradation.kind for degradation in collected}
    assert expected <= seen, (
        f"{site}: {sorted(expected - seen)} recorded inside a pool worker never "
        f"reached the caller's degradation scope (saw {sorted(seen)}). The "
        f"fan-out's callable is not carrying the caller's collection context, so "
        f"a stop can fail inside {site} and the tour still reports itself clean"
    )


def test_both_fan_outs_run_units_concurrently_without_context_error() -> None:
    """Positive (3+ units, forced overlap) and negative (1 unit).

    Both fan-outs must survive N-way worker overlap AND still deliver what their
    workers recorded to the caller. See the module docstring for the four source
    mutations that were applied, run and reverted to prove each half red.
    """
    # --- Positive: three units, both fan-outs, forced N-way overlap -----------
    stitched, sequence, route = _prebuilt_route(3)
    premium_plan = plan_premium_authoring(
        stitched,
        sequence,
        route,
        snapshot=None,
        snapshot_sha256="0" * 64,
        routing_version="offline-test",
        policy_version="offline-test",
    )
    assert len(premium_plan.units) == 3

    premium_executor = _OverlapForcingExecutor(unit_count=3, site="execute_premium_plan")
    with degradation_scope() as premium_collected:
        try:
            premium_responses = execute_premium_plan(
                premium_plan,
                executor=premium_executor,
                receipt_sink=EphemeralReceiptSink(),
            )
        except RuntimeError as exc:  # pragma: no cover - failure path, not the fix
            raise AssertionError(
                f"execute_premium_plan raised a RuntimeError under forced worker "
                f"overlap ({exc!r}); this is the production outage reproduced "
                f"through its real on-ramp"
            ) from exc
    assert len(premium_responses) == 3, (
        f"execute_premium_plan returned {len(premium_responses)} responses for "
        f"3 units — one response per unit is the contract a caller's spend "
        f"precheck relies on"
    )
    _assert_genuine_overlap(premium_executor.shook_hands, site="execute_premium_plan")
    _assert_every_worker_reached_the_caller(
        premium_collected, premium_executor, unit_count=3, site="execute_premium_plan"
    )

    # --- Negative: a single-unit plan never overlapped and must keep
    # working, and must not start crying wolf. These executors record NOTHING,
    # so the collector stays EMPTY unless the tour path itself starts reporting
    # a degradation on a perfectly healthy single-stop run.
    solo_stitched, solo_sequence, solo_route = _prebuilt_route(1)
    solo_premium_plan = plan_premium_authoring(
        solo_stitched,
        solo_sequence,
        solo_route,
        snapshot=None,
        snapshot_sha256="0" * 64,
        routing_version="offline-test",
        policy_version="offline-test",
    )
    assert len(solo_premium_plan.units) == 1

    with degradation_scope() as solo_premium_collected:
        solo_premium_responses = execute_premium_plan(
            solo_premium_plan,
            executor=_OverlapForcingExecutor(
                unit_count=1, site="execute_premium_plan", records=False
            ),
            receipt_sink=EphemeralReceiptSink(),
        )
    assert len(solo_premium_responses) == 1
    assert solo_premium_collected == [], (
        f"a clean single-stop premium run recorded a degradation "
        f"({solo_premium_collected}) — single-stop tours worked throughout the "
        f"outage and must not start crying wolf now that concurrency is fixed"
    )


class _MarkerFailureError(RuntimeError):
    """A distinctive exception a test can find on the wire, not a real fault."""


class _FailingUnitExecutor:
    """Every unit succeeds except one, which raises INSIDE the fan-out.

    This executor's claim is narrower than the overlap tests above: it is not about
    concurrency, it is about what happens to what a worker throws. A caller
    must both learn what broke (a degradation naming the exception type,
    message and stop index, in ITS OWN scope) and have the tour actually stop
    — a partial premium tour must abort, never ship half-narrated. Today
    neither fan-out catches anything, so the exception already propagates;
    what this test pins is that it is also RECORDED before it does.
    """

    cost_bearing = False
    provider_name = "offline"

    def __init__(self, *, fail_stop_index: int) -> None:
        self._fail_stop_index = fail_stop_index

    def execute(self, unit: object) -> PhysicalProviderResponse:
        stop_index = unit.stop_index  # type: ignore[attr-defined]
        if stop_index == self._fail_stop_index:
            raise _MarkerFailureError(f"MARKER-unit-{stop_index}-failed")
        sentences = [
            sentence.model_dump(mode="json")
            for sentence in unit.authorized_request.stitched.script  # type: ignore[attr-defined]
        ]
        body = json.dumps(
            {"sentences": sentences},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return PhysicalProviderResponse(
            body=body,
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            model=COMPOSE_MODEL,
            provider_request_id=f"offline-{stop_index}",
            stop_reason="end_turn",
        )


def test_a_unit_failure_inside_a_fan_out_reaches_the_caller() -> None:
    """A unit failing inside either fan-out must be RECORDED (exception
    type, message, stop index — in the caller's own degradation scope) AND the
    exception must still be RE-RAISED, so a partial premium tour aborts rather
    than shipping half-narrated.
    """
    stitched, sequence, route = _prebuilt_route(3)
    premium_plan = plan_premium_authoring(
        stitched,
        sequence,
        route,
        snapshot=None,
        snapshot_sha256="0" * 64,
        routing_version="offline-test",
        policy_version="offline-test",
    )

    with (
        degradation_scope() as premium_collected,
        pytest.raises(_MarkerFailureError, match="MARKER-unit-1-failed"),
    ):
        execute_premium_plan(
            premium_plan,
            executor=_FailingUnitExecutor(fail_stop_index=1),
            receipt_sink=EphemeralReceiptSink(),
        )
    assert len(premium_collected) == 1, (
        f"execute_premium_plan: expected exactly one degradation for the failed "
        f"unit to reach the caller's scope, got {premium_collected}"
    )
    premium_degradation = premium_collected[0]
    assert premium_degradation.error_type == "_MarkerFailureError", premium_degradation
    assert "MARKER-unit-1-failed" in premium_degradation.error_message, premium_degradation
    assert premium_degradation.context.get("stop_index") == "1", premium_degradation
