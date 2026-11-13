"""Tests for the dedup data hygiene foundation:

1. Pydantic NarrativeBeatCreate hash auto-compute and reject-mismatch.
2. CLI scripts/validate_beats.py hash + identity-tuple checks, including
   the legacy_unknown wildcard semantics.
3. The validator is single-file-path scoped (city isolation).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.api.models.nodes import NarrativeBeatCreate, _normalized_script_body_hash

REPO_ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = REPO_ROOT / "scripts" / "validate_beats.py"
FIXTURE_PARIS = REPO_ROOT / "tests" / "fixtures" / "beats_multi_chunk.json"
FIXTURE_LONDON = REPO_ROOT / "tests" / "fixtures" / "beats_london_min.json"


def run_validator(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), str(path)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


# ── Pydantic field validation ──


def test_hash_auto_computed():
    """An empty hash gets filled with SHA-256 of normalized body."""
    beat = NarrativeBeatCreate(script_body="Hello world")
    assert beat.script_body_hash == hashlib.sha256(b"hello world").hexdigest()


def test_hash_present_correct_accepted():
    """A caller-provided hash that matches normalized body is accepted."""
    body = "Some Body Here"
    correct = _normalized_script_body_hash(body)
    beat = NarrativeBeatCreate(script_body=body, script_body_hash=correct)
    assert beat.script_body_hash == correct


def test_hash_present_wrong_rejected():
    """A caller-provided hash that doesn't match body raises."""
    with pytest.raises(ValidationError):
        NarrativeBeatCreate(script_body="Foo", script_body_hash="bogus")


def test_normalized_hash_collapses_whitespace_and_case():
    """Whitespace runs collapse, case is folded."""
    a = _normalized_script_body_hash("  Hello   WORLD\n")
    b = _normalized_script_body_hash("hello world")
    assert a == b


# ── CLI validator: happy paths ──


def test_baseline_fixture_passes():
    """The hand-crafted multi-chunk fixture is valid as-shipped."""
    result = run_validator(FIXTURE_PARIS)
    assert result.returncode == 0, result.stdout + result.stderr


def test_validator_legacy_unknown_wildcard():
    """Two beats with `legacy_unknown` book_slug at the same
    POI/lens do NOT trigger an identity collision (wildcard semantics)."""
    # The baseline fixture already has two `Pont Neuf / hidden_history /
    # legacy_unknown / legacy_unknown` beats with different bodies. They
    # must pass.
    result = run_validator(FIXTURE_PARIS)
    assert result.returncode == 0, result.stdout + result.stderr


# ── CLI validator: failure modes ──


def test_validator_hash_collision(tmp_path):
    """Two beats sharing script_body_hash → exit 1."""
    beats = json.loads(FIXTURE_PARIS.read_text())
    # Make beat 4 carry beat 1's body + hash but a distinct identity
    # tuple (different lens) so we trigger ONLY hash collision.
    beats[3]["script_body"] = beats[0]["script_body"]
    beats[3]["script_body_hash"] = beats[0]["script_body_hash"]
    beats[3]["lens"] = "different_lens"
    target = tmp_path / "beats.json"
    target.write_text(json.dumps(beats))
    result = run_validator(target)
    assert result.returncode == 1
    assert "HASH_COLLISION" in result.stdout


def test_validator_identity_collision(tmp_path):
    """Two beats sharing the full identity tuple → exit 1."""
    beats = json.loads(FIXTURE_PARIS.read_text())
    # Clone beat 1 with a different body (and recomputed hash) but same
    # identity tuple. Only identity should trip.
    duplicate = json.loads(json.dumps(beats[0]))
    duplicate["beat_id"] = duplicate["beat_id"] + "_dup"
    duplicate["script_body"] = "A different body that says nothing about Philippe-Auguste."
    normalized = re.sub(r"\s+", " ", duplicate["script_body"].lower().strip())
    duplicate["script_body_hash"] = hashlib.sha256(normalized.encode()).hexdigest()
    beats.append(duplicate)
    target = tmp_path / "beats.json"
    target.write_text(json.dumps(beats))
    result = run_validator(target)
    assert result.returncode == 1
    assert "IDENTITY_COLLISION" in result.stdout


