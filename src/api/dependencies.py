"""FastAPI dependency injection for Neo4j sessions."""

from __future__ import annotations

from collections.abc import Generator
from typing import TYPE_CHECKING

from src.connection import create_driver, get_database

if TYPE_CHECKING:
    from neo4j import Driver, Session

    from src.tour.verify import FaithfulnessChecker

_driver: Driver | None = None


def init_driver() -> None:
    """Called during FastAPI lifespan startup.

    Builds the driver LAZILY (no eager connectivity check): the web service must
    start and open its port even when Neo4j is unreachable, or the Render deploy
    fails its health check and the whole release is rolled back. DB errors then
    surface per-request (and /api/v1/healthz reports ``neo4j_connected: false``)
    instead of crashing startup. If even constructing the driver fails (malformed
    URI / missing env), we log and leave it None so the app still starts —
    requests then 500 with the clear "Driver not initialized" assert.
    """
    global _driver
    try:
        _driver = create_driver(verify=False)
        _init_aura_resume()
    except Exception as exc:  # never let driver setup crash startup
        import logging

        logging.getLogger("ondoway.api").warning(
            "Neo4j driver init failed at startup (%s); starting anyway — DB calls "
            "will fail per-request until connectivity returns.",
            exc,
        )
        _driver = None


def _init_aura_resume() -> None:
    """Build the Aura resume coordinator and, if enabled, fire a wake in the
    background so a cold deploy against a PAUSED instance starts resuming it
    immediately — without blocking startup or crashing if Aura is unreachable.

    A NO-OP when AURA creds are unset (local dev + CI): build_coordinator()
    returns None and nothing runs.
    """
    import threading

    from src.aura_resume import get_coordinator, reset_coordinator

    # Re-read env in case this process was redeployed with newly-set creds.
    reset_coordinator()
    coordinator = get_coordinator()
    if coordinator is None:
        return

    def _wake() -> None:
        try:
            coordinator.ensure_resuming()
        except Exception as exc:  # ensure_resuming never raises, but be defensive
            import logging

            logging.getLogger("ondoway.aura").warning(
                "Background Aura wake failed at startup: %s", exc
            )

    threading.Thread(target=_wake, name="aura-resume-startup", daemon=True).start()


def get_resume_coordinator():
    """Return the process-wide Aura ResumeCoordinator, or None if disabled.

    The app.py ServiceUnavailable handler + degraded healthz call this to nudge a
    paused instance awake on a failed DB probe.
    """
    from src.aura_resume import get_coordinator

    return get_coordinator()


def close_driver() -> None:
    """Called during FastAPI lifespan shutdown."""
    global _driver
    if _driver:
        _driver.close()
        _driver = None


def get_session() -> Generator[Session]:
    """Yield a Neo4j session for the duration of one request."""
    assert _driver is not None, "Driver not initialized — is the app running?"
    session = _driver.session(database=get_database())
    try:
        yield session
    finally:
        session.close()


def get_driver() -> Driver:
    """Return the process-wide driver (the tour engine's corpus loader opens
    its own sessions, so it needs the driver rather than a request session)."""
    assert _driver is not None, "Driver not initialized — is the app running?"
    return _driver


def get_premium_compose_executor():
    """The paid writer for POST /trips/{id}/compose: zero-retry and receipt-preserving.
    It writes the day's stops, their longer tellings and the standby stops. The
    place-recipe build constructs its own writer and does not use this factory."""

    import src.tour.premium_tour as premium_tour

    return premium_tour.AnthropicPremiumExecutor()


def get_faithfulness_checker() -> FaithfulnessChecker | None:
    """VERIFY's entailment checker for the authoring paths — ALWAYS the real Haiku
    checker, paired with the real Opus author (M-7: the real authoring is never
    gated by the trusting Mock). The hermetic suite patches this to an offline
    stub (``tests/conftest.py``'s money-guard); tests override to inject rejecting
    checkers.
    """
    from src.tour.verify import HaikuFaithfulnessChecker

    return HaikuFaithfulnessChecker()
