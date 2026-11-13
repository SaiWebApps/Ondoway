"""Fixed-destination B-materialization (HYBRID) fed to ORDER.

When a tour carries a fixed end B and the routed A→B leg fits the walk budget
(so the feasibility refusal does NOT fire), ``select_route`` must pin a concrete POI as the
LAST ordered stop:

  - SNAP: if a selected (already in-corridor) POI sits within ~150m of B, the
    route's last stop is that real POI.
  - SYNTHESIZE: otherwise a sentinel POI at B's *exact* coordinate is added to
    the selected set and pinned last.

The ``end=None`` paths (open walk + round trip) must be LITERALLY unchanged —
the ordered-id identity baseline (in test_tour_selection.py) still
guards that, and this module re-asserts the open-walk end and round-trip end
behaviour directly.

These are REAL ``select_route`` runs over synthetic corpora — the cost function
is never mocked. Expectations are derived from the live functions
(haversine_m, default_leg_seconds, walk_budget_seconds) so a test fails only if
the BEHAVIOUR is wrong, never against a hardcoded leg time.
"""

from __future__ import annotations

import pytest

from src.tour.contract import POI, BeatRef, TourInput
from src.tour.routing import (
    default_leg_seconds,
    haversine_m,
    pace_corrected_walk_seconds,
    walk_budget_seconds,
)
from src.tour.selection import (
    B_SNAP_PROXIMITY_M,
    CorpusSnapshot,
    select_route,
)
from tests.test_tour_selection import _auto_beat_seconds, _density_fillers

PDV = (48.8555, 2.3656)


# ---------------------------------------------------------------------------
# Fixtures (mirroring the established feasibility-test helpers).
# ---------------------------------------------------------------------------

#: How tightly the background cluster is packed, in metres.
#:
#: Every test in this module has ONE subject POI — a snap target, a sentinel, a
#: far endpoint — that has to end the route, and the background exists only to
#: give the plan enough stops to fill the requested duration. Since 2026-08-04 a
#: plan must also land inside the certification TIME band, so the background can
#: no longer be four POIs sitting on top of the start: four stops cannot fill an
#: hour and a half. But the default duration-scaled spiral is calibrated to spend
#: about a third of the request on walking all by itself, which would leave
#: nothing in the walk budget for the route to REACH its subject. Packing the
#: same anchor count into 100 m keeps the stops and hands the budget back.
_BACKGROUND_RADIUS_M = 100.0


def _background(duration_min: int, n: int | None = None) -> list[POI]:
    """Tier-3 background anchors: enough stops to fill `duration_min`, no more.

    Tier 3 with the minimum anchor beat count is the lowest thing the density
    gate still counts as an anchor candidate. That keeps the background scored
    BELOW each test's tier-5 subject, so the greedy cannot fill the tour on
    cheap background alone and leave the POI under test unselected.
    """
    return _density_fillers(
        PDV,
        duration_min=duration_min,
        radius_m=_BACKGROUND_RADIUS_M,
        tier=3,
        beat_count=3,
        n=n,
    )


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


