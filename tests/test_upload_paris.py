"""Tests for upload_paris hardening: city geofence, disputed-exclusion, the
pre-upload validate_beats gate, and (Step 4.0) the provenance fields —
source_passage / source_chunk_slug / key_claims — on both the upload path and
the targeted backfill (which must never run the audio_url-wiping full upload)."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.upload_paris import (
    PARIS_BBOX,
    _assert_beats_valid,
    _backfill_provenance,
    _beat_blocked,
    _city_paths,
    _ensure_lenses,
    _in_city_bounds,
    _provenance_fields,
    _upload_beats,
    _upload_pois,
    publish_beats,
)
from src.api.models.nodes import canonical_name_key
from src.connection import get_database
from src.ingest import model
from tests import ingest_job_script as script_mod
from tests.conftest import needs_neo4j
from tests.ingest_helpers import stamp

_REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# _city_paths resolves the corpus root via the hermetic-aware helper: with
# ONBOARD_DATA_ROOT set the deploy subprocess reads {tmp}/{slug}; unset →
# <repo>/data/{slug} exactly (real paris/new_york deploys unchanged).
# ---------------------------------------------------------------------------
def test_city_paths_defaults_to_repo_data_when_unset(monkeypatch) -> None:
    """Unset ONBOARD_DATA_ROOT → <repo>/data/{slug}/... — byte-identical to the old
    hardcoded path. [undo: n/a for unset; guards the default is preserved]"""
    monkeypatch.delenv("ONBOARD_DATA_ROOT", raising=False)
    poi, beats = _city_paths("london")
    assert poi == _REPO_ROOT / "data" / "london" / "poi-raw.json"
    assert beats == _REPO_ROOT / "data" / "london" / "beats.json"


def test_city_paths_honors_onboard_data_root(monkeypatch, tmp_path) -> None:
    """ONBOARD_DATA_ROOT=tmp → {tmp}/{slug}/... so the deploy reads the hermetic
    corpus. [undo: hardcode REPO_ROOT/data → RED when the env is set]"""
    monkeypatch.setenv("ONBOARD_DATA_ROOT", str(tmp_path))
    poi, beats = _city_paths("london")
    assert poi == tmp_path / "london" / "poi-raw.json"
    assert beats == tmp_path / "london" / "beats.json"


def test_in_city_bounds_accepts_paris():
    assert _in_city_bounds(48.8566, 2.3522)


@pytest.mark.parametrize("lat,lon", [(42.36, -71.06), (0.0, 0.0), (48.85, 3.50), (51.5, 2.3)])
def test_in_city_bounds_rejects_out_of_city(lat, lon):
    assert not _in_city_bounds(lat, lon)


def _beat(status: str) -> dict:
    return {"poi_name": "X", "script_body": "b", "fact_check": {"status": status}}


def test_beat_blocked_disputed():
    assert _beat_blocked(_beat("disputed"))


def test_beat_not_blocked_verified_or_unverified():
    assert not _beat_blocked(_beat("verified"))
    assert not _beat_blocked(_beat("unverified"))


def test_beat_blocked_missing_essentials():
    assert _beat_blocked({"poi_name": "X", "script_body": ""})
    assert _beat_blocked({"poi_name": "", "script_body": "b"})


def _min_beat(beat_id: str, topic: str, body_hash: str) -> dict:
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


def test_assert_beats_valid_passes(tmp_path):
    p = tmp_path / "beats.json"
    p.write_text(json.dumps([_min_beat("a", "t", "h1")]))
    _assert_beats_valid(p)  # must not raise


def test_assert_beats_valid_raises_on_invalid(tmp_path):
    """A dup script_body_hash trips validate_beats → upload is refused."""
    p = tmp_path / "beats.json"
    p.write_text(json.dumps([_min_beat("a", "t1", "dup"), _min_beat("b", "t2", "dup")]))
    with pytest.raises(RuntimeError):
        _assert_beats_valid(p)


# ---------------------------------------------------------------------------
# Step 4.0 — provenance fields (source_passage / source_chunk_slug / key_claims)
# ---------------------------------------------------------------------------


def test_provenance_fields_present():
    beat = {
        "source_passage": "  The bridge was completed in 1607.  ",
        "source_chunk_slug": "pariswalks-chunk-03",
        "key_claims": ["Pont Neuf completed 1607", "  ", "Oldest standing bridge in Paris"],
    }
    fields = _provenance_fields(beat)
    assert fields["source_passage"] == "The bridge was completed in 1607."
    assert fields["source_chunk_slug"] == "pariswalks-chunk-03"
    assert fields["key_claims"] == ["Pont Neuf completed 1607", "Oldest standing bridge in Paris"]


def test_provenance_fields_absent_normalize_to_none():
    """Missing / empty / whitespace-only inputs all map to None so Neo4j's
    SET-null-removes-property semantics keep the keys absent, never null."""
    for beat in ({}, {"source_passage": "", "source_chunk_slug": "  ", "key_claims": []},
                 {"key_claims": ["", "   "]}):
        fields = _provenance_fields(beat)
        assert fields == {
            "source_passage": None,
            "source_chunk_slug": None,
            "key_claims": None,
        }


def _provenance_beat(beat_id: str, poi_name: str, *, with_fields: bool) -> dict:
    beat = {
        "beat_id": beat_id,
        "poi_name": poi_name,
        "script_body": "A fact. Another fact.",
        "fact_check": {"status": "verified"},
    }
    if with_fields:
        beat.update(
            source_passage="A fact from the source book.",
            source_chunk_slug="test-book-chunk-01",
            key_claims=["claim one", "claim two"],
        )
    return beat


@needs_neo4j
class TestProvenanceUploadAndBackfill:
    POI_NAME = "Provenance Test POI"

    def _seed_poi(self, driver) -> None:
        with driver.session(database=get_database()) as s:
            s.run(
                "MERGE (p:POI {name: $name}) SET p.id = 'prov-test-poi', p.city_name = 'paris'",
                name=self.POI_NAME,
            )

    def _beat_props(self, driver, beat_id: str) -> dict:
        with driver.session(database=get_database()) as s:
            rec = s.run(
                "MATCH (b:NarrativeBeat {beat_id: $bid}) RETURN properties(b) AS props",
                bid=beat_id,
            ).single()
            return rec["props"] if rec else {}

    def test_upload_writes_provenance_and_absence_stays_absent(self, clean_driver):
        self._seed_poi(clean_driver)
        beats = [
            _provenance_beat("prov-b1", self.POI_NAME, with_fields=True),
            _provenance_beat("prov-b2", self.POI_NAME, with_fields=False),
        ]
        with clean_driver.session(database=get_database()) as s:
            stats = _upload_beats(s, beats, "paris")
        assert stats["linked"] == 2

        b1 = self._beat_props(clean_driver, "prov-b1")
        assert b1["source_passage"] == "A fact from the source book."
        assert b1["source_chunk_slug"] == "test-book-chunk-01"
        assert list(b1["key_claims"]) == ["claim one", "claim two"]

        b2 = self._beat_props(clean_driver, "prov-b2")
        assert "source_passage" not in b2
        assert "source_chunk_slug" not in b2
        assert "key_claims" not in b2

    def test_backfill_sets_only_provenance_on_existing_beats(self, clean_driver):
        """The targeted backfill sets the three fields by beat_id and leaves
        every other property (esp. audio_url) untouched."""
        with clean_driver.session(database=get_database()) as s:
            s.run(
                "MERGE (b:NarrativeBeat {beat_id: 'prov-b3'}) "
                "SET b.id = 'prov-b3-id', b.script_body = 'Body.', "
                "    b.audio_url = 'https://example.com/existing.mp3'"
            )
            stats = _backfill_provenance(
                s,
                [
                    _provenance_beat("prov-b3", self.POI_NAME, with_fields=True),
                    _provenance_beat("prov-missing", self.POI_NAME, with_fields=True),
                    _provenance_beat("prov-no-fields", self.POI_NAME, with_fields=False),
                ],
            )
        # prov-missing has no node (MATCH skips); prov-no-fields has nothing to set.
        assert stats == {"updated": 1, "candidates": 2}
        b3 = self._beat_props(clean_driver, "prov-b3")
        assert list(b3["key_claims"]) == ["claim one", "claim two"]
        assert b3["source_passage"] == "A fact from the source book."
        assert b3["audio_url"] == "https://example.com/existing.mp3"

    def test_full_upload_preserves_audio_url_on_reupload(self, clean_driver):
        """A repo->graph re-deploy via the FULL upload path must NOT wipe live
        audio_url (it is stamped ON CREATE only); every other field re-syncs.
        This makes `make deploy TARGET=cloud` / a re-deploy safe to run repeatedly."""
        self._seed_poi(clean_driver)
        beat = _provenance_beat("prov-audio", self.POI_NAME, with_fields=True)
        with clean_driver.session(database=get_database()) as s:
            _upload_beats(s, [beat], "paris")
        assert self._beat_props(clean_driver, "prov-audio")["audio_url"] == ""
        # Simulate generated TTS audio + a stale body landing on the live beat.
        with clean_driver.session(database=get_database()) as s:
            s.run(
                "MATCH (b:NarrativeBeat {beat_id: 'prov-audio'}) SET "
                "b.audio_url = 'https://example.com/generated.mp3', b.script_body = 'STALE'"
            )
        # Re-deploy the same beat with an edited body.
        beat["script_body"] = "RESYNCED BODY"
        with clean_driver.session(database=get_database()) as s:
            _upload_beats(s, [beat], "paris")
        props = self._beat_props(clean_driver, "prov-audio")
        assert props["audio_url"] == "https://example.com/generated.mp3", (
            "re-deploy WIPED live audio_url — audio_url must be ON CREATE only"
        )
        assert props["script_body"] == "RESYNCED BODY", "re-deploy must re-sync other fields"


# ---------------------------------------------------------------------------
# Defect: _upload_beats MERGEs NarrativeBeat on unconstrained beat_id. Two
# distinct beats that both carry an empty/missing beat_id would MATCH the same
# {beat_id: ""} node and SET-overwrite each other, silently collapsing two
# beats into one node (no uniqueness constraint on NarrativeBeat.beat_id
# catches it). Empty-beat_id beats must be skipped, never merged.
# ---------------------------------------------------------------------------


@needs_neo4j
class TestEmptyBeatIdNotCollapsed:
    POI_NAME = "Empty Beat Id POI"

    def _seed_poi(self, driver) -> None:
        with driver.session(database=get_database()) as s:
            s.run(
                "MERGE (p:POI {name: $name}) SET p.id = 'empty-bid-poi', p.city_name = 'paris'",
                name=self.POI_NAME,
            )

    def _beat(self, beat_id: str, body: str) -> dict:
        return {
            "beat_id": beat_id,
            "poi_name": self.POI_NAME,
            "script_body": body,
            "fact_check": {"status": "verified"},
        }

    def test_distinct_empty_beat_id_beats_do_not_collapse(self, clean_driver):
        """Two beats with empty beat_id must not both MERGE onto {beat_id: ''}
        and overwrite each other. They are skipped, so no single node ends up
        silently carrying only the last beat's body."""
        self._seed_poi(clean_driver)
        beats = [
            self._beat("", "First distinct body."),
            self._beat("", "Second distinct body."),
            self._beat("real-id", "A real beat that still uploads."),
        ]
        with clean_driver.session(database=get_database()) as s:
            stats = _upload_beats(s, beats, "paris")

        # Both empty-beat_id beats are refused; the real one uploads.
        assert stats["no_beat_id"] == 2
        assert stats["linked"] == 1

        with clean_driver.session(database=get_database()) as s:
            empty = s.run(
                "MATCH (b:NarrativeBeat {beat_id: ''}) RETURN count(b) AS c"
            ).single()["c"]
            real = s.run(
                "MATCH (b:NarrativeBeat {beat_id: 'real-id'}) RETURN b.script_body AS body"
            ).single()
        # No empty-beat_id node was created (no silent collapse), and the
        # legitimate beat is intact.
        assert empty == 0
        assert real["body"] == "A real beat that still uploads."


