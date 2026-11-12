"""Auth routes — login, token management, and user info."""

from __future__ import annotations

import os
import time
import uuid
from collections import deque
from datetime import UTC, datetime
from threading import Lock

from fastapi import APIRouter, Depends, HTTPException, Request, status
from neo4j import Session

from src.api.auth.apple import verify_apple_id_token
from src.api.auth.dependencies import get_current_user
from src.api.auth.email import EmailDeliveryError, send_magic_link
from src.api.auth.google import verify_google_id_token
from src.api.auth.schemas import (
    AppleAuthRequest,
    GoogleAuthRequest,
    MagicLinkRequest,
    MagicLinkVerifyRequest,
    OnboardingRequest,
    OnboardingResponse,
    RefreshRequest,
    TokenResponse,
    UserResponse,
    WorkbenchSessionResponse,
)
from src.api.auth.tokens import (
    TokenError,
    create_access_token,
    create_magic_token,
    create_refresh_token,
    verify_magic_token,
    verify_token,
)
from src.api.dependencies import get_session

router = APIRouter(prefix="/auth", tags=["auth"])

_MERGE_USER = """
MERGE (u:User {email: $email})
SET u.id = coalesce(u.id, randomUUID()),
    u.created_at = coalesce(u.created_at, datetime()),
    u.last_logon = datetime()
RETURN u
"""

_FIND_BY_GOOGLE_SUB = "MATCH (u:User {google_sub: $sub}) RETURN u"
_SET_GOOGLE_SUB = """
MATCH (u:User {email: $email})
SET u.google_sub = coalesce(u.google_sub, $google_sub)
"""

_FIND_BY_APPLE_SUB = "MATCH (u:User {apple_sub: $sub}) RETURN u"
_SET_APPLE_SUB = """
MATCH (u:User {email: $email})
SET u.apple_sub = coalesce(u.apple_sub, $apple_sub)
"""


# --- Refresh-token store: rotation + revocation ------------------------------
# A refresh token is valid for REFRESH_TOKEN_EXPIRE_DAYS (14). Minting one and
# never recording it means a LEAKED token silently mints fresh access tokens for
# the whole window, with no denylist, no reuse detection and no logout — the only
# remedy would be rotating JWT_SECRET_KEY for every user at once. So persist the
# jti and rotate on every use: presenting an already-consumed jti is the signature
# of a stolen token being replayed, and the standard response is to revoke the
# entire token family (both the thief's copy and the victim's).
#
# The MERGE key is the jti, a uuid4 minted per token — globally unique, so the
# key is safe across users and cities.
_MERGE_REFRESH_TOKEN = """
MATCH (u:User {id: $user_id})
MERGE (t:RefreshToken {jti: $jti})
ON CREATE SET t.sid = $sid,
              t.family = $family,
              t.user_id = $user_id,
              t.issued_at = datetime(),
              t.expires_at = datetime($expires_at),
              t.used = false,
              t.revoked = false
MERGE (u)-[r:HAS_REFRESH_TOKEN]->(t)
ON CREATE SET r.id = randomUUID(), r.created_at = datetime()
"""

_LOOKUP_REFRESH_TOKEN = """
MATCH (t:RefreshToken {jti: $jti})
RETURN t.used AS used, t.revoked AS revoked, t.family AS family, t.user_id AS user_id
"""

_MARK_REFRESH_TOKEN_USED = "MATCH (t:RefreshToken {jti: $jti}) SET t.used = true"

_REVOKE_REFRESH_FAMILY = "MATCH (t:RefreshToken {family: $family}) SET t.revoked = true"

_REVOKE_ALL_FOR_USER = "MATCH (t:RefreshToken {user_id: $user_id}) SET t.revoked = true"

# Expired rows can never authenticate anything, so drop them opportunistically —
# otherwise the store grows by one node per login for the life of the deployment.
_DELETE_EXPIRED_REFRESH_TOKENS = """
MATCH (t:RefreshToken) WHERE t.expires_at < datetime() DETACH DELETE t
"""
_REFRESH_CLEANUP_INTERVAL_S = int(os.getenv("REFRESH_TOKEN_CLEANUP_INTERVAL_S", "3600"))
_last_refresh_cleanup = 0.0


