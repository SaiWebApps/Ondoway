"""Bound, finishable FACT and ENJOY certification over one frozen blueprint.

The deterministic parts of quality review live here; model intelligence is injected
through small protocols.  A model may propose a rejection, but it cannot invent a new
rubric, omit sentences silently, reject on a numeric score, or produce an uncited
enjoyment veto.  Every report is bound to the exact blueprint fingerprint.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .artifact import (
    FinalTourBlueprint,
    sentences_payload_sha256,
    validate_blueprint_integrity,
    validate_llm_composed_blueprint,
)
from .corpus_places import bytes_sha256, canonical_payload_bound_to_hash, text_sha256

SHA256_PATTERN = r"^[0-9a-f]{64}$"
FACT_PROMPT_POLICY_VERSION = "tour-fact-review-v2"
ENJOY_PROMPT_POLICY_VERSION = "tour-enjoy-review-v2"
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


def _canonical_sha256(value: BaseModel) -> str:
    payload = json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return bytes_sha256(payload)


class ReviewUsage(BaseModel):
    """Auditable provider usage for exactly one bounded review operation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    calls: int = Field(..., ge=0, le=1)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    estimated_cost_usd: float = Field(default=0, ge=0)


class SourceEvidence(BaseModel):
    """Locatable immutable source material supporting one cited beat.

    ``script_body`` and ``key_claims`` are non-authoritative extraction aids.
    Only the byte-verified ``source_passage`` can prove a quotation or claim.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

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
    key_claims: tuple[str, ...]
    script_body: str = Field(..., min_length=1)
    evidence_sha256: str = Field(..., pattern=SHA256_PATTERN)


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
    """The complete, hash-bound batch sent to one factual reviewer call."""

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

    def fingerprint(self) -> str:
        return _canonical_sha256(self)


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
    input_sha256: str = Field(..., pattern=SHA256_PATTERN)
    provider_response_sha256: str = Field(default="0" * 64, pattern=SHA256_PATTERN)
    verdicts: tuple[FactSentenceVerdict, ...]
    usage: ReviewUsage


class FactReviewer(Protocol):
    """One provider call at most for the complete candidate sentence batch."""

    def review(self, request: FactReviewInput) -> FactReviewBatch: ...


class EvidenceInfrastructureError(ValueError):
    """Corrupt/unavailable evidence plumbing; never proof that narration is false."""


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


class FactGateReport(BaseModel):
    """Final factual result with typed repair targets and no score threshold."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    blueprint_sha256: str = Field(..., pattern=SHA256_PATTERN)
    input_sha256: str = Field(..., pattern=SHA256_PATTERN)
    state: GateState
    reviewer_model: str | None = None
    policy_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    prompt_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    provider_response_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    independent_reviewer_model: str | None = None
    independent_policy_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    independent_prompt_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    independent_provider_response_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    deterministic_findings: tuple[FactFinding, ...] = ()
    verdicts: tuple[FactSentenceVerdict, ...] = ()
    independent_verdicts: tuple[FactSentenceVerdict, ...] = ()
    consensus_findings: tuple[FactFinding, ...] = ()
    consensus_claim_ids: tuple[str, ...] = ()
    usage: ReviewUsage = ReviewUsage(calls=0)
    independent_usage: ReviewUsage = ReviewUsage(calls=0)
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.state == "PASS"

    @property
    def repairable_findings(self) -> tuple[FactFinding, ...]:
        return tuple(finding for finding in self.all_findings if finding.severity == "repairable")

    @property
    def all_findings(self) -> tuple[FactFinding, ...]:
        # Primary model findings remain evidence in ``verdicts``, but only
        # deterministic defects or independently confirmed findings are actionable.
        return (*self.deterministic_findings, *self.consensus_findings)


def source_evidence_fingerprint(source: SourceEvidence) -> str:
    """Hash evidence content while excluding its self-declared fingerprint."""
    return bytes_sha256(
        json.dumps(
            source.model_dump(mode="json", exclude={"evidence_sha256"}),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )


def validate_source_evidence(
    source: SourceEvidence,
    source_bytes_loader: Callable[[SourceEvidence], bytes],
) -> None:
    """Reload the exact source span or raise a typed infrastructure error."""
    if source.evidence_sha256 != source_evidence_fingerprint(source):
        raise EvidenceInfrastructureError(f"evidence fingerprint mismatch: {source.evidence_id}")
    if source.source_passage_sha256 != text_sha256(source.source_passage):
        raise EvidenceInfrastructureError(f"source passage hash mismatch: {source.evidence_id}")
    try:
        blob = source_bytes_loader(source)
    except (FileNotFoundError, KeyError, OSError, ValueError) as exc:
        raise EvidenceInfrastructureError(
            f"source blob unavailable for {source.evidence_id}: {exc}"
        ) from exc
    if bytes_sha256(blob) != source.source_blob_sha256:
        raise EvidenceInfrastructureError(f"source blob hash mismatch: {source.evidence_id}")
    if source.passage_byte_end > len(blob):
        raise EvidenceInfrastructureError(
            f"source passage bounds exceed blob: {source.evidence_id}"
        )
    try:
        passage = blob[source.passage_byte_start : source.passage_byte_end].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvidenceInfrastructureError(
            f"source passage bounds split UTF-8: {source.evidence_id}"
        ) from exc
    if passage != source.source_passage:
        raise EvidenceInfrastructureError(f"source passage bytes mismatch: {source.evidence_id}")


EvidenceByBeat = dict[str, SourceEvidence | tuple[SourceEvidence, ...]]


def _evidence_for_beat(
    evidence_by_beat: EvidenceByBeat, beat_id: str
) -> tuple[SourceEvidence, ...]:
    value = evidence_by_beat.get(beat_id)
    if value is None:
        return ()
    return value if isinstance(value, tuple) else (value,)


def _reviewer_source_span(source: SourceEvidence) -> FactSourceSpan:
    """Drop extraction aids before any provider can see factual evidence."""
    return FactSourceSpan(
        evidence_id=source.evidence_id,
        beat_id=source.beat_id,
        poi_id=source.poi_id,
        source_document_id=source.source_document_id,
        source_revision=source.source_revision,
        source_chunk_slug=source.source_chunk_slug,
        source_blob_sha256=source.source_blob_sha256,
        passage_byte_start=source.passage_byte_start,
        passage_byte_end=source.passage_byte_end,
        source_passage=source.source_passage,
        source_passage_sha256=source.source_passage_sha256,
    )


def _derived_evidence_for_sentence(
    blueprint: FinalTourBlueprint, sentence_index: int
) -> FactDerivedEvidence:
    sentence = blueprint.script.script[sentence_index]
    binding = next(
        (
            item
            for item in blueprint.derived_narration_evidence
            if item.sentence_index == sentence_index
        ),
        None,
    )
    if binding is None:
        raise EvidenceInfrastructureError(
            f"non-beat sentence {sentence_index} lacks frozen derivation evidence"
        )
    return FactDerivedEvidence(
        evidence_id=f"derived:{sentence_index}:{sentence.source_id}",
        blueprint_sha256=blueprint.fingerprint(),
        source_id=sentence.source_id,
        stop_index=sentence.stop_idx,
        claim_requirement=binding.claim_requirement,
        derivation_kind=binding.evidence_kind,
        derivation_payload_json=binding.payload_json,
        derivation_payload_sha256=binding.payload_sha256,
    )


def build_fact_review_input(
    blueprint: FinalTourBlueprint,
    evidence_by_beat: EvidenceByBeat,
    source_bytes_loader: Callable[[SourceEvidence], bytes],
) -> tuple[FactReviewInput, tuple[FactFinding, ...]]:
    """Build the complete review batch and fail closed on missing/altered evidence."""
    integrity_error = validate_blueprint_integrity(blueprint)
    if integrity_error:
        raise ValueError(f"invalid final blueprint: {integrity_error}")
    eligibility_error = validate_llm_composed_blueprint(blueprint)
    if eligibility_error:
        raise ValueError(f"blueprint is ineligible for FACT review: {eligibility_error}")

    preflight: list[FactFinding] = []
    sentence_inputs: list[FactSentenceInput] = []
    evidence_table: dict[str, FactEvidence] = {}
    seated_owner = {
        beat_id: poi.id for poi in blueprint.script.selected_pois for beat_id in poi.beat_ids
    }
    for sentence_index, sentence in enumerate(blueprint.script.script):
        evidence: list[SourceEvidence] = []
        if sentence.source_type == "beat":
            for beat_id in sentence.cited_beat_ids:
                sources = _evidence_for_beat(evidence_by_beat, beat_id)
                if not sources:
                    preflight.append(
                        FactFinding(
                            code="missing_evidence",
                            severity="blocker",
                            sentence_index=sentence_index,
                            sentence_sha256=text_sha256(sentence.text),
                            passage_byte_start=0,
                            passage_byte_end=len(sentence.text.encode("utf-8")),
                            material_claim=sentence.text,
                            material_type="specific_event",
                            exact_passage=sentence.text,
                            evidence_ids=(),
                            explanation=(
                                f"cited beat {beat_id!r} lacks exact locatable evidence: "
                                "no source span was supplied"
                            ),
                        )
                    )
                    continue
                for source in sources:
                    if source.beat_id != beat_id:
                        raise EvidenceInfrastructureError(
                            f"evidence {source.evidence_id} belongs to beat "
                            f"{source.beat_id}, not {beat_id}"
                        )
                    owner = seated_owner.get(beat_id)
                    if owner is not None and source.poi_id != owner:
                        raise EvidenceInfrastructureError(
                            f"evidence {source.evidence_id} belongs to POI "
                            f"{source.poi_id}, not seated owner {owner}"
                        )
                    validate_source_evidence(source, source_bytes_loader)
                    reviewer_span = _reviewer_source_span(source)
                    prior = evidence_table.get(source.evidence_id)
                    if prior is not None and prior != reviewer_span:
                        raise EvidenceInfrastructureError(
                            f"evidence id {source.evidence_id} has conflicting content"
                        )
                    evidence_table[source.evidence_id] = reviewer_span
                    evidence.append(source)

            # Punctuation alone cannot distinguish attributed verbatim speech from
            # scare quotes or editorial gloss in an audio script. Exact-quotation
            # claims remain material, but the calibrated two-reviewer FACT path owns
            # that semantic decision; deterministic preflight only proves evidence
            # identity and availability.

        if sentence.source_type in {"glue", "arith"}:
            derived = _derived_evidence_for_sentence(blueprint, sentence_index)
            evidence_table[derived.evidence_id] = derived

        sentence_inputs.append(
            FactSentenceInput(
                sentence_index=sentence_index,
                stop_index=sentence.stop_idx,
                sentence_sha256=text_sha256(sentence.text),
                text=sentence.text,
                source_type=sentence.source_type,
                claim_requirement=(
                    "MATERIAL" if sentence.source_type == "beat" else derived.claim_requirement
                ),
                evidence_ids=(
                    tuple(source.evidence_id for source in evidence)
                    if sentence.source_type == "beat"
                    else (derived.evidence_id,)
                ),
            )
        )
    return (
        FactReviewInput(
            blueprint_sha256=blueprint.fingerprint(),
            provider_narration_sha256=sentences_payload_sha256(
                blueprint.script.script
            ),
            evidence_table=tuple(evidence_table.values()),
            sentences=tuple(sentence_inputs),
        ),
        tuple(preflight),
    )


def fact_finding_claim_id(finding: FactFinding) -> str:
    """Stable identity used for two-reviewer factual consensus."""
    payload = {
        "code": finding.code,
        "severity": finding.severity,
        "sentence_index": finding.sentence_index,
        "sentence_sha256": finding.sentence_sha256,
        "passage_byte_start": finding.passage_byte_start,
        "passage_byte_end": finding.passage_byte_end,
        "material_claim": finding.material_claim,
        "material_type": finding.material_type,
        "evidence_ids": sorted(finding.evidence_ids),
    }
    return bytes_sha256(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))


