"""density.py: tourability assessment. Pure-function tests.

These tests pin the density formula's math, the status
table, and the round-trip-vs-one-way differential against
hand-computed expected values.
"""

from __future__ import annotations

import math

from src.tour.contract import POI, BeatRef, TourInput
from src.tour.density import (
    GREEN_FILL_RATIO_MIN,
    YELLOW_FILL_RATIO_MIN,
    TourabilityAssessment,
    TourabilityRefusedError,
    assess,
)
from src.tour.routing import envelope_radius_m

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _poi(
    pid: str,
    *,
    tier: int = 5,
    role: str = "stop",
    lat: float,
    lng: float,
    beat_count: int = 5,
    typical_duration_min: int = 0,
    visit_seconds_inside: int | None = None,
) -> POI:
    # The two visit arguments default to the CONTRACT defaults (0 and None), so a
    # POI built without them prices at zero visit seconds and the gate's capacity
    # collapses to exactly the beat seconds it counted before 2026-08-06. That is
    # what keeps every hand-computed fixture in this file valid across the
    # currency move rather than needing re-derivation.
    return POI(
        id=pid,
        name=pid,
        tier=tier,
        poi_role=role,
        lat=lat,
        lng=lng,
        areas=("Le Marais",),
        beat_count=beat_count,
        typical_duration_min=typical_duration_min,
        visit_seconds_inside=visit_seconds_inside,
    )


def _beat(
    bid: str,
    poi_id: str,
    *,
    est_spoken_seconds: int = 60,
    word_count: int = 0,
    active: bool = True,
) -> BeatRef:
    return BeatRef(
        id=bid,
        poi_id=poi_id,
        est_spoken_seconds=est_spoken_seconds,
        word_count=word_count,
        active_status="active" if active else "deprecated",
    )


def _input(start, duration_min, *, round_trip=False) -> TourInput:
    return TourInput(
        start=start,
        duration_min=duration_min,
        city_slug="paris",
        round_trip=round_trip,
    )


PDV = (48.8555, 2.3656)
SACRE_COEUR = (48.88645, 2.34312)
PANTHEON = (48.84622, 2.34604)


# ---------------------------------------------------------------------------
# Pin the canonical formula values
# ---------------------------------------------------------------------------


def test_walk_radius_matches_canonical_formula():
    """walk_radius = (duration x nominal x 0.4 x 3000 / 1.35) / 60, halved if round-trip.

    The nominal fraction is 1.00 since 2026-08-04; it was a flat 0.83 until the legacy
    planning policy was deleted, which is why this radius grew from ~738 m to ~889 m.
    """
    # 60-min one-way: 60 x 1.00 x 0.4 x 3000 / 1.35 / 60 = 888.9m
    expected_one_way = (60 * 1.00 * 0.4 * 3000 / 1.35) / 60
    assert math.isclose(envelope_radius_m(60, round_trip=False), expected_one_way, rel_tol=1e-6)
    assert math.isclose(envelope_radius_m(60, round_trip=True), expected_one_way / 2, rel_tol=1e-6)


def test_target_audio_matches_canonical_formula():
    """target_audio_s = duration x the policy's nominal fraction x 0.6 x 60.

    Nominal is 1.00 since 2026-08-04 (was a flat 0.83), so this is 2160 s, and it is now
    THE SAME NUMBER selection fills — the whole point of threading the policy into the
    density gate.
    """
    pois = [_poi("p", lat=PDV[0], lng=PDV[1])]
    beats = {"p": (_beat("b1", "p"),)}
    a = assess(_input(PDV, 60), pois, beats)
    expected = round(60 * 1.00 * 0.6 * 60)
    assert a.target_dwell_seconds == expected


# ---------------------------------------------------------------------------
# Hand-computed fixture: GREEN
# ---------------------------------------------------------------------------


def test_green_when_capacity_exceeds_target_with_4_anchors_compact():
    """Synthetic GREEN: 4 colocated tier-5 anchors, capacity > target."""
    pois = [
        _poi(f"p{i}", tier=5, lat=PDV[0] + 0.0001 * i, lng=PDV[1], beat_count=10) for i in range(4)
    ]
    beats = {
        f"p{i}": tuple(_beat(f"b{i}_{j}", f"p{i}", est_spoken_seconds=200) for j in range(10))
        for i in range(4)
    }
    a = assess(_input(PDV, 60, round_trip=False), pois, beats)
    # 40 beats x 200s = 8000s. target = 1793s. fill = 4.46.
    assert a.fill_ratio >= GREEN_FILL_RATIO_MIN
    assert a.anchor_candidate_count >= 4
    assert a.cluster_compactness < 0.6
    assert a.status == "GREEN"


