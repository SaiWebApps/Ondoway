"""REACH — isochrone membership + ReachVerdict mode mapping. Hermetic.

Extends the density-gate coverage (tests/test_tour_density.py): the gate's
GREEN/YELLOW/RED statuses map to ReachVerdict.mode standard/ambient|redirect/
refuse, and the candidate filter switches from the analytic haversine circle
to the (mocked) Valhalla walking isochrone, radius fallback when degraded.

Fixture math (60 min, the one planning policy since 2026-08-04): the audio
target is 3600 x 1.00 x 0.60 = 2160 s and the walk allocation is 1440 s, so an
hour's tour has to deliver 3240-3960 s of active time. Envelopes are 888.9 m
one-way and 444.4 m round-trip. Every number here used to carry a flat 0.83
factor (1793 s of audio, 738 m / 369 m of envelope) that no longer exists.
"""

from __future__ import annotations

import json

import httpx
import pytest

from src.tour.contract import POI, BeatRef, TourInput
from src.tour.density import TourabilityRefusedError
from src.tour.routing import walk_budget_seconds
from src.tour.routing_client import RoutingClient
from src.tour.selection import (
    VALHALLA_MAX_CONTOUR_MINUTES,
    _isochrone_walk_minutes,
    select_route,
)
from tests.test_tour_selection import PDV, _density_fillers, _poi, _snap


def _beats(poi_id: str, n: int, spoken_s: int) -> list[BeatRef]:
    return [
        BeatRef(
            id=f"{poi_id}-b{i}",
            poi_id=poi_id,
            est_spoken_seconds=spoken_s,
            active_status="active",
        )
        for i in range(n)
    ]


#: Voiced seconds per anchor in the thin pool, and how many anchors carry them.
#: 12 x 174 s = 2088 s of stories against a 2160 s target, so the fill ratio is
#: 0.967 — under 1.0, hence NOT green, and over 0.5, hence YELLOW by fill.
_THIN_ANCHORS: int = 12
_THIN_BEATS_PER_ANCHOR: int = 3
_THIN_SECONDS_PER_BEAT: int = 58


def _thin_cluster(
    prefix: str = "thin", *, round_trip: bool = False
) -> tuple[list[POI], dict[str, list[BeatRef]]]:
    """A pool that is thin on STORIES but still able to fill the hour.

    YELLOW means "there is less to say here than an hour wants, but a tour is
    still worth building", so a YELLOW fixture has to be thin in exactly one way
    and healthy in every other. Thin on audio: 2088 s of stories against the
    2160 s target. Healthy on spread: 12 anchors over the same spiral the green
    fixtures use, so the tour walks the ~1250 s that audio alone cannot supply.

    This used to be four anchors standing 5 m apart carrying 1200 s between
    them. That was YELLOW, but since the walk budget became two-sided on
    2026-08-04 it delivered roughly 850 s of tour against a 3240 s floor and was
    refused outright — so the ambient/redirect disclosure these tests exist to
    check was never reached. Story length is deliberately small enough that a
    whole anchor fits inside one stop's speaking ceiling, which is the only
    regime where the planner's two prices for a stop agree.
    """
    pois = _density_fillers(
        PDV, n=_THIN_ANCHORS, round_trip=round_trip, prefix=prefix
    )
    beats = {
        p.id: _beats(p.id, _THIN_BEATS_PER_ANCHOR, _THIN_SECONDS_PER_BEAT) for p in pois
    }
    return pois, beats


#: Isochrone box: generous to the west of PdV, cut off ~293 m east of it — wide
#: enough to hold the whole background cluster, narrow enough to exclude the
#: across-the-river POI these tests are about. It used to stop 146 m east, which
#: also clipped the far edge of the background spiral once that spiral grew from
#: four colocated POIs to a real one, and turned "the isochrone excludes the POI
#: across the river" into "the isochrone excludes an unrelated filler too".
_BOX_W, _BOX_E = PDV[1] - 0.02, PDV[1] + 0.004
_BOX_S, _BOX_N = PDV[0] - 0.01, PDV[0] + 0.01


