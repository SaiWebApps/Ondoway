"""Checks about the TEST SUITE, not the product.

Every other file here asks "does the code work?". This one asks "does the suite
actually measure what its names claim?" The failure takes shapes like these, and
the product looks fine through all of them:

1. Browser tests named for generating a tour stub ``/trips/preview``, so none of
   them generates one.
2. A test of the workbench's default voice reads the dropdown builder while the
   request it sends carries ``provider: 'mock'``.
3. A test asserts a hole as if it were a property, so closing the hole breaks
   the test.
4. A shard reports thousands of passes while ``pyproject.toml``'s ``--ignore``
   list excludes the shards that measure tour OUTPUT.

One name for all four: **the thing named is not the thing measured.** A missing
test is visibly missing; a lying test occupies the slot, so nobody writes the
real one. That is why these live in ``tests/`` and run in ``make test`` rather
than in ``.claude/``: a rule only works if something executes it. These execute.

No regex: function bodies come from ``ast``, and every check asserts it FOUND
its subject before asserting anything about it, so a broken derivation fails
loudly instead of passing vacuously.
"""

from __future__ import annotations

import ast
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]
BROWSER_SUITE = REPO / "tests" / "test_workbench_ui.py"

#: The endpoint that does the work behind "generate a tour" in the workbench.
TOUR_ENDPOINT = "/trips/preview"


def _all_functions(path: pathlib.Path) -> dict[str, str]:
    """EVERY function in ``path`` — tests, helpers and fixtures — to its source."""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            segment = ast.get_source_segment(src, node)
            if segment:
                out[node.name] = segment
    return out


def _names_referenced(source: str) -> set[str]:
    """Every identifier the snippet mentions, including fixture parameter names.

    Parameters matter because pytest injects fixtures by name: a test that takes
    ``stubbed_preview`` never *calls* it, but is stubbed by it all the same.
    """
    tree = ast.parse(source)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            names |= {a.arg for a in node.args.args}
    return names


def _reachable_source(name: str, functions: dict[str, str]) -> str:
    """A function's source PLUS every same-module function it can reach.

    Without this the derivation is fooled by one move: lift the stub into a
    helper and the test's own body is clean. Measured — the first version of this
    guard passed while three tests were stubbed through ``_route_tour_preview``,
    which is the very failure this file was written about. Following the call is
    the difference between a guard and a decoration.
    """
    seen: set[str] = set()
    pending = [name]
    chunks: list[str] = []
    while pending:
        current = pending.pop()
        if current in seen or current not in functions:
            continue
        seen.add(current)
        body = functions[current]
        chunks.append(body)
        pending.extend(n for n in _names_referenced(body) if n in functions and n not in seen)
    return "\n".join(chunks)


def _test_functions(path: pathlib.Path) -> dict[str, str]:
    """Every ``test_*`` function mapped to its source AND its helpers' source."""
    functions = _all_functions(path)
    return {
        name: _reachable_source(name, functions)
        for name in functions
        if name.startswith("test")
    }


def _stubs_the_endpoint(body: str) -> bool:
    """Does this test install a network stub over the tour endpoint?

    Playwright interception is ``<page>.route(<pattern>, handler)``. A test that
    routes the endpoint is answering its own request, so whatever it proves, it
    is not that the endpoint works.
    """
    for piece in body.split(".route(")[1:]:
        head = piece[:120]
        if TOUR_ENDPOINT in head:
            return True
    return False


def test_at_least_one_browser_test_generates_a_tour_unstubbed() -> None:
    """The workbench suite must exercise REAL tour generation at least once.

    This is the guard for the defect that started this file. The browser suite
    had fourteen tour tests and all fourteen answered their own request with a
    canned Notre-Dame payload, so ``make test`` was fully green while the
    workbench could not produce a tour in the UI at all.

    Rendering tests are legitimate and most of those fourteen should stay — you
    do want to assert the basic-lane banner renders without paying for a tour.
    What is not legitimate is having ONLY those. This requires one test that
    drives the real endpoint and lets it really answer.

    Deliberately a POSITIVE requirement: it cannot be satisfied by renaming a
    test or deleting a pattern, only by actually exercising the path.

    UNDO TEST: stub ``/trips/preview`` in the unstubbed test -> RED.
    """
    assert BROWSER_SUITE.is_file(), f"{BROWSER_SUITE} is missing — this guard measures nothing"
    tests = _test_functions(BROWSER_SUITE)
    assert tests, "no test functions parsed out of the browser suite; the derivation is broken"

    touching = {name: body for name, body in tests.items() if TOUR_ENDPOINT in body}
    assert touching, (
        f"no test in {BROWSER_SUITE.name} references {TOUR_ENDPOINT} at all. Either the "
        f"endpoint was renamed (update TOUR_ENDPOINT) or the workbench's tour path lost "
        f"its browser coverage entirely."
    )

    unstubbed = sorted(n for n, body in touching.items() if not _stubs_the_endpoint(body))
    stubbed = sorted(n for n, body in touching.items() if _stubs_the_endpoint(body))

    assert unstubbed, (
        f"all {len(stubbed)} browser tests that touch {TOUR_ENDPOINT} STUB it, so not one of "
        f"them proves the workbench can generate a tour — they prove it can render a canned "
        f"payload. This is exactly how `make test` stayed green while the UI could not build "
        f"a tour.\n\nStubbed: {stubbed}\n\n"
        f"Fix by adding ONE test that drives the real endpoint (seed a corpus the engine can "
        f"actually route — tier >= 3 POIs with >= 3 active beats each) and asserts real stops "
        f"render. Do not fix this by deleting the stubbed tests; they cover rendering."
    )


def test_the_endpoint_guard_can_tell_stubbed_from_unstubbed() -> None:
    """The detector above must actually detect. Proves it both ways.

    Without this, ``_stubs_the_endpoint`` could return False for everything and
    the guard would pass while measuring nothing — the same vacuous-pass failure
    this file exists to catch. So: a body that routes the endpoint must read as
    stubbed, and one that only requests it must not.
    """
    stubbed_body = 'page.route("**/trips/preview", lambda route: route.fulfill(status=200))'
    plain_body = 'with page.expect_response(lambda r: "/trips/preview" in r.url): btn.click()'

    assert _stubs_the_endpoint(stubbed_body), "detector missed a real stub — it proves nothing"
    assert not _stubs_the_endpoint(plain_body), "detector called a plain request a stub"


