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
import re
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

from scripts.dedup_pairs import shingle_set
from scripts.extract_validators import _CLASS_RANGES, word_count
from src.schema.definitions import TAGGABLE_LENSES
from src.tour.density import ANCHOR_CANDIDATE_BEAT_COUNT_MIN
from src.tour.selection import area_type_rank

#: Shingle width for the copied-body test. Eight words is evidence of copying.
VERBATIM_SHINGLE: int = 8

#: At or above this share of shared shingles, a body was copied rather than written.
VERBATIM_THRESHOLD: float = 0.70

#: A shared run of this many words is a lift. Same eight words as VERBATIM_SHINGLE,
#: named separately because it gates a decision while the shingle width only sizes a
#: ratio, and the two would otherwise be one constant serving two jobs.
VERBATIM_RUN_BLOCK: int = 8

#: The word contract for the run measure: digits and letters, case-folded, everything
#: else a boundary. Punctuation, apostrophes and hyphens do not survive it, so
#: "dog-barbers" is two words and "city's" is two. Deliberately looser than
#: `shingle_set`'s whitespace split, which keeps punctuation glued to a word and so
#: reads a lift as unshared whenever a comma moved.
_VERBATIM_WORD = re.compile(r"[0-9a-z\u00c0-\u024f]+")

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


def verbatim_word_spans(text: str) -> list[tuple[str, int, int]]:
    """Every word of `text` as `(word, start_char, end_char)`, case-folded.

    Positions are kept because a caller has to ask where a matched run sits — inside
    a quotation or out in the narration — and a bare word list cannot answer that.
    """
    return [(m.group(), m.start(), m.end()) for m in _VERBATIM_WORD.finditer(text.lower())]


def verbatim_words(text: str) -> list[str]:
    """The case-folded words of `text`, in order."""
    return [word for word, _, _ in verbatim_word_spans(text)]


def max_verbatim_run(script_body: str, source_passage: str) -> int:
    """Length of the longest word run the body shares with its source passage.

    This is the copying measure with a threshold behind it. `verbatim_ratio` divides
    matched shingles by body length, so one lifted clause inside a long body scores
    near zero — it describes how copied a whole body is, and answers nothing about
    whether any sentence was lifted. The longest run answers exactly that and does not
    move when the body around it grows.

    Returns 0 when either side is empty.
    """
    return longest_shared_run(verbatim_words(script_body), verbatim_words(source_passage))


def longest_shared_run(body_words: list[str], source_words: list[str]) -> int:
    """Length of the longest contiguous run `body_words` shares with `source_words`.

    Binary search on the run length: a shared run of length k implies one of length
    k-1, so the property is monotone and log(n) k-gram set builds settle it.
    """
    return shared_run_at(body_words, source_words)[0]


def shared_run_at(body_words: list[str], source_words: list[str]) -> tuple[int, int]:
    """The longest shared run as `(length, first index in body_words)`.

    Index is -1 when there is no shared run at all.
    """
    if not body_words or not source_words:
        return 0, -1

    def shares(k: int) -> bool:
        source_grams = {tuple(source_words[i : i + k]) for i in range(len(source_words) - k + 1)}
        return any(
            tuple(body_words[i : i + k]) in source_grams for i in range(len(body_words) - k + 1)
        )

    low, high = 0, min(len(body_words), len(source_words))
    while low < high:
        mid = (low + high + 1) // 2
        if shares(mid):
            low = mid
        else:
            high = mid - 1
    if low == 0:
        return 0, -1
    source_grams = {tuple(source_words[i : i + low]) for i in range(len(source_words) - low + 1)}
    for i in range(len(body_words) - low + 1):
        if tuple(body_words[i : i + low]) in source_grams:
            return low, i
    return low, -1


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


#: Quotation delimiters that a rewrite actually uses. Straight and curly doubles,
#: guillemets, and curly singles. The straight apostrophe is handled separately:
#: it is the same character as the possessive, so it needs a position test rather
#: than a character test.
_QUOTE_PAIRS = (('"', '"'), ("\u201c", "\u201d"), ("\u00ab", "\u00bb"), ("\u2018", "\u2019"))

