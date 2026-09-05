"""Count a city's beats from the files that own them.

The owner cannot see two things about a corpus without opening it: how good the
beats are, and where the city is thin. This module answers the first half — the
fact-check mix, the length-class mix, the body sizes, and how much of each beat
was copied out of its source rather than authored.

Files are canonical here. `data/{city}/beats.json` carries `fact_check`,
`beat_length_class`, `script_body` and `source_passage`; the graph carries none
of the last two, so the report never opens a driver.

The one number with a threshold behind it is the verbatim ratio: the share of a
body's 8-word shingles that also appear in its own source passage. An 8-word run
is evidence the sentence was lifted; a 5-word run is ordinary phrasing. A beat at
or above `VERBATIM_THRESHOLD` was copied, not written.

Run as `uv run python scripts/corpus_report.py --city paris`.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

from scripts.dedup_pairs import shingle_set
from scripts.extract_validators import word_count

#: Shingle width for the copied-body test. Eight words is evidence of copying.
VERBATIM_SHINGLE: int = 8

#: At or above this share of shared shingles, a body was copied rather than written.
VERBATIM_THRESHOLD: float = 0.70

#: The bucket a beat lands in when it carries no `fact_check` block at all —
#: distinct from `unverified`, which is a check that ran and reached no verdict.
NEVER_CHECKED = "never-checked"

#: The bucket for a beat with no `beat_length_class`. Paris carries the field on
#: two thirds of its beats; the rest are not a class, and are not `micro` either.
NOT_CLASSIFIED = "not-classified"

#: Display order for the statuses the pipeline actually writes. Any other status
#: still appears in the report, after these — the report never hides a value it
#: did not expect, and `validate_beats` is what refuses one at commit.
_STATUS_ORDER = ("verified", "corrected", "unverified", "disputed", NEVER_CHECKED)

_REPO_ROOT = Path(__file__).resolve().parent.parent


def verbatim_ratio(script_body: str, source_passage: str) -> float:
    """Share of the body's 8-word shingles that also appear in its source passage.

    Returns 0.0 when either side is empty: a beat with no recorded source cannot
    be shown to have been copied from one.
    """
    body = shingle_set(script_body, VERBATIM_SHINGLE)
    if not body:
        return 0.0
    source = shingle_set(source_passage, VERBATIM_SHINGLE)
    if not source:
        return 0.0
    return len(body & source) / len(body)


def fact_check_buckets(beats: list[dict]) -> dict[str, int]:
    """Beats per `fact_check.status`, with a missing block counted separately."""
    counts: Counter[str] = Counter()
    for beat in beats:
        status = (beat.get("fact_check") or {}).get("status")
        counts[status or NEVER_CHECKED] += 1
    return _ordered(counts, _STATUS_ORDER)


def length_class_buckets(beats: list[dict]) -> dict[str, int]:
    """Beats per `beat_length_class`, with a missing class counted separately."""
    counts: Counter[str] = Counter()
    for beat in beats:
        counts[beat.get("beat_length_class") or NOT_CLASSIFIED] += 1
    return _ordered(counts, ("anchor", "mid", "seasoning", "micro", NOT_CLASSIFIED))


def body_size_summary(beats: list[dict]) -> dict[str, int]:
    """Median and p90 body length in words — the 'size' half of beat quality."""
    words = sorted(word_count(b.get("script_body") or "") for b in beats)
    if not words:
        return {"median_words": 0, "p90_words": 0, "longest_words": 0}
    return {
        "median_words": int(statistics.median(words)),
        "p90_words": words[int(len(words) * 0.9) - 1],
        "longest_words": words[-1],
    }


def verbatim_summary(beats: list[dict]) -> dict[str, Any]:
    """How many beats were copied from their source, and how copied the corpus is."""
    ratios = [
        verbatim_ratio(b.get("script_body") or "", b.get("source_passage") or "")
        for b in beats
    ]
    flagged = sum(1 for r in ratios if r >= VERBATIM_THRESHOLD)
    return {
        "flagged": flagged,
        "flagged_pct": round(100 * flagged / len(ratios), 1) if ratios else 0.0,
        "median_ratio": round(statistics.median(ratios), 3) if ratios else 0.0,
        "threshold": VERBATIM_THRESHOLD,
        "shingle": VERBATIM_SHINGLE,
    }


def quality_report(beats: list[dict]) -> dict[str, Any]:
    """The quality half of the corpus report for one city's beats."""
    return {
        "total_beats": len(beats),
        "fact_check": fact_check_buckets(beats),
        "length_class": length_class_buckets(beats),
        "body_size": body_size_summary(beats),
        "verbatim": verbatim_summary(beats),
    }


def load_city_beats(city_slug: str, *, data_dir: Path | None = None) -> list[dict]:
    """Read one city's beats.json. Raises FileNotFoundError naming the path."""
    root = data_dir if data_dir is not None else _REPO_ROOT / "data"
    path = root / city_slug / "beats.json"
    if not path.is_file():
        raise FileNotFoundError(f"no beats file for city {city_slug!r} at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _ordered(counts: Counter[str], preferred: tuple[str, ...]) -> dict[str, int]:
    """Counts in a stable reading order: known keys first, surprises after."""
    out = {key: counts[key] for key in preferred if counts[key]}
    for key in sorted(counts):
        if key not in out:
            out[key] = counts[key]
    return out


def _render(city_slug: str, report: dict[str, Any]) -> str:
    lines = [f"{city_slug} — {report['total_beats']} beats"]
    fc = "  ".join(f"{k} {v}" for k, v in report["fact_check"].items())
    lines.append(f"  fact-check   {fc}")
    lc = "  ".join(f"{k} {v}" for k, v in report["length_class"].items())
    lines.append(f"  length       {lc}")
    size = report["body_size"]
    lines.append(
        f"  body size    median {size['median_words']}w  "
        f"p90 {size['p90_words']}w  longest {size['longest_words']}w"
    )
    vb = report["verbatim"]
    lines.append(
        f"  verbatim     {vb['flagged']} beats ({vb['flagged_pct']}%) are "
        f">={int(vb['threshold'] * 100)}% copied from their source passage"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--city", default="paris", help="City slug under data/ (default paris).")
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON.")
    args = parser.parse_args(argv)

    try:
        beats = load_city_beats(args.city)
    except FileNotFoundError as exc:
        print(f"✗ {exc}")
        return 1

    report = quality_report(beats)
    print(json.dumps(report, indent=2) if args.json else _render(args.city, report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
