"""A failed transition sentence must not destroy the tour.

``HaikuGlueClient`` writes the one-line links between stops ("Walk southwest for
about five minutes"). It became the production default on 2026-07-31, replacing a
canned client that could not fail. It shipped with NO exception handling and no
tests of its own, so any provider hiccup on a decorative sentence propagated out
of ``generate()`` and took the whole tour with it.

Measured the day it was found: with ``ANTHROPIC_API_KEY`` blank, the Anthropic
SDK raises ``TypeError("Could not resolve authentication method")`` from inside
``stitch``, and ``POST /trips/preview`` answered **HTTP 500 in 3.3s**. The route
has a Basic-lane fallback for exactly this situation and never reached it,
because the failure happened during PLANNING, before the try block that degrades.

Glue is the cheapest thing in the tour and the least load-bearing. The engine
already has the right answer for "no glue available" — ``NO_GLUE_SENTINEL``,
which callers turn into a deterministic template sentence via
``_coerce_glue_output(out, default=template_nav)``. The client simply never
returned it on failure. These tests pin that it does.
"""

from __future__ import annotations

import pytest

from src.tour.glue_client import DEFAULT_HAIKU_MODEL, NO_GLUE_SENTINEL, HaikuGlueClient


class _RaisingMessages:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.calls = 0

    def create(self, **_kwargs: object) -> object:
        self.calls += 1
        raise self._exc


class _RaisingClient:
    def __init__(self, exc: Exception) -> None:
        self.messages = _RaisingMessages(exc)


def _client_with(exc: Exception) -> HaikuGlueClient:
    """A HaikuGlueClient whose SDK call raises, without touching the real SDK."""
    client = HaikuGlueClient.__new__(HaikuGlueClient)
    client.model = DEFAULT_HAIKU_MODEL
    client.max_output_tokens = 256
    client.input_tokens = 0
    client.output_tokens = 0
    client._client = _RaisingClient(exc)
    client._prompt = "{{CATEGORY}} {{CONTEXT}} {{REQUEST}}"
    return client


@pytest.mark.parametrize(
    "exc",
    [
        TypeError("Could not resolve authentication method"),
        RuntimeError("connection reset"),
        ValueError("overloaded_error"),
    ],
    ids=["missing-credential", "network", "provider-overload"],
)
def test_a_failing_glue_call_degrades_instead_of_killing_the_tour(exc: Exception) -> None:
    """Any exception from the provider becomes the no-glue sentinel.

    The sentinel is the engine's designed "no transition available" value; the
    caller replaces it with a deterministic template sentence. So the tour keeps
    all its stops and loses only a nicety.

    UNDO TEST: remove the try/except from HaikuGlueClient.stitch -> RED, and the
    exception propagates exactly as it did when a blank API key produced a 500.
    """
    client = _client_with(exc)
    out = client.stitch("GLUE_NAV", "context", "walk to the next stop")
    assert out == NO_GLUE_SENTINEL, (
        f"stitch returned {out!r} instead of the no-glue sentinel; a decorative "
        f"transition sentence must never be able to fail a whole tour"
    )
    assert client._client.messages.calls == 1, "the provider should be tried exactly once"


def test_the_failure_is_not_silent() -> None:
    """Degrading is fine. Degrading WITHOUT a trace is how a tour quietly loses
    its transitions and nobody ever finds out — the same class of defect as the
    canned glue that shipped as the default for months."""
    import logging

    client = _client_with(RuntimeError("boom"))
    logger = logging.getLogger("src.tour.glue_client")
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Capture()
    logger.addHandler(handler)
    try:
        client.stitch("GLUE_NAV", "context", "walk to the next stop")
    finally:
        logger.removeHandler(handler)

    assert records, "the glue failure was swallowed with no log record at all"
    assert any("boom" in str(r.getMessage()) or r.exc_info for r in records), (
        "the log record does not carry the underlying cause"
    )
