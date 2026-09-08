"""Integration tests for POST /api/v1/trips/generate — real engine, live corpus.

Since M0b the endpoint runs the real tour engine (src/tour), whose density
gate refuses sparse areas (needs >=4 anchor candidates). The conftest test
instance (port 7688) only carries toy seed data (3 POIs / 5 beats), so these
tests run against the LIVE local Paris dev graph (port 7687) the way
tests/test_tour_golden_*.py do—through a separate, localhost-only dev-graph
profile and overridden app session/driver dependencies.

Unlike the goldens these tests WRITE to the dev graph: a disposable test
Profile and the Trips/ItineraryItems the endpoint persists. Setup and
teardown both delete them, so a crashed run never accumulates residue.

The tour input mirrors fixtures/tour_golden/ile_oneway_90min.json (Pont Neuf
metro, 90 min, one-way) — a known-GREEN multi-stop start on the live corpus.
"""

from __future__ import annotations

import inspect
import json
import threading

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.auth.tokens import create_access_token
from src.api.dependencies import get_driver, get_session
from src.api.models.trips import GeneratedStop
from src.api.routes import trips
from src.api.routes.trips import _releg_kept_stops
from src.tour.authoring import COMPOSE_MODEL
from src.tour.certification_provider import PhysicalProviderResponse
from src.tour.contract import TourInput
from src.tour.degradations import record
from src.tour.density import TourabilityRefusedError
from src.tour.options import build_route_option
from src.tour.premium_tour import plan_premium_tour, resolve_build_identity
from src.tour.routing import haversine_m, pace_corrected_walk_seconds
from src.tour.routing_client import RoutingClient
from src.tour.selection import load_paris_corpus, select_route
from tests.conftest import needs_neo4j
from tests.live_graph import open_dev_driver

LENSED_PROFILE_ID = "m0b-trip-api-test-profile-lensed"
NOLENS_PROFILE_ID = "m0b-trip-api-test-profile-nolens"
PROFILE_IDS = [LENSED_PROFILE_ID, NOLENS_PROFILE_ID]
# The trip routes are ownership-scoped (2026-07-19 IDOR fix): they require a bearer
# token AND the profile must hang off the calling User. Own both disposable profiles
# with one disposable User so these tests exercise the authorized path.
TEST_USER_ID = "m0b-trip-api-test-user"
TEST_USER_EMAIL = "m0b-trip-api@example.test"
PROFILE_LENSES = ["hidden_history", "literary_heritage"]

# Same input as fixtures/tour_golden/ile_oneway_90min.json.
ILE_START = (48.8568, 2.3414)
ILE_DURATION_MIN = 90


def _live_driver():
    """Open only the localhost:7687 graph; this module writes disposable data."""
    return open_dev_driver()


def _delete_test_artifacts(driver) -> None:
    """Remove the disposable profiles and any trips/items they captain."""
    with driver.session() as s:
        s.run(
            "MATCH (p:Profile) WHERE p.id IN $pids "
            "OPTIONAL MATCH (p)-[:IS_CAPTAIN_OF]->(t:Trip) "
            "OPTIONAL MATCH (t)-[:HAS_STOP]->(i:ItineraryItem) "
            "DETACH DELETE t, i",
            pids=PROFILE_IDS,
        )
        s.run("MATCH (p:Profile) WHERE p.id IN $pids DETACH DELETE p", pids=PROFILE_IDS)
        s.run("MATCH (u:User {id: $uid}) DETACH DELETE u", uid=TEST_USER_ID)


@pytest.fixture(scope="module")
def live_neo4j():
    d = _live_driver()
    if d is None:
        pytest.skip(
            "Local Paris dev Neo4j unreachable. These tests write disposable "
            "records and refuse anything but localhost:7687. Run through "
            "`make test` or `make test-file FILE=tests/test_trip_api.py`."
        )
    yield d
    d.close()


@pytest.fixture(scope="module")
def test_profiles(live_neo4j):
    """Disposable profiles on the live graph: one lensed, one without lenses."""
    _delete_test_artifacts(live_neo4j)  # clear residue from any crashed prior run
    with live_neo4j.session() as s:
        s.run(
            "MERGE (u:User {id: $uid}) SET u.email = $email",
            uid=TEST_USER_ID,
            email=TEST_USER_EMAIL,
        )
        for pid in PROFILE_IDS:
            s.run(
                "MERGE (p:Profile {id: $pid}) SET p.display_name = 'M0b API Test' "
                "WITH p MATCH (u:User {id: $uid}) MERGE (u)-[:HAS_PROFILE]->(p)",
                pid=pid,
                uid=TEST_USER_ID,
            )
        linked = s.run(
            "MATCH (p:Profile {id: $pid}) "
            "UNWIND $lenses AS lname "
            "MATCH (l:Lens {name: lname}) "
            "MERGE (p)-[:PREFERS_LENS]->(l) "
            "RETURN count(l) AS linked",
            pid=LENSED_PROFILE_ID,
            lenses=PROFILE_LENSES,
        ).single()["linked"]
        assert linked == len(PROFILE_LENSES), (
            f"Live graph is missing Lens nodes for {PROFILE_LENSES} — "
            f"linked only {linked}. Re-sync the dev corpus (make db-up + upload)."
        )
    yield
    _delete_test_artifacts(live_neo4j)


@pytest.fixture(scope="module")
def client(live_neo4j, test_profiles):
    """TestClient whose request sessions AND engine corpus driver hit the live graph."""
    app = create_app()

    def _live_session():
        with live_neo4j.session() as s:
            yield s

    app.dependency_overrides[get_session] = _live_session
    app.dependency_overrides[get_driver] = lambda: live_neo4j

    def _mint_fresh_bearer(request):
        """Sign EVERY request with a token minted just now.

        Ownership-scoped routes: authenticate as the User owning both profiles.

        Minted per REQUEST, not once per module, and that is load-bearing rather than
        fastidious. `ACCESS_TOKEN_EXPIRE_MINUTES` is 60 (src/api/auth/config.py:121) and
        this fixture is module-scoped, so a single token used to cover the whole file.
        On 2026-08-05 this module's runtime passed that hour — the planner explores far
        more since the stop ceilings were removed and the fixtures were enriched to fill
        a real hour — and every test after the 60-minute mark got 401 "Invalid or expired
        token" while asserting a 422. That surfaced as 3 failures and 11 fixture errors
        in a full-suite run, and NONE of them was a product fault: a 60-minute access
        token is correct in production. A per-request token cannot expire mid-module no
        matter how slow the suite becomes.
        """
        request.headers["Authorization"] = (
            f"Bearer {create_access_token(TEST_USER_ID, TEST_USER_EMAIL)}"
        )

    with TestClient(app) as c:
        c.event_hooks["request"] = [_mint_fresh_bearer]
        yield c


@pytest.fixture(scope="module")
def snapshot(live_neo4j):
    return load_paris_corpus(live_neo4j, city_slug="paris")


@pytest.fixture(scope="module")
def ile_engine_route(snapshot):
    """The engine's own answer for the Île input — the reference the API must match."""
    tour_input = TourInput(
        start=ILE_START,
        duration_min=ILE_DURATION_MIN,
        city_slug="paris",
        lenses=None,
        round_trip=False,
    )
    # Same routing mode as the endpoint (RoutingClient; falls back to
    # haversine when local Valhalla is down) or stop-order comparisons
    # diverge whenever Valhalla is up.
    with RoutingClient() as rc:
        return select_route(tour_input, snapshot, routing_client=rc)


def _body(profile_id: str, **overrides) -> dict:
    body = {
        "profile_id": profile_id,
        "center_lat": ILE_START[0],
        "center_lng": ILE_START[1],
        "duration_min": ILE_DURATION_MIN,
        "round_trip": False,
        "start_date": "2026-06-12",
        "end_date": "2026-06-13",
        "start_time": "09:00",
    }
    body.update(overrides)
    return body


@pytest.fixture(scope="module")
def ile_response(client):
    """One engine-backed generation, shared by the response-shape assertions.

    Uses the no-lens profile so the engine input (lenses=None) matches
    ile_engine_route exactly.
    """
    resp = client.post("/api/v1/trips/generate", json=_body(NOLENS_PROFILE_ID))
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
    return resp.json()


@needs_neo4j
def test_generate_plans_through_the_shared_block_one(ile_response, snapshot):
    """AC-4/AC-7 (re-derived at Phase 4 S4.4, design §8.1): the phone's day IS
    Block 1's plan, not a second planner's.

    §8.1 deleted pick-one-of-three, so Block 1 is ``plan_premium_tour`` and the
    endpoint publishes exactly ONE option. The invariant this test has always
    carried is unchanged: the endpoint plans through the one shared planner, and
    what it publishes is byte-identical to what that planner produced for the
    identical input. The K-diversity clause died with the flavours (a written
    §0.1.2 decision — it pinned the deleted Jaccard machinery).

    * STRUCTURAL — the handler calls the shared planner and no longer contains a
      second route-selection or script-generation pass of its own.
    * BEHAVIOURAL — the published option is byte-identical to the shared
      planner's plan (AC-7).
    """
    planner_source = inspect.getsource(trips.generate_trip)
    assert "plan_premium_tour(" in planner_source, (
        "generate_trip must plan through the one shared planner"
    )
    for second_pass in (
        "select_k_routes(",
        "build_poi_beat_plans_capped(",
        "select_vignette_beats(",
        "generate(",
    ):
        assert second_pass not in planner_source, (
            f"generate_trip still runs its own {second_pass.rstrip('(')} pass — "
            "that is the duplicate algorithm this slice removes"
        )

    body = ile_response
    tour_input = TourInput(
        start=ILE_START,
        duration_min=ILE_DURATION_MIN,
        city_slug="paris",
        lenses=None,
        round_trip=False,
    )
    with RoutingClient() as rc:
        plan = plan_premium_tour(tour_input, snapshot, routing_client=rc)

    assert len(body["options"]) == 1, (
        f"the endpoint published {len(body['options'])} options; Phase 4 plans ONE day"
    )

    beats_by_id = {ref.id: ref for refs in snapshot.beats_by_poi.values() for ref in refs}
    option = body["options"][0]
    expected = build_route_option(
        plan.route,
        plan.source,
        beats_by_id,
        route_id=option["route_id"],
        snapshot=snapshot,
        sequence=plan.sequence,
    ).model_dump(mode="json")
    assert [s["poi_id"] for s in option["stops"] if s["band"] == "dwell"] == [
        p.id for p in plan.route.pois
    ], "the published day visits different places from the planned day"
    assert option["eta_seconds"] > 0
    assert option["eta_seconds"] == expected["eta_seconds"], (
        "the published day declares an arrival time the planned day did not"
    )
    assert [s["narration"] for s in option["stops"]] == [
        s["narration"] for s in expected["stops"]
    ], "the published day was written by a second pass over the same route"
    # Catch-all: nothing at all about the published option may differ from the
    # plan the shared planner produced.
    assert option == expected, "the published day diverged from the planned day"

    # The trip actually saved is THE day.
    assert [s["poi_id"] for s in body["stops"]] == [p.id for p in plan.route.pois]


