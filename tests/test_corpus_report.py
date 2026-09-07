"""The corpus report counts a city's beats from the files that own them.

The report answers two questions the owner cannot otherwise see: how good the
beats are, and where the city is thin. This file pins the quality half — the
fact-check mix, the length-class mix, and the verbatim ratio that says how much
of a beat was copied out of its source rather than authored.

The verbatim ratio is the one number with a threshold behind it: a body sharing
70% of its 8-word shingles with its source passage is copied, not written. An
8-word run is evidence of copying; a 5-word run is ordinary phrasing.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from scripts.corpus_report import (
    anchor_readiness,
    coverage_report,
    density_summary,
    fact_check_buckets,
    length_class_buckets,
    lens_area_matrix,
    out_of_band,
    poi_area_index,
    quality_report,
    render_coverage,
    thin_areas,
    verbatim_summary,
)
from scripts.verbatim import (
    VERBATIM_THRESHOLD,
    verbatim_ratio,
)

_PASSAGE = (
    "The tower was completed in 1889 for the World's Fair and was intended to "
    "stand for only twenty years before being dismantled."
)


def test_verbatim_ratio_flags_a_copied_body() -> None:
    """A body lifted straight out of the passage scores 1.0."""
    assert verbatim_ratio(_PASSAGE, _PASSAGE) == pytest.approx(1.0)
    assert verbatim_ratio(_PASSAGE, _PASSAGE) >= VERBATIM_THRESHOLD


def test_the_summary_reports_the_run_the_review_path_blocks_on() -> None:
    """The ratio describes a corpus; the longest run decides a beat, and both ship."""
    lifted = {"script_body": _PASSAGE, "source_passage": _PASSAGE}
    summary = verbatim_summary([lifted])
    assert summary["run_blocked"] == 1
    assert summary["median_run"] >= summary["run_block"]


def test_verbatim_ratio_ignores_a_rewritten_body() -> None:
    """Same facts, new sentences: below the threshold, so Lane B leaves it alone."""
    rewritten = (
        "Gustave Eiffel raised his iron frame for the exposition of 1889, and "
        "Paris fully expected to pull it down again two decades later."
    )
    assert verbatim_ratio(rewritten, _PASSAGE) < VERBATIM_THRESHOLD


def test_verbatim_ratio_of_a_beat_with_no_source_is_zero() -> None:
    """A beat carrying no source passage cannot be judged copied."""
    assert verbatim_ratio(_PASSAGE, "") == 0.0
    assert verbatim_ratio("", _PASSAGE) == 0.0


def test_missing_fact_check_block_buckets_as_never_checked() -> None:
    """London's beats carry no fact_check block at all; they are not 'unverified'."""
    beats = [
        {"fact_check": {"status": "verified"}},
        {"fact_check": {}},
        {},
    ]
    buckets = fact_check_buckets(beats)
    assert buckets["verified"] == 1
    assert buckets["never-checked"] == 2


def test_missing_length_class_buckets_as_not_classified() -> None:
    """Paris carries beat_length_class on 1068 of 1562 beats; the rest are not a class."""
    beats = [
        {"beat_length_class": "mid"},
        {"beat_length_class": ""},
        {},
    ]
    buckets = length_class_buckets(beats)
    assert buckets["mid"] == 1
    assert buckets["not-classified"] == 2


def test_paris_quality_counts_match_the_file() -> None:
    """The report's totals are the corpus's, measured against the real Paris file."""
    beats_path = Path(__file__).resolve().parents[1] / "data" / "paris" / "beats.json"
    if not beats_path.is_file():
        pytest.skip("paris corpus not present on this machine")
    beats = json.loads(beats_path.read_text(encoding="utf-8"))

    report = quality_report(beats)

    assert report["total_beats"] == 1562
    assert report["fact_check"]["verified"] == 407
    assert report["fact_check"]["disputed"] == 21
    assert report["fact_check"]["never-checked"] == 597
    # Measured this session with an 8-word shingle at a 0.70 threshold.
    assert report["verbatim"]["flagged"] == 246


