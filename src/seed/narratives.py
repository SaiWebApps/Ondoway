"""Seed NarrativeBeat nodes tied to POIs and tagged with Lenses.

A beat's duration is importance_tier * 60 seconds.
Café de Flore gets two beats (different lenses) to prove multi-beat per POI.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.connection import get_database
from src.schema.definitions import TAGGABLE_LENSES

if TYPE_CHECKING:
    from neo4j import Driver

_MERGE_BEAT = """
MATCH (p:POI {name: $poi_name, city_name: $city_name})
MERGE (p)-[r:HAS_BEAT]->(b:NarrativeBeat {script_body: $script_body})
SET b.id            = coalesce(b.id, randomUUID()),
    b.version       = $version,
    b.active_status = $active_status,
    b.audio_url     = $audio_url,
    b.duration_sec  = $duration_sec,
    b.kid_friendly  = $kid_friendly,
    r.sort_order    = $sort_order
WITH b
UNWIND $lens_names AS lens_name
MATCH (l:Lens {name: lens_name})
MERGE (b)-[:TAGGED_WITH]->(l)
"""

BEATS: list[dict] = [
    {
        "poi_name": "Eiffel Tower",
        "script_body": (
            "Look up. Every rivet you see was placed by hand — 2.5 million of them. "
            "Gustave Eiffel didn't build this for beauty; he built it to prove iron "
            "could touch the sky. Parisians called it an eyesore. Now it's the most "
            "visited paid monument on Earth."
        ),
        "lens_names": ["historic_arch", "hidden_history"],
        "importance_tier": 5,
    },
    {
        "poi_name": "Cafe de Flore",
        "script_body": (
            "This corner table — yes, this exact one — is where Sartre scribbled "
            "Being and Nothingness while chain-smoking Gauloises. De Beauvoir sat "
            "across from him, writing her own masterpiece. The hot chocolate recipe "
            "hasn't changed since 1887."
        ),
        "lens_names": ["literary_heritage"],
        "importance_tier": 3,
    },
    {
        "poi_name": "Cafe de Flore",
        "script_body": (
            "During the Occupation, Café de Flore became an unlikely resistance "
            "hub. The Nazis preferred the Deux Magots next door — which made Flore "
            "the place where intellectuals quietly planned acts of cultural defiance."
        ),
        "lens_names": ["dark_history"],
        "importance_tier": 3,
    },
    {
        "poi_name": "Shakespeare and Company",
        "script_body": (
            "Sylvia Beach opened the original shop in 1919 and published Ulysses "
            "when no one else would. This reincarnation, opened by George Whitman "
            "in 1951, still lets writers sleep among the shelves — they call them "
            "Tumbleweeds."
        ),
        "lens_names": ["literary_heritage", "local_legends"],
        "importance_tier": 2,
    },
]

# Validate all lens references are taggable at import time
for _beat in BEATS:
    for _ln in _beat["lens_names"]:
        assert _ln in TAGGABLE_LENSES, f"Beat lens '{_ln}' is not a taggable lens"


def _build_beat_params(beat: dict, sort_order: int) -> dict:
    """Transform a beat definition into Cypher parameters."""
    poi_slug = beat["poi_name"].lower().replace(" ", "_")
    return {
        "poi_name": beat["poi_name"],
        "city_name": beat.get("city_name", "paris"),
        "script_body": beat["script_body"],
        "lens_names": beat["lens_names"],
        "version": 1,
        "active_status": "active",
        "audio_url": f"s3://ondoway-audio/placeholder/{poi_slug}.mp3",
        "duration_sec": beat["importance_tier"] * 60,
        "kid_friendly": "yes",
        "sort_order": sort_order,
    }


def _create_beat(tx, params: dict) -> None:
    tx.run(_MERGE_BEAT, **params)


def seed_beats(driver: Driver) -> int:
    """Seed all narrative beats. Returns count created."""
    poi_counters: dict[str, int] = {}
    with driver.session(database=get_database()) as session:
        for beat in BEATS:
            poi = beat["poi_name"]
            sort_order = poi_counters.get(poi, 0)
            poi_counters[poi] = sort_order + 1
            session.execute_write(_create_beat, _build_beat_params(beat, sort_order))
    return len(BEATS)
