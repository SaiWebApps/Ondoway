"""Walk the whole tour flow on both surfaces. They must enter each stage by the
same door.

CLAUDE.md rule 1: "The workbench and the app run the EXACT SAME code for
everything they share — routing, planning, authoring, audio: one algorithm, one
construction site, imported by both surfaces."

Written after a divergence shipped with a comment beside it explaining why the
split seemed reasonable: the tourist's audio resolved its voice through the
failover chain (``get_provider_with_fallback``) while the workbench's Listen
button resolved a single provider (``get_provider``). During an outage an editor
would watch a stop fail that the app would have voiced — and the comment made it
read as considered rather than broken.

WHAT IT DOES
------------
It walks the tour from the request arriving to the audio coming out, once for
each surface, and compares the doors.

  1. ASK EACH SURFACE WHAT IT CALLS. The phone's Dart services and the
     workbench's HTML page carry their API addresses as string literals; those
     come out by splitting on quote characters.
  2. MAP EACH ADDRESS TO ITS HANDLER, by parsing the FastAPI route decorators.
  3. WALK THE CALLS from every handler, following the graph.
  4. FOR EACH STAGE of the flow — route selection, planning, audio pipeline,
     voice resolution — collect which functions of that stage's module the
     surface ENTERS from outside the module. Those are its doors.
  5. THE DOOR SETS MUST MATCH.

WHY THIS SHAPE AND NOT A BLUNTER ONE
------------------------------------
Two blunter drafts were written first and both were unusable. Comparing every
reachable function flagged fifty legitimate differences (the phone composes
trips; the workbench lists providers). Hunting wrapper/inner pairs flagged
fifteen, thirteen of them false — `provider.generate(...)` read as a call to a
module function named `generate`. A guard nobody can read gets deleted, and the
owner's ledger records that deleting a noisy guard costs the classes that work.

Counting only ENTRIES INTO A SHARED MODULE FROM OUTSIDE IT is the precise
question: internal plumbing is the module's own business, but the door a surface
knocks on IS the decision. Two doors into one module is the codebase making one
decision two ways.

A stage only counts when BOTH surfaces reach that module at all, so a feature
only one surface has is never mistaken for drift.

NO REGEX (owner ruling 2026-08-29, enforced by no-regex-in-hooks.py). Quote
splitting and `ast` are parses. A pattern would only catch the spellings someone
thought of.
"""

from __future__ import annotations

import ast
import collections
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

PHONE_SOURCES = sorted((REPO / "mobile" / "lib" / "services").glob("*.dart"))
WORKBENCH_SOURCES = sorted((REPO / "frontend").glob("*.html"))
ROUTES_DIR = REPO / "src" / "api" / "routes"

OWNERSHIP = json.loads((REPO / ".agents" / "team" / "ownership.json").read_text())

#: Shared stages come from the same registry the delivery gate validates. Adding a
#: shared capability without adding its surface-parity proof is therefore impossible.
STAGES = tuple(
    (
        capability["id"],
        capability["stage_module"],
        capability["canonical_entrypoint"].split(":", 1)[1],
        capability.get("parity", "CANONICAL_OWNER"),
    )
    for capability in OWNERSHIP["capabilities"]
    if capability["status"] == "ACTIVE"
    and {"mobile", "workbench"}.issubset(capability["consumers"])
)

HTTP_VERBS = ("get", "post", "put", "patch", "delete")
QUOTES = ("'", '"', "`")


# ---------------------------------------------------------------- the surfaces