def test_a_beat_with_no_source_is_untraceable_not_clean() -> None:
    """A beat that cites no source cannot be scored, so it must not read as authored.

    137 Paris beats carry no `source_passage`. Scoring them 0.0 and leaving them
    in the denominator reports them as original prose, when the truth is that
    nothing can be said about them — and an untraceable beat is its own defect.
    """
    beats = [
        {"script_body": _PASSAGE, "source_passage": _PASSAGE},
        {
            "script_body": "Wholly different prose about a wholly different place.",
            "source_passage": _PASSAGE,
        },
        {"script_body": "A beat that cites nothing at all.", "source_passage": ""},
    ]
    summary = verbatim_summary(beats)

    assert summary["untraceable"] == 1
    # The share is of what could actually be tested, not of the whole corpus.
    assert summary["scoreable"] == 2
    assert summary["flagged"] == 1
    assert summary["flagged_pct"] == 50.0


def test_length_class_reports_bodies_outside_their_declared_band() -> None:
    """A class is a claim about length; the report checks it rather than trusting it.

    All 561 London beats declare `mid` (80-200 words) with a median body of 22,
    which renders as a clean bar unless the declared class is checked against
    the word count the repo already defines.
    """
    beats = [
        {"beat_length_class": "mid", "script_body": " ".join(["w"] * 120)},
        {"beat_length_class": "mid", "script_body": "far too short"},
        {"beat_length_class": "anchor", "script_body": " ".join(["w"] * 90)},
    ]
    out = out_of_band(beats)

    assert out["count"] == 2
    assert out["classified"] == 3
    assert {r["beat_length_class"] for r in out["examples"]} == {"mid", "anchor"}


# ── Coverage: where the city is thin ────────────────────────────────────────


def test_anchor_ready_counts_pois_with_three_beats() -> None:
    """A POI anchors a tour at three beats, per density.ANCHOR_CANDIDATE_BEAT_COUNT_MIN."""
    beats = [{"poi_name": "Ready"}] * 3 + [{"poi_name": "Thin"}] * 2
    pois = [{"name": "Ready"}, {"name": "Thin"}, {"name": "Silent"}]

    readiness = anchor_readiness(beats, pois)

    assert readiness["ready"] == 1
    assert readiness["total_pois"] == 3
    # A POI with no beats at all is thin too — it is not simply absent.
    assert {"Thin", "Silent"} == {p["poi_name"] for p in readiness["thin"]}


def test_area_join_prefers_the_most_specific_non_city_area() -> None:
    """A POI sits in its district and in the city; the district is the useful answer."""
    rows = [
        {"poi_name": "Musee Carnavalet", "area_name": "Paris", "area_type": "city"},
        {
            "poi_name": "Musee Carnavalet",
            "area_name": "3rd Arrondissement",
            "area_type": "district",
        },
    ]
    assert poi_area_index(rows)["Musee Carnavalet"] == "3rd Arrondissement"


def test_area_join_ranks_by_area_type_not_by_name() -> None:
    """Pins the ranking itself.

    The obvious fixture ("3rd Arrondissement" vs "Paris") is satisfied by plain
    alphabetical order, so it cannot tell a real priority from no priority at
    all. Here the alphabetically-first row is the city, so only a rank that
    reads `area_type` picks the district.
    """
    rows = [
        {"poi_name": "P", "area_name": "Aaa City", "area_type": "city"},
        {"poi_name": "P", "area_name": "Zzz District", "area_type": "district"},
    ]
    assert poi_area_index(rows)["P"] == "Zzz District"


def test_thin_areas_counts_ready_and_thin_per_area() -> None:
    """`thin_areas` feeds a rendered panel, so returning nothing must fail."""
    pois = [{"name": "A"}, {"name": "B"}, {"name": "C"}]
    per_poi = Counter({"A": 3, "B": 1})
    poi_area = {"A": "Marais", "B": "Marais", "C": "Belleville"}

    rows = {row["area"]: row for row in thin_areas(pois, per_poi, poi_area)}

    assert rows["Marais"] == {"area": "Marais", "ready": 1, "thin": 1}
    # C has no beats at all and still belongs to its area.
    assert rows["Belleville"] == {"area": "Belleville", "ready": 0, "thin": 1}


def test_area_join_falls_back_to_city_when_that_is_all_there_is() -> None:
    """A POI mapped only to the city still resolves; it is not dropped from coverage."""
    rows = [{"poi_name": "Lone", "area_name": "Paris", "area_type": "city"}]
    assert poi_area_index(rows)["Lone"] == "Paris"


