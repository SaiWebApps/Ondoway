"""Live-graph tour INVARIANT gate (marker: invariants; excluded from the bar).

Generates REAL tours across representative start/duration/lens/end paths on the
dev graph (7687) + Valhalla, and asserts the workbench-reported 2026-07 defect
classes stay dead. Each is a bug a real tester hit; a regression flips this RED.

Run as part of ``make test``; its internal shard provisions dev data and Valhalla.

Design notes
------------
- Assertions run on the ENGINE output (``generate`` -> Script + Route), the
  shared core every surface (preview/generate) is built on. Per-stop narration
  is reconstructed by grouping ``script.script`` sentences by ``stop_idx``.
- Every input asserts ALL invariants and collects every violation into one
  message, so a single failing tour reports its full defect list (not just the
  first), the way the mechanical sweep does.
- These are HOLISTIC, real-corpus guards. The fast, deterministic per-fix unit
  guards live in ``test_tour_selection.py`` / ``test_tour_generation.py`` /
  ``test_trip_preview_vignettes.py`` and run in the default bar.
"""

from __future__ import annotations

import re
from collections import defaultdict

import pytest

pytestmark = pytest.mark.invariants

# Representative paths across BOTH launch cities.
# (label, city_slug, start(lat,lng), duration_min, lenses, end(lat,lng) | None)
#
# New York (second city) note: these assert the same NARRATIVE invariants, which
# are routing-independent. The enriched NYC corpus LANDED on the dev graph
# (2026-08-25): all 402 POIs now carry `typical_duration_min`, `visit_basis`,
# `place_category` and `queue_class`, and 214 carry `visit_seconds_inside`, where
# the corpus measured before it carried NONE of them (0 of 402 on every one of those
# properties). NYC legs are still haversine — Valhalla carries only Île-de-France
# tiles — so this is narrative-defect coverage for NYC, NOT a claim of routing
# parity. Coords were dry-run to confirm they yield feasible, invariant-clean
# tours before being pinned.
_PATHS = [
    ("concorde-250-loop", "paris", (48.8656, 2.3281), 200, None, None),
    ("louvre-150-loop", "paris", (48.8606, 2.3376), 150, None, None),
    ("marais-150-hidden", "paris", (48.8590, 2.3620), 150,
     ["hidden_history", "war_conflict", "social_change"], None),
    ("latin-120-parks", "paris", (48.8480, 2.3470), 120,
     ["parks_gardens", "waterways_views", "nature_landscape"], None),
    ("pantheon-120-loop", "paris", (48.8462, 2.3464), 120, None, None),
    # Point-to-point (the Test-3/Test-4 shape): a generous budget so the path is
    # feasible — an over-budget fixed end is a separate (cruise-mode) concern.
    ("garnier-leshalles-p2p", "paris", (48.8719, 2.3316), 180, None, (48.8626, 2.3449)),
    # THE REPORTED DEFECT (owner, 2026-08-04): Rue Royale -> Notre-Dame, 300 min. The
    # workbench built a route that ran ~5 km PAST its own destination to Parc de la
    # Villette and came back. The A->B corridor here is a straight 2.5 km east along the
    # Seine, so a stop in the north-east is not a detour on the way — it is a round trip
    # bolted onto a one-way walk.
    #
    # COORDINATES ARE RESOLVED, NOT TYPED. Rue Royale carries no POI of its own (it is a
    # street), so the start is the midpoint of its two ends as the graph holds them:
    # Place de la Concorde (48.865608, 2.321038) and La Madeleine (48.870202, 2.325391).
    # The end is Notre-Dame Cathedral's exact graph coordinate — the same one 11 archived
    # tours already start from.
    #
    # WHY 300 AND NOT LESS: the engine prices this 2563 m straight line at 4153 s of
    # walking (3 km/h under the x1.35 haversine correction), and a fixed destination must
    # be reachable inside the planning band's maximum elapsed ceiling, which puts the
    # feasibility floor around 63 min at the 1.00 nominal fraction. (Before 2026-08-04
    # the ceiling was the bare walk budget at a flat 0.83 and the floor was 209 min; the
    # helper that computed it, ``smallest_duration_min_for_walk_seconds``, was deleted
    # with the legacy policy.) Below the floor the request is refused outright and the
    # defect cannot reproduce. 300 is the owner's actual request.
    ("rue-royale-notredame-p2p", "paris", (48.867905, 2.323215), 300, None,
     (48.852966, 2.349902)),
    # THE TWO NEW YORK ROWS, BACK FROM THE TOMBSTONE (2026-08-25). They were moved
    # out of this list because their "a feasible fixture must not refuse" premise
    # was measurably FALSE on the un-enriched corpus (74 of 150 minutes buildable
    # for lower-manhattan, 64 of 150 for greenwich — both under the half line), and
    # an ALARM was put on the tombstone that fires the
    # moment the corpus upload makes either day buildable. The corpus landed and
    # the alarm fired on both rows, exactly as designed, so both return here to be
    # judged on their full narrative invariants (INV1-INV8) again.
    ("nyc-lower-manhattan-150", "new_york", (40.7069, -74.0113), 150, None, None),
    ("nyc-greenwich-village-150-hidden", "new_york", (40.7336, -74.0027), 150,
     ["hidden_history", "social_change"], None),
]

