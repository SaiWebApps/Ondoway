"""Fixed-destination feasibility refusal.

When a tour carries a fixed end B, the routed A→B leg alone must fit inside the
reachability ceiling — since 2026-08-04 the certification band's maximum elapsed
time plus the materiality tolerance, not the bare walk budget. If it does not,
``select_route`` raises ``TourabilityRefusedError`` BEFORE the greedy, carrying:

- ``gap_minutes`` (int): how many minutes the routed A→B leg overshoots that
  ceiling.
- a ``"loop"`` alternative (drop B, walk an open loop from A at the requested
  duration).
- an ``"extend"`` alternative (keep B, lengthen the tour to the A→B-correct
  smallest duration whose ceiling covers the routed A→B leg).

These are REAL ``select_route`` runs over synthetic corpora — the cost
function is never mocked. ``end=None`` never enters the branch, so an
open walk's ordered POIs are untouched.
"""

from __future__ import annotations

import math

import pytest

from src.tour.contract import POI, BeatRef, TourInput
from src.tour.density import FeasibilityAlternative, TourabilityRefusedError
from src.tour.routing import (
    default_leg_seconds,
    envelope_radius_m,
    haversine_m,
    walk_budget_seconds,
)
from src.tour.selection import (
    CorpusSnapshot,
    bearing,
    in_wedge,
    poi_score,
    select_route,
)
from tests.test_tour_selection import _auto_beat_seconds, _density_fillers

PDV = (48.8555, 2.3656)


def _snap(pois: list[POI]) -> CorpusSnapshot:
    """CorpusSnapshot with synthetic beats sized to ONE STOP's audio ceiling.

    Every POI's whole beat set is worth ``MAX_DWELL_AUDIO_SECONDS`` (270 s), the
    most a single stop can ever speak. That matters because selection prices a
    stop twice — the greedy CREDITS a POI's planned audio, emission then CAPS it
    at 270 s — and the two prices only agree when the POI carries no more than
    one stop's worth. The previous flat 240 s per beat gave an 8-beat filler
    1920 s of credit against 270 s of delivery, so the greedy believed it had
    filled the hour after two stops and handed the certification repair a route
    that was nowhere near the requested duration.
    """
    beats: dict[str, tuple[BeatRef, ...]] = {
        p.id: tuple(
            BeatRef(
                id=f"{p.id}-b{i}",
                poi_id=p.id,
                est_spoken_seconds=_auto_beat_seconds(max(0, p.beat_count or 0)),
                active_status="active",
            )
            for i in range(max(0, p.beat_count or 0))
        )
        for p in pois
    }
    return CorpusSnapshot(
        pois=tuple(pois),
        beats_by_poi=beats,
        area_types={"Paris": "city", "Le Marais": "neighborhood"},
        adjacent_areas={},
        lens_neighbors={},
    )


# A destination far enough east of PdV that the routed (pace-corrected
# haversine) A→B leg dwarfs the walk budget of any short tour. We never
# hardcode the leg seconds — every assertion derives from the live
# routing functions so the test fails only if the BEHAVIOUR is wrong.
FAR_EAST_END = (PDV[0], PDV[1] + 0.060)  # ~4.4 km east; far beyond a 30-min budget
NEAR_END = (PDV[0], PDV[1] + 0.0004)  # ~30 m east; trivially inside any budget


def _green_snapshot(duration_min: int):
    """Background corpus rich enough to PLAN `duration_min`, not merely to be GREEN.

    Since 2026-08-04 clearing the density gate is no longer enough: the plan also
    has to land inside the certification TIME band (0.90-1.10 of the request), and
    the number of stops and the amount of walking that takes both scale with the
    requested duration. So the filler cluster is duration-scaled too.
    """
    return _snap(_density_fillers(PDV, duration_min=duration_min))


