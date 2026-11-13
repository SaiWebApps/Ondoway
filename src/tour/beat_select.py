"""Within-POI beat ordering.

Given a POI and its active beats, pick an ordering strategy and emit a
deterministic ordered list.

A 2026-04-29 change tightened the Area cold-open hoist
(`find_area_orientation_beat`): a sibling-POI orientation beat may only
hoist when (a) it is the start POI's own beat, (b) the beat carries no
physical_cues at all, or (c) the beat's source POI is within
`HOIST_PROXIMITY_M` of the start stop. Otherwise the cues describe a
different physical location and would land geographically dishonest at
the cold-open. Falls through to SYNTHESIZED_OPENER instead.

- "sub_location": building walk. Use fixtures.SUB_LOCATION_ORDER if
  curated; otherwise fall back to the (alphabetically stable) order of
  distinct sub_locations seen on the beats. Pick at most one beat per
  sub_location bucket. The trailing "no sub_location" pool then emits
  best-first — not a single closer.
- "trigger_address": square circumnavigation. Stable order by trigger
  address; one beat per address, then the no-address pool best-first.

Every strategy's plan is then trimmed to a per-tier ceiling (DEFAULT_FLAT_MAX
for dense stops) centrally in select_poi_beats — the golden's human-ideal roster
never voices more than that at a single stop.
- "narrative_function": flat fallback. hook → establishing → deepen →
  climax -> callback (and any remaining), 4-6 beats.

Branch thresholds: sub_location active when the stop's active beats carry
≥3 distinct values; trigger_address active when ≥5 distinct values;
otherwise flat.

Pure functions. No Neo4j. The Neo4j loader lives in selection.py.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Callable, Iterable

from .contract import POI, BeatRef, BeatSequence, OrderingStrategy, POIBeats, Route
from .fixtures import sub_location_order_for
from .routing import beat_spoken_seconds

NARRATIVE_FUNCTION_ORDER: tuple[str, ...] = (
    "hook",
    "establishing",
    "deepen",
    "climax",
    "callback",
)

SUB_LOCATION_THRESHOLD: int = 3  # ≥3 distinct values → building walk
TRIGGER_ADDRESS_THRESHOLD: int = 5  # ≥5 distinct values → square circumnav
# Calibration (2026-04-29): bumped from 6 to 8. The empirical Île
# walk's Sainte-Chapelle (5 beats) + Île de la Cité (4 beats) lost
# fixture deepens to the 6-beat trim. 8 keeps all empirical anchor-flat
# beats while staying under the audio budget for 90-min tours
# (Île 8 anchors x ~4 extra-beat seconds is well within the 44-min budget).
DEFAULT_FLAT_MAX: int = 12
# Spatial stops need a small non-addressed lane for the general context or
# closing beat that legitimately belongs to the POI as a whole.  This is a
# minimum, not a separate unlimited quota: additional unlocated beats may win
# the ordinary score-based remainder, and the overall tier cap still applies.
SPATIAL_NO_KEY_RESERVE: int = 1
# Calibration (2026-04-29): bumped from 2 to 3. The empirical
# Vert-Galant pause stop carries 3 beats (establishing + view + tarnished);
# the prior cap dropped the 'tarnished' deepen.
PAUSE_BEATS_MAX: int = 4  # tier-3 stop

# Calibration (2026-04-29): when both sub_location and
# trigger_address meet their thresholds, pick the dominant primitive.
# The two threshold tests alone prescribe no tie-break; the empirical
# Place des Vosges walk (6 sub_locs, 23 trigger_addresses) demonstrated
# trigger should win when the address pool is much richer. Without this
# rule, sub_loc fires first and PdV emits ~8 beats instead of ~25.
# Conciergerie (6 sub_locs, 0 triggers) still uses sub_location because
# triggers don't meet threshold.

# Claim-level dedup. Runs after the per-strategy ordering and before
# tone-variety. Only beats that share at least one lens (or both have none)
# are eligible to collide. They collide when ``J``, the Jaccard of their
# entity sets or of their subject-tag word sets, reaches this value.
B8_JACCARD_THRESHOLD: float = 0.8

# Recall recalibration (2026-04-29). Three known duplicate pairs from an
# earlier rerun all evaded the 0.8 entities/subject_tag thresholds:
#   Mesme Gallet  (de1377b4 / bf668074): entities J=0.500, subject_tag empty
#   Hugo affair   (b41d4c77 / a1833622): entities J=0.667, subject_tag empty
#   Pantheon-vow  (ee115ca8 / 142060a7): entities J=0.125 (Saint vs St.),
#                                        subject_tag empty
# Mesme/Hugo are paraphrases — caught by char-5-gram body Jaccard at 0.30.
# Pantheon entities don't canonicalize to a high Jaccard (the supporting
# casts diverge: Soufflot/Chateauroux vs Voltaire/Rousseau) but both leads
# carry the same year + lead-entity signature — caught by year-overlap +
# canonical-entity-overlap ≥ 2.
B8_CHAR_NGRAM_N: int = 5
B8_CHAR_NGRAM_THRESHOLD: float = 0.30
B8_CANONICAL_ENTITY_PAIR_MIN: int = 2  # min canonical-entity overlap for the year+entity signal

# Tie-breaker priority when two beats collide and beat-score is equal.
# Higher index wins (climax beats deepen, etc.).
_NARRATIVE_FUNCTION_PRIORITY: dict[str, int] = {
    "transition": 1,
    "callback": 2,
    "scene_setter": 3,
    "hook": 4,
    "establishing": 5,
    "deepen": 6,
    "climax": 7,
}


def choose_ordering_strategy(beats: Iterable[BeatRef]) -> OrderingStrategy:
    """Pick the spatial primitive to sequence by.

    When both primitives meet threshold, the one with more distinct values
    wins. PdV (6 sub_locs, 23 trigger_addrs) requires trigger_address to
    reproduce the empirical address-by-address circumnavigation; Conciergerie
    (6 sub_locs, 0 triggers) keeps sub_location because triggers fail the
    threshold gate.
    """
    sub_n = len({b.sub_location for b in beats if b.sub_location})
    trig_n = len({b.trigger_address for b in beats if b.trigger_address})
    if sub_n >= SUB_LOCATION_THRESHOLD and (trig_n < TRIGGER_ADDRESS_THRESHOLD or sub_n >= trig_n):
        return "sub_location"
    if trig_n >= TRIGGER_ADDRESS_THRESHOLD:
        return "trigger_address"
    if sub_n >= SUB_LOCATION_THRESHOLD:
        return "sub_location"
    return "narrative_function"


def select_poi_beats(
    poi: POI,
    beats: Iterable[BeatRef],
    *,
    interest_lenses: Iterable[str] | None = None,
    apply_cap: bool = True,
) -> POIBeats:
    """Order and trim a POI's beats, returning the chosen plan.

    ``apply_cap=False`` (via :func:`select_poi_beats_full`) skips the per-tier
    ceiling so the FULL ordered plan is returned. The capped plan is always a
    SUBSET of the full plan in the same order — a prefix for the flat strategy, a
    score-selected subset for the spatial strategies (``_cap_spatial_by_score``)
    — so keep-exploring extras (full minus voiced) stay well-defined either way.
    """
    active = [b for b in beats if b.active_status == "active"]
    if not active:
        return POIBeats(
            poi_id=poi.id,
            poi_name=poi.name,
            ordering_strategy="narrative_function",
            beats=(),
        )

    interest = frozenset(s.lower() for s in (interest_lenses or []))
    strategy = choose_ordering_strategy(active)

    if strategy == "sub_location":
        ordered = _order_by_sub_location(poi, active, interest)
    elif strategy == "trigger_address":
        ordered = _order_by_trigger_address(active, interest)
    else:
        ordered = _order_by_narrative_function(poi, active, interest)

    # Orientation is a cold-open primitive that competes
    # with anchor-class beats in the no_loc / no_addr slot under the
    # spatial strategies. When an orientation beat exists at the POI,
    # prepend the highest-scoring one so the cold-open lookup can find
    # it. Generation's consumed_in_cold_open set keeps it from being
    # double-emitted at stop 0.
    ordered = _hoist_orientation(ordered, active, interest)
    # Protect the just-hoisted stop_orientation head from being the dedup loser:
    # _hoist_orientation promoted it precisely so generation's cold-open lookup
    # (_find_orientation_beat / _build_cold_open) can find a canonical orientation
    # beat for this stop. If dedup silently dropped it on a paraphrase collision the
    # hoist would be undone and the tour would fall back to the SYNTHESIZED_OPENER —
    # the exact cold-open flatness the hoist exists to prevent. Mirrors the
    # ordered[0] protection in _cap_spatial_by_score.
    protected_id = (
        ordered[0].id
        if ordered and (ordered[0].beat_type or "").lower() == "stop_orientation"
        else None
    )
    ordered = _apply_b8_lite_dedup(ordered, interest, protected_id=protected_id)

    # Per-tier ceiling, applied AFTER dedup (so the capped plan is a strict
    # subset of the uncapped plan — one shared dedup pass, no diverging
    # paraphrase-survivors) but BEFORE tone-variety. Order matters: _hoist_orientation
    # puts the cold-open at index 0, and _enforce_tone_variety may swap it off index 0;
    # capping before tone lets the cap protect the still-at-head orientation, and tone
    # then only REORDERS the survivors (never drops), so the cold-open can't vanish.
    # The golden's human-ideal roster never voices more than DEFAULT_FLAT_MAX beats at
    # a single stop — even a 23-address square (Place des Vosges) is curated to 8 — so
    # the spatial strategies get the ceiling the flat strategy always had. Spatial
    # plans keep the BEST cap-many by score then stay in walk order (a plain prefix
    # would seat low-value early addresses and drop the human's editorial picks: PdV
    # 6/18 vs best-by-score 11/18); flat keeps its narrative-arc prefix.
    if apply_cap:
        if strategy == "narrative_function":
            ordered = _apply_flat_cap(poi, ordered)
        else:
            ordered = _cap_spatial_by_score(poi, ordered, interest)

    # Spatial strategies pass their grouping so tone variety cannot reorder beats
    # across sub-anchors (see _enforce_tone_variety); flat stops keep the
    # whole-stop interleave.
    ordered = _enforce_tone_variety(ordered, group_of=_tone_group_key(strategy))
    # Flat/narrative stops only: after selection is settled, make the surviving
    # beats flow oldest→newest so a history-dense stop reads in order instead of
    # a chronological jumble. Reorder-only (golden-safe); spatial strategies keep
    # their walk order. The final stop's closing beat is re-hoisted downstream by
    # generation.reorder_final_stop_for_closing, so a grand closing still lands last.
    if strategy == "narrative_function":
        ordered = _order_body_chronologically(ordered)

    return POIBeats(
        poi_id=poi.id,
        poi_name=poi.name,
        ordering_strategy=strategy,
        beats=tuple(ordered),
    )


def select_poi_beats_full(
    poi: POI,
    beats: Iterable[BeatRef],
    *,
    interest_lenses: Iterable[str] | None = None,
) -> POIBeats:
    """The UNCAPPED beat plan — every active beat, ordered, no per-tier trim.

    Identical ordering/dedup/tone-variety to :func:`select_poi_beats` but without
    the per-tier ceiling, so ``select_poi_beats(...).beats`` is a SUBSET of this
    plan's beats in the same order (a prefix for the flat strategy; a score-selected
    subset for the spatial strategies). The beats NOT in the capped output are a
    stop's "keep exploring here" extras — what the tour budget had no room to voice.
    """
    return select_poi_beats(poi, beats, interest_lenses=interest_lenses, apply_cap=False)


def extra_beat_ids(
    poi: POI,
    beats: Iterable[BeatRef],
    voiced_ids: Iterable[str],
    *,
    interest_lenses: Iterable[str] | None = None,
) -> tuple[str, ...]:
    """The ordered ids of a stop's beats the tour did NOT voice.

    A stop's "keep exploring here" extras: the uncapped plan
    (:func:`select_poi_beats_full`) minus the beats already voiced in the tour
    (its ScriptPOI ``beat_ids``), in the full plan's order — narrative-priority
    order for the flat strategy, walk order for the spatial strategies. Empty when
    the tour voiced everything the stop had. (The spatial strategies voice the
    best-scoring beats, so these extras are the lower-value remainder.)

    Transit-class beats never enter the pool: extras play STANDING at the stop
    (the tap), and a walk's own narration — a beat the origin-verified pick may
    have rightly refused for this route — read standing still tells the person
    to walk a road they are not on. Same function set as the pick and the
    anchor-block filter (``TRANSIT_NARRATIVE_FUNCTIONS``), one definition.
    """
    from .generation import TRANSIT_NARRATIVE_FUNCTIONS  # cycle avoidance

    voiced = set(voiced_ids)
    full = select_poi_beats_full(poi, beats, interest_lenses=interest_lenses)
    return tuple(
        b.id
        for b in full.beats
        if b.id not in voiced
        and (b.narrative_function or "").lower() not in TRANSIT_NARRATIVE_FUNCTIONS
    )


def govern_poi_beats(
    plan: POIBeats,
    allowance_seconds: int | None,
    *,
    interest: frozenset[str] | None = None,
) -> tuple[POIBeats, tuple[str, ...]]:
    """Cap a POI's emitted plan to a per-stop audio allowance (the audio governor).

    ``allowance_seconds is None`` marks an EXEMPT stop (the start-anchor or the
    fixed-end-B / pulled endpoint): the plan passes through untouched with no
    overflow. Otherwise keep a PREFIX of the ordered beats whose cumulative
    voiced seconds (``routing.beat_spoken_seconds``) stay within the allowance;
    the FIRST beat is always kept (a stop must speak at least once, even if
    ``beats[0]`` alone exceeds the allowance — the ratified bounded one-beat
    overshoot). The remaining ordered beats are the keep-exploring overflow.
    Pure; does not re-order (preserves the cold-open -> body -> closer arc up to
    the cut).
    """
    if allowance_seconds is None or not plan.beats:
        return plan, ()
    cut = len(plan.beats)
    consumed = 0
    for i, beat in enumerate(plan.beats):
        secs = beat_spoken_seconds(beat)
        if i > 0 and consumed + secs > allowance_seconds:
            cut = i
            break
        consumed += secs
    if cut >= len(plan.beats):
        return plan, ()
    kept = list(plan.beats[:cut])
    overflow = list(plan.beats[cut:])
    # A stop seated FOR a subject must not be governed mute on it.
    # The arc orders the buckets, so an off-family hook can outrank every
    # on-family beat and the prefix cut sheds the lot. When the person asked
    # and the prefix keeps none of the family the plan holds, swap ONE in for
    # the last kept beat — the cold-open never moves, and the seconds move by
    # at most one beat, the same bounded overshoot the first-beat rule already
    # ratifies. No interest, or the family already kept: the plain prefix,
    # byte-identical.
    if interest:
        def on_family(beat: BeatRef) -> bool:
            return any(lens.lower() in interest for lens in beat.lenses)

        if not any(on_family(beat) for beat in kept):
            wanted = next((beat for beat in overflow if on_family(beat)), None)
            if wanted is not None:
                overflow.remove(wanted)
                if len(kept) > 1:
                    overflow.insert(0, kept.pop())
                kept.append(wanted)
    capped = plan.model_copy(update={"beats": tuple(kept)})
    return capped, tuple(b.id for b in overflow)


# --- ordering strategies ----------------------------------------------------


def _order_by_sub_location(
    poi: POI,
    beats: list[BeatRef],
    interest: frozenset[str],
) -> list[BeatRef]:
    """Group by sub_location, pick best in each, sequence per fixture order."""
    by_loc: dict[str, list[BeatRef]] = defaultdict(list)
    no_loc: list[BeatRef] = []
    for b in beats:
        (no_loc if not b.sub_location else by_loc[b.sub_location]).append(b)

    curated = sub_location_order_for(poi.name)
    seen_locs = list(by_loc.keys())
    if curated is not None:
        # Order: curated entries that exist, in fixture order, then any
        # uncurated keys alphabetically. Stable + deterministic.
        ordered_keys = [k for k in curated if k in by_loc]
        leftover = sorted(k for k in seen_locs if k not in ordered_keys)
        ordered_keys.extend(leftover)
    else:
        ordered_keys = sorted(seen_locs)

    out: list[BeatRef] = []
    for key in ordered_keys:
        bucket = by_loc[key]
        # Emit the best beat first (the single voiced pick per bucket), then the
        # remaining bucket members best-first. The per-tier cap in select_poi_beats
        # (_cap_spatial_by_score) trims the VOICED plan to one-per-bucket-ish; the
        # UNCAPPED full plan keeps every active beat so bucket-losers surface as
        # keep-exploring extras (mirrors the no_loc pool below).
        out.extend(_top_by_score(bucket, interest, len(bucket)))

    # Trailing "no sub_location" pool: emit the un-keyed pool best-first (not
    # one closer) — a dense POI like Île de la Cité carries most of its beats with
    # sub_location=None and the single-closer rule silently dropped ~15 of them.
    # Best-first keeps the old _pick_best beat at the front. The per-tier ceiling in
    # select_poi_beats bounds the VOICED plan (the audio governor exempts the marquee,
    # so a local ceiling is what stops the marquee becoming an encyclopedia-dump);
    # the un-voiced tail surfaces as keep-exploring extras.
    if no_loc:
        out.extend(_top_by_score(no_loc, interest, len(no_loc)))

    return out


def _order_by_trigger_address(
    beats: list[BeatRef],
    interest: frozenset[str],
) -> list[BeatRef]:
    """One beat per trigger_address, addresses in stable order."""
    by_addr: dict[str, list[BeatRef]] = defaultdict(list)
    no_addr: list[BeatRef] = []
    for b in beats:
        (no_addr if not b.trigger_address else by_addr[b.trigger_address]).append(b)

    ordered_keys = sorted(by_addr.keys(), key=_address_sort_key)

    # Best beat per address first, then that address's remaining beats best-first,
    # so the UNCAPPED full plan keeps every active beat (bucket-losers surface as
    # keep-exploring extras); the per-tier cap trims the voiced plan (see
    # _order_by_sub_location for the rationale, mirrors the no_addr pool below).
    out: list[BeatRef] = []
    for k in ordered_keys:
        out.extend(_top_by_score(by_addr[k], interest, len(by_addr[k])))
    # Emit the no-trigger_address pool best-first (see _order_by_sub_location);
    # the per-tier ceiling in select_poi_beats bounds the voiced plan.
    if no_addr:
        out.extend(_top_by_score(no_addr, interest, len(no_addr)))
    return out


def _order_by_narrative_function(
    poi: POI,
    beats: list[BeatRef],
    interest: frozenset[str],
) -> list[BeatRef]:
    """Flat tier-5/4 fallback or tier-3 pause: the full hook→…→callback arc,
    UNCAPPED. The per-tier ceiling is applied centrally in :func:`select_poi_beats`
    (gated by ``apply_cap``), uniformly with the spatial strategies."""
    by_fn: dict[str, list[BeatRef]] = defaultdict(list)
    for b in beats:
        by_fn[(b.narrative_function or "establishing")].append(b)

    out: list[BeatRef] = []
    for fn in NARRATIVE_FUNCTION_ORDER:
        bucket = by_fn.pop(fn, None)
        if bucket:
            bucket.sort(key=lambda b: _beat_score(b, interest), reverse=True)
            out.extend(bucket)

    # Anything with a non-canonical narrative_function falls in last.
    leftover: list[BeatRef] = []
    for bucket in by_fn.values():
        bucket.sort(key=lambda b: _beat_score(b, interest), reverse=True)
        leftover.extend(bucket)
    out.extend(leftover)
    return out


# A 4-digit year 1000-2099 — the anchor for "do these facts flow chronologically".
_BEAT_YEAR_RE = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")


def _min_year(beat: BeatRef) -> int:
    """Earliest 4-digit year mentioned in the beat body; 9999 if it names none."""
    years = [int(y) for y in _BEAT_YEAR_RE.findall(beat.script_body or "")]
    return min(years) if years else 9999


def _order_body_chronologically(ordered: list[BeatRef]) -> list[BeatRef]:
    """Reorder a flat stop's beats so dated facts flow oldest→newest.

    The workbench "the facts are not flowing logically into one another" /
    "chronological jumble" complaint: narrative-function bucketing emitted a
    stop's beats 1983, 2003, 1190, 2026… A hoisted ``stop_orientation`` head is
    kept in place (the cold-open lookup depends on it); the remaining body is
    STABLE-sorted by earliest year, so undated beats (year 9999) keep their
    relative order and trail the dated ones. Pure reorder — no beat is dropped,
    so golden overlap (a beat-id count, order-independent) is unchanged.
    """
    if len(ordered) <= 2:
        return ordered
    head_n = 1 if (ordered[0].beat_type or "").lower() == "stop_orientation" else 0
    body = ordered[head_n:]
    body_sorted = [b for _, b in sorted(enumerate(body), key=lambda iv: (_min_year(iv[1]), iv[0]))]
    return [*ordered[:head_n], *body_sorted]


def _tier_cap(poi: POI) -> int:
    """The per-tier beat ceiling: tier-3 pause stops PAUSE_BEATS_MAX; walk-by
    tier-1/2 a single beat; dense tier-5/4 stops DEFAULT_FLAT_MAX — the ceiling the
    golden's own human-ideal roster respects at every stop."""
    if poi.tier == 3:
        return PAUSE_BEATS_MAX
    if poi.tier in (1, 2):
        return 1  # walk-by; selection.py decides whether to use it
    return DEFAULT_FLAT_MAX


