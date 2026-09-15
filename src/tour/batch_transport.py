"""Batch API transport for the durable certification workflow.

Wraps the Anthropic Message Batches endpoint for 50% cost reduction on
certification compose and review calls. The synchronous certification
provider (certification_provider.py) remains the authority for the
receipt shape and response parsing; this module handles only submission,
polling, and result mapping.

Never edit tour_batch_candidate.py — its own SHA is sealed into plans.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .certification_provider import PhysicalProviderResponse, _response_text, _usage


@dataclass(frozen=True)
class BatchUnitResult:
    """One unit's outcome from a batch, before mapping into the certification receipt."""
    custom_id: str
    result_type: str  # "succeeded" | "errored" | "canceled" | "expired"
    response: PhysicalProviderResponse | None  # None for non-succeeded
    batch_id: str
    error_message: str | None = None


def batch_client(*, max_retries: int = 0) -> Any:
    """Every client here comes from the bounded factory, never built bare.

    max_retries=0 is a SUBMISSION client (a hidden SDK retry could double-submit
    the batch = double-spend); any other value asks for the READ client, whose
    idempotent polls keep bounded transient-failure retries.
    """
    from .anthropic_client import (
        certification_batch_compose_client,
        certification_batch_read_client,
    )

    if max_retries:
        return certification_batch_read_client()
    return certification_batch_compose_client()


#: The Batch API's own custom_id contract. Enforced HERE so a bad id is a $0
#: local ValueError, never a 400 after the requests are built — the first live
#: submission was refused for a colon a stub client happily accepted.
_CUSTOM_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def submit_batch(
    requests: list[tuple[str, Mapping[str, object]]],
    *,
    client: object | None = None,
) -> Any:
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    for custom_id, _sdk_request in requests:
        if not _CUSTOM_ID_PATTERN.fullmatch(custom_id):
            raise ValueError(
                f"custom_id {custom_id!r} violates the Batch API pattern "
                "^[a-zA-Z0-9_-]{1,64}$"
            )
    if client is None:
        client = batch_client()
    batch_requests = [
        Request(
            custom_id=custom_id,
            params=MessageCreateParamsNonStreaming(**dict(sdk_request)),
        )
        for custom_id, sdk_request in requests
    ]
    return client.messages.batches.create(requests=batch_requests)


#: SDK errors a status check may be retried past: the request never got an
#: answer, or the service said try again. Named, not imported, because the
#: SDK is never an import-time dependency of this package.
_TRANSIENT_ERROR_NAMES = (
    "APIConnectionError",  # includes APITimeoutError
    "RateLimitError",
    "InternalServerError",
    "ServiceUnavailableError",
    "OverloadedError",
    "DeadlineExceededError",
)


def _is_transient(exc: Exception) -> bool:
    try:
        import anthropic
    except ImportError:
        return False
    classes = tuple(
        cls for cls in (getattr(anthropic, name, None) for name in _TRANSIENT_ERROR_NAMES)
        if isinstance(cls, type)
    )
    return bool(classes) and isinstance(exc, classes)


def _is_not_found(exc: Exception) -> bool:
    try:
        import anthropic
    except ImportError:
        return False
    cls = getattr(anthropic, "NotFoundError", None)
    return isinstance(cls, type) and isinstance(exc, cls)