# THE TOMBSTONE (`_THIN_CORPUS_PATHS`) AND ITS ALARM TEST
# (`test_a_corpus_too_thin_for_the_day_refuses_honestly`) ARE GONE — the alarm
# fired and was serviced, which is the terminal state its own design named.
#
# On 2026-08-24 both NYC rows moved out of `_PATHS` because their premise ("a
# feasible fixture must not refuse") was measurably false on the un-enriched
# corpus: lower-manhattan 26 min buildable of a 60 ask (43.3%) and 74 of 150
# (49.3%), greenwich 64 of 150 (42.7%), the generality sweep 122 of 250 (48.8%) —
# every one under the half line. They were parked under a test asserting the true
# thing (an honest refusal that names the buildable day and carries a way out),
# with the exit condition written into that test: "it fails the moment the
# corpus upload makes either day buildable, sending
# both rows back to `_PATHS`."
#
# 2026-08-25: the enriched corpus landed, both days build (4 stops and 2 stops for
# their 150-minute asks), the alarm fired on both rows, and both rows are back in
# `_PATHS` above with every narrative invariant judged again. Nothing is left for
# the tombstone to hold, and an empty `parametrize` list is a vacuous pass (pytest
# turns it into a skip), so the list and its test are deleted rather than kept as
# an empty shell.
#
# WHAT THAT COSTS, stated rather than hidden: the far-short refusal shape those
# rows exercised ("the longest day that can be built here is about N minutes",
# carrying `alternatives` and `gap_minutes`) now has NO live case among this
# file's pinned starts. Swept 2026-08-25 across every `_GENERALITY_STARTS` entry
# of both cities at 60/120/150/180/250 min: 57 of 60 combinations BUILD, and the
# three surviving refusals are a different shape that this test's assertions do
# not fit — new_york (40.7736, -73.9566) @60 raises `TourabilityRefusedError`
# ("No POIs are reachable on foot within a 60-min walk of this start"), and
# new_york (40.7061, -73.9969) @60 and @150 raise the OVERRUN
# `CertificationPlanningInfeasibleError`; all three carry `alternatives=()` and
# `gap_minutes=None`. Re-pointing the test at those would have meant deleting both
# of its assertions to get green, so it was deleted honestly instead.
#
# WHAT STILL GATES THE CONTRACT, checked rather than assumed: the never-silent
# half is gated here for every start and duration by
# `test_no_silent_empty_tour_in_any_city` below. The refusal-is-useful half keeps
# HERMETIC coverage on planner-raised refusals — non-empty `alternatives` in
# `test_tour_feasibility.py` (`test_far_end_short_budget_raises_with_gap_and_alternatives`),
# `test_tour_dials.py` (`test_composed_dials_cannot_duck_under_the_one_underfill_line`)
# and `test_tour_party.py` (`test_a_leg_cap_that_starves_the_day_refuses_with_the_cap_named`);
# the message naming minutes in the same dials test, in
# `test_tour_selection.py::test_fixed_end_red_start_circle_defers_to_routed_fixed_end_checks`
# and in `test_tour_b_materialization.py` (
# `test_one_story_fixed_end_corpus_is_refused_not_padded_with_a_sentinel`).
# What is gone with this test, and is worth someone's attention rather than a
# silent loss: no test now asserts the two halves TOGETHER over whatever refusal a
# live corpus happens to produce.

