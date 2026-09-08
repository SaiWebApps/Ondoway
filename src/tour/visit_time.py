"""How long a visitor spends AT a place — the third hand on the tour clock.

Time at a stop is min(what the place absorbs, what this visitor's interest
justifies, the party's ceiling). All three arguments matter: Camille
(historic_arch) spends 33 minutes in the Cour Carrée where Théo (dark_history)
spends 8, and a chocolate museum is 90 minutes for one visitor and a walk-past
for the next. See docs/personas/.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import TYPE_CHECKING, Literal

from .contract import POI, PromiseShape

if TYPE_CHECKING:
    # CorpusSnapshot lives in selection.py, NOT contract.py — and it is imported
    # only for the annotation, because step 3B.8 has selection importing the
    # combining rule back out of this module. A real import both ways is a cycle.
    # Same shape as src/tour/density.py, which solved this first.
    from .selection import CorpusSnapshot

#: A one-hop lens match unlocks this fraction of the gap between the outside and
#: inside capacities. Mirrors LENS_ADJACENCY_ONE_HOP = 0.6 rather than inventing
#: a second scale.
ONE_HOP_VISIT_FRACTION: float = 0.6

#: Rain halves what standing in an UNCOVERED place is worth (plan S3.3,
#: deviation iv). The queue is exempt: the line takes what it takes, whatever
#: falls on it.
RAIN_DWELL_FRACTION: float = 0.5

#: The place categories (contract row 6.7 vocabulary) that shelter a visit.
#: Everything else — square, garden, street, bridge, park, market, monument,
#: other, and the "" of an unjudged corpus — counts as OPEN, the safe direction
#: for rain (plan S3.3, deviation i): never promise shelter the data cannot
#: back. A wrong "open" under-prices a dry arcade; a wrong "covered" strands a
#: visitor in the rain.
_COVERED_CATEGORIES: frozenset[str] = frozenset({"arcade", "museum", "church", "gallery"})

#: The queue_class values that CLAIM a line exists. None is no claim at all
#: (the audited pass has not reached this POI) and "none" is the audited claim
#: that there IS no line (contract row 6.5) — neither prices a second of wait,
#: and neither gives a `wall` end anything to refuse.
_QUEUE_CLAIMS: frozenset[str] = frozenset({"short", "long", "unpredictable"})


def place_is_covered(poi: POI) -> bool:
    """THE covered/open decision — rain pricing's only input, defined once.

    The plan names the sabotage this guards against: "a second coveredness map
    in selection.py". `tests/test_one_promise_pricing.py` scans the engine for
    any other file that speaks of cover while naming `place_category`.
    """
    return poi.place_category in _COVERED_CATEGORIES


def _hh_mm_to_hours(text: str) -> float:
    """"10:30" -> 10.5. Raises ValueError on anything else; callers fail open."""
    hours, minutes = text.split(":")
    return int(hours) + int(minutes) / 60


def _hour_in_peak_band(bands_json: str, clock_hour: int) -> bool:
    """Does an arrival at `clock_hour` fall inside a queue peak band?

    Parses the JSON-encoded `[["HH:MM", "HH:MM"], ...]` the graph stores, with
    the same fail-open discipline as the opening-hours parser
    (`_clock_exclusion_reason`, selection.py): a table that does not parse, a
    malformed window, an unknown shape — all mean NO peak data, so the price
    falls to off-peak, the milder claim. A data defect must under-price a wait,
    never invent one. The structural bars in `tests/test_poi_queues.py` guard
    the data itself.

    Bands are half-open — `start <= hour < end` — mirroring the opening-hours
    overlap test, so 10:00 sits before a 10:30-16:00 band and 16:00 after it.
    """
    bands = _parse_peak_bands(bands_json)
    if bands is None:
        return False
    return any(start <= clock_hour < end for start, end in bands)


@lru_cache(maxsize=1024)
def _parse_peak_bands(bands_json: str) -> tuple[tuple[float, float], ...] | None:
    """Parse-once cache for a POI's stored peak bands; None = no peak data.

    CACHED because the repair prices thousands of trials per request and each
    trial re-prices every stop at its arrival hour: parsing the same ~370
    corpus strings per call multiplied a JSON decode into the planner's hot
    path (measured as part of a 4-8x wall-clock blowup at W3.2). The corpus
    holds at most one distinct string per POI, so the cache is tiny and
    permanent.
    """
    try:
        bands = json.loads(bands_json)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(bands, list):
        return None
    parsed: list[tuple[float, float]] = []
    for window in bands:
        if (
            not isinstance(window, list)
            or len(window) != 2
            or not all(isinstance(t, str) for t in window)
        ):
            return None  # malformed window → no peak data → off-peak
        try:
            start, end = (_hh_mm_to_hours(t) for t in window)
        except ValueError:
            return None
        parsed.append((start, end))
    return tuple(parsed)


def _queue_seconds(poi: POI, clock_hour: int | None) -> int:
    """Seconds of line this arrival stands, from the row-6.5 corpus fields.

    "short"/"long" price by arrival-hour band: `queue_minutes_peak` inside a
    `queue_peak_hours` band, `queue_minutes_offpeak` outside one. An undated
    request (`clock_hour` None) prices at OFF-PEAK — no hour claimed means the
    milder claim, because an undated plan that budgeted every line at its worst
    hour would refuse days that are actually fine. "unpredictable" prices at
    `queue_minutes_peak` regardless of hour: the only honest single number for
    an unboundable wait is its worst known bound.
    """
    if poi.queue_class not in _QUEUE_CLAIMS:
        return 0
    if poi.queue_class == "unpredictable":
        return poi.queue_minutes_peak * 60
    if clock_hour is not None and _hour_in_peak_band(poi.queue_peak_hours, clock_hour):
        return poi.queue_minutes_peak * 60
    return poi.queue_minutes_offpeak * 60


def visit_shape(
    poi: POI,
    interest: frozenset[str],
    snapshot: CorpusSnapshot | None,
    *,
    party_ceiling_seconds: int | None = None,
    clock_hour: int | None = None,
    closed_today: bool = False,
    weather: Literal["dry", "rain"] | None = None,
    wall: bool = False,
    exterior_only: bool = False,
) -> PromiseShape:
    """THE promise pricing — what standing at this place costs, as a shape.

    Camille's Sainte-Chapelle is 38 minutes of chapel and 28 minutes of line,
    and "price the building at a single 66 and the line becomes permanent"
    (docs/personas/01-architecture-pilgrim.md:77-84) — so the cost is three
    numbers, not one: outside seconds, inside seconds, and a queue that belongs
    to the hour rather than the building (design §3.3).

    ``snapshot`` may be None ONLY when no interest was declared, because that is
    the one case the lens graph is never consulted for. Callers without a corpus
    — density's legacy diagnostic form is the only one — must pass an empty
    interest rather than a snapshot of None with lenses, which would otherwise
    silently price every place as a lens miss.

    Decision order, each rule test-pinned in tests/test_one_promise_pricing.py:
    1. the outside/inside split (today's blend arithmetic, extracted, then the
       party ceiling); 2. queue by arrival-hour band; 3. a queue the ceiling
       cannot cover collapses the stop to outside-only — nobody stands 28
       minutes of line for the zero minutes the ceiling has left; 4. a `wall`
       end refuses ANY audited line ("no stop whose duration is unboundable",
       design §2.3) but never the place; 5. `closed_today` voids the door and
       keeps the exterior (delete-vs-demote, panel W1.9 dissent 1);
       5b. `exterior_only` is THE DAY'S OWN CLOCK saying the same thing — the
       planner's last-resort "step outside" repair move, for a stop whose
       interior the requested minutes cannot hold; 6. rain
       halves an uncovered place's dwell worth, never its queue.

    Rules 3, 4, 5 and 5b are ONE collapse with four ways in, deliberately: the
    stop survives as its exterior, the door and the door's line drop, the facade
    is kept. A second exterior rule written anywhere else is the fork
    `tests/test_one_promise_pricing.py` scans the engine for.
    """
    from .selection import _lens_relation  # single source of truth for the hop model

    if snapshot is None and interest:
        raise ValueError(
            "visit_seconds needs a CorpusSnapshot to price a declared interest; "
            "pass an empty interest instead of guessing a lens relation"
        )

    outside = poi.typical_duration_min * 60
    inside = poi.visit_seconds_inside
    if not poi_has_interior(poi):
        blend = outside
    else:
        relation = _lens_relation(poi, interest, snapshot)
        if relation == "direct":
            blend = inside
        # "no_lens" is DELIBERATELY not "direct" here, unlike _lens_adjacency at
        # selection.py:3405-3418. Uniform 1.0 is right for SELECTION — do not
        # penalise someone for not choosing. Giving them the maximum interior at
        # every place is wrong for DWELL: it hands a family with a five-year-old
        # a 45-minute cathedral. Same classifier, two questions, two answers.
        elif relation in ("one_hop", "no_lens"):
            blend = round(outside + (inside - outside) * ONE_HOP_VISIT_FRACTION)
        else:
            blend = outside

    # An interior share was JUSTIFIED iff the uncapped blend exceeded the
    # outside number — the ceiling may then eat it, but the visitor still
    # reaches the door, so the door's line still applies.
    goes_inside = blend > outside
    base_visit = blend if party_ceiling_seconds is None else min(blend, party_ceiling_seconds)
    outside_seconds = min(outside, base_visit)
    inside_seconds = base_visit - outside_seconds

    # Only someone who reaches the door stands in its line: an outside-only
    # visitor skips the queue, which is exactly what a single folded "66"
    # could never let them do.
    queue_seconds = _queue_seconds(poi, clock_hour) if goes_inside else 0

    if (
        closed_today
        # A wall refuses ANY audited line, not only the unpredictable class:
        # Marcus is four minutes from £180 (04-layover-sprinter.md:45-47).
        or (wall and poi.queue_class in _QUEUE_CLAIMS)
        # THE DAY'S OWN CLOCK. The other three triggers are facts about the
        # PLACE (a locked door, an unboundable line) or about the PARTY (a stop
        # ceiling); this is the only one the requested DURATION can raise, and
        # it is what lets the planner say "you will not have time to go in, but
        # the place is still worth standing at" instead of refusing the day.
        # Set per stop by the timebox repair's fourth move, as a last resort.
        or exterior_only
        # The queue is indivisible and the ceiling covers the WHOLE stop; when
        # visit + wait exceed it, the visitor does not join the line. Never
        # triggers without a queue, since base_visit is already ceiling-capped.
        or (
            party_ceiling_seconds is not None
            and base_visit + queue_seconds > party_ceiling_seconds
        )
    ):
        # Outside-only, NEVER deleted: the stop survives as the exterior view
        # ("excluded entirely under wall" excludes the line and the interior,
        # design §3.3; a closed museum is still worth standing in front of).
        goes_inside = False
        inside_seconds = 0
        queue_seconds = 0
        outside_seconds = (
            outside if party_ceiling_seconds is None else min(outside, party_ceiling_seconds)
        )

    # A covered CATEGORY shelters the visit; a voided door has no visit. A
    # museum the clock closed — or the day's own repair stepped outside of —
    # is a facade in the weather, so its outdoor stand pays the rain penalty
    # like any square (Docs/adr/0004's acceptance finding: the closed Louvre
    # was a rainy day's longest outdoor stand, exempt because its category
    # said covered).
    door_voided = closed_today or exterior_only
    if weather == "rain" and (not place_is_covered(poi) or door_voided):
        outside_seconds = round(outside_seconds * RAIN_DWELL_FRACTION)
        inside_seconds = round(inside_seconds * RAIN_DWELL_FRACTION)

    return PromiseShape(
        outside_seconds=outside_seconds,
        inside_seconds=inside_seconds,
        queue_seconds=queue_seconds,
        goes_inside=goes_inside,
        closed_today=closed_today,
    )


def shape_total_seconds(shape: PromiseShape) -> int:
    """A stop's whole cost — THE one spelling of the components sum.

    `tests/test_one_promise_pricing.py` scans the engine for any second
    `outside + inside + queue` expression, because two expressions of one
    quantity agree only until one of them is edited.
    """
    return shape.outside_seconds + shape.inside_seconds + shape.queue_seconds


def shape_at_place_seconds(shape: PromiseShape) -> int:
    """Time AT the place — the line excluded. THE one spelling of that sum.

    A genuinely different quantity from `shape_total_seconds`, not a shortcut
    around it: design §3.3 forbids folding the wait into the visit ("price
    the building at a single 66 and the line becomes permanent"), so a
    surface that prints an at-place number beside a queue column needs this
    exact split. The harness's shape column is the founding consumer.
    """
    return shape.outside_seconds + shape.inside_seconds


def visit_ceiling_seconds(poi: POI, *, party_ceiling_seconds: int | None = None) -> int:
    """The MOST a visitor could justify standing at this place — its shape ceiling.

    Design §3.1's shape has a top: the full interior for a place with one (the
    "direct" relation already prices it there; a one-hop or no-lens visitor is
    priced 60 % of the way, so that gap is their headroom), and the outside number
    itself for a place with none — a square is not lengthened by a dial, because a
    planner giving Place de la Concorde forty-five minutes is "walking you somewhere
    pointless" (design §8.3); Camille's twenty silent minutes there are HER choice.
    The party ceiling caps it like every other visit (a family's six-minute stop).

    THE one spelling of the ceiling: the drop primitive's redistribution (Phase 5
    S5.5, W4.2 locked semantics 4 — "freed minutes flow to the anchors within shape
    ceilings") reads it, and nothing else may spell "as long as this place can be".
    """
    outside = poi.typical_duration_min * 60
    top = poi.visit_seconds_inside if poi_has_interior(poi) else outside
    if party_ceiling_seconds is not None:
        top = min(top, party_ceiling_seconds)
    return int(top)


def poi_has_interior(poi: POI) -> bool:
    """Whether this place's interior is worth more than its outside view —
    THE one spelling of the uncapped interior test (`visit_shape`'s own gate
    on the blend, and any surface's in/out label when no shape was priced).
    """
    outside = poi.typical_duration_min * 60
    return poi.visit_seconds_inside is not None and poi.visit_seconds_inside > outside


def visit_seconds(
    poi: POI,
    interest: frozenset[str],
    snapshot: CorpusSnapshot | None,
    *,
    party_ceiling_seconds: int | None = None,
) -> int:
    """Seconds this visitor spends AT this place — `visit_shape`'s total.

    A delegation, not a second pricer: the identity defaults (no clock, not
    closed, no weather, no wall) reproduce today's number byte-for-byte on an
    unpassed corpus, and on a queue-passed one the total already carries the
    off-peak line — one definition, so no gate can budget the visit while the
    tourist also pays the wait.
    """
    shape = visit_shape(poi, interest, snapshot, party_ceiling_seconds=party_ceiling_seconds)
    return shape_total_seconds(shape)


def listened_seconds(audio_seconds: int, listening_rate: float = 1.0) -> int:
    """What a piece of narration COSTS this person, in seconds — its stated length
    scaled by the learned listening rate (design §4.1: Paulo replays and consumes
    narration at ~1.5x; the remaining day's estimates scale to the observed rate).
    THE one place the rate meets narration on the server: the final gate, every
    trial under a replan and the session's clock all price through it (Phase 5
    S5.10), so the certified day and the wire clock agree by construction. 1.0 is
    the identity. **Extends** `stop_seconds`, which then takes the longer of the
    visit and THIS."""
    return round(audio_seconds * listening_rate)


def stop_seconds(visit_seconds: int, audio_seconds: int) -> int:
    """The one rule for how long a stop lasts.

    Each caller supplies its own audio number — the greedy has no Route during
    insertion and must price through the governor allowance, while the final
    gate and generation price through ``build_poi_beat_plans_capped``, and the
    density gate prices an uncapped pool. THE COMBINING is what must not fork;
    the audio sources legitimately differ and saying otherwise would be a lie.

    A stop is never shorter than what it says — the tour cannot cut its own
    narration off mid-sentence — and never shorter than what the place and the
    visitor jointly justify. So it is the longer of the two, and a long silence
    inside it is CORRECT: Camille stands twenty minutes at Concorde for four
    minutes of audio (docs/personas/01-architecture-pilgrim.md, step 5).

    Defined here rather than at the final gate because the density gate needs it
    first, and a second copy written at the second use is exactly the defect
    this phase exists to remove.
    """
    return max(visit_seconds, audio_seconds)


def served_elapsed_seconds(walk_seconds: int, dwell_seconds: int) -> int:
    """How long this tour takes — THE ONE COMPOSITION.

    A two-term sum barely looks worth a function, and that is precisely why it
    grew two spellings: the planner's final band gate composed it in selection.py
    and the rubric's C7 composed it again in quality_rubric.py. Two expressions of
    one quantity agree until one of them is edited, and then a tour is certified at
    one length and judged at another.

    The DWELL each caller passes legitimately differs and must not be forced to
    match: the planner has a corpus and no Script, so it prices stops through
    ``selection.served_dwell_seconds``; the rubric has the Script generation
    actually produced and reads ``dwell_seconds`` straight off its stops. Those are
    two honest views of the same tour at two moments. THE COMBINING is what forks,
    so the combining is what lives here.

    Walking is the whole walk, never a share of it: narration heard on the move is
    free (product ruling 2026-07-19) because it happens inside a walk this sum
    already counts.
    """
    return walk_seconds + dwell_seconds
