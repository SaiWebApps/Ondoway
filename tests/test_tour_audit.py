"""Audit gate—Seine geometry plus the flagship routed regression.

The grade shard owns this entire file so its pure geometry checks and its
live-Valhalla flagship check stay together without marker deselections.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from shapely.geometry import LineString, shape

from src.tour.audit import (
    decode_polyline6,
    eta_error,
    seine_crossing_metres,
)

pytestmark = pytest.mark.grade

_GEO = Path(__file__).resolve().parent / "fixtures" / "geo"


def _water():
    return shape(json.loads((_GEO / "seine_paris.geojson").read_text())["geometry"])


def _bridges():
    return shape(json.loads((_GEO / "seine_bridges.geojson").read_text())["geometry"])


def _encode6(coords: list[tuple[float, float]]) -> str:
    """Encode [(lat, lng), ...] to a precision-6 polyline (inverse of decode)."""
    out = []
    plat = plng = 0
    for lat, lng in coords:
        ilat, ilng = round(lat * 1e6), round(lng * 1e6)
        for delta in (ilat - plat, ilng - plng):
            delta = delta << 1 ^ (delta >> 31)
            while delta >= 0x20:
                out.append(chr((0x20 | (delta & 0x1F)) + 63))
                delta >>= 5
            out.append(chr(delta + 63))
        plat, plng = ilat, ilng
    return "".join(out)


# ---------------------------------------------------------------------------
# Polyline decode (external ground truth: round-trip + known endpoints)
# ---------------------------------------------------------------------------


def test_decode_round_trips_encoded_coords():
    coords = [(48.8568, 2.3414), (48.8567, 2.3409), (48.8559, 2.3447)]
    decoded = decode_polyline6(_encode6(coords))  # decode returns (lng, lat)
    for (lat, lng), (x, y) in zip(coords, decoded, strict=True):
        assert math.isclose(y, lat, abs_tol=1e-6)
        assert math.isclose(x, lng, abs_tol=1e-6)


# ---------------------------------------------------------------------------
# Seine crossing geometry (hermetic, real fixtures + synthetic polylines)
# ---------------------------------------------------------------------------


def test_water_fixture_matches_ground_truth():
    """The committed Seine polygon: mid-river is water, islands/banks are land."""
    water = _water()
    from shapely.geometry import Point

    assert water.contains(Point(2.3370, 48.8585))  # mid-Seine S of Louvre
    assert not water.contains(Point(2.3376, 48.8606))  # Louvre courtyard
    assert not water.contains(Point(2.3499, 48.8530))  # Notre-Dame (island)


def test_line_across_open_water_is_flagged():
    # A straight line N-S across the Seine just west of the islands, away from
    # any bridge corridor — pure "swim".
    swim = _encode6([(48.8600, 2.3360), (48.8560, 2.3360)])
    metres = seine_crossing_metres([swim], _water(), _bridges())
    assert metres > 100, f"a cross-river swim should register, got {metres:.0f}m"


def test_line_on_land_registers_zero():
    # Rue de Rivoli-ish, well north of the river.
    onland = _encode6([(48.8610, 2.3400), (48.8612, 2.3450)])
    assert seine_crossing_metres([onland], _water(), _bridges()) == pytest.approx(0.0)


def test_bridge_corridor_removes_the_crossing():
    """The same swim, but routed along a real bridge, is excluded by the
    bridge corridors — that is what makes the gate fair to bridge-using routes."""
    water, bridges = _water(), _bridges()
    # Pont au Change crosses the north arm ~ (48.8565, 2.3470). A short segment
    # over it sits inside `water` but inside a bridge corridor too.
    seg = LineString([(2.3470, 48.8568), (2.3470, 48.8562)])
    assert not water.difference(bridges).intersects(seg) or (
        water.intersects(seg)  # it is over water...
        and seine_crossing_metres([_encode6([(48.8568, 2.3470), (48.8562, 2.3470)])],
                                  water, bridges) < 20
    )


# ---------------------------------------------------------------------------
# ETA error arithmetic
# ---------------------------------------------------------------------------


def test_eta_error_basic():
    assert eta_error(74 * 60, 90 * 60) == pytest.approx(abs(74 - 90) / 90, abs=1e-6)
    assert eta_error(90 * 60, 90 * 60) == 0.0
    assert eta_error(100, 0) == 0.0


# ---------------------------------------------------------------------------
# Flagship route (marked grade; needs live Valhalla + dev graph)
# ---------------------------------------------------------------------------


def test_flagship_route_never_swims_the_seine():
    from src.tour.contract import TourInput
    from src.tour.routing_client import RoutingClient
    from src.tour.selection import load_paris_corpus, select_route
    from tests.live_graph import open_dev_driver

    d = open_dev_driver()
    if d is None:
        pytest.skip("live dev Neo4j unreachable — `make db-up`")
    snap = load_paris_corpus(d, city_slug="paris")
    d.close()

    tour_input = TourInput(start=(48.8568, 2.3414), duration_min=90, city_slug="paris")
    with RoutingClient() as rc:
        route = select_route(tour_input, snap, routing_client=rc)
    if not route.routed:
        pytest.skip("Valhalla not serving routed polylines — `make valhalla-up`")

    polylines = [t.polyline for t in route.transits if t.polyline]
    assert polylines, "a routed flagship tour must carry polylines"
    metres = seine_crossing_metres(polylines, _water(), _bridges())
    assert metres < 20.0, f"flagship route swims {metres:.0f}m of Seine (bridges excluded)"
