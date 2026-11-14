"""Party tests — does the planner know WHO is walking?

The party tests live here: the contract axes and presets, the resolver, the
harness flags and per-stop table, the leg cap, pace, route surface, and the
escape radius.

The RED that proved the axes real: `TourInput` is `extra="forbid"`
(src/tour/contract.py), so constructing it with `party` / the axis fields
raised before the fields existed. That forbid is load-bearing for this file
and its only guard is
`tests/test_tour_contract.py::test_tour_input_rejects_extra_keys` — if that
guard ever dies, the RED here goes vacuous with it.
"""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.tour.contract import POI, Route, TourInput, TransitSegment


def _base_input(**overrides) -> TourInput:
    fields = {
        "start": (48.8568, 2.3414),
        "duration_min": 120,
        "city_slug": "paris",
    }
    fields.update(overrides)
    return TourInput(**fields)


def _resolved(**overrides) -> TourInput:
    from src.tour.contract import resolve_party_axes

    return resolve_party_axes(_base_input(**overrides))


# --- the axes and the presets ------------------------------------------------


def test_presets_are_shortcuts_over_axes_and_explicit_axes_win():
    """A preset is a SHORTCUT over the axes, and an explicitly-set axis always
    beats its preset's value.

    A party tap may set several axes at once; the presets are shortcuts over
    axes, and the axes are what the planner reads. A preset that could
    silently overwrite an axis the
    visitor set by hand would make the tap an override, not a shortcut.
    """
    kept_ceiling = _resolved(party="family", max_stop_minutes=10)
    assert kept_ceiling.max_stop_minutes == 10, "explicit ceiling must beat the preset's 6"
    assert kept_ceiling.walking_pace == 2.0, "unset axes still fill from the preset"

    kept_cap = _resolved(party="take_it_easy", max_leg_minutes=20)
    assert kept_cap.max_leg_minutes == 20, "explicit leg cap must beat the preset's 13"

    kept_surface = _resolved(party="family", route_surface="any")
    assert kept_surface.route_surface == "any", (
        "an explicitly-passed surface — even the default value — must beat the preset"
    )


def test_no_party_and_no_axes_is_todays_behaviour_byte_identical():
    """No party, no axes → nothing moves: every axis defaults to None (surface
    to "any"), and the resolver returns the input unchanged. The identity
    default is what keeps every existing caller and golden byte-identical."""
    from src.tour.contract import resolve_party_axes

    inp = _base_input()
    assert inp.party is None
    assert inp.walking_pace is None
    assert inp.max_leg_minutes is None
    assert inp.rest_cadence_minutes is None
    assert inp.escape_radius_m is None
    assert inp.route_surface == "any"
    assert inp.narration_register is None
    assert resolve_party_axes(inp) == inp


def test_solo_preset_sets_no_ceiling():
    """`solo` sets NO per-stop ceiling — a solo history walker's anchor can be
    65 minutes inside one building, and a preset stop ceiling under 65 minutes
    cuts the heart out of that day. A per-stop ceiling is never set by
    mobility.
    """
    resolved = _resolved(party="solo")
    assert resolved.max_stop_minutes is None
    assert resolved.narration_register == "solo"
    assert resolved.walking_pace is None, "solo walks at normal pace"


def test_couple_register_is_warm_never_romantic():
    """`couple` sets the register to warm — and never romantic. No mobility
    axis moves: a couple who would rather talk walks at normal pace and needs
    no caps."""
    resolved = _resolved(party="couple")
    assert resolved.narration_register == "warm"
    assert resolved.walking_pace is None
    assert resolved.max_leg_minutes is None
    assert resolved.max_stop_minutes is None


def test_family_preset_matches_a_family_day():
    """`family` sets the ceiling ~6 min, half pace, the rest cadence, the
    escape radius, no-stairs surface, and the family register.

    On a family day six minutes is the ceiling at a stop, not the floor; pace
    drops to roughly half; the toilet stop is non-negotiable; and the distance
    from the exit is capped, not just the leg length, so family days are tight
    loops around a short escape radius.
    """
    resolved = _resolved(party="family")
    assert resolved.max_stop_minutes == 6
    assert resolved.walking_pace == 2.0
    assert resolved.rest_cadence_minutes is not None
    assert resolved.escape_radius_m is not None
    assert resolved.route_surface == "no_stairs"
    assert resolved.narration_register == "family"


def test_take_it_easy_slows_the_walking_never_the_talking():
    """`take-it-easy` caps the leg at 13 minutes, goes step-free, slows the
    pace, keeps the rest cadence — and leaves the register ALONE.

    A step-free visitor's per-leg limit is the binding constraint, and the
    request carries it as a cap. Register never follows mobility: slow the
    walking, never the talking. Take-it-easy gets the rest cadence as well as
    family: a visitor who needs frequent rests sits on benches.
    """
    resolved = _resolved(party="take_it_easy")
    # Owner decision, 2026-08-19: 12 produced a one-stop day on a step-free
    # visitor's own ground; her doctor's measured number is 13, the cap every
    # good demo day already uses. RE-DERIVED from 12 with that written reason.
    assert resolved.max_leg_minutes == 13
    assert resolved.route_surface == "step_free"
    assert resolved.walking_pace is not None and resolved.walking_pace > 1.0
    assert resolved.rest_cadence_minutes is not None
    assert resolved.narration_register is None, "slow the walking, never the talking"
    assert resolved.max_stop_minutes is None, (
        "no ceiling: a step-free visitor's centrepiece is a 46-minute sit; "
        "a per-stop ceiling is never set by mobility"
    )


def test_with_luggage_slows_and_keeps_stairs_off_the_route():
    """`with-luggage` slows the pace and avoids stairs (and cobbles, which
    ride the route-surface costing): a traveller with wheeled luggage walks
    slower than normal, and the wheels make cobbles unpleasant. Luggage
    changes the route surface. Register stays solo; no ceiling."""
    resolved = _resolved(party="with_luggage")
    assert resolved.walking_pace is not None and 1.0 < resolved.walking_pace < 2.0
    assert resolved.route_surface == "no_stairs"
    assert resolved.narration_register == "solo"
    assert resolved.max_stop_minutes is None


def test_walking_pace_below_one_is_rejected():
    """The pace multiplier's fast direction is LOCKED: a multiplier below 1.0
    would re-open the raised-and-held-back `PACE_KMH` pin from the slow side.
    Slow is a fact about real walkers; fast is a lie about the city."""
    with pytest.raises(ValidationError):
        _base_input(walking_pace=0.8)


def test_party_axes_round_trip_through_model_dump():
    """`TourInput(**inp.model_dump())` must survive the new fields — the same
    round-trip the clock fields honour (test_tour_clock.py), because persisted
    tour_input_json rebuilds the model from a plain dict."""
    inp = _base_input(party="family", max_leg_minutes=9, route_surface="no_stairs")
    again = TourInput(**inp.model_dump())
    assert again == inp


# --- the harness speaks party, and shows a DAY --------------------------------


