#!/usr/bin/env python3
"""Claude hook adapter for the shared teamflow writer lease."""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any


class MutationRefusedError(RuntimeError):
    """A tool attempted a mutation outside the active workflow lease."""


MUTATING_SHELL_MARKERS = (
    " >",
    ">>",
    "sed -i",
    "git commit",
    "git add",
    "git mv",
    "git rm",
    "apply_patch",
    "terraform apply",
    "kubectl apply",
)


def _relative(path: str, cwd: Path) -> str:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = cwd / candidate
    try:
        return candidate.resolve().relative_to(cwd.resolve()).as_posix()
    except ValueError:
        return candidate.resolve().as_posix()


def _active_lease(state: dict[str, Any]) -> dict[str, Any] | None:
    return next(
        (lease for lease in state.get("leases", {}).values() if lease.get("status") == "ACTIVE"),
        None,
    )


def authorize_mutation(payload: dict[str, Any], *, run_dir: Path, runtime: str, cwd: Path) -> None:
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    if tool not in {"Edit", "Write", "MultiEdit", "Bash"}:
        return
    state_path = run_dir / "state.json"
    if not state_path.exists():
        raise MutationRefusedError("the active teamflow run has no state")
    if tool in {"Edit", "Write", "MultiEdit"}:
        raw_target = Path(str(tool_input.get("file_path", "")))
        absolute_target = raw_target if raw_target.is_absolute() else cwd / raw_target
        if absolute_target.resolve() == state_path.resolve():
            raise MutationRefusedError(
                "workflow state may be changed only through the transition CLI"
            )
        target = _relative(str(raw_target), cwd)
    else:
        command = str(tool_input.get("command", ""))
        if not any(marker in f" {command}" for marker in MUTATING_SHELL_MARKERS):
            return
        target = None
    state = json.loads(state_path.read_text())
    lease = _active_lease(state)
    if lease is None:
        raise MutationRefusedError("the active teamflow run has no writer lease")
    expires_at = lease.get("expires_at")
    if not expires_at:
        raise MutationRefusedError("the active writer lease has no expiry")
    try:
        expiry = dt.datetime.fromisoformat(expires_at)
    except ValueError as exc:
        raise MutationRefusedError("the active writer lease has an invalid expiry") from exc
    if expiry.tzinfo is None or expiry <= dt.datetime.now(dt.UTC):
        raise MutationRefusedError("the active writer lease has expired")
    if lease.get("runtime") != runtime:
        raise MutationRefusedError(
            f"active writer {lease.get('writer_id')} runs on "
            f"{lease.get('runtime')}; {runtime} is read-only"
        )
    if target is not None:
        allowed = [str(path).rstrip("/") for path in lease.get("allowed_resources", [])]
        if not any(target == path or target.startswith(f"{path}/") for path in allowed):
            raise MutationRefusedError(f"{target} is outside the active writer lease")


def _active_run(cwd: Path) -> Path | None:
    explicit = os.getenv("ONDOWAY_TEAMFLOW_RUN_DIR")
    if explicit:
        return Path(explicit)
    pointer = cwd / ".teamflow" / "active-run.json"
    if not pointer.exists():
        return None
    try:
        return Path(json.loads(pointer.read_text())["run_dir"])
    except (KeyError, ValueError, OSError) as exc:
        raise MutationRefusedError(f"active-run pointer is invalid: {exc}") from exc


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        cwd = Path(payload.get("cwd") or Path.cwd())
        run_dir = _active_run(cwd)
        if run_dir is not None:
            authorize_mutation(payload, run_dir=run_dir, runtime="claude", cwd=cwd)
    except (MutationRefusedError, ValueError, OSError) as exc:
        print(json.dumps({"decision": "block", "reason": str(exc)}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
