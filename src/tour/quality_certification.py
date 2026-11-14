"""Typed FACT and ENJOY review contracts, their validators, and quality calibration.

The batch review validates provider output against these models: a reviewer must
cover every sentence and item, cite exact spans, and leave totals and release
decisions to trusted code. Calibration replays one provider call against the frozen
manifest labels.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .corpus_places import (
    SHA256_PATTERN,
    bytes_sha256,
    canonical_payload_bound_to_hash,
    canonical_sha256,
    model_sha256,
    text_sha256,
)

FACT_PROMPT_POLICY_VERSION = "tour-fact-review-v2"
ENJOY_RELEASE_THRESHOLD = 7.0
ENJOY_AXIS_NAMES = (
    "site_presence",
    "narrative_motion",
    "human_specificity",
    "spoken_voice",
    "editorial_control",
)
GateState = Literal["PASS", "FAIL", "INCONCLUSIVE", "ERROR"]


def _opaque_enjoyment_id(kind: Literal["item", "segment"], stable_id: str) -> str:
    """Derive a stable provider-visible ID without leaking semantic reference names."""
    return f"{kind}-{text_sha256(f'{kind}:{stable_id}')[:24]}"


class ReviewUsage(BaseModel):
    """Auditable provider usage for exactly one bounded review operation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    calls: int = Field(..., ge=0, le=1)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    estimated_cost_usd: float = Field(default=0, ge=0)