def _issue_refresh_token(session: Session, user_id: str, session_id: str, token_family: str) -> str:
    """Mint a refresh token AND record it, so it can later be rotated/revoked."""
    global _last_refresh_cleanup

    token = create_refresh_token(user_id, session_id, token_family)
    payload = verify_token(token, "refresh")
    expires_at = datetime.fromtimestamp(payload["exp"], UTC).isoformat()
    session.run(
        _MERGE_REFRESH_TOKEN,
        jti=payload["jti"],
        sid=session_id,
        family=token_family,
        user_id=user_id,
        expires_at=expires_at,
    )

    now = time.monotonic()
    if now - _last_refresh_cleanup >= _REFRESH_CLEANUP_INTERVAL_S:
        _last_refresh_cleanup = now
        session.run(_DELETE_EXPIRED_REFRESH_TOKENS)

    return token


# --- Abuse ceiling for magic-link sends -------------------------------------
#
# THE PER-IP KEY IS GONE AND IS NOT COMING BACK. Every limiter in this codebase
# was deleted on 2026-07-31 because they were keyed on the CLIENT IP, and per-IP
# throttling punishes exactly the wrong people: mobile carriers put thousands of
# subscribers behind a handful of addresses, a hotel is one address, and the
# owner's own workbench is one address. That key is the defect, and nothing here
# reintroduces it.
#
# What is left is unauthenticated and sends a REAL email through Resend to
# whatever address the body names — the endpoint is both login AND signup, since
# ``/magic-link/verify`` is what MERGEs the user. Uncapped, anyone who finds the
# URL can bomb one victim's inbox or fan out across strangers, and Resend
# enforces its abuse policy by SUSPENDING the account. That failure mode does not
# cost money; it takes login away from everybody, including the owner.
#
# So the ceiling is keyed on the two things a legitimate person can never trip:
#
#   1. THE TARGET EMAIL. Counts sends *to* an address. A real person asks for a
#      link to their own inbox a handful of times; an attacker hits one address
#      hundreds of times. You are never the address being bombed.
#   2. A GLOBAL CEILING. One person logging in is a rounding error against a
#      whole-window total; a fan-out across thousands of addresses is not.
#
# Both defaults are deliberately LOOSE. They are an abuse ceiling, not a quota —
# the intent is that only an attack ever touches them. Set either to 0 to
# disable it; that is a knob, not a removal.
_MAGIC_LINK_PER_EMAIL_MAX = int(os.getenv("MAGIC_LINK_PER_EMAIL_MAX", "30"))
_MAGIC_LINK_GLOBAL_MAX = int(os.getenv("MAGIC_LINK_GLOBAL_MAX", "500"))
_MAGIC_LINK_WINDOW_S = int(os.getenv("MAGIC_LINK_WINDOW_S", "3600"))
_magic_link_by_email: dict[str, deque[float]] = {}
_magic_link_global: deque[float] = deque()
_magic_link_lock = Lock()


def reset_magic_link_ceiling() -> None:
    """Clear the counters. For tests; never called by the product."""
    with _magic_link_lock:
        _magic_link_by_email.clear()
        _magic_link_global.clear()


