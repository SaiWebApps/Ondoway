"""Phase 5 session tests — the living session's PLANNER half. Hermetic.

One hand per file (the test_tour_clock.py model): this file is the SESSION hand on
the planner side — the replan context (S5.6) and the contingency set (S5.7). Every
gating test cites its source: design §4 (01-design.md:205-293), the W5.2 panel's
locked rulings (phase5-ledger.md, "LOCKED RULINGS", all eleven personas,
2026-08-18), the Phase-4 carried findings, and the persona lines they enforce.
"""

from __future__ import annotations

import math

import pytest

from src.tour.contract import POI, TourInput
from tests.test_tour_selection import PDV, _poi, _snap


def _at(center: tuple[float, float], distance_m: float, bearing_deg: float, pid: str, **kw) -> POI:
    dlat_m = 1.0 / 111_320.0
    dlng_m = 1.0 / (111_320.0 * math.cos(math.radians(center[0])))
    b = math.radians(bearing_deg)
    return _poi(
        pid,
        lat=center[0] + distance_m * math.cos(b) * dlat_m,
        lng=center[1] + distance_m * math.sin(b) * dlng_m,
        **kw,
    )


def _interior(poi: POI, *, outside_min: int, inside_min: int) -> POI:
    return poi.model_copy(
        update={"typical_duration_min": outside_min, "visit_seconds_inside": inside_min * 60}
    )


def _stand(poi: POI, minutes: int) -> POI:
    return poi.model_copy(update={"typical_duration_min": minutes})


def _bench(poi: POI, minutes: int = 8) -> POI:
    return poi.model_copy(
        update={"poi_role": "body", "tier": 1, "beat_count": 0, "typical_duration_min": minutes}
    )


# ---------------------------------------------------------------------------
# S5.6 — ReplanContext in THE one planner; "more breaks" as its first customer
# ---------------------------------------------------------------------------


def _carried_three_corpus():
    """A base day of one big interior anchor and two small stands, with two
    benches beside the path — the shape of Phase-4 CARRIED 3 (Place des Vosges:
    Carnavalet 47-inside dropped for Rue des Rosiers 25-outside to fund the rests,
    walking 23 -> 39, no rest promise)."""
    anchor = _interior(
        _at(PDV, 120.0, 20.0, "anchor", tier=5, beat_count=5), outside_min=8, inside_min=34
    )
    stand_b = _stand(_at(PDV, 160.0, 110.0, "stand-b", tier=4, beat_count=4), 9)
    stand_c = _stand(_at(PDV, 150.0, 250.0, "stand-c", tier=4, beat_count=4), 9)
    bench_1 = _bench(_at(PDV, 140.0, 65.0, "bench-1"))
    bench_2 = _bench(_at(PDV, 150.0, 300.0, "bench-2"))
    return anchor, stand_b, stand_c, bench_1, bench_2


def test_replan_context_is_an_input_to_the_one_planner_and_holds_what_it_protects():
    """Design §4.5.2/§4.5.3/§4.5.4 and §4.6 ("the server is the only place a plan
    decision is made ... one replan brain"): a replan is THE planner called with a
    `ReplanContext` — the base day's protected stops, the base day's longest leg
    as a ceiling, a zero floor for a tail, the learned rates — never a second
    planner. W5.2 R1.5 (Fiona & Dev, Julien): what is protected is what the PERSON
    asked for; the caller decides membership, the planner honours it.

    The behavioural half: a protected stop the fresh planner would DROP under a
    rest cadence (CARRIED 3's shape) is held under the context, a rest is seated,
    and no leg is longer than the base day's longest. UNDO: make the planner ignore
    `replan.protected_poi_ids` -> the anchor is dropped again -> RED.
    """
    from src.tour.contract import ReplanContext
    from src.tour.routing import longest_walk_minutes
    from src.tour.selection import select_route

    anchor, stand_b, stand_c, bench_1, bench_2 = _carried_three_corpus()
    snap = _snap([anchor, stand_b, stand_c, bench_1, bench_2])
    base_input = TourInput(start=PDV, duration_min=60, city_slug="paris", round_trip=True)

    base = select_route(base_input, snap)
    assert anchor.id in {p.id for p in base.pois}, "fixture premise: the anchor is on the base day"
    base_longest = longest_walk_minutes(base.transits)

    # THE CONTEXT — the base day's anchor held, its longest leg a ceiling.
    ctx = ReplanContext(
        protected_poi_ids=(anchor.id,),
        longest_leg_ceiling_seconds=max(
            (t.leg_seconds if t.leg_seconds is not None else t.walk_seconds) for t in base.transits
        ),
        floor_zero=False,
    )
    with_rests = select_route(
        base_input.model_copy(update={"rest_cadence_minutes": 8}), snap, replan=ctx
    )
    ids = [p.id for p in with_rests.pois]
    assert anchor.id in ids, f"the protected anchor was dropped to fund the rests: {ids}"
    assert with_rests.planned_visit_seconds[anchor.id] >= base.planned_visit_seconds[anchor.id], (
        "the anchor was SHAVED to fund the rests (W4.2 locked semantics 2)"
    )
    assert any(p.poi_role == "body" for p in with_rests.pois), f"no rest was seated: {ids}"
    assert longest_walk_minutes(with_rests.transits) <= max(base_longest, 1), (
        f"the replan lengthened the longest leg: {longest_walk_minutes(with_rests.transits)} > "
        f"{base_longest} (locked semantics 2: never lengthen the longest leg)"
    )