def _apply_flat_cap(poi: POI, ordered: list[BeatRef]) -> list[BeatRef]:
    """Flat-strategy ceiling: keep the narrative-arc PREFIX (a thin corpus is
    delivered whole — honest length, not padded). The arc order is intentional, so
    the flat strategy keeps its front, not the globally best-scoring beats."""
    return ordered[: _tier_cap(poi)]


def _cap_spatial_by_score(
    poi: POI, ordered: list[BeatRef], interest: frozenset[str]
) -> list[BeatRef]:
    """Spatial ceiling: cover the walk's lanes, then fill by editorial score.

    A purely global top-N lets several strong complementary beats at one
    sub-location crowd every beat from a quieter sub-location out of the voiced
    tour.  It can also erase all POI-wide context/closing beats merely because
    they have no spatial key.  Reserve one bounded no-key slot, retain the best
    primary beat from as many keyed groups as the cap permits, then spend the
    remaining capacity on the best complementary beats globally.  Finally,
    filter the original plan so the survivors remain in walk order.

    This is deliberately structural rather than fixture-specific: it applies to
    both sub-location and trigger-address plans, never names a POI or beat id,
    and cannot exceed the existing tier cap.

    The head beat (index 0) is always kept: after _hoist_orientation and BEFORE
    _enforce_tone_variety (which runs after this cap), index 0 is the hoisted
    cold-open / orientation primitive, which the stop must open on regardless of
    score. Capping before tone-variety is what makes index-0 protection sufficient:
    tone-variety could otherwise swap the orientation off the head first, then a
    positional cap would protect the wrong beat and evict the cold-open."""
    cap = _tier_cap(poi)
    if len(ordered) <= cap:
        return ordered

    strategy = choose_ordering_strategy(ordered)
    def key_of(beat: BeatRef) -> str | None:
        if strategy == "sub_location":
            return beat.sub_location
        return beat.trigger_address

    by_key: dict[str, list[BeatRef]] = defaultdict(list)
    no_key: list[BeatRef] = []
    for beat in ordered:
        key = key_of(beat)
        if key:
            by_key[key].append(beat)
        else:
            no_key.append(beat)

    # Index zero may be a deliberately hoisted orientation beat.  Preserve it
    # regardless of score, as the previous implementation did.
    keep = {ordered[0].id}

    def add_best(pool: list[BeatRef], limit: int) -> None:
        room = cap - len(keep)
        if room <= 0 or limit <= 0:
            return
        available = [beat for beat in pool if beat.id not in keep]
        keep.update(beat.id for beat in _top_by_score(available, interest, min(limit, room)))

    # One POI-wide lane is guaranteed when present.  More no-key beats can still
    # earn places during the final complementary fill.
    add_best(no_key, SPATIAL_NO_KEY_RESERVE)

    # A primary represents each physical group before secondary beats compete.
    # If there are more groups than slots (e.g. a very dense square), the best
    # group primaries win deterministically rather than breaching the cap.
    primaries = [_pick_best(bucket, interest) for bucket in by_key.values()]
    add_best(primaries, len(primaries))

    # Complementary beats — including additional beats at one sub-location and
    # additional no-key context — compete together for the bounded remainder.
    add_best(ordered, cap)
    return [b for b in ordered if b.id in keep]