def test_party_flags_parse_and_flagless_parse_carries_no_party():
    """`scripts/tour_build.py` accepts --party and the six axis flags, and a
    flagless parse carries no party and no axes — the identity default that
    keeps a legacy invocation byte-identical. The flags are what let the
    harness build visibly different days side by side."""
    import importlib

    tour_build = importlib.import_module("scripts.tour_build")
    parser = tour_build._build_arg_parser()

    flagged = parser.parse_args(
        [
            "--start", "X", "--duration", "60", "--canned",
            "--party", "take-it-easy",
            "--max-stop-minutes", "8",
            "--max-leg-minutes", "10",
            "--walking-pace", "1.5",
            "--rest-cadence-minutes", "25",
            "--escape-radius-m", "600",
            "--route-surface", "step_free",
        ]
    )
    assert flagged.party == "take-it-easy"
    assert flagged.max_stop_minutes == 8
    assert flagged.max_leg_minutes == 10
    assert flagged.walking_pace == 1.5
    assert flagged.rest_cadence_minutes == 25
    assert flagged.escape_radius_m == 600
    assert flagged.route_surface == "step_free"

    flagless = parser.parse_args(["--start", "X", "--duration", "60", "--canned"])
    assert flagless.party is None
    assert flagless.max_stop_minutes is None
    assert flagless.max_leg_minutes is None
    assert flagless.walking_pace is None
    assert flagless.rest_cadence_minutes is None
    assert flagless.escape_radius_m is None
    assert flagless.route_surface is None


def _priced_route() -> tuple[Route, SimpleNamespace]:
    """A hermetic two-stop priced route + a script stand-in for the printer."""
    a = POI(
        id="poi-a", name="Jardin des Tuileries", tier=4, poi_role="stop",
        lat=48.8635, lng=2.3275, place_category="garden",
    )
    b = POI(
        id="poi-b", name="Musee d'Orsay", tier=5, poi_role="stop",
        lat=48.8600, lng=2.3266, place_category="museum",
    )
    route = Route(
        pois=(a, b),
        transits=(
            TransitSegment(
                from_poi_id=None, to_poi_id="poi-a", distance_m=600.0, walk_seconds=540
            ),
            TransitSegment(
                from_poi_id="poi-a", to_poi_id="poi-b", distance_m=800.0, walk_seconds=720
            ),
        ),
        total_walk_distance_m=1400.0,
        total_walk_seconds=1260,
        planned_visit_seconds={"poi-a": 1080, "poi-b": 1500},
    )
    script = SimpleNamespace(
        selected_pois=[
            SimpleNamespace(dwell_seconds=1080, lat=a.lat, lng=a.lng, name=a.name),
            SimpleNamespace(dwell_seconds=1500, lat=b.lat, lng=b.lng, name=b.name),
        ]
    )
    return route, script


def test_breakdown_prints_one_line_per_stop_with_category_and_minutes(capsys):
    """The breakdown shows a DAY, not a total: one line per stop — name,
    place category, stand/visit minutes, and the walking leg INTO that stop.

    The side-by-side table of different days is assembled from these lines
    verbatim, and the harness printout carries the category labels, not only
    the review tables.
    """
    import importlib

    tour_build = importlib.import_module("scripts.tour_build")
    route, script = _priced_route()

    tour_build._print_breakdown(
        tour_input=_base_input(), wall_clock_s=1.0, route=route, script=script
    )
    out = capsys.readouterr().out

    assert "per-stop" in out, "the per-stop table must exist"
    table = out.split("per-stop", 1)[1]
    lines = [ln for ln in table.splitlines() if "Jardin des Tuileries" in ln]
    assert lines, "the per-stop table must carry one line per stop"
    tuileries = lines[0]
    assert "garden" in tuileries, "the line carries the place category"
    assert "18" in tuileries, "the line carries the stand/visit minutes (1080s)"
    assert "9" in tuileries, "the line carries the walk-in leg minutes (540s)"

    orsay = [ln for ln in table.splitlines() if "Musee d'Orsay" in ln]
    assert orsay and "museum" in orsay[0] and "25" in orsay[0] and "12" in orsay[0]


def test_breakdown_prints_dash_when_route_carries_no_pricing(capsys):
    """A legacy-shape route (empty `planned_visit_seconds` — the four legacy
    harnesses' shape, per the field's own comment in contract.py) prints the
    SAME table with a dash in the visit column, never a different shape and
    never a zero that reads as a measurement."""
    import importlib

    tour_build = importlib.import_module("scripts.tour_build")
    route, script = _priced_route()
    unpriced = route.model_copy(update={"planned_visit_seconds": {}})

    tour_build._print_breakdown(
        tour_input=_base_input(), wall_clock_s=1.0, route=unpriced, script=script
    )
    out = capsys.readouterr().out
    assert "per-stop" in out, "the table prints even when the route carries no pricing"
    table = out.split("per-stop", 1)[1]
    tuileries = [ln for ln in table.splitlines() if "Jardin des Tuileries" in ln]
    assert tuileries, "the table carries the stop even when the route is unpriced"
    assert "—" in tuileries[0], "unpriced visit minutes print as a dash, not a zero"


# --- pace reaches the clock, in the slow direction ----------------------------


def test_pace_multiplier_of_one_is_byte_identical():
    """The identity default: 1.0 is the same arithmetic as before this
    parameter existed, in both the seconds calculation and the reach circle
    (multiplier unset/1.0 = today)."""
    from src.tour.routing import envelope_radius_m, pace_corrected_walk_seconds

    assert pace_corrected_walk_seconds(1000.0) == pace_corrected_walk_seconds(
        1000.0, pace_multiplier=1.0
    )
    assert envelope_radius_m(120, round_trip=False) == envelope_radius_m(
        120, round_trip=False, pace_multiplier=1.0
    )


def test_half_pace_doubles_legs_and_halves_the_circle():
    """A multiplier of 2.0 doubles a leg's seconds and halves the envelope
    radius — a family's pace drops to roughly half, and the with-luggage
    preset has its own slow multiplier."""
    from src.tour.routing import envelope_radius_m, pace_corrected_walk_seconds

    normal_seconds = pace_corrected_walk_seconds(1000.0)
    slow_seconds = pace_corrected_walk_seconds(1000.0, pace_multiplier=2.0)
    assert slow_seconds == 2 * normal_seconds

    normal_radius = envelope_radius_m(120, round_trip=False)
    slow_radius = envelope_radius_m(120, round_trip=False, pace_multiplier=2.0)
    assert slow_radius == pytest.approx(normal_radius / 2.0)


def test_density_assessment_for_a_paced_request_reports_the_smaller_walk_radius():
    """The tourability gate and the planner must shrink together
    (`density.assess` computes its own `envelope_radius_m`,
    so pace has to thread there too or the gate offers a circle the planner
    will refuse to walk). Same corpus, same request, only `walking_pace`
    differs — the paced assessment's `walk_radius_m` is exactly half."""
    from src.tour.density import assess_snapshot
    from tests.test_tour_selection import PDV, _density_fillers, _snap

    corpus = _snap(_density_fillers(PDV, duration_min=120))
    normal = TourInput(start=PDV, duration_min=120, city_slug="paris")
    paced = TourInput(start=PDV, duration_min=120, city_slug="paris", walking_pace=2.0)

    normal_assessment = assess_snapshot(normal, corpus)
    paced_assessment = assess_snapshot(paced, corpus)
    assert paced_assessment.walk_radius_m == pytest.approx(
        normal_assessment.walk_radius_m / 2.0
    )