def test_more_breaks_on_the_wire_is_a_replan_of_the_base_day_not_a_fresh_plan():
    """Phase-4 CARRIED 3, the first customer: turning "More breaks" plans the SAME
    day again with the cadence added — the base day's anchor and longest leg held —
    rather than a fresh day that funds its rests by deleting the anchor and walking
    further (W5.1 (b)3: PdV walking 23 -> 39, longest 10 -> 18, Carnavalet 47-inside
    -> Rue des Rosiers 25-outside, no rest promise). The public planner door does
    the two calls itself, so every surface (workbench dial, phone, harness) gets the
    relative semantics without knowing about the context. UNDO: make `select_route`
    plan a cadenced request fresh -> the anchor goes -> RED (given the premise).
    """
    from src.tour.selection import select_route

    anchor, stand_b, stand_c, bench_1, bench_2 = _carried_three_corpus()
    snap = _snap([anchor, stand_b, stand_c, bench_1, bench_2])
    base_input = TourInput(start=PDV, duration_min=60, city_slug="paris", round_trip=True)
    base = select_route(base_input, snap)
    assert anchor.id in {p.id for p in base.pois}

    dialed = select_route(base_input.model_copy(update={"rest_cadence_minutes": 8}), snap)
    ids = [p.id for p in dialed.pois]
    assert anchor.id in ids, f"'More breaks' funded its rests by deleting the anchor: {ids}"
    assert dialed.planned_visit_seconds[anchor.id] >= base.planned_visit_seconds[anchor.id]
    assert any(p.poi_role == "body" for p in dialed.pois), f"no rest seated: {ids}"
    assert any(pr.kind == "rest" for pr in dialed.promises), (
        f"the rest is not a PROMISE on the day: {[pr.kind for pr in dialed.promises]}"
    )


def test_a_tail_replans_with_a_zero_floor_and_one_leg_home_is_a_legal_answer():
    """W5.1 defect 6 (5 of 6 "from here with the minutes left" cells refused, three
    ways) and W5.2 R1.1 (11/11): wrap-up-from-here has a floor of ZERO — it may be
    one leg home with no stop (Nadia: "one tap, no server, no speech"; Marcus;
    Sofia: "my tail is one lit leg plus 8 minutes on a corner, which has to be a
    legal answer"). Under a `ReplanContext` the density gate does not refuse a thin
    remainder, the underfill line does not bind, and a remainder with a fixed end
    and no stop that fits comes back as the walk to the finish — with the overrun
    REPORTED on the route, never a refusal (Fiona & Dev: no entry may be a 422).
    """
    from src.tour.contract import ReplanContext
    from src.tour.selection import select_route

    # A start with ONE far stand and a fixed end 400 m away: with 12 minutes left,
    # the walk home is ~10.8 min — the only honest answer is that walk.
    far = _stand(_at(PDV, 900.0, 0.0, "far", tier=5, beat_count=5), 20)
    end = _at(PDV, 400.0, 180.0, "end-marker")
    snap = _snap([far])
    tail = TourInput(
        start=PDV, end=(end.lat, end.lng), duration_min=12, city_slug="paris", round_trip=False
    )
    from src.tour.density import TourabilityRefusedError
    from src.tour.selection import CertificationPlanningInfeasibleError

    # The fresh planner refuses this tail today (W5.1 defect 6).
    with pytest.raises((TourabilityRefusedError, CertificationPlanningInfeasibleError)):
        select_route(tail, snap)
    day = select_route(tail, snap, replan=ReplanContext())
    assert all(p.id != far.id for p in day.pois), "a 20-minute stand does not fit 12 minutes"
    assert day.transits, "the tail must still be a walk to the finish"
    assert day.total_walk_seconds > 0


# ---------------------------------------------------------------------------
# S5.7 — the contingency set, computed once, on the server
# ---------------------------------------------------------------------------


def _wall_day_corpus():
    """A WALL day (Marcus's shape, 04-layover-sprinter.md) with a protected rest: three
    story stands, a bench the party asked for, and a finish 1.5 km on that the clock
    defends. Late out of the first stand, the fabric stands go SILENTLY first (R1.3);
    when only the bench and the walk home are left and they still do not fit, the ONE
    question asks about the rest — shorten it or keep it — never drops it (R2.3)."""
    stand_a = _stand(_at(PDV, 150.0, 20.0, "stand-a", tier=5, beat_count=5), 14)
    stand_b = _stand(_at(PDV, 320.0, 60.0, "stand-b", tier=4, beat_count=4), 12)
    bench = _bench(_at(PDV, 460.0, 80.0, "bench"), 10)
    stand_c = _stand(_at(PDV, 620.0, 90.0, "stand-c", tier=5, beat_count=5), 20)
    end = _at(PDV, 1500.0, 95.0, "end-marker")
    return stand_a, stand_b, bench, stand_c, end


