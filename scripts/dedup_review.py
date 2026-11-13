"""Does this codebase answer the same question in two places?

WHY THIS IS A REVIEWER AND NOT A LINT RULE. The duplication that has actually cost
this project was SEMANTIC, not textual. The tour algorithm existed once for the
phone and once for the workbench; the two shared no lines, no names and no
structure, and reuniting them became its own project (``c8a35a75``). No pattern
scan can see that. A regex, a substring search, even an AST shape hash all answer
"does this text appear twice" — and for the expensive duplicate the answer is no.

The question that finds it is "what is this code FOR?", and answering that needs
reading. So this reads.

WHY IT IS IN THE BUILD AND NOT AN AGENT SOMEONE INVOKES. The thing that keeps
producing this defect is a coding model, and a rule a model has to remember is the
same as no rule. The precedent: prose caps were tried and
died when ``/dev``'s loop counters called a hook that did not exist.

TWO MODES, because one guard cannot be both fast enough to run after every edit and
thorough enough to be believed at the end.

``--changed`` runs after edits. It asks ONE question — does this new or changed
code answer a question the codebase already answers? — against a cached inventory
of what everything else is for. It is advisory ON PURPOSE. A multi-file change is
legitimately incoherent in the middle: this repo's own plan has a step where the
app must raise until the next step lands. A guard that blocks there gets switched
off, and a guard that is off catches nothing. The precedent:
a regex command guard blocked 16 of 20 harmless commands, caught 0 of 70 real
ones, and was deleted.

``--full`` runs at close and BLOCKS. Three passes over everything, and it is what
the pre-commit bar reads. Ignoring the advisory one in the moment is fine.
Ignoring this one is not possible.

THE THREE PASSES, because the middle one is where the reasoning is.

1. NAME THE RESPONSIBILITY. One call per module. For every function, the model
   states in one plain sentence the single question it answers. Bodies are
   included, because a name and a docstring are exactly what a forked
   implementation gets right while diverging underneath.

2. GROUP BY RESPONSIBILITY. One call over the whole inventory — sentences only, no
   bodies, so the model holds the entire codebase at once. This is the pass that
   catches a fork: two entries whose sentences say the same thing, however
   different their code.

3. RULE ON EACH GROUP, with the real bodies, in a separate call. A model grouping
   on one-line summaries WILL over-group; this is the check on it, and it gets the
   evidence so a superficial resemblance can be dismissed on the code.

FAILING CLOSED. A group the ruling pass cannot decide is REPORTED, not passed. An
unreviewable answer is not a clean one.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.tour.anthropic_client import judge_client

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The surfaces that have forked before, or would hurt most if they did. The tour
#: engine is the whole reason this exists; the API layer is where a private preview
#: builder once duplicated the option builder (deleted 2026-08-04).
DEFAULT_ROOTS = ("src/tour", "src/api")

#: What every function is FOR, keyed by module, with the module's hash so an
#: unchanged file is never re-read. Under ``.claude/``, which ``.gitignore``
#: already covers.
CACHE_PATH = REPO_ROOT / ".claude" / "dedup-inventory.json"

#: Reading a whole module and judging what it is for is the expensive half of this
#: job, and a small model reads badly. The full pass runs at close, not in a loop.
#: Cost is not a constraint here.
FULL_MODEL = "claude-opus-4-8"
#: The after-every-edit pass asks a much narrower question — does this one function
#: match anything on a list — and has to answer while somebody is waiting.
CHANGED_MODEL = "claude-haiku-4-5-20251001"

#: A function shorter than this is plumbing — a property, a one-line delegation —
#: and two of them looking alike is not the defect this hunts.
MIN_BODY_LINES = 4


@dataclass(frozen=True)
class Definition:
    module: str
    name: str
    lineno: int
    source: str

    @property
    def ref(self) -> str:
        return f"{self.module}:{self.lineno} {self.name}"


def _watched_files(roots: list[str]) -> list[Path]:
    return [p for root in roots for p in sorted((REPO_ROOT / root).rglob("*.py"))]


def _module_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _definitions(path: Path) -> list[Definition]:
    """Every function in a module, with its real body."""
    text = path.read_text(encoding="utf-8")
    module = path.relative_to(REPO_ROOT).as_posix()
    out: list[Definition] = []
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if (node.end_lineno or node.lineno) - node.lineno < MIN_BODY_LINES:
            continue
        out.append(
            Definition(
                module=module,
                name=node.name,
                lineno=node.lineno,
                source=ast.get_source_segment(text, node) or "",
            )
        )
    return out


def _ask(client: Any, prompt: str, *, model: str, max_tokens: int) -> str:
    """One judge call.

    ``temperature=0`` where the model still takes it, so a borderline verdict does
    not flake between runs and a gate that just passed cannot fail on a re-run.
    The newer reasoning models reject the parameter outright (400,
    "`temperature` is deprecated for this model"), so it is sent only where it is
    accepted rather than guessed at.
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if model == CHANGED_MODEL:
        kwargs["temperature"] = 0
    response = client.messages.create(**kwargs)
    return "".join(
        getattr(block, "text", "") for block in (getattr(response, "content", []) or [])
    ).strip()


