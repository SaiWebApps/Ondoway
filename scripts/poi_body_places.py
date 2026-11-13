"""Collect a city's body places — public toilets and benches — from OpenStreetMap.

WHY A SECOND OVERPASS FETCH AND NOT A FLAG ON THE OPENING-HOURS PASS. One script
answers one question (`scripts/poi_opening_hours.py`'s own header records the
precedent). That pass matches NAMED tourist elements to the narrated POIs already
in ``poi-raw.json`` and hands the result to a model for an audited judgement.
This pass asks a different question of the same API: it collects UNNAMED amenity
nodes (``amenity=toilets``, ``amenity=bench`` / ``leisure=bench``), matches them
to nothing, calls NO model ($0 end to end), and writes a SEPARATE file. What IS
shared is imported rather than copied: ``OVERPASS_URL`` (the one endpoint
constant) comes from the opening-hours pass, and ``assert_ingestable`` /
``ONBOARD_USER_AGENT`` come from ``src/onboard/fetch.py`` — the guarded-door,
descriptive-User-Agent pattern (Overpass 406s an anonymous client). The
one-retry-then-abort rule is the same as
``poi_opening_hours.fetch_osm_hours``: Overpass unreachable after 2 attempts
aborts nonzero, never a silent pass, because a body-places file that silently
came out empty would read as "this city has no toilets".

WHERE THE RESULTS GO, AND WHERE THEY MUST NEVER GO. Records land in
``data/{slug}/body-places.json`` — NEVER in ``poi-raw.json``. Body places are
not narrated places: they carry no beats, no tier judgement, no story, and must
never enter the beat pipeline or the dedup/review flows that read the POI file.
The upload half reads this file
and uploads each record with ``poi_role="body"``; the planner prices the "body"
role at 0.0 in ``POI_ROLE_MULTIPLIER``, so a body place can never seat as a
narrated stop — only the cadence axis may ever schedule one.

WHY THE DATA EXISTS AT ALL: body stops are first-class promises. A rest window
with no bench under it means standing. A family's toilet stop is real minutes
with no story in it, and it cannot be skipped. A slow walker's bench IS a stop,
with a location and a duration.

RECORD SHAPE, one object per OSM node, sorted by (kind, osm id) for stable
diffs::

    {
      "id": "body-toilet-<osmid>" | "body-bench-<osmid>",   # stable across runs
      "kind": "toilet" | "bench",
      "lat": <float>,
      "lng": <float>,
      "poi_role": "body"
    }

USAGE. ``--slug`` picks the city (default paris; the bbox comes from
``src.city_registry.bbox_map()``, the one bbox table). ``--limit N`` is the dry
run: it prints the first N records and WRITES NOTHING. Exits nonzero when the
fetch fails.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from scripts.poi_opening_hours import OVERPASS_URL

ROOT = Path(__file__).resolve().parent.parent

#: Nodes only, on purpose: a toilets/bench WAY (a mapped building outline or a
#: long shared bench) is rare and its centre adds nothing a node does not, while
#: ``out center`` on nwr would roughly double the payload for a city of benches.
OVERPASS_QUERY = """
[out:json][timeout:120];
(
  node["amenity"="toilets"]({s},{w},{n},{e});
  node["amenity"="bench"]({s},{w},{n},{e});
  node["leisure"="bench"]({s},{w},{n},{e});
);
out;
"""


def fetch_body_places(bbox: tuple[float, float, float, float]) -> list[dict[str, Any]]:
    """One bulk Overpass pull -> the sorted record list documented in the header.

    ``bbox`` is the registry's (min_lat, max_lat, min_lon, max_lon); Overpass
    wants (south, west, north, east). One retry, then abort — an empty file
    born of a network failure would read as "this city has no toilets".
    """
    from src.onboard.fetch import ONBOARD_USER_AGENT, assert_ingestable

    assert_ingestable(OVERPASS_URL)
    import httpx

    query = OVERPASS_QUERY.format(s=bbox[0], w=bbox[2], n=bbox[1], e=bbox[3])
    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            # Overpass 406s an anonymous client; the descriptive agent is the
            # same one the guarded ingest door sends, for the same reason.
            resp = httpx.post(
                OVERPASS_URL,
                data={"data": query},
                timeout=180,
                headers={"User-Agent": ONBOARD_USER_AGENT},
            )
            resp.raise_for_status()
            payload = resp.json()
            break
        except Exception as exc:  # reported verbatim below; one retry then abort
            last_error = exc
            print(f"  Overpass attempt {attempt} failed: {exc}", file=sys.stderr)
    else:
        raise SystemExit(
            f"✗ Overpass unreachable after 2 attempts ({last_error}); refusing to write "
            "a body-places file that would silently read as 'this city has no toilets'."
        )

    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for el in payload.get("elements", []):
        tags = el.get("tags", {})
        lat, lng = el.get("lat"), el.get("lon")
        if lat is None or lng is None:
            continue
        # Toilet wins a (pathological) node tagged as both: a place to plan
        # around outranks a place to sit, and the order keeps the id stable.
        if tags.get("amenity") == "toilets":
            kind = "toilet"
        elif tags.get("amenity") == "bench" or tags.get("leisure") == "bench":
            kind = "bench"
        else:
            continue
        record_id = f"body-{kind}-{el['id']}"
        if record_id in seen:
            continue
        seen.add(record_id)
        records.append(
            {
                "id": record_id,
                "kind": kind,
                "lat": float(lat),
                "lng": float(lng),
                "poi_role": "body",
            }
        )
    records.sort(key=lambda r: (r["kind"], int(r["id"].rsplit("-", 1)[1])))
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--slug", default="paris", help="City slug (default: paris)")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Print only the first N records and write NOTHING. The dry-run knob.",
    )
    args = parser.parse_args(argv)

    from src import city_registry

    boxes = city_registry.bbox_map()
    if args.slug not in boxes:
        raise SystemExit(f"✗ unknown city slug {args.slug!r}; registered: {sorted(boxes)}")

    print(
        f"  fetching toilets and benches for {args.slug} (one Overpass query) …",
        flush=True,
    )
    records = fetch_body_places(boxes[args.slug])
    toilets = sum(1 for r in records if r["kind"] == "toilet")
    print(f"  {len(records)} body places ({toilets} toilets, {len(records) - toilets} benches).")

    if args.limit is not None:
        for record in records[: args.limit]:
            print(f"  {json.dumps(record, ensure_ascii=False)}")
        print(f"\nDry run (--limit {args.limit}). Nothing written.")
        return 0

    path = ROOT / "data" / args.slug / "body-places.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n")
    print(f"✓ wrote {len(records)} body places to {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
