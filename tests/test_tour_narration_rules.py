"""Narration tests — point first, closes, plants and threads, two lengths, register.

One narration-hand per file, on the `tests/test_tour_visit_time.py` model: each gating test
states in plain words the rule it enforces.

THE ONE WRITER. Every rule below lives in ``src/tour/authoring.py``'s ``_COMPOSE_SYSTEM`` — the
locked voice — and ships to the provider through ``candidate_compose_request_envelope``; the
tests read the envelope (what the writer is actually told), not a second copy. Every edit to
that prompt changes ``premium_authoring_policy_sha256()`` and is a DECLARED BREAKAGE of the
sealed certification data.
"""

from __future__ import annotations

from src.tour.authoring import (
    ComposeRequest,
    candidate_compose_request_envelope,
)
from src.tour.candidate_authoring import AuthoringCandidateIdentity, AuthoringStopRequest
from src.tour.contract import (
    BeatRef,
    Script,
    ScriptPOI,
    Sentence,
    TourInput,
    ValidationReport,
)


def _one_stop_request(**input_overrides) -> ComposeRequest:
    """A one-stop compose request, the shape the seam builds per stop."""
    beat = BeatRef(
        id="b0",
        poi_id="p0",
        script_body="Henri IV laid out the square. He never lived in it.",
        key_claims=("Henri IV laid out the square", "the king never lived in it"),
    )
    inputs = TourInput(start=(48.855, 2.365), duration_min=60, city_slug="paris", **input_overrides)
    stitched = Script(
        city_slug="paris",
        generated_at="2026-08-19T00:00:00Z",
        inputs=inputs,
        total_audio_seconds=10,
        total_walking_seconds=0,
        total_walk_distance_m=0,
        total_planned_seconds=600,
        selected_pois=(
            ScriptPOI(
                id="p0", name="Place des Vosges", tier=5, lat=48.855, lng=2.365, beat_ids=("b0",)
            ),
        ),
        lens_coverage={},
        script=(
            Sentence(
                text="Henri IV laid out the square.", source_id="b0", source_type="beat", stop_idx=0
            ),
            Sentence(text="He never lived in it.", source_id="b0", source_type="beat", stop_idx=0),
        ),
        validation=ValidationReport(),
    )
    return ComposeRequest(
        stitched=stitched, beats_by_id={"b0": beat}, tour_context=("Place des Vosges",)
    )


def _envelope(request: ComposeRequest) -> dict:
    from src.tour.authoring import compose_input_sha256

    candidate = AuthoringCandidateIdentity.create(
        candidate_slot="A",
        contract_sha256="1" * 64,
        reference_manifest_sha256="2" * 64,
        calibration_manifest_sha256="3" * 64,
        grounded_source_sha256="4" * 64,
        route_sha256="5" * 64,
        authoring_policy_sha256="6" * 64,
    )
    stop_request = AuthoringStopRequest.create(
        candidate=candidate, stop_index=0, compose_input_sha256=compose_input_sha256(request)
    )
    _text, sdk_request = candidate_compose_request_envelope(request, stop_request)
    return sdk_request


def _system_text(envelope: dict) -> str:
    """Extract the system prompt text from an SDK request's system field.

    Handles both the plain-string form and the block-list form (used when
    cache_control is present).
    """
    system = envelope["system"]
    if isinstance(system, str):
        return system
    return "\n".join(block["text"] for block in system if block.get("type") == "text")


# ---------------------------------------------------------------------------
# POINT FIRST: ONE rule in the locked voice (the floor check is C13, in
# tests/test_tour_quality_rubric.py).
# ---------------------------------------------------------------------------


def test_the_locked_voice_puts_the_point_first_and_saves_no_twist_for_the_end():
    """Every piece is written with its point in the FIRST minute: a visitor may walk
    off at minute eight of nine, routinely, not accidentally. The point is the turn
    with its stakes, through the one named person, fused with where to look, inside
    the stop's first ~100 words counted from the stop's OWN first
    sentence — never from a recap of an earlier stop or the walking line; the kicker may
    stay last as colour but the piece may never depend on it; the recaps go.

    MEASURED 2026-08-19: the locked voice instructed the opposite — "BUILD,
    DON'T FLATTEN … keep raising the stakes … toward a payoff near the end … the twist
    comes LATE" — and every composed stop ended on a kicker, with 150-185 words in its
    last minute and 24-44 s of recap before the place was named. This test reads the
    envelope the provider actually receives. UNDO: restore the "comes LATE" rule -> RED.
    """
    system = _system_text(_envelope(_one_stop_request()))
    assert "THE POINT FIRST" in system, "the rule must be in the locked voice"
    assert "first hundred words" in system, "the panel's measure (R1) names the window"
    assert "recap" in system.lower(), "the recap of an earlier stop is forbidden by name"
    for held_back in ("comes LATE", "payoff near the end", "BUILD, DON'T FLATTEN"):
        assert held_back not in system, f"the locked voice still says {held_back!r}"
    from src.tour.authoring import _COMPOSE_SYSTEM

    assert system == _COMPOSE_SYSTEM


# ---------------------------------------------------------------------------
# CLOSES THAT LAND ANYWHERE ("every prefix is decent").
# ---------------------------------------------------------------------------


