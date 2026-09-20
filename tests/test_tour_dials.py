"""Phase 4 dial tests — the W4.2 panel's dials, carried and obeyed. Hermetic.

One hand per file (the test_tour_clock.py model): this file is the DIALS hand.
Every gating test cites its source — the W4.2 panel's locked semantics
(specs/2026-08-07-tour-algorithm-redesign/phase4-ledger.md, "LOCKED DIAL
SEMANTICS", all eleven personas, 2026-08-11) and design §8.1/§9 Phase 4.

The four dials and their engine meanings, as the panel ruled them:
- stop_density   — "Fewer stops, longer at each" / "More stops, shorter at each"
- narration_density — "Less talking / More talking" (supply, never topic)
- avoid_queues   — "Skip the queues" (the retired "quieter", named plainly)
- category_minus — "Less of THIS today" (Greta's kind chips; swap, never starve)
"""

from __future__ import annotations

from itertools import pairwise

import pytest
from pydantic import ValidationError

from src.tour.contract import POI, BeatRef, Route, TourInput, TransitSegment, resolve_party_axes
from tests.test_tour_selection import PDV, _density_fillers, _poi, _snap


def _categorised(poi: POI, category: str) -> POI:
    return poi.model_copy(update={"place_category": category})


def _queued(poi: POI, minutes_peak: int) -> POI:
    return poi.model_copy(
        update={
            "queue_class": "long",
            "queue_minutes_peak": minutes_peak,
            "queue_minutes_offpeak": max(5, minutes_peak // 4),
        }
    )


def test_tour_input_carries_the_four_dials():
    """W4.2 locked semantics: the four dials are request axes (deviation iv —
    existing axes preferred; these four are the genuinely new ones, S3.2 mould).
    RED before the fields existed: TourInput is extra="forbid", so construction
    raised (the guard on that forbid is
    tests/test_tour_contract.py::test_tour_input_rejects_extra_keys)."""
    inp = TourInput(
        start=PDV,
        duration_min=120,
        city_slug="paris",
        stop_density="fewer",
        narration_density="less",
        avoid_queues=True,
        category_minus=("museum", "church"),
    )
    assert inp.stop_density == "fewer"
    assert inp.narration_density == "less"
    assert inp.avoid_queues is True
    assert inp.category_minus == ("museum", "church")
    # The defaults are today's behaviour, byte-identical.
    plain = TourInput(start=PDV, duration_min=120, city_slug="paris")
    assert plain.stop_density is None
    assert plain.narration_density is None
    assert plain.avoid_queues is False
    assert plain.category_minus == ()
    # A value outside the vocabulary is rejected loudly, not absorbed.
    with pytest.raises(ValidationError):
        TourInput(start=PDV, duration_min=120, city_slug="paris", stop_density="fewest")


def test_stop_density_more_resolves_to_the_short_stop_ceiling():
    """W4.2 ruling 4: the "more" direction is the proven short-stop shape (the
    measured a-stop20 cell); an explicitly-set ceiling always wins (the
    resolver's one rule)."""
    resolved = resolve_party_axes(
        TourInput(start=PDV, duration_min=120, city_slug="paris", stop_density="more")
    )
    assert resolved.max_stop_minutes == 20
    explicit = resolve_party_axes(
        TourInput(
            start=PDV,
            duration_min=120,
            city_slug="paris",
            stop_density="more",
            max_stop_minutes=45,
        )
    )
    assert explicit.max_stop_minutes == 45
    fewer = resolve_party_axes(
        TourInput(start=PDV, duration_min=120, city_slug="paris", stop_density="fewer")
    )
    assert fewer.max_stop_minutes is None, "the fewer direction has no axis mapping"


def test_category_minus_swaps_the_kind_out():
    """W4.2 ruling 7 (Greta: "not what I did yesterday"; Julien: "zero museums
    today, and it must behave like the lens — swap in different places, not
    starve them"): an excluded kind leaves the dwell pool; the day still plans.
    """
    from src.tour.selection import select_route

    # Categorise members of the calibrated corpus itself (a bolt-on stop loses
    # to duration-calibrated fillers), so the premise is structural.
    fillers = _density_fillers(PDV, duration_min=60, round_trip=True)
    pois = [
        _categorised(fillers[0], "museum"),
        _categorised(fillers[1], "church"),
        *fillers[2:],
    ]
    snap = _snap(pois)
    museum_id = fillers[0].id
    inp = TourInput(start=PDV, duration_min=60, city_slug="paris", round_trip=True)

    control = select_route(inp, snap)
    assert museum_id in {p.id for p in control.pois}, (
        "fixture premise: the museum must be worth seating undialed"
    )

    dialed = select_route(
        inp.model_copy(update={"category_minus": ("museum",)}),
        snap,
    )
    seated = {p.id for p in dialed.pois}
    assert museum_id not in seated, "the excluded kind was still seated"
    assert len(dialed.pois) >= 2, "the exclusion starved the day instead of swapping"


def test_avoid_queues_prefers_the_queue_free_rival():
    """W4.2 ruling 6 ("Skip the queues" — Théo: "the darkest thing in the day
    was the queue"; Sofia: a 40-minute outdoor stand-still): a peak-queue-heavy
    stop is dimmed so its queue-free rival wins; without the dial the heavier
    stop is seated (the premise that makes the flip real)."""
    from src.tour.selection import select_route

    # Queue a member of the calibrated corpus (guaranteed worth seating), and
    # add ONE extra rival so a seat is genuinely contested — with no scarcity
    # the dimmed stop keeps its seat and the dial has nothing visible to do.
    fillers = _density_fillers(PDV, duration_min=60, round_trip=True)
    queued = _queued(fillers[0], 40)
    rival = _poi(
        "free-door", lat=fillers[0].lat + 0.0002, lng=fillers[0].lng + 0.0002
    )
    snap = _snap([queued, rival, *fillers[1:]])
    inp = TourInput(start=PDV, duration_min=60, city_slug="paris", round_trip=True)

    control = select_route(inp, snap)
    assert queued.id in {p.id for p in control.pois}, (
        "fixture premise: the queued place must be worth seating undialed"
    )

    dialed = select_route(inp.model_copy(update={"avoid_queues": True}), snap)
    assert queued.id not in {p.id for p in dialed.pois}, (
        "the dial did not dim the queue-heavy stop"
    )
    assert len(dialed.pois) >= 2, "avoiding queues starved the day"


def test_stop_density_fewer_concentrates_the_day():
    """W4.2 ruling 4 (Camille's label: "Fewer stops, longer at each" — count
    down, anchors kept whole): the concentrate pass drops the weakest stops,
    never below three, never below the concentrate floor — a dial must not
    steer the day into its own underfill refusal."""
    from src.tour.selection import select_route

    snap = _snap(_density_fillers(PDV, duration_min=60, round_trip=True))
    base = TourInput(start=PDV, duration_min=60, city_slug="paris", round_trip=True)

    control = select_route(base, snap)
    assert len(control.pois) >= 5, (
        f"fixture premise: the undialed day must be busy, got {len(control.pois)}"
    )

    fewer = select_route(base.model_copy(update={"stop_density": "fewer"}), snap)
    assert len(fewer.pois) < len(control.pois), "the dial dropped nothing"
    assert len(fewer.pois) >= 3, "the dial concentrated below the stop floor"
    # The concentrated day keeps its strongest stops whole — no shaving here.
    control_visits = dict(control.planned_visit_seconds)
    for poi in fewer.pois:
        if poi.id in control_visits:
            assert fewer.planned_visit_seconds.get(poi.id, 0) >= control_visits[poi.id], (
                f"{poi.id} was shaved by the fewer dial; freed minutes must flow "
                "to the anchors, never out of them"
            )


def test_narration_density_scales_the_one_emission_ceiling():
    """W4.2 ruling 5 (Fiona & Dev's dial, Paulo's label "Less talking / More
    talking" — narration SUPPLY, never topic): the per-stop ceiling scales at
    the ONE emission choke point; trimmed beats land in keep-exploring
    overflow, never on the floor."""
    from src.tour.selection import (
        MAX_DWELL_AUDIO_SECONDS,
        build_poi_beat_plans_capped,
        planned_audio_seconds,
    )

    talker = _poi("talker", lat=PDV[0] + 0.0004, lng=PDV[1])
    # Six genuinely DISTINCT stories (the picker's paraphrase dedup collapses
    # near-identical bodies to one beat, which hides every ceiling): 360s raw,
    # past the default 270s ceiling, so all three densities separate.
    stories = (
        "The gate was cut in 1607 for a royal procession that never came.",
        "A fire in 1871 hollowed the upper floor and spared the stair.",
        "The fountain ran wine for one night in 1660, by decree.",
        "Two duellists met on the roof in 1832; both survived, embarrassed.",
        "The cellar held a printing press through the Occupation.",
        "A resident kept a leopard here until the neighbours petitioned.",
    )
    beats = [
        BeatRef(
            id=f"talker-b{i}",
            poi_id="talker",
            est_spoken_seconds=60,
            active_status="active",
            script_body=body,
        )
        for i, body in enumerate(stories)
    ]
    snap = _snap([talker], beats_by_poi={"talker": beats})
    route = Route(
        pois=(talker,),
        transits=(
            TransitSegment(
                from_poi_id=None, to_poi_id="talker", distance_m=50, walk_seconds=81
            ),
        ),
        total_walk_distance_m=50,
        total_walk_seconds=81,
    )

    def voiced(density):
        capped = build_poi_beat_plans_capped(
            route, snapshot=snap, lenses=None, end_is_none=True, narration_density=density
        )
        (plan, overflow) = capped[0]
        return planned_audio_seconds(plan.beats), overflow

    default_audio, default_overflow = voiced(None)
    less_audio, less_overflow = voiced("less")
    more_audio, _ = voiced("more")

    assert default_audio <= MAX_DWELL_AUDIO_SECONDS
    assert less_audio <= MAX_DWELL_AUDIO_SECONDS // 2, "'less' did not halve the ceiling"
    assert less_audio < default_audio
    assert more_audio > default_audio, "'more' did not raise the ceiling"
    # Whole-beat caps: what 'less' trims lands in keep-exploring overflow.
    assert len(less_overflow) > len(default_overflow), (
        "trimmed beats must become keep-exploring overflow, never vanish"
    )

def test_the_dials_ride_both_request_models_and_normalize_the_wire_forms():
    """W4.2 + the S4.7 workbench build: the page sends hyphenated presets
    ("take-it-easy") and weather "auto"; the engine spells presets with
    underscores and knows dry/rain. Normalized at the API edge — declared on
    BOTH request models (the S1.3a precedent: neither sets model_config, so an
    undeclared field would be silently dropped, and the caller would get an
    undialed tour with no error saying why)."""
    from src.api.models.trips import TripGenerateRequest, TripPreviewRequest

    preview = TripPreviewRequest(
        center_lat=48.85,
        center_lng=2.35,
        duration_min=120,
        party="take-it-easy",
        weather="auto",
        stop_density="fewer",
        narration_density="less",
        avoid_queues=True,
        category_minus=["museum"],
        max_leg_minutes=9,
        rest_cadence_minutes=40,
        walking_pace=1.5,
        pinned_poi_ids=["poi-1"],
    )
    assert preview.party == "take_it_easy", "the hyphen wire form must normalize"
    assert preview.weather == "auto"
    assert preview.stop_density == "fewer"
    assert preview.narration_density == "less"
    assert preview.avoid_queues is True
    assert preview.category_minus == ["museum"]
    assert preview.pinned_poi_ids == ["poi-1"]

    generate = TripGenerateRequest(
        profile_id="p1",
        center_lat=48.85,
        center_lng=2.35,
        duration_min=120,
        start_date="2026-08-12",
        end_date="2026-08-12",
        party="with-luggage",
        stop_density="more",
        avoid_queues=True,
    )
    assert generate.party == "with_luggage"
    assert generate.stop_density == "more"

    with pytest.raises(ValidationError):
        TripPreviewRequest(
            center_lat=48.85, center_lng=2.35, duration_min=60, party="marching-band"
        )


# --- the day notes are statements about the BUILT day, never the request -----
# (W4.12 closing panel — Julien, Greta, Théo, Camille, Marcus, Nadia, Aiko, Sofia:
# "Left out today, as asked: museum." printed on a day byte-identical to the
# un-dialled one; "Places with long waits were left out, as asked." printed above
# a stop whose wait had gone UP from 3 to 10 minutes; "we will see it from the
# outside" printed for a Montmartre cabaret on a Tuileries→Notre-Dame day.)


def _wire_day(*pois: POI, queue_seconds: dict[str, int] | None = None, **updates) -> Route:
    """A Route the wire's note writer can read: stops, legs, waits, closures."""
    legs = tuple(
        TransitSegment(from_poi_id=a.id, to_poi_id=b.id, distance_m=300, walk_seconds=240)
        for a, b in pairwise(pois)
    )
    return Route(
        pois=tuple(pois),
        transits=legs,
        total_walk_distance_m=300.0 * len(legs),
        total_walk_seconds=240 * len(legs),
        planned_queue_seconds=dict(queue_seconds or {}),
        **updates,
    )


def _dial_body(**dials):
    from src.api.models.trips import TripPreviewRequest

    return TripPreviewRequest(center_lat=PDV[0], center_lng=PDV[1], duration_min=180, **dials)


def test_a_kind_exclusion_note_describes_the_day_not_the_request():
    """The note is TRUE of the built day in both arms, and never a receipt for
    work not done. When the dial had nothing to remove it says so; when a
    place of the excluded kind is nonetheless in the day (a pin can outrank
    the dial) it names it rather than claiming an exclusion the list beneath
    disproves.

    UNDO TEST: restore `notes.append(f"Left out today, as asked: {kinds}.")`
    and the first arm asserts a sentence that claims an action -> RED.
    """
    from src.api.routes.trips import _preview_day_notes

    church = _categorised(_poi("Saint-Paul", lat=PDV[0], lng=PDV[1] + 0.001), "church")
    square = _poi("Place des Vosges", lat=PDV[0] + 0.001, lng=PDV[1])
    museum = _categorised(_poi("Musee Carnavalet", lat=PDV[0] + 0.002, lng=PDV[1]), "museum")

    no_museum = _preview_day_notes(_wire_day(church, square), _dial_body(category_minus=["museum"]))
    (note,) = [n for n in no_museum if "museum" in n]
    assert note == "No museum stops in this day, as asked.", note
    assert "left out" not in note.lower(), "a claim of removal on a day nothing was removed from"

    still_there = _preview_day_notes(
        _wire_day(church, museum, square), _dial_body(category_minus=["museum"])
    )
    (note,) = [n for n in still_there if "museum" in n]
    assert note == "You asked for no museum stops; still in this day: Musee Carnavalet.", note


def test_the_queue_note_names_the_longest_wait_that_remains():
    """"Skip the queues" states the RULE it applied (true whether or not it
    changed anything) and then the longest wait actually left in the day, by
    name — so a wait that went UP on the replan (arrival-hour repricing) is
    on the screen, not contradicted by it.

    UNDO TEST: restore "Places with long waits were left out, as asked." ->
    the residual-wait sentence is gone -> RED.
    """
    from src.api.routes.trips import _preview_day_notes
    from src.tour.selection import AVOID_QUEUES_EXCLUDE_PEAK_MINUTES

    a = _poi("Hotel de Sully", lat=PDV[0], lng=PDV[1] + 0.001)
    b = _poi("La Samaritaine", lat=PDV[0] + 0.001, lng=PDV[1])
    c = _poi("Place des Vosges", lat=PDV[0] + 0.002, lng=PDV[1])

    with_waits = _preview_day_notes(
        _wire_day(a, b, c, queue_seconds={a.id: 60, b.id: 600}),
        _dial_body(avoid_queues=True),
    )
    (note,) = [n for n in with_waits if "wait" in n]
    assert note.startswith(
        f"Nothing with a wait over {AVOID_QUEUES_EXCLUDE_PEAK_MINUTES} minutes at its busiest "
        "was considered, as asked"
    ), note
    assert note.endswith("the longest wait left in this day is 10 min, at La Samaritaine."), note
    assert "left out" not in note.lower()

    quiet = _preview_day_notes(_wire_day(a, c), _dial_body(avoid_queues=True))
    (note,) = [n for n in quiet if "wait" in n]
    assert note.endswith("no waits in this day."), note


def test_a_closure_note_names_only_the_doors_the_day_actually_reaches():
    """Owner ruling, 2026-09-19 (P10H-M11): the day's notes answer the day. A
    shut door the route visits is named, with the outside promise when it is
    kept at outside price. A shut door the day never goes near answers a
    question nobody asked — a Tuileries day once trailed a Montmartre cabaret
    — so an off-route exclusion produces no note at all.

    UNDO TEST: restore the off-route arms ("and it is not in this day" /
    "so it is not in your day") -> the no-off-route-note assertions go RED.
    """
    from src.api.routes.trips import _preview_day_notes
    from src.tour.contract import ClockExclusion

    market = _poi("Marche Bastille", lat=PDV[0], lng=PDV[1] + 0.001)
    square = _poi("Place des Vosges", lat=PDV[0] + 0.001, lng=PDV[1])
    cabaret_id = "Lapin Agile"  # closed, kept in the pool at outside price, never picked

    exclusions = (
        ClockExclusion(
            poi_id=market.id, name=market.name, reason="closed all day Wednesday",
            kept_outside=True,
        ),
        ClockExclusion(
            poi_id=cabaret_id, name=cabaret_id, reason="closed all day Wednesday",
            kept_outside=True,
        ),
        ClockExclusion(
            poi_id="Crypte", name="Crypte", reason="closed all day Wednesday",
            kept_outside=False,
        ),
    )
    notes = _preview_day_notes(
        _wire_day(market, square, clock_exclusions=exclusions), _dial_body()
    )
    assert "Marche Bastille — closed all day Wednesday — we will see it from the outside" in notes
    # Off the route entirely: true but noisy, and the owner ruled the notes
    # name only doors the day touches. No sentence for either arm.
    assert not any("Lapin Agile" in n for n in notes), (
        "a door the day never reaches was named in its notes"
    )
    assert not any("Crypte" in n for n in notes), (
        "a door the day never reaches was named in its notes"
    )


def test_the_could_not_confirm_note_keys_on_the_hours_source():
    """The doubt sentences name exactly the on-route doors whose hours are a
    guess, or carry no source at all (Docs/adr/0006). Map hours escape the
    doubt — the map is the top source and is spoken plainly — and an ungated
    stop was never in question. Nothing doubted, no sentence at all.

    On a DATED day each guess is spoken with the time we think the door
    opens, read from the guess for that day; a dateless day has no day to
    read, so the guesses are named together.

    UNDO TEST: key the list on `opening_hours is not None` alone -> the map
    stop is named as doubted -> RED.
    """
    from src.api.routes.trips import _preview_day_notes

    hours = "Mo-Su 09:00-18:00"
    from_map = _poi("Musee du Plan", lat=PDV[0], lng=PDV[1] + 0.001).model_copy(
        update={"opening_hours": hours, "opening_hours_source": "map"}
    )
    guessed = _poi("Chapelle Douteuse", lat=PDV[0] + 0.001, lng=PDV[1]).model_copy(
        update={"opening_hours": "Mo-Su 10:00-18:00", "opening_hours_source": "guess"}
    )
    sourceless = _poi("Crypte Sans Source", lat=PDV[0] + 0.003, lng=PDV[1]).model_copy(
        update={"opening_hours": hours, "opening_hours_source": None}
    )
    always = _poi("Parc Ouvert", lat=PDV[0] + 0.004, lng=PDV[1]).model_copy(
        update={"opening_hours": "24/7", "opening_hours_source": "guess"}
    )
    street = _poi("Place des Vosges", lat=PDV[0] + 0.002, lng=PDV[1])  # ungated

    dated = _preview_day_notes(
        _wire_day(from_map, guessed, sourceless, always, street),
        _dial_body(start_datetime="2026-08-11T10:00:00"),
    )
    doubted = [n for n in dated if "confirm" in n]
    assert doubted == [
        "We think Chapelle Douteuse opens at 10:00, but we could not confirm that.",
        "We think Crypte Sans Source opens at 09:00, but we could not confirm that.",
        "We think Parc Ouvert is open all day, but we could not confirm that.",
    ], doubted

    dateless = _preview_day_notes(_wire_day(from_map, guessed, sourceless, street), _dial_body())
    (note,) = [n for n in dateless if "confirm" in n]
    assert note == (
        "We could not confirm opening times for Chapelle Douteuse, Crypte Sans Source."
    ), note

    all_from_the_map = _preview_day_notes(_wire_day(from_map, street), _dial_body())
    assert not any("confirm" in n for n in all_from_the_map), all_from_the_map


def test_a_doubted_door_is_doubted_once_on_the_notes_channel():
    """A kept-closed guessed door already carries the doubt clause inside
    its own exclusion line (the reason is composed by the one hedging
    function, for pool and arrival closures alike), so repeating it in the
    could-not-confirm list is the same ignorance said twice two lines apart.
    The skip keys on the exclusion's `kept_outside` FIELD, never on the
    reason's words (W4.12). A doubted door with no exclusion stays listed —
    the list is its only doubt."""
    from src.api.routes.trips import _preview_day_notes
    from src.tour.contract import ClockExclusion

    hours = "Mo-Su 09:00-18:00"
    closed_doubted = _poi("Marche Bastille", lat=PDV[0], lng=PDV[1] + 0.001).model_copy(
        update={"opening_hours": hours, "opening_hours_source": "guess"}
    )
    open_doubted = _poi("Chapelle Douteuse", lat=PDV[0] + 0.001, lng=PDV[1]).model_copy(
        update={"opening_hours": hours, "opening_hours_source": "guess"}
    )
    route = _wire_day(
        closed_doubted, open_doubted,
        clock_exclusions=(
            ClockExclusion(
                poi_id=closed_doubted.id, name=closed_doubted.name,
                reason="we think it is closed all day Wednesday, but we could not confirm that",
                kept_outside=True, guessed=True,
            ),
        ),
    )
    notes = _preview_day_notes(route, _dial_body())
    (confirm_note,) = [n for n in notes if "could not confirm" in n and "—" not in n]
    assert confirm_note == "We could not confirm opening times for Chapelle Douteuse.", notes
    assert sum("Marche Bastille" in n for n in notes) == 1, notes


def test_a_door_with_no_hours_on_record_is_named_not_silent():
    """Docs/adr/0006: a door with unknown hours fails open WITH that
    disclosure. The doubt sentence above is about hours somebody wrote down;
    this one counts DOORS — a gated stop with no hours at all is the
    least-known door. It gets its own plain sentence; an ungated square stays
    silent, and the no-record door never leaks into the could-not-confirm list.

    UNDO TEST: key the note on `opening_hours is not None` -> Notre-Dame
    vanishes from every note -> RED."""
    from src.api.routes.trips import _preview_day_notes

    cathedral = _poi("Notre-Dame Cathedral", lat=PDV[0], lng=PDV[1] + 0.001).model_copy(
        update={"gated": True}
    )
    street = _poi("Place des Vosges", lat=PDV[0] + 0.001, lng=PDV[1])  # ungated

    notes = _preview_day_notes(_wire_day(cathedral, street), _dial_body())
    (note,) = [n for n in notes if "on record" in n]
    assert note == "No opening times on record for Notre-Dame Cathedral.", note
    assert not any("confirm" in n for n in notes), notes


def test_the_api_resolves_presets_and_the_more_dial_exactly_as_the_harness_does():
    """ONE ENGINE (memory: workbench and app share ONE path). `resolve_party_axes`
    expands presets and the "more stops" dial into the axes the planner reads —
    and it was called by scripts/tour_build.py ALONE. Through the API the Party
    dropdown never expanded and `stop_density="more"` never became its stop
    ceiling. MEASURED at the W4.12 close on the live wire: the "More stops" day
    was byte-identical to base and kept a 47-minute museum, while an explicit
    20-minute ceiling on the identical request reshaped the day. Every API
    request goes through `_build_tour_input`, so that is where the resolve
    lives; this pins it.

    UNDO TEST: make `_build_tour_input` return `TourInput(**kwargs)` bare -> RED
    on both arms.
    """
    from src.api.routes.trips import _build_tour_input

    more = _build_tour_input(
        start=PDV, duration_min=180, city_slug="paris", round_trip=True, stop_density="more"
    )
    assert more.max_stop_minutes == 20, (
        "the More-stops dial did not resolve to its stop ceiling on the API path"
    )
    easy = _build_tour_input(
        start=PDV, duration_min=180, city_slug="paris", round_trip=True, party="take_it_easy"
    )
    # 13: owner ruling 2026-08-19 (Phase 6, ruling 4).
    assert easy.max_leg_minutes == 13 and easy.rest_cadence_minutes == 20, (
        "the take-it-easy preset did not expand into its axes on the API path"
    )
    # Explicit wins, on the wire as in the harness.
    explicit = _build_tour_input(
        start=PDV, duration_min=180, city_slug="paris", round_trip=True,
        stop_density="more", max_stop_minutes=45,
    )
    assert explicit.max_stop_minutes == 45


def test_promise_windows_are_coarse_on_the_wire_and_contain_the_planned_minute():
    """F&D own the coarse-window ruling (W4.2 deviation v) and re-judged the built
    thing at W4.12: "11:32-12:02" is minute-fiction — the width is the stop's
    duration, not uncertainty, and 11:32 is not a time anybody says. The planner
    keeps the exact minute (Phase 5's replan needs it); the WIRE rounds arrival
    DOWN and departure UP to five-minute marks, so the spoken window always
    contains the planned one. A zero-length window (the A→B finish point) stays
    one time — it must never widen into a stay that does not exist. Dateless
    days ("" / "") pass through untouched.

    UNDO TEST: make `_preview_promises` copy `promise.arrives_hhmm` verbatim ->
    "11:32" reaches the wire -> RED.
    """
    from src.api.routes.trips import _coarse_window

    assert _coarse_window("11:32", "12:02") == ("11:30", "12:05")
    assert _coarse_window("10:18", "10:58") == ("10:15", "11:00")
    assert _coarse_window("13:10", "14:30") == ("13:10", "14:30"), "already on the marks"
    assert _coarse_window("12:50", "12:50") == ("12:50", "12:50"), "a finish point is one time"
    assert _coarse_window("12:52", "12:52") == ("12:50", "12:50")
    assert _coarse_window("23:58", "00:03") == ("23:55", "00:05"), "midnight wraps, never 24:05"
    assert _coarse_window("", "") == ("", ""), "a dateless day carries no window"


def test_the_wire_promise_carries_the_coarse_window_not_the_planners_minute():
    """The rounding is applied where the person reads it. UNDO TEST: make
    `_preview_promises` copy the planner's minute verbatim -> RED."""
    from src.api.routes.trips import _preview_promises
    from src.tour.contract import Promise, PromiseShape

    square = _poi("Place des Vosges", lat=PDV[0], lng=PDV[1])
    promise = Promise(
        kind="anchor",
        poi_id=square.id,
        shape=PromiseShape(
            outside_seconds=1800, inside_seconds=0, queue_seconds=0,
            goes_inside=False, closed_today=False,
        ),
        arrives_hhmm="11:32",
        departs_hhmm="12:02",
    )
    route = _wire_day(square, promises=(promise,))
    (wire,) = _preview_promises(route)
    assert (wire.arrives_hhmm, wire.departs_hhmm) == ("11:30", "12:05"), (
        f"the wire shows the planner's minute: {wire.arrives_hhmm}-{wire.departs_hhmm}"
    )
    assert wire.name == "Place des Vosges"


# ---------------------------------------------------------------------------
# Phase 5 — the composed dials and the ONE underfill line (S5.3)
# ---------------------------------------------------------------------------


def _ring(center: tuple[float, float], radius_m: float, count: int, prefix: str, **kw):
    """`count` POIs on a ring of `radius_m` around `center` — consecutive ones
    are radius*sqrt(2) apart at four points, so a chain of them fits a leg cap
    that a single hop to anywhere further would break."""
    import math

    dlat_m = 1.0 / 111_320.0
    dlng_m = 1.0 / (111_320.0 * math.cos(math.radians(center[0])))
    out = []
    for i in range(count):
        theta = 2 * math.pi * i / count
        out.append(
            _poi(
                f"{prefix}-{i}",
                lat=center[0] + radius_m * math.cos(theta) * dlat_m,
                lng=center[1] + radius_m * math.sin(theta) * dlng_m,
                **kw,
            )
        )
    return out


def test_composed_dials_cannot_duck_under_the_one_underfill_line():
    """Phase 5 S5.3 — ONE underfill line, at ONE final gate.

    W4.11's carried finding (phase4-ledger.md): "COMPOSED extreme dials can duck
    under the 50% underfill line through pass-composition (leg9+rest40+fewer lands
    a 2-stop 68-of-180 day): each pass respects its own floor, but they do not
    compose into one final line." Re-fetched at W5.1 (evidence/phase5-session/
    w51-cells.txt, PDV-stacked): 2 stops, 68 of 180 minutes, 111 unplanned,
    shipped 200. The W4.2 panel's unanimous worst finding (D-i) rules the line:
    a best-possible day under HALF the ask is "no day, mislabelled" (Aiko: "never
    quietly hand back a sixth of what I asked"; Rosemary: "never silently return
    less day than asked"; Greta: "make leg6 refuse like c-leg12 does"). Design
    §8.3 keeps the floor SOFT above that line.

    THE HOLE, read in the code (plan re-plan 2026-08-18): the 0.5 check lived
    inside the timebox repair's under-fill fallback and compared against the
    REPAIR budget — which a rest cadence has already shrunk by its rest reserve
    (`selection.py` "RESTS ARE PART OF THE DAY THE REPAIR PLANS"). Under cadence 20
    the 180-minute ask becomes a ~126-minute nominal, so the "half" line drops to
    ~63 minutes and a 75-minute day walks through it. Every pass after the repair
    (concentrate, demotion, twin collapse, ordering, orientation) then ran
    downstream of that line and upstream of a final band check that carried no
    floor. This test composes exactly that: a leg cap that pins the day to a near
    ring, a rest cadence that shrinks the repair budget, and the fewer dial.

    Also W5.1 defect 7: the refusal said "132 minutes asked for" to a person who
    asked for 180 (Paulo's plain-language rulings bind — the number is the
    request's).

    UNDO: put the 0.5 refusal back inside the repair's fallback and remove it from
    the final gate → the composed day ships again → RED.
    """
    from src.tour.selection import (
        CertificationPlanningInfeasibleError,
        select_route,
    )

    start = PDV
    # A ring of four real 15-minute stands, 150 m from the start (~212 m apart, all
    # under a 7-minute hop; the whole day ~75 min of 180) …
    near = [
        p.model_copy(update={"typical_duration_min": 15})
        for p in _ring(start, 150.0, 4, "near")
    ]
    # … a rich cluster ~890 m north the uncapped day walks to (the density gate stays
    # GREEN: the day starves at the CAP, never at the corpus) …
    far_center = (start[0] + 0.008, start[1])
    far = _density_fillers(far_center, duration_min=180, round_trip=True, radius_m=80.0)
    # … and three benches beside the far cluster: they count in the rest reserve
    # (the repair budget shrinks by three rests) but sit far outside the seating
    # rule's detour of the near ring, so the capped day never seats one.
    benches = [
        p.model_copy(update={"typical_duration_min": 10})
        for p in _ring(far_center, 60.0, 3, "bench", role="body", tier=1, beat_count=0)
    ]
    snap = _snap(near + far + benches)

    base = TourInput(start=start, duration_min=180, city_slug="paris", round_trip=True)

    # PREMISE — uncapped, the corpus plans a real day (it walks north).
    uncapped = select_route(base, snap)
    assert len(uncapped.pois) >= 3, "fixture premise: the uncapped day must be real"

    composed = base.model_copy(
        update={"max_leg_minutes": 7, "rest_cadence_minutes": 20, "stop_density": "fewer"}
    )
    # THE LINE — the composed dials pin the day to the near ring: about 75 of 180
    # minutes, under half of what was asked. Refuse, naming the binding dial and the
    # REQUEST's minutes; never ship it.
    with pytest.raises(CertificationPlanningInfeasibleError) as caught:
        select_route(composed, snap)
    message = str(caught.value)
    assert "limit on any single walk" in message and "7-minute" in message, message
    assert "180 minutes asked for" in message, (
        f"the refusal must name the REQUEST's minutes, not a shrunk budget: {message}"
    )
    assert "overruns" not in message, message
    assert caught.value.alternatives, "a refusal must offer a way out"

    # THE OPEN EXEMPTION — `open` zeroes the floor (design §2.3), so the same
    # composed day ships, disclosed, instead of refusing.
    open_day = select_route(composed.model_copy(update={"end_hardness": "open"}), snap)
    assert 1 <= len(open_day.pois) <= 4, "open ships the short honest day"

    # THE TOMBSTONE — one line, two licensed readers: the fraction is read at
    # the final gate and at the dead-door drop's floor guard (Docs/adr/0004's
    # underfill carve-out — the guard refuses a drop the gate would refuse to
    # ship, so both sites MUST share the one constant) — and nowhere inside
    # the repair (an absence-of-code invariant, so it is a source read; the
    # behaviour above is what proves the gate works).
    import inspect

    from src.tour import selection

    assert "UNDERFILL_REFUSAL_FRACTION" not in inspect.getsource(
        selection._apply_certification_timebox_repair
    ), "the repair still carries its own underfill line — two lines, two answers"
    assert "UNDERFILL_REFUSAL_FRACTION" in inspect.getsource(selection._drop_dead_doors)
    assert inspect.getsource(selection).count("UNDERFILL_REFUSAL_FRACTION") == 3, (
        "one definition, two licensed reads (final gate, drop floor guard) — "
        "any other count is a new underfill line under src/"
    )


# ---------------------------------------------------------------------------
# Phase 5 — ONE drop primitive: merge the legs, re-check the cap, redistribute (S5.5)
# ---------------------------------------------------------------------------


def _interior_stand(poi: POI, *, outside_min: int, inside_min: int) -> POI:
    """A place with an interior priced at the one-hop/no-lens BLEND — so it has
    headroom up to its full interior (the shape ceiling)."""
    return poi.model_copy(
        update={"typical_duration_min": outside_min, "visit_seconds_inside": inside_min * 60}
    )


def test_fewer_stops_hands_the_freed_minutes_to_the_survivors_within_their_ceilings():
    """Phase 5 S5.5 (Phase-4 CARRIED 2; W4.2 locked semantics 4 — Camille's label
    "Fewer places, longer at each": stop count down "with the freed minutes flowing
    to the anchors within shape ceilings"). Measured at W4.12 and re-fetched at
    W5.1 (b)2: the flagship "fewer" freed 100 minutes (6 → 4 stops) and NOT ONE
    surviving stop was a minute longer — 40/10/38/12 before and after, 129 minutes
    to slack; PdV byte-identical. Read in the code (plan re-plan): the concentrate
    pass dropped and never redistributed (`selected = trial_set` and nothing
    re-priced the survivors).

    ONE drop primitive now does the drop: merge the two legs, re-check the cap on
    the merged leg (§4.5.3), and hand the freed minutes to the surviving protected
    and anchor stops — strongest first, never past a stop's shape ceiling (the
    full interior for a place with one; the outside number for a place without),
    never to the weakest because it has the most headroom (the plan's sabotage
    line). The concentrate pass CALLS it (tombstone below).

    UNDO: make the primitive grant nothing (or the concentrate pass ignore its
    grants) → survivors' visits identical to base → RED.
    """
    from src.tour.selection import select_route

    # Six interior stands on an 80 m ring (hops ~113 m ≈ 3 min): each priced at
    # the no-lens blend 5 + (15-5)*0.6 = 11 min, ceiling 15 min → 4 min of headroom.
    stands = [
        _interior_stand(p, outside_min=5, inside_min=15) for p in _ring(PDV, 80.0, 6, "stand")
    ]
    snap = _snap(stands)
    base = TourInput(start=PDV, duration_min=90, city_slug="paris", round_trip=True)

    control = select_route(base, snap)
    assert len(control.pois) >= 5, f"fixture premise: a busy undialed day, got {len(control.pois)}"

    fewer = select_route(base.model_copy(update={"stop_density": "fewer"}), snap)
    assert len(fewer.pois) < len(control.pois), "the dial dropped nothing"
    survivors = [p for p in fewer.pois if p.id in {q.id for q in control.pois}]
    grew = [
        p.id
        for p in survivors
        if fewer.planned_visit_seconds[p.id] > control.planned_visit_seconds[p.id]
    ]
    assert grew, (
        "the freed minutes reached NO survivor — every surviving stop is exactly as long "
        f"as before the drop: {[(p.id, fewer.planned_visit_seconds[p.id]) for p in survivors]}"
    )
    for p in survivors:
        assert fewer.planned_visit_seconds[p.id] <= 15 * 60, (
            f"{p.id} was pushed past its shape ceiling (the full 15-minute interior): "
            f"{fewer.planned_visit_seconds[p.id]} s"
        )
        assert fewer.planned_visit_seconds[p.id] >= control.planned_visit_seconds[p.id], (
            f"{p.id} was SHAVED by the fewer dial"
        )
    # The dial never lengthens the day past what was planned: freed ≥ granted.
    freed = sum(control.planned_visit_seconds[p.id] for p in control.pois if p not in survivors)
    granted = sum(
        fewer.planned_visit_seconds[p.id] - control.planned_visit_seconds[p.id] for p in survivors
    )
    assert 0 < granted <= freed + 1, (freed, granted)


def _line(start: tuple[float, float], spacing_m: float, count: int, prefix: str, **kw):
    """`count` POIs in a straight line north of `start`, `spacing_m` apart — a
    CHAIN, so dropping a middle stop fuses two hops into one (a ring around the
    start is hub-shaped and never does)."""
    dlat_m = 1.0 / 111_320.0
    return [
        _poi(f"{prefix}-{i}", lat=start[0] + spacing_m * (i + 1) * dlat_m, lng=start[1], **kw)
        for i in range(count)
    ]


def test_a_drop_whose_merged_leg_breaks_the_cap_is_not_taken():
    """Phase 5 S5.5 — design §4.5.3: "Every drop re-checks the longest-single-walk
    cap. Dropping a stop merges its two legs — drop Rosemary's bench and a 12-minute
    and a 9-minute leg fuse into 21 continuous minutes, double her limit. Without
    this rule every drop is a trap." Four 15-minute stands in a line north of the
    start, 212 m (5.7 min) apart, an open walk under a 6-minute cap. The weakest
    stop by score is the FIRST (equal scores, id order); dropping it — or any
    middle stop — fuses two hops into 11.4 minutes. The "fewer" dial must not
    take such a drop: it drops the END stop instead (no merged leg), the day keeps
    every walk under six and is not refused (S5.4 would refuse a day that shipped
    the merged leg; the right answer is never to make it).

    UNDO: let the concentrate pass drop without the merged-leg check → the first
    stop goes, the 11-minute leg reaches the street certification → refused → RED.
    """
    from src.tour.routing import longest_walk_minutes
    from src.tour.selection import select_route

    stands = [
        p.model_copy(update={"typical_duration_min": 15}) for p in _line(PDV, 212.0, 4, "line")
    ]
    snap = _snap(stands)
    day = select_route(
        TourInput(
            start=PDV,
            duration_min=110,
            city_slug="paris",
            round_trip=False,
            max_leg_minutes=6,
            stop_density="fewer",
        ),
        snap,
    )
    ids = [p.id for p in day.pois]
    assert len(ids) == 3, f"the dial should drop exactly one stop: {ids}"
    assert {"line-0", "line-1", "line-2"} <= set(ids), (
        f"a middle (or the first) stop was dropped and its legs merged: {ids}"
    )
    assert longest_walk_minutes(day.transits) <= 6, [t.walk_seconds for t in day.transits]


def test_the_one_drop_primitive_never_drops_a_protected_stop_and_the_pass_calls_it():
    """§4.5.2 — rests, meals, toilets, pins and the finish are never auto-cut; they
    trigger the question instead. The primitive REFUSES a protected drop outright
    (a caller that wants to drop a promise must ask, not call this). And the
    tombstone (plan §10.8): the concentrate pass has no inline drop of its own — an
    absence-of-code invariant, so it is a source read."""
    import inspect

    import pytest as _pytest

    from src.tour import selection
    from src.tour.selection import drop_stop

    a, b, c = _ring(PDV, 100.0, 3, "s")
    with _pytest.raises(ValueError):
        drop_stop(
            [a, b, c],
            b,
            start=PDV,
            close=PDV,
            leg_seconds_fn=None,
            max_leg_seconds=None,
            visit_of=lambda p: 600,
            ceiling_of=lambda p: 900,
            protected_ids={b.id},
        )
    source = inspect.getsource(selection._select_route_once)
    concentrate = source[source.index('input.stop_density == "fewer"'):]
    concentrate = concentrate[: concentrate.index("apply_co_located_demotion")]
    assert "drop_stop(" in concentrate, "the concentrate pass does not call the one primitive"
    assert "trial_set = [p for p in selected if p.id != weakest.id]" not in concentrate, (
        "the concentrate pass still carries its own inline drop — two spellings of one drop"
    )
