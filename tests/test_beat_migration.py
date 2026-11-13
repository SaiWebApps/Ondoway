"""Tests for scripts/migrate_beats_dedup_fields.py.

Covers:
- branched parser (unified_v1 vs legacy)
- idempotency
- source_chunk_slug ambiguity sentinel
- git-clean pre-flight; snapshot before mutation
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.migrate_beats_dedup_fields import (
    LEGACY_AMBIGUOUS,
    LEGACY_UNKNOWN,
    build_poi_chunk_index,
    derive_source_chunk_slug,
    parse_legacy_beat_id,
    parse_unified_v1_book_slug,
)
from scripts.migrate_beats_dedup_fields import (
    main as migrate_main,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "migrate_beats_dedup_fields.py"


# Hand-crafted minimal book-log for these unit tests. Includes a POI
# (`Pont Neuf`) that appears in two distinct chunks → must derive to
# `legacy_ambiguous`. Includes a POI (`Val-de-Grace`) that appears in
# two ENTRIES but both with the same chunk slug → must derive to that
# single chunk slug (mirrors the real Paris case after re-extraction).
SAMPLE_LOG = {
    "city": "Paris",
    "books_processed": [
        {
            "book_title": "Test Book",
            "author": "Tester",
            "chunks_processed": [
                {"chunk": "chunk-a", "pois_touched": ["Louvre Museum", "Pont Neuf"]},
                {"chunk": "chunk-b", "pois_touched": ["Pont Neuf", "Saint-Eustache"]},
                {"chunk": "chunk-15-vdg", "pois_touched": ["Val-de-Grace"]},
                {"chunk": "chunk-15-vdg", "pois_touched": ["Val-de-Grace"]},
            ],
        }
    ],
}


def _fresh_repo(tmp_path: Path) -> Path:
    """Create a tmp git repo + checked-in beats.json + book-log.json so the
    git-clean pre-flight can pass."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=tmp_path, check=True)
    return tmp_path


def _commit_files(tmp_path: Path, beats_path: Path, log_path: Path) -> None:
    subprocess.run(["git", "add", str(beats_path), str(log_path)], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=tmp_path, check=True)


# ── Pure-function unit tests ──


def test_parse_unified_v1_book_slug_happy():
    book = parse_unified_v1_book_slug(
        "paris_val_de_grace_historic_worship_around_and_about_paris_intact_17c",
        city_name="paris",
        lens="historic_worship",
        topic_slug="intact_17c",
    )
    assert book == "around_and_about_paris"


def test_parse_unified_v1_book_slug_falls_back():
    # missing city prefix
    assert parse_unified_v1_book_slug("garbage", "paris", "lens", "topic") == LEGACY_UNKNOWN
    # missing topic suffix
    assert (
        parse_unified_v1_book_slug("paris_x_lens_y_z", "paris", "lens", "WRONG") == LEGACY_UNKNOWN
    )


def test_parse_legacy_beat_id_happy():
    book, topic = parse_legacy_beat_id(
        "louvre_museum_war_conflict_fortress_around_and_about_paris", "war_conflict"
    )
    assert book == "around_and_about_paris"
    assert topic == "fortress"


def test_parse_legacy_beat_id_unknown_book_falls_back():
    book, topic = parse_legacy_beat_id("some_poi_lens_topic_unknown_book_slug", "lens")
    assert book == LEGACY_UNKNOWN
    assert topic == LEGACY_UNKNOWN


def test_derive_source_chunk_slug_disambiguation():
    idx = build_poi_chunk_index(SAMPLE_LOG)
    # Pont Neuf appears in chunk-a AND chunk-b → ambiguous
    assert derive_source_chunk_slug("Pont Neuf", idx) == LEGACY_AMBIGUOUS
    # Val-de-Grace appears in TWO entries but both same chunk → single
    assert derive_source_chunk_slug("Val-de-Grace", idx) == "chunk-15-vdg"
    # Louvre is unique
    assert derive_source_chunk_slug("Louvre Museum", idx) == "chunk-a"
    # Unknown POI
    assert derive_source_chunk_slug("Unknown POI", idx) == LEGACY_UNKNOWN


