"""Per-stop uncapped-vs-capped emitted audio for a LIVE tour — governor proof.

Deterministic (no LLM/render): loads the live corpus, runs select_route, then
compares build_poi_beat_plans (uncapped) against build_poi_beat_plans_capped
(the audio governor) per stop. Shows exactly which incidental stops the
governor truncates and by how much.
"""

from __future__ import annotations

import argparse

from src.connection import create_driver
from src.tour.contract import TourInput
from src.tour.routing import planned_audio_seconds
from src.tour.selection import (
    GOVERNOR_SHARE_OF_DELIVERED,
    build_poi_beat_plans,
    build_poi_beat_plans_capped,
    load_paris_corpus,
    select_route,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start", required=True, help="'lat,lng'")
    ap.add_argument("--duration", type=int, required=True)
    ap.add_argument("--round-trip", action="store_true")
    ap.add_argument("--city-slug", default="paris")
    a = ap.parse_args()

    lat, lng = (float(x) for x in a.start.split(","))
    driver = create_driver()
    try:
        snap = load_paris_corpus(driver, city_slug=a.city_slug)
    finally:
        driver.close()
    inp = TourInput(
        start=(lat, lng),
        duration_min=a.duration,
        city_slug=a.city_slug,
        round_trip=a.round_trip,
    )
    route = select_route(inp, snap)
    if not route.pois:
        print("(no POIs selected)")
        return 0

    uncapped = build_poi_beat_plans(route, snap, lenses=None)
    capped = build_poi_beat_plans_capped(route, snap, lenses=None, end_is_none=inp.end is None)
    # Mirror build_poi_beat_plans_capped's exempt set: the MARQUEE (highest-tier stop, ties ->
    # highest audio) + the fixed destination / pulled endpoint.
    u_audio = [planned_audio_seconds(p.beats) for p in uncapped]
    exempt = set()
    if route.fixed_end_poi_id is not None:
        exempt.add(route.fixed_end_poi_id)
    if route.pois:
        marquee = max(range(len(route.pois)), key=lambda i: (route.pois[i].tier, u_audio[i]))
        exempt.add(route.pois[marquee].id)
    total_capped = sum(planned_audio_seconds(k.beats) for k, _ in capped)
    ceiling = int(GOVERNOR_SHARE_OF_DELIVERED * total_capped)

    print(f"route: {[p.name for p in route.pois]}")
    print(f"start_anchor={route.start_anchor_poi_id}  fixed_end={route.fixed_end_poi_id}")
    print(f"delivered (capped) total = {total_capped}s = {total_capped / 60:.1f}min")
    print(f"1/3-of-delivered ceiling  = {ceiling}s = {ceiling / 60:.1f}min")
    print(f"{'POI':32} {'uncapped':>9} {'capped':>8} {'overflow':>9} {'exempt':>7}")
    for poi, u, (k, ov) in zip(route.pois, uncapped, capped, strict=True):
        us = planned_audio_seconds(u.beats)
        ks = planned_audio_seconds(k.beats)
        flag = "  <== CAPPED" if ks < us else ""
        print(
            f"{poi.name[:32]:32} {us:8}s {ks:7}s {len(ov):9} "
            f"{str(poi.id in exempt):>7}{flag}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
