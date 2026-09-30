"""Read-only report: which same-place beat pairs the engine's play-time duplicate guard
(`src/tour/beat_select.py`, B8-lite) treats as one story, and on which signal — so a
change to the guard is judged on a whole corpus, not on a handful of golden tours.

Why it exists (2026-09-29): on the job-B replay the guard merged two distinct new stories
that each listed one shared name. Tightening the name signal was built and REVERTED after
this report showed it also released about a dozen real cross-book duplicates in the legacy
corpora (Brooklyn Bridge x3, Flatiron, Wyckoff House). Diff this report's output before and
after any change to the guard.

Every pair at one place that shares a lens is checked, which is an UPPER BOUND on what the
guard ever sees (it only compares beats selected together). Legacy records are read as
they are; new-shape records through the export view, skipping what the publish blocks.
$0, files only. Usage: make b8-dedup-report FILE=data/paris/beats.json
"""

from __future__ import annotations

import argparse
import collections
import itertools
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.upload_paris import _beat_blocked, _export_view, _lenses_of
from scripts.validate_beats import _record_shape
from src.tour import beat_select as bs
from src.tour.contract import BeatRef


def _ref(raw: dict) -> BeatRef:
    view = _export_view(raw)
    body = view.get("script_body") or ""
    return BeatRef(
        id=raw.get("beat_id") or "?",
        poi_id=raw.get("poi_name") or "?",
        lenses=tuple(_lenses_of(view)),
        entities=tuple(e for e in view.get("entities") or () if isinstance(e, str)),
        subject_tag=view.get("subject_tag"),
        script_body=body,
        word_count=len(body.split()),
    )


def _signals(a: BeatRef, b: BeatRef) -> list[str]:
    """The guard's signals that fire for this pair (`beat_select._claims_collide`)."""
    fired = []
    if bs._entities_jaccard(a, b) >= bs.B8_JACCARD_THRESHOLD:
        fired.append("entities")
    if bs._subject_tag_jaccard(a, b) >= bs.B8_JACCARD_THRESHOLD:
        fired.append("subject_tag")
    if bs._char_ngram_jaccard(a, b) >= bs.B8_CHAR_NGRAM_THRESHOLD:
        fired.append("prose")
    if bs._shares_year_and_canonical_entities(a, b):
        fired.append("year+names")
    return fired


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    args = ap.parse_args()
    records = json.loads(Path(args.file).read_text())
    records = records if isinstance(records, list) else records.get("beats", [])

    by_place: dict[str, list[tuple[BeatRef, str]]] = collections.defaultdict(list)
    for raw in records:
        if _record_shape(raw) == "new" and _beat_blocked(raw):
            continue
        ref = _ref(raw)
        if ref.script_body:
            by_place[raw.get("poi_name") or "?"].append((ref, raw.get("title") or ref.id))

    pairs = 0
    lines: list[str] = []
    for place, beats in sorted(by_place.items()):
        for (a, ta), (b, tb) in itertools.combinations(beats, 2):
            if not bs._share_lens(a, b):
                continue
            pairs += 1
            if not bs._claims_collide(a, b):
                continue
            fired = _signals(a, b)
            shared = sorted(set(a.entities) & set(b.entities))
            lines.append(f"  {place} | {ta!r} <> {tb!r} | {'+'.join(fired)} | shared {shared}")

    print(f"file {args.file}: {sum(len(v) for v in by_place.values())} beats, "
          f"{len(by_place)} places, {pairs} lens-sharing pairs, {len(lines)} collide")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
