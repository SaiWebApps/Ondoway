#!/usr/bin/env python3
"""Read-before-edit, enforced: no edit to a guarded source file the session has not read whole.

The team process asks every session to walk the code with codegraph or whole-file
reads before touching it. Asked is not enforced; this hook is the enforcement.
It runs on two Claude Code hook events, from the same script:

- PostToolUse (Read, Bash): records the LINE RANGE every read covered —
  a `Read` (its offset and limit, or the default window from the top),
  `codegraph node --file <path>`, or a bare `cat <path>`, which cover the file
  entire. Ranges accumulate and merge, so a file longer than one Read window is
  walked by paging through it; the refusal names the lines still missing.

  A cap that no single Read could clear was worse than no cap: a 2353-line file
  could never earn a receipt, so the only ways forward were the two shell
  commands or reading an excerpt and reasoning from it — and reasoning is not
  what this hook gates. A wall invites climbing round; a ladder is climbed.
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


def _load(path: Path) -> dict[str, list[list[int]]]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if isinstance(data, list):  # receipts written before ranges: whole files
        return {str(p): [[1, 10**9]] for p in data}
    try:
        return {str(k): [[int(a), int(b)] for a, b in v] for k, v in data.items()}
    except (TypeError, ValueError):
        return {}


def _save(path: Path, receipts: dict[str, list[list[int]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({key: receipts[key] for key in sorted(receipts)}))


def _line_total(cwd: Path, rel: str) -> int:
    try:
        with (cwd / rel).open(errors="replace") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return 0


def _merge(ranges: list[list[int]]) -> list[list[int]]:
    """Overlapping and adjacent spans become one, so coverage is a simple walk."""
    merged: list[list[int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _gaps(ranges: list[list[int]], total: int) -> list[tuple[int, int]]:
    """The lines still unread, so a refusal names them instead of saying 'walk first'."""
    missing: list[tuple[int, int]] = []
    cursor = 1
    for start, end in _merge(ranges):
        if start > cursor:
            missing.append((cursor, start - 1))
        cursor = max(cursor, end + 1)
    if cursor <= total:
        missing.append((cursor, total))
    return missing


def _read_ranges(tool: str, tool_input: dict, cwd: Path) -> list[tuple[str, int, int]]:
    """What this call actually covered, as (path, first line, last line)."""
    if tool == "Read":
        rel = _relative(str(tool_input.get("file_path", "")), cwd)
        total = _line_total(cwd, rel)
        if not total:
            return []
        try:
            start = max(1, int(tool_input.get("offset") or 1))
            limit = tool_input.get("limit")
            span = int(limit) if limit else READ_DEFAULT_LINES
        except (TypeError, ValueError):
            return []
        return [(rel, start, min(total, start + span - 1))]
    if tool == "Bash":
        command = str(tool_input.get("command", ""))
        found = CODEGRAPH_FILE_RE.findall(command)
        bare = BARE_CAT_RE.match(command)
        if bare:
            found.append(bare.group(1))
        covered = []
        for path in found:
            rel = _relative(path, cwd)
            total = _line_total(cwd, rel)
            if total:
                covered.append((rel, 1, total))
        return covered
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


def pre_tool_use(tool: str, tool_input: dict, cwd: Path, receipts: dict) -> int:
    if tool in ("Edit", "Write", "MultiEdit"):
        rel = _relative(str(tool_input.get("file_path", "")), cwd)
        if not _guarded(rel) or not (cwd / rel).is_file():
            return 0
        missing = _gaps(receipts.get(rel, []), _line_total(cwd, rel))
        if not missing:
            return 0
        where = ", ".join(f"{first}-{last}" for first, last in missing[:4])
        return _refuse(
            f"walk first: {rel} is not read whole in this session — missing lines {where}. "
            f"Read those lines (Read with offset and limit; the ranges add up until the "
            f"file is covered) or run `codegraph node --file {rel}`, then retry the edit."
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
    covered = [span for span in _read_ranges(tool, tool_input, cwd) if _guarded(span[0])]
    if covered:
        receipts = _load(receipts_file)
        for rel, start, end in covered:
            receipts[rel] = _merge([*receipts.get(rel, []), [start, end]])
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