def _snap(pois: list[POI]) -> CorpusSnapshot:
    """CorpusSnapshot with synthetic beats sized to ONE STOP's audio ceiling.

    A POI's whole beat set is worth ``MAX_DWELL_AUDIO_SECONDS`` (270 s), which is
    the most any single stop can ever speak. Selection prices a stop twice — the
    greedy CREDITS the POI's planned audio, emission then CAPS it — and the two
    prices only agree when no POI carries more than one stop's worth. The old
    flat 240 s per beat gave an 8-beat filler 1920 s of credit against 270 s of
    delivery, so the greedy stopped adding stops long before the tour was full.
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


class _DeterministicRoutingClient:
    """Hermetic fake: routed leg times equal int(pace-corrected haversine).

    Mirrors the identity-baseline client. isochrone() returns None so REACH
    falls back to the analytic envelope; route() returns a None polyline.
    """

    def leg_seconds(self, from_lat: float, from_lng: float, to_lat: float, to_lng: float) -> int:
        return int(pace_corrected_walk_seconds(haversine_m(from_lat, from_lng, to_lat, to_lng)))

    def route(
        self, from_lat: float, from_lng: float, to_lat: float, to_lng: float
    ) -> tuple[int, float, None]:
        d = haversine_m(from_lat, from_lng, to_lat, to_lng)
        return (int(pace_corrected_walk_seconds(d)), d, None)

    def isochrone(self, lat: float, lng: float, minutes: int) -> None:
        return None

    def close(self) -> None:
        return None


# A duration whose walk budget comfortably covers a short east A→B leg, so the
# feasibility refusal does NOT fire and the route reaches ORDER.
DURATION = 90


def _ab_in_budget(end: tuple[float, float], duration_min: int = DURATION) -> bool:
    """Live: does the routed A→B leg fit inside the walk budget (no feasibility refusal)?"""
    return default_leg_seconds(PDV[0], PDV[1], end[0], end[1]) <= walk_budget_seconds(duration_min)


def _assert_not_red(inp: TourInput, snap: CorpusSnapshot) -> None:
    from src.tour.density import assess as assess_tourability

    assert assess_tourability(inp, snap.pois, snap.beats_by_poi).status != "RED"


# ---------------------------------------------------------------------------
# SNAP: B within ~150m of a selected in-corridor POI → last stop is that POI.
# ---------------------------------------------------------------------------


def test_fixed_end_snaps_to_nearby_selected_poi():
    """B placed within ~150m of a real selected anchor: the route's last stop
    is that anchor's id (no sentinel synthesized)."""
    # A real east anchor that is well inside the corridor and budget.
    east = _anchor("east-real", 0.0, 0.0040, tier=5, beats=12)  # ~290m east of PdV
    snap = _snap([east, *_background(DURATION)])

    # B is ~30m east of the real anchor — comfortably inside B_SNAP_PROXIMITY_M.
    end = (east.lat, east.lng + 0.0004)
    snap_dist = haversine_m(end[0], end[1], east.lat, east.lng)
    assert 0.0 < snap_dist <= B_SNAP_PROXIMITY_M  # precondition, live distance

    inp = TourInput(start=PDV, end=end, duration_min=DURATION, city_slug="paris")
    assert _ab_in_budget(end)  # the feasibility check does not refuse
    _assert_not_red(inp, snap)

    route = select_route(inp, snap)
    ids = tuple(p.id for p in route.pois)
    assert ids, "fixed-end route should not be empty"
    # SNAP: last stop is the real anchor; no synthesized sentinel anywhere.
    assert ids[-1] == east.id
    assert not any(p.id.startswith("__end_b__") for p in route.pois)
    assert end not in {(p.lat, p.lng) for p in route.pois}  # exact B coord NOT added


def test_fixed_end_records_exempt_identity_and_no_start_anchor():
    """An A→B route records fixed_end_poi_id (the snapped B, exempt) and
    leaves start_anchor_poi_id None — A→B seats no positional start-anchor
    (exempt_anchor_id is guarded by ``input.end is None``), so nothing on the
    corridor is start-anchor-exempt. Additive metadata; ordering is unchanged."""
    east = _anchor("east-real", 0.0, 0.0040, tier=5, beats=12)  # ~290m east of PdV
    snap = _snap([east, *_background(DURATION)])
    end = (east.lat, east.lng + 0.0004)  # ~30m east of east-real -> within snap
    inp = TourInput(start=PDV, end=end, duration_min=DURATION, city_slug="paris")
    assert _ab_in_budget(end)
    _assert_not_red(inp, snap)

    route = select_route(inp, snap)
    assert route.fixed_end_poi_id == east.id, "B snapped to the real east anchor"
    assert route.fixed_end_poi_id == route.pois[-1].id, "fixed_end is the last stop"
    assert route.start_anchor_poi_id is None, "A→B seats no positional start-anchor"