def _magic_link_ceiling(email: str) -> None:
    """Raise 429 if this ADDRESS, or the deployment as a whole, is being flooded.

    Never considers the caller's IP — see the block above for why.

    Memory: the per-email keys derive from an attacker-supplied request body, so
    every exit path (including the 429) drops keys whose window has emptied.
    Without that, each rejected request would leak a permanent dict entry and an
    unauthenticated caller could grow the worker's RSS until it is OOM-killed.
    """
    now = time.monotonic()
    cutoff = now - _MAGIC_LINK_WINDOW_S
    key = email.strip().lower()
    with _magic_link_lock:
        for stale in [k for k, v in _magic_link_by_email.items() if not v or v[-1] <= cutoff]:
            del _magic_link_by_email[stale]

        if _MAGIC_LINK_GLOBAL_MAX > 0:
            while _magic_link_global and _magic_link_global[0] <= cutoff:
                _magic_link_global.popleft()
            if len(_magic_link_global) >= _MAGIC_LINK_GLOBAL_MAX:
                retry = max(1, int(_magic_link_global[0] + _MAGIC_LINK_WINDOW_S - now))
                raise HTTPException(
                    status_code=429,
                    detail="Too many magic-link requests right now, please retry shortly",
                    headers={"Retry-After": str(retry)},
                )

        if _MAGIC_LINK_PER_EMAIL_MAX > 0:
            hits = _magic_link_by_email.setdefault(key, deque())
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= _MAGIC_LINK_PER_EMAIL_MAX:
                retry = max(1, int(hits[0] + _MAGIC_LINK_WINDOW_S - now))
                if not hits:
                    _magic_link_by_email.pop(key, None)
                raise HTTPException(
                    status_code=429,
                    detail="Too many magic links requested for this address, please retry later",
                    headers={"Retry-After": str(retry)},
                )
            hits.append(now)

        if _MAGIC_LINK_GLOBAL_MAX > 0:
            _magic_link_global.append(now)


@router.get("/me", response_model=UserResponse)
def me(current_user: dict = Depends(get_current_user)):
    return UserResponse(**current_user)


# ── The workbench's own identity ─────────────────────────────────────────────
#
# CLAUDE.md rule 1: the workbench and the app run the EXACT SAME code for everything
# they share. The full telling (`plan_premium_full_telling`) and the keep-exploring
# extras (`build_poi_extra_beats` / `build_poi_extra_narration`) are written by
# POST /trips/{id}/compose and voiced by POST /audio/stops/{id}/keep-exploring — the
# tourist's own endpoints — so an editor can only ever preview that content by calling
# THOSE endpoints. Both are ownership-scoped and authenticated, and that stays exactly
# as it is: adding an auth-optional branch to the phone's routes would be a second door
# into one stage AND a security regression on the tourist's surface. So the workbench
# signs in like anybody else, and this is where it gets its identity: the workbench
# writes a tour only through the saved-trip pair (POST /trips/generate, then
# POST /trips/{id}/compose), exactly as the phone does.
#
# The workbench has no inbox and no Apple/Google account, so there is no link to send
# and no token to verify; it mints its session directly. That makes it a real
# credential-issuing route, so it is GATED by the same fail-closed flag that already
# protects the workbench CRUD routers (src/api/app.py) and the audio spend surface
# (src/api/routes/audio.py): production pins WORKBENCH_API_ENABLED="false"
# (render.yaml), where this answers 404 — indistinguishable from a route that was never
# mounted. The check runs per-REQUEST rather than at import for the reason audio.py
# gives: this module is imported once but create_app() may run many times (tests) with
# a different env, and a request-time gate can never be stale.

#: One stable workbench operator. A fixed address so repeated sign-ins MERGE onto the
#: same User and its trips accumulate under one profile instead of forking a new
#: identity per click. `.local` is reserved and unroutable — nothing can ever be
#: delivered to it, which is the point: this account is never emailed.
WORKBENCH_USER_EMAIL = "workbench@ondoway.local"
WORKBENCH_PROFILE_NAME = "Editorial Workbench"

#: The profile half of the same MERGE the onboarding route does, minus the lenses.
#: A lens-free profile is not a degraded one: `_resolve_lenses` falls back to None and
#: the engine plans unbiased, which is what an editor auditioning the corpus wants —
#: and the tour form still sends its own `lenses` when the editor names some.
_MERGE_WORKBENCH_PROFILE = """
MATCH (u:User {id: $user_id})
MERGE (u)-[hp:HAS_PROFILE]->(p:Profile {display_name: $display_name})
ON CREATE SET p.id = randomUUID(), p.created_at = datetime(),
              hp.id = randomUUID(), hp.created_at = datetime()
RETURN p.id AS profile_id
"""


