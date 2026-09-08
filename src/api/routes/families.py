"""Family routes — create a family, invite into it, join it, list your own.

docs/adr/0005: a family is a persistent group of profiles
(`Profile -[:MEMBER_OF]-> Family`); one member invites the others with a
shareable link (which the phone also renders as a QR) minted from the same
token machinery as the magic-link sign-in. The group changes NOTHING the
walker hears — it exists so the planner can know the party and every member
can read the same day (the read-widening lives in the trip routes).

The no-confirmation rule the trip routes follow holds here too: a family the
caller has no profile in is reported 404, never 403 — the surface must not
confirm foreign ids.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from neo4j import Session
from pydantic import BaseModel

from src.api.auth.config import FRONTEND_URL
from src.api.auth.dependencies import get_current_user
from src.api.auth.tokens import TokenError, create_invite_token, verify_invite_token
from src.api.crud.families import (
    add_member,
    caller_profile_in_family,
    create_family,
    families_for_user,
    latest_profile_id,
)
from src.api.dependencies import get_session
from src.api.routes.trips import _owned_profile_id

router = APIRouter(prefix="/families", tags=["families"])


class FamilyCreateRequest(BaseModel):
    name: str | None = None
    #: Which of the caller's profiles founds the family. Membership hangs off
    #: the profile (ADR 0005); omitted, the caller's latest profile is used —
    #: the same resolution GET /profile documents.
    profile_id: str | None = None


class FamilyInviteRequest(BaseModel):
    family_id: str


class FamilyJoinRequest(BaseModel):
    token: str
    #: Which of the caller's profiles joins. Omitted: the caller's latest.
    profile_id: str | None = None


def _resolve_caller_profile(
    session: Session, user_id: str, requested_profile_id: str | None
) -> str:
    """The caller's own profile for this operation: the requested one iff they
    own it (404 otherwise — never confirm a foreign profile id), else their
    latest. 404 when they have no profile at all."""
    if requested_profile_id is not None:
        if _owned_profile_id(session, user_id, requested_profile_id) is None:
            raise HTTPException(404, f"Profile '{requested_profile_id}' not found")
        return requested_profile_id
    profile_id = latest_profile_id(session, user_id)
    if profile_id is None:
        raise HTTPException(404, "No profile for this user")
    return profile_id


@router.post("", status_code=201)
def create_family_route(
    body: FamilyCreateRequest,
    current_user: dict = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    profile_id = _resolve_caller_profile(session, current_user["id"], body.profile_id)
    family_id = create_family(session, profile_id, body.name)
    if family_id is None:
        raise HTTPException(404, f"Profile '{profile_id}' not found")
    return {"family_id": family_id, "name": body.name, "profile_id": profile_id}


@router.post("/invite")
def invite_to_family(
    body: FamilyInviteRequest,
    current_user: dict = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Mint the shareable invite link: a typed 7-day JWT carrying the family id
    and the inviter's uid. Any member can invite (ADR 0005: "one person invites
    the others"); a non-member — or a family that does not exist — is 404."""
    if caller_profile_in_family(session, current_user["id"], body.family_id) is None:
        raise HTTPException(404, f"Family '{body.family_id}' not found")
    token = create_invite_token(body.family_id, current_user["id"])
    expires_at = datetime.fromtimestamp(
        verify_invite_token(token)["exp"], UTC
    ).isoformat()
    return {
        # Rides the /auth/* path family (the app's registered deep-link space —
        # src/api/app.py's apple-app-site-association) so a tapped link opens
        # the app; the phone's join route reads ?token=.
        "invite_url": f"{FRONTEND_URL}/auth/join-family?token={token}",
        "expires_at": expires_at,
    }


@router.post("/join")
def join_family(
    body: FamilyJoinRequest,
    current_user: dict = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Redeem an invite: verify the typed token (401 on expiry/tampering, with a
    reason the phone can turn into a plain retry message), then MERGE the
    caller's profile into the family — joining twice is one membership."""
    try:
        payload = verify_invite_token(body.token)
    except TokenError as exc:
        raise HTTPException(
            401, {"reason": "invite_invalid", "detail": str(exc)}
        ) from exc
    family_id = payload["family_id"]
    profile_id = _resolve_caller_profile(session, current_user["id"], body.profile_id)
    if not add_member(session, family_id, profile_id):
        raise HTTPException(404, f"Family '{family_id}' not found")
    return {"family_id": family_id, "profile_id": profile_id}


@router.get("/mine")
def my_families(
    current_user: dict = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Every family any of the caller's profiles belongs to, with the members'
    display names — the family screen's read."""
    return {"families": families_for_user(session, current_user["id"])}
