"""Route-option cards interleave walk-past vignettes and walking narration.

These assertions are the spec for the cards a route option carries, on the
preview and on the phone's own generate reply alike. The local ``_preview_stops``
helper drives the ONE shared implementation,
``src.tour.options.build_route_option``, so the assertions pin the code both
surfaces run.
"""

from __future__ import annotations

import datetime as dt

from src.tour.contract import (
    POI,
    BeatRef,
    BeatSequence,
    Route,
    Script,
    ScriptPOI,
    Sentence,
    TourInput,
    TransitSegment,
    ValidationReport,
)
from src.tour.options import build_route_option
from src.tour.selection import CorpusSnapshot


def _preview_stops(script, route, vignette_beats, snapshot, overflow_by_poi):
    """The option's cards, in order, through the ONE shared interleave."""
    beats_by_id = {b.id: b for beats in snapshot.beats_by_poi.values() for b in beats}
    return build_route_option(
        route,
        script,
        beats_by_id,
        route_id="test-opt1",
        snapshot=snapshot,
        sequence=BeatSequence(
            poi_beats=(),
            vignette_beats=vignette_beats,
            overflow_by_poi=overflow_by_poi,
        ),
    ).stops


def _poi(pid: str, tier: int = 2) -> POI:
    return POI(id=pid, name=pid, tier=tier, poi_role="walk_by_only", lat=48.85, lng=2.36)


def _script_two_stops() -> Script:
    return Script(
        city_slug="paris",
        generated_at=dt.datetime(2026, 7, 2, tzinfo=dt.UTC).isoformat(),
        inputs=TourInput(start=(48.85, 2.36), duration_min=60, city_slug="paris"),
        total_audio_seconds=60,
        total_walking_seconds=60,
        total_walk_distance_m=100,
        total_planned_seconds=120,
        selected_pois=(
            ScriptPOI(id="d0", name="Dwell Zero", tier=5, lat=48.85, lng=2.36,
                      dwell_seconds=600),
            ScriptPOI(id="d1", name="Dwell One", tier=5, lat=48.86, lng=2.37,
                      dwell_seconds=300),
        ),
        lens_coverage={},
        script=(
            Sentence(text="Stop zero story.", source_id="GLUE_PACING",
                     source_type="glue", stop_idx=0),
            Sentence(text="Stop one story.", source_id="GLUE_PACING",
                     source_type="glue", stop_idx=1),
        ),
        validation=ValidationReport(),
    )


def _route(vignettes) -> Route:
    pois = (POI(id="d0", name="Dwell Zero", tier=5, poi_role="stop", lat=48.85, lng=2.36),
            POI(id="d1", name="Dwell One", tier=5, poi_role="stop", lat=48.86, lng=2.37))
    transits = tuple(
        TransitSegment(from_poi_id=None, to_poi_id=p.id, distance_m=100.0, walk_seconds=60)
        for p in pois
    )
    return Route(
        pois=pois, transits=transits, total_walk_distance_m=200.0,
        total_walk_seconds=120, vignettes=vignettes,
    )


def _snapshot(vpoi: POI, beat: BeatRef) -> CorpusSnapshot:
    return CorpusSnapshot(
        pois=(vpoi,), beats_by_poi={vpoi.id: (beat,)}, area_types={}, adjacent_areas={}
    )


def test_vignette_interleaves_before_its_leg_destination():
    vpoi = _poi("v1")
    beat = BeatRef(id="vb1", poi_id="v1",
                   script_body="A tiny plaque marks the spot. More detail here.")
    stops = _preview_stops(
        _script_two_stops(),
        _route({1: (vpoi,)}),
        {1: (beat,)},
        _snapshot(vpoi, beat),
        {},
    )
    assert [s.band for s in stops] == ["dwell", "vignette", "dwell"]
    vignette = stops[1]
    assert vignette.name == "v1"
    assert vignette.minutes == 0
    assert vignette.narration == "A tiny plaque marks the spot."
    assert vignette.spotlight > 0
    # Dwell stops keep their narration blocks untouched.
    assert stops[0].narration == "Stop zero story."
    assert stops[2].narration == "Stop one story."