def _box_isochrone_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/isochrone":
        ring = [
            [_BOX_W, _BOX_S],
            [_BOX_E, _BOX_S],
            [_BOX_E, _BOX_N],
            [_BOX_W, _BOX_N],
            [_BOX_W, _BOX_S],
        ]
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
    if request.url.path == "/route":
        # Plausible routed legs so the routed divisor works during these tests.
        body = json.loads(request.content)
        a, b = body["locations"]
        from src.tour.routing import haversine_m

        d = haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
        return httpx.Response(
            200,
            json={
                "trip": {
                    "legs": [
                        {"summary": {"time": round(d * 1.3), "length": d / 1000.0},
                         "shape": "iso_mock_shape"}
                    ]
                }
            },
        )
    return httpx.Response(404)


def _client(handler) -> RoutingClient:
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://valhalla.test")
    return RoutingClient(client=http)


def _refusing(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused", request=request)


# ---------------------------------------------------------------------------
# Mode mapping
# ---------------------------------------------------------------------------


def test_green_maps_to_standard():
    snap = _snap(_density_fillers(PDV), area_types={"Le Marais": "neighborhood"})
    inp = TourInput(start=PDV, duration_min=60, city_slug="paris", round_trip=True)
    route = select_route(inp, snap)
    assert route.reach is not None
    assert route.reach.mode == "standard"
    assert route.reach.degraded is True  # no client → analytic envelope
    assert route.tourability is None  # GREEN attaches nothing


def test_thin_yellow_maps_to_ambient():
    pois, beats = _thin_cluster()
    snap = _snap(pois, area_types={"Le Marais": "neighborhood"}, beats_by_poi=beats)
    inp = TourInput(start=PDV, duration_min=60, city_slug="paris", round_trip=False)
    route = select_route(inp, snap)
    assert route.reach.mode == "ambient"
    assert route.reach.alternative_destination is None
    assert route.tourability is not None and route.tourability.status == "YELLOW"


def test_round_trip_yellow_with_gem_maps_to_redirect():
    pois, beats = _thin_cluster(round_trip=True)
    # Tier-5 gem ~500m east: beyond the 444m round-trip envelope, inside the
    # 889m one-way equivalent → density suggests it as the one-way alternative.
    gem = _poi("faraway-gem", lat=PDV[0], lng=PDV[1] + 0.00683, areas=("Le Marais",))
    beats["faraway-gem"] = _beats("faraway-gem", 4, 200)
    snap = _snap([*pois, gem], area_types={"Le Marais": "neighborhood"}, beats_by_poi=beats)
    inp = TourInput(start=PDV, duration_min=60, city_slug="paris", round_trip=True)
    route = select_route(inp, snap)
    assert route.reach.mode == "redirect"
    assert route.reach.alternative_destination == "faraway-gem"


def test_red_still_refuses():
    lone = _poi("lone", lat=PDV[0], lng=PDV[1])
    snap = _snap([lone], beats_by_poi={"lone": _beats("lone", 3, 100)})
    inp = TourInput(start=PDV, duration_min=60, city_slug="paris", round_trip=False)
    with pytest.raises(TourabilityRefusedError):
        select_route(inp, snap)


# ---------------------------------------------------------------------------
# Isochrone membership vs the old radius
# ---------------------------------------------------------------------------


def _east_of_river_pool() -> list[POI]:
    """A background cluster the isochrone box fully contains, one far POI inside
    the box and one far POI outside it — the across-the-river case.

    Three requirements have to hold at once, and the shape below is what
    satisfies all three.

    The far POI must be genuinely SELECTED when the straight-line circle admits
    it, not merely reachable, or the test says nothing about which membership
    test ran. So the background is packed inside 60 m and scored low (tier 3,
    three stories each): it supplies stops and almost no walking, leaving the
    walk allocation free for the one-way endpoint pull to close the route on a
    POI in the far half of the 889 m envelope.

    The tour must also fill the hour BOTH ways round, and a tour is only in band
    if it walks about 1200 s on top of its narration. A background that tight
    cannot supply that on its own, so the far POI is the walking — and the
    isochrone run, which is denied the POI across the river, needs an equally
    far POI it IS allowed to reach. Hence the west-bank twin, inside the box at
    the same distance, one tier lower so the pull prefers the east POI whenever
    the east POI is admitted at all.
    """
    east = _poi("east-of-river", lat=PDV[0], lng=PDV[1] + 0.0070, areas=("Le Marais",))
    west = _poi(
        "west-bank", tier=4, lat=PDV[0], lng=PDV[1] - 0.0070, areas=("Le Marais",)
    )
    background = _density_fillers(PDV, radius_m=60.0, tier=3, beat_count=3)
    return [*background, west, east]


def test_out_of_isochrone_poi_rejected_but_radius_fallback_admits():
    """A POI inside the straight-line envelope but outside the walking
    isochrone (across the river, no bridge) is rejected — the exact case the
    analytic circle got wrong. Without the isochrone it is still admitted."""
    pois = _east_of_river_pool()
    snap = _snap(pois, area_types={"Le Marais": "neighborhood"})
    inp = TourInput(start=PDV, duration_min=60, city_slug="paris", round_trip=False)

    with _client(_box_isochrone_handler) as rc:
        iso_route = select_route(inp, snap, routing_client=rc)
    fallback_route = select_route(inp, snap)

    assert iso_route.reach.degraded is False
    assert "east-of-river" not in [p.id for p in iso_route.pois]
    assert fallback_route.reach.degraded is True
    assert "east-of-river" in [p.id for p in fallback_route.pois]


def test_isochrone_refused_falls_back_degraded_with_same_roster():
    pois = _density_fillers(PDV)
    snap = _snap(pois, area_types={"Le Marais": "neighborhood"})
    inp = TourInput(start=PDV, duration_min=60, city_slug="paris", round_trip=True)

    with _client(_refusing) as rc:
        degraded_route = select_route(inp, snap, routing_client=rc)
    bare_route = select_route(inp, snap)

    assert degraded_route.reach.degraded is True
    assert [p.id for p in degraded_route.pois] == [p.id for p in bare_route.pois]


def test_reachable_poi_count_reflects_isochrone():
    """The count REACH reports is the count the membership test admitted.

    Both numbers are read off the fixture rather than written down. They used to
    be the literals 4 and 5, which described a background cluster of four
    colocated POIs; that cluster has since had to grow into something that can
    fill an hour, and a literal count silently stopped meaning "everything the
    circle admits, and one fewer once the isochrone rules out the far POI"."""
    pois = _east_of_river_pool()
    snap = _snap(pois, area_types={"Le Marais": "neighborhood"})
    inp = TourInput(start=PDV, duration_min=60, city_slug="paris", round_trip=False)

    with _client(_box_isochrone_handler) as rc:
        iso_route = select_route(inp, snap, routing_client=rc)
    fallback_route = select_route(inp, snap)

    # The circle admits every POI in the fixture; the isochrone admits every one
    # of them EXCEPT the POI outside the box.
    assert fallback_route.reach.reachable_poi_count == len(pois)
    assert iso_route.reach.reachable_poi_count == len(pois) - 1


# ---------------------------------------------------------------------------
# Contour minutes
# ---------------------------------------------------------------------------


def test_isochrone_contour_minutes_mirror_walk_budget():
    """The contour REACH asks Valhalla for is the product's own walk allowance,
    halved for an out-and-back.

    Both sides are READ from the live budget rather than written down. They used
    to be the literals 20 and 10, which were 60 x 0.83 x 0.40 under the flat
    planning factor deleted on 2026-08-04; the same 40% allocation is now 24
    minutes, so the literals had stopped describing the product's own budget."""
    budget_min = walk_budget_seconds(60) / 60.0

    assert _isochrone_walk_minutes(60, round_trip=False) == round(budget_min)
    assert _isochrone_walk_minutes(60, round_trip=True) == round(budget_min / 2.0)
    assert _isochrone_walk_minutes(1, round_trip=True) == 1  # floor


def test_reach_verdict_round_trips_on_contract():
    from src.tour.contract import ReachVerdict

    v = ReachVerdict(
        mode="redirect",
        degraded=True,
        walk_minutes=20,
        reachable_poi_count=7,
        alternative_destination="Notre-Dame Cathedral",
    )
    assert ReachVerdict.model_validate(v.model_dump()) == v


def test_the_contour_asked_for_never_exceeds_what_the_engine_will_answer():
    """A contour above Valhalla's maximum is not a bigger polygon — it is no polygon.

    MEASURED against the live engine 2026-08-06: a 120-minute contour is accepted
    and a 121-minute contour is refused with
    ``{"error_code":151,"error":"Exceeded max time: 120"}`` in about 4 ms. REACH
    then falls back to a plain haversine circle with no road network, losing the
    across-the-river protection the polygon exists to provide — silently, because a
    refused isochrone is a degraded reach rather than an error.

    Before the clamp, the request crossed that limit at roughly a 300-minute walk
    on the open arm and at roughly 110 minutes on the fixed-destination arm, which
    is most real tour lengths rather than an exotic edge case.
    """
    # 400 minutes: the walk envelope is 400 x 1.00 nominal x 0.40 = 160 minutes.
    assert _isochrone_walk_minutes(400, round_trip=False) == VALHALLA_MAX_CONTOUR_MINUTES

    # 300 minutes lands exactly ON the maximum, so the polygon is still available.
    assert _isochrone_walk_minutes(300, round_trip=False) == VALHALLA_MAX_CONTOUR_MINUTES

    # Well under the limit, the clamp is inert and the contour still mirrors the
    # walk envelope. A clamp that changed these would shrink every ordinary tour.
    assert _isochrone_walk_minutes(90, round_trip=False) == 36
    assert _isochrone_walk_minutes(60, round_trip=False) == 24
    # Round trips halve the reach, so they need twice the duration to be clamped.
    assert _isochrone_walk_minutes(600, round_trip=True) == VALHALLA_MAX_CONTOUR_MINUTES


def test_every_golden_fixture_sits_below_the_contour_clamp():
    """PROVEN, not assumed: the clamp cannot move a golden tour.

    The clamp is inert at or under a 300-minute request, and this test proves it
    for the fixtures rather than assuming it, because ``_isochrone_walk_minutes``
    is SHARED with the open arm — the clamp could move an open tour, and a golden
    that moved would be absorbed by a re-baseline as expected drift.

    Reads the committed fixture files rather than restating their durations here,
    so adding a longer golden fails this test instead of silently escaping it.
    """
    from pathlib import Path

    fixture_dir = Path(__file__).resolve().parent.parent / "fixtures" / "tour_golden"
    fixtures = sorted(fixture_dir.glob("*.json"))
    assert fixtures, f"no golden fixtures found under {fixture_dir}"

    for path in fixtures:
        payload = json.loads(path.read_text())
        request = payload["input"]
        duration = request["duration_min"]
        round_trip = bool(request.get("round_trip"))
        asked = _isochrone_walk_minutes(duration, round_trip=round_trip)
        unclamped = round(
            (duration * 0.40 / 2.0) if round_trip else (duration * 0.40)
        )
        assert asked == unclamped, (
            f"{path.name} requests {duration} min "
            f"({'round trip' if round_trip else 'one way'}), whose contour "
            f"clamps from {unclamped} to {asked}. The clamp is NOT inert for this "
            f"fixture, so Phase 1 can move this golden tour."
        )
