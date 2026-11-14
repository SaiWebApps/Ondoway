"""Seed Paris POI nodes with Neo4j native GeoPoint locations."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from neo4j import Driver

from src.api.models.nodes import canonical_name_key
from src.connection import get_database

# MERGE stays on (name, city_name) to match nodes seeded before name_key existed,
# but we ALSO SET the canonical name_key so seeded POIs carry the same
# dedup key as create_node writes — a later create_node/upload MERGEs onto them
# instead of forking a duplicate. name_key is derived from name via the single
# source of truth (canonical_name_key), not stored in the row.
_MERGE_POI = """
MERGE (p:POI {name: $name, city_name: $city_name})
SET p.id             = coalesce(p.id, randomUUID()),
    p.name_key       = $name_key,
    p.short_description = $short_description,
    p.location       = point({latitude: $lat, longitude: $lng, srid: 4326}),
    p.importance_tier = $importance_tier,
    p.trigger_radius  = $trigger_radius,
    p.typical_duration_min = $typical_duration_min,
    p.kid_friendly    = $kid_friendly
"""

PARIS_POIS: list[dict] = [
    {
        "name": "Eiffel Tower",
        "city_name": "paris",
        "short_description": "Iron lattice tower on the Champ de Mars, symbol of Paris.",
        "lat": 48.858400,
        "lng": 2.294500,
        "importance_tier": 5,
        "trigger_radius": 10,
        "typical_duration_min": 90,
        "kid_friendly": "yes",
    },
    {
        "name": "Cafe de Flore",
        "city_name": "paris",
        "short_description": "Legendary Left Bank café, haunt of Sartre and de Beauvoir.",
        "lat": 48.854000,
        "lng": 2.332500,
        "importance_tier": 3,
        "trigger_radius": 10,
        "typical_duration_min": 30,
        "kid_friendly": "yes",
    },
    {
        "name": "Shakespeare and Company",
        "city_name": "paris",
        "short_description": "Iconic English-language bookshop across from Notre-Dame.",
        "lat": 48.852600,
        "lng": 2.347100,
        "importance_tier": 2,
        "trigger_radius": 10,
        "typical_duration_min": 45,
        "kid_friendly": "yes",
    },
]


def _create_poi(tx, poi: dict) -> None:
    # Derive name_key here (not stored in PARIS_POIS) so it always matches the
    # canonical form of whatever `name` is seeded.
    tx.run(_MERGE_POI, name_key=canonical_name_key(poi["name"]), **poi)


def seed_pois(driver: Driver) -> int:
    """Seed all Paris POIs. Returns count created."""
    with driver.session(database=get_database()) as session:
        for poi in PARIS_POIS:
            session.execute_write(_create_poi, poi)
    return len(PARIS_POIS)
