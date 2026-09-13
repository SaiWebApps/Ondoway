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

    rc = report.main(["--file", str(path), "--run", "6"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "named longest=6 name=yes" in out
    assert "phrasing longest=6 name=no" in out
    assert "clean longest=" in out
    assert "beats=3 at_or_over=2 refused_with_exemption=1 refused_without=2" in out
