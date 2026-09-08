"""Phase 8 S8.5 — §7.4: the eleven days are replayable traces.

Design §7.4 makes the persona files executable: "Each persona file is a
minute-by-minute script, so each becomes a replayable GPS-and-behaviour trace
through the session loop", asserting five invariants. Design §11 puts them
off-device. This file is those five, one test each, driven through the LIVE
session endpoints by `tests/persona_traces.py` (the runner, which names what it
extends). Every test cites its source: a persona file and step, a section of
01-design.md, or a locked W8.2 ruling (§0.1.1, design §7.5).

Live corpus, $0: the days are planned on the local dev graph (port 7687, the
tests/test_trip_api.py precedent) and composed through the product's own
offline executor — the money guard in tests/conftest.py hands the compose path
`OfflinePremiumExecutor`, so no provider call is made anywhere in this file.
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
from src.tour.contract import END_B_SENTINEL_PREFIX
from src.tour.premium_tour import OfflinePremiumExecutor
from src.tour.verify import MockFaithfulnessChecker
from tests.conftest import needs_neo4j
from tests.live_graph import open_dev_driver
from tests.persona_traces import (
    DAYS,
    TRACE_PROFILE_ID,
    TRACE_USER_EMAIL,
    TRACE_USER_ID,
    Trace,
    build_trace,
    leg_minutes,
    resolved_cap_minutes,
    walk_the_day,
)

#: The eleven days are built ONCE in a module fixture (~5:20 on an idle machine,
#: slower when the DB shard's other workers share the CPU and Valhalla), and
#: pytest-timeout charges that whole setup to the FIRST test of the module. The
#: shard's 300 s hang-breaker therefore killed exactly this fixture on the first
#: parallel run. This file carries its own ceiling instead: wide enough for the
#: measured cost under contention, still finite against a genuine hang.
pytestmark = pytest.mark.timeout(900)

#: How many of the eleven days the product SERVES at $0 today. MEASURED on this
#: tree, 2026-08-24 (phase8-ledger.md §S8.5, the before-picture): camille (5
#: stops), greta (3) and rosemary (1); re-measured after P9R-S1, when paulo's
#: day began serving through the origin-verified transit pick, the drop valve
#: and the two reworded beats; re-measured again at Phase 10, when aiko's day
#: began serving — her compose was refused over one forbidden corpus walking
#: line, reworded at its source, and the hours work made her Tuesday real
#: (`test_aikos_rainy_tuesday_serves` is her own pin). The rest are refused by
#: S8.3's placement floors or the C3 audio floor — each named, each pinned by
#: `test_every_persona_day_either_serves_or_refuses_by_name`. A FLOOR, not an
#: equality: a day that starts serving is never a failure; a day that stops
#: serving is.
SERVED_FLOOR = 5

#: The wire speaks in whole minutes (`start_time` is HH:MM and `dwell_seconds`
#: is floored to minutes when a leg is read back), so a leg read off two clocks
#: can sit one minute above the leg the planner certified. One minute, stated
#: once, and never more: a cap breach the design cares about is Rosemary's
#: 12-and-9 fusing into 21.
CLOCK_ROUNDING_MINUTES = 1


def _delete_trace_artifacts(driver) -> None:
    with driver.session() as s:
        s.run(
            "MATCH (p:Profile {id: $pid}) "
            "OPTIONAL MATCH (p)-[:IS_CAPTAIN_OF]->(t:Trip) "
            "OPTIONAL MATCH (t)-[:HAS_STOP]->(i:ItineraryItem) "
            "DETACH DELETE t, i",
            pid=TRACE_PROFILE_ID,
        )
        s.run("MATCH (p:Profile {id: $pid}) DETACH DELETE p", pid=TRACE_PROFILE_ID)
        s.run("MATCH (u:User {id: $uid}) DETACH DELETE u", uid=TRACE_USER_ID)


@pytest.fixture(scope="module")
def live_neo4j():
    driver = open_dev_driver()
    if driver is None:
        pytest.skip(
            "The local Paris dev graph (localhost:7687) is unreachable. A persona "
            "trace walks the real corpus; run it through "
            "`make test-file FILE=tests/test_persona_traces.py`."
        )
    yield driver
    driver.close()


@pytest.fixture(scope="module")
def trace_identity(live_neo4j):
    """The disposable walker these traces are. Cleared before and after, so a
    crashed run leaves no residue on the dev graph."""
    _delete_trace_artifacts(live_neo4j)
    with live_neo4j.session() as s:
        s.run(
            "MERGE (u:User {id: $uid}) SET u.email = $email",
            uid=TRACE_USER_ID,
            email=TRACE_USER_EMAIL,
        )
        s.run(
            "MERGE (p:Profile {id: $pid}) SET p.display_name = 'S8.5 persona trace' "
            "WITH p MATCH (u:User {id: $uid}) MERGE (u)-[:HAS_PROFILE]->(p)",
            pid=TRACE_PROFILE_ID,
            uid=TRACE_USER_ID,
        )
    yield
    _delete_trace_artifacts(live_neo4j)


@pytest.fixture(scope="module")
def client(live_neo4j, trace_identity):
    """A TestClient whose request sessions AND corpus driver are the live graph,
    signing every request with a token minted just now (a module of eleven days
    outlives a 60-minute access token — the test_trip_api.py measurement)."""
    app = create_app()

    def _live_session():
        with live_neo4j.session() as s:
            yield s

    app.dependency_overrides[get_session] = _live_session
    app.dependency_overrides[get_driver] = lambda: live_neo4j
    # THE $0 SEAM, DECLARED HERE rather than inherited. tests/conftest.py's money
    # guard patches the billing clients per TEST (an autouse function-scoped
    # fixture), and the eleven days are built in a MODULE-scoped fixture — which
    # pytest sets up BEFORE any function-scoped fixture, so the guard is not yet
    # armed. Measured 2026-08-24: the first run of this file reached
    # `AnthropicPremiumExecutor` and only stopped at the blank API key the
    # conftest scrubs. A trace says what it costs at its own call site: the
    # product's own offline adapter, and the offline entailment stub the guard
    # itself uses.
    app.dependency_overrides[get_premium_compose_executor] = lambda: OfflinePremiumExecutor()
    app.dependency_overrides[get_faithfulness_checker] = lambda: MockFaithfulnessChecker()

    def _mint_fresh_bearer(request):
        request.headers["Authorization"] = (
            f"Bearer {create_access_token(TRACE_USER_ID, TRACE_USER_EMAIL)}"
        )

    with TestClient(app) as c:
        c.event_hooks["request"] = [_mint_fresh_bearer]
        yield c


@pytest.fixture(scope="module")
def traces(client) -> dict[str, Trace]:
    """THE ELEVEN DAYS, walked once. Built and replayed in ONE fixture because a
    trace is expensive and the five invariants read the SAME stream — replay
    once, assert many, which is what "replayable" is for."""
    return {name: walk_the_day(client, build_trace(client, name)) for name in DAYS}


@pytest.fixture(scope="module")
def served(traces) -> dict[str, Trace]:
    return {name: trace for name, trace in traces.items() if trace.served}


def _protected_ids(day: dict) -> set[str]:
    return {p["promise_id"] for p in day.get("promises", []) if p.get("protected")}


def _stop_ids(day: dict) -> list[str]:
    return [st["poi_id"] for st in day.get("stops", [])]


def _spoken_about(reply: dict) -> set[str]:
    """Every stop the reply SAYS something about: the question's at-risk thing,
    and any stop an entry names as its trigger. A promise named on the wire was
    not dropped in silence (§4.3 — "fabric may change silently; promises may
    not"; §4.2's second tier is the question)."""
    out: set[str] = set()
    for entry in reply.get("contingencies", []):
        if entry.get("at_risk_stop_id"):
            out.add(entry["at_risk_stop_id"])
        if entry.get("question") and entry["trigger"].get("stop_id"):
            out.add(entry["trigger"]["stop_id"])
    return out


@needs_neo4j
def test_every_persona_day_either_serves_or_refuses_by_name(traces):
    """docs/personas/00-what-these-are-for.md, the rule this whole set exists
    for: "A design is not finished because it is elegant. It is finished when it
    can represent all eleven of these days. When a proposal cannot hold one of
    them, say which one and what it costs — do not quietly redefine the persona
    to fit the algorithm."

    So: every one of the eleven either SERVES — a day, its promises and its
    contingency set on the wire — or is REFUSED BY NAME (W8.2 R8: plain words, a
    named gate, nothing consumed, never a silently thinner day). What is
    forbidden is the third thing: a day that half-arrives. The count that serves
    is a measured FLOOR, not an equality, so a day that starts serving is never
    a failure; a day that stops serving is.

    UNDO: make a refusal shapeless (drop `reason` from the compose 422) -> RED.
    """
    assert set(traces) == set(DAYS), sorted(traces)
    refused = {name: t for name, t in traces.items() if not t.served}
    for name, trace in refused.items():
        assert isinstance(trace.refusal, dict), f"{name}: {trace.refused_at} {trace.refusal!r}"
        named = trace.refusal.get("reason") or trace.refusal.get("cause")
        assert named, (
            f"{name} was refused at {trace.refused_at} with nothing named: {trace.refusal}"
        )
        assert trace.session is None, f"{name} was refused AND served"
    served = {name for name, t in traces.items() if t.served}
    assert len(served) >= SERVED_FLOOR, (
        f"{len(served)} of {len(DAYS)} persona days serve, under the measured floor "
        f"of {SERVED_FLOOR}. Serving: {sorted(served)}. Refused: "
        + "; ".join(
            f"{n} at {t.refused_at}: {(t.refusal or {}).get('reason')}"
            for n, t in refused.items()
        )
    )


@needs_neo4j
def test_no_promise_is_ever_silently_dropped(served):
    """§7.4.1, fuzzed across replans. Design §4.3: "fabric may change silently;
    promises may not"; §4.2: a promise event produces "exactly one behaviour: the
    question". W5.14 is the measured failure this pins — on Rosemary's own day
    (05-step-free-visitor.md: "Start Musée d'Orsay, end Musée d'Orsay") the live
    replan handed back the bench alone and dropped the Orsay in silence.

    At every step of every walked day: a promise the wire marked `protected` and
    still ahead is either still on the day the server hands back, or the reply
    SAYS something about it. Never absent and unmentioned.

    **THIS TEST'S UNDO IS NOT PROVEN, and that is a written decision, not a quiet
    pass (§0.1.3).** Three mutations were tried at the real sites and all three
    left it GREEN: emptying `_person_protected`; taking the finish off the
    replan's tail; and both together. The dump that explains it
    (`evidence/phase8-gates/s85-before-picture.md`): on Camille the only protected
    promise is her DECLARED END, so the tail materialises it at every step —
    `kept=1, dropped=[]` even with twenty minutes left on a five-hour day; on
    Greta a stop IS dropped in silence at the linger, but her day is an open walk
    with no named end, so by W5.2 R1.5 the planner's own anchor is not a promise
    and nothing was owed. **On the days that currently serve, a protected item
    can never be at risk.** What would prove it is a served day carrying a
    protected item that is NOT the finish — a rest or a pin. Rosemary's bench day
    is exactly that day, and it cannot serve today (the C3 audio floor). Carried
    to W8.6 with the other two rows; the test stays because it asserts the right
    rule and bites the moment such a day serves.
    """
    checked = 0
    for name, trace in served.items():
        for step in trace.walk:
            if not step.served:
                continue
            ahead = {st["poi_id"] for st in step.ahead}
            protected = _protected_ids(step.before) & ahead
            if not protected:
                continue
            checked += 1
            kept = set(_stop_ids(step.reply))
            silent = protected - kept - _spoken_about(step.reply)
            assert not silent, (
                f"{name} — {step.label}: {sorted(silent)} was promised, was still "
                f"ahead, and the reply neither kept it nor said a word about it. "
                f"Kept: {sorted(kept)}; spoken about: {sorted(_spoken_about(step.reply))}"
            )
    assert checked, (
        "no walked step had a protected promise still ahead — this invariant "
        "measured nothing (design §7.4.1 is fuzzed ACROSS replans)"
    )


@needs_neo4j
def test_every_drop_rechecks_the_longest_leg_cap(served):
    """§7.4.2 and design §4.5.3, in Rosemary's own words
    (05-step-free-visitor.md, step 2: "Twelve minutes of walking, and that is her
    limit in one go"; the breaks bullet: "A walking budget is not one number").
    §4.5.3 spells the trap: "Dropping a stop MERGES its two legs — drop
    Rosemary's bench and a 12-minute and a 9-minute leg fuse into 21 continuous
    minutes, double her limit. Without this rule every drop is a trap."

    THE CEILING IS THE CONTRACT'S OWN: `ReplanContext.longest_leg_ceiling_seconds`
    names it — "§4.5.3: the base day's longest street leg; a replan may not mint
    a longer one (Rosemary's 12 + 9 fusing into 21)" — and where the party also
    carries a per-leg cap (`contract._PARTY_AXES`, take-it-easy's 13), the
    tighter of the two binds. So: any replan that DROPS a stop must hand back a
    day whose every leg still sits under that ceiling, measured off the wire's
    own clocks, never a second walking model.

    **THIS TEST'S UNDO IS NOT PROVEN EITHER, and the attempt is the finding**
    (§0.1.3, a written decision). Removing the party's `max_leg_minutes` from the
    replan's tail left it GREEN: no day that currently SERVES carries a cap —
    take-it-easy is the only §2.4 row with a number and Rosemary's day collapses
    to one stop. Passing the base day's longest leg to the live replan as
    `ReplanContext.longest_leg_ceiling_seconds` (as `build_contingency_set` does)
    was then tried as a FIX and REVERTED on measurement, one variable moved:
    without it Camille's replan passes; with it, the planner refuses her middle
    stops because their insertion legs breach the 17-minute ceiling and hands back
    the Arc straight to Notre-Dame as ONE THIRTY-NINE MINUTE WALK — longer than
    anything it rejected. The ceiling bounds what may be INSERTED; it cannot bound
    the walk that is left when nothing is.

    THE RULE NOW EXISTS (P9R-S4.M1): `_prefer_keeping_over_a_longer_leg` on
    the live replan detects a breach after a drop and retries ONCE with the
    dropped stops PROTECTED (force-seat is the compel lever the ceiling never
    had) plus the base ceiling, then serves whichever day has the shorter
    longest leg. It is a preference with honest fallbacks — a retry that
    refuses, or improves nothing, ships the lesser evil — so this test is the
    behavioural bar it must clear on every served day, and QA's undo proved
    it discriminating (the helper stubbed to a pass-through goes RED here on
    paulo's day).
    """
    drops_checked = 0
    days_with_legs = 0
    for name, trace in served.items():
        base_legs = [minutes for _a, _b, minutes in leg_minutes(trace.stops)]
        if not base_legs:
            continue  # a one-stop day has no walk to lengthen
        days_with_legs += 1
        ceiling = min(
            value
            for value in (resolved_cap_minutes(name), max(base_legs))
            if value is not None
        )
        for step in trace.walk:
            if not step.served:
                continue
            before_ids = {st["poi_id"] for st in step.ahead}
            after_ids = set(_stop_ids(step.reply))
            if not (before_ids - after_ids):
                continue  # nothing was dropped; the rule has nothing to re-check
            drops_checked += 1
            over = [
                (a, b, minutes)
                for a, b, minutes in leg_minutes(step.reply["stops"])
                if minutes > ceiling + CLOCK_ROUNDING_MINUTES
            ]
            assert not over, (
                f"{name} — {step.label}: dropping {sorted(before_ids - after_ids)} fused "
                f"a walk past the {ceiling}-minute ceiling (base day's legs {base_legs}, "
                f"party cap {resolved_cap_minutes(name)}): {over}"
            )
    assert days_with_legs, (
        "no served day has a walking leg, so §4.5.3 measured nothing — the rule "
        "is about two legs FUSING when the stop between them is dropped"
    )
    assert drops_checked, (
        f"{days_with_legs} day(s) with legs walked and not one replan dropped a "
        "stop, so the leg-cap re-check measured nothing"
    )


@needs_neo4j
def test_protected_items_are_never_auto_cut(served):
    """§7.4.3 and design §4.5.2: "Rests, meals, toilets and the finish are never
    auto-cut; they trigger the question instead." W5.2 R1.5 fixes whose promises
    those are — what the PERSON asked for. Read here over the PRECOMPUTED set
    (design §4.6: the phone selects from it and decides nothing), so an answer
    the phone might apply cannot quietly be the one that cuts the bench.

    A wrap-up entry is exempt: the person chose to end the day. An entry that
    seats nothing at all is counted and reported, never silently passed.

    UNDO: drop `protected_poi_ids` from `contingency.py::ctx_from` -> a late
    band's answer sheds the rest by arithmetic -> RED.
    """
    checked = 0
    empty_tails = 0
    for name, trace in served.items():
        protected = _protected_ids(trace.session)
        order = _stop_ids(trace.session)
        if not protected:
            continue
        for entry in trace.contingencies:
            if entry["trigger"]["kind"] == "wrap_up_from":
                continue
            if not entry["stop_ids"]:
                empty_tails += 1
                continue
            trigger = entry["trigger"].get("stop_id")
            index = order.index(trigger) if trigger in order else -1
            still_ahead = {pid for pid in order[index + 1 :] if pid in protected}
            cut = still_ahead - set(entry["stop_ids"])
            if entry.get("at_risk_stop_id"):
                cut -= {entry["at_risk_stop_id"]}
            assert not cut, (
                f"{name} — the precomputed answer for {entry['trigger']} cut "
                f"{sorted(cut)} without asking: kept {entry['stop_ids']}"
            )
            checked += 1
    assert checked, (
        f"no precomputed entry had a protected item ahead of it "
        f"({empty_tails} entries seated nothing at all), so §4.5.2 measured nothing"
    )


@needs_neo4j
def test_announcements_carry_screen_text_and_never_land_on_a_walking_leg(served):
    """§7.4.4 and design §4.4: "Speech queues to a natural moment — never into a
    conversation, never onto a walking leg (a sentence missed on the move is gone
    forever for a second-language listener)" (§4.4.1, Paulo —
    08-second-language-listener.md step 2: "rewinding on a narrow pavement means
    stopping and being walked into. That content is simply gone"), "Everything
    spoken ALSO appears on screen" (§4.4.2), "One sentence, maximum" (§4.4.4).

    Three clauses, each measured on the wire:
    (a) every announcement of every version carries its screen line, and a
        question is one sentence naming its default arm (W5.2 R2.4);
    (b) fired from a position genuinely ON A LEG — outside every footprint the
        day placed — the reply still obeys (a), so a walker who can hear nothing
        can read it and owes no answer;
    (c) nothing auto-plays on a leg by construction: every stop whose piece has
        words carries the FOOTPRINT it plays inside (Phase 7 S7.3), and the only
        leg-bound text on the wire is `leg_narration`.

    UNDO: drop `screen_text=q` from `routes/trips.py::_live_question` -> (a)/(b)
    RED; drop `triggers=place_stops(route)` from `compose_trip` -> (c) RED.
    """
    on_leg_steps = 0
    announcements = 0
    placed = 0
    for name, trace in served.items():
        versions = [trace.session, *(s.reply for s in trace.walk if s.served)]
        if trace.final_session and "refusal" not in trace.final_session:
            versions.append(trace.final_session)
        for day in versions:
            for entry in day.get("contingencies", []):
                announcements += 1
                assert entry["screen_text"].strip(), (
                    f"{name}: {entry['trigger']} speaks with nothing on screen (§4.4.2)"
                )
                if entry.get("question"):
                    assert entry["default_arm"], (
                        f"{name}: {entry['trigger']} asks without naming its default (R2.4)"
                    )
                    assert entry["question"].strip().endswith("?"), entry["question"]
        for step in trace.walk:
            if step.served and step.on_the_leg:
                on_leg_steps += 1
                for entry in step.reply.get("contingencies", []):
                    assert entry["screen_text"].strip(), (
                        f"{name} — mid-leg: {entry['trigger']} spoke with nothing on "
                        "screen; a leg is where a sentence is lost (§4.4.1/§4.4.2)"
                    )
                    if entry.get("question"):
                        assert entry["default_arm"], (
                            f"{name} — mid-leg: a question with no default arm leaves a "
                            "walking listener owing an answer (W5.2 R2.4)"
                        )
        for stop in trace.stops:
            if not (stop.get("narration") or "").strip():
                continue
            placed += 1
            trigger = stop.get("trigger") or {}
            assert float(trigger.get("radius_m") or 0) > 0, (
                f"{name}: {stop['poi_name']!r} has words and no footprint to play them "
                "in — nothing places it, so nothing keeps it off the leg (S7.3)"
            )
    assert announcements, "no announcement was measured (§4.4.2 measured nothing)"
    assert on_leg_steps, "no step of any walk was ON A LEG (§4.4.1 measured nothing)"
    assert placed, "no stop carried words (§7.4.4's third clause measured nothing)"


@needs_neo4j
def test_every_prefix_is_decent_and_wrap_up_ends_with_a_close(served):
    """§7.4.5: "Every prefix is decent — wrap-up at any minute of any simulated
    day ends with a close." Nadia's day is the source
    (03-family-with-children.md, step 10 and its breaks bullet: "The tour will
    not be finished. It ends at 78% by design ... EVERY PREFIX OF THE ROUTE MUST
    BE A DECENT TOUR"), and design §5.3 is the mechanism: "Every named stretch
    carries a written one-line close that can play wherever the stretch actually
    ends. This is what 'wrap it up' plays, and it is why quitting early feels
    like finishing."

    So, on every version of every walked day: a wrap-up exists FROM every stop
    the person can be standing at — rests included (W6.2, Rosemary: "the one
    place I am most likely to decide to go home is where the button does
    nothing") — and the day it would end has a written close to play.

    UNDO: build wrap-ups from story stops only in
    `contingency.py::build_contingency_set` -> a rest has none -> RED; drop the
    close -> the wrap-up ends on nothing -> RED.
    """
    stops_checked = 0
    versions_checked = 0
    for name, trace in served.items():
        days = [trace.session]
        if trace.final_session and "refusal" not in trace.final_session:
            days.append(trace.final_session)
        for day in days:
            wrap_from = {
                entry["trigger"]["stop_id"]
                for entry in day.get("contingencies", [])
                if entry["trigger"]["kind"] == "wrap_up_from"
            }
            standable = [
                st for st in day.get("stops", [])
                if not st["poi_id"].startswith(END_B_SENTINEL_PREFIX)
            ]
            if not standable:
                continue
            versions_checked += 1
            missing = [st["poi_name"] for st in standable if st["poi_id"] not in wrap_from]
            assert not missing, (
                f"{name} v{day['plan_version']}: no wrap-up from {missing} — the button "
                "does nothing where the person is most likely to press it"
            )
            for stop in standable:
                stops_checked += 1
                assert (stop.get("close_text") or "").strip(), (
                    f"{name} v{day['plan_version']}: {stop['poi_name']!r} has no written "
                    "close, so wrapping up there ends on nothing (§5.3)"
                )
    assert versions_checked, "no version of any day was checked for its prefixes"
    assert stops_checked, "no stop was checked for its close (§5.3 measured nothing)"


def _anchor_subs_and_beat_subs(driver) -> tuple[dict[str, set[str]], dict[str, str]]:
    """Per anchored place, the sub-locations its reviewed anchors name; and per beat
    id, the beat's own sub_location — read from the graph the day was planned from,
    so the pin follows the data rather than a hardcoded list."""
    import json as _json

    anchored: dict[str, set[str]] = {}
    sub_by_beat: dict[str, str] = {}
    with driver.session() as session:
        for record in session.run(
            "MATCH (p:POI {city_name: 'paris'}) WHERE p.anchors IS NOT NULL "
            "RETURN p.id AS id, p.anchors AS anchors"
        ):
            subs: set[str] = set()
            for anchor in _json.loads(record["anchors"]):
                subs.update(anchor.get("sub_locations") or [])
            anchored[record["id"]] = subs
        for record in session.run(
            "MATCH (b:NarrativeBeat) WHERE b.sub_location IS NOT NULL "
            "RETURN b.id AS id, b.sub_location AS sub"
        ):
            sub_by_beat[record["id"]] = record["sub"]
    return anchored, sub_by_beat


@needs_neo4j
def test_no_leg_line_outlives_its_pair_and_every_reviewed_anchor_gets_its_chapter(
    served, live_neo4j
):
    """S1 across the eleven: two things a walker cannot check for themselves.

    A leg line names both its ends, so it is true of ONE pair. On every version of every
    served day, a stop carrying leg words records the stop it is actually walked to from.
    A line with no recorded pair predates the field and is exempt: there is nothing to
    check it against.

    And a place big enough to walk around in tells its named spots AT them. Where a
    person reviewed an anchor, the story is cut there and the chapter rides the stop; a
    stop with reviewed anchors and no chapters is telling the whole place from whichever
    edge the walker came in by.

    WHAT THIS DOES NOT GUARD, measured rather than assumed. **No replan in these eleven
    days ever re-orders the stops it keeps** — every scripted replan truncates from the
    front, so the predecessor of a kept stop never changes and the re-leg rule is a
    no-op on the whole trace corpus. Reduce `_releg_kept_stops` to `return stops` and
    this test still passes. What it does pin is that the provenance reaches the wire and
    stays consistent: blank the adapter's `leg_from_poi_id` and `legs_checked` falls to
    zero, which is RED. The DROP-and-rewrite behaviour is guarded by the re-leg rule's
    own unit tests, which construct the re-ordered day these personas never produce.
    Closing this gap needs a persona whose day re-orders — a real gap, named here so the
    next reader does not mistake this green for proof.

    UNDO: blank `leg_from_poi_id` in the adapter -> nothing carries provenance -> RED.
    Take the anchors back off the graph -> a reviewed place emits no chapters -> RED.
    """
    anchored_subs, sub_by_beat = _anchor_subs_and_beat_subs(live_neo4j)
    legs_checked = 0
    anchored_checked = 0
    versions_checked = 0
    for name, trace in served.items():
        days = [trace.session]
        days += [step.reply for step in trace.walk if "refusal" not in step.reply]
        if trace.final_session and "refusal" not in trace.final_session:
            days.append(trace.final_session)
        for day in days:
            stops = day.get("stops", [])
            if not stops:
                continue
            versions_checked += 1
            for position, stop in enumerate(stops):
                written_from = stop.get("leg_from_poi_id")
                # Position 0 of a replanned tail is walked to from the stop the walker
                # has already LEFT, which this reply no longer lists — its pair is real
                # and simply not checkable from here. Every later stop's is.
                if position and stop.get("leg_narration") and written_from is not None:
                    legs_checked += 1
                    walked_from = stops[position - 1]["poi_id"]
                    assert written_from == walked_from, (
                        f"{name}: {stop['poi_name']}'s leg line was written for the walk "
                        f"from {written_from!r} and is now walked to from {walked_from!r} "
                        "— it names an end nobody is coming from"
                    )

    # THE CHAPTER OBLIGATION FOLLOWS THE VOICED NAMED SPOT. A day whose lens
    # voices only sub-less beats at an anchored place owes no chapter (Camille's
    # architecture day at the pool-anchored Tuileries names no spot); a day that
    # VOICES a named spot must tell it standing there — an unclaimed voiced sub
    # is the S1 disease (Sofia's galleries), and the ratchet holds: anchoring a
    # place obliges its voiced named spots, every one.
    for name, trace in served.items():
        for stop in (trace.session or {}).get("stops", []):
            named = anchored_subs.get(stop["poi_id"])
            if named is None:
                continue
            voiced_subs = {sub_by_beat.get(bid) for bid in stop.get("beat_ids", [])}
            voiced_subs.discard(None)
            if not voiced_subs:
                continue
            anchored_checked += 1
            unclaimed = voiced_subs - named
            assert not unclaimed, (
                f"{name}: {stop['poi_name']} voices named spot(s) {sorted(unclaimed)} "
                "no reviewed anchor claims — they are being told from the "
                "footprint's edge"
            )
            assert stop.get("segments"), (
                f"{name}: {stop['poi_name']} voices anchored spots but emitted no "
                "chapter — its named spots are being told from the footprint's edge"
            )

    assert anchored_checked, (
        "no served day voiced a named spot at an anchored place, so the chapter "
        "ratchet measured nothing — the sub_location field is not reaching the wire"
    )
    assert versions_checked, "no served day to check"
    assert legs_checked, (
        "no leg line carried a recorded pair on any served day — the check would pass "
        "vacuously, so the field is not reaching the wire"
    )


@needs_neo4j
def test_a_lensed_day_serves_the_subject_it_was_asked_for(served, live_neo4j):
    """S3 across the eleven: the walker cannot check the label against the corpus,
    so the wire must never dress a stop in a subject nobody asked for.

    On every served day whose request named subjects, every story stop's label sits
    inside the asked family — the request's lenses plus their one-hop parents and
    children, the same expansion the matcher, the gate and the label all read. A
    rest (no beat) and an unlensed stop (nothing claimed) assert nothing.

    And the floor the design session fixed: the three days that served before the
    subject work — camille, greta, rosemary — still serve after it; paulo joins
    them at P9R-S1, his compose no longer refused over the walk's own narration.

    UNDO: blank the adapter's `prefer` threading -> Camille's Notre-Dame wears
    `hidden_history` again -> RED. Drop the pool gate -> a lens-miss stop returns
    with an off-family label -> RED.
    """
    from src.tour.selection import LOAD_LENS_HIERARCHY_CYPHER, _lens_neighbor_map

    for name in ("camille", "greta", "rosemary", "paulo"):
        assert name in served, f"{name} must serve — a served day going refused is a failure"

    with live_neo4j.session() as graph:
        neighbors = _lens_neighbor_map(graph.run(LOAD_LENS_HIERARCHY_CYPHER).data())

    labels_checked = 0
    for name, trace in served.items():
        lenses = trace.request.get("lenses") or []
        if not lenses:
            continue
        family = {s.lower() for s in lenses}
        for lens in list(family):
            family |= set(neighbors.get(lens, frozenset()))
        for stop in (trace.session or {}).get("stops", []):
            if not stop.get("beat_id") or not stop.get("lens_name"):
                continue
            labels_checked += 1
            assert stop["lens_name"].lower() in family, (
                f"{name} asked for {lenses} and {stop['poi_name']} is dressed as "
                f"{stop['lens_name']!r} — a subject nobody asked for"
            )
    assert labels_checked, "no lensed label was checked — the clause would pass vacuously"


@needs_neo4j
def test_aikos_rainy_tuesday_serves(client):
    """Phase 10's gate, red-first: 07-rainy-tuesday.md step 6 is the failure this
    phase exists to close ("Walks to a museum she wanted to see and finds it
    shut. It is Tuesday."). Aiko's day must SERVE — and while it is refused, the
    assertion message below is the diagnosis: the named gate and the wire's own
    refusal detail, read off one trace instead of eleven.

    UNDO: revert the Phase 10 hours work -> her day refuses again -> RED.
    """
    trace = build_trace(client, "aiko")
    assert trace.served, f"aiko refused at {trace.refused_at}: {trace.refusal}"
