"""GRADE — exemplar-calibrated tour rubric.

A pure, weighted multi-axis score of a generated tour against a golden
fixture. This is a CI REGRESSION gate, not the ≥90% golden-overlap ideal:
the baseline is set so the current corpus passes with margin and a genuine
regression (POIs lost, spine wrong, beats dropped, validation broken) drops
below it. Never self-tunes — changing a weight or the baseline is a
deliberate, reviewed edit.

The scoring function is pure (no DB, no engine) so it is unit-tested in the
default bar; the live grading harness (``tests/test_tour_grade.py``, marked
``grade``) runs the real pipeline against the dev graph and grades it.
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

# A generated tour must grade at or above this to pass. Calibrated to
# measure the SHIPPED path (build_poi_beat_plans_capped, 9 beats
# on Île) rather than the raw uncapped path (23 beats). Measured 2026-08-27:
# Île 0.506, PdV 0.743. The floor is 85% of the lower score = 0.43.
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

    Beats are keyed by the DURABLE corpus slug (``expected_stable_beat_ids``).
    The old ``expected_beat_ids`` held the per-database UUIDs Neo4j mints at
    upload, which rotate on every re-seed; a fixture keyed that way can never
    match real output.

    An empty expectation is a BROKEN FIXTURE, not a perfect tour. This used to
    read a key the re-key had deleted and fall through to a 1.0 recall, so a
    tour that reproduced NOTHING scored ``beat_overlap=1.00`` and passed the
    baseline. Refuse loudly instead: a silent 1.0 launders exactly the
    regression this gate exists to catch.
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

    # SAME FAIL-OPEN CLASS AS THE BEATS AXIS ABOVE, and it survived the first repair of
    # it: a fixture with no `expected_spine_area` and an engine that produced no spine
    # compared None == None and scored a free 1.0 — 0.20 of a 0.65 baseline for declaring
    # nothing. Caught by hostile review 2026-07-30. An absent expectation is a broken
    # fixture on every axis, not just the one that happened to be reported.
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
        # A failed validation FAILS the grade outright — never a 0.10 dock. The axis
        # keeps its weight in the breakdown (the score still reports it), but a
        # fabricating tour scoring 0.90 must not clear a 0.65 baseline while the
        # validation gate refuses to serve it. The weight test is re-derived as
        # a written decision (tests/test_tour_grade.py).
        passed=score >= baseline and validation_passed,
    )
