"""Tour grade rubric used by the grade test shard.

A pure, weighted multi-axis score of a generated tour against a golden
fixture. It is a regression gate, not the ≥90% golden-overlap ideal: the
baseline is set so the current corpus passes with margin and a genuine
regression (POIs lost, spine wrong, beats dropped, validation broken) drops
below it. It never self-tunes; a weight or the baseline changes only by a
deliberate, reviewed edit.

The scoring function is pure (no database, no engine). The grade tests call
it with the output of the real pipeline run against the dev graph.
"""

from __future__ import annotations

from dataclasses import dataclass

# Axis weights (sum to 1.0). POI recall and beat overlap dominate — they are
# what "reproduces the human tour" most directly; spine and validation are
# structural guards.
W_POI_RECALL = 0.40
W_BEAT_OVERLAP = 0.30
W_SPINE_MATCH = 0.20
W_VALIDATION = 0.10

# A generated tour must grade at or above this to pass. The baseline grades
# the shipped capped path (build_poi_beat_plans_capped), not the raw uncapped
# path, and sits at 85% of the lower golden score (Île 0.506, PdV 0.743).
GRADE_BASELINE = 0.43


@dataclass(frozen=True)
class GradeResult:
    poi_recall: float
    beat_overlap: float
    spine_match: float
    validation: float
    score: float
    passed: bool

    def breakdown(self) -> str:
        return (
            f"score={self.score:.3f} "
            f"(poi_recall={self.poi_recall:.2f} beat_overlap={self.beat_overlap:.2f} "
            f"spine={self.spine_match:.0f} validation={self.validation:.0f}) "
            f"{'PASS' if self.passed else 'FAIL'} @ baseline {GRADE_BASELINE}"
        )


def _recall(expected: set[str], got: set[str]) -> float:
    """|expected ∩ got| / |expected|. Callers must reject an empty expectation."""
    return len(expected & got) / len(expected)


def grade_tour(
    *,
    generated_poi_names: list[str],
    generated_beat_ids: list[str],
    generated_spine_area: str | None,
    validation_passed: bool,
    fixture: dict,
    baseline: float = GRADE_BASELINE,
) -> GradeResult:
    """Score a generated tour against a golden ``fixture`` dict.

    Reads the fixture's ``expected_pois``/``expected_stable_beat_ids``/
    ``expected_spine_area``; the engine output is passed in (the caller runs
    the pipeline), so this stays pure and unit-testable.

    Beats are keyed by the durable corpus slug (``expected_stable_beat_ids``),
    never by the per-database UUIDs Neo4j mints at upload, which change on
    every re-seed.

    An empty expectation is a BROKEN FIXTURE, not a perfect tour: it would score
    ``beat_overlap=1.00`` for a tour that reproduced nothing and pass the baseline.
    Refuse loudly instead; a silent 1.0 launders exactly the regression this gate
    exists to catch.
    """
    expected_pois = set(fixture.get("expected_pois") or ())
    expected_beats = set(fixture.get("expected_stable_beat_ids") or ())
    empty = [
        name
        for name, value in (
            ("expected_pois", expected_pois),
            ("expected_stable_beat_ids", expected_beats),
        )
        if not value
    ]
    if empty:
        raise ValueError(
            "golden fixture declares no "
            + " and no ".join(empty)
            + " — cannot be graded (an empty expectation would score a perfect "
            "recall and hide a total regression)"
        )

    poi_recall = _recall(expected_pois, set(generated_poi_names))
    beat_overlap = _recall(expected_beats, set(generated_beat_ids))

    # A fixture without expected_spine_area is broken: None == None would score a free 0.20.
    expected_spine = fixture.get("expected_spine_area")
    if not expected_spine:
        raise ValueError(
            "golden fixture declares no expected_spine_area — cannot be graded (a null "
            "expectation would match a null result and score a free 0.20)"
        )
    spine_match = 1.0 if generated_spine_area == expected_spine else 0.0
    validation = 1.0 if validation_passed else 0.0

    score = (
        W_POI_RECALL * poi_recall
        + W_BEAT_OVERLAP * beat_overlap
        + W_SPINE_MATCH * spine_match
        + W_VALIDATION * validation
    )
    return GradeResult(
        poi_recall=poi_recall,
        beat_overlap=beat_overlap,
        spine_match=spine_match,
        validation=validation,
        score=score,
        # A failed validation fails the grade outright, whatever the score. The
        # axis keeps its weight in the score and breakdown, but a tour the
        # validation gate refuses to serve never passes the grade.
        passed=score >= baseline and validation_passed,
    )
