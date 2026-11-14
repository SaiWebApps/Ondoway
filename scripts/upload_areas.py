#!/usr/bin/env python3
"""Upload a city's Area hierarchy + WITHIN edges to Neo4j.

City-parameterized (multi-city safe). Does, idempotently (all MERGE-based):
  1. Area nodes        <- data/{slug}/areas.json (boundary from OSM cache or manual_boundary)
  2. Area->Area WITHIN <- areas.json parent_area chain
  3. POI->Area WITHIN  <- data/{slug}/within_edges.json (poi_to_area)

It writes over the driver through the API's own construction code
(``crud.create_node`` for an Area, ``crud.create_edges`` for WITHIN), and only
what differs from the graph: an Area whose written fields already match, and a
WITHIN edge that already exists, are not written.

Prereq: the city's POIs already uploaded (step 1 of ``make deploy CITY=...``).
The same local-only guard as the POI upload applies; ``--allow-cloud`` lifts it.

Usage:
    python scripts/upload_areas.py --slug new_york
"""
from __future__ import annotations

import argparse
import json
import sys
from functools import partial
from pathlib import Path

from shapely.geometry import Polygon

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.upload_paris import (
    _assert_upload_target_allowed,
    same_value,
    write_in_chunks,
)
from src.api.crud.edges import create_edges
from src.api.crud.nodes import create_node
from src.api.models.nodes import AreaCreate
from src.city_registry import onboard_data_root
from src.connection import abort_on_connection_error, create_driver, get_database
from src.utils.spatial import coords_to_wkt, fetch_osm_boundary, simplify_polygon

# The Area payload keys create_node does not copy onto the node under their own
# name: the MERGE key, and the coordinates that become `centroid`.
_AREA_PAYLOAD_NOT_COPIED = {"name", "area_type", "city_name", "centroid_lat", "centroid_lng"}


def _city_data_dir(slug: str) -> Path:
    """The city's corpus dir under the hermetic-aware data root
    (``$ONBOARD_DATA_ROOT`` when set, else ``<repo>/data``)."""
    return onboard_data_root() / slug


def get_boundary(area: dict, slug: str) -> list[tuple[float, float]]:
    """Boundary coords as (lat, lng) — from manual_boundary or the OSM cache."""
    if area.get("manual_boundary"):
        coords = [tuple(c) for c in area["manual_boundary"]]
        if coords[0] != coords[-1]:
            coords.append(coords[0])
        return coords
    osm_id = area.get("osm_relation_id")
    if not osm_id:
        raise ValueError(f"No boundary source for {area['name']}")
    return fetch_osm_boundary(osm_id, city_slug=slug)


def compute_centroid(coords: list[tuple[float, float]]) -> tuple[float, float]:
    poly = Polygon([(lng, lat) for lat, lng in coords])
    c = poly.centroid
    return (c.y, c.x)


def area_payload(area: dict, slug: str) -> dict:
    """The validated create payload for one Area, exactly what the API's
    ``POST /nodes/Area`` would hand to ``create_node``."""
    simplified = simplify_polygon(get_boundary(area, slug))
    centroid = compute_centroid(simplified)
    return AreaCreate(
        name=area["name"],
        area_type=area["area_type"],
        city_name=area["city_name"],
        boundary=coords_to_wkt(simplified),
        centroid_lat=centroid[0],
        centroid_lng=centroid[1],
        short_description="",
    ).model_dump()


def area_changed(payload: dict, props: dict | None) -> bool:
    """Whether ``create_node`` would change the graph Area ``props``: a new Area,
    or any field it writes that differs."""
    if props is None:
        return True
    centroid = props.get("centroid")
    if centroid is None or (centroid.latitude, centroid.longitude) != (
        payload["centroid_lat"],
        payload["centroid_lng"],
    ):
        return True
    return any(
        not same_value(props.get(k), v)
        for k, v in payload.items()
        if k not in _AREA_PAYLOAD_NOT_COPIED
    )


def _graph_areas(session, city_name: str) -> dict[tuple[str, str], dict]:
    """(name, area_type) -> properties of this city's Area nodes."""
    return {
        (r["props"]["name"], r["props"]["area_type"]): dict(r["props"])
        for r in session.run(
            "MATCH (a:Area {city_name: $city}) RETURN properties(a) AS props", city=city_name
        )
    }


def upload_areas(driver, areas: list[dict], slug: str) -> dict[str, str]:
    """Create or update the Area nodes that differ from the graph; return
    {name: id} for every Area the graph now holds."""
    type_order = {"city": 0, "borough": 1, "district": 1, "neighborhood": 2,
                  "island": 2, "corridor": 2}
    areas = sorted(areas, key=lambda a: type_order.get(a["area_type"], 3))

    ids: dict[str, str] = {}
    written = 0
    print("=== Area nodes ===")
    with driver.session(database=get_database()) as session:
        graph = _graph_areas(session, areas[0]["city_name"])
        for area in areas:
            name = area["name"]
            try:
                payload = area_payload(area, slug)
                props = graph.get((name, area["area_type"]))
                if not area_changed(payload, props):
                    ids[name] = props["id"]
                    continue
                node = session.execute_write(lambda tx, p=payload: create_node(tx, "Area", p))
                ids[name] = node["id"]
                written += 1
                print(f"  [OK] {name} ({area['area_type']})")
            except Exception as e:
                print(f"  [FAIL] {name}: {e}")
    print(f"  {written} of {len(areas)} areas changed")
    return ids


