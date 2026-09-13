"""Narration lift report over a new-shape beats file — `make ingest-lift-report FILE=`. $0.

For every beat: the longest run its narration shares with any of its claims'
spans (outside attributed quotation), whether that run is a proper name
(exempt under the owner ruling of 2026-09-13), and how many beats the
narration gate would refuse at `--run` words with and without the exemption.
The judge measured job 1's twenty beats by hand — ten of twenty at six words,
mostly names and titles; this reproduces that number on any file.

    make ingest-lift-report FILE=data-ingest/new_york/beats.job1-2026-09-12.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.verbatim import run_outside_quotation
from src.ingest import gates, narrate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", type=Path, required=True, help="a new-shape beats.json")
    parser.add_argument(
        "--run", type=int, default=narrate.NARRATION_LIFT_GATE_RUN,
        help="the run length to count refusals at (default: the narration gate's)",
    )
    args = parser.parse_args(argv)

    beats = json.loads(args.file.read_text(encoding="utf-8"))
    at_or_over = with_exemption = without = 0
    for beat in beats:
        text = (beat.get("narration") or {}).get("text") or ""
        spans = [s["span"] for c in beat.get("claims") or [] for s in c.get("sources") or []]
        longest = {"length": 0, "text": ""}
        for span in spans:
            found = run_outside_quotation(text, span)
            if found["length"] > longest["length"]:
                longest = found
        cased = gates._cased_run(longest["text"], text) if longest["text"] else None
        is_name = bool(cased and gates.is_proper_name(cased.group(0)))
        refused = any(
            gates.lift(text, span, run_length=args.run) is not None for span in spans
        )
        over = longest["length"] >= args.run
        at_or_over += over
        without += over
        with_exemption += refused
        print(
            f"{beat.get('story_slug')} longest={longest['length']} "
            f"name={'yes' if is_name else 'no'} refused={'yes' if refused else 'no'} "
            f"run={longest['text']!r}"
        )
    print(
        f"beats={len(beats)} at_or_over={at_or_over} refused_with_exemption={with_exemption} "
        f"refused_without={without} run_length={args.run}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
