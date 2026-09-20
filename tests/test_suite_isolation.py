"""No two test workers ever share a graph (SI-S1).

The pure track runs eight workers and the database track three, and worker
numbers fold onto three graphs. A test that opens a driver without carrying
the database tag runs in the pure track and wipes a graph the database track
is seeding: the audit then fails with EntityNotFound and vanished users that
no isolated run reproduces. The tag comes from the auto-tagger's fixture-name
set, so the whole class is closed by one rule: every fixture that opens a
driver is in that set, checked mechanically here.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent


def _tagged_fixture_names() -> frozenset[str]:
    import tests.conftest as conftest

    return conftest._DB_FIXTURES


def _driver_opening_fixtures(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        is_fixture = any("fixture" in ast.dump(d) for d in node.decorator_list)
        if not is_fixture:
            continue
        opens = any(
            isinstance(n, ast.Call)
            and getattr(n.func, "id", getattr(n.func, "attr", "")) == "create_driver"
            for n in ast.walk(node)
        )
        if opens:
            found.append(node.name)
    return found


def test_every_driver_opening_fixture_is_in_the_auto_tag_set():
    tagged = _tagged_fixture_names()
    escapees: list[str] = []
    for path in sorted(TESTS.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        for name in _driver_opening_fixtures(path):
            if name not in tagged:
                escapees.append(f"{path.name}: {name}")
    assert not escapees, (
        "these fixtures open a database driver but are not in conftest's "
        "_DB_FIXTURES, so their tests run in the pure track and wipe a graph "
        "the database track owns:\n  " + "\n  ".join(escapees)
    )


def test_the_known_seeded_driver_files_are_tagged():
    """The three files that escaped in the 2026-09-19 audit stay tagged."""
    tagged = _tagged_fixture_names()
    assert "seeded_driver" in tagged