def _idty_beat(beat_id: str, topic: str, body_hash: str) -> dict:
    """A minimal, otherwise-valid beat (legacy_unknown book → grounding + identity
    wildcards skip) whose beat_id/topic/hash are caller-controlled."""
    return {
        "beat_id": beat_id,
        "city_name": "paris",
        "poi_name": "X",
        "lens": "historic_arch",
        "book_slug": "legacy_unknown",
        "topic_slug": topic,
        "source_chunk_slug": "legacy_ambiguous",
        "script_body_hash": body_hash,
    }


def test_validator_duplicate_beat_id_fails(tmp_path):
    """Two beats sharing a beat_id → exit 1 with BEAT_ID_COLLISION.

    The upload path MERGEs NarrativeBeat on beat_id with NO DB uniqueness
    constraint behind it, so two beats with the same beat_id collapse onto one
    graph node (the second SET-overwrites the first). Distinct topic_slug + hash
    here mean ONLY the beat_id check can catch them. [undo: remove
    _check_beat_id_uniqueness from validate() → returncode 0 → RED]"""
    beats = [
        _idty_beat("paris_dup_beat", "t1", "h1"),
        _idty_beat("paris_dup_beat", "t2", "h2"),
    ]
    target = tmp_path / "beats.json"
    target.write_text(json.dumps(beats))
    result = run_validator(target)
    assert result.returncode == 1
    assert "BEAT_ID_COLLISION" in result.stdout


def test_validator_unique_beat_ids_pass(tmp_path):
    """Distinct beat_ids (the normal case) do not trip the collision check."""
    beats = [
        _idty_beat("paris_beat_a", "t1", "h1"),
        _idty_beat("paris_beat_b", "t2", "h2"),
    ]
    target = tmp_path / "beats.json"
    target.write_text(json.dumps(beats))
    assert run_validator(target).returncode == 0, "unique beat_ids must pass"


def test_validator_missing_hash_flagged(tmp_path):
    """A beat with no script_body_hash field is flagged (HASH_MISSING)."""
    beats = json.loads(FIXTURE_PARIS.read_text())
    del beats[0]["script_body_hash"]
    target = tmp_path / "beats.json"
    target.write_text(json.dumps(beats))
    result = run_validator(target)
    assert result.returncode == 1
    assert "HASH_MISSING" in result.stdout


def test_validator_unreadable_file_returns_2(tmp_path):
    """Operator-error exit code 2 is distinct from data-integrity exit 1."""
    target = tmp_path / "does_not_exist.json"
    result = run_validator(target)
    assert result.returncode == 2


def test_validator_not_a_list_returns_2(tmp_path):
    target = tmp_path / "object.json"
    target.write_text(json.dumps({"not": "a list"}))
    result = run_validator(target)
    assert result.returncode == 2


# ── City isolation ──


def test_validator_city_isolated(tmp_path):
    """Sequentially validating Paris and London fixtures must leave
    each independent. Neither sees the other's data even though both files
    sit in the same fixtures directory."""
    # Run both back-to-back from a fresh subprocess so any module-level
    # cache pollution would surface.
    paris_copy = tmp_path / "paris.json"
    london_copy = tmp_path / "london.json"
    shutil.copy(FIXTURE_PARIS, paris_copy)
    shutil.copy(FIXTURE_LONDON, london_copy)
    paris_result = run_validator(paris_copy)
    london_result = run_validator(london_copy)
    assert paris_result.returncode == 0, paris_result.stdout + paris_result.stderr
    assert london_result.returncode == 0, london_result.stdout + london_result.stderr
    # And the order doesn't matter:
    london_first = run_validator(london_copy)
    paris_second = run_validator(paris_copy)
    assert london_first.returncode == 0
    assert paris_second.returncode == 0


# ── commit-time Wikipedia source-grounding gate ──


def _wiki_beat(chunk: str, source_passage: str) -> dict:
    return {
        "beat_id": "paris_test_poi_historic_arch_wikipedia_t",
        "city_name": "paris",
        "poi_name": "Test POI",
        "lens": "historic_arch",
        "book_slug": "wikipedia",
        "topic_slug": "t",
        "source_chunk_slug": chunk,
        "script_body_hash": "deadbeef",
        "source_passage": source_passage,
    }


