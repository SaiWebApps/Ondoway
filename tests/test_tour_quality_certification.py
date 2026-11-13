"""Undo tests for bounded FACT/ENJOY certification."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.tour.artifact import (
    BuildFingerprint,
    CompositionTrace,
    build_final_blueprint,
    derive_playback_assignments,
    remap_provider_playback_assignments,
    sentences_payload_sha256,
    validate_llm_composed_blueprint,
)
from src.tour.contract import (
    POI,
    BeatRef,
    PhysicalCue,
    POIBeats,
    Route,
    Script,
    ScriptPOI,
    Sentence,
    TourInput,
    TransitSegment,
    ValidationReport,
)
from src.tour.generation import (
    GLUE_PACING,
    SENSORY_INVITATION,
    _build_synthesized_opener,
)
from src.tour.quality_certification import (
    ENJOY_AXIS_NAMES,
    ENJOY_RELEASE_THRESHOLD,
    FACT_PROMPT_POLICY_VERSION,
    EnjoymentAxisAssessment,
    EnjoymentAxisScores,
    EnjoymentEvidenceSpan,
    EnjoymentIndependentReviewPair,
    EnjoymentScoredAssessment,
    EnjoymentScoreDecision,
    EnjoymentScoredReviewBatch,
    FactClaimDecision,
    FactFinding,
    FactPolicyCalibrationCase,
    FactPolicyCalibrationVerdict,
    FactReviewBatch,
    FactSentenceVerdict,
    QualityAdjudicationBatch,
    QualityCalibrationBatch,
    QualityCalibrationReport,
    ReviewUsage,
    SourceEvidence,
    calibrate_quality,
    certify_enjoyment,
    certify_facts,
    certify_quality,
    decide_enjoyment_score,
    enjoyment_aggregate_meets_release_threshold,
    enjoyment_axis_aggregates,
    enjoyment_calibration_from_quality,
    enjoyment_consensus_below_gold_axes,
    enjoyment_pair_meets_score_gate,
    enjoyment_reviewer_total,
    enjoyment_two_reviewer_aggregate,
    load_enjoyment_anchors,
    load_quality_calibration_inputs,
    quality_calibration_report_fingerprint,
    source_evidence_fingerprint,
    validate_quality_calibration_report,
    validate_quality_gate_report,
)

ROOT = Path(__file__).resolve().parents[1]
# The calibration inputs are FIXTURES (moved 2026-08-18): planning prose is not
# test input, and fixtures/ is what tests read — the reference-tours precedent.
CALIBRATION = ROOT / "fixtures/tour-certification/calibration-manifest.json"
REFERENCES = ROOT / "fixtures/tour-certification/investigation-reference-manifest.json"
PROMPT_SHA = "9" * 64
POLICY_SHA = "7" * 64


def test_fact_policy_version_is_v2() -> None:
    assert FACT_PROMPT_POLICY_VERSION == "tour-fact-review-v2"


def _enjoyment_axis_scores(
    *,
    site_presence: int = 1,
    narrative_motion: int = 1,
    human_specificity: int = 1,
    spoken_voice: int = 1,
    editorial_control: int = 1,
) -> EnjoymentAxisScores:
    passage = "A concrete passage."

    def assessment(score: int, explanation: str) -> EnjoymentAxisAssessment:
        return EnjoymentAxisAssessment(
            score=score,
            evidence=(
                EnjoymentEvidenceSpan(
                    segment_id="segment-1",
                    passage_byte_start=0,
                    passage_byte_end=len(passage.encode("utf-8")),
                    exact_passage=passage,
                ),
            ),
            explanation=explanation,
        )

    return EnjoymentAxisScores(
        site_presence=assessment(
            site_presence,
            "How strongly the narration places the listener on site.",
        ),
        narrative_motion=assessment(
            narrative_motion,
            "Whether the story develops and rewards attention.",
        ),
        human_specificity=assessment(
            human_specificity,
            "Whether concrete people and telling details carry the story.",
        ),
        spoken_voice=assessment(
            spoken_voice,
            "Whether the narration sounds natural and rewarding aloud.",
        ),
        editorial_control=assessment(
            editorial_control,
            "Whether selection, pacing, and emphasis feel intentional.",
        ),
    )


@pytest.mark.parametrize("score", (0, 1, 2))
def test_site_presence_accepts_only_rubric_scores(score: int) -> None:
    scores = _enjoyment_axis_scores(site_presence=score)
    assert scores.site_presence.score == score


@pytest.mark.parametrize("score", (-1, 3))
def test_site_presence_rejects_scores_outside_rubric(score: int) -> None:
    with pytest.raises(ValueError, match="score"):
        _enjoyment_axis_scores(site_presence=score)


@pytest.mark.parametrize("score", (0, 1, 2))
def test_narrative_motion_accepts_only_rubric_scores(score: int) -> None:
    scores = _enjoyment_axis_scores(narrative_motion=score)
    assert scores.narrative_motion.score == score


@pytest.mark.parametrize("score", (-1, 3))
def test_narrative_motion_rejects_scores_outside_rubric(score: int) -> None:
    with pytest.raises(ValueError, match="score"):
        _enjoyment_axis_scores(narrative_motion=score)


@pytest.mark.parametrize("score", (0, 1, 2))
def test_human_specificity_accepts_only_rubric_scores(score: int) -> None:
    scores = _enjoyment_axis_scores(human_specificity=score)
    assert scores.human_specificity.score == score


@pytest.mark.parametrize("score", (-1, 3))
def test_human_specificity_rejects_scores_outside_rubric(score: int) -> None:
    with pytest.raises(ValueError, match="score"):
        _enjoyment_axis_scores(human_specificity=score)


@pytest.mark.parametrize("score", (0, 1, 2))
def test_spoken_voice_accepts_only_rubric_scores(score: int) -> None:
    scores = _enjoyment_axis_scores(spoken_voice=score)
    assert scores.spoken_voice.score == score


@pytest.mark.parametrize("score", (-1, 3))
def test_spoken_voice_rejects_scores_outside_rubric(score: int) -> None:
    with pytest.raises(ValueError, match="score"):
        _enjoyment_axis_scores(spoken_voice=score)


@pytest.mark.parametrize("score", (0, 1, 2))
def test_editorial_control_accepts_only_rubric_scores(score: int) -> None:
    scores = _enjoyment_axis_scores(editorial_control=score)
    assert scores.editorial_control.score == score


@pytest.mark.parametrize("score", (-1, 3))
def test_editorial_control_rejects_scores_outside_rubric(score: int) -> None:
    with pytest.raises(ValueError, match="score"):
        _enjoyment_axis_scores(editorial_control=score)


def test_provider_cannot_supply_enjoyment_total() -> None:
    with pytest.raises(ValueError, match="total"):
        EnjoymentScoredAssessment(
            item_id="opaque-item-1",
            axes=_enjoyment_axis_scores(),
            total=10,
        )


def test_provider_cannot_supply_enjoyment_release_decision() -> None:
    with pytest.raises(ValueError, match="decision"):
        EnjoymentScoredAssessment(
            item_id="opaque-item-1",
            axes=_enjoyment_axis_scores(),
            decision="PASS",
        )


def test_reviewer_total_is_computed_from_five_axes_in_code() -> None:
    assessment = EnjoymentScoredAssessment(
        item_id="opaque-item-1",
        axes=_enjoyment_axis_scores(
            site_presence=2,
            narrative_motion=1,
            human_specificity=0,
            spoken_voice=2,
            editorial_control=1,
        ),
    )
    assert enjoyment_reviewer_total(assessment) == 6


def test_two_reviewer_aggregate_averages_trusted_totals() -> None:
    first = EnjoymentScoredAssessment(
        item_id="opaque-item-1",
        axes=_enjoyment_axis_scores(
            site_presence=2,
            narrative_motion=1,
            human_specificity=0,
            spoken_voice=2,
            editorial_control=1,
        ),
    )
    second = EnjoymentScoredAssessment(
        item_id="opaque-item-1",
        axes=_enjoyment_axis_scores(
            site_presence=2,
            narrative_motion=2,
            human_specificity=1,
            spoken_voice=2,
            editorial_control=1,
        ),
    )
    assert enjoyment_reviewer_total(first) == 6
    assert enjoyment_reviewer_total(second) == 8
    assert enjoyment_two_reviewer_aggregate(first, second) == 7


def test_enjoyment_release_threshold_accepts_seven() -> None:
    assert ENJOY_RELEASE_THRESHOLD == 7
    assert enjoyment_aggregate_meets_release_threshold(7) is True


def test_enjoyment_release_threshold_rejects_six_point_five() -> None:
    assert enjoyment_aggregate_meets_release_threshold(6.5) is False


def test_unanimous_zero_axis_fails_despite_high_total() -> None:
    first = EnjoymentScoredAssessment(
        item_id="opaque-item-1",
        axes=_enjoyment_axis_scores(
            site_presence=0,
            narrative_motion=2,
            human_specificity=2,
            spoken_voice=2,
            editorial_control=2,
        ),
    )
    second = first.model_copy()
    aggregates = dict(enjoyment_axis_aggregates(first, second))
    assert tuple(aggregates) == ENJOY_AXIS_NAMES
    assert enjoyment_two_reviewer_aggregate(first, second) == 8
    assert aggregates["site_presence"] == 0
    assert enjoyment_pair_meets_score_gate(first, second) is False


def test_every_enjoyment_axis_requires_exact_evidence() -> None:
    with pytest.raises(ValueError, match="evidence"):
        EnjoymentAxisAssessment(
            score=1,
            evidence=(),
            explanation="An unsupported axis score.",
        )


def test_first_blind_enjoyment_review_has_a_typed_scored_result() -> None:
    assessment = EnjoymentScoredAssessment(
        item_id="item-222222222222222222222222",
        axes=_enjoyment_axis_scores(),
    )
    batch = EnjoymentScoredReviewBatch(
        review_slot="first",
        reviewer_model="claude-reviewer-a",
        policy_sha256="a" * 64,
        prompt_sha256="b" * 64,
        input_sha256="c" * 64,
        provider_response_sha256="d" * 64,
        assessments=(assessment,),
        usage=ReviewUsage(calls=1, input_tokens=100, output_tokens=20),
    )
    assert batch.assessments == (assessment,)
    assert batch.usage.calls == 1


def test_second_blind_enjoyment_review_validates_against_the_same_items() -> None:
    assessment = EnjoymentScoredAssessment(
        item_id="item-222222222222222222222222",
        axes=_enjoyment_axis_scores(),
    )
    first = EnjoymentScoredReviewBatch(
        review_slot="first",
        reviewer_model="claude-reviewer-a",
        policy_sha256="a" * 64,
        prompt_sha256="b" * 64,
        input_sha256="c" * 64,
        provider_response_sha256="d" * 64,
        assessments=(assessment,),
        usage=ReviewUsage(calls=1),
    )
    second = first.model_copy(
        update={
            "review_slot": "second",
            "provider_response_sha256": "e" * 64,
        }
    )
    pair = EnjoymentIndependentReviewPair(first=first, second=second)
    assert pair.second.review_slot == "second"


def test_matching_below_gold_axis_becomes_consensus() -> None:
    gold_id = "item-111111111111111111111111"
    candidate_id = "item-222222222222222222222222"
    gold = EnjoymentScoredAssessment(
        item_id=gold_id,
        axes=_enjoyment_axis_scores(
            site_presence=2,
            narrative_motion=2,
            human_specificity=2,
            spoken_voice=2,
            editorial_control=2,
        ),
    )
    first_candidate = EnjoymentScoredAssessment(
        item_id=candidate_id,
        axes=_enjoyment_axis_scores(
            site_presence=0,
            narrative_motion=2,
            human_specificity=2,
            spoken_voice=2,
            editorial_control=2,
        ),
    )
    second_candidate = EnjoymentScoredAssessment(
        item_id=candidate_id,
        axes=_enjoyment_axis_scores(
            site_presence=1,
            narrative_motion=2,
            human_specificity=2,
            spoken_voice=2,
            editorial_control=2,
        ),
    )
    first = EnjoymentScoredReviewBatch(
        review_slot="first",
        reviewer_model="claude-reviewer-a",
        policy_sha256="a" * 64,
        prompt_sha256="b" * 64,
        input_sha256="c" * 64,
        provider_response_sha256="d" * 64,
        assessments=(gold, first_candidate),
        usage=ReviewUsage(calls=1),
    )
    second = first.model_copy(
        update={
            "review_slot": "second",
            "provider_response_sha256": "e" * 64,
            "assessments": (gold, second_candidate),
        }
    )
    pair = EnjoymentIndependentReviewPair(first=first, second=second)
    assert enjoyment_consensus_below_gold_axes(
        pair,
        candidate_item_id=candidate_id,
        positive_gold_item_ids=(gold_id,),
    ) == ("site_presence",)


def test_pairwise_axis_disagreement_remains_advisory() -> None:
    gold_id = "item-111111111111111111111111"
    candidate_id = "item-222222222222222222222222"
    gold = EnjoymentScoredAssessment(
        item_id=gold_id,
        axes=_enjoyment_axis_scores(
            site_presence=2,
            narrative_motion=2,
            human_specificity=2,
            spoken_voice=2,
            editorial_control=2,
        ),
    )
    first_candidate = EnjoymentScoredAssessment(
        item_id=candidate_id,
        axes=_enjoyment_axis_scores(
            site_presence=1,
            narrative_motion=2,
            human_specificity=2,
            spoken_voice=2,
            editorial_control=2,
        ),
    )
    second_candidate = EnjoymentScoredAssessment(
        item_id=candidate_id,
        axes=_enjoyment_axis_scores(
            site_presence=2,
            narrative_motion=1,
            human_specificity=2,
            spoken_voice=2,
            editorial_control=2,
        ),
    )
    first = EnjoymentScoredReviewBatch(
        review_slot="first",
        reviewer_model="claude-reviewer-a",
        policy_sha256="a" * 64,
        prompt_sha256="b" * 64,
        input_sha256="c" * 64,
        provider_response_sha256="d" * 64,
        assessments=(gold, first_candidate),
        usage=ReviewUsage(calls=1),
    )
    pair = EnjoymentIndependentReviewPair(
        first=first,
        second=first.model_copy(
            update={
                "review_slot": "second",
                "provider_response_sha256": "e" * 64,
                "assessments": (gold, second_candidate),
            }
        ),
    )
    assert enjoyment_consensus_below_gold_axes(
        pair,
        candidate_item_id=candidate_id,
        positive_gold_item_ids=(gold_id,),
    ) == ()


def test_unanimous_below_gold_axis_rejects_candidate() -> None:
    gold_id = "item-111111111111111111111111"
    candidate_id = "item-222222222222222222222222"
    gold = EnjoymentScoredAssessment(
        item_id=gold_id,
        axes=_enjoyment_axis_scores(
            site_presence=2,
            narrative_motion=2,
            human_specificity=2,
            spoken_voice=2,
            editorial_control=2,
        ),
    )
    candidate = EnjoymentScoredAssessment(
        item_id=candidate_id,
        axes=_enjoyment_axis_scores(
            site_presence=1,
            narrative_motion=2,
            human_specificity=2,
            spoken_voice=2,
            editorial_control=2,
        ),
    )
    first = EnjoymentScoredReviewBatch(
        review_slot="first",
        reviewer_model="claude-reviewer-a",
        policy_sha256="a" * 64,
        prompt_sha256="b" * 64,
        input_sha256="c" * 64,
        provider_response_sha256="d" * 64,
        assessments=(gold, candidate),
        usage=ReviewUsage(calls=1),
    )
    pair = EnjoymentIndependentReviewPair(
        first=first,
        second=first.model_copy(
            update={
                "review_slot": "second",
                "provider_response_sha256": "e" * 64,
            }
        ),
    )
    decision = decide_enjoyment_score(
        pair,
        candidate_item_id=candidate_id,
        positive_gold_item_ids=(gold_id,),
    )
    assert isinstance(decision, EnjoymentScoreDecision)
    assert decision.aggregate == 9
    assert decision.consensus_below_gold_axes == ("site_presence",)
    assert decision.decision == "FAIL"


def test_nonunanimous_below_gold_axis_does_not_reject_passing_candidate() -> None:
    gold_id = "item-111111111111111111111111"
    candidate_id = "item-222222222222222222222222"
    gold = EnjoymentScoredAssessment(
        item_id=gold_id,
        axes=_enjoyment_axis_scores(
            site_presence=2,
            narrative_motion=2,
            human_specificity=2,
            spoken_voice=2,
            editorial_control=2,
        ),
    )
    first_candidate = EnjoymentScoredAssessment(
        item_id=candidate_id,
        axes=_enjoyment_axis_scores(
            site_presence=1,
            narrative_motion=2,
            human_specificity=2,
            spoken_voice=2,
            editorial_control=2,
        ),
    )
    second_candidate = EnjoymentScoredAssessment(
        item_id=candidate_id,
        axes=_enjoyment_axis_scores(
            site_presence=2,
            narrative_motion=2,
            human_specificity=2,
            spoken_voice=2,
            editorial_control=2,
        ),
    )
    first = EnjoymentScoredReviewBatch(
        review_slot="first",
        reviewer_model="claude-reviewer-a",
        policy_sha256="a" * 64,
        prompt_sha256="b" * 64,
        input_sha256="c" * 64,
        provider_response_sha256="d" * 64,
        assessments=(gold, first_candidate),
        usage=ReviewUsage(calls=1),
    )
    pair = EnjoymentIndependentReviewPair(
        first=first,
        second=first.model_copy(
            update={
                "review_slot": "second",
                "provider_response_sha256": "e" * 64,
                "assessments": (gold, second_candidate),
            }
        ),
    )
    decision = decide_enjoyment_score(
        pair,
        candidate_item_id=candidate_id,
        positive_gold_item_ids=(gold_id,),
    )
    assert decision.aggregate == 9.5
    assert decision.consensus_below_gold_axes == ()
    assert decision.decision == "PASS"


def test_fact_policy_calibration_case_requires_source_text() -> None:
    with pytest.raises(ValueError, match="source_text"):
        FactPolicyCalibrationCase(
            case_id="missing-source",
            candidate_text="A candidate claim.",
            expected="PASS",
        )


def test_fact_policy_calibration_case_requires_candidate_text() -> None:
    with pytest.raises(ValueError, match="candidate_text"):
        FactPolicyCalibrationCase(
            case_id="missing-candidate",
            source_text="A source passage.",
            expected="PASS",
        )


def _sha(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode()
    return hashlib.sha256(value).hexdigest()


def _fact_finding(
    text: str,
    *,
    code="unsupported_material_claim",
    severity="repairable",
    explanation="The source supports destruction, not actor or method.",
) -> FactFinding:
    return FactFinding(
        code=code,
        severity=severity,
        sentence_index=0,
        sentence_sha256=_sha(text),
        passage_byte_start=0,
        passage_byte_end=len(text.encode("utf-8")),
        material_claim=text,
        material_type="specific_event",
        exact_passage=text,
        evidence_ids=("source:b0",),
        explanation=explanation,
    )


def _enjoyment_span(item, passage: str) -> EnjoymentEvidenceSpan:
    for segment in item.segments:
        char_start = segment.narration.find(passage)
        if char_start >= 0:
            return EnjoymentEvidenceSpan(
                segment_id=segment.segment_id,
                passage_byte_start=len(segment.narration[:char_start].encode("utf-8")),
                passage_byte_end=len(
                    segment.narration[: char_start + len(passage)].encode("utf-8")
                ),
                exact_passage=passage,
            )
    raise AssertionError(f"passage absent from item: {passage!r}")


def _item_axis_scores(
    item,
    *,
    site_presence: int = 2,
    narrative_motion: int = 2,
    human_specificity: int = 2,
    spoken_voice: int = 2,
    editorial_control: int = 2,
) -> EnjoymentAxisScores:
    """Build exact, same-item evidence for each scored axis."""
    passage = item.narration[: min(40, len(item.narration))]
    span = _enjoyment_span(item, passage)

    def assessment(score: int, explanation: str) -> EnjoymentAxisAssessment:
        return EnjoymentAxisAssessment(
            score=score,
            evidence=(span,),
            explanation=explanation,
        )

    return EnjoymentAxisScores(
        site_presence=assessment(site_presence, "Evidence for site presence."),
        narrative_motion=assessment(narrative_motion, "Evidence for narrative motion."),
        human_specificity=assessment(
            human_specificity, "Evidence for human specificity."
        ),
        spoken_voice=assessment(spoken_voice, "Evidence for spoken voice."),
        editorial_control=assessment(
            editorial_control, "Evidence for editorial control."
        ),
    )


def _item_assessment(
    item,
    scores: tuple[int, int, int, int, int] = (2, 2, 2, 2, 2),
) -> EnjoymentScoredAssessment:
    return EnjoymentScoredAssessment(
        item_id=item.item_id,
        axes=_item_axis_scores(
            item,
            site_presence=scores[0],
            narrative_motion=scores[1],
            human_specificity=scores[2],
            spoken_voice=scores[3],
            editorial_control=scores[4],
        ),
    )


def _blueprint(
    text: str = "The square was destroyed.",
    *,
    source_type: str = "beat",
    source_id: str | None = None,
    calibration_sha256: str = "c" * 64,
    reference_sha256: str = "b" * 64,
):
    tour_input = TourInput(
        start=(48.85, 2.35),
        duration_min=10,
        city_slug="paris",
        round_trip=False,
    )
    poi = POI(
        id="p0",
        name="The Square",
        tier=5,
        poi_role="anchor",
        lat=48.85,
        lng=2.35,
    )
    route = Route(
        pois=(poi,),
        transits=(
            TransitSegment(
                from_poi_id=None,
                to_poi_id="p0",
                distance_m=0,
                walk_seconds=0,
                leg_seconds=0,
                leg_distance_m=0,
                polyline="shape",
                source="valhalla",
            ),
        ),
        total_walk_distance_m=0,
        total_walk_seconds=0,
        routed=True,
    )
    resolved_source_id = source_id or (
        "b0" if source_type == "beat" else "GLUE_NAV"
    )
    script = Script(
        city_slug="paris",
        generated_at="2026-07-21T00:00:00Z",
        inputs=tour_input,
        total_audio_seconds=60,
        total_walking_seconds=0,
        total_walk_distance_m=0,
        total_planned_seconds=600,
        selected_pois=(
            ScriptPOI(
                id="p0",
                name="The Square",
                tier=5,
                lat=48.85,
                lng=2.35,
                beat_ids=("b0",),
            ),
        ),
        lens_coverage={},
        script=(
            Sentence(
                text=text,
                source_id=resolved_source_id,
                source_type=source_type,
                stop_idx=0,
            ),
        ),
        validation=ValidationReport(),
    )
    build = BuildFingerprint(
        commit_sha="1" * 40,
        corpus_sha256="2" * 64,
        module_version="tour-v1",
        prompt_sha256="3" * 64,
        compose_model="claude-test",
        policy_version="tour-certification-v1",
        routing_engine="valhalla",
        routing_version="3.5.1",
        routing_config_sha256="4" * 64,
        tts_provider="openai",
        tts_model="tts-1-hd",
        tts_voice="nova",
    )
    authorized_source = (
        (
            script.script[0],
            Sentence(
                text="Grounded Basic Tour detail.",
                source_id="b0",
                source_type="beat",
                stop_idx=0,
            ),
        )
        if resolved_source_id in {"GLUE_PACING", "GLUE_CLOSING"}
        else (
            script.script[0].model_copy(
                update={"text": "Grounded Basic Tour wording."}
            ),
        )
    )
    provider_trace = CompositionTrace(
        unit_id="stop:0",
        stop_index=0,
        request_sha256="5" * 64,
        response_sha256="6" * 64,
        derivation="provider_response",
        authorized_source_sentences=authorized_source,
        source_sentences=script.script,
        source_payload_sha256=sentences_payload_sha256(script.script),
        source_sentence_indexes=(0,),
        sentence_indexes=(0,),
        sentence_sha256s=(_sha(text),),
    )
    return build_final_blueprint(
        contract_sha256="a" * 64,
        reference_manifest_sha256=reference_sha256,
        calibration_manifest_sha256=calibration_sha256,
        build=build,
        tour_input=tour_input,
        route=route,
        script=script,
        vignette_beat_ids=(),
        playback_assignments=derive_playback_assignments(script, vignette_beat_ids=()),
        composition_trace=(provider_trace,),
    )


def _evidence(
    blob: bytes,
    passage: str | None = None,
    *,
    evidence_id: str = "source:b0",
) -> SourceEvidence:
    passage = passage or blob.decode()
    start = blob.index(passage.encode())
    source = SourceEvidence(
        evidence_id=evidence_id,
        beat_id="b0",
        poi_id="p0",
        source_document_id="book-1",
        source_revision="pinned-revision-1",
        source_chunk_slug="chunk-1",
        source_blob_sha256=_sha(blob),
        passage_byte_start=start,
        passage_byte_end=start + len(passage.encode()),
        source_passage=passage,
        source_passage_sha256=_sha(passage),
        key_claims=("The square was destroyed.",),
        script_body="The square was destroyed.",
        evidence_sha256="0" * 64,
    )
    return source.model_copy(update={"evidence_sha256": source_evidence_fingerprint(source)})


def _quality_calibration(calibration_sha256: str = "c" * 64) -> QualityCalibrationReport:
    from src.tour.quality_certification import _seal_quality_calibration_report

    return _seal_quality_calibration_report(
        calibration_manifest_sha256=calibration_sha256,
        reference_manifest_sha256="b" * 64,
        fact_suite_sha256="4" * 64,
        anchor_suite_sha256="5" * 64,
        input_bundle_sha256="8" * 64,
        input_sha256="6" * 64,
        provider_response_sha256="c" * 64,
        state="PASS",
        reviewer_model="claude-test",
        policy_sha256=POLICY_SHA,
        approved_fact_prompt_sha256=PROMPT_SHA,
        approved_enjoy_prompt_sha256=PROMPT_SHA,
        approved_adjudication_prompt_sha256=PROMPT_SHA,
        fact_calibration_verdicts=(
            FactPolicyCalibrationVerdict(
                case_id="fixture-fact",
                decision="PASS",
                explanation="Fixture calibration boundary.",
            ),
        ),
        fact_decisions=(("fixture-fact", "PASS"),),
        enjoyment_decisions=(("fixture-enjoy", "PASS"),),
        enjoyment_assessments=(
            EnjoymentScoredAssessment(
                item_id="fixture-enjoy",
                axes=_enjoyment_axis_scores(
                    site_presence=2,
                    narrative_motion=2,
                    human_specificity=2,
                    spoken_voice=2,
                    editorial_control=2,
                ),
            ),
        ),
        usage=ReviewUsage(calls=1),
    )


class _FactReviewer:
    def __init__(
        self,
        decisions=("PASS",),
        findings=(),
        *,
        malformed=False,
        raises=False,
        model="claude-test",
        nonbeat_material=False,
    ):
        self.decisions = decisions
        self.findings = findings
        self.malformed = malformed
        self.raises = raises
        self.model = model
        self.nonbeat_material = nonbeat_material
        self.calls = 0

    def review(self, request):
        self.calls += 1
        if self.raises:
            raise TimeoutError("bounded timeout")
        verdicts = []
        for index, sentence in enumerate(request.sentences):
            decision = self.decisions[index]
            nonmaterial = (
                sentence.source_type != "beat" and not self.nonbeat_material
            ) or decision == "LICENSED_COLOR"
            if sentence.source_type != "beat" and not self.nonbeat_material:
                span_role = "CONNECTIVE"
                claim_decision = "CONNECTIVE"
                material_type = "connective"
            elif decision == "LICENSED_COLOR":
                span_role = "LICENSED_GLOSS"
                claim_decision = "LICENSED_GLOSS"
                material_type = "licensed_nonpropositional_gloss"
            else:
                span_role = "MATERIAL_CLAIM"
                claim_decision = (
                    "SUPPORTED"
                    if decision == "PASS"
                    else "REPAIRABLE_NARROWING"
                    if self.findings and self.findings[0].severity == "repairable"
                    else "MATERIAL_DEFECT"
                )
                material_type = "specific_event"
            verdicts.append(
                FactSentenceVerdict(
                    sentence_index=sentence.sentence_index,
                    sentence_sha256=sentence.sentence_sha256,
                    decision=decision,
                    claims=(
                        FactClaimDecision(
                            passage_byte_start=0,
                            passage_byte_end=len(sentence.text.encode("utf-8")),
                            exact_passage=sentence.text,
                            atomic_claim=(
                                self.findings[0].material_claim
                                if decision == "FAIL" and self.findings
                                else sentence.text
                            ),
                            span_role=span_role,
                            decision=claim_decision,
                            material_type=material_type,
                            evidence_ids=(
                                sentence.evidence_ids
                                if span_role == "MATERIAL_CLAIM"
                                else ()
                            ),
                            explanation=(
                                "Complete atomic disposition for this test sentence."
                            ),
                        ),
                    ),
                    no_material_assertion_reason=(
                        "No material factual assertion." if nonmaterial else None
                    ),
                    findings=self.findings if decision == "FAIL" else (),
                )
            )
        verdicts = tuple(verdicts)
        if self.malformed:
            verdicts = ()
        return FactReviewBatch(
            reviewer_model=self.model,
            policy_sha256=POLICY_SHA,
            prompt_sha256=PROMPT_SHA,
            input_sha256=request.fingerprint(),
            provider_response_sha256="a" * 64,
            verdicts=verdicts,
            usage=ReviewUsage(calls=1, input_tokens=100, output_tokens=10),
        )


def test_basic_tour_is_ineligible_for_fact_review_without_spend() -> None:
    basic = _blueprint().model_copy(update={"composition_trace": ()})
    reviewer = _FactReviewer()

    report = certify_facts(
        basic,
        {"b0": _evidence(b"The square was destroyed.")},
        lambda _source: b"The square was destroyed.",
        _quality_calibration(),
        reviewer,
    )

    assert report.state == "ERROR"
    assert "basic tour" in (report.error or "")
    assert reviewer.calls == 0


def test_provider_echo_with_physical_trace_reaches_fact_reviewer() -> None:
    blueprint = _blueprint()
    trace = blueprint.composition_trace[0].model_copy(
        update={"authorized_source_sentences": blueprint.script.script}
    )
    echoed = blueprint.model_copy(update={"composition_trace": (trace,)})
    reviewer = _FactReviewer()

    report = certify_facts(
        echoed,
        {"b0": _evidence(b"The square was destroyed.")},
        lambda _source: b"The square was destroyed.",
        _quality_calibration(),
        reviewer,
    )

    assert report.state == "PASS"
    assert reviewer.calls == 1


def test_llm_candidate_trace_must_cover_every_routed_stop() -> None:
    blueprint = _blueprint()
    second = blueprint.route.pois[0].model_copy(
        update={"id": "p1", "name": "Second routed stop"}
    )
    route = blueprint.route.model_copy(update={"pois": (*blueprint.route.pois, second)})
    second_script_poi = blueprint.script.selected_pois[0].model_copy(
        update={"id": "p1", "name": "Second routed stop"}
    )
    script = blueprint.script.model_copy(
        update={"selected_pois": (*blueprint.script.selected_pois, second_script_poi)}
    )
    incomplete = blueprint.model_copy(update={"route": route, "script": script})

    with pytest.raises(ValueError, match="cover every routed stop exactly once"):
        type(blueprint).model_validate(incomplete.model_dump(mode="python"))


def test_llm_candidate_rejects_duplicate_stop_traces() -> None:
    blueprint = _blueprint()
    trace = blueprint.composition_trace[0]
    duplicate = blueprint.model_copy(update={"composition_trace": (trace, trace)})

    with pytest.raises(ValueError, match="composition trace repeats a stop unit"):
        type(blueprint).model_validate(duplicate.model_dump(mode="python"))


def test_llm_candidate_rejects_mixed_provider_and_fallback_derivations() -> None:
    blueprint = _blueprint()
    provider = blueprint.composition_trace[0]
    fallback = provider.model_copy(update={"derivation": "stitched_fallback"})
    mixed = blueprint.model_copy(update={"composition_trace": (provider, fallback)})

    reason = validate_llm_composed_blueprint(mixed)

    assert reason is not None
    assert "provider_response" in reason


def test_provider_echo_equal_to_source_is_eligible_when_physically_traced() -> None:
    blueprint = _blueprint()
    trace = blueprint.composition_trace[0].model_copy(
        update={"authorized_source_sentences": blueprint.script.script}
    )
    echoed = blueprint.model_copy(update={"composition_trace": (trace,)})

    assert validate_llm_composed_blueprint(echoed) is None


def test_all_glue_provider_echo_is_eligible_when_physically_traced() -> None:
    blueprint = _blueprint(
        "And that brings our walk to a close.",
        source_type="glue",
        source_id="GLUE_CLOSING",
    )
    trace = blueprint.composition_trace[0].model_copy(
        update={"authorized_source_sentences": blueprint.script.script}
    )
    echoed = blueprint.model_copy(update={"composition_trace": (trace,)})

    assert validate_llm_composed_blueprint(echoed) is None


def test_llm_candidate_rejects_a_final_sentence_unbound_to_provider_payload() -> None:
    blueprint = _blueprint()
    extra = Sentence(
        text="A second supported detail.",
        source_id="b0",
        source_type="beat",
        stop_idx=0,
    )
    script = blueprint.script.model_copy(
        update={"script": (*blueprint.script.script, extra)}
    )

    with pytest.raises(ValueError, match="bind every final sentence exactly once"):
        build_final_blueprint(
            contract_sha256=blueprint.contract_sha256,
            reference_manifest_sha256=blueprint.reference_manifest_sha256,
            calibration_manifest_sha256=blueprint.calibration_manifest_sha256,
            build=blueprint.build,
            tour_input=blueprint.tour_input,
            route=blueprint.route,
            script=script,
            vignette_beat_ids=(),
            playback_assignments=derive_playback_assignments(
                script, vignette_beat_ids=()
            ),
            composition_trace=blueprint.composition_trace,
        )


def _remap_with_added_glue(source_id: str, **kwargs):
    """Remap a provider script that ADDED one sentence the frozen source lacks."""
    source = _blueprint().script
    added = Sentence(text="Now carry on toward the square.", source_id=source_id,
                     source_type="glue", stop_idx=0)
    provider = source.model_copy(update={"script": (*source.script, added)})
    return remap_provider_playback_assignments(
        source_script=source,
        source_assignments=derive_playback_assignments(source, vignette_beat_ids=()),
        provider_script=provider,
        **kwargs,
    )


def test_added_provider_sentence_fails_closed_by_default() -> None:
    """The strict default is what certification replay depends on — it must not move.

    MUTATION that must turn this red: make ``derivable_leg_source_ids`` default to a
    non-None value in ``remap_provider_playback_assignments``.
    """
    with pytest.raises(ValueError, match="cites no frozen playback source"):
        _remap_with_added_glue("GLUE_NAV")


def test_added_glue_sentence_is_placeable_when_the_caller_opts_in() -> None:
    """The interactive preview opts in so one added transition cannot bin a whole tour.

    Measured 2026-07-26: the composer legitimately emits a transition sentence, and the
    frozen stitch has no ``(source_id, stop_idx)`` pair for it, so a fully authored
    6-stop Paris tour was discarded whole. Placement is a PURE FUNCTION of source_id
    (``derive_playback_assignments``), so the derived value equals what the freeze
    would have produced — nothing is read from prose.

    MUTATION that must turn this red: delete the ``placements is None and
    derivable_leg_source_ids is not None`` branch.
    """
    out = _remap_with_added_glue("GLUE_NAV", derivable_leg_source_ids=frozenset({"GLUE_NAV"}))
    assert len(out) == 2, "the added sentence did not receive an assignment"
    assert out[-1].placement == "leg", (
        f"a concurrent glue label must play on the leg, got {out[-1].placement!r}"
    )
    assert out[-1].index == 0


def test_opting_in_still_rejects_an_invented_source_id() -> None:
    """The opt-in is a narrow allowance, NOT a bypass.

    An id that is neither a recognised glue label nor a beat the frozen source cites is
    invented provenance and must still fail closed, or the relaxation would launder
    hallucinated citations into a certifiable artifact.

    MUTATION that must turn this red: drop the membership test and place any sentence
    whose pair is missing.
    """
    with pytest.raises(ValueError, match="cites no frozen playback source"):
        _remap_with_added_glue("GLUE_INVENTED", derivable_leg_source_ids=frozenset({"GLUE_NAV"}))


def test_exact_enjoyment_anchor_slices_load_and_preserve_labels() -> None:
    calibration_sha, anchors = load_enjoyment_anchors(
        repo_root=ROOT,
        calibration_manifest_path=CALIBRATION,
        reference_manifest_path=REFERENCES,
    )
    assert calibration_sha == _sha(CALIBRATION.read_bytes())
    assert [anchor.expected for anchor in anchors] == ["PASS", "PASS", "PASS"]
    item_ids = [anchor.item.item_id for anchor in anchors]
    segment_ids = [anchor.item.segments[0].segment_id for anchor in anchors]
    assert len(item_ids) == len(set(item_ids)) == 3
    assert len(segment_ids) == len(set(segment_ids)) == 3
    assert all(item_id.startswith("item-") and len(item_id) == 29 for item_id in item_ids)
    assert all(
        segment_id.startswith("segment-") and len(segment_id) == 32
        for segment_id in segment_ids
    )
    serialized = json.dumps(
        [anchor.item.model_dump(mode="json") for anchor in anchors]
    )
    assert all(
        semantic_id not in serialized
        for semantic_id in (
            "le_meurice_01f",
            "le_meurice_01e",
            "le_meurice_01g",
            "le_meurice_01c_version_a",
            "le_meurice_01c_version_b",
        )
    )


class _QualityCalibrationReviewer:
    def __init__(
        self,
        *,
        wrong_fact_case: str | None = None,
        wrong_enjoyment_item: str | None = None,
    ):
        self.wrong_fact_case = wrong_fact_case
        self.wrong_enjoyment_item = wrong_enjoyment_item
        self.calls = 0

    def calibrate(self, fact_cases, enjoyment_anchors):
        from src.tour.quality_certification import _quality_calibration_input_sha256

        self.calls += 1
        fact_verdicts = []
        for case in fact_cases:
            decision = case.expected
            if case.case_id == self.wrong_fact_case:
                decision = "FAIL" if decision == "PASS" else "PASS"
            fact_verdicts.append(
                FactPolicyCalibrationVerdict(
                    case_id=case.case_id,
                    decision=decision,
                    explanation="Frozen policy boundary classification.",
                )
            )
        expected = {anchor.item.item_id: anchor.expected for anchor in enjoyment_anchors}
        enjoyment_assessments = []
        for item in sorted(
            (anchor.item for anchor in enjoyment_anchors),
            key=lambda candidate: candidate.item_id,
        ):
            decision = expected[item.item_id]
            if item.item_id == self.wrong_enjoyment_item:
                decision = "FAIL" if decision == "PASS" else "PASS"
            scores = (2, 2, 2, 2, 2) if decision == "PASS" else (0, 0, 0, 0, 0)
            enjoyment_assessments.append(_item_assessment(item, scores))
        return QualityCalibrationBatch(
            reviewer_model="claude-enjoy",
            policy_sha256=POLICY_SHA,
            approved_fact_prompt_sha256=PROMPT_SHA,
            approved_enjoy_prompt_sha256=PROMPT_SHA,
            approved_adjudication_prompt_sha256=PROMPT_SHA,
            input_sha256=_quality_calibration_input_sha256(
                fact_cases, enjoyment_anchors
            ),
            provider_response_sha256="c" * 64,
            fact_verdicts=tuple(fact_verdicts),
            enjoyment_assessments=tuple(enjoyment_assessments),
            usage=ReviewUsage(calls=1),
        )


def test_one_combined_call_calibrates_fact_boundaries_artifacts_and_enjoy_anchors() -> None:
    inputs = load_quality_calibration_inputs(
        repo_root=ROOT,
        calibration_manifest_path=CALIBRATION,
        reference_manifest_path=REFERENCES,
    )
    manifest = json.loads(CALIBRATION.read_text())
    assert len(inputs.fact_cases) == len(manifest["fact_policy_calibration"]["cases"])
    assert len(inputs.enjoyment_anchors) == 3
    assert not any(
        case.case_id in {"paris_a", "paris_b", "nyc_a", "nyc_b"}
        for case in inputs.fact_cases
    )
    reviewer = _QualityCalibrationReviewer()
    report = calibrate_quality(
        inputs=inputs,
        reviewer=reviewer,
    )
    assert report.state == "PASS", report.error
    assert reviewer.calls == 1
    assert [decision for _, decision in report.fact_decisions] == [
        case.expected for case in inputs.fact_cases
    ]


def test_any_fact_policy_calibration_mismatch_halts_as_calibration_failure() -> None:
    inputs = load_quality_calibration_inputs(
        repo_root=ROOT,
        calibration_manifest_path=CALIBRATION,
        reference_manifest_path=REFERENCES,
    )
    report = calibrate_quality(
        inputs=inputs,
        reviewer=_QualityCalibrationReviewer(
            wrong_fact_case=inputs.fact_cases[0].case_id
        ),
    )
    assert report.state == "FAIL"
    assert report.error is None


def test_manifest_hash_cannot_seal_a_partial_calibration_suite() -> None:
    inputs = load_quality_calibration_inputs(
        repo_root=ROOT,
        calibration_manifest_path=CALIBRATION,
        reference_manifest_path=REFERENCES,
    )
    from src.tour.quality_certification import quality_calibration_inputs_fingerprint

    partial = inputs.model_copy(
        update={"fact_cases": inputs.fact_cases[:-1], "bundle_sha256": "0" * 64}
    )
    partial = partial.model_copy(
        update={"bundle_sha256": quality_calibration_inputs_fingerprint(partial)}
    )
    reviewer = _QualityCalibrationReviewer()
    report = calibrate_quality(inputs=partial, reviewer=reviewer)
    assert report.state == "ERROR"
    assert "differs from the exact manifest" in (report.error or "")
    assert reviewer.calls == 0


def test_fact_preflight_rejects_missing_exact_source_without_spend() -> None:
    reviewer = _FactReviewer()
    report = certify_facts(
        _blueprint(), {}, lambda source: b"", _quality_calibration(), reviewer
    )
    assert report.state == "FAIL"
    assert report.deterministic_findings[0].code == "missing_evidence"
    assert reviewer.calls == 0


def test_fact_preflight_treats_mutated_source_blob_as_infrastructure_error() -> None:
    original = b"The square was destroyed."
    reviewer = _FactReviewer()
    report = certify_facts(
        _blueprint(),
        {"b0": _evidence(original)},
        lambda source: b"The square was preserved.",
        _quality_calibration(),
        reviewer,
    )
    assert report.state == "ERROR"
    assert "blob hash mismatch" in (report.error or "")
    assert reviewer.calls == 0


def test_fact_loader_outage_is_error_not_evidence_that_the_tour_is_false() -> None:
    blob = b"The square was destroyed."
    reviewer = _FactReviewer()

    def unavailable(source):
        raise OSError("source store offline")

    report = certify_facts(
        _blueprint(),
        {"b0": _evidence(blob)},
        unavailable,
        _quality_calibration(),
        reviewer,
    )
    assert report.state == "ERROR"
    assert "source store offline" in (report.error or "")
    assert reviewer.calls == 0


def test_generated_script_body_is_excluded_and_quote_typography_reaches_review() -> None:
    blob = b"The source says only that the square was destroyed."
    source = _evidence(blob).model_copy(
        update={"script_body": 'A witness called it "torn down in a night".'}
    )
    source = source.model_copy(
        update={"evidence_sha256": source_evidence_fingerprint(source)}
    )
    reviewer = _FactReviewer()
    report = certify_facts(
        _blueprint('A witness called it “torn down in a night”.'),
        {"b0": source},
        lambda item: blob,
        _quality_calibration(),
        reviewer,
    )
    assert report.state == "PASS"
    assert report.deterministic_findings == ()
    assert reviewer.calls == 1


def test_destroyed_to_torn_down_is_a_general_policy_calibration_pass() -> None:
    inputs = load_quality_calibration_inputs(
        repo_root=ROOT,
        calibration_manifest_path=CALIBRATION,
        reference_manifest_path=REFERENCES,
    )
    case = next(
        item
        for item in inputs.fact_cases
        if item.candidate_text == "The square was torn down."
    )
    assert case.source_text == "The square was destroyed."
    assert case.expected == "PASS"


def test_destroyed_to_came_down_is_a_general_policy_calibration_pass() -> None:
    inputs = load_quality_calibration_inputs(
        repo_root=ROOT,
        calibration_manifest_path=CALIBRATION,
        reference_manifest_path=REFERENCES,
    )
    case = next(
        item
        for item in inputs.fact_cases
        if item.candidate_text == "The square came down."
    )
    assert case.source_text == "The square was destroyed."
    assert case.expected == "PASS"


def test_invented_actor_is_a_material_fact_calibration_failure() -> None:
    inputs = load_quality_calibration_inputs(
        repo_root=ROOT,
        calibration_manifest_path=CALIBRATION,
        reference_manifest_path=REFERENCES,
    )
    case = next(
        item
        for item in inputs.fact_cases
        if item.candidate_text == "Revolutionaries tore the square down."
    )
    assert case.source_text == "The square was destroyed."
    assert case.expected == "FAIL"


def test_invented_cause_is_a_material_fact_calibration_failure() -> None:
    inputs = load_quality_calibration_inputs(
        repo_root=ROOT,
        calibration_manifest_path=CALIBRATION,
        reference_manifest_path=REFERENCES,
    )
    case = next(
        item
        for item in inputs.fact_cases
        if item.candidate_text == "A fire destroyed the building in 1802."
    )
    assert case.source_text == "The building was destroyed in 1802."
    assert case.expected == "FAIL"


def test_changed_date_is_a_material_fact_calibration_failure() -> None:
    inputs = load_quality_calibration_inputs(
        repo_root=ROOT,
        calibration_manifest_path=CALIBRATION,
        reference_manifest_path=REFERENCES,
    )
    case = next(
        item
        for item in inputs.fact_cases
        if item.candidate_text == "The hotel opened in 1837."
    )
    assert case.source_text == "The hotel opened in 1835."
    assert case.expected == "FAIL"


def test_changed_number_is_a_material_fact_calibration_failure() -> None:
    inputs = load_quality_calibration_inputs(
        repo_root=ROOT,
        calibration_manifest_path=CALIBRATION,
        reference_manifest_path=REFERENCES,
    )
    case = next(
        item
        for item in inputs.fact_cases
        if item.candidate_text == "The façade has eleven columns."
    )
    assert case.source_text == "The façade has twelve columns."
    assert case.expected == "FAIL"


def test_changed_attribution_is_a_material_fact_calibration_failure() -> None:
    inputs = load_quality_calibration_inputs(
        repo_root=ROOT,
        calibration_manifest_path=CALIBRATION,
        reference_manifest_path=REFERENCES,
    )
    case = next(
        item
        for item in inputs.fact_cases
        if item.candidate_text
        == "Thackeray stayed here while researching A Tale of Two Cities."
    )
    assert (
        case.source_text
        == "Dickens stayed here while researching A Tale of Two Cities."
    )
    assert case.expected == "FAIL"


def test_lost_uncertainty_is_a_material_fact_calibration_failure() -> None:
    inputs = load_quality_calibration_inputs(
        repo_root=ROOT,
        calibration_manifest_path=CALIBRATION,
        reference_manifest_path=REFERENCES,
    )
    case = next(
        item
        for item in inputs.fact_cases
        if item.candidate_text == "Orwell worked at Le Meurice."
    )
    assert "never named the hotel" in case.source_text
    assert "Paris lore" in case.source_text
    assert case.expected == "FAIL"


def test_altered_direct_quote_is_a_material_fact_calibration_failure() -> None:
    inputs = load_quality_calibration_inputs(
        repo_root=ROOT,
        calibration_manifest_path=CALIBRATION,
        reference_manifest_path=REFERENCES,
    )
    case = next(
        item
        for item in inputs.fact_cases
        if "servant of servants" in item.candidate_text
    )
    assert "slave of the slave" in case.source_text
    assert case.expected == "FAIL"


def test_atmospheric_connective_is_a_fact_calibration_pass() -> None:
    inputs = load_quality_calibration_inputs(
        repo_root=ROOT,
        calibration_manifest_path=CALIBRATION,
        reference_manifest_path=REFERENCES,
    )
    case = next(
        item
        for item in inputs.fact_cases
        if item.candidate_text == "For a moment, let the city fall quiet around you."
    )
    assert case.expected == "PASS"


def test_lovers_garden_rhetorical_connective_is_a_fact_calibration_pass() -> None:
    inputs = load_quality_calibration_inputs(
        repo_root=ROOT,
        calibration_manifest_path=CALIBRATION,
        reference_manifest_path=REFERENCES,
    )
    case = next(
        item
        for item in inputs.fact_cases
        if item.candidate_text.startswith("A king famous for his lovers")
    )
    assert "legendary amorous exploits" in case.source_text
    assert "popular haunt of lovers" in case.source_text
    assert case.expected == "PASS"


def test_fact_request_serializes_multiple_source_spans_once_in_a_shared_table() -> None:
    blob = b"The square was destroyed. Contemporary letters dispute who did it."
    first = _evidence(blob, "The square was destroyed.", evidence_id="source:b0:a")
    second = _evidence(
        blob,
        "Contemporary letters dispute who did it.",
        evidence_id="source:b0:b",
    )
    from src.tour.quality_certification import build_fact_review_input

    request, findings = build_fact_review_input(
        _blueprint(),
        {"b0": (first, second)},
        lambda item: blob,
    )
    assert findings == ()
    assert request.provider_narration_sha256 == sentences_payload_sha256(
        _blueprint().script.script
    )
    assert request.sentences[0].evidence_ids == ("source:b0:a", "source:b0:b")
    assert [item.evidence_id for item in request.evidence_table] == [
        "source:b0:a",
        "source:b0:b",
    ]
    serialized = request.model_dump(mode="json")["evidence_table"]
    assert all("script_body" not in item and "key_claims" not in item for item in serialized)
    assert [item["source_passage"] for item in serialized] == [
        first.source_passage,
        second.source_passage,
    ]
    assert "evidence" not in request.sentences[0].model_fields_set


def test_beat_sentences_require_material_fact_analysis() -> None:
    blob = b"The square was destroyed."
    from src.tour.quality_certification import build_fact_review_input

    request, findings = build_fact_review_input(
        _blueprint(),
        {"b0": _evidence(blob)},
        lambda item: blob,
    )
    assert findings == ()
    assert request.sentences[0].claim_requirement == "MATERIAL"


def test_factual_navigation_glue_has_blueprint_evidence_and_semantic_review() -> None:
    blueprint = _blueprint("Walk east to The Square.", source_type="glue")
    from src.tour.quality_certification import build_fact_review_input

    request, findings = build_fact_review_input(blueprint, {}, lambda item: b"")
    assert findings == ()
    assert request.sentences[0].evidence_ids == ("derived:0:GLUE_NAV",)
    evidence = request.evidence_table[0]
    assert evidence.evidence_kind == "blueprint_derivation"
    assert evidence.claim_requirement == "REVIEW_ALL"
    assert evidence.derivation_kind == "route_snapshot"
    assert '"destination_stop"' in evidence.derivation_payload_json
    assert '"tour_input"' not in evidence.derivation_payload_json
    assert '"script_totals"' not in evidence.derivation_payload_json

    reviewed = certify_facts(
        blueprint,
        {},
        lambda item: b"",
        _quality_calibration(),
        _FactReviewer(),
    )
    assert reviewed.state == "PASS"

    supported = certify_facts(
        blueprint,
        {},
        lambda item: b"",
        _quality_calibration(),
        _FactReviewer(nonbeat_material=True),
    )
    assert supported.state == "PASS"


def test_every_derived_sentence_reaches_semantic_fact_review() -> None:
    for text in (
        "You're starting in the Marais.",
        "Turn left.",
        "Notice the façade.",
        "You're standing at Pont Neuf.",
    ):
        reviewer = _FactReviewer()
        report = certify_facts(
            _blueprint(text, source_type="glue"),
            {},
            lambda item: b"",
            _quality_calibration(),
            reviewer,
        )
        assert report.state == "PASS", text
        assert reviewer.calls == 1


def test_fixed_real_tour_closing_can_finish_as_nonpropositional() -> None:
    """RE-DERIVED when authored per-stretch closes replaced the generic sign-off: the
    test extends to them rather than being deleted.
    The stitch's FALLBACK closes — the day's physical closure, the loop line, the last
    rest's line — are nonpropositional fixed text and certify PASS; an AUTHORED close (the
    one writer's, a GLUE_CLOSING line that lands the stop's point) is narrator content and
    is REVIEWED like every other derived sentence, never waved through on its label. The
    old sign-off ("keep exploring on your own") is gone."""
    from src.tour.contract import GENERIC_OPEN_TOUR_CLOSING, NONPROPOSITIONAL_GLUE_TEMPLATES

    assert GENERIC_OPEN_TOUR_CLOSING in NONPROPOSITIONAL_GLUE_TEMPLATES
    assert not any("exploring on your own" in t for t in NONPROPOSITIONAL_GLUE_TEMPLATES)
    for text in sorted(NONPROPOSITIONAL_GLUE_TEMPLATES):
        reviewer = _FactReviewer()
        report = certify_facts(
            _blueprint(text, source_type="glue", source_id="GLUE_CLOSING"),
            {},
            lambda item: b"",
            _quality_calibration(),
            reviewer,
        )
        assert report.state == "PASS", text
    # An authored close is content: reviewed.
    reviewer = _FactReviewer()
    report = certify_facts(
        _blueprint(
            "That's the Conciergerie — the Queen waited here, and so did the men who sent her.",
            source_type="glue",
            source_id="GLUE_CLOSING",
        ),
        {},
        lambda item: b"",
        _quality_calibration(),
        reviewer,
    )
    assert report.state == "PASS"
    assert reviewer.calls == 1


def test_real_sensory_invitation_emits_as_finishable_pacing() -> None:
    base = _blueprint()
    first_stop = POIBeats(
        poi_id="p0",
        poi_name="The Square",
        ordering_strategy="narrative_function",
        beats=(
            BeatRef(
                id="b0",
                poi_id="p0",
                physical_cues=(
                    PhysicalCue(cue="the skyline", feature_type="view"),
                ),
            ),
        ),
    )
    route = base.route.model_copy(
        update={
            "transits": (
                base.route.transits[0].model_copy(
                    update={"walk_seconds": 60, "leg_seconds": 60}
                ),
            )
        }
    )
    emitted = _build_synthesized_opener(
        first_stop, route, base.tour_input, stop_idx=0
    )
    invitation = next(item for item in emitted if item.text == SENSORY_INVITATION)
    assert invitation.source_id == GLUE_PACING

    report = certify_facts(
        _blueprint(
            invitation.text,
            source_type=invitation.source_type,
            source_id=invitation.source_id,
        ),
        {},
        lambda item: b"",
        _quality_calibration(),
        _FactReviewer(),
    )
    assert report.state == "PASS"


@pytest.mark.parametrize(
    ("source_id", "text", "decision", "nonbeat_material"),
    (
        ("GLUE_PACING", "Take a breath.", "PASS", False),
        (
            "GLUE_PACING",
            "The city exhales here.",
            "LICENSED_COLOR",
            True,
        ),
        (
            "GLUE_CLOSING",
            "The square saves its best surprise for last.",
            "LICENSED_COLOR",
            True,
        ),
    ),
)
def test_soft_glue_semantics_can_finish_without_an_exact_prose_whitelist(
    source_id: str,
    text: str,
    decision: str,
    nonbeat_material: bool,
) -> None:
    from src.tour.quality_certification import build_fact_review_input

    blueprint = _blueprint(text, source_type="glue", source_id=source_id)
    request, findings = build_fact_review_input(blueprint, {}, lambda item: b"")
    assert findings == ()
    assert request.sentences[0].claim_requirement == "REVIEW_ALL"

    report = certify_facts(
        blueprint,
        {},
        lambda item: b"",
        _quality_calibration(),
        _FactReviewer(
            decisions=(decision,), nonbeat_material=nonbeat_material
        ),
    )
    assert report.state == "PASS"


def test_derived_prose_uses_one_general_semantic_review_policy() -> None:
    from src.tour.quality_certification import build_fact_review_input

    blueprint = _blueprint(
        "Continue east to the square.",
        source_type="glue",
        source_id="provider-derived-source",
    )
    request, findings = build_fact_review_input(blueprint, {}, lambda item: b"")
    assert findings == ()
    assert request.sentences[0].claim_requirement == "REVIEW_ALL"


def test_caller_cannot_forge_derived_narration_authority() -> None:
    from src.tour.quality_certification import build_fact_review_input

    blueprint = _blueprint("Turn left.", source_type="glue")
    binding = blueprint.derived_narration_evidence[0]
    forged_json = '{"destination_stop":{"name":"Invented"}}'
    attacks = (
        binding.model_copy(
            update={
                "payload_json": forged_json,
                "payload_sha256": _sha(forged_json),
            }
        ),
        binding.model_copy(update={"claim_requirement": "NONPROPOSITIONAL"}),
        binding.model_copy(update={"evidence_kind": "corpus_field"}),
    )
    for attack in attacks:
        forged = blueprint.model_copy(
            update={"derived_narration_evidence": (attack,)}
        )
        with pytest.raises(ValueError, match="invalid final blueprint"):
            build_fact_review_input(forged, {}, lambda item: b"")


def test_changed_exact_quote_is_material_but_not_a_punctuation_only_preflight_veto() -> None:
    blob = b'The witness wrote "destroyed in a night" in the diary.'
    blueprint = _blueprint('The witness wrote “torn down in a night.”')
    reviewer = _FactReviewer()
    report = certify_facts(
        blueprint,
        {"b0": _evidence(blob)},
        lambda source: blob,
        _quality_calibration(),
        reviewer,
    )
    assert report.state == "PASS"
    assert report.deterministic_findings == ()
    assert reviewer.calls == 1


def test_mixed_and_single_quote_delimiters_do_not_create_deterministic_vetoes() -> None:
    blob = b"The source says only that the square was destroyed."
    reviewer = _FactReviewer()
    mixed = certify_facts(
        _blueprint('A witness called it “torn down in a night".'),
        {"b0": _evidence(blob)},
        lambda source: blob,
        _quality_calibration(),
        reviewer,
    )
    assert mixed.state == "PASS"
    assert mixed.deterministic_findings == ()

    single = certify_facts(
        _blueprint("A witness called it 'torn down in a night'."),
        {"b0": _evidence(blob)},
        lambda source: blob,
        _quality_calibration(),
        reviewer,
    )
    assert single.state == "PASS"
    assert single.deterministic_findings == ()
    assert reviewer.calls == 2


def test_scare_quote_editorial_gloss_cannot_permanently_reject_a_valid_tour() -> None:
    blob = b"The restoration replaced most of the original structure."
    text = (
        "Calling it a “repair” is generous: the restoration replaced most of "
        "the original structure."
    )
    reviewer = _FactReviewer()
    report = certify_facts(
        _blueprint(text),
        {"b0": _evidence(blob)},
        lambda source: blob,
        _quality_calibration(),
        reviewer,
    )
    assert report.state == "PASS"
    assert report.deterministic_findings == ()
    assert reviewer.calls == 1


def test_ascii_apostrophes_are_not_misread_as_quotation_delimiters() -> None:
    blob = b"You're here, and it's quiet."
    reviewer = _FactReviewer()
    report = certify_facts(
        _blueprint(blob.decode()),
        {"b0": _evidence(blob)},
        lambda source: blob,
        _quality_calibration(),
        reviewer,
    )
    assert report.state == "PASS"
    assert report.deterministic_findings == ()
    assert reviewer.calls == 1


def test_supported_material_fact_is_finishable() -> None:
    blob = b"The square was destroyed."
    report = certify_facts(
        _blueprint(),
        {"b0": _evidence(blob)},
        lambda source: blob,
        _quality_calibration(),
        _FactReviewer(decisions=("PASS",)),
    )
    assert report.state == "PASS"


def test_nonpropositional_licensed_colour_is_finishable() -> None:
    report = certify_facts(
        _blueprint(
            "The city exhales here.",
            source_type="glue",
            source_id="GLUE_PACING",
        ),
        {},
        lambda source: b"",
        _quality_calibration(),
        _FactReviewer(decisions=("LICENSED_COLOR",), nonbeat_material=True),
    )
    assert report.state == "PASS"


def test_editorial_overstatement_is_typed_local_repair_not_evaluation_error() -> None:
    blob = b"The square was destroyed."
    finding = _fact_finding("Revolutionaries tore the square down.")
    primary = _FactReviewer(decisions=("FAIL",), findings=(finding,))
    independent = _FactReviewer(decisions=("FAIL",), findings=(finding,))
    report = certify_facts(
        _blueprint("Revolutionaries tore the square down."),
        {"b0": _evidence(blob)},
        lambda source: blob,
        _quality_calibration(),
        primary,
        independent,
    )
    assert report.state == "FAIL"
    assert report.error is None
    assert report.consensus_findings == (finding,)
    assert report.repairable_findings == (finding,)
    assert primary.calls == independent.calls == 1


def test_one_fact_reviewer_cannot_issue_a_final_rejection() -> None:
    blob = b"The square was destroyed."
    finding = _fact_finding("Revolutionaries tore the square down.")
    report = certify_facts(
        _blueprint("Revolutionaries tore the square down."),
        {"b0": _evidence(blob)},
        lambda source: blob,
        _quality_calibration(),
        _FactReviewer(decisions=("FAIL",), findings=(finding,)),
    )
    assert report.state == "INCONCLUSIVE"
    assert report.consensus_findings == ()
    assert report.repairable_findings == ()
    assert "one factual reviewer" in (report.error or "")


def test_fact_reviewers_must_confirm_the_same_exact_defect_to_reject() -> None:
    blob = b"The square was destroyed."
    primary_finding = _fact_finding(
        "Revolutionaries tore the square down.",
        explanation="Unsupported actor and method.",
    )
    different_finding = _fact_finding(
        "Revolutionaries tore the square down.",
        code="misattribution",
        severity="blocker",
        explanation="A different alleged defect.",
    )
    report = certify_facts(
        _blueprint("Revolutionaries tore the square down."),
        {"b0": _evidence(blob)},
        lambda source: blob,
        _quality_calibration(),
        _FactReviewer(decisions=("FAIL",), findings=(primary_finding,)),
        _FactReviewer(decisions=("FAIL",), findings=(different_finding,)),
    )
    assert report.state == "INCONCLUSIVE"
    assert report.consensus_findings == ()
    assert report.repairable_findings == ()
    assert "did not confirm" in (report.error or "")


def test_same_fact_code_and_passage_but_different_atomic_claim_is_not_consensus() -> None:
    blob = b"The square was destroyed."
    text = "Revolutionaries tore the square down."
    primary = _fact_finding(text).model_copy(
        update={"material_claim": "Revolutionaries destroyed the square."}
    )
    independent = _fact_finding(text).model_copy(
        update={"material_claim": "The square was torn down by force."}
    )
    report = certify_facts(
        _blueprint(text),
        {"b0": _evidence(blob)},
        lambda source: blob,
        _quality_calibration(),
        _FactReviewer(decisions=("FAIL",), findings=(primary,)),
        _FactReviewer(decisions=("FAIL",), findings=(independent,)),
    )
    assert report.state == "INCONCLUSIVE"
    assert report.consensus_claim_ids == ()


def test_fact_reviewer_cannot_omit_the_second_clause_of_a_sentence() -> None:
    blob = b"The square was destroyed and witnesses disputed who did it."
    text = blob.decode()

    class _PartialReviewer(_FactReviewer):
        def review(self, request):
            batch = super().review(request)
            verdict = batch.verdicts[0]
            claim = verdict.claims[0]
            first_clause = "The square was destroyed"
            truncated_claim = claim.model_copy(
                update={
                    "passage_byte_end": len(first_clause.encode("utf-8")),
                    "exact_passage": first_clause,
                    "atomic_claim": first_clause,
                }
            )
            return batch.model_copy(
                update={
                    "verdicts": (
                        verdict.model_copy(update={"claims": (truncated_claim,)}),
                    )
                }
            )

    report = certify_facts(
        _blueprint(text),
        {"b0": _evidence(blob)},
        lambda source: blob,
        _quality_calibration(),
        _PartialReviewer(),
    )
    assert report.state == "ERROR"
    assert "omits sentence text" in (report.error or "")


def test_forged_fact_calibration_model_copy_is_rejected_by_fingerprint() -> None:
    blob = b"The square was destroyed."
    calibration = _quality_calibration().model_copy(
        update={"approved_fact_prompt_sha256": "8" * 64}
    )
    report = certify_facts(
        _blueprint(),
        {"b0": _evidence(blob)},
        lambda source: blob,
        calibration,
        _FactReviewer(),
    )
    assert report.state == "ERROR"
    assert "fingerprint mismatch" in (report.error or "")


def test_fact_reviewer_must_account_for_every_sentence() -> None:
    blob = b"The square was destroyed."
    report = certify_facts(
        _blueprint(),
        {"b0": _evidence(blob)},
        lambda source: blob,
        _quality_calibration(),
        _FactReviewer(malformed=True),
    )
    assert report.state == "ERROR"
    assert "every sentence" in (report.error or "")


def test_fact_reviewer_cannot_launder_a_beat_sentence_as_connective() -> None:
    blob = b"The square was destroyed."

    class _ConnectiveLaunderingReviewer(_FactReviewer):
        def review(self, request):
            batch = super().review(request)
            verdict = batch.verdicts[0]
            claim = verdict.claims[0].model_copy(
                update={
                    "atomic_claim": "No material assertion.",
                    "span_role": "CONNECTIVE",
                    "decision": "CONNECTIVE",
                    "material_type": "connective",
                    "evidence_ids": (),
                }
            )
            return batch.model_copy(
                update={
                    "verdicts": (
                        verdict.model_copy(
                            update={
                                "decision": "PASS",
                                "claims": (claim,),
                                "no_material_assertion_reason": "Treated as connective.",
                                "findings": (),
                            }
                        ),
                    )
                }
            )

    report = certify_facts(
        _blueprint(),
        {"b0": _evidence(blob)},
        lambda source: blob,
        _quality_calibration(),
        _ConnectiveLaunderingReviewer(),
    )
    assert report.state == "ERROR"
    assert "launders required material prose" in (report.error or "")


def test_fact_reviewer_cannot_cite_unknown_evidence() -> None:
    blob = b"The square was destroyed."

    class _UnknownEvidenceReviewer(_FactReviewer):
        def review(self, request):
            batch = super().review(request)
            verdict = batch.verdicts[0]
            claim = verdict.claims[0].model_copy(
                update={"evidence_ids": ("source:not-authorized",)}
            )
            return batch.model_copy(
                update={
                    "verdicts": (
                        verdict.model_copy(update={"claims": (claim,)}),
                    )
                }
            )

    report = certify_facts(
        _blueprint(),
        {"b0": _evidence(blob)},
        lambda source: blob,
        _quality_calibration(),
        _UnknownEvidenceReviewer(),
    )
    assert report.state == "ERROR"
    assert "alien evidence" in (report.error or "")


def test_fact_provider_timeout_is_error_not_false_tour_failure() -> None:
    blob = b"The square was destroyed."
    report = certify_facts(
        _blueprint(),
        {"b0": _evidence(blob)},
        lambda source: blob,
        _quality_calibration(),
        _FactReviewer(raises=True),
    )
    assert report.state == "ERROR"
    assert "TimeoutError" in (report.error or "")


class _EnjoyReviewer:
    def __init__(
        self,
        *,
        candidate_scores: tuple[int, int, int, int, int] = (2, 2, 2, 2, 2),
        slot: str = "first",
        model: str = "claude-enjoy",
        prompt: str = PROMPT_SHA,
    ):
        self.candidate_scores = candidate_scores
        self.slot = slot
        self.model = model
        self.prompt = prompt
        _, self.anchors = load_enjoyment_anchors(
            repo_root=ROOT,
            calibration_manifest_path=CALIBRATION,
            reference_manifest_path=REFERENCES,
        )
        self.calls = 0

    def review(self, items):
        from src.tour.quality_certification import _enjoyment_review_input_sha256

        self.calls += 1
        candidates = {item.item_id for item in items}
        anchor_labels = {
            anchor.item.item_id: anchor.expected for anchor in self.anchors
        }
        neutral_items = tuple(
            sorted(
                (*tuple(anchor.item for anchor in self.anchors), *items),
                key=lambda item: item.item_id,
            )
        )
        assessments = []
        for item in neutral_items:
            if item.item_id in candidates:
                scores = self.candidate_scores
            else:
                scores = (
                    (2, 2, 2, 2, 2)
                    if anchor_labels[item.item_id] == "PASS"
                    else (0, 0, 0, 0, 0)
                )
            assessments.append(_item_assessment(item, scores))
        return EnjoymentScoredReviewBatch(
            review_slot=self.slot,
            reviewer_model=self.model,
            policy_sha256=POLICY_SHA,
            prompt_sha256=self.prompt,
            input_sha256=_enjoyment_review_input_sha256(items, self.anchors),
            provider_response_sha256=("d" if self.slot == "first" else "e") * 64,
            assessments=tuple(assessments),
            usage=ReviewUsage(calls=1, input_tokens=100, output_tokens=10),
        )


def _calibrations():
    _, anchors, quality = _full_calibration()
    primary = enjoyment_calibration_from_quality(quality, anchors=anchors)
    assert primary.state == "PASS"
    return quality.calibration_manifest_sha256, primary, primary, anchors, quality


def test_basic_tour_is_ineligible_for_enjoyment_review_without_spend() -> None:
    calibration_sha, primary, independent, anchors, quality = _calibrations()
    basic = _blueprint(
        "A compact story.",
        source_type="glue",
        calibration_sha256=calibration_sha,
        reference_sha256=quality.reference_manifest_sha256,
    ).model_copy(update={"composition_trace": ()})
    reviewer = _EnjoyReviewer()

    report = certify_enjoyment(
        basic,
        anchors=anchors,
        quality_calibration=quality,
        primary_calibration=primary,
        primary_reviewer=reviewer,
        independent_calibration=independent,
        independent_reviewer=None,
    )

    assert report.state == "ERROR"
    assert "basic tour" in (report.error or "")
    assert reviewer.calls == 0


def test_provider_echo_with_physical_trace_reaches_enjoyment_reviewer() -> None:
    calibration_sha, primary, independent, anchors, quality = _calibrations()
    blueprint = _blueprint(
        calibration_sha256=calibration_sha,
        reference_sha256=quality.reference_manifest_sha256,
    )
    trace = blueprint.composition_trace[0].model_copy(
        update={"authorized_source_sentences": blueprint.script.script}
    )
    echoed = blueprint.model_copy(update={"composition_trace": (trace,)})
    reviewer = _EnjoyReviewer()

    report = certify_enjoyment(
        echoed,
        anchors=anchors,
        quality_calibration=quality,
        primary_calibration=primary,
        primary_reviewer=reviewer,
        independent_calibration=independent,
        independent_reviewer=None,
    )

    assert report.state == "INCONCLUSIVE"
    assert reviewer.calls == 1


def _full_calibration():
    inputs = load_quality_calibration_inputs(
        repo_root=ROOT,
        calibration_manifest_path=CALIBRATION,
        reference_manifest_path=REFERENCES,
    )
    quality = calibrate_quality(
        inputs=inputs,
        reviewer=_QualityCalibrationReviewer(),
    )
    assert quality.state == "PASS"
    return inputs.calibration_manifest_sha256, inputs.enjoyment_anchors, quality


class _QualityAdjudicator:
    def __init__(
        self,
        *,
        fact_decision: str = "PASS",
        fact_findings=(),
        candidate_scores: tuple[int, int, int, int, int] = (2, 2, 2, 2, 2),
    ):
        self.fact_decision = fact_decision
        self.fact_findings = fact_findings
        self.candidate_scores = candidate_scores
        self.calls = 0
        self.requests = []

    def review(self, request):
        self.calls += 1
        self.requests.append(request)
        assert request.fact_request is not None
        assert request.enjoyment_items
        fact_verdicts = _FactReviewer(
            decisions=(self.fact_decision,),
            findings=self.fact_findings,
            model="claude-enjoy",
        ).review(request.fact_request).verdicts
        candidates = {item.item_id for item in request.enjoyment_items}
        anchor_labels = {
            anchor.item.item_id: anchor.expected
            for anchor in request.enjoyment_anchors
        }
        neutral_items = tuple(
            sorted(
                (
                    *tuple(anchor.item for anchor in request.enjoyment_anchors),
                    *request.enjoyment_items,
                ),
                key=lambda item: item.item_id,
            )
        )
        assessments = []
        for item in neutral_items:
            if item.item_id in candidates:
                scores = self.candidate_scores
            else:
                scores = (
                    (2, 2, 2, 2, 2)
                    if anchor_labels[item.item_id] == "PASS"
                    else (0, 0, 0, 0, 0)
                )
            assessments.append(_item_assessment(item, scores))
        return QualityAdjudicationBatch(
            reviewer_model="claude-enjoy",
            policy_sha256=POLICY_SHA,
            prompt_sha256=PROMPT_SHA,
            input_sha256=request.fingerprint(),
            provider_response_sha256="e" * 64,
            fact_verdicts=fact_verdicts,
            enjoyment_assessments=tuple(assessments),
            usage=ReviewUsage(calls=1, input_tokens=200, output_tokens=20),
        )


def _certified_quality(
    *,
    text: str = "The square was destroyed.",
    primary_scores: tuple[int, int, int, int, int] = (2, 2, 2, 2, 2),
    independent_scores: tuple[int, int, int, int, int] = (2, 2, 2, 2, 2),
    primary_fact_decision: str = "PASS",
    independent_fact_decision: str = "PASS",
    fact_findings=(),
):
    calibration_sha, anchors, calibration = _full_calibration()
    blob = b"The square was destroyed."
    blueprint = _blueprint(
        text,
        calibration_sha256=calibration_sha,
        reference_sha256=calibration.reference_manifest_sha256,
    )
    report = certify_quality(
        blueprint,
        evidence_by_beat={"b0": _evidence(blob)},
        source_bytes_loader=lambda source: blob,
        calibration=calibration,
        enjoyment_anchors=anchors,
        fact_reviewer=_FactReviewer(
            decisions=(primary_fact_decision,),
            findings=fact_findings,
            model="claude-enjoy",
        ),
        enjoyment_reviewer=_EnjoyReviewer(candidate_scores=primary_scores),
        adjudicator=_QualityAdjudicator(
            fact_decision=independent_fact_decision,
            fact_findings=fact_findings,
            candidate_scores=independent_scores,
        ),
    )
    return blueprint, report


def test_fixed_quality_protocol_reviews_fact_and_scored_enjoyment_twice() -> None:
    calibration_sha, anchors, calibration = _full_calibration()
    blob = b"The square was destroyed."
    blueprint = _blueprint(
        calibration_sha256=calibration_sha,
        reference_sha256=calibration.reference_manifest_sha256,
    )
    adjudicator = _QualityAdjudicator()
    reviewer = _EnjoyReviewer()
    report = certify_quality(
        blueprint,
        evidence_by_beat={"b0": _evidence(blob)},
        source_bytes_loader=lambda source: blob,
        calibration=calibration,
        enjoyment_anchors=anchors,
        fact_reviewer=_FactReviewer(model="claude-enjoy"),
        enjoyment_reviewer=reviewer,
        adjudicator=adjudicator,
    )

    assert report.state == "PASS"
    assert reviewer.calls == adjudicator.calls == 1
    assert adjudicator.requests[0].fact_request is not None
    assert len(adjudicator.requests[0].enjoyment_items) == 1
    assert len(adjudicator.requests[0].enjoyment_anchors) == 3
    assert report.enjoyment is not None
    assert report.enjoyment.first_scored_review is not None
    assert report.enjoyment.second_scored_review is not None
    assert report.enjoyment.score_decision is not None


def test_primary_fact_pass_independent_wrong_agency_finding_is_inconclusive() -> None:
    text = "Revolutionaries tore the square down."
    finding = _fact_finding(
        text, explanation="The source does not support the actor or method."
    )
    _, report = _certified_quality(
        text=text,
        independent_fact_decision="FAIL",
        fact_findings=(finding,),
    )

    assert report.state == "INCONCLUSIVE"
    assert report.fact.state == "INCONCLUSIVE"
    assert report.fact.consensus_findings == ()


def test_two_fact_reviews_confirming_same_defect_can_finish_as_rejected() -> None:
    text = "Revolutionaries tore the square down."
    finding = _fact_finding(
        text, explanation="The source does not support the actor or method."
    )
    _, report = _certified_quality(
        text=text,
        primary_fact_decision="FAIL",
        independent_fact_decision="FAIL",
        fact_findings=(finding,),
    )

    assert report.state == "FAIL"
    assert report.fact.consensus_findings == (finding,)


def test_same_axis_below_positive_gold_consensus_rejects_candidate() -> None:
    _, report = _certified_quality(
        primary_scores=(1, 2, 2, 2, 2),
        independent_scores=(1, 2, 2, 2, 2),
    )

    assert report.state == "FAIL"
    assert report.enjoyment is not None
    assert report.enjoyment.consensus_below_gold_axes == ("site_presence",)
    assert report.enjoyment.score_decision is not None
    assert report.enjoyment.score_decision.decision == "FAIL"


def test_axis_score_dissent_is_advisory_without_consensus_failure() -> None:
    _, report = _certified_quality(
        primary_scores=(1, 2, 2, 2, 2),
        independent_scores=(2, 2, 2, 2, 2),
    )

    assert report.state == "PASS"
    assert report.enjoyment is not None
    assert report.enjoyment.consensus_below_gold_axes == ()
    assert report.enjoyment.dissent_is_advisory is True


def test_quality_calibration_requires_every_frozen_enjoyment_label() -> None:
    inputs = load_quality_calibration_inputs(
        repo_root=ROOT,
        calibration_manifest_path=CALIBRATION,
        reference_manifest_path=REFERENCES,
    )
    changed_item = inputs.enjoyment_anchors[0].item.item_id
    report = calibrate_quality(
        inputs=inputs,
        reviewer=_QualityCalibrationReviewer(
            wrong_enjoyment_item=changed_item,
        ),
    )

    assert report.state == "FAIL"


def test_enjoyment_anchor_suite_hash_binds_human_labels() -> None:
    _, anchors = load_enjoyment_anchors(
        repo_root=ROOT,
        calibration_manifest_path=CALIBRATION,
        reference_manifest_path=REFERENCES,
    )
    from src.tour.quality_certification import _anchors_sha256

    changed = (
        anchors[0].model_copy(update={"expected": "FAIL"}),
        *anchors[1:],
    )
    assert _anchors_sha256(changed) != _anchors_sha256(anchors)


def test_one_scored_enjoyment_review_is_provisional() -> None:
    (
        calibration_sha,
        primary_calibration,
        independent_calibration,
        anchors,
        quality,
    ) = _calibrations()
    blueprint = _blueprint(
        "Look across the square. One small detail changes the whole story.",
        source_type="glue",
        calibration_sha256=calibration_sha,
        reference_sha256=quality.reference_manifest_sha256,
    )
    report = certify_enjoyment(
        blueprint,
        anchors=anchors,
        quality_calibration=quality,
        primary_calibration=primary_calibration,
        primary_reviewer=_EnjoyReviewer(),
        independent_calibration=independent_calibration,
        independent_reviewer=None,
    )

    assert report.state == "INCONCLUSIVE"
    assert report.first_scored_review is not None
    assert report.second_scored_review is None
    assert report.score_decision is None
    assert "one enjoyment reviewer" in (report.error or "")


def test_scored_review_requires_candidate_and_every_anchor_in_order() -> None:
    calibration_sha, primary, independent, anchors, quality = _calibrations()
    blueprint = _blueprint(
        "A compact story with a clear turn and payoff.",
        source_type="glue",
        calibration_sha256=calibration_sha,
        reference_sha256=quality.reference_manifest_sha256,
    )
    base = _EnjoyReviewer()

    class _MissingAssessment:
        def review(self, items):
            batch = base.review(items)
            return batch.model_copy(update={"assessments": batch.assessments[:-1]})

    report = certify_enjoyment(
        blueprint,
        anchors=anchors,
        quality_calibration=quality,
        primary_calibration=primary,
        primary_reviewer=_MissingAssessment(),
        independent_calibration=independent,
        independent_reviewer=None,
    )

    assert report.state == "ERROR"
    assert "every opaque item exactly once in order" in (report.error or "")


def test_scored_axis_evidence_must_be_exact_utf8_in_the_same_item() -> None:
    calibration_sha, primary, independent, anchors, quality = _calibrations()
    blueprint = _blueprint(
        "A compact story with a clear turn and payoff.",
        source_type="glue",
        calibration_sha256=calibration_sha,
        reference_sha256=quality.reference_manifest_sha256,
    )
    base = _EnjoyReviewer()

    class _TamperedEvidence:
        def review(self, items):
            batch = base.review(items)
            candidate_id = items[0].item_id
            assessments = list(batch.assessments)
            index = next(
                position
                for position, assessment in enumerate(assessments)
                if assessment.item_id == candidate_id
            )
            assessment = assessments[index]
            axis = assessment.axes.site_presence
            span = axis.evidence[0].model_copy(
                update={"exact_passage": axis.evidence[0].exact_passage + "!"}
            )
            axis = axis.model_copy(update={"evidence": (span,)})
            axes = assessment.axes.model_copy(update={"site_presence": axis})
            assessments[index] = assessment.model_copy(update={"axes": axes})
            return batch.model_copy(update={"assessments": tuple(assessments)})

    report = certify_enjoyment(
        blueprint,
        anchors=anchors,
        quality_calibration=quality,
        primary_calibration=primary,
        primary_reviewer=_TamperedEvidence(),
        independent_calibration=independent,
        independent_reviewer=None,
    )

    assert report.state == "ERROR"
    assert "span is not exact UTF-8" in (report.error or "")


def test_primary_scored_review_cannot_claim_the_second_slot() -> None:
    calibration_sha, primary, independent, anchors, quality = _calibrations()
    blueprint = _blueprint(
        "A compact story with a clear turn and payoff.",
        source_type="glue",
        calibration_sha256=calibration_sha,
        reference_sha256=quality.reference_manifest_sha256,
    )

    report = certify_enjoyment(
        blueprint,
        anchors=anchors,
        quality_calibration=quality,
        primary_calibration=primary,
        primary_reviewer=_EnjoyReviewer(slot="second"),
        independent_calibration=independent,
        independent_reviewer=None,
    )

    assert report.state == "ERROR"
    assert "first independent slot" in (report.error or "")


def test_independent_scored_review_cannot_reuse_the_first_slot() -> None:
    calibration_sha, primary, independent, anchors, quality = _calibrations()
    blueprint = _blueprint(
        "A compact story with a clear turn and payoff.",
        source_type="glue",
        calibration_sha256=calibration_sha,
        reference_sha256=quality.reference_manifest_sha256,
    )

    report = certify_enjoyment(
        blueprint,
        anchors=anchors,
        quality_calibration=quality,
        primary_calibration=primary,
        primary_reviewer=_EnjoyReviewer(),
        independent_calibration=independent,
        independent_reviewer=_EnjoyReviewer(slot="first"),
    )

    assert report.state == "ERROR"
    assert "second independent slot" in (report.error or "")


def test_candidate_must_use_exact_calibrated_enjoyment_model_and_prompt() -> None:
    calibration_sha, primary, independent, anchors, quality = _calibrations()
    blueprint = _blueprint(
        "A compact story with a payoff.",
        source_type="glue",
        calibration_sha256=calibration_sha,
        reference_sha256=quality.reference_manifest_sha256,
    )

    report = certify_enjoyment(
        blueprint,
        anchors=anchors,
        quality_calibration=quality,
        primary_calibration=primary,
        primary_reviewer=_EnjoyReviewer(model="different-model"),
        independent_calibration=independent,
        independent_reviewer=None,
    )

    assert report.state == "ERROR"
    assert "differs from calibrated" in (report.error or "")


def test_terminal_quality_validation_recomputes_server_owned_enjoyment_decision() -> None:
    blueprint, report = _certified_quality()

    assert report.state == "PASS"
    assert validate_quality_gate_report(report, blueprint) is None
    assert report.enjoyment is not None
    assert report.enjoyment.score_decision is not None
    tampered_decision = report.enjoyment.score_decision.model_copy(
        update={"aggregate": report.enjoyment.score_decision.aggregate - 0.5}
    )
    tampered_enjoyment = report.enjoyment.model_copy(
        update={"score_decision": tampered_decision}
    )
    tampered_report = report.model_copy(update={"enjoyment": tampered_enjoyment})

    assert (
        validate_quality_gate_report(tampered_report, blueprint)
        == "terminal ENJOY result differs from trusted score recomputation"
    )


@pytest.mark.parametrize("response_hash", [None, "0" * 64])
def test_passing_calibration_requires_a_nonzero_provider_response_hash(
    response_hash: str | None,
) -> None:
    _blueprint_value, report = _certified_quality()
    tampered = report.calibration.model_copy(
        update={"provider_response_sha256": response_hash}
    )
    tampered = tampered.model_copy(
        update={"report_sha256": quality_calibration_report_fingerprint(tampered)}
    )

    assert (
        validate_quality_calibration_report(tampered)
        == "passing quality calibration report is incomplete"
    )
