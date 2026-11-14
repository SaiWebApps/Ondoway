"""Tests for the name_key backfill migration and the seed/upload write paths.

Covers:
  - canonical_name_key collapses casing/whitespace to one key;
  - the localhost guard refuses a cloud URI unless --allow-cloud;
  - plan_backfill selects only POIs missing/stale name_key (idempotent re-plan);
  - the collision detector flags ≥2 POIs whose names differ only by case/space;
  - apply_backfill SETs name_key on a node that lacks it (against local 7688,
    via the port-guarded clean_driver fixture) and never merges/deletes;
  - the seed and bulk-upload paths write name_key.
"""

from __future__ import annotations

import pytest

from scripts import backfill_name_key as bnk
from scripts.upload_paris import PARIS_BBOX, _upload_pois
from src.api.models.nodes import canonical_name_key
from src.connection import get_database
from src.seed.locations import seed_pois
from tests.conftest import needs_neo4j

# ── canonical_name_key: casing/whitespace collapse (single source of truth) ──


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Notre-Dame", "notre-dame"),
        ("notre-dame", "notre-dame"),
        ("Notre-Dame ", "notre-dame"),
        ("  Notre-Dame  ", "notre-dame"),
        ("Notre-Dame\tde   Paris", "notre-dame de paris"),
        ("PLACE des Vosges", "place des vosges"),
    ],
)
def test_canonical_name_key_collapses_casing_and_whitespace(raw, expected):
    assert canonical_name_key(raw) == expected


def test_canonical_name_key_variants_map_to_one_key():
    variants = ["Café de Flore", "café de flore", "  Café  de  Flore ", "CAFÉ DE FLORE"]
    keys = {canonical_name_key(v) for v in variants}
    assert len(keys) == 1


# ── localhost guard: refuse cloud URI unless --allow-cloud, fires pre-connect ──


def test_guard_refuses_cloud_uri_without_flag(monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "neo4j+s://abc123.databases.neo4j.io")
    with pytest.raises(SystemExit) as exc:
        bnk._assert_local_target(allow_cloud=False)
    assert "REFUSING" in str(exc.value)


def test_guard_allows_cloud_uri_with_flag(monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "neo4j+s://abc123.databases.neo4j.io")
    # Must not raise when the operator explicitly opts in.
    bnk._assert_local_target(allow_cloud=True)


@pytest.mark.parametrize(
    "uri",
    [
        "bolt://localhost:7687",
        "bolt://127.0.0.1:7688",
        "neo4j://localhost:7687",
    ],
)
def test_guard_allows_localhost(monkeypatch, uri):
    monkeypatch.setenv("NEO4J_URI", uri)
    bnk._assert_local_target(allow_cloud=False)  # no raise


# ── plan_backfill: select only missing/stale, idempotent re-plan ──


def test_plan_backfill_selects_missing_and_stale():
    pois = [
        {"id": "1", "name": "Notre-Dame", "name_key": None, "city_name": "paris"},
        # id 2 already canonical → skipped; id 3 has a stale key → re-set.
        {"id": "2", "name": "Louvre", "name_key": "louvre", "city_name": "paris"},
        {"id": "3", "name": "Panthéon", "name_key": "PANTHÉON", "city_name": "paris"},
    ]
    to_set, no_name = bnk.plan_backfill(pois)
    ids = {p["id"]: p["target_name_key"] for p in to_set}
    assert ids == {"1": "notre-dame", "3": "panthéon"}
    assert no_name == []


def test_plan_backfill_is_idempotent_after_canonical():
    pois = [
        {"id": "1", "name": "Notre-Dame", "name_key": "notre-dame", "city_name": "paris"},
        {"id": "2", "name": "Louvre", "name_key": "louvre", "city_name": "paris"},
    ]
    to_set, no_name = bnk.plan_backfill(pois)
    assert to_set == []
    assert no_name == []


def test_plan_backfill_reports_nameless_rows():
    pois = [{"id": "x", "name": "", "name_key": None, "city_name": "paris"}]
    to_set, no_name = bnk.plan_backfill(pois)
    assert to_set == []
    assert [p["id"] for p in no_name] == ["x"]


# ── collision detector: flag names differing only by case/space ──


def test_detect_collisions_flags_case_and_space_variants():
    pois = [
        {"id": "a", "name": "Notre-Dame", "name_key": None, "city_name": "paris"},
        {"id": "b", "name": "notre-dame ", "name_key": None, "city_name": "paris"},
        {"id": "c", "name": "Louvre", "name_key": None, "city_name": "paris"},
    ]
    clusters = bnk.detect_collisions(pois)
    assert len(clusters) == 1
    cluster_ids = {p["id"] for p in clusters[0]}
    assert cluster_ids == {"a", "b"}