# --- scoring + helpers ------------------------------------------------------


def _pick_best(bucket: list[BeatRef], interest: frozenset[str]) -> BeatRef:
    return max(bucket, key=lambda b: _beat_score(b, interest))


def _top_by_score(pool: list[BeatRef], interest: frozenset[str], n: int) -> list[BeatRef]:
    """The n best-scoring beats, highest first. ``_top_by_score(pool, i, 1)[0]``
    is exactly ``_pick_best(pool, i)`` — so the no-key emission keeps the old
    single-closer beat at the front and merely adds the runners-up behind it."""
    return sorted(pool, key=lambda b: _beat_score(b, interest), reverse=True)[:n]


def _beat_score(beat: BeatRef, interest: frozenset[str]) -> tuple:
    """Stable, deterministic beat score for tie-breaking.

    Returns a tuple sorted high→low: lens-match, length-class quality,
    word-count, and lastly id to break ties.
    """
    lens_match = 1 if interest and any(lens.lower() in interest for lens in beat.lenses) else 0
    length_rank = _length_class_rank(beat.beat_length_class)
    return (lens_match, length_rank, beat.word_count or beat.est_spoken_seconds, beat.id)


def _length_class_rank(value: str | None) -> int:
    return {
        "anchor": 4,
        "mid": 3,
        "seasoning": 2,
        "micro": 1,
    }.get((value or "").lower(), 0)