def _reach_ceiling(duration_min: int) -> int:
    """The A->B reachability ceiling selection actually applies.

    Since 2026-08-04 this is the planning band's MAXIMUM elapsed time plus the
    materiality tolerance, on every route shape. The legacy arm that compared the
    routed leg against the bare walk budget went with the flat 0.83 policy.
    """
    from src.tour.routing import (
        TIMEBOX_MATERIALITY_TOLERANCE_SECONDS,
        route_planning_budget,
    )

    return (
        route_planning_budget(duration_min).maximum_elapsed_seconds
        + TIMEBOX_MATERIALITY_TOLERANCE_SECONDS
    )


# ---------------------------------------------------------------------------
# Far A→B + short budget RAISES, carrying gap + loop + extend.
# ---------------------------------------------------------------------------


def test_far_end_short_budget_raises_with_gap_and_alternatives():
    snap = _green_snapshot(30)
    inp = TourInput(
        start=PDV, end=FAR_EAST_END, duration_min=30, city_slug="paris"
    )
    # Precondition for the test to be meaningful: the routed A→B leg really
    # does exceed what a 30-minute tour can reach (derived, not assumed). It
    # also exceeds the bare walk budget, which is the weaker of the two.
    t_ab = default_leg_seconds(PDV[0], PDV[1], FAR_EAST_END[0], FAR_EAST_END[1])
    ceiling = _reach_ceiling(30)
    assert t_ab > ceiling > walk_budget_seconds(30)

    with pytest.raises(TourabilityRefusedError) as excinfo:
        select_route(inp, snap)
    exc = excinfo.value

    # gap_minutes is a positive int equal to the ceil-overshoot in minutes, and
    # the quantity it overshoots is the reachability ceiling that selection now
    # enforces — the certification band's maximum elapsed time plus the
    # materiality tolerance. Before 2026-08-04 the gap was measured against the
    # bare walk budget; measuring it there now would over-report the shortfall
    # by the difference between the two, and the number is shown to the user.
    assert isinstance(exc.gap_minutes, int)
    assert exc.gap_minutes >= 1
    assert exc.gap_minutes == math.ceil((t_ab - ceiling) / 60)

    # At least a loop and an extend alternative, both structured. (Selection
    # may also add a 'closer_b' when an in-budget anchor exists — the fillers
    # here are in budget — so this is a subset check, not exact equality. The
    # closer_b path has its own dedicated tests below.)
    kinds = {alt.kind for alt in exc.alternatives}
    assert {"loop", "extend"} <= kinds
    by_kind = {alt.kind: alt for alt in exc.alternatives}

    loop = by_kind["loop"]
    assert isinstance(loop, FeasibilityAlternative)
    assert loop.drop_end is True
    assert loop.duration_min == 30  # loop at the requested duration

    extend = by_kind["extend"]
    assert extend.drop_end is False
    # The extend duration is the A→B-correct smallest duration: its budget
    # covers the routed leg, and it is genuinely longer than requested.
    assert extend.duration_min > 30
    # The reachability ceiling is the certification band's MAXIMUM elapsed time plus
    # the materiality tolerance, not the bare walk budget: the legacy walk-budget arm
    # was deleted 2026-08-04 with the flat 0.83 policy. So the extend duration is the
    # least minute whose ceiling covers the routed leg.
    assert _reach_ceiling(extend.duration_min) >= t_ab
    assert _reach_ceiling(extend.duration_min - 1) < t_ab


def test_extend_alternative_is_not_density_max_supportable():
    """The extend duration is derived from the A→B leg, independent of
    density.max_supportable_duration_min (which is None on a non-RED,
    fill-healthy corpus)."""
    snap = _green_snapshot(30)
    inp = TourInput(start=PDV, end=FAR_EAST_END, duration_min=30, city_slug="paris")
    with pytest.raises(TourabilityRefusedError) as excinfo:
        select_route(inp, snap)
    exc = excinfo.value
    # The corpus is fill-healthy near A, so assessment is not RED and the
    # diagnostic max_supportable is None — yet extend is a concrete int.
    assert exc.assessment.status != "RED"
    assert exc.assessment.max_supportable_duration_min is None
    extend = next(a for a in exc.alternatives if a.kind == "extend")
    assert isinstance(extend.duration_min, int)
    t_ab = default_leg_seconds(PDV[0], PDV[1], FAR_EAST_END[0], FAR_EAST_END[1])
    assert _reach_ceiling(extend.duration_min) >= t_ab
    assert _reach_ceiling(extend.duration_min - 1) < t_ab