# ---------------------------------------------------------------------------
# Hand-computed fixture: RED — empty radius
# ---------------------------------------------------------------------------


def test_red_when_zero_beats_in_radius():
    """Le Marais centroid 60-min round-trip with 0 beats in radius → RED."""
    a = assess(_input(PDV, 60, round_trip=True), [], {})
    assert a.status == "RED"
    assert a.fill_ratio == 0.0
    assert a.anchor_candidate_count == 0


def test_red_excludes_zero_beat_pois_from_anchor_count():
    """Anchor candidate count must NOT include POIs with 0 active beats."""
    pois = [
        _poi("zero_beats", tier=4, lat=PDV[0], lng=PDV[1], beat_count=0),
    ]
    beats = {"zero_beats": ()}
    a = assess(_input(PDV, 60, round_trip=False), pois, beats)
    assert a.anchor_candidate_count == 0
    assert a.fill_ratio == 0.0
    assert a.status == "RED"


def test_red_excludes_zero_active_beat_pois():
    """A POI with only deprecated beats counts as zero for the anchor pool."""
    pois = [_poi("p", tier=5, lat=PDV[0], lng=PDV[1], beat_count=2)]
    beats = {
        "p": (
            _beat("b1", "p", active=False),
            _beat("b2", "p", active=False),
        )
    }
    a = assess(_input(PDV, 60, round_trip=False), pois, beats)
    assert a.reachable_poi_count == 0
    assert a.anchor_candidate_count == 0


# ---------------------------------------------------------------------------
# Hand-computed fixture: YELLOW
# ---------------------------------------------------------------------------


def test_yellow_when_fill_in_yellow_band():
    """0.5 ≤ fill < 1.0 → YELLOW even if anchor count is fine."""
    # 5 colocated anchors, but each beat is short so capacity falls into [0.5, 1.0) target band.
    # target for 60min = 1793s. Aim for ~1200s capacity → fill = 0.67.
    pois = [
        _poi(f"p{i}", tier=5, lat=PDV[0] + 0.00005 * i, lng=PDV[1], beat_count=3) for i in range(5)
    ]
    beats = {
        f"p{i}": tuple(_beat(f"b{i}_{j}", f"p{i}", est_spoken_seconds=80) for j in range(3))
        for i in range(5)
    }
    a = assess(_input(PDV, 60, round_trip=False), pois, beats)
    # 15 beats x 80s = 1200s; target = 1793s; fill = 0.67.
    assert YELLOW_FILL_RATIO_MIN <= a.fill_ratio < GREEN_FILL_RATIO_MIN
    assert a.status == "YELLOW"


def test_yellow_attaches_max_supportable_duration():
    """When capacity is tight, max_supportable_duration is reported."""
    pois = [_poi("p", tier=5, lat=PDV[0], lng=PDV[1], beat_count=5)]
    beats = {"p": tuple(_beat(f"b{j}", "p", est_spoken_seconds=80) for j in range(5))}
    a = assess(_input(PDV, 60, round_trip=False), pois, beats)
    # capacity = 400s; max_supportable = 400 / (1.00x0.6x60) = 400/36 ~ 11 min
    assert a.max_supportable_duration_min is not None
    assert a.max_supportable_duration_min == int(400 / (1.00 * 0.6 * 60))


# ---------------------------------------------------------------------------
# Round-trip vs one-way differential
# ---------------------------------------------------------------------------


def test_round_trip_radius_smaller_than_one_way():
    """Same start, same duration: round-trip envelope is half the one-way."""
    one_way = envelope_radius_m(60, round_trip=False)
    round_trip = envelope_radius_m(60, round_trip=True)
    assert math.isclose(round_trip, one_way / 2.0, rel_tol=1e-9)


