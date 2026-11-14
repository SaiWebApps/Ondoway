"""Pure input loading for semantic review of the sealed 4+4 Premium batch."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict

from pydantic import BaseModel

from scripts.tour_batch_candidate import DEFAULT_OUTPUT_ROOT, MANIFEST_PATH, _batch_plan
from scripts.tour_text_candidate import _private_write_new
from src.connection import create_driver
from src.tour.authoring import COMPOSE_MODEL, FACT_REVIEW_MODEL, _sentences_from_json
from src.tour.batch_regression_manifest import load_frozen_tour_batch
from src.tour.certification_provider import (
    AnthropicCertificationProvider,
    CallPurpose,
    PhysicalProviderResponse,
)
from src.tour.contract import Sentence
from src.tour.corpus_places import canonical_bytes
from src.tour.provider_text_review import (
    build_enjoyment_item,
    build_fact_input,
    candidate_narration_sha256,
    validate_calibration,
    validate_enjoyment,
    validate_fact,
)
from src.tour.quality_certification import (
    EnjoymentItem,
    FactDerivedEvidence,
    FactReviewInput,
    QualityCalibrationInputs,
    load_quality_calibration_inputs,
)
from src.tour.quality_requests import (
    BATCH_FACT_PROMPT_SHA256,
    CALIBRATION_PROMPT_SHA256,
    ENJOY_PROMPT_SHA256,
    QUALITY_POLICY_SHA256,
    ProviderCalibrationPayload,
    ProviderEnjoyPayload,
    ProviderFactPayload,
    batch_fact_request_envelope,
    calibration_request_envelope,
    enjoyment_request_envelope,
    request_envelope_sha256,
)
from src.tour.routing_client import RoutingClient

LIVE_REVIEW_APPROVAL_ENV = "ONDOWAY_TOUR_BATCH_REVIEW_APPROVED"
FACT_CANARY_UNIT_ID = "fact:paris-west-axis-90"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REVIEW_ROOT = ROOT / "data" / "certification" / "tour-batch-review-v2"
LEGACY_REVIEW_ROOT = ROOT / "data" / "certification" / "tour-batch-review-v1"


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True)
class BatchReviewTourInputs:
    case_id: str
    tour_plan: dict[str, object]
    tour_artifact: dict[str, object]
    receipts: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class BatchReviewInputs:
    batch_plan_sha256: str
    tours: tuple[BatchReviewTourInputs, ...]


@dataclass(frozen=True)
class BatchReviewMaterial:
    fact_inputs: dict[str, FactReviewInput]
    enjoyment_items: dict[str, EnjoymentItem]


@dataclass(frozen=True)
class ReviewRuntimeUnit:
    unit_id: str
    purpose: Literal["calibration", "fact_review", "enjoy_review"]
    envelope: str
    sdk_request: dict[str, object]
    payload_type: type[BaseModel]
    substantive_input: object
    enjoyment_anchors: tuple[object, ...] = ()


@dataclass(frozen=True)
class BatchReviewDispatchResult:
    evaluation_status: Literal["COMPLETE", "PARTIAL", "INFRA_ERROR"]
    response_receipts: dict[str, dict[str, object]]
    parsed_results: dict[str, object]
    infrastructure_errors: dict[str, str]


@dataclass(frozen=True)
class ProviderFreeReviewContext:
    inputs: BatchReviewInputs
    material: BatchReviewMaterial
    calibration_inputs: QualityCalibrationInputs
    review_plan: BatchReviewPlan
    runtime: dict[str, ReviewRuntimeUnit]


def _review_authoring_requests(
    *,
    inputs: BatchReviewInputs,
    runtime: Mapping[str, object],
) -> dict[tuple[str, int], object]:
    """Copy authoring requests and restore only evidenced vignette BeatRefs.

    Certification authoring omitted vignette beats from ``beats_by_id`` even
    when both its stitched source and the durable provider response cited them.
    The sealed request remains untouched; these copies exist only to recover
    the source evidence needed for review.
    """

    requests: dict[tuple[str, int], object] = {}
    for tour in inputs.tours:
        tour_runtime = runtime[tour.case_id]
        vignette_by_id = {
            beat.id: beat
            for beats in tour_runtime["sequence"].vignette_beats.values()
            for beat in beats
        }
        receipts = {receipt["stop_index"]: receipt for receipt in tour.receipts}
        for stop_index, item in tour_runtime["units"].items():
            request = item["request"]
            stitched_ids = {
                beat_id
                for sentence in request.stitched.script
                for beat_id in sentence.cited_beat_ids
            }
            provider_ids = {
                beat_id
                for raw in json.loads(receipts[stop_index]["raw_response"])["sentences"]
                for beat_id in Sentence.model_validate(raw).cited_beat_ids
            }
            additions = {
                beat_id: vignette_by_id[beat_id]
                for beat_id in stitched_ids & provider_ids
                if beat_id in vignette_by_id and beat_id not in request.beats_by_id
            }
            requests[(tour.case_id, stop_index)] = request.model_copy(
                update={"beats_by_id": {**request.beats_by_id, **additions}}
            )
    return requests


class DirectoryReviewReceiptStore:
    """Exclusive, canonical review receipts rooted in one sealed run directory."""

    def __init__(self, root: Path) -> None:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not root.is_dir() or root.is_symlink():
            raise ValueError("review receipt root must be an ordinary directory")
        self.root = root

    def _path(self, kind: str, unit_id: str) -> Path:
        unit_key = _sha256(unit_id.encode("utf-8"))[:24]
        return self.root / f"{kind}-{unit_key}.json"

    def _load(self, kind: str, unit_id: str) -> dict[str, object] | None:
        path = self._path(kind, unit_id)
        if not path.exists():
            return None
        if path.is_symlink() or not path.is_file():
            raise ValueError("review receipt path is not an ordinary file")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("review receipt is not an object")
        return value

    def load_attempt(self, unit_id: str) -> dict[str, object] | None:
        return self._load("attempt", unit_id)

    def load_response(self, unit_id: str) -> dict[str, object] | None:
        return self._load("response", unit_id)

    def load_failure(self, unit_id: str) -> dict[str, object] | None:
        return self._load("failure", unit_id)

    def write_attempt(self, unit_id: str, value: dict[str, object]) -> None:
        _private_write_new(self._path("attempt", unit_id), value)

    def write_response(self, unit_id: str, value: dict[str, object]) -> None:
        _private_write_new(self._path("response", unit_id), value)

    def write_failure(self, unit_id: str, value: dict[str, object]) -> None:
        _private_write_new(self._path("failure", unit_id), value)


class BatchReviewPlanUnit(TypedDict):
    unit_id: str
    purpose: Literal["calibration", "fact_review", "enjoy_review"]
    case_id: str | None
    request_sha256: str
    input_byte_count: int
    output_token_ceiling: int


class BatchReviewPlan(TypedDict):
    schema_version: Literal["ondoway-tour-batch-review-plan-v1"]
    authoring_batch_plan_sha256: str
    authoring_request_sha256s: dict[str, list[str]]
    provider_receipt_sha256s: dict[str, list[str]]
    tour_artifact_sha256s: dict[str, str]
    calibration_manifest_sha256: str
    reference_manifest_sha256: str
    calibration_bundle_sha256: str
    quality_policy_sha256: str
    prompt_sha256s: dict[str, str]
    model: str
    thinking: dict[str, str]
    sdk_max_retries: int
    application_deadline_seconds: None
    units: list[BatchReviewPlanUnit]
    review_plan_sha256: str


def _require_self_hash(document: dict[str, object], hash_field: str) -> None:
    expected = document.get(hash_field)
    core = {key: value for key, value in document.items() if key != hash_field}
    if not isinstance(expected, str) or expected != _sha256(canonical_bytes(core)):
        raise ValueError(f"invalid {hash_field}")


def load_batch_review_inputs(
    *,
    batch_plan: dict[str, object],
    tour_artifacts: Mapping[str, dict[str, object]],
    stop_receipts: Mapping[tuple[str, int], dict[str, object]],
) -> BatchReviewInputs:
    """Validate and type the exact provider-authored inputs for batch review."""

    if batch_plan.get("schema_version") != "ondoway-tour-batch-plan-v1":
        raise ValueError("unexpected batch plan schema")
    _require_self_hash(batch_plan, "batch_plan_sha256")
    planned_tours = batch_plan.get("tours")
    if not isinstance(planned_tours, list) or len(planned_tours) != 8:
        raise ValueError("review batch must contain exactly eight planned tours")

    case_ids = [tour.get("case_id") for tour in planned_tours if isinstance(tour, dict)]
    if len(case_ids) != 8 or any(not isinstance(case_id, str) for case_id in case_ids):
        raise ValueError("every planned tour must have a case id")
    if len(set(case_ids)) != 8:
        raise ValueError("planned tour case ids must be unique")
    if set(tour_artifacts) != set(case_ids):
        raise ValueError("tour artifacts do not exactly match planned tour ids")

    expected_receipt_keys: set[tuple[str, int]] = set()
    result: list[BatchReviewTourInputs] = []
    for tour_plan in planned_tours:
        if not isinstance(tour_plan, dict):
            raise ValueError("planned tour must be an object")
        _require_self_hash(tour_plan, "tour_plan_sha256")
        case_id = tour_plan["case_id"]
        units = tour_plan.get("units")
        if not isinstance(units, list):
            raise ValueError("planned tour units must be a list")
        unit_by_stop: dict[int, dict[str, object]] = {}
        for unit in units:
            if not isinstance(unit, dict) or not isinstance(unit.get("stop_index"), int):
                raise ValueError("planned stop unit is malformed")
            stop_index = unit["stop_index"]
            if stop_index in unit_by_stop:
                raise ValueError("planned tour repeats a stop index")
            unit_by_stop[stop_index] = unit
            expected_receipt_keys.add((case_id, stop_index))

        artifact = tour_artifacts[case_id]
        if artifact.get("schema_version") != "ondoway-provider-authored-tour-v1":
            raise ValueError("unexpected tour artifact schema")
        if artifact.get("provenance") != "provider_response":
            raise ValueError("tour artifact is not provider-authored Premium output")
        if artifact.get("case_id") != case_id:
            raise ValueError("tour artifact case id differs from its plan")
        if artifact.get("tour_plan_sha256") != tour_plan["tour_plan_sha256"]:
            raise ValueError("tour artifact differs from its tour plan")
        if artifact.get("model") != tour_plan.get("model"):
            raise ValueError("tour artifact model differs from its plan")
        _require_self_hash(artifact, "artifact_sha256")

        artifact_stops = artifact.get("stops")
        if not isinstance(artifact_stops, list):
            raise ValueError("tour artifact stops must be a list")
        artifact_by_stop: dict[int, dict[str, object]] = {}
        for stop in artifact_stops:
            if not isinstance(stop, dict) or not isinstance(stop.get("stop_index"), int):
                raise ValueError("tour artifact stop is malformed")
            stop_index = stop["stop_index"]
            if stop_index in artifact_by_stop:
                raise ValueError("tour artifact repeats a stop index")
            artifact_by_stop[stop_index] = stop
        if set(artifact_by_stop) != set(unit_by_stop):
            raise ValueError("tour artifact is incomplete or has unplanned stops")

        receipts: list[dict[str, object]] = []
        ordered_text: list[str] = []
        for stop_index, unit in sorted(unit_by_stop.items()):
            receipt = stop_receipts.get((case_id, stop_index))
            if receipt is None:
                raise ValueError("planned provider receipt is missing")
            receipt_core = {
                key: value for key, value in receipt.items() if key != "receipt_sha256"
            }
            raw_response = receipt.get("raw_response")
            schema_version = receipt.get("schema_version")
            if schema_version == "ondoway-text-candidate-stop-v2":
                # A Batch API receipt (the v2 authoring transport): the batch id
                # replaces the per-call latency, and only a recorded SUCCESS may
                # sit in a stop file — every other outcome is a failure file.
                batch_id = receipt.get("batch_id")
                if (
                    not isinstance(batch_id, str)
                    or not batch_id
                    or receipt.get("result_type") != "succeeded"
                    or "latency_ms" in receipt
                ):
                    raise ValueError("batch stop receipt is not a well-formed success")
            elif schema_version != "ondoway-text-candidate-stop-v1":
                raise ValueError("provider receipt differs from its planned request")
            if (
                receipt.get("stop_index") != stop_index
                or receipt.get("request_id") != unit.get("request_id")
                or receipt.get("request_sha256") != unit.get("request_sha256")
                or receipt.get("model") != tour_plan.get("model")
                or receipt.get("receipt_sha256") != _sha256(canonical_bytes(receipt_core))
                or not isinstance(raw_response, str)
                or receipt.get("response_sha256") != _sha256(raw_response.encode("utf-8"))
            ):
                raise ValueError("provider receipt differs from its planned request")
            artifact_stop = artifact_by_stop[stop_index]
            if (
                artifact_stop.get("poi_name") != unit.get("poi_name")
                or artifact_stop.get("provider_response_sha256")
                != receipt.get("response_sha256")
                or artifact_stop.get("parsed_payload_sha256")
                != receipt.get("parsed_payload_sha256")
            ):
                raise ValueError("tour artifact stop differs from its provider receipt")
            sentences = artifact_stop.get("sentences")
            if not isinstance(sentences, list) or any(
                not isinstance(sentence, str) for sentence in sentences
            ):
                raise ValueError("tour artifact narration is malformed")
            ordered_text.extend(sentences)
            receipts.append(receipt)
        if artifact.get("customer_text_sha256") != _sha256(canonical_bytes(ordered_text)):
            raise ValueError("tour artifact customer text hash is invalid")
        result.append(
            BatchReviewTourInputs(
                case_id=case_id,
                tour_plan=tour_plan,
                tour_artifact=artifact,
                receipts=tuple(receipts),
            )
        )

    # The receipt count is the PLAN's own unit count, never a literal: how many
    # stops a day carries changes with the engine, and the set equality against
    # the plan is the check that cannot go stale.
    if not expected_receipt_keys or set(stop_receipts) != expected_receipt_keys:
        raise ValueError("review batch must contain exactly the planned provider receipts")
    return BatchReviewInputs(
        batch_plan_sha256=batch_plan["batch_plan_sha256"],
        tours=tuple(result),
    )


def build_batch_review_material(
    *,
    inputs: BatchReviewInputs,
    authoring_requests: Mapping[tuple[str, int], object],
) -> BatchReviewMaterial:
    """Build one full-tour factual input and enjoyment item per sealed tour."""

    expected_request_keys = {
        (tour.case_id, receipt["stop_index"])
        for tour in inputs.tours
        for receipt in tour.receipts
    }
    if set(authoring_requests) != expected_request_keys:
        raise ValueError("authoring requests do not exactly match the sealed provider receipts")

    fact_inputs: dict[str, FactReviewInput] = {}
    enjoyment_items: dict[str, EnjoymentItem] = {}
    for tour in inputs.tours:
        artifact_stops = {
            stop["stop_index"]: stop for stop in tour.tour_artifact["stops"]
        }
        sentences_by_stop: dict[int, tuple[Sentence, ...]] = {}
        for receipt in tour.receipts:
            stop_index = receipt["stop_index"]
            payload = json.loads(receipt["raw_response"])
            raw_sentences = payload.get("sentences")
            if not isinstance(raw_sentences, list):
                raise ValueError("provider receipt has no sentence list")
            # THE one parser (never a bare model_validate): the assembly judged
            # the day through `_sentences_from_json`'s deterministic corrections
            # (mangled beat ids, a glue line mistyped "beat"), so the review must
            # read the SAME sentences — a raw re-parse rejects a mistyped
            # GLUE_REFLECTION that the parser corrects.
            sentences = _sentences_from_json(
                raw_sentences, authoring_requests[(tour.case_id, stop_index)]
            )
            if any(sentence.stop_idx != stop_index for sentence in sentences):
                raise ValueError("provider sentence is assigned to the wrong stop")
            if tuple(sentence.text for sentence in sentences) != tuple(
                artifact_stops[stop_index]["sentences"]
            ):
                raise ValueError("provider receipt narration differs from the tour artifact")
            sentences_by_stop[stop_index] = sentences

        narration_sha256 = candidate_narration_sha256(sentences_by_stop)
        evidence_by_id: dict[str, FactDerivedEvidence] = {}
        full_sentences = []
        sentence_index = 0
        for stop_index in sorted(sentences_by_stop):
            stop_input = build_fact_input(
                stop_index=stop_index,
                sentences=sentences_by_stop[stop_index],
                request=authoring_requests[(tour.case_id, stop_index)],
                route_summary=tour.tour_plan["route"],
                candidate_sha256=narration_sha256,
            )
            for evidence in stop_input.evidence_table:
                prior = evidence_by_id.get(evidence.evidence_id)
                if prior is not None and prior != evidence:
                    raise ValueError("full-tour factual evidence id has conflicting content")
                evidence_by_id[evidence.evidence_id] = evidence
            for sentence in stop_input.sentences:
                full_sentences.append(
                    sentence.model_copy(update={"sentence_index": sentence_index})
                )
                sentence_index += 1
        fact_inputs[tour.case_id] = FactReviewInput(
            blueprint_sha256=narration_sha256,
            provider_narration_sha256=narration_sha256,
            evidence_table=tuple(evidence_by_id.values()),
            sentences=tuple(full_sentences),
        )
        enjoyment_items[tour.case_id] = build_enjoyment_item(
            sentences_by_stop,
            narration_sha256,
        )

    if len(fact_inputs) != 8 or len(enjoyment_items) != 8:
        raise ValueError("review material must contain exactly eight complete tours")
    return BatchReviewMaterial(
        fact_inputs=fact_inputs,
        enjoyment_items=enjoyment_items,
    )


def _review_plan_unit(
    *,
    unit_id: str,
    purpose: Literal["calibration", "fact_review", "enjoy_review"],
    case_id: str | None,
    envelope: str,
    sdk_request: dict[str, object],
) -> BatchReviewPlanUnit:
    if sdk_request.get("thinking") != {"type": "adaptive"}:
        raise ValueError("review request does not preserve adaptive thinking")
    output_ceiling = sdk_request.get("max_tokens")
    if not isinstance(output_ceiling, int):
        raise ValueError("review request has no output token ceiling")
    return {
        "unit_id": unit_id,
        "purpose": purpose,
        "case_id": case_id,
        "request_sha256": request_envelope_sha256(envelope),
        "input_byte_count": len(envelope.encode("utf-8")),
        "output_token_ceiling": output_ceiling,
    }


def build_batch_review_runtime(
    *,
    material: BatchReviewMaterial,
    calibration_inputs: QualityCalibrationInputs,
    archival_enjoyment: dict[str, EnjoymentItem] | None = None,
) -> dict[str, ReviewRuntimeUnit]:
    """Build the exact prepared requests behind the sealed 18-unit plan."""

    case_ids = sorted(material.fact_inputs)
    if len(case_ids) != 8 or set(material.enjoyment_items) != set(case_ids):
        raise ValueError("review runtime requires exactly eight complete tour inputs")
    if archival_enjoyment is not None and set(archival_enjoyment) != set(case_ids):
        raise ValueError("archival enjoyment items do not cover the review batch case ids")
    runtime: dict[str, ReviewRuntimeUnit] = {}

    envelope, sdk_request = calibration_request_envelope(
        calibration_inputs.fact_cases,
        calibration_inputs.enjoyment_anchors,
        model=COMPOSE_MODEL,
    )
    runtime["calibration"] = ReviewRuntimeUnit(
        unit_id="calibration",
        purpose="calibration",
        envelope=envelope,
        sdk_request=sdk_request,
        payload_type=ProviderCalibrationPayload,
        substantive_input=calibration_inputs,
    )
    envelope, sdk_request = calibration_request_envelope(
        calibration_inputs.fact_cases,
        calibration_inputs.enjoyment_anchors,
        model=FACT_REVIEW_MODEL,
    )
    runtime["calibration-fact"] = ReviewRuntimeUnit(
        unit_id="calibration-fact",
        purpose="calibration",
        envelope=envelope,
        sdk_request=sdk_request,
        payload_type=ProviderCalibrationPayload,
        substantive_input=calibration_inputs,
    )
    for case_id in case_ids:
        envelope, sdk_request = batch_fact_request_envelope(
            material.fact_inputs[case_id],
            model=FACT_REVIEW_MODEL,
        )
        unit_id = f"fact:{case_id}"
        runtime[unit_id] = ReviewRuntimeUnit(
            unit_id=unit_id,
            purpose="fact_review",
            envelope=envelope,
            sdk_request=sdk_request,
            payload_type=ProviderFactPayload,
            substantive_input=material.fact_inputs[case_id],
        )
    for case_id in case_ids:
        if archival_enjoyment is not None:
            enjoy_candidates = (archival_enjoyment[case_id], material.enjoyment_items[case_id])
        else:
            enjoy_candidates = (material.enjoyment_items[case_id],)

        envelope, sdk_request = enjoyment_request_envelope(
            enjoy_candidates,
            calibration_inputs.enjoyment_anchors,
            model=COMPOSE_MODEL,
        )
        unit_id = f"enjoy:{case_id}"
        runtime[unit_id] = ReviewRuntimeUnit(
            unit_id=unit_id,
            purpose="enjoy_review",
            envelope=envelope,
            sdk_request=sdk_request,
            payload_type=ProviderEnjoyPayload,
            substantive_input=enjoy_candidates,
            enjoyment_anchors=tuple(calibration_inputs.enjoyment_anchors),
        )
    if len(runtime) != 18 or any(
        unit.sdk_request.get("thinking") != {"type": "adaptive"}
        for unit in runtime.values()
    ):
        raise ValueError("review runtime does not preserve the sealed execution policy")
    return runtime


def _attempt_record(unit: ReviewRuntimeUnit) -> dict[str, object]:
    core = {
        "schema_version": "ondoway-tour-batch-review-attempt-v1",
        "unit_id": unit.unit_id,
        "request_sha256": request_envelope_sha256(unit.envelope),
    }
    return {**core, "marker_sha256": _sha256(canonical_bytes(core))}


def _validate_attempt(unit: ReviewRuntimeUnit, value: dict[str, object]) -> None:
    core = {key: item for key, item in value.items() if key != "marker_sha256"}
    if value != _attempt_record(unit) or value.get("marker_sha256") != _sha256(
        canonical_bytes(core)
    ):
        raise ValueError("altered or plan-mismatched batch review attempt")


def _response_record(
    unit: ReviewRuntimeUnit,
    response: PhysicalProviderResponse,
) -> dict[str, object]:
    raw_response = response.body.decode("utf-8")
    core = {
        "schema_version": "ondoway-tour-batch-review-response-v1",
        "unit_id": unit.unit_id,
        "purpose": unit.purpose,
        "request_sha256": request_envelope_sha256(unit.envelope),
        "response_sha256": _sha256(response.body),
        "provider_request_id": response.provider_request_id,
        "model": response.model,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "latency_ms": response.latency_ms,
        "raw_response": raw_response,
    }
    if response.stop_reason is not None:
        core["stop_reason"] = response.stop_reason
    return {**core, "receipt_sha256": _sha256(canonical_bytes(core))}


def _validate_response(unit: ReviewRuntimeUnit, value: dict[str, object]) -> None:
    core = {key: item for key, item in value.items() if key != "receipt_sha256"}
    raw_response = value.get("raw_response")
    if (
        value.get("schema_version") != "ondoway-tour-batch-review-response-v1"
        or value.get("unit_id") != unit.unit_id
        or value.get("purpose") != unit.purpose
        or value.get("request_sha256") != request_envelope_sha256(unit.envelope)
        or value.get("model") != unit.sdk_request.get("model")
        or not (value.get("stop_reason") is None or isinstance(value.get("stop_reason"), str))
        or not isinstance(raw_response, str)
        or value.get("response_sha256") != _sha256(raw_response.encode("utf-8"))
        or value.get("receipt_sha256") != _sha256(canonical_bytes(core))
    ):
        raise ValueError("altered or plan-mismatched batch review response")


def _failure_record(unit: ReviewRuntimeUnit, exc: Exception) -> dict[str, object]:
    core = {
        "schema_version": "ondoway-tour-batch-review-failure-v1",
        "unit_id": unit.unit_id,
        "request_sha256": request_envelope_sha256(unit.envelope),
        "failure_type": type(exc).__name__,
        "failure_message_sha256": _sha256(str(exc).encode("utf-8")),
    }
    return {**core, "failure_sha256": _sha256(canonical_bytes(core))}


def _parse_review_response(unit: ReviewRuntimeUnit, receipt: dict[str, object]) -> object:
    if receipt.get("stop_reason") == "max_tokens":
        raise ValueError("Anthropic certification response was truncated")
    payload = unit.payload_type.model_validate_json(receipt["raw_response"])
    if unit.purpose == "calibration":
        model = str(unit.sdk_request.get("model", COMPOSE_MODEL))
        return validate_calibration(
            receipt, payload, unit.substantive_input,
            model=model, fact_only=(unit.unit_id == "calibration-fact"),
        )
    if unit.purpose == "fact_review":
        return validate_fact(receipt, payload, unit.substantive_input)
    return validate_enjoyment(
        receipt,
        payload,
        unit.substantive_input,
        unit.enjoyment_anchors,
    )


def seed_exact_calibration_receipt(
    *,
    unit: ReviewRuntimeUnit,
    source: DirectoryReviewReceiptStore,
    target: DirectoryReviewReceiptStore,
) -> bool:
    """Reuse only a cryptographically identical completed calibration call."""

    if unit.unit_id not in ("calibration", "calibration-fact"):
        raise ValueError("only calibration may be reused across review roots")
    if any(
        loader(unit.unit_id) is not None
        for loader in (target.load_attempt, target.load_response, target.load_failure)
    ):
        return False
    attempt = source.load_attempt(unit.unit_id)
    response = source.load_response(unit.unit_id)
    if attempt is None or response is None or source.load_failure(unit.unit_id) is not None:
        return False
    try:
        _validate_attempt(unit, attempt)
        _validate_response(unit, response)
    except (TypeError, ValueError):
        return False
    target.write_attempt(unit.unit_id, attempt)
    target.write_response(unit.unit_id, response)
    return True


def _run_review_unit(
    *,
    unit: ReviewRuntimeUnit,
    store: DirectoryReviewReceiptStore,
    invoke: Callable[[str, str, Mapping[str, object]], PhysicalProviderResponse],
) -> tuple[dict[str, object] | None, object | None, str | None]:
    response = store.load_response(unit.unit_id)
    attempt = store.load_attempt(unit.unit_id)
    failure = store.load_failure(unit.unit_id)
    if response is not None:
        if attempt is None:
            return response, None, "completed response has no durable attempt marker"
        try:
            _validate_attempt(unit, attempt)
            _validate_response(unit, response)
            return response, _parse_review_response(unit, response), None
        except Exception as exc:
            return response, None, f"{type(exc).__name__}:{_sha256(str(exc).encode())}"
    if attempt is not None:
        try:
            _validate_attempt(unit, attempt)
        except Exception as exc:
            return None, None, f"{type(exc).__name__}:{_sha256(str(exc).encode())}"
        return None, None, "indeterminate prior paid attempt"
    if failure is not None:
        return None, None, "terminal prior provider failure"

    store.write_attempt(unit.unit_id, _attempt_record(unit))
    try:
        physical = invoke(unit.purpose, unit.unit_id, unit.sdk_request)
        receipt = _response_record(unit, physical)
    except Exception as exc:
        store.write_failure(unit.unit_id, _failure_record(unit, exc))
        return None, None, f"{type(exc).__name__}:{_sha256(str(exc).encode())}"
    store.write_response(unit.unit_id, receipt)
    try:
        return receipt, _parse_review_response(unit, receipt), None
    except Exception as exc:
        return receipt, None, f"{type(exc).__name__}:{_sha256(str(exc).encode())}"


def dispatch_batch_review(
    *,
    runtime: Mapping[str, ReviewRuntimeUnit],
    store: DirectoryReviewReceiptStore,
    invoke: Callable[[str, str, Mapping[str, object]], PhysicalProviderResponse],
    max_workers: int = 4,
) -> BatchReviewDispatchResult:
    """Run calibration first, then the remaining units once with bounded concurrency."""

    if list(runtime) != [
        "calibration",
        "calibration-fact",
        *sorted(unit_id for unit_id in runtime if unit_id.startswith("fact:")),
        *sorted(unit_id for unit_id in runtime if unit_id.startswith("enjoy:")),
    ] or len(runtime) != 18:
        raise ValueError("review runtime is not the sealed deterministic 18-unit order")
    if not 1 <= max_workers <= 16:
        raise ValueError("review worker count must be between one and sixteen")

    receipts: dict[str, dict[str, object]] = {}
    parsed: dict[str, object] = {}
    errors: dict[str, str] = {}
    for cal_id in ("calibration", "calibration-fact"):
        cal_receipt, cal_result, cal_error = _run_review_unit(
            unit=runtime[cal_id],
            store=store,
            invoke=invoke,
        )
        if cal_receipt is not None:
            receipts[cal_id] = cal_receipt
        if cal_result is not None:
            parsed[cal_id] = cal_result
        if cal_error is not None:
            errors[cal_id] = cal_error
            return BatchReviewDispatchResult(
                evaluation_status="INFRA_ERROR",
                response_receipts=receipts,
                parsed_results=parsed,
                infrastructure_errors=errors,
            )

    canary = runtime[FACT_CANARY_UNIT_ID]
    canary_receipt, canary_result, canary_error = _run_review_unit(
        unit=canary, store=store, invoke=invoke
    )
    if canary_receipt is not None:
        receipts[canary.unit_id] = canary_receipt
    if canary_result is not None:
        parsed[canary.unit_id] = canary_result
    if canary_error is not None:
        errors[canary.unit_id] = canary_error
        return BatchReviewDispatchResult(
            evaluation_status="INFRA_ERROR",
            response_receipts=receipts,
            parsed_results=parsed,
            infrastructure_errors=errors,
        )

    def run_stage(units: list[ReviewRuntimeUnit]) -> None:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            unit_by_future = {
                pool.submit(_run_review_unit, unit=unit, store=store, invoke=invoke): unit
                for unit in units
            }
            for future in as_completed(unit_by_future):
                unit = unit_by_future[future]
                receipt, result, error = future.result()
                if receipt is not None:
                    receipts[unit.unit_id] = receipt
                if result is not None:
                    parsed[unit.unit_id] = result
                if error is not None:
                    errors[unit.unit_id] = error

    run_stage([
        unit
        for unit_id, unit in runtime.items()
        if unit_id.startswith("fact:") and unit_id != FACT_CANARY_UNIT_ID
    ])
    run_stage([unit for unit_id, unit in runtime.items() if unit_id.startswith("enjoy:")])
    status: Literal["COMPLETE", "PARTIAL", "INFRA_ERROR"]
    if not errors:
        status = "COMPLETE"
    elif parsed:
        status = "PARTIAL"
    else:
        status = "INFRA_ERROR"
    return BatchReviewDispatchResult(
        evaluation_status=status,
        response_receipts=receipts,
        parsed_results=parsed,
        infrastructure_errors=errors,
    )


def build_batch_review_plan(
    *,
    inputs: BatchReviewInputs,
    material: BatchReviewMaterial,
    calibration_inputs: QualityCalibrationInputs,
    archival_enjoyment: dict[str, EnjoymentItem] | None = None,
) -> BatchReviewPlan:
    """Build and seal the deterministic provider-free 18-unit review plan."""

    case_ids = sorted(tour.case_id for tour in inputs.tours)
    if set(material.fact_inputs) != set(case_ids) or set(material.enjoyment_items) != set(
        case_ids
    ):
        raise ValueError("review material does not exactly match the sealed tours")

    runtime = build_batch_review_runtime(
        material=material,
        calibration_inputs=calibration_inputs,
        archival_enjoyment=archival_enjoyment,
    )
    units = [
        _review_plan_unit(
            unit_id=unit.unit_id,
            purpose=unit.purpose,
            case_id=(
                None
                if unit.unit_id.startswith("calibration")
                else unit.unit_id.split(":", 1)[1]
            ),
            envelope=unit.envelope,
            sdk_request=unit.sdk_request,
        )
        for unit in runtime.values()
    ]

    tours_by_id = {tour.case_id: tour for tour in inputs.tours}
    core = {
        "schema_version": "ondoway-tour-batch-review-plan-v1",
        "authoring_batch_plan_sha256": inputs.batch_plan_sha256,
        "authoring_request_sha256s": {
            case_id: [unit["request_sha256"] for unit in tours_by_id[case_id].tour_plan["units"]]
            for case_id in case_ids
        },
        "provider_receipt_sha256s": {
            case_id: [receipt["receipt_sha256"] for receipt in tours_by_id[case_id].receipts]
            for case_id in case_ids
        },
        "tour_artifact_sha256s": {
            case_id: tours_by_id[case_id].tour_artifact["artifact_sha256"]
            for case_id in case_ids
        },
        "calibration_manifest_sha256": calibration_inputs.calibration_manifest_sha256,
        "reference_manifest_sha256": calibration_inputs.reference_manifest_sha256,
        "calibration_bundle_sha256": calibration_inputs.bundle_sha256,
        "quality_policy_sha256": QUALITY_POLICY_SHA256,
        "prompt_sha256s": {
            "calibration": CALIBRATION_PROMPT_SHA256,
            "fact": BATCH_FACT_PROMPT_SHA256,
            "enjoy": ENJOY_PROMPT_SHA256,
        },
        "model": {"enjoy": COMPOSE_MODEL, "fact": FACT_REVIEW_MODEL},
        "thinking": {"type": "adaptive"},
        "sdk_max_retries": 0,
        "application_deadline_seconds": None,
        "units": units,
    }
    return {
        **core,
        "review_plan_sha256": _sha256(canonical_bytes(core)),
    }


def gate_batch_review_execution(
    *,
    inputs: BatchReviewInputs,
    material: BatchReviewMaterial,
    review_plan: BatchReviewPlan,
    live: bool,
    approved_review_plan_sha256: str | None,
    client_factory: Callable[[], object],
) -> BatchReviewPlan:
    """Fail closed before client construction; this gate never dispatches a call."""

    case_ids = sorted(tour.case_id for tour in inputs.tours)
    if len(case_ids) != 8 or len(set(case_ids)) != 8:
        raise ValueError("review inputs do not contain exactly eight unique tours")
    if set(material.fact_inputs) != set(case_ids) or set(material.enjoyment_items) != set(
        case_ids
    ):
        raise ValueError("review material differs from the sealed inputs")
    tours_by_id = {tour.case_id: tour for tour in inputs.tours}
    if review_plan.get("authoring_batch_plan_sha256") != inputs.batch_plan_sha256:
        raise ValueError("review plan differs from the authoring batch")
    if review_plan.get("authoring_request_sha256s") != {
        case_id: [unit["request_sha256"] for unit in tours_by_id[case_id].tour_plan["units"]]
        for case_id in case_ids
    }:
        raise ValueError("review plan differs from the authoring requests")
    if review_plan.get("provider_receipt_sha256s") != {
        case_id: [receipt["receipt_sha256"] for receipt in tours_by_id[case_id].receipts]
        for case_id in case_ids
    }:
        raise ValueError("review plan differs from the provider receipts")
    if review_plan.get("tour_artifact_sha256s") != {
        case_id: tours_by_id[case_id].tour_artifact["artifact_sha256"]
        for case_id in case_ids
    }:
        raise ValueError("review plan differs from the provider tour artifacts")
    units = review_plan.get("units")
    if (
        not isinstance(units, list)
        or len(units) != 18
        or [unit.get("purpose") for unit in units]
        != [
            "calibration",
            "calibration",
            *("fact_review" for _ in range(8)),
            *("enjoy_review" for _ in range(8)),
        ]
        or review_plan.get("thinking") != {"type": "adaptive"}
        or review_plan.get("sdk_max_retries") != 0
        or review_plan.get("application_deadline_seconds") is not None
    ):
        raise ValueError("review plan execution policy is invalid")
    review_core = {
        key: value for key, value in review_plan.items() if key != "review_plan_sha256"
    }
    sealed_hash = review_plan.get("review_plan_sha256")
    if not isinstance(sealed_hash, str) or sealed_hash != _sha256(canonical_bytes(review_core)):
        raise ValueError("review plan self-hash is invalid")

    if not live:
        return review_plan
    if approved_review_plan_sha256 != sealed_hash:
        raise ValueError("approved review-plan hash must exactly match the sealed plan")
    if os.environ.get(LIVE_REVIEW_APPROVAL_ENV) != "1":
        raise ValueError(f"live review requires {LIVE_REVIEW_APPROVAL_ENV}=1")
    client_factory()
    return review_plan


def _load_authoring_documents(
    *,
    batch_root: Path,
    batch_plan: dict[str, object],
) -> tuple[dict[str, dict[str, object]], dict[tuple[str, int], dict[str, object]]]:
    tour_artifacts: dict[str, dict[str, object]] = {}
    stop_receipts: dict[tuple[str, int], dict[str, object]] = {}
    for tour_plan in batch_plan["tours"]:
        case_id = tour_plan["case_id"]
        city_slug = tour_plan["tour_input"]["city_slug"]
        case_root = batch_root / city_slug / case_id
        tour_artifacts[case_id] = json.loads(
            (case_root / "tour.json").read_text(encoding="utf-8")
        )
        for unit in tour_plan["units"]:
            stop_index = unit["stop_index"]
            stop_receipts[(case_id, stop_index)] = json.loads(
                (case_root / f"stop-{stop_index}.json").read_text(encoding="utf-8")
            )
    return tour_artifacts, stop_receipts


def load_archival_enjoyment_items(batch_root: Path) -> dict[str, EnjoymentItem]:
    stored_plan = json.loads((batch_root / "plan.json").read_text(encoding="utf-8"))
    _, stop_receipts = _load_authoring_documents(
        batch_root=batch_root,
        batch_plan=stored_plan,
    )
    items: dict[str, EnjoymentItem] = {}
    for tour_plan in stored_plan["tours"]:
        case_id = tour_plan["case_id"]
        sentences_by_stop: dict[int, tuple[Sentence, ...]] = {}
        for unit in tour_plan["units"]:
            stop_index = unit["stop_index"]
            receipt = stop_receipts[(case_id, stop_index)]
            raw_sentences = json.loads(receipt["raw_response"])["sentences"]
            sentences_by_stop[stop_index] = tuple(
                Sentence.model_validate(s) for s in raw_sentences
            )
        items[case_id] = build_enjoyment_item(
            sentences_by_stop,
            candidate_narration_sha256(sentences_by_stop),
        )
    return items


def build_provider_free_review_context(
    *,
    batch_root: Path = DEFAULT_OUTPUT_ROOT,
    archival_root: Path | None = None,
) -> ProviderFreeReviewContext:
    """Validate all frozen inputs and expose the exact provider-free plan/runtime."""

    if archival_root is not None and archival_root.resolve() == batch_root.resolve():
        raise ValueError(
            "archival-root and batch-root resolve to the same directory; "
            "a blind comparison requires two distinct batches"
        )

    stored_plan = json.loads((batch_root / "plan.json").read_text(encoding="utf-8"))
    manifest = load_frozen_tour_batch(MANIFEST_PATH)
    driver = create_driver()
    try:
        with RoutingClient() as routing_client:
            current_plan, runtime = _batch_plan(
                manifest,
                driver=driver,
                routing_client=routing_client,
            )
    finally:
        driver.close()
    if stored_plan != current_plan:
        raise ValueError("stored provider batch differs from its reconstructed authoring plan")

    tour_artifacts, stop_receipts = _load_authoring_documents(
        batch_root=batch_root,
        batch_plan=stored_plan,
    )
    inputs = load_batch_review_inputs(
        batch_plan=stored_plan,
        tour_artifacts=tour_artifacts,
        stop_receipts=stop_receipts,
    )
    authoring_requests = _review_authoring_requests(inputs=inputs, runtime=runtime)
    material = build_batch_review_material(
        inputs=inputs,
        authoring_requests=authoring_requests,
    )
    cert_fixtures = ROOT / "fixtures" / "tour-certification"
    calibration_inputs = load_quality_calibration_inputs(
        repo_root=ROOT,
        calibration_manifest_path=cert_fixtures / "calibration-manifest.json",
        reference_manifest_path=cert_fixtures / "investigation-reference-manifest.json",
    )
    archival_enjoyment: dict[str, EnjoymentItem] | None = None
    if archival_root is not None:
        archival_enjoyment = load_archival_enjoyment_items(archival_root)
    review_plan = build_batch_review_plan(
        inputs=inputs,
        material=material,
        calibration_inputs=calibration_inputs,
        archival_enjoyment=archival_enjoyment,
    )
    review_runtime = build_batch_review_runtime(
        material=material,
        calibration_inputs=calibration_inputs,
        archival_enjoyment=archival_enjoyment,
    )
    return ProviderFreeReviewContext(
        inputs=inputs,
        material=material,
        calibration_inputs=calibration_inputs,
        review_plan=review_plan,
        runtime=review_runtime,
    )


def build_provider_free_review_plan(
    *,
    batch_root: Path = DEFAULT_OUTPUT_ROOT,
    archival_root: Path | None = None,
) -> BatchReviewPlan:
    """Compatibility wrapper returning only the provider-free sealed plan."""

    return build_provider_free_review_context(
        batch_root=batch_root, archival_root=archival_root
    ).review_plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--approve-review-plan-sha256")
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--review-root", type=Path, default=DEFAULT_REVIEW_ROOT)
    parser.add_argument("--calibration-reuse-root", type=Path, default=LEGACY_REVIEW_ROOT)
    parser.add_argument("--archival-root", type=Path, default=None)
    args = parser.parse_args(argv)
    context = build_provider_free_review_context(
        batch_root=args.batch_root,
        archival_root=args.archival_root,
    )
    if not args.live:
        print(canonical_bytes(context.review_plan).decode("utf-8"))
        return 0

    holder: dict[str, AnthropicCertificationProvider] = {}

    def client_factory() -> AnthropicCertificationProvider:
        from src.tour.anthropic_client import certification_batch_compose_client

        client = certification_batch_compose_client()
        provider = AnthropicCertificationProvider(
            compose_client=client,
            judge_client=client,
        )
        holder["provider"] = provider
        return provider

    gate_batch_review_execution(
        inputs=context.inputs,
        material=context.material,
        review_plan=context.review_plan,
        live=True,
        approved_review_plan_sha256=args.approve_review_plan_sha256,
        client_factory=client_factory,
    )
    store = DirectoryReviewReceiptStore(args.review_root)
    plan_path = args.review_root / "review-plan.json"
    if plan_path.exists():
        if json.loads(plan_path.read_text(encoding="utf-8")) != context.review_plan:
            raise ValueError("durable review plan differs from the approved plan")
    else:
        _private_write_new(plan_path, context.review_plan)
    if args.calibration_reuse_root.exists():
        reuse_source = DirectoryReviewReceiptStore(args.calibration_reuse_root)
        for cal_id in ("calibration", "calibration-fact"):
            seed_exact_calibration_receipt(
                unit=context.runtime[cal_id],
                source=reuse_source,
                target=store,
            )
    provider = holder["provider"]
    result = dispatch_batch_review(
        runtime=context.runtime,
        store=store,
        invoke=lambda purpose, unit_id, request: provider.invoke(
            purpose=CallPurpose(purpose),
            unit_id=unit_id,
            request=request,
        ),
        max_workers=args.max_workers,
    )
    summary = {
        "review_plan_sha256": context.review_plan["review_plan_sha256"],
        "evaluation_status": result.evaluation_status,
        "completed_unit_count": len(result.response_receipts),
        "parsed_unit_count": len(result.parsed_results),
        "provider_request_ids": {
            unit_id: receipt["provider_request_id"]
            for unit_id, receipt in sorted(result.response_receipts.items())
        },
        "infrastructure_error_unit_ids": sorted(result.infrastructure_errors),
    }
    print(canonical_bytes(summary).decode("utf-8"))
    return 0


__all__ = [
    "LIVE_REVIEW_APPROVAL_ENV",
    "BatchReviewDispatchResult",
    "BatchReviewInputs",
    "BatchReviewMaterial",
    "BatchReviewPlan",
    "BatchReviewPlanUnit",
    "BatchReviewTourInputs",
    "DirectoryReviewReceiptStore",
    "ProviderFreeReviewContext",
    "ReviewRuntimeUnit",
    "build_batch_review_material",
    "build_batch_review_plan",
    "build_batch_review_runtime",
    "build_provider_free_review_context",
    "build_provider_free_review_plan",
    "dispatch_batch_review",
    "gate_batch_review_execution",
    "load_archival_enjoyment_items",
    "load_batch_review_inputs",
    "main",
    "seed_exact_calibration_receipt",
]


if __name__ == "__main__":
    raise SystemExit(main())
