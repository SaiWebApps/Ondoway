"""Valhalla pedestrian routing client with haversine fallback (M1).

Primary engine: Valhalla at ``VALHALLA_URL`` (the Render private service in
production; Docker via ``make valhalla-up`` locally, port 8002) speaking the
documented JSON API:

- POST /route      {locations: [{lat, lon}, ...], costing: "pedestrian",
                    units: "kilometers"} ->
                    trip.legs[0].summary.time (seconds),
                    trip.legs[0].summary.length (KILOMETERS),
                    trip.legs[0].shape (encoded polyline, 6-digit precision)
- POST /isochrone  {locations: [{lat, lon}], costing: "pedestrian",
                    contours: [{time: minutes}], polygons: true} ->
                    GeoJSON FeatureCollection

Every public method degrades to the pure-math fallback —
``pace_corrected_walk_seconds`` over ``haversine_m`` (src/tour/routing.py) —
when Valhalla is unreachable or answers garbage, so the engine and the test
suite work with no container running. A ``None`` polyline (route) or ``None``
result (isochrone) is the caller's signal that the fallback produced the
numbers, which ``summarise_route`` surfaces as ``TransitSegment.source``.
"""

from __future__ import annotations

import hashlib
import os

import httpx

from .contract import ValhallaLegReceipt
from .corpus_places import canonical_json
from .routing import PACE_KMH, REACH_PACE_KMH, haversine_m, pace_corrected_walk_seconds

# Pace pin (2026-07-02, hostile-panel finding): Valhalla defaults pedestrians to
# ~5.1 km/h, and every leg time the engine reports must come from the same walker
# its budgets assume, or a routed leg and its haversine fallback describe two
# different people. `PACE_KMH` was 3.0 until 2026-08-06 and is now 4.5, which is
# what people actually walk; the reach contour deliberately did NOT follow it (see
# `_REACH_COSTING_OPTIONS`).
_PEDESTRIAN_COSTING_OPTIONS = {"pedestrian": {"walking_speed": PACE_KMH}}
#: THE ISOCHRONE IS THE REACH TEST, and it must not widen when the pace does.
#:
#: `_reach_predicate` uses the Valhalla polygon as PRIMARY and the analytic circle
#: only as a fallback, so pinning the circle to `REACH_PACE_KMH` and leaving the
#: contour on `PACE_KMH` would report a fixed radius while the region actually
#: admitted grew by half — the change reading green while doing the opposite of what
#: it claims. At a 120-minute contour that is roughly 6 km of path at 3.0 km/h and
#: roughly 9 km at 4.5.
_REACH_COSTING_OPTIONS = {"pedestrian": {"walking_speed": REACH_PACE_KMH}}
_ROUTING_CONFIG = {
    "costing": "pedestrian",
    "costing_options": _PEDESTRIAN_COSTING_OPTIONS,
    "units": "kilometers",
}

#: What each `TourInput.route_surface` axis value asks Valhalla for, per
#: W2.1's live capability proof (redesign §2.4; plan S2.7; the redesign's
#: spec tree is retired — git history): a real request
#: over the Rue Foyatier stairs (Place Saint-Pierre -> Sacre-Coeur forecourt)
#: measurably swung the route around the butte under `step_penalty` alone
#: (+878 m, +1060 s) and under `step_penalty` + `type: "wheelchair"` combined
#: (+888 m) — probed live, not read from documentation. `max_grade` and
#: `use_hills` were ALSO probed on the same pair and recorded INERT; they are
#: deliberately absent here. "any" carries no override — today's requests,
#: byte-identical.
ROUTE_SURFACE_COSTING_OVERRIDES: dict[str, dict | None] = {
    "any": None,
    "no_stairs": {"step_penalty": 3600},
    "step_free": {"step_penalty": 3600, "type": "wheelchair"},
}


VALHALLA_ROUTING_CONFIG_JSON = canonical_json(_ROUTING_CONFIG)
VALHALLA_ROUTING_CONFIG_SHA256 = hashlib.sha256(
    VALHALLA_ROUTING_CONFIG_JSON.encode("utf-8")
).hexdigest()

#: The hashes of THIS build's routing configuration under every route-surface
#: override it can ask for (W5.14, Rosemary: a step-free day was labelled "worked out
#: with different settings than this version expects" on every leg — the check
#: expected only the default). A receipt carrying any of these was routed by this
#: build's settings; one carrying none was not.
VALHALLA_ROUTING_CONFIG_SHA256_BY_SURFACE: dict[str, str] = {
    surface: (
        VALHALLA_ROUTING_CONFIG_SHA256
        if override is None
        else hashlib.sha256(
            canonical_json(
                {
                    **_ROUTING_CONFIG,
                    "costing_options": {
                        "pedestrian": {
                            **_PEDESTRIAN_COSTING_OPTIONS["pedestrian"],
                            **override,
                        }
                    },
                }
            ).encode("utf-8")
        ).hexdigest()
    )
    for surface, override in ROUTE_SURFACE_COSTING_OVERRIDES.items()
}
THIS_BUILDS_ROUTING_CONFIG_SHA256S: frozenset[str] = frozenset(
    VALHALLA_ROUTING_CONFIG_SHA256_BY_SURFACE.values()
)

