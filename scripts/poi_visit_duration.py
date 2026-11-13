"""Price how long a visitor spends AT each POI — the capacity pass behind /poi-visit-duration.

WHY THIS IS A SCRIPT AND NOT A PROMPT. Nineteen of the twenty-one files in
``.claude/commands/`` are prompts a model reads, with no program behind them, and
no Makefile target invokes one — Make cannot run a prompt. Two commands do have a
backing script (``beat-dedup`` -> ``scripts/beat_dedup.py``, ``tour-build`` ->
``scripts/tour_build.py``), and this follows that precedent so the pass has a
repeatable, preflighted, dry-runnable executor instead of depending on an agent
session that cannot be re-run identically.

WHAT IT PRODUCES, per POI in ``data/{slug}/poi-raw.json``:

``typical_duration_min``  How long a visitor usefully spends at the place WITHOUT
                          going inside — the forecourt, the facade, the square.
                          The field already exists on ``POICreate``
                          (``src/api/models/nodes.py:141``); this fills it. Only
                          2 of Paris's 370 entries carried it before this pass.
``visit_seconds_inside``  How long a visitor usefully spends INSIDE, including
                          any queue. ``null`` for a place with no interior — a
                          street, a bridge, a square.
``visit_basis``           One sentence that argues for both numbers, written so
                          somebody who has never been to Paris can judge it.

WHY TWO NUMBERS. One number cannot serve two visitors. One visitor gives
Sainte-Chapelle 38 minutes inside plus 28 more in the queue to get in; another gives
the same building 15 from the outside and never goes in. The pair is what lets an
interest change how long you stay, not only where you go.

QUEUES COUNT, AND THE BASIS SENTENCE RECORDS THE SPLIT (owner ruling 2026-08-06).
``visit_seconds_inside`` is the honest elapsed cost of going in, queue included,
because this release exists to make the tour's clock match the tourist's day and
Release 1 has no concept of what time of day it is — a queue excluded here is
simply lost, and every ticketed interior would quietly under-run. The basis
sentence must state the split ("28 minutes of queue plus 38 under the glass") so
Release 2's opening-hours work can separate the two without re-pricing the city.

THE NUMBERS ARE ARGUED FOR, NOT ASSERTED. ``visit_basis`` exists because nobody
reviewing this data has been to most of these places. A number with no sentence
behind it is unauditable, so this script refuses to write one.

SPEND. One model call per batch of POIs, so a full Paris pass is roughly
370 / BATCH_SIZE calls. ``--limit`` runs a subset and ``--dry-run`` writes nothing,
which is the intended way to inspect the output before committing to a full pass.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

#: The model that does the pricing. ``claude-opus-4-8`` is what this repo already
#: uses for corpus-generating work (``src/onboard/beat_draft.py:47``); the judge
#: model is deliberately not reused, because this is a judgement call about a place
#: rather than a yes/no verdict on a sentence. Overridable with ``--model``.
DEFAULT_MODEL: str = "claude-opus-4-8"

#: POIs per model call. Small enough that one bad batch is cheap to re-run and the
#: model keeps every place in view; large enough that a full pass is ~25 calls.
DEFAULT_BATCH_SIZE: int = 15

#: Ceiling on an interior visit, in seconds (90 minutes). A concrete cap rather
#: than a derived one: ``src/tour/contract.py:49`` allows any duration in
#: ``0 < duration_min <= 600``, so there is no "shortest supported tour" to derive
#: a bound from. 90 minutes is above the 66-minute Sainte-Chapelle visit, so the
#: cap rejects absurdity without rejecting the longest real visit observed.
VISIT_SECONDS_INSIDE_MAX: int = 5400

#: Floor on the outside number, in minutes. A place nobody pauses at for even two
#: minutes is a walk-past, not a stop, and 0 is the contract default that would
#: otherwise sail through unnoticed.
TYPICAL_DURATION_MIN_FLOOR: int = 2

PROMPT_HEADER = """\
You are pricing how long a visitor usefully spends at each place on a walking \
tour. Two numbers per place, and a sentence that argues for them.

READ THIS FIRST — IT IS THE MOST COMMON WAY THIS TASK GOES WRONG.

You are pricing the place's CAPACITY: how long it can absorb somebody who \
genuinely wants to be there. You are NOT pricing an average visitor's rushed \
stop. A later stage of this system takes your number and scales it DOWN for \
visitors whose interests only partly match the place. So if you hand back a \
typical hurried visit, it gets discounted a second time and every tour comes out \
starved. **Price the engaged visitor. The system does the discounting.**

CALIBRATION, from real observed visits. Match this scale:

- Sainte-Chapelle: OUTSIDE 15 min (the facade and the courtyard are modest and \
you have seen them). INSIDE 3960 s — 28 minutes of security and ticket queue \
plus 38 minutes under the glass. Not 30 minutes. Not 20.
- The Louvre's Cour Carree: OUTSIDE 33 min for somebody who came for the \
architecture — into the courtyard, round it, out to the east colonnade, then \
standing looking up. A big enclosed courtyard absorbs half an hour.
- Place de la Concorde: OUTSIDE 35 min. An open square with an obelisk still \
absorbs half an hour, because you walk between positions on it.
- Pont Neuf: OUTSIDE 12 min, INSIDE null. A bridge is a destination, not a \
corridor, but there is nothing to enter.
- The Conciergerie: INSIDE 3900 s. The medieval hall, the tribunal, the cell.
- A parish church you can walk into: INSIDE roughly 1000-1200 s.

