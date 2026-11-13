"""Diagnostic CLI: side-by-side overlap report against a golden fixture.

Runs the full shipped pipeline (select_route → build_poi_beat_plans_capped →
generate) against the live Paris Neo4j and prints a per-POI diff against
fixtures/tour_golden/<fixture>.json, counting SCRIPT-level beats exactly like
the authoritative gate (tests/test_tour_golden_*.py) so the numbers match.

NOT a test dependency — useful when iterating on calibration knobs to see
exactly which beats land where.

Usage:
    python scripts/tour_golden_diff.py pdv_round_trip_60min
    python scripts/tour_golden_diff.py ile_oneway_90min
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.connection import create_driver
from src.tour.contract import BeatSequence, TourInput
from src.tour.generation import generate
from src.tour.routing_client import RoutingClient
from src.tour.selection import build_poi_beat_plans_capped, load_paris_corpus, select_route

FIXTURE_DIR = ROOT / "fixtures" / "tour_golden"


def main(name: str) -> int:
    fixture_path = FIXTURE_DIR / f"{name}.json"
    if not fixture_path.exists():
        candidates = sorted(p.stem for p in FIXTURE_DIR.glob("*.json"))
        print(f"Fixture not found: {fixture_path}")
        print(f"Available: {candidates}")
        return 2

    fixture = json.loads(fixture_path.read_text())
    inp = fixture["input"]
    expected = set(fixture["expected_stable_beat_ids"])

    driver = create_driver()
    snapshot = load_paris_corpus(driver, city_slug=inp["city_slug"])

    tour_input = TourInput(
        start=tuple(inp["start"]),
        duration_min=inp["duration_min"],
        city_slug=inp["city_slug"],
        round_trip=inp["round_trip"],
        lenses=inp.get("lenses"),
        theme_hint=inp.get("theme_hint"),
        start_label=inp.get("start_label"),
    )

    # Routed leg costs when the local Valhalla is up (make valhalla-up);
    # identical to the haversine path when it isn't (total fallback).
    with RoutingClient() as routing_client:
        route = select_route(tour_input, snapshot, routing_client=routing_client)

    # Measurement parity: build through the SHIPPED path (merges co-located
    # demoted_beats + applies the governor cap) and count SCRIPT-level beat
    # sentences after generate() — exactly what the authoritative gate does
    # (tests/test_tour_golden_*.py). Previously this CLI called select_poi_beats on
    # the raw pool and counted plan.beats, so its numbers drifted from the gate.
    seq = BeatSequence(
        poi_beats=tuple(
            pb
            for pb, _ in build_poi_beat_plans_capped(
                route, snapshot, lenses=tour_input.lenses, end_is_none=tour_input.end is None
            )
        )
    )
    script = generate(seq, route, tour_input)

    print("=" * 80)
    print(f"FIXTURE: {name}")
    print(f"Input: start={tour_input.start} duration={tour_input.duration_min}min "
          f"round_trip={tour_input.round_trip}")
    print(f"Spine: {route.spine_area} (expected {fixture.get('expected_spine_area')!r})")
    print(f"POIs picked: {[p.name for p in route.pois]}")
    print(f"Expected POIs: {fixture.get('expected_pois')}")
    print()

    # Fixtures pin the DURABLE corpus slug (`stable_beat_id`), while a Script's
    # `source_id` is the per-database UUID. Translate before comparing, or every
    # overlap reads 0% — the exact defect the re-key exists to remove. The gate's
    # copy of this mapping lives in tests/test_tour_golden_consistency.py, which
    # scripts/ may not import (tests/ is .dockerignore'd out of the image), so it is
    # repeated here and guarded by test_golden_diff_cli_reads_the_durable_key.
    stable_for: dict[str, str] = {
        beat.id: beat.stable_beat_id
        for poi in seq.poi_beats
        for beat in poi.beats
        if beat.stable_beat_id
    }
    for beats in seq.vignette_beats.values():
        for beat in beats:
            if beat.stable_beat_id:
                stable_for[beat.id] = beat.stable_beat_id

    generated_ids: set[str] = {
        stable_for[s.source_id]
        for s in script.script
        if s.source_type == "beat" and s.source_id in stable_for
    }

    poi_by_id = {p.id: p for p in route.pois}
    print("Per-POI breakdown:")
    for plan in seq.poi_beats:
        poi = poi_by_id.get(plan.poi_id)
        name_str = poi.name if poi else plan.poi_id
        tier_str = f"t{poi.tier}" if poi else "t?"
        plan_ids = {b.stable_beat_id for b in plan.beats if b.stable_beat_id}
        n_in_fix = len(plan_ids & expected)
        n_voiced = len(plan_ids & generated_ids)
        print(f"  {name_str} ({tier_str}): "
              f"strategy={plan.ordering_strategy}, "
              f"beats={len(plan.beats)} (voiced={n_voiced}, fixture-hits={n_in_fix})")

    overlap = len(expected & generated_ids)
    overlap_pct = overlap / len(expected) if expected else 0.0
    missing = sorted(expected - generated_ids)
    extra = sorted(generated_ids - expected)

    print()
    print(f"Overlap: {overlap}/{len(expected)} = {overlap_pct:.1%}")
    if missing:
        print(f"Missing fixture beats ({len(missing)}):")
        with driver.session() as s:
            for bid in missing[:25]:
                rec = s.run(
                    "MATCH (p:POI)-[:HAS_BEAT]->(b:NarrativeBeat {beat_id: $bid}) "
                    "RETURN p.name AS poi, b.sub_location AS sub, "
                    "b.narrative_function AS nf, b.script_body AS body",
                    bid=bid,
                ).data()
                if rec:
                    r = rec[0]
                    body = (r.get("body") or "")[:60].replace("\n", " ")
                    print(f"  {bid[-38:]} | {r['poi']:35s} | "
                          f"sub={(r.get('sub') or '')[:25]:25s} | "
                          f"nf={(r.get('nf') or '')[:14]:14s} | {body!r}")
        if len(missing) > 25:
            print(f"  … {len(missing) - 25} more")
    if extra:
        print(f"Extra generated beats not in fixture: {len(extra)} (omitted; not penalized)")

    driver.close()
    return 0 if overlap_pct >= 0.90 else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
