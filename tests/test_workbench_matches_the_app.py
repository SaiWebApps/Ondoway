"""THE WORKBENCH MUST BEHAVE LIKE THE REAL APP — NO MOCKS, EVER.

OWNER RULING, 2026-07-31: "We need stronger guards or tests to prevent Claude or
ChatGPT from ever trying to spoof anything in the workbench. No mocks ever."

The editorial workbench exists for one reason: so the owner can judge the product
a TOURIST gets. Anything that makes it resolve a different implementation than
production makes that judgement worthless. This repo already had the rule for
NARRATION ("never mock in the workbench: wiring mock as the workbench default and
calling it real is the specific lie that destroyed trust"). Nobody extended it to
AUDIO, and that is the hole the owner found in two screenshots on 2026-07-31:

1. the audio provider selector was sitting on ``mock`` — ``loadTtsProviders``
   force-selected it after fetching the provider list, silently overriding the
   ``openai`` default, so every "play" was a silent WAV; and
2. ``generateTourPreview`` opened a ``window.confirm`` spend warning before every
   preview ("...spends real money on your API key"), which no tourist ever sees
   and which also blocks any automated demo run.

THE INVARIANT THESE TESTS ENCODE: whatever the workbench causes the API to do, it
must resolve THE SAME REAL IMPLEMENTATIONS PRODUCTION RESOLVES.

Every guard here DERIVES its expectation. There is deliberately no list of
forbidden names anywhere in this file, and no list of *permitted* ones either: a
guard that names "mock" only catches something called mock, and the next spoof
will be called replay, offline, stub, fixture, sample or studio.

No regex, no greps, no allowlists, no denylists (owner-banned). Parsing is done
with ``ast`` and plain string operations, and every derivation asserts it found
something before asserting anything is absent, so a broken parser fails loudly
instead of passing vacuously.

WHERE THE EXPECTATIONS COME FROM (the four sources of truth):

* ``render.yaml`` — what production pins. Never a hand-typed copy of it.
* the ``Protocol`` classes in ``src/`` — which methods are the product's output.
* ``socket``/``ipaddress`` — whether an address is a real upstream or this
  machine talking to itself. NOT a list of loopback names: the first version of
  this file carried ``{"localhost", "127.0.0.1", "0.0.0.0", "::1"}`` and a stub
  on ``127.0.0.2``, ``tts.localhost``, ``host.docker.internal`` or a LAN address
  read as "external" to all four of them.
* the process itself — a real request, with the socket layer denied, so a
  provider that returns bytes without attempting egress is caught no matter
  which module installed the substitution.

WHAT THIS FILE CANNOT CATCH (human must watch — do not delete this list):

* **A REUSED SERVER.** ``scripts/workbench.sh`` reuses anything already
  answering ``/healthz`` on :8000 and then NONE of its env block runs. A server
  started by ``make api``, ``make flutter-ios``, a sibling session or a bare
  ``uv run uvicorn`` sets no ``TTS_PROVIDER`` and lands on ``get_provider``'s
  "mock" fallback. Every guard here reads files and this process; none of them
  can see a process started an hour ago by another command. THE FIX IS PRODUCT,
  NOT TEST: the page must display the identity the server resolved, so a
  screenshot carries the proof. Until it does, the human must confirm the
  workbench log line "API PID ..." appeared (a reused server prints "reusing it"
  instead).
* **THE PREVIEW ROUTE IS NOT THE TOURIST'S ROUTE.** ``ttsPlay`` POSTs to
  ``/audio/preview``, which serves from an in-process cache; the tourist's app
  gets audio from ``generate_stop_audio``, which writes to storage. NEITHER
  TRUNCATES ANY MORE — the preview's character cap was deleted on 2026-08-04
  (it was an abuse bound on an anonymous endpoint, never a quality setting), so
  the one length divergence between the two paths is closed. What remains is
  still a genuine difference in plumbing — a cache and a memory buffer on one
  side, a storage write on the other — and only an output-equality run (same
  narration through both, compare bytes and duration) would prove the audio
  itself matches. That needs a live provider and is not a $0 check.
* **PRE-WRITTEN STORAGE ARTIFACTS.** ``/audio/generate-trip-stops`` skips
  regeneration whenever an item already has an ``audio_url`` whose artifact
  exists. Files hand-placed under ``audio_store/`` are indistinguishable from
  generated ones by any static or in-process check.
* **A CACHE ON THE COMPOSE PATH.** Test 4 proves nothing is pre-seeded at
  import, and test 3 proves a fresh Listen really leaves the machine, but a
  cache that fills on the FIRST call and replays on the second is only visible
  by issuing the same ``/trips/preview`` twice against a live server and
  requiring two different ``provider_request_id`` values. That spends money.
* **VOICE/MODEL AS A CLASS CONSTANT.** ``DEFAULT_MODEL = "tts-1-hd"`` is shared
  by workbench and production, so downgrading it is not a workbench-vs-prod
  divergence at all — it is a product change and no parity check can see it.
  Test 7 does catch the env-var half (``OPENAI_VOICE`` set only locally).
* **THE PREVIEW STOP CAP — CLOSED 2026-08-04, and test 14 now guards the
  closure.** Until that date ``certification_planning_policy`` capped the preview
  at 8 stops while the persisted path allowed 15, so the workbench structurally
  could not show a tour as long as one a tourist could be given. OWNER RULING 5
  ("no stop limits, period") removed BOTH, and every other ceiling with them.
  Duration is now the only bound on tour length on every surface, including the
  quality scorer. Test 14 measures that a long request really does seat more than
  15 stops, and pins the tractability fallback that replaced the ceilings' one
  useful side effect — keeping the exponential order solver off a 25-stop route.
* **RUNTIME DOM BEHAVIOUR.** Tests 12 and 13 read the selection/disclosure code
  rather than executing it. Only the Playwright shard
  (``make test-workbench``) executes this page.
* **A DIVERGENCE THAT NEEDS REAL DATA TO APPEAR.** Tests 16-19 drive all four
  real HTTP handlers, but against a fixed synthetic corpus, a stand-in graph and
  a stand-in walking-times service. That is deliberate — it is what makes "the
  two surfaces produced the same tour" a statement about CODE rather than about
  whether the corpus moved between two calls — but it means a fault that only
  shows on live Paris content, or only against a real Valhalla answer, is
  outside their reach. The live-corpus behaviour belongs to the tour-invariant
  and golden shards.

Cost: $0 and hermetic. File reads, in-process imports, and real requests served
by the app in this process: one with the socket layer denied before it can open,
and four planning/authoring calls whose graph, corpus and routing upstream are
all stood in for. No DB, no container, no browser, no provider call, no money.
It does one DNS lookup per distinct upstream host named in ``src/`` (to classify
the address, never to reach it).
"""

from __future__ import annotations

import ast
import contextlib
import importlib
import ipaddress
import os
import pathlib
import secrets
import socket
import time
from functools import cache
from typing import Any
from urllib.parse import urlparse

REPO = pathlib.Path(__file__).resolve().parents[1]
SRC = REPO / "src"
REVIEW_HTML = REPO / "frontend" / "review.html"
RENDER_YAML = REPO / "render.yaml"
WORKBENCH_SH = REPO / "scripts" / "workbench.sh"
MAKEFILE = REPO / "Makefile"
PROFILE_DIR = REPO / "config" / "profiles"

# The human-facing surfaces of the workbench: planning the day, writing the
# tour, and hearing it. ``authorTourDay`` joined at Phase 4 (design §8.1): the
# pick-one-of-three flow died, so the ONE "Write the tour" action is now the
# workbench's paid call — exactly where a spend gate would go back first.
TOURIST_FACING_FUNCTIONS = (
    "async function generateTourPreview()",
    "async function authorTourDay()",
    "async function loadTtsProviders()",
    "async function ttsPlay(",
)


# --------------------------------------------------------------------------
# derivation helpers — addresses
# --------------------------------------------------------------------------


@cache
def _host_is_external(host: str) -> bool:
    """True when ``host`` is a real upstream rather than this machine.

    Derived from the ADDRESS, not from any list of names. An IP literal is
    classified directly by :mod:`ipaddress`; a name is resolved and EVERY
    resolved address must be globally routable. That covers loopback in all its
    forms (``127.0.0.2``, ``::1``, ``::ffff:127.0.0.1``, ``tts.localhost``),
    RFC1918 (``192.168.x``, ``host.docker.internal``), link-local and the
    unspecified address, with nothing to maintain when the next alias appears.

    Fails LOUDLY on an unresolvable name rather than guessing: "I could not
    prove this reaches a real service" must never read as "it does".
    """
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise AssertionError(
            f"cannot classify {host!r}: name resolution failed ({exc}). This guard "
            f"decides whether an implementation reaches a real upstream by looking "
            f"at the addresses its endpoint resolves to, so an unresolvable host is "
            f"an UNPROVEN one. Either the host is bogus (a spoof) or this machine "
            f"has no DNS — check the network before touching this test."
        ) from exc
    addresses = {info[4][0] for info in infos}
    assert addresses, f"{host!r} resolved to nothing at all"
    return all(ipaddress.ip_address(addr).is_global for addr in addresses)


def _is_external_endpoint(value: object) -> bool:
    """True if ``value`` is an http(s) URL pointing at a real upstream host."""
    if not isinstance(value, str) or "://" not in value:
        return False
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https"):
        return False
    return parsed.hostname is not None and _host_is_external(parsed.hostname)


# --------------------------------------------------------------------------
# derivation helpers — python structure
# --------------------------------------------------------------------------


@cache
def _source_modules() -> tuple[tuple[pathlib.Path, ast.Module], ...]:
    """Every module under ``src/``, parsed once."""
    parsed = []
    for path in sorted(SRC.rglob("*.py")):
        parsed.append((path, ast.parse(path.read_text())))
    assert parsed, "found no python modules under src/ — the walk is broken"
    return tuple(parsed)


def _module_classes(tree: ast.Module) -> dict[str, ast.ClassDef]:
    return {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}


def _module_functions(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


@cache
def _environment_accessors(tree: ast.Module) -> tuple[frozenset[str], frozenset[str]]:
    """How THIS module spells "read the environment", from its own imports.

    MEMOISED PER MODULE, and that is not an optimisation nicety — it is the
    difference between this file finishing and the python shard being killed.
    The answer depends only on the module's own import statements, but it was
    recomputed by walking the WHOLE module tree again for EVERY node the
    callers below visit, which made ``_implementation_dispatchers`` quadratic in
    module size: MEASURED at 185s per call, three calls in this file, ~9 minutes
    of a shard that is killed at 15. Cached it is ~1s per call. The key is the
    parsed module object itself, which ``_source_modules`` already hands out
    once and keeps, so identity is stable and the cache is bounded by the number
    of modules under ``src/``. Same inputs, same answer — nothing is skipped.

    Returns the call-form spellings (``os.getenv``, ``environ.get``, a bare
    ``getenv``) and the subscript-form ones (``os.environ[...]``). Derived per
    module rather than hardcoded, because ``import os as _os`` — or
    ``from os import getenv`` — otherwise walks straight past a scan that only
    knows the literal string "os.getenv". That is not hypothetical: the first
    version of this file hardcoded it, and a mutation that aliased the import
    passed the env-conditioned-return guard cleanly.
    """
    calls: set[str] = set()
    subscripts: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os":
                    local = alias.asname or "os"
                    calls |= {f"{local}.getenv", f"{local}.environ.get"}
                    subscripts.add(f"{local}.environ")
        elif isinstance(node, ast.ImportFrom) and node.module == "os":
            for alias in node.names:
                local = alias.asname or alias.name
                if alias.name == "getenv":
                    calls.add(local)
                elif alias.name == "environ":
                    calls.add(f"{local}.get")
                    subscripts.add(local)
    return frozenset(calls), frozenset(subscripts)


def _reads_env_call(node: ast.AST, tree: ast.Module) -> bool:
    """Whether this node reads the environment, however the module spells it."""
    calls, subscripts = _environment_accessors(tree)
    if isinstance(node, ast.Call):
        return ast.unparse(node.func) in calls
    if isinstance(node, ast.Subscript):
        return ast.unparse(node.value) in subscripts
    return False


def _env_read_name(node: ast.AST, tree: ast.Module) -> str | None:
    """The variable NAME an environment read asks for, or None.

    Resolves a module constant used as the name (``os.getenv(_ADMIN_GATE_ENV)``)
    against the module's own assignments, so indirection through a constant is
    not a way to disappear from a read-site scan.
    """
    if not _reads_env_call(node, tree):
        return None
    if isinstance(node, ast.Subscript):
        first: ast.expr | None = node.slice
    else:
        first = node.args[0] if node.args else None  # type: ignore[union-attr]
    if first is None:
        return None
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    if isinstance(first, ast.Name):
        for stmt in tree.body:
            if (
                isinstance(stmt, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == first.id for t in stmt.targets)
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)
            ):
                return stmt.value.value
    return None


def _discover_registries() -> list[tuple[pathlib.Path, str, dict[str, ast.ClassDef]]]:
    """Find every name -> implementation-class registry defined in ``src/``.

    Structural, not by name: a module-level assignment of a dict literal whose
    keys are all string constants and whose values are all classes defined in
    that same module. That is what a swappable-implementation registry looks
    like, whatever the variable ends up being called.
    """
    registries: list[tuple[pathlib.Path, str, dict[str, ast.ClassDef]]] = []
    for path, tree in _source_modules():
        classes = _module_classes(tree)
        if not classes:
            continue
        for node in tree.body:
            if isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            elif isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            else:
                continue
            if not isinstance(value, ast.Dict) or not value.keys:
                continue
            if not all(
                isinstance(k, ast.Constant) and isinstance(k.value, str) for k in value.keys
            ):
                continue
            if not all(isinstance(v, ast.Name) and v.id in classes for v in value.values):
                continue
            variable = getattr(targets[0], "id", "<unnamed>")
            mapping = {
                key.value: classes[val.id]  # type: ignore[union-attr]
                for key, val in zip(value.keys, value.values, strict=True)
            }
            registries.append((path, variable, mapping))
    return registries


