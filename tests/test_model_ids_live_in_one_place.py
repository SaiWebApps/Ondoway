"""Every model id is spelled once, in the model registry's settings file.

A caller names the job it does and reads the model from ``src.model_registry``; a
model id written as a string anywhere else in ``src/`` or ``scripts/`` is a second
copy of a setting, and moving a model family then needs an edit per copy.

No regex: the scan reads every tracked Python file with ``ast`` and compares each
string constant to the registry's ids for equality. Every check proves it found
something before it asserts that something is absent.
"""

from __future__ import annotations

import ast
import json
import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[1]
REGISTRY_MODULE = "src/model_registry.py"
REGISTRY_FILE = REPO / "src" / "models.json"


def _registry_model_ids() -> frozenset[str]:
    return frozenset(json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))["models"])


def _scanned_files() -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", "--", "src", "scripts"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    return sorted(path for path in listed if path.endswith(".py") and path != REGISTRY_MODULE)


def _model_id_literals(source: str, model_ids: frozenset[str]) -> list[tuple[int, str]]:
    """(line, id) for every string constant in ``source`` that is a registered id."""
    return [
        (node.lineno, node.value)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value in model_ids
    ]


def test_the_scan_sees_the_registry_and_the_code() -> None:
    """The guard below is only as good as what it reads: the registry must list
    models, the scan must cover the code, and the detector must flag a literal id."""
    model_ids = _registry_model_ids()
    assert model_ids, "the registry lists no model ids"
    files = _scanned_files()
    assert len(files) > 50, f"the scan found only {len(files)} Python files under src/ and scripts/"
    sample_id = sorted(model_ids)[0]
    sample = f"MODEL = {sample_id!r}\n"
    assert _model_id_literals(sample, model_ids) == [(1, sample_id)]


def test_no_model_id_is_written_outside_the_registry() -> None:
    model_ids = _registry_model_ids()
    offenders = [
        f"{path}:{line} {model_id!r}"
        for path in _scanned_files()
        for line, model_id in _model_id_literals(
            (REPO / path).read_text(encoding="utf-8"), model_ids
        )
    ]
    assert not offenders, (
        "model ids written outside the registry; name the job with "
        "src.model_registry.model_for(role) instead:\n" + "\n".join(offenders)
    )
