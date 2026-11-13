"""Judge three practical affordances of every POI — the place-judgements pass (design row 6.4).

EXTENDS ``scripts/poi_visit_duration.py`` — same corpus file, same batched audited-AI
shape, same round-trip write guard (``load_pois`` / ``dump_pois`` are IMPORTED from it,
not copied, so the refuse-to-write-on-reformat guard stays defined once) and the same
reader of a model reply (``records_for_batch``: fence, parse, alignment). It is a
separate script rather than a flag on that one because it answers a DIFFERENT QUESTION:
social and physical affordances — can a child run here, can two people sit and talk
here, is it OK to be left standing here after dark — not time. Folding it in would fuse
two prompts, two validators and two review protocols into one CLI whose halves succeed
and fail independently.

WHAT IT PRODUCES, per POI in ``data/{slug}/poi-raw.json``:

``children_can_run``  Can children be loud, fast and free-range here? A family's
                      place-minutes are most valuable where the kids can run, and the
                      corpus had no channel for it.
``sit_and_talk``      Can two people sit down somewhere quiet enough to hear each
                      other? A couple who would rather talk picks the Palais-Royal
                      garden on seating, enclosure and quiet — three properties the
                      corpus had no field for.
``good_after_dark``   Good to be LEFT STANDING there in the dark? On a dated walk
                      that finishes after dusk, the planner prefers a finishing place
                      marked true here, and discloses a dark finish when none fits.
``judgement_basis``   ONE sentence arguing all three verdicts, written so a reviewer
                      who has never been to the city can judge them.

THESE ARE AI JUDGEMENTS WITH NO EXTERNAL SOURCE — SAMPLE THEM AT TWICE THE NORMAL
RATE IN REVIEW. These three are the only data fields with no external source
behind them (no OSM tag, no observable fact): the model's verdict
is the whole provenance. The review step therefore samples these rows at twice
the rate of the externally-sourced passes, and ``judgement_basis`` exists because it is
the only audit trail a reviewer gets. This script refuses to write a verdict without it.

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

# The load/serialise/dump mechanics are the sibling pass's, imported so the
# refuse-to-write-on-reformat guard (dump_pois proves serialise() round-trips the
# original bytes before writing) lives in exactly one place, and records_for_batch
# with them so the fence strip, the parse and the alignment check are also stated
# once. DEFAULT_MODEL and DEFAULT_BATCH_SIZE ride along deliberately: both passes
# are corpus-judgement work on the same file, and retuning the model or batch
# shape should move them together, once.
try:  # run directly as `python scripts/poi_place_judgements.py` — scripts/ is sys.path[0]
    from poi_visit_duration import (
        DEFAULT_BATCH_SIZE,
        DEFAULT_MODEL,
        dump_pois,
        load_pois,
        records_for_batch,
    )
except ModuleNotFoundError:  # imported as `scripts.poi_place_judgements` (pytest)
    from scripts.poi_visit_duration import (
        DEFAULT_BATCH_SIZE,
        DEFAULT_MODEL,
        dump_pois,
        load_pois,
        records_for_batch,
    )

ROOT = Path(__file__).resolve().parent.parent

#: The three verdicts, then the sentence that argues them. Write-back, the
#: needs-judging check and the structural validator all iterate this one tuple.
JUDGEMENT_BOOLS: tuple[str, ...] = ("children_can_run", "sit_and_talk", "good_after_dark")
JUDGEMENT_FIELDS: tuple[str, ...] = (*JUDGEMENT_BOOLS, "judgement_basis")

PROMPT_HEADER = """\
You are judging three practical affordances of each place on a walking tour: can \
children run here, can two people sit and talk here, and is it OK to be left \
standing here after dark. Three booleans and one sentence that argues them.

READ THIS FIRST — IT IS THE MOST COMMON WAY THIS TASK GOES WRONG.

These are NOT ratings of how good, famous or pleasant a place is. Each is a \
narrow physical and social test, and a world-famous place fails them as easily \
as an obscure one. Judge what is physically and socially there — enclosure, \
traffic, seating, noise, gates, lighting — never reputation. The three are \
independent: any combination of true and false can be correct.