def _json_block(text: str) -> Any:
    """The first JSON value in a reply, or None.

    Deliberately tolerant of a model wrapping its answer in prose or a fence: the
    alternative is a guard that fails on formatting rather than on findings.
    """
    for opener, closer in (("[", "]"), ("{", "}")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None


def _load_cache() -> dict[str, Any]:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_cache(cache: dict[str, Any]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")


_RESPONSIBILITY_PROMPT = """\
Below is one Python module from a GPS-triggered audio walking tour engine.

For EVERY function shown, state in ONE plain sentence the single question that
function answers for the system. Write the QUESTION IT ANSWERS, not what it does
mechanically.

Good: "how long does a visitor spend at this place"
Good: "which stops make up this tour, in what order"
Bad:  "iterates the POI list and returns a filtered list"

Two functions that answer the same question must get the SAME sentence, even if
their code looks nothing alike. That is the entire point: matching implementations
is easy and useless; matching responsibilities is what finds a fork.

Reply with JSON only: [{{"name": "...", "answers": "..."}}]

MODULE {module}

{source}
"""

_GROUPING_PROMPT = """\
Below is every function in a tour engine, each with the one question it answers.

Find groups where TWO OR MORE functions answer the SAME question. You are looking
for duplicated RESPONSIBILITY — two places the system decides one thing — not
similar names and not similar code.

Include a group when the same decision is made twice, even in different modules,
even with entirely different implementations. That is the case that matters: this
codebase once had two complete tour algorithms, one serving the phone and one
serving the workbench, sharing no code at all, and they disagreed about the same
tour.

Do NOT include:
- a wrapper and the thing it delegates to (that is reuse, which is the goal)
- functions that merely operate on the same data
- a test double or fake alongside the real thing

Reply with JSON only:
[{{"question": "...", "members": ["module.py:12 name", ...]}}]
An empty list is a valid and welcome answer.

INVENTORY
{inventory}
"""

_RULING_PROMPT = """\
Two or more functions in a tour engine are suspected of answering the same
question. Here is the actual code.

Rule on it. They are DUPLICATE only if the system makes ONE decision in two places
— meaning a change to how that decision is made would have to be made twice, and
forgetting one would let the two disagree.

They are DISTINCT if they answer genuinely different questions, if one delegates to
the other, or if one is a deliberate independent guard on the other (a check
restating what it checks is not a fork).

Suspected shared question: {question}

Reply with JSON only:
{{"verdict": "DUPLICATE" | "DISTINCT", "why": "one sentence",
  "fix": "what to extract and who should call it, or empty when DISTINCT"}}

{bodies}
"""

_CHANGED_PROMPT = """\
A tour engine already answers the questions listed below, each by exactly one
function.

Here is code that was JUST WRITTEN OR CHANGED. For each function in it, decide
whether it answers a question the list already covers — meaning the system would
now decide one thing in two places, and a future change would have to be made
twice.

Ignore a function that CALLS an existing one; that is reuse and it is the goal.
Ignore a function whose own entry is already on the list (it was edited, not
duplicated) unless it now answers a DIFFERENT existing question.

Reply with JSON only:
[{{"name": "...", "already_answered_by": "module.py:12 name", "why": "one sentence"}}]
An empty list is the expected answer most of the time.

QUESTIONS THE CODEBASE ALREADY ANSWERS
{inventory}

JUST CHANGED — {module}
{source}
"""


def _refresh_inventory(client: Any, files: list[Path], cache: dict[str, Any],
                       *, verbose: bool) -> dict[str, Any]:
    """Re-read only the modules whose bytes moved. Returns the updated cache."""
    for path in files:
        module = path.relative_to(REPO_ROOT).as_posix()
        digest = _module_hash(path)
        if cache.get(module, {}).get("sha") == digest:
            continue
        definitions = _definitions(path)
        if not definitions:
            cache[module] = {"sha": digest, "functions": []}
            continue
        reply = _ask(
            client,
            _RESPONSIBILITY_PROMPT.format(
                module=module, source="\n\n".join(d.source for d in definitions)
            ),
            model=FULL_MODEL,
            max_tokens=4096,
        )
        answers = {
            str(e.get("name", "")): str(e.get("answers", ""))
            for e in (_json_block(reply) or [])
        }
        cache[module] = {
            "sha": digest,
            "functions": [
                {"name": d.name, "lineno": d.lineno, "answers": answers[d.name]}
                for d in definitions
                if d.name in answers
            ],
        }
        if verbose:
            print(f"  read {module}: {len(cache[module]['functions'])} function(s)",
                  flush=True)
    return cache


def _inventory_lines(cache: dict[str, Any], *, skip_module: str | None = None) -> list[str]:
    lines: list[str] = []
    for module, entry in sorted(cache.items()):
        if module == skip_module:
            continue
        for fn in entry.get("functions", []):
            lines.append(f"{module}:{fn['lineno']} {fn['name']} — {fn['answers']}")
    return lines


def _run_changed(client: Any, files: list[Path], cache: dict[str, Any]) -> int:
    """The after-every-edit pass. Advisory: always exits 0 (see module docstring)."""
    moved = [p for p in files
             if cache.get(p.relative_to(REPO_ROOT).as_posix(), {}).get("sha")
             != _module_hash(p)]
    if not moved:
        return 0
    if not cache:
        # Nothing to compare against yet. Say so rather than passing silently —
        # a guard with no baseline is not a clean result.
        print("dedup: no inventory yet — run `make dedup-review` once to build it.")
        return 0

    findings: list[str] = []
    for path in moved:
        module = path.relative_to(REPO_ROOT).as_posix()
        definitions = _definitions(path)
        if not definitions:
            continue
        inventory = _inventory_lines(cache, skip_module=module)
        reply = _ask(
            client,
            _CHANGED_PROMPT.format(
                inventory="\n".join(inventory),
                module=module,
                source="\n\n".join(d.source for d in definitions),
            ),
            model=CHANGED_MODEL,
            max_tokens=2048,
        )
        for hit in _json_block(reply) or []:
            findings.append(
                f"  {module} :: {hit.get('name')} may already be answered by "
                f"{hit.get('already_answered_by')}\n      {hit.get('why', '')}"
            )

    if findings:
        print("dedup: possible SECOND ANSWER to a question this codebase already "
              "answers —")
        print("\n".join(findings))
        print("  Call the existing one, or say in a comment why these are genuinely "
              "independent.")
        print("  `make dedup-review` is the blocking version and reads the real code.")
    return 0


def _run_full(client: Any, files: list[Path], cache: dict[str, Any]) -> int:
    print(f"Reading {len(files)} modules for what each function is FOR...", flush=True)
    cache = _refresh_inventory(client, files, cache, verbose=True)
    _save_cache(cache)

    by_ref = {
        f"{module}:{fn['lineno']} {fn['name']}": (module, fn)
        for module, entry in cache.items()
        for fn in entry.get("functions", [])
    }
    inventory = _inventory_lines(cache)
    if not inventory:
        print("Nothing to review.", file=sys.stderr)
        return 2

    print(f"\nLooking for one question answered twice across {len(inventory)} "
          f"functions...", flush=True)
    groups = _json_block(
        _ask(client, _GROUPING_PROMPT.format(inventory="\n".join(inventory)),
             model=FULL_MODEL, max_tokens=4096)
    ) or []
    if not groups:
        print("\nNo duplicated responsibility found.")
        return 0

    print(f"{len(groups)} candidate group(s); ruling on each with the real code.\n",
          flush=True)
    sources = {d.ref: d for path in files for d in _definitions(path)}
    confirmed: list[dict[str, Any]] = []
    unreviewable: list[str] = []

    for group in groups:
        members = [r for r in group.get("members", []) if r in by_ref and r in sources]
        if len(members) < 2:
            continue
        bodies = "\n\n".join(f"### {r}\n{sources[r].source}" for r in members)
        ruling = _json_block(
            _ask(client,
                 _RULING_PROMPT.format(question=group.get("question", ""), bodies=bodies),
                 model=FULL_MODEL, max_tokens=2048)
        )
        if not isinstance(ruling, dict) or "verdict" not in ruling:
            unreviewable.append(", ".join(members))
            continue
        if str(ruling["verdict"]).upper() == "DUPLICATE":
            confirmed.append({**ruling, "members": members})

    if not confirmed and not unreviewable:
        print("No duplicated responsibility survived review.")
        return 0

    for finding in confirmed:
        print(f"DUPLICATE  {finding['why']}")
        for ref in finding["members"]:
            print(f"    {ref}")
        if finding.get("fix"):
            print(f"    fix: {finding['fix']}")
        print()
    for group in unreviewable:
        print(f"UNREVIEWABLE  no verdict for: {group}\n")

    print(f"{len(confirmed)} duplicated responsibility(ies), "
          f"{len(unreviewable)} unreviewable.\n"
          f"Extract the shared decision and have both sites call it. Two answers to "
          f"one question agree until one of them is edited.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Find one question answered twice.")
    parser.add_argument(
        "--changed",
        action="store_true",
        help="review only what moved since the last inventory, against it. Fast and "
        "ADVISORY — always exits 0. This is what runs after an edit.",
    )
    parser.add_argument(
        "--root", action="append", dest="roots",
        help="limit the review to this path (repeatable).",
    )
    args = parser.parse_args()

    files = _watched_files(args.roots or list(DEFAULT_ROOTS))
    if not files:
        print("No Python files to review.", file=sys.stderr)
        return 2

    client = judge_client()
    cache = _load_cache()
    if args.changed:
        return _run_changed(client, files, cache)
    return _run_full(client, files, cache)


if __name__ == "__main__":
    raise SystemExit(main())