# ── End-to-end CLI tests ──


def test_migration_branches_on_prompt_version(tmp_path):
    """unified_v1 preserves topic_slug; legacy parses both fields."""
    _fresh_repo(tmp_path)
    beats = [
        # legacy
        {
            "beat_id": "louvre_museum_war_conflict_fortress_around_and_about_paris",
            "poi_name": "Louvre Museum",
            "lens": "war_conflict",
            "script_body": "The Louvre began as a fortress.",
            "_meta": {"prompt_version": "beat_from_book_v1"},
        },
        {
            "beat_id": "saint_eustache_dark_history_moliere_around_and_about_paris",
            "poi_name": "Saint-Eustache",
            "lens": "dark_history",
            "script_body": "Moliere was buried here.",
            "_meta": {"prompt_version": "pipeline_batch_v1"},
        },
        # unified_v1
        {
            "beat_id": "paris_val_de_grace_historic_worship_around_and_about_paris_intact_17c",
            "poi_name": "Val-de-Grace",
            "lens": "historic_worship",
            "topic_slug": "intact_17c",
            "city_name": "paris",
            "script_body": "Val-de-Grace remains intact.",
            "_meta": {"prompt_version": "unified_v1"},
        },
        {
            "beat_id": "paris_val_de_grace_hidden_history_around_and_about_paris_anne_vow",
            "poi_name": "Val-de-Grace",
            "lens": "hidden_history",
            "topic_slug": "anne_vow",
            "city_name": "paris",
            "script_body": "Anne of Austria vowed to build it.",
            "_meta": {"prompt_version": "unified_v1"},
        },
    ]
    beats_path = tmp_path / "beats.json"
    log_path = tmp_path / "book-log.json"
    beats_path.write_text(json.dumps(beats))
    log_path.write_text(json.dumps(SAMPLE_LOG))
    _commit_files(tmp_path, beats_path, log_path)

    code = migrate_main([str(beats_path)])
    assert code == 0

    out = json.loads(beats_path.read_text())
    by_id = {b["beat_id"]: b for b in out}

    # legacy parses both
    louvre = by_id["louvre_museum_war_conflict_fortress_around_and_about_paris"]
    assert louvre["book_slug"] == "around_and_about_paris"
    assert louvre["topic_slug"] == "fortress"

    eustache = by_id["saint_eustache_dark_history_moliere_around_and_about_paris"]
    assert eustache["book_slug"] == "around_and_about_paris"
    assert eustache["topic_slug"] == "moliere"

    # unified_v1 preserves topic_slug, parses book_slug
    vdg_a = by_id["paris_val_de_grace_historic_worship_around_and_about_paris_intact_17c"]
    assert vdg_a["topic_slug"] == "intact_17c"
    assert vdg_a["book_slug"] == "around_and_about_paris"

    vdg_b = by_id["paris_val_de_grace_hidden_history_around_and_about_paris_anne_vow"]
    assert vdg_b["topic_slug"] == "anne_vow"
    assert vdg_b["book_slug"] == "around_and_about_paris"

    # All beats get city_name and script_body_hash
    for b in out:
        assert b["city_name"] == "paris"
        assert b["script_body_hash"]


def test_migration_requires_clean_git(tmp_path):
    """A non-empty `git status --porcelain` aborts before any mutation."""
    _fresh_repo(tmp_path)
    beats = [{"beat_id": "x", "poi_name": "X", "lens": "l", "script_body": "y"}]
    beats_path = tmp_path / "beats.json"
    log_path = tmp_path / "book-log.json"
    beats_path.write_text(json.dumps(beats))
    log_path.write_text(json.dumps(SAMPLE_LOG))
    # NOTE: no commit — beats.json is untracked → git status returns content
    pre_content = beats_path.read_text()
    snapshot_path = beats_path.with_suffix(beats_path.suffix + ".pre-migration")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(beats_path)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    # No mutation
    assert beats_path.read_text() == pre_content
    # No snapshot written
    assert not snapshot_path.exists()


