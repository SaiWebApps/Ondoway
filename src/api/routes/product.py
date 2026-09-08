"""Public read-only product endpoints: the lens taxonomy and the caller's profile.

Unlike the workbench CRUD routers (graph/nodes/edges/schema/onboard), these are
mounted UNCONDITIONALLY in src/api/app.py — outside `_workbench_api_enabled()`
— because the mobile client needs them with the workbench gate off.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from neo4j import Session

from src.api.auth.dependencies import get_current_user
from src.api.dependencies import get_session

router = APIRouter(tags=["product"])


@router.get("/lenses")
def list_lenses(session: Session = Depends(get_session)) -> list[dict]:
    """Return the lens taxonomy.

    P3 build (run-context.md decisions `lenses_shape`, `p1_minimal_build`):
    each parent lens carries a nested `children` list of its child lenses
    (id, name, display_label, is_parent:false), gathered via OPTIONAL MATCH
    on IS_PARENT_OF so a childless parent yields an empty list rather than
    being dropped.
    """
    result = session.run(
        "MATCH (parent:Lens {is_parent: true}) "
        "OPTIONAL MATCH (parent)-[:IS_PARENT_OF]->(child:Lens) "
        "RETURN parent.id AS id, parent.name AS name, "
        "parent.display_label AS display_label, parent.is_parent AS is_parent, "
        "collect(CASE WHEN child IS NULL THEN NULL ELSE {"
        "id: child.id, name: child.name, display_label: child.display_label, "
        "is_parent: child.is_parent} END) AS children"
    )
    return [
        {
            "id": record["id"],
            "name": record["name"],
            "display_label": record["display_label"],
            "is_parent": record["is_parent"],
            "children": [c for c in record["children"] if c is not None],
        }
        for record in result
    ]


@router.get("/profile")
def get_profile(
    current_user: dict = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Return the caller's profile.

    `get_current_user` requires a valid bearer (401 on missing/malformed/expired
    before this body runs — no data leak). Body: profile_id, display_name,
    selected_lens_ids (the profile's PREFERS_LENS child-lens ids, [] if none),
    and theme_preference (verbatim when set, null when the property is absent —
    read-only pass-through). 404 (not a fabricated empty profile) when the user
    has no HAS_PROFILE. When a user has >1 profile, the caller's OWN latest wins
    — created_at DESC with the id as the tie-break, so the resolution is
    deterministic even between profiles created in the same instant (or seeded
    without a created_at). The one-profile shape is the phone's contract; the
    multi-profile listing is GET /profiles.
    """
    record = session.run(
        "MATCH (u:User {id: $uid})-[:HAS_PROFILE]->(p:Profile) "
        "WITH p ORDER BY p.created_at DESC, p.id LIMIT 1 "
        "OPTIONAL MATCH (p)-[:PREFERS_LENS]->(l:Lens) "
        "RETURN p.id AS profile_id, p.display_name AS display_name, "
        "p.theme_preference AS theme_preference, collect(l.id) AS selected_lens_ids",
        uid=current_user["id"],
    ).single()

    if record is None:
        raise HTTPException(status_code=404, detail="No profile for this user")

    return {
        "profile_id": record["profile_id"],
        "display_name": record["display_name"],
        "selected_lens_ids": record["selected_lens_ids"],
        "theme_preference": record["theme_preference"],
    }


@router.get("/profiles")
def list_profiles(
    current_user: dict = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Every profile of the calling user — the family screen's picker.

    GET /profile keeps its one-profile shape for the phone's existing read;
    this is the multi-profile listing beside it (a user's profiles are e.g.
    Mom and Kid — ADR 0005 hangs family membership off exactly these). Ordered
    newest first, the same created_at DESC + id ordering /profile resolves by.
    """
    records = session.run(
        "MATCH (u:User {id: $uid})-[:HAS_PROFILE]->(p:Profile) "
        "RETURN p.id AS profile_id, p.display_name AS display_name "
        "ORDER BY p.created_at DESC, p.id",
        uid=current_user["id"],
    )
    return {"profiles": [dict(r) for r in records]}
