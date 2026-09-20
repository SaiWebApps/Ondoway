#!/usr/bin/env python3
"""Shared, fail-closed workflow and single-authority checks for Claude and Codex."""

from __future__ import annotations

import argparse
import ast
import copy
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

TEAM_DIR = Path(__file__).resolve().parent
TRANSITIONS = json.loads((TEAM_DIR / "transitions.json").read_text())
OWNERSHIP_PATH = TEAM_DIR / "ownership.json"

PHASE_TERMINALS = {"RELEASED", "CONTRACT_INVALID", "ROLLED_BACK", "ABORTED"}
REVIEW_DISPOSITIONS = {"BLOCK", "EVIDENCE_REQUIRED", "OUTSIDE_CONTRACT"}
CHANGE_INTENTS = {"EXTEND_EXISTING", "EXTRACT_SHARED", "REPLACE", "NEW_CAPABILITY"}
ARCHETYPES = {
    "CAPABILITY",
    "REPRODUCER_FIX",
    "CHARACTERIZATION_REFACTOR",
    "MIGRATION_DATA",
    "UI_PROOF",
    "DEPLOYMENT_CANARY",
    "DEPENDENCY_PROBE",
}


class WorkflowError(ValueError):
    """A workflow request that cannot safely proceed."""


def _require(value: Any, message: str) -> None:
    if value is None or value == "" or value == [] or value == {}:
        raise WorkflowError(message)


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def validate_capabilities(capabilities: list[dict[str, Any]]) -> None:
    active_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    entrypoints: dict[str, str] = {}
    for capability in capabilities:
        for field in ("id", "canonical_entrypoint", "owned_rules", "consumers", "status"):
            _require(capability.get(field), f"capability requires {field}")
        if capability["status"] == "ACTIVE":
            active_by_id[capability["id"]].append(capability)
        entrypoint = capability["canonical_entrypoint"]
        previous = entrypoints.setdefault(entrypoint, capability["id"])
        if previous != capability["id"]:
            raise WorkflowError(
                f"canonical entrypoint {entrypoint} cannot own both {previous} "
                f"and {capability['id']}"
            )
    duplicates = sorted(
        capability_id for capability_id, rows in active_by_id.items() if len(rows) != 1
    )
    if duplicates:
        raise WorkflowError(f"each capability must have one active owner: {', '.join(duplicates)}")


def validate_slice_slot(slot: dict[str, Any]) -> None:
    for field in (
        "id",
        "archetype",
        "outcomes",
        "max_attempts",
        "change_intent",
        "behavior_owner",
        "consumers",
        "searched_existing_paths",
    ):
        _require(slot.get(field), f"slice slot requires {field}")
    if slot["archetype"] not in ARCHETYPES:
        raise WorkflowError(f"unknown slice archetype: {slot['archetype']}")
    if slot["change_intent"] not in CHANGE_INTENTS:
        raise WorkflowError(f"unknown change_intent: {slot['change_intent']}")
    if int(slot["max_attempts"]) < 1:
        raise WorkflowError("max_attempts must be positive")
    if slot["change_intent"] == "REPLACE":
        _require(slot.get("retirement_plan"), "REPLACE requires retirement_plan")
    dormancy = slot.get("dormancy")
    if dormancy:
        for field in ("flag", "activation_slot", "owner", "expires_at", "neutrality_evidence"):
            _require(dormancy.get(field), f"dormancy requires {field}")


def validate_review_findings(findings: list[dict[str, Any]]) -> None:
    if len(findings) > 5:
        raise WorkflowError("a review may contain at most 5 findings")
    seen: set[str] = set()
    for finding in findings:
        _require(finding.get("id"), "review finding requires id")
        _require(finding.get("evidence"), "review finding requires evidence")
        if finding.get("disposition") not in REVIEW_DISPOSITIONS:
            raise WorkflowError("review finding has an invalid disposition")
        if finding["id"] in seen:
            raise WorkflowError(f"duplicate review finding id: {finding['id']}")
        seen.add(finding["id"])


def validate_evidence_envelope(envelope: dict[str, Any]) -> None:
    for field in ("kind", "actor", "slot_id", "content"):
        _require(envelope.get(field), f"evidence envelope requires {field}")
    if envelope["kind"] == "REVIEW":
        findings = envelope["content"].get("findings")
        if findings is None:
            raise WorkflowError("review evidence requires findings")
        validate_review_findings(findings)
    elif envelope["kind"] == "RELEASE":
        content = envelope["content"]
        _require(content.get("manifest_hash"), "release evidence requires manifest_hash")
        checks = content.get("checks")
        _require(checks, "release evidence requires checks")
        for check in checks:
            for field in ("id", "status", "evidence"):
                _require(check.get(field), f"release check requires {field}")
            if check["status"] not in {"PASS", "FAIL"}:
                raise WorkflowError("release check status must be PASS or FAIL")


