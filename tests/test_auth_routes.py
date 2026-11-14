"""Integration tests for auth routes — needs test Neo4j running."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.api.auth.routes import WORKBENCH_USER_EMAIL
from src.api.auth.tokens import (
    create_access_token,
    create_magic_token,
    create_refresh_token,
    verify_token,
)
from src.connection import get_database
from tests.conftest import needs_neo4j


def _seed_user(driver, user_id: str, email: str):
    """Insert a test user directly into Neo4j."""
    with driver.session(database=get_database()) as session:
        session.run(
            """
            MERGE (u:User {email: $email})
            SET u.id = $uid,
                u.created_at = datetime(),
                u.last_logon = datetime()
            """,
            uid=user_id,
            email=email,
        )


def _count_users(driver, email: str) -> int:
    with driver.session(database=get_database()) as session:
        result = session.run(
            "MATCH (u:User {email: $email}) RETURN count(u) AS c",
            email=email,
        ).single()
        return result["c"]


@needs_neo4j
class TestAuthMeEndpoint:
    def test_me_with_valid_token(self, client, clean_driver):
        _seed_user(clean_driver, "user-99", "me@ondoway.app")
        token = create_access_token("user-99", "me@ondoway.app")

        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "user-99"
        assert data["email"] == "me@ondoway.app"

    def test_me_no_auth_header(self, client):
        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 401

    def test_me_invalid_token(self, client):
        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer garbage.token.here"},
        )
        assert resp.status_code == 401

    def test_me_expired_token(self, client):
        with patch("src.api.auth.tokens.ACCESS_TOKEN_EXPIRE_MINUTES", -1):
            token = create_access_token("user-99", "me@ondoway.app")

        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401

    def test_me_user_not_in_db(self, client):
        token = create_access_token("nonexistent-user", "ghost@ondoway.app")

        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401

    def test_me_refresh_token_rejected(self, client):
        token = create_refresh_token("user-99", "sess", "fam")

        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401


@needs_neo4j
class TestMagicLinkRequest:
    @patch("src.api.auth.routes.send_magic_link", new_callable=AsyncMock)
    def test_request_returns_200(self, mock_send, client):
        resp = client.post(
            "/api/v1/auth/magic-link/request",
            json={"email": "user@example.com"},
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "Magic link sent"
        mock_send.assert_called_once()

    def test_request_invalid_email_returns_422(self, client):
        resp = client.post(
            "/api/v1/auth/magic-link/request",
            json={"email": "not-an-email"},
        )
        assert resp.status_code == 422

    @patch(
        "src.api.auth.routes.send_magic_link",
        new_callable=AsyncMock,
        side_effect=Exception("Resend down"),
    )
    def test_request_email_failure_returns_502(self, mock_send, client):
        from src.api.auth.email import EmailDeliveryError

        mock_send.side_effect = EmailDeliveryError("Resend API returned 500")
        resp = client.post(
            "/api/v1/auth/magic-link/request",
            json={"email": "user@example.com"},
        )
        assert resp.status_code == 502


@needs_neo4j
class TestMagicLinkVerify:
    def test_verify_creates_user_and_returns_tokens(self, client, clean_driver):
        token = create_magic_token("newuser@ondoway.app")

        resp = client.post(
            "/api/v1/auth/magic-link/verify",
            json={"token": token},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

        # Verify user was created in Neo4j
        assert _count_users(clean_driver, "newuser@ondoway.app") == 1

    def test_verify_existing_user_no_duplicate(self, client, clean_driver):
        _seed_user(clean_driver, "existing-id", "existing@ondoway.app")
        token = create_magic_token("existing@ondoway.app")

        resp = client.post(
            "/api/v1/auth/magic-link/verify",
            json={"token": token},
        )

        assert resp.status_code == 200
        assert _count_users(clean_driver, "existing@ondoway.app") == 1

    def test_verify_expired_token_returns_401(self, client):
        with patch("src.api.auth.tokens.MAGIC_LINK_EXPIRE_MINUTES", -1):
            token = create_magic_token("user@ondoway.app")

        resp = client.post(
            "/api/v1/auth/magic-link/verify",
            json={"token": token},
        )
        assert resp.status_code == 401

    def test_verify_invalid_token_returns_401(self, client):
        resp = client.post(
            "/api/v1/auth/magic-link/verify",
            json={"token": "garbage.token"},
        )
        assert resp.status_code == 401

    def test_returned_tokens_are_valid(self, client, clean_driver):
        token = create_magic_token("valid@ondoway.app")

        resp = client.post(
            "/api/v1/auth/magic-link/verify",
            json={"token": token},
        )
        data = resp.json()

        access_payload = verify_token(data["access_token"], "access")
        assert access_payload["email"] == "valid@ondoway.app"

        refresh_payload = verify_token(data["refresh_token"], "refresh")
        assert refresh_payload["sub"] == access_payload["sub"]

    @patch("src.api.auth.routes.send_magic_link", new_callable=AsyncMock)
    def test_full_flow_request_then_verify(self, mock_send, client, clean_driver):
        # Step 1: Request magic link
        resp = client.post(
            "/api/v1/auth/magic-link/request",
            json={"email": "flow@ondoway.app"},
        )
        assert resp.status_code == 200

        # Extract the token that was passed to send_magic_link
        sent_token = mock_send.call_args[0][1]

        # Step 2: Verify magic link
        resp = client.post(
            "/api/v1/auth/magic-link/verify",
            json={"token": sent_token},
        )
        assert resp.status_code == 200
        data = resp.json()

        # Step 3: Use access token with /me
        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {data['access_token']}"},
        )
        assert resp.status_code == 200
        assert resp.json()["email"] == "flow@ondoway.app"


@needs_neo4j
class TestRefreshEndpoint:
    def test_refresh_returns_new_access_token(self, client, clean_driver):
        _seed_user(clean_driver, "refresh-user", "refresh@ondoway.app")
        token = create_magic_token("refresh@ondoway.app")
        verify_resp = client.post("/api/v1/auth/magic-link/verify", json={"token": token})
        refresh_token = verify_resp.json()["refresh_token"]

        resp = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        access_payload = verify_token(data["access_token"], "access")
        assert access_payload["email"] == "refresh@ondoway.app"

    def test_refresh_invalid_token_returns_401(self, client):
        resp = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "garbage"},
        )
        assert resp.status_code == 401

    def test_refresh_expired_token_returns_401(self, client):
        with patch("src.api.auth.tokens.REFRESH_TOKEN_EXPIRE_DAYS", -1):
            token = create_refresh_token("u", "s", "f")

        resp = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": token},
        )
        assert resp.status_code == 401

    def test_refresh_with_access_token_returns_401(self, client):
        token = create_access_token("u", "e@test.com")

        resp = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": token},
        )
        assert resp.status_code == 401

    def test_refresh_deleted_user_returns_401(self, client):
        token = create_refresh_token("deleted-user", "s", "f")

        resp = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": token},
        )
        assert resp.status_code == 401


@needs_neo4j
class TestGoogleAuthEndpoint:
    @patch("src.api.auth.routes.verify_google_id_token")
    def test_google_auth_returns_tokens(self, mock_verify, client, clean_driver):
        mock_verify.return_value = {
            "email": "google@gmail.com",
            "sub": "google-123",
            "name": "Google User",
        }

        resp = client.post(
            "/api/v1/auth/google",
            json={"id_token": "valid-google-token"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data

    @patch("src.api.auth.routes.verify_google_id_token")
    def test_google_auth_invalid_token_returns_401(self, mock_verify, client):
        from src.api.auth.tokens import TokenError

        mock_verify.side_effect = TokenError("Invalid Google ID token")

        resp = client.post(
            "/api/v1/auth/google",
            json={"id_token": "bad-token"},
        )
        assert resp.status_code == 401

    @patch("src.api.auth.routes.verify_google_id_token")
    def test_google_auth_creates_new_user(self, mock_verify, client, clean_driver):
        mock_verify.return_value = {
            "email": "newgoogle@gmail.com",
            "sub": "g-456",
            "name": "New User",
        }

        resp = client.post(
            "/api/v1/auth/google",
            json={"id_token": "tok"},
        )
        assert resp.status_code == 200
        assert _count_users(clean_driver, "newgoogle@gmail.com") == 1

    @patch("src.api.auth.routes.verify_google_id_token")
    def test_google_auth_existing_user_no_duplicate(self, mock_verify, client, clean_driver):
        _seed_user(clean_driver, "existing-g", "existingg@gmail.com")
        mock_verify.return_value = {
            "email": "existingg@gmail.com",
            "sub": "g-789",
            "name": "Existing",
        }

        resp = client.post(
            "/api/v1/auth/google",
            json={"id_token": "tok"},
        )
        assert resp.status_code == 200
        assert _count_users(clean_driver, "existingg@gmail.com") == 1

    @patch("src.api.auth.routes.verify_google_id_token")
    def test_google_access_token_works_with_me(self, mock_verify, client, clean_driver):
        mock_verify.return_value = {
            "email": "gme@gmail.com",
            "sub": "g-000",
            "name": "Me",
        }

        resp = client.post(
            "/api/v1/auth/google",
            json={"id_token": "tok"},
        )
        data = resp.json()

        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {data['access_token']}"},
        )
        assert resp.status_code == 200
        assert resp.json()["email"] == "gme@gmail.com"


@needs_neo4j
class TestWorkbenchSession:
    """The editorial workbench's own identity: the workbench runs the app's own
    authenticated routes, never a parallel copy of them.

    The full telling and the keep-exploring extras are written by
    POST /trips/{id}/compose and voiced by POST /audio/stops/{id}/keep-exploring —
    both ownership-scoped and authenticated. The workbench reaches them by signing
    in, not by anyone loosening those routes, so what matters here is that the
    identity this mints is REAL (the trips router accepts it), that asking twice
    does not fork a second operator, and that the route is fail-closed.
    """

    def test_it_mints_an_identity_that_works(self, client, clean_driver):
        resp = client.post("/api/v1/auth/workbench-session")

        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == WORKBENCH_USER_EMAIL
        assert data["profile_id"]
        assert data["token_type"] == "bearer"
        assert verify_token(data["access_token"], "access")["sub"] == data["user_id"]

        me = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {data['access_token']}"},
        )
        assert me.status_code == 200
        assert me.json()["email"] == WORKBENCH_USER_EMAIL

    def test_asking_twice_returns_the_same_operator(self, client, clean_driver):
        first = client.post("/api/v1/auth/workbench-session").json()
        second = client.post("/api/v1/auth/workbench-session").json()

        assert first["user_id"] == second["user_id"]
        assert first["profile_id"] == second["profile_id"]
        assert _count_users(clean_driver, WORKBENCH_USER_EMAIL) == 1

    def test_the_identity_opens_the_tourists_own_routes(self, client, clean_driver):
        """The point of the whole endpoint: the trips router accepts this pair.

        ``GET /trips`` is ownership-scoped exactly as ``/trips/generate`` and
        ``/trips/{id}/compose`` are (``_owned_profile_id``), and needs no corpus or
        routing to answer — so it proves the token and the profile really belong to
        each other without paying for a tour.
        """
        session = client.post("/api/v1/auth/workbench-session").json()
        headers = {"Authorization": f"Bearer {session['access_token']}"}

        owned = client.get(f"/api/v1/trips?profile_id={session['profile_id']}", headers=headers)
        assert owned.status_code == 200
        assert owned.json() == []

        # And ownership scoping is untouched: a profile this operator does not own
        # is still a 404, never a 403 and never a read.
        foreign = client.get("/api/v1/trips?profile_id=someone-elses-profile", headers=headers)
        assert foreign.status_code == 404

    @pytest.mark.parametrize("value", ["", "false", "0", "flase", "  "])
    def test_it_is_fail_closed_without_an_explicit_yes(self, client, monkeypatch, value):
        """Unset, off, or typo'd keeps the credential route CLOSED.

        This is the security property: production pins WORKBENCH_API_ENABLED="false"
        (render.yaml), and a route that mints access tokens must be a 404 there —
        indistinguishable from one that was never mounted. Read per-request, so
        setting the env is enough; no app rebuild.
        """
        monkeypatch.setenv("WORKBENCH_API_ENABLED", value)
        resp = client.post("/api/v1/auth/workbench-session")
        assert resp.status_code == 404

    def test_it_opens_on_an_explicit_yes(self, client, monkeypatch):
        monkeypatch.setenv("WORKBENCH_API_ENABLED", "TRUE")
        assert client.post("/api/v1/auth/workbench-session").status_code == 200


@needs_neo4j
class TestCrossProviderIdentity:
    """User who signs in with Google OAuth and then magic link (same email)
    must resolve to the same User node — not create a duplicate."""

    @patch("src.api.auth.routes.verify_google_id_token")
    def test_google_then_magic_link_same_user(self, mock_verify, client, clean_driver):
        email = "crosstest@gmail.com"

        # Step 1: Sign in with Google
        mock_verify.return_value = {"email": email, "sub": "g-cross-1", "name": "Cross"}
        resp = client.post("/api/v1/auth/google", json={"id_token": "tok"})
        assert resp.status_code == 200
        google_user_id = verify_token(resp.json()["access_token"], "access")["sub"]

        # Step 2: Sign in with magic link (same email)
        magic_token = create_magic_token(email)
        resp = client.post("/api/v1/auth/magic-link/verify", json={"token": magic_token})
        assert resp.status_code == 200
        magic_user_id = verify_token(resp.json()["access_token"], "access")["sub"]

        # Same user, not a duplicate
        assert google_user_id == magic_user_id
        assert _count_users(clean_driver, email) == 1

    @patch("src.api.auth.routes.verify_google_id_token")
    def test_magic_link_then_google_same_user(self, mock_verify, client, clean_driver):
        email = "crosstest2@gmail.com"

        # Step 1: Sign in with magic link
        magic_token = create_magic_token(email)
        resp = client.post("/api/v1/auth/magic-link/verify", json={"token": magic_token})
        assert resp.status_code == 200
        magic_user_id = verify_token(resp.json()["access_token"], "access")["sub"]

        # Step 2: Sign in with Google (same email)
        mock_verify.return_value = {"email": email, "sub": "g-cross-2", "name": "Cross2"}
        resp = client.post("/api/v1/auth/google", json={"id_token": "tok"})
        assert resp.status_code == 200
        google_user_id = verify_token(resp.json()["access_token"], "access")["sub"]

        # Same user, not a duplicate
        assert google_user_id == magic_user_id
        assert _count_users(clean_driver, email) == 1