def test_detect_collisions_respects_city_scope():
    # Same name, different city → NOT a collision (multi-city safe).
    pois = [
        {"id": "a", "name": "Notre-Dame", "name_key": None, "city_name": "paris"},
        {"id": "b", "name": "Notre-Dame", "name_key": None, "city_name": "reims"},
    ]
    assert bnk.detect_collisions(pois) == []


def test_detect_collisions_none_when_all_distinct():
    pois = [
        {"id": "a", "name": "Louvre", "name_key": None, "city_name": "paris"},
        {"id": "b", "name": "Panthéon", "name_key": None, "city_name": "paris"},
    ]
    assert bnk.detect_collisions(pois) == []


# ── DB-backed: apply sets name_key on a node lacking it; never merges/deletes ──


@needs_neo4j
class TestBackfillAgainstLocalDB:
    def test_apply_sets_name_key_and_is_idempotent(self, clean_driver):
        with clean_driver.session(database=get_database()) as s:
            # A POI with NO name_key (written before name_key existed).
            s.run(
                "CREATE (p:POI {id: 'nk-1', name: 'Notre-Dame ', city_name: 'paris'})"
            )
            before = bnk._fetch_pois(s, "paris")
            node_count_before = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]

            to_set, no_name = bnk.plan_backfill(before)
            assert [p["id"] for p in to_set] == ["nk-1"]
            assert no_name == []
            updated = bnk.apply_backfill(s, to_set)
            assert updated == 1

            rec = s.run(
                "MATCH (p:POI {id: 'nk-1'}) RETURN p.name_key AS nk, p.name AS name"
            ).single()
            assert rec["nk"] == "notre-dame"
            assert rec["name"] == "Notre-Dame "  # display casing preserved

            # Never merged/deleted: node count unchanged.
            node_count_after = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            assert node_count_after == node_count_before

            # Idempotent: re-plan yields nothing, re-apply sets 0.
            after = bnk._fetch_pois(s, "paris")
            again, _ = bnk.plan_backfill(after)
            assert again == []
            assert bnk.apply_backfill(s, again) == 0

    def test_apply_touches_only_stale_leaves_canonical_alone(self, clean_driver):
        with clean_driver.session(database=get_database()) as s:
            s.run(
                "CREATE (p:POI {id: 'nk-ok', name: 'Louvre', "
                "name_key: 'louvre', city_name: 'paris'})"
            )
            s.run(
                "CREATE (p:POI {id: 'nk-stale', name: 'Panthéon', "
                "name_key: 'WRONG', city_name: 'paris'})"
            )
            pois = bnk._fetch_pois(s, "paris")
            to_set, _ = bnk.plan_backfill(pois)
            assert {p["id"] for p in to_set} == {"nk-stale"}
            bnk.apply_backfill(s, to_set)
            rec = s.run(
                "MATCH (p:POI {id: 'nk-stale'}) RETURN p.name_key AS nk"
            ).single()
            assert rec["nk"] == "panthéon"


# ── write paths: seed + upload write name_key ──


@needs_neo4j
class TestWritePathsSetNameKey:
    def test_seed_pois_sets_name_key(self, clean_driver):
        seed_pois(clean_driver)
        with clean_driver.session(database=get_database()) as s:
            rows = s.run(
                "MATCH (p:POI) RETURN p.name AS name, p.name_key AS name_key"
            ).data()
        assert rows, "seed_pois created no POIs"
        for row in rows:
            assert row["name_key"] == canonical_name_key(row["name"]), row

    def test_upload_pois_sets_name_key(self, clean_driver):
        pois = [
            {
                "name": "Île de la Cité",
                "latitude": 48.8550,
                "longitude": 2.3470,
                "short_description": "Island in the Seine.",
                "importance_tier": 4,
                "trigger_radius": 10,
                "kid_friendly": "yes",
                "name_variations": [],
                "poi_role": "stop",
            }
        ]
        stats = _upload_pois(clean_driver, pois, "paris", PARIS_BBOX)
        assert stats["created"] == 1
        with clean_driver.session(database=get_database()) as s:
            rec = s.run(
                "MATCH (p:POI {name: 'Île de la Cité'}) RETURN p.name_key AS nk"
            ).single()
        assert rec["nk"] == canonical_name_key("Île de la Cité")
