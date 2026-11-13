"""Hermetic tests for _snapshot_from_records — the Neo4j → CorpusSnapshot
parse, including the provenance fields. No DB."""

from __future__ import annotations

import pytest

from src.tour.corpus_places import CoordinateProvenance
from src.tour.selection import LOAD_PARIS_BEATS_CYPHER, _snapshot_from_records


def _beat_record(**overrides) -> dict:
    base = {
        "id": "b1",
        "poi_id": "p1",
        "script_body": "Henri IV built the square.",
        "entities": ["Henri IV"],
        "active_status": "active",
        "lenses": ["hidden_history"],
    }
    base.update(overrides)
    return base


_POI_RECORD = {"id": "p1", "name": "Place des Vosges", "tier": 5, "poi_role": "stop",
               "lat": 48.85, "lng": 2.36, "areas": ["Le Marais"]}


def test_provenance_fields_parse_when_present():
    rec = _beat_record(
        source_passage="Henri IV built the square in 1612.",
        source_chunk_slug="vosges-ch3",
        key_claims=["Henri IV built it", "completed 1612"],
    )
    snap = _snapshot_from_records([_POI_RECORD], [rec], [], [], [])
    beat = snap.beats_for("p1")[0]
    assert beat.source_passage == "Henri IV built the square in 1612."
    assert beat.source_chunk_slug == "vosges-ch3"
    assert beat.key_claims == ("Henri IV built it", "completed 1612")


def test_provenance_fields_default_empty_when_absent():
    """The current corpus has no provenance props; the loader must not choke —
    Cypher returns null for a missing property, and the fields default."""
    snap = _snapshot_from_records([_POI_RECORD], [_beat_record()], [], [], [])
    beat = snap.beats_for("p1")[0]
    assert beat.source_passage is None
    assert beat.source_chunk_slug is None
    assert beat.key_claims == ()
    # existing fields still parse
    assert beat.script_body == "Henri IV built the square."
    assert beat.lenses == ("hidden_history",)


def test_key_claims_drops_blank_entries():
    rec = _beat_record(key_claims=["real claim", "", "  ", "another"])
    snap = _snapshot_from_records([_POI_RECORD], [rec], [], [], [])
    assert snap.beats_for("p1")[0].key_claims == ("real claim", "another")


def test_optional_stable_place_fields_round_trip_without_replacing_graph_ids():
    coordinates = CoordinateProvenance.build(
        authority_kind="corpus_record",
        authority_id="manifest:fixture",
        source_record_id="wikidata:Q1",
        coordinates=(48.85, 2.36),
        source_payload={"label": "Place des Vosges"},
    )
    poi_record = {
        **_POI_RECORD,
        "canonical_place_id": "paris:place-des-vosges",
        "aliases": ["Vosges", "Place Royale"],
        "coordinate_provenance": coordinates.model_dump_json(),
    }
    beat_record = _beat_record(
        stable_beat_id="paris:beat:vosges:1",
        place_plan_id="paris:place-plan:vosges:1",
        lenses=["history", "architecture"],
    )
    snap = _snapshot_from_records([poi_record], [beat_record], [], [], [])
    poi = snap.pois[0]
    beat = snap.beats_for("p1")[0]
    assert poi.id == "p1"  # graph/API identity is unchanged; the place fields are additive.
    assert poi.canonical_place_id == "paris:place-des-vosges"
    assert poi.aliases == ("Place Royale", "Vosges")
    assert poi.coordinate_provenance == coordinates
    assert beat.id == "b1"
    assert beat.stable_beat_id == "paris:beat:vosges:1"
    assert beat.place_plan_id == "paris:place-plan:vosges:1"
    assert beat.lenses == ("architecture", "history")


def test_placeholder_beats_without_stable_ids_are_excluded():
    """An `active` beat with beat_id NULL AND placeholder audio is a
    seed artifact, never corpus content, and must never reach a tour.

    MEASURED on the dev graph 2026-07-25: Eiffel Tower and Shakespeare and
    Company each carry TWO copies of the same seeded beat — one adopted into
    the corpus with a `dbsync_*` beat_id, one with beat_id NULL. Both point at
    s3://ondoway-audio/placeholder/*.mp3. db_parity.py:113 and
    prune_orphan_pois.py:48 both filter `beat_id IS NOT NULL`, so the NULL
    twins are structurally invisible to parity and to prune; only the loader
    can keep them out of a tour. The exclusion is the CONJUNCTION: two paris
    beats have placeholder audio *and* a real beat_id (real corpus content
    awaiting TTS), and dropping those would be data loss.
    """
    placeholder = _beat_record(
        id="b-placeholder",
        stable_beat_id=None,
        audio_url="s3://ondoway-audio/placeholder/eiffel_tower.mp3",
        script_body="Look up. Every rivet you see was placed by hand.",
    )
    adopted_twin = _beat_record(
        id="b-adopted",
        stable_beat_id="dbsync_5a8874d6",
        audio_url="s3://ondoway-audio/placeholder/eiffel_tower.mp3",
        script_body="Look up. Every rivet you see was placed by hand.",
    )
    corpus_beat = _beat_record(
        id="b-corpus",
        stable_beat_id=None,
        audio_url="s3://ondoway-audio/paris/vosges-1.mp3",
    )

    snap = _snapshot_from_records(
        [_POI_RECORD], [placeholder, adopted_twin, corpus_beat], [], [], []
    )

    beat_ids = [b.id for b in snap.beats_for("p1")]
    assert "b-placeholder" not in beat_ids
    # Negative controls: neither half of the conjunction excludes on its own.
    assert beat_ids == ["b-adopted", "b-corpus"]
    # POI.beat_count feeds the density gate — it must count the kept beats only.
    assert snap.pois[0].beat_count == 2
    # The filter is unreachable from Neo4j unless the loader actually selects
    # audio_url, so pin that the corpus query returns it.
    assert "b.audio_url" in LOAD_PARIS_BEATS_CYPHER


def test_present_malformed_coordinate_provenance_is_not_silently_dropped():
    with pytest.raises(ValueError, match="coordinate_provenance"):
        _snapshot_from_records(
            [{**_POI_RECORD, "coordinate_provenance": "not-json"}],
            [_beat_record()],
            [],
            [],
            [],
        )