def test_the_contingency_set_is_replans_of_the_planned_day_with_the_one_question():
    """Design §4.6 — "at plan time and after every replan [the server] emits a
    CONTINGENCY SET alongside the plan: precomputed answers to the divergences that
    matter — running late or early by bands, a stop skipped, a promise at risk,
    wrap-up from here — each with its alternates". W5.2 R1 (coverage): wrap-up from
    EVERY stop with a zero floor (11/11); a skip for every stop whose answer never
    adds a building (R1.2); late bands at 10/20/30-40 (R1.3); early bands that only
    lengthen what is there (R1.4); promise-at-risk only for what the PERSON asked for
    (R1.5). W5.2 R2 (the one question): one sentence, TWO arms, EACH with the place
    kept and a CLOCK TIME; the safe arm first under a wall (Marcus); none of the
    banned words; a protected rest is shortened or kept, never an arm to be dropped
    (Rosemary, R2.3); everything spoken is on screen (§4.4.2). W5.1 (d): the spend is
    the union of NEW stops across entries, printed before it is billed — and with
    every entry drawn from the planned day it is ZERO.

    Every entry is THE planner (`plan_premium_tour`) called with a `ReplanContext` —
    a contingency IS a replan taken early (Extends). UNDO: make an entry reuse another
    band's answer, or drop the question's screen text -> RED.
    """
    from src.tour.contingency import BANNED_WORDS, build_contingency_set
    from src.tour.contract import ReplanContext
    from src.tour.selection import select_route

    stand_a, stand_b, bench, stand_c, end = _wall_day_corpus()
    snap = _snap([stand_a, stand_b, bench, stand_c])
    request = TourInput(
        start=PDV,
        end=(end.lat, end.lng),
        duration_min=110,
        city_slug="paris",
        round_trip=False,
        start_datetime="2026-08-19T14:00",
        end_hardness="wall",
        rest_cadence_minutes=6,
    )
    base = select_route(request, snap)
    ids = [p.id for p in base.pois]
    assert bench.id in ids and stand_c.id in ids and stand_a.id in ids, f"premise: {ids}"

    # THE PERSON'S PROTECTED SET (R1.5): the rest they asked for and the finish.
    protected = ReplanContext(protected_poi_ids=(bench.id,))
    cset = build_contingency_set(base, request, snap, routing_client=None, person=protected)

    kinds = {}
    for entry in cset.entries:
        kinds.setdefault(entry.trigger["kind"], []).append(entry)
    story_stops = [p for p in base.pois if p.poi_role != "body" and not p.id.startswith("__")]

    # COVERAGE — R1.1 wrap-up and R1.2 skip from every stop; late/early bands present.
    assert len(kinds.get("wrap_up_from", [])) >= len(story_stops), kinds.keys()
    assert len(kinds.get("stop_skipped", [])) >= len(story_stops), kinds.keys()
    assert {tuple(e.trigger["band_minutes"]) for e in kinds.get("running_late", [])} >= {
        (10, 20), (20, 30), (30, 40)
    }, [e.trigger for e in kinds.get("running_late", [])]
    assert {tuple(e.trigger["band_minutes"]) for e in kinds.get("running_early", [])} >= {
        (10, 20), (20, 40)
    }
    # No entry adds a building: every stop of every entry was on the planned day.
    planned = {p.id for p in base.pois}
    for entry in cset.entries:
        assert set(entry.stop_ids) <= planned, (entry.trigger, entry.stop_ids)
    assert cset.authoring_units == 0, "no new stop, no new authoring — the spend is zero"
    # Every entry speaks on screen; a question never travels without screen text.
    for entry in cset.entries:
        assert entry.screen_text.strip(), entry.trigger
        if entry.question is not None:
            assert entry.screen_text.strip(), entry.trigger

    # THE ONE QUESTION — running late out of the first stand on a WALL day. Fabric goes
    # SILENTLY first (R1.3): the earliest band's answer differs from the base day and
    # keeps the rest. Deeper in the bands only the rest and the walk home remain and
    # they no longer fit: the question names the rest in the panel's words — two arms,
    # two clocks, safe arm first, one sentence, no banned word.
    from_first = [e for e in kinds["running_late"] if e.from_stop_index == 0]
    earliest = next(e for e in from_first if tuple(e.trigger["band_minutes"]) == (10, 20))
    assert set(earliest.stop_ids) != planned, "the late answer is the base day — no answer"
    assert bench.id in earliest.stop_ids, "the protected rest was dropped by arithmetic (§4.5.2)"
    asked = [e for e in from_first if e.question is not None]
    assert asked, (
        "no late band asked the one question about the rest: "
        + "; ".join(
            f"{e.trigger['band_minutes']}: {e.stop_ids} overrun "
            f"{e.route.overrun_seconds if e.route else None}"
            for e in from_first
        )
    )
    late = asked[0]
    assert late.at_risk_stop_id == bench.id
    import re

    clocks = re.findall(r"\b\d{1,2}:\d{2}\b", late.question)
    assert len(clocks) >= 2, f"two arms, two clock times: {late.question!r}"
    assert late.question.count("?") == 1 and ". " not in late.question, (
        f"one sentence, maximum: {late.question!r}"
    )
    lowered = late.question.lower()
    for word in BANNED_WORDS:
        assert word not in lowered, f"banned word {word!r} in {late.question!r}"
    # A wall day puts the SAFE arm first (Marcus) and defaults to it.
    assert late.default_arm == "shorten", late.default_arm
    assert late.question.lower().startswith(("sit ", "go ")), late.question


# ---------------------------------------------------------------------------
# S5.10 — the server half of the SESSION CLOCK seam: ONE re-timing expression,
# priced at the person's learned listening rate; the finish is a VIEW of it
# ---------------------------------------------------------------------------


def test_the_servers_one_clock_prices_narration_at_the_learned_rate_and_the_finish_is_its_view():
    """Design §4.1 (Paulo: "consumes narration at ~1.5x its stated length ... the
    remaining day's estimates scale to the observed rate"); §4.6 (the seam: ONE
    re-timing expression on the server, `stop_clocks`, and nothing else may spell
    its clock); plan S5.10 ("two re-timing expressions that agree today by
    coincidence" is sabotage). A stop costs the LONGER of its priced visit and what
    it says at the learned rate — the final gate's own rule (`stop_seconds`), so
    the certified day and the wire clock are one quantity — and `finish_clock` is
    the last departure plus the closing leg read off the SAME walk, never a second
    sum. UNDO: sum walks and visits by hand in `finish_clock` (drop the rate) ->
    RED on the shift; ignore the audio map in `stop_clocks` -> RED on the 60 s.
    """
    from src.tour.contingency import finish_clock, stop_clocks
    from src.tour.routing import leg_walk_seconds
    from src.tour.selection import select_route

    stand_a, stand_b, bench, stand_c, end = _wall_day_corpus()
    snap = _snap([stand_a, stand_b, bench, stand_c])
    request = TourInput(
        start=PDV,
        end=(end.lat, end.lng),
        duration_min=110,
        city_slug="paris",
        round_trip=False,
        start_datetime="2026-08-19T14:00",
        end_hardness="wall",
        rest_cadence_minutes=6,
    )
    route = select_route(request, snap)
    ids = [p.id for p in route.pois]
    assert ids[0] == stand_a.id and stand_b.id in ids, f"premise: {ids}"
    visit_a = int(route.planned_visit_seconds[stand_a.id])
    assert visit_a == 14 * 60, visit_a
    # Fifteen minutes of narration at a fourteen-minute stand: the stop says
    # more than the visit, so what it SAYS is what it costs.
    audio = {stand_a.id: 900}
    by = lambda clocks: {p.id: (arr, dep) for p, arr, dep in clocks}  # noqa: E731
    plain = by(stop_clocks(route, request))
    heard = by(stop_clocks(route, request, audio_seconds_by_id=audio))
    paulo = by(stop_clocks(route, request, audio_seconds_by_id=audio, listening_rate=1.5))
    # rate 1.0: max(840, 900) - 840 = 60 s later at every later stop.
    assert (heard[stand_b.id][0] - plain[stand_b.id][0]).total_seconds() == 60
    # rate 1.5: max(840, 1350) - 900 = 450 s later still — Paulo's overrun is
    # priced once, here, not rediscovered stop by stop.
    assert (paulo[stand_b.id][0] - heard[stand_b.id][0]).total_seconds() == 450
    assert paulo[stand_a.id][0] == plain[stand_a.id][0]  # the arrival AT the stop is unchanged
    # The finish is a VIEW: last departure + whatever walk is not already inside
    # the stops' own legs. A declared finish is MATERIALIZED as the last stop (its
    # sentinel), so here that closing remainder is zero and the finish IS the
    # sentinel's arrival — the same view, read once.
    assert route.pois[-1].id.startswith("__end_b"), [p.id for p in route.pois]
    into = sum(leg_walk_seconds(t) for t in route.transits[: len(route.pois)])
    closing = route.total_walk_seconds - into
    start = paulo[stand_a.id][0] - __import__("datetime").timedelta(
        seconds=leg_walk_seconds(route.transits[0])
    )
    fin = finish_clock(
        route, request, clock_start=start, audio_seconds_by_id=audio, listening_rate=1.5
    )
    last_departure = max(dep for _arr, dep in paulo.values())
    assert (fin - last_departure).total_seconds() == closing
    fin_plain = finish_clock(route, request, clock_start=start)
    assert (fin - fin_plain).total_seconds() == 510  # 60 + 450, the same shift, once


