"""Tests for scripts/beats_io.py — the atomic commit helper.

Covers AC-11 (atomic end-of-extraction validation) and BP-7 (rollback
leaves both files byte-identical when validation fails).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from scripts.beats_io import BeatValidationError, commit
from tests import ingest_job_script as ingest_script


def _hash(body: str) -> str:
    normalized = re.sub(r"\s+", " ", body.lower().strip())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _beat(
    beat_id: str,
    body: str,
    *,
    poi_name: str = "Default POI",
    lens: str = "historic_arch",
    topic_slug: str = "default",
    book_slug: str = "around_and_about_paris",
    chunk: str = "chunk-00-default",
) -> dict:
    return {
        "beat_id": beat_id,
        "city_name": "paris",
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


def _seed(tmp_path: Path) -> tuple[Path, Path]:
    beats_path = tmp_path / "beats.json"
    log_path = tmp_path / "book-log.json"
    seed_beat = _beat(
        "paris_louvre_historic_arch_around_and_about_paris_fortress",
        "The Louvre began as a fortress under Philippe-Auguste in 1190.",
        poi_name="Louvre Museum",
        topic_slug="fortress",
        chunk="chunk-01-1st-arr",
    )
    log = {
        "city": "Paris",
        "books_processed": [
            {
                "book_title": "Around and About Paris",
                "author": "Thirza Vallois",
                "chunks_processed": [{"chunk": "chunk-01-1st-arr", "beats_extracted": 1}],
            }
        ],
    }
    beats_path.write_text(json.dumps([seed_beat], indent=2) + "\n")
    log_path.write_text(json.dumps(log, indent=2) + "\n")
    return beats_path, log_path


# ── BP-7 rollback proof ──


def test_rollback_byte_identical_on_hash_collision(tmp_path):
    """BP-7 + AC-11 — validator fail leaves both files byte-identical."""
    beats_path, log_path = _seed(tmp_path)
    pre_beats = _sha_file(beats_path)
    pre_log = _sha_file(log_path)

    # Final-state list contains both the seed beat AND a new beat that
    # shares its script_body_hash (different lens so identity doesn't
    # collide — we want HASH_COLLISION alone to trip).
    seed = json.loads(beats_path.read_text())
    collider = _beat(
        "paris_louvre_war_conflict_around_and_about_paris_fortress",
        "The Louvre began as a fortress under Philippe-Auguste in 1190.",
        poi_name="Louvre Museum",
        lens="war_conflict",
        topic_slug="fortress",
        chunk="chunk-01-1st-arr",
    )
    final_beats = [*seed, collider]
    final_log = json.loads(log_path.read_text())  # unchanged

    with pytest.raises(BeatValidationError) as exc:
        commit(
            final_beats,
            final_log,
            beats_path=beats_path,
            log_path=log_path,
        )

    assert "HASH_COLLISION" in str(exc.value)
    assert _sha_file(beats_path) == pre_beats
    assert _sha_file(log_path) == pre_log
    # No staging files lingering
    assert not (tmp_path / "beats.json.staging").exists()
    assert not (tmp_path / "book-log.json.staging").exists()


def test_rollback_byte_identical_on_identity_collision(tmp_path):
    """BP-7 — identity-tuple collision also rolls back cleanly."""
    beats_path, log_path = _seed(tmp_path)
    pre_beats = _sha_file(beats_path)
    pre_log = _sha_file(log_path)

    seed = json.loads(beats_path.read_text())
    # Same identity tuple as seed, different body.
    duplicate = _beat(
        "paris_louvre_historic_arch_around_and_about_paris_fortress_dup",
        "A different body about Philippe-Auguste.",
        poi_name="Louvre Museum",
        topic_slug="fortress",
        chunk="chunk-01-1st-arr",
    )
    final_beats = [*seed, duplicate]

    with pytest.raises(BeatValidationError) as exc:
        commit(
            final_beats,
            json.loads(log_path.read_text()),
            beats_path=beats_path,
            log_path=log_path,
        )

    assert "IDENTITY_COLLISION" in str(exc.value)
    assert _sha_file(beats_path) == pre_beats
    assert _sha_file(log_path) == pre_log


# ── Happy path ──


def test_commit_happy_path_writes_both_files(tmp_path):
    """Valid final state → both files updated atomically."""
    beats_path, log_path = _seed(tmp_path)

    seed = json.loads(beats_path.read_text())
    new_beat = _beat(
        "paris_val_de_grace_historic_worship_around_and_about_paris_1645_vow",
        "Anne of Austria vowed in 1645 to build the church.",
        poi_name="Val-de-Grace",
        lens="historic_worship",
        topic_slug="1645_vow",
        chunk="chunk-15-5th-arr-val-de-grace",
    )
    final_beats = [*seed, new_beat]
    final_log = json.loads(log_path.read_text())
    final_log["books_processed"][0]["chunks_processed"].append(
        {"chunk": "chunk-15-5th-arr-val-de-grace", "beats_extracted": 1}
    )

    commit(
        final_beats,
        final_log,
        beats_path=beats_path,
        log_path=log_path,
    )

    written_beats = json.loads(beats_path.read_text())
    assert len(written_beats) == 2
    assert written_beats[1]["beat_id"].endswith("1645_vow")

    written_log = json.loads(log_path.read_text())
    chunks = written_log["books_processed"][0]["chunks_processed"]
    assert any(c["chunk"] == "chunk-15-5th-arr-val-de-grace" for c in chunks)

    # No staging files leak on success either.
    assert not (tmp_path / "beats.json.staging").exists()
    assert not (tmp_path / "book-log.json.staging").exists()


def test_commit_missing_hash_rejected(tmp_path):
    """A final-state list with any hash-missing beat is rejected, files
    remain byte-identical."""
    beats_path, log_path = _seed(tmp_path)
    pre_beats = _sha_file(beats_path)
    pre_log = _sha_file(log_path)

    seed = json.loads(beats_path.read_text())
    new_beat = _beat(
        "paris_val_de_grace_historic_worship_around_and_about_paris_no_hash",
        "Some body text.",
        poi_name="Val-de-Grace",
        lens="historic_worship",
        topic_slug="no_hash",
        chunk="chunk-15-5th-arr-val-de-grace",
    )
    del new_beat["script_body_hash"]
    final_beats = [*seed, new_beat]

    with pytest.raises(BeatValidationError) as exc:
        commit(
            final_beats,
            json.loads(log_path.read_text()),
            beats_path=beats_path,
            log_path=log_path,
        )

    assert "HASH_MISSING" in str(exc.value)
    assert _sha_file(beats_path) == pre_beats
    assert _sha_file(log_path) == pre_log


def test_commit_pre_existing_targets_untouched_on_fail(tmp_path):
    """Even if the target beats file exists with real content, a failed
    commit leaves its bytes intact — not truncated mid-write."""
    beats_path, log_path = _seed(tmp_path)
    original_bytes = beats_path.read_bytes()

    seed = json.loads(beats_path.read_text())
    collider = _beat(
        "paris_louvre_other_around_and_about_paris_fortress",
        "The Louvre began as a fortress under Philippe-Auguste in 1190.",
        poi_name="Louvre Museum",
        lens="hidden_history",
        topic_slug="fortress",
        chunk="chunk-01-1st-arr",
    )

    with pytest.raises(BeatValidationError):
        commit(
            [*seed, collider],
            json.loads(log_path.read_text()),
            beats_path=beats_path,
            log_path=log_path,
        )

    assert beats_path.read_bytes() == original_bytes


def test_commit_grounds_a_new_shape_file_under_the_given_chunks_root(tmp_path):
    """A new-shape file written under a data root that is NOT beside a
    Books/ directory (the ingest runner's per-job root) can only ground its
    spans through an explicit `chunks_root`: without it the validator cannot
    derive one, refuses the staged write, and both targets stay absent;
    with it the same records commit. The chunks root here holds the real
    Lonely Planet chunk-07 the records' spans are quoted from."""
    chunks_root = tmp_path / "chunks"
    (chunks_root / ingest_script.LP_SOURCE).mkdir(parents=True)
    (chunks_root / ingest_script.LP_SOURCE / f"{ingest_script.LP_CHUNK}.txt").write_bytes(
        ingest_script.REAL_CHUNK.read_bytes()
    )
    out = tmp_path / "out" / "new_york"
    out.mkdir(parents=True)
    beats_path, log_path = out / "beats.json", out / "book-log.json"
    records = ingest_script.existing_records()
    log = {"city": "new_york", "books_processed": []}

    with pytest.raises(BeatValidationError) as exc:
        commit(records, log, beats_path=beats_path, log_path=log_path)
    assert "chunks root" in str(exc.value)
    assert not beats_path.exists() and not log_path.exists()

    commit(records, log, beats_path=beats_path, log_path=log_path, chunks_root=chunks_root)
    assert json.loads(beats_path.read_text()) == records
    assert json.loads(log_path.read_text()) == log
