"""Learn each door's opening hours — data row 6.1 behind /poi-opening-hours.

WHY A SECOND SCRIPT AND NOT A FLAG ON THE CAPACITY PASS. One script answers one
question (`scripts/poi_visit_duration.py`'s own header records the precedent):
that script prices how long a place absorbs a visitor, this one learns WHEN the
place can be entered at all. Different question, different prompt, different
calibration, different failure modes. What IS shared is imported rather than
copied: `load_pois` / `serialise` / `dump_pois` (the refuse-to-write-on-reformat
round-trip guard) and `records_for_batch` (the one reader of a model reply — the
fence strip, the parse and the alignment check) come straight from the capacity
pass, so the two passes can never drift into different file conventions.

WHAT IT PRODUCES, per POI in ``data/{slug}/poi-raw.json`` (Docs/adr/0006):

``gated``                 The door verdict: true when a door, gate or ticket
                          line stands between the street and the experience;
                          false when the whole value stands in the open.
``opening_hours``         OpenStreetMap ``opening_hours`` TEXT, exactly as the
                          map writes it ("Tu-Su 09:30-18:00; Th 09:30-21:45"),
                          read at planning time by the ``opening_hours`` library
                          so seasons, holidays and past-midnight rules survive.
                          ``null`` = no hours held: a place with no door, or a
                          door whose hours nobody holds (its basis says which).
``opening_hours_source``  ``"map"`` when the text is the place's own OpenStreetMap
                          tag, ``"guess"`` when a model wrote it. ``null`` with
                          null hours. The source decides how the hours are
                          SPOKEN; nothing else does.
``opening_hours_basis``   The one-sentence argument, per the house style.

THE MAP COMES FIRST, AND A GUESS NEVER OVERRULES IT. Two Overpass pulls over the
city registry's bbox: the SCOPED classes (tourism, historic, worship, market,
park, garden — the payload stays in the hundreds) matched by name containment
within 150 m, then EVERY named element carrying hours, matched only by an
EQUAL normalised name within 150 m — the wide net needs the stricter match or a
café claims the cathedral next door. A match is stored only for a DOOR, and only
when the library reads it and it carries no quoted comment (a comment reads as
OPEN to the library, so unknown is the honest answer). Every run refreshes the
map rows; the model is asked only about doors the map does not hold, and it
answers in the map's own grammar, validated through the same library.

NETWORK. Overpass is on the ingest allowlist (``src/onboard/fetch.py``); this
script calls ``assert_ingestable`` before fetching and then uses httpx directly,
the ``scripts/geocode_pois.py`` operator-script precedent — the fixture-mode
knob in the guarded door exists so the TEST bar never touches the network, and
this is operator tooling that must.

SPEND. Two Overpass queries and one model call per batch of doors the map does
not hold. ``--limit`` prices a subset and writes nothing; that is the intended
dry run before a full pass.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

from scripts.poi_visit_duration import (
    DEFAULT_MODEL,
    dump_pois,
    load_pois,
    records_for_batch,
)

ROOT = Path(__file__).resolve().parent.parent

#: POIs per model call. Same trade as the capacity pass: one bad batch is cheap
#: to re-run, and a full pass stays a handful of calls.
DEFAULT_BATCH_SIZE: int = 15

#: The only vocabulary a door's hours may carry (CONTEXT.md "Hours source").
SOURCE_MAP = "map"
SOURCE_GUESS = "guess"
HOURS_SOURCES: tuple[str, ...] = (SOURCE_MAP, SOURCE_GUESS)

#: An OSM element may claim a POI only from closer than this. Wider and a
#: café's hours attach to the cathedral next door; narrower and a way's
#: computed centre misses its own forecourt.
OSM_MATCH_RADIUS_M: float = 150.0

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
#: The SCOPED pull: named, tourist-relevant elements carrying hours — the
#: payload stays in the hundreds, and name containment is safe inside it.
OVERPASS_SCOPED_QUERY = """
[out:json][timeout:120];
(
  nwr["opening_hours"]["name"]["tourism"]({s},{w},{n},{e});
  nwr["opening_hours"]["name"]["historic"]({s},{w},{n},{e});
  nwr["opening_hours"]["name"]["amenity"~"^(place_of_worship|marketplace)$"]({s},{w},{n},{e});
  nwr["opening_hours"]["name"]["leisure"~"^(park|garden)$"]({s},{w},{n},{e});
);
out center tags;
"""
#: The WIDE pull: every named element carrying hours (tens of thousands in
#: Paris). Matched by an EQUAL name only — see the header.
OVERPASS_WIDE_QUERY = """
[out:json][timeout:180];
nwr["opening_hours"]["name"]({s},{w},{n},{e});
out center tags;
"""

PROMPT_HEADER = """\
You are recording WHEN each place on a walking tour can be entered — its opening
days and hours — so a planner never routes a visitor to a locked door.

