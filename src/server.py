"""Lightweight HTTP API for the Ondoway Neo4j dashboard.

Serves graph data as JSON and hosts the workbench pages.
No framework dependencies — uses stdlib http.server + json.
"""

from __future__ import annotations

import json
import os
from http.server import HTTPServer, SimpleHTTPRequestHandler
from typing import Any

from src.connection import create_driver, get_database
from src.verify.counts import count_nodes_by_label, count_relationships_by_type, total_counts
from src.verify.traversals import run_all_traversals

WORKBENCH_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web", "workbench")
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


class DashboardHandler(SimpleHTTPRequestHandler):
    """Handle API routes and serve static files from web/workbench/."""

    driver = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WORKBENCH_DIR, **kwargs)

    def do_GET(self):
        if self.path == "/api/status":
            self._json_response(_build_api_response(self.driver))
        else:
            super().do_GET()

    def _json_response(self, data: dict) -> None:
        body = json.dumps(data, default=str).encode()
        self.send_response(200)
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