def test_validator_wikipedia_grounded_passes(tmp_path):
    """A Wikipedia beat whose source_passage is in the pinned .txt passes."""
    chunk = "test_poi-rev-123"
    (tmp_path / "wikipedia").mkdir()
    text = "The arch was built in 1806. It stands at the Place du Carrousel near the Louvre."
    (tmp_path / "wikipedia" / f"{chunk}.txt").write_text(text)
    target = tmp_path / "beats.json"
    target.write_text(json.dumps([_wiki_beat(chunk, text)]))
    result = run_validator(target)
    assert result.returncode == 0, result.stdout + result.stderr


def test_validator_wikipedia_ungrounded_fails(tmp_path):
    """A source_passage absent from the pinned .txt is blocked at commit."""
    chunk = "test_poi-rev-123"
    (tmp_path / "wikipedia").mkdir()
    (tmp_path / "wikipedia" / f"{chunk}.txt").write_text(
        "The monument is built of marble. It stands in a public square."
    )
    passage = (
        "The arch is three times the size of its sibling. The statues were carved "
        "by Ramey and Cartellier. The horses came from Berlin."
    )
    target = tmp_path / "beats.json"
    target.write_text(json.dumps([_wiki_beat(chunk, passage)]))
    result = run_validator(target)
    assert result.returncode == 1
    assert "WIKIPEDIA_UNGROUNDED" in result.stdout


def test_validator_wikipedia_missing_source_fails(tmp_path):
    """A Wikipedia beat whose pinned .txt is absent cannot be committed."""
    target = tmp_path / "beats.json"
    target.write_text(json.dumps([_wiki_beat("absent-rev-9", "Some claim here that has words.")]))
    result = run_validator(target)
    assert result.returncode == 1
    assert "WIKIPEDIA_MISSING_SOURCE" in result.stdout


# ── commit-time BOOK source-grounding gate (with legacy grandfather) ──

_BOOK_CHUNK = "The hotel was built in 1655 for the financier. It stands on the rue Saint-Antoine."
_BOOK_UNGROUNDED_SP = "It was a palace of glass. The duke fled to Venice. Cannons lined the roof."


def _book_beat(
    topic: str, lens: str, source_passage: str, body_hash: str, chunk: str = "chunk-01"
) -> dict:
    return {
        "beat_id": f"paris_x_{lens}_around_and_about_paris_{topic}",
        "city_name": "paris",
        "poi_name": "X",
        "lens": lens,
        "book_slug": "around_and_about_paris",
        "topic_slug": topic,
        "source_chunk_slug": chunk,
        "script_body_hash": body_hash,
        "source_passage": source_passage,
    }


def _setup_book(tmp_path, beats, grandfather_hashes=None) -> Path:
    (tmp_path / "data" / "paris").mkdir(parents=True, exist_ok=True)
    book_dir = tmp_path / "Books" / "Paris" / "around-and-about-paris"
    book_dir.mkdir(parents=True, exist_ok=True)
    (book_dir / "chunk-01.txt").write_text(_BOOK_CHUNK)
    if grandfather_hashes is not None:
        (tmp_path / "data" / "paris" / "grounding_grandfathered.json").write_text(
            json.dumps({"exempt_script_body_hashes": grandfather_hashes})
        )
    target = tmp_path / "data" / "paris" / "beats.json"
    target.write_text(json.dumps(beats))
    return target


def test_validator_book_grounded_passes(tmp_path):
    target = _setup_book(tmp_path, [_book_beat("a", "historic_arch", _BOOK_CHUNK, "h1")])
    result = run_validator(target)
    assert result.returncode == 0, result.stdout + result.stderr


def test_validator_book_ungrounded_fails(tmp_path):
    """A non-grandfathered book beat that doesn't quote its chunk is blocked."""
    target = _setup_book(tmp_path, [_book_beat("b", "war_conflict", _BOOK_UNGROUNDED_SP, "h2")])
    result = run_validator(target)
    assert result.returncode == 1
    assert "BOOK_UNGROUNDED" in result.stdout


