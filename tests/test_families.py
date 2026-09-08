"""Families — a persistent group of profiles, joined by one shareable invite.

docs/adr/0005: a Family is its own node, joined by profiles
(`Profile -[:MEMBER_OF]-> Family`); one member invites the others with a
shareable link minted from the same token machinery as the magic-link sign-in.

These tests run on the lane's TEST graph (the conftest fixtures; under
`make test-file LANE=4` that is test4 on :7697) with no corpus: the family
surface never plans a day, so a hand-planted User/Profile pair is the whole
world it needs. The client is the tests/test_trip_api.py mould — create_app()
against the env-configured test graph, every request signed with a token
minted just now.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.auth.config import JWT_ALGORITHM, MAGIC_LINK_SECRET_KEY
from src.api.auth.tokens import (
    INVITE_TOKEN_EXPIRE_DAYS,
    TokenError,
    create_access_token,
    create_invite_token,
    verify_invite_token,
)
from tests.conftest import needs_neo4j

FIONA_USER_ID = "p10-families-test-user-fiona"
FIONA_EMAIL = "p10-families-fiona@example.test"
FIONA_PROFILE_ID = "p10-families-test-profile-fiona"
DEV_USER_ID = "p10-families-test-user-dev"
DEV_EMAIL = "p10-families-dev@example.test"
DEV_PROFILE_ID = "p10-families-test-profile-dev"


def _bearer(user_id: str, email: str) -> dict[str, str]:
    """Headers signing THIS request as the given identity, minted just now."""
    return {"Authorization": f"Bearer {create_access_token(user_id, email)}"}


@pytest.fixture(scope="module")
def graph(clean_driver):
    """Two disposable identities on the clean test graph: Fiona (creates the
    family) and Dev (joins it). `clean_driver` has already wiped the graph and
    applied the schema, so the Family unique-id constraint is live here."""
    with clean_driver.session() as s:
        for uid, email, pid, name in (
            (FIONA_USER_ID, FIONA_EMAIL, FIONA_PROFILE_ID, "Fiona"),
            (DEV_USER_ID, DEV_EMAIL, DEV_PROFILE_ID, "Dev"),
        ):
            s.run("MERGE (u:User {id: $uid}) SET u.email = $email", uid=uid, email=email)
            s.run(
                "MERGE (p:Profile {id: $pid}) "
                "SET p.display_name = $name, p.created_at = datetime() "
                "WITH p MATCH (u:User {id: $uid}) MERGE (u)-[:HAS_PROFILE]->(p)",
                pid=pid,
                uid=uid,
                name=name,
            )
    return clean_driver


@pytest.fixture(scope="module")
def client(graph):
    app = create_app()
    with TestClient(app) as c:
        yield c


# ── The invite token itself (no DB) ──────────────────────────────────────────


class TestInviteToken:
    def test_mint_and_verify_round_trip(self):
        token = create_invite_token("fam-1", "user-1")
        payload = verify_invite_token(token)
        assert payload["family_id"] == "fam-1"
        assert payload["sub"] == "user-1"
        assert payload["type"] == "invite"

    def test_seven_day_expiry(self):
        token = create_invite_token("fam-1", "user-1")
        payload = verify_invite_token(token)
        lifetime = payload["exp"] - payload["iat"]
        assert lifetime == INVITE_TOKEN_EXPIRE_DAYS * 24 * 3600

    def test_wrong_type_refused(self):
        with pytest.raises(TokenError):
            verify_invite_token(create_access_token("user-1", "a@b.test"))

    def test_garbage_refused(self):
        with pytest.raises(TokenError):
            verify_invite_token("not-a-jwt")

    def test_expired_invite_refused(self):
        now = datetime.now(UTC)
        expired = jwt.encode(
            {
                "family_id": "fam-1",
                "sub": "user-1",
                "type": "invite",
                "iat": now - timedelta(days=8),
                "exp": now - timedelta(days=1),
            },
            MAGIC_LINK_SECRET_KEY,
            algorithm=JWT_ALGORITHM,
        )
        with pytest.raises(TokenError):
            verify_invite_token(expired)


# ── The wire: create → invite → join → mine ──────────────────────────────────


@needs_neo4j
class TestFamilyFlow:
    @pytest.fixture(scope="class")
    def family_id(self, client) -> str:
        resp = client.post(
            "/api/v1/families",
            json={"name": "The Fionas"},
            headers=_bearer(FIONA_USER_ID, FIONA_EMAIL),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["family_id"]
        return body["family_id"]

    @pytest.fixture(scope="class")
    def invite(self, client, family_id) -> dict:
        resp = client.post(
            "/api/v1/families/invite",
            json={"family_id": family_id},
            headers=_bearer(FIONA_USER_ID, FIONA_EMAIL),
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    def test_invite_carries_a_shareable_url_and_expiry(self, invite, family_id):
        assert "token=" in invite["invite_url"]
        token = invite["invite_url"].split("token=", 1)[1]
        payload = verify_invite_token(token)
        assert payload["family_id"] == family_id
        assert payload["sub"] == FIONA_USER_ID
        expires = datetime.fromisoformat(invite["expires_at"])
        assert expires > datetime.now(UTC) + timedelta(days=6)

    def test_join_makes_a_family_of_two(self, client, invite, family_id):
        token = invite["invite_url"].split("token=", 1)[1]
        resp = client.post(
            "/api/v1/families/join",
            json={"token": token},
            headers=_bearer(DEV_USER_ID, DEV_EMAIL),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["family_id"] == family_id

        mine = client.get(
            "/api/v1/families/mine", headers=_bearer(FIONA_USER_ID, FIONA_EMAIL)
        )
        assert mine.status_code == 200, mine.text
        families = mine.json()["families"]
        assert len(families) == 1
        members = {m["display_name"] for m in families[0]["members"]}
        assert members == {"Fiona", "Dev"}

    def test_both_members_see_the_same_family(self, client, invite, family_id):
        for uid, email in ((FIONA_USER_ID, FIONA_EMAIL), (DEV_USER_ID, DEV_EMAIL)):
            mine = client.get("/api/v1/families/mine", headers=_bearer(uid, email))
            assert mine.status_code == 200
            assert [f["family_id"] for f in mine.json()["families"]] == [family_id]

    def test_joining_twice_is_idempotent(self, client, graph, invite, family_id):
        token = invite["invite_url"].split("token=", 1)[1]
        again = client.post(
            "/api/v1/families/join",
            json={"token": token},
            headers=_bearer(DEV_USER_ID, DEV_EMAIL),
        )
        assert again.status_code == 200, again.text
        with graph.session() as s:
            edges = s.run(
                "MATCH (p:Profile {id: $pid})-[m:MEMBER_OF]->(f:Family {id: $fid}) "
                "RETURN count(m) AS n",
                pid=DEV_PROFILE_ID,
                fid=family_id,
            ).single()["n"]
        assert edges == 1

    def test_expired_token_is_a_typed_401(self, client):
        now = datetime.now(UTC)
        expired = jwt.encode(
            {
                "family_id": "fam-x",
                "sub": FIONA_USER_ID,
                "type": "invite",
                "iat": now - timedelta(days=8),
                "exp": now - timedelta(days=1),
            },
            MAGIC_LINK_SECRET_KEY,
            algorithm=JWT_ALGORITHM,
        )
        resp = client.post(
            "/api/v1/families/join",
            json={"token": expired},
            headers=_bearer(DEV_USER_ID, DEV_EMAIL),
        )
        assert resp.status_code == 401, resp.text
        assert resp.json()["detail"]["reason"] == "invite_invalid"

    def test_token_for_a_deleted_family_is_404(self, client):
        token = create_invite_token("family-that-never-existed", FIONA_USER_ID)
        resp = client.post(
            "/api/v1/families/join",
            json={"token": token},
            headers=_bearer(DEV_USER_ID, DEV_EMAIL),
        )
        assert resp.status_code == 404, resp.text

    def test_invite_to_a_family_the_caller_is_not_in_is_404(self, client, family_id):
        """The no-confirmation rule: a family id the caller has no profile in is
        indistinguishable from one that does not exist."""
        with_stranger = client.post(
            "/api/v1/families/invite",
            json={"family_id": family_id},
            headers=_bearer("p10-families-stranger", "stranger@example.test"),
        )
        # The stranger user does not even exist on the graph -> 401 from auth;
        # an EXISTING user outside the family gets 404. Plant one to prove it.
        assert with_stranger.status_code == 401
        resp = client.post(
            "/api/v1/families/invite",
            json={"family_id": "no-such-family"},
            headers=_bearer(FIONA_USER_ID, FIONA_EMAIL),
        )
        assert resp.status_code == 404