def test_round_trip_pulls_in_fewer_beats_than_one_way():
    """Same corpus, round-trip envelope reaches fewer POIs → lower fill."""
    # Place POIs at increasing distance from start so the envelope cut bites.
    pois = [
        _poi("near", tier=5, lat=PDV[0], lng=PDV[1] + 0.001, beat_count=5),  # ~73m
        _poi("mid", tier=5, lat=PDV[0], lng=PDV[1] + 0.005, beat_count=5),  # ~365m
        _poi("far", tier=5, lat=PDV[0], lng=PDV[1] + 0.009, beat_count=5),  # ~659m
    ]
    beats = {
        pid: tuple(_beat(f"{pid}_{j}", pid, est_spoken_seconds=120) for j in range(5))
        for pid in ("near", "mid", "far")
    }
    rt = assess(_input(PDV, 60, round_trip=True), pois, beats)
    ow = assess(_input(PDV, 60, round_trip=False), pois, beats)
    # round-trip radius = 369m; one-way = 738m. round-trip cuts mid + far.
    assert rt.reachable_poi_count <= ow.reachable_poi_count
    assert rt.fill_ratio < ow.fill_ratio


def test_round_trip_yellow_offers_one_way_alternative():
    """When round-trip is the bottleneck, suggest a tier-5 destination outside its envelope."""
    # Sparse near, but a tier-5 anchor with rich beats sits beyond the round-trip envelope.
    near = _poi("near", tier=4, lat=PDV[0], lng=PDV[1], beat_count=2)
    far_anchor = _poi(
        "Far Anchor",
        tier=5,
        lat=PDV[0],
        lng=PDV[1] + 0.008,  # ~590m east, between round-trip (369m) and one-way (738m)
        beat_count=20,
    )
    beats = {
        "near": (
            _beat("n1", "near", est_spoken_seconds=60),
            _beat("n2", "near", est_spoken_seconds=60),
        ),
        "Far Anchor": tuple(
            _beat(f"f{i}", "Far Anchor", est_spoken_seconds=200) for i in range(20)
        ),
    }
    a = assess(_input(PDV, 60, round_trip=True), [near, far_anchor], beats)
    # Round-trip envelope of 369m drops "Far Anchor" (590m). near is only 2 beats.
    # → status RED or YELLOW; the one-way alternative should name "Far Anchor".
    assert a.status != "GREEN"
    assert a.one_way_alternative_destination == "Far Anchor"


# ---------------------------------------------------------------------------
# POI role filter
# ---------------------------------------------------------------------------


def test_walk_by_only_pois_excluded_from_density():
    """walk_by_only POIs aren't anchor candidates, even with rich beats."""
    pois = [_poi("wb", tier=5, role="walk_by_only", lat=PDV[0], lng=PDV[1], beat_count=10)]
    beats = {"wb": tuple(_beat(f"b{i}", "wb", est_spoken_seconds=200) for i in range(10))}
    a = assess(_input(PDV, 60, round_trip=False), pois, beats)
    assert a.reachable_poi_count == 0
    assert a.anchor_candidate_count == 0


def test_setting_role_pois_count():
    """Setting-role POIs count toward density (matches selection's filter)."""
    pois = [
        _poi(f"s{i}", tier=5, role="setting", lat=PDV[0] + 0.0001 * i, lng=PDV[1], beat_count=5)
        for i in range(4)
    ]
    beats = {
        f"s{i}": tuple(_beat(f"s{i}_{j}", f"s{i}", est_spoken_seconds=200) for j in range(5))
        for i in range(4)
    }
    a = assess(_input(PDV, 60, round_trip=False), pois, beats)
    assert a.reachable_poi_count == 4
    assert a.anchor_candidate_count == 4


# ---------------------------------------------------------------------------
# Word-count fallback
# ---------------------------------------------------------------------------


def test_word_count_fallback_when_est_spoken_seconds_missing():
    """Beats without est_spoken_seconds use word_count / 2.5."""
    pois = [_poi("p", tier=5, lat=PDV[0], lng=PDV[1], beat_count=4)]
    beats = {
        "p": tuple(_beat(f"b{j}", "p", est_spoken_seconds=0, word_count=250) for j in range(4))
    }
    a = assess(_input(PDV, 60, round_trip=False), pois, beats)
    # 250/2.5 = 100s per beat x 4 beats = 400s
    assert a.dwell_capacity_seconds == 400


# ---------------------------------------------------------------------------
# Compactness diagnostic
# ---------------------------------------------------------------------------


def test_compactness_zero_when_one_anchor():
    """A single anchor → mean pairwise distance is undefined; clamp to 0."""
    pois = [_poi("solo", tier=5, lat=PDV[0], lng=PDV[1], beat_count=10)]
    beats = {"solo": tuple(_beat(f"b{i}", "solo", est_spoken_seconds=200) for i in range(10))}
    a = assess(_input(PDV, 60, round_trip=False), pois, beats)
    assert a.cluster_compactness == 0.0
    assert a.anchor_candidate_count == 1