def _exact_utf8_span(text: str, start: int, end: int) -> str | None:
    raw = text.encode("utf-8")
    if end > len(raw) or start >= end:
        return None
    try:
        return raw[start:end].decode("utf-8")
    except UnicodeDecodeError:
        return None


def _fact_batch_error(request: FactReviewInput, batch: FactReviewBatch) -> str | None:
    if batch.usage.calls != 1:
        return "fact reviewer must consume exactly one bounded batch call"
    if batch.input_sha256 != request.fingerprint():
        return "fact reviewer output is bound to a different input"
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


def certify_facts(
    blueprint: FinalTourBlueprint,
    evidence_by_beat: EvidenceByBeat,
    source_bytes_loader: Callable[[SourceEvidence], bytes],
    calibration: QualityCalibrationReport,
    reviewer: FactReviewer,
    independent_reviewer: FactReviewer | None = None,
) -> FactGateReport:
    """Run preflight then at most one batch review; malformed output fails as ERROR."""
    calibration_error = validate_quality_calibration_report(calibration)
    if calibration_error:
        return FactGateReport(
            blueprint_sha256=blueprint.fingerprint(),
            input_sha256="0" * 64,
            state="ERROR",
            error=calibration_error,
        )
    if not calibration.passed:
        return FactGateReport(
            blueprint_sha256=blueprint.fingerprint(),
            input_sha256="0" * 64,
            state="ERROR",
            error="the FACT reviewer has not passed frozen quality calibration",
        )
    if calibration.calibration_manifest_sha256 != blueprint.calibration_manifest_sha256:
        return FactGateReport(
            blueprint_sha256=blueprint.fingerprint(),
            input_sha256="0" * 64,
            state="ERROR",
            error="FACT calibration manifest does not match the blueprint",
        )
    if calibration.reference_manifest_sha256 != blueprint.reference_manifest_sha256:
        return FactGateReport(
            blueprint_sha256=blueprint.fingerprint(),
            input_sha256="0" * 64,
            state="ERROR",
            error="FACT calibration reference manifest does not match the blueprint",
        )
    try:
        request, deterministic_findings = build_fact_review_input(
            blueprint, evidence_by_beat, source_bytes_loader
        )
    except (TypeError, ValueError) as exc:
        return FactGateReport(
            blueprint_sha256=blueprint.fingerprint(),
            input_sha256="0" * 64,
            state="ERROR",
            error=str(exc),
        )
    if deterministic_findings:
        return FactGateReport(
            blueprint_sha256=request.blueprint_sha256,
            input_sha256=request.fingerprint(),
            state="FAIL",
            deterministic_findings=deterministic_findings,
        )
    try:
        batch = reviewer.review(request)
    except Exception as exc:  # provider errors are typed, never false factual vetoes
        return FactGateReport(
            blueprint_sha256=request.blueprint_sha256,
            input_sha256=request.fingerprint(),
            state="ERROR",
            error=f"fact review error: {type(exc).__name__}: {exc}",
        )
    error = _fact_batch_error(request, batch)
    if not error and (
        batch.reviewer_model != calibration.reviewer_model
        or batch.policy_sha256 != calibration.policy_sha256
        or batch.prompt_sha256 != calibration.approved_fact_prompt_sha256
    ):
        error = "FACT reviewer model/policy/prompt differs from calibrated reviewer"
    if error:
        return FactGateReport(
            blueprint_sha256=request.blueprint_sha256,
            input_sha256=request.fingerprint(),
            state="ERROR",
            reviewer_model=batch.reviewer_model,
            policy_sha256=batch.policy_sha256,
            prompt_sha256=batch.prompt_sha256,
            provider_response_sha256=batch.provider_response_sha256,
            usage=batch.usage,
            error=error,
        )
    if not any(verdict.decision == "FAIL" for verdict in batch.verdicts):
        return FactGateReport(
            blueprint_sha256=request.blueprint_sha256,
            input_sha256=request.fingerprint(),
            state="PASS",
            reviewer_model=batch.reviewer_model,
            policy_sha256=batch.policy_sha256,
            prompt_sha256=batch.prompt_sha256,
            provider_response_sha256=batch.provider_response_sha256,
            verdicts=batch.verdicts,
            usage=batch.usage,
        )
    if independent_reviewer is None:
        return FactGateReport(
            blueprint_sha256=request.blueprint_sha256,
            input_sha256=request.fingerprint(),
            state="INCONCLUSIVE",
            reviewer_model=batch.reviewer_model,
            policy_sha256=batch.policy_sha256,
            prompt_sha256=batch.prompt_sha256,
            provider_response_sha256=batch.provider_response_sha256,
            verdicts=batch.verdicts,
            usage=batch.usage,
            error="one factual reviewer cannot issue a final rejection",
        )
    try:
        independent = independent_reviewer.review(request)
    except Exception as exc:
        return FactGateReport(
            blueprint_sha256=request.blueprint_sha256,
            input_sha256=request.fingerprint(),
            state="ERROR",
            reviewer_model=batch.reviewer_model,
            policy_sha256=batch.policy_sha256,
            prompt_sha256=batch.prompt_sha256,
            provider_response_sha256=batch.provider_response_sha256,
            verdicts=batch.verdicts,
            usage=batch.usage,
            error=f"independent FACT review error: {type(exc).__name__}: {exc}",
        )
    independent_error = _fact_batch_error(request, independent)
    if not independent_error and (
        independent.reviewer_model != calibration.reviewer_model
        or independent.policy_sha256 != calibration.policy_sha256
        or independent.prompt_sha256 != calibration.approved_fact_prompt_sha256
    ):
        independent_error = "independent FACT reviewer model/policy/prompt differs from calibration"
    if independent_error:
        return FactGateReport(
            blueprint_sha256=request.blueprint_sha256,
            input_sha256=request.fingerprint(),
            state="ERROR",
            reviewer_model=batch.reviewer_model,
            policy_sha256=batch.policy_sha256,
            prompt_sha256=batch.prompt_sha256,
            provider_response_sha256=batch.provider_response_sha256,
            verdicts=batch.verdicts,
            usage=batch.usage,
            independent_usage=independent.usage,
            independent_reviewer_model=independent.reviewer_model,
            independent_policy_sha256=independent.policy_sha256,
            independent_prompt_sha256=independent.prompt_sha256,
            independent_provider_response_sha256=(independent.provider_response_sha256),
            error=independent_error,
        )
    primary_findings = tuple(finding for verdict in batch.verdicts for finding in verdict.findings)
    independent_keys = {
        fact_finding_claim_id(finding)
        for verdict in independent.verdicts
        for finding in verdict.findings
    }
    consensus = tuple(
        finding
        for finding in primary_findings
        if fact_finding_claim_id(finding) in independent_keys
    )
    return FactGateReport(
        blueprint_sha256=request.blueprint_sha256,
        input_sha256=request.fingerprint(),
        state="FAIL" if consensus else "INCONCLUSIVE",
        reviewer_model=batch.reviewer_model,
        policy_sha256=batch.policy_sha256,
        prompt_sha256=batch.prompt_sha256,
        provider_response_sha256=batch.provider_response_sha256,
        verdicts=batch.verdicts,
        independent_verdicts=independent.verdicts,
        consensus_findings=consensus,
        consensus_claim_ids=tuple(fact_finding_claim_id(item) for item in consensus),
        usage=batch.usage,
        independent_usage=independent.usage,
        independent_reviewer_model=independent.reviewer_model,
        independent_policy_sha256=independent.policy_sha256,
        independent_prompt_sha256=independent.prompt_sha256,
        independent_provider_response_sha256=independent.provider_response_sha256,
        error=None if consensus else "factual reviewers did not confirm the same defect",
    )


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
    input_sha256: str = Field(..., pattern=SHA256_PATTERN)
    provider_response_sha256: str = Field(..., pattern=SHA256_PATTERN)
    assessments: tuple[EnjoymentScoredAssessment, ...] = Field(..., min_length=1)
    usage: ReviewUsage


