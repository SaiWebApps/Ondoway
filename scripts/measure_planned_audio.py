"""Measure per-POI planned voiced audio vs the tier-dwell proxy.

Take this measurement before changing the dwell/audio accounting: for each
candidate anchor, what would selection consume under honest accounting,
max(dwell, planned_audio), versus the dwell alone?

Planned audio is routing.planned_audio_seconds — the engine's own per-beat rule
(est_spoken_seconds override, else word_count at routing.SPOKEN_WPM) — applied to the
plan select_poi_beats emits for the POI under the requested lenses — glue/vignette
seconds are excluded because selection never sees them.

Run via `make measure-planned-audio` (dev Neo4j from .env). Options:
  ARGS='--pois "Pont Neuf,Notre-Dame Cathedral" --duration-min 90'
"""

from __future__ import annotations

import argparse

from src.connection import create_driver
from src.tour.beat_select import select_poi_beats
from src.tour.routing import planned_audio_seconds, target_dwell_seconds
from src.tour.selection import CorpusSnapshot, load_paris_corpus


def measure(
    snapshot: CorpusSnapshot,
    *,
    poi_names: list[str] | None,
    lenses: list[str] | None,
) -> list[dict]:
    rows = []
    wanted = {n.strip().lower() for n in poi_names} if poi_names else None
    for poi in snapshot.pois:
        if wanted is not None and poi.name.lower() not in wanted:
            continue
        if wanted is None and poi.poi_role not in ("stop", "setting"):
            # mirror selection.POI_ROLE_MULTIPLIER: only seatable roles matter
            continue
        active = snapshot.beats_for(poi.id)
        plan = select_poi_beats(poi, active, interest_lenses=lenses)
        planned = planned_audio_seconds(plan.beats)
        # A stop's length is priced per visitor against the
        # place's own capacity, so the comparable figure here is what the
        # place absorbs from the outside — the one number every POI carries.
        dwell = poi.typical_duration_min * 60
        rows.append(
            {
                "name": poi.name,
                "tier": poi.tier,
                "active_beats": len(active),
                "planned_beats": len(plan.beats),
                "strategy": plan.ordering_strategy,
                "planned_s": planned,
                "dwell_s": dwell,
                "honest_s": max(dwell, planned),
            }
        )
    rows.sort(key=lambda r: r["honest_s"] - r["dwell_s"], reverse=True)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pois", help="comma-separated POI names (default: every stop-role POI)")
    parser.add_argument("--lenses", help="comma-separated interest lenses (default: none)")
    parser.add_argument("--city", default="paris")
    parser.add_argument(
        "--duration-min",
        type=int,
        help="if set, print the audio budget/floor this duration implies for context",
    )
    args = parser.parse_args()

    poi_names = [s for s in (args.pois or "").split(",") if s.strip()] or None
    lenses = [s for s in (args.lenses or "").split(",") if s.strip()] or None

    driver = create_driver()
    try:
        snapshot = load_paris_corpus(driver, city_slug=args.city)
    finally:
        driver.close()

    rows = measure(snapshot, poi_names=poi_names, lenses=lenses)
    header = (
        f"{'POI':<42} {'tier':>4} {'act':>4} {'plan':>4} {'strategy':<18} "
        f"{'planned_s':>9} {'dwell_s':>7} {'honest_s':>8}"
    )
    print(header)
    print("-" * len(header))
    for r in rows:
        print(
            f"{r['name']:<42.42} {r['tier']:>4} {r['active_beats']:>4} {r['planned_beats']:>4} "
            f"{r['strategy']:<18} {r['planned_s']:>9} {r['dwell_s']:>7} {r['honest_s']:>8}"
        )

    total_planned = sum(r["planned_s"] for r in rows)
    total_dwell = sum(r["dwell_s"] for r in rows)
    total_honest = sum(r["honest_s"] for r in rows)
    print("-" * len(header))
    print(
        f"{'TOTAL over ' + str(len(rows)) + ' POIs':<42} {'':>4} {'':>4} {'':>4} {'':<18} "
        f"{total_planned:>9} {total_dwell:>7} {total_honest:>8}"
    )
    if args.duration_min:
        budget = target_dwell_seconds(args.duration_min)
        print(
            f"\nduration {args.duration_min}min → dwell budget {budget}s, "
            f"fill floor {round(0.8 * budget)}s (FILL_PASS_DWELL_FLOOR_FRAC=0.8)"
        )


if __name__ == "__main__":
    main()
