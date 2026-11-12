"""Pure construction and validation helpers for provider-authored text review."""

from __future__ import annotations

import json

from .authoring import COMPOSE_MODEL
from .contract import Sentence
from .corpus_places import bytes_sha256
from .quality_certification import (
    EnjoymentItem,
    EnjoymentScoredReviewBatch,
    EnjoymentSegment,
    FactDerivedEvidence,
    FactReviewBatch,
    FactReviewInput,
    FactSentenceInput,
    FactSentenceVerdict,
    QualityCalibrationBatch,
    ReviewUsage,
    _fact_batch_error,
    _validate_scored_enjoyment_batch,
    calibrate_quality,
)
from .quality_requests import (
    ADJUDICATION_PROMPT_SHA256,
    BATCH_FACT_PROMPT_SHA256,
    ENJOY_PROMPT_SHA256,
    FACT_PROMPT_SHA256,
    QUALITY_POLICY_SHA256,
    ProviderCalibrationPayload,
    ProviderEnjoyPayload,
    ProviderFactPayload,
    materialize_provider_enjoyment_assessments,
)

_MODEL_PRICING: dict[str, tuple[int, int]] = {
    "claude-opus-4-8": (5, 25),
    "claude-sonnet-5": (3, 15),
}
_DEFAULT_PRICING = (5, 25)


def _canonical_hash(value: object) -> str:
    return bytes_sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )


def candidate_narration_sha256(
    sentences_by_stop: dict[int, tuple[Sentence, ...]],
) -> str:
    return _canonical_hash(
        [
            sentence.model_dump(mode="json")
            for stop_index in sorted(sentences_by_stop)
            for sentence in sentences_by_stop[stop_index]
        ]
    )