def test_a_replan_prices_the_day_at_the_persons_listening_rate():
    """Design §4.1 (the learned listening rate scales the remaining day's estimates)
    through THE planner (§4.6): the ReplanContext S5.6 threaded carried
    `listening_rate` and nothing consumed it. A tail whose three protected stands
    say more than they stand (270 s of narration at two-minute stands) FITS its
    minutes at rate 1.0 and OVERRUNS them at rate 2.0 — reported on the route
    (floor zero, S5.6), never refused — because every trial and the final gate
    price `max(visit, audio x rate)`. UNDO: drop `listening_rate=` at the final
    gate -> the second half goes RED (overrun 0 at rate 2.0)."""
    from src.tour.contract import ReplanContext
    from src.tour.selection import select_route

    stands = [
        _stand(_at(PDV, d, 20.0, f"say-{i}", tier=5, beat_count=5), 2)
        for i, d in enumerate((150.0, 300.0, 450.0))
    ]
    snap = _snap(stands)
    keep = tuple(s.id for s in stands)

    def tail(rate: float):
        ctx = ReplanContext(
            protected_poi_ids=keep, keep_to_poi_ids=keep, floor_zero=True, listening_rate=rate
        )
        return select_route(
            TourInput(
                start=PDV,
                duration_min=27,
                city_slug="paris",
                round_trip=False,
                start_datetime="2026-08-19T14:00",
            ),
            snap,
            replan=ctx,
        )

    normal, paulo = tail(1.0), tail(2.0)
    assert [p.id for p in normal.pois] == list(keep), [p.id for p in normal.pois]
    assert [p.id for p in paulo.pois] == list(keep), [p.id for p in paulo.pois]
    assert normal.overrun_seconds == 0, normal.overrun_seconds
    # RE-DERIVED at Phase 6 S6.6 (the three-minute tight): the old band (700-900)
    # hardcoded the 270-era fixture arithmetic (its beats derive their length from
    # MAX_DWELL_AUDIO_SECONDS, so the ceiling move changed what the day voices).
    # The MECHANISM is what this test pins, not the pricing formula: a doubled
    # rate must overrun a day that fits at 1.0, and a tripled rate must overrun
    # more than a doubled one — monotone in the rate, reported never refused.
    assert paulo.overrun_seconds > 0, "rate 2.0 must overrun the same day"
    heavier = tail(3.0)
    assert heavier.overrun_seconds > paulo.overrun_seconds, (
        heavier.overrun_seconds,
        paulo.overrun_seconds,
    )


# ---------------------------------------------------------------------------
# S5.11 — announcement etiquette: what the SERVER and the WIRE can enforce
# ---------------------------------------------------------------------------


def test_the_set_and_the_wire_enforce_the_etiquette_they_can():
    """Design §4.4 (the four hard rules): 2 — everything spoken also appears on
    screen; 4 — ONE sentence, maximum ("over a screaming five-year-old, mute beats
    graceful"). W5.2 R2.1 — the question is ONE sentence with TWO arms, each naming
    the place kept and a clock time; R2.4/R2.5 — asked once, on screen as two big
    buttons with the DEFAULT named. Plan S5.11: "the wire enforces what it can:
    `screen_text` non-empty whenever `question` is, and a sentence-count validator
    on the question"; sabotage: "a two-sentence question that explains itself".
    UNDO: drop `one_sentence` from the set's `add` -> the built-set half goes RED
    on a two-sentence screen line; drop the model validator -> the wire half RED.
    """
    import pytest
    from pydantic import ValidationError

    from src.api.models.trips import SessionContingency
    from src.tour.contingency import one_sentence

    # The one-sentence rule: a clock is not a sentence break; a separator is not.
    assert one_sentence("Next: the bench · your finish about 15:09") == (
        "Next: the bench · your finish about 15:09"
    )
    q = (
        "Keep your full rest and be at the Orsay about 17:05, "
        "or sit 4 minutes and be there by 16:55?"
    )
    assert one_sentence(q) == q
    with pytest.raises(ValueError, match="ONE sentence"):
        one_sentence("We noticed the time. Keep the bench, or go on?")
    with pytest.raises(ValueError, match="ONE sentence"):
        one_sentence("Carry on. Nothing changes from here.")

    # The wire's last door before the phone.
    ok = SessionContingency(
        contingency_id="v1-1",
        trigger={"kind": "promise_at_risk", "stop_id": "bench"},
        plan_version=1,
        screen_text=q,
        question=q,
        default_arm="keep",
    )
    assert ok.question == q
    with pytest.raises(ValidationError, match="also on screen"):
        SessionContingency(
            contingency_id="v1-2",
            trigger={"kind": "wrap_up_from", "stop_id": "a"},
            plan_version=1,
            screen_text="   ",
        )
    with pytest.raises(ValidationError, match="ONE sentence"):
        SessionContingency(
            contingency_id="v1-3",
            trigger={"kind": "promise_at_risk", "stop_id": "bench"},
            plan_version=1,
            screen_text="x",
            question="The bench will make you late. Keep it, or go on?",
            default_arm="keep",
        )
    with pytest.raises(ValidationError, match="default arm"):
        SessionContingency(
            contingency_id="v1-4",
            trigger={"kind": "promise_at_risk", "stop_id": "bench"},
            plan_version=1,
            screen_text=q,
            question=q,
        )

    # And the set the server builds obeys both on a real day: every line one
    # sentence, every question two arms with clocks and a default, on screen.
    from src.tour.contingency import build_contingency_set
    from src.tour.contract import ReplanContext
    from src.tour.selection import select_route

    stand_a, stand_b, bench, stand_c, end = _wall_day_corpus()
    snap = _snap([stand_a, stand_b, bench, stand_c])
    request = TourInput(
        start=PDV,
        end=(end.lat, end.lng),
        duration_min=110,
        city_slug="paris",
        round_trip=False,
        start_datetime="2026-08-19T14:00",
        end_hardness="wall",
        rest_cadence_minutes=6,
    )
    base = select_route(request, snap)
    cset = build_contingency_set(
        base,
        request,
        snap,
        routing_client=None,
        person=ReplanContext(protected_poi_ids=(bench.id,)),
    )
    questions = [e for e in cset.entries if e.question]
    assert questions, "premise: the wall day asks about the bench"
    for e in cset.entries:
        one_sentence(e.screen_text)
        assert e.screen_text.strip()
        if e.question:
            one_sentence(e.question)
            assert e.question.endswith("?") and ", or " in e.question
            assert e.default_arm in ("keep", "shorten")
            # every arm names a clock time (R2.1)
            assert all(":" in arm for arm in e.question.split(", or ")), e.question


