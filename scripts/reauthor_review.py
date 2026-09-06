"""Record a human decision about a re-authored beat.

`reauthor_run.py` produces candidates; this is where a person accepts or refuses
them. Nothing here writes to the corpus — the decisions live alongside the
candidates in `data/{city}/reauthored.json`, and a later step carries the
approved ones into `beats.json` through `beats_io.commit`.

Two rules make a decision worth having:

**An approval is bound to the text it was given for.** It stores the hash of the
body approved, so editing the rewrite afterwards makes the approval stale rather
than silently carrying it onto prose nobody read. `validate_beats` already
enforces exactly this for `fact_check.verified_body_hash`; this is the same rule
for the same reason.

**A decision names its reviewer.** An unattributed verdict is not a record.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: The only verdicts a reviewer may record. A typo must not become a fourth state
#: that nothing downstream handles.
DECISIONS: tuple[str, ...] = ("approve", "reject")

_REPO_ROOT = Path(__file__).resolve().parent.parent


def body_hash(text: str) -> str:
    """SHA-256 of a body, used to bind an approval to the text it approved."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def reviewer_identity() -> str:
    """Who is deciding, from git — real from the first decision, per I7.

    Falls back to the OS user when git has no identity configured, so a decision
    is never recorded as nobody.
    """
    try:
        name = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=_REPO_ROOT,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        name = ""
    return name or os.getenv("USER") or "unknown"


def record_decision(
    records: list[dict], beat_id: str, decision: str, *, decided_by: str
) -> dict[str, Any]:
    """Attach a verdict to one candidate, bound to the body it was given for."""
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}, got {decision!r}")
    if not decided_by.strip():
        raise ValueError("a decision must name its reviewer")
    for row in records:
        if row.get("beat_id") == beat_id:
            row["decision"] = decision
            row["decided_by"] = decided_by.strip()
            row["decided_at"] = datetime.now(UTC).isoformat()
            row["decided_body_hash"] = body_hash(row.get("body_after") or "")
            return row
    raise KeyError(f"no candidate with beat_id {beat_id!r}")


def is_stale(row: dict) -> bool:
    """Whether a recorded decision no longer applies to the current body."""
    stamped = row.get("decided_body_hash")
    if not stamped:
        return False
    return stamped != body_hash(row.get("body_after") or "")


def review_order(records: list[dict]) -> list[dict]:
    """What still needs a person, most-doubted first; decided rows sink."""
    return sorted(
        records,
        key=lambda r: (
            bool(r.get("decision")),
            -r.get("flags", 0),
            -r.get("ratio_after", 0.0),
            r.get("beat_id", ""),
        ),
    )


def decision_summary(records: list[dict]) -> dict[str, int]:
    """How much of the queue is done, and whether any approval has gone stale."""
    return {
        "total": len(records),
        "approved": sum(1 for r in records if r.get("decision") == "approve"),
        "rejected": sum(1 for r in records if r.get("decision") == "reject"),
        "undecided": sum(1 for r in records if not r.get("decision")),
        "stale": sum(1 for r in records if is_stale(r)),
    }


def candidates_path(city_slug: str, *, data_dir: Path | None = None) -> Path:
    root = data_dir if data_dir is not None else _REPO_ROOT / "data"
    return root / city_slug / "reauthored.json"


def load_candidates(city_slug: str, *, data_dir: Path | None = None) -> list[dict]:
    """Read a city's candidates, or an empty list when the run has not been made."""
    path = candidates_path(city_slug, data_dir=data_dir)
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def save_candidates(
    city_slug: str, records: list[dict], *, data_dir: Path | None = None
) -> None:
    """Write candidates back atomically, so an interrupted save cannot truncate them.

    A staging file plus `os.replace` — the same shape `beats_io.commit` uses, kept
    separate because that helper validates beats and this file holds candidates.
    """
    path = candidates_path(city_slug, data_dir=data_dir)
    staging = path.with_suffix(path.suffix + ".staging")
    staging.write_text(
        json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    os.replace(staging, path)
