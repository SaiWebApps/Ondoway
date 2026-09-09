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
- no guess word survives: TBD, likely, probably, "verify later", assuming,
  should, seems, presumably, "appears to".

THE QUOTE IS THE RECEIPT. A citation proves a line number exists; it never
proves the line says what the plan claims, and a line number goes stale the
moment an earlier milestone shifts the file. So a plan line may carry the cited
line VERBATIM:

    - `_reorderRemaining` (`mobile/lib/services/x.dart:1947`) reads: `void _reorderRemaining(...) {`

and the text after `reads:` must equal that line byte for byte, stripped. A
quote cannot be written without reading the line, and it stops matching the
moment the file moves underneath it — which is the drift alarm a one-shot
citation check does not have. Inside a milestone section (`# M<n> …`) a bullet
citing an EXACT line must quote it; a range (`:N-M`) points at a region and
does not.

AND A CLAIM WITHOUT A CITATION IS NOT A CLAIM. A backticked code identifier —
one carrying `_` or a capital — must be paired with a citation on its own line.
Checking only the citations that happen to be present rewards leaving them out,
which is how a plan fills with assertions nobody can check.

Run bare — `python3 .claude/ledger/plan_check.py .claude/runs/<run>/plan.md` —
and present nothing until it exits 0. Takes any number of markdown artifacts,
so a grilling round is gated by the same command as a plan. Stdlib only, agent
tooling under `.claude/` (the `track.py` precedent).
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
GUESS_RE = re.compile(
    r"\b(TBD|likely|probably|verify later|assuming|should|seems|presumably|appears to)\b",
    re.IGNORECASE,
)
FENCE = "```"
#: `… reads: `<the cited line, verbatim>`` — the proof that the line was read.
READS_RE = re.compile(r"\breads:\s*`(?P<quote>[^`]*)`")
#: A milestone section: inside one, a bullet citing an exact line must quote it.
MILESTONE_RE = re.compile(r"^#+\s*M\d+\b")
BULLET_RE = re.compile(r"^\s*[-*+]\s")


#: Language words and short literal values that wear backticks without claiming
#: anything about this repo: `None` is Python's, `FR` is a value inside a quoted
#: string. A symbol carries an underscore or mixed case — `HAS_STOP` still does.
NOT_A_SYMBOL = {"None", "True", "False", "TODO"}


def _is_code_identifier(token: str) -> bool:
    """A backticked token that claims to be code: it carries an underscore or a
    capital. Plain prose words in backticks (`keep`, `map`) claim nothing and are
    left alone; `SessionPlan` and `door_closed` are claims and need a citation."""
    if token in NOT_A_SYMBOL:
        return False
    if len(token) <= 3 and token.isupper() and "_" not in token:
        return False  # a value (`FR`, `PH`), not a symbol
    return "_" in token or any(character.isupper() for character in token)


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
    in_milestone = False
    for lineno, line in enumerate(plan.read_text().splitlines(), start=1):
        if line.strip().startswith(FENCE):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.startswith("#"):
            in_milestone = bool(MILESTONE_RE.match(line))
        if not line.strip():
            last_file = None  # a paragraph break ends the bare-ref inheritance
            continue
        # A verbatim quote is SOURCE, not the plan's own prose. It is blanked out
        # — same length, so every offset below still lines up — before any other
        # check runs, so the quoted code never reads as this plan's citation, its
        # symbol, or its guess word.
        reads = READS_RE.search(line)
        scan = (
            line[: reads.start()] + " " * (reads.end() - reads.start()) + line[reads.end() :]
            if reads
            else line
        )
        for match in GUESS_RE.finditer(scan):
            failures.append(
                f"{plan}:{lineno}: guess word {match.group(1)!r} — resolve it against the code"
            )
        # (file, start, end, position-in-line): the position pairs a citation
        # with the backticked symbol nearest BEFORE it — "`select_route`
        # (`src/x.py:12-40`)" — so a wrapped bullet never checks one clause's
        # symbol against the next clause's lines.
        refs: list[tuple[str, int, int, int]] = []
        #: Spans of backticked paths carrying no line number. They anchor a claim
        #: to a file, and a path is not also a symbol needing its own citation.
        path_spans: list[tuple[int, int]] = []
        for m in FILE_REF_RE.finditer(scan):
            rel, err = resolve(m.group("path"), root, tracked)
            if err:
                failures.append(f"{plan}:{lineno}: {err}")
                continue
            last_file = rel
            refs.append(
                (rel, int(m.group("start")), int(m.group("end") or m.group("start")), m.start())
            )
        for m in BARE_PATH_RE.finditer(scan):
            path_spans.append((m.start(), m.end()))
            rel, err = resolve(m.group("path"), root, tracked)
            if err:
                failures.append(f"{plan}:{lineno}: {err}")
            else:
                last_file = rel
        for m in BARE_REF_RE.finditer(scan):
            if last_file is None:
                failures.append(
                    f"{plan}:{lineno}: bare line ref {m.group(0)} with no file cited "
                    "in this paragraph"
                )
                continue
            start, end = int(m.group("start")), int(m.group("end") or m.group("start"))
            refs.append((last_file, start, end, m.start()))
        symbol_at = [
            (s.start(), s.group(1)) for s in SYMBOL_RE.finditer(scan) if "." not in s.group(1)
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

        # THE QUOTE IS THE RECEIPT: the line is repeated verbatim, so the claim
        # cannot be written without reading it and stops matching when it moves.
        if reads is not None:
            cited_before = [ref for ref in refs if ref[3] < reads.start()]
            if not cited_before:
                failures.append(
                    f"{plan}:{lineno}: `reads:` quotes a line that this line never cites"
                )
            else:
                rel, start, _end, _pos = cited_before[-1]
                lines = _line_count(root, rel, cache)
                if 1 <= start <= len(lines):
                    said, quoted = lines[start - 1].strip(), reads.group("quote").strip()
                    if said != quoted:
                        failures.append(
                            f"{plan}:{lineno}: the quote is not what {rel}:{start} says\n"
                            f"      plan says: {quoted}\n"
                            f"      file says: {said}"
                        )

        # A CODE CLAIM WITH NO CITATION. Checking only the citations that happen
        # to be there rewards leaving them out.
        if not refs and not path_spans:
            uncited = [name for _, name in symbol_at if _is_code_identifier(name)]
            if uncited:
                failures.append(
                    f"{plan}:{lineno}: `{uncited[0]}` is a code claim with no citation on "
                    "this line — cite it, or drop the backticks if it is prose"
                )

        # A MILESTONE'S BULLET THAT NAMES AN EXACT LINE MUST PROVE IT.
        if in_milestone and reads is None and BULLET_RE.match(line):
            exact = [ref for ref in refs if ref[1] == ref[2]]
            if exact:
                rel, start, _end, _pos = exact[0]
                failures.append(
                    f"{plan}:{lineno}: {rel}:{start} names one exact line — quote it with "
                    "`reads:` so the claim proves itself, or cite a range"
                )
    return failures


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: plan_check.py <artifact.md> [artifact.md ...]", file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parent.parent.parent
    total = 0
    for name in argv[1:]:
        plan = Path(name)
        if not plan.is_file():
            print(f"{plan}: no such artifact", file=sys.stderr)
            return 2
        failures = check_plan(plan, root)
        for failure in failures:
            print(failure)
        total += len(failures)
        if not failures:
            print(f"{plan}: every citation resolves.")
    if total:
        print(
            f"{total} unresolved citation(s): not ready to present.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