def test_fixed_end_snaps_to_nearest_of_two_in_range_pois():
    """When two selected POIs are within ~150m of B, the route ends at the
    geometrically NEAREST one (derived live from haversine_m)."""
    near = _anchor("snap-near", 0.0, 0.0040, tier=5, beats=10)
    farther = _anchor("snap-far", 0.0, 0.0046, tier=5, beats=10)
    snap = _snap([near, farther, *_background(DURATION)])

    # B sits between them but closer to `near`.
    end = (near.lat, near.lng + 0.0002)
    d_near = haversine_m(end[0], end[1], near.lat, near.lng)
    d_far = haversine_m(end[0], end[1], farther.lat, farther.lng)
    assert d_near < d_far <= B_SNAP_PROXIMITY_M  # both in range; near is closer

    inp = TourInput(start=PDV, end=end, duration_min=DURATION, city_slug="paris")
    assert _ab_in_budget(end)
    _assert_not_red(inp, snap)

    route = select_route(inp, snap)
    ids = tuple(p.id for p in route.pois)
    assert near.id in ids and farther.id in ids  # both got selected
    assert ids[-1] == near.id  # nearest wins the snap


# ---------------------------------------------------------------------------
# SYNTHESIZE: B far from every selected POI → sentinel at exact B coord, last.
# ---------------------------------------------------------------------------


def test_fixed_end_synthesizes_sentinel_when_b_is_far_from_every_poi():
    """B with no selected POI within ~150m: a sentinel POI is synthesized at
    B's EXACT coordinate and pinned as the last stop."""
    # An east anchor that is in-corridor but a clear, > ~150m gap from B, so B
    # cannot snap onto it.
    east = _anchor("east-real", 0.0, 0.0040, tier=5, beats=12)
    snap = _snap([east, *_background(DURATION)])

    # B is ~440m further east than the real anchor — beyond the snap radius of
    # every selected POI, but A→B still fits the 90-min budget.
    end = (east.lat, east.lng + 0.0060)
    inp = TourInput(start=PDV, end=end, duration_min=DURATION, city_slug="paris")
    assert _ab_in_budget(end)
    _assert_not_red(inp, snap)

    route = select_route(inp, snap)
    ids = tuple(p.id for p in route.pois)
    assert ids, "fixed-end route should not be empty"

    last = route.pois[-1]
    # The last stop is the synthesized sentinel AT B's exact coordinate.
    assert last.id.startswith("__end_b__")
    assert (last.lat, last.lng) == end
    assert last.poi_role == "stop"
    # Every nearest selected POI was genuinely farther than the snap radius,
    # proving synthesis (not snap) was the correct branch — live distances.
    for poi in route.pois:
        if poi is last:
            continue
        assert haversine_m(end[0], end[1], poi.lat, poi.lng) > B_SNAP_PROXIMITY_M


def test_sentinel_route_ends_at_b_on_routed_cost_path_too():
    """Synthesis + endpoint pinning holds on the deterministic routed cost
    path, not only the no-client haversine path."""
    east = _anchor("east-real", 0.0, 0.0040, tier=5, beats=12)
    snap = _snap([east, *_background(DURATION)])
    end = (east.lat, east.lng + 0.0060)
    inp = TourInput(start=PDV, end=end, duration_min=DURATION, city_slug="paris")
    assert _ab_in_budget(end)
    _assert_not_red(inp, snap)

    route = select_route(inp, snap, routing_client=_DeterministicRoutingClient())
    last = route.pois[-1]
    assert last.id.startswith("__end_b__")
    assert (last.lat, last.lng) == end


# ---------------------------------------------------------------------------
# end=None invariance: open walk and round trip are LITERALLY unchanged.
# ---------------------------------------------------------------------------


