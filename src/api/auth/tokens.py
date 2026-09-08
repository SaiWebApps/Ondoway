"""JWT token creation and verification for access, refresh, magic link, and
family invite tokens."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt

from src.api.auth.config import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    JWT_ALGORITHM,
    JWT_SECRET_KEY,
    MAGIC_LINK_EXPIRE_MINUTES,
    MAGIC_LINK_SECRET_KEY,
    REFRESH_TOKEN_EXPIRE_DAYS,
)


class TokenError(Exception):
    """Raised when token creation or verification fails."""


def create_access_token(user_id: str, email: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "email": email,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: str, session_id: str, token_family: str) -> str:
    now = datetime.now(UTC)
    jti = str(uuid.uuid4())
    payload = {
        "sub": user_id,
        "sid": session_id,
        "family": token_family,
        "jti": jti,
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def create_magic_token(email: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "email": email,
        "type": "magic",
        "iat": now,
        "exp": now + timedelta(minutes=MAGIC_LINK_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, MAGIC_LINK_SECRET_KEY, algorithm=JWT_ALGORITHM)


#: How long a family invite link stays usable. A shareable link travels over
#: any channel (docs/adr/0005) and may sit in a chat for days before it is
#: tapped, so it lives longer than a magic link — but it still expires, because
#: an invite is a standing door into a family's shared days.
INVITE_TOKEN_EXPIRE_DAYS: int = 7


def create_invite_token(family_id: str, inviter_user_id: str) -> str:
    """Mint a family invite token: the magic pair's mould, typed "invite".

    Signed with MAGIC_LINK_SECRET_KEY (like the magic link, and unlike the
    bearer pair) so a leaked invite can never be replayed against the access
    verifier even if a type check is ever missed.
    """
    now = datetime.now(UTC)
    payload = {
        "family_id": family_id,
        "sub": inviter_user_id,
        "type": "invite",
        "iat": now,
        "exp": now + timedelta(days=INVITE_TOKEN_EXPIRE_DAYS),
    }
    return jwt.encode(payload, MAGIC_LINK_SECRET_KEY, algorithm=JWT_ALGORITHM)


def verify_invite_token(token: str) -> dict:
    """Verify a family invite token and return its payload ({family_id, sub, …}).

    Raises TokenError on expiry, tampering, wrong type, or a missing family_id.
    """
    try:
        payload = jwt.decode(token, MAGIC_LINK_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Invite has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError(f"Invalid invite: {exc}") from exc

    if payload.get("type") != "invite":
        raise TokenError(f"Wrong token type: expected 'invite', got {payload.get('type')!r}")
    if not payload.get("family_id"):
        raise TokenError("Invite token missing family_id")
    return payload


def verify_token(token: str, expected_type: str) -> dict:
    """Verify a JWT and return its payload. Raises TokenError on failure."""
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError(f"Invalid token: {exc}") from exc

    if payload.get("type") != expected_type:
        raise TokenError(
            f"Wrong token type: expected {expected_type!r}, got {payload.get('type')!r}"
        )
    return payload


def verify_magic_token(token: str) -> str:
    """Verify a magic link token and return the email. Raises TokenError on failure."""
    try:
        payload = jwt.decode(token, MAGIC_LINK_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Magic link has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError(f"Invalid magic link: {exc}") from exc

    if payload.get("type") != "magic":
        raise TokenError(f"Wrong token type: expected 'magic', got {payload.get('type')!r}")

    email = payload.get("email")
    if not email:
        raise TokenError("Magic link token missing email")
    return email