def _address_sort_key(addr: str) -> tuple:
    """Sort 'no. 1', 'no. 2', 'no. 13' numerically when prefix matches.

    Falls back to alphabetic for free-form addresses (e.g. '62 rue Saint-Antoine').
    """
    head_digits: list[str] = []
    for ch in addr:
        if ch.isdigit():
            head_digits.append(ch)
        elif head_digits or ch in (".", " "):
            if head_digits and not ch.isdigit():
                break
        else:
            continue
    n = int("".join(head_digits)) if head_digits else 10**9
    return (n, addr.lower())


def reorder_final_stop_for_closing(beat_sequence: BeatSequence) -> BeatSequence:
    """Move a closing-friendly beat to the last position at the final stop.

    2026-04-29: the closing glue ("End the walk here, or carry
    on…") fires after the last beat of the last stop regardless of what
    that beat is. Earlier outputs ended mid-Tour-de-France-jersey
    (Tour 4) and mid-Mme-de-Motteville-zinger (Tour 1) because the
    closing glue followed whichever beat happened to land last in the
    spatial / narrative ordering.

    Preference: ``narrative_function='callback'`` > ``'climax'`` >
    longest beat by ``script_body`` length. The first match becomes the
    new closing slot; everything else preserves its order.

    No-op when the final stop has fewer than two beats or when the
    closing-friendly beat is already last.
    """
    if not beat_sequence.poi_beats:
        return beat_sequence
    # The terminal NARRATED stop is not necessarily poi_beats[-1]: an A→B route
    # appends a beatless synthesized-destination sentinel. Walk backwards for the
    # last plan WITH beats, mirroring selection.py's `_keep_final_closing_beat`
    # caller — the two halves of this protection must agree on the terminal.
    last_idx = next(
        (
            i
            for i in range(len(beat_sequence.poi_beats) - 1, -1, -1)
            if beat_sequence.poi_beats[i].beats
        ),
        None,
    )
    if last_idx is None:
        return beat_sequence
    last_plan = beat_sequence.poi_beats[last_idx]
    if len(last_plan.beats) < 2:
        return beat_sequence
    closing_idx = _find_closing_friendly_index(last_plan.beats)
    if closing_idx is None or closing_idx == len(last_plan.beats) - 1:
        return beat_sequence
    beats = list(last_plan.beats)
    moved = beats.pop(closing_idx)
    beats.append(moved)
    new_last = last_plan.model_copy(update={"beats": tuple(beats)})
    new_poi_beats = (
        *beat_sequence.poi_beats[:last_idx],
        new_last,
        *beat_sequence.poi_beats[last_idx + 1 :],
    )
    return beat_sequence.model_copy(update={"poi_beats": new_poi_beats})