def evidence_for_sentence(
    *,
    stop_index: int,
    sentence: Sentence,
    request: object,
    route_summary: dict[str, object],
    candidate_sha256: str,
) -> tuple[FactDerivedEvidence, ...]:
    if sentence.source_type == "beat":
        result: list[FactDerivedEvidence] = []
        beats_by_id = request.beats_by_id
        for beat_id in sentence.cited_beat_ids:
            beat = beats_by_id.get(beat_id)
            if beat is None:
                raise ValueError(f"provider sentence cites beat absent from request: {beat_id}")
            payload = {
                "beat_id": beat_id,
                "key_claims": list(beat.key_claims),
                "script_body": beat.script_body,
                "source_passage": beat.source_passage,
            }
            payload_json = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            )
            result.append(
                FactDerivedEvidence(
                    evidence_id=f"beat:{beat_id}",
                    blueprint_sha256=candidate_sha256,
                    source_id=beat_id,
                    stop_index=stop_index,
                    claim_requirement="MATERIAL",
                    derivation_kind="corpus_field",
                    derivation_payload_json=payload_json,
                    derivation_payload_sha256=bytes_sha256(payload_json.encode("utf-8")),
                )
            )
        return tuple(result)

    route_payload = {
        "route_receipt": route_summary,
        "stop_index": stop_index,
        "visited_claims_before_stop": list(request.visited_claims_by_slot.get(stop_index, ())),
    }
    payload_json = json.dumps(
        route_payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return (
        FactDerivedEvidence(
            evidence_id=f"derived:{stop_index}:{sentence.source_id}",
            blueprint_sha256=candidate_sha256,
            source_id=sentence.source_id,
            stop_index=stop_index,
            claim_requirement="REVIEW_ALL",
            derivation_kind="route_snapshot",
            derivation_payload_json=payload_json,
            derivation_payload_sha256=bytes_sha256(payload_json.encode("utf-8")),
        ),
    )


def build_fact_input(
    *,
    stop_index: int,
    sentences: tuple[Sentence, ...],
    request: object,
    route_summary: dict[str, object],
    candidate_sha256: str,
) -> FactReviewInput:
    table: dict[str, FactDerivedEvidence] = {}
    sentence_inputs: list[FactSentenceInput] = []
    for sentence_index, sentence in enumerate(sentences):
        evidence = evidence_for_sentence(
            stop_index=stop_index,
            sentence=sentence,
            request=request,
            route_summary=route_summary,
            candidate_sha256=candidate_sha256,
        )
        for item in evidence:
            prior = table.get(item.evidence_id)
            if prior is not None and prior != item:
                raise ValueError("a factual evidence id has conflicting content")
            table[item.evidence_id] = item
        sentence_inputs.append(
            FactSentenceInput(
                sentence_index=sentence_index,
                stop_index=stop_index,
                sentence_sha256=bytes_sha256(sentence.text.encode("utf-8")),
                text=sentence.text,
                source_type=sentence.source_type,
                claim_requirement=("MATERIAL" if sentence.source_type == "beat" else "REVIEW_ALL"),
                evidence_ids=tuple(item.evidence_id for item in evidence),
            )
        )
    return FactReviewInput(
        blueprint_sha256=candidate_sha256,
        provider_narration_sha256=candidate_sha256,
        evidence_table=tuple(table.values()),
        sentences=tuple(sentence_inputs),
    )


def build_enjoyment_item(
    sentences_by_stop: dict[int, tuple[Sentence, ...]],
    candidate_sha256: str,
) -> EnjoymentItem:
    segments: list[EnjoymentSegment] = []
    for stop_index in sorted(sentences_by_stop):
        narration = " ".join(sentence.text for sentence in sentences_by_stop[stop_index])
        segments.append(
            EnjoymentSegment(
                segment_id=f"segment-{bytes_sha256(f'{candidate_sha256}:{stop_index}'.encode())[:24]}",
                stop_index=stop_index,
                placement="stop",
                narration=narration,
                narration_sha256=bytes_sha256(narration.encode("utf-8")),
            )
        )
    narration = "\n".join(segment.narration for segment in segments)
    return EnjoymentItem(
        item_id=f"item-{bytes_sha256(f'item:{candidate_sha256}'.encode())[:24]}",
        source_payload_sha256=candidate_sha256,
        narration=narration,
        narration_sha256=bytes_sha256(narration.encode("utf-8")),
        segments=tuple(segments),
    )


class _CalibrationReplay:
    def __init__(self, batch: QualityCalibrationBatch) -> None:
        self.batch = batch

    def calibrate(self, fact_cases, enjoyment_anchors) -> QualityCalibrationBatch:
        return self.batch


def review_usage(receipt: dict[str, object]) -> ReviewUsage:
    input_tokens = int(receipt["input_tokens"])
    output_tokens = int(receipt["output_tokens"])
    model = str(receipt.get("model", ""))
    input_rate, output_rate = _MODEL_PRICING.get(model, _DEFAULT_PRICING)
    return ReviewUsage(
        calls=1,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost_usd=(
            input_tokens * input_rate + output_tokens * output_rate
        )
        / 1_000_000,
    )


def validate_calibration(
    receipt: dict[str, object],
    payload: ProviderCalibrationPayload,
    inputs: object,
    *,
    model: str = COMPOSE_MODEL,
    fact_only: bool = False,
) -> object:
    normalized_enjoyment = materialize_provider_enjoyment_assessments(
        payload.enjoyment_assessments,
        items=tuple(anchor.item for anchor in inputs.enjoyment_anchors),
    )
    batch = QualityCalibrationBatch(
        reviewer_model=model,
        policy_sha256=QUALITY_POLICY_SHA256,
        approved_fact_prompt_sha256=FACT_PROMPT_SHA256,
        approved_enjoy_prompt_sha256=ENJOY_PROMPT_SHA256,
        approved_adjudication_prompt_sha256=ADJUDICATION_PROMPT_SHA256,
        input_sha256=_canonical_hash(
            {
                "fact_cases": [case.model_dump(mode="json") for case in inputs.fact_cases],
                "enjoyment_anchors": [
                    anchor.model_dump(mode="json") for anchor in inputs.enjoyment_anchors
                ],
            }
        ),
        provider_response_sha256=receipt["response_sha256"],
        fact_verdicts=payload.fact_verdicts,
        enjoyment_assessments=normalized_enjoyment,
        usage=review_usage(receipt),
    )
    report = calibrate_quality(inputs=inputs, reviewer=_CalibrationReplay(batch))
    if report.passed:
        return report
    if report.error:
        raise ValueError(f"review model calibration error: {report.error}")
    if fact_only:
        expected = {case.case_id: case.expected for case in inputs.fact_cases}
        if all(
            expected.get(v.case_id) == v.decision for v in payload.fact_verdicts
        ):
            return report
    raise ValueError(
        f"review model did not pass frozen calibration: {report.state}"
    )


def validate_fact(
    receipt: dict[str, object], payload: ProviderFactPayload, request: FactReviewInput
) -> FactReviewBatch:
    expected_indices = list(range(len(request.sentences)))
    if [item.sentence_index for item in payload.verdicts] != expected_indices:
        raise ValueError(
            "invalid FACT review payload: fact reviewer must cover every sentence "
            "exactly once and in order"
        )

    verdicts: list[FactSentenceVerdict] = []
    for item, sentence in zip(payload.verdicts, request.sentences, strict=True):
        sentence_bytes = sentence.text.encode("utf-8")
        findings = []
        for finding in item.findings:
            passage_bytes = finding.exact_passage.encode("utf-8")
            passage_start = finding.passage_byte_start
            passage_end = finding.passage_byte_end
            supplied_span_is_exact = (
                0 <= passage_start < passage_end <= len(sentence_bytes)
                and sentence_bytes[passage_start:passage_end] == passage_bytes
            )
            if not supplied_span_is_exact:
                passage_start = sentence_bytes.find(passage_bytes)
                if (
                    passage_start < 0
                    or sentence_bytes.find(passage_bytes, passage_start + 1) >= 0
                ):
                    raise ValueError(
                        "invalid FACT review payload: fact finding at sentence "
                        f"{sentence.sentence_index} must identify one unambiguous exact passage"
                    )
                passage_end = passage_start + len(passage_bytes)
            findings.append(
                finding.model_copy(
                    update={
                        "sentence_index": sentence.sentence_index,
                        "sentence_sha256": sentence.sentence_sha256,
                        "passage_byte_start": passage_start,
                        "passage_byte_end": passage_end,
                    }
                )
            )
        verdicts.append(
            FactSentenceVerdict(
                sentence_index=sentence.sentence_index,
                sentence_sha256=sentence.sentence_sha256,
                decision=item.decision,
                findings=tuple(findings),
            )
        )
    batch = FactReviewBatch(
        reviewer_model=COMPOSE_MODEL,
        policy_sha256=QUALITY_POLICY_SHA256,
        prompt_sha256=BATCH_FACT_PROMPT_SHA256,
        input_sha256=request.fingerprint(),
        provider_response_sha256=receipt["response_sha256"],
        verdicts=tuple(verdicts),
        usage=review_usage(receipt),
    )
    error = _fact_batch_error(request, batch)
    if error:
        raise ValueError(f"invalid FACT review payload: {error}")
    return batch


def validate_enjoyment(
    receipt: dict[str, object],
    payload: ProviderEnjoyPayload,
    candidates: tuple[EnjoymentItem, ...],
    anchors: tuple,
) -> EnjoymentScoredReviewBatch:
    normalized_assessments = materialize_provider_enjoyment_assessments(
        payload.assessments,
        items=(*tuple(anchor.item for anchor in anchors), *candidates),
    )
    batch = EnjoymentScoredReviewBatch(
        review_slot="first",
        reviewer_model=COMPOSE_MODEL,
        policy_sha256=QUALITY_POLICY_SHA256,
        prompt_sha256=ENJOY_PROMPT_SHA256,
        input_sha256=_canonical_hash(
            {
                "items": [
                    value.model_dump(mode="json")
                    for value in sorted(
                        (*tuple(anchor.item for anchor in anchors), *candidates),
                        key=lambda value: value.item_id,
                    )
                ]
            }
        ),
        provider_response_sha256=receipt["response_sha256"],
        assessments=normalized_assessments,
        usage=review_usage(receipt),
    )
    error = _validate_scored_enjoyment_batch(
        candidates, batch, anchors=anchors, expected_slot="first"
    )
    if error:
        raise ValueError(f"invalid ENJOY review payload: {error}")
    return batch
