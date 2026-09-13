#!/usr/bin/env python
"""Chunk-level pipeline-report builder for `/unified-beat-extract`.

The skill spec lists ~12 report sections at the end (lens distribution,
length-class shape, sub_location coverage, foreign-phrase preservation,
extractor_state summary, etc.). Until now those were hand-computed in
ad-hoc Python after each extraction. This module turns them into a
single callable that returns a structured dict.

The output dict is what the skill's PIPELINE REPORT step reads back to
the user. It also feeds the end-of-book promotion gate (≤5 %
imported_context across the whole book, fabrication audit at chunk
boundaries).

This is reporting only — no validation, no I/O. Pure function over the
beats list + chunk text + corpus context.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from scripts.extract_validators import (
    _CLASS_RANGES,
    copying_gate,
    fabrication_probe,
    word_count,
)
from scripts.verbatim import VERBATIM_RUN_BLOCK, verbatim_ratio


def audit_chunk(
    *,
    beats: list[dict],
    chunk_text: str,
    poi_index: dict[str, dict],
    live_beats: list[dict] | None = None,
) -> dict[str, Any]:
    """Produce the §PIPELINE REPORT dict for a single-chunk extraction.

    Args:
        beats: the new beats emitted in this run (only — not the full
            corpus). Caller is responsible for the chunk filter.
        chunk_text: the source chunk text (same one cited in
            `source_passage` fields).
        poi_index: name → POI dict from `data/{city}/poi-raw.json`.
            Used for tier lookup and existing-POI matching.
        live_beats: optional live-corpus beat list. When provided, the
            report includes NEW (POI, lens) coverage analysis — how many
            of this chunk's beats open a (POI, lens) combination not
            already in the live corpus. Skip if you're auditing in
            isolation.

    Returns a dict with these top-level keys (all sections in the spec):
        - extraction_summary
        - poi_matching
        - collision_check
        - length_class_distribution
        - enum_distribution
        - establishing_coverage
        - sensory_anchor_consistency
        - sub_location_coverage
        - trigger_address_coverage
        - foreign_phrase_preservation
        - structural_beat_types
        - extractor_state_summary
        - fabrication_audit
        - new_coverage   (only when live_beats is provided)
        - yield_per_1k_words
    """
    n = len(beats)
    src_words = word_count(chunk_text)

    out: dict[str, Any] = {
        "extraction_summary": _extraction_summary(beats),
        "poi_matching": _poi_matching(beats, poi_index),
        "collision_check": _collision_check(beats),
        "length_class_distribution": _length_class_distribution(beats),
        "enum_distribution": _enum_distribution(beats),
        "establishing_coverage": _establishing_coverage(beats, poi_index, live_beats),
        "sensory_anchor_consistency": _sensory_anchor_consistency(beats, poi_index),
        "sub_location_coverage": _sub_location_coverage(beats, poi_index),
        "trigger_address_coverage": _trigger_address_coverage(beats),
        "foreign_phrase_preservation": _foreign_phrase_preservation(beats),
        "structural_beat_types": _structural_beat_types(beats),
        "extractor_state_summary": _extractor_state_summary(beats),
        "fabrication_audit": _fabrication_audit(beats, chunk_text),
        "copying_audit": _copying_audit(beats),
        "yield_per_1k_words": (n / src_words * 1000) if src_words else 0.0,
    }
    if live_beats is not None:
        out["new_coverage"] = _new_coverage(beats, live_beats)
    return out


# ─── Section builders ────────────────────────────────────────────────


def _extraction_summary(beats: list[dict]) -> dict[str, Any]:
    return {
        "total_beats": len(beats),
        "beats_per_lens": dict(Counter(b.get("lens", "") for b in beats).most_common()),
        "beats_per_poi": dict(Counter(b.get("poi_name", "") for b in beats).most_common()),
    }


def _poi_matching(beats: list[dict], poi_index: dict[str, dict]) -> dict[str, Any]:
    new_poi = sorted({b["poi_name"] for b in beats if b.get("new_poi")})
    sub_poi = sorted({b["poi_name"] for b in beats if b.get("parent_poi")})
    existing = sorted(
        {b["poi_name"] for b in beats if not b.get("new_poi") and b["poi_name"] in poi_index}
    )
    unmatched = [
        b["poi_name"]
        for b in beats
        if not b.get("new_poi") and not b.get("parent_poi") and b["poi_name"] not in poi_index
    ]
    return {
        "existing_count": len(existing),
        "new_pois": new_poi,
        "sub_pois": sub_poi,
        "unmatched": sorted(set(unmatched)),
    }


def _collision_check(beats: list[dict]) -> dict[str, Any]:
    by_id: Counter[str] = Counter(b.get("beat_id", "") for b in beats)
    id_collisions = [bid for bid, c in by_id.items() if c > 1]
    by_identity: dict[tuple, list[str]] = {}
    for b in beats:
        key = (
            b.get("city_name"),
            b.get("poi_name"),
            b.get("lens"),
            b.get("book_slug"),
            b.get("topic_slug"),
        )
        by_identity.setdefault(key, []).append(b.get("beat_id", ""))
    identity_collisions = {k: v for k, v in by_identity.items() if len(v) > 1}
    return {
        "beat_id_collisions": id_collisions,
        "identity_tuple_collisions": [
            {"key": list(k), "beats": v} for k, v in identity_collisions.items()
        ],
    }


def _length_class_distribution(beats: list[dict]) -> dict[str, Any]:
    classes = Counter(b.get("beat_length_class", "") for b in beats)
    out_of_range: list[dict] = []
    for b in beats:
        cls = b.get("beat_length_class", "")
        wc = word_count(b.get("script_body", ""))
        lo, hi = _CLASS_RANGES.get(cls, (0, 10**9))
        if not (lo <= wc <= hi):
            out_of_range.append(
                {
                    "beat_id": b.get("beat_id"),
                    "declared": cls,
                    "word_count": wc,
                    "expected_range": [lo, hi],
                }
            )
    return {
        "counts": dict(classes),
        "out_of_range": out_of_range,
    }


def _enum_distribution(beats: list[dict]) -> dict[str, Any]:
    n = len(beats) or 1
    nf = Counter(b.get("narrative_function", "") for b in beats)
    bt = Counter(b.get("beat_type", "") for b in beats)
    er = Counter(b.get("emotional_register", "") for b in beats)

    over_thresh = {
        field: [k for k, c in counter.most_common() if c / n > 0.7]
        for field, counter in [
            ("narrative_function", nf),
            ("beat_type", bt),
            ("emotional_register", er),
        ]
    }
    return {
        "narrative_function": dict(nf),
        "beat_type": dict(bt),
        "emotional_register": dict(er),
        "over_70pct": {k: v for k, v in over_thresh.items() if v},
    }


def _establishing_coverage(
    beats: list[dict],
    poi_index: dict[str, dict],
    live_beats: list[dict] | None,
) -> dict[str, Any]:
    """For every POI touched in this run with poi_role=stop, does it now
    have ≥1 establishing beat (counting both live + new)?"""
    touched = {b["poi_name"] for b in beats if not b.get("new_poi")}
    needs: list[str] = []
    has: list[str] = []
    for poi in touched:
        meta = poi_index.get(poi)
        if not meta or meta.get("poi_role") != "stop":
            continue
        in_run = any(
            b["poi_name"] == poi and b.get("narrative_function") == "establishing" for b in beats
        )
        in_live = (
            any(
                b["poi_name"] == poi and b.get("narrative_function") == "establishing"
                for b in (live_beats or [])
            )
            if live_beats is not None
            else False
        )
        (has if (in_run or in_live) else needs).append(poi)
    return {"with_establishing": sorted(has), "missing_establishing": sorted(needs)}


def _sensory_anchor_consistency(beats: list[dict], poi_index: dict[str, dict]) -> dict[str, Any]:
    sa_no_cues = [
        b.get("beat_id") for b in beats if b.get("sensory_anchor") and not b.get("physical_cues")
    ]
    cues_no_sa = [
        b.get("beat_id") for b in beats if not b.get("sensory_anchor") and b.get("physical_cues")
    ]
    tier3_empty: list[str] = []
    for b in beats:
        meta = poi_index.get(b.get("poi_name", ""))
        if meta and meta.get("importance_tier", 0) >= 3 and not b.get("physical_cues"):
            tier3_empty.append(b.get("beat_id", ""))
    return {
        "sensory_anchor_no_cues": sa_no_cues,
        "cues_without_sensory_anchor": cues_no_sa,
        "tier3_plus_empty_cues": tier3_empty,
    }


def _sub_location_coverage(beats: list[dict], poi_index: dict[str, dict]) -> dict[str, Any]:
    by_poi: dict[str, set[str]] = {}
    populated = 0
    for b in beats:
        sub = b.get("sub_location")
        if sub:
            populated += 1
            by_poi.setdefault(b["poi_name"], set()).add(sub)
    tier5_no_sub: list[str] = []
    for poi, sl in by_poi.items():
        meta = poi_index.get(poi, {})
        if meta.get("importance_tier") == 5 and len(sl) == 0:
            tier5_no_sub.append(poi)
    return {
        "populated": populated,
        "by_poi": {k: sorted(v) for k, v in by_poi.items()},
        "tier5_pois_with_no_sub_locations": tier5_no_sub,
    }


def _trigger_address_coverage(beats: list[dict]) -> dict[str, Any]:
    populated = [b for b in beats if b.get("trigger_address")]
    by_anchor: dict[str, list[str]] = {}
    for b in populated:
        by_anchor.setdefault(b["poi_name"], []).append(b["trigger_address"])
    return {
        "populated": len(populated),
        "by_anchor": {k: sorted(v) for k, v in by_anchor.items()},
    }


def _foreign_phrase_preservation(beats: list[dict]) -> dict[str, Any]:
    distinct: dict[str, str] = {}
    inconsistent: list[dict] = []
    for b in beats:
        body = b.get("script_body", "")
        for entry in b.get("inline_foreign_phrases", []):
            phrase = entry.get("phrase", "")
            gloss = entry.get("gloss", "")
            if phrase and phrase not in distinct:
                distinct[phrase] = gloss
            if phrase and phrase not in body:
                inconsistent.append({"beat_id": b.get("beat_id"), "phrase": phrase})
    return {
        "distinct_count": len(distinct),
        "first_20": [{"phrase": p, "gloss": g} for p, g in list(distinct.items())[:20]],
        "inconsistent_with_script_body": inconsistent,
    }


def _structural_beat_types(beats: list[dict]) -> dict[str, Any]:
    counts = Counter(b.get("beat_type", "") for b in beats)
    structural = ["stop_orientation", "transit", "sidebar", "practicalities"]
    return {k: counts.get(k, 0) for k in structural}


def _extractor_state_summary(beats: list[dict]) -> dict[str, Any]:
    n = len(beats) or 1
    states = Counter(b.get("fact_check", {}).get("extractor_state", "MISSING") for b in beats)
    imported = states.get("imported_context", 0)
    return {
        "counts": dict(states),
        "imported_context_ratio": imported / n,
        "over_40pct_ceiling": (imported / n) > 0.4,
        "missing_extractor_state": [
            b.get("beat_id") for b in beats if "extractor_state" not in b.get("fact_check", {})
        ],
    }


def _copying_audit(beats: list[dict]) -> dict[str, Any]:
    """How much of this chunk's prose came out of the book word for word.

    The distribution, not only the failures. A chunk sitting just under the block is
    a chunk about to go over it on the next book, and a count of zero blocked says
    nothing about that. `verbatim_ratio` is reported beside the run and gates
    nothing: it describes how copied a whole body is, and scores one lifted clause
    in a long body near zero, which is how a corpus reached 78% lifted while that
    number read 17%.
    """
    rows = []
    for beat in beats:
        run = copying_gate(beat.get("script_body", ""), beat.get("source_passage", ""))
        rows.append(
            {
                "beat_id": beat.get("beat_id", ""),
                "run": run[0],
                "text": run[1],
                "ratio": round(
                    verbatim_ratio(
                        beat.get("script_body", "") or "", beat.get("source_passage", "") or ""
                    ),
                    3,
                ),
            }
        )
    over = [r for r in rows if r["run"] >= VERBATIM_RUN_BLOCK]
    runs = sorted(r["run"] for r in rows)
    return {
        "block_at": VERBATIM_RUN_BLOCK,
        "blocked": len(over),
        "blocked_beats": [
            {"beat_id": r["beat_id"], "run": r["run"], "text": r["text"]} for r in over
        ],
        "median_run": runs[len(runs) // 2] if runs else 0,
        "longest_run": runs[-1] if runs else 0,
        "runs_within_two_of_the_block": sum(
            1 for r in runs if VERBATIM_RUN_BLOCK - 2 <= r < VERBATIM_RUN_BLOCK
        ),
        "median_ratio": (sorted(r["ratio"] for r in rows)[len(rows) // 2] if rows else 0.0),
    }


def _fabrication_audit(beats: list[dict], chunk_text: str) -> dict[str, Any]:
    """Programmatic re-probe of every emitted beat against the chunk text.

    A beat that emerged with `extractor_state: clean` but the probe
    detects unsourced claims is a self-flag failure. A beat that emerged
    with `extractor_state: imported_context` and the probe still finds
    new claims means `flagged_claims` is incomplete."""
    self_flag_failures: list[dict] = []
    incomplete_flags: list[dict] = []
    for b in beats:
        state = b.get("fact_check", {}).get("extractor_state", "clean")
        verdict = fabrication_probe(
            script_body=b.get("script_body", ""),
            physical_cues=b.get("physical_cues", []),
            source_passage=b.get("source_passage", ""),
            chunk_text=chunk_text,
        )
        if verdict.has_fabrication and state == "clean":
            self_flag_failures.append(
                {
                    "beat_id": b.get("beat_id"),
                    "unsourced_in_body": verdict.unsourced_claims,
                    "unsourced_in_cues": [
                        {"cue_index": idx, "claim": c} for idx, c in verdict.cue_unsourced
                    ],
                }
            )
        elif verdict.has_fabrication and state == "imported_context":
            existing = set(b.get("fact_check", {}).get("flagged_claims", []))
            new_findings = [c for c in verdict.unsourced_claims if c not in existing]
            new_cue_findings = [
                {"cue_index": idx, "claim": c}
                for idx, c in verdict.cue_unsourced
                if c not in existing
            ]
            if new_findings or new_cue_findings:
                incomplete_flags.append(
                    {
                        "beat_id": b.get("beat_id"),
                        "missing_from_flagged_claims": new_findings,
                        "missing_cue_findings": new_cue_findings,
                    }
                )
    return {
        "self_flag_failures": self_flag_failures,
        "incomplete_imported_context_flags": incomplete_flags,
    }


def _new_coverage(beats: list[dict], live_beats: list[dict]) -> dict[str, Any]:
    """For each beat: is its (POI, lens) tuple new vs the live corpus?"""
    live_combos: set[tuple[str, str]] = {
        (b.get("poi_name", ""), b.get("lens", "")) for b in live_beats
    }
    new_combos: list[dict] = []
    for b in beats:
        if b.get("new_poi"):
            new_combos.append({"reason": "new_poi", "poi": b["poi_name"], "lens": b.get("lens")})
            continue
        combo = (b.get("poi_name", ""), b.get("lens", ""))
        if combo not in live_combos:
            new_combos.append(
                {
                    "reason": "new_combo",
                    "poi": b["poi_name"],
                    "lens": b.get("lens"),
                }
            )
    return {
        "new_combinations": len(new_combos),
        "new_ratio": len(new_combos) / max(1, len(beats)),
        "details": new_combos,
    }
