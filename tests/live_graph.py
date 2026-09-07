"""Safe access to the populated local dev graph from pytest shards.

Pytest's destructive fixtures use ``NEO4J_*`` and are wipe-allowlisted to the
test-graph ports.  Live-corpus tests use this separate prefix and can only
open a localhost DEV graph — one of the lane dev ports below — so neither
environment can silently fall through to Aura.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from neo4j import GraphDatabase
from neo4j.exceptions import AuthError, ServiceUnavailable

#: The localhost dev graphs a live-corpus test may open: one per lane, the
#: same ports scripts/preflight.py's lane table publishes for its dev rows.
#: tests/test_preflight.py pins the two sets against each other, so a new
#: lane that forgets this file fails there by name.
DEV_GRAPH_PORTS: frozenset[int] = frozenset({7687, 7692, 7693, 7696})


def dev_graph_environment() -> dict[str, str]:
    return {
        "NEO4J_URI": os.getenv("ONDOWAY_DEV_NEO4J_URI", ""),
        "NEO4J_USER": os.getenv("ONDOWAY_DEV_NEO4J_USER", ""),
        "NEO4J_PASSWORD": os.getenv("ONDOWAY_DEV_NEO4J_PASSWORD", ""),
        "NEO4J_DATABASE": os.getenv("ONDOWAY_DEV_NEO4J_DATABASE", "neo4j"),
    }


def assert_walk_was_routed(route, *, golden: str) -> None:
    """Fail LOUDLY when a golden walk was priced by the haversine fallback.

    A golden's overlap baseline was measured on the ROUTED walk, and
    ``RoutingClient`` degrades stickily to straight-line estimates the moment
    Valhalla answers late or not at all — so under the bar (where preflight
    guarantees Valhalla is up) a fallback leg means Valhalla stopped answering
    mid-run, and the overlap number is about to measure a different walk. That
    must read as what it is, never as a mystery overlap dip.
    """
    fallen = [
        f"{t.from_poi_id or 'start'}→{t.to_poi_id or 'end'}"
        for t in route.transits
        if t.source != "valhalla"
    ]
    assert not fallen, (
        f"this {golden} golden walk was NOT routed: {len(fallen)} of "
        f"{len(route.transits)} legs fell back to haversine ({fallen[:4]}) — "
        f"Valhalla answered late or not at all, so the overlap baseline would "
        f"measure a different walk than the one it was calibrated on. Check "
        f"`make valhalla-status` and the machine's load; this is a routing "
        f"outage or contention, not a tour regression."
    )


def open_dev_driver():
    """Return a verified localhost dev-graph driver, or ``None`` when unavailable.

    The port must be one of the lane dev graphs — the profile the Make target
    executes under supplies it, so a lane's live-corpus tests read that lane's
    own corpus and nothing here can ever address Aura.
    """
    env = dev_graph_environment()
    uri = env["NEO4J_URI"]
    parsed = urlparse(uri)
    if (
        parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.port not in DEV_GRAPH_PORTS
        or not env["NEO4J_USER"]
        or not env["NEO4J_PASSWORD"]
        or env["NEO4J_DATABASE"] != "neo4j"
    ):
        return None
    try:
        driver = GraphDatabase.driver(
            uri,
            auth=(env["NEO4J_USER"], env["NEO4J_PASSWORD"]),
        )
        driver.verify_connectivity()
        return driver
    except (ServiceUnavailable, AuthError, Exception):
        return None