def _string_literals(text):
    """Every string literal, found by walking the text once.

    A CHARACTER WALK, NOT A SPLIT. Splitting on a quote character treats every
    APOSTROPHE IN PROSE as a delimiter, and a single stray one flips the parity
    of every literal after it, so whole regions of a file become invisible while
    the file still parses and every other guard still passes.

    MEASURED 2026-08-29 on mobile/lib/services/trip_service.dart, whose comments
    say "the phone's" and "the tourist's" — 212 apostrophes, an EVEN count, so
    nothing looked wrong in aggregate. Two of the phone's real endpoints were
    absent from the split's output while sitting verbatim in the file:

        $baseUrl/audio/generate-trip-stops/$tripId
        $baseUrl/trips/$tripId/session/replan

    The second is the living session's replan, and dropping it removed four
    doors into src/tour/contingency.py from the phone's derived surface. This
    guard was therefore reporting agreement on a stage it was not looking at:
    an under-counted surface can only ever produce a PASS, so the failure mode
    is silent by construction. That is the worst shape a guard can have.

    Comments are skipped for the same reason prose broke the split — prose is
    where the stray apostrophes live. Dart and JavaScript share `//` and `/* */`,
    and the workbench's HTML carries its script inline, so one walk reads both
    surfaces. An apostrophe that never closes on its line is not a literal and
    is stepped over; a backtick may legitimately span lines and is not.

    Still no regex (owner ruling 2026-08-29): a character walk and ``str.find``
    are parses, and the walk is exactly what a pattern could not do here — the
    thing that broke the old version was context, not spelling.
    """
    found = []
    index, length = 0, len(text)
    while index < length:
        char = text[index]
        if char == "/" and text[index + 1 : index + 2] == "/":
            line_end = text.find("\n", index)
            index = length if line_end == -1 else line_end + 1
            continue
        if char == "/" and text[index + 1 : index + 2] == "*":
            block_end = text.find("*/", index + 2)
            index = length if block_end == -1 else block_end + 2
            continue
        if char not in QUOTES:
            index += 1
            continue
        cursor, piece = index + 1, []
        while cursor < length and text[cursor] != char:
            if text[cursor] == "\\" and cursor + 1 < length:
                piece.append(text[cursor + 1])
                cursor += 2
                continue
            if text[cursor] == "\n" and char != "`":
                break  # an apostrophe in prose, not an opening quote
            piece.append(text[cursor])
            cursor += 1
        if cursor < length and text[cursor] == char:
            found.append("".join(piece))
            index = cursor + 1
            continue
        index += 1
    return found


def _api_path(literal):
    """The API path a literal requests, interpolations blanked, or None.

    `$baseUrl/audio/stops/$stopId/keep-exploring` -> `/audio/stops/{}/keep-exploring`

    The QUERY STRING is dropped, because a route decorator never carries one:
    `$baseUrl/trips?profile_id=$profileId` addresses the handler registered at
    `/trips`, and keeping the query made it match nothing — the phone's saved-trips
    call was read as a request to a route that does not exist, so the surface it
    reaches went uncounted. Same silent-PASS failure as the parity bug above.
    """
    if "/" not in literal:
        return None
    head, _, rest = literal.partition("/")
    if "$" not in head:  # not a request; a lens name, a CSS class, a message
        return None
    rest = rest.partition("?")[0]
    segments = [
        "{}" if part.startswith("$") or part.startswith("{") else part
        for part in rest.split("/")
        if part
    ]
    return "/" + "/".join(segments) if segments else None


def paths_requested_by(sources):
    paths = set()
    for path in sources:
        for literal in _string_literals(path.read_text(errors="replace")):
            api = _api_path(literal)
            if api:
                paths.add(api)
    return paths


# ------------------------------------------------------------------ the server


def _blank_params(route_path):
    segments = ["{}" if p.startswith("{") else p for p in route_path.split("/") if p]
    return "/" + "/".join(segments)


def _route_paths(node):
    out = []
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        func = decorator.func
        if not (isinstance(func, ast.Attribute) and func.attr in HTTP_VERBS):
            continue
        for arg in decorator.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                out.append(arg.value)
    return out


def _plain_calls(node):
    """`foo(...)` only, never `obj.foo(...)`.

    Method calls are excluded deliberately: counting them conflates a method with
    a module function of the same name, which is how an earlier draft read
    `provider.generate(...)` as a call to a function named `generate`.
    """
    return {
        inner.func.id
        for inner in ast.walk(node)
        if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
    }


def survey():
    """(calls, defined_in, handlers_by_path) read out of src/ in one pass."""
    calls = collections.defaultdict(set)
    defined_in = {}
    handlers = {}
    for path in sorted((REPO / "src").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(errors="replace"))
        except SyntaxError:  # pragma: no cover — a broken tree is a lint failure
            continue
        relative = str(path.relative_to(REPO))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            defined_in.setdefault(node.name, relative)
            calls[node.name] |= _plain_calls(node)
            if path.parent == ROUTES_DIR:
                for route in _route_paths(node):
                    handlers[_blank_params(route)] = node.name
    return calls, defined_in, handlers


# -------------------------------------------------------------------- the walk


def reachable(entries, calls, defined_in):
    """Every function this surface can reach, following the call graph."""
    seen, stack = set(), list(entries)
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        for callee in calls.get(name, ()):
            if callee in defined_in:
                stack.append(callee)
    return seen