def _stitch_day(n_stops: int, *, round_trip: bool) -> tuple:
    """A stitched day of ``n_stops`` story stops through the real stitcher with the
    offline glue client — the same call the compose path makes."""
    from src.tour.contract import POI, BeatSequence, POIBeats, Route, TransitSegment
    from src.tour.generation import generate
    from src.tour.glue_client import MockGlueClient

    pois = tuple(
        POI(
            id=f"p{i}", name=f"Stop {i}", tier=5, poi_role="stop",
            lat=48.855 + i * 0.003, lng=2.365, areas=("Le Marais",),
        )
        for i in range(n_stops)
    )
    plans = tuple(
        POIBeats(
            poi_id=p.id, poi_name=p.name, ordering_strategy="narrative_function",
            beats=(
                BeatRef(
                    id=f"b{i}", poi_id=p.id,
                    script_body=f"Someone did something at stop {i} in 1600. It mattered.",
                    key_claims=(f"something happened at stop {i} in 1600",),
                ),
            ),
        )
        for i, p in enumerate(pois)
    )
    transits = tuple(
        TransitSegment(from_poi_id=None if i == 0 else f"p{i-1}", to_poi_id=p.id,
                       distance_m=300.0, walk_seconds=240)
        for i, p in enumerate(pois)
    )
    if round_trip:
        transits = (*transits, TransitSegment(from_poi_id=f"p{n_stops-1}", to_poi_id=None,
                                              distance_m=300.0, walk_seconds=240))
    route = Route(pois=pois, transits=transits, total_walk_distance_m=300.0 * len(transits),
                  total_walk_seconds=240 * len(transits))
    inputs = TourInput(start=(48.855, 2.365), duration_min=90, city_slug="paris",
                       round_trip=round_trip)
    seq = BeatSequence(poi_beats=plans)
    script = generate(seq, route, inputs, glue_client=MockGlueClient())
    return script, seq, route


def test_the_stitch_supplies_one_close_per_stop_and_the_last_is_the_days():
    """Every named stretch carries a written one-line close that can play wherever the
    stretch actually ends. A stretch is each stop's story AND the day; the close is the
    LAST sentence of the telling; at the last stop ONE close — the day's — never the
    stop's and the day's in a row (a listener does not want "that's the walk" twice);
    "keep exploring on your own" is gone (to someone alone, "on your own" is a remark).

    The STITCH is the stitched-corpus lane AND the one writer's source: it must supply a
    GLUE_CLOSING at EVERY stop — the fallback that ships when the writer leaves it
    unrewritten (reported as close_not_authored), and the sentence the
    composer is licensed to rewrite (authoring.py's grounding rule: glue keeps a source_id
    supplied in this stop's stitched script). MEASURED 2026-08-19: only the last
    stop carried closing lines — two of them, the second the thank-you-and-keep-exploring
    sign-off. UNDO: close only the last stop again -> RED."""
    from src.tour.generation import GLUE_CLOSING

    for round_trip in (True, False):
        script, _seq, _route = _stitch_day(3, round_trip=round_trip)
        by_stop: dict[int, list[Sentence]] = {}
        for s in script.script:
            by_stop.setdefault(s.stop_idx, []).append(s)
        for k in range(3):
            closes = [s for s in by_stop[k] if s.source_id == GLUE_CLOSING]
            assert len(closes) == 1, (round_trip, k, [s.text for s in by_stop[k]])
            assert by_stop[k][-1].source_id == GLUE_CLOSING, "the close is the stop's LAST line"
            assert "exploring on your own" not in closes[0].text
            assert "Thank you" not in closes[0].text
        # A mid-day stop's fallback names ITS place; the last stop's names the day.
        assert "Stop 1" in by_stop[1][-1].text
        assert "walk" in by_stop[2][-1].text.lower() or "loop" in by_stop[2][-1].text.lower()


def test_every_prefix_of_a_stitched_day_ends_on_that_stops_close():
    """Every prefix is decent — wrap-up at any minute of any simulated day ends with a
    close. For every k, the day cut after stop k ends on stop k's close and its sentences
    still pass the floor's structural blockers (C5 no verbatim repeat, C6 no empty stop)
    — a visitor who leaves at 78 % of the day hears a close, not a mid-kicker."""
    from src.tour.generation import GLUE_CLOSING
    from src.tour.quality_rubric import score_tour

    script, _seq, route = _stitch_day(4, round_trip=False)
    for k in range(4):
        prefix = tuple(s for s in script.script if s.stop_idx <= k)
        assert prefix[-1].source_id == GLUE_CLOSING and prefix[-1].stop_idx == k
        cut = script.model_copy(
            update={"script": prefix, "selected_pois": script.selected_pois[: k + 1]}
        )
        cut_route = route.model_copy(
            update={"pois": route.pois[: k + 1], "transits": route.transits[: k + 1]}
        )
        report = score_tour(cut, cut_route, {})
        blockers = {f.check for f in report.blockers}
        assert not blockers & {"C5-verbatim-repeat", "C6-empty-stop"}, (k, blockers)


def test_the_locked_voice_has_the_close_rule():
    """One close per stretch, the LAST sentence of the telling; names the place; a
    landing, not a summary or a lesson; only facts the stop already voiced; no clock, no
    direction, never what was skipped, no thank-you, no "keep exploring". The rule ships
    in the one writer's system prompt."""
    system = _system_text(_envelope(_one_stop_request()))
    assert "THE CLOSE" in system
    assert "GLUE_CLOSING" in system
    for forbidden in ("keep exploring", "what was left out", "thank"):
        assert forbidden in system.lower(), f"the rule must forbid {forbidden!r} by name"


