#!/usr/bin/env python
"""Atomic commit helper for `beats.json` + `book-log.json`.

All end-of-extraction and `/beat-wipe` writes go through `commit()`. The
function stages both files, runs `validate_beats.py` against the staged
beats, and only performs the two atomic renames when validation passes.

On validator fail, staging files are removed and both target paths
remain byte-identical to their pre-call state — that is the rollback
guarantee proved by `tests/test_atomic_commit.py::test_rollback_byte_identical`.

No CLI surface, no test-only flags (BP-10). Production callers import
this helper; the rollback proof lives in pytest.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


class BeatValidationError(Exception):
    """Raised by `commit()` when the staged beats fail validation.

    When this exception is raised, both target files are guaranteed
    byte-identical to their pre-call state — no disk write reached
    either target path.
    """


_VALIDATOR = Path(__file__).resolve().parent / "validate_beats.py"


def _staging_path(target: Path) -> Path:
    return target.with_name(target.name + ".staging")


def _write_json(obj: Any, path: Path) -> None:
    with path.open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def commit(
    final_beats: list[dict],
    final_log: dict,
    *,
    beats_path: os.PathLike | str,
    log_path: os.PathLike | str,
    chunks_root: os.PathLike | str | None = None,
) -> None:
    """Atomically write `final_beats` and `final_log` to their targets.

    Callers compute the full final state of both files. This helper is
    a thin atomicity-plus-validation layer with no business logic.

    `chunks_root` is handed to the validator as `--chunks-root` so a
    new-shape file written under a data root other than the repo's
    `data/` (the ingest runner's per-job root) still grounds every span;
    left `None`, the validator derives it from the path as before.

    Steps:
      1. Serialize `final_beats` to `{beats_path}.staging`.
      2. Run `validate_beats.py` on the staged beats.
      3. If validator exits non-zero: delete staging, raise
         `BeatValidationError` with the validator report. Both targets
         untouched.
      4. Serialize `final_log` to `{log_path}.staging`.
      5. `os.replace` both staging files onto their targets (beats
         first, log second). Each replace is atomic on the same
         filesystem.
    """
    beats_target = Path(beats_path)
    log_target = Path(log_path)
    beats_staging = _staging_path(beats_target)
    log_staging = _staging_path(log_target)

    try:
        _write_json(final_beats, beats_staging)
    except OSError:
        _safe_unlink(beats_staging)
        raise

    try:
        cmd = [sys.executable, str(_VALIDATOR), str(beats_staging)]
        if chunks_root is not None:
            cmd += ["--chunks-root", str(chunks_root)]
        result = subprocess.run(cmd, capture_output=True, text=True)
    except Exception:
        _safe_unlink(beats_staging)
        raise

    if result.returncode != 0:
        report = (result.stdout or "") + (result.stderr or "")
        _safe_unlink(beats_staging)
        raise BeatValidationError(
            f"validate_beats rejected staged write (exit {result.returncode}):\n{report.rstrip()}"
        )

    try:
        _write_json(final_log, log_staging)
    except OSError:
        _safe_unlink(beats_staging)
        _safe_unlink(log_staging)
        raise

    try:
        os.replace(beats_staging, beats_target)
        os.replace(log_staging, log_target)
    except OSError:
        # Replace can fail across filesystems; surface clearly.
        _safe_unlink(beats_staging)
        _safe_unlink(log_staging)
        raise
