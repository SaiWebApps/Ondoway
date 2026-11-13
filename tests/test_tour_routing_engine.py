"""Hermetic tests for the RoutingClient (Valhalla pedestrian + haversine fallback).

No Docker, no network, no skips: the Valhalla side is an httpx.MockTransport
speaking the documented /route and /isochrone JSON shapes; the outage side is
a transport that raises ConnectError. The mock "road network" applies a 1.2x
detour at 4 km/h — deliberately different from the fallback's 1.35x at 3 km/h,
so routed and haversine numbers can never coincide on a non-zero leg.
"""

from __future__ import annotations

import json

import httpx
import pytest

from src.tour.routing import haversine_m, pace_corrected_walk_seconds
from src.tour.routing_client import BASE_URL_ENV, RoutingClient

# Five real Paris points: Eiffel Tower, Louvre, Notre-Dame, Place des Vosges,
# Sacré-Cœur.
POINTS = [
    (48.8584, 2.2945),
    (48.8606, 2.3376),
    (48.8530, 2.3499),
    (48.8556, 2.3656),
    (48.8867, 2.3431),
]

SHAPE = "mock_polyline6_abc123"


def test_render_hostport_environment_is_normalized_to_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(BASE_URL_ENV, "ondoway-valhalla:8002")
    with RoutingClient() as client:
        assert client._client.base_url == httpx.URL("http://ondoway-valhalla:8002")


def _routed_meters(d_m: float) -> float:
    return d_m * 1.2


def _routed_seconds(d_m: float) -> int:
    return round(_routed_meters(d_m) / (4000.0 / 3600.0))


def _valhalla_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/status":
        return httpx.Response(200, json={"version": "3.7.0-test"})
    body = json.loads(request.content)
    if request.url.path == "/route":
        a, b = body["locations"]
        d = haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
        return httpx.Response(
            200,
            json={
                "trip": {
                    "legs": [
                        {
                            "summary": {
                                "time": _routed_seconds(d),
                                "length": _routed_meters(d) / 1000.0,  # km, per API
                            },
                            "shape": SHAPE,
                        }
                    ]
                }
            },
        )
    if request.url.path == "/isochrone":
        # Generous box over all Paris test geographies: the REACH
        # filter genuinely applies this polygon, so it must cover every
        # fixture coordinate (the parsing tests only assert its shape).
        ring = [[2.20, 48.80], [2.45, 48.80], [2.45, 48.92], [2.20, 48.92], [2.20, 48.80]]
        return httpx.Response(
            200,
            json={
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"metric": "time"},
                        "geometry": {"type": "Polygon", "coordinates": [ring]},
                    }
                ],
            },
        )
    return httpx.Response(404)