CALIBRATION, matched to real observed days:

- The Palais-Royal garden: children_can_run TRUE (fully enclosed, traffic-free \
gravel and lawns where a child can be loud and fast without danger or \
disapproval); sit_and_talk TRUE (movable metal chairs in a courtyard enclosed on \
all four sides, quiet enough to talk at normal volume); good_after_dark FALSE \
(the gates are locked at night, so nobody can be left standing inside it).
- The Pont Neuf: children_can_run FALSE (a road bridge with traffic down the \
middle and parapets straight onto the river); sit_and_talk FALSE (the half-moon \
bays have stone benches, but they sit metres from moving traffic and \
conversation is effort); good_after_dark TRUE (lit, open, central, people \
crossing at all hours).
- The Galerie Vivienne: all three FALSE — a narrow covered shopping arcade: \
glass shopfronts at child height, no free seating (café tables must be paid \
for), echoing enough that talking is effort, and locked after closing.

FOR EACH PLACE, RETURN:

1. `children_can_run` — true only where a child can be LOUD, FAST and \
FREE-RANGE: enclosed or traffic-free ground, room to move, nothing fragile at \
child height, nobody who will shush them. Open space is not enough — a child at \
full speed must not be able to end up in traffic. Interiors where quiet is \
expected — churches, galleries, museums — are false however family-friendly. A \
pavement beside traffic is false however famous the building.

2. `sit_and_talk` — true only where two people can SIT DOWN on real, free \
seating — benches, chairs, broad steps or low walls people actually sit on — \
somewhere QUIET enough to hold a conversation at normal volume. Standing room \
is false. Seating beside heavy traffic or inside an echoing hall is false. \
Seating you must pay for (a café terrace) does not count.

3. `good_after_dark` — true only where a tour could LEAVE SOMEONE STANDING at \
nightfall and they would be fine: lit, open (not locked or gated at night), \
with people normally about. This is about ENDING a tour, not nightlife: a \
deserted lane is false however safe it feels by day; a locked park is false \
however lovely inside.

4. `judgement_basis` — ONE sentence arguing all three verdicts, written for a \
reviewer who has never been to this city. Name the physical facts — enclosure, \
traffic, seating, noise, gates, lighting, crowds — never "popular" or \
"charming", which argue nothing. These judgements have NO external data source, \
so this sentence is the only audit trail a reviewer gets.

RULES THAT MATTER:

- If you do not know the place, say so in `judgement_basis` and judge \
conservatively from its name and description: default to false rather than \
inventing an affordance a family or a couple would then be routed to.
- Return STRICT JSON only: an array with one object per place, in the same \
order, each with keys `name`, `children_can_run`, `sit_and_talk`, \
`good_after_dark`, `judgement_basis`. Booleans must be JSON true/false, never \
strings. No prose before or after, no markdown fence.

