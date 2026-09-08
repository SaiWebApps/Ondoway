#!/usr/bin/env python3
"""Refuse a plan whose code references were not read: every file:line must resolve.

A plan is built from the walk, and the walk leaves `file:line` citations. A
citation nobody checked is a guess wearing a line number, and guessed plans are
the ones that get rewritten mid-build. This checker turns the plan's own
citations into a gate:

- every `path:N` or `path:N-M` names a file that exists (a bare basename such
  as `trips.py:2182` must resolve to exactly one tracked file);
- a bare `:N` or `:N-M` inherits the last file cited earlier in the same
  paragraph — with or without a line number — and fails when there is none;
- the cited range lies inside the file;
- the backticked symbol nearest before a citation on the same plan line
  (`select_route` (`src/x.py:12-40`)) appears inside the cited lines, or
  the cited lines sit inside that symbol's own definition;
- no guess word survives: TBD, likely, probably, "verify later", assuming.

Run bare — `python3 .claude/ledger/plan_check.py .claude/runs/<run>/plan.md` —
and present nothing until it exits 0. Stdlib only, agent tooling under
`.claude/` (the `track.py` precedent).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_PATH = (
    r"(?P<path>\.?[A-Za-z0-9_][A-Za-z0-9_./-]*"
    r"\.(?:py|dart|html|js|ts|yaml|yml|toml|json|md|txt|sh)"
    r"|(?:[A-Za-z0-9_./-]*/)?(?:Makefile|Dockerfile))"
)
FILE_REF_RE = re.compile(r"`?" + _PATH + r":(?P<start>\d+)(?:-(?P<end>\d+))?`?")
#: A backticked path with no line number still names the paragraph's file, so a
#: bare `:N` after it has somewhere to resolve.
BARE_PATH_RE = re.compile(r"`" + _PATH + r"`")
BARE_REF_RE = re.compile(r"`:(?P<start>\d+)(?:-(?P<end>\d+))?`")
SYMBOL_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)`")
DEF_RE = re.compile(r"^(?P<indent>\s*)(?:async\s+)?(?:def|class)\s+(?P<name>[A-Za-z_]\w*)\b")
GUESS_RE = re.compile(r"\b(TBD|likely|probably|verify later|assuming)\b", re.IGNORECASE)
FENCE = "```"


def tracked_files(root: Path) -> list[str]:
    proc = subprocess.run(["git", "ls-files"], capture_output=True, text=True, cwd=root)
    return proc.stdout.splitlines()


def resolve(path: str, root: Path, tracked: list[str]) -> tuple[str | None, str | None]:
    """(repo-relative path, error). A full path wins; else a unique suffix match."""
    if (root / path).is_file():
        return path, None
    matches = [f for f in tracked if f == path or f.endswith("/" + path)]
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        return None, f"{path} names no tracked file"
    return None, f"{path} is ambiguous: " + ", ".join(sorted(matches))


def _line_count(root: Path, rel: str, cache: dict[str, list[str]]) -> list[str]:
    if rel not in cache:
        cache[rel] = (root / rel).read_text(errors="replace").splitlines()
    return cache[rel]


def _symbol_holds(symbol: str, lines: list[str], start: int, end: int) -> bool:
    """The symbol appears inside the cited lines, or the cited lines lie inside
    the symbol's own definition (a range in a function's body cites that
    function). A range inside some OTHER definition never counts."""
    cited = "\n".join(lines[start - 1 : end])
    if re.search(rf"\b{re.escape(symbol)}\b", cited):
        return True
    owner: tuple[int, int] | None = None  # (line, indent) of the enclosing def
    for lineno in range(start - 1, 0, -1):
        m = DEF_RE.match(lines[lineno - 1])
        if m is None:
            continue
        indent = len(m.group("indent"))
        if owner is None or indent < owner[1]:
            if m.group("name") == symbol:
                return not any(
                    (d := DEF_RE.match(lines[i - 1])) and len(d.group("indent")) <= indent
                    for i in range(lineno + 1, end + 1)
                )
            owner = (lineno, indent)
            if indent == 0:
                break
    return False


def check_plan(plan: Path, root: Path) -> list[str]:
    tracked = tracked_files(root)
    cache: dict[str, list[str]] = {}
    failures: list[str] = []
    last_file: str | None = None
    in_fence = False
    for lineno, line in enumerate(plan.read_text().splitlines(), start=1):
        if line.strip().startswith(FENCE):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not line.strip():
            last_file = None  # a paragraph break ends the bare-ref inheritance
            continue
        for match in GUESS_RE.finditer(line):
            failures.append(
                f"{plan}:{lineno}: guess word {match.group(1)!r} — resolve it against the code"
            )
        # (file, start, end, position-in-line): the position pairs a citation
        # with the backticked symbol nearest BEFORE it — "`select_route`
        # (`src/x.py:12-40`)" — so a wrapped bullet never checks one clause's
        # symbol against the next clause's lines.
        refs: list[tuple[str, int, int, int]] = []
        for m in FILE_REF_RE.finditer(line):
            rel, err = resolve(m.group("path"), root, tracked)
            if err:
                failures.append(f"{plan}:{lineno}: {err}")
                continue
            last_file = rel
            refs.append(
                (rel, int(m.group("start")), int(m.group("end") or m.group("start")), m.start())
            )
        for m in BARE_PATH_RE.finditer(line):
            rel, err = resolve(m.group("path"), root, tracked)
            if err:
                failures.append(f"{plan}:{lineno}: {err}")
            else:
                last_file = rel
        for m in BARE_REF_RE.finditer(line):
            if last_file is None:
                failures.append(
                    f"{plan}:{lineno}: bare line ref {m.group(0)} with no file cited "
                    "in this paragraph"
                )
                continue
            start, end = int(m.group("start")), int(m.group("end") or m.group("start"))
            refs.append((last_file, start, end, m.start()))
        symbol_at = [
            (s.start(), s.group(1)) for s in SYMBOL_RE.finditer(line) if "." not in s.group(1)
        ]
        for rel, start, end, pos in refs:
            lines = _line_count(root, rel, cache)
            if start < 1 or end < start or end > len(lines):
                failures.append(
                    f"{plan}:{lineno}: {rel}:{start}-{end} is outside the file "
                    f"({len(lines)} lines)"
                )
                continue
            before = [name for at, name in symbol_at if at < pos]
            symbol = before[-1] if before else None
            if symbol is not None and not _symbol_holds(symbol, lines, start, end):
                failures.append(
                    f"{plan}:{lineno}: `{symbol}` is not at {rel}:{start}-{end} "
                    "— stale or guessed citation"
                )
    return failures


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: plan_check.py <plan.md>", file=sys.stderr)
        return 2
    plan = Path(argv[1])
    if not plan.is_file():
        print(f"{plan}: no such plan", file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parent.parent.parent
    failures = check_plan(plan, root)
    for failure in failures:
        print(failure)
    if failures:
        print(
            f"{len(failures)} unresolved citation(s): the plan is not ready to present.",
            file=sys.stderr,
        )
        return 1
    print(f"{plan}: every citation resolves.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
