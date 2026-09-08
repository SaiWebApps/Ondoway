"""Shared test fixtures for Neo4j integration tests.

Tests that need a live Neo4j mark themselves with @pytest.mark.integration.
If Neo4j is unreachable, integration tests are skipped automatically.

Tests connect to a dedicated test Neo4j instance (bolt://localhost:7688)
so that production data in the dev instance is never touched.
Start it with: make db-up DB=test

Phase 4.5 hardening (2026-04-29): _wipe() hard-asserts the connected URI's
port belongs to _TEST_PORT_ALLOWLIST before running DETACH DELETE. Stops a
recurrence of the Phase 4 incident where load_dotenv(override=True) inside
a test mutated NEO4J_URI to the dev port and the conftest fixture wiped
the production corpus. See data/paris/.pipeline-state.json →
backlog.conftest_test_isolation for context.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

# Collection happens before per-test fixtures.  On an ordinary test run, reserve
# paid-provider credentials with empty values BEFORE importing the API or any
# module that calls ``load_dotenv()``.  An explicit Makefile live target is the
# only supported way to preserve real credentials in the test process.
_LIVE_PROVIDER_TESTS = os.getenv("ONDOWAY_LIVE_TESTS") == "1"
if not _LIVE_PROVIDER_TESTS:
    for _paid_key in (
        "ANTHROPIC_API_KEY",
        "ELEVENLABS_API_KEY",
        "OPENAI_API_KEY",
        "RESEND_API_KEY",
    ):
        os.environ[_paid_key] = ""

# Make owns the test environment.  There is deliberately no dotenv fallback:
# a raw pytest invocation with missing/wrong values is rejected by
# ``pytest_configure`` below before a fixture can open a driver.
os.environ["ONBOARD_PROVIDER"] = "mock"
os.environ.setdefault("WORKBENCH_API_ENABLED", "true")

# (A local test run used to declare itself a local build here with
# ONDOWAY_ALLOW_DIRTY_LOCAL_BUILD=1. A dirty tree is no longer a refusal anywhere —
# src.tour.premium_tour.resolve_build_identity stamps HEAD tagged dirty and warns —
# so there is nothing to declare, 2026-08-18.)

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.audio.provider import MockTTSProvider, register_provider

# ── The $0 audio seam (OWNER RULING 2026-07-31: no fake provider is ever served)
#
# src/audio/provider.py deliberately does NOT register MockTTSProvider, because
# that registry IS the workbench's provider dropdown and the set of values
# POST /audio/preview honours. Registering it HERE puts the silent-WAV double in
# the pytest interpreter and nowhere else: the workbench server, the Playwright
# suite's uvicorn subprocess and the Render deployment all import
# src.audio.provider without ever importing this file, so none of them can offer
# or serve it. The whole audio suite keeps costing $0 and no human can be shown
# a fake.
register_provider("mock", MockTTSProvider)
# get_provider() now fails closed on an unset TTS_PROVIDER (it used to fall back
# to "mock" — the defect). Pin the test process to the double it just registered
# so provider-less calls in the suite keep their free path. setdefault, so
# `make test-live` or a deliberate override still wins.
os.environ.setdefault("TTS_PROVIDER", "mock")

# ── The $0 glue seam (same principle, the narration side)
#
# src/tour/generation.py now builds a REAL HaikuGlueClient when no client is
# passed. That was the right fix — the canned client had been the silent default
# on every live path, so every transition sentence in every tour the owner ever
# read was a fixed string. But POST /trips/generate exposes no seam to inject a
# client, so every hermetic test that goes through that route now constructs a
# paid Anthropic client and the money guard refuses AT FIXTURE SETUP: whole
# modules error out instead of running.
#
# Bind the double HERE, the same one door the audio seam uses, and ONLY outside
# the live shard. `make test-live` sets ONDOWAY_LIVE_TESTS=1 and therefore keeps
# the real client — so this can never quietly stand in for a live run, which is
# the failure mode the never-mock-as-default rule exists to prevent. Product code
# keeps no fallback: outside this interpreter the real client is the only one.
#
# Keyed on ONDOWAY_LIVE_TESTS since the ONDOWAY_ENABLE_PAID_LLM_CALLS gate was
# removed 2026-07-31 — see src/tour/anthropic_client.py for why.
if os.getenv("ONDOWAY_LIVE_TESTS") != "1":
    import src.tour.generation as _generation
    from src.tour.glue_client import MockGlueClient as _MockGlueClient

    _generation.HaikuGlueClient = _MockGlueClient

from src.connection import Neo4jConnectionError, create_driver, get_database
from src.schema.constraints import apply_all


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Project policy: a skipped test counts as a FAILURE — no silent non-runs.

    Any skip outcome is flipped to 'failed' (the original skip reason is shown
    as the failure detail). Explicit xfail is preserved, since that is an
    asserted expected-failure, not a silent skip.
    """
    outcome = yield
    report = outcome.get_result()
    if report.skipped and not getattr(report, "wasxfail", False):
        report.outcome = "failed"


