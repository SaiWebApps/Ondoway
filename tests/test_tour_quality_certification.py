"""Tests for the FACT and ENJOY review contracts, their validators and quality calibration."""

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
    FACT_PROMPT_POLICY_VERSION,
    EnjoymentAxisAssessment,
    EnjoymentAxisScores,
    EnjoymentEvidenceSpan,
    EnjoymentItem,
    EnjoymentScoredAssessment,
    EnjoymentScoredReviewBatch,
    EnjoymentSegment,
    FactClaimDecision,
    FactDerivedEvidence,
    FactPolicyCalibrationCase,
    FactPolicyCalibrationVerdict,
    FactReviewBatch,
    FactReviewInput,
    FactSentenceInput,
    FactSentenceVerdict,
    QualityCalibrationBatch,
    ReviewUsage,
    _anchors_sha256,
    _fact_batch_error,
    _validate_scored_enjoyment_batch,
    calibrate_quality,
    enjoyment_reviewer_total,
    load_enjoyment_anchors,
    load_quality_calibration_inputs,
)

ROOT = Path(__file__).resolve().parents[1]
# The calibration inputs are FIXTURES: tests read fixtures/, never planning prose.
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
        provider_response_sha256="d" * 64,
        assessments=(assessment,),
        usage=ReviewUsage(calls=1, input_tokens=100, output_tokens=20),
    )
    assert batch.assessments == (assessment,)
    assert batch.usage.calls == 1


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


def _fact_request(
    text: str = "The square was destroyed.", *, source_type: str = "beat"
) -> FactReviewInput:
    """A one-sentence FACT batch whose sentence cites one piece of derived evidence."""
    payload_json = json.dumps(
        {"source_passage": text}, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    claim_requirement = "MATERIAL" if source_type == "beat" else "REVIEW_ALL"
    evidence = FactDerivedEvidence(
        evidence_id="source:b0",
        blueprint_sha256="a" * 64,
        source_id="b0",
        stop_index=0,
        claim_requirement=claim_requirement,
        derivation_kind="corpus_field" if source_type == "beat" else "route_snapshot",
        derivation_payload_json=payload_json,
        derivation_payload_sha256=_sha(payload_json),
    )
    return FactReviewInput(
        blueprint_sha256="a" * 64,
        provider_narration_sha256="b" * 64,
        evidence_table=(evidence,),
        sentences=(
            FactSentenceInput(
                sentence_index=0,
                stop_index=0,
                sentence_sha256=_sha(text),
                text=text,
                source_type=source_type,
                claim_requirement=claim_requirement,
                evidence_ids=(evidence.evidence_id,),
            ),
        ),
    )


class _FactReviewer:
    def __init__(
        self,
        decisions=("PASS",),
        findings=(),
        *,
        malformed=False,
        nonbeat_material=False,
    ):
        self.decisions = decisions
        self.findings = findings
        self.malformed = malformed
        self.nonbeat_material = nonbeat_material

    def review(self, request):
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
            reviewer_model="claude-test",
            policy_sha256=POLICY_SHA,
            prompt_sha256=PROMPT_SHA,
            provider_response_sha256="a" * 64,
            verdicts=verdicts,
            usage=ReviewUsage(calls=1, input_tokens=100, output_tokens=10),
        )


def test_basic_tour_is_ineligible_for_fact_review() -> None:
    basic = _blueprint().model_copy(update={"composition_trace": ()})

    assert "basic tour" in (validate_llm_composed_blueprint(basic) or "")


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
    assert "LLM provider responses" in reason


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


def test_caller_cannot_forge_derived_narration_authority() -> None:
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
        with pytest.raises(ValueError, match="narration"):
            type(blueprint).model_validate(forged.model_dump(mode="python"))


def test_supported_material_fact_is_finishable() -> None:
    request = _fact_request()
    batch = _FactReviewer(decisions=("PASS",)).review(request)
    assert _fact_batch_error(request, batch) is None


def test_nonpropositional_licensed_colour_is_finishable() -> None:
    request = _fact_request("The city exhales here.", source_type="glue")
    batch = _FactReviewer(decisions=("LICENSED_COLOR",), nonbeat_material=True).review(request)
    assert _fact_batch_error(request, batch) is None