#: A straight single quote opens a quotation only at a boundary and before a word,
#: and closes one only after a word and before a boundary. "Colette's" matches
#: neither, so a possessive never opens a span that swallows the rest of a body.
_SINGLE_OPEN = re.compile("(?:(?<=^)|(?<=[\\s(\u2014\u2013:,]))'(?=[\\w\u00c0-\u024f])")
_SINGLE_CLOSE = re.compile("(?<=[\\w\u00c0-\u024f.,!?;])'(?=$|[\\s)\u2014\u2013.,;:!?])")

_ATTRIBUTION_CUES = frozenset(
    [
        "wrote",
        "writes",
        "written",
        "write",
        "said",
        "says",
        "say",
        "saying",
        "told",
        "tells",
        "telling",
        "asked",
        "asks",
        "called",
        "calls",
        "calling",
        "recalled",
        "recalls",
        "described",
        "describes",
        "describing",
        "noted",
        "notes",
        "observed",
        "observes",
        "remarked",
        "remarks",
        "quipped",
        "declared",
        "declares",
        "dubbed",
        "termed",
        "letter",
        "diary",
        "memoir",
        "inscription",
        "inscribed",
        "motto",
        "epitaph",
        "quoted",
        "quoting",
    ]
)

#: How far either side of a quotation an attribution may sit. One clause, not a
#: paragraph: "she wrote:" leads a quote and "he told a friend" interrupts one.
_ATTRIBUTION_WINDOW = 120


def quoted_char_spans(text: str) -> list[tuple[int, int]]:
    """Character ranges of `text` that sit inside a quotation, opener to closer.

    Unpaired openers are dropped rather than run to the end of the body: a stray
    quote mark must not exempt everything after it.
    """
    spans: list[tuple[int, int]] = []
    for opener, closer in _QUOTE_PAIRS:
        if opener == closer:
            marks = [m.start() for m in re.finditer(re.escape(opener), text)]
            spans.extend((marks[i], marks[i + 1]) for i in range(0, len(marks) - 1, 2))
            continue
        depth, start = 0, 0
        for index, char in enumerate(text):
            if char == opener and depth == 0:
                depth, start = 1, index
            elif char == closer and depth == 1:
                spans.append((start, index))
                depth = 0
    opens = [m.start() for m in _SINGLE_OPEN.finditer(text)]
    closes = [m.start() for m in _SINGLE_CLOSE.finditer(text)]
    for start in opens:
        following = [c for c in closes if c > start]
        if following:
            spans.append((start, following[0]))
            closes = [c for c in closes if c > following[0]]
    return sorted(spans)


def is_attributed(text: str, span: tuple[int, int]) -> bool:
    """Whether a quotation names who said it, within a clause of either end."""
    start, end = span
    window = (
        text[max(0, start - _ATTRIBUTION_WINDOW) : start] + text[end : end + _ATTRIBUTION_WINDOW]
    )
    return any(word in _ATTRIBUTION_CUES for word in verbatim_words(window))


def attributed_quote_spans(text: str) -> list[tuple[int, int]]:
    """The quotations of `text` that name a speaker — the only exempt ones."""
    return [span for span in quoted_char_spans(text) if is_attributed(text, span)]


def unquoted_segments(text: str) -> list[list[str]]:
    """`text` as word runs, cut wherever an attributed quotation removes words.

    Cutting rather than deleting is what makes the gate read a quotation as a
    boundary: a lift that resumes on the far side of a quote is two shorter runs,
    and a genuine quotation split by "he told a friend" leaves only those four
    words behind instead of one long apparent lift straddling both halves.
    """
    exempt = attributed_quote_spans(text)
    segments: list[list[str]] = []
    current: list[str] = []
    for word, start, end in verbatim_word_spans(text):
        if any(qs <= start and end <= qe for qs, qe in exempt):
            if current:
                segments.append(current)
                current = []
            continue
        current.append(word)
    if current:
        segments.append(current)
    return segments