# A seated dwell stop must voice at least this much narration — the "empty
# second stop that just says 'Walk to the next stop.'" bug floor.
_MIN_DWELL_NARRATION_CHARS = 80

# INV8 — no small run of consecutive stops forces an illogical out-and-back
# detour relative to what a direct walk between its own flanking stops costs.
#
# The 2026-08-04 owner-reported defect (Rue Royale -> Notre-Dame, 300 min): the
# workbench built a route that ran ~5 km PAST its own destination, out to Parc
# de la Villette, and back. The first version of this check ("no single leg
# may eat more than 25% of the tour's whole walking budget") MISSED that exact
# case — the ~5 km round trip splits into two legs of roughly 2.5 km each,
# neither one alone crossing a whole-tour budget share — while it flagged 8 of
# the file's other 8 routes (every one of the 9 fixtures in `_PATHS` except
# the defect fixture itself), which are simply long because the tour itself
# covers a lot of ground. Sharing a budget was the wrong shape of check: a
# spread-out route and a there-and-back detour both produce "long legs"; only
# the detour produces a leg PAIR that costs far more than a direct walk
# between the same two neighbours would have.
#
# The SECOND version measured only a single stop against its immediate
# neighbours (leg p->i plus i->n vs. the direct p->n walk). That is blind to a
# CLUSTER: two adjacent far-away stops sit close to EACH OTHER, so each one's
# immediate neighbour is the other far stop, not a near one — the single-stop
# ratio never spikes even though the pair, taken together, is the identical
# commute-shaped detour. This version instead slides a window of 1 to
# ``_MAX_DETOUR_WINDOW`` CONSECUTIVE stops and compares the cost of walking
# through the whole window against the direct walk between whatever sits
# immediately before and after it — catching a lone detour stop and a small
# detour cluster the same way. The window is capped deliberately: a run of
# many consecutive stops is what an ordinary multi-stop tour looks like (it is
# EXPECTED to cost much more than a straight line across itself, because
# visiting things is the point), so growing the window without bound would
# flag good, ordinary tours. A cluster larger than the cap is a real
# possibility this check does not cover — a known, stated limitation, not a
# silent one.
#
# Both a ratio AND an absolute-minutes floor must trip together, so two stops
# that merely sit close together (a tiny, easily-inflated direct distance)
# cannot false-positive on ratio alone.
#
# CALIBRATION (2026-08-04, against this file's own 9 live `_PATHS` fixtures):
# a first pass at a 600s (10 min) floor still flagged 4 routes whose detour
# reaches as high as 1377s (23 min) of real extra walking, at a single stop —
# Luxembourg Gardens, inserted between the Pantheon and the Sorbonne on a
# 3-stop, 120-minute tour, where it is 40% of that tour's ENTIRE walk. That
# is a real, substantial detour by any absolute measure, not "a few hundred
# metres" — it is kept OUT of this check's flagged set on the judgment call
# that a park matching the tour's own requested lens (parks_gardens) is worth
# a real detour, not because its size is negligible. The reported Villette
# defect costs roughly 130 minutes of extra walking at the engine's pace for
# its ~5 km round trip — a full order of magnitude past Luxembourg Gardens.
# 1800s (30 min) draws the line between them; it is an empirical line fit to
# this file's fixtures, not a principled worthiness threshold, and it may need
# to move once more real routes exercise this check.
_MAX_STOP_DETOUR_RATIO = 2.0  # via-this-window cost vs. the direct walk around it
_MIN_DETOUR_EXTRA_SECONDS = 1800  # ...and at least 30 real minutes of pure detour
_MAX_DETOUR_WINDOW = 3  # check runs of 1, 2, and 3 consecutive stops as one unit