# ---------------------------------------------------------------------------
# Within-budget A→B does NOT raise.
# ---------------------------------------------------------------------------


def test_near_end_within_budget_does_not_raise():
    snap = _green_snapshot(60)
    inp = TourInput(start=PDV, end=NEAR_END, duration_min=60, city_slug="paris")
    # The near leg is comfortably inside the budget.
    t_ab = default_leg_seconds(PDV[0], PDV[1], NEAR_END[0], NEAR_END[1])
    assert t_ab <= walk_budget_seconds(60)
    # No raise — a Route is returned.
    route = select_route(inp, snap)
    assert route is not None


# ---------------------------------------------------------------------------
# An over-budget A→B raises, never returns an empty plan. (The invariant lived in
# select_k_routes' first-flavour rule; the flavours died, the invariant is
# select_route's own.)
# ---------------------------------------------------------------------------


def test_select_route_raises_for_over_budget_end():
    snap = _green_snapshot(30)
    inp = TourInput(start=PDV, end=FAR_EAST_END, duration_min=30, city_slug="paris")
    with pytest.raises(TourabilityRefusedError) as excinfo:
        select_route(inp, snap)
    assert excinfo.value.gap_minutes is not None


# ---------------------------------------------------------------------------
# end=None NEVER enters the branch (the open-walk baseline holds).
# ---------------------------------------------------------------------------


def test_end_none_never_raises_even_when_corpus_spans_far():
    """A far POI in the corpus must NOT trigger the feasibility refusal when
    there is no fixed end — the branch is guarded on input.end is not None."""
    far_poi = POI(
        id="far-corner",
        name="Far Corner",
        tier=5,
        poi_role="stop",
        lat=FAR_EAST_END[0],
        lng=FAR_EAST_END[1],
        areas=("Le Marais",),
        beat_count=8,
    )
    snap = _snap([*_density_fillers(PDV, duration_min=30), far_poi])
    inp = TourInput(start=PDV, duration_min=30, city_slug="paris")  # end=None
    assert inp.end is None
    # No raise: open walk never consults the A→B feasibility branch.
    route = select_route(inp, snap)
    assert route is not None


def test_end_none_and_within_budget_end_produce_routes_no_refusal():
    """Both end=None and an in-budget end return Routes; neither refuses. The
    end=None path must be untouched by the fixed-end refusal branch."""
    snap = _green_snapshot(60)
    open_route = select_route(
        TourInput(start=PDV, duration_min=60, city_slug="paris"), snap
    )
    near_route = select_route(
        TourInput(start=PDV, end=NEAR_END, duration_min=60, city_slug="paris"), snap
    )
    assert open_route is not None
    assert near_route is not None


# ---------------------------------------------------------------------------
# closer_b: bearing/±45° wedge helper + empty-wedge fallback.
#
# All synthetic. Every expectation is DERIVED from the live functions
# (bearing, in_wedge, poi_score, walk_budget_seconds, default_leg_seconds) so
# a test fails only if the BEHAVIOUR is wrong, never against a hardcoded value.
# ---------------------------------------------------------------------------

# A duration whose walk budget is large enough that some anchors fit and some
# do not — chosen so the test can place candidates on both sides of the budget.
CLOSER_DURATION = 30


def _anchor(poi_id: str, dlat: float, dlng: float, *, tier: int = 5, beats: int = 8) -> POI:
    """A tier-≥3 'stop' anchor offset (dlat, dlng) degrees from PdV."""
    return POI(
        id=poi_id,
        name=poi_id,
        tier=tier,
        poi_role="stop",
        lat=PDV[0] + dlat,
        lng=PDV[1] + dlng,
        areas=("Le Marais",),
        beat_count=beats,
    )


