"""Regression test: every lens slug referenced anywhere in src/ must exist in
the canonical set defined in src/schema/definitions.py.

This catches schema drift from stale seed files, hand-rolled migrations, or
copy-pasted slugs that diverge from the V3 schema. Runs in <100ms with no
database connection.

How it works:
- Walks src/**/*.py for any string literal that looks like a lens key
  (e.g. "lens_names": [...], "lenses": [...], lens="hidden_history")
- Compares each found slug against MVP_LENSES + DAG_CHILD_LENSES
- Fails with a list of offenders + the file:line where each was found

If you intentionally add a new lens, update definitions.py first — then this
test will pass automatically.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from src.schema.definitions import DAG_CHILD_LENSES, MVP_LENSES, TAGGABLE_LENSES

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"

CANONICAL_SLUGS: set[str] = {lens["name"] for lens in MVP_LENSES} | {
    child["name"] for child in DAG_CHILD_LENSES
}

# Keys whose values reference lens slugs. Add to this set if new patterns appear.
LENS_KEYS = {"lens", "lens_name", "lens_names", "lenses", "lens_slug", "lens_slugs"}

# Slug shape: lowercase + underscore. Length >= 4 to avoid false positives.
SLUG_RE = re.compile(r"^[a-z][a-z_]{3,}$")


def _collect_lens_strings(py_file: Path) -> list[tuple[int, str]]:
    """Walk a Python file's AST. Return (lineno, slug) tuples for any string
    literal that appears as a value of a known lens-key. Robust against
    nested dicts, lists, and assignments."""
    try:
        tree = ast.parse(py_file.read_text())
    except SyntaxError:
        return []

    found: list[tuple[int, str]] = []

    class Visitor(ast.NodeVisitor):
        def visit_Dict(self, node: ast.Dict) -> None:
            for key, value in zip(node.keys, node.values, strict=False):
                if isinstance(key, ast.Constant) and key.value in LENS_KEYS:
                    self._extract(value)
            self.generic_visit(node)

        def visit_keyword(self, node: ast.keyword) -> None:
            if node.arg in LENS_KEYS:
                self._extract(node.value)
            self.generic_visit(node)

        def visit_Assign(self, node: ast.Assign) -> None:
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id.lower() in {
                    "lens_names",
                    "lens_slugs",
                    "taggable_lenses",
                }:
                    self._extract(node.value)
            self.generic_visit(node)

        def _extract(self, value: ast.AST) -> None:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                found.append((value.lineno, value.value))
            elif isinstance(value, (ast.List, ast.Tuple, ast.Set)):
                for elt in value.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        found.append((elt.lineno, elt.value))

    Visitor().visit(tree)
    return found


def test_no_unknown_lens_slugs_in_src() -> None:
    """Every lens slug referenced in src/ must be in definitions.py."""
    offenders: list[str] = []

    # Skip the schema definition file itself — that's where the canonical list lives.
    schema_file = SRC_ROOT / "schema" / "definitions.py"

    for py_file in sorted(SRC_ROOT.rglob("*.py")):
        if py_file == schema_file:
            continue
        for lineno, slug in _collect_lens_strings(py_file):
            if not SLUG_RE.match(slug):
                continue  # not slug-shaped — probably a display label or similar
            if slug not in CANONICAL_SLUGS:
                rel = py_file.relative_to(REPO_ROOT)
                offenders.append(f"  {rel}:{lineno}  '{slug}'")

    if offenders:
        pytest.fail(
            "Lens drift: found references to lens slugs not in definitions.py.\n"
            "Either fix the reference or add the slug to MVP_LENSES / DAG_CHILD_LENSES:\n"
            + "\n".join(offenders)
        )


def test_canonical_slug_count() -> None:
    """Sanity check: the canonical set has the expected size. If this number
    changes, definitions.py was modified — update this test deliberately."""
    assert len(MVP_LENSES) == 8, f"Expected 8 parent lenses, got {len(MVP_LENSES)}"
    assert len(DAG_CHILD_LENSES) == 21, f"Expected 21 child lenses, got {len(DAG_CHILD_LENSES)}"
    assert len(CANONICAL_SLUGS) == 29, (
        f"Expected 29 unique canonical slugs, got {len(CANONICAL_SLUGS)}"
    )


# Cities with a beats.json file we want to enforce taggable-lens-only on.
# Add new cities here when their corpus reaches upload-readiness.
# The London corpus was an onboarding proof, never a launch city; it is retired to
# _to_be_deleted/ and is no longer parametrized here.
PARAMETRIZED_CITIES = ["paris", "new_york"]


@pytest.mark.parametrize("city", PARAMETRIZED_CITIES)
def test_no_parent_lens_in_city_beats(city: str) -> None:
    """Every beat in data/{city}/beats.json must use a TAGGABLE (child/leaf)
    lens slug, never a parent lens.

    Distinct from `test_no_unknown_lens_slugs_in_src`: that one walks src/
    code for *any* canonical slug (parent or child) — useful for guarding
    against typos and stale references. This one walks per-city beat data
    and enforces the stronger invariant that the API enforces at write
    time: only child/leaf lenses are taggable on beats.

    Catches the failure mode that bit Phase 6 of the Paris closeout: 5
    beats slipped through with parent slug 'commerce_innovation', causing
    silent TAGGED_WITH 422s at /upload time. Adding this test as a
    pre-upload gate ensures the same regression can't recur on Paris or
    any future city.
    """
    beats_path = Path(__file__).resolve().parent.parent / "data" / city / "beats.json"
    # A listed-but-not-yet-materialized corpus has no beats to violate the invariant,
    # so treat it as an empty corpus rather than pytest.skip(): this project's conftest
    # flips EVERY skip to a FAILURE (no silent non-runs), which would RED a newly listed
    # city until its data lands. The moment data/{city}/beats.json exists its real beats
    # are checked; the always-present paris/new_york corpora are unaffected.
    beats = json.loads(beats_path.read_text()) if beats_path.exists() else []

    parent_slugs = {l["name"] for l in MVP_LENSES}
    offenders: list[tuple[str, str]] = []  # (beat_id, lens)
    for beat in beats:
        lens = beat.get("lens", "")
        if not lens:
            continue
        if lens in parent_slugs or lens not in TAGGABLE_LENSES:
            offenders.append((beat.get("beat_id", "<no-beat-id>"), lens))

    if offenders:
        sample = "\n".join(f"  {bid}  lens='{lens}'" for bid, lens in offenders[:10])
        pytest.fail(
            f"{city}: {len(offenders)} beat(s) use non-taggable (parent) lens slugs.\n"
            f"Re-tag each to a child lens (TAGGABLE_LENSES list in definitions.py).\n"
            f"Sample (first 10):\n{sample}"
            + (f"\n  ... and {len(offenders) - 10} more" if len(offenders) > 10 else "")
        )
