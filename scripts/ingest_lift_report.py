"""Narration lift report over a new-shape beats file — `make ingest-lift-report FILE=`. $0.

For every beat: the longest run its narration shares with any of its claims'
sources — the chunks its claims cite, or their spans where a chunk is missing
(a cited span can stop mid-sentence) — outside attributed quotation, whether
that whole run is a proper
name, how many ordinary words it carries beside names (a run with no more than
`model.NAME_RUN_ORDINARY_WORDS_MAX`, and no longer than
`model.NAME_RUN_WORDS_MAX` words, is exempt — owner rulings 2026-09-13 and
2026-10-11), and how many beats the narration gate would refuse at `--run`
words with and without the exemption.
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

from pydantic import ValidationError

from scripts.verbatim import run_outside_quotation
from src.ingest import gates, model, narrate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", type=Path, required=True, help="a new-shape beats.json")
    parser.add_argument(
        "--run", type=int, default=narrate.NARRATION_LIFT_GATE_RUN,
        help="the run length to count refusals at (default: the narration gate's)",
    )
    parser.add_argument(
        "--books-root", type=Path, default=ROOT / "Books",
        help="where the chunks live: each beat is measured against the chunks its claims "
        "cite, as the P4 gate and the validator do, and against its spans where a chunk "
        "is missing",
    )
    args = parser.parse_args(argv)

    beats = json.loads(args.file.read_text(encoding="utf-8"))
    at_or_over = with_exemption = without = 0
    for beat in beats:
        text = (beat.get("narration") or {}).get("text") or ""
        try:
            spans = [
                source
                for _what, source in model.lift_sources(
                    model.Beat.model_validate(beat), args.books_root / beat.get("city_name", "")
                )
            ]
        except ValidationError:  # a legacy or half-written record: its spans, as read
            spans = [s["span"] for c in beat.get("claims") or [] for s in c.get("sources") or []]
        longest = {"length": 0, "text": ""}
        for span in spans:
            found = run_outside_quotation(text, span)
            if found["length"] > longest["length"]:
                longest = found
        cased = model._cased_run(longest["text"], text) if longest["text"] else None
        is_name = bool(cased and gates.is_proper_name(cased.group(0)))
        ordinary = model.ordinary_words(cased.group(0)) if cased else 0
        refused = any(
            gates.lift(text, span, run_length=args.run, context=spans) is not None
            for span in spans
        )
        over = longest["length"] >= args.run
        at_or_over += over
        without += over
        with_exemption += refused
        print(
            f"{beat.get('story_slug')} longest={longest['length']} "
            f"name={'yes' if is_name else 'no'} ordinary={ordinary} "
            f"refused={'yes' if refused else 'no'} "
            f"run={longest['text']!r}"
        )
    print(
        f"beats={len(beats)} at_or_over={at_or_over} refused_with_exemption={with_exemption} "
        f"refused_without={without} run_length={args.run}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