def _find_closing_friendly_index(beats: tuple[BeatRef, ...]) -> int | None:
    """Index of the most closing-friendly beat (callback, climax, longest).

    ``stop_orientation`` beats are excluded — they're cold-open
    primitives the per-POI hoist places at position 0; relocating one
    to the closing slot would land it after the cold open already
    emitted it AND give the close a "let me orient you" feel rather
    than a wrap-up.
    """
    eligible_indices = [
        i
        for i, b in enumerate(beats)
        if (b.beat_type or "").lower() != "stop_orientation"
        and (b.narrative_function or "").lower() != "stop_orientation"
    ]
    if not eligible_indices:
        return None
    for target in ("callback", "climax"):
        for i in eligible_indices:
            if (beats[i].narrative_function or "").lower() == target:
                return i
    # Fallback: longest body among eligibles. Avoids landing closing
    # glue on a one-line factoid and gives the close narrative weight.
    best_idx = eligible_indices[0]
    best_len = -1
    for i in eligible_indices:
        body_len = len(beats[i].script_body or "")
        if body_len > best_len:
            best_len = body_len
            best_idx = i
    return best_idx


HOIST_PROXIMITY_M: float = 100.0
"""Cold-open hoist proximity gate.

A sibling-POI orientation beat may only hoist to the start stop when
the source POI is within this radius — otherwise the beat's
physical_cues describe somewhere else (Pariswalks PdV "children's
play area" landing at Hotel de Sully). 100 m matches the schema's
geofence trigger radius, which is the natural notion of "same place".
"""


