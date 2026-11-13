"""THE ONE INTERLEAVE — ``build_route_option`` assembles the day's cards. Hermetic.

Moved intact from ``tests/test_tour_flavours.py``: that file was the k-flavours suite
and died with ``select_k_routes`` and the diversity machinery (a visitor never wants to
compare routes), but ``build_route_option`` is count-agnostic and SURVIVES as the one place a Route
becomes user-facing cards (``test_tour_one_engine.py`` pins it as THE ONE INTERLEAVE).
These are its tests: dwell/leg/vignette card assembly, spotlight and band scoring,
lens coverage notes, and the contract round-trip.

Deleting the flavour suite wholesale would have deleted this coverage with it; the plan
said "delete wholesale" before the file had been read end to end.
"""

from __future__ import annotations

import pytest

from src.tour.contract import (
    POI,
    BeatRef,
    BeatSequence,
    Route,
    RouteOption,
    RouteOptionStop,
    Script,
    ScriptPOI,
    Sentence,
    TourInput,
    TransitSegment,
    ValidationReport,
)
from src.tour.options import build_route_option
from tests.test_tour_selection import _snap

# ---------------------------------------------------------------------------
# RouteOption assembly
# ---------------------------------------------------------------------------


def _hand_built_route_and_script():
    pois = (
        POI(id="p1", name="Anchor", tier=5, poi_role="stop", lat=48.85, lng=2.35),
        POI(id="p2", name="Passby", tier=3, poi_role="walk_by_only", lat=48.86, lng=2.36),
    )
    transits = (
        TransitSegment(from_poi_id=None, to_poi_id="p1", distance_m=500, walk_seconds=810,
                       leg_seconds=600, polyline="shape1", source="valhalla"),
        TransitSegment(from_poi_id="p1", to_poi_id="p2", distance_m=300, walk_seconds=486),
    )
    route = Route(
        pois=pois, transits=transits, total_walk_distance_m=800, total_walk_seconds=1296,
    )
    script = Script(
        city_slug="paris", generated_at="2026-06-12T00:00:00Z",
        inputs=TourInput(start=(48.85, 2.35), duration_min=60, city_slug="paris"),
        total_audio_seconds=600, total_walking_seconds=1296, total_walk_distance_m=800,
        total_planned_seconds=2000,
        selected_pois=(
            ScriptPOI(id="p1", name="Anchor", tier=5, lat=48.85, lng=2.35,
                      dwell_seconds=300, beat_ids=("b1", "b2")),
            ScriptPOI(id="p2", name="Passby", tier=3, lat=48.86, lng=2.36,
                      dwell_seconds=60, beat_ids=("b3",)),
        ),
        lens_coverage={"hidden_history": 2, "historic_arch": 1},
        script=(), validation=ValidationReport(),
    )
    beats_by_id = {
        "b1": BeatRef(id="b1", poi_id="p1", lenses=("hidden_history",)),
        "b2": BeatRef(id="b2", poi_id="p1", lenses=("hidden_history",)),
        "b3": BeatRef(id="b3", poi_id="p2", lenses=()),
    }
    # build_route_option scores spotlight/band, so it needs the
    # snapshot (POI tiers for gravity, beat lenses for lens_relevance). Mirror
    # the route's POIs and their beats exactly.
    snapshot = _snap(
        list(pois),
        beats_by_poi={pid: [b for b in beats_by_id.values() if b.poi_id == pid]
                      for pid in ("p1", "p2")},
    )
    return route, script, beats_by_id, snapshot


def test_build_route_option_maps_engine_outputs():
    route, script, beats_by_id, snapshot = _hand_built_route_and_script()
    opt = build_route_option(
        route,
        script,
        beats_by_id,
        route_id="trip-1-opt1",
        snapshot=snapshot,
        sequence=BeatSequence(poi_beats=()),
    )

    assert opt.route_id == "trip-1-opt1"
    assert [s.poi_id for s in opt.stops] == ["p1", "p2"]
    assert opt.stops[0].lens == "hidden_history"
    assert opt.stops[0].visit_or_walk_past == "visit"
    assert opt.stops[0].minutes == 5  # 300s dwell
    assert opt.stops[1].lens is None  # unlensed beat
    assert opt.stops[1].visit_or_walk_past == "walk_past"
    # eta: routed leg (600) preferred over haversine (810); unrouted leg uses
    # its walk_seconds (486); plus dwells (300 + 60).
    assert opt.eta_seconds == 600 + 486 + 300 + 60
    assert opt.lens_summary == {"hidden_history": 2, "historic_arch": 1}
    assert opt.degraded is False  # no reach verdict on the hand-built route
    assert opt.stop_audio == {} and opt.why_this_works is None  # unfilled slots


