"""The dashboard serves the corpus report beside the graph one.

These run a real `DashboardHandler` on an ephemeral port and speak HTTP to it, so
they pin the route as the browser meets it — including that adding an API branch
did not break the static fall-through the page itself relies on.

No Neo4j driver is set on the handler: the corpus report reads files, and a route
that needed a database to answer a question about a JSON file would be the wrong
route.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import HTTPServer

import pytest

from src.server import DashboardHandler


@pytest.fixture
def dashboard_url() -> Iterator[str]:
    """A live dashboard on an ephemeral port, with no database behind it."""
    DashboardHandler.driver = None
    server = HTTPServer(("127.0.0.1", 0), DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _get(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_corpus_route_returns_the_report_for_a_known_city(dashboard_url: str) -> None:
    """One request carries both halves the screen draws: quality and coverage.

    Against the real, unstubbed `available_cities()`: it lists the city directories
    that carry a beats.json, so with the London corpus retired to _to_be_deleted/
    the answer is exactly the two live cities. Asserted as an equality, so a corpus
    reappearing under data/ (or one going missing) is caught here.
    """
    status, body = _get(f"{dashboard_url}/api/corpus?city=paris")
    assert status == 200

    payload = json.loads(body)
    assert payload["city"] == "paris"
    assert payload["quality"]["total_beats"] == 1562
    assert payload["quality"]["verbatim"]["flagged"] == 246
    assert payload["coverage"]["anchor_readiness"]["total_pois"] == 370
    assert payload["coverage"]["areas_mapped"] is True
    # The page reads its city selector from here rather than hardcoding slugs.
    assert payload["cities"] == ["new_york", "paris"]


def test_corpus_route_reports_an_unmapped_city_without_failing(
    dashboard_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A city with no areas generated still answers, and says so.

    The missing-areas condition is injected rather than borrowed from London's
    directory, so this keeps testing the route's `FileNotFoundError` branch after
    that directory is quarantined — the branch is the behaviour, not the city.
    """

    def _no_areas(_city: str) -> tuple[list[dict], list[str]]:
        raise FileNotFoundError("areas.json")

    monkeypatch.setattr("src.server.load_city_areas", _no_areas)
    status, body = _get(f"{dashboard_url}/api/corpus?city=paris")
    assert status == 200
    assert json.loads(body)["coverage"]["areas_mapped"] is False


def test_corpus_route_carries_the_pois_worth_fixing(dashboard_url: str) -> None:
    """The screen's most actionable list must reach it, grouped by how thin each POI is.

    A count tells the owner his corpus is thin; a name tells him what to go write.
    Paris has 30 POIs with no beats at all, and they are the work queue.
    """
    status, body = _get(f"{dashboard_url}/api/corpus?city=paris")
    assert status == 200
    thin = json.loads(body)["coverage"]["anchor_readiness"]["thin"]

    assert len(thin) == 207
    zero = [row["poi_name"] for row in thin if row["beats"] == 0]
    assert len(zero) == 30
    # Sorted thinnest-first, so the queue is already in priority order.
    assert thin[0]["beats"] == 0


def test_corpus_route_rejects_an_unknown_city(dashboard_url: str) -> None:
    """A city with no files is a 404 naming the city — never a stack trace."""
    status, body = _get(f"{dashboard_url}/api/corpus?city=atlantis")
    assert status == 404
    assert "atlantis" in json.loads(body)["error"]


def test_unknown_path_still_falls_through_to_static(dashboard_url: str) -> None:
    """Adding an API branch must not stop the handler serving frontend/ files."""
    status, body = _get(f"{dashboard_url}/auth.html")
    assert status == 200
    assert b"<" in body


def test_corpus_page_is_served(dashboard_url: str) -> None:
    """The page the owner actually opens is reachable from the same server."""
    status, body = _get(f"{dashboard_url}/corpus.html")
    assert status == 200
    assert b"corpus" in body.lower()


# ── The re-author review surface is gone ────────────────────────────────────


def _post(url: str, payload: dict) -> tuple[int, bytes]:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_the_review_surface_is_gone(dashboard_url: str) -> None:
    """No re-author queue, no decision endpoint — just the corpus route left.

    A live, unstubbed handler: nothing here is monkeypatched. The route is gone
    entirely — a bare 404 from the static fall-through, not a 200 with an empty
    queue and not a purpose-built "gone" error body.
    """
    status, _ = _get(f"{dashboard_url}/api/reauthored?city=paris")
    assert status == 404

    # The one write this server used to accept is unimplemented. There is no
    # do_POST method at all, so the stdlib's own default answers 501 — not a
    # hand-written 501 that keeps the endpoint alive as a dead method.
    status, _ = _post(
        f"{dashboard_url}/api/reauthored/decision",
        {"city": "paris", "beat_id": "x", "decision": "approve"},
    )
    assert status == 501