def poll_batch(
    batch_id: str,
    *,
    client: object | None = None,
    poll_interval_s: float = 10,
    max_poll_s: float = 3600,
    on_poll: Callable[[Any, float], None] | None = None,
    max_consecutive_retrieve_errors: int = 0,
    on_poll_error: Callable[[Exception, int], None] | None = None,
    retrieve_error_backoff_s: float | None = None,
    not_found_grace_s: float = 0,
) -> Any:
    """Poll until the batch has ended. `on_poll(batch, elapsed_s)` is called
    on every poll, the final one included, so a caller can print a
    heartbeat: a batch phase otherwise sits silent for minutes.

    A caller may ride out up to `max_consecutive_retrieve_errors` transient
    status-check failures in a row (connection, timeout, 5xx, 429 — see
    `_is_transient`): each is reported to `on_poll_error(exc, consecutive)`
    and the poll goes on; a successful check resets the count. A non-transient
    error, one past the limit, or one past the deadline raises. The default
    (0) raises the first error, as every caller did before.

    With `retrieve_error_backoff_s` the wait after the nth consecutive error is
    backoff * 2**(n-1), so the error budget spans an outage rather than a
    minute of fast failures; otherwise errors wait `poll_interval_s`.

    A 404 in the first `not_found_grace_s` seconds counts as a tolerated error
    too: the Batch API can answer not_found for a batch created a moment ago
    (slice 9 job 2, 169 ms after submission). After the window it raises."""
    if client is None:
        client = batch_client(max_retries=2)
    started = time.monotonic()
    deadline = started + max_poll_s
    consecutive_errors = 0
    while True:
        try:
            batch = client.messages.batches.retrieve(batch_id)
        except Exception as exc:
            consecutive_errors += 1
            just_submitted = time.monotonic() - started < not_found_grace_s
            if (
                not (_is_transient(exc) or (just_submitted and _is_not_found(exc)))
                or consecutive_errors > max_consecutive_retrieve_errors
                or time.monotonic() >= deadline
            ):
                raise
            if on_poll_error is not None:
                try:
                    on_poll_error(exc, consecutive_errors)
                except Exception as report_exc:  # observability must never end a billed batch
                    print(
                        f"on_poll_error warning: {type(report_exc).__name__}: {report_exc} "
                        f"(batch {batch_id})"
                    )
            time.sleep(
                retrieve_error_backoff_s * 2 ** (consecutive_errors - 1)
                if retrieve_error_backoff_s
                else poll_interval_s
            )
            continue
        consecutive_errors = 0
        if on_poll is not None:
            try:
                on_poll(batch, time.monotonic() - started)
            except Exception as exc:  # observability must never end a billed batch
                print(f"on_poll warning: {type(exc).__name__}: {exc} (batch {batch_id})")
        if batch.processing_status == "ended":
            return batch
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"batch {batch_id} did not reach 'ended' within {max_poll_s}s; "
                f"last processing_status was {batch.processing_status!r}"
            )
        time.sleep(poll_interval_s)


def collect_results(
    batch_id: str,
    *,
    client: object | None = None,
) -> dict[str, BatchUnitResult]:
    if client is None:
        client = batch_client(max_retries=2)
    collected: dict[str, BatchUnitResult] = {}
    for individual in client.messages.batches.results(batch_id):
        outcome = individual.result
        if outcome.type == "succeeded":
            message = outcome.message
            text = _response_text(message)
            input_tokens, output_tokens, cache_creation, cache_read = _usage(message)
            response = PhysicalProviderResponse(
                body=text.encode("utf-8"),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=0,
                model=message.model,
                provider_request_id=message.id,
                stop_reason=message.stop_reason,
                cache_creation_input_tokens=cache_creation,
                cache_read_input_tokens=cache_read,
            )
            collected[individual.custom_id] = BatchUnitResult(
                custom_id=individual.custom_id,
                result_type=outcome.type,
                response=response,
                batch_id=batch_id,
                error_message=None,
            )
            continue
        error_message: str | None = None
        if outcome.type == "errored":
            error_message = f"{outcome.error.error.type}: {outcome.error.error.message}"
        collected[individual.custom_id] = BatchUnitResult(
            custom_id=individual.custom_id,
            result_type=outcome.type,
            response=None,
            batch_id=batch_id,
            error_message=error_message,
        )
    return collected


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def build_batch_receipt(
    *,
    unit_result: BatchUnitResult,
    request_id: str,
    request_sha256: str,
    response_sha256: str,
    parsed_payload_sha256: str,
    poi_name: str,
    stop_index: int,
    raw_response: str,
) -> dict[str, object]:
    response = unit_result.response
    if response is None:
        raise ValueError("batch receipt requires a physical provider response")
    core = {
        "schema_version": "ondoway-text-candidate-stop-v2",
        "stop_index": stop_index,
        "poi_name": poi_name,
        "request_id": request_id,
        "request_sha256": request_sha256,
        "response_sha256": response_sha256,
        "parsed_payload_sha256": parsed_payload_sha256,
        "provider_request_id": response.provider_request_id,
        "model": response.model,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "raw_response": raw_response,
        "batch_id": unit_result.batch_id,
        "result_type": unit_result.result_type,
    }
    return {**core, "receipt_sha256": hashlib.sha256(_canonical_bytes(core)).hexdigest()}


