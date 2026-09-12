"""Tests for scripts/db_parity.py — slice 8 (Docs/ingestion/rebuild-spec.md
§6): a withdrawn beat is a NAMED category in the parity report, never
drift. The `dev-data` preflight runs this script on every `make test-file`
in every lane, so a misreport here blocks every session.

Hermetic: a per-test data root (`ONBOARD_DATA_ROOT`) holding one city
with one in-bbox POI, and the lane's pytest graph; no live client, no
network, no spend (test_no_live_client_in_this_file, the same walker as
the ingest test files).

Run one file: make test-file FILE=tests/test_db_parity.py
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

from scripts import db_parity
from src.api.models.nodes import canonical_name_key
from src.connection import get_database
from tests.conftest import needs_neo4j

CITY = "new_york"
PLACE = "Solomon R. Guggenheim Museum"
ACTIVE_ID = f"{CITY}/guggenheim/active-story"
WITHDRAWN_ID = f"{CITY}/guggenheim/withdrawn-story"


def _legacy_beat(beat_id: str) -> dict:
    return {
        "beat_id": beat_id,
        "city_name": CITY,
        "poi_name": PLACE,
        "lens": "hidden_history",
        "script_body": "A story about the place.",
        "fact_check": {"status": "verified"},
    }


@pytest.fixture
def data_root(tmp_path, monkeypatch) -> Path:
    """One city, one in-bbox POI, a beats file naming ONLY the active beat."""
    city_dir = tmp_path / "data" / CITY
    city_dir.mkdir(parents=True)
    (city_dir / "poi-raw.json").write_text(
        json.dumps([{"name": PLACE, "latitude": 40.7830, "longitude": -73.9590}])
    )
    (city_dir / "beats.json").write_text(json.dumps([_legacy_beat(ACTIVE_ID)]))
    monkeypatch.setenv("ONBOARD_DATA_ROOT", str(tmp_path / "data"))
    return tmp_path / "data"


def _seed_graph(driver) -> None:
    """The POI with the active beat the file names and a withdrawn beat the
    file no longer names — HAS_BEAT kept on both, as the publisher leaves it."""
    with driver.session(database=get_database()) as s:
        s.run("MATCH (n) DETACH DELETE n")
        s.run(
            "CREATE (p:POI {name: $name, name_key: $key, city_name: $city, id: 'gugg'}) "
            "CREATE (p)-[:HAS_BEAT]->(:NarrativeBeat {beat_id: $active, active_status: 'active'}) "
            "CREATE (p)-[:HAS_BEAT]->(:NarrativeBeat {beat_id: $gone, active_status: 'withdrawn'})",
            name=PLACE,
            key=canonical_name_key(PLACE),
            city=CITY,
            active=ACTIVE_ID,
            gone=WITHDRAWN_ID,
        )


@needs_neo4j
def test_withdrawn_beats_are_reported_not_drift(clean_driver, data_root, monkeypatch, capsys):
    """A beat the graph holds as withdrawn is not an "extra" beat: the
    active set matches the file, the report names the withdrawn count on
    both sides, and the exit code is 0. [undo: count withdrawn beats in the
    graph's beat_id set → "+1 extra" drift → exit 1 → RED]"""
    _seed_graph(clean_driver)

    with clean_driver.session(database=get_database()) as s:
        actual = db_parity._actual(s, CITY)
    assert actual["beat_ids"] == {ACTIVE_ID}
    assert actual["withdrawn"] == 1
    expected = db_parity._expected(CITY)
    assert expected["beat_ids"] == {ACTIVE_ID}
    assert expected["withdrawn"] == 0

    monkeypatch.setattr(sys, "argv", ["db_parity", CITY])
    rc = db_parity.main()
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "PARITY OK" in out
    assert "withdrawn" in out and "db=1" in out.split("withdrawn", 1)[1].split("\n", 1)[0], out


def test_no_live_client_in_this_file():
    """This $0-spend test file never names a live LLM client or reads its
    API key. Its own body is exempt from the walk below — this docstring
    and the assert messages name those things on purpose to describe the
    rule, which is not the violation the rule guards against.
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    self_name = "test_no_live_client_in_this_file"
    forbidden_names = {"AnthropicClient", "anthropic"}
    forbidden_env_var = "ANTHROPIC_API_KEY"

    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == self_name:
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name):
                assert sub.id not in forbidden_names, f"{sub.id!r} must not appear in this file"
            elif isinstance(sub, ast.Attribute):
                assert sub.attr not in forbidden_names, (
                    f"{sub.attr!r} must not appear in this file"
                )
            elif isinstance(sub, ast.ImportFrom) and sub.module:
                assert sub.module.split(".")[0] not in forbidden_names, (
                    f"from-import of {sub.module!r} must not appear in this file"
                )
            elif isinstance(sub, ast.alias):
                top_level_name = sub.name.split(".")[0]
                assert top_level_name not in forbidden_names, (
                    f"import of {sub.name!r} must not appear in this file"
                )
                assert sub.asname not in forbidden_names, (
                    f"import alias {sub.asname!r} must not appear in this file"
                )
            elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                assert forbidden_env_var not in sub.value, (
                    f"{forbidden_env_var!r} must not appear in this file"
                )