def _protocol_product_methods(tree: ast.Module) -> dict[str, list[str]]:
    """Per Protocol class in this module, the methods that produce its output.

    Derived from the Protocol's OWN ast, not from a name this test invented:
    a ``@property`` (like ``name``) reports an identity, while an undecorated
    method (like ``generate`` or ``upload``) is the contract's work. Scoping the
    realness probe to these is what stops a class parking a genuine, actually
    executing call to api.openai.com in some unrelated ``healthcheck`` method
    while ``generate`` hands back a fixture.
    """
    protocols: dict[str, list[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        base_names = {b.id for b in node.bases if isinstance(b, ast.Name)}
        if "Protocol" not in base_names:
            continue
        protocols[node.name] = [
            item.name
            for item in node.body
            if isinstance(item, ast.FunctionDef) and not item.decorator_list
        ]
    return protocols


def _callee_definition(call: ast.Call, tree: ast.Module) -> ast.FunctionDef | None:
    """Resolve a call's callee to a module-level ``def`` in the same module."""
    func = call.func
    if isinstance(func, ast.Name):
        return _module_functions(tree).get(func.id)
    return None


def _url_position_argument(call: ast.Call, tree: ast.Module) -> ast.expr | None:
    """The argument bound to the callee's FIRST parameter — the URL slot.

    The position is read off the callee's own ``def`` when it lives in this
    module (``post_with_retry(url, *, headers, json, ...)`` — first parameter),
    so a keyword-form call binds correctly too. When the callee cannot be
    resolved in-repo the first positional argument is used, which is the same
    slot in ``httpx.post``/``requests.post``.

    Why this matters: the previous version of this check accepted the endpoint
    appearing in ANY argument, so ``_post_with_retry(self.STUB_URL,
    headers={"X-Upstream": self.API_URL})`` posted to a local stub while the
    decorative header satisfied the guard.
    """
    definition = _callee_definition(call, tree)
    if definition is not None:
        params = [a.arg for a in definition.args.posonlyargs + definition.args.args]
        if params and params[0] in ("self", "cls"):
            params = params[1:]
        if not params:
            return None
        first = params[0]
        for keyword in call.keywords:
            if keyword.arg == first:
                return keyword.value
        return call.args[0] if call.args else None
    return call.args[0] if call.args else None


def _class_endpoint_attributes(node: ast.ClassDef) -> set[str]:
    """Class-level constants that hold a real upstream endpoint."""
    attrs: set[str] = set()
    for stmt in node.body:
        if (
            isinstance(stmt, ast.Assign)
            and isinstance(stmt.value, ast.Constant)
            and _is_external_endpoint(stmt.value.value)
        ):
            attrs |= {t.id for t in stmt.targets if isinstance(t, ast.Name)}
    return attrs


def _expression_names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _expression_attributes(node: ast.AST) -> set[str]:
    return {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}


def _method_output_comes_from_upstream(
    method: ast.FunctionDef, endpoint_attrs: set[str], tree: ast.Module
) -> bool:
    """True when EVERY value this method returns flows out of an upstream call.

    Three properties, all required, all derived:

    1. some call inside the method receives a real upstream endpoint IN THE URL
       POSITION (see :func:`_url_position_argument`);
    2. the result of that call is not discarded — a plain forward taint walk
       from the call's result must reach the returned expression; and
    3. EVERY ``return`` in the method is reached that way.

    Property 3 is what makes an env-gated early return ("if a cached artifact
    exists, return it") impossible to hide: the artifact branch returns a value
    that is not data-dependent on the upstream call, so the method fails whether
    or not the branch is ever taken. Property 2 is what makes a discarded
    "connection warm-up" call useless as an alibi.
    """
    derived_locals: set[str] = set()
    for sub in ast.walk(method):
        if (
            isinstance(sub, ast.Assign)
            and isinstance(sub.value, ast.JoinedStr)
            and _expression_attributes(sub.value) & endpoint_attrs
        ):
            derived_locals |= {t.id for t in sub.targets if isinstance(t, ast.Name)}

    def _slot_is_upstream(slot: ast.expr) -> bool:
        if isinstance(slot, ast.Constant):
            return _is_external_endpoint(slot.value)
        if isinstance(slot, ast.Attribute):
            return slot.attr in endpoint_attrs
        if isinstance(slot, ast.Name):
            return slot.id in derived_locals
        return False

    upstream_calls: list[ast.Call] = []
    for sub in ast.walk(method):
        if not isinstance(sub, ast.Call):
            continue
        slot = _url_position_argument(sub, tree)
        if slot is not None and _slot_is_upstream(slot):
            upstream_calls.append(sub)
    if not upstream_calls:
        return False

    tainted: set[str] = set()
    upstream_ids = {id(call) for call in upstream_calls}

    def _is_tainted(expr: ast.AST) -> bool:
        for sub in ast.walk(expr):
            if isinstance(sub, ast.Call) and id(sub) in upstream_ids:
                return True
            if isinstance(sub, ast.Name) and sub.id in tainted:
                return True
        return False

    # Forward fixed point: an assignment whose value touches tainted data taints
    # its targets. Iterating to stability handles loops and augmented assignment
    # (``audio += resp.content`` inside a per-chunk loop) without ordering rules.
    for _ in range(8):
        before = set(tainted)
        for sub in ast.walk(method):
            if isinstance(sub, ast.Assign) and _is_tainted(sub.value):
                tainted |= _expression_names(ast.Tuple(elts=list(sub.targets), ctx=ast.Store()))
            elif (
                isinstance(sub, ast.AnnAssign | ast.AugAssign)
                and sub.value is not None
                and isinstance(sub.target, ast.Name)
                and _is_tainted(sub.value)
            ):
                tainted.add(sub.target.id)
        if tainted == before:
            break

    returns = [r for r in ast.walk(method) if isinstance(r, ast.Return) and r.value is not None]
    if not returns:
        return False
    return all(_is_tainted(r.value) for r in returns)


def _class_serves_real_upstream_output(node: ast.ClassDef, tree: ast.Module) -> bool:
    """Behavioural realness: does this class's PRODUCT come from an upstream?

    Scoped to the product methods the module's own ``Protocol`` declares, so a
    genuine call parked anywhere else in the class is no alibi. Nominal checks
    are absent by design — a future ``ReplayProvider``/``StudioProvider``/
    ``SampleProvider`` is judged the same way this judges ``OpenAITTSProvider``.
    """
    protocols = _protocol_product_methods(tree)
    product_names: set[str] = set()
    for names in protocols.values():
        product_names |= set(names)
    if not product_names:
        return False
    endpoint_attrs = _class_endpoint_attributes(node)
    methods = {m.name: m for m in node.body if isinstance(m, ast.FunctionDef)}
    implemented = [methods[name] for name in sorted(product_names) if name in methods]
    if not implemented:
        return False
    return any(
        _method_output_comes_from_upstream(method, endpoint_attrs, tree) for method in implemented
    )


def _implementation_dispatchers() -> list[dict[str, Any]]:
    """Find every module-level function that PICKS an implementation from env.

    Structural, and deliberately shape-agnostic — it recognises a registry
    lookup (``get_provider`` reading ``_PROVIDERS``) and an ``if``/``elif``
    ladder (``get_storage`` returning one of three classes) with the same rule:
    a module-level function that reads at least one environment variable and can
    return two or more different constructed classes.

    The previous version classified a variable as implementation-selecting only
    when its VALUE was a key in a discovered registry. That definition created
    its own exemption twice over: ``AUDIO_STORAGE=r2`` is not a key in any
    registry, so the storage backend was unguarded; and a workbench pinned to an
    unknown name (``TTS_PROVIDER=studio``, registered from a module the guard
    never imports) was skipped rather than compared.
    """
    registries_by_module: dict[pathlib.Path, list[tuple[str, dict[str, ast.ClassDef]]]] = {}
    for path, variable, mapping in _discover_registries():
        registries_by_module.setdefault(path, []).append((variable, mapping))

    dispatchers: list[dict[str, Any]] = []
    for path, tree in _source_modules():
        classes = _module_classes(tree)
        registries = registries_by_module.get(path, [])
        for name, func in _module_functions(tree).items():
            env_names = {
                read
                for sub in ast.walk(func)
                for read in [_env_read_name(sub, tree)]
                if read is not None
            }
            if not env_names:
                continue

            candidates: dict[str, ast.ClassDef] = {}
            for sub in ast.walk(func):
                if (
                    isinstance(sub, ast.Return)
                    and isinstance(sub.value, ast.Call)
                    and isinstance(sub.value.func, ast.Name)
                    and sub.value.func.id in classes
                ):
                    candidates[sub.value.func.id] = classes[sub.value.func.id]
            referenced = _expression_names(func)
            for variable, mapping in registries:
                if variable in referenced:
                    for cls in mapping.values():
                        candidates[cls.name] = cls
            if len(candidates) < 2:
                continue
            dispatchers.append(
                {
                    "path": path,
                    "tree": tree,
                    "node": func,
                    "function": name,
                    "env_names": env_names,
                    "candidates": candidates,
                    "registries": {v: m for v, m in registries if v in referenced},
                }
            )
    return dispatchers


def _production_resolved_class(dispatcher: dict[str, Any], value: str) -> str | None:
    """Which candidate class this dispatcher picks for ``value``, read from ast.

    Two shapes, resolved without constructing anything (production's own choice
    frequently cannot be constructed on a developer machine — that is the whole
    point of the storage divergence):

    * a registry lookup: the registry's own mapping answers it; and
    * an if/elif ladder: the branch whose test compares against this exact
      constant, and which returns a constructed class.
    """
    for mapping in dispatcher["registries"].values():
        if value in mapping:
            return mapping[value].name
    for node in ast.walk(dispatcher["node"]):
        if not isinstance(node, ast.If):
            continue
        constants = {
            sub.value
            for sub in ast.walk(node.test)
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
        }
        if value not in constants:
            continue
        for stmt in node.body:
            for sub in ast.walk(stmt):
                if (
                    isinstance(sub, ast.Return)
                    and isinstance(sub.value, ast.Call)
                    and isinstance(sub.value.func, ast.Name)
                    and sub.value.func.id in dispatcher["candidates"]
                ):
                    return sub.value.func.id
    return None


# --------------------------------------------------------------------------
# derivation helpers — environments
# --------------------------------------------------------------------------


def _shell_env_assignments(text: str) -> dict[str, str]:
    """Collect ``NAME=value`` assignments from a shell script, by plain parsing.

    Only bare, unquoted-or-simply-quoted scalar assignments are collected, which
    is what an implementation-selecting variable looks like. Comment lines are
    skipped so the prose above a setting is never mistaken for the setting.
    """
    assignments: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        for token in line.split():
            if "=" not in token:
                continue
            name, _, value = token.partition("=")
            if not name or not name.replace("_", "").isalnum():
                continue
            if not name.isupper():
                continue
            assignments[name] = value.strip().strip("\"'")
    return assignments


def _render_yaml_env_assignments() -> dict[str, str]:
    """Collect the ``- key: NAME`` / ``value: V`` pairs pinned in render.yaml."""
    lines = RENDER_YAML.read_text().splitlines()
    assignments: dict[str, str] = {}
    marker = "- key:"
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith(marker):
            continue
        name = stripped[len(marker) :].strip()
        for follow in lines[index + 1 :]:
            following = follow.strip()
            if not following or following.startswith("#"):
                continue
            if following.startswith("value:"):
                assignments[name] = following[len("value:") :].strip().strip("\"'")
            break
    return assignments


def _makefile_variable(name: str) -> str:
    """Read one ``NAME := value`` assignment out of the Makefile."""
    for line in MAKEFILE.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{name} :=") or stripped.startswith(f"{name}:="):
            return stripped.split(":=", 1)[1].strip()
    raise AssertionError(f"the Makefile defines no {name}")


def _makefile_default(name: str) -> str:
    """Read one ``NAME ?= value`` overridable default out of the Makefile.

    Only a literal right-hand side is taken (no ``$(`` in the value), the same
    rule ``scripts/preflight.py`` applies when it expands the ``PRE_*`` sets —
    so this can never start a substitution chain.
    """
    for line in MAKEFILE.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{name} ?=") or stripped.startswith(f"{name}?="):
            value = stripped.split("?=", 1)[1].strip()
            assert "$(" not in value, f"{name}'s default is not a literal: {value!r}"
            return value
    raise AssertionError(f"the Makefile defines no {name} ?= default")


def _workbench_profile_name() -> str:
    """Which committed profile ``make workbench`` overlays, read off the Makefile.

    ``config/profiles/<name>`` is the LAST env layer ``scripts/dev_env.py``
    applies (``build_environment``: os.environ, then the Render fetch, then the
    profile), so it OVERRIDES production's own values. A guard that reads only
    ``scripts/workbench.sh`` is reading the second-highest-precedence source and
    calling it the environment.

    The exec wrapper names its profile through the lane mechanism
    (``--profile $(DEV_PROFILE)``), so a variable token resolves through the
    Makefile's own ``?=`` default — the main lane, which is the one the
    workbench parity claim is about.
    """
    lines = MAKEFILE.read_text().splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("workbench:"):
            continue
        for follow in lines[index + 1 :]:
            if follow and not follow[0].isspace():
                break
            if "scripts/workbench.sh" not in follow:
                continue
            exec_var = None
            for raw_token in follow.split():
                token = raw_token.lstrip("@")
                if token.startswith("$(") and token.endswith(")") and "EXEC" in token:
                    exec_var = token[2:-1]
            assert exec_var, f"cannot see which env wrapper starts the workbench: {follow!r}"
            tokens = _makefile_variable(exec_var).split()
            for position, token in enumerate(tokens):
                if token == "--profile":
                    profile = tokens[position + 1]
                    if profile.startswith("$(") and profile.endswith(")"):
                        return _makefile_default(profile[2:-1])
                    return profile
            raise AssertionError(f"{exec_var} names no --profile")
    raise AssertionError("the Makefile has no `workbench:` target")


def _profile_assignments(profile: str) -> dict[str, str]:
    path = PROFILE_DIR / profile
    assert path.is_file(), f"the committed profile {path} does not exist"
    assignments: dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        assignments[name.strip()] = value.strip().strip("\"'")
    assert assignments, f"parsed no assignments out of {path}"
    return assignments


def _workbench_effective_env() -> dict[str, str]:
    """The environment the workbench server actually runs with.

    Layered in the order the tooling layers it: production's own values (the
    Render fetch) are the base, the committed profile overrides them, and
    ``scripts/workbench.sh``'s inline assignments on the uvicorn command line
    override everything. Only the last two can DIVERGE from production, so those
    are what the parity tests compare.
    """
    env = dict(_render_yaml_env_assignments())
    env.update(_profile_assignments(_workbench_profile_name()))
    env.update(_shell_env_assignments(WORKBENCH_SH.read_text()))
    return env


def _workbench_overrides() -> dict[str, str]:
    """Only the values the workbench itself sets (profile + inline)."""
    overrides = dict(_profile_assignments(_workbench_profile_name()))
    overrides.update(_shell_env_assignments(WORKBENCH_SH.read_text()))
    return overrides


def _probe_a_server_process(code: str, payload: object) -> Any:
    """Run ``code`` in a fresh interpreter that boots the API the way uvicorn does.

    NOT in the pytest process, deliberately. ``tests/conftest.py`` legitimately
    calls ``register_provider("mock", MockTTSProvider)`` so the $0 shard can opt
    into silence, and earlier tests leave rate-limit and cache entries behind.
    Any guard that inspected the pytest interpreter would therefore be measuring
    the test harness, not the server — and "the harness registered a fake" is
    indistinguishable from "the product registered a fake" once you are inside
    it. A child process with the workbench's own environment and no conftest is
    the closest thing to the server the owner actually looks at that costs $0.

    The child receives its input as JSON on stdin and answers with JSON on
    stdout, so nothing is interpolated into source text.
    """
    import json
    import subprocess
    import sys

    env = dict(os.environ)
    env.update(_workbench_effective_env())
    env["ONDOWAY_ALLOW_INSECURE_AUTH_SECRETS"] = "1"
    env["PYTHONPATH"] = str(REPO)
    completed = subprocess.run(
        [sys.executable, "-c", code],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(REPO),
        env=env,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, (
        f"the probe process failed ({completed.returncode}). This guard has to "
        f"observe a real API process rather than the pytest interpreter, so a "
        f"crash here is a failed guard, not a skipped one.\n"
        f"stdout: {completed.stdout[-2000:]}\nstderr: {completed.stderr[-2000:]}"
    )
    marker = "<<<ondoway-probe>>>"
    assert marker in completed.stdout, (
        f"the probe process produced no result marker; it cannot have run.\n"
        f"stdout: {completed.stdout[-2000:]}\nstderr: {completed.stderr[-2000:]}"
    )
    return json.loads(completed.stdout.split(marker, 1)[1])


# --------------------------------------------------------------------------
# derivation helpers — javascript, by plain brace/paren matching
# --------------------------------------------------------------------------


def _matched_block(html: str, open_index: int, opener: str, closer: str) -> str:
    depth = 0
    for i in range(open_index, len(html)):
        if html[i] == opener:
            depth += 1
        elif html[i] == closer:
            depth -= 1
            if depth == 0:
                return html[open_index : i + 1]
    raise AssertionError(f"unclosed {opener!r} at offset {open_index}")


def _js_function_body(html: str, declaration: str) -> str:
    """Return one JS function's source, located by plain brace matching.

    The parameter list is skipped by paren-matching FIRST. Taking the next ``{``
    after the declaration would otherwise grab a destructured parameter — real
    case: ``ttsPlay({ text, cacheKey, btn, audioEl })`` yielded a 32-character
    "body", which an absence assertion would have passed vacuously.
    """
    start = html.find(declaration)
    if start == -1:
        raise AssertionError(f"{declaration!r} is missing from review.html")
    params_open = html.index("(", start)
    params = _matched_block(html, params_open, "(", ")")
    open_brace = html.index("{", params_open + len(params))
    return _matched_block(html, open_brace, "{", "}")


def _all_offsets(text: str, needle: str) -> list[int]:
    offsets, cursor = [], 0
    while True:
        found = text.find(needle, cursor)
        if found == -1:
            return offsets
        offsets.append(found)
        cursor = found + 1


def _click_listener_bodies(html: str) -> list[str]:
    """Every ``addEventListener('click', ... => { ... })`` body on the page.

    The workbench's tour surface is driven by ONE delegated click dispatcher
    (``detailBody.addEventListener('click', (e) => {...})``), not by handlers
    named after the functions they call. A guard scoped to the three named
    function declarations never sees it, which is exactly where a re-added
    spend gate would sit: on the line above ``generateTourPreview()``.
    """
    bodies = []
    for offset in _all_offsets(html, "addEventListener('click'"):
        arrow = html.find("=>", offset)
        if arrow == -1:
            continue
        brace = html.find("{", arrow)
        if brace == -1:
            continue
        bodies.append(_matched_block(html, brace, "{", "}"))
    return bodies


def _call_arguments(html: str, call_offset: int, callee: str) -> list[str]:
    """Split one call's argument list at top level, by paren matching."""
    open_paren = html.index("(", call_offset + len(callee) - 1)
    block = _matched_block(html, open_paren, "(", ")")[1:-1]
    args, depth, current = [], 0, ""
    for ch in block:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            args.append(current)
            current = ""
            continue
        current += ch
    if current.strip():
        args.append(current)
    return [" ".join(a.split()) for a in args]


# --------------------------------------------------------------------------
# TEST 1 — realness, derived from behaviour rather than from the name
# --------------------------------------------------------------------------


def test_every_provider_the_workbench_can_select_really_calls_out() -> None:
    """OWNER RULING (2026-07-31): no mocks in the workbench, ever.

    The check is behavioural, so it does not care what a spoof calls itself. For
    each implementation a registry can return, this parses the defining module
    with ``ast`` and asks whether the class's PRODUCT — the methods its own
    ``Protocol`` declares — is data that came out of a call to a real upstream.

    Four holes closed since the first version, each one a spoof that passed it:

    * realness was keyed by the string NAME and OR-unioned across registries, so
      a decoy dict elsewhere in ``src/`` mapping ``"openai"`` to the genuine
      class vouched for a live registry that mapped it to a fake. Realness is
      now per registry ENTRY;
    * the probe walked the whole class, so a genuine call in an unrelated method
      excused a ``generate`` that returned a fixture. It is now scoped to the
      Protocol's product methods;
    * the endpoint counted wherever it appeared in a call, so posting to a
      loopback stub with the real URL riding along in a header passed. It must
      now be in the URL POSITION, read off the callee's own signature; and
    * presence of the call was enough, so a discarded warm-up call, or an
      env-gated early return above it, passed. Every ``return`` must now be
      data-dependent on the upstream response.

    UNDO TEST: point a registry entry at a class that returns canned bytes, add
    an early return above the POST, discard the response, move the call to
    another method, or aim it at a stub -> RED.
    """
    registries = _discover_registries()
    assert registries, (
        "discovered no implementation registries under src/ — the structural "
        "search is broken, so 'every selectable provider is real' proves nothing"
    )

    by_module = {path: tree for path, tree in _source_modules()}
    realness: dict[tuple[str, str], bool] = {}
    for path, variable, mapping in registries:
        tree = by_module[path]
        for key, class_node in mapping.items():
            realness[(variable, key)] = _class_serves_real_upstream_output(class_node, tree)

    assert any(realness.values()), (
        f"no registered implementation appears to reach an external service at "
        f"all ({sorted(realness)}). The realness probe is broken — failing loudly "
        f"rather than declaring every provider fake or every provider real."
    )

    selectable = _workbench_selectable_providers()
    assert selectable, (
        "parsed zero <option> values out of #ttsProviderSelect — the parser is "
        "broken, so the absence of a fake option proves nothing"
    )

    known = {key for _variable, key in realness}
    unknown = selectable - known
    assert not unknown, (
        f"the workbench audio selector offers {sorted(unknown)}, which no registry "
        f"in src/ can resolve. Known implementations: {sorted(known)}."
    )

    spoofs = sorted(
        f"{variable}[{key!r}]"
        for (variable, key), real in realness.items()
        if key in selectable and not real
    )
    assert not spoofs, (
        f"the workbench can select {spoofs}, whose product method never returns "
        f"data that came out of a call to a real upstream — it cannot be serving "
        f"a real service, whatever it is named. The workbench exists so the owner "
        f"can judge what a tourist actually gets; serving a canned response "
        f"through it makes that judgement worthless. Remove the option from "
        f"frontend/review.html; do NOT relax this test."
    )


def _workbench_selectable_providers() -> set[str]:
    """Every provider value the workbench audio selector can send."""
    html = REVIEW_HTML.read_text()
    anchor = html.find('id="ttsProviderSelect"')
    if anchor == -1:
        raise AssertionError("the #ttsProviderSelect audio provider selector is missing")
    start = html.rfind("<select", 0, anchor)
    end = html.find("</select>", anchor)
    if start == -1 or end == -1:
        raise AssertionError("#ttsProviderSelect is not a closed <select> element")
    block = html[start:end]

    values: set[str] = set()
    needle = '<option value="'
    cursor = 0
    while True:
        opening = block.find(needle, cursor)
        if opening == -1:
            break
        value_start = opening + len(needle)
        value_end = block.index('"', value_start)
        values.add(block[value_start:value_end])
        cursor = value_end
    return values


# --------------------------------------------------------------------------
# TEST 2 — the audited registry is the one the running server dispatches on
# --------------------------------------------------------------------------


_REGISTRY_PROBE = """
import json, sys
payload = json.load(sys.stdin)
import importlib
import src.api.app  # noqa: F401  -- boot the API exactly as uvicorn does
result = []
for entry in payload:
    module = importlib.import_module(entry["module"])
    live = getattr(module, entry["variable"], None)
    row = {"module": entry["module"], "variable": entry["variable"], "kind": type(live).__name__}
    if isinstance(live, dict):
        row["keys"] = sorted(live)
        row["classes"] = {k: getattr(v, "__qualname__", repr(v)) for k, v in live.items()}
        row["resolved"] = {}
        for resolver in entry["resolvers"]:
            call = getattr(module, resolver)
            row["resolved"][resolver] = {
                k: type(call(k)).__qualname__ for k in sorted(live)
            }
    result.append(row)
print("<<<ondoway-probe>>>" + json.dumps(result))
"""


def test_the_registry_the_guard_audited_is_the_one_the_server_dispatches_on() -> None:
    """A source audit proves nothing if the runtime resolves something else.

    Test 1 reads dict literals out of ``src/``. Three mechanisms make that
    reading a fiction while every source file still looks impeccable:

    * ``register_provider("openai", Wrapper)`` called from any module the API
      imports rebinds the key AFTER import — every visible name (render.yaml,
      workbench.sh, the dropdown, ``list_providers()``) stays correct;
    * a resolver that short-circuits before the lookup
      (``if name == "openai" and os.getenv(...): return Studio()``) leaves the
      registry entirely untouched and correct; and
    * ``__new__``/a metaclass on the genuine class returns a different object,
      so the audited class body is real AND reachable and you still never hold
      an instance of it.

    So this boots the API in a CHILD process with the workbench's environment —
    see :func:`_probe_a_server_process` for why the pytest interpreter is the
    wrong place to ask — and compares the live dict against the audited literal,
    then asserts the resolver hands back exactly the class the registry maps
    each key to. Class comparison, not a list of approved wrappers.

    UNDO TEST: rebind a key at import, branch inside the resolver, or add a
    substituting ``__new__`` -> RED.
    """
    registries = _discover_registries()
    assert registries, "no registries discovered — nothing to compare against"

    by_module = {path: tree for path, tree in _source_modules()}
    request = []
    expected: dict[tuple[str, str], dict[str, str]] = {}
    for path, variable, mapping in registries:
        module_name = ".".join(path.relative_to(REPO).with_suffix("").parts)
        # The registry is only load-bearing if a resolver actually reads it.
        # Derived structurally: a module-level function that references this
        # registry, takes an argument, and returns a constructed object.
        resolvers = [
            func.name
            for func in _module_functions(by_module[path]).values()
            if variable in _expression_names(func)
            and (func.args.args or func.args.posonlyargs)
            and any(
                isinstance(n, ast.Return) and isinstance(n.value, ast.Call) for n in ast.walk(func)
            )
        ]
        assert resolvers, (
            f"{module_name} defines {variable} but no function that reads it and "
            f"returns a constructed implementation. Either the registry is dead "
            f"(and test 1 audits nothing) or the resolver was renamed into a "
            f"shape this derivation cannot see — fix the derivation, not this."
        )
        request.append(
            {"module": module_name, "variable": variable, "resolvers": sorted(resolvers)}
        )
        expected[(module_name, variable)] = {k: v.name for k, v in mapping.items()}

    rows = _probe_a_server_process(_REGISTRY_PROBE, request)
    assert len(rows) == len(request), "the probe answered about a different set of registries"

    checked = 0
    for row in rows:
        key = (row["module"], row["variable"])
        source = expected[key]
        assert row["kind"] == "dict", (
            f"{row['module']}.{row['variable']} is a {row['kind']} in a running "
            f"server — the structural discovery in test 1 is auditing something "
            f"the server does not dispatch on"
        )
        assert set(row["keys"]) == set(source), (
            f"{row['module']}.{row['variable']} holds {sorted(row['keys'])} in a "
            f"running server but the source literal declares {sorted(source)}. "
            f"Something registered (or removed) an implementation after import, "
            f"so the registry test 1 audited is NOT the registry the workbench "
            f"dispatches on."
        )
        for name, class_name in source.items():
            assert row["classes"][name] == class_name, (
                f"{row['module']}.{row['variable']}[{name!r}] is "
                f"{row['classes'][name]} in a running server but {class_name} in "
                f"source. The workbench would judge an implementation nothing "
                f"audited."
            )
        for resolver, resolutions in row["resolved"].items():
            for name, class_name in resolutions.items():
                assert class_name == source[name], (
                    f"{row['module']}.{resolver}({name!r}) returned a "
                    f"{class_name}, but the registry maps that key to "
                    f"{source[name]}. The registry is decorative: the resolver "
                    f"decides, and it decided differently. Every guard that reads "
                    f"the registry is therefore auditing a fiction."
                )
                checked += 1
    assert checked, "resolved zero implementations — this test proved nothing"


# --------------------------------------------------------------------------
# TEST 3 — a Listen press really leaves the machine
# --------------------------------------------------------------------------


def test_pressing_listen_in_the_workbench_leaves_the_machine(monkeypatch) -> None:
    """The only question a static guard cannot dodge: did packets try to leave?

    Every source-level guard in this file reasons about WHICH implementation
    would be selected. None of them can see:

    * a transport swap in ``src/audio/_http.py`` — no provider class is touched,
      ``_default_client`` simply returns an ``httpx.MockTransport`` client and
      every AST still shows a POST to api.openai.com;
    * a pre-seeded ``_preview_cache`` — ``provider.generate`` is never called at
      all, so the provider's honesty is irrelevant;
    * an env-gated artifact read at the top of ``generate``; or
    * an instance substitution that leaves the audited class untouched.

    So this drives the REAL route the workbench's Listen button POSTs to, in
    process, with the socket layer denied, on text minted at test time. Freshly
    minted text cannot be pre-seeded in any fixture, so no cache can satisfy it.
    Three derived assertions:

    * at least one outbound connection was ATTEMPTED (a cache, an artifact, a
      mock transport or a stubbed client all attempt none);
    * every host attempted is globally routable — a local stub fails this by
      address, with no host list involved; and
    * with egress denied the request FAILS. A route that still returns audio
      when the network is gone was never getting audio from the network.

    Real money is impossible here: DNS is denied before any connection opens,
    and the API key is a throwaway string this test invents.

    UNDO TEST: make ``_default_client`` return a ``MockTransport`` client,
    pre-seed the preview cache, or return a local artifact from ``generate``
    -> RED (no connection attempted). Point the provider at a local stub -> RED
    (attempted host is not globally routable).
    """
    from fastapi.testclient import TestClient

    import src.audio._http as http_module
    import src.audio.provider as provider_module

    workbench_env = _workbench_effective_env()
    production_env = _render_yaml_env_assignments()
    provider_variable = "TTS_PROVIDER"
    assert provider_variable in production_env, (
        "render.yaml no longer pins the audio provider, so this test cannot "
        "derive which implementation the tourist hears"
    )
    for name, value in workbench_env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("ONDOWAY_ALLOW_INSECURE_AUTH_SECRETS", "1")
    # A throwaway credential: the provider refuses to attempt anything without
    # one, and refusing early would look exactly like "made no network call".
    monkeypatch.setenv("OPENAI_API_KEY", f"probe-{secrets.token_hex(8)}")
    # The retry backoff exists for real gateway flaps; nothing here is transient.
    monkeypatch.setattr(provider_module, "_TTS_INITIAL_BACKOFF_SEC", 0.0)
    monkeypatch.setattr(http_module, "INITIAL_BACKOFF_SEC", 0.0)

    attempted: list[str] = []
    real_getaddrinfo = socket.getaddrinfo

    def _denied(host, port, *args, **kwargs):
        attempted.append(str(host))
        raise OSError(f"ondoway egress denied during {__name__}")

    monkeypatch.setattr(socket, "getaddrinfo", _denied)

    from src.api.app import create_app

    novel_text = f"Egress probe {secrets.token_hex(16)} for the workbench listen button."
    with TestClient(create_app()) as client:
        response = client.post(
            "/api/v1/audio/preview",
            json={"text": novel_text, "provider": production_env[provider_variable]},
        )

    monkeypatch.setattr(socket, "getaddrinfo", real_getaddrinfo)

    assert attempted, (
        "pressing Listen on text that did not exist until this test ran produced "
        f"HTTP {response.status_code} without the process attempting a single "
        "outbound connection. Whatever the workbench played, it did not come "
        "from a real text-to-speech service — the bytes were already on this "
        "machine (a cache, an artifact, a stubbed transport or a fake provider). "
        "The owner would have judged narration a tourist will never hear."
    )
    local = sorted({host for host in attempted if not _host_is_external(host)})
    assert not local, (
        f"the audio request went to {local}, which is this machine talking to "
        f"itself. A stub on a loopback or private address is still a spoof, "
        f"however genuine the HTTP client is."
    )
    assert response.status_code != 200, (
        f"with every outbound connection denied, /audio/preview still answered "
        f"200 with {len(response.content)} bytes of audio. Something served that "
        f"audio from inside this process."
    )


# --------------------------------------------------------------------------
# TEST 4 — nothing answers a request from data that predates it
# --------------------------------------------------------------------------


_CONTAINER_PROBE = """
import json, sys
payload = json.load(sys.stdin)
import importlib
import src.api.app  # noqa: F401  -- boot the API exactly as uvicorn does
result = {}
for module_name, names in payload.items():
    module = importlib.import_module(module_name)
    for name in names:
        container = getattr(module, name, None)
        if isinstance(container, (dict, list, set)):
            try:
                snapshot = json.loads(json.dumps(
                    sorted(container) if isinstance(container, set) else container))
            except (TypeError, ValueError):
                snapshot = None  # unserializable content: never matches a literal
            result[module_name + "." + name] = {
                "len": len(container), "content": snapshot,
            }
print("<<<ondoway-probe>>>" + json.dumps(result))
"""

#: Module-level containers that legitimately hold entries at boot, because their
#: entries are a TRANSLATION TABLE declared as a source literal — not responses.
#: Exactly like the consent test's pinned ``confirm(`` population: this is not an
#: allowlist of implementations (the content equality below is still derived and
#: enforced), it pins the accounted-for POPULATION so a NEW literal table on a
#: route module forces a decision here — justify it in this comment or remove it.
#: Do not simply add a name.
#:
#: - ``trips._RENAMED_TOURABILITY_FIELDS`` — stored-schema field renames, so a
#:   tourability disclosure saved before a rename is translated instead of
#:   silently dropped (see its comment in src/api/routes/trips.py).
_BOOT_TIME_LITERAL_TABLES = frozenset({"src.api.routes.trips._RENAMED_TOURABILITY_FIELDS"})


def test_no_response_cache_is_pre_seeded_before_the_first_request() -> None:
    """A warm-up that fills a response cache at import serves canned bytes.

    ``/audio/preview`` consults a module-level cache BEFORE calling the
    provider. A plausible cost-saving import-time warm-up (read a fixtures
    directory, put each entry) makes every Listen press return bytes off disk
    with the provider never invoked — and every provider-realness guard stays
    green because the provider is untouched.

    Derived, not named: in a freshly booted server, every module-level container
    on a route module must be EMPTY — a response cache with entries before the
    first request has answers to questions nobody asked — with ONE narrow,
    pinned exception (``_BOOT_TIME_LITERAL_TABLES``): a container whose source
    assignment is a pure literal may hold EXACTLY that literal, byte for byte,
    because a declared translation table (a stored-schema rename map) is source
    the reviewer reads, not data a warm-up smuggled in. The equality is strict
    content, not length: anything an import-time hook adds, replaces or computes
    diverges from the parsed source literal and still fails.

    Measured in a child process (see :func:`_probe_a_server_process`): by the
    time this test runs, the pytest interpreter has already served requests
    through several of these modules, so its containers are legitimately warm.
    Only a fresh boot can distinguish "seeded at import" from "used since".

    UNDO TEST: seed one entry into any route module's cache at import -> RED.
    Mutate the pinned table at import (``_RENAMED_TOURABILITY_FIELDS["x"] = "y"``
    below its literal) -> live content no longer equals the source literal -> RED.
    """
    routes_dir = SRC / "api" / "routes"
    assert routes_dir.is_dir(), "src/api/routes is missing — the derivation is broken"

    request: dict[str, list[str]] = {}
    declared_literals: dict[str, object] = {}
    for path in sorted(routes_dir.glob("*.py")):
        tree = ast.parse(path.read_text())
        module_name = ".".join(path.relative_to(REPO).with_suffix("").parts)
        names: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.append(node.target.id)
                value, target = node.value, node.target.id
            elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(
                node.targets[0], ast.Name
            ):
                names.append(node.targets[0].id)
                value, target = node.value, node.targets[0].id
            else:
                if isinstance(node, ast.Assign):
                    names.extend(t.id for t in node.targets if isinstance(t, ast.Name))
                continue
            # The SOURCE literal, when the assignment is one: what the container
            # must still equal, exactly, in the freshly booted process.
            if value is not None:
                try:
                    literal = ast.literal_eval(value)
                except (ValueError, TypeError, SyntaxError):
                    literal = None
                if isinstance(literal, (dict, list, set)):
                    # An unsortable mixed-type set is never comparable, so it is
                    # never pinned — suppressed rather than raised.
                    with contextlib.suppress(TypeError):
                        declared_literals[f"{module_name}.{target}"] = (
                            sorted(literal) if isinstance(literal, set) else literal
                        )
        if names:
            request[module_name] = names
    assert request, "found no module-level bindings on any route module"

    observed = _probe_a_server_process(_CONTAINER_PROBE, request)
    assert observed, "found no module-level containers on any route module to check"

    filled = []
    for name, report in sorted(observed.items()):
        if not report["len"]:
            continue
        if name in _BOOT_TIME_LITERAL_TABLES:
            assert name in declared_literals, (
                f"{name} is pinned as a boot-time literal table, but its source "
                f"assignment is not a pure literal any more — whatever fills it "
                f"now runs at import, which is exactly what this guard exists to "
                f"catch. Un-pin it or make it a literal again."
            )
            assert report["content"] == declared_literals[name], (
                f"{name} no longer holds the literal its source declares.\n"
                f"  source : {declared_literals[name]!r}\n"
                f"  booted : {report['content']!r}\n"
                f"Something mutated it at import time; a translation table that "
                f"grows at boot is a cache wearing a constant's name."
            )
            continue
        filled.append(f"{name} ({report['len']})")
    assert not filled, (
        f"{filled} already hold entries in a freshly booted server, before any "
        f"request has been served. A pre-filled container on a route module "
        f"answers requests with data that predates them, which is a canned "
        f"response no matter what produced it — and the provider it bypasses "
        f"stays entirely honest while it happens. If one of these is genuinely a "
        f"source-declared translation table, pin it in _BOOT_TIME_LITERAL_TABLES "
        f"with a justification; do not simply widen this assertion."
    )


# --------------------------------------------------------------------------
# TEST 5 — the served app ships no substitutions, in the workbench's own env
# --------------------------------------------------------------------------


def test_the_served_app_has_no_dependency_overrides(monkeypatch) -> None:
    """OWNER RULING (2026-07-31): the workbench serves the real engine.

    ``app.dependency_overrides`` is the exact mechanism the test suite uses to
    swap real providers for fakes. It is a dict on the live app object, so
    anything that populates it at import or construction time applies to every
    request the workbench makes.

    Constructed here under the WORKBENCH'S OWN environment, derived from the
    Makefile target, the committed profile and ``scripts/workbench.sh`` rather
    than hand-copied. A bare ``create_app()`` in a naked test process cannot see
    an override installed behind ``if os.getenv("WORKBENCH_API_ENABLED")`` —
    which is precisely the condition a substitution would hide behind, since it
    is true for the workbench and false for production.

    UNDO TEST: populate an override inside ``create_app`` (conditionally on any
    variable the workbench sets, or unconditionally) -> RED.
    """
    for name, value in _workbench_effective_env().items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("ONDOWAY_ALLOW_INSECURE_AUTH_SECRETS", "1")

    from src.api.app import create_app

    app = create_app()
    assert app.dependency_overrides == {}, (
        f"create_app() ships {len(app.dependency_overrides)} dependency override(s): "
        f"{sorted(str(k) for k in app.dependency_overrides)}. The workbench would "
        f"be judging substituted implementations while looking identical to the "
        f"real thing. Overrides belong in a test fixture, never in the app factory."
    )


# --------------------------------------------------------------------------
# TEST 6 — the workbench resolves the implementations production pins
# --------------------------------------------------------------------------


def test_the_workbench_resolves_the_implementations_production_pins() -> None:
    """OWNER RULING (2026-07-31): workbench and production must agree.

    IMPLEMENTATION-SELECTING is now derived from the code's SHAPE rather than
    from a value happening to be a registry key: a module-level function that
    reads the environment and can return two or more different constructed
    classes. That recognises ``get_provider`` (registry lookup) and
    ``get_storage`` (an if/elif ladder) identically, and it does not care
    whether the configured value is a name the guard has heard of — a workbench
    pinned to ``studio`` is compared against render.yaml just the same.

    The workbench must SET each of these itself — in
    ``config/profiles/<profile>`` (the LAST layer ``dev_env.build_environment``
    applies, so it overrides even the Render fetch) or inline on
    ``scripts/workbench.sh``'s uvicorn line, which overrides everything.
    Omission is a failure, not a pass: leaving the value to whatever the process
    happens to inherit is exactly how the workbench silently landed on
    ``get_provider()``'s fallback while every file still read correctly.

    ONE divergence is tolerated, and it is derived rather than listed: a
    divergence passes only when production's own implementation CANNOT BE
    CONSTRUCTED in a workbench process. ``AUDIO_STORAGE=r2`` is that case —
    ``R2StorageProvider()`` raises without R2 credentials, so local dev has no
    choice. ``TTS_PROVIDER`` is not: ``OpenAITTSProvider()`` constructs
    anywhere, so any divergence there is a spoof and fails. Nothing is named.

    FOUND BY THIS TEST (2026-07-31): scripts/workbench.sh set no TTS_PROVIDER at
    all, so the workbench server inherited ``get_provider()``'s "mock" fallback
    while production pinned "openai" — the backend twin of the screenshotted
    frontend bug.

    UNDO TEST: change TTS_PROVIDER in scripts/workbench.sh, or DELETE the line
    (leaving the value to whatever is inherited) -> RED.
    """
    import importlib

    dispatchers = _implementation_dispatchers()
    assert len(dispatchers) >= 2, (
        f"discovered {len(dispatchers)} implementation dispatcher(s) in src/; the "
        f"audio provider and the audio storage backend are both dispatchers, so a "
        f"result below two means the structural search is broken and this test "
        f"would pass no matter what the workbench resolved"
    )

    production_env = _render_yaml_env_assignments()
    overrides = _workbench_overrides()
    compared = 0
    for dispatcher in dispatchers:
        module_name = ".".join(dispatcher["path"].relative_to(REPO).with_suffix("").parts)
        for name in sorted(dispatcher["env_names"]):
            if name not in production_env:
                continue
            compared += 1
            assert name in overrides, (
                f"render.yaml pins {name}={production_env[name]!r} but the "
                f"workbench sets it nowhere — not in config/profiles/"
                f"{_workbench_profile_name()}, not on scripts/workbench.sh's "
                f"uvicorn line. Whatever the process inherits then chooses which "
                f"implementation the owner judges, and the code's own fallback is "
                f"the thing this file exists to keep out of the workbench. Pin it "
                f"explicitly to production's value."
            )
            if overrides[name] == production_env[name]:
                continue
            # Tolerated only when production's own choice cannot run here.
            resolve = getattr(importlib.import_module(module_name), dispatcher["function"])
            probe_env = dict(_workbench_effective_env())
            probe_env[name] = production_env[name]
            saved = dict(os.environ)
            try:
                os.environ.clear()
                os.environ.update(probe_env)
                try:
                    resolve()
                    constructible = True
                except Exception:
                    constructible = False
            finally:
                os.environ.clear()
                os.environ.update(saved)
            assert not constructible, (
                f"{name} selects a different implementation in the workbench "
                f"({overrides[name]!r}) than in production "
                f"({production_env[name]!r}), and production's choice constructs "
                f"perfectly well in a workbench process — so there is no reason "
                f"for the workbench to be running something else. render.yaml is "
                f"the source of truth: fix scripts/workbench.sh or "
                f"config/profiles/{_workbench_profile_name()}. The workbench is "
                f"only worth looking at while it runs the same code a tourist runs."
            )
    assert compared, (
        "render.yaml pins no implementation-selecting variable at all, so this "
        "test would pass no matter what the workbench resolved. That is exactly "
        "the state that let the workbench fall back to a fake provider."
    )


# --------------------------------------------------------------------------
# TEST 7 — the workbench does not tune the audio path behind production's back
# --------------------------------------------------------------------------


def test_the_workbench_does_not_tune_the_audio_path_behind_productions_back() -> None:
    """Same provider, different product: voice, model and caps are not free.

    Test 6 compares variables production PINS. The nastier version sets one it
    does not: ``OPENAI_VOICE=alloy`` gives the owner a completely genuine, fully
    billed call to api.openai.com in a voice no tourist will ever hear, and
    ``AUDIO_PREVIEW_CACHE_ENTRIES=0`` silently re-bills every replayed stop. Neither
    value is a registry key, so the old "is this value a known implementation?"
    classification could never see either one.

    Derived scope, no path list: the modules that participate in the audio
    product are the ones that DEFINE an implementation dispatcher, plus the
    route modules that IMPORT one. Env reads lexically inside a candidate class
    production does not resolve are excluded — ``AUDIO_STORAGE_PATH`` only
    configures the local storage backend production never builds, so it is not
    a divergence in what the owner hears.

    The rule: the workbench may not SET a variable that steers the audio path
    unless render.yaml pins it too (in which case test 6 owns the comparison).

    UNDO TEST: add OPENAI_VOICE or AUDIO_PREVIEW_CACHE_ENTRIES to
    scripts/workbench.sh or the profile -> RED.
    """
    dispatchers = _implementation_dispatchers()
    assert dispatchers, "no dispatchers discovered — cannot scope the audio path"

    production_env = _render_yaml_env_assignments()
    dispatcher_modules = {d["path"] for d in dispatchers}

    # Which candidate classes production never resolves — their configuration is
    # irrelevant to what the owner hears. Read from the dispatcher's own ast, so
    # nothing has to be constructible on this machine.
    resolved_classes: set[str] = set()
    all_candidates: set[str] = set()
    for dispatcher in dispatchers:
        all_candidates |= set(dispatcher["candidates"])
        for env_name in dispatcher["env_names"]:
            pinned = production_env.get(env_name)
            if pinned is None:
                continue
            picked = _production_resolved_class(dispatcher, pinned)
            assert picked is not None, (
                f"cannot tell which implementation {dispatcher['function']} picks for "
                f"{env_name}={pinned!r}, the value render.yaml pins. Without that, "
                f"this test cannot tell a production setting from a dead one."
            )
            resolved_classes.add(picked)
    assert resolved_classes, "resolved none of production's implementations from ast"
    unresolved_classes = all_candidates - resolved_classes

    participating: set[pathlib.Path] = set(dispatcher_modules)
    dispatcher_names = {d["function"] for d in dispatchers}
    for path, tree in _source_modules():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and any(alias.name in dispatcher_names for alias in node.names)
            ):
                participating.add(path)
    assert len(participating) > len(dispatcher_modules), (
        "no route module imports an implementation dispatcher — the audio-path "
        "scope collapsed to the dispatchers themselves, so this test would miss "
        "any tuning applied at the route layer"
    )

    steering: set[str] = set()
    for path in sorted(participating):
        tree = dict(_source_modules())[path]
        excluded: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name in unresolved_classes:
                excluded |= {id(sub) for sub in ast.walk(node)}
        for node in ast.walk(tree):
            if id(node) in excluded:
                continue
            read = _env_read_name(node, tree)
            if read is not None:
                steering.add(read)
    assert steering, "found no environment reads on the audio path — scope is broken"

    overrides = _workbench_overrides()
    unpinned = sorted(name for name in steering & set(overrides) if name not in production_env)
    assert not unpinned, (
        f"the workbench sets {unpinned} on the audio path, and render.yaml pins "
        f"no value for any of them — so the owner is judging audio produced under "
        f"settings a tourist's request never uses. Voice, model and length caps "
        f"are part of the product, not test scaffolding. Either pin the same "
        f"value in render.yaml or stop setting it locally."
    )


# --------------------------------------------------------------------------
# TEST 8 — an implementation must use the payload it is handed
# --------------------------------------------------------------------------


def test_every_implementation_uses_the_payload_it_is_handed() -> None:
    """The last hop: a real provider call whose bytes are then thrown away.

    ``generate_stop_audio`` returns the URL that ``storage.upload(audio_bytes,
    key)`` hands back, and the workbench plays THAT. A storage backend whose
    ``upload`` ignores its ``data`` argument and returns a hand-picked path
    gives the owner curated audio while OpenAI is genuinely called and genuinely
    billed — ``provider.name`` still reports the real provider, and every
    realness guard in this file stays green.

    Derived from the Protocol's own signature: for every class that implements a
    Protocol method, the FIRST parameter — the payload — must be referenced
    somewhere in the body. An implementation that never looks at what it was
    given is not implementing the contract, whatever it is called. (Only the
    first parameter: ``MockTTSProvider.generate`` legitimately ignores
    ``voice_id``, and so would any provider without voice selection.)

    UNDO TEST: add a storage or TTS implementation whose product method ignores
    its payload argument -> RED.
    """
    checked = 0
    for _path, tree in _source_modules():
        protocols = _protocol_product_methods(tree)
        if not protocols:
            continue
        product_names: set[str] = set()
        for names in protocols.values():
            product_names |= set(names)
        protocol_names = set(protocols)
        for class_node in _module_classes(tree).values():
            if class_node.name in protocol_names:
                continue
            for method in class_node.body:
                if not isinstance(method, ast.FunctionDef):
                    continue
                if method.name not in product_names or method.decorator_list:
                    continue
                params = [a.arg for a in method.args.posonlyargs + method.args.args]
                if params and params[0] in ("self", "cls"):
                    params = params[1:]
                if not params:
                    continue
                payload = params[0]
                checked += 1
                used = any(isinstance(n, ast.Name) and n.id == payload for n in ast.walk(method))
                assert used, (
                    f"{class_node.name}.{method.name} never reads its {payload!r} "
                    f"argument. It answers with something it did not derive from "
                    f"what it was given — canned output wearing a real "
                    f"implementation's name. The workbench would play it as the "
                    f"tourist's audio."
                )
    assert checked, "inspected no Protocol implementations — the derivation is broken"


# --------------------------------------------------------------------------
# TEST 9 — nothing between the request and the response reads the environment
# --------------------------------------------------------------------------


def test_no_request_handler_lets_anything_but_the_request_choose_the_response() -> None:
    """Three ways to substitute the engine without touching a registry.

    * A bare conditional in a route handler:
      ``if os.getenv("TRIPS_PREVIEW_FAST_DEV") == "1": return _basic_fallback()``
      exported only from ``scripts/workbench.sh``. Not a class, not a registry
      key, absent from render.yaml entirely, so nothing to compare against.
    * A dependency-provider function that constructs a DIFFERENT class under an
      env var. ``get_premium_compose_executor`` is the actual narration LLM for
      ``POST /trips/preview`` — the endpoint ``generateTourPreview`` calls — and
      one added branch returning ``OfflinePremiumExecutor()`` serves the owner
      un-authored stitch text at $0 while every label still says Premium. This
      never touches ``dependency_overrides``: it changes what the DEFAULT
      dependency returns.
    * Middleware keyed on request metadata. ``review.html`` is opened as a
      ``file://`` page, so its fetches carry a distinctive ``Origin``; a
      middleware that branches on that serves the workbench a cheaper response
      while the mobile app sails through.

    All three are checked structurally: route handlers may not branch to a
    ``return`` on an environment read, dependency providers may construct at
    most one class, and in-repo middleware may not branch on the request.

    UNDO TEST: add an env-conditioned return to any route handler, a second
    constructed class to any Depends provider, or a request-conditioned branch
    to a middleware -> RED.
    """
    routes_dir = SRC / "api" / "routes"
    handlers: list[tuple[str, ast.FunctionDef, ast.Module]] = []
    dependency_calls: list[tuple[str, str]] = []
    for path in sorted(routes_dir.glob("*.py")):
        tree = ast.parse(path.read_text())
        module_name = ".".join(path.relative_to(REPO).with_suffix("").parts)
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            routed = any(
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and isinstance(dec.func.value, ast.Name)
                and dec.func.value.id == "router"
                for dec in node.decorator_list
            )
            if not routed:
                continue
            handlers.append((f"{module_name}.{node.name}", node, tree))
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Name)
                    and sub.func.id == "Depends"
                    and sub.args
                ):
                    dependency_calls.append((module_name, ast.unparse(sub.args[0])))
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                for sub in ast.walk(dec):
                    if (
                        isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Name)
                        and sub.func.id == "Depends"
                        and sub.args
                    ):
                        dependency_calls.append((module_name, ast.unparse(sub.args[0])))
    assert handlers, "found no routed handlers under src/api/routes — derivation broken"
    assert dependency_calls, "found no Depends() providers — derivation broken"

    def _reads_environment(node: ast.AST, tree: ast.Module) -> bool:
        return any(_reads_env_call(sub, tree) for sub in ast.walk(node))

    for label, handler, handler_tree in handlers:
        for sub in ast.walk(handler):
            if not isinstance(sub, ast.If) or not _reads_environment(sub.test, handler_tree):
                continue
            returns = any(
                isinstance(inner, ast.Return)
                for branch in (sub.body, sub.orelse)
                for stmt in branch
                for inner in ast.walk(stmt)
            )
            assert not returns, (
                f"{label} lets an environment variable decide which response it "
                f"returns ({ast.unparse(sub.test)}). A variable set only in "
                f"scripts/workbench.sh then serves the owner a different product "
                f"than the tourist gets, with nothing in render.yaml to compare "
                f"against and no provider, registry or override touched."
            )

    import importlib

    checked_dependencies = 0
    for module_name, expression in sorted(set(dependency_calls)):
        module = importlib.import_module(module_name)
        provider = getattr(module, expression.split(".")[0], None)
        if provider is None or not callable(provider):
            continue
        try:
            source = ast.parse(_dedent_source(provider))
        except (OSError, TypeError, SyntaxError):
            continue
        constructed = set()
        for sub in ast.walk(source):
            if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Call):
                constructed.add(ast.unparse(sub.value.func))
        checked_dependencies += 1
        assert len(constructed) <= 1, (
            f"the dependency provider {expression} can construct {sorted(constructed)} "
            f"— more than one implementation. Whichever branch is taken is decided "
            f"inside the function, invisibly to app.dependency_overrides and to "
            f"every registry guard. The compose executor for POST /trips/preview "
            f"is the narration the owner reads: it must have exactly one answer."
        )
    assert checked_dependencies, "resolved no dependency providers — derivation broken"

    app_tree = ast.parse((SRC / "api" / "app.py").read_text())
    local_names = set()
    for node in ast.walk(app_tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("src."):
            local_names |= {alias.asname or alias.name for alias in node.names}
    for node in ast.walk(app_tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_middleware"
            and node.args
        ):
            registered = ast.unparse(node.args[0])
            assert registered not in local_names, (
                f"src/api/app.py registers the in-repo middleware {registered}. "
                f"Middleware sits above every route and can answer a request "
                f"from its headers alone — review.html's file:// origin is "
                f"distinguishable from the mobile app's. If this middleware is "
                f"genuinely needed, prove here that it cannot vary the response "
                f"by request metadata."
            )
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            decorated = any(
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and dec.func.attr == "middleware"
                for dec in node.decorator_list
            )
            if not decorated:
                continue
            params = [a.arg for a in node.args.args]
            assert params, f"middleware {node.name} takes no request parameter"
            request_param = params[0]
            for sub in ast.walk(node):
                if isinstance(sub, ast.If) and request_param in _expression_names(sub.test):
                    raise AssertionError(
                        f"the middleware {node.name} branches on the request itself "
                        f"({ast.unparse(sub.test)}). That is how one client is served "
                        f"a different response than another while both call the same "
                        f"route — the workbench and the app must be indistinguishable."
                    )


def _dedent_source(obj: object) -> str:
    import inspect
    import textwrap

    return textwrap.dedent(inspect.getsource(obj))  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# TEST 10 — no consent gate between the click and the call
# --------------------------------------------------------------------------


def test_no_consent_gate_between_the_click_and_the_tourist_facing_call() -> None:
    """OWNER RULING (2026-07-31): the spend-confirmation dialog is gone for good.

    ``generateTourPreview`` used to ``window.confirm`` that generating a preview
    "spends real money on your API key" and return early on Cancel. The tourist
    app has no spend prompt, so the workbench must not have one either — and a
    blocking modal also stalls any automated demo or screenshot run.

    SCOPE, widened after the first version: asserting ``confirm(`` absent from
    the named function BODIES misses the place the gate would actually go.
    The tour surface is driven by ONE delegated click dispatcher
    (``detailBody.addEventListener('click', ...)``), an anonymous arrow function
    that is none of the declarations, and a ``confirm(`` on the line above
    ``generateTourPreview();`` sits outside every scanned body. So:

    * every click-listener body that reaches a tourist-facing function is
      scanned, not just the functions themselves;
    * the whole file's ``confirm(`` population is PINNED, so a new one anywhere
      forces a decision here (review.html confirms once, inside
      ``showMergePreview``'s zero-beat branch, where OK deletes the source POI —
      a data-loss guard on an editor-only screen no tourist sees, and deleting
      it would defeat test_workbench_review_regressions.py::test_defect4_*); and
    * the dispatch to the plan AND the write buttons must be the FIRST thing
      their branches do, so a custom overlay, a required checkbox or a
      disabled-button gate cannot be wired around either call site without a
      statement appearing in between. That check names no dialog implementation
      at all — it asserts the tourist's "one click, one tour" property.

    NAME ANCHORS RE-DERIVED at S4.8 (Phase 4, design §8.1 + W4.2): the generate
    path kept its name (``generateTourPreview`` now plans THE day), the pick
    (``authorTourOption``, one click per option card) died with the flavour UI,
    and the paid call moved to the ONE ``authorTourDay`` behind "Write the tour"
    — so the write dispatch is checked exactly like the plan dispatch. A dial
    turn is the same one-tap promise through a change listener
    (``scheduleTourReplan``), so that path is asserted gate-free too.

    UNDO TEST: put any ``confirm(...)`` back into generateTourPreview,
    authorTourDay or scheduleTourReplan, or into the click dispatcher, or insert
    ANY statement before the dispatcher's call to generateTourPreview() or
    authorTourDay() -> RED.
    """
    html = REVIEW_HTML.read_text()

    assert "spends real money on your API key" not in html, (
        "the spend-warning text is back in the workbench. A tourist is never "
        "warned about API cost, so neither is the editor standing in for one."
    )
    assert "prompt(" not in html, (
        "window.prompt blocks the page on a modal the real app never shows, and "
        "it stalls automated demo runs."
    )

    confirms = _all_offsets(html, "confirm(")
    assert len(confirms) == 1, (
        f"review.html contains {len(confirms)} confirm( call(s); exactly one is "
        f"accounted for (the POI-deleting branch of showMergePreview). A new "
        f"blocking dialog anywhere on this page is a new decision — justify it "
        f"in this test's docstring or remove it. Do not simply bump the number."
    )

    names = []
    for declaration in TOURIST_FACING_FUNCTIONS:
        body = _js_function_body(html, declaration)
        assert len(body) > 200, f"the extracted body of {declaration!r} is implausibly short"
        assert "confirm(" not in body, (
            f"{declaration!r} shows a confirmation modal. The workbench must "
            f"replicate the tourist's app experience as closely as possible, and "
            f"a tourist is never asked to approve anything before hearing a tour."
        )
        head = declaration.split("function ", 1)[1]
        names.append(head.split("(")[0])
    assert names, "derived no tourist-facing function names"

    listeners = _click_listener_bodies(html)
    assert listeners, "found no click listeners in review.html — the parser is broken"
    reaching = [body for body in listeners if any(f"{n}(" in body for n in names)]
    assert reaching, (
        "no click listener reaches a tourist-facing function; the workbench's "
        "Generate and Listen buttons are dispatched from one, so finding none "
        "means this scan is looking at the wrong thing"
    )
    for body in reaching:
        assert "confirm(" not in body, (
            "a click handler that dispatches tour generation or audio playback "
            "shows a confirmation modal before doing so. Scoping this check to "
            "the three named functions is exactly how a gate placed one frame "
            "up the stack goes unnoticed."
        )

    # Scoped to what a PERSON's tap reaches, which is the whole claim: nothing
    # stands between pressing a button and the day being planned or written. The
    # page's own automatic re-fetch (after the server reports the plan moved) is
    # not a gate — nobody is being asked for anything — so it is deliberately not
    # in scope here; it is a click listener that must be clean. Both one-tap
    # calls are held to the same window check: the free plan AND the paid write
    # (design §8.1's one "Write the tour" action — the exact place a spend
    # dialog would be reintroduced first).
    for callee in ("generateTourPreview()", "authorTourDay()"):
        dispatching = [body for body in listeners if callee in body]
        assert dispatching, f"no click listener calls {callee} — the button is dead"
        for body in dispatching:
            for offset in _all_offsets(body, callee):
                window_start = max(body.rfind("{", 0, offset), body.rfind(")", 0, offset))
                assert window_start != -1, f"cannot locate the dispatch branch for {callee}"
                between = body[window_start + 1 : offset].strip()
                assert between in ("", "{"), (
                    f"something runs between the click and {callee}: "
                    f"{between!r}. A tourist taps once and the tour generates; any "
                    f"acknowledgement, overlay, checkbox or gate in between makes the "
                    f"workbench a different product from the app — and none of them need "
                    f"to say the word confirm."
                )

    # THE DIAL PATH (Phase 4, W4.2 panel): a dial turn re-plans the day through
    # one change listener -> scheduleTourReplan -> the debounced
    # generateTourPreview(). The debounce is not a gate — nobody is asked
    # anything; it folds a burst of turns into one request — so the scheduler
    # must reach the planner and must hold no dialog of its own.
    replan = _js_function_body(html, "function scheduleTourReplan()")
    assert len(replan) > 80, "the extracted body of scheduleTourReplan() is implausibly short"
    assert "generateTourPreview()" in replan, (
        "scheduleTourReplan no longer reaches generateTourPreview(), so the dials "
        "are dead and every 'the day re-plans itself' claim is unmeasured"
    )
    assert "confirm(" not in replan, (
        "the dial path shows a confirmation modal; a tourist turning a dial is "
        "never asked to approve anything"
    )


# --------------------------------------------------------------------------
# TEST 11 — the tour on screen is exactly the response the server sent
# --------------------------------------------------------------------------


def test_the_rendered_tour_is_exactly_the_server_response() -> None:
    """The cheapest spoof of all lives in the browser, not the server.

    Two shapes, neither of which touches Python:

    * a canned tour rendered through the real pipeline — the renderer called a
      second time from the failure branch with a hand-written object literal
      produces byte-for-byte the same DOM, badges and quality panels as a live
      Premium result; and
    * a label rewrite — one line between ``await resp.json()`` and the render
      call setting ``provider``/``compose_status``/``candidate_eligible``, so
      the narration is genuinely live but the tier and cost labels the owner
      judges by are forged.

    Both die on one derived property: the page renders the parsed response and
    nothing else. The page does this TWICE, because planning the day and
    writing it are two calls — so both stages are checked, each with exactly one
    call site, inside the function that performed its own fetch, whose first
    argument is the awaited JSON. No variable to patch, no second entry point, no
    wrapper. Both field lists are read off the endpoints' own Pydantic response
    models, so they cannot go stale.

    STAGES RE-DERIVED at S4.8 (Phase 4, design §8.1): ``renderTourOptions`` WAS
    the flavour UI and died with the pick — the plan reply's ``options[0]`` IS
    the day and ``renderTourDay`` draws it straight from ``generateTourPreview``'s
    fetch; the write moved from the per-card ``authorTourOption`` to the ONE
    ``authorTourDay``, which still hands ``renderTourStops`` its parsed reply.

    UNDO TEST: add a second call to either renderer, wrap an argument in anything,
    or assign to a response field before rendering -> RED.
    """
    from src.api.models.trips import TripAuthoredTourResponse, TripPreviewResponse

    html = REVIEW_HTML.read_text()

    # (fetching function, renderer, path it must have fetched, response model)
    stages = [
        (
            "async function generateTourPreview()",
            "renderTourDay(",
            "/trips/preview",
            TripPreviewResponse,
        ),
        (
            "async function authorTourDay()",
            "renderTourStops(",
            "/trips/preview/author",
            TripAuthoredTourResponse,
        ),
    ]

    for declaration, callee, path, model in stages:
        offsets = _all_offsets(html, callee)
        assert offsets, f"{callee} is missing from review.html"
        call_sites = [o for o in offsets if not html[:o].rstrip().endswith("function")]
        assert len(call_sites) == 1, (
            f"{callee} is called from {len(call_sites)} places. Exactly one call "
            f"site — the one holding the live {path} response — can be proved to "
            f"render what the server sent; a second one renders whatever its caller "
            f"made up, through the identical DOM, badges and quality panels the "
            f"owner reads as proof of a live Premium tour."
        )

        body = _js_function_body(html, declaration)
        assert callee in body, (
            f"the only {callee} call site is outside {declaration!r}, so nothing "
            f"ties what is rendered to the {path} fetch"
        )
        assert path in body, f"{declaration!r} no longer fetches {path}"

        arguments = _call_arguments(html, call_sites[0], callee)
        assert arguments, f"{callee} is called with no arguments"
        assert arguments[0] == "await resp.json()", (
            f"{callee} is handed {arguments[0]!r} rather than the parsed response "
            f"itself. Anything other than a bare `await resp.json()` — a variable, "
            f"an Object.assign, a spread, a merge — is a place to inject fields the "
            f"server never sent, and the owner cannot see the difference on screen."
        )

        fields = sorted(model.model_fields)
        assert fields, f"{model.__name__} declares no fields"
        for field in fields:
            for offset in _all_offsets(body, f".{field}"):
                tail = body[offset + len(field) + 1 :].lstrip()
                if tail.startswith("=") and not tail.startswith("=="):
                    raise AssertionError(
                        f"{declaration!r} writes to the response field {field!r} "
                        f"before rendering. The narration can be perfectly live "
                        f"while the tier, narrator and certification labels are "
                        f"forged — and those labels are what the owner judges cost "
                        f"and quality by."
                    )


# --------------------------------------------------------------------------
# TEST 12 — the selector defaults to the provider production pins
# --------------------------------------------------------------------------


def test_the_audio_selector_defaults_to_the_provider_production_pins() -> None:
    """The screenshotted bug lived here, and the static markup does not show it.

    ``loadTtsProviders`` wipes the ``<select>`` and rebuilds it from
    ``/audio/providers`` at runtime, so the pristine single-option markup test 1
    parses proves nothing about what ends up selected. The original defect was
    exactly this: the markup said ``openai`` and the JS force-selected ``mock``.

    Derived from render.yaml: the value production pins must be the value this
    function selects, and it must select exactly once — a second selection
    (an availability fallback: "if the pinned provider is unavailable, take the
    first one that is") would silently downgrade a human whenever the API key is
    missing or rate-limited.

    A source read, not an execution. The Playwright shard is what actually runs
    this page; see the file docstring.

    UNDO TEST: change which value loadTtsProviders selects, or add a second
    selection -> RED.
    """
    html = REVIEW_HTML.read_text()
    body = _js_function_body(html, "async function loadTtsProviders()")
    production_env = _render_yaml_env_assignments()
    pinned = production_env.get("TTS_PROVIDER")
    assert pinned, "render.yaml pins no TTS_PROVIDER to derive the expectation from"

    assert f"'{pinned}'" in body or f'"{pinned}"' in body, (
        f"loadTtsProviders never mentions {pinned!r}, the provider render.yaml "
        f"pins. Whatever it selects, it is not deriving it from production."
    )
    selections = _all_offsets(body, ".selected = true")
    assert len(selections) == 1, (
        f"loadTtsProviders performs {len(selections)} selections. One is the "
        f"pinned production provider; a second is a fallback, and a fallback is "
        f"how an editor gets silently downgraded to whatever happens to be "
        f"available when the real key is missing."
    )


# --------------------------------------------------------------------------
# TEST 13 — the Basic-lane disclosure is driven by the server's own fields
# --------------------------------------------------------------------------


def test_the_basic_lane_disclosure_is_driven_by_the_servers_own_fields() -> None:
    """A refused tour must never wear the Premium label.

    The backend genuinely returns an ungraded, non-certifiable grounded-stitch
    tour whenever candidate authoring is refused or ineligible. In the page,
    the entire disclosure hangs on one client-side boolean built from
    ``candidate_eligible`` and ``basic_tour``; "simplifying" that condition
    makes a degraded tour render the Premium narrator badge and the
    certification-eligible label.

    Derived from the response model: both fields exist on the reply that carries a
    WRITTEN tour, the renderer must consult both, and it must build the
    Premium-only quality panel exactly once (i.e. on one branch). They live on the
    author reply rather than the plan reply because there is no Basic fallback and
    no eligibility until something has actually been written.

    UNDO TEST: drop either field from the condition, or append the Premium panel
    unconditionally -> RED.
    """
    from src.api.models.trips import TripAuthoredTourResponse

    fields = set(TripAuthoredTourResponse.model_fields)
    required = {"candidate_eligible", "basic_tour"}
    assert required <= fields, (
        f"the authored-tour response no longer carries {sorted(required - fields)}; "
        f"the Basic-vs-Premium disclosure this test guards is derived from those "
        f"fields, so the derivation must be updated, not deleted"
    )

    html = REVIEW_HTML.read_text()
    body = _js_function_body(html, "function renderTourStops(")
    for field in sorted(required):
        assert field in body, (
            f"renderTourStops no longer consults {field!r}. Without it the page "
            f"cannot tell a refused Basic tour from a certifiable Premium one, "
            f"and the owner would grade an un-authored fallback as the product."
        )
    panels = _all_offsets(body, "buildNarrationQualityPanel(")
    assert len(panels) == 1, (
        f"renderTourStops builds the Premium narration panel {len(panels)} time(s). "
        f"It belongs on exactly one branch — the eligible one. Rendering it "
        f"unconditionally puts the Premium narrator badge on a Basic tour."
    )


# --------------------------------------------------------------------------
# TEST 14 — duration is the only bound on how many stops a tour has
# --------------------------------------------------------------------------

#: The 17-point set on which cheapest insertion is provably WORSE than the exact
#: solver, so "which orderer ran" is observable rather than asserted. MEASURED on
#: this fixture: exact 7300 s, cheapest-insertion 7526 s. 17 and not 25 on purpose
#: — at 25 the exact solver would never return, and a test that hangs is not RED.
_ORDERING_PROBE_SEED = 7
_ORDERING_PROBE_N = 17


def _probe_points(n: int, seed: int):
    """``n`` deterministic POIs in an ~1.8 km box, built without a corpus."""
    import random

    from src.tour.contract import POI

    rng = random.Random(seed)
    return [
        POI(
            id=f"p{i:02d}",
            name=f"p{i:02d}",
            tier=5,
            poi_role="stop",
            lat=_PDV[0] + rng.uniform(-0.008, 0.008),
            lng=_PDV[1] + rng.uniform(-0.008, 0.008),
        )
        for i in range(n)
    ]


_PDV = (48.85675, 2.341033)


def test_duration_is_the_only_stop_bound_on_the_planning_path(monkeypatch) -> None:
    """OWNER RULING 5 — "no stop limits, period" — measured, not asserted.

    Until 2026-08-04 the planner clamped every tour to at most 15 seated anchors
    (``selection.HARD_ANCHOR_CAP``) and the certification policy clamped the
    PREVIEW to 8, so the workbench structurally could not show a tour as long as
    one a tourist could be given. Both are gone. What bounds a tour now is the
    time budget and nothing else, on every surface.

    Removing a ceiling from an exponential solver is only safe if something
    catches the pathological case, so this test also pins the replacement:
    ``ordering.order_stops`` orders EXACTLY up to ``ORDERING_EXACT_MAX`` points
    and by cheapest insertion above it, and never drops a stop either way.

    UNDO (each turns this RED):
      * restore the ``min(HARD_ANCHOR_CAP, ...)`` clamp -> assertions 3 and 7;
      * restore ``max_stops=8`` in ``certification_planning_policy`` -> 3 and 7;
      * restore the ``1 <= max_stops <= 8`` validator message -> 7b;
      * set ``ORDERING_EXACT_MAX = 17`` -> assertions 5 and 6;
      * delete the ``TIMEBOX_REPAIR_MAX_TRIALS`` early return -> assertion 8.
    """
    from src.tour import selection
    from src.tour.contract import TourInput
    from src.tour.ordering import ORDERING_EXACT_MAX, cheapest_insertion_open, held_karp_open
    from src.tour.ordering import order_stops as _order_stops
    from src.tour.premium_tour import certification_planning_policy
    from src.tour.routing import RoutePlanningPolicy
    from src.tour.selection import select_route
    from tests.test_tour_selection import _poi, _snap

    # 1. A dense start area and a duration long enough to seat well past the old
    #    ceiling. Each place takes eight minutes to SEE (re-derived at the Phase 6
    #    close): a stop's length is max(visit, audio), and since S6.6 capped
    #    tellings at three minutes a lattice of talk-only places could not build
    #    half of 400 minutes — the one underfill line (Phase 5 S5.3) refused it
    #    at 156 of the 200 minutes required. A dense area of real places is
    #    places you go inside; with the visit priced, the fixture seats 31 (measured)
    #    in about three seconds. Two beats per POI keeps the greedy visit-bound
    #    rather than letting it terminate audio-bound.
    pois = [
        _poi(
            f"p{i:02d}",
            tier=5,
            lat=_PDV[0] + (i % 6) * 0.0004,
            lng=_PDV[1] + (i // 6) * 0.0004,
            beat_count=2,
        ).model_copy(update={"typical_duration_min": 8})
        for i in range(40)
    ]
    snap = _snap(pois)
    inp = TourInput(start=_PDV, duration_min=400, city_slug="paris")

    # 2. Planned on the CERTIFICATION path — the non-legacy branch, where the
    #    deleted ``planning_budget.max_stops`` guards lived (the priced-trial
    #    guard, the repair's "requires an authorized stop cap" ValueError, and the
    #    add-pass's ``len(base.ordered) < max_stops`` gate). The band is
    #    deliberately WIDE rather than the shipped 0.90-1.10: the shipped band is
    #    two-sided and a synthetic uniform lattice cannot fill 400 minutes of
    #    elapsed time, so the narrow band would refuse for a TIME reason and this
    #    test would never reach its stop-count claim. The 0.90-1.10 band is
    #    covered by tests/test_tour_certification_selection.py; what is under test
    #    here is the stop ceiling.
    no_cap_policy = RoutePlanningPolicy.certification(
        minimum_requested_fraction=0.05,
        maximum_requested_fraction=2.0,
        policy_id="test-no-cap",
    )
    # There is only ONE planning branch since step 6 deleted the legacy flat policy, so
    # this fixture reaches the same code the preview does; only the band differs.
    assert no_cap_policy.nominal_requested_fraction > 0
    route = select_route(inp, snap, planning_policy=no_cap_policy)

    # 3. THE AC-16 BEHAVIOUR: far more dwell stops than any deleted ceiling could
    #    produce. MEASURED on this fixture: 31 uncapped (32 before the Phase 6
    #    ceiling move), against exactly 16 with the old ceilings restored (15
    #    greedy anchors plus the one stop the certification repair's add-pass can
    #    contribute — a COUNT cap, which the visit re-derivation does not move).
    #    So the threshold is 20, not 16 — ">15" would have passed with every
    #    ceiling still in place, which is the precise way this assertion could
    #    have been a passenger.
    #
    #    20 is also past ORDERING_EXACT_MAX, so this route was ordered by the
    #    cheapest-insertion FALLBACK end to end: the assertion below is
    #    simultaneously the proof that the fallback works inside real planning,
    #    not just when called directly.
    assert len(route.pois) >= 20, (
        f"a 400-minute request in a dense area seated only {len(route.pois)} stops. "
        f"16 or fewer means a stop ceiling survives somewhere on the planning path; "
        f"this fixture seats 31 when duration is genuinely the only bound"
    )
    assert len(route.pois) > ORDERING_EXACT_MAX, (
        "the fixture no longer exercises the cheapest-insertion fallback inside "
        "select_route, so this test would stop proving the tractability half"
    )

    # 3b. The PREVIEW's own policy — where ``max_stops=8`` lived — still builds.
    #     Restoring that argument without restoring the field is a TypeError here,
    #     and restoring both is caught by assertion 7's AST scan.
    assert certification_planning_policy(policy_id="preview").nominal_requested_fraction == 1.0

    # 4. No stop is seated twice once the cap stops truncating the list.
    assert len(route.pois) == len({p.id for p in route.pois})

    # 5. THE DISPATCH: above the tractability wall the fallback runs, and it is a
    #    genuinely DIFFERENT (worse) order — so this observes which solver ran
    #    rather than trusting the branch.
    probe = _probe_points(_ORDERING_PROBE_N, _ORDERING_PROBE_SEED)
    assert _ORDERING_PROBE_N > ORDERING_EXACT_MAX
    dispatched = [p.id for p in _order_stops(probe, fixed_start=_PDV)]
    assert dispatched == [p.id for p in cheapest_insertion_open(probe, fixed_start=_PDV)]
    assert dispatched != [p.id for p in held_karp_open(probe, fixed_start=_PDV)], (
        "the dispatcher handed 17 points to the EXACT solver; that is the "
        "exponential path this fallback exists to keep off the planner"
    )
    # ...and it kept every stop. A fallback that drops stops is a product change.
    assert sorted(dispatched) == sorted(p.id for p in probe)

    # 6. THE CPU BOUND, at a size the exact solver could not survive. CPU time,
    #    not wall clock: a contended host must not make this flaky.
    wide = _probe_points(40, seed=11)
    t0 = time.process_time()
    _order_stops(wide, fixed_start=_PDV)
    assert time.process_time() - t0 < 1.0

    # 7. STRUCTURAL: no stop ceiling survives anywhere on the planning path.
    for module in ("premium_tour.py", "routing.py", "selection.py"):
        tree = ast.parse((SRC / "tour" / module).read_text())
        keywords = {
            kw.arg
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for kw in node.keywords
        }
        assert "max_stops" not in keywords, f"{module} still passes a max_stops="
        attributes = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        assert "max_stops" not in attributes, f"{module} still reads a .max_stops"
        assigned = {
            target.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign | ast.AnnAssign)
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
            if isinstance(target, ast.Name)
        }
        assert not assigned & {"HARD_ANCHOR_CAP", "ANCHOR_CAP_DIVISOR"}, (
            f"{module} re-declares a deleted anchor cap"
        )

    # 7b. The dead 1..8 validator went with the field it validated. Without this
    #     the clause is unreachable-but-present, and nothing else can see it.
    routing_source = (SRC / "tour" / "routing.py").read_text()
    assert "certification planning supports one to eight stops" not in routing_source

    # 8. The repair's own trial bound is real, not decoration. Deleting the stop
    #    ceiling also removed what used to bound the repair's |incumbents| x |pool|
    #    enumeration, so TIMEBOX_REPAIR_MAX_TRIALS is now the only thing standing
    #    between a long tour and a quadratic pile of exponential orderings.
    #
    #    Asserting "a route still comes back" is NOT enough on its own — that is
    #    also true when the bound is deleted. So this counts the pricing calls the
    #    repair actually makes. MEASURED on this fixture: 321 unbounded, 35 with
    #    the bound squeezed to 3. Deleting the early return puts it back to 321.
    priced: list[int] = []
    real_trial = selection._certification_route_trial

    def _counting_trial(*args, **kwargs):
        priced.append(1)
        return real_trial(*args, **kwargs)

    monkeypatch.setattr(selection, "_certification_route_trial", _counting_trial)
    monkeypatch.setattr(selection, "TIMEBOX_REPAIR_MAX_TRIALS", 3)
    squeezed = select_route(inp, snap, planning_policy=no_cap_policy)
    assert squeezed.pois, (
        "truncating the timebox repair to 3 trials made planning refuse outright; "
        "the bound must degrade the repair, never fail the request"
    )
    assert len(priced) < 60, (
        f"the timebox repair priced {len(priced)} trials with its bound squeezed to "
        f"3, so the bound is not being enforced (unbounded on this fixture is 321)"
    )


# --------------------------------------------------------------------------
# TEST 15 — a tolerated divergence stays where it was tolerated
# --------------------------------------------------------------------------


def test_a_flag_allowed_to_differ_never_steers_the_request_path() -> None:
    """The subtlest attack rides on a divergence that is already legitimate.

    ``WORKBENCH_API_ENABLED`` is genuinely ``true`` locally and ``false`` in
    production — it gates mounting the unauthenticated CRUD routers, and every
    parity check is therefore forced to let it through. That makes it the
    perfect carrier: a second read site inside a request path
    (``if _workbench_api_enabled(): plan = plan_premium_tour(..., faster)``)
    inherits a divergence the guards were told is intentional.

    Derived: any variable the workbench sets to a value production pins
    DIFFERENTLY may only be consulted where the divergence itself is decided —
    the app factory (the function that constructs the FastAPI app) and the
    dependency callables that gate routes. It may never be read, directly or
    through a predicate, inside a route handler's own body.

    UNDO TEST: read the flag (or call a function that reads it) inside any
    routed handler -> RED.
    """
    production_env = _render_yaml_env_assignments()
    overrides = _workbench_overrides()
    # Implementation-SELECTING divergences belong to the parity tests above,
    # which judge them by whether production's own choice can be constructed
    # here. What is left is the flag class: a boolean the workbench flips for a
    # reason that has nothing to do with which implementation runs.
    dispatch_variables: set[str] = set()
    for dispatcher in _implementation_dispatchers():
        dispatch_variables |= dispatcher["env_names"]
    divergent = {
        name
        for name, value in overrides.items()
        if name in production_env and production_env[name] != value
    } - dispatch_variables
    assert divergent, (
        "no variable diverges between the workbench and production, so this "
        "test guards nothing. It exists because a legitimate divergence is the "
        "safest place to hide an illegitimate one — if the set is genuinely "
        "empty, say so here rather than deleting the check."
    )

    # Functions anywhere in src/ whose body reads one of those variables.
    carriers: dict[str, set[str]] = {}
    for _path, tree in _source_modules():
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            read = {
                name
                for sub in ast.walk(node)
                for name in [_env_read_name(sub, tree)]
                if name in divergent
            }
            if read:
                carriers.setdefault(node.name, set()).update(read)
    assert carriers, (
        f"nothing in src/ reads {sorted(divergent)} — the divergence is inert, so "
        f"either the derivation is broken or the variable is dead"
    )

    routes_dir = SRC / "api" / "routes"
    offenders: list[str] = []
    for path in sorted(routes_dir.glob("*.py")):
        tree = ast.parse(path.read_text())
        module_name = ".".join(path.relative_to(REPO).with_suffix("").parts)
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            routed = any(
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and isinstance(dec.func.value, ast.Name)
                and dec.func.value.id == "router"
                for dec in node.decorator_list
            )
            if not routed:
                continue
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Name)
                    and sub.func.id in carriers
                ):
                    offenders.append(f"{module_name}.{node.name} -> {sub.func.id}()")
                read = _env_read_name(sub, tree)
                if read in divergent:
                    offenders.append(f"{module_name}.{node.name} reads {read}")
    assert not offenders, (
        f"{offenders} consult a flag that is deliberately different in the "
        f"workbench and production. Every parity guard is forced to tolerate that "
        f"difference, so anything riding on it changes the product the owner sees "
        f"while looking entirely intentional. Route handlers must behave "
        f"identically in both environments; keep the divergence in the app "
        f"factory and the route gates, where it is decided."
    )


