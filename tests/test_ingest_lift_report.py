"""Tests for scripts/ingest_lift_report.py — `make ingest-lift-report FILE=`:
for every beat in a new-shape beats file, the longest run its narration
shares with any of its claims' spans, and how many beats the narration
gate would refuse at a given run length — with and without the proper-name
exemption. The judge measured job 1's twenty beats by hand (ten of twenty
at six words, mostly names); this makes that number reproducible. $0.
"""

from __future__ import annotations

import json

import scripts.ingest_lift_report as report
from tests.ingest_helpers import minimal_beat, stamp


def _beat(slug: str, narration: str, span: str) -> dict:
    beat = stamp(minimal_beat(story_slug=slug, beat_id=f"new_york/test-poi/{slug}"))
    beat["narration"]["text"] = narration
    beat["claims"][0]["sources"][0]["span"] = span
    return beat


def test_the_report_counts_refusals_with_and_without_the_name_exemption(tmp_path, capsys):
    beats = [
        _beat("named", "Rebay ran the Museum of Non-Objective Painting for him.",
              "the Museum of Non-Objective Painting on 54th St"),
        _beat("phrasing", "He was an avid collector of Asian art from youth.",
              "an avid collector of Asian art and a patron"),
        _beat(
            "clean",
            "The building was finished in 1959.",
            "Construction was finally completed in 1959",
        ),
    ]
    path = tmp_path / "beats.json"
    path.write_text(json.dumps(beats), encoding="utf-8")

    # No chunks under this root: each beat is measured against its own spans.
    rc = report.main(["--file", str(path), "--run", "6", "--books-root", str(tmp_path)])

    out = capsys.readouterr().out
    assert rc == 0
    assert "named longest=6 name=yes" in out
    assert "phrasing longest=6 name=no" in out
    assert "clean longest=" in out
    assert "beats=3 at_or_over=2 refused_with_exemption=1 refused_without=2" in out


def test_the_report_refuses_what_the_gate_refuses_across_a_beats_spans(tmp_path, capsys):
    """The report and the P4 gate are one measure: a sentence around an exempt
    name run is added up across ALL the beat's spans (owner ruling 2026-10-12),
    so the report must hand `gates.lift` those spans too — one span at a time
    it printed refused=no for a beat the gate refuses."""
    beat = _beat(
        "moma",
        "The world-famous Museum of Modern Art holds an extensive and unique collection "
        "of modern and contemporary art.",
        "The world-famous Museum of Modern Art (MoMA) has an extensive",
    )
    beat["claims"][1]["sources"][0]["span"] = (
        "has an extensive and unique collection of contemporary and modern art"
    )
    path = tmp_path / "beats.json"
    path.write_text(json.dumps([beat]), encoding="utf-8")

    rc = report.main(["--file", str(path), "--run", "7", "--books-root", str(tmp_path)])

    out = capsys.readouterr().out
    assert rc == 0
    assert "moma longest=7 name=no ordinary=2 refused=yes" in out
    assert "beats=1 at_or_over=1 refused_with_exemption=1 refused_without=1" in out


def test_the_report_reads_the_chunk_past_a_span_that_stops_short(tmp_path, capsys):
    """A cited span can stop mid-sentence, so the report — like the P4 gate
    and the validator — measures a narration against the chunk its claims
    cite. Against the span alone this copy is three words long."""
    beat = _beat(
        "past-the-span",
        "It has an extensive and unique collection of contemporary and modern art.",
        "The world-famous Museum of Modern Art (MoMA) has an extensive",
    )
    beat["claims"] = [beat["claims"][0]]
    source = beat["claims"][0]["sources"][0]
    chunk_dir = tmp_path / "books" / beat["city_name"] / source["source_id"]
    chunk_dir.mkdir(parents=True)
    (chunk_dir / f"{source['chunk']}.txt").write_text(
        "The world-famous Museum of Modern Art (MoMA) has an extensive\n"
        "and unique collection of contemporary and modern art. There is plenty to see.\n",
        encoding="utf-8",
    )
    path = tmp_path / "beats.json"
    path.write_text(json.dumps([beat]), encoding="utf-8")

    rc = report.main(["--file", str(path), "--run", "7", "--books-root", str(tmp_path / "books")])

    out = capsys.readouterr().out
    assert rc == 0
    assert "past-the-span longest=11 name=no" in out
    assert "refused=yes" in out

    rc = report.main(["--file", str(path), "--run", "7", "--books-root", str(tmp_path)])
    assert rc == 0
    assert "past-the-span longest=3 name=no" in capsys.readouterr().out


def test_the_report_measures_a_record_it_cannot_parse_against_its_spans(tmp_path, capsys):
    """The report reads whatever file it is given: a record the model refuses
    (a legacy or half-written one) is still measured, against its spans."""
    beat = _beat(
        "odd",
        "He was an avid collector of Asian art and later sold it all.",
        "an avid collector of Asian art and a patron",
    )
    del beat["beat_type"]
    path = tmp_path / "beats.json"
    path.write_text(json.dumps([beat]), encoding="utf-8")

    rc = report.main(["--file", str(path), "--run", "7", "--books-root", str(tmp_path)])

    assert rc == 0
    assert "odd longest=7 name=no ordinary=3 refused=yes" in capsys.readouterr().out
