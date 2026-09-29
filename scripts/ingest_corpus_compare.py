"""Compare a new-shape beats file against the legacy corpus at the same places — `make
ingest-corpus-compare NEW=<beats.json>`. $0, read-only.

The district-port question (owner, 2026-09-19): is the new corpus trustworthy — free of direct
copies from its books — and how does it compare on coverage, beats per place and lens variety?
Copying is measured with the repo's own `scripts.verbatim` (the longest shared run, and the share
of 8-word shingles), each beat against its OWN source chunks: a legacy beat's `source_chunk_slug`,
a new beat's narration against every chunk its claims cite (the worst one counts). Places are the
new file's. Three sets: legacy, every book; legacy from only the chapters the new file was built
from (like for like); and the new file, whole and publishable (neither held nor `practicalities`,
the two kinds the uploader keeps out). Prints JSON with every number the report quotes, and the
spread of beats per place, not only the mean.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.verbatim import (
    VERBATIM_RUN_BLOCK,
    VERBATIM_THRESHOLD,
    max_verbatim_run,
    verbatim_ratio,
)

#: The shingle share the 2026-09-19 report also counted ("10%+ of shingles shared").
LOW_RATIO: float = 0.10


def _chunk_text(books: Path, source_id: str, chunk: str, cache: dict) -> str:
    key = (source_id, chunk)
    if key not in cache:
        path = books / source_id / f"{chunk}.txt"
        cache[key] = path.read_text(encoding="utf-8") if path.exists() else ""
    return cache[key]


def _copying(pairs: list[tuple[str, list[str]]]) -> dict[str, int]:
    """Counts over (body, [chunk texts]) pairs, each body against its worst chunk."""
    runs = ratios = high = 0
    for body, chunks in pairs:
        run = max((max_verbatim_run(body, c) for c in chunks), default=0)
        ratio = max((verbatim_ratio(body, c) for c in chunks), default=0.0)
        runs += run >= VERBATIM_RUN_BLOCK
        ratios += ratio >= LOW_RATIO
        high += ratio >= VERBATIM_THRESHOLD
    return {"beats": len(pairs), "any_8_word_run": runs, "ratio_10pct": ratios,
            "ratio_70pct": high}


def _shape(beats: list[dict], lenses_of) -> dict[str, Any]:
    per_place: dict[str, int] = defaultdict(int)
    lenses_at: dict[str, set[str]] = defaultdict(set)
    for b in beats:
        per_place[b["poi_name"]] += 1
        lenses_at[b["poi_name"]].update(lenses_of(b))
    counts = sorted(per_place.values())
    return {
        "beats": len(beats),
        "places": len(per_place),
        "beats_per_place_mean": round(len(beats) / len(per_place), 2) if per_place else 0,
        "beats_per_place_spread": {
            "min": counts[0] if counts else 0,
            "median": statistics.median(counts) if counts else 0,
            "max": counts[-1] if counts else 0,
        },
        "distinct_lenses": len(set().union(*lenses_at.values())) if lenses_at else 0,
        "lenses_per_place_mean": (
            round(sum(len(v) for v in lenses_at.values()) / len(lenses_at), 2) if lenses_at else 0
        ),
        "by_place": dict(sorted(per_place.items())),
    }


def compare(new: list[dict], legacy: list[dict], books: Path) -> dict[str, Any]:
    places = {b["poi_name"] for b in new}
    cache: dict = {}
    chapters = {
        (s["source_id"], s["chunk"]) for b in new for c in b["claims"] for s in c["sources"]
    }
    legacy_here = [b for b in legacy if b.get("poi_name") in places]
    like = [b for b in legacy_here if (b.get("book_slug"), b.get("source_chunk_slug")) in chapters]
    publishable = [
        b for b in new
        if not (b.get("review") or {}).get("held") and b.get("beat_type") != "practicalities"
    ]

    def legacy_pairs(beats):
        return [
            (b.get("script_body") or "",
             [_chunk_text(books, b.get("book_slug") or "", b.get("source_chunk_slug") or "",
                          cache)])
            for b in beats
        ]

    def new_pairs(beats):
        out = []
        for b in beats:
            cited = {(s["source_id"], s["chunk"]) for c in b["claims"] for s in c["sources"]}
            out.append((b["narration"]["text"],
                        [_chunk_text(books, sid, ch, cache) for sid, ch in sorted(cited)]))
        return out

    by_book: dict[str, list[dict]] = defaultdict(list)
    for b in legacy_here:
        by_book[b.get("book_slug") or "?"].append(b)
    claims = [c for b in new for c in b["claims"]]
    return {
        "places": sorted(places),
        "chapters": sorted(f"{s}/{c}" for s, c in chapters),
        "copying": {
            "legacy_by_book": {k: _copying(legacy_pairs(v)) for k, v in sorted(by_book.items())},
            "legacy_all": _copying(legacy_pairs(legacy_here)),
            "legacy_like_for_like": _copying(legacy_pairs(like)),
            "new": _copying(new_pairs(new)),
        },
        "shape": {
            "legacy_all": _shape(legacy_here, lambda b: [b["lens"]] if b.get("lens") else []),
            "legacy_like_for_like": _shape(like, lambda b: [b["lens"]] if b.get("lens") else []),
            "new_file": _shape(new, lambda b: b.get("lenses") or []),
            "new_publishable": _shape(publishable, lambda b: b.get("lenses") or []),
        },
        "provenance": {
            "new_claims": len(claims),
            "new_claims_with_span": sum(
                1 for c in claims if c["sources"] and all(s.get("span") for s in c["sources"])
            ),
            "new_beats_citing_two_books": sum(
                1 for b in new
                if len({s["source_id"] for c in b["claims"] for s in c["sources"]}) > 1
            ),
            "legacy_key_claims": sum(len(b.get("key_claims") or []) for b in legacy_here),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--new", type=Path, required=True, help="a new-shape beats.json")
    parser.add_argument("--legacy", type=Path, default=ROOT / "data" / "new_york" / "beats.json")
    parser.add_argument("--books", type=Path, default=ROOT / "Books" / "new_york")
    args = parser.parse_args(argv)
    new = json.loads(args.new.read_text(encoding="utf-8"))
    legacy = json.loads(args.legacy.read_text(encoding="utf-8"))
    print(json.dumps(compare(new, legacy, args.books), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
