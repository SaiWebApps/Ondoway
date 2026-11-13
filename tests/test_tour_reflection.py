"""GLUE_REFLECTION whitelist + pure audio-deficit placement.

Pure placement only: which legs get a reflection. The reflection TEXT is
COMPOSE's job; the stitcher never writes one.
"""

from __future__ import annotations

import datetime as dt

from src.tour.contract import (
    POI,
    BeatRef,
    BeatSequence,
    POIBeats,
    Route,
    Script,
    Sentence,
    TourInput,
    TransitSegment,
    ValidationReport,
)
from src.tour.generation import GLUE_LABELS, GLUE_REFLECTION
from src.tour.reflection import (
    GLUE_SENTENCE_SECONDS,
    REFLECTION_MIN_DEFICIT_SECONDS,
    reflection_slots,
)
from src.tour.validation import validate_script

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _poi(pid: str) -> POI:
    return POI(id=pid, name=pid, tier=5, poi_role="stop", lat=48.85, lng=2.36)


def _beat(bid: str, poi_id: str, *, body: str = "A fact.", **kwargs) -> BeatRef:
    return BeatRef(id=bid, poi_id=poi_id, script_body=body, **kwargs)


def _stops(n: int) -> tuple[POIBeats, ...]:
    return tuple(
        POIBeats(
            poi_id=f"p{i}",
            poi_name=f"p{i}",
            ordering_strategy="sub_location",
            beats=(_beat(f"b{i}", f"p{i}"),),
        )
        for i in range(n)
    )


def _route_with_legs(walks: list[int]) -> Route:
    """Route whose transits[i] (the leg INTO stop i) walks ``walks[i]`` seconds."""
    pois = tuple(_poi(f"p{i}") for i in range(len(walks)))
    transits = tuple(
        TransitSegment(
            from_poi_id=None if i == 0 else pois[i - 1].id,
            to_poi_id=p.id,
            distance_m=100.0,
            walk_seconds=walks[i],
        )
        for i, p in enumerate(pois)
    )
    return Route(
        pois=pois,
        transits=transits,
        total_walk_distance_m=100.0 * len(pois),
        total_walk_seconds=sum(walks),
    )


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------


def test_deficit_threshold_is_inclusive_90s():
    # Leg into stop 1 carries only the ~4s glue line.
    eligible = REFLECTION_MIN_DEFICIT_SECONDS + GLUE_SENTENCE_SECONDS  # deficit == 90
    just_under = eligible - 1  # deficit == 89
    two_stops = BeatSequence(poi_beats=_stops(2))
    assert reflection_slots(_route_with_legs([10, eligible]), two_stops) == (1,)
    assert reflection_slots(_route_with_legs([10, just_under]), two_stops) == ()


def test_longest_deficit_wins_and_adjacent_slot_is_skipped():
    # 5 stops -> cap 2. Eligible legs: into 1 (deficit 96), 3 (396), 4 (296).
    # Sorted by deficit: 3, 4, 1. Slot 4 is adjacent to 3 -> skipped; 1 taken.
    route = _route_with_legs([10, 100, 10, 400, 300])
    assert reflection_slots(route, BeatSequence(poi_beats=_stops(5))) == (1, 3)


def test_never_two_consecutive_legs():
    # 6 stops -> cap 3. Legs into 1..5 all huge, descending. Picks 1, 3, 5.
    route = _route_with_legs([10, 500, 490, 480, 470, 460])
    assert reflection_slots(route, BeatSequence(poi_beats=_stops(6))) == (1, 3, 5)


def test_cap_is_half_the_stops():
    # 4 stops -> cap 2 even though all 3 legs qualify. Deficits: 1->496,
    # 3->446, 2->396; slot 1 then slot 3 fill the cap before 2 is reached.
    route = _route_with_legs([10, 500, 400, 450])
    slots = reflection_slots(route, BeatSequence(poi_beats=_stops(4)))
    assert slots == (1, 3)


def test_slot_zero_never_fires():
    # Huge leg from start to the FIRST stop; nothing visited yet -> no slot 0.
    route = _route_with_legs([900, 10])
    assert reflection_slots(route, BeatSequence(poi_beats=_stops(2))) == ()


def test_empty_and_single_stop_yield_nothing():
    assert reflection_slots(_route_with_legs([]), BeatSequence(poi_beats=())) == ()
    assert reflection_slots(_route_with_legs([600]), BeatSequence(poi_beats=_stops(1))) == ()


def test_transit_beat_audio_counts_against_the_deficit():
    """A corpus transit beat that would fire on the leg absorbs the deficit."""
    stops = list(_stops(2))
    transit_beat = _beat(
        "t1",
        "p1",
        body="Leave p0 and walk along the quay toward p1.",
        narrative_function="transition",
        trigger_address="from p0",
        est_spoken_seconds=150,
    )
    stops[1] = POIBeats(
        poi_id="p1",
        poi_name="p1",
        ordering_strategy="sub_location",
        beats=(_beat("b1", "p1"), transit_beat),
    )
    # Leg walks 200s; transit beat speaks 150s -> deficit 50 < 90 -> ineligible.
    route = _route_with_legs([10, 200])
    assert reflection_slots(route, BeatSequence(poi_beats=tuple(stops))) == ()
    # Without the beat the same leg is eligible (deficit 196).
    assert reflection_slots(route, BeatSequence(poi_beats=_stops(2))) == (1,)


# ---------------------------------------------------------------------------
# Whitelist
# ---------------------------------------------------------------------------


def test_glue_reflection_is_whitelisted_and_traceable():
    assert GLUE_REFLECTION in GLUE_LABELS

    stops = _stops(2)
    script = Script(
        city_slug="paris",
        generated_at=dt.datetime(2026, 7, 1, tzinfo=dt.UTC).isoformat(),
        inputs=TourInput(start=(48.85, 2.36), duration_min=60, city_slug="paris"),
        total_audio_seconds=10,
        total_walking_seconds=10,
        total_walk_distance_m=10,
        total_planned_seconds=20,
        selected_pois=(),
        lens_coverage={},
        script=(
            Sentence(text="A fact.", source_id="b0", source_type="beat", stop_idx=0),
            Sentence(
                text="You've now seen two sides of the same story.",
                source_id=GLUE_REFLECTION,
                source_type="glue",
                stop_idx=1,
            ),
        ),
        validation=ValidationReport(),
    )
    report = validate_script(script, BeatSequence(poi_beats=stops))
    assert report.untraceable_sentences == ()
    assert report.forbidden_phrase_hits == ()