class EnjoymentIndependentReviewPair(BaseModel):
    """Two separately authorized reviews of the same opaque item set."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    first: EnjoymentScoredReviewBatch
    second: EnjoymentScoredReviewBatch

    @model_validator(mode="after")
    def _reviews_are_parallel_and_separately_slotted(
        self,
    ) -> EnjoymentIndependentReviewPair:
        if self.first.review_slot != "first" or self.second.review_slot != "second":
            raise ValueError("enjoyment reviews occupy the wrong independent slots")
        if self.first.input_sha256 != self.second.input_sha256:
            raise ValueError("independent enjoyment reviews saw different inputs")
        first_ids = tuple(item.item_id for item in self.first.assessments)
        second_ids = tuple(item.item_id for item in self.second.assessments)
        if first_ids != second_ids:
            raise ValueError("independent enjoyment reviews assessed different items")
        if self.first.usage.calls != 1 or self.second.usage.calls != 1:
            raise ValueError("each independent enjoyment review must use one call")
        return self


class EnjoymentScoreDecision(BaseModel):
    """Server-owned release result derived from two blind scored reviews."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_item_id: str = Field(..., min_length=1)
    first_total: int = Field(..., ge=0, le=10)
    second_total: int = Field(..., ge=0, le=10)
    aggregate: float = Field(..., ge=0, le=10)
    axis_aggregates: tuple[tuple[str, float], ...]
    consensus_below_gold_axes: tuple[str, ...] = ()
    decision: Literal["PASS", "FAIL"]


def enjoyment_below_gold_axes(
    review: EnjoymentScoredReviewBatch,
    *,
    candidate_item_id: str,
    positive_gold_item_ids: tuple[str, ...],
) -> tuple[str, ...]:
    """Return axes where this reviewer placed the candidate below every positive gold."""
    if not positive_gold_item_ids:
        raise ValueError("enjoyment comparison requires positive gold items")
    by_id = {item.item_id: item for item in review.assessments}
    if len(by_id) != len(review.assessments):
        raise ValueError("scored enjoyment review repeats an item id")
    if candidate_item_id in positive_gold_item_ids:
        raise ValueError("candidate cannot be a positive gold item")
    required = {candidate_item_id, *positive_gold_item_ids}
    if not required <= set(by_id):
        raise ValueError("scored enjoyment review omits candidate or positive gold")
    candidate = by_id[candidate_item_id]
    return tuple(
        axis
        for axis in ENJOY_AXIS_NAMES
        if getattr(candidate.axes, axis).score
        < min(getattr(by_id[item_id].axes, axis).score for item_id in positive_gold_item_ids)
    )


def enjoyment_consensus_below_gold_axes(
    pair: EnjoymentIndependentReviewPair,
    *,
    candidate_item_id: str,
    positive_gold_item_ids: tuple[str, ...],
) -> tuple[str, ...]:
    """Only the same below-gold axis from both reviews is consensus."""
    first = set(
        enjoyment_below_gold_axes(
            pair.first,
            candidate_item_id=candidate_item_id,
            positive_gold_item_ids=positive_gold_item_ids,
        )
    )
    second = set(
        enjoyment_below_gold_axes(
            pair.second,
            candidate_item_id=candidate_item_id,
            positive_gold_item_ids=positive_gold_item_ids,
        )
    )
    return tuple(axis for axis in ENJOY_AXIS_NAMES if axis in first & second)


def decide_enjoyment_score(
    pair: EnjoymentIndependentReviewPair,
    *,
    candidate_item_id: str,
    positive_gold_item_ids: tuple[str, ...],
) -> EnjoymentScoreDecision:
    """Apply total, zero-axis, and same-axis gold-consensus gates in trusted code."""
    first_by_id = {item.item_id: item for item in pair.first.assessments}
    second_by_id = {item.item_id: item for item in pair.second.assessments}
    try:
        first = first_by_id[candidate_item_id]
        second = second_by_id[candidate_item_id]
    except KeyError as exc:
        raise ValueError("independent enjoyment review omits the candidate") from exc
    consensus = enjoyment_consensus_below_gold_axes(
        pair,
        candidate_item_id=candidate_item_id,
        positive_gold_item_ids=positive_gold_item_ids,
    )
    score_gate = enjoyment_pair_meets_score_gate(first, second)
    return EnjoymentScoreDecision(
        candidate_item_id=candidate_item_id,
        first_total=enjoyment_reviewer_total(first),
        second_total=enjoyment_reviewer_total(second),
        aggregate=enjoyment_two_reviewer_aggregate(first, second),
        axis_aggregates=enjoyment_axis_aggregates(first, second),
        consensus_below_gold_axes=consensus,
        decision="PASS" if score_gate and not consensus else "FAIL",
    )


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


def enjoyment_two_reviewer_aggregate(
    first: EnjoymentScoredAssessment,
    second: EnjoymentScoredAssessment,
) -> float:
    """Average two independently authored trusted totals; result increments by 0.5."""
    return (enjoyment_reviewer_total(first) + enjoyment_reviewer_total(second)) / 2


def enjoyment_aggregate_meets_release_threshold(aggregate: float) -> bool:
    """Apply the server-owned 7/10 release threshold."""
    if aggregate < 0 or aggregate > 10 or aggregate * 2 != int(aggregate * 2):
        raise ValueError("enjoyment aggregate must be a 0-10 half-point value")
    return aggregate >= ENJOY_RELEASE_THRESHOLD


def enjoyment_axis_aggregates(
    first: EnjoymentScoredAssessment,
    second: EnjoymentScoredAssessment,
) -> tuple[tuple[str, float], ...]:
    """Compute the two-reviewer average for each frozen rubric axis."""
    return tuple(
        (
            axis,
            (
                getattr(first.axes, axis).score
                + getattr(second.axes, axis).score
            )
            / 2,
        )
        for axis in ENJOY_AXIS_NAMES
    )


def enjoyment_pair_meets_score_gate(
    first: EnjoymentScoredAssessment,
    second: EnjoymentScoredAssessment,
) -> bool:
    """Require both the 7/10 total and no unanimously zero-scored axis."""
    return enjoyment_aggregate_meets_release_threshold(
        enjoyment_two_reviewer_aggregate(first, second)
    ) and all(score > 0 for _, score in enjoyment_axis_aggregates(first, second))


class ScoredEnjoymentReviewer(Protocol):
    def review(
        self, items: tuple[EnjoymentItem, ...]
    ) -> EnjoymentScoredReviewBatch: ...


class EnjoymentCalibrationReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    calibration_manifest_sha256: str = Field(..., pattern=SHA256_PATTERN)
    anchor_suite_sha256: str = Field(..., pattern=SHA256_PATTERN)
    state: GateState
    reviewer_model: str | None = None
    policy_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    prompt_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    decisions: tuple[tuple[str, Literal["PASS", "FAIL"]], ...] = ()
    usage: ReviewUsage = ReviewUsage(calls=0)
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.state == "PASS"


class EnjoymentGateReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    blueprint_sha256: str = Field(..., pattern=SHA256_PATTERN)
    coverage: Literal["text_only"] = "text_only"
    state: GateState
    first_scored_review: EnjoymentScoredReviewBatch | None = None
    second_scored_review: EnjoymentScoredReviewBatch | None = None
    score_decision: EnjoymentScoreDecision | None = None
    consensus_below_gold_axes: tuple[str, ...] = ()
    dissent_is_advisory: bool = False
    reviewer_model: str | None = None
    policy_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    prompt_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    provider_response_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    independent_reviewer_model: str | None = None
    independent_policy_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    independent_prompt_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    independent_provider_response_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    usage: tuple[ReviewUsage, ...] = ()
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.state == "PASS"


class QualityCalibrationBatch(BaseModel):
    """One physical calibration call covering FACT policy and ENJOY anchors."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reviewer_model: str = Field(..., min_length=1)
    policy_sha256: str = Field(..., pattern=SHA256_PATTERN)
    approved_fact_prompt_sha256: str = Field(..., pattern=SHA256_PATTERN)
    approved_enjoy_prompt_sha256: str = Field(..., pattern=SHA256_PATTERN)
    approved_adjudication_prompt_sha256: str = Field(..., pattern=SHA256_PATTERN)
    input_sha256: str = Field(..., pattern=SHA256_PATTERN)
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
    """Exact manifest-derived suite; arbitrary subsets cannot masquerade as frozen."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    calibration_manifest_text: str = Field(..., min_length=2)
    reference_manifest_text: str = Field(..., min_length=2)
    calibration_manifest_sha256: str = Field(..., pattern=SHA256_PATTERN)
    reference_manifest_sha256: str = Field(..., pattern=SHA256_PATTERN)
    fact_cases: tuple[FactPolicyCalibrationCase, ...]
    enjoyment_anchors: tuple[EnjoymentCalibrationAnchor, ...]
    bundle_sha256: str = Field(..., pattern=SHA256_PATTERN)