# ---------------------------------------------------------------------------
# Defect: POI MERGE key must be (name_key, city_name) — casing/whitespace name
# variants must dedup onto a single node, matching the API create_node path,
# not fork into duplicate POIs with split beat sets.
# ---------------------------------------------------------------------------


def _poi(name: str) -> dict:
    # A valid in-bounds Paris POI (Notre-Dame) with the canonical dedup key
    # computed the same way _upload_pois does.
    return {
        "name": name,
        "name_key": canonical_name_key(name),
        "city_name": "paris",
        "latitude": 48.8530,
        "longitude": 2.3499,
    }


@needs_neo4j
class TestPoiMergeKeyDedup:
    def _poi_nodes_for(self, driver, name_key: str) -> list[dict]:
        with driver.session(database=get_database()) as s:
            recs = s.run(
                "MATCH (p:POI {name_key: $nk, city_name: 'paris'}) "
                "RETURN properties(p) AS props",
                nk=name_key,
            )
            return [r["props"] for r in recs]

    def test_casing_whitespace_variants_dedup_to_one_poi(self, clean_driver):
        """Two variants that differ only by casing/whitespace share one name_key,
        so the upload path MERGEs onto a single POI node instead of forking —
        the exact fork the name_key canonicalization was built to prevent."""
        name_key = canonical_name_key("Notre-Dame")

        with clean_driver.session(database=get_database()) as s:
            _upload_pois(s, [_poi("Notre-Dame")], "paris", PARIS_BBOX)
        with clean_driver.session(database=get_database()) as s:
            # Casing + trailing whitespace variant — same canonical key.
            _upload_pois(s, [_poi("notre-dame  ")], "paris", PARIS_BBOX)

        nodes = self._poi_nodes_for(clean_driver, name_key)
        assert len(nodes) == 1, (
            f"Expected one deduped POI, got {len(nodes)}: "
            f"{[n.get('name') for n in nodes]}"
        )
        # Display casing is preserved (last-written form).
        assert nodes[0]["name"] == "notre-dame  "
        assert nodes[0]["name_key"] == name_key


