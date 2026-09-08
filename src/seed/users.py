"""Seed test User and Profile nodes with HAS_PROFILE + PREFERS_LENS
relationships, and the family the two profiles share (MEMBER_OF)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.connection import get_database
from src.schema.definitions import TAGGABLE_LENSES

if TYPE_CHECKING:
    from neo4j import Driver

_MERGE_USER = """
MERGE (u:User {email: $email})
SET u.id = coalesce(u.id, randomUUID()),
    u.created_at = coalesce(u.created_at, datetime()),
    u.last_logon = datetime()
"""

_MERGE_PROFILE = """
MATCH (u:User {email: $email})
MERGE (u)-[:HAS_PROFILE]->(p:Profile {display_name: $display_name})
SET p.id = coalesce(p.id, randomUUID())
"""

_MERGE_PREFERS_LENS = """
MATCH (p:Profile {display_name: $profile_name})
MATCH (l:Lens {name: $lens_name})
MERGE (p)-[:PREFERS_LENS]->(l)
"""

_MERGE_FAMILY = """
MERGE (f:Family {id: $family_id})
SET f.name = $name,
    f.created_at = coalesce(f.created_at, datetime())
"""

_MERGE_MEMBER_OF = """
MATCH (p:Profile {display_name: $profile_name})
MATCH (f:Family {id: $family_id})
MERGE (p)-[:MEMBER_OF]->(f)
"""

TEST_USER_EMAIL = "testuser@ondoway.app"

#: The seeded family both profiles share (docs/adr/0005: a family is a
#: persistent group of PROFILES — Mom and Kid are two profiles of one user,
#: and a couple is a family of two). A fixed id keeps the MERGE idempotent
#: under the Family unique-id constraint.
FAMILY_ID = "seed-family-mom-kid"
FAMILY_NAME = "The Testersons"

PROFILES: list[dict] = [
    {
        "display_name": "Mom",
        "lenses": ["hidden_history", "historic_cuisine", "literary_heritage"],
    },
    {
        "display_name": "Kid",
        "lenses": ["street_art", "parks_gardens", "local_legends"],
    },
]

# Validate all profile lens preferences are taggable at import time
for _profile in PROFILES:
    for _ln in _profile["lenses"]:
        assert _ln in TAGGABLE_LENSES, f"Profile lens '{_ln}' is not a taggable lens"


def _create_user(tx, email: str) -> None:
    tx.run(_MERGE_USER, email=email)


def _create_profile(tx, email: str, display_name: str) -> None:
    tx.run(_MERGE_PROFILE, email=email, display_name=display_name)


def _link_lens(tx, profile_name: str, lens_name: str) -> None:
    tx.run(_MERGE_PREFERS_LENS, profile_name=profile_name, lens_name=lens_name)


def _create_family(tx) -> None:
    tx.run(_MERGE_FAMILY, family_id=FAMILY_ID, name=FAMILY_NAME)


def _join_family(tx, profile_name: str) -> None:
    tx.run(_MERGE_MEMBER_OF, profile_name=profile_name, family_id=FAMILY_ID)


def seed_users(driver: Driver) -> dict[str, int]:
    """Seed test user, profiles, lens preferences, and their family. Returns counts."""
    with driver.session(database=get_database()) as session:
        session.execute_write(_create_user, TEST_USER_EMAIL)

        for profile in PROFILES:
            session.execute_write(_create_profile, TEST_USER_EMAIL, profile["display_name"])
            for lens_name in profile["lenses"]:
                session.execute_write(_link_lens, profile["display_name"], lens_name)

        session.execute_write(_create_family)
        for profile in PROFILES:
            session.execute_write(_join_family, profile["display_name"])

    return {"users": 1, "profiles": len(PROFILES), "families": 1}