def _plan_with_closes(*, drop_close_at: int | None = None, template_at: int | None = None):
    """A hermetic per-stop plan through the one seam, with an executor that echoes the
    stitch except: at ``drop_close_at`` the GLUE_CLOSING sentence is omitted; at
    ``template_at`` it is left as the stitch's template (not authored); elsewhere the
    close is rewritten into an authored line."""
    from src.tour.generation import GLUE_CLOSING
    from src.tour.premium_tour import plan_premium_authoring
    from tests.test_tour_authoring_gates import _offline_response

    script, seq, route = _stitch_day(3, round_trip=False)
    plan = plan_premium_authoring(
        script, seq, route, snapshot=None, snapshot_sha256="0" * 64,
        routing_version="offline-test", policy_version="offline-test",
    )

    class _Executor:
        cost_bearing = False
        provider_name = "offline"

        def execute(self, unit):
            out = []
            k = unit.stop_index
            for s in unit.authorized_request.stitched.script:
                d = s.model_dump(mode="json")
                if s.source_id == GLUE_CLOSING:
                    if k == drop_close_at:
                        continue
                    if k != template_at:
                        d["text"] = f"That's Stop {k} — something happened here in 1600."
                out.append(d)
            return _offline_response(unit, out)

    return plan, _Executor()


def test_a_composed_stop_without_a_close_refuses_at_finalize_and_a_template_is_not_authored():
    """The close is what "wrap it up" plays, so a composed stop that has NO close
    cannot ship — the live finalizer refuses it. A close left as the stitch's TEMPLATE
    is not authored (the owner's rule: "no template close counted as authored") — it ships,
    because the fallback is better than silence, but it is REPORTED on the degradations
    channel the phone and the workbench read, never counted as the writer's. UNDO: drop
    the close requirement from finalize_premium_tour -> the missing-close day finalizes
    -> RED."""
    import pytest

    from src.tour.degradations import degradation_scope, summarize
    from src.tour.premium_tour import (
        EphemeralReceiptSink,
        PremiumBuildIdentity,
        execute_premium_plan,
        finalize_premium_tour,
    )
    from src.tour.verify import MockFaithfulnessChecker

    def finalize(plan, executor):
        responses = execute_premium_plan(
            plan, executor=executor, receipt_sink=EphemeralReceiptSink()
        )
        return finalize_premium_tour(
            plan, responses, faithfulness_checker=MockFaithfulnessChecker(),
            build_identity=PremiumBuildIdentity(commit_sha="a" * 40),
        )

    # every close authored -> finalizes, nothing reported
    plan, executor = _plan_with_closes()
    with degradation_scope() as rows:
        result = finalize(plan, executor)
    assert result.blueprint.script.script[-1].source_id == "GLUE_CLOSING"
    assert not [r for r in summarize(rows) if r["kind"] == "close_not_authored"]

    # a stop with NO close -> refused
    plan, executor = _plan_with_closes(drop_close_at=1)
    with pytest.raises(ValueError, match="close"):
        finalize(plan, executor)

    # a template close -> ships, reported as not authored (stop 2)
    plan, executor = _plan_with_closes(template_at=2)
    with degradation_scope() as rows:
        result = finalize(plan, executor)
    reported = [r for r in summarize(rows) if r["kind"] == "close_not_authored"]
    assert len(reported) == 1 and reported[0]["context"].get("stop_index") == "2", reported


def test_an_authored_close_is_entailed_against_its_own_stops_claims_and_a_template_is_exempt():
    """The fact-gates stay: the close may only land facts THIS stop voiced — it is
    entailed against the claims and bodies of the beats cited at its own stop, the way
    a reflection is entailed against the visited claims (verify.py); a close that
    imports a fact from nowhere is reported unfaithful. The stitch's template ("And
    that's Stop 1.") carries no fact and is exempt: no new fact needing a source — the
    gates bind it."""
    from src.tour.generation import GLUE_CLOSING
    from src.tour.verify import verify_faithfulness

    script, seq, _route = _stitch_day(2, round_trip=False)
    beats_by_id = {b.id: b for plan in seq.poi_beats for b in plan.beats}

    class _RejectEverything:
        calls = 0

        def entails(self, key_claims: tuple[str, ...], sentence_text: str) -> bool:
            self.calls += 1
            return False

    # the template close: never sent to the checker
    checker = _RejectEverything()
    failures = verify_faithfulness(script, beats_by_id, checker)
    assert not [s for s, _r in failures if s.source_id == GLUE_CLOSING]
    assert checker.calls == 0  # every beat sentence is verbatim corpus text; the close exempt

    # an authored close at stop 1 IS checked, against stop 1's claims only
    authored = tuple(
        s.model_copy(update={"text": "That's Stop 1 — where something happened in 1600."})
        if s.source_id == GLUE_CLOSING and s.stop_idx == 1
        else s
        for s in script.script
    )
    checked = script.model_copy(update={"script": authored})

    class _Recording:
        def __init__(self):
            self.seen = []

        def entails(self, key_claims: tuple[str, ...], sentence_text: str) -> bool:
            self.seen.append((sentence_text, key_claims))
            return False

    rec = _Recording()
    failures = verify_faithfulness(checked, beats_by_id, rec)
    close_fail = [(s, r) for s, r in failures if s.source_id == GLUE_CLOSING]
    assert len(close_fail) == 1 and close_fail[0][1] == "unfaithful_close", failures
    (sentence, support), = rec.seen
    assert "Stop 1" in sentence
    assert all("stop 1" in piece for piece in support), support  # its OWN stop's claims