def test_compactness_close_to_one_when_anchors_at_envelope_edge():
    """Anchors at opposite envelope edges → high compactness."""
    envelope_radius_m(60, round_trip=False)
    # Two anchors on opposite sides of the envelope (~radius apart).
    # 4 anchor candidates required for evaluation; place 2 east, 2 west.
    pois = [
        _poi(
            f"east{i}",
            tier=5,
            lat=PDV[0],
            lng=PDV[1] + 0.0085,  # ~624m east
            beat_count=5,
        )
        for i in range(2)
    ] + [
        _poi(
            f"west{i}",
            tier=5,
            lat=PDV[0],
            lng=PDV[1] - 0.0085,  # ~624m west
            beat_count=5,
        )
        for i in range(2)
    ]
    beats = {
        p.id: tuple(_beat(f"{p.id}_{j}", p.id, est_spoken_seconds=200) for j in range(5))
        for p in pois
    }
    a = assess(_input(PDV, 60, round_trip=False), pois, beats)
    assert a.cluster_compactness > 0.5  # spread across most of the envelope
    # That spread should kick this OUT of GREEN even with rich beats.
    assert a.status != "GREEN" or a.cluster_compactness <= 0.6


# ---------------------------------------------------------------------------
# Live-ish smoke (synthetic Le Marais)
# ---------------------------------------------------------------------------


