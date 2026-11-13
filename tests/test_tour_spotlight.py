"""Pure spotlight() scoring (gravity x lens_relevance x proximity).

These test the ADDITIVE pure functions only. They must NOT depend on (and must
not change) select_route's selection: the tier/lens gates stay in
place. The end=None identity baseline lives in
tests/test_tour_selection.py and is untouched here.

The scoring contract:
  spotlight(poi) = gravity(tier) x lens_relevance(poi, lenses) x proximity(detour)
  - gravity ∝ importance_tier (monotonic in 1..5)
  - lens_relevance = 1.0 direct | 0.6 parent/child 1-hop | LENS_FLOOR(0.25) miss
                     | 1.0 when lenses None/empty   (a miss DIMS, never zeroes)
  - proximity = 1.0 on-path, decaying with marginal_detour_seconds, always > 0
"""

from __future__ import annotations

import math
from itertools import pairwise

from src.tour.contract import POI, BeatRef
from src.tour.selection import (
    BAND_FULL,
    BAND_HEADLINE,
    BAND_LANDMARK_TIER,
    BAND_SHORT,
    BAND_SILENT,
    BAND_THRESHOLD_FULL,
    BAND_THRESHOLD_HEADLINE,
    BAND_THRESHOLD_SHORT,
    BAND_THRESHOLD_VIGNETTE,
    BAND_VIGNETTE,
    LENS_FLOOR,
    PROXIMITY_DECAY_SECONDS,
    CorpusSnapshot,
    band_for_spotlight,
    gravity,
    is_dwell_band,
    lens_relevance,
    proximity,
    spotlight,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _poi(pid: str, *, tier: int = 5, lat: float = 48.85, lng: float = 2.35) -> POI:
    return POI(id=pid, name=pid, tier=tier, poi_role="stop", lat=lat, lng=lng, beat_count=5)


def _lensed_beats(poi_id: str, lenses: tuple[str, ...], *, n: int = 3) -> list[BeatRef]:
    return [
        BeatRef(
            id=f"{poi_id}-b{i}",
            poi_id=poi_id,
            est_spoken_seconds=240,
            active_status="active",
            lenses=lenses,
        )
        for i in range(n)
    ]


def _snap(
    pois: list[POI],
    *,
    beats_by_poi: dict[str, list[BeatRef]] | None = None,
    lens_neighbors: dict[str, frozenset[str]] | None = None,
) -> CorpusSnapshot:
    beats = {k: tuple(v) for k, v in (beats_by_poi or {}).items()}
    return CorpusSnapshot(
        pois=tuple(pois),
        beats_by_poi=beats,
        area_types={},
        adjacent_areas={},
        lens_neighbors=lens_neighbors or {},
    )


# history -[:IS_PARENT_OF]-> dark_history, symmetric 1-hop map (as the loader builds it).
_HOP_MAP = {
    "history": frozenset({"dark_history"}),
    "dark_history": frozenset({"history"}),
}


# ---------------------------------------------------------------------------
# gravity(tier) — monotonic in tier
# ---------------------------------------------------------------------------


def test_gravity_strictly_monotonic_in_tier():
    values = [gravity(t) for t in (1, 2, 3, 4, 5)]
    assert values == sorted(values), "gravity must be non-decreasing in tier"
    # Strict: every step up in tier strictly increases gravity.
    for lo, hi in pairwise(values):
        assert hi > lo, "gravity must be STRICTLY increasing in tier"


def test_gravity_positive_for_all_valid_tiers():
    for t in (1, 2, 3, 4, 5):
        assert gravity(t) > 0.0


def test_gravity_reuses_linear_tier_weight():
    # Reuses poi_score's importance = float(tier); a tier-5 pulls 5x a tier-1.
    assert gravity(5) == 5.0 * gravity(1)


# ---------------------------------------------------------------------------
# lens_relevance — the direct / 1-hop / miss / no-lens matrix
# ---------------------------------------------------------------------------


def test_lens_relevance_no_lens_is_uniform_one():
    """No lenses requested → uniform 1.0 (unbiased)."""
    p = _poi("p")
    snap = _snap([p], beats_by_poi={"p": _lensed_beats("p", ("hidden_history",))})
    assert lens_relevance(p, lenses=None, snapshot=snap) == 1.0
    assert lens_relevance(p, lenses=frozenset(), snapshot=snap) == 1.0


def test_lens_relevance_direct_hit_is_one():
    p = _poi("p")
    snap = _snap([p], beats_by_poi={"p": _lensed_beats("p", ("hidden_history",))})
    assert lens_relevance(p, lenses=frozenset({"hidden_history"}), snapshot=snap) == 1.0


def test_lens_relevance_one_hop_is_point_six_both_directions():
    """A parent/child 1-hop via lens_neighbors scores 0.6 in BOTH directions:
    requesting the parent and reaching a child-lensed beat, and vice versa."""
    p = _poi("p")
    child_snap = _snap(
        [p],
        beats_by_poi={"p": _lensed_beats("p", ("dark_history",))},
        lens_neighbors=_HOP_MAP,
    )
    parent_snap = _snap(
        [p],
        beats_by_poi={"p": _lensed_beats("p", ("history",))},
        lens_neighbors=_HOP_MAP,
    )
    # request parent "history", beat is child "dark_history"
    assert lens_relevance(p, lenses=frozenset({"history"}), snapshot=child_snap) == 0.6
    # request child "dark_history", beat is parent "history"
    assert lens_relevance(p, lenses=frozenset({"dark_history"}), snapshot=parent_snap) == 0.6


def test_lens_relevance_miss_is_floor_not_zero():
    """A thematic miss DIMS to LENS_FLOOR (0.25) — it never zeroes."""
    p = _poi("p")
    snap = _snap(
        [p],
        beats_by_poi={"p": _lensed_beats("p", ("hidden_history",))},
        lens_neighbors=_HOP_MAP,
    )
    # request an unrelated lens with no direct/1-hop relation to hidden_history.
    rel = lens_relevance(p, lenses=frozenset({"war_conflict"}), snapshot=snap)
    assert rel == LENS_FLOOR
    assert rel > 0.0, "a miss must dim, never zero"
    assert LENS_FLOOR == 0.25


def test_lens_relevance_matrix_orders_direct_above_hop_above_miss():
    """Direct (1.0) > one-hop (0.6) > miss (0.25) > 0 — the full matrix order."""
    direct = lens_relevance(
        _poi("d"),
        lenses=frozenset({"history"}),
        snapshot=_snap(
            [_poi("d")],
            beats_by_poi={"d": _lensed_beats("d", ("history",))},
            lens_neighbors=_HOP_MAP,
        ),
    )
    hop = lens_relevance(
        _poi("h"),
        lenses=frozenset({"history"}),
        snapshot=_snap(
            [_poi("h")],
            beats_by_poi={"h": _lensed_beats("h", ("dark_history",))},
            lens_neighbors=_HOP_MAP,
        ),
    )
    miss = lens_relevance(
        _poi("m"),
        lenses=frozenset({"history"}),
        snapshot=_snap(
            [_poi("m")],
            beats_by_poi={"m": _lensed_beats("m", ("street_art",))},
            lens_neighbors=_HOP_MAP,
        ),
    )
    assert direct > hop > miss > 0.0


def test_lens_relevance_direct_wins_when_beat_has_both_direct_and_hop():
    """If a POI carries both a direct-hit beat lens and a 1-hop one, the direct
    hit (1.0) wins — direct dominates the classification."""
    p = _poi("p")
    snap = _snap(
        [p],
        beats_by_poi={"p": _lensed_beats("p", ("history", "dark_history"))},
        lens_neighbors=_HOP_MAP,
    )
    assert lens_relevance(p, lenses=frozenset({"history"}), snapshot=snap) == 1.0


# ---------------------------------------------------------------------------
# proximity — 1.0 on-path, decays with detour, always > 0
# ---------------------------------------------------------------------------


def test_proximity_on_path_is_one():
    assert proximity(0.0) == 1.0
    assert proximity() == 1.0


def test_proximity_strictly_decreasing_in_detour():
    detours = [0.0, 60.0, 300.0, 600.0, 1800.0]
    vals = [proximity(d) for d in detours]
    for lo, hi in pairwise(vals):
        assert hi < lo, "proximity must STRICTLY decrease as detour grows"


def test_proximity_never_zeroes():
    # Even a huge detour keeps a positive (if tiny) factor — proximity alone
    # never silences a POI (the silence invariant).
    assert proximity(36_000.0) > 0.0


def test_proximity_efolding_constant():
    # At exactly PROXIMITY_DECAY_SECONDS the factor is 1/e.
    assert math.isclose(proximity(PROXIMITY_DECAY_SECONDS), 1.0 / math.e, rel_tol=1e-9)


def test_proximity_clamps_negative_detour_to_one():
    # Integer routing rounding can yield a tiny negative marginal detour; it is
    # clamped so proximity never exceeds the on-path 1.0.
    assert proximity(-50.0) == 1.0


# ---------------------------------------------------------------------------
# spotlight — the product, and the silence invariants
# ---------------------------------------------------------------------------


def test_spotlight_is_product_of_the_three_factors():
    p = _poi("p", tier=4)
    snap = _snap([p], beats_by_poi={"p": _lensed_beats("p", ("hidden_history",))})
    detour = 300.0
    lenses = frozenset({"hidden_history"})
    expected = (
        gravity(p.tier)
        * lens_relevance(p, lenses=lenses, snapshot=snap)
        * proximity(detour)
    )
    got = spotlight(p, lenses=lenses, snapshot=snap, marginal_detour_seconds=detour)
    assert math.isclose(got, expected, rel_tol=1e-12)


def test_spotlight_lens_miss_dims_but_never_zeroes():
    """A lens miss multiplies in LENS_FLOOR, dimming the score — but a real POI
    on the path still has a STRICTLY POSITIVE spotlight (lens alone never
    silences). Compared to a direct hit, the miss is exactly LENS_FLOORx lower."""
    miss_poi = _poi("miss", tier=5)
    hit_poi = _poi("hit", tier=5)
    snap = _snap(
        [miss_poi, hit_poi],
        beats_by_poi={
            "miss": _lensed_beats("miss", ("street_art",)),
            "hit": _lensed_beats("hit", ("war_conflict",)),
        },
    )
    lenses = frozenset({"war_conflict"})
    s_miss = spotlight(miss_poi, lenses=lenses, snapshot=snap)
    s_hit = spotlight(hit_poi, lenses=lenses, snapshot=snap)
    assert s_miss > 0.0, "a lens miss must never zero the spotlight"
    assert s_miss < s_hit
    assert math.isclose(s_miss / s_hit, LENS_FLOOR, rel_tol=1e-9)


def test_spotlight_high_gravity_miss_beats_low_gravity_hit():
    """A tier-5 off-genre landmark still out-spotlights a tier-1 on-genre
    footnote at the same proximity (5 x 0.25 = 1.25 > 1 x 1.0). Gravity can
    rescue an off-genre landmark from silence — the only silence is low-gravity
    AND off-genre."""
    big_miss = _poi("big", tier=5)
    small_hit = _poi("small", tier=1)
    snap = _snap(
        [big_miss, small_hit],
        beats_by_poi={
            "big": _lensed_beats("big", ("street_art",)),
            "small": _lensed_beats("small", ("war_conflict",)),
        },
    )
    lenses = frozenset({"war_conflict"})
    assert spotlight(big_miss, lenses=lenses, snapshot=snap) > spotlight(
        small_hit, lenses=lenses, snapshot=snap
    )


def test_spotlight_monotonic_in_gravity_holding_lens_and_proximity_fixed():
    snap_by_tier = {}
    for t in (1, 2, 3, 4, 5):
        pid = f"t{t}"
        snap_by_tier[t] = (
            _poi(pid, tier=t),
            _snap([_poi(pid, tier=t)], beats_by_poi={pid: _lensed_beats(pid, ("hidden_history",))}),
        )
    lenses = frozenset({"hidden_history"})
    scores = [
        spotlight(p, lenses=lenses, snapshot=s)
        for p, s in (snap_by_tier[t] for t in (1, 2, 3, 4, 5))
    ]
    for lo, hi in pairwise(scores):
        assert hi > lo


def test_spotlight_decays_with_detour_holding_gravity_and_lens_fixed():
    p = _poi("p", tier=4)
    snap = _snap([p], beats_by_poi={"p": _lensed_beats("p", ("hidden_history",))})
    lenses = frozenset({"hidden_history"})
    near = spotlight(p, lenses=lenses, snapshot=snap, marginal_detour_seconds=60.0)
    far = spotlight(p, lenses=lenses, snapshot=snap, marginal_detour_seconds=1800.0)
    on_path = spotlight(p, lenses=lenses, snapshot=snap, marginal_detour_seconds=0.0)
    assert on_path > near > far > 0.0


def test_spotlight_no_lens_uses_uniform_relevance():
    """With no lenses, spotlight reduces to gravity x proximity (relevance 1.0)."""
    p = _poi("p", tier=3)
    snap = _snap([p], beats_by_poi={"p": _lensed_beats("p", ("anything",))})
    got = spotlight(p, lenses=None, snapshot=snap, marginal_detour_seconds=0.0)
    assert math.isclose(got, gravity(3) * 1.0 * 1.0, rel_tol=1e-12)


# ---------------------------------------------------------------------------
# band_for_spotlight: spotlight -> headline/full/short/vignette/silent
# ---------------------------------------------------------------------------
#
# The thresholds are lower-inclusive cut points on the spotlight score
# (BAND_THRESHOLD_*). For a high-tier POI (tier >= BAND_LANDMARK_TIER) the
# silence invariant floors the band at vignette regardless of score.


def test_band_thresholds_are_ordered_and_positive():
    # The cut points must be a strictly descending positive ladder, else the
    # if/elif chain in band_for_spotlight would shadow a band.
    cuts = [
        BAND_THRESHOLD_HEADLINE,
        BAND_THRESHOLD_FULL,
        BAND_THRESHOLD_SHORT,
        BAND_THRESHOLD_VIGNETTE,
    ]
    for hi, lo in pairwise(cuts):
        assert hi > lo > 0.0


def test_band_headline_boundary_lower_inclusive():
    # At exactly the headline cut -> headline; a hair below -> full.
    assert band_for_spotlight(BAND_THRESHOLD_HEADLINE, tier=5) == BAND_HEADLINE
    assert band_for_spotlight(BAND_THRESHOLD_HEADLINE + 1.0, tier=5) == BAND_HEADLINE
    assert band_for_spotlight(BAND_THRESHOLD_HEADLINE - 1e-9, tier=5) == BAND_FULL


def test_band_full_boundary_lower_inclusive():
    assert band_for_spotlight(BAND_THRESHOLD_FULL, tier=4) == BAND_FULL
    assert band_for_spotlight(BAND_THRESHOLD_FULL - 1e-9, tier=4) == BAND_SHORT


def test_band_short_boundary_lower_inclusive():
    assert band_for_spotlight(BAND_THRESHOLD_SHORT, tier=2) == BAND_SHORT
    assert band_for_spotlight(BAND_THRESHOLD_SHORT - 1e-9, tier=2) == BAND_VIGNETTE


def test_band_vignette_boundary_lower_inclusive_for_low_tier():
    # At the vignette cut -> vignette; below it a LOW-tier POI goes silent.
    assert band_for_spotlight(BAND_THRESHOLD_VIGNETTE, tier=1) == BAND_VIGNETTE
    assert band_for_spotlight(BAND_THRESHOLD_VIGNETTE - 1e-9, tier=1) == BAND_SILENT


def test_band_full_ladder_via_real_spotlight_scores():
    """Drive the classifier with REAL spotlight scores (on-path, proximity 1.0)
    so the thresholds are exercised against the actual gravity x lens product,
    not hand-fed numbers."""

    def band(tier: int, lens: str) -> str:
        p = _poi("a", tier=tier)
        s = _snap(
            [p],
            beats_by_poi={"a": _lensed_beats("a", (lens,))},
            lens_neighbors=_HOP_MAP,
        )
        score = spotlight(p, lenses=frozenset({"history"}), snapshot=s)
        return band_for_spotlight(score, tier=tier)

    # Calibrated cuts: headline >=5, full >=4, short >=3 (dwell floor),
    # vignette >=0.5, else silent (tier>=4 landmark floored at vignette).
    # tier-5 direct (5.0) -> headline.
    assert band(5, "history") == BAND_HEADLINE
    # tier-4 direct (4.0) -> full.
    assert band(4, "history") == BAND_FULL
    # tier-5 one-hop (3.0) and tier-3 direct (3.0) -> short: the dwell floor.
    assert band(5, "dark_history") == BAND_SHORT
    assert band(3, "history") == BAND_SHORT
    # Below the dwell floor -> vignette: tier-2 direct (2.0), tier-1 direct (1.0),
    # and a tier-5 off-genre landmark (1.25) dimmed to a brief mention.
    assert band(2, "history") == BAND_VIGNETTE
    assert band(1, "history") == BAND_VIGNETTE
    assert band(5, "street_art") == BAND_VIGNETTE
    # tier-1 one-hop (0.6) -> vignette; tier-2 miss (0.5) -> vignette.
    assert band(1, "dark_history") == BAND_VIGNETTE
    assert band(2, "street_art") == BAND_VIGNETTE
    # tier-1 miss (0.25) -> SILENT (low gravity AND off-genre).
    assert band(1, "street_art") == BAND_SILENT


def test_silence_invariant_high_gravity_off_genre_is_not_silent():
    """Silence invariant: a high-gravity off-genre landmark clears to at least
    vignette -- lens alone never silences it, and even a huge proximity detour
    that drives its score below the silent cut must NOT make it silent."""
    # tier-5 off-genre on-path (score 1.25) is a vignette (a brief mention) --
    # below the calibrated dwell floor (3.0) but well clear of silence.
    assert band_for_spotlight(5.0 * LENS_FLOOR, tier=5) == BAND_VIGNETTE
    # Now drive the score arbitrarily low (as a huge detour would) -- the
    # landmark guard floors it at vignette, never silent.
    for landmark_tier in range(BAND_LANDMARK_TIER, 6):
        b = band_for_spotlight(0.0, tier=landmark_tier)
        assert b == BAND_VIGNETTE, f"tier {landmark_tier} must never be silent"
        assert b != BAND_SILENT


def test_silence_invariant_low_gravity_off_genre_is_silent():
    """Silence invariant, other half: a genuinely low-gravity off-genre POI (its
    score fell below the vignette cut) IS silent -- that is the ONLY silence."""
    # tier-1 off-genre miss = 0.25 < BAND_THRESHOLD_VIGNETTE -> silent.
    miss_score = 1.0 * LENS_FLOOR
    assert miss_score < BAND_THRESHOLD_VIGNETTE
    for low_tier in range(1, BAND_LANDMARK_TIER):
        assert band_for_spotlight(miss_score, tier=low_tier) == BAND_SILENT


def test_silence_invariant_via_real_spotlight_low_vs_high_gravity():
    """End-to-end through spotlight(): same off-genre lens, the low-tier POI
    goes silent while the high-tier landmark does not."""
    low = _poi("low", tier=1)
    high = _poi("high", tier=5)
    snap = _snap(
        [low, high],
        beats_by_poi={
            "low": _lensed_beats("low", ("street_art",)),
            "high": _lensed_beats("high", ("street_art",)),
        },
    )
    lenses = frozenset({"war_conflict"})
    low_band = band_for_spotlight(
        spotlight(low, lenses=lenses, snapshot=snap), tier=low.tier
    )
    high_band = band_for_spotlight(
        spotlight(high, lenses=lenses, snapshot=snap), tier=high.tier
    )
    assert low_band == BAND_SILENT
    assert high_band != BAND_SILENT


def test_dwell_vs_vignette_collapse():
    """The output-facing collapse: headline/full/short are dwell stops;
    vignette and silent are NOT dwell."""
    assert is_dwell_band(BAND_HEADLINE)
    assert is_dwell_band(BAND_FULL)
    assert is_dwell_band(BAND_SHORT)
    assert not is_dwell_band(BAND_VIGNETTE)
    assert not is_dwell_band(BAND_SILENT)
