"""Graph operations for families — a persistent group of profiles (docs/adr/0005).

Membership hangs off the PROFILE, not the user: everything a family coordinates
already belongs to profiles (trips are captained and crewed by profiles, lens
preferences live on profiles). Every write is a MERGE, so joining twice is one
membership.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from neo4j import Session

_CREATE_FAMILY = """
MATCH (p:Profile {id: $profile_id})
CREATE (f:Family {id: $family_id, name: $name, created_at: datetime()})
MERGE (p)-[:MEMBER_OF]->(f)
RETURN f.id AS family_id
"""

_ADD_MEMBER = """
MATCH (f:Family {id: $family_id})
MATCH (p:Profile {id: $profile_id})
MERGE (p)-[:MEMBER_OF]->(f)
RETURN f.id AS family_id
"""

_FAMILIES_FOR_USER = """
MATCH (u:User {id: $user_id})-[:HAS_PROFILE]->(:Profile)-[:MEMBER_OF]->(f:Family)
WITH DISTINCT f
MATCH (member:Profile)-[:MEMBER_OF]->(f)
WITH f, member ORDER BY member.display_name
WITH f, collect({profile_id: member.id, display_name: member.display_name}) AS members
RETURN f.id AS family_id, f.name AS name, members
ORDER BY f.created_at
"""

_CALLER_PROFILE_IN_FAMILY = """
MATCH (u:User {id: $user_id})-[:HAS_PROFILE]->(p:Profile)-[:MEMBER_OF]
      ->(f:Family {id: $family_id})
RETURN p.id AS profile_id LIMIT 1
"""

_LATEST_PROFILE = """
MATCH (u:User {id: $user_id})-[:HAS_PROFILE]->(p:Profile)
RETURN p.id AS profile_id ORDER BY p.created_at DESC, p.id LIMIT 1
"""


def create_family(session: Session, profile_id: str, name: str | None) -> str | None:
    """Create a Family with the given profile as its first member.

    Returns the new family id, or None when the profile does not exist (the
    route has already ownership-checked it, so None is a race, not a leak).
    """
    record = session.run(
        _CREATE_FAMILY,
        profile_id=profile_id,
        family_id=str(uuid.uuid4()),
        name=name,
    ).single()
    return record["family_id"] if record else None


def add_member(session: Session, family_id: str, profile_id: str) -> bool:
    """MERGE the profile into the family. False when the family is gone."""
    record = session.run(_ADD_MEMBER, family_id=family_id, profile_id=profile_id).single()
    return record is not None


def families_for_user(session: Session, user_id: str) -> list[dict[str, Any]]:
    """Every family any of the user's profiles belongs to, with its members."""
    return [dict(r) for r in session.run(_FAMILIES_FOR_USER, user_id=user_id)]


def caller_profile_in_family(session: Session, user_id: str, family_id: str) -> str | None:
    """The caller's own profile id inside the family, or None when no profile of
    theirs is a member — the membership check invites hang on."""
    record = session.run(
        _CALLER_PROFILE_IN_FAMILY, user_id=user_id, family_id=family_id
    ).single()
    return record["profile_id"] if record else None


def latest_profile_id(session: Session, user_id: str) -> str | None:
    """The caller's newest profile — the same deterministic resolution
    GET /profile documents (created_at DESC, id as the tie-break)."""
    record = session.run(_LATEST_PROFILE, user_id=user_id).single()
    return record["profile_id"] if record else None