def test_the_close_rides_the_wire_as_close_text():
    """Every word ever spoken exists as on-screen text: each stop's
    close travels to the phone as its own field — `close_text` on the stop — beside the
    narration it ends, so [Head back now] can show and play THIS stop's close without
    parsing the narration (the tap plays the current stretch's close, one line).
    The CRUD adapter reads it off the script's GLUE_CLOSING sentence at that stop."""
    from src.api.crud.trips import route_script_to_stops
    from src.api.models.trips import GeneratedStop
    from src.tour.generation import GLUE_CLOSING

    script, seq, _route = _stitch_day(3, round_trip=False)
    beats_by_id = {b.id: b for plan in seq.poi_beats for b in plan.beats}
    stops = route_script_to_stops(
        script.selected_pois, beats_by_id, {p.id: "10:00" for p in script.selected_pois},
        script=script,
    )
    closes = {s.stop_idx: s.text for s in script.script if s.source_id == GLUE_CLOSING}
    for idx, stop in enumerate(stops):
        assert stop["close_text"] == closes[idx], (idx, stop.get("close_text"))
        assert stop["narration"].endswith(stop["close_text"]), "the piece ENDS on its close"
    wire = GeneratedStop(
        sort_order=1, poi_id="p0", poi_name="Stop 0", lat=48.855, lng=2.365, duration_min=5,
        importance_tier=5, start_time="10:00", close_text=stops[0]["close_text"],
    )
    assert wire.close_text == closes[0]


# ---------------------------------------------------------------------------
# PLANTS stay inside the stretch; THREADS survive adaptation.
# ---------------------------------------------------------------------------


def test_the_locked_voice_forbids_forward_promises_and_recaps_and_asks_for_threads():
    """A plant may only promise a payoff INSIDE its own stretch; the sentence binding an
    alternate to the day's theme is pre-authored as a pair at authoring time. A stop may
    NAME its neighbour as a fact but never PROMISE it — "next", "in a minute", "you'll
    see", "we'll go inside", "later" go; the payoff re-names its subject; the recap (the
    backward plant) goes. The thread is ONE sentence, ≤ 15 words, one name, no idiom, a
    fact of THIS stop that binds it to the walk's theme — never a recap of the last
    stop; none rather than glue.

    MEASURED: the prompt said "BUILD MOMENTUM … plant a question at one stop
    and pay it off at the NEXT one", 33 of 43 kept predecessors carried a forward
    reference, and the reflection recap re-told Henri IV's mistresses three times in one
    afternoon. UNDO: restore "pay it off at the NEXT one" -> RED."""
    system = _system_text(_envelope(_one_stop_request()))
    assert "pay it off at the NEXT" not in system, "the cross-stop plant rule is gone"
    assert "BUILD MOMENTUM" not in system
    assert "NO FORWARD PROMISES" in system
    for banned in ("in a minute", "we'll go inside", "you'll see"):
        assert banned in system, f"the rule names {banned!r} as a forbidden promise"
    assert "THE THREAD" in system
    assert "fifteen words" in system.lower() or "15 words" in system
    assert "think back" not in system.lower(), "no recap instruction survives"


def test_a_skip_adjacency_gets_a_thread_slot_in_the_one_seam_and_threads_ride_the_output():
    """A thread is pre-authored as a pair at authoring time, for every adjacency the set
    can make. The set produces a skip for EVERY stop, so the pair (k-2 -> k) exists for
    every k >= 2 by construction: stop k's compose request names the stop two back as a
    THREAD predecessor, the schema lets the writer answer with `threads`, and the
    finalizer keeps what came back, one sentence each, entailed like a reflection.
    Spend: zero extra calls — the thread rides inside the stop's own authoring unit."""
    from src.tour.authoring import _COMPOSE_OUTPUT_SCHEMA, _certification_compose_requests

    script, seq, route = _stitch_day(4, round_trip=False)
    _beats, _stops, requests = _certification_compose_requests(script, seq, route)
    assert requests[0].thread_from == () and requests[1].thread_from == ()
    assert requests[2].thread_from == ("Stop 0",)
    assert requests[3].thread_from == ("Stop 1",)
    # The user prompt names the predecessor; the schema carries the answer.
    from src.tour.authoring import _compose_user_prompt

    assert "THREADS" in _compose_user_prompt(requests[3], 1, None)
    assert "Stop 1" in _compose_user_prompt(requests[3], 1, None)
    assert "threads" in _COMPOSE_OUTPUT_SCHEMA["properties"]
    assert "sentences" in _COMPOSE_OUTPUT_SCHEMA["required"]
    assert "threads" not in _COMPOSE_OUTPUT_SCHEMA["required"], "none rather than glue is legal"