def test_fact_reviewer_cannot_omit_the_second_clause_of_a_sentence() -> None:
    text = "The square was destroyed and witnesses disputed who did it."

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

    request = _fact_request(text)
    error = _fact_batch_error(request, _PartialReviewer().review(request))
    assert "omits sentence text" in (error or "")


def test_fact_reviewer_must_account_for_every_sentence() -> None:
    request = _fact_request()
    error = _fact_batch_error(request, _FactReviewer(malformed=True).review(request))
    assert "every sentence" in (error or "")


def test_fact_reviewer_cannot_launder_a_beat_sentence_as_connective() -> None:
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

    request = _fact_request()
    error = _fact_batch_error(request, _ConnectiveLaunderingReviewer().review(request))
    assert "launders required material prose" in (error or "")


def test_fact_reviewer_cannot_cite_unknown_evidence() -> None:
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

    request = _fact_request()
    error = _fact_batch_error(request, _UnknownEvidenceReviewer().review(request))
    assert "alien evidence" in (error or "")


class _EnjoyReviewer:
    def __init__(
        self,
        *,
        candidate_scores: tuple[int, int, int, int, int] = (2, 2, 2, 2, 2),
        slot: str = "first",
    ):
        self.candidate_scores = candidate_scores
        self.slot = slot
        _, self.anchors = load_enjoyment_anchors(
            repo_root=ROOT,
            calibration_manifest_path=CALIBRATION,
            reference_manifest_path=REFERENCES,
        )

    def review(self, items):
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
            reviewer_model="claude-enjoy",
            policy_sha256=POLICY_SHA,
            prompt_sha256=PROMPT_SHA,
            provider_response_sha256=("d" if self.slot == "first" else "e") * 64,
            assessments=tuple(assessments),
            usage=ReviewUsage(calls=1, input_tokens=100, output_tokens=10),
        )


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
    changed = (
        anchors[0].model_copy(update={"expected": "FAIL"}),
        *anchors[1:],
    )
    assert _anchors_sha256(changed) != _anchors_sha256(anchors)


def _candidate_item(text: str = "A compact story with a clear turn and payoff.") -> EnjoymentItem:
    segment = EnjoymentSegment(
        segment_id="segment-candidate",
        stop_index=0,
        placement="stop",
        narration=text,
        narration_sha256=_sha(text),
    )
    return EnjoymentItem(
        item_id="item-candidate",
        source_payload_sha256="a" * 64,
        narration=text,
        narration_sha256=_sha(text),
        segments=(segment,),
    )


def _scored_review_error(reviewer, *, expected_slot: str = "first") -> str | None:
    items = (_candidate_item(),)
    return _validate_scored_enjoyment_batch(
        items,
        reviewer.review(items),
        anchors=_EnjoyReviewer().anchors,
        expected_slot=expected_slot,
    )


def test_a_complete_scored_review_is_accepted() -> None:
    assert _scored_review_error(_EnjoyReviewer()) is None


def test_scored_review_requires_candidate_and_every_anchor_in_order() -> None:
    base = _EnjoyReviewer()

    class _MissingAssessment:
        def review(self, items):
            batch = base.review(items)
            return batch.model_copy(update={"assessments": batch.assessments[:-1]})

    error = _scored_review_error(_MissingAssessment())
    assert "every opaque item exactly once in order" in (error or "")


def test_scored_axis_evidence_must_be_exact_utf8_in_the_same_item() -> None:
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

    error = _scored_review_error(_TamperedEvidence())
    assert "span is not exact UTF-8" in (error or "")


def test_primary_scored_review_cannot_claim_the_second_slot() -> None:
    error = _scored_review_error(_EnjoyReviewer(slot="second"), expected_slot="first")
    assert "first independent slot" in (error or "")


def test_independent_scored_review_cannot_reuse_the_first_slot() -> None:
    error = _scored_review_error(_EnjoyReviewer(slot="first"), expected_slot="second")
    assert "second independent slot" in (error or "")