def validate_charter(charter: dict[str, Any]) -> None:
    required = (
        "schema_version",
        "phase_id",
        "version",
        "outcomes",
        "non_goals",
        "invariants",
        "baseline",
        "criteria",
        "risk",
        "rollback",
        "budgets",
        "slots",
        "defect_reserve_slots",
        "capabilities",
    )
    for field in required:
        if field not in charter:
            raise WorkflowError(f"charter requires {field}")
    if charter["schema_version"] != 1:
        raise WorkflowError("unsupported charter schema_version")
    resources = set(charter.get("risk", {}).get("resources", []))
    if charter.get("mode", "SUPERVISED") == "UNATTENDED" and "UNFENCEABLE_EXTERNAL" in resources:
        raise WorkflowError("unfenceable external mutation is prohibited unattended")
    for field in (
        "phase_id",
        "outcomes",
        "invariants",
        "baseline",
        "criteria",
        "rollback",
        "budgets",
        "slots",
        "capabilities",
    ):
        _require(charter[field], f"charter requires non-empty {field}")
    criterion_ids: set[str] = set()
    for criterion in charter["criteria"]:
        for field in ("id", "kind", "evidence"):
            _require(criterion.get(field), f"criterion requires {field}")
        if criterion["kind"] not in {"MECHANICAL", "JUDGMENT"}:
            raise WorkflowError(f"invalid criterion kind: {criterion['kind']}")
        if criterion["kind"] == "MECHANICAL":
            _require(criterion.get("threshold"), "mechanical criterion requires threshold")
        else:
            for field in (
                "oracle",
                "tie_break_authority",
                "timeout_seconds",
                "unavailable_disposition",
            ):
                _require(criterion.get(field), f"judgment criterion requires oracle field {field}")
        if criterion["id"] in criterion_ids:
            raise WorkflowError(f"duplicate criterion id: {criterion['id']}")
        criterion_ids.add(criterion["id"])
    validate_capabilities(charter["capabilities"])
    capability_ids = {row["id"] for row in charter["capabilities"] if row["status"] == "ACTIVE"}
    slot_ids: set[str] = set()
    for slot in charter["slots"]:
        validate_slice_slot(slot)
        if slot["id"] in slot_ids:
            raise WorkflowError(f"duplicate slice slot id: {slot['id']}")
        slot_ids.add(slot["id"])
        unknown_outcomes = sorted(set(slot["outcomes"]) - criterion_ids)
        if unknown_outcomes:
            raise WorkflowError(f"slot {slot['id']} names unknown criteria: {unknown_outcomes}")
        if slot["behavior_owner"] not in capability_ids:
            raise WorkflowError(f"slot {slot['id']} names no active behavior owner")
    default_reserve = min(2, max(1, (len(charter["slots"]) + 3) // 4))
    reserve = int(charter["defect_reserve_slots"])
    if reserve < 0:
        raise WorkflowError("defect_reserve_slots cannot be negative")
    if reserve > default_reserve and not charter.get("defect_reserve_override_approved"):
        raise WorkflowError("raised defect reserve requires owner approval")
    for exception in charter.get("baseline_exceptions", []):
        for field in (
            "id",
            "evidence",
            "scope",
            "owner",
            "created_at",
            "expires_at",
            "flake_policy",
            "justification",
        ):
            _require(exception.get(field), f"baseline exception requires {field}")
        if set(exception.get("intersects", [])) & (criterion_ids | {"SAFETY"}):
            raise WorkflowError("baseline exception intersects a charter criterion or safety")


def _require_transition(kind: str, source: str, destination: str) -> str:
    allowed = TRANSITIONS[kind].get(source)
    if allowed is None or destination not in allowed:
        raise WorkflowError(f"illegal {kind} transition: {source} -> {destination}")
    return destination


def require_phase_transition(source: str, destination: str) -> str:
    return _require_transition("phase", source, destination)


def require_attempt_transition(source: str, destination: str) -> str:
    return _require_transition("attempt", source, destination)


def _latest_review(state: dict[str, Any], slot_id: str) -> dict[str, Any] | None:
    return next(
        (
            envelope
            for envelope in reversed(state.get("evidence", []))
            if envelope.get("kind") == "REVIEW" and envelope.get("slot_id") == slot_id
        ),
        None,
    )


def transition_attempt(state: dict[str, Any], slot_id: str, destination: str) -> str:
    slot = state.get("slots", {}).get(slot_id)
    if slot is None:
        raise WorkflowError(f"unknown slice slot: {slot_id}")
    source = slot["state"]
    require_attempt_transition(source, destination)
    review = _latest_review(state, slot_id)
    if destination == "ACCEPTED":
        if review is None:
            raise WorkflowError("ACCEPTED requires review evidence")
        validate_evidence_envelope(review)
        dispositions = {finding["disposition"] for finding in review["content"].get("findings", [])}
        if dispositions & {"BLOCK", "EVIDENCE_REQUIRED"}:
            raise WorkflowError("ACCEPTED requires a review with no unresolved findings")
    elif destination == "DEFECT_FIX":
        if int(slot.get("repair_rounds", 0)) >= 1:
            raise WorkflowError("an attempt permits only one repair round")
        if review is None or not any(
            finding["disposition"] == "BLOCK" for finding in review["content"].get("findings", [])
        ):
            raise WorkflowError("DEFECT_FIX requires a BLOCK review finding")
        slot["repair_rounds"] = int(slot.get("repair_rounds", 0)) + 1
    elif destination == "EVIDENCE_PROBE":
        if int(slot.get("probe_rounds", 0)) >= 1:
            raise WorkflowError("an attempt permits only one evidence probe")
        if review is None or not any(
            finding["disposition"] == "EVIDENCE_REQUIRED"
            for finding in review["content"].get("findings", [])
        ):
            raise WorkflowError("EVIDENCE_PROBE requires an EVIDENCE_REQUIRED finding")
        slot["probe_rounds"] = int(slot.get("probe_rounds", 0)) + 1
    slot["state"] = destination
    return destination


def acquire_lease(
    state: dict[str, Any],
    *,
    slot_id: str,
    writer_id: str,
    runtime: str,
    allowed_resources: list[str],
    ttl_seconds: int,
    now: str | None = None,
) -> dict[str, Any]:
    active = [
        lease for lease in state.setdefault("leases", {}).values() if lease["status"] == "ACTIVE"
    ]
    if active:
        holder = active[0]
        raise WorkflowError(f"active writer {holder['writer_id']} already holds the workflow lease")
    if ttl_seconds < 1:
        raise WorkflowError("lease ttl_seconds must be positive")
    acquired = dt.datetime.fromisoformat(now) if now else dt.datetime.now(dt.UTC)
    state["fencing_generation"] = int(state.get("fencing_generation", 0)) + 1
    lease = {
        "slot_id": slot_id,
        "writer_id": writer_id,
        "runtime": runtime,
        "allowed_resources": allowed_resources,
        "ttl_seconds": ttl_seconds,
        "acquired_at": acquired.isoformat(),
        "expires_at": (acquired + dt.timedelta(seconds=ttl_seconds)).isoformat(),
        "heartbeat_at": acquired.isoformat(),
        "fencing_generation": state["fencing_generation"],
        "status": "ACTIVE",
    }
    state["leases"][slot_id] = lease
    return lease


def transfer_lease(
    state: dict[str, Any],
    *,
    slot_id: str,
    writer_id: str,
    runtime: str,
    now: str | None = None,
) -> dict[str, Any]:
    previous = state.setdefault("leases", {}).get(slot_id)
    if previous is None or previous.get("status") != "SUSPENDED":
        raise WorkflowError("writer transfer requires a suspended lease")
    transferred_at = dt.datetime.fromisoformat(now) if now else dt.datetime.now(dt.UTC)
    state["fencing_generation"] = int(state.get("fencing_generation", 0)) + 1
    lease = {
        **previous,
        "writer_id": writer_id,
        "runtime": runtime,
        "acquired_at": transferred_at.isoformat(),
        "heartbeat_at": transferred_at.isoformat(),
        "fencing_generation": state["fencing_generation"],
        "status": "ACTIVE",
        "transferred_from": previous["writer_id"],
    }
    state["leases"][slot_id] = lease
    return lease


def _module_name(repo: Path, path: Path) -> str:
    return ".".join(path.relative_to(repo).with_suffix("").parts)


def _function_nodes(tree: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [
        node
        for node in ast.iter_child_nodes(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _python_graph(
    repo: Path,
) -> tuple[dict[str, ast.AST], dict[str, set[str]], set[str], set[str]]:
    definitions: dict[str, ast.AST] = {}
    calls: dict[str, set[str]] = defaultdict(set)
    production_roots: set[str] = set()
    test_roots: set[str] = set()
    files = []
    for directory in ("src", "tests"):
        root = repo / directory
        if root.exists():
            files.extend(root.rglob("*.py"))
    files.sort()
    for path in files:
        module = _module_name(repo, path)
        is_test = path.relative_to(repo).parts[0] == "tests"
        try:
            tree = ast.parse(path.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        imported: dict[str, str] = {}
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    imported[alias.asname or alias.name] = f"{node.module}:{alias.name}"
        local = {node.name: f"{module}:{node.name}" for node in _function_nodes(tree)}
        for node in _function_nodes(tree):
            symbol = local[node.name]
            definitions[symbol] = node
            if is_test and node.name.startswith("test"):
                test_roots.add(symbol)
            elif not is_test and (
                node.name == "main" or node.decorator_list or "/routes/" in str(path)
            ):
                production_roots.add(symbol)
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Call) or not isinstance(inner.func, ast.Name):
                    continue
                name = inner.func.id
                target = imported.get(name) or local.get(name)
                if target:
                    calls[symbol].add(target)
    return definitions, calls, production_roots, test_roots


def python_reachability(repo: Path, changed_paths: list[str]) -> dict[str, Any]:
    definitions, calls, production_roots, test_roots = _python_graph(repo)
    changed_modules = {
        ".".join(Path(path).with_suffix("").parts) for path in changed_paths if path.endswith(".py")
    }

    def walk(roots: set[str]) -> dict[str, set[str]]:
        reached_by: dict[str, set[str]] = defaultdict(set)
        for root in roots:
            queue = deque([root])
            seen: set[str] = set()
            while queue:
                current = queue.popleft()
                if current in seen:
                    continue
                seen.add(current)
                reached_by[current].add(root)
                queue.extend(calls.get(current, ()))
        return reached_by

    reached_by_production = walk(production_roots)
    reached_by_tests = walk(test_roots)
    symbols: dict[str, dict[str, list[str]]] = {}
    dead: list[str] = []
    untested: list[str] = []
    for symbol in sorted(definitions):
        module, _name = symbol.split(":", 1)
        if module not in changed_modules:
            continue
        symbol_production_roots = sorted(reached_by_production.get(symbol, set()))
        symbol_test_roots = sorted(reached_by_tests.get(symbol, set()))
        symbols[symbol] = {
            "production_roots": symbol_production_roots,
            "test_roots": symbol_test_roots,
        }
        if not symbol_production_roots:
            dead.append(symbol)
        elif not symbol_test_roots:
            untested.append(symbol)
    return {"symbols": symbols, "dead_symbols": dead, "untested_symbols": untested}


class _NormalizeFunction(ast.NodeTransformer):
    def __init__(self, node: ast.FunctionDef | ast.AsyncFunctionDef):
        names = [
            arg.arg for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
        ]
        if node.args.vararg:
            names.append(node.args.vararg.arg)
        if node.args.kwarg:
            names.append(node.args.kwarg.arg)
        self.renames = {name: f"ARG{i}" for i, name in enumerate(names)}

    def visit_Name(self, node: ast.Name) -> ast.AST:
        return ast.copy_location(
            ast.Name(id=self.renames.get(node.id, node.id), ctx=node.ctx), node
        )

    def visit_arg(self, node: ast.arg) -> ast.AST:
        return ast.copy_location(
            ast.arg(arg=self.renames.get(node.arg, node.arg), annotation=node.annotation), node
        )


def _function_fingerprint(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    body = list(node.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    if len(list(ast.walk(ast.Module(body=body, type_ignores=[])))) < 10:
        return None
    shell = copy.deepcopy(node)
    shell.name = "FUNCTION"
    shell.body = copy.deepcopy(body)
    shell.decorator_list = []
    shell.returns = None
    normalized = _NormalizeFunction(shell).visit(shell)
    return hashlib.sha256(ast.dump(normalized, include_attributes=False).encode()).hexdigest()


def find_python_clones(repo: Path, changed_paths: list[str]) -> list[dict[str, str]]:
    changed = {Path(path).as_posix() for path in changed_paths}
    by_fingerprint: dict[str, list[tuple[str, bool]]] = defaultdict(list)
    for path in sorted((repo / "src").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        module = _module_name(repo, path)
        relative = path.relative_to(repo).as_posix()
        for node in _function_nodes(tree):
            value = _function_fingerprint(node)
            if value:
                by_fingerprint[value].append((f"{module}:{node.name}", relative in changed))
    clones: list[dict[str, str]] = []
    for rows in by_fingerprint.values():
        changed_rows = sorted(symbol for symbol, is_changed in rows if is_changed)
        existing_rows = sorted(symbol for symbol, is_changed in rows if not is_changed)
        comparisons = existing_rows or changed_rows[:1]
        for changed_symbol in changed_rows:
            for existing_symbol in comparisons:
                if changed_symbol == existing_symbol:
                    continue
                clones.append({"changed": changed_symbol, "existing": existing_symbol})
    return sorted(clones, key=lambda row: (row["changed"], row["existing"]))


def _atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _load_state(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "state.json"
    if not path.exists():
        raise WorkflowError(f"run has no state: {run_dir}")
    return json.loads(path.read_text())


def _save_state(run_dir: Path, state: dict[str, Any]) -> None:
    _atomic_write(run_dir / "state.json", state)


def _set_active_run(run_dir: Path, *, active: bool) -> None:
    repo = TEAM_DIR.parents[1]
    try:
        run_dir.resolve().relative_to(repo)
    except ValueError:
        return
    pointer = repo / ".teamflow" / "active-run.json"
    if active:
        _atomic_write(pointer, {"run_dir": str(run_dir.resolve())})
    elif pointer.exists():
        try:
            current = json.loads(pointer.read_text()).get("run_dir")
        except (ValueError, OSError):
            current = None
        if current == str(run_dir.resolve()):
            pointer.unlink()


def _event(
    state: dict[str, Any], event: str, requested_by: str, evidence: str | None = None
) -> None:
    state.setdefault("events", []).append(
        {
            "event": event,
            "requested_by": requested_by,
            "evidence": evidence,
            "at": dt.datetime.now(dt.UTC).isoformat(),
        }
    )


def _outcome(state: dict[str, Any]) -> str:
    phase = state["phase_state"]
    return {
        "AWAITING_APPROVAL": "owner approval required",
        "READY": "ready to start",
        "ACTIVE": "delivery active",
        "BLOCKED_EXTERNAL": "blocked by external dependency",
        "RELEASE_CANDIDATE": "aggregate verification required",
        "RELEASING": "release in progress",
        "RELEASED": "released",
        "CONTRACT_INVALID": "charter must be reshaped",
        "ROLLED_BACK": "release rolled back",
        "ABORTED": "run aborted",
    }.get(phase, phase.lower().replace("_", " "))


def command_shape(args: argparse.Namespace) -> dict[str, Any]:
    charter = json.loads(args.charter.read_text())
    validate_charter(charter)
    charter_hash = fingerprint(charter)
    state = {
        "schema_version": 1,
        "phase_id": charter["phase_id"],
        "phase_version": charter["version"],
        "charter_hash": charter_hash,
        "phase_state": "AWAITING_APPROVAL",
        "operational_status": None,
        "fencing_generation": 0,
        "leases": {},
        "slots": {
            slot["id"]: {
                "state": "DRAFT",
                "attempt": 1,
                "max_attempts": slot["max_attempts"],
                "repair_rounds": 0,
                "probe_rounds": 0,
            }
            for slot in charter["slots"]
        },
        "evidence": [],
        "events": [],
    }
    _event(state, "SHAPING -> AWAITING_APPROVAL", "router", charter_hash)
    _atomic_write(args.run_dir / "charter.json", charter)
    _save_state(args.run_dir, state)
    _set_active_run(args.run_dir, active=True)
    return {"phase_state": state["phase_state"], "charter_hash": charter_hash}


def command_validate(args: argparse.Namespace) -> dict[str, Any]:
    charter = json.loads((args.run_dir / "charter.json").read_text())
    state = _load_state(args.run_dir)
    validate_charter(charter)
    if fingerprint(charter) != state["charter_hash"]:
        raise WorkflowError("charter hash changed without OWNER_CHANGE")
    return {
        "valid": True,
        "phase_state": state["phase_state"],
        "charter_hash": state["charter_hash"],
    }


def command_approve(args: argparse.Namespace) -> dict[str, Any]:
    state = _load_state(args.run_dir)
    if args.charter_hash != state["charter_hash"]:
        raise WorkflowError("approval hash does not match the sealed charter")
    require_phase_transition(state["phase_state"], "READY")
    state["phase_state"] = "READY"
    state["approved_by"] = args.by
    _event(state, "AWAITING_APPROVAL -> READY", args.by, args.charter_hash)
    _save_state(args.run_dir, state)
    return {"phase_state": "READY", "charter_hash": state["charter_hash"]}


def command_status(args: argparse.Namespace) -> dict[str, Any]:
    state = _load_state(args.run_dir)
    return {
        "phase_id": state["phase_id"],
        "phase_state": state["phase_state"],
        "outcome": _outcome(state),
        "active_writer": next(
            (
                lease["writer_id"]
                for lease in state["leases"].values()
                if lease["status"] == "ACTIVE"
            ),
            None,
        ),
        "remaining_obligation": None
        if state["phase_state"] in PHASE_TERMINALS
        else _outcome(state),
    }


def command_transition(args: argparse.Namespace) -> dict[str, Any]:
    state = _load_state(args.run_dir)
    if args.entity == "phase":
        source = state["phase_state"]
        require_phase_transition(source, args.to)
        if args.to == "RELEASE_CANDIDATE" and any(
            row["state"] != "COMPLETED" for row in state["slots"].values()
        ):
            raise WorkflowError("release candidate requires every slot COMPLETED")
        state["phase_state"] = args.to
    else:
        if not args.slot or args.slot not in state["slots"]:
            raise WorkflowError("attempt transition requires a known --slot")
        source = state["slots"][args.slot]["state"]
        transition_attempt(state, args.slot, args.to)
    _event(state, f"{args.entity}:{source} -> {args.to}", args.requested_by, args.evidence)
    _save_state(args.run_dir, state)
    if args.entity == "phase" and args.to in PHASE_TERMINALS:
        _set_active_run(args.run_dir, active=False)
    return {"entity": args.entity, "from": source, "to": args.to}


def command_lease(args: argparse.Namespace) -> dict[str, Any]:
    state = _load_state(args.run_dir)
    if args.lease_command == "acquire":
        slot = state["slots"].get(args.slot)
        if not slot or slot["state"] != "READY":
            raise WorkflowError("lease acquisition requires a READY slot")
        lease = acquire_lease(
            state,
            slot_id=args.slot,
            writer_id=args.writer,
            runtime=args.runtime,
            allowed_resources=args.resource,
            ttl_seconds=args.ttl,
        )
        slot["state"] = "LEASED"
        if state["phase_state"] == "READY":
            state["phase_state"] = "ACTIVE"
        _event(state, f"lease acquired for {args.slot}", args.writer, fingerprint(lease))
        result = lease
    elif args.lease_command == "transfer":
        slot = state["slots"].get(args.slot)
        if not slot or slot["state"] != "SUSPENDED_INSPECTION":
            raise WorkflowError("writer transfer requires a suspended attempt")
        result = transfer_lease(
            state,
            slot_id=args.slot,
            writer_id=args.writer,
            runtime=args.runtime,
        )
        slot["state"] = "LEASED"
        _event(state, f"lease transferred for {args.slot}", args.writer, fingerprint(result))
    else:
        lease = state["leases"].get(args.slot)
        if not lease or lease["status"] != "ACTIVE":
            raise WorkflowError("slot has no active lease")
        if args.writer != lease["writer_id"]:
            raise WorkflowError("only the active writer may update its lease")
        if args.lease_command == "heartbeat":
            heartbeat_at = dt.datetime.now(dt.UTC)
            lease["heartbeat_at"] = heartbeat_at.isoformat()
            lease["expires_at"] = (
                heartbeat_at + dt.timedelta(seconds=int(lease["ttl_seconds"]))
            ).isoformat()
        elif args.lease_command == "suspend":
            state["slots"][args.slot]["state"] = "SUSPENDED_INSPECTION"
            lease["status"] = "SUSPENDED"
        elif args.lease_command == "release":
            lease["status"] = "RELEASED"
        result = lease
        _event(state, f"lease {args.lease_command} for {args.slot}", args.writer)
    _save_state(args.run_dir, state)
    return result


def command_evidence(args: argparse.Namespace) -> dict[str, Any]:
    state = _load_state(args.run_dir)
    payload = json.loads(args.file.read_text())
    validate_evidence_envelope(payload)
    envelope = {
        **payload,
        "hash": fingerprint(payload),
        "submitted_at": dt.datetime.now(dt.UTC).isoformat(),
    }
    state["evidence"].append(envelope)
    _event(state, "evidence submitted", payload["actor"], envelope["hash"])
    _save_state(args.run_dir, state)
    return {"evidence_hash": envelope["hash"]}


def _changed_python_paths(repo: Path, explicit: list[str] | None) -> list[str]:
    if explicit:
        return explicit
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACMR"],
        cwd=repo,
        check=True,
        text=True,
        capture_output=True,
    )
    return [
        line
        for line in result.stdout.splitlines()
        if line.startswith("src/") and line.endswith(".py")
    ]


def command_verify(args: argparse.Namespace) -> dict[str, Any]:
    changed = _changed_python_paths(args.repo, args.path)
    if args.verify_command == "reachability":
        result = python_reachability(args.repo, changed)
        if result["dead_symbols"]:
            raise WorkflowError(
                f"production symbols have no production caller: {result['dead_symbols']}"
            )
        if result["untested_symbols"]:
            raise WorkflowError(
                f"production symbols have no test path: {result['untested_symbols']}"
            )
        return result
    if args.verify_command == "single-authority":
        ownership = json.loads(OWNERSHIP_PATH.read_text())
        validate_capabilities(ownership["capabilities"])
        clones = find_python_clones(args.repo, changed)
        if clones:
            raise WorkflowError(f"parallel implementations detected: {clones}")
        return {"valid": True, "clones": []}
    charter = json.loads(args.charter.read_text())
    for slot in charter["slots"]:
        validate_slice_slot(slot)
    return {"valid": True}


def command_ownership(args: argparse.Namespace) -> dict[str, Any]:
    ownership = json.loads(OWNERSHIP_PATH.read_text())
    if args.ownership_command == "inspect":
        query = args.query.casefold()
        rows = [
            row
            for row in ownership["capabilities"]
            if query in canonical_json(row).decode().casefold()
        ]
        codegraph = None
        if (args.repo / ".codegraph").exists():
            result = subprocess.run(
                ["codegraph", "explore", args.query],
                cwd=args.repo,
                text=True,
                capture_output=True,
                check=False,
            )
            codegraph = {"exit_code": result.returncode, "output": result.stdout[-12000:]}
        return {"capabilities": rows, "codegraph": codegraph}
    candidate = json.loads(args.file.read_text())
    capabilities = [row for row in ownership["capabilities"] if row["id"] != candidate["id"]]
    capabilities.append(candidate)
    validate_capabilities(capabilities)
    ownership["capabilities"] = sorted(capabilities, key=lambda row: row["id"])
    _atomic_write(OWNERSHIP_PATH, ownership)
    return {"registered": candidate["id"], "ownership_hash": fingerprint(ownership)}


def command_candidate(args: argparse.Namespace) -> dict[str, Any]:
    state = _load_state(args.run_dir)
    if any(row["state"] != "COMPLETED" for row in state["slots"].values()):
        raise WorkflowError("candidate requires every immutable slot COMPLETED")
    if any(lease["status"] == "ACTIVE" for lease in state["leases"].values()):
        raise WorkflowError("candidate requires every writer lease released")
    manifest = json.loads(args.manifest.read_text())
    for field in (
        "source_ref",
        "artifacts",
        "configuration",
        "data_versions",
        "feature_flags",
        "targets",
        "evidence",
    ):
        if field not in manifest:
            raise WorkflowError(f"release manifest requires {field}")
    for evidence in manifest["evidence"]:
        for field in ("command", "exit_code", "observed_at"):
            if field not in evidence:
                raise WorkflowError(f"candidate evidence requires {field}")
        if evidence["exit_code"] != 0:
            raise WorkflowError(f"candidate evidence is red: {evidence['command']}")
    require_phase_transition(state["phase_state"], "RELEASE_CANDIDATE")
    state["phase_state"] = "RELEASE_CANDIDATE"
    state["release_manifest_hash"] = fingerprint(manifest)
    _atomic_write(args.run_dir / "release-manifest.json", manifest)
    _event(state, "ACTIVE -> RELEASE_CANDIDATE", args.requested_by, state["release_manifest_hash"])
    _save_state(args.run_dir, state)
    return {"phase_state": "RELEASE_CANDIDATE", "manifest_hash": state["release_manifest_hash"]}


def command_release(args: argparse.Namespace) -> dict[str, Any]:
    state = _load_state(args.run_dir)
    manifest = json.loads((args.run_dir / "release-manifest.json").read_text())
    if fingerprint(manifest) != state.get("release_manifest_hash"):
        raise WorkflowError("release manifest changed after candidate construction")
    destination = "RELEASING" if state["phase_state"] == "RELEASE_CANDIDATE" else "RELEASED"
    if destination == "RELEASED":
        release_evidence = next(
            (
                envelope
                for envelope in reversed(state.get("evidence", []))
                if envelope.get("kind") == "RELEASE"
            ),
            None,
        )
        if release_evidence is None:
            raise WorkflowError("RELEASED requires release evidence")
        validate_evidence_envelope(release_evidence)
        content = release_evidence["content"]
        if content["manifest_hash"] != state["release_manifest_hash"]:
            raise WorkflowError("release evidence names a different aggregate manifest")
        failed = [check["id"] for check in content["checks"] if check["status"] != "PASS"]
        if failed:
            raise WorkflowError(f"release evidence contains failed checks: {failed}")
    require_phase_transition(state["phase_state"], destination)
    state["phase_state"] = destination
    if destination == "RELEASED":
        state["operational_status"] = "CURRENT"
    _event(state, f"release -> {destination}", args.requested_by, state["release_manifest_hash"])
    _save_state(args.run_dir, state)
    if destination == "RELEASED":
        _set_active_run(args.run_dir, active=False)
    return {"phase_state": destination, "operational_status": state.get("operational_status")}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    shape = commands.add_parser("shape")
    shape.add_argument("--run-dir", type=Path, required=True)
    shape.add_argument("--charter", type=Path, required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--run-dir", type=Path, required=True)
    approve = commands.add_parser("approve")
    approve.add_argument("--run-dir", type=Path, required=True)
    approve.add_argument("--by", required=True)
    approve.add_argument("--charter-hash", required=True)
    status = commands.add_parser("status")
    status.add_argument("--run-dir", type=Path, required=True)
    transition = commands.add_parser("transition")
    transition.add_argument("--run-dir", type=Path, required=True)
    transition.add_argument("--entity", choices=("phase", "attempt"), required=True)
    transition.add_argument("--slot")
    transition.add_argument("--to", required=True)
    transition.add_argument("--requested-by", required=True)
    transition.add_argument("--evidence")

    lease = commands.add_parser("lease")
    lease_commands = lease.add_subparsers(dest="lease_command", required=True)
    for name in ("acquire", "heartbeat", "suspend", "transfer", "release"):
        item = lease_commands.add_parser(name)
        item.add_argument("--run-dir", type=Path, required=True)
        item.add_argument("--slot", required=True)
        item.add_argument("--writer", required=True)
        if name in {"acquire", "transfer"}:
            item.add_argument("--runtime", choices=("claude", "codex", "human"), required=True)
        if name == "acquire":
            item.add_argument("--resource", action="append", required=True)
            item.add_argument("--ttl", type=int, default=1800)

    evidence = commands.add_parser("evidence")
    evidence_commands = evidence.add_subparsers(dest="evidence_command", required=True)
    submit = evidence_commands.add_parser("submit")
    submit.add_argument("--run-dir", type=Path, required=True)
    submit.add_argument("--file", type=Path, required=True)

    ownership = commands.add_parser("ownership")
    ownership_commands = ownership.add_subparsers(dest="ownership_command", required=True)
    inspect = ownership_commands.add_parser("inspect")
    inspect.add_argument("--repo", type=Path, default=Path.cwd())
    inspect.add_argument("--query", required=True)
    register = ownership_commands.add_parser("register")
    register.add_argument("--file", type=Path, required=True)

    verify = commands.add_parser("verify")
    verify_commands = verify.add_subparsers(dest="verify_command", required=True)
    for name in ("reachability", "single-authority"):
        item = verify_commands.add_parser(name)
        item.add_argument("--repo", type=Path, default=Path.cwd())
        item.add_argument("--path", action="append")
    retirement = verify_commands.add_parser("retirement")
    retirement.add_argument("--repo", type=Path, default=Path.cwd())
    retirement.add_argument("--charter", type=Path, required=True)

    candidate = commands.add_parser("candidate")
    candidate_commands = candidate.add_subparsers(dest="candidate_command", required=True)
    build = candidate_commands.add_parser("build")
    build.add_argument("--run-dir", type=Path, required=True)
    build.add_argument("--manifest", type=Path, required=True)
    build.add_argument("--requested-by", default="router")
    release = commands.add_parser("release")
    release_commands = release.add_subparsers(dest="release_command", required=True)
    verify_release = release_commands.add_parser("verify")
    verify_release.add_argument("--run-dir", type=Path, required=True)
    verify_release.add_argument("--requested-by", required=True)
    return root


def dispatch(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "shape":
        return command_shape(args)
    if args.command == "validate":
        return command_validate(args)
    if args.command == "approve":
        return command_approve(args)
    if args.command == "status":
        return command_status(args)
    if args.command == "transition":
        return command_transition(args)
    if args.command == "lease":
        return command_lease(args)
    if args.command == "evidence":
        return command_evidence(args)
    if args.command == "ownership":
        return command_ownership(args)
    if args.command == "verify":
        return command_verify(args)
    if args.command == "candidate":
        return command_candidate(args)
    if args.command == "release":
        return command_release(args)
    raise WorkflowError(f"unsupported command: {args.command}")


def main() -> int:
    try:
        result = dispatch(parser().parse_args())
    except (WorkflowError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
