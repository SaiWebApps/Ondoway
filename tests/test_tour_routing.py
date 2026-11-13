"""routing.py: pace + haversine + dwell math."""

from __future__ import annotations

import math
from datetime import date

import pytest

from src.tour.contract import POI, BeatRef
from src.tour.routing import (
    DWELL_FRACTION,
    HAVERSINE_CORRECTION,
    PACE_KMH,
    REACH_PACE_KMH,
    WALK_FRACTION,
    beat_spoken_seconds,
    civil_dusk_local,
    envelope_radius_m,
    governor_allowance_seconds,
    haversine_m,
    insertion_cost_seconds,
    insertion_extra_at_index,
    pace_corrected_walk_seconds,
    path_walk_seconds,
    planned_audio_seconds,
    planned_total_seconds,
    summarise_route,
    target_dwell_seconds,
    walk_budget_seconds,
)

# ---------------------------------------------------------------------------
# Haversine
# ---------------------------------------------------------------------------


def test_haversine_zero_when_same_point():
    assert haversine_m(48.85, 2.35, 48.85, 2.35) == 0.0


def test_haversine_symmetric():
    a = haversine_m(48.85, 2.35, 48.86, 2.36)
    b = haversine_m(48.86, 2.36, 48.85, 2.35)
    assert math.isclose(a, b, abs_tol=1e-6)


def test_haversine_paris_known_distance():
    # 0.01° latitude at Paris ≈ 1112 m. Reference value ±5m.
    d = haversine_m(48.85, 2.35, 48.86, 2.35)
    assert 1107 < d < 1117


def test_haversine_pdv_to_notre_dame():
    # Place des Vosges → Notre-Dame: roughly 1.1 km haversine.
    d = haversine_m(48.8555, 2.3656, 48.852966, 2.349902)
    assert 1100 < d < 1300


# ---------------------------------------------------------------------------
# Pace math
# ---------------------------------------------------------------------------


def test_pace_corrected_zero_distance():
    assert pace_corrected_walk_seconds(0) == 0


def test_pace_corrected_negative_clamps_to_zero():
    assert pace_corrected_walk_seconds(-100) == 0


def test_pace_corrected_1km_haversine():
    # 1000m x 1.35 = 1350m at 3 km/h (50 m/min) → 27 min = 1620s.
    secs = pace_corrected_walk_seconds(1000)
    assert secs == 1620


def test_pace_corrected_500m_haversine():
    # 500m x 1.35 = 675m at 50 m/min → 13.5 min = 810s.
    assert pace_corrected_walk_seconds(500) == 810


def test_pace_constants_lock():
    assert PACE_KMH == 3.0
    assert REACH_PACE_KMH == 3.0
    assert HAVERSINE_CORRECTION == 1.35
    assert math.isclose(WALK_FRACTION + DWELL_FRACTION, 1.0)


def test_the_reach_radius_does_not_follow_the_walking_pace(monkeypatch):
    """How far to SEND someone is not a consequence of how fast they walk.

    These are two equal numbers today, which is exactly why this needs a test
    rather than an inequality: equal values are a coincidence waiting to be
    mistaken for a rule, and the next person to correct the walking pace —
    3.0 km/h is a stroll, not a walk — would otherwise widen the search region
    by half without meaning to. At a 300-minute request that takes the radius
    from 4,444 m to 6,667 m, and Parc de la Villette sits 5,614 m from Rue
    Royale, so it re-enters the circle that was shrunk to exclude it — a shrink
    undone through a constant with nothing to do with it.

    This raises the WALKING pace and asserts the reach is untouched.
    """
    import src.tour.routing as routing_module

    before = envelope_radius_m(300, round_trip=False)
    monkeypatch.setattr(routing_module, "PACE_KMH", 4.5)
    after = envelope_radius_m(300, round_trip=False)

    assert after == before, (
        f"walking faster widened the reach from {before:.0f} m to {after:.0f} m. "
        f"The radius must follow REACH_PACE_KMH, which is a product choice, not "
        f"PACE_KMH, which is a measurement of walking speed."
    )
    assert math.isclose(before, 4444.4, rel_tol=1e-3)


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------


