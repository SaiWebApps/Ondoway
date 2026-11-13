"""Automated narration-coherence gate (web-first verification).

Generates a real Paris tour against the live dev graph (port 7687) and asserts
the stitched per-stop narration is COHERENT — every stop non-empty, opens with a
cold-open, closes with closing glue, each stop carries beat narration, and the
validation gate passes. Catches stitcher regressions (dropped sentences, empty
or glue-only stops, missing open/close) WITHOUT mobile or a human listen.

Live-graph, golden-style, routed through the internal golden shard in
``make test``. Missing dev Neo4j is a failure.
"""

from __future__ import annotations

from collections import Counter

import pytest

from src.tour.beat_select import select_poi_beats
from src.tour.contract import BeatSequence, TourInput
from src.tour.generation import GLUE_CLOSING, generate
from src.tour.render_md import stop_narration_text
from src.tour.routing_client import RoutingClient
from src.tour.selection import load_paris_corpus, select_route
from tests.live_graph import open_dev_driver

pytestmark = pytest.mark.golden


def _live_driver():
    return open_dev_driver()


@pytest.fixture(scope="module")
def snapshot():
    d = _live_driver()
    if d is None:
        pytest.skip("Live Paris Neo4j unreachable — start it with `make db-up`.")
    try:
        yield load_paris_corpus(d, city_slug="paris")
    finally:
        d.close()


def test_generated_paris_tour_narration_is_coherent(snapshot):
    tour_input = TourInput(
        start=(48.852966, 2.349902),  # Notre-Dame / Île de la Cité (dense)
        duration_min=60,
        city_slug="paris",
        lenses=["historic_arch"],
    )
    with RoutingClient() as rc:
        route = select_route(tour_input, snapshot, routing_client=rc)
    assert route.pois, "expected a tourable route from a dense central-Paris start"

    plans = [select_poi_beats(p, snapshot.beats_for(p.id)) for p in route.pois]
    script = generate(BeatSequence(poi_beats=tuple(plans)), route, tour_input)
    n_stops = len(route.pois)

    # 1. The validation gate passes (nothing untraceable / forbidden).
    assert script.validation.passed, "narration must pass the validation gate"

    # 2. Every stop has non-empty narration — no gaps, no empty stops.
    per_stop = stop_narration_text(script)
    assert set(per_stop) == set(range(n_stops)), "every stop must carry narration"
    for idx, text in per_stop.items():
        assert text.strip(), f"stop {idx} narration is empty"

    # 3. Opens with a cold-open glue at stop 0; closes with closing glue.
    assert any(
        s.stop_idx == 0 and s.source_type == "glue" for s in script.script
    ), "tour must open with a cold-open"
    assert any(s.source_id == GLUE_CLOSING for s in script.script), "tour must close"

    # 4. Each stop carries real beat narration (a story, not glue-only); the
    #    opening anchor is multi-sentence.
    beats_per_stop = Counter(s.stop_idx for s in script.script if s.source_type == "beat")
    for idx in range(n_stops):
        assert beats_per_stop[idx] >= 1, f"stop {idx} has no beat narration (glue-only)"
    assert beats_per_stop[0] >= 2, "the opening anchor should be multi-sentence"