class FactSourceSpan(BaseModel):
    """Only byte-verified source fields that a FACT reviewer may treat as evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_kind: Literal["source_span"] = "source_span"
    evidence_id: str = Field(..., min_length=1)
    beat_id: str = Field(..., min_length=1)
    poi_id: str = Field(..., min_length=1)
    source_document_id: str = Field(..., min_length=1)
    source_revision: str = Field(..., min_length=1)
    source_chunk_slug: str = Field(..., min_length=1)
    source_blob_sha256: str = Field(..., pattern=SHA256_PATTERN)
    passage_byte_start: int = Field(..., ge=0)
    passage_byte_end: int = Field(..., gt=0)
    source_passage: str = Field(..., min_length=1)
    source_passage_sha256: str = Field(..., pattern=SHA256_PATTERN)


class FactDerivedEvidence(BaseModel):
    """Canonical blueprint values supporting navigation and derived glue claims."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_kind: Literal["blueprint_derivation"] = "blueprint_derivation"
    evidence_id: str = Field(..., min_length=1)
    blueprint_sha256: str = Field(..., pattern=SHA256_PATTERN)
    source_id: str = Field(..., min_length=1)
    stop_index: int = Field(..., ge=0)
    claim_requirement: Literal["MATERIAL", "REVIEW_ALL", "NONPROPOSITIONAL"]
    derivation_kind: Literal[
        "route_snapshot",
        "corpus_field",
        "visited_claims",
        "fixed_template",
    ]
    derivation_payload_json: str = Field(..., min_length=2)
    derivation_payload_sha256: str = Field(..., pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def _payload_is_canonical_and_bound(self) -> FactDerivedEvidence:
        canonical_payload_bound_to_hash(
            self.derivation_payload_json,
            self.derivation_payload_sha256,
            label="derived FACT evidence",
        )
        return self


FactEvidence = FactSourceSpan | FactDerivedEvidence


class FactSentenceInput(BaseModel):
    """One exact final sentence and only the evidence its citations authorize."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sentence_index: int = Field(..., ge=0)
    stop_index: int = Field(..., ge=0)
    sentence_sha256: str = Field(..., pattern=SHA256_PATTERN)
    text: str = Field(..., min_length=1)
    source_type: Literal["beat", "glue", "arith"]
    claim_requirement: Literal["REVIEW_ALL", "MATERIAL", "NONPROPOSITIONAL"]
    evidence_ids: tuple[str, ...] = ()


class FactReviewInput(BaseModel):
    """The complete batch sent to one factual reviewer call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    blueprint_sha256: str = Field(..., pattern=SHA256_PATTERN)
    provider_narration_sha256: str = Field(..., pattern=SHA256_PATTERN)
    policy_version: Literal["tour-fact-review-v2"] = FACT_PROMPT_POLICY_VERSION
    evidence_table: tuple[FactEvidence, ...] = ()
    sentences: tuple[FactSentenceInput, ...]

    @model_validator(mode="after")
    def _evidence_is_normalized_once(self) -> FactReviewInput:
        evidence_ids = [item.evidence_id for item in self.evidence_table]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("FACT evidence table contains duplicate ids")
        known = set(evidence_ids)
        for sentence in self.sentences:
            if len(sentence.evidence_ids) != len(set(sentence.evidence_ids)):
                raise ValueError("a FACT sentence repeats an evidence id")
            if not set(sentence.evidence_ids) <= known:
                raise ValueError("a FACT sentence cites evidence absent from the table")
        return self


FactFindingCode = Literal[
    "unsupported_material_claim",
    "contradiction",
    "misattribution",
    "quote_mismatch",
    "lost_uncertainty",
    "broken_entity",
    "missing_evidence",
]


class FactFinding(BaseModel):
    """One exact FACT defect; ``repairable`` is a local edit, not project doom."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: FactFindingCode
    severity: Literal["repairable", "blocker"]
    sentence_index: int = Field(..., ge=0)
    sentence_sha256: str = Field(..., pattern=SHA256_PATTERN)
    passage_byte_start: int = Field(..., ge=0)
    passage_byte_end: int = Field(..., gt=0)
    material_claim: str = Field(..., min_length=1)
    material_type: Literal[
        "named_entity_or_role",
        "attribution",
        "date_era_or_number",
        "location_or_direction",
        "specific_event",
        "historical_causation_or_agency",
        "superlative_or_ranking",
        "certainty_or_dispute_status",
        "quotation",
    ]
    exact_passage: str = Field(..., min_length=1)
    evidence_ids: tuple[str, ...] = ()
    explanation: str = Field(..., min_length=1)


class FactClaimDecision(BaseModel):
    """One atomic claim or licensed gloss; omission is impossible by schema."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    passage_byte_start: int = Field(..., ge=0)
    passage_byte_end: int = Field(..., gt=0)
    exact_passage: str = Field(..., min_length=1)
    atomic_claim: str = Field(..., min_length=1)
    span_role: Literal["MATERIAL_CLAIM", "LICENSED_GLOSS", "CONNECTIVE"]
    decision: Literal[
        "SUPPORTED",
        "LICENSED_GLOSS",
        "REPAIRABLE_NARROWING",
        "MATERIAL_DEFECT",
        "CONNECTIVE",
    ]
    material_type: Literal[
        "named_entity_or_role",
        "attribution",
        "date_era_or_number",
        "location_or_direction",
        "specific_event",
        "historical_causation_or_agency",
        "superlative_or_ranking",
        "certainty_or_dispute_status",
        "quotation",
        "licensed_nonpropositional_gloss",
        "connective",
    ]
    evidence_ids: tuple[str, ...] = ()
    explanation: str = Field(..., min_length=1)


class FactSentenceVerdict(BaseModel):
    """Reviewer decision for one sentence; licensed colour is explicitly a pass."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sentence_index: int = Field(..., ge=0)
    sentence_sha256: str = Field(..., pattern=SHA256_PATTERN)
    decision: Literal["PASS", "LICENSED_COLOR", "FAIL"]
    claims: tuple[FactClaimDecision, ...] = ()
    no_material_assertion_reason: str | None = Field(default=None, min_length=1)
    findings: tuple[FactFinding, ...] = ()


class FactReviewBatch(BaseModel):
    """Output of one batched model review."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reviewer_model: str = Field(..., min_length=1)
    policy_sha256: str = Field(..., pattern=SHA256_PATTERN)
    prompt_sha256: str = Field(..., pattern=SHA256_PATTERN)
    provider_response_sha256: str = Field(default="0" * 64, pattern=SHA256_PATTERN)
    verdicts: tuple[FactSentenceVerdict, ...]
    usage: ReviewUsage


class FactPolicyCalibrationCase(BaseModel):
    """One exact frozen boundary case for licensed colour versus changed fact."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(..., min_length=1)
    source_text: str = Field(..., min_length=1)
    candidate_text: str = Field(..., min_length=1)
    expected: Literal["PASS", "FAIL"]


class FactPolicyCalibrationVerdict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(..., min_length=1)
    decision: Literal["PASS", "FAIL"]
    explanation: str = Field(..., min_length=1)


def _exact_utf8_span(text: str, start: int, end: int) -> str | None:
    raw = text.encode("utf-8")
    if end > len(raw) or start >= end:
        return None
    try:
        return raw[start:end].decode("utf-8")
    except UnicodeDecodeError:
        return None


def _fact_batch_error(request: FactReviewInput, batch: FactReviewBatch) -> str | None:
    verdicts = batch.verdicts
    if [item.sentence_index for item in verdicts] != list(range(len(request.sentences))):
        return "fact reviewer must cover every sentence exactly once and in order"
    evidence_by_id = {item.evidence_id: item for item in request.evidence_table}
    for sentence, verdict in zip(request.sentences, verdicts, strict=True):
        if verdict.sentence_sha256 != sentence.sentence_sha256:
            return f"fact verdict hash mismatch at sentence {sentence.sentence_index}"
        if verdict.decision == "FAIL" and not verdict.findings:
            return f"failed fact verdict {sentence.sentence_index} has no exact finding"
        if verdict.decision != "FAIL" and verdict.findings:
            return f"passing fact verdict {sentence.sentence_index} carries a blocker"
        allowed_evidence = {
            evidence_id for evidence_id in sentence.evidence_ids if evidence_id in evidence_by_id
        }
        if verdict.claims:
            cursor = 0
            for claim in verdict.claims:
                if claim.passage_byte_start != cursor:
                    return f"fact verdict {sentence.sentence_index} leaves or overlaps text"
                exact = _exact_utf8_span(
                    sentence.text, claim.passage_byte_start, claim.passage_byte_end
                )
                if exact != claim.exact_passage:
                    return f"fact span at sentence {sentence.sentence_index} is not exact UTF-8"
                cursor = claim.passage_byte_end
            if cursor != len(sentence.text.encode("utf-8")):
                return f"fact verdict {sentence.sentence_index} omits sentence text"
            material_claims = tuple(
                claim for claim in verdict.claims if claim.span_role == "MATERIAL_CLAIM"
            )
            requires_material_analysis = sentence.claim_requirement == "MATERIAL"
            if requires_material_analysis and not material_claims:
                return (
                    f"fact verdict {sentence.sentence_index} launders required material "
                    "prose as connective or gloss"
                )
            if bool(material_claims) == bool(verdict.no_material_assertion_reason):
                return (
                    f"fact verdict {sentence.sentence_index} must explain only a genuinely "
                    "non-material sentence"
                )
            claim_decisions = {claim.decision for claim in verdict.claims}
            if verdict.decision == "PASS" and not claim_decisions <= {
                "SUPPORTED",
                "CONNECTIVE",
            }:
                return f"passing fact verdict {sentence.sentence_index} has a non-supported claim"
            if verdict.decision == "LICENSED_COLOR" and (
                "LICENSED_GLOSS" not in claim_decisions
                or not claim_decisions
                <= {
                    "SUPPORTED",
                    "LICENSED_GLOSS",
                    "CONNECTIVE",
                }
            ):
                return f"licensed-colour verdict {sentence.sentence_index} lacks licensed gloss"
            if verdict.decision == "FAIL" and not claim_decisions & {
                "REPAIRABLE_NARROWING",
                "MATERIAL_DEFECT",
            }:
                return f"failed fact verdict {sentence.sentence_index} lacks a failing claim"
        for claim in verdict.claims:
            if not set(claim.evidence_ids) <= allowed_evidence:
                return f"fact claim at sentence {sentence.sentence_index} cites alien evidence"
            if claim.span_role == "MATERIAL_CLAIM" and claim.decision not in {
                "SUPPORTED",
                "REPAIRABLE_NARROWING",
                "MATERIAL_DEFECT",
            }:
                return f"material claim at sentence {sentence.sentence_index} has wrong lane"
            if claim.span_role == "LICENSED_GLOSS" and (
                claim.decision != "LICENSED_GLOSS"
                or claim.material_type != "licensed_nonpropositional_gloss"
            ):
                return f"licensed gloss at sentence {sentence.sentence_index} is material"
            if claim.span_role == "CONNECTIVE" and (
                claim.decision != "CONNECTIVE" or claim.material_type != "connective"
            ):
                return f"connective at sentence {sentence.sentence_index} has wrong lane"
            if claim.decision == "SUPPORTED" and not claim.evidence_ids:
                return f"supported claim at sentence {sentence.sentence_index} has no evidence"
        for finding in verdict.findings:
            if (
                finding.sentence_index != sentence.sentence_index
                or finding.sentence_sha256 != sentence.sentence_sha256
                or _exact_utf8_span(
                    sentence.text,
                    finding.passage_byte_start,
                    finding.passage_byte_end,
                )
                != finding.exact_passage
            ):
                return f"fact finding at sentence {sentence.sentence_index} has wrong span"
            if not set(finding.evidence_ids) <= allowed_evidence:
                return f"fact finding at sentence {sentence.sentence_index} cites alien evidence"
            matching_claim = not verdict.claims or any(
                claim.span_role == "MATERIAL_CLAIM"
                and claim.decision in {"REPAIRABLE_NARROWING", "MATERIAL_DEFECT"}
                and claim.passage_byte_start == finding.passage_byte_start
                and claim.passage_byte_end == finding.passage_byte_end
                and claim.atomic_claim == finding.material_claim
                and claim.material_type == finding.material_type
                and set(claim.evidence_ids) == set(finding.evidence_ids)
                for claim in verdict.claims
            )
            if not matching_claim:
                return f"fact finding at sentence {sentence.sentence_index} lacks its claim span"
    return None


class EnjoymentSegment(BaseModel):
    """One ordered playback/stop segment retained for cross-stop review."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    segment_id: str = Field(..., min_length=1)
    stop_index: int = Field(..., ge=0)
    placement: Literal["leg", "stop", "anchor"]
    narration: str = Field(..., min_length=1)
    narration_sha256: str = Field(..., pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def _hash_matches_narration(self) -> EnjoymentSegment:
        if self.narration_sha256 != text_sha256(self.narration):
            raise ValueError("segment narration hash does not match text")
        return self


class EnjoymentItem(BaseModel):
    """Exact narration presented to an enjoyment reviewer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_id: str = Field(..., min_length=1)
    source_payload_sha256: str = Field(..., pattern=SHA256_PATTERN)
    narration: str = Field(..., min_length=1)
    narration_sha256: str = Field(..., pattern=SHA256_PATTERN)
    segments: tuple[EnjoymentSegment, ...] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _hash_matches_narration(self) -> EnjoymentItem:
        if self.narration_sha256 != text_sha256(self.narration):
            raise ValueError("narration_sha256 does not match enjoyment narration")
        if self.narration != "\n".join(segment.narration for segment in self.segments):
            raise ValueError("enjoyment narration does not match ordered segments")
        segment_ids = [segment.segment_id for segment in self.segments]
        if len(segment_ids) != len(set(segment_ids)):
            raise ValueError("enjoyment item repeats a segment id")
        return self


class EnjoymentCalibrationAnchor(BaseModel):
    """An exact anchor plus its human-frozen comparative-band label."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    item: EnjoymentItem
    expected: Literal["PASS", "FAIL"]


class EnjoymentEvidenceSpan(BaseModel):
    """One exact UTF-8 span inside a structured candidate or anchor segment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    segment_id: str = Field(..., min_length=1)
    passage_byte_start: int = Field(..., ge=0)
    passage_byte_end: int = Field(..., gt=0)
    exact_passage: str = Field(..., min_length=1)


class EnjoymentAxisAssessment(BaseModel):
    """One reviewer-authored axis score; aggregation remains server-owned."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    score: Literal[0, 1, 2]
    evidence: tuple[EnjoymentEvidenceSpan, ...] = Field(..., min_length=1)
    explanation: str = Field(..., min_length=1)


class EnjoymentAxisScores(BaseModel):
    """Named five-axis rubric, introduced one frozen axis at a time."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    site_presence: EnjoymentAxisAssessment
    narrative_motion: EnjoymentAxisAssessment
    human_specificity: EnjoymentAxisAssessment
    spoken_voice: EnjoymentAxisAssessment
    editorial_control: EnjoymentAxisAssessment


class EnjoymentScoredAssessment(BaseModel):
    """Provider-facing output: axis evidence only, never totals or release decisions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_id: str = Field(..., min_length=1)
    axes: EnjoymentAxisScores


class EnjoymentScoredReviewBatch(BaseModel):
    """One blind provider call containing only typed per-item axis assessments."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    review_slot: Literal["first", "second"]
    reviewer_model: str = Field(..., min_length=1)
    policy_sha256: str = Field(..., pattern=SHA256_PATTERN)
    prompt_sha256: str = Field(..., pattern=SHA256_PATTERN)
    provider_response_sha256: str = Field(..., pattern=SHA256_PATTERN)
    assessments: tuple[EnjoymentScoredAssessment, ...] = Field(..., min_length=1)
    usage: ReviewUsage


def enjoyment_reviewer_total(assessment: EnjoymentScoredAssessment) -> int:
    """Compute the reviewer total in trusted code; range is exactly 0-10."""
    axes = assessment.axes
    return sum(
        (
            axes.site_presence.score,
            axes.narrative_motion.score,
            axes.human_specificity.score,
            axes.spoken_voice.score,
            axes.editorial_control.score,
        )
    )


class QualityCalibrationBatch(BaseModel):
    """One physical calibration call covering FACT policy and ENJOY anchors."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reviewer_model: str = Field(..., min_length=1)
    policy_sha256: str = Field(..., pattern=SHA256_PATTERN)
    approved_fact_prompt_sha256: str = Field(..., pattern=SHA256_PATTERN)
    approved_enjoy_prompt_sha256: str = Field(..., pattern=SHA256_PATTERN)
    provider_response_sha256: str = Field(default="0" * 64, pattern=SHA256_PATTERN)
    fact_verdicts: tuple[FactPolicyCalibrationVerdict, ...]
    enjoyment_assessments: tuple[EnjoymentScoredAssessment, ...]
    usage: ReviewUsage


class QualityCalibrationReviewer(Protocol):
    def calibrate(
        self,
        fact_cases: tuple[FactPolicyCalibrationCase, ...],
        enjoyment_anchors: tuple[EnjoymentCalibrationAnchor, ...],
    ) -> QualityCalibrationBatch: ...


class QualityCalibrationInputs(BaseModel):
    """The exact FACT and ENJOY calibration suite derived from the frozen manifest."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    calibration_manifest_text: str = Field(..., min_length=2)
    reference_manifest_text: str = Field(..., min_length=2)
    calibration_manifest_sha256: str = Field(..., pattern=SHA256_PATTERN)
    reference_manifest_sha256: str = Field(..., pattern=SHA256_PATTERN)
    fact_cases: tuple[FactPolicyCalibrationCase, ...]
    enjoyment_anchors: tuple[EnjoymentCalibrationAnchor, ...]
    bundle_sha256: str = Field(..., pattern=SHA256_PATTERN)


def quality_calibration_inputs_fingerprint(inputs: QualityCalibrationInputs) -> str:
    return model_sha256(inputs, exclude={"bundle_sha256"})


class QualityCalibrationReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    calibration_manifest_sha256: str = Field(..., pattern=SHA256_PATTERN)
    reference_manifest_sha256: str = Field(..., pattern=SHA256_PATTERN)
    fact_suite_sha256: str = Field(..., pattern=SHA256_PATTERN)
    anchor_suite_sha256: str = Field(..., pattern=SHA256_PATTERN)
    input_bundle_sha256: str = Field(..., pattern=SHA256_PATTERN)
    input_sha256: str = Field(..., pattern=SHA256_PATTERN)
    inputs: QualityCalibrationInputs | None = None
    provider_response_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    state: GateState
    reviewer_model: str | None = None
    policy_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    approved_fact_prompt_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    approved_enjoy_prompt_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    fact_calibration_verdicts: tuple[FactPolicyCalibrationVerdict, ...] = ()
    fact_decisions: tuple[tuple[str, Literal["PASS", "FAIL"]], ...] = ()
    enjoyment_decisions: tuple[tuple[str, Literal["PASS", "FAIL"]], ...] = ()
    enjoyment_assessments: tuple[EnjoymentScoredAssessment, ...] = ()
    usage: ReviewUsage = ReviewUsage(calls=0)
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.state == "PASS"


def _anchors_sha256(anchors: tuple[EnjoymentCalibrationAnchor, ...]) -> str:
    return canonical_sha256([anchor.model_dump(mode="json") for anchor in anchors])


def _enjoyment_span_error(item: EnjoymentItem, span: EnjoymentEvidenceSpan) -> str | None:
    segment = next((part for part in item.segments if part.segment_id == span.segment_id), None)
    if segment is None:
        return f"unknown segment {span.segment_id}"
    if (
        _exact_utf8_span(segment.narration, span.passage_byte_start, span.passage_byte_end)
        != span.exact_passage
    ):
        return f"span is not exact UTF-8 in segment {span.segment_id}"
    return None


def resolve_enjoyment_evidence_offsets(
    assessments: tuple[EnjoymentScoredAssessment, ...],
    *,
    items: tuple[EnjoymentItem, ...],
) -> tuple[EnjoymentScoredAssessment, ...]:
    """Replace untrusted provider offsets with unique, server-derived UTF-8 offsets."""
    items_by_id = {item.item_id: item for item in items}
    if len(items_by_id) != len(items):
        raise ValueError("enjoyment evidence input repeats an item id")

    normalized: list[EnjoymentScoredAssessment] = []
    for assessment in assessments:
        item = items_by_id.get(assessment.item_id)
        if item is None:
            raise ValueError(f"unknown enjoyment item {assessment.item_id}")
        segments_by_id = {segment.segment_id: segment for segment in item.segments}
        axes: dict[str, EnjoymentAxisAssessment] = {}
        for axis_name in ENJOY_AXIS_NAMES:
            axis = getattr(assessment.axes, axis_name)
            evidence: list[EnjoymentEvidenceSpan] = []
            for span in axis.evidence:
                segment = segments_by_id.get(span.segment_id)
                if segment is None:
                    raise ValueError(f"unknown segment {span.segment_id}")
                narration = segment.narration.encode("utf-8")
                passage = span.exact_passage.encode("utf-8")
                start = narration.find(passage)
                if start < 0:
                    raise ValueError(
                        f"exact passage is absent from segment {span.segment_id}"
                    )
                if narration.find(passage, start + 1) >= 0:
                    raise ValueError(
                        f"exact passage is not unique in segment {span.segment_id}"
                    )
                evidence.append(
                    span.model_copy(
                        update={
                            "passage_byte_start": start,
                            "passage_byte_end": start + len(passage),
                        }
                    )
                )
            axes[axis_name] = axis.model_copy(update={"evidence": tuple(evidence)})
        normalized.append(
            assessment.model_copy(
                update={"axes": EnjoymentAxisScores.model_validate(axes)}
            )
        )
    return tuple(normalized)


def _neutral_enjoyment_items(
    items: tuple[EnjoymentItem, ...],
    anchors: tuple[EnjoymentCalibrationAnchor, ...],
) -> tuple[EnjoymentItem, ...]:
    neutral_items = tuple(
        sorted(
            (*tuple(anchor.item for anchor in anchors), *items),
            key=lambda item: item.item_id,
        )
    )
    if len({item.item_id for item in neutral_items}) != len(neutral_items):
        raise ValueError("blind enjoyment input repeats an opaque item id")
    return neutral_items


def _validate_scored_enjoyment_batch(
    items: tuple[EnjoymentItem, ...],
    batch: EnjoymentScoredReviewBatch,
    *,
    anchors: tuple[EnjoymentCalibrationAnchor, ...],
    expected_slot: Literal["first", "second"],
) -> str | None:
    """Validate the exact item roster and same-item axis evidence."""
    if batch.review_slot != expected_slot:
        return f"enjoyment review must occupy the {expected_slot} independent slot"
    neutral_items = _neutral_enjoyment_items(items, anchors)
    expected_ids = tuple(item.item_id for item in neutral_items)
    actual_ids = tuple(item.item_id for item in batch.assessments)
    if actual_ids != expected_ids:
        return "enjoyment reviewer must assess every opaque item exactly once in order"
    by_id = {item.item_id: item for item in neutral_items}
    for assessment in batch.assessments:
        item = by_id[assessment.item_id]
        for axis_name in ENJOY_AXIS_NAMES:
            axis = getattr(assessment.axes, axis_name)
            for evidence in axis.evidence:
                span_error = _enjoyment_span_error(item, evidence)
                if span_error:
                    return (
                        f"enjoyment assessment {assessment.item_id} axis "
                        f"{axis_name}: {span_error}"
                    )
    return None


def _single_review_enjoyment_decision(
    assessment: EnjoymentScoredAssessment,
) -> Literal["PASS", "FAIL"]:
    total_passes = enjoyment_reviewer_total(assessment) >= ENJOY_RELEASE_THRESHOLD
    every_axis_present = all(
        getattr(assessment.axes, axis_name).score > 0
        for axis_name in ENJOY_AXIS_NAMES
    )
    return "PASS" if total_passes and every_axis_present else "FAIL"


def _quality_calibration_input_sha256(
    fact_cases: tuple[FactPolicyCalibrationCase, ...],
    enjoyment_anchors: tuple[EnjoymentCalibrationAnchor, ...],
) -> str:
    return canonical_sha256(
        {
            "fact_cases": [case.model_dump(mode="json") for case in fact_cases],
            "enjoyment_anchors": [
                anchor.model_dump(mode="json") for anchor in enjoyment_anchors
            ],
        }
    )


def _fact_calibration_suite_sha256(
    fact_cases: tuple[FactPolicyCalibrationCase, ...],
) -> str:
    return canonical_sha256([case.model_dump(mode="json") for case in fact_cases])


def calibrate_quality(
    *,
    inputs: QualityCalibrationInputs,
    reviewer: QualityCalibrationReviewer,
) -> QualityCalibrationReport:
    """Use exactly one provider call to calibrate both frozen quality policies."""
    calibration_manifest_sha256 = inputs.calibration_manifest_sha256
    reference_manifest_sha256 = inputs.reference_manifest_sha256
    fact_cases = inputs.fact_cases
    enjoyment_anchors = inputs.enjoyment_anchors
    input_hash = _quality_calibration_input_sha256(fact_cases, enjoyment_anchors)
    fact_suite_hash = _fact_calibration_suite_sha256(fact_cases)
    anchor_suite_hash = _anchors_sha256(enjoyment_anchors)
    try:
        batch = reviewer.calibrate(fact_cases, enjoyment_anchors)
    except Exception as exc:
        return QualityCalibrationReport(
            calibration_manifest_sha256=calibration_manifest_sha256,
            reference_manifest_sha256=reference_manifest_sha256,
            fact_suite_sha256=fact_suite_hash,
            anchor_suite_sha256=anchor_suite_hash,
            input_bundle_sha256=inputs.bundle_sha256,
            input_sha256=input_hash,
            inputs=inputs,
            state="ERROR",
            error=f"quality calibration error: {type(exc).__name__}: {exc}",
        )
    if [verdict.case_id for verdict in batch.fact_verdicts] != [
        case.case_id for case in fact_cases
    ]:
        error = "quality calibration must cover every FACT policy case in order"
    else:
        enjoy_batch = EnjoymentScoredReviewBatch(
            review_slot="first",
            reviewer_model=batch.reviewer_model,
            policy_sha256=batch.policy_sha256,
            prompt_sha256=batch.approved_enjoy_prompt_sha256,
            provider_response_sha256=batch.provider_response_sha256,
            assessments=batch.enjoyment_assessments,
            usage=batch.usage,
        )
        error = _validate_scored_enjoyment_batch(
            (),
            enjoy_batch,
            anchors=enjoyment_anchors,
            expected_slot="first",
        )
    if error:
        return QualityCalibrationReport(
            calibration_manifest_sha256=calibration_manifest_sha256,
            reference_manifest_sha256=reference_manifest_sha256,
            fact_suite_sha256=fact_suite_hash,
            anchor_suite_sha256=anchor_suite_hash,
            input_bundle_sha256=inputs.bundle_sha256,
            input_sha256=input_hash,
            inputs=inputs,
            provider_response_sha256=batch.provider_response_sha256,
            state="ERROR",
            reviewer_model=batch.reviewer_model,
            policy_sha256=batch.policy_sha256,
            approved_fact_prompt_sha256=batch.approved_fact_prompt_sha256,
            approved_enjoy_prompt_sha256=batch.approved_enjoy_prompt_sha256,
            usage=batch.usage,
            error=error,
        )
    fact_decisions = tuple((verdict.case_id, verdict.decision) for verdict in batch.fact_verdicts)
    enjoyment_decisions = tuple(
        (assessment.item_id, _single_review_enjoyment_decision(assessment))
        for assessment in batch.enjoyment_assessments
    )
    expected_fact = {case.case_id: case.expected for case in fact_cases}
    expected_enjoy = {anchor.item.item_id: anchor.expected for anchor in enjoyment_anchors}
    passed = all(
        expected_fact[case_id] == decision for case_id, decision in fact_decisions
    ) and all(expected_enjoy[item_id] == decision for item_id, decision in enjoyment_decisions)
    return QualityCalibrationReport(
        calibration_manifest_sha256=calibration_manifest_sha256,
        reference_manifest_sha256=reference_manifest_sha256,
        fact_suite_sha256=fact_suite_hash,
        anchor_suite_sha256=anchor_suite_hash,
        input_bundle_sha256=inputs.bundle_sha256,
        input_sha256=input_hash,
        inputs=inputs,
        provider_response_sha256=batch.provider_response_sha256,
        state="PASS" if passed else "FAIL",
        reviewer_model=batch.reviewer_model,
        policy_sha256=batch.policy_sha256,
        approved_fact_prompt_sha256=batch.approved_fact_prompt_sha256,
        approved_enjoy_prompt_sha256=batch.approved_enjoy_prompt_sha256,
        fact_calibration_verdicts=batch.fact_verdicts,
        fact_decisions=fact_decisions,
        enjoyment_decisions=enjoyment_decisions,
        enjoyment_assessments=batch.enjoyment_assessments,
        usage=batch.usage,
    )


#: Where a reference document the frozen manifests name actually lives today.
#:
#: The manifests are SEALED BY HASH. `reference_manifest_sha256` is derived from
#: their exact bytes and is bound into every candidate identity in the frozen
#: batch under `data/certification/tour-batch-v1`, which is the control arm a
#: regeneration is compared against. Editing a path inside a manifest would
#: change that hash and silently invalidate the comparison. So a manifest keeps
#: saying where a document WAS, and this table says where it IS.
#:
#: The manifests name three reference documents under `specs/`. They are inputs
#: this module OPENS at runtime, which makes them fixtures, so they live under
#: `fixtures/certification-references/`. The loader below verifies each
#: document's bytes against the manifest's own sha256, so a wrong file at the
#: mapped path fails exactly as loudly as a missing one.
#:
#: A path living in JSON DATA rather than in source is invisible to a search over
#: the code, so any move of these documents needs a line here.
REFERENCE_DOCUMENT_MOVES: tuple[tuple[str, str], ...] = (
    ("specs/", "fixtures/certification-references/"),
)


def reference_document_path(repo_root: Path, manifest_path: str) -> Path:
    """The on-disk path for a document the frozen manifest names.

    A prefix swap and nothing cleverer: the rest of the manifest's path is kept
    exactly, so two documents can never collide and the mapping stays readable
    beside the manifest that motivated it.
    """
    for was, now in REFERENCE_DOCUMENT_MOVES:
        if manifest_path.startswith(was):
            return repo_root / (now + manifest_path[len(was):])
    return repo_root / manifest_path


def load_enjoyment_anchors(
    *,
    repo_root: Path,
    calibration_manifest_path: Path,
    reference_manifest_path: Path,
) -> tuple[str, tuple[EnjoymentCalibrationAnchor, ...]]:
    """Load only exact, hash-verified ENJOY anchors from the frozen manifests."""
    calibration_bytes = calibration_manifest_path.read_bytes()
    calibration = json.loads(calibration_bytes)
    references = json.loads(reference_manifest_path.read_bytes())
    docs = {document["id"]: document for document in references["documents"]}
    anchors: list[EnjoymentCalibrationAnchor] = []
    for anchor in calibration["anchors"]:
        expected = anchor["expected"]["ENJOY"]
        if expected not in {"PASS", "FAIL"}:
            continue
        document = docs[anchor["reference_document_id"]]
        path = reference_document_path(repo_root, document["path"])
        raw = path.read_bytes()
        if bytes_sha256(raw) != document["sha256"]:
            raise ValueError(f"reference document hash mismatch: {document['id']}")
        slice_spec = anchor["slice"]
        start_marker = slice_spec["start_marker"].encode("utf-8")
        start_token = start_marker + b"\n"
        if raw.count(start_token) != 1:
            raise ValueError(f"anchor start marker is not unique: {anchor['id']}")
        start = raw.index(start_token) + len(start_token)
        if slice_spec["end_marker"] is None:
            selected = raw[start:]
        else:
            end_token = b"\n" + slice_spec["end_marker"].encode("utf-8") + b"\n"
            candidates: list[bytes] = []
            search_at = start
            while True:
                end = raw.find(end_token, search_at)
                if end < 0:
                    break
                candidate = raw[start:end]
                if (
                    len(candidate) == slice_spec["size_bytes"]
                    and bytes_sha256(candidate) == slice_spec["sha256"]
                ):
                    candidates.append(candidate)
                search_at = end + 1
            if len(candidates) != 1:
                raise ValueError(
                    f"anchor end marker does not resolve one hashed slice: {anchor['id']}"
                )
            selected = candidates[0]
        if len(selected) != slice_spec["size_bytes"]:
            raise ValueError(f"anchor byte size mismatch: {anchor['id']}")
        if bytes_sha256(selected) != slice_spec["sha256"]:
            raise ValueError(f"anchor slice hash mismatch: {anchor['id']}")
        narration = selected.decode("utf-8")
        opaque_item_id = _opaque_enjoyment_id("item", anchor["id"])
        opaque_segment_id = _opaque_enjoyment_id("segment", anchor["id"])
        anchors.append(
            EnjoymentCalibrationAnchor(
                item=EnjoymentItem(
                    item_id=opaque_item_id,
                    source_payload_sha256=slice_spec["sha256"],
                    narration=narration,
                    narration_sha256=text_sha256(narration),
                    segments=(
                        EnjoymentSegment(
                            segment_id=opaque_segment_id,
                            stop_index=0,
                            placement="stop",
                            narration=narration,
                            narration_sha256=text_sha256(narration),
                        ),
                    ),
                ),
                expected=expected,
            )
        )
    if not anchors:
        raise ValueError("frozen calibration contains no exact enjoyment anchors")
    return bytes_sha256(calibration_bytes), tuple(anchors)


def load_quality_calibration_inputs(
    *,
    repo_root: Path,
    calibration_manifest_path: Path,
    reference_manifest_path: Path,
) -> QualityCalibrationInputs:
    """Load and seal the exact manifest-derived FACT and ENJOY calibration suite."""
    calibration_bytes = calibration_manifest_path.read_bytes()
    reference_bytes = reference_manifest_path.read_bytes()
    calibration = json.loads(calibration_bytes)
    calibration_sha, anchors = load_enjoyment_anchors(
        repo_root=repo_root,
        calibration_manifest_path=calibration_manifest_path,
        reference_manifest_path=reference_manifest_path,
    )
    boundary_cases = tuple(
        FactPolicyCalibrationCase(
            case_id=f"fact-policy-{index + 1}",
            source_text=case["source_text"],
            candidate_text=case["candidate_text"],
            expected=case["expected_fact"],
        )
        for index, case in enumerate(calibration["fact_policy_calibration"]["cases"])
    )
    if not boundary_cases:
        raise ValueError("frozen FACT policy calibration must contain boundary cases")
    inputs = QualityCalibrationInputs(
        calibration_manifest_text=calibration_bytes.decode("utf-8"),
        reference_manifest_text=reference_bytes.decode("utf-8"),
        calibration_manifest_sha256=calibration_sha,
        reference_manifest_sha256=bytes_sha256(reference_bytes),
        fact_cases=boundary_cases,
        enjoyment_anchors=anchors,
        bundle_sha256="0" * 64,
    )
    return inputs.model_copy(
        update={"bundle_sha256": quality_calibration_inputs_fingerprint(inputs)}
    )


__all__ = [
    "ENJOY_AXIS_NAMES",
    "ENJOY_RELEASE_THRESHOLD",
    "EnjoymentAxisAssessment",
    "EnjoymentAxisScores",
    "EnjoymentCalibrationAnchor",
    "EnjoymentEvidenceSpan",
    "EnjoymentItem",
    "EnjoymentScoredAssessment",
    "EnjoymentScoredReviewBatch",
    "EnjoymentSegment",
    "FactClaimDecision",
    "FactDerivedEvidence",
    "FactFinding",
    "FactPolicyCalibrationCase",
    "FactPolicyCalibrationVerdict",
    "FactReviewBatch",
    "FactReviewInput",
    "FactSentenceVerdict",
    "FactSourceSpan",
    "QualityCalibrationBatch",
    "QualityCalibrationInputs",
    "QualityCalibrationReport",
    "QualityCalibrationReviewer",
    "ReviewUsage",
    "calibrate_quality",
    "enjoyment_reviewer_total",
    "load_enjoyment_anchors",
    "load_quality_calibration_inputs",
    "resolve_enjoyment_evidence_offsets",
]
