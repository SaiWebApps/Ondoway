"""Learn each POI's opening days and hours — data row 6.1 behind /poi-opening-hours.

WHY A SECOND SCRIPT AND NOT A FLAG ON THE CAPACITY PASS. One script answers one
question (`scripts/poi_visit_duration.py`'s own header records the precedent):
that script prices how long a place absorbs a visitor, this one learns WHEN the
place can be entered at all. Different question, different prompt, different
calibration, different failure modes. What IS shared is imported rather than
copied: `load_pois` / `serialise` / `dump_pois` (the refuse-to-write-on-reformat
round-trip guard) and `records_for_batch` (the one reader of a model reply — the
fence strip, the parse and the alignment check) come straight from the capacity
pass, so the two passes can never drift into different file conventions.

WHAT IT PRODUCES, per POI in ``data/{slug}/poi-raw.json``:

``opening_hours``         A week table — all seven keys ``mon``..``sun``, each a
                          list of ``["HH:MM", "HH:MM"]`` open windows; ``[]``
                          means closed that whole day (``{"tue": []}`` is
                          Rosemary's Tuesday closure). ``null`` = NOT GATED — a
                          street, a square, a bridge — the same load-bearing
                          null ``visit_seconds_inside`` uses. Null is never a
                          way to record "unknown hours for a gated place"; that
                          case is ALSO null but its basis says so, because the
                          safe direction is "never clock-excluded".
``opening_hours_source``  ``"osm"`` when the table transcribes a live OSM
                          ``opening_hours`` tag, ``"ai"`` when it is an audited
                          model judgement. ``null`` when the hours are null.
``opening_hours_basis``   The one-sentence argument, per the house style. For
                          OSM rows it quotes the raw tag, so a reviewer can
                          check the transcription without leaving the file.

SOURCE HIERARCHY (design 6.1: "OSM + audited AI pass"). One bulk Overpass query
over the city registry's bbox pulls every named tourist-relevant element that
carries ``opening_hours``; a POI within 150 m of a name-matching element gets
that raw tag handed to the model WITH the instruction to transcribe it
faithfully rather than judge. Everything else is an audited model pass. The
matcher reuses ``src.tour.routing.haversine_m`` (the one distance definition)
and the bbox comes from ``src.city_registry`` (the one bbox table).

NETWORK. Overpass is on the ingest allowlist (``src/onboard/fetch.py``); this
script calls ``assert_ingestable`` before fetching and then uses httpx directly,
the ``scripts/geocode_pois.py`` operator-script precedent — the fixture-mode
knob in the guarded door exists so the TEST bar never touches the network, and
this is operator tooling that must.

SPEND. One model call per batch, so a full Paris pass is roughly
370 / BATCH_SIZE calls plus one Overpass query. ``--limit`` prices a subset and
writes nothing; that is the intended dry run before a full pass.
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
#: to re-run, and a full pass stays ~25 calls.
DEFAULT_BATCH_SIZE: int = 15

DAY_KEYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

#: An OSM element may claim a POI only from closer than this. Wider and a
#: café's hours attach to the cathedral next door; narrower and a way's
#: computed centre misses its own forecourt.
OSM_MATCH_RADIUS_M: float = 150.0

#: Overpass returns only named, tourist-relevant elements carrying
#: ``opening_hours`` — scoping by tag keeps the payload in the hundreds instead
#: of every shop and restaurant in the city.
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_QUERY = """
[out:json][timeout:120];
(
  nwr["opening_hours"]["name"]["tourism"]({s},{w},{n},{e});
  nwr["opening_hours"]["name"]["historic"]({s},{w},{n},{e});
  nwr["opening_hours"]["name"]["amenity"~"^(place_of_worship|marketplace)$"]({s},{w},{n},{e});
  nwr["opening_hours"]["name"]["leisure"~"^(park|garden)$"]({s},{w},{n},{e});
);
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
A wrong table locks a visitor out of an open door; null merely leaves the
planner as trusting as it is today. Err toward null.

