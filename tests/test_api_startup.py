"""API startup resilience — the Render deploy fix.

Regression guard for the 2026-07 outage: the app EAGERLY verified the Neo4j
connection during FastAPI lifespan startup (`init_driver` -> `create_driver()`),
so when the Aura instance became unreachable (DNS `Cannot resolve address ...`)
the whole process crashed on startup, never opened its port, and every Render
deploy failed its health check -> daily "deploy failed" emails.

The app must START even when the database is unreachable: build the driver
lazily, and let DB errors surface per-request. `/api/v1/healthz` then returns
200 with ``neo4j_connected: false`` so the deploy stays green while the outage
is visible.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.app import _workbench_api_enabled, create_app

_DEAD_AURA_URI = "neo4j+s://deadbeef-nonexistent-ondoway.databases.neo4j.io:7687"


def test_app_starts_and_healthz_degraded_when_db_unreachable(monkeypatch):
    """Constructing TestClient runs the lifespan (init_driver). With an
    unreachable DB it must NOT raise, and /api/v1/healthz must return 200."""
    monkeypatch.setenv("NEO4J_URI", _DEAD_AURA_URI)
    monkeypatch.setenv("NEO4J_USER", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "wrong-or-gone")

    # If startup weren't lazy, TestClient(create_app()) would raise here.
    with TestClient(create_app()) as client:
        resp = client.get("/api/v1/healthz")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["neo4j_connected"] is False, "an unreachable DB must report not-connected"
    assert body["status"] == "degraded"


def test_init_driver_never_crashes_startup(monkeypatch):
    """init_driver swallows any driver-construction failure so the port still opens."""
    import src.api.dependencies as dep

    monkeypatch.setenv("NEO4J_URI", _DEAD_AURA_URI)
    monkeypatch.setenv("NEO4J_USER", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "x")
    try:
        dep.init_driver()  # must not raise / SystemExit
        assert dep._driver is not None  # lazy driver was built
    finally:
        dep.close_driver()


def test_workbench_crud_gated_off_public_deployment(monkeypatch):
    """Defect #2 (hostile red-team): the graph/nodes/edges/schema routers are the
    LOCAL editorial workbench's UNAUTHENTICATED create/update/delete surface. On
    the public deployment (WORKBENCH_API_ENABLED=false) they must NOT be mounted,
    so an anonymous caller can't mutate the graph; the mobile-facing routes stay
    up."""
    monkeypatch.setenv("WORKBENCH_API_ENABLED", "false")
    monkeypatch.setenv("NEO4J_URI", _DEAD_AURA_URI)
    monkeypatch.setenv("NEO4J_USER", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "x")
    with TestClient(create_app()) as client:
        # the unauthenticated write surface is GONE (route not mounted -> 404)
        assert client.post(
            "/api/v1/nodes/POI", json={"name": "x", "city_name": "y"}
        ).status_code == 404
        assert client.get("/api/v1/cities").status_code == 404
        # mobile-facing surface still up
        assert client.get("/api/v1/healthz").status_code == 200


def test_workbench_crud_gated_off_by_default(monkeypatch):
    """New FAIL-CLOSED contract (2026-07-14 security hardening): the workbench
    graph-CRUD routers are the LOCAL editorial workbench's UNAUTHENTICATED
    create/update/DETACH-DELETE surface. They now mount ONLY when
    WORKBENCH_API_ENABLED is EXPLICITLY truthy — an unset (or typo'd) value
    leaves them OFF, so a mis-configured deploy can never silently expose the
    repo-write/deploy surface on the internet. Checked via the route table so no
    live DB is needed."""
    # UNSET => workbench routes ABSENT (fail-closed default); mobile routes stay.
    monkeypatch.delenv("WORKBENCH_API_ENABLED", raising=False)
    paths = {getattr(r, "path", None) for r in create_app().routes}
    assert "/api/v1/cities" not in paths, "graph router must NOT mount when unset"
    assert "/api/v1/nodes/{label}" not in paths, "nodes write surface must NOT mount when unset"
    # The ingest front door reads a caller-named filesystem path and writes
    # data/{city}/ (Docs/ingestion/rebuild-spec.md §5): same gate, same rule.
    assert "/api/v1/ingest/jobs" not in paths, "ingest front door must NOT mount when unset"
    assert "/api/v1/healthz" in paths, "mobile-facing routes stay mounted"
    # Explicit truthy => opt-in, workbench routes PRESENT.
    monkeypatch.setenv("WORKBENCH_API_ENABLED", "true")
    enabled = {getattr(r, "path", None) for r in create_app().routes}
    assert "/api/v1/cities" in enabled, "graph router mounts when explicitly enabled"
    assert "/api/v1/nodes/{label}" in enabled, "nodes write surface mounts when explicitly enabled"
    assert "/api/v1/ingest/jobs" in enabled, "ingest front door mounts when explicitly enabled"


@pytest.mark.parametrize("value", ["true", "1", "yes", "on", "TRUE", "On", "yes "])
def test_workbench_gate_truthy_values_mount(monkeypatch, value):
    """FAIL-CLOSED parser: only an explicit truthy value (case-insensitive,
    whitespace-trimmed) opens the gate."""
    monkeypatch.setenv("WORKBENCH_API_ENABLED", value)
    assert _workbench_api_enabled() is True


@pytest.mark.parametrize("value", ["false", "0", "no", "off", "FALSE", "", "garbage", "disabled"])
def test_workbench_gate_non_truthy_values_stay_off(monkeypatch, value):
    """FAIL-CLOSED parser: explicit off values AND any unrecognized/empty value
    (a typo, a garbage export) keep the gate OFF — the whole point of the flip."""
    monkeypatch.setenv("WORKBENCH_API_ENABLED", value)
    assert _workbench_api_enabled() is False


def test_workbench_gate_unset_is_off(monkeypatch):
    """FAIL-CLOSED parser: an UNSET var is OFF (opt-in, not opt-out)."""
    monkeypatch.delenv("WORKBENCH_API_ENABLED", raising=False)
    assert _workbench_api_enabled() is False