THE ONE DISTINCTION THAT MATTERS: is the place GATED? A place is gated when a
door, gate or ticket line stands between the street and the experience — a
museum, a church you enter, a covered arcade with grilles, a walled garden or
park with closing bells, a market hall. A place is NOT gated when its whole
value stands in the open — a street, a bridge, an open square, a riverbank, a
monument viewed from the pavement. Not-gated places take `opening_hours: null`,
and that null is a real statement, never a shrug.

IF A PLACE IS GATED BUT YOU DO NOT CONFIDENTLY KNOW ITS HOURS: return null AND
say so in the basis ("gated, hours not confidently known; left unfiltered").
A wrong answer locks a visitor out of an open door; null merely leaves the
planner as trusting as it is today. Err toward null.

THE HOURS GRAMMAR, exactly: OpenStreetMap's `opening_hours` text, one string.
- days are Mo Tu We Th Fr Sa Su, ranges with a dash, lists with commas;
- rules are separated by "; " and a day with no hours is "off";
- times are 24-hour HH:MM-HH:MM; a lunch closure is two spans joined by ",";
- examples: "Mo-Su 09:30-23:45", "Tu-Su 10:00-18:00; Mo off",
  "Mo-Fr 08:00-12:00,14:00-19:00; Sa 09:00-12:00; Su off";
- NEVER a quoted comment, never prose, never "sunrise", never a season word
  unless you are certain of the dated rule. Where hours vary by season,
  record the CURRENT typical pattern and say so in the basis.

FOR EACH PLACE RETURN: `name` (copied exactly), `gated` (true or false — the
one distinction above, answered EXPLICITLY for every place, including the ones
whose hours you do not know), `opening_hours` (the string or null),
`opening_hours_basis` (ONE sentence: what kind of place it is and where the
hours came from — the institution's own published pattern — or why it is not
gated. Never "popular" or "usually open").

Return STRICT JSON only: an array, one object per place, same order, no prose,
no markdown fence.

THE PLACES:
"""


def _normalise(name: str) -> str:
    """Casefolded, accent-stripped, alphanumeric words — the matching key."""
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", ascii_only.casefold()).split())


def _names_match(ours: str, theirs: str) -> bool:
    """The SCOPED match: equal, or containment either way — guarded so a
    four-letter fragment ("pont") cannot claim every bridge in the city."""
    a, b = _normalise(ours), _normalise(theirs)
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return len(shorter) >= 5 and shorter in longer


def _names_equal(ours: str, theirs: str) -> bool:
    """The WIDE match: the normalised names are equal, nothing looser. Among
    every shop and café in the city, containment is how the Luxembourg
    Gardens take the Luxembourg Museum's hours."""
    a, b = _normalise(ours), _normalise(theirs)
    return bool(a) and a == b