def doors_into(module, entries, calls, defined_in):
    """Functions of `module` that are called from OUTSIDE it, on this walk.

    Walks the call graph from `entries`. Whenever a function that does NOT live
    in `module` calls one that does, that callee is a door.
    """
    doors, seen, stack = set(), set(), list(entries)
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        caller_home = defined_in.get(name)
        for callee in calls.get(name, ()):
            if callee not in defined_in:
                continue
            callee_home = defined_in.get(callee)
            if callee_home == module and caller_home != module:
                doors.add(callee)
            stack.append(callee)
    return doors


def test_both_surfaces_enter_every_shared_stage_by_the_same_door():
    """Walk input -> route -> plan -> audio on each surface; the doors must match.

    UNDO TEST: point one surface's route at `get_provider` while the other keeps
    `get_provider_with_fallback` -> RED at the "voice resolution" stage, naming
    both doors. That is the real 2026-08-29 divergence; this test was written
    against it while it was still present and confirmed to fail before the fix.
    """
    assert PHONE_SOURCES, "found no phone service sources; the derivation is broken"
    assert WORKBENCH_SOURCES, "found no workbench sources; the derivation is broken"

    calls, defined_in, handlers = survey()
    assert handlers, "parsed no route decorators; the derivation is broken"

    phone_entries = {handlers[p] for p in paths_requested_by(PHONE_SOURCES) if p in handlers}
    bench_entries = {handlers[p] for p in paths_requested_by(WORKBENCH_SOURCES) if p in handlers}
    assert phone_entries, "no phone request matched a route; the derivation is broken"
    assert bench_entries, "no workbench request matched a route; the derivation is broken"

    problems = []
    phone_reachable = reachable(phone_entries, calls, defined_in)
    bench_reachable = reachable(bench_entries, calls, defined_in)
    for stage, module, owner, parity in STAGES:
        if owner not in phone_reachable:
            problems.append(
                f"  {stage}  [{module}]\n"
                f"      canonical owner: {owner}\n"
                "      THE PHONE CANNOT REACH THE SHARED OWNER"
            )
            continue
        if owner not in bench_reachable:
            problems.append(
                f"  {stage}  [{module}]\n"
                f"      canonical owner: {owner}\n"
                "      THE WORKBENCH CANNOT REACH THE SHARED OWNER"
            )
            continue
        if parity != "SAME_MODULE_DOORS":
            continue
        phone_doors = doors_into(module, phone_entries, calls, defined_in)
        bench_doors = doors_into(module, bench_entries, calls, defined_in)

        # THE RULE IS DIRECTIONAL (owner ruling 2026-08-29): the workbench must
        # match the phone identically, and where the phone does MORE the
        # workbench must do more too. Its entire purpose is to reproduce the
        # tourist's experience and debug it — a stage the tourist's path exercises
        # and the workbench's does not is a stage no editor can ever see go wrong.
        #
        # The reverse is allowed: a workbench-only door (the provider dropdown's
        # list_providers) is editorial furniture with no tourist equivalent, and
        # banning it would be banning the workbench's own UI.
        missing = sorted(phone_doors - bench_doors)
        if missing:
            problems.append(
                f"  {stage}  [{module}]\n"
                f"      the tourist's path reaches:  {sorted(phone_doors)}\n"
                f"      the workbench reaches:       {sorted(bench_doors)}\n"
                f"      THE WORKBENCH CANNOT REACH:  {missing}"
            )

    assert not problems, (
        "THE SURFACES ENTER A SHARED STAGE BY DIFFERENT DOORS (CLAUDE.md rule 1).\n\n"
        + "\n".join(problems)
        + "\n\nOne surface gets behaviour the other does not. Everything the two "
        "share must be ONE function called by both — a second door is a defect "
        "even when it behaves identically today, because the two drift the "
        "moment either is edited.\n"
        f"\n  phone endpoints:     {sorted(phone_entries)}"
        f"\n  workbench endpoints: {sorted(bench_entries)}"
    )


def test_every_registered_capability_owner_exists():
    """The registry cannot claim a single owner that does not exist in production."""
    missing = []
    for capability in OWNERSHIP["capabilities"]:
        if capability["status"] != "ACTIVE":
            continue
        module, symbol = capability["canonical_entrypoint"].split(":", 1)
        path = REPO / (module.replace(".", "/") + ".py")
        if not path.exists():
            missing.append(f"{capability['id']}: missing module {path.relative_to(REPO)}")
            continue
        tree = ast.parse(path.read_text())
        names = {
            node.name
            for node in ast.iter_child_nodes(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        if symbol not in names:
            missing.append(
                f"{capability['id']}: missing symbol {capability['canonical_entrypoint']}"
            )
    assert not missing, "capability ownership registry has dead owners:\n" + "\n".join(missing)
