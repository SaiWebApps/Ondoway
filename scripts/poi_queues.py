"""Price the QUEUE at every POI — the queue pass (redesign data row 6.5, plan W3.1).

EXTENDS ``scripts/poi_visit_duration.py`` — same corpus file, same batched audited-AI
shape, same round-trip write guard (``load_pois`` / ``dump_pois`` are IMPORTED from it,
not copied, so the refuse-to-write-on-reformat guard stays defined once), the same
reader of a model reply (``records_for_batch``: fence, parse, alignment), and the same
window grammar as the opening-hours pass (``_TIME_RE`` is imported, not restated, so
"what is a valid HH:MM" keeps exactly one definition). It is a separate script because
it answers a DIFFERENT QUESTION (the header rule in ``scripts/poi_visit_duration.py``):
not how long a place absorbs a visitor, not when its door is open, but how long the
LINE in front of that door costs — a fourth kind of time: a queue is priced
separately, by hour at the busy season's level, it belongs to the day rather
than to the building, and it is excluded entirely under `wall`.

WHAT IT PRODUCES, per POI in ``data/{slug}/poi-raw.json``:

``queue_class``           ``none | short | long | unpredictable``. ``none`` means no
                          line exists to stand in — a street, a bridge, a square, an
                          ungated church door. ``unpredictable`` is the catacombs
                          class: a wait that CANNOT BE BOUNDED, which is never
                          offered under a ``wall`` end.
``queue_minutes_peak``    Integer minutes of line at the WORST normal hour.
``queue_minutes_offpeak`` Integer minutes of line at a quiet hour. Peak >= off-peak
                          by definition; both 0 for ``none``.
``queue_peak_hours``      The daily hour bands when the peak applies — a list of
                          ``["HH:MM", "HH:MM"]`` windows, exactly the window
                          encoding ``opening_hours`` already uses (one grammar for
                          clock ranges, defined once). ``[]`` when no band is worse
                          than the rest.
``queue_basis``           ONE sentence arguing for the class and both numbers,
                          judgeable by someone who has never been to the city.

THE QUEUE IS THE WAIT BEFORE ENTERING — security and ticket line — AND IT IS PRICED
SEPARATELY FROM VISIT TIME. ``visit_seconds_inside`` currently folds a typical queue
into the visit (owner ruling 2026-08-06: the basis sentence records the split so a
later pass "can separate the two without re-pricing the city"); this pass is that
later pass. The Sainte-Chapelle is the anchor: 28 minutes of line plus 38 under
the glass are TWO facts, and folding them
into one 66 makes the wait permanent — unskippable by someone who only wants the
outside, unshrinkable at opening time.

REVIEW AT THE STANDARD RATE, WITH NAMED SPOT CHECKS. Unlike the place judgements,
queues have observable external sources (timed-ticket pages, institutions' own
"expect security lines" guidance), so the double-rate rule does not apply. The
reviewer MUST individually check: the Louvre, Sainte-Chapelle, the Musée d'Orsay,
and every row classed ``unpredictable`` (the catacombs class) — the marquee lines a
wrong number would misprice for the most visitors, and the class that changes what a
``wall`` day may be offered at all.

SPEND. One model call per batch of POIs, so a full Paris pass is roughly
370 / BATCH_SIZE calls (~25). ``--limit`` runs a subset and ``--dry-run`` writes
nothing; both are the intended way to inspect output before committing to a full pass.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# The load/serialise/dump mechanics are the capacity pass's, imported so the
# refuse-to-write-on-reformat guard lives in exactly one place; records_for_batch
# is its one reader of a model reply (fence, parse, alignment), imported for the
# same reason; DEFAULT_MODEL and DEFAULT_BATCH_SIZE ride along so retuning
# corpus-judgement work moves every pass at once (the poi_place_judgements
# precedent). _TIME_RE is the opening-hours pass's one definition of a valid
# HH:MM — queue peak bands reuse its grammar rather than fork it.
try:  # run directly as `python scripts/poi_queues.py` — scripts/ is sys.path[0]
    from poi_opening_hours import _TIME_RE
    from poi_visit_duration import (
        DEFAULT_BATCH_SIZE,
        DEFAULT_MODEL,
        dump_pois,
        load_pois,
        records_for_batch,
    )
except ModuleNotFoundError:  # imported as `scripts.poi_queues` (pytest)
    from scripts.poi_opening_hours import _TIME_RE
    from scripts.poi_visit_duration import (
        DEFAULT_BATCH_SIZE,
        DEFAULT_MODEL,
        dump_pois,
        load_pois,
        records_for_batch,
    )

ROOT = Path(__file__).resolve().parent.parent

#: THE closed vocabulary. The structural test
#: asserts every written value is one of these and that this tuple never silently
#: widens — an unreviewed fifth class would change what a `wall` day may be offered.
QUEUE_CLASSES: tuple[str, ...] = ("none", "short", "long", "unpredictable")

#: The two integer fields, then all five. Write-back, the needs-pricing check and
#: the structural validator all iterate these tuples.
QUEUE_INT_FIELDS: tuple[str, ...] = ("queue_minutes_peak", "queue_minutes_offpeak")
QUEUE_FIELDS: tuple[str, ...] = (
    "queue_class",
    *QUEUE_INT_FIELDS,
    "queue_peak_hours",
    "queue_basis",
)

#: Absurdity ceiling on a priced wait, in minutes. Four hours clears the longest
#: famous European line's plausible worst normal hour (the catacombs' two-hours-plus)
#: while refusing a number that could swallow a whole day; a place that would
#: genuinely exceed it is `unpredictable`, capped here, and says so in its basis —
#: the VISIT_SECONDS_INSIDE_MAX precedent.
QUEUE_MINUTES_MAX: int = 240

PROMPT_HEADER = """\
You are pricing the QUEUE at each place on a walking tour: the security and ticket \
line a visitor stands in BEFORE entering. One class, two numbers, the hour bands \
when the line peaks, and a sentence that argues them.