def test_threads_are_kept_one_sentence_short_and_entailed_and_ride_the_composition():
    """A thread is ONE sentence, under fifteen words; it is content, fact-gated; it is
    never a recap. The finalizer keeps the writer's threads by stop and predecessor.
    RE-DERIVED 2026-08-19 (measured): a quality miss — two sentences, over-long,
    unentailed — is DROPPED AND REPORTED (``thread_dropped``), never a refusal: with
    ValueError here, two of three real composes of one test day died whole over an
    optional line (the remedy is "none rather than glue"; refusal is for missing
    MANDATORY content). Answering a name never asked for is a protocol violation and
    still refuses."""
    import pytest

    from src.tour.degradations import degradation_scope, summarize
    from src.tour.premium_tour import (
        EphemeralReceiptSink,
        execute_premium_plan,
        finalize_premium_composition,
        plan_premium_authoring,
    )
    from tests.test_tour_authoring_gates import _offline_response

    script, seq, route = _stitch_day(3, round_trip=False)
    plan = plan_premium_authoring(
        script, seq, route, snapshot=None, snapshot_sha256="0" * 64,
        routing_version="offline-test", policy_version="offline-test",
    )

    def executor_with(thread_text):
        class _Executor:
            cost_bearing = False
            provider_name = "offline"

            def execute(self, unit):
                stitched = unit.authorized_request.stitched.script
                sentences = [s.model_dump(mode="json") for s in stitched]
                payload = {"sentences": sentences}
                if unit.stop_index == 2:
                    payload["threads"] = [{"from": "Stop 0", "text": thread_text}]
                return _offline_response(unit, sentences, extra=payload)

        return _Executor()

    good = "Something happened at stop 2 in 1600 — the same year as at Stop 0."
    responses = execute_premium_plan(
        plan, executor=executor_with(good), receipt_sink=EphemeralReceiptSink()
    )
    composition = finalize_premium_composition(plan, responses)
    assert composition.threads_by_stop == {2: {"Stop 0": good}}

    for bad, why in (
        ("Two sentences here. And a second one.", "not ONE sentence"),
        (
            "This thread has far too many words in it to be a thread at all, "
            "well over the fifteen allowed.",
            "over 15 words",
        ),
    ):
        responses = execute_premium_plan(
            plan, executor=executor_with(bad), receipt_sink=EphemeralReceiptSink()
        )
        with degradation_scope() as rows:
            composition = finalize_premium_composition(plan, responses)
        assert composition.threads_by_stop == {}, "the bad line never ships"
        dropped = [r for r in summarize(rows) if r["kind"] == "thread_dropped"]
        assert len(dropped) == 1 and why in str(dropped[0]), (why, dropped)

    # FACT-GATED (a thread is content; the same union as the in-script thread):
    # with the real checker on, a thread it cannot entail is dropped and reported —
    # never shipped, never silently absorbed, and never the whole day's death.
    class _RefusesThreads:
        def __init__(self):
            self.calls = []

        def entails(self, claims, text):
            self.calls.append((tuple(claims), text))
            return good not in text

    responses = execute_premium_plan(
        plan, executor=executor_with(good), receipt_sink=EphemeralReceiptSink()
    )
    checker = _RefusesThreads()
    with degradation_scope() as rows:
        composition = finalize_premium_composition(plan, responses, faithfulness_checker=checker)
    assert composition.threads_by_stop == {}
    assert any(good == text for _claims, text in checker.calls), (
        "the thread reached the checker"
    )
    dropped = [r for r in summarize(rows) if r["kind"] == "thread_dropped"]
    assert len(dropped) == 1 and "not entailed" in str(dropped[0])

    # A thread for a stop nobody asked about is a PROTOCOL violation: still refused.
    class _UnaskedExecutor:
        cost_bearing = False
        provider_name = "offline"

        def execute(self, unit):
            stitched = unit.authorized_request.stitched.script
            sentences = [s.model_dump(mode="json") for s in stitched]
            payload = {"sentences": sentences}
            if unit.stop_index == 1:  # stop 1 has no thread_from
                payload["threads"] = [{"from": "Stop 0", "text": good}]
            return _offline_response(unit, sentences, extra=payload)

    responses = execute_premium_plan(
        plan, executor=_UnaskedExecutor(), receipt_sink=EphemeralReceiptSink()
    )
    with pytest.raises(ValueError, match=r"was not asked for"):
        finalize_premium_composition(plan, responses)


def test_threads_ride_the_wire_as_thread_lines_keyed_by_the_predecessors_name():
    """The pair's line is authored at compose time and must reach
    the phone — as `thread_lines` on the stop, keyed by the name of the stop that may
    come right before it, decoded from the one JSON string the graph stores (Neo4j
    properties cannot hold maps). The stitched-corpus lane carries none: a thread is
    the writer's, and the stitch has no writer."""
    from src.api.models.trips import GeneratedStop

    wire = GeneratedStop(
        sort_order=3, poi_id="p2", poi_name="Stop 2", lat=48.85, lng=2.36, duration_min=5,
        importance_tier=5, start_time="10:20",
        thread_lines='{"Stop 0": "The same tribunal filled this courtyard."}',
    )
    assert wire.thread_lines == {"Stop 0": "The same tribunal filled this courtyard."}
    assert wire.model_dump()["thread_lines"] == {
        "Stop 0": "The same tribunal filled this courtyard."
    }
    bare = GeneratedStop(
        sort_order=1, poi_id="p0", poi_name="Stop 0", lat=48.85, lng=2.36, duration_min=5,
        importance_tier=5, start_time="10:00",
    )
    assert bare.thread_lines is None


# ---------------------------------------------------------------------------
# Two lengths per major stop.
# ---------------------------------------------------------------------------


def test_the_tight_telling_is_about_three_minutes_at_the_one_emission_choke_point():
    """TIGHT = point-first, about three minutes (~450 words). The measured big stops of
    a family's day ran 4.5 and 4.9 minutes, past their own four. The ceiling is the ONE
    emission choke point (C9 governor v4), so the value moves THERE and pricing
    moves with it (a stop is priced at what it will voice — two truths would drift).
    The density dial keeps its shape: "less" halves (90 s = the never-demote floor),
    "more" gives half again (270 s — exactly the old default). UNDO: restore 270 → RED."""
    from src.tour.selection import (
        MAX_DWELL_AUDIO_SECONDS,
        MIN_DWELL_AUDIO_SECONDS,
    )

    assert MAX_DWELL_AUDIO_SECONDS == 180, "the tight telling is about three minutes"
    assert MAX_DWELL_AUDIO_SECONDS // 2 >= MIN_DWELL_AUDIO_SECONDS, (
        "a 'less talking' capped stop still never demotes to a vignette"
    )


