#!/usr/bin/env python3
"""Refuse a plan whose code references were not read: every file:line must resolve.

A plan is built from the walk, and the walk leaves `file:line` citations. A
citation nobody checked is a guess wearing a line number, and guessed plans are
the ones that get rewritten mid-build. This checker turns the plan's own
citations into a gate:

- every `path:N` or `path:N-M` names a file that exists (a bare basename such
  as `trips.py:2182` must resolve to exactly one tracked file);
- a bare `:N` or `:N-M` inherits the last file cited earlier in the same
  paragraph, and fails when there is none;
- the cited range lies inside the file;
- when the same plan line names symbols in backticks (`select_route`), at
  least one of them appears inside the cited lines;
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

FILE_REF_RE = re.compile(
    r"`?(?P<path>[A-Za-z0-9_][A-Za-z0-9_./-]*\.(?:py|dart|html|js|ts|yaml|yml|toml|json|md|txt))"
    r":(?P<start>\d+)(?:-(?P<end>\d+))?`?"
)
BARE_REF_RE = re.compile(r"`:(?P<start>\d+)(?:-(?P<end>\d+))?`")
SYMBOL_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)`")
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
        refs: list[tuple[str | None, int, int]] = []
        for m in FILE_REF_RE.finditer(line):
            rel, err = resolve(m.group("path"), root, tracked)
            if err:
                failures.append(f"{plan}:{lineno}: {err}")
                continue
            last_file = rel
            refs.append((rel, int(m.group("start")), int(m.group("end") or m.group("start"))))
        for m in BARE_REF_RE.finditer(line):
            if last_file is None:
                failures.append(
                    f"{plan}:{lineno}: bare line ref {m.group(0)} with no file cited "
                    "in this paragraph"
                )
                continue
            refs.append((last_file, int(m.group("start")), int(m.group("end") or m.group("start"))))
        symbols = [s for s in SYMBOL_RE.findall(line) if "." not in s]
        for rel, start, end in refs:
            if rel is None:
                continue
            lines = _line_count(root, rel, cache)
            if start < 1 or end < start or end > len(lines):
                failures.append(
                    f"{plan}:{lineno}: {rel}:{start}-{end} is outside the file "
                    f"({len(lines)} lines)"
                )
                continue
            if symbols:
                cited = "\n".join(lines[start - 1 : end])
                if not any(re.search(rf"\b{re.escape(s)}\b", cited) for s in symbols):
                    failures.append(
                        f"{plan}:{lineno}: none of {symbols} appears at {rel}:{start}-{end} "
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