READ THIS FIRST — IT IS THE MOST COMMON WAY THIS TASK GOES WRONG.

The queue is NOT visit time and NOT a rating of popularity. It is the wait between \
joining the line outside and getting in, priced separately from however long the \
visit inside takes. A world-famous place can have no line at all (big doors, no \
ticket) and a modest one can have a long one (timed entry, one small door). Price \
the LINE, never the fame.

THE CLASSES:

- `none` — there is no line to stand in: a street, a bridge, a square, a garden \
gate, a church you walk straight into. A place with no interior at all is always \
`none`.
- `short` — a line exists but is minor at its worst: a bag check that moves, a \
ticket desk with a few people at it.
- `long` — a substantial line is a normal part of entering at busy hours.
- `unpredictable` — the wait CANNOT BE BOUNDED: entry is throttled to a trickle, \
and the line can be twenty minutes or three hours with no way to know which \
(the catacombs class). The planner refuses to promise these under a hard end \
time, so use this class only when the variance is the famous fact about the place.

CALIBRATION, matched to real observed days:

- Sainte-Chapelle: `long`, peak 30, off-peak 10, peak hours [["10:30","16:00"]]. \
Airport-style security plus a ticket check ahead of a small chapel; the observed \
day behind this corpus stood 28 minutes in it.
- The Louvre (pyramid entrance): `long`, peak 40, off-peak 15, peak hours \
[["10:00","15:00"]]. Security into the pyramid, then ticket gates; timed tickets \
shorten but do not remove it.
- Pont Neuf, any street, any open square: `none`, 0, 0, []. There is no door and \
no line — the whole value stands in the open.
- A parish church with an open door: `none`, 0, 0, []. A door you walk through \
without stopping is not a queue.
- The Catacombs: `unpredictable`, peak 120, off-peak 45, peak hours \
[["11:00","17:00"]]. Entry is capped to a few hundred people underground at once, \
so the line moves at a trickle and cannot be bounded; the numbers are the typical \
case, and the basis must say the bound is untrustworthy.

