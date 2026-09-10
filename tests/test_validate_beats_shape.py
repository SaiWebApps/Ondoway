"""Tests for scripts/validate_beats.py's shape dispatch + --chunks-root CLI flag.

specs/2026-09-09-ingest-slice-1, track B, step 8. AC-25, AC-26, AC-27 (verbatim
text in run-context.md): the CLI passes a new-shape file when given
--chunks-root, refuses a file that mixes new- and legacy-shape records
(SHAPE_MIXED) or that contains an indeterminate record (SHAPE_UNKNOWN), and
derives Books/{City} case-insensitively from the beats path when no flag is
given (erroring with a named remedy when no root can be found).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = REPO_ROOT / "scripts" / "validate_beats.py"
FIXTURE = REPO_ROOT / "fixtures" / "ingestion" / "guggenheim-example.json"
LEGACY_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "beats_multi_chunk.json"

LP_SOURCE = "lonely-planet-new-york-city"
LP_CHUNK = "chunk-07-upper-east-side"
FROMMERS_SOURCE = "frommers-nyc-2024"
FROMMERS_CHUNK = "chunk-05-ch05-uptown"


def _fixture_records() -> list[dict[str, Any]]:
    return json.loads(FIXTURE.read_text())


def _one_legacy_record() -> dict[str, Any]:
    record = json.loads(LEGACY_FIXTURE.read_text())[0]
    assert "claims" not in record
    assert "narration" not in record
    return record


def run_validator(*args: str, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


# ── AC-25: --chunks-root makes a new-shape file pass ──


def test_cli_new_shape_passes_with_chunks_root() -> None:
    result = run_validator(str(FIXTURE), "--chunks-root", "Books/new_york")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout


# ── AC-26: a mixed-shape file, and an indeterminate-shape file, are refused ──


def test_cli_mixed_or_unknown_shape_is_refused(tmp_path: Path) -> None:
    # One new-shape record (the fixture's first beat) + one real legacy
    # record in the same file -> SHAPE_MIXED.
    mixed = [_fixture_records()[0], _one_legacy_record()]
    mixed_path = tmp_path / "mixed.json"
    mixed_path.write_text(json.dumps(mixed))

    mixed_result = run_validator(str(mixed_path))

    assert mixed_result.returncode == 1, mixed_result.stdout + mixed_result.stderr
    assert "SHAPE_MIXED" in mixed_result.stdout

    # A record carrying nothing but a beat_id is neither new nor a real
    # legacy beat -> SHAPE_UNKNOWN, never a guessed SHAPE_LEGACY.
    unknown_path = tmp_path / "unknown.json"
    unknown_path.write_text(json.dumps([{"beat_id": "x"}]))

    unknown_result = run_validator(str(unknown_path))

    assert unknown_result.returncode == 1, unknown_result.stdout + unknown_result.stderr
    assert "SHAPE_UNKNOWN" in unknown_result.stdout


# ── AC-27: chunks root derivation from the beats path, case-insensitively ──


def test_cli_derives_books_root_from_beats_path(tmp_path: Path) -> None:
    beats_path = tmp_path / "data" / "new_york" / "beats.json"
    beats_path.parent.mkdir(parents=True)
    beats_path.write_text(json.dumps(_fixture_records()))

    # Mixed-case city dir name — derivation matches it case-insensitively,
    # the same way the legacy grounding gate's Books/{City} lookup does.
    books_city_dir = tmp_path / "Books" / "New_York"
    (books_city_dir / LP_SOURCE).mkdir(parents=True)
    (books_city_dir / LP_SOURCE / f"{LP_CHUNK}.txt").write_text(
        (REPO_ROOT / "Books" / "new_york" / LP_SOURCE / f"{LP_CHUNK}.txt").read_text()
    )
    (books_city_dir / FROMMERS_SOURCE).mkdir(parents=True)
    (books_city_dir / FROMMERS_SOURCE / f"{FROMMERS_CHUNK}.txt").write_text(
        (REPO_ROOT / "Books" / "new_york" / FROMMERS_SOURCE / f"{FROMMERS_CHUNK}.txt").read_text()
    )

    result = run_validator(str(beats_path))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout

    # A new-shape file with no derivable root and no --chunks-root flag is a
    # hard error naming the remedy, never a silent pass or soft-skip.
    orphan_path = tmp_path / "orphan-beats.json"
    orphan_path.write_text(json.dumps(_fixture_records()))

    orphan_result = run_validator(str(orphan_path))

    assert orphan_result.returncode == 2, orphan_result.stdout + orphan_result.stderr
    assert "--chunks-root" in (orphan_result.stdout + orphan_result.stderr)


# ── AC-28: the real corpus is still legacy-shaped and still passes ──


def test_cli_real_corpus_still_passes_as_legacy() -> None:
    for beats_path in (
        REPO_ROOT / "data" / "paris" / "beats.json",
        REPO_ROOT / "data" / "new_york" / "beats.json",
    ):
        result = run_validator(str(beats_path))

        assert result.returncode == 0, result.stdout + result.stderr
        assert "shape=legacy" in result.stdout, result.stdout
