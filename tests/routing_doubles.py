"""Hermetic routing doubles whose EXACT legs can DIFFER from the estimate.

Every other routing double in this repo — ``_DeterministicRoutingClient``
(tests/test_tour_b_materialization.py) and ``_FakeRoutingClient``
(tests/test_trip_preview_contract.py) — is DEFINED to make the street-routed
number equal the pace-corrected haversine, so the planner's estimate and its exact
legs agree by construction and no test can tell them apart (measured live, the
gap runs -187 s to +285 s per leg on real Paris streets and flips sign by
neighbourhood). Editing those doubles in place would silently re-point every suite
that imports them, so this file is the home for a double that diverges on purpose.

``DivergentRoutingClient`` answers the planner's TWO questions differently, the way
a stale or differently-costed leg pricing would: ``leg_seconds`` (what selection
prices with) returns the plain estimate; ``route`` / ``route_with_receipt`` (what the
final ``summarise_route`` reads) return ``factor x estimate`` WITH a polyline, so the
transit's ``source == "valhalla"`` and ``routing.leg_walk_seconds`` prefers the exact
number. A certification written on ``walk_seconds`` cannot see the difference; one
written on ``leg_walk_seconds`` can — which is the whole point of the double.
"""

from __future__ import annotations

from src.tour.routing import haversine_m, pace_corrected_walk_seconds


class DivergentRoutingClient:
    """Routed legs = ``factor`` x the estimate, carrying a polyline; estimate unchanged."""

    def __init__(self, factor: float = 1.0) -> None:
        self.factor = factor
        self.exact_calls = 0

    # -- the context-manager shape the API path uses -----------------------------
    def __enter__(self) -> DivergentRoutingClient:
        return self

    def __exit__(self, *exc) -> None:
        return None

    def close(self) -> None:
        return None

    # -- what SELECTION prices with: the plain estimate ----------------------------
    def leg_seconds(
        self, from_lat: float, from_lng: float, to_lat: float, to_lng: float, **_kw
    ) -> int:
        return int(pace_corrected_walk_seconds(haversine_m(from_lat, from_lng, to_lat, to_lng)))

    # -- what the FINAL route reads: the exact street number, with a polyline ------
    def route(
        self, from_lat: float, from_lng: float, to_lat: float, to_lng: float, **_kw
    ) -> tuple[int, float, str]:
        d = haversine_m(from_lat, from_lng, to_lat, to_lng)
        self.exact_calls += 1
        return (round(pace_corrected_walk_seconds(d) * self.factor), d, "divergent-polyline")

    def route_with_receipt(
        self, from_lat: float, from_lng: float, to_lat: float, to_lng: float, **_kw
    ) -> tuple[int, float, str, None]:
        seconds, distance_m, shape = self.route(from_lat, from_lng, to_lat, to_lng)
        return seconds, distance_m, shape, None

    def isochrone(self, lat: float, lng: float, minutes: int) -> None:
        return None
