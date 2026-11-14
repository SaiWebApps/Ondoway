"""Golden test: Île de la Cité one-way 90min reproduces empirical Tour 2.

Loads the canonical fixture under fixtures/tour_golden/ile_oneway_90min.json
and runs the live selection → beat_select → generation pipeline against the
production Paris Neo4j. Guards the established beat-ID overlap baseline while
reporting the 90% empirical target, plus spine area + validation gates.

Missing populated local dev Neo4j is a hard failure.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.tour.contract import BeatSequence, TourInput
from src.tour.generation import generate
from src.tour.routing_client import RoutingClient
from src.tour.selection import build_poi_beat_plans_capped, load_paris_corpus, select_route
from tests.live_graph import assert_walk_was_routed, open_dev_driver
from tests.test_tour_golden_consistency import generated_stable_beat_ids

# Quality-comparison gate against a human-curated ideal tour; excluded from the
# definitive `make test` bar (routed through its internal golden shard).
pytestmark = pytest.mark.golden

FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent / "fixtures" / "tour_golden" / "ile_oneway_90min.json"
)
OVERLAP_TARGET = 0.90
# AN ABSOLUTE HIT COUNT, NOT A RATIO. Read all of this before changing the number.
#
# WHY ABSOLUTE: a fractional floor divides by the pinned-set size, so changing the
# pinned set would move the bar without anyone deciding to. PINNED_SET_SIZES in
# test_tour_golden_consistency.py goes red whenever the set's size changes, so the floor
# is re-derived on purpose, never by drift.
#
# HOW IT IS DERIVED: floor = 85% of a fresh `make golden-probe` measurement of OUTPUT
# against the current pinned set, rounded down. The pinned set is 17 beats; beats at POIs
# this 90-minute corridor does not seat under visit-time pricing sit in the fixture's
# structurally_unreachable with that reason. The measurement is 6 hits (35.3%), so the
# floor is 0.85 x 6 = 5.1 -> 5.
#
# STATE IT PLAINLY when the floor moves: say whether the absolute count went up or down.
# A percentage quoted against a different pinned set is not a comparison.
#
# OVERLAP_TARGET (90%) stays as the unmet aspiration.
OVERLAP_MIN_HITS = 5


def _live_driver():
    return open_dev_driver()


@pytest.fixture(scope="module")
def fixture():
    return json.loads(FIXTURE_PATH.read_text())


@pytest.fixture(scope="module")
def live_neo4j():
    d = _live_driver()
    if d is None:
        pytest.skip(
            "Live Paris Neo4j unreachable — golden tests require the production "
            "dev instance (port 7687). Start it with `make db-up`."
        )
    yield d
    d.close()


@pytest.fixture(scope="module")
def snapshot(live_neo4j):
    return load_paris_corpus(live_neo4j, city_slug="paris")


def _generated_beat_ids(snapshot, fixture):
    inp = fixture["input"]
    tour_input = TourInput(
        start=tuple(inp["start"]),
        duration_min=inp["duration_min"],
        city_slug=inp["city_slug"],
        round_trip=inp["round_trip"],
        lenses=inp.get("lenses"),
        theme_hint=inp.get("theme_hint"),
        start_label=inp.get("start_label"),
    )
    # The walk must be ROUTED: the overlap baseline was measured on routed legs,
    # and a haversine fallback (Valhalla late, down, or under contention) fails
    # here by name instead of surfacing as a mystery overlap dip.
    with RoutingClient() as routing_client:
        route = select_route(tour_input, snapshot, routing_client=routing_client)
    assert_walk_was_routed(route, golden="Île")
    # Golden-harness fidelity: route through the shipped build path so the
    # goldens merge co-located demoted_beats like production.
    seq = BeatSequence(
        poi_beats=tuple(
            pb
            for pb, _ in build_poi_beat_plans_capped(
                route, snapshot, lenses=None, end_is_none=True
            )
        )
    )
    script = generate(seq, route, tour_input)
    return script, route, seq


def test_ile_golden_overlap(snapshot, fixture):
    """Beat-ID overlap must not regress below the established routed baseline."""
    script, route, _seq = _generated_beat_ids(snapshot, fixture)

    expected: set[str] = set(fixture["expected_stable_beat_ids"])
    generated_ids, untranslated = generated_stable_beat_ids(script, _seq)
    assert not untranslated, (
        f"{len(untranslated)} emitted beats carry no stable_beat_id: {untranslated[:5]}"
    )

    overlap = len(expected & generated_ids)
    overlap_pct = overlap / len(expected) if expected else 0.0
    missing = sorted(expected - generated_ids)

    # Emitted UNCONDITIONALLY for `make golden-probe`. The probe used to grep this
    # number out of pytest's ASSERTION text, so it only ever produced output while
    # the gate was RED — the moment the goldens went green the probe returned nothing
    # and failed under pipefail. A gate's reporting must not depend on the gate
    # failing. Guarded by test_golden_probe_marker_is_emitted_by_both_goldens.
    print(
        f"GOLDEN-OVERLAP Ile {overlap}/{len(expected)} "
        f"({overlap_pct:.1%}) floor {OVERLAP_MIN_HITS} hits"
    )

    assert overlap >= OVERLAP_MIN_HITS, (
        f"Île golden overlap {overlap}/{len(expected)} ({overlap_pct:.1%}) below the "
        f"regression floor of {OVERLAP_MIN_HITS} hits (empirical target "
        f"{OVERLAP_TARGET:.0%}). "
        f"Missing: {missing[:10]}{'…' if len(missing) > 10 else ''}. "
        f"Generated POIs: {[p.name for p in route.pois]}. "
        f"Spine: {route.spine_area}."
    )


def test_ile_golden_spine(snapshot, fixture):
    """Spine area should be Île de la Cité (the district-dominance rule)."""
    _, route, _ = _generated_beat_ids(snapshot, fixture)
    expected_spine = fixture["expected_spine_area"]
    assert route.spine_area == expected_spine, (
        f"Spine area mismatch: expected {expected_spine!r}, got {route.spine_area!r}. "
        f"POIs picked: {[p.name for p in route.pois]}."
    )


def test_ile_golden_validation_passes(snapshot, fixture):
    script, _, _ = _generated_beat_ids(snapshot, fixture)
    assert script.validation.passed, (
        f"Validation failed. "
        f"Untraceable: "
        f"{[s.text[:80] for s in script.validation.untraceable_sentences]}. "
        f"Forbidden: "
        f"{[(s.text[:80], phrase) for s, phrase in script.validation.forbidden_phrase_hits]}."
    )


def test_ile_golden_picks_notre_dame(snapshot, fixture):
    """Notre-Dame Cathedral must be among the picked anchors (it's the centerpiece)."""
    _, route, _ = _generated_beat_ids(snapshot, fixture)
    poi_names = {p.name for p in route.pois}
    assert "Notre-Dame Cathedral" in poi_names, (
        f"Notre-Dame Cathedral not selected. POIs: {sorted(poi_names)}."
    )
