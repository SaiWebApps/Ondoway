"""Flow-primitive tests: ``consult_source`` resilience.

A LIVE onboarding run consults several sources; ONE source failing (a 403, a
timeout, a malformed payload) must NOT abort the whole run — it should degrade
to that source contributing nothing, leaving a VISIBLE ``error`` event as the
record, so ``assemble`` still proceeds with the other sources' candidates.

Pure — no Neo4j, no network: the live path is exercised by monkeypatching
``flow.run_connector`` (so no httpx call happens), the fixture path by a fake
connector whose ``parse`` raises. Run with:
    make test-file FILE=tests/test_onboard_flow.py
"""

from __future__ import annotations

import json

import httpx

from src.onboard import fetch, flow
from src.onboard.flow import consult_source
from src.onboard.jobs import JobStore
from src.onboard.models import CityContext, ConnectorResult

# London frame: (min_lat, max_lat, min_lon, max_lon) — its committed connector
# fixtures live under tests/fixtures/onboard/london/.
LONDON = CityContext(slug="london", display_name="London", bbox=(51.28, 51.70, -0.51, 0.33))


def _store_job() -> tuple[JobStore, str]:
    store = JobStore()
    job = store.create("london", ["license_clean"])
    return store, job.id


def _errors(store: JobStore, job_id: str) -> list:
    return [e for e in store.get(job_id).events if e.kind == "error"]


class _BoomConnector:
    """A structurally-conformant connector whose ``parse`` ALWAYS raises — stands
    in for a source returning a malformed payload."""

    slug = "boom"

    def consult_url(self, ctx: CityContext) -> tuple[str, dict | None]:
        return ("https://en.wikipedia.org/w/api.php", {"q": "x"})

    def parse(self, payload: dict, ctx: CityContext) -> ConnectorResult:
        raise ValueError("malformed geosearch payload")


def test_consult_source_swallows_live_connector_failure(monkeypatch) -> None:
    """LIVE path: a connector run that raises an HTTP 403 (the real Wikimedia
    failure) does NOT propagate — ``consult_source`` returns an EMPTY result and
    appends a single ``error`` event naming the source + the error.

    UNDO: remove the try/except in consult_source -> the raise propagates ->
    ``consult_source`` raises instead of returning -> RED."""
    monkeypatch.setattr(fetch, "HTTP_MODE", "live")

    def _boom(connector, ctx, store, job_id):
        req = httpx.Request("GET", "https://en.wikipedia.org/w/api.php")
        resp = httpx.Response(403, request=req)
        raise httpx.HTTPStatusError("403 Forbidden", request=req, response=resp)

    monkeypatch.setattr(flow, "run_connector", _boom)
    store, job_id = _store_job()

    result = consult_source("wikipedia", LONDON, store, job_id)  # must NOT raise

    assert result == ConnectorResult()  # empty -> assemble proceeds with other sources
    errs = _errors(store, job_id)
    assert len(errs) == 1, [e.model_dump() for e in store.get(job_id).events]
    assert errs[0].source == "wikipedia"
    assert "wikipedia failed" in errs[0].message
    assert "403" in errs[0].message


def test_consult_source_swallows_fixture_parse_error(monkeypatch) -> None:
    """FIXTURE path: a connector whose ``parse`` raises does NOT propagate — the
    same graceful degradation (empty result + one ``error`` event).

    UNDO: remove the try/except in consult_source -> the parse error propagates
    -> RED."""
    monkeypatch.setattr(fetch, "HTTP_MODE", "fixture")
    monkeypatch.setitem(flow.CONNECTORS, "boom", _BoomConnector())
    # Point the fake at ANY existing committed london fixture (its content is
    # irrelevant — the fake's parse raises before reading it).
    monkeypatch.setitem(flow.CONNECTOR_FIXTURE, "boom", "wikivoyage_page.json")
    store, job_id = _store_job()

    result = consult_source("boom", LONDON, store, job_id)  # must NOT raise

    assert result == ConnectorResult()
    errs = _errors(store, job_id)
    assert len(errs) == 1
    assert errs[0].source == "boom"
    assert "boom failed" in errs[0].message


def test_consult_source_normal_connector_returns_result_unchanged(monkeypatch) -> None:
    """A HEALTHY source is UNCHANGED by the resilience wrap: it returns exactly
    the result a direct ``parse`` of the same committed fixture yields, and emits
    NO ``error`` event. (This guards that the resilience wrap leaves the happy
    path alone — it stays GREEN with or without the try/except.)"""
    monkeypatch.setattr(fetch, "HTTP_MODE", "fixture")
    store, job_id = _store_job()

    result = consult_source("wikipedia", LONDON, store, job_id)

    fixture_path = flow.FIXTURES_ROOT / "london" / flow.CONNECTOR_FIXTURE["wikipedia"]
    direct = flow.CONNECTORS["wikipedia"].parse(json.loads(fixture_path.read_text()), LONDON)
    assert result.candidates, "expected a non-empty wikipedia result (guards vacuous equality)"
    assert result.candidates == direct.candidates
    assert _errors(store, job_id) == []