# ---------------------------------------------------------------------------
# W5.14 / S5.16 — the place the person named is a promise; the live path ASKS
# ---------------------------------------------------------------------------


def test_the_place_the_person_named_as_start_or_finish_is_a_promise():
    """W5.14, all eleven: on an art-lens day AT the Orsay, the live replan dropped the
    Orsay to keep an 8-minute bench — R1.5 as locked ("never the planner's anchor on
    an open unpinned walk") applied to a place the PERSON named. Rosemary: "The Orsay
    is where I said I begin and end". So: a story stop within 60 m of where the person
    said the day begins or ends is a promise (`own_place_ids`); the planner's own
    anchor elsewhere is not. The set treats it as protected: every entry keeps it,
    and the question is about the REST first (R2.3 — the thing that can be shortened).
    UNDO: return () from own_place_ids -> the first assertion goes RED."""
    from src.tour.contingency import at_risk_choice, build_contingency_set, own_place_ids
    from src.tour.contract import ReplanContext
    from src.tour.selection import select_route

    # A round trip that BEGINS at a story stop (the museum), with a bench and a
    # second stand along the way.
    museum = _stand(_at(PDV, 0.0, 0.0, "museum", tier=5, beat_count=5), 40)
    bench = _bench(_at(PDV, 250.0, 30.0, "bench"), 8)
    stand = _stand(_at(PDV, 450.0, 40.0, "stand", tier=4, beat_count=4), 12)
    snap = _snap([museum, bench, stand])
    request = TourInput(
        start=PDV,
        duration_min=110,
        city_slug="paris",
        round_trip=True,
        start_datetime="2026-08-19T14:00",
        rest_cadence_minutes=6,
    )
    route = select_route(request, snap)
    ids = [p.id for p in route.pois]
    assert museum.id in ids and bench.id in ids, f"premise: {ids}"
    assert own_place_ids(route, request) == (museum.id,)
    # The planner's anchor elsewhere is not the person's place.
    away = request.model_copy(update={"start": (PDV[0] + 0.01, PDV[1] + 0.01), "round_trip": False})
    assert own_place_ids(route, away) == ()
    # The question is about the rest first: it can be shortened, the museum cannot.
    assert at_risk_choice(route, [museum.id, bench.id]).id == bench.id
    assert at_risk_choice(route, [museum.id]).id == museum.id
    # In the built set, no entry from a stop BEFORE the museum drops the museum.
    cset = build_contingency_set(
        route, request, snap, routing_client=None, person=ReplanContext(protected_poi_ids=())
    )
    museum_at = ids.index(museum.id)
    for e in cset.entries:
        if e.from_stop_index < museum_at and e.route is not None and e.route.pois:
            assert museum.id in e.stop_ids or e.trigger["kind"] == "wrap_up_from", (
                e.trigger,
                e.stop_ids,
            )


def test_the_finish_is_named_as_the_place_when_the_person_named_one():
    """W5.14 / S5.19 (Camille, Aiko, Rosemary, Greta, Paulo): "'your finish' is plan
    vocabulary — say 'the Orsay'". The finish's name in every line and arm is the PLACE
    when the day ends at a stop the person named (a round trip from a story POI, or a
    declared end that is a stop of the day); "your finish" / "your start" only when the
    day ends somewhere unnamed. The route's own places decide, not the request's words.
    UNDO: return the generic words regardless of the route -> RED."""
    from src.api.routes.trips import _finish_name
    from src.tour.selection import select_route

    museum = _stand(_at(PDV, 0.0, 0.0, "museum", tier=5, beat_count=5), 40)
    bench = _bench(_at(PDV, 250.0, 30.0, "bench"), 8)
    stand = _stand(_at(PDV, 450.0, 40.0, "stand", tier=4, beat_count=4), 12)
    snap = _snap([museum, bench, stand])
    round_trip = TourInput(
        start=PDV,
        duration_min=110,
        city_slug="paris",
        round_trip=True,
        start_datetime="2026-08-19T14:00",
        rest_cadence_minutes=6,
    )
    route = select_route(round_trip, snap)
    assert museum.id in {p.id for p in route.pois}, "premise: the day visits the start's museum"
    assert _finish_name(round_trip, route) == museum.name
    # Without the route there is nothing to name: the generic words.
    assert _finish_name(round_trip) == "your start"
    # An open walk from an unnamed corner: generic words even with the route.
    away = round_trip.model_copy(
        update={"start": (PDV[0] + 0.01, PDV[1] + 0.01), "round_trip": False}
    )
    assert _finish_name(away, route) == "your start"
    declared = round_trip.model_copy(update={"round_trip": False, "end": (PDV[0] + 0.01, PDV[1])})
    assert _finish_name(declared, route) == "your finish"


# ---------------------------------------------------------------------------
# Phase 6 S6.4 — the wrap-up entry: what the screen says, and from where
# ---------------------------------------------------------------------------