def require_workbench() -> None:
    """Dependency: 404 unless the workbench surface is explicitly enabled.

    The gate is ``app._workbench_api_enabled``, the one fail-closed parser: only an
    explicit truthy value opens the surface. It is imported here, at request time,
    because app imports this module at load.
    """
    from src.api.app import _workbench_api_enabled  # circular at module load

    if not _workbench_api_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


@router.post(
    "/workbench-session",
    response_model=WorkbenchSessionResponse,
    dependencies=[Depends(require_workbench)],
)
def workbench_session(session: Session = Depends(get_session)):
    """Mint the editorial workbench's access token and its profile id.

    Idempotent: both halves are a MERGE, so calling it twice returns the same user and
    the same profile with a fresh token. The page calls it before the tourist flow and
    again after a 401, which is why no refresh token is issued — there is nothing to
    rotate that asking again does not replace.
    """
    result = session.run(_MERGE_USER, email=WORKBENCH_USER_EMAIL).single()
    user_node = result["u"]
    user_id = user_node.get("id")
    profile = session.run(
        _MERGE_WORKBENCH_PROFILE,
        user_id=user_id,
        display_name=WORKBENCH_PROFILE_NAME,
    ).single()
    return WorkbenchSessionResponse(
        access_token=create_access_token(user_id, WORKBENCH_USER_EMAIL),
        user_id=user_id,
        email=WORKBENCH_USER_EMAIL,
        profile_id=profile["profile_id"],
    )


@router.post("/magic-link/request", status_code=200)
async def magic_link_request(request: Request, body: MagicLinkRequest):
    _magic_link_ceiling(body.email)
    token = create_magic_token(body.email)
    try:
        await send_magic_link(body.email, token)
    except EmailDeliveryError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to send magic link email: {exc}",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Email delivery connection error: {type(exc).__name__}: {exc}",
        ) from exc
    return {"message": "Magic link sent"}


@router.post("/magic-link/verify", response_model=TokenResponse)
def magic_link_verify(
    body: MagicLinkVerifyRequest,
    session: Session = Depends(get_session),
):
    try:
        email = verify_magic_token(body.token)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    result = session.run(_MERGE_USER, email=email).single()
    user_node = result["u"]
    user_id = user_node.get("id")

    session_id = str(uuid.uuid4())
    token_family = str(uuid.uuid4())

    return TokenResponse(
        access_token=create_access_token(user_id, email),
        refresh_token=_issue_refresh_token(session, user_id, session_id, token_family),
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    body: RefreshRequest,
    session: Session = Depends(get_session),
):
    try:
        payload = verify_token(body.refresh_token, "refresh")
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    user_id = payload["sub"]
    jti = payload.get("jti")
    family = payload.get("family")
    session_id = payload.get("sid") or str(uuid.uuid4())

    # A refresh token that carries no jti/family predates the rotation store and
    # cannot be tracked or revoked — refuse it rather than honour it blindly.
    if not jti or not family:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token is not rotatable, please sign in again",
        )

    record = session.run(_LOOKUP_REFRESH_TOKEN, jti=jti).single()
    if record is None or record["revoked"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token is no longer valid",
        )

    if record["used"]:
        # Replay of an already-consumed token: either the legitimate client or a
        # thief holds a copy. We cannot tell which, so revoke the whole family —
        # the standard reuse-detection response — and force a fresh sign-in.
        session.run(_REVOKE_REFRESH_FAMILY, family=family)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token reuse detected, session revoked",
        )

    result = session.run("MATCH (u:User {id: $uid}) RETURN u.email AS email", uid=user_id).single()

    if not result:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    session.run(_MARK_REFRESH_TOKEN_USED, jti=jti)

    return TokenResponse(
        access_token=create_access_token(user_id, result["email"]),
        # Rotate: same sid + family, fresh jti. Returning the submitted token
        # would leave a single long-lived secret in flight for 14 days.
        refresh_token=_issue_refresh_token(session, user_id, session_id, family),
    )