def test_route_option_contract_round_trips():
    route, script, beats_by_id, snapshot = _hand_built_route_and_script()
    opt = build_route_option(
        route,
        script,
        beats_by_id,
        route_id="rt",
        snapshot=snapshot,
        sequence=BeatSequence(poi_beats=()),
    )
    assert RouteOption.model_validate(opt.model_dump()) == opt


def test_build_route_option_populates_spotlight_and_band_per_stop():
    """Every selected stop carries a strictly positive
    spotlight score and a band. The fixture requests no lens, so lens_relevance
    is uniform 1.0 and on-corridor proximity is 1.0, giving spotlight == gravity
    == tier: p1 (tier 5) = 5.0 -> headline -> dwell; p2 (tier 3) = 3.0 -> full ->
    dwell. The score must be computed, not the 0.0 default."""
    route, script, beats_by_id, snapshot = _hand_built_route_and_script()
    opt = build_route_option(
        route,
        script,
        beats_by_id,
        route_id="rt",
        snapshot=snapshot,
        sequence=BeatSequence(poi_beats=()),
    )
    assert opt.stops, "fixture must produce at least one stop"
    for stop in opt.stops:
        assert stop.spotlight > 0.0
        assert stop.band in ("dwell", "vignette")
    by_id = {s.poi_id: s for s in opt.stops}
    # No lens requested -> spotlight collapses to gravity (tier).
    assert by_id["p1"].spotlight == pytest.approx(5.0)
    assert by_id["p2"].spotlight == pytest.approx(3.0)
    # tier-5 (5.0 >= 4.0 headline) and tier-3 (3.0 >= 2.0 full) are both dwell.
    assert by_id["p1"].band == "dwell"
    assert by_id["p2"].band == "dwell"


def test_build_route_option_lens_dims_off_genre_stop_to_vignette():
    """A requested lens DIMS an off-genre stop via lens_relevance.
    A tier-3 stop whose beats miss the lens scores 3 x LENS_FLOOR (0.25) = 0.75,
    below the short/full cuts -> vignette band; the on-genre tier-5 anchor stays
    a headline dwell. Proves the spotlight (not a fixed default) drives the band."""
    route, script, beats_by_id, snapshot = _hand_built_route_and_script()
    # Request a lens that only p1's beats carry; p2's beat is unlensed (a miss).
    lensed_script = script.model_copy(
        update={
            "inputs": TourInput(
                start=(48.85, 2.35), duration_min=60, city_slug="paris",
                lenses=["hidden_history"],
            )
        }
    )
    opt = build_route_option(
        route,
        lensed_script,
        beats_by_id,
        route_id="rt",
        snapshot=snapshot,
        sequence=BeatSequence(poi_beats=()),
    )
    by_id = {s.poi_id: s for s in opt.stops}
    assert by_id["p1"].spotlight == pytest.approx(5.0)  # tier 5 x direct hit 1.0
    assert by_id["p1"].band == "dwell"
    assert by_id["p2"].spotlight == pytest.approx(0.75)  # tier 3 x LENS_FLOOR 0.25
    assert by_id["p2"].band == "vignette"


def test_route_option_lens_coverage_note_none_without_lens():
    """No lens requested -> nothing to surface -> note is None."""
    route, script, beats_by_id, snapshot = _hand_built_route_and_script()
    opt = build_route_option(
        route,
        script,
        beats_by_id,
        route_id="rt",
        snapshot=snapshot,
        sequence=BeatSequence(poi_beats=()),
    )
    assert opt.lens_coverage_note is None