def test_planned_total_60min():
    # The nominal fraction is 1.00 since 2026-08-04, so a 60-minute request is planned
    # to 60 minutes of active time. It was 2988s (0.83) under the deleted flat policy.
    assert planned_total_seconds(60) == 3600


def test_walk_budget_60min():
    # 60 x 1.00 x 0.40 x 60 = 1440s. Was 1195 under the deleted flat 0.83.
    assert walk_budget_seconds(60) == 1440


def test_audio_budget_60min():
    # 60 x 1.00 x 0.60 x 60 = 2160s. Was 1793 under the deleted flat 0.83.
    assert target_dwell_seconds(60) == 2160


def test_envelope_round_trip_is_half_of_one_way():
    one_way = envelope_radius_m(60, round_trip=False)
    rt = envelope_radius_m(60, round_trip=True)
    assert math.isclose(rt, one_way / 2, rel_tol=1e-9)


def test_envelope_60min_one_way_paris():
    # walk_min = 60 x 1.00 x 0.40 = 24.0, x3 km/h / 1.35 -> ~889 m. It was ~738 m under
    # the deleted flat 0.83 policy.
    r = envelope_radius_m(60, round_trip=False)
    assert 880 < r < 895


def test_envelope_zero_duration():
    assert envelope_radius_m(0, round_trip=False) == 0.0


# ---------------------------------------------------------------------------
# Dwell
# ---------------------------------------------------------------------------


def test_a_stops_length_is_no_longer_a_property_of_its_tier():
    """DELETED 2026-08-06, and this test is the guard against it coming back.

    Four tests used to live here pinning a flat per-tier dwell: 300 s for tiers 5
    and 4, 150 s for tier 3, 0 for tier 1. They asserted the table faithfully; the
    table was the problem. It said a stop's length is a property of how FAMOUS a
    place is, so every tier-5 anchor got the same five minutes — simultaneously far
    too little for the Louvre and far too much for a bridge — and nothing in it
    could express two visitors spending 33 and 8 minutes at the same courtyard.

    A stop's length is now min(what the place absorbs, what this visitor's interest
    justifies, the party's ceiling), floored at its own narration. It is priced once
    in selection, where the corpus snapshot is open, and carried on
    ``Route.planned_visit_seconds``. See src/tour/visit_time.py and
    tests/test_tour_visit_time.py.

    Rewritten rather than deleted, because a deleted test is a guard nobody misses.
    """
    import src.tour.routing as routing_module

    assert not hasattr(routing_module, "compute_dwell_seconds"), (
        "compute_dwell_seconds is back. A stop's length must not be derived from "
        "its importance tier; price it through src/tour/visit_time.py instead."
    )
    assert not hasattr(routing_module, "DWELL_SECONDS_BY_TIER"), (
        "DWELL_SECONDS_BY_TIER is back. See src/tour/visit_time.py."
    )


# ---------------------------------------------------------------------------
# summarise_route
# ---------------------------------------------------------------------------


def _poi(pid: str, lat: float, lng: float) -> POI:
    return POI(id=pid, name=pid, tier=5, poi_role="stop", lat=lat, lng=lng)


def test_summarise_route_empty():
    r = summarise_route(
        (), start_lat=48.0, start_lng=2.0, round_trip=False, duration_min=60, spine_area=None
    )
    assert r.pois == ()
    assert r.transits == ()
    assert r.total_walk_seconds == 0


def test_summarise_route_single_one_way_has_one_segment():
    r = summarise_route(
        [_poi("a", 48.86, 2.35)],
        start_lat=48.85,
        start_lng=2.35,
        round_trip=False,
        duration_min=60,
        spine_area="Le Marais",
    )
    assert len(r.transits) == 1
    assert r.transits[0].from_poi_id is None
    assert r.transits[0].to_poi_id == "a"
    assert r.spine_area == "Le Marais"


def test_summarise_route_round_trip_appends_return_segment():
    r = summarise_route(
        [_poi("a", 48.86, 2.35), _poi("b", 48.87, 2.36)],
        start_lat=48.85,
        start_lng=2.35,
        round_trip=True,
        duration_min=90,
        spine_area=None,
    )
    assert r.transits[-1].to_poi_id is None
    # 3 transits: start→a, a→b, b→start.
    assert len(r.transits) == 3


