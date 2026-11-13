"""Audit gate — does the flagship route swim the Seine,
and is its ETA honest?

Two independent geometric/temporal checks over a finished Route:

1. **Seine crossing** — decode each leg's Valhalla polyline (precision 6) to a
   shapely LineString and sum the length that falls inside the Seine water
   polygon with bridge corridors removed. A pedestrian route that uses
   bridges (as Valhalla's network does) scores ~0 m; a route that cuts across
   open water scores hundreds of metres. Geometry fixtures are real
   OpenStreetMap data (``tests/fixtures/geo/seine_paris.geojson`` +
   ``seine_bridges.geojson``), validated against ground-truth points.

2. **ETA error** — |routed_eta - reference| / reference. ``routed_eta`` is the
   honest sum of routed leg seconds + per-stop dwell; the reference is the
   caller's (see SEINE/ETA notes in tests). Pure arithmetic.

Lengths are computed with a local equirectangular metres approximation
(constant scale at Paris latitude) — fine for the sub-kilometre legs here.
"""

from __future__ import annotations

import itertools
import math

from shapely.geometry import LineString, MultiLineString
from shapely.geometry.base import BaseGeometry

# metres per degree at Paris latitude (~48.86°N).
_M_PER_DEG_LAT = 111_320.0
_M_PER_DEG_LNG = 73_200.0


def decode_polyline6(encoded: str) -> list[tuple[float, float]]:
    """Decode a Valhalla precision-6 polyline to ``[(lng, lat), ...]``
    (shapely x=lng, y=lat order)."""
    coords: list[tuple[float, float]] = []
    idx = lat = lng = 0
    factor = 1_000_000
    while idx < len(encoded):
        for axis in range(2):
            shift = result = 0
            while True:
                b = ord(encoded[idx]) - 63
                idx += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if axis == 0:
                lat += delta
            else:
                lng += delta
        coords.append((lng / factor, lat / factor))
    return coords


def _line_metres(line: LineString) -> float:
    total = 0.0
    for (x1, y1), (x2, y2) in itertools.pairwise(line.coords):
        total += math.hypot((x2 - x1) * _M_PER_DEG_LNG, (y2 - y1) * _M_PER_DEG_LAT)
    return total


def _geom_metres(geom: BaseGeometry) -> float:
    if geom.is_empty:
        return 0.0
    if isinstance(geom, LineString):
        return _line_metres(geom)
    if isinstance(geom, MultiLineString):
        return sum(_line_metres(g) for g in geom.geoms)
    # Point/GeometryCollection from a tangential touch — zero length.
    if hasattr(geom, "geoms"):
        return sum(_geom_metres(g) for g in geom.geoms)
    return 0.0


def seine_crossing_metres(
    leg_polylines: list[str],
    water: BaseGeometry,
    bridges: BaseGeometry | None = None,
) -> float:
    """Total metres of the decoded route polylines that fall inside the Seine
    (with ``bridges`` corridors removed). ~0 for a bridge-using route."""
    audit_water = water.difference(bridges) if bridges is not None else water
    inside = 0.0
    for encoded in leg_polylines:
        pts = decode_polyline6(encoded)
        if len(pts) < 2:
            continue
        inside += _geom_metres(LineString(pts).intersection(audit_water))
    return inside


# DELETED 2026-08-06: route_eta_seconds. A fifth, unused definition of "elapsed"
# whose docstring called itself "Honest ETA" while pricing every stop by tier. It
# had no caller in src/, tests/ or scripts/, so it could never have been honest
# about anything. The one definition of elapsed now lives with the planner.


def eta_error(routed_eta_seconds: int, reference_seconds: int) -> float:
    """|routed - reference| / reference; 0.0 when reference is 0."""
    if reference_seconds <= 0:
        return 0.0
    return abs(routed_eta_seconds - reference_seconds) / reference_seconds