def test_select_route_prices_a_slower_partys_legs_when_seating_stops():
    """Not just the arithmetic in isolation — `select_route` ACTUALLY uses
    the scaled leg cost when deciding what fits. Proof: a rich anchor a
    normal-pace round trip affords (worth the there-and-back walk) a
    half-pace round trip cannot, because every leg toward it now costs twice
    as long against the SAME walk budget. A half-pace family both walks
    slower and is offered a smaller world."""
    from src.tour.selection import select_route
    from tests.test_tour_selection import PDV, _density_fillers, _poi, _snap

    # RE-DERIVED for the three-minute tight: six fillers filled an
    # hour at 270 s a stop; at 180 the same pool is "far short" and the day
    # refuses before the pace question is even asked. n=None derives the pool
    # from the planner's own constants (the sweep's fix).
    fillers = _density_fillers(PDV, duration_min=60, round_trip=True, radius_m=60.0)
    edge_lat = PDV[0] + 0.0030  # ~333 m north: inside normal RT reach (444 m),
    # outside halved RT reach (222 m) — envelope_radius_m halves for round trips.
    edge_anchor = _poi("edge-anchor", lat=edge_lat, lng=PDV[1], beat_count=15)
    corpus = _snap([edge_anchor, *fillers])

    normal = select_route(
        TourInput(start=PDV, duration_min=60, city_slug="paris", round_trip=True), corpus
    )
    paced = select_route(
        TourInput(
            start=PDV, duration_min=60, city_slug="paris", round_trip=True, walking_pace=2.0
        ),
        corpus,
    )
    assert "edge-anchor" in {p.id for p in normal.pois}, (
        "fixture premise: the edge anchor must be worth the walk at normal pace"
    )
    assert "edge-anchor" not in {p.id for p in paced.pois}, (
        "a half-pace party must not be offered a world it cannot actually walk"
    )


def test_isochrone_walking_speed_override_rides_the_request(monkeypatch):
    """The Valhalla reach contour's `walking_speed` scales by
    `REACH_PACE_KMH / multiplier` per request when pace is set (the road
    polygon must shrink with the circle), and stays the documented
    default when it is not — proven against the real request body, the same
    MockTransport-recording shape `test_tour_routing_engine.py` already uses.
    """
    import json

    import httpx

    from src.tour.routing import REACH_PACE_KMH
    from src.tour.routing_client import RoutingClient

    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        ring = [[2.20, 48.80], [2.45, 48.80], [2.45, 48.92], [2.20, 48.92], [2.20, 48.80]]
        return httpx.Response(
            200,
            json={
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]}}
                ],
            },
        )

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://valhalla.test")
    with RoutingClient(client=http) as rc:
        rc.isochrone(48.8584, 2.2945, 30)  # no override: today's default
        rc.isochrone(48.8584, 2.2945, 30, walking_speed_kmh=REACH_PACE_KMH / 2.0)

    assert seen[0]["costing_options"]["pedestrian"]["walking_speed"] == REACH_PACE_KMH
    assert seen[1]["costing_options"]["pedestrian"]["walking_speed"] == pytest.approx(
        REACH_PACE_KMH / 2.0
    )


# --- route surface rides per-request costing -----------------------------------


def test_step_free_axis_rides_the_costing_options():
    """`route_surface` set → the mock transport sees the step-avoiding
    override in the request body; unset → byte-identical body to today.

    Riverside stairs that any shortest-path router would love are unusable to
    a step-free visitor, and wheeled luggage makes cobbles unpleasant. Mapping
    proven live: `no_stairs` -> `step_penalty`; `step_free` adds
    `type: wheelchair`.
    """
    import json

    import httpx

    from src.tour.routing_client import RoutingClient
    from src.tour.selection import select_route
    from tests.test_tour_selection import PDV

    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path == "/route":
            seen.append(body["costing_options"]["pedestrian"])
            a, b = body["locations"]
            from src.tour.routing import haversine_m

            d = haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
            return httpx.Response(
                200,
                json={
                    "trip": {
                        "legs": [
                            {
                                "summary": {"time": max(1, round(d / 1.1)), "length": d / 1000.0},
                                "shape": "mockshape",
                            }
                        ]
                    }
                },
            )
        return httpx.Response(404)

    from tests.test_tour_selection import _density_fillers, _snap

    corpus = _snap(_density_fillers(PDV, duration_min=60, n=10, radius_m=90.0))

    def _fresh_client() -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://valhalla.test")

    with RoutingClient(client=_fresh_client()) as rc:
        select_route(
            TourInput(start=PDV, duration_min=60, city_slug="paris"),
            corpus,
            routing_client=rc,
        )
    assert seen, "fixture premise: at least one /route call must have happened"
    assert "step_penalty" not in seen[0], "an unset route_surface must carry no override"

    seen.clear()
    with RoutingClient(client=_fresh_client()) as rc:
        select_route(
            TourInput(
                start=PDV, duration_min=60, city_slug="paris", route_surface="step_free"
            ),
            corpus,
            routing_client=rc,
        )
    assert seen, "fixture premise: at least one /route call must have happened"
    assert seen[0]["step_penalty"] == 3600
    assert seen[0]["type"] == "wheelchair"
    assert "walking_speed" in seen[0], "the override must merge into the pace pin"


def test_route_with_receipt_surface_override_produces_a_self_consistent_receipt():
    """A receipt built under an override validates — its `routing_config_json`
    reflects the OVERRIDDEN config actually sent, not the module default
    (`ValhallaLegReceipt._canonical_payloads_match_fields` derives the config
    it expects from the request itself), and `route_with_receipt` never
    mutates the shared `_ROUTING_CONFIG`/`_PEDESTRIAN_COSTING_OPTIONS` module
    state — proven by a plain call after the overridden one returning to the
    documented default."""
    from src.tour.routing_client import VALHALLA_ROUTING_CONFIG_JSON
    from tests.test_tour_routing_engine import POINTS, _client, _valhalla_handler

    with _client(_valhalla_handler) as rc:
        _, _, _, receipt = rc.route_with_receipt(
            *POINTS[0], *POINTS[1], costing_options_override={"step_penalty": 3600}
        )
        assert receipt is not None
        assert receipt.routing_config_json != VALHALLA_ROUTING_CONFIG_JSON
        assert '"step_penalty":3600' in receipt.routing_config_json

        # The module default must be UNCHANGED for the very next call.
        _, _, _, plain_receipt = rc.route_with_receipt(*POINTS[0], *POINTS[1])
        assert plain_receipt is not None
        assert plain_receipt.routing_config_json == VALHALLA_ROUTING_CONFIG_JSON


# --- place judgements — one poi_score consumer per judgement -----------------


def test_family_days_weight_children_can_run_places_up():
    """A family day's `poi_score` weights a `children_can_run` place above an
    otherwise-identical rival; no party (or a non-family party) leaves the two
    tied.

    Much of a family's day is valuable because the kids can be
    loud/fast/free-range, and the corpus otherwise has no 'children can run
    here' channel.
    """
    from src.tour.selection import poi_score
    from tests.test_tour_selection import PDV, _poi, _snap

    runnable = _poi("runnable", lat=PDV[0], lng=PDV[1], beat_count=5).model_copy(
        update={"children_can_run": True}
    )
    plain = _poi("plain", lat=PDV[0], lng=PDV[1], beat_count=5)
    corpus = _snap([runnable, plain])

    tied_no_party = poi_score(runnable, None, frozenset(), corpus) == poi_score(
        plain, None, frozenset(), corpus
    )
    assert tied_no_party, "with no party the two otherwise-identical POIs must tie"

    tied_wrong_party = poi_score(
        runnable, None, frozenset(), corpus, party="solo"
    ) == poi_score(plain, None, frozenset(), corpus, party="solo")
    assert tied_wrong_party, "a non-family party must not favour children_can_run"

    family_runnable = poi_score(runnable, None, frozenset(), corpus, party="family")
    family_plain = poi_score(plain, None, frozenset(), corpus, party="family")
    assert family_runnable > family_plain, (
        "a family day must weight the children-can-run place above its "
        "otherwise-identical rival"
    )


