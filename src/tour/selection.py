"""POI selection — §3.2 of phase-1-design.

Two entry points:

- ``load_paris_corpus(driver, ...)`` — pulls a CorpusSnapshot of POIs +
  beat metadata from Neo4j. City-scoped per CLAUDE.md guardrails. The
  city_slug used at the call site is mandatory; this is the only
  Neo4j-aware function in tour/. All POIs/Beats arrive frozen.
- ``select_route(input, snapshot)`` — pure function. Computes the route
  envelope, picks the spine area, scores each POI, and runs a
  routing-aware greedy until budget is exhausted (§3.2 cap = 12 outer
  anchors per Q5 clarification). Returns a Route.

The Cypher query is the only place where Neo4j-specific assumptions
live. Keep it deliberate and well-commented.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import InitVar, dataclass, replace
from dataclasses import field as dataclass_field
from datetime import datetime, time, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING

from shapely.errors import ShapelyError as _ShapelyError
from shapely.geometry import Point as _ShapelyPoint
from shapely.geometry import shape as _shapely_shape
from shapely.prepared import prep as _shapely_prep

from .beat_select import (
    _find_closing_friendly_index,
    extra_beat_ids,
    govern_poi_beats,
    select_poi_beats,
)
from .contract import (
    END_B_SENTINEL_NAME,
    END_B_SENTINEL_PREFIX,
    POI,
    Anchor,
    BeatRef,
    ClockExclusion,
    PhysicalCue,
    POIBeats,
    Promise,
    PromiseShape,
    ReachVerdict,
    ReplanContext,
    Route,
    TourabilityAssessment,
    TourInput,
)
from .corpus_places import CorpusMaterializationPlan, CorpusPlaceManifest
from .degradations import record
from .density import FeasibilityAlternative, TourabilityRefusedError
from .density import assess_snapshot as assess_tourability
from .ordering import order_stops
from .routing import (
    DEFAULT_ROUTE_PLANNING_POLICY,
    EARTH_RADIUS_M,
    REACH_PACE_KMH,
    TIMEBOX_MATERIALITY_TOLERANCE_SECONDS,
    WALK_FRACTION,
    LegSecondsFn,
    RoutePlanningBudget,
    RoutePlanningPolicy,
    civil_dusk_local,
    default_leg_seconds,
    envelope_radius_m,
    haversine_m,
    insertion_cost_seconds,
    insertion_extra_at_index,
    leg_walk_seconds,
    longest_walk_minutes,
    pace_corrected_walk_seconds,
    path_leg_seconds,
    planned_audio_seconds,
    route_planning_budget,
    summarise_route,
    within_planning_timebox,
)
from .routing import (
    target_dwell_seconds as target_dwell_seconds,
)
from .routing_client import ROUTE_SURFACE_COSTING_OVERRIDES
from .visit_time import (
    RAIN_DWELL_FRACTION,
    listened_seconds,
    place_is_covered,
    served_elapsed_seconds,
    shape_total_seconds,
    stop_seconds,
    visit_ceiling_seconds,
    visit_seconds,
    visit_shape,
)

if TYPE_CHECKING:
    from .routing_client import RoutingClient


# Capability token held by the one validating converter.  It is not a security
# boundary against malicious Python code; it prevents ordinary constructors,
# fixtures, and future refactors from accidentally blessing a raw snapshot as
# materialized merely because its fields have the right shape.
_MATERIALIZED_SNAPSHOT_TOKEN = object()

# §3 lens_adjacency hop model (ALGORITHM-SPEC.md, M3): a requested lens scores
# a POI 1.0 on a direct beat-lens hit, 0.6 when a beat lens is one
# IS_PARENT_OF hop away (parent OR child), and 0.0 on a miss — a thematic
# FILTER, not a bias (supersedes the old rule-41 "bias not filter" and the
# INTEREST_BIAS_* constants it came with). With no lenses requested the
# factor is uniform 1.0 and selection degrades to importance x richness x
# alignment x role.
LENS_ADJACENCY_DIRECT: float = 1.0
LENS_ADJACENCY_ONE_HOP: float = 0.6
LENS_ADJACENCY_MISS: float = 0.0

# §3 spotlight model (ALGORITHM-SPEC.md, Phase 3). Lens is genre/tone, not a
# gate: every corridor POI is eligible and a continuous spotlight score
# allocates dwell. spotlight = gravity(tier) x lens_relevance x proximity.
#
# Crucially distinct from the legacy _lens_adjacency above, whose MISS=0.0
# makes lens a HARD FILTER (the current select_route gate). lens_relevance
# below shares the SAME direct/1-hop/miss CLASSIFICATION but maps a miss to a
# POSITIVE FLOOR (LENS_FLOOR) — a miss DIMS, it never zeroes. This is purely
# additive (Step 3.1): select_route's selection is untouched until Step 3.5.
#
# LENS_FLOOR (=0.25): lens_relevance on a thematic miss. A landmark on the
# user's path still clears the floor on gravity → at least a brief mention;
# "the only silence is low-gravity AND off-genre" (§3).
LENS_FLOOR: float = 0.25
LENS_RELEVANCE_DIRECT: float = 1.0  # direct lens hit
LENS_RELEVANCE_ONE_HOP: float = 0.6  # parent/child 1-hop via lens_neighbors
LENS_RELEVANCE_NO_LENS: float = 1.0  # no lenses requested → uniform

# proximity(poi, route) — the on-path bonus. 1.0 when the POI sits on the A-B
# line (zero marginal detour) and decays as the routed detour off that line
# grows. Exponential decay exp(-detour_seconds / PROXIMITY_DECAY_SECONDS):
#   - 1.0 at detour 0 (perfectly on-path),
#   - strictly monotonically decreasing in detour,
#   - ALWAYS > 0 (never zeroes) — so proximity alone can never silence a POI,
#     preserving the §3 invariant that the ONLY silence is low-gravity AND
#     off-genre (and keeping the multiplicative objective's factors ≥ 0, §9.5).
# PROXIMITY_DECAY_SECONDS is the e-folding constant: at this many seconds of
# marginal detour the proximity factor falls to 1/e (~0.368). 600s (10 min)
# means a POI a 10-min detour off the direct line keeps ~37% of its on-path
# weight — a gentle dimming, not a cliff.
PROXIMITY_DECAY_SECONDS: float = 600.0

# §3 band classifier (ALGORITHM-SPEC.md, Phase 3 Step 3.2). The continuous
# spotlight score maps to one of five output bands:
#   headline | full | short | vignette | silent
# The output-facing collapse is dwell (headline/full/short) vs vignette;
# "silent" means excluded for this user. The thresholds below are INITIAL,
# principled anchors -- they are refined in the Step 3.5 golden re-baseline,
# NOT frozen here.
#
# Anchoring (on-path, proximity == 1.0, so spotlight == gravity x lens):
#   - tier-1 off-genre miss  = 1 x 0.25 = 0.25  (canonical SILENT case)
#   - tier-5 off-genre miss  = 5 x 0.25 = 1.25  (high-gravity landmark; must
#                                                 clear to at least VIGNETTE)
#   - tier-1 1-hop           = 1 x 0.6  = 0.60
#   - tier-3 direct hit      = 3 x 1.0  = 3.0
#   - tier-5 direct hit      = 5 x 1.0  = 5.0   (canonical HEADLINE case)
# Thresholds are lower-inclusive cut points on the spotlight score:
# Calibrated at the Step 3.5 golden re-baseline: the DWELL floor (short) sits at
# tier-3 gravity (gravity(t)=float(t)) so a no-lens tour's dwell pool matches the
# prior ANCHOR_TIERS={3,4,5} anchors (which the human-ideal goldens were built on)
# -- tier-1/2 no-lens POIs fall to vignette instead of crowding out the golden
# anchors. Lens dimming still demotes off-genre POIs on lensed tours; the
# two-track (dwell/vignette) output stands.
BAND_THRESHOLD_HEADLINE: float = 5.0  # >= 5.0 -> headline (tier-5, or lens-lifted)
BAND_THRESHOLD_FULL: float = 4.0  # >= 4.0 -> full stop (tier-4+)
BAND_THRESHOLD_SHORT: float = 3.0  # >= 3.0 -> short stop; the DWELL floor (~ tier-3 anchor)
BAND_THRESHOLD_VIGNETTE: float = 0.5  # >= 0.5 -> walk-past vignette; below -> silent
#
# The §3 silence invariant is structural, NOT a pure score threshold: silence
# requires BOTH a lens miss AND low gravity. A high-gravity landmark
# (tier >= BAND_LANDMARK_TIER) is NEVER silent -- lens alone can dim it to a
# vignette but never to silence ("the only silence is low-gravity AND
# off-genre"). This guard floors such a POI at VIGNETTE even if a large
# proximity detour would otherwise push its score below the silent cut.
BAND_LANDMARK_TIER: int = 4

# Filler-stub demotion (2026-07-04, user-agent ratified). The dwell/vignette band
# decision (band_for_spotlight) is score-driven (gravity x lens x proximity) and
# NEVER looks at how much a stop will actually SPEAK. So a low-tier POI with one
# 56s beat still becomes a full DWELL stop — a "walked here for one sentence"
# anticlimax the tourists flagged (Crypte 56s, Hotel de la Monnaie 44s). A DWELL
# stop that would emit fewer than this many voiced seconds is demoted to a
# walk-by vignette instead — EXCEPT a landmark (tier >= BAND_LANDMARK_TIER), which
# never vanishes to a one-liner (a thin landmark discloses, per the density gate).
# Measured on FULL emitted audio (select_poi_beats keeps every beat; the lens only
# re-orders), so a stop thin FOR a lens but rich overall (e.g. the Pantheon, ~40s
# on-lens but ~275s total) is correctly KEPT.
MIN_DWELL_AUDIO_SECONDS: int = 90

# Absolute per-stop audio CEILING (C9h, 2026-07-16). No single stop may voice more
# than this many seconds of continuous narration — the 10-minute "wall, not a walk"
# monologue an acceptance review flagged as the top reason tours feel stilted.
# The terminal stop keeps a closing-friendly beat by swapping lower-priority
# kept beats back into overflow; it does not receive an over-cap exception.
# Applied to EVERY stop (the marquee included) on BOTH open-walk and A→B routes, as
# min(existing governor cap, this). It is a WHOLE-BEAT cap via govern_poi_beats:
# trimmed beats become keep-exploring overflow (never dropped), and compose
# recomputes its coverage baseline on the capped stitch, so NO fact is lost. Well
# above MIN_DWELL_AUDIO_SECONDS so a capped stop never demotes to a vignette.
# Tunable: raise to let a star dominate longer; lower for tighter stops.
# DERIVED, not chosen (2026-07-19). This ceiling and the quality standard's per-stop
# WORD cap were written independently and silently drifted: at 420 s, and with
# routing.beat_spoken_seconds converting at 150 wpm, this permitted 420*150/60 = 1050
# words in a single stop -- 40% above the published 750-word cap
# (src/tour/quality_rubric.py GORGE_MAX_WORDS_PER_STOP, sourced in
# fixtures/tour-quality-standard/01-standard.md from museum-sector practice).
# Notre-Dame's MEASURED 1038-word stop was therefore the ceiling WORKING AS CONFIGURED,
# not a cap failure. Tied to the word cap so the two can no longer drift:
#     750 words / 150 wpm = 5.0 min = 300 s
#
# THEY DRIFTED AGAIN. GORGE_MAX_WORDS_PER_STOP moved 750 -> 850 in a069efd (measured:
# the widest stop in the certified corpus is 808 words, and 750 was BLOCKING three
# tours this project itself certified). This constant did not move with it, and the
# "can no longer drift" above is therefore false as written. Re-derived against 850 it
# would be 850/150*60 - 30 = 310 s.
#
# LEFT AT 270 DELIBERATELY, PENDING AN OWNER DECISION (2026-07-27) — and the owner's
# panel answered (2026-08-19, Phase 6 W6.2 R3, 11/11 LOCKED): "TIGHT = point-first,
# about three minutes (~450 words)". The measured F&D big stops ran 4.5 and 4.9 min at
# this 270 — past the panel's own four. 180 s at 150 wpm = 450 beat words, the panel's
# number; the ~75-word glue reserve rides on top as before (a rendered stop ~525 words,
# ~3.5 min spoken). The ceiling is ALSO the price of a stop's dwell in the budget
# (planned_capped_audio_seconds), so tighter tellings seat more stops per day — one
# constant, one truth; a compose-time-only trim would fork priced from voiced, the
# drift class this comment already records twice. The gap to
# GORGE_MAX_WORDS_PER_STOP (850) is now deliberate headroom: the stitch can no longer
# gorge a stop, so C8 guards COMPOSE inflation only. The full telling (design §5.5,
# S6.6) is a SECOND composed piece with its own budget (<= 3x tight, hard cap 12 min),
# never this ceiling raised.
# MINUS a MEASURED glue reserve: the ceiling caps BEAT audio, but a rendered stop also
# carries generation glue (arrival line, look-cue, closer). Measured per-stop glue on
# the Ile de la Cite tour: 2 / 65 / 16 / 12 / 73 words. Budgeting the worst case
# (~75 words = 30 s) keeps the RENDERED stop inside the cap instead of landing at 765.
MAX_DWELL_AUDIO_SECONDS: int = 180

# The five band labels, ordered loudest -> quietest. "silent" means excluded.
BAND_HEADLINE: str = "headline"
BAND_FULL: str = "full"
BAND_SHORT: str = "short"
BAND_VIGNETTE: str = "vignette"
BAND_SILENT: str = "silent"

# Output-facing collapse: the dwell bands (a real stop) vs the vignette band
# (one line as you pass). "silent" is neither -- it is excluded.
DWELL_BANDS: frozenset[str] = frozenset({BAND_HEADLINE, BAND_FULL, BAND_SHORT})

# Track B (Step B.1) — walk-past vignettes along the legs. A vignette-band POI
# within this perpendicular distance of a leg's straight segment is close
# enough to voice as a one-liner without any detour (locked deferred
# clarification: 50 m).
VIGNETTE_MAX_DETOUR_M: float = 50.0
# At most this many vignettes per leg — more one-liners than this on a single
# walk crowds the transit narration (locked deferred clarification: 2).
VIGNETTE_MAX_PER_LEG: int = 2

AREA_ALIGNMENT_SPINE: float = 1.0
AREA_ALIGNMENT_ADJACENT: float = 0.5
AREA_ALIGNMENT_OTHER: float = 0.2

POI_ROLE_MULTIPLIER: dict[str, float] = {
    "stop": 1.0,
    "setting": 0.7,
    "walk_by_only": 0.0,
    # A toilet or a bench (redesign row 6.3, plan S2.5) is scheduled by the
    # BODY CLOCK, never the story ranking — design §3.1: a bench is a
    # scheduled item, not a scored sight. The zero is the whole contract: it
    # is what keeps a body place from ever out-scoring a narrated stop once
    # the upload puts one in the graph; only `_seat_body_stops`, gated on the
    # rest-cadence axis, may ever place one.
    "body": 0.0,
}

ANCHOR_TIERS: frozenset[int] = frozenset({3, 4, 5})

# DELETED 2026-08-04 (OWNER RULING 5, "no stop limits, period"): ANCHOR_CAP_DIVISOR
# and HARD_ANCHOR_CAP. The greedy now stops only when the walk budget or the audio
# budget is spent, so DURATION is the sole bound on how many stops a tour has. The
# tractability role the 15-anchor cap used to carry incidentally — keeping the exact
# Held-Karp orderer under its one-second guard — now belongs to
# ``ordering.ORDERING_EXACT_MAX``, which switches to cheapest insertion above 16
# points without ever dropping a stop.

# Spine selection: pick the most-populated non-city Area within this many
# metres of the start point, weighted by tier among the candidate POIs.
SPINE_RADIUS_M: float = 800.0

# Areas that are city-scope (excluded from spine choice) or de-prioritised.
EXCLUDED_AREA_TYPES: frozenset[str] = frozenset({"city"})

# Phase 2.6 calibration: a neighborhood/island/corridor Area beats a
# district Area unless the district has at least SPINE_DISTRICT_DOMINANCE
# times the (tier-weighted) vote count of the smaller Area. This makes
# "Île de la Cité" the spine for a Pont Neuf start instead of "1st
# Arrondissement", because districts naturally accumulate more votes
# (every district contains everything inside it). The 5%-tolerance rule
# from Phase 2.5 was too weak to overcome that vote bias.
SPINE_DISTRICT_DOMINANCE: float = 2.0
SPECIFIC_AREA_TYPES: frozenset[str] = frozenset({"neighborhood", "island", "corridor"})
DISTRICT_AREA_TYPES: frozenset[str] = frozenset({"district"})
SPINE_AREA_TYPE_PRIORITY: tuple[str, ...] = (
    "neighborhood",
    "island",
    "corridor",
    "district",
    "city",
)

# Phase 7.5 (Fix 3, 2026-04-29) — same-physical-location POI demotion.
# After the greedy + endpoint-pull + fill pass, two selected POIs sometimes
# sit at the same address (e.g. Musée Victor Hugo at 6 PdV vs Place des
# Vosges no. 6). The user walks past the same building twice as separate
# stops. Demote the smaller-tier of any such pair into the larger; merge
# its beats into the host POI's pool so content isn't lost.
#
# Threshold is the v3 schema geofence radius (100 m) — the same distance
# the runtime uses to decide two coordinates point at "the same physical
# place". The Phase 7.5 spec called for 15 m, but live Paris corpus
# coordinates show PdV (centroid 48.8555,2.3656) sits ~85 m from
# Musée Victor Hugo's pin (48.8548,2.3661); a 15 m gate would never
# catch the headline case the spec was written to fix. The name-token
# overlap signal (`_has_cross_poi_address_overlap`) remains the
# semantic guard — adjacent-but-unrelated POIs (e.g. ND vs Crypte
# Archéologique) don't carry beats referencing each other's distinctive
# tokens, so they don't trigger demotion.
DEMOTION_PROXIMITY_M: float = 100.0

# Demotion is restricted to anchor-tier (≥4) POIs. The empirical Île
# walk treats Square du Vert-Galant (tier 3, ~80 m from Pont Neuf) as
# its own pause stop; merging tier-3 pauses into nearby tier-5 anchors
# would drop deliberate Pariswalks-style stops. Pavillon du Roi /
# Hôtel de Coulanges-style cases cited by the spec are all anchor-tier
# POIs, so this guard preserves the headline behaviour while
# protecting empirical pause-stop semantics.
DEMOTION_MIN_TIER: int = 4
# Common topographic / generic prefix tokens that don't make a POI
# distinctive when grepping addresses for cross-POI overlap. "Musee
# Victor Hugo" → distinctive token "hugo" (not "musee" / "victor"
# alone — Victor is a personal name, but the tokens we care about are
# the unambiguously POI-specific ones).
_NAME_GENERIC_TOKENS: frozenset[str] = frozenset(
    {
        "the",
        "of",
        "and",
        "or",
        "de",
        "des",
        "du",
        "le",
        "la",
        "les",
        "et",
        "place",
        "rue",
        "boulevard",
        "avenue",
        "quai",
        "pont",
        "musee",
        "musée",
        "hotel",
        "hôtel",
        "cathedral",
        "cathedrale",
        "cathédrale",
        "church",
        "saint",
        "sainte",
        "st",
        "ste",
        "square",
        "garden",
        "jardin",
        "park",
        "parc",
        "tower",
        "tour",
        "victor",  # ambiguous given name; co-located before this fires
    }
)
_NAME_TOKEN_MIN_LEN: int = 4


# Phase 7 (2026-04-29) fill pass — target_audio is a floor, not a soft stop.
# After the main greedy + endpoint-pull, if delivered audio is still well
# below target, run a relaxed-cost-efficiency second pass that adds
# anchors until the audio floor is met or walk budget is nearly spent.
# Concorde 180-min one-way Phase 6 case: 5 anchors selected, 25-min audio
# proxy, 89-min target, 41/60-min walk used. Greedy stalls because
# `score / extra_cost` no longer favours additions. Phase 7 fill pass
# adds higher-walk-cost candidates because being below the audio floor
# matters more than route efficiency at that point.
FILL_PASS_DWELL_FLOOR_FRAC: float = 0.8
FILL_PASS_WALK_BUDGET_FRAC: float = 0.95
# #21 under-fill rescue: while below the stop floor, admit a further stop whose
# MARGINAL walk cost is proportional to the time it EARNS the visitor —
# walk-seconds per standing-still second. A rich nearby stop the greedy couldn't
# fit is worth the detour; a far, thin stop is a walk-slog (25 min walking for 3
# min of anything) and stays OUT.
#
# THE DENOMINATOR MOVED ON 2026-08-06 AND THE THRESHOLD DID NOT, on purpose. It
# was walk-seconds per AUDIO-second, which asked "is this walk worth what the
# place says?" — so a forty-minute interior with ninety seconds of narration
# rated 27 and was refused as a slog. The question was always meant to be "is
# this walk worth what you get?", and the answer is the stop, not its script.
#
# Tuned on the live routed corpus, which showed a clean gap on the OLD
# denominator: rich stops the fix SHOULD add rated 3.2-4.2 (Pantheon 3.47, Louvre
# Museum 3.2-3.3, Palais-Royal 4.19), thin slogs >=5.0 (Pont des Arts 5.0, thin
# streets 5.7-16). 4.5 sat squarely in that gap. Moving the denominator can only
# LOWER a candidate's ratio (dwell >= audio always), so 4.5 stayed a valid divider
# and every stop that passed before still passed; what changed is that
# interior-heavy stops stopped being rejected. (A marginal ratio is used, not a
# total-walk cap: the fill-pass insertion cost is a PRE-Held-Karp overestimate,
# so a total-walk threshold wrongly rejects good stops once re-optimised.)
#
# RE-DERIVED 4.5 -> 2.0 BY THE ELEVEN, 2026-08-24 (Phase 8 W8.6, Q2; verdicts in
# evidence/phase8-gates/verdicts/w86-*.md). 4.5 was tuned on the engine's own
# corpus gap — never on what a traveller will actually walk — and it CONTRADICTED
# the product's own illogical-detour gate, which calls anything past 2.0x the
# direct walk (and 1800 s of pure detour) a defect
# (tests/test_tour_invariants_live.py::_MAX_STOP_DETOUR_RATIO). The planner was
# buying days its own invariant then flagged: the measured Panthéon 120-minute
# loop walked 43 minutes to a bookshop at 60.7x, and four live fixtures failed
# INV8 on windows this line admitted.
#
# The panel's own numbers, each from their documented day: Aiko 1:3 · Paulo 1:2 ·
# Fiona & Dev 1:2 · Camille 1:1 (her dearest leg, Pont Neuf, ran exactly 1:1) ·
# Nadia 1:1 at her measured half-pace · Marcus 1:1 with the bag · Rosemary 1:1
# (and never a leg over 12 min) · Sofia 1:1 · Greta 1.5 · Théo 2.0 · Julien 2.0
# for SILENT walking (told walking, he rules, costs nothing). 2.0 is the loosest
# line no panelist's OWN defended trade breaks — Théo's 16-minute walk for nine
# silent minutes at the deportation memorial (1.8) and Greta's narrated 2:1
# Orangerie leg both survive — and it is the single number all eleven named as
# the gate. Rosemary on the old value, verbatim: 4.5 "would march me twenty-two
# minutes for a five-minute look. That is not a tour; that is how you put me in
# a taxi home."
#
# Greta's standing dissent, recorded rather than averaged away: do not treat one
# constant as the answer forever — read a traveller's walking appetite when the
# system can hold one. Aiko's: the price of walking is condition-scaled (rain).
RESCUE_MAX_WALK_PER_DWELL_SECOND: float = 2.0

#: The ABSOLUTE half of the same W8.6 ruling: however much a stop earns the
#: visitor, no single added stop may cost more than this much PURE detour. Both
#: halves are needed because a ratio alone cannot catch a rich stop bought with an
#: hour of walking, and an absolute alone cannot catch a thin one bought with ten
#: minutes. INHERITED, not invented: it is the same 1800 s the illogical-detour
#: invariant pairs with its 2.0x ratio (`_MIN_DETOUR_EXTRA_SECONDS`), so the
#: planner and the gate that judges it now speak one rule instead of two — the
#: §10.8.2 discipline applied to a threshold rather than a formula.
MAX_STOP_PURE_DETOUR_SECONDS: int = 1800


def stop_earns_its_walk(marginal_walk_seconds: int, stop_dwell_seconds: int) -> bool:
    """THE ONE walking-worth line (Phase 8 W8.6; the panel's Q2, 11/11).

    Is this stop worth the walking it costs to add? Both halves of the ruling,
    in one definition every site that ADDS a stop calls — the ordinary fill, the
    under-fill rescue, and the timebox repair's slog test — because a rule that
    lives in three places is a rule that forks (§10.8.2, §0.4). Before this, the
    rescue applied a ratio, the repair applied the same ratio, and the ordinary
    fill applied NOTHING but the walk budget, so a stop the rescue would refuse
    was bought a line earlier as long as the budget had room. That is the
    measured Shape B zig-zag (concorde-250 at 3.5-7.8x, greenwich at 2.7-4.1x)
    and Nadia named it: "a planner that flags 60.7x with one hand and buys it
    with the other is not planning, it is arguing with itself."

    The currency is VISITOR-TIME on both sides (design §4.5.1): seconds of
    walking against the seconds the stop earns the visitor standing there —
    never against what it says, because "a forty-minute interior carrying ninety
    seconds of audio" is the shape this release exists to seat.
    """
    return (
        marginal_walk_seconds <= RESCUE_MAX_WALK_PER_DWELL_SECOND * max(1, stop_dwell_seconds)
        and marginal_walk_seconds <= MAX_STOP_PURE_DETOUR_SECONDS
    )
# The rescue lifts a tour to at least this many stops (the reported failure was a
# 60-min tour seating only ONE). Kept modest so two far-ish rich stops can't
# accumulate into a walk-heavy route — a 1->2 stop lift is the fix; a rich dense
# area already seats more via the greedy, and this never fires there.
RESCUE_STOP_FLOOR: int = 2
# C11a: a GREEN-density route whose DELIVERED audio is below this fraction of the
# audio target is disclosed as thin (delivered_thin). ~0.5 flags the pool-vs-
# delivered gap (e.g. a 90-min request delivering ~7 min) while leaving genuinely
# rich tours (Ile ~0.68 of target) unflagged.
GREEN_THIN_DELIVERY_FRAC: float = 0.5
# C9 governor v4 (domination-gated share-of-DELIVERED, 2026-07-04). The cap acts
# ONLY on a genuine DOMINATING OUTLIER — a non-exempt stop whose emitted audio
# both exceeds GOVERNOR_SHARE_OF_DELIVERED of the tour's total delivered audio AND
# exceeds GOVERNOR_DOMINATION_FACTOR x the MEAN of the OTHER non-exempt stops (so
# it is drowning its peers, e.g. the UC5 'Ile de la Cite' encyclopedia-dump; the
# mean-of-others gate means two co-dominators can't shield each other). Balanced
# tours (every stop near its fair share) are NEVER capped — the panel proved an
# always-on share cap over-trims them. EXEMPT (may dominate): the marquee (the
# highest-tier delivered stop, ties -> highest-audio) and the fixed destination /
# pulled endpoint. Importance-based exemption fixes the v3 bug where the greedy's
# nearest proximity-seed (often a thin courtyard) held the exemption while the
# real star was capped. See C9F-GOVERNOR-V3-SHARE.md + the panel refutation.
GOVERNOR_SHARE_OF_DELIVERED: float = 1.0 / 3.0
GOVERNOR_DOMINATION_FACTOR: float = 1.5


def _band_alternatives(
    *,
    input: TourInput,
    planning_policy: RoutePlanningPolicy,
    best_elapsed_seconds: int | None,
) -> tuple[tuple[FeasibilityAlternative, ...], int | None]:
    """The loop/extend pair for a route that cannot reach its frozen band.

    Mirrors the Step-2.2a fixed-destination construction exactly, so both refusals
    reach a surface in the same shape and one renderer can draw either. ``extend`` is
    the smallest duration whose maximum elapsed ceiling covers what the best bounded
    route actually costs. Returns ``((), None)`` when there is nothing actionable to
    offer, which is the honest answer when no bounded route was priced at all.
    """
    if best_elapsed_seconds is None:
        return (), None
    budget = route_planning_budget(input.duration_min, planning_policy)
    overshoot = best_elapsed_seconds - budget.maximum_elapsed_seconds
    gap_minutes = math.ceil(overshoot / 60) if overshoot > 0 else None
    suggested = 1
    while (
        route_planning_budget(suggested, planning_policy).maximum_elapsed_seconds
        + TIMEBOX_MATERIALITY_TOLERANCE_SECONDS
        < best_elapsed_seconds
    ):
        suggested += 1
    alternatives: tuple[FeasibilityAlternative, ...] = (
        FeasibilityAlternative(kind="extend", duration_min=suggested, drop_end=False),
    )
    if input.end is not None:
        alternatives = (
            FeasibilityAlternative(kind="loop", duration_min=input.duration_min, drop_end=True),
            *alternatives,
        )
    return alternatives, gap_minutes


class CertificationPlanningInfeasibleError(Exception):
    """No eligible bounded stop set can satisfy a frozen certification band."""

    def __init__(
        self,
        *,
        policy_id: str,
        minimum_elapsed_seconds: int,
        maximum_elapsed_seconds: int,
        best_elapsed_seconds: int | None,
        reason: str,
        gap_minutes: int | None = None,
        alternatives: tuple[FeasibilityAlternative, ...] = (),
    ) -> None:
        self.policy_id = policy_id
        self.minimum_elapsed_seconds = minimum_elapsed_seconds
        self.maximum_elapsed_seconds = maximum_elapsed_seconds
        self.best_elapsed_seconds = best_elapsed_seconds
        self.reason = reason
        # Same two fields TourabilityRefusedError carries, so ONE refusal-detail helper
        # can serialise both and a surface never has to know which one it caught. Before
        # 2026-08-04 this error had no alternatives at all and escaped POST
        # /trips/generate uncaught — a 500 to a traveller whose request merely did not
        # fit.
        self.gap_minutes = gap_minutes
        self.alternatives = alternatives
        # The tail is a whole clause, not a substituted number, because there are two
        # different truths to tell and one of them has no number in it. Measured
        # 2026-08-25 on a New York 60-minute start: `best_elapsed_seconds` was None and
        # the old form rendered "best eligible bounded route nones." — the word "none"
        # with the seconds unit glued on, in a sentence that had already claimed "the
        # shortest one found is still longer than asked for" while nothing had been
        # found at all. W8.2's ruling R8 requires an editor-facing refusal to be "a line
        # edit, not a guess"; a sentence that contradicts itself is neither.
        if best_elapsed_seconds is None:
            tail = "and no bounded route could be priced at all"
        else:
            tail = f"best eligible bounded route {best_elapsed_seconds}s"
        super().__init__(
            f"Certification planning infeasible under {policy_id}: {reason}; "
            f"required {minimum_elapsed_seconds}-{maximum_elapsed_seconds}s, {tail}."
        )


# Endpoint-pull (Q1, one-way only): after greedy completes, force-include
# the highest-scoring un-selected POI in the far half of the reachable
# envelope as the final stop. The "far half" is anything beyond this
# fraction of max_radius from start.
ENDPOINT_PULL_FAR_FRACTION: float = 0.5
# Try the top-K far candidates by score; accept the first that fits
# walk-budget + anchor-cap with ≤ ENDPOINT_PULL_MAX_DROPS drops. Without
# this, a single highest-score candidate that busts budget would silently
# abandon the pull even when the next-best far candidate would fit.
ENDPOINT_PULL_CANDIDATE_TOP_K: int = 5
# Greedy reserves this fraction of the walk budget for endpoint-pull on
# one-way routes. Without reservation the greedy can use 98% of the
# budget on tight neighborhood clusters, leaving no room for a
# far-envelope closing stop and silently abandoning the pull.
ENDPOINT_PULL_RESERVED_BUDGET_FRACTION: float = 0.25


# Step 2.4 (2026-06-30) — fixed-destination B-materialization (HYBRID).
# When the request carries a fixed end B, ORDER must pin a concrete POI as
# the last stop. If a selected (already in-corridor) POI sits within this
# many metres of B, snap B onto it — the user's "end here" is satisfied by
# walking to that real anchor. Otherwise synthesize a sentinel POI at B's
# exact coordinate and add it to the selected set so Held-Karp can pin it.
# Geometric haversine proximity (like DEMOTION_PROXIMITY_M) is the right
# metric here: this is a "the destination *is* this place" judgment, not a
# routed-detour budget question (the corridor gate already enforced that).
B_SNAP_PROXIMITY_M: float = 150.0
# poi_role / tier for a synthesized B sentinel. role 'stop' marks it a
# plain ordered stop (not an anchor the greedy would have scored); a
# neutral mid tier keeps it from skewing any tier-weighted pass that might
# see it downstream — it carries no beats, so it contributes no audio.
B_SENTINEL_POI_ROLE: str = "stop"
B_SENTINEL_TIER: int = 3


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CorpusSnapshot:
    """All inputs `select_route` needs in pure-function form."""

    pois: tuple[POI, ...]
    beats_by_poi: Mapping[str, tuple[BeatRef, ...]]
    area_types: Mapping[str, str]  # area_name → area_type (district/neighborhood/...)
    adjacent_areas: Mapping[str, frozenset[str]]  # area_name → directly-adjacent area names
    # M3: lens hierarchy for the §3 lens_adjacency hop model. Lowercased lens
    # name → its IS_PARENT_OF neighbors one hop away, BOTH directions (parent
    # and children). Empty when a fixture doesn't care about lens hops.
    lens_neighbors: Mapping[str, frozenset[str]] = dataclass_field(default_factory=dict)
    place_manifest: CorpusPlaceManifest | None = None

    def beats_for(self, poi_id: str) -> tuple[BeatRef, ...]:
        if self.place_manifest is not None and not isinstance(self, MaterializedCorpusSnapshot):
            raise ValueError("typed corpus place evidence has not been materialized")
        return self.beats_by_poi.get(poi_id, ())


@dataclass(frozen=True)
class MaterializedCorpusSnapshot(CorpusSnapshot):
    """Deep-immutable, manifest-validated input admitted to typed selection.

    The distinct type is intentional: carrying a manifest is not sufficient.
    Only :func:`place_materialization.materialize_corpus_snapshot` may convert
    source rows into this selection-ready form after sentence coverage,
    provenance, canonical identities, and playback contexts have been checked.
    """

    materialization_plan: CorpusMaterializationPlan | None = None
    snapshot_sha256: str = ""
    _validation_token: InitVar[object | None] = None

    def __post_init__(self, _validation_token: object | None) -> None:
        mappings = (
            self.beats_by_poi,
            self.area_types,
            self.adjacent_areas,
            self.lens_neighbors,
        )
        if any(not isinstance(value, MappingProxyType) for value in mappings):
            raise ValueError("materialized corpus mappings must be deep-immutable")
        if self.place_manifest is None or self.materialization_plan is None:
            raise ValueError("materialized corpus requires its manifest and decision plan")
        if _validation_token is not _MATERIALIZED_SNAPSHOT_TOKEN:
            raise ValueError("only the validated materializer may construct this snapshot")
        if self.materialization_plan.source_manifest_sha256 != self.place_manifest.manifest_sha256:
            raise ValueError("materialization plan is bound to a different manifest")
        if not re.fullmatch(r"[0-9a-f]{64}", self.snapshot_sha256):
            raise ValueError("materialized corpus requires a canonical snapshot hash")


def require_materialized_snapshot(snapshot: CorpusSnapshot, *, operation: str) -> None:
    """Validate one typed snapshot at a public post-selection boundary."""

    if snapshot.place_manifest is None:
        return
    if not isinstance(snapshot, MaterializedCorpusSnapshot):
        raise ValueError(f"typed corpus must be materialized before {operation}")
    from .place_materialization import validate_materialized_corpus_snapshot

    validate_materialized_corpus_snapshot(snapshot)


def _place_identity_key(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(character)
    ).casefold()


def is_container_identity_poi(poi: POI) -> bool:
    """Whether this POI *is* one of its own containing areas — a neighbourhood or
    district standing in as if it were a findable place.

    Measured on the live Paris graph (2026-07-26): exactly ONE POI qualifies —
    ``Les Halles``, which exists both as an Area and as a tier-5 POI with
    ``poi_role='setting'``. A tourist told to "go to Les Halles" while standing in
    Les Halles has been given no destination.
    """

    return _place_identity_key(poi.name) in {_place_identity_key(area) for area in poi.areas}


def route_has_container_identity_stop(route: Route) -> bool:
    """Return whether a neighborhood or area was used as a fictional stop."""

    return any(is_container_identity_poi(poi) for poi in route.pois)


def choose_discrete_route(routes: list[Route]) -> Route:
    """Choose the first ranked route whose stops are findable identities."""

    for route in routes:
        if not route_has_container_identity_stop(route):
            return route
    raise ValueError("bounded candidates contain no route with all-discrete stops")


# ---------------------------------------------------------------------------
# Neo4j loader
# ---------------------------------------------------------------------------


# Cypher pulls every active beat for every city POI in one shot. POIs
# carry their Area memberships as a list; beats carry only what selection
# and ordering need. ORDER BY p.id because Cypher row order is otherwise
# unspecified — snapshot.pois must be identical across two loads of the
# same graph or greedy tie-breaks drift between runs.
LOAD_PARIS_POIS_CYPHER = """
MATCH (p:POI {city_name: $city_slug})
OPTIONAL MATCH (p)-[:WITHIN]->(a:Area)
WITH p, collect(DISTINCT a.name) AS area_names
WHERE p.location IS NOT NULL
RETURN
  p.id            AS id,
  p.canonical_place_id AS canonical_place_id,
  p.name          AS name,
  p.aliases       AS aliases,
  p.coordinate_provenance AS coordinate_provenance,
  p.importance_tier AS tier,
  p.poi_role      AS poi_role,
  p.location.y    AS lat,
  p.location.x    AS lng,
  p.typical_duration_min AS typical_duration_min,
  p.visit_seconds_inside AS visit_seconds_inside,
  p.visit_basis   AS visit_basis,
  p.opening_hours AS opening_hours,
  p.opening_hours_source AS opening_hours_source,
  p.opening_hours_basis AS opening_hours_basis,
  p.gated         AS gated,
  p.opening_hours_verified AS opening_hours_verified,
  p.place_category AS place_category,
  p.children_can_run AS children_can_run,
  p.sit_and_talk  AS sit_and_talk,
  p.good_after_dark AS good_after_dark,
  p.judgement_basis AS judgement_basis,
  p.queue_class   AS queue_class,
  p.queue_minutes_peak AS queue_minutes_peak,
  p.queue_minutes_offpeak AS queue_minutes_offpeak,
  p.queue_peak_hours AS queue_peak_hours,
  p.queue_basis   AS queue_basis,
  p.trigger_radius AS trigger_radius,
  p.anchors       AS anchors,
  area_names      AS areas
ORDER BY p.id
"""

LOAD_PARIS_BEATS_CYPHER = """
MATCH (p:POI {city_name: $city_slug})-[:HAS_BEAT]->(b:NarrativeBeat)
WHERE b.active_status = 'active'
OPTIONAL MATCH (b)-[:TAGGED_WITH]->(l:Lens)
WITH p, b, collect(DISTINCT l.name) AS lens_names
RETURN
  b.id                  AS id,
  b.beat_id             AS stable_beat_id,
  b.place_plan_id       AS place_plan_id,
  p.id                  AS poi_id,
  b.sub_location        AS sub_location,
  b.trigger_address     AS trigger_address,
  b.narrative_function  AS narrative_function,
  b.beat_type           AS beat_type,
  b.emotional_register  AS emotional_register,
  b.beat_length_class   AS beat_length_class,
  b.est_spoken_seconds  AS est_spoken_seconds,
  b.script_body         AS script_body,
  b.audio_url           AS audio_url,
  b.entities            AS entities,
  b.subject_tag         AS subject_tag,
  b.active_status       AS active_status,
  b.physical_cues       AS physical_cues,
  b.pronunciation       AS pronunciation,
  b.source_passage      AS source_passage,
  b.source_chunk_slug   AS source_chunk_slug,
  b.key_claims          AS key_claims,
  lens_names            AS lenses
ORDER BY coalesce(b.beat_id, b.id)
"""

LOAD_AREA_TYPES_CYPHER = """
MATCH (a:Area)
RETURN a.name AS name, a.area_type AS area_type, a.city_name AS city_name
"""

# Two areas are "adjacent" when at least one POI is in both.
LOAD_AREA_ADJACENCY_CYPHER = """
MATCH (p:POI {city_name: $city_slug})-[:WITHIN]->(a:Area)
WITH p, collect(DISTINCT a.name) AS area_names
UNWIND area_names AS a1
UNWIND area_names AS a2
WITH a1, a2 WHERE a1 < a2
RETURN a1, a2, count(*) AS shared_pois
"""

# Lens hierarchy for the §3 lens_adjacency hop model. Lens nodes are global
# (not city-scoped) by design — the taxonomy is shared across cities.
LOAD_LENS_HIERARCHY_CYPHER = """
MATCH (parent:Lens)-[:IS_PARENT_OF]->(child:Lens)
RETURN parent.name AS parent, child.name AS child
"""


def load_paris_corpus(driver, *, city_slug: str = "paris") -> CorpusSnapshot:
    """Pull a CorpusSnapshot for one city from Neo4j."""
    with driver.session() as session:
        poi_records = session.run(LOAD_PARIS_POIS_CYPHER, city_slug=city_slug).data()
        beat_records = session.run(LOAD_PARIS_BEATS_CYPHER, city_slug=city_slug).data()
        area_records = session.run(LOAD_AREA_TYPES_CYPHER).data()
        adj_records = session.run(LOAD_AREA_ADJACENCY_CYPHER, city_slug=city_slug).data()
        lens_records = session.run(LOAD_LENS_HIERARCHY_CYPHER).data()

    return _snapshot_from_records(
        poi_records, beat_records, area_records, adj_records, lens_records
    )


def _lens_neighbor_map(lens_records: list[dict]) -> dict[str, frozenset[str]]:
    """Symmetric 1-hop IS_PARENT_OF neighborhood, lowercased both ways."""
    acc: dict[str, set[str]] = {}
    for r in lens_records:
        parent = (r.get("parent") or "").strip().lower()
        child = (r.get("child") or "").strip().lower()
        if not parent or not child:
            continue
        acc.setdefault(parent, set()).add(child)
        acc.setdefault(child, set()).add(parent)
    return {k: frozenset(v) for k, v in acc.items()}


# src/seed/narratives.py:95 stamps every seeded beat with this audio prefix.
# A seeded beat that was later adopted into the corpus carries a real
# ``beat_id``; the un-adopted twin left behind by re-seeding does not, and
# is invisible to db_parity.py:113 and prune_orphan_pois.py:48 (both filter
# ``beat_id IS NOT NULL``). Excluding it here is the only thing keeping
# placeholder audio out of a tour.
_PLACEHOLDER_AUDIO_PREFIX = "s3://ondoway-audio/placeholder/"


#: Weekday index (datetime.weekday(): Monday=0) -> the week-table key the
#: opening-hours pass writes, and the English name the exclusion reason speaks.
_CLOCK_DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_CLOCK_DAY_NAMES = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


def _clock_exclusion_reason(
    opening_hours_json: str,
    source: str | None,
    start: datetime,
    duration_min: int,
) -> str | None:
    """THE one definition of clock-closure (redesign 6.1). None = not closed.

    A POI is clock-CLOSED only when its opening table is CLOSED for the
    ENTIRE visit window (start → start + duration): a place open for any part
    of the window is simply open — arriving to find a door that closes in
    an hour is the visitor's trade to make, a locked one is not. What closure
    MEANS is the call site's decision, not this function's (plan S3.5, W1.9
    dissent 1): a closed place with an exterior demotes to an outside-only
    stop; only one with nothing to stand and see is removed. Both outcomes
    append their own trailer to the detail returned here.

    FAILS OPEN, deliberately: a table that does not parse, a malformed window,
    an unknown shape — all return None, i.e. "not closed". A data defect
    must degrade to today's trusting behaviour, never lock a visitor out of an
    open door. The structural bars in tests/test_poi_opening_hours.py are the
    guard on the data itself.
    """
    try:
        table = json.loads(opening_hours_json)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(table, dict):
        return None

    end = start + timedelta(minutes=duration_min)
    closed_day_names: list[str] = []
    open_windows_seen: list[str] = []
    cursor = start
    while cursor < end:
        day_key = _CLOCK_DAY_KEYS[cursor.weekday()]
        windows = table.get(day_key)
        if not isinstance(windows, list):
            return None  # malformed / missing day → fail open
        next_midnight = datetime.combine(cursor.date() + timedelta(days=1), time.min)
        segment_end = min(end, next_midnight)
        seg_from = cursor.strftime("%H:%M")
        seg_to = "24:00" if segment_end == next_midnight else segment_end.strftime("%H:%M")
        for window in windows:
            if (
                not isinstance(window, list)
                or len(window) != 2
                or not all(isinstance(t, str) for t in window)
            ):
                return None  # malformed window → fail open
            opens, closes = window
            if opens < seg_to and closes > seg_from:
                return None  # open for part of the window → seatable
        closed_day_names.append(_CLOCK_DAY_NAMES[cursor.weekday()])
        open_windows_seen.extend(f"{w[0]}-{w[1]}" for w in windows)
        cursor = next_midnight

    # PLAIN LANGUAGE (W4.2 panel, Paulo's wording rulings; design deviation v).
    # A person reads this sentence, so it carries no provenance tag: "(hours: OSM)"
    # was ruled a failure of plain language. The DOUBT that tag encoded is not
    # dropped — it is said in words, so the reader learns we are unsure without
    # having to decode a label. A verified table simply states the closure.
    doubt = "" if (source or "").lower() == "osm" else ", though we could not confirm its hours"
    if len(closed_day_names) == 1:
        day = closed_day_names[0]
        if open_windows_seen:
            detail = (
                f"closed {day} {start.strftime('%H:%M')}-{end.strftime('%H:%M')} "
                f"(open {', '.join(open_windows_seen)})"
            )
        else:
            detail = f"closed all day {day}"
    else:
        detail = (
            f"closed for the whole of your visit ({closed_day_names[0]}-{closed_day_names[-1]})"
        )
    return detail + doubt


def _closed_all_day(opening_hours_json: str, day: datetime) -> bool:
    """Whether the table lists NO open window at all on ``day`` — the fact the
    voice's "closed today" branch needs, read from the data itself (a decision
    belongs in a field, never recovered from the reason sentence's words).
    Fails to False on bad data, the same trusting direction as the closure
    rule: an unknowable schedule never strengthens a claim."""
    try:
        table = json.loads(opening_hours_json)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(table, dict):
        return False
    windows = table.get(_CLOCK_DAY_KEYS[day.weekday()])
    return isinstance(windows, list) and not windows


def _is_unadopted_placeholder_beat(record: dict) -> bool:
    """True for a seed-artifact beat: no stable beat_id AND placeholder audio.

    The conjunction is deliberate. Real corpus beats awaiting TTS still point
    at the placeholder prefix, and hermetic callers legitimately omit
    ``stable_beat_id``; neither half alone may exclude a beat.
    """
    if _clean(record.get("stable_beat_id")) is not None:
        return False
    audio_url = record.get("audio_url")
    return isinstance(audio_url, str) and audio_url.startswith(_PLACEHOLDER_AUDIO_PREFIX)


def _snapshot_from_records(
    poi_records: list[dict],
    beat_records: list[dict],
    area_records: list[dict],
    adj_records: list[dict],
    lens_records: list[dict] | None = None,
    place_manifest: CorpusPlaceManifest | None = None,
) -> CorpusSnapshot:
    pois: list[POI] = []
    beats_by_poi_acc: dict[str, list[BeatRef]] = {}

    for r in beat_records:
        if _is_unadopted_placeholder_beat(r):
            continue
        body = r.get("script_body")
        ref = BeatRef(
            id=r["id"],
            poi_id=r["poi_id"],
            sub_location=_clean(r.get("sub_location")),
            trigger_address=_clean(r.get("trigger_address")),
            narrative_function=_clean(r.get("narrative_function")),
            beat_type=_clean(r.get("beat_type")),
            emotional_register=_clean(r.get("emotional_register")),
            beat_length_class=_clean(r.get("beat_length_class")),
            est_spoken_seconds=int(r.get("est_spoken_seconds") or 0),
            word_count=_count_words(body),
            entities=tuple(r.get("entities") or ()),
            subject_tag=_clean(r.get("subject_tag")),
            lenses=tuple(sorted(s for s in (r.get("lenses") or ()) if s)),
            active_status=r.get("active_status") or "active",
            script_body=body if isinstance(body, str) and body.strip() else None,
            physical_cues=_decode_physical_cues(r.get("physical_cues")),
            pronunciation=_clean(r.get("pronunciation")),
            source_passage=_clean(r.get("source_passage")),
            source_chunk_slug=_clean(r.get("source_chunk_slug")),
            key_claims=tuple(s.strip() for s in (r.get("key_claims") or ()) if s and s.strip()),
            stable_beat_id=_clean(r.get("stable_beat_id")),
            place_plan_id=_clean(r.get("place_plan_id")),
        )
        beats_by_poi_acc.setdefault(ref.poi_id, []).append(ref)

    for r in poi_records:
        pid = r["id"]
        beats = beats_by_poi_acc.get(pid, [])
        beat_count = len(beats)
        # matching_lens_beat_count is legacy: the M3 lens_adjacency hop model
        # scans beat lenses directly, so nothing computes it any more. The
        # contract field stays (spec §5: every existing field preserved).
        pois.append(
            POI(
                id=pid,
                name=r["name"],
                tier=int(r.get("tier") or 1),
                poi_role=r.get("poi_role") or "stop",
                lat=float(r["lat"]),
                lng=float(r["lng"]),
                areas=tuple(sorted(a for a in (r.get("areas") or ()) if a)),
                beat_count=beat_count,
                matching_lens_beat_count=0,
                canonical_place_id=_clean(r.get("canonical_place_id")),
                aliases=tuple(
                    sorted(
                        {
                            alias.strip()
                            for alias in (r.get("aliases") or ())
                            if isinstance(alias, str) and alias.strip()
                        },
                        key=lambda value: (value.casefold(), value),
                    )
                ),
                coordinate_provenance=_decode_coordinate_provenance(r.get("coordinate_provenance")),
                # THREE PLACES CAN EAT THESE FIELDS SILENTLY, and all three are
                # closed: the Cypher above only returns what it names, this
                # constructor only sets what it lists, and POI is extra="ignore"
                # so an unlisted keyword vanishes without an error. A corpus
                # written before the capacity pass returns None for all three and
                # lands on the contract defaults, which reproduce today's
                # behaviour exactly.
                typical_duration_min=int(r.get("typical_duration_min") or 0),
                visit_seconds_inside=(
                    int(r["visit_seconds_inside"])
                    if r.get("visit_seconds_inside") is not None
                    else None
                ),
                visit_basis=_clean(r.get("visit_basis")) or "",
                # The clock fields (redesign 6.1/6.7) ride the same three
                # closed hops as the capacity trio above; an unpriced corpus
                # returns None for all four and lands on the contract defaults
                # (None hours = never clock-excluded — the safe direction).
                opening_hours=_clean(r.get("opening_hours")),
                opening_hours_source=_clean(r.get("opening_hours_source")),
                opening_hours_basis=_clean(r.get("opening_hours_basis")) or "",
                # The trust half (Docs/adr/0003) — same closed-hop rule. gated
                # keeps three states: a record carrying None lands on None (no
                # claim, fail-open), never on False (a claim of no door).
                gated=(bool(r["gated"]) if r.get("gated") is not None else None),
                opening_hours_verified=_clean(r.get("opening_hours_verified")),
                place_category=_clean(r.get("place_category")) or "",
                # Row 6.4 (plan S2.6) — same closed-hop safe-default rule: a
                # corpus the judgements pass has not reached returns None for
                # all four and lands on the contract defaults (affords nothing).
                children_can_run=bool(r.get("children_can_run")),
                sit_and_talk=bool(r.get("sit_and_talk")),
                good_after_dark=bool(r.get("good_after_dark")),
                judgement_basis=_clean(r.get("judgement_basis")) or "",
                # The queue (redesign row 6.5, plan S3.4) — same closed-hop
                # safe-default rule: an unpassed corpus returns None for all
                # five, queue_class lands on the contract's None (= never
                # priced), and the inert int/str defaults claim nothing.
                queue_class=_clean(r.get("queue_class")),
                queue_minutes_peak=int(r.get("queue_minutes_peak") or 0),
                queue_minutes_offpeak=int(r.get("queue_minutes_offpeak") or 0),
                queue_peak_hours=_clean(r.get("queue_peak_hours")) or "",
                # THE FOOTPRINT (Phase 7 S7.4; design §5.6 C7) — the same closed-hop
                # rule: the graph's metres land on the POI; a record carrying none (or
                # the graph's 0) lands on the contract's None, and the audio placement
                # rule (src/tour/placement.py) falls to its door-sized default.
                trigger_radius=(
                    float(r["trigger_radius"]) if r.get("trigger_radius") else None
                ),
                # THE REVIEWED ANCHORS (Phase 7 S7.7 B) — the same closed-hop rule: the
                # graph's JSON list lands on the POI; a record carrying none lands on
                # the contract's empty tuple and the story is told uncut.
                anchors=_decode_anchors(r.get("anchors")),
                queue_basis=_clean(r.get("queue_basis")) or "",
            )
        )

    area_types = {r["name"]: (r.get("area_type") or "") for r in area_records}

    adjacency: dict[str, set[str]] = {}
    for r in adj_records:
        a1 = r["a1"]
        a2 = r["a2"]
        if not a1 or not a2:
            continue
        adjacency.setdefault(a1, set()).add(a2)
        adjacency.setdefault(a2, set()).add(a1)
    adjacent_areas = {k: frozenset(v) for k, v in adjacency.items()}

    return CorpusSnapshot(
        pois=tuple(pois),
        beats_by_poi={k: tuple(v) for k, v in beats_by_poi_acc.items()},
        area_types=area_types,
        adjacent_areas=adjacent_areas,
        lens_neighbors=_lens_neighbor_map(lens_records or []),
        place_manifest=place_manifest,
    )


def _decode_anchors(raw) -> tuple[Anchor, ...]:
    """Decode a POI's reviewed anchors (Phase 7 S7.7 B) from the graph's JSON string (the
    `opening_hours` / `physical_cues` precedent) or a plain list. Fails OPEN like the cue
    decoder: nothing, an empty string, a malformed payload or a malformed entry reads as
    no anchor — an uncut story — never a refusal to load the corpus."""
    if raw is None:
        return ()
    if isinstance(raw, str):
        if not raw.strip():
            return ()
        try:
            decoded = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return ()
    else:
        decoded = raw
    if not isinstance(decoded, list):
        return ()
    out: list[Anchor] = []
    for entry in decoded:
        if not isinstance(entry, dict):
            continue
        try:
            out.append(Anchor.model_validate(entry))
        except ValueError:
            continue
    return tuple(out)


def _clean(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, str) and not v.strip():
        return None
    return v


def _count_words(s) -> int:
    if not isinstance(s, str):
        return 0
    return sum(1 for token in s.split() if token)


def _decode_physical_cues(raw) -> tuple[PhysicalCue, ...]:
    """Decode the JSON-encoded physical_cues string back to PhysicalCue tuple.

    Neo4j stores list[dict] as JSON-encoded strings (see
    src/api/crud/nodes.py::_encode_complex_props). This loader reads the
    raw value back into structured tuples so beat_select / generation
    can reason about cues without re-querying.

    Tolerates legacy list[str] beats (Vallois pre-migration shape) by
    wrapping bare strings in a default cue dict.
    """
    if raw is None:
        return ()
    if isinstance(raw, str):
        if not raw.strip():
            return ()
        try:
            decoded = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return ()
    else:
        decoded = raw
    if not isinstance(decoded, list):
        return ()
    cues: list[PhysicalCue] = []
    for item in decoded:
        if isinstance(item, dict):
            cue_text = item.get("cue") or ""
            if not cue_text:
                continue
            cues.append(
                PhysicalCue(
                    cue=cue_text,
                    direction=item.get("direction"),
                    feature_type=item.get("feature_type"),
                )
            )
        elif isinstance(item, str) and item.strip():
            cues.append(PhysicalCue(cue=item.strip()))
    return tuple(cues)


def _decode_coordinate_provenance(raw):
    """Decode a D1 coordinate evidence object; missing legacy data stays None.

    A present but malformed value is rejected rather than downgraded to
    untrusted coordinates.  D2 will persist this canonical JSON shape.
    """

    if raw is None or raw == "":
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("coordinate_provenance is not JSON") from exc
    if not isinstance(raw, dict):
        raise ValueError("coordinate_provenance must be an object")
    from .corpus_places import CoordinateProvenance

    return CoordinateProvenance.model_validate(raw)


# ---------------------------------------------------------------------------
# Step 2.2b — closer_b geometry (bearing + ±45° wedge)
# ---------------------------------------------------------------------------

# Half-angle of the directional wedge around the A→B bearing. A candidate B'
# counts as "in the same direction" when its A→B' bearing lies within ±45° of
# the A→B bearing — a 90°-wide cone pointing at the originally-requested B.
CLOSER_B_WEDGE_HALF_ANGLE_DEG: float = 45.0


def bearing(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Compass bearing in degrees [0, 360) from (lat1,lng1) toward (lat2,lng2).

    Euclidean (equirectangular) bearing: longitude differences are scaled by
    cos(mean latitude) so a degree of longitude is weighted like the local
    east-west distance. 0° = due north, 90° = due east. Adequate for the
    short, in-city A→B' separations the wedge test compares — we never need
    great-circle bearing precision here, only relative direction.
    """
    mean_lat_rad = math.radians((lat1 + lat2) / 2.0)
    dx = (lng2 - lng1) * math.cos(mean_lat_rad)  # east component
    dy = lat2 - lat1  # north component
    # atan2(east, north) gives a clockwise-from-north compass bearing.
    return math.degrees(math.atan2(dx, dy)) % 360.0


def _angular_diff_deg(a: float, b: float) -> float:
    """Smallest absolute difference between two bearings, in [0, 180]."""
    diff = abs(a - b) % 360.0
    return diff if diff <= 180.0 else 360.0 - diff


def in_wedge(
    origin_lat: float,
    origin_lng: float,
    axis_lat: float,
    axis_lng: float,
    target_lat: float,
    target_lng: float,
    *,
    half_angle_deg: float = CLOSER_B_WEDGE_HALF_ANGLE_DEG,
) -> bool:
    """True iff ``target`` lies inside the ±half_angle wedge around the
    origin→axis bearing.

    The wedge is centred on the bearing from ``origin`` to ``axis`` (the A→B
    direction). ``target`` (a candidate B') is inside when the angular gap
    between its origin→target bearing and the axis bearing is ≤ ``half_angle``.
    """
    axis_bearing = bearing(origin_lat, origin_lng, axis_lat, axis_lng)
    target_bearing = bearing(origin_lat, origin_lng, target_lat, target_lng)
    return _angular_diff_deg(axis_bearing, target_bearing) <= half_angle_deg


def _closer_b_alternative(
    *,
    start_lat: float,
    start_lng: float,
    end_lat: float,
    end_lng: float,
    duration_min: int,
    interest: frozenset[str],
    snapshot: CorpusSnapshot,
    leg_cost_fn: LegSecondsFn,
    walk_budget: int | None = None,
    planning_policy: RoutePlanningPolicy = DEFAULT_ROUTE_PLANNING_POLICY,
) -> FeasibilityAlternative | None:
    """Build the Step 2.2b 'closer_b' alternative, or None when none fits.

    Scans the LANDMARK-tier POIs (``poi_role`` in the eligible roles, ``tier``
    in ``ANCHOR_TIERS``, >=1 active beat) and keeps those whose routed A->B'
    leg fits inside the walk budget — using the very divisor the caller passes
    (``leg_fn or default_leg_seconds``), never straight-line haversine.

    NOTE (Step 3.5 model switch): a suggested DESTINATION B' is intentionally
    still restricted to ``ANCHOR_TIERS`` — "end your walk here" should point at
    a real landmark, never a tier-1 footnote. So this pool is deliberately
    NARROWER than select_route's now-gateless corridor candidate pool; the two
    are no longer identical. The ranking uses ``poi_score``, whose lens factor
    is now the floored ``lens_relevance``, so a lens-miss landmark ranks below a
    lens-hit one rather than tying at zero.

    Among the in-budget anchors:
    - If any lie inside the ±45° wedge around the A→B bearing, pick the highest
      ``poi_score`` (tie-break ascending ``id``) inside the wedge.
    - Otherwise (empty wedge), fall back to the highest ``poi_score`` (tie-break
      ascending ``id``) in-budget anchor regardless of bearing.

    Returns None when no anchor is reachable inside the walk budget, so the
    refusal omits closer_b entirely in that case. ``spine`` is None at this
    pre-selection point, so ``poi_score`` evaluates with neutral area
    alignment (the rank still reflects tier x richness x lens fit x role).
    """
    budget = (
        route_planning_budget(duration_min, planning_policy).walk_budget_seconds
        if walk_budget is None
        else walk_budget
    )

    in_budget: list[POI] = []
    for poi in snapshot.pois:
        if poi.poi_role not in POI_ROLE_MULTIPLIER or POI_ROLE_MULTIPLIER[poi.poi_role] <= 0.0:
            continue
        if poi.tier not in ANCHOR_TIERS:
            continue
        if not _has_active_beats(poi, snapshot):
            continue
        t_ab_prime = leg_cost_fn(start_lat, start_lng, poi.lat, poi.lng)
        if t_ab_prime <= budget:
            in_budget.append(poi)

    if not in_budget:
        return None

    def _rank_key(p: POI) -> tuple[float, str]:
        # Highest poi_score first (negate), ascending id as the deterministic
        # tie-break — mirrors every other selection ranking in this module.
        return (-poi_score(p, None, interest, snapshot), p.id)

    in_wedge_anchors = [
        p for p in in_budget if in_wedge(start_lat, start_lng, end_lat, end_lng, p.lat, p.lng)
    ]
    pool = in_wedge_anchors if in_wedge_anchors else in_budget
    target = min(pool, key=_rank_key)
    return FeasibilityAlternative(
        kind="closer_b",
        duration_min=duration_min,
        drop_end=True,
        poi_id=target.id,
        lat=target.lat,
        lng=target.lng,
    )


# ---------------------------------------------------------------------------
# Step 2.4 — fixed-destination B-materialization (HYBRID)
# ---------------------------------------------------------------------------


def _materialize_fixed_end_b(
    selected: list[POI],
    *,
    end_lat: float,
    end_lng: float,
) -> tuple[list[POI], POI]:
    """Resolve a fixed end B to a concrete POI inside ``selected`` for ORDER.

    HYBRID rule (locked):
    - If a selected POI sits within ``B_SNAP_PROXIMITY_M`` of B, snap B onto
      the nearest such POI (ties broken by ascending id for determinism) and
      return it unchanged — ``selected`` is returned as-is.
    - Otherwise synthesize a sentinel POI at B's *exact* coordinate
      (``poi_role='stop'``, neutral tier, no beats) and return a new list
      with the sentinel appended, so Held-Karp can pin it as ``fixed_end``.

    All members of ``selected`` are already in-corridor on the fixed-end path
    (the §2.3 corridor gate filtered the candidate pool), so the snap pool is
    exactly ``selected``. The returned POI is guaranteed present in the
    returned list — the precondition ``order_stops(fixed_end=...)`` enforces.
    """
    nearest: POI | None = None
    nearest_dist = float("inf")
    for poi in selected:
        d = haversine_m(end_lat, end_lng, poi.lat, poi.lng)
        # Strictly-closer keeps the first-seen on a tie; the explicit id
        # tie-break below makes the choice independent of list order.
        if d < nearest_dist or (d == nearest_dist and nearest is not None and poi.id < nearest.id):
            nearest_dist = d
            nearest = poi

    if nearest is not None and nearest_dist <= B_SNAP_PROXIMITY_M:
        return selected, nearest

    sentinel = end_b_sentinel_poi(end_lat, end_lng)
    return [*selected, sentinel], sentinel


def end_b_sentinel_poi(end_lat: float, end_lng: float) -> POI:
    """THE one constructor for the A→B end sentinel — its id encodes its own
    coordinate, so a persisted trip can re-materialize it (see
    ``end_b_sentinel_from_id``) instead of mistaking the engine's own marker for
    corpus drift (Phase 6 W6.12: Sofia's day could never be composed — 409
    "corpus_changed" over ``__end_b__48.858300_2.347000``)."""
    return POI(
        id=f"{END_B_SENTINEL_PREFIX}{end_lat:.6f}_{end_lng:.6f}",
        name=END_B_SENTINEL_NAME,
        tier=B_SENTINEL_TIER,
        poi_role=B_SENTINEL_POI_ROLE,
        lat=end_lat,
        lng=end_lng,
    )


def end_b_sentinel_from_id(poi_id: str) -> POI | None:
    """Rebuild the sentinel a persisted route carries, from its own id; None for
    a non-sentinel or malformed id (the caller then treats it as a real POI)."""
    if not poi_id.startswith(END_B_SENTINEL_PREFIX):
        return None
    try:
        lat_s, lng_s = poi_id[len(END_B_SENTINEL_PREFIX):].split("_")
        return end_b_sentinel_poi(float(lat_s), float(lng_s))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Selection (pure)
# ---------------------------------------------------------------------------


def expanded_interest_lenses(
    lenses: Iterable[str] | None, snapshot: CorpusSnapshot
) -> frozenset[str] | None:
    """The requested subjects plus their one-hop family — parent and child, the SAME
    hop `_lens_relation` classifies POIs by, read off ``snapshot.lens_neighbors`` and
    spelled once for the beat matcher. Beats carry leaf lenses only, so a request for
    a parent ("history") matches nothing at a beat without this; with it, the parent
    stands for its children. None stays None: no request, no bias."""
    if not lenses:
        return None
    low = frozenset(s.lower() for s in lenses)
    family = set(low)
    for lens in low:
        family |= snapshot.lens_neighbors.get(lens, frozenset())
    return frozenset(family)


def build_poi_beat_plans(
    route: Route, snapshot: CorpusSnapshot, *, lenses: Iterable[str] | None
) -> tuple[POIBeats, ...]:
    """Ordered per-POI beat plans for a route, with each co-located demoted
    sibling's beats merged into its host (the single source shared by
    ``/trips/generate``, ``/trips/{id}/compose``, ``scripts/tour_build.py`` and
    the golden harnesses). Vignette handling + ``BeatSequence`` construction stay
    at the call sites (they differ per surface). C9b: an identity refactor of the
    three production loops that already merged ``demoted_beats``; the golden
    harnesses are routed through it too so they measure the shipped pipeline.
    """
    require_materialized_snapshot(snapshot, operation="beat planning")
    family = expanded_interest_lenses(lenses, snapshot)
    plans: list[POIBeats] = []
    for poi in route.pois:
        beats = list(snapshot.beats_for(poi.id))
        beats.extend(route.demoted_beats.get(poi.id, ()))
        plans.append(select_poi_beats(poi, beats, interest_lenses=family))
    return tuple(plans)


def build_poi_extra_beats(
    route: Route,
    snapshot: CorpusSnapshot,
    voiced_by_poi: dict[str, tuple[str, ...]],
    *,
    lenses: Iterable[str] | None,
) -> dict[str, tuple[str, ...]]:
    """KE1: per-POI "keep exploring here" extras — the un-voiced beats of each stop.

    Uses the SAME merged beat pool (``snapshot.beats_for`` + ``demoted_beats``) as
    :func:`build_poi_beat_plans`, so the extras are exactly the uncapped plan minus
    what the tour voiced, in priority order. POIs with no extras are omitted.
    """
    require_materialized_snapshot(snapshot, operation="extra-beat planning")
    family = expanded_interest_lenses(lenses, snapshot)
    out: dict[str, tuple[str, ...]] = {}
    for poi in route.pois:
        beats = list(snapshot.beats_for(poi.id))
        beats.extend(route.demoted_beats.get(poi.id, ()))
        extras = extra_beat_ids(poi, beats, voiced_by_poi.get(poi.id, ()), interest_lenses=family)
        if extras:
            out[poi.id] = extras
    return out


def build_poi_extra_narration(
    extra_by_poi: dict[str, tuple[str, ...]],
    snapshot: CorpusSnapshot,
) -> dict[str, str]:
    """KE2: per-POI "keep exploring here" narration — DETERMINISTIC, no LLM.

    The extras are CURATED CORPUS beats (each carries a real ``script_body``), so
    they are faithful by construction — unlike the freely-composed main narration
    they need NO LLM entailment / VERIFY gate. We therefore stitch them the same
    way the main narration voices its beat portion (``generation._beat_to_sentences``
    -> ``split_sentences``), joining sentence texts with a single space — the exact
    join ``stop_narration_text`` uses for the per-stop audio the /audio path voices.

    ``extra_by_poi`` maps poi_id -> ORDERED extra beat ids (the output of
    :func:`build_poi_extra_beats`); the join preserves that priority order, so
    extra_beat_ids and extra_narration are consistent by construction. A POI's
    resolvable extras with empty/absent bodies contribute nothing; a POI whose
    extras yield no text at all is omitted (no empty-string narration is stored).
    """
    require_materialized_snapshot(snapshot, operation="extra narration")
    from .generation import split_sentences  # local import: avoid a module cycle

    beats_by_id = {
        ref.id: (poi_id, ref) for poi_id, refs in snapshot.beats_by_poi.items() for ref in refs
    }
    out: dict[str, str] = {}
    for poi_id, beat_ids in extra_by_poi.items():
        sentences: list[str] = []
        for bid in beat_ids:
            resolved = beats_by_id.get(bid)
            if resolved is None:
                continue
            actual_poi_id, ref = resolved
            if actual_poi_id != poi_id or not ref.script_body:
                continue
            sentences.extend(split_sentences(ref.script_body))
        narration = " ".join(sentences)
        if narration:
            out[poi_id] = narration
    return out


def _domination_caps(is_exempt: list[bool], audio: list[int]) -> list[int | None]:
    """Per-stop second-caps for the v4 domination-gated governor.

    Exempt stops → ``None`` (uncapped). A non-exempt stop is capped ONLY when it is
    a genuine DOMINATING OUTLIER: it exceeds ``GOVERNOR_SHARE_OF_DELIVERED`` of the
    total delivered audio AND exceeds ``GOVERNOR_DOMINATION_FACTOR`` x the MEAN of
    the OTHER non-exempt stops (it is drowning its peers). Comparing to the mean of
    the others — not the next-largest — closes the pairwise-shielding gap where two
    co-dominators would each hide behind the other. A dominator is capped to the
    share ceiling; capping lowers the total, so we re-check to a fixed point. A
    balanced tour, where no stop dwarfs the mean of its peers, is NEVER capped —
    the whole point of the v4 redesign. A cluster of 3+ NEAR-EQUAL rich stops
    converges to a balanced delivery (only the single largest, if any, trims)
    rather than each being cut. Needs ≥2 non-exempt stops for "domination" to mean
    anything.
    """
    n = len(audio)
    caps: list[int | None] = [None if is_exempt[i] else audio[i] for i in range(n)]
    non_exempt = [i for i in range(n) if caps[i] is not None]
    if len(non_exempt) < 2:
        return caps  # need a dominator + at least one drowned peer
    # Cap the largest current dominator to the ceiling, then re-evaluate. A
    # "dominator" is over the ⅓ ceiling AND exceeds the factor x the MEAN of the
    # OTHER non-exempt stops — compared to the peers it is drowning, not to the
    # next-largest (so two co-dominators can't shield each other, the panel gap).
    # Terminates: each cap strictly lowers the (integer) delivered total, which is
    # bounded below by 0, so the loop runs a finite number of passes.
    while True:
        total = sum(audio[i] if caps[i] is None else caps[i] for i in range(n))
        ceiling = int(GOVERNOR_SHARE_OF_DELIVERED * total)
        target: int | None = None
        for i in non_exempt:
            if caps[i] <= ceiling:
                continue
            others = [caps[j] for j in non_exempt if j != i]
            mean_others = sum(others) / len(others)
            if caps[i] <= GOVERNOR_DOMINATION_FACTOR * max(1.0, mean_others):
                continue  # balanced — not a drowning outlier
            if target is None or caps[i] > caps[target]:
                target = i
        if target is None:
            break
        caps[target] = ceiling
    return caps


def _is_filler_stub(poi: POI, snapshot: CorpusSnapshot, lenses: Iterable[str] | None) -> bool:
    """A dwell-eligible POI too THIN to justify a dedicated stop — demote it to a
    walk-by vignette (the "walked here for one sentence" anticlimax).

    True iff the POI would be a DWELL stop (on-path dwell band) that is NOT a
    landmark (tier < BAND_LANDMARK_TIER) AND whose full emitted audio is under
    ``MIN_DWELL_AUDIO_SECONDS``. The dwell-band guard matters: a silent or already-
    vignette POI is NOT a filler-stub (it was never going to be a dedicated stop),
    so this must not re-promote a silent POI into a walk-by. Shared by the dwell-
    pool filter (keeps it out of the greedy) and :func:`select_vignettes` (routes
    it to a walk-by), so /trips/generate and /trips/compose agree by construction.
    """
    if poi.tier >= BAND_LANDMARK_TIER:
        return False
    score = spotlight(poi, lenses=lenses or None, snapshot=snapshot)
    if not is_dwell_band(band_for_spotlight(score, tier=poi.tier)):
        return False  # vignette/silent already — not a would-be dwell stop
    return planned_capped_audio_seconds(poi, snapshot, lenses, None) < MIN_DWELL_AUDIO_SECONDS


def build_poi_beat_plans_capped(
    route: Route,
    snapshot: CorpusSnapshot,
    *,
    lenses: Iterable[str] | None,
    end_is_none: bool = False,
    narration_density: str | None = None,
) -> tuple[tuple[POIBeats, tuple[str, ...]], ...]:
    """C9 governor v4 — the shared EMISSION choke point: ``(kept, overflow_ids)``
    per POI, in route order. All six build sites (generate, compose, preview,
    tour_build, the two golden harnesses) go through here so the cap lives in ONE
    place.

    Two caps compose (whichever is tighter wins per stop):
    1. The v4 DOMINATION governor (:func:`_domination_caps`) — relative, runs only
       on ``end_is_none`` (round-trip / open-walk); caps a single dominating outlier
       and EXEMPTS the MARQUEE (highest-tier delivered stop, ties -> highest audio)
       and ``route.fixed_end_poi_id``.
    2. The absolute ``MAX_DWELL_AUDIO_SECONDS`` CEILING (C9h) — no stop, marquee
       included, may voice more than this on EITHER route type, so no spot becomes a
       10-minute monologue. The marquee may still dominate, but only up to the
       ceiling.

    Both are WHOLE-BEAT caps via :func:`govern_poi_beats`: trimmed beats are returned
    as keep-exploring overflow (never silently dropped), so no fact is lost.
    """
    plans = build_poi_beat_plans(route, snapshot, lenses=lenses)
    if not plans:
        return ()
    if end_is_none:
        audio = [planned_audio_seconds(plan.beats) for plan in plans]
        exempt_ids: set[str] = set()
        if route.fixed_end_poi_id is not None:
            exempt_ids.add(route.fixed_end_poi_id)
        # The marquee: highest-tier stop, ties -> highest audio. Always a real POI in
        # the delivered route (never a dangling / demoted id).
        marquee = max(range(len(plans)), key=lambda i: (route.pois[i].tier, audio[i]))
        exempt_ids.add(route.pois[marquee].id)
        is_exempt = [poi.id in exempt_ids for poi in route.pois]
        dom_caps = _domination_caps(is_exempt, audio)
    else:
        # A→B: no relative domination governor, but the absolute ceiling still binds.
        dom_caps = [None] * len(plans)
    # Apply the absolute ceiling to EVERY stop: min(domination cap, MAX). A None
    # (exempt / uncapped-by-domination) stop is capped to the MAX; a domination-capped
    # stop keeps the tighter of the two.
    #
    # "LESS TALKING / MORE TALKING" (Phase 4 dial, W4.2 — Fiona & Dev's dial,
    # Paulo's label): narration SUPPLY scales the per-stop ceiling at the ONE
    # emission choke point, so every build site obeys it. "less" halves the
    # ceiling (shorter pieces, more room to talk); "more" raises it by half.
    # Whole-beat caps as ever — trimmed beats land in keep-exploring overflow,
    # never on the floor. None = today's ceiling, byte-identical. The full
    # point-first mechanics (walk-off-at-minute-eight, silent legs) are
    # Phase 6's; this is the supply knob the dial needs to be real.
    ceiling = MAX_DWELL_AUDIO_SECONDS
    if narration_density == "less":
        ceiling = MAX_DWELL_AUDIO_SECONDS // 2
    elif narration_density == "more":
        ceiling = MAX_DWELL_AUDIO_SECONDS + MAX_DWELL_AUDIO_SECONDS // 2
    final_caps = [ceiling if c is None else min(c, ceiling) for c in dom_caps]
    family = expanded_interest_lenses(lenses, snapshot)
    capped = [
        govern_poi_beats(plan, cap, interest=family)
        for plan, cap in zip(plans, final_caps, strict=True)
    ]
    # Protect the last NARRATED stop's closing-friendly beat from the cap. The closing
    # glue fires after the last beat of the last stop WITH content, and
    # reorder_final_stop_for_closing (which runs later, in generate) can only reorder
    # beats the cap KEPT — so if the cap trimmed the callback/climax, the tour ends
    # mid-fact. Skip a beatless fixed-end (A→B) sentinel and protect the real terminal.
    for i in range(len(capped) - 1, -1, -1):
        if capped[i][0].beats:
            capped[i] = _keep_final_closing_beat(
                capped[i], plans[i], allowance_seconds=final_caps[i]
            )
            break
    return tuple(capped)


def planned_audio_by_poi(
    route: Route,
    snapshot: CorpusSnapshot,
    *,
    interest: frozenset[str] | None,
    end_is_none: bool,
    narration_density: str | None = None,
) -> dict[str, int]:
    """What each stop SAYS, in seconds — this route's own CAPPED plan, the same
    emission choke point generation uses (Phase 5 S5.10). Extracted from
    :func:`served_dwell_seconds` so the session's clock (`contingency.stop_clocks`)
    prices narration off the SAME map the final gate does; a second reading of
    the beat plans elsewhere would let the wire clock and the certified day
    disagree about the same stop. **Extends** `served_dwell_seconds`.
    """
    return {
        plan.poi_id: planned_audio_seconds(plan.beats)
        # The density dial threads here so the gate prices the SAME capped
        # audio emission will voice — certified and served stay one quantity.
        for plan, _ in build_poi_beat_plans_capped(
            route,
            snapshot,
            lenses=interest or None,
            end_is_none=end_is_none,
            narration_density=narration_density,
        )
    }


def served_dwell_seconds(
    route: Route,
    snapshot: CorpusSnapshot,
    *,
    interest: frozenset[str] | None,
    end_is_none: bool,
    narration_density: str | None = None,
    listening_rate: float = 1.0,
) -> int:
    """Standing-still seconds this route actually serves.

    ONE definition, and the reason this function exists rather than the sum being
    written at each site. Four places price a route's non-walking time — the final
    band gate, the timebox repair's trial, the fixed-end rescue trim, and the
    generated tour — and while any of them spelled it differently they could
    disagree about the same route. The planner then buys a tour the tourist does
    not get, which is the defect this whole phase removes.

    Per stop it is ``stop_seconds(what the visitor spends there, what it says)``,
    where "what it says" is this route's own CAPPED plan — the same emission choke
    point generation uses, so the gate cannot count narration the tour will not
    voice.

    ``planned_visit_seconds`` empty means nobody priced this route, and every stop
    falls back to the length of its own narration — exactly the pre-Phase-3
    behaviour, which is what makes the four harnesses that build a bare Route safe.

    ``listening_rate`` (design §4.1, Paulo; Phase 5 S5.10) scales what a stop SAYS,
    never what the visitor spends there: a person who consumes narration at 1.5x
    its stated length costs each stop ``max(visit, audio x 1.5)``. It rides
    ``ReplanContext.listening_rate`` and is 1.0 — byte-identical — everywhere else.
    """
    audio_by_id = planned_audio_by_poi(
        route,
        snapshot,
        interest=interest,
        end_is_none=end_is_none,
        narration_density=narration_density,
    )
    # Iterate the ROUTE's stops, not the plans: a stop with no beat plan at all
    # (a materialized fixed-end sentinel is the common case) still costs the
    # visitor the time they stand there.
    return sum(
        stop_seconds(
            route.planned_visit_seconds.get(poi.id, 0),
            listened_seconds(audio_by_id.get(poi.id, 0), listening_rate),
        )
        for poi in route.pois
    )


def _keep_final_closing_beat(
    capped: tuple[POIBeats, tuple[str, ...]],
    full_plan: POIBeats,
    *,
    allowance_seconds: int = MAX_DWELL_AUDIO_SECONDS,
) -> tuple[POIBeats, tuple[str, ...]]:
    """If the per-stop cap trimmed the FINAL stop's closing-friendly beat
    (callback > climax > longest, per ``_find_closing_friendly_index``), fit it
    inside the SAME allowance by returning the lowest-priority kept tail beats
    to overflow.  This closes on a real ending without the former one-beat cap
    leak.  Overflow is rebuilt in full-plan order, so every displaced source
    fact remains available through keep-exploring.

    No-op when nothing was trimmed, the closing beat already survived, or the
    closing beat alone exceeds the allowance (no whole-beat plan can satisfy
    both constraints in that malformed-corpus case)."""
    kept, overflow = capped
    if not overflow:
        return capped
    idx = _find_closing_friendly_index(full_plan.beats)
    if idx is None:
        return capped
    closing = full_plan.beats[idx]
    if any(b.id == closing.id for b in kept.beats):
        return capped
    if closing.id not in overflow or planned_audio_seconds((closing,)) > allowance_seconds:
        return capped

    # govern_poi_beats kept a priority prefix.  Remove from its tail first, so
    # the cold-open/head is displaced only if it is the sole way to fit the
    # closing beat.  At least the closing remains because it fits by itself.
    fitted = list(kept.beats)
    while fitted and planned_audio_seconds((*fitted, closing)) > allowance_seconds:
        fitted.pop()
    fitted.append(closing)
    fitted_ids = {beat.id for beat in fitted}
    new_kept = kept.model_copy(update={"beats": tuple(fitted)})
    new_overflow = tuple(beat.id for beat in full_plan.beats if beat.id not in fitted_ids)
    return new_kept, new_overflow


def planned_capped_audio_seconds(
    poi: POI,
    snapshot: CorpusSnapshot,
    lenses: Iterable[str] | None,
    allowance: int | None,
) -> int:
    """Voiced seconds a POI's plan CONSUMES under the C9 governor: the capped
    (allowance-truncated) beats for an incidental stop, or the full uncapped plan
    for an exempt anchor (``allowance is None``). This is selection's FLOOR-LESS
    audio currency — no tier floor; tier dwell survives only as C8's reported
    minute floor. Pure per (poi, lenses, allowance); memoize at the call site.
    """
    family = expanded_interest_lenses(lenses, snapshot)
    plan = select_poi_beats(poi, snapshot.beats_for(poi.id), interest_lenses=family)
    kept, _overflow = govern_poi_beats(plan, allowance, interest=family)
    return planned_audio_seconds(kept.beats)


#: The longest walking contour Valhalla will answer. MEASURED against the live
#: engine on 2026-08-06: a 120-minute contour is accepted and a 121-minute contour
#: is refused with ``{"error_code":151,"error":"Exceeded max time: 120"}``.
#:
#: WHY THIS MATTERS FAR MORE THAN IT LOOKS. ``_reach_predicate`` uses the Valhalla
#: road polygon as its PRIMARY admission test and the analytic circle only as a
#: fallback when the polygon is unavailable. So whenever the contour asked for
#: exceeds this limit, the circle silently becomes the real reach test — losing the
#: across-the-river protection the polygon exists to provide — and whenever it does
#: not, the circle is NOT the reach test and quoting its radius describes something
#: the planner did not use. Any report of "the radius searched" is meaningless
#: without saying which of the two was operative.
VALHALLA_MAX_CONTOUR_MINUTES: int = 120


def reach_envelope_searched(
    input: TourInput,
    *,
    planning_policy: RoutePlanningPolicy = DEFAULT_ROUTE_PLANNING_POLICY,
) -> tuple[float, int]:
    """The circle the reach test ACTUALLY searches: ``(radius_m, isochrone_minutes)``.

    ONE definition, called by ``select_route`` and by the ``tour-build`` harness,
    so the harness reports what the planner DID rather than what it ought to do.

    ONE ARM, DELIBERATELY. There used to be two. A fixed-destination request sized
    its radius from the TOTAL time ceiling — walking PLUS narration — so a
    300-minute request searched **12,259 m** where the open arm's walk envelope
    gives **4,444 m**: a factor of 2.76, and 7.6x the area. Paris is about 10 km
    across, so the fixed-end arm searched a circle wider than the city in order to
    plan a walk between two points half an hour apart. That is the arithmetic by
    which a Rue Royale to Notre-Dame request reached Parc de la Villette, 5.6 km
    away in the wrong direction.

    The reach question is identical on both shapes — *how far can this visitor walk
    from here?* — so there is no route shape for which a walking-plus-talking
    ceiling is the right bound. Hence one arm.

    WHY THE HARNESS SHARES THIS RATHER THAN RE-DERIVING IT. A harness that computed
    the radius from the requested duration would have printed 4,444 m both before
    and after this fix, and the phase verifying the fix would have recorded "no
    change" while proving nothing. See VALHALLA_MAX_CONTOUR_MINUTES above for the
    second half of that hazard: this circle is only the FALLBACK admission test, so
    a report of it must also say whether the road polygon was available.

    Reads `input.walking_pace` directly (plan S2.4) rather than taking a
    separate parameter — the harness calls this with the same `TourInput` it
    built, so a paced request's printed reach radius is automatically the
    smaller, correct one with no second thing to keep in sync.
    """
    planning_budget = route_planning_budget(input.duration_min, planning_policy)
    pace_multiplier = input.walking_pace if input.walking_pace is not None else 1.0
    return (
        envelope_radius_m(
            input.duration_min,
            round_trip=input.round_trip,
            planning_policy=planning_policy,
            pace_multiplier=pace_multiplier,
        ),
        _isochrone_walk_minutes(
            input.duration_min,
            round_trip=input.round_trip,
            walk_minutes=planning_budget.walk_envelope_minutes,
        ),
    )


def select_route(
    input: TourInput,
    snapshot: CorpusSnapshot,
    *,
    routing_client: RoutingClient | None = None,
    score_penalty: dict[str, float] | None = None,
    planning_policy: RoutePlanningPolicy = DEFAULT_ROUTE_PLANNING_POLICY,
    replan: ReplanContext | None = None,
) -> Route:
    """THE planner's door: plan the day once, then certify its leg cap on the
    STREET legs of the final route (Phase 5 S5.4).

    ``replan`` (Phase 5 S5.6, `contract.ReplanContext`) makes the plan RELATIVE
    to a day that already exists — the session's replans hand one in. THE FIRST
    CUSTOMER IS THE "MORE BREAKS" DIAL (Phase-4 CARRIED 3): a rest cadence with no
    context of its own is planned as a replan of the SAME request without the
    cadence — the base day's anchor and promises held, its longest leg a ceiling —
    because W4.2 locked semantics 2 says the dial "may NEVER lengthen the longest
    leg, never fund a rest by shaving an anchor", and a fresh plan under a
    rest-shrunk budget did exactly that (W5.1 (b)3: PdV walking 23 -> 39, longest
    10 -> 18, Carnavalet 47-inside -> Rue des Rosiers 25-outside, no rest promise).
    Two calls to the one planner; no second planner (plan §10.8.1).

    ``_select_route_once`` is the whole planning pass (spine, greedy, repair,
    dials, ordering, body stops, the band gates). What this wrapper adds is the
    one check no pass upstream can make: the per-leg cap read off the FINAL
    routed transits through ``routing.longest_walk_minutes`` — the same number,
    through the same rounding, the head line prints. Upstream the cap is checked
    on the legs each pass minted (`_insertion_legs_fit_cap`, the repair's chains),
    but a later DROP merges two legs — measured live 2026-08-18: the repair
    certified Carnavalet -> Victor Hugo 538 s and Victor Hugo -> Place des Vosges
    176 s under a 540 s cap, co-located demotion folded Victor Hugo away, and the
    merged 576 s leg shipped under "Shorter walks = 9" (Phase-4 CARRIED 1;
    design §4.5.3: "dropping a stop MERGES its two legs"). And the estimate can
    simply differ from the street (W5.1 (b)1: -187 s to +285 s per leg). On a
    breach: tighten by the overshoot and retry ONCE; if the street still breaks
    the cap, REFUSE with the cap named in the dial's own words (W4.12 fix 10) —
    never ship a head line that contradicts the dial the person turned
    (05-step-free-visitor.md, breaks bullet 1). ``max_leg_minutes`` None = today,
    byte-identical: the wrapper is a pass-through.
    """
    if replan is None and input.rest_cadence_minutes is not None:
        replan = _relative_context_for_the_cadence_dial(
            input,
            snapshot,
            routing_client=routing_client,
            score_penalty=score_penalty,
            planning_policy=planning_policy,
        )
    try:
        route = _select_route_once(
            input,
            snapshot,
            routing_client=routing_client,
            score_penalty=score_penalty,
            planning_policy=planning_policy,
            replan=replan,
        )
    except (CertificationPlanningInfeasibleError, TourabilityRefusedError):
        if not input.lenses:
            raise
        # THE SUBJECT VALVE (Phase 9 S3): the gate above must never turn a day
        # a thin area used to serve into a refusal — the landmark-disclosure
        # rule's own scenario, a lensed start where everything reachable misses.
        # One retry with the gate lifted, and the day SAYS SO through the same
        # channel every quiet degradation travels.
        record(
            kind="subject_gate_relaxed",
            human=(
                "This area is thin for the subjects you chose, so some stops "
                "sit outside them rather than serving no day at all."
            ),
            component="selection.select_route",
        )
        route = _select_route_once(
            input,
            snapshot,
            routing_client=routing_client,
            score_penalty=score_penalty,
            planning_policy=planning_policy,
            replan=replan,
            lens_gate=False,
        )
    cap = input.max_leg_minutes
    # (§4.5.3's base-day ceiling binds INSIDE the pass — every admission, drop and
    # fold reads it as `max_leg_seconds` — but the street certification below is the
    # person's own limit: a bench detour a minute over the base's longest leg is
    # not a refusal-grade contradiction of any dial the person turned.)
    if cap is None:
        return route
    longest = longest_walk_minutes(route.transits)
    if longest <= cap:
        _say_when_the_walk_limit_binds(
            route,
            input,
            snapshot,
            routing_client=routing_client,
            score_penalty=score_penalty,
            planning_policy=planning_policy,
            replan=replan,
        )
        return route
    # TIGHTEN AND RETRY, ONCE, at the exceeded value: plan again under a cap
    # shrunk by exactly the overshoot, so the legs the street stretches land
    # under the cap the person set. One retry, never a loop (the plan's own
    # sabotage line): a second miss is a refusal.
    tightened = cap - (longest - cap)
    retried: Route | None = None
    if tightened >= 1:
        try:
            retried = _select_route_once(
                input.model_copy(update={"max_leg_minutes": tightened}),
                snapshot,
                routing_client=routing_client,
                score_penalty=score_penalty,
                planning_policy=planning_policy,
                replan=replan,
            )
        except (CertificationPlanningInfeasibleError, TourabilityRefusedError):
            retried = None
        if retried is not None and longest_walk_minutes(retried.transits) <= cap:
            # THE RETRY MUST NOT PAY WITH A REST (W5.14, all eleven — Rosemary:
            # "removing my rest to satisfy my leg cap is backwards — the rest exists
            # because of the legs"). A retried day that lost a rest the first day had
            # seated is a gutted day, not an answer: say the honest line below and
            # let HER decide (allow the longer walk, or a shorter day).
            rests_before = {p.id for p in route.pois if p.poi_role == "body"}
            rests_after = {p.id for p in retried.pois if p.poi_role == "body"}
            if rests_before <= rests_after:
                return retried
            retried = None
    if replan is not None and replan.floor_zero:
        # A REPLANNED remainder never refuses (W5.2 R1.1): a walk home longer than
        # the person's limit is a fact to SAY — "the entry must say where I sit on
        # the way" (Rosemary) — so the breach rides the route for S5.11's one line.
        return (retried or route).model_copy(
            update={
                "leg_cap_breach_seconds": max(
                    0, longest_walk_minutes((retried or route).transits) * 60 - cap * 60
                )
            }
        )
    budget = route_planning_budget(
        input.duration_min, planning_policy, end_hardness=input.end_hardness
    )
    # The way out is structured the same way every band refusal's is: the
    # closest day's own length as the duration to ask for (the `extend` row is
    # "a day of about N minutes" — the twin of "ask for a shorter day").
    closest_elapsed = served_elapsed_seconds(
        route.total_walk_seconds,
        served_dwell_seconds(
            route,
            snapshot,
            interest=frozenset(input.lenses or []),
            end_is_none=input.end is None,
            narration_density=input.narration_density,
            listening_rate=replan.listening_rate if replan is not None else 1.0,
        ),
    )
    alternatives, gap_minutes = _band_alternatives(
        input=input, planning_policy=planning_policy, best_elapsed_seconds=closest_elapsed
    )
    raise CertificationPlanningInfeasibleError(
        policy_id=planning_policy.policy_id,
        minimum_elapsed_seconds=budget.minimum_elapsed_seconds,
        maximum_elapsed_seconds=budget.nominal_elapsed_seconds,
        best_elapsed_seconds=closest_elapsed,
        reason=(
            # W4.12 fix 11's honest note, now the refusal it stood in for: the
            # closest day, its street number, the person's own limit.
            f"the closest day has a walk of about {longest} minutes by the street "
            f"route, over the {cap}-minute limit on any single walk — allow "
            f"longer walks, or ask for a shorter day"
        ),
        alternatives=alternatives,
        gap_minutes=gap_minutes,
    )


#: A day of this many story stops or fewer, under the person's walking cap and below
#: its nominal, is the shape the panel called "a museum ticket" (W5.14: Rosemary's
#: one-stop, no-walking, no-rest day under her preset's 12 minutes, with the bench day
#: one street minute away) — the cap is suspected of binding and the planner looks
#: once at a longer one. Three-stop days and up are never re-planned for this.
WALK_LIMIT_BINDS_MAX_STOPS: int = 2
#: How far beyond the cap the planner looks, once, for the fuller day it offers.
WALK_LIMIT_OFFER_MINUTES: tuple[int, ...] = (1, 2, 3)
WALK_LIMIT_BINDS_DEGRADATION = "walk_limit_binds"


def _say_when_the_walk_limit_binds(
    route: Route,
    input: TourInput,
    snapshot: CorpusSnapshot,
    *,
    routing_client,
    score_penalty,
    planning_policy,
    replan: ReplanContext | None,
) -> None:
    """One honest line when the person's walking cap is what made the day thin
    (W5.14, all eleven, Q1 — Rosemary: "say the line to me, and offer the answer you
    already own"). A day of one or two story stops that honours the cap but runs
    below its nominal is looked at ONCE more at a cap one to three minutes longer; if
    a materially fuller day exists there (a quarter more experience, more stops), the
    planner SAYS so on the existing degradations channel (the itinerary shows the row)
    and names the walk it would take — she decides. Never a refusal (the W4.2 line at
    50 % stands), never silent. Nothing is spent on a day of three stops or more, or
    on a replan."""
    # A plan-time relative replan (the rest-cadence dial: floor_zero False) is a
    # fresh day and gets the line; a live TAIL (floor zero) never does.
    if (replan is not None and replan.floor_zero) or input.max_leg_minutes is None:
        return
    if not route.pois:
        return
    cap = input.max_leg_minutes
    budget = route_planning_budget(
        input.duration_min, planning_policy, end_hardness=input.end_hardness
    )
    if budget.nominal_elapsed_seconds <= 0:
        return

    def experience_of(r: Route) -> int:
        return served_elapsed_seconds(
            r.total_walk_seconds,
            served_dwell_seconds(
                r,
                snapshot,
                interest=frozenset(input.lenses or []),
                end_is_none=input.end is None,
                narration_density=input.narration_density,
            ),
        ) - sum(r.planned_queue_seconds.values())

    story_stops = [p for p in route.pois if p.poi_role != "body" and not _has_end_sentinel([p])]
    if len(story_stops) > WALK_LIMIT_BINDS_MAX_STOPS:
        return
    have = experience_of(route)
    if have >= budget.nominal_elapsed_seconds:
        return
    for extra in WALK_LIMIT_OFFER_MINUTES:
        try:
            fuller = _select_route_once(
                input.model_copy(update={"max_leg_minutes": cap + extra}),
                snapshot,
                routing_client=routing_client,
                score_penalty=score_penalty,
                planning_policy=planning_policy,
            )
        except (CertificationPlanningInfeasibleError, TourabilityRefusedError):
            continue
        street = longest_walk_minutes(fuller.transits)
        if street > cap + extra:
            continue  # its own street legs break the longer cap; not an offer
        gain = experience_of(fuller)
        if gain < have * 1.25 or len(fuller.pois) <= len(route.pois):
            continue
        rests = sum(1 for p in fuller.pois if p.poi_role == "body")
        stops = sum(1 for p in fuller.pois if p.poi_role != "body" and not _has_end_sentinel([p]))
        record(
            kind=WALK_LIMIT_BINDS_DEGRADATION,
            human=(
                f"With walks of up to {max(street, cap + extra)} minutes this day would "
                f"have {stops} stops"
                + (" and a rest" if rests else "")
                + f", and run about {gain // 60} minutes instead of {have // 60}; "
                "allow longer walks to get it."
            ),
            component="selection.select_route",
            cap_minutes=str(cap),
            offered_cap_minutes=str(cap + extra),
            experience_seconds=str(have),
            offered_experience_seconds=str(gain),
        )
        return


def _relative_context_for_the_cadence_dial(
    input: TourInput,
    snapshot: CorpusSnapshot,
    *,
    routing_client: RoutingClient | None,
    score_penalty: dict[str, float] | None,
    planning_policy: RoutePlanningPolicy,
) -> ReplanContext | None:
    """The "More breaks" dial's context: the SAME request without the cadence,
    planned once, then held — its anchor and promises protected, its longest
    street leg a ceiling (W4.2 locked semantics 2; Phase-4 CARRIED 3). If the base
    cannot be planned at all there is nothing to hold, and the cadenced request
    plans fresh (None)."""
    base_input = input.model_copy(update={"rest_cadence_minutes": None})
    try:
        base = _select_route_once(
            base_input,
            snapshot,
            routing_client=routing_client,
            score_penalty=score_penalty,
            planning_policy=planning_policy,
        )
    except (CertificationPlanningInfeasibleError, TourabilityRefusedError):
        return None
    protected = tuple(
        dict.fromkeys(
            [pr.poi_id for pr in base.promises if pr.kind in ("anchor", "pinned", "finish")]
            + list(input.pinned_poi_ids)
        )
    )
    longest = max((leg_walk_seconds(t) for t in base.transits), default=None)
    return ReplanContext(
        protected_poi_ids=protected,
        longest_leg_ceiling_seconds=longest,
        # The SAME day plus rests: the pool is the base day's own stops (locked
        # semantics 2 — the dial moves cadence only, it does not re-cut the day).
        keep_to_poi_ids=tuple(p.id for p in base.pois),
        floor_zero=False,
    )


def _select_route_once(
    input: TourInput,
    snapshot: CorpusSnapshot,
    *,
    routing_client: RoutingClient | None = None,
    score_penalty: dict[str, float] | None = None,
    planning_policy: RoutePlanningPolicy = DEFAULT_ROUTE_PLANNING_POLICY,
    replan: ReplanContext | None = None,
    lens_gate: bool = True,
) -> Route:
    """Compute the spine, score POIs, run greedy selection. Returns a Route.

    ONE planning pass; ``select_route`` is the public door that certifies its
    leg cap on the street legs afterwards (Phase 5 S5.4).

    M2: ``routing_client`` only enriches the final Route's transits with
    routed leg_seconds/polylines (via summarise_route) — selection scoring
    stays on haversine until M3.

    ``score_penalty`` (poi_id → multiplicative factor) is the per-place score
    channel. Its live producers are the RAIN pricing below (a stop with no
    cover dims on a wet day — commit 71654c97) and the skip-the-queues dial;
    leave None for normal selection. It was born as the k-flavours diversity
    knob; the flavours died at Phase 4 (design §8.1) and the channel stayed.

    Phase 6 added two guards before the greedy:

    1. **Density gate** (§3.7) — call ``density.assess_snapshot(input, snapshot)``. RED
       raises ``TourabilityRefusedError``; YELLOW attaches the assessment to
       ``Route.tourability`` for the harness to surface.
    2. **Zero-beat-POI exclusion** — POIs with no active beats are
       removed from the candidate pool before scoring. This was the
       Phase 5 Petit Palais bug: a tier-4 POI with 0 beats was selected
       as an anchor purely because it sat on the route corridor.
    """
    if snapshot.place_manifest is not None and not isinstance(snapshot, MaterializedCorpusSnapshot):
        raise ValueError(
            "typed corpus must be validated by materialize_corpus_snapshot "
            "before density or selection"
        )
    # "ALREADY TRUE" — the leg cap's first answer (W4.2 locked semantics 1,
    # measured at the W4.12 close). A cap that the un-capped day already
    # satisfies cannot bind, and the ruling is that it then says so and changes
    # nothing. It did not: the capped path plans by a bounded search whose
    # timebox repair can only DROP stops, and dropping a middle stop merges two
    # walks into one longer walk that breaks the cap — so under a cap the
    # repair could not shrink an over-long day into band, and a 25-minute cap
    # REFUSED the flagship (Tuileries→Notre-Dame, 300 min) whose un-capped day
    # has an 18-minute longest walk and plans in 47 s. Loosening a limit
    # deleted the tour (Camille), with the same numbers at 9 and 25 (Rosemary:
    # "if loosening a limit changes not one digit, the limit is not in the
    # sum"). So: plan without the cap first; if that day already honours the
    # cap, it IS the day. Only a cap the un-capped day breaks reaches the
    # bounded search below, which is the case that search exists for. The
    # replan-capable repair (drop AND re-fill under a cap) is Phase 5's work.
    if input.max_leg_minutes is not None:
        relaxed = _select_route_once(
            input.model_copy(update={"max_leg_minutes": None}),
            snapshot,
            routing_client=routing_client,
            score_penalty=score_penalty,
            planning_policy=planning_policy,
            replan=replan,
        )
        longest = max((leg_walk_seconds(t) for t in relaxed.transits), default=0)
        if longest <= input.max_leg_minutes * 60:
            return relaxed
    start_lat, start_lng = input.start
    interest = frozenset(input.lenses or [])
    # The request's own hardness bends this ONE budget (redesign §2.3): every
    # repair, rescue and final band check below receives this object, so `open`
    # dropping the floor and `wall` capping the ceiling propagate everywhere
    # without a second derivation. Helper call sites keep the firm default.
    planning_budget = route_planning_budget(
        input.duration_min, planning_policy, end_hardness=input.end_hardness
    )
    if replan is not None and replan.floor_zero:
        # A TAIL HAS NO FLOOR (Phase 5 S5.6; W5.2 R1.1, 11/11): the remainder of
        # a day already walked is never padded toward a number and never refused
        # for being short — one leg home with no stop is a legal answer.
        planning_budget = replace(planning_budget, minimum_elapsed_seconds=0)
    # THE PARTY'S PACE (redesign §2.4; plan S2.4), derived once and threaded
    # into every mechanism that must slow together: the walk-cost divisor
    # (`leg_fn`, below) and the reach circle's road-network test
    # (`_reach_predicate`). The analytic reach radius and density's own copy
    # of it read `input.walking_pace` straight off the input instead of
    # taking this as a second parameter — see `reach_envelope_searched` and
    # `density.assess` — so there is only one number to keep in sync, not two.
    # 1.0 = axis unset = today, byte-identical.
    pace_multiplier = input.walking_pace if input.walking_pace is not None else 1.0
    # THE PARTY'S ROUTE SURFACE (redesign §2.4; plan S2.7), per W2.1's live
    # capability proof (phase2-ledger.md): step_penalty genuinely moves a
    # route off real stairs. None for "any" — today's requests, byte-
    # identical. Threaded into `leg_fn` below and into every `summarise_route`
    # call, so the route selection prices and the route the traveller
    # receives are ROUTED UNDER THE SAME COSTING — never a route chosen
    # step-free and reported over stairs.
    surface_override = ROUTE_SURFACE_COSTING_OVERRIDES[input.route_surface]
    # WHO THIS DAY IS FOR (redesign row 6.4; plan S2.6) — threaded into every
    # `poi_score` call below so a family day weights `children_can_run`
    # places up and a take-it-easy/couple day weights `sit_and_talk` up. None
    # (no party) = today's scoring, byte-identical.
    party = input.party
    # RAIN REACHES THE RANKING (deviation iv, amended at W3.3). Halving a wet
    # square's DWELL was supposed to shift selection toward covered places
    # "through the existing value model" — but dwell flows into the greedy as
    # COST, not worth, so cheaper wet squares packed DENSER: the first D3 rain
    # run swapped a covered church OUT for an open street. Worth reaches
    # selection only through score, and the planner already owns exactly one
    # per-place score knob — the M6 penalty dict — so rain rides it: every
    # uncovered place's score dims by the same RAIN_DWELL_FRACTION the dwell
    # already uses (one constant, one coveredness map, both imported from THE
    # pricer). None weather = empty overlay = byte-identical.
    if input.weather == "rain":
        rain_penalty = dict(score_penalty or {})
        for poi in snapshot.pois:
            if not place_is_covered(poi):
                rain_penalty[poi.id] = rain_penalty.get(poi.id, 1.0) * RAIN_DWELL_FRACTION
        score_penalty = rain_penalty
    # "SKIP THE QUEUES" (Phase 4 dial, W4.2): peak-queue-heavy places dim
    # through the same one per-place score knob the rain rides. Peak minutes
    # are the honest yardstick — the dial holds whatever hour the day lands
    # on, and a place that queues at peak is the risk being avoided. False =
    # empty overlay = byte-identical.
    if input.avoid_queues:
        queue_penalty = dict(score_penalty or {})
        for poi in snapshot.pois:
            if (
                poi.queue_class not in (None, "none")
                and poi.queue_minutes_peak >= AVOID_QUEUES_MIN_PEAK_MINUTES
            ):
                queue_penalty[poi.id] = queue_penalty.get(poi.id, 1.0) * AVOID_QUEUES_SCORE_FACTOR
        score_penalty = queue_penalty
    # A fixed destination changes the SHAPE of the route, not how far a visitor can
    # walk. The 2026-08-04 "certification reach model" that used to branch here has
    # been deleted: it sized both the reach circle and the greedy's walking cap from
    # `maximum_elapsed_seconds + tolerance`, the ceiling on walking PLUS narration.
    # There is no route shape for which a walking-plus-talking ceiling is the right
    # bound on walking alone. Both quantities now come from the walk budget, on
    # every shape. See reach_envelope_searched.
    certification_fixed_end = input.end is not None
    # M3: the §3 divisor — routed leg times when a client is given (memoized;
    # the greedy re-evaluates the same coordinate pairs many times), else the
    # pace-corrected haversine. ALWAYS a real callable (never None) since
    # plan S2.4: a slow party's multiplier has to reach every consumer of
    # this ONE divisor, and the three sites that used to read
    # ``leg_fn or default_leg_seconds`` would otherwise need the multiplier
    # threaded through them separately, forking the arithmetic in two places.
    # At `pace_multiplier == 1.0` the base function rides through unwrapped —
    # byte-identical to before this existed, in both the client and no-client
    # cases (``leg_fn or default_leg_seconds`` already always resolved to
    # ``default_leg_seconds`` when ``leg_fn`` was ``None``; assigning that
    # same function object directly changes nothing any caller can observe).
    _base_leg_fn = (
        _memoized_leg_fn(routing_client, costing_options_override=surface_override)
        if routing_client is not None
        else default_leg_seconds
    )
    if pace_multiplier == 1.0:
        leg_fn = _base_leg_fn
    else:

        def leg_fn(lat1: float, lng1: float, lat2: float, lng2: float) -> int:
            return round(_base_leg_fn(lat1, lng1, lat2, lng2) * pace_multiplier)

    # Phase 6 density gate. Start-circle RED refuses open/round-trip requests.
    # Fixed-end requests defer to the routed A→B corridor checks below.
    # THE one line that makes the tourability gate and the planner speak the same
    # currency. Without it density judges the pool against its own audio target
    # while selection fills a different one.
    assessment = assess_tourability(input, snapshot, planning_policy=planning_policy)
    if assessment.status == "RED" and input.end is None and replan is None:
        # A replanned remainder is not a fresh request: a thin spot late in a
        # planned day is where the person IS, and "carry on and re-time" from
        # there is the answer, never a refusal (W5.1 defect 6).
        raise TourabilityRefusedError(assessment)

    # Step 2.2a: fixed-destination feasibility. When a fixed end B exists, the
    # routed A→B leg alone must fit inside the walk budget — otherwise no
    # in-budget tour can reach B and the greedy would silently emit a route
    # that never gets there. Compute the routed leg time with the SAME divisor
    # the greedy uses (leg_fn when a routing client is given, else the
    # pace-corrected haversine) — NEVER a straight-line haversine, which would
    # admit across-the-river endpoints no bridge serves. Raise BEFORE the
    # greedy, exactly like RED density. (end is None for open/loop walks —
    # they never enter this branch, so the Step-2.0d invariance baseline is
    # untouched.)
    if input.end is not None:
        leg_cost_fn = leg_fn  # never None (see the derivation above)
        t_ab = leg_cost_fn(start_lat, start_lng, input.end[0], input.end[1])
        reachability_ceiling = (
            planning_budget.maximum_elapsed_seconds + TIMEBOX_MATERIALITY_TOLERANCE_SECONDS
        )
        # Under a ReplanContext the walk home that no longer fits the minutes
        # left is the honest answer with its overrun REPORTED (Rosemary at her
        # bench: 20.5 minutes to the Orsay with 15 left is the question's raw
        # material, not a 422 — W5.2 R2), so the refusal below is a fresh plan's.
        if t_ab > reachability_ceiling and replan is None:
            overshoot_s = t_ab - reachability_ceiling
            gap_minutes = math.ceil(overshoot_s / 60)
            suggested_duration = 1
            while (
                route_planning_budget(suggested_duration, planning_policy).maximum_elapsed_seconds
                + TIMEBOX_MATERIALITY_TOLERANCE_SECONDS
                < t_ab
            ):
                suggested_duration += 1
            alternatives = (
                # Drop B and loop from A at the requested duration.
                FeasibilityAlternative(kind="loop", duration_min=input.duration_min, drop_end=True),
                # Keep B but extend to the smallest duration whose active-time
                # ceiling covers the routed A→B leg.
                FeasibilityAlternative(
                    kind="extend",
                    duration_min=suggested_duration,
                    drop_end=False,
                ),
            )
            # Step 2.2b: when at least one anchor is reachable inside the walk
            # budget, also offer a 'closer_b' pointing at a nearer destination
            # B' — preferring an anchor in the A→B direction (±45° wedge),
            # falling back to the highest-scoring in-budget anchor otherwise.
            # Omitted entirely when no anchor fits the budget.
            closer_b = _closer_b_alternative(
                start_lat=start_lat,
                start_lng=start_lng,
                end_lat=input.end[0],
                end_lng=input.end[1],
                duration_min=input.duration_min,
                interest=interest,
                snapshot=snapshot,
                leg_cost_fn=leg_cost_fn,
                walk_budget=planning_budget.walk_budget_seconds,
                planning_policy=planning_policy,
            )
            if closer_b is not None:
                alternatives = (*alternatives, closer_b)
            raise TourabilityRefusedError(
                assessment,
                (
                    f"Destination unreachable in {input.duration_min} min: routed A→B "
                    f"leg {t_ab}s exceeds reachability ceiling "
                    f"{reachability_ceiling}s by {gap_minutes} min."
                ),
                gap_minutes=gap_minutes,
                alternatives=alternatives,
            )

    # Step 1: REACH (§2.1, M5) — Valhalla walking isochrone replaces the
    # analytic radius (a straight-line circle admits across-the-river POIs no
    # bridge serves). Falls back to the exact haversine envelope when the
    # isochrone is unavailable. Phase 6 also drops 0-active-beat POIs.
    # ONE definition of the searched circle, shared with the tour-build harness so
    # the harness cannot report a radius the planner never used. See
    # reach_envelope_searched above for why that mattered.
    radius_m, iso_minutes = reach_envelope_searched(input, planning_policy=planning_policy)
    reach_contains, reach_degraded = _reach_predicate(
        (start_lat, start_lng),
        radius_m,
        iso_minutes,
        routing_client,
        pace_multiplier=pace_multiplier,
    )
    # Step 2.3: corridor (time-ellipse) reach filter for fixed-destination
    # walks. When a fixed end B exists, an anchor only earns candidacy if the
    # routed detour through it — A→poi→B — still fits inside the walk budget:
    # ``t(A, poi) + t(poi, B) <= walk_budget_seconds(duration_min)``. This is
    # the two-focus (A, B) ellipse whose string length is the walk budget,
    # measured with the SAME divisor the greedy uses (``leg_fn``, never None
    # — see its derivation) — NEVER straight-line haversine, which would admit
    # across-the-river anchors no bridge serves. ``end is None`` for open/loop
    # walks: ``corridor_admits`` is None and the gate is skipped entirely, so
    # the candidate pool on that path is LITERALLY unchanged (Step-2.0d
    # identity baseline holds).
    corridor_admits = None
    if input.end is not None:
        corridor_leg_fn = leg_fn
        end_lat, end_lng = input.end
        corridor_budget = planning_budget.walk_budget_seconds

        def corridor_admits(lat: float, lng: float) -> bool:
            t_a_poi = corridor_leg_fn(start_lat, start_lng, lat, lng)
            t_poi_b = corridor_leg_fn(lat, lng, end_lat, end_lng)
            return t_a_poi + t_poi_b <= corridor_budget

    # Phase 3 re-baseline (Step 3.5): THE MODEL SWITCH. The hard tier gate
    # (poi.tier not in ANCHOR_TIERS), the walk_by_only exclusion, and the
    # lens-miss exclusion are GONE. Every corridor POI with active beats is now
    # ELIGIBLE; the continuous spotlight score + band decide
    # dwell/vignette/silent. This produces the s3 two-track output:
    #   - silent  : excluded for this user. The ONLY silence is low-gravity AND
    #               off-genre -- per band_for_spotlight that fires solely when a
    #               POI is BOTH below the lens floor (a thematic miss) AND
    #               low-gravity (tier < BAND_LANDMARK_TIER). Lens alone, gravity
    #               alone, or a detour alone never silences a landmark.
    #   - vignette: eligible but not a dwell stop (a tier-low or off-genre POI,
    #               or any walk_by_only POI -- role multiplier 0.0 zeroes its
    #               poi_score so it can never out-rank a real stop). Surfaced as
    #               an on-path one-liner by a downstream step (RouteOptionStop
    #               band="vignette"), NOT inserted into the dwell route here.
    #   - dwell   : a real stop (headline/full/short) -- the greedy/endpoint-
    #               pull/fill draw the ordered route from these.
    # The band is measured on-path (marginal_detour_seconds=0): a detour is an
    # ordering cost, not an eligibility test, and band_for_spotlight already
    # floors any landmark (tier >= BAND_LANDMARK_TIER) at vignette regardless
    # of detour. ``candidates`` below is the DWELL pool the greedy consumes;
    # vignette/silent POIs are deliberately kept out of it so dwell-minute
    # allocation is unchanged in spirit -- gate removal WIDENS which POIs can
    # earn a dwell stop (every tier, every lens), it does not make the route
    # stop everywhere.
    # THE PLANNER'S CLOCK (redesign 6.1). Parsed once; the contract validator
    # has already guaranteed the ISO form. None = dateless request = no
    # filtering anywhere below — the identity default that keeps every
    # existing tour and golden byte-identical.
    clock_start = (
        datetime.fromisoformat(input.start_datetime) if input.start_datetime is not None else None
    )
    clock_exclusions: list[ClockExclusion] = []
    # POIs closed for the whole window that keep an exterior worth standing at
    # (plan S3.5, W1.9 dissent 1): they stay in the pool and price OUTSIDE-ONLY
    # through `price_visit` below. Populated by the candidate loop.
    closed_today_ids: set[str] = set()

    # HOW LONG THIS VISITOR SPENDS AT A PLACE — one closure, three clock reads
    # (plan S3.3/S3.5; deviation v, estimate-then-exact, the walk clock's own
    # shape): the greedy budgets every stop at the REQUEST's start hour (the
    # estimate, memoized in `_visit`), while the repair's trials and the final
    # Route re-price each stop at its ordered ARRIVAL hour (the exact form,
    # via `_arrival_priced_visits`) — same closure, same rules, different
    # clock reads, so a queue that peaks at noon prices differently at 09:00
    # and 14:00 without a second pricing spelling anywhere. At the identity
    # defaults (dateless, no weather flag, no closures, unpassed queue corpus,
    # end hardness not "wall") this is byte-identical to the bare
    # ``visit_seconds`` it replaced — the delegation is proven in
    # tests/test_one_promise_pricing.py.
    party_ceiling_seconds = (
        input.max_stop_minutes * 60 if input.max_stop_minutes is not None else None
    )
    wall_hardness = input.end_hardness == "wall"
    # THE ESTIMATE HOUR (deviation v, amended at W3.2): the greedy prices
    # queues at the MIDDLE of the requested window, not its first minute. A
    # 10:00 start put every estimate in the off-peak band while the real
    # arrivals (11:00 onward) priced peak, and that gap summed across a day
    # exceeded what a single repair drop can shed — measured at W3.2, four of
    # seven baseline cells refused with the best route ~45 min over its
    # ceiling. The window's midpoint is the unbiased one-hour stand-in for
    # "when this day stands at places"; the repair's trials and the final
    # route still re-price every stop at its exact ordered arrival hour.
    request_hour = (
        (clock_start + timedelta(minutes=input.duration_min // 2)).hour
        if clock_start is not None
        else None
    )

    # MINUTES A DROP HANDED THIS PLACE (Phase 5 S5.5, W4.2 locked semantics 4):
    # `drop_stop` grants a survivor extra visit seconds within its shape ceiling
    # and this dict carries them into THE one pricing closure below, so the
    # repair's trials, the served arrivals and the promise shapes all read the
    # longer visit through the same door. Empty = no drop granted anything = today.
    visit_extension_seconds: dict[str, int] = dict(
        replan.visit_extension_seconds if replan is not None else {}
    )

    # STOPS THE DAY HAS NO ROOM TO GO INSIDE (the timebox repair's fourth move,
    # "step outside"). THE MIRROR of `visit_extension_seconds` above, and
    # deliberately built the same way: a per-POI channel, written by one pass and
    # read by THE one pricing closure below, so the repair's trials, the served
    # arrivals and the promise shapes all price the same visit. That channel
    # LENGTHENS a stop; this one prices it exterior-only — through `visit_shape`'s
    # OWN collapse, never a second rule here. Empty = every interior fits = today,
    # byte-identical.
    exterior_only_ids: set[str] = set()

    def _arrival_door_reason(
        cand: POI, clock_hour: int | None, arrival_clock: datetime | None
    ) -> str | None:
        """THE whole-window closure rule aimed at ONE stop's own clock — the
        arrival-window door check. None = the door is open (or unknowable) when
        the walk arrives. The pool rule above judges a door against the whole
        tour window, so on a long day a place open for any slice of it counts
        open even when the walk reaches it hours outside that slice; this asks
        the only question the visitor standing there cares about. Consulted only
        on a decided order (an arrival clock exists only there — the estimate
        half stays at the request hour, deviation v), only for a door the pool
        rule has not already voided, and only for a visit that would otherwise
        go inside. The window it tests is the stop's own uncollapsed stand, so
        a door that opens or closes mid-visit stays open — the same
        any-part-open trade the pool rule makes.
        """
        if arrival_clock is None or cand.opening_hours is None:
            return None
        if cand.id in closed_today_ids:
            return None  # the pool rule already voided this door
        open_shape = visit_shape(
            cand,
            interest,
            snapshot,
            party_ceiling_seconds=party_ceiling_seconds,
            clock_hour=clock_hour,
            weather=input.weather,
            wall=wall_hardness,
            exterior_only=cand.id in exterior_only_ids,
        )
        if not open_shape.goes_inside:
            return None  # nothing behind the door to void
        window_min = max(1, math.ceil(shape_total_seconds(open_shape) / 60))
        return _clock_exclusion_reason(
            cand.opening_hours, cand.opening_hours_source, arrival_clock, window_min
        )

    def shape_visit(
        cand: POI, clock_hour: int | None, arrival_clock: datetime | None = None
    ) -> PromiseShape:
        shape = visit_shape(
            cand,
            interest,
            snapshot,
            party_ceiling_seconds=party_ceiling_seconds,
            clock_hour=clock_hour,
            # A door is voided by the whole-window pool rule OR by this stop's
            # own arrival window — one collapse, two clocks that can prove it.
            closed_today=(
                cand.id in closed_today_ids
                or _arrival_door_reason(cand, clock_hour, arrival_clock) is not None
            ),
            weather=input.weather,
            wall=wall_hardness,
            exterior_only=cand.id in exterior_only_ids,
        )
        extra = visit_extension_seconds.get(cand.id, 0)
        if extra <= 0:
            return shape
        # The gift lands where the visitor stands: inside if the visit goes
        # inside (more of the museum), else on the pavement.
        if shape.goes_inside:
            return shape.model_copy(update={"inside_seconds": shape.inside_seconds + extra})
        return shape.model_copy(update={"outside_seconds": shape.outside_seconds + extra})

    def price_visit(
        cand: POI, clock_hour: int | None, arrival_clock: datetime | None = None
    ) -> int:
        return shape_total_seconds(shape_visit(cand, clock_hour, arrival_clock))

    def ceiling_visit(cand: POI) -> int:
        return visit_ceiling_seconds(cand, party_ceiling_seconds=party_ceiling_seconds)

    _visit_memo: dict[str, int] = {}

    def _visit(cand: POI) -> int:
        cached = _visit_memo.get(cand.id)
        if cached is None:
            cached = price_visit(cand, request_hour)
            _visit_memo[cand.id] = cached
        return cached

    reachable_count = 0
    candidates: list[POI] = []
    # Fixed-end POIs that fail ONLY the ordinary walking-allocation ellipse.
    # They stay out of greedy/ordinary fill, but the elapsed-time rescue may
    # consider them using the exact A→selected→candidate→B routed total.
    corridor_rescue_candidates: list[POI] = []
    filler_stubs: list[tuple[int, POI]] = []  # (audio, poi) for the never-empty guard
    for poi in snapshot.pois:
        in_reach = reach_contains(poi.lat, poi.lng)
        if in_reach:
            reachable_count += 1
        if not in_reach:
            continue
        if not _has_active_beats(poi, snapshot):
            continue  # Phase 6: zero-beat POIs carry no audio -> nothing to say.
        on_path_band = band_for_spotlight(
            spotlight(poi, lenses=interest or None, snapshot=snapshot),
            tier=poi.tier,
        )
        # A LANDMARK (tier >= BAND_LANDMARK_TIER) that a LENS dimmed to vignette
        # stays a DWELL stop — a landmark thin FOR your interest DISCLOSES (via the
        # density/tourability gate), it never vanishes. Without this floor, a
        # lensed thin-area start (e.g. The Sorbonne under dark_history, spotlight
        # 4.0 -> 1.0) collapses every reachable POI to vignette and empties the
        # whole tour, silently dropping a major landmark. band_for_spotlight never
        # takes a landmark below vignette, so this catches exactly the dimmed case.
        lens_dimmed_landmark = poi.tier >= BAND_LANDMARK_TIER and on_path_band == BAND_VIGNETTE
        if not is_dwell_band(on_path_band) and not lens_dimmed_landmark and not poi.requires_dwell:
            # silent -> excluded entirely; a NON-landmark vignette -> a walk-by, not
            # a dwell stop. Either way it does not enter the greedy's dwell pool.
            continue
        if POI_ROLE_MULTIPLIER.get(poi.poi_role, 0.0) <= 0.0:
            # walk_by_only (and any zero-weight role): an on-path vignette, never
            # a dwell anchor. Its poi_score is 0, so admitting it would let the
            # greedy insert a content-less stop (0 > -inf) -- keep it out of the
            # dwell pool explicitly.
            continue
        if is_container_identity_poi(poi):
            # A POI that IS one of its own areas is not a destination. It was already
            # rejected downstream by choose_discrete_route -- but only AFTER routing,
            # and that guard kills the WHOLE route, so a single such POI in every
            # bounded candidate refused the tour outright with
            # "bounded candidates contain no route with all-discrete stops".
            #
            # That is a genuine contradiction between two live rules:
            # ELIGIBLE_POI_ROLES (density.py) admits poi_role='setting' as stoppable,
            # while choose_discrete_route rejects a setting whose name matches its
            # area. MEASURED 2026-07-26: `Les Halles` is the only Paris POI that is
            # both, and it alone made the 90-min Ile de la Cite preview return 422.
            # Excluding it from the DWELL POOL resolves the contradiction at the
            # right layer -- it stays available as a walk-past vignette, which is
            # what a neighbourhood actually is.
            continue
        if replan is not None and (
            poi.id in replan.visited_poi_ids
            or (poi.place_category and poi.place_category in replan.spent_categories)
            or (
                replan.keep_to_poi_ids is not None
                and poi.id not in replan.keep_to_poi_ids
                and poi.id not in replan.protected_poi_ids
            )
        ):
            # A REPLAN'S POOL (Phase 5 S5.6): a place already stood at or skipped
            # is never re-seated; the category walked away from is spent for the
            # day (Greta, W5.2 R1.2); and a keep-to list confines the answer to
            # a subset of the planned day — no new building, no new narration
            # (Fiona & Dev, Nadia, Julien, Paulo, Greta — R1.2/R1.4).
            continue
        if (
            lens_gate
            and interest
            and _lens_relation(poi, interest, snapshot) == "miss"
        ):
            # THE SUBJECT GATE (Phase 9 S3): on a lensed request, a story
            # candidate with NOTHING in the asked family — the same
            # direct/one-hop/miss the spotlight classifies by — leaves the
            # dwell pool, so a place that can answer swaps in. An off-subject
            # stop is dropped or replaced, never served mislabelled; fewer
            # honest stops beat a padded day. The gate decides only the OPEN
            # pool: a pin and a replan's protected promises never ride it —
            # the force-seat pass below seats every held stop straight from
            # the snapshot, so the person's own hand on the day outranks this
            # exclusion by construction. `select_route`'s valve lifts the gate
            # — saying so — rather than refuse a day a thin area used to
            # serve. Walking past stays fine: vignettes are untouched.
            continue
        if poi.place_category and poi.place_category in input.category_minus:
            # "LESS OF THIS TODAY" (Phase 4 dial, W4.2 — Greta's "not what I did
            # yesterday", Julien's "zero museums today"): an excluded KIND leaves
            # the dwell pool entirely, so a different kind swaps in — the lens
            # behaviour, never the shaved-stop behaviour. Recorded at the WIRE
            # from the request itself (one note per excluded kind, not one row
            # per museum in Paris) — the escape-radius disclosure precedent.
            # Walking past one is still fine: vignettes are untouched.
            continue
        if (
            input.avoid_queues
            and poi.queue_class not in (None, "none")
            and poi.queue_minutes_peak >= AVOID_QUEUES_EXCLUDE_PEAK_MINUTES
        ):
            # "SKIP THE QUEUES", the exclusion tier (see the constants above):
            # a serious peak queue leaves the dwell pool under the dial — a
            # score dim provably loses to proximity, and the person who turned
            # this dial is refusing the stand, not asking for it to be
            # slightly less likely. Disclosed at the wire from the request.
            continue
        if clock_start is not None and poi.opening_hours is not None:
            # THE CLOCK RULE (redesign 6.1; re-ruled by the Phase 1 panel, W1.9
            # dissent 1 — plan S3.5): on a dated request, a place whose opening
            # table is CLOSED for the entire visit window is DEMOTED, not
            # deleted — it stays in the dwell pool as an OUTSIDE-ONLY stop
            # (interior and queue zeroed by `price_visit`, exterior minutes
            # kept) and the day says so. Four personas ruled a locked door is
            # still a facade worth standing at; the D1 demo's own Monday run
            # STARTS at a Monday-closed Orsay. The ONE honest removal that
            # remains: a closed place with no outside value
            # (typical_duration_min == 0) — nothing to stand and see — leaves
            # the pool, recorded, exactly as before. That is the only closure
            # keying allowed: never tier, never score.
            # No datetime, or no table (None = not gated / pass not run), means
            # no filtering — the safe direction and the identity default.
            clock_reason = _clock_exclusion_reason(
                poi.opening_hours,
                poi.opening_hours_source,
                clock_start,
                input.duration_min,
            )
            if clock_reason is not None:
                # The record carries the closure FACT and the pool DECISION, not the
                # traveller's sentence. What a person is told depends on whether the
                # place ends up ON the route — a closed facade the greedy never picks
                # is "not in your day", not "we will see it from the outside" — and
                # only the reader holding the finished route knows that (W4.12:
                # "Lapin Agile — closed all day Wednesday — we will see it from the
                # outside" printed on a Tuileries→Notre-Dame day; Lapin Agile is in
                # Montmartre). "seated outside only" was already ruled out by the
                # W4.2 panel: "seated" is the engine's word, and on a MARKET it read
                # as tables outside a shut market.
                kept_outside = poi.typical_duration_min > 0
                if kept_outside:
                    closed_today_ids.add(poi.id)
                clock_exclusions.append(
                    ClockExclusion(
                        poi_id=poi.id,
                        name=poi.name,
                        reason=clock_reason,
                        kept_outside=kept_outside,
                        all_day=_closed_all_day(poi.opening_hours, clock_start),
                    )
                )
                if not kept_outside:
                    continue
        if (
            input.escape_radius_m is not None
            and haversine_m(start_lat, start_lng, poi.lat, poi.lng) > input.escape_radius_m
        ):
            # THE ESCAPE RADIUS (redesign §2.4; plan S2.8) — a constraint on
            # DISTANCE FROM THE START, not on any one leg: "a meltdown 25
            # minutes from the exit means carrying a child for 25 minutes"
            # (design §2.4; panel locked cost D4, 03-panel-findings.md).
            # Dwell pool only, same layer and same identity default as the
            # clock filter above — walking past a far place is still fine.
            # Recorded NOWHERE, unlike a closed museum: a closed door needs
            # explaining, a too-far bench does not, and cluttering
            # `clock_exclusions` with radius ejections would bury the day's
            # real disclosures (plan S2.8's own sabotage warning).
            continue
        if (
            not poi.requires_dwell and _is_filler_stub(poi, snapshot, interest or None)
            # A PLACE STILL HAS TO HAVE SOMETHING TO SAY.
            #
            # This briefly also exempted anything WORTH VISITING regardless of what
            # it says, so a chocolate museum — low tier, thin beats, ninety minutes
            # of interior — could be a stop. That is a real shape and the release
            # is built for it, but measured on the live corpus the exemption cost
            # more than it bought, twice:
            #
            #   * The 60-minute Place des Vosges round trip walked twenty minutes
            #     to Marche Bastille — thirty minutes of visit, NOTHING said —
            #     instead of standing in a square the corpus has twenty-one things
            #     to say about.
            #   * The 300-minute Rue Royale walk took a church 1,146 m off the
            #     line behind a fifty-one-minute unbroken march, and traded
            #     twenty-two minutes of standing for twenty-three of walking.
            #
            # The cause is that the greedy ranks by score over insertion cost, so a
            # cheap-to-reach silent place outranks a rich one the moment silence
            # stops disqualifying it. Seating a place that says nothing is not what
            # "a stop is worth what the visitor spends there" was ever meant to
            # license: Camille's silence at Concorde is silence AT a place the tour
            # told her about.
            #
            # Re-opening this needs the greedy to value what a stop CONTRIBUTES,
            # not just what it costs to reach — which is a change to the objective,
            # not to this filter. Recorded rather than deleted so the next attempt
            # starts from the measurement instead of the idea.
        ):
            # Too thin for a dedicated stop — keep it OUT of the greedy's dwell
            # pool so it surfaces as a walk-by vignette (select_vignettes applies
            # the SAME predicate). Held for the never-empty guard below.
            filler_stubs.append(
                (planned_capped_audio_seconds(poi, snapshot, interest or None, None), poi)
            )
            continue
        if corridor_admits is not None and not corridor_admits(poi.lat, poi.lng):
            # Outside the ordinary walk-allocation ellipse is no longer silent
            # deletion.  The candidate may be useful when the tour is underfilled,
            # but only the elapsed rescue can admit it after pricing the full pinned-B
            # route plus emitted audio against the existing total-time ceiling.
            corridor_rescue_candidates.append(poi)
            continue
        candidates.append(poi)

    certification_candidates = sorted(
        {poi.id: poi for poi in [*candidates, *corridor_rescue_candidates]}.values(),
        key=lambda poi: poi.id,
    )

    # Never empty the tour: if EVERY dwell-eligible POI was a thin filler-stub,
    # keep the richest one as a real stop (a one-stop tour beats a zero-stop tour;
    # the density gate already surfaces thinness). Landmarks are never filler, so
    # this only fires in genuinely thin low-tier areas.
    if not candidates and filler_stubs:
        candidates.append(max(filler_stubs, key=lambda pair: pair[0])[1])

    reach = ReachVerdict(
        mode=(
            "standard"
            if assessment.status == "GREEN"
            else "redirect"
            if assessment.one_way_alternative_destination
            else "ambient"
        ),
        degraded=reach_degraded,
        walk_minutes=iso_minutes,
        reachable_poi_count=reachable_count,
        alternative_destination=assessment.one_way_alternative_destination,
    )

    if (
        not candidates
        and not (certification_fixed_end and certification_candidates)
        and replan is None  # a remainder with nothing seatable is one leg home (S5.6)
    ):
        # REACH (the authoritative walkable-POI check) found nothing seatable.
        # The haversine density pre-check can be optimistic at an edge/waterfront
        # start (e.g. on the Brooklyn Bridge: POIs across the East River are
        # inside the straight-line envelope but NOT inside the walking
        # isochrone). Returning a silent 0-stop Route here shipped an empty
        # "tour" for ANY such start in ANY city. Refuse cleanly with the
        # assessment's alternatives, exactly like the density-RED path.
        raise TourabilityRefusedError(
            assessment,
            f"No POIs are reachable on foot within a {input.duration_min}-min walk "
            f"of this start — try a longer duration or a start nearer the density.",
        )

    # Step 2: spine.
    spine = pick_spine_area(start_lat, start_lng, candidates, snapshot)

    # Step 3: greedy with insertion cost.
    walk_budget = planning_budget.walk_budget_seconds
    # What the tour aims to spend NOT walking. The greedy fills this by seating
    # stops, and a stop costs what the visitor SPENDS there — the longer of the
    # visit and the narration — not what it says. Spending narration seconds
    # against this budget is what made a 120-minute request serve 157 minutes
    # while reporting 120, because the tourist stood at places the planner had
    # priced at the length of their audio.
    dwell_budget = planning_budget.dwell_target_seconds

    # The greedy spends WALKING seconds and nothing else, so its cap is the
    # WALKING allocation. It used to be ``certification_total_ceiling`` on the
    # fixed-destination arm, which is the ceiling on total ACTIVE time — walking
    # PLUS narration. At 90 minutes that handed the greedy 6000 walking seconds
    # against a 2160-second allocation, so it could spend the whole tour's
    # elapsed budget on walking before one second of narration was counted and
    # every A→B route breached the band by construction. A cap on walking must
    # be a walk budget for EVERY caller; there is no route shape for which the
    # elapsed ceiling is the right bound on this quantity.
    #
    # The endpoint-pull reserve is subtracted only on the one shape that runs
    # that post-step (open one-way — see Step 4 below, gated on
    # ``input.end is None and not input.round_trip``). A round trip and a fixed
    # destination never run it, so both get the whole allocation.
    # A KEEP-CONSTRAINED REPLAN TAIL CANNOT PULL (Phase 6 W6.11, measured on the
    # F&D day): with `keep_to_poi_ids` set, every non-keep candidate is filtered
    # before the pull ever ranks a far anchor — so the reserve starved skip
    # tails for nothing, and the set shipped entries whose screen text promised
    # the next stop over an empty stop_ids. The pull-less shapes get the whole
    # allocation.
    if (
        certification_fixed_end
        or input.round_trip
        or (replan is not None and replan.keep_to_poi_ids is not None)
    ):
        greedy_walk_budget = walk_budget
    else:
        greedy_walk_budget = int(walk_budget * (1.0 - ENDPOINT_PULL_RESERVED_BUDGET_FRACTION))

    selected: list[POI] = []
    # A fixed destination is part of the route from the first planning decision,
    # even when no corpus POI represents it.  Greedy insertion deltas therefore
    # spend the remaining A→B allocation rather than starting from a fictional
    # zero-length open walk.
    consumed_walk = (
        leg_fn(start_lat, start_lng, input.end[0], input.end[1]) if input.end is not None else 0
    )
    consumed_dwell = 0

    # VISITOR PINS (plan S3.6; design §3.2 — Théo pins one thing absolutely;
    # Julien pins nothing): a pin is a CERTAINTY, not a preference. Pinned
    # stops leave the greedy's own pool here and are force-seated below, once
    # the seating helpers exist.
    pinned_ids: frozenset[str] = frozenset(input.pinned_poi_ids)
    # THE HELD SET (Phase 5 S5.6): a replan's protected stops are seated and
    # protected exactly as pins are — force-seated, never dropped by the repair
    # or the concentrate pass, never folded — but they keep their OWN promise
    # kind (a base day's anchor held through the "More breaks" dial stays an
    # anchor; it is not relabelled a pin, so a session never asks a question
    # about a planner's anchor — W5.2 R1.5).
    held_ids: frozenset[str] = pinned_ids | frozenset(
        replan.protected_poi_ids if replan is not None else ()
    )
    # THE LEARNED LISTENING RATE (design §4.1, Paulo; Phase 5 S5.10): a replan
    # prices every trial and the final gate with what the person actually
    # spends listening; 1.0 (the identity) on every fresh plan.
    listening_rate: float = replan.listening_rate if replan is not None else 1.0
    remaining = [
        candidate
        for candidate in (
            certification_candidates if certification_fixed_end and not candidates else candidates
        )
        if candidate.id not in held_ids
    ]

    # C9 governor (SCOPED to round-trip / open-walk, end is None; A->B keeps the
    # Phase-2 currency + corridor discipline — see _capped_audio): floor-less
    # capped-audio currency. The first-seated POI is the START-ANCHOR
    # (positional, decision 3) and is exempt (allowance None => full accounting);
    # every other stop is capped to the per-stop allowance. The audio break is
    # SUPPRESSED until the route reaches the min(3, d//10) stop floor, so a
    # beat-rich anchor can never collapse the tour to one stop (the abandoned
    # >=2 hard-ceiling refutation).
    # STILL AN AUDIO CAP, deliberately: it bounds how long one ordinary stop may
    # TALK, which is a fairness rule about narration share and has nothing to do
    # with how long the visitor stands there. Only its base moved, and the number
    # is unchanged at every duration (routing.governor_allowance_seconds says the
    # same thing at the same arithmetic).
    allowance = dwell_budget // max(1, min(3, input.duration_min // 10))
    count_floor = min(3, input.duration_min // 10)
    exempt_anchor_id: str | None = None
    _capped_memo: dict[tuple[str, bool], int] = {}

    def _capped_audio(cand: POI, *, exempt: bool) -> int:
        key = (cand.id, exempt)
        cached = _capped_memo.get(key)
        if cached is None:
            # Fixed-end A→B emission skips the relative domination governor but
            # still applies the absolute per-stop ceiling.  Selection must count
            # that same capped beat plan: tier dwell proxies can over-credit a
            # thin landmark (for example 300 planned seconds for 125 seconds of
            # material), making both the greedy and fill pass stop while the
            # shipped tour remains below its audio floor.
            if input.end is not None:
                stop_allowance = MAX_DWELL_AUDIO_SECONDS
            else:
                stop_allowance = None if exempt else allowance
            # An EXEMPT anchor is exempt from the GOVERNOR allowance, not from the
            # absolute MAX_DWELL_AUDIO_SECONDS ceiling — build_poi_beat_plans_capped
            # applies that ceiling to every stop including the marquee. Crediting the
            # greedy with UNCAPPED audio made the planner believe a marquee filled far
            # more of the audio target than the tour actually delivers, so it stopped
            # adding stops early — which is why tightening the ceiling alone SHORTENS
            # a tour instead of spreading it over more stops.
            cached = planned_capped_audio_seconds(cand, snapshot, interest, stop_allowance)
            _capped_memo[key] = cached
        return cached

    def _stop_cost(cand: POI, *, exempt: bool) -> int:
        """What seating this candidate costs the STANDING-STILL budget.

        One combining rule, imported rather than restated — see
        ``visit_time.stop_seconds``. The audio half is the greedy's own capped
        number, because no Route exists during insertion; the gate and generation
        supply their own. The COMBINING is what must not fork.
        """
        return stop_seconds(_visit(cand), _capped_audio(cand, exempt=exempt))

    # THE PER-LEG CAP (redesign §2.4; plan S2.3): with the axis set, the banded
    # longest-leg RANK's soft preference becomes a HARD admission rule at every
    # mechanism that can add a stop — here, the fill pass, the endpoint pull,
    # and the timebox repair's trials. None = axis unset = today, byte-identical.
    max_leg_seconds = input.max_leg_minutes * 60 if input.max_leg_minutes is not None else None
    if replan is not None and replan.longest_leg_ceiling_seconds is not None:
        # §4.5.3 — a replan may not mint a leg longer than the base day's longest.
        max_leg_seconds = (
            replan.longest_leg_ceiling_seconds
            if max_leg_seconds is None
            else min(max_leg_seconds, replan.longest_leg_ceiling_seconds)
        )
    greedy_close: tuple[float, float] | None
    if input.end is not None:
        greedy_close = input.end
    elif input.round_trip:
        greedy_close = (start_lat, start_lng)
    else:
        greedy_close = None

    # FORCE-SEAT THE PINS (plan S3.6) before the greedy spends a second of
    # budget. What cannot be honoured is REFUSED by name — a pin silently
    # missing from the day is the one outcome worse than a refusal: the
    # visitor built their day around it. Checked here: the pin exists in this
    # corpus, is reachable on foot, is not closed-with-nothing-to-stand-
    # outside-for (a closed pin WITH an exterior was already demoted to an
    # outside-only stop by the clock rule and seats normally), and fits the
    # walking budget. A beatless or off-band pin still seats — a silent stop
    # at the visitor's own chosen place is honest; an overridden pin is not.
    if held_ids:
        poi_by_id = {poi.id: poi for poi in snapshot.pois}
        held_in_order = list(
            dict.fromkeys(
                list(input.pinned_poi_ids)
                + list(replan.protected_poi_ids if replan is not None else ())
            )
        )
        for pin_id in held_in_order:
            pin = poi_by_id.get(pin_id)
            if pin is None:
                if replan is not None and pin_id not in pinned_ids:
                    continue  # a held stop the corpus no longer carries: nothing to seat
                raise TourabilityRefusedError(
                    assessment,
                    f"Pinned place {pin_id!r} is not in this city's corpus.",
                )
            if any(seated.id == pin.id for seated in selected):
                continue  # the same place pinned twice
            if not reach_contains(pin.lat, pin.lng) and pin_id in pinned_ids:
                raise TourabilityRefusedError(
                    assessment,
                    f"Pinned place '{pin.name}' is not reachable on foot within a "
                    f"{input.duration_min}-min walk of this start.",
                )
            if (
                clock_start is not None
                and pin.opening_hours is not None
                and pin.id not in closed_today_ids
                and _clock_exclusion_reason(
                    pin.opening_hours,
                    pin.opening_hours_source,
                    clock_start,
                    input.duration_min,
                )
                is not None
            ):
                raise TourabilityRefusedError(
                    assessment,
                    f"Pinned place '{pin.name}' is closed for this entire visit "
                    "window and has nothing to stand outside for.",
                )
            if input.end is not None:
                extra, idx = _insertion_cost_with_fixed_end(
                    pin,
                    selected,
                    start_lat=start_lat,
                    start_lng=start_lng,
                    fixed_end=input.end,
                    leg_seconds_fn=leg_fn,
                )
            else:
                extra, idx = insertion_cost_seconds(
                    pin,
                    selected,
                    start_lat=start_lat,
                    start_lng=start_lng,
                    round_trip=input.round_trip,
                    leg_seconds_fn=leg_fn,
                )
            if consumed_walk + extra > walk_budget and (replan is None or pin_id in pinned_ids):
                raise TourabilityRefusedError(
                    assessment,
                    f"Pinned place '{pin.name}' cannot be reached inside this "
                    f"request's walking budget — extend the duration or drop "
                    "the pin.",
                )
            # (A replan's HELD stop that no longer fits still seats — the overrun
            # is reported on the route, the question's raw material, never a 422.)
            selected.insert(idx, pin)
            consumed_walk += extra
            consumed_dwell += _stop_cost(pin, exempt=False)

    while remaining:
        best_candidate: POI | None = None
        best_extra: int = 0
        best_idx: int = 0
        best_value: float = -math.inf

        for cand in remaining:
            if input.end is not None:
                extra, idx = _insertion_cost_with_fixed_end(
                    cand,
                    selected,
                    start_lat=start_lat,
                    start_lng=start_lng,
                    fixed_end=input.end,
                    leg_seconds_fn=leg_fn,
                )
            else:
                extra, idx = insertion_cost_seconds(
                    cand,
                    selected,
                    start_lat=start_lat,
                    start_lng=start_lng,
                    round_trip=input.round_trip,
                    leg_seconds_fn=leg_fn,
                )
            if consumed_walk + extra > greedy_walk_budget:
                continue
            # DON'T SEAT A STOP THE TOUR HAS NO ROOM FOR.
            #
            # The break below fires AFTER an add, so the greedy always overshot by
            # up to one stop. That was harmless while a stop cost what it SAID —
            # narration is capped at 270 s, so the overshoot was minutes. It is not
            # harmless now that a stop costs what the visitor SPENDS there: Place
            # des Vosges alone absorbs 35 minutes, so one add past the line blew a
            # 60-minute round trip out to 68 and the hard ceiling then refused the
            # whole tour. A refusal is what the traveller sees, on the most
            # ordinary request there is.
            #
            # The `selected` guard is what keeps a one-stop tour possible: a place
            # bigger than the entire request still gets seated when nothing else
            # has been, because a short tour of one big place beats no tour.
            if (
                selected
                and consumed_dwell + _stop_cost(cand, exempt=cand.id == exempt_anchor_id)
                > dwell_budget
            ):
                continue
            if not _insertion_legs_fit_cap(
                cand,
                selected,
                idx,
                start=(start_lat, start_lng),
                close=greedy_close,
                max_leg_seconds=max_leg_seconds,
                leg_seconds_fn=leg_fn,
            ):
                continue
            base = poi_score(cand, spine, interest, snapshot, penalty=score_penalty, party=party)
            # +1s smoothing prevents division-by-zero on co-located POIs.
            # Phase 7.5: clamp the denominator floor at 1.0 — integer
            # rounding inside ``insertion_cost_seconds`` can yield extra=-1
            # when a candidate sits between two waypoints, which the bare
            # ``extra + 1.0`` would resolve to a divide-by-zero.
            value = base / max(1.0, extra + 1.0)
            # Exact-value ties break on id (matching every other selection
            # path) so the pick never depends on candidate iteration order.
            if value > best_value or (
                value == best_value and best_candidate is not None and cand.id < best_candidate.id
            ):
                best_value = value
                best_candidate = cand
                best_extra = extra
                best_idx = idx

        if best_candidate is None:
            break

        # Insert at the best position.
        selected.insert(best_idx, best_candidate)
        consumed_walk += best_extra
        # Start-anchor exemption applies ONLY to round-trip / open-walk (end is
        # None): there the first-seated POI is genuinely the start-anchor and may
        # dominate (decision 3). For A->B the first-seated is an INCIDENTAL
        # corridor POI — exempting it would starve the real destination B (which
        # is materialized post-greedy and exempt at EMISSION), so A->B caps every
        # corridor stop.
        if input.end is None and exempt_anchor_id is None:
            exempt_anchor_id = best_candidate.id
        consumed_dwell += _stop_cost(best_candidate, exempt=best_candidate.id == exempt_anchor_id)
        remaining.remove(best_candidate)

        if consumed_dwell >= dwell_budget and (
            input.end is not None or len(selected) >= count_floor
        ):
            break

    # Step 4: endpoint-pull (one-way only). Force-include a far-envelope POI
    # as the closing anchor so traverses (e.g. Pont Neuf → Île east tip)
    # don't truncate near the start. Re-orders the route end-to-end via
    # insertion-cost optimisation; respects the walk budget.
    pulled_endpoint_id: str | None = None
    # THE AFTER-DARK FINISH (plan S3.7; design §4.3's dusk trigger; Sofia's
    # swap rule, 11-solo-after-dark; row 6.4's good_after_dark finally
    # consumed): on a DATED run whose nominal finish lands after civil dusk,
    # the far-endpoint ranking below PREFERS a finisher that is good after
    # dark — the user is never asked (Sofia's never-ask rule), an UNDATED run
    # never enters (no clock = no dusk = today's ranking, byte-identical),
    # and an unknown region prices no verdict (civil_dusk_local fails open).
    # The projection is the request's own nominal length — what the plan aims
    # to spend is known before any stop is chosen.
    finish_after_dusk = False
    if clock_start is not None:
        dusk = civil_dusk_local(clock_start.date(), start_lat, start_lng)
        finish_after_dusk = dusk is not None and (
            clock_start + timedelta(seconds=planning_budget.nominal_elapsed_seconds) > dusk
        )
    if input.end is None and not input.round_trip and selected:
        far_radius_min = radius_m * ENDPOINT_PULL_FAR_FRACTION
        already = {p.id for p in selected}
        far_candidates = [
            c
            for c in candidates
            if c.id not in already
            and haversine_m(start_lat, start_lng, c.lat, c.lng) >= far_radius_min
        ]
        if far_candidates:
            ranked_far = sorted(
                far_candidates,
                key=lambda c: (
                    # Dusk first, and only when it binds: False sorts before
                    # True, so a dark-good finisher outranks a dark-bad one of
                    # ANY score on an after-dusk day — a rank preference, not
                    # a gate: when no lit finisher fits the pull, the dark one
                    # still serves, disclosed below.
                    not c.good_after_dark if finish_after_dusk else False,
                    -poi_score(c, spine, interest, snapshot, penalty=score_penalty, party=party),
                    c.id,
                ),
            )[:ENDPOINT_PULL_CANDIDATE_TOP_K]
            for cand in ranked_far:
                pulled = _apply_endpoint_pull(
                    selected,
                    cand,
                    spine=spine,
                    interest=interest,
                    snapshot=snapshot,
                    start_lat=start_lat,
                    start_lng=start_lng,
                    walk_budget=walk_budget,
                    leg_seconds_fn=leg_fn,
                    score_penalty=score_penalty,
                    max_leg_seconds=max_leg_seconds,
                    party=party,
                    # A pin is never traded for a nicer ending (plan S3.6).
                    protected_ids=pinned_ids,
                )
                if pulled is not selected and pulled[-1].id == cand.id:
                    selected = pulled
                    pulled_endpoint_id = cand.id
                    break

    # Phase 7 fill pass — the dwell target is a floor. Greedy +
    # endpoint-pull may emit a route well below it when cost-efficient
    # additions run out. Add anchors with a relaxed cost-efficiency
    # threshold until target is met or walk budget is nearly spent.
    # See FILL_PASS_* constants for thresholds.
    rescue_added_ids: list[str] = []
    selected = _apply_fill_pass(
        selected,
        candidates,
        spine=spine,
        interest=interest,
        snapshot=snapshot,
        start_lat=start_lat,
        start_lng=start_lng,
        leg_seconds_fn=leg_fn,
        score_penalty=score_penalty,
        party=party,
        round_trip=input.round_trip,
        walk_budget=walk_budget,
        dwell_budget=dwell_budget,
        stop_cost_fn=_stop_cost,
        exempt_anchor_id=exempt_anchor_id,
        rescue_floor=RESCUE_STOP_FLOOR,
        fixed_end=input.end,
        rescue_candidates=(corridor_rescue_candidates if input.end is not None else []),
        rescue_added_ids=rescue_added_ids,
        max_leg_seconds=max_leg_seconds,
    )

    # A rescue is allowed to trade walking-allocation seconds for useful audio,
    # but never to breach the existing total elapsed ceiling.  Recompute an exact
    # pinned-B Held-Karp route with the shipped capped beat plans and roll back the
    # lowest-value rescue additions deterministically until it fits.  Ordinary
    # greedy/fill selections are untouched; they already obey their tighter walk
    # budgets.
    if input.end is not None and rescue_added_ids:
        elapsed_ceiling = planning_budget.maximum_elapsed_seconds
        rescue_ids = set(rescue_added_ids)
        while True:
            trial_selected, trial_end = _materialize_fixed_end_b(
                selected, end_lat=input.end[0], end_lng=input.end[1]
            )
            trial_selected = order_stops(
                trial_selected,
                fixed_start=(start_lat, start_lng),
                fixed_end=trial_end,
                routed_cost_fn=leg_fn,
            )
            trial_route = summarise_route(
                trial_selected,
                start_lat=start_lat,
                start_lng=start_lng,
                round_trip=False,
                duration_min=input.duration_min,
                spine_area=spine,
                routing_client=routing_client,
                planning_policy=planning_policy,
                # The trim decides whether a rescued stop stays, so it has to
                # price the same clock the tourist experiences. Without this the
                # trial's visits are zero and the loop keeps a route it believes
                # fits and the traveller will overrun by hours. Arrival-priced
                # (deviation v's exact half), like the repair's trials.
                planned_visit_seconds=_arrival_priced_visits(
                    trial_selected,
                    # trial_selected already ends at the materialized B, so the
                    # chain needs no fixed-end coordinate appended.
                    _full_route_leg_seconds(
                        trial_selected,
                        start_lat=start_lat,
                        start_lng=start_lng,
                        round_trip=False,
                        leg_seconds_fn=leg_fn,
                    ),
                    clock_start=clock_start,
                    price_visit=price_visit,
                ),
                costing_options_override=surface_override,
            ).model_copy(update={"fixed_end_poi_id": trial_end.id})
            trial_dwell = served_dwell_seconds(
                trial_route,
                snapshot,
                interest=interest,
                end_is_none=False,
                narration_density=input.narration_density,
                listening_rate=listening_rate,
            )
            trial_elapsed = served_elapsed_seconds(trial_route.total_walk_seconds, trial_dwell)
            if trial_elapsed <= elapsed_ceiling:
                break
            removable = [poi for poi in selected if poi.id in rescue_ids]
            if not removable:
                break
            drop = min(
                removable,
                key=lambda poi: (
                    poi_score(poi, spine, interest, snapshot, penalty=score_penalty, party=party),
                    poi.id,
                ),
            )
            selected = [poi for poi in selected if poi.id != drop.id]
            rescue_ids.remove(drop.id)

    # RESTS ARE PART OF THE DAY THE REPAIR PLANS (plan S3.6; §4.5.2 — rests
    # are promise-grade this phase). Body stops seat AFTER the repair, on the
    # final walking order — but their minutes are real, so a repair that fills
    # the day to the nominal leaves them nowhere to stand: measured on a
    # 60-minute fixture, the seated bench pushed a 3,560 s day to 3,849
    # against a 3,600 s hard ceiling and the whole request REFUSED — a family
    # asking for rest breaks on a full day got no day at all. The repair
    # therefore aims at a clock shrunk by the expected rest time: cadence
    # crossings the walking budget could produce, capped by how many body
    # places exist, each priced at the pool's own mean seat time plus the
    # seating rule's worst diversion walk. No cadence = no reserve = today.
    repair_budget = planning_budget
    body_pool = [p for p in snapshot.pois if p.poi_role == "body"]
    if input.rest_cadence_minutes is not None and body_pool and replan is None:
        # A REPLAN takes no reserve (Phase 5 S5.6): the reserve is a fresh plan's
        # device — shrink first, seat later — and it is exactly what funded rests
        # by deleting an anchor and then seated none (CARRIED 3: the shrunk repair
        # shed the walking that would have crossed the cadence). Relative to a
        # day that exists, the planner keeps the day, seats the benches on it,
        # and only then sheds FABRIC through the one drop primitive if it overruns.
        expected_rests = min(
            planning_budget.walk_budget_seconds // (input.rest_cadence_minutes * 60),
            len(body_pool),
        )
        mean_rest_seconds = round(
            sum(p.typical_duration_min for p in body_pool) / len(body_pool) * 60
        )
        # A rest costs its seat time PLUS the walk off the path and back —
        # measured on the founding fixture the detour (317 s) outweighed the
        # seat (300 s), so a seat-only reserve still burst the ceiling. Priced
        # at the engine's own pace over the seating rule's worst diversion.
        rest_detour_seconds = pace_corrected_walk_seconds(2 * BODY_STOP_MAX_DETOUR_M)
        rest_reserve = expected_rests * (mean_rest_seconds + rest_detour_seconds)
        if rest_reserve > 0:
            repair_budget = replace(
                planning_budget,
                minimum_elapsed_seconds=max(
                    0, planning_budget.minimum_elapsed_seconds - rest_reserve
                ),
                nominal_elapsed_seconds=max(
                    0, planning_budget.nominal_elapsed_seconds - rest_reserve
                ),
                maximum_elapsed_seconds=max(
                    0, planning_budget.maximum_elapsed_seconds - rest_reserve
                ),
            )
    # The timebox repair now runs for EVERY route shape. It used to be gated on the
    # policy not being the legacy one, so an open or round-trip walk on the phone's
    # path never got band repair at all.
    selected = _apply_certification_timebox_repair(
        selected,
        certification_candidates if certification_fixed_end else candidates,
        input=input,
        snapshot=snapshot,
        spine=spine,
        interest=interest,
        score_penalty=score_penalty,
        leg_seconds_fn=leg_fn,
        planning_policy=planning_policy,
        planning_budget=repair_budget,
        # Certification must price what the ORDER pass below will actually build.
        # That pass pins the pulled endpoint last (see the `elif` there), so the
        # repair has to carry the same pin or it certifies a cheaper route than
        # the one the traveller is given.
        pulled_endpoint_id=pulled_endpoint_id,
        # And it must price the same SHAPES on the same CLOCK the traveller is
        # served: closed doors, queues at the arrival hour, weather, wall —
        # the exact half of deviation v's estimate-then-exact contract.
        price_visit=price_visit,
        clock_start=clock_start,
        # The pulled endpoint and every pin are PROMISES (plan S3.6;
        # §4.5.2/§4.5.4): their selection encodes rules the repair's rank
        # cannot see (the dusk preference; the visitor's own certainty), so
        # no drop or exchange may remove them.
        protected_promise_ids=(
            set(held_ids) | ({pulled_endpoint_id} if pulled_endpoint_id is not None else set())
        )
        or None,
        # Queues are BUDGETED, never credited (the panel's convergent ruling):
        # the rank's fit term reads elapsed minus these seconds.
        queue_visit=lambda cand, hour: shape_visit(cand, hour).queue_seconds,
        report_overrun=replan is not None and replan.floor_zero,
        listening_rate=listening_rate,
        # THE FOURTH MOVE'S CHANNEL (design §4.5.1). Add, drop and exchange all
        # speak through the returned stop list; "keep this stop, price it
        # exterior-only" has nowhere to go in a list of POIs, so it rides this
        # set — the one THE pricing closure above already reads. `shape_visit`
        # comes with it because the move only offers a stop the visitor would
        # otherwise have gone INSIDE.
        #
        # PLANNING ONLY — NEVER ON A LIVE REPLAN, and this is the whole rule.
        # Design §4.3: "fabric may change silently; promises may not." At planning
        # time nothing has been promised yet, so trading a cathedral's interior for
        # its facade is an honest way to serve the hour that was asked for. Once the
        # day is on the wire that interior IS a promise, and withdrawing it because
        # the walker is running late is a silently thinner day — exactly what W8.2's
        # ruling R8 forbids. The session's own machinery already owns that moment:
        # it ASKS ("keep the rest, or shorten?") rather than deciding for her.
        # MEASURED 2026-08-25: with the move live on the replan path,
        # `test_a_day_built_around_one_place_asks_rather_than_dropping_it` lost its
        # question entirely — the engine stepped Rosemary's Orsay outside instead of
        # asking her. Forcing `exterior_only` off restored the question exactly.
        exterior_only_ids=exterior_only_ids if replan is None else None,
        shape_visit=shape_visit if replan is None else None,
    )
    for stepped_id in exterior_only_ids:
        # The greedy's estimate memo predates the step outside (the same
        # staleness the concentrate pass clears after a drop's grant).
        _visit_memo.pop(stepped_id, None)

    # "FEWER STOPS, LONGER AT EACH" (Phase 4 dial, W4.2 — Camille/Nadia's
    # locked label). Concentrate AFTER the repair: drop the weakest
    # unprotected stops so the day keeps its anchors whole and gains named
    # slack. Bounded three ways (constants above): never below three stops,
    # never below the concentrate floor (which sits above the underfill
    # refusal line — a dial must never steer the day into its own refusal),
    # never more than the drop fraction. Promises are never dropped —
    # §4.5.2's rule reaching this pass too.
    if input.stop_density == "fewer" and len(selected) > CONCENTRATE_MIN_STOPS:
        protected_for_density = set(held_ids) | (
            {pulled_endpoint_id} if pulled_endpoint_id is not None else set()
        )
        max_drops = max(1, round(len(selected) * CONCENTRATE_MAX_DROP_FRACTION))
        drops = 0
        while drops < max_drops and len(selected) > CONCENTRATE_MIN_STOPS:
            # Weakest first — but THE ONE DROP PRIMITIVE decides whether a drop
            # may be taken (Phase 5 S5.5): a drop whose merged leg breaks the
            # per-leg cap is not taken (design §4.5.3), so the pass walks down
            # the weakness order to the first stop it may drop, and the freed
            # minutes flow to the survivors (W4.2 locked semantics 4) through
            # `visit_extension_seconds`, which THE pricing closure reads.
            droppable = sorted(
                (p for p in selected if p.id not in protected_for_density),
                key=lambda p: (
                    poi_score(
                        p,
                        spine,
                        interest,
                        snapshot,
                        penalty=score_penalty,
                        party=input.party,
                    ),
                    p.id,
                ),
            )
            taken = False
            for weakest in droppable:
                outcome = drop_stop(
                    selected,
                    weakest,
                    start=(start_lat, start_lng),
                    close=greedy_close,
                    leg_seconds_fn=leg_fn,
                    max_leg_seconds=max_leg_seconds,
                    visit_of=lambda p: price_visit(p, request_hour),
                    ceiling_of=ceiling_visit,
                    protected_ids=protected_for_density,
                )
                if not outcome.merged_leg_fits:
                    continue
                trial = _certification_route_trial(
                    list(outcome.remaining),
                    input=input,
                    snapshot=snapshot,
                    interest=interest,
                    leg_seconds_fn=leg_fn,
                    planning_budget=repair_budget,
                    pulled_endpoint_id=pulled_endpoint_id,
                    price_visit=price_visit,
                    clock_start=clock_start,
                    queue_visit=lambda cand, hour: shape_visit(cand, hour).queue_seconds,
                    listening_rate=listening_rate,
                )
                if trial is None or (
                    trial.elapsed_seconds - trial.queue_seconds
                    < CONCENTRATE_FLOOR_FRACTION * planning_budget.nominal_elapsed_seconds
                ):
                    continue
                selected = list(outcome.remaining)
                for poi_id, extra in outcome.granted.items():
                    visit_extension_seconds[poi_id] = visit_extension_seconds.get(poi_id, 0) + extra
                    _visit_memo.pop(poi_id, None)  # the estimate memo is stale now
                drops += 1
                taken = True
                break
            if not taken:
                break

    # Phase 7.5 Fix 3: detect co-located POI pairs in the final selection
    # and demote the smaller-tier of each pair. Demoted POI beats are
    # merged into the host's pool by the harness via Route.demoted_beats.
    selected, demoted_beats = apply_co_located_demotion(
        selected,
        snapshot,
        never_fold=held_ids,
        leg_seconds_fn=leg_fn,
        max_leg_seconds=max_leg_seconds,
        start=(start_lat, start_lng),
        close=greedy_close,
    )
    # Belt-and-suspenders against a place loaded twice (same display NAME, distinct
    # id): every dedup path above keys on POI id, and apply_co_located_demotion's
    # tier>=4 + cross-address-token gate can skip a bare same-name twin, so an
    # id-distinct twin would otherwise surface as two adjacent stops (the workbench
    # duplicate-stop bug — the beat-starved copy reads "Walk to the next stop."). Fold
    # twins by NAME here; the dropped twin's beats merge into the survivor. On a clean
    # corpus (globally unique names) this is a strict no-op.
    selected, twin_beats = collapse_name_twins(
        selected,
        snapshot,
        never_fold=held_ids,
        leg_seconds_fn=leg_fn,
        max_leg_seconds=max_leg_seconds,
        start=(start_lat, start_lng),
        close=greedy_close,
    )
    for host_id, beats in twin_beats.items():
        demoted_beats[host_id] = demoted_beats.get(host_id, ()) + beats

    # M4 ORDER: exact Held-Karp pass over the final set — the greedy's
    # insertion order is a by-product of selection, not an optimum. A pulled
    # endpoint stays pinned last (if demotion didn't absorb it); round trips
    # optimize the closed tour.
    #
    # Step 2.4: when the request carries a fixed end B, materialize B (HYBRID:
    # snap to the nearest in-corridor selected POI within ~150m, else
    # synthesize a sentinel at B's exact coord and add it to ``selected``) and
    # pin it as ``fixed_end`` so the route literally ends at B. This SUPERSEDES
    # the endpoint-pull-derived fixed_end on the fixed-end path. The end-is-None
    # branch is LITERALLY unchanged (Step-2.0d identity baseline holds).
    fixed_end = None
    if input.end is not None and (selected or replan is not None):
        # A replanned TAIL with nothing that fits still walks to its finish: the
        # end sentinel alone is "one leg home" (W5.2 R1.1), so B materializes
        # even over an empty selection under a ReplanContext.
        selected, fixed_end = _materialize_fixed_end_b(
            selected, end_lat=input.end[0], end_lng=input.end[1]
        )
    elif pulled_endpoint_id is not None and not input.round_trip:
        fixed_end = next((p for p in selected if p.id == pulled_endpoint_id), None)
    selected = order_stops(
        selected,
        fixed_start=(start_lat, start_lng),
        fixed_end=fixed_end,
        round_trip=input.round_trip,
        routed_cost_fn=leg_fn,
    )
    # A round trip's direction is chosen, not inherited from a tie-break
    # (plan S3.8a): the marquee lands late when reversal is walk-free.
    if input.round_trip:
        selected = _orient_loop(
            selected,
            snapshot=snapshot,
            interest=interest,
            start=(start_lat, start_lng),
            leg_seconds_fn=leg_fn,
        )

    # Never ship a silent 0-stop tour. Even when REACH found candidates, the
    # greedy can seat NONE of them (every reachable anchor's round-trip insertion
    # cost exceeds a tight walk budget — e.g. a short tour from an isolated start),
    # or demotion/collapse can fold the selection to empty. In any city that is an
    # honest refusal, not a served empty route: surface the assessment's
    # alternatives (extend the duration, move the start) like the density-RED path.
    # (A→B has already materialized its pinned end B above, so a fixed-destination
    # tour is never empty here.)
    if not selected and replan is None:
        raise TourabilityRefusedError(
            assessment,
            f"No POIs could be seated within a {input.duration_min}-min walk of this "
            f"start — try a longer duration or a start nearer the density.",
        )
    # BODY STOPS (redesign §3.1; plan S2.5) — AFTER the emptiness check (a
    # body stop must never mask a genuinely empty story route) and AFTER
    # every scoring/certification pass (a bench is scheduled by the body
    # clock, never the story ranking). Only the closing return-to-start leg
    # of a round trip is not considered — Phase 2's mechanical-seating scope;
    # phase2-ledger.md records it.
    if input.rest_cadence_minutes is not None:
        # body_pool was derived once, above the repair (its rest reserve).
        selected = _seat_body_stops(
            selected,
            start=(start_lat, start_lng),
            rest_cadence_minutes=input.rest_cadence_minutes,
            body_pool=body_pool,
            leg_seconds_fn=leg_fn,
        )
    if replan is not None and selected:
        # SHED FABRIC, NEVER A HELD STOP OR A REST (Phase 5 S5.6). A replan took
        # no rest reserve: it kept the day and seated its benches on it. If the
        # seated day now overruns the minutes, drop the weakest unprotected
        # story stop through THE one drop primitive — merged leg re-checked
        # against the cap (§4.5.3), freed minutes handed to the survivors within
        # their ceilings (locked semantics 4) — until it fits or nothing droppable
        # remains; whatever overrun is left is REPORTED at the final gate, never
        # refused (W5.2 R1.1). Rests are the protected class (§4.5.2): a bench is
        # never the thing that pays.
        def _elapsed_of(stops: list[POI]) -> int | None:
            # THE FINAL GATE'S OWN ARITHMETIC on the order as it stands — legs
            # through the one leg function, visits at their arrival hours, dwell
            # through `served_dwell_seconds` — never a re-ordered trial (a
            # Held-Karp trial found 3,535 s where the served order, bench detour
            # included, was 3,849 s, and the loop stopped shedding a day that
            # then shipped over its ceiling).
            legs = _full_route_leg_seconds(
                stops,
                start_lat=start_lat,
                start_lng=start_lng,
                round_trip=input.round_trip,
                leg_seconds_fn=leg_fn,
                fixed_end=None if input.end is None or _has_end_sentinel(stops) else input.end,
            )
            arrivals = _walk_arrivals(
                stops, legs, clock_start=clock_start, price_visit=price_visit
            )
            priced = Route(
                pois=tuple(stops),
                transits=(),
                total_walk_distance_m=0.0,
                total_walk_seconds=sum(legs),
                planned_visit_seconds={poi.id: secs for poi, _h, secs, _c in arrivals},
            )
            dwell = served_dwell_seconds(
                priced,
                snapshot,
                interest=interest,
                end_is_none=input.end is None,
                narration_density=input.narration_density,
                listening_rate=listening_rate,
            )
            return served_elapsed_seconds(sum(legs), dwell)

        while True:
            elapsed = _elapsed_of(selected)
            if elapsed is None or elapsed <= planning_budget.nominal_elapsed_seconds:
                break
            droppable = sorted(
                (
                    p
                    for p in selected
                    if p.id not in held_ids
                    and p.poi_role != "body"
                    and p.id != pulled_endpoint_id
                    and not p.id.startswith(END_B_SENTINEL_PREFIX)  # the finish is not fabric
                ),
                key=lambda p: (
                    poi_score(
                        p,
                        spine,
                        interest,
                        snapshot,
                        penalty=score_penalty,
                        party=input.party,
                    ),
                    p.id,
                ),
            )
            shed = False
            for weakest in droppable:
                # A drop whose merged leg is the WALK HOME is bounded by the person's
                # own limit only (one leg home is legal, W5.2 R1.1), never by the base
                # day's longest-leg guardrail; a drop between two kept stops keeps both.
                idx = next(i for i, p in enumerate(selected) if p.id == weakest.id)
                successor_is_finish = idx + 1 >= len(selected) or selected[
                    idx + 1
                ].id.startswith(END_B_SENTINEL_PREFIX)
                person_cap = (
                    input.max_leg_minutes * 60 if input.max_leg_minutes is not None else None
                )
                outcome = drop_stop(
                    selected,
                    weakest,
                    start=(start_lat, start_lng),
                    close=greedy_close,
                    leg_seconds_fn=leg_fn,
                    max_leg_seconds=person_cap if successor_is_finish else max_leg_seconds,
                    visit_of=lambda p: price_visit(p, request_hour),
                    ceiling_of=ceiling_visit,
                    protected_ids=set(held_ids),
                )
                if not outcome.merged_leg_fits:
                    continue
                selected = list(outcome.remaining)
                for poi_id, extra in outcome.granted.items():
                    visit_extension_seconds[poi_id] = visit_extension_seconds.get(poi_id, 0) + extra
                    _visit_memo.pop(poi_id, None)
                shed = True
                break
            if not shed:
                break
    # Disclose a dark finish nothing could fix (plan S3.7): the day is dated,
    # runs past dusk, ends where the walk ends (one-way — a loop finishes at
    # the visitor's own start and a fixed B is their own choice), and that
    # place is not good after dark. The pull preferred every lit finisher
    # first, so reaching here means none fit — say so rather than let four
    # confident stops imply the ending was judged fine (Sofia stands at the
    # end of this day alone; 11-solo-after-dark).
    if (
        finish_after_dusk
        and input.end is None
        and not input.round_trip
        and selected
        and not selected[-1].good_after_dark
    ):
        clock_exclusions.append(
            ClockExclusion(
                poi_id=selected[-1].id,
                name=selected[-1].name,
                reason=(
                    "finishes after civil dusk at a place not good after dark; "
                    "no lit finisher fit this day"
                ),
            )
        )
    # SAY THE STEP OUTSIDE (W8.2 ruling R8 — a silently thinner day is
    # forbidden). The repair's fourth move kept these stops and dropped their
    # interiors, which changes what the visitor was promised, so the day carries
    # the fact on the SAME notes channel a locked door and an unlit finish
    # already use. The sentence, and the `kept_outside` left False beside it,
    # are `interior_did_not_fit_reason`'s; its docstring says why. Only stops
    # that actually SURVIVED onto the day are named: one the repair stepped
    # outside and then traded away is not in the day at all. A stop the clock
    # already spoke for keeps that sentence — its door was shut before the
    # minutes were ever the question.
    already_disclosed = {excl.poi_id for excl in clock_exclusions}
    for poi in selected:
        if poi.id in exterior_only_ids and poi.id not in already_disclosed:
            clock_exclusions.append(
                ClockExclusion(
                    poi_id=poi.id,
                    name=poi.name,
                    reason=interior_did_not_fit_reason(input.duration_min),
                )
            )
    # The served order's ONE arrival walk (deviation v's exact half): visit
    # prices and promise shapes both read it, so the two can never disagree
    # about the hour a visitor stands anywhere.
    final_arrivals = _walk_arrivals(
        selected,
        _full_route_leg_seconds(
            selected,
            start_lat=start_lat,
            start_lng=start_lng,
            round_trip=input.round_trip,
            leg_seconds_fn=leg_fn,
        ),
        clock_start=clock_start,
        price_visit=price_visit,
    )
    route = summarise_route(
        selected,
        start_lat=start_lat,
        start_lng=start_lng,
        round_trip=input.round_trip,
        duration_min=input.duration_min,
        spine_area=spine,
        routing_client=routing_client,
        planning_policy=planning_policy,
        # PRICED ONCE, HERE. This is the only place in the request with both the
        # corpus snapshot (needed to read a POI's beat lenses) and the final stop
        # set, so it is the only place the answer can be computed. Generation has
        # neither. Carrying it on the Route is what stops the planner and the
        # served tour disagreeing about how long a stop lasts.
        # THE SAME CLOSURE THE GREEDY SPENT, AT THE SERVED CLOCK. The greedy
        # budgeted each stop through `price_visit` at the request's START hour
        # (its estimate); the served route prices each stop through the SAME
        # closure at its ordered ARRIVAL hour — deviation v's estimate-then-
        # exact, one pricing spelling, two clock reads. The repair's trials
        # used this exact form, so the set it certified and the route served
        # here are the same quantity by construction. At the identity defaults
        # (dateless / no weather / no closures / unpassed queues) both reads
        # are byte-identical to the pre-promise `visit_seconds`.
        planned_visit_seconds={poi.id: seconds for poi, _hour, seconds, _clock in final_arrivals},
        costing_options_override=surface_override,
    )
    if demoted_beats:
        route = route.model_copy(update={"demoted_beats": demoted_beats})
    route = route.model_copy(update={"reach": reach})
    # THE HONESTY SURFACE'S PER-STOP FACTS (Phase 4, W4.2 deviation v): queue
    # minutes and the in/out side of the door, priced at the SAME ordered
    # arrival hours the visits were priced at — one clock read, so the surface
    # a person commits on and the budget the day was planned on can never
    # disagree (Camille's finding: a stop seated inside at peak with a blank
    # queue column is a finish promise unpriced by half an hour).
    route = route.model_copy(
        update={
            "planned_queue_seconds": {
                poi.id: shape_visit(poi, hour, clock).queue_seconds
                for poi, hour, _seconds, clock in final_arrivals
            },
            "visit_goes_inside": {
                poi.id: shape_visit(poi, hour, clock).inside_seconds > 0
                for poi, hour, _seconds, clock in final_arrivals
            },
            # Phase 7 S7.6: the placed OUTSIDE seconds — the same shape at the same
            # hour; the audio placement rule's threshold before a door.
            "planned_outside_seconds": {
                poi.id: shape_visit(poi, hour, clock).outside_seconds
                for poi, hour, _seconds, clock in final_arrivals
            },
        }
    )
    # SAY THE DOOR THE ARRIVAL CLOCK SHUT (the step-outside mould above). The
    # pool rule spoke for whole-window closures before any stop was seated; a
    # door shut at one stop's own arrival is known only now, off the served
    # walk, so its disclosure lands here — the same notes channel, and the same
    # `kept_outside` flag a whole-window demotion carries: the facade is kept,
    # the door is voided, and every reader draws "closed" from the flag.
    already_disclosed = {excl.poi_id for excl in clock_exclusions}
    for poi, hour, _seconds, clock in final_arrivals:
        if poi.id in already_disclosed:
            continue
        arrival_reason = _arrival_door_reason(poi, hour, clock)
        if arrival_reason is not None:
            clock_exclusions.append(
                ClockExclusion(
                    poi_id=poi.id,
                    name=poi.name,
                    reason=arrival_reason,
                    kept_outside=True,
                    # An arrival-window closure on a day with windows is by
                    # construction not all-day; read the table anyway so a
                    # data edit cannot desynchronise the two claims.
                    all_day=(
                        _closed_all_day(poi.opening_hours, clock)
                        if poi.opening_hours is not None
                        else False
                    ),
                )
            )
    # C9 governor exempt identity — record which POIs are EXEMPT from the per-stop
    # audio cap so compose and the golden harnesses (which lack the greedy locals,
    # and where pois[0] is NOT the start-anchor after Held-Karp) read the SAME
    # exempt set the greedy used. Additive metadata only; pois/transits/beats are
    # untouched (identity baseline holds bit-for-bit). None on A→B / empty routes.
    # Only persist an anchor id that is ACTUALLY in the final route: the greedy's
    # first-seated exempt_anchor_id can be dropped afterward by co-located demotion
    # (folded into its host), which would otherwise leave start_anchor_poi_id
    # dangling on a POI the route no longer contains. (v4's governor exempts the
    # marquee, computed in-wrapper, so this field is advisory — but keep it
    # consistent for the persisted options_json contract.)
    route_poi_ids = {p.id for p in route.pois}
    anchor_update: dict[str, str | None] = {}
    if exempt_anchor_id is not None and exempt_anchor_id in route_poi_ids:
        anchor_update["start_anchor_poi_id"] = exempt_anchor_id
    if fixed_end is not None and fixed_end.id in route_poi_ids:
        anchor_update["fixed_end_poi_id"] = fixed_end.id
    if anchor_update:
        route = route.model_copy(update=anchor_update)
    if clock_exclusions:
        # Additive metadata (the vignettes mould): only a dated request can
        # collect these, so every dateless route stays byte-identical.
        route = route.model_copy(update={"clock_exclusions": tuple(clock_exclusions)})
    # THE DAY'S PROMISES (plan S3.6; design §3.1): named off the served order,
    # each shape priced at the hour the visitor stands there — the same
    # arrival walk the visit prices used. Additive, the vignettes mould.
    promises = _assemble_promises(
        final_arrivals,
        snapshot=snapshot,
        interest=interest,
        shape_visit=shape_visit,
        pinned_ids=pinned_ids,
    )
    if promises:
        route = route.model_copy(update={"promises": promises})
    # Last-line invariant, now applied to EVERY route shape: after materialization,
    # demotion, exact ordering and final-closing governance, the route must still sit
    # inside the same frozen band the repair used. The legacy fixed-end-only variant
    # of this guard was deleted 2026-08-04 with the policy it belonged to.
    #
    # THE ONE DEFINITION OF ELAPSED: walking plus standing still, where standing
    # still is `served_dwell_seconds` — the same function the repair's trials and
    # the rescue trim call. Until 2026-08-06 this line added NARRATION to walking,
    # so a tour certified at 300 minutes served walk + visits and could run hours
    # over. Generation serves max(visit, audio) per stop; this counts the same
    # rule on the same capped plans, so certified and served are the same
    # quantity by construction rather than by coincidence.
    final_dwell = served_dwell_seconds(
        route,
        snapshot,
        interest=interest,
        end_is_none=input.end is None,
        narration_density=input.narration_density,
        listening_rate=listening_rate,
    )
    # THE CEILING IS HARD AND THE FLOOR IS SOFT.
    #
    # Over the request: refuse. A tourist who asks for three hours and is handed
    # three hours twenty has been given a worse thing than a refusal, because they
    # will discover it at the far end of a walk.
    #
    # Under the floor: SHIP IT and say so. Duration is a ceiling, not a contract
    # to fill, and the only way a planner can guarantee filling one is by walking
    # you somewhere pointless — which is the reported bug. An honestly short tour
    # with a sentence explaining it is a better product, and it closes the last
    # door through which that bug returns.
    elapsed_ceiling = planning_budget.nominal_elapsed_seconds
    final_elapsed = served_elapsed_seconds(route.total_walk_seconds, final_dwell)
    if final_elapsed > elapsed_ceiling and replan is not None and replan.floor_zero:
        # A REPLANNED remainder that runs over its minutes is REPORTED, never
        # refused (Phase 5 S5.6; W5.2 R1.1/R2): the protected day comes back
        # with the overrun on it — the raw material of the ONE question — because
        # a tail is not a request the phone can take a 422 for.
        route = route.model_copy(update={"overrun_seconds": final_elapsed - elapsed_ceiling})
    elif final_elapsed > elapsed_ceiling + TIMEBOX_MATERIALITY_TOLERANCE_SECONDS:
        # THE CEILING IS HARD, AND "OVER" MEANS MATERIALLY OVER (Phase 8 W8.6,
        # the panel's Q4, 11/11 SERVE). This compared strictly, so a route
        # pricing its request plus TWENTY-TWO SECONDS was refused outright —
        # measured live on the paris never-empty invariant, 15022 s against
        # 15000 s (evidence/phase8-gates/w86-invariants-fresh.log). The engine's
        # own `TIMEBOX_MATERIALITY_TOLERANCE_SECONDS` already declares a route
        # within one displayed minute "materially in the same customer timebox",
        # and `within_planning_timebox` has applied it on both sides of the band
        # since it was written — this one gate did not, so the product
        # contradicted its own definition of material. Rosemary: "an engine that
        # declares that and then refuses over twenty-two seconds does not have a
        # rule; it has a mood." Sofia: "no gate may refuse on a quantity below
        # one displayed minute." Marcus, whose train is the reason the ceiling is
        # hard at all: "22 seconds is under one traffic light of noise … my
        # margin lives in whole minutes I build myself."
        #
        # Nothing else moves. The tolerance is the SAME one minute the band check
        # uses (§10.8.2 — one definition of material), a route genuinely over its
        # request still refuses with its alternatives, and the replanned-tail
        # branch above still REPORTS its overrun from the raw ceiling because a
        # tail never refuses and its number is the question's raw material.
        alternatives, gap_minutes = _band_alternatives(
            input=input,
            planning_policy=planning_policy,
            best_elapsed_seconds=final_elapsed,
        )
        raise CertificationPlanningInfeasibleError(
            policy_id=planning_policy.policy_id,
            minimum_elapsed_seconds=planning_budget.minimum_elapsed_seconds,
            maximum_elapsed_seconds=elapsed_ceiling,
            best_elapsed_seconds=final_elapsed,
            reason=(
                "post-selection transforms pushed the exact route OVER the "
                "requested duration, and no in-band drop was available"
            ),
            alternatives=alternatives,
            gap_minutes=gap_minutes,
        )
    # THE ONE UNDERFILL LINE (Phase 5 S5.3). The floor is soft above it, and
    # below it a day is refused with what binds named — the W4.2 panel's
    # unanimous worst finding (D-i: a best-possible day under HALF the ask is
    # "no day, mislabelled"; Aiko, Rosemary, Greta by name in phase4-ledger.md).
    # It lives HERE and only here, after every pass that can shorten a day has
    # run — the repair, the concentrate dial, co-located demotion, twin collapse,
    # ordering, orientation, body seating — because W4.11 measured the passes
    # composing under a line each of them respected on its own (leg 9 + rest 40
    # + fewer shipped 68 of 180; W5.1 re-fetched it), and because the repair's
    # copy of this line compared against the repair's rest-shrunk budget while
    # printing that shrunk number as "asked for" (Rosemary read "132 minutes
    # asked for" against her 180 — W5.1 defect 7). Currency: the day's
    # EXPERIENCE — elapsed minus the queue seconds it budgets, the panel-ruled
    # number ("queues are budgeted, never credited") — against the REQUEST's own
    # nominal. `open` zeroes the floor and is exempt by construction (design
    # §2.3, Julien's leavable-blank clock).
    final_experience = final_elapsed - sum(route.planned_queue_seconds.values())
    if (
        planning_budget.minimum_elapsed_seconds > 0
        and final_experience < UNDERFILL_REFUSAL_FRACTION * planning_budget.nominal_elapsed_seconds
    ):
        # In the dial's own words (Paulo, W4.12: "a cap, on a leg, that binds —
        # three second meanings of everyday words in one clause").
        binding = (
            f"the {input.max_leg_minutes}-minute limit on any single walk is "
            f"what stops it — allow longer walks, or ask for a shorter day"
            if input.max_leg_minutes is not None
            else "there is not enough to visit near this start for a day this long"
        )
        alternatives, gap_minutes = _band_alternatives(
            input=input,
            planning_policy=planning_policy,
            best_elapsed_seconds=final_elapsed,
        )
        raise CertificationPlanningInfeasibleError(
            policy_id=planning_policy.policy_id,
            minimum_elapsed_seconds=planning_budget.minimum_elapsed_seconds,
            maximum_elapsed_seconds=elapsed_ceiling,
            best_elapsed_seconds=final_elapsed,
            reason=(
                f"the longest day that can be built here is about "
                f"{final_elapsed // 60} minutes, far short of the "
                f"{input.duration_min} minutes asked for; {binding}"
            ),
            alternatives=alternatives,
            gap_minutes=gap_minutes,
        )
    if final_elapsed < planning_budget.minimum_elapsed_seconds:
        # Measured against what was ASKED FOR, not against the floor: the sentence
        # the traveller reads is "this is 4h40 rather than the 5h you asked for",
        # and 4h30 is an internal threshold they never see.
        route = route.model_copy(
            update={"elapsed_shortfall_seconds": elapsed_ceiling - final_elapsed}
        )
    # Track B (Step B.2): attach walk-past vignettes AFTER ordering — the leg
    # geometry is final only now. Additive metadata: ``pois``/``transits`` are
    # untouched (the identity baseline holds bit-for-bit).
    # The snapshot-aware density boundary already performed the one full
    # manifest/materialization validation for this route.  Use the private
    # implementation here so vignette selection stays O(corpus), not another
    # full replay validation.  External callers use the guarded public wrapper.
    vignettes = _select_vignettes_validated(route, snapshot, lenses=interest or None)
    if vignettes:
        route = route.model_copy(update={"vignettes": vignettes})
    # C11a: a GREEN-density pool can still DELIVER thin — audio far under the
    # request (few reachable/affordable dwell POIs, or beat-thin ones) or a
    # single dominating stop. Flag it so the surface discloses "honest but thin"
    # instead of silently reading fully-GREEN. (The engine-side answer to the
    # 2026-07-02 pool-vs-delivered gap; replaces the client-side thin heuristic.)
    if assessment.status == "GREEN":
        # v4: measure the UNCAPPED available content, NOT the governor-capped
        # emission. The governor only moves a dominating stop's overflow into
        # keep-exploring extras (still available to the walker on demand), so a
        # capped tour is not "thin" — its content is all there. Measuring capped
        # audio would flip a healthy tour to a spurious thin banner when the
        # governor fires (the panel's bug-5). This is the original C11a currency.
        #
        # BOTH SIDES ARE STANDING-STILL SECONDS since 2026-08-06. Comparing
        # delivered NARRATION against a dwell target would call a tour thin for
        # being quiet, which is the opposite of true: a route of long interior
        # visits is the richest thing this release can produce, and Camille's
        # sixteen silent minutes at Concorde are the tour working.
        delivered_dwell = sum(
            stop_seconds(
                route.planned_visit_seconds.get(p.id, 0),
                planned_capped_audio_seconds(p, snapshot, interest or None, None),
            )
            for p in route.pois
        )
        if (
            delivered_dwell < GREEN_THIN_DELIVERY_FRAC * assessment.target_dwell_seconds
            or len(route.pois) < 2
        ):
            assessment = assessment.model_copy(update={"delivered_thin": True})
    return _attach_tourability_if_yellow(route, assessment)


def apply_co_located_demotion(
    selected: list[POI],
    snapshot: CorpusSnapshot,
    *,
    never_fold: frozenset[str] = frozenset(),
    leg_seconds_fn: LegSecondsFn | None = None,
    max_leg_seconds: int | None = None,
    start: tuple[float, float] | None = None,
    close: tuple[float, float] | None = None,
) -> tuple[list[POI], dict[str, tuple[BeatRef, ...]]]:
    """Return (selected_minus_demoted, host_id → demoted_beats).

    A FOLD IS A DROP (Phase 5 S5.5; design §4.5.3): folding the demoted stop
    away merges the two legs around it. With ``max_leg_seconds`` set (and
    ``start`` given), a fold whose merged leg would break the per-leg cap is NOT
    taken — both stops stay, and the day keeps its walks under the cap. Measured
    live 2026-08-18 (phase5-ledger.md defect 11): the repair certified
    Carnavalet -> Victor Hugo 538 s and Victor Hugo -> Place des Vosges 176 s
    under a 540 s cap; this pass folded Victor Hugo into Place des Vosges and the
    merged 576 s leg shipped. No cap = today's behaviour, byte-identical.

    A pair (A, B) of selected POIs qualifies for demotion when
    ``haversine(A, B) ≤ DEMOTION_PROXIMITY_M`` AND at least one beat at
    one POI carries a ``trigger_address`` or ``sub_location`` mentioning
    a distinctive token of the other POI's name. Smaller tier loses;
    on a tie, fewer beats loses; final tie-break is alphabetical id so
    the outcome is deterministic.

    The Tour 1 case: Musée Victor Hugo (tier 4) sits at the same
    physical address as Place des Vosges no. 6 (tier 5). PdV carries a
    beat with sub_location ``hugo-museum-no-6`` whose tokens overlap
    Hugo museum's distinctive ``hugo`` token. Hugo museum demotes;
    its 2 beats merge into PdV's pool.

    No-op when the route is empty or no pair qualifies.
    """
    if len(selected) < 2:
        return list(selected), {}

    demoted_to_host: dict[str, str] = {}
    for i in range(len(selected)):
        a = selected[i]
        if a.id in demoted_to_host:
            continue
        for j in range(i + 1, len(selected)):
            b = selected[j]
            if b.id in demoted_to_host or a.id in demoted_to_host:
                continue
            # A materialized off-site story is valid only at its canonical
            # destination.  Post-selection demotion must never move it back to
            # an address/name-derived host.
            if a.requires_dwell or b.requires_dwell:
                continue
            if (
                a.canonical_place_id is not None
                and b.canonical_place_id is not None
                and a.canonical_place_id != b.canonical_place_id
            ):
                continue
            # Both POIs must be anchor-tier; pause-tier (≤3) POIs are
            # deliberate empirical-walk stops and must not collapse.
            if a.tier < DEMOTION_MIN_TIER or b.tier < DEMOTION_MIN_TIER:
                continue
            distance = haversine_m(a.lat, a.lng, b.lat, b.lng)
            if distance > DEMOTION_PROXIMITY_M:
                continue
            if not _has_cross_poi_address_overlap(a, b, snapshot):
                continue
            host, demote = _pick_demotion_host(a, b)
            if demote.id in never_fold:
                # A PINNED stop is never folded into a co-located host (plan
                # S3.6): the visitor asked for THIS place as a stop of its
                # own, and a merged host is exactly the silent override a pin
                # forbids. The host being pinned is fine — it survives.
                continue
            if max_leg_seconds is not None and start is not None:
                _held, merged = merged_leg_seconds(
                    selected, demote, start=start, close=close, leg_seconds_fn=leg_seconds_fn
                )
                if merged > max_leg_seconds:
                    continue
            demoted_to_host[demote.id] = host.id

    if not demoted_to_host:
        return list(selected), {}

    new_selected = [p for p in selected if p.id not in demoted_to_host]
    demoted_beats: dict[str, tuple[BeatRef, ...]] = {}
    for demoted_id, host_id in demoted_to_host.items():
        # Fold beats into the ULTIMATE survivor, never an intermediate host that is
        # itself demoted (a 3-way chain would otherwise orphan a POI's whole content:
        # nothing ever reads demoted_beats for a POI that left the route).
        survivor_id = _resolve_demotion_host(host_id, demoted_to_host)
        beats = snapshot.beats_for(demoted_id)
        demoted_beats[survivor_id] = demoted_beats.get(survivor_id, ()) + tuple(beats)
    return new_selected, demoted_beats


def _resolve_demotion_host(host_id: str, demoted_to_host: dict[str, str]) -> str:
    """Follow a co-located demotion host-chain to its ultimate survivor — a host that
    is not itself demoted. With 3+ co-located POIs the pairwise picks can chain
    (A→B, B→C), so A's beats must land on C, not on the demoted intermediate B.
    Cycle-safe via ``seen`` (``_pick_demotion_host`` is antisymmetric, so cycles
    cannot form) — kept for parity with ``_resolve_twin_host``."""
    seen: set[str] = set()
    while host_id in demoted_to_host and host_id not in seen:
        seen.add(host_id)
        host_id = demoted_to_host[host_id]
    return host_id


def _pick_demotion_host(a: POI, b: POI) -> tuple[POI, POI]:
    """Return (host, demoted) given a co-located pair.

    Larger tier hosts; on tie, more-beats hosts; final tie-break is
    alphabetical id (low → host) so the choice is deterministic.
    """
    if a.tier != b.tier:
        return (a, b) if a.tier > b.tier else (b, a)
    if a.beat_count != b.beat_count:
        return (a, b) if a.beat_count > b.beat_count else (b, a)
    return (a, b) if a.id < b.id else (b, a)


def _has_cross_poi_address_overlap(a: POI, b: POI, snapshot: CorpusSnapshot) -> bool:
    """True iff a beat at one POI mentions a distinctive token of the other.

    Looks at ``trigger_address`` and ``sub_location`` (case-insensitive
    substring) on every beat at A and B. Distinctive tokens are name
    fragments ≥ 4 chars that aren't generic topographic prefixes
    ("place", "rue", "musee" etc.) — see ``_NAME_GENERIC_TOKENS``.
    """
    a_tokens = _distinctive_name_tokens(a.name)
    b_tokens = _distinctive_name_tokens(b.name)
    if not a_tokens and not b_tokens:
        return False

    def _beat_address_text(beat: BeatRef) -> str:
        return f"{beat.trigger_address or ''} {beat.sub_location or ''}".lower()

    for beat in snapshot.beats_for(a.id):
        text = _beat_address_text(beat)
        if any(tok in text for tok in b_tokens):
            return True
    for beat in snapshot.beats_for(b.id):
        text = _beat_address_text(beat)
        if any(tok in text for tok in a_tokens):
            return True
    return False


def _distinctive_name_tokens(name: str) -> set[str]:
    """Lowercase name tokens ≥ 4 chars excluding generic topographic words."""
    if not name:
        return set()
    tokens = re.findall(r"[a-zà-öø-ÿ]+", name.lower())
    return {t for t in tokens if len(t) >= _NAME_TOKEN_MIN_LEN and t not in _NAME_GENERIC_TOKENS}


# Two selected POIs sharing a display name within this radius are one place loaded
# twice (a data twin), not two distinct places — the corpus keeps names globally
# unique, so this only ever fires on duplicated data.
NAME_TWIN_PROXIMITY_M = 250.0


def _pick_twin_host(a: POI, b: POI) -> tuple[POI, POI]:
    """(host, dropped) for a same-name twin pair — keep the richer / higher-tier /
    lower-id one so the choice is deterministic and content is preserved."""
    if a.beat_count != b.beat_count:
        return (a, b) if a.beat_count > b.beat_count else (b, a)
    if a.tier != b.tier:
        return (a, b) if a.tier > b.tier else (b, a)
    return (a, b) if a.id < b.id else (b, a)


def _resolve_twin_host(host_id: str, dropped_to_host: dict[str, str]) -> str:
    """Follow a twin host-chain to its ultimate survivor — a host that is not itself
    dropped. With 3+ same-name twins the pairwise picks can chain (A→C, C→B), so a
    dropped twin's beats must land on B, not the dropped intermediate C. Cycle-safe
    via ``seen`` (``_pick_twin_host`` is antisymmetric, so cycles cannot form)."""
    seen: set[str] = set()
    while host_id in dropped_to_host and host_id not in seen:
        seen.add(host_id)
        host_id = dropped_to_host[host_id]
    return host_id


def collapse_name_twins(
    selected: list[POI],
    snapshot: CorpusSnapshot,
    *,
    never_fold: frozenset[str] = frozenset(),
    leg_seconds_fn: LegSecondsFn | None = None,
    max_leg_seconds: int | None = None,
    start: tuple[float, float] | None = None,
    close: tuple[float, float] | None = None,
) -> tuple[list[POI], dict[str, tuple[BeatRef, ...]]]:
    """Collapse selected POIs that share a display NAME into one (name-keyed dedup).

    A FOLD IS A DROP (Phase 5 S5.5; design §4.5.3): with ``max_leg_seconds`` set
    (and ``start`` given), a fold whose merged leg would break the per-leg cap is
    not taken — the same rule as `apply_co_located_demotion`. No cap = today.

    Returns ``(selected_minus_twins, host_id -> merged_twin_beats)``. Two POIs are
    twins when their names match (case-insensitive, trimmed) and they sit within
    ``NAME_TWIN_PROXIMITY_M``. This catches a place loaded twice (same name, distinct
    id) that id-based dedup and the tier-/address-gated co-located demotion both miss.
    The dropped twin's beats fold into the survivor so no content is lost.

    No-op when the route is empty or every selected name is unique — the clean-corpus
    case — so a well-formed tour is never perturbed.
    """
    if len(selected) < 2:
        return list(selected), {}

    def _norm(name: str) -> str:
        return (name or "").strip().casefold()

    dropped_to_host: dict[str, str] = {}
    for i in range(len(selected)):
        a = selected[i]
        if a.id in dropped_to_host:
            continue
        for j in range(i + 1, len(selected)):
            b = selected[j]
            if b.id in dropped_to_host:
                continue
            if a.requires_dwell or b.requires_dwell:
                continue
            # A shared display name is not identity.  Once both records carry
            # canonical IDs, only equal IDs may collapse; nearby distinct
            # places remain separate.
            if (
                a.canonical_place_id is not None
                and b.canonical_place_id is not None
                and a.canonical_place_id != b.canonical_place_id
            ):
                continue
            if _norm(a.name) != _norm(b.name):
                continue
            if haversine_m(a.lat, a.lng, b.lat, b.lng) > NAME_TWIN_PROXIMITY_M:
                continue
            host, drop = _pick_twin_host(a, b)
            if drop.id in never_fold:
                # A pinned twin keeps its own seat (plan S3.6) — same rule as
                # co-located demotion above.
                continue
            if max_leg_seconds is not None and start is not None:
                _held, merged = merged_leg_seconds(
                    selected, drop, start=start, close=close, leg_seconds_fn=leg_seconds_fn
                )
                if merged > max_leg_seconds:
                    continue
            dropped_to_host[drop.id] = host.id

    if not dropped_to_host:
        return list(selected), {}

    new_selected = [p for p in selected if p.id not in dropped_to_host]
    merged: dict[str, tuple[BeatRef, ...]] = {}
    for drop_id, host_id in dropped_to_host.items():
        # Fold beats into the ULTIMATE survivor, never an intermediate host that is
        # itself dropped (a 3-way chain would otherwise orphan a twin's beats).
        survivor_id = _resolve_twin_host(host_id, dropped_to_host)
        merged[survivor_id] = merged.get(survivor_id, ()) + tuple(snapshot.beats_for(drop_id))
    return new_selected, merged


def _apply_fill_pass(
    selected: list[POI],
    candidates: list[POI],
    *,
    spine: str | None,
    interest: frozenset[str],
    snapshot: CorpusSnapshot,
    start_lat: float,
    start_lng: float,
    round_trip: bool,
    walk_budget: int,
    dwell_budget: int,
    stop_cost_fn: Callable[..., int],
    exempt_anchor_id: str | None,
    rescue_floor: int = 0,
    leg_seconds_fn: LegSecondsFn | None = None,
    score_penalty: dict[str, float] | None = None,
    party: str | None = None,
    fixed_end: tuple[float, float] | None = None,
    rescue_candidates: list[POI] | None = None,
    rescue_added_ids: list[str] | None = None,
    max_leg_seconds: int | None = None,
) -> list[POI]:
    """Phase 7: fill until the dwell floor is met or walk budget hits 95%.

    Score-first ranking (not score / cost) so we keep adding genuinely
    rich anchors at the price of a higher walk cost. Stops when:
      - delivered STANDING-STILL time
        >= ``FILL_PASS_DWELL_FLOOR_FRAC x dwell_budget``;
      - cumulative walk would exceed
        ``FILL_PASS_WALK_BUDGET_FRAC x walk_budget``;
      - no remaining candidate fits.

    Until 2026-08-06 the first of those counted narration and called it a
    "dwell-seconds proxy". It is no longer a proxy: ``stop_cost_fn`` returns
    what the visitor actually spends at a stop, so a place with a long interior
    and little to say now fills the tour, which is the whole point of the
    release. A room you stand in for forty minutes used to count for the ninety
    seconds it spoke.

    Insertions go through ``insertion_cost_seconds`` so the route stays
    geometrically sane. The post-endpoint-pull last-anchor (one-way
    routes) is preserved by clamping insertion idx to interior positions
    when the route already has ≥2 stops on a one-way path.

    #21 UNDER-FILL RESCUE: in a thin area the greedy can seat only one stop
    because a second stop's round-trip detour busts ``walk_budget`` — even
    though the tour massively under-fills the visitor's TIME (e.g. a 60-min Latin
    Quarter loop seating only Sorbonne: 6 min audio, 15 min walk). While the route
    is BELOW ``count_floor`` stops, a further stop is admitted when its MARGINAL
    walk cost is proportional to the time it earns (``extra <=
    RESCUE_MAX_WALK_PER_DWELL_SECOND x cand_dwell``): a rich nearby stop is
    seated, a far/thin walk-slog is not. A tour that ALREADY meets the floor
    returns early (below), so a full few-stop tour (e.g. the PdV golden, whose
    beat-heavy stops clear the floor) is never expanded — only the genuine
    under-fill is; and a multi-stop tour (Île, ≥ rescue_floor) is never touched.
    """
    if not selected or not candidates:
        return selected

    floor_dwell = dwell_budget * FILL_PASS_DWELL_FLOOR_FRAC
    walk_cap = int(walk_budget * FILL_PASS_WALK_BUDGET_FRAC)
    # The chain's closing point, for the per-leg cap check (S2.3): the fixed
    # end on A→B, the start on a round trip, nothing on an open end — the same
    # close every walk measurement below already uses.
    fill_close: tuple[float, float] | None
    if fixed_end is not None:
        fill_close = fixed_end
    elif round_trip:
        fill_close = (start_lat, start_lng)
    else:
        fill_close = None

    consumed_dwell = sum(stop_cost_fn(p, exempt=p.id == exempt_anchor_id) for p in selected)
    if consumed_dwell >= floor_dwell:
        return selected  # already met; no fill needed

    consumed_walk = _full_route_walk_seconds(
        selected,
        start_lat=start_lat,
        start_lng=start_lng,
        round_trip=round_trip,
        leg_seconds_fn=leg_seconds_fn,
        fixed_end=fixed_end,
    )

    selected_ids = {p.id for p in selected}
    pool = [c for c in candidates if c.id not in selected_ids]
    pool.sort(
        key=lambda c: (
            -poi_score(c, spine, interest, snapshot, penalty=score_penalty, party=party),
            c.id,
        )
    )

    def _insertion(cand: POI, sel: list[POI]) -> tuple[int, int]:
        if fixed_end is not None:
            return _insertion_cost_with_fixed_end(
                cand,
                sel,
                start_lat=start_lat,
                start_lng=start_lng,
                fixed_end=fixed_end,
                leg_seconds_fn=leg_seconds_fn,
            )
        extra, idx = insertion_cost_seconds(
            cand,
            sel,
            start_lat=start_lat,
            start_lng=start_lng,
            round_trip=round_trip,
            leg_seconds_fn=leg_seconds_fn,
        )
        # One-way with ≥2 stops: never insert after the endpoint-pulled last anchor.
        if (not round_trip) and len(sel) >= 2 and idx >= len(sel):
            idx = len(sel) - 1
            extra = insertion_extra_at_index(
                cand,
                sel,
                idx,
                start_lat=start_lat,
                start_lng=start_lng,
                round_trip=round_trip,
                leg_seconds_fn=leg_seconds_fn,
            )
        return extra, idx

    # Phase 1 — the original within-walk_cap fill (unchanged): add rich anchors
    # while there is genuine walk slack. This alone reaches the stop floor for
    # any area where a nearby second stop fits the budget.
    if consumed_walk < walk_cap:
        for cand in pool:
            if consumed_dwell >= floor_dwell:
                break
            extra, idx = _insertion(cand, selected)
            if consumed_walk + extra > walk_cap:
                continue
            # THE WORTH LINE BINDS HERE TOO (Phase 8 W8.6, Q2 11/11). Walk slack
            # is permission to walk, never a reason to: this arm used to admit
            # any candidate the budget could still afford, so a stop the rescue
            # below would refuse as a slog was bought here a few lines earlier.
            if not stop_earns_its_walk(extra, stop_cost_fn(cand, exempt=False)):
                continue
            if not _insertion_legs_fit_cap(
                cand,
                selected,
                idx,
                start=(start_lat, start_lng),
                close=fill_close,
                max_leg_seconds=max_leg_seconds,
                leg_seconds_fn=leg_seconds_fn,
            ):
                continue
            selected = [*selected[:idx], cand, *selected[idx:]]
            consumed_walk += extra
            consumed_dwell += stop_cost_fn(cand, exempt=False)

    # Phase 2 — #21 under-fill rescue: ONLY if Phase 1 could not reach the stop
    # floor (a thin area where every next stop's detour busts walk_cap). Lift to
    # RESCUE_STOP_FLOOR by seating a stop whose MARGINAL walk is proportional to
    # the audio it delivers (rich nearby stop in, far/thin walk-slog out). Runs on
    # top of Phase 1, so it never displaces a within-budget stop Phase 1 chose.
    move_ceiling = walk_budget / WALK_FRACTION if WALK_FRACTION else float("inf")
    if rescue_floor > 0 and (len(selected) < rescue_floor or consumed_dwell < floor_dwell):
        seated = {p.id for p in selected}
        rescue_pool_by_id = {cand.id: cand for cand in pool}
        for cand in rescue_candidates or ():
            rescue_pool_by_id.setdefault(cand.id, cand)
        rescue_pool = sorted(
            rescue_pool_by_id.values(),
            key=lambda cand: (
                -poi_score(cand, spine, interest, snapshot, penalty=score_penalty, party=party),
                cand.id,
            ),
        )
        for cand in rescue_pool:
            if cand.id in seated:
                continue
            if len(selected) >= rescue_floor and consumed_dwell >= floor_dwell:
                break
            # LENS FIDELITY. A landmark the lens DIMMED to vignette is kept in the
            # pool by the lens_dimmed_landmark floor so it never vanishes from the
            # tour — but it belongs as a walk-past MENTION, not a dwell stop. The
            # rescue must not promote it: asking for dark_history and being made to
            # stand in front of an off-genre POI is exactly the failure the band
            # logic exists to prevent.
            #
            # NO `party=` HERE, DELIBERATELY (plan S2.6): this call feeds a BAND
            # classification (dwell vs vignette eligibility), not a ranking —
            # "banding is not party-aware in this phase". Threading party into
            # the score above would let a family's affordance boost change
            # WHETHER a POI is eligible at all, not just how it ranks among
            # already-eligible candidates.
            if interest and not is_dwell_band(
                band_for_spotlight(
                    poi_score(cand, spine, interest, snapshot, penalty=score_penalty),
                    tier=cand.tier,
                )
            ):
                continue
            extra, idx = _insertion(cand, selected)
            if not _insertion_legs_fit_cap(
                cand,
                selected,
                idx,
                start=(start_lat, start_lng),
                close=fill_close,
                max_leg_seconds=max_leg_seconds,
                leg_seconds_fn=leg_seconds_fn,
            ):
                continue
            cand_dwell = stop_cost_fn(cand, exempt=False)
            # IS THE WALK WORTH THE STOP? Priced against what the stop EARNS the
            # visitor, not what it says. The old form divided by narration alone,
            # so a forty-minute interior carrying ninety seconds of audio was
            # classified a walk-slog and refused — which is exactly the shape of
            # stop this release exists to seat.
            #
            # THE W8.6 WORTH LINE (the panel's Q2, 11/11), through its one
            # definition. A stop that cannot earn its walk is not rescued into
            # the day — "walking is the price I pay, not part of the day I
            # bought" (Nadia).
            if not stop_earns_its_walk(extra, cand_dwell):
                continue
            # MOVE CEILING: walking + standing still is the tourist's real elapsed
            # time. Cap it at the engine's own nominal total, or filling to the
            # floor produces a route needing 67.7 min for a 60-min request.
            if consumed_walk + extra + consumed_dwell + cand_dwell > move_ceiling:
                continue
            selected = [*selected[:idx], cand, *selected[idx:]]
            consumed_walk += extra
            consumed_dwell += cand_dwell
            seated.add(cand.id)
            if rescue_added_ids is not None:
                rescue_added_ids.append(cand.id)

    return selected


def _isochrone_walk_minutes(
    duration_min: int,
    *,
    round_trip: bool,
    walk_minutes: float | None = None,
    planning_policy: RoutePlanningPolicy = DEFAULT_ROUTE_PLANNING_POLICY,
) -> int:
    """The walking-time contour REACH asks Valhalla for.

    Mirrors ``envelope_radius_m``'s derivation: the planning walk budget in minutes,
    halved for round trips (out-and-back reach).
    """
    walk_min = (
        route_planning_budget(duration_min, planning_policy).walk_envelope_minutes
        if walk_minutes is None
        else walk_minutes
    )
    asked = round(walk_min / 2.0 if round_trip else walk_min)
    # CLAMPED to what the engine will actually answer. Asking for more than
    # VALHALLA_MAX_CONTOUR_MINUTES does not get a bigger polygon — it gets
    # `{"error_code":151,"error":"Exceeded max time: 120"}` in about 4 ms, after
    # which REACH silently falls back to a plain circle with no road network and
    # loses the across-the-river protection the polygon exists to provide. Asking
    # for exactly the maximum keeps the polygon.
    return max(1, min(VALHALLA_MAX_CONTOUR_MINUTES, asked))


# A real pedestrian isochrone covers a meaningful slice of the walk envelope; a
# real Paris 30-min iso is ~5 km² (≈2x the haversine circle), while a tile-less
# Valhalla returns a degenerate ~0.03 km² near-point. Require the iso to cover at
# least this fraction of the haversine-envelope circle, else it is degenerate and
# REACH falls back to the analytic envelope. 0.25 sits ~7x below a real iso and
# ~20x above a degenerate one — a wide, safe margin.
ISO_MIN_AREA_FRACTION: float = 0.25


def _reach_predicate(
    start: tuple[float, float],
    radius_m: float,
    iso_minutes: int,
    routing_client: RoutingClient | None,
    *,
    pace_multiplier: float = 1.0,
):
    """(contains(lat, lng), degraded) — the REACH membership test (§2.1).

    Primary: point-in-polygon against the Valhalla walking isochrone
    (prepared shapely geometry; GeoJSON rings are (lng, lat)). Fallback —
    no client, isochrone refused, or unparseable geometry — is the exact
    pre-M5 haversine envelope with ``degraded=True``.

    ``pace_multiplier`` (plan S2.4) rides the isochrone request as a slower
    ``walking_speed`` — ``REACH_PACE_KMH / pace_multiplier`` — so the
    road-network polygon shrinks for a slow party exactly as the analytic
    ``radius_m`` fallback already has (``envelope_radius_m`` at the call
    site). 1.0 is the identity default: ``None`` rides through to
    ``RoutingClient.isochrone``, which keeps today's costing, byte-identical.
    """
    if routing_client is not None:
        # The keyword rides ONLY when a real pace shrink is asked for — an
        # injected legacy client (tests, doubles) need not know it at the 1.0
        # identity, the same compatibility contract `_memoized_leg_fn` keeps
        # for `costing_options_override`. A client asked for a shrunk contour
        # must accept it: dropping the shrink would report a reach the party
        # cannot walk.
        if pace_multiplier == 1.0:
            iso = routing_client.isochrone(start[0], start[1], iso_minutes)
        else:
            iso = routing_client.isochrone(
                start[0],
                start[1],
                iso_minutes,
                walking_speed_kmh=REACH_PACE_KMH / pace_multiplier,
            )
        if iso is not None:
            try:
                geoms = [
                    _shapely_shape(f["geometry"])
                    for f in iso.get("features", ())
                    if f.get("geometry")
                ]
                if geoms:
                    union = geoms[0]
                    for g in geoms[1:]:
                        union = union.union(g)
                    # Reject a DEGENERATE isochrone. Valhalla with no tiles for
                    # the region (e.g. New York on a Paris-only tileset) can't
                    # snap the origin to an edge and returns a near-point polygon
                    # (~0.03 km² vs the ~5 km² of a real 30-min walk). Trusting it
                    # collapses REACH to the handful of co-located start POIs, so
                    # the greedy seats 1 stop. Fall through to the haversine
                    # envelope when the polygon is implausibly small for the walk.
                    lng_km = 111.0 * math.cos(math.radians(start[0]))
                    iso_area_km2 = union.area * 111.0 * lng_km
                    envelope_area_km2 = math.pi * (radius_m / 1000.0) ** 2
                    if iso_area_km2 >= ISO_MIN_AREA_FRACTION * envelope_area_km2:
                        prepared = _shapely_prep(union)
                        return (
                            lambda lat, lng: bool(prepared.covers(_ShapelyPoint(lng, lat))),
                            False,
                        )
            except (KeyError, TypeError, ValueError, _ShapelyError):
                # Malformed GeoJSON, or shapely GEOS errors on degenerate/
                # self-intersecting isochrone rings (GEOSException derives
                # ShapelyError, not ValueError — pre-2026-07-02 it escaped
                # as a 500) → analytic fallback below.
                pass

    start_lat, start_lng = start
    return (
        lambda lat, lng: haversine_m(start_lat, start_lng, lat, lng) <= radius_m,
        True,
    )


#: How far (metres) from a leg's midpoint a body place may sit and still be
#: seated there — the "bounded detour" plan S2.5 asks for. 150 m is roughly
#: two minutes each way at the corpus pace: enough to reach a real nearby
#: bench or toilet, not enough to turn a rest stop into its own excursion.
BODY_STOP_MAX_DETOUR_M: float = 150.0


def _seat_body_stops(
    ordered: list[POI],
    *,
    start: tuple[float, float],
    rest_cadence_minutes: int,
    body_pool: list[POI],
    leg_seconds_fn: LegSecondsFn | None,
) -> list[POI]:
    """Seat a bench or toilet at every stretch of walking longer than the
    rest cadence — design §3.1's body-stop promise (plan S2.5): "a rest
    window with no bench under it is thirteen minutes standing on a stick".
    Nadia's toilet stop is "Ten minutes, zero cultural content, entirely
    non-negotiable" (docs/personas/03-family-with-children.md step 5).

    Runs on the FINAL walking order, after every scoring/certification pass
    has already decided the story stops — a body place is scheduled by the
    body clock, never the story ranking (POI_ROLE_MULTIPLIER prices it 0.0,
    so it cannot compete for a dwell slot on its own). A seated body stop is
    a ZERO-NARRATION stop, not a zero-DURATION one: its dwell comes from its
    own `typical_duration_min` (priced by the upload, not by beats — a body
    place carries none).

    Full promise-grade protection (a bench guaranteed to exist before the
    day is served, re-verified against the elapsed ceiling) is Phase 3's
    protected class; this is Phase 2's mechanical seating only — the walk
    total this adds is reported honestly by `summarise_route`, downstream,
    but is not re-checked against the certification band here.
    """
    if not body_pool or not ordered:
        return ordered
    cadence_seconds = rest_cadence_minutes * 60
    fn = leg_seconds_fn or default_leg_seconds
    result: list[POI] = []
    prev_lat, prev_lng = start
    accumulated = 0
    seated_ids: set[str] = set()

    for poi in ordered:
        leg_s = fn(prev_lat, prev_lng, poi.lat, poi.lng)
        if accumulated + leg_s > cadence_seconds:
            mid_lat = (prev_lat + poi.lat) / 2.0
            mid_lng = (prev_lng + poi.lng) / 2.0
            candidates_here = [b for b in body_pool if b.id not in seated_ids]
            nearest = min(
                candidates_here,
                key=lambda b: haversine_m(mid_lat, mid_lng, b.lat, b.lng),
                default=None,
            )
            if nearest is not None and (
                haversine_m(mid_lat, mid_lng, nearest.lat, nearest.lng) <= BODY_STOP_MAX_DETOUR_M
            ):
                result.append(nearest)
                seated_ids.add(nearest.id)
                accumulated = fn(nearest.lat, nearest.lng, poi.lat, poi.lng)
            else:
                # Crossed the cadence with nothing nearby to seat — reset the
                # clock anyway rather than compounding a second, longer
                # over-cadence stretch on top of the first.
                accumulated = leg_s
        else:
            accumulated += leg_s
        result.append(poi)
        prev_lat, prev_lng = poi.lat, poi.lng

    return result


def _memoized_leg_fn(
    client: RoutingClient, *, costing_options_override: dict | None = None
) -> LegSecondsFn:
    """Memoized routed leg times for the §3 divisor.

    The greedy, endpoint-pull, and fill pass re-evaluate the same coordinate
    pairs many times per request; with a live Valhalla each unique pair costs
    one HTTP roundtrip, so cache per select_route call. (The client itself
    sticky-degrades to pure math after the first transport failure.)

    ``costing_options_override`` (plan S2.7) carries the route-surface axis's
    Valhalla costing on every call this closure makes — constant for the
    whole ``select_route`` invocation, so it is safe to close over rather
    than fold into the cache key. Passed ONLY when an override is actually
    set: an injected legacy client (tests, doubles) need not know the
    keyword when no surface axis rides — the same explicit compatibility
    contract ``routing._transit`` keeps for ``route_with_receipt``-less
    clients. A client that IS asked for a surface must accept the keyword,
    because silently dropping the override would route a step-free request
    over stairs.
    """
    cache: dict[tuple[float, float, float, float], int] = {}

    def leg_fn(lat1: float, lng1: float, lat2: float, lng2: float) -> int:
        key = (lat1, lng1, lat2, lng2)
        if key not in cache:
            if costing_options_override is None:
                cache[key] = client.leg_seconds(lat1, lng1, lat2, lng2)
            else:
                cache[key] = client.leg_seconds(
                    lat1,
                    lng1,
                    lat2,
                    lng2,
                    costing_options_override=costing_options_override,
                )
        return cache[key]

    return leg_fn


def _insertion_legs_fit_cap(
    cand: POI,
    sel: list[POI],
    idx: int,
    *,
    start: tuple[float, float],
    close: tuple[float, float] | None,
    max_leg_seconds: int | None,
    leg_seconds_fn: LegSecondsFn | None,
) -> bool:
    """Would the two legs minted by inserting ``cand`` at ``idx`` fit the cap?

    THE per-leg admission check (redesign §2.4; plan S2.3), shared by the
    greedy and the fill pass so the rule cannot fork. A walking budget is not
    one number: Rosemary's twelve-minute per-leg limit is the binding
    constraint while her 54-minute total is unremarkable
    (docs/personas/05-step-free-visitor.md, breaks bullet 1). Deliberately
    LOCAL — only the two would-be legs around the priced position, O(1) per
    candidate; an O(n) whole-chain sweep per candidate per round is the
    quadratic this shape exists to avoid. The endpoint pull and the timebox
    repair check whole chains instead, because they run once per route.
    ``close`` is the fixed end for A→B, the start for a round trip, and None
    for an open end. ``max_leg_seconds`` None = axis unset = today, no check.
    """
    if max_leg_seconds is None:
        return True
    fn = leg_seconds_fn or default_leg_seconds
    prev = (sel[idx - 1].lat, sel[idx - 1].lng) if idx > 0 else start
    if fn(prev[0], prev[1], cand.lat, cand.lng) > max_leg_seconds:
        return False
    if idx < len(sel):
        nxt: tuple[float, float] | None = (sel[idx].lat, sel[idx].lng)
    else:
        nxt = close
    if nxt is None:
        return True
    return fn(cand.lat, cand.lng, nxt[0], nxt[1]) <= max_leg_seconds


def _insertion_cost_with_fixed_end(
    cand: POI,
    selected: list[POI],
    *,
    start_lat: float,
    start_lng: float,
    fixed_end: tuple[float, float],
    leg_seconds_fn: LegSecondsFn | None = None,
) -> tuple[int, int]:
    """Cheapest insertion delta in the pinned chain A→selected→B.

    Unlike the open-walk helper, B contributes to the base route even when it
    has no corpus POI.  Equal deltas keep the earliest insertion index, making
    the result independent of candidate iteration order.
    """
    best_extra = math.inf
    best_idx = 0
    for idx in range(len(selected) + 1):
        extra = insertion_extra_at_index(
            cand,
            selected,
            idx,
            start_lat=start_lat,
            start_lng=start_lng,
            round_trip=False,
            leg_seconds_fn=leg_seconds_fn,
            fixed_end=fixed_end,
        )
        if extra < best_extra:
            best_extra = extra
            best_idx = idx
    return int(best_extra), best_idx


def _full_route_walk_seconds(
    pois: list[POI],
    *,
    start_lat: float,
    start_lng: float,
    round_trip: bool,
    leg_seconds_fn: LegSecondsFn | None = None,
    fixed_end: tuple[float, float] | None = None,
) -> int:
    # Building the chain is this function's job; measuring it is not.
    return sum(
        _full_route_leg_seconds(
            pois,
            start_lat=start_lat,
            start_lng=start_lng,
            round_trip=round_trip,
            leg_seconds_fn=leg_seconds_fn,
            fixed_end=fixed_end,
        )
    )


def _full_route_leg_seconds(
    pois: list[POI],
    *,
    start_lat: float,
    start_lng: float,
    round_trip: bool,
    leg_seconds_fn: LegSecondsFn | None = None,
    fixed_end: tuple[float, float] | None = None,
) -> list[int]:
    """Every leg of a full route, separately — start, the stops, and the close."""
    coords: list[tuple[float, float]] = [(start_lat, start_lng)]
    coords.extend((p.lat, p.lng) for p in pois)
    if fixed_end is not None:
        coords.append(fixed_end)
    elif round_trip:
        coords.append((start_lat, start_lng))
    return path_leg_seconds(coords, leg_seconds_fn)


def _walk_arrivals(
    ordered: list[POI] | tuple[POI, ...],
    legs: list[int],
    *,
    clock_start: datetime | None,
    price_visit: Callable[[POI, int | None], int],
) -> list[tuple[POI, int | None, int, datetime | None]]:
    """Walk a decided order once: ``(poi, arrival_hour, visit_seconds,
    arrival_clock)`` each — the clock minute-precise, the hour its pricing
    band (Phase 4 added the clock so promise windows read off THE one
    accumulation instead of a second arithmetic).

    THE one accumulation behind the exact half of the estimate-then-exact
    contract (plan S3.3, deviation v): the greedy budgeted every stop at the
    request's start hour; anything that prices a DECIDED order — the repair's
    trials, the rescue trim, the final Route, the promise assembly — walks
    the chain instead, so a stop reached at 14:00 pays the 14:00 queue, not
    the 09:00 one. A second walk of this accumulation anywhere is the fork
    the phase forbids; `_arrival_priced_visits` and `_assemble_promises` are
    both views of this one.

    ``legs[i]`` is the walk INTO stop ``i`` (`_full_route_leg_seconds`' shape;
    a round trip's closing leg is simply unused). The cursor advances by the
    stop's own PRICED visit — narration that outruns the visit is not counted
    here, an estimate the hour-band granularity absorbs (peak bands are hours
    wide). A dateless request has no cursor and every stop prices at the
    None-hour (off-peak) band.

    On a DATED walk the cursor itself — the minute-precise arrival clock — is
    handed to ``price_visit`` as a third argument, so the one pricing closure
    can void a door this stop's own arrival window proves shut (the
    arrival-window check). A dateless walk keeps the two-argument call, so a
    legacy double never sees an argument it was not written for.
    """
    out: list[tuple[POI, int | None, int, datetime | None]] = []
    cursor = clock_start
    for leg_seconds, poi in zip(legs, ordered, strict=False):
        if cursor is not None:
            cursor += timedelta(seconds=leg_seconds)
        hour = cursor.hour if cursor is not None else None
        seconds = (
            price_visit(poi, hour, cursor) if cursor is not None else price_visit(poi, hour)
        )
        out.append((poi, hour, seconds, cursor))
        if cursor is not None:
            cursor += timedelta(seconds=seconds)
    return out


def _arrival_priced_visits(
    ordered: list[POI] | tuple[POI, ...],
    legs: list[int],
    *,
    clock_start: datetime | None,
    price_visit: Callable[[POI, int | None], int],
) -> dict[str, int]:
    """Per-stop visit seconds at each stop's ordered arrival hour — the
    dict view of `_walk_arrivals` (see there for the contract)."""
    return {
        poi.id: seconds
        for poi, _hour, seconds, _clock in _walk_arrivals(
            ordered, legs, clock_start=clock_start, price_visit=price_visit
        )
    }


@dataclass(frozen=True)
class _CertificationRouteTrial:
    selected: tuple[POI, ...]
    ordered: tuple[POI, ...]
    walk_seconds: int
    dwell_seconds: int
    #: Seconds of LINE inside this trial's dwell, at each stop's arrival hour.
    #: Queues are BUDGETED, never credited (the Phase 3 panel's convergent
    #: ruling — Camille: "the fit metric cannot tell a minute under
    #: Sainte-Chapelle's glass from a minute in a queue"): the elapsed ceiling
    #: counts them in full, but the rank's nearest-the-request preference
    #: measures elapsed MINUS this, so the day that stands in line longest can
    #: never win the ranking BECAUSE it stands in line.
    queue_seconds: int = 0
    #: The longest SINGLE walk in this route. A walking budget is not one number:
    #: Rosemary's total of 54 minutes is unremarkable and her twelve-minute
    #: per-leg limit is the binding constraint, so "a route with one 25-minute leg
    #: and one 5-minute leg has the same total and is unusable"
    #: (docs/personas/05-step-free-visitor.md). Nadia's afternoon ends early when a
    #: leg runs long, and Marcus is watching a train. Nothing in the model expresses
    #: this yet; ranking on it is the cheapest thing that stops the planner
    #: PREFERRING such a route while a better one sits in the same trial set.
    max_leg_seconds: int = 0

    @property
    def elapsed_seconds(self) -> int:
        """Walking plus standing still — the tourist's whole clock.

        Was ``walk_seconds + audio_seconds``. If the final gate prices elapsed in
        visits and the repair does not, the repair sees a route thousands of
        seconds under the floor and keeps buying enormous walks to close a gap
        the visit term already closed. That is Parc de la Villette coming back.

        Delegates the actual combining to ``served_elapsed_seconds`` (dedup-
        review 2026-08-07) — this trial's own walk/dwell terms are legitimately
        its own, but the RULE for adding them is the one rule every caller uses.
        """
        return served_elapsed_seconds(self.walk_seconds, self.dwell_seconds)


def _certification_route_trial(
    selected: list[POI],
    *,
    input: TourInput,
    snapshot: CorpusSnapshot,
    interest: frozenset[str],
    leg_seconds_fn: LegSecondsFn | None,
    planning_budget: RoutePlanningBudget,
    pulled_endpoint_id: str | None = None,
    price_visit: Callable[[POI, int | None], int] | None = None,
    clock_start: datetime | None = None,
    queue_visit: Callable[[POI, int | None], int] | None = None,
    listening_rate: float = 1.0,
) -> _CertificationRouteTrial | None:
    """Price one stop set in the exact routed/capped certification currency.

    ``pulled_endpoint_id`` MUST mirror what ``select_route`` will pin when it
    re-orders the repaired set, because certification is only meaningful if it
    measures the route the engine actually builds.  Pinning a stop last is not
    free: it forbids the cheapest tour through the same places, and on the live
    Paris flagship it cost 1003 extra seconds of walking — enough to push a route
    the repair had certified at 5403s out to 6406s, past the ceiling, where the
    final band check refused it and the traveller lost the option entirely.
    """

    if not selected and input.end is None:
        return None
    materialized = list(selected)
    fixed_end: POI | None = None
    if input.end is not None:
        materialized, fixed_end = _materialize_fixed_end_b(
            materialized, end_lat=input.end[0], end_lng=input.end[1]
        )
    elif pulled_endpoint_id is not None and not input.round_trip:
        # Same lookup select_route performs, and deliberately the same miss
        # behaviour: a trial that DROPPED the pulled endpoint pins nothing, which
        # is exactly what the engine will do with that set.
        fixed_end = next((poi for poi in materialized if poi.id == pulled_endpoint_id), None)
    ordered = order_stops(
        materialized,
        fixed_start=input.start,
        fixed_end=fixed_end,
        round_trip=input.round_trip,
        routed_cost_fn=leg_seconds_fn,
    )
    legs = _full_route_leg_seconds(
        ordered,
        start_lat=input.start[0],
        start_lng=input.start[1],
        round_trip=input.round_trip,
        leg_seconds_fn=leg_seconds_fn,
    )
    walk_seconds = sum(legs)
    # WITHOUT THIS EVERY TRIAL PRICES ITS VISITS AT ZERO. The repair would
    # then judge every candidate stop set by narration alone while the final
    # gate judges the winner by visits, and the two would disagree about the
    # same route by hours. With `price_visit` given (select_route's own call),
    # each stop prices at its ordered ARRIVAL hour — the exact half of the
    # estimate-then-exact contract (deviation v), so the repair certifies the
    # same clock the traveller is served. The bare fallback keeps every
    # injected legacy caller (tests, doubles) on the pre-promise arithmetic.
    trial_queue_seconds = 0
    if price_visit is not None:
        arrivals = _walk_arrivals(ordered, legs, clock_start=clock_start, price_visit=price_visit)
        planned = {poi.id: seconds for poi, _hour, seconds, _clock in arrivals}
        if queue_visit is not None:
            # The SAME arrival hours the visits priced — no second accumulation.
            trial_queue_seconds = sum(
                queue_visit(poi, hour) for poi, hour, _seconds, _clock in arrivals
            )
    else:
        planned = {
            poi.id: visit_seconds(
                poi,
                interest,
                snapshot,
                party_ceiling_seconds=(
                    input.max_stop_minutes * 60 if input.max_stop_minutes is not None else None
                ),
            )
            for poi in ordered
        }
    route = Route(
        pois=tuple(ordered),
        transits=(),
        total_walk_distance_m=0.0,
        total_walk_seconds=walk_seconds,
        fixed_end_poi_id=fixed_end.id if fixed_end is not None else None,
        planned_visit_seconds=planned,
    )
    dwell_seconds = served_dwell_seconds(
        route,
        snapshot,
        interest=interest,
        end_is_none=input.end is None,
        narration_density=input.narration_density,
        listening_rate=listening_rate,
    )
    return _CertificationRouteTrial(
        selected=tuple(selected),
        ordered=tuple(ordered),
        walk_seconds=walk_seconds,
        dwell_seconds=dwell_seconds,
        max_leg_seconds=max(legs, default=0),
        queue_seconds=trial_queue_seconds,
    )


def _marquee_index(
    ordered: list[POI] | tuple[POI, ...],
    snapshot: CorpusSnapshot,
    interest: frozenset[str],
) -> int:
    """The marquee stop's index: highest tier, ties → highest uncapped audio,
    then id. The same identity the C9 governor exempts at emission
    (build_poi_beat_plans_capped), restated because no Route exists at
    planning time; the loop orientation (S3.8a) and the anchor promise
    (S3.6) both read THIS one computation."""
    return max(
        range(len(ordered)),
        key=lambda i: (
            ordered[i].tier,
            planned_capped_audio_seconds(ordered[i], snapshot, interest or None, None),
            ordered[i].id,
        ),
    )


def _orient_loop(
    ordered: list[POI],
    *,
    snapshot: CorpusSnapshot,
    interest: frozenset[str],
    start: tuple[float, float],
    leg_seconds_fn: LegSecondsFn | None,
) -> list[POI]:
    """Choose a round trip's direction deliberately (plan S3.8a, deviation vi).

    A closed loop's two directions cost the same walk, so Held-Karp's pick
    between them is a tie-break accident of ids. Nadia's concentration rule
    (W1.9: "Tuesday's back-loaded anchor was right by luck; make it
    deliberate") wants the marquee LATE — the day builds toward its anchor
    and every prefix stays decent. The reversal is adopted only when the
    marquee genuinely lands later AND the reversed loop does not cost
    materially more walking (routed legs are near- but not exactly
    symmetric; the band's own materiality tolerance is the line). LOOPS
    ONLY: an open walk's order is geometry, and this function is never
    called for one (the S3.8 sabotage line).

    The marquee here is the same identity the C9 governor exempts at
    emission — highest tier, ties → highest uncapped audio — restated
    because no Route exists yet at ordering time; ids break the final tie
    so the choice never depends on list order. S3.6's anchor promise reads
    this same computation.
    """
    if len(ordered) < 2:
        return ordered
    marquee = _marquee_index(ordered, snapshot, interest)
    if len(ordered) - 1 - marquee <= marquee:
        return ordered  # the accident already landed it in the later half
    reversed_order = list(reversed(ordered))
    forward_walk = _full_route_walk_seconds(
        ordered,
        start_lat=start[0],
        start_lng=start[1],
        round_trip=True,
        leg_seconds_fn=leg_seconds_fn,
    )
    reversed_walk = _full_route_walk_seconds(
        reversed_order,
        start_lat=start[0],
        start_lng=start[1],
        round_trip=True,
        leg_seconds_fn=leg_seconds_fn,
    )
    if reversed_walk <= forward_walk + TIMEBOX_MATERIALITY_TOLERANCE_SECONDS:
        return reversed_order
    return ordered


#: The plan names at most this many promises (design §3.1: "2-5 PROMISES on a
#: clock connected by FABRIC"). A cap on the NAMED list only — capping it
#: drops no stop; a sixth pinned/rest stop is still seated and protected, it
#: is simply not headlined.
#:
#: LOWERED 5 -> 3 BY THE PHASE'S KILL CRITERION (W3.2, 2026-08-11): the
#: 180-min dated cells measured 7-47 s against the <= 11 s budget, and the
#: criterion permits exactly one trade for speed — this count — before
#: anything else. Applied with the caveat the measurement itself forced into
#: the ledger: capped and uncapped runs time IDENTICALLY (solo 32.19 s
#: uncapped vs 32.25 s re-measured), so the promise list is NOT the cost —
#: the repair's multi-pass enumeration over a demotion-widened dated pool is,
#: and that lever is named for the owner and the next phase rather than
#: silently traded here.
MAX_PROMISES: int = 3


def _assemble_promises(
    arrivals: list[tuple[POI, int | None, int, datetime | None]],
    *,
    snapshot: CorpusSnapshot,
    interest: frozenset[str],
    shape_visit: Callable[[POI, int | None], PromiseShape],
    pinned_ids: frozenset[str],
) -> tuple[Promise, ...]:
    """Name the day's 2-5 promises off the FINAL ordered walk (plan S3.6;
    design §3.1 — the plan becomes promises on a clock connected by fabric).

    ``arrivals`` is `_walk_arrivals`' output for the served order, so each
    promise's shape is priced at the hour the visitor actually stands there.
    Kinds, in the order the day earns them: every PINNED stop (the visitor's
    own certainties, §3.2), the ANCHOR (the marquee — `_marquee_index`, the
    governor-exempt identity), the FINISH (the last stop of the walk: the
    materialized B, the pulled endpoint, or a loop's deliberate late marquee
    — §3.5), and every REST (a seated body stop, §3.1). One promise per
    stop: a pinned marquee is promised as PINNED (the stronger claim). The
    list caps at MAX_PROMISES — pins first, then anchor, finish, rests — and
    capping the LIST never unseats a stop. Promises are OUTPUT — the
    planner's obligations, not knobs (the S3.2 sabotage line).

    A one-stop day yields one promise; "2-5" is the design's range for a
    normal day, not a floor a degenerate request must fake its way to.
    """
    if not arrivals:
        return ()
    ordered = [poi for poi, _hour, _seconds, _clock in arrivals]
    hour_by_id = {poi.id: hour for poi, hour, _seconds, _clock in arrivals}
    # The minute-precise arrival clock, for the arrival-window door check: a
    # promise must never say "goes inside" of a door shut at its own arrival.
    # Dateless arrivals keep the two-argument call (legacy doubles never see
    # an argument they were not written for) — the same contract
    # `_walk_arrivals` keeps.
    clock_by_id = {poi.id: clock for poi, _hour, _seconds, clock in arrivals}
    # The window, read off THE one accumulation (Phase 4, W4.2 deviation v):
    # arrival clock in, arrival + priced visit out; "" on a dateless day.
    window_by_id = {
        poi.id: (
            (clock.strftime("%H:%M"), (clock + timedelta(seconds=seconds)).strftime("%H:%M"))
            if clock is not None
            else ("", "")
        )
        for poi, _hour, seconds, clock in arrivals
    }
    marquee_id = ordered[_marquee_index(ordered, snapshot, interest)].id
    finish_id = ordered[-1].id

    candidates: list[tuple[str, str]] = []  # (kind, poi_id), priority order
    for poi in ordered:
        if poi.id in pinned_ids:
            candidates.append(("pinned", poi.id))
    candidates.append(("anchor", marquee_id))
    candidates.append(("finish", finish_id))
    for poi in ordered:
        if poi.poi_role == "body":
            candidates.append(("rest", poi.id))

    promised: dict[str, Promise] = {}
    poi_by_id = {poi.id: poi for poi in ordered}
    for kind, poi_id in candidates:
        if poi_id in promised or len(promised) >= MAX_PROMISES:
            continue
        poi = poi_by_id[poi_id]
        arrives, departs = window_by_id[poi_id]
        arrival_clock = clock_by_id[poi_id]
        promised[poi_id] = Promise(
            kind=kind,
            poi_id=poi_id,
            shape=(
                shape_visit(poi, hour_by_id[poi_id], arrival_clock)
                if arrival_clock is not None
                else shape_visit(poi, hour_by_id[poi_id])
            ),
            arrives_hhmm=arrives,
            departs_hhmm=departs,
        )
    return tuple(promised.values())


#: Greta's "another one of those" dimmer (plan S3.8b; design §4.5.5 — the
#: category-diverse replacement, row 6.7's planner consumer). A stop whose
#: place_category is shared by at least two stops AND at least half of a set
#: scores at this fraction of itself in the repair's rankings. BOUNDED, and
#: the bound is the point (§2.4:130's principle, pinned by
#: test_variety_never_beats_a_landmark): 0.8 re-orders near-ties toward
#: variety while a two-tier landmark gap (5·0.8 = 4.0 > 3) survives it, so
#: the dimmer can never trade a landmark away for the sake of a different
#: kind of place.
REPLACEMENT_CATEGORY_DIVERSITY_FACTOR: float = 0.8


def _category_dominates(category: str, counts: Counter[str], set_size: int) -> bool:
    """Whether ``category`` is "another one of those" within a set of stops:
    present at least twice AND covering at least half the set. Empty
    categories (unpassed corpus) never dominate — the safe default."""
    if not category:
        return False
    count = counts.get(category, 0)
    return count >= 2 and count * 2 >= set_size


def _diversity_weighted_score(
    pois: Iterable[POI],
    *,
    spine: str | None,
    interest: frozenset[str],
    snapshot: CorpusSnapshot,
    score_penalty: dict[str, float] | None,
    party: str | None,
) -> float:
    """Sum of ``poi_score`` over a candidate stop set with dominating
    categories dimmed (plan S3.8b): each stop whose place_category dominates
    THIS set scores at ``REPLACEMENT_CATEGORY_DIVERSITY_FACTOR``. Measured on
    the trial's own composition, so the repair's rank genuinely prefers the
    day with a different kind of place over the third museum when the two are
    otherwise equal — a preference the pool ORDER alone could never express,
    because rank decides among banked trials.
    """
    members = list(pois)
    counts = Counter(poi.place_category for poi in members if poi.place_category)
    total = 0.0
    for poi in members:
        score = poi_score(poi, spine, interest, snapshot, penalty=score_penalty, party=party)
        if _category_dominates(poi.place_category, counts, len(members)):
            score *= REPLACEMENT_CATEGORY_DIVERSITY_FACTOR
        total += score
    return total


#: Most (incumbent, candidate) exchange trials the timebox repair will price.
#: Each trial runs a full exact-or-fallback ordering plus a capped beat-plan
#: pricing, so the enumeration below is |selected| x |pool| trials. Until
#: 2026-08-04 that was bounded only by the 8-stop planning cap; with duration as
#: the sole stop bound it is not bounded at all. The pool is already score-sorted
#: and the incumbents are id-sorted, so truncating at a fixed count is
#: deterministic — the same tour is produced on every run.
TIMEBOX_REPAIR_MAX_TRIALS: int = 4000

# THE ONE UNDERFILL LINE (Phase 4 S4.5, the W4.2 panel's unanimous D-i finding;
# moved to the final band check at Phase 5 S5.3 — read exactly once, in
# `select_route`, after every pass). The band floor stays SOFT — an honestly
# short day ships with its shortfall disclosed (design §8.3; the well-liked base
# days run 62-68%) — but a best-possible day delivering under HALF the requested
# experience is "no day, mislabelled" (Aiko: "never quietly hand back a sixth of
# what I asked"; Rosemary: "never silently return less day than asked"), and it
# REFUSES with the binding constraint named instead. `open` end-hardness zeroes
# the band floor and is exempt by construction.
UNDERFILL_REFUSAL_FRACTION: float = 0.5

# "SKIP THE QUEUES" (Phase 4 dial, W4.2 — the panel retired "quieter" as a word
# and named the need: fewer people, fewer queues). Two tiers, because a score
# dim measurably LOSES to proximity (a 30 m Louvre out-ranks a dimmed rival at
# 300 m on value-per-second — found by this dial's own test): a door costing
# SERIOUS peak standing time leaves the dwell pool entirely (Théo refuses the
# 40-minute Notre-Dame stand outright; vignette walk-pasts untouched — the
# category_minus precedent), while a token-to-moderate door is dimmed through
# the one per-place score knob so a queue-free rival wins the near-tie. Both on
# top of Phase 3's queues-are-budgeted-never-credited rule.
AVOID_QUEUES_MIN_PEAK_MINUTES: int = 10
AVOID_QUEUES_EXCLUDE_PEAK_MINUTES: int = 20
AVOID_QUEUES_SCORE_FACTOR: float = 0.4

# "FEWER STOPS, LONGER AT EACH" (Phase 4 dial, W4.2 — Camille: "stop count down
# with the freed minutes flowing to the anchors"). The concentrate pass drops
# the weakest unprotected stops AFTER the repair, never below three stops,
# never below CONCENTRATE_FLOOR_FRACTION of the nominal (which keeps it above
# the 0.5 underfill refusal line — a dial must not steer the day into its own
# refusal), and at most this fraction of the seated count.
CONCENTRATE_MIN_STOPS: int = 3
CONCENTRATE_FLOOR_FRACTION: float = 0.55
CONCENTRATE_MAX_DROP_FRACTION: float = 0.4


@dataclass(frozen=True)
class StopDrop:
    """What ONE drop did — the outcome of `drop_stop`, THE one drop primitive."""

    #: The stop set without the dropped stop, in the same order.
    remaining: tuple[POI, ...]
    #: The leg minted between the dropped stop's neighbours (0 when the stop was
    #: last on an open walk and no leg was merged).
    merged_leg_seconds: int
    #: Whether that merged leg honours the per-leg cap (always True with no cap).
    merged_leg_fits: bool
    #: Visitor-time the drop freed: the stop's visit plus the walking it saved.
    freed_seconds: int
    #: survivor id -> extra visit seconds granted, strongest survivor first, each
    #: within its shape ceiling; sums to at most `freed_seconds`.
    granted: dict[str, int]


def _has_end_sentinel(stops: list[POI] | tuple[POI, ...]) -> bool:
    return any(p.id.startswith(END_B_SENTINEL_PREFIX) for p in stops)


def merged_leg_seconds(
    selected: list[POI] | tuple[POI, ...],
    drop: POI,
    *,
    start: tuple[float, float],
    close: tuple[float, float] | None,
    leg_seconds_fn: LegSecondsFn | None,
) -> tuple[int, int]:
    """(the two legs the stop holds today, the ONE leg that replaces them).

    THE merge arithmetic every drop shares (design §4.5.3): the dropped stop's
    predecessor (or the start) to its successor (or the close — the start on a
    round trip, B on A→B, nothing on an open walk, when the merged leg is 0).
    """
    fn = leg_seconds_fn or default_leg_seconds
    idx = next(i for i, p in enumerate(selected) if p.id == drop.id)
    prev = (selected[idx - 1].lat, selected[idx - 1].lng) if idx > 0 else start
    nxt = (selected[idx + 1].lat, selected[idx + 1].lng) if idx + 1 < len(selected) else close
    into = fn(prev[0], prev[1], drop.lat, drop.lng)
    if nxt is None:
        return into, 0
    out = fn(drop.lat, drop.lng, nxt[0], nxt[1])
    return into + out, fn(prev[0], prev[1], nxt[0], nxt[1])


def grant_freed_seconds(
    survivors: list[POI] | tuple[POI, ...],
    freed_seconds: int,
    *,
    visit_of: Callable[[POI], int],
    ceiling_of: Callable[[POI], int],
    protected_ids: set[str] | frozenset[str],
    receive_first: Callable[[POI], object] | None = None,
) -> dict[str, int]:
    """THE grant rule (W4.2 locked semantics 4): freed visitor-time flows to the
    survivors PROTECTED FIRST, then the strongest (tier, current visit, id), each
    up to its shape ceiling, never more than was freed. One spelling, read by the
    drop primitive and by the contingency set's skip/early entries (a skip and an
    early band free minutes without the planner dropping anything)."""
    order = receive_first or (lambda p: (-p.tier, -visit_of(p), p.id))
    ranked = sorted(survivors, key=lambda p: (0 if p.id in protected_ids else 1, order(p)))
    granted: dict[str, int] = {}
    pool = max(0, freed_seconds)
    for survivor in ranked:
        if pool <= 0:
            break
        headroom = max(0, ceiling_of(survivor) - visit_of(survivor))
        take = min(headroom, pool)
        if take > 0:
            granted[survivor.id] = take
            pool -= take
    return granted


def drop_stop(
    selected: list[POI] | tuple[POI, ...],
    drop: POI,
    *,
    start: tuple[float, float],
    close: tuple[float, float] | None,
    leg_seconds_fn: LegSecondsFn | None,
    max_leg_seconds: int | None,
    visit_of: Callable[[POI], int],
    ceiling_of: Callable[[POI], int],
    protected_ids: set[str] | frozenset[str],
    receive_first: Callable[[POI], object] | None = None,
) -> StopDrop:
    """THE ONE DROP PRIMITIVE (Phase 5 S5.5): drop a stop, MERGE its two legs,
    re-check the per-leg cap on the merged leg, and hand the freed minutes to
    the surviving protected and anchor stops within their shape ceilings.

    A dial's drop and a replan's drop are the same operation priced in the same
    currency — visitor-time, never narration-minutes (design §4.5.1) — so there
    is exactly one spelling: the "fewer stops" concentrate pass calls this, and
    the session's replans (S5.6+) call this. Three panel-forced rules live here
    and nowhere else:

    - §4.5.2 — a PROTECTED stop (a rest, meal, toilet, pin, the finish) is never
      dropped by arithmetic; it triggers the question. Asking to drop one is a
      programming error here, not a policy: ``ValueError``.
    - §4.5.3 — "dropping a stop MERGES its two legs" (Rosemary's 12 + 9 fusing
      into 21): the merged leg is priced with the SAME leg function selection uses
      and reported against the cap; a caller must not take a drop whose
      ``merged_leg_fits`` is False. Measured live 2026-08-18: co-located demotion
      folded Victor Hugo into Place des Vosges and shipped a 576 s leg under a
      540 s cap (phase5-ledger.md defect 11).
    - W4.2 locked semantics 4 — the freed minutes flow to the SURVIVORS, protected
      first and then the strongest (``receive_first`` orders them; the default is
      tier, then current visit length, then id), each up to its shape ceiling
      (``visit_time.visit_ceiling_seconds``) — never to the weakest because it has
      the most headroom (the plan's sabotage line), never past a ceiling, and never
      more than was freed, so a day is never lengthened by a drop.
    """
    if drop.id in protected_ids:
        raise ValueError(
            f"{drop.id!r} is a protected stop (design §4.5.2): a rest, meal, toilet, pin "
            "or the finish is never auto-cut — it triggers the question, not a drop"
        )
    old_legs, merged = merged_leg_seconds(
        selected, drop, start=start, close=close, leg_seconds_fn=leg_seconds_fn
    )
    fits = max_leg_seconds is None or merged <= max_leg_seconds
    remaining = tuple(p for p in selected if p.id != drop.id)
    freed = max(0, visit_of(drop) + old_legs - merged)
    granted = grant_freed_seconds(
        remaining,
        freed,
        visit_of=visit_of,
        ceiling_of=ceiling_of,
        protected_ids=protected_ids,
        receive_first=receive_first,
    )
    return StopDrop(
        remaining=remaining,
        merged_leg_seconds=merged,
        merged_leg_fits=fits,
        freed_seconds=freed,
        granted=granted,
    )


#: How many times the repair may RE-RUN itself on its best over-ceiling set
#: when no single move lands in-band. One enumeration can shed at most ONE
#: stop (drops and exchanges both remove one incumbent), which was enough
#: while overshoots were a stop's worth — but arrival-hour queue re-pricing
#: (plan S3.3, deviation v) can move a day by SEVERAL stops' worth at once
#: (measured at W3.2: 45 min over on a 180-min request), and a repair that
#: cannot shed two stops answers that with a refusal. Each pass starts from
#: the previous best's strictly different set, so the recursion is bounded
#: twice over: by this cap and by the shrinking set.
TIMEBOX_REPAIR_MAX_PASSES: int = 4


def interior_did_not_fit_reason(duration_min: int) -> str:
    """THE sentence a stepped-outside stop carries, in one place.

    Written for a person and checkable by a stranger, like every other
    `ClockExclusion.reason` (W4.2 panel, Paulo's wording rulings). It takes the
    after-dusk disclosure's shape, not the closed door's: this is a note about a
    stop that STAYED, so the planner's sentence is the whole story, with no
    second half for a reader to compose.

    `kept_outside` stays FALSE for the same reason, and it is load-bearing rather
    than a nicety. That flag means the CLOCK voided a door and the facade was
    kept, and every reader draws the word "closed" from it — the harness's
    per-stop column renders `closed — outside only`. Notre-Dame is not closed;
    the day simply has no room to go in. A decision belongs in a field (W4.12),
    and this is a different decision, so it must not borrow that field.

    On the wire and in the harness alike this reads as "Notre-Dame Cathedral —
    its interior does not fit the 60 minutes you asked for, so we will see it
    from the outside", and the per-stop table prints the true `25 min out`.
    """
    return (
        f"its interior does not fit the {duration_min} minutes you asked for, "
        "so we will see it from the outside"
    )


def _apply_certification_timebox_repair(
    selected: list[POI],
    candidates: list[POI],
    *,
    input: TourInput,
    snapshot: CorpusSnapshot,
    spine: str | None,
    interest: frozenset[str],
    score_penalty: dict[str, float] | None,
    leg_seconds_fn: LegSecondsFn | None,
    planning_policy: RoutePlanningPolicy,
    planning_budget: RoutePlanningBudget,
    pulled_endpoint_id: str | None = None,
    price_visit: Callable[[POI, int | None], int] | None = None,
    clock_start: datetime | None = None,
    protected_promise_ids: set[str] | None = None,
    queue_visit: Callable[[POI, int | None], int] | None = None,
    report_overrun: bool = False,
    listening_rate: float = 1.0,
    exterior_only_ids: set[str] | None = None,
    shape_visit: Callable[[POI, int | None], PromiseShape] | None = None,
    _pass_number: int = 1,
) -> list[POI]:
    """Bounded add/exchange/drop/step-outside repair for one frozen policy.

    ``report_overrun`` (Phase 5 S5.6, a ReplanContext's remainder): when nothing
    banks, hand back the best over-ceiling set instead of refusing — the final
    gate REPORTS the overrun on the route (W5.2 R1.1: a tail is never a 422).

    Only the already-eligible ordinary candidate pool is considered, so the
    pass cannot waive lens, role, REACH, or fixed-B corridor rules.  Each trial
    is exactly ordered and priced with routed walk plus the same whole-beat cap
    emission uses.  Structural glue and observations are deliberately absent:
    neither is a legitimate source of duration padding.

    ``protected_promise_ids`` (plan S3.6; design §4.5.2/§4.5.4 — the terminus
    is not the shock absorber) are incumbents no DROP may remove: the pulled
    endpoint (whose selection may encode a rule the rank cannot see — the
    dusk-preferred lit finisher was measurably traded back for a richer dark
    one before this existed), every pinned stop, and the stop carrying a
    fixed destination B (which this function also derives itself, below).
    Exchanges and adds still consider every trial; only removal of a promise
    is refused.

    THE FOURTH MOVE — "STEP OUTSIDE" (``exterior_only_ids`` + ``shape_visit``).
    Add, drop and exchange all speak through the returned stop list, so this
    function could only ever answer "which places", never "how much of this
    place". That silence is what made a fixed-end day refusable by arithmetic no
    move could touch: the destination is PROTECTED from every drop, so its price
    is a floor — Notre-Dame's fifty-minute interior plus its line is 65 minutes
    of a 60-minute request before a step is walked, and the day was refused with
    "every route reachable from this start overruns the requested duration".
    The engine already knew how to say "you will not have time to go in, but the
    place is still worth standing at" — that is `visit_shape`'s collapse, which a
    locked door, a `wall` end and a party ceiling each trigger — it simply had no
    way to be told so by the DURATION the visitor asked for. This set is that
    way: the repair writes a stop into it and re-runs its own enumeration with
    the stop priced exterior-only through that same collapse.

    It is a LAST RESORT, and the ordering is the whole guarantee: a day that fits
    with everyone's interior intact must never lose an interior, so this runs
    only after every add, drop, exchange and repair pass has failed to put ANY
    day under the ceiling — and only where the alternative is a REFUSAL, which is
    why a ``report_overrun`` remainder returns before reaching it. It applies to
    the protected destination too — that is
    the point, since the destination is the one stop no other move can reach.
    ``select_route`` reads the set afterwards and discloses each stepped stop on
    the day's own notes channel; a silently thinner day is forbidden (W8.2 R8).
    """

    preferred_trials: list[_CertificationRouteTrial] = []
    last_resort_trials: list[_CertificationRouteTrial] = []
    #: Every trial that fits UNDER the ceiling, whether or not it reaches the
    #: floor. The floor is soft (disclose and ship short); the ceiling is hard.
    under_ceiling_trials: list[_CertificationRouteTrial] = []
    #: The lowest-elapsed trial the CEILING rejected — the seed for another
    #: repair pass when nothing fits (TIMEBOX_REPAIR_MAX_PASSES).
    best_over: list[_CertificationRouteTrial] = []
    observed: list[int] = []
    # STRICT-IMPROVEMENT PRECONDITION. When the incumbent ALREADY satisfies the
    # band, the repair has nothing to repair, and every remaining move is a
    # discretionary swap judged by ``rank`` — which prices only distance from the
    # nominal duration and never prices WALKING. Left ungoverned it will trade a
    # compliant route for one that walks materially further to land a few seconds
    # nearer the nominal, which is a pure downgrade for the tourist: more walking,
    # no more narration. (The pressure to do so is systemic, not incidental: the
    # planner books each stop against the per-tour narration allowance while
    # emission caps every stop at MAX_DWELL_AUDIO_SECONDS, so routes routinely
    # arrive here short of their target duration and the only currency the repair
    # can pay in is distance.) So an in-band incumbent may only be displaced by a
    # trial that does not materially increase walking. The threshold is the SAME
    # materiality tolerance the band itself uses: seconds too few to change what
    # the tourist experiences are not worth defending, and inventing a second
    # tolerance would mean two different definitions of "material" in one function.
    #
    # Both of these are re-derived by ``enumerate_moves`` on every round, because
    # the fourth move re-prices the incumbent: a day whose destination has stepped
    # outside is a different base with a different band answer.
    base: _CertificationRouteTrial | None = None
    incumbent_in_band = False

    def record(
        trial: _CertificationRouteTrial | None,
        *,
        added: POI | None = None,
        reference_walk_seconds: int | None = None,
    ) -> None:
        """Bank one ALREADY-PRICED stop set as a possible answer.

        Split out of ``consider`` so a trial the enumeration below has already
        paid for can be offered as a solution without being priced twice.
        """
        if trial is None or len(observed) >= TIMEBOX_REPAIR_MAX_TRIALS:
            return
        observed.append(trial.elapsed_seconds)
        # THE CEILING IS THE REQUEST ITSELF. It was ``maximum + tolerance`` — 1.10
        # of the request plus a minute — so a 300-minute request could bank a
        # 331-minute tour, and the repair would then buy walking to reach it.
        # Duration is a ceiling, not a target: ask for five hours and the tour is
        # at most five hours. Marcus has to be on a platform at 16:40, and ten
        # per cent over is a missed train
        # (docs/personas/04-layover-sprinter.md).
        if trial.elapsed_seconds > planning_budget.nominal_elapsed_seconds:
            if not best_over or trial.elapsed_seconds < best_over[0].elapsed_seconds:
                best_over[:] = [trial]
            return
        # THE PER-LEG CAP IS AN ADMISSION RULE HERE TOO (plan S2.3). The greedy
        # cap-checked its own insertions, but a repair trial re-orders the whole
        # set, which can mint a brand-new over-cap leg — and these trials LOOK
        # leg-aware because rank() reads max_leg_seconds, but rank only ranks.
        # Same filter shape as the ceiling above: over the cap = ineligible in
        # every list, because under the axis a route with one over-cap leg "has
        # the same total and is unusable" (05-step-free-visitor.md, bullet 1).
        if input.max_leg_minutes is not None and trial.max_leg_seconds > input.max_leg_minutes * 60:
            return
        # THE WALKING BUDGET IS AN ADMISSION RULE HERE TOO — on days whose
        # walking is DISCRETIONARY (Phase 6 S6.6). The repair's only currency is
        # distance (the strict-improvement note above records the systemic
        # pressure), and the three-minute tight (R3) makes routes arrive short
        # more often — so the repair bought the band with walking: a 60-minute
        # one-way day walked 40 s past its budget, a 105-minute one 145 s
        # (measured 2026-08-19, the sweep's open one-way cells +
        # test_select_route_respects_walk_budget; greedy, pull and fill all obey
        # their own walk checks — this was the one door left open). A trial may
        # never walk past the budget — or past the incumbent, when the incumbent
        # is already over it (a replan tail's way home can exceed the walking
        # fraction by construction; the repair may then only hold or shrink the
        # walking, never grow it). FIXED-B DAYS ARE EXEMPT: the corridor to B is
        # MANDATORY walking, priced by the elapsed ceiling, the per-leg cap and
        # the walk-per-dwell slog ratio — a flat walking bound there refuses the
        # very adds the corridor repair exists to seat (measured on this suite:
        # the 12-minute A->B fixture's museum detour is the PRODUCT, and the
        # thin-corridor day refused outright instead of shipping short).
        if input.end is None:
            walk_ceiling = max(
                planning_budget.walk_budget_seconds,
                base.walk_seconds if base is not None else 0,
            )
            if trial.walk_seconds > walk_ceiling:
                return
        # Bank it as a fallback BEFORE the band test. A trial that fits under the
        # ceiling but cannot reach the floor is an honestly short tour, and an
        # honestly short tour is a better product than a refusal — it is also the
        # last door through which the reported bug could return, since a planner
        # that must reach the floor will walk you to Parc de la Villette to do it.
        under_ceiling_trials.append(trial)
        ratio_exceeded = False
        if added is not None and reference_walk_seconds is not None:
            # Price the candidate against the route *without that candidate*.
            # For an exchange, comparing against the original incumbent route
            # can hide a pure walk-slog whenever the removed incumbent was an
            # even larger detour.
            marginal_walk = max(0, trial.walk_seconds - reference_walk_seconds)
            # THE PRODUCER OF THE TWO LISTS, and it has to move with the consumer
            # below or the guard keeps demoting exactly the stops this release
            # exists to seat. Priced in narration, a forty-five-minute interior
            # carrying ninety seconds of audio rates its walk at 30x and is filed
            # as a last-resort slog. Priced in what the visitor gets, the same
            # stop rates under 1x and is preferred — which is correct, because
            # walking twenty minutes for forty-five minutes inside the
            # Conciergerie is the best trade in Theo's afternoon. The added
            # stop's arrival hour is unknown before its trial orders it, so
            # the slog ratio prices at the window's MIDPOINT hour — the same
            # estimate the greedy budgets with (deviation v, W3.2 amendment).
            if price_visit is not None:
                added_visit = price_visit(
                    added,
                    (
                        (clock_start + timedelta(minutes=input.duration_min // 2)).hour
                        if clock_start is not None
                        else None
                    ),
                )
            else:
                added_visit = visit_seconds(
                    added,
                    interest,
                    snapshot,
                    party_ceiling_seconds=(
                        input.max_stop_minutes * 60 if input.max_stop_minutes is not None else None
                    ),
                )
            added_dwell = stop_seconds(
                added_visit,
                planned_capped_audio_seconds(
                    added,
                    snapshot,
                    interest or None,
                    MAX_DWELL_AUDIO_SECONDS,
                ),
            )
            # THE SAME WORTH LINE the fill pass applies, through its one
            # definition (W8.6 Q2, 11/11): a trial that buys its stop with more
            # walking than the stop earns — or with more than the absolute
            # pure-detour ceiling — is a last resort, never a preferred repair.
            ratio_exceeded = not stop_earns_its_walk(marginal_walk, added_dwell)
        if within_planning_timebox(trial.elapsed_seconds, planning_budget):
            if (
                incumbent_in_band
                and base is not None
                and trial.walk_seconds > base.walk_seconds + TIMEBOX_MATERIALITY_TOLERANCE_SECONDS
            ):
                return
            target = last_resort_trials if ratio_exceeded else preferred_trials
            target.append(trial)

    def consider(
        trial_selected: list[POI],
        *,
        added: POI | None = None,
        reference_walk_seconds: int | None = None,
    ) -> None:
        if len(observed) >= TIMEBOX_REPAIR_MAX_TRIALS:
            return
        record(
            _certification_route_trial(
                trial_selected,
                input=input,
                snapshot=snapshot,
                interest=interest,
                leg_seconds_fn=leg_seconds_fn,
                planning_budget=planning_budget,
                pulled_endpoint_id=pulled_endpoint_id,
                price_visit=price_visit,
                clock_start=clock_start,
                queue_visit=queue_visit,
                listening_rate=listening_rate,
            ),
            added=added,
            reference_walk_seconds=reference_walk_seconds,
        )

    selected_ids = {poi.id for poi in selected}
    # THE PROTECTED SET a DROP must never remove (plan S3.6 generalising the
    # old single protected_end_id). Always includes the stop currently
    # carrying a fixed destination B: removing it does not lose B (each trial
    # re-materializes it), but it re-materializes as a contentless sentinel at
    # B's coordinate — trading a narrated stop for a bare pin while walking
    # exactly as far.
    protected = set(protected_promise_ids or ())
    if input.end is not None and selected:
        _, materialized_end = _materialize_fixed_end_b(
            selected, end_lat=input.end[0], end_lng=input.end[1]
        )
        protected.add(materialized_end.id)
    # Pool enumeration order carries Greta's dimmer too (plan S3.8b): under
    # the trial cap, the different-kind candidate should be PRICED before the
    # third museum, not merely preferred if both happen to be priced. The
    # decision itself lives in `rank`'s diversity-weighted score.
    seated_category_counts = Counter(poi.place_category for poi in selected if poi.place_category)

    def _pool_rank_score(candidate: POI) -> float:
        score = poi_score(
            candidate, spine, interest, snapshot, penalty=score_penalty, party=input.party
        )
        would_be = seated_category_counts.copy()
        if candidate.place_category:
            would_be[candidate.place_category] += 1
        if _category_dominates(candidate.place_category, would_be, len(selected) + 1):
            score *= REPLACEMENT_CATEGORY_DIVERSITY_FACTOR
        return score

    pool = sorted(
        (candidate for candidate in candidates if candidate.id not in selected_ids),
        key=lambda poi: (-_pool_rank_score(poi), poi.id),
    )
    def enumerate_moves() -> None:
        """Price the incumbent and every ADD, DROP and EXCHANGE around it.

        A function rather than a straight-line block because the fourth move
        re-runs exactly this enumeration with one stop priced exterior-only —
        the adds matter there as much as anywhere (the day that fits is
        Notre-Dame's facade PLUS the bridge the visitor is standing on), and a
        second copy of the enumeration written for that round is the fork this
        module exists to avoid.
        """
        nonlocal base, incumbent_in_band
        base = _certification_route_trial(
            selected,
            input=input,
            snapshot=snapshot,
            interest=interest,
            leg_seconds_fn=leg_seconds_fn,
            planning_budget=planning_budget,
            pulled_endpoint_id=pulled_endpoint_id,
            price_visit=price_visit,
            clock_start=clock_start,
            queue_visit=queue_visit,
            listening_rate=listening_rate,
        )
        incumbent_in_band = base is not None and within_planning_timebox(
            base.elapsed_seconds, planning_budget
        )
        # ``base`` is the incumbent priced just above; banking it directly is the
        # same trial ``consider(list(selected))`` would recompute.
        record(base)
        if base is not None:
            for candidate in pool:
                consider(
                    [*selected, candidate],
                    added=candidate,
                    reference_walk_seconds=base.walk_seconds,
                )
        for incumbent in sorted(selected, key=lambda poi: poi.id):
            if incumbent.id in protected:
                # A promise is un-removable through EITHER door: not by the pure
                # DROP below, and not by an EXCHANGE that swaps it for a pool
                # candidate — an exchange removes the incumbent just the same,
                # which is exactly how the dusk-preferred lit finisher was traded
                # back for a richer dark one before this guard covered both.
                continue
            retained = [poi for poi in selected if poi.id != incumbent.id]
            retained_trial = _certification_route_trial(
                retained,
                input=input,
                snapshot=snapshot,
                interest=interest,
                leg_seconds_fn=leg_seconds_fn,
                planning_budget=planning_budget,
                pulled_endpoint_id=pulled_endpoint_id,
                price_visit=price_visit,
                clock_start=clock_start,
                queue_visit=queue_visit,
                listening_rate=listening_rate,
            )
            reference_walk_seconds = (
                retained_trial.walk_seconds if retained_trial is not None else 0
            )
            # The DROP itself is a solution, not merely a pricing reference for the
            # exchanges below. Every other move here holds the stop count (exchange)
            # or raises it (add), so without this a route that overshoots the ceiling
            # has NO move that shortens it and the whole option is refused — the
            # 2026-08-04 collapse to a single walk. Stop ceilings used to make the
            # overshoot impossible; with duration as the only stop bound it is
            # routine. A removal cannot be a walk-slog (it adds no walk and no
            # audio), so it is never a last resort — and it still has to beat every
            # other in-band trial on ``rank`` to be chosen, where the score tie-break
            # favours the route that kept more stops.
            if retained and incumbent.id not in protected:
                record(retained_trial)
            for candidate in pool:
                consider(
                    [*retained, candidate],
                    added=candidate,
                    reference_walk_seconds=reference_walk_seconds,
                )

    def _storied_rank(
        trial: _CertificationRouteTrial,
    ) -> tuple[float, float, int, tuple[str, ...]]:
        """How good a day is when no day can reach the floor — bigger is better.

        THE ONE such ordering, because two places choose among under-ceiling
        days: the branch below, and the fourth move deciding which stop should
        step outside. A second spelling of "the most storied day" would let the
        two disagree about the same pair of days.

        THE MOST STORIED DAY, NOT THE LONGEST WALK (Phase 8 W8.6, the panel's
        Q1 ruling, 11/11; design §8.3's deleted fill-the-requested-time and
        §4.5.1's price-in-visitor-time).

        This key read ELAPSED — walking plus standing — so a day was "longer"
        for having walked further, and the pick padded with pavement. Measured
        live on the PdV golden (2026-08-24, evidence/phase8-gates/w86-*): a
        60-minute round trip starting in the MIDDLE of Place des Vosges (tier
        5, 64 beats, 30 minutes of standing) served ONE stop — Place de la
        Bastille (tier 3, 15 beats), 12 minutes' walk away — and never said a
        word about the square the walker was standing in, because 1200 s of
        standing + 1436 s of walking beat 1800 s of standing at the best
        place. The Panthéon 120-minute loop is the same shape (43 minutes of
        walking to a bookshop, its own INV8 gate rating the detour 60.7x).

        The panel, all eleven, ruled the arithmetic backwards. Théo: "padding
        the length with pavement is a walk filed as dwell." Camille: "length
        made of walking is not length." Nadia: "walking is the price I pay, at
        half speed, often with a five-year-old on my hip." Rosemary: "the
        square I am standing in, told honestly short, beats any day padded
        longer with my steps." So the currency here is STANDING STILL — the
        day's own places — and walking is what it costs, never what it is
        worth.

        Queue seconds still spend the budget and still never count as the day
        (unchanged, the same rule rank's fit term keeps). Ties break on the
        diversity-weighted score, then on ids, so the choice stays
        deterministic exactly as `rank` is. The CEILING is untouched: every
        candidate compared here already fits, and a day is never lengthened by
        this.
        """
        return (
            # THE BEST PLACES FIRST — the day's own story value, which is what
            # "storied" means to the engine: tier x richness x lens fit x
            # alignment, summed over the day's stops.
            #
            # UNDIMMED HERE, AND THAT IS THE SECOND MEASURED CORRECTION.
            # Ranking by the DIVERSITY-WEIGHTED score made this key prefer a
            # strictly SMALLER day: Greta's dimmer fires when a category covers
            # half a set, so a two-museum day dims BOTH its stops to 0.8 while
            # a one-museum day keeps full marks, and 0.8 x (Orangerie + Orsay)
            # came out under Orsay alone. Measured live on Rosemary's own
            # request (Orsay round trip, take-it-easy, 13-minute cap): the day
            # collapsed from [Musee de l'Orangerie, Musee d'Orsay] with 19
            # minutes of walking to [Musee d'Orsay] with none — and with the
            # walking went the cadence crossing that seats her BENCH, the one
            # stop her whole persona is about (`test_a_day_with_a_rest_
            # generates_on_the_app_path` caught it).
            #
            # A "most storied day" measure must be MONOTONE: a day that keeps
            # everything another day has, plus one more real place, is never
            # the poorer day. The plain sum is monotone; the dimmed sum is not.
            # Greta's rule keeps its job as the TIE-BREAK below — choosing
            # between days of equal story value, which is what "between two
            # otherwise-equal repairs" meant when it was written — and never
            # decides that fewer places is more storied.
            sum(
                poi_score(
                    poi, spine, interest, snapshot,
                    penalty=score_penalty, party=input.party,
                )
                for poi in trial.selected
            ),
            # Greta's dimmer, among days the sum above cannot separate.
            # MEASURED CORRECTION, 2026-08-24: this key first read
            # `dwell - queue`, the LONGEST STANDING TIME — and that is option
            # (b) of the panel's own Q1, which every one of the eleven
            # REJECTED in favour of (a), the most storied day. Camille said
            # exactly why: "maximised standing time still picks quantity over
            # the best places, and could still skip the square I am in." The
            # golden proved her right within the hour: on the Place des Vosges
            # 60-minute round trip the dwell key picked Musee Victor Hugo —
            # tier 4, TWO beats, a 35-minute visit — over Place des Vosges
            # itself, tier 5 with SIXTY-FOUR stories and a 30-minute visit.
            # A longer stand at a quieter place is not a better day.
            _diversity_weighted_score(
                trial.selected,
                spine=spine,
                interest=interest,
                snapshot=snapshot,
                score_penalty=score_penalty,
                party=input.party,
            ),
            # Between days of equal story value, the one that gives more time
            # AT those places wins — standing still, minus the minutes spent
            # in line for it (queues are budgeted, never credited).
            trial.dwell_seconds - trial.queue_seconds,
            tuple(sorted(poi.id for poi in trial.selected)),
        )

    def best_under_ceiling() -> list[POI]:
        # UNDER-FILLED. Nothing reaches the floor, but something fits under the
        # ceiling. The floor stays SOFT for honest near-misses (design §8.3:
        # duration is a ceiling, not a contract to fill; the gate downstream
        # measures the shortfall and attaches the disclosure). Hand back the
        # most storied day under the ceiling and let THE ONE UNDERFILL LINE at
        # the final band check decide (Phase 5 S5.3): the W4.2 panel's unanimous
        # worst finding (D-i) — a best-possible day under HALF the ask is no day
        # — used to be enforced HERE, against THIS budget, and this budget is the
        # repair's: a rest cadence has already shrunk it by its rest reserve, so
        # the "half" line sat at ~63 minutes of a 180-minute ask and every pass
        # after the repair ran downstream of it (W4.11's carried composition hole,
        # re-measured at W5.1: 68 of 180 shipped). The line now lives once, after
        # every pass, on the request's own budget.
        return list(max(under_ceiling_trials, key=_storied_rank).selected)

    def rank(
        trial: _CertificationRouteTrial,
    ) -> tuple[int, float, float, tuple[str, ...]]:
        # Diversity-weighted (plan S3.8b): a trial's score dims its own
        # dominating categories, so between two otherwise-equal repairs the
        # one whose day is not "three museums" wins — Greta's rule reaching
        # the DECISION, not just the enumeration order.
        score = _diversity_weighted_score(
            trial.selected,
            spine=spine,
            interest=interest,
            snapshot=snapshot,
            score_penalty=score_penalty,
            party=input.party,
        )
        return (
            # THE LONGEST SINGLE WALK COMES FIRST, in ten-minute bands.
            #
            # A walking budget is not one number. Nothing in this engine could say
            # so until now: the total was capped and the worst leg was free, so a
            # route with one fifty-minute march could out-rank a route of five
            # short ones that filled the same clock. Rosemary stops every twelve
            # minutes and "a route with one 25-minute leg and one 5-minute leg has
            # the same total and is unusable"
            # (docs/personas/05-step-free-visitor.md); Nadia's afternoon simply
            # ends when a leg runs long.
            #
            # BANDED, not exact, and that is the point: shaving forty seconds off
            # the worst leg is not worth losing a better set of places for, so
            # within a band the older preferences decide exactly as before. This
            # only fires when one route asks for a materially longer unbroken walk
            # than another.
            trial.max_leg_seconds // 600,
            # NEAREST THE REQUEST, MEASURED IN EXPERIENCE — elapsed minus queue
            # (the Phase 3 panel's convergent ruling, Camille's words: "the fit
            # metric cannot tell a minute under Sainte-Chapelle's glass from a
            # minute in a queue"). The ceiling banked the FULL elapsed above, so
            # a queue still spends the budget; it just never EARNS rank — the
            # first W3.2 corpus runs measurably promoted a 40-minute Louvre line
            # to "best fit" over queue-free multi-stop days because lines were
            # the cheapest way to fill a 180-minute bucket.
            abs(
                planning_budget.nominal_elapsed_seconds
                - (trial.elapsed_seconds - trial.queue_seconds)
            ),
            -score,
            tuple(sorted(poi.id for poi in trial.selected)),
        )

    def step_outside_repair() -> list[POI] | None:
        """THE FOURTH MOVE, and the LAST one tried: keep a stop, drop its door.

        Reached only when no add, no drop, no exchange and no further repair pass
        could put ANY day under the ceiling — so by the time this runs it is
        already established that no day exists with every interior intact. That
        ordering is the guarantee, and it is what the test named
        ``test_a_day_that_fits_with_the_interior_never_steps_outside`` pins.

        EVERY incumbent the visitor would have gone INSIDE is offered, and the
        BEST day wins — through ``rank`` and ``_storied_rank``, the two orderings
        this repair already decides everything else by. Taking the first offer
        that yielded anything handed the choice to the alphabet: measured on a
        pinned hall and a destination hall, stepping outside the stop whose id
        sorted first left a day a minute and a half SHORT of the band and cost
        the visitor a thirty-two-minute interior, while the other offer's day sat
        squarely inside it. A day in the band beats a merely-under-ceiling one,
        exactly as it does in the main body below; inside each kind the ranking
        decides. Offers are built in ascending-id order and ``min``/``max`` keep
        the first extreme, so the choice stays deterministic.

        Each offer prices its own enumeration on its OWN trial budget — the banks
        AND the counter are cleared for it, and restored afterwards. They used to
        share the counter with the first enumeration, so a repair that had
        already spent ``TIMEBOX_REPAIR_MAX_TRIALS`` offered every stop and priced
        none of them: the move silently never ran and the traveller got the
        refusal back instead of the day. (Measured on a 400-minute request over a
        214-place pool, round one spends the whole cap.) The work this buys is
        real — at most one enumeration per interior stop — and it is only ever
        spent on a day whose alternative is a REFUSAL. A replanned remainder is
        not such a day: it hands its overrun back for the person to answer, and
        the caller returns before this is ever called.

        A stop that yields nothing is put back, so a failed offer leaves no trace
        on the pricing. The PROTECTED destination is offered like any other — it
        is the stop no drop can reach, which is exactly why this move exists.

        ``None`` means nothing was gained and the caller refuses as before.
        """
        if exterior_only_ids is None or shape_visit is None:
            return None
        # The window's MIDPOINT hour, the same estimate the slog ratio above uses:
        # a stop's true arrival hour is only known once a trial has ordered it.
        estimate_hour = (
            (clock_start + timedelta(minutes=input.duration_min // 2)).hour
            if clock_start is not None
            else None
        )
        # A FAILED OFFER LEAVES NO TRACE. The refusal below reads `observed` and
        # `best_over` to say WHICH days were found and how far off they were, and
        # that sentence must describe the days the visitor could have had with
        # every interior intact — not the exterior-priced trials this move tried
        # and could not use. So the banks are restored whole, exactly as the
        # pricing channel itself is.
        was = (
            list(observed),
            list(best_over),
            list(preferred_trials),
            list(last_resort_trials),
            list(under_ceiling_trials),
        )
        in_band: list[tuple[tuple, str, list[POI]]] = []
        short_of_band: list[tuple[_CertificationRouteTrial, str]] = []
        for incumbent in sorted(selected, key=lambda poi: poi.id):
            if incumbent.id in exterior_only_ids:
                continue
            if not shape_visit(incumbent, estimate_hour).goes_inside:
                continue  # nothing to step out of
            exterior_only_ids.add(incumbent.id)
            observed.clear()
            preferred_trials.clear()
            last_resort_trials.clear()
            under_ceiling_trials.clear()
            best_over.clear()
            enumerate_moves()
            stepped = preferred_trials or last_resort_trials
            if stepped:
                banked = min(stepped, key=rank)
                in_band.append((rank(banked), incumbent.id, list(banked.selected)))
            elif under_ceiling_trials:
                short_of_band.append(
                    (max(under_ceiling_trials, key=_storied_rank), incumbent.id)
                )
            exterior_only_ids.discard(incumbent.id)
        (
            observed[:],
            best_over[:],
            preferred_trials[:],
            last_resort_trials[:],
            under_ceiling_trials[:],
        ) = was
        if in_band:
            _key, stepped_id, stops = min(in_band, key=lambda offer: offer[0])
        elif short_of_band:
            banked, stepped_id = max(
                short_of_band, key=lambda offer: _storied_rank(offer[0])
            )
            stops = list(banked.selected)
        else:
            return None
        exterior_only_ids.add(stepped_id)
        return stops

    enumerate_moves()

    eligible_trials = preferred_trials or last_resort_trials
    if not eligible_trials and under_ceiling_trials:
        return best_under_ceiling()
    if not eligible_trials:
        # EVERY trial overshoots the request (an under-ceiling trial would have
        # taken the branch above; a leg-cap-rejected one never banks). One
        # enumeration can shed at most one stop, and an arrival-hour queue
        # re-pricing can move the day by several stops' worth at once — so
        # before refusing, run another pass from the best over-ceiling set this
        # one found (strictly different, or there is nothing new to try).
        # Bounded by TIMEBOX_REPAIR_MAX_PASSES.
        if (
            best_over
            and _pass_number < TIMEBOX_REPAIR_MAX_PASSES
            and {poi.id for poi in best_over[0].selected} != selected_ids
        ):
            try:
                return _apply_certification_timebox_repair(
                    list(best_over[0].selected),
                    candidates,
                    input=input,
                    snapshot=snapshot,
                    spine=spine,
                    interest=interest,
                    score_penalty=score_penalty,
                    leg_seconds_fn=leg_seconds_fn,
                    planning_policy=planning_policy,
                    planning_budget=planning_budget,
                    pulled_endpoint_id=pulled_endpoint_id,
                    price_visit=price_visit,
                    clock_start=clock_start,
                    protected_promise_ids=protected_promise_ids,
                    queue_visit=queue_visit,
                    report_overrun=report_overrun,
                    listening_rate=listening_rate,
                    exterior_only_ids=exterior_only_ids,
                    shape_visit=shape_visit,
                    _pass_number=_pass_number + 1,
                )
            except CertificationPlanningInfeasibleError:
                # Every deeper drop pass failed too — including their own fourth
                # moves. THIS pass's stop set has not been offered one yet, and
                # dropping stops is always tried before dropping an interior, so
                # the move gets its turn here. If it finds nothing either, the
                # DEEPER refusal is re-raised unchanged: it counted the days that
                # search actually priced, and a refusal must keep saying exactly
                # what it said before this move existed.
                stepped_after_deeper = step_outside_repair()
                if stepped_after_deeper is not None:
                    return stepped_after_deeper
                raise
        if report_overrun:
            # A TAIL NEVER TRADES AN INTERIOR AWAY, and it is reached BEFORE the
            # fourth move because the fourth move's own precondition is a REFUSAL.
            # `report_overrun` is a replanned REMAINDER (W5.2 R1.1/R2): it never
            # refuses — the best over-ceiling day comes back with its overrun on it,
            # and that number is the raw material of the ONE question the person
            # answers ("keep your full rest and be at the Orsay about 17:02, or sit
            # six minutes and be back by 17:00"). Stepping a stop outside here
            # answers that question FOR her and hands back a quietly thinner day
            # instead. Measured 2026-08-25 on Rosemary's own day, forty-six minutes
            # late out of the Orangerie: the remainder priced 100 s over her clock,
            # the move shed an interior to absorb those 100 s, `overrun_seconds` came
            # back 0, and the live reply carried NO question at all — the exact
            # silent shedding W5.14 exists to stop. Below, where the alternative
            # really is a refusal, the move runs exactly as before.
            return list(best_over[0].selected) if best_over else list(selected)
        stepped_outside = step_outside_repair()
        if stepped_outside is not None:
            return stepped_outside
        # Nothing banked at all. TWO different worlds land here and the refusal
        # must not confuse them (the c-leg12 defect, W4.2: the message claimed
        # the day "overruns" while its own numbers showed 75 built against 270
        # required): with a leg cap set, every candidate day may have been
        # REJECTED AT THE CAP — the day starved, it did not overrun.
        # THREE worlds, not two (W4.12 re-derivation of the D-ii template). The
        # old test was `min(observed) < floor` and then REPORTED `max(observed)`,
        # so a candidate set that straddled the band — some days too short, some
        # too long, none in it — printed the longest OVERRUN as "the longest day
        # that fits the cap" and advised a SHORTER day: on the flagship, "555
        # minutes fits ... ask for a shorter day" against a 300-minute request
        # (Théo, Camille, Rosemary, Marcus, Sofia, Julien, Paulo all read it).
        floor = planning_budget.minimum_elapsed_seconds
        ceiling = planning_budget.nominal_elapsed_seconds
        under = [o for o in observed if o < floor]
        over = [o for o in observed if o > ceiling]
        best = min(observed) if observed else None
        capped = input.max_leg_minutes is not None
        if not observed:
            # A FOURTH WORLD, and the one the tail was rewritten for. NOTHING was
            # priced: the repair was handed no stop it could seat, so there is no
            # route to have overrun anything and no shortest one to have been
            # found. Measured 2026-08-25 on a New York 60-minute start, the
            # sentence made all three claims at once — every route overran, the
            # shortest was still too long, and (in its own tail) no bounded route
            # had been priced at all. `reason` is ALSO the clause a traveller
            # reads on its own, with none of the rest of the line beside it
            # (src/api/routes/trips.py::_refusal_detail prints `reason` and files
            # the whole sentence under `technical`), so it has to be true alone.
            reason = (
                "no stop near this start could be seated into a route — try a "
                "longer day, or a start with more within walking distance"
            )
        elif under and not over and capped:
            # STARVED under a cap: everything found is too short. Report the
            # longest that fits, which is the honest ceiling of what the cap allows.
            best = max(under)
            reason = (
                f"with no single walk longer than {input.max_leg_minutes} minutes, "
                f"the most that can be built here is about {best // 60} minutes, "
                f"not the {ceiling // 60} you asked for — allow longer walks, or "
                f"ask for a shorter day"
            )
        elif under and over:
            # THE BAND HAS A GAP: the routes found are either too long or too
            # short, and nothing lands between. Say both numbers; advise both ways.
            best = min(over)
            walk_clause = (
                f" with no single walk longer than {input.max_leg_minutes} minutes"
                if capped
                else ""
            )
            reason = (
                f"no route lands between {floor // 60} and {ceiling // 60} minutes"
                f"{walk_clause}: the ones found run about {max(under) // 60} minutes "
                f"or less, or about {min(over) // 60} minutes or more — "
                + ("allow longer walks, or " if capped else "")
                + "ask for a longer or a shorter day"
            )
        elif over:
            reason = (
                "every route reachable from this start overruns the requested "
                "duration; the shortest one found is still longer than asked for"
            )
        else:
            # THE OVERRUN SENTENCE IS NOW GUARDED BY ITS OWN PREMISE. It used to
            # be the bare `else`, so it claimed an overrun in any world the three
            # branches above did not name — including two where nothing had
            # overrun at all: every day too short with no leg cap to blame, and
            # every day IN band but rejected on its walking.
            #
            # MEASURED 2026-08-25, and this is why there is no branch per world:
            # I could not construct either state. Five attempts against the real
            # repair — a lone over-long stop, an in-band pair under a tight cap,
            # the same pair with a fixed end so the far stop could not be dropped,
            # and two distance/dwell tunings — every one was resolved first by
            # drop, by the under-ceiling bank, or by the straddle branch above.
            # They are unreachable through today's three moves. So rather than
            # enumerate worlds nobody can reach, the claim is simply not made
            # unless its own evidence is present, and anything else says only what
            # is certain: days were found, none could be served.
            reason = (
                "no route from this start could be served at the requested length"
            )
        alternatives, gap_minutes = _band_alternatives(
            input=input,
            planning_policy=planning_policy,
            best_elapsed_seconds=best,
        )
        raise CertificationPlanningInfeasibleError(
            policy_id=planning_policy.policy_id,
            minimum_elapsed_seconds=planning_budget.minimum_elapsed_seconds,
            maximum_elapsed_seconds=planning_budget.nominal_elapsed_seconds,
            best_elapsed_seconds=best,
            reason=reason,
            alternatives=alternatives,
            gap_minutes=gap_minutes,
        )

    return list(min(eligible_trials, key=rank).selected)


# Endpoint-pull will drop at most this many incumbents to make room for
# the far-envelope endpoint. Larger values can collapse the route to one
# anchor (the endpoint alone); two preserves the spine-anchor cluster
# while still letting one weak incumbent give way for an east-tip pick.
# NOTE: the drop count alone does NOT preserve the cluster when the
# greedy could only seat <= MAX_DROPS incumbents (structural for 60-min
# one-way tours, whose greedy sees only 75% of the walk budget) — that
# is the 2026-07-02 Rue Cler collapse, where a 39-beat far anchor
# evicted the entire route. _apply_endpoint_pull therefore also refuses
# to drop the last incumbent: a route consisting solely of the pulled
# endpoint is not a tour.
ENDPOINT_PULL_MAX_DROPS: int = 2


def _apply_endpoint_pull(
    selected: list[POI],
    endpoint: POI,
    *,
    spine: str | None,
    interest: frozenset[str],
    snapshot: CorpusSnapshot,
    start_lat: float,
    start_lng: float,
    walk_budget: int,
    leg_seconds_fn: LegSecondsFn | None = None,
    score_penalty: dict[str, float] | None = None,
    max_leg_seconds: int | None = None,
    party: str | None = None,
    protected_ids: frozenset[str] = frozenset(),
) -> list[POI]:
    """Insert `endpoint` as the closing stop, dropping at most
    ENDPOINT_PULL_MAX_DROPS weak incumbents to fit the walk budget. If the
    endpoint can't be made to fit within those drops, return the input
    unchanged (greedy result wins).

    ``protected_ids`` (plan S3.6) are incumbents the pull may never trade for
    its ending — pinned stops. When only protected incumbents remain to drop,
    the pull abandons instead.

    The anchor-cap half of this loop was deleted 2026-08-04 with every other stop
    ceiling; only the walk-budget drop below remains, and it is what still makes
    the loop terminate.
    """
    incumbents = list(selected)
    drops_used = 0
    while True:
        # M4: order the trial route exactly (replaces the greedy
        # best-insertion `_reorder_with_endpoint`), endpoint pinned last.
        candidate_route = order_stops(
            [*incumbents, endpoint],
            fixed_start=(start_lat, start_lng),
            fixed_end=endpoint,
            routed_cost_fn=leg_seconds_fn,
        )
        legs = _full_route_leg_seconds(
            candidate_route,
            start_lat=start_lat,
            start_lng=start_lng,
            # An endpoint-pull candidate is an OPEN chain: it ends at the pulled
            # endpoint and never walks back. Stated rather than defaulted, because
            # a silently-false round trip would under-count a loop by its closing leg.
            round_trip=False,
            leg_seconds_fn=leg_seconds_fn,
        )
        walk = sum(legs)
        if max_leg_seconds is not None and max(legs, default=0) > max_leg_seconds:
            # THE PER-LEG CAP checks the pulled ordering's WHOLE chain (S2.3 —
            # this pass runs once per route, so the full sweep is affordable
            # here). One over-cap leg makes the pull unusable to the party that
            # set the axis, and dropping incumbents only LENGTHENS the surviving
            # legs, so there is nothing to iterate toward: abandon the pull and
            # let the greedy result (already cap-admitted leg by leg) stand.
            return list(selected)
        if walk <= walk_budget:
            return candidate_route
        droppable = [p for p in incumbents if p.id not in protected_ids]
        if len(incumbents) <= 1 or not droppable or drops_used >= ENDPOINT_PULL_MAX_DROPS:
            # Bounded drops exhausted — or the next drop would evict the
            # LAST incumbent, leaving [endpoint] alone (a one-stop route
            # of just the pulled endpoint is a collapse, not a tour —
            # the 2026-07-02 Rue Cler regression), or only PINNED
            # incumbents remain (a pin is never traded for an ending,
            # plan S3.6): abandon the pull and let the greedy result stand.
            return list(selected)
        incumbents = _drop_weakest(
            incumbents,
            spine,
            interest,
            snapshot,
            score_penalty,
            party=party,
            protected_ids=protected_ids,
        )
        drops_used += 1


def _drop_weakest(
    pois: list[POI],
    spine: str | None,
    interest: frozenset[str],
    snapshot: CorpusSnapshot,
    score_penalty: dict[str, float] | None = None,
    *,
    party: str | None = None,
    protected_ids: frozenset[str] = frozenset(),
) -> list[POI]:
    weakest = min(
        (p for p in pois if p.id not in protected_ids),
        key=lambda p: (
            poi_score(p, spine, interest, snapshot, penalty=score_penalty, party=party),
            p.id,
        ),
    )
    return [p for p in pois if p.id != weakest.id]


# ---------------------------------------------------------------------------
# Spine selection
# ---------------------------------------------------------------------------


def pick_spine_area(
    start_lat: float,
    start_lng: float,
    candidates: Iterable[POI],
    snapshot: CorpusSnapshot,
) -> str | None:
    """Most-populous non-city Area among POIs within SPINE_RADIUS_M.

    Tier-weighted: each candidate POI casts `tier` votes for each of its
    Areas. **Phase 2.6 rule:** if the top-scoring Area is a district,
    promote a competing neighborhood/island/corridor whose votes are at
    least `(top / SPINE_DISTRICT_DOMINANCE)` — i.e., the district must be
    ≥2x the more-specific area to keep the spine. Districts naturally
    accumulate more votes (every district contains everything inside
    it), so without this lift districts always win and the algorithm
    can never pick "Île de la Cité" near Pont Neuf.

    Among multiple specific Areas the highest score wins; among true
    score ties, fall back to SPINE_AREA_TYPE_PRIORITY then alphabetical.

    Falls back to the closest candidate's most-specific non-city Area
    when no POIs are within SPINE_RADIUS_M.
    """
    nearby: list[POI] = []
    for poi in candidates:
        if haversine_m(start_lat, start_lng, poi.lat, poi.lng) <= SPINE_RADIUS_M:
            nearby.append(poi)

    if not nearby:
        sorted_by_dist = sorted(
            candidates,
            key=lambda p: haversine_m(start_lat, start_lng, p.lat, p.lng),
        )
        for poi in sorted_by_dist:
            ranked = _rank_areas_by_specificity(poi.areas, snapshot)
            if ranked:
                return ranked[0]
        return None

    votes: Counter[str] = Counter()
    for poi in nearby:
        for area in poi.areas:
            if _is_excluded(area, snapshot):
                continue
            votes[area] += poi.tier

    if not votes:
        return None

    leader = max(votes, key=lambda a: (votes[a], -_type_rank(a, snapshot), a))
    leader_type = snapshot.area_types.get(leader, "")

    # If the leader is a district, see whether a more-specific Area is
    # close enough (≥ leader / SPINE_DISTRICT_DOMINANCE) to win.
    if leader_type in DISTRICT_AREA_TYPES:
        threshold = votes[leader] / SPINE_DISTRICT_DOMINANCE
        contenders = [
            a
            for a in votes
            if snapshot.area_types.get(a, "") in SPECIFIC_AREA_TYPES and votes[a] >= threshold
        ]
        if contenders:
            return max(contenders, key=lambda a: (votes[a], -_type_rank(a, snapshot), a))

    # Fall through: literal top-scorer. Ties broken by area_type priority
    # then alphabetically (deterministic).
    return min(votes.keys(), key=lambda a: _spine_tiebreak_key(a, votes, snapshot))


def _type_rank(area: str, snapshot: CorpusSnapshot) -> int:
    area_type = snapshot.area_types.get(area, "")
    return (
        SPINE_AREA_TYPE_PRIORITY.index(area_type)
        if area_type in SPINE_AREA_TYPE_PRIORITY
        else len(SPINE_AREA_TYPE_PRIORITY)
    )


def _spine_tiebreak_key(area: str, votes: Counter[str], snapshot: CorpusSnapshot) -> tuple:
    """Smaller is better: (-vote_count, type_rank, vote_count, name).

    Used only when the 2x district lift hasn't already chosen a winner —
    e.g., among multiple specific Areas with identical scores.
    """
    return (-votes[area], _type_rank(area, snapshot), votes[area], area)


def _rank_areas_by_specificity(areas: tuple[str, ...], snapshot: CorpusSnapshot) -> list[str]:
    """Order a POI's Areas from most-specific to least, dropping city-typed."""
    ranked = []
    for a in areas:
        if _is_excluded(a, snapshot):
            continue
        area_type = snapshot.area_types.get(a, "")
        rank = (
            SPINE_AREA_TYPE_PRIORITY.index(area_type)
            if area_type in SPINE_AREA_TYPE_PRIORITY
            else len(SPINE_AREA_TYPE_PRIORITY)
        )
        ranked.append((rank, a))
    ranked.sort()
    return [a for _, a in ranked]


def _is_excluded(area: str, snapshot: CorpusSnapshot) -> bool:
    area_type = snapshot.area_types.get(area, "")
    return area_type in EXCLUDED_AREA_TYPES


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


#: Bounded party-affordance boost inside `poi_score` (redesign row 6.4; plan
#: S2.6): family days weight `children_can_run` places up; take-it-easy and
#: couple days weight `sit_and_talk` up. Bounded and test-pinned — large
#: enough to let a richer children's/talkable place occasionally beat a
#: marginally higher-scoring one, never large enough to override a landmark
#: (`importance` alone spans 1-5). `spotlight` does NOT gain this factor:
#: banding is not party-aware in this phase, and this is a NEW factor added
#: to `poi_score` alone, not a re-opening of the poi_score/spotlight
#: deliberate-overlap ruling (dedup-review 2026-08-07).
PARTY_AFFORDANCE_BOOST: float = 1.25


def _party_affordance_factor(poi: POI, party: str | None) -> float:
    if party == "family" and poi.children_can_run:
        return PARTY_AFFORDANCE_BOOST
    if party in ("couple", "take_it_easy") and poi.sit_and_talk:
        return PARTY_AFFORDANCE_BOOST
    return 1.0


def poi_score(
    poi: POI,
    spine_area: str | None,
    interest: frozenset[str],
    snapshot: CorpusSnapshot,
    *,
    penalty: dict[str, float] | None = None,
    party: str | None = None,
) -> float:
    """The §3 per-POI score: importance x richness x lens_relevance x alignment x role.

    ``penalty`` (poi_id → factor) is the per-place score channel; its live
    producers are the rain pricing and the skip-the-queues dial. Born as
    the k-flavours diversity knob; the flavours died at Phase 4 (§8.1).

    Phase 3 re-baseline (Step 3.5): the lens factor is now the POSITIVE-floored
    ``lens_relevance`` (miss -> LENS_FLOOR = 0.25), NOT the legacy hard-filter
    ``_lens_adjacency`` (miss -> 0.0). This is the second half of the model
    switch: with the candidate-pool lens gate removed, a lens-miss POI is
    eligible, so its score must DIM rather than ZERO -- otherwise the greedy
    would still never pick it and a tier-5 off-genre landmark would silently
    drop out (violating "lens alone never silences a landmark", s3). The
    objective stays strictly multiplicative (s9.5); only the lens factor's
    miss value changed from 0.0 to a positive floor. With no lenses requested
    both functions return 1.0, so the unlensed objective is byte-identical.
    """
    # DELIBERATE OVERLAP WITH `spotlight`, not a fork (dedup-review 2026-08-07):
    # both multiply a tier-derived weight by the ONE `lens_relevance` definition,
    # and that shared factor already lives in one function. The tier transforms
    # differ ON PURPOSE — the greedy weighs raw tier against richness/alignment/
    # role, while spotlight uses `gravity(tier)` against proximity to band
    # OUTPUT — so collapsing them would force one transform to serve two
    # different questions. If the §3 factor model itself changes, change
    # `lens_relevance` (shared automatically) and revisit both tier weights.
    importance = float(poi.tier)
    richness = math.log1p(max(0, poi.beat_count))
    relevance = lens_relevance(poi, lenses=interest or None, snapshot=snapshot)
    alignment = _area_alignment(poi, spine_area, snapshot)
    role_mult = POI_ROLE_MULTIPLIER.get(poi.poi_role, 0.0)
    diversity = penalty.get(poi.id, 1.0) if penalty else 1.0
    affordance = _party_affordance_factor(poi, party)
    return importance * richness * relevance * alignment * role_mult * diversity * affordance


def _lens_adjacency(poi: POI, interest: frozenset[str], snapshot: CorpusSnapshot) -> float:
    """§3 hop model over the POI's active beat lenses (M3, replaces _interest_bias).

    1.0 when any beat lens directly matches a requested lens; 0.6 when any
    beat lens is one IS_PARENT_OF hop from a requested lens (parent or
    child); 0.0 otherwise. No requested lenses → uniform 1.0 so selection
    degrades gracefully to importance x richness x alignment x role.
    """
    relation = _lens_relation(poi, interest, snapshot)
    if relation == "no_lens" or relation == "direct":
        return LENS_ADJACENCY_DIRECT
    if relation == "one_hop":
        return LENS_ADJACENCY_ONE_HOP
    return LENS_ADJACENCY_MISS


def _lens_relation(poi: POI, interest: frozenset[str], snapshot: CorpusSnapshot) -> str:
    """Classify a POI's relation to the requested lenses (shared §3 logic).

    Returns one of ``"no_lens"`` (no lenses requested), ``"direct"`` (a beat
    lens directly matches a requested lens), ``"one_hop"`` (a beat lens is one
    IS_PARENT_OF hop — parent OR child — from a requested lens), or
    ``"miss"`` (neither). This is the single source of truth for the
    direct/1-hop/miss decision: both the legacy ``_lens_adjacency`` (hard
    filter, miss → 0.0) and the new ``lens_relevance`` (spotlight floor,
    miss → LENS_FLOOR) map these labels onto their own weights, so the two
    can never drift apart on classification.
    """
    if not interest:
        return "no_lens"
    interest_low = {s.lower() for s in interest}
    neighbors: set[str] = set()
    for lens in interest_low:
        neighbors |= snapshot.lens_neighbors.get(lens, frozenset())
    one_hop = False
    for beat in snapshot.beats_for(poi.id):
        for lens in beat.lenses:
            lens_low = lens.lower()
            if lens_low in interest_low:
                return "direct"
            if lens_low in neighbors:
                one_hop = True
    return "one_hop" if one_hop else "miss"


# ---------------------------------------------------------------------------
# §3 spotlight model (Phase 3) — pure scoring, ADDITIVE (no selection change)
# ---------------------------------------------------------------------------


def gravity(tier: int) -> float:
    """Gravity ∝ importance_tier (1..5) — the §3 'how much it pulls' factor.

    Monotonic (strictly increasing) in tier, reusing the SAME linear tier
    weighting ``poi_score`` already applies (``importance = float(poi.tier)``)
    so the spotlight stays consistent with the existing objective. A tier-5
    landmark pulls 5x a tier-1 footnote. Always > 0 for valid tiers (1..5).
    """
    return float(tier)


def lens_relevance(poi: POI, *, lenses: frozenset[str] | None, snapshot: CorpusSnapshot) -> float:
    """§3 lens_relevance — a POSITIVE-FLOORED genre factor (never a gate).

    - 1.0 on a direct lens hit,
    - 0.6 on a parent/child 1-hop via ``snapshot.lens_neighbors``,
    - LENS_FLOOR (0.25) on a miss — a miss DIMS, it never zeroes,
    - 1.0 (uniform) when ``lenses`` is None/empty.

    Shares its direct/1-hop/miss classification with ``_lens_adjacency`` via
    ``_lens_relation`` — the ONLY difference is the miss maps to LENS_FLOOR
    here instead of 0.0. Because the floor is strictly positive, lens alone
    can never silence a POI; only the combined spotlight (low gravity AND a
    miss) can drop one below an output band (§3).
    """
    interest = lenses or frozenset()
    relation = _lens_relation(poi, interest, snapshot)
    if relation == "no_lens":
        return LENS_RELEVANCE_NO_LENS
    if relation == "direct":
        return LENS_RELEVANCE_DIRECT
    if relation == "one_hop":
        return LENS_RELEVANCE_ONE_HOP
    return LENS_FLOOR


def proximity(marginal_detour_seconds: float = 0.0) -> float:
    """§3 proximity — the on-path bonus, decaying with marginal detour.

    1.0 when the POI is exactly on the A-B line (``marginal_detour_seconds``
    == 0) and exponentially decaying as the routed detour off that line grows:
    ``exp(-marginal_detour_seconds / PROXIMITY_DECAY_SECONDS)``. The decay is
    strictly monotonically decreasing in detour and ALWAYS > 0, so proximity
    alone never zeroes a POI (preserving the §3 silence invariant). Negative
    detours (a POI that shortens the route — rare, from integer routing
    rounding) are clamped to 0 so proximity caps at 1.0.
    """
    detour = max(0.0, marginal_detour_seconds)
    return math.exp(-detour / PROXIMITY_DECAY_SECONDS)


def spotlight(
    poi: POI,
    *,
    lenses: frozenset[str] | None,
    snapshot: CorpusSnapshot,
    marginal_detour_seconds: float = 0.0,
) -> float:
    """§3 continuous spotlight score = gravity x lens_relevance x proximity.

    PURE and ADDITIVE — this Step 3.1 function does NOT touch select_route's
    selection (the tier/lens gates stay until Step 3.5). It scores how much of
    the user's time and audio a POI earns; downstream steps map the score onto
    output bands (headline / full / short / vignette / silent).

    All three factors are ≥ 0 (the §9.5 multiplicative-objective invariant),
    and gravity is > 0 for any valid tier while lens_relevance is ≥ LENS_FLOOR
    > 0 and proximity is > 0 — so the spotlight of any real POI is strictly
    positive. A lens miss merely DIMS it (xLENS_FLOOR); the silence band is a
    downstream threshold decision, not a zero here.
    """
    return (
        gravity(poi.tier)
        * lens_relevance(poi, lenses=lenses, snapshot=snapshot)
        * proximity(marginal_detour_seconds)
    )


def band_for_spotlight(spotlight_score: float, *, tier: int) -> str:
    """§3 band classifier — map a spotlight score (+ POI tier) to an output band.

    Returns one of ``"headline"`` | ``"full"`` | ``"short"`` | ``"vignette"`` |
    ``"silent"``. The dwell bands (headline/full/short) are a real stop; the
    vignette band is one line as you pass; ``"silent"`` means excluded for this
    user. Use ``is_dwell_band`` / ``DWELL_BANDS`` for the output-facing collapse.

    PURE and ADDITIVE — this does NOT touch select_route's selection (the
    tier/lens gates stay until Step 3.5). Thresholds are INITIAL anchors
    (BAND_THRESHOLD_*), refined in the Step 3.5 golden re-baseline.

    The §3 silence invariant is encoded STRUCTURALLY, not as a bare score
    threshold: silence requires BOTH a lens miss AND low gravity. A high-gravity
    landmark (``tier >= BAND_LANDMARK_TIER``) is therefore floored at
    ``vignette`` even if a large proximity detour drove its score below the
    silent cut -- "lens alone never silences a landmark on the user's path"
    (and a detour alone never does either). Only a genuinely low-gravity,
    off-genre POI (low tier whose score fell below BAND_THRESHOLD_VIGNETTE)
    goes silent.
    """
    if spotlight_score >= BAND_THRESHOLD_HEADLINE:
        return BAND_HEADLINE
    if spotlight_score >= BAND_THRESHOLD_FULL:
        return BAND_FULL
    if spotlight_score >= BAND_THRESHOLD_SHORT:
        return BAND_SHORT
    if spotlight_score >= BAND_THRESHOLD_VIGNETTE:
        return BAND_VIGNETTE
    # Below the vignette cut. A high-gravity landmark never goes silent on
    # score alone -- floor it at vignette (the §3 invariant). Only a
    # low-gravity POI that also missed on lens (the joint condition that
    # produced this low score) is truly silent.
    if tier >= BAND_LANDMARK_TIER:
        return BAND_VIGNETTE
    return BAND_SILENT


def is_dwell_band(band: str) -> bool:
    """Output-facing collapse: True for a dwell stop (headline/full/short).

    ``vignette`` is a walk-past line (not a dwell stop); ``silent`` is
    excluded. Both return False.
    """
    return band in DWELL_BANDS


# ---------------------------------------------------------------------------
# Track B (Step B.1) — walk-past vignette selection along the legs
# ---------------------------------------------------------------------------


def _point_to_segment_m(
    lat: float, lng: float, a_lat: float, a_lng: float, b_lat: float, b_lng: float
) -> float:
    """Perpendicular distance (m) from a point to the A→B straight segment.

    Local equirectangular projection around the segment's mean latitude —
    accurate to well under a metre at the sub-kilometre scales a walking leg
    spans, and fully deterministic. Points beyond the segment's endpoints
    measure to the nearest endpoint (it is a SEGMENT, not an infinite line).
    """
    lat0 = math.radians((a_lat + b_lat) / 2.0)
    kx = math.cos(lat0) * EARTH_RADIUS_M
    ky = EARTH_RADIUS_M

    ax, ay = math.radians(a_lng) * kx, math.radians(a_lat) * ky
    bx, by = math.radians(b_lng) * kx, math.radians(b_lat) * ky
    px, py = math.radians(lng) * kx, math.radians(lat) * ky

    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq <= 0.0:
        return math.hypot(px - ax, py - ay)  # degenerate (co-located) leg
    t = ((px - ax) * dx + (py - ay) * dy) / seg_len_sq
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def select_vignettes(
    route: Route,
    snapshot: CorpusSnapshot,
    lenses: frozenset[str] | None = None,
) -> dict[int, tuple[POI, ...]]:
    """Guarded external boundary for walk-past vignette selection."""

    require_materialized_snapshot(snapshot, operation="vignette selection")
    return _select_vignettes_validated(route, snapshot, lenses)


def _select_vignettes_validated(
    route: Route,
    snapshot: CorpusSnapshot,
    lenses: frozenset[str] | None = None,
) -> dict[int, tuple[POI, ...]]:
    """Track B Step B.1 — pure walk-past vignette selection along the legs.

    Returns ``leg_idx → vignette POIs`` where leg ``i`` is the walk INTO stop
    ``i``, matching ``route.transits`` indexing. Locked rules (Track B,
    IMPLEMENTATION-PLAN deferred clarifications):

    - eligible iff the POI's on-path band is exactly ``vignette``
      (``band_for_spotlight(spotlight(...), tier=poi.tier)``); dwell-band
      POIs are real-stop material, silent POIs stay excluded;
    - within ``VIGNETTE_MAX_DETOUR_M`` (perpendicular) of the leg's straight
      segment;
    - never a dwell stop of this route;
    - deduped across legs — the first (earliest) leg that EMITS a POI wins; a
      candidate merely cut by the cap on an earlier leg stays available;
    - capped at ``VIGNETTE_MAX_PER_LEG`` per leg;
    - deterministic order: spotlight descending, then id.

    One engine invariant joins the locked rules: a POI with no active beats
    has nothing to voice (the Phase 6 zero-beat rule that keeps such POIs out
    of the dwell pool), so it never becomes a vignette either — the band
    tagging in ``select_route`` only ever fires on active-beat POIs.

    Only legs BETWEEN two route POIs are considered: the Route contract does
    not carry the start coordinate, so leg 0 (start → first stop) and a round
    trip's closing leg (last stop → start) have no computable geometry here
    and never yield vignettes.
    """
    pois = route.pois
    if len(pois) < 2:
        return {}
    dwell_ids = {p.id for p in pois}

    scored: list[tuple[float, POI]] = []
    for poi in snapshot.pois:
        if poi.id in dwell_ids:
            continue
        if not poi.vignette_eligible or poi.requires_dwell:
            continue
        if not _has_active_beats(poi, snapshot):
            continue
        score = spotlight(poi, lenses=lenses or None, snapshot=snapshot)
        # Vignette-band POIs are walk-bys by score; ALSO route a demoted
        # filler-stub here (a dwell-band POI too thin for a dedicated stop — the
        # same predicate the dwell-pool filter used, so generate/compose agree).
        if band_for_spotlight(score, tier=poi.tier) != BAND_VIGNETTE and not _is_filler_stub(
            poi, snapshot, lenses
        ):
            continue
        scored.append((score, poi))
    if not scored:
        return {}
    # Deterministic candidate ranking: spotlight desc, then id.
    scored.sort(key=lambda pair: (-pair[0], pair[1].id))

    out: dict[int, tuple[POI, ...]] = {}
    assigned: set[str] = set()
    for leg_idx in range(1, len(pois)):
        a, b = pois[leg_idx - 1], pois[leg_idx]
        picked: list[POI] = []
        for _score, poi in scored:
            if len(picked) >= VIGNETTE_MAX_PER_LEG:
                break
            if poi.id in assigned:
                continue
            dist = _point_to_segment_m(poi.lat, poi.lng, a.lat, a.lng, b.lat, b.lng)
            if dist > VIGNETTE_MAX_DETOUR_M:
                continue
            picked.append(poi)
        if picked:
            out[leg_idx] = tuple(picked)
            assigned.update(p.id for p in picked)
    return out


def _area_alignment(poi: POI, spine_area: str | None, snapshot: CorpusSnapshot) -> float:
    if spine_area is None:
        return AREA_ALIGNMENT_OTHER  # neutral
    if spine_area in poi.areas:
        return AREA_ALIGNMENT_SPINE
    adjacent = snapshot.adjacent_areas.get(spine_area, frozenset())
    if any(a in adjacent for a in poi.areas):
        return AREA_ALIGNMENT_ADJACENT
    return AREA_ALIGNMENT_OTHER


def _has_active_beats(poi: POI, snapshot: CorpusSnapshot) -> bool:
    """True iff the POI has at least one active beat in the snapshot.

    Phase 5 Tour 4 selected Petit Palais (tier 4) as a stop with zero
    beats because routing-aware scoring liked the corridor geometry.
    The fix: filter the candidate pool by beat count before scoring.
    """
    return any((beat.active_status or "active") == "active" for beat in snapshot.beats_for(poi.id))


def _attach_tourability_if_yellow(route: Route, assessment: TourabilityAssessment) -> Route:
    """Attach the assessment to the Route for surface-side disclosure.

    Attached when YELLOW, OR (C11a) when GREEN-but-delivered-thin. A fully-GREEN
    route that delivers richly carries NO tourability field — its absence signals
    "no warning needed". RED tours never reach this code path (raised earlier).
    """
    if assessment.status == "YELLOW" or assessment.delivered_thin:
        return route.model_copy(update={"tourability": assessment})
    return route


__all__ = [
    "CLOSER_B_WEDGE_HALF_ANGLE_DEG",
    "LENS_FLOOR",
    "PROXIMITY_DECAY_SECONDS",
    "VIGNETTE_MAX_DETOUR_M",
    "VIGNETTE_MAX_PER_LEG",
    "CorpusSnapshot",
    "MaterializedCorpusSnapshot",
    "bearing",
    "gravity",
    "in_wedge",
    "lens_relevance",
    "load_paris_corpus",
    "pick_spine_area",
    "poi_score",
    "proximity",
    "require_materialized_snapshot",
    "select_route",
    "select_vignettes",
    "spotlight",
]