@pytest.fixture(autouse=True)
def _money_guard_no_live_compose(request, monkeypatch):
    """HARD money-guard. The product ALWAYS builds the real Opus author + Haiku
    checker (``get_premium_compose_executor`` / ``get_faithfulness_checker`` — there
    is no mock provider, so a CUSTOMER can never be served the stitcher passthrough
    as if it were the narrator). The hermetic bar must therefore be prevented from
    ever CONSTRUCTING those billing clients: for every non-``live`` test, patch the
    real classes to their offline stubs. The hermetic Python shard then
    physically cannot make a paid Anthropic call — the real clients are never
    instantiated.
    ``@pytest.mark.live`` tests run in ``make test`` through the dedicated
    ``test-live`` shard; they intentionally spend and bind the real client by direct
    import, so they are left untouched."""
    if request.node.get_closest_marker("live"):
        if not _LIVE_PROVIDER_TESTS:
            pytest.fail(
                "live provider test requires ONDOWAY_LIVE_TESTS=1; "
                "use the explicit Makefile live target"
            )
        return
    import src.tour.verify as _verify_mod

    # isort: split
    # The import inside the arm below is deliberately local (it used to be pinned
    # byte-for-byte by a structural test retired 2026-08-18); keep it local so the
    # premium module is only imported when the guard actually arms.

    # PREMIUM authoring money-guard: the workbench now uses the same zero-retry,
    # receipt-preserving physical boundary as certification batches. Product
    # construction is replaced by the explicit $0 adapter; injected fake
    # providers still exercise the real executor in unit tests.
    import src.tour.premium_tour as _premium_mod

    _real_premium = _premium_mod.AnthropicPremiumExecutor

    def _guard_premium(provider=None):
        if provider is None:
            return _premium_mod.OfflinePremiumExecutor()
        return _real_premium(provider)

    monkeypatch.setattr(_premium_mod, "AnthropicPremiumExecutor", _guard_premium)
    # No non-live test constructs the real Haiku checker with a fake SDK, so the
    # billing checker is always swapped for the offline trusting stub.
    monkeypatch.setattr(
        _verify_mod, "HaikuFaithfulnessChecker", _verify_mod.MockFaithfulnessChecker
    )

    import src.tour.batch_transport as _batch_mod

    def _guard_batch_client():
        raise RuntimeError(
            "batch_client() blocked by the hermetic money-guard; "
            "use the explicit Makefile live target"
        )

    monkeypatch.setattr(_batch_mod, "batch_client", _guard_batch_client)


# Ports the conftest is allowed to wipe. Update this if your local test
# instance runs on a different port. Dev/production must NEVER be in here.
#
# 7690, 7691 and 7697 are the per-worktree pytest graphs (docker-compose.yml,
# `make test-file LANE=2`). They are dedicated, disposable and
# identical in role to 7688 — one per concurrent worktree, precisely so that
# this module-scoped wipe cannot destroy a sibling agent's fixtures. Dev
# (7687), workbench (7689) and Aura stay out: the workbench suite asserts exact
# state on 7689 and would be broken by a wipe it did not perform.
_TEST_PORT_ALLOWLIST: set[int] = {7688, 7690, 7691, 7697}


def _assert_test_port() -> None:
    """Hard-block if NEO4J_URI is pointed at a non-test port.

    Read at call time (not at fixture instantiation) so a test that
    mutates os.environ between fixture creation and _wipe() can't slip
    a destructive Cypher past the guard.
    """
    uri = os.getenv("NEO4J_URI", "")
    parsed = urlparse(uri)
    port = parsed.port
    if port not in _TEST_PORT_ALLOWLIST:
        raise RuntimeError(
            f"Refusing to _wipe() against non-test Neo4j. "
            f"NEO4J_URI={uri!r} (port={port}). "
            f"Test database must run on port {sorted(_TEST_PORT_ALLOWLIST)}. "
            f"If your local test instance uses a different port, "
            f"update tests/conftest.py:_TEST_PORT_ALLOWLIST."
        )


_XDIST_WORKER_DB = {
    0: {"port": 7688, "password": "ondoway_test_2026"},
    1: {"port": 7690, "password": "ondoway_test2_2026"},
    2: {"port": 7691, "password": "ondoway_test3_2026"},
}
_DB_FIXTURES = frozenset({"driver", "clean_driver", "client", "live_neo4j"})


def pytest_collection_modifyitems(config, items):
    """Auto-tag tests that use a DB fixture so the Makefile can split them."""
    needs_db = pytest.mark.needs_db
    for item in items:
        if _DB_FIXTURES & set(item.fixturenames):
            item.add_marker(needs_db)


