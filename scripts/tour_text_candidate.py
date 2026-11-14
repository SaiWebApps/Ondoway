"""Bounded full-tour Anthropic authoring for text evaluation only.

This lane produces and preserves exact provider-authored text. It does not bake
audio, publish a certified product response, score the internal source scaffold,
or substitute fallback narration after a failed call.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

from src.connection import create_driver
from src.tour.anthropic_client import certification_compose_client
from src.tour.artifact import sentences_payload_sha256
from src.tour.authoring import (
    CERTIFICATION_COMPOSE_MAX_OUTPUT_TOKENS,
    COMPOSE_MODEL,
    _certification_compose_requests,
    _sentences_from_json,
    candidate_compose_request_envelope,
    compose_input_sha256,
)
from src.tour.candidate_authoring import (
    AuthoringCandidateIdentity,
    AuthoringCandidatePlan,
    AuthoringStopRequest,
)
from src.tour.certification_provider import AnthropicCertificationProvider, CallPurpose
from src.tour.contract import BeatSequence, TourInput
from src.tour.corpus_places import canonical_bytes
from src.tour.generation import generate
from src.tour.premium_authorities import PREMIUM_AUTHORITIES
from src.tour.premium_tour import premium_authoring_policy_sha256
from src.tour.routing import summarise_route
from src.tour.routing_client import RoutingClient
from src.tour.selection import build_poi_beat_plans_capped, load_paris_corpus

ROOT = Path(__file__).resolve().parents[1]
#: The frozen route this lane replays. Under `fixtures/`, beside the two
#: certification manifests, because these are inputs the code OPENS — not a
#: planning document. `specs/` was deleted 2026-09-02 and is refused by the junk
#: guard; a fixture that lived there was one `rm -rf` from breaking this script.
SPEC = ROOT / "fixtures" / "tour-certification"
LIVE_APPROVAL_ENV = "ONDOWAY_TEXT_CANDIDATE_APPROVED"
INPUT_USD_PER_MILLION_TOKENS = 5
OUTPUT_USD_PER_MILLION_TOKENS = 25
ABSOLUTE_CALL_DEADLINE_SECONDS = 180


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _private_write_new(path: Path, value: object) -> None:
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise ValueError("receipt parent must be an existing ordinary directory")
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(16)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        data = canonical_bytes(value)
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("receipt write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        with suppress(FileNotFoundError):
            temporary.unlink()
        raise
    os.close(descriptor)
    try:
        os.link(temporary, path)
    finally:
        temporary.unlink()
    parent_descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(parent_descriptor)
    finally:
        os.close(parent_descriptor)


def _load_route_and_source() -> tuple[TourInput, object, BeatSequence, object]:
    summary = json.loads((SPEC / "live-route-summary.json").read_text(encoding="utf-8"))
    tour_input = TourInput.model_validate_json(
        (SPEC / "live-tour-request.json").read_text(encoding="utf-8")
    )
    driver = create_driver()
    try:
        snapshot = load_paris_corpus(driver, city_slug=tour_input.city_slug)
    finally:
        driver.close()
    by_id = {poi.id: poi for poi in snapshot.pois}
    ordered = tuple(by_id[item["id"]] for item in summary["pois"])
    if tuple(poi.name for poi in ordered) != tuple(item["name"] for item in summary["pois"]):
        raise ValueError("database POI identities differ from the accepted route")
    with RoutingClient() as routing:
        route = summarise_route(
            ordered,
            start_lat=tour_input.start[0],
            start_lng=tour_input.start[1],
            round_trip=tour_input.round_trip,
            duration_min=tour_input.duration_min,
            spine_area=None,
            routing_client=routing,
        )
    if not route.routed or len(route.transits) != len(summary["legs"]):
        raise ValueError("accepted route did not reconstruct through Valhalla")
    for transit, expected in zip(route.transits, summary["legs"], strict=True):
        receipt = transit.valhalla_receipt
        if (
            receipt is None
            or receipt.request_sha256 != expected["request_sha256"]
            or receipt.response_sha256 != expected["response_sha256"]
            or transit.leg_seconds != expected["seconds"]
            or transit.leg_distance_m != expected["distance_m"]
        ):
            raise ValueError("Valhalla replay differs from the accepted route receipt")
    capped = build_poi_beat_plans_capped(
        route,
        snapshot,
        lenses=tour_input.lenses,
        end_is_none=True,
    )
    sequence = BeatSequence(
        poi_beats=tuple(plan for plan, _overflow in capped),
        overflow_by_poi={plan.poi_id: overflow for plan, overflow in capped if overflow},
    )
    source = generate(
        sequence,
        route,
        tour_input,
        now=dt.datetime(2026, 7, 21, tzinfo=dt.UTC),
        validate_output=False,
    )
    return tour_input, route, sequence, source


def _file_sha256(path: Path) -> str:
    return _sha256(path.read_bytes())


def _authoring_policy_sha256() -> str:
    """Compatibility name for the shared packaged authoring-policy hash."""

    return premium_authoring_policy_sha256()


def _plan(slot: Literal["A", "B", "C"]) -> tuple[dict[str, object], dict[int, object]]:
    _tour_input, route, sequence, source = _load_route_and_source()
    _beats, stops, requests = _certification_compose_requests(source, sequence, route)
    candidate = AuthoringCandidateIdentity.create(
        candidate_slot=slot,
        contract_sha256=PREMIUM_AUTHORITIES.contract_sha256,
        reference_manifest_sha256=PREMIUM_AUTHORITIES.reference_manifest_sha256,
        calibration_manifest_sha256=PREMIUM_AUTHORITIES.calibration_manifest_sha256,
        grounded_source_sha256=sentences_payload_sha256(source.script),
        route_sha256=_sha256(canonical_bytes(route.model_dump(mode="json"))),
        authoring_policy_sha256=_authoring_policy_sha256(),
    )
    authoring = AuthoringCandidatePlan(
        candidate=candidate,
        stop_requests=tuple(
            AuthoringStopRequest.create(
                candidate=candidate,
                stop_index=stop_index,
                compose_input_sha256=compose_input_sha256(requests[stop_index]),
            )
            for stop_index in stops
        ),
    )
    units: list[dict[str, object]] = []
    for stop_request in authoring.stop_requests:
        envelope, sdk_request = candidate_compose_request_envelope(
            requests[stop_request.stop_index],
            stop_request,
            model=COMPOSE_MODEL,
        )
        units.append(
            {
                "stop_index": stop_request.stop_index,
                "poi_name": route.pois[stop_request.stop_index].name,
                "request_id": stop_request.request_id,
                "request_sha256": _sha256(envelope.encode("utf-8")),
                "input_byte_count": len(envelope.encode("utf-8")),
                "output_token_ceiling": CERTIFICATION_COMPOSE_MAX_OUTPUT_TOKENS,
                "sdk_request": sdk_request,
            }
        )
    public_units = [
        {key: value for key, value in unit.items() if key != "sdk_request"} for unit in units
    ]
    plan_core = {
        "schema_version": "ondoway-text-candidate-plan-v1",
        "candidate": candidate.model_dump(mode="json"),
        "model": COMPOSE_MODEL,
        "thinking": {"type": "adaptive"},
        "sdk_max_retries": 0,
        "absolute_call_deadline_seconds": ABSOLUTE_CALL_DEADLINE_SECONDS,
        "runner_sha256": _file_sha256(Path(__file__)),
        "provider_boundary_sha256": _file_sha256(
            ROOT / "src" / "tour" / "certification_provider.py"
        ),
        "units": public_units,
    }
    plan = {**plan_core, "plan_sha256": _sha256(canonical_bytes(plan_core))}
    return plan, {
        unit["stop_index"]: {
            "unit": unit,
            "request": requests[unit["stop_index"]],
            "authoring": authoring.stop_requests[unit["stop_index"]],
        }
        for unit in units
    }


class _AbsoluteCallScope:
    def __init__(self, deadline_seconds: float = ABSOLUTE_CALL_DEADLINE_SECONDS) -> None:
        if deadline_seconds <= 0:
            raise ValueError("call deadline must be positive")
        self._deadline_seconds = deadline_seconds
        self._started = time.monotonic()
        self._lock = threading.Lock()
        self._stream: object | None = None
        self._expired = False
        self._finished = False
        timer = threading.Timer(deadline_seconds, self._expire)
        timer.daemon = True
        self._timer = timer
        timer.start()

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._expired

    @property
    def expired(self) -> bool:
        with self._lock:
            return self._expired

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self._started

    def _expire(self) -> None:
        with self._lock:
            if self._finished:
                return
            self._expired = True
            stream = self._stream
        if stream is not None:
            close = getattr(stream, "close", None)
            if callable(close):
                with suppress(Exception):
                    close()

    def register(self, stream: object) -> None:
        with self._lock:
            if self._stream is not None:
                raise RuntimeError("call scope already owns a physical stream")
            self._stream = stream
            expired = self._expired
        if expired:
            close = getattr(stream, "close", None)
            if callable(close):
                close()

    def finish(self) -> None:
        with self._lock:
            self._finished = True
            self._stream = None
        self._timer.cancel()


def _live_unit(
    client: object,
    item: dict[str, object],
    *,
    absolute_deadline_seconds: float | None = ABSOLUTE_CALL_DEADLINE_SECONDS,
) -> dict[str, object]:
    scope = (
        _AbsoluteCallScope(absolute_deadline_seconds)
        if absolute_deadline_seconds is not None
        else None
    )
    provider = AnthropicCertificationProvider(
        compose_client=client,
        judge_client=client,
        cancellation_scope=scope,
    )
    unit = item["unit"]
    try:
        response = provider.invoke(
            purpose=CallPurpose.COMPOSE,
            unit_id=f"stop:{unit['stop_index']}",
            request=unit["sdk_request"],
        )
        if scope is not None and (
            scope.expired or scope.elapsed_seconds > absolute_deadline_seconds
        ):
            raise TimeoutError("physical provider call exceeded its absolute deadline")
    finally:
        if scope is not None:
            scope.finish()
    request = item["request"]
    try:
        payload = json.loads(response.body)
        sentences = _sentences_from_json(payload["sentences"], request)
    except Exception as exc:
        core = {
            "schema_version": "ondoway-text-candidate-provider-payload-failure-v1",
            "stop_index": unit["stop_index"],
            "poi_name": unit["poi_name"],
            "request_id": unit["request_id"],
            "request_sha256": unit["request_sha256"],
            "response_sha256": _sha256(response.body),
            "provider_request_id": response.provider_request_id,
            "model": response.model,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "latency_ms": response.latency_ms,
            "raw_response": response.body.decode("utf-8"),
            "failure_type": type(exc).__name__,
            "failure_message_sha256": _sha256(str(exc).encode("utf-8")),
        }
        return {**core, "receipt_sha256": _sha256(canonical_bytes(core))}
    core = {
        "schema_version": "ondoway-text-candidate-stop-v1",
        "stop_index": unit["stop_index"],
        "poi_name": unit["poi_name"],
        "request_id": unit["request_id"],
        "request_sha256": unit["request_sha256"],
        "response_sha256": _sha256(response.body),
        "parsed_payload_sha256": sentences_payload_sha256(sentences),
        "provider_request_id": response.provider_request_id,
        "model": response.model,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "latency_ms": response.latency_ms,
        "raw_response": response.body.decode("utf-8"),
    }
    return {**core, "receipt_sha256": _sha256(canonical_bytes(core))}


def _load_completed_receipt(
    path: Path,
    item: dict[str, object],
) -> dict[str, object]:
    receipt = json.loads(path.read_text(encoding="utf-8"))
    core = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    unit = item["unit"]
    schema_version = receipt.get("schema_version")
    if schema_version == "ondoway-text-candidate-stop-v2":
        # A Batch API receipt: the batch id replaces the per-call latency, and a
        # COMPLETED receipt may only record a success — every other outcome is a
        # failure file, never a stop file.
        batch_id = receipt.get("batch_id")
        if (
            not isinstance(batch_id, str)
            or not batch_id
            or receipt.get("result_type") != "succeeded"
            or "latency_ms" in receipt
        ):
            raise ValueError("batch stop receipt is not a well-formed success")
    elif schema_version != "ondoway-text-candidate-stop-v1":
        raise ValueError("completed stop receipt differs from the frozen plan")
    if (
        receipt.get("stop_index") != unit["stop_index"]
        or receipt.get("request_id") != unit["request_id"]
        or receipt.get("request_sha256") != unit["request_sha256"]
        or receipt.get("model") != COMPOSE_MODEL
        or receipt.get("receipt_sha256") != _sha256(canonical_bytes(core))
    ):
        raise ValueError("completed stop receipt differs from the frozen plan")
    raw = receipt.get("raw_response")
    if not isinstance(raw, str) or receipt.get("response_sha256") != _sha256(raw.encode("utf-8")):
        raise ValueError("completed stop receipt has altered provider bytes")
    payload = json.loads(raw)
    sentences = _sentences_from_json(payload["sentences"], item["request"])
    if receipt.get("parsed_payload_sha256") != sentences_payload_sha256(sentences):
        raise ValueError("completed stop receipt has altered parsed provider text")
    return receipt


def _summary(plan: dict[str, object]) -> dict[str, object]:
    units = plan["units"]
    input_ceiling = sum(unit["input_byte_count"] for unit in units)
    output_ceiling = sum(unit["output_token_ceiling"] for unit in units)
    maximum_cost = (
        input_ceiling * INPUT_USD_PER_MILLION_TOKENS
        + output_ceiling * OUTPUT_USD_PER_MILLION_TOKENS
    ) / 1_000_000
    return {
        "candidate_id": plan["candidate"]["candidate_id"],
        "plan_sha256": plan["plan_sha256"],
        "model": plan["model"],
        "thinking": plan["thinking"],
        "physical_call_count": len(units),
        "sdk_max_retries": plan["sdk_max_retries"],
        "absolute_call_deadline_seconds": plan["absolute_call_deadline_seconds"],
        "runner_sha256": plan["runner_sha256"],
        "provider_boundary_sha256": plan["provider_boundary_sha256"],
        "input_byte_ceiling": input_ceiling,
        "output_token_ceiling": output_ceiling,
        "maximum_cost_usd": round(maximum_cost, 6),
        "units": units,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", choices=("A", "B", "C"), default="A")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--approve-plan-sha256")
    parser.add_argument("--max-workers", type=int, default=4)
    args = parser.parse_args(argv)
    load_dotenv(ROOT / ".env", override=False)
    plan, runtime = _plan(args.candidate)
    if not args.live:
        print(json.dumps(_summary(plan), indent=2, sort_keys=True))
        return 0
    if os.environ.get(LIVE_APPROVAL_ENV) != "1":
        raise ValueError(f"live authoring requires {LIVE_APPROVAL_ENV}=1")
    if args.approve_plan_sha256 != plan["plan_sha256"]:
        raise ValueError("--approve-plan-sha256 must match the dry-run plan")
    if not 1 <= args.max_workers <= len(runtime):
        raise ValueError("max workers must be within the physical call count")

    output_dir = ROOT / "data" / "certification" / "paris" / f"text-candidate-{args.candidate}"
    output_dir.mkdir(mode=0o700, parents=False, exist_ok=True)
    plan_path = output_dir / "plan.json"
    if plan_path.exists():
        if json.loads(plan_path.read_text(encoding="utf-8")) != plan:
            raise ValueError("existing candidate plan differs from this run")
    else:
        _private_write_new(plan_path, plan)
    for stop_index in runtime:
        if (output_dir / f"failure-{stop_index}.json").exists():
            raise ValueError("candidate already contains a failed physical attempt")
        completed_path = output_dir / f"stop-{stop_index}.json"
        if completed_path.exists():
            _load_completed_receipt(completed_path, runtime[stop_index])
        attempt_path = output_dir / f"attempt-{stop_index}.json"
        if attempt_path.exists() and not completed_path.exists():
            raise ValueError(
                "candidate has an indeterminate prior physical attempt; use a new candidate"
            )

    pending = {
        stop_index: item
        for stop_index, item in runtime.items()
        if not (output_dir / f"stop-{stop_index}.json").exists()
    }
    client = certification_compose_client()
    for stop_index, item in pending.items():
        unit = item["unit"]
        marker_core = {
            "schema_version": "ondoway-text-candidate-attempt-v1",
            "stop_index": stop_index,
            "request_id": unit["request_id"],
            "request_sha256": unit["request_sha256"],
        }
        marker = {
            **marker_core,
            "marker_sha256": _sha256(canonical_bytes(marker_core)),
        }
        _private_write_new(output_dir / f"attempt-{stop_index}.json", marker)
    failures: list[BaseException] = []
    with ThreadPoolExecutor(max_workers=args.max_workers) as pool:
        stop_by_future = {
            pool.submit(_live_unit, client, item): stop_index
            for stop_index, item in pending.items()
        }
        for future in as_completed(stop_by_future):
            stop_index = stop_by_future[future]
            try:
                receipt = future.result()
            except BaseException as exc:
                failures.append(exc)
                failed = {
                    "schema_version": "ondoway-text-candidate-failure-v1",
                    "stop_index": stop_index,
                    "failure_type": type(exc).__name__,
                    "failure_message_sha256": _sha256(str(exc).encode("utf-8")),
                }
                path = output_dir / f"failure-{stop_index}.json"
                if not path.exists():
                    _private_write_new(path, failed)
            else:
                if receipt["schema_version"] == "ondoway-text-candidate-stop-v1":
                    _private_write_new(output_dir / f"stop-{stop_index}.json", receipt)
                else:
                    failures.append(RuntimeError("provider payload did not parse"))
                    _private_write_new(
                        output_dir / f"failure-{stop_index}.json",
                        receipt,
                    )
    if failures:
        raise RuntimeError(
            f"candidate failed {len(failures)} physical stop call(s); no fallback was used"
        ) from failures[0]

    receipts = [
        _load_completed_receipt(
            output_dir / f"stop-{index}.json",
            runtime[index],
        )
        for index in sorted(runtime)
    ]
    result = {
        **_summary(plan),
        "state": "completed",
        "actual_input_tokens": sum(item["input_tokens"] for item in receipts),
        "actual_output_tokens": sum(item["output_tokens"] for item in receipts),
        "actual_cost_usd": round(
            sum(item["input_tokens"] for item in receipts)
            * INPUT_USD_PER_MILLION_TOKENS
            / 1_000_000
            + sum(item["output_tokens"] for item in receipts)
            * OUTPUT_USD_PER_MILLION_TOKENS
            / 1_000_000,
            6,
        ),
        "receipt_sha256s": [item["receipt_sha256"] for item in receipts],
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
