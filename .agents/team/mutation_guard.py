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


#: The files that are the law: the workflow core, its skill, the tracker's own
#: code, and the settings that register this hook. Refused always — with or
#: without an active run — so the party being policed cannot edit its police.
PROTECTED_PREFIXES = (
    ".agents/team/",
    ".agents/skills/team/",
    ".claude/ledger/",
    ".claude/commands/team.md",
    ".claude/settings.json",
)


def _release_window(cwd: Path) -> dict[str, Any] | None:
    """A process change happens between stories through a recorded window:
    `.teamflow/release-window.json` naming the owner and the owner's reason.
    The file is local state, never committed; its presence is the audit trail
    and removing it closes the window."""
    path = cwd / ".teamflow" / "release-window.json"
    if not path.exists():
        return None
    try:
        window = json.loads(path.read_text())
    except (ValueError, OSError):
        return None
    return window if isinstance(window, dict) else None


def refuse_protected(payload: dict[str, Any], *, cwd: Path) -> None:
    """Refuse edits to the process files and direct tracker-database writes."""
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    if tool in {"Edit", "Write", "MultiEdit"}:
        target = _relative(str(tool_input.get("file_path", "")), cwd)
        touched = [p for p in PROTECTED_PREFIXES if target == p.rstrip("/") or target.startswith(p)]
    elif tool == "Bash":
        command = str(tool_input.get("command", ""))
        if "tracker.db" in command and not command.lstrip().startswith("python3"):
            raise MutationRefusedError(
                "the tracker database is written only through track.py, whose "
                "commands re-derive what they record; a direct write is a claim "
                "nobody checked"
            )
        # A protected path AFTER a mutating marker is the file being written
        # (`sed -i ... .agents/team/x.py`, `> .claude/settings.json`). A path
        # before one is an invocation (`python3 .claude/ledger/track.py show
        # > /dev/null`) and stays free.
        padded = f" {command}"
        touched = [
            prefix
            for marker in MUTATING_SHELL_MARKERS
            for at in range(len(padded))
            if padded.startswith(marker, at)
            for prefix in PROTECTED_PREFIXES
            if prefix.rstrip("/") in padded[at + len(marker):]
        ]
    else:
        return
    if not touched:
        return
    window = _release_window(cwd)
    if window is None:
        raise MutationRefusedError(
            f"{touched[0]} is a process file. The pipeline changes only between "
            "stories, with the owner's word recorded: open a release window at "
            ".teamflow/release-window.json naming owner and why"
        )
    if not window.get("owner") or not window.get("why"):
        raise MutationRefusedError(
            "the release window must name the owner and the owner's reason"
        )


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
        refuse_protected(payload, cwd=cwd)
        run_dir = _active_run(cwd)
        if run_dir is not None:
            authorize_mutation(payload, run_dir=run_dir, runtime="claude", cwd=cwd)
    except (MutationRefusedError, ValueError, OSError) as exc:
        print(json.dumps({"decision": "block", "reason": str(exc)}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
