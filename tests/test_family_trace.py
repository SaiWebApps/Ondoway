"""The family day is one day — the shared-session trace (Phase 10 S2, M2.4).

A scripted server-side trace at $0, in the persona traces' own discipline
(live dev corpus, TestClient, OfflinePremiumExecutor): Fiona invites, Dev
joins, Fiona composes a real day — and BOTH read the same session, stop for
stop. Writes keep one captain: Dev's replan is the typed 403. When the trip is
gone, Dev's read is a plain 404.

Its own file, deliberately: `tests/test_persona_traces.py`'s DAYS table is the
panel-judged set and warns against a twelfth spelling — so this trace IMPORTS
the runner (`build_trace`, Camille's own request) rather than respelling a day.
Camille is used because her day is one of the measured served floor.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.auth.tokens import create_access_token
from src.api.dependencies import (
    get_driver,
    get_faithfulness_checker,
    get_premium_compose_executor,
    get_session,
)
from src.tour.premium_tour import OfflinePremiumExecutor
from src.tour.verify import MockFaithfulnessChecker
from tests.conftest import needs_neo4j
from tests.live_graph import open_dev_driver
from tests.persona_traces import Trace, build_trace

#: Disposable identities on the lane's dev graph (on lane 4: dev4, the seeded
#: Paris corpus). Cleared before and after, so a crashed run leaves no residue.
CAPTAIN_USER_ID = "p10-family-trace-user-captain"
CAPTAIN_EMAIL = "p10-family-trace-captain@example.test"
CAPTAIN_PROFILE_ID = "p10-family-trace-profile-captain"
CREW_USER_ID = "p10-family-trace-user-crew"
CREW_EMAIL = "p10-family-trace-crew@example.test"
CREW_PROFILE_ID = "p10-family-trace-profile-crew"
_PROFILE_IDS = [CAPTAIN_PROFILE_ID, CREW_PROFILE_ID]
_USER_IDS = [CAPTAIN_USER_ID, CREW_USER_ID]

#: One composed five-hour day plus its contingency set is minutes, not seconds;
#: the persona-trace file carries the same ceiling for the same reason.
pytestmark = pytest.mark.timeout(900)


def _delete_trace_artifacts(driver) -> None:
    with driver.session() as s:
        s.run(
            "MATCH (p:Profile) WHERE p.id IN $pids "
            "OPTIONAL MATCH (p)-[:IS_CAPTAIN_OF|IS_CREW_OF]->(t:Trip) "
            "OPTIONAL MATCH (t)-[:HAS_STOP]->(i:ItineraryItem) "
            "DETACH DELETE t, i",
            pids=_PROFILE_IDS,
        )
        # A family these disposable profiles founded holds no one else.
        s.run(
            "MATCH (p:Profile)-[:MEMBER_OF]->(f:Family) WHERE p.id IN $pids "
            "DETACH DELETE f",
            pids=_PROFILE_IDS,
        )
        s.run("MATCH (p:Profile) WHERE p.id IN $pids DETACH DELETE p", pids=_PROFILE_IDS)
        s.run("MATCH (u:User) WHERE u.id IN $uids DETACH DELETE u", uids=_USER_IDS)


@pytest.fixture(scope="module")
def live_neo4j():
    driver = open_dev_driver()
    if driver is None:
        pytest.skip(
            "The lane's dev graph is unreachable. The family trace walks the "
            "real corpus; run it through "
            "`make test-file LANE=4 FILE=tests/test_family_trace.py`."
        )
    yield driver
    driver.close()


@pytest.fixture(scope="module")
def family_identities(live_neo4j):
    _delete_trace_artifacts(live_neo4j)
    with live_neo4j.session() as s:
        for uid, email, pid, name in (
            (CAPTAIN_USER_ID, CAPTAIN_EMAIL, CAPTAIN_PROFILE_ID, "Fiona"),
            (CREW_USER_ID, CREW_EMAIL, CREW_PROFILE_ID, "Dev"),
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
    yield
    _delete_trace_artifacts(live_neo4j)


def _make_client(app, user_id: str, email: str) -> TestClient:
    """One identity's client — the persona runner's mould, minted per request."""

    def _mint_fresh_bearer(request):
        request.headers["Authorization"] = f"Bearer {create_access_token(user_id, email)}"

    client = TestClient(app)
    client.event_hooks["request"] = [_mint_fresh_bearer]
    return client


@pytest.fixture(scope="module")
def clients(live_neo4j, family_identities):
    app = create_app()

    def _live_session():
        with live_neo4j.session() as s:
            yield s

    app.dependency_overrides[get_session] = _live_session
    app.dependency_overrides[get_driver] = lambda: live_neo4j
    # The $0 seam, declared at the call site (the persona traces' own reason:
    # module fixtures outrun the function-scoped money guard).
    app.dependency_overrides[get_premium_compose_executor] = lambda: OfflinePremiumExecutor()
    app.dependency_overrides[get_faithfulness_checker] = lambda: MockFaithfulnessChecker()

    with (
        _make_client(app, CAPTAIN_USER_ID, CAPTAIN_EMAIL) as captain,
        _make_client(app, CREW_USER_ID, CREW_EMAIL) as crew,
    ):
        yield captain, crew


@pytest.fixture(scope="module")
def family_day(clients) -> Trace:
    """THE flow, walked once, asserted many: invite → join → the captain
    composes Camille's day (the runner's own request — never a twelfth
    spelling) as the family's shared trip."""
    captain, crew = clients

    created = captain.post("/api/v1/families", json={"name": "Trace family"})
    assert created.status_code == 201, created.text
    family_id = created.json()["family_id"]

    invited = captain.post("/api/v1/families/invite", json={"family_id": family_id})
    assert invited.status_code == 200, invited.text
    token = invited.json()["invite_url"].split("token=", 1)[1]

    joined = crew.post("/api/v1/families/join", json={"token": token})
    assert joined.status_code == 200, joined.text
    assert joined.json()["family_id"] == family_id

    trace = build_trace(captain, "camille", profile_id=CAPTAIN_PROFILE_ID)
    assert trace.served, (
        f"the family's day must serve to be shared — refused at "
        f"{trace.refused_at}: {trace.refusal}"
    )
    return trace