def test_vignette_one_liner_does_not_bleed_into_dwell_narration():
    """The vignette's one-liner is voiced by its OWN interleaved card; it must NOT
    also fold into the following dwell stop's narration. Live, _build_transit puts
    the one-liner at the arrival dwell stop's stop_idx as a beat sentence, which
    stop_narration_text then folds in — the workbench 'bleed' the user reported."""
    vpoi = _poi("v1")
    one_liner = "A tiny plaque marks the spot."
    beat = BeatRef(id="vb1", poi_id="v1", script_body=f"{one_liner} More detail here.")
    base = _script_two_stops()
    # Reproduce the fold: the vignette one-liner sentence sits at dwell stop 1.
    bled = base.model_copy(update={"script": (
        *base.script,
        Sentence(text=one_liner, source_id="vb1", source_type="beat", stop_idx=1),
    )})
    stops = _preview_stops(bled, _route({1: (vpoi,)}), {1: (beat,)}, _snapshot(vpoi, beat), {})
    assert [s.band for s in stops] == ["dwell", "vignette", "dwell"]
    assert stops[1].narration == one_liner  # the vignette CARD still voices it
    # The following dwell card must NOT repeat the vignette line.
    assert one_liner not in stops[2].narration, f"vignette bled into dwell: {stops[2].narration!r}"
    assert stops[2].narration == "Stop one story."


def test_no_vignettes_is_todays_shape():
    stops = _preview_stops(
        _script_two_stops(), _route({}), {}, _snapshot(_poi("vx"), BeatRef(id="b", poi_id="vx")), {}
    )
    assert [s.band for s in stops] == ["dwell", "dwell"]


def test_unvoiceable_vignette_is_not_shown():
    """A vignette POI with no voiceable beat is dropped from the cards —
    what is not voiced is not shown."""
    vpoi = _poi("v1")
    beat = BeatRef(id="vb1", poi_id="v1")  # no script_body
    stops = _preview_stops(
        _script_two_stops(), _route({1: (vpoi,)}), {}, _snapshot(vpoi, beat), {}
    )
    assert [s.band for s in stops] == ["dwell", "dwell"]


def test_deeper_dive_flag_reflects_overflow_by_poi():
    """KE9: a dwell stop gets has_deeper_dive=True iff its poi_id has non-empty
    overflow in overflow_by_poi; other dwell stops are False. Vignette stops are
    always False (walk-past has no 'keep exploring here' extras)."""
    vpoi = _poi("v1")
    beat = BeatRef(id="vb1", poi_id="v1",
                   script_body="A tiny plaque marks the spot. More detail here.")
    # d0 capped some beats out (extras exist); d1 did not. The vignette sits on
    # leg 1, between the two dwell stops.
    stops = _preview_stops(
        _script_two_stops(),
        _route({1: (vpoi,)}),
        {1: (beat,)},
        _snapshot(vpoi, beat),
        {"d0": ("extra-b1", "extra-b2")},
    )
    assert [s.band for s in stops] == ["dwell", "vignette", "dwell"]
    # d0: overflow present -> deeper-dive badge.
    assert stops[0].has_deeper_dive is True
    # The interleaved vignette never carries the flag.
    assert stops[1].has_deeper_dive is False
    # d1: no overflow -> no badge.
    assert stops[2].has_deeper_dive is False


def test_deeper_dive_flag_false_when_no_overflow():
    """KE9: with an empty overflow_by_poi every stop stays has_deeper_dive=False —
    behavior-preserving default."""
    stops = _preview_stops(
        _script_two_stops(), _route({}), {}, _snapshot(_poi("vx"), BeatRef(id="b", poi_id="vx")), {}
    )
    assert [s.band for s in stops] == ["dwell", "dwell"]
    assert all(s.has_deeper_dive is False for s in stops)


def test_preview_normalizes_display_em_dashes_to_commas():
    """The workbench narration reads the way the audio sounds: a clause em-dash
    becomes a comma pause, not a dangling stroke (the tourist's em-dash complaint)."""
    em = chr(0x2014)
    base = _script_two_stops()
    bled = base.model_copy(update={"script": (
        Sentence(text=f"The Meurice {em} grandest of the palace hotels {em} faced the gardens.",
                 source_id="GLUE_PACING", source_type="glue", stop_idx=0),
        base.script[1],
    )})
    stops = _preview_stops(
        bled, _route({}), {}, _snapshot(_poi("vx"), BeatRef(id="b", poi_id="vx")), {}
    )
    assert em not in stops[0].narration, f"em-dash leaked into display: {stops[0].narration!r}"
    assert "Meurice, grandest of the palace hotels, faced" in stops[0].narration