def test_route_option_lens_coverage_note_reflects_corridor_density():
    """With a lens requested, the note reports how many route
    POIs speak to it. p1's beats hit hidden_history; p2's beat is unlensed (a
    miss). So 1 of 2 places speak to the lens."""
    route, script, beats_by_id, snapshot = _hand_built_route_and_script()
    lensed_script = script.model_copy(
        update={
            "inputs": TourInput(
                start=(48.85, 2.35), duration_min=60, city_slug="paris",
                lenses=["hidden_history"],
            )
        }
    )
    opt = build_route_option(
        route,
        lensed_script,
        beats_by_id,
        route_id="rt",
        snapshot=snapshot,
        sequence=BeatSequence(poi_beats=()),
    )
    assert opt.lens_coverage_note == "1 of 2 places on this route speak to the chosen lens(es)."


# ---------------------------------------------------------------------------
# Vignettes interleaved into RouteOption.stops
# ---------------------------------------------------------------------------


def _route_script_with_vignette():
    """The hand-built fixture plus one walk-past vignette POI on leg 1
    (the p1 → p2 walk): stops must interleave as [p1, v1, p2]."""
    route, script, beats_by_id, _ = _hand_built_route_and_script()
    v = POI(id="v1", name="Fountain", tier=2, poi_role="stop", lat=48.855, lng=2.355)
    vbeat = BeatRef(
        id="vb1",
        poi_id="v1",
        lenses=("hidden_history",),
        active_status="active",
        script_body="A quiet fountain from another century. It still runs.",
    )
    route = route.model_copy(update={"vignettes": {1: (v,)}})
    snapshot = _snap(
        [*route.pois, v],
        beats_by_poi={
            "p1": [beats_by_id["b1"], beats_by_id["b2"]],
            "p2": [beats_by_id["b3"]],
            "v1": [vbeat],
        },
    )
    return route, script, beats_by_id, snapshot, vbeat


def test_route_option_interleaves_vignette_after_leg_origin():
    """The vignette on leg 1 (the walk INTO stop 1) sits between stop 0 and
    stop 1, as a minutes=0 walk_past band="vignette" stop with its score."""
    route, script, beats_by_id, snapshot, vbeat = _route_script_with_vignette()
    opt = build_route_option(
        route,
        script,
        beats_by_id,
        route_id="rt",
        snapshot=snapshot,
        sequence=BeatSequence(poi_beats=(), vignette_beats={1: (vbeat,)}),
    )

    assert [s.poi_id for s in opt.stops] == ["p1", "v1", "p2"]
    v_stop = opt.stops[1]
    assert v_stop.band == "vignette"
    assert v_stop.visit_or_walk_past == "walk_past"
    assert v_stop.minutes == 0
    assert v_stop.spotlight == pytest.approx(2.0)  # tier-2, no lens -> gravity
    assert v_stop.lens == "hidden_history"  # dominant lens of its own beats
    assert (v_stop.name, v_stop.lat, v_stop.lng) == ("Fountain", 48.855, 2.355)


def test_route_option_dwell_stops_and_eta_unchanged_by_vignettes():
    """Vignettes are additive: the dwell stops (and eta, which counts routed
    legs + dwell only) are byte-identical with and without them."""
    route_v, script, beats_by_id, snapshot, vbeat = _route_script_with_vignette()
    route_plain = route_v.model_copy(update={"vignettes": {}})
    sequence = BeatSequence(poi_beats=(), vignette_beats={1: (vbeat,)})

    opt_v = build_route_option(
        route_v, script, beats_by_id, route_id="rt", snapshot=snapshot, sequence=sequence
    )
    opt_plain = build_route_option(
        route_plain, script, beats_by_id, route_id="rt", snapshot=snapshot, sequence=sequence
    )

    dwell_only = tuple(s for s in opt_v.stops if s.poi_id != "v1")
    assert dwell_only == opt_plain.stops
    assert opt_v.eta_seconds == opt_plain.eta_seconds
    assert opt_v.lens_summary == opt_plain.lens_summary


def test_route_option_empty_vignettes_is_todays_output():
    """route.vignettes == {} -> exactly today's stop list (no vignette rows)."""
    route, script, beats_by_id, snapshot = _hand_built_route_and_script()
    assert route.vignettes == {}
    opt = build_route_option(
        route,
        script,
        beats_by_id,
        route_id="rt",
        snapshot=snapshot,
        sequence=BeatSequence(poi_beats=()),
    )
    assert [s.poi_id for s in opt.stops] == ["p1", "p2"]
    assert all(s.band == "dwell" for s in opt.stops)