def _in_budget(poi: POI, duration_min: int = CLOSER_DURATION) -> bool:
    """Live: is the routed A→poi leg inside the walk budget?"""
    t = default_leg_seconds(PDV[0], PDV[1], poi.lat, poi.lng)
    return t <= walk_budget_seconds(duration_min)


def _score(poi: POI, snap: CorpusSnapshot) -> float:
    """Live poi_score with the same neutral-spine/no-interest call the
    closer_b scan uses inside select_route."""
    return poi_score(poi, None, frozenset(), snap)


def _closer_b(exc: TourabilityRefusedError) -> FeasibilityAlternative | None:
    return next((a for a in exc.alternatives if a.kind == "closer_b"), None)


def _assert_not_red(inp: TourInput, snap: CorpusSnapshot) -> None:
    """The fixed-end refusal branch only runs when density is not RED (RED raises
    earlier at the gate). Assert that via the live assessor so each closer_b
    test provably exercises the branch."""
    from src.tour.density import assess as assess_tourability

    assert assess_tourability(inp, snap.pois, snap.beats_by_poi).status != "RED"


def test_closer_b_wedge_hit_picks_highest_score_inside_wedge():
    """With in-budget anchors inside the ±45° wedge toward B, closer_b targets
    the highest-poi_score one inside the wedge — not the higher-scoring anchor
    that sits OUTSIDE the wedge."""
    # B is far east (over budget → triggers the fixed-end refusal).
    end = FAR_EAST_END
    # in-wedge anchors: strongly EAST (large dlng, tiny dlat) → bearing ≈ 90°,
    # inside the east-pointing ±45° wedge. Both in budget. wedge_high has more
    # beats → strictly higher poi_score than wedge_low.
    wedge_low = _anchor("wedge-lo", 0.0, 0.0040, tier=5, beats=4)
    wedge_high = _anchor("wedge-hi", 0.00005, 0.0045, tier=5, beats=12)
    # out-of-wedge anchors: strongly NORTH (large dlat, ~0 dlng) → bearing ≈ 0°,
    # outside the east wedge. north_top has the single highest score of all and
    # MUST be ignored because the wedge is non-empty. The remaining north pads
    # bring the rich-anchor count to ≥6 so density clears via the rich-pool
    # escape hatch (fill ≥ 1.5 AND anchors ≥ 6) without forcing a tight cluster.
    north_top = _anchor("north-top", 0.0024, 0.0, tier=5, beats=40)
    north_pads = [
        _anchor(f"north-pad-{i}", 0.0014 + 0.0002 * i, 0.00005 * (i - 1), tier=5, beats=6)
        for i in range(4)
    ]
    anchors = [wedge_low, wedge_high, north_top, *north_pads]
    snap = _snap(anchors)

    inp = TourInput(start=PDV, end=end, duration_min=CLOSER_DURATION, city_slug="paris")
    _assert_not_red(inp, snap)

    # Preconditions, all derived from live functions.
    for a in anchors:
        assert _in_budget(a)
    assert in_wedge(PDV[0], PDV[1], end[0], end[1], wedge_low.lat, wedge_low.lng)
    assert in_wedge(PDV[0], PDV[1], end[0], end[1], wedge_high.lat, wedge_high.lng)
    for a in (north_top, *north_pads):
        assert not in_wedge(PDV[0], PDV[1], end[0], end[1], a.lat, a.lng)
    # The out-of-wedge anchor genuinely outscores both in-wedge ones, proving
    # the wedge gate (not raw score) drives the pick.
    assert _score(north_top, snap) > _score(wedge_high, snap) > _score(wedge_low, snap)

    with pytest.raises(TourabilityRefusedError) as excinfo:
        select_route(inp, snap)
    cb = _closer_b(excinfo.value)

    assert cb is not None
    assert cb.kind == "closer_b"
    assert cb.drop_end is True
    assert cb.duration_min == CLOSER_DURATION
    # Highest-score anchor INSIDE the wedge wins.
    assert cb.poi_id == wedge_high.id
    assert (cb.lat, cb.lng) == (wedge_high.lat, wedge_high.lng)
    # loop + extend still present and intact.
    kinds = {a.kind for a in excinfo.value.alternatives}
    assert {"loop", "extend"} <= kinds


