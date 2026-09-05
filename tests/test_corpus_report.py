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
from pathlib import Path

import pytest

from scripts.corpus_report import (
    VERBATIM_THRESHOLD,
    anchor_readiness,
    coverage_report,
    density_summary,
    fact_check_buckets,
    length_class_buckets,
    lens_area_matrix,
    poi_area_index,
    quality_report,
    render_coverage,
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


def test_coverage_says_a_city_is_unmapped_rather_than_reporting_zeroes() -> None:
    """London has no areas generated. That is a missing input, not a corpus with no gaps."""
    report = coverage_report(
        [{"poi_name": "Tower of London", "lens": "dark_history"}],
        [{"name": "Tower of London"}],
        poi_to_area=[],
        area_names=[],
    )
    assert report["areas_mapped"] is False

    rendered = render_coverage("london", report)
    assert "gen-within-edges" in rendered
    # The lens rows would otherwise read "absent from 0 of 0 areas", which is not a finding.
    assert "0 of 0 areas" not in rendered


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

    assert "make gen-within-edges CITY=london" in rendered
    assert "make tourability CITY=london" in rendered


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
