"""The human-authored reference tours, loaded as scoreable ``Script`` objects.

WHY THIS EXISTS
---------------
``score_gold_text.py`` scores the owner's gold passage — 509 words, one stop. That
calibrates the PROSE checks and nothing else: checks that need more than one stop, a
duration, or a route report NOT APPLICABLE on a single passage, so they had never been
calibrated against anything a human approved.

``fixtures/reference-tours/`` holds two COMPLETE human-written tours,
hand-composed from the live corpus on 2026-04-26 and recorded in the goldens as the
reference for what a good tour contains. This module turns them into ``Script`` +
``Route`` pairs so the REAL ``quality_rubric.score_tour`` can run on them unchanged.
Nothing here re-implements a check or softens a threshold.

TWO GRANULARITIES — THIS IS THE WHOLE POINT
-------------------------------------------
A first version of this loader promoted EVERY narration-owning heading to a stop and
reported "0 blockers" on both tours. A judge consult killed that commit: the Île
document declares its stops explicitly (``## STOP — …``, 8 of them, matching the 8
POIs its golden fixture records), and grouped the document's own way, two stops
exceed the C8 gorge cap. The zero was an artifact of segmentation, not calibration.

So the loader now models both units, because they answer different questions:

* **stop** — the document's declared unit (a named place; what the rubric's
  ``stop_idx`` means on machine output, where a stop is a POI with one GPS trigger).
* **position** — the deepest narration-owning heading (one standing/pointing spot;
  inside a big stop the tourist WALKS between positions: facade, portal, nave…).

MEASURED 2026-07-29, unmodified rubric, both granularities:

    Île de la Cité, BY DECLARED STOP (12 sections):
        Conciergerie 901 words, Notre-Dame 1595 words -> C8-gorged FIRES on both.
        Every other section is 31-425 words.
    Île de la Cité, BY POSITION (29): widest 367 words -> zero blockers.
    Place des Vosges (no STOP markers; a circumnavigation of one square where each
    paused-at house IS the stop; 25 positions): widest 239 words -> zero blockers.
    Place des Vosges, BY LEVEL-2 SECTION — the read this loader deliberately does
    NOT take: 4 sections, one of them ``CIRCUMNAVIGATION`` at 2744 words, which
    would be a third C8 blocker. That read is wrong — nobody stands at a section
    called CIRCUMNAVIGATION; it is 22 house-by-house pauses of ~125 words each —
    but the figure is recorded here so the suppression is auditable, and the
    no-markers branch is pinned by test so the rule cannot flip silently.

The contradiction is the calibration finding, and it is deliberately NOT resolved
here: per standing position, human practice never exceeds ~370 words (the 850 cap is
generous); per named POI, humans go to 1595 when the POI has interior structure to
walk through. Whether C8's unit should be the position (needs sub-location-aware
stops in the engine) or the POI (needs a cap the reference can pass) is a product
decision. The calibration tests in ``tests/test_tour_quality_rubric.py`` pin BOTH
measurements so the decision cannot be made silently.

WHICH CHECKS THIS HARNESS ACTUALLY EXERCISES — honesty over coverage claims
---------------------------------------------------------------------------
Live here: C3 (thin), C5 (verbatim repeat), C8 (gorged), and the WARNs C4, C9, C11.
Structurally silent, and why:

    C1-starved   needs the corpus beat map; we pass ``beats_by_poi={}``
    C2-tier      needs route vignettes; the documents have none
    C6-empty     the parser drops narration-free headings, so a 0-word stop
                 cannot exist by construction
    C7 / C7b     need a planned time budget / per-leg transits; not in the documents
    C12-too-close  the documents carry no coordinates; the loader synthesizes
                 spread-out ones, so C12 is NOT calibrated here — even though the
                 Île tour contains the very adjacency (Conciergerie / Palais) that
                 C12's provenance cites. Real-coordinate calibration needs the
                 corpus, not this file.

PARSING
-------
Plain string operations only — never a regular expression. The two documents do not
share a tag vocabulary (``[PW:...]`` vs ``[RG:...]``/``[F:...]``/``[V:...]``, with
``+``-joined pairs), and a pattern written for the first silently returned ZERO
matches on the second. Structure, not pattern: a heading line opens a section, a
``>`` line is narration, a ``**[...]**`` span is a provenance marker and is removed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
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
from src.tour.generation import SPOKEN_WPM

REPO_ROOT = Path(__file__).resolve().parent.parent
#: The two hand-composed reference tours, beside the golden fixtures they
#: calibrate. This tool and the rubric tests open them by path, so they live
#: under fixtures/, not in a folder for prose.
EMPIRICAL_DIR = REPO_ROOT / "fixtures" / "reference-tours"

#: Golden-fixture key -> the document it was composed from. The fixture is the
#: provenance link (its ``_doc`` field names the document) and supplies the
#: requested duration.
REFERENCE_TOURS: dict[str, str] = {
    "pdv_round_trip_60min": "01-place-des-vosges.md",
    "ile_oneway_90min": "02-ile-de-la-cite-notre-dame.md",
}

_TERMINATORS = (". ", "? ", "! ")


@dataclass(frozen=True)
class Position:
    """One standing/pointing spot: the deepest heading that owns narration."""

    heading: str
    narration: tuple[str, ...]

    @property
    def words(self) -> int:
        return sum(len(line.split()) for line in self.narration)


@dataclass(frozen=True)
class Stop:
    """One document-declared stop (a level-2 section), holding its positions."""

    heading: str
    positions: tuple[Position, ...]

    @property
    def words(self) -> int:
        return sum(p.words for p in self.positions)


@dataclass(frozen=True)
class HumanTour:
    """One human-authored reference tour, scoreable at either granularity."""

    key: str
    document: Path
    duration_min: int
    #: True when the document declares stops explicitly (``## STOP — …``). When
    #: False (Place des Vosges), each position IS a stop and the two views agree.
    has_stop_markers: bool
    stops: tuple[Stop, ...]

    @property
    def positions(self) -> tuple[Position, ...]:
        return tuple(p for s in self.stops for p in s.positions)

    @property
    def narration_words(self) -> int:
        return sum(s.words for s in self.stops)

    def script_by_stop(self) -> tuple[Script, Route]:
        """One rubric stop per document-declared stop — the unit ``stop_idx``
        means on machine output."""
        units = [(s.heading, [line for p in s.positions for line in p.narration])
                 for s in self.stops]
        return _build(self.key, units, self.duration_min)

    def script_by_position(self) -> tuple[Script, Route]:
        """One rubric stop per standing position."""
        units = [(p.heading, list(p.narration)) for p in self.positions]
        return _build(self.key, units, self.duration_min)


def strip_provenance_markers(line: str) -> str:
    """Remove every ``**[...]**`` span. Plain string ops — never a regular expression."""
    out = line
    while "**[" in out:
        start = out.index("**[")
        closing = out.find("]**", start)
        if closing == -1:
            break
        out = out[:start] + out[closing + len("]**") :]
    return out.strip()


def split_into_sentences(line: str) -> list[str]:
    """Split narration into sentences on terminator+space, keeping the terminator."""
    pieces = [line]
    for terminator in _TERMINATORS:
        nxt: list[str] = []
        for piece in pieces:
            parts = piece.split(terminator)
            for i, part in enumerate(parts):
                if i < len(parts) - 1:
                    part = part + terminator.strip()
                nxt.append(part)
        pieces = nxt
    return [p.strip() for p in pieces if p.strip()]


def parse_document(document: Path) -> tuple[bool, tuple[Stop, ...]]:
    """Parse into the document's own hierarchy.

    A level-2 heading (``## ``) opens a stop. Any deeper heading opens a position
    inside the current stop. Narration attached directly to a level-2 heading is
    its own position (the stop's opening spot). Headings that own no narration
    anywhere beneath them are dropped — a stop with nothing to say is not a stop.
    """
    stop_heading: str | None = None
    position_heading: str | None = None
    narration: list[str] = []
    # accumulator: [(stop_heading, [(position_heading, [lines])])]
    acc: list[tuple[str, list[tuple[str, list[str]]]]] = []

    def close_position() -> None:
        nonlocal narration
        if narration and acc:
            acc[-1][1].append((position_heading or acc[-1][0], narration))
        narration = []

    for raw in document.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("#"):
            depth = len(line) - len(line.lstrip("#"))
            heading = line[depth:].strip()
            close_position()
            if depth <= 2:
                stop_heading = heading
                position_heading = None
                acc.append((heading, []))
            else:
                position_heading = heading
            continue
        if line.startswith(">") and stop_heading is not None:
            text = strip_provenance_markers(line[1:].strip())
            if text:
                narration.append(text)
    close_position()

    stops = tuple(
        Stop(
            heading=stop_name,
            positions=tuple(
                Position(heading=pos_name, narration=tuple(lines))
                for pos_name, lines in positions
            ),
        )
        for stop_name, positions in acc
        if positions
    )
    has_markers = any(s.heading.startswith("STOP") for s in stops)
    return has_markers, stops


def _build(key: str, units: list[tuple[str, list[str]]], duration_min: int) -> tuple[Script, Route]:
    """Assemble a ``Script``/``Route`` pair the real rubric can score.

    Coordinates are synthetic (the documents carry none), spaced far apart so C12
    cannot fire on a loader artifact — which also means C12 is NOT calibrated by
    anything built here; see the module docstring.
    """
    sentences: list[Sentence] = []
    script_pois: list[ScriptPOI] = []
    route_pois: list[POI] = []

    for idx, (name, lines) in enumerate(units):
        pid = f"{key}-stop{idx}"
        lat = 48.8500 + idx * 0.0020
        lng = 2.3600 + idx * 0.0020
        script_pois.append(ScriptPOI(id=pid, name=name, tier=3, lat=lat, lng=lng))
        route_pois.append(POI(id=pid, name=name, tier=3, poi_role="stop", lat=lat, lng=lng))
        for line in lines:
            for piece in split_into_sentences(line):
                sentences.append(
                    Sentence(
                        text=piece,
                        source_id=f"{pid}-s{len(sentences)}",
                        source_type="beat",
                        stop_idx=idx,
                    )
                )

    words = sum(len(s.text.split()) for s in sentences)
    audio_seconds = int(words / SPOKEN_WPM * 60)

    script = Script(
        city_slug="paris",
        generated_at="2026-04-26T00:00:00Z",
        inputs=TourInput(start=(48.85, 2.36), duration_min=duration_min, city_slug="paris"),
        total_audio_seconds=audio_seconds,
        total_walking_seconds=600,
        total_walk_distance_m=800.0,
        total_planned_seconds=duration_min * 60,
        selected_pois=tuple(script_pois),
        lens_coverage={},
        script=tuple(sentences),
        validation=ValidationReport(),
    )
    route = Route(
        pois=tuple(route_pois),
        transits=(),
        total_walk_distance_m=800.0,
        total_walk_seconds=600,
        vignettes={},
        err_short_total_seconds=0,
    )
    return script, route


def load_human_reference_tours() -> list[HumanTour]:
    """Both reference tours, duration taken from each one's golden fixture."""
    fixtures = REPO_ROOT / "fixtures" / "tour_golden"
    tours: list[HumanTour] = []
    for key, filename in REFERENCE_TOURS.items():
        fixture = json.loads((fixtures / f"{key}.json").read_text(encoding="utf-8"))
        duration = int(fixture["input"]["duration_min"])
        has_markers, stops = parse_document(EMPIRICAL_DIR / filename)
        if not has_markers:
            # No declared stops (Place des Vosges): each position is the stop.
            stops = tuple(
                Stop(heading=p.heading, positions=(p,)) for s in stops for p in s.positions
            )
        tours.append(
            HumanTour(
                key=key,
                document=EMPIRICAL_DIR / filename,
                duration_min=duration,
                has_stop_markers=has_markers,
                stops=stops,
            )
        )
    return tours


def main() -> int:
    """Calibration report at both granularities. $0, no DB, no provider.

    Exit code reflects only the UNEXPECTED: a blocker at position granularity.
    C8 firing at Île's declared-stop granularity is the known, test-pinned
    contradiction and is reported, not treated as failure.
    """
    from src.tour.quality_rubric import score_tour

    exit_code = 0
    print("=" * 78)
    print("THE HUMAN-AUTHORED REFERENCE TOURS, scored by the real rubric")
    print("=" * 78)

    for tour in load_human_reference_tours():
        print()
        print(f"{tour.key}  ({tour.document.name})")
        print(f"  requested {tour.duration_min} min | declared stops: "
              f"{'yes' if tour.has_stop_markers else 'no — each position is the stop'}")
        print(f"  {len(tour.stops)} stops / {len(tour.positions)} positions / "
              f"{tour.narration_words} narration words")

        for label, (script, route) in (
            ("by declared stop", tour.script_by_stop()),
            ("by position", tour.script_by_position()),
        ):
            if not tour.has_stop_markers and label == "by declared stop":
                continue  # identical to by-position; printing both would mislead
            report = score_tour(script, route, beats_by_poi={})
            print(f"  [{label}]")
            print(f"    BLOCKERS: {len(report.blockers)}")
            for f in report.blockers:
                print(f"      {f.check} @ {f.poi_name}: {f.message[:96]}")
                if label == "by position":
                    exit_code = 1
            counts: dict[str, int] = {}
            for f in report.warnings:
                counts[f.check] = counts.get(f.check, 0) + 1
            for check, n in sorted(counts.items(), key=lambda kv: -kv[1]):
                print(f"    warn {check}: {n}")

    print()
    print("-" * 78)
    print("Per POSITION the reference never nears the gorge cap; per DECLARED STOP")
    print("two Île stops exceed it. Which unit C8 should police is a product")
    print("decision — pinned by the calibration tests, decided by a human.")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
