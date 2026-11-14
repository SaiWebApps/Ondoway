"""REACH must reject a DEGENERATE isochrone and fall back to the haversine
envelope.

A Valhalla with no tiles for the requested region (New York on a Paris-only
tileset) cannot snap the origin to an edge and returns a near-point polygon
(~0.03 km² vs the ~5 km² of a real 30-min walk). Trusting it collapses REACH to
the few co-located start POIs, so the greedy seats a 1-stop tour. This guards the
2026-07-12 fix: an implausibly-small isochrone is discarded for the analytic
envelope. Paris (a real, large isochrone) is unaffected — proven by golden-probe.
"""

from __future__ import annotations

from src.tour.selection import _reach_predicate


def _square_iso(lng: float, lat: float, half_deg: float) -> dict:
    """A square Polygon isochrone centred on (lat,lng); GeoJSON rings are (lng,lat)."""
    ring = [
        [lng - half_deg, lat - half_deg],
        [lng + half_deg, lat - half_deg],
        [lng + half_deg, lat + half_deg],
        [lng - half_deg, lat + half_deg],
        [lng - half_deg, lat - half_deg],
    ]
    return {"features": [{"geometry": {"type": "Polygon", "coordinates": [ring]}}]}


class _FakeRC:
    def __init__(self, iso):
        self._iso = iso

    def isochrone(self, lat, lng, minutes, *, walking_speed_kmh=None):
        return self._iso


# NYC lower-manhattan; radius_m ~= a 150-min round-trip envelope.
_START = (40.7069, -74.0113)
_RADIUS_M = 922.0
_POI_500M = (40.7115, -74.0090)  # ~500m from start: outside a tiny iso, inside the envelope


def test_degenerate_isochrone_falls_back_to_haversine():
    # ~110m square (~0.01 km²) — far below 0.25 x the envelope circle.
    tiny = _square_iso(-74.0113, 40.7069, 0.0005)
    contains, degraded = _reach_predicate(_START, _RADIUS_M, 30, _FakeRC(tiny))
    assert degraded is True  # rejected the degenerate iso
    assert contains(*_POI_500M)  # a 500m POI IS reachable via the envelope fallback


def test_real_isochrone_is_used_verbatim():
    # ~4.4km square (~13 km²) — a plausible real walk; the polygon governs.
    big = _square_iso(-74.0113, 40.7069, 0.02)
    contains, degraded = _reach_predicate(_START, _RADIUS_M, 30, _FakeRC(big))
    assert degraded is False  # trusted the real iso
    assert contains(*_POI_500M)  # inside the big polygon
    assert not contains(41.5, -73.0)  # far outside: the polygon (not haversine) governs


def test_no_isochrone_still_falls_back():
    contains, degraded = _reach_predicate(_START, _RADIUS_M, 30, _FakeRC(None))
    assert degraded is True
    assert contains(*_POI_500M)