DEFAULT_BASE_URL = "http://localhost:8002"
BASE_URL_ENV = "VALHALLA_URL"
DEFAULT_TIMEOUT_S = 2.0
_KM_TO_M = 1000.0

# Any transport problem, bad HTTP status, or unparseable/missing field falls
# back — the fallback must be total or selection dies when the container is
# down. json.JSONDecodeError subclasses ValueError.
_FALLBACK_ERRORS = (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError)


class RoutingClient:
    """Pedestrian leg times/shapes from Valhalla, haversine when it's away."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        client: httpx.Client | None = None,
    ):
        # An injected client (tests use httpx.MockTransport) carries its own
        # base_url/timeout; base_url/timeout_s apply only to the owned one.
        resolved_base_url = base_url or os.getenv(BASE_URL_ENV, DEFAULT_BASE_URL)
        if "://" not in resolved_base_url:
            resolved_base_url = f"http://{resolved_base_url}"
        self._client = client or httpx.Client(base_url=resolved_base_url, timeout=timeout_s)
        # M3: sticky degradation. The greedy makes hundreds-to-thousands of
        # leg calls per request; once a TRANSPORT failure proves Valhalla is
        # away, stop attempting HTTP for this instance's lifetime. Bad
        # responses (4xx/5xx/garbage) do NOT degrade — those are per-request.
        self._degraded = False
        self._routing_version: str | None = None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> RoutingClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def leg_seconds(
        self,
        from_lat: float,
        from_lng: float,
        to_lat: float,
        to_lng: float,
        *,
        costing_options_override: dict | None = None,
    ) -> int:
        """Walking seconds for one leg (routed, else pace-corrected haversine)."""
        seconds, _meters, _shape = self.route(
            from_lat, from_lng, to_lat, to_lng, costing_options_override=costing_options_override
        )
        return seconds

    def route(
        self,
        from_lat: float,
        from_lng: float,
        to_lat: float,
        to_lng: float,
        *,
        costing_options_override: dict | None = None,
    ) -> tuple[int, float, str | None]:
        """(walk_seconds, distance_m, encoded_polyline6 | None) for one leg.

        ``None`` polyline means the haversine fallback produced the numbers
        (distance_m is then the straight-line metres, matching what
        summarise_route records today).
        """
        seconds, distance_m, shape, _receipt = self.route_with_receipt(
            from_lat,
            from_lng,
            to_lat,
            to_lng,
            costing_options_override=costing_options_override,
        )
        return seconds, distance_m, shape

    def route_with_receipt(
        self,
        from_lat: float,
        from_lng: float,
        to_lat: float,
        to_lng: float,
        *,
        costing_options_override: dict | None = None,
    ) -> tuple[int, float, str | None, ValhallaLegReceipt | None]:
        """Route one leg and retain replayable evidence when Valhalla succeeds.

        Fallback behavior remains byte-for-byte compatible with :meth:`route`;
        a ``None`` receipt is the explicit signal that no Valhalla result exists.

        ``costing_options_override`` (plan S2.7; W2.1 proved live: `step_penalty`
        genuinely moves a route off the Rue Foyatier stairs) merges INTO the
        pedestrian costing for THIS request only — ``None`` (the default) keeps
        ``_ROUTING_CONFIG``, byte-identical to every caller before this
        parameter existed. Never mutates the module-level ``_ROUTING_CONFIG`` /
        ``_PEDESTRIAN_COSTING_OPTIONS``, which would repoint every request
        including the reach contour and change ``VALHALLA_ROUTING_CONFIG_SHA256``
        for tours that never asked for a surface constraint. The receipt's
        ``routing_config_json``/``routing_config_sha256`` are rebuilt from the
        OVERRIDDEN config rather than the module constants when an override
        rides — ``ValhallaLegReceipt._canonical_payloads_match_fields`` derives
        the config it expects from the request itself (strips ``locations``,
        hashes the rest), so a receipt claiming the default config while an
        override actually rode the request would fail that check, correctly.
        """
        if self._degraded:
            seconds, distance_m, shape = self._route_fallback(from_lat, from_lng, to_lat, to_lng)
            return seconds, distance_m, shape, None

        if costing_options_override is None:
            routing_config = _ROUTING_CONFIG
            routing_config_json = VALHALLA_ROUTING_CONFIG_JSON
            routing_config_sha256 = VALHALLA_ROUTING_CONFIG_SHA256
        else:
            routing_config = {
                **_ROUTING_CONFIG,
                "costing_options": {
                    "pedestrian": {
                        **_PEDESTRIAN_COSTING_OPTIONS["pedestrian"],
                        **costing_options_override,
                    }
                },
            }
            routing_config_json = canonical_json(routing_config)
            routing_config_sha256 = hashlib.sha256(
                routing_config_json.encode("utf-8")
            ).hexdigest()
        request_payload = {
            "locations": [
                {"lat": from_lat, "lon": from_lng},
                {"lat": to_lat, "lon": to_lng},
            ],
            **routing_config,
        }
        try:
            resp = self._client.post(
                "/route",
                json=request_payload,
            )
            resp.raise_for_status()
            response_payload = resp.json()
            leg = response_payload["trip"]["legs"][0]
            seconds = round(leg["summary"]["time"])
            distance_m = float(leg["summary"]["length"]) * _KM_TO_M
            shape = leg["shape"]
            request_json = canonical_json(request_payload)
            response_json = canonical_json(response_payload)
            receipt = ValhallaLegReceipt(
                requested_from=(from_lat, from_lng),
                requested_to=(to_lat, to_lng),
                request_json=request_json,
                request_sha256=hashlib.sha256(request_json.encode("utf-8")).hexdigest(),
                routing_config_json=routing_config_json,
                routing_config_sha256=routing_config_sha256,
                response_json=response_json,
                response_sha256=hashlib.sha256(response_json.encode("utf-8")).hexdigest(),
                seconds=seconds,
                distance_m=distance_m,
                polyline=shape,
            )
            return seconds, distance_m, shape, receipt
        except httpx.TransportError:
            self._degraded = True
            seconds, distance_m, shape = self._route_fallback(from_lat, from_lng, to_lat, to_lng)
            return seconds, distance_m, shape, None
        except _FALLBACK_ERRORS:
            seconds, distance_m, shape = self._route_fallback(from_lat, from_lng, to_lat, to_lng)
            return seconds, distance_m, shape, None

    def routing_version(self) -> str:
        """Return the Valhalla version from this exact client instance.

        Premium provenance must bind route receipts to the engine that produced
        them. Missing or malformed status is therefore an error at the Premium
        boundary; ordinary Basic routing remains independently fallback-safe.
        """

        if self._routing_version is not None:
            return self._routing_version
        response = self._client.get("/status")
        response.raise_for_status()
        payload = response.json()
        version = payload.get("version")
        if not isinstance(version, str) or not version.strip():
            raise ValueError("Valhalla status omitted a routing version")
        self._routing_version = version.strip()
        return self._routing_version

    @staticmethod
    def _route_fallback(
        from_lat: float, from_lng: float, to_lat: float, to_lng: float
    ) -> tuple[int, float, None]:
        d = haversine_m(from_lat, from_lng, to_lat, to_lng)
        return (pace_corrected_walk_seconds(d), d, None)

    def isochrone(
        self, lat: float, lng: float, minutes: int, *, walking_speed_kmh: float | None = None
    ) -> dict | None:
        """GeoJSON FeatureCollection of the walking isochrone, or ``None``.

        ``None`` tells the caller to keep using the analytic envelope radius
        (``envelope_radius_m``) — the pre-M1 behavior.

        ``walking_speed_kmh`` (plan S2.4) overrides the request's pedestrian
        speed for THIS call only — the mechanism a slow party's pace shrinks
        the reach polygon with, so the road-network admission test and the
        analytic circle (``envelope_radius_m(..., pace_multiplier=...)``)
        shrink together rather than one silently staying full-size while the
        other reports a shrink that never happened. ``None`` (the default)
        keeps ``_REACH_COSTING_OPTIONS`` — today's behaviour, byte-identical.
        """
        if self._degraded:
            return None
        costing_options = (
            _REACH_COSTING_OPTIONS
            if walking_speed_kmh is None
            else {"pedestrian": {"walking_speed": walking_speed_kmh}}
        )
        try:
            resp = self._client.post(
                "/isochrone",
                json={
                    "locations": [{"lat": lat, "lon": lng}],
                    "costing": "pedestrian",
                    "costing_options": costing_options,
                    "contours": [{"time": float(minutes)}],
                    "polygons": True,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("type") != "FeatureCollection" or not data.get("features"):
                return None
            return data
        except httpx.TransportError:
            self._degraded = True
            return None
        except _FALLBACK_ERRORS:
            return None