def test_route_option_leg0_and_closing_leg_vignette_positions():
    """Contract-general placement: a leg-0 vignette precedes stop 0; a
    closing-leg (index len(stops)) vignette follows the last stop."""
    route, script, beats_by_id, _ = _hand_built_route_and_script()
    v0 = POI(id="v0", name="Gate", tier=2, poi_role="stop", lat=48.849, lng=2.349)
    v_end = POI(id="v9", name="Bridge", tier=2, poi_role="stop", lat=48.861, lng=2.361)
    route = route.model_copy(update={"vignettes": {0: (v0,), 2: (v_end,)}})
    vb0 = BeatRef(id="vb0", poi_id="v0", script_body="An old gate.")
    vb9 = BeatRef(id="vb9", poi_id="v9", script_body="An old bridge.")
    snapshot = _snap(
        [*route.pois, v0, v_end],
        beats_by_poi={
            "p1": [beats_by_id["b1"], beats_by_id["b2"]],
            "p2": [beats_by_id["b3"]],
            "v0": [vb0],
            "v9": [vb9],
        },
    )
    opt = build_route_option(
        route,
        script,
        beats_by_id,
        route_id="rt",
        snapshot=snapshot,
        sequence=BeatSequence(poi_beats=(), vignette_beats={0: (vb0,), 2: (vb9,)}),
    )
    assert [s.poi_id for s in opt.stops] == ["v0", "p1", "p2", "v9"]
    assert opt.stops[0].band == "vignette" and opt.stops[-1].band == "vignette"


def test_build_route_option_carries_the_leg_and_vignette_narration_cards():
    """THE ONE INTERLEAVE: leg, vignette and dwell cards all come out of the shared
    builder, which absorbed the API layer's fourth copy (trips._preview_stops).

    Exercises all three card kinds at once on one fixture: a GLUE_NAV line spoken
    while walking into stop 1 (its own leg card, carrying the WALK's minutes), a
    walk-past one-liner on that same leg (its own vignette card, zero minutes), and
    the two dwell stops — with neither the leg line nor the vignette line surviving
    on a dwell card."""
    route, script, beats_by_id, snapshot, vbeat = _route_script_with_vignette()
    script = script.model_copy(
        update={
            "script": (
                Sentence(text="Anchor story.", source_id="GLUE_PACING",
                         source_type="glue", stop_idx=0),
                Sentence(text="From Anchor, head on to Passby, about a minute away.",
                         source_id="GLUE_NAV", source_type="glue", stop_idx=1),
                Sentence(text="A quiet fountain from another century.",
                         source_id="vb1", source_type="beat", stop_idx=1),
                Sentence(text="Passby story.", source_id="GLUE_PACING",
                         source_type="glue", stop_idx=1),
            )
        }
    )
    sequence = BeatSequence(
        poi_beats=(),
        vignette_beats={1: (vbeat,)},
        overflow_by_poi={"p1": ("extra-1",)},
    )

    opt = build_route_option(
        route, script, beats_by_id, route_id="rt", snapshot=snapshot, sequence=sequence
    )

    # 1. THE ORDER: the leg into a stop, then that leg's vignettes, then the stop.
    assert [(s.band, s.name) for s in opt.stops] == [
        ("dwell", "Anchor"),
        ("leg", "Walk to Passby"),
        ("vignette", "Fountain"),
        ("dwell", "Passby"),
    ]

    # 2. THE LEG CARD carries the walking narration and the WALK's duration.
    leg = opt.stops[1]
    assert "head on to Passby" in leg.narration
    assert leg.minutes == 8  # the 486s leg, not the narration's length
    assert leg.spotlight == 0.0
    assert leg.poi_id == "p2"  # the arrival stop's identity, never an invented id
    assert leg.has_deeper_dive is False

    # 3. THE VIGNETTE CARD voices the one-liner, at zero minutes.
    vignette = opt.stops[2]
    assert vignette.narration == "A quiet fountain from another century."
    assert vignette.minutes == 0
    assert vignette.has_deeper_dive is False

    # 4. NO DOUBLE-VOICING: neither the leg line nor the vignette line survives on
    #    a dwell card.
    assert opt.stops[3].narration == "Passby story."
    assert opt.stops[0].narration == "Anchor story."

    # 5. THE DEEPER-DIVE FLAG rides the dwell card whose POI had overflow.
    assert opt.stops[0].has_deeper_dive is True
    assert opt.stops[3].has_deeper_dive is False

    # 6. Cards cost no elapsed time: eta is unchanged from the no-narration build.
    assert opt.eta_seconds == 600 + 486 + 300 + 60