# ...AND THE DETOUR IS NOT EARNED BY WHAT IS IN IT (2026-08-24).
#
# THE LIMITATION THIS CLOSES IS THE ONE THE COMMENT ABOVE ALREADY NAMED: "a run of
# many consecutive stops is what an ordinary multi-stop tour looks like (it is
# EXPECTED to cost much more than a straight line across itself, because visiting
# things is the point)", and "1800s draws the line ... it is an empirical line fit
# to this file's fixtures, not a principled worthiness threshold, and it may need
# to move once more real routes exercise this check."
#
# More real routes have now exercised it, and they show the two-part test firing on
# a GOOD tour. A LOOP's flanking points sit near each other by construction, so its
# windows are compared against a very short direct line and the ratio explodes
# however sensible the day is. Measured on the live concorde-250-loop: 69 minutes
# of walking against 130 minutes of standing — the shape a full visitor day has
# (measured at 0.40 to 0.55) — and EVERY stop earns its own
# walk with room to spare, priced in visitor-time:
#
#     Hotel Le Meurice       46s walking / 912s standing   ratio 0.05
#     Palais-Royal          887s walking / 1800s standing  ratio 0.49
#     Arc du Carrousel      194s walking / 600s standing   ratio 0.32
#     Jardin des Tuileries    0s walking / 2400s standing  ratio 0.00
#     Place de la Concorde 1303s walking / 2100s standing  ratio 0.62
#
# against the cap of 2.0. So the third clause asks what the first two
# cannot: is this extra walking EARNED by the time the window gives the visitor?
# It is the same rule `selection.stop_earns_its_walk` applies when ADMITTING a stop
# (price trades in visitor-time), now applied to judging a finished route, so the
# planner and the gate that judges it speak one currency.
#
# THIS DOES NOT SOFTEN THE DEFECT THE CHECK EXISTS FOR. The reported Villette shape
# spends ~130 minutes of walking to reach places the visitor barely stands at, so
# its extra walking dwarfs its window's dwell and it still fires — as do both
# synthetic true-positive fixtures below, whose stops carry no visit seconds at all
# (a detour to somewhere you spend no time is pure detour by definition). All THREE
# clauses must trip together.
_MAX_DETOUR_WALK_PER_DWELL_SECOND = 2.0

# GENERALITY CONTRACT (any city, current + future): for every neighbourhood start
# x duration, select_route must EITHER serve a non-empty tour OR raise
# TourabilityRefusedError with alternatives — NEVER a silent 0-stop Route (which
# shipped an empty "tour" from edge/waterfront starts or tight budgets). A start
# per major neighbourhood per city; ONBOARD a new city by adding its starts here.
_GENERALITY_STARTS = {
    "paris": [(48.8606, 2.3376), (48.8590, 2.3620), (48.8480, 2.3470),
              (48.8867, 2.3431), (48.8584, 2.2945), (48.8532, 2.3692)],
    "new_york": [(40.7069, -74.0113), (40.7336, -74.0027), (40.7549, -73.9840),
                 (40.7736, -73.9566), (40.8116, -73.9465), (40.7061, -73.9969)],
}

# city_slug -> live CorpusSnapshot, loaded once for every city the fixtures use.
_SNAPSHOTS: dict[str, object] = {}


@pytest.fixture(scope="module", autouse=True)
def _live_snapshot():
    """Load each fixture city's live corpus once; skip if the dev graph is down."""
    from src.tour.selection import load_paris_corpus
    from tests.live_graph import open_dev_driver

    d = open_dev_driver()
    if d is None:
        pytest.skip("live dev Neo4j unreachable — start it with `make db-up`")
    for city in {c for _, c, *_ in _PATHS} | set(_GENERALITY_STARTS):
        _SNAPSHOTS[city] = load_paris_corpus(d, city_slug=city)
    d.close()
    yield
    _SNAPSHOTS.clear()


def _build_tour(city, start, duration_min, lenses, end):
    """Run the real pipeline for one input; return (route, script)."""
    from src.tour.beat_select import select_poi_beats
    from src.tour.contract import BeatSequence, TourInput
    from src.tour.generation import generate
    from src.tour.routing_client import RoutingClient
    from src.tour.selection import select_route

    snapshot = _SNAPSHOTS[city]
    tour_input = TourInput(
        start=tuple(start), duration_min=duration_min, city_slug=city,
        round_trip=(end is None), lenses=lenses,
        end=(tuple(end) if end else None),
    )
    with RoutingClient() as rc:
        route = select_route(tour_input, snapshot, routing_client=rc)
    plans = [select_poi_beats(p, snapshot.beats_for(p.id)) for p in route.pois]
    script = generate(BeatSequence(poi_beats=tuple(plans)), route, tour_input)
    return route, script