def test_summarise_route_carries_the_queue_map_a_rebuild_hands_back():
    """The seconds of line the planner priced at each stop's arrival hour are
    priced at selection's one site against the corpus
    snapshot — compose's rebuild has no such site, so the map is the seventh keyword
    extra, handed back exactly as `planned_visit_seconds` is. Omitted, it is the empty
    map every caller got before: no stop is queued, byte-identical."""
    r = summarise_route(
        [_poi("a", 48.86, 2.35), _poi("b", 48.87, 2.36)],
        start_lat=48.85,
        start_lng=2.35,
        round_trip=False,
        duration_min=60,
        spine_area=None,
        planned_queue_seconds={"a": 28 * 60},
    )
    assert r.planned_queue_seconds == {"a": 28 * 60}
    bare = summarise_route(
        [_poi("a", 48.86, 2.35)],
        start_lat=48.85, start_lng=2.35, round_trip=False, duration_min=60, spine_area=None,
    )
    assert bare.planned_queue_seconds == {}
    # The door (which side of the door the visit lives on) and the placed OUTSIDE
    # seconds ride the same way — the eighth and ninth extras, empty by default.
    doors = summarise_route(
        [_poi("a", 48.86, 2.35)],
        start_lat=48.85, start_lng=2.35, round_trip=False, duration_min=60, spine_area=None,
        visit_goes_inside={"a": True}, planned_outside_seconds={"a": 15 * 60},
    )
    assert doors.visit_goes_inside == {"a": True}
    assert doors.planned_outside_seconds == {"a": 15 * 60}
    assert bare.visit_goes_inside == {} and bare.planned_outside_seconds == {}


def test_summarise_route_totals_use_routed_time_and_distance_when_available():
    class _Routed:
        def route(self, *_coords):
            return (321, 456.5, "encoded-shape")

    r = summarise_route(
        [_poi("a", 48.86, 2.35)],
        start_lat=48.85,
        start_lng=2.35,
        round_trip=False,
        duration_min=60,
        spine_area=None,
        routing_client=_Routed(),
    )

    assert r.routed is True
    assert r.transits[0].leg_seconds == 321
    assert r.transits[0].leg_distance_m == 456.5
    assert r.total_walk_seconds == 321
    assert r.total_walk_distance_m == 456.5


# ---------------------------------------------------------------------------
# insertion_cost_seconds
# ---------------------------------------------------------------------------


def test_insertion_cost_picks_cheapest_position():
    # ordered: A (north), B (south). Candidate C is east of B → best to insert
    # after B (idx 2).
    a = _poi("A", 48.870, 2.350)
    b = _poi("B", 48.850, 2.350)
    c = _poi("C", 48.851, 2.380)
    extra, idx = insertion_cost_seconds(
        c, [a, b], start_lat=48.860, start_lng=2.350, round_trip=False
    )
    assert idx == 2
    assert extra > 0


def test_insertion_cost_empty_route_inserts_at_zero():
    extra, idx = insertion_cost_seconds(
        _poi("A", 48.86, 2.36),
        [],
        start_lat=48.85,
        start_lng=2.35,
        round_trip=False,
    )
    assert idx == 0
    assert extra > 0


