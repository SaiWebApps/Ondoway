"""Tests for tests/live_graph.py — the dev-graph opener every golden,
persona, coherence and authoring-gate shard shares.

`open_dev_driver` exists so a live-corpus test can only ever open a LOCAL
dev graph: never Aura, never the destructive pytest graphs (7688-family,
full-wiped per module), never a workbench graph (7689-family, wiped per
run). Until slice 9 it accepted exactly port 7687, so on a lane (the
`test2` profile sets `ONDOWAY_DEV_NEO4J_URI` to 7692) every one of those
shards silently SKIPPED, and green on the lane proved nothing about the
tour bar. Hermetic: the driver factory is stubbed; nothing connects.
"""

from __future__ import annotations

import pytest

from tests import live_graph


class _Driver:
    def __init__(self, uri: str, auth: tuple[str, str]) -> None:
        self.uri = uri
        self.auth = auth

    def verify_connectivity(self) -> None:
        return None


def _point_at(monkeypatch: pytest.MonkeyPatch, uri: str) -> list[str]:
    opened: list[str] = []

    def factory(uri: str, auth: tuple[str, str]) -> _Driver:
        opened.append(uri)
        return _Driver(uri, auth)

    monkeypatch.setattr(live_graph.GraphDatabase, "driver", staticmethod(factory))
    monkeypatch.setenv("ONDOWAY_DEV_NEO4J_URI", uri)
    monkeypatch.setenv("ONDOWAY_DEV_NEO4J_USER", "neo4j")
    monkeypatch.setenv("ONDOWAY_DEV_NEO4J_PASSWORD", "pw")
    monkeypatch.setenv("ONDOWAY_DEV_NEO4J_DATABASE", "neo4j")
    return opened


@pytest.mark.parametrize("port", [7687, 7692, 7693])
def test_every_lanes_dev_graph_opens(monkeypatch, port):
    """The canonical dev graph and each lane's own dev graph (the `dev2`
    and `dev3` rows of scripts/preflight.py's DATABASES) open, so the
    golden bar on a lane reads that lane's corpus instead of skipping."""
    opened = _point_at(monkeypatch, f"bolt://localhost:{port}")
    driver = live_graph.open_dev_driver()
    assert driver is not None
    assert opened == [f"bolt://localhost:{port}"]


@pytest.mark.parametrize(
    "uri",
    [
        "bolt://localhost:7688",  # the canonical pytest graph, full-wiped per module
        "bolt://localhost:7690",  # lane 2's pytest graph
        "bolt://localhost:7689",  # the workbench graph, wiped per run
        "bolt://localhost:7694",  # lane 2's workbench graph
        "neo4j+s://abc123.databases.neo4j.io:7687",  # Aura, never
        "bolt://localhost:7687",  # right port, but no credentials below
    ],
)
def test_anything_but_a_local_dev_graph_is_refused_without_connecting(monkeypatch, uri):
    opened = _point_at(monkeypatch, uri)
    if uri == "bolt://localhost:7687":
        monkeypatch.setenv("ONDOWAY_DEV_NEO4J_PASSWORD", "")
    assert live_graph.open_dev_driver() is None
    assert opened == []