@router.post("/logout", status_code=200)
def logout(
    body: RefreshRequest,
    session: Session = Depends(get_session),
):
    """Revoke the presented refresh token's family (sign out this device).

    Idempotent and never 401s: a client clearing its local tokens must be able to
    fire-and-forget this call even when the token it holds is already expired or
    unknown."""
    try:
        payload = verify_token(body.refresh_token, "refresh")
    except TokenError:
        return {"message": "Logged out"}

    family = payload.get("family")
    if family:
        session.run(_REVOKE_REFRESH_FAMILY, family=family)
    return {"message": "Logged out"}


@router.post("/logout-all", status_code=200)
def logout_all(
    current_user: dict = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Revoke every refresh token for the authenticated user (stolen-device case)."""
    session.run(_REVOKE_ALL_FOR_USER, user_id=current_user["id"])
    return {"message": "All sessions revoked"}


@router.post("/google", response_model=TokenResponse)
def google_auth(
    body: GoogleAuthRequest,
    session: Session = Depends(get_session),
):
    try:
        google_info = verify_google_id_token(body.id_token)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    email = google_info["email"]
    google_sub = google_info.get("sub", "")

    existing = session.run(_FIND_BY_GOOGLE_SUB, sub=google_sub).single() if google_sub else None
    if existing:
        user_node = existing["u"]
        email = user_node["email"]
    else:
        result = session.run(_MERGE_USER, email=email).single()
        user_node = result["u"]

    user_id = user_node.get("id")

    if google_sub:
        session.run(_SET_GOOGLE_SUB, email=email, google_sub=google_sub)

    session_id = str(uuid.uuid4())
    token_family = str(uuid.uuid4())

    return TokenResponse(
        access_token=create_access_token(user_id, email),
        refresh_token=_issue_refresh_token(session, user_id, session_id, token_family),
    )


@router.post("/apple", response_model=TokenResponse)
def apple_auth(
    body: AppleAuthRequest,
    session: Session = Depends(get_session),
):
    try:
        apple_info = verify_apple_id_token(body.identity_token, nonce=body.nonce)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    email = apple_info["email"]
    apple_sub = apple_info.get("sub", "")

    existing = session.run(_FIND_BY_APPLE_SUB, sub=apple_sub).single() if apple_sub else None
    if existing:
        user_node = existing["u"]
        email = user_node["email"]
    else:
        result = session.run(_MERGE_USER, email=email).single()
        user_node = result["u"]

    user_id = user_node.get("id")

    if apple_sub:
        session.run(_SET_APPLE_SUB, email=email, apple_sub=apple_sub)

    session_id = str(uuid.uuid4())
    token_family = str(uuid.uuid4())

    return TokenResponse(
        access_token=create_access_token(user_id, email),
        refresh_token=_issue_refresh_token(session, user_id, session_id, token_family),
    )


_ONBOARDING_QUERY = """
MATCH (u:User {id: $user_id})
MERGE (u)-[hp:HAS_PROFILE]->(p:Profile {display_name: $display_name})
ON CREATE SET p.id = randomUUID(), p.created_at = datetime(),
              hp.id = randomUUID(), hp.created_at = datetime()
WITH p
UNWIND $lens_ids AS lid
MATCH (lens:Lens {id: lid, is_parent: false})
MERGE (p)-[r:PREFERS_LENS]->(lens)
ON CREATE SET r.id = randomUUID(), r.created_at = datetime()
RETURN p.id AS profile_id, p.display_name AS display_name, count(lens) AS lens_count
"""


@router.post("/onboarding/complete", response_model=OnboardingResponse)
def onboarding_complete(
    body: OnboardingRequest,
    current_user: dict = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if len(body.lens_ids) < 3:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="At least 3 lenses required",
        )

    user_id = current_user["id"]
    email = current_user["email"]
    display_name = email.split("@")[0]

    result = session.run(
        _ONBOARDING_QUERY,
        user_id=user_id,
        display_name=display_name,
        lens_ids=body.lens_ids,
    ).single()

    if result is None or result["lens_count"] == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User or lenses not found",
        )

    return OnboardingResponse(
        profile_id=result["profile_id"],
        display_name=result["display_name"],
        lens_count=result["lens_count"],
    )
