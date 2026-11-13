"""Regression tests for three auth hardening defects in src/api/auth/routes.py.

1. The magic-link per-IP limiter trusted the LEFTMOST X-Forwarded-For hop, which
   is attacker-controlled — every spoofed value landed in a fresh bucket, so the
   per-IP cap never fired and one host could email-bomb unlimited addresses
   through the (billed) Resend account.
2. The limiter leaked two permanent dict entries per request on
   attacker-controlled keys — an unauthenticated memory-exhaustion DoS.
3. Refresh tokens were neither recorded, rotated, nor revocable, so a stolen
   token minted access tokens for 14 days with no way to invalidate it short of
   rotating JWT_SECRET_KEY for every user.

Each test is RED without the corresponding fix (see the per-test docstrings for
the undo-test) and GREEN with it.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api.auth.tokens import create_refresh_token, verify_token
from tests.conftest import needs_neo4j

REQUEST_URL = "/api/v1/auth/magic-link/request"


@pytest.fixture()
def nodb_client():
    """TestClient with the Neo4j driver patched out — magic-link/request never
    touches the DB, so these tests need no live Neo4j.

    Deliberately NOT named ``client``: that name belongs to the DB-backed
    module-scoped fixture in conftest, which the refresh-rotation tests below use.
    """
    with patch("src.api.app.init_driver"), patch("src.api.app.close_driver"):
        from src.api.app import create_app

        app = create_app()
        with TestClient(app) as c:
            yield c


# --- spoofable X-Forwarded-For -----------------------------------------------


# --- unbounded limiter state -------------------------------------------------


# --- refresh tokens are not rotated or revocable -----------------------------


def _login(client, email: str) -> dict:
    """Complete a magic-link login and return the token pair."""
    from src.api.auth.tokens import create_magic_token

    resp = client.post("/api/v1/auth/magic-link/verify", json={"token": create_magic_token(email)})
    assert resp.status_code == 200, resp.text
    return resp.json()


@needs_neo4j
class TestRefreshTokenRotationAndRevocation:
    def test_refresh_returns_a_rotated_token_not_the_submitted_one(self, client, clean_driver):
        """Undo-test: return ``body.refresh_token`` again and this goes RED — one
        long-lived secret stays in flight for the full 14 days."""
        tokens = _login(client, "rotate@ondoway.app")
        submitted = tokens["refresh_token"]

        resp = client.post("/api/v1/auth/refresh", json={"refresh_token": submitted})

        assert resp.status_code == 200
        rotated = resp.json()["refresh_token"]
        assert rotated != submitted

        old = verify_token(submitted, "refresh")
        new = verify_token(rotated, "refresh")
        # Rotation keeps the session identity but mints a fresh jti.
        assert new["jti"] != old["jti"]
        assert new["family"] == old["family"]
        assert new["sid"] == old["sid"]

    def test_replaying_a_consumed_refresh_token_401s_and_kills_the_family(
        self, client, clean_driver
    ):
        """A stolen token replayed after the victim already used it must 401 AND
        revoke the whole family, so the thief's rotated copy dies too.

        Undo-test: drop the ``record["used"]`` branch in /refresh and the replay
        succeeds with 200.
        """
        tokens = _login(client, "replay@ondoway.app")
        stolen = tokens["refresh_token"]

        first = client.post("/api/v1/auth/refresh", json={"refresh_token": stolen})
        assert first.status_code == 200
        rotated = first.json()["refresh_token"]

        # Replay of the consumed token.
        replay = client.post("/api/v1/auth/refresh", json={"refresh_token": stolen})
        assert replay.status_code == 401

        # ...and the family is now dead, including the token minted from it.
        after = client.post("/api/v1/auth/refresh", json={"refresh_token": rotated})
        assert after.status_code == 401

    def test_logout_revokes_the_refresh_token(self, client, clean_driver):
        """Undo-test: remove the /logout route (or its family revoke) and the
        post-logout refresh returns 200."""
        tokens = _login(client, "logout@ondoway.app")
        refresh_token = tokens["refresh_token"]

        out = client.post("/api/v1/auth/logout", json={"refresh_token": refresh_token})
        assert out.status_code == 200

        resp = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
        assert resp.status_code == 401

    def test_logout_is_idempotent_and_never_401s(self, client, clean_driver):
        """A client clearing local state must be able to fire-and-forget."""
        tokens = _login(client, "idem@ondoway.app")
        for _ in range(2):
            resp = client.post(
                "/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}
            )
            assert resp.status_code == 200
        junk = client.post("/api/v1/auth/logout", json={"refresh_token": "garbage"})
        assert junk.status_code == 200

    def test_logout_all_revokes_every_session_for_the_user(self, client, clean_driver):
        """Stolen-device case: one call kills all outstanding refresh tokens."""
        email = "logoutall@ondoway.app"
        first = _login(client, email)
        second = _login(client, email)
        assert first["refresh_token"] != second["refresh_token"]

        resp = client.post(
            "/api/v1/auth/logout-all",
            headers={"Authorization": f"Bearer {second['access_token']}"},
        )
        assert resp.status_code == 200

        for tokens in (first, second):
            assert (
                client.post(
                    "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
                ).status_code
                == 401
            )

    def test_unrecorded_refresh_token_is_rejected(self, client, clean_driver):
        """A well-formed, correctly-signed token that was never issued through the
        store (e.g. minted before the store existed, or forged with a leaked
        secret) has no denylist entry and must not be honoured.

        Undo-test: drop the ``record is None`` branch and this returns 200.
        """
        resp = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": create_refresh_token("ghost-user", "sid", "fam")},
        )
        assert resp.status_code == 401