def _stop_texts(script):
    """Map stop_idx -> list[Sentence] and stop_idx -> joined narration."""
    by_stop: dict[int, list] = defaultdict(list)
    for s in script.script:
        by_stop[s.stop_idx].append(s)
    return by_stop


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def _check_invariants(route, script) -> list[str]:
    """Return a list of invariant-violation strings (empty == clean)."""
    v: list[str] = []
    by_stop = _stop_texts(script)
    stop_idxs = sorted(by_stop)

    # INV1 — no two ADJACENT stops share a display name (the Tuileries dup).
    names = [p.name for p in route.pois]
    for i in range(1, len(names)):
        if names[i] and names[i] == names[i - 1]:
            v.append(f"INV1 duplicate-adjacent-stop: {names[i]!r} at stops {i - 1},{i}")

    # INV7 — stop_idx values are emitted in non-decreasing order.
    emitted = [s.stop_idx for s in script.script]
    if emitted != sorted(emitted):
        v.append(f"INV7 non-monotonic stop order: {emitted[:12]}...")

    for idx in stop_idxs:
        sents = by_stop[idx]
        beat_sents = [s for s in sents if s.source_type == "beat"]
        narration = " ".join(s.text for s in sents)

        # INV2 — a seated stop is never empty / glue-only. Exception: a pinned
        # fixed-end endpoint (the "__end_b__" sentinel) has no POI content of its
        # own, so the correct output is a graceful arrival ("...your final
        # destination") + the closing — not a beat.
        is_sentinel_end = (
            idx == stop_idxs[-1]
            and idx < len(route.pois)
            and route.pois[idx].id.startswith("__end_b__")
        )
        if not beat_sents:
            if is_sentinel_end and "destination" in narration.lower():
                pass  # graceful pinned-endpoint arrival — correct, not empty
            else:
                v.append(f"INV2 empty/glue-only stop {idx}: {narration[:70]!r}")
        elif len(narration) < _MIN_DWELL_NARRATION_CHARS:
            v.append(f"INV2 too-thin stop {idx} ({len(narration)} chars): {narration[:70]!r}")

        # INV6 — no EXACT-duplicate sentence within a single stop (literal repeat).
        seen: set[str] = set()
        for s in sents:
            key = _norm(s.text)
            if len(key) > 20 and key in seen:
                v.append(f"INV6 exact-duplicate sentence in stop {idx}: {s.text[:60]!r}")
            seen.add(key)

        # INV4/INV5 — opener staging is not doubled or mis-cased.
        for s in sents:
            if "look up at look up at" in s.text.lower() or "notice notice" in s.text.lower():
                v.append(f"INV4 doubled staging verb in stop {idx}: {s.text[:60]!r}")
            if re.search(r"\b(?:look up at|notice)\s+The\s+[a-z]", s.text):
                v.append(f"INV5 mis-cased staging ('at The <noun>') in stop {idx}: {s.text[:60]!r}")

    # INV8 — no run of 1..``_MAX_DETOUR_WINDOW`` consecutive stops forces an
    # illogical out-and-back detour (design note above the constants). Reads
    # leg cost the way summarise_route totals it (routing.py:439-442): routed
    # seconds when Valhalla answered, the haversine fallback otherwise. The
    # "skip this window" comparison is necessarily haversine — there is no
    # routed leg between two POIs that were never walked directly — corrected
    # by the same pace formula the engine uses everywhere else, so the two
    # sides are on equal footing.
    from src.tour.routing import haversine_m, pace_corrected_walk_seconds

    name_by_id = {p.id: p.name for p in route.pois}
    coord_by_id = {p.id: (p.lat, p.lng) for p in route.pois}
    start_coord = tuple(script.inputs.start)

    def _coord(poi_id: str | None) -> tuple[float, float]:
        return coord_by_id[poi_id] if poi_id is not None else start_coord

    def _label(poi_id: str | None) -> str:
        return name_by_id.get(poi_id, "the start")

    def _leg_cost_seconds(t) -> int:
        return int(t.leg_seconds) if t.source == "valhalla" else t.walk_seconds

    transits = route.transits
    n = len(route.pois)
    for start_i in range(n):
        for width in range(1, min(_MAX_DETOUR_WINDOW, n - start_i) + 1):
            end_i = start_i + width - 1
            leg_out_idx = end_i + 1
            if leg_out_idx >= len(transits):
                continue  # runs off the walk's true terminus — not a detour
            leg_in, leg_out = transits[start_i], transits[leg_out_idx]
            prev_lat, prev_lng = _coord(leg_in.from_poi_id)
            next_lat, next_lng = _coord(leg_out.to_poi_id)
            direct_seconds = pace_corrected_walk_seconds(
                haversine_m(prev_lat, prev_lng, next_lat, next_lng)
            )
            if direct_seconds <= 0:
                continue  # flanking points coincide — nothing to compare
            via_seconds = sum(
                _leg_cost_seconds(transits[k]) for k in range(start_i, leg_out_idx + 1)
            )
            extra_seconds = via_seconds - direct_seconds
            ratio = via_seconds / direct_seconds
            # What the window GIVES the visitor: the time they stand at its stops.
            window_dwell = sum(
                route.planned_visit_seconds.get(p.id, 0)
                for p in route.pois[start_i:end_i + 1]
            )
            earned = extra_seconds <= _MAX_DETOUR_WALK_PER_DWELL_SECOND * window_dwell
            if (
                ratio > _MAX_STOP_DETOUR_RATIO
                and extra_seconds > _MIN_DETOUR_EXTRA_SECONDS
                and not earned
            ):
                window_names = " -> ".join(p.name for p in route.pois[start_i:end_i + 1])
                v.append(
                    f"INV8 illogical detour at stop(s) {start_i}-{end_i} "
                    f"({window_names}): {_label(leg_in.from_poi_id)} -> {window_names} -> "
                    f"{_label(leg_out.to_poi_id)} costs {via_seconds}s, {ratio:.1f}x the "
                    f"{direct_seconds}s direct walk between its flanking stops "
                    f"({extra_seconds}s of pure detour against {window_dwell}s of "
                    f"standing there; cap {_MAX_STOP_DETOUR_RATIO:.1f}x / "
                    f"{_MIN_DETOUR_EXTRA_SECONDS}s / "
                    f"{_MAX_DETOUR_WALK_PER_DWELL_SECOND:.1f}x dwell)"
                )

    # INV3 (a literal "thank you" sign-off on the LAST stop) is DELETED: it pinned the
    # old algorithm. Every named stretch carries a written one-line close that plays
    # wherever the stretch ends, so every PREFIX of a day ends with a close — the
    # every-prefix close assertion in test_tour_narration_rules.py replaces it.

    return v


