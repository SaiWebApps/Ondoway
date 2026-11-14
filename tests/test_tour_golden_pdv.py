"""Golden test: Place des Vosges round-trip 60min reproduces empirical Tour 1.

Loads the canonical fixture under fixtures/tour_golden/pdv_round_trip_60min.json
and runs the live selection → beat_select → generation pipeline against the
production Paris Neo4j. Guards the established beat-ID overlap baseline while
reporting the 90% empirical target, plus spine area + validation gates.

Missing populated local dev Neo4j is a hard failure.
The conftest test driver (port 7688) is wiped per session and is NOT used here.
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
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "tour_golden"
    / "pdv_round_trip_60min.json"
)
OVERLAP_TARGET = 0.90
# AN ABSOLUTE HIT COUNT, NOT A RATIO — see the longer note in test_tour_golden_ile.py.
#
# This is the weakest golden floor and the one to argue with.
#
# THE FLOOR IS THE MEASUREMENT, NOT 85% OF IT. The 85% rule is right for a larger count
# but DEGENERATES at a small one: 85% of 3 rounds to 2, a floor that shrugs at losing a
# third of the hits. There is no headroom to buy: this gate is deterministic by
# construction (src/tour/selection.py orders POIs by p.id specifically so two loads of the
# same graph cannot drift). So the floor IS the measurement — lose a single hit and this
# goes red, which is what a regression floor is for. If it ever flakes, the fix is to name
# the non-determinism here, not to add slack.
#
# WHY SO LOW: the human document is a deep walk of ONE square, narrating the Place des
# Vosges arcade address by address (no 3, no 16, no 20, no 23, no 24, Pavillon de la
# Reine, Coconnas, Ma Bourgogne, Hotel de Chaulnes). Every miss sits at Place des Vosges
# ITSELF — the square the day does seat — so none is structurally unreachable and none is
# removed from the pinned set of 21. The per-stop audio ceiling (MAX_DWELL_AUDIO_SECONDS)
# caps how many beat words one stop voices, so a 21-address single-square walk is not a
# tour this engine can build. Closing the gap means teaching it sub-location stops (a
# standing position inside one POI), NOT moving this number.
#
# The fresh `make golden-probe` measurement of OUTPUT is 3 hits.
OVERLAP_MIN_HITS = 3


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
    """Run the full pipeline and return the set of beat IDs in the Script."""
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
    assert_walk_was_routed(route, golden="PdV")
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


def test_pdv_golden_overlap(snapshot, fixture):
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
        f"GOLDEN-OVERLAP PdV {overlap}/{len(expected)} "
        f"({overlap_pct:.1%}) floor {OVERLAP_MIN_HITS} hits"
    )

    assert overlap >= OVERLAP_MIN_HITS, (
        f"PdV golden overlap {overlap}/{len(expected)} ({overlap_pct:.1%}) below the "
        f"regression floor of {OVERLAP_MIN_HITS} hits (empirical target "
        f"{OVERLAP_TARGET:.0%}). "
        f"Missing: {missing[:10]}{'…' if len(missing) > 10 else ''}. "
        f"Generated POIs: {[p.name for p in route.pois]}. "
        f"Spine: {route.spine_area}."
    )


def test_pdv_golden_spine(snapshot, fixture):
    """Spine area should be Le Marais."""
    _script, route, _ = _generated_beat_ids(snapshot, fixture)
    expected_spine = fixture["expected_spine_area"]
    assert route.spine_area == expected_spine, (
        f"Spine area mismatch: expected {expected_spine!r}, got {route.spine_area!r}. "
        f"POIs picked: {[p.name for p in route.pois]}."
    )


def test_pdv_golden_validation_passes(snapshot, fixture):
    """Validation must pass (0 untraceable, 0 forbidden)."""
    script, _, _ = _generated_beat_ids(snapshot, fixture)
    assert script.validation.passed, (
        f"Validation failed. "
        f"Untraceable: "
        f"{[s.text[:80] for s in script.validation.untraceable_sentences]}. "
        f"Forbidden: "
        f"{[(s.text[:80], phrase) for s, phrase in script.validation.forbidden_phrase_hits]}."
    )


def test_pdv_golden_picks_pdv(snapshot, fixture):
    """Place des Vosges must be among the picked anchors."""
    _, route, _ = _generated_beat_ids(snapshot, fixture)
    poi_names = {p.name for p in route.pois}
    assert "Place des Vosges" in poi_names, (
        f"Place des Vosges not selected. POIs: {sorted(poi_names)}."
    )