def quality_calibration_inputs_fingerprint(inputs: QualityCalibrationInputs) -> str:
    return bytes_sha256(
        json.dumps(
            inputs.model_dump(mode="json", exclude={"bundle_sha256"}),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )


def validate_quality_calibration_inputs(
    inputs: QualityCalibrationInputs,
) -> str | None:
    if inputs.bundle_sha256 != quality_calibration_inputs_fingerprint(inputs):
        return "quality calibration input bundle fingerprint mismatch"
    if text_sha256(inputs.calibration_manifest_text) != inputs.calibration_manifest_sha256:
        return "quality calibration manifest bytes mismatch"
    if text_sha256(inputs.reference_manifest_text) != inputs.reference_manifest_sha256:
        return "quality reference manifest bytes mismatch"
    try:
        manifest = json.loads(inputs.calibration_manifest_text)
        json.loads(inputs.reference_manifest_text)
    except json.JSONDecodeError:
        return "quality calibration bundle contains invalid manifest JSON"
    expected_cases = tuple(
        FactPolicyCalibrationCase(
            case_id=f"fact-policy-{index + 1}",
            source_text=case["source_text"],
            candidate_text=case["candidate_text"],
            expected=case["expected_fact"],
        )
        for index, case in enumerate(manifest["fact_policy_calibration"]["cases"])
    )
    if inputs.fact_cases != expected_cases or not expected_cases:
        return "quality calibration FACT suite differs from the exact manifest"
    expected_anchor_specs = [
        item for item in manifest["anchors"] if item["expected"]["ENJOY"] in {"PASS", "FAIL"}
    ]
    if len(inputs.enjoyment_anchors) != len(expected_anchor_specs):
        return "quality calibration ENJOY suite omits a manifest anchor"
    for anchor, spec in zip(inputs.enjoyment_anchors, expected_anchor_specs, strict=True):
        if (
            anchor.item.item_id != _opaque_enjoyment_id("item", spec["id"])
            or anchor.expected != spec["expected"]["ENJOY"]
            or anchor.item.narration_sha256 != spec["slice"]["sha256"]
            or len(anchor.item.narration.encode("utf-8")) != spec["slice"]["size_bytes"]
        ):
            return "quality calibration ENJOY suite differs from the exact manifest"
    return None


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
    approved_adjudication_prompt_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    fact_calibration_verdicts: tuple[FactPolicyCalibrationVerdict, ...] = ()
    fact_decisions: tuple[tuple[str, Literal["PASS", "FAIL"]], ...] = ()
    enjoyment_decisions: tuple[tuple[str, Literal["PASS", "FAIL"]], ...] = ()
    enjoyment_assessments: tuple[EnjoymentScoredAssessment, ...] = ()
    usage: ReviewUsage = ReviewUsage(calls=0)
    error: str | None = None
    report_sha256: str = Field(..., pattern=SHA256_PATTERN)

    @property
    def passed(self) -> bool:
        return self.state == "PASS"


def quality_calibration_report_fingerprint(report: QualityCalibrationReport) -> str:
    return bytes_sha256(
        json.dumps(
            report.model_dump(mode="json", exclude={"report_sha256"}),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )


def _seal_quality_calibration_report(
    **values: object,
) -> QualityCalibrationReport:
    report = QualityCalibrationReport(report_sha256="0" * 64, **values)
    return report.model_copy(
        update={"report_sha256": quality_calibration_report_fingerprint(report)}
    )


def validate_quality_calibration_report(
    report: QualityCalibrationReport,
) -> str | None:
    if report.report_sha256 != quality_calibration_report_fingerprint(report):
        return "quality calibration report fingerprint mismatch"
    if report.passed and (
        report.usage.calls != 1
        or not report.reviewer_model
        or not report.policy_sha256
        or not report.approved_fact_prompt_sha256
        or not report.approved_enjoy_prompt_sha256
        or not report.approved_adjudication_prompt_sha256
        or not report.provider_response_sha256
        or report.provider_response_sha256 == "0" * 64
        or not report.fact_calibration_verdicts
        or not report.fact_decisions
        or not report.enjoyment_decisions
        or not report.enjoyment_assessments
        or not report.input_bundle_sha256
    ):
        return "passing quality calibration report is incomplete"
    if report.inputs is not None:
        inputs_error = validate_quality_calibration_inputs(report.inputs)
        if inputs_error:
            return f"embedded calibration inputs are invalid: {inputs_error}"
        if (
            report.inputs.bundle_sha256 != report.input_bundle_sha256
            or report.inputs.calibration_manifest_sha256 != report.calibration_manifest_sha256
            or report.inputs.reference_manifest_sha256 != report.reference_manifest_sha256
            or _fact_calibration_suite_sha256(report.inputs.fact_cases) != report.fact_suite_sha256
            or _anchors_sha256(report.inputs.enjoyment_anchors) != report.anchor_suite_sha256
            or _quality_calibration_input_sha256(
                report.inputs.fact_cases, report.inputs.enjoyment_anchors
            )
            != report.input_sha256
        ):
            return "embedded calibration inputs differ from the sealed report"
        if report.passed:
            recomputed_fact = tuple(
                (verdict.case_id, verdict.decision)
                for verdict in report.fact_calibration_verdicts
            )
            if recomputed_fact != report.fact_decisions:
                return "calibration FACT decisions differ from provider verdict replay"
            scored_batch = EnjoymentScoredReviewBatch(
                review_slot="first",
                reviewer_model=report.reviewer_model or "missing",
                policy_sha256=report.policy_sha256 or "0" * 64,
                prompt_sha256=report.approved_enjoy_prompt_sha256 or "0" * 64,
                input_sha256=_enjoyment_review_input_sha256(
                    (), report.inputs.enjoyment_anchors
                ),
                provider_response_sha256=(
                    report.provider_response_sha256 or "0" * 64
                ),
                assessments=report.enjoyment_assessments,
                usage=report.usage,
            )
            scored_error = _validate_scored_enjoyment_batch(
                (),
                scored_batch,
                anchors=report.inputs.enjoyment_anchors,
                expected_slot="first",
            )
            if scored_error:
                return f"calibration ENJOY evidence is invalid: {scored_error}"
            recomputed = tuple(
                (assessment.item_id, _single_review_enjoyment_decision(assessment))
                for assessment in report.enjoyment_assessments
            )
            if recomputed != report.enjoyment_decisions:
                return "calibration ENJOY decisions differ from trusted score replay"
    return None


class QualityAdjudicationInput(BaseModel):
    """Blind, exact input for the run's sole independent quality call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    blueprint_sha256: str = Field(..., pattern=SHA256_PATTERN)
    fact_request: FactReviewInput | None = None
    enjoyment_items: tuple[EnjoymentItem, ...] = Field(default=(), max_length=1)
    enjoyment_anchors: tuple[EnjoymentCalibrationAnchor, ...] = ()

    @model_validator(mode="after")
    def _one_candidate_without_primary_verdicts(self) -> QualityAdjudicationInput:
        if self.fact_request is None and not self.enjoyment_items:
            raise ValueError("adjudication requires a FACT or ENJOY candidate")
        if (
            self.fact_request is not None
            and self.fact_request.blueprint_sha256 != self.blueprint_sha256
        ):
            raise ValueError("adjudication FACT input belongs to a different blueprint")
        if self.enjoyment_items and not self.enjoyment_anchors:
            raise ValueError("ENJOY adjudication requires exact frozen anchors")
        expected_item_id = _opaque_enjoyment_id("item", self.blueprint_sha256)
        if self.enjoyment_items and self.enjoyment_items[0].item_id != expected_item_id:
            raise ValueError("adjudication ENJOY input belongs to a different blueprint")
        anchor_ids = [anchor.item.item_id for anchor in self.enjoyment_anchors]
        if len(anchor_ids) != len(set(anchor_ids)):
            raise ValueError("adjudication enjoyment anchors contain duplicate ids")
        if expected_item_id in anchor_ids:
            raise ValueError("candidate cannot masquerade as a frozen anchor")
        return self

    def fingerprint(self) -> str:
        return _canonical_sha256(self)


class QualityAdjudicationBatch(BaseModel):
    """One provider response, optionally covering both disputed quality gates."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reviewer_model: str = Field(..., min_length=1)
    policy_sha256: str = Field(..., pattern=SHA256_PATTERN)
    prompt_sha256: str = Field(..., pattern=SHA256_PATTERN)
    input_sha256: str = Field(..., pattern=SHA256_PATTERN)
    provider_response_sha256: str = Field(default="0" * 64, pattern=SHA256_PATTERN)
    fact_verdicts: tuple[FactSentenceVerdict, ...] = ()
    enjoyment_assessments: tuple[EnjoymentScoredAssessment, ...] = ()
    usage: ReviewUsage


def _provider_payload_sha256(payload: object) -> str:
    return bytes_sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )


def fact_provider_payload_sha256(
    verdicts: tuple[FactSentenceVerdict, ...],
) -> str:
    return _provider_payload_sha256(
        {"verdicts": [item.model_dump(mode="json") for item in verdicts]}
    )


def enjoyment_provider_payload_sha256(
    assessments: tuple[EnjoymentScoredAssessment, ...],
) -> str:
    return _provider_payload_sha256(
        {"assessments": [item.model_dump(mode="json") for item in assessments]}
    )


def calibration_provider_payload_sha256(
    fact_verdicts: tuple[FactPolicyCalibrationVerdict, ...],
    enjoyment_assessments: tuple[EnjoymentScoredAssessment, ...],
) -> str:
    return _provider_payload_sha256(
        {
            "fact_verdicts": [item.model_dump(mode="json") for item in fact_verdicts],
            "enjoyment_assessments": [
                item.model_dump(mode="json") for item in enjoyment_assessments
            ],
        }
    )


def adjudication_provider_payload_sha256(
    fact_verdicts: tuple[FactSentenceVerdict, ...],
    enjoyment_assessments: tuple[EnjoymentScoredAssessment, ...],
) -> str:
    return _provider_payload_sha256(
        {
            "fact_verdicts": [item.model_dump(mode="json") for item in fact_verdicts],
            "enjoyment_assessments": [
                item.model_dump(mode="json") for item in enjoyment_assessments
            ],
        }
    )


class QualityAdjudicator(Protocol):
    def review(self, request: QualityAdjudicationInput) -> QualityAdjudicationBatch: ...


class QualityGateReport(BaseModel):
    """Final FACT+ENJOY result for one immutable blueprint."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    blueprint_sha256: str = Field(..., pattern=SHA256_PATTERN)
    calibration_report_sha256: str = Field(..., pattern=SHA256_PATTERN)
    calibration_manifest_sha256: str = Field(..., pattern=SHA256_PATTERN)
    reference_manifest_sha256: str = Field(..., pattern=SHA256_PATTERN)
    anchor_suite_sha256: str = Field(..., pattern=SHA256_PATTERN)
    calibration: QualityCalibrationReport
    state: GateState
    fact: FactGateReport
    enjoyment: EnjoymentGateReport | None = None
    fact_input: FactReviewInput | None = None
    enjoyment_items: tuple[EnjoymentItem, ...] = Field(default=(), max_length=1)
    enjoyment_anchors: tuple[EnjoymentCalibrationAnchor, ...] = ()
    adjudication_usage: ReviewUsage = ReviewUsage(calls=0)
    adjudication_provider_response_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.state == "PASS"


def validate_quality_gate_report(
    report: QualityGateReport, blueprint: FinalTourBlueprint
) -> str | None:
    """Reject incomplete or internally contradictory terminal quality reports."""
    blueprint_sha256 = blueprint.fingerprint()
    eligibility_error = validate_llm_composed_blueprint(blueprint)
    if eligibility_error:
        return f"quality report evaluates an ineligible blueprint: {eligibility_error}"
    if report.blueprint_sha256 != blueprint_sha256:
        return "quality report uses a different blueprint"
    calibration_error = validate_quality_calibration_report(report.calibration)
    if calibration_error:
        return f"quality report has invalid calibration: {calibration_error}"
    if report.calibration.inputs is None:
        return "terminal quality report omits the exact frozen calibration inputs"
    expected_fact_decisions = tuple(
        (case.case_id, case.expected) for case in report.calibration.inputs.fact_cases
    )
    expected_enjoyment_decisions = tuple(
        (anchor.item.item_id, anchor.expected)
        for anchor in sorted(
            report.calibration.inputs.enjoyment_anchors,
            key=lambda anchor: anchor.item.item_id,
        )
    )
    if (
        report.calibration.fact_decisions != expected_fact_decisions
        or report.calibration.enjoyment_decisions != expected_enjoyment_decisions
    ):
        return "terminal calibration decisions differ from the frozen expected labels"
    if (
        report.calibration_report_sha256 != report.calibration.report_sha256
        or report.calibration_manifest_sha256 != report.calibration.calibration_manifest_sha256
        or report.reference_manifest_sha256 != report.calibration.reference_manifest_sha256
        or report.anchor_suite_sha256 != report.calibration.anchor_suite_sha256
    ):
        return "quality report calibration binding is internally inconsistent"
    if report.fact.blueprint_sha256 != blueprint_sha256:
        return "FACT report uses a different blueprint"
    if (
        report.calibration_manifest_sha256 != blueprint.calibration_manifest_sha256
        or report.reference_manifest_sha256 != blueprint.reference_manifest_sha256
    ):
        return "quality report calibration manifests differ from the blueprint"
    if report.enjoyment is not None and report.enjoyment.blueprint_sha256 != blueprint_sha256:
        return "ENJOY report uses a different blueprint"
    if report.state == "ERROR":
        return report.error or "quality report is ERROR without an explanation"
    if report.error and report.state != "INCONCLUSIVE":
        return "non-inconclusive quality report carries an error"
    if report.state == "PASS" and (
        report.fact.state != "PASS" or report.enjoyment is None or report.enjoyment.state != "PASS"
    ):
        return "passing quality report contradicts its FACT/ENJOY results"
    if report.state == "FAIL" and not (
        report.fact.state == "FAIL"
        or (report.enjoyment is not None and report.enjoyment.state == "FAIL")
    ):
        return "failed quality report has no failed FACT/ENJOY result"
    if report.state == "INCONCLUSIVE" and not (
        report.fact.state == "INCONCLUSIVE"
        or (report.enjoyment is not None and report.enjoyment.state == "INCONCLUSIVE")
    ):
        return "inconclusive quality report has no inconclusive FACT/ENJOY result"

    fact = report.fact
    if (
        not fact.reviewer_model
        or not fact.policy_sha256
        or not fact.prompt_sha256
        or not fact.independent_reviewer_model
        or not fact.independent_policy_sha256
        or not fact.independent_prompt_sha256
        or not fact.provider_response_sha256
        or fact.provider_response_sha256 == "0" * 64
        or not fact.independent_provider_response_sha256
        or fact.independent_provider_response_sha256 == "0" * 64
        or fact.usage.calls != 1
        or fact.independent_usage.calls != 1
        or len(fact.verdicts) != len(blueprint.script.script)
        or len(fact.independent_verdicts) != len(blueprint.script.script)
        or fact.input_sha256 == "0" * 64
    ):
        return "terminal FACT report lacks complete primary and adjudication evidence"
    if (
        fact.reviewer_model != report.calibration.reviewer_model
        or fact.policy_sha256 != report.calibration.policy_sha256
        or fact.prompt_sha256 != report.calibration.approved_fact_prompt_sha256
        or fact.independent_reviewer_model != report.calibration.reviewer_model
        or fact.independent_policy_sha256 != report.calibration.policy_sha256
        or fact.independent_prompt_sha256 != report.calibration.approved_adjudication_prompt_sha256
    ):
        return "terminal FACT reviewers differ from the frozen calibration"
    if (
        report.fact_input is None
        or report.fact_input.blueprint_sha256 != blueprint_sha256
        or report.fact_input.provider_narration_sha256
        != sentences_payload_sha256(blueprint.script.script)
        or report.fact_input.fingerprint() != fact.input_sha256
    ):
        return "terminal FACT report lacks its exact review input"
    primary_fact_batch = FactReviewBatch(
        reviewer_model=fact.reviewer_model,
        policy_sha256=fact.policy_sha256,
        prompt_sha256=fact.prompt_sha256,
        input_sha256=fact.input_sha256,
        provider_response_sha256=fact.provider_response_sha256,
        verdicts=fact.verdicts,
        usage=fact.usage,
    )
    primary_fact_error = _fact_batch_error(report.fact_input, primary_fact_batch)
    if primary_fact_error:
        return f"terminal primary FACT evidence is invalid: {primary_fact_error}"
    independent_fact_batch = FactReviewBatch(
        reviewer_model=fact.independent_reviewer_model,
        policy_sha256=fact.independent_policy_sha256,
        prompt_sha256=fact.independent_prompt_sha256,
        input_sha256=fact.input_sha256,
        provider_response_sha256=fact.independent_provider_response_sha256,
        verdicts=fact.independent_verdicts,
        usage=fact.independent_usage,
    )
    independent_fact_error = _fact_batch_error(report.fact_input, independent_fact_batch)
    if independent_fact_error:
        return f"terminal adjudicated FACT evidence is invalid: {independent_fact_error}"
    enjoyment = report.enjoyment
    if enjoyment is None:
        return "terminal quality report lacks ENJOY evidence"
    if (
        enjoyment.first_scored_review is None
        or enjoyment.second_scored_review is None
        or enjoyment.score_decision is None
        or not enjoyment.reviewer_model
        or not enjoyment.policy_sha256
        or not enjoyment.prompt_sha256
        or not enjoyment.independent_reviewer_model
        or not enjoyment.independent_policy_sha256
        or not enjoyment.independent_prompt_sha256
        or not enjoyment.provider_response_sha256
        or enjoyment.provider_response_sha256 == "0" * 64
        or not enjoyment.independent_provider_response_sha256
        or enjoyment.independent_provider_response_sha256 == "0" * 64
        or not report.adjudication_provider_response_sha256
        or report.adjudication_provider_response_sha256 == "0" * 64
        or len(enjoyment.usage) != 2
        or any(item.calls != 1 for item in enjoyment.usage)
        or report.adjudication_usage.calls != 1
    ):
        return "terminal ENJOY report lacks complete primary and adjudication evidence"
    if (
        enjoyment.reviewer_model != report.calibration.reviewer_model
        or enjoyment.policy_sha256 != report.calibration.policy_sha256
        or enjoyment.prompt_sha256 != report.calibration.approved_enjoy_prompt_sha256
        or enjoyment.independent_reviewer_model != report.calibration.reviewer_model
        or enjoyment.independent_policy_sha256 != report.calibration.policy_sha256
        or enjoyment.independent_prompt_sha256
        != report.calibration.approved_adjudication_prompt_sha256
        or fact.independent_usage != report.adjudication_usage
        or enjoyment.usage[1] != report.adjudication_usage
        or fact.independent_provider_response_sha256 != report.adjudication_provider_response_sha256
        or enjoyment.independent_provider_response_sha256
        != report.adjudication_provider_response_sha256
    ):
        return "terminal adjudication differs from the frozen calibration or usage"
    expected_item = _blueprint_enjoyment_item(blueprint)
    if report.enjoyment_items != (expected_item,) or not report.enjoyment_anchors:
        return "terminal ENJOY report lacks its exact candidate and frozen anchors"
    if (
        report.enjoyment_anchors != report.calibration.inputs.enjoyment_anchors
        or _anchors_sha256(report.enjoyment_anchors) != report.anchor_suite_sha256
    ):
        return "terminal ENJOY anchors differ from the calibrated anchor suite"
    first_scored = enjoyment.first_scored_review
    second_scored = enjoyment.second_scored_review
    for label, batch, slot in (
        ("primary", first_scored, "first"),
        ("adjudicated", second_scored, "second"),
    ):
        batch_error = _validate_scored_enjoyment_batch(
            report.enjoyment_items,
            batch,
            anchors=report.enjoyment_anchors,
            expected_slot=slot,
        )
        if batch_error:
            return f"terminal {label} ENJOY evidence is invalid: {batch_error}"
    if (
        first_scored.reviewer_model != enjoyment.reviewer_model
        or first_scored.policy_sha256 != enjoyment.policy_sha256
        or first_scored.prompt_sha256 != enjoyment.prompt_sha256
        or first_scored.provider_response_sha256 != enjoyment.provider_response_sha256
        or second_scored.reviewer_model != enjoyment.independent_reviewer_model
        or second_scored.policy_sha256 != enjoyment.independent_policy_sha256
        or second_scored.prompt_sha256 != enjoyment.independent_prompt_sha256
        or second_scored.provider_response_sha256
        != enjoyment.independent_provider_response_sha256
        or enjoyment.usage != (first_scored.usage, second_scored.usage)
    ):
        return "terminal ENJOY summary differs from its scored review evidence"
    scored_pair = EnjoymentIndependentReviewPair(
        first=first_scored,
        second=second_scored,
    )
    replayed_score = decide_enjoyment_score(
        scored_pair,
        candidate_item_id=expected_item.item_id,
        positive_gold_item_ids=tuple(
            anchor.item.item_id
            for anchor in report.enjoyment_anchors
            if anchor.expected == "PASS"
        ),
    )
    if (
        enjoyment.score_decision != replayed_score
        or enjoyment.consensus_below_gold_axes
        != replayed_score.consensus_below_gold_axes
        or enjoyment.state != replayed_score.decision
    ):
        return "terminal ENJOY result differs from trusted score recomputation"
    primary_fact = fact.model_copy(
        update={
            "state": "INCONCLUSIVE",
            "independent_reviewer_model": None,
            "independent_policy_sha256": None,
            "independent_prompt_sha256": None,
            "independent_provider_response_sha256": None,
            "independent_verdicts": (),
            "consensus_findings": (),
            "consensus_claim_ids": (),
            "independent_usage": ReviewUsage(calls=0),
            "error": "one factual reviewer cannot issue a final result",
        }
    )
    primary_enjoyment = enjoyment.model_copy(
        update={
            "state": "INCONCLUSIVE",
            "second_scored_review": None,
            "score_decision": None,
            "consensus_below_gold_axes": (),
            "dissent_is_advisory": False,
            "independent_reviewer_model": None,
            "independent_policy_sha256": None,
            "independent_prompt_sha256": None,
            "independent_provider_response_sha256": None,
            "usage": (enjoyment.usage[0],),
            "error": "one enjoyment reviewer cannot issue a final result",
        }
    )
    adjudication = QualityAdjudicationBatch(
        reviewer_model=fact.independent_reviewer_model,
        policy_sha256=fact.independent_policy_sha256,
        prompt_sha256=fact.independent_prompt_sha256,
        input_sha256=QualityAdjudicationInput(
            blueprint_sha256=blueprint_sha256,
            fact_request=report.fact_input,
            enjoyment_items=report.enjoyment_items,
            enjoyment_anchors=report.enjoyment_anchors,
        ).fingerprint(),
        provider_response_sha256=report.adjudication_provider_response_sha256,
        fact_verdicts=fact.independent_verdicts,
        enjoyment_assessments=second_scored.assessments,
        usage=report.adjudication_usage,
    )
    replayed_fact = _reconcile_fact_adjudication(primary_fact, adjudication)
    replayed_enjoyment = _reconcile_enjoyment_adjudication(
        primary_enjoyment,
        adjudication,
        candidate_item_id=expected_item.item_id,
        positive_gold_item_ids=tuple(
            anchor.item.item_id
            for anchor in report.enjoyment_anchors
            if anchor.expected == "PASS"
        ),
    )
    if fact != replayed_fact or enjoyment != replayed_enjoyment:
        return "terminal quality result differs from deterministic adjudication replay"
    expected_state: GateState
    if replayed_fact.state == "FAIL" or replayed_enjoyment.state == "FAIL":
        expected_state = "FAIL"
    elif replayed_fact.state == "INCONCLUSIVE":
        expected_state = "INCONCLUSIVE"
    else:
        expected_state = "PASS"
    if report.state != expected_state:
        return "overall quality state differs from deterministic gate aggregation"
    return None


def _items_sha256(items: tuple[EnjoymentItem, ...]) -> str:
    payload = json.dumps(
        [item.model_dump(mode="json") for item in items],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return bytes_sha256(payload)


def _anchors_sha256(anchors: tuple[EnjoymentCalibrationAnchor, ...]) -> str:
    return bytes_sha256(
        json.dumps(
            [anchor.model_dump(mode="json") for anchor in anchors],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )


def _enjoyment_review_input_sha256(
    items: tuple[EnjoymentItem, ...],
    anchors: tuple[EnjoymentCalibrationAnchor, ...],
) -> str:
    neutral_items = _neutral_enjoyment_items(items, anchors)
    return bytes_sha256(
        json.dumps(
            {"items": [item.model_dump(mode="json") for item in neutral_items]},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )


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
    """Validate exact roster, call binding, and same-item axis evidence."""
    if batch.review_slot != expected_slot:
        return f"enjoyment review must occupy the {expected_slot} independent slot"
    if batch.usage.calls != 1:
        return "enjoyment reviewer must consume exactly one bounded batch call"
    if batch.input_sha256 != _enjoyment_review_input_sha256(items, anchors):
        return "enjoyment reviewer output is bound to a different neutral input"
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
    return bytes_sha256(
        json.dumps(
            {
                "fact_cases": [case.model_dump(mode="json") for case in fact_cases],
                "enjoyment_anchors": [
                    anchor.model_dump(mode="json") for anchor in enjoyment_anchors
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )


def _fact_calibration_suite_sha256(
    fact_cases: tuple[FactPolicyCalibrationCase, ...],
) -> str:
    return bytes_sha256(
        json.dumps(
            [case.model_dump(mode="json") for case in fact_cases],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )


def calibrate_quality(
    *,
    inputs: QualityCalibrationInputs,
    reviewer: QualityCalibrationReviewer,
) -> QualityCalibrationReport:
    """Use exactly one provider call to calibrate both frozen quality policies."""
    inputs_error = validate_quality_calibration_inputs(inputs)
    if inputs_error:
        return _seal_quality_calibration_report(
            calibration_manifest_sha256=inputs.calibration_manifest_sha256,
            reference_manifest_sha256=inputs.reference_manifest_sha256,
            fact_suite_sha256=_fact_calibration_suite_sha256(inputs.fact_cases),
            anchor_suite_sha256=_anchors_sha256(inputs.enjoyment_anchors),
            input_bundle_sha256=inputs.bundle_sha256,
            input_sha256="0" * 64,
            inputs=inputs,
            state="ERROR",
            error=inputs_error,
        )
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
        return _seal_quality_calibration_report(
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
    if batch.usage.calls != 1:
        error = "quality calibration must consume exactly one bounded call"
    elif batch.input_sha256 != input_hash:
        error = "quality calibration output is bound to a different input"
    elif [verdict.case_id for verdict in batch.fact_verdicts] != [
        case.case_id for case in fact_cases
    ]:
        error = "quality calibration must cover every FACT policy case in order"
    else:
        enjoy_batch = EnjoymentScoredReviewBatch(
            review_slot="first",
            reviewer_model=batch.reviewer_model,
            policy_sha256=batch.policy_sha256,
            prompt_sha256=batch.approved_enjoy_prompt_sha256,
            input_sha256=_enjoyment_review_input_sha256((), enjoyment_anchors),
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
        return _seal_quality_calibration_report(
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
            approved_adjudication_prompt_sha256=(batch.approved_adjudication_prompt_sha256),
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
    return _seal_quality_calibration_report(
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
        approved_adjudication_prompt_sha256=batch.approved_adjudication_prompt_sha256,
        fact_calibration_verdicts=batch.fact_verdicts,
        fact_decisions=fact_decisions,
        enjoyment_decisions=enjoyment_decisions,
        enjoyment_assessments=batch.enjoyment_assessments,
        usage=batch.usage,
    )


def enjoyment_calibration_from_quality(
    report: QualityCalibrationReport,
    *,
    anchors: tuple[EnjoymentCalibrationAnchor, ...],
) -> EnjoymentCalibrationReport:
    """Project the one combined calibration call into the ENJOY gate contract."""
    return EnjoymentCalibrationReport(
        calibration_manifest_sha256=report.calibration_manifest_sha256,
        anchor_suite_sha256=_anchors_sha256(anchors),
        state=report.state,
        reviewer_model=report.reviewer_model,
        policy_sha256=report.policy_sha256,
        prompt_sha256=report.approved_enjoy_prompt_sha256,
        decisions=report.enjoyment_decisions,
        usage=report.usage,
        error=report.error,
    )


def _blueprint_enjoyment_item(blueprint: FinalTourBlueprint) -> EnjoymentItem:
    eligibility_error = validate_llm_composed_blueprint(blueprint)
    if eligibility_error:
        raise ValueError(f"blueprint is ineligible for ENJOY review: {eligibility_error}")
    segments = tuple(
        EnjoymentSegment(
            segment_id=chunk.cue_id,
            stop_index=chunk.index,
            placement=chunk.placement,
            narration=chunk.text,
            narration_sha256=chunk.narration_sha256,
        )
        for chunk in blueprint.narration_chunks
    )
    narration = "\n".join(segment.narration for segment in segments).strip()
    if not narration:
        raise ValueError("an empty final script cannot be enjoyable")
    return EnjoymentItem(
        item_id=_opaque_enjoyment_id("item", blueprint.fingerprint()),
        source_payload_sha256=sentences_payload_sha256(blueprint.script.script),
        narration=narration,
        narration_sha256=text_sha256(narration),
        segments=segments,
    )


def certify_enjoyment(
    blueprint: FinalTourBlueprint,
    *,
    anchors: tuple[EnjoymentCalibrationAnchor, ...],
    quality_calibration: QualityCalibrationReport,
    primary_calibration: EnjoymentCalibrationReport,
    primary_reviewer: ScoredEnjoymentReviewer,
    independent_calibration: EnjoymentCalibrationReport | None,
    independent_reviewer: ScoredEnjoymentReviewer | None,
) -> EnjoymentGateReport:
    """Score the neutral candidate-and-gold set; one review is never final."""
    blueprint_hash = blueprint.fingerprint()
    integrity_error = validate_blueprint_integrity(blueprint)
    if integrity_error:
        return EnjoymentGateReport(
            blueprint_sha256=blueprint_hash,
            state="ERROR",
            error=f"invalid final blueprint: {integrity_error}",
        )
    eligibility_error = validate_llm_composed_blueprint(blueprint)
    if eligibility_error:
        return EnjoymentGateReport(
            blueprint_sha256=blueprint_hash,
            state="ERROR",
            error=f"blueprint is ineligible for ENJOY review: {eligibility_error}",
        )
    calibration_error = validate_quality_calibration_report(quality_calibration)
    if calibration_error:
        return EnjoymentGateReport(
            blueprint_sha256=blueprint_hash,
            state="ERROR",
            error=calibration_error,
        )
    expected_primary = enjoyment_calibration_from_quality(quality_calibration, anchors=anchors)
    if primary_calibration != expected_primary:
        return EnjoymentGateReport(
            blueprint_sha256=blueprint_hash,
            state="ERROR",
            error="enjoyment calibration is not the exact quality-report projection",
        )
    if quality_calibration.reference_manifest_sha256 != blueprint.reference_manifest_sha256:
        return EnjoymentGateReport(
            blueprint_sha256=blueprint_hash,
            state="ERROR",
            error="enjoyment calibration reference manifest does not match blueprint",
        )
    if not primary_calibration.passed:
        return EnjoymentGateReport(
            blueprint_sha256=blueprint_hash,
            state="ERROR",
            error="the enjoyment reviewer has not passed the frozen anchor calibration",
        )
    if primary_calibration.calibration_manifest_sha256 != blueprint.calibration_manifest_sha256:
        return EnjoymentGateReport(
            blueprint_sha256=blueprint_hash,
            state="ERROR",
            error="enjoyment calibration manifest does not match the blueprint",
        )
    item = _blueprint_enjoyment_item(blueprint)
    if primary_calibration.anchor_suite_sha256 != _anchors_sha256(anchors):
        return EnjoymentGateReport(
            blueprint_sha256=blueprint_hash,
            state="ERROR",
            error="enjoyment anchors differ from the calibrated exact suite",
        )
    try:
        batch = primary_reviewer.review((item,))
    except Exception as exc:
        return EnjoymentGateReport(
            blueprint_sha256=blueprint_hash,
            state="ERROR",
            error=f"enjoyment review error: {type(exc).__name__}: {exc}",
        )
    error = _validate_scored_enjoyment_batch(
        (item,), batch, anchors=anchors, expected_slot="first"
    )
    if error:
        return EnjoymentGateReport(
            blueprint_sha256=blueprint_hash,
            state="ERROR",
            reviewer_model=batch.reviewer_model,
            policy_sha256=batch.policy_sha256,
            prompt_sha256=batch.prompt_sha256,
            provider_response_sha256=batch.provider_response_sha256,
            usage=(batch.usage,),
            error=error,
        )
    if (
        batch.reviewer_model != primary_calibration.reviewer_model
        or batch.policy_sha256 != primary_calibration.policy_sha256
        or batch.prompt_sha256 != primary_calibration.prompt_sha256
    ):
        return EnjoymentGateReport(
            blueprint_sha256=blueprint_hash,
            state="ERROR",
            reviewer_model=batch.reviewer_model,
            policy_sha256=batch.policy_sha256,
            prompt_sha256=batch.prompt_sha256,
            provider_response_sha256=batch.provider_response_sha256,
            usage=(batch.usage,),
            error="candidate reviewer model/prompt differs from calibrated reviewer",
        )
    if (
        independent_calibration is None
        or independent_reviewer is None
        or not independent_calibration.passed
    ):
        return EnjoymentGateReport(
            blueprint_sha256=blueprint_hash,
            state="INCONCLUSIVE",
            first_scored_review=batch,
            reviewer_model=batch.reviewer_model,
            policy_sha256=batch.policy_sha256,
            prompt_sha256=batch.prompt_sha256,
            provider_response_sha256=batch.provider_response_sha256,
            usage=(batch.usage,),
            error="one enjoyment reviewer cannot issue a final result",
        )
    if independent_calibration != expected_primary:
        return EnjoymentGateReport(
            blueprint_sha256=blueprint_hash,
            state="ERROR",
            first_scored_review=batch,
            reviewer_model=batch.reviewer_model,
            policy_sha256=batch.policy_sha256,
            prompt_sha256=batch.prompt_sha256,
            provider_response_sha256=batch.provider_response_sha256,
            usage=(batch.usage,),
            error="independent enjoyment calibration is not the exact projection",
        )
    if independent_calibration.calibration_manifest_sha256 != blueprint.calibration_manifest_sha256:
        return EnjoymentGateReport(
            blueprint_sha256=blueprint_hash,
            state="ERROR",
            first_scored_review=batch,
            reviewer_model=batch.reviewer_model,
            policy_sha256=batch.policy_sha256,
            prompt_sha256=batch.prompt_sha256,
            provider_response_sha256=batch.provider_response_sha256,
            usage=(batch.usage,),
            error="independent enjoyment calibration manifest does not match blueprint",
        )
    try:
        independent_batch = independent_reviewer.review((item,))
    except Exception as exc:
        return EnjoymentGateReport(
            blueprint_sha256=blueprint_hash,
            state="ERROR",
            first_scored_review=batch,
            reviewer_model=batch.reviewer_model,
            policy_sha256=batch.policy_sha256,
            prompt_sha256=batch.prompt_sha256,
            usage=(batch.usage,),
            error=f"independent enjoyment review error: {type(exc).__name__}: {exc}",
        )
    error = _validate_scored_enjoyment_batch(
        (item,), independent_batch, anchors=anchors, expected_slot="second"
    )
    if error:
        return EnjoymentGateReport(
            blueprint_sha256=blueprint_hash,
            state="ERROR",
            first_scored_review=batch,
            second_scored_review=independent_batch,
            reviewer_model=batch.reviewer_model,
            policy_sha256=batch.policy_sha256,
            prompt_sha256=batch.prompt_sha256,
            provider_response_sha256=batch.provider_response_sha256,
            independent_reviewer_model=independent_batch.reviewer_model,
            independent_policy_sha256=independent_batch.policy_sha256,
            independent_prompt_sha256=independent_batch.prompt_sha256,
            independent_provider_response_sha256=(independent_batch.provider_response_sha256),
            usage=(batch.usage, independent_batch.usage),
            error=error,
        )
    if (
        independent_batch.reviewer_model != independent_calibration.reviewer_model
        or independent_batch.policy_sha256 != independent_calibration.policy_sha256
        or independent_batch.prompt_sha256 != independent_calibration.prompt_sha256
    ):
        return EnjoymentGateReport(
            blueprint_sha256=blueprint_hash,
            state="ERROR",
            first_scored_review=batch,
            second_scored_review=independent_batch,
            reviewer_model=batch.reviewer_model,
            policy_sha256=batch.policy_sha256,
            prompt_sha256=batch.prompt_sha256,
            provider_response_sha256=batch.provider_response_sha256,
            independent_reviewer_model=independent_batch.reviewer_model,
            independent_policy_sha256=independent_batch.policy_sha256,
            independent_prompt_sha256=independent_batch.prompt_sha256,
            independent_provider_response_sha256=(independent_batch.provider_response_sha256),
            usage=(batch.usage, independent_batch.usage),
            error="independent reviewer model/prompt differs from its calibration",
        )
    pair = EnjoymentIndependentReviewPair(first=batch, second=independent_batch)
    decision = decide_enjoyment_score(
        pair,
        candidate_item_id=item.item_id,
        positive_gold_item_ids=tuple(
            anchor.item.item_id for anchor in anchors if anchor.expected == "PASS"
        ),
    )
    first_candidate = next(
        assessment for assessment in batch.assessments
        if assessment.item_id == item.item_id
    )
    second_candidate = next(
        assessment for assessment in independent_batch.assessments
        if assessment.item_id == item.item_id
    )
    return EnjoymentGateReport(
        blueprint_sha256=blueprint_hash,
        state=decision.decision,
        first_scored_review=batch,
        second_scored_review=independent_batch,
        score_decision=decision,
        consensus_below_gold_axes=decision.consensus_below_gold_axes,
        dissent_is_advisory=first_candidate.axes != second_candidate.axes,
        reviewer_model=batch.reviewer_model,
        policy_sha256=batch.policy_sha256,
        prompt_sha256=batch.prompt_sha256,
        provider_response_sha256=batch.provider_response_sha256,
        independent_reviewer_model=independent_batch.reviewer_model,
        independent_policy_sha256=independent_batch.policy_sha256,
        independent_prompt_sha256=independent_batch.prompt_sha256,
        independent_provider_response_sha256=independent_batch.provider_response_sha256,
        usage=(batch.usage, independent_batch.usage),
    )


def _validate_quality_adjudication(
    request: QualityAdjudicationInput,
    batch: QualityAdjudicationBatch,
    calibration: QualityCalibrationReport,
) -> str | None:
    if batch.usage.calls != 1:
        return "quality adjudication must consume exactly one bounded call"
    if batch.input_sha256 != request.fingerprint():
        return "quality adjudication output is bound to a different input"
    if (
        batch.reviewer_model != calibration.reviewer_model
        or batch.policy_sha256 != calibration.policy_sha256
        or batch.prompt_sha256 != calibration.approved_adjudication_prompt_sha256
    ):
        return "quality adjudicator model/policy/prompt differs from calibration"
    if request.fact_request is None:
        if batch.fact_verdicts:
            return "quality adjudicator returned unrequested FACT verdicts"
    else:
        fact_batch = FactReviewBatch(
            reviewer_model=batch.reviewer_model,
            policy_sha256=batch.policy_sha256,
            prompt_sha256=calibration.approved_fact_prompt_sha256 or "0" * 64,
            input_sha256=request.fact_request.fingerprint(),
            verdicts=batch.fact_verdicts,
            usage=batch.usage,
        )
        fact_error = _fact_batch_error(request.fact_request, fact_batch)
        if fact_error:
            return f"independent FACT output invalid: {fact_error}"
    if not request.enjoyment_items:
        if batch.enjoyment_assessments:
            return "quality adjudicator returned unrequested ENJOY assessments"
    else:
        enjoy_batch = EnjoymentScoredReviewBatch(
            review_slot="second",
            reviewer_model=batch.reviewer_model,
            policy_sha256=batch.policy_sha256,
            prompt_sha256=batch.prompt_sha256,
            input_sha256=_enjoyment_review_input_sha256(
                request.enjoyment_items, request.enjoyment_anchors
            ),
            provider_response_sha256=batch.provider_response_sha256,
            assessments=batch.enjoyment_assessments,
            usage=batch.usage,
        )
        enjoy_error = _validate_scored_enjoyment_batch(
            request.enjoyment_items,
            enjoy_batch,
            anchors=request.enjoyment_anchors,
            expected_slot="second",
        )
        if enjoy_error:
            return f"independent ENJOY output invalid: {enjoy_error}"
    return None


def _reconcile_fact_adjudication(
    primary: FactGateReport,
    independent: QualityAdjudicationBatch,
) -> FactGateReport:
    primary_failed = any(verdict.decision == "FAIL" for verdict in primary.verdicts)
    independent_failed = any(verdict.decision == "FAIL" for verdict in independent.fact_verdicts)
    primary_findings = tuple(
        finding for verdict in primary.verdicts for finding in verdict.findings
    )
    independent_keys = {
        fact_finding_claim_id(finding)
        for verdict in independent.fact_verdicts
        for finding in verdict.findings
    }
    consensus = tuple(
        finding
        for finding in primary_findings
        if fact_finding_claim_id(finding) in independent_keys
    )
    if not primary_failed and not independent_failed:
        state: GateState = "PASS"
        error = None
    elif primary_failed and independent_failed and consensus:
        state = "FAIL"
        error = None
    else:
        state = "INCONCLUSIVE"
        error = "factual reviewers did not confirm the same result and exact defect"
    return FactGateReport(
        blueprint_sha256=primary.blueprint_sha256,
        input_sha256=primary.input_sha256,
        state=state,
        reviewer_model=primary.reviewer_model,
        policy_sha256=primary.policy_sha256,
        prompt_sha256=primary.prompt_sha256,
        provider_response_sha256=primary.provider_response_sha256,
        independent_reviewer_model=independent.reviewer_model,
        independent_policy_sha256=independent.policy_sha256,
        independent_prompt_sha256=independent.prompt_sha256,
        independent_provider_response_sha256=independent.provider_response_sha256,
        deterministic_findings=primary.deterministic_findings,
        verdicts=primary.verdicts,
        independent_verdicts=independent.fact_verdicts,
        consensus_findings=consensus,
        consensus_claim_ids=tuple(fact_finding_claim_id(item) for item in consensus),
        usage=primary.usage,
        independent_usage=independent.usage,
        error=error,
    )


def _reconcile_enjoyment_adjudication(
    primary: EnjoymentGateReport,
    independent: QualityAdjudicationBatch,
    *,
    candidate_item_id: str,
    positive_gold_item_ids: tuple[str, ...],
) -> EnjoymentGateReport:
    if primary.first_scored_review is None:
        raise ValueError("primary enjoyment report has no scored review")
    second = EnjoymentScoredReviewBatch(
        review_slot="second",
        reviewer_model=independent.reviewer_model,
        policy_sha256=independent.policy_sha256,
        prompt_sha256=independent.prompt_sha256,
        input_sha256=primary.first_scored_review.input_sha256,
        provider_response_sha256=independent.provider_response_sha256,
        assessments=independent.enjoyment_assessments,
        usage=independent.usage,
    )
    pair = EnjoymentIndependentReviewPair(
        first=primary.first_scored_review,
        second=second,
    )
    decision = decide_enjoyment_score(
        pair,
        candidate_item_id=candidate_item_id,
        positive_gold_item_ids=positive_gold_item_ids,
    )
    first_candidate = next(
        assessment for assessment in pair.first.assessments
        if assessment.item_id == candidate_item_id
    )
    second_candidate = next(
        assessment for assessment in pair.second.assessments
        if assessment.item_id == candidate_item_id
    )
    return EnjoymentGateReport(
        blueprint_sha256=primary.blueprint_sha256,
        coverage=primary.coverage,
        state=decision.decision,
        first_scored_review=pair.first,
        second_scored_review=pair.second,
        score_decision=decision,
        consensus_below_gold_axes=decision.consensus_below_gold_axes,
        dissent_is_advisory=first_candidate.axes != second_candidate.axes,
        reviewer_model=primary.reviewer_model,
        policy_sha256=primary.policy_sha256,
        prompt_sha256=primary.prompt_sha256,
        provider_response_sha256=primary.provider_response_sha256,
        independent_reviewer_model=independent.reviewer_model,
        independent_policy_sha256=independent.policy_sha256,
        independent_prompt_sha256=independent.prompt_sha256,
        independent_provider_response_sha256=independent.provider_response_sha256,
        usage=(*primary.usage, independent.usage),
    )


def certify_quality(
    blueprint: FinalTourBlueprint,
    *,
    evidence_by_beat: EvidenceByBeat,
    source_bytes_loader: Callable[[SourceEvidence], bytes],
    calibration: QualityCalibrationReport,
    enjoyment_anchors: tuple[EnjoymentCalibrationAnchor, ...],
    fact_reviewer: FactReviewer,
    enjoyment_reviewer: ScoredEnjoymentReviewer,
    adjudicator: QualityAdjudicator,
) -> QualityGateReport:
    """Run the fixed two-reviewer FACT+ENJOY protocol with one blind second call.

    Every eligible candidate uses the same three candidate-review calls: primary
    FACT, primary ENJOY, then one combined independent adjudication of both axes.
    A primary PASS therefore cannot silently become a false final PASS.
    """
    blueprint_hash = blueprint.fingerprint()
    report_binding = {
        "calibration_report_sha256": calibration.report_sha256,
        "calibration_manifest_sha256": calibration.calibration_manifest_sha256,
        "reference_manifest_sha256": calibration.reference_manifest_sha256,
        "anchor_suite_sha256": calibration.anchor_suite_sha256,
    }
    fact = certify_facts(
        blueprint,
        evidence_by_beat,
        source_bytes_loader,
        calibration,
        fact_reviewer,
    )
    if fact.state == "FAIL" and fact.deterministic_findings:
        return QualityGateReport(
            blueprint_sha256=blueprint_hash,
            **report_binding,
            calibration=calibration,
            state="FAIL",
            fact=fact,
        )
    if fact.state == "ERROR":
        return QualityGateReport(
            blueprint_sha256=blueprint_hash,
            **report_binding,
            calibration=calibration,
            state="ERROR",
            fact=fact,
            error=fact.error,
        )

    enjoyment_calibration = enjoyment_calibration_from_quality(
        calibration, anchors=enjoyment_anchors
    )
    enjoyment = certify_enjoyment(
        blueprint,
        anchors=enjoyment_anchors,
        quality_calibration=calibration,
        primary_calibration=enjoyment_calibration,
        primary_reviewer=enjoyment_reviewer,
        independent_calibration=None,
        independent_reviewer=None,
    )
    if enjoyment.state == "ERROR":
        return QualityGateReport(
            blueprint_sha256=blueprint_hash,
            **report_binding,
            calibration=calibration,
            state="ERROR",
            fact=fact,
            enjoyment=enjoyment,
            error=enjoyment.error,
        )

    try:
        fact_request, deterministic_findings = build_fact_review_input(
            blueprint, evidence_by_beat, source_bytes_loader
        )
        if deterministic_findings:
            raise ValueError("FACT preflight changed between primary and adjudication")
        enjoyment_item = _blueprint_enjoyment_item(blueprint)
        request = QualityAdjudicationInput(
            blueprint_sha256=blueprint_hash,
            fact_request=fact_request,
            enjoyment_items=(enjoyment_item,),
            enjoyment_anchors=enjoyment_anchors,
        )
        independent = adjudicator.review(request)
    except Exception as exc:
        return QualityGateReport(
            blueprint_sha256=blueprint_hash,
            **report_binding,
            calibration=calibration,
            state="ERROR",
            fact=fact,
            enjoyment=enjoyment,
            error=f"quality adjudication error: {type(exc).__name__}: {exc}",
        )
    error = _validate_quality_adjudication(request, independent, calibration)
    if error:
        return QualityGateReport(
            blueprint_sha256=blueprint_hash,
            **report_binding,
            calibration=calibration,
            state="ERROR",
            fact=fact,
            enjoyment=enjoyment,
            fact_input=request.fact_request,
            enjoyment_items=request.enjoyment_items,
            enjoyment_anchors=request.enjoyment_anchors,
            adjudication_usage=independent.usage,
            adjudication_provider_response_sha256=(independent.provider_response_sha256),
            error=error,
        )
    final_fact = _reconcile_fact_adjudication(fact, independent)
    final_enjoyment = _reconcile_enjoyment_adjudication(
        enjoyment,
        independent,
        candidate_item_id=enjoyment_item.item_id,
        positive_gold_item_ids=tuple(
            anchor.item.item_id
            for anchor in enjoyment_anchors
            if anchor.expected == "PASS"
        ),
    )
    if final_fact.state == "FAIL" or final_enjoyment.state == "FAIL":
        state: GateState = "FAIL"
    elif final_fact.state == "INCONCLUSIVE":
        state = "INCONCLUSIVE"
    else:
        state = "PASS"
    return QualityGateReport(
        blueprint_sha256=blueprint_hash,
        **report_binding,
        calibration=calibration,
        state=state,
        fact=final_fact,
        enjoyment=final_enjoyment,
        fact_input=request.fact_request,
        enjoyment_items=request.enjoyment_items,
        enjoyment_anchors=request.enjoyment_anchors,
        adjudication_usage=independent.usage,
        adjudication_provider_response_sha256=independent.provider_response_sha256,
        error=final_fact.error if state == "INCONCLUSIVE" else None,
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
#: The move: `specs/` was deleted 2026-09-02 by owner ruling — plans and specs
#: are scratch, not product — and three reference documents were living inside
#: it. They are inputs this module OPENS at runtime, which makes them fixtures,
#: so they went to `fixtures/` with the rest. Nothing was lost in the move: the
#: loader below verifies each document's bytes against the manifest's own
#: sha256, so a wrong file at the new path fails exactly as loudly as a missing
#: one.
#:
#: This was found the expensive way. A path living in JSON DATA rather than in
#: source is invisible to every search over the code, so two independent sweeps
#: for "what reads a file under specs/" reported all-clear, and the full suite
#: went red on 33 tests in this module. Any future move of these documents needs
#: a line here, not a search.
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
    if calibration_sha != bytes_sha256(calibration_bytes):
        raise ValueError("calibration manifest changed during load")
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
    "ENJOY_PROMPT_POLICY_VERSION",
    "ENJOY_RELEASE_THRESHOLD",
    "EnjoymentAxisAssessment",
    "EnjoymentAxisScores",
    "EnjoymentCalibrationAnchor",
    "EnjoymentCalibrationReport",
    "EnjoymentEvidenceSpan",
    "EnjoymentGateReport",
    "EnjoymentIndependentReviewPair",
    "EnjoymentItem",
    "EnjoymentScoreDecision",
    "EnjoymentScoredAssessment",
    "EnjoymentScoredReviewBatch",
    "EnjoymentSegment",
    "EvidenceInfrastructureError",
    "FactClaimDecision",
    "FactDerivedEvidence",
    "FactFinding",
    "FactGateReport",
    "FactPolicyCalibrationCase",
    "FactPolicyCalibrationVerdict",
    "FactReviewBatch",
    "FactReviewInput",
    "FactReviewer",
    "FactSentenceVerdict",
    "FactSourceSpan",
    "QualityAdjudicationBatch",
    "QualityAdjudicationInput",
    "QualityAdjudicator",
    "QualityCalibrationBatch",
    "QualityCalibrationInputs",
    "QualityCalibrationReport",
    "QualityCalibrationReviewer",
    "QualityGateReport",
    "ReviewUsage",
    "ScoredEnjoymentReviewer",
    "SourceEvidence",
    "build_fact_review_input",
    "calibrate_quality",
    "certify_enjoyment",
    "certify_facts",
    "certify_quality",
    "decide_enjoyment_score",
    "enjoyment_aggregate_meets_release_threshold",
    "enjoyment_axis_aggregates",
    "enjoyment_below_gold_axes",
    "enjoyment_calibration_from_quality",
    "enjoyment_consensus_below_gold_axes",
    "enjoyment_pair_meets_score_gate",
    "enjoyment_reviewer_total",
    "enjoyment_two_reviewer_aggregate",
    "fact_finding_claim_id",
    "load_enjoyment_anchors",
    "load_quality_calibration_inputs",
    "quality_calibration_report_fingerprint",
    "resolve_enjoyment_evidence_offsets",
    "source_evidence_fingerprint",
    "validate_quality_calibration_inputs",
    "validate_quality_calibration_report",
    "validate_quality_gate_report",
    "validate_source_evidence",
]