@needs_neo4j
class TestTripGenerateEngine:
    """The endpoint must surface the real engine's selection, order, and beats."""

    def test_response_shape(self, ile_response):
        data = ile_response
        assert isinstance(data["trip_id"], str) and data["trip_id"]
        assert data["profile_id"] == NOLENS_PROFILE_ID
        assert data["total_stops"] == len(data["stops"]) > 0
        assert data["anchor_count"] + data["flavour_count"] == data["total_stops"]
        assert isinstance(data["lens_coverage"], dict)
        for stop in data["stops"]:
            assert stop["beat_ids"], "every engine stop narrates at least one beat"
            assert stop["beat_id"] == stop["beat_ids"][0]
            assert stop["dwell_seconds"] >= 0
        # Primary-beat passthrough: the corpus' active beats carry script
        # bodies, so a generated trip must surface them (mobile's
        # audio-already-exists fast path reads these fields).
        assert any(s["script_body"] for s in data["stops"])

    def test_options_surface_the_one_day(self, ile_response):
        """Phase 4 S4.4 (design §8.1): the response carries exactly ONE RouteOption
        — THE day — and its DWELL stops mirror the persisted trip.

        Re-derived from the old K-flavours pin (a written §0.1.2 decision): the
        1-to-3 count and the pairwise-Jaccard diversity clause pinned the deleted
        flavour machinery; the surviving invariants — the option mirrors the trip,
        carries a positive arrival time, and every card is one of the three known
        kinds — are unchanged.

        Track B (B.3) interleaves band="vignette" walk-past stops into
        RouteOption.stops — additive annotations, not itinerary stops — so the
        mirror assertion is dwell-scoped.
        """
        options = ile_response["options"]
        assert len(options) == 1, "Phase 4 plans ONE day (design §8.1)"
        option = options[0]

        dwell_ids = [s["poi_id"] for s in option["stops"] if s["band"] == "dwell"]
        assert dwell_ids == [s["poi_id"] for s in ile_response["stops"]]
        assert option["route_id"] == f"{ile_response['trip_id']}-opt1"
        assert option["eta_seconds"] > 0
        assert option["stops"], "a day without stops is not a day"
        for stop in option["stops"]:
            # Three kinds of card: a stop you stand at, a sight you pass, and the
            # narration you hear on the way to the next stop.
            assert stop["band"] in ("dwell", "vignette", "leg")

    def test_stop_order_matches_select_route(self, ile_response, ile_engine_route):
        expected = [p.id for p in ile_engine_route.pois]
        got = [s["poi_id"] for s in ile_response["stops"]]
        assert got == expected, (
            f"API stop order diverged from select_route. "
            f"Engine: {[p.name for p in ile_engine_route.pois]}; API poi_ids: {got}."
        )

    def test_beat_ids_traceable_to_route_pois(self, ile_response, ile_engine_route, snapshot):
        allowed = {
            poi.id: (
                {b.id for b in snapshot.beats_for(poi.id)}
                | {b.id for b in ile_engine_route.demoted_beats.get(poi.id, ())}
            )
            for poi in ile_engine_route.pois
        }
        for stop in ile_response["stops"]:
            orphans = set(stop["beat_ids"]) - allowed[stop["poi_id"]]
            assert not orphans, (
                f"Stop {stop['poi_name']} returned beat_ids not traceable to its "
                f"POI (or its demoted pool): {sorted(orphans)}"
            )