def test_migration_idempotent(tmp_path):
    """A second run leaves file byte-identical."""
    _fresh_repo(tmp_path)
    beats = [
        {
            "beat_id": "louvre_museum_war_conflict_fortress_around_and_about_paris",
            "poi_name": "Louvre Museum",
            "lens": "war_conflict",
            "script_body": "The Louvre began as a fortress.",
            "_meta": {"prompt_version": "beat_from_book_v1"},
        },
    ]
    beats_path = tmp_path / "beats.json"
    log_path = tmp_path / "book-log.json"
    beats_path.write_text(json.dumps(beats))
    log_path.write_text(json.dumps(SAMPLE_LOG))
    _commit_files(tmp_path, beats_path, log_path)

    assert migrate_main([str(beats_path)]) == 0
    after_first = beats_path.read_bytes()

    # Re-commit so git status is clean for second run
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "after first migration"], cwd=tmp_path, check=True)

    assert migrate_main([str(beats_path)]) == 0
    after_second = beats_path.read_bytes()

    assert after_first == after_second


def test_migration_source_chunk_ambiguous(tmp_path):
    """A POI in ≥2 distinct chunks → source_chunk_slug = legacy_ambiguous."""
    _fresh_repo(tmp_path)
    beats = [
        {
            "beat_id": "pont_neuf_hidden_history_oldest_around_and_about_paris",
            "poi_name": "Pont Neuf",
            "lens": "hidden_history",
            "script_body": "It is the oldest.",
            "_meta": {"prompt_version": "pipeline_batch_v1"},
        },
    ]
    beats_path = tmp_path / "beats.json"
    log_path = tmp_path / "book-log.json"
    beats_path.write_text(json.dumps(beats))
    log_path.write_text(json.dumps(SAMPLE_LOG))
    _commit_files(tmp_path, beats_path, log_path)

    assert migrate_main([str(beats_path)]) == 0
    out = json.loads(beats_path.read_text())
    assert out[0]["source_chunk_slug"] == LEGACY_AMBIGUOUS


def test_migration_writes_snapshot_before_mutation(tmp_path):
    """A `{beats.json}.pre-migration` snapshot exists after a successful run."""
    _fresh_repo(tmp_path)
    beats = [
        {
            "beat_id": "louvre_museum_war_conflict_fortress_around_and_about_paris",
            "poi_name": "Louvre Museum",
            "lens": "war_conflict",
            "script_body": "Fortress.",
            "_meta": {"prompt_version": "beat_from_book_v1"},
        },
    ]
    beats_path = tmp_path / "beats.json"
    log_path = tmp_path / "book-log.json"
    beats_path.write_text(json.dumps(beats))
    log_path.write_text(json.dumps(SAMPLE_LOG))
    _commit_files(tmp_path, beats_path, log_path)

    pre_content = beats_path.read_bytes()
    assert migrate_main([str(beats_path)]) == 0

    snapshot = beats_path.with_suffix(beats_path.suffix + ".pre-migration")
    assert snapshot.exists()
    assert snapshot.read_bytes() == pre_content


def test_migration_preserves_real_unified_topic_slug(tmp_path):
    """Idempotency: an existing real topic_slug on a unified_v1 beat is never
    overwritten by parsing."""
    _fresh_repo(tmp_path)
    beats = [
        {
            "beat_id": "paris_val_de_grace_historic_worship_around_and_about_paris_intact_17c",
            "poi_name": "Val-de-Grace",
            "lens": "historic_worship",
            "topic_slug": "human_curated_topic",  # existing real value
            "city_name": "paris",
            "script_body": "x",
            "_meta": {"prompt_version": "unified_v1"},
        },
    ]
    beats_path = tmp_path / "beats.json"
    log_path = tmp_path / "book-log.json"
    beats_path.write_text(json.dumps(beats))
    log_path.write_text(json.dumps(SAMPLE_LOG))
    _commit_files(tmp_path, beats_path, log_path)

    assert migrate_main([str(beats_path)]) == 0
    out = json.loads(beats_path.read_text())
    assert out[0]["topic_slug"] == "human_curated_topic"