# --------------------------------------------------------------------------
# TESTS 16-19 — ONE ENGINE: the phone and the workbench run the SAME OBJECTS
# --------------------------------------------------------------------------
#
# THE OWNER'S QUESTION, 2026-08-04: "prove to me that the app and the workbench
# ultimately invoke the exact same tour logic code end-to-end. No duplicate, no
# separate modules with the logic. The exact same code."
#
# Two client programs in two languages — the phone is Dart
# (``mobile/lib/services/trip_service.dart``), the workbench is JavaScript
# (``frontend/review.html``) — and both cross into Python at
# ``src/api/routes/trips.py``. So the question reduces to a single one that can be
# answered by running something: DO THOSE HANDLERS EXECUTE THE SAME FUNCTIONS?
#
# WHY NOT A TRACER. The obvious instrument — ``sys.settrace``, record every code
# object executed under ``src/tour``, compare the two sets — is unsound HERE.
# FastAPI runs a synchronous handler on a worker thread taken from a pool, and
# ``sys.settrace`` instruments only the thread it was set on. A trace that comes
# back EMPTY is then indistinguishable from a trace that comes back CLEAN, and a
# proof may not have a failure mode that looks like success.
#
# THE INSTRUMENT USED INSTEAD — SINGLE-POINT INTERCEPTION. Break ONE shared object
# and require BOTH surfaces to break. A duplicate implementation cannot hide from
# that, because a duplicate is by definition not the object that was broken. This
# is the project's own undo-test discipline pointed at architecture rather than at
# a bug, and unlike a tracer it is thread-safe: the substitution lives on the
# module object, so it applies whichever worker thread the handler lands on.
#
# WHY EVERY ALIAS IS SUBSTITUTED, NOT ONE. ``from x import y`` creates a SECOND
# binding. Replacing the attribute on the DEFINING module intercepts calls that
# resolve through that module's globals — including a delegate's own internal call
# — but NOT a caller that imported the name and holds its own reference; replacing
# it on the CALLER catches the opposite half. Patch one and a surface reached
# through the other name looks like a divergence that is not there, or worse like
# a convergence that is not there. So the binding set is DERIVED from the import
# graph (:func:`_seam_bindings`) and every member is replaced at once. A module
# that starts importing the planner tomorrow is covered without anyone remembering
# to add it here.
#
# WHAT IS AND IS NOT STOOD IN FOR. These four tests replace the corpus reader, the
# walking-times client and the Neo4j session — the DATA SOURCES and the STORE. They
# never replace the planner or the authoring seam, which are the subject: standing
# in for those would be assuming the answer. Test 16 asserts, by object identity,
# that the objects reached are the real ones.