def run_outside_quotation(body_after: str, source_passage: str) -> dict[str, Any]:
    """The longest run the rewrite shares with its source outside an attributed quote.

    Returns the run's `length` and the `text` of the words that matched, so a
    reader can see the lift rather than a number claiming there was one.
    """
    source_words = verbatim_words(source_passage)
    best_length, best_words = 0, []
    for segment in unquoted_segments(body_after):
        length, index = shared_run_at(segment, source_words)
        if length > best_length:
            best_length, best_words = length, segment[index : index + length]
    return {"length": best_length, "text": " ".join(best_words)}


def verbatim_summary(beats: list[dict]) -> dict[str, Any]:
    """How many beats were copied from their source, and how copied the corpus is.

    Two measures, because they answer different questions and only one of them gates.
    The RATIO says how copied a whole body is; it is reported because it describes the
    corpus, and it is not a gate, because one lifted clause inside a long body scores
    near zero under it. The longest RUN outside an attributed quotation says whether
    any sentence was lifted at all, which is the question a reader is actually asking,
    and it is the number the review path blocks on.
    """
    scoreable = [b for b in beats if (b.get("source_passage") or "").strip()]
    untraceable = [b for b in beats if not (b.get("source_passage") or "").strip()]
    ratios = [
        verbatim_ratio(b.get("script_body") or "", b.get("source_passage") or "") for b in scoreable
    ]
    runs = [
        run_outside_quotation(b.get("script_body") or "", b.get("source_passage") or "")["length"]
        for b in scoreable
    ]
    blocked = sum(1 for r in runs if r >= VERBATIM_RUN_BLOCK)
    flagged = sum(1 for r in ratios if r >= VERBATIM_THRESHOLD)
    return {
        "run_blocked": blocked,
        "run_blocked_pct": round(100 * blocked / len(runs), 1) if runs else 0.0,
        "median_run": int(statistics.median(runs)) if runs else 0,
        "run_block": VERBATIM_RUN_BLOCK,
        "flagged": flagged,
        "scoreable": len(ratios),
        # A beat citing no source is not clean prose — it is a beat nothing can be
        # said about, and it stays out of the denominator rather than diluting it.
        "untraceable": len(untraceable),
        "flagged_pct": round(100 * flagged / len(ratios), 1) if ratios else 0.0,
        "median_ratio": round(statistics.median(ratios), 3) if ratios else 0.0,
        "threshold": VERBATIM_THRESHOLD,
        "shingle": VERBATIM_SHINGLE,
    }


def out_of_band(beats: list[dict]) -> dict[str, Any]:
    """Beats whose body length contradicts the class they declare.

    `beat_length_class` is a claim, not a measurement: the extractor writes it and
    nothing downstream re-checks it. Every London beat declares `mid` (80-200
    words) with a median body of 22, which reads as a cleanly classified corpus
    unless the claim is tested against `_CLASS_RANGES`, the bands the repo
    already defines for exactly this purpose.
    """
    classified = [b for b in beats if b.get("beat_length_class")]
    offenders = []
    for beat in classified:
        low, high = _CLASS_RANGES.get(beat["beat_length_class"], (0, 10**9))
        words = word_count(beat.get("script_body") or "")
        if not low <= words <= high:
            offenders.append(
                {
                    "beat_id": beat.get("beat_id", ""),
                    "beat_length_class": beat["beat_length_class"],
                    "words": words,
                    "band": [low, high],
                }
            )
    return {
        "count": len(offenders),
        "classified": len(classified),
        "pct": round(100 * len(offenders) / len(classified), 1) if classified else 0.0,
        "examples": offenders[:5],
    }


