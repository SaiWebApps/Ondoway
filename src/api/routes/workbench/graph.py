"""Graph data endpoint for vis.js visualization."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from neo4j import Session

from src.api.dependencies import get_session
from src.api.utils import serialize_neo4j_props

router = APIRouter(tags=["graph"])


@router.get("/graph")
def get_full_graph(session: Session = Depends(get_session)):
    """Fetch all nodes and relationships formatted for vis.js."""
    nodes_result = session.run(
        "MATCH (n) RETURN n.id AS id, labels(n) AS labels, properties(n) AS props"
    )
    nodes = []
    for record in nodes_result:
        props = serialize_neo4j_props(dict(record["props"]))
        primary_label = record["labels"][0] if record["labels"] else "Unknown"
        display = (
            props.get("display_name")
            or props.get("name")
            or props.get("display_label")
            or props.get("email")
            or primary_label
        )
        nodes.append(
            {
                "id": record["id"],
                "label": display,
                "group": primary_label,
                "labels": record["labels"],
                "properties": props,
            }
        )

    rels_result = session.run(
        "MATCH (a)-[r]->(b) "
        "RETURN elementId(r) AS eid, type(r) AS type, "
        "a.id AS source_id, b.id AS target_id, properties(r) AS props"
    )
    edges = [
        {
            "id": r["eid"],
            "from": r["source_id"],
            "to": r["target_id"],
            "label": r["type"],
            "properties": serialize_neo4j_props(dict(r["props"])) if r["props"] else {},
        }
        for r in rels_result
    ]

    return {"nodes": nodes, "edges": edges}


@router.get("/cities")
def list_cities(session: Session = Depends(get_session)):
    """Distinct cities present in the graph, for the workbench city picker.

    Sourced from ``POI.city_name`` — the EXACT, case-sensitive key the workbench
    uses to fetch beats (``/graph/poi/{name}/beats?city_name=...``). Typing the
    name by hand risks a casing fork ('Paris' vs 'paris') that silently empties
    every beat fetch; a picker sourced from the graph removes that hazard.

    Each city carries a POI count and the POI-coordinate centroid (POIs store
    coordinates in a ``location`` point) so the 50 km review geofence needs no
    external geocode for a known city. ``centre_lat``/``centre_lng`` are null
    when no POI in that city has coordinates yet (the caller falls back to a
    manual geocode).
    """
    result = session.run(
        "MATCH (p:POI) "
        "WHERE p.city_name IS NOT NULL "
        "WITH p.city_name AS city_name, count(p) AS poi_count, "
        "     avg(p.location.latitude) AS centre_lat, "
        "     avg(p.location.longitude) AS centre_lng "
        "RETURN city_name, poi_count, centre_lat, centre_lng "
        "ORDER BY poi_count DESC, city_name"
    )
    cities = [
        {
            "city_name": r["city_name"],
            "poi_count": r["poi_count"],
            "centre_lat": r["centre_lat"],
            "centre_lng": r["centre_lng"],
        }
        for r in result
    ]
    return {"cities": cities}


_POI_BEATS_TAIL = (
    'WHERE b.active_status = "active" '
    "OPTIONAL MATCH (b)-[:TAGGED_WITH]->(l:Lens) "
    "WITH b, r, collect(DISTINCT l.name) AS lens_slugs "
    "RETURN b.id AS id, b.script_body AS script_body, "
    "b.version AS version, b.active_status AS active_status, "
    "b.duration_sec AS duration_sec, lens_slugs AS lens_slugs, "
    "r.sort_order AS sort_order "
    "ORDER BY r.sort_order"
)


def _rows_to_beats(result) -> list[dict]:
    return [
        {
            "id": r["id"],
            "script_body": r["script_body"],
            "version": r["version"],
            "active_status": r["active_status"],
            "duration_sec": r["duration_sec"],
            "lens_slugs": r["lens_slugs"],
            "lens_slug": r["lens_slugs"][0] if r["lens_slugs"] else None,
            "sort_order": r["sort_order"],
        }
        for r in result
    ]


@router.get("/graph/poi/by-id/{poi_id}/beats")
def get_poi_beats_by_id(
    poi_id: str,
    session: Session = Depends(get_session),
):
    """Fetch active beats and their lens tags for exactly ONE POI node, by id.

    The name-keyed sibling below conflates ``force_create``'d same-name POIs in
    the same city: it returns the union of every same-name node's beats, so the
    workbench's conflict detection and merge preview attribute a beat to the
    wrong node (deprecating an unrelated POI's beat, or failing to find the
    HAS_BEAT edge it is trying to delete). Any caller that already holds the
    selected node's id must use this route. The projection/aggregation shape is
    identical, so responses are drop-in compatible.
    """
    result = session.run(
        "MATCH (p:POI {id: $poi_id})-[r:HAS_BEAT]->(b:NarrativeBeat) " + _POI_BEATS_TAIL,
        poi_id=poi_id,
    )
    beats = _rows_to_beats(result)
    return {"poi_id": poi_id, "beats": beats}


@router.get("/graph/poi/{poi_name}/beats")
def get_poi_beats(
    poi_name: str,
    city_name: str,
    session: Session = Depends(get_session),
):
    """Fetch active beats and their lens tags for a POI by (name, city_name).

    Beats are matched first and lenses joined via OPTIONAL MATCH, then aggregated
    into a per-beat ``lens_slugs`` list. This mirrors selection.py so that
    (a) lens-less active beats are still returned (review.html conflict detection
    must see them to flag duplicates) and (b) a multi-lens beat produces exactly
    one row (not N duplicate rows that inflate DB-beat counts / hard-match noise).
    ``lens_slug`` is retained as the first slug (or None) for backward compat.

    NOTE: (name, city_name) is NOT unique — ``force_create`` allows duplicate
    names — so this returns the union across every same-name node. Callers that
    must operate on one specific node (conflict resolution, merge) must use
    ``/graph/poi/by-id/{poi_id}/beats`` instead. Retained for viewer.html.
    """
    result = session.run(
        "MATCH (p:POI {name: $name, city_name: $city_name})-[r:HAS_BEAT]->(b:NarrativeBeat) "
        + _POI_BEATS_TAIL,
        name=poi_name,
        city_name=city_name,
    )
    beats = _rows_to_beats(result)
    return {"poi_name": poi_name, "beats": beats}


@router.get("/graph/area/{area_name}/beats")
def get_area_beats(area_name: str, session: Session = Depends(get_session)):
    """Fetch active beats and their lens tags for an Area by name.

    Mirrors get_poi_beats: OPTIONAL-MATCH the lens and aggregate into a per-beat
    ``lens_slugs`` list so lens-less active beats survive and multi-lens beats
    yield exactly one row. ``lens_slug`` is the first slug (or None) for compat.
    """
    result = session.run(
        "MATCH (a:Area {name: $name})-[:HAS_BEAT]->(b:NarrativeBeat) "
        'WHERE b.active_status = "active" '
        "OPTIONAL MATCH (b)-[:TAGGED_WITH]->(l:Lens) "
        "WITH b, collect(DISTINCT l.name) AS lens_slugs "
        "RETURN b.id AS id, b.script_body AS script_body, "
        "b.version AS version, b.active_status AS active_status, "
        "b.duration_sec AS duration_sec, lens_slugs AS lens_slugs",
        name=area_name,
    )
    beats = [
        {
            "id": r["id"],
            "script_body": r["script_body"],
            "version": r["version"],
            "active_status": r["active_status"],
            "duration_sec": r["duration_sec"],
            "lens_slugs": r["lens_slugs"],
            "lens_slug": r["lens_slugs"][0] if r["lens_slugs"] else None,
        }
        for r in result
    ]
    return {"area_name": area_name, "beats": beats}


@router.get("/graph/area/{area_name}/contents")
def get_area_contents(area_name: str, session: Session = Depends(get_session)):
    """Fetch child Areas and POIs contained WITHIN an Area."""
    result = session.run(
        "MATCH (child)-[:WITHIN]->(a:Area {name: $name}) "
        "OPTIONAL MATCH (child)-[:HAS_BEAT]->(b:NarrativeBeat) "
        "WITH child, labels(child) AS lbls, count(b) AS beat_count "
        "RETURN lbls, child.name AS name, child.id AS id, "
        "child.area_type AS area_type, child.short_description AS short_description, "
        "beat_count "
        "ORDER BY lbls[0], name",
        name=area_name,
    )
    sub_areas = []
    pois = []
    for r in result:
        item = {
            "name": r["name"],
            "id": r["id"],
            "short_description": r["short_description"],
            "beat_count": r["beat_count"],
        }
        if "Area" in r["lbls"]:
            item["area_type"] = r["area_type"]
            sub_areas.append(item)
        elif "POI" in r["lbls"]:
            pois.append(item)
    return {"area_name": area_name, "sub_areas": sub_areas, "pois": pois}