#: The two convergence points every tour request passes through, whichever client
#: asked. These are the SUBJECTS of tests 16-18 — the things whose identity is being
#: proven — exactly as ``TOURIST_FACING_FUNCTIONS`` at the top of this file names the
#: surfaces under test. They are not an allowlist of blessed implementations: which
#: modules bind them, which handlers reach them, and whether each is one object or
#: several, are all derived below rather than stated.
#:
#: Re-derived at S4.8 (Phase 4, design §8.1): ``plan_premium_options`` — the
#: K-option entry point ``plan_premium_tour`` used to delegate to — is deleted,
#: so Block 1 is now literally ONE name. The invariant is unchanged: one planner,
#: one authoring seam, on both surfaces.
PLAN_BLOCK = ("plan_premium_tour",)
AUTHOR_BLOCK = ("execute_premium_plan", "finalize_premium_tour")

#: Where both blocks are defined. Everything else about them is derived.
ENGINE_MODULE = "src.tour.premium_tour"

#: The phone's two calls and the workbench's two, as the HTTP layer sees them.
PHONE_PLAN_PATH = "/api/v1/trips/generate"
PHONE_AUTHOR_PATH = "/api/v1/trips/{trip_id}/compose"
WORKBENCH_PLAN_PATH = "/api/v1/trips/preview"
WORKBENCH_AUTHOR_PATH = "/api/v1/trips/preview/author"