def test_closer_b_empty_wedge_falls_back_to_highest_score_in_budget():
    """When NO in-budget anchor sits inside the ±45° wedge toward B, closer_b
    falls back to the highest-poi_score in-budget anchor regardless of
    bearing."""
    end = FAR_EAST_END  # wedge points east
    # All anchors strongly NORTH (large dlat, ~0 dlng) — outside the east wedge
    # — but in budget. fallback_top has the most beats → highest poi_score.
    north_a = _anchor("north-a", 0.0010, 0.0, tier=5, beats=5)
    north_b = _anchor("north-b", 0.0014, 0.00005, tier=5, beats=8)
    north_c = _anchor("north-c", 0.0018, -0.00005, tier=5, beats=14)
    fallback_top = _anchor("north-top", 0.0022, 0.0, tier=5, beats=30)
    anchors = [north_a, north_b, north_c, fallback_top]
    snap = _snap(anchors)

    inp = TourInput(start=PDV, end=end, duration_min=CLOSER_DURATION, city_slug="paris")
    _assert_not_red(inp, snap)

    # Precondition: the wedge is empty over EVERY anchor, and all are in budget.
    for a in anchors:
        assert _in_budget(a)
        assert not in_wedge(PDV[0], PDV[1], end[0], end[1], a.lat, a.lng)
    # fallback_top is the unique highest score.
    best = min(anchors, key=lambda p: (-_score(p, snap), p.id))
    assert best.id == fallback_top.id

    with pytest.raises(TourabilityRefusedError) as excinfo:
        select_route(inp, snap)
    cb = _closer_b(excinfo.value)

    assert cb is not None
    # Empty-wedge fallback = highest-score in-budget anchor overall.
    assert cb.poi_id == fallback_top.id
    assert (cb.lat, cb.lng) == (fallback_top.lat, fallback_top.lng)


def test_closer_b_omitted_when_no_anchor_within_budget():
    """When NO tier-≥3 anchor lies inside the walk budget, closer_b is omitted
    (loop + extend still offered).

    Density is kept out of RED by near-start TIER-2 'stop' POIs (audio fill,
    but NOT closer_b anchors — closer_b requires tier ≥ 3). The only tier-≥3
    anchors sit far east, beyond the walk budget, so closer_b finds nothing to
    offer."""
    end = FAR_EAST_END
    # Tier-2 'stop's near the start: they count toward density fill (role+beats)
    # but are NOT closer_b anchors (tier < 3). The COUNT is what tunes fill now:
    # each POI's whole beat set is worth one stop's audio ceiling (270 s) against
    # a 30-minute audio target of 1080 s, so three of them land fill at 0.75 —
    # density YELLOW (0.5 <= fill < 1.0 with zero tier-≥3 anchor candidates),
    # which still reaches the refusal branch because only RED raises early.
    fill_pois = [
        _anchor(f"t2-fill-{i}", 0.0003 + 0.0001 * i, 0.0, tier=2, beats=2) for i in range(3)
    ]
    # The only tier-≥3 anchor is far east, beyond the walk budget.
    far_anchor = _anchor("far-anchor", 0.0, 0.060, tier=5, beats=20)
    snap = _snap([*fill_pois, far_anchor])

    inp = TourInput(start=PDV, end=end, duration_min=CLOSER_DURATION, city_slug="paris")
    # Preconditions (live): density is not RED (so the refusal branch is reached),
    # and NO tier-≥3 anchor is within the walk budget.
    _assert_not_red(inp, snap)
    tier3plus = [p for p in snap.pois if p.tier >= 3]
    assert tier3plus and all(not _in_budget(p) for p in tier3plus)

    with pytest.raises(TourabilityRefusedError) as excinfo:
        select_route(inp, snap)
    exc = excinfo.value
    assert exc.assessment.status != "RED"  # branch reached, not early RED raise
    # loop + extend present, closer_b absent.
    kinds = {a.kind for a in exc.alternatives}
    assert kinds == {"loop", "extend"}
    assert _closer_b(exc) is None


