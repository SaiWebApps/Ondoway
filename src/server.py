"""Lightweight HTTP API for the Ondoway dashboards.

Serves two reports and hosts the static frontend. `/api/status` answers about the
graph and needs a driver; `/api/corpus` answers about a city's beat files and
deliberately does not — a route that needed a database to describe a JSON file
would be reading the wrong thing.

No framework dependencies — uses stdlib http.server + json.
"""

from __future__ import annotations

import json
import os
from http.server import HTTPServer, SimpleHTTPRequestHandler
from typing import Any
from urllib.parse import parse_qs, urlparse

from scripts.corpus_report import (
    available_cities,
    coverage_report,
    density_summary,
    load_city_areas,
    load_city_beats,
    load_city_pois,
    quality_report,
)
from scripts.reauthor_cleanroom import city_name, unsupported_words
from scripts.reauthor_review import (
    CANDIDATE_FILES,
    DECISIONS,
    decision_summary,
    load_candidates,
    record_decision,
    review_order,
    reviewer_identity,
    save_candidates,
)
from src.connection import create_driver, get_database
from src.verify.counts import count_nodes_by_label, count_relationships_by_type, total_counts
from src.verify.traversals import run_all_traversals

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
PORT = int(os.getenv("DASHBOARD_PORT", "8080"))


def _graph_data(driver) -> dict[str, Any]:
    """Fetch all nodes and relationships for visualization."""
    with driver.session(database=get_database()) as session:
        nodes_result = session.run(
            "MATCH (n) RETURN id(n) AS id, labels(n) AS labels, properties(n) AS props"
        )
        nodes = []
        for record in nodes_result:
            props = dict(record["props"])
            # Convert Neo4j spatial points to serializable dicts
            for key, val in props.items():
                if hasattr(val, "latitude"):
                    props[key] = {"lat": val.latitude, "lng": val.longitude}
            nodes.append(
                {
                    "id": record["id"],
                    "labels": record["labels"],
                    "props": props,
                }
            )

        rels_result = session.run(
            "MATCH (a)-[r]->(b) "
            "RETURN id(r) AS id, type(r) AS type, id(a) AS source, id(b) AS target"
        )
        rels = [
            {
                "id": r["id"],
                "type": r["type"],
                "source": r["source"],
                "target": r["target"],
            }
            for r in rels_result
        ]

    return {"nodes": nodes, "relationships": rels}


def _build_api_response(driver) -> dict[str, Any]:
    """Assemble full dashboard payload."""
    traversals = run_all_traversals(driver)
    return {
        "node_counts": count_nodes_by_label(driver),
        "rel_counts": count_relationships_by_type(driver),
        "totals": total_counts(driver),
        "traversals": [
            {
                "name": t.name,
                "passed": t.passed,
                "row_count": t.row_count,
                "min_expected": t.min_expected,
                "sample_rows": t.sample_rows[:5],
            }
            for t in traversals
        ],
        "graph": _graph_data(driver),
    }


def _corpus_payload(city_slug: str) -> dict[str, Any]:
    """Both halves of one city's corpus report, read from files.

    An unmapped city still answers: `coverage_report` marks `areas_mapped` false
    and the page says which command generates them, rather than drawing an empty
    grid that reads like full coverage.
    """
    beats = load_city_beats(city_slug)
    pois = load_city_pois(city_slug)
    try:
        poi_to_area, area_names = load_city_areas(city_slug)
    except FileNotFoundError:
        poi_to_area, area_names = [], []
    return {
        "city": city_slug,
        "cities": available_cities(),
        "quality": quality_report(beats),
        "coverage": coverage_report(beats, pois, poi_to_area, area_names),
        "density": density_summary(city_slug),
    }


def _needs_a_person(record: dict, source: str) -> bool:
    """Whether one candidate still has a question only a reviewer can answer."""
    if source == "cleanroom":
        return bool(record.get("flags"))
    verified = record.get("verified") or {}
    return verified.get("status") == "escalate" or not verified