THE PLACES:
"""


def needs_values(poi: dict[str, Any], *, rejudge: bool) -> bool:
    """True when this POI still has to be judged."""
    if rejudge:
        return True
    return not (
        all(isinstance(poi.get(field), bool) for field in JUDGEMENT_BOOLS)
        and isinstance(poi.get("judgement_basis"), str)
        and poi["judgement_basis"].strip()
    )


def describe(poi: dict[str, Any]) -> str:
    """One compact line per place for the prompt."""
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
    """Structural check on one judged place. Returns an error string, or None.

    Deliberately structural only, exactly as the visit-duration pass rules:
    nobody reviewing this output can check city facts, so the checks are the
    ones that need no knowledge of the city — real booleans (JSON `"true"` the
    string is a verdict nothing downstream can read) and a non-empty basis
    sentence, the same properties the pass's test module asserts over the
    finished file.
    """
    for field in JUDGEMENT_BOOLS:
        value = record.get(field)
        if not isinstance(value, bool):
            return f"{name}: {field} is not a real boolean ({value!r})"
    basis = record.get("judgement_basis")
    if not isinstance(basis, str) or not basis.strip():
        return f"{name}: judgement_basis is empty — an unargued judgement is unauditable"
    return None


def judge_batch(client: Any, model: str, batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
        help="Judge only the first N unjudged POIs. The dry-run knob: writes nothing.",
    )
    parser.add_argument(
        "--rejudge",
        action="store_true",
        help="Re-judge every POI, including ones already carrying verdicts.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the judged places and write nothing.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Default: {DEFAULT_MODEL}")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    args = parser.parse_args(argv)

    path = ROOT / "data" / args.slug / "poi-raw.json"
    if not path.exists():
        raise SystemExit(f"✗ no POI file at {path}")

    pois, original = load_pois(path)
    by_name = {p.get("name"): p for p in pois}
    todo = [p for p in pois if needs_values(p, rejudge=args.rejudge)]
    if args.limit is not None:
        todo = todo[: args.limit]

    print(f"{len(pois)} POIs in {path.relative_to(ROOT)}; {len(todo)} to judge.")
    if not todo:
        print("Nothing to do.")
        return 0

    from src.tour.anthropic_client import compose_client

    client = compose_client()

    judged: list[dict[str, Any]] = []
    failed_names: list[str] = []
    errors: list[str] = []
    for start in range(0, len(todo), args.batch_size):
        batch = todo[start : start + args.batch_size]
        print(f"  judging {start + 1}-{start + len(batch)} of {len(todo)} …", flush=True)
        for record in judge_batch(client, args.model, batch):
            name = record.get("name", "(unnamed)")
            problem = validate(record, name=name)
            if problem:
                errors.append(problem)
                failed_names.append(name)
                continue
            judged.append(record)

    # ONE retry of just the failures — the visit-duration pass's rule, for the
    # same reason: over 370 places a single malformed row must not cost the whole
    # paid run, and a row that fails twice is a real problem that must be seen,
    # not ground away at.
    if failed_names:
        retry = [p for p in todo if p.get("name") in set(failed_names)]
        print(f"\n  retrying {len(retry)} record(s) that failed the structural check …", flush=True)
        errors = []
        for start in range(0, len(retry), args.batch_size):
            batch = retry[start : start + args.batch_size]
            for record in judge_batch(client, args.model, batch):
                name = record.get("name", "(unnamed)")
                problem = validate(record, name=name)
                if problem:
                    errors.append(f"(after one retry) {problem}")
                    continue
                judged.append(record)

    print()
    for record in judged:
        verdicts = "   ".join(
            f"{label}: {'yes' if record[field] else 'no'}"
            for field, label in zip(JUDGEMENT_BOOLS, ("run", "sit+talk", "after-dark"), strict=True)
        )
        print(f"  {record['name']}")
        print(f"      {verdicts}")
        print(f"      basis: {record['judgement_basis']}")

    if errors:
        print(f"\n✗ {len(errors)} record(s) failed the structural check twice:", file=sys.stderr)
        for problem in errors:
            print(f"    {problem}", file=sys.stderr)
        print(
            "  These are NOT written. Everything that passed still is, so re-running "
            "this target judges exactly the ones still missing.",
            file=sys.stderr,
        )

    if args.dry_run or args.limit is not None:
        print(f"\nDry run ({len(judged)} judged). Nothing written.")
        return 1 if errors else 0

    for record in judged:
        poi = by_name.get(record["name"])
        if poi is None:
            print(f"✗ model returned an unknown place: {record['name']!r}", file=sys.stderr)
            return 1
        for field in JUDGEMENT_FIELDS:
            poi[field] = record[field]

    dump_pois(path, pois, original)
    print(f"\n✓ wrote {len(judged)} judged POIs to {path.relative_to(ROOT)}")
    if errors:
        print(f"  {len(errors)} still unjudged — re-run this target to pick up just those.")
    print(f"  NEXT, AND MANDATORY: make sync-poi-exports SLUG={args.slug} — fields")
    print("  written here do not reach the graph until that sync happens, then")
    print("  prove it: make test-file FILE=tests/test_export_consistency.py")
    # Non-zero when anything is still unjudged: a pass that silently exits 0 with
    # gaps in it is how a partial corpus gets treated as a finished one.
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