def _refusing_handler(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


def _client(handler) -> RoutingClient:
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://valhalla.test")
    return RoutingClient(client=http)


# ---------------------------------------------------------------------------
# Routed path (mocked Valhalla up)
# ---------------------------------------------------------------------------


def test_matrix_diagonal_zero_and_routed_differs_from_haversine():
    """5x5 Paris matrix: diagonal is 0; every off-diagonal routed time/distance
    differs from the pure haversine fallback."""
    with _client(_valhalla_handler) as rc:
        differing = 0
        for i, (lat1, lng1) in enumerate(POINTS):
            for j, (lat2, lng2) in enumerate(POINTS):
                secs = rc.leg_seconds(lat1, lng1, lat2, lng2)
                fallback = pace_corrected_walk_seconds(haversine_m(lat1, lng1, lat2, lng2))
                if i == j:
                    assert secs == 0
                else:
                    assert secs > 0
                    if secs != fallback:
                        differing += 1
        assert differing == 20, "every non-zero leg should be visibly routed, not haversine"


def test_route_parses_documented_response_fields():
    """seconds <- trip.legs[0].summary.time; metres <- length (km) * 1000;
    polyline <- trip.legs[0].shape."""
    (lat1, lng1), (lat2, lng2) = POINTS[0], POINTS[1]
    d = haversine_m(lat1, lng1, lat2, lng2)
    with _client(_valhalla_handler) as rc:
        seconds, meters, shape = rc.route(lat1, lng1, lat2, lng2)
    assert seconds == _routed_seconds(d)
    assert meters == pytest.approx(_routed_meters(d))
    assert shape == SHAPE


def test_routing_version_comes_from_the_same_client_instance() -> None:
    with _client(_valhalla_handler) as rc:
        assert rc.routing_version() == "3.7.0-test"
        assert rc.routing_version() == "3.7.0-test"


def test_isochrone_returns_feature_collection():
    with _client(_valhalla_handler) as rc:
        iso = rc.isochrone(48.8584, 2.2945, 30)
    assert iso is not None
    assert iso["type"] == "FeatureCollection"
    assert iso["features"][0]["geometry"]["type"] == "Polygon"


def test_requests_use_documented_wire_format():
    """Pin the outgoing JSON: pedestrian costing, lat/lon keys, km units,
    minute contours with polygons=true.

    Also pins the route-surface options here rather than in a second
    wire-format test: an unset override leaves the request byte-identical to the default
    ``_ROUTING_CONFIG``; a set override merges into ``costing_options.pedestrian``
    alongside the pace pin, and rides the SAME wire format the plain request
    uses — never a second shape.
    """
    seen: list[tuple[str, dict]] = []

    def recording_handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, json.loads(request.content)))
        return _valhalla_handler(request)

    with _client(recording_handler) as rc:
        rc.route(*POINTS[0], *POINTS[1])
        rc.isochrone(48.8584, 2.2945, 30)
        rc.route(*POINTS[0], *POINTS[1], costing_options_override={"step_penalty": 3600})

    route_bodies = [body for path, body in seen if path == "/route"]
    plain_body, surfaced_body = route_bodies[0], route_bodies[1]
    assert plain_body["costing"] == "pedestrian"
    assert plain_body["units"] == "kilometers"
    assert plain_body["locations"][0] == {"lat": POINTS[0][0], "lon": POINTS[0][1]}
    assert "step_penalty" not in plain_body["costing_options"]["pedestrian"], (
        "an unset surface axis must not carry any override"
    )

    iso_body = dict(seen)["/isochrone"]
    assert iso_body["costing"] == "pedestrian"
    assert iso_body["contours"] == [{"time": 30.0}]
    assert iso_body["polygons"] is True

    assert surfaced_body["costing_options"]["pedestrian"]["step_penalty"] == 3600
    assert "walking_speed" in surfaced_body["costing_options"]["pedestrian"], (
        "the surface override must MERGE into the pace pin, not replace it"
    )


# ---------------------------------------------------------------------------
# Fallback path (Valhalla down or broken)
# ---------------------------------------------------------------------------


def test_connect_error_falls_back_to_exact_haversine():
    """ConnectionError -> exactly
    pace_corrected_walk_seconds(haversine_m(...)), polyline None."""
    with _client(_refusing_handler) as rc:
        for lat1, lng1 in POINTS:
            for lat2, lng2 in POINTS:
                d = haversine_m(lat1, lng1, lat2, lng2)
                expected = pace_corrected_walk_seconds(d)
                assert rc.leg_seconds(lat1, lng1, lat2, lng2) == expected
                seconds, meters, shape = rc.route(lat1, lng1, lat2, lng2)
                assert (seconds, shape) == (expected, None)
                assert meters == pytest.approx(d)


def test_http_500_falls_back():
    with _client(lambda request: httpx.Response(500, text="boom")) as rc:
        (lat1, lng1), (lat2, lng2) = POINTS[0], POINTS[4]
        d = haversine_m(lat1, lng1, lat2, lng2)
        assert rc.leg_seconds(lat1, lng1, lat2, lng2) == pace_corrected_walk_seconds(d)


def test_garbage_response_falls_back():
    with _client(lambda request: httpx.Response(200, json={"unexpected": True})) as rc:
        (lat1, lng1), (lat2, lng2) = POINTS[1], POINTS[3]
        d = haversine_m(lat1, lng1, lat2, lng2)
        seconds, meters, shape = rc.route(lat1, lng1, lat2, lng2)
        assert seconds == pace_corrected_walk_seconds(d)
        assert meters == pytest.approx(d)
        assert shape is None