def _day_with_a_major(*, priced: dict[str, int] | None = None):
    """The 3-stop stitched day, stop 1 given a SECOND STORY (overflow beats) and a
    priced visit — the two halves of the MAJOR definition."""
    from src.tour.contract import BeatSequence, POIBeats

    script, seq, route = _stitch_day(3, round_trip=False)
    overflow = (
        BeatRef(
            id="ov1", poi_id="p1",
            script_body="The second story of stop 1 began in 1650. It ran for years.",
            key_claims=("a second story at stop 1 began in 1650",),
        ),
        BeatRef(
            id="ov2", poi_id="p1",
            script_body="It ended when the crown took the building back.",
            key_claims=("the crown took the building at stop 1",),
        ),
    )
    plans = tuple(
        POIBeats(
            poi_id=pb.poi_id, poi_name=pb.poi_name,
            ordering_strategy=pb.ordering_strategy,
            beats=(*pb.beats, *(overflow if pb.poi_id == "p1" else ())),
        )
        if pb.poi_id == "p1" else pb
        for pb in seq.poi_beats
    )
    seq = BeatSequence(
        poi_beats=tuple(seq.poi_beats),
        vignette_beats=seq.vignette_beats,
        overflow_by_poi={"p1": ("ov1", "ov2")},
    )
    # Where the overflow BODIES live, with the overflow map naming them — what
    # compose_trip hands the full-telling planner.
    full_seq = BeatSequence(poi_beats=plans, overflow_by_poi={"p1": ("ov1", "ov2")})
    route = route.model_copy(
        update={
            "planned_visit_seconds": priced
            if priced is not None
            else {"p0": 60, "p1": 600, "p2": 60}
        }
    )
    return script, seq, full_seq, route


def test_a_major_stop_is_a_priced_budget_and_a_second_story_never_a_tier():
    """Major is not a tier: MAJOR = the priced visit can hold the full telling after the
    tight one (a budget, not a badge — read as priced >= 2x the tight, the floor at
    which a continuation as long as the telling itself fits) AND the corpus holds a
    second story (overflow). Where the corpus is thin there is no full telling, which
    beats a watered-down one. UNDO: return every overflow stop regardless of the priced
    visit -> RED."""
    from src.tour.premium_tour import full_telling_majors

    script, seq, _full_seq, route = _day_with_a_major()
    majors = full_telling_majors(script, seq, route)
    assert list(majors) == [1], "stop 1 alone: overflow AND a priced visit that holds both"

    # No second story anywhere -> no majors, whatever the pricing says.
    no_overflow = seq.model_copy(update={"overflow_by_poi": {}})
    assert full_telling_majors(script, no_overflow, route) == {}

    # A priced visit too small to hold the full telling after the tight one.
    script2, seq2, _fs2, route2 = _day_with_a_major(priced={"p0": 60, "p1": 10, "p2": 60})
    assert full_telling_majors(script2, seq2, route2) == {}
    # The budget is the smaller of 3x the tight and the 12-minute hard cap.
    budget = majors[1]
    tight_words = sum(
        len(s.text.split()) for s in script.script if s.stop_idx == 1
    )
    assert budget.tight_seconds == round(tight_words / 2.5)
    assert budget.full_budget_seconds == min(3 * budget.tight_seconds, 720)


def test_the_full_telling_is_authored_through_the_one_seam_from_the_second_story():
    """FULL = a second COMPOSED piece from the same
    material — never the leftover corpus served raw ("a dump, not a telling") —
    written point-first with its own close, repeating nothing of the tight. The seam is
    THE seam: a one-stop plan over the overflow beats through plan_premium_authoring,
    whose request carries the tight telling as ALREADY TOLD. UNDO: drop the
    already_told wiring -> RED."""
    from src.tour.authoring import _compose_user_prompt
    from src.tour.premium_tour import full_telling_majors, plan_premium_full_telling

    script, seq, full_seq, route = _day_with_a_major()
    majors = full_telling_majors(script, seq, route)
    plan = plan_premium_full_telling(
        script, full_seq, route, stop_index=1, budget=majors[1],
        snapshot=None, snapshot_sha256="0" * 64,
        routing_version="offline-test", policy_version="offline-test",
    )
    assert len(plan.units) == 1, "one stop, one unit — the same per-stop seam"
    unit = plan.units[0]
    stitched = unit.authorized_request.stitched.script
    assert {s.source_id for s in stitched if s.source_type == "beat"} == {"ov1", "ov2"}, (
        "the full telling composes the SECOND story — the overflow beats"
    )
    assert stitched[-1].source_id == "GLUE_CLOSING", "with its own close to rewrite"
    tight_text = " ".join(s.text for s in script.script if s.stop_idx == 1)
    assert unit.authorized_request.already_told == tight_text
    prompt = _compose_user_prompt(unit.authorized_request, 1, None)
    assert "ALREADY TOLD" in prompt
    assert "repeat" in prompt.lower()