FOR EACH PLACE, RETURN:

1. `queue_class` — exactly one of "none", "short", "long", "unpredictable".

2. `queue_minutes_peak` — integer minutes of line at the WORST normal hour. 0 for \
`none`. At least 1 for every other class: a line that never costs a minute is \
`none`.

3. `queue_minutes_offpeak` — integer minutes at a quiet hour (opening time, late \
afternoon). Never more than the peak. 0 for `none`.

4. `queue_peak_hours` — the daily hour bands when the peak number applies, as a \
list of ["HH:MM","HH:MM"] windows on the 24-hour clock (["11:00","16:00"] is one \
band). [] for `none`, and [] when the line is constant rather than peaked — but if \
your peak number is HIGHER than your off-peak number, you are claiming a peak \
exists and must say WHEN it is.

5. `queue_basis` — ONE sentence arguing the class and both numbers, written for a \
reviewer who has never been to this city. Name the mechanism — security screening, \
timed tickets, one small door, capped occupancy — never "popular" or "busy", which \
argue nothing. For `unpredictable`, say WHY the wait cannot be bounded.

RULES THAT MATTER:

- Price the wait BEFORE entering only. Time inside, however crowded, is visit \
time and is priced elsewhere.
- Peak vs off-peak is BY HOUR OF DAY. Where season moves the line (summer doubles \
it), price the busy season and say so in the basis.
- If you do not know the place, say so in `queue_basis` and default toward `none` \
for open places and `short` for small ticketed interiors — never invent a famous \
line.
- Return STRICT JSON only: an array with one object per place, in the same order, \
each with keys `name`, `queue_class`, `queue_minutes_peak`, \
`queue_minutes_offpeak`, `queue_peak_hours`, `queue_basis`. No prose before or \
after, no markdown fence.

