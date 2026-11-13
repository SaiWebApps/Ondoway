"""Cypher query functions for node CRUD operations."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from src.api.crud.validation import (
    raise_422 as _raise_422,
)
from src.api.crud.validation import (
    validate_label as _validate_label,
)
from src.api.crud.validation import (
    validate_property_keys as _validate_property_keys,
)
from src.api.models.nodes import canonical_name_key, protected_node_keys
from src.api.utils import serialize_neo4j_props

if TYPE_CHECKING:
    from neo4j import Session

def _encode_complex_props(props: dict) -> dict:
    """JSON-encode list-of-dict values so Neo4j can store them as strings.

    Neo4j refuses `SET n.x = $x` when $x is a list of maps. Pydantic models
    such as `physical_cues: list[PhysicalCue]` and
    `inline_foreign_phrases: list[InlineForeignPhrase]` arrive as list[dict]
    after model_dump(). Encode them here; ``serialize_neo4j_props`` in
    src/api/utils.py decodes them on read.
    """
    encoded = {}
    for key, val in props.items():
        if isinstance(val, list) and val and all(isinstance(item, dict) for item in val):
            encoded[key] = json.dumps(val)
        else:
            encoded[key] = val
    return encoded


def _record_to_node(record) -> dict[str, Any]:
    """Convert a Neo4j record to a node dict."""
    props = serialize_neo4j_props(dict(record["props"]))
    return {
        "id": record["id"],
        "labels": record["labels"],
        "properties": props,
    }


def list_nodes(session: Session, label: str, skip: int, limit: int) -> tuple[list[dict], int]:
    """Return paginated nodes of a label and total count."""
    _validate_label(label)
    count_result = session.run(f"MATCH (n:{label}) RETURN count(n) AS total").single()
    total = count_result["total"]

    result = session.run(
        f"MATCH (n:{label}) "
        f"RETURN n.id AS id, labels(n) AS labels, properties(n) AS props "
        f"ORDER BY n.id SKIP $skip LIMIT $limit",
        skip=skip,
        limit=limit,
    )
    nodes = [_record_to_node(record) for record in result]
    return nodes, total


def get_node(session: Session, label: str, node_id: str) -> dict | None:
    """Return a single node by label and id property, or None."""
    _validate_label(label)
    result = session.run(
        f"MATCH (n:{label} {{id: $node_id}}) "
        f"RETURN n.id AS id, labels(n) AS labels, properties(n) AS props",
        node_id=node_id,
    ).single()
    if result is None:
        return None
    return _record_to_node(result)


def create_node(session: Session, label: str, properties: dict[str, Any]) -> dict:
    """Create a node with a generated UUID id. Returns the created node.

    POI and Area use MERGE for idempotent upserts on a natural key.
    NarrativeBeat always CREATEs a fresh node with a server-generated id —
    ids are never client-supplied, so re-posting the same beat produces a new
    node and callers must dedup upstream.
    """
    _validate_label(label)
    _validate_property_keys(properties)
    params = _encode_complex_props(dict(properties))

    # City-key normalization guard (2026-07-03): POI.city_name is the canonical
    # city slug — the tour engine matches POIs on it (selection.py) and the
    # workbench city picker (GET /cities) keys off it. Casing/whitespace variants
    # ('Paris' vs 'paris') fork the (name, city_name) MERGE key into duplicate
    # nodes whose beats the picker cannot reach — the exact failure the picker was
    # built to remove. Normalize POI city_name to a canonical lowercase, stripped
    # form at this single ingestion choke point so future uploads can never
    # re-fork it. Scoped to POI: Area.city_name keeps its own ('Paris') convention
    # (Areas join to POIs by :WITHIN, not by city_name equality).
    if label == "POI" and isinstance(params.get("city_name"), str):
        params["city_name"] = params["city_name"].strip().lower()

    # Name-key normalization guard (2026-07-06): the previous guard
    # normalized city_name but NOT name, so 'Notre-Dame' / 'notre-dame' /
    # 'Notre-Dame ' forked the MERGE key into duplicate nodes. Derive a
    # canonical `name_key` (collapse internal whitespace + trim + lower) and
    # MERGE on (name_key, city_name) while keeping n.name for display casing.
    if label == "POI" and isinstance(params.get("name"), str):
        params["name_key"] = canonical_name_key(params["name"])

    if label == "POI" and "latitude" in params and "longitude" in params:
        lat = params.pop("latitude")
        lng = params.pop("longitude")
        params["lat"] = lat
        params["lng"] = lng
        force_create = params.pop("force_create", False)

        set_parts = [
            "n.id = coalesce(n.id, randomUUID())",
            "n.created_at = coalesce(n.created_at, datetime())",
            "n.location = point({latitude: $lat, longitude: $lng, srid: 4326})",
        ]
        for key in params:
            # name/city_name/name_key are in the MERGE pattern (or set there);
            # n.name is set explicitly below to preserve display casing.
            if key not in ("lat", "lng", "name", "city_name", "name_key"):
                set_parts.append(f"n.{key} = ${key}")
        # Preserve display casing: the MERGE keys on name_key, but n.name keeps
        # the last-written display form.
        set_parts.append("n.name = $name")

        if force_create:
            # CREATE forces a new node even if (name_key, city_name) matches — used
            # when editor confirms "different place" for a proximity match with same
            # name.
            query = (
                f"CREATE (n:POI {{name_key: $name_key, city_name: $city_name}}) "
                f"SET {', '.join(set_parts)} "
                f"RETURN n.id AS id, labels(n) AS labels, properties(n) AS props"
            )
        else:
            # MERGE on (name_key, city_name) for idempotent POI creation (default
            # path). Multi-city safe — Notre-Dame Paris vs Notre-Dame Reims won't
            # collide; casing/whitespace variants of the same name now dedup.
            query = (
                f"MERGE (n:POI {{name_key: $name_key, city_name: $city_name}}) "
                f"SET {', '.join(set_parts)} "
                f"RETURN n.id AS id, labels(n) AS labels, properties(n) AS props"
            )
    elif label == "NarrativeBeat":
        # Create-by-id (2026-07-06): the old MERGE key was a bare,
        # GLOBAL script_body — two beats with identical narration across
        # different POIs/cities collided into one shared node, so an upload for
        # one POI silently overwrote another's beat. Each beat is now an
        # independent node keyed on its own id.
        #
        # Beat ids are ALWAYS server-generated (2026-07-19): a client-supplied
        # `id` is dropped, never used as a MERGE key. `NarrativeBeatCreate`
        # has no `id` field and pydantic ignores extras, so a client id could
        # never reach this function through POST /nodes/NarrativeBeat anyway —
        # the old MERGE-by-id branch was unreachable, and honouring an id here
        # would hand clients control of the primary key. Callers that need
        # re-upload idempotence must dedup UPSTREAM (see
        # scripts/upload_hidden_gardens.py, which matches on script_body_hash
        # before POSTing). Every create therefore yields a fresh node.
        params.pop("id", None)
        set_parts = [
            "n.id = coalesce(n.id, randomUUID())",
            "n.created_at = coalesce(n.created_at, datetime())",
        ]
        set_parts.extend(f"n.{key} = ${key}" for key in params)

        query = (
            f"CREATE (n:NarrativeBeat) "
            f"SET {', '.join(set_parts)} "
            f"RETURN n.id AS id, labels(n) AS labels, properties(n) AS props"
        )
    elif label == "Area":
        # MERGE on compound key (name, area_type, city_name) for idempotent Area creation
        lat = params.pop("centroid_lat")
        lng = params.pop("centroid_lng")
        params["lat"] = lat
        params["lng"] = lng

        set_parts = [
            "n.id = coalesce(n.id, randomUUID())",
            "n.created_at = coalesce(n.created_at, datetime())",
            "n.centroid = point({latitude: $lat, longitude: $lng, srid: 4326})",
        ]
        for key in params:
            if key not in ("lat", "lng", "name", "area_type", "city_name"):
                set_parts.append(f"n.{key} = ${key}")

        query = (
            f"MERGE (n:Area {{name: $name, area_type: $area_type, city_name: $city_name}}) "
            f"SET {', '.join(set_parts)} "
            f"RETURN n.id AS id, labels(n) AS labels, properties(n) AS props"
        )
    else:
        set_parts = [
            "n.id = randomUUID()",
            "n.created_at = datetime()",
        ]
        for key in params:
            set_parts.append(f"n.{key} = ${key}")

        query = (
            f"CREATE (n:{label}) "
            f"SET {', '.join(set_parts)} "
            f"RETURN n.id AS id, labels(n) AS labels, properties(n) AS props"
        )

    result = session.run(query, **params).single()
    return _record_to_node(result)


def update_node(
    session: Session, label: str, node_id: str, properties: dict[str, Any]
) -> dict | None:
    """Update node properties. Returns updated node or None if not found."""
    _validate_label(label)
    if not properties:
        return get_node(session, label, node_id)

    _validate_property_keys(properties)

    # Never rewrite identity/merge-key properties. A PUT with {"id":X}
    # would orphan the node from every id-keyed query and edge; {"name"}/
    # {"city_name"}/{"area_type"} would fork the idempotent MERGE key. Reject
    # the whole update (422) if it touches any protected key.
    protected = protected_node_keys(label)
    offending = sorted(k for k in properties if k in protected)
    if offending:
        _raise_422(
            f"cannot update protected {label} propert"
            f"{'y' if len(offending) == 1 else 'ies'} "
            f"{', '.join(repr(k) for k in offending)}: these identify the node "
            f"or form its merge key and are immutable",
            loc=("body", "properties", offending[0]),
        )

    properties = _encode_complex_props(
        dict(properties)
    )  # Don't mutate caller's dict + JSON-encode list[dict]

    params: dict[str, Any] = {"node_id": node_id}
    set_parts: list[str] = []

    # POI: convert latitude/longitude to spatial point.
    # Coordinates must be supplied as a PAIR — a lone latitude (or
    # longitude) previously produced an empty SET clause -> CypherSyntaxError
    # (500). Reject partial coordinates with a clean 422.
    if label == "POI" and ("latitude" in properties or "longitude" in properties):
        lat = properties.pop("latitude", None)
        lng = properties.pop("longitude", None)
        if (lat is None) != (lng is None):
            _raise_422(
                "latitude and longitude must be updated together (supply both or "
                "neither)",
                loc=("body", "properties", "latitude" if lat is not None else "longitude"),
            )
        if lat is not None and lng is not None:
            set_parts.append("n.location = point({latitude: $lat, longitude: $lng, srid: 4326})")
            params["lat"] = lat
            params["lng"] = lng

    # Area: convert centroid_lat/centroid_lng to spatial point (same pair rule).
    if label == "Area" and ("centroid_lat" in properties or "centroid_lng" in properties):
        lat = properties.pop("centroid_lat", None)
        lng = properties.pop("centroid_lng", None)
        if (lat is None) != (lng is None):
            _raise_422(
                "centroid_lat and centroid_lng must be updated together (supply both "
                "or neither)",
                loc=(
                    "body",
                    "properties",
                    "centroid_lat" if lat is not None else "centroid_lng",
                ),
            )
        if lat is not None and lng is not None:
            set_parts.append("n.centroid = point({latitude: $lat, longitude: $lng, srid: 4326})")
            params["lat"] = lat
            params["lng"] = lng

    for key, val in properties.items():
        set_parts.append(f"n.{key} = ${key}")
        params[key] = val

    # Never emit an empty SET clause (invalid Cypher -> 500). This can
    # happen if the only supplied properties were popped above (e.g. a payload
    # of solely {"latitude","longitude"} whose pair became the point SET is
    # fine, but a payload that reduces to nothing must be a no-op). If nothing
    # remains to set, return the node unchanged.
    if not set_parts:
        return get_node(session, label, node_id)

    query = (
        f"MATCH (n:{label} {{id: $node_id}}) "
        f"SET {', '.join(set_parts)} "
        f"RETURN n.id AS id, labels(n) AS labels, properties(n) AS props"
    )
    result = session.run(query, **params).single()
    if result is None:
        return None
    return _record_to_node(result)


def delete_node(session: Session, label: str, node_id: str) -> bool:
    """DETACH DELETE a node. Returns True if found and deleted."""
    _validate_label(label)
    result = session.run(
        f"MATCH (n:{label} {{id: $node_id}}) DETACH DELETE n RETURN count(*) AS deleted",
        node_id=node_id,
    ).single()
    return result["deleted"] > 0
