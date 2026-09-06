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
    """One request carries both halves the screen draws: quality and coverage."""
    status, body = _get(f"{dashboard_url}/api/corpus?city=paris")
    assert status == 200

    payload = json.loads(body)
    assert payload["city"] == "paris"
    assert payload["quality"]["total_beats"] == 1562
    assert payload["quality"]["verbatim"]["flagged"] == 246
    assert payload["coverage"]["anchor_readiness"]["total_pois"] == 370
    assert payload["coverage"]["areas_mapped"] is True
    # The page reads its city selector from here rather than hardcoding slugs.
    assert "london" in payload["cities"]


def test_corpus_route_reports_an_unmapped_city_without_failing(dashboard_url: str) -> None:
    """London has no areas generated; the route still answers, and says so."""
    status, body = _get(f"{dashboard_url}/api/corpus?city=london")
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


# ── The re-author review surface ────────────────────────────────────────────


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


def test_review_route_serves_candidates_most_doubted_first(dashboard_url: str) -> None:
    """The queue the reviewer works through, in the order they should work it."""
    status, body = _get(f"{dashboard_url}/api/reauthored?city=paris")
    assert status == 200

    payload = json.loads(body)
    assert payload["summary"]["total"] == 524 - 278  # paris only
    flags = [row["flags"] for row in payload["candidates"]]
    assert flags == sorted(flags, reverse=True)
    first = payload["candidates"][0]
    # A reviewer cannot judge without all three texts on the row.
    assert first["source_passage"] and first["body_before"] and first["body_after"]


def test_review_route_names_the_reviewer_it_would_record(dashboard_url: str) -> None:
    """The screen shows whose name goes on a decision before any is made."""
    status, body = _get(f"{dashboard_url}/api/reauthored?city=paris")
    assert status == 200
    assert json.loads(body)["reviewer"].strip()


def test_a_decision_is_refused_for_an_unknown_beat(dashboard_url: str) -> None:
    """A bad id is a 404 naming it, never a traceback or a silent no-op."""
    status, body = _post(
        f"{dashboard_url}/api/reauthored/decision",
        {"city": "paris", "beat_id": "not-a-real-beat", "decision": "approve"},
    )
    assert status == 404
    assert "not-a-real-beat" in json.loads(body)["error"]


def test_a_decision_is_refused_for_an_unknown_verdict(dashboard_url: str) -> None:
    """Only approve and reject exist; anything else is a 400, not a fourth state."""
    status, _ = _post(
        f"{dashboard_url}/api/reauthored/decision",
        {"city": "paris", "beat_id": "x", "decision": "looks-fine"},
    )
    assert status == 400


def test_review_route_shows_only_what_needs_a_person(dashboard_url: str) -> None:
    """The queue is the escalations, not the corpus. 384 of 524 were machine-settled."""
    status, body = _get(f"{dashboard_url}/api/reauthored?city=paris")
    assert status == 200
    payload = json.loads(body)

    assert payload["summary"]["auto_approved"] > 0
    assert payload["summary"]["escalated"] == len(payload["candidates"])
    # Every row a person sees carries the machine's reason for not settling it.
    assert all(row["verified"]["reason"] for row in payload["candidates"])
    assert len(payload["candidates"]) < payload["summary"]["total"]


def test_review_route_can_still_show_everything(dashboard_url: str) -> None:
    """Spot-checking the auto-approved is a legitimate thing to want."""
    status, body = _get(f"{dashboard_url}/api/reauthored?city=paris&show=all")
    payload = json.loads(body)
    assert status == 200
    assert len(payload["candidates"]) == payload["summary"]["total"]