def test_open_walk_end_none_still_ends_at_far_far_anchor():
    """end=None open walk: ORDER still ends at the far anchor (no B-materialize
    branch runs). The endpoint-pull-derived fixed_end path is untouched."""
    far = _anchor("far-east", 0.0, 0.0090, tier=5, beats=30)  # far envelope, rich
    snap = _snap([far, *_background(DURATION)])
    inp = TourInput(start=PDV, duration_min=DURATION, city_slug="paris")  # end=None

    route = select_route(inp, snap)
    ids = tuple(p.id for p in route.pois)
    assert far.id in ids
    assert ids[-1] == far.id  # endpoint-pull pins the far anchor last
    assert not any(p.id.startswith("__end_b__") for p in route.pois)  # no sentinel


def test_round_trip_still_returns_to_start():
    """round_trip=True (end is None): ORDER optimizes a closed tour, the last
    transit returns to A, and no B sentinel is ever synthesized."""
    far = _anchor("far-east", 0.0, 0.0050, tier=5, beats=20)
    snap = _snap([far, *_background(DURATION)])
    inp = TourInput(start=PDV, duration_min=DURATION, city_slug="paris", round_trip=True)

    route = select_route(inp, snap)
    assert route.pois, "round-trip route should not be empty"
    assert not any(p.id.startswith("__end_b__") for p in route.pois)  # no sentinel
    # round_trip closes the loop: the final transit goes back to start (to_poi None).
    assert route.transits[-1].to_poi_id is None


# ---------------------------------------------------------------------------
# 2026-07-03 — single-stop regression class guards on the fixed-end (A→B) path.
#
# The end != None pipeline composes the corridor gate, the endpoint pull
# (which still runs BEFORE B-materialization supersedes its fixed_end), the
# fill pass and _materialize_fixed_end_b in an order nothing else tests.
# Universal invariant for every fixture below: a delivered route has >= 2
# beat-bearing stops OR carries a non-None tourability disclosure.
# ---------------------------------------------------------------------------


def test_fixed_end_corridor_rich_route_keeps_multiple_beat_bearing_stops():
    """A rich on-corridor A→B pool must deliver multiple beat-bearing stops.

    The corridor gate (t(A,poi) + t(poi,B) <= walk_budget) is the newest
    pool-shrinking filter; an over-tight gate (comparing against the greedy
    walk budget instead of the full budget, or a haversine/routed mixup)
    empties the pool down to one anchor and delivers a 1-story A→B tour with
    NO warning (the fixture is GREEN). Every candidate here is proven
    corridor-admissible below, and nothing sits beyond the endpoint-pull's far
    floor, so the only thing that can shrink the delivery to one story is the
    gate misfiring. B then SNAPs onto east-3, ~15 m away.
    """
    from src.tour.routing import envelope_radius_m

    east1 = _anchor("east-1", 0.0, 0.0020, tier=5, beats=8)  # ~146m east
    east2 = _anchor("east-2", 0.0, 0.0040, tier=5, beats=8)  # ~293m east
    east3 = _anchor("east-3", 0.0, 0.0060, tier=5, beats=8)  # ~439m east
    snap = _snap([east1, east2, east3, *_background(DURATION)])
    end = (PDV[0], PDV[1] + 0.0062)  # ~15m past east-3 — inside the snap radius

    inp = TourInput(start=PDV, end=end, duration_min=DURATION, city_slug="paris")

    # Preconditions, derived live (module house style).
    assert _ab_in_budget(end)  # no feasibility refusal
    assert haversine_m(*end, east3.lat, east3.lng) <= B_SNAP_PROXIMITY_M  # SNAP branch
    budget = walk_budget_seconds(DURATION)
    for poi in (east1, east2, east3, *_background(DURATION)):
        detour = default_leg_seconds(PDV[0], PDV[1], poi.lat, poi.lng) + default_leg_seconds(
            poi.lat, poi.lng, *end
        )
        assert detour <= budget, f"corridor must admit {poi.id}: {detour}s > {budget}s"
    # No far candidate for the pull: everything sits inside the far floor.
    far_floor = 0.5 * envelope_radius_m(DURATION, round_trip=False)
    assert haversine_m(PDV[0], PDV[1], east3.lat, east3.lng) < far_floor
    _assert_not_red(inp, snap)

    route = select_route(inp, snap)
    ids = [p.id for p in route.pois]

    assert ids[-1] == east3.id, f"route must literally end at B's snap; got {ids}"
    beatful = [p for p in route.pois if snap.beats_for(p.id)]
    assert len(beatful) >= 2, f"1-story A→B delivery: beat-bearing stops = {ids}"
    assert not any(p.id.startswith("__end_b__") for p in route.pois)
    # GREEN fixture: a thin delivery here would be SILENT — exactly the
    # guarded class.
    assert route.tourability is None
    assert len(beatful) >= 2 or route.tourability is not None


