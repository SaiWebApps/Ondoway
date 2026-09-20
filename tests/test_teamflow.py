"""Behavioral tests for the shared Claude/Codex delivery workflow."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TEAMFLOW_PATH = REPO / ".agents" / "team" / "teamflow.py"
MUTATION_GUARD_PATH = REPO / ".agents" / "team" / "mutation_guard.py"


def _load_teamflow():
    spec = importlib.util.spec_from_file_location("ondoway_teamflow", TEAMFLOW_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_mutation_guard():
    spec = importlib.util.spec_from_file_location("ondoway_mutation_guard", MUTATION_GUARD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def teamflow():
    return _load_teamflow()


def _charter() -> dict:
    return {
        "schema_version": 1,
        "phase_id": "phase-example",
        "version": 1,
        "outcomes": ["A traveller receives one generated tour."],
        "non_goals": ["Changing narration style."],
        "invariants": ["Workbench and app use the same planner."],
        "baseline": {"ref": "abc123", "checks": ["pytest tests/test_example.py"]},
        "criteria": [
            {
                "id": "tour-produced",
                "kind": "MECHANICAL",
                "evidence": ["pytest tests/test_example.py"],
                "threshold": "exit 0",
            }
        ],
        "external_assumptions": [],
        "risk": {"level": "LOW", "resources": ["LOCAL_RECOVERABLE"]},
        "rollback": "Revert the candidate commit.",
        "budgets": {
            "active_seconds": 3600,
            "cost_usd": 0,
            "tool_calls": 100,
            "escalations": 1,
        },
        "slots": [
            {
                "id": "S1",
                "archetype": "CAPABILITY",
                "outcomes": ["tour-produced"],
                "max_attempts": 2,
                "change_intent": "EXTEND_EXISTING",
                "behavior_owner": "tour-planning",
                "consumers": ["mobile", "workbench"],
                "searched_existing_paths": ["codegraph explore tour planning"],
            }
        ],
        "defect_reserve_slots": 1,
        "capabilities": [
            {
                "id": "tour-planning",
                "canonical_entrypoint": "src.tour.premium_tour:plan_premium_tour",
                "owned_rules": ["route selection", "tour planning"],
                "consumers": ["mobile", "workbench"],
                "adapters": [
                    "src.api.routes.trips:generate_trip",
                    "src.api.routes.trips:preview_trip",
                ],
                "status": "ACTIVE",
            }
        ],
        "baseline_exceptions": [],
    }


def test_charter_rejects_two_active_owners_for_one_capability(teamflow):
    charter = _charter()
    charter["capabilities"].append(
        {
            **charter["capabilities"][0],
            "canonical_entrypoint": "frontend.workbench:plan_tour",
        }
    )

    with pytest.raises(teamflow.WorkflowError, match="one active owner"):
        teamflow.validate_charter(charter)


def test_charter_rejects_judgment_criterion_without_oracle(teamflow):
    charter = _charter()
    charter["criteria"] = [{"id": "sounds-good", "kind": "JUDGMENT", "evidence": ["rendered tour"]}]

    with pytest.raises(teamflow.WorkflowError, match="oracle"):
        teamflow.validate_charter(charter)


def test_illegal_phase_transition_fails_closed(teamflow):
    with pytest.raises(teamflow.WorkflowError, match="illegal phase transition"):
        teamflow.require_phase_transition("READY", "RELEASED")

    assert teamflow.require_phase_transition("READY", "ACTIVE") == "ACTIVE"


def test_second_writer_cannot_acquire_an_active_lease(teamflow):
    state = {"leases": {}, "fencing_generation": 0}
    first = teamflow.acquire_lease(
        state,
        slot_id="S1",
        writer_id="claude",
        runtime="claude",
        allowed_resources=["src/"],
        ttl_seconds=300,
        now="2026-09-10T10:00:00+00:00",
    )

    with pytest.raises(teamflow.WorkflowError, match="active writer"):
        teamflow.acquire_lease(
            state,
            slot_id="S1",
            writer_id="codex",
            runtime="codex",
            allowed_resources=["src/"],
            ttl_seconds=300,
            now="2026-09-10T10:01:00+00:00",
        )

    assert first["fencing_generation"] == 1


def test_slice_replacement_requires_retirement(teamflow):
    slot = _charter()["slots"][0] | {"change_intent": "REPLACE"}

    with pytest.raises(teamflow.WorkflowError, match="retirement_plan"):
        teamflow.validate_slice_slot(slot)


def test_dormant_code_requires_activation_and_expiry(teamflow):
    slot = _charter()["slots"][0] | {"dormancy": {"flag": "NEW_ENGINE"}}

    with pytest.raises(teamflow.WorkflowError, match="activation_slot"):
        teamflow.validate_slice_slot(slot)


def test_review_is_bounded_and_has_only_three_dispositions(teamflow):
    findings = [
        {"id": f"F{i}", "disposition": "OUTSIDE_CONTRACT", "evidence": "not in charter"}
        for i in range(6)
    ]
    with pytest.raises(teamflow.WorkflowError, match="at most 5"):
        teamflow.validate_review_findings(findings)

    with pytest.raises(teamflow.WorkflowError, match="disposition"):
        teamflow.validate_review_findings(
            [{"id": "F1", "disposition": "NICE_TO_HAVE", "evidence": "opinion"}]
        )


def test_reachability_rejects_production_function_called_only_by_tests(tmp_path, teamflow):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "engine.py").write_text(
        "def used():\n    return 1\n\ndef test_only():\n    return 2\n"
    )
    (tmp_path / "src" / "app.py").write_text(
        "from src.engine import used\n\ndef main():\n    return used()\n"
    )
    (tmp_path / "tests" / "test_engine.py").write_text(
        "from src.engine import test_only, used\n\n"
        "def test_it():\n    assert test_only() == 2\n    assert used() == 1\n"
    )

    receipt = teamflow.python_reachability(tmp_path, ["src/engine.py"])

    assert receipt["dead_symbols"] == ["src.engine:test_only"]
    assert receipt["symbols"]["src.engine:used"]["production_roots"] == ["src.app:main"]
    assert receipt["symbols"]["src.engine:used"]["test_roots"] == ["tests.test_engine:test_it"]
    assert receipt["untested_symbols"] == []


def test_normalized_clone_detection_catches_parallel_business_logic(tmp_path, teamflow):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app_engine.py").write_text(
        "def choose_stop(score):\n    if score > 4:\n        return 'keep'\n    return 'drop'\n"
    )
    (tmp_path / "src" / "workbench_engine.py").write_text(
        "def preview_stop(value):\n    if value > 4:\n        return 'keep'\n    return 'drop'\n"
    )

    clones = teamflow.find_python_clones(tmp_path, ["src/workbench_engine.py"])

    assert clones == [
        {
            "changed": "src.workbench_engine:preview_stop",
            "existing": "src.app_engine:choose_stop",
        }
    ]


def test_clone_detection_catches_two_parallel_functions_added_together(tmp_path, teamflow):
    (tmp_path / "src").mkdir()
    for name, function, argument in (
        ("app_engine.py", "choose_stop", "score"),
        ("workbench_engine.py", "preview_stop", "value"),
    ):
        (tmp_path / "src" / name).write_text(
            f"def {function}({argument}):\n"
            f"    if {argument} > 4:\n"
            "        return 'keep'\n"
            "    return 'drop'\n"
        )

    clones = teamflow.find_python_clones(tmp_path, ["src/app_engine.py", "src/workbench_engine.py"])

    assert clones == [
        {
            "changed": "src.workbench_engine:preview_stop",
            "existing": "src.app_engine:choose_stop",
        }
    ]


def test_cli_shape_validate_approve_and_report_status(tmp_path):
    charter_path = tmp_path / "charter.json"
    run_dir = tmp_path / "run"
    charter_path.write_text(json.dumps(_charter()))

    shaped = subprocess.run(
        [sys.executable, TEAMFLOW_PATH, "shape", "--run-dir", run_dir, "--charter", charter_path],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )
    assert shaped.returncode == 0, shaped.stderr
    shaped_payload = json.loads(shaped.stdout)
    assert shaped_payload["phase_state"] == "AWAITING_APPROVAL"

    approved = subprocess.run(
        [
            sys.executable,
            TEAMFLOW_PATH,
            "approve",
            "--run-dir",
            run_dir,
            "--by",
            "owner",
            "--charter-hash",
            shaped_payload["charter_hash"],
        ],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )
    assert approved.returncode == 0, approved.stderr
    assert json.loads(approved.stdout)["phase_state"] == "READY"

    status = subprocess.run(
        [sys.executable, TEAMFLOW_PATH, "status", "--run-dir", run_dir],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["outcome"] == "ready to start"


def test_seeded_ownership_map_names_one_tour_planner(teamflow):
    ownership = json.loads((REPO / ".agents" / "team" / "ownership.json").read_text())

    teamflow.validate_capabilities(ownership["capabilities"])
    planner = next(row for row in ownership["capabilities"] if row["id"] == "tour-planning")

    assert planner["canonical_entrypoint"] == "src.tour.premium_tour:plan_premium_tour"
    assert set(planner["consumers"]) == {"mobile", "workbench"}


def test_public_schema_defines_every_workflow_record():
    schema = json.loads((REPO / ".agents" / "team" / "schema.json").read_text())

    assert set(schema["$defs"]) >= {
        "PhaseCharter",
        "CapabilityOwner",
        "SliceSlot",
        "Attempt",
        "Lease",
        "EvidenceEnvelope",
        "ReachabilityReceipt",
        "ReviewFinding",
        "ReleaseManifest",
    }


def test_mutation_guard_rejects_a_second_runtime_writer(tmp_path):
    guard = _load_mutation_guard()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "state.json").write_text(
        json.dumps(
            {
                "leases": {
                    "S1": {
                        "status": "ACTIVE",
                        "writer_id": "codex-1",
                        "runtime": "codex",
                        "allowed_resources": ["src/"],
                        "expires_at": "2999-01-01T00:00:00+00:00",
                    }
                }
            }
        )
    )

    with pytest.raises(guard.MutationRefusedError, match="codex-1"):
        guard.authorize_mutation(
            {"tool_name": "Edit", "tool_input": {"file_path": str(tmp_path / "src/x.py")}},
            run_dir=run_dir,
            runtime="claude",
            cwd=tmp_path,
        )


def test_mutation_guard_rejects_an_expired_writer_lease(tmp_path):
    guard = _load_mutation_guard()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "state.json").write_text(
        json.dumps(
            {
                "leases": {
                    "S1": {
                        "status": "ACTIVE",
                        "writer_id": "claude-1",
                        "runtime": "claude",
                        "allowed_resources": ["src/"],
                        "expires_at": "2000-01-01T00:00:00+00:00",
                    }
                }
            }
        )
    )

    with pytest.raises(guard.MutationRefusedError, match="expired"):
        guard.authorize_mutation(
            {"tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "src/x.py")}},
            run_dir=run_dir,
            runtime="claude",
            cwd=tmp_path,
        )


def test_mutation_guard_allows_the_lease_holder_inside_its_scope(tmp_path):
    guard = _load_mutation_guard()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "state.json").write_text(
        json.dumps(
            {
                "leases": {
                    "S1": {
                        "status": "ACTIVE",
                        "writer_id": "claude-1",
                        "runtime": "claude",
                        "allowed_resources": ["src/"],
                        "expires_at": "2999-01-01T00:00:00+00:00",
                    }
                }
            }
        )
    )

    guard.authorize_mutation(
        {"tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "src/x.py")}},
        run_dir=run_dir,
        runtime="claude",
        cwd=tmp_path,
    )


def test_mutation_guard_rejects_direct_workflow_state_edits(tmp_path):
    guard = _load_mutation_guard()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "state.json").write_text(json.dumps({"leases": {}}))

    with pytest.raises(guard.MutationRefusedError, match="transition CLI"):
        guard.authorize_mutation(
            {"tool_name": "Edit", "tool_input": {"file_path": str(run_dir / "state.json")}},
            run_dir=run_dir,
            runtime="claude",
            cwd=tmp_path,
        )


def test_writer_transfer_requires_suspension_and_rotates_fencing(teamflow):
    state = {"leases": {}, "fencing_generation": 0}
    teamflow.acquire_lease(
        state,
        slot_id="S1",
        writer_id="claude-1",
        runtime="claude",
        allowed_resources=["src/"],
        ttl_seconds=300,
        now="2026-09-10T10:00:00+00:00",
    )

    with pytest.raises(teamflow.WorkflowError, match="suspended"):
        teamflow.transfer_lease(
            state,
            slot_id="S1",
            writer_id="codex-1",
            runtime="codex",
            now="2026-09-10T10:01:00+00:00",
        )

    state["leases"]["S1"]["status"] = "SUSPENDED"
    transferred = teamflow.transfer_lease(
        state,
        slot_id="S1",
        writer_id="codex-1",
        runtime="codex",
        now="2026-09-10T10:01:00+00:00",
    )

    assert transferred["writer_id"] == "codex-1"
    assert transferred["fencing_generation"] == 2


def test_review_evidence_envelope_uses_the_bounded_finding_schema(teamflow):
    envelope = {
        "kind": "REVIEW",
        "actor": "reviewer-1",
        "slot_id": "S1",
        "content": {
            "findings": [{"id": "F1", "disposition": "BLOCK", "evidence": "pytest node failed"}]
        },
    }
    teamflow.validate_evidence_envelope(envelope)

    envelope["content"]["findings"][0]["disposition"] = "IMPROVEMENT"
    with pytest.raises(teamflow.WorkflowError, match="disposition"):
        teamflow.validate_evidence_envelope(envelope)


def test_attempt_cannot_be_accepted_without_a_clean_review(teamflow):
    state = {
        "slots": {"S1": {"state": "REVIEWING", "repair_rounds": 0, "probe_rounds": 0}},
        "evidence": [],
    }

    with pytest.raises(teamflow.WorkflowError, match="review evidence"):
        teamflow.transition_attempt(state, "S1", "ACCEPTED")

    state["evidence"].append(
        {
            "kind": "REVIEW",
            "actor": "reviewer-1",
            "slot_id": "S1",
            "content": {"findings": []},
        }
    )
    assert teamflow.transition_attempt(state, "S1", "ACCEPTED") == "ACCEPTED"


def test_attempt_allows_only_one_repair_round(teamflow):
    state = {
        "slots": {"S1": {"state": "REVIEWING", "repair_rounds": 0, "probe_rounds": 0}},
        "evidence": [
            {
                "kind": "REVIEW",
                "actor": "reviewer-1",
                "slot_id": "S1",
                "content": {
                    "findings": [{"id": "F1", "disposition": "BLOCK", "evidence": "failure"}]
                },
            }
        ],
    }

    teamflow.transition_attempt(state, "S1", "DEFECT_FIX")
    teamflow.transition_attempt(state, "S1", "CANDIDATE")
    teamflow.transition_attempt(state, "S1", "REVIEWING")

    with pytest.raises(teamflow.WorkflowError, match="repair round"):
        teamflow.transition_attempt(state, "S1", "DEFECT_FIX")


def test_unattended_charter_rejects_unfenceable_external_mutation(teamflow):
    charter = _charter()
    charter["mode"] = "UNATTENDED"
    charter["risk"]["resources"] = ["UNFENCEABLE_EXTERNAL"]

    with pytest.raises(teamflow.WorkflowError, match="unattended"):
        teamflow.validate_charter(charter)


def test_cli_runs_one_slice_to_a_truthful_release(tmp_path):
    charter_path = tmp_path / "charter.json"
    run_dir = tmp_path / "run"
    charter_path.write_text(json.dumps(_charter()))

    def invoke(*arguments: object) -> dict:
        result = subprocess.run(
            [sys.executable, TEAMFLOW_PATH, *map(str, arguments)],
            cwd=REPO,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    shaped = invoke("shape", "--run-dir", run_dir, "--charter", charter_path)
    invoke(
        "approve",
        "--run-dir",
        run_dir,
        "--by",
        "owner",
        "--charter-hash",
        shaped["charter_hash"],
    )
    invoke(
        "transition",
        "--run-dir",
        run_dir,
        "--entity",
        "attempt",
        "--slot",
        "S1",
        "--to",
        "READY",
        "--requested-by",
        "router",
    )
    invoke(
        "lease",
        "acquire",
        "--run-dir",
        run_dir,
        "--slot",
        "S1",
        "--writer",
        "codex-1",
        "--runtime",
        "codex",
        "--resource",
        "src/",
    )
    for destination in ("BUILDING", "CANDIDATE", "REVIEWING"):
        invoke(
            "transition",
            "--run-dir",
            run_dir,
            "--entity",
            "attempt",
            "--slot",
            "S1",
            "--to",
            destination,
            "--requested-by",
            "router",
        )
    review = tmp_path / "review.json"
    review.write_text(
        json.dumps(
            {
                "kind": "REVIEW",
                "actor": "claude-reviewer",
                "slot_id": "S1",
                "content": {"findings": []},
            }
        )
    )
    invoke("evidence", "submit", "--run-dir", run_dir, "--file", review)
    for destination in ("ACCEPTED", "COMPLETED"):
        invoke(
            "transition",
            "--run-dir",
            run_dir,
            "--entity",
            "attempt",
            "--slot",
            "S1",
            "--to",
            destination,
            "--requested-by",
            "router",
        )
    invoke(
        "lease",
        "release",
        "--run-dir",
        run_dir,
        "--slot",
        "S1",
        "--writer",
        "codex-1",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "source_ref": "abc123",
                "artifacts": ["tour-api"],
                "configuration": "config-hash",
                "data_versions": ["schema-v1"],
                "feature_flags": {},
                "targets": ["local"],
                "evidence": [
                    {
                        "command": "pytest tests/test_example.py",
                        "exit_code": 0,
                        "observed_at": "2026-09-10T10:00:00+00:00",
                    }
                ],
            }
        )
    )
    invoke("candidate", "build", "--run-dir", run_dir, "--manifest", manifest)
    assert (
        invoke("release", "verify", "--run-dir", run_dir, "--requested-by", "release-owner")[
            "phase_state"
        ]
        == "RELEASING"
    )
    unproved_release = subprocess.run(
        [
            sys.executable,
            TEAMFLOW_PATH,
            "release",
            "verify",
            "--run-dir",
            str(run_dir),
            "--requested-by",
            "release-owner",
        ],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )
    assert unproved_release.returncode == 2
    assert "release evidence" in unproved_release.stderr

    release_evidence = tmp_path / "release-evidence.json"
    release_evidence.write_text(
        json.dumps(
            {
                "kind": "RELEASE",
                "actor": "release-owner",
                "slot_id": "PHASE",
                "content": {
                    "manifest_hash": json.loads((run_dir / "state.json").read_text())[
                        "release_manifest_hash"
                    ],
                    "checks": [
                        {
                            "id": "smoke",
                            "status": "PASS",
                            "evidence": "local target returned the expected artifact",
                        }
                    ],
                },
            }
        )
    )
    invoke("evidence", "submit", "--run-dir", run_dir, "--file", release_evidence)
    assert invoke("release", "verify", "--run-dir", run_dir, "--requested-by", "release-owner") == {
        "operational_status": "CURRENT",
        "phase_state": "RELEASED",
    }
    assert invoke("status", "--run-dir", run_dir)["outcome"] == "released"