def test_a_wrap_up_on_an_open_walk_never_says_straight_to_your_start():
    """W6.2 R2/R8 (11/11): "'Straight to your start' is a direction to nowhere" (Nadia,
    Marcus, Julien, F&D — 09:10 they restart where the conversation pauses and end at
    dinner, wherever that is). On an OPEN one-way walk a wrap-up has no finish to head
    for: the screen line says what the day was and that the rest of it is theirs from
    here; it never names a start or a finish. On a day WITH a finish the line still
    heads for the place (Place des Vosges, Notre-Dame — S5.19). UNDO: fall back to
    `_finish_name`'s "your start" -> RED."""
    from src.tour.contingency import build_contingency_set
    from src.tour.contract import ReplanContext
    from src.tour.selection import select_route

    stand_a, stand_b, bench, stand_c, _end = _wall_day_corpus()
    snap = _snap([stand_a, stand_b, bench, stand_c])
    open_walk = TourInput(
        start=PDV, duration_min=110, city_slug="paris", round_trip=False,
        start_datetime="2026-08-23T15:00", end_hardness="open",
    )
    base = select_route(open_walk, snap)
    cset = build_contingency_set(
        base, open_walk, snap, routing_client=None, person=ReplanContext(), finish_name="your start"
    )
    wraps = [e for e in cset.entries if e.trigger["kind"] == "wrap_up_from"]
    assert wraps, "premise: wrap-up entries exist"
    for e in wraps:
        low = e.screen_text.lower()
        assert "your start" not in low and "straight to" not in low, e.screen_text
        assert "yours" in low or "walk" in low, e.screen_text  # the day handed back
    # Every other entry on the open walk keeps the same rule — no "Straight to your start".
    for e in cset.entries:
        assert "your start" not in e.screen_text.lower(), (e.trigger, e.screen_text)


def test_a_rest_has_its_own_wrap_up_entry():
    """Rosemary (05, step 3: "The bench is a stop") and W6.2 R2: "RO13 has a [Head back
    now] entry at the Orangerie and the Orsay and none at the bench — the one place I
    am most likely to decide to go home is where the button does nothing." A wrap-up
    entry exists FROM every stop the person can be at — rests included. UNDO: build
    wrap-ups from story stops only -> RED."""
    from src.tour.contingency import build_contingency_set
    from src.tour.contract import ReplanContext
    from src.tour.selection import select_route

    stand_a, stand_b, bench, stand_c, end = _wall_day_corpus()
    snap = _snap([stand_a, stand_b, bench, stand_c])
    request = TourInput(
        start=PDV, end=(end.lat, end.lng), duration_min=110, city_slug="paris",
        round_trip=False, start_datetime="2026-08-19T14:00", rest_cadence_minutes=6,
    )
    base = select_route(request, snap)
    assert bench.id in {p.id for p in base.pois}, "premise: the bench is on the day"
    cset = build_contingency_set(
        base, request, snap, routing_client=None,
        person=ReplanContext(protected_poi_ids=(bench.id,)),
    )
    wrap_from = {e.trigger["stop_id"] for e in cset.entries if e.trigger["kind"] == "wrap_up_from"}
    assert bench.id in wrap_from, sorted(wrap_from)


def test_the_banned_list_binds_words_not_fragments():
    """W5.2 R2.2's list binds plan-words on screen and aloud; the W6.2 panel found the
    check reads LETTERS: "wallpaper" carries "wall", "husband" "band", "compromise"
    "promise", "later" "late", "darkness" "dark" (Théo, Camille: "bind the planner's
    meanings, not the letters, or my closes can't say true things"). A close is narrator
    content and goes through the same door. UNDO: substring matching -> RED."""
    import pytest

    from src.tour.contingency import plain

    for ok in (
        "The wallpaper in the Queen's cell is a reconstruction.",
        "Her husband was killed in a duel in 1651.",
        "No compromise was possible; later that year the kings left.",
        "The nave is cool and the aisles are in darkness by four.",
    ):
        assert plain(ok) == ok
    for banned in (
        "You are running late.",
        "We hit a wall at 16:40.",
        "That is a promise we keep.",
        "The band is 10 to 20 minutes.",
    ):
        with pytest.raises(ValueError):
            plain(banned)


def test_a_wrap_up_from_the_place_the_day_ends_at_does_not_send_you_there():
    """W6.2 R2 (Rosemary): "my wrap-up at the Orsay shows 'Straight to Musée d'Orsay' to
    a woman standing in the Orsay — the day's close is what belongs there." When the
    wrap-up is from the stop the day ends at (the start of a round trip that is a stop,
    a declared end that is a stop), the screen says the walk is done — never a direction
    to where they stand. UNDO: "Straight to <finish>" regardless -> RED."""
    from src.tour.contingency import build_contingency_set
    from src.tour.contract import ReplanContext
    from src.tour.selection import select_route

    museum = _stand(_at(PDV, 0.0, 0.0, "museum", tier=5, beat_count=5), 40)
    bench = _bench(_at(PDV, 250.0, 30.0, "bench"), 8)
    stand = _stand(_at(PDV, 450.0, 40.0, "stand", tier=4, beat_count=4), 12)
    snap = _snap([museum, bench, stand])
    round_trip = TourInput(
        start=PDV, duration_min=110, city_slug="paris", round_trip=True,
        start_datetime="2026-08-19T14:00", rest_cadence_minutes=6,
    )
    route = select_route(round_trip, snap)
    ids = [p.id for p in route.pois]
    assert museum.id in ids, "premise: the day visits the start's museum"
    cset = build_contingency_set(
        route, round_trip, snap, routing_client=None, person=ReplanContext(),
        finish_name=museum.name,
    )
    wraps = {e.trigger["stop_id"]: e for e in cset.entries if e.trigger["kind"] == "wrap_up_from"}
    assert museum.id in wraps
    at_finish = wraps[museum.id].screen_text.lower()
    assert "straight to" not in at_finish and museum.name.lower() not in at_finish, at_finish
    assert "walk" in at_finish, at_finish
    # From a stop that is NOT the finish, the line still heads for the place.
    others = [e for sid, e in wraps.items() if sid != museum.id]
    assert others and all("Straight to" in e.screen_text for e in others), [
        e.screen_text for e in others
    ]