@needs_neo4j
def test_both_members_get_the_same_session(clients, family_day):
    """The family reads ONE day: the crew's GET returns the very stops the
    captain composed — equal stop ids, equal order, same plan version."""
    _captain, crew = clients
    got = crew.get(f"/api/v1/trips/{family_day.trip_id}/session")
    assert got.status_code == 200, got.text
    crew_day = got.json()

    captain_stop_ids = [st["stop_id"] for st in family_day.session["stops"]]
    crew_stop_ids = [st["stop_id"] for st in crew_day["stops"]]
    assert captain_stop_ids, "a shared day with no stops shares nothing"
    assert crew_stop_ids == captain_stop_ids
    assert crew_day["plan_version"] == family_day.session["plan_version"]


@needs_neo4j
def test_crew_replan_is_refused_typed(clients, family_day):
    """Writes keep one captain: the crew's replan is the typed 403, named so
    the phone can explain it — never a silent 404 and never a served replan."""
    _captain, crew = clients
    resp = crew.post(
        f"/api/v1/trips/{family_day.trip_id}/session/replan",
        json={
            "lat": family_day.session["stops"][0]["lat"],
            "lng": family_day.session["stops"][0]["lng"],
            "wall_elapsed_seconds": 0,
            "tour_elapsed_seconds": 0,
            "next_stop_index": 0,
        },
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["reason"] == "captain_only"


@needs_neo4j
def test_a_deleted_trip_is_gone_for_the_crew_too(clients, family_day, live_neo4j):
    """The TRANSITION is the claim: the crew reads the shared day (200 — the
    family read-widening, the half a non-family build fails), and after the
    trip is deleted the same read is a plain 404 — the shared day does not
    outlive the day. (Deletion is a graph operation: no product delete
    endpoint exists; this trace removes the trip the way the persona traces
    clear their own artifacts.) Runs LAST — it destroys the day the other
    assertions read."""
    _captain, crew = clients
    before = crew.get(f"/api/v1/trips/{family_day.trip_id}/session")
    assert before.status_code == 200, before.text

    with live_neo4j.session() as s:
        s.run(
            "MATCH (t:Trip {id: $tid}) "
            "OPTIONAL MATCH (t)-[:HAS_STOP]->(i:ItineraryItem) "
            "DETACH DELETE t, i",
            tid=family_day.trip_id,
        )
    got = crew.get(f"/api/v1/trips/{family_day.trip_id}/session")
    assert got.status_code == 404, got.text
