"""Offline regression tests for the WITHIN-edge staging file.

These tests validate `data/paris/within_edges.json` against `areas.json` and
`poi-raw.json` without requiring a running Neo4j or API. They guard against
regressions in the staging-generation pipeline.
"""

from __future__ import annotations

import json
from pathlib import Path

PARIS = Path(__file__).resolve().parent.parent / "data" / "paris"


def _load():
    return (
        json.load(open(PARIS / "areas.json")),
        json.load(open(PARIS / "poi-raw.json")),
        json.load(open(PARIS / "within_edges.json")),
    )


def test_no_orphan_pois_except_vincennes():
    _, pois, edges = _load()
    poi_names = {p["name"] for p in pois if p.get("latitude") is not None}
    covered = {e["poi_name"] for e in edges["poi_to_area"]}
    orphans = poi_names - covered
    assert orphans == {"Chateau de Vincennes"}, (
        f"Unexpected orphan POIs (only Vincennes is allowed outside Paris): {orphans}"
    )


def test_every_area_has_at_least_one_poi():
    areas, _, edges = _load()
    pois_per_area = {}
    for e in edges["poi_to_area"]:
        pois_per_area.setdefault(e["area_name"], 0)
        pois_per_area[e["area_name"]] += 1
    empty = [a["name"] for a in areas if pois_per_area.get(a["name"], 0) == 0]
    assert not empty, f"every Area must hold at least one POI; Areas with 0 POIs: {empty}"


def test_area_to_area_acyclic():
    areas, _, _ = _load()
    parents = {a["name"]: a.get("parent_area") for a in areas}
    for start in parents:
        seen = []
        node = start
        while node is not None:
            assert node not in seen, f"cycle in parent_area chain at {node}: {seen}"
            seen.append(node)
            node = parents.get(node)


def test_paris_is_only_root():
    areas, _, _ = _load()
    roots = [a["name"] for a in areas if a.get("parent_area") is None]
    assert roots == ["Paris"], f"expected single root 'Paris', got {roots}"


def test_le_marais_post_expansion_membership():
    """Le Marais's expanded polygon contains at least 40 POIs."""
    _, _, edges = _load()
    marais = [e for e in edges["poi_to_area"] if e["area_name"] == "Le Marais"]
    assert len(marais) >= 40, f"Le Marais should have ≥40 POIs after expansion; got {len(marais)}"


def test_ile_de_la_cite_includes_notre_dame():
    """Île de la Cité polygon must include canonical island POIs."""
    _, _, edges = _load()
    members = {e["poi_name"] for e in edges["poi_to_area"] if e["area_name"] == "Île de la Cité"}
    required = {
        "Notre-Dame Cathedral",
        "Sainte-Chapelle",
        "Conciergerie",
        "Pont Neuf",
        "Place Dauphine",
        "Hotel-Dieu",
        "Memorial des Martyrs de la Deportation",
    }
    missing = required - members
    assert not missing, f"Île de la Cité missing canonical POIs: {missing}"


def test_tier5_anchors_have_expected_areas():
    _, _, edges = _load()
    by_poi = {}
    for e in edges["poi_to_area"]:
        by_poi.setdefault(e["poi_name"], set()).add(e["area_name"])
    cases = {
        "Notre-Dame Cathedral": {"Paris", "4th Arrondissement", "Île de la Cité"},
        "Eiffel Tower": {"Paris", "7th Arrondissement"},
        "Louvre Museum": {"Paris", "1st Arrondissement"},
        "Place des Vosges": {"Paris", "4th Arrondissement", "Le Marais"},
        "Sacre-Coeur Basilica": {"Paris", "18th Arrondissement", "Montmartre"},
        "Conciergerie": {"Paris", "1st Arrondissement", "Île de la Cité"},
    }
    for poi, required in cases.items():
        actual = by_poi.get(poi, set())
        missing = required - actual
        assert not missing, f"{poi} missing expected Areas {missing}; has {actual}"


def test_area_to_area_count_matches_non_root_areas():
    areas, _, edges = _load()
    non_root = [a for a in areas if a.get("parent_area") is not None]
    a2a = edges["area_to_area"]
    assert len(a2a) == len(non_root), (
        f"Area→Area edges ({len(a2a)}) should match non-root Areas ({len(non_root)})"
    )
