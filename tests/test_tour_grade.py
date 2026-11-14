"""Tour grade: rubric checks plus the live regression gate.

The grade shard owns this entire file. It runs the pure rubric checks and the
real dev-graph gate together, with no marker deselections.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.tour.grade import GRADE_BASELINE, grade_tour

pytestmark = pytest.mark.grade

# ---------------------------------------------------------------------------
# Pure rubric (runs in the default bar)
# ---------------------------------------------------------------------------

_FIXTURE = {
    "expected_pois": ["A", "B", "C", "D"],
    "expected_stable_beat_ids": ["b1", "b2", "b3", "b4", "b5"],
    "expected_spine_area": "Le Marais",
}


def test_perfect_tour_scores_one():
    g = grade_tour(
        generated_poi_names=["A", "B", "C", "D"],
        generated_beat_ids=["b1", "b2", "b3", "b4", "b5"],
        generated_spine_area="Le Marais",
        validation_passed=True,
        fixture=_FIXTURE,
    )
    assert g.score == pytest.approx(1.0)
    assert g.passed


def test_axes_weight_as_documented():
    # All POIs, half the beats, right spine, valid:
    # 0.4*1 + 0.3*0.4 + 0.2*1 + 0.1*1 = 0.82
    g = grade_tour(
        generated_poi_names=["A", "B", "C", "D"],
        generated_beat_ids=["b1", "b2"],  # 2/5 = 0.4 overlap
        generated_spine_area="Le Marais",
        validation_passed=True,
        fixture=_FIXTURE,
    )
    assert g.poi_recall == pytest.approx(1.0)
    assert g.beat_overlap == pytest.approx(0.4)
    assert g.score == pytest.approx(0.82)


def test_lost_pois_and_beats_fail_baseline():
    # Wrong POIs entirely, no beats: 0.4*0 + 0.3*0 + 0.2*1 + 0.1*1 = 0.30
    g = grade_tour(
        generated_poi_names=["X", "Y"],
        generated_beat_ids=[],
        generated_spine_area="Le Marais",
        validation_passed=True,
        fixture=_FIXTURE,
    )
    assert g.score == pytest.approx(0.30)
    assert not g.passed
    assert g.score < GRADE_BASELINE


def test_wrong_spine_and_failed_validation_dock_their_weights():
    """The axis ARITHMETIC is unchanged — validation still reports its 0.10
    weight in the breakdown — but a failed validation FAILS the grade outright,
    never a 0.10 dock. The measured contradiction: a fabricating tour
    (validation=0) could score 0.90 and clear the 0.65 baseline, so the gate
    blessed exactly the tour the validation gate refuses to serve.
    UNDO: return `passed = score >= baseline`
    alone -> a failed-validation 0.70 passes again -> RED."""
    g = grade_tour(
        generated_poi_names=["A", "B", "C", "D"],
        generated_beat_ids=["b1", "b2", "b3", "b4", "b5"],
        generated_spine_area="Wrong Area",
        validation_passed=False,
        fixture=_FIXTURE,
    )
    # 0.4 + 0.3 + 0 + 0 = 0.70
    assert g.score == pytest.approx(0.70)
    assert g.spine_match == 0.0
    assert g.validation == 0.0
    assert not g.passed, (
        "a failed validation must fail the grade (hard zero), whatever the score"
    )


def test_a_fabricating_tour_cannot_buy_its_way_past_the_baseline():
    """The exact measured case: perfect recall/overlap/spine with a
    FAILED validation scores 0.90 — above the 0.65 baseline — and must still
    fail. The same axes with validation TRUE pass, so the hard zero bites on
    validation alone."""
    failed = grade_tour(
        generated_poi_names=["A", "B", "C", "D"],
        generated_beat_ids=["b1", "b2", "b3", "b4", "b5"],
        generated_spine_area="Le Marais",
        validation_passed=False,
        fixture=_FIXTURE,
    )
    assert failed.score == pytest.approx(0.90)
    assert not failed.passed

    clean = grade_tour(
        generated_poi_names=["A", "B", "C", "D"],
        generated_beat_ids=["b1", "b2", "b3", "b4", "b5"],
        generated_spine_area="Le Marais",
        validation_passed=True,
        fixture=_FIXTURE,
    )
    assert clean.passed


def test_an_empty_expectation_is_refused_not_scored_perfect():
    """A fixture that expects nothing is broken, and must never grade 1.0.

    Regression guard, measured 2026-07-30: grade_tour read ``expected_beat_ids``
    after the fixtures were re-keyed to ``expected_stable_beat_ids``, so the set
    came back empty and ``_recall`` returned 1.0 — the re-keyed Ile fixture
    scored beat_overlap=1.00 with ZERO generated beats and PASSED the baseline.
    """
    for broken in (
        {"expected_pois": [], "expected_stable_beat_ids": ["b1"], "expected_spine_area": "X"},
        {"expected_pois": ["A"], "expected_stable_beat_ids": [], "expected_spine_area": "X"},
        # the exact shape of the defect: the durable key absent entirely
        {"expected_pois": ["A"], "expected_beat_ids": ["b1"], "expected_spine_area": "X"},
        # the SPINE axis had the same hole two lines below the first repair: a null
        # expectation matched a null result for a free 0.20 of the baseline.
        {"expected_pois": ["A"], "expected_stable_beat_ids": ["b1"], "expected_spine_area": None},
        {"expected_pois": ["A"], "expected_stable_beat_ids": ["b1"]},
    ):
        with pytest.raises(ValueError, match="declares no"):
            grade_tour(
                generated_poi_names=[],
                generated_beat_ids=[],
                generated_spine_area=None,
                validation_passed=True,
                fixture=broken,
            )


# ---------------------------------------------------------------------------
# Live regression gate (marked grade; excluded from make test)
# ---------------------------------------------------------------------------

_FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "tour_golden"
_GOLDENS = ["ile_oneway_90min", "pdv_round_trip_60min"]


def _live_graded(fixture: dict):
    """Run the real pipeline for a fixture's input and grade it. Skips (→ fails
    under conftest) only handled by the caller via the live driver helper.

    The fixtures pin durable corpus slugs, while the runtime keys beats by the
    per-database UUID. ``grade_tour`` is left untouched: the harness translates both
    sides onto the slug before handing them over.
    """
    from src.tour.contract import BeatSequence, TourInput
    from src.tour.generation import generate
    from src.tour.routing_client import RoutingClient
    from src.tour.selection import build_poi_beat_plans_capped, select_route
    from tests.test_tour_golden_consistency import generated_stable_beat_ids

    inp = fixture["input"]
    tour_input = TourInput(
        start=tuple(inp["start"]), duration_min=inp["duration_min"],
        city_slug=inp["city_slug"], round_trip=inp["round_trip"], lenses=inp.get("lenses"),
    )
    snapshot = _SNAPSHOT
    with RoutingClient() as rc:
        route = select_route(tour_input, snapshot, routing_client=rc)
    capped = build_poi_beat_plans_capped(
        route, snapshot, lenses=None,
        end_is_none=route.fixed_end_poi_id is None,
    )
    seq = BeatSequence(poi_beats=tuple(pb for pb, _ in capped))
    script = generate(seq, route, tour_input)
    gen_beats, untranslated = generated_stable_beat_ids(script, seq)
    assert not untranslated, (
        f"{len(untranslated)} emitted beats carry no stable_beat_id: {untranslated[:5]}"
    )
    return route, grade_tour(
        generated_poi_names=[p.name for p in route.pois],
        generated_beat_ids=sorted(gen_beats),
        generated_spine_area=route.spine_area,
        validation_passed=script.validation.passed,
        fixture=fixture,
    )


_SNAPSHOT = None  # module-level cache set by the fixture below


@pytest.fixture(scope="module", autouse=True)
def _live_snapshot():
    """Load the live Paris corpus once; skip the grade tests if it's down."""
    global _SNAPSHOT
    from src.tour.selection import load_paris_corpus
    from tests.live_graph import open_dev_driver

    d = open_dev_driver()
    if d is None:
        pytest.skip("live dev Neo4j unreachable — start it with `make db-up`")
    _SNAPSHOT = load_paris_corpus(d, city_slug="paris")
    d.close()
    yield


@pytest.mark.parametrize("name", _GOLDENS)
def test_live_golden_clears_grade_baseline(name):
    fixture = json.loads((_FIXTURE_DIR / f"{name}.json").read_text())
    _route, g = _live_graded(fixture)
    assert g.passed, f"{name} regressed: {g.breakdown()}"


def test_broken_golden_drops_below_baseline():
    """PROVE: corrupt a golden's expectations and the SAME live output grades
    below baseline — the gate genuinely catches a regression, not a tautology."""
    fixture = json.loads((_FIXTURE_DIR / "ile_oneway_90min.json").read_text())
    _route, real = _live_graded(fixture)
    assert real.passed  # sanity: real output passes

    broken = dict(fixture)
    broken["expected_pois"] = ["Eiffel Tower", "Sacre-Coeur", "Louvre", "Arc de Triomphe"]
    broken["expected_stable_beat_ids"] = ["nonexistent-1", "nonexistent-2", "nonexistent-3"]
    broken["expected_spine_area"] = "Montmartre"
    _route2, broken_grade = _live_graded(broken)
    assert not broken_grade.passed, f"broken golden should fail: {broken_grade.breakdown()}"
    assert broken_grade.score < GRADE_BASELINE