# ---------------------------------------------------------------------------
# Showstopper: the beat→POI link must be CITY-SCOPED. Many POI names recur
# across cities (Chinatown, SoHo, Greenwich Village, Chelsea and Cleopatra's
# Needle all appeared in BOTH the New York corpus and the since-retired London
# one, which is how this was found). A name-only match
# would MERGE a London beat onto New York's identically-named POI — seating
# London beats on NYC tours and failing db_parity. The link must additionally
# constrain city_name = the city being deployed.
# ---------------------------------------------------------------------------


@needs_neo4j
class TestBeatPoiLinkCityScoped:
    def _seed_poi(self, driver, name: str, city: str, marker: str) -> None:
        with driver.session(database=get_database()) as s:
            s.run(
                "CREATE (p:POI {name: $name, city_name: $city, id: $marker})",
                name=name,
                city=city,
                marker=marker,
            )

    def _beat(self, beat_id: str, poi_name: str, city: str) -> dict:
        return {
            "beat_id": beat_id,
            "poi_name": poi_name,
            "city_name": city,
            "script_body": "A beat about a place that shares its name across cities.",
            "fact_check": {"status": "verified"},
        }

    def test_cross_city_name_collision_does_not_link(self, clean_driver):
        """A London beat whose poi_name ("Chinatown") ALSO exists in New York
        must NOT attach to the New York POI when no London POI of that name
        exists. [undo: drop `city_name: $city` from the OPTIONAL MATCH → the
        london beat MERGEs a HAS_BEAT onto the NY Chinatown POI → RED]"""
        self._seed_poi(clean_driver, "Chinatown", "new_york", "ny-chinatown-1")
        with clean_driver.session(database=get_database()) as s:
            stats = _upload_beats(
                s, [self._beat("london_chinatown_1", "Chinatown", "london")], "london"
            )
        # No London POI named Chinatown exists → the beat links to nothing.
        assert stats["linked"] == 0
        with clean_driver.session(database=get_database()) as s:
            leaked = s.run(
                "MATCH (p:POI {id: 'ny-chinatown-1'})-[:HAS_BEAT]->(b) RETURN count(b) AS c"
            ).single()["c"]
            beat_exists = s.run(
                "MATCH (b:NarrativeBeat {beat_id: 'london_chinatown_1'}) RETURN count(b) AS c"
            ).single()["c"]
        assert leaked == 0, "London beat leaked onto the New York Chinatown POI"
        # City-blind match would have created + linked the beat; scoped match
        # never creates it (WITH ... WHERE p IS NOT NULL filters it out).
        assert beat_exists == 0

    def test_same_city_poi_still_links(self, clean_driver):
        """The scope must not over-block: when a London POI of that name DOES
        exist, the London beat attaches to it (and never to the NY twin)."""
        self._seed_poi(clean_driver, "SoHo", "new_york", "ny-soho-2")
        self._seed_poi(clean_driver, "SoHo", "london", "ldn-soho-2")
        with clean_driver.session(database=get_database()) as s:
            stats = _upload_beats(
                s, [self._beat("london_soho_2", "SoHo", "london")], "london"
            )
        assert stats["linked"] == 1
        with clean_driver.session(database=get_database()) as s:
            ldn = s.run(
                "MATCH (p:POI {id: 'ldn-soho-2'})-[:HAS_BEAT]->(b) RETURN count(b) AS c"
            ).single()["c"]
            ny = s.run(
                "MATCH (p:POI {id: 'ny-soho-2'})-[:HAS_BEAT]->(b) RETURN count(b) AS c"
            ).single()["c"]
        assert ldn == 1, "London beat did not attach to its own London POI"
        assert ny == 0, "London beat leaked onto the New York SoHo POI"