def test_insertion_extra_local_delta_equals_the_whole_path_splice_difference():
    """The local three-leg delta IS the whole-path difference, leg for leg.

    ``insertion_extra_at_index`` is computed as leg(prev→cand) + leg(cand→next)
    minus leg(prev→next); every other leg is byte-identical between the base and
    spliced paths, so the two spellings must agree exactly — including under an
    ASYMMETRIC leg function (a routed leg is directed: stairs one way). The
    whole-path spelling this replaced re-summed both paths per candidate index,
    which the certification repair's candidate enumeration turned into 28.8M
    haversine calls in 70 s on one 400-minute request (measured 2026-08-28).
    """

    def asymmetric_fn(lat1, lng1, lat2, lng2):
        base = round(haversine_m(lat1, lng1, lat2, lng2))
        return base + (7 if (lat1, lng1) < (lat2, lng2) else 3)

    start = (48.860, 2.350)
    ordered = [
        _poi("A", 48.870, 2.350),
        _poi("B", 48.850, 2.350),
        _poi("C", 48.851, 2.380),
    ]
    cand = _poi("X", 48.858, 2.365)
    ends = [
        {"round_trip": False, "fixed_end": None},
        {"round_trip": True, "fixed_end": None},
        {"round_trip": False, "fixed_end": (48.853, 2.3499)},
    ]
    for fn in (None, asymmetric_fn):
        for shape in ends:
            for prefix_len in range(len(ordered) + 1):
                stops = ordered[:prefix_len]
                for idx in range(len(stops) + 1):
                    base_coords = [start, *((p.lat, p.lng) for p in stops)]
                    new_pois = [*stops[:idx], cand, *stops[idx:]]
                    new_coords = [start, *((p.lat, p.lng) for p in new_pois)]
                    if shape["fixed_end"] is not None:
                        base_coords.append(shape["fixed_end"])
                        new_coords.append(shape["fixed_end"])
                    elif shape["round_trip"]:
                        base_coords.append(start)
                        new_coords.append(start)
                    reference = path_walk_seconds(new_coords, fn) - path_walk_seconds(
                        base_coords, fn
                    )
                    got = insertion_extra_at_index(
                        cand,
                        stops,
                        idx,
                        start_lat=start[0],
                        start_lng=start[1],
                        round_trip=shape["round_trip"],
                        leg_seconds_fn=fn,
                        fixed_end=shape["fixed_end"],
                    )
                    assert got == reference, (
                        f"local delta {got} != whole-path difference {reference} "
                        f"(stops={len(stops)}, idx={idx}, shape={shape}, "
                        f"fn={'asymmetric' if fn else 'default'})"
                    )


# ---------------------------------------------------------------------------
# beat_spoken_seconds / planned_audio_seconds — the shared audio clock
# The single source of truth generation, reflection, and density now defer to.
# ---------------------------------------------------------------------------


def _beat(est: int = 0, wc: int = 0) -> BeatRef:
    return BeatRef(id="b", poi_id="p", est_spoken_seconds=est, word_count=wc)


def test_beat_spoken_seconds_prefers_positive_est_override():
    # est_spoken_seconds wins when > 0, even against a large word_count.
    assert beat_spoken_seconds(_beat(est=300, wc=100000)) == 300


def test_beat_spoken_seconds_word_count_fallback_150wpm():
    # est == 0 -> word_count at 150 wpm: 150 words = 60s; 375 words = 150s.
    assert beat_spoken_seconds(_beat(est=0, wc=150)) == 60
    assert beat_spoken_seconds(_beat(est=0, wc=375)) == 150


def test_beat_spoken_seconds_nonpositive_est_falls_through_to_word_count():
    # A non-positive est must NOT be used (density's historical > 0 guard, now
    # shared): it falls through to word_count. The live corpus has no negatives;
    # this pins the guard so a future negative never ships as spoken seconds.
    assert beat_spoken_seconds(_beat(est=-5, wc=150)) == 60
    assert beat_spoken_seconds(_beat(est=0, wc=150)) == 60


def test_beat_spoken_seconds_zero_when_both_empty():
    assert beat_spoken_seconds(_beat(est=0, wc=0)) == 0
    assert beat_spoken_seconds(_beat(est=-9, wc=0)) == 0


@pytest.mark.parametrize(
    "wc", [0, 1, 2, 3, 5, 7, 10, 37, 149, 150, 151, 999, 2042, 2286, 3154, 5000]
)
def test_beat_spoken_seconds_word_count_form_matches_density_2point5(wc: int):
    # BYTE-IDENTITY GUARD: the shared 150 wpm fallback (round(wc/150*60)) must
    # equal density's historical round(wc/2.5) for every word_count, or the
    # density gate (audio_capacity_s) would shift and reshape routes. Both are
    # 2*wc/5, which is never a rounding tie for integer wc.
    assert beat_spoken_seconds(_beat(est=0, wc=wc)) == round(wc / 2.5)


def test_planned_audio_seconds_sums_the_plan():
    beats = [_beat(est=300, wc=0), _beat(est=0, wc=150), _beat(est=0, wc=0)]
    assert planned_audio_seconds(beats) == 360  # 300 + 60 + 0
    assert planned_audio_seconds([]) == 0