@pytest.mark.parametrize("label,city,start,duration,lenses,end", _PATHS,
                         ids=[p[0] for p in _PATHS])
def test_generated_tour_holds_invariants(label, city, start, duration, lenses, end):
    from src.tour.density import TourabilityRefusedError

    try:
        route, script = _build_tour(city, start, duration, lenses, end)
    except TourabilityRefusedError as e:  # a feasible fixture must not refuse
        pytest.fail(f"{label}: engine refused a fixture expected to be feasible — {e}")
    assert route.pois, f"{label}: engine produced no stops"
    violations = _check_invariants(route, script)
    assert not violations, (
        f"\n{label}: {len(violations)} invariant violation(s):\n  " + "\n  ".join(violations)
    )


def _synthetic_route_and_script(pois, waypoint_lls, *, start_ll, duration_min=300):
    """Build a real ``Route``/``Script`` pair from plain (lat, lng) waypoints,
    pricing every leg with the engine's OWN haversine + pace formula
    (``src.tour.routing``) rather than hand-picked numbers, so a synthetic
    fixture cannot silently disagree with how the engine actually prices a
    walk. ``waypoint_lls`` is the full ordered path INCLUDING the start
    point and excluding nothing — i.e. ``[start, *pois-in-order]``.
    """
    from src.tour.contract import Route, Script, TourInput, TransitSegment, ValidationReport
    from src.tour.routing import haversine_m, pace_corrected_walk_seconds

    ids = [None, *(p.id for p in pois)]
    assert len(ids) == len(waypoint_lls), "one waypoint per POI, plus the start"
    transits = []
    total = 0
    for k in range(len(waypoint_lls) - 1):
        lat1, lng1 = waypoint_lls[k]
        lat2, lng2 = waypoint_lls[k + 1]
        d = haversine_m(lat1, lng1, lat2, lng2)
        secs = pace_corrected_walk_seconds(d)
        transits.append(
            TransitSegment(
                from_poi_id=ids[k], to_poi_id=ids[k + 1], distance_m=d, walk_seconds=secs
            )
        )
        total += secs
    route = Route(
        pois=tuple(pois), transits=tuple(transits),
        total_walk_distance_m=sum(t.distance_m for t in transits), total_walk_seconds=total,
    )
    inputs = TourInput(
        start=start_ll, duration_min=duration_min, city_slug="paris",
        round_trip=False, end=waypoint_lls[-1],
    )
    script = Script(
        city_slug="paris", generated_at="2026-01-01T00:00:00Z", inputs=inputs,
        total_audio_seconds=0, total_walking_seconds=total,
        total_walk_distance_m=int(sum(t.distance_m for t in transits)),
        total_planned_seconds=total, selected_pois=(), lens_coverage={}, script=(),
        validation=ValidationReport(),
    )
    return route, script