THE PLACES:
"""


def needs_values(poi: dict[str, Any], *, rescore: bool) -> bool:
    """True when this POI still has to be priced."""
    if rescore:
        return True
    return not (
        poi.get("queue_class") in QUEUE_CLASSES
        and all(
            isinstance(poi.get(field), int) and not isinstance(poi.get(field), bool)
            for field in QUEUE_INT_FIELDS
        )
        and isinstance(poi.get("queue_peak_hours"), list)
        and isinstance(poi.get("queue_basis"), str)
        and poi["queue_basis"].strip()
    )


def describe(poi: dict[str, Any]) -> str:
    """One compact line per place for the prompt. `place_category` rides along
    because it settles the easy half outright: a street or a bridge is `none`
    before the model reads another word."""
    return json.dumps(
        {
            "name": poi.get("name", ""),
            "description": poi.get("short_description", ""),
            "role": poi.get("poi_role", ""),
            "category": poi.get("place_category", ""),
            "importance_tier_FOR_IDENTIFICATION_ONLY": poi.get("importance_tier"),
        },
        ensure_ascii=False,
    )


def validate(record: dict[str, Any], *, name: str) -> str | None:
    """Structural check on one priced place. Returns an error string, or None.

    Deliberately structural only, the rule every sibling pass records: nobody
    reviewing this output can check city facts, so these are the properties
    checkable without knowing the city — the same ones
    ``tests/test_poi_queues.py`` asserts over the finished file.
    """
    cls = record.get("queue_class")
    if cls not in QUEUE_CLASSES:
        return f"{name}: queue_class {cls!r} is not one of {list(QUEUE_CLASSES)}"

    for field in QUEUE_INT_FIELDS:
        value = record.get(field)
        if not isinstance(value, int) or isinstance(value, bool):
            return f"{name}: {field} is not an integer ({value!r})"
        if value < 0:
            return f"{name}: {field} {value} is negative"
        if value > QUEUE_MINUTES_MAX:
            return f"{name}: {field} {value} exceeds the {QUEUE_MINUTES_MAX}-minute cap"
    peak = record["queue_minutes_peak"]
    offpeak = record["queue_minutes_offpeak"]
    if peak < offpeak:
        return (
            f"{name}: queue_minutes_peak {peak} is below queue_minutes_offpeak "
            f"{offpeak} — peak is by definition the worse hour"
        )

    windows = record.get("queue_peak_hours")
    if not isinstance(windows, list):
        return f"{name}: queue_peak_hours is not a list of windows ({windows!r})"
    for window in windows:
        # Same window grammar as opening_hours, including "24:00" as a
        # legitimate END of a band but never a start.
        if (
            not isinstance(window, list)
            or len(window) != 2
            or not all(isinstance(t, str) for t in window)
            or not _TIME_RE.match(window[0])
            or not (_TIME_RE.match(window[1]) or window[1] == "24:00")
        ):
            return f"{name}: queue_peak_hours window {window!r} is not ['HH:MM', 'HH:MM']"
        if window[0] >= window[1]:
            return (
                f"{name}: queue_peak_hours window {window!r} is zero-length or "
                "backwards — a peak band must end after it starts"
            )

    if cls == "none":
        if peak != 0 or offpeak != 0:
            return (
                f"{name}: queue_class 'none' with nonzero minutes (peak {peak}, "
                f"off-peak {offpeak}) — a place with no line costs no wait"
            )
        if windows:
            return (
                f"{name}: queue_class 'none' with peak hours {windows!r} — "
                "a place with no line has no peak"
            )
    elif peak < 1:
        return (
            f"{name}: queue_class {cls!r} with a zero peak wait — "
            "a line that never costs a minute is 'none'"
        )
    if peak > offpeak and not windows:
        return (
            f"{name}: peak {peak} exceeds off-peak {offpeak} but queue_peak_hours "
            "is empty — an hour-priced planner cannot tell WHEN the peak applies"
        )

    basis = record.get("queue_basis")
    if not isinstance(basis, str) or not basis.strip():
        return f"{name}: queue_basis is empty — an unargued wait is unauditable"
    return None


def price_batch(client: Any, model: str, batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One model call for one batch. Returns the parsed records, unvalidated.

    The call itself is twice-tried against TRANSPORT trouble (timeout /
    dropped connection), the same discipline the pass already applies to
    structurally-bad rows — the first full Paris run died on one
    ``httpx.ReadTimeout`` twenty-odd calls in, taking every batch with it.
    A second transport failure on the same batch is a real outage: named
    and fatal, never silently skipped (a silently unpriced batch reads as
    "reviewed, no queue anywhere").
    """
    from anthropic import APIConnectionError, APITimeoutError

    prompt = PROMPT_HEADER + "\n".join(describe(p) for p in batch)
    response = None
    for attempt in (1, 2):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=8000,
                messages=[{"role": "user", "content": prompt}],
            )
            break
        except (APITimeoutError, APIConnectionError) as exc:
            if attempt == 2:
                raise SystemExit(
                    f"✗ transport failed twice on a batch of {len(batch)} "
                    f"(first POI: {batch[0].get('name', '?')}): {exc}"
                ) from exc
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    return records_for_batch(text, batch)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--slug", default="paris", help="City slug (default: paris)")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Price only the first N unpriced POIs. The dry-run knob: writes nothing.",
    )
    parser.add_argument(
        "--rescore",
        action="store_true",
        help="Re-price every POI, including ones already carrying queue values.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the priced places and write nothing.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Default: {DEFAULT_MODEL}")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    args = parser.parse_args(argv)

    path = ROOT / "data" / args.slug / "poi-raw.json"
    if not path.exists():
        raise SystemExit(f"✗ no POI file at {path}")

    pois, original = load_pois(path)
    by_name = {p.get("name"): p for p in pois}
    todo = [p for p in pois if needs_values(p, rescore=args.rescore)]
    if args.limit is not None:
        todo = todo[: args.limit]

    print(f"{len(pois)} POIs in {path.relative_to(ROOT)}; {len(todo)} to price.")
    if not todo:
        print("Nothing to do.")
        return 0

    from src.tour.anthropic_client import compose_client

    client = compose_client()

    priced: list[dict[str, Any]] = []
    failed_names: list[str] = []
    errors: list[str] = []
    for start in range(0, len(todo), args.batch_size):
        batch = todo[start : start + args.batch_size]
        print(f"  pricing {start + 1}-{start + len(batch)} of {len(todo)} …", flush=True)
        for record in price_batch(client, args.model, batch):
            name = record.get("name", "(unnamed)")
            problem = validate(record, name=name)
            if problem:
                errors.append(problem)
                failed_names.append(name)
                continue
            priced.append(record)

    # ONE retry of just the failures — the capacity pass's rule, for the same
    # reason: over 370 places a single malformed row must not cost the whole paid
    # run, and a row that fails twice is a real problem that must be seen, not
    # ground away at.
    if failed_names:
        retry = [p for p in todo if p.get("name") in set(failed_names)]
        print(f"\n  retrying {len(retry)} record(s) that failed the structural check …", flush=True)
        errors = []
        for start in range(0, len(retry), args.batch_size):
            batch = retry[start : start + args.batch_size]
            for record in price_batch(client, args.model, batch):
                name = record.get("name", "(unnamed)")
                problem = validate(record, name=name)
                if problem:
                    errors.append(f"(after one retry) {problem}")
                    continue
                priced.append(record)

    print()
    for record in priced:
        bands = ", ".join(f"{start}-{end}" for start, end in record["queue_peak_hours"])
        print(f"  {record['name']}")
        print(
            f"      queue: {record['queue_class']}    "
            f"peak {record['queue_minutes_peak']} min    "
            f"off-peak {record['queue_minutes_offpeak']} min    "
            f"peak hours: {bands if bands else '—'}"
        )
        print(f"      basis: {record['queue_basis']}")

    if errors:
        print(f"\n✗ {len(errors)} record(s) failed the structural check twice:", file=sys.stderr)
        for problem in errors:
            print(f"    {problem}", file=sys.stderr)
        print(
            "  These are NOT written. Everything that passed still is, so re-running "
            "this target prices exactly the ones still missing.",
            file=sys.stderr,
        )

    if args.dry_run or args.limit is not None:
        print(f"\nDry run ({len(priced)} priced). Nothing written.")
        return 1 if errors else 0

    for record in priced:
        poi = by_name.get(record["name"])
        if poi is None:
            print(f"✗ model returned an unknown place: {record['name']!r}", file=sys.stderr)
            return 1
        # Explicit per-field assignments, not a loop: the export-sync coverage
        # scan (tests/test_export_consistency.py) derives a pass's written fields
        # from exactly this `poi["<field>"] = ...` shape.
        poi["queue_class"] = record["queue_class"]
        poi["queue_minutes_peak"] = record["queue_minutes_peak"]
        poi["queue_minutes_offpeak"] = record["queue_minutes_offpeak"]
        poi["queue_peak_hours"] = record["queue_peak_hours"]
        poi["queue_basis"] = record["queue_basis"]

    dump_pois(path, pois, original)
    print(f"\n✓ wrote queue pricing for {len(priced)} POIs to {path.relative_to(ROOT)}")
    if errors:
        print(f"  {len(errors)} still unpriced — re-run this target to pick up just those.")
    print(f"  NEXT, AND MANDATORY: make sync-poi-exports SLUG={args.slug} — fields")
    print("  written here do not reach the graph until that sync runs.")
    # Non-zero while anything is unpriced, same as every sibling pass: a pass that
    # silently exits 0 with gaps is how a partial corpus gets treated as finished.
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
