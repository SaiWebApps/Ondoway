"""Hermetic unit tests for _resolve_lenses (lens precedence) — no Neo4j.

The live-graph integration tests in test_trip_api.py prove the API and engine
agree on the resulting route, but on the current corpus the Île input selects
the SAME route for every lens set — so they cannot detect a broken precedence
path. These tests pin the precedence logic itself: request lenses win; else
the profile's PREFERS_LENS names, sorted for determinism; else None.
"""

from __future__ import annotations

from src.api.models.trips import TripGenerateRequest
from src.api.routes.app.trips import _resolve_lenses


class _FakeSession:
    """Stands in for a neo4j Session: returns canned PREFERS_LENS rows."""

    def __init__(self, profile_lens_names: list[str]):
        self._names = profile_lens_names
        self.queries_run = 0

    def run(self, query, **params):
        self.queries_run += 1
        return [{"name": n} for n in self._names]


def _request(**overrides) -> TripGenerateRequest:
    body = {
        "profile_id": "prof-1",
        "center_lat": 48.85,
        "center_lng": 2.35,
        "start_date": "2026-06-12",
        "end_date": "2026-06-13",
    }
    body.update(overrides)
    return TripGenerateRequest(**body)


def test_request_lenses_win_over_profile():
    session = _FakeSession(["street_art", "parks_gardens"])
    body = _request(lenses=["dark_history"])
    assert _resolve_lenses(session, body) == ["dark_history"]
    assert session.queries_run == 0, "profile must not be consulted when request has lenses"


def test_profile_lenses_used_when_request_has_none_and_are_sorted():
    session = _FakeSession(["literary_heritage", "hidden_history"])
    body = _request()
    assert _resolve_lenses(session, body) == ["hidden_history", "literary_heritage"]


def test_no_lenses_anywhere_resolves_to_none():
    session = _FakeSession([])
    body = _request()
    assert _resolve_lenses(session, body) is None