WHEN A LINE BELOW CARRIES `osm_opening_hours`, that is the place's own live
OpenStreetMap tag. TRANSCRIBE it into the week table faithfully — do not
second-guess it, extend it, or blend it with your own knowledge. Your basis
must quote the raw tag so a reviewer can check the transcription. If the tag is
too mangled to transcribe, return null and quote it in the basis anyway.

THE TABLE SHAPE, exactly:
- an object with ALL SEVEN keys "mon","tue","wed","thu","fri","sat","sun";
- each value a list of ["HH:MM","HH:MM"] open windows, 24-hour clock;
- an empty list [] means CLOSED that whole day — {"tue": []} is a Tuesday
  closure;
- no zero-length or backwards window; split a lunch closure into two windows.
- Where hours vary by season, record the CURRENT typical pattern and say so in
  the basis.

FOR EACH PLACE RETURN: `name` (copied exactly), `gated` (true or false — the
one distinction above, answered EXPLICITLY for every place, including the ones
whose hours you do not know), `opening_hours` (table or null),
`opening_hours_basis` (ONE sentence: what kind of place it is and where
the hours came from — the OSM tag, the institution's own published pattern, or
why it is not gated. Never "popular" or "usually open").

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
    a, b = _normalise(ours), _normalise(theirs)
    if not a or not b:
        return False
    if a == b:
        return True
    # Containment either way, guarded so a four-letter fragment ("pont")
    # cannot claim every bridge in the city.
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return len(shorter) >= 5 and shorter in longer


def fetch_osm_hours(bbox: tuple[float, float, float, float]) -> list[dict[str, Any]]:
    """One bulk Overpass pull: [{name, lat, lng, opening_hours}, ...].

    ``bbox`` is the registry's (min_lat, max_lat, min_lon, max_lon); Overpass
    wants (south, west, north, east). One retry, then abort — silently pricing
    the whole city as "ai" because a network call failed would erase the better
    source without anyone seeing it happen.
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
            f"✗ Overpass unreachable after 2 attempts ({last_error}); refusing to run "
            "an all-AI pass that silently drops the OSM half of the source hierarchy."
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


def match_osm(
    pois: list[dict[str, Any]], elements: list[dict[str, Any]]
) -> dict[str, str]:
    """POI name -> raw OSM opening_hours, for POIs with a close name-matching
    element. Nearest match wins when several qualify."""
    from src.tour.routing import haversine_m

    matched: dict[str, str] = {}
    for poi in pois:
        lat, lng = poi.get("latitude"), poi.get("longitude")
        if lat is None or lng is None:
            continue
        best: tuple[float, str] | None = None
        for el in elements:
            distance = haversine_m(float(lat), float(lng), el["lat"], el["lng"])
            if distance > OSM_MATCH_RADIUS_M:
                continue
            if not _names_match(poi.get("name", ""), el["name"]):
                continue
            if best is None or distance < best[0]:
                best = (distance, el["opening_hours"])
        if best is not None:
            matched[poi["name"]] = best[1]
    return matched


def needs_values(poi: dict[str, Any], *, rescore: bool) -> bool:
    """True when this POI still has to go through the pass.

    A POI missing the explicit `gated` verdict (Docs/adr/0003) needs the pass
    even when its hours are already priced — the gated backfill is what ends
    the null-means-two-things overload, and the write path below preserves an
    existing hours table on such a row unless --rescore says otherwise.
    """
    if rescore:
        return True
    if "opening_hours" not in poi:
        return True
    if not isinstance(poi.get("gated"), bool):
        return True
    basis = poi.get("opening_hours_basis")
    if not (isinstance(basis, str) and basis.strip()):
        return True
    return poi["opening_hours"] is not None and poi.get("opening_hours_source") not in (
        "osm",
        "ai",
    )


def describe(poi: dict[str, Any], osm_hours: str | None) -> str:
    """One compact line per place for the prompt."""
    record = {
        "name": poi.get("name", ""),
        "description": poi.get("short_description", ""),
        "role": poi.get("poi_role", ""),
    }
    if osm_hours is not None:
        record["osm_opening_hours"] = osm_hours
    return json.dumps(record, ensure_ascii=False)


_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def validate(record: dict[str, Any], *, name: str, gated_only: bool = False) -> str | None:
    """Structural check on one record. Returns an error string, or None.

    Structural only, like the capacity pass: nobody reviewing this can check
    Paris facts, so these are the properties checkable without knowing the
    city — the same ones ``tests/test_poi_opening_hours.py`` asserts over the
    finished file.

    ``gated_only``: the target POI already carries a table the write path
    preserves, so the only thing this record contributes is the door verdict —
    a sloppy table in the reply must not refuse the verdict it rides with.
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
        return f"{name}: opening_hours_basis is empty — an unargued table is unauditable"
    if hours is not None and record["gated"] is False:
        return (
            f"{name}: carries an hours table while gated=false — an ungated place "
            "has no door for a table to describe"
        )
    if hours is None:
        return None
    if not isinstance(hours, dict) or set(hours) != set(DAY_KEYS):
        return f"{name}: opening_hours must carry exactly the keys {list(DAY_KEYS)}"
    for day in DAY_KEYS:
        windows = hours[day]
        if not isinstance(windows, list):
            return f"{name}: {day} is not a list of windows"
        for window in windows:
            # "24:00" is a legitimate END-of-day close (Palais de Tokyo shuts
            # at midnight; OSM writes it this way) but never a start.
            if (
                not isinstance(window, list)
                or len(window) != 2
                or not all(isinstance(t, str) for t in window)
                or not _TIME_RE.match(window[0])
                or not (_TIME_RE.match(window[1]) or window[1] == "24:00")
            ):
                return f"{name}: {day} window {window!r} is not ['HH:MM', 'HH:MM']"
            if window[0] >= window[1]:
                return (
                    f"{name}: {day} window {window!r} is zero-length or backwards — "
                    "an open window must end after it starts"
                )
    return None


