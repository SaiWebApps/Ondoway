"""contract.py: input validation + dataclass roundtrip."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.tour.contract import (
    POI,
    BeatRef,
    BeatSequence,
    POIBeats,
    Promise,
    PromiseShape,
    Route,
    TourInput,
    TransitSegment,
)


def test_tour_input_minimal_valid():
    inp = TourInput(start=(48.8553, 2.3653), duration_min=60, city_slug="paris")
    assert inp.start == (48.8553, 2.3653)
    assert inp.round_trip is False
    assert inp.lenses is None


def test_tour_input_full_payload():
    inp = TourInput(
        start=(48.85675, 2.341033),
        duration_min=90,
        city_slug="paris",
        lenses=["historic_arch", "famous_residents"],
        round_trip=True,
        theme_hint="kings",
        start_label="Pont Neuf metro",
    )
    assert inp.lenses == ["historic_arch", "famous_residents"]
    assert inp.round_trip is True


def test_tour_input_strips_empty_lenses():
    inp = TourInput(
        start=(48.0, 2.0),
        duration_min=60,
        city_slug="paris",
        lenses=["historic_arch", "", "  "],
    )
    assert inp.lenses == ["historic_arch"]


def test_tour_input_empty_lens_list_normalized_to_none():
    inp = TourInput(start=(48.0, 2.0), duration_min=60, city_slug="paris", lenses=["", "  "])
    assert inp.lenses is None


def test_tour_input_rejects_bad_lat():
    with pytest.raises(ValidationError):
        TourInput(start=(120.0, 2.0), duration_min=60, city_slug="paris")


def test_tour_input_rejects_bad_lng():
    with pytest.raises(ValidationError):
        TourInput(start=(48.0, 200.0), duration_min=60, city_slug="paris")


def test_tour_input_rejects_zero_duration():
    with pytest.raises(ValidationError):
        TourInput(start=(48.0, 2.0), duration_min=0, city_slug="paris")


def test_tour_input_rejects_extra_keys():
    with pytest.raises(ValidationError):
        TourInput(
            start=(48.0, 2.0),
            duration_min=60,
            city_slug="paris",
            magic_field="oops",
        )


def test_tour_input_is_frozen():
    inp = TourInput(start=(48.0, 2.0), duration_min=60, city_slug="paris")
    with pytest.raises(ValidationError):
        inp.duration_min = 90


def test_tour_input_requires_city_slug():
    with pytest.raises(ValidationError):
        TourInput(start=(48.0, 2.0), duration_min=60, city_slug="")


def test_tour_input_accepts_end_destination():
    inp = TourInput(
        start=(48.8553, 2.3653), end=(48.8738, 2.2950), duration_min=90, city_slug="paris"
    )
    assert inp.end == (48.8738, 2.2950)


def test_tour_input_end_defaults_to_none():
    inp = TourInput(start=(48.8553, 2.3653), duration_min=60, city_slug="paris")
    assert inp.end is None


def test_tour_input_rejects_bad_end_lat():
    with pytest.raises(ValidationError):
        TourInput(start=(48.0, 2.0), end=(120.0, 2.0), duration_min=60, city_slug="paris")


def test_tour_input_rejects_bad_end_lng():
    with pytest.raises(ValidationError):
        TourInput(start=(48.0, 2.0), end=(48.0, 200.0), duration_min=60, city_slug="paris")


def test_tour_input_end_and_round_trip_are_mutually_exclusive():
    with pytest.raises(ValidationError):
        TourInput(
            start=(48.0, 2.0),
            end=(48.87, 2.29),
            duration_min=60,
            city_slug="paris",
            round_trip=True,
        )


def test_tour_input_round_trip_without_end_ok():
    inp = TourInput(start=(48.0, 2.0), duration_min=60, city_slug="paris", round_trip=True)
    assert inp.round_trip is True
    assert inp.end is None


def test_tour_input_end_round_trips_through_model_dump():
    inp = TourInput(start=(48.0, 2.0), end=(48.87, 2.29), duration_min=60, city_slug="paris")
    revived = TourInput(**inp.model_dump())
    assert revived.end == (48.87, 2.29)
    assert revived == inp


def test_poi_dataclass_roundtrip():
    p = POI(
        id="poi-1",
        name="Place des Vosges",
        tier=5,
        poi_role="setting",
        lat=48.8555,
        lng=2.3656,
        areas=("Le Marais", "Paris"),
        beat_count=59,
        matching_lens_beat_count=12,
    )
    dumped = p.model_dump()
    revived = POI(**dumped)
    assert revived == p


def test_beat_ref_defaults():
    b = BeatRef(id="b1", poi_id="p1")
    assert b.sub_location is None
    assert b.entities == ()
    assert b.lenses == ()


def test_route_holds_segments_and_budgets():
    poi = POI(id="p1", name="X", tier=5, poi_role="stop", lat=48.85, lng=2.35)
    seg = TransitSegment(from_poi_id=None, to_poi_id="p1", distance_m=120, walk_seconds=144)
    r = Route(
        pois=(poi,),
        transits=(seg,),
        total_walk_distance_m=120.0,
        total_walk_seconds=144,
        spine_area="Le Marais",
        target_dwell_seconds=1800,
        err_short_total_seconds=2988,
    )
    assert r.pois[0].name == "X"
    assert r.spine_area == "Le Marais"


def test_beat_sequence_holds_strategies():
    pb = POIBeats(
        poi_id="p1",
        poi_name="X",
        ordering_strategy="sub_location",
        beats=(BeatRef(id="b1", poi_id="p1"),),
    )
    seq = BeatSequence(poi_beats=(pb,))
    assert seq.poi_beats[0].ordering_strategy == "sub_location"


def test_beat_sequence_rejects_invalid_strategy():
    with pytest.raises(ValidationError):
        POIBeats(
            poi_id="p",
            poi_name="X",
            ordering_strategy="random",  # type: ignore[arg-type]
            beats=(),
        )


def test_a_poi_without_visit_capacity_still_constructs_and_defaults_safely():
    """The three visit-capacity fields must be additive, or every corpus breaks.

    The whole safety argument rests on this: the fields land on the
    contract BEFORE the capacity pass has run over any city, so a POI that knows
    nothing about them has to construct and report values that reproduce today's
    behaviour exactly.

    The `0` default is load-bearing rather than a placeholder. Generation floors a
    stop at `max(planned_visit, planned_audio)`, so a visit time of 0 means the
    stop lasts exactly as long as its narration — which is what happens today.
    A `None` default here would raise instead, and a non-zero one would silently
    lengthen every stop in every unpriced city.
    """
    poi = POI(
        id="no-capacity",
        name="A place nobody has priced yet",
        tier=3,
        poi_role="stop",
        lat=48.8566,
        lng=2.3522,
    )
    assert poi.typical_duration_min == 0
    assert poi.visit_seconds_inside is None
    assert poi.visit_basis == ""

    # And a fully priced POI carries them through unchanged.
    priced = POI(
        id="priced",
        name="Sainte-Chapelle",
        tier=5,
        poi_role="stop",
        lat=48.8554,
        lng=2.3450,
        typical_duration_min=15,
        visit_seconds_inside=3960,
        visit_basis="Modest exterior; inside is a 28-minute queue plus 38 under the glass.",
    )
    assert priced.typical_duration_min == 15
    assert priced.visit_seconds_inside == 3960
    assert priced.visit_basis.startswith("Modest exterior")


def test_every_transit_segment_declares_its_mode():
    """Walking is the only mode Release 1 plans, and it must be SAID.

    The field exists so adding public transport later is an addition rather than a
    rewrite. Without it, "these seconds are walking seconds" is an unstated
    assumption baked into every leg calculation — the pace correction, the walk
    budget, the reach envelope — and a transit leg would silently inherit all of
    them.
    """
    seg = TransitSegment(
        from_poi_id=None, to_poi_id="a", distance_m=100.0, walk_seconds=120
    )
    assert seg.mode == "walk"


def test_tour_input_pins_and_weather_default_to_todays_behaviour():
    """An input that pins nothing and carries no sky signal plans exactly as before.

    Pinning is the visitor's move, so the empty tuple is an open walk with no
    pins — today's behaviour, byte-identical. `weather=None` means no signal
    was fetched (weather is fetched, never asked), and an
    unsignalled request must plan byte-identically to a pre-weather request —
    the `start_datetime` None-means-no-clock precedent.
    """
    inp = TourInput(start=(48.8553, 2.3653), duration_min=60, city_slug="paris")
    assert inp.pinned_poi_ids == ()
    assert inp.weather is None


def test_tour_input_carries_pins_and_weather_and_round_trips():
    """The visitor may pin any offered stop into a promise.

    Pins and the weather signal must survive TourInput(**inp.model_dump()) —
    the `end` round-trip precedent — because persisted tour_input_json is how
    a session replans the same day (a pin rebuilds the day around the
    pinned chapel from the same stored request).
    """
    inp = TourInput(
        start=(48.8553, 2.3653),
        duration_min=90,
        city_slug="paris",
        pinned_poi_ids=("poi-chapel", "poi-market"),
        weather="rain",
    )
    assert inp.pinned_poi_ids == ("poi-chapel", "poi-market")
    assert inp.weather == "rain"
    revived = TourInput(**inp.model_dump())
    assert revived == inp


def test_tour_input_rejects_an_unknown_weather_signal():
    """"dry" | "rain" is the whole vocabulary — a decision, not a forecast adjective.

    Asserts the LITERAL rejected the value, not merely extra="forbid" refusing an
    unknown key: before the field exists this raises extra_forbidden and the test
    stays red, so it cannot pass without the implementation.
    """
    with pytest.raises(ValidationError) as exc_info:
        TourInput(start=(48.0, 2.0), duration_min=60, city_slug="paris", weather="snow")
    assert exc_info.value.errors()[0]["type"] == "literal_error"


def test_a_poi_the_queue_pass_has_not_reached_is_never_priced():
    """queue_class=None is the never-priced switch.

    A queue is a fourth kind of time — not walking, not being-at-a-place, not
    narration — priced separately and excluded entirely under a "wall" end.
    So the safe default for a corpus the audited queue pass has
    not reached is NO CLAIM AT ALL: None means unpassed, and an unpassed queue
    is NEVER priced. The int and string defaults are inert until `queue_class`
    is set, so no default can imply a real queue — "none" is the audited claim
    that there is no line; None is no claim at all (the visit-capacity
    precedent: an unpriced corpus reproduces today's behaviour exactly).
    """
    poi = POI(
        id="unqueued",
        name="A place the queue pass has not reached",
        tier=3,
        poi_role="stop",
        lat=48.8566,
        lng=2.3522,
    )
    assert poi.queue_class is None
    assert poi.queue_minutes_peak == 0
    assert poi.queue_minutes_offpeak == 0
    assert poi.queue_peak_hours == ""
    assert poi.queue_basis == ""

    # And a fully audited POI carries all five columns through unchanged.
    queued = POI(
        id="queued",
        name="Sainte-Chapelle",
        tier=5,
        poi_role="stop",
        lat=48.8554,
        lng=2.3450,
        queue_class="long",
        queue_minutes_peak=28,
        queue_minutes_offpeak=10,
        queue_peak_hours='[["10:00", "16:00"]]',
        queue_basis="Security screening line: 28 minutes at midday, 10 at opening.",
    )
    assert queued.queue_class == "long"
    assert queued.queue_minutes_peak == 28
    assert queued.queue_minutes_offpeak == 10
    assert queued.queue_peak_hours == '[["10:00", "16:00"]]'
    assert queued.queue_basis.startswith("Security screening")


def test_poi_rejects_an_unknown_queue_class():
    """none / short / long / unpredictable is the whole vocabulary.

    POI ignores unknown keys (extra="ignore"), so before the field exists this
    constructs silently and the test stays red on DID NOT RAISE — it cannot
    pass without the Literal actually existing.
    """
    with pytest.raises(ValidationError):
        POI(
            id="p",
            name="X",
            tier=3,
            poi_role="stop",
            lat=48.0,
            lng=2.0,
            queue_class="massive",  # type: ignore[arg-type]
        )


def test_promise_shape_and_promise_carry_the_visit_shape_and_nothing_else():
    """A promise is OUTPUT — the planner's obligation — never a scoring knob.

    A promise includes "the shape of the visit" — 65 minutes
    inside is a different promise from 15 minutes outside, and the queue is a
    third number again. Those three numbers, which side of the door the visit
    lives on, and whether the clock has already voided the door are the ENTIRE
    shape. The exact-field-set assertion is the mechanical form of that rule:
    a score, weight, or priority landing on either model turns the planner's
    obligations into knobs, and this test goes red the moment one appears.
    """
    assert set(PromiseShape.model_fields) == {
        "outside_seconds",
        "inside_seconds",
        "queue_seconds",
        "goes_inside",
        "closed_today",
    }
    # A promise also carries its clock WINDOW — arrival and departure, "" on a
    # dateless day — read off THE one arrival accumulation. A window is an
    # obligation ("Place des Vosges, around 11:30 to noon"), not a knob; it is
    # the third thing a promise IS: each promise carries a clock window.
    # Still exhaustive: anything else
    # landing here is a knob and this goes red.
    assert set(Promise.model_fields) == {
        "kind",
        "poi_id",
        "shape",
        "arrives_hhmm",
        "departs_hhmm",
    }


def test_promise_kinds_are_the_four_species_of_the_day():
    """The promise list, as a vocabulary: the interest anchor, the
    visitor's pin, a body stop, the finish. A detour is fabric, and
    fabric is never promised — "fabric may change silently; promises may not".
    """
    shape = PromiseShape(
        outside_seconds=900,
        inside_seconds=3900,
        queue_seconds=1680,
        goes_inside=True,
        closed_today=False,
    )
    for kind in ("anchor", "pinned", "rest", "finish"):
        assert Promise(kind=kind, poi_id="poi-chapel", shape=shape).kind == kind
    with pytest.raises(ValidationError):
        Promise(kind="detour", poi_id="poi-chapel", shape=shape)  # type: ignore[arg-type]


def test_promise_is_frozen_and_forbids_scoring_state():
    """extra="forbid" is what makes the no-knobs rule mechanical at runtime:
    a caller smuggling score= or priority= onto a promise is refused at
    construction, and a frozen promise cannot be edited after the planner
    swears it (a pin is the visitor's decision, not a preference).
    """
    shape = PromiseShape(
        outside_seconds=0,
        inside_seconds=0,
        queue_seconds=0,
        goes_inside=False,
        closed_today=True,
    )
    with pytest.raises(ValidationError):
        PromiseShape(
            outside_seconds=0,
            inside_seconds=0,
            queue_seconds=0,
            goes_inside=False,
            closed_today=False,
            score=1.0,
        )
    with pytest.raises(ValidationError):
        Promise(kind="finish", poi_id="p", shape=shape, priority=3)
    promise = Promise(kind="finish", poi_id="p", shape=shape)
    with pytest.raises(ValidationError):
        promise.poi_id = "other"


def test_promise_shape_rejects_negative_seconds():
    """The three numbers are durations, so ge=0 — the file's rule for every
    seconds field on a frozen model (walk_seconds, eta_seconds, ...)."""
    with pytest.raises(ValidationError):
        PromiseShape(
            outside_seconds=-1,
            inside_seconds=0,
            queue_seconds=0,
            goes_inside=False,
            closed_today=False,
        )


def test_route_promises_default_empty_and_carry_through():
    """Route.promises is additive in the `vignettes` mould.

    An unpromised Route — every Route today — must stay byte-identical, and a
    promise-native Route carries the planner's obligations so downstream
    phases can protect them through replans ("fabric may change silently;
    promises may not").
    """
    poi = POI(id="p1", name="X", tier=5, poi_role="stop", lat=48.85, lng=2.35)
    seg = TransitSegment(from_poi_id=None, to_poi_id="p1", distance_m=120, walk_seconds=144)
    bare = Route(
        pois=(poi,), transits=(seg,), total_walk_distance_m=120.0, total_walk_seconds=144
    )
    assert bare.promises == ()

    promised = Route(
        pois=(poi,),
        transits=(seg,),
        total_walk_distance_m=120.0,
        total_walk_seconds=144,
        promises=(
            Promise(
                kind="anchor",
                poi_id="p1",
                shape=PromiseShape(
                    outside_seconds=900,
                    inside_seconds=0,
                    queue_seconds=0,
                    goes_inside=False,
                    closed_today=False,
                ),
            ),
        ),
    )
    assert promised.promises[0].kind == "anchor"
    assert promised.promises[0].shape.outside_seconds == 900