def test_the_option_card_label_answers_the_request_like_the_session_does():
    """The session's label rule reaches the option cards too.

    The session stops label through ``dominant_lens(prefer=family)`` in the API
    adapter, but the SAME stop's option card is built here, and a card labelled
    by plain majority wears whatever context rode along — one stop, two labels
    on one wire. The label rule lives in ONE place (``dominant_lens``); this
    pins that the one interleave passes it the request's expanded family, for
    dwell cards and walk-past vignettes alike.

    The fixture asks for the PARENT lens ("history"); the stop's beats are two
    dark_history and one hidden_history (a child of history, per the snapshot's
    lens_neighbors). Plain majority says dark_history; the person's day says
    hidden_history. UNDO: drop ``prefer=`` from either dominant_lens call in
    options.py -> that card reads "dark_history" -> RED."""
    poi = POI(id="p1", name="Anchor", tier=5, poi_role="stop", lat=48.85, lng=2.35)
    vpoi = POI(id="v1", name="Fountain", tier=2, poi_role="stop", lat=48.855, lng=2.355)
    beats_by_id = {
        "b1": BeatRef(id="b1", poi_id="p1", lenses=("dark_history",)),
        "b2": BeatRef(id="b2", poi_id="p1", lenses=("dark_history",)),
        "b3": BeatRef(id="b3", poi_id="p1", lenses=("hidden_history",)),
    }
    vbeats = (
        BeatRef(id="vb1", poi_id="v1", lenses=("dark_history",), active_status="active",
                script_body="A quiet fountain from another century. It still runs."),
        BeatRef(id="vb2", poi_id="v1", lenses=("dark_history",), active_status="active",
                script_body="Its basin fed the whole street."),
        BeatRef(id="vb3", poi_id="v1", lenses=("hidden_history",), active_status="active",
                script_body="A smugglers' mark is cut into the rim."),
    )
    route = Route(
        pois=(poi,),
        transits=(TransitSegment(from_poi_id=None, to_poi_id="p1", distance_m=500,
                                 walk_seconds=810),),
        total_walk_distance_m=500, total_walk_seconds=810,
        vignettes={0: (vpoi,)},
    )
    script = Script(
        city_slug="paris", generated_at="2026-06-12T00:00:00Z",
        inputs=TourInput(start=(48.85, 2.35), duration_min=60, city_slug="paris",
                         lenses=["history"]),
        total_audio_seconds=300, total_walking_seconds=810, total_walk_distance_m=500,
        total_planned_seconds=1200,
        selected_pois=(
            ScriptPOI(id="p1", name="Anchor", tier=5, lat=48.85, lng=2.35,
                      dwell_seconds=300, beat_ids=("b1", "b2", "b3")),
        ),
        lens_coverage={}, script=(), validation=ValidationReport(),
    )
    snapshot = _snap(
        [poi, vpoi],
        beats_by_poi={"p1": list(beats_by_id.values()), "v1": list(vbeats)},
        lens_neighbors={
            "history": frozenset({"hidden_history"}),
            "hidden_history": frozenset({"history"}),
        },
    )
    opt = build_route_option(
        route, script, beats_by_id, route_id="rt", snapshot=snapshot,
        sequence=BeatSequence(poi_beats=(), vignette_beats={0: vbeats}),
    )
    by_id = {s.poi_id: s for s in opt.stops}
    assert by_id["p1"].lens == "hidden_history", (
        f"the dwell card wears {by_id['p1'].lens!r} on a history day"
    )
    assert by_id["v1"].lens == "hidden_history", (
        f"the vignette card wears {by_id['v1'].lens!r} on a history day"
    )