def price_batch(
    client: Any, model: str, batch: list[dict[str, Any]], osm_by_name: dict[str, str]
) -> list[dict[str, Any]]:
    """One model call for one batch. Returns the parsed records, unvalidated."""
    prompt = PROMPT_HEADER + "\n".join(
        describe(p, osm_by_name.get(p.get("name", ""))) for p in batch
    )
    response = client.messages.create(
        model=model,
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    return records_for_batch(text, batch)


# ── the verification ladder (Docs/adr/0003) ────────────────────────────────
#
# "Verified" is inspectable per place, forever: a row's `opening_hours_verified`
# records WHO or WHAT confirmed the table, at which TIER, on what EVIDENCE, and
# WHEN. Three tiers:
#   0 — corroboration: the live OSM tag still equals the tag the row's basis
#       quotes, so the stored transcription describes today's published hours.
#       Auto-passes with both observations recorded. Deterministic, $0.
#   1 — a cited model judgement (the audited pass) — surfaces in the queue for
#       the human's bulk approval rather than self-certifying at launch.
#   2 — a human, through the interactive review below. The permanent floor:
#       conflicts, low confidence, and the top-gravity places are reviewed one
#       by one; the corroborated tail may be bulk-approved.
# A re-fetch that DISAGREES with a verified row demotes it (the auto-demote
# rule): the voice falls back to the could-not-confirm disclosure and the row
# re-enters the queue at its tier.

_OSM_TAG_IN_BASIS_RE = re.compile(r"OSM tag ['\"]([^'\"]+)['\"]")


def _verified_record(tier: int, approver: str, evidence: str) -> dict[str, Any]:
    from datetime import date

    return {
        "tier": tier,
        "approver": approver,
        "evidence": evidence,
        "at": date.today().isoformat(),
    }


def quoted_osm_tag(poi: dict[str, Any]) -> str | None:
    """The raw OSM tag an osm-sourced row's basis quotes, or None."""
    if poi.get("opening_hours_source") != "osm":
        return None
    match = _OSM_TAG_IN_BASIS_RE.search(poi.get("opening_hours_basis") or "")
    return match.group(1) if match else None


def corroborate(
    pois: list[dict[str, Any]], live_tags: dict[str, str]
) -> tuple[list[str], list[str], list[str]]:
    """Tier-0 pass over gated, table-carrying, unverified rows.

    Returns (verified_names, conflict_names, demoted_names): a row whose live
    tag equals its quoted tag is tier-0 verified; one whose live tag DIFFERS is
    a conflict for the queue — and if it was already verified, it is demoted on
    the spot (the auto-demote rule). A row with no live tag is left for the
    queue untouched.
    """
    verified: list[str] = []
    conflicts: list[str] = []
    demoted: list[str] = []
    for poi in pois:
        if poi.get("gated") is not True or poi.get("opening_hours") is None:
            continue
        quoted = quoted_osm_tag(poi)
        live = live_tags.get(poi.get("name", ""))
        if quoted is None or live is None:
            continue
        if live == quoted:
            if poi.get("opening_hours_verified") is None:
                poi["opening_hours_verified"] = _verified_record(
                    0,
                    "corroboration",
                    f'OSM tag unchanged since transcription: "{live}"',
                )
                verified.append(poi["name"])
        else:
            conflicts.append(poi["name"])
            if poi.get("opening_hours_verified") is not None:
                poi["opening_hours_verified"] = None
                demoted.append(poi["name"])
    return verified, conflicts, demoted


def review_queue(
    pois: list[dict[str, Any]], conflicts: list[str]
) -> list[dict[str, Any]]:
    """The rows still owing a human decision, in review order: conflicts first,
    then by gravity (importance_tier) descending, then name — the owner's
    minutes land where a wrong "open" costs the most."""
    conflict_set = set(conflicts)
    pending = [
        p
        for p in pois
        if p.get("gated") is True
        and p.get("opening_hours") is not None
        and p.get("opening_hours_verified") is None
    ]
    return sorted(
        pending,
        key=lambda p: (
            p.get("name") not in conflict_set,
            -(p.get("importance_tier") or 1),
            p.get("name", ""),
        ),
    )


def _render_row(poi: dict[str, Any], live_tag: str | None) -> str:
    hours = poi.get("opening_hours") or {}
    closed = ", ".join(d for d in DAY_KEYS if hours.get(d) == []) or "none"
    lines = [
        f"  {poi.get('name')}  (tier {poi.get('importance_tier')}, "
        f"source {poi.get('opening_hours_source')})",
        f"    closed days: {closed}",
        f"    basis: {poi.get('opening_hours_basis')}",
    ]
    if live_tag is not None:
        lines.append(f'    live OSM tag now: "{live_tag}"')
    return "\n".join(lines)


def run_review(
    pois: list[dict[str, Any]],
    live_tags: dict[str, str],
    conflicts: list[str],
    *,
    approver: str,
    list_only: bool,
) -> int:
    """The interactive tier-2 session (the beat_dedup approve-loop precedent).

    ``list_only`` renders the queue and decides nothing — the demoable,
    TTY-free view. Decisions: [a]ccept (tier 2), [n]ull the table (hours
    genuinely unknowable; fail-open honesty), [s]kip, [b]ulk-accept every
    remaining UNCONTESTED osm-sourced row (one confirmation, each row still
    stamped with this approver), [q]uit. Returns the number decided.
    """
    queue = review_queue(pois, conflicts)
    print(f"\n  {len(queue)} row(s) in the review queue (conflicts first).")
    if list_only:
        for poi in queue:
            print()
            print(_render_row(poi, live_tags.get(poi.get("name", ""))))
        return 0
    decided = 0
    index = 0
    while index < len(queue):
        poi = queue[index]
        print()
        print(_render_row(poi, live_tags.get(poi.get("name", ""))))
        answer = input("  [a]ccept  [n]ull  [s]kip  [b]ulk-osm  [q]uit > ").strip().lower()
        if answer == "a":
            poi["opening_hours_verified"] = _verified_record(
                2, approver, "reviewed in the hours session"
            )
            decided += 1
            index += 1
        elif answer == "n":
            poi["opening_hours"] = None
            poi["opening_hours_source"] = None
            poi["opening_hours_verified"] = None
            poi["opening_hours_basis"] = (
                "Gated, but the published hours could not be confirmed in review; "
                "left unfiltered."
            )
            decided += 1
            index += 1
        elif answer == "b":
            conflict_set = set(conflicts)
            bulk = [
                p
                for p in queue[index:]
                if p.get("opening_hours_source") == "osm"
                and p.get("name") not in conflict_set
            ]
            confirm = input(f"  accept {len(bulk)} uncontested OSM rows? [y/N] > ")
            if confirm.strip().lower() == "y":
                for p in bulk:
                    p["opening_hours_verified"] = _verified_record(
                        2, approver, "bulk-accepted: OSM-sourced, uncontested"
                    )
                decided += len(bulk)
                queue = [p for p in queue if p.get("opening_hours_verified") is None]
                index = 0
        elif answer == "q":
            break
        else:
            index += 1
    print(f"\n  {decided} row(s) decided this session.")
    return decided


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--slug", default="paris", help="City slug (default: paris)")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N unpriced POIs. The dry-run knob.",
    )
    parser.add_argument(
        "--rescore",
        action="store_true",
        help="Re-run every POI, including ones already carrying hours.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the results and write nothing.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Default: {DEFAULT_MODEL}")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Run the ladder instead of the pricing pass: tier-0 corroboration, "
        "auto-demote on drift, then the review queue.",
    )
    parser.add_argument(
        "--list-queue",
        action="store_true",
        help="With --verify: render the review queue and decide nothing.",
    )
    parser.add_argument(
        "--approver",
        default=None,
        help="With --verify: who is deciding — stamped onto every tier-2 record.",
    )
    args = parser.parse_args(argv)

    path = ROOT / "data" / args.slug / "poi-raw.json"
    if not path.exists():
        raise SystemExit(f"✗ no POI file at {path}")

    pois, original = load_pois(path)

    if args.verify:
        from src import city_registry

        print("  fetching OSM opening_hours (one Overpass query) …", flush=True)
        elements = fetch_osm_hours(city_registry.bbox_map()[args.slug])
        live_tags = match_osm(pois, elements)
        verified, conflicts, demoted = corroborate(pois, live_tags)
        print(
            f"  tier-0: {len(verified)} corroborated, {len(conflicts)} conflict(s), "
            f"{len(demoted)} demoted."
        )
        if not args.list_queue and not args.approver:
            raise SystemExit(
                "✗ --verify needs --approver <name> for the review session "
                "(or --list-queue to render the queue and decide nothing)."
            )
        run_review(
            pois,
            live_tags,
            conflicts,
            approver=args.approver or "",
            list_only=args.list_queue,
        )
        if args.list_queue:
            # "Render the review queue and decide nothing" — nothing decided
            # means nothing WRITTEN: the corroboration computed on the way to
            # the queue stays in memory, and the file stays byte-identical.
            print("\nQueue listed. Nothing decided, nothing written.")
            return 0
        if args.dry_run:
            print("\nDry run. Nothing written.")
            return 0
        dump_pois(path, pois, original)
        print(f"\n✓ wrote verification state to {path.relative_to(ROOT)}")
        print(f"  NEXT, AND MANDATORY: make sync-poi-exports SLUG={args.slug}")
        return 0
    by_name = {p.get("name"): p for p in pois}
    todo = [p for p in pois if needs_values(p, rescore=args.rescore)]
    if args.limit is not None:
        todo = todo[: args.limit]

    print(f"{len(pois)} POIs in {path.relative_to(ROOT)}; {len(todo)} to process.")
    if not todo:
        print("Nothing to do.")
        return 0

    from src import city_registry

    print("  fetching OSM opening_hours (one Overpass query) …", flush=True)
    elements = fetch_osm_hours(city_registry.bbox_map()[args.slug])
    osm_by_name = match_osm(todo, elements)
    print(
        f"  OSM carries opening_hours for {len(osm_by_name)} of {len(todo)} "
        f"POIs ({len(elements)} candidate elements)."
    )

    from src.tour.anthropic_client import compose_client

    client = compose_client()

    priced: list[dict[str, Any]] = []
    failed_names: list[str] = []
    errors: list[str] = []
    for start in range(0, len(todo), args.batch_size):
        batch = todo[start : start + args.batch_size]
        print(f"  processing {start + 1}-{start + len(batch)} of {len(todo)} …", flush=True)
        for record in price_batch(client, args.model, batch, osm_by_name):
            name = record.get("name", "(unnamed)")
            existing = by_name.get(name, {})
            keeps_table = existing.get("opening_hours") is not None and not args.rescore
            problem = validate(record, name=name, gated_only=keeps_table)
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
        print(f"\n  retrying {len(retry)} record(s) that failed the structural check …", flush=True)
        errors = []
        for start in range(0, len(retry), args.batch_size):
            batch = retry[start : start + args.batch_size]
            for record in price_batch(client, args.model, batch, osm_by_name):
                name = record.get("name", "(unnamed)")
                existing = by_name.get(name, {})
                keeps_table = existing.get("opening_hours") is not None and not args.rescore
                problem = validate(record, name=name, gated_only=keeps_table)
                if problem:
                    errors.append(f"(after one retry) {problem}")
                    continue
                priced.append(record)

    print()
    for record in priced:
        hours = record["opening_hours"]
        source = "—" if hours is None else ("osm" if record["name"] in osm_by_name else "ai")
        closed = (
            "not gated"
            if hours is None
            else (
                "closed " + ", ".join(d for d in DAY_KEYS if hours[d] == [])
                if any(hours[d] == [] for d in DAY_KEYS)
                else "open all seven days"
            )
        )
        print(f"  {record['name']}")
        print(f"      hours: {closed}    source: {source}")
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
        # The door verdict always lands; an EXISTING hours table is preserved
        # unless --rescore was explicit (Docs/adr/0003): a gated backfill over
        # an already-priced corpus must never re-roll live tables through
        # model nondeterminism and move clock exclusions as a side effect.
        poi["gated"] = record["gated"]
        if poi.get("opening_hours") is not None and not args.rescore:
            continue
        hours = record["opening_hours"]
        poi["opening_hours"] = hours
        # The SCRIPT assigns the source, deterministically: the model is never
        # asked to grade its own provenance.
        poi["opening_hours_source"] = (
            None if hours is None else ("osm" if record["name"] in osm_by_name else "ai")
        )
        poi["opening_hours_basis"] = record["opening_hours_basis"]

    dump_pois(path, pois, original)
    print(f"\n✓ wrote opening hours for {len(priced)} POIs to {path.relative_to(ROOT)}")
    if errors:
        print(f"  {len(errors)} still unprocessed — re-run this target to pick up just those.")
    print(f"  NEXT, AND MANDATORY: make sync-poi-exports SLUG={args.slug} — fields")
    print("  written here do not reach the graph until that sync runs.")
    # Non-zero while anything is unprocessed, same as the capacity pass.
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