def test_a_full_telling_is_kept_gated_and_never_kills_the_day():
    """The gates on the OPTIONAL second piece follow the threads' rule (drop and
    report, never a dead day): over the word budget -> dropped (`full_telling_dropped`); a
    sentence echoing the tight telling verbatim -> dropped; clean -> the full
    narration and its close ride the result. UNDO: ship the over-budget full ->
    RED."""
    from src.tour.degradations import degradation_scope, summarize
    from src.tour.premium_tour import (
        EphemeralReceiptSink,
        execute_premium_plan,
        finalize_premium_full_telling,
        full_telling_majors,
        plan_premium_full_telling,
    )
    from tests.test_tour_authoring_gates import _offline_response

    script, seq, full_seq, route = _day_with_a_major()
    majors = full_telling_majors(script, seq, route)
    plan = plan_premium_full_telling(
        script, full_seq, route, stop_index=1, budget=majors[1],
        snapshot=None, snapshot_sha256="0" * 64,
        routing_version="offline-test", policy_version="offline-test",
    )

    class _Echo:
        cost_bearing = False
        provider_name = "offline"

        def __init__(self, extra_sentences=(), close="And that's the second story of Stop 1."):
            self.extra = extra_sentences
            self.close = close

        def execute(self, unit):
            stitched = unit.authorized_request.stitched.script
            sentences = [
                s.model_dump(mode="json") for s in stitched if s.source_type == "beat"
            ]
            for text in self.extra:
                sentences.append({**sentences[0], "text": text})
            sentences.append({
                "text": self.close, "source_id": "GLUE_CLOSING",
                "source_type": "glue", "stop_idx": 1, "also_cites": [],
            })
            return _offline_response(unit, sentences)

    # Clean: the full telling ships with its close.
    responses = execute_premium_plan(plan, executor=_Echo(), receipt_sink=EphemeralReceiptSink())
    with degradation_scope() as rows:
        full = finalize_premium_full_telling(plan, responses, budget=majors[1])
    assert full is not None
    assert full.close_text == "And that's the second story of Stop 1."
    assert "second story of stop 1 began in 1650" in full.narration.lower()
    assert not [r for r in summarize(rows) if r["kind"] == "full_telling_dropped"]

    # A sentence of the TIGHT telling inside the full -> dropped and reported.
    tight_sentence = next(s.text for s in script.script if s.stop_idx == 1)
    responses = execute_premium_plan(
        plan, executor=_Echo(extra_sentences=(tight_sentence,)),
        receipt_sink=EphemeralReceiptSink(),
    )
    with degradation_scope() as rows:
        full = finalize_premium_full_telling(plan, responses, budget=majors[1])
    assert full is None, "a continuation that repeats the tight is a press cutting — cut it"
    dropped = [r for r in summarize(rows) if r["kind"] == "full_telling_dropped"]
    assert len(dropped) == 1 and "repeat" in str(dropped[0]).lower()

    # Over the word budget -> dropped and reported, never trimmed in silence.
    # One sentence guaranteed past the budget: the budget in words plus fifty.
    budget_words = round(majors[1].full_budget_seconds * 2.5)
    over = (" ".join(["word"] * (budget_words + 50)) + ".",)
    responses = execute_premium_plan(
        plan, executor=_Echo(extra_sentences=over), receipt_sink=EphemeralReceiptSink()
    )
    with degradation_scope() as rows:
        full = finalize_premium_full_telling(plan, responses, budget=majors[1])
    assert full is None
    dropped = [r for r in summarize(rows) if r["kind"] == "full_telling_dropped"]
    assert len(dropped) == 1 and "budget" in str(dropped[0]).lower()


def test_the_full_telling_rides_the_wire_and_the_more_tap_never_serves_the_dump():
    """The full telling reaches the phone as its own fields
    (`full_narration`, `full_close_text`) beside the tight ones, and the on-demand
    audio door serves IT — never the raw keep-exploring stitch — wherever it exists
    (the uncomposed dump is "a dump, not a telling"). Minor stops
    keep the extras route unchanged. UNDO: serve extra_narration first -> RED."""
    import inspect

    from src.api.models.trips import GeneratedStop
    from src.api.routes import audio as audio_routes

    wire = GeneratedStop(
        sort_order=2, poi_id="p1", poi_name="Stop 1", lat=48.86, lng=2.36, duration_min=5,
        importance_tier=5, start_time="10:10",
        full_narration="The second story, told in full. And that's the second story.",
        full_close_text="And that's the second story.",
    )
    assert wire.full_narration and wire.full_close_text
    bare = GeneratedStop(
        sort_order=1, poi_id="p0", poi_name="Stop 0", lat=48.85, lng=2.36, duration_min=5,
        importance_tier=5, start_time="10:00",
    )
    assert bare.full_narration is None and bare.full_close_text is None

    # The door's preference is the authored full telling, structurally: the handler
    # reads full_narration and falls back to the extras — in that order.
    src = inspect.getsource(audio_routes.keep_exploring_stop_audio)
    assert 'rec["full_narration"] or rec["extra_narration"]' in src


# ---------------------------------------------------------------------------
# The writer is told the door — a compose request carries its stop's door
# state, so the one writer never has to guess whether "step inside" is a lie.
# The placement floor (validation.door_line_without_door) stays the backstop;
# this is the instruction that makes the floor's refusal rare, and it is what
# lets the narration SAY the honest thing at a shut door.
# ---------------------------------------------------------------------------


def _door_route(route, *, doorless: dict[str, bool], closed_ids: set[str] = frozenset()):
    from src.tour.contract import ClockExclusion

    exclusions = tuple(
        ClockExclusion(poi_id=pid, name=pid, reason="closed today", kept_outside=True)
        for pid in closed_ids
    )
    return route.model_copy(
        update={
            "visit_goes_inside": {
                p.id: not doorless.get(p.id, False) for p in route.pois
            },
            "clock_exclusions": exclusions,
        }
    )