def _vignette_beat_names_poi(body: str | None, poi_name: str) -> bool:
    """True when the beat's FIRST sentence — the ONLY one voiced as a walk-past
    one-liner (``generation._vignette_one_liners``) — contains the POI's name.

    A vignette one-liner is stitched into the leg INTO the next seated stop, so a
    line that does not name its own POI (e.g. "The statue of Nelson at the top was
    carved from…") reads as if it belongs to that stop — the Nelson's-Column-under-
    the-National-Gallery mis-attribution. Preferring a
    self-naming beat ("Nelson's Column is a monument in Trafalgar Square…") fixes
    it in canonical corpus text, with no glue and no new proper-noun leakage."""
    if not body or not poi_name:
        return False
    # Use the SAME splitter generation uses to voice the one-liner, so the sentence
    # checked here is exactly the sentence a tourist hears (a naive ". " split would
    # disagree on abbreviations — "St. Paul's Cathedral" — and mis-judge naming).
    # Lazy import: generation imports THIS module (cycle avoidance).
    from .generation import split_sentences

    sents = split_sentences(body)
    if not sents:
        return False
    return poi_name.casefold() in sents[0].casefold()


def select_vignette_beats(
    vignettes: dict[int, tuple[POI, ...]],
    beats_by_poi_id: dict[str, tuple[BeatRef, ...]],
    *,
    lenses: Iterable[str] | None = None,
) -> dict[int, tuple[BeatRef, ...]]:
    """ONE best voiceable beat per walk-past vignette POI.

    Builds ``BeatSequence.vignette_beats`` from ``Route.vignettes``
    + the corpus snapshot's ``beats_by_poi`` mapping. Per vignette POI: the
    first ACTIVE beat with a ``script_body``, preferring — when lenses are
    requested — one that carries a requested lens (the one-liner should speak
    to the user's genre when the corpus allows). A POI with no voiceable beat
    contributes nothing; a leg whose POIs all lack one is dropped. Beat order
    follows the vignette POI order (spotlight desc, then id).
    """
    interest = frozenset(s.lower() for s in (lenses or []))
    out: dict[int, tuple[BeatRef, ...]] = {}
    for leg_idx, pois in vignettes.items():
        chosen: list[BeatRef] = []
        for poi in pois:
            voiceable = [
                b
                for b in beats_by_poi_id.get(poi.id, ())
                if (b.active_status or "active") == "active" and b.script_body
            ]
            if not voiceable:
                continue
            # Prefer a SELF-NAMING beat (its first sentence names the POI) so the
            # walk-past one-liner is never mis-read as the seated stop's content;
            # within the preferred pool still honour a requested lens, else take the
            # first. No self-naming beat -> the prior behaviour (lensed else first).
            naming = [b for b in voiceable if _vignette_beat_names_poi(b.script_body, poi.name)]
            # Within the self-naming pool prefer the SHORTEST first sentence — a tighter
            # walk-past line — with a requested lens still the primary key below. (No-op
            # for a POI with one self-naming beat; free insurance where several exist.)
            if naming:
                from .generation import split_sentences

                pool = sorted(
                    naming, key=lambda b: len(split_sentences(b.script_body or "")[0].split())
                )
            else:
                pool = voiceable
            lensed = next(
                (b for b in pool if any(ln.lower() in interest for ln in b.lenses)),
                None,
            )
            chosen.append(lensed if lensed is not None else pool[0])
        if chosen:
            out[leg_idx] = tuple(chosen)
    return out


def find_area_orientation_beat(
    beat_sequence: BeatSequence,
    route: Route,
    *,
    start_idx: int = 0,
    interest_lenses: Iterable[str] | None = None,
) -> BeatRef | None:
    """Search the Route for a stop_orientation beat that can hoist to cold-open.

    2026-04-29: the per-POI hoist in ``select_poi_beats`` only surfaces an
    orientation beat when one exists at the start POI. Most tier-5 anchors
    lack one, so the cold-open falls back to the SYNTHESIZED_OPENER even when
    a sibling POI in the same Area carries the canonical Pariswalks-format opener.

    A geographic-honesty guard (2026-04-29) limits that. The unguarded
    hoist promoted any orientation beat from the same Area as
    the start stop, which produced Tour 1 stop 1 emitting the Pariswalks
    "find a bench in the garden, near the children's play area" beat at
    Hotel de Sully — those features exist at Place des Vosges (the
    sibling POI), not at the start. The refined rule accepts a
    candidate only when:

    - (a) the beat's POI matches the start POI (the original
      behaviour; preferred), OR
    - (b) the beat has no physical_cues at all (treat as Area-generic), OR
    - (c) the beat's source POI is within ``HOIST_PROXIMITY_M`` of the
      start stop (cues describe nearby features the listener can see).

    When no candidate passes, returns ``None`` — generation falls
    through to the SYNTHESIZED_OPENER, which is the geographically
    honest answer.
    """
    poi_beats = beat_sequence.poi_beats
    if not poi_beats or start_idx >= len(poi_beats):
        return None
    pois_by_id = {p.id: p for p in route.pois}
    start_poi = pois_by_id.get(poi_beats[start_idx].poi_id)
    if start_poi is None:
        return None
    start_areas = {a for a in start_poi.areas if a}
    if not start_areas:
        return None
    interest = frozenset(s.lower() for s in (interest_lenses or []))

    from .routing import haversine_m  # local import; no cycle risk

    candidates: list[BeatRef] = []
    for idx, plan in enumerate(poi_beats):
        if idx == start_idx:
            continue  # already searched per-POI; this is the cross-POI path
        peer = pois_by_id.get(plan.poi_id)
        if peer is None:
            continue
        if not (start_areas & {a for a in peer.areas if a}):
            continue
        peer_distance_m = haversine_m(start_poi.lat, start_poi.lng, peer.lat, peer.lng)
        for beat in plan.beats:
            is_orientation = (beat.beat_type or "").lower() == "stop_orientation" or (
                beat.narrative_function or ""
            ).lower() == "stop_orientation"
            if not is_orientation:
                continue
            if not _hoist_geographically_honest(
                beat,
                beat_poi_id=peer.id,
                start_poi_id=start_poi.id,
                peer_distance_m=peer_distance_m,
            ):
                continue
            candidates.append(beat)
    if not candidates:
        return None
    return _pick_best(candidates, interest)


def _hoist_geographically_honest(
    beat: BeatRef,
    *,
    beat_poi_id: str,
    start_poi_id: str,
    peer_distance_m: float,
) -> bool:
    """The geographic-honesty gate. See ``find_area_orientation_beat`` docstring."""
    if beat_poi_id == start_poi_id:
        return True  # same POI — the original behaviour
    if not beat.physical_cues:
        return True  # no cues → Area-generic; safe to hoist
    return peer_distance_m <= HOIST_PROXIMITY_M