# ---------------------------------------------------------------------------
# governor_allowance_seconds — the per-stop share-governor allowance
# (ratified 2026-07-03: budget / min(3, d//10) = budget/3 for d>=30).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("d", [30, 45, 60, 90, 120])
def test_governor_allowance_is_budget_over_three_for_normal_durations(d: int):
    # d>=30 -> min(3, d//10)=3 -> allowance = target_dwell_seconds(d)//3.
    assert governor_allowance_seconds(d) == target_dwell_seconds(d) // 3


def test_governor_allowance_concrete_values():
    # 30-min budget 1080 -> 360s (6 min); 90-min budget 3240 -> 1080s (18 min). Both
    # moved with the nominal fraction from 0.83 to 1.00 (they were 298 and 896).
    assert governor_allowance_seconds(30) == 360
    assert governor_allowance_seconds(90) == 1080


def test_governor_allowance_divisor_floors_for_short_durations():
    # d<20 -> d//10 in {0,1} -> divisor floored at 1 -> allowance == full budget
    # (very short tours seat ~1 stop; no incidental cap needed). No ZeroDivision.
    assert governor_allowance_seconds(15) == target_dwell_seconds(15)
    assert governor_allowance_seconds(5) == target_dwell_seconds(5)
    # d in [20,29] -> d//10=2 -> budget/2.
    assert governor_allowance_seconds(25) == target_dwell_seconds(25) // 2


# ---------------------------------------------------------------------------
# Civil dusk (the after-dark finish's clock)
# ---------------------------------------------------------------------------

# Notre-Dame forecourt — the routing-engine test convention's Paris point.
_DUSK_LAT, _DUSK_LNG = 48.8530, 2.3499


def test_civil_dusk_paris_summer_evening():
    """A Paris August evening keeps usable light until well past 21:00 local.

    Reference: civil dusk in Paris on 2026-08-15 is ~21:35 CEST. The NOAA
    approximation is good to a few minutes; the band below is wide enough to
    hold it and narrow enough that a UTC-vs-local or solar-vs-civil confusion
    (a 1-2 h error) cannot pass. Dusk is the trigger for the after-dark finish,
    which reads good_after_dark.
    """
    dusk = civil_dusk_local(date(2026, 8, 15), _DUSK_LAT, _DUSK_LNG)
    assert dusk is not None
    assert dusk.tzinfo is None  # same naive-local clock as TourInput.start_datetime
    assert dusk.date() == date(2026, 8, 15)
    minutes = dusk.hour * 60 + dusk.minute
    assert 21 * 60 + 20 <= minutes <= 22 * 60, f"August dusk landed at {dusk:%H:%M}"


def test_civil_dusk_paris_winter_evening():
    """A Paris December day goes dark before 18:00 (~17:30 CET on the 15th) —
    the season the after-dark finish rule exists for."""
    dusk = civil_dusk_local(date(2026, 12, 15), _DUSK_LAT, _DUSK_LNG)
    assert dusk is not None
    minutes = dusk.hour * 60 + dusk.minute
    assert 17 * 60 + 10 <= minutes <= 17 * 60 + 50, f"December dusk landed at {dusk:%H:%M}"


def test_civil_dusk_moves_with_the_season():
    """June dusk is later than December dusk — the monotonic sanity check that
    catches a sign error in the hour-angle without pinning exact ephemeris."""
    june = civil_dusk_local(date(2026, 6, 20), _DUSK_LAT, _DUSK_LNG)
    december = civil_dusk_local(date(2026, 12, 20), _DUSK_LAT, _DUSK_LNG)
    assert june is not None and december is not None
    assert (june.hour, june.minute) > (december.hour, december.minute)


def test_civil_dusk_outside_known_regions_is_no_verdict():
    """A coordinate outside every known civil-zone region answers None — no
    zone means no dusk verdict, and the finish rule degrades to today's
    behaviour (the weather door's fail-open contract; a wrong political
    timezone would misjudge dusk by hours, worse than no answer)."""
    assert civil_dusk_local(date(2026, 8, 15), 40.7128, -74.0060) is None  # New York


def test_civil_dusk_is_deterministic():
    a = civil_dusk_local(date(2026, 8, 15), _DUSK_LAT, _DUSK_LNG)
    b = civil_dusk_local(date(2026, 8, 15), _DUSK_LAT, _DUSK_LNG)
    assert a == b