# ---------------------------------------------------------------------------
# band="leg" — walk-concurrent narration gets its own card
# ---------------------------------------------------------------------------
#
# Product ruling 2026-07-19: "Audio overlaps the walking. It is a part of the tour
# experience." Navigation lines and reflections are spoken WHILE WALKING the leg
# INTO stop i, but they carry stop_idx == i, so they were concatenated into that
# stop's narration. The editor saw one undifferentiated blob: they could not play
# the walk content on its own, and could not see how much of a long walk was
# actually filled.
#
# MEASURED on the live Paris graph immediately after this change (60-min Ile de la
# Cite, dark_history):
#     "Walk to Conciergerie"          4 min walk ->  11 words (4s)
#     "Walk to Theatre de la Ville"   6 min walk ->  18 words (7s)
#     whole tour: 54 words = 22s of leg narration against 1650s of walking (1.3%)
# Surfacing that ratio is the entire point of the card.


def _script_with_leg_glue() -> Script:
    """Two stops where the leg INTO stop 1 carries a GLUE_NAV line."""
    base = _script_two_stops()
    return base.model_copy(
        update={
            "script": (
                Sentence(text="Stop zero story.", source_id="GLUE_PACING",
                         source_type="glue", stop_idx=0),
                Sentence(text="From Dwell Zero, head on to Dwell One, about a minute away.",
                         source_id="GLUE_NAV", source_type="glue", stop_idx=1),
                Sentence(text="Stop one story.", source_id="GLUE_PACING",
                         source_type="glue", stop_idx=1),
            )
        }
    )


def _legs(stops):
    return [s for s in stops if s.band == "leg"]


def test_walk_narration_gets_its_own_leg_card():
    """UNDO TEST: drop the is_walk_concurrent branch in build_route_option (so
    GLUE_NAV falls back into the dwell bucket) and this goes RED — no leg card
    is emitted."""
    stops = _preview_stops(_script_with_leg_glue(), _route({}), {}, _snapshot(
        _poi("v"), BeatRef(id="b", poi_id="v", script_body="x.")), {})
    legs = _legs(stops)
    assert len(legs) == 1, [(s.band, s.name) for s in stops]
    assert legs[0].name == "Walk to Dwell One"
    assert "head on to Dwell One" in legs[0].narration


def test_leg_card_precedes_the_stop_it_walks_to():
    stops = _preview_stops(_script_with_leg_glue(), _route({}), {}, _snapshot(
        _poi("v"), BeatRef(id="b", poi_id="v", script_body="x.")), {})
    order = [(s.band, s.name) for s in stops]
    i = order.index(("leg", "Walk to Dwell One"))
    assert order[i + 1] == ("dwell", "Dwell One"), order


def test_leg_content_is_removed_from_the_dwell_card_not_duplicated():
    """No double-voicing: a sentence lives on exactly one card."""
    stops = _preview_stops(_script_with_leg_glue(), _route({}), {}, _snapshot(
        _poi("v"), BeatRef(id="b", poi_id="v", script_body="x.")), {})
    dwell_one = next(s for s in stops if s.band == "dwell" and s.name == "Dwell One")
    assert "head on to Dwell One" not in dwell_one.narration
    assert dwell_one.narration == "Stop one story."


def test_silent_leg_emits_no_card():
    """A leg with nothing to say is silence; an empty card would imply content
    that does not exist. The FIRST leg of a real tour has exactly none."""
    stops = _preview_stops(_script_two_stops(), _route({}), {}, _snapshot(
        _poi("v"), BeatRef(id="b", poi_id="v", script_body="x.")), {})
    assert _legs(stops) == []
    assert [s.band for s in stops] == ["dwell", "dwell"]


def test_leg_card_minutes_report_the_walk_not_the_narration():
    """The card shows the WALK duration, so a 6-minute walk sits next to 7 seconds
    of narration — the gap made visible rather than averaged away."""
    route = _route({})
    long_walk = route.model_copy(
        update={
            "transits": (
                TransitSegment(from_poi_id=None, to_poi_id="d0", distance_m=100.0,
                               walk_seconds=0),
                TransitSegment(from_poi_id="d0", to_poi_id="d1", distance_m=600.0,
                               walk_seconds=360),
            )
        }
    )
    stops = _preview_stops(_script_with_leg_glue(), long_walk, {}, _snapshot(
        _poi("v"), BeatRef(id="b", poi_id="v", script_body="x.")), {})
    leg = _legs(stops)[0]
    assert leg.minutes == 6, "leg card must report the 360s walk, not its own length"
    assert len(leg.narration.split()) < 30
