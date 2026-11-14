"""Size each POI's FOOTPRINT — `trigger_radius` — by place kind, with the marquees reviewed
by name. Deterministic, $0, re-runnable at will.

WHY. `trigger_radius` is how far from the pin a walker is AT the place: the audio placement
rule (src/tour/placement.py) puts it on the wire and the phone's one predicate reads it. The
Paris corpus carried the field on every record since the first upload and nothing read it;
177 of 370 records sat at the uploader's 10 m default — the Louvre, the
Orsay, Notre-Dame, the Pont Neuf, Sainte-Chapelle, Parc de la Villette among them — so the
phone drew one doorway for a 140 m square and a doorway alike. The rule: "at the stop"
is the place's own footprint: the Place des Vosges is 140 m across, and a walker stands at
Concorde in three different spots.

WHAT IT WRITES, per POI in ``data/{slug}/poi-raw.json``:

- A record still at the uploader's default (``UPLOADER_DEFAULT_M``) — the mark of a record
  nobody sized — takes the footprint of its PLACE KIND from the one table
  (``scripts/geocode_pois.py::FOOTPRINT_BY_KIND``, reached through the one function
  ``trigger_radius_for`` — EXTENDED, never copied).
- A record named in ``REVIEWED`` takes the reviewed footprint, whatever it carried: the
  marquees and every stop of the eleven sample visitor days, each with the
  sentence that argues it, written so someone who has never been to Paris can judge it.
- Every other record keeps its curated value (the area-radius fix of 2026-06-16, the
  geocoder's curated streets and squares) — this pass never lowers a reviewed-elsewhere
  number and never touches a record it has no reason for.
- The argument lands in ``_pipeline.trigger_radius_basis`` on the record, the audit trail
  (the ``geocode_audit.trigger_radius_reasoning`` precedent of the New York pass).

The write path (``load_pois`` / ``dump_pois``, the refuse-to-write-on-reformat guard) is
imported from the capacity pass, never copied. Then, AND MANDATORY: ``make sync-poi-exports``
— a field written here does not reach the export chunks, or the graph the API upload
builds, until that sync runs; `tests/test_export_consistency.py` now pins the two equal.

CARRIED (not this pass): a line for a street, a polygon for a cemetery or a garden's walls;
the corpus holds no such geometry and the rule's ``kind`` field is where it lands.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from scripts.geocode_pois import FOOTPRINT_BY_KIND, trigger_radius_for
from scripts.poi_visit_duration import dump_pois, load_pois

ROOT = Path(__file__).resolve().parent.parent

#: The value `scripts/upload_paris.py` writes when a record carries none — a record still
#: at it was never sized.
UPLOADER_DEFAULT_M: int = 10

#: Cities whose corpus has been through THIS pass — the declaring act, read by the two
#: guards (tests/test_poi_trigger_radius.py, tests/test_export_consistency.py). Adding a
#: slug says "this city's footprints are sized and its export chunks carry them";
#: removing one to reach green is forbidden. New York is not here yet — but the reason
#: has changed: its normalise-then-sync job HAS run. Its poi-raw.json and
#: 15 of its 34 export chunks had been committed at 1-SPACE indent (not 4, as this
#: comment used to say), which `dump_pois` refuses to reformat; all of them are
#: byte-faithful and fully synced now, and `tests/test_export_consistency.py` pins it.
#: What still holds New York out is only that this pass has not been run over it.
CITIES_WITH_FOOTPRINTS: tuple[str, ...] = ("paris",)

#: THE REVIEWED FOOTPRINTS, by the corpus name, each with its argument. The marquees, the
#: places a review named, and every stop of the eleven sample visitor days.
#: Metres from the corpus pin.
REVIEWED: dict[str, tuple[int, str]] = {
    # The "unreviewed footprint" CLASS: a value that
    # was neither the uploader's default nor on this list was never looked at. The two
    # instances a review named, reviewed now; the class rule from here on: a stop the
    # visit GOES INSIDE must have a footprint that covers its BUILDING, or leaving the
    # circle happens indoors and the next leg piece can start under the roof.
    "Jardin des Tuileries": (
        430,
        "The garden runs about 900 m gate to gate and the pin sits mid-garden: at the "
        "old 100 the Concorde gate stood 325 m outside the circle, so entering there "
        "played nothing for a five-minute walk. 430 reaches both "
        "terraces AND the Jardin du Carrousel at the east end — the spot the corpus "
        "voices, 415 m from the pin, whose reviewed anchor says 'right here' there; "
        "the gates themselves are the carried polygon row.",
    ),
    "La Samaritaine": (
        60,
        "A four-building block about 100 m deep between rue de Rivoli and the quay: "
        "at the old 20 the circle sat inside the store, so the piece could only start "
        "indoors and leaving the circle happened "
        "under the roof. 60 from the pin covers the street doors on "
        "all sides.",
    ),
    "Place des Vosges": (
        70,
        "A 140 m square: the arcade runs seventy metres from the central statue, and "
        "stepping under the arcade is being there. One sample day starts, "
        "stops and finishes here, and the ten metres between this 70 and the 60 m "
        "own-place radius are NOT what starts the walker's clock during the fifteen "
        "minutes the walker stands in the square before moving: the own-place circle is "
        "drawn round the walker's FIRST FIX and this one round the pin, so on a square "
        "this wide a walker can leave the first while still standing inside the second. "
        "70 kept; where the clock's circle is centred is the carried item, not the "
        "footprint.",
    ),
    "Notre-Dame Cathedral": (
        100,
        "The pin sits in the building; the parvis where a walker stands runs about "
        "100 m west of the facade, so the footprint reaches it "
        "(the pin itself stays where the corpus put it — a carried data row).",
    ),
    "Louvre Museum": (
        120,
        "The Cour Napoleon around the pyramid is 150 m across and the Cour Carree "
        "another 120 m east of it: a walker at the Louvre is anywhere in those courts.",
    ),
    "Musee d'Orsay": (
        60,
        "The forecourt between the river quay and the door is about 60 m deep; "
        "a bench sits on it.",
    ),
    "Palais-Royal": (
        300,
        "The garden runs 275 m by 100 m behind the Cour d'Honneur, and the pin sits at "
        "the palace forecourt — so the far Beaujolais gallery is 295 m out. 300 covers "
        "the whole walk-in complex (the Tuileries precedent); the reviewed anchors, not "
        "the footprint edge, are what say 'right here' inside it.",
    ),
    "Parc Monceau": (
        380,
        "The park spans from the west-side pin to the Musée Cernuschi's east-edge gate "
        "(the corpus's own record calibrates it, ~630 m); 380 reaches the Courcelles "
        "rotunda on the north edge, where a person stands at the gate the corpus seats "
        "a bench inside — the reviewed anchor, not the footprint edge, says 'right here'.",
    ),
    "Place de la Concorde": (
        120,
        "The square is 360 m by 210 m; a visitor's three positions on it span the middle.",
    ),
    "Pont Neuf": (
        120,
        "The bridge is 238 m long across both arms of the river: its half-length from "
        "the Vert-Galant point is 120 m.",
    ),
    "Square du Vert-Galant": (
        50,
        "The triangular pocket-park at the island's western tip, seven metres below the "
        "street: the pin sits mid-square and fifty metres reaches both the staircase "
        "down from the bridge and the point itself. Its circle lies WHOLLY inside the "
        "Pont Neuf's 120 m one — the pins are 59 m apart and the bridge passes overhead "
        "— so a walker on the point is in both footprints at once. They are at the "
        "SQUARE: the smaller circle is the more specific place, and the bridge's piece "
        "belongs to the people on the deck above. Pont Neuf's 120 is derived from its "
        "238 m span and stands.",
    ),
    "Place Dauphine": (
        60,
        "A triangular place 120 m long from its apex at the Pont Neuf to the Palais de "
        "Justice front, with the pin in the middle: at the old 30 the circle held the "
        "central gravel only, and two visitors spend their first twelve minutes over "
        "café menus at the apex end, outside it. Sixty metres is the place's "
        "half-length, and is also the own-place radius the wire gives a start square "
        "(placement.py), so the square they set out from is ONE circle whichever of the "
        "two is read. It overlaps the Pont Neuf's footprint by geometry, not by error: "
        "the apex IS the bridge's approach, 125 m pin to pin, so stop one's piece arms "
        "over those twelve minutes — the carried edge.",
    ),
    "Pere Lachaise Cemetery": (
        500,
        "Forty-four hectares inside walls; the 2026-06-16 area fix sized it at the "
        "setting cap and the Mur des Fédérés, the columbarium and the memorials are all "
        "inside it — kept (the walls themselves are a carried polygon row). The other "
        "half of the question is answered beside this number rather than in it: three "
        "reviewed ANCHORS ride the record — the Mur des Fédérés first, then Edith "
        "Piaf's grave and Oscar Wilde's tomb — so the 500 m disc says which cemetery "
        "you are in and the anchor says where in it you are standing.",
    ),
    "Conciergerie": (
        40,
        "The quay facade is 100 m long; the entrance on the boulevard du Palais and "
        "the Seine-side view of the towers both count as being there.",
    ),
    "Sainte-Chapelle": (
        30,
        "Inside the Palais de Justice enclosure: the courtyard in front of the chapel "
        "door, reached through the security line — the line is the place.",
    ),
    "Palais de Justice": (
        40,
        "The boulevard du Palais front is 80 m of gilded gates; standing before them "
        "is being at the Palais.",
    ),
    "Bourse de Commerce — Pinault Collection": (
        40,
        "A rotunda 60 m across with its portico and the Medici column on its flank.",
    ),
    "Arc de Triomphe du Carrousel": (
        30,
        "A free-standing arch in the open Place du Carrousel: within thirty metres "
        "you are at it.",
    ),
    "Hotel de Ville": (
        60,
        "The parvis in front of the facade is 80 m deep; a visitor stands on it.",
    ),
    "Musee de l'Orangerie": (
        30,
        "A pavilion at the Tuileries' west end: the door on the terrace.",
    ),
    "Eiffel Tower": (
        80,
        "The four feet span a 125 m square; under the tower is at the tower.",
    ),
    "Arc de Triomphe": (
        60,
        "The arch stands in the Etoile roundabout; the pavement ring and the underpass "
        "mouths are fifty metres out.",
    ),
    "Sacre-Coeur Basilica": (
        50,
        "The parvis and the top of the stairs in front of the facade.",
    ),
    "Pantheon": (
        40,
        "The Place du Pantheon in front of the portico.",
    ),
    "Parc de la Villette": (
        300,
        "Fifty-five hectares of park; sized at a setting's cap, the way the cemeteries "
        "and the Luxembourg were.",
    ),
    "Centre Pompidou": (
        60,
        "The sloping piazza in front of the building is 60 m deep.",
    ),
    "Grand Palais": (
        60,
        "A 240 m nave; the avenue Winston-Churchill front and its steps.",
    ),
    "Petit Palais": (
        40,
        "The entrance portico on the avenue and the garden court behind it.",
    ),
    "Les Invalides": (
        60,
        "The cour d'honneur and the north front; the esplanade beyond is its own walk.",
    ),
    "Opéra Bastille": (
        40,
        "The curved glass front on the Place de la Bastille and its steps.",
    ),
    "Palais Garnier": (
        40,
        "The steps and the Place de l'Opera front.",
    ),
    "Place de la Bastille": (
        80,
        "A 200 m roundabout-square around the July Column.",
    ),
    "Place Vendome": (
        80,
        "224 m by 213 m with the column at its centre.",
    ),
    "Trocadero": (
        80,
        "The esplanade between the two wings is 150 m wide; the view of the tower is "
        "the whole of it.",
    ),
    "Cimetière de Passy": (
        80,
        "A walled cemetery of two hectares behind the Trocadero; the gate.",
    ),
    "Port de l'Arsenal": (
        100,
        "A 500 m marina basin; the quay along it.",
    ),
    "Rue de la Paix": (
        60,
        "A 230 m street of jewellers between the Opera and Vendome: half a block.",
    ),
    "Grands Boulevards": (
        75,
        "A two-kilometre arc of boulevards; 75 m is one block of them.",
    ),
    "Rue Montorgueil": (
        60,
        "A 400 m market street; half a block of it.",
    ),
    "Place de la Bourse": (
        40,
        "The open square in front of the Palais Brongniart's colonnade.",
    ),
    "Passage des Panoramas": (
        60,
        "A passage under glass 133 m long: you are in it from either mouth.",
    ),
    "Place de Louvois": (
        30,
        "A small square around its fountain, opposite the old library.",
    ),
    "Place du Caire": (
        20,
        "A small triangular place in front of the Egyptian facade.",
    ),
}


def size(poi: dict[str, Any]) -> tuple[int, str] | None:
    """The footprint this pass gives one record, with its argument — or None when the
    record keeps what it carries (a curated value nobody reviewed by name)."""
    name = str(poi.get("name", ""))
    if name in REVIEWED:
        return REVIEWED[name]
    if poi.get("trigger_radius", UPLOADER_DEFAULT_M) == UPLOADER_DEFAULT_M:
        return trigger_radius_for(
            name, UPLOADER_DEFAULT_M, place_category=poi.get("place_category") or None
        )
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--slug", default="paris", help="City slug (default: paris)")
    parser.add_argument("--dry-run", action="store_true", help="Print the table and write nothing.")
    args = parser.parse_args(argv)

    path = ROOT / "data" / args.slug / "poi-raw.json"
    if not path.exists():
        raise SystemExit(f"✗ no POI file at {path}")

    pois, original = load_pois(path)
    names = {str(p.get("name", "")) for p in pois}
    unknown = sorted(n for n in REVIEWED if n not in names)
    if unknown:
        raise SystemExit(
            "✗ the reviewed table names records that are not in the corpus "
            f"(renamed? misspelt?): {unknown}"
        )

    rows: list[tuple[str, str, int, int, str]] = []
    by_kind: dict[str, int] = dict.fromkeys(FOOTPRINT_BY_KIND, 0)
    reviewed_hits = 0
    for poi in pois:
        answer = size(poi)
        if answer is None:
            continue
        metres, basis = answer
        before = poi.get("trigger_radius")
        if before == metres and (poi.get("_pipeline") or {}).get("trigger_radius_basis") == basis:
            continue
        name = str(poi.get("name", ""))
        kind = poi.get("place_category") or "other"
        if name in REVIEWED:
            reviewed_hits += 1
        else:
            by_kind[kind] = by_kind.get(kind, 0) + 1
        rows.append((name, kind, before, metres, basis))
        poi["trigger_radius"] = metres
        poi.setdefault("_pipeline", {})["trigger_radius_basis"] = basis

    print(f"{len(pois)} POIs in {path.relative_to(ROOT)}; {len(rows)} footprint(s) to write.")
    print(f"  reviewed by name: {reviewed_hits}; sized by kind from the uploader's default:")
    for kind in FOOTPRINT_BY_KIND:
        if by_kind.get(kind):
            print(f"    {kind:9s} {by_kind[kind]:4d}  -> {FOOTPRINT_BY_KIND[kind]} m")
    for name, kind, before, metres, basis in rows:
        print(f"  {before!s:>4} -> {metres:>4} m  {kind:9s} {name}")
        print(f"                   {basis}")
    if args.dry_run:
        print("\nDry run. Nothing written.")
        return 0
    if not rows:
        print("Nothing to write: every footprint already carries this pass's answer.")
        return 0
    dump_pois(path, pois, original)
    print(f"\n✓ wrote {len(rows)} footprint(s) to {path.relative_to(ROOT)}")
    print(f"  NEXT, AND MANDATORY: make sync-poi-exports SLUG={args.slug}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