def test_le_marais_dense_centroid_green():
    """Le Marais centroid 60-min round-trip should be GREEN (synthetic stand-in).

    An earlier finding said Le Marais is dense; this synthetic
    fixture pins that property in unit tests so a regression in the
    formula (e.g., a misplaced 0.83 factor) is caught.
    """
    # 8 tier-5 anchors clustered in 200m around Marais centroid, 6 beats each.
    pois = [
        _poi(
            f"marais{i}",
            tier=5,
            lat=48.857 + (i % 4) * 0.0008,
            lng=2.362 + (i // 4) * 0.0008,
            beat_count=6,
        )
        for i in range(8)
    ]
    beats = {
        p.id: tuple(_beat(f"{p.id}_{j}", p.id, est_spoken_seconds=240) for j in range(6))
        for p in pois
    }
    marais_centroid = (48.8580, 2.3620)
    a = assess(_input(marais_centroid, 60, round_trip=True), pois, beats)
    assert a.status == "GREEN"
    assert a.fill_ratio > 1.0
    assert a.anchor_candidate_count >= 4


# ---------------------------------------------------------------------------
# TourabilityRefusedError
# ---------------------------------------------------------------------------


def test_low_fill_with_tight_anchors_is_red_not_yellow():
    """Fill floor: fill < 0.5 AND tight anchor cluster must still be RED.

    The anchor-disjunct used to fire alone,
    which let a Sacré-Cœur 90min RT (fill 0.17, 4 colocated
    Montmartre anchors) slip through as YELLOW. Adding a fill ≥ 0.5
    floor on the anchor-disjunct closes the loophole.
    """
    pois = [
        _poi(f"p{i}", tier=5, lat=PDV[0] + 0.0001 * i, lng=PDV[1], beat_count=3) for i in range(4)
    ]
    beats = {
        f"p{i}": tuple(_beat(f"b{i}_{j}", f"p{i}", est_spoken_seconds=20) for j in range(3))
        for i in range(4)
    }
    a = assess(_input(PDV, 60, round_trip=True), pois, beats)
    # 12 beats x 20s = 240s; target round-trip 60min = 1793s; fill ≈ 0.13.
    # 4 colocated tier-5 anchors with ≥3 beats each → compactness near 0 →
    # would fire yellow_by_anchors without the floor. With floor, fill < 0.5
    # → RED.
    assert a.fill_ratio < 0.5
    assert a.anchor_candidate_count == 4
    assert a.status == "RED"


def test_alternative_finder_picks_richest_far_anchor():
    """`one_way_alternative_destination` should name the densest tier-5 POI
    inside the one-way envelope but outside the round-trip envelope."""
    near = _poi("near", tier=4, lat=PDV[0], lng=PDV[1], beat_count=1)
    # Two candidates beyond the round-trip envelope.
    poor_far = _poi("Poor Far", tier=5, lat=PDV[0], lng=PDV[1] + 0.0060, beat_count=2)
    rich_far = _poi("Rich Far", tier=5, lat=PDV[0], lng=PDV[1] + 0.0085, beat_count=20)
    beats = {
        "near": (_beat("n", "near", est_spoken_seconds=20),),
        "Poor Far": tuple(_beat(f"p{i}", "Poor Far", est_spoken_seconds=80) for i in range(2)),
        "Rich Far": tuple(_beat(f"r{i}", "Rich Far", est_spoken_seconds=200) for i in range(20)),
    }
    a = assess(_input(PDV, 60, round_trip=True), [near, poor_far, rich_far], beats)
    assert a.status != "GREEN"  # round-trip envelope can't reach far anchors
    assert a.one_way_alternative_destination == "Rich Far"


def test_refused_carries_assessment():
    a = TourabilityAssessment(
        status="RED",
        walk_radius_m=369.0,
        fill_ratio=0.05,
        dwell_capacity_seconds=90,
        target_dwell_seconds=1793,
        reachable_poi_count=1,
        reachable_beat_count=2,
        anchor_candidate_count=0,
        cluster_compactness=0.0,
        duration_min=60,
        round_trip=True,
    )
    exc = TourabilityRefusedError(a)
    assert exc.assessment is a
    assert "RED" in str(exc)


def test_surplus_fill_with_spread_anchors_is_yellow_not_red():
    """Status-table hole (density-critic finding, 2026-07-02): fill >= 1.0 with
    anchors 3-5 but compactness > 0.7 fell through EVERY clause to RED —
    yellow_by_fill requires fill < 1.0, yellow_by_anchors requires comp <= 0.7,
    GREEN requires anchors >= 4 AND comp <= 0.6. A pool with MORE than enough
    audio (observed live: the Champ-de-Mars-lawn start — fill 1.43, anchors 4,
    comp 0.75) was refused as 'insufficient corpus', which is self-
    contradictory. Surplus fill that isn't GREEN must be at least YELLOW
    (generate but warn)."""
    from src.tour.density import _status

    assert _status(fill_ratio=1.43, anchor_candidate_count=4, cluster_compactness=0.75) == "YELLOW"


def test_status_is_monotone_in_fill_ratio():
    """Adding audio capacity must never flip accept -> refuse. Pre-fix:
    fill 0.9 / anchors 2 -> YELLOW (yellow_by_fill has no anchor floor) but
    fill 1.2 / anchors 2 -> RED (falls out of the [0.5, 1.0) window with no
    other clause to catch it) — strictly more content produced a refusal."""
    from src.tour.density import _status

    rank = {"RED": 0, "YELLOW": 1, "GREEN": 2}
    below = _status(fill_ratio=0.9, anchor_candidate_count=2, cluster_compactness=0.0)
    above = _status(fill_ratio=1.2, anchor_candidate_count=2, cluster_compactness=0.0)
    assert rank[above] >= rank[below], (
        f"fill 1.2 -> {above} ranks below fill 0.9 -> {below}: refusal is not "
        f"monotone in audio capacity"
    )
    # And the floor is untouched: catastrophically thin stays RED.
    assert _status(fill_ratio=0.4, anchor_candidate_count=5, cluster_compactness=0.2) == "RED"


# ---------------------------------------------------------------------------
# Lens-specific fill (thin-for-your-interest disclosure)
# ---------------------------------------------------------------------------


def _lensed_beat(bid: str, poi_id: str, lenses: tuple[str, ...], *, secs: int = 60) -> BeatRef:
    return BeatRef(id=bid, poi_id=poi_id, est_spoken_seconds=secs, lenses=lenses)


def _lens_fixture():
    poi = _poi("anchor", lat=PDV[0], lng=PDV[1])
    beats = {
        "anchor": (
            _lensed_beat("b1", "anchor", ("dark_history",)),
            _lensed_beat("b2", "anchor", ("hidden_history",)),
            _lensed_beat("b3", "anchor", ("hidden_history",)),
            _lensed_beat("b4", "anchor", ()),  # no lens
        )
    }
    return [poi], beats


def test_on_lens_fill_is_none_without_requested_lenses():
    pois, beats = _lens_fixture()
    a = assess(_input(PDV, 60), pois, beats)
    assert a.on_lens_fill_ratio is None


def test_on_lens_fill_differs_by_interest_at_same_start():
    pois, beats = _lens_fixture()
    dark = assess(
        TourInput(start=PDV, duration_min=60, city_slug="paris", lenses=["dark_history"]),
        pois, beats,
    )
    hidden = assess(
        TourInput(start=PDV, duration_min=60, city_slug="paris", lenses=["hidden_history"]),
        pois, beats,
    )
    # Lens-agnostic overall fill is IDENTICAL for both tourists (the bug).
    assert dark.fill_ratio == hidden.fill_ratio
    # ...but the on-lens fill distinguishes them by EXACT audio: 1 dark beat (60s)
    # vs 2 hidden beats (120s). Non-zero, so a "count nothing" mutation is caught.
    target = dark.target_dwell_seconds
    assert dark.on_lens_fill_ratio == 60 / target > 0
    assert hidden.on_lens_fill_ratio == 120 / target
    assert hidden.on_lens_fill_ratio == 2 * dark.on_lens_fill_ratio
    # On-lens fill never exceeds the overall fill (a subset of the audio).
    assert dark.on_lens_fill_ratio < dark.fill_ratio


def test_the_gate_prices_both_halves_of_its_ratio_in_standing_still_seconds():
    """Capacity and target must be the same currency or the gate judges nothing.

    This module's own history is the argument. Its ``_target_dwell_seconds``
    docstring records the last time the two halves drifted: the gate judged a
    pool against 1793 s while the planner aimed at 2160 s, and served a thin
    tour as healthy. The target is in standing-still seconds, not narration
    seconds, so the capacity has to be in the same currency.

    THE MUTATION THIS CATCHES. Leave the numerator summing beat seconds and the
    ratio silently becomes narration-over-visits. A place with a big interior
    and one short beat — a museum, which is most of what an interest-led tour
    is made of — would then contribute 60 seconds of capacity for the 29
    minutes a visitor actually spends inside it, and a perfectly rich area
    would read as thin.
    """
    from src.tour.routing import route_planning_budget
    from src.tour.visit_time import stop_seconds, visit_seconds

    # One museum: five minutes outside, forty-five inside, and one 60-second beat.
    museum = _poi(
        "museum",
        lat=PDV[0],
        lng=PDV[1],
        typical_duration_min=5,
        visit_seconds_inside=2700,
    )
    beats = {"museum": (_beat("b1", "museum", est_spoken_seconds=60),)}

    a = assess(_input(PDV, 60), [museum], beats)

    # The denominator is the planner's own number, not a restatement of it.
    assert a.target_dwell_seconds == route_planning_budget(60).dwell_target_seconds

    # The numerator is what a visitor SPENDS there, not what the place SAYS.
    expected_visit = visit_seconds(museum, frozenset(), None)
    assert expected_visit > 60, "fixture is pointless unless the interior dominates"
    assert a.dwell_capacity_seconds == stop_seconds(expected_visit, 60)
    assert a.dwell_capacity_seconds > 60, (
        "capacity still equals the beat seconds, so the numerator never moved and "
        "the gate is dividing narration by visits"
    )

    # And the ratio is those two and nothing else.
    assert a.fill_ratio == a.dwell_capacity_seconds / a.target_dwell_seconds

    # A place with no interior is unchanged: its capacity IS its narration, which
    # is why every hand-computed fixture above still holds.
    street = _poi("street", lat=PDV[0], lng=PDV[1])
    b = assess(_input(PDV, 60), [street], {"street": (_beat("s1", "street"),)})
    assert b.dwell_capacity_seconds == 60


def test_the_longest_supported_duration_is_solved_in_the_same_currency():
    """`max_supportable_duration_min` is the gate's third reader of the currency.

    It answers "how long a tour does this area actually support?" by solving
    capacity against one minute's target. Solve a standing-still capacity
    against a narration target and the answer is wrong by the ratio between
    them — and it is shown to the traveller as a suggestion.
    """
    from src.tour.routing import route_planning_budget

    # Deliberately thin so the assessment is not GREEN and the diagnostic runs.
    thin = _poi(
        "thin",
        tier=3,
        lat=PDV[0],
        lng=PDV[1],
        typical_duration_min=4,
        visit_seconds_inside=None,
    )
    a = assess(_input(PDV, 60), [thin], {"thin": (_beat("t1", "thin"),)})

    assert a.status != "GREEN", "fixture must be thin or the diagnostic never runs"
    assert a.max_supportable_duration_min is not None
    per_minute = route_planning_budget(1).dwell_target_seconds
    assert a.max_supportable_duration_min == max(
        1, int(a.dwell_capacity_seconds / per_minute)
    )


def test_on_lens_matching_is_case_insensitive():
    pois, beats = _lens_fixture()
    a = assess(
        TourInput(start=PDV, duration_min=60, city_slug="paris", lenses=["DARK_HISTORY"]),
        pois, beats,
    )
    assert a.on_lens_fill_ratio is not None and a.on_lens_fill_ratio > 0
