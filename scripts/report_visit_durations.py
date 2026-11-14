"""The audit table for POI visit capacity — how a non-visitor checks the capacity pass.

WHY THIS EXISTS. The capacity pass (`scripts/poi_visit_duration.py`) prices 370
Paris places, and nobody reviewing the result has been to most of them. Paris facts
cannot be checked by the person who has to sign this off. So the review is
STRUCTURAL, and this report is what makes that possible: it puts the extremes and
the reasoning side by side, where a wrong number shows up as an implausible
RELATIONSHIP rather than as a wrong fact.

THE THREE CHECKS IT IS BUILT TO SUPPORT:

1. A niche museum has a HIGH inside capacity despite a LOW tier. If importance
   leaked into the number, the top-20 table is where it shows — it will read as a
   list of famous places instead of a list of absorbing ones.
2. A street, bridge or square has NO inside capacity. The "nothing to enter"
   section exists so that is visible rather than inferred.
3. The biggest monuments land in a plausible SPREAD, not all pinned at one value.

WHY THE CORRIDOR SECTION EXISTS. A parish church's interior is neither top-20 nor
bottom-20 of 370, so the extremes alone cannot answer the one question this
report exists for: do the interiors between Rue Royale and Notre-Dame
carry enough visit time between them to fill roughly two and a half hours? The
corridor section lists exactly those places so that question is answerable by
reading, not by guessing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.tour.routing import haversine_m

ROOT = Path(__file__).resolve().parent.parent

#: The reported failing case: Rue Royale to Notre-Dame, the corridor this whole
#: release is judged on. Coordinates are the owner's exact clicked start and end.
RUE_ROYALE: tuple[float, float] = (48.8686, 2.3229)
NOTRE_DAME: tuple[float, float] = (48.8530, 2.3499)

#: A place is "on the corridor" when going A -> place -> B costs no more than this
#: multiple of going A -> B directly. The standard corridor ellipse, rather than
#: a fixed off-axis distance.
DEFAULT_CORRIDOR_FACTOR: float = 1.5

EXTREME_ROWS: int = 20


def load_pois(slug: str) -> list[dict[str, Any]]:
    path = ROOT / "data" / slug / "poi-raw.json"
    if not path.exists():
        raise SystemExit(f"✗ no POI file at {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit(f"✗ {path} is not a JSON array of POIs.")
    return data


def is_priced(poi: dict[str, Any]) -> bool:
    """True when the capacity pass has been over this POI."""
    return isinstance(poi.get("typical_duration_min"), int) and bool(
        str(poi.get("visit_basis", "")).strip()
    )


def corridor_members(
    pois: list[dict[str, Any]], factor: float
) -> list[tuple[float, dict[str, Any]]]:
    """POIs inside the A -> B ellipse, ordered by how far along the corridor they sit.

    The ordering key is distance from A, so the printed list reads in walking order
    and a gap in the corridor is visible as a jump in the numbers.
    """
    a_lat, a_lng = RUE_ROYALE
    b_lat, b_lng = NOTRE_DAME
    direct = haversine_m(a_lat, a_lng, b_lat, b_lng)
    budget = direct * factor
    found: list[tuple[float, dict[str, Any]]] = []
    for poi in pois:
        lat, lng = poi.get("latitude"), poi.get("longitude")
        if not isinstance(lat, int | float) or not isinstance(lng, int | float):
            continue
        from_a = haversine_m(a_lat, a_lng, lat, lng)
        to_b = haversine_m(lat, lng, b_lat, b_lng)
        if from_a + to_b <= budget:
            found.append((from_a, poi))
    found.sort(key=lambda pair: pair[0])
    return found


def inside_seconds(poi: dict[str, Any]) -> int | None:
    value = poi.get("visit_seconds_inside")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def print_row(poi: dict[str, Any], *, prefix: str = "") -> None:
    inside = inside_seconds(poi)
    inside_str = "—  (nothing to enter)" if inside is None else f"{inside // 60:3d} min"
    outside = poi.get("typical_duration_min")
    outside_str = "—" if not isinstance(outside, int) else f"{outside:3d} min"
    tier = poi.get("importance_tier", "?")
    print(f"{prefix}  {poi.get('name', '(unnamed)')[:46]:46s}  tier {tier}")
    print(f"{prefix}      outside {outside_str}    inside {inside_str}")
    basis = str(poi.get("visit_basis", "")).strip()
    print(f"{prefix}      {basis if basis else '(NO BASIS — this number argues nothing)'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--slug", default="paris")
    parser.add_argument("--corridor-factor", type=float, default=DEFAULT_CORRIDOR_FACTOR)
    args = parser.parse_args(argv)

    pois = load_pois(args.slug)
    priced = [p for p in pois if is_priced(p)]
    with_inside = [p for p in priced if inside_seconds(p) is not None]
    without_inside = [p for p in priced if inside_seconds(p) is None]

    print("=" * 78)
    print(f"VISIT CAPACITY AUDIT — {args.slug}")
    print("=" * 78)
    print(f"  {len(pois)} POIs in the corpus")
    print(f"  {len(priced)} priced by the capacity pass")
    print(f"  {len(with_inside)} have an interior; {len(without_inside)} have nothing to enter")

    if not priced:
        print()
        print("  ✗ NOTHING IS PRICED YET. The capacity pass has not run over this city.")
        print("    Run it first:  make poi-visit-duration SLUG=" + args.slug)
        print("    Until then this report has nothing to audit and no check below can be made.")
        return 1

    ranked = sorted(with_inside, key=lambda p: inside_seconds(p) or 0, reverse=True)

    print()
    print("-" * 78)
    print(f"THE {EXTREME_ROWS} MOST ABSORBING INTERIORS")
    print("  CHECK: is this a list of ABSORBING places, or a list of FAMOUS ones?")
    print("  If the tiers below are all 4s and 5s, importance leaked into the number.")
    print("-" * 78)
    for poi in ranked[:EXTREME_ROWS]:
        print_row(poi)

    print()
    print("-" * 78)
    print(f"THE {EXTREME_ROWS} LEAST ABSORBING INTERIORS")
    print("  CHECK: these should be places with a small or incidental inside, not")
    print("  places that simply have no inside at all — those are in the next section.")
    print("-" * 78)
    for poi in reversed(ranked[-EXTREME_ROWS:]):
        print_row(poi)

    print()
    print("-" * 78)
    print(f"NOTHING TO ENTER — {len(without_inside)} places")
    print("  CHECK: streets, bridges, squares and gardens belong here. A museum or a")
    print("  church appearing in this list is a miss by the capacity pass.")
    print("-" * 78)
    for poi in without_inside:
        outside = poi.get("typical_duration_min")
        outside_str = "—" if not isinstance(outside, int) else f"{outside:3d} min"
        print(f"  {str(poi.get('name', ''))[:46]:46s}  outside {outside_str}")

    members = corridor_members(pois, args.corridor_factor)
    direct_m = haversine_m(*RUE_ROYALE, *NOTRE_DAME)
    corridor_priced = [poi for _, poi in members if is_priced(poi)]
    total_inside_s = sum(inside_seconds(p) or 0 for p in corridor_priced)
    total_outside_s = sum(
        (p.get("typical_duration_min") or 0) * 60
        for p in corridor_priced
        if inside_seconds(p) is None
    )

    print()
    print("=" * 78)
    print("THE RUE ROYALE → NOTRE-DAME CORRIDOR")
    print(f"  A = {RUE_ROYALE}   B = {NOTRE_DAME}   straight line {direct_m:.0f} m")
    print(f"  Ellipse: A->place->B costs at most {args.corridor_factor}x the direct line")
    print(f"  {len(members)} places on the corridor, {len(corridor_priced)} of them priced")
    print("-" * 78)
    print("  THE ONE QUESTION THIS SECTION ANSWERS:")
    print("  do these interiors carry enough time between them to fill ~2.5 hours?")
    print("-" * 78)
    for from_a, poi in members:
        print_row(poi, prefix=f"[{from_a:5.0f}m]")

    print()
    print("-" * 78)
    print("  CORRIDOR CAPACITY, SUMMED:")
    print(f"    interiors:            {total_inside_s:6d} s  ({total_inside_s / 60:.0f} min)")
    print(f"    exteriors-only:       {total_outside_s:6d} s  ({total_outside_s / 60:.0f} min)")
    combined = total_inside_s + total_outside_s
    print(f"    combined:             {combined:6d} s  ({combined / 60:.0f} min)")
    print()
    print("    Roughly 2.5 hours is 9000 s. This total is NOT a planner prediction —")
    print("    no tour visits every place on a corridor. It is a ceiling: if this")
    print("    number is not comfortably above 9000 s, the capacity pass was too")
    print("    conservative and the planner will refuse exactly as today's code does.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