def quality_report(beats: list[dict]) -> dict[str, Any]:
    """The quality half of the corpus report for one city's beats."""
    return {
        "total_beats": len(beats),
        "fact_check": fact_check_buckets(beats),
        "length_class": length_class_buckets(beats),
        "out_of_band": out_of_band(beats),
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


def poi_area_index(poi_to_area: list[dict]) -> dict[str, str]:
    """Map each POI to its most specific area.

    A POI sits in several areas at once — its neighborhood, its district, and the
    city. Coverage is only legible at the specific end, so the city is the answer
    of last resort rather than the usual one. Ranking follows
    `SPINE_AREA_TYPE_PRIORITY`, the order the tour engine already sorts areas by,
    with an unknown type ranking last and ties broken alphabetically so the
    report is stable between runs.
    """
    by_poi: dict[str, list[dict]] = {}
    for row in poi_to_area:
        by_poi.setdefault(row["poi_name"], []).append(row)

    def rank(row: dict) -> tuple[int, str]:
        return area_type_rank(row.get("area_type", "")), row.get("area_name", "")

    return {poi: min(rows, key=rank)["area_name"] for poi, rows in by_poi.items()}


def anchor_readiness(beats: list[dict], pois: list[dict]) -> dict[str, Any]:
    """POIs carrying enough beats to hold a visitor still, and the ones that do not.

    A POI anchors a tour at `ANCHOR_CANDIDATE_BEAT_COUNT_MIN` beats. A POI with no
    beats at all is counted as thin rather than omitted: an absent POI is the most
    thin a POI can be, and leaving it out would flatter the corpus.
    """
    per_poi = Counter(b.get("poi_name", "") for b in beats)
    thin = [
        {"poi_name": poi["name"], "beats": per_poi.get(poi["name"], 0)}
        for poi in pois
        if per_poi.get(poi["name"], 0) < ANCHOR_CANDIDATE_BEAT_COUNT_MIN
    ]
    thin.sort(key=lambda row: (row["beats"], row["poi_name"]))
    return {
        "ready": len(pois) - len(thin),
        "total_pois": len(pois),
        "minimum_beats": ANCHOR_CANDIDATE_BEAT_COUNT_MIN,
        "thin": thin,
    }


def lens_area_matrix(
    beats: list[dict], poi_area: dict[str, str], area_names: list[str]
) -> dict[str, dict[str, int]]:
    """Beats per (taggable lens, area), with every cell present.

    The empty cell IS the finding — a lens absent from an area is the reason a
    visitor asking for that interest there gets someone else's tour — so every
    lens in `TAGGABLE_LENSES` is crossed with every area and a cell with no beats
    reads 0 rather than going missing.
    """
    matrix = {lens: dict.fromkeys(area_names, 0) for lens in TAGGABLE_LENSES}
    for beat in beats:
        area = poi_area.get(beat.get("poi_name", ""))
        lens = beat.get("lens", "")
        if area is None or lens not in matrix or area not in matrix[lens]:
            continue
        matrix[lens][area] += 1
    return matrix


def empty_lens_rows(matrix: dict[str, dict[str, int]]) -> list[dict[str, Any]]:
    """Lenses ranked by how many areas hold none of them — thinnest interest first."""
    rows = [
        {
            "lens": lens,
            "empty_areas": sum(1 for count in cells.values() if count == 0),
            "total_areas": len(cells),
            "beats": sum(cells.values()),
        }
        for lens, cells in matrix.items()
    ]
    rows.sort(key=lambda row: (-row["empty_areas"], row["lens"]))
    return rows


def thin_areas(
    pois: list[dict], per_poi: Counter[str], poi_area: dict[str, str]
) -> list[dict[str, Any]]:
    """Areas ranked by how few of their POIs can anchor a tour."""
    tally: dict[str, dict[str, int]] = {}
    for poi in pois:
        area = poi_area.get(poi["name"])
        if area is None:
            continue
        cell = tally.setdefault(area, {"ready": 0, "thin": 0})
        if per_poi.get(poi["name"], 0) >= ANCHOR_CANDIDATE_BEAT_COUNT_MIN:
            cell["ready"] += 1
        else:
            cell["thin"] += 1
    rows = [{"area": area, **cell} for area, cell in tally.items()]
    rows.sort(key=lambda row: (row["ready"], -row["thin"]))
    return rows


def coverage_report(
    beats: list[dict], pois: list[dict], poi_to_area: list[dict], area_names: list[str]
) -> dict[str, Any]:
    """The coverage half of the corpus report: where the city is thin."""
    poi_area = poi_area_index(poi_to_area)
    matrix = lens_area_matrix(beats, poi_area, area_names)
    return {
        # A city whose areas were never generated has no gaps to report — it has a
        # missing input. Saying "absent from 0 of 0 areas" would read as coverage.
        # Two different failures with two different remedies: a city can have no
        # area roster at all (areas.json empty), or a roster nobody has joined to
        # its POIs yet. Only the second is what gen-within-edges fixes.
        "areas_defined": bool(area_names),
        "areas_mapped": bool(poi_area),
        "anchor_readiness": anchor_readiness(beats, pois),
        "thin_areas": thin_areas(pois, Counter(b.get("poi_name", "") for b in beats), poi_area),
        "empty_lenses": empty_lens_rows(matrix),
        "lens_area_matrix": matrix,
        "unmapped_pois": sorted({p["name"] for p in pois if p["name"] not in poi_area}),
    }


def density_summary(city_slug: str, *, data_dir: Path | None = None) -> dict[str, Any]:
    """Summarise a city's pre-computed tourability map, or say it has none.

    `scripts/tourability_map.py` writes an 11,205-cell, 14MB artifact per city by
    asking the graph and the runtime density engine — the one part of this report
    that is not derivable from the beat files. Only cities that have been through
    it have a map, so a city without one is reported as a missing artifact with
    the command that makes it, never as a city whose density happens to be zero.

    The map is summarised to a status mix per bucket and never returned whole:
    the browser has no use for eleven thousand cells.
    """
    root = data_dir if data_dir is not None else _REPO_ROOT / "data"
    path = root / city_slug / "tourability_map.json"
    if not path.is_file():
        return {"generated": False, "command": f"make tourability CITY={city_slug}"}

    payload = json.loads(path.read_text(encoding="utf-8"))
    cells = payload["cells"]
    buckets = []
    for duration in payload["duration_buckets"]:
        for round_trip in payload["round_trip_modes"]:
            key = f"{duration}min_{'round_trip' if round_trip else 'one_way'}"
            mix = Counter(cell[key]["status"] for cell in cells)
            buckets.append(
                {
                    "key": key,
                    "label": f"{duration}min {'round-trip' if round_trip else 'one-way'}",
                    "green": mix["GREEN"],
                    "yellow": mix["YELLOW"],
                    "red": mix["RED"],
                    "green_pct": round(100 * mix["GREEN"] / len(cells), 1) if cells else 0.0,
                }
            )
    return {
        "generated": True,
        "generated_at": payload["generated_at"],
        "grid_resolution_m": payload["grid_resolution_m"],
        "cells": len(cells),
        "buckets": buckets,
    }


def available_cities(*, data_dir: Path | None = None) -> list[str]:
    """City slugs that have beats to report on, so no caller hardcodes the list."""
    root = data_dir if data_dir is not None else _REPO_ROOT / "data"
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir() if (d / "beats.json").is_file())