# ---------------------------------------------------------------------------
# Slice 8 (Docs/ingestion/rebuild-spec.md §6): the publisher CONVERGES the
# graph on the file. After every beat in the file is MERGEd, every beat of
# that city the file no longer names is withdrawn (active_status =
# 'withdrawn', HAS_BEAT kept) so a re-publish restores it. New-shape records
# (§2) are exported through the legacy field contract the tour engine reads.
# The records come from tests/ingest_job_script.py: valid on disk, spans
# verbatim in the real Lonely Planet chunk-07.
# ---------------------------------------------------------------------------


def _beat_status(driver, beat_id: str) -> tuple[str | None, int]:
    """(active_status, number of HAS_BEAT edges into the beat)."""
    with driver.session(database=get_database()) as s:
        rec = s.run(
            "MATCH (b:NarrativeBeat {beat_id: $bid}) "
            "OPTIONAL MATCH (:POI)-[r:HAS_BEAT]->(b) "
            "RETURN b.active_status AS st, count(r) AS edges",
            bid=beat_id,
        ).single()
    assert rec is not None, f"{beat_id} is not in the graph"
    return rec["st"], rec["edges"]


def _seed_place(driver, name: str, city: str) -> None:
    with driver.session(database=get_database()) as s:
        s.run(
            "MERGE (p:POI {name: $name, city_name: $city}) SET p.id = coalesce(p.id, $name)",
            name=name,
            city=city,
        )


