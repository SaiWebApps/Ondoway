"""The quarantine directory: what the rebuild retires, and where it now lives.

Slice 0 deletes nothing. Everything the ingestion rebuild retires is MOVED under a
top-level ``_to_be_deleted/`` directory keeping its relative path, so any of it is
restorable with one ``git mv _to_be_deleted/<path> <path>`` until the slice that
removes the directory outright.

This pins the layout from both ends: each retired group is present under the
quarantine at its original relative path, AND absent from that path in the live
tree. A copy that left the original in place, or a move that landed somewhere
else, is a failure here rather than a silence — the live tree is what `make lint`,
`available_cities()` and pytest collection all read.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
QUARANTINE = REPO_ROOT / "_to_be_deleted"

#: The Lane B re-author pipeline, by the directory it lived in, the glob that names
#: its files there, and how many of them there are: its scripts, their tests, and
#: the review page they were served behind. The count makes a partial move fail.
RETIRED_GROUPS: tuple[tuple[str, str, int], ...] = (
    ("scripts", "reauthor_*.py", 8),
    ("tests", "test_reauthor_*.py", 8),
    ("frontend", "rewrites.html", 1),
)

#: The London corpus — an onboarding proof, never a launch city. Named by the files
#: that make a directory a corpus, so an emptied-out directory still fails.
RETIRED_CITY_DIR = ("data", "london")
RETIRED_CITY_FILES = (
    "poi-raw.json",
    "beats.json",
    "areas.json",
    "within_edges.json",
    "book-log.json",
)


def test_retired_paths_live_under_to_be_deleted() -> None:
    """Every retired path sits under ``_to_be_deleted/`` — and nowhere else."""
    problems: list[str] = []

    for parent, pattern, expected in RETIRED_GROUPS:
        moved = sorted(p.name for p in (QUARANTINE / parent).glob(pattern))
        if len(moved) != expected:
            problems.append(
                f"_to_be_deleted/{parent}/{pattern}: expected {expected} file(s), "
                f"found {len(moved)} ({moved})"
            )
        left_behind = [name for name in moved if (REPO_ROOT / parent / name).exists()]
        still_live = sorted(p.name for p in (REPO_ROOT / parent).glob(pattern))
        if left_behind or still_live:
            problems.append(
                f"{parent}/{pattern} still resolves in the live tree: "
                f"{sorted(set(left_behind) | set(still_live))}"
            )

    parent, city = RETIRED_CITY_DIR
    quarantined_city = QUARANTINE / parent / city
    missing = [f for f in RETIRED_CITY_FILES if not (quarantined_city / f).is_file()]
    if missing:
        problems.append(f"_to_be_deleted/{parent}/{city}/ is missing {missing}")
    if (REPO_ROOT / parent / city).exists():
        problems.append(f"{parent}/{city}/ still exists in the live tree")

    assert not problems, "the quarantine move is incomplete:\n  " + "\n  ".join(problems)


def test_quarantined_model_output_is_gitignored() -> None:
    """The quarantine's data/ subtree is machine-local paid-model output.

    ``_to_be_deleted/data/`` (literal, not a glob) must be gitignored so a future
    ``git add -A`` in this directory never sweeps up regenerable model output — but the
    rule must not swallow the quarantined *scripts*, which are tracked source moved here
    by ``git mv`` and must stay restorable.
    """
    ignored = subprocess.run(
        ["git", "check-ignore", "-v", "_to_be_deleted/data/paris/claims.json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert ignored.returncode == 0, (
        "_to_be_deleted/data/paris/claims.json should be gitignored: "
        f"stdout={ignored.stdout!r} stderr={ignored.stderr!r}"
    )
    assert "_to_be_deleted/data/" in ignored.stdout, (
        f"expected the literal `_to_be_deleted/data/` rule to be cited, got: {ignored.stdout!r}"
    )

    not_ignored = subprocess.run(
        ["git", "check-ignore", "_to_be_deleted/scripts/reauthor_run.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert not_ignored.returncode == 1, (
        "_to_be_deleted/scripts/reauthor_run.py must NOT be gitignored (it is tracked, "
        f"moved source): {not_ignored.stdout!r}"
    )

    tracked_london = subprocess.run(
        ["git", "ls-files", "_to_be_deleted/data/london"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    tracked_count = len([line for line in tracked_london.stdout.splitlines() if line])
    assert tracked_count == 150, (
        f"expected 150 tracked files under _to_be_deleted/data/london, found {tracked_count}"
    )


def test_the_quarantine_names_its_restore_and_its_deletion_slice() -> None:
    """``_to_be_deleted/README.md`` is the label on the box, not just the box.

    Anyone who lands in this directory — a future session, a reviewer, the owner
    doing the slice-11 cleanup — needs to know what is here, why it was quarantined
    rather than deleted outright, how to put any one piece back, and when the whole
    directory is finally allowed to go. AC-18 names four groups, the ADR reason, the
    one-command restore, the orphans.json force-tracking wrinkle, and the deletion
    slice; each is checked by a token only that group carries (`scripts/reauthor`,
    `test_reauthor`, `rewrites.html`, `data/london`), so a README that drops any one
    group fails here rather than reading as done.
    """
    readme = QUARANTINE / "README.md"
    assert readme.is_file(), f"{readme} does not exist"
    text = readme.read_text()

    # The four retired groups.
    assert "scripts/reauthor" in text, "README does not name the reauthor scripts group"
    assert "test_reauthor" in text, "README does not name the reauthor tests group"
    assert "rewrites.html" in text, "README does not name frontend/rewrites.html"
    assert "data/london" in text, "README does not name data/london/"
    assert "orphans.json" in text, "README does not name orphans.json"

    # The ADR-0001 reason.
    assert "ADR-0001" in text, "README does not cite ADR-0001 as the reason"

    # The one-command restore.
    assert "git mv _to_be_deleted/" in text, (
        "README does not give the `git mv _to_be_deleted/<path> <path>` restore command"
    )

    # orphans.json is force-tracked, and its beats leave beats.json only at slice 10.
    assert "force" in text.lower(), (
        "README does not say orphans.json is force-tracked (git add -f)"
    )
    assert "slice 10" in text.lower(), (
        "README does not name slice 10 as when orphans' beats leave beats.json"
    )

    # Slice 11 deletes the directory outright.
    assert "slice 11" in text.lower(), (
        "README does not name slice 11 as the slice that deletes this directory"
    )


def test_orphans_json_mirrors_the_chunkless_beats() -> None:
    """``orphans.json`` holds exactly the chunkless beats, unchanged, from beats.json.

    AC-8/AC-9: the 137 Paris beats whose ``source_chunk_slug`` is ``'legacy_ambiguous'``
    (no real source chunk survives for them) live in ``orphans.json`` under the
    quarantine — force-tracked despite ``_to_be_deleted/data/`` being gitignored — with
    the same ids and the same records. ``beats.json`` itself is untouched: those 137
    beats stay live there for parity with 7687/Aura until slice 10's graph swap.
    """
    beats_path = REPO_ROOT / "data" / "paris" / "beats.json"
    orphans_path = QUARANTINE / "data" / "paris" / "orphans.json"

    beats = json.loads(beats_path.read_text())
    expected = [b for b in beats if b.get("source_chunk_slug") == "legacy_ambiguous"]
    assert len(expected) == 137, (
        f"expected 137 chunkless beats in data/paris/beats.json, found {len(expected)}"
    )

    assert orphans_path.is_file(), f"{orphans_path} does not exist"
    orphans = json.loads(orphans_path.read_text())
    assert orphans == expected, "orphans.json does not exactly mirror the chunkless beats"

    tracked = subprocess.run(
        ["git", "ls-files", "_to_be_deleted/data/paris/orphans.json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert tracked.stdout.strip() == "_to_be_deleted/data/paris/orphans.json", (
        "orphans.json must be force-tracked despite _to_be_deleted/data/ being gitignored"
    )

    status = subprocess.run(
        ["git", "status", "--porcelain", "data/paris/beats.json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert status.stdout == "", f"data/paris/beats.json must be unchanged: {status.stdout!r}"
