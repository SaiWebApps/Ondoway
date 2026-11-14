"""Manifest-driven Premium authoring for the frozen Paris/New York batch.

Dry planning executes the real selection, Valhalla routing, grounded source,
and Anthropic request construction paths without constructing a paid client.
Live execution preserves exact provider text and never substitutes fallback.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from scripts.tour_text_candidate import (
    INPUT_USD_PER_MILLION_TOKENS,
    OUTPUT_USD_PER_MILLION_TOKENS,
    _file_sha256,
    _live_unit,
    _load_completed_receipt,
    _private_write_new,
    _sha256,
)
from src.connection import create_driver
from src.tour import batch_transport
from src.tour.anthropic_client import certification_batch_compose_client
from src.tour.artifact import sentences_payload_sha256
from src.tour.authoring import COMPOSE_MODEL, _sentences_from_json
from src.tour.batch_regression_manifest import (
    FrozenTourBatchManifest,
    FrozenTourCase,
    load_frozen_tour_batch,
)
from src.tour.certification_provider import PhysicalProviderResponse
from src.tour.corpus_places import canonical_bytes
from src.tour.premium_tour import (
    certification_planning_policy,
    finalize_premium_composition,
    plan_premium_tour,
)
from src.tour.routing import RoutePlanningPolicy
from src.tour.routing_client import RoutingClient
from src.tour.selection import load_paris_corpus

ROOT = Path(__file__).resolve().parents[1]
#: The frozen request manifest. A fixture the code OPENS, so it lives under
#: `fixtures/` — `specs/` was deleted 2026-09-02 and is refused by the junk guard.
BATCH_SPEC = ROOT / "fixtures" / "tour-batch-regression"
MANIFEST_PATH = BATCH_SPEC / "requests.v1.json"
DEFAULT_OUTPUT_ROOT = ROOT / "data" / "certification" / "tour-batch-v1"
LIVE_APPROVAL_ENV = "ONDOWAY_TOUR_BATCH_APPROVED"
FIXED_GENERATION_TIME = dt.datetime(2026, 7, 22, tzinfo=dt.UTC)


def _planning_policy(case: FrozenTourCase) -> RoutePlanningPolicy:
    return certification_planning_policy(
        policy_id=f"tour-batch-v1:{case.request_sha256}",
    )


def _plan_tour(
    case: FrozenTourCase,
    *,
    driver: object,
    routing_client: RoutingClient,
    snapshot: object | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    """Plan one case through the same city-neutral algorithm."""

    if snapshot is None:
        snapshot = load_paris_corpus(driver, city_slug=case.tour_input.city_slug)
    shared = plan_premium_tour(
        case.tour_input,
        snapshot,
        routing_client=routing_client,
        planning_policy=_planning_policy(case),
        generation_time=FIXED_GENERATION_TIME,
    )
    plan = shared.batch_payload(
        case_id=case.case_id,
        archetype=case.archetype,
        request_sha256=case.request_sha256,
    )
    runtime_units: dict[int, dict[str, object]] = {}
    for shared_unit in shared.units:
        unit = {**shared_unit.public_payload(), "sdk_request": shared_unit.sdk_request}
        runtime_units[shared_unit.stop_index] = {
            "unit": unit,
            "request": shared_unit.authorized_request,
            "authoring": shared_unit.authoring_request,
        }
    runtime = {
        "case": case,
        "premium_plan": shared,
        "route": shared.route,
        "sequence": shared.sequence,
        "source": shared.source,
        "units": runtime_units,
    }
    return plan, runtime


def _batch_plan(
    manifest: FrozenTourBatchManifest,
    *,
    driver: object,
    routing_client: RoutingClient,
) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    tours: list[dict[str, object]] = []
    runtime: dict[str, dict[str, object]] = {}
    snapshots: dict[str, object] = {}
    for case in manifest.cases:
        city_slug = case.tour_input.city_slug
        if city_slug not in snapshots:
            snapshots[city_slug] = load_paris_corpus(driver, city_slug=city_slug)
        tour, tour_runtime = _plan_tour(
            case,
            driver=driver,
            routing_client=routing_client,
            snapshot=snapshots[city_slug],
        )
        tours.append(tour)
        runtime[case.case_id] = tour_runtime
    core = {
        "schema_version": "ondoway-tour-batch-plan-v1",
        "manifest_sha256": manifest.manifest_sha256,
        "runner_sha256": _file_sha256(Path(__file__)),
        "model": COMPOSE_MODEL,
        "thinking": {"type": "adaptive"},
        "sdk_max_retries": 0,
        "application_deadline_seconds": None,
        "tours": tours,
    }
    return (
        {**core, "batch_plan_sha256": _sha256(canonical_bytes(core))},
        runtime,
    )


def _write_new_bytes(path: Path, value: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(value)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("artifact write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _assemble_provider_tour(
    tour_plan: dict[str, object],
    tour_runtime: dict[str, object],
    *,
    output_dir: Path,
) -> dict[str, object]:
    """Render one complete customer tour from validated provider receipts."""

    runtime_units = tour_runtime["units"]
    stops: list[dict[str, object]] = []
    ordered_text: list[str] = []
    physical_responses: list[PhysicalProviderResponse] = []
    for unit in tour_plan["units"]:
        stop_index = unit["stop_index"]
        item = runtime_units[stop_index]
        receipt = _load_completed_receipt(
            output_dir / f"stop-{stop_index}.json",
            item,
        )
        payload = json.loads(receipt["raw_response"])
        sentences = _sentences_from_json(payload["sentences"], item["request"])
        physical_responses.append(
            PhysicalProviderResponse(
                body=receipt["raw_response"].encode("utf-8"),
                input_tokens=receipt["input_tokens"],
                output_tokens=receipt["output_tokens"],
                # v2 (Batch API) receipts carry no latency: the batch has no
                # per-call wall clock, and the replay below never reads it.
                latency_ms=receipt.get("latency_ms", 0),
                model=receipt["model"],
                provider_request_id=receipt["provider_request_id"],
            )
        )
        texts = [sentence.text for sentence in sentences]
        ordered_text.extend(texts)
        stops.append(
            {
                "stop_index": stop_index,
                "poi_name": unit["poi_name"],
                "sentences": texts,
                "provider_response_sha256": receipt["response_sha256"],
                "parsed_payload_sha256": receipt["parsed_payload_sha256"],
            }
        )
    # Shared pure replay proves durable batch receipts satisfy the same
    # request/response and VERIFY boundary as the interactive workbench.
    finalize_premium_composition(tour_runtime["premium_plan"], tuple(physical_responses))
    tour_core = {
        "schema_version": "ondoway-provider-authored-tour-v1",
        "case_id": tour_plan["case_id"],
        "tour_plan_sha256": tour_plan["tour_plan_sha256"],
        "tour_input": tour_plan["tour_input"],
        "route": tour_plan["route"],
        "model": tour_plan["model"],
        "thinking": tour_plan["thinking"],
        "provenance": "provider_response",
        "stops": stops,
        "customer_text_sha256": _sha256(canonical_bytes(ordered_text)),
    }
    tour = {**tour_core, "artifact_sha256": _sha256(canonical_bytes(tour_core))}
    json_path = output_dir / "tour.json"
    if json_path.exists():
        if json.loads(json_path.read_text(encoding="utf-8")) != tour:
            raise ValueError("existing tour artifact differs from validated receipts")
    else:
        _private_write_new(json_path, tour)

    route_names = " → ".join(poi["name"] for poi in tour_plan["route"]["pois"])
    lines = [
        f"# {tour_plan['case_id']} — Exact API-authored tour text",
        "",
        f"Route: {route_names}",
        "",
    ]
    for stop in stops:
        lines.extend(
            [
                f"## {stop['stop_index'] + 1}. {stop['poi_name']}",
                "",
                "\n\n".join(stop["sentences"]),
                "",
            ]
        )
    markdown = "\n".join(lines).encode("utf-8")
    markdown_path = output_dir / "tour.md"
    if markdown_path.exists():
        if markdown_path.read_bytes() != markdown:
            raise ValueError("existing customer tour differs from validated receipts")
    else:
        _write_new_bytes(markdown_path, markdown)
    return tour


def _execute_batch(
    plan: dict[str, object],
    runtime: dict[str, dict[str, object]],
    *,
    output_root: Path,
    client_factory: Callable[[], object],
    max_workers: int,
    transport: str = "sync",
) -> dict[str, object]:
    """Execute every pending stop once after durable attempt marking.

    ``transport`` picks the physical lane, never the receipts' meaning:
    - "sync": one bounded streaming call per stop (the original lane; v1
      receipts with a latency).
    - "batch": ONE Message Batches submission for every pending stop at the
      Batch API's half price (v2 receipts carry the batch_id instead of a
      latency). The submission's batch_id is persisted to disk the instant
      ``batches.create`` returns, so a crash during the poll resumes by id —
      an attempt marker beside a persisted submission is a determinate,
      resumable state, not the indeterminate one the sync lane refuses.
    """

    if transport not in ("sync", "batch"):
        raise ValueError("transport must be 'sync' or 'batch'")
    units_by_key: dict[tuple[str, int], dict[str, object]] = {}
    output_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    plan_path = output_root / "plan.json"
    if plan_path.exists():
        if json.loads(plan_path.read_text(encoding="utf-8")) != plan:
            raise ValueError("existing batch plan differs from this run")
    else:
        _private_write_new(plan_path, plan)

    submission_path = output_root / "batch-submission.json"
    resumable_submission = transport == "batch" and submission_path.exists()

    for tour_plan in plan["tours"]:
        case_id = tour_plan["case_id"]
        case = runtime[case_id]["case"]
        output_dir = output_root / case.tour_input.city_slug / case_id
        output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        for stop_index, item in runtime[case_id]["units"].items():
            failure_path = output_dir / f"failure-{stop_index}.json"
            completed_path = output_dir / f"stop-{stop_index}.json"
            if failure_path.exists():
                raise ValueError(f"{case_id} stop {stop_index} already has a failed attempt")
            if completed_path.exists():
                _load_completed_receipt(completed_path, item)
                continue
            attempt_path = output_dir / f"attempt-{stop_index}.json"
            if attempt_path.exists() and not resumable_submission:
                raise ValueError(f"{case_id} stop {stop_index} has an indeterminate prior attempt")
            units_by_key[(case_id, stop_index)] = item

    if not 1 <= max_workers <= 8:
        raise ValueError("max workers must be between one and eight")

    for (case_id, stop_index), item in units_by_key.items():
        case = runtime[case_id]["case"]
        output_dir = output_root / case.tour_input.city_slug / case_id
        attempt_path = output_dir / f"attempt-{stop_index}.json"
        if attempt_path.exists():
            continue
        unit = item["unit"]
        marker_core = {
            "schema_version": "ondoway-tour-batch-attempt-v1",
            "case_id": case_id,
            "stop_index": stop_index,
            "request_id": unit["request_id"],
            "request_sha256": unit["request_sha256"],
            "batch_plan_sha256": plan["batch_plan_sha256"],
        }
        _private_write_new(
            attempt_path,
            {**marker_core, "marker_sha256": _sha256(canonical_bytes(marker_core))},
        )

    failures: list[tuple[str, int, BaseException]] = []
    if units_by_key and transport == "batch":
        results = batch_transport.execute_batch_pipeline(
            [
                # "-stop-" not ":" — the Batch API's custom_id alphabet is
                # [a-zA-Z0-9_-] and the ids are constructed, never parsed back.
                (f"{case_id}-stop-{stop_index}", item["unit"]["sdk_request"])
                for (case_id, stop_index), item in units_by_key.items()
            ],
            submission_path=submission_path,
            plan_sha256=plan["batch_plan_sha256"],
            client=client_factory(),
            on_progress=print,
        )
        for (case_id, stop_index), item in units_by_key.items():
            case = runtime[case_id]["case"]
            output_dir = output_root / case.tour_input.city_slug / case_id
            unit = item["unit"]
            unit_result = results.get(f"{case_id}-stop-{stop_index}")
            try:
                if unit_result is None:
                    raise RuntimeError("batch returned no result for this unit")
                if unit_result.result_type != "succeeded" or unit_result.response is None:
                    raise RuntimeError(
                        unit_result.error_message or f"batch unit {unit_result.result_type}"
                    )
                body = unit_result.response.body
                payload = json.loads(body)
                sentences = _sentences_from_json(payload["sentences"], item["request"])
                receipt = batch_transport.build_batch_receipt(
                    unit_result=unit_result,
                    request_id=unit["request_id"],
                    request_sha256=unit["request_sha256"],
                    response_sha256=_sha256(body),
                    parsed_payload_sha256=sentences_payload_sha256(sentences),
                    poi_name=unit["poi_name"],
                    stop_index=stop_index,
                    raw_response=body.decode("utf-8"),
                )
            except BaseException as exc:
                failures.append((case_id, stop_index, exc))
                failure = {
                    "schema_version": "ondoway-tour-batch-failure-v1",
                    "case_id": case_id,
                    "stop_index": stop_index,
                    "failure_type": type(exc).__name__,
                    "failure_message_sha256": _sha256(str(exc).encode("utf-8")),
                }
                _private_write_new(output_dir / f"failure-{stop_index}.json", failure)
            else:
                _private_write_new(output_dir / f"stop-{stop_index}.json", receipt)
    elif units_by_key:
        client = client_factory()
        with ThreadPoolExecutor(max_workers=min(max_workers, len(units_by_key))) as pool:
            key_by_future = {
                pool.submit(
                    _live_unit,
                    client,
                    item,
                    absolute_deadline_seconds=None,
                ): key
                for key, item in units_by_key.items()
            }
            for future in as_completed(key_by_future):
                case_id, stop_index = key_by_future[future]
                case = runtime[case_id]["case"]
                output_dir = output_root / case.tour_input.city_slug / case_id
                try:
                    receipt = future.result()
                    if receipt["schema_version"] != "ondoway-text-candidate-stop-v1":
                        raise RuntimeError("provider payload did not parse")
                except BaseException as exc:
                    failures.append((case_id, stop_index, exc))
                    failure = {
                        "schema_version": "ondoway-tour-batch-failure-v1",
                        "case_id": case_id,
                        "stop_index": stop_index,
                        "failure_type": type(exc).__name__,
                        "failure_message_sha256": _sha256(str(exc).encode("utf-8")),
                    }
                    _private_write_new(output_dir / f"failure-{stop_index}.json", failure)
                else:
                    _private_write_new(output_dir / f"stop-{stop_index}.json", receipt)

    tours: list[dict[str, object]] = []
    for tour_plan in plan["tours"]:
        case_id = tour_plan["case_id"]
        case = runtime[case_id]["case"]
        output_dir = output_root / case.tour_input.city_slug / case_id
        if all(
            (output_dir / f"stop-{unit['stop_index']}.json").exists() for unit in tour_plan["units"]
        ):
            tours.append(
                _assemble_provider_tour(
                    tour_plan,
                    runtime[case_id],
                    output_dir=output_dir,
                )
            )
    if failures:
        raise RuntimeError(
            f"batch failed {len(failures)} physical stop call(s); no fallback was used"
        ) from failures[0][2]
    if len(tours) != len(plan["tours"]):
        raise RuntimeError("batch is incomplete; no partial tour was substituted")
    return {
        "schema_version": "ondoway-tour-batch-result-v1",
        "batch_plan_sha256": plan["batch_plan_sha256"],
        "tour_artifact_sha256s": [tour["artifact_sha256"] for tour in tours],
        "state": "completed",
    }


def _summary(plan: dict[str, object]) -> dict[str, object]:
    units = [unit for tour in plan["tours"] for unit in tour["units"]]
    input_ceiling = sum(unit["input_byte_count"] for unit in units)
    output_ceiling = sum(unit["output_token_ceiling"] for unit in units)
    maximum_cost = (
        input_ceiling * INPUT_USD_PER_MILLION_TOKENS
        + output_ceiling * OUTPUT_USD_PER_MILLION_TOKENS
    ) / 1_000_000
    return {
        "mode": "dry-plan",
        "batch_plan_sha256": plan["batch_plan_sha256"],
        "manifest_sha256": plan["manifest_sha256"],
        "tour_count": len(plan["tours"]),
        "physical_call_count": len(units),
        "model": plan["model"],
        "thinking": plan["thinking"],
        "sdk_max_retries": plan["sdk_max_retries"],
        "application_deadline_seconds": plan["application_deadline_seconds"],
        "maximum_cost_usd": round(maximum_cost, 6),
        "tours": plan["tours"],
    }


def _live_output_root(output_root: Path) -> Path:
    """The directory a live batch may author into, or a refusal.

    ``DEFAULT_OUTPUT_ROOT`` is the FROZEN control arm the release gate compares a
    regeneration AGAINST, so authoring into it destroys the baseline the new batch is
    measured by. The comparison is on the RESOLVED path, so every spelling of that one
    directory is the same refusal: a trailing slash, a leading ``./``, an absolute
    path, a ``..`` segment. Omitting ``--output-root`` lands on the default and is
    refused for the same reason, which is why ``make tour-batch-live`` requires
    ``OUTPUT_ROOT`` — that recipe is convenience, and this is the guard a direct
    ``python -m scripts.tour_batch_candidate --live`` cannot walk around.
    """
    resolved = output_root.resolve()
    if resolved == DEFAULT_OUTPUT_ROOT.resolve():
        raise ValueError(
            f"--output-root {resolved} is the frozen control arm the release gate "
            "compares against; live authoring must write elsewhere"
        )
    return resolved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--approve-plan-sha256")
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--case")
    # "batch" routes the same sealed requests through the Message Batches API at
    # half price; "sync" stays the original per-stop streaming lane. The sealed
    # plan is transport-agnostic — the approve hash covers identical requests
    # either way — so this flag changes the bill, never the comparison.
    parser.add_argument("--transport", choices=("sync", "batch"), default="sync")
    args = parser.parse_args(argv)
    # Settled before the manifest, the graph or the router are touched, so a live run
    # aimed at the control arm dies without doing anything. Dry planning never reads
    # this value, so it keeps the default and is unaffected.
    live_output_root = _live_output_root(args.output_root) if args.live else None
    manifest = load_frozen_tour_batch(MANIFEST_PATH)
    driver = create_driver()
    try:
        with RoutingClient() as routing_client:
            if args.case:
                if args.live:
                    raise ValueError("single-case mode is provider-free only")
                selected = next(
                    (case for case in manifest.cases if case.case_id == args.case),
                    None,
                )
                if selected is None:
                    raise ValueError(f"unknown frozen case {args.case!r}")
                tour_plan, _tour_runtime = _plan_tour(
                    selected,
                    driver=driver,
                    routing_client=routing_client,
                )
                print(json.dumps(tour_plan, indent=2, sort_keys=True))
                return 0
            plan, runtime = _batch_plan(
                manifest,
                driver=driver,
                routing_client=routing_client,
            )
    finally:
        driver.close()

    if not args.live:
        print(json.dumps(_summary(plan), indent=2, sort_keys=True))
        return 0
    if os.environ.get(LIVE_APPROVAL_ENV) != "1":
        raise ValueError(f"live authoring requires {LIVE_APPROVAL_ENV}=1")
    if args.approve_plan_sha256 != plan["batch_plan_sha256"]:
        raise ValueError("--approve-plan-sha256 must match the dry batch plan")
    result = _execute_batch(
        plan,
        runtime,
        output_root=live_output_root,
        client_factory=(
            # Read via the module attribute so the hermetic money-guard's patch
            # of batch_transport.batch_client is the one every path sees.
            batch_transport.batch_client
            if args.transport == "batch"
            else certification_batch_compose_client
        ),
        max_workers=args.max_workers,
        transport=args.transport,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
