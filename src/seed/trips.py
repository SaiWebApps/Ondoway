"""Seed a Trip with ItineraryItems connecting Profile → POI → Beat."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from neo4j import Driver

from src.connection import get_database
from src.seed.users import FAMILY_ID

_MERGE_TRIP = """
MERGE (t:Trip {name: $name})
SET t.id              = coalesce(t.id, randomUUID()),
    t.start_date      = date($start_date),
    t.end_date        = date($end_date),
    t.cover_image_url = $cover_image_url,
    t.status          = $status
"""

_MERGE_CAPTAIN = """
MATCH (p:Profile {display_name: $captain_name})
MATCH (t:Trip {name: $trip_name})
MERGE (p)-[:IS_CAPTAIN_OF]->(t)
"""

_MERGE_CREW = """
MATCH (p:Profile {display_name: $crew_name})
MATCH (t:Trip {name: $trip_name})
MERGE (p)-[:IS_CREW_OF]->(t)
"""

#: The seeded trip is the seeded family's day — Mom captains it, Kid crews it,
#: both are members — so it carries the DAY_OF mark the creation path writes
#: for a day born inside a family (src/api/crud/trips.py).
_MERGE_DAY_OF = """
MATCH (t:Trip {name: $trip_name})
MATCH (f:Family {id: $family_id})
MERGE (t)-[:DAY_OF]->(f)
"""

_MERGE_ITINERARY_ITEM = """
MATCH (t:Trip {name: $trip_name})
MATCH (prof:Profile {display_name: $profile_name})
MATCH (poi:POI {name: $poi_name, city_name: $city_name})
MATCH (poi)-[:HAS_BEAT]->(beat:NarrativeBeat)
WITH t, prof, poi, beat LIMIT 1
MERGE (t)-[:HAS_STOP]->(item:ItineraryItem {sort_order: $sort_order})
SET item.id             = coalesce(item.id, randomUUID()),
    item.scheduled_date = date($scheduled_date),
    item.start_time     = time($start_time),
    item.duration_min   = $duration_min,
    item.status         = 'NOT_STARTED'
MERGE (item)-[:ASSIGNED_TO]->(prof)
MERGE (item)-[:AT_POI]->(poi)
MERGE (item)-[:PLAYS_BEAT]->(beat)
"""

TRIP_DEF = {
    "name": "Paris Spring 2026",
    "start_date": "2026-04-10",
    "end_date": "2026-04-14",
    "cover_image_url": "https://images.ondoway.app/trips/paris-spring.jpg",
    "status": "planning",
    "captain": "Mom",
    "crew": ["Kid"],
}

STOPS: list[dict] = [
    {
        "poi_name": "Eiffel Tower",
        "city_name": "paris",
        "sort_order": 1,
        "start_time": "09:00",
        "duration_min": 90,
    },
    {
        "poi_name": "Cafe de Flore",
        "city_name": "paris",
        "sort_order": 2,
        "start_time": "11:00",
        "duration_min": 30,
    },
    {
        "poi_name": "Shakespeare and Company",
        "city_name": "paris",
        "sort_order": 3,
        "start_time": "12:00",
        "duration_min": 45,
    },
]


def _create_trip(tx, trip: dict) -> None:
    tx.run(_MERGE_TRIP, **{k: v for k, v in trip.items() if k not in ("captain", "crew")})


def _assign_captain(tx, trip_name: str, captain_name: str) -> None:
    tx.run(_MERGE_CAPTAIN, captain_name=captain_name, trip_name=trip_name)


def _assign_crew(tx, trip_name: str, crew_name: str) -> None:
    tx.run(_MERGE_CREW, crew_name=crew_name, trip_name=trip_name)


def _mark_family_day(tx, trip_name: str) -> None:
    tx.run(_MERGE_DAY_OF, trip_name=trip_name, family_id=FAMILY_ID)


def _create_stop(tx, trip_name: str, profile_name: str, stop: dict) -> None:
    tx.run(
        _MERGE_ITINERARY_ITEM,
        trip_name=trip_name,
        profile_name=profile_name,
        scheduled_date=TRIP_DEF["start_date"],
        **stop,
    )


def seed_trip(driver: Driver) -> dict[str, int]:
    """Seed the test trip with captain, crew, and itinerary items."""
    with driver.session(database=get_database()) as session:
        session.execute_write(_create_trip, TRIP_DEF)
        session.execute_write(_assign_captain, TRIP_DEF["name"], TRIP_DEF["captain"])
        for crew_name in TRIP_DEF["crew"]:
            session.execute_write(_assign_crew, TRIP_DEF["name"], crew_name)
        session.execute_write(_mark_family_day, TRIP_DEF["name"])
        for stop in STOPS:
            session.execute_write(_create_stop, TRIP_DEF["name"], TRIP_DEF["captain"], stop)

    return {"trips": 1, "stops": len(STOPS)}
