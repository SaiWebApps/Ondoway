"""The AUTHOR block (Block 2) never re-plans a route.

A persisted trip already HAS its route: /trips/{id}/compose must author that exact
route, never re-plan one. There is now exactly ONE seam that does this —
``plan_premium_authoring`` -> ``execute_premium_plan`` -> ``finalize_premium_composition``
in ``src/tour/premium_tour.py``. The second implementation that used to live in
``src/tour/authoring.py`` was deleted on 2026-08-04; these guards moved with the claim.

The four properties the AUTHOR block is defined by:

* it never reaches the planner — proven STRUCTURALLY as well as at runtime, because a
  module-level ``from .selection import select_route`` binds at import time and a
  monkeypatch on the ``selection`` module can no longer see it. The structural guard is
  **FUNCTION-SCOPED**: ``premium_tour.py`` as a whole legitimately imports the planner for
  ``plan_premium_tour`` (which is BLOCK 1, and whose job IS to plan), so a module-scope
  check here would be vacuously red forever. Only the four Block-2 function bodies are
  read, and a function-local import inside one of them is exactly the shape that check
  exists to catch;
* it builds exactly ONE authoring unit per dwell stop — not one per sentence, and
  never zero for a stop the stitched script forgot — so a caller's spend precheck
  can reserve ``len(units)`` calls and be right;
* it tolerates what real persisted trips actually contain — a round-trip transit
  shape (``len(transits) == len(pois) + 1``), 1..15 stops, and receiptless
  haversine-degraded legs, all of which ``plan_premium_tour``'s PLANNING bars refuse
  outright. Those bars live in Block 1 and are not part of Block 2.

``test_the_author_block_never_replans`` is the pinned gate for this step, so it
carries every one of those clauses itself: reverting ANY of them must turn that one node
id red.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from src.tour import selection
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
from src.tour.premium_tour import (
    EphemeralReceiptSink,
    execute_premium_plan,
    finalize_premium_composition,
    plan_premium_authoring,
)

_SEAM_PATH = Path(__file__).resolve().parents[1] / "src" / "tour" / "premium_tour.py"

#: The Block-2 functions. Everything the AUTHOR block is allowed to be, by name.
#: The module as a whole legitimately imports the planner for ``plan_premium_tour``,
#: so a module-scope guard here would be vacuously red. The prohibition is therefore
#: FUNCTION-SCOPED: these bodies, and nothing else.
_BLOCK_TWO_FUNCTIONS = frozenset(
    {
        "plan_premium_authoring",
        "execute_premium_plan",
        "finalize_premium_composition",
        "finalize_premium_tour",
    }
)

#: Modules that PLAN a route. The seam authors a route it is handed; if any of
#: these appears anywhere in its import graph — including inside a function body —
#: the seam has grown a way to build a different route than the one persisted.
_PLANNING_MODULES = frozenset(
    {
        "selection",
        "premium_tour",
        "routing",
        "routing_client",
        "ordering",
        "beat_select",
        "density",
        "options",
    }
)

#: Planning entry points, by the name they are CALLED under (bare or attribute).
#: (select_k_routes left the set when the product stopped offering three routes.)
_PLANNING_CALLS = frozenset(
    {
        "choose_discrete_route",
        "plan_premium_tour",
        "certification_planning_policy",
        "route_planning_budget",
        "summarise_route",
        "insertion_cost_seconds",
        "generate",
    }
)


def _block_two_import_and_call_graph() -> tuple[set[str], set[str]]:
    """Every name the Block-2 function BODIES import and call, parsed — not matched.

    ``ast`` is used deliberately: a text search over the file would silently return
    an empty match on any shape it did not anticipate and the guard would pass by
    accident. Nested function definitions inside a Block-2 body (``invoke`` inside
    ``execute_premium_plan``) are walked too, because that is where a fan-out worker
    would reach a planner from.
    """
    tree = ast.parse(_SEAM_PATH.read_text(encoding="utf-8"), filename=str(_SEAM_PATH))
    bodies = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name in _BLOCK_TWO_FUNCTIONS
    ]
    assert {node.name for node in bodies} == _BLOCK_TWO_FUNCTIONS, (
        "a Block-2 function was renamed or removed, so this guard now watches less "
        f"than it claims: found {sorted(node.name for node in bodies)}"
    )
    imported: set[str] = set()
    called: set[str] = set()
    for body in bodies:
        for node in ast.walk(body):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.update(alias.name.split("."))
            elif isinstance(node, ast.ImportFrom):
                imported.update((node.module or "").split("."))
                for alias in node.names:
                    imported.add(alias.name)
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    called.add(func.id)
                elif isinstance(func, ast.Attribute):
                    called.add(func.attr)
    return imported, called


def _assert_the_seam_cannot_reach_the_planner() -> None:
    imported, called = _block_two_import_and_call_graph()
    assert not imported & _PLANNING_MODULES, (
        "a Block-2 function imports a route-PLANNING module: "
        f"{sorted(imported & _PLANNING_MODULES)}"
    )
    assert not called & _PLANNING_CALLS, (
        "a Block-2 function calls a route-PLANNING entry point: "
        f"{sorted(called & _PLANNING_CALLS)}"
    )


class CountingOfflineExecutor:
    """$0 executor that echoes the stitch back and records every call."""

    cost_bearing = False
    provider_name = "offline"

    def __init__(self) -> None:
        self.stop_calls: list[int] = []

    def execute(self, unit) -> PhysicalProviderResponse:
        self.stop_calls.append(unit.stop_index)
        body = json.dumps(
            {
                "sentences": [
                    sentence.model_dump(mode="json")
                    for sentence in unit.authorized_request.stitched.script
                ]
            },
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
            provider_request_id=f"offline-{unit.stop_index}",
            stop_reason="end_turn",
        )


def _prebuilt(
    stop_count: int,
    *,
    round_trip: bool = False,
    routed: bool = True,
    sentences_per_stop: int = 1,
) -> tuple[Script, BeatSequence, Route]:
    """A persisted-trip-shaped (route, sequence, stitched script) triple.

    ``routed=False`` reproduces the haversine-degraded legs a trip persists when
    Valhalla was unavailable: no ``leg_seconds``, no receipt anywhere.
    ``sentences_per_stop > 1`` gives a stop several sentences, which is what makes
    "one unit per DWELL STOP" a different claim from "one unit per sentence".
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
    leg_count = stop_count + 1 if round_trip else stop_count
    transits = tuple(
        TransitSegment(
            from_poi_id=None if index == 0 else pois[index - 1].id,
            to_poi_id=pois[index].id if index < stop_count else pois[0].id,
            distance_m=120.0,
            walk_seconds=100,
            leg_seconds=110 if routed else None,
            leg_distance_m=130.0 if routed else None,
            source="haversine",
        )
        for index in range(leg_count)
    )
    route = Route(
        pois=pois,
        transits=transits,
        total_walk_distance_m=120.0 * leg_count,
        total_walk_seconds=100 * leg_count,
    )
    beats_by_stop = {
        index: tuple(
            BeatRef(
                id=f"b{index}_{part}",
                poi_id=f"p{index}",
                script_body=(
                    f"The stone here was cut in the year of the flood, "
                    f"stop {index} part {part}."
                ),
                key_claims=(f"stop {index} part {part} has cut stone",),
            )
            for part in range(sentences_per_stop)
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
        total_walking_seconds=100 * leg_count,
        total_walk_distance_m=120.0 * leg_count,
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


def _drop_stop(stitched: Script, stop_index: int) -> Script:
    """The stitched script a dwell stop fell out of — no sentence carries it."""
    return stitched.model_copy(
        update={
            "script": tuple(
                sentence for sentence in stitched.script if sentence.stop_idx != stop_index
            )
        }
    )


def _plan(stitched: Script, sequence: BeatSequence, route: Route):
    return plan_premium_authoring(
        stitched,
        sequence,
        route,
        snapshot=None,
        snapshot_sha256="0" * 64,
        routing_version="offline-test",
        policy_version="offline-test",
    )


def _author(plan, executor) -> Script:
    """Block 2's execute-then-finalize, returning the Script the old seam returned.

    ``finalize_premium_composition`` rather than ``finalize_premium_tour``: the
    latter builds a certification blueprint and resolves a git build fingerprint,
    neither of which this file's claim (the AUTHOR block never re-plans) involves.
    """
    responses = execute_premium_plan(plan, executor=executor, receipt_sink=EphemeralReceiptSink())
    return finalize_premium_composition(plan, responses).script


def _forbid_planning(monkeypatch: pytest.MonkeyPatch) -> None:
    def _explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("the prebuilt-route seam must never re-plan a route")

    # select_k_routes is gone; select_route is the planner.
    monkeypatch.setattr(selection, "select_route", _explode)
    monkeypatch.setattr(selection, "choose_discrete_route", _explode)


def test_the_author_block_never_replans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end — the pinned gate for this step.

    Every clause lives here on purpose: reverting the no-replanning boundary, the
    one-unit-per-dwell-stop rule, the round-trip tolerance, the 15-stop cap or the
    receiptless tolerance each turns THIS node id red.

    The structural half is FUNCTION-SCOPED over ``src/tour/premium_tour.py``: the four
    Block-2 bodies, not the module, because the module also holds Block 1's planner.
    """
    _forbid_planning(monkeypatch)
    _assert_the_seam_cannot_reach_the_planner()

    # --- a round-trip route, several sentences per stop -------------------
    stitched, sequence, route = _prebuilt(4, round_trip=True, sentences_per_stop=2)
    assert len(route.transits) == len(route.pois) + 1
    assert len(stitched.script) == 2 * len(route.pois)

    plan = _plan(stitched, sequence, route)
    # ONE unit per dwell stop — not one per sentence, and not one fewer.
    assert len(plan.units) == len(route.pois)
    assert [unit.stop_index for unit in plan.units] == [0, 1, 2, 3]
    assert [unit.poi_name for unit in plan.units] == [poi.name for poi in route.pois]
    assert len(plan.authoring.stop_requests) == len(route.pois)
    # No re-planning: the seam authors the route it was handed, byte-identically.
    assert plan.route is route

    executor = CountingOfflineExecutor()
    script = _author(plan, executor)

    # Exactly one call per dwell stop (the stops run in parallel, so order is
    # not part of the contract — the COUNT is what a spend precheck reserves).
    assert sorted(executor.stop_calls) == [0, 1, 2, 3]
    assert sorted({s.stop_idx for s in script.script}) == [0, 1, 2, 3]
    assert sorted(s.text for s in script.script) == sorted(s.text for s in stitched.script)

    # A dwell stop with no sentence is REFUSED, never silently unauthored: a
    # caller reserving len(units) calls would otherwise pay for 3 and persist a
    # 4-stop trip whose fourth stop was never written. The LAST stop is the one
    # that matters — a gap in the middle already fails the candidate plan's
    # "cover every stop once in order" validator, but a short tail runs clean
    # through it and just quietly authors fewer stops than the route has.
    with pytest.raises(ValueError, match="one authoring unit per dwell stop"):
        _plan(_drop_stop(stitched, len(route.pois) - 1), sequence, route)

    # --- 15 stops (the old pin refused 9+) --------------------------------
    wide_plan = _plan(*_prebuilt(15))
    assert len(wide_plan.units) == 15
    wide_executor = CountingOfflineExecutor()
    wide_script = _author(wide_plan, wide_executor)
    assert sorted(wide_executor.stop_calls) == list(range(15))
    assert len({s.stop_idx for s in wide_script.script}) == 15

    # --- receiptless, haversine-degraded legs -----------------------------
    bare = _prebuilt(3, routed=False)
    assert all(transit.valhalla_receipt is None for transit in bare[2].transits)
    assert all(transit.leg_seconds is None for transit in bare[2].transits)
    bare_executor = CountingOfflineExecutor()
    bare_script = _author(_plan(*bare), bare_executor)
    assert sorted(bare_executor.stop_calls) == [0, 1, 2]
    assert len(bare_script.script) == 3


def test_prebuilt_route_authors_fifteen_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    """1..15 stops — the old 8-stop pin refused 9+."""
    _forbid_planning(monkeypatch)
    stitched, sequence, route = _prebuilt(15)

    plan = _plan(stitched, sequence, route)
    assert len(plan.units) == 15

    executor = CountingOfflineExecutor()
    script = _author(plan, executor)
    assert sorted(executor.stop_calls) == list(range(15))
    assert len({s.stop_idx for s in script.script}) == 15


def test_prebuilt_route_authors_a_single_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    _forbid_planning(monkeypatch)
    stitched, sequence, route = _prebuilt(1)

    executor = CountingOfflineExecutor()
    script = _author(_plan(stitched, sequence, route), executor)
    assert sorted(executor.stop_calls) == [0]
    assert len(script.script) == 1


def test_prebuilt_route_authors_receiptless_haversine_legs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Authoring needs no Valhalla evidence; plan_premium_tour's receipt bar does."""
    _forbid_planning(monkeypatch)
    stitched, sequence, route = _prebuilt(3, routed=False)
    assert all(transit.valhalla_receipt is None for transit in route.transits)
    assert all(transit.leg_seconds is None for transit in route.transits)

    executor = CountingOfflineExecutor()
    script = _author(_plan(stitched, sequence, route), executor)
    assert sorted(executor.stop_calls) == [0, 1, 2]
    assert len(script.script) == 3


def test_prebuilt_route_refuses_a_stop_the_stitch_forgot() -> None:
    """Fewer stitched stops than dwell stops is a refusal, not a shorter plan."""
    stitched, sequence, route = _prebuilt(4)
    with pytest.raises(ValueError, match="one authoring unit per dwell stop"):
        _plan(_drop_stop(stitched, 3), sequence, route)


def test_prebuilt_route_refuses_a_gap_in_the_middle_of_the_stitch() -> None:
    """An interior gap is refused too, and now by the count guard first.

    Before the guard this case was caught downstream by the candidate plan's
    "cover every stop once in order" validator — the trailing-stop case above is
    the one that had nothing at all standing in front of it.
    """
    stitched, sequence, route = _prebuilt(4)
    with pytest.raises(ValueError, match="one authoring unit per dwell stop"):
        _plan(_drop_stop(stitched, 1), sequence, route)


def test_the_author_block_authors_past_the_deleted_fifteen_stop_ceiling() -> None:
    """INVERTED 2026-08-04 ("no stop limits, period").

    This test used to assert that a 16-stop route was REFUSED with "one to fifteen
    stops". That ceiling sat behind the planner and rejected routes the same engine
    had already built, so it was deleted along with every other stop cap; duration
    is now the only bound on how many stops a tour has. The claim is therefore
    inverted rather than dropped — 16 stops must author cleanly, one script per
    dwell stop, all the way through the seam.
    """
    stitched, sequence, route = _prebuilt(16)
    plan = _plan(stitched, sequence, route)
    assert len(plan.units) == 16

    executor = CountingOfflineExecutor()
    script = _author(plan, executor)
    assert sorted(executor.stop_calls) == list(range(16))
    assert len({s.stop_idx for s in script.script}) == 16