def pytest_configure(config):
    """Refuse to run the whole suite against any non-test database.

    Many fixtures across the suite issue ``MATCH (n) DETACH DELETE n`` via their
    own ``create_driver()``, bypassing ``_wipe()``'s per-call guard. Rather than
    guard each one, we hard-stop the entire run when ``NEO4J_URI`` is not an
    allowlisted test port. The cloud (Aura) database is the single persistent
    store and must NEVER be wiped by tests—cloud connectivity is checked
    read-only by the definitive suite or ``make db-parity TARGET=cloud``.
    """
    worker_id = os.environ.get("PYTEST_XDIST_WORKER")
    if worker_id is not None:
        slot = int(worker_id.replace("gw", "")) % len(_XDIST_WORKER_DB)
        db = _XDIST_WORKER_DB[slot]
        os.environ["NEO4J_URI"] = f"bolt://localhost:{db['port']}"
        os.environ["NEO4J_PASSWORD"] = db["password"]

    if _LIVE_PROVIDER_TESTS and (
        os.getenv("MAGIC_LINK_PROVIDER") != "resend" or not os.getenv("RESEND_API_KEY")
    ):
        pytest.exit(
            "Live provider tests require MAGIC_LINK_PROVIDER=resend and a non-empty "
            "RESEND_API_KEY. `make test-live` fetches both from Render; missing "
            "credentials are a failure, never a deselection."
        )

    uri = os.getenv("NEO4J_URI", "")
    port = urlparse(uri).port
    if port not in _TEST_PORT_ALLOWLIST:
        pytest.exit(
            f"Refusing to run the test suite against NEO4J_URI={uri!r} (port={port}). "
            f"The suite contains destructive fixtures; it may only run against the test "
            f"database on port {sorted(_TEST_PORT_ALLOWLIST)}. The cloud DB is the single "
            f"persistent store and is never wiped by tests—use "
            f"`make db-parity TARGET=cloud` for a read-only cloud check."
        )


def _neo4j_available() -> bool:
    try:
        driver = create_driver()
        driver.close()
        return True
    except (Neo4jConnectionError, Exception):
        return False


needs_neo4j = pytest.mark.skipif(
    not _neo4j_available(),
    reason="Neo4j not available—run through the owning Make test target",
)


_lane_lock_file = None


def _acquire_lane_lock() -> None:
    """Prevent two pytest sessions from sharing the same test database.

    fcntl.flock(LOCK_EX | LOCK_NB) fails immediately if another process holds
    the lock, and the OS releases it on process death — no stale lockfiles.

    The lock lives in the system temp dir, keyed by bolt port: the databases
    are machine-global resources, so a repo-relative path would let two
    checkouts (or a worktree) wipe the same graph while each holds its own
    "lock". It also keeps dead sessions from littering the repo root.

    Idempotent per process: `driver` (session) and `clean_driver` (module) both
    call this, and re-opening the file would silently drop the held flock when
    the old file object is garbage-collected.
    """
    global _lane_lock_file
    if _lane_lock_file is not None:
        return
    uri = os.getenv("NEO4J_URI", "")
    port = urlparse(uri).port or 0
    lock_path = Path(tempfile.gettempdir()) / f"ondoway-pytest-lane-{port}.lock"
    _lane_lock_file = lock_path.open("w")
    try:
        fcntl.flock(_lane_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        _lane_lock_file.close()
        _lane_lock_file = None
        raise RuntimeError(
            f"Another test session is already using the test database on port {port}. "
            f"A sandbox worktree runs on LANE=4 (its own graphs); lanes 2/3 are the "
            f"definitive bar's shards. Pass LANE=4, or wait for the other session."
        ) from exc


@pytest.fixture(scope="session")
def driver():
    """Session-scoped Neo4j driver. Wipes DB before and after all tests."""
    _acquire_lane_lock()
    d = create_driver()
    _wipe(d)
    yield d
    _wipe(d)
    d.close()


def _wipe(driver) -> None:
    """DETACH DELETE every node, but ONLY against an allowlisted test port.

    The port assertion runs every call so env mutation between fixture
    creation and wipe (the Phase 4 incident vector) cannot bypass it.
    """
    _assert_test_port()
    with driver.session(database=get_database()) as session:
        session.run("MATCH (n) DETACH DELETE n")


@pytest.fixture(scope="module")
def clean_driver():
    """Create a driver with a clean DB + schema constraints."""
    _acquire_lane_lock()
    _assert_test_port()
    d = create_driver()
    with d.session(database=get_database()) as s:
        s.run("MATCH (n) DETACH DELETE n")
    apply_all(d)
    yield d
    d.close()


@pytest.fixture(scope="module")
def client(clean_driver):
    """TestClient backed by a clean Neo4j database (no seed data)."""
    app = create_app()
    with TestClient(app) as c:
        yield c


def load_onboard_fixture(city: str, name: str) -> dict:
    """Load a Step-3 connector JSON fixture from tests/fixtures/onboard/{city}/{name}.

    Connectors are PURE — their ``parse`` maps a raw provider JSON payload to a
    typed ``ConnectorResult`` — so the bar drives them from these committed
    fixtures and never touches the network.
    """
    return json.loads((Path(__file__).parent / "fixtures" / "onboard" / city / name).read_text())
