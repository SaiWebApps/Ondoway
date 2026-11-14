"""Zero-spend operational proofs for the manifest-driven Premium batch."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import tour_batch_candidate as runner
from src.tour.artifact import sentences_payload_sha256
from src.tour.authoring import (
    COMPOSE_MODEL,
    ComposeRequest,
    _sentences_from_json,
    candidate_compose_request_envelope,
    compose_input_sha256,
)
from src.tour.batch_regression_manifest import FrozenTourCase, load_frozen_tour_batch
from src.tour.candidate_authoring import AuthoringCandidateIdentity, AuthoringStopRequest
from src.tour.contract import (
    Script,
    ScriptPOI,
    Sentence,
    TourInput,
    ValidationReport,
)
from src.tour.corpus_places import canonical_bytes


def _case(case_id: str = "paris-test") -> FrozenTourCase:
    city = "new_york" if case_id.startswith("nyc") else "paris"
    start = (40.73, -73.99) if city == "new_york" else (48.85, 2.35)
    return FrozenTourCase(
        case_id=case_id,
        archetype="test fixture",
        tour_input=TourInput(start=start, duration_min=60, city_slug=city),
    )


def _runtime_item(case: FrozenTourCase) -> dict[str, object]:
    source_sentence = Sentence(
        text="Look across the square.",
        source_id="GLUE_NAV",
        source_type="glue",
        stop_idx=0,
    )
    script = Script(
        city_slug=case.tour_input.city_slug,
        generated_at="2026-07-22T00:00:00Z",
        inputs=case.tour_input,
        total_audio_seconds=5,
        total_walking_seconds=0,
        total_walk_distance_m=0,
        total_planned_seconds=3600,
        selected_pois=(
            ScriptPOI(
                id="p0",
                name="Square",
                tier=5,
                lat=case.tour_input.start[0],
                lng=case.tour_input.start[1],
            ),
        ),
        lens_coverage={},
        script=(source_sentence,),
        validation=ValidationReport(),
    )
    request = ComposeRequest(stitched=script, tour_context=("Square",))
    candidate = AuthoringCandidateIdentity.create(
        candidate_slot="A",
        contract_sha256="1" * 64,
        reference_manifest_sha256="2" * 64,
        calibration_manifest_sha256="3" * 64,
        grounded_source_sha256="4" * 64,
        route_sha256="5" * 64,
        authoring_policy_sha256="6" * 64,
    )
    authoring = AuthoringStopRequest.create(
        candidate=candidate,
        stop_index=0,
        compose_input_sha256=compose_input_sha256(request),
    )
    envelope, sdk_request = candidate_compose_request_envelope(
        request,
        authoring,
        model=COMPOSE_MODEL,
    )
    return {
        "unit": {
            "stop_index": 0,
            "poi_name": "Square",
            "request_id": authoring.request_id,
            "request_sha256": runner._sha256(envelope.encode()),
            "input_byte_count": len(envelope.encode()),
            "output_token_ceiling": 64_000,
            "sdk_request": sdk_request,
        },
        "request": request,
        "authoring": authoring,
    }


def _fixture_plan(case_id: str = "paris-test") -> tuple[dict, dict]:
    case = _case(case_id)
    item = _runtime_item(case)
    public_unit = {key: value for key, value in item["unit"].items() if key != "sdk_request"}
    tour = {
        "schema_version": "ondoway-tour-batch-tour-plan-v1",
        "case_id": case.case_id,
        "archetype": case.archetype,
        "request_sha256": case.request_sha256,
        "tour_input": case.tour_input.model_dump(mode="json"),
        "candidate": item["authoring"].candidate.model_dump(mode="json"),
        "route": {
            "route_sha256": "5" * 64,
            "routed": True,
            "total_walk_seconds": 1200,
            "total_walk_distance_m": 1000,
            "pois": [{"id": "p0", "name": "Square", "lat": 48.85, "lng": 2.35}],
            "legs": [],
        },
        "grounded_source_sha256": "4" * 64,
        "model": COMPOSE_MODEL,
        "thinking": {"type": "adaptive"},
        "sdk_max_retries": 0,
        "application_deadline_seconds": None,
        "units": [public_unit],
        "tour_plan_sha256": "7" * 64,
    }
    plan = {
        "schema_version": "ondoway-tour-batch-plan-v1",
        "manifest_sha256": "8" * 64,
        "runner_sha256": "9" * 64,
        "model": COMPOSE_MODEL,
        "thinking": {"type": "adaptive"},
        "sdk_max_retries": 0,
        "application_deadline_seconds": None,
        "tours": [tour],
        "batch_plan_sha256": "a" * 64,
    }
    runtime = {
        case.case_id: {
            "case": case,
            "premium_plan": "shared-premium-plan",
            "units": {0: item},
        }
    }
    return plan, runtime


def _receipt(item: dict[str, object], text: str) -> dict[str, object]:
    unit = item["unit"]
    payload = {
        "sentences": [
            {
                "text": text,
                "source_id": "GLUE_NAV",
                "source_type": "glue",
                "stop_idx": 0,
            }
        ]
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    sentences = _sentences_from_json(payload["sentences"], item["request"])
    core = {
        "schema_version": "ondoway-text-candidate-stop-v1",
        "stop_index": 0,
        "poi_name": "Square",
        "request_id": unit["request_id"],
        "request_sha256": unit["request_sha256"],
        "response_sha256": runner._sha256(raw.encode()),
        "parsed_payload_sha256": sentences_payload_sha256(sentences),
        "provider_request_id": "provider-one",
        "model": COMPOSE_MODEL,
        "input_tokens": 10,
        "output_tokens": 20,
        "latency_ms": 30,
        "raw_response": raw,
    }
    return {**core, "receipt_sha256": runner._sha256(canonical_bytes(core))}


class _StubBatchClient:
    """A fake Anthropic client for the Batch API arm — real pipeline, $0.

    Drives the REAL ``execute_batch_pipeline`` (submit → persist → poll →
    collect) so the runner test exercises the actual transport rather than a
    monkeypatched seam. ``fail_submit=True`` makes ``create`` fail the test —
    the resume path must never re-submit.
    """

    def __init__(self, texts_by_custom_id: dict[str, str], *, fail_submit: bool = False,
                 errored: frozenset[str] = frozenset()) -> None:
        self._texts = texts_by_custom_id
        self._errored = errored
        self._fail_submit = fail_submit
        outer = self

        class _Batches:
            def create(self, *, requests):
                if outer._fail_submit:
                    pytest.fail("resume re-submitted an already-submitted batch")
                outer.submitted = [request["custom_id"] for request in requests]
                return SimpleNamespace(id="msgbatch_stub_01")

            def retrieve(self, batch_id):
                assert batch_id == "msgbatch_stub_01"
                return SimpleNamespace(processing_status="ended")

            def results(self, batch_id):
                assert batch_id == "msgbatch_stub_01"
                for custom_id, text in outer._texts.items():
                    if custom_id in outer._errored:
                        yield SimpleNamespace(
                            custom_id=custom_id,
                            result=SimpleNamespace(
                                type="errored",
                                error=SimpleNamespace(
                                    error=SimpleNamespace(
                                        type="invalid_request_error", message="stub refusal"
                                    )
                                ),
                            ),
                        )
                        continue
                    payload = {
                        "sentences": [
                            {
                                "text": text,
                                "source_id": "GLUE_NAV",
                                "source_type": "glue",
                                "stop_idx": 0,
                            }
                        ]
                    }
                    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                    yield SimpleNamespace(
                        custom_id=custom_id,
                        result=SimpleNamespace(
                            type="succeeded",
                            message=SimpleNamespace(
                                content=[SimpleNamespace(type="text", text=raw)],
                                usage=SimpleNamespace(
                                    input_tokens=10,
                                    output_tokens=20,
                                    cache_creation_input_tokens=0,
                                    cache_read_input_tokens=0,
                                ),
                                model=COMPOSE_MODEL,
                                id="msg-batch-one",
                                stop_reason="end_turn",
                            ),
                        ),
                    )

        self.messages = SimpleNamespace(batches=_Batches())


def test_batch_transport_arm_writes_v2_receipts_and_renders_exact_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Batch API arm runs the REAL pipeline end to end at $0: submission is
    persisted before polling, the stop receipt is schema v2 (batch_id, no
    latency_ms), and the rendered tour carries the provider's exact text."""
    plan, runtime = _fixture_plan()
    provider_text = "Batch provider text — exact."
    client = _StubBatchClient({"paris-test-stop-0": provider_text})
    monkeypatch.setattr(runner, "finalize_premium_composition", lambda *_args: None)

    result = runner._execute_batch(
        plan,
        runtime,
        output_root=tmp_path,
        client_factory=lambda: client,
        max_workers=1,
        transport="batch",
    )

    assert result["state"] == "completed"
    submission = json.loads((tmp_path / "batch-submission.json").read_text())
    assert submission["batch_id"] == "msgbatch_stub_01"
    assert submission["custom_ids"] == ["paris-test-stop-0"]
    receipt = json.loads(
        (tmp_path / "paris" / "paris-test" / "stop-0.json").read_text()
    )
    assert receipt["schema_version"] == "ondoway-text-candidate-stop-v2"
    assert receipt["batch_id"] == "msgbatch_stub_01"
    assert receipt["result_type"] == "succeeded"
    assert "latency_ms" not in receipt
    tour = json.loads((tmp_path / "paris" / "paris-test" / "tour.json").read_text())
    assert tour["stops"][0]["sentences"] == [provider_text]
    assert (tmp_path / "paris" / "paris-test" / "attempt-0.json").is_file()