def _hoist_orientation(
    ordered: list[BeatRef],
    active: list[BeatRef],
    interest: frozenset[str],
) -> list[BeatRef]:
    """Surface a stop_orientation beat at the head when one exists.

    The cold-open in generation.py looks for a beat with
    ``beat_type='stop_orientation'``. Under the sub_location and
    trigger_address strategies, those beats land in the no-spatial-key
    bucket and lose the single closing slot to anchor-class beats.
    This hoist preserves the orientation beat as a head primitive so
    the cold-open lookup actually finds it. If the orientation beat is
    already in ``ordered``, it's moved (not duplicated) to position 0.
    """
    orientations = [b for b in active if (b.beat_type or "").lower() == "stop_orientation"]
    if not orientations:
        return ordered
    chosen = _pick_best(orientations, interest)
    rest = [b for b in ordered if b.id != chosen.id]
    return [chosen, *rest]


def _apply_b8_lite_dedup(
    beats: list[BeatRef], interest: frozenset[str], *, protected_id: str | None = None
) -> list[BeatRef]:
    """Drop near-duplicate beats.

    Two beats collide when they share at least one lens (or are both
    lensless) AND either entities Jaccard ≥ B8_JACCARD_THRESHOLD or
    subject_tag-token Jaccard ≥ threshold. On collision the loser is:

    1. lower beat-select score (the existing tuple);
    2. then shorter ``script_body``;
    3. then lower narrative_function priority
       (climax > deepen > establishing > hook > scene_setter
        > callback > transition).

    ``protected_id`` (the hoisted stop_orientation cold-open head) is never
    selected as the loser: when it would lose a collision, the OTHER beat is
    dropped instead so the cold-open primitive always survives.

    Survives in original order; this is paraphrase removal, not
    re-ordering.

    Logs a debug line via ``_DEDUP_TRACE`` when a drop fires so the
    smoke harness can audit which beats lost.
    """
    if len(beats) < 2:
        return beats

    drop_ids: set[str] = set()
    for i, a in enumerate(beats):
        if a.id in drop_ids:
            continue
        for b in beats[i + 1 :]:
            if b.id in drop_ids:
                continue
            if not _share_lens(a, b):
                continue
            if not _claims_collide(a, b):
                continue
            loser = _pick_dedup_loser(a, b, interest)
            if protected_id is not None and loser.id == protected_id:
                # The cold-open head must survive — drop the other beat instead.
                loser = b if loser is a else a
            drop_ids.add(loser.id)
            _DEDUP_TRACE.append(
                {
                    "kept": (a.id if loser is b else b.id),
                    "dropped": loser.id,
                    "entities_jaccard": _entities_jaccard(a, b),
                    "subject_tag_jaccard": _subject_tag_jaccard(a, b),
                }
            )

    return [b for b in beats if b.id not in drop_ids]


# Optional debug trace (per-process, cleared by ``reset_dedup_trace``).
# The smoke harness reads this so the user can see which paraphrase
# pairs collapsed without parsing logs.
_DEDUP_TRACE: list[dict] = []


def reset_dedup_trace() -> None:
    _DEDUP_TRACE.clear()


def get_dedup_trace() -> list[dict]:
    return list(_DEDUP_TRACE)


def _share_lens(a: BeatRef, b: BeatRef) -> bool:
    """Beats with no lens collide with each other; otherwise need an overlap."""
    set_a = {x.lower() for x in a.lenses if x}
    set_b = {x.lower() for x in b.lenses if x}
    if not set_a and not set_b:
        return True
    return bool(set_a & set_b)


def _claims_collide(a: BeatRef, b: BeatRef) -> bool:
    if _entities_jaccard(a, b) >= B8_JACCARD_THRESHOLD:
        return True
    if _subject_tag_jaccard(a, b) >= B8_JACCARD_THRESHOLD:
        return True
    # Char-5-gram body Jaccard catches paraphrase pairs that
    # the entity threshold misses (Mesme Gallet, Hugo affair).
    if _char_ngram_jaccard(a, b) >= B8_CHAR_NGRAM_THRESHOLD:
        return True
    # Same year + ≥2 shared canonical entities catches
    # "same founding story, divergent prose" pairs (Pantheon vow).
    return bool(_shares_year_and_canonical_entities(a, b))


def _entities_jaccard(a: BeatRef, b: BeatRef) -> float:
    set_a = {e.strip().lower() for e in a.entities if e and e.strip()}
    set_b = {e.strip().lower() for e in b.entities if e and e.strip()}
    return _jaccard(set_a, set_b)


def _subject_tag_jaccard(a: BeatRef, b: BeatRef) -> float:
    tokens_a = _tokenise(a.subject_tag)
    tokens_b = _tokenise(b.subject_tag)
    return _jaccard(tokens_a, tokens_b)


def _char_ngram_jaccard(a: BeatRef, b: BeatRef) -> float:
    """Character n-gram Jaccard on script_body.

    Catches paraphrases the entity-Jaccard misses — e.g., the Mesme
    Gallet / Hugo affair pairs where two extractors paraphrased the
    same anecdote at different lengths.
    """
    body_a = a.script_body or ""
    body_b = b.script_body or ""
    if not body_a or not body_b:
        return 0.0
    return _jaccard(_char_ngrams(body_a), _char_ngrams(body_b))


def _char_ngrams(s: str) -> set[str]:
    s = re.sub(r"\s+", " ", s.lower()).strip()
    n = B8_CHAR_NGRAM_N
    if len(s) < n:
        return {s} if s else set()
    return {s[i : i + n] for i in range(len(s) - n + 1)}


def _shares_year_and_canonical_entities(a: BeatRef, b: BeatRef) -> bool:
    """True iff both beats share at least one 4-digit year token in their
    script_body AND at least ``B8_CANONICAL_ENTITY_PAIR_MIN`` canonicalized
    entities. Targets the Pantheon-vow case where divergent supporting
    casts swamp the entity Jaccard but the lede ("In 1744, Louis XV …
    Saint Genevieve") is identical.

    Canonicalization: lowercase + ASCII-fold + strip leading sentence-
    starter words ("Built", "The") + normalise "Saint X" / "St. X" /
    "St X" to a single form so "Saint Genevieve" and "St. Geneviève"
    collide.
    """
    canon_a = _canonical_entities(a)
    canon_b = _canonical_entities(b)
    if len(canon_a & canon_b) < B8_CANONICAL_ENTITY_PAIR_MIN:
        return False
    years_a = _year_tokens(a.script_body)
    years_b = _year_tokens(b.script_body)
    return bool(years_a & years_b)


