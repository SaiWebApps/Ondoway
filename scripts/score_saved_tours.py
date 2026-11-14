"""Score every saved tour artifact against the CURRENT quality rubric. $0, no DB.

WHY THIS EXISTS
---------------
``data/{city}/tours/*.json`` holds real tours built by past runs. Each one is a
serialized ``Script``. The rubric (``quality_rubric.score_tour``) has moved on since they
were written, and nobody has ever asked it what it thinks of them. This does exactly that,
and nothing else: no generation, no provider call, no container, no Neo4j.

Run it to answer "what does today's bar actually catch on real tours?" before changing the
bar.

WHAT IS AND IS NOT MEASURED — read this before quoting a number
---------------------------------------------------------------
The saved artifact is a ``Script``. It is NOT a ``Route``, and two rubric inputs were never
persisted. Rather than reconstruct them from guesswork, the missing inputs are supplied as
EMPTY, which makes the checks that depend on them structurally inert. Those checks are
reported as NOT RUN, never as PASS — a check that could not fire is not a clean bill of
health.

  * ``beats_by_poi`` (the corpus map) is empty, so C1-starved cannot fire. It needs the
    live graph to know how many beats a POI *could* have supported. Supplying the
    artifact's own seated ``beat_ids`` instead would UNDERSTATE availability and produce a
    confidently wrong number, which is worse than a gap.
  * ``transits`` is empty, so C7b-leg-audio-overruns-walk cannot fire.
  * ``poi_role`` is not persisted on ``ScriptPOI``; a placeholder is used. Verified safe:
    ``quality_rubric`` never reads it.

The C7 time ceiling IS reconstructed, and exactly: ``routing.planned_total_seconds`` is
CALLED here, not re-derived, so the ceiling cannot drift from the engine's.
``duration_min`` comes from the persisted ``inputs``.

That helper was named ``err_short_total_seconds`` and returned a flat 0.83 of the
requested duration until 2026-08-04, when the legacy planning policy was deleted and the
nominal fraction became 1.00. THE CONSEQUENCE IS DELIBERATE AND MUST NOT BE MISREAD AS A
NO-OP RENAME: every saved tour this script scores is now judged against a 60-minute
ceiling for a 60-minute request instead of a 49.8-minute one, so C7 is about 20 percent
more permissive across the whole historical corpus. Leaving the old helper in place
would have been worse — the scorer would have kept a ceiling the engine no longer plans
to, and the "cannot drift" claim above would have become false.

THE AUDIO-CLOCK CAVEAT
----------------------
``total_audio_seconds`` did not measure audio before commit c987848 (2026-07-19): it
credited every glue sentence a flat 4s and capped beat credit at the corpus estimate. Any
artifact generated before that date carries a meaningless audio number, so C3-thin and
C7-time-budget are untrustworthy on it. Artifacts are split by ``generated_at`` and the
two cohorts are reported separately. Do not average across them.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from src.tour.contract import POI, Route, Script
from src.tour.quality_rubric import Severity, score_tour
from src.tour.routing import planned_total_seconds

REPO_ROOT = Path(__file__).resolve().parent.parent

#: c987848 rewrote _sum_audio to measure spoken words. Before this, total_audio_seconds
#: measured seating volume, not audio.
AUDIO_CLOCK_FIXED = datetime.fromisoformat("2026-07-19T00:00:00+00:00")

#: Checks that CANNOT fire given the inputs above. Reported as NOT RUN, never as PASS.
#: Getting this list WRONG is the defect this script is most prone to: an undeclared inert
#: check reads as "ran and passed". Two have already been caught here — audit it whenever
#: the reconstruction in ``_route_from_script`` changes.
INERT_CHECKS = {
    "C1-starved": "needs the live corpus map (beats available per POI)",
    "C7b-leg-audio-overruns-walk": "needs per-leg transit segments, not persisted",
    "C2-tier-inversion": "needs route vignettes; Script does not persist them",
}


def _route_from_script(script: Script) -> Route:
    """A minimal Route carrying only what the rubric reads. See module docstring."""
    return Route(
        pois=tuple(
            POI(
                id=p.id,
                name=p.name,
                tier=p.tier,
                poi_role="anchor",  # not persisted; verified unread by the rubric
                lat=p.lat,
                lng=p.lng,
                areas=(p.area,) if p.area else (),
            )
            for p in script.selected_pois
        ),
        transits=(),  # not persisted -> C7b inert
        total_walk_distance_m=script.total_walk_distance_m,
        total_walk_seconds=script.total_walking_seconds,
        err_short_total_seconds=planned_total_seconds(script.inputs.duration_min),
    )


def _load(path: Path) -> Script | None:
    try:
        return Script.model_validate(json.loads(path.read_text()))
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--city", default="paris")
    ap.add_argument("--limit", type=int, default=0, help="0 = every artifact")
    args = ap.parse_args()

    tour_dir = REPO_ROOT / "data" / args.city / "tours"
    paths = sorted(tour_dir.glob("*.json"))
    if args.limit:
        paths = paths[: args.limit]
    if not paths:
        print(f"No saved tours under {tour_dir}")
        return 1

    unreadable: list[str] = []
    rows: list[tuple[str, bool, list]] = []  # (name, audio_clock_trustworthy, findings)

    for path in paths:
        script = _load(path)
        if script is None:
            unreadable.append(path.name)
            continue
        try:
            report = score_tour(script, _route_from_script(script), beats_by_poi={})
        except Exception as exc:  # a rubric that cannot score a real tour is itself news
            unreadable.append(f"{path.name} (scoring raised {type(exc).__name__}: {exc})")
            continue
        try:
            trustworthy = datetime.fromisoformat(script.generated_at) >= AUDIO_CLOCK_FIXED
        except (ValueError, TypeError):  # naive vs tz-aware compare raises TypeError
            trustworthy = False
        rows.append((path.name, trustworthy, list(report.findings)))

    scored = len(rows)
    print(f"\n{'=' * 78}\nSCORED {scored} saved tours from {tour_dir}")
    if unreadable:
        print(f"UNREADABLE / UNSCOREABLE: {len(unreadable)}")
        for name in unreadable[:10]:
            print(f"    {name}")
    if not scored:
        return 1

    fresh = [r for r in rows if r[1]]
    stale = [r for r in rows if not r[1]]
    print(
        f"  audio clock trustworthy (built on/after 2026-07-19): {len(fresh)}\n"
        f"  audio clock BROKEN (built before):                   {len(stale)}"
        "   <- C3/C7 numbers below are meaningless for these"
    )

    def tally(subset: list[tuple[str, bool, list]], label: str) -> None:
        """Report TOURS AFFECTED per check, plus raw finding count.

        These are two different numbers and conflating them inflates the rate past 100%:
        one tour can trip the same check at several stops.
        """
        if not subset:
            return
        tours_hit: dict[str, Counter] = {"BLOCKER": Counter(), "WARN": Counter()}
        findings_total: Counter = Counter()
        clean = 0
        for _name, _ok, findings in subset:
            if not findings:
                clean += 1
            seen: dict[str, str] = {}
            for f in findings:
                findings_total[f.check] += 1
                seen[f.check] = "BLOCKER" if f.severity is Severity.BLOCKER else "WARN"
            for check, sev in seen.items():
                tours_hit[sev][check] += 1
        n = len(subset)
        print(f"\n{'-' * 78}\n{label}  (n={n})")
        print(f"  tours with ZERO findings: {clean}/{n}")
        for title in ("BLOCKER", "WARN"):
            counter = tours_hit[title]
            print(f"\n  {title}{'':26}tours affected        total findings")
            if not counter:
                print("    (none fired)")
            for check, count in counter.most_common():
                print(
                    f"    {check:34} {count:>4}/{n} ({count / n:>4.0%})"
                    f"{findings_total[check]:>15}"
                )

    tally(fresh, "COHORT A — audio clock trustworthy")
    tally(stale, "COHORT B — audio clock broken (C3/C7 unreliable)")

    print(f"\n{'-' * 78}\nCHECKS THAT COULD NOT RUN (reported as NOT RUN, not as PASS)")
    for check, why in INERT_CHECKS.items():
        print(f"    {check:34} {why}")
    print(
        "\nNot covered by this rubric at all: the eight semantic checks G1-G8 of the "
        "quality standard.\nOnly G8 (fabrication) and a non-gating G4 (semantic "
        "repetition) exist in the codebase.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
