#!/usr/bin/env python3
"""Read-before-edit, enforced: no edit to a guarded source file the session has not read whole.

The team process asks every session to walk the code with codegraph or whole-file
reads before touching it. Asked is not enforced; this hook is the enforcement.
It runs on two Claude Code hook events, from the same script:

- PostToolUse (Read, Bash): records a RECEIPT for every guarded file the session
  read whole — a `Read` with no offset/limit on a file that fits one default Read
  window, `codegraph node --file <path>`, or a bare `cat <path>` that nothing
  truncates. A bare Read of a file longer than the window is an excerpt and earns
  nothing; the refusal points at the command that shows the whole file.
- PreToolUse (Edit, Write, MultiEdit, Bash): refuses an edit to an existing guarded
  file with no receipt, and refuses in-place shell writes (`sed -i`, `>`/`>>`
  redirects, `tee`) to guarded paths outright — those bypass the receipt and the
  Edit tool's own whole-file discipline.

Receipts live per session under `.claude/runs/.walk-receipts/` (gitignored). A
file created by Write (it does not exist yet) needs no receipt. Anything the hook
cannot parse it lets through with a note on stderr: a hook bug must never lock
the repo, and a silent block would be worse than none.

Guarded roots: src/, scripts/, tests/, mobile/lib/, mobile/test/, frontend/.
Stdlib only — agent tooling under `.claude/`, never in the product's dependency
graph (the `.claude/ledger/track.py` precedent).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

GUARDED = ("src/", "scripts/", "tests/", "mobile/lib/", "mobile/test/", "frontend/")
RECEIPTS_DIR = Path(".claude/runs/.walk-receipts")
#: The Read tool's default window. A file longer than this needs an offset walk
#: or `codegraph node --file`; a bare Read of it is an excerpt, not a whole read.
READ_DEFAULT_LINES = 2000

CODEGRAPH_FILE_RE = re.compile(r"codegraph\s+node\s+--file\s+([^\s;|&]+)")
BARE_CAT_RE = re.compile(r"^\s*cat\s+(?:-n\s+)?([^\s;|&<>]+)\s*$")
SED_INPLACE_RE = re.compile(r"\bsed\s+(?:-[a-zA-Z]*\s+)*-i\b")
REDIRECT_RE = re.compile(r">{1,2}\s*([^\s;|&]+)")
TEE_RE = re.compile(r"\btee\s+(?:-a\s+)?([^\s;|&]+)")
PATH_TOKEN_RE = re.compile(r"[^\s;|&'\"<>()]+")


def _relative(path: str, cwd: Path) -> str:
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            return candidate.resolve().relative_to(cwd.resolve()).as_posix()
        except ValueError:
            return candidate.as_posix()
    return candidate.as_posix()


def _guarded(rel: str) -> bool:
    return any(rel.startswith(root) for root in GUARDED)


def _receipts_path(cwd: Path, session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "unknown")
    return cwd / RECEIPTS_DIR / f"{safe}.json"


def _load(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    try:
        return set(json.loads(path.read_text()))
    except (OSError, ValueError):
        return set()


def _save(path: Path, receipts: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(receipts)))


def _fits_one_read(cwd: Path, rel: str) -> bool:
    """A default Read shows at most READ_DEFAULT_LINES; a longer file was NOT read whole."""
    try:
        with (cwd / rel).open(errors="replace") as handle:
            return sum(1 for _ in handle) <= READ_DEFAULT_LINES
    except OSError:
        return False


def _whole_read_paths(tool: str, tool_input: dict, cwd: Path) -> list[str]:
    """Guarded files this tool call read WHOLE, as repo-relative paths."""
    if tool == "Read":
        if tool_input.get("offset") in (None, 0, 1) and tool_input.get("limit") is None:
            rel = _relative(str(tool_input.get("file_path", "")), cwd)
            return [rel] if _fits_one_read(cwd, rel) else []
        return []
    if tool == "Bash":
        command = str(tool_input.get("command", ""))
        found = CODEGRAPH_FILE_RE.findall(command)
        bare = BARE_CAT_RE.match(command)
        if bare:
            found.append(bare.group(1))
        return [_relative(p, cwd) for p in found]
    return []


def _shell_write_targets(command: str, cwd: Path) -> list[str]:
    """Guarded paths a shell command writes in place, bypassing Edit."""
    targets: list[str] = []
    if SED_INPLACE_RE.search(command):
        targets += [
            _relative(tok, cwd) for tok in PATH_TOKEN_RE.findall(command) if _guarded(tok)
        ]
    targets += [_relative(p, cwd) for p in REDIRECT_RE.findall(command)]
    targets += [_relative(p, cwd) for p in TEE_RE.findall(command)]
    return [t for t in targets if _guarded(t)]


def _refuse(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


def pre_tool_use(tool: str, tool_input: dict, cwd: Path, receipts: set[str]) -> int:
    if tool in ("Edit", "Write", "MultiEdit"):
        rel = _relative(str(tool_input.get("file_path", "")), cwd)
        if not _guarded(rel) or not (cwd / rel).is_file():
            return 0
        if rel in receipts:
            return 0
        return _refuse(
            f"walk first: {rel} has not been read whole in this session. "
            f"Run `codegraph node --file {rel}` or Read it with no offset/limit, "
            "then retry the edit."
        )
    if tool == "Bash":
        targets = _shell_write_targets(str(tool_input.get("command", "")), cwd)
        if targets:
            return _refuse(
                "in-place shell writes to source are refused: "
                + ", ".join(sorted(set(targets)))
                + ". Read the file whole, then use the Edit tool."
            )
    return 0


def post_tool_use(tool: str, tool_input: dict, cwd: Path, receipts_file: Path) -> int:
    paths = [p for p in _whole_read_paths(tool, tool_input, cwd) if _guarded(p)]
    if paths:
        receipts = _load(receipts_file)
        receipts.update(paths)
        _save(receipts_file, receipts)
    return 0


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError as exc:
        print(
            f"walk_receipts: unreadable hook payload ({exc}); letting the call through",
            file=sys.stderr,
        )
        return 0
    event = payload.get("hook_event_name", "")
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    cwd = Path(payload.get("cwd") or Path.cwd())
    receipts_file = _receipts_path(cwd, str(payload.get("session_id", "")))
    if event == "PreToolUse":
        return pre_tool_use(tool, tool_input, cwd, _load(receipts_file))
    if event == "PostToolUse":
        return post_tool_use(tool, tool_input, cwd, receipts_file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
