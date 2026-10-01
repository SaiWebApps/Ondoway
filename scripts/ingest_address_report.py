"""Street addresses a new-shape beats file's narrations voice — `make ingest-address-report
FILE=`. $0, files only.

OWNER RULING 2026-09-30: narration never says a street address unless the story turns on
it (Baker Street in a Sherlock Holmes beat). P4 carries that as a prompt rule, not a
refusing gate, so this report is how a real run shows whether it holds. Story beats and
`practicalities` beats (never published) are counted apart; each flagged beat is listed
so a person can tell the Baker Street kind from the plain location line, with what the P5
check (`gates.place_address_sentences`) does to it: `p5=refuses` (a bare location line,
rewritten out) or `p5=keeps` (an address the story's own claims carry).

    make ingest-address-report FILE=data-ingest/sandboxes/<sb>/new_york/beats.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.ingest import gates, judge_narration


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", type=Path, required=True, help="a new-shape beats.json")
    args = parser.parse_args(argv)

    beats = json.loads(args.file.read_text(encoding="utf-8"))
    story = practical = 0
    for beat in beats:
        found = gates.street_addresses((beat.get("narration") or {}).get("text") or "")
        if not found:
            continue
        is_practical = beat.get("beat_type") == "practicalities"
        practical += is_practical
        story += not is_practical
        label = "PRACTICAL" if is_practical else "STORY"
        claims = [c["text"] for c in beat.get("claims") or [] if c.get("status") == "resolved"]
        told = judge_narration.sentences(beat["narration"]["text"])
        verdict = "refuses" if gates.place_address_sentences(told, claims) else "keeps"
        print(f"{label} {beat.get('story_slug')}: {' | '.join(found)} p5={verdict}")
    print(
        f"beats={len(beats)} story_with_address={story} practicalities_with_address={practical}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