_LEADING_SENTENCE_STARTERS: frozenset[str] = frozenset(
    {"built", "the", "a", "an", "during", "by", "after", "upon", "since", "founded"}
)
_SAINT_TOKEN_NORMALISATION: dict[str, str] = {"st": "saint", "st.": "saint"}


def _canonical_entities(beat: BeatRef) -> set[str]:
    out: set[str] = set()
    for entity in beat.entities or ():
        canon = _canonicalise_entity(entity)
        if canon:
            out.add(canon)
    return out


def _canonicalise_entity(s: str | None) -> str | None:
    if not s:
        return None
    folded = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    folded = folded.lower().strip(" .,;:\"'")
    if not folded:
        return None
    parts = [p for p in folded.split() if p]
    # Strip a single leading sentence-starter (e.g., "Built" leaked from
    # an extractor's first-word capitalisation), but only when there's a
    # real entity word after it — never reduce a multi-token entity to nothing.
    if len(parts) > 1 and parts[0] in _LEADING_SENTENCE_STARTERS:
        parts = parts[1:]
    parts = [_SAINT_TOKEN_NORMALISATION.get(p, p) for p in parts]
    return " ".join(parts) if parts else None


def _year_tokens(text: str | None) -> set[str]:
    if not text:
        return set()
    return set(re.findall(r"\b(1[0-9]{3}|20[0-2][0-9])\b", text))


def _tokenise(s: str | None) -> set[str]:
    if not s:
        return set()
    return {t for t in (chunk.strip().lower() for chunk in s.split()) if t}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0  # neither has data → not a collision
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _pick_dedup_loser(a: BeatRef, b: BeatRef, interest: frozenset[str]) -> BeatRef:
    """Return the beat to drop. Tie-breakers per the dedup docstring above."""
    score_a = _beat_score(a, interest)
    score_b = _beat_score(b, interest)
    if score_a > score_b:
        return b
    if score_b > score_a:
        return a

    len_a = len(a.script_body or "")
    len_b = len(b.script_body or "")
    if len_a > len_b:
        return b
    if len_b > len_a:
        return a

    nf_a = _NARRATIVE_FUNCTION_PRIORITY.get((a.narrative_function or "").lower(), 0)
    nf_b = _NARRATIVE_FUNCTION_PRIORITY.get((b.narrative_function or "").lower(), 0)
    if nf_a > nf_b:
        return b
    if nf_b > nf_a:
        return a

    # Final stable fallback: drop the lexicographically-greater id so the
    # outcome is deterministic for tests.
    return b if a.id < b.id else a


def _tone_group_key(strategy: OrderingStrategy) -> Callable[[BeatRef], str | None] | None:
    """The spatial grouping tone-variety must not reorder across, per strategy.

    ``narrative_function`` is not spatially sequenced, so it has no grouping to
    preserve and keeps the whole-stop interleave (None).
    """
    if strategy == "sub_location":
        return lambda b: b.sub_location
    if strategy == "trigger_address":
        return lambda b: b.trigger_address
    return None


def _enforce_tone_variety(
    beats: list[BeatRef],
    group_of: Callable[[BeatRef], str | None] | None = None,
) -> list[BeatRef]:
    """Avoid 3 consecutive somber/reverent beats (tone variety).

    Constructive interleave: bucket the beats into somber/reverent vs.
    relief (everything else), preserving each bucket's stable input order.
    Emit at most two consecutive somber beats before forcing a relief beat
    whenever any relief remains; only when relief is exhausted do we fall
    back to the honest (still-somber) tail. This provably contains no run of
    three consecutive somber/reverent beats whenever relief beats exist to
    break every run — unlike a local-swap heuristic, it cannot ping-pong
    between a leading and a trailing run.

    ``group_of`` (2026-07-19) confines the interleave to one contiguous spatial
    group at a time. A sub_location / trigger_address plan is sequenced address
    by address, and the whole-stop interleave reordered beats ACROSS those
    groups: at the Conciergerie it emitted sub-anchor ``rue-de-paris-cells``
    twice, non-contiguously, split by marie-antoinette-cell and
    kitchen-pavilion — walking the listener away from a spot and back to it.
    Note ``_order_body_chronologically`` is already guarded to the flat strategy
    for exactly this reason; tone-variety was not.

    Tradeoff, deliberate: a wholly-somber group can still run past two, and a
    run may join across a group boundary. For a spatially-sequenced stop, walk
    coherence outranks tone variety — the alternative is teleporting the listener.
    """
    if group_of is not None:
        out: list[BeatRef] = []
        run_beats: list[BeatRef] = []
        run_key: object = object()  # sentinel: never equal to a real key
        for beat in beats:
            key = group_of(beat)
            if run_beats and key == run_key:
                run_beats.append(beat)
                continue
            if run_beats:
                out.extend(_enforce_tone_variety(run_beats))
            run_beats, run_key = [beat], key
        if run_beats:
            out.extend(_enforce_tone_variety(run_beats))
        return out

    somber_registers = {"somber", "reverent"}

    def is_somber(b: BeatRef) -> bool:
        return (b.emotional_register or "").lower() in somber_registers

    somber = [b for b in beats if is_somber(b)]
    relief = [b for b in beats if not is_somber(b)]

    # No violation is possible without at least three somber beats, or when
    # there is no relief to interleave — return the input order untouched.
    if len(somber) < 3 or not relief:
        return list(beats)

    out: list[BeatRef] = []
    si = ri = 0
    run = 0  # length of the current trailing somber run in `out`
    while si < len(somber) or ri < len(relief):
        force_relief = run >= 2 and ri < len(relief)
        if force_relief or (si >= len(somber) and ri < len(relief)):
            out.append(relief[ri])
            ri += 1
            run = 0
        else:
            out.append(somber[si])
            si += 1
            run += 1
    return out