def test_a_doorless_stops_compose_request_carries_the_door_and_an_open_one_carries_none():
    """The one ComposeRequest builder reads the Route's own door map: a stop
    priced outside-only gets a DOOR block in its user prompt — never invite
    inside — and an open stop's prompt is untouched. UNDO: drop the door_state
    wiring -> the writer is blind again -> RED."""
    from src.tour.authoring import _certification_compose_requests, _compose_user_prompt

    script, seq, route = _stitch_day(2, round_trip=False)
    route = _door_route(route, doorless={"p1": True})
    _beats, _stops, requests = _certification_compose_requests(script, seq, route)

    assert requests[1].door_state, "the outside-only stop's request must carry its door"
    assert not requests[0].door_state, "an open stop carries no door block"
    doorless_prompt = _compose_user_prompt(requests[1], 1, None)
    open_prompt = _compose_user_prompt(requests[0], 1, None)
    assert "DOOR" in doorless_prompt
    assert "never invite" in doorless_prompt.lower()
    assert "DOOR" not in open_prompt


def test_a_clock_shut_door_may_be_said_and_a_no_time_door_is_never_called_closed():
    """Two different truths, two instructions (the `kept_outside` flag's own
    distinction, contract.ClockExclusion): a door the CLOCK voided may be said
    plainly to be closed today; a stop that is outside-only because the day
    has no time for its interior is NOT closed, and the writer must not call
    it that (the interior_did_not_fit rule: Notre-Dame is not closed — the day
    is short)."""
    from src.tour.authoring import _certification_compose_requests

    script, seq, route = _stitch_day(2, round_trip=False)
    clock_shut = _door_route(route, doorless={"p1": True}, closed_ids={"p1"})
    _b, _s, shut_requests = _certification_compose_requests(script, seq, clock_shut)
    assert "closed today" in shut_requests[1].door_state.lower()

    no_time = _door_route(route, doorless={"p1": True})
    _b, _s, time_requests = _certification_compose_requests(script, seq, no_time)
    assert "not call the place closed" in time_requests[1].door_state.lower()


def test_a_route_with_no_door_map_renders_every_prompt_exactly_as_before():
    """The identity default: an empty ``visit_goes_inside`` map (a dateless or
    legacy route) renders byte-identical prompts — the compose seam never
    invents a door nobody priced. The PROMPT is asserted first and alone: it
    is the behavioural identity, meaningful on any tree (the QA undo test
    flagged the field read below as API-coupled — it stays, second, as the
    wiring check)."""
    from src.tour.authoring import _certification_compose_requests, _compose_user_prompt

    script, seq, route = _stitch_day(2, round_trip=False)
    _b, _s, requests = _certification_compose_requests(script, seq, route)
    for request in requests.values():
        assert "DOOR" not in _compose_user_prompt(request, 1, None)
    assert all(not r.door_state for r in requests.values())


# ---------------------------------------------------------------------------
# narration_register consumed.
# ---------------------------------------------------------------------------


def test_solo_is_byte_identical_and_warm_and_family_are_deltas_in_the_one_voice():
    """SOLO = the locked voice as written, byte-identical — the
    baseline the other two are deltas from. WARM: "you" plural where English allows;
    never "you two"/"both of you"/"your partner"; never address the relationship; a
    register may take a clause away, never add a sentence. FAMILY: aloud, short
    declaratives (under ~20 words), one thing to find with the eyes in the first
    minute, may address the child once per stop ("see/look/find" — never "kids",
    never a name), may lead with the child-friendly true things and push the rest
    into the full telling. What must NOT change: the facts and names, the length,
    the voice's identity, point-first, the close; register never carries the hour,
    the rain or mobility. UNDO: stop rendering the register block -> RED."""
    from src.tour.authoring import _certification_compose_requests, _compose_user_prompt

    def prompt_for(register):
        script, seq, route = _stitch_day(2, round_trip=False)
        if register is not None:
            script = script.model_copy(
                update={"inputs": script.inputs.model_copy(
                    update={"narration_register": register}
                )}
            )
        _beats, _stops, requests = _certification_compose_requests(script, seq, route)
        return _compose_user_prompt(requests[0], 1, None)

    none_prompt = prompt_for(None)
    solo = prompt_for("solo")
    warm = prompt_for("warm")
    family = prompt_for("family")

    assert solo == none_prompt, "solo IS the locked voice — byte-identical, no delta block"
    assert "REGISTER" not in solo
    assert warm != solo and family != solo and warm != family
    for phrase in ("you two", "both of you", "your partner"):
        assert phrase in warm, f"warm names its ban {phrase!r}"
    assert "never add a sentence" in warm
    for phrase in ("twenty words", "see/look/find", '"kids"'):
        assert phrase in family, f"family carries {phrase!r}"
    # The invariants ride on both deltas: never the facts, never the close.
    for delta in (warm, family):
        assert "facts" in delta and "close" in delta.lower()


def test_paulos_density_rules_bind_every_register_in_the_locked_voice():
    """The density rules bind EVERY register — one name a sentence,
    short words, gloss hard English as well as French ("Jesuit"), no idiom. A
    reviewer counted PDV-base at 1.3-9 names a sentence and ten idioms in 4.5 minutes. These
    live in the LOCKED VOICE (the system prompt), not in a register delta — the
    sealed policy hash moves and is declared. UNDO: drop the DENSITY rule -> RED."""
    system = _system_text(_envelope(_one_stop_request()))
    assert "one proper name" in system.lower() or "one name a sentence" in system.lower()
    assert "Jesuit" in system, "the gloss rule names Paulo's own example"
    assert "idiom" in system.lower()