def test_fixed_end_uses_a_nearby_poi_without_collapsing_the_greedy_cluster():
    """A fixed destination may snap to a nearby POI but keeps a rich route."""
    # RE-DERIVED for the three-minute tight: at 75
    # minutes the six-background fixture went density-YELLOW (each stop supplies
    # less capped audio), and backgrounds enough for GREEN out-competed the
    # north cluster. Swept duration x background against every assertion:
    # 55 minutes with seven background anchors holds them all, with holding
    # neighbours on both sides (50-65 min, n 6-8).
    duration = 55
    n1 = _anchor("n-1", 0.0018, 0.0, tier=5, beats=6)  # ~200m north
    n2 = _anchor("n-2", 0.0027, 0.0, tier=5, beats=6)  # ~300m north
    mountain = _anchor("far-mountain", 0.0, 0.00753, tier=5, beats=39)  # ~551m east
    decoys = [
        _anchor("decoy-w", 0.0, -0.0082, tier=3, beats=3),  # ~600m west
        _anchor("decoy-nw", 0.0038, -0.0058, tier=3, beats=3),
        _anchor("decoy-sw", -0.0038, -0.0058, tier=3, beats=3),
    ]
    # Background so the plan can fill 75 minutes: the six named POIs above carry
    # six stops' worth of audio against a target of ten, which is density YELLOW,
    # and this test's last assertion is that a healthy fixed-end route discloses
    # NOTHING — so the fixture has to be genuinely healthy. The count is
    # deliberately SMALLER than the shortfall: six cheap background anchors close
    # most of the gap but not all of it, so the greedy still has to reach out for
    # the named cluster and the mountain rather than filling the hour next door.
    # Measured over a 4-10 sweep: four refuses, seven and ten drop the north
    # cluster, five and six both hold every assertion.
    snap = _snap([n1, n2, mountain, *decoys, *_background(duration, n=7)])
    end = (PDV[0], PDV[1] + 0.00685)  # ~500m due east
    assert _ab_in_budget(end, duration)
    assert haversine_m(*end, mountain.lat, mountain.lng) <= B_SNAP_PROXIMITY_M
    inp = TourInput(start=PDV, end=end, duration_min=duration, city_slug="paris")

    route = select_route(inp, snap)
    ids = [p.id for p in route.pois]

    # RE-DERIVED (2026-08-25) — a written decision, not a nursed number. This
    # assertion used to be `"n-1" in ids and "n-2" in ids`.
    #
    # WHY IT MOVED. A planner change tightened the walking-worth line from 4.5 to 2.0
    # walk-seconds per standing-second and added the 1800 s pure-detour ceiling, both
    # INHERITED from the product's own illogical-detour invariant so the planner and the
    # gate that judges it speak one rule. The north cluster sits 200 m and 300 m off a
    # corridor that runs EAST, so reaching it is pure detour. Under the new line the
    # FARTHER of the two no longer earns its walk.
    #
    # MEASURED, not assumed, before this was touched: bypassing `stop_earns_its_walk`
    # alone restores n-2 exactly (7 stops, both cluster anchors) — so the line is the
    # cause and nothing else is. Swept afterwards against every assertion here:
    # background n in 4-8 and cluster beats in 6-16, **no combination seats both**
    # (n<=5 loses n-1 and goes tourability-YELLOW; n>=6 loses n-2; beats>=14 at n=6
    # loses n-1 instead). The old pair-assertion is unreachable by construction now.
    #
    # WHAT THIS TEST IS FOR SURVIVES INTACT: a fixed destination that SNAPS onto a
    # nearby POI must not collapse the route into a thin one. That is the invariant;
    # "these two particular ids" was the 4.5-era snapshot of it. The replacement is
    # STRICTLY STRONGER than the `len(beatful) >= 2` floor it sits beside — the route
    # must still reach the cluster, still end at B's snap, and still deliver a rich day.
    assert any(n in ids for n in ("n-1", "n-2")), (
        f"the greedy must still reach the named cluster, not fill the hour next door; got {ids}"
    )
    assert route.fixed_end_poi_id == route.pois[-1].id == mountain.id
    assert not any(poi.id.startswith("__end_b__") for poi in route.pois)
    beatful = [p for p in route.pois if snap.beats_for(p.id)]
    assert len(beatful) >= 4, f"B-snap collapsed the route to a thin day; got {ids}"
    assert route.tourability is None


