"""Tests for scripts/wipe_beats.py + .claude/commands/beat-wipe.md.

Covers AC-2 (chunk-level wipe, idempotent, byte-identical on no-op)
and BP-8 (never deletes legacy_ambiguous beats).
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.wipe_beats import main as wipe_main

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "wipe_beats.py"


def _hash(body: str) -> str:
    normalized = re.sub(r"\s+", " ", body.lower().strip())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _beat(
    beat_id: str,
    body: str,
    *,
    poi_name: str,
    lens: str,
    topic_slug: str,
    book_slug: str = "around_and_about_paris",
    chunk: str,
    city_name: str = "paris",
) -> dict:
    return {
        "beat_id": beat_id,
        "city_name": city_name,
        "poi_name": poi_name,
        "lens": lens,
        "book_slug": book_slug,
        "topic_slug": topic_slug,
        "source_chunk_slug": chunk,
        "script_body": body,
        "script_body_hash": _hash(body),
    }


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def env(tmp_path: Path) -> dict[str, Path]:
    """Two-chunk fixture with one legacy_ambiguous beat at the same book."""
    beats = [
        _beat(
            "paris_val_de_grace_historic_worship_around_and_about_paris_1645_vow",
            "Anne of Austria vowed in 1645 to build the church.",
            poi_name="Val-de-Grace",
            lens="historic_worship",
            topic_slug="1645_vow",
            chunk="chunk-15-5th-arr-val-de-grace",
        ),
        _beat(
            "paris_val_de_grace_historic_arch_around_and_about_paris_dome",
            "The dome crowns the church.",
            poi_name="Val-de-Grace",
            lens="historic_arch",
            topic_slug="dome",
            chunk="chunk-15-5th-arr-val-de-grace",
        ),
        _beat(
            "paris_louvre_historic_arch_around_and_about_paris_fortress",
            "The Louvre began as a fortress in 1190.",
            poi_name="Louvre Museum",
            lens="historic_arch",
            topic_slug="fortress",
            chunk="chunk-01-1st-arr",
        ),
        # Legacy-ambiguous beat at the same book — must survive wipe.
        _beat(
            "paris_les_halles_historic_worship_around_and_about_paris_market",
            "Les Halles was the belly of Paris.",
            poi_name="Les Halles",
            lens="historic_worship",
            topic_slug="market",
            chunk="legacy_ambiguous",
        ),
    ]
    log = {
        "city": "Paris",
        "books_processed": [
            {
                "book_title": "Around and About Paris",
                "author": "Thirza Vallois",
                "chunks_processed": [
                    {"chunk": "chunk-15-5th-arr-val-de-grace", "beats_extracted": 2},
                    {"chunk": "chunk-01-1st-arr", "beats_extracted": 1},
                ],
            }
        ],
    }
    beats_path = tmp_path / "beats.json"
    log_path = tmp_path / "book-log.json"
    beats_path.write_text(json.dumps(beats, indent=2) + "\n")
    log_path.write_text(json.dumps(log, indent=2) + "\n")
    return {"beats": beats_path, "log": log_path, "dir": tmp_path}


def _run_cli(env: dict[str, Path], *args: str) -> int:
    return wipe_main(
        [
            "paris/around-and-about-paris",
            "--chunk",
            *args[:1],
            "--beats-path",
            str(env["beats"]),
            "--log-path",
            str(env["log"]),
            *args[1:],
        ]
    )


def test_wipe_removes_exact_chunk(env, capsys):
    """AC-2 — `--apply` removes the two VdG beats + the VdG log entry;
    the Louvre beat and the legacy_ambiguous Les Halles beat survive."""
    rc = _run_cli(env, "chunk-15-5th-arr-val-de-grace", "--apply")
    assert rc == 0, capsys.readouterr().err

    beats_after = json.loads(env["beats"].read_text())
    beat_ids = [b["beat_id"] for b in beats_after]
    assert "paris_louvre_historic_arch_around_and_about_paris_fortress" in beat_ids
    assert "paris_les_halles_historic_worship_around_and_about_paris_market" in beat_ids
    assert all("val_de_grace" not in bid for bid in beat_ids)

    log_after = json.loads(env["log"].read_text())
    chunks = log_after["books_processed"][0]["chunks_processed"]
    assert all(c["chunk"] != "chunk-15-5th-arr-val-de-grace" for c in chunks)
    assert any(c["chunk"] == "chunk-01-1st-arr" for c in chunks)


def test_wipe_ignores_legacy_ambiguous(env, capsys):
    """BP-8 — a wipe whose chunk arg is `legacy_ambiguous` is refused
    up front: no beats removed, no log entry removed, both files
    byte-identical. The sentinel is not a valid wipe target regardless
    of whether a beat or log entry happens to carry it."""
    pre_beats = _sha_file(env["beats"])
    pre_log = _sha_file(env["log"])

    rc = _run_cli(env, "legacy_ambiguous", "--apply")
    captured = capsys.readouterr()
    assert rc == 0, captured.err

    beats_after = json.loads(env["beats"].read_text())
    beat_ids = [b["beat_id"] for b in beats_after]
    # The legacy_ambiguous beat survives.
    assert "paris_les_halles_historic_worship_around_and_about_paris_market" in beat_ids
    # In fact, no beat was removed.
    assert len(beats_after) == 4
    assert "refused" in captured.out.lower()
    assert _sha_file(env["beats"]) == pre_beats
    assert _sha_file(env["log"]) == pre_log


def test_wipe_refuses_legacy_ambiguous_log_entry(tmp_path):
    """BP-8 symmetry — even if a (hypothetically corrupt) book-log
    carried a chunks_processed entry named `legacy_ambiguous`, the wipe
    must refuse to remove it. The sentinel guard is symmetric on both
    sides of the wipe."""
    beats_path = tmp_path / "beats.json"
    log_path = tmp_path / "book-log.json"
    beats_path.write_text(json.dumps([], indent=2) + "\n")
    log_path.write_text(
        json.dumps(
            {
                "city": "Paris",
                "books_processed": [
                    {
                        "book_title": "Around and About Paris",
                        "author": "Thirza Vallois",
                        "chunks_processed": [{"chunk": "legacy_ambiguous"}],
                    }
                ],
            },
            indent=2,
        )
        + "\n"
    )
    pre_log = _sha_file(log_path)

    rc = wipe_main(
        [
            "paris/around-and-about-paris",
            "--chunk",
            "legacy_ambiguous",
            "--beats-path",
            str(beats_path),
            "--log-path",
            str(log_path),
            "--apply",
        ]
    )
    assert rc == 0
    assert _sha_file(log_path) == pre_log


def test_wipe_idempotent_byte_identical(env, capsys):
    """AC-2 — second wipe on already-wiped chunk leaves both files
    byte-identical to their post-first-wipe state."""
    assert _run_cli(env, "chunk-15-5th-arr-val-de-grace", "--apply") == 0

    pre_beats = _sha_file(env["beats"])
    pre_log = _sha_file(env["log"])

    assert _run_cli(env, "chunk-15-5th-arr-val-de-grace", "--apply") == 0
    captured = capsys.readouterr()
    assert "already clean" in captured.out

    assert _sha_file(env["beats"]) == pre_beats
    assert _sha_file(env["log"]) == pre_log


def test_wipe_dry_run_no_mutation(env, capsys):
    """AC-2 — without --apply, no disk writes even when matches exist."""
    pre_beats = _sha_file(env["beats"])
    pre_log = _sha_file(env["log"])

    assert _run_cli(env, "chunk-15-5th-arr-val-de-grace") == 0
    captured = capsys.readouterr()
    assert "dry-run" in captured.out
    assert "plan: remove 2 beat(s)" in captured.out

    assert _sha_file(env["beats"]) == pre_beats
    assert _sha_file(env["log"]) == pre_log


def test_wipe_cli_invocation(env):
    """End-to-end subprocess run — matches the verification command
    form that lives in 05-plan.md Scope 2 Part C."""
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "paris/around-and-about-paris",
            "--chunk",
            "chunk-15-5th-arr-val-de-grace",
            "--beats-path",
            str(env["beats"]),
            "--log-path",
            str(env["log"]),
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "dry-run" in result.stdout
    assert "plan: remove 2 beat(s)" in result.stdout


def test_a_book_slug_written_either_way_finds_its_beats() -> None:
    """Two slug conventions live in the corpus and the wipe has to match both.

    The migration derived `around_and_about_paris` from a title; the extractor wrote
    `lonely-planet-new-york-city` from a directory name. Every New York beat carries
    the dashed form. Comparing on one convention matched nothing for those 1,687
    beats and printed "already clean" — a no-op indistinguishable from success, on
    the tool the whole re-extraction depends on.
    """
    from scripts.wipe_beats import find_matching_beats, same_book

    assert same_book("lonely-planet-new-york-city", "lonely_planet_new_york_city")
    assert same_book("around_and_about_paris", "around-and-about-paris")
    assert not same_book("lonely-planet-new-york-city", "big-onion-ten-historic-tours")

    beats = [
        {"beat_id": "a", "book_slug": "lonely-planet-new-york-city", "source_chunk_slug": "c7"},
        {"beat_id": "b", "book_slug": "around_and_about_paris", "source_chunk_slug": "c7"},
        {"beat_id": "c", "book_slug": "lonely-planet-new-york-city", "source_chunk_slug": "c8"},
    ]
    for written_as in ("lonely-planet-new-york-city", "lonely_planet_new_york_city"):
        kept, removed = find_matching_beats(beats, written_as, "c7")
        assert [b["beat_id"] for b in removed] == ["a"], written_as
        assert len(kept) == 2