def validate_receipt(receipt: dict[str, object]) -> None:
    schema_version = receipt.get("schema_version")
    if schema_version not in {
        "ondoway-text-candidate-stop-v1",
        "ondoway-text-candidate-stop-v2",
    }:
        raise ValueError(f"unsupported receipt schema_version: {schema_version!r}")
    core = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    if receipt.get("receipt_sha256") != hashlib.sha256(_canonical_bytes(core)).hexdigest():
        raise ValueError("receipt_sha256 does not match the canonical hash of its core fields")
    if schema_version == "ondoway-text-candidate-stop-v1":
        latency_ms = receipt.get("latency_ms")
        if not isinstance(latency_ms, int) or latency_ms < 0:
            raise ValueError("v1 receipt latency_ms must be a non-negative int")
        return
    batch_id = receipt.get("batch_id")
    if not isinstance(batch_id, str) or not batch_id:
        raise ValueError("v2 receipt batch_id must be a non-empty string")
    if receipt.get("result_type") not in {"succeeded", "errored", "canceled", "expired"}:
        raise ValueError("v2 receipt result_type must be one of the known batch outcomes")
    if "latency_ms" in receipt:
        raise ValueError("v2 receipt must not include latency_ms")


def persist_batch_submission(
    path: Path,
    *,
    batch_id: str,
    custom_ids: list[str],
    plan_sha256: str,
) -> None:
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    core = {
        "schema_version": "ondoway-batch-submission-v1",
        "batch_id": batch_id,
        "custom_ids": custom_ids,
        "plan_sha256": plan_sha256,
        "submitted_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }
    data = _canonical_bytes(core)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(16)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("submission write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.rename(temporary, path)


def load_batch_submission(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    submission = json.loads(path.read_text(encoding="utf-8"))
    schema_version = submission.get("schema_version")
    if schema_version != "ondoway-batch-submission-v1":
        raise ValueError(f"unsupported submission schema_version: {schema_version!r}")
    return submission


def execute_batch_pipeline(
    requests: list[tuple[str, Mapping[str, object]]],
    *,
    submission_path: Path,
    plan_sha256: str,
    client: object | None = None,
    poll_interval_s: float = 10,
    max_poll_s: float = 3600,
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, BatchUnitResult]:
    if client is None:
        client = batch_client()
    submission = load_batch_submission(submission_path)
    if submission is not None:
        batch_id = submission["batch_id"]
        if on_progress is not None:
            on_progress(f"resuming batch {batch_id}")
    else:
        if on_progress is not None:
            on_progress(f"submitting {len(requests)} requests")
        batch = submit_batch(requests, client=client)
        persist_batch_submission(
            submission_path,
            batch_id=batch.id,
            custom_ids=[custom_id for custom_id, _ in requests],
            plan_sha256=plan_sha256,
        )
        batch_id = batch.id
    if on_progress is not None:
        on_progress("polling...")
    poll_batch(batch_id, client=client, poll_interval_s=poll_interval_s, max_poll_s=max_poll_s)
    if on_progress is not None:
        on_progress("collecting results")
    return collect_results(batch_id, client=client)


__all__ = [
    "BatchUnitResult",
    "batch_client",
    "build_batch_receipt",
    "collect_results",
    "execute_batch_pipeline",
    "load_batch_submission",
    "persist_batch_submission",
    "poll_batch",
    "submit_batch",
    "validate_receipt",
]
