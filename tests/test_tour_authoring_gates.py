"""AC-5 — the anti-hallucination gates on the PERSISTED path (`POST /trips/{id}/compose`).

The cutover (step A3) swapped that endpoint's engine to the per-stop authoring seam.
The seam's finalizer was built for the CERTIFICATION replay, which deliberately runs a
structural-only validator, the trusting offline entailment stub, and no coverage
baseline at all.  Moving the persisted endpoint onto it therefore silently dropped
three gates the whole-tour composer had wired for it (ledger decision D3):

* the REAL faithfulness checker (``get_faithfulness_checker`` -> Haiku entailment),
* the coverage baseline (``claims_realized_by`` on the pre-compose stitch), so a bold
  fusion cannot silently DELETE a fact the stitch voiced,
* the full ``validate_script`` scan — the forbidden-phrase / invented-proper-noun /
  invented-year check over glue, which structural traceability cannot see.

Parity with the pre-cutover endpoint ONLY: no new checks, and the certification replay
keeps its own (unchanged) defaults, which is why the three arrive as injectable
parameters rather than as a new default.

``test_unfaithful_output_is_refused_and_trip_untouched`` is the ledger's pinned gate for
this step, so every clause lives inside that one node id: unwiring ANY of the three in
``src/tour/authoring.py`` or ``src/api/routes/trips.py`` must turn it red.

The endpoint is driven for real (live Paris dev graph, a disposable profile and trip)
because the claim under test is not "the finalizer can refuse" but "the PERSISTED path
refuses AND writes nothing" — a gate that runs after the write would look identical at
the seam and lose the trip.  Every provider boundary is a $0 injected double.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import threading

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.auth.tokens import create_access_token
from src.api.dependencies import get_driver, get_session
from src.tour.authoring import COMPOSE_MODEL
from src.tour.certification_provider import PhysicalProviderResponse
from src.tour.contract import (
    POI,
    BeatRef,
    BeatSequence,
    POIBeats,
    Route,
    Script,
    ScriptPOI,
    Sentence,
    TourInput,
    TransitSegment,
    ValidationReport,
)
from src.tour.generation import GLUE_REFLECTION
from src.tour.premium_tour import (
    EphemeralReceiptSink,
    execute_premium_plan,
    finalize_premium_composition,
    finalize_premium_tour,
    plan_premium_authoring,
)
from tests.conftest import needs_neo4j
from tests.live_graph import open_dev_driver

# Disposable identities of this module's own (never shared with another test module,
# so a crash here cannot strand another module's fixtures).
PROFILE_ID = "authoring-gates-test-profile"
TEST_USER_ID = "authoring-gates-test-user"
TEST_USER_EMAIL = "authoring-gates@example.test"

# Same known-GREEN multi-stop start as tests/test_trip_api.py (the Île walk).
ILE_START = (48.8568, 2.3414)
ILE_DURATION_MIN = 90

#: A proper noun and a year that appear NOWHERE in the Paris corpus, so a glue
#: sentence carrying them is unambiguously runtime invention.
INVENTED_NAME = "Zarnovia"
INVENTED_YEAR = "1043"

#: What the claim-blurring executor writes over every beat sentence: grammatical,
#: traceable, and sharing no content tokens with any key_claim — so the ONLY gate
#: that can catch it is the coverage baseline.
BLURRED_TEXT = "That much is written down."


def _delete_test_artifacts(driver) -> None:
    with driver.session() as s:
        s.run(
            "MATCH (p:Profile {id: $pid}) "
            "OPTIONAL MATCH (p)-[:IS_CAPTAIN_OF]->(t:Trip) "
            "OPTIONAL MATCH (t)-[:HAS_STOP]->(i:ItineraryItem) "
            "DETACH DELETE t, i",
            pid=PROFILE_ID,
        )
        s.run("MATCH (p:Profile {id: $pid}) DETACH DELETE p", pid=PROFILE_ID)
        s.run("MATCH (u:User {id: $uid}) DETACH DELETE u", uid=TEST_USER_ID)


@pytest.fixture(scope="module")
def live_neo4j():
    driver = open_dev_driver()
    if driver is None:
        pytest.skip(
            "Local Paris dev Neo4j unreachable. This module writes disposable records "
            "and refuses anything but localhost:7687. Run it through "
            "`make test-file FILE=tests/test_tour_authoring_gates.py`."
        )
    yield driver
    driver.close()


@pytest.fixture(scope="module")
def test_profile(live_neo4j):
    _delete_test_artifacts(live_neo4j)  # residue from any crashed prior run
    with live_neo4j.session() as s:
        s.run(
            "MERGE (u:User {id: $uid}) SET u.email = $email",
            uid=TEST_USER_ID,
            email=TEST_USER_EMAIL,
        )
        s.run(
            "MERGE (p:Profile {id: $pid}) SET p.display_name = 'Authoring Gates Test' "
            "WITH p MATCH (u:User {id: $uid}) MERGE (u)-[:HAS_PROFILE]->(p)",
            pid=PROFILE_ID,
            uid=TEST_USER_ID,
        )
    yield
    _delete_test_artifacts(live_neo4j)


@pytest.fixture(scope="module")
def client(live_neo4j, test_profile):
    """TestClient whose request sessions AND engine corpus driver hit the live graph."""
    app = create_app()

    def _live_session():
        with live_neo4j.session() as s:
            yield s

    app.dependency_overrides[get_session] = _live_session
    app.dependency_overrides[get_driver] = lambda: live_neo4j
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {create_access_token(TEST_USER_ID, TEST_USER_EMAIL)}"
        yield c


@pytest.fixture()
def fresh_trip(client):
    """One freshly generated, not-yet-composed trip on the live corpus.

    The glue client is the offline double here, bound once in ``tests/conftest.py``
    (the same door the $0 audio seam uses) rather than per-module. Without it this
    fixture builds a real paid Anthropic client — ``src/tour/generation.py`` is
    real-by-default now and ``POST /trips/generate`` has no injection seam — and
    both tests in this module error at setup instead of running.
    """
    resp = client.post(
        "/api/v1/trips/generate",
        json={
            "profile_id": PROFILE_ID,
            "center_lat": ILE_START[0],
            "center_lng": ILE_START[1],
            "duration_min": ILE_DURATION_MIN,
            "round_trip": False,
            "start_date": "2026-06-12",
            "end_date": "2026-06-13",
            "start_time": "09:00",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# $0 provider doubles — one per gate, each defective in exactly ONE way
# ---------------------------------------------------------------------------


def _offline_response(
    unit, sentences: list[dict], *, extra: dict | None = None
) -> PhysicalProviderResponse:
    """``extra`` (Phase 6 S6.5): the whole payload when a test needs more than
    ``sentences`` — e.g. the writer's ``threads``; default byte-identical to before."""
    body = json.dumps(
        extra if extra is not None else {"sentences": sentences},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return PhysicalProviderResponse(
        body=body,
        input_tokens=0,
        output_tokens=0,
        latency_ms=0,
        model=COMPOSE_MODEL,
        provider_request_id=f"offline-{unit.stop_index}",
        stop_reason="end_turn",
    )


def _stitched(unit) -> list[dict]:
    return [s.model_dump(mode="json") for s in unit.authorized_request.stitched.script]


class _RewritingExecutor:
    """Rewrites every beat sentence so it is no longer VERBATIM corpus text.

    That matters: ``verify_faithfulness`` short-circuits a sentence found verbatim in
    its beat's ``script_body`` (the corpus is canonical), so an echo-the-stitch double
    would never reach the entailment checker at all and could not tell a wired checker
    from an unwired one. The original text is kept INSIDE the rewrite, so the coverage
    baseline still sees every claim realized — faithfulness is the only gate that can
    fire.
    """

    cost_bearing = False
    provider_name = "offline"

    def execute(self, unit) -> PhysicalProviderResponse:
        sentences = []
        for sentence in _stitched(unit):
            if sentence["source_type"] == "beat":
                # "As it happens" and not "Here is the thing": a floor-neutral
                # prefix, because a vignette one-liner is LEG-placed and the S8.3
                # deixis floor rightly refuses "Here …" on a leg — this double's
                # job is to trip ONLY the entailment checker.
                sentence = {**sentence, "text": f"As it happens, {sentence['text']}"}
            sentences.append(sentence)
        return _offline_response(unit, sentences)


class _ClaimBlurringExecutor:
    """Deletes the FACTS while keeping the structure: every beat sentence is replaced
    by one grounded-looking, claim-free line.

    Citations, sentence counts and stop indices are untouched, so traceability, the
    glue scan and (under the trusting offline checker) faithfulness all pass. The only
    thing wrong with this tour is that the facts the stitch voiced are gone — which is
    exactly and only what the coverage baseline catches.
    """

    cost_bearing = False
    provider_name = "offline"

    def execute(self, unit) -> PhysicalProviderResponse:
        sentences = []
        for sentence in _stitched(unit):
            if sentence["source_type"] == "beat":
                sentence = {**sentence, "text": BLURRED_TEXT}
            sentences.append(sentence)
        return _offline_response(unit, sentences)


class _InventingGlueExecutor:
    """Adds a proper noun and a year no cited beat carries to a GLUE sentence.

    Glue keeps its own traceable ``source_id`` and every beat sentence is echoed back
    byte-identically, so traceability, faithfulness and coverage are all satisfied:
    only ``validate_script``'s forbidden-phrase / invented-noun / invented-year scan
    can see this. Reflection glue is left alone — it is fail-closed under a DIFFERENT
    gate (entailment), which would muddy the proof.
    """

    cost_bearing = False
    provider_name = "offline"

    def __init__(self) -> None:
        self.stops_invented: list[int] = []
        self._lock = threading.Lock()

    def execute(self, unit) -> PhysicalProviderResponse:
        sentences = []
        invented = False
        for sentence in _stitched(unit):
            if (
                not invented
                and sentence["source_type"] != "beat"
                and sentence["source_id"] != GLUE_REFLECTION
            ):
                sentence = {
                    **sentence,
                    "text": (
                        f"{sentence['text']} The court had moved to "
                        f"{INVENTED_NAME} by {INVENTED_YEAR}."
                    ),
                }
                invented = True
            sentences.append(sentence)
        if invented:
            with self._lock:
                self.stops_invented.append(unit.stop_index)
        return _offline_response(unit, sentences)


class _RejectingChecker:
    """A faithfulness checker that entails NOTHING, and records that it was asked.

    ``verify_faithfulness`` fans the entailment calls out across a thread pool, so the
    call log is locked.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], str]] = []
        self._lock = threading.Lock()

    def entails(self, key_claims: tuple[str, ...], sentence_text: str) -> bool:
        with self._lock:
            self.calls.append((key_claims, sentence_text))
        return False


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _override_dep(client, dep: str, value):
    from src.api import dependencies

    target = getattr(dependencies, dep)
    client.app.dependency_overrides[target] = lambda: value
    return target


def _clear_dep(client, target) -> None:
    client.app.dependency_overrides.pop(target, None)


def _persisted(live_neo4j, trip_id: str) -> dict:
    """Every property of every persisted stop, plus the composed marker.

    ``properties(i)`` rather than a hand-listed projection: "byte-unchanged" has to
    mean the whole node, or a gate that rewrote a field this test forgot to name
    would still look clean.
    """
    with live_neo4j.session() as s:
        stops = s.run(
            "MATCH (t:Trip {id: $tid})-[:HAS_STOP]->(i:ItineraryItem) "
            "RETURN properties(i) AS props ORDER BY i.sort_order",
            tid=trip_id,
        ).data()
        composed_route_id = s.run(
            "MATCH (t:Trip {id: $tid}) RETURN t.composed_route_id AS rid", tid=trip_id
        ).single()["rid"]
    return {"stops": [row["props"] for row in stops], "composed_route_id": composed_route_id}


def _compose(client, trip_id: str):
    return client.post(f"/api/v1/trips/{trip_id}/compose", json={"route_id": f"{trip_id}-opt1"})


def _refusal_detail(resp, caplog) -> dict:
    """The 422 body, having first established the refusal came from the GATE.

    The route turns a seam ``ValueError`` (an unauthorable shape, an unparseable
    payload) into a byte-identical 422 — same reason, same zeroed counts — and logs it.
    Without this check a coverage assertion of the form "422 with every reported count
    at zero" would be satisfied by a crash that never ran a gate at all.
    """
    assert resp.status_code == 422, resp.text
    # RE-DERIVED 2026-08-19 (Phase 6 W6.1): the VERIFY branch now NAMES itself in the
    # log ("Compose refused by VERIFY …" plus one line per blocking sentence — the
    # author path's precedent), so the two branches are told apart POSITIVELY rather
    # than by the absence of any error record, which was the only signal before.
    messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert not any(m.startswith("Per-stop authoring could not run") for m in messages), (
        "the endpoint logged the seam's ValueError branch, so this 422 is not a VERIFY "
        f"refusal: {messages}"
    )
    assert any(m.startswith("Compose refused by VERIFY") for m in messages), (
        f"a VERIFY refusal names itself in the server log; saw: {messages}"
    )
    detail = resp.json()["detail"]
    assert detail["reason"] == "compose_verification_failed"
    # Phase 8 S8.4: one bounded targeted re-roll of the failing stops precedes a
    # VERIFY refusal; these doubles fail every roll, so the refusal stands at an
    # honestly-counted attempts=2 (the phone reads detail['attempts']).
    assert detail["attempts"] == 2, "the phone reads detail['attempts']"
    return detail


@needs_neo4j
def test_invented_glue_is_refused_and_the_trip_is_untouched(client, live_neo4j, fresh_trip, caplog):
    """AC-5 end to end — INVENTION still BLOCKS, and blocks before any write.

    Glue carrying a proper noun and a year that no cited beat has is runtime invention
    that structural traceability cannot see, and this is the check that catches it. It
    remains blocking after the 2026-08-03 demotion because it NAMES its subject exactly:
    a specific token, absent from a specific source set. That is the standing rule —
    a check may block only when it can name the specific thing that is wrong.

    Afterwards the trip's stops in Neo4j are byte-unchanged and the trip is still
    uncomposed, so the client can offer another flavour (decision D2). A gate that ran
    after the write would look identical at the seam and lose the trip.

    This was one clause of ``test_unfaithful_output_is_refused_and_trip_untouched``. It
    is now its own node id because its two siblings became ADVISORY and therefore
    COMPOSE — which marks the trip composed and would 409 every later clause sharing the
    fixture.
    """
    trip_id = fresh_trip["trip_id"]
    before = _persisted(live_neo4j, trip_id)
    assert before["composed_route_id"] is None
    assert len(before["stops"]) > 1, "a one-stop trip cannot exercise a per-stop gate"

    inventor = _InventingGlueExecutor()
    executor_target = _override_dep(client, "get_premium_compose_executor", inventor)
    try:
        with caplog.at_level(logging.ERROR, logger="ondoway.api"):
            resp = _compose(client, trip_id)
        detail = _refusal_detail(resp, caplog)
        assert inventor.stops_invented, (
            "no stop of this stitch carries a non-reflection glue sentence, so the "
            "invention clause never ran — re-pick the fixture tour"
        )
        assert detail["forbidden"] > 0, (
            "invented glue survived: the persisted path is still running "
            f"structural traceability instead of the full validate_script scan: {detail}"
        )
        assert detail["untraceable"] == 0, detail
        assert _persisted(live_neo4j, trip_id) == before
    finally:
        _clear_dep(client, executor_target)


@needs_neo4j
def test_the_endpoint_consults_the_real_checker_even_though_entailment_only_advises(
    client, live_neo4j, fresh_trip, caplog
):
    """The endpoint must WIRE the real faithfulness checker, whether or not it blocks.

    Entailment became advisory on 2026-08-03 (it fired on ~a fifth of a good tour), so a
    REFUSAL can no longer be used as the proof that the checker is wired — which is what
    the previous version of this test relied on. The durable proof is the checker's own
    call log: a checker that is built-but-ignored fails this, and so does one the
    endpoint never asks for. A demotion to advisory must not quietly hide that
    distinction.

    The tour now COMPOSES despite entailing nothing, and that is the intended behaviour:
    the failures are recorded on the report and logged per sentence by the route, so a
    human sees strictly more than the old bare count.
    """
    trip_id = fresh_trip["trip_id"]
    before = _persisted(live_neo4j, trip_id)
    assert before["composed_route_id"] is None

    checker = _RejectingChecker()
    checker_target = _override_dep(client, "get_faithfulness_checker", checker)
    executor_target = _override_dep(client, "get_premium_compose_executor", _RewritingExecutor())
    try:
        with caplog.at_level(logging.ERROR, logger="ondoway.api"):
            resp = _compose(client, trip_id)
        assert checker.calls, (
            "the faithfulness checker the endpoint depends on was never consulted — "
            "the per-stop finalizer is still verifying with the trusting offline stub"
        )
        assert resp.status_code == 200, (
            "a tour that entails NOTHING was refused: entailment is ADVISORY since "
            f"2026-08-03 and must not gate serving. {resp.text[:400]}"
        )
    finally:
        _clear_dep(client, checker_target)
        _clear_dep(client, executor_target)


@needs_neo4j
def test_a_tour_with_deleted_facts_composes_because_coverage_only_advises(
    client, live_neo4j, fresh_trip, caplog
):
    """Dropped-fact detection is ADVISORY: it counts shared WORDS, so it cannot block.

    ``verify_claim_coverage`` reports a claim as deleted when no composed sentence
    shares enough tokens with it. MEASURED 2026-08-03 on a real Paris tour: it reported
    "Square named for Henri IV" as dropped from a tour that says precisely that in
    different words. A paraphrase is indistinguishable from a deletion under word
    counting, so this check can only ever say "I don't know" and therefore RECORDS.

    It is replaced, not abandoned: the scored fact-check runs the same judge in reverse
    ("is this source claim still present?"), which distinguishes a reworded fact from a
    missing one. See the run-context addendum.

    RE-DERIVED at Phase 8 S8.4 (design §7.2): a claim-blurred day now DOES refuse —
    but on the QUALITY FLOOR, never on coverage. Every beat sentence collapsed to
    "That much is written down." is a rich corpus STARVED to a line (C1) and a
    silent walk (C3) — the exact defects the rubric names — so the refusal must be
    ``tour_quality_blocked`` with those checks, while the VERIFY layer's coverage
    stays advisory (the old 200-assertion's whole point, now proven by the refusal
    REASON rather than by serving a starved day)."""
    trip_id = fresh_trip["trip_id"]
    executor_target = _override_dep(
        client, "get_premium_compose_executor", _ClaimBlurringExecutor()
    )
    try:
        with caplog.at_level(logging.ERROR, logger="ondoway.api"):
            resp = _compose(client, trip_id)
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["reason"] == "tour_quality_blocked", (
            "a blurred tour must die on the QUALITY FLOOR; coverage stays advisory "
            f"and compose_verification_failed would mean it blocked at VERIFY: {detail}"
        )
        checks = {b["check"] for b in detail["blockers"]}
        assert checks & {"C1-starved", "C3-thin"}, detail
        assert not any(m.startswith("Compose refused by VERIFY") for m in
                       (r.getMessage() for r in caplog.records)), (
            "the VERIFY layer refused the blur — coverage stopped advising"
        )
    finally:
        _clear_dep(client, executor_target)


@needs_neo4j
def test_a_faithful_tour_still_composes(client, live_neo4j, fresh_trip):
    """The gates are not a blanket refusal: the echo-the-stitch tour still passes.

    Without this, wiring the three gates to reject EVERYTHING would satisfy the test
    above. It also pins the parity claim from the other side — the stitch a persisted
    trip is built from clears the full scan, the coverage baseline and entailment.
    """
    trip_id = fresh_trip["trip_id"]

    class _EchoExecutor:
        cost_bearing = False
        provider_name = "offline"

        def execute(self, unit) -> PhysicalProviderResponse:
            return _offline_response(unit, _stitched(unit))

    target = _override_dep(client, "get_premium_compose_executor", _EchoExecutor())
    try:
        resp = _compose(client, trip_id)
    finally:
        _clear_dep(client, target)
    assert resp.status_code == 200, resp.text
    # The frozen trip is deleted (design §8.2, Phase 5 S5.8): nothing writes
    # `composed_route_id` any more — a compose is version 1 of the living session.
    # (This line pinned the deleted writer and was red from S5.8 until Phase 6 D6.0
    # re-derived it; audit C had not classified this file.)
    assert resp.json()["plan_version"] == 1
    assert _persisted(live_neo4j, trip_id)["composed_route_id"] is None


def test_the_certification_replay_keeps_its_own_gate_defaults() -> None:
    """Parity is opt-IN: the certification replay's defaults are untouched (D3).

    The certification path judges prose by MEANING (its structural-only validator is
    deliberate, not an oversight), so the three gates arrive as parameters that default
    to today's behaviour. A change that flipped the defaults instead would silently
    re-gate every certification replay and every Premium build.
    """
    import inspect

    from src.tour.authoring import finalize_certification_composition

    finalize = inspect.signature(finalize_certification_composition).parameters
    for name in ("faithfulness_checker", "enforce_claim_coverage", "scan_glue_for_invention"):
        assert name in finalize, f"the per-stop finalizer has no injectable {name}"
    assert finalize["faithfulness_checker"].default is None
    assert finalize["enforce_claim_coverage"].default is False
    assert finalize["scan_glue_for_invention"].default is False

    seam = inspect.signature(finalize_premium_composition).parameters
    for name in ("faithfulness_checker", "enforce_claim_coverage", "scan_glue_for_invention"):
        assert name in seam, f"the Block-2 finalizer cannot pass {name} through"

    # Phase 8 S8.3: the placement floors are the FOURTH knob, same contract —
    # OFF for the sealed certification replay, hard-coded ON for the live door.
    assert finalize["enforce_placement_floors"].default is False
    assert "enforce_placement_floors" in seam
    assert seam["enforce_placement_floors"].default is False
    from src.tour.premium_tour import finalize_live_composition as _live_gates
    from src.tour.premium_tour import finalize_premium_tour as _live_door

    assert "finalize_live_composition(" in inspect.getsource(_live_door)
    assert "enforce_placement_floors=True" in inspect.getsource(_live_gates), (
        "the live finalizer must hard-code the placement floors ON — an omitted "
        "knob is exactly how the workbench ran no gates for three days"
    )


# ---------------------------------------------------------------------------
# Phase 8 S8.3 — the writer's words match the geometry they play in
# (W8.2 R1/R2/R4/R5; phase7-ledger carry 1 + carry 8; W8.1(f) mechanisms
# (a) mangled beat id, (c) fusion crossing playback contexts).
# ---------------------------------------------------------------------------


def _words_match_geometry_fixture() -> tuple[Script, BeatSequence, Route]:
    """Three stops, one walk-past vignette on leg 1, a thread asked of stop 2.

    Floor-clean by construction: the only leg-placed sentence (the vignette
    one-liner) carries no arrived words, every stop piece is still, and every
    stop ends on its close — so a floors-ON finalize of the ECHO of this stitch
    passes, and each test's executor introduces exactly ONE defect.
    """
    pois = (
        POI(id="p0", name="Stop Zero", tier=3, poi_role="anchor", lat=48.850, lng=2.350),
        POI(id="p1", name="Stop One", tier=3, poi_role="anchor", lat=48.851, lng=2.351),
        POI(id="p2", name="Stop Two", tier=3, poi_role="anchor", lat=48.852, lng=2.352),
    )
    vignette_poi = POI(
        id="vp", name="The Fortress", tier=2, poi_role="stop", lat=48.8505, lng=2.3505
    )
    transits = tuple(
        TransitSegment(
            from_poi_id=None if i == 0 else pois[i - 1].id,
            to_poi_id=p.id,
            distance_m=100.0,
            walk_seconds=90,
            leg_seconds=100,
            leg_distance_m=110.0,
            source="valhalla",
        )
        for i, p in enumerate(pois)
    )
    route = Route(
        pois=pois,
        transits=transits,
        total_walk_distance_m=300.0,
        total_walk_seconds=300,
        vignettes={1: (vignette_poi,)},
    )
    alpha = BeatRef(
        id="story-alpha", poi_id="p0",
        script_body="Henri the Fourth paid for the first terrace himself.",
        key_claims=("henri the fourth paid for the first terrace",),
    )
    beta = BeatRef(
        id="story-beta", poi_id="p1",
        script_body="The workshops sold silk under the arcades.",
        key_claims=("the workshops sold silk under the arcades",),
    )
    gamma = BeatRef(
        id="story-gamma", poi_id="p1",
        script_body="A printing press ran in the corner house.",
        key_claims=("a printing press ran in the corner house",),
    )
    delta = BeatRef(
        id="story-delta", poi_id="p2",
        script_body="The stone for the whole terrace came from a quarry near Fontainebleau.",
        key_claims=("the stone came from a quarry near fontainebleau",),
    )
    vig = BeatRef(
        id="vig-fortress", poi_id="vp",
        script_body="The fortress kept the crown jewels for a century.",
        key_claims=("the fortress kept the crown jewels",),
    )
    sequence = BeatSequence(
        poi_beats=(
            POIBeats(poi_id="p0", poi_name="Stop Zero",
                     ordering_strategy="narrative_function", beats=(alpha,)),
            POIBeats(poi_id="p1", poi_name="Stop One",
                     ordering_strategy="narrative_function", beats=(beta, gamma)),
            POIBeats(poi_id="p2", poi_name="Stop Two",
                     ordering_strategy="narrative_function", beats=(delta,)),
        ),
        vignette_beats={1: (vig,)},
    )
    stitched = Script(
        city_slug="paris",
        generated_at="2026-08-23T00:00:00Z",
        inputs=TourInput(start=(48.850, 2.350), duration_min=45, city_slug="paris"),
        total_audio_seconds=30,
        total_walking_seconds=300,
        total_walk_distance_m=300,
        total_planned_seconds=2700,
        selected_pois=(
            ScriptPOI(id="p0", name="Stop Zero", tier=3, lat=48.850, lng=2.350,
                      beat_ids=("story-alpha",)),
            ScriptPOI(id="p1", name="Stop One", tier=3, lat=48.851, lng=2.351,
                      beat_ids=("story-beta", "story-gamma")),
            ScriptPOI(id="p2", name="Stop Two", tier=3, lat=48.852, lng=2.352,
                      beat_ids=("story-delta",)),
        ),
        lens_coverage={},
        script=(
            Sentence(text="Henri the Fourth paid for the first terrace himself.",
                     source_id="story-alpha", source_type="beat", stop_idx=0),
            Sentence(text="And that's Stop Zero.", source_id="GLUE_CLOSING",
                     source_type="glue", stop_idx=0),
            Sentence(text="The fortress kept the crown jewels for a century.",
                     source_id="vig-fortress", source_type="beat", stop_idx=1),
            Sentence(text="The workshops sold silk under the arcades.",
                     source_id="story-beta", source_type="beat", stop_idx=1),
            Sentence(text="A printing press ran in the corner house.",
                     source_id="story-gamma", source_type="beat", stop_idx=1),
            Sentence(text="And that's Stop One.", source_id="GLUE_CLOSING",
                     source_type="glue", stop_idx=1),
            Sentence(
                text="The stone for the whole terrace came from a quarry near Fontainebleau.",
                source_id="story-delta", source_type="beat", stop_idx=2),
            Sentence(text="And that brings our walk to a close.", source_id="GLUE_CLOSING",
                     source_type="glue", stop_idx=2),
        ),
        validation=ValidationReport(),
    )
    return stitched, sequence, route


class _MutatingEchoExecutor:
    """Echoes the stitch, applying one caller-supplied mutation to the payload."""

    cost_bearing = False
    provider_name = "offline"

    def __init__(self, mutate, extra_by_stop: dict[int, dict] | None = None) -> None:
        self._mutate = mutate
        self._extra_by_stop = extra_by_stop or {}

    def execute(self, unit) -> PhysicalProviderResponse:
        sentences = [self._mutate(dict(s), unit.stop_index) for s in _stitched(unit)]
        extra = self._extra_by_stop.get(unit.stop_index)
        payload = {"sentences": sentences, **extra} if extra else None
        return _offline_response(unit, sentences, extra=payload)


def _finalized_with(mutate, *, extra_by_stop=None, **gates):
    stitched, sequence, route = _words_match_geometry_fixture()
    plan = plan_premium_authoring(
        stitched, sequence, route,
        snapshot=None, snapshot_sha256="0" * 64,
        routing_version="offline-test", policy_version="offline-test",
    )
    executor = _MutatingEchoExecutor(mutate, extra_by_stop)
    responses = execute_premium_plan(
        plan, executor=executor, receipt_sink=EphemeralReceiptSink()
    )
    return finalize_premium_composition(plan, responses, **gates)


def test_a_beat_id_the_writer_mangled_by_one_character_is_corrected_not_refused():
    """W8.2 R4 (Camille): a cited id at edit distance 1 from exactly ONE id the
    request supplied is a copy error, corrected deterministically — the day
    composes and the citation is the TRUE id (so entailment still gates it).
    W7.14's live instance: the writer cited `1311cfd-…` for `1311cf7d-…` and the
    day died 422, three attempts. UNDO: drop the correction in
    ``_sentences_from_json`` -> the refusal returns -> RED."""

    def mangle_source(sentence: dict, stop_index: int) -> dict:
        if sentence["source_id"] == "story-beta":
            return {**sentence, "source_id": "story-betaa"}  # one inserted character
        return sentence

    composition = _finalized_with(mangle_source)
    corrected = [s for s in composition.script.script if s.text.startswith("The workshops")]
    assert corrected and corrected[0].source_id == "story-beta", corrected

    def mangle_also_cites(sentence: dict, stop_index: int) -> dict:
        if sentence["source_id"] == "story-beta":
            return {**sentence, "also_cites": ["story-gamm"]}  # one deleted character
        return sentence

    composition = _finalized_with(mangle_also_cites)
    fused = [s for s in composition.script.script if s.text.startswith("The workshops")]
    assert fused and fused[0].also_cites == ("story-gamma",), fused


def test_a_mangled_id_with_no_close_neighbour_still_refuses():
    """Fail-closed stays: an id at edit distance 2+ from everything the request
    supplied is not a copy error — it is an invention, and traceability blocks."""
    from src.tour.compose_gate import ComposeVerificationError

    def mangle_far(sentence: dict, stop_index: int) -> dict:
        if sentence["source_id"] == "story-beta":
            return {**sentence, "source_id": "story-bxtaa"}
        return sentence

    with pytest.raises(ComposeVerificationError) as excinfo:
        _finalized_with(mangle_far)
    assert excinfo.value.report.untraceable_sentences


def test_the_correction_never_guesses_between_two_close_ids():
    """Deterministic means UNIQUE: a mangle at edit distance 1 from TWO known ids
    is left alone (and traceability refuses it), never resolved by guessing."""
    from src.tour.authoring import _corrected_citation

    known = frozenset({"story-beta", "story-gamma"})
    assert _corrected_citation("story-betaa", known) == "story-beta"
    assert _corrected_citation("story-gamm", known) == "story-gamma"
    assert _corrected_citation("story-beta", known) == "story-beta"  # already known
    two_close = frozenset({"beat-a", "beat-b"})
    assert _corrected_citation("beat-c", two_close) == "beat-c"  # ambiguous: untouched
    assert _corrected_citation("beat-xy", two_close) == "beat-xy"  # distance 2: untouched


def test_a_glue_sentence_the_writer_mistyped_as_beat_is_coerced_not_refused():
    """The R4 copy-error doctrine, extended to the TYPE field (live 2026-08-29,
    v3 batch, nyc-lower-manhattan-90): the writer emitted its requested
    reflection with ``source_id: GLUE_REFLECTION`` but ``source_type: "beat"``
    — the id is authoritative (ids are copied from the prompt; no beat carries
    a glue id), so the mistyped label is corrected deterministically and every
    downstream gate still judges the sentence. A "beat"-typed sentence whose id
    is NOT an authorized derived id stays refused — fail-closed is untouched.
    UNDO: drop the coercion in ``_sentences_from_json`` -> the 1-untraceable
    refusal returns -> RED."""
    from src.tour.compose_gate import ComposeVerificationError

    def mistype_close(sentence: dict, stop_index: int) -> dict:
        if sentence["source_id"] == "GLUE_CLOSING" and stop_index == 1:
            return {**sentence, "source_type": "beat"}
        return sentence

    composition = _finalized_with(mistype_close)
    closes = [
        s for s in composition.script.script
        if s.stop_idx == 1 and s.source_id == "GLUE_CLOSING"
    ]
    assert closes and closes[0].source_type == "glue", closes

    def invent_beat_id(sentence: dict, stop_index: int) -> dict:
        if sentence["source_id"] == "story-beta":
            return {**sentence, "source_id": "GLUE_INVENTED_KIND"}
        return sentence

    with pytest.raises(ComposeVerificationError) as excinfo:
        _finalized_with(invent_beat_id)
    assert excinfo.value.report.untraceable_sentences


def test_a_fusion_across_playback_contexts_is_refused_by_name():
    """W8.1(f) mechanism (c), live on 2026-08-23 (trip 45819603, twice): the
    writer fused a stop STORY sentence with a walk-past VIGNETTE beat and the
    day died on a bare ValueError from ``partition_final_script`` — surfaced as
    `compose_verification_failed` with every count 0, the unnamed refusal R8
    forbids. With the floors ON, the SAME defect is a NAMED verification
    finding, so the bounded retry can tell the writer which sentence to unfuse.
    UNDO: drop the fused-context floor -> the bare ValueError returns -> RED."""
    from src.tour.compose_gate import ComposeVerificationError

    def fuse_across(sentence: dict, stop_index: int) -> dict:
        if sentence["source_id"] == "story-beta":
            return {**sentence, "also_cites": ["vig-fortress"]}
        return sentence

    with pytest.raises(ComposeVerificationError) as excinfo:
        _finalized_with(fuse_across, enforce_placement_floors=True)
    codes = [code for _, code in excinfo.value.report.forbidden_phrase_hits]
    assert "fused_across_playback_contexts" in codes, codes

    # The sealed certification replay is untouched: floors OFF composes this.
    composition = _finalized_with(fuse_across)
    assert composition.script.script


def test_floors_on_the_clean_echo_still_compose():
    """The floors are not a blanket refusal: the fixture's own echo passes them."""
    composition = _finalized_with(
        lambda sentence, stop_index: sentence, enforce_placement_floors=True
    )
    assert composition.script.script


def test_a_thread_that_repeats_its_stops_own_telling_is_dropped():
    """W8.2 R3 (11/11): one fact, one voice, per day — a thread that repeats the
    stop's own narration is dropped (Greta's coronation story+thread, Sofia's
    Templar bridge leg+thread). The drop rides the existing R5 channel: the day
    ships without the line, reported, never refused. UNDO: drop the
    near-verbatim check in ``_keep_threads`` -> the repeat ships -> RED."""
    echo = lambda sentence, stop_index: sentence  # noqa: E731

    repeat = {"threads": [{"from": "Stop Zero",
                           "text": "The stone for the terrace came from a quarry "
                                   "near Fontainebleau."}]}
    composition = _finalized_with(echo, extra_by_stop={2: repeat})
    assert 2 not in composition.threads_by_stop, composition.threads_by_stop

    honest = {"threads": [{"from": "Stop Zero",
                           "text": "The same king paid for both terraces."}]}
    composition = _finalized_with(echo, extra_by_stop={2: honest})
    assert composition.threads_by_stop.get(2) == {
        "Stop Zero": "The same king paid for both terraces."
    }


# ---------------------------------------------------------------------------
# AC-6/AC-8 — the cross-stop echo dedup port
# ---------------------------------------------------------------------------

#: A fact voiced verbatim at stop 0, then echoed again at stop 1 — the
#: Île-de-la-Cité three-source-book case ``claim_dedup.suppress_repeated_claims``
#: exists for, just two stops apart instead of two beats at one stop.
_DUP_TEXT = "The span was finished in the winter of 1400."
#: Stop 1's OTHER sentence, carrying a claim nothing else voices — so beat
#: "b1a" is never the SOLE carrier of anything and the dedup pass's "never
#: empty a beat" restore cannot put the echo back.
_NOVEL_TEXT = "Its stone came from a quarry near Fontainebleau."


def _cross_stop_echo_fixture() -> tuple[Script, BeatSequence, Route]:
    """A persisted-trip-shaped (route, sequence, stitched script) pair, pure and
    provider-free, modeled on ``_prebuilt`` in
    ``tests/test_tour_authoring_from_route.py`` — but stop 1 seats ONE beat with
    TWO sentences: an echo of stop 0's fact plus a sentence carrying a claim
    nothing else voices. Both sentences must belong to the SAME beat — the dedup
    pass's "never empty a beat" restore fires per BEAT ID, so if the echo and the
    novel sentence were split across two different one-sentence beats, dropping
    the echo would empty ITS beat and the restore would put it right back.
    """
    pois = (
        POI(id="p0", name="Stop 0", tier=3, poi_role="anchor", lat=48.850, lng=2.350),
        POI(id="p1", name="Stop 1", tier=3, poi_role="anchor", lat=48.851, lng=2.351),
    )
    transits = (
        TransitSegment(
            from_poi_id=None,
            to_poi_id="p0",
            distance_m=100.0,
            walk_seconds=90,
            leg_seconds=100,
            leg_distance_m=110.0,
            source="valhalla",
        ),
        TransitSegment(
            from_poi_id="p0",
            to_poi_id="p1",
            distance_m=100.0,
            walk_seconds=90,
            leg_seconds=100,
            leg_distance_m=110.0,
            source="valhalla",
        ),
    )
    route = Route(
        pois=pois,
        transits=transits,
        total_walk_distance_m=200.0,
        total_walk_seconds=200,
    )
    beat0 = BeatRef(
        id="b0", poi_id="p0", script_body=_DUP_TEXT, key_claims=("the span finished in 1400",)
    )
    beat1 = BeatRef(
        id="b1",
        poi_id="p1",
        script_body=f"{_DUP_TEXT} {_NOVEL_TEXT}",
        key_claims=(
            "the span finished in 1400",
            "its stone came from a quarry near fontainebleau",
        ),
    )
    sequence = BeatSequence(
        poi_beats=(
            POIBeats(
                poi_id="p0",
                poi_name="Stop 0",
                ordering_strategy="narrative_function",
                beats=(beat0,),
            ),
            POIBeats(
                poi_id="p1",
                poi_name="Stop 1",
                ordering_strategy="narrative_function",
                beats=(beat1,),
            ),
        )
    )
    stitched = Script(
        city_slug="paris",
        generated_at="2026-07-30T00:00:00Z",
        inputs=TourInput(start=(48.850, 2.350), duration_min=30, city_slug="paris"),
        total_audio_seconds=24,
        total_walking_seconds=200,
        total_walk_distance_m=200,
        total_planned_seconds=1800,
        selected_pois=(
            ScriptPOI(id="p0", name="Stop 0", tier=3, lat=48.850, lng=2.350, beat_ids=("b0",)),
            ScriptPOI(
                id="p1",
                name="Stop 1",
                tier=3,
                lat=48.851,
                lng=2.351,
                beat_ids=("b1",),
            ),
        ),
        lens_coverage={},
        script=(
            Sentence(text=_DUP_TEXT, source_id="b0", source_type="beat", stop_idx=0),
            # Phase 6 S6.4: the stitch closes EVERY stop (the fallback the one writer
            # rewrites); a persisted-trip-shaped fixture carries the same shape.
            Sentence(
                text="And that's Stop 0.", source_id="GLUE_CLOSING", source_type="glue", stop_idx=0
            ),
            Sentence(text=_DUP_TEXT, source_id="b1", source_type="beat", stop_idx=1),
            Sentence(text=_NOVEL_TEXT, source_id="b1", source_type="beat", stop_idx=1),
            Sentence(
                text="And that brings our walk to a close.",
                source_id="GLUE_CLOSING",
                source_type="glue",
                stop_idx=1,
            ),
        ),
        validation=ValidationReport(),
    )
    return stitched, sequence, route


class _EchoExecutor:
    """$0 offline executor that echoes each stop's stitched sentences back
    unchanged — so whatever cross-stop repeat the stitch carries survives
    into the assembled output UNLESS the finalizer itself suppresses it."""

    cost_bearing = False
    provider_name = "offline"

    def execute(self, unit) -> PhysicalProviderResponse:
        return _offline_response(unit, _stitched(unit))


def test_a_sentence_outside_the_authored_stops_is_refused_not_shipped_unattested() -> None:
    """No sentence may ship in composed.script without a CompositionTrace covering it.

    The traces are built by grouping the assembled stream on ``stop_idx``. A sentence
    whose ``stop_idx`` falls OUTSIDE the authored stop set is therefore in no group at
    all: it lands in ``composed.script`` — persisted, voiced to a tourist — attested by
    nothing, with every per-stop trace still internally consistent.

    That is reachable, not theoretical. ``_sentences_from_json`` coerces a BEAT
    sentence's ``stop_idx`` back to the stitch's true stop, but a glue or reflection
    line keeps whatever index the provider echoed, so a mis-echoed one migrates. The
    judge consult on this step named it the single most likely failure.

    UNDO: delete the ``attested != set(range(...))`` check in ``finish`` and this goes
    GREEN — which is exactly what a mutation run proved before this test existed.
    """
    stitched, sequence, route = _cross_stop_echo_fixture()
    plan = plan_premium_authoring(
        stitched,
        sequence,
        route,
        snapshot=None,
        snapshot_sha256="0" * 64,
        routing_version="offline-test",
        policy_version="offline-test",
    )

    class _StopMigratingExecutor:
        """Echoes the stitch, but re-stamps one GLUE line onto a stop nobody authored."""

        cost_bearing = False
        provider_name = "offline"

        def execute(self, unit) -> PhysicalProviderResponse:
            sentences = _stitched(unit)
            if unit.stop_index == 0:
                # A GLUE line, because `_sentences_from_json` coerces a BEAT
                # sentence's stop_idx back to the stitch's true stop — only a
                # non-beat line can carry a foreign index through parsing.
                sentences.append(
                    {
                        "text": "You are now somewhere this tour never goes.",
                        "source_id": GLUE_REFLECTION,
                        "source_type": "glue",
                        "stop_idx": 99,
                        "also_cites": [],
                    }
                )
            return _offline_response(unit, sentences)

    with pytest.raises(ValueError) as excinfo:
        responses = execute_premium_plan(
            plan, executor=_StopMigratingExecutor(), receipt_sink=EphemeralReceiptSink()
        )
        finalize_premium_composition(plan, responses)

    message = str(excinfo.value)
    assert "attested by no composition trace" in message or "subsequence" in message, (
        "a sentence stamped onto an unauthored stop must be refused by name; the "
        f"finalizer raised something else instead: {message!r}"
    )


def test_cross_stop_echo_is_suppressed() -> None:
    """AC-6/AC-8 — the pinned gate for this step.

    ``compose.py``'s whole-tour composer ran ``_dedup_composed`` (claim-repeat +
    exact-repeat + same-beat near-dup suppression) over the ASSEMBLED sentence
    stream before verifying it, so a fact voiced at one stop and echoed again at
    a LATER stop never shipped twice. The per-stop seam's shared finalizer,
    ``finalize_certification_composition``, assembles per-stop provider output
    into that identical stream but never called the pass — so the same cross-stop
    echo would ship on every authoring surface that finalizer serves.

    AC-6 requires the pass to sit on the SHARED finalizer. Two live surfaces end
    there, both through ``finalize_live_composition``: the persisted
    ``POST /trips/{id}/compose`` (through ``finalize_premium_tour``) and the
    place-recipe build (through ``finalize_place_recipe``). So the finalizer is
    DRIVEN twice here rather than argued from the fact that they share a callee:

    * through the fan-out both surfaces use — ``execute_premium_plan`` +
      ``finalize_premium_composition``;
    * with the responses handed to ``finalize_premium_composition`` directly,
      with no fan-out above it.

    Driving only the first would leave a port that moved the pass UP, out of the
    finalizer and into the fan-out or a route, fully green — which is exactly the
    fix AC-6 forbids. The direct clause is what pins the pass to the SHARED
    finalizer.

    The last block pins the composition trace, because suppression and
    attestation pull against each other — and an earlier version of this test got
    that relationship BACKWARDS, which is what produced the defect a judge consult
    later caught. It asserted ``trace.source_sentences == shipped`` and justified it
    as "FinalTourBlueprint requires a trace's cited source sentences to equal the
    stop's FINAL sentences". That is not what the blueprint requires. Read
    ``artifact.py:763-769``: it indexes INTO ``source_sentences`` using
    ``source_sentence_indexes`` and compares THAT to the final stop text. The model
    is built for a SUBSEQUENCE — ``source_sentence_indexes`` is constrained
    monotonic, unique and in-range, one per final sentence.

    So the honest shape, which the finalizer now produces and this test now pins, is:
    ``source_sentences`` = the RAW provider payload, ``source_sentence_indexes`` =
    which of them survived de-dup. Re-grouping the bundle post-dedup instead made the
    finalizer's fail-closed provenance guard a tautology (comparing the deduped
    stream against itself) AND recorded text the provider never sent under
    ``derivation="provider_response"``, while ``response_sha256`` still named the
    untouched body.
    """
    stitched, sequence, route = _cross_stop_echo_fixture()
    plan = plan_premium_authoring(
        stitched,
        sequence,
        route,
        snapshot=None,
        snapshot_sha256="0" * 64,
        routing_version="offline-test",
        policy_version="offline-test",
    )
    executor = _EchoExecutor()

    # --- through the fan-out (compose and the recipe build) ----------------
    phone_responses = execute_premium_plan(
        plan, executor=executor, receipt_sink=EphemeralReceiptSink()
    )
    script = finalize_premium_composition(plan, phone_responses).script

    all_texts = [s.text for s in script.script]
    assert all_texts.count(_DUP_TEXT) == 1, (
        f"the cross-stop echo was not suppressed: {_DUP_TEXT!r} appears "
        f"{all_texts.count(_DUP_TEXT)} times in {all_texts!r}"
    )
    assert _NOVEL_TEXT in all_texts, (
        "the dedup pass dropped stop 1's ONLY novel sentence, not just its echo "
        "of stop 0 — this would silently delete a fact, not suppress a repeat"
    )
    # The stop's BEAT sentences (Phase 6 S6.4: every stop also ends on its close, which
    # is glue and not part of the telling the dedup pass judges).
    stop0_texts = [s.text for s in script.script if s.stop_idx == 0 and s.source_type == "beat"]
    assert stop0_texts == [_DUP_TEXT], "stop 0's own (first) telling must survive untouched"

    # --- the finalizer alone, with no fan-out above it ---------------------
    premium_plan = plan
    responses = tuple(executor.execute(unit) for unit in premium_plan.units)
    composition = finalize_premium_composition(premium_plan, responses)

    direct_texts = [s.text for s in composition.script.script]
    assert direct_texts.count(_DUP_TEXT) == 1, (
        "the shared finalizer, driven without the fan-out, shipped the cross-stop "
        f"echo: {_DUP_TEXT!r} appears {direct_texts.count(_DUP_TEXT)} times in "
        f"{direct_texts!r} — the dedup pass is not on the SHARED choke point"
    )
    assert _NOVEL_TEXT in direct_texts, (
        "the shared finalizer dropped stop 1's only novel sentence — the dedup "
        "pass is deleting a fact, not suppressing a repeat"
    )
    assert direct_texts == all_texts, (
        "the finalizer gave different output with and without the fan-out on "
        f"identical input; AC-6 requires the same suppression on both "
        f"({direct_texts!r} vs {all_texts!r})"
    )

    # --- the trace still attests the provider response ----------------------
    traces = {trace.stop_index: trace for trace in composition.composition_trace}
    assert sorted(traces) == [0, 1], (
        "dedup emptied a stop out of the trace entirely; suppression must never "
        f"cost a stop its narration (traced stops: {sorted(traces)})"
    )
    raw_by_stop = {
        unit.stop_index: response
        for unit, response in zip(premium_plan.units, responses, strict=True)
    }
    for stop_index, trace in traces.items():
        assert trace.derivation == "provider_response"
        assert trace.response_sha256 == hashlib.sha256(raw_by_stop[stop_index].body).hexdigest(), (
            f"stop {stop_index}'s trace no longer hashes the raw provider body — "
            "the dedup rewrote the attestation of what the provider actually said"
        )
        shipped = tuple(s for s in composition.script.script if s.stop_idx == stop_index)
        # THE INVARIANT FinalTourBlueprint ACTUALLY ENFORCES (artifact.py:763-769):
        # it indexes INTO source_sentences with source_sentence_indexes and compares
        # THAT to the final stop text. So the trace must carry the RAW provider payload
        # plus the indexes of the sentences that survived de-dup — not the deduped text.
        #
        # An earlier version of this test asserted `source_sentences == shipped` and
        # justified it as "FinalTourBlueprint would reject this tour at build time".
        # That justification is false, and it forced the finalizer to rebind
        # composed_by_stop to the post-dedup grouping, which (a) made the fail-closed
        # provenance guard a tautology and (b) recorded text the provider never sent
        # under derivation="provider_response". A judge consult caught both.
        traced = tuple(trace.source_sentences[k] for k in trace.source_sentence_indexes)
        assert traced == shipped, (
            f"stop {stop_index}: indexing the trace's source by its surviving indexes "
            "must reproduce the shipped text exactly — this is the check "
            "FinalTourBlueprint runs at build time"
        )
        raw_texts = tuple(s["text"] for s in json.loads(raw_by_stop[stop_index].body)["sentences"])
        assert tuple(s.text for s in trace.source_sentences) == raw_texts, (
            f"stop {stop_index}'s trace must attest the RAW provider payload, not the "
            "post-dedup text: source_payload_sha256 is read as 'what the provider "
            "returned', and response_sha256 already names that same untouched body"
        )


def test_every_live_surface_runs_the_same_three_gates() -> None:
    """AC-20 — every surface a listener hears runs the same three anti-hallucination gates.

    The shared finalizer runs the three gates (entailment, invented proper nouns and
    years in glue, facts deleted by a bold fusion) only when its caller passes them in;
    at their defaults it verifies with ``MockFaithfulnessChecker``, the stub that
    approves every sentence. Two live surfaces reach it, both through
    ``finalize_live_composition``, which holds the gates: the persisted
    ``POST /trips/{id}/compose`` (through ``finalize_premium_tour``), which is also how
    the workbench hears a tour, and the place-recipe build (through
    ``finalize_place_recipe``).

    The finalizer is DRIVEN twice here on ONE fixture with the SAME doubles — once
    through the fan-out, once with the responses handed straight in — rather than argued
    from the fact that callers share it, because sharing a callee says nothing about the
    ARGUMENTS each caller passes to it.

    UNDO: give the checker parameter of ``finalize_premium_tour``,
    ``finalize_live_composition`` or ``finalize_place_recipe`` a default, so a caller can
    silently omit it -> RED.
    """
    from src.tour.place_recipes import finalize_place_recipe
    from src.tour.premium_tour import finalize_live_composition

    stitched, sequence, route = _cross_stop_echo_fixture()
    plan = plan_premium_authoring(
        stitched,
        sequence,
        route,
        snapshot=None,
        snapshot_sha256="0" * 64,
        routing_version="offline-test",
        policy_version="offline-test",
    )
    executor = _RewritingExecutor()

    # Entailment is ADVISORY, so neither call REFUSES here. What parity means is that
    # both CONSULT the same checker. See
    # ``test_faithfulness_and_dropped_facts_are_advisory_not_blocking`` for the demotion.

    # --- through the fan-out, as compose and the recipe build run it ---------
    fanned_checker = _RejectingChecker()
    fanned_responses = execute_premium_plan(
        plan, executor=executor, receipt_sink=EphemeralReceiptSink()
    )
    finalize_premium_composition(
        plan,
        fanned_responses,
        faithfulness_checker=fanned_checker,
        enforce_claim_coverage=True,
        scan_glue_for_invention=True,
    )
    assert fanned_checker.calls, "baseline broken: the fan-out path never consulted its checker"

    # --- the responses handed straight to the finalizer — must consult IDENTICALLY
    direct_plan = plan
    responses = tuple(executor.execute(unit) for unit in direct_plan.units)
    direct_checker = _RejectingChecker()
    finalize_premium_composition(
        direct_plan,
        responses,
        faithfulness_checker=direct_checker,
        enforce_claim_coverage=True,
        scan_glue_for_invention=True,
    )
    assert direct_checker.calls, (
        "the finalizer never consulted the injected faithfulness checker when handed "
        "responses directly — it is verifying with the trusting offline stub, so "
        "unchecked narration would read as checked"
    )
    assert len(direct_checker.calls) == len(fanned_checker.calls), (
        f"the two calls asked the checker a DIFFERENT number of questions "
        f"(fan-out {len(fanned_checker.calls)}, direct {len(direct_checker.calls)}) — "
        "same engine, same tour, so the entailment work must be identical"
    )

    # --- the LIVE wiring cannot silently skip them ----------------------------
    # A working parameter proves nothing if a live caller omits it. compose_trip calls
    # finalize_premium_tour, the recipe build calls finalize_place_recipe, and both end
    # in finalize_live_composition, so the checker must be REQUIRED at each of them — a
    # default would let a caller reach the shared finalizer with no entailment gate.
    for live_entry in (finalize_premium_tour, finalize_live_composition, finalize_place_recipe):
        checker_param = inspect.signature(live_entry).parameters["faithfulness_checker"]
        assert checker_param.default is inspect.Parameter.empty, (
            f"{live_entry.__name__} takes an OPTIONAL faithfulness checker, so a live "
            "surface can reach the shared finalizer with no entailment gate"
        )


def test_faithfulness_and_dropped_facts_are_advisory_not_blocking() -> None:
    """OWNER RULING 2026-08-03: the entailment and dropped-fact checks RECORD, they do
    not BLOCK. Structural checks still block.

    MEASURED that day on a real Paris tour, with the gates newly wired to the workbench:
    ``0 untraceable, 1 forbidden, 0 provenance, 28 faithfulness, 6 coverage`` — and of
    the 28, exactly ONE was a verified invention (two source books disagree, 1867 vs
    1870, and the model synthesised a reconciliation neither states). Sampled false
    alarms included

        "Look hard at the façade, because it's a manifesto of Art Nouveau."

    which is the corpus's own sentence with two words changed, and a dropped-fact report
    for "Square named for Henri IV" on a tour that says exactly that in different words
    (the coverage check counts shared WORDS, so a paraphrase reads as a deletion).

    A check that fires on ~a fifth of a good tour cannot decide whether to ship it. The
    rule the owner set: a check may BLOCK only when it can NAME the specific thing that
    is wrong; a check that can only say "I don't know" RECORDS. Both of these are in the
    second category until the scored replacement lands (see the run-context addendum:
    per-claim scores with an unsupported SPAN, bands not averages, repair before cut).

    They stay ON, stay on the report, and stay in the logs — this is a demotion from
    blocking to advisory, NOT a removal. ``faithfulness_checked`` still records whether
    the pass ran, so "checked and clean" is still distinguishable from "never checked".

    UNDO: put ``faithfulness_failures`` or ``coverage_failures`` back into ``passed``
    and this goes RED — which is the state that blocked every tour on 2026-08-03.
    """
    sentence = Sentence(
        text="Look hard at the façade, because it's a manifesto of Art Nouveau.",
        stop_idx=0,
        source_type="beat",
        source_id="beat-1",
    )

    advisory_only = ValidationReport(
        faithfulness_failures=((sentence, "unfaithful:beat-1"),),
        coverage_failures=(("beat-2", "Square named for Henri IV"),),
        faithfulness_checked=True,
    )
    assert advisory_only.passed, (
        "an entailment miss and a dropped-fact report BLOCKED the tour — these two "
        "over-fire (28 flags, 1 verified defect, on a real tour) and must only advise"
    )
    # The evidence is still carried, or the demotion would be a deletion.
    assert advisory_only.faithfulness_failures and advisory_only.coverage_failures
    assert advisory_only.faithfulness_checked

    # The structural checks are cheap, exact, and DO still block.
    for blocking in (
        ValidationReport(untraceable_sentences=(sentence,)),
        ValidationReport(forbidden_phrase_hits=((sentence, "banned"),)),
        ValidationReport(provenance_failures=(("beat-1", 0.12),)),
    ):
        assert not blocking.passed, (
            f"a structural failure stopped blocking: {blocking!r} — traceability, "
            "forbidden phrases and provenance name exactly what is wrong and must gate"
        )
