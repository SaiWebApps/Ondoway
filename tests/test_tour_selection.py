"""selection.py: envelope, spine, greedy. Pure-function tests."""

from __future__ import annotations

import math

import pytest

from src.tour.contract import POI, BeatRef, POIBeats, Route, TourInput
from src.tour.routing import (
    envelope_radius_m,
    haversine_m,
    pace_corrected_walk_seconds,
)
from src.tour.selection import (
    AREA_ALIGNMENT_ADJACENT,
    AREA_ALIGNMENT_OTHER,
    AREA_ALIGNMENT_SPINE,
    MAX_DWELL_AUDIO_SECONDS,
    CertificationPlanningInfeasibleError,
    CorpusSnapshot,
    build_poi_beat_plans,
    build_poi_beat_plans_capped,
    build_poi_extra_beats,
    choose_discrete_route,
    pick_spine_area,
    poi_score,
    route_has_container_identity_stop,
    select_route,
)

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
    areas: tuple[str, ...] = (),
    beat_count: int = 5,
) -> POI:
    return POI(
        id=pid,
        name=pid,
        tier=tier,
        poi_role=role,
        lat=lat,
        lng=lng,
        areas=areas,
        beat_count=beat_count,
    )


def _auto_beat_seconds(beat_count: int) -> int:
    """Per-beat voiced seconds so a POI's WHOLE beat set fits one stop's ceiling.

    Selection prices a stop TWICE and the two prices must agree. The greedy
    credits ``planned_capped_audio_seconds(poi, ..., allowance)`` where the C9
    governor allowance is ``target_dwell_seconds(d) // min(3, d // 10)`` — 720 s
    at 60 min, 1440 s at 120 min. Emission then caps every stop at
    ``MAX_DWELL_AUDIO_SECONDS`` (270 s). A fixture POI carrying more than 270 s
    of beats is therefore CREDITED up to 5x what the tour will ever speak, so
    the greedy believes it has filled the audio target after a third of the
    stops, stops adding them, and hands the certification repair a route that
    is far short of the requested duration. Sizing the whole beat set to the
    per-stop ceiling makes credit == delivery, which is the only regime where a
    synthetic fixture behaves like the planner's own arithmetic says it should.

    Kept at >= 1 s so a zero-beat POI stays representable.
    """
    return max(1, MAX_DWELL_AUDIO_SECONDS // max(1, beat_count))


def _snap(
    pois: list[POI],
    *,
    area_types: dict[str, str] | None = None,
    adjacent: dict[str, set[str]] | None = None,
    beats_by_poi: dict[str, list[BeatRef]] | None = None,
    lens_neighbors: dict[str, frozenset[str]] | None = None,
) -> CorpusSnapshot:
    types = {**{"Paris": "city"}, **(area_types or {})}
    adj = {k: frozenset(v) for k, v in (adjacent or {}).items()}
    # The density gate runs at the top of select_route(), so test
    # fixtures need at least one beat per POI to clear it. Auto-inject
    # synthetic beats based on poi.beat_count when no explicit beats are
    # provided — keeps the existing selection-logic tests focused on
    # what they're testing without forcing each test to repeat fake-beat
    # boilerplate.
    raw = beats_by_poi or {}
    beats: dict[str, tuple[BeatRef, ...]] = {}
    for p in pois:
        if p.id in raw:
            beats[p.id] = tuple(raw[p.id])
            continue
        n = max(0, p.beat_count or 0)
        beats[p.id] = tuple(
            BeatRef(
                id=f"{p.id}-b{i}",
                poi_id=p.id,
                est_spoken_seconds=_auto_beat_seconds(n),
                active_status="active",
            )
            for i in range(n)
        )
    return CorpusSnapshot(
        pois=tuple(pois),
        beats_by_poi=beats,
        area_types=types,
        adjacent_areas=adj,
        lens_neighbors=lens_neighbors or {},
    )


PDV = (48.8555, 2.3656)
PONT_NEUF = (48.85675, 2.341033)


def test_container_identity_is_context_not_a_fictional_stop() -> None:
    container = POI(
        id="container",
        name="Ile de la Cite",
        tier=5,
        poi_role="setting",
        lat=48.85,
        lng=2.35,
        areas=("Île de la Cité", "Paris"),
    )
    discrete = container.model_copy(
        update={"id": "square", "name": "Square Jean XXIII"}
    )
    container_route = Route(
        pois=(container,),
        transits=(),
        total_walk_distance_m=0,
        total_walk_seconds=0,
    )
    discrete_route = container_route.model_copy(update={"pois": (discrete,)})

    assert route_has_container_identity_stop(container_route)
    assert not route_has_container_identity_stop(discrete_route)
    assert choose_discrete_route([container_route, discrete_route]) == discrete_route


#: How far the filler cluster reaches, as metres per sqrt(minute).
#:
#: This sets how much WALKING a filled tour contains, which since 2026-08-04 is
#: half of whether a fixture can be planned at all. Active time is walk plus
#: capped audio. The greedy stops once it reaches the audio target (0.60 of the
#: request) and no stop speaks more than MAX_DWELL_AUDIO_SECONDS, so audio alone
#: can never reach the two-sided band's 0.90 floor — the remaining 0.30 has to
#: arrive as walking, while walk_budget_seconds simultaneously caps walking at
#: 0.40. The fixture therefore has to land walking in a WINDOW, not merely keep
#: it small.
#:
#: For n stops spread over a disc of radius R the tour walks about
#: ``0.7 * sqrt(n * pi) * R`` metres at 1.62 s/m, and the greedy seats
#: n ~ duration/7.5. Solving that for the middle of the window (0.34 of the
#: request) gives R ~ 28 * sqrt(duration) — which also stays inside the round-trip
#: envelope (7.4 * duration) at every duration the suite uses.
_FILLER_RADIUS_PER_SQRT_MIN: float = 23.0


def _anchors_for_duration(duration_min: int) -> int:
    """How many filler anchors a tour of `duration_min` needs to be plannable.

    The greedy seats stops until it reaches the dwell target
    (``target_dwell_seconds``) and every stop is capped at
    ``MAX_DWELL_AUDIO_SECONDS``, so it seats about ``target / ceiling`` of them —
    DERIVED from the planner's own constants, because the first version baked
    the arithmetic in as ``duration_min / 7.5`` (0.60 x 60 / 270) and went stale
    the day the ceiling moved to three minutes:
    the 120-minute sweep cells starved and flagged tourability on a fixture
    that claims to be rich by construction. A proportional third on top,
    floored at six, keeps the pool non-empty for the endpoint pull, the fill
    pass and the certification repair, all of which need somewhere left to go
    after the greedy has stopped — and the headroom has to GROW with duration,
    because a flat six spare anchors is most of the pool at 30 minutes and
    almost none of it at 400.
    """
    from src.tour.routing import target_dwell_seconds

    seated = math.ceil(target_dwell_seconds(duration_min) / MAX_DWELL_AUDIO_SECONDS)
    return seated + max(6, math.ceil(seated / 3))


#: Round trips walk the closing leg back to the start, which a one-way tour never
#: pays, so the same anchors over the same disc cost a round trip more walking.
#: Shrinking its cluster keeps both shapes inside the walk allocation without
#: changing anything else about the fixture. Measured over the 28-cell duration
#: sweep: 0.90 leaves the 30-minute round trip 12 s over, 0.85 and 0.80 both pass
#: every cell, so this sits at the top of the verified window.
_ROUND_TRIP_RADIUS_FACTOR: float = 0.85


def _density_fillers(
    start: tuple[float, float],
    *,
    duration_min: int = 60,
    round_trip: bool = False,
    n: int | None = None,
    radius_m: float | None = None,
    tier: int = 5,
    beat_count: int = 5,
    prefix: str = "filler",
) -> list[POI]:
    """Tier-5 anchor candidates on a compact spiral around `start`.

    Two gates have to clear, and they pull in opposite directions.

    The DENSITY gate wants a rich, tight pool: >= 4 anchor candidates
    with fill >= 1.0 and compactness <= 0.6, or the rich-pool escape of fill
    >= 1.5 with >= 6 anchors. The certification TIME band (0.90-1.10 of the
    request since 2026-08-04, two-sided) wants a pool the planner can spend a
    whole hour on. The old fixture — 4 POIs colocated within ~7 m, 8 beats of
    240 s each — satisfied the first and could not satisfy the second: 4
    stops deliver 4 x 270 s of audio and almost no walking, roughly 20 minutes
    of active time against a 54-minute floor, so every test using it refused
    with CertificationPlanningInfeasibleError.

    The fix is more anchors and real spacing, not weaker gates: a phyllotaxis
    spiral whose anchor count comes from _anchors_for_duration and whose reach
    comes from _FILLER_RADIUS_PER_SQRT_MIN (14 anchors inside ~217 m at the
    default 60 minutes). Both scale with the request, so the cluster always sits
    inside that duration's own round-trip envelope while still being spread
    enough that filling the audio target accumulates the walking the band needs.
    Beat seconds come from _auto_beat_seconds, so each anchor's whole beat set
    equals one stop's emission ceiling, which is also what makes the density
    fill ratio scale with the anchor COUNT rather than per-POI beat length.

    Pass ``duration_min`` for anything other than an hour, and ``n`` when a test
    needs an exact anchor count for its own assertion. Pass ``radius_m`` to pack
    the anchors tighter than the default: a test whose subject is a DISTANT POI
    (the endpoint pull, the far-anchor guards) needs the background cluster to
    contribute stops without contributing walking, so that its far POI stays the
    route's only source of walking seconds and cannot be traded away for a
    nearer one of equal audio. Pass ``tier=3`` with
    ``beat_count=3`` for BACKGROUND anchors: still anchor candidates as far as
    density is concerned (ANCHOR_CANDIDATE_TIER_MIN is 3, and its beat-count
    minimum is 3 too) and still able to carry the walking the band needs, but
    scored low enough that a test's own tier-5 subject wins the dwell slot it is
    being tested for.

    Fillers still carry no test-specific signal: they share one area, one tier
    and one beat budget, so spine, envelope and lens assertions read the same
    as they did when the cluster was colocated.
    """
    count = _anchors_for_duration(duration_min) if n is None else n
    outer_m = (
        _FILLER_RADIUS_PER_SQRT_MIN * math.sqrt(duration_min)
        if radius_m is None
        else radius_m
    )
    if round_trip:
        outer_m *= _ROUND_TRIP_RADIUS_FACTOR
    dlat_m = 1.0 / 111_320.0
    dlng_m = 1.0 / (111_320.0 * math.cos(math.radians(start[0])))
    golden_angle = math.pi * (3.0 - math.sqrt(5.0))
    out: list[POI] = []
    for i in range(count):
        # sqrt spacing spreads the anchors EVENLY over the disc rather than
        # bunching them at the rim, so nearest-neighbour hops stay uniform.
        radius = outer_m * math.sqrt((i + 1) / count)
        angle = golden_angle * (i + 1)
        out.append(
            POI(
                id=f"{prefix}-{i}",
                name=f"{prefix}-{i}",
                tier=tier,
                poi_role="stop",
                lat=start[0] + radius * math.cos(angle) * dlat_m,
                lng=start[1] + radius * math.sin(angle) * dlng_m,
                areas=("Le Marais",),
                beat_count=beat_count,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Envelope: respects max_radius
# ---------------------------------------------------------------------------


def test_envelope_excludes_far_pois():
    near = _poi("near", lat=48.8556, lng=2.3658, areas=("Le Marais",))
    # ~3km east of PdV — well outside the 889m one-way envelope.
    far = _poi("far", lat=48.8556, lng=2.4000, areas=("Le Marais",))
    snap = _snap(
        [near, far, *_density_fillers(PDV)], area_types={"Le Marais": "neighborhood"}
    )
    inp = TourInput(start=PDV, duration_min=60, city_slug="paris", round_trip=False)

    route = select_route(inp, snap)
    ids = [p.id for p in route.pois]
    assert "near" in ids
    assert "far" not in ids


def test_envelope_round_trip_uses_half_radius():
    # POI sits at ~500m, between the round-trip envelope (444m) and the one-way
    # one (889m), so it is REACHABLE one-way and UNREACHABLE on a round trip.
    near = _poi("near", lat=48.8556, lng=2.3658, areas=("Le Marais",))
    medium = _poi("medium", lat=48.8590, lng=2.3720, areas=("Le Marais",))
    fillers = _density_fillers(PDV)
    snap = _snap([near, medium, *fillers], area_types={"Le Marais": "neighborhood"})

    # Preconditions, derived from the live envelope so the test pins BEHAVIOUR:
    # the medium POI straddles the two radii, which is the whole point.
    d_medium = haversine_m(*PDV, medium.lat, medium.lng)
    assert envelope_radius_m(60, round_trip=True) < d_medium
    assert d_medium <= envelope_radius_m(60, round_trip=False)

    one_way = select_route(
        TourInput(start=PDV, duration_min=60, city_slug="paris", round_trip=False), snap
    )
    rt = select_route(
        TourInput(start=PDV, duration_min=60, city_slug="paris", round_trip=True), snap
    )

    # The medium-distance POI must drop out of the round-trip route. Asserted on
    # the POI the test is ABOUT: the old blanket subset check over every id was a
    # proxy that only held while the pool was 4 colocated fillers, and it now
    # fails on harmless churn between two independently-planned routes.
    assert "medium" not in {p.id for p in rt.pois}
    # Both shapes still plan, so the exclusion above is the envelope halving and
    # not a refusal that emptied the round trip.
    assert one_way.pois and rt.pois


def test_envelope_excludes_walk_by_only():
    walk_by = _poi("wb", role="walk_by_only", lat=48.8556, lng=2.3658)
    stop = _poi("stop", lat=48.8556, lng=2.3660)
    snap = _snap([walk_by, stop, *_density_fillers(PDV)])
    route = select_route(TourInput(start=PDV, duration_min=60, city_slug="paris"), snap)
    assert "wb" not in [p.id for p in route.pois]


def test_low_tier_no_lens_eligible_as_vignette_not_dwell():
    """The hard tier gate is GONE, but spotlight ALLOCATES scarce
    dwell-minutes, it does not stop everywhere.

    Before the model switch a tier-1 POI was hard-EXCLUDED from the anchor pool
    (ANCHOR_TIERS = {3,4,5}). After the model switch it is ELIGIBLE -- its
    on-path spotlight gravity(1) x 1.0 x 1.0 = 1.0 is a VIGNETTE (a brief
    mention), not silent. But 1.0 is below the calibrated dwell floor
    (BAND_THRESHOLD_SHORT = 3.0 = tier-3 gravity), so it is NOT a dwell stop:
    the tier-5 anchor keeps the dwell slot and the tier-1 vignette does not
    crowd it out. This is what stops low-tier POIs from displacing the golden
    anchors (the pre-calibration bug that dropped the Ile golden 53.2->42.6).
    """
    from src.tour.selection import BAND_VIGNETTE, band_for_spotlight, spotlight

    t1 = _poi("t1", tier=1, lat=48.8556, lng=2.3658)
    t5 = _poi("t5", tier=5, lat=48.8556, lng=2.3660)
    snap = _snap([t1, t5, *_density_fillers(PDV)])
    # Eligible, not excluded: the tier-1 no-lens POI is a vignette, not silent.
    t1_band = band_for_spotlight(spotlight(t1, lenses=None, snapshot=snap), tier=1)
    assert t1_band == BAND_VIGNETTE, f"tier-1 no-lens should be an eligible vignette, got {t1_band}"
    # Allocation: the tier-5 anchor is a dwell stop; the tier-1 vignette is NOT
    # in the dwell route (route.pois are dwell stops).
    route = select_route(TourInput(start=PDV, duration_min=60, city_slug="paris"), snap)
    ids = [p.id for p in route.pois]
    # C9 governor (budget/3 floor): 5 tier-5 anchors compete (t5 + 4 density
    # fillers) for ~3 dwell slots, so a specific tier-5 (t5) may drop to the
    # closer fillers. The invariant that MUST survive: every dwell stop is a
    # tier-5 anchor — the tier-1 no-lens POI never displaces one into a slot.
    assert route.pois, f"anchors must still fill dwell slots, got {ids}"
    assert all(p.tier == 5 for p in route.pois), (
        f"every dwell stop must be a tier-5 anchor (no tier-1 crowd-in); got {ids}"
    )
    assert "t1" not in ids, (
        "a tier-1 no-lens POI is an eligible VIGNETTE, not a dwell stop -- it must "
        f"not consume a dwell slot from the anchors. Got {ids}"
    )


def test_low_tier_off_genre_goes_silent_only_when_both():
    """Silence invariant: silent ONLY when low-gravity AND off-genre.

    With a lens requested, a tier-1 POI whose beats MISS the lens scores
    gravity(1) x LENS_FLOOR(0.25) x proximity(1.0) = 0.25 -> below the vignette
    cut AND tier < BAND_LANDMARK_TIER -> SILENT, so it never enters the dwell
    pool. A tier-5 POI that MISSES the same lens scores 5 x 0.25 = 1.25 ->
    vignette-floored landmark, still eligible (lens alone never silences a
    landmark). A tier-4 POI that HITS the lens is a full dwell stop. So with the
    lens active, the silent tier-1 miss drops out while the lens-hit themed POI
    is selected.
    """
    from src.tour.selection import (
        BAND_SILENT,
        band_for_spotlight,
        spotlight,
    )

    lens = frozenset({"hidden_history"})
    t1_miss = _poi("t1-miss", tier=1, lat=48.8556, lng=2.3661, areas=("Le Marais",))
    t5_miss = _poi("t5-miss", tier=5, lat=48.8557, lng=2.3662, areas=("Le Marais",))
    themed = _poi("themed", tier=4, lat=48.8556, lng=2.3658, areas=("Le Marais",))
    snap = _snap(
        [t1_miss, t5_miss, themed, *_density_fillers(PDV)],
        area_types={"Le Marais": "neighborhood"},
        beats_by_poi={"themed": _lensed_beats("themed", ("hidden_history",))},
        lens_neighbors=_HOP_MAP,
    )
    # Unit-level proof of the invariant at the band classifier.
    assert (
        band_for_spotlight(spotlight(t1_miss, lenses=lens, snapshot=snap), tier=1) == BAND_SILENT
    )
    assert (
        band_for_spotlight(spotlight(t5_miss, lenses=lens, snapshot=snap), tier=5) != BAND_SILENT
    ), "a tier-5 landmark that misses the lens is dimmed to vignette, never silenced"

    inp = TourInput(
        start=PDV, duration_min=60, city_slug="paris", lenses=["hidden_history"], round_trip=True
    )
    ids = [p.id for p in select_route(inp, snap).pois]
    assert "t1-miss" not in ids, (
        f"low-gravity off-genre POI must be silent (excluded), got {ids}"
    )
    assert "themed" in ids, f"the lens-hit POI must be a dwell stop, got {ids}"


def test_lens_dimmed_landmark_stays_a_dwell_stop_not_empty():
    """Landmark dwell-floor: a tier >= BAND_LANDMARK_TIER POI that a LENS dims to
    VIGNETTE must stay a DWELL stop — it discloses "thin for your interest", it
    never vanishes. Without this floor, a lensed thin area where every reachable
    POI is a lens-dimmed landmark (e.g. The Sorbonne under dark_history) empties
    the whole tour and silently drops a major landmark."""
    from src.tour.selection import BAND_VIGNETTE, band_for_spotlight, spotlight

    lens = frozenset({"hidden_history"})
    # Two tier-4 landmarks that MISS the lens; the density fillers (tier-5) also
    # miss it — so EVERY reachable POI is a lens-dimmed landmark and the ONLY way
    # to avoid an empty tour is the dwell-floor.
    lm1 = _poi("landmark-1", tier=4, lat=48.8556, lng=2.3658, areas=("Le Marais",))
    lm2 = _poi("landmark-2", tier=4, lat=48.8560, lng=2.3663, areas=("Le Marais",))
    snap = _snap(
        [lm1, lm2, *_density_fillers(PDV)],
        area_types={"Le Marais": "neighborhood"},
        lens_neighbors=_HOP_MAP,
    )
    # Precondition: the landmark is dimmed to VIGNETTE by the lens (pre-fix it was
    # dropped from the dwell pool at the candidate filter).
    assert band_for_spotlight(spotlight(lm1, lenses=lens, snapshot=snap), tier=4) == BAND_VIGNETTE

    inp = TourInput(
        start=PDV, duration_min=60, city_slug="paris", lenses=["hidden_history"], round_trip=True
    )
    route = select_route(inp, snap)
    ids = [p.id for p in route.pois]
    assert route.pois, "a lensed area of dimmed landmarks must NOT empty the tour"
    assert any(i.startswith("landmark-") or i.startswith("filler-") for i in ids), (
        f"a lens-dimmed landmark must survive as a dwell stop, got {ids}"
    )


# ---------------------------------------------------------------------------
# Spine
# ---------------------------------------------------------------------------


def test_spine_picks_most_populated_neighborhood():
    pois = [
        _poi("a", lat=48.8555, lng=2.3656, areas=("Le Marais", "Paris")),
        _poi("b", lat=48.8550, lng=2.3660, areas=("Le Marais", "Paris")),
        _poi("c", lat=48.8553, lng=2.3658, areas=("Île de la Cité", "Paris")),
    ]
    snap = _snap(
        pois,
        area_types={"Le Marais": "neighborhood", "Île de la Cité": "island"},
    )
    spine = pick_spine_area(*PDV, pois, snap)
    assert spine == "Le Marais"


def test_spine_excludes_city_typed_areas():
    pois = [_poi("a", lat=48.8555, lng=2.3656, areas=("Paris",))]
    snap = _snap(pois)
    spine = pick_spine_area(*PDV, pois, snap)
    assert spine != "Paris"


def test_spine_falls_back_to_closest_when_none_within_radius():
    # POI is 1.5 km north — outside SPINE_RADIUS_M (800).
    far = _poi("far", lat=48.870, lng=2.366, areas=("Distant Hood",))
    snap = _snap([far], area_types={"Distant Hood": "neighborhood"})
    spine = pick_spine_area(*PDV, [far], snap)
    assert spine == "Distant Hood"


def test_spine_returns_none_when_no_candidates():
    snap = _snap([])
    assert pick_spine_area(*PDV, [], snap) is None


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def test_score_increases_with_tier_and_beat_count():
    p_low = _poi("low", tier=3, lat=48.85, lng=2.35, beat_count=2)
    p_high = _poi("high", tier=5, lat=48.85, lng=2.35, beat_count=20)
    snap = _snap([p_low, p_high])
    s_low = poi_score(p_low, None, frozenset(), snap)
    s_high = poi_score(p_high, None, frozenset(), snap)
    assert s_high > s_low


def test_score_area_alignment_spine_beats_other():
    p_in = _poi("in", lat=48.85, lng=2.35, areas=("Le Marais",))
    p_out = _poi("out", lat=48.85, lng=2.35, areas=("Latin Quarter",))
    snap = _snap(
        [p_in, p_out],
        area_types={"Le Marais": "neighborhood", "Latin Quarter": "neighborhood"},
    )
    s_in = poi_score(p_in, "Le Marais", frozenset(), snap)
    s_out = poi_score(p_out, "Le Marais", frozenset(), snap)
    assert s_in > s_out
    assert math.isclose(s_in / s_out, AREA_ALIGNMENT_SPINE / AREA_ALIGNMENT_OTHER, rel_tol=1e-9)


def test_score_area_alignment_adjacent_between_spine_and_other():
    p_adj = _poi("adj", lat=48.85, lng=2.35, areas=("Latin Quarter",))
    snap = _snap(
        [p_adj],
        area_types={"Le Marais": "neighborhood", "Latin Quarter": "neighborhood"},
        adjacent={"Le Marais": {"Latin Quarter"}, "Latin Quarter": {"Le Marais"}},
    )
    s = poi_score(p_adj, "Le Marais", frozenset(), snap)
    p_other = _poi("oth", lat=48.85, lng=2.35, areas=("Far",))
    snap2 = _snap(
        [p_other],
        area_types={"Le Marais": "neighborhood", "Far": "neighborhood"},
    )
    s_other = poi_score(p_other, "Le Marais", frozenset(), snap2)
    assert AREA_ALIGNMENT_OTHER < AREA_ALIGNMENT_ADJACENT < AREA_ALIGNMENT_SPINE
    assert s > s_other


def _lensed_beats(poi_id: str, lenses: tuple[str, ...], *, n: int = 5) -> list[BeatRef]:
    """Beats that all HIT `lenses`, sized like _snap's auto-injected ones.

    Beat length follows _auto_beat_seconds for the same reason: a stop speaks
    at most MAX_DWELL_AUDIO_SECONDS, so a POI carrying more than that is priced
    by the greedy at several times what the tour will ever say.
    """
    return [
        BeatRef(
            id=f"{poi_id}-b{i}",
            poi_id=poi_id,
            est_spoken_seconds=_auto_beat_seconds(n),
            active_status="active",
            lenses=lenses,
        )
        for i in range(n)
    ]


# history -[:IS_PARENT_OF]-> dark_history, symmetric 1-hop map as the loader builds it
_HOP_MAP = {
    "history": frozenset({"dark_history"}),
    "dark_history": frozenset({"history"}),
}


def test_lens_adjacency_direct_hit_is_full_weight():
    """A direct beat-lens hit scores 1.0 — identical to the no-lens run."""
    p = _poi("p", lat=48.85, lng=2.35)
    snap = _snap([p], beats_by_poi={"p": _lensed_beats("p", ("hidden_history",))})
    s_direct = poi_score(p, None, frozenset({"hidden_history"}), snap)
    s_neutral = poi_score(p, None, frozenset(), snap)
    assert s_direct > 0
    assert math.isclose(s_direct, s_neutral, rel_tol=1e-9)


def test_lens_adjacency_one_hop_is_point_six_both_directions():
    """One IS_PARENT_OF hop scores 0.6 — parent requested reaching a
    child-lensed beat, and child requested reaching a parent-lensed beat."""
    p = _poi("p", lat=48.85, lng=2.35)
    child_beats = _snap(
        [p],
        beats_by_poi={"p": _lensed_beats("p", ("dark_history",))},
        lens_neighbors=_HOP_MAP,
    )
    parent_beats = _snap(
        [p],
        beats_by_poi={"p": _lensed_beats("p", ("history",))},
        lens_neighbors=_HOP_MAP,
    )
    for snap, requested in ((child_beats, "history"), (parent_beats, "dark_history")):
        s_hop = poi_score(p, None, frozenset({requested}), snap)
        s_neutral = poi_score(p, None, frozenset(), snap)
        assert math.isclose(s_hop / s_neutral, 0.6, rel_tol=1e-9)


def test_lens_adjacency_ranks_direct_above_one_hop():
    direct = _poi("direct", lat=48.8556, lng=2.3658)
    hop = _poi("hop", lat=48.8557, lng=2.3659)
    snap = _snap(
        [direct, hop],
        beats_by_poi={
            "direct": _lensed_beats("direct", ("history",)),
            "hop": _lensed_beats("hop", ("dark_history",)),
        },
        lens_neighbors=_HOP_MAP,
    )
    s_direct = poi_score(direct, None, frozenset({"history"}), snap)
    s_hop = poi_score(hop, None, frozenset({"history"}), snap)
    assert s_direct > s_hop > 0


def test_lens_miss_dims_but_does_not_zero_poi_score():
    """The model switch made poi_score's lens
    factor the POSITIVE-floored lens_relevance (miss -> LENS_FLOOR = 0.25), NOT
    the legacy hard-filter _lens_adjacency (miss -> 0.0).

    A high-gravity (tier-5) POI that MISSES the requested lens must score a
    strictly positive, DIMMED value -- exactly LENS_FLOOR of its no-lens score,
    not zero. This is what lets a lens-miss landmark still earn a dwell stop
    ("lens promotes, never silences a landmark") rather than vanishing.
    """
    from src.tour.selection import LENS_FLOOR

    missed = _poi("missed", tier=5, lat=48.8557, lng=2.3659, areas=("Le Marais",))
    snap = _snap(
        [missed],
        area_types={"Le Marais": "neighborhood"},
        beats_by_poi={"missed": _lensed_beats("missed", ("film_tv",))},
        lens_neighbors=_HOP_MAP,
    )
    s_miss = poi_score(missed, None, frozenset({"hidden_history"}), snap)
    s_neutral = poi_score(missed, None, frozenset(), snap)
    assert s_miss > 0.0, "a lens miss must DIM, not zero, the score (model switch)"
    assert math.isclose(s_miss / s_neutral, LENS_FLOOR, rel_tol=1e-9), (
        f"a lens-miss score must be LENS_FLOOR of the no-lens score; "
        f"got ratio {s_miss / s_neutral}"
    )


def test_lens_miss_high_gravity_poi_survives_as_vignette():
    """The lens-miss HARD exclusion is GONE -- a tier-5
    off-genre POI is no longer dropped; it is DIMMED to a vignette (5 x
    LENS_FLOOR = 1.25, below the 3.0 dwell floor; landmark-floored, never
    silent) -- a brief mention. Dimmed is NOT a dwell stop: the lens-hit
    anchors take the dwell slots; the off-genre landmark is an eligible
    vignette. (Lens alone never silences a landmark; off-genre dims it.)
    """
    from src.tour.selection import BAND_VIGNETTE, band_for_spotlight, spotlight

    # Lens-hit anchors so the dwell route is viable and dense enough to clear
    # both the density gate and the two-sided time band. Four anchors packed at
    # the start cannot fill an hour, so the pool is the filler spiral, all of it
    # lens-hitting.
    hits = _density_fillers(
        PDV, round_trip=True, n=3 * _anchors_for_duration(60), prefix="hit"
    )
    # The off-genre POI is planted at the MEDIAN distance of that pool, so its
    # exclusion cannot be read as "it was simply further away" — anchors both
    # nearer AND further than it are on offer, and only the lens differs.
    median_hit = sorted(hits, key=lambda p: haversine_m(*PDV, p.lat, p.lng))[len(hits) // 2]
    missed = _poi(
        "missed",
        tier=5,
        lat=2 * PDV[0] - median_hit.lat,  # mirrored through the start, so the
        lng=2 * PDV[1] - median_hit.lng,  # distance is EXACTLY the median hit's
        areas=("Le Marais",),
    )
    snap = _snap(
        [*hits, missed],
        area_types={"Le Marais": "neighborhood"},
        beats_by_poi={
            **{h.id: _lensed_beats(h.id, ("hidden_history",)) for h in hits},
            "missed": _lensed_beats("missed", ("street_art",)),
        },
        lens_neighbors=_HOP_MAP,
    )
    lenses = frozenset({"hidden_history"})
    # Eligible + dimmed, not silenced: the tier-5 off-genre POI is a vignette.
    b = band_for_spotlight(spotlight(missed, lenses=lenses, snapshot=snap), tier=5)
    assert b == BAND_VIGNETTE, f"tier-5 off-genre must dim to a vignette, not silent/dwell; got {b}"
    # And it does NOT take a dwell slot: the lens-hit anchors are the route.
    inp = TourInput(
        start=PDV, duration_min=60, city_slug="paris", lenses=["hidden_history"], round_trip=True
    )
    ids = [p.id for p in select_route(inp, snap).pois]
    assert any(h.id in ids for h in hits), f"lens-hit dwell stops must be selected, got {ids}"
    # The discriminator is the lens, not the distance: an on-genre anchor sitting
    # FURTHER from the start than the off-genre one still takes a dwell slot.
    missed_m = haversine_m(*PDV, missed.lat, missed.lng)
    assert any(haversine_m(*PDV, h.lat, h.lng) > missed_m for h in hits if h.id in ids), (
        f"fixture drifted: no selected on-genre anchor is further out than 'missed'; got {ids}"
    )
    assert "missed" not in ids, (
        "a tier-5 off-genre POI is an eligible VIGNETTE (a brief mention), not a dwell "
        f"stop -- it must not take a dwell slot. Got {ids}"
    )


# ---------------------------------------------------------------------------
# Greedy selection
# ---------------------------------------------------------------------------


def test_select_route_returns_route_object_shape():
    p = _poi("p", lat=48.8556, lng=2.3658, areas=("Le Marais",))
    snap = _snap([p, *_density_fillers(PDV)], area_types={"Le Marais": "neighborhood"})
    route = select_route(
        TourInput(start=PDV, duration_min=60, city_slug="paris", round_trip=True), snap
    )
    assert hasattr(route, "pois")
    assert hasattr(route, "transits")
    assert hasattr(route, "total_walk_distance_m")
    assert hasattr(route, "total_walk_seconds")
    assert hasattr(route, "spine_area")


def test_select_route_round_trip_returns_to_origin():
    p = _poi("p", lat=48.8556, lng=2.3658, areas=("Le Marais",))
    snap = _snap([p, *_density_fillers(PDV)], area_types={"Le Marais": "neighborhood"})
    rt = select_route(
        TourInput(start=PDV, duration_min=60, city_slug="paris", round_trip=True), snap
    )
    if rt.pois:
        assert rt.transits[-1].to_poi_id is None  # close-to-origin


def test_select_route_respects_walk_budget():
    """A pool with more reachable anchors than the tour can afford must still
    come back inside the walk allocation — the tourist walks 40% of the hour,
    not 60% of it.

    The bound is READ from walk_budget_seconds rather than written down. It used
    to be the literal 1195, which was 3600 x 0.83 x 0.40 under the flat legacy
    planning policy deleted on 2026-08-04. Under the certification policy the
    same 40% allocation is 3600 x 1.00 x 0.40 = 1440, so the literal had stopped
    describing the product's own budget and would have gone on drifting silently
    at the next policy move.
    """
    from src.tour.routing import walk_budget_seconds

    # Enough spread anchors that the greedy could overspend if it were allowed
    # to, plus the filler spiral so the pool can actually fill an hour.
    pois = [
        _poi(f"p{i}", lat=48.8550 + i * 0.0010, lng=2.3650 + i * 0.0010)
        for i in range(20)
    ]
    snap = _snap([*pois, *_density_fillers(PDV)])
    inp = TourInput(start=PDV, duration_min=60, city_slug="paris", round_trip=False)
    route = select_route(inp, snap)
    assert route.total_walk_seconds <= walk_budget_seconds(60) + 5  # rounding cushion


def test_select_route_seats_each_candidate_at_most_once():
    """The stop CEILING was deleted 2026-08-04; what must still
    hold is that a long duration in a tight cluster seats each candidate once.

    The cluster used to be 25 POIs ~5 m apart. A 400-minute request now has to
    deliver at least 360 minutes of active time, and a stop speaks at most
    MAX_DWELL_AUDIO_SECONDS, so 25 colocated stops cannot come close however
    many beats they carry — the corpus is simply not big enough for the tour
    that was asked for. Sized via _anchors_for_duration instead, which is the
    same seat-count arithmetic the greedy uses.
    """
    pois = _density_fillers(
        PDV, duration_min=400, n=2 * _anchors_for_duration(400), prefix="p"
    )
    snap = _snap(pois)
    # Long duration: the time budget is now the only thing that stops the greedy.
    route = select_route(TourInput(start=PDV, duration_min=400, city_slug="paris"), snap)
    assert len(route.pois) == len({p.id for p in route.pois})


def test_long_tour_seats_more_than_the_old_twelve_stop_cap():
    """The thin-tour fix: a long request seats denser coverage of nearby POIs
    instead of overstuffing 12 stops.

    The pool is sized by _anchors_for_duration so a 250-minute request has
    somewhere to put 250/7.5 ~ 34 stops; the 25-POI grid it replaced could not
    reach the two-sided band's 225-minute floor at all, because a stop speaks at
    most MAX_DWELL_AUDIO_SECONDS however many beats sit behind it.
    """
    pois = _density_fillers(
        PDV, duration_min=250, n=2 * _anchors_for_duration(250), prefix="p"
    )
    snap = _snap(pois)
    route = select_route(TourInput(start=PDV, duration_min=250, city_slug="paris"), snap)
    n = len(route.pois)
    assert n > 12, f"a long tour should seat >12 stops, got {n}"
    assert n == len({p.id for p in route.pois})


def test_the_exact_ordering_threshold_stays_within_its_timing_ceiling():
    """The exact Held-Karp order solver stays under its 1s guard only up to ~16
    points; keep ``order_stops``' exact/fallback threshold at or under that
    measured ceiling (see tests/test_tour_ordering.py::
    test_the_exact_threshold_is_small_enough_to_be_called_in_a_loop). This
    guarantee moved out of ``HARD_ANCHOR_CAP`` when the stop ceiling was deleted:
    the planner no longer bounds n, so the ORDERER has to."""
    from src.tour.ordering import ORDERING_EXACT_MAX

    assert ORDERING_EXACT_MAX <= 16


def test_select_route_deterministic_under_input_shuffle():
    base = [
        _poi(f"p{i}", lat=48.8550 + i * 0.0001, lng=2.3650 + i * 0.0001)
        for i in range(8)
    ] + _density_fillers(PDV)

    import random

    rng = random.Random(0)
    a = list(base)
    b = list(base)
    rng.shuffle(a)
    rng.shuffle(b)

    snap_a = _snap(a)
    snap_b = _snap(b)
    inp = TourInput(start=PDV, duration_min=60, city_slug="paris", round_trip=False)
    route_a = select_route(inp, snap_a)
    route_b = select_route(inp, snap_b)
    assert [p.id for p in route_a.pois] == [p.id for p in route_b.pois]


def test_explicit_default_planning_policy_is_byte_identical_to_omitting_it():
    """Threading the policy must be a strict no-op for every existing caller.

    This used to thread LEGACY_ROUTE_PLANNING_POLICY, the flat 0.83 planning
    policy deleted on 2026-08-04 — so as written it could only ever raise
    ImportError. What it was really guarding is still live and still worth
    guarding: passing a policy explicitly must produce byte-identical output to
    leaving the argument off. It now threads the surviving policy. That the
    legacy object stays deleted is pinned separately, by the deleted-name list
    in tests/test_tour_one_engine.py.
    """
    from src.tour.routing import DEFAULT_ROUTE_PLANNING_POLICY

    pois = _density_fillers(PDV)
    snap = _snap(pois, area_types={"Le Marais": "neighborhood"})
    inp = TourInput(start=PDV, duration_min=60, city_slug="paris", round_trip=False)

    implicit = select_route(inp, snap)
    explicit = select_route(
        inp,
        snap,
        planning_policy=DEFAULT_ROUTE_PLANNING_POLICY,
    )

    assert explicit.model_dump(mode="json") == implicit.model_dump(mode="json")


def test_select_route_prefers_spine_area():
    # Two POIs equidistant; one in spine area, one in non-adjacent area.
    in_marais = _poi(
        "marais",
        lat=48.8556,
        lng=2.3658,
        areas=("Le Marais",),
        beat_count=5,
    )
    out_other = _poi(
        "other",
        lat=48.8554,  # equally close — flip lat slightly
        lng=2.3654,
        areas=("Other Hood",),
        beat_count=5,
    )
    # Force spine to be Le Marais by sheer count: the filler spiral is all in
    # Marais and near the start, so the spine vote wins. It also carries the
    # density gate and (since the band went two-sided on 2026-08-04) supplies
    # enough anchors to fill the hour. The assertions are unchanged.
    fillers = _density_fillers(PDV, prefix="f")
    snap = _snap(
        [in_marais, out_other, *fillers],
        area_types={"Le Marais": "neighborhood", "Other Hood": "neighborhood"},
    )
    route = select_route(TourInput(start=PDV, duration_min=60, city_slug="paris"), snap)
    assert route.spine_area == "Le Marais"
    ids = [p.id for p in route.pois]
    # Spine POI must be selected (bias is a scoring multiplier, not a hard
    # filter — but in a tight cluster of equals, the spine POI is preferred).
    assert "marais" in ids


def test_yellow_density_attaches_assessment_to_route():
    """A YELLOW assessment must surface as route.tourability.

    YELLOW means "there is less to say here than an hour wants, but a tour is
    still worth building", so this fixture has to be thin in EXACTLY one way and
    healthy in every other. Thin on audio: eight anchors carrying 225 s of stories
    each, 1800 s against a 2160 s target, a fill ratio of 0.83. Healthy on
    everything else: eight anchors is well past the four the density gate wants,
    and they are spread over 220 m so the tour still walks enough to fill the hour
    it was asked for.

    That second half is what changed on 2026-08-04. The fixture used to be five
    POIs standing on top of each other, which delivered about twenty minutes of
    tour against a fifty-four minute floor and was refused outright — so the
    disclosure this test exists to check was never reached. Sizing each anchor's
    whole beat set below one stop's speaking ceiling also matters: a POI holding
    more audio than a stop can ever voice is credited for the surplus, and the
    planner stops adding stops on the strength of audio the tourist never hears.
    """
    pois = _density_fillers(
        PDV, duration_min=60, n=8, radius_m=220.0, tier=5, beat_count=5, prefix="y"
    )
    beats: dict[str, list[BeatRef]] = {
        p.id: [
            BeatRef(
                id=f"{p.id}-b{j}",
                poi_id=p.id,
                est_spoken_seconds=45,
                active_status="active",
            )
            for j in range(5)
        ]
        for p in pois
    }
    snap = _snap(
        pois,
        area_types={"Le Marais": "neighborhood"},
        beats_by_poi=beats,
    )
    route = select_route(
        TourInput(start=PDV, duration_min=60, city_slug="paris", round_trip=False),
        snap,
    )
    assert route.tourability is not None
    assert route.tourability.status == "YELLOW"
    assert 0.5 <= route.tourability.fill_ratio < 1.0


def test_zero_beat_poi_excluded_from_anchor_pool():
    """Selection guard: tier-3+ POIs with 0 active beats are dropped."""
    # Both carry the fillers' area so the control POI ("rich" must be selected)
    # is not quietly outscored on spine alignment instead of on beats.
    # Both carry the fillers' area so the control POI ("rich" must be selected)
    # is not quietly outscored on spine alignment instead of on beats, and both
    # sit out at the rim of the filler spiral rather than on top of the start.
    # A stop AT the start contributes no walking, which since 2026-08-04 makes it
    # the first thing the certification timebox repair trades away when it is
    # hunting for active seconds — the control would then fail for a reason that
    # has nothing to do with beat counts.
    rich = _poi(
        "rich",
        tier=5,
        lat=PDV[0] + 0.0018,
        lng=PDV[1],
        areas=("Le Marais",),
        beat_count=8,
    )
    empty = _poi(
        "empty",
        tier=4,
        lat=PDV[0] + 0.0018,
        lng=PDV[1] + 0.00005,
        areas=("Le Marais",),
        beat_count=0,
    )
    snap = _snap(
        [rich, empty, *_density_fillers(PDV, radius_m=100.0, tier=3, beat_count=3)],
        area_types={"Le Marais": "neighborhood"},
        beats_by_poi={"empty": []},  # explicit zero
    )
    route = select_route(TourInput(start=PDV, duration_min=60, city_slug="paris"), snap)
    ids = {p.id for p in route.pois}
    assert "rich" in ids
    assert "empty" not in ids, (
        "Zero-beat POI must be excluded from the candidate pool — "
        "this was the Petit Palais bug"
    )


def test_select_route_empty_when_no_candidates_in_envelope():
    """An empty reachable envelope now raises TourabilityRefusedError.

    Before the density gate this returned an empty Route. The density gate runs
    before the envelope filter and refuses (RED) when there's no corpus
    to support a tour at all.
    """
    from src.tour.density import TourabilityRefusedError

    far = _poi("far", lat=49.5, lng=3.0)  # well outside Paris
    snap = _snap([far])
    with pytest.raises(TourabilityRefusedError) as excinfo:
        select_route(TourInput(start=PDV, duration_min=60, city_slug="paris"), snap)
    assert excinfo.value.assessment.status == "RED"
    assert excinfo.value.assessment.reachable_poi_count == 0


def test_fixed_end_red_start_circle_defers_to_routed_fixed_end_checks():
    """A RED start circle does not decide a fixed-destination tour by itself.

    With no destination, a RED density assessment refuses on the spot. With one,
    it must stand aside: whether the tour is possible is a question about the
    route to B, and the checks that know about B run later. This test pins which
    layer answers, on a corpus that is RED either way.

    A reachable destination gets past the density gate and is then judged on
    whether a plan can fill the requested hour — which this one-POI corpus cannot,
    so the refusal comes from certification. An unreachable destination is
    refused by the destination check itself, before any of that. Two different
    refusals from the same RED corpus is the whole point; if the density gate
    stopped deferring, the first case would refuse with the second's kind of
    error and this test would fail.

    What B-materialization does once a fixed-end route IS buildable is covered on
    a corpus that can be built, in tests/test_tour_b_materialization.py.
    """
    from src.tour.density import TourabilityRefusedError, assess_snapshot

    close_end = (PDV[0], PDV[1] + 0.001)
    destination = _poi(
        "destination",
        lat=close_end[0],
        lng=close_end[1],
        beat_count=1,
    )
    snap = _snap([destination])
    viable = TourInput(
        start=PDV,
        end=close_end,
        duration_min=60,
        city_slug="paris",
    )
    assert assess_snapshot(viable, snap).status == "RED"

    # Deferred: the density gate did NOT raise. While the floor was soft this
    # corpus SHIPPED a six-minute day for a sixty-minute ask, disclosed. That
    # cliff is ruled out: a dial turn may refuse with a reason, or re-plan a
    # full day; it may NEVER quietly hand back a sixth of what was asked for
    # (UNDERFILL_REFUSAL_FRACTION). So deferral is proved the
    # way this test's own docstring first proved it: by WHICH refusal arrives.
    # A RED start circle with a reachable end is refused by CERTIFICATION,
    # naming the shortfall in minutes and what binds — never by the density
    # gate.
    from src.tour.selection import CertificationPlanningInfeasibleError

    with pytest.raises(CertificationPlanningInfeasibleError) as caught:
        select_route(viable, snap)
    reason = caught.value.reason
    assert "far short of the 60 minutes asked for" in reason, reason
    assert "not enough to visit near this start" in reason, reason

    impossible = viable.model_copy(update={"end": (PDV[0], PDV[1] + 0.1)})
    with pytest.raises(TourabilityRefusedError, match="Destination unreachable"):
        select_route(impossible, snap)


# ---------------------------------------------------------------------------
# Endpoint-pull for one-way routes
# ---------------------------------------------------------------------------




def test_endpoint_pull_does_not_apply_to_round_trip():
    """Round-trip routes must not append a far-envelope stop at the end."""
    start = (48.85675, 2.341033)
    near = _poi(
        "near",
        tier=5,
        lat=start[0],
        lng=start[1] + 0.0002,
        areas=("Île de la Cité",),
        beat_count=5,
    )
    far = _poi(
        "far",
        tier=5,
        lat=start[0],
        lng=start[1] + 0.0050,
        areas=("Île de la Cité",),
        beat_count=5,
    )
    fillers = _density_fillers(start)
    snap = _snap([near, far, *fillers], area_types={"Île de la Cité": "island"})
    rt = select_route(
        TourInput(start=start, duration_min=60, city_slug="paris", round_trip=True),
        snap,
    )
    if rt.pois:
        # Last segment must close to origin (to_poi_id=None), not to a POI.
        assert rt.transits[-1].to_poi_id is None


def test_endpoint_pull_respects_walk_budget():
    """Endpoint-pull won't be applied if it would blow the walk budget."""
    start = (48.85675, 2.341033)
    # Make the far candidate so distant that adding it busts the budget.
    near = _poi(
        "near",
        tier=5,
        lat=start[0],
        lng=start[1] + 0.0001,
        areas=("Île de la Cité",),
        beat_count=5,
    )
    too_far_lng = start[1] + 0.020  # ~1.5km — ringed against a 60-min budget
    too_far = _poi(
        "too-far",
        tier=5,
        lat=start[0],
        lng=too_far_lng,
        areas=("Île de la Cité",),
        beat_count=5,
    )
    snap = _snap(
        [near, too_far, *_density_fillers(start, tier=3, beat_count=3)],
        area_types={"Île de la Cité": "island"},
    )
    route = select_route(
        TourInput(start=start, duration_min=60, city_slug="paris", round_trip=False),
        snap,
    )
    # too-far should be rejected — outside envelope or busts walk budget.
    assert "too-far" not in [p.id for p in route.pois]


def test_endpoint_pull_never_evicts_entire_route():
    """A far beat-mountain anchor must not evict every greedy incumbent.

    Regression for the 2026-07-02 Rue Cler collapse: a 60-min one-way
    preview near Invalides returned a ONE-stop tour. The greedy had seated
    a sane 2-stop near cluster, then the endpoint-pull ranked far-envelope
    candidates by raw poi_score — a tier-5 POI with an outlier beat count
    (39, inflated by a book-extraction campaign) won despite its leg eating
    ~90% of the walk budget — and dropped BOTH incumbents (exactly
    ENDPOINT_PULL_MAX_DROPS) because only the far anchor alone fit. A tour
    must never collapse to just the pulled endpoint: the pull abandons
    instead and the greedy cluster survives.

    Geometry (60-min one-way: walk budget 1195s, greedy budget 896s,
    envelope 738m, far floor 369m, 3km/h x1.35 pace => ~1.62 s/m): two
    cheap tier-5 anchors west (250m, 400m — the greedy seats both for
    ~650s and can afford nothing more), a 39-beat tier-5 anchor 600m east
    (solo leg ~970s <= 1195s: the collapse candidate; any trial keeping an
    incumbent busts the budget), and three tier-3 decoys 500-600m out in
    other directions so the density gate's rich-pool clause (fill >= 1.5,
    anchors >= 6) clears without adding anything the greedy can afford.
    """
    start = (48.8568, 2.3414)
    area = ("Île de la Cité",)
    near1 = _poi(
        "near-1", tier=5, lat=start[0], lng=start[1] - 0.003415, areas=area, beat_count=6
    )  # 250m W
    near2 = _poi(
        "near-2", tier=5, lat=start[0], lng=start[1] - 0.005464, areas=area, beat_count=6
    )  # 400m W
    # The greedy cluster the pull must not wipe out. It was exactly near-1 and
    # near-2 until 2026-08-04; two stops can no longer fill an hour, so the
    # cluster is now a west-side group around them. The invariant is unchanged —
    # the pull may not leave the far anchor standing alone — but it is asserted
    # over the cluster rather than over two specific ids, because with more than
    # two members the engine is free to seat any of them.
    west_cluster = _density_fillers(
        (start[0], start[1] - 0.00444),  # ~325m W, between near-1 and near-2
        radius_m=110.0,
        beat_count=6,
        prefix="west",
    )
    far = _poi(
        "far-mountain", tier=5, lat=start[0], lng=start[1] + 0.008197, areas=area, beat_count=39
    )  # 600m E
    decoys = [
        _poi("decoy-n", tier=3, lat=start[0] + 0.004950, lng=start[1], areas=area, beat_count=3),
        _poi("decoy-s", tier=3, lat=start[0] - 0.005400, lng=start[1], areas=area, beat_count=3),
        _poi(
            "decoy-ne",
            tier=3,
            lat=start[0] + 0.003150,
            lng=start[1] + 0.004781,
            areas=area,
            beat_count=3,
        ),
    ]
    snap = _snap(
[near1, near2, far, *decoys, *west_cluster],
        area_types={"Île de la Cité": "island"},
    )

    inp = TourInput(start=start, duration_min=60, city_slug="paris", round_trip=False)
    route = select_route(inp, snap)
    ids = [p.id for p in route.pois]

    # Pre-fix this came back as exactly ["far-mountain"] — one stop.
    assert ids != ["far-mountain"], "endpoint-pull evicted the entire greedy route"
    assert len(ids) >= 2, f"one-stop tour emitted: {ids}"
    # The greedy's west cluster must survive the pull.
    cluster_ids = {near1.id, near2.id, *(p.id for p in west_cluster)}
    survivors = cluster_ids & set(ids)
    assert len(survivors) >= 2, (
        f"the pull left fewer than two west-cluster anchors standing: {ids}"
    )


def test_isolated_single_anchor_is_refused_with_the_duration_it_could_support():
    """One anchor in reach: the tour is refused, and the refusal says what fits.

    This is the Père Lachaise pattern (found 2026-07-02): a start
    point with exactly one dwellable anchor anywhere near it. It used to be
    answered by building the one-stop tour anyway and hanging a YELLOW "this area
    is thin" assessment on it, so the surfaces could explain the single stop
    rather than look broken.

    That outcome became unreachable on 2026-08-04, and the arithmetic says so
    rather than any judgement call. A stop speaks at most MAX_DWELL_AUDIO_SECONDS
    (270 s) and a tour may spend at most 0.40 of its duration walking, so a
    one-stop tour can fill at most 0.40 x duration + 270 s — while the
    certification band will not accept less than 0.90 x duration. Those only
    overlap below about nine minutes, which is shorter than anything the product
    offers. The sweep below pins that: the refusal is not a quirk of one duration.

    So the honest answer moved one step earlier, from a disclosure to a refusal —
    and it is a better answer, because it carries the duration this corpus COULD
    support instead of quietly handing over a minute of tour for an hour asked
    for. The YELLOW disclosure itself is still live and is exercised on a corpus
    that can actually be built, by
    ``test_yellow_density_attaches_assessment_to_route``.
    """
    from src.tour.density import TourabilityRefusedError, assess

    start = (48.8568, 2.3414)
    lone = _poi(
        "lone-anchor",
        tier=4,
        lat=start[0],
        lng=start[1] + 0.002,
        areas=("Île de la Cité",),
        beat_count=5,
    )
    snap = _snap([lone], area_types={"Île de la Cité": "island"})

    for duration_min in (30, 60, 120):
        inp = TourInput(
            start=start, duration_min=duration_min, city_slug="paris", round_trip=False
        )
        assessment = assess(inp, snap.pois, snap.beats_by_poi)
        assert assessment.anchor_candidate_count == 1, "the fixture's whole premise"
        assert assessment.status == "RED"

        with pytest.raises(TourabilityRefusedError) as excinfo:
            select_route(inp, snap)
        refused = excinfo.value.assessment
        assert refused.status == "RED"
        assert refused.anchor_candidate_count == 1
        # The useful half of the refusal: what this corpus can actually carry.
        assert refused.max_supportable_duration_min is not None
        assert refused.max_supportable_duration_min < duration_min


# ---------------------------------------------------------------------------
# Spine tie-break
# ---------------------------------------------------------------------------


def test_spine_tiebreak_prefers_island_over_tied_district():
    """When island and district score identically, island wins."""
    pois = [
        _poi("a", tier=5, lat=48.857, lng=2.341, areas=("Île de la Cité", "1st Arrondissement")),
        _poi("b", tier=5, lat=48.856, lng=2.342, areas=("Île de la Cité", "1st Arrondissement")),
        _poi("c", tier=4, lat=48.855, lng=2.343, areas=("Île de la Cité", "1st Arrondissement")),
    ]
    snap = _snap(
        pois,
        area_types={
            "Île de la Cité": "island",
            "1st Arrondissement": "district",
        },
    )
    spine = pick_spine_area(48.857, 2.341, pois, snap)
    assert spine == "Île de la Cité"


def test_spine_tiebreak_prefers_neighborhood_over_tied_district():
    pois = [
        _poi("a", tier=5, lat=48.855, lng=2.366, areas=("Le Marais", "4th Arrondissement")),
        _poi("b", tier=4, lat=48.856, lng=2.366, areas=("Le Marais", "4th Arrondissement")),
    ]
    snap = _snap(
        pois,
        area_types={"Le Marais": "neighborhood", "4th Arrondissement": "district"},
    )
    spine = pick_spine_area(48.855, 2.366, pois, snap)
    assert spine == "Le Marais"


# 2x district dominance rule.


def test_spine_prefers_island_over_district_below_2x():
    """Island B at 1.875x ratio (below 2x threshold) → smaller Area wins."""
    # 7 POIs in district A, ∑tier = 30 (5+5+5+5+4+3+3).
    district_pois = [
        _poi(f"d{i}", tier=t, lat=48.857, lng=2.341, areas=("District A",))
        for i, t in enumerate([5, 5, 5, 5, 4, 3, 3])
    ]
    # 4 POIs in island B, ∑tier = 16 (5+5+3+3).
    island_pois = [
        _poi(f"i{i}", tier=t, lat=48.857, lng=2.341, areas=("Island B",))
        for i, t in enumerate([5, 5, 3, 3])
    ]
    snap = _snap(
        district_pois + island_pois,
        area_types={"District A": "district", "Island B": "island"},
    )
    spine = pick_spine_area(48.857, 2.341, district_pois + island_pois, snap)
    assert spine == "Island B", f"ratio 30/16=1.875 < 2x → island should win, got {spine}"


def test_spine_picks_district_when_2x_dominant():
    """District A at 3x ratio (above 2x threshold) → district wins."""
    # 12 POIs in district A, ∑tier = 60.
    district_pois = [
        _poi(f"d{i}", tier=5, lat=48.857, lng=2.341, areas=("District A",)) for i in range(12)
    ]
    # 4 POIs in island B, ∑tier = 20.
    island_pois = [
        _poi(f"i{i}", tier=5, lat=48.857, lng=2.341, areas=("Island B",)) for i in range(4)
    ]
    snap = _snap(
        district_pois + island_pois,
        area_types={"District A": "district", "Island B": "island"},
    )
    spine = pick_spine_area(48.857, 2.341, district_pois + island_pois, snap)
    assert spine == "District A", f"ratio 60/20=3x ≥ 2x → district wins, got {spine}"


def test_spine_district_dominance_picks_strongest_specific_when_multiple():
    """When several specific Areas qualify, take the highest-vote one."""
    # District big, two contender islands; island B has more votes.
    district_pois = [
        _poi(f"d{i}", tier=5, lat=48.857, lng=2.341, areas=("District A",))
        for i in range(8)  # ∑tier = 40
    ]
    weak_island = [
        _poi(f"w{i}", tier=4, lat=48.857, lng=2.341, areas=("Island weak",))
        for i in range(3)  # ∑tier = 12
    ]
    strong_island = [
        _poi(f"s{i}", tier=5, lat=48.857, lng=2.341, areas=("Island strong",))
        for i in range(5)  # ∑tier = 25
    ]
    snap = _snap(
        district_pois + weak_island + strong_island,
        area_types={
            "District A": "district",
            "Island weak": "island",
            "Island strong": "island",
        },
    )
    pois = district_pois + weak_island + strong_island
    spine = pick_spine_area(48.857, 2.341, pois, snap)
    # threshold = 40 / 2 = 20 → Island weak (12) excluded, Island strong (25) qualifies.
    assert spine == "Island strong"


# Latin Quarter polygon regression.


def test_notre_dame_in_ile_not_latin_quarter():
    """Live-data regression against the **production** Paris corpus.

    Asserts the Latin Quarter polygon-overshoot bug has been fixed: Notre-Dame
    sits in Île de la Cité, not Latin Quarter. Uses the separately pinned
    localhost dev-graph profile because pytest's destructive fixtures use the
    disposable test DB.
    """

    from tests.live_graph import open_dev_driver

    driver = open_dev_driver()
    if driver is None:
        pytest.skip("local dev Neo4j unreachable")
    try:
        with driver.session() as s:
            r = s.run(
                "MATCH (p:POI {city_name: 'paris', name: 'Notre-Dame Cathedral'})"
                "-[:WITHIN]->(a:Area) RETURN collect(a.name) AS areas"
            ).single()
        areas = set(r["areas"]) if r else set()
    finally:
        driver.close()
    if not areas:
        pytest.skip("Notre-Dame not present in production corpus")
    assert "Île de la Cité" in areas, f"ND missing from Île de la Cité: {areas}"
    assert "Latin Quarter" not in areas, f"ND still leaks into Latin Quarter: {areas}"


# ---------------------------------------------------------------------------
# Fill pass: target_audio is a floor, not a soft stop
# ---------------------------------------------------------------------------


def test_fill_pass_adds_anchors_when_below_floor():
    """Greedy stalls below the audio floor with walk slack remaining → fill pass adds.

    Synthetic 180-min one-way scenario: 3 anchors clustered near start
    (greedy fills these cheaply but only consumes ~15 min walk and ~15
    min dwell) plus 6 additional reachable tier-3 candidates whose
    individual walk costs are all small. Greedy picks the cheap cluster,
    stops because score-per-cost flattens, leaving walk slack and dwell
    well below the audio floor. Fill pass should add at least one of
    the residual candidates.
    """
    start = (48.85675, 2.341033)
    main = [
        _poi(
            f"main-{i}",
            tier=5,
            lat=start[0] + 0.0001 * i,
            lng=start[1] + 0.0002 * i,
            areas=("Le Marais",),
            beat_count=8,
        )
        for i in range(3)
    ]
    fill_candidates = [
        _poi(
            f"fill-{i}",
            tier=3,
            lat=start[0] + 0.0008 * (i + 1),
            lng=start[1] + 0.0010 * (i + 1),
            areas=("Le Marais",),
            beat_count=4,
        )
        for i in range(6)
    ]
    snap = _snap(
        main + fill_candidates + _density_fillers(start, duration_min=180, tier=3, beat_count=3),
        area_types={"Le Marais": "neighborhood"},
    )

    inp = TourInput(start=start, duration_min=180, city_slug="paris", round_trip=False)
    route = select_route(inp, snap)
    ids = [p.id for p in route.pois]

    assert any(i.startswith("fill-") for i in ids), (
        f"Fill pass should have pulled in at least one tier-3 candidate; got POIs: {ids}"
    )


def test_fill_pass_does_not_run_when_already_above_floor():
    """When greedy already meets the audio floor, fill pass is a no-op.

    The mirror image of the test above: there, the greedy stalls short of the
    audio floor and the fill pass has to rescue it, proven by a tier-3 candidate
    named ``fill-*`` turning up in the route. Here the rich cluster satisfies the
    floor on its own, and the SAME kind of candidate must stay out.

    Naming the forbidden additions is what makes this checkable. It used to
    assert a stop COUNT (at most six, the anchor cap of the day), which stopped
    meaning anything once a tour had to fill the duration it was asked for:
    a healthy hour now seats around eight stops, so the old bound failed on a
    perfectly correct route and could not have told a fill-pass addition from a
    legitimate one anyway.
    """
    start = (48.85675, 2.341033)
    # A rich tier-5 cluster with enough anchors that the greedy clears the audio
    # floor on its own and the tour is full on walking too. Twenty is inside a
    # verified block (18 through 24 all hold; 16 leaves the plan short of the
    # band and it is refused before the fill pass is even the question).
    rich = _density_fillers(
        start, duration_min=60, n=20, tier=5, beat_count=12, prefix="rich"
    )
    # The bait: thin, low-tier, several hundred metres out — inside reach, so
    # nothing geometric excludes them, but exactly what a fill pass reaches for.
    bait = [
        _poi(
            f"fill-{i}",
            tier=3,
            lat=start[0] + 0.0040 + 0.0004 * i,
            lng=start[1] + 0.0030 + 0.0004 * i,
            areas=("Le Marais",),
            beat_count=2,
        )
        for i in range(4)
    ]
    snap = _snap([*rich, *bait], area_types={"Le Marais": "neighborhood"})
    for poi in bait:
        assert haversine_m(*start, poi.lat, poi.lng) <= envelope_radius_m(
            60, round_trip=False
        ), "the bait must be reachable, or its absence proves nothing"
    inp = TourInput(start=start, duration_min=60, city_slug="paris", round_trip=False)
    route = select_route(inp, snap)
    ids = [p.id for p in route.pois]
    assert not any(i.startswith("fill-") for i in ids), (
        f"greedy already cleared the audio floor, so no fill candidate should "
        f"have been added; got {ids}"
    )


def test_fixed_end_fill_uses_emitted_audio_and_adds_safe_content(monkeypatch):
    """A→B fill must not over-credit thin landmarks with tier dwell proxies.

    Twenty landmarks sit strung along the corridor from A to B, and each of them
    has only 105 seconds of story to tell. The old fixed-end currency did not ask
    what they would actually say: it valued every stop at a flat 300 seconds by
    tier, so seven of them "cleared" the fill floor on 2100 seconds of audio that
    was never going to be spoken, and the remaining thirteen — all of them safely
    on the path, costing almost nothing to visit — were left out of a tour that
    was in fact badly short. Emission voices the capped beat plans, so selection
    has to spend the same seconds. The proof is that EVERY on-path stop is taken.

    Twenty is the arithmetic of the time band, not an arbitrary enrichment. A stop
    speaks at most 270 seconds, walking is capped at 0.40 of the request, and an
    hour must deliver at least 0.90 of it, so about 1800 seconds of audio has to
    come from somewhere — which at 105 seconds a stop is eighteen stops before the
    walking is even counted. The rich off-corridor anchors keep the density
    pre-check GREEN while staying ineligible for this A→B route.
    """
    from src.tour.routing import planned_audio_seconds, walk_budget_seconds

    start = PDV
    corridor_step = 0.0005
    on_path = [
        _poi(
            f"on-path-{i}",
            tier=5,
            lat=start[0],
            lng=start[1] + corridor_step * (i + 1),
            areas=("Test Corridor",),
            beat_count=3,
        )
        for i in range(20)
    ]
    end = (start[0], start[1] + corridor_step * (len(on_path) + 2))
    density_only = [
        _poi(
            f"off-corridor-{i}",
            tier=5,
            lat=start[0] + 0.004 + 0.0001 * i,
            lng=start[1] + 0.0001 * i,
            areas=("Test Corridor",),
            beat_count=3,
        )
        for i in range(4)
    ]
    beats_by_poi = {
        poi.id: [
            BeatRef(
                id=f"{poi.id}-b{j}",
                poi_id=poi.id,
                est_spoken_seconds=35 if poi in on_path else 240,
                active_status="active",
            )
            for j in range(3)
        ]
        for poi in [*on_path, *density_only]
    }
    snap = _snap(
        [*on_path, *density_only],
        area_types={"Test Corridor": "corridor"},
        beats_by_poi=beats_by_poi,
    )

    def endpoint_pull_must_not_run(*args, **kwargs):
        raise AssertionError("a fixed destination supersedes open-walk endpoint pull")

    monkeypatch.setattr(
        "src.tour.selection._apply_endpoint_pull", endpoint_pull_must_not_run
    )

    route = select_route(
        TourInput(start=start, end=end, duration_min=60, city_slug="paris"), snap
    )
    selected_ids = {poi.id for poi in route.pois}

    assert {poi.id for poi in on_path} <= selected_ids, (
        "the emitted-audio deficit should admit the seventh safe on-path stop"
    )
    assert selected_ids.isdisjoint(poi.id for poi in density_only)
    capped = build_poi_beat_plans_capped(route, snap, lenses=None, end_is_none=False)
    emitted_audio = sum(planned_audio_seconds(plan.beats) for plan, _ in capped)
    assert route.total_walk_seconds <= walk_budget_seconds(60)
    assert route.total_walk_seconds + emitted_audio <= route.err_short_total_seconds


def test_fixed_end_insertion_currency_includes_bare_destination():
    """A fixed B contributes to the empty base route and every insertion delta."""
    from src.tour.selection import (
        _full_route_walk_seconds,
        _insertion_cost_with_fixed_end,
    )

    start = (0.0, 0.0)
    end = (0.0, 10.0)
    candidate = _poi("candidate", lat=1.0, lng=5.0, beat_count=3)

    def routed(lat1, lng1, lat2, lng2):
        return round(math.hypot(lat2 - lat1, lng2 - lng1) * 100)

    base = _full_route_walk_seconds(
        [],
        start_lat=start[0],
        start_lng=start[1],
        round_trip=False,
        leg_seconds_fn=routed,
        fixed_end=end,
    )
    extra, idx = _insertion_cost_with_fixed_end(
        candidate,
        [],
        start_lat=start[0],
        start_lng=start[1],
        fixed_end=end,
        leg_seconds_fn=routed,
    )

    assert base == routed(*start, *end)
    assert idx == 0
    assert base + extra == routed(*start, candidate.lat, candidate.lng) + routed(
        candidate.lat, candidate.lng, *end
    )


def test_fixed_end_elapsed_rescue_considers_corridor_failure_but_rejects_over_ceiling():
    """Rescue uses exact pinned-B elapsed currency, not a corridor tolerance."""
    from src.tour.selection import _apply_fill_pass, _full_route_walk_seconds

    start = (0.0, 0.0)
    end = (0.0, 10.0)
    base = _poi("base", tier=5, lat=0.0, lng=5.0, beat_count=3)
    near = _poi("a-near", tier=5, lat=1.0, lng=5.0, beat_count=3)
    far = _poi("z-far", tier=5, lat=10.0, lng=5.0, beat_count=3)
    snap = _snap([base, near, far])

    def routed(lat1, lng1, lat2, lng2):
        return round(math.hypot(lat2 - lat1, lng2 - lng1) * 100)

    walk_budget = 1000
    assert routed(*start, near.lat, near.lng) + routed(near.lat, near.lng, *end) > walk_budget
    added: list[str] = []
    selected = _apply_fill_pass(
        [base],
        [base],
        spine=None,
        interest=frozenset(),
        snapshot=snap,
        start_lat=start[0],
        start_lng=start[1],
        round_trip=False,
        walk_budget=walk_budget,
        dwell_budget=1000,
        stop_cost_fn=lambda poi, *, exempt: 100,
        exempt_anchor_id=None,
        rescue_floor=2,
        leg_seconds_fn=routed,
        fixed_end=end,
        rescue_candidates=[near, far],
        rescue_added_ids=added,
    )

    reversed_added: list[str] = []
    reversed_selected = _apply_fill_pass(
        [base],
        [base],
        spine=None,
        interest=frozenset(),
        snapshot=snap,
        start_lat=start[0],
        start_lng=start[1],
        round_trip=False,
        walk_budget=walk_budget,
        dwell_budget=1000,
        stop_cost_fn=lambda poi, *, exempt: 100,
        exempt_anchor_id=None,
        rescue_floor=2,
        leg_seconds_fn=routed,
        fixed_end=end,
        rescue_candidates=[far, near],
        rescue_added_ids=reversed_added,
    )

    assert {poi.id for poi in selected} == {"base", "a-near"}
    assert added == ["a-near"]
    assert [poi.id for poi in reversed_selected] == [poi.id for poi in selected]
    assert reversed_added == added
    pinned_walk = _full_route_walk_seconds(
        selected,
        start_lat=start[0],
        start_lng=start[1],
        round_trip=False,
        leg_seconds_fn=routed,
        fixed_end=end,
    )
    assert pinned_walk > walk_budget, "rescue may exceed the walking allocation"
    assert pinned_walk + 200 <= walk_budget / 0.4, "but not the elapsed ceiling"


def test_fixed_end_final_exact_elapsed_guard_catches_post_order_drift(monkeypatch):
    """A fixed-B route that drifts out of the time band AFTER planning is refused.

    Ordering, final routing and the beat caps all run after the plan is chosen,
    and each of them can move the route's real elapsed time. A route that was in
    the band when it was certified and out of it by the time it is handed back is
    exactly the failure worth catching, because nothing downstream re-measures.

    The drift is simulated by making the final summary report a preposterous walk
    (100,000 seconds) that the planner never priced. The control run below proves
    the fixture is otherwise healthy, so the refusal can only be the drift.
    """
    from src.tour import selection as selection_module

    start = PDV
    # The destination is ~590 m out, which puts real walking seconds on the A->B
    # leg itself, and the corridor pool is packed tight so its detours stay inside
    # the walk allocation.
    end = (start[0], start[1] + 0.008)
    pois = _density_fillers(start, duration_min=60, radius_m=90.0)
    snap = _snap(pois, area_types={"Le Marais": "neighborhood"})
    inp = TourInput(start=start, end=end, duration_min=60, city_slug="paris")

    # Control: undrifted, this fixture plans and is handed back.
    assert select_route(inp, snap).pois

    real_summarise = selection_module.summarise_route

    def drifted_summarise(*args, **kwargs):
        route = real_summarise(*args, **kwargs)
        return route.model_copy(update={"total_walk_seconds": 100_000})

    monkeypatch.setattr(selection_module, "summarise_route", drifted_summarise)
    with pytest.raises(CertificationPlanningInfeasibleError, match="post-selection transforms"):
        select_route(inp, snap)


def test_fill_pass_respects_walk_budget_cap():
    """Fill pass clips at FILL_PASS_WALK_BUDGET_FRAC x walk_budget."""
    from src.tour.routing import walk_budget_seconds
    from src.tour.selection import FILL_PASS_WALK_BUDGET_FRAC

    start = (48.85675, 2.341033)
    # Three near anchors + several far candidates the fill pass can't fit.
    near = [
        _poi(
            f"n-{i}",
            tier=5,
            lat=start[0] + 0.0001 * i,
            lng=start[1] + 0.0001 * i,
            areas=("Le Marais",),
            beat_count=8,
        )
        for i in range(3)
    ]
    # Each "far" candidate is ~1km from origin, so any insertion costs ≥ 14 min.
    far = [
        _poi(
            f"f-{i}",
            tier=3,
            lat=start[0] + 0.005 + 0.0005 * i,
            lng=start[1] + 0.010 + 0.0005 * i,
            areas=("Le Marais",),
            beat_count=2,
        )
        for i in range(8)
    ]
    snap = _snap(
        near + far + _density_fillers(start, tier=3, beat_count=3),
        area_types={"Le Marais": "neighborhood"},
    )
    inp = TourInput(start=start, duration_min=60, city_slug="paris", round_trip=False)
    route = select_route(inp, snap)
    walk_cap = int(walk_budget_seconds(60) * FILL_PASS_WALK_BUDGET_FRAC)
    assert route.total_walk_seconds <= walk_cap


def test_fill_pass_under_floor_rescue_adds_nearby_stop():
    """A 2nd nearby stop that busts walk_budget but fits total planned time
    is added while the tour is below the stop floor and the audio floor.

    Mirrors the live Latin Quarter 60-min loop: greedy seats one stop (Pantheon),
    the second (Sorbonne) round-trip detour busts walk_budget, yet audio is far
    under target and total time has ample slack. The under-fill rescue admits it.

    **THE SECOND STOP'S WORTH WAS RE-DERIVED ON 2026-08-24, not a nursed
    number.** This fixture used to seat B on a
    370-second dwell against 1420 seconds of marginal walking — a ratio of 3.84,
    i.e. TWENTY-FOUR MINUTES OF WALKING FOR A SIX-MINUTE LOOK. That shape is
    ruled out: at the old 4.5 ratio a visitor walks twenty-two minutes for a
    five-minute look, which is not a tour. What the test EXISTS for — a
    one-stop tour is lifted to two by the rescue, and the ordinary pass alone
    would not do it — is unchanged and still proven by both arms below. What
    changed is that the stop it reaches for now EARNS its walk: B is a
    fifteen-minute stop (900 s), so the trade is 1420:900 = 1.58 — inside the
    dearest trade a visitor defends (1.8, sixteen minutes' walk for nine silent
    minutes at the deportation memorial) and under the locked 2.0 cap. The
    geometry is untouched; only the claim about what is worth walking to moved.
    """
    from src.tour.routing import walk_budget_seconds
    from src.tour.selection import _apply_fill_pass, target_dwell_seconds

    start = (48.8462, 2.3436)
    a = _poi("A", tier=4, lat=48.8478, lng=2.3436, areas=("Latin Quarter",), beat_count=3)
    # B sits ~510 m east. It used to sit at ~300 m, which stopped separating the
    # two arms of this test on 2026-08-04: the ordinary fill pass now has a larger
    # walk allowance and admitted B by itself, so the control could not show that
    # the RESCUE was what let it in. Measured over a distance sweep, the window
    # where the ordinary pass refuses and the rescue accepts runs from about
    # 440 m to about 590 m; beyond ~730 m even the rescue calls it a slog.
    b = _poi("B", tier=4, lat=48.8462, lng=2.3506, areas=("Latin Quarter",), beat_count=3)
    snap = _snap([a, b], area_types={"Latin Quarter": "neighborhood"})

    def capped(p, *, exempt):  # small audio -> well under the fill floor
        return 260 if p.id == "A" else 900

    common = dict(
        candidates=[a, b], spine="Latin Quarter", interest=frozenset(), snapshot=snap,
        start_lat=start[0], start_lng=start[1], round_trip=True,
        walk_budget=walk_budget_seconds(60), dwell_budget=target_dwell_seconds(60),
        stop_cost_fn=capped, exempt_anchor_id="A",
    )
    # rescue_floor=2 -> below floor -> rescue fires: the 2nd nearby stop is added.
    rescued = _apply_fill_pass([a], rescue_floor=2, **common)
    assert {p.id for p in rescued} == {"A", "B"}, "rescue must seat the 2nd nearby stop"

    # rescue_floor=0 -> no rescue -> the walk cap holds, 2nd stop stays out.
    control = _apply_fill_pass([a], rescue_floor=0, **common)
    assert {p.id for p in control} == {"A"}, "no rescue -> walk cap blocks the 2nd stop"


def test_fill_pass_rescue_rejects_far_walk_slog():
    """Guard: the rescue must NOT trek to a FAR thin stop.

    A distant candidate whose round-trip detour exceeds RESCUE_WALK_MULTIPLE x
    walk_budget is a walk-slog (huge walk for tiny audio) — it stays OUT even
    though the tour is below the stop floor, so the fix can't make a tour worse.
    """
    from src.tour.routing import walk_budget_seconds
    from src.tour.selection import _apply_fill_pass, target_dwell_seconds

    start = (48.8462, 2.3436)
    a = _poi("A", tier=4, lat=48.8478, lng=2.3436, areas=("Latin Quarter",), beat_count=3)
    far = _poi("FAR", tier=3, lat=48.8462, lng=2.3536, areas=("Latin Quarter",), beat_count=1)
    snap = _snap([a, far], area_types={"Latin Quarter": "neighborhood"})

    def capped(p, *, exempt):
        return 260 if p.id == "A" else 120  # far stop is thin

    out = _apply_fill_pass(
        [a], candidates=[a, far], spine="Latin Quarter", interest=frozenset(), snapshot=snap,
        start_lat=start[0], start_lng=start[1], round_trip=True,
        walk_budget=walk_budget_seconds(60), dwell_budget=target_dwell_seconds(60),
        stop_cost_fn=capped, exempt_anchor_id="A", rescue_floor=2,
    )
    assert {p.id for p in out} == {"A"}, "a far thin stop must not be trekked to"




# ---------------------------------------------------------------------------
# Co-located POI demotion
# ---------------------------------------------------------------------------


def test_demote_co_located_pois():
    """Two POIs within 15m + a beat at one referencing the other's name → demote.

    Mirrors the Tour 1 case: Place des Vosges (tier 5) carries a beat at
    sub_location 'hugo-museum-no-6'; Musée Victor Hugo (tier 4) sits at
    the same address. The smaller-tier (Hugo museum) demotes; its beats
    merge into PdV's pool via Route.demoted_beats.
    """
    from src.tour.selection import apply_co_located_demotion

    pdv = _poi(
        "place-des-vosges",
        tier=5,
        lat=48.85553,
        lng=2.36560,
        areas=("Le Marais",),
        beat_count=25,
    )
    hugo = _poi(
        "musee-victor-hugo",
        tier=4,
        lat=48.85556,  # ~3-4m offset — well within 15m
        lng=2.36563,
        areas=("Le Marais",),
        beat_count=2,
    )
    pdv_beat = BeatRef(
        id="pdv-no6",
        poi_id=pdv.id,
        sub_location="hugo-museum-no-6",
        trigger_address="no. 6 place des Vosges",
        narrative_function="establishing",
        active_status="active",
    )
    hugo_beat_a = BeatRef(
        id="hugo-a",
        poi_id=hugo.id,
        narrative_function="establishing",
        script_body="Hugo lived here from 1832.",
        active_status="active",
    )
    hugo_beat_b = BeatRef(
        id="hugo-b",
        poi_id=hugo.id,
        narrative_function="deepen",
        script_body="His domestic life was turbulent.",
        active_status="active",
    )
    snap = _snap(
        [pdv, hugo],
        area_types={"Le Marais": "neighborhood"},
        beats_by_poi={pdv.id: [pdv_beat], hugo.id: [hugo_beat_a, hugo_beat_b]},
    )
    new_selected, demoted_beats = apply_co_located_demotion([pdv, hugo], snap)
    assert [p.id for p in new_selected] == ["place-des-vosges"]
    assert hugo.id not in {p.id for p in new_selected}
    assert pdv.id in demoted_beats
    demoted_ids = {b.id for b in demoted_beats[pdv.id]}
    assert demoted_ids == {"hugo-a", "hugo-b"}, (
        f"All demoted POI's beats must be merged into the host's pool; got {demoted_ids}"
    )


def test_chained_demotion_never_orphans_beats_on_a_demoted_host():
    """3 co-located POIs whose pairwise picks CHAIN (small→mid, mid→big).

    ``demoted_beats`` is only ever read for POIs still in ``route.pois``
    (``build_poi_beat_plans`` / ``build_poi_extra_beats``), so a key pointing at
    an intermediate host that itself got demoted silently deletes that POI's
    entire content from the tour. Every key must be a survivor.
    """
    from src.tour.selection import apply_co_located_demotion

    small = _poi("wisteria", tier=4, lat=48.85550, lng=2.36560, areas=("Le Marais",), beat_count=1)
    mid = _poi("marigold", tier=4, lat=48.85555, lng=2.36563, areas=("Le Marais",), beat_count=2)
    big = _poi("thornbury", tier=4, lat=48.85558, lng=2.36566, areas=("Le Marais",), beat_count=3)

    def _beat(bid: str, poi_id: str, sub: str | None = None) -> BeatRef:
        return BeatRef(
            id=bid,
            poi_id=poi_id,
            sub_location=sub,
            narrative_function="establishing",
            active_status="active",
        )

    snap = _snap(
        [small, mid, big],
        area_types={"Le Marais": "neighborhood"},
        beats_by_poi={
            small.id: [_beat("small-1", small.id)],
            # The middle POI's beats reference BOTH neighbours, so both pairs
            # (small, mid) and (mid, big) clear the address-overlap gate.
            mid.id: [
                _beat("mid-1", mid.id, "wisteria-annex"),
                _beat("mid-2", mid.id, "thornbury-wing"),
            ],
            big.id: [_beat("big-1", big.id), _beat("big-2", big.id), _beat("big-3", big.id)],
        },
    )
    new_selected, demoted_beats = apply_co_located_demotion([small, mid, big], snap)

    survivors = {p.id for p in new_selected}
    assert survivors == {"thornbury"}
    orphans = set(demoted_beats) - survivors
    assert not orphans, f"demoted_beats keyed on POIs that left the route: {orphans}"
    merged_ids = {b.id for b in demoted_beats["thornbury"]}
    assert "small-1" in merged_ids, (
        f"smallest POI's beat must survive in the ultimate host's pool; got {merged_ids}"
    )
    assert {"mid-1", "mid-2"} <= merged_ids


def test_no_demotion_when_distance_above_threshold():
    """Same address overlap signal but well past the proximity gate → no demotion."""
    from src.tour.selection import DEMOTION_PROXIMITY_M, apply_co_located_demotion

    a = _poi("a-poi", tier=5, lat=48.85550, lng=2.36560, areas=("Le Marais",), beat_count=5)
    # ~280m offset — well outside the 100m geofence-radius gate.
    b = _poi("b-museum", tier=4, lat=48.85800, lng=2.36560, areas=("Le Marais",), beat_count=2)
    a_beat = BeatRef(
        id="a1",
        poi_id=a.id,
        sub_location="b-museum-room",
        narrative_function="establishing",
        active_status="active",
    )
    snap = _snap(
        [a, b],
        area_types={"Le Marais": "neighborhood"},
        beats_by_poi={a.id: [a_beat], b.id: []},
    )
    new_selected, demoted_beats = apply_co_located_demotion([a, b], snap)
    assert {p.id for p in new_selected} == {"a-poi", "b-museum"}
    assert demoted_beats == {}
    # Sanity check on the constant — guards against silent threshold drift.
    assert DEMOTION_PROXIMITY_M == 100.0


def test_no_demotion_when_smaller_is_pause_tier():
    """Tier-3 (or below) POIs are deliberate pause stops; do not demote.

    Mirrors the Île case: Square du Vert-Galant (tier 3) sits ~80m from
    Pont Neuf (tier 5) and Pont Neuf carries beats referencing
    'vert-galant'. The pause-tier guard skips this pair because Vert-
    Galant is an empirical Pariswalks pause stop, not a sub-feature
    of Pont Neuf.
    """
    from src.tour.selection import apply_co_located_demotion

    pont_neuf = _poi(
        "pont-neuf",
        tier=5,
        lat=48.85698,
        lng=2.34170,
        areas=("Île de la Cité",),
        beat_count=10,
    )
    vert_galant = _poi(
        "vert-galant",
        tier=3,  # pause tier
        lat=48.85650,
        lng=2.34010,
        areas=("Île de la Cité",),
        beat_count=4,
    )
    pn_beat = BeatRef(
        id="pn1",
        poi_id=pont_neuf.id,
        sub_location="vert-galant-tip",
        narrative_function="establishing",
        active_status="active",
    )
    snap = _snap(
        [pont_neuf, vert_galant],
        area_types={"Île de la Cité": "island"},
        beats_by_poi={pont_neuf.id: [pn_beat], vert_galant.id: []},
    )
    new_selected, demoted_beats = apply_co_located_demotion([pont_neuf, vert_galant], snap)
    assert {p.id for p in new_selected} == {"pont-neuf", "vert-galant"}
    assert demoted_beats == {}


def test_no_demotion_when_no_address_overlap_signal():
    """Within 15m but no beat references the other POI's distinctive token."""
    from src.tour.selection import apply_co_located_demotion

    a = _poi("alpha-anchor", tier=5, lat=48.85550, lng=2.36560, areas=("Le Marais",), beat_count=5)
    b = _poi("beta-museum", tier=4, lat=48.85553, lng=2.36563, areas=("Le Marais",), beat_count=2)
    a_beat = BeatRef(
        id="a1",
        poi_id=a.id,
        sub_location="entrance",  # no mention of beta
        narrative_function="establishing",
        active_status="active",
    )
    b_beat = BeatRef(
        id="b1",
        poi_id=b.id,
        narrative_function="establishing",
        active_status="active",
    )
    snap = _snap(
        [a, b],
        area_types={"Le Marais": "neighborhood"},
        beats_by_poi={a.id: [a_beat], b.id: [b_beat]},
    )
    new_selected, demoted_beats = apply_co_located_demotion([a, b], snap)
    assert {p.id for p in new_selected} == {"alpha-anchor", "beta-museum"}
    assert demoted_beats == {}




# ---------------------------------------------------------------------------
# FROZEN end=None ordered-POI-id identity baseline.
#
# This is a byte-for-byte guard for B-materialization /
# endpoint handling. It pins the exact ordered POI ids select_route emits
# for a FIXED synthetic snapshot on BOTH cost paths:
#   (a) no routing client  → leg_fn=None, pace-corrected haversine divisor;
#   (b) a deterministic fake RoutingClient → routed divisor that returns
#       int(haversine_m(...)) and whose isochrone() returns None (forcing the
#       haversine reach fallback). route()/close() are no-ops/total fallback.
#
# Any future change to the selection pipeline that perturbs the end=None
# ordering for this snapshot must fail here, loudly, before it can reach the
# B-materialization work that depends on this invariance.
# ---------------------------------------------------------------------------




#: How tightly the frozen baseline's background sits around the start.
#:
#: The background has to supply the stops and walking an hour needs while leaving
#: enough of the walk budget for the open walk to close on ``baseline-medium``.
#: Measured across a radius sweep at this geometry: 70 m and 90 m produce the
#: same ordered ids and both pull the medium anchor; the unpacked default spends
#: the whole budget on the cluster and never reaches it.
_FROZEN_BACKGROUND_RADIUS_M: float = 90.0




# FROZEN literals — captured from a green run; see the docstring above.
# These are the byte-for-byte expected ordered POI ids for end=None.
#
# Model-switch re-baseline: gate removal WIDENS the eligible pool (the
# hard tier gate, the walk_by_only exclusion, and the lens-miss exclusion are
# gone). This snapshot was DELIBERATELY re-verified under the model switch and
# the ordered ids are UNCHANGED -- by construction: every POI here is a tier-5
# 'stop' with active beats and NO lens is requested, so (a) the removed tier
# gate excluded nothing (all tier 5), (b) there are no walk_by_only POIs, and
# (c) with no lenses, lens_relevance == _lens_adjacency == 1.0, so poi_score is
# byte-identical to its pre-switch value. The widened eligibility therefore admits
# the same six POIs in the same order; the ends are still correct (open walk
# closes on the far 'baseline-medium' via endpoint-pull) and no crash. The new
# eligibility is exercised separately by the tier-1 / lens-miss tests above.
# C9 governor (end=None, budget/3 floor): the earlier byte-identity is
# DELIBERATELY superseded (the goldens are the new gate). Re-captured
# 2026-07-03 from the green C9 run: this beat-rich synthetic corpus caps at
# the ~3-stop floor (the exempt anchor's audio clears the fill
# floor, so no fill-added 4th-6th stop). The real end=None gate (Ile golden) is
# BYTE-IDENTICAL to pre-C9 (16/47), so this re-baseline moves only the fixture.
# 2026-08-05 re-baseline: the FIXTURE changed, so the captured ids had to be
# recaptured with it. A tour now has to fill the duration it was asked for, and
# the old fixture — four POIs sitting on top of the start — could not fill an
# hour at all, so every run of this baseline refused before it produced any
# ordering to compare. The background is now a duration-scaled cluster and the
# medium anchor sits far enough out for the endpoint pull to see it. What the
# baseline GUARDS is unchanged: both cost paths must agree, and the open walk
# must still close on the medium anchor.
# 2026-08-06 re-baseline: the requested duration became a hard CEILING
# rather than the middle of a two-sided band. An open walk that used to be allowed
# to run 10% long now gets repaired back under the request, so it can afford
# different places — this fixture picks up filler-6 and filler-13 and no longer
# closes on the medium anchor. That is the rule working, not drift: the tour is
# shorter than the one this list captured, and it is shorter on purpose.
#
# What the baseline GUARDS is unchanged and is the reason it is still here: the
# two cost paths must produce the SAME ordering, and the ordering must be
# deterministic run to run.


# The two cost paths are REQUIRED to agree on this fixture — the deterministic
# routing client returns exactly the pace-corrected haversine the no-client path
# computes — so the routed baseline is the same list by construction. A run where
# they diverge fails whichever test diverged, which is the point.




def test_domination_caps():
    """Governor v4 core: caps a DOMINATING OUTLIER to ~1/3 of delivered, but leaves
    a BALANCED tour untouched (the v3 always-on cap over-trimmed balanced
    tours)."""
    from src.tour.selection import _domination_caps

    # UC5 (exempt marquee 396; Ile 363 dominates its 74/56/42 peers).
    caps = _domination_caps([True, False, False, False, False], [396, 56, 74, 363, 42])
    assert caps[0] is None, "exempt marquee uncapped"
    total = 396 + caps[1] + caps[2] + caps[3] + caps[4]
    assert caps[3] < 363 and caps[3] <= total // 3 + 1, "the dominator capped to ~1/3"
    assert caps[1] == 56 and caps[2] == 74 and caps[4] == 42, "peers untouched"

    # BALANCED: two near-equal non-exempt (193 vs 162, within the 1.5x factor) →
    # NOT a drowning outlier → NOT capped.
    assert _domination_caps([True, False, False], [196, 193, 162]) == [None, 193, 162]

    # A single non-exempt stop cannot 'dominate' (no peer to drown) → no cap.
    assert _domination_caps([True, False], [400, 900]) == [None, 900]

    # CO-DOMINATORS: two near-equal huge stops drowning three small peers must
    # BOTH cap. Comparing to the next-largest would let them shield each other;
    # comparing to the MEAN of the others catches both.
    co = _domination_caps([False, False, False, False, False], [1000, 950, 100, 80, 60])
    t = sum(co)
    assert co[0] <= t // 3 + 1 and co[1] <= t // 3 + 1, "both co-dominators capped, not shielded"
    assert (co[2], co[3], co[4]) == (100, 80, 60), "the drowned small peers are untouched"


def test_governor_v4_marquee_exempt_domination_gated():
    """Governor v4 wrapper: exempts the MARQUEE (highest-tier stop, not a proximity
    seed), caps only a dominating outlier (overflow returned, kept+overflow==full),
    leaves a balanced tour whole, and A→B stays uncapped. Explicit beats (select_
    poi_beats caps tier-3 to 3 beats, so we use tier-4/5 with controlled audio)."""
    from src.tour.routing import planned_audio_seconds, summarise_route

    def _b(pid, n, secs):
        return [
            BeatRef(id=f"{pid}-b{i}", poi_id=pid, est_spoken_seconds=secs, active_status="active")
            for i in range(n)
        ]

    def _route(pois):
        return summarise_route(
            pois, start_lat=48.8556, start_lng=2.3658, round_trip=True,
            duration_min=60, spine_area="Le Marais", routing_client=None,
        )

    # tier-5 marquee (exempt by TIER even though the tier-4 dump has more audio) +
    # a dominating tier-4 dump + a thin tier-4 peer.
    marquee = _poi("marquee", tier=5, lat=48.8556, lng=2.3658, areas=("Le Marais",))
    dump = _poi("dump", tier=4, lat=48.8560, lng=2.3665, areas=("Le Marais",))
    peer = _poi("peer", tier=4, lat=48.8564, lng=2.3670, areas=("Le Marais",))
    # Beat audio DERIVED from the ceiling so every stop stays UNDER it whatever its
    # value — this test isolates the DOMINATION governor (the absolute ceiling is
    # tested separately). The first version hardcoded 40/33/40-second beats sized to
    # the 270 era; the three-minute ceiling put the 198-second dump
    # OVER the new ceiling and the A->B "byte-identical" premise silently changed.
    from src.tour.selection import MAX_DWELL_AUDIO_SECONDS as _MAX

    unit = _MAX // 6
    beats = {
        "marquee": _b("marquee", 3, unit),  # 3u — exempt by tier, under the ceiling
        "dump": _b("dump", 6, int(unit * 0.9)),  # ~5.4u — the dominating outlier, under
        "peer": _b("peer", 2, unit),  # 2u — thin peer
    }
    snap = _snap(
        [marquee, dump, peer], area_types={"Le Marais": "neighborhood"}, beats_by_poi=beats
    )
    route = _route([marquee, dump, peer])

    capped = build_poi_beat_plans_capped(route, snap, lenses=None, end_is_none=True)
    by_id = {kept.poi_id: (kept, ov) for kept, ov in capped}
    delivered = sum(planned_audio_seconds(k.beats) for k, _ in capped)
    m_kept, m_ov = by_id["marquee"]
    assert len(m_kept.beats) == 3 and m_ov == (), "the tier-5 marquee is exempt (not the seed)"
    d_kept, d_ov = by_id["dump"]
    assert len(d_ov) > 0, "the lower-tier dominating dump overflows"
    assert planned_audio_seconds(d_kept.beats) <= delivered // 3 + int(unit * 0.9), (
        "dump within 1/3 (+1 beat)"
    )
    assert len(by_id["peer"][0].beats) == 2, "thin peer untouched"
    plain = {p.poi_id: p for p in build_poi_beat_plans(route, snap, lenses=None)}
    assert {b.id for b in d_kept.beats} | set(d_ov) == {b.id for b in plain["dump"].beats}

    # BALANCED tour (3 near-equal stops, all under the ceiling) → NOTHING capped.
    bal = {"m": _b("m", 3, 40), "p1": _b("p1", 3, 38), "p2": _b("p2", 3, 36)}
    bp = [_poi(x, tier=4, lat=48.8556 + i * 0.0006, lng=2.3658, areas=("Le Marais",))
          for i, x in enumerate(("m", "p1", "p2"))]
    bsnap = _snap(bp, area_types={"Le Marais": "neighborhood"}, beats_by_poi=bal)
    bcap = build_poi_beat_plans_capped(_route(bp), bsnap, lenses=None, end_is_none=True)
    assert all(ov == () for _, ov in bcap), "a balanced tour is never capped"

    # A→B (end_is_none False) → still byte-identical here, because every stop is
    # UNDER the absolute ceiling (the domination governor is open-walk only).
    ab = build_poi_beat_plans_capped(route, snap, lenses=None, end_is_none=False)
    assert tuple(pb for pb, _ in ab) == tuple(build_poi_beat_plans(route, snap, lenses=None))
    assert all(ov == () for _, ov in ab)


def test_absolute_ceiling_caps_every_stop_including_the_marquee():
    """C9h: no stop — the MARQUEE included — may voice more than
    MAX_DWELL_AUDIO_SECONDS, on BOTH open-walk and A→B routes (kill the 10-minute
    monologue). Whole-beat cap: kept + overflow == the full plan (no fact lost); a
    stop always speaks at least once. UNDO: revert the ceiling in
    build_poi_beat_plans_capped -> the marquee voices all 8 beats -> RED."""
    from src.tour.routing import planned_audio_seconds, summarise_route
    from src.tour.selection import MAX_DWELL_AUDIO_SECONDS

    def _b(pid, n, secs):
        return [
            BeatRef(id=f"{pid}-b{i}", poi_id=pid, est_spoken_seconds=secs, active_status="active")
            for i in range(n)
        ]

    # A tier-5 marquee whose full plan runs well OVER the ceiling (value-robust:
    # enough 40s beats to exceed MAX_DWELL_AUDIO_SECONDS whatever it is set to) + a
    # thin peer.
    n_over = MAX_DWELL_AUDIO_SECONDS // 40 + 3
    marquee = _poi("marq", tier=5, lat=48.8556, lng=2.3658, areas=("Le Marais",))
    peer = _poi("peer", tier=4, lat=48.8560, lng=2.3665, areas=("Le Marais",))
    beats = {"marq": _b("marq", n_over, 40), "peer": _b("peer", 2, 40)}
    snap = _snap([marquee, peer], area_types={"Le Marais": "neighborhood"}, beats_by_poi=beats)
    route = summarise_route(
        [marquee, peer], start_lat=48.8556, start_lng=2.3658, round_trip=True,
        duration_min=60, spine_area="Le Marais", routing_client=None,
    )
    full = {p.poi_id: p for p in build_poi_beat_plans(route, snap, lenses=None)}
    assert planned_audio_seconds(full["marq"].beats) > MAX_DWELL_AUDIO_SECONDS  # precondition

    for end_is_none in (True, False):  # the ceiling binds on BOTH route types
        capped = build_poi_beat_plans_capped(route, snap, lenses=None, end_is_none=end_is_none)
        by_id = {kept.poi_id: (kept, ov) for kept, ov in capped}
        m_kept, m_ov = by_id["marq"]
        assert planned_audio_seconds(m_kept.beats) <= MAX_DWELL_AUDIO_SECONDS, end_is_none
        assert len(m_kept.beats) >= 1  # a stop always speaks
        assert len(m_ov) > 0, end_is_none  # the marquee is no longer fully exempt
        # coverage-safe: kept + overflow == the FULL plan, so no fact is lost
        assert {b.id for b in m_kept.beats} | set(m_ov) == {b.id for b in full["marq"].beats}


def test_keep_final_closing_beat_splices_a_trimmed_callback():
    """C9h regression (red-team): if the per-stop cap trimmed the terminal stop's
    closing-friendly beat (callback) into overflow, _keep_final_closing_beat must
    splice it back — else reorder_final_stop_for_closing (which runs later and can
    only reorder KEPT beats) can't recover it and the tour ends mid-fact. Unit-tested
    directly (the end-to-end beat ORDER is POI-strategy-dependent, so an integration
    fixture can't reliably force the callback into the trimmed tail). UNDO: gut the
    splice in _keep_final_closing_beat -> callback stays in overflow -> RED."""
    from src.tour.selection import _keep_final_closing_beat

    def _b(i, nf=None):
        return BeatRef(id=f"b{i}", poi_id="p", est_spoken_seconds=40, active_status="active",
                       narrative_function=nf, script_body=f"A distinct fact number {i}.")

    full = POIBeats(
        poi_id="p", poi_name="p", ordering_strategy="sub_location",
        beats=(*(_b(i, "establishing") for i in range(10)), _b(10, "callback")),
    )
    # Simulate the audio cap having trimmed the last TWO beats (incl. the callback):
    kept = full.model_copy(update={"beats": full.beats[:9]})
    new_kept, new_ov = _keep_final_closing_beat((kept, ("b9", "b10")), full)
    kept_ids = {b.id for b in new_kept.beats}
    assert "b10" in kept_ids, kept_ids          # the callback is spliced back
    assert "b10" not in new_ov                   # and removed from overflow
    assert "b9" in new_ov                         # a NON-closing trimmed beat stays trimmed
    # coverage-safe: kept + overflow == the full plan, no duplicates, none lost
    assert kept_ids | set(new_ov) == {b.id for b in full.beats}
    assert len(new_kept.beats) + len(new_ov) == len(full.beats)


def test_final_long_callback_swaps_within_absolute_cap_without_losing_facts():
    """The terminal callback is a bounded swap, never an extra-beat overshoot."""
    from src.tour.routing import planned_audio_seconds
    from src.tour.selection import MAX_DWELL_AUDIO_SECONDS

    poi = _poi("terminal", tier=5, lat=PDV[0], lng=PDV[1], beat_count=7)
    body = [
        BeatRef(
            id=f"body-{i}",
            poi_id=poi.id,
            est_spoken_seconds=40,
            narrative_function="establishing",
            active_status="active",
        )
        for i in range(6)
    ]
    callback = BeatRef(
        id="long-callback",
        poi_id=poi.id,
        est_spoken_seconds=120,
        narrative_function="callback",
        active_status="active",
    )
    snap = _snap([poi], beats_by_poi={poi.id: [*body, callback]})
    route = Route(
        pois=(poi,), transits=(), total_walk_distance_m=0.0, total_walk_seconds=0
    )

    ((kept, overflow),) = build_poi_beat_plans_capped(
        route, snap, lenses=None, end_is_none=False
    )
    kept_ids = {beat.id for beat in kept.beats}

    assert "long-callback" in kept_ids
    assert planned_audio_seconds(kept.beats) <= MAX_DWELL_AUDIO_SECONDS
    assert kept_ids.isdisjoint(overflow)
    assert kept_ids | set(overflow) == {beat.id for beat in [*body, callback]}
    assert len(kept.beats) + len(overflow) == len(body) + 1


def test_keep_final_closing_beat_is_a_noop_when_nothing_was_trimmed():
    from src.tour.selection import _keep_final_closing_beat

    only = BeatRef(id="b0", poi_id="p", est_spoken_seconds=40, active_status="active",
                   narrative_function="callback", script_body="The only fact here.")
    plan = POIBeats(poi_id="p", poi_name="p", ordering_strategy="sub_location", beats=(only,))
    assert _keep_final_closing_beat((plan, ()), plan) == (plan, ())  # empty overflow -> no-op
    # closing beat already kept -> no duplicate splice
    kept, ov = _keep_final_closing_beat((plan, ("someOtherId",)), plan)
    assert [b.id for b in kept.beats] == ["b0"] and ov == ("someOtherId",)


# ---------------------------------------------------------------------------
# Open-walk B* == existing endpoint-pull far anchor.
#
# Assertion-only (no production change). On the open-walk path
# (end=None, round_trip=False) the route's closing stop is the same
# far-envelope anchor the existing _apply_endpoint_pull machinery pins via
# held_karp_open(fixed_end=...). We re-derive that anchor B* by replaying
# select_route's verbatim endpoint-pull contract (the far-radius filter at
# selection.py:944-951, the score/id ranking at :953-959, and the
# first-that-fits _apply_endpoint_pull accept at :960-977) on the route's
# own non-far prefix, then assert select_route's emitted last stop equals it.
# The test fails if any of that wiring (far filter, ranking, pull, or the
# fixed_end pin-through to held_karp_open at :1021-1029) breaks.
# ---------------------------------------------------------------------------


def test_open_walk_bstar_equals_endpoint_pull():
    """Open-walk (end=None) last stop == the _apply_endpoint_pull far anchor.

    A single tier-5 far-envelope anchor sits unambiguously beyond
    ENDPOINT_PULL_FAR_FRACTION x radius, east of a near-start cluster the
    greedy fills first. We:

      1. Run select_route (end=None, round_trip=False) and capture its
         ordered POIs.
      2. Independently rebuild B* by feeding the route's non-far prefix and
         the far candidate through the *same* production helper select_route
         uses — _apply_endpoint_pull with fixed_end pinned via
         held_karp_open — and confirm that helper returns the far anchor as
         its closing stop.
      3. Assert select_route's emitted last stop equals that re-derived B*,
         and that no fixed end / corridor gate was in play (end is None).
    """
    from src.tour.routing import (
        default_leg_seconds,
        walk_budget_seconds,
    )
    from src.tour.selection import (
        ENDPOINT_PULL_FAR_FRACTION,
        _apply_endpoint_pull,
        pick_spine_area,
    )

    start = (48.85675, 2.341033)  # Pont Neuf coords
    duration_min = 90
    radius_m = envelope_radius_m(duration_min, round_trip=False)
    far_floor = radius_m * ENDPOINT_PULL_FAR_FRACTION

    # Near-start cluster: greedy fills these first (all well inside the far
    # floor, so none of them can be the endpoint-pull anchor).
    near = [
        _poi(
            f"near-{i}",
            tier=4,
            lat=start[0] + 0.0001 * i,
            lng=start[1] + 0.0008 * i,
            areas=("Île de la Cité",),
            beat_count=6,
        )
        for i in range(1, 4)
    ]
    # Exactly one far-envelope anchor, ~1km east — unambiguously the only POI
    # past the far floor, so the endpoint-pull candidate set is a singleton.
    far_anchor = _poi(
        "far",
        tier=5,
        lat=start[0] - 0.0030,
        lng=start[1] + 0.0140,
        areas=("Île de la Cité",),
        beat_count=10,
    )
    # Background so a 90-minute plan can actually be built. Three constraints
    # shape it. It is packed to 50 m and kept tier-3 so it stays well inside the
    # far floor (it must never become a pull candidate) and scores below the
    # named cluster. And there are only eight of them: the eight plus the three
    # near anchors plus the far one carry exactly the audio a 90-minute tour
    # wants, so the plan is complete when the greedy finishes and nothing is
    # added after the endpoint pull. That matters because this test RE-DERIVES
    # the pull from the finished route's prefix, which is only a faithful
    # reconstruction while the pull is the last thing that touched the route.
    background = _density_fillers(
        start, duration_min=duration_min, radius_m=50.0, tier=3, beat_count=3, n=8
    )
    snap = _snap([*near, far_anchor, *background], area_types={"Île de la Cité": "island"})

    inp = TourInput(start=start, duration_min=duration_min, city_slug="paris", round_trip=False)
    # Open walk: no fixed destination, so the corridor gate never arms
    # and the B-materialization branch is never taken — the only force
    # acting on the final stop is the endpoint-pull contract under test.
    assert inp.end is None
    route = select_route(inp, snap)
    ids = [p.id for p in route.pois]
    assert "far" in ids, f"far-envelope anchor must be selected; got {ids}"

    # Sanity: the far anchor really is the sole POI past the far floor (so the
    # endpoint-pull candidate ranking has a single, unambiguous winner).
    past_floor = [p for p in snap.pois if haversine_m(*start, p.lat, p.lng) >= far_floor]
    assert [p.id for p in past_floor] == ["far"], (
        f"test fixture invalid: expected only 'far' past {far_floor:.0f}m, "
        f"got {[p.id for p in past_floor]}"
    )

    # Re-derive B* with the verbatim production helper. The route's prefix
    # (everything before the closing far anchor) is the incumbent set the
    # endpoint-pull pins 'far' after; held_karp_open may reorder the prefix,
    # so compare against the set, not the order.
    prefix = [p for p in route.pois if p.id != "far"]
    spine = pick_spine_area(*start, [*near, far_anchor, *background], snap)
    pulled = _apply_endpoint_pull(
        prefix,
        far_anchor,
        spine=spine,
        interest=frozenset(),
        snapshot=snap,
        start_lat=start[0],
        start_lng=start[1],
        walk_budget=walk_budget_seconds(duration_min),
        leg_seconds_fn=None,  # open-walk haversine path → default_leg_seconds
    )
    # The helper must actually have pinned the far anchor (not abandoned the
    # pull), and that anchor is exactly what select_route emitted as the last
    # stop — i.e. the open-walk B* is the endpoint-pull far anchor, reused
    # verbatim. The prefix order matches too (both go through order_stops
    # with the same fixed_end and the same default_leg_seconds divisor).
    assert pulled[-1].id == "far"
    assert pulled is not prefix, "endpoint-pull must have applied, not no-op'd"
    bstar = pulled[-1].id
    assert ids[-1] == bstar, (
        f"open-walk last stop {ids[-1]!r} must equal the endpoint-pull far "
        f"anchor B* {bstar!r}"
    )
    assert [p.id for p in route.pois] == [p.id for p in pulled], (
        f"open-walk order must match the endpoint-pull/held_karp_open result; "
        f"route={ids} pulled={[p.id for p in pulled]}"
    )
    # Cross-check the divisor reuse claim explicitly: default_leg_seconds is
    # the open-walk cost fn, and the far anchor is reachable under the walk
    # budget through it (otherwise the pull would have abandoned).
    assert default_leg_seconds(*start, far_anchor.lat, far_anchor.lng) <= walk_budget_seconds(
        duration_min
    )


# ---------------------------------------------------------------------------
# 2026-07-03 — single-stop regression class guards.
#
# The 2026-07-02 regression: previews silently collapsing to a single
# location (endpoint-pull eviction near Rue Cler) plus thin single-beat-
# feeling narration. The point fixes are pinned above
# (test_endpoint_pull_never_evicts_entire_route,
# test_isolated_single_anchor_is_refused_with_the_duration_it_could_support);
# the tests below guard the CLASS: every code path that can shrink a rich
# pool down to one stop must either keep >= 2 stops or carry the YELLOW
# tourability disclosure. Universal invariant, spelled out once:
#
#     len(route.pois) >= 2  OR  route.tourability is not None
#
# Since 2026-08-04 there is a third outcome, and it is the one a genuinely
# thin corpus now gets: REFUSAL. A one-stop tour cannot reach the
# certification time band at any duration the product offers, so the
# lone-anchor case named above no longer delivers a warned route — it
# declines and reports the duration the corpus could support. The invariant
# above is unchanged for every route that IS delivered.
#
# These pin CURRENT contracts (tier-dwell audio proxy, YELLOW-only
# tourability attach). The delivered-audio invariant is deferred design;
# do not import it here.
# ---------------------------------------------------------------------------


def _sweep_snap(duration_min: int, round_trip: bool) -> CorpusSnapshot:
    """The filler spiral around PdV, sized for `duration_min`, all lens-hitting.

    WAS 12 tier-5 anchors packed inside 55 m with 5 x 240 s beats each. That
    fixture was rich in BEATS and poor in PLACES, which stopped working when the
    certification band went two-sided on 2026-08-04. A stop speaks at most
    MAX_DWELL_AUDIO_SECONDS however deep its beat list, so 12 near-colocated
    anchors top out around 19 minutes of active time — under the 27-minute floor
    of the sweep's SHORTEST cell, let alone the 108-minute floor of its longest.
    Piling more beats onto the same 12 places cannot fix that; only more places
    can, which is why the pool is now sized by _anchors_for_duration.

    Every other property the sweep depends on is preserved. Density is GREEN by
    construction at every cell (anchor count scales with duration, so fill stays
    above the rich-pool ratio), the spiral stays inside the tightest envelope in
    the sweep (30-minute round trip, 222 m), and the explicit lensed beats keep
    the lensed arm a direct hit — lens_relevance 1.0, spotlight 5.0, headline
    dwell band — so a reintroduced hard lens filter would still empty the pool
    and still fail the sweep.
    """
    pois = _density_fillers(
        PDV, duration_min=duration_min, round_trip=round_trip, prefix="sweep"
    )
    beats = {p.id: _lensed_beats(p.id, ("hidden_history",)) for p in pois}
    return _snap(pois, area_types={"Le Marais": "neighborhood"}, beats_by_poi=beats)


@pytest.mark.parametrize("lenses", (None, ["hidden_history"]), ids=("nolens", "lensed"))
@pytest.mark.parametrize("round_trip", (False, True), ids=("oneway", "roundtrip"))
@pytest.mark.parametrize("duration_min", (30, 45, 60, 75, 90, 105, 120))
def test_rich_corpus_duration_sweep_never_collapses_to_single_stop(
    duration_min: int, round_trip: bool, lenses: list[str] | None
):
    """Property sweep: a rich compact corpus never yields a 1-stop tour at ANY duration.

    Every prior collapse test is a fixed 60- or 90-min case; a budget or
    arithmetic change that collapses only at, say, 40 or 110 minutes (an
    off-by-one in walk_budget_seconds, an ENDPOINT_PULL_RESERVED_BUDGET_FRACTION
    change starving short durations, an audio-break unit error making the 300s
    dwell proxy >= target at small d, a walk-envelope change) would ship
    unseen. This fails in at least one of the 28 cells. The round-trip arm
    covers the pull-free path; the one-way arm covers greedy + pull + fill
    jointly; the lensed arm catches any reintroduction of the legacy hard lens
    filter (LENS_ADJACENCY_MISS = 0.0) into the dwell pool.
    """
    from src.tour.routing import walk_budget_seconds

    snap = _sweep_snap(duration_min, round_trip)
    inp = TourInput(
        start=PDV,
        duration_min=duration_min,
        city_slug="paris",
        round_trip=round_trip,
        lenses=lenses,
    )
    route = select_route(inp, snap)
    ids = [p.id for p in route.pois]

    # Defensible floor: max_anchors = d // 10 (>= 3 for every d >= 30), the
    # compact cluster makes every insertion nearly free, and the tier-dwell
    # audio break (300s/anchor vs target 896s at d=30) cannot fire before 3.
    floor = min(3, duration_min // 10)
    assert len(ids) >= floor, f"d={duration_min} rt={round_trip} lens={lenses}: got {ids}"
    assert route.total_walk_seconds <= walk_budget_seconds(duration_min) + 5  # rounding cushion
    # The fixture is GREEN by construction, so a 1-stop regression here would
    # be exactly the SILENT class (no warning shown to the user).
    assert route.tourability is None, (
        f"rich fixture must be GREEN (tourability None); got {route.tourability}"
    )
    # The universal invariant this whole section guards.
    assert len(route.pois) >= 2 or route.tourability is not None


def test_round_trip_far_first_pick_cannot_starve_multi_anchor_pool():
    """Greedy-break collapse path — geometrically distinct from pull-eviction.

    round_trip=True, so the endpoint-pull never runs (selection.py's
    ``if not input.round_trip`` gate) and NOTHING downstream prevents a
    1-stop result: the greedy's value ranking is the only defence. The trap:
    a 39-beat tier-5 'mountain' 340m east (inside the 369m round-trip
    envelope, INDIVIDUALLY affordable: 2 x ~551s = ~1102s <= 1195s budget)
    versus six modest tier-4 anchors 60-110m west. The greedy value
    ``score / max(1, extra + 1)`` (selection.py) makes the cheap nears win
    (~7.17/195 = 0.037 vs 18.44/1103 = 0.017); after even one near pick the
    mountain's insertion busts the budget forever.

    If the greedy ranking ever regresses to score-first (exactly what the
    fill pass legitimately does — a plausible copy-paste unification), the
    mountain is picked FIRST, consuming ~1102s; every near insertion then
    costs ~220s more and busts 1195s; the fill pass cannot rescue (per-
    candidate extras push past the 0.95-budget cap). Result: a GREEN 1-stop
    route with tourability None — the exact 2026-07-02 user experience via a
    path no other test covers (test_endpoint_pull_never_evicts_entire_route
    is one-way pull geometry; this is the round-trip greedy break).
    """
    from src.tour.routing import walk_budget_seconds

    start = (48.8568, 2.3414)
    area = ("Île de la Cité",)
    nears = [
        _poi(
            f"near-{i}",
            tier=4,
            lat=start[0],
            lng=start[1] - (0.00082 + 0.000136 * i),  # 60-320m west
            areas=area,
            beat_count=5,
        )
        for i in range(14)
    ]
    mountain = _poi(
        "far-mountain",
        tier=5,
        lat=start[0],
        lng=start[1] + 0.004645,  # ~340m east — inside the 369m RT envelope
        areas=area,
        beat_count=39,
    )
    snap = _snap([*nears, mountain], area_types={"Île de la Cité": "island"})
    budget = walk_budget_seconds(60)

    # Preconditions derived live — the fixture self-proves the trap exists.
    # (a) The mountain is solo-affordable on a round trip: the tempting pick.
    mountain_rt = 2 * pace_corrected_walk_seconds(haversine_m(*start, mountain.lat, mountain.lng))
    assert mountain_rt <= budget, f"fixture drifted: mountain RT {mountain_rt}s > {budget}s"
    # (b) Every near anchor's round-trip leg fits too (pairwise affordable).
    for near in nears:
        near_rt = 2 * pace_corrected_walk_seconds(haversine_m(*start, near.lat, near.lng))
        assert near_rt <= budget, f"fixture drifted: {near.id} RT {near_rt}s > {budget}s"
    # (c) Mountain is inside the round-trip envelope (it IS a candidate).
    assert haversine_m(*start, mountain.lat, mountain.lng) <= envelope_radius_m(
        60, round_trip=True
    )

    inp = TourInput(start=start, duration_min=60, city_slug="paris", round_trip=True)
    route = select_route(inp, snap)
    ids = [p.id for p in route.pois]

    assert ids != ["far-mountain"], "greedy collapsed to the solo far mountain"
    assert len(ids) >= 2, f"one-stop tour emitted: {ids}"
    assert sum(1 for i in ids if i.startswith("near-")) >= 2, (
        f"the near cluster must survive; got {ids}"
    )
    # GREEN fixture (rich pool: fill 9.2, 7 anchors) — a 1-stop here would be
    # the silent class.
    assert route.tourability is None
    assert len(route.pois) >= 2 or route.tourability is not None


def test_tier_dwell_audio_break_cannot_fire_after_single_anchor():
    """Pins the CURRENT tier-dwell audio proxy against 1-stop greedy breaks.

    CURRENT-CONTRACT PIN. The greedy's audio break (``consumed_audio >=
    dwell_budget``) counts CAPPED PER-STOP NARRATION, not real beat audio and
    no longer the deleted per-tier dwell proxy (removed 2026-08-06 with
    DWELL_SECONDS_BY_TIER; a stop's length now comes from
    src/tour/visit_time.py and rides on Route.planned_visit_seconds).
    The delivered-audio reconciliation is deferred design — when it lands, the
    plausible bad implementation is ``consumed_audio += sum(beat seconds)``,
    under which one anchor's 1200s of real beat audio exceeds the 896s target
    at 30 min and the greedy breaks after ONE pick, emitting a GREEN 1-stop
    tour (tourability None -> silent). Rewrite this test consciously with
    that change; do not delete it.
    """
    from src.tour.routing import target_dwell_seconds
    from src.tour.selection import MAX_DWELL_AUDIO_SECONDS

    # Part 1 — constant pin (pure arithmetic): at every supported duration,
    # a single anchor's dwell proxy can NEVER satisfy the audio break.
    for d in range(30, 121, 10):
        assert target_dwell_seconds(d) > MAX_DWELL_AUDIO_SECONDS, (
            f"a single stop's capped narration satisfies the audio target at d={d} — "
            "the greedy can now break after ONE anchor (single-stop class)"
        )

    # Part 2 — behavioral: the tightest supported duration (30 min) on a GREEN
    # cluster. The sharp pin used to be "exactly 3 stops", which was
    # max_anchors = 30 // 10 — a stop CEILING that was deleted on
    # 2026-08-04. Pinning it now would re-assert a rule the product does not
    # have. The stop count is instead DERIVED from the rule that actually stops
    # the greedy: it seats stops until their capped audio reaches the audio
    # target, so it stops at target / MAX_DWELL_AUDIO_SECONDS of them.
    from src.tour.routing import target_dwell_seconds

    cluster = _density_fillers(PDV, duration_min=30, round_trip=True, prefix="c")
    snap = _snap(cluster, area_types={"Le Marais": "neighborhood"})
    route = select_route(
        TourInput(start=PDV, duration_min=30, city_slug="paris", round_trip=True), snap
    )
    audio_bound_stops = math.ceil(target_dwell_seconds(30) / MAX_DWELL_AUDIO_SECONDS)
    assert len(route.pois) == audio_bound_stops, (
        f"expected the audio-bound {audio_bound_stops} stops at 30 min; "
        f"got {len(route.pois)}"
    )
    # The class invariant this test exists for: never a 1-stop break.
    assert len(route.pois) >= 2
    assert route.tourability is None


def test_build_poi_extra_beats_is_full_minus_voiced():
    # A tier-5 POI with 16 beats: the tour voices 12 (flat cap DEFAULT_FLAT_MAX), 4 are extras.
    poi = _poi("rich", tier=5, lat=PDV[0], lng=PDV[1], beat_count=0)
    beats = [
        BeatRef(id=f"rich-b{i:02d}", poi_id="rich", narrative_function="establishing",
                word_count=300 - i, entities=(f"E{i}",), active_status="active")
        for i in range(16)
    ]
    snap = _snap([poi], beats_by_poi={"rich": beats})
    route = Route(pois=(poi,), transits=(), total_walk_distance_m=0.0, total_walk_seconds=0)

    voiced = build_poi_beat_plans(route, snap, lenses=None)[0]
    voiced_ids = tuple(b.id for b in voiced.beats)
    extras = build_poi_extra_beats(route, snap, {"rich": voiced_ids}, lenses=None)

    assert len(voiced_ids) == 12, "flat cap voices DEFAULT_FLAT_MAX (12)"
    assert set(extras["rich"]).isdisjoint(voiced_ids), "extras are never voiced beats"
    assert len(extras["rich"]) == 4, "the 4 beats beyond the cap are the extras"


def test_build_poi_extra_beats_omits_pois_with_no_extras():
    poi = _poi("thin", tier=5, lat=PDV[0], lng=PDV[1], beat_count=0)
    beats = [
        BeatRef(id=f"thin-b{i}", poi_id="thin", narrative_function="establishing",
                word_count=100 - i, entities=(f"T{i}",), active_status="active")
        for i in range(3)  # under the cap -> all voiced -> no extras
    ]
    snap = _snap([poi], beats_by_poi={"thin": beats})
    route = Route(pois=(poi,), transits=(), total_walk_distance_m=0.0, total_walk_seconds=0)
    voiced = tuple(b.id for b in build_poi_beat_plans(route, snap, lenses=None)[0].beats)
    assert build_poi_extra_beats(route, snap, {"thin": voiced}, lenses=None) == {}


# ---------------------------------------------------------------------------
# Same-name twin collapse — the workbench duplicate-stop bug
# ---------------------------------------------------------------------------


def test_same_name_twins_never_produce_duplicate_stops():
    """A place loaded twice (same NAME, different id) must collapse to ONE stop.

    Reproduces the workbench duplicate-stop bug (Rodin/Rodin, Tuileries/Tuileries):
    two nodes sharing a display name both get selected, and the id-keyed dedup plus
    the co-located-demotion gate (tier>=4 AND <=100m AND cross-address token
    overlap) can miss them, so the tour shows the place twice — the 2nd stop
    beat-starved. The engine must dedup by NAME so a duplicate in the DATA can
    never surface as a duplicate STOP; the dropped twin's beats fold into the
    survivor (no content lost).
    """
    start = (48.8553, 2.3159)
    # A place loaded twice: identical coords + display name, distinct ids. Both get
    # selected (co-located = zero marginal walk), and the co-located-demotion guard
    # skips them (their beats carry no cross-address token overlap), so today the
    # tour emits the place twice — the beat-starved copy reads "Walk to the next stop."
    host = POI(id="rodin-real", name="Musee Rodin", tier=5, poi_role="stop",
               lat=start[0], lng=start[1], areas=("Le Marais",), beat_count=8)
    twin = POI(id="rodin-dup", name="Musee Rodin", tier=5, poi_role="stop",
               lat=start[0], lng=start[1], areas=("Le Marais",), beat_count=8)
    fillers = _density_fillers(start, duration_min=120)
    snap = _snap([host, twin, *fillers], area_types={"Le Marais": "neighborhood"})
    route = select_route(TourInput(start=start, duration_min=120, city_slug="paris"), snap)

    names = [p.name for p in route.pois]
    assert names.count("Musee Rodin") == 1, f"twin place surfaced twice: {names}"
    assert len(names) == len(set(names)), f"duplicate-name stops in route: {names}"
    # No content lost: whichever twin is dropped, its beats fold into the survivor.
    survivor = next(p for p in route.pois if p.name == "Musee Rodin")
    (dropped_id,) = {"rodin-real", "rodin-dup"} - {survivor.id}
    merged = route.demoted_beats.get(survivor.id, ())
    assert any(b.poi_id == dropped_id for b in merged), (
        f"dropped twin {dropped_id} beats not merged into survivor {survivor.id}")


def test_three_way_name_twins_lose_no_beats():
    """3+ same-name twins fold to ONE survivor with NONE of the dropped twins'
    beats lost. Guards a host-chain: A drops to C and C drops to B — A's beats
    must reach the ultimate survivor B, not orphan on the dropped intermediate C.
    """
    from src.tour.selection import collapse_name_twins

    coord = (48.8553, 2.3159)
    # beat_count picks the host (more beats wins): b(3) > c(2) > a(1). Processing
    # order [a, b, c] chains a→c then c→b, so a's beats route through dropped c.
    a = POI(id="a", name="Twin", tier=5, poi_role="stop", lat=coord[0], lng=coord[1], beat_count=1)
    b = POI(id="b", name="Twin", tier=5, poi_role="stop", lat=coord[0], lng=coord[1], beat_count=3)
    c = POI(id="c", name="Twin", tier=5, poi_role="stop", lat=coord[0], lng=coord[1], beat_count=2)
    snap = _snap([a, b, c])

    new_selected, merged = collapse_name_twins([a, b, c], snap)

    assert [p.id for p in new_selected] == ["b"], "should fold to the richest twin"
    survivor_id = new_selected[0].id
    merged_poi_ids = {beat.poi_id for beat in merged.get(survivor_id, ())}
    assert merged_poi_ids == {"a", "c"}, f"a dropped twin's beats were lost: {merged_poi_ids}"


# ---------------------------------------------------------------------------
# C12 enforcement: MIN_STOP_SEPARATION_M guard in select_route's greedy loop.
# 17m (Palais de Justice / Conciergerie, a MEASURED defect) must never seat
# both; 86m (Conciergerie / Sainte-Chapelle, a MEASURED legitimate pair) must
# seat both. Fillers are spaced >50m apart from each other and from the pair
# so this test isolates the guard itself, not the fill-pass/density-gate
# interaction (a separate, larger finding).
# ---------------------------------------------------------------------------

_SEP_START = (48.8555, 2.3656)
_SEP_PAIR_BASE = (_SEP_START[0] - 0.0020, _SEP_START[1])  # ~222m south of start
_SEP_17M_OFFSET = 0.00015288467300032285  # haversine-calibrated: exactly 17.0m
_SEP_86M_OFFSET = 0.00077341658108665  # haversine-calibrated: exactly 86.0m


def _sep_fillers() -> list[POI]:
    """Low-score anchor candidates spread around the start, to clear the
    density gate AND the two-sided time band without competing with the near/far
    pair (tier 3, minimal 3 beats — well below the pair's tier 5 / 20 beats).

    Was 4 anchors on a diagonal. Four stops cannot fill an hour now that a stop
    speaks at most MAX_DWELL_AUDIO_SECONDS, so this is the filler spiral in its
    background mode — tier 3 with 3 beats, which is what keeps these from taking
    the slots the separation pair is being tested for.
    """
    return _density_fillers(
        _SEP_START, tier=3, beat_count=3, prefix="sep-filler"
    )


def _sep_pair(offset: float) -> tuple[POI, POI]:
    near_a = _poi(
        "sep-near-a", tier=5, lat=_SEP_PAIR_BASE[0], lng=_SEP_PAIR_BASE[1],
        areas=("Le Marais",), beat_count=20,
    )
    near_b = _poi(
        "sep-near-b", tier=5, lat=_SEP_PAIR_BASE[0] + offset, lng=_SEP_PAIR_BASE[1],
        areas=("Le Marais",), beat_count=20,
    )
    return near_a, near_b


def test_selection_does_not_filter_close_but_distinct_pois():
    """REFUTATION GUARD. A minimum stop-separation filter in ``select_route`` was tried
    on 2026-07-19 and is REFUTED — do not re-add it. Measured against the real corpora:
    48 Paris pairs and 46 New York pairs sit under 50 m, and they are two different
    populations. True duplicates cluster at 0.0-1.7 m (NYSE/Wall Street, Place/Square des
    Abbesses, Flatiron District/Flatiron Building) and are CORPUS geocoding defects.
    Genuinely distinct landmarks start at ~8 m: Pantheon/Bibliotheque Sainte-Genevieve
    (14.9 m), Concorde/Jeu de Paume (14.3 m), and -- decisively -- Hotel Le Meurice /
    Angelina (8.4 m) and Galignani / Angelina (11.8 m).

    Le Meurice and Galignani are the POIs in the product owner's OWN gold text,
    which narrates "At number 224, the English bookshop Galignani..." with Le Meurice
    at 228. A 50 m
    filter makes the gold-standard stop structurally unbuildable.

    Two further defects were proven when the guard was live: it is first-come-wins, so it
    drops the BEST stop rather than the redundant one (a tier-5 POI with 20 beats lost to
    an arbitrary filler seated first), and it left ``_apply_fill_pass`` /
    ``_apply_endpoint_pull`` unguarded, so it did not even prevent the defect it targeted.

    The real defect it was written for -- Conciergerie narration describing the Palais de
    Justice the listener just left -- is SEMANTIC REPETITION (check G4), not proximity.
    Distance cannot distinguish "one building told twice" from "two institutions on one
    street".
    """
    near_a, near_b = _sep_pair(_SEP_17M_OFFSET)
    assert haversine_m(near_a.lat, near_a.lng, near_b.lat, near_b.lng) == pytest.approx(
        17.0, abs=0.01
    )
    snap = _snap(
        [near_a, near_b, *_sep_fillers()], area_types={"Le Marais": "neighborhood"}
    )
    inp = TourInput(start=_SEP_START, duration_min=60, city_slug="paris", round_trip=False)
    route = select_route(inp, snap)
    ids = {p.id for p in route.pois}
    seated = ids & {"sep-near-a", "sep-near-b"}
    assert seated == {"sep-near-a", "sep-near-b"}, (
        "selection must NOT silently drop a close-but-distinct POI -- that deletes real "
        f"landmarks (Le Meurice/Galignani are 8-12 m apart); got {seated}"
    )


def test_min_stop_separation_guard_seats_both_of_an_86m_pair():
    near_a, near_b = _sep_pair(_SEP_86M_OFFSET)
    assert haversine_m(near_a.lat, near_a.lng, near_b.lat, near_b.lng) == pytest.approx(
        86.0, abs=0.01
    )
    snap = _snap(
        [near_a, near_b, *_sep_fillers()], area_types={"Le Marais": "neighborhood"}
    )
    inp = TourInput(start=_SEP_START, duration_min=60, city_slug="paris", round_trip=False)
    route = select_route(inp, snap)
    ids = {p.id for p in route.pois}
    seated = ids & {"sep-near-a", "sep-near-b"}
    assert seated == {"sep-near-a", "sep-near-b"}, (
        f"86m pair (legitimate, distinct) must seat BOTH, got {seated}"
    )








# ---------------------------------------------------------------------------
# Walking is the PRICE of a day, never its value.
#
# Three rules, measured on the Place des Vosges / Panthéon day shapes: (1) when
# the asked-for length cannot be filled honestly, the day handed over is the
# most STORIED one, honestly short — padding the length with pavement is a walk
# filed as dwell; (2) a stop is never admitted past the product's own 2.0x
# detour gate — the old 4.5 walk-per-dwell allowance would march a visitor
# twenty-two minutes for a five-minute look; (3) a route within one displayed
# minute of its ceiling is the same customer timebox (routing.py's own
# materiality rule) and serves. The planner never fills the requested time for
# its own sake, because the only way to guarantee filling it is to walk the
# visitor somewhere pointless; a day is priced in visitor-time.
# ---------------------------------------------------------------------------


def _priced_poi(pid: str, *, tier: int, lng_offset_deg: float, visit_min: int,
                beat_count: int) -> POI:
    """A candidate at PDV's latitude, `lng_offset_deg` east, with a real priced
    visit (`typical_duration_min`) — the field `visit_seconds` reads."""
    return _poi(
        pid, tier=tier, lat=PDV[0], lng=PDV[1] + lng_offset_deg,
        areas=("Le Marais",), beat_count=beat_count,
    ).model_copy(update={"typical_duration_min": visit_min})


def test_an_under_filled_day_is_the_most_storied_one_never_the_longest_walk():
    """The measured case: a 60-minute round trip
    standing IN a tier-5 square with the most to tell served ONE stop: a lesser
    square 12 minutes' walk away, because 'longest day under the ceiling'
    counted the walking as day-length (2636s of walk-padded 'day' beat 1800s of
    standing at the best place). The square the visitor stands in, told
    honestly short, beats any day padded longer with their steps.

    Unit seam: the timebox repair's under-fill choice, with the live PdV
    geometry in miniature — the rich square AT the start (visit 30 min, zero
    walking), the lesser one 410 m out (visit 20 min). Both days sit UNDER the
    band floor, so the under-fill branch is what decides; the lesser day's
    1328 s round trip stays inside the 1440 s walking allocation, so the walk
    admission rule does not decide it either (at 450 m it does, which is how
    this fixture was first written and why it proved nothing).

    Walk-padded arithmetic today: 1200 s standing + 1328 s walking = 2528 s
    "day" beats 1800 s of standing at the best place — the live Bastille shape
    (1436 s of walking inside a 1440 s allocation) in miniature.

    UNDO: return the under-fill choice to max(elapsed minus queue) -> the lesser
    walk-padded day wins again -> RED."""
    from src.tour.routing import DEFAULT_ROUTE_PLANNING_POLICY, route_planning_budget
    from src.tour.selection import _apply_certification_timebox_repair

    rich = _priced_poi("rich-square", tier=5, lng_offset_deg=0.0, visit_min=30,
                       beat_count=24)
    lesser = _priced_poi("lesser-square", tier=3, lng_offset_deg=0.005600,
                         visit_min=20, beat_count=4)
    snap = _snap([rich, lesser], area_types={"Le Marais": "neighborhood"})
    inp = TourInput(start=PDV, duration_min=60, city_slug="paris", round_trip=True)

    out = _apply_certification_timebox_repair(
        [rich],
        [rich, lesser],
        input=inp,
        snapshot=snap,
        spine="Le Marais",
        interest=frozenset(),
        score_penalty=None,
        leg_seconds_fn=None,
        planning_policy=DEFAULT_ROUTE_PLANNING_POLICY,
        planning_budget=route_planning_budget(60),
    )
    assert [p.id for p in out] == ["rich-square"], (
        f"the under-filled day must be the most storied one (the square the "
        f"walker stands in), never the longest walk; got {[p.id for p in out]}"
    )

    # ...AND STORIED IS NOT THE SAME AS LONG: the most storied day beats the
    # longest standing time, because maximised standing time still picks
    # quantity over the best places. A quiet place with a LONGER visit must not
    # beat the storied square — the live Place des Vosges golden picked Musee
    # Victor Hugo, tier 4 with TWO beats and a 35-minute visit, over the
    # 64-story square, when this key read dwell. Both candidates sit AT the
    # start here, so walking cannot decide it and only the currency can.
    quiet_but_long = _priced_poi("quiet-but-long", tier=4, lng_offset_deg=0.00002,
                                 visit_min=35, beat_count=2)
    snap2 = _snap([rich, quiet_but_long], area_types={"Le Marais": "neighborhood"})
    out2 = _apply_certification_timebox_repair(
        [rich],
        [rich, quiet_but_long],
        input=inp,
        snapshot=snap2,
        spine="Le Marais",
        interest=frozenset(),
        score_penalty=None,
        leg_seconds_fn=None,
        planning_policy=DEFAULT_ROUTE_PLANNING_POLICY,
        planning_budget=route_planning_budget(60),
    )
    assert "rich-square" in {p.id for p in out2}, (
        "a 2-beat place with a longer visit must not displace the 24-story square "
        f"— storied is not the same as long; got {[p.id for p in out2]}"
    )

    # ...AND THE MEASURE IS MONOTONE: a day that keeps everything another day has
    # PLUS one more real place is never the poorer day. Two stops of the SAME
    # category are the case that breaks a naive answer — the diversity dimmer
    # fires when a category covers half a set, so ranking by the dimmed score made
    # a two-museum day score BELOW a one-museum day and the planner dropped a
    # stop to "improve" the day. Measured live on a visitor's own request: her day
    # collapsed from [Musee de l'Orangerie, Musee d'Orsay] to [Musee d'Orsay], and
    # with the walking went the cadence crossing that seats her BENCH.
    #
    # UNDO: rank the under-fill choice by `_diversity_weighted_score` instead of
    # the plain sum -> the single stop wins -> RED.
    # Sized so the PAIR fits under the 3600 s ceiling (1200 + 900 of standing plus
    # 713 s of walking = 2813 s) and both days sit under the 3240 s floor, so the
    # under-fill branch is what decides — and so the two keys genuinely disagree:
    # the plain sum prefers the pair (16.09 + 3.30), while the dimmer fires on
    # a set that is two-thirds one category and scores it 0.8 x 19.39 = 15.51,
    # UNDER the 16.09 of the single stop. Without that gap the test would pass
    # under either key and prove nothing.
    same_category_pair = [
        _priced_poi("gallery-a", tier=5, lng_offset_deg=0.0, visit_min=20, beat_count=24)
        .model_copy(update={"place_category": "gallery"}),
        _priced_poi("gallery-b", tier=3, lng_offset_deg=0.00300, visit_min=15,
                    beat_count=2).model_copy(update={"place_category": "gallery"}),
    ]
    snap3 = _snap(same_category_pair, area_types={"Le Marais": "neighborhood"})
    out3 = _apply_certification_timebox_repair(
        [same_category_pair[0]],
        same_category_pair,
        input=inp,
        snapshot=snap3,
        spine="Le Marais",
        interest=frozenset(),
        score_penalty=None,
        leg_seconds_fn=None,
        planning_policy=DEFAULT_ROUTE_PLANNING_POLICY,
        planning_budget=route_planning_budget(60),
    )
    assert {p.id for p in out3} == {"gallery-a", "gallery-b"}, (
        "two same-category places must not score BELOW one of them — the variety "
        f"dimmer is a tie-break, never a reason to drop a stop; got "
        f"{[p.id for p in out3]}"
    )


def test_a_stop_is_never_admitted_past_the_walking_worth_line():
    """A stop is never admitted past the product's own detour gate. The
    admission line is TWO bounds on an added stop's marginal walking — at most
    ``STOP_WALK_WORTH_MAX`` (2.0) seconds of walking per second it earns the
    visitor standing there, AND at most ``MAX_STOP_PURE_DETOUR_SECONDS``
    (1800 s — the same 30-minute line the illogical-detour invariant enforces,
    tests/test_tour_invariants_live.py). The measured case: the
    Panthéon 120 loop walked 43 minutes to a bookshop because 4.5:1 admitted it.
    The dearest trade a visitor defends — 16 minutes' walk for a 9-minute stand
    at the memorial, 1.8:1 — stays admissible: the cap is the loosest line no
    defended trade breaks.

    EACH BOUND IS ISOLATED BY ITS OWN CANDIDATE, so each mutation has something
    to break — the first version of this test used one candidate that tripped
    BOTH bounds, so neither mutation flipped it and it proved nothing (caught by
    its own UNDO pass, sabotage mode 5). Every candidate is offered alone, since
    admitting one would move the next one's marginal walk.

    UNDO: return RESCUE_MAX_WALK_PER_DWELL_SECOND to 4.5 -> `ratio-slog` seats
    -> RED. Drop the MAX_STOP_PURE_DETOUR_SECONDS half -> `detour-slog` seats
    -> RED."""
    from src.tour.selection import _apply_fill_pass

    a = _priced_poi("a-anchor", tier=4, lng_offset_deg=0.0, visit_min=15, beat_count=5)
    # 462 m out: marginal loop walk 1498 s. UNDER the 1800 s absolute ceiling, so
    # only the RATIO can refuse it — 1498 vs 2.0 x 400 s of standing (3.7:1, the
    # "twenty-two minutes for a five-minute look" shape). At 4.5 it
    # seats (1498 <= 1800).
    ratio_slog = _priced_poi("ratio-slog", tier=5, lng_offset_deg=0.006318,
                             visit_min=7, beat_count=8)
    # 616 m out: marginal 1996 s against a 20-minute stop. INSIDE the ratio
    # (1996 <= 2.0 x 1200), so only the ABSOLUTE ceiling can refuse it — half an
    # hour of pure detour is the line the product's own INV8 gate draws.
    detour_slog = _priced_poi("detour-slog", tier=5, lng_offset_deg=0.008420,
                              visit_min=20, beat_count=8)
    # 500 m out: marginal 1618 s for a 15-minute stop — 1.8:1, exactly the
    # dearest trade a visitor defends (sixteen minutes' walk for nine silent minutes at the
    # deportation memorial), and inside both bounds. It must still seat, or the
    # line has stopped being a worth test and become a distance cap.
    worth_it = _priced_poi("worth-it", tier=4, lng_offset_deg=-0.006828,
                           visit_min=15, beat_count=5)
    dwell_by_id = {"a-anchor": 900, "ratio-slog": 400, "detour-slog": 1200,
                   "worth-it": 900}
    snap = _snap([a, ratio_slog, detour_slog, worth_it],
                 area_types={"Le Marais": "neighborhood"})

    def offered_alone(candidate: POI) -> set[str]:
        return {
            p.id
            for p in _apply_fill_pass(
                [a],
                [a, candidate],
                spine="Le Marais",
                interest=frozenset(),
                snapshot=snap,
                start_lat=PDV[0],
                start_lng=PDV[1],
                round_trip=True,
                walk_budget=2160,
                dwell_budget=3240,
                stop_cost_fn=lambda p, *, exempt: dwell_by_id[p.id],
                exempt_anchor_id="a-anchor",
                rescue_floor=2,
            )
        }

    assert "ratio-slog" not in offered_alone(ratio_slog), (
        "a stop costing more walking than it earns the visitor must stay out "
        "(the ratio half of the worth line)"
    )
    assert "detour-slog" not in offered_alone(detour_slog), (
        "a stop bought with more than half an hour of pure detour must stay out "
        "(the absolute half of the worth line)"
    )
    assert "worth-it" in offered_alone(worth_it), (
        "the 1.8:1 trade (the memorial shape) must still seat"
    )


def test_a_route_within_one_displayed_minute_of_its_ceiling_serves(monkeypatch):
    """A route within one displayed minute of its ceiling serves. The measured
    case: a 250-minute request
    refused at 250 minutes and 22 SECONDS, while routing.py's own
    TIMEBOX_MATERIALITY_TOLERANCE_SECONDS declares a route within one displayed
    minute "materially in the same customer timebox". Refusing over twenty-two
    seconds after declaring that is not a rule. There is one definition of
    material.

    Same drift mechanism as the exact-elapsed guard test above, sized to 22
    seconds instead of a preposterous number. UNDO: drop the tolerance from the
    final ceiling check -> this refuses again -> RED."""
    from src.tour import selection as selection_module
    from src.tour.routing import route_planning_budget
    from src.tour.selection import served_dwell_seconds
    from src.tour.visit_time import served_elapsed_seconds

    start = PDV
    end = (start[0], start[1] + 0.008)
    pois = _density_fillers(start, duration_min=60, radius_m=90.0)
    snap = _snap(pois, area_types={"Le Marais": "neighborhood"})
    inp = TourInput(start=start, end=end, duration_min=60, city_slug="paris")

    control = select_route(inp, snap)
    assert control.pois
    control_dwell = served_dwell_seconds(
        control, snap, interest=frozenset(), end_is_none=False
    )
    control_elapsed = served_elapsed_seconds(control.total_walk_seconds, control_dwell)
    ceiling = route_planning_budget(60).nominal_elapsed_seconds
    drift = ceiling - control_elapsed + 22
    assert drift > 0, "fixture drifted: the control day already fills its ceiling"

    real_summarise = selection_module.summarise_route

    def drifted_summarise(*args, **kwargs):
        route = real_summarise(*args, **kwargs)
        return route.model_copy(
            update={"total_walk_seconds": route.total_walk_seconds + drift}
        )

    monkeypatch.setattr(selection_module, "summarise_route", drifted_summarise)
    served = select_route(inp, snap)
    assert served.pois, "a day 22 seconds over its ceiling is the same customer timebox"


# --- the loop's direction is a choice ------------------------------------------


def _line_loop(marquee_first: bool) -> list:
    """Three stops east of PDV on a line: the tier-5 marquee nearest the start,
    two tier-4 stops beyond it — the shape whose forward order front-loads the
    day's anchor."""
    marquee = _poi("marquee", tier=5, lat=48.8555, lng=2.3683)  # ~200 m
    mid = _poi("mid", tier=4, lat=48.8555, lng=2.3710)  # ~400 m
    far = _poi("far", tier=4, lat=48.8555, lng=2.3738)  # ~600 m
    ordered = [marquee, mid, far]
    return ordered if marquee_first else list(reversed(ordered))


def test_a_round_trips_direction_lands_the_marquee_late():
    """A closed loop's two directions cost the same walk, so the direction is
    a CHOICE, and the marquee lands late deliberately: a back-loaded anchor
    must be right by design, not by luck. Held-Karp's tie-break had made it
    an accident of ids."""
    from src.tour.selection import _orient_loop

    ordered = _line_loop(marquee_first=True)
    snapshot = _snap(list(ordered))
    oriented = _orient_loop(
        ordered,
        snapshot=snapshot,
        interest=frozenset(),
        start=PDV,
        leg_seconds_fn=None,
    )
    assert oriented[-1].id == "marquee", (
        f"the marquee sat first and the loop was not reversed: {[p.id for p in oriented]}"
    )


def test_an_already_late_marquee_keeps_its_order():
    """No churn when the accident already landed right."""
    from src.tour.selection import _orient_loop

    ordered = _line_loop(marquee_first=False)
    snapshot = _snap(list(ordered))
    oriented = _orient_loop(
        ordered,
        snapshot=snapshot,
        interest=frozenset(),
        start=PDV,
        leg_seconds_fn=None,
    )
    assert [p.id for p in oriented] == [p.id for p in ordered]


def test_direction_choice_never_buys_walking():
    """The reversal is COST-FREE or it does not happen (routed
    legs are near- but not exactly symmetric): under a leg fn that prices
    westward walking +500 s, reversing the eastbound loop would add real
    walking, so the forward order stands even with the marquee early."""
    from src.tour.selection import _orient_loop

    def eastbound_cheap(lat1: float, lng1: float, lat2: float, lng2: float) -> int:
        from src.tour.routing import default_leg_seconds

        penalty = 500 if lng2 < lng1 else 0
        return default_leg_seconds(lat1, lng1, lat2, lng2) + penalty

    ordered = _line_loop(marquee_first=True)
    snapshot = _snap(list(ordered))
    oriented = _orient_loop(
        ordered,
        snapshot=snapshot,
        interest=frozenset(),
        start=PDV,
        leg_seconds_fn=eastbound_cheap,
    )
    assert [p.id for p in oriented] == [p.id for p in ordered], (
        "the reversal bought materially more walking and must be refused"
    )


# ---------------------------------------------------------------------------
# The timebox repair's FOURTH MOVE — "step outside".
#
# Add, drop and exchange can only answer "which places". A fixed-end request
# whose DESTINATION is too big for the hour therefore had no answer at all: the
# destination is protected from every drop, so its interior plus its line is a
# floor no move can lower, and the day was refused outright. The engine already
# knows how to keep a place and drop its door — a locked door, a `wall` end and
# a party ceiling each trigger that one collapse in `visit_shape` — so the
# fourth move simply lets the requested DURATION trigger it too, as a LAST
# RESORT, and the day says so.
# ---------------------------------------------------------------------------

_ND_START = (48.85675, 2.341033)  # Pont Neuf
_ND_END = (48.85675, 2.341033 + 0.0090)  # ~658 m east — the cathedral


def _pont_neuf_to_cathedral() -> tuple[POI, POI, CorpusSnapshot]:
    """Pont Neuf to a cathedral, in the live case's own arithmetic.

    The bridge is a place with no interior: 12 minutes on it, 720 s. The
    cathedral has 25 minutes of facade, 50 minutes of interior and a 15-minute
    line, and the requested lens is a DIRECT hit on its beats, so `visit_shape`
    prices the whole 50-minute interior plus the line — 3900 s at one stop. The
    walk between them is ~1067 s. Those are the frozen
    `paris-pont-neuf-notre-dame-60` numbers to within a few seconds.
    """
    bridge = _poi(
        "bridge",
        tier=5,
        lat=_ND_START[0],
        lng=_ND_START[1],
        areas=("Île de la Cité",),
        beat_count=5,
    ).model_copy(update={"typical_duration_min": 12})
    cathedral = _poi(
        "cathedral",
        tier=5,
        lat=_ND_END[0],
        lng=_ND_END[1],
        areas=("Île de la Cité",),
        beat_count=8,
    ).model_copy(
        update={
            "typical_duration_min": 25,
            "visit_seconds_inside": 50 * 60,
            "visit_basis": "fifty minutes under the vaults, twenty-five on the parvis",
            "queue_class": "long",
            "queue_minutes_peak": 15,
            "queue_minutes_offpeak": 15,
        }
    )
    snap = _snap(
        [bridge, cathedral],
        area_types={"Île de la Cité": "island"},
        beats_by_poi={
            "bridge": _lensed_beats("bridge", ("historic_arch",)),
            "cathedral": _lensed_beats("cathedral", ("historic_arch",), n=8),
        },
    )
    return bridge, cathedral, snap


def _elapsed_of(route, snap) -> int:
    from src.tour.selection import served_dwell_seconds
    from src.tour.visit_time import served_elapsed_seconds

    return served_elapsed_seconds(
        route.total_walk_seconds,
        served_dwell_seconds(
            route, snap, interest=frozenset({"historic_arch"}), end_is_none=False
        ),
    )


def test_a_destination_too_big_for_the_hour_is_served_from_the_outside():
    """The visitor asks for an hour from Pont Neuf ending at the cathedral, and
    gets one — standing outside the cathedral rather than being told no.

    Going IN costs 50 minutes plus a 15-minute line, so the smallest possible
    day with the interior is 4967 s against a 3600 s request, and every add,
    drop and exchange leaves it there: the destination is protected, so nothing
    the repair could remove touches that number. The honest answer is not a
    refusal — it is the hour the visitor asked for, with the facade instead of
    the nave, said out loud. 3287 s: 720 s on the bridge, 1067 s of walking,
    1500 s in front of the cathedral, no line.
    """
    from src.tour.routing import route_planning_budget
    from src.tour.visit_time import shape_total_seconds, visit_shape

    bridge, cathedral, snap = _pont_neuf_to_cathedral()
    lens = frozenset({"historic_arch"})
    band = route_planning_budget(60)

    # The premise: going inside is a bigger number than the whole request, and
    # it belongs to the DESTINATION, which no drop may remove.
    inside = visit_shape(cathedral, lens, snap)
    assert inside.goes_inside is True
    assert shape_total_seconds(inside) > band.nominal_elapsed_seconds

    inp = TourInput(
        start=_ND_START,
        end=_ND_END,
        duration_min=60,
        city_slug="paris",
        lenses=["historic_arch"],
    )
    route = select_route(inp, snap)

    assert [p.id for p in route.pois] == [bridge.id, cathedral.id]
    elapsed = _elapsed_of(route, snap)
    assert band.minimum_elapsed_seconds <= elapsed <= band.nominal_elapsed_seconds, elapsed

    # The stop the visitor stands at, priced as the exterior it now is: no
    # interior, no line, the facade minutes kept.
    assert route.visit_goes_inside[cathedral.id] is False
    assert route.planned_queue_seconds[cathedral.id] == 0
    assert route.planned_visit_seconds[cathedral.id] == 25 * 60

    # NEVER SILENTLY. The day carries the fact on the same notes
    # channel a locked door and an unlit finish use, in one self-contained
    # sentence a stranger can judge — and NOT flagged `kept_outside`, which is
    # where every reader gets the word "closed" (the harness's own column reads
    # "closed — outside only"). This cathedral is open; the hour is the problem.
    said = [ex for ex in route.clock_exclusions if ex.poi_id == cathedral.id]
    assert len(said) == 1, route.clock_exclusions
    assert said[0].kept_outside is False, "the place is not closed — the day is short"
    assert said[0].reason == (
        "its interior does not fit the 60 minutes you asked for, "
        "so we will see it from the outside"
    ), said[0].reason
    # ...and the promise the planner swore says the same thing.
    finish = next(p for p in route.promises if p.poi_id == cathedral.id)
    assert finish.shape.goes_inside is False
    assert finish.shape.queue_seconds == 0


def test_a_day_that_fits_with_the_interior_never_steps_outside():
    """LAST RESORT, and the ordering is the point: the same request with room
    for the interior keeps the interior.

    100 minutes fits the whole thing — bridge, walk, 50 minutes inside, and the
    line: 5687 s inside a 5400-6000 s band. A repair that reached for the
    exterior whenever it was cheaper would hand this visitor a facade and half
    an hour of nothing. Nothing is stepped outside, and nothing is disclosed,
    because nothing was taken away.
    """
    from src.tour.routing import route_planning_budget

    bridge, cathedral, snap = _pont_neuf_to_cathedral()
    band = route_planning_budget(100)

    inp = TourInput(
        start=_ND_START,
        end=_ND_END,
        duration_min=100,
        city_slug="paris",
        lenses=["historic_arch"],
    )
    route = select_route(inp, snap)

    assert [p.id for p in route.pois] == [bridge.id, cathedral.id]
    elapsed = _elapsed_of(route, snap)
    assert band.minimum_elapsed_seconds <= elapsed <= band.nominal_elapsed_seconds, elapsed

    assert route.visit_goes_inside[cathedral.id] is True, (
        "an interior that fits the day must never be traded for its facade"
    )
    # The whole stop: 50 minutes of building plus the 15-minute line it costs
    # to get into it (`planned_visit_seconds` is the shape's total).
    assert route.planned_visit_seconds[cathedral.id] == 50 * 60 + 15 * 60
    assert route.planned_queue_seconds[cathedral.id] == 15 * 60
    assert route.clock_exclusions == (), (
        f"nothing was taken away, so there is nothing to disclose; "
        f"got {route.clock_exclusions}"
    )


def test_a_replanned_remainder_reports_its_overrun_instead_of_stepping_outside():
    """A TAIL never buys its clock with an interior — it hands the overrun back.

    The fourth move exists to save a day whose only alternative is a REFUSAL.
    A replanned remainder is never refused: the best over-ceiling
    day comes back with `overrun_seconds` on it, and that number is the raw
    material of the ONE question the person answers ("keep your full rest and
    be at the Orsay about 17:02, or sit six minutes and be back by 17:00" —
    `contingency.question_text`). A repair that steps a stop outside
    on this path answers the question FOR her and hands back a quietly thinner
    day instead. Measured 2026-08-25 on a visitor's own day, forty-six minutes
    late out of the Orangerie: the remainder priced 100 s over her clock, the
    move shed an interior to absorb them, the overrun came back 0, and the live
    reply carried NO question at all.

    Same request and same corpus as
    `test_a_destination_too_big_for_the_hour_is_served_from_the_outside`, which
    is the deliberate contrast: a FRESH plan of this hour steps the cathedral
    outside, because there the alternative really is a refusal. Both stops are
    the person's own here — the bridge held, the cathedral her declared finish —
    so no drop or exchange can shorten the day either, exactly as that
    visitor's Orsay and her bench cannot.

    UNDO: try the fourth move before the `report_overrun` return -> the
    cathedral's interior is traded away and the overrun comes back 0 -> RED.
    """
    from src.tour.contract import ReplanContext
    from src.tour.routing import route_planning_budget

    bridge, cathedral, snap = _pont_neuf_to_cathedral()
    band = route_planning_budget(60)
    inp = TourInput(
        start=_ND_START,
        end=_ND_END,
        duration_min=60,
        city_slug="paris",
        lenses=["historic_arch"],
    )

    route = select_route(
        inp,
        snap,
        replan=ReplanContext(floor_zero=True, protected_poi_ids=(bridge.id, cathedral.id)),
    )

    assert [p.id for p in route.pois] == [bridge.id, cathedral.id]
    assert route.visit_goes_inside[cathedral.id] is True, (
        "a remainder must not trade an interior away to fit the minutes left — "
        "the person decides that, through the question"
    )
    # The whole stop, interior and line, exactly as the day that fits keeps it.
    assert route.planned_visit_seconds[cathedral.id] == 50 * 60 + 15 * 60
    assert route.planned_queue_seconds[cathedral.id] == 15 * 60
    # And the number the question is built from rides back on the route.
    elapsed = _elapsed_of(route, snap)
    assert route.overrun_seconds == elapsed - band.nominal_elapsed_seconds > 0, (
        f"the tail must REPORT its overrun; got {route.overrun_seconds}"
    )
    assert route.clock_exclusions == (), (
        f"nothing was stepped outside, so there is nothing to disclose; "
        f"got {route.clock_exclusions}"
    )


def test_a_repair_that_has_spent_its_trial_budget_still_steps_outside(monkeypatch):
    """The fourth move must not be starved by the trial budget the FIRST
    enumeration spent.

    ``observed`` does double duty — it is the refusal's evidence list AND the
    counter the trial cap reads — and the fourth move re-ran the whole
    enumeration without resetting it. So on any repair whose first enumeration
    reached ``TIMEBOX_REPAIR_MAX_TRIALS``, every exterior-only enumeration
    returned before pricing a thing and the move silently never ran: the
    traveller got the refusal back, not the day. (Measured on a 400-minute
    request over a 214-place pool, round one spends the whole cap.)

    TWO is exactly what the first enumeration prices here, measured
    2026-08-25: the incumbent, and the one add the two-place pool offers. The
    destination is protected, so there is no drop and no exchange. A cap of two
    therefore takes NOTHING away from that enumeration — it banks every trial
    it would have banked at four thousand — while leaving the fourth move
    nothing at all. The pool size is asserted below so the count cannot drift
    out from under this.
    """
    from src.tour import selection as selection_module

    bridge, cathedral, snap = _pont_neuf_to_cathedral()
    assert len(snap.pois) == 2, "the trial count in this docstring assumes the pair"
    inp = TourInput(
        start=_ND_START,
        end=_ND_END,
        duration_min=60,
        city_slug="paris",
        lenses=["historic_arch"],
    )

    monkeypatch.setattr(selection_module, "TIMEBOX_REPAIR_MAX_TRIALS", 2)
    route = select_route(inp, snap)

    assert [p.id for p in route.pois] == [bridge.id, cathedral.id]
    assert route.visit_goes_inside[cathedral.id] is False, (
        "a spent trial budget must not silently disable the step-outside move"
    )
    assert route.planned_visit_seconds[cathedral.id] == 25 * 60


def _two_interiors_one_hour() -> tuple[POI, POI, CorpusSnapshot, TourInput]:
    """A pinned hall at the start and a destination hall, both too big together.

    Both stops are PROTECTED — one by the visitor's pin, one by being the fixed
    destination — so no drop or exchange exists and the repair reaches its
    fourth move with TWO interiors to choose between. That is the only shape in
    which the choice is a choice: where a stop can be dropped, dropping it is
    tried first.

    The arithmetic (60 minutes = a 3240-3600 s band; 474 s of walking):
      - both interiors:      1900 + 2200 + 474 = 4574 s — over the ceiling;
      - `a-pin` outside:      480 + 2200 + 474 = 3154 s — under the ceiling but
                                                          SHORT of the band;
      - `z-dest` outside:    1900 + 1200 + 474 = 3574 s — squarely in the band.
    """
    a_pin = _poi(
        "a-pin", tier=5, lat=_ND_START[0], lng=_ND_START[1],
        areas=("Île de la Cité",), beat_count=5,
    ).model_copy(update={"typical_duration_min": 8, "visit_seconds_inside": 1900})
    z_dest = _poi(
        "z-dest", tier=5, lat=_ND_START[0], lng=_ND_START[1] + 0.0040,
        areas=("Île de la Cité",), beat_count=5,
    ).model_copy(update={"typical_duration_min": 20, "visit_seconds_inside": 2200})
    snap = _snap(
        [a_pin, z_dest],
        area_types={"Île de la Cité": "island"},
        beats_by_poi={
            "a-pin": _lensed_beats("a-pin", ("historic_arch",)),
            "z-dest": _lensed_beats("z-dest", ("historic_arch",)),
        },
    )
    inp = TourInput(
        start=_ND_START,
        end=(z_dest.lat, z_dest.lng),
        duration_min=60,
        city_slug="paris",
        lenses=["historic_arch"],
        pinned_poi_ids=["a-pin"],
    )
    return a_pin, z_dest, snap, inp


def test_which_stop_steps_outside_is_ranked_never_alphabetical():
    """With several interiors on offer, the day kept is the best day the repair
    can rank — never whichever stop's id sorts first.

    Stepping outside `a-pin` leaves a 3154 s day: under the ceiling, but short
    of the band by a minute and a half, and the visitor loses a 32-minute
    interior to keep an 8-minute facade. Stepping outside `z-dest` leaves a
    3574 s day, squarely inside the band. Taking the first offer that yields
    anything hands over the worse day for no reason but the alphabet.
    """
    from src.tour.routing import (
        DEFAULT_ROUTE_PLANNING_POLICY,
        default_leg_seconds,
        route_planning_budget,
    )
    from src.tour.selection import _apply_certification_timebox_repair
    from src.tour.visit_time import shape_total_seconds, visit_shape

    a_pin, z_dest, snap, inp = _two_interiors_one_hour()
    lens = frozenset({"historic_arch"})
    band = route_planning_budget(60)

    # THE ONE pricing channel `_select_route_once` wires, wired the same way
    # here: the shape reads the set, the total reads the shape.
    exterior_only_ids: set[str] = set()

    def shape_visit(poi: POI, hour: int | None):
        return visit_shape(poi, lens, snap, exterior_only=poi.id in exterior_only_ids)

    def price_visit(poi: POI, hour: int | None) -> int:
        return shape_total_seconds(shape_visit(poi, hour))

    # The premise, priced live: both stops go inside, `a-pin` is the one whose
    # id sorts first (so first-offer-wins and best-offer-wins disagree), and
    # only ONE of the two exterior-priced days lands in the band.
    walk = default_leg_seconds(*_ND_START, z_dest.lat, z_dest.lng)
    assert shape_visit(a_pin, None).goes_inside is True
    assert shape_visit(z_dest, None).goes_inside is True
    assert sorted([a_pin.id, z_dest.id]) == [a_pin.id, z_dest.id]
    facades = {p.id: p.typical_duration_min * 60 for p in (a_pin, z_dest)}
    interiors = {p.id: p.visit_seconds_inside for p in (a_pin, z_dest)}
    step_a = facades[a_pin.id] + interiors[z_dest.id] + walk
    step_z = interiors[a_pin.id] + facades[z_dest.id] + walk
    assert interiors[a_pin.id] + interiors[z_dest.id] + walk > band.nominal_elapsed_seconds
    assert step_a < band.minimum_elapsed_seconds, "the alphabetical day is short of the band"
    assert band.minimum_elapsed_seconds <= step_z <= band.nominal_elapsed_seconds

    out = _apply_certification_timebox_repair(
        [a_pin, z_dest],
        [a_pin, z_dest],
        input=inp,
        snapshot=snap,
        spine="Île de la Cité",
        interest=lens,
        score_penalty=None,
        leg_seconds_fn=None,
        planning_policy=DEFAULT_ROUTE_PLANNING_POLICY,
        planning_budget=band,
        price_visit=price_visit,
        protected_promise_ids={a_pin.id},
        exterior_only_ids=exterior_only_ids,
        shape_visit=shape_visit,
    )

    assert {p.id for p in out} == {a_pin.id, z_dest.id}, [p.id for p in out]
    assert exterior_only_ids == {z_dest.id}, (
        "the stop that steps outside must be the one that leaves the better "
        f"day, not the one whose id sorts first; got {exterior_only_ids}"
    )
    # And the day it leaves is the in-band one priced above, not the short one.
    assert sum(price_visit(p, None) for p in out) + walk == step_z


# ---------------------------------------------------------------------------
# The refusal a repair raises has to tell ONE true story.
# ---------------------------------------------------------------------------


def test_the_refusal_tells_one_true_story_whether_or_not_a_route_was_priced():
    """Measured 2026-08-25 on a New York 60-minute start, the refusal read:
    "every route reachable from this start overruns the requested duration; the
    shortest one found is still longer than asked for; required 3240-3600s, and
    no bounded route could be priced at all." Three claims, and they cannot all
    be true: every route overran, a shortest one was found, nothing was priced.

    Both states are pinned here, whole sentence and all, because the defect was
    a sentence rather than a number.
    """
    import math as _math

    from src.tour.routing import (
        DEFAULT_ROUTE_PLANNING_POLICY,
        TIMEBOX_MATERIALITY_TOLERANCE_SECONDS,
        route_planning_budget,
    )
    from src.tour.selection import _apply_certification_timebox_repair
    from src.tour.visit_time import visit_seconds

    band = route_planning_budget(60)
    policy_id = DEFAULT_ROUTE_PLANNING_POLICY.policy_id

    def _repair(selected: list[POI], candidates: list[POI], snapshot: CorpusSnapshot):
        return _apply_certification_timebox_repair(
            selected,
            candidates,
            input=TourInput(
                start=PDV, duration_min=60, city_slug="paris", round_trip=True
            ),
            snapshot=snapshot,
            spine="Le Marais",
            interest=frozenset(),
            score_penalty=None,
            leg_seconds_fn=None,
            planning_policy=DEFAULT_ROUTE_PLANNING_POLICY,
            planning_budget=band,
        )

    # STATE ONE — a route WAS priced, and every one of them overran. The
    # sentence may say so, and the tail carries the number it found.
    huge = _poi(
        "huge", tier=5, lat=PDV[0], lng=PDV[1], areas=("Le Marais",), beat_count=5
    ).model_copy(update={"typical_duration_min": 200})
    huge_snap = _snap([huge], area_types={"Le Marais": "neighborhood"})
    best = visit_seconds(huge, frozenset(), huge_snap)
    assert best > band.nominal_elapsed_seconds, "the premise: one stop overruns the hour"

    with pytest.raises(CertificationPlanningInfeasibleError) as overran:
        _repair([huge], [huge], huge_snap)

    assert str(overran.value) == (
        f"Certification planning infeasible under {policy_id}: "
        "every route reachable from this start overruns the requested duration; "
        "the shortest one found is still longer than asked for; "
        f"required {band.minimum_elapsed_seconds}-{band.nominal_elapsed_seconds}s, "
        f"best eligible bounded route {best}s."
    ), str(overran.value)
    assert overran.value.best_elapsed_seconds == best
    assert overran.value.gap_minutes == _math.ceil(
        (best - band.maximum_elapsed_seconds) / 60
    )
    # An open walk has no destination to drop, so the only way out is a longer
    # day — the least one whose ceiling covers what the best route costs.
    ((extend,),) = (overran.value.alternatives,)
    assert (extend.kind, extend.drop_end) == ("extend", False)
    assert (
        route_planning_budget(extend.duration_min).maximum_elapsed_seconds
        + TIMEBOX_MATERIALITY_TOLERANCE_SECONDS
        >= best
    )
    assert (
        route_planning_budget(extend.duration_min - 1).maximum_elapsed_seconds
        + TIMEBOX_MATERIALITY_TOLERANCE_SECONDS
        < best
    )

    # STATE TWO — nothing was priced at all. Nothing overran, nothing was
    # found, and the sentence has to say the one thing that is true.
    with pytest.raises(CertificationPlanningInfeasibleError) as nothing:
        _repair([], [], _snap([], area_types={"Le Marais": "neighborhood"}))

    assert str(nothing.value) == (
        f"Certification planning infeasible under {policy_id}: "
        "no stop near this start could be seated into a route — try a longer "
        "day, or a start with more within walking distance; "
        f"required {band.minimum_elapsed_seconds}-{band.nominal_elapsed_seconds}s, "
        "and no bounded route could be priced at all."
    ), str(nothing.value)
    assert nothing.value.best_elapsed_seconds is None
    # Nothing actionable is KNOWN, so nothing is offered: an "extend" duration
    # is derived from what the best route costs, and there was no best route.
    assert nothing.value.alternatives == ()
    assert nothing.value.gap_minutes is None


# ---------------------------------------------------------------------------
# The parent lens counts for its children at the beat matcher.
# ---------------------------------------------------------------------------


def test_a_parent_lens_counts_for_its_children_at_the_beat_matcher():
    """Beats carry leaf lenses only, so a request for a parent ('history') matched
    nothing at the beat level and the ordering went lens-blind — and visitors
    ask exactly that way. The expansion is the SAME one-hop family the POI
    classifier (`_lens_relation`) already reads off `snapshot.lens_neighbors`,
    spelled once and handed to the beat matcher: the parent stands for its children.
    """
    poi = _poi("p1", tier=5, lat=48.8555, lng=2.3656, beat_count=0)
    dark = BeatRef(
        id="b-dark", poi_id="p1", lenses=("dark_history",), narrative_function="deepen",
        word_count=40, est_spoken_seconds=60, active_status="active", script_body="x",
    )
    views = BeatRef(
        id="b-views", poi_id="p1", lenses=("waterways_views",), narrative_function="deepen",
        word_count=90, est_spoken_seconds=60, active_status="active", script_body="y",
    )
    snap = _snap(
        [poi],
        beats_by_poi={"p1": [views, dark]},
        lens_neighbors={
            "history": frozenset({"dark_history", "hidden_history"}),
            "dark_history": frozenset({"history"}),
        },
    )
    from src.tour.routing import summarise_route

    route = summarise_route(
        [poi], start_lat=48.8556, start_lng=2.3658, round_trip=False,
        duration_min=60, spine_area="Le Marais", routing_client=None,
    )
    plans = build_poi_beat_plans(route, snap, lenses=["history"])
    assert plans[0].beats[0].id == "b-dark", (
        "the child of the requested parent leads; the off-family beat follows"
    )
    # A leaf request stays exact: dark_history asked for by name still leads.
    leaf = build_poi_beat_plans(route, snap, lenses=["dark_history"])
    assert leaf[0].beats[0].id == "b-dark"
    # No request, no bias: the heavier beat keeps today's order.
    unbiased = build_poi_beat_plans(route, snap, lenses=None)
    assert unbiased[0].beats[0].id == "b-views"


# ---------------------------------------------------------------------------
# A lensed day never seats a lens-miss story stop; the valve discloses.
# ---------------------------------------------------------------------------


def _lens_poi(pid: str, lat: float, lng: float, *, tier: int = 5) -> POI:
    return _poi(pid, tier=tier, lat=lat, lng=lng, beat_count=0)


def _lens_beat(bid: str, pid: str, lens: str) -> BeatRef:
    return BeatRef(
        id=bid, poi_id=pid, lenses=(lens,), narrative_function="deepen",
        word_count=40, est_spoken_seconds=90, active_status="active", script_body="x",
    )


def _lens_snap(miss_ids: set[str], pois: list[POI]) -> CorpusSnapshot:
    return _snap(
        pois,
        beats_by_poi={
            p.id: [
                _lens_beat(f"{p.id}-b{i}", p.id,
                           "waterways_views" if p.id in miss_ids else "dark_history")
                for i in range(5)
            ]
            for p in pois
        },
        lens_neighbors={"dark_history": frozenset({"history"})},
    )


def test_a_lensed_day_never_seats_a_lens_miss_story_stop():
    """The measured fault: an architecture day seated the Orangerie, whose
    only beats answer a subject the visitor never asked for. On a lensed request, a story
    candidate with NOTHING in the asked family (the same direct/one-hop/miss the
    POI spotlight classifies by) leaves the dwell pool, and the planner replaces
    it — fewer honest stops beat a padded day. No request: untouched."""
    lat, lng = 48.8555, 2.3656
    pois = [
        _lens_poi(f"hit{i}", lat + 0.0005 + 0.0005 * i, lng) for i in range(1, 9)
    ] + [_lens_poi("miss1", lat + 0.0005, lng)]  # nearest and tier 5: seated today
    snap = _lens_snap({"miss1"}, pois)
    lensed = select_route(
        TourInput(start=(lat, lng), duration_min=30, city_slug="paris",
                  lenses=["dark_history"], round_trip=False),
        snap,
    )
    assert "miss1" not in {p.id for p in lensed.pois}, (
        f"the miss was seated on a lensed day: {[p.id for p in lensed.pois]}"
    )
    open_day = select_route(
        TourInput(start=(lat, lng), duration_min=30, city_slug="paris", round_trip=False),
        snap,
    )
    assert "miss1" in {p.id for p in open_day.pois}, (
        "premise: without a lens the miss is attractive enough to seat"
    )


def test_a_pinned_place_outranks_the_subject_gate():
    """A visitor pins the thing they came for; a pin is the person's own hand on the day
    and no subject gate second-guesses it.

    The mechanism is the FORCE-SEAT pass, not a carve-out in the gate itself:
    the gate may drop a pinned miss from the open pool, and the force-seat pass
    seats every held stop straight from the snapshot afterwards. UNDO: skip the
    force-seat pass (guard out its `if held_ids:` block) -> the gated pool never
    offers the pinned miss and nothing re-seats it -> RED."""
    lat, lng = 48.8555, 2.3656
    pois = [
        _lens_poi(f"hit{i}", lat + 0.0005 + 0.0005 * i, lng) for i in range(1, 9)
    ] + [_lens_poi("pinned-miss", lat + 0.0005, lng)]
    snap = _lens_snap({"pinned-miss"}, pois)
    route = select_route(
        TourInput(start=(lat, lng), duration_min=30, city_slug="paris",
                  lenses=["dark_history"], round_trip=False,
                  pinned_poi_ids=("pinned-miss",)),
        snap,
    )
    assert "pinned-miss" in {p.id for p in route.pois}


def test_the_valve_serves_the_day_and_says_so_when_everything_misses():
    """The Sorbonne rule, kept: a thin area where EVERY reachable place misses the
    subject must not become a refusal where it served before. The gate lifts, the
    day is built from what the city has, and the degradations channel says so —
    the disclosure ethos every other pool exclusion follows."""
    from src.tour.degradations import degradation_scope

    lat, lng = 48.8555, 2.3656
    pois = [
        _lens_poi(f"m{i}", lat + 0.001 * i, lng) for i in range(1, 7)
    ] + [_lens_poi("m0", lat + 0.0005, lng)]
    snap = _lens_snap({f"m{i}" for i in range(0, 7)}, pois)
    with degradation_scope() as rows:
        route = select_route(
            TourInput(start=(lat, lng), duration_min=30, city_slug="paris",
                      lenses=["dark_history"], round_trip=False),
            snap,
        )
    assert route.pois, "the day is served, never refused, when the area is thin"
    assert any(r.kind == "subject_gate_relaxed" for r in rows), (
        f"the valve must say so: {[r.kind for r in rows]}"
    )
