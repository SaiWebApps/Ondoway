"""Safe access to the populated local dev graph from pytest shards.

Pytest's destructive fixtures use ``NEO4J_*`` and are hard-pinned to the
7688-family test graphs.  Live-corpus tests use this separate prefix and can
only open a LOCAL DEV graph -- the canonical 7687 or a lane's own (7692, 7693:
the ``dev*`` rows of scripts/preflight.py's DATABASES) -- so neither
environment can silently fall through to Aura, a wiped pytest graph or a
workbench graph.  Until slice 9 only 7687 was accepted, so on a lane every
golden, persona, coherence and authoring-gate shard SKIPPED and green there
proved nothing about the tour bar.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from neo4j import GraphDatabase
from neo4j.exceptions import AuthError, ServiceUnavailable

from scripts.preflight import DATABASES

#: Every local dev graph a live-corpus test may open: one port per lane.
DEV_GRAPH_PORTS: frozenset[int] = frozenset(
    spec.port for spec in DATABASES if spec.key.startswith("dev")
)


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
    """Return a verified driver on a local dev graph, or ``None`` when unavailable."""
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