def fetch_osm_hours(
    bbox: tuple[float, float, float, float], query: str = OVERPASS_SCOPED_QUERY
) -> list[dict[str, Any]]:
    """One bulk Overpass pull: [{name, lat, lng, opening_hours}, ...].

    ``bbox`` is the registry's (min_lat, max_lat, min_lon, max_lon); Overpass
    wants (south, west, north, east). One retry, then abort — silently pricing
    the whole city as guesses because a network call failed would erase the
    better source without anyone seeing it happen.
    """
    from src.onboard.fetch import ONBOARD_USER_AGENT, assert_ingestable

    assert_ingestable(OVERPASS_URL)
    import httpx

    body = query.format(s=bbox[0], w=bbox[2], n=bbox[1], e=bbox[3])
    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            # Overpass 406s an anonymous client; the descriptive agent is the
            # same one the guarded ingest door sends, for the same reason.
            resp = httpx.post(
                OVERPASS_URL,
                data={"data": body},
                timeout=240,
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
            f"✗ Overpass unreachable after 2 attempts ({last_error}); refusing to run "
            "an all-guess pass that silently drops the map half of the source hierarchy."
        )

    elements: list[dict[str, Any]] = []
    for el in payload.get("elements", []):
        tags = el.get("tags", {})
        name, hours = tags.get("name"), tags.get("opening_hours")
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lng = el.get("lon") or (el.get("center") or {}).get("lon")
        if name and hours and lat is not None and lng is not None:
            elements.append(
                {"name": name, "lat": float(lat), "lng": float(lng), "opening_hours": hours}
            )
    return elements


def _nearest_match(
    poi: dict[str, Any], elements: list[dict[str, Any]], names_fit
) -> str | None:
    from src.tour.routing import haversine_m

    lat, lng = poi.get("latitude"), poi.get("longitude")
    if lat is None or lng is None:
        return None
    best: tuple[float, str] | None = None
    for el in elements:
        distance = haversine_m(float(lat), float(lng), el["lat"], el["lng"])
        if distance > OSM_MATCH_RADIUS_M:
            continue
        if not names_fit(poi.get("name", ""), el["name"]):
            continue
        if best is None or distance < best[0]:
            best = (distance, el["opening_hours"])
    return None if best is None else best[1]