def test_validator_book_grandfathered_exempt(tmp_path):
    """A legacy beat whose hash is grandfathered is exempt even if ungrounded."""
    target = _setup_book(
        tmp_path,
        [_book_beat("c", "dark_history", _BOOK_UNGROUNDED_SP, "h3")],
        grandfather_hashes=["h3"],
    )
    result = run_validator(target)
    assert result.returncode == 0, result.stdout + result.stderr


def test_validator_book_grandfather_invalidated_on_edit(tmp_path):
    """Editing a grandfathered beat changes its hash → no longer exempt → enforced."""
    # grandfather lists the OLD hash; the beat now carries a NEW hash (simulated edit)
    target = _setup_book(
        tmp_path,
        [_book_beat("c", "dark_history", _BOOK_UNGROUNDED_SP, "h3_new")],
        grandfather_hashes=["h3_old"],
    )
    result = run_validator(target)
    assert result.returncode == 1
    assert "BOOK_UNGROUNDED" in result.stdout


def test_validator_book_unlocatable_chunk_soft_skips(tmp_path):
    """A book beat whose chunk file doesn't exist soft-skips (never breaks commits)."""
    beat = _book_beat("d", "hidden_history", _BOOK_UNGROUNDED_SP, "h4", chunk="chunk-99")
    target = _setup_book(tmp_path, [beat])
    result = run_validator(target)
    assert result.returncode == 0, result.stdout + result.stderr


def test_validator_book_legacy_chunk_soft_skips(tmp_path):
    """A legacy-sentinel chunk slug soft-skips."""
    beat = _book_beat("e", "hidden_history", _BOOK_UNGROUNDED_SP, "h5", chunk="legacy_ambiguous")
    target = _setup_book(tmp_path, [beat])
    result = run_validator(target)
    assert result.returncode == 0, result.stdout + result.stderr


# ── commit-time verification-freshness gate ──


def _verified_beat(body_hash: str, verified_body_hash: str | None) -> dict:
    fc = {"status": "verified"}
    if verified_body_hash is not None:
        fc["verified_body_hash"] = verified_body_hash
    return {
        "beat_id": "paris_x_historic_arch_legacy_unknown_t",
        "city_name": "paris",
        "poi_name": "X",
        "lens": "historic_arch",
        "book_slug": "legacy_unknown",  # skips grounding + identity wildcard
        "topic_slug": "t",
        "source_chunk_slug": "legacy_ambiguous",
        "script_body_hash": body_hash,
        "fact_check": fc,
    }


def test_validator_verified_matching_hash_passes(tmp_path):
    target = tmp_path / "beats.json"
    target.write_text(json.dumps([_verified_beat("hX", "hX")]))
    assert run_validator(target).returncode == 0


def test_validator_verified_stale_hash_fails(tmp_path):
    """A verified beat whose body changed since verification is blocked."""
    target = tmp_path / "beats.json"
    target.write_text(json.dumps([_verified_beat("hNEW", "hOLD")]))
    result = run_validator(target)
    assert result.returncode == 1
    assert "VERIFICATION_STALE" in result.stdout


def test_validator_verified_without_stamp_passes(tmp_path):
    """A verified beat with no verified_body_hash is unprotected, not failed."""
    target = tmp_path / "beats.json"
    target.write_text(json.dumps([_verified_beat("hX", None)]))
    assert run_validator(target).returncode == 0


# ── controlled fact_check.status vocabulary ──


def _status_beat(status) -> dict:
    return {
        "beat_id": "paris_x_historic_arch_legacy_unknown_t",
        "city_name": "paris",
        "poi_name": "X",
        "lens": "historic_arch",
        "book_slug": "legacy_unknown",
        "topic_slug": "t",
        "source_chunk_slug": "legacy_ambiguous",
        "script_body_hash": "hZ",
        "fact_check": {"status": status},
    }


def test_validator_status_invalid_fails(tmp_path):
    """An off-vocabulary status (e.g. the 'dispute' typo) is blocked at commit."""
    target = tmp_path / "beats.json"
    target.write_text(json.dumps([_status_beat("dispute")]))
    result = run_validator(target)
    assert result.returncode == 1
    assert "STATUS_INVALID" in result.stdout


def test_validator_status_valid_passes(tmp_path):
    target = tmp_path / "beats.json"
    target.write_text(json.dumps([_status_beat("disputed")]))
    assert run_validator(target).returncode == 0