def test_closer_b_tie_break_is_lowest_id():
    """Two in-budget, in-wedge anchors with IDENTICAL poi_score tie-break on
    ascending id."""
    end = FAR_EAST_END
    # Same tier + beats + role → identical poi_score (neutral spine, no lenses).
    # Both strongly EAST → inside the wedge, in budget. Ids chosen so the
    # ascending-id winner ("aaa-tie") is the one INSERTED SECOND, proving the
    # pick is id-driven, not iteration-order-driven.
    tie_hi = _anchor("zzz-tie", 0.0, 0.0042, tier=5, beats=10)
    tie_lo = _anchor("aaa-tie", 0.00005, 0.0044, tier=5, beats=10)
    # NORTH padding anchors (out of wedge) so density clears the rich-pool
    # escape hatch (≥6 rich anchors); they never enter the in-wedge pool so
    # they cannot win the closer_b pick.
    pads = [
        _anchor(f"north-pad-{i}", 0.0014 + 0.0002 * i, 0.00005 * (i - 1), tier=5, beats=6)
        for i in range(4)
    ]
    anchors = [tie_hi, tie_lo, *pads]  # tie_hi before tie_lo on purpose
    snap = _snap(anchors)

    inp = TourInput(start=PDV, end=end, duration_min=CLOSER_DURATION, city_slug="paris")
    _assert_not_red(inp, snap)

    for a in (tie_hi, tie_lo):
        assert _in_budget(a)
        assert in_wedge(PDV[0], PDV[1], end[0], end[1], a.lat, a.lng)
    for a in pads:
        assert _in_budget(a)
        assert not in_wedge(PDV[0], PDV[1], end[0], end[1], a.lat, a.lng)
    # Identical scores (the precondition that makes id the tie-break).
    assert _score(tie_lo, snap) == _score(tie_hi, snap)
    # The tied pair outscores neither relevant: both are in-wedge so the pool
    # is exactly {tie_hi, tie_lo}; the winner is the lower id.
    with pytest.raises(TourabilityRefusedError) as excinfo:
        select_route(inp, snap)
    cb = _closer_b(excinfo.value)

    assert cb is not None
    assert cb.poi_id == "aaa-tie"  # ascending id wins the tie


def test_bearing_and_wedge_helpers_are_self_consistent():
    """The wedge membership test agrees with the bearing angular gap at the
    ±45° boundary — a real geometric check on the helpers themselves."""
    a_lat, a_lng = PDV
    # Due east axis.
    axis_lat, axis_lng = PDV[0], PDV[1] + 0.01
    axis_b = bearing(a_lat, a_lng, axis_lat, axis_lng)
    # A point due-east is dead-centre in the wedge.
    assert in_wedge(a_lat, a_lng, axis_lat, axis_lng, PDV[0], PDV[1] + 0.005)
    # A point due-north is ~90° off the east axis → outside a 45° half-wedge.
    north_b = bearing(a_lat, a_lng, PDV[0] + 0.01, PDV[1])
    gap = abs((axis_b - north_b + 180) % 360 - 180)
    assert gap > 45.0
    assert not in_wedge(a_lat, a_lng, axis_lat, axis_lng, PDV[0] + 0.01, PDV[1])


def test_end_none_unchanged_by_closer_b_logic():
    """Invariance: an open walk (end=None) never enters the fixed-end refusal
    branch, so no closer_b is computed and a Route is returned."""
    snap = _snap(
        [
            *_density_fillers(PDV, duration_min=CLOSER_DURATION),
            _anchor("east-anchor", 0.0006, 0.0012),
        ]
    )
    inp = TourInput(start=PDV, duration_min=CLOSER_DURATION, city_slug="paris")
    assert inp.end is None
    route = select_route(inp, snap)
    assert route is not None