def test_route_option_round_trips_with_explicit_spotlight_fields():
    """The new fields survive a full model_dump -> model_validate round-trip when
    set to non-default values, so the contract actually carries them."""
    stop = RouteOptionStop(
        poi_id="p1",
        name="Anchor",
        lat=48.85,
        lng=2.35,
        band="vignette",
        spotlight=0.42,
    )
    opt = RouteOption(
        route_id="rt",
        stops=(stop,),
        eta_seconds=600,
        lens_coverage_note="only 2 places on this route speak to film and TV",
    )
    rebuilt = RouteOption.model_validate(opt.model_dump())
    assert rebuilt == opt
    assert rebuilt.stops[0].band == "vignette"
    assert rebuilt.stops[0].spotlight == 0.42
    assert rebuilt.lens_coverage_note == "only 2 places on this route speak to film and TV"


def test_the_fixed_end_waypoint_is_flagged_and_named_as_a_finish_point_not_a_place():
    """An A→B day whose last real stop was dropped ended with
    "- Destination: 0 min · outside" counted as stop 5 of 5, and a visitor
    finishing after dark must never end at a nameless place. The sentinel is a WAYPOINT
    (contract.END_B_SENTINEL_PREFIX): the card carries `is_finish_point=True` and
    the honest name, so a screen ends the day there instead of counting it. A
    real place keeps False. The prefix is the ONE definition every surface reads.

    UNDO TEST: drop `is_finish_point=sp.id.startswith(END_B_SENTINEL_PREFIX)` from
    the dwell card in options.py -> the sentinel's card reads False -> RED.
    """
    from src.tour.contract import END_B_SENTINEL_NAME, END_B_SENTINEL_PREFIX

    end_id = f"{END_B_SENTINEL_PREFIX}48.852966_2.349902"
    pois = (
        POI(id="p1", name="Anchor", tier=5, poi_role="stop", lat=48.85, lng=2.35),
        POI(id=end_id, name=END_B_SENTINEL_NAME, tier=3, poi_role="stop",
            lat=48.852966, lng=2.349902),
    )
    transits = (
        TransitSegment(from_poi_id=None, to_poi_id="p1", distance_m=500, walk_seconds=810),
        TransitSegment(from_poi_id="p1", to_poi_id=end_id, distance_m=300, walk_seconds=486),
    )
    route = Route(pois=pois, transits=transits, total_walk_distance_m=800, total_walk_seconds=1296)
    script = Script(
        city_slug="paris", generated_at="2026-06-12T00:00:00Z",
        inputs=TourInput(start=(48.85, 2.35), duration_min=60, city_slug="paris",
                         end=(48.852966, 2.349902)),
        total_audio_seconds=300, total_walking_seconds=1296, total_walk_distance_m=800,
        total_planned_seconds=1600,
        selected_pois=(
            ScriptPOI(id="p1", name="Anchor", tier=5, lat=48.85, lng=2.35,
                      dwell_seconds=300, beat_ids=("b1",)),
            ScriptPOI(id=end_id, name=END_B_SENTINEL_NAME, tier=3, lat=48.852966,
                      lng=2.349902, dwell_seconds=0, beat_ids=()),
        ),
        lens_coverage={}, script=(), validation=ValidationReport(),
    )
    beats_by_id = {"b1": BeatRef(id="b1", poi_id="p1", lenses=())}
    snapshot = _snap(list(pois), beats_by_poi={"p1": [beats_by_id["b1"]], end_id: []})

    opt = build_route_option(
        route, script, beats_by_id, route_id="rt", snapshot=snapshot,
        sequence=BeatSequence(poi_beats=()),
    )
    by_id = {s.poi_id: s for s in opt.stops if s.band == "dwell"}
    assert by_id["p1"].is_finish_point is False
    end_card = by_id[end_id]
    assert end_card.is_finish_point is True, "the A→B waypoint was not flagged"
    assert end_card.name == END_B_SENTINEL_NAME == "Your finish point"
    assert end_card.minutes == 0
    # The old name must not come back through any door.
    assert "Destination" not in {s.name for s in opt.stops}
