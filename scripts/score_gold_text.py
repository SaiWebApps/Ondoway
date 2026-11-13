"""Score the owner's own GOLD TEXT with the real rubric. $0, no DB, no provider.

WHY
---
``make score-saved-tours`` measured 191 real tours and found two WARN checks — C9 (mean
sentence length) and C10 (look-cue opener) — firing on **100%** of them. A check that never
passes discriminates nothing.

BOTH ARE NOW RESOLVED, and in OPPOSITE directions (2026-07-27) — this script is what
settled which was which. C9's cap was retuned 15 -> 20, because C9 ranked the gold above
34 of 38 machine stops and only its threshold was wrong. C10 was DELETED, because it was a
regex over 18 sentence-initial verbs that rated the gold's own opener "a bare fact".
Openers are now UNCHECKED until G1 ships — see
tests/test_tour_quality_rubric.py::test_openers_are_unchecked_since_c10_was_deleted.

There are two possible explanations demanding opposite fixes:

  1. every generated tour really is worse than the bar, or
  2. the bar sits somewhere the owner's OWN gold text cannot reach either.

This settles it by running the SAME rubric over the gold text in
``fixtures/tour-quality-standard/01-standard.md`` §1 — the passage the owner wrote
by hand and from which S1-S10 were derived. **A check the gold fails is mis-calibrated.**

READ THIS BEFORE QUOTING A NUMBER
----------------------------------
**Failing a check and being WORSE THAN MACHINE OUTPUT are different questions**, and
conflating them already produced one wrong recommendation here. The gold fails C9 — but it
also ranks BETTER than 34 of 38 machine stops on the very same measure. So C9's THRESHOLD
is wrong while C9 itself orders the two correctly. Deleting it would have thrown away a
working signal. Use ``--compare`` to print that ranking alongside the verdict; never quote
the pass/fail line on its own.

Related trap: ``mean_sentence_words`` is what C9 reads, and it is NOT total words divided
by total sentences. An earlier version of this script printed the naive figure in its
header and the rubric's figure two lines below, and let the reader pick. Only the rubric's
number appears now.

HOW IT SCORES
-------------
The gold is one continuous narration at one place, so it is rehydrated into a single-stop
``Script`` and passed to the REAL ``quality_rubric.score_tour``. Nothing is
re-implemented — an earlier version hand-rolled C5 and diverged from it (the real one
lowercases and skips sentences under five words).

Some checks cannot mean anything for a single passage of prose and are filtered out and
listed as NOT APPLICABLE rather than reported as passes:

  * C4-imbalance — one stop is trivially 100% of the tour
  * C3-thin / C7 / C7b — need a requested duration, a route and walking legs
  * C1-starved — needs the live corpus map
  * C2-tier-inversion, C12-stops-too-close, C11-year-density-outlier — need >1 stop or POI

What genuinely IS measured: **C5** (verbatim repetition), **C6** (empty stop), **C8**
(gorging) and **C9**. C10 no longer exists.

The gold text is extracted STRUCTURALLY — the markdown blockquote under the §1 heading,
matched on line prefixes, never a pattern match. See ``extract_gold_text``.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import glob
import json
import statistics as st
from pathlib import Path

from src.tour.contract import (
    POI,
    Route,
    Script,
    ScriptPOI,
    Sentence,
    TourInput,
    ValidationReport,
)
from src.tour.generation import split_sentences
from src.tour.narration_quality import score_narration
from src.tour.quality_rubric import Severity, score_tour

REPO_ROOT = Path(__file__).resolve().parent.parent
#: The owner's own gold passage. This script READS it, so it is a fixture and
#: lives under `fixtures/` — the spec tree was deleted 2026-09-02 and is refused by
#: the junk guard.
STANDARD = REPO_ROOT / "fixtures" / "tour-quality-standard" / "01-standard.md"
GOLD_HEADING = "## 1. The North Star"

#: c987848 fixed the audio clock; tours before it carry a meaningless audio number.
AUDIO_CLOCK_FIXED = _dt.datetime.fromisoformat("2026-07-19T00:00:00+00:00")

#: Checks a single passage of prose structurally cannot exercise. Filtered from the
#: verdict and printed as NOT APPLICABLE — never silently counted as a pass.
NOT_APPLICABLE = {
    "C1-starved": "needs the corpus map (beats available per POI)",
    "C2-tier-inversion": "needs route vignettes; Script does not persist them",
    "C3-thin": "total_audio_seconds is a fabricated 0 here, so C3 would measure the "
    "placeholder, not the gold",
    "C4-imbalance": "one stop is trivially 100% of its own tour",
    "C7-time-budget": "needs a route and a walking budget",
    "C7b-leg-audio-overruns-walk": "needs per-leg transit segments",
    "C11-year-density-outlier": "relative to the tour's own mean; needs >1 stop",
    "C12-stops-too-close": "needs consecutive stops to measure between",
}


def extract_gold_text(path: Path = STANDARD) -> str:
    """Pull the North Star blockquote out of the standard, by document structure only.

    Walks lines: find the North Star heading, then take every markdown blockquote line until the
    next heading. No pattern matching — a prefix test and a slice, so an empty result is
    impossible to mistake for a match.
    """
    lines = path.read_text().splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.startswith(GOLD_HEADING))
    except StopIteration as exc:
        raise SystemExit(f"'{GOLD_HEADING}' not found in {path}") from exc

    quoted: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("#"):
            break
        if line.startswith(">"):
            quoted.append(line[1:].strip())

    if not quoted:
        raise SystemExit(f"No blockquote found under '{GOLD_HEADING}' in {path}")

    return " ".join(part for part in quoted if part)


def _gold_as_script(text: str) -> Script:
    """The gold rehydrated as a one-stop Script, so the REAL rubric can score it."""
    sentences = [s for s in (p.strip() for p in split_sentences(text)) if s]
    return Script(
        city_slug="paris",
        generated_at=_dt.datetime(2026, 7, 15, tzinfo=_dt.UTC).isoformat(),
        inputs=TourInput(
            start=(48.8656, 2.3281), duration_min=60, city_slug="paris", round_trip=False
        ),
        total_audio_seconds=0,
        total_walking_seconds=0,
        total_walk_distance_m=0,
        total_planned_seconds=0,
        selected_pois=(
            ScriptPOI(
                id="gold", name="Hôtel Le Meurice", tier=5, lat=48.8656, lng=2.3281,
                area="Tuileries",
            ),
        ),
        lens_coverage={},
        script=tuple(
            Sentence(text=s, source_id="gold-beat", source_type="beat", stop_idx=0)
            for s in sentences
        ),
        validation=ValidationReport(),
    )


def _minimal_route() -> Route:
    return Route(
        pois=(
            POI(
                id="gold", name="Hôtel Le Meurice", tier=5, poi_role="anchor",
                lat=48.8656, lng=2.3281,
            ),
        ),
        transits=(),
        total_walk_distance_m=0,
        total_walk_seconds=0,
    )


def _machine_stop_means() -> list[float]:
    """Per-stop mean sentence length across post-audio-fix saved tours.

    C9 fires PER STOP. Comparing whole-tour aggregates compresses machine numbers toward
    the gold and hides that the gold ranks better — the error this flag exists to prevent.
    """
    out: list[float] = []
    for path in sorted(glob.glob(str(REPO_ROOT / "data" / "paris" / "tours" / "*.json"))):
        try:
            script = Script.model_validate(json.loads(Path(path).read_text()))
            fresh = _dt.datetime.fromisoformat(script.generated_at) >= AUDIO_CLOCK_FIXED
        except Exception:
            continue
        if not fresh:
            continue
        by_stop: dict[int, list[str]] = {}
        for sentence in script.script:
            by_stop.setdefault(sentence.stop_idx, []).append(sentence.text)
        for texts in by_stop.values():
            joined = " ".join(texts)
            if joined.strip():
                out.append(score_narration(joined).mean_sentence_words)
    return sorted(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--compare",
        action="store_true",
        help="also rank the gold against real machine stops (recommended for C9)",
    )
    args = ap.parse_args()

    gold = extract_gold_text()
    script = _gold_as_script(gold)
    nq = score_narration(gold)
    report = score_tour(script, _minimal_route(), beats_by_poi={})

    print(f"\n{'=' * 78}\nTHE OWNER'S GOLD TEXT, scored by the real rubric")
    print(f"source: {STANDARD.relative_to(REPO_ROOT)} §1")
    print(
        f"{len(gold.split())} words, {len(script.script)} sentences, "
        f"mean_sentence_words {nq.mean_sentence_words} (the figure C9 reads)\n"
    )

    applicable = [f for f in report.findings if f.check not in NOT_APPLICABLE]
    suppressed = sorted({f.check for f in report.findings if f.check in NOT_APPLICABLE})

    if applicable:
        for finding in applicable:
            sev = "BLOCKER" if finding.severity is Severity.BLOCKER else "WARN"
            print(f"  FAIL  {finding.check:28} ({sev})  {finding.message}")
    else:
        print("  no applicable check fired")

    print(f"\n{'-' * 78}\nNOT APPLICABLE — a single passage of prose cannot exercise these")
    for check, why in NOT_APPLICABLE.items():
        fired = "  (would have fired — suppressed)" if check in suppressed else ""
        print(f"    {check:34} {why}{fired}")

    if args.compare:
        stops = _machine_stop_means()
        if stops:
            better = sum(1 for x in stops if x > nq.mean_sentence_words)
            print(f"\n{'-' * 78}\nC9 RANKING — the gold against {len(stops)} real machine stops")
            print(
                f"    gold {nq.mean_sentence_words:.2f}   "
                f"machine min {min(stops):.1f} / median {st.median(stops):.1f} / "
                f"max {max(stops):.1f}"
            )
            print(f"    the gold reads SHORTER than {better}/{len(stops)} machine stops")
            print(
                "\n    So C9 ORDERS them correctly even though the gold fails its "
                "threshold.\n    Retune the cap; do not delete the check."
            )

    failed = sorted({f.check for f in applicable})
    print(f"\n{'=' * 78}")
    if failed:
        print("THE GOLD TEXT FAILS ITS OWN BAR on: " + ", ".join(failed))
        print(
            "\nBefore acting: a failing check is EITHER a mis-set threshold OR a broken\n"
            "measure, and the two need opposite fixes. Run with --compare to see whether\n"
            "the check still ranks the gold above machine output. If it does, retune it.\n"
            "If it cannot tell them apart, replace it with the semantic check the\n"
            "standard already specifies."
        )
    else:
        print("The gold passes every applicable check. The bar is reachable.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