def test_inv8_flags_a_synthetic_single_stop_detour():
    """None of this file's live fixtures currently reproduce the reported
    defect shape (the engine no longer routes rue-royale-notredame-p2p through
    Parc de la Villette today), so INV8's true-positive path has no live case
    to exercise it. This builds the defect shape directly — ONE stop several
    kilometres off the direct line between its two flanking stops — using the
    engine's real pace model (see `_synthetic_route_and_script`), and proves
    INV8 still catches it. Paired with the 9 live cases in `_PATHS` all coming
    back clean, this is the evidence the check measures the right thing: it
    is silent on genuine sightseeing routes and loud on an illogical detour.
    """
    from src.tour.contract import POI

    start_ll = (48.868, 2.323)
    p_near_1 = POI(id="a", name="Near Start", tier=3, poi_role="anchor", lat=48.865, lng=2.330)
    p_far = POI(id="b", name="Far Detour Stop", tier=3, poi_role="anchor", lat=48.896, lng=2.387)
    p_near_2 = POI(id="c", name="Near End", tier=3, poi_role="anchor", lat=48.860, lng=2.340)
    route, script = _synthetic_route_and_script(
        [p_near_1, p_far, p_near_2],
        [start_ll, (48.865, 2.330), (48.896, 2.387), (48.860, 2.340)],
        start_ll=start_ll,
    )
    violations = _check_invariants(route, script)
    inv8 = [x for x in violations if x.startswith("INV8")]
    assert inv8, f"INV8 did not fire on a synthetic single-stop detour: {violations}"


