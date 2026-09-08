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
from src.api.auth.config import JWT_ALGORITHM, JWT_SECRET_KEY, MAGIC_LINK_SECRET_KEY
from src.api.auth.tokens import (
    INVITE_TOKEN_EXPIRE_DAYS,
    TokenError,
    create_access_token,
    create_invite_token,
    verify_invite_token,
    verify_magic_token,
    verify_token,
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

    def test_invite_refused_as_access_and_refresh_by_type_alone(self):
        """The TYPE claim refuses cross-use on its own, not just the secret
        split: an invite-typed payload signed with the BEARER secret still
        fails the access and refresh verifiers."""
        now = datetime.now(UTC)
        invite_on_bearer_secret = jwt.encode(
            {
                "family_id": "fam-1",
                "sub": "user-1",
                "type": "invite",
                "iat": now,
                "exp": now + timedelta(days=7),
            },
            JWT_SECRET_KEY,
            algorithm=JWT_ALGORITHM,
        )
        with pytest.raises(TokenError):
            verify_token(invite_on_bearer_secret, "access")
        with pytest.raises(TokenError):
            verify_token(invite_on_bearer_secret, "refresh")

    def test_invite_refused_as_magic_link(self):
        """A real invite token shares the magic link's SECRET, so the secret
        split cannot refuse it there — and it must still be refused."""
        with pytest.raises(TokenError):
            verify_magic_token(create_invite_token("fam-1", "user-1"))

    def test_invite_refused_as_magic_link_by_type_alone(self):
        """The TYPE claim carries the refusal on its own: an invite-typed
        token on the magic secret WITH an email claim (so the missing-email
        fallback cannot save the day) still fails the magic verifier."""
        now = datetime.now(UTC)
        invite_with_email = jwt.encode(
            {
                "family_id": "fam-1",
                "sub": "user-1",
                "email": "a@b.test",
                "type": "invite",
                "iat": now,
                "exp": now + timedelta(days=7),
            },
            MAGIC_LINK_SECRET_KEY,
            algorithm=JWT_ALGORITHM,
        )
        with pytest.raises(TokenError):
            verify_magic_token(invite_with_email)

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

    def test_stranger_invite_is_404_or_unauthenticated(self, client, family_id):
        """The no-confirmation rule: a family id the caller has no profile in is
        indistinguishable from one that does not exist."""
        with_stranger = client.post(
            "/api/v1/families/invite",
            json={"family_id": family_id},
            headers=_bearer("p10-families-stranger", "stranger@example.test"),
        )
        # The stranger user does not even exist on the graph -> 401 from auth;
        # an EXISTING authenticated non-member's 404 is pinned below.
        assert with_stranger.status_code == 401
        resp = client.post(
            "/api/v1/families/invite",
            json={"family_id": "no-such-family"},
            headers=_bearer(FIONA_USER_ID, FIONA_EMAIL),
        )
        assert resp.status_code == 404

    def test_an_existing_non_member_cannot_mint_an_invite(
        self, client, graph, family_id
    ):
        """Invite minting requires MEMBERSHIP, not just authentication: a real,
        authenticated user with a real profile who is simply not in the family
        gets the same 404 as if the family did not exist."""
        uid = "p10-families-test-user-outsider"
        email = "p10-families-outsider@example.test"
        pid = "p10-families-test-profile-outsider"
        with graph.session() as s:
            s.run("MERGE (u:User {id: $uid}) SET u.email = $email", uid=uid, email=email)
            s.run(
                "MERGE (p:Profile {id: $pid}) "
                "SET p.display_name = 'Outsider', p.created_at = datetime() "
                "WITH p MATCH (u:User {id: $uid}) MERGE (u)-[:HAS_PROFILE]->(p)",
                pid=pid,
                uid=uid,
            )
        try:
            resp = client.post(
                "/api/v1/families/invite",
                json={"family_id": family_id},
                headers=_bearer(uid, email),
            )
            assert resp.status_code == 404, resp.text
        finally:
            with graph.session() as s:
                s.run("MATCH (p:Profile {id: $pid}) DETACH DELETE p", pid=pid)
                s.run("MATCH (u:User {id: $uid}) DETACH DELETE u", uid=uid)


# ── M2.2: a crewed trip is readable by its crew; writes keep one captain ─────
#
# The matrix the plan names: crew-read 200, crew-replan (and crew-compose) a
# typed 403 "captain_only", stranger 404 (never 403 — the no-confirmation rule
# at src/api/routes/trips.py holds for crew exactly as it does for owners).

STRANGER_USER_ID = "p10-families-test-user-stranger"
STRANGER_EMAIL = "p10-families-stranger@example.test"
STRANGER_PROFILE_ID = "p10-families-test-profile-stranger"

CREWED_TRIP_ID = "p10-families-crewed-trip"


@pytest.fixture(scope="module")
def crewed_trip(graph):
    """A composed-looking trip captained by Fiona's profile with Dev's profile
    as crew, plus a stranger identity — planted directly, so the guard matrix
    needs no corpus and no engine run."""
    from src.api.models.trips import SessionPlan

    session_json = SessionPlan(
        trip_id=CREWED_TRIP_ID, plan_version=1, stops=[], retime_tolerance_seconds=120
    ).model_dump_json()
    with graph.session() as s:
        s.run(
            "MERGE (u:User {id: $uid}) SET u.email = $email",
            uid=STRANGER_USER_ID,
            email=STRANGER_EMAIL,
        )
        s.run(
            "MERGE (p:Profile {id: $pid}) "
            "SET p.display_name = 'Stranger', p.created_at = datetime() "
            "WITH p MATCH (u:User {id: $uid}) MERGE (u)-[:HAS_PROFILE]->(p)",
            pid=STRANGER_PROFILE_ID,
            uid=STRANGER_USER_ID,
        )
        s.run(
            "MERGE (t:Trip {id: $tid}) "
            "SET t.name = 'Crewed day', t.status = 'planning', "
            "    t.created_at = datetime(), t.plan_version = 1, "
            "    t.session_json = $sj "
            "WITH t MATCH (captain:Profile {id: $cap}) "
            "MERGE (captain)-[:IS_CAPTAIN_OF]->(t) "
            "WITH t MATCH (crew:Profile {id: $crew}) "
            "MERGE (crew)-[:IS_CREW_OF]->(t)",
            tid=CREWED_TRIP_ID,
            sj=session_json,
            cap=FIONA_PROFILE_ID,
            crew=DEV_PROFILE_ID,
        )
    return CREWED_TRIP_ID


@needs_neo4j
class TestCrewReadsCaptainWrites:
    def test_crew_reads_the_session_200(self, client, crewed_trip):
        resp = client.get(
            f"/api/v1/trips/{crewed_trip}/session", headers=_bearer(DEV_USER_ID, DEV_EMAIL)
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["trip_id"] == crewed_trip

    def test_captain_still_reads_the_session_200(self, client, crewed_trip):
        resp = client.get(
            f"/api/v1/trips/{crewed_trip}/session",
            headers=_bearer(FIONA_USER_ID, FIONA_EMAIL),
        )
        assert resp.status_code == 200, resp.text

    def test_crew_replan_is_a_typed_403(self, client, crewed_trip):
        resp = client.post(
            f"/api/v1/trips/{crewed_trip}/session/replan",
            json={
                "lat": 48.86,
                "lng": 2.34,
                "wall_elapsed_seconds": 0,
                "tour_elapsed_seconds": 0,
            },
            headers=_bearer(DEV_USER_ID, DEV_EMAIL),
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"]["reason"] == "captain_only"

    def test_crew_compose_is_a_typed_403(self, client, crewed_trip):
        resp = client.post(
            f"/api/v1/trips/{crewed_trip}/compose",
            json={"route_id": f"{crewed_trip}-opt1"},
            headers=_bearer(DEV_USER_ID, DEV_EMAIL),
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"]["reason"] == "captain_only"

    def test_stranger_is_404_never_403(self, client, crewed_trip):
        for method, url, body in (
            ("get", f"/api/v1/trips/{crewed_trip}/session", None),
            (
                "post",
                f"/api/v1/trips/{crewed_trip}/session/replan",
                {
                    "lat": 48.86,
                    "lng": 2.34,
                    "wall_elapsed_seconds": 0,
                    "tour_elapsed_seconds": 0,
                },
            ),
            ("post", f"/api/v1/trips/{crewed_trip}/compose", {"route_id": "x-opt1"}),
        ):
            kwargs = {"headers": _bearer(STRANGER_USER_ID, STRANGER_EMAIL)}
            if body is not None:
                kwargs["json"] = body
            resp = getattr(client, method)(url, **kwargs)
            assert resp.status_code == 404, f"{method} {url}: {resp.status_code} {resp.text}"

    def test_trip_list_includes_crewed_trips(self, client, crewed_trip):
        resp = client.get(
            f"/api/v1/trips?profile_id={DEV_PROFILE_ID}",
            headers=_bearer(DEV_USER_ID, DEV_EMAIL),
        )
        assert resp.status_code == 200, resp.text
        assert crewed_trip in [t["trip_id"] for t in resp.json()]


@needs_neo4j
class TestCrewDerivedAtCreation:
    @pytest.fixture()
    def shared_family(self, graph):
        """Fiona and Dev sharing a family, planted HERE — the class carries its
        own world, so it runs green alone as well as inside the file."""
        family_id = "p10-families-derived-crew-family"
        with graph.session() as s:
            s.run(
                "MERGE (f:Family {id: $fid}) SET f.created_at = datetime() "
                "WITH f MATCH (p:Profile) WHERE p.id IN $pids "
                "MERGE (p)-[:MEMBER_OF]->(f)",
                fid=family_id,
                pids=[FIONA_PROFILE_ID, DEV_PROFILE_ID],
            )
        yield family_id
        with graph.session() as s:
            s.run("MATCH (f:Family {id: $fid}) DETACH DELETE f", fid=family_id)

    def test_trip_creation_crews_the_captains_family_co_members(
        self, graph, shared_family
    ):
        """crud.create_trip_with_stops derives IS_CREW_OF from the captain's
        family membership: Fiona and Dev share a family (the fixture's), so a
        trip Fiona captains crews Dev — and never Fiona."""
        from src.api.crud.trips import create_trip_with_stops
        from src.connection import get_database

        with graph.session(database=get_database()) as s:
            result = create_trip_with_stops(
                s,
                trip_name="Derived-crew day",
                profile_id=FIONA_PROFILE_ID,
                start_date="2026-09-08",
                end_date="2026-09-08",
                stops=[],
            )
            crew = [
                r["pid"]
                for r in s.run(
                    "MATCH (p:Profile)-[:IS_CREW_OF]->(t:Trip {id: $tid}) "
                    "RETURN p.id AS pid",
                    tid=result["trip_id"],
                )
            ]
            s.run(
                "MATCH (t:Trip {id: $tid}) DETACH DELETE t", tid=result["trip_id"]
            )
        assert crew == [DEV_PROFILE_ID]


# ── The late joiner: joining the family crews you onto its existing days ─────
#
# The pinned semantics for the plan-first order: a day planned BEFORE the
# invite is still the family's day. add_member re-derives the whole family's
# crew in the same transaction as the membership write — the joiner reads the
# family's existing trips, the family reads the joiner's, and the captain of a
# trip is never their own crew. Writes keep one captain either way.

PLANFIRST_FAMILY_ID = "p10-families-planfirst-family"
PLANFIRST_TRIP_ID = "p10-families-planfirst-trip"
JOINERS_OWN_TRIP_ID = "p10-families-joiners-own-trip"


@needs_neo4j
class TestJoinLateCrewsTheExistingDays:
    @pytest.fixture()
    def plan_first_world(self, graph):
        """Fiona alone in a family with an already-composed day, and Dev —
        not yet a member — with a composed day of his own. Planted directly
        (the crewed_trip mould), torn down whole."""
        from src.api.models.trips import SessionPlan

        with graph.session() as s:
            s.run(
                "MERGE (f:Family {id: $fid}) SET f.created_at = datetime() "
                "WITH f MATCH (p:Profile {id: $pid}) MERGE (p)-[:MEMBER_OF]->(f)",
                fid=PLANFIRST_FAMILY_ID,
                pid=FIONA_PROFILE_ID,
            )
            for trip_id, captain_id in (
                (PLANFIRST_TRIP_ID, FIONA_PROFILE_ID),
                (JOINERS_OWN_TRIP_ID, DEV_PROFILE_ID),
            ):
                session_json = SessionPlan(
                    trip_id=trip_id,
                    plan_version=1,
                    stops=[],
                    retime_tolerance_seconds=120,
                ).model_dump_json()
                s.run(
                    "MERGE (t:Trip {id: $tid}) "
                    "SET t.name = 'Plan-first day', t.status = 'planning', "
                    "    t.created_at = datetime(), t.plan_version = 1, "
                    "    t.session_json = $sj "
                    "WITH t MATCH (captain:Profile {id: $cap}) "
                    "MERGE (captain)-[:IS_CAPTAIN_OF]->(t)",
                    tid=trip_id,
                    sj=session_json,
                    cap=captain_id,
                )
        yield
        with graph.session() as s:
            s.run(
                "MATCH (t:Trip) WHERE t.id IN $tids DETACH DELETE t",
                tids=[PLANFIRST_TRIP_ID, JOINERS_OWN_TRIP_ID],
            )
            s.run(
                "MATCH (f:Family {id: $fid}) DETACH DELETE f",
                fid=PLANFIRST_FAMILY_ID,
            )

    def _join_as_dev(self, client):
        token = create_invite_token(PLANFIRST_FAMILY_ID, FIONA_USER_ID)
        resp = client.post(
            "/api/v1/families/join",
            json={"token": token},
            headers=_bearer(DEV_USER_ID, DEV_EMAIL),
        )
        assert resp.status_code == 200, resp.text
        return resp

    def test_plan_first_then_join_reads_200_and_writes_403(
        self, client, plan_first_world
    ):
        """The whole plan-first walk: before joining, the family's day does not
        exist for Dev (404 — the no-confirmation rule); after joining, he reads
        it (200) and stays crew on writes (the typed 403)."""
        before = client.get(
            f"/api/v1/trips/{PLANFIRST_TRIP_ID}/session",
            headers=_bearer(DEV_USER_ID, DEV_EMAIL),
        )
        assert before.status_code == 404, before.text

        self._join_as_dev(client)

        after = client.get(
            f"/api/v1/trips/{PLANFIRST_TRIP_ID}/session",
            headers=_bearer(DEV_USER_ID, DEV_EMAIL),
        )
        assert after.status_code == 200, after.text
        assert after.json()["trip_id"] == PLANFIRST_TRIP_ID

        replan = client.post(
            f"/api/v1/trips/{PLANFIRST_TRIP_ID}/session/replan",
            json={
                "lat": 48.86,
                "lng": 2.34,
                "wall_elapsed_seconds": 0,
                "tour_elapsed_seconds": 0,
            },
            headers=_bearer(DEV_USER_ID, DEV_EMAIL),
        )
        assert replan.status_code == 403, replan.text
        assert replan.json()["detail"]["reason"] == "captain_only"

        compose = client.post(
            f"/api/v1/trips/{PLANFIRST_TRIP_ID}/compose",
            json={"route_id": f"{PLANFIRST_TRIP_ID}-opt1"},
            headers=_bearer(DEV_USER_ID, DEV_EMAIL),
        )
        assert compose.status_code == 403, compose.text
        assert compose.json()["detail"]["reason"] == "captain_only"

    def test_the_join_shares_the_joiners_own_days_too(self, client, plan_first_world):
        """The re-derivation runs for the WHOLE family: the day Dev composed
        before joining becomes readable by Fiona — and she is crew on it."""
        self._join_as_dev(client)

        got = client.get(
            f"/api/v1/trips/{JOINERS_OWN_TRIP_ID}/session",
            headers=_bearer(FIONA_USER_ID, FIONA_EMAIL),
        )
        assert got.status_code == 200, got.text

        replan = client.post(
            f"/api/v1/trips/{JOINERS_OWN_TRIP_ID}/session/replan",
            json={
                "lat": 48.86,
                "lng": 2.34,
                "wall_elapsed_seconds": 0,
                "tour_elapsed_seconds": 0,
            },
            headers=_bearer(FIONA_USER_ID, FIONA_EMAIL),
        )
        assert replan.status_code == 403, replan.text
        assert replan.json()["detail"]["reason"] == "captain_only"

    def test_the_captain_is_never_their_own_crew(self, client, graph, plan_first_world):
        self._join_as_dev(client)

        with graph.session() as s:
            self_crewed = s.run(
                "MATCH (p:Profile)-[:IS_CREW_OF]->(t:Trip)<-[:IS_CAPTAIN_OF]-(p) "
                "WHERE t.id IN $tids RETURN count(*) AS n",
                tids=[PLANFIRST_TRIP_ID, JOINERS_OWN_TRIP_ID],
            ).single()["n"]
        assert self_crewed == 0


# ── Foreign profile ids are never confirmed ──────────────────────────────────


@needs_neo4j
class TestForeignProfileIsNeverConfirmed:
    """POST /families and /families/join take an optional profile_id — which of
    the CALLER's profiles acts. A profile id the caller does not own is 404,
    indistinguishable from one that does not exist, and nothing is written."""

    @pytest.fixture()
    def fionas_family(self, graph):
        family_id = "p10-families-foreign-guard-family"
        with graph.session() as s:
            s.run(
                "MERGE (f:Family {id: $fid}) SET f.created_at = datetime() "
                "WITH f MATCH (p:Profile {id: $pid}) MERGE (p)-[:MEMBER_OF]->(f)",
                fid=family_id,
                pid=FIONA_PROFILE_ID,
            )
        yield family_id
        with graph.session() as s:
            s.run("MATCH (f:Family {id: $fid}) DETACH DELETE f", fid=family_id)

    def test_create_family_with_a_foreign_profile_is_404(self, client):
        resp = client.post(
            "/api/v1/families",
            json={"name": "Not yours", "profile_id": DEV_PROFILE_ID},
            headers=_bearer(FIONA_USER_ID, FIONA_EMAIL),
        )
        assert resp.status_code == 404, resp.text

    def test_join_with_a_foreign_profile_is_404_and_writes_nothing(
        self, client, graph, fionas_family
    ):
        token = create_invite_token(fionas_family, FIONA_USER_ID)
        resp = client.post(
            "/api/v1/families/join",
            json={"token": token, "profile_id": FIONA_PROFILE_ID},
            headers=_bearer(DEV_USER_ID, DEV_EMAIL),
        )
        assert resp.status_code == 404, resp.text
        with graph.session() as s:
            joined = s.run(
                "MATCH (p:Profile {id: $pid})-[:MEMBER_OF]->(f:Family {id: $fid}) "
                "RETURN count(*) AS n",
                pid=DEV_PROFILE_ID,
                fid=fionas_family,
            ).single()["n"]
        assert joined == 0


# ── M2.2: the profile stops collapsing ───────────────────────────────────────


@needs_neo4j
class TestProfiles:
    def test_get_profile_keeps_one_profile_shape_deterministically(self, client, graph):
        """GET /profile keeps the phone's one-profile contract: the caller's OWN
        latest, resolved deterministically (created_at DESC, id tie-break)."""
        with graph.session() as s:
            s.run(
                "MERGE (p:Profile {id: 'p10-families-fiona-second'}) "
                "SET p.display_name = 'Fiona II', "
                "    p.created_at = datetime() + duration('PT1H') "
                "WITH p MATCH (u:User {id: $uid}) MERGE (u)-[:HAS_PROFILE]->(p)",
                uid=FIONA_USER_ID,
            )
        resp = client.get("/api/v1/profile", headers=_bearer(FIONA_USER_ID, FIONA_EMAIL))
        assert resp.status_code == 200, resp.text
        assert resp.json()["profile_id"] == "p10-families-fiona-second"

    def test_get_profile_tie_breaks_equal_created_at_on_id(self, client, graph):
        """Two profiles created in the SAME instant: created_at DESC ties, so
        the id tie-break (ascending) decides — deterministically, every call."""
        uid = "p10-families-tiebreak-user"
        email = "p10-families-tiebreak@example.test"
        pids = ["p10-families-tiebreak-b", "p10-families-tiebreak-a"]
        with graph.session() as s:
            s.run("MERGE (u:User {id: $uid}) SET u.email = $email", uid=uid, email=email)
            for pid in pids:
                s.run(
                    "MERGE (p:Profile {id: $pid}) "
                    "SET p.display_name = $pid, "
                    "    p.created_at = datetime('2026-09-01T12:00:00Z') "
                    "WITH p MATCH (u:User {id: $uid}) MERGE (u)-[:HAS_PROFILE]->(p)",
                    pid=pid,
                    uid=uid,
                )
        try:
            resp = client.get("/api/v1/profile", headers=_bearer(uid, email))
            assert resp.status_code == 200, resp.text
            assert resp.json()["profile_id"] == "p10-families-tiebreak-a"
        finally:
            with graph.session() as s:
                s.run("MATCH (p:Profile) WHERE p.id IN $pids DETACH DELETE p", pids=pids)
                s.run("MATCH (u:User {id: $uid}) DETACH DELETE u", uid=uid)

    def test_get_profiles_lists_every_profile(self, client):
        resp = client.get("/api/v1/profiles", headers=_bearer(FIONA_USER_ID, FIONA_EMAIL))
        assert resp.status_code == 200, resp.text
        profiles = resp.json()["profiles"]
        assert {p["profile_id"] for p in profiles} == {
            FIONA_PROFILE_ID,
            "p10-families-fiona-second",
        }
        assert all(p["display_name"] for p in profiles)