def test_isochrone_none_on_connect_error_and_on_garbage():
    with _client(_refusing_handler) as rc:
        assert rc.isochrone(48.8584, 2.2945, 30) is None
    with _client(lambda request: httpx.Response(200, json={"type": "nope"})) as rc:
        assert rc.isochrone(48.8584, 2.2945, 30) is None


def test_default_construction_owns_a_client():
    """RoutingClient() with no injected client builds (and closes) its own —
    no network call is made by construction."""
    rc = RoutingClient()
    rc.close()


# ---------------------------------------------------------------------------
# Routed leg_seconds/polyline on the contract; summarise_route/select_route
# ---------------------------------------------------------------------------

from src.tour.contract import Route, TourInput, TransitSegment
from src.tour.routing import summarise_route
from src.tour.selection import select_route
from tests.test_tour_selection import PDV, _density_fillers, _poi, _snap


def test_transit_segment_m2_fields_round_trip_and_default():
    routed = TransitSegment(
        from_poi_id=None,
        to_poi_id="p1",
        distance_m=500.0,
        walk_seconds=810,
        leg_seconds=540,
        polyline=SHAPE,
        source="valhalla",
    )
    assert TransitSegment.model_validate(routed.model_dump()) == routed
    # Construction without the routed fields still works; they default to haversine.
    legacy = TransitSegment(from_poi_id=None, to_poi_id="p1", distance_m=500.0, walk_seconds=810)
    assert (legacy.leg_seconds, legacy.polyline, legacy.source) == (None, None, "haversine")
    assert TransitSegment.model_validate(legacy.model_dump()) == legacy


def test_route_m2_fields_round_trip_and_default():
    route = Route(
        pois=(),
        transits=(),
        total_walk_distance_m=0.0,
        total_walk_seconds=0,
        routed=False,
        route_polyline=None,
    )
    assert (route.routed, route.route_polyline) == (False, None)
    assert (route.backtrack_ratio, route.flow_score) == (0.0, 0.0)
    assert Route.model_validate(route.model_dump()) == route


def _two_stop_pois():
    return [
        _poi("a", lat=PDV[0] + 0.0030, lng=PDV[1], areas=("Le Marais",)),  # ~330m N
        _poi("b", lat=PDV[0], lng=PDV[1] + 0.0045, areas=("Le Marais",)),  # ~330m E
    ]


def test_summarise_route_with_client_populates_routed_legs():
    with _client(_valhalla_handler) as rc:
        route = summarise_route(
            _two_stop_pois(),
            start_lat=PDV[0],
            start_lng=PDV[1],
            round_trip=True,
            duration_min=60,
            spine_area="Le Marais",
            routing_client=rc,
        )
    assert route.routed is True
    assert len(route.transits) == 3  # start->a, a->b, b->start
    for seg in route.transits:
        assert seg.source == "valhalla"
        assert seg.polyline == SHAPE
        assert seg.valhalla_receipt is not None
        assert seg.valhalla_receipt.polyline == seg.polyline
        assert seg.valhalla_receipt.seconds == seg.leg_seconds
        assert seg.valhalla_receipt.distance_m == seg.leg_distance_m
        expected = _routed_seconds(seg.distance_m)
        assert seg.leg_seconds == expected
        # budgets stay haversine: walk_seconds untouched by the client
        assert seg.walk_seconds == pace_corrected_walk_seconds(seg.distance_m)
        assert seg.leg_seconds != seg.walk_seconds  # 1.2x@4km/h vs 1.35x@3km/h


def test_summarise_route_without_client_keeps_haversine_defaults():
    route = summarise_route(
        _two_stop_pois(),
        start_lat=PDV[0],
        start_lng=PDV[1],
        round_trip=False,
        duration_min=60,
        spine_area="Le Marais",
    )
    assert route.routed is False
    for seg in route.transits:
        assert (seg.leg_seconds, seg.polyline, seg.source) == (None, None, "haversine")