_PROOF_USER_ID = "one-engine-proof-user"
_PROOF_PROFILE_ID = "one-engine-proof-profile"


class _SeamBrokenError(Exception):
    """Raised by the stand-in installed on every binding of a shared seam."""


# --------------------------------------------------------------------------
# derivation helpers — which names can reach the two blocks
# --------------------------------------------------------------------------


def _resolved_import(node: ast.ImportFrom, package: str) -> str:
    """The absolute module an ``ImportFrom`` names, resolving a relative one."""
    if not node.level:
        return node.module or ""
    parts = package.split(".")
    base = parts[: len(parts) - node.level + 1]
    return ".".join([*base, node.module] if node.module else base)


def _dotted_name(path: pathlib.Path) -> str:
    parts = list(path.relative_to(REPO).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


@cache
def _seam_bindings(names: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    """Every ``(module, attribute)`` under ``src/`` through which ``names`` is callable.

    Read off the import graph, never listed. Two kinds of binding exist and both
    matter: the attribute on the module that DEFINES the function, and the attribute
    a module gets when it writes ``from src.tour.premium_tour import <name>``. An
    ``as`` alias is followed, because the local spelling is what the caller's globals
    hold and therefore what a substitution has to replace.
    """
    wanted = set(names)
    found: list[tuple[str, str]] = []
    for path, tree in _source_modules():
        dotted = _dotted_name(path)
        package = dotted.rsplit(".", 1)[0] if "." in dotted else ""
        defined = {
            node.name
            for node in tree.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        found.extend((dotted, name) for name in sorted(wanted & defined))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if _resolved_import(node, package) != ENGINE_MODULE:
                continue
            found.extend(
                (dotted, alias.asname or alias.name)
                for alias in node.names
                if alias.name in wanted
            )
    return tuple(sorted(set(found)))


def _bound_object(dotted: str, attribute: str) -> object:
    return getattr(importlib.import_module(dotted), attribute)


def _assert_bindings_are_usable(bindings: tuple[tuple[str, str], ...], names) -> None:
    """Non-vacuity: the derivation really found the request path before anything else.

    A binding sweep that silently found nothing would make every "both surfaces
    broke" assertion below pass for free, since nothing would have been replaced and
    nothing would have been called. So this fails LOUDLY on an empty or partial
    derivation rather than letting the interception tests report a green vacuum.
    """
    assert bindings, f"derived no binding at all for {sorted(names)} — the sweep is broken"
    modules = {dotted for dotted, _ in bindings}
    assert ENGINE_MODULE in modules, (
        f"{ENGINE_MODULE} does not define {sorted(names)} any more; the convergence "
        f"point moved and these tests are watching the wrong thing"
    )
    assert "src.api.routes.trips" in modules, (
        f"no route module binds {sorted(names)}, so neither client can be reaching "
        f"them — either the handlers moved or the import sweep is broken"
    )


# --------------------------------------------------------------------------
# the harness: one process, one corpus, both surfaces
# --------------------------------------------------------------------------


class _StubGraph:
    """The smallest thing that can stand in for Neo4j across the phone's two routes.

    It is a STORE, not tour logic. It answers the three record-shaped reads the trip
    routes make (does this user own this profile, does this user own this trip, what
    compose inputs were persisted), remembers the two JSON blobs ``/trips/generate``
    writes so ``/trips/{id}/compose`` can read them back, and reports that it created
    exactly the relationships it was asked to create. Every iterating read (profile
    lens preferences, lens display labels, per-beat audio) is legitimately empty here,
    which is the same shape a fresh graph gives.

    Standing in for the DATABASE is what keeps these tests $0 and deterministic.
    Standing in for the PLANNER or the AUTHORING SEAM would assume the very thing
    under test, so neither is ever touched — see test 16, which asserts by object
    identity that the ones reached are the real ones.
    """

    def __init__(self, profile_id: str) -> None:
        self._record: dict[str, Any] = {
            "id": profile_id,
            "pid": profile_id,
            "ti": None,
            "oj": None,
            "composed": None,
            # Phase 5 (design §4) gave the trip a plan_version (``pv``, 0 on a
            # fresh graph) and a session blob (``sj``, none until the day starts)
            # that the compose-inputs read returns. Re-derived at the Phase 6
            # close: this STORE must answer every record-shaped read the routes
            # make, and the Phase 5 commit grew the read without growing it.
            "pv": 0,
            "sj": None,
            "edges": 0,
            # W7.13 (Sofia's finish): the item CREATE now reports whether the stop's
            # POI exists (`poi_found`) so a wrong id fails loudly and the A→B finish
            # sentinel is stored. On this stand-in every non-sentinel POI "exists".
            "poi_found": True,
            # The family split gave the trip routes a roles read (captain or
            # crew); this store's one user captains its one trip.
            "roles": ["IS_CAPTAIN_OF"],
        }

    def run(self, _cypher: str, **params: Any) -> _StubGraph:
        if params.get("tour_input_json") is not None:
            self._record["ti"] = params["tour_input_json"]
        if params.get("options_json") is not None:
            self._record["oj"] = params["options_json"]
        if params.get("beat_ids") is not None:
            self._record["edges"] = len(params["beat_ids"])
        return self

    def single(self) -> dict[str, Any]:
        return dict(self._record)

    def __iter__(self):
        return iter(())

    def execute_write(self, unit_of_work):
        return unit_of_work(self)


# ---------------------------------------------------------------------------
# The dense GREEN corpus + mock-transport routing client. MOVED HERE from
# tests/test_tour_flavours.py at Phase 4's D4.0: that file was the k-flavours
# suite and died with select_k_routes (design §8.1), and this file was the only
# other importer of these two fixtures — an import that cannot resolve does not
# fail loudly at collection, it fails at run time, so the fixtures move INTO
# their importer before the file dies (audit E §5.3).
#
# 4 directional clusters x 5 rich POIs (20 tier-5 anchors, 300s dwell each),
# reaching ~533m from the start — inside the 60-min one-way envelope (738m).
# ---------------------------------------------------------------------------


def _grid_pois() -> list:
    from tests.test_tour_selection import PDV, _poi

    out = []
    directions = [(0.003, 0.0), (0.0, 0.0045), (-0.003, 0.0), (0.0, -0.0045)]
    for d, (dlat, dlng) in enumerate(directions):
        for i in range(5):
            scale = 1.0 + 0.15 * i
            out.append(
                _poi(
                    f"poi-{d}-{i}",
                    lat=PDV[0] + dlat * scale,
                    lng=PDV[1] + dlng * scale,
                    areas=("Le Marais",),
                )
            )
    return out


def _rich_beats(pois) -> dict:
    from src.tour.contract import BeatRef

    return {
        p.id: [
            BeatRef(
                id=f"{p.id}-b{i}",
                poi_id=p.id,
                est_spoken_seconds=240,
                active_status="active",
                script_body=f"Story {i} about {p.id}.",
                lenses=("hidden_history",) if i % 2 == 0 else ("historic_arch",),
            )
            for i in range(5)
        ]
        for p in pois
    }


def _dense_snap():
    from tests.test_tour_selection import _snap

    pois = _grid_pois()
    return _snap(
        pois,
        area_types={"Le Marais": "neighborhood"},
        beats_by_poi=_rich_beats(pois),
    )


def _flavour_mock_handler(request):
    """Generous isochrone + haversine-proportional routed legs."""
    import json

    import httpx

    if request.url.path == "/isochrone":
        ring = [[2.20, 48.80], [2.45, 48.80], [2.45, 48.92], [2.20, 48.92], [2.20, 48.80]]
        return httpx.Response(
            200,
            json={
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature", "properties": {},
                     "geometry": {"type": "Polygon", "coordinates": [ring]}}
                ],
            },
        )
    if request.url.path == "/route":
        from src.tour.routing import haversine_m

        body = json.loads(request.content)
        a, b = body["locations"]
        d = haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
        return httpx.Response(
            200,
            json={"trip": {"legs": [{"summary": {"time": round(d * 1.3), "length": d / 1000.0},
                                     "shape": "flavour_mock_shape"}]}},
        )
    return httpx.Response(404)


def _mock_routing_client():
    import httpx

    from src.tour.routing_client import RoutingClient

    http = httpx.Client(
        transport=httpx.MockTransport(_flavour_mock_handler), base_url="http://valhalla.test"
    )
    return RoutingClient(client=http)


def _proof_client(monkeypatch, graph: _StubGraph):
    """A TestClient on the real app, given one fixed corpus and one routing client.

    Both surfaces are served by the SAME app object in the SAME process, so a
    difference between them cannot come from configuration, environment, process
    state or the order the two ran in.

    The corpus reader and the walking-times client are replaced ONCE, on the route
    module, so both surfaces get byte-identical inputs — that is what makes the
    output comparison in test 19 mean something. The routing client is the
    mock-transport one defined above: a stubbed upstream SERVICE,
    which the no-mocks rule permits and in fact requires, since the alternative is a
    live Valhalla whose answers can move between two back-to-back requests.

    The app's lifespan is deliberately NOT entered: it opens a real Neo4j driver, and
    every database dependency is overridden here anyway.
    """
    from fastapi.testclient import TestClient

    from src.api.app import create_app
    from src.api.auth.dependencies import get_current_user
    from src.api.dependencies import get_driver, get_session
    from src.api.routes import trips

    monkeypatch.setenv("ONDOWAY_ALLOW_INSECURE_AUTH_SECRETS", "1")
    snapshot = _dense_snap()
    monkeypatch.setattr(trips, "load_paris_corpus", lambda *_a, **_k: snapshot)
    monkeypatch.setattr(trips, "RoutingClient", _mock_routing_client)

    app = create_app()
    app.dependency_overrides[get_session] = lambda: graph
    app.dependency_overrides[get_driver] = lambda: graph
    app.dependency_overrides[get_current_user] = lambda: {"id": _PROOF_USER_ID}
    return TestClient(app)


def _plan_request(**extra: Any) -> dict[str, Any]:
    """One request body, shared by every surface, so nothing differs but the route."""
    from tests.test_tour_selection import PDV

    return {
        "center_lat": PDV[0],
        "center_lng": PDV[1],
        "duration_min": 60,
        "city_slug": "paris",
        "round_trip": False,
        **extra,
    }


def _generate_request(**extra: Any) -> dict[str, Any]:
    return _plan_request(
        profile_id=_PROOF_PROFILE_ID,
        start_date="2026-08-05",
        end_date="2026-08-05",
        **extra,
    )


def _post(client, path: str, body: dict[str, Any]):
    """POST and report a surface that could not answer at all as ``None``."""
    try:
        return client.post(path, json=body)
    except _SeamBrokenError:
        return None


def _dwell_poi_ids(option: dict[str, Any]) -> list[str]:
    """The stops of an option, in order. Dwell cards only, and that filter is required.

    A ``leg`` card deliberately repeats its ARRIVAL stop's ``poi_id`` (see
    ``RouteOptionStop`` in src/tour/contract.py: "a consumer matching POI ids must
    therefore filter to band == 'dwell'"), so an unfiltered id list legitimately
    contains duplicates and would compare two different things.
    """
    return [stop["poi_id"] for stop in option["stops"] if stop["band"] == "dwell"]


# --------------------------------------------------------------------------
# TEST 16 — the two surfaces name ONE planner and ONE authoring seam
# --------------------------------------------------------------------------


def test_the_phone_and_the_workbench_name_one_planner_and_one_author() -> None:
    """AC-1 — object identity, which is stricter than any source comparison.

    The check is ``is``, not ``==``. Two byte-identical copies of the planner living
    in two modules would diff clean, pass every text search, satisfy any "same
    algorithm" review — and FAIL here, because they are two objects. That is exactly
    the failure the owner asked about: not "does the workbench run something similar"
    but "does it run the same code".

    Every binding is checked, not the obvious one. ``from x import y`` gives the
    importer its own reference, so "the route module's planner" and "the engine
    module's planner" are two attributes that can drift apart in one edit.

    This test says nothing about whether the handlers CALL these objects — that is
    tests 17 and 18, which break them and watch both surfaces fail.

    UNDO TEST: in ``src/api/routes/trips.py``, replace the
    ``from src.tour.premium_tour import plan_premium_tour`` binding with a local
    copy of the function (however faithful) -> RED on the PLAN block; do the same for
    ``execute_premium_plan`` -> RED on the AUTHOR block.
    """
    for block in (PLAN_BLOCK, AUTHOR_BLOCK):
        bindings = _seam_bindings(block)
        _assert_bindings_are_usable(bindings, block)

        # Group every derived binding by the name it ORIGINATES from, then require
        # each group to be one single object. Grouping by the engine module's own
        # attribute is what makes an alias (`import ... as ...`) comparable.
        canonical = {name: _bound_object(ENGINE_MODULE, name) for name in block}
        divergent: dict[str, list[str]] = {}
        for dotted, attribute in bindings:
            resolved = _bound_object(dotted, attribute)
            matches = [name for name, obj in canonical.items() if obj is resolved]
            if not matches:
                divergent.setdefault(f"{dotted}.{attribute}", []).append(
                    f"resolves to {resolved!r}, which is none of {sorted(canonical)}"
                )
        assert not divergent, (
            f"a module reaches the tour engine through its OWN object rather than the "
            f"one {ENGINE_MODULE} defines: {divergent}. Byte-identical source is not "
            f"enough — a copy is a second implementation the moment either one is "
            f"edited, and the workbench would then be judging a tour the phone cannot "
            f"produce."
        )

    # Re-derived at S4.8 (Phase 4, design §8.1): plan_premium_tour IS Block 1 —
    # the K-option entry point it used to delegate to was deleted with the
    # flavours, so "ONE planner" is now literally one name. A returning
    # plan_premium_options would hand the two surfaces a second planning name to
    # drift through — exactly this test's subject — so its absence is asserted
    # rather than assumed. (Which callers reach the one planner is
    # tests/test_tour_one_engine.py's THE_ONE_PLANNER anchor, not restated here.)
    from src.tour import premium_tour

    assert not hasattr(premium_tour, "plan_premium_options"), (
        "plan_premium_options is back in src/tour/premium_tour.py. Phase 4 "
        "(design §8.1) deleted the K-option planner and narrowed PLAN_BLOCK to "
        "the one day-planner; a second planning entry point is a second name the "
        "phone and the workbench can each pick, which is the divergence this "
        "test exists to make impossible."
    )


# --------------------------------------------------------------------------
# TEST 17 — breaking the ONE planner breaks BOTH surfaces (BLOCK 1)
# --------------------------------------------------------------------------


def test_breaking_the_one_planner_breaks_both_surfaces(monkeypatch) -> None:
    """AC-1 / AC-4 — the load-bearing proof, and the only one a duplicate cannot dodge.

    Every binding through which the planner can be reached is replaced with a
    stand-in that records who called it and then refuses. Then the phone's planning
    call and the workbench's planning call are both made, against the same app in the
    same process. Two things must be true of EACH surface:

      * the stand-in fired exactly ONCE — so that surface really went through the one
        replaced object, rather than through some other route to the same answer; and
      * the surface could not answer — so it has no second implementation to fall back
        on. A surface that still returns a plan when the shared planner is broken was
        never using the shared planner.

    Either assertion alone is weak. Together they are the whole claim: this surface
    went through that object, and it cannot produce a tour without it.

    Re-derived at S4.8 (Phase 4, design §8.1): the one planner is
    ``plan_premium_tour`` alone — ``select_k_routes`` and the K-option entry
    point are deleted, so the stand-in now replaces exactly one name's bindings
    and "fired exactly once" reads directly as "this surface planned its ONE day
    through the one Block".

    UNDO TEST: reintroduce a private planning path on either handler — restore the
    deleted ``_preview_stops`` interleave, or inline a copy of the day-planner
    into ``generate_trip`` the way a direct ``select_k_routes(...)`` call lived
    there until 2026-08-04 — and that surface's pair of assertions goes RED: it
    answers 201/200 without the stand-in ever firing.
    """
    bindings = _seam_bindings(PLAN_BLOCK)
    _assert_bindings_are_usable(bindings, PLAN_BLOCK)

    calls: list[str] = []

    def _refuse(*_args: Any, **_kwargs: Any):
        calls.append("plan")
        raise _SeamBrokenError("the one planner was replaced for this test")

    for dotted, attribute in bindings:
        monkeypatch.setattr(importlib.import_module(dotted), attribute, _refuse)

    client = _proof_client(monkeypatch, _StubGraph(_PROOF_PROFILE_ID))

    del calls[:]
    workbench = _post(client, WORKBENCH_PLAN_PATH, _plan_request())
    workbench_calls = len(calls)

    del calls[:]
    phone = _post(client, PHONE_PLAN_PATH, _generate_request())
    phone_calls = len(calls)

    assert workbench_calls == 1, (
        f"the workbench's plan request went through the shared planner "
        f"{workbench_calls} times, not once. Zero means it planned the tour some "
        f"other way — a second implementation, which is precisely what must not "
        f"exist. More than one means an alias was reached twice and the surface's "
        f"call count can no longer be read."
    )
    assert phone_calls == 1, (
        f"the phone's plan request went through the shared planner {phone_calls} "
        f"times, not once. Zero means POST /trips/generate has its own planner again, "
        f"as it did until 2026-08-04 when it called select_k_routes directly with no "
        f"planning policy and quietly produced a shorter walk than the workbench did "
        f"for the identical request."
    )
    assert workbench is None, (
        f"with the shared planner broken, the workbench still answered HTTP "
        f"{workbench.status_code}. It is planning tours with something else."
    )
    assert phone is None, (
        f"with the shared planner broken, the phone still answered HTTP "
        f"{phone.status_code}. It is planning tours with something else."
    )


# --------------------------------------------------------------------------
# TEST 18 — breaking the ONE authoring seam breaks BOTH surfaces (BLOCK 2)
# --------------------------------------------------------------------------


def test_breaking_the_one_author_seam_breaks_both_surfaces(monkeypatch) -> None:
    """AC-1 / AC-8 — the same proof for the half that writes the words.

    Planning and authoring are two separate convergence points, and proving one says
    nothing about the other. This drives the phone's ``POST /trips/{id}/compose`` and
    the workbench's ``POST /trips/preview/author`` — a real generate-then-compose
    round trip on the phone's side, so the compose call reads back the very inputs the
    generate call persisted.

    The outcome assertion differs from test 17's for one honest reason: the workbench
    authoring route wraps its seam in a blanket ``except Exception`` and falls back to
    the Basic lane, so it answers 200 whatever happens. "Could not answer" is
    therefore read off the BODY rather than the status — no authored option, and a
    narration kind that is explicitly not the LLM candidate. The phone's route has no
    such fallback and simply cannot answer.

    Re-derived at S4.8 (Phase 4, design §8.1): the plan reply's ``options`` is a
    one-element list — THE day — and its ``route_id`` is read off that reply
    exactly as the page reads it; the opt-N spelling survives only as the wire
    convention (deviation i: ``preview-<12hex>-opt1`` here, ``{trip_id}-opt1``
    on the phone), not as a pick among N.

    UNDO TEST: give either handler its own authoring path — reintroduce the deleted
    ``author_prebuilt_route`` seam and call it from one of them — and that surface's
    assertions go RED: it produces an authored tour with the stand-in never firing.
    """
    plan_bindings = _seam_bindings(PLAN_BLOCK)
    author_bindings = _seam_bindings(AUTHOR_BLOCK)
    _assert_bindings_are_usable(author_bindings, AUTHOR_BLOCK)

    graph = _StubGraph(_PROOF_PROFILE_ID)
    client = _proof_client(monkeypatch, graph)

    # 1. Plan normally on both surfaces first — authoring needs something to author,
    #    and the planner must be untouched while that happens.
    generated = client.post(PHONE_PLAN_PATH, json=_generate_request())
    assert generated.status_code == 201, generated.text
    trip = generated.json()
    assert trip["options"], "the phone planned no options, so there is nothing to author"

    previewed = client.post(WORKBENCH_PLAN_PATH, json=_plan_request())
    assert previewed.status_code == 200, previewed.text
    preview_options = previewed.json()["options"]
    assert preview_options, "the workbench planned no options, so there is nothing to author"

    # 2. NOW break the authoring seam — every binding of both of its functions.
    calls: list[str] = []

    def _refuse(*_args: Any, **_kwargs: Any):
        calls.append("author")
        raise _SeamBrokenError("the one authoring seam was replaced for this test")

    for dotted, attribute in author_bindings:
        monkeypatch.setattr(importlib.import_module(dotted), attribute, _refuse)
    # The planner stays REAL, and is checked to be: this test is about Block 2, and
    # both authoring routes legitimately re-derive their plan before writing it. If
    # the planner had been caught by the substitution too, "the seam fired once"
    # would be measuring a request that never got past planning.
    still_real = {
        _bound_object(dotted, attribute) is _refuse for dotted, attribute in plan_bindings
    }
    assert still_real == {False}, "the planner was replaced as well; this is a Block-2 test"

    del calls[:]
    workbench = client.post(
        WORKBENCH_AUTHOR_PATH,
        json=_plan_request(route_id=preview_options[0]["route_id"]),
    )
    workbench_calls = len(calls)

    del calls[:]
    phone = _post(
        client,
        PHONE_AUTHOR_PATH.format(trip_id=trip["trip_id"]),
        {"route_id": f"{trip['trip_id']}-opt1"},
    )
    phone_calls = len(calls)

    assert workbench_calls == 1, (
        f"the workbench's authoring request went through the shared seam "
        f"{workbench_calls} times, not once. Zero means the workbench writes tour "
        f"narration with an implementation the phone does not use, which makes every "
        f"judgement the owner forms in the workbench a judgement about a different "
        f"product."
    )
    assert phone_calls == 1, (
        f"the phone's compose request went through the shared seam {phone_calls} "
        f"times, not once. Zero means POST /trips/{{id}}/compose has its own author "
        f"again, as it did while the second seam in authoring.py was alive."
    )
    assert workbench.status_code == 200, workbench.text
    authored = workbench.json()
    assert authored["option"] is None and authored["narration_kind"] != "llm_candidate", (
        f"with the shared authoring seam broken, the workbench still produced an "
        f"authored tour ({authored['narration_kind']!r}). It is writing narration "
        f"with something else."
    )
    assert phone is None, (
        f"with the shared authoring seam broken, the phone's compose still answered "
        f"HTTP {phone.status_code}. It is writing narration with something else."
    )


# --------------------------------------------------------------------------
# TEST 19 — identical inputs, identical tour, on both surfaces
# --------------------------------------------------------------------------


def test_both_surfaces_plan_the_identical_tour(monkeypatch) -> None:
    """AC-7 — the outcome the object identity above is supposed to buy.

    Tests 16-18 prove the two surfaces run one implementation. This proves the thing
    the owner actually cares about downstream of that: the same request produces the
    same walk. Same start, same duration, same city, same lenses, in ONE process
    against ONE corpus, back to back — and the stop lists and arrival times must
    match option for option.

    WHY THIS IS A FAIR COMPARISON RATHER THAN A COINCIDENCE. Planning is a pure
    function of the request, the corpus and the walking times: nothing in selection
    reads a clock, a random source or a fresh identifier, and the corpus is loaded in
    a fixed order. The two residual sources of drift are the corpus and the routing
    service, which are live in production and can genuinely change BETWEEN two calls
    — that is exactly why the workbench's authoring route refuses a stale plan with
    409 rather than writing a different tour than the one shown. Here both are fixed
    for the duration of the test, so any difference that appears is a difference in
    CODE, which is what is being measured.

    Compared on the two fields that define a walk: the ordered dwell-stop POI ids
    (where the tourist stands, in what order) and ``eta_seconds`` (how long it takes).
    The two payloads legitimately differ elsewhere — the preview blanks narration and
    drops the walking cards because nothing has been written yet, and the route ids
    are minted from different things — and demanding those match would be demanding
    the two surfaces stop having different jobs.

    Re-derived at S4.8 (Phase 4, design §8.1): there is ONE day now, not a set of
    flavours to agree on. Both surfaces must return ``options`` as a one-element
    list, and that one day must match stop for stop — ``zip(..., strict=True)``
    over one element is exactly the claim.

    UNDO TEST: make either surface plan from a different corpus or a different walk
    budget — or hand one of them a second option again — and the count or the
    ordered-id assertion goes RED naming the option.
    """
    client = _proof_client(monkeypatch, _StubGraph(_PROOF_PROFILE_ID))

    previewed = client.post(WORKBENCH_PLAN_PATH, json=_plan_request())
    assert previewed.status_code == 200, previewed.text
    generated = client.post(PHONE_PLAN_PATH, json=_generate_request())
    assert generated.status_code == 201, generated.text

    workbench_options = previewed.json()["options"]
    phone_options = generated.json()["options"]

    # Non-vacuity BEFORE equality: two empty lists are equal and prove nothing.
    assert workbench_options, "the workbench returned no options at all"
    assert phone_options, "the phone returned no options at all"
    assert len(_dwell_poi_ids(workbench_options[0])) > 1, (
        "the first option has at most one stop, so an ordered-id comparison cannot "
        "distinguish agreement from coincidence — the fixture has drifted"
    )

    # THE day, on both surfaces: a one-element list (design §8.1; the field stays
    # a list because stored pre-Phase-4 payloads and the opt-N route ids are
    # list-shaped — deviation i).
    assert len(workbench_options) == 1, (
        f"the workbench planned {len(workbench_options)} options; since design "
        f"§8.1 the plan reply carries exactly ONE day"
    )
    assert len(phone_options) == 1, (
        f"the phone planned {len(phone_options)} options; since design §8.1 the "
        f"plan reply carries exactly ONE day"
    )

    for index, (mine, theirs) in enumerate(
        zip(workbench_options, phone_options, strict=True), start=1
    ):
        assert _dwell_poi_ids(mine) == _dwell_poi_ids(theirs), (
            f"option {index} visits different places, or the same places in a "
            f"different order, depending on which client asked:\n"
            f"  workbench: {_dwell_poi_ids(mine)}\n"
            f"  phone:     {_dwell_poi_ids(theirs)}\n"
            f"The owner judges tours in the workbench; a tourist walks the phone's."
        )
        assert mine["eta_seconds"] == theirs["eta_seconds"], (
            f"option {index} is offered as {mine['eta_seconds']}s in the workbench "
            f"and {theirs['eta_seconds']}s on the phone for the same walk."
        )

