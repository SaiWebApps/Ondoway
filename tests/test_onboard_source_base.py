"""Unit tests for the source connector Protocol + network door (base.py).

Pure — no Neo4j, no network: ``http_get_json`` is monkeypatched so
``run_connector`` never hits the wire. Run with:
    make test-file FILE=tests/test_onboard_source_base.py
"""

from __future__ import annotations

from src.onboard.jobs import JobStore
from src.onboard.models import CityContext, ConnectorResult, PoiCandidate
from src.onboard.sources import base
from src.onboard.sources.base import SourceConnector, run_connector


class _FakeConnector:
    """A structurally-conformant connector (does NOT inherit the Protocol)."""

    slug = "fake"

    def consult_url(self, ctx: CityContext) -> tuple[str, dict | None]:
        return ("https://en.wikipedia.org/w/api.php", {"a": "b"})

    def parse(self, payload: dict, ctx: CityContext) -> ConnectorResult:
        return ConnectorResult(
            candidates=[
                PoiCandidate(
                    name="Somewhere",
                    source="fake",
                    source_url="https://en.wikipedia.org/wiki/Somewhere",
                )
            ]
        )


def _ctx() -> CityContext:
    return CityContext(
        slug="london", display_name="London", bbox=(51.2, 51.7, -0.5, 0.3)
    )


def test_run_connector_emits_source_consult_with_concrete_url(monkeypatch) -> None:
    """run_connector emits a source_consult event carrying the CONCRETE URL
    (params encoded) and a candidate_batch event with a scalar candidate count —
    all WITHOUT live HTTP (http_get_json is monkeypatched to a fixed dict)."""
    monkeypatch.setattr(base, "http_get_json", lambda url, params=None: {"ok": True})
    store = JobStore()
    job = store.create("london", ["license_clean"])

    result = run_connector(_FakeConnector(), _ctx(), store, job.id)
    assert isinstance(result, ConnectorResult)
    assert len(result.candidates) == 1

    events = store.get(job.id).events
    consult = [e for e in events if e.kind == "source_consult"]
    assert len(consult) == 1
    assert consult[0].source == "fake"
    assert consult[0].url is not None
    assert "a=b" in consult[0].url  # params encoded into the concrete URL

    batch = [e for e in events if e.kind == "candidate_batch"]
    assert len(batch) == 1
    assert batch[0].data["candidates"] == 1


def test_source_connector_is_runtime_checkable() -> None:
    """SourceConnector is a @runtime_checkable Protocol, so a structurally
    conformant connector passes isinstance without inheriting from it."""
    fake = _FakeConnector()
    assert isinstance(fake, SourceConnector)