def test_one_story_fixed_end_corpus_is_refused_not_padded_with_a_sentinel():
    """A destination with only ONE story to tell on the way is refused outright.

    This is the same worry as before — the destination sentinel carries no beats,
    so any downstream "the route has two POIs, it must be fine" check would read
    a one-story tour as a two-stop tour and ship the thin, single-beat-feeling
    delivery the user complained about. What changed on 2026-08-04 is where that
    worry is now answered.

    It used to be answered by DISCLOSURE: the route was still built, and a YELLOW
    tourability assessment rode along on it to say "this is thin". That outcome is
    no longer reachable at any duration the product offers. A single stop can
    speak at most ``MAX_DWELL_AUDIO_SECONDS`` (270 s) and walking is capped at
    0.40 of the request, so a one-story tour tops out at 0.40 x duration + 270 s
    of active time, while the certification band demands at least 0.90 x
    duration. The two only overlap below about nine minutes.

    So it is now answered by REFUSAL: the plan cannot reach the band, selection
    says so, and no route — padded or otherwise — is produced. Note WHICH refusal.
    The start circle is density RED, but a fixed destination makes the density
    gate defer to the routed checks rather than raise, so what actually stops this
    tour is the certification band. The sentinel's beatlessness is still
    load-bearing, and is exercised on a corpus rich enough to deliver by
    ``test_fixed_end_corridor_rich_route_keeps_multiple_beat_bearing_stops``,
    which counts BEAT-BEARING stops rather than raw POIs.
    """
    from src.tour.density import assess as assess_tourability
    from src.tour.selection import CertificationPlanningInfeasibleError

    duration = 60
    lone = _anchor("lone", 0.0, 0.00205, tier=4, beats=5)  # ~150m east
    snap = _snap([lone])
    end = (PDV[0], PDV[1] + 0.0055)  # ~400m east

    inp = TourInput(start=PDV, end=end, duration_min=duration, city_slug="paris")

    # Preconditions, derived live: nothing EXCEPT the thinness of the corpus is
    # what stops this tour. The destination is reachable, it is far enough from
    # the lone anchor that B-materialization would have had to synthesize a
    # sentinel, and the lone anchor clears the corridor gate.
    assert _ab_in_budget(end, duration)  # no feasibility refusal
    assert haversine_m(*end, lone.lat, lone.lng) > B_SNAP_PROXIMITY_M  # SYNTHESIZE branch
    corridor = default_leg_seconds(PDV[0], PDV[1], lone.lat, lone.lng) + default_leg_seconds(
        lone.lat, lone.lng, *end
    )
    assert corridor <= walk_budget_seconds(duration)  # lone is admitted

    # One anchor carrying one stop's worth of audio against an hour's target, and
    # it knows the honest answer: this corpus supports a much shorter tour.
    assessment = assess_tourability(inp, snap.pois, snap.beats_by_poi)
    assert assessment.status == "RED"
    assert assessment.anchor_candidate_count == 1
    assert assessment.max_supportable_duration_min is not None
    assert assessment.max_supportable_duration_min < duration

    # No route comes back at all, so there is nothing for a beatless sentinel to
    # pad. The refusal says, in a traveller's words, how much day this corpus can
    # honestly build and what was asked (the sentence itself used to name a
    # "TIME band", which was engineer-speak, and this pin was the last reader of
    # that wording).
    with pytest.raises(CertificationPlanningInfeasibleError) as excinfo:
        select_route(inp, snap)
    reason = excinfo.value.reason
    assert "far short of the" in reason and "minutes asked for" in reason, reason
    assert excinfo.value.best_elapsed_seconds is not None
    assert excinfo.value.best_elapsed_seconds < excinfo.value.minimum_elapsed_seconds