# ---------------------------------------------------------------------------
# Corridor (time-ellipse) reach filter when end is set.
#
# When a fixed end B exists AND the routed A→B leg fits the walk budget (so the
# fixed-end refusal does NOT fire), only anchors whose routed detour A→poi→B still
# fits the budget — t(A,poi)+t(poi,B) ≤ walk_budget_seconds — earn candidacy.
# A POI on the A-B axis is admitted; a far off-axis POI (in REACH, but whose
# two-focus detour overruns the budget) is rejected from the candidate pool,
# so it never appears in the route. The end=None path is LITERALLY unchanged.
#
# All synthetic; every expectation is DERIVED from the live cost / REACH
# functions (default_leg_seconds, walk_budget_seconds, envelope_radius_m,
# haversine_m), never a hardcoded leg time.
# ---------------------------------------------------------------------------

# A fixed end due east of PdV whose routed A→B leg fits the walk budget, so
# the fixed-end check does NOT refuse and selection proceeds to the candidate loop.
CORRIDOR_END = (PDV[0], PDV[1] + 0.0064)
# Duration chosen so (a) the walk budget covers the direct A→B leg (the refusal is
# silent), (b) a near-REACH-edge off-axis detour still overruns it, and (c) the
# anchor cap (duration // 10, here 6) exceeds the 4 density fillers + on-axis
# anchor, so every corridor-admitted candidate is actually selected — letting
# the route's POIs stand in for the post-corridor candidate pool.
CORRIDOR_DURATION = 60

#: How tightly the background cluster is packed for the corridor trio.
#:
#: These three tests share one corpus so that the end=None case is a true
#: invariance control — same POIs, same duration, only ``end`` differs. That
#: corpus has to do two jobs at once: carry enough stops to fill the hour the
#: certification band demands, and still leave enough of the walk budget for the
#: route to REACH the off-axis anchor when nothing is filtering it out. The
#: default spiral (~178 m at 60 min) spends most of the 1440 s budget on the
#: cluster alone, which starves the reach; packing it into 100 m keeps the stop
#: count and gives the budget back. Measured over a 3x3 sweep of anchor count and
#: radius, 100 m is the middle of the cell block where all three tests hold.
_CORRIDOR_FILLER_RADIUS_M = 100.0


def _corridor_background() -> list[POI]:
    """The shared background anchors for all three corridor tests.

    Deliberately tier-3 with the minimum anchor beat count: still anchor
    candidates as far as the density gate is concerned, and still carrying a
    full stop's worth of audio, but scored below each test's own tier-5 subject
    so the greedy cannot fill the hour on cheap background alone and leave the
    POI under test on the shelf.
    """
    return _density_fillers(
        PDV,
        duration_min=CORRIDOR_DURATION,
        radius_m=_CORRIDOR_FILLER_RADIUS_M,
        tier=3,
        beat_count=3,
    )


def _off_axis_anchor() -> POI:
    """The shared anchor the fixed-end corridor rejects but end=None keeps.

    Due NORTH of the start, so the detour A→off→B away from the due-east axis
    overruns the walk budget, while the anchor itself stays comfortably inside
    the one-way REACH envelope. Both properties are re-asserted live in every
    test that uses it, so moving it can never silently void the subject.
    """
    return _anchor("off-axis", 0.0045, 0.0, tier=5, beats=40)


def _corridor_admits(poi: POI, end: tuple[float, float], duration_min: int) -> bool:
    """Live two-focus corridor predicate: does A→poi→B fit the walk budget?"""
    t_a = default_leg_seconds(PDV[0], PDV[1], poi.lat, poi.lng)
    t_b = default_leg_seconds(poi.lat, poi.lng, end[0], end[1])
    return t_a + t_b <= walk_budget_seconds(duration_min)


def _in_reach(poi: POI, duration_min: int) -> bool:
    """Live: does the POI sit inside the one-way REACH envelope of the start?"""
    radius_m = envelope_radius_m(duration_min, round_trip=False)
    return haversine_m(PDV[0], PDV[1], poi.lat, poi.lng) <= radius_m