@needs_neo4j
class TestTripGenerateErrors:
    def test_profile_not_found_404(self, client):
        resp = client.post(
            "/api/v1/trips/generate", json=_body("nonexistent-profile-id-xyz")
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_sparse_origin_refused_422(self, client):
        """The density gate's RED refusal maps to a structured 422 body. Sydney
        is far from every Paris POI, so there is no fixed-destination overshoot:
        gap_minutes is None and alternatives is empty (those only appear for an
        A→B feasibility refusal, exercised in TestTripGenerateFixedDestination)."""
        body = _body(NOLENS_PROFILE_ID, center_lat=-33.8688, center_lng=151.2093)
        resp = client.post("/api/v1/trips/generate", json=body)
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert isinstance(detail, dict)
        assert isinstance(detail["reason"], str) and detail["reason"]
        assert detail["gap_minutes"] is None
        assert detail["alternatives"] == []


# Versailles palace — a real Paris-region coordinate ~14km SW of the Île start.
# The routed A→B leg (or its haversine fallback) is hours long, dwarfing any
# tour walk budget, so a fixed-end A→B request from the Île GREEN start lands in
# the Step-2.2 fixed-destination feasibility refusal — never density-RED (the
# Île start stays GREEN at 90 min). Far enough that no selected POI snaps within
# B_SNAP_PROXIMITY_M, so the engine synthesizes a sentinel at B's exact coord.
VERSAILLES_END = (48.8049, 2.1204)


@needs_neo4j
class TestTripGenerateFixedDestination:
    """Step 2.6: end_lat/end_lng thread into TourInput.end; the route ends at B,
    and an unreachable B yields a structured 422 mirroring the Step-2.2 error."""
    def test_ab_request_route_ends_at_b(self, client, snapshot, ile_engine_route):
        """An in-budget A→B request returns a route ending at B. B is the coord
        of a real POI the open Île route selected, so B-materialization snaps B
        onto a selected anchor: the API's final stop is the engine's fixed-end
        route's final POI (derived live, both via RoutingClient)."""
        # A selected anchor from the open route → guaranteed in-corridor and
        # reachable, so the A→B request is feasible (no refusal).
        b_poi = ile_engine_route.pois[-1]
        b_end = (b_poi.lat, b_poi.lng)
        with RoutingClient() as rc:
            engine_route = select_route(
                TourInput(
                    start=ILE_START,
                    duration_min=ILE_DURATION_MIN,
                    city_slug="paris",
                    lenses=None,
                    round_trip=False,
                    end=b_end,
                ),
                snapshot,
                routing_client=rc,
            )
        resp = client.post(
            "/api/v1/trips/generate",
            json=_body(NOLENS_PROFILE_ID, end_lat=b_end[0], end_lng=b_end[1]),
        )
        assert resp.status_code == 201, resp.text
        got = [s["poi_id"] for s in resp.json()["stops"]]
        expected = [p.id for p in engine_route.pois]
        assert got == expected, (
            f"API order diverged from fixed-end select_route: {got} vs {expected}"
        )
        # The route literally ends at B (the materialized fixed_end).
        assert got[-1] == engine_route.pois[-1].id

    def test_over_budget_ab_returns_structured_422(self, client, snapshot):
        """An A→B request whose routed A→B leg exceeds the walk budget returns a
        422 whose JSON body matches the live Step-2.2 TourabilityRefusedError:
        {reason, gap_minutes, alternatives:[{kind,...}]} with the kind enum
        (loop/extend, plus closer_b when an anchor fits the budget)."""
        # Reproduce the endpoint's own engine call to derive the truth live —
        # robust whether Valhalla is up (routed leg) or down (haversine).
        tour_input = TourInput(
            start=ILE_START,
            duration_min=ILE_DURATION_MIN,
            city_slug="paris",
            lenses=None,
            round_trip=False,
            end=VERSAILLES_END,
        )
        with (
            RoutingClient() as rc,
            pytest.raises(TourabilityRefusedError) as caught,
        ):
            select_route(tour_input, snapshot, routing_client=rc)
        exc = caught.value
        # Guard: this must be the FIXED-DESTINATION feasibility refusal (carries
        # gap_minutes + alternatives), not a plain density-RED refusal.
        assert exc.gap_minutes is not None and exc.gap_minutes > 0
        assert exc.alternatives, "fixed-destination refusal must offer alternatives"
        expected_kinds = [a.kind for a in exc.alternatives]
        assert {"loop", "extend"} <= set(expected_kinds)
        assert set(expected_kinds) <= {"loop", "extend", "closer_b"}

        resp = client.post(
            "/api/v1/trips/generate",
            json=_body(NOLENS_PROFILE_ID, end_lat=VERSAILLES_END[0], end_lng=VERSAILLES_END[1]),
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert isinstance(detail, dict)
        assert detail["reason"] == str(exc)
        assert detail["gap_minutes"] == exc.gap_minutes
        # The structured alternatives mirror the engine's FeasibilityAlternative
        # tuple field-for-field (kind enum + duration_min/drop_end/poi_id/lat/lng).
        assert [a["kind"] for a in detail["alternatives"]] == expected_kinds
        assert detail["alternatives"] == [
            {
                "kind": a.kind,
                "duration_min": a.duration_min,
                "drop_end": a.drop_end,
                "poi_id": a.poi_id,
                "lat": a.lat,
                "lng": a.lng,
            }
            for a in exc.alternatives
        ]


# ---------------------------------------------------------------------------
# The authoring-seam doubles. The frozen-trip tests that used them (the one-compose
# 409, the -optN selector, composed_route_id) were DELETED at Phase 5 D5.0 — design
# §8.2, audit C §1's eleven DELETE-AT-PHASE-5 rows. The doubles stay: the invariants
# that survive the frozen trip (a verification failure is a 422 that persists nothing;
# one authoring call per stop through the one seam, fact-gate consulted; the outage
# row travels; authored text is what is persisted) are re-derived against the living
# session's endpoints at S5.8, and these are the physical-boundary doubles they need.
# ---------------------------------------------------------------------------

COMPOSE_MARKER = "COMPOSED-MARKER: the story, retold for you."


class _MarkerAuthoringExecutor:
    """Deterministic author: opens the first stop with a recognizable phrase —
    route-independent proof that the AUTHORED output (not the stitch) was persisted.

    It edits sentence TEXT only and leaves every citation alone, so the marker is
    the single variable: anything that fails afterwards is a real gate finding, not
    the double inventing an untraceable sentence.
    """

    cost_bearing = False
    provider_name = "offline"

    def execute(self, unit) -> PhysicalProviderResponse:
        sentences = [s.model_dump(mode="json") for s in unit.authorized_request.stitched.script]
        if unit.stop_index == 0:
            sentences[0] = {
                **sentences[0],
                "text": f"{COMPOSE_MARKER} {sentences[0]['text']}",
            }
        return _offline_response(unit, sentences)


def _provider_bytes(sentences: list[dict]) -> bytes:
    """The exact JSON envelope the per-stop authoring seam parses back."""
    return json.dumps(
        {"sentences": sentences},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _offline_response(unit, sentences: list[dict]) -> PhysicalProviderResponse:
    return PhysicalProviderResponse(
        body=_provider_bytes(sentences),
        input_tokens=0,
        output_tokens=0,
        latency_ms=0,
        model=COMPOSE_MODEL,
        provider_request_id=f"offline-{unit.stop_index}",
        stop_reason="end_turn",
    )


class _PerStopCountingExecutor:
    """A COST-BEARING physical-boundary double for the per-stop authoring seam.

    It records which dwell stop each physical call was made for and echoes the
    grounded stitch straight back, so the assertions are on the number of calls the
    endpoint actually made — not on a status code that could be green with the old
    whole-tour composer still doing the work. ``cost_bearing`` is True on purpose:
    the spend guard only arms for billable providers, so a $0 stub could never
    prove the reservation.
    """

    cost_bearing = True
    provider_name = "anthropic"

    def __init__(self) -> None:
        self.stop_calls: list[int] = []
        self._lock = threading.Lock()

    def execute(self, unit) -> PhysicalProviderResponse:
        with self._lock:
            self.stop_calls.append(unit.stop_index)
        return _offline_response(
            unit,
            [s.model_dump(mode="json") for s in unit.authorized_request.stitched.script],
        )


class _HallucinatingExecutor:
    """Authors one sentence citing a beat that is not in the grounded source.

    That is the untraceable-citation half of VERIFY, so the per-stop finalizer must
    refuse the whole tour. $0 and non-billing, so it reserves nothing and the spend
    assertions in the same test stay about the paid path.
    """

    cost_bearing = False
    provider_name = "offline"

    def execute(self, unit) -> PhysicalProviderResponse:
        sentences = [s.model_dump(mode="json") for s in unit.authorized_request.stitched.script]
        sentences[0] = {
            **sentences[0],
            "source_id": "beat-that-was-never-in-the-corpus",
            "source_type": "beat",
            "also_cites": [],
        }
        return _offline_response(unit, sentences)


class _RewritingCountingExecutor(_PerStopCountingExecutor):
    """Counts physical calls like its parent, but REWRITES instead of echoing.

    The corpus is canonical: a sentence copied verbatim out of the beat it cites is
    trivially faithful and never reaches the entailment checker at all
    (src/tour/verify.py:181-187, 217). A pure echo therefore proves nothing about
    whether the fact-checking gate ran. Prefixing each stop's first beat-cited
    sentence — the same edit ``_MarkerAuthoringExecutor`` makes — is what gives the
    gate a question to ask, and it leaves every citation alone so any later refusal
    is a real finding rather than this double inventing an untraceable sentence.
    """

    def execute(self, unit) -> PhysicalProviderResponse:
        with self._lock:
            self.stop_calls.append(unit.stop_index)
        sentences = [s.model_dump(mode="json") for s in unit.authorized_request.stitched.script]
        for i, sentence in enumerate(sentences):
            if sentence.get("source_type") == "beat":
                sentences[i] = {**sentence, "text": f"{COMPOSE_MARKER} {sentence['text']}"}
                break
        return _offline_response(unit, sentences)


class _CountingChecker:
    """Records every entailment question asked, and approves all of them.

    It changes no verdict, so it cannot make a failing tour pass; it only proves the
    gate CONSULTED a checker. Mirrors src/tour/verify.py's FaithfulnessChecker
    protocol exactly — ``entails(key_claims, sentence_text)``.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], str]] = []

    def entails(self, key_claims: tuple[str, ...], sentence_text: str) -> bool:
        self.calls.append((key_claims, sentence_text))
        return True


class _ColdStartingRoutingClient:
    """``ondoway-valhalla`` while it cold-starts or rebuilds tiles (render.yaml:98-124).

    Every request to it fails, and it fails the two DIFFERENT ways the real client
    fails, which is the whole point of the stub:

    - walking legs fall back to a straight-line estimate with no polyline and no
      receipt, never raising — src/tour/routing_client.py:175-181;
    - the version read RAISES, because that one call deliberately has no fallback of
      its own — src/tour/routing_client.py:183-200.

    Standing in for the class rather than stopping the container keeps the shared
    :8002 instance every other test and every sibling session depends on completely
    untouched.
    """

    def __init__(self, *args, **kwargs) -> None:
        return None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> bool:
        return False

    def routing_version(self) -> str:
        raise RuntimeError("valhalla is cold-starting")

    def route_with_receipt(self, from_lat, from_lng, to_lat, to_lng):
        seconds, distance_m, shape = self.route(from_lat, from_lng, to_lat, to_lng)
        return seconds, distance_m, shape, None

    def route(self, from_lat, from_lng, to_lat, to_lng):
        d = haversine_m(from_lat, from_lng, to_lat, to_lng)
        return int(pace_corrected_walk_seconds(d)), d, None

    def leg_seconds(self, from_lat, from_lng, to_lat, to_lng) -> int:
        return self.route(from_lat, from_lng, to_lat, to_lng)[0]

    def isochrone(self, lat: float, lng: float, minutes: int, **_kw) -> None:
        # (`**_kw`: the pace-aware reach passes `walking_speed_kmh` since Phase 2 —
        # a cold engine answers no isochrone whatever it is asked.)
        return None

    def close(self) -> None:
        return None


#: Phase 8 S8.4 — the C5 payload: one ≥5-word sentence injected verbatim at TWO
#: stops. Floor-neutral on purpose (no motion verb, no leg deixis, no compass):
#: the ONLY defect it carries is the cross-stop repeat the rubric's C5 names.
_C5_DUP_TEXT = "The very same words end this story once more."


def _c5_repeat_defect(sentences: list[dict]) -> list[dict]:
    """Rewrite the stop's close to the shared C5 payload (still one close, last)."""
    out = list(sentences)
    for i in range(len(out) - 1, -1, -1):
        if out[i].get("source_id") == "GLUE_CLOSING":
            out[i] = {**out[i], "text": _C5_DUP_TEXT}
            break
    return out


def _hallucinate_defect(sentences: list[dict]) -> list[dict]:
    """The untraceable-citation defect (a VERIFY blocker), on the first sentence."""
    out = list(sentences)
    out[0] = {
        **out[0],
        "source_id": "beat-that-was-never-in-the-corpus",
        "source_type": "beat",
        "also_cites": [],
    }
    return out


class _OneBadRollExecutor:
    """A stateful roll-dependent author: the DAY compose of each `bad_stops` stop
    gets `defect` applied on its first roll (every roll when `always`), and every
    later roll echoes the stitch clean — the measured live shape of the writer's
    stochastic collisions (Camille run 1 vs runs 2-3, 2026-08-23). Full-telling
    calls (already_told set) are never defected and are excluded from
    `day_calls_by_stop`, so assertions about the bounded targeted re-roll count
    only the day's own composes."""

    cost_bearing = False
    provider_name = "offline"

    def __init__(self, defect, *, bad_stops: frozenset[int] | set[int], always: bool = False):
        self._defect = defect
        self._bad = set(bad_stops)
        self._always = always
        self._day_calls: dict[int, int] = {}
        self._lock = threading.Lock()

    def day_calls_by_stop(self) -> dict[int, int]:
        with self._lock:
            return dict(self._day_calls)

    def execute(self, unit) -> PhysicalProviderResponse:
        is_day_compose = not unit.authorized_request.already_told
        if is_day_compose:
            with self._lock:
                n = self._day_calls[unit.stop_index] = self._day_calls.get(unit.stop_index, 0) + 1
        sentences = [s.model_dump(mode="json") for s in unit.authorized_request.stitched.script]
        if is_day_compose and unit.stop_index in self._bad and (self._always or n == 1):
            sentences = self._defect(sentences)
        return _offline_response(unit, sentences)


def _persisted_stop_narrations(live_neo4j, trip_id: str) -> list[dict]:
    with live_neo4j.session() as s:
        return s.run(
            "MATCH (t:Trip {id: $tid})-[:HAS_STOP]->(i:ItineraryItem) "
            "RETURN i.id AS id, i.narration AS narration, i.close_text AS close_text "
            "ORDER BY i.sort_order",
            tid=trip_id,
        ).data()


def _override_dep(client, dep: str, value):
    """Swap a FastAPI dependency for a test double; returns the override key."""
    from src.api import dependencies

    target = getattr(dependencies, dep)
    client.app.dependency_overrides[target] = lambda: value
    return target


def _clear_dep(client, target) -> None:
    client.app.dependency_overrides.pop(target, None)


@pytest.fixture()
def cutover_trip(client):
    """One freshly generated, not-yet-composed trip on the live corpus."""
    resp = client.post("/api/v1/trips/generate", json=_body(NOLENS_PROFILE_ID))
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_a_dirty_local_tree_is_stamped_and_warned_never_refused(monkeypatch, caplog) -> None:
    """A build fingerprint names the commit a tour came from. Off a deployment, the
    local HEAD is that commit — TAGGED ``local_dirty_tree=True`` with a WARNING when
    the working tree has uncommitted changes (not reproducible, not certifiable) —
    and it is NEVER a refusal and needs no opt-in (2026-08-18). The class of error
    this closes: an environment condition disguised as a product failure — the old
    refusal turned every `make api` compose on a developer tree into a 503, and each
    entry point had to remember a flag. On Render the commit is the deployment's and
    git is never consulted. UNDO: bring the refusal back -> RED here on any dirty tree
    (and this suite's own tree is dirty whenever it is being worked on)."""
    import logging

    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    monkeypatch.delenv("GIT_COMMIT_SHA", raising=False)
    monkeypatch.delenv("ONDOWAY_ALLOW_DIRTY_LOCAL_BUILD", raising=False)
    with caplog.at_level(logging.WARNING, logger="ondoway.api"):
        identity = resolve_build_identity()  # never raises, whatever the tree
    import re
    import subprocess

    from src.tour.premium_tour import REPO_ROOT

    assert re.fullmatch(r"[0-9a-f]{40}", identity.commit_sha)
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain=v1"], cwd=REPO_ROOT, capture_output=True, text=True
        ).stdout.strip()
    )
    assert identity.local_dirty_tree is dirty
    assert ("DIRTY local tree" in caplog.text) is dirty
    # A deployment stamps its own commit and never reads git.
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    deployed = resolve_build_identity()
    assert deployed.commit_sha == "a" * 40 and deployed.local_dirty_tree is False


def _orangerie_pin(live_neo4j) -> list[str]:
    """Rosemary's doc names the Orangerie, so her day carries it as her own pin.

    The S3 subject gate reads the dwell pool by beat family, and the Orangerie's
    one active beat is tagged war_conflict — a miss for visual_art at any hop —
    so her art-lensed day honestly sheds it (fewer honest stops beat a padded
    day; the Phase 9 decisions doc §4). Her doc's three-stop shape (Orangerie,
    bench, Orsay) survives as HER hand on the day: a pin outranks the gate by
    design, and everything downstream (the seated rest, the promise-tier
    timings) keeps the geometry it was measured on."""
    with live_neo4j.session() as s:
        row = s.run(
            "MATCH (p:POI {city_name: 'paris'}) "
            "WHERE toLower(p.name) CONTAINS 'orangerie' "
            "RETURN p.id AS id ORDER BY p.id LIMIT 1"
        ).single()
    assert row, "the live graph carries no Orangerie POI — re-sync the dev corpus"
    return [row["id"]]


@needs_neo4j
def test_a_day_with_a_rest_generates_on_the_app_path(client, live_neo4j) -> None:
    """Rosemary's day at her own doc's leg length (Orsay round trip, take-it-easy,
    art lens, the Orangerie pinned — `_orangerie_pin` — and a 13-minute cap) seats
    a BENCH — a rest with no story beat. The app
    path (`/trips/generate`, the model the phone reads) must carry it: until
    2026-08-18 it 500'd on the rest's missing beat, and every family / take-it-easy
    day that seated a rest was unreachable from the phone while the preview path
    (another model) showed it fine. Found by W5.13's demo setup. UNDO: require
    `GeneratedStop.beat_id` -> RED (500)."""
    resp = client.post(
        "/api/v1/trips/generate",
        json=_body(
            LENSED_PROFILE_ID,
            center_lat=48.859962,
            center_lng=2.326561,
            duration_min=180,
            round_trip=True,
            start_date="2026-08-19",
            end_date="2026-08-19",
            start_time="14:00",
            party="take_it_easy",
            lenses=["visual_art"],
            max_leg_minutes=13,
            pinned_poi_ids=_orangerie_pin(live_neo4j),
        ),
    )
    assert resp.status_code == 201, resp.text
    stops = resp.json()["stops"]
    rests = [s for s in stops if s["beat_id"] is None]
    assert rests, f"premise: the 13-minute day seats a rest — {[s['poi_name'] for s in stops]}"
    assert all(s["poi_id"] and s["start_time"] for s in stops), "a rest is a whole stop"


def test_generate_and_compose_report_what_degraded(client, live_neo4j, cutover_trip) -> None:
    """AC-18 — the phone's two endpoints say what quietly went wrong, like the web does.

    OWNER RULING 2026-07-31: "Don't just log errors. Actually show them in the workbench
    UI. Otherwise, they're invisible." The workbench preview has carried that list since
    then. The phone's own two calls collected the same facts and threw them away, so a
    tour planned on estimated walking times looked, on a phone, exactly like one planned
    on measured ones.

    The fault injected is the real one rather than a synthetic marker: the walking
    service is unreachable, so every leg falls back to a straight-line estimate and the
    version read fails. Both must travel, and NEITHER may become a refusal — the service
    is a real dependency that cold-starts and rebuilds tiles, and refusing would take
    tour generation down for every user while it does.

    Three things the already-shipped phone build depends on are asserted literally,
    because a mismatch on any of them shows the traveller NOTHING, with no error and no
    crash: the list is at the TOP level of the body, it is a JSON array, and every row
    is an object carrying a ``human`` string. ``human`` is the only key the production
    Dart reads.

    WHY GENERATE IS PROVEN WITH A PLANTED ROW. Both halves were first written against
    the real outage. Generate does not pass that way, and NOT because of anything this
    change does: with estimated legs the live 90-minute walk comes out at 6009s against
    a 4860-5940s band, and the planner refuses it 422 — the over-ceiling refusal a
    separate lane is fixing, which the timebox repair cannot bring back down because it
    can add or swap a stop but not drop one. Asserting generate's routing row on the
    live corpus would import that unrelated bug into this test and make it fail for a
    reason it is not about. A planted row proves what this step actually changed — that
    a degradation recorded anywhere inside the request reaches the top level of the
    reply instead of the floor — with no dependence on which route the planner happens
    to pick. The routing row's own content is proven end-to-end on the planning surface
    generate shares by
    test_trip_preview_contract.py::test_estimated_legs_are_labelled_not_silently_shipped.

    THE COMPOSE HALF (the real outage on ``/compose``: 200 with the routing rows, never
    a refusal, ``component == "trips.compose_trip"``) was REMOVED at Phase 5 D5.0 with
    the frozen trip it drove (design §8.2; audit C §1: "its compose half must be
    re-pointed at the session endpoints at Phase 5; the generate half survives as is").
    It returns at S5.8 against the living session's endpoints, assertions re-derived
    there with their citation. The generate half above is unchanged.
    """
    # 1. A CLEAN RUN SAYS SO. An empty list is a real statement — "nothing degraded" —
    #    and it is not the same as a missing key, which a client cannot distinguish
    #    from an old server.
    assert "degradations" in cutover_trip, sorted(cutover_trip)
    assert cutover_trip["degradations"] == [], cutover_trip["degradations"]

    # 2. GENERATE: a degradation recorded from inside the request reaches the caller.
    real_loader = trips.load_paris_corpus

    def _loader_that_degrades(*args, **kwargs):
        record(
            kind="test_probe",
            human="A probe recorded one degradation so the channel can be seen.",
            component="tests.test_trip_api",
        )
        return real_loader(*args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(trips, "load_paris_corpus", _loader_that_degrades)
        degraded = client.post("/api/v1/trips/generate", json=_body(NOLENS_PROFILE_ID))
    assert degraded.status_code == 201, degraded.text

    rows = degraded.json()["degradations"]
    assert isinstance(rows, list), type(rows)
    assert [row["kind"] for row in rows] == ["test_probe"], rows
    row = rows[0]
    assert isinstance(row, dict), row
    assert isinstance(row.get("human"), str) and row["human"], row
    # The six keys ``summarize`` emits — including the count that collapses repeats, or
    # a fan-out of identical failures prints the same line once per occurrence.
    assert set(row) == {
        "kind",
        "human",
        "component",
        "error_type",
        "error_message",
        "context",
        "count",
    }, sorted(row)
    assert row["count"] == 1, row


# ---------------------------------------------------------------------------
# Phase 5 S5.8 — THE LIVING SESSION on the wire; the frozen trip is deleted (§8.2)
# ---------------------------------------------------------------------------


def _compose(client, trip_id, executor):
    target = _override_dep(client, "get_premium_compose_executor", executor)
    try:
        return client.post(f"/api/v1/trips/{trip_id}/compose", json={"route_id": f"{trip_id}-opt1"})
    finally:
        _clear_dep(client, target)


@needs_neo4j
class TestLivingSession:
    """Design §8.2 deletes the frozen trip — "a stop list written once, narrated once,
    refusing a second compose (`mark_trip_composed`, the 409)" — for "a living session
    with versions, promises, alternates, history". Design §4.6: the server is the only
    place a plan decision is made; it emits the contingency set beside the plan and
    the phone SELECTS from it. Audit C's rulings on the deleted rows are adopted
    verbatim: the refusal CONTRACT survives (quality standard §6b, design §7.2), the
    one-compose lock dies."""

    def test_a_second_compose_is_the_next_version_not_a_conflict(
        self, client, live_neo4j, cutover_trip
    ):
        """Design §8.2 verbatim. Before: the second compose was a 409 `already_composed`
        (audit C: "the purest single row in the set"). Now every write mints a version;
        the trip's stops are re-persisted through the same `replace_trip_stops`. The
        writer and the lock are gone from the source — an absence-of-code invariant,
        so the tombstone is a source read (plan §0.4 / the 2026-08-18 amendment)."""
        import inspect

        from src.api import crud
        from src.api.routes import trips as trips_routes

        trip_id = cutover_trip["trip_id"]
        first = _compose(client, trip_id, _MarkerAuthoringExecutor())
        assert first.status_code == 200, first.text
        assert first.json()["plan_version"] == 1
        second = _compose(client, trip_id, _MarkerAuthoringExecutor())
        assert second.status_code == 200, (
            f"a second compose is version 2, never a conflict: {second.status_code} {second.text}"
        )
        assert second.json()["plan_version"] == 2
        assert second.json()["stops"], "version 2 is a day"
        with live_neo4j.session() as s:
            row = s.run(
                "MATCH (t:Trip {id: $tid}) RETURN t.plan_version AS pv, "
                "t.composed_route_id AS legacy",
                tid=trip_id,
            ).single()
        assert row["pv"] == 2
        assert row["legacy"] is None, "nothing writes the frozen trip's lock any more"
        # THE TOMBSTONE: the writer and the 409 are gone.
        assert not hasattr(crud.trips, "mark_trip_composed")
        assert "already_composed" not in inspect.getsource(trips_routes)

    def test_a_verification_failure_is_a_422_that_persists_nothing(
        self, client, live_neo4j, cutover_trip
    ):
        """Audit C's ruled successor to `test_refused_flavour_is_422_and_leaves_trip_untouched`
        (quality standard §6b: "What changed is the ENGINE underneath, not the refusal
        contract"; design §7.2: "A day failing the floor is not served"). A refusal is a
        422 carrying reason + attempts + untraceable, and NOTHING degraded is persisted:
        the items are untouched, no session version is written, and GET /session says
        there is no session yet — the frozen-trip halves ("another flavour can be
        tried", `-opt1`, `rid is None`) are gone."""
        trip_id = cutover_trip["trip_id"]

        def narrations():
            with live_neo4j.session() as s:
                return s.run(
                    "MATCH (t:Trip {id: $tid})-[:HAS_STOP]->(i:ItineraryItem) "
                    "RETURN i.id AS id, i.narration AS narration ORDER BY i.sort_order",
                    tid=trip_id,
                ).data()

        before = narrations()
        refused = _compose(client, trip_id, _HallucinatingExecutor())
        assert refused.status_code == 422, refused.text
        detail = refused.json()["detail"]
        assert detail["reason"] == "compose_verification_failed"
        # Phase 8 S8.4: a VERIFY refusal now spends ONE bounded targeted re-roll
        # of the failing stops before refusing (Camille run 1, 2026-08-23: one
        # bad roll killed a day two clean rolls served). This executor fails
        # EVERY roll, so the refusal stands — at attempts=2, honestly counted.
        assert detail["attempts"] == 2
        assert detail["untraceable"] > 0
        assert narrations() == before, "a refusal must leave the trip exactly as it was"
        with live_neo4j.session() as s:
            row = s.run(
                "MATCH (t:Trip {id: $tid}) RETURN t.plan_version AS pv, t.session_json AS sj",
                tid=trip_id,
            ).single()
        assert not row["pv"] and row["sj"] is None, "a refusal writes no session version"
        no_session = client.get(f"/api/v1/trips/{trip_id}/session")
        assert no_session.status_code == 404, no_session.text
        assert no_session.json()["detail"]["reason"] == "no_session_yet"

    def test_the_session_is_on_the_wire_with_its_contingency_set(
        self, client, live_neo4j, cutover_trip
    ):
        """Design §4.6 — the plan and its contingency set, on the wire, versioned; the
        phone selects. W5.2 R1.1: wrap-up from EVERY stop; R1.2: no entry adds a
        building; §4.4.2: a question never travels without screen text; R1.5: the
        promises carry who is protected."""
        trip_id = cutover_trip["trip_id"]
        composed = _compose(client, trip_id, _MarkerAuthoringExecutor())
        assert composed.status_code == 200, composed.text
        planned_stop_ids = [st["stop_id"] for st in composed.json()["stops"]]
        planned_poi_ids = {st["poi_id"] for st in composed.json()["stops"]}

        got = client.get(f"/api/v1/trips/{trip_id}/session")
        assert got.status_code == 200, got.text
        plan = got.json()
        assert plan["plan_version"] == 1
        assert [st["stop_id"] for st in plan["stops"]] == planned_stop_ids
        tolerance = plan["retime_tolerance_seconds"]
        assert isinstance(tolerance, int) and tolerance > 0
        assert plan["promises"], "the day carries its promises"
        assert all("protected" in p for p in plan["promises"])
        entries = plan["contingencies"]
        assert entries, "the contingency set is on the wire"
        kinds = {e["trigger"]["kind"] for e in entries}
        assert "wrap_up_from" in kinds and "stop_skipped" in kinds, kinds
        wrap_from = {
            e["trigger"]["stop_id"] for e in entries if e["trigger"]["kind"] == "wrap_up_from"
        }
        assert planned_poi_ids <= wrap_from, "wrap-up from EVERY stop (R1.1)"
        for e in entries:
            assert set(e["stop_ids"]) <= planned_poi_ids, (e["trigger"], e["stop_ids"])
            assert e["screen_text"].strip(), e["trigger"]
            assert e["plan_version"] == 1
        assert client.get("/api/v1/trips/no-such-trip/session").status_code == 404

    def test_a_replan_is_the_next_version_over_the_same_items(
        self, client, live_neo4j, cutover_trip
    ):
        """Design §4.6 — the ONE replan brain: the phone reports its position, clocks
        and learned rates; the server replans the remainder through THE planner and
        mints version N+1 over the SAME items (no re-authoring, no new place, the
        audio already made is kept). AC-18's compose half, re-pointed here (audit C,
        LOAD-BEARING): the walking outage travels on the reply as the traveller's
        sentence, never a refusal."""
        trip_id = cutover_trip["trip_id"]
        composed = _compose(client, trip_id, _MarkerAuthoringExecutor())
        assert composed.status_code == 200, composed.text
        stops = composed.json()["stops"]
        assert len(stops) >= 2
        item_ids = {st["stop_id"] for st in stops}
        # Just leaving the first stop, a quarter of an hour in, heading to the second.
        here = stops[0]
        body = {
            "lat": here["lat"],
            "lng": here["lng"],
            "wall_elapsed_seconds": 15 * 60,
            "tour_elapsed_seconds": 14 * 60,
            "observed_pace": 1.1,
            "listening_rate": 1.2,
            "next_stop_index": 1,
        }
        replanned = client.post(f"/api/v1/trips/{trip_id}/session/replan", json=body)
        assert replanned.status_code == 200, replanned.text
        plan = replanned.json()
        assert plan["plan_version"] == 2
        assert {st["stop_id"] for st in plan["stops"]} <= item_ids, (
            "a replan is a version over the SAME items — never a different day"
        )
        assert stops[1]["stop_id"] in {st["stop_id"] for st in plan["stops"]}, (
            "the next planned stop must survive a replan with the day mostly ahead: "
            f"{[st['stop_id'] for st in plan['stops']]}"
        )
        assert all(e["plan_version"] == 2 for e in plan["contingencies"])
        assert client.get(f"/api/v1/trips/{trip_id}/session").json()["plan_version"] == 2

        # THE OUTAGE TRAVELS (AC-18, re-pointed): a cold walking engine degrades, never refuses.
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(trips, "RoutingClient", _ColdStartingRoutingClient)
            degraded = client.post(f"/api/v1/trips/{trip_id}/session/replan", json=body)
        assert degraded.status_code == 200, degraded.text
        rows = {r["kind"]: r for r in degraded.json()["degradations"]}
        assert "walking_times_estimated" in rows, degraded.json()["degradations"]
        assert rows["walking_times_estimated"]["human"] == (
            "Walking times between stops are estimates, not measured routes, so the tour "
            "may run a little longer or shorter than it says."
        )
        assert degraded.json()["plan_version"] == 3

    def test_a_walker_reports_a_shut_door_and_the_day_replans_without_it(
        self, client, live_neo4j, cutover_trip
    ):
        """Docs/adr/0006 rule 5 — the phone reports the stop whose door the walker
        found shut; the day replans WITHOUT that stop through the one replan brain;
        the place's `hours_closed_reports` rises by one per report so the next
        walker with a guess there hears it; the hours themselves never change. A
        stop not ahead of the walker cannot be reported."""
        trip_id = cutover_trip["trip_id"]
        composed = _compose(client, trip_id, _MarkerAuthoringExecutor())
        assert composed.status_code == 200, composed.text
        stops = composed.json()["stops"]
        assert len(stops) >= 2
        shut = stops[1]
        with live_neo4j.session() as s:
            before = s.run(
                "MATCH (p:POI {id: $pid}) "
                "RETURN p.hours_closed_reports AS n, p.opening_hours AS hours",
                pid=shut["poi_id"],
            ).single()
        try:
            body = {
                "lat": shut["lat"],
                "lng": shut["lng"],
                "wall_elapsed_seconds": 25 * 60,
                "tour_elapsed_seconds": 24 * 60,
                "next_stop_index": 1,
                "closed_stop_id": shut["poi_id"],
            }
            replanned = client.post(f"/api/v1/trips/{trip_id}/session/replan", json=body)
            assert replanned.status_code == 200, replanned.text
            plan = replanned.json()
            assert plan["plan_version"] == 2
            assert shut["poi_id"] not in {st["poi_id"] for st in plan["stops"]}, (
                "the shut door is out of the day"
            )
            assert not any(
                shut["poi_id"] in e["stop_ids"] for e in plan["contingencies"]
            ), "no carried-forward answer names the shut door"
            with live_neo4j.session() as s:
                after = s.run(
                    "MATCH (p:POI {id: $pid}) "
                    "RETURN p.hours_closed_reports AS n, p.opening_hours AS hours",
                    pid=shut["poi_id"],
                ).single()
            assert after["n"] == (before["n"] or 0) + 1
            assert after["hours"] == before["hours"], "one report never rewrites hours"
            # The door is out of this day now, so the SAME walker cannot report it
            # twice; a second walker's day (a fresh compose) can.
            twice_over = client.post(f"/api/v1/trips/{trip_id}/session/replan", json=body)
            assert twice_over.status_code == 422, twice_over.text
            recomposed = _compose(client, trip_id, _MarkerAuthoringExecutor())
            assert recomposed.status_code == 200, recomposed.text
            again = client.post(f"/api/v1/trips/{trip_id}/session/replan", json=body)
            assert again.status_code == 200, again.text
            with live_neo4j.session() as s:
                twice = s.run(
                    "MATCH (p:POI {id: $pid}) RETURN p.hours_closed_reports AS n",
                    pid=shut["poi_id"],
                ).single()["n"]
            assert twice == (before["n"] or 0) + 2
            # The LAST door of the day found shut: the day ends without it, never a
            # refusal — the walker is still owed the way home.
            recomposed = _compose(client, trip_id, _MarkerAuthoringExecutor())
            assert recomposed.status_code == 200, recomposed.text
            last = stops[-1]
            ending = client.post(
                f"/api/v1/trips/{trip_id}/session/replan",
                json={
                    **body,
                    "lat": last["lat"],
                    "lng": last["lng"],
                    "next_stop_index": len(stops) - 1,
                    "closed_stop_id": last["poi_id"],
                },
            )
            assert ending.status_code == 200, ending.text
            assert last["poi_id"] not in {st["poi_id"] for st in ending.json()["stops"]}
            # A stop already behind the walker is not a door ahead: refused by name.
            behind = client.post(
                f"/api/v1/trips/{trip_id}/session/replan",
                json={**body, "next_stop_index": 2, "closed_stop_id": stops[0]["poi_id"]},
            )
            assert behind.status_code == 422, behind.text
            assert stops[0]["poi_id"] in json.dumps(behind.json())
        finally:
            with live_neo4j.session() as s:
                s.run(
                    "MATCH (p:POI {id: $pid}) "
                    "SET p.hours_closed_reports = coalesce(p.hours_closed_reports, 1) - 1",
                    pid=stops[-1]["poi_id"],
                )
                if before["n"] is None:
                    s.run(
                        "MATCH (p:POI {id: $pid}) REMOVE p.hours_closed_reports",
                        pid=shut["poi_id"],
                    )
                else:
                    s.run(
                        "MATCH (p:POI {id: $pid}) SET p.hours_closed_reports = $n",
                        pid=shut["poi_id"], n=before["n"],
                    )

    def test_a_live_replan_answers_with_the_day_and_the_full_set_follows(
        self, client, live_neo4j, cutover_trip
    ):
        """W5.12 — the LIVE REPLAN bar's remedy, the design's own ("narrow the live path
        to re-timing; widen the precomputed set"): measured on the FD trace the set was
        ~0.4 s per entry against ~1 s for the one planner call, so the reply carries the
        replanned DAY (stops re-timed, promises, the day's planned end) plus the previous
        version's answers that still hold for it, and the FULL set is computed right after
        the reply and persisted as the same version — the next fetch has it. Every carried
        entry names only stops still ahead; the persisted set has a wrap-up from every
        stop of the new day (R1.1)."""
        trip_id = cutover_trip["trip_id"]
        composed = _compose(client, trip_id, _MarkerAuthoringExecutor())
        assert composed.status_code == 200, composed.text
        v1 = client.get(f"/api/v1/trips/{trip_id}/session").json()
        assert v1["planned_end_hhmm"] and len(v1["planned_end_hhmm"]) == 5, v1["planned_end_hhmm"]
        here = v1["stops"][0]
        reply = client.post(
            f"/api/v1/trips/{trip_id}/session/replan",
            json={
                "lat": here["lat"],
                "lng": here["lng"],
                "wall_elapsed_seconds": 15 * 60,
                "tour_elapsed_seconds": 14 * 60,
                "next_stop_index": 1,
            },
        )
        assert reply.status_code == 200, reply.text
        day = reply.json()
        ahead = {st["poi_id"] for st in day["stops"]}
        assert day["plan_version"] == 2 and day["planned_end_hhmm"] == v1["planned_end_hhmm"]
        for e in day["contingencies"]:
            assert e["plan_version"] == 2
            assert set(e["stop_ids"]) <= ahead and set(e["alternate_stop_ids"]) <= ahead
            assert e["trigger"].get("stop_id") in ahead
        # The full set landed for the same version (the TestClient runs the
        # background task before returning): a wrap-up from every stop ahead.
        stored = client.get(f"/api/v1/trips/{trip_id}/session").json()
        assert stored["plan_version"] == 2
        wrap_from = {
            e["trigger"]["stop_id"]
            for e in stored["contingencies"]
            if e["trigger"]["kind"] == "wrap_up_from"
        }
        assert ahead <= wrap_from, (ahead, wrap_from)
        assert len(stored["contingencies"]) >= len(day["contingencies"])

    def test_a_day_built_around_one_place_asks_rather_than_dropping_it(
        self, client, live_neo4j
    ):
        """W5.14 / S5.16 — THE PROMISE TIER ON THE LIVE PATH, on Rosemary's own day
        (Orsay round trip, take-it-easy, art lens, her doc's 13-minute legs: the
        Orangerie — carried as her own pin, `_orangerie_pin` — her bench, the
        Orsay). Forty-six minutes late out of the Orangerie —
        beyond every precomputed band (the last late band ends at 40) — the live replan
        used to hand back "Bench" alone,
        the Orsay dropped as fabric to keep an 8-minute rest, in silence (the D6
        transcript, W5.13). Now: the place she named as where the day begins and ends
        is a promise (`own_place_ids`), the replanned remainder KEEPS it, and because
        keeping everything overruns her clock the reply carries the ONE question of
        R2 — keep the full rest and be back later, or sit fewer minutes and be back by
        the clock — as an entry of kind "live" the phone applies at once. UNDO: drop the
        own place from `_person_protected` -> the Orsay is gone from the reply -> RED.

        RE-DERIVED 2026-08-19 (Phase 6 S6.1a, a written decision, not a quiet edit): the
        lateness was FIFTY minutes, tuned while compose rebuilt her day on the default
        routing surface (stairs allowed) and the session's clocks came from those legs.
        On the step-free legs she was actually planned with, 50 minutes late leaves the
        remainder 340 s over her 17:00 with everything kept — more than her 8-minute
        bench can give up above the 3-minute shortest rest (R2.3: shortened, never
        removed), so the product correctly asks NOTHING and lets the finish move with
        one screen line (R2.4/R2.5, S5.18). Forty-six minutes is the same scenario on
        the right clocks: 100 s over, a 6-minute sit absorbs it, the question fires."""
        gen = client.post(
            "/api/v1/trips/generate",
            json=_body(
                LENSED_PROFILE_ID,
                center_lat=48.859962,
                center_lng=2.326561,
                duration_min=180,
                round_trip=True,
                start_date="2026-08-19",
                end_date="2026-08-19",
                start_time="14:00",
                party="take_it_easy",
                lenses=["visual_art"],
                max_leg_minutes=13,
                pinned_poi_ids=_orangerie_pin(live_neo4j),
                # Phase 8 S8.4: the serving gate (§7.2) measures this day's echo at
                # ~0.117 audio-per-walking — a coin-flip under C3's 0.12 floor,
                # whose calibration corpus held no take-it-easy day (the W8.6
                # disposition row in phase8-ledger.md). Her "More talking" dial
                # keeps the day HER day (bench, 13-minute legs, step-free) and
                # feeds it the narration her own carried ask wants at the bench —
                # a day the product serves, which is this test's premise.
                narration_density="more",
            ),
        )
        assert gen.status_code == 201, gen.text
        trip_id = gen.json()["trip_id"]
        names = [s["poi_name"] for s in gen.json()["stops"]]
        assert any("Orsay" in n for n in names) and any(n == "Bench" for n in names), names
        composed = _compose(client, trip_id, _MarkerAuthoringExecutor())
        assert composed.status_code == 200, composed.text
        session = client.get(f"/api/v1/trips/{trip_id}/session").json()
        stops = session["stops"]
        orsay = next(s for s in stops if "Orsay" in s["poi_name"])
        assert any(p["kind"] == "anchor" or p["protected"] for p in session["promises"])
        first = stops[0]  # the Orangerie
        # Standing at the first stop, having stayed 50 minutes past its clock.
        hh, mm = (int(x) for x in first["start_time"].split(":"))
        dh, dm = (int(x) for x in session["day_start_hhmm"].split(":"))
        elapsed = (hh * 60 + mm - dh * 60 - dm) * 60 + int(first["dwell_seconds"]) + 46 * 60
        reply = client.post(
            f"/api/v1/trips/{trip_id}/session/replan",
            json={
                "lat": first["lat"],
                "lng": first["lng"],
                "wall_elapsed_seconds": elapsed,
                "tour_elapsed_seconds": elapsed,
                "next_stop_index": 1,
            },
        )
        assert reply.status_code == 200, reply.text
        day = reply.json()
        kept = [s["poi_name"] for s in day["stops"]]
        assert orsay["poi_id"] in {s["poi_id"] for s in day["stops"]}, (
            f"the Orsay is her day; the live replan dropped it: {kept}"
        )
        live = [e for e in day["contingencies"] if e["trigger"]["kind"] == "live"]
        kinds = [e["trigger"] for e in day["contingencies"]]
        assert live, f"no question on the live path; entries: {kinds}"
        q = live[0]
        assert q["question"] and q["question"].endswith("?") and ", or " in q["question"], q
        assert "rest" in q["question"] and "Orsay" in q["question"], q["question"]
        assert q["default_arm"] in ("keep", "shorten")
        assert orsay["poi_id"] in q["stop_ids"] and orsay["poi_id"] in q["alternate_stop_ids"], q
        assert q["screen_text"] == q["question"]

    def test_a_phone_clock_that_diverges_is_reported_never_corrected(
        self, client, live_neo4j, cutover_trip
    ):
        """THE SESSION CLOCK SEAM, on the wire (plan S5.10; design §4.6 — the
        silent-divergence bug the one-brain rule exists to prevent). The phone sends
        its OWN re-timed clock for the stop it is heading to; the server compares it
        with what ITS one expression says for the same stop in the version it just
        minted (`stop_clocks`, the wire's `start_time`) and a gap beyond the session's
        tolerance is REPORTED — a row on the reply's `degradations`, both registers —
        and never corrected in either direction: the reply keeps the server's clock.
        A phone clock inside the tolerance writes no row. UNDO: assign the phone's
        clock into the reply's first stop -> RED (the clock changed); drop the record
        -> RED (no row)."""
        trip_id = cutover_trip["trip_id"]
        composed = _compose(client, trip_id, _MarkerAuthoringExecutor())
        assert composed.status_code == 200, composed.text
        stops = composed.json()["stops"]
        assert len(stops) >= 2
        # The session's clocks are the one expression's: each stop names a real HH:MM.
        session = client.get(f"/api/v1/trips/{trip_id}/session").json()
        assert all(len(st["start_time"]) == 5 for st in session["stops"]), session["stops"]
        tolerance = session["retime_tolerance_seconds"]
        here = stops[0]
        base = {
            "lat": here["lat"],
            "lng": here["lng"],
            "wall_elapsed_seconds": 15 * 60,
            "tour_elapsed_seconds": 14 * 60,
            "next_stop_index": 1,
        }
        # First: agree. The reply's clock for the next stop is the server's own;
        # a phone that reckons the same writes no row. (`next_stop_index` indexes
        # the version being replanned FROM: after this reply the next stop is that
        # version's first, so the later calls say 0.)
        agreed = client.post(f"/api/v1/trips/{trip_id}/session/replan", json=base)
        assert agreed.status_code == 200, agreed.text
        server_hhmm = agreed.json()["stops"][0]["start_time"]
        assert agreed.json()["stops"][0]["poi_id"] == stops[1]["poi_id"], agreed.json()["stops"]
        ahead = {**base, "next_stop_index": 0}
        same = client.post(
            f"/api/v1/trips/{trip_id}/session/replan",
            json={**ahead, "phone_next_stop_hhmm": server_hhmm},
        )
        assert same.status_code == 200, same.text
        assert same.json()["stops"][0]["poi_id"] == stops[1]["poi_id"], same.json()["stops"]
        assert "session_clock_divergence" not in {r["kind"] for r in same.json()["degradations"]}, (
            same.json()["degradations"],
            server_hhmm,
            same.json()["stops"][0]["start_time"],
        )
        # The session names the frame both clocks live in.
        assert len(same.json()["day_start_hhmm"]) == 5, same.json()["day_start_hhmm"]
        # Then: diverge by more than the tolerance. Reported, not corrected.
        hh, mm = (int(x) for x in server_hhmm.split(":"))
        later = (hh * 60 + mm + tolerance // 60 + 5) % (24 * 60)
        phone_hhmm = f"{later // 60:02d}:{later % 60:02d}"
        diverged = client.post(
            f"/api/v1/trips/{trip_id}/session/replan",
            json={**ahead, "phone_next_stop_hhmm": phone_hhmm},
        )
        assert diverged.status_code == 200, diverged.text
        rows = {r["kind"]: r for r in diverged.json()["degradations"]}
        assert "session_clock_divergence" in rows, diverged.json()["degradations"]
        row = rows["session_clock_divergence"]
        assert "Neither clock was changed" in row["human"], row["human"]
        assert row["context"]["phone_hhmm"] == phone_hhmm
        assert row["context"]["server_hhmm"] == diverged.json()["stops"][0]["start_time"]
        assert diverged.json()["stops"][0]["start_time"] == same.json()["stops"][0]["start_time"], (
            "the server keeps its own clock — the phone's is reported, never adopted"
        )
        assert diverged.json()["stops"][0]["start_time"] != phone_hhmm

    def test_a_day_failing_the_rubric_floor_is_refused_and_the_trip_untouched(
        self, client, live_neo4j, cutover_trip
    ):
        """Phase 8 S8.4 (design §7.2; quality standard §7): `score_tour`'s `passed`
        finally GATES SERVING on the persisted path. W8.1(b) proved the gap live:
        Greta's served day carried `BLOCKER C3-thin` and was composed, persisted,
        voiced and served, because nothing on this path ran the rubric. Here a
        C5 cross-stop verbatim repeat — a blocker `_dedup_composed` cannot remove
        (glue is never deduped) — survives one bounded targeted recompose (the
        executor keeps injecting it), so the day is REFUSED by name (R8: the named
        gate, the stop, our own message — never provider prose) and the trip is
        byte-untouched with no session written. This is audit F's ordered
        caller-side test, beside `test_passed_is_false…`, never an edit of it.
        UNDO: drop the `score_tour` call from `compose_trip` -> the repeat is
        served 200 -> RED."""
        trip_id = cutover_trip["trip_id"]
        before = _persisted_stop_narrations(live_neo4j, trip_id)

        executor = _OneBadRollExecutor(_c5_repeat_defect, bad_stops={0, 1}, always=True)
        resp = _compose(client, trip_id, executor)
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["reason"] == "tour_quality_blocked", detail
        assert any(b["check"] == "C5-verbatim-repeat" for b in detail["blockers"]), detail
        named = next(b for b in detail["blockers"] if b["check"] == "C5-verbatim-repeat")
        assert named["stop_idx"] is not None and named["message"], named
        assert detail["recomposed_stops"], (
            "C5 is compose-fixable; the gate must spend its one bounded recompose "
            f"before refusing: {detail}"
        )
        assert _persisted_stop_narrations(live_neo4j, trip_id) == before, (
            "a quality refusal must leave the trip exactly as it was"
        )
        with live_neo4j.session() as s:
            row = s.run(
                "MATCH (t:Trip {id: $tid}) RETURN t.plan_version AS pv, t.session_json AS sj",
                tid=trip_id,
            ).single()
        assert not row["pv"] and row["sj"] is None, "a quality refusal writes no session"

    def test_a_fixable_rubric_blocker_gets_one_targeted_recompose(
        self, client, live_neo4j, cutover_trip
    ):
        """Phase 8 S8.4: `compose_fixable` + `StopMaterial` authorize AT MOST ONE
        targeted per-stop recompose before the refusal — same request, fresh roll,
        ONLY the failing stop (a whole-day re-roll would burn every stop's spend
        on one stop's defect). The C5 injector is transient (first roll only), so
        the recompose clears it: the day serves 200 and the persisted day carries
        no repeated sentence. UNDO: recompose every stop instead -> the
        per-stop call counts flatten -> RED."""
        trip_id = cutover_trip["trip_id"]
        executor = _OneBadRollExecutor(_c5_repeat_defect, bad_stops={0, 1})
        resp = _compose(client, trip_id, executor)
        assert resp.status_code == 200, resp.text
        assert resp.json()["plan_version"] == 1

        repeat_carriers = [
            st for st in resp.json()["stops"] if _C5_DUP_TEXT in (st.get("close_text") or "")
        ]
        assert len(repeat_carriers) <= 1, (
            "the served day still repeats the injected close at "
            f"{len(repeat_carriers)} stops — the recompose did not clear the C5"
        )
        # TARGETED: C5 names its SECOND occurrence (stop 1); only that stop
        # re-rolls. Full tellings ride the same executor afterwards, so the
        # bound is per-DAY-COMPOSE calls: stop 1 exactly one more than stop 0.
        day_calls = executor.day_calls_by_stop()
        assert day_calls.get(1, 0) == day_calls.get(0, 0) + 1, day_calls

    def test_a_verify_refusal_gets_one_targeted_reroll_before_the_422(
        self, client, live_neo4j, cutover_trip
    ):
        """Phase 8 S8.3's bounded retry (the step's own words: untraceable writer
        output "refuses at AUTHORING time with the bounded retry, so her day
        composes deterministically") — measured live 2026-08-23: Camille's run-1
        compose died 422 on ONE bad roll (`fused_across_playback_contexts`,
        stop 3) while the identical request rolled clean twice after. One
        targeted same-request re-roll of ONLY the stops the VERIFY report names
        turns a one-bad-roll day into a served day; a stop that fails twice
        still refuses (the always-bad case keeps
        `test_a_verification_failure_is_a_422_that_persists_nothing` honest,
        now at attempts=2). UNDO: drop the re-roll -> this day dies on the
        first roll -> RED."""
        trip_id = cutover_trip["trip_id"]
        executor = _OneBadRollExecutor(_hallucinate_defect, bad_stops={1})
        resp = _compose(client, trip_id, executor)
        assert resp.status_code == 200, resp.text
        day_calls = executor.day_calls_by_stop()
        assert day_calls.get(1, 0) == day_calls.get(0, 0) + 1, (
            f"only the failing stop re-rolls: {day_calls}"
        )

    def test_a_reordering_replan_rewrites_the_item_so_no_fetch_can_resurrect_the_stale_line(
        self, client, live_neo4j, cutover_trip, monkeypatch, tmp_path
    ):
        """S1.M6 — the replanned line reaches the ITEM, where audio truth lives.

        The session GET overlays the items' current audio onto the stops
        (`_with_live_audio`), because audio facts live on the items. A replan that
        rewrites a leg line only in the session json therefore un-drops itself on the
        phone's very next fetch: the item still carries the OLD text's file, and the
        overlay serves it over the cleared field. Sofia hears the stale walk again.

        So a re-legged stop's new text and cleared audio are written to its item in the
        same request, and the voicing pass is scheduled for exactly those items — the
        `_finish_session_set` mould — so the corrected words get their file before the
        walk when the machine is quick, and the leg is silent rather than wrong when it
        is not. A stop whose pair still holds keeps its item byte-untouched: its audio
        was paid for and its words are still true.

        UNDO, measured: skip the item write and the ITEM keeps the old words — the
        background voicing then re-voices THOSE, so it is the item-vs-wire assertion
        at the end that goes red (the wire serves new words over an item that never
        got them), not the stale-url check alone. Give the replan back the wholesale
        copy instead and the premise assertion goes red: nothing is rewritten at all.
        """
        monkeypatch.setenv("AUDIO_STORAGE", "local")
        monkeypatch.setenv("AUDIO_STORAGE_PATH", str(tmp_path))
        # The fixture day's own two stops, PINNED (Theo's gesture), at the exact
        # duration the planner names for the pinned pair: the same clean-composing
        # beats with no room for a third stop. A pinned stop is a protected
        # promise, so the replan must KEEP both and can only re-order them.
        day_poi_ids = [st["poi_id"] for st in cutover_trip["stops"]]
        assert len(day_poi_ids) >= 2, cutover_trip["stops"]
        gen = client.post(
            "/api/v1/trips/generate",
            json=_body(NOLENS_PROFILE_ID, pinned_poi_ids=day_poi_ids, duration_min=109),
        )
        assert gen.status_code == 201, gen.text
        trip_id = gen.json()["trip_id"]
        composed = _compose(client, trip_id, _MarkerAuthoringExecutor())
        assert composed.status_code == 200, composed.text
        v1 = client.get(f"/api/v1/trips/{trip_id}/session").json()
        stops = v1["stops"]
        assert len(stops) >= 2, [st["poi_name"] for st in stops]
        legged_items = [st["stop_id"] for st in stops if st["leg_narration"]]
        assert legged_items, "premise: the composed day carries leg lines"
        line_was = {st["stop_id"]: st["leg_narration"] for st in stops}
        # The voiced state: every leg line already has its file on the ITEM.
        with live_neo4j.session() as s:
            s.run(
                "MATCH (i:ItineraryItem) WHERE i.id IN $ids "
                "SET i.leg_audio_url = 'file:///stale-' + i.id + '.mp3', "
                "    i.leg_audio_duration_sec = 9.0",
                ids=legged_items,
            )

        # Replanning from beside the LAST stop re-orders the kept day (measured on
        # this corpus: the planner walks nearest-first from where the person stands).
        # Beside the FAR stop, a street off its pin: from there the planner walks
        # nearest-first, and with both stops protected it can only swap them.
        far = stops[-1]
        reply = client.post(
            f"/api/v1/trips/{trip_id}/session/replan",
            json={
                "lat": far["lat"] + 0.0003,
                "lng": far["lng"] + 0.0003,
                "wall_elapsed_seconds": 15 * 60,
                "tour_elapsed_seconds": 14 * 60,
                "next_stop_index": 0,
            },
        )
        assert reply.status_code == 200, reply.text
        new_order = [st["poi_id"] for st in reply.json()["stops"]]
        kept = set(new_order)
        assert new_order != [st["poi_id"] for st in stops if st["poi_id"] in kept], (
            f"premise: standing at the far end re-orders the kept stops — v1 "
            f"{[st['poi_name'] for st in stops]} -> reply "
            f"{[st['poi_name'] for st in reply.json()['stops']]}"
        )

        # THE PHONE'S NEXT FETCH — the overlay path, where the stale file used to
        # come back. Every leg line either kept its own audio (pair unchanged) or
        # was rewritten and carries anything but the old file.
        fetched = client.get(f"/api/v1/trips/{trip_id}/session").json()
        assert fetched["plan_version"] == reply.json()["plan_version"]
        rewritten = 0
        for position, st in enumerate(fetched["stops"]):
            if position and st["leg_from_poi_id"] is not None and st["leg_narration"]:
                assert st["leg_from_poi_id"] == fetched["stops"][position - 1]["poi_id"], (
                    f"{st['poi_name']} is walked to from a stop its line was not written for"
                )
            if st["leg_narration"] != line_was.get(st["stop_id"]):
                rewritten += 1
                stale = f"file:///stale-{st['stop_id']}.mp3"
                assert st["leg_audio_url"] != stale, (
                    f"{st['poi_name']}: the overlay serves the OLD line's file under "
                    f"the NEW words — {st['leg_narration']!r}"
                )
        assert rewritten, "premise: a re-ordering replan rewrites at least one leg line"

        # The item is the source of truth: it carries the new words, and never the
        # stale file beside them.
        with live_neo4j.session() as s:
            rows = s.run(
                "MATCH (i:ItineraryItem) WHERE i.id IN $ids "
                "RETURN i.id AS id, i.leg_narration AS text, i.leg_audio_url AS url",
                ids=[st["stop_id"] for st in fetched["stops"] if st["stop_id"]],
            ).data()
        by_item = {st["stop_id"]: st for st in fetched["stops"]}
        for row in rows:
            served = by_item[row["id"]]
            assert row["text"] == served["leg_narration"], (
                f"the item and the wire disagree about {row['id']}'s leg words"
            )
            if served["leg_narration"] != line_was.get(row["id"]):
                assert row["url"] != f"file:///stale-{row['id']}.mp3"

    def test_compose_rebuilds_the_day_under_the_surface_it_was_planned_with(
        self, client, live_neo4j, monkeypatch
    ):
        """Phase 6 S6.1a — the route surface rides through compose. A take-it-easy
        day is planned step-free (design §2.4; plan S2.7: "never a route selected
        under one costing and reported under another"), and compose REBUILDS the
        persisted pick through `summarise_route` — which, measured 2026-08-19 (Phase
        6 W6.1), was handed no costing override: every leg of Rosemary's composed
        day was re-routed on the default surface (stairs allowed), and the composed
        clocks, legs and polylines the phone plays were not the day she was shown.
        (It composed at all only because `finalize_premium_tour` stamped the
        default hash — the sibling test in tests/test_tour_party.py.) Here: the
        router is asked, on EVERY leg compose routes, for the step-free costing, and
        the compose still lands (the fingerprint now names that identity). UNDO:
        drop the override from compose's `summarise_route` call -> the recorded
        overrides are all None -> RED."""
        from src.tour import routing_client as routing_client_module
        from src.tour.routing_client import ROUTE_SURFACE_COSTING_OVERRIDES

        gen = client.post(
            "/api/v1/trips/generate",
            json=_body(
                LENSED_PROFILE_ID,
                center_lat=48.859962,
                center_lng=2.326561,
                duration_min=180,
                round_trip=True,
                start_date="2026-08-19",
                end_date="2026-08-19",
                start_time="14:00",
                party="take_it_easy",
                lenses=["visual_art"],
                max_leg_minutes=13,
                # Phase 8 S8.4: same re-derivation as the promise-tier test above —
                # this test's concern is the ROUTE SURFACE through compose, so its
                # day must be one the serving gate serves (the thin echo variant is
                # the W8.6 disposition row, not this fixture's job).
                narration_density="more",
            ),
        )
        assert gen.status_code == 201, gen.text
        trip_id = gen.json()["trip_id"]
        seen: list[dict | None] = []
        real = routing_client_module.RoutingClient.route_with_receipt

        def spy(self, *args, costing_options_override=None, **kwargs):
            seen.append(costing_options_override)
            return real(self, *args, costing_options_override=costing_options_override, **kwargs)

        monkeypatch.setattr(routing_client_module.RoutingClient, "route_with_receipt", spy)
        composed = _compose(client, trip_id, _MarkerAuthoringExecutor())
        assert composed.status_code == 200, composed.text
        assert seen, "fixture premise: compose must route the rebuilt day's legs"
        expected = ROUTE_SURFACE_COSTING_OVERRIDES["step_free"]
        assert all(o == expected for o in seen), (
            f"compose routed {sum(o != expected for o in seen)} of {len(seen)} legs under a "
            f"costing other than the step-free one the day was planned with: {seen[:4]}"
        )


# ---------------------------------------------------------------------------
# S1.M4 — no leg line survives a walk it was not written for.
# ---------------------------------------------------------------------------


def _legged(poi_id: str, name: str, *, from_id: str | None, line: str | None) -> GeneratedStop:
    return GeneratedStop(
        sort_order=1,
        poi_id=poi_id,
        poi_name=name,
        lat=48.85,
        lng=2.35,
        duration_min=10,
        importance_tier=4,
        start_time="10:00",
        leg_narration=line,
        leg_from_poi_id=from_id,
        leg_audio_url=f"https://audio.example/{poi_id}-leg.mp3" if line else None,
        leg_audio_duration_sec=9.0 if line else None,
    )


def _replan_route(pois: list[tuple[str, str]]):
    from src.tour.contract import POI, Route, TransitSegment

    nodes = tuple(
        POI(id=pid, name=name, tier=4, poi_role="stop", lat=48.85, lng=2.35)
        for pid, name in pois
    )
    transits = tuple(
        TransitSegment(
            from_poi_id=None if i == 0 else nodes[i - 1].id,
            to_poi_id=p.id,
            distance_m=300.0,
            walk_seconds=240,
        )
        for i, p in enumerate(nodes)
    )
    return Route(
        pois=nodes,
        transits=transits,
        total_walk_distance_m=300.0 * len(nodes),
        total_walk_seconds=240 * len(nodes),
        spine_area="Les Halles",
        target_dwell_seconds=1800,
        err_short_total_seconds=1800,
    )


def test_a_replan_drops_and_rewrites_a_leg_line_whose_walk_no_longer_happens():
    """Sofia's day re-cuts around the dark and the Bourse now comes before the Tour
    Saint-Jacques. The line written for the walk from Saint-Eustache described nine
    minutes from a place she is no longer coming from, and the phone plays a leg file
    against whichever stop is currently before it — so the old words must not survive.

    The replacement names the new pair and the new routed length, and its audio fields
    are cleared so the voicing pass re-voices it rather than replaying the old file.
    """
    stops = [
        _legged("bourse", "Bourse de Commerce", from_id="eustache", line=None),
        _legged(
            "saint-jacques",
            "Tour Saint-Jacques",
            from_id="eustache",
            line="From Saint-Eustache, make your way on to Tour Saint-Jacques, "
            "about a nine-minute walk away.",
        ),
    ]
    out = _releg_kept_stops(
        stops,
        _replan_route([("bourse", "Bourse de Commerce"), ("saint-jacques", "Tour Saint-Jacques")]),
        walked_in_from=None,
    )
    stale = out[1]
    assert "Saint-Eustache" not in (stale.leg_narration or "")
    assert stale.leg_from_poi_id == "bourse"
    assert "Bourse de Commerce" in stale.leg_narration
    assert "Tour Saint-Jacques" in stale.leg_narration
    assert "4-minute" in stale.leg_narration, "the routed length of the walk she takes"
    assert stale.leg_audio_url is None and stale.leg_audio_duration_sec is None


def test_a_replan_leaves_a_line_and_its_audio_alone_when_the_pair_still_holds():
    """A correct line is never re-voiced: the audio was already paid for and the words
    are still true, so nothing about that leg moves."""
    line = "From the Bourse de Commerce, make your way on to Tour Saint-Jacques."
    stops = [
        _legged("bourse", "Bourse de Commerce", from_id=None, line=None),
        _legged("saint-jacques", "Tour Saint-Jacques", from_id="bourse", line=line),
    ]
    out = _releg_kept_stops(
        stops,
        _replan_route([("bourse", "Bourse de Commerce"), ("saint-jacques", "Tour Saint-Jacques")]),
        walked_in_from=None,
    )
    assert out[1].leg_narration == line
    assert out[1].leg_audio_url == "https://audio.example/saint-jacques-leg.mp3"


def test_the_leg_under_way_is_judged_against_the_stop_the_walker_has_already_left():
    """The first stop still ahead has no predecessor inside the replanned tail, but the
    walker is mid-walk toward it from the stop they just left. That walk is really
    happening, so its line stands."""
    line = "From the Bourse de Commerce, make your way on to Tour Saint-Jacques."
    ahead = [_legged("saint-jacques", "Tour Saint-Jacques", from_id="bourse", line=line)]
    out = _releg_kept_stops(
        ahead,
        _replan_route([("saint-jacques", "Tour Saint-Jacques")]),
        walked_in_from=_legged("bourse", "Bourse de Commerce", from_id=None, line=None),
    )
    assert out[0].leg_narration == line, "the walk under way is not re-written"
    assert out[0].leg_audio_url is not None


def test_a_line_with_no_recorded_pair_is_left_alone_rather_than_assumed_stale():
    """An item written before the leg carried its provenance says nothing about which
    walk it describes. Unknown is not known-stale: rewriting them all would re-voice
    every leg of every older day for a fault that may not be there."""
    line = "From somewhere, make your way on."
    stops = [
        _legged("bourse", "Bourse de Commerce", from_id=None, line=None),
        _legged("saint-jacques", "Tour Saint-Jacques", from_id=None, line=line),
    ]
    out = _releg_kept_stops(
        stops,
        _replan_route([("bourse", "Bourse de Commerce"), ("saint-jacques", "Tour Saint-Jacques")]),
        walked_in_from=None,
    )
    assert out[1].leg_narration == line
    assert out[1].leg_audio_url is not None