def test_lens_area_cell_is_zero_not_missing() -> None:
    """An empty cell is the finding. A lens with no beats in an area reads 0, not absent."""
    beats = [{"poi_name": "P", "lens": "dark_history"}]
    matrix = lens_area_matrix(beats, {"P": "3rd Arrondissement"}, ["3rd Arrondissement"])

    assert matrix["dark_history"]["3rd Arrondissement"] == 1
    # street_art is a taggable lens with no beats here — the cell must exist and be 0.
    assert matrix["street_art"]["3rd Arrondissement"] == 0


def test_a_city_with_no_areas_defined_is_not_told_to_generate_edges() -> None:
    """London's areas.json is empty, and `make gen-within-edges` exits 1 on that.

    No areas DEFINED and no areas MAPPED are different failures with different
    remedies. Printing the edge-generator command for the first one hands the
    owner something that cannot work.
    """
    report = coverage_report(
        [{"poi_name": "Tower of London", "lens": "dark_history"}],
        [{"name": "Tower of London"}],
        poi_to_area=[],
        area_names=[],
    )
    assert report["areas_defined"] is False
    assert report["areas_mapped"] is False

    rendered = render_coverage("london", report)
    assert "gen-within-edges" not in rendered
    assert "areas.json" in rendered
    # The lens rows would otherwise read "absent from 0 of 0 areas", which is not a finding.
    assert "0 of 0 areas" not in rendered


def test_a_city_with_areas_but_no_edges_is_told_to_generate_them() -> None:
    """Areas defined but never joined to POIs is exactly what gen-within-edges fixes."""
    report = coverage_report(
        [{"poi_name": "Tower of London", "lens": "dark_history"}],
        [{"name": "Tower of London"}],
        poi_to_area=[],
        area_names=["Southwark"],
    )
    assert report["areas_defined"] is True
    assert report["areas_mapped"] is False
    assert "make gen-within-edges CITY=london" in render_coverage("london", report)


# ── Density: the map where there is one, the command where there is not ─────


def test_density_panel_reports_absent_map_as_absent(tmp_path: Path) -> None:
    """No map is a missing artifact, not a city with no density."""
    (tmp_path / "london").mkdir()
    summary = density_summary("london", data_dir=tmp_path)

    assert summary["generated"] is False
    # The panel must hand the owner the command, not a blank map.
    assert summary["command"] == "make tourability CITY=london"


def test_density_line_survives_a_city_with_no_areas() -> None:
    """Areas and the density map are different artifacts; one missing must not hide the other."""
    report = coverage_report([], [], poi_to_area=[], area_names=[])
    report["density"] = {"generated": False, "command": "make tourability CITY=london"}

    rendered = render_coverage("london", report)

    # Whatever the area state says, the density line is a separate artifact and
    # must still be reported — that is the regression this test exists for.
    assert "make tourability CITY=london" in rendered
    assert "areas" in rendered


def test_density_summary_counts_the_status_mix() -> None:
    """The panel summarises the 14MB artifact; it never ships it to the browser."""
    city_dir = Path(__file__).resolve().parents[1] / "data" / "paris"
    if not (city_dir / "tourability_map.json").is_file():
        pytest.skip("paris tourability map not generated on this machine")

    summary = density_summary("paris")

    assert summary["generated"] is True
    assert summary["cells"] == 11205
    rt60 = next(b for b in summary["buckets"] if b["key"] == "60min_round_trip")
    # Measured this session: 48 GREEN, 288 YELLOW, 10869 RED.
    assert (rt60["green"], rt60["yellow"], rt60["red"]) == (48, 288, 10869)
    assert summary["generated_at"].startswith("2026-04-30")


def test_paris_area_join_covers_every_poi() -> None:
    """Every POI carrying beats resolves to an area, or is a recorded orphan."""
    city_dir = Path(__file__).resolve().parents[1] / "data" / "paris"
    if not (city_dir / "within_edges.json").is_file():
        pytest.skip("paris corpus not present on this machine")
    edges = json.loads((city_dir / "within_edges.json").read_text(encoding="utf-8"))
    beats = json.loads((city_dir / "beats.json").read_text(encoding="utf-8"))

    index = poi_area_index(edges["poi_to_area"])
    orphans = set(edges["_meta"].get("orphans") or [])
    unresolved = {b["poi_name"] for b in beats if b["poi_name"] not in index} - orphans

    assert unresolved == set()