@needs_neo4j
def test_publish_withdraws_beats_absent_from_file(clean_driver):
    """§6: publish a two-beat new-shape file; re-publish with one beat
    removed → that beat is withdrawn with its HAS_BEAT kept, the other is
    still active; publish both again → the withdrawn beat is active again.
    [undo: drop the withdraw step → the removed beat stays 'active' → RED]"""
    records = script_mod.existing_records()
    origins, visiting = records
    _seed_place(clean_driver, script_mod.PLACE, script_mod.CITY)

    with clean_driver.session(database=get_database()) as s:
        stats = publish_beats(s, records, script_mod.CITY)
    assert stats["linked"] == 2 and stats["withdrawn"] == 0
    assert _beat_status(clean_driver, script_mod.ORIGINS_ID) == ("active", 1)
    assert _beat_status(clean_driver, script_mod.VISITING_ID) == ("active", 1)

    with clean_driver.session(database=get_database()) as s:
        stats = publish_beats(s, [origins], script_mod.CITY)
    assert stats["linked"] == 1 and stats["withdrawn"] == 1
    assert _beat_status(clean_driver, script_mod.ORIGINS_ID) == ("active", 1)
    assert _beat_status(clean_driver, script_mod.VISITING_ID) == ("withdrawn", 1), (
        "a beat the file no longer names must be withdrawn, its HAS_BEAT kept"
    )

    with clean_driver.session(database=get_database()) as s:
        stats = publish_beats(s, [origins, visiting], script_mod.CITY)
    assert stats["linked"] == 2 and stats["withdrawn"] == 0
    assert _beat_status(clean_driver, script_mod.VISITING_ID) == ("active", 1), (
        "re-publishing a withdrawn beat must restore it"
    )


def _variant(base: dict, story_slug: str, **fields) -> dict:
    """A copy of `base` as another story at the same place, hashes restamped."""
    rec = deepcopy(base)
    rec["story_slug"] = story_slug
    rec["beat_id"] = f"{script_mod.CITY}/{model.slug(script_mod.PLACE)}/{story_slug}"
    rec.update(fields)
    return stamp(rec)


#: duration_sec → beat_length_class, the §2 thresholds as literals
#: (micro <8 s, seasoning <32 s, mid <80 s, else anchor).
_LENGTH_CLASSES = [
    (7, "micro"),
    (8, "seasoning"),
    (31, "seasoning"),
    (32, "mid"),
    (79, "mid"),
    (80, "anchor"),
]


