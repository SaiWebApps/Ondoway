"""Spatial utilities for boundary polygons and point-in-polygon checks."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests
from shapely import wkt
from shapely.geometry import MultiPolygon, Point, Polygon

# Overpass is a shared public service: after each request that reaches it, the
# next one waits this long.
_OVERPASS_PAUSE_SECONDS = 1.0


def _boundary_cache_dir(city_slug: str) -> Path:
    return Path("data") / city_slug / "boundaries"


def fetch_osm_boundary(
    osm_relation_id: int, city_slug: str = "paris"
) -> list[tuple[float, float]]:
    """Fetch polygon coordinates from OSM Overpass API for a relation.

    Caches under ``data/{city_slug}/boundaries/`` to avoid repeated API calls;
    a cached boundary returns at once, and only a network fetch pauses
    afterwards for Overpass. Returns list of (lat, lng) tuples.
    """
    cache_dir = _boundary_cache_dir(city_slug)
    cache_file = cache_dir / f"{osm_relation_id}.json"

    if cache_file.exists():
        with open(cache_file) as f:
            return [tuple(coord) for coord in json.load(f)]

    query = f"""
    [out:json][timeout:60];
    relation({osm_relation_id});
    out geom;
    """
    # Retry with exponential backoff for rate limiting
    for attempt in range(4):
        resp = requests.post(
            "https://overpass-api.de/api/interpreter",
            data={"data": query},
            timeout=90,
        )
        if resp.status_code == 429 or resp.status_code == 504:
            wait = 10 * (2**attempt)
            print(f"  Overpass {resp.status_code}, retrying in {wait}s...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        break
    else:
        resp.raise_for_status()  # raise the last error
    data = resp.json()

    coords = _extract_outer_coords(data)
    if not coords:
        raise ValueError(f"No outer boundary found for OSM relation {osm_relation_id}")

    # Cache the result
    cache_dir.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "w") as f:
        json.dump(coords, f)

    time.sleep(_OVERPASS_PAUSE_SECONDS)
    return [tuple(c) for c in coords]


def _extract_outer_coords(
    overpass_data: dict,
) -> list[tuple[float, float]]:
    """Extract outer boundary coordinates from Overpass relation response.

    Returns list of (lat, lng) tuples.
    """
    elements = overpass_data.get("elements", [])
    for element in elements:
        if element.get("type") != "relation":
            continue
        members = element.get("members", [])
        # Collect all "outer" way geometries
        outer_coords: list[tuple[float, float]] = []
        for member in members:
            if member.get("role") == "outer" and "geometry" in member:
                geom = member["geometry"]
                segment = [(pt["lat"], pt["lon"]) for pt in geom]
                outer_coords = _merge_segments(outer_coords, segment)
        if outer_coords:
            # Ensure closed
            if outer_coords[0] != outer_coords[-1]:
                outer_coords.append(outer_coords[0])
            return outer_coords
    return []


def _merge_segments(
    existing: list[tuple[float, float]],
    new_segment: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Merge way segments into a continuous ring."""
    if not existing:
        return new_segment

    # Try to connect: if last point of existing matches first of new
    if existing[-1] == new_segment[0]:
        return existing + new_segment[1:]
    # If last of existing matches last of new, reverse new
    if existing[-1] == new_segment[-1]:
        return existing + list(reversed(new_segment))[1:]
    # If first of existing matches last of new
    if existing[0] == new_segment[-1]:
        return new_segment + existing[1:]
    # If first of existing matches first of new
    if existing[0] == new_segment[0]:
        return list(reversed(new_segment)) + existing[1:]
    # No match — just append (may produce invalid ring, simplify will handle)
    return existing + new_segment


def simplify_polygon(
    coords: list[tuple[float, float]], max_vertices: int = 15
) -> list[tuple[float, float]]:
    """Simplify a polygon to have between 5 and max_vertices vertices.

    Uses Shapely's Douglas-Peucker simplification with increasing tolerance.
    Input/output: list of (lat, lng) tuples.
    """
    # Shapely uses (x, y) = (lng, lat)
    shapely_coords = [(lng, lat) for lat, lng in coords]
    poly = Polygon(shapely_coords)

    if not poly.is_valid:
        poly = poly.buffer(0)  # fix self-intersections
        if isinstance(poly, MultiPolygon):
            poly = max(poly.geoms, key=lambda g: g.area)

    current_vertices = len(poly.exterior.coords) - 1  # exclude closing point

    if current_vertices <= max_vertices:
        result = list(poly.exterior.coords)
    else:
        tolerance = 0.0001
        for _ in range(50):
            simplified = poly.simplify(tolerance, preserve_topology=True)
            if isinstance(simplified, MultiPolygon):
                simplified = max(simplified.geoms, key=lambda g: g.area)
            n = len(simplified.exterior.coords) - 1
            if 5 <= n <= max_vertices:
                result = list(simplified.exterior.coords)
                break
            if n < 5:
                # Went too far — back off
                tolerance *= 0.7
            else:
                tolerance *= 1.5
        else:
            # Fallback: just take the simplified result
            result = list(simplified.exterior.coords)

    # Convert back to (lat, lng) and ensure closed
    lat_lng = [(y, x) for x, y in result]
    if lat_lng[0] != lat_lng[-1]:
        lat_lng.append(lat_lng[0])
    return lat_lng


def coords_to_wkt(coords: list[tuple[float, float]]) -> str:
    """Convert (lat, lng) coordinate pairs to WKT POLYGON string.

    WKT uses (longitude latitude) ordering.
    Ensures the polygon is closed (first vertex = last vertex).
    """
    if coords[0] != coords[-1]:
        coords = [*coords, coords[0]]

    # WKT format: POLYGON((lng lat, lng lat, ...))
    pairs = [f"{lng} {lat}" for lat, lng in coords]
    return f"POLYGON(({', '.join(pairs)}))"


def point_in_polygon(lat: float, lng: float, wkt_str: str, buffer_deg: float = 0.0) -> bool:
    """Test if a point is inside a WKT polygon.

    Args:
        buffer_deg: Optional buffer in degrees to account for polygon
            simplification artifacts. 0.001 deg ≈ 100m at Paris latitude.

    Note: Shapely uses (x=lng, y=lat) ordering.
    """
    poly = wkt.loads(wkt_str)
    if buffer_deg > 0:
        poly = poly.buffer(buffer_deg)
    point = Point(lng, lat)
    return poly.covers(point)


def point_in_areas(lat: float, lng: float, buffer_deg: float = 0.00075) -> list[dict]:
    """Find all Areas containing a given point by querying Neo4j.

    Args:
        buffer_deg: Buffer in degrees for polygon simplification tolerance.
            Default 0.00075 deg ≈ 83m at Paris latitude.

    Returns list of dicts with name, area_type, city_name for matching areas.
    """
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        auth=(
            os.environ.get("NEO4J_USER", "neo4j"),
            os.environ.get("NEO4J_PASSWORD", "ondoway_dev_2026"),
        ),
    )
    try:
        with driver.session() as session:
            result = session.run(
                "MATCH (a:Area) WHERE a.boundary IS NOT NULL "
                "RETURN a.name AS name, a.area_type AS area_type, "
                "a.city_name AS city_name, a.boundary AS boundary"
            ).data()

        matches = []
        for area in result:
            if point_in_polygon(lat, lng, area["boundary"], buffer_deg=buffer_deg):
                matches.append(
                    {
                        "name": area["name"],
                        "area_type": area["area_type"],
                        "city_name": area["city_name"],
                    }
                )
        return matches
    finally:
        driver.close()