def test_corridor_admits_on_axis_poi_for_fixed_end():
    """A strong anchor sitting on the A-B axis satisfies the two-focus corridor
    (t(A,poi)+t(poi,B) ≤ budget) and IS selected into the route."""
    end = CORRIDOR_END
    duration = CORRIDOR_DURATION
    # Midway between A and B on the due-east axis → its detour ≈ the direct
    # A→B leg, well inside the budget. Rich tier-5 anchor so the greedy picks it.
    on_axis = _anchor("on-axis", 0.0, 0.0032, tier=5, beats=20)
    snap = _snap([*_corridor_background(), on_axis])

    inp = TourInput(start=PDV, end=end, duration_min=duration, city_slug="paris")
    _assert_not_red(inp, snap)
    # Preconditions (live): the A→B leg fits the budget (no fixed-end refusal) and the
    # on-axis anchor clears the corridor predicate.
    assert default_leg_seconds(PDV[0], PDV[1], end[0], end[1]) <= walk_budget_seconds(duration)
    assert _corridor_admits(on_axis, end, duration)

    route = select_route(inp, snap)
    assert route is not None
    assert on_axis.id in {p.id for p in route.pois}


def test_corridor_rejects_far_off_axis_poi_for_fixed_end():
    """A far off-axis anchor that is inside REACH and rich enough to outscore
    everything is STILL excluded from the route, because its two-focus detour
    t(A,poi)+t(poi,B) overruns the walk budget — the corridor gate drops it."""
    end = CORRIDOR_END  # axis points due east
    duration = CORRIDOR_DURATION
    # Off-axis: due NORTH near the REACH-radius edge. t(A,off) alone nearly
    # exhausts the budget, so the A→off→B detour blows well past it — yet it is
    # the single richest anchor, so its absence can only be the corridor gate.
    off_axis = _off_axis_anchor()
    # On-axis control: a rich anchor on the segment that the corridor admits, so
    # the route is non-empty and we are comparing pool membership, not emptiness.
    on_axis = _anchor("on-axis", 0.0, 0.0032, tier=5, beats=20)
    snap = _snap([*_corridor_background(), off_axis, on_axis])

    inp = TourInput(start=PDV, end=end, duration_min=duration, city_slug="paris")
    _assert_not_red(inp, snap)

    # Preconditions, all live-derived:
    # 1. No fixed-end refusal (A→B fits budget).
    assert default_leg_seconds(PDV[0], PDV[1], end[0], end[1]) <= walk_budget_seconds(duration)
    # 2. off_axis is genuinely INSIDE REACH (so REACH is not what excludes it)…
    assert _in_reach(off_axis, duration)
    # 3. …but its two-focus detour overruns the budget → corridor rejects it.
    assert not _corridor_admits(off_axis, end, duration)
    # 4. on_axis passes the corridor (route stays non-empty).
    assert _corridor_admits(on_axis, end, duration)
    # 5. off_axis is the richest anchor — absent the corridor it WOULD be picked,
    #    so its absence isolates the corridor gate (not score / greedy budget).
    assert _score(off_axis, snap) > _score(on_axis, snap)

    route = select_route(inp, snap)
    assert route is not None
    pool_ids = {p.id for p in route.pois}
    assert off_axis.id not in pool_ids  # corridor-rejected, never selected
    assert on_axis.id in pool_ids       # corridor-admitted, selected


def test_corridor_filter_does_not_touch_end_none_pool():
    """Invariance: with end=None the corridor gate is skipped, so a
    far off-axis anchor that the fixed-end corridor would reject is STILL a
    candidate and gets selected. Same corpus, same duration — only end differs."""
    duration = CORRIDOR_DURATION
    # The very anchor the fixed-end corridor rejects (off-axis, in REACH).
    off_axis = _off_axis_anchor()
    snap = _snap([*_corridor_background(), off_axis])

    # Sanity: under a fixed end this anchor IS corridor-rejected…
    assert _in_reach(off_axis, duration)
    assert not _corridor_admits(off_axis, CORRIDOR_END, duration)

    # …but with end=None the gate never runs, so it remains in the pool.
    open_inp = TourInput(start=PDV, duration_min=duration, city_slug="paris")
    assert open_inp.end is None
    open_route = select_route(open_inp, snap)
    assert open_route is not None
    assert off_axis.id in {p.id for p in open_route.pois}