def load_city_pois(city_slug: str, *, data_dir: Path | None = None) -> list[dict]:
    """Read one city's poi-raw.json. Raises FileNotFoundError naming the path."""
    return _load_city_file(city_slug, "poi-raw.json", data_dir=data_dir)


def load_city_areas(
    city_slug: str, *, data_dir: Path | None = None
) -> tuple[list[dict], list[str]]:
    """Read one city's POI-to-area edges and the area names they may resolve to."""
    edges = _load_city_file(city_slug, "within_edges.json", data_dir=data_dir)
    areas = _load_city_file(city_slug, "areas.json", data_dir=data_dir)
    return edges["poi_to_area"], [a["name"] for a in areas]


def _load_city_file(city_slug: str, name: str, *, data_dir: Path | None = None) -> Any:
    root = data_dir if data_dir is not None else _REPO_ROOT / "data"
    path = root / city_slug / name
    if not path.is_file():
        raise FileNotFoundError(f"no {name} for city {city_slug!r} at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _density_lines(density: dict[str, Any] | None) -> list[str]:
    """The density line, which does not depend on areas being mapped.

    A city can have a density map and no areas, or areas and no map: the two
    artifacts are generated by different commands, so neither absence hides the
    other's answer.
    """
    if not density:
        return []
    if not density["generated"]:
        return [f"  density map         not generated — run: {density['command']}"]
    rt60 = next((b for b in density["buckets"] if b["key"] == "60min_round_trip"), None)
    if rt60 is None:
        return []
    return [
        f"  density map         {density['generated_at'][:10]} — 60min round-trip: "
        f"{rt60['green_pct']}% GREEN of {density['cells']} cells"
    ]


def render_coverage(city_slug: str, report: dict[str, Any]) -> str:
    """The coverage half as text. An unmapped city is named as unmapped, not as empty."""
    readiness = report["anchor_readiness"]
    lines = [
        f"{city_slug} — coverage",
        f"  anchor-ready POIs   {readiness['ready']} of {readiness['total_pois']}  "
        f"(>={readiness['minimum_beats']} beats)",
    ]
    lines.extend(_density_lines(report.get("density")))
    if not report["areas_defined"]:
        lines.append(
            f"  areas               none defined — data/{city_slug}/areas.json is empty; "
            "the city needs an area roster before coverage can be reported"
        )
        return "\n".join(lines)
    if not report["areas_mapped"]:
        lines.append(
            f"  areas               defined but not joined to POIs — run: "
            f"make gen-within-edges CITY={city_slug}"
        )
        return "\n".join(lines)
    label = "  thinnest areas    "
    for row in report["thin_areas"][:3]:
        lines.append(
            f"{label}  {row['area']} — {row['ready']} anchor-ready, "
            f"{row['thin']} POIs under {readiness['minimum_beats']} beats"
        )
        label = "                    "
    label = "  empty lens cells  "
    for row in report["empty_lenses"][:3]:
        lines.append(
            f"{label}  {row['lens']}: {row['beats']} beats, absent from "
            f"{row['empty_areas']} of {row['total_areas']} areas"
        )
        label = "                    "
    unmapped = report["unmapped_pois"]
    if unmapped:
        shown = ", ".join(unmapped[:5])
        rest = f" (+{len(unmapped) - 5} more)" if len(unmapped) > 5 else ""
        lines.append(f"  unmapped POIs       {len(unmapped)}: {shown}{rest}")
    return "\n".join(lines)


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
    oob = report["out_of_band"]
    lines.append(
        f"  mislabelled  {oob['count']} of {oob['classified']} classified beats "
        f"({oob['pct']}%) have a body outside the band their class declares"
    )
    size = report["body_size"]
    lines.append(
        f"  body size    median {size['median_words']}w  "
        f"p90 {size['p90_words']}w  longest {size['longest_words']}w"
    )
    vb = report["verbatim"]
    lines.append(
        f"  lifted       {vb['run_blocked']} of {vb['scoreable']} scoreable beats "
        f"({vb['run_blocked_pct']}%) share a {vb['run_block']}+ word run with their source "
        f"outside an attributed quotation  (median run {vb['median_run']}w)"
    )
    lines.append(
        f"  verbatim     {vb['flagged']} of {vb['scoreable']} scoreable beats "
        f"({vb['flagged_pct']}%) are >={int(vb['threshold'] * 100)}% copied from their source"
    )
    if vb["untraceable"]:
        lines.append(
            f"  untraceable  {vb['untraceable']} beats cite no source at all — "
            "they cannot be scored, and are not counted as clean"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--city", default="paris", help="City slug under data/ (default paris).")
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON.")
    parser.add_argument(
        "--coverage",
        action="store_true",
        help="Report where the city is thin instead of how good its beats are.",
    )
    args = parser.parse_args(argv)

    try:
        beats = load_city_beats(args.city)
        if args.coverage:
            pois = load_city_pois(args.city)
            poi_to_area, area_names = load_city_areas(args.city)
    except FileNotFoundError as exc:
        print(f"✗ {exc}")
        return 1

    if args.coverage:
        report = coverage_report(beats, pois, poi_to_area, area_names)
        report["density"] = density_summary(args.city)
        rendered = render_coverage(args.city, report)
    else:
        report = quality_report(beats)
        rendered = _render(args.city, report)

    print(json.dumps(report, indent=2) if args.json else rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