def test_skipping_an_uncategorised_stop_spends_no_category():
    """Phase 6 W6.11's demo found the class: a skip marks the skipped stop's
    place_category SPENT (Greta's satiation — "galleries spent, churches down"),
    but most of the corpus carries the default category 'other', so skipping ONE
    uncategorised stop marked 'other' spent and the tail replan seated NOTHING —
    measured on the F&D day: every skip entry after the first shipped stop_ids []
    while its own screen text promised "Next: Conciergerie". The absence of a
    category is not a category: 'other' (and None) is never spent. A REAL
    category still spends. UNDO: spend 'other' again -> RED."""
    from src.tour.contingency import build_contingency_set
    from src.tour.contract import ReplanContext
    from src.tour.selection import select_route

    stand_a, stand_b, bench, stand_c, end = _wall_day_corpus()
    # stand_a carries no real category (the corpus default); stand_b carries one.
    stand_a = stand_a.model_copy(update={"place_category": "other"})
    stand_b = stand_b.model_copy(update={"place_category": "museum"})
    snap = _snap([stand_a, stand_b, bench, stand_c])
    request = TourInput(
        start=PDV,
        end=(end.lat, end.lng),
        duration_min=110,
        city_slug="paris",
        round_trip=False,
        start_datetime="2026-08-19T14:00",
        rest_cadence_minutes=6,
    )
    base = select_route(request, snap)
    assert stand_a.id in [p.id for p in base.pois], "premise: the 'other' stop is seated"

    seen: list[tuple[str, tuple]] = []
    real_select = select_route

    def spy_plan(inp, ctx):
        seen.append((f"{inp.start[0]:.5f}", tuple(ctx.spent_categories)))
        return real_select(inp, snap, replan=ctx)

    build_contingency_set(
        base, request, snap, routing_client=None,
        person=ReplanContext(), plan_version=1, plan=spy_plan,
    )
    all_spent = [cats for _pos, cats in seen]
    assert not any("other" in cats for cats in all_spent), (
        f"'other' must never be spent; ctx rows: {all_spent}"
    )
    assert any("museum" in cats for cats in all_spent), (
        "a REAL category is still spent when its stop is skipped"
    )


def test_a_keep_constrained_tail_spends_its_whole_walking_budget():
    """Phase 6 W6.11's demo found the second half of the empty-tail class: on an
    open one-way day the greedy RESERVES a quarter of the walking budget for the
    endpoint pull — but a replan tail with `keep_to_poi_ids` set can never pull
    (every non-keep candidate is filtered before the pull ranks them), so the
    reserve starved tails for nothing. Measured on the F&D day: the skip-of-BHV
    tail (one keep, Place des Vosges, a 1291 s walk) seated NOTHING below a
    72-minute remainder because 0.75 x its walking budget fell 157 s short —
    while the entry's own screen text promised "Next: Place des Vosges". A
    keep-constrained tail gets the whole allocation. UNDO: reserve again on
    keep-constrained tails -> RED."""
    from src.tour.contract import ReplanContext
    from src.tour.routing import walk_budget_seconds
    from src.tour.selection import select_route

    # One keepable stop whose straight walk costs ~0.8 of the walking budget:
    # over the 0.75 the pull reserve leaves, under the budget itself.
    minutes = 30
    budget = walk_budget_seconds(minutes)
    # default_leg_seconds is pace-corrected haversine; place the keep by metres.
    from src.tour.routing import pace_corrected_walk_seconds

    target_walk = int(budget * 0.8)
    metres = 100.0
    while pace_corrected_walk_seconds(metres) < target_walk:
        metres += 25.0
    far_keep = _stand(_at(PDV, metres, 0.0, "far-keep", tier=5, beat_count=5), 5)
    # Background anchors so the DENSITY gate sees a live area (they are all
    # filtered by keep_to before seating — only the far keep may seat).
    background = [
        _stand(_at(PDV, 60.0 + 30.0 * i, 90.0, f"bg-{i}", tier=4, beat_count=3), 5)
        for i in range(8)
    ]
    snap = _snap([far_keep, *background])
    ctx = ReplanContext(keep_to_poi_ids=(far_keep.id,), floor_zero=True)
    tail = select_route(
        TourInput(
            start=PDV, duration_min=minutes, city_slug="paris", round_trip=False,
            start_datetime="2026-08-19T16:00",
        ),
        snap,
        replan=ctx,
    )
    assert [p.id for p in tail.pois] == [far_keep.id], (
        f"the keep must be seated — walk {pace_corrected_walk_seconds(metres)}s "
        f"against budget {budget}s; got {[p.id for p in tail.pois]}"
    )


# ---------------------------------------------------------------------------
# M6 — a guessed stop yields a door_closed entry naming an open map place
# ---------------------------------------------------------------------------


