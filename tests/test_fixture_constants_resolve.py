"""Every path constant that names a fixture must name a file that is there.

WHY THIS FILE EXISTS. On 2026-09-02 the `specs/` tree was deleted, and nine files
the product OPENS at runtime were living inside it. Five module-level constants
pointed at six of them; the other three are named inside a JSON manifest and
reached through it, which is its own trap and gets its own test at the bottom.
Nothing in the suite would have noticed:

  * `tests/test_tour_text_candidate_runner.py` and
    `tests/test_tour_batch_candidate_runner.py` only IMPORT their module.
    `Path(...) / "x.json"` never touches the disk, and the reads sit inside
    functions those tests do not call.
  * `scripts/coverage_calibrate.py` and `scripts/faithfulness_calibrate.py` are
    imported by no test at all.
  * `make lint` cannot help: the ruff path list in `Makefile` names
    `tour_batch_candidate.py` but not `tour_text_candidate.py`,
    `coverage_calibrate.py` or `faithfulness_calibrate.py`.

So the whole suite would have gone green over a deleted folder, and the failure
would have surfaced weeks later as a `FileNotFoundError` in a paid batch run,
against a file no longer present in any reachable commit.

A constant is only as true as the file under it. This asserts the file, by
reading the constant the product reads — never a path retyped here, which would
pass while the product's own copy pointed somewhere else.
"""

from __future__ import annotations

from pathlib import Path


def test_the_frozen_route_replay_inputs_are_where_the_runner_looks():
    """`scripts/tour_text_candidate.py` reads both of these inside `_load_route_and_source`."""
    from scripts.tour_text_candidate import SPEC

    assert (SPEC / "live-route-summary.json").is_file(), f"missing under {SPEC}"
    assert (SPEC / "live-tour-request.json").is_file(), f"missing under {SPEC}"


def test_the_frozen_batch_manifest_is_where_the_runner_looks():
    """`load_frozen_tour_batch(MANIFEST_PATH)` opens this on every batch plan."""
    from scripts.tour_batch_candidate import MANIFEST_PATH

    assert MANIFEST_PATH.is_file(), f"missing: {MANIFEST_PATH}"


def test_the_gold_standard_passage_is_where_the_scorer_looks():
    """`extract_gold_text()` reads this, and `make score-gold-text` calls it."""
    from scripts.score_gold_text import STANDARD, extract_gold_text

    assert STANDARD.is_file(), f"missing: {STANDARD}"
    # Not just present — still carries the North Star heading the extractor looks for.
    # A moved file that no longer carries the heading is the same outage with a
    # friendlier traceback.
    assert extract_gold_text().strip(), "the North Star blockquote came back empty"


def test_both_judge_calibration_sets_are_where_the_bake_offs_look():
    """The two prompt bake-offs are the only way to re-tune the judges."""
    from scripts.coverage_calibrate import _SET as COVERAGE_SET
    from scripts.faithfulness_calibrate import _SET as FAITHFULNESS_SET

    assert COVERAGE_SET.is_file(), f"missing: {COVERAGE_SET}"
    assert FAITHFULNESS_SET.is_file(), f"missing: {FAITHFULNESS_SET}"


def test_every_document_the_frozen_manifests_open_is_on_disk_and_unchanged():
    """The gap that made 33 tests go red, pinned so it cannot reopen.

    These paths do not appear in any source file. They sit inside
    `investigation-reference-manifest.json` as DATA, and the quality checker
    reads them through it. Two independent searches for "what opens a file under
    specs/" therefore reported all-clear, `specs/` was deleted, and the whole
    certification module failed on a missing file.

    A search over code cannot find a path that lives in a data file. This walks
    the manifest the way the product does, and checks the bytes as well as the
    presence — the manifests are sealed by hash, so a document that is present
    but altered is the same outage with a friendlier message.
    """
    import hashlib
    import json

    from src.tour.quality_certification import reference_document_path

    root = Path(__file__).resolve().parents[1]
    certification = root / "fixtures" / "tour-certification"
    calibration = json.loads((certification / "calibration-manifest.json").read_text())
    references = json.loads(
        (certification / "investigation-reference-manifest.json").read_text()
    )
    documents = {doc["id"]: doc for doc in references["documents"]}

    opened = 0
    for anchor in calibration["anchors"]:
        if anchor["expected"]["ENJOY"] not in {"PASS", "FAIL"}:
            continue
        document = documents[anchor["reference_document_id"]]
        path = reference_document_path(root, document["path"])
        assert path.is_file(), f"the manifest names {document['path']}, which is not at {path}"
        got = hashlib.sha256(path.read_bytes()).hexdigest()
        assert got == document["sha256"], f"{path} no longer matches its sealed hash"
        opened += 1

    assert opened, "the manifest yielded no anchors to check — the walk found nothing"


def test_no_fixture_constant_points_back_into_the_deleted_specs_tree():
    """The regression, stated as itself: `specs/` is gone and stays gone.

    Checked on the constants rather than by scanning source text, so a new
    constant added later is covered the moment it is listed here — and so this
    assertion cannot be satisfied by a comment that merely stops saying `specs`.
    """
    from scripts.coverage_calibrate import _SET as COVERAGE_SET
    from scripts.faithfulness_calibrate import _SET as FAITHFULNESS_SET
    from scripts.score_gold_text import STANDARD
    from scripts.tour_batch_candidate import MANIFEST_PATH
    from scripts.tour_text_candidate import SPEC

    for constant in (SPEC, MANIFEST_PATH, STANDARD, COVERAGE_SET, FAITHFULNESS_SET):
        assert "specs" not in Path(constant).parts, f"{constant} still points into specs/"
