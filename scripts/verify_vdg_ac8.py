#!/usr/bin/env python
"""Live duplicate-beat assertion for the Val-de-Grace cleanup.

Computes exact pairwise Jaccard over 5-gram word shingles on every
Val-de-Grace beat in `data/paris/beats.json`. Asserts that no pair scores
Jaccard >= 0.8 unless both beats carry `dedup_reviewed: true` AND the pair
was the subject of an action == "KEEP_BOTH" entry in
`data/paris/_dedup_review/_log.jsonl`.

Exits 0 on pass, 1 on any violation, 2 on file I/O problems.
"""

from __future__ import annotations

import json
import sys
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.dedup_pairs import shingle_set  # noqa: E402

BEATS_PATH = Path("data/paris/beats.json")
LOG_PATH = Path("data/paris/_dedup_review/_log.jsonl")
POI_MATCH = "grace"  # case-insensitive substring of poi_name ("Val-de-Grace")
AC8_THRESHOLD = 0.8


def _load_beats() -> list[dict]:
    with BEATS_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_keep_both_pairs() -> set[frozenset[str]]:
    if not LOG_PATH.exists():
        return set()
    pairs: set[frozenset[str]] = set()
    with LOG_PATH.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            entry = json.loads(line)
            if entry.get("action") == "KEEP_BOTH":
                pair = entry.get("pair") or []
                if len(pair) == 2:
                    pairs.add(frozenset(pair))
    return pairs


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    u = len(a | b)
    return len(a & b) / u if u else 0.0


def main() -> int:
    try:
        beats = _load_beats()
    except (OSError, json.JSONDecodeError) as exc:
        print(f"verify_vdg_ac8: cannot read {BEATS_PATH}: {exc}", file=sys.stderr)
        return 2

    vdg = [b for b in beats if POI_MATCH in (b.get("poi_name", "") or "").lower()]
    if not vdg:
        print("verify_vdg_ac8: no VdG beats found — unexpected")
        return 1

    keep_both_pairs = _load_keep_both_pairs()
    beats_by_id = {b["beat_id"]: b for b in vdg}
    shingles = {b["beat_id"]: shingle_set(b.get("script_body", "")) for b in vdg}

    violations: list[str] = []
    for a_id, b_id in combinations(sorted(beats_by_id), 2):
        j = _jaccard(shingles[a_id], shingles[b_id])
        if j < AC8_THRESHOLD:
            continue
        pair_key = frozenset((a_id, b_id))
        both_reviewed = bool(
            beats_by_id[a_id].get("dedup_reviewed") and beats_by_id[b_id].get("dedup_reviewed")
        )
        keep_both = pair_key in keep_both_pairs
        if both_reviewed and keep_both:
            continue
        violations.append(
            f"AC8_VIOLATION jaccard={j:.3f} A={a_id} B={b_id} "
            f"both_reviewed={both_reviewed} keep_both_logged={keep_both}"
        )

    if violations:
        print(f"verify_vdg_ac8: FAIL ({len(violations)} violation(s))")
        for v in violations:
            print(f"  {v}")
        return 1

    print(f"verify_vdg_ac8: PASS ({len(vdg)} VdG beats, 0 violations)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
