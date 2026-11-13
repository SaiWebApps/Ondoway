"""Geocode POIs via OpenStreetMap Nominatim with pedestrian-stop placement.

Default mode geocodes POIs flagged `_pipeline.geocode_needs_verification` (or
missing lat/lng); `--all` re-geocodes everything. Keeps any existing coordinate
as a fallback when Nominatim misses, and stamps a `_pipeline.geocode_audit`
block. Coordinates outside the city bbox are rejected (fallback kept, LOW conf).

Usage: python -m scripts.geocode_pois --slug new_york
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import requests

HEADERS = {"User-Agent": "Ondoway-pipeline/1.0 (serblowa@gmail.com)"}
# NYC five-borough bounding box (lat, lng)
BBOX = (40.47, 40.93, -74.27, -73.69)  # min_lat, max_lat, min_lng, max_lng
VIEWBOX = "-74.27,40.93,-73.69,40.47"  # left,top,right,bottom for Nominatim


def in_bbox(lat: float, lng: float) -> bool:
    return BBOX[0] <= lat <= BBOX[1] and BBOX[2] <= lng <= BBOX[3]


def nominatim(query: str) -> tuple[float, float, str] | None:
    params = {
        "q": query, "format": "json", "limit": "1",
        "viewbox": VIEWBOX, "bounded": "1",
    }
    try:
        r = requests.get("https://nominatim.openstreetmap.org/search",
                         params=params, headers=HEADERS, timeout=15)
        time.sleep(1.1)
        if r.status_code != 200:
            return None
        data = r.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"]), data[0].get("display_name", "")
    except Exception:
        return None
    return None


#: THE one footprint table by PLACE KIND: how far
#: from the pin a walker is AT a place of this kind, in metres, for a record nobody has
#: reviewed by name. Keyed by the closed place-category vocabulary
#: (scripts/poi_place_category.py). A place you are IN (a garden, a bridge, an arcade, a
#: street) is sized to be entered at its edge; a place you stand AT (a door, a fountain, a
#: café) to the pavement in front of it. The marquees are reviewed by name in
#: scripts/poi_trigger_radius.py — never sized from this table.
FOOTPRINT_BY_KIND: dict[str, int] = {
    "arcade": 60,  # a passage under glass 100-130 m long: you are in it from either mouth
    "bridge": 80,  # a Seine bridge is 150-240 m long: its half
    "church": 25,  # the door and the parvis step
    "gallery": 25,  # a department store's doors
    "garden": 60,  # a Paris square-garden is 80-150 m across: its gate
    "market": 40,  # a market hall or a street market's block
    "monument": 25,  # a fountain, a statue, an arch: within a glance
    "museum": 30,  # the forecourt and the door
    "park": 100,  # gates and lawns; the big parks are reviewed by name
    "square": 40,  # an open place; the marquee squares are reviewed by name
    "street": 60,  # a block of it
    "other": 15,  # a building, a café, a hôtel particulier: its door
}


def trigger_radius_for(
    name: str, default: int, *, place_category: str | None = None
) -> tuple[int, str]:
    """The footprint for a record nobody reviewed by name: by PLACE KIND when the record
    carries one (the Paris corpus), else by name token (the New York
    corpus, which has no kinds yet) — the original table, unchanged. Returns (metres,
    the sentence that argues it)."""
    if place_category in FOOTPRINT_BY_KIND:
        return FOOTPRINT_BY_KIND[place_category], f"Sized by place kind: {place_category}"
    n = name.lower()
    if any(w in n for w in ("botanic", "botanical")):
        return 150, "Large garden, multiple approaches"
    if any(w in n for w in (" park", "garden", "cemetery", "meadow", "green")):
        return 120, "Large green space, multiple approaches"
    if "island" in n:
        return 150, "Island-scale POI"
    if any(w in n for w in ("zoo", "yard", "center", "centre", "terminal", "yards")):
        return 60, "Large complex/campus"
    if "square" in n:
        return 40, "Open square"
    if any(w in n for w in (" street", "bridge", "avenue", "boulevard", " row")):
        return 75, "Linear POI"
    building = ("cathedral", "church", "museum", "theater", "theatre", "hall", "library")
    if any(w in n for w in building):
        return 25, "Large public building entrance"
    return default or 15, "Building/entrance default"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--all", action="store_true", help="re-geocode every POI")
    args = ap.parse_args()

    path = Path("data") / args.slug / "poi-raw.json"
    pois = json.loads(path.read_text())
    city = "New York, NY"

    todo = []
    for p in pois:
        pl = p.setdefault("_pipeline", {})
        if args.all or pl.get("geocode_needs_verification") or p.get("latitude") is None:
            todo.append(p)

    print(f"{len(pois)} POIs total; geocoding {len(todo)}")
    hi = med = low = 0
    flagged = []
    for i, p in enumerate(todo, 1):
        name = p["name"]
        pl = p["_pipeline"]
        addr = (pl.get("address") or "").strip()
        parent = pl.get("parent_poi")
        queries = []
        if addr:
            queries.append(f"{addr}, {city}" if "new york" not in addr.lower() else addr)
        if parent:
            queries.append(f"{name}, {parent}, {city}")
        queries.append(f"{name}, {city}")

        hit = None
        for q in queries:
            hit = nominatim(q)
            if hit and in_bbox(hit[0], hit[1]):
                break
            hit = None

        kept_fallback = False
        if hit:
            lat, lng, disp = round(hit[0], 6), round(hit[1], 6), hit[2]
            conf = "MEDIUM"
            med += 1
            src = "OpenStreetMap Nominatim"
        else:
            # keep existing knowledge-based coordinate as fallback
            lat, lng = p.get("latitude"), p.get("longitude")
            if lat is not None:
                lat, lng = round(float(lat), 6), round(float(lng), 6)
            conf = "LOW"
            low += 1
            kept_fallback = True
            disp = "Nominatim miss — kept agent knowledge-based coordinate"
            src = pl.get("geocode_source", "assistant_knowledge")
            flagged.append(name)

        tr, tr_reason = trigger_radius_for(name, p.get("trigger_radius", 15))
        p["latitude"] = lat
        p["longitude"] = lng
        p["trigger_radius"] = tr
        pl["geocode_needs_verification"] = kept_fallback
        pl["geocode_audit"] = {
            "source": src,
            "confidence": conf,
            "placement": f"Nominatim best match for '{name}'" if not kept_fallback
                         else "Knowledge-based fallback (Nominatim miss); verify manually",
            "trigger_radius_reasoning": tr_reason,
            "notes": disp[:200],
        }
        if i % 20 == 0:
            print(f"  {i}/{len(todo)} ...")

    with open(path, "w") as f:
        json.dump(pois, f, indent=2, ensure_ascii=False)
    print(f"\nDONE. confidence: HIGH={hi} MEDIUM={med} LOW={low}")
    if flagged:
        print(f"LOW-confidence (Nominatim miss, fallback kept) — {len(flagged)}:")
        for n in flagged:
            print(f"  - {n}")


if __name__ == "__main__":
    main()
