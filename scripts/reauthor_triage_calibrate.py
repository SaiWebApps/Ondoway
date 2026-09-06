"""Score the conflict gate against defects it did not see, and report its miss rate.

The conflict gate was designed while two real conflicts were on screen, so catching
those two is not evidence about it. A gate whose miss rate has never been measured
is exactly what LEARNINGS #24 forbids quoting.

The labelled set is made rather than found. Every candidate whose `body_before`
already agrees with its `source_passage` is a known-clean case; planting one changed
number in that body makes a known-conflicted case out of it, and the gate's recall is
the share of plants it finds. Nothing is planted in `source_passage`, so the injected
defect has the shape the real one has: the shipped body disagreeing with the source
that the rewrite was told to treat as truth.

Four channels are injected separately, because a single recall number hides which
kind of disagreement escapes:

- `digit` — a figure changed to another figure, the shape of the Pier 59/60 case;
- `word-number` — a figure changed to its spelled-out form, which the gate reads as
  a word and cannot compare;
- `name` — one token of a proper name substituted, the Charles Dickens/Darwin shape;
- `unit-drop` — a figure changed with its neighbouring noun removed, so the slot the
  comparison needs is gone.

**This measures misses, and cannot measure false alarms.** The baseline is defined as
"records the gate passes", so counting how many of them it fires on would always
answer zero. Precision is established by reading what the gate flagged on the real
files, which is a hand audit, not a number this script can produce.

Free and deterministic. Run as
`uv run python scripts/reauthor_triage_calibrate.py --city paris`.
"""

from __future__ import annotations

import argparse
import json
import re
from typing import Any

from scripts.reauthor_preview import EXCLUDED_CITIES
from scripts.reauthor_review import load_candidates
from scripts.reauthor_triage import name_conflicts, numeric_conflicts

#: Spelled-out forms for the word-number channel, which exists to size a known
#: false-negative rather than to be passed.
_SPELLED = {
    "1": "one",
    "2": "two",
    "3": "three",
    "4": "four",
    "5": "five",
    "6": "six",
    "7": "seven",
    "8": "eight",
    "9": "nine",
    "10": "ten",
    "11": "eleven",
    "12": "twelve",
    "20": "twenty",
    "25": "twenty-five",
    "30": "thirty",
    "40": "forty",
    "50": "fifty",
    "54": "fifty-four",
    "59": "fifty-nine",
    "60": "sixty",
    "100": "a hundred",
}


def conflicted(source: str, before: str) -> bool:
    """Whether the gate refuses this pair auto-approval."""
    return bool(numeric_conflicts(source, before) or name_conflicts(source, before))


def _first_number(text: str) -> re.Match[str] | None:
    """The first standalone figure with a word beside it — the shape a slot needs."""
    for match in re.finditer(r"(?<![\w,.])(\d{1,4}(?:,\d{3})*)(?![\w,.])", text):
        before, after = text[: match.start()], text[match.end() :]
        if re.search(r"[A-Za-z]\s*$", before) or re.match(r"\s*[A-Za-z]", after):
            return match
    return None


def inject_digit(source: str, before: str) -> str | None:
    """Change a figure to a different figure, keeping the words around it."""
    match = _first_number(before)
    if match is None:
        return None
    digits = match.group(1)
    bumped = str(int(digits.replace(",", "")) + 7)
    return before[: match.start(1)] + bumped + before[match.end(1) :]


def inject_word_number(source: str, before: str) -> str | None:
    """Change a figure to its spelled-out form — a known miss, sized here."""
    match = _first_number(before)
    if match is None or match.group(1) not in _SPELLED:
        return None
    return before[: match.start(1)] + _SPELLED[match.group(1)] + before[match.end(1) :]


def inject_unit_drop(source: str, before: str) -> str | None:
    """Change a figure and delete the word beside it, removing the slot itself."""
    match = _first_number(before)
    if match is None:
        return None
    digits = match.group(1)
    bumped = str(int(digits.replace(",", "")) + 7)
    head, tail = before[: match.start(1)], before[match.end(1) :]
    trimmed = re.sub(r"\s+\w+", "", tail, count=1) if re.match(r"\s+\w", tail) else tail
    return head + bumped + trimmed


def inject_name(source: str, before: str) -> str | None:
    """Substitute one token of a two-word proper name, as a misremembered name is."""
    for match in re.finditer(r"\b([A-Z][a-z]{2,})\s+([A-Z][a-z]{2,})\b", before):
        second = match.group(2)
        swapped = second[:-2] + ("kx" if second[-2:] != "kx" else "zq")
        return before[: match.start(2)] + swapped + before[match.end(2) :]
    return None


CHANNELS = {
    "digit": inject_digit,
    "word-number": inject_word_number,
    "unit-drop": inject_unit_drop,
    "name": inject_name,
}


def calibrate(records: list[dict]) -> dict[str, Any]:
    """Recall per injection channel, over the records the gate currently passes.

    Only records the gate already calls clean are eligible: injecting into a pair the
    gate would refuse anyway would score the plant as caught when nothing was.
    """
    clean = [
        r
        for r in records
        if (r.get("source_passage") or "").strip()
        and (r.get("body_before") or "").strip()
        and not conflicted(r["source_passage"], r["body_before"])
    ]
    result: dict[str, Any] = {
        "records": len(records),
        "clean_baseline": len(clean),
        "channels": {},
        "misses": {},
    }
    for name, inject in CHANNELS.items():
        caught, planted, missed = 0, 0, []
        for record in clean:
            mutated = inject(record["source_passage"], record["body_before"])
            if mutated is None or mutated == record["body_before"]:
                continue
            planted += 1
            if conflicted(record["source_passage"], mutated):
                caught += 1
            elif len(missed) < 3:
                missed.append(
                    {"beat_id": record.get("beat_id", ""), "body_after_injection": mutated[:160]}
                )
        result["channels"][name] = {
            "planted": planted,
            "caught": caught,
            "recall_pct": round(100 * caught / planted, 1) if planted else None,
            "false_negative_pct": round(100 * (planted - caught) / planted, 1) if planted else None,
        }
        result["misses"][name] = missed
    return result


def render(city_slug: str, result: dict[str, Any]) -> str:
    lines = [
        f"{city_slug} — conflict-gate calibration by defect injection",
        f"  clean baseline      {result['clean_baseline']} of {result['records']} records "
        f"the gate already passes",
        "  precision           not measurable here — the baseline IS what the gate passes;"
        " read the flagged rows instead",
    ]
    for name, cell in result["channels"].items():
        if not cell["planted"]:
            lines.append(f"  {name:18s}  no record admitted this injection")
            continue
        lines.append(
            f"  {name:18s}  {cell['caught']}/{cell['planted']} caught "
            f"— recall {cell['recall_pct']}%, misses {cell['false_negative_pct']}%"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--city", default="paris", help="City slug under data/ (default paris).")
    parser.add_argument("--json", action="store_true", help="Emit the calibration as JSON.")
    args = parser.parse_args(argv)

    if args.city.lower() in EXCLUDED_CITIES:
        print(
            f"✗ {args.city} is excluded from re-author work; it has no candidates to calibrate on."
        )
        return 2

    records = load_candidates(args.city)
    if not records:
        print(f"✗ no data/{args.city}/reauthored.json — nothing to calibrate against.")
        return 1

    result = calibrate(records)
    print(
        json.dumps(result, indent=2, ensure_ascii=False) if args.json else render(args.city, result)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