def test_summarise_route_client_down_marks_haversine_source():
    with _client(_refusing_handler) as rc:
        route = summarise_route(
            _two_stop_pois(),
            start_lat=PDV[0],
            start_lng=PDV[1],
            round_trip=False,
            duration_min=60,
            spine_area="Le Marais",
            routing_client=rc,
        )
    assert route.routed is False
    for seg in route.transits:
        assert seg.source == "haversine"
        assert seg.polyline is None
        # the client's fallback equals the budget math exactly
        assert seg.leg_seconds == seg.walk_seconds


# ---------------------------------------------------------------------------
# Routed insertion cost (the divisor) + sticky degradation
# ---------------------------------------------------------------------------


def test_transport_failure_degrades_client_permanently():
    """One refused connect must not become thousands in the greedy: after the
    first transport failure the client skips HTTP for its lifetime."""
    calls = {"n": 0}

    def counting_refuser(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("refused", request=request)

    with _client(counting_refuser) as rc:
        (lat1, lng1), (lat2, lng2) = POINTS[0], POINTS[1]
        d = haversine_m(lat1, lng1, lat2, lng2)
        for _ in range(5):
            assert rc.leg_seconds(lat1, lng1, lat2, lng2) == pace_corrected_walk_seconds(d)
        assert rc.isochrone(*POINTS[0], 30) is None
    assert calls["n"] == 1


def test_http_status_error_does_not_degrade():
    """4xx/5xx are per-request problems — the client keeps trying."""
    calls = {"n": 0}

    def flaky_500(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, text="boom")

    with _client(flaky_500) as rc:
        rc.route(*POINTS[0], *POINTS[1])
        rc.route(*POINTS[0], *POINTS[2])
    assert calls["n"] == 2


def test_insertion_cost_uses_leg_seconds_fn():
    from src.tour.routing import insertion_cost_seconds

    a = _poi("a", lat=48.8556, lng=2.3658)
    b = _poi("b", lat=48.8600, lng=2.3700)
    extra, _idx = insertion_cost_seconds(
        b,
        [a],
        start_lat=PDV[0],
        start_lng=PDV[1],
        round_trip=False,
        leg_seconds_fn=lambda lat1, lng1, lat2, lng2: 100,  # flat 100s per leg
    )
    # base path start->a is one leg (100s); any insertion makes two (200s).
    assert extra == 100


def test_memoized_leg_fn_caches_directed_pairs():
    from src.tour.selection import _memoized_leg_fn

    calls = {"n": 0}

    def counting(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _valhalla_handler(request)

    with _client(counting) as rc:
        fn = _memoized_leg_fn(rc)
        first = fn(*POINTS[0], *POINTS[1])
        assert fn(*POINTS[0], *POINTS[1]) == first
        assert calls["n"] == 1
        fn(*POINTS[1], *POINTS[0])  # reverse direction is a distinct leg
        assert calls["n"] == 2


# Routed-divisor inversion fixture. Three POIs standing 20 m from the start that
# the mock road network walls off, while pricing every other leg plausibly. The
# wall is the ONLY thing the two pricings disagree about, so any difference in
# the selection can have come from nothing else.
_WALLED_STOPS = [
    ("near", 48.85568, 2.3656),
    ("c1", 48.855685, 2.3656),
    ("c2", 48.85569, 2.3656),
]
_NEAR_CLUSTER = [(lat, lng) for _, lat, lng in _WALLED_STOPS]
_WALLED_IDS = frozenset(pid for pid, _, _ in _WALLED_STOPS)
# Straight-line 500 m north: past the halfway mark of the 889 m envelope, so the
# one-way endpoint pull can close the route on it. It is the fixture's source of
# WALKING, which since the budget went two-sided on 2026-08-04 is half of whether
# a tour can be planned at all — a tight cluster of stops fills the narration
# allowance and still lands barely two thirds of the requested hour.
_FAR_ANCHOR = (PDV[0] + 0.0045, PDV[1])
#: Seconds per straight-line metre the mock road network charges off the wall.
#: Deliberately unequal to the haversine fallback's 1.62, so a routed leg is
#: always visibly routed, and cheap enough that the walled cluster is the only
#: thing the routed run cannot afford.
_DIVISOR_OPEN_ROAD_S_PER_M = 1.3
#: What the mock road network charges to reach a walled POI, whatever the
#: straight line says. It is longer than the whole hour the tour was asked for,
#: which is the purest form of the case this fixture exists for: 20 m away on the
#: map, more than an hour away on foot. A per-metre wall cannot express that —
#: the cost of walling a POI would fall with the very distance that makes the
#: straight-line planner want it, so a POI close enough to be certainly picked is
#: also too cheap to wall.
_DIVISOR_WALL_SECONDS = 4000


def _divisor_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path != "/route":
        return httpx.Response(404)  # no isochrone: REACH uses the analytic envelope
    body = json.loads(request.content)
    a, b = body["locations"]
    walled = any(
        abs(b["lat"] - lat) < 1e-9 and abs(b["lon"] - lng) < 1e-9
        for lat, lng in _NEAR_CLUSTER
    )
    secs = (
        _DIVISOR_WALL_SECONDS
        if walled
        else round(
            haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
            * _DIVISOR_OPEN_ROAD_S_PER_M
        )
    )
    return httpx.Response(
        200,
        json={"trip": {"legs": [{"summary": {"time": secs, "length": 0.1}, "shape": SHAPE}]}},
    )


def test_routed_divisor_inverts_greedy_choice():
    """When routed times contradict straight-line distance, the greedy follows
    the ROUTED times — selection changes.

    The three POIs the mock road network walls off stand 20 m from the start and
    are the richest thing on offer, so straight-line pricing seats all of them
    first. By road each costs more than the whole hour, so routed pricing seats
    none of them. Everything else in the fixture is scaffolding that lets both
    runs fill the requested hour: a low-scored background cluster supplies stops,
    and one far anchor supplies the walking narration alone can never reach.
    """
    pois = [
        *(
            _poi(pid, lat=lat, lng=lng, areas=("Le Marais",))
            for pid, lat, lng in _WALLED_STOPS
        ),
        _poi("far", lat=_FAR_ANCHOR[0], lng=_FAR_ANCHOR[1], areas=("Le Marais",)),
        # Twelve background stops, pinned (the helper's ``n`` exists for exactly
        # this). Re-derived: the helper's duration-derived count grew from 14
        # to 18 when tellings became three minutes, and at
        # eighteen the straight-line run trades the THIRD colocated walled stop
        # for fillers (measured: bare seated c2 and near, not c1) — a pool-size
        # effect, not the pricing contrast this test is about. At twelve the
        # premise holds as written: bare seats all three, routed seats none.
        *_density_fillers(PDV, radius_m=60.0, tier=3, beat_count=3, n=12),
    ]
    snap = _snap(pois, area_types={"Le Marais": "neighborhood"})
    tour_input = TourInput(start=PDV, duration_min=60, city_slug="paris", round_trip=False)

    bare = {p.id for p in select_route(tour_input, snap).pois}
    with _client(_divisor_handler) as rc:
        routed = {p.id for p in select_route(tour_input, snap, routing_client=rc).pois}

    assert bare >= _WALLED_IDS, (
        f"straight-line pricing must seat the whole near cluster; got {sorted(bare)}"
    )
    assert not (_WALLED_IDS & routed), (
        f"routed pricing must wall the near cluster off entirely; got {sorted(routed)}"
    )
    assert bare != routed  # routed times change the selection


def test_routed_divisor_lets_cheaper_network_fit_more():
    """The client changes selection decisions: insertion costs COME from the
    client. The mock's road network
    (1.2x detour at 4 km/h ≈ 1.08 s/m) is cheaper than the haversine
    fallback's 1.62 s/m, so the same budget fits MORE of the fixture —
    and every transit leg arrives enriched."""
    pois = [*_density_fillers(PDV), *_two_stop_pois()]
    snap = _snap(pois, area_types={"Le Marais": "neighborhood"})
    tour_input = TourInput(start=PDV, duration_min=60, city_slug="paris", round_trip=False)

    bare = select_route(tour_input, snap)
    with _client(_valhalla_handler) as rc:
        routed = select_route(tour_input, snap, routing_client=rc)

    bare_ids = {p.id for p in bare.pois}
    routed_ids = {p.id for p in routed.pois}
    assert bare_ids, "GREEN fixture must select something without the client"
    # The claim is a COUNT, and it is asserted as one. It used to be written as a
    # strict SUPERSET, which was a stronger statement than the divisor makes and
    # a false one: the two runs plan independently, and since the budget went
    # two-sided on 2026-08-04 the repair trades one stop for another to land
    # inside the band, so the cheaper run reaches a higher count by a route that
    # is not the dearer run's route plus extras.
    assert len(routed_ids) > len(bare_ids), (
        f"the cheaper network must fit MORE stops: "
        f"bare={sorted(bare_ids)} routed={sorted(routed_ids)}"
    )
    assert routed_ids - bare_ids, "and at least one of them is a stop bare could not afford"
    assert routed.routed is True
    assert all(seg.leg_seconds is not None for seg in routed.transits)
    assert any(seg.leg_seconds != seg.walk_seconds for seg in routed.transits), (
        "at least one leg must be visibly routed"
    )


def test_requests_pin_pedestrian_walking_speed_to_pace_kmh():
    """Pace pin: Valhalla defaults pedestrians to ~5.1 km/h while the entire
    budget model is spec'd at PACE_KMH = 3.0 (routing.py). Without costing_options every routed
    leg time assumes a walker ~70% faster than the product designs for, and the
    20-min isochrone reaches ~1.7km where the analytic envelope says 738m —
    the divergence behind the density-gate/REACH mismatch. Both request bodies
    must pin walking_speed to the spec'd pace."""
    from src.tour.routing import PACE_KMH

    captured: dict[str, dict] = {}

    def _capturing_handler(request: httpx.Request) -> httpx.Response:
        captured[request.url.path] = json.loads(request.content)
        return _valhalla_handler(request)

    with _client(_capturing_handler) as rc:
        rc.route(*POINTS[0], *POINTS[1])
        rc.isochrone(*POINTS[0], minutes=20)

    for path in ("/route", "/isochrone"):
        body = captured.get(path)
        assert body is not None, f"{path} request never sent"
        speed = body.get("costing_options", {}).get("pedestrian", {}).get("walking_speed")
        assert speed == PACE_KMH, (
            f"{path} must pin costing_options.pedestrian.walking_speed to "
            f"PACE_KMH ({PACE_KMH}); got {speed!r} — Valhalla's ~5.1 km/h "
            f"default silently times legs for a much faster walker"
        )


def test_reach_predicate_falls_back_on_shapely_geos_errors(monkeypatch):
    """Fallback-totality hole: Valhalla's
    isochrone generalization can emit degenerate/self-intersecting rings, and
    shapely then raises GEOSException — which derives ShapelyError, NOT any of
    the (KeyError, TypeError, ValueError) the predicate caught. Pre-fix that
    exception propagated as a 500 instead of degrading to the analytic
    envelope like every other isochrone failure mode."""
    import shapely.errors

    from src.tour import selection as sel

    def _boom(_geom):
        raise shapely.errors.GEOSException("TopologyException: side location conflict")

    monkeypatch.setattr(sel, "_shapely_prep", _boom)

    with _client(_valhalla_handler) as rc:
        contains, degraded = sel._reach_predicate(
            POINTS[0], radius_m=738.0, iso_minutes=20, routing_client=rc
        )

    assert degraded is True, "GEOS failure must degrade to the analytic envelope"
    # The analytic fallback still answers membership sanely: the start itself
    # is inside; a point ~10km away is not.
    assert contains(*POINTS[0]) is True
    assert contains(48.95, 2.5) is False