def _write_within_chunk(tx, chunk: list[dict], source_label: str) -> int:
    return len(create_edges(tx, "WITHIN", source_label, "Area", chunk))


def _graph_within(session, city_name: str) -> set[tuple[str, str]]:
    """(source id, area id) of every WITHIN edge into this city's Areas."""
    return {
        (r["s"], r["a"])
        for r in session.run(
            "MATCH (s)-[:WITHIN]->(a:Area {city_name: $city}) RETURN s.id AS s, a.id AS a",
            city=city_name,
        )
    }


def upload_within(
    driver, source_label: str, pairs: list[tuple[str, str]], existing: set[tuple[str, str]]
) -> int:
    """Create the WITHIN edges among ``pairs`` (source id, area id) that the
    graph lacks; return how many were written."""
    rows = [
        {"source_id": s, "target_id": a, "properties": {}}
        for s, a in dict.fromkeys(pairs)
        if (s, a) not in existing
    ]
    return write_in_chunks(driver, rows, partial(_write_within_chunk, source_label=source_label))


def upload_area_to_area(
    driver, areas: list[dict], ids: dict[str, str], existing: set[tuple[str, str]]
) -> int:
    print("\n=== Area->Area WITHIN edges ===")
    pairs = []
    for area in areas:
        parent = area.get("parent_area")
        if not parent:
            continue
        cid, pid = ids.get(area["name"]), ids.get(parent)
        if not cid or not pid:
            print(f"  [SKIP] {area['name']} -> {parent} (missing node)")
            continue
        pairs.append((cid, pid))
    written = upload_within(driver, "Area", pairs, existing)
    print(f"  {written} of {len(pairs)} Area->Area edges changed")
    return len(pairs)


def upload_poi_to_area(
    driver, slug: str, city_name: str, ids: dict[str, str], existing: set[tuple[str, str]]
) -> tuple[int, int]:
    print("\n=== POI->Area WITHIN edges ===")
    with open(_city_data_dir(slug) / "within_edges.json") as f:
        staging = json.load(f)
    names = sorted({e["poi_name"] for e in staging["poi_to_area"]})
    with driver.session(database=get_database()) as session:
        pois_by_name = {
            r["name"]: r["id"]
            for r in session.run(
                "UNWIND $names AS name MATCH (p:POI {name: name, city_name: $city}) "
                "RETURN name, p.id AS id",
                names=names,
                city=city_name,
            )
        }
    print(f"Resolved {len(pois_by_name)} POIs, {len(ids)} Areas")

    pairs = []
    failed = 0
    missing_poi, missing_area = set(), set()
    for e in staging["poi_to_area"]:
        poi_id = pois_by_name.get(e["poi_name"])
        area_id = ids.get(e["area_name"])
        if not poi_id:
            missing_poi.add(e["poi_name"])
            failed += 1
            continue
        if not area_id:
            missing_area.add(e["area_name"])
            failed += 1
            continue
        pairs.append((poi_id, area_id))
    written = upload_within(driver, "POI", pairs, existing)
    print(
        f"POI->Area edges: {written} of {len(pairs)} changed, "
        f"{len(staging['poi_to_area'])} in the repo (failed {failed})"
    )
    if missing_poi:
        print(f"  POIs not in Neo4j ({len(missing_poi)}): {sorted(missing_poi)[:10]}")
    if missing_area:
        print(f"  Areas not in Neo4j: {sorted(missing_area)}")
    return len(pairs), failed


@abort_on_connection_error
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slug", required=True, help="city slug, e.g. new_york")
    ap.add_argument("--allow-cloud", action="store_true", help="permit a non-local target")
    args = ap.parse_args()
    # Before any driver exists, exactly as the POI upload does.
    _assert_upload_target_allowed(args.allow_cloud)

    with open(_city_data_dir(args.slug) / "areas.json") as f:
        areas = json.load(f)
    if not areas:
        print(f"No Areas for '{args.slug}'")
        return
    city_name = areas[0]["city_name"]
    print(f"Uploading {len(areas)} Areas for '{args.slug}' (city_name={city_name})\n")

    driver = create_driver()
    try:
        ids = upload_areas(driver, areas, args.slug)
        with driver.session(database=get_database()) as session:
            existing = _graph_within(session, city_name)
        a2a = upload_area_to_area(driver, areas, ids, existing)
        linked, failed = upload_poi_to_area(driver, args.slug, city_name, ids, existing)
    finally:
        driver.close()

    print("\n=== DONE ===")
    print(f"Area nodes:      {len(ids)}/{len(areas)}")
    print(f"Area->Area:      {a2a}")
    print(f"POI->Area:       {linked} linked, {failed} failed")


if __name__ == "__main__":
    main()