def match_osm(
    pois: list[dict[str, Any]],
    scoped: list[dict[str, Any]],
    wide: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    """POI name -> raw OpenStreetMap ``opening_hours`` text.

    The scoped classes first, by containment (today's rule, unchanged); a POI
    they leave unmatched is tried against the wide set by EQUAL name only.
    Nearest wins when several qualify. The scope is the disambiguation: the
    wide set holds every shop and restaurant in the city."""
    matched: dict[str, str] = {}
    for poi in pois:
        tag = _nearest_match(poi, scoped, _names_match)
        if tag is None and wide:
            tag = _nearest_match(poi, wide, _names_equal)
        if tag is not None:
            matched[poi["name"]] = tag
    return matched


def readable_hours(text: object) -> str | None:
    """The text as the map wrote it, when the planner can trust the library
    to read it — else None (unknown, the honest answer). Refused: anything
    that is not a string, a string carrying a quoted comment (the library
    reads "closed for works" as OPEN with a note), and a string the library
    cannot parse."""
    if not isinstance(text, str) or not text.strip():
        return None
    stripped = text.strip()
    if '"' in stripped:
        return None
    from opening_hours import OpeningHours

    try:
        OpeningHours(stripped)
    except Exception:  # the library's own ParserError, whatever it is named
        return None
    return stripped


def needs_values(poi: dict[str, Any], *, rescore: bool) -> bool:
    """True when this POI still has to go through the model pass.

    A POI missing the explicit `gated` verdict needs the pass even when its
    hours are already held. A door the map holds never does."""
    if rescore:
        return True
    if "opening_hours" not in poi:
        return True
    if not isinstance(poi.get("gated"), bool):
        return True
    basis = poi.get("opening_hours_basis")
    if not (isinstance(basis, str) and basis.strip()):
        return True
    return poi["opening_hours"] is not None and poi.get("opening_hours_source") not in HOURS_SOURCES


def describe(poi: dict[str, Any]) -> str:
    """One compact line per place for the prompt."""
    record = {
        "name": poi.get("name", ""),
        "description": poi.get("short_description", ""),
        "role": poi.get("poi_role", ""),
    }
    return json.dumps(record, ensure_ascii=False)


_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def validate(record: dict[str, Any], *, name: str, gated_only: bool = False) -> str | None:
    """Structural check on one model record. Returns an error string, or None.

    Structural only, like the capacity pass: nobody reviewing this can check
    Paris facts, so these are the properties checkable without knowing the
    city — the same ones ``tests/test_poi_opening_hours.py`` asserts over the
    finished file.

    ``gated_only``: the target POI already carries hours the write path
    preserves, so the only thing this record contributes is the door verdict —
    a sloppy answer in the reply must not refuse the verdict it rides with.
    """
    if gated_only:
        if not isinstance(record.get("gated"), bool):
            return f"{name}: gated is {record.get('gated')!r} — an explicit true/false is required"
        return None
    hours = record.get("opening_hours")
    basis = record.get("opening_hours_basis")

    if not isinstance(record.get("gated"), bool):
        return (
            f"{name}: gated is {record.get('gated')!r} — the door verdict is the one "
            "distinction that matters and must be an explicit true/false"
        )
    if not isinstance(basis, str) or not basis.strip():
        return f"{name}: opening_hours_basis is empty — an unargued answer is unauditable"
    if hours is not None and record["gated"] is False:
        return (
            f"{name}: carries hours while gated=false — an ungated place "
            "has no door for hours to describe"
        )
    if hours is None:
        return None
    if readable_hours(hours) is None:
        return (
            f"{name}: opening_hours {hours!r} is not OpenStreetMap text the library reads "
            "(or carries a quoted comment)"
        )
    return None


def price_batch(client: Any, model: str, batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One model call for one batch. Returns the parsed records, unvalidated."""
    prompt = PROMPT_HEADER + "\n".join(describe(p) for p in batch)
    response = client.messages.create(
        model=model,
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    return records_for_batch(text, batch)


def apply_map_hours(pois: list[dict[str, Any]], matched: dict[str, str]) -> dict[str, int]:
    """Write the map's hours onto every DOOR the map holds — over any guess,
    never onto a place with no door. A matched tag the library cannot read is
    left unknown and counted. Returns the counts."""
    counts = {"map": 0, "unreadable": 0}
    for poi in pois:
        if poi.get("gated") is not True:
            if poi.get("opening_hours") is not None:
                poi["opening_hours"] = None
                poi["opening_hours_source"] = None
            continue
        tag = matched.get(poi.get("name", ""))
        if tag is None:
            continue
        text = readable_hours(tag)
        if text is None:
            counts["unreadable"] += 1
            continue
        poi["opening_hours"] = text
        poi["opening_hours_source"] = SOURCE_MAP
        poi["opening_hours_basis"] = f"Door; hours are the place's own OpenStreetMap tag: {text}."
        counts["map"] += 1
    return counts


def _coverage(pois: list[dict[str, Any]]) -> str:
    doors = [p for p in pois if p.get("gated") is True]
    by_map = sum(1 for p in doors if p.get("opening_hours_source") == SOURCE_MAP)
    by_guess = sum(1 for p in doors if p.get("opening_hours_source") == SOURCE_GUESS)
    unknown = sum(1 for p in doors if p.get("opening_hours") is None)
    return f"{by_map} map, {by_guess} guess, {unknown} unknown of {len(doors)} doors"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--slug", default="paris", help="City slug (default: paris)")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Send only the first N unpriced doors to the model. The dry-run knob.",
    )
    parser.add_argument(
        "--rescore",
        action="store_true",
        help="Re-ask the model about every POI, including ones already carrying hours.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the results and write nothing.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Default: {DEFAULT_MODEL}")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    args = parser.parse_args(argv)

    path = ROOT / "data" / args.slug / "poi-raw.json"
    if not path.exists():
        raise SystemExit(f"✗ no POI file at {path}")

    pois, original = load_pois(path)
    from src import city_registry

    bbox = city_registry.bbox_map()[args.slug]
    print("  fetching OpenStreetMap hours (two Overpass queries) …", flush=True)
    scoped = fetch_osm_hours(bbox, OVERPASS_SCOPED_QUERY)
    wide = fetch_osm_hours(bbox, OVERPASS_WIDE_QUERY)
    matched = match_osm(pois, scoped, wide)
    counts = apply_map_hours(pois, matched)
    print(
        f"  the map holds {counts['map']} doors ({len(scoped)} scoped, {len(wide)} wide "
        f"elements); {counts['unreadable']} unreadable tag(s) left unknown."
    )

    by_name = {p.get("name"): p for p in pois}
    todo = [p for p in pois if needs_values(p, rescore=args.rescore)]
    if args.limit is not None:
        todo = todo[: args.limit]
    print(f"{len(pois)} POIs in {path.relative_to(ROOT)}; {len(todo)} for the model.")

    priced: list[dict[str, Any]] = []
    errors: list[str] = []
    if todo:
        from src.tour.anthropic_client import compose_client

        client = compose_client()
        failed_names: list[str] = []
        for start in range(0, len(todo), args.batch_size):
            batch = todo[start : start + args.batch_size]
            print(f"  processing {start + 1}-{start + len(batch)} of {len(todo)} …", flush=True)
            for record in price_batch(client, args.model, batch):
                name = record.get("name", "(unnamed)")
                existing = by_name.get(name, {})
                keeps_hours = existing.get("opening_hours") is not None and not args.rescore
                problem = validate(record, name=name, gated_only=keeps_hours)
                if problem:
                    errors.append(problem)
                    failed_names.append(name)
                    continue
                priced.append(record)

        # ONE retry of just the failures — same budget logic as the capacity pass:
        # a slip a re-ask fixes must not cost the run; a row failing twice must be
        # seen, not ground away at.
        if failed_names:
            retry = [p for p in todo if p.get("name") in set(failed_names)]
            print(
                f"\n  retrying {len(retry)} record(s) that failed the structural check …",
                flush=True,
            )
            errors = []
            for start in range(0, len(retry), args.batch_size):
                batch = retry[start : start + args.batch_size]
                for record in price_batch(client, args.model, batch):
                    name = record.get("name", "(unnamed)")
                    existing = by_name.get(name, {})
                    keeps_hours = existing.get("opening_hours") is not None and not args.rescore
                    problem = validate(record, name=name, gated_only=keeps_hours)
                    if problem:
                        errors.append(f"(after one retry) {problem}")
                        continue
                    priced.append(record)

        print()
        for record in priced:
            print(f"  {record['name']}")
            print(f"      hours: {record['opening_hours'] or '— (unknown)'}")
            print(f"      basis: {record['opening_hours_basis']}")

    if errors:
        print(f"\n✗ {len(errors)} record(s) failed the structural check twice:", file=sys.stderr)
        for problem in errors:
            print(f"    {problem}", file=sys.stderr)
        print(
            "  These are NOT written. Everything that passed still is, so re-running "
            "this target processes exactly the ones still missing.",
            file=sys.stderr,
        )

    if args.dry_run or args.limit is not None:
        print(f"\nDry run ({len(priced)} processed). Nothing written.")
        return 1 if errors else 0

    for record in priced:
        poi = by_name.get(record["name"])
        if poi is None:
            print(f"✗ model returned an unknown place: {record['name']!r}", file=sys.stderr)
            return 1
        # The door verdict always lands; hours the row already holds are
        # preserved unless --rescore was explicit, and a map row is never a
        # guess's to overwrite.
        poi["gated"] = record["gated"]
        if poi.get("opening_hours") is not None and not args.rescore:
            continue
        if poi.get("opening_hours_source") == SOURCE_MAP:
            continue
        hours = readable_hours(record["opening_hours"]) if record["gated"] else None
        poi["opening_hours"] = hours
        poi["opening_hours_source"] = SOURCE_GUESS if hours is not None else None
        poi["opening_hours_basis"] = record["opening_hours_basis"]

    dump_pois(path, pois, original)
    print(f"\n✓ wrote hours to {path.relative_to(ROOT)}: {_coverage(pois)}")
    if errors:
        print(f"  {len(errors)} still unprocessed — re-run this target to pick up just those.")
    print(f"  NEXT, AND MANDATORY: make sync-poi-exports SLUG={args.slug} — fields")
    print("  written here do not reach the graph until that sync runs.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