def test_a_guessed_stop_yields_a_door_closed_entry_naming_an_open_map_place():
    """Docs/adr/0006 rule 4, M6: every story stop whose hours are a GUESS
    carries a ``door_closed`` entry in the contingency set. The entry names a
    STANDBY — a map-sourced place open at arrival — as the alternate the walker
    can carry to if the door turns out to be shut. The question is one sentence
    with keep first (default_arm="keep"); the standby is a NEW stop so
    authoring_units > 0.

    UNDO: remove the door_closed loop from build_contingency_set -> RED.
    """
    from src.tour.contingency import BANNED_WORDS, build_contingency_set
    from src.tour.contract import ReplanContext
    from src.tour.selection import select_route

    # A corpus with one guessed stop on the day and one map-sourced standby
    # candidate NOT on the day. The guessed stop is high-tier and on-path so
    # the planner picks it; the standby is low-tier and off-path so the base
    # route ignores it but a tail from the guessed stop's position can reach it.
    stand_map = _stand(_at(PDV, 150.0, 20.0, "stand-map", tier=5, beat_count=5), 14)
    stand_guess = _interior(
        _at(PDV, 320.0, 60.0, "stand-guess", tier=5, beat_count=5),
        outside_min=5,
        inside_min=7,
    )
    stand_guess = stand_guess.model_copy(
        update={
            "opening_hours": "Mo-Su 09:00-18:00",
            "opening_hours_source": "guess",
            "place_category": "museum",
        }
    )
    bench = _bench(_at(PDV, 460.0, 80.0, "bench-m6"), 10)
    stand_end = _stand(_at(PDV, 620.0, 90.0, "stand-end", tier=5, beat_count=5), 20)
    end = _at(PDV, 1500.0, 95.0, "end-m6")

    # A standby candidate: map-sourced, open wide, low-tier and off the base
    # route's bearing so it is NOT picked for the base day.
    standby = _stand(_at(PDV, 400.0, 70.0, "standby-open", tier=3, beat_count=3), 10)
    standby = standby.model_copy(
        update={
            "opening_hours": "Mo-Su 06:00-22:00",
            "opening_hours_source": "map",
        }
    )

    snap = _snap([stand_map, stand_guess, bench, stand_end, standby])
    request = TourInput(
        start=PDV,
        end=(end.lat, end.lng),
        duration_min=110,
        city_slug="paris",
        round_trip=False,
        start_datetime="2026-08-19T14:00",
        end_hardness="wall",
        rest_cadence_minutes=6,
    )
    base = select_route(request, snap)
    ids = [p.id for p in base.pois]
    assert stand_guess.id in ids, f"premise: guessed stop must be on the day; got {ids}"
    assert "standby-open" not in ids, f"premise: standby must NOT be on base day; got {ids}"
    assert base.visit_goes_inside.get(stand_guess.id), (
        f"premise: stand_guess must go inside; visit_goes_inside={base.visit_goes_inside}"
    )

    protected = ReplanContext(protected_poi_ids=(bench.id,))
    cset = build_contingency_set(base, request, snap, routing_client=None, person=protected)

    kinds = {}
    for entry in cset.entries:
        kinds.setdefault(entry.trigger["kind"], []).append(entry)

    # A door_closed entry exists for the guessed stop.
    assert "door_closed" in kinds, f"no door_closed entry; kinds present: {list(kinds.keys())}"
    dc = [e for e in kinds["door_closed"] if e.trigger["stop_id"] == stand_guess.id]
    assert dc, f"no door_closed for {stand_guess.id}"
    entry = dc[0]

    # The alternate names a map-sourced place (the standby).
    assert entry.alternate_stop_ids, "no alternate — no standby offered"
    planned = {p.id for p in base.pois}
    new_ids = set(entry.alternate_stop_ids) - planned
    assert new_ids, "the alternate should name a NEW stop (the standby)"

    # The standby counts toward authoring_units.
    assert cset.authoring_units > 0, "the standby is a new stop — authoring spend > 0"

    # The question is one sentence, keep first, no banned words.
    assert entry.question is not None, "door_closed must carry the ONE question"
    assert entry.default_arm == "keep", f"default must be keep; got {entry.default_arm}"
    assert entry.question.count("?") == 1 and ". " not in entry.question, (
        f"one sentence, maximum: {entry.question!r}"
    )
    lowered = entry.question.lower()
    for word in BANNED_WORDS:
        assert word not in lowered, f"banned word {word!r} in {entry.question!r}"


def test_a_nearer_shut_standby_is_skipped_for_the_farther_open_one():
    """M6 openness pre-filter: a map-sourced candidate NEARER to the guessed stop
    but CLOSED at arrival is never offered — the chooser picks the farther open one.
    UNDO: remove the _clock_exclusion_reason filter from the min() generator -> RED
    (the nearer shut candidate wins by distance).
    """
    from src.tour.contingency import build_contingency_set
    from src.tour.contract import ReplanContext
    from src.tour.selection import select_route

    stand_map = _stand(_at(PDV, 150.0, 20.0, "stand-map", tier=5, beat_count=5), 14)
    stand_guess = _interior(
        _at(PDV, 320.0, 60.0, "stand-guess2", tier=5, beat_count=5),
        outside_min=5,
        inside_min=7,
    )
    stand_guess = stand_guess.model_copy(
        update={
            "opening_hours": "Mo-Su 09:00-18:00",
            "opening_hours_source": "guess",
            "place_category": "museum",
        }
    )
    bench = _bench(_at(PDV, 460.0, 80.0, "bench-m6b"), 10)
    stand_end = _stand(_at(PDV, 620.0, 90.0, "stand-end2", tier=5, beat_count=5), 20)
    end = _at(PDV, 1500.0, 95.0, "end-m6b")

    # NEARER candidate — closed at 14:24 (Mo-Su 06:00-09:00), tier 1 so the
    # base planner ignores it but the door_closed chooser still sees it.
    shut_near = _stand(
        _at(PDV, 350.0, 65.0, "shut-near", tier=1, beat_count=1), 10
    )
    shut_near = shut_near.model_copy(
        update={
            "opening_hours": "Mo-Su 06:00-09:00",
            "opening_hours_source": "map",
        }
    )
    # FARTHER candidate — open at 14:24, same low tier.
    open_far = _stand(
        _at(PDV, 400.0, 70.0, "open-far", tier=1, beat_count=1), 10
    )
    open_far = open_far.model_copy(
        update={
            "opening_hours": "Mo-Su 06:00-22:00",
            "opening_hours_source": "map",
        }
    )

    snap = _snap([stand_map, stand_guess, bench, stand_end, shut_near, open_far])
    request = TourInput(
        start=PDV,
        end=(end.lat, end.lng),
        duration_min=110,
        city_slug="paris",
        round_trip=False,
        start_datetime="2026-08-19T14:00",
        end_hardness="wall",
        rest_cadence_minutes=6,
    )
    base = select_route(request, snap)
    ids = [p.id for p in base.pois]
    assert stand_guess.id in ids, f"premise: guessed stop on day; got {ids}"
    assert "shut-near" not in ids and "open-far" not in ids, (
        f"premise: neither candidate on day; {ids}"
    )

    cset = build_contingency_set(
        base, request, snap, routing_client=None,
        person=ReplanContext(protected_poi_ids=(bench.id,)),
    )
    dc = [e for e in cset.entries if e.trigger.get("kind") == "door_closed"
          and e.trigger["stop_id"] == stand_guess.id]
    assert dc, "no door_closed entry for the guessed stop"
    entry = dc[0]
    # The shut candidate must NOT appear — the open one must.
    assert "open-far" in entry.alternate_stop_ids, (
        f"the farther open candidate must be the standby; got {entry.alternate_stop_ids}"
    )
    assert "shut-near" not in entry.alternate_stop_ids, (
        f"the nearer shut candidate must be skipped; got {entry.alternate_stop_ids}"
    )
