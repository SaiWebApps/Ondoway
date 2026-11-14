#!/usr/bin/env python
"""Orchestrate the semantic dedup pass: candidate-pair mining, Haiku judging,
markdown report writing, and interactive CLI apply with atomic commit.

Public entry points:
  * `main(argv)` — CLI. Runs the full workflow, or the report-only / apply-only
    sub-phases via subcommands.
  * `run_report(...)` — mine pairs + classify + write report. Returns the
    report path and the in-memory decision list. No `beats.json` mutation.
  * `apply_decisions(...)` — given a decision list (one per pair), mutate the
    in-memory beats, then call `beats_io.commit` for atomic write. Appends one
    line per applied decision to `_dedup_review/_log.jsonl`.

Report-only vs apply phases are decoupled so the interactive CLI loop (task 7)
and the pytest harness (task 6) can both drive the same apply logic.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import beats_io, dedup_pairs  # noqa: E402
from scripts.beat_dedup_judge import classify_pair  # noqa: E402

VALID_ACTIONS = ("SKIP", "INSERT", "COMBINE", "KEEP_BOTH")

_RECOMMENDATION_BY_CLASS = {
    "same_story_same_wording": "SKIP",
    "same_story_added_detail": "INSERT",
    "same_story_enhanced_content": "COMBINE",
    "different_story": "KEEP_BOTH",
}


def _timestamp(now: _dt.datetime | None = None) -> str:
    now = now or _dt.datetime.now(_dt.timezone.utc)
    return now.strftime("%Y-%m-%dT%H-%M-%SZ")


def _iso(now: _dt.datetime | None = None) -> str:
    now = now or _dt.datetime.now(_dt.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_hash(body: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", body.lower().strip()).encode("utf-8")).hexdigest()


def _load_beats(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_log(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _newer_beat(beat_a: dict, beat_b: dict) -> str:
    """Return beat_id of the beat with the newer `_meta.generated_at`.

    Missing / unparseable timestamps compare as empty string, so any beat with
    a timestamp beats one without. Tie → beat_b (the second-inserted)."""
    ta = (beat_a.get("_meta") or {}).get("generated_at", "")
    tb = (beat_b.get("_meta") or {}).get("generated_at", "")
    return beat_a["beat_id"] if ta > tb else beat_b["beat_id"]


def build_report(
    pairs_with_verdicts: list[dict],
    *,
    city: str,
    threshold: float,
    num_perm: int,
    shingle_size: int,
    beats_by_id: dict[str, dict],
    timestamp: str | None = None,
) -> str:
    ts = timestamp or _timestamp()
    lines: list[str] = []
    lines.append(f"# Beat Dedup Review — {city} — {ts}\n")
    lines.append(f"**Candidate pairs:** {len(pairs_with_verdicts)} (Jaccard ≥ {threshold})")
    lines.append(
        f"**Threshold:** {threshold} | **Shingles:** {shingle_size}-gram | "
        f"**Permutations:** {num_perm}\n"
    )
    lines.append("---\n")

    parse_failed_first = sorted(
        pairs_with_verdicts,
        key=lambda p: (not p["verdict"].get("_parse_failed", False), -p["jaccard"]),
    )

    for idx, pair in enumerate(parse_failed_first, start=1):
        a = beats_by_id[pair["beat_a"]]
        b = beats_by_id[pair["beat_b"]]
        v = pair["verdict"]
        lines.append(f"## Pair {idx} — Jaccard {pair['jaccard']:.2f}\n")
        lines.append(
            f"- **A:** `{pair['beat_a']}`\n"
            f"  (lens: `{a.get('lens', '')}`, {len(a.get('script_body', ''))} chars)"
        )
        lines.append(
            f"- **B:** `{pair['beat_b']}`\n"
            f"  (lens: `{b.get('lens', '')}`, {len(b.get('script_body', ''))} chars)\n"
        )
        classification = v.get("classification", "different_story")
        reasoning = v.get("reasoning", "")
        suffix = " _(parse_failed — spot-check)_" if v.get("_parse_failed") else ""
        lines.append(f"**Haiku classification:** `{classification}`{suffix}")
        lines.append(f"**Reasoning:** {reasoning}\n")
        rec = _RECOMMENDATION_BY_CLASS.get(classification, "KEEP_BOTH")
        lines.append(f"**Recommendation:** `{rec}`\n")
        lines.append(
            "Beat A excerpt: " + (a.get("script_body", "")[:240] or "_empty_") + "\n"
        )
        lines.append(
            "Beat B excerpt: " + (b.get("script_body", "")[:240] or "_empty_") + "\n"
        )
        lines.append("---\n")

    return "\n".join(lines) + "\n"


def run_report(
    city: str,
    *,
    beats_path: Path | None = None,
    log_path: Path | None = None,
    threshold: float = dedup_pairs.DEFAULT_THRESHOLD,
    num_perm: int = dedup_pairs.DEFAULT_NUM_PERM,
    shingle_size: int = dedup_pairs.DEFAULT_SHINGLE,
    poi_filter: Iterable[str] | None = None,
    classifier: Callable[[dict, dict], dict] = classify_pair,
    now: _dt.datetime | None = None,
    out_dir: Path | None = None,
) -> dict:
    """Mine pairs, classify each via `classifier`, write markdown report.

    Returns a dict with keys: `report_path`, `timestamp`, `pairs` (list of
    {beat_a, beat_b, jaccard, verdict, recommendation}), `city`.
    """
    beats_path = beats_path or Path("data") / city / "beats.json"
    beats = _load_beats(beats_path)
    beats_by_id = {b["beat_id"]: b for b in beats}

    if poi_filter:
        poi_lower = {p.lower() for p in poi_filter}
        filtered = [b for b in beats if b.get("poi_name", "").lower() in poi_lower]
    else:
        filtered = beats

    pairs = dedup_pairs.find_pairs(
        filtered,
        threshold=threshold,
        num_perm=num_perm,
        shingle_size=shingle_size,
    )

    results: list[dict] = []
    for p in pairs:
        a = beats_by_id.get(p["beat_a"])
        b = beats_by_id.get(p["beat_b"])
        if not a or not b:
            continue
        verdict = classifier(a, b)
        results.append(
            {
                "beat_a": p["beat_a"],
                "beat_b": p["beat_b"],
                "jaccard": p["jaccard"],
                "verdict": verdict,
                "recommendation": _RECOMMENDATION_BY_CLASS.get(
                    verdict.get("classification", "different_story"), "KEEP_BOTH"
                ),
            }
        )

    ts = _timestamp(now)
    out_dir = out_dir or (Path("data") / city / "_dedup_review")
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"{ts}.md"
    report_path.write_text(
        build_report(
            results,
            city=city,
            threshold=threshold,
            num_perm=num_perm,
            shingle_size=shingle_size,
            beats_by_id=beats_by_id,
            timestamp=ts,
        ),
        encoding="utf-8",
    )
    return {
        "report_path": str(report_path),
        "timestamp": ts,
        "pairs": results,
        "city": city,
    }


def apply_decisions(
    decisions: list[dict],
    *,
    beats_path: Path,
    log_path: Path,
    city: str,
    out_dir: Path | None = None,
    now: _dt.datetime | None = None,
    skip_commit: bool = False,
) -> dict:
    """Apply a decision list to beats.json and append audit log lines.

    Each decision must have: `beat_a`, `beat_b`, `jaccard`, `classification`,
    `action` (one of VALID_ACTIONS), plus `merged_text` when action == COMBINE.
    Optional: `approver`.

    Returns: `{"mutations": int, "log_path": str, "audit_lines": list[dict]}`.
    """
    beats = _load_beats(beats_path)
    log = _load_log(log_path)

    beats_by_id = {b["beat_id"]: b for b in beats}
    keep: dict[str, dict] = dict(beats_by_id)
    audit_lines: list[dict] = []

    for dec in decisions:
        a_id = dec["beat_a"]
        b_id = dec["beat_b"]
        action = dec["action"]
        if action not in VALID_ACTIONS:
            raise ValueError(f"invalid action {action!r} for pair ({a_id}, {b_id})")
        a = keep.get(a_id)
        b = keep.get(b_id)
        if not a or not b:
            # Either beat was already removed by a prior decision — skip safely.
            continue

        if action == "SKIP":
            drop_id = _newer_beat(a, b)
            # Never drop a verified beat in favour of an unverified duplicate —
            # verification is human-reviewed content we don't want to lose to a
            # timestamp tie-break. If exactly one side is verified, keep it.
            a_ver = (a.get("fact_check") or {}).get("status") == "verified"
            b_ver = (b.get("fact_check") or {}).get("status") == "verified"
            if a_ver and not b_ver:
                drop_id = b_id
            elif b_ver and not a_ver:
                drop_id = a_id
            keep.pop(drop_id, None)
        elif action == "INSERT":
            pass  # both stay, no mutation
        elif action == "COMBINE":
            merged_text = dec.get("merged_text")
            if not merged_text or not merged_text.strip():
                raise ValueError(
                    f"COMBINE action for ({a_id}, {b_id}) requires non-empty merged_text"
                )
            new_beat = dict(a)
            new_beat["script_body"] = merged_text.strip()
            new_beat["script_body_hash"] = _normalize_hash(merged_text)
            # The merged body is new text — drop any inherited verification so a
            # "verified" badge can't carry onto content no human checked. The
            # merged beat must be re-run through /fact-check.
            merged_fc = dict(new_beat.get("fact_check") or {})
            merged_fc["status"] = "unverified"
            merged_fc.pop("verified_body_hash", None)
            new_beat["fact_check"] = merged_fc
            new_beat["merged_from"] = [a_id, b_id]
            pair_digest = hashlib.sha256(
                f"{a_id}\n{b_id}".encode("utf-8")
            ).hexdigest()[:16]
            new_beat["beat_id"] = f"merged_{pair_digest}"
            meta = dict(new_beat.get("_meta") or {})
            meta["merged_at"] = _iso(now)
            meta["prompt_version"] = "dedup_merge_v1"
            new_beat["_meta"] = meta
            keep.pop(a_id, None)
            keep.pop(b_id, None)
            keep[new_beat["beat_id"]] = new_beat
        elif action == "KEEP_BOTH":
            a["dedup_reviewed"] = True
            b["dedup_reviewed"] = True

        audit_lines.append(
            {
                "ts": _iso(now),
                "pair": [a_id, b_id],
                "jaccard": dec["jaccard"],
                "classification": dec["classification"],
                "action": action,
                "approver": dec.get("approver", ""),
            }
        )

    final_beats = list(keep.values())

    if not skip_commit:
        beats_io.commit(
            final_beats,
            log,
            beats_path=beats_path,
            log_path=log_path,
        )

    out_dir = out_dir or (Path("data") / city / "_dedup_review")
    out_dir.mkdir(parents=True, exist_ok=True)
    log_jsonl = out_dir / "_log.jsonl"
    with log_jsonl.open("a", encoding="utf-8") as fh:
        for line in audit_lines:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")

    return {
        "mutations": len(audit_lines),
        "log_path": str(log_jsonl),
        "audit_lines": audit_lines,
        "final_beats": final_beats,
    }


# ---------- Interactive CLI (task 7) ----------

_ACTION_BY_KEY = {
    "a": None,  # accept recommendation — resolved per-pair
    "s": "SKIP",
    "c": "COMBINE",
    "k": "KEEP_BOTH",
    "i": "INSERT",
}


def _prompt_interactive(pairs: list[dict]) -> tuple[list[dict], bool]:
    decisions: list[dict] = []
    quit_flag = False
    for i, pair in enumerate(pairs, start=1):
        rec = pair["recommendation"]
        classification = pair["verdict"].get("classification", "different_story")
        print(f"\nPair {i}/{len(pairs)} — Jaccard {pair['jaccard']:.2f}")
        print(f"  A: {pair['beat_a']}")
        print(f"  B: {pair['beat_b']}")
        print(f"  Haiku: {classification}")
        print(f"  Recommend: {rec}")
        while True:
            choice = input("  [a]ccept / [s]kip / [i]nsert / [c]ombine / [k]eep-both / [q]uit: ").strip().lower()
            if choice == "q":
                quit_flag = True
                break
            if choice == "a":
                action = rec
                break
            if choice in _ACTION_BY_KEY:
                action = _ACTION_BY_KEY[choice]
                break
            print("  (expected one of a/s/i/c/k/q)")
        if quit_flag:
            break
        merged_text = None
        if action == "COMBINE":
            print("  Paste merged text, then a blank line to end:")
            collected: list[str] = []
            while True:
                line = input()
                if not line:
                    break
                collected.append(line)
            merged_text = "\n".join(collected)
        decisions.append(
            {
                "beat_a": pair["beat_a"],
                "beat_b": pair["beat_b"],
                "jaccard": pair["jaccard"],
                "classification": classification,
                "action": action,
                "merged_text": merged_text,
            }
        )
    return decisions, quit_flag


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("city", help="city slug (e.g. paris)")
    ap.add_argument("--threshold", type=float, default=dedup_pairs.DEFAULT_THRESHOLD)
    ap.add_argument("--num-perm", type=int, default=dedup_pairs.DEFAULT_NUM_PERM)
    ap.add_argument("--shingle-size", type=int, default=dedup_pairs.DEFAULT_SHINGLE)
    ap.add_argument("--poi", action="append", default=None, help="restrict to this POI (repeatable)")
    ap.add_argument("--beats-path", default=None)
    ap.add_argument("--log-path", default=None)
    ap.add_argument(
        "--report-only",
        action="store_true",
        help="mine pairs, classify, write report; skip interactive apply",
    )
    args = ap.parse_args(argv[1:])

    beats_path = Path(args.beats_path) if args.beats_path else Path("data") / args.city / "beats.json"
    log_path = Path(args.log_path) if args.log_path else Path("data") / args.city / "book-log.json"

    report = run_report(
        args.city,
        beats_path=beats_path,
        log_path=log_path,
        threshold=args.threshold,
        num_perm=args.num_perm,
        shingle_size=args.shingle_size,
        poi_filter=args.poi,
    )
    print(f"beat-dedup: wrote report → {report['report_path']}")
    print(f"beat-dedup: {len(report['pairs'])} candidate pair(s)")

    if args.report_only:
        return 0

    if not report["pairs"]:
        print("beat-dedup: nothing to apply")
        return 0

    if not sys.stdin.isatty():
        print(
            "beat-dedup: refuse to apply — stdin is not a TTY. Applying requires "
            "interactive per-pair approval. Re-run in a real terminal, or re-run "
            "with --report-only to inspect the report without applying.",
            file=sys.stderr,
        )
        return 2

    decisions, quit_flag = _prompt_interactive(report["pairs"])
    if quit_flag and not decisions:
        print("beat-dedup: quit before any decisions; nothing applied")
        return 0

    result = apply_decisions(
        decisions,
        beats_path=beats_path,
        log_path=log_path,
        city=args.city,
    )
    suffix = " (resumable: remaining pairs un-applied)" if quit_flag else ""
    print(f"beat-dedup: applied {result['mutations']} decisions{suffix}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