def test_take_it_easy_and_couple_days_weight_sit_and_talk_places_up():
    """Take-it-easy and couple days weight a `sit_and_talk` place up; family
    and solo do not.

    A couple who would rather talk picks a garden for its seating, enclosure
    and quiet, properties no POI in the corpus otherwise carries.
    """
    from src.tour.selection import poi_score
    from tests.test_tour_selection import PDV, _poi, _snap

    talkable = _poi("talkable", lat=PDV[0], lng=PDV[1], beat_count=5).model_copy(
        update={"sit_and_talk": True}
    )
    plain = _poi("plain", lat=PDV[0], lng=PDV[1], beat_count=5)
    corpus = _snap([talkable, plain])

    for party in ("couple", "take_it_easy"):
        boosted = poi_score(talkable, None, frozenset(), corpus, party=party)
        rival = poi_score(plain, None, frozenset(), corpus, party=party)
        assert boosted > rival, f"{party} must weight the sit-and-talk place up"

    for party in ("family", "solo", None):
        tied = poi_score(talkable, None, frozenset(), corpus, party=party) == poi_score(
            plain, None, frozenset(), corpus, party=party
        )
        assert tied, f"{party!r} must not favour sit_and_talk"


def test_spotlight_does_not_gain_the_affordance_factor():
    """`spotlight` (the banding function) is UNCHANGED by the place
    judgements: banding is not party-aware. A
    `children_can_run` place scores identically under `spotlight` regardless
    of party; only `poi_score` (ranking) moves."""
    from src.tour.selection import spotlight
    from tests.test_tour_selection import PDV, _poi, _snap

    runnable = _poi("runnable", lat=PDV[0], lng=PDV[1], beat_count=5).model_copy(
        update={"children_can_run": True}
    )
    corpus = _snap([runnable])

    # spotlight() takes no `party` argument at all — its signature itself is
    # the proof; this call would raise TypeError if one were added.
    value_a = spotlight(runnable, lenses=None, snapshot=corpus)
    value_b = spotlight(runnable, lenses=None, snapshot=corpus)
    assert value_a == value_b


# --- escape radius as a candidate filter --------------------------------------


def test_escape_radius_refuses_a_far_anchor_on_family_but_seats_it_on_solo():
    """A rich anchor just outside the escape radius is refused when the axis
    is set (family) and seated when it is not (solo) — the SAME corpus, the
    SAME distance, only the axis differs.

    A meltdown 25 minutes from the exit means carrying a child for 25 minutes,
    so escape radius is a constraint on distance from the START, not on any
    one leg: family days are tight loops around a short escape radius.
    """
    from src.tour.selection import select_route
    from tests.test_tour_selection import PDV, _density_fillers, _poi, _snap

    # RE-DERIVED for the three-minute tight: eight fillers filled
    # 90 minutes at 270 s a stop; at 180 the day is "far short" and refuses before
    # the axis is asked. Swept 14-22: 14-18 hold both arms (solo seats the far
    # anchor, the family radius refuses it); 20+ out-competes it even solo. 16 is
    # the middle of the window.
    fillers = _density_fillers(PDV, duration_min=90, round_trip=True, n=16, radius_m=70.0)
    far_lat = PDV[0] + 0.0045  # ~500 m north
    far_anchor = _poi("far-anchor", lat=far_lat, lng=PDV[1], beat_count=15)
    corpus = _snap([far_anchor, *fillers])

    solo = select_route(
        TourInput(start=PDV, duration_min=90, city_slug="paris", round_trip=True), corpus
    )
    assert "far-anchor" in {p.id for p in solo.pois}, (
        "fixture premise: the far anchor must be worth the walk with no radius set"
    )

    family = select_route(
        TourInput(
            start=PDV, duration_min=90, city_slug="paris", round_trip=True, escape_radius_m=300
        ),
        corpus,
    )
    assert "far-anchor" not in {p.id for p in family.pois}, (
        "a family's escape radius must refuse a stop the exit is too far from"
    )
    assert family.clock_exclusions == (), (
        "a too-far stop needs no disclosure — only a closed door does"
    )


# --- the per-leg cap becomes a constraint ------------------------------------

#: ~555 m north of PDV: a ~15-minute leg at the corpus pace (1.62 s/m) — over
#: a 12-minute cap. The two stones flank the midpoint ±100 m east/west, each
#: ~8 minutes from both the start and the far anchor.
_FAR_ANCHOR = (48.86052, 2.3656)
_STONE_EAST = (48.85803, 2.36697)
_STONE_WEST = (48.85803, 2.36423)
_LEG_CAP_SECONDS = 12 * 60


def _leg_cap_corpus(*, with_stones: bool):
    from tests.test_tour_selection import PDV, _density_fillers, _poi, _snap

    # RE-DERIVED for the three-minute tight: 13/15 fillers filled
    # 120 minutes at 270 s a stop; at 180 the pool starves and every arm refuses,
    # while the fully-derived pool (32) is so rich the far anchor is out-competed
    # and never seated. Swept 18-30 against every arm: 18-24 hold all of them
    # (uncapped seats the far anchor over an over-cap leg; the capped stoneless
    # day trades it away; the capped stones day keeps it leg-legal). 22 is the
    # middle of the window; the stones still replace two.
    fillers = _density_fillers(
        PDV,
        duration_min=120,
        round_trip=True,
        n=20 if with_stones else 22,
        radius_m=80.0,
    )
    pois = [
        _poi("far-anchor", lat=_FAR_ANCHOR[0], lng=_FAR_ANCHOR[1]),
        *fillers,
    ]
    if with_stones:
        pois.append(_poi("stone-east", lat=_STONE_EAST[0], lng=_STONE_EAST[1]))
        pois.append(_poi("stone-west", lat=_STONE_WEST[0], lng=_STONE_WEST[1]))
    return _snap(pois)


def _leg_cap_request(max_leg_minutes: int | None = None) -> TourInput:
    from tests.test_tour_selection import PDV

    kwargs: dict[str, object] = {}
    if max_leg_minutes is not None:
        kwargs["max_leg_minutes"] = max_leg_minutes
    return TourInput(
        start=PDV, duration_min=120, city_slug="paris", round_trip=True, **kwargs
    )


def _route_leg_seconds(route) -> list[int]:
    return [
        t.leg_seconds if t.leg_seconds is not None else t.walk_seconds
        for t in route.transits
    ]


def test_a_leg_cap_prefers_more_walking_in_shorter_pieces():
    """With the axis set, no single walk may exceed the cap — the planner buys
    the far anchor with MORE total walking in shorter pieces when the city
    offers stepping stones, and trades it away when it does not.

    A walking budget is not one number. A step-free visitor's total can be
    unremarkable while her *per-leg* limit is the binding constraint, and the
    request carries it as a cap. A route with one 25-minute leg and one
    5-minute leg has the same total as a gentler one and is unusable.
    """
    from src.tour.selection import select_route

    # PREMISE — without the axis the planner happily builds the long march:
    # the far anchor is seated over a leg well past 12 minutes.
    uncapped = select_route(_leg_cap_request(), _leg_cap_corpus(with_stones=False))
    assert "far-anchor" in {p.id for p in uncapped.pois}, (
        "fixture premise: the rich far anchor must be worth seating uncapped"
    )
    assert max(_route_leg_seconds(uncapped)) > _LEG_CAP_SECONDS, (
        "fixture premise: seating it uncapped must cost an over-cap leg"
    )

    # THE CAP IS HARD — same stoneless corpus: every single leg obeys the cap,
    # which here means the far anchor is traded away (no cap-respecting path
    # to it exists).
    capped_stoneless = select_route(
        _leg_cap_request(12), _leg_cap_corpus(with_stones=False)
    )
    assert max(_route_leg_seconds(capped_stoneless), default=0) <= _LEG_CAP_SECONDS

    # SHORTER PIECES, MORE WALKING — give the city stepping stones and the
    # capped planner keeps the anchor: every leg fits the cap, and the day
    # walks further in total than the capped stoneless day to do it.
    capped_stones = select_route(
        _leg_cap_request(12), _leg_cap_corpus(with_stones=True)
    )
    assert "far-anchor" in {p.id for p in capped_stones.pois}, (
        "the cap must restructure the walk, not delete the anchor the city "
        "offers stones for"
    )
    assert max(_route_leg_seconds(capped_stones)) <= _LEG_CAP_SECONDS
    assert capped_stones.total_walk_seconds > capped_stoneless.total_walk_seconds


