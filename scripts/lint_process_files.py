"""Lint the self-describing files: no dangling references, no dated scars, no dead names.

Process files — CLAUDE.md, the agent/command/rule definitions under .claude/,
settings.json, .gitignore, the Makefile — describe the repo to every session
that works on it, and Docs/ plus README.md describe it to every human. The
defects this lint refuses, each with a file:line:

- a reference to a path that no longer exists, left behind when the thing it
  named was deleted (process files, Docs/ and README.md);
- an incident narrative (recognized by its ISO date) baked into a rule, which
  states history instead of the present constraint (process files only — a
  document may record history; incidents go to git history and
  .claude/LEARNINGS.md, which is exempt as the incident log);
- a backticked `make <target>` naming a target the live Makefile does not
  define (every scanned markdown file);
- a `specs/<path>` citation in src/ or scripts/ Python — the spec tree is
  retired, so a path citation there points at fixtures/ or at git history.

Four things a reference scan over prose must NOT flag: a `~`-prefixed home
path (not a repo claim), a gitignored path (a machine-local artifact, judged
by `git check-ignore`), a line that narrates a removal (the one sentence
allowed to name a thing that is gone), and a `./`-prefixed path (a planned
artifact prose says it *creates*, not a claim that the repo already has it).

Stdlib only; run as `uv run python scripts/lint_process_files.py`.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

#: Scanned for BOTH checks: prose rules where a date is a scar and a path is a claim.
MD_GLOBS = (
    "CLAUDE.md",
    ".claude/agents/*.md",
    ".claude/commands/*.md",
    ".claude/rules/*.md",
)

#: Scanned for dangling references only.
REF_ONLY_FILES = (".claude/settings.json", "Makefile")

#: Markdown scanned for dangling references and make targets, never for dates:
#: documentation may record history; it may not describe a repo that is gone.
DOC_GLOBS = ("README.md", "Docs/**/*.md")

#: Python scanned for retired-spec-tree citations.
PY_GLOBS = ("src/**/*.py", "scripts/**/*.py")

#: Repo directories a bare `some/path` token may refer to. A token outside
#: these (an URL fragment, `and/or` prose) is not a repo claim.
TOP_DIRS = (
    "src",
    "tests",
    "scripts",
    "fixtures",
    "docs",
    "data",
    "mobile",
    "frontend",
    "config",
    "Books",
    ".claude",
)

#: Documented example paths that name no real file on purpose.
EXAMPLE_PATHS = frozenset({"tests/test_x.py"})

DATE_RE = re.compile(r"\b20\d{2}-\d{2}\b")
#: A dated freshness stamp is a record by design, like a log entry — not a scar.
DATED_FIELD_RE = re.compile(r"^\s*last_verified:")
#: A token carrying any of these — or an `...` ellipsis — is a template, glob,
#: or make-variable, not a claim.
PLACEHOLDER_RE = re.compile(r"[{}<>*…$]|\.\.\.")
TOKEN_RE = re.compile(r"[^\s`'\"]+")
PATH_CHARS_RE = re.compile(r"[\w\-./]+")
_STRIP = ".,;:()[]"

#: A backticked make invocation: the word right after `make is the target claim.
MAKE_TARGET_RE = re.compile(r"`make\s+([A-Za-z][\w-]*)")
#: A Makefile rule line: one or more target names before an un-assigned colon.
MAKEFILE_RULE_RE = re.compile(r"^([A-Za-z][\w-]*(?:\s+[A-Za-z][\w-]*)*)\s*:(?!=)")
#: A citation into the retired spec tree — `specs/` followed by a path segment.
#: Bare `specs/` (a removal narration, or the certification remap tuple) is not
#: a citation and stays legal.
SPECS_CITATION_RE = re.compile(r"specs/[\w-]")
#: A line that narrates a removal is the one sentence allowed to name what is
#: gone; the reference scan on Docs/README skips it.
REMOVAL_NARRATION_RE = re.compile(r"\b(deleted|retired|removed|git history)\b", re.IGNORECASE)


def find_dates(text: str) -> list[str]:
    """Every ISO year-month in the text — the marker of an incident narrative."""
    return DATE_RE.findall(text)


def is_planned_path(token: str) -> bool:
    """True when ``token`` is a `./`-prefixed path.

    Prose that says a change *creates* `./src/ingest/model.py` is describing a
    planned artifact, not claiming the repo already has it — so it must never
    be treated as a dangling reference just because the file doesn't exist yet.
    """
    return token.startswith("./")


def find_refs(text: str) -> set[str]:
    """Every repo-relative path the text claims exists.

    A claim is a whole token that either contains `.claude/` or starts with a
    known top-level directory and has a `/`. Tokens carrying placeholder or
    glob characters, characters a repo path cannot hold, or a `./` planned-path
    prefix, make no claim.
    """
    refs: set[str] = set()
    for raw in TOKEN_RE.findall(text):
        # Right-strip punctuation only: the leading "." of ".claude/" is path.
        token = raw.lstrip("([").rstrip(_STRIP)
        if PLACEHOLDER_RE.search(token):
            continue
        if token.startswith("~"):
            continue  # a home path is never a repo claim
        if is_planned_path(token):
            continue  # a planned artifact, not a claim the repo already has it
        if ".claude/" in token:
            token = token[token.index(".claude/") :]
        elif not any(token.startswith(top + "/") for top in TOP_DIRS):
            continue
        if not PATH_CHARS_RE.fullmatch(token):
            continue
        ref = token.rstrip("/").rstrip(_STRIP)
        if ref and ref not in EXAMPLE_PATHS:
            refs.add(ref)
    return refs


def _dangling_refs(root: Path, rel: str, lineno: int, line: str) -> list[tuple[str, int, str]]:
    """(file, line, ref) for every claimed path that does not exist — before the
    gitignore filter, which runs once per repo scan rather than once per line."""
    return [
        (rel, lineno, ref)
        for ref in sorted(find_refs(line))
        if not (root / ref).exists()
    ]


def _gitignored(root: Path, refs: set[str]) -> set[str]:
    """The subset of ``refs`` that git ignores — machine-local artifacts, not
    repo claims. Outside a git checkout (or without git) nothing is ignored,
    so every dangling reference still stands."""
    if not refs:
        return set()
    try:
        proc = subprocess.run(
            ["git", "check-ignore", "--stdin"],
            input="\n".join(sorted(refs)),
            capture_output=True,
            text=True,
            cwd=root,
        )
    except OSError:
        return set()
    return set(proc.stdout.splitlines())


def makefile_targets(root: Path) -> frozenset[str]:
    """Every target name the live Makefile defines."""
    makefile = root / "Makefile"
    if not makefile.is_file():
        return frozenset()
    targets: set[str] = set()
    for line in makefile.read_text().splitlines():
        match = MAKEFILE_RULE_RE.match(line)
        if match:
            targets.update(match.group(1).split())
    return frozenset(targets)


def _make_target_violations(rel: str, lineno: int, line: str, targets: frozenset[str]) -> list[str]:
    return [
        f"{rel}:{lineno}: unknown make target — `make {name}` names no target "
        f"in the live Makefile"
        for name in MAKE_TARGET_RE.findall(line)
        if name not in targets
    ]


def _scan_lines(path: Path):
    yield from enumerate(path.read_text().splitlines(), start=1)


def check_repo(root: Path) -> list[str]:
    """Every violation in the repo's self-describing files, as `path:line: message`."""
    violations: list[str] = []
    pending_refs: list[tuple[str, int, str]] = []
    targets = makefile_targets(root)

    for pattern in MD_GLOBS:
        for path in sorted(root.glob(pattern)):
            rel = path.relative_to(root).as_posix()
            in_fence = False
            for lineno, line in _scan_lines(path):
                if line.lstrip().startswith("```"):
                    in_fence = not in_fence
                # A date inside a code fence or a dated metadata field is
                # example data, not a rule narrating its own history.
                if not in_fence and not DATED_FIELD_RE.match(line):
                    violations += [
                        f"{rel}:{lineno}: dated scar — {date} states an incident, "
                        f"not a present constraint (move it to git history or LEARNINGS.md)"
                        for date in find_dates(line)
                    ]
                pending_refs += _dangling_refs(root, rel, lineno, line)
                violations += _make_target_violations(rel, lineno, line, targets)

    for pattern in DOC_GLOBS:
        for path in sorted(root.glob(pattern)):
            rel = path.relative_to(root).as_posix()
            for lineno, line in _scan_lines(path):
                # No dated-scar check: documentation may record history. The
                # removal-narration skip is the flip side of the same rule —
                # the one line allowed to name what is gone is the one that
                # says so.
                if not REMOVAL_NARRATION_RE.search(line):
                    pending_refs += _dangling_refs(root, rel, lineno, line)
                violations += _make_target_violations(rel, lineno, line, targets)

    for name in REF_ONLY_FILES:
        path = root / name
        if path.is_file():
            for lineno, line in _scan_lines(path):
                pending_refs += _dangling_refs(root, name, lineno, line)

    for pattern in PY_GLOBS:
        for path in sorted(root.glob(pattern)):
            rel = path.relative_to(root).as_posix()
            for lineno, line in _scan_lines(path):
                if SPECS_CITATION_RE.search(line):
                    violations.append(
                        f"{rel}:{lineno}: specs/ citation — the spec tree is retired; "
                        f"point at fixtures/ or at git history"
                    )

    gitignore = root / ".gitignore"
    if gitignore.is_file():
        for lineno, line in _scan_lines(gitignore):
            stripped = line.strip()
            if stripped.startswith("!"):
                target = stripped[1:].rstrip("/")
                if not PLACEHOLDER_RE.search(target) and not (root / target).exists():
                    violations.append(
                        f".gitignore:{lineno}: dangling reference — "
                        f"negation for {target}, which does not exist"
                    )
            elif stripped.startswith("#"):
                pending_refs += _dangling_refs(root, ".gitignore", lineno, stripped)

    ignored = _gitignored(root, {ref for _, _, ref in pending_refs})
    violations += [
        f"{rel}:{lineno}: dangling reference — {ref} does not exist"
        for rel, lineno, ref in pending_refs
        if ref not in ignored
    ]

    return violations


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    violations = check_repo(root)
    for violation in violations:
        print(violation)
    if violations:
        print(f"{len(violations)} process-file violation(s).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