Notice how large the outside numbers are. **Streets, squares, gardens and \
bridges are where this pass most often under-prices, and in aggregate that is \
fatal**: they are most of a city, so pricing them at 3-5 minutes each starves \
every tour while each individual row still looks defensible.

FOR EACH PLACE, RETURN:

1. `typical_duration_min` — integer MINUTES a visitor usefully spends at the place \
WITHOUT going inside. The facade, the forecourt, the square, the view. This is \
never null and never below 2: a place nobody pauses at for two minutes is \
something you walk past, not a stop. A street or a square's whole value is \
outside, so this is its real number, not a token one.

2. `visit_seconds_inside` — integer SECONDS a visitor usefully spends AT THE \
PLACE IN TOTAL when they go in: the outside time PLUS the interior PLUS any \
security or ticket queue. **It is a TOTAL, not the interior on its own**, so it \
can never be smaller than `typical_duration_min * 60` — a visitor who goes in \
also stood outside first. THE COMMON MISTAKE, and the one that gets a record \
rejected: a landmark with a spectacular exterior and a small interior. The \
Chrysler Building is fifteen minutes of looking up at the spire from the street \
and about ten in the lobby, so it is `typical_duration_min` 15 and \
`visit_seconds_inside` **1500** (900 + 600) — NOT 600. Add them. Use null when \
there is no interior to enter, or when entering is not a normal part of \
visiting: streets, bridges, squares, gardens, monuments you view from outside, \
statues, fountains. Never more than 5400 (90 minutes). **5400 is a hard \
ceiling, not a target.** A place that would \
genuinely absorb longer — a major national museum — returns 5400 AND says so in \
its basis, e.g. "would absorb three hours; capped at the 90-minute tour maximum". \
Do not drift other places up toward the ceiling to look consistent with it.

3. `visit_basis` — ONE sentence justifying both numbers, written for somebody who \
has never been to this city. Say what is physically there and how long it takes to \
see, e.g. "interior, about 30 rooms over two floors, typical visit 45-60 min plus \
a 10-min ticket queue" or "open square, one monument and a colonnade to walk \
round, nothing to enter". Never write "popular" or "worth a visit" — those argue \
nothing.

RULES THAT MATTER:

- Price the PLACE'S CAPACITY, not its fame. A small chocolate museum can absorb 90 \
minutes; a world-famous bridge absorbs 10. Importance tier is given below only so \
you know what the place is — it must NOT drive the number. This is the single \
most common way this task goes wrong.
- Include realistic queues in the inside number for places that reliably have one.
- If you do not know a place, say so in `visit_basis` and price it conservatively \
from what its name and description tell you. Do not invent specifics.
- Return STRICT JSON only: an array with one object per place, in the same order, \
each with keys `name`, `typical_duration_min`, `visit_seconds_inside`, \
`visit_basis`. No prose before or after, no markdown fence.

