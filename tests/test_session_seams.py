"""Phase 5 — the two SEAMS of the living session (design §4.6; plan S5.9/S5.10).

The 2026-08-18 amendment to the plan (phase4-ledger.md "TEST CULL"): each seam is
BEHAVIOURAL wherever a behavioural check can exist, and a source scan ONLY where the
invariant is about the source itself — and says so in its docstring. The one-engine
moulds this file replaces (`test_tour_one_engine.py`, `_mentions`, the tombstones)
were retired with the cull; the shape kept here from them is what made them honest:
NON-VACUITY FIRST (the files exist and are the ones we mean), a git-index check (a
file that vanished from the tree is not "clean"), and an anti-over-deletion clause
naming what must SURVIVE.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
MOBILE_LIB = ROOT / "mobile" / "lib"
MOBILE_TEST = ROOT / "mobile" / "test"

#: The ONE Dart method that changes the current plan; its only decision input is a
#: server contingency id (design §4.6; plan S5.9 — "the phone SELECTS, it never
#: DECIDES").
THE_ONE_SELECTOR = "bool applyContingency(String contingencyId)"

#: Vocabulary of a second replan brain. Any of these under mobile/ is a defect: the
#: phone holds "no scoring, no candidate pool, no policy" (§4.6). Matched as whole
#: words / identifiers, case-insensitive on the words, exact on the identifiers.
BANNED_BRAIN_WORDS: tuple[str, ...] = (
    r"\bcandidate(s)?\b",
    r"\bbestAlternate\b",
    r"\b_bestAlternate\b",
    r"\bchooseBest\w*\b",
    r"\bpickBest\w*\b",
    r"\bbestOf\w*\b",
    r"\brank(ed|ing)?\b",
    r"\bscore(d|s|Stop|Entry|Route)?\b",
    r"\bpoi_score\b",
    r"\binsertionCost\w*\b",
    r"\bheldKarp\b",
    r"\bHeld-Karp\b",
    r"\bgreedy\b",
    r"\bplan(Route|Day|Tour)\b",
    r"\breplan(Route|Day|Tour)\b",
    r"\bselectRoute\b",
    r"\bcontingencyFor\w*\b",
    r"\btieBreak\w*\b",
)

#: What must SURVIVE under mobile/lib — the anti-over-deletion clause: the phone's
#: arithmetic (§4.6's one permitted computation), the prefetch door (§4.7), and the
#: session vocabulary this phase adds. An over-eager sweep that deleted these would
#: pass the ban and fail here.
SURVIVING_MOBILE_NEIGHBOURS: tuple[str, ...] = (
    "haversineDistance",
    "prefetchAudio",
    "prefetchSessionAudio",
    "matchContingency",
    "holdSession",
    "SessionPlan",
    "SessionContingency",
    "fetchSession",
    "replanSession",
    # Phase 7 S7.3: the placement vocabulary the phone READS (never defines).
    "StopTrigger",
    "PlacementPolicy",
    "_atPlace",
)


def _dart_files() -> list[pathlib.Path]:
    return sorted([*MOBILE_LIB.rglob("*.dart"), *MOBILE_TEST.rglob("*.dart")])


def _tracked() -> set[str]:
    out = subprocess.run(
        ["git", "ls-files", "--", "mobile/lib", "mobile/test"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return set(out)


def test_the_replan_brain_is_only_on_the_server():
    """SEAM 1 (plan S5.9; design §4.6 "One replan brain — the phone SELECTS, it never
    DECIDES"). The invariant is an ABSENCE of code — no scoring, ranking, candidate
    pool or plan construction anywhere under mobile/ — which cannot be proven
    behaviourally, so this half is a source scan and says so. The BEHAVIOURAL half
    of the seam lives in mobile/test/services/session_selection_test.dart: the
    first matching entry in server order is selected, exactly one method changes the
    plan on a server id, the question's default holds until answered, alternates
    prefetch through the existing cache door.

    Non-vacuity first: the Dart tree exists, is tracked, and carries the session
    vocabulary. Then the ban. Then the positive clause: exactly ONE Dart method
    changes the current plan and its only decision input is a server contingency
    id. RED by mutation: add a local `_bestAlternate()` to the playback service.
    """
    files = _dart_files()
    assert len(files) >= 20, f"the Dart tree is not where this seam expects it ({len(files)})"
    tracked = _tracked()
    playback = MOBILE_LIB / "services" / "tour_playback_service.dart"
    for must in (
        "services/tour_playback_service.dart",
        "services/trip_service.dart",
        "models/trip.dart",
        "services/audio_service.dart",
    ):
        assert (MOBILE_LIB / must).exists(), f"{must} vanished from mobile/lib"
        assert f"mobile/lib/{must}" in tracked, f"{must} is not in the git index"

    corpus = {p: p.read_text(encoding="utf-8") for p in files}
    lib_text = "\n".join(t for p, t in corpus.items() if MOBILE_LIB in p.parents)

    # NON-VACUITY: the things that must survive are present.
    for name in SURVIVING_MOBILE_NEIGHBOURS:
        assert name in lib_text, f"{name!r} is missing from mobile/lib — the sweep over-deleted"

    # THE BAN: no second brain's vocabulary anywhere under mobile/.
    hits: list[str] = []
    for path, text in corpus.items():
        for pattern in BANNED_BRAIN_WORDS:
            for m in re.finditer(pattern, text, flags=re.IGNORECASE):
                line = text.count("\n", 0, m.start()) + 1
                hits.append(f"{path.relative_to(ROOT)}:{line}: {m.group(0)!r}")
    assert not hits, "a replan brain is growing in the app:\n  " + "\n  ".join(hits[:20])

    # THE POSITIVE CLAUSE: exactly ONE method changes the current plan, keyed on a
    # server contingency id, and the working stop list is assigned only by the
    # tour lifecycle, that method's re-ordering, the audio catch-up's
    # same-words adoption (P9R-S6: it swaps a held stop for a copy carrying the
    # file the server voiced for the SAME words, id-matched — no stop added,
    # dropped or moved, so it is not a plan decision), and holdSession's
    # remap of a NEW server plan version onto the held stops (P10H-M6b: the
    # server already decided the order; the phone seats what it holds, standbys
    # included, in the server's own sequence — no local ranking anywhere).
    # A plan change still arrives only through the one selector.
    play_src = corpus[playback]
    assert play_src.count(THE_ONE_SELECTOR) == 1, "the one selector is not exactly one"
    assignments = re.findall(r"^\s*_stops = .*$", play_src, flags=re.MULTILINE)
    assert len(assignments) == 5, (
        "the working stop list is assigned only in startTour, stopTour, the "
        "selector's re-ordering, adoptSessionAudio's same-words audio swap, and "
        "holdSession's new-version remap of server order onto held stops; "
        f"found {len(assignments)}: {assignments}"
    )


def _body_of(source: str, signature: str) -> str:
    """The text of one Dart/Python method from its signature to the next member
    at the same indentation — enough for an is-it-in-there scan."""
    start = source.index(signature)
    indent = source[: start].rsplit("\n", 1)[-1]
    rest = source[start + len(signature) :]
    end = re.search(rf"\n{indent}(?:[A-Za-z@/]|\}})", rest)
    return rest if end is None else rest[: end.start()]


def test_the_session_clock_is_checked_against_the_server():
    """SEAM 2 (plan S5.10; design §4.6 — the silent-divergence bug the one-brain
    rule exists to prevent). Exactly ONE re-timing expression each side; the
    reconnect path COMPARES them; a divergence beyond `retime_tolerance_seconds`
    is REPORTED (a row on the degradations channel on the server, a line for the
    screen on the phone) and never silently corrected — no assignment from the
    server's clock back into the local one, and none the other way.

    The BEHAVIOURAL halves live where behaviour can be run: on the server,
    tests/test_trip_api.py::TestLivingSession::test_a_phone_clock_that_diverges_is_reported_never_corrected
    (live corpus: the row appears, the reply keeps the server's clock) and
    tests/test_tour_session.py (the one expression prices narration at the learned
    rate; the finish is its view); on the phone,
    mobile/test/services/session_arithmetic_test.dart ("REPORTED, never corrected":
    the notice appears, the phone's re-timing is unchanged). What ONLY a source
    read can assert is the ONE-ness and the ABSENCE — one `stop_clocks`, one
    `retimeRemaining`, no second running clock, and no assignment inside either
    compare — so that half is a scan and says so. RED by mutation: correct the
    phone from the server inside `_compareClockWithServer` (an assignment) -> RED;
    a second walk-less clock in the CRUD adapter -> RED.
    """
    src_dir = ROOT / "src"
    py = {p: p.read_text(encoding="utf-8") for p in sorted(src_dir.rglob("*.py"))}
    contingency = src_dir / "tour" / "contingency.py"
    routes = src_dir / "api" / "routes" / "trips.py"
    crud = src_dir / "api" / "crud" / "trips.py"
    for must in (contingency, routes, crud):
        assert must.exists(), f"{must} vanished"

    # ONE server expression, and everything that spells a clock is a view of it.
    definitions = [p for p, t in py.items() if "def stop_clocks(" in t]
    assert definitions == [contingency], definitions
    assert py[contingency].count("def stop_clocks(") == 1
    finish = _body_of(py[contingency], "def finish_clock(")
    assert "stop_clocks(" in finish and "total_walk_seconds + sum(" not in finish, (
        "finish_clock must be a VIEW of stop_clocks, not a second sum"
    )
    for view in ("def _wire_clocks(", "def _session_promises("):
        assert "stop_clocks(" in _body_of(py[routes], view), f"{view} does not read the one clock"
    # The CRUD adapter's walk-less running clock is gone (S5.10): it writes what
    # it is handed.
    adapter = _body_of(py[crud], "def route_script_to_stops(")
    assert "current_minute" not in adapter and "current_hour" not in adapter, (
        "a second, walk-less clock is back in the adapter"
    )
    assert "clocks.get(sp.id" in adapter

    # The server COMPARES and REPORTS, and assigns nothing.
    report = _body_of(py[routes], "def _report_phone_clock(")
    assert "clock_divergence_seconds(" in report and "record(" in report
    assert "SESSION_CLOCK_DIVERGENCE" in report
    assert re.search(r"session_plan\.stops\[[^\]]*\]\s*=", report) is None, (
        "the server adopted the phone's clock"
    )
    assert re.search(r"^\s*(?:first|session_plan)\.\w+\s*=", report, re.M) is None
    assert "_report_phone_clock(" in _body_of(py[routes], "def replan_trip_session(")

    # ONE phone expression, COMPARED on every held version, REPORTED, no assignment.
    playback = (MOBILE_LIB / "services" / "tour_playback_service.dart").read_text(encoding="utf-8")
    assert playback.count("List<StopEta> retimeRemaining()") == 1
    assert playback.count("kHaversineCorrection / paceMps") == 1, (
        "the walk arithmetic must be spelled once (the one _walkSeconds helper)"
    )
    assert playback.count("int _walkSeconds(double meters)") == 1
    assert playback.count("_walkSeconds(") >= 3  # its definition and its two readers
    assert playback.count("void _compareClockWithServer(SessionPlan session)") == 1
    hold = _body_of(playback, "void holdSession(SessionPlan session)")
    assert "_compareClockWithServer(session);" in hold
    compare = _body_of(playback, "void _compareClockWithServer(SessionPlan session)")
    assert "retimeRemaining()" in compare and "_serverClockFor(" in compare
    assert "_clockNotices.add(" in compare and "retimeToleranceSeconds" in compare
    assert re.search(r"^\s*_\w+\s*=[^=]", compare, re.M) is None, (
        "the compare assigns into the phone's state — the server is correcting the phone:\n"
        + compare
    )
    assert re.search(r"^\s*_\w+\s*[-+*/]=", compare, re.M) is None
    # The behavioural halves are where the docstring says.
    arithmetic = MOBILE_TEST / "services" / "session_arithmetic_test.dart"
    assert arithmetic.exists()
    assert "REPORTED, never corrected" in arithmetic.read_text(encoding="utf-8")
    api_test = (ROOT / "tests" / "test_trip_api.py").read_text(encoding="utf-8")
    assert "def test_a_phone_clock_that_diverges_is_reported_never_corrected(" in api_test


def test_the_audio_placement_rule_has_one_definition():
    """SEAM 3 (plan S7.3; design §5.6 C7, §8.4; W7.2 R1). ONE definition of WHERE a
    stop's piece plays: the server places each stop (`place_stops`, one module,
    src/tour/placement.py), the wire carries it (`GeneratedStop.trigger`,
    `SessionPlan.placement`), the item stores it, and the phone asks ONE predicate
    (`_atPlace`, over the one distance-within-radius spelling `_within`) that reads the
    wire's radius — no literal circle anywhere in the playback service. Until S7.3 the
    app drew two circles of its own (10 m to start, 40 m to be "at"), for a 140 m square
    and a doorway alike.

    The BEHAVIOURAL halves live where behaviour can be run: tests/test_tour_placement.py
    (the rule: courtyard 140 m, door 10 m, the sentinel's own-place radius, the family's
    standstill, the wire) and mobile/test/services/tour_playback_service_test.dart, group
    "the footprint is the place (S7.3)" (a 140 m courtyard triggers ~130 m from its
    centroid, a 10 m door stays silent at 30 m, the family piece waits for the standstill,
    told once is told, no trigger = no auto-play). What ONLY a source read can assert is
    the ONE-ness and the ABSENCE — one definition, one construction site, every wire site
    carrying it, no second circle — so this half is a scan and says so. RED by mutation:
    put `kStopCircleM` or a `<= 10.0` distance test back into the playback service;
    construct a StopTrigger outside placement.py; drop `trigger=` from a GeneratedStop
    construction site or `placement` from the session.
    """
    src_dir = ROOT / "src"
    py = {p: p.read_text(encoding="utf-8") for p in sorted(src_dir.rglob("*.py"))}
    placement = src_dir / "tour" / "placement.py"
    contingency = src_dir / "tour" / "contingency.py"
    routes = src_dir / "api" / "routes" / "trips.py"
    crud = src_dir / "api" / "crud" / "trips.py"
    for must in (placement, contingency, routes, crud):
        assert must.exists(), f"{must} vanished"

    # ONE server definition, ONE construction site.
    assert [p for p, t in py.items() if "def place_stops(" in t] == [placement]
    assert [p for p, t in py.items() if "class StopTrigger(" in t] == [placement]
    assert [p for p, t in py.items() if "class PlacementPolicy(" in t] == [placement]
    constructors = [p for p, t in py.items() if re.search(r"\bStopTrigger\(", t)]
    assert constructors == [placement], f"a second construction site: {constructors}"
    # The own-place radius is defined ONCE (in the rule) and READ by the contingency set.
    assert py[placement].count("OWN_PLACE_RADIUS_M: float = 60.0") == 1
    assert re.search(r"^OWN_PLACE_RADIUS_M\s*[:=]", py[contingency], re.M) is None, (
        "contingency.py defines the own-place radius again"
    )
    assert re.search(
        r"from (?:src\.tour\.|\.)placement import[^\n]*OWN_PLACE_RADIUS_M", py[contingency]
    ), "contingency.py does not read the one own-place radius from the rule"

    # EVERY wire construction site carries the placed geometry; the item stores it; the
    # saved-trips reader hands it back; every session version carries the day's policy.
    for name in ("def generate_trip(", "def compose_trip("):
        body = _body_of(py[routes], name)
        assert "triggers=place_stops(route)" in body, f"{name} does not place its stops"
        assert 'trigger=s.get("trigger")' in body, f"{name} drops the trigger from the wire"
    assert "trigger_json" in _body_of(py[crud], "def _create_itinerary_items(")
    assert "trigger_json" in _body_of(py[crud], "def list_trips_for_profile(")
    adapter = _body_of(py[crud], "def route_script_to_stops(")
    assert '"trigger": placed[sp.id].model_dump()' in adapter
    # S7.5: the priced queue is a planner decision compose cannot work out again — it is
    # persisted with the pick and handed back to the ONE rebuild (summarise_route's slot),
    # so a composed day's triggers carry the same seconds of line the planned day's did.
    generate = _body_of(py[routes], "def generate_trip(")
    compose = _body_of(py[routes], "def compose_trip(")
    assert '"planned_queue_seconds": dict(flavour.planned_queue_seconds)' in generate
    assert "planned_queue_seconds=" in compose, "compose rebuilds the route without its queues"
    assert "route.planned_queue_seconds.get(" in _body_of(
        py[src_dir / "tour" / "placement.py"], "def place_stops("
    ), "the rule does not READ the one queue map (a mention is not a read)"
    # S7.6: the door and the placed outside seconds ride the same three hops — priced at
    # selection's ONE site beside the queue map, persisted with the pick, handed back to
    # the one rebuild, read by the rule.
    selection = py[src_dir / "tour" / "selection.py"]
    assert selection.count('"planned_outside_seconds": {') == 1, (
        "the outside map is not priced once"
    )
    assert selection.count('"planned_queue_seconds": {') == 1
    assert '"visit_goes_inside": dict(flavour.visit_goes_inside)' in generate
    assert '"planned_outside_seconds": dict(flavour.planned_outside_seconds)' in generate
    assert "visit_goes_inside=" in compose and "planned_outside_seconds=" in compose
    place_stops_body = _body_of(py[src_dir / "tour" / "placement.py"], "def place_stops(")
    assert "route.visit_goes_inside.get(" in place_stops_body
    assert "route.planned_outside_seconds.get(" in place_stops_body
    # S7.7: the LEG piece (design §5.6 C7; plan defect 7) is split from the story by THE
    # one leg-vs-stop rule (`is_walk_concurrent` — the workbench's leg cards and the
    # blueprint's playback assignments read the same one), rides every wire site as
    # `leg_narration`, is voiced through the one session-line voicing table, stored on the
    # item, read back by the saved-trips reader and overlaid by the session GET.
    render_md = py[src_dir / "tour" / "render_md.py"]
    assert render_md.count("is_walk_concurrent(") >= 1, "the split does not use the one rule"
    assert "def stop_leg_text(" in render_md and "def stop_narration_text(" in render_md
    assert "leg_narration" in adapter, "the adapter does not carry the leg piece"
    for name in ("def generate_trip(", "def compose_trip("):
        assert 'leg_narration=s.get("leg_narration")' in _body_of(py[routes], name), name
    assert "leg_narration" in _body_of(py[crud], "def _create_itinerary_items(")
    assert "leg_audio_url" in _body_of(py[crud], "def list_trips_for_profile(")
    assert "leg_audio_url" in _body_of(py[routes], "def _with_live_audio(")
    audio_routes = py[src_dir / "api" / "routes" / "audio.py"]
    voicing = _body_of(audio_routes, "def _voice_session_lines(")
    assert '"leg_narration"' in voicing and '"leg_audio_url"' in voicing, (
        "the leg piece is not voiced through the one session-line table"
    )
    # S7.7 (B): THE CHAPTERS — one rule cuts a marquee's story at its reviewed anchors
    # (`place_segments`, reading the anchor off the POI through `anchor_of`), the
    # adapter carries them, generate and compose hand the route's anchors in through
    # ONE placement call (`place_anchors(route)`), the item stores `segments_json`,
    # the voicing table voices each chapter, the session GET overlays the files, and
    # the phone starts a chapter at ONE site.
    placement_py = py[src_dir / "tour" / "placement.py"]
    assert placement_py.count("def place_segments(") == 1
    assert placement_py.count("def anchor_of(") == 1
    assert placement_py.count("class StopSegment(") == 1
    assert "anchors=place_anchors(route)" in generate and "anchors=place_anchors(route)" in compose
    assert "place_segments(" in adapter and "anchor_of(" in adapter
    assert "segments_json" in _body_of(py[crud], "def _create_itinerary_items(")
    assert "segments_json" in _body_of(py[crud], "def list_trips_for_profile(")
    assert "segments_json" in _body_of(py[routes], "def _with_live_audio(")
    assert "segments_json" in voicing, "the chapters are not voiced through the one pass"
    session = _body_of(py[routes], "def _session_plan(")
    assert session.count("SessionPlan(") == 2
    assert '"placement": place_day(tour_input)' in session
    assert session.count("**finish_fields,") == 2, "a session version without the policy"

    # THE PHONE: reads the wire, defines nothing, draws no circle of its own.
    playback = (MOBILE_LIB / "services" / "tour_playback_service.dart").read_text(encoding="utf-8")
    model = (MOBILE_LIB / "models" / "trip.dart").read_text(encoding="utf-8")
    lib_text = "\n".join(p.read_text(encoding="utf-8") for p in sorted(MOBILE_LIB.rglob("*.dart")))
    assert "class StopTrigger" in model and "final StopTrigger? trigger;" in model
    assert "class PlacementPolicy" in model and "final PlacementPolicy? placement;" in model
    assert "kStopCircleM" not in lib_text, "the one-size circle is back (plan defect 3)"
    assert playback.count("bool _within(") == 1, "the distance-within-radius spelling is not one"
    assert playback.count("bool _atPlace(") == 1, "the one at-the-place predicate is not one"
    at_place = _body_of(playback, "bool _atPlace(")
    assert "trigger" in at_place and "_within(" in at_place, at_place
    assert playback.count("_atPlace(") >= 5, "the readers do not all ask the one predicate"
    # No metre literal is ever compared against a distance in the service: every radius
    # the phone tests against came off the wire (a stop's trigger, the day's own place).
    literal = re.findall(r"haversineDistance\([^;]*?\)\s*(?:<=|<|>=|>)\s*\d", playback)
    assert not literal, f"a literal circle in the playback service: {literal}"
    assert re.search(r"\b(?:10|40)\.0\b", playback) is None, "a 10 m / 40 m literal survives"
    assert "ownPlaceM" in playback, "the start square / finish do not read the day's own place"
    # S7.7: the leg piece plays ON THE LEG — one start site, told once, off the wire's field.
    assert playback.count("void _maybeStartLeg(") == 1, "the leg piece has not one start site"
    assert "legAudioUrl" in model and "legAudioUrl" in _body_of(playback, "void _maybeStartLeg(")
    # S7.7 (B): the chapters — one model, one start site, the radius off the wire.
    assert "class StopSegment" in model and "final List<StopSegment> segments;" in model
    assert playback.count("void _maybeStartSegment(") == 1
    # RE-DERIVED at W7.13: `_chapterDue()` split into `_chaptersAtThisStop` /
    # `_chapterAtHand` / `_chapterOffered` for the panel's two chapter findings (an
    # unheard chapter stays on the screen — Aiko; a told one replays by tap where you
    # stand — Camille, R1(c)). The invariant is unchanged: the anchor's radius is read
    # through the ONE within-a-radius spelling, in the one at-hand check.
    assert "_within(" in _body_of(playback, "_chapterAtHand("), (
        "the chapter's radius is not read through the one within-a-radius spelling"
    )
    # W7.13 (F&D): the R6 cap and the R5 resume rule ride the WIRE's policy block —
    # the server decides them (place_day), the model carries them, and the phone's
    # readers consult the policy before any party stand-in.
    assert "sentence_cap_s" in py[placement] and "interruption_resume" in py[placement]
    assert "sentenceCapS" in model and "interruptionResume" in model
    assert "placement?.sentenceCapS" in playback
    assert "interruptionResumeByTap" in playback
