"""Density gating.

Density gating guards against two failure modes that the selection
guards alone do not catch:

1. A sparse starting point yields a mostly silent tour — under 11% audio
   fill, such as 5 minutes of audio over a 1.3 km, 90-minute walk. The
   empirical walks run 70-80% audio fill.
2. A tier-3+ POI with **zero active beats** gets picked as an anchor,
   because routing-aware scoring does not look at the beat count.

Density gating answers "given a (start, duration, round_trip), is the
reachable beat pool rich enough to support an honest tour?" before any
selection runs. Three outcomes:

- **GREEN** — generate as normal.
- **YELLOW** — generate but warn explicitly; recommend a longer-fill
  alternative duration when one exists.
- **RED** — refuse to generate; return actionable alternatives (shorter
  duration, one-way to a denser destination, or a different start).

Runtime cost: one envelope-radius haversine sweep over the POI
roster (303 POIs in Paris) — well under 50 ms.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, NamedTuple

from .contract import POI, BeatRef, TourabilityAssessment, TourInput
from .routing import (
    DEFAULT_ROUTE_PLANNING_POLICY,
    RoutePlanningPolicy,
    beat_spoken_seconds,
    envelope_radius_m,
    haversine_m,
    route_planning_budget,
)
from .visit_time import stop_seconds, visit_seconds

if TYPE_CHECKING:
    from .selection import CorpusSnapshot

# Status thresholds, frozen at calibration.
GREEN_FILL_RATIO_MIN: float = 1.0
# Empirical audio-fill observed on the reference walks (module docstring:
# "The empirical walks run 70-80% audio fill"). Display-honesty threshold
# only — the banner may claim "below the 70-80% bar" solely under this value;
# classification does NOT use it.
EMPIRICAL_FILL_BAR: float = 0.70
GREEN_ANCHOR_CANDIDATES_MIN: int = 4
GREEN_CLUSTER_COMPACTNESS_MAX: float = 0.6

YELLOW_FILL_RATIO_MIN: float = 0.5
YELLOW_ANCHOR_CANDIDATES_MIN: int = 3
YELLOW_CLUSTER_COMPACTNESS_MAX: float = 0.7

# Calibration (2026-04-29): the canonical compactness check
# (≤ 0.6 for GREEN) misfires for long one-way tours where rich anchor
# candidates legitimately spread across the envelope. The empirical
# Île de la Cité one-way 90min from Pont Neuf scores fill=3.95 (4x
# target), anchors=28 (well above min), but compactness=0.74 because
# the candidate pool genuinely spans the 1.1km traverse from end to
# end. The literal formula classifies it RED, but the user-facing gate
# requires Tours 1, 2, 4 to remain GREEN. Resolution: a "rich-pool"
# escape hatch that bypasses the compactness ceiling when fill *and*
# anchor count both clear higher bars. This preserves the formula's
# protection against thin tours (Sacré-Cœur 5% fill, Pantheon 11% fill)
# while letting genuinely-dense traverses through.
RICH_POOL_FILL_RATIO_MIN: float = 1.5
RICH_POOL_ANCHOR_CANDIDATES_MIN: int = 6

# Anchor candidate definition: tier ≥ 3 and at least this many active
# beats. Excludes 0-beat POIs even if they would otherwise score well
# (the Petit Palais bug — tier 4, 0 beats, selected anyway).
ANCHOR_CANDIDATE_TIER_MIN: int = 3
ANCHOR_CANDIDATE_BEAT_COUNT_MIN: int = 3

# Eligible POI roles for density (mirrors selection.py's filter).
ELIGIBLE_POI_ROLES: frozenset[str] = frozenset({"stop", "setting"})


class FeasibilityAlternative(NamedTuple):
    """One actionable way out of a feasibility refusal.

    ``kind`` is the alternative class:
    - ``"loop"`` — drop the fixed destination B and walk an open loop from A.
    - ``"extend"`` — keep B but lengthen the tour to ``duration_min`` so the
      routed A→B leg fits inside the walk budget.
    - ``"closer_b"`` — keep the requested duration but aim the user
      at a nearer destination B' that IS reachable inside the walk budget.
      Carries the chosen anchor's ``poi_id``/``lat``/``lng`` so the harness can
      offer it as the new endpoint.
    ``duration_min`` is the recommended duration for the alternative (the
    requested duration for ``loop`` and ``closer_b``; the A→B-correct extended
    duration for ``extend``). ``drop_end`` is True when the alternative discards
    the originally-requested B (``loop`` and ``closer_b``; ``closer_b`` swaps in
    its own target).

    ``poi_id``/``lat``/``lng`` carry the closer_b target. They default to None
    and stay None for ``loop`` and ``extend`` (which carry no target POI).
    """

    kind: str
    duration_min: int
    drop_end: bool
    poi_id: str | None = None
    lat: float | None = None
    lng: float | None = None


class TourabilityRefusedError(Exception):
    """Raised by selection when a tour cannot be built.

    Two refusal causes share this exception:

    1. **Density RED** — ``density.assess()`` returns RED. Carries the
       ``assessment`` so the harness can surface fill_ratio,
       anchor_candidate_count, and the recommended alternatives.
    2. **Fixed-destination infeasibility** — a fixed end B exists but
       the routed A→B leg already exceeds the walk budget, so no in-budget tour
       can reach B. Carries ``gap_minutes`` (how many minutes the A→B leg
       overshoots the walk budget) plus a tuple of ``alternatives``
       (``FeasibilityAlternative`` — a 'loop' from A, an 'extend' to a longer
       duration, and, when at least one anchor is reachable inside the walk
       budget, a 'closer_b' pointing at a nearer destination B'). The
       ``assessment`` is still attached for context.
    """

    def __init__(
        self,
        assessment: TourabilityAssessment,
        message: str | None = None,
        *,
        gap_minutes: int | None = None,
        alternatives: tuple[FeasibilityAlternative, ...] = (),
    ):
        self.assessment = assessment
        self.gap_minutes = gap_minutes
        self.alternatives = alternatives
        super().__init__(message or self._default_message(assessment))

    @staticmethod
    def _default_message(a: TourabilityAssessment) -> str:
        return (
            f"Tour density RED: fill_ratio={a.fill_ratio:.2f}, "
            f"anchor_candidates={a.anchor_candidate_count}, "
            f"compactness={a.cluster_compactness:.2f}. "
            f"Insufficient corpus for a {a.duration_min}-min "
            f"{'round-trip' if a.round_trip else 'one-way'} tour."
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def assess(
    tour_input: TourInput,
    pois: Iterable[POI],
    beats_by_poi: dict[str, tuple[BeatRef, ...]],
    *,
    planning_policy: RoutePlanningPolicy = DEFAULT_ROUTE_PLANNING_POLICY,
    snapshot: CorpusSnapshot | None = None,
) -> TourabilityAssessment:
    """Low-level component assessment retained for legacy diagnostics/tests.

    Product selection must call :func:`assess_snapshot`, which refuses typed
    place evidence until the validating materializer has relocated or omitted
    every beat.  This detached-components form cannot carry that proof.

    ``snapshot`` is optional and exists only so the capacity measure can price a
    place against the requested lenses. Without it the pool is priced as if no
    interest were declared, which is honest rather than merely tolerant: a
    detached component list cannot answer "does this place speak to dark
    history?" at all.
    """
    start_lat, start_lng = tour_input.start
    duration_min = tour_input.duration_min
    round_trip = tour_input.round_trip

    # THE GATE AND THE PLANNER MUST SHRINK TOGETHER.
    # `select_route` derives this same multiplier from the same
    # `TourInput.walking_pace` field; reading it here rather than threading a
    # second parameter is what keeps the two readings from drifting apart.
    pace_multiplier = tour_input.walking_pace if tour_input.walking_pace is not None else 1.0
    walk_radius_m = envelope_radius_m(
        duration_min,
        round_trip=round_trip,
        planning_policy=planning_policy,
        pace_multiplier=pace_multiplier,
    )

    # Reachable POIs: within walk envelope AND eligible role AND at
    # least one active beat (zero-beat POIs are diagnostic noise, not
    # tour content; the Petit Palais bug).
    # Lens-specific fill: count ONLY the standing-still time the requested lenses
    # would earn, so the surface can disclose "thin for YOUR interest"
    # (dark_history vs hidden_history) rather than a lens-agnostic pool number
    # identical for both.
    requested_lenses = {s.lower() for s in (tour_input.lenses or [])}
    # Pricing a place against a declared interest needs the lens graph. The
    # legacy detached-components form of this function has no snapshot, so it
    # prices as an undeclared visitor rather than guessing a lens miss — see
    # visit_seconds, which refuses the guess outright.
    interest = frozenset(requested_lenses) if snapshot is not None else frozenset()

    reachable_pois: list[POI] = []
    dwell_capacity_s = 0
    on_lens_dwell_s = 0
    reachable_beat_count = 0
    for poi in pois:
        if poi.poi_role not in ELIGIBLE_POI_ROLES:
            continue
        if haversine_m(start_lat, start_lng, poi.lat, poi.lng) > walk_radius_m:
            continue
        beats = beats_by_poi.get(poi.id, ())
        active = [b for b in beats if (b.active_status or "active") == "active"]
        if not active:
            continue
        reachable_pois.append(poi)
        poi_audio_s = 0
        poi_on_lens_audio_s = 0
        for beat in active:
            secs = _beat_spoken_seconds(beat)
            poi_audio_s += secs
            if requested_lenses and requested_lenses.intersection(
                s.lower() for s in beat.lenses
            ):
                poi_on_lens_audio_s += secs
        # BOTH HALVES OF THE RATIO ARE NOW STANDING-STILL SECONDS. Until
        # 2026-08-06 the numerator was beat seconds and the denominator became a
        # dwell target, which would have judged a pool measured in narration
        # against a target measured in visits — the identical drift this
        # module's own history records, where the gate judged 1793 s against a
        # planner aiming at 2160 s and served a thin tour as healthy.
        #
        # Audio here is UNCAPPED on purpose. This is a measure of what the POOL
        # holds, not of what one route would emit, and it is the currency
        # selection.py's delivered-thin check has always used.
        visit_s = visit_seconds(poi, interest, snapshot)
        dwell_capacity_s += stop_seconds(visit_s, poi_audio_s)
        # "Thin for YOUR interest": a place that says nothing about the declared
        # lens contributes nothing here, and one that does contributes what this
        # visitor would actually spend there. A dark-history visitor spends 65
        # minutes inside the Conciergerie BECAUSE it matches that interest.
        if poi_on_lens_audio_s:
            on_lens_dwell_s += stop_seconds(visit_s, poi_on_lens_audio_s)
        reachable_beat_count += len(active)

    target_dwell_s = _target_dwell_seconds(duration_min, planning_policy)
    fill_ratio = dwell_capacity_s / target_dwell_s if target_dwell_s > 0 else 0.0
    on_lens_fill_ratio: float | None = (
        (on_lens_dwell_s / target_dwell_s if target_dwell_s > 0 else 0.0)
        if requested_lenses
        else None
    )

    anchor_candidates = [
        p
        for p in reachable_pois
        if p.tier >= ANCHOR_CANDIDATE_TIER_MIN
        and (
            len(
                [b for b in beats_by_poi.get(p.id, ()) if (b.active_status or "active") == "active"]
            )
            >= ANCHOR_CANDIDATE_BEAT_COUNT_MIN
        )
    ]
    cluster_compactness = _compactness(anchor_candidates, walk_radius_m)

    status = _status(
        fill_ratio=fill_ratio,
        anchor_candidate_count=len(anchor_candidates),
        cluster_compactness=cluster_compactness,
    )

    max_supportable: int | None = None
    one_way_alternative: str | None = None
    if status != "GREEN":
        # Diagnostic: the duration where fill_ratio would equal 1.0 at
        # the current dwell capacity. Only meaningful when the limiting
        # factor is fill_ratio (not anchor count or compactness).
        if dwell_capacity_s > 0:
            max_supportable = _duration_where_fill_equals_one(
                dwell_capacity_s, planning_policy
            )

        # Round-trip-limited alternative: if the round-trip envelope
        # was the bottleneck, find the densest one-way destination
        # reachable inside the equivalent one-way envelope.
        if round_trip:
            one_way_alternative = _suggest_one_way_destination(
                start_lat=start_lat,
                start_lng=start_lng,
                duration_min=duration_min,
                pois=list(pois),
                beats_by_poi=beats_by_poi,
                planning_policy=planning_policy,
            )

    return TourabilityAssessment(
        status=status,
        walk_radius_m=walk_radius_m,
        fill_ratio=fill_ratio,
        dwell_capacity_seconds=dwell_capacity_s,
        target_dwell_seconds=target_dwell_s,
        reachable_poi_count=len(reachable_pois),
        reachable_beat_count=reachable_beat_count,
        anchor_candidate_count=len(anchor_candidates),
        cluster_compactness=cluster_compactness,
        duration_min=duration_min,
        round_trip=round_trip,
        max_supportable_duration_min=max_supportable,
        one_way_alternative_destination=one_way_alternative,
        on_lens_fill_ratio=on_lens_fill_ratio,
    )


def assess_snapshot(
    tour_input: TourInput,
    snapshot: CorpusSnapshot,
    *,
    planning_policy: RoutePlanningPolicy = DEFAULT_ROUTE_PLANNING_POLICY,
) -> TourabilityAssessment:
    """Manifest-aware density boundary used by the product algorithm."""

    if snapshot.place_manifest is not None:
        from .place_materialization import validate_materialized_corpus_snapshot
        from .selection import MaterializedCorpusSnapshot

        if not isinstance(snapshot, MaterializedCorpusSnapshot):
            raise ValueError("typed corpus must be materialized before density")
        validate_materialized_corpus_snapshot(snapshot)
    return assess(  # type: ignore[arg-type]
        tour_input,
        snapshot.pois,
        snapshot.beats_by_poi,
        planning_policy=planning_policy,
        snapshot=snapshot,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _target_dwell_seconds(duration_min: int, planning_policy: RoutePlanningPolicy) -> int:
    """The planner's OWN standing-still target — the number selection then fills.

    Derived from the planning budget rather than restated here. Until 2026-08-04 this
    was a bare ``duration_min x 0.83 x 0.6 x 60``, which no policy could reach: after
    the walk envelope started following the certification policy, the gate would have
    judged a pool against 1793 s while the planner aimed at 2160 s — reading GREEN on a
    pool holding barely half the content the tour needed, which is a thin tour served as
    healthy, exactly what this module exists to prevent.

    That history is why the numerator moved in the SAME step as this rename on
    2026-08-06: a target measured in visits against a pool measured in narration
    is the same defect wearing different clothes.
    """
    return route_planning_budget(duration_min, planning_policy).dwell_target_seconds


def _beat_spoken_seconds(beat: BeatRef) -> int:
    """Voiced seconds for one beat (delegates to the shared routing clock)."""
    return beat_spoken_seconds(beat)


def _compactness(anchors: list[POI], walk_radius_m: float) -> float:
    """Mean pairwise haversine / walk radius; 0.0 = colocated, 1.0 = on edge.

    Returns 0.0 for <2 anchors (degenerate) and clamps to [0, 1] in case
    a candidate sneaks past the envelope filter (it shouldn't).
    """
    n = len(anchors)
    if n < 2 or walk_radius_m <= 0:
        return 0.0
    total = 0.0
    pair_count = 0
    for i in range(n):
        for j in range(i + 1, n):
            total += haversine_m(anchors[i].lat, anchors[i].lng, anchors[j].lat, anchors[j].lng)
            pair_count += 1
    if pair_count == 0:
        return 0.0
    mean = total / pair_count
    return min(1.0, max(0.0, mean / walk_radius_m))


def _status(
    *,
    fill_ratio: float,
    anchor_candidate_count: int,
    cluster_compactness: float,
) -> str:
    """The status table (with the rich-pool calibration).

    GREEN  if (fill ≥ 1.0 AND anchors ≥ 4 AND compactness ≤ 0.6)
        OR (fill ≥ 1.5 AND anchors ≥ 6)   # rich-pool escape hatch
    YELLOW if 0.5 ≤ fill < 1.0
        OR (anchors ≥ 3 AND compactness ≤ 0.7)
    RED    otherwise

    The rich-pool clause keeps long one-way traverses with genuinely
    dense corpora out of RED. See module docstring for the calibration
    rationale (Île one-way 90min: fill 3.95, anchors 28, compactness
    0.74 — healthy in practice).
    """
    canonical_green = (
        fill_ratio >= GREEN_FILL_RATIO_MIN
        and anchor_candidate_count >= GREEN_ANCHOR_CANDIDATES_MIN
        and cluster_compactness <= GREEN_CLUSTER_COMPACTNESS_MAX
    )
    rich_pool_green = (
        fill_ratio >= RICH_POOL_FILL_RATIO_MIN
        and anchor_candidate_count >= RICH_POOL_ANCHOR_CANDIDATES_MIN
    )
    if canonical_green or rich_pool_green:
        return "GREEN"

    yellow_by_fill = YELLOW_FILL_RATIO_MIN <= fill_ratio < GREEN_FILL_RATIO_MIN
    # Calibration (2026-04-29): the literal status table allowed
    # the anchor-disjunct to fire alone. That let catastrophically thin
    # tours (Sacré-Cœur 90min RT at fill 0.17 with 4 colocated anchors)
    # slip through as YELLOW instead of RED — contradicting the expectation
    # that such a round trip is refused. Adding the same fill
    # floor as the by-fill clause (≥ 0.5) closes that loophole while
    # still preserving the disjunct as a "many tight anchors" pathway
    # for tours where fill is borderline-but-not-catastrophic.
    yellow_by_anchors = (
        anchor_candidate_count >= YELLOW_ANCHOR_CANDIDATES_MIN
        and cluster_compactness <= YELLOW_CLUSTER_COMPACTNESS_MAX
        and fill_ratio >= YELLOW_FILL_RATIO_MIN
    )
    # Monotonicity in fill (2026-07-02): a pool with
    # MORE than enough audio (fill >= 1.0) that misses GREEN on anchors or
    # compactness previously fell through every clause to RED — refusing as
    # "insufficient corpus" while holding a surplus, and (worse) adding audio
    # capacity could flip an accept into a refusal (fill 0.9 -> YELLOW but
    # fill 1.2 with 2 anchors -> RED). Surplus fill is always at least
    # YELLOW: generate, but warn about the spread/thin-anchor shape.
    yellow_by_surplus_fill = fill_ratio >= GREEN_FILL_RATIO_MIN
    if yellow_by_fill or yellow_by_anchors or yellow_by_surplus_fill:
        return "YELLOW"

    return "RED"


def _duration_where_fill_equals_one(
    dwell_capacity_s: int, planning_policy: RoutePlanningPolicy
) -> int:
    """Solve target_dwell_s(d) = dwell_capacity_s for d.

    The per-minute dwell target is linear in duration, so one minute's worth of it
    divides the capacity directly. Round down to the nearest minute; floor at 1 to
    avoid a degenerate 0.

    This is the third reader of the gate's currency, and it has to move with the
    other two: solving a dwell-second capacity against a narration-second target
    would answer "the longest tour this area supports" in the wrong unit.
    """
    if dwell_capacity_s <= 0:
        return 1
    seconds_per_minute = route_planning_budget(1, planning_policy).dwell_target_seconds
    if seconds_per_minute <= 0:
        return 1
    return max(1, int(dwell_capacity_s / seconds_per_minute))


def _suggest_one_way_destination(
    *,
    start_lat: float,
    start_lng: float,
    duration_min: int,
    pois: list[POI],
    beats_by_poi: dict[str, tuple[BeatRef, ...]],
    planning_policy: RoutePlanningPolicy = DEFAULT_ROUTE_PLANNING_POLICY,
) -> str | None:
    """Highest-density tier-5 POI inside the equivalent one-way envelope.

    Used only when round_trip was the bottleneck. The recommended
    destination should be visibly farther than the round-trip envelope
    so the suggestion is meaningful.
    """
    one_way_radius = envelope_radius_m(
        duration_min, round_trip=False, planning_policy=planning_policy
    )
    round_trip_radius = envelope_radius_m(
        duration_min, round_trip=True, planning_policy=planning_policy
    )
    best_name: str | None = None
    best_score = -1.0
    for poi in pois:
        if poi.poi_role not in ELIGIBLE_POI_ROLES:
            continue
        if poi.tier < 5:
            continue
        d = haversine_m(start_lat, start_lng, poi.lat, poi.lng)
        if d > one_way_radius:
            continue
        if d <= round_trip_radius:
            continue  # already inside the limiting envelope; not a "different" suggestion
        beats = beats_by_poi.get(poi.id, ())
        active = [b for b in beats if (b.active_status or "active") == "active"]
        if not active:
            continue
        # Score = beat count x distance bonus (farther = more meaningful suggestion).
        score = float(len(active)) * (d / one_way_radius)
        if score > best_score:
            best_score = score
            best_name = poi.name
    return best_name


__all__ = [
    "ANCHOR_CANDIDATE_BEAT_COUNT_MIN",
    "ANCHOR_CANDIDATE_TIER_MIN",
    "GREEN_ANCHOR_CANDIDATES_MIN",
    "GREEN_CLUSTER_COMPACTNESS_MAX",
    "GREEN_FILL_RATIO_MIN",
    "YELLOW_ANCHOR_CANDIDATES_MIN",
    "YELLOW_CLUSTER_COMPACTNESS_MAX",
    "YELLOW_FILL_RATIO_MIN",
    "FeasibilityAlternative",
    "TourabilityAssessment",
    "TourabilityRefusedError",
    "assess",
    "assess_snapshot",
]