THE PLACES:
"""


def load_pois(path: Path) -> tuple[list[dict[str, Any]], str]:
    """Return the POI list and the file's exact original text.

    The original text is returned so :func:`dump_pois` can prove a round trip
    before writing. ``poi-raw.json`` is the canonical POI source for a city; a
    silent reformat of all 370 entries would bury this pass's real diff.
    """
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, list):
        raise SystemExit(f"✗ {path} is not a JSON array of POIs (got {type(data).__name__}).")
    return data, raw


def serialise(pois: list[dict[str, Any]]) -> str:
    """The one serialisation this file uses: 2-space indent, literal accents, no
    trailing newline. Measured against the committed file, not guessed."""
    return json.dumps(pois, indent=2, ensure_ascii=False)


def dump_pois(path: Path, pois: list[dict[str, Any]], original: str) -> None:
    """Write the POI list back, refusing if serialisation is not faithful.

    The guard: re-serialising the ORIGINAL parse must reproduce the original bytes.
    If it does not, this script's formatting convention has drifted from the file's
    and writing would produce a whole-file diff that hides the real change.
    """
    check = serialise(json.loads(original))
    if check != original:
        raise SystemExit(
            "✗ refusing to write: re-serialising poi-raw.json does not reproduce it "
            "byte for byte, so this write would reformat the whole file and bury the "
            "actual change. Fix serialise() to match the file before running again."
        )
    path.write_text(serialise(pois), encoding="utf-8")


def needs_values(poi: dict[str, Any], *, rescore: bool) -> bool:
    """True when this POI still has to be priced."""
    if rescore:
        return True
    return not (
        isinstance(poi.get("typical_duration_min"), int)
        and isinstance(poi.get("visit_basis"), str)
        and poi["visit_basis"].strip()
        and "visit_seconds_inside" in poi
    )


def describe(poi: dict[str, Any]) -> str:
    """One compact line per place for the prompt."""
    return json.dumps(
        {
            "name": poi.get("name", ""),
            "description": poi.get("short_description", ""),
            "role": poi.get("poi_role", ""),
            "importance_tier_FOR_IDENTIFICATION_ONLY": poi.get("importance_tier"),
        },
        ensure_ascii=False,
    )


def validate(record: dict[str, Any], *, name: str) -> str | None:
    """Structural check on one priced place. Returns an error string, or None.

    Deliberately structural only. Nobody reviewing this output can check Paris
    facts, so these are the properties that can be checked without knowing the
    city — the same six the pass's test module asserts over the finished file.
    """
    outside = record.get("typical_duration_min")
    inside = record.get("visit_seconds_inside")
    basis = record.get("visit_basis")

    if not isinstance(outside, int) or isinstance(outside, bool):
        return f"{name}: typical_duration_min is not an integer ({outside!r})"
    if outside < TYPICAL_DURATION_MIN_FLOOR:
        return (
            f"{name}: typical_duration_min {outside} is below the "
            f"{TYPICAL_DURATION_MIN_FLOOR}-minute floor"
        )
    if inside is not None:
        if not isinstance(inside, int) or isinstance(inside, bool):
            return f"{name}: visit_seconds_inside is neither an integer nor null ({inside!r})"
        if inside < 0:
            return f"{name}: visit_seconds_inside {inside} is negative"
        if inside > VISIT_SECONDS_INSIDE_MAX:
            return (
                f"{name}: visit_seconds_inside {inside} exceeds the "
                f"{VISIT_SECONDS_INSIDE_MAX}s cap"
            )
        if inside < outside * 60:
            return (
                f"{name}: visit_seconds_inside {inside} is below the outside number "
                f"({outside} min = {outside * 60}s); inside must absorb at least as much"
            )
    if not isinstance(basis, str) or not basis.strip():
        return f"{name}: visit_basis is empty — a number with no argument behind it is unauditable"
    return None


def records_for_batch(text: str, batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The model's reply as one record per place in ``batch``, in the batch's order.

    ONE READER FOR EVERY PASS. Capacity, opening hours, queues and place
    judgements all ask the model for the same thing — "an array, one object per
    place, in the same order" — and all four used to strip the fence, parse the
    JSON and check the count in their own copy of this block, so a fix to one
    left three unfixed. This is that block, defined once and imported.

    THE COUNT CHECK IS THE POINT, AND IT STAYS. A reply carrying a different
    number of records than the batch has no knowable alignment, and pairing
    them up anyway would stamp one place's answer onto another place's row.

    A BARE OBJECT IS READ AS ONE RECORD WHEN — AND ONLY WHEN — THE BATCH IS ONE
    PLACE. The retry path re-asks a SINGLE failed row, and a model answering a
    one-item request routinely returns the object rather than an array of one.
    With one place there is no alignment left to guess: the record can only be
    about the place that was asked about, so reading the other shape the same
    answer commonly arrives in is not guessing. At every other length, and for
    anything that is neither a list nor an object, it refuses as before.
    """
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    try:
        records = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"✗ model returned unparseable JSON: {exc}\n--- raw ---\n{text[:2000]}"
        ) from exc
    if isinstance(records, dict) and len(batch) == 1:
        return [records]
    if not isinstance(records, list) or len(records) != len(batch):
        raise SystemExit(
            f"✗ model returned {len(records) if isinstance(records, list) else '?'} records "
            f"for a batch of {len(batch)}; refusing to guess the alignment."
        )
    return records


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--slug", default="paris", help="City slug (default: paris)")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Price only the first N unpriced POIs. The dry-run knob.",
    )
    parser.add_argument(
        "--rescore",
        action="store_true",
        help="Re-price every POI, including ones already carrying values.",
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

    # ONE retry of just the failures. Over 370 places a single malformed row would
    # otherwise discard every good row before it, and this pass costs real model
    # credits — so a slip that a re-ask fixes must not cost the whole run. Exactly
    # one retry: a row that fails twice is a real problem and must be seen, not
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
        inside = record["visit_seconds_inside"]
        inside_str = "—" if inside is None else f"{inside}s ({inside // 60} min)"
        print(f"  {record['name']}")
        print(f"      outside: {record['typical_duration_min']} min    inside: {inside_str}")
        print(f"      basis:   {record['visit_basis']}")

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
        poi["typical_duration_min"] = record["typical_duration_min"]
        poi["visit_seconds_inside"] = record["visit_seconds_inside"]
        poi["visit_basis"] = record["visit_basis"]

    dump_pois(path, pois, original)
    print(f"\n✓ wrote {len(priced)} priced POIs to {path.relative_to(ROOT)}")
    if errors:
        print(f"  {len(errors)} still unpriced — re-run this target to pick up just those.")
    print(f"  NEXT, AND MANDATORY: make sync-poi-exports SLUG={args.slug} — fields")
    print("  written here do not reach the graph until that sync runs.")
    # Non-zero when anything is still unpriced: a pass that silently exits 0 with
    # gaps in it is how a partial corpus gets treated as a finished one.
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