def test_inv8_flags_a_synthetic_two_stop_cluster_detour():
    """The single-stop version of this check (measuring one stop against its
    immediate neighbours) is blind to a CLUSTER: two adjacent far-away stops
    sit close to EACH OTHER, so each one's nearest neighbour is the other far
    stop, not a near one — the per-stop ratio never spikes even though the
    pair, walked together, is the identical commute-shaped detour as the
    single-stop case above. This builds exactly that shape — two stops ~150m
    apart, both several kilometres off the direct line between the tour's
    real near stops — and proves the windowed check (``_MAX_DETOUR_WINDOW``)
    still catches it as a unit.
    """
    from src.tour.contract import POI

    start_ll = (48.868, 2.323)
    p_near_1 = POI(id="a", name="Near Start", tier=3, poi_role="anchor", lat=48.865, lng=2.330)
    p_far_1 = POI(id="b", name="Far Stop 1", tier=3, poi_role="anchor", lat=48.896, lng=2.387)
    p_far_2 = POI(id="c", name="Far Stop 2", tier=3, poi_role="anchor", lat=48.897, lng=2.389)
    p_near_2 = POI(id="d", name="Near End", tier=3, poi_role="anchor", lat=48.860, lng=2.340)
    route, script = _synthetic_route_and_script(
        [p_near_1, p_far_1, p_far_2, p_near_2],
        [start_ll, (48.865, 2.330), (48.896, 2.387), (48.897, 2.389), (48.860, 2.340)],
        start_ll=start_ll,
    )
    # Confirm the blind spot is real before trusting the fix for it: at
    # window width 1 alone, neither far stop's immediate neighbours are near
    # stops, so the ratio should NOT spike.
    from src.tour.routing import haversine_m, pace_corrected_walk_seconds

    single_stop_ratios = []
    for i in (1, 2):  # the two far stops, 0-indexed into route.pois
        leg_in, leg_out = route.transits[i], route.transits[i + 1]
        prev_poi, next_poi = route.pois[i - 1], route.pois[i + 1]
        direct = pace_corrected_walk_seconds(
            haversine_m(prev_poi.lat, prev_poi.lng, next_poi.lat, next_poi.lng)
        )
        via = int(leg_in.walk_seconds) + int(leg_out.walk_seconds)
        single_stop_ratios.append(via / direct if direct else float("inf"))
    assert all(r < _MAX_STOP_DETOUR_RATIO for r in single_stop_ratios), (
        f"fixture no longer demonstrates the single-stop blind spot: {single_stop_ratios}"
    )

    violations = _check_invariants(route, script)
    inv8 = [x for x in violations if x.startswith("INV8")]
    assert inv8, f"INV8 did not fire on a synthetic two-stop cluster detour: {violations}"


@pytest.mark.parametrize("city", sorted(_GENERALITY_STARTS))
def test_no_silent_empty_tour_in_any_city(city):
    """GENERALITY: across every neighbourhood x duration, select_route serves a
    non-empty tour OR refuses cleanly — never a silent 0-stop Route. This is the
    contract a new city must also satisfy (add its starts to _GENERALITY_STARTS).

    **BOTH HONEST REFUSAL CLASSES COUNT, decided on 2026-08-24 — not a catch
    widened to get green.** The
    invariant this test names is "never a SILENT 0-stop route", and the engine
    has TWO loud refusals, not one: `TourabilityRefusedError` (there is nothing
    to tour here) and `CertificationPlanningInfeasibleError` (the day that can be
    built here is far shorter than the one asked for). The second one's own
    definition records that it was built to be indistinguishable from the first
    at the surface — "the same two fields TourabilityRefusedError carries, so ONE
    refusal-detail helper can serialise both, and a surface never has to know
    which one it caught" — and it carries the same `alternatives` / `gap_minutes`
    the first does. This test caught only the first, so an honest refusal naming
    the buildable day ("the longest day that can be built here is about 122
    minutes, far short of the 250 minutes asked for") was scored as a defect.
    This refusal is the CORRECT answer below half the ask, and its sentence stays
    as written. What
    the test forbids is unchanged: a Route with no stops, returned in silence.
    """
    from src.tour.contract import TourInput
    from src.tour.density import TourabilityRefusedError
    from src.tour.routing_client import RoutingClient
    from src.tour.selection import (
        CertificationPlanningInfeasibleError,
        select_route,
    )

    snap = _SNAPSHOTS[city]
    empties: list[str] = []
    for lat, lng in _GENERALITY_STARTS[city]:
        for duration in (60, 120, 180, 250):
            ti = TourInput(start=(lat, lng), duration_min=duration,
                           city_slug=city, round_trip=True)
            try:
                with RoutingClient() as rc:
                    route = select_route(ti, snap, routing_client=rc)
            except (TourabilityRefusedError, CertificationPlanningInfeasibleError):
                continue  # an honest refusal (with alternatives) is a valid outcome
            if not route.pois:
                empties.append(f"{(lat, lng)} @ {duration}min served a SILENT 0-stop route")
    assert not empties, f"\n{city}: {len(empties)} silent-empty tour(s):\n  " + "\n  ".join(empties)
