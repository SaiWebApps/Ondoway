"""Tests for src/ingest/unit.py — specs/2026-09-10-ingest-slice-3, step 1.

Step 1 covers AC-1 (Unit.key / Unit.custom_id fit the Batch API's
`^[a-zA-Z0-9_-]{1,64}$` custom_id contract, and custom_id only accepts
attempts 1 and 2), AC-2 (load_unit reads a real chunk verbatim and raises
FileNotFoundError on a missing one), AC-3 (a bad rights_basis or as_of
raises pydantic ValidationError, via the probe-Source after-validator
delegating to model.Source's own rules rather than a private import), and
AC-4 (Unit is frozen and extra='forbid'; Unit.source() stamps a span with
the unit's own source_id/chunk/as_of/rights_basis). AC-55 (test_ingest_unit
is one of the five ingest test files with a self-contained
test_no_live_client_in_this_file).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.ingest import model
from src.ingest import unit as unit_mod

REPO_ROOT = Path(__file__).resolve().parent.parent
BOOKS_ROOT = REPO_ROOT / "Books" / "new_york"
LP_SOURCE = "lonely-planet-new-york-city"
LP_CHUNK = "chunk-07-upper-east-side"

CUSTOM_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _make_unit(**overrides: object) -> unit_mod.Unit:
    fields: dict[str, object] = {
        "city": "new_york",
        "source_id": LP_SOURCE,
        "chunk": LP_CHUNK,
        "text": "Some passage text.",
        "as_of": 2023,
        "rights_basis": "owned_copy",
    }
    fields.update(overrides)
    return unit_mod.Unit(**fields)


def test_unit_key_and_custom_ids_fit_the_batch_api():
    unit = _make_unit()
    expected_key = model.slug("lonely-planet-new-york-city-chunk-07-upper-east-side")
    assert unit.key == expected_key

    a1 = unit.custom_id(1)
    a2 = unit.custom_id(2)
    assert a1 == f"{expected_key}-a1"
    assert a2 == f"{expected_key}-a2"
    assert CUSTOM_ID_RE.fullmatch(a1)
    assert CUSTOM_ID_RE.fullmatch(a2)

    long_unit = _make_unit(source_id="x" * 70)
    long_custom_id = long_unit.custom_id(1)
    assert len(long_custom_id) <= 64
    assert long_custom_id == f"{long_unit.key[:60]}-a1"
    assert CUSTOM_ID_RE.fullmatch(long_custom_id)

    with pytest.raises(ValueError):
        unit.custom_id(3)
    with pytest.raises(ValueError):
        unit.custom_id(0)


def test_unit_loads_a_real_chunk_verbatim():
    unit = unit_mod.load_unit(
        BOOKS_ROOT,
        city="new_york",
        source_id=LP_SOURCE,
        chunk=LP_CHUNK,
        as_of=2023,
        rights_basis="owned_copy",
    )
    expected_path = BOOKS_ROOT / LP_SOURCE / f"{LP_CHUNK}.txt"
    assert unit.text == expected_path.read_text()
    assert unit.path == expected_path

    with pytest.raises(FileNotFoundError):
        unit_mod.load_unit(
            BOOKS_ROOT,
            city="new_york",
            source_id=LP_SOURCE,
            chunk="chunk-99-does-not-exist",
            as_of=2023,
            rights_basis="owned_copy",
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"rights_basis": "fair_use"},
        {"as_of": ""},
        {"as_of": None},
    ],
)
def test_unit_refuses_a_bad_rights_basis_or_as_of(overrides):
    with pytest.raises(ValidationError):
        _make_unit(**overrides)


def test_unit_is_frozen_and_builds_sources_with_its_own_fields():
    unit = _make_unit()

    with pytest.raises(ValidationError):
        unit.text = "changed"

    with pytest.raises(ValidationError):
        _make_unit(extra="x")

    source = unit.source("a span")
    assert isinstance(source, model.Source)
    assert source.source_id == unit.source_id
    assert source.chunk == unit.chunk
    assert source.as_of == unit.as_of
    assert source.rights_basis == unit.rights_basis
    assert source.span == "a span"
    assert source.stated_value is None


def test_no_live_client_in_this_file():
    """AC-55: this $0-spend test file never names a live LLM client or
    reads its API key. Its own body is exempt from the walk below — this
    docstring and the assert messages name those things on purpose to
    describe the rule, which is not the violation the rule guards against.
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    self_name = "test_no_live_client_in_this_file"
    forbidden_names = {"AnthropicClient", "anthropic"}
    forbidden_env_var = "ANTHROPIC_API_KEY"

    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == self_name:
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name):
                assert sub.id not in forbidden_names, f"{sub.id!r} must not appear in this file"
            elif isinstance(sub, ast.Attribute):
                assert sub.attr not in forbidden_names, (
                    f"{sub.attr!r} must not appear in this file"
                )
            elif isinstance(sub, ast.ImportFrom) and sub.module:
                assert sub.module.split(".")[0] not in forbidden_names, (
                    f"from-import of {sub.module!r} must not appear in this file"
                )
            elif isinstance(sub, ast.alias):
                top_level_name = sub.name.split(".")[0]
                assert top_level_name not in forbidden_names, (
                    f"import of {sub.name!r} must not appear in this file"
                )
                assert sub.asname not in forbidden_names, (
                    f"import alias {sub.asname!r} must not appear in this file"
                )
            elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                assert forbidden_env_var not in sub.value, (
                    "this file must never read ANTHROPIC_API_KEY"
                )