@needs_neo4j
def test_new_shape_export_mapping(clean_driver, tmp_path):
    """§2 graph export: script_body = narration.text; key_claims = the
    RESOLVED claims' texts only (a superseded claim is left out);
    beat_length_class from duration_sec at the spec's thresholds; one
    TAGGED_WITH per `lenses` entry; active_status 'active'; and a beat
    with review.held = true is not published at all. The file validates
    under the real chunk first, so this is the shape slice 10 will publish.
    [undo: export key_claims from every claim → the superseded text leaks → RED]"""
    chunks = script_mod.chunk_dir(tmp_path)
    origins, visiting = script_mod.existing_records()
    superseded = deepcopy(origins["claims"][1])
    superseded.update(
        claim_id="c03",
        text=script_mod.OPENED_1939["text"],
        status="superseded",
    )
    superseded["sources"][0]["span"] = script_mod.OPENED_1939["span"]
    mapped = _variant(
        origins,
        "how-the-museum-came-to-be",
        claims=[*origins["claims"], superseded],
        lenses=["hidden_history", "visual_art"],
        duration_sec=46,
    )
    lengths = [
        _variant(visiting, f"length-{sec}", duration_sec=sec) for sec, _ in _LENGTH_CLASSES
    ]
    held = _variant(visiting, "held-story", review={"held": True, "reason": "merge held"})
    records = [mapped, *lengths, held]
    assert model.validate(records, chunks_root=chunks.parent) == []

    _seed_place(clean_driver, script_mod.PLACE, script_mod.CITY)
    with clean_driver.session(database=get_database()) as s:
        _ensure_lenses(s, {"hidden_history", "visual_art"})
        stats = publish_beats(s, records, script_mod.CITY)
    assert stats["linked"] == 1 + len(_LENGTH_CLASSES)
    assert stats["blocked"] == 1, "a held beat is the new-shape analogue of a disputed one"

    with clean_driver.session(database=get_database()) as s:
        props = s.run(
            "MATCH (b:NarrativeBeat {beat_id: $bid}) RETURN properties(b) AS p",
            bid=mapped["beat_id"],
        ).single()["p"]
        lenses = {
            r["l"]
            for r in s.run(
                "MATCH (b:NarrativeBeat {beat_id: $bid})-[:TAGGED_WITH]->(l:Lens) "
                "RETURN l.name AS l",
                bid=mapped["beat_id"],
            )
        }
        classes = {
            r["bid"]: r["c"]
            for r in s.run(
                "MATCH (b:NarrativeBeat) WHERE b.beat_id STARTS WITH $prefix "
                "RETURN b.beat_id AS bid, b.beat_length_class AS c",
                prefix=f"{script_mod.CITY}/{model.slug(script_mod.PLACE)}/length-",
            )
        }
        held_nodes = s.run(
            "MATCH (b:NarrativeBeat {beat_id: $bid}) RETURN count(b) AS c", bid=held["beat_id"]
        ).single()["c"]

    assert props["script_body"] == script_mod.ORIGINS_NARRATION
    assert list(props["key_claims"]) == [
        script_mod.COLLECTING["text"],
        script_mod.COMPLETED["text"],
    ], "key_claims must carry the resolved claims only, never the superseded one"
    assert props["beat_length_class"] == "mid"
    assert props["active_status"] == "active"
    assert props["duration_sec"] == 46
    assert lenses == {"hidden_history", "visual_art"}
    assert classes == {
        f"{script_mod.CITY}/{model.slug(script_mod.PLACE)}/length-{sec}": cls
        for sec, cls in _LENGTH_CLASSES
    }
    assert held_nodes == 0, "a review.held beat must not be published"


@needs_neo4j
def test_legacy_lens_tags_one_relationship(clean_driver):
    """Both real cities are still legacy-shape: a record's single `lens` must
    keep producing exactly one TAGGED_WITH edge through the per-entry tagging
    slice 8 introduced. [undo: `_lenses_of` legacy branch → [] → RED]"""
    _seed_place(clean_driver, "Legacy Lens POI", "paris")
    beat = {
        "beat_id": "legacy-lens-1",
        "poi_name": "Legacy Lens POI",
        "script_body": "A legacy beat with one lens.",
        "lens": "historic_arch",
        "fact_check": {"status": "verified"},
    }
    with clean_driver.session(database=get_database()) as s:
        _ensure_lenses(s, {"historic_arch"})
        stats = _upload_beats(s, [beat], "paris")
        lenses = [
            r["l"]
            for r in s.run(
                "MATCH (b:NarrativeBeat {beat_id: 'legacy-lens-1'})-[:TAGGED_WITH]->(l:Lens) "
                "RETURN l.name AS l"
            )
        ]
    assert stats["linked"] == 1 and stats["tagged"] == 1
    assert lenses == ["historic_arch"]