def _reauthored_payload(
    city_slug: str, *, show_all: bool = False, source: str = "rewrite"
) -> dict[str, Any]:
    """The review queue for one city, from either authoring path.

    By default this is ONLY what the machine could not settle. The design's D1 is
    triage, not blanket verification, so a rewrite two independent models agree
    adds nothing and drops nothing does not consume a person's attention. Pass
    show=all to spot-check the auto-approved, which is worth doing periodically.

    `source=cleanroom` reads the clean-room bodies instead. They are ranked by the
    same `review_order` and decided through the same POST, because a candidate is a
    candidate — and a clean-room body that carries a flag has nowhere else to go.
    """
    records = load_candidates(city_slug, source=source)
    summary = decision_summary(records)
    summary["auto_approved"] = sum(
        1 for r in records if str(r.get("decided_by", "")).startswith("auto:")
    )
    summary["escalated"] = sum(
        1 for r in records if (r.get("verified") or {}).get("status") == "escalate"
    )
    summary["unverified"] = sum(1 for r in records if not r.get("verified"))
    # What the reviewer is actually being handed, whichever artifact this is. The
    # tally cannot read `escalated` for both: a clean-room body has no panel verdict
    # to escalate, and reporting zero there would say nobody is needed.
    summary["needs_a_person"] = sum(1 for r in records if _needs_a_person(r, source))

    shown = review_order(records)
    if source == "cleanroom":
        for row in shown:
            row["unsupported_words"] = unsupported_words(
                row.get("body_after") or "",
                row.get("claims_given") or [],
                poi=row.get("poi_name") or "",
                city=city_name(city_slug),
            )
    if not show_all:
        # The two artifacts are asked different questions. A rewrite carries a panel's
        # verdict, so what needs a person is an escalation or a beat nothing judged. A
        # clean-room body carries no verdict at all — the gates are what looked at it —
        # so what needs a person is what a gate flagged.
        shown = [r for r in shown if _needs_a_person(r, source)]
    return {
        "city": city_slug,
        "reviewer": reviewer_identity(),
        "summary": summary,
        "candidates": shown,
        "showing": "all" if show_all else "escalated",
        "source": source,
    }


class DashboardHandler(SimpleHTTPRequestHandler):
    """Handle API routes and serve static files from frontend/."""

    driver = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=FRONTEND_DIR, **kwargs)

    def do_GET(self):
        route = urlparse(self.path)
        if route.path == "/api/status":
            self._json_response(_build_api_response(self.driver))
        elif route.path == "/api/corpus":
            self._corpus_route(parse_qs(route.query).get("city", ["paris"])[0])
        elif route.path == "/api/reauthored":
            query = parse_qs(route.query)
            self._json_response(
                _reauthored_payload(
                    query.get("city", ["paris"])[0],
                    show_all=query.get("show", [""])[0] == "all",
                    source=query.get("source", ["rewrite"])[0],
                )
            )
        else:
            super().do_GET()

    def do_POST(self):
        """The one write this server accepts: a reviewer's verdict on one rewrite."""
        if urlparse(self.path).path != "/api/reauthored/decision":
            self._json_response({"error": "no such endpoint"}, status=404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, TypeError):
            self._json_response({"error": "body must be JSON"}, status=400)
            return
        self._decision_route(payload)

    def _decision_route(self, payload: dict) -> None:
        city = payload.get("city") or "paris"
        beat_id = payload.get("beat_id") or ""
        decision = payload.get("decision") or ""
        if decision not in DECISIONS:
            self._json_response({"error": f"decision must be one of {list(DECISIONS)}"}, status=400)
            return

        source = payload.get("source") or "rewrite"
        if source not in CANDIDATE_FILES:
            self._json_response(
                {"error": f"source must be one of {list(CANDIDATE_FILES)}"}, status=400
            )
            return
        records = load_candidates(city, source=source)
        try:
            row = record_decision(records, beat_id, decision, decided_by=reviewer_identity())
        except KeyError:
            self._json_response(
                {"error": f"no candidate with beat_id {beat_id!r} in {city}"}, status=404
            )
            return
        except ValueError as exc:
            self._json_response({"error": str(exc)}, status=400)
            return

        save_candidates(city, records, source=source)
        self._json_response({"decided": row, "summary": decision_summary(records)})

    def _corpus_route(self, city_slug: str) -> None:
        """Answer for one city, or say which city could not be found."""
        try:
            self._json_response(_corpus_payload(city_slug))
        except FileNotFoundError as exc:
            self._json_response(
                {"error": f"no corpus for city {city_slug!r}", "detail": str(exc)},
                status=404,
            )

    def _json_response(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        """Suppress default noisy logging."""


def serve() -> None:
    """Start the dashboard server."""
    driver = create_driver()
    DashboardHandler.driver = driver

    server = HTTPServer(("127.0.0.1", PORT), DashboardHandler)
    print(f"\n  🌐 Dashboard running at http://localhost:{PORT}")
    print("  Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Shutting down...")
    finally:
        driver.close()
        server.server_close()


if __name__ == "__main__":
    serve()