def test_a_leg_cap_the_uncapped_day_already_honours_is_already_true():
    """When a dial turn cannot bind it SAYS already true, and changes
    nothing. A cap the un-capped day
    already satisfies must return THAT day, byte-identical, and never enter the
    bounded search.

    It did not. The bounded search's timebox repair can only DROP stops, and
    dropping a middle stop merges two walks into one longer walk that breaks
    the cap — so under a cap the repair could not shrink an over-long day into
    band. On the live flagship (Tuileries→Notre-Dame, 300 min) whose un-capped
    day has an 18-minute longest walk, a 25-MINUTE cap REFUSED with the same
    numbers a 9-minute cap did: loosening a constraint deleted the tour, and a
    limit whose loosening changes not one digit is not in the sum. The fix:
    plan without the cap first; if it already honours the cap, it is the day.

    UNDO TEST: delete the "ALREADY TRUE" block at the top of select_route ->
    the 20-minute cap runs the bounded search and returns a different route
    (more walking in shorter pieces) -> RED.
    """
    from src.tour.selection import select_route

    corpus = _leg_cap_corpus(with_stones=False)  # the un-capped day marches ~15 min
    uncapped = select_route(_leg_cap_request(), corpus)
    longest = max(_route_leg_seconds(uncapped))
    assert longest > _LEG_CAP_SECONDS, "fixture premise: the 12-minute cap must bind"
    loose_cap = (longest // 60) + 5  # comfortably above the longest walk: cannot bind
    assert loose_cap * 60 > longest

    # THE SEAM, pinned directly: a cap that cannot bind must never ENTER the
    # bounded search. `_insertion_legs_fit_cap` is that search's own admission
    # rule and is called with a real cap only there (the un-capped plan calls
    # it with None and returns at once) — so a call carrying a cap IS the
    # bounded search running. On this small corpus the two paths happen to
    # agree on the route, which is exactly why the invariant, not the route,
    # is what this test holds.
    import src.tour.selection as selection_mod

    real_fit = selection_mod._insertion_legs_fit_cap

    def _tripwire(*args, **kwargs):
        if kwargs.get("max_leg_seconds") is not None:
            raise AssertionError(
                "the bounded search ran for a cap the un-capped day already honours"
            )
        return real_fit(*args, **kwargs)

    selection_mod._insertion_legs_fit_cap = _tripwire
    try:
        already_true = select_route(_leg_cap_request(loose_cap), corpus)
    finally:
        selection_mod._insertion_legs_fit_cap = real_fit
    assert already_true == uncapped, (
        "a cap the un-capped day already honours must return that day unchanged"
    )
    # And a cap that DOES bind still reaches the bounded search (the sibling
    # test above proves what that search does — here it trades the anchor
    # away); this only pins the seam.
    binding = select_route(_leg_cap_request(12), corpus)
    assert max(_route_leg_seconds(binding), default=0) <= _LEG_CAP_SECONDS
    assert binding != uncapped


def test_a_leg_cap_that_starves_the_day_refuses_with_the_cap_named():
    """A dial turn may refuse with a reason, or re-plan a full day; it may
    NEVER quietly hand back a sixth of what was asked for.

    Measured 2026-08-11: the "shorter walks" strawman at 6 minutes collapsed a
    180-minute round trip to ONE stop, zero walking, ~30 minutes of day —
    shipped as "planned 180", tourability GREEN.

    The floor stays SOFT for honest near-misses (duration is a ceiling, not a
    contract to fill — the well-liked base days run 62-68% and ship
    disclosed). What refuses is the EXTREME: a best-possible day under HALF the
    ask. And the refusal must name what binds — the leg cap — not claim the
    day "overruns" (the wrong-template defect).
    """
    from src.tour.selection import CertificationPlanningInfeasibleError, select_route
    from tests.test_tour_selection import PDV, _density_fillers, _poi, _snap

    # One anchor beside the start; a RICH duration-calibrated cluster ~890 m
    # north (inside the 180-min reach envelope, so the density gate stays
    # GREEN — the day must starve at the CAP, not at the corpus). Uncapped
    # this plans a real multi-stop day by walking the ~24-minute leg north;
    # under a 6-minute leg cap only the near anchor is reachable, and one stop
    # cannot fill half of three hours.
    far_center = (PDV[0] + 0.008, PDV[1])
    pois = [
        _poi("near-anchor", lat=PDV[0] + 0.0008, lng=PDV[1]),
        *_density_fillers(
            far_center, duration_min=180, round_trip=True, radius_m=80.0
        ),
    ]
    snap = _snap(pois)

    # PREMISE — uncapped, the same corpus plans a multi-stop day (the corpus is
    # not the problem; the cap is).
    uncapped = select_route(
        TourInput(start=PDV, duration_min=180, city_slug="paris", round_trip=True),
        snap,
    )
    assert len(uncapped.pois) >= 2, "fixture premise: uncapped must be a real day"

    # THE REFUSAL — capped at 6, the best buildable day is one near stop, far
    # under half the ask: refuse, name the cap, never ship it silently.
    with pytest.raises(CertificationPlanningInfeasibleError) as caught:
        select_route(
            TourInput(
                start=PDV,
                duration_min=180,
                city_slug="paris",
                round_trip=True,
                max_leg_minutes=6,
            ),
            snap,
        )
    message = str(caught.value)
    assert "overruns" not in message, (
        "the refusal claims the day OVERRUNS while it starved — the "
        "wrong-template defect"
    )
    # Named in the dial's own words — "the N-minute limit on any
    # single walk" — never "walking-leg cap ... binds", which the language judge
    # ruled three second meanings of everyday words in one clause.
    assert "limit on any single walk" in message and "6-minute" in message, (
        f"the refusal must name the constraint that binds, plainly: {message}"
    )
    assert "walking-leg cap" not in message and "binds" not in message, message
    assert caught.value.alternatives, "a refusal must offer a way out"

    # THE OPEN EXEMPTION — the same starved request under end_hardness='open'
    # ships the short day instead of refusing: open means "however long it is"
    # (a visitor may leave the end of the day blank).
    open_day = select_route(
        TourInput(
            start=PDV,
            duration_min=180,
            city_slug="paris",
            round_trip=True,
            max_leg_minutes=6,
            end_hardness="open",
        ),
        snap,
    )
    assert len(open_day.pois) >= 1, "open hardness keeps the honest short day"


def test_breakdown_prints_the_resolved_party_even_when_it_moves_no_number(capsys):
    """Two runs whose axes tie on every NUMBER the table shows (a preset pair
    whose only new axis is the narration register, which touches none of
    walk/dwell/leg/table cells) must still print visibly different output —
    the day has to be different in the OUTPUT, not just in the code path
    that built it. Take-it-easy + solo is a legitimate pair."""
    import importlib

    tour_build = importlib.import_module("scripts.tour_build")

    plain = _base_input()
    tour_build._print_breakdown(tour_input=plain, wall_clock_s=1.0)
    assert "party:              none" in capsys.readouterr().out

    from src.tour.contract import resolve_party_axes

    take_it_easy = resolve_party_axes(_base_input(party="take_it_easy"))
    tour_build._print_breakdown(tour_input=take_it_easy, wall_clock_s=1.0)
    out_a = capsys.readouterr().out
    assert "preset=take_it_easy" in out_a
    assert "register=" not in out_a, "take-it-easy alone leaves the register unset"

    solo_and_take_it_easy = take_it_easy.model_copy(update={"narration_register": "solo"})
    tour_build._print_breakdown(tour_input=solo_and_take_it_easy, wall_clock_s=1.0)
    out_b = capsys.readouterr().out
    assert "register=solo" in out_b, (
        "the solo+take-it-easy pair must show its one distinguishing axis "
        "even though it moves no other cell in the table"
    )
    assert out_a != out_b, "the two runs must be visibly different in the output"


def test_seven_number_block_is_unchanged_above_the_per_stop_table(capsys):
    """The seven-number block still opens the breakdown, unchanged, above the
    new table, so earlier measurements stay readable."""
    import importlib

    tour_build = importlib.import_module("scripts.tour_build")
    route, script = _priced_route()

    tour_build._print_breakdown(
        tour_input=_base_input(), wall_clock_s=1.0, route=route, script=script
    )
    out = capsys.readouterr().out

    for label in (
        "walk:", "dwell:", "total (walk+dwell):", "longest leg:",
        "furthest off A→B:", "reach radius:", "wall clock:",
    ):
        assert label in out, f"the seven-number block lost {label!r}"
    assert out.index("walk:") < out.index("per-stop"), (
        "the per-stop table must sit BELOW the seven-number block"
    )


# ---------------------------------------------------------------------------
# The leg cap is certified on the EXACT legs of the FINAL route
# ---------------------------------------------------------------------------


def _ring_of(center, radius_m, count, prefix, **kw):
    """`count` POIs on a ring around `center` (consecutive ones radius*sqrt(2)
    apart at four points)."""
    import math

    from tests.test_tour_selection import _poi

    dlat_m = 1.0 / 111_320.0
    dlng_m = 1.0 / (111_320.0 * math.cos(math.radians(center[0])))
    return [
        _poi(
            f"{prefix}-{i}",
            lat=center[0] + radius_m * math.cos(2 * math.pi * i / count) * dlat_m,
            lng=center[1] + radius_m * math.sin(2 * math.pi * i / count) * dlng_m,
            **kw,
        )
        for i in range(count)
    ]


def _street_minutes(transits) -> int:
    """The test's OWN oracle for the longest street leg — deliberately spelled
    apart from `routing.longest_walk_minutes`, because a certification that reads
    the estimate would fool an assertion that reads it through the same helper
    (an undo probe proved exactly that)."""
    worst = 0
    for t in transits:
        exact = t.leg_seconds if (t.source == "valhalla" and t.leg_seconds is not None) else None
        worst = max(worst, exact if exact is not None else t.walk_seconds)
    return round(worst / 60)


def _at(center, distance_m, bearing_deg, pid, **kw):
    import math

    from tests.test_tour_selection import _poi

    dlat_m = 1.0 / 111_320.0
    dlng_m = 1.0 / (111_320.0 * math.cos(math.radians(center[0])))
    b = math.radians(bearing_deg)
    return _poi(
        pid,
        lat=center[0] + distance_m * math.cos(b) * dlat_m,
        lng=center[1] + distance_m * math.sin(b) * dlng_m,
        **kw,
    )


def test_the_leg_cap_is_certified_on_the_street_route_not_the_estimate():
    """A day whose ESTIMATED legs honour "no single walk longer than N
    minutes" and whose STREET-ROUTED legs do not must never ship with a head line
    that contradicts the dial the person turned. Measured live:
    Carnavalet -> Place des Vosges est 473 s <= 540 < exact 576 s under a 9-minute
    cap; a step-free visitor's Tuileries -> Orsay est 11.1 min, 13.8 by the street
    under her 12. A walking budget is not one number: one long leg makes a route
    unusable. Every entry is timed on the STREET route.

    After ``summarise_route`` has produced the final routed transits, the cap is
    certified on ``leg_walk_seconds`` — THE one expression, exact when a polyline
    came back — through the same rounding the head line reads
    (``routing.longest_walk_minutes``); on a breach the planner tightens once and
    retries at the exceeded value, and if the street still breaks the cap it
    REFUSES with the cap named in plain words rather than shipping a
    contradiction.

    Arm A uses ``tests/routing_doubles.DivergentRoutingClient`` — the only double in
    the repo whose exact != estimate (Extends: the two deterministic doubles were
    considered and rejected as the home; both are defined to agree). UNDO: certify
    on ``t.walk_seconds`` instead of ``leg_walk_seconds(t)`` -> the divergent day
    ships again -> RED. Arm A's control (factor 1.0) proves the certification never
    refuses a day whose street legs honour the cap.
    """
    from src.tour.selection import CertificationPlanningInfeasibleError, select_route
    from tests.routing_doubles import DivergentRoutingClient
    from tests.test_tour_selection import PDV, _snap

    start = PDV
    # Six 10-minute stands on a 200 m ring (a hexagon: every hop 200 m = 5.4 min
    # by the estimate — under a 6-minute cap; x1.3 by the "street" = 7.0 min —
    # over it). Six tier-5 anchors keep density GREEN on their own; the whole day
    # is ~98 min estimated / ~109 by the street, inside a 120-minute request either
    # way, so the ONLY thing at stake is the cap.
    near = [
        p.model_copy(update={"typical_duration_min": 10})
        for p in _ring_of(start, 200.0, 6, "near")
    ]
    snap = _snap(near)
    capped = TourInput(
        start=start, duration_min=120, city_slug="paris", round_trip=True, max_leg_minutes=6
    )

    # CONTROL — street == estimate: the ring day ships and honours the cap.
    honest = select_route(capped, snap, routing_client=DivergentRoutingClient(factor=1.0))
    assert len(honest.pois) >= 3, "fixture premise: the capped ring day is real"
    assert _street_minutes(honest.transits) <= 6, (
        "control: the certification must not refuse a day whose street legs fit"
    )

    # THE FINDING — the estimate fits, the street does not: never shipped as-is.
    divergent = DivergentRoutingClient(factor=1.3)
    try:
        day = select_route(capped, snap, routing_client=divergent)
    except CertificationPlanningInfeasibleError as refused:
        message = str(refused)
        assert "6-minute limit on any single walk" in message, message
        assert "street route" in message, (
            f"the refusal must say the street route is what breaks the cap: {message}"
        )
        assert "overruns" not in message and "walking-leg cap" not in message, message
        assert refused.alternatives, "a refusal must offer a way out"
    else:
        # A tighten-and-retry that landed is fine — but only if the street agrees.
        assert _street_minutes(day.transits) <= 6, (
            f"shipped a day whose street route breaks the 6-minute cap: "
            f"{[t.leg_seconds for t in day.transits]}"
        )
    assert divergent.exact_calls > 0, "the double's exact legs were never read"


def test_a_fold_that_merges_two_legs_cannot_leave_the_merged_leg_over_the_cap():
    """The second arm — the LIVE mechanism behind a shipped leg over the cap
    (measured in-process 2026-08-18): on the PdV cap-9 day the
    repair certified Carnavalet -> Victor Hugo 538 s and Victor Hugo -> Place des
    Vosges 176 s, both under 540; co-located demotion then folded Victor Hugo into
    Place des Vosges and the MERGED leg Carnavalet -> Place des Vosges (576 s) was
    never re-checked. Dropping a stop MERGES its two legs, and a fold IS a drop,
    so the merged leg must honour the longest-single-walk cap too.

    Hermetic on default legs (no double): a name-twin pair on one bearing — the
    lower-tier twin is the stepping stone the higher-tier twin needs to be reached
    under the cap; the twin collapse drops the stepping stone and the merged leg
    breaks the cap. The invariant, robust to HOW it is honoured (the street
    certification refuses; a drop that re-checks the fold itself may instead keep
    the day): a shipped day's longest STREET leg honours the cap, or the day is
    refused with the cap named. Never a shipped contradiction.
    """
    from src.tour.selection import CertificationPlanningInfeasibleError, select_route
    from tests.test_tour_selection import _snap

    start = (48.8555, 2.3656)
    # A 100 m ring of four 7-minute stands (hops ~141 m = 3.8 min under a 4-min cap)
    # and a twin pair on bearing 30 degrees: A at 140 m (tier 4), B at 280 m
    # (tier 5), same display name — B is only reachable through A under the cap.
    ring = [
        p.model_copy(update={"typical_duration_min": 7})
        for p in _ring_of(start, 100.0, 4, "ring")
    ]
    twin_a = _at(start, 140.0, 30.0, "twin-a", tier=4, beat_count=3).model_copy(
        update={"name": "Twin", "typical_duration_min": 7}
    )
    twin_b = _at(start, 280.0, 30.0, "twin-b", tier=5, beat_count=5).model_copy(
        update={"name": "Twin", "typical_duration_min": 7}
    )
    snap = _snap([*ring, twin_a, twin_b])
    # An open walk (no closing leg), so the far twin can sit at the tail.
    capped = TourInput(
        start=start, duration_min=75, city_slug="paris", round_trip=False, max_leg_minutes=4
    )
    try:
        day = select_route(capped, snap)
    except CertificationPlanningInfeasibleError as refused:
        assert "4-minute limit on any single walk" in str(refused), str(refused)
        return
    ids = [p.id for p in day.pois]
    assert "twin-b" in ids, f"fixture premise: the far twin must be seated: {ids}"
    assert _street_minutes(day.transits) <= 4, (
        f"a fold merged two legs into one over the 4-minute cap and the day shipped: "
        f"stops {ids}, legs {[t.walk_seconds for t in day.transits]}"
    )


def test_a_cap_retry_that_loses_a_rest_is_the_honest_line_not_a_gutted_day(monkeypatch):
    """On a step-free visitor's real day: under her preset's 12-minute cap
    the street-certified retry tightened to 11, dropped the Orangerie AND her
    bench, and shipped a one-stop day with 80 unplanned minutes and no rest — while
    the same build says the honest line for a ten-minute leg at Place des Vosges.
    Removing a rest to satisfy a leg cap is backwards: the rest exists because of
    the legs. So the retry may not pay with a rest: a retried day that
    lost a rest the first day had seated is REFUSED with the line that names the
    street number and her limit, and SHE decides (allow the longer walk, or a
    shorter day). A retry that keeps every rest still ships. UNDO: return the retry
    regardless of its rests -> RED (the gutted day ships)."""
    from src.tour import selection
    from src.tour.selection import CertificationPlanningInfeasibleError, select_route
    from tests.routing_doubles import DivergentRoutingClient
    from tests.test_tour_selection import PDV, _snap

    # A real day with a bench: six stands on a 200 m ring (every hop 200 m = 5.4
    # min by the estimate, under the 6-minute cap) and a bench beside the path, a
    # rest every few minutes so the bench is seated.
    near = [
        p.model_copy(update={"typical_duration_min": 10})
        for p in _ring_of(PDV, 200.0, 6, "near")
    ]
    bench = _at(PDV, 200.0, 30.0, "bench", role="body", tier=1, beat_count=0).model_copy(
        update={"typical_duration_min": 6}
    )
    snap = _snap([*near, bench])
    request = TourInput(
        start=PDV,
        duration_min=120,
        city_slug="paris",
        round_trip=True,
        max_leg_minutes=6,
        rest_cadence_minutes=6,
    )
    honest = select_route(request, snap, routing_client=DivergentRoutingClient(factor=1.0))
    assert bench.id in {p.id for p in honest.pois}, "premise: the day seats the bench"

    # The first pass breaks the cap on the street (one leg stretched); the retry
    # "lands" under the cap but without the bench. Both are stand-ins for what the
    # planner produced on a step-free visitor's day (a stretched leg; a retry that paid with
    # her rest).
    stretched = honest.model_copy(
        update={
            "transits": (
                honest.transits[0].model_copy(
                    update={
                        "source": "valhalla",
                        "leg_seconds": 8 * 60,
                        "valhalla_receipt": None,
                    }
                ),
                *honest.transits[1:],
            )
        }
    )
    without_bench = honest.model_copy(
        update={"pois": tuple(p for p in honest.pois if p.id != bench.id)}
    )
    # The door plans TWICE before the cap check on a rest-cadence day (the base,
    # then the "more breaks" replan over it), then once more for the cap retry.
    calls: list[int | None] = []

    def fake_once(input, snapshot, **kw):
        calls.append(input.max_leg_minutes)
        return stretched if len(calls) <= 2 else without_bench

    monkeypatch.setattr(selection, "_select_route_once", fake_once)
    with pytest.raises(CertificationPlanningInfeasibleError) as refused:
        select_route(request, snap, routing_client=DivergentRoutingClient(factor=1.0))
    message = str(refused.value)
    assert "walk of about 8 minutes by the street route" in message, message
    assert "6-minute limit on any single walk" in message, message
    assert calls == [6, 6, 4], calls  # the one retry, at the exceeded value
    # Control: a retry that keeps the rest still ships.
    calls.clear()

    def keeps_the_rest(input, snapshot, **kw):
        calls.append(input.max_leg_minutes)
        return stretched if len(calls) <= 2 else honest

    monkeypatch.setattr(selection, "_select_route_once", keeps_the_rest)
    shipped = select_route(request, snap, routing_client=DivergentRoutingClient(factor=1.0))
    assert bench.id in {p.id for p in shipped.pois}


def test_a_walking_cap_that_leaves_a_one_stop_day_says_what_a_longer_walk_would_buy():
    """A step-free visitor's preset day: under her 12-minute cap the
    planner's own estimate cannot reach the Orangerie, so she gets ONE stop, no walking,
    no rest, 90 minutes of 180 — "a museum ticket", served with its shortfall
    disclosed because the 50 % line stands. She needs the line said to her, with the
    answer the planner already owns. So a day of one or two story stops under a cap,
    below its nominal, is looked at ONCE at a cap one to three minutes longer, and a
    materially fuller day there becomes ONE honest line on the degradations channel
    (the itinerary shows it): what the longer walk would buy, in her words — she
    decides. Never a refusal, never on a three-stop day, never on a live tail.
    UNDO: return before the look -> RED (no row)."""
    from src.tour.degradations import degradation_scope
    from src.tour.selection import WALK_LIMIT_BINDS_DEGRADATION, select_route
    from tests.routing_doubles import DivergentRoutingClient
    from tests.test_tour_selection import PDV, _snap

    # One long stand 150 m from the start (5.4 min: inside a 6-minute cap; 50 minutes
    # there, so the one-stop day sits ABOVE the 50 % line and ships) and a
    # cluster 270 m out (7.3 min: over 6, under 8) — the cap is what keeps the day to
    # one stop, exactly that step-free day's shape.
    near = _at(PDV, 150.0, 0.0, "near", tier=5, beat_count=5).model_copy(
        update={"typical_duration_min": 70}
    )
    far = [
        _at(PDV, 270.0, b, f"far-{i}", tier=5, beat_count=5).model_copy(
            update={"typical_duration_min": 12}
        )
        for i, b in enumerate((60.0, 120.0, 180.0))
    ]
    snap = _snap([near, *far])
    capped = TourInput(
        start=PDV, duration_min=130, city_slug="paris", round_trip=True, max_leg_minutes=6
    )
    with degradation_scope() as rows:
        day = select_route(capped, snap, routing_client=DivergentRoutingClient(factor=1.0))
    assert [p.id for p in day.pois] == ["near"], [p.id for p in day.pois]
    offers = [r for r in rows if r.kind == WALK_LIMIT_BINDS_DEGRADATION]
    assert len(offers) == 1, [r.kind for r in rows]
    line = offers[0].human
    assert line.startswith("With walks of up to 8 minutes this day would have"), line
    assert "allow longer walks to get it" in line, line
    assert "instead of" in line, line
    # Control: a day of three stops or more under its cap (the six-stand ring,
    # with the same far cluster beyond the cap) is never looked at again, and
    # nothing is offered — the line is for the one-or-two-stop shape only.
    ring = [
        p.model_copy(update={"typical_duration_min": 10})
        for p in _ring_of(PDV, 200.0, 6, "ring")
    ]
    with degradation_scope() as rows:
        full = select_route(
            capped, _snap([*ring, *far]), routing_client=DivergentRoutingClient(factor=1.0)
        )
    assert len(full.pois) >= 3, [p.id for p in full.pois]
    assert not [r for r in rows if r.kind == WALK_LIMIT_BINDS_DEGRADATION]


def test_a_step_free_day_is_not_labelled_as_routed_with_foreign_settings():
    """Every take-it-easy day carried `routing_setup_unexpected` — its legs
    are routed STEP-FREE (the route-surface override), their receipts hash that
    configuration, and the check expected only the default one. This build's
    settings under ANY route-surface override are this build's settings; only a
    receipt matching none of them was routed by something else. UNDO: compare
    against the default hash alone -> RED (the step-free day is labelled again)."""
    from types import SimpleNamespace

    from src.tour.degradations import degradation_scope
    from src.tour.premium_tour import ROUTING_CONFIG_DEGRADATION, record_routing_degradations
    from src.tour.routing_client import (
        ROUTE_SURFACE_COSTING_OVERRIDES,
        VALHALLA_ROUTING_CONFIG_SHA256,
        VALHALLA_ROUTING_CONFIG_SHA256_BY_SURFACE,
    )

    assert set(VALHALLA_ROUTING_CONFIG_SHA256_BY_SURFACE) == set(ROUTE_SURFACE_COSTING_OVERRIDES)
    assert VALHALLA_ROUTING_CONFIG_SHA256_BY_SURFACE["any"] == VALHALLA_ROUTING_CONFIG_SHA256
    step_free = VALHALLA_ROUTING_CONFIG_SHA256_BY_SURFACE["step_free"]
    assert step_free != VALHALLA_ROUTING_CONFIG_SHA256

    def day(sha: str):
        leg = SimpleNamespace(valhalla_receipt=SimpleNamespace(routing_config_sha256=sha))
        return SimpleNamespace(transits=[leg, leg], routed=True)

    with degradation_scope() as rows:
        record_routing_degradations(day(step_free), component="test")
    assert not [r for r in rows if r.kind == ROUTING_CONFIG_DEGRADATION], (
        "a step-free day routed by THIS build is not 'different settings'"
    )
    with degradation_scope() as rows:
        record_routing_degradations(day("f" * 64), component="test")
    assert [r for r in rows if r.kind == ROUTING_CONFIG_DEGRADATION], (
        "a configuration none of this build's surfaces produce IS foreign"
    )


# --- the route surface rides through compose and finalize --------------------


def test_a_surface_routed_day_finalizes_under_its_own_routing_identity():
    """A day routed under a surface override (step-free, no-stairs) is finalized
    under THAT routing identity — the build fingerprint stamps the override's
    config hash, so the blueprint's self-consistency check passes and the
    workbench can author a take-it-easy or family day at all.

    A route is never selected under one costing and reported under another.
    MEASURED 2026-08-19 (the in-process compose of a take-it-easy day):
    `finalize_premium_tour` stamped the DEFAULT
    `VALHALLA_ROUTING_CONFIG_SHA256` into the fingerprint while every leg receipt
    carried the step-free hash, so `FinalTourBlueprint` raised "Valhalla receipt
    routing configuration differs from build fingerprint" — three attempts, three
    refusals, one Opus call each. On the workbench that is the Basic fallback for
    every surface-constrained day; on the phone it only LOOKED fine because compose
    rebuilt the route on the default surface (its sibling test in
    tests/test_trip_api.py). UNDO: stamp the default hash again -> RED here.
    """
    from src.tour.premium_tour import (
        EphemeralReceiptSink,
        OfflinePremiumExecutor,
        PremiumBuildIdentity,
        execute_premium_plan,
        finalize_premium_tour,
        plan_premium_authoring,
    )
    from src.tour.routing_client import (
        ROUTE_SURFACE_COSTING_OVERRIDES,
        VALHALLA_ROUTING_CONFIG_SHA256,
        VALHALLA_ROUTING_CONFIG_SHA256_BY_SURFACE,
    )
    from src.tour.verify import MockFaithfulnessChecker
    from tests.test_tour_authoring_gates import _cross_stop_echo_fixture
    from tests.test_tour_routing_engine import _client, _valhalla_handler

    def day(surface: str):
        stitched, sequence, route = _cross_stop_echo_fixture()
        override = ROUTE_SURFACE_COSTING_OVERRIDES[surface]
        # Real receipts from the real client over a mock transport, routed under
        # the surface's costing exactly as selection routes them.
        legs = []
        with _client(_valhalla_handler) as rc:
            prev = route.pois[0]
            for transit, poi in zip(route.transits, route.pois, strict=True):
                frm = (poi.lat, poi.lng) if transit.from_poi_id is None else (prev.lat, prev.lng)
                kwargs = {"costing_options_override": override} if override else {}
                seconds, distance_m, shape, receipt = rc.route_with_receipt(
                    *frm, poi.lat, poi.lng, **kwargs
                )
                assert receipt is not None
                legs.append(
                    transit.model_copy(
                        update={
                            "leg_seconds": seconds,
                            "leg_distance_m": distance_m,
                            "polyline": shape,
                            "source": "valhalla",
                            "valhalla_receipt": receipt,
                        }
                    )
                )
                prev = poi
        route = route.model_copy(update={"transits": tuple(legs), "routed": True})
        inputs = stitched.inputs.model_copy(update={"route_surface": surface})
        stitched = stitched.model_copy(update={"inputs": inputs})
        plan = plan_premium_authoring(
            stitched,
            sequence,
            route,
            snapshot=None,
            snapshot_sha256="0" * 64,
            routing_version="offline-test",
            policy_version="offline-test",
        )
        responses = execute_premium_plan(
            plan, executor=OfflinePremiumExecutor(), receipt_sink=EphemeralReceiptSink()
        )
        return finalize_premium_tour(
            plan,
            responses,
            faithfulness_checker=MockFaithfulnessChecker(),
            build_identity=PremiumBuildIdentity(commit_sha="a" * 40),
        ).blueprint

    step_free = day("step_free")
    expected = VALHALLA_ROUTING_CONFIG_SHA256_BY_SURFACE["step_free"]
    assert expected != VALHALLA_ROUTING_CONFIG_SHA256, (
        "fixture premise: the override has its own hash"
    )
    assert step_free.build.routing_config_sha256 == expected, (
        "the fingerprint must name the routing identity the legs were ACTUALLY routed under"
    )
    receipt_hashes = {t.valhalla_receipt.routing_config_sha256 for t in step_free.route.transits}
    assert receipt_hashes == {expected}

    # The unconstrained day is byte-identical to before: the default hash.
    plain = day("any")
    assert plain.build.routing_config_sha256 == VALHALLA_ROUTING_CONFIG_SHA256
