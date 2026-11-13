"""Regression test for the conftest test-isolation guard.

The incident: a golden test called load_dotenv(override=True) which
mutated NEO4J_URI from the test port (7688) to the dev port (7687). The
conftest's session-scoped driver fixture later instantiated a driver
against the mutated env and ran _wipe() — destroying the dev corpus.

The guard in tests/conftest.py:_assert_test_port() now refuses to wipe
unless NEO4J_URI's port is in _TEST_PORT_ALLOWLIST. These tests verify
that:

1. _wipe() raises RuntimeError when NEO4J_URI points at a non-test port.
2. _wipe() proceeds without raising when NEO4J_URI is on the allowlist.
3. The error message names the offending URI/port and the allowlist file.

Mocks the driver so no actual Cypher runs against any real instance.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.conftest import _TEST_PORT_ALLOWLIST, _assert_test_port, _wipe


def test_test_port_allowlist_includes_7688():
    """The test instance port must be 7688 unless explicitly broadened."""
    assert 7688 in _TEST_PORT_ALLOWLIST
    # Dev/production ports must never appear.
    assert 7687 not in _TEST_PORT_ALLOWLIST


def test_assert_test_port_blocks_dev_port(monkeypatch):
    """Pointing at the dev port must raise — this is the incident's vector."""
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
    with pytest.raises(RuntimeError) as exc_info:
        _assert_test_port()
    msg = str(exc_info.value)
    assert "Refusing to _wipe() against non-test Neo4j" in msg
    assert "bolt://localhost:7687" in msg
    assert "port=7687" in msg
    assert "_TEST_PORT_ALLOWLIST" in msg


def test_assert_test_port_blocks_remote_dev_uri(monkeypatch):
    """A remote dev URI on the standard 7687 port must also block."""
    monkeypatch.setenv("NEO4J_URI", "bolt://prod.example.com:7687")
    with pytest.raises(RuntimeError):
        _assert_test_port()


def test_assert_test_port_blocks_missing_port(monkeypatch):
    """A URI without an explicit port (parsed.port=None) must also block —
    we never want to assume a default.
    """
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost")
    with pytest.raises(RuntimeError) as exc_info:
        _assert_test_port()
    assert "port=None" in str(exc_info.value)


def test_assert_test_port_blocks_empty_uri(monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "")
    with pytest.raises(RuntimeError):
        _assert_test_port()


def test_assert_test_port_allows_test_port(monkeypatch):
    """The standard test port must pass."""
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7688")
    _assert_test_port()  # no exception


def test_wipe_proceeds_against_test_port(monkeypatch):
    """When NEO4J_URI is the test port, _wipe runs the Cypher (mocked driver)."""
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7688")
    mock_driver = MagicMock()
    mock_session = MagicMock()
    mock_driver.session.return_value.__enter__.return_value = mock_session

    _wipe(mock_driver)

    mock_session.run.assert_called_once_with("MATCH (n) DETACH DELETE n")


def test_wipe_blocks_against_dev_port(monkeypatch):
    """The incident scenario: env was mutated to dev port between
    driver creation and _wipe call. The guard must block before any Cypher
    reaches the driver.
    """
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
    mock_driver = MagicMock()

    with pytest.raises(RuntimeError):
        _wipe(mock_driver)

    # The Cypher must not have been executed.
    mock_driver.session.assert_not_called()


def test_wipe_re_evaluates_env_at_call_time(monkeypatch):
    """The incident vector specifically: env mutated AFTER the
    driver fixture was created but BEFORE _wipe() was called. The guard
    reads NEO4J_URI inside _assert_test_port() each call, not once at
    fixture instantiation.
    """
    # Start on test port — driver "creation" passes
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7688")
    mock_driver = MagicMock()
    # Mutate env AFTER the driver exists — simulate the leaky load_dotenv
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
    with pytest.raises(RuntimeError):
        _wipe(mock_driver)
