"""Pre-compute the Paris tourability map.

Outputs two artifacts under ``data/{city_slug}/``:

- ``tourability_map.json`` — per-grid-cell density assessment for every
  (duration, round_trip) bucket. The schema:

  ``{"generated_at": ..., "grid_resolution_m": 100, "duration_buckets":
  [60,90,120,180], "round_trip_modes": [true,false], "cells": [
  {"lat": 48.8566, "lng": 2.3522,
   "60min_round_trip": {fill_ratio, anchor_candidates,
                       cluster_compactness, status}, …}, …]}``

- ``tourability_summary.md`` — human-readable rollup: status mix per
  duration bucket, best-of-GREEN start points, the most-likely-asked
  thin tier-5 anchors, and a per-Area % GREEN breakdown.

This is a one-shot diagnostic. The runtime computes density per-request
in ``src/tour/density.py``; the map is for launch-readiness analysis,
product UX (heatmaps), and ad-hoc "where can I start a tour?" lookups.

Runtime: ~10k cells × 4 durations × 2 modes ≈ 80k assessments. Each
assessment is O(n_POIs + n_anchor_candidates²). On the Paris corpus
(303 POIs) this completes in under 5 minutes single-threaded.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import UTC, datetime
from pathlib import Path

from src.connection import create_driver
from src.tour.contract import TourInput
from src.tour.density import assess_snapshot
from src.tour.routing import haversine_m
from src.tour.selection import load_paris_corpus

DURATION_BUCKETS: tuple[int, ...] = (60, 90, 120, 180)
ROUND_TRIP_MODES: tuple[bool, ...] = (True, False)
DEFAULT_GRID_RESOLUTION_M: int = 100


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--city-slug", default="paris")
    parser.add_argument(
        "--grid-resolution-m",
        type=int,
        default=DEFAULT_GRID_RESOLUTION_M,
        help="Grid cell edge length in metres (default 100).",
    )
    parser.add_argument(
        "--margin-m",
        type=int,
        default=200,
        help="Extra margin (m) around the POI bounding box.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Override output dir (default: data/{city_slug}/).",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    out_dir = (
        Path(args.output_dir)
        if args.output_dir
        else project_root / "data" / args.city_slug
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    driver = create_driver()
    try:
        snapshot = load_paris_corpus(driver, city_slug=args.city_slug)
    finally:
        driver.close()

    if not snapshot.pois:
        print(f"\u2717 Empty POI corpus for {args.city_slug!r}; nothing to map.")
        return 1

    bbox = _compute_bbox(snapshot.pois, margin_m=args.margin_m)
    grid_lats, grid_lngs = _build_grid(bbox, resolution_m=args.grid_resolution_m)
    print(
        f"Paris bbox: lat [{bbox['min_lat']:.5f}, {bbox['max_lat']:.5f}], "
        f"lng [{bbox['min_lng']:.5f}, {bbox['max_lng']:.5f}]; "
        f"grid {len(grid_lats)} × {len(grid_lngs)} = "
        f"{len(grid_lats) * len(grid_lngs)} cells."
    )

    cells: list[dict] = []
    t0 = time.perf_counter()
    total_buckets = len(DURATION_BUCKETS) * len(ROUND_TRIP_MODES)
    n_cells = len(grid_lats) * len(grid_lngs)
    progress_step = max(1, n_cells // 20)
    cell_idx = 0

    for lat in grid_lats:
        for lng in grid_lngs:
            cell_record: dict = {"lat": round(lat, 6), "lng": round(lng, 6)}
            for duration_min in DURATION_BUCKETS:
                for round_trip in ROUND_TRIP_MODES:
                    inp = TourInput(
                        start=(lat, lng),
                        duration_min=duration_min,
                        city_slug=args.city_slug,
                        round_trip=round_trip,
                    )
                    a = assess_snapshot(inp, snapshot)
                    key = _bucket_key(duration_min, round_trip)
                    cell_record[key] = {
                        "fill_ratio": round(a.fill_ratio, 3),
                        "anchor_candidates": a.anchor_candidate_count,
                        "cluster_compactness": round(a.cluster_compactness, 3),
                        "status": a.status,
                    }
            cells.append(cell_record)
            cell_idx += 1
            if cell_idx % progress_step == 0:
                pct = 100 * cell_idx / n_cells
                elapsed = time.perf_counter() - t0
                rate = cell_idx / max(0.001, elapsed)
                eta = (n_cells - cell_idx) / rate
                print(f"  {cell_idx}/{n_cells} cells ({pct:.0f}%); eta {eta:.0f}s")

    elapsed = time.perf_counter() - t0
    print(f"Done. {n_cells} cells × {total_buckets} buckets in {elapsed:.1f}s.")

    map_path = out_dir / "tourability_map.json"
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "city_slug": args.city_slug,
        "grid_resolution_m": args.grid_resolution_m,
        "bounding_box": bbox,
        "duration_buckets": list(DURATION_BUCKETS),
        "round_trip_modes": list(ROUND_TRIP_MODES),
        "cells": cells,
    }
    map_path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"\u2713 wrote {map_path} ({_size_kb(map_path)} KB)")

    summary_path = out_dir / "tourability_summary.md"
    summary_md = _build_summary_markdown(payload, snapshot)
    summary_path.write_text(summary_md)
    print(f"\u2713 wrote {summary_path}")
    return 0


# ---------------------------------------------------------------------------
# Grid construction
# ---------------------------------------------------------------------------


def _compute_bbox(pois, *, margin_m: int) -> dict:
    """Bounding box from POI lat/lng with a buffer on each edge (in metres)."""
    min_lat = min(p.lat for p in pois)
    max_lat = max(p.lat for p in pois)
    min_lng = min(p.lng for p in pois)
    max_lng = max(p.lng for p in pois)

    # Convert margin metres → degrees at this latitude.
    mid_lat = (min_lat + max_lat) / 2.0
    deg_per_m_lat = 1.0 / 111_000.0
    deg_per_m_lng = 1.0 / (111_000.0 * math.cos(math.radians(mid_lat)))
    return {
        "min_lat": min_lat - margin_m * deg_per_m_lat,
        "max_lat": max_lat + margin_m * deg_per_m_lat,
        "min_lng": min_lng - margin_m * deg_per_m_lng,
        "max_lng": max_lng + margin_m * deg_per_m_lng,
    }


def _build_grid(bbox: dict, *, resolution_m: int) -> tuple[list[float], list[float]]:
    mid_lat = (bbox["min_lat"] + bbox["max_lat"]) / 2.0
    deg_per_m_lat = 1.0 / 111_000.0
    deg_per_m_lng = 1.0 / (111_000.0 * math.cos(math.radians(mid_lat)))
    lat_step = resolution_m * deg_per_m_lat
    lng_step = resolution_m * deg_per_m_lng

    lats: list[float] = []
    lat = bbox["min_lat"]
    while lat <= bbox["max_lat"] + 1e-9:
        lats.append(lat)
        lat += lat_step
    lngs: list[float] = []
    lng = bbox["min_lng"]
    while lng <= bbox["max_lng"] + 1e-9:
        lngs.append(lng)
        lng += lng_step
    return lats, lngs


def _bucket_key(duration_min: int, round_trip: bool) -> str:
    return f"{duration_min}min_{'round_trip' if round_trip else 'one_way'}"


def _size_kb(p: Path) -> int:
    return p.stat().st_size // 1024


# ---------------------------------------------------------------------------
# Summary markdown
# ---------------------------------------------------------------------------


def _build_summary_markdown(payload: dict, snapshot) -> str:
    lines: list[str] = []
    cells = payload["cells"]
    n_cells = len(cells)

    lines.append("# Paris Tourability Map — Summary")
    lines.append("")
    lines.append(f"**Generated:** {payload['generated_at']}")
    lines.append(f"**City:** {payload['city_slug']}")
    lines.append(f"**Grid resolution:** {payload['grid_resolution_m']}m")
    lines.append(f"**Cells:** {n_cells}")
    lines.append("")
    bbox = payload["bounding_box"]
    lines.append(
        f"**Bounding box:** lat [{bbox['min_lat']:.5f}, {bbox['max_lat']:.5f}], "
        f"lng [{bbox['min_lng']:.5f}, {bbox['max_lng']:.5f}]"
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Status mix per duration bucket")
    lines.append("")
    lines.append("| Bucket | GREEN | YELLOW | RED |")
    lines.append("|---|---|---|---|")
    for duration in payload["duration_buckets"]:
        for round_trip in payload["round_trip_modes"]:
            key = _bucket_key(duration, round_trip)
            counts = {"GREEN": 0, "YELLOW": 0, "RED": 0}
            for c in cells:
                counts[c[key]["status"]] += 1
            mode = "round-trip" if round_trip else "one-way"
            lines.append(
                f"| {duration}min {mode} "
                f"| {counts['GREEN']} ({100*counts['GREEN']/n_cells:.1f}%) "
                f"| {counts['YELLOW']} ({100*counts['YELLOW']/n_cells:.1f}%) "
                f"| {counts['RED']} ({100*counts['RED']/n_cells:.1f}%) |"
            )
    lines.append("")
    lines.append("---")
    lines.append("")

    # Top-10 GREEN starts at 60min round-trip (the most common bucket).
    lines.append("## Top 10 highest-density GREEN starts (60min round-trip)")
    lines.append("")
    bucket_60rt = "60min_round_trip"
    green_60rt = [
        c
        for c in cells
        if c[bucket_60rt]["status"] == "GREEN"
    ]
    green_60rt.sort(key=lambda c: -c[bucket_60rt]["fill_ratio"])
    nearest_poi = _build_nearest_poi_lookup(snapshot)
    if not green_60rt:
        lines.append("(no GREEN cells at this bucket — corpus is too thin for 60min round-trips)")
    else:
        lines.append("| Rank | Lat,Lng | Nearest POI | Fill | Anchors | Compactness |")
        lines.append("|---|---|---|---|---|---|")
        for rank, c in enumerate(green_60rt[:10], start=1):
            cell = c[bucket_60rt]
            poi = nearest_poi(c["lat"], c["lng"])
            lines.append(
                f"| {rank} | {c['lat']:.5f}, {c['lng']:.5f} | {poi} "
                f"| {cell['fill_ratio']:.2f} | {cell['anchor_candidates']} "
                f"| {cell['cluster_compactness']:.2f} |"
            )
    lines.append("")
    lines.append("---")
    lines.append("")

    # Most-likely-asked thin tier-5 anchors that fall YELLOW or RED.
    lines.append("## 10 most-likely-asked thin tier-5 starts")
    lines.append("")
    lines.append(
        "Tier-5 POIs whose own cell falls YELLOW or RED at 60min round-trip — "
        "expected user starting points where the gate will refuse or warn."
    )
    lines.append("")
    thin_tier5 = _thin_tier5_starts(snapshot, cells)
    if not thin_tier5:
        lines.append("(no thin tier-5 anchors at this bucket)")
    else:
        lines.append(
            "| POI | 60min RT | 60min one-way | 90min RT | 90min one-way | "
            "Recommendation |"
        )
        lines.append("|---|---|---|---|---|---|")
        for row in thin_tier5[:10]:
            lines.append(
                f"| {row['name']} | {row['60min_round_trip']} "
                f"| {row['60min_one_way']} | {row['90min_round_trip']} "
                f"| {row['90min_one_way']} | {row['recommendation']} |"
            )
    lines.append("")
    lines.append("---")
    lines.append("")

    # Neighborhood-level rollup: % GREEN per Area at 60min round-trip.
    lines.append("## Neighborhood-level rollup (60min round-trip)")
    lines.append("")
    lines.append(
        "Cells assigned to the nearest POI's (most-specific) Area; % GREEN "
        "within each Area. Useful but not the canonical gate."
    )
    lines.append("")
    area_rollup = _area_rollup(snapshot, cells, bucket_60rt)
    lines.append("| Area | Cells | % GREEN | % YELLOW | % RED |")
    lines.append("|---|---|---|---|---|")
    for row in area_rollup:
        if row["cells"] == 0:
            continue
        lines.append(
            f"| {row['area']} | {row['cells']} | "
            f"{row['green_pct']:.0f}% | {row['yellow_pct']:.0f}% | {row['red_pct']:.0f}% |"
        )

    return "\n".join(lines).rstrip() + "\n"


def _build_nearest_poi_lookup(snapshot):
    pois = list(snapshot.pois)

    def nearest(lat: float, lng: float) -> str:
        best = None
        best_d = float("inf")
        for p in pois:
            d = haversine_m(lat, lng, p.lat, p.lng)
            if d < best_d:
                best_d = d
                best = p
        return best.name if best else "?"

    return nearest


def _thin_tier5_starts(snapshot, cells):
    """Tier-5 POIs whose own cell is YELLOW or RED at 60min round-trip."""
    # Build a fast cell lookup keyed by rounded coords.
    cell_index: dict[tuple[float, float], dict] = {
        (c["lat"], c["lng"]): c for c in cells
    }

    def _nearest_cell(lat: float, lng: float) -> dict | None:
        best = None
        best_d = float("inf")
        for c in cells:
            d_lat = c["lat"] - lat
            d_lng = c["lng"] - lng
            d2 = d_lat * d_lat + d_lng * d_lng
            if d2 < best_d:
                best_d = d2
                best = c
        return best

    rows: list[dict] = []
    for poi in snapshot.pois:
        if poi.tier != 5:
            continue
        cell = _nearest_cell(poi.lat, poi.lng)
        if cell is None:
            continue
        statuses = {
            key.replace("min_", "min_"): cell[key]["status"]
            for key in cell
            if key not in ("lat", "lng")
        }
        # Only flag if 60min round-trip is YELLOW or RED.
        rt60 = cell["60min_round_trip"]["status"]
        if rt60 == "GREEN":
            continue
        recommendation = _build_recommendation(cell)
        rows.append(
            {
                "name": poi.name,
                "60min_round_trip": cell["60min_round_trip"]["status"],
                "60min_one_way": cell["60min_one_way"]["status"],
                "90min_round_trip": cell["90min_round_trip"]["status"],
                "90min_one_way": cell["90min_one_way"]["status"],
                "recommendation": recommendation,
                "fill_60rt": cell["60min_round_trip"]["fill_ratio"],
            }
        )
    rows.sort(key=lambda r: r["fill_60rt"])
    return rows


def _build_recommendation(cell: dict) -> str:
    """Best alternative bucket for a thin start (the cheapest GREEN escape)."""
    options = [
        ("60min_one_way", "60min one-way"),
        ("90min_one_way", "90min one-way"),
        ("120min_one_way", "120min one-way"),
        ("180min_one_way", "180min one-way"),
        ("90min_round_trip", "90min round-trip"),
        ("120min_round_trip", "120min round-trip"),
        ("180min_round_trip", "180min round-trip"),
    ]
    for key, label in options:
        if cell[key]["status"] == "GREEN":
            return f"Try {label}"
    return "All buckets thin — sparse start"


def _area_rollup(snapshot, cells, bucket_key: str):
    """Assign each cell to the nearest non-city Area; tally GREEN/YELLOW/RED."""
    # Build (poi → Areas) and (Area → area_type) lookups.
    poi_list = list(snapshot.pois)

    def nearest_area_for(lat: float, lng: float) -> str | None:
        # Pick the closest POI; return the most-specific non-city Area
        # of that POI per the SPINE_AREA_TYPE_PRIORITY.
        if not poi_list:
            return None
        nearest = min(
            poi_list, key=lambda p: haversine_m(lat, lng, p.lat, p.lng)
        )
        priority = ("neighborhood", "island", "corridor", "district", "city")
        ranked = sorted(
            (a for a in nearest.areas),
            key=lambda a: priority.index(snapshot.area_types.get(a, ""))
            if snapshot.area_types.get(a, "") in priority
            else len(priority),
        )
        for a in ranked:
            if snapshot.area_types.get(a, "") != "city":
                return a
        return None

    bucket: dict[str, dict[str, int]] = {}
    for c in cells:
        area = nearest_area_for(c["lat"], c["lng"])
        if area is None:
            continue
        bucket.setdefault(area, {"GREEN": 0, "YELLOW": 0, "RED": 0})
        bucket[area][c[bucket_key]["status"]] += 1

    rows: list[dict] = []
    for area, counts in bucket.items():
        total = counts["GREEN"] + counts["YELLOW"] + counts["RED"]
        if total == 0:
            continue
        rows.append(
            {
                "area": area,
                "cells": total,
                "green_pct": 100 * counts["GREEN"] / total,
                "yellow_pct": 100 * counts["YELLOW"] / total,
                "red_pct": 100 * counts["RED"] / total,
            }
        )
    rows.sort(key=lambda r: -r["green_pct"])
    return rows


if __name__ == "__main__":
    raise SystemExit(main())