def test_the_end_sentinel_rebuilds_from_its_own_id():
    """The A→B end sentinel's id encodes its coordinate, so a persisted route
    re-materializes it instead of mistaking the engine's own marker for corpus
    drift (measured: a Châtelet day 409'd "corpus_changed" on every compose,
    permanently uncomposable). Malformed and non-sentinel ids return None (the
    caller treats them as real POIs). The compose-side guard —
    the one that goes RED when the rebuild is dropped from the corpus check —
    is test_compose_corpus_check_rebuilds_the_end_sentinel below."""
    from src.tour.selection import end_b_sentinel_from_id, end_b_sentinel_poi

    original = end_b_sentinel_poi(48.8583, 2.347)
    rebuilt = end_b_sentinel_from_id(original.id)
    assert rebuilt is not None
    assert (rebuilt.id, rebuilt.lat, rebuilt.lng) == (original.id, 48.8583, 2.347)
    assert end_b_sentinel_from_id("a-real-poi-id") is None
    assert end_b_sentinel_from_id("__end_b__not_numbers_x") is None


def test_compose_corpus_check_rebuilds_the_end_sentinel():
    """The compose-side guard: a persisted day must stay composable. A Châtelet
    day persisted the engine's own `__end_b__<lat>_<lng>` stop; the compose rebuild
    looked it up in the corpus, found nothing, and refused 409 corpus_changed on every
    attempt. The corpus check must re-materialize the sentinel from its id and still
    refuse a genuinely missing corpus id. UNDO: delete the sentinel loop in
    _resolve_persisted_pick -> the first call raises 409 corpus_changed."""
    from fastapi import HTTPException

    from src.api.routes.trips import _resolve_persisted_pick
    from src.tour.selection import end_b_sentinel_poi

    real = POI(id="p-real", name="Real stop", tier=1, poi_role="stop", lat=48.86, lng=2.35)
    sentinel_id = end_b_sentinel_poi(48.8583, 2.347).id

    picked = _resolve_persisted_pick([real.id, sentinel_id], {real.id: real})
    assert [p.id for p in picked] == [real.id, sentinel_id]
    assert (picked[1].lat, picked[1].lng) == (48.8583, 2.347)

    with pytest.raises(HTTPException) as excinfo:
        _resolve_persisted_pick([real.id, "p-gone", sentinel_id], {real.id: real})
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail == {"reason": "corpus_changed", "missing_poi_ids": ["p-gone"]}