def test_batch_transport_arm_resumes_from_persisted_submission_without_resubmitting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crash after submission must resume by batch_id — never a second paid
    submission, and never an 'indeterminate prior attempt' refusal, because the
    persisted submission file IS the determinate record."""
    plan, runtime = _fixture_plan()
    provider_text = "Collected on resume."
    monkeypatch.setattr(runner, "finalize_premium_composition", lambda *_args: None)
    from src.tour.batch_transport import persist_batch_submission

    (tmp_path / "paris" / "paris-test").mkdir(parents=True)
    persist_batch_submission(
        tmp_path / "batch-submission.json",
        batch_id="msgbatch_stub_01",
        custom_ids=["paris-test-stop-0"],
        plan_sha256=plan["batch_plan_sha256"],
    )
    attempt_core = {"schema_version": "ondoway-tour-batch-attempt-v1"}
    (tmp_path / "paris" / "paris-test" / "attempt-0.json").write_text(
        json.dumps(attempt_core), encoding="utf-8"
    )
    client = _StubBatchClient({"paris-test-stop-0": provider_text}, fail_submit=True)

    result = runner._execute_batch(
        plan,
        runtime,
        output_root=tmp_path,
        client_factory=lambda: client,
        max_workers=1,
        transport="batch",
    )

    assert result["state"] == "completed"
    tour = json.loads((tmp_path / "paris" / "paris-test" / "tour.json").read_text())
    assert tour["stops"][0]["sentences"] == [provider_text]


def test_batch_transport_arm_marks_errored_units_and_substitutes_no_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, runtime = _fixture_plan()
    monkeypatch.setattr(runner, "finalize_premium_composition", lambda *_args: None)
    client = _StubBatchClient(
        {"paris-test-stop-0": "never used"}, errored=frozenset({"paris-test-stop-0"})
    )

    with pytest.raises(RuntimeError, match="no fallback"):
        runner._execute_batch(
            plan,
            runtime,
            output_root=tmp_path,
            client_factory=lambda: client,
            max_workers=1,
            transport="batch",
        )

    output = tmp_path / "paris" / "paris-test"
    assert not (output / "tour.json").exists()
    failure = json.loads((output / "failure-0.json").read_text())
    assert failure["schema_version"] == "ondoway-tour-batch-failure-v1"


def test_batch_requests_preserve_adaptive_thinking_without_deadline() -> None:
    plan, runtime = _fixture_plan()
    sdk_request = runtime["paris-test"]["units"][0]["unit"]["sdk_request"]

    assert sdk_request["thinking"] == {"type": "adaptive"}
    assert plan["application_deadline_seconds"] is None
    assert plan["sdk_max_retries"] == 0


def test_all_attempt_markers_precede_client_and_exact_text_is_rendered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, runtime = _fixture_plan()
    expected_marker = tmp_path / "paris" / "paris-test" / "attempt-0.json"
    provider_text = "Provider text — unchanged."

    def client_factory() -> object:
        assert expected_marker.is_file()
        return object()

    def live_unit(client, item, *, absolute_deadline_seconds):
        assert client is not None
        assert absolute_deadline_seconds is None
        return _receipt(item, provider_text)

    monkeypatch.setattr(runner, "_live_unit", live_unit)
    finalized: list[tuple[object, int]] = []
    monkeypatch.setattr(
        runner,
        "finalize_premium_composition",
        lambda plan, responses: finalized.append((plan, len(responses))),
    )
    result = runner._execute_batch(
        plan,
        runtime,
        output_root=tmp_path,
        client_factory=client_factory,
        max_workers=1,
    )

    assert result["state"] == "completed"
    assert finalized == [("shared-premium-plan", 1)]
    tour_path = tmp_path / "paris" / "paris-test" / "tour.json"
    tour = json.loads(tour_path.read_text(encoding="utf-8"))
    assert tour["provenance"] == "provider_response"
    assert tour["stops"][0]["sentences"] == [provider_text]
    assert provider_text in (tmp_path / "paris" / "paris-test" / "tour.md").read_text()


def test_completed_receipt_resumes_without_constructing_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, runtime = _fixture_plan()
    monkeypatch.setattr(
        runner,
        "_live_unit",
        lambda _client, item, **_kwargs: _receipt(item, "First and only call."),
    )
    monkeypatch.setattr(runner, "finalize_premium_composition", lambda *_args: None)
    runner._execute_batch(
        plan,
        runtime,
        output_root=tmp_path,
        client_factory=object,
        max_workers=4,
    )

    runner._execute_batch(
        plan,
        runtime,
        output_root=tmp_path,
        client_factory=lambda: pytest.fail("resume constructed a paid client"),
        max_workers=4,
    )


def test_malformed_provider_payload_creates_no_tour_or_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, runtime = _fixture_plan()
    monkeypatch.setattr(
        runner,
        "_live_unit",
        lambda _client, _item, **_kwargs: {
            "schema_version": "ondoway-text-candidate-provider-payload-failure-v1"
        },
    )

    with pytest.raises(RuntimeError, match="no fallback"):
        runner._execute_batch(
            plan,
            runtime,
            output_root=tmp_path,
            client_factory=object,
            max_workers=1,
        )

    output = tmp_path / "paris" / "paris-test"
    assert not (output / "tour.json").exists()
    assert not (output / "tour.md").exists()
    assert (output / "failure-0.json").is_file()


def test_same_planning_function_handles_all_eight_cases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = load_frozen_tour_batch(runner.MANIFEST_PATH)
    seen: list[str] = []
    loaded: list[str] = []

    snapshots: list[object] = []

    def plan_tour(case, *, driver, routing_client, snapshot):
        assert driver == "driver"
        assert routing_client == "routing"
        snapshots.append(snapshot)
        seen.append(case.tour_input.city_slug)
        return (
            {"case_id": case.case_id, "request_sha256": case.request_sha256, "units": []},
            {"case": case, "units": {}},
        )

    monkeypatch.setattr(runner, "_plan_tour", plan_tour)

    def load_snapshot(_driver, *, city_slug):
        loaded.append(city_slug)
        return f"snapshot:{city_slug}"

    monkeypatch.setattr(runner, "load_paris_corpus", load_snapshot)
    plan, _runtime = runner._batch_plan(
        manifest,
        driver="driver",
        routing_client="routing",
    )

    assert seen == ["paris"] * 4 + ["new_york"] * 4
    assert loaded == ["paris", "new_york"]
    assert snapshots == ["snapshot:paris"] * 4 + ["snapshot:new_york"] * 4
    assert len(plan["tours"]) == 8


def test_batch_publishes_the_exact_shared_premium_plan_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case()
    expected = {"schema_version": "shared-plan", "bytes": [1, 2, 3]}
    shared = SimpleNamespace(
        units=(),
        route="shared-route",
        sequence="shared-sequence",
        source="shared-source",
        batch_payload=lambda **metadata: expected,
    )

    def shared_plan(tour_input, snapshot, **kwargs):
        assert tour_input is case.tour_input
        assert snapshot == "snapshot"
        assert kwargs["routing_client"] == "routing"
        assert kwargs["generation_time"] == runner.FIXED_GENERATION_TIME
        return shared

    monkeypatch.setattr(runner, "plan_premium_tour", shared_plan)
    plan, runtime = runner._plan_tour(
        case,
        driver="driver",
        routing_client="routing",
        snapshot="snapshot",
    )

    assert plan is expected
    assert runtime["premium_plan"] is shared
    assert runtime["units"] == {}


def test_dry_main_never_constructs_paid_client(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan, runtime = _fixture_plan()

    class Driver:
        def close(self) -> None:
            pass

    class Routing:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(runner, "create_driver", Driver)
    monkeypatch.setattr(runner, "RoutingClient", Routing)
    monkeypatch.setattr(runner, "_batch_plan", lambda *_args, **_kwargs: (plan, runtime))
    monkeypatch.setattr(
        runner,
        "certification_batch_compose_client",
        lambda: pytest.fail("dry plan constructed a paid client"),
    )

    assert runner.main([]) == 0
    assert json.loads(capsys.readouterr().out)["batch_plan_sha256"] == "a" * 64


def test_wrong_live_hash_rejects_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, runtime = _fixture_plan()

    class Driver:
        def close(self) -> None:
            pass

    class Routing:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(runner, "create_driver", Driver)
    monkeypatch.setattr(runner, "RoutingClient", Routing)
    monkeypatch.setattr(runner, "_batch_plan", lambda *_args, **_kwargs: (plan, runtime))
    monkeypatch.setenv(runner.LIVE_APPROVAL_ENV, "1")
    monkeypatch.setattr(
        runner,
        "_execute_batch",
        lambda *_args, **_kwargs: pytest.fail("wrong hash reached execution"),
    )

    with pytest.raises(ValueError, match="dry batch plan"):
        runner.main(
            [
                "--live",
                "--approve-plan-sha256",
                "b" * 64,
                "--output-root",
                str(tmp_path / "tour-batch-v2"),
            ]
        )


def test_live_without_output_root_refuses_the_frozen_control_arm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitting --output-root lands on DEFAULT_OUTPUT_ROOT — the sealed batch the
    release gate compares against — so --live refuses instead of authoring over it,
    before the manifest, the graph or the router are touched."""
    monkeypatch.setattr(
        runner,
        "create_driver",
        lambda: pytest.fail("a refused live run reached the graph"),
    )

    with pytest.raises(ValueError, match="frozen control arm"):
        runner.main(["--live", "--approve-plan-sha256", "a" * 64])


@pytest.mark.parametrize(
    "spelling",
    [
        "data/certification/tour-batch-v1",
        "data/certification/tour-batch-v1/",
        "./data/certification/tour-batch-v1",
        "data/certification/tour-batch-v2/../tour-batch-v1",
        str(runner.DEFAULT_OUTPUT_ROOT),
    ],
)
def test_live_refuses_every_spelling_of_the_frozen_control_arm(
    spelling: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The control arm is refused by RESOLVED path, never by string match, so a
    trailing slash, a leading ./, a .. segment and the absolute spelling are one
    refusal rather than four holes."""
    monkeypatch.chdir(runner.ROOT)
    monkeypatch.setattr(
        runner,
        "create_driver",
        lambda: pytest.fail("a refused live run reached the graph"),
    )

    with pytest.raises(ValueError, match="frozen control arm"):
        runner.main(
            ["--live", "--approve-plan-sha256", "a" * 64, "--output-root", spelling]
        )
