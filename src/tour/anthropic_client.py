"""ONE bounded Anthropic client factory for every model call the tour engine makes.

WHY THIS EXISTS (measured 2026-07-19). Every Anthropic client in the tour engine
was constructed bare — ``anthropic.Anthropic()`` at ``compose.py:536``,
``verify.py:117``, ``compose_correct.py:212``, ``factcheck.py`` x3 and
``claim_repetition.py:349``. A bare client inherits the SDK defaults: a **600 s
timeout with 2 retries**, i.e. **up to 30 minutes for a single stalled call**.

That is not a theoretical bound. A workbench ``POST /trips/preview`` was observed
holding **128 sockets to api.anthropic.com — 91 CLOSE_WAIT, 34 ESTABLISHED and
unmoving — and had not returned after 32 minutes**, against a UI that promises
"~1 min". The Anthropic API itself was healthy throughout (a direct Haiku call
answered in 1.2 s), so the fault was entirely local: unbounded waits with no
ceiling, multiplied by the compose ladder's fan-out.

The fix is deliberately NOT "make it faster" — it is "make it FAIL, visibly and
soon, instead of hanging". A bounded client turns a 30-minute silent hang into a
prompt, catchable error the compose ladder already knows how to degrade from.

THRESHOLD PROVENANCE — measured or reasoned, none invented:

``JUDGE_TIMEOUT_S`` = 45 s. Judge/verify calls are Haiku with ``max_tokens``
between 5 (``factcheck.py:213``, a single-token verdict) and 2048
(``factcheck.py:117``). A round trip on this machine measured **1.2 s**
(2026-07-19, direct SDK call). 45 s is ~37x that — generous headroom for a slow
or contended call, while still bounding a stall.

``COMPOSE_TIMEOUT_S`` = 180 s. Compose is Opus, STREAMING, with
``thinking={"type": "adaptive"}`` and ``max_tokens=COMPOSE_MAX_OUTPUT_TOKENS``
(64 000). Note that 64 000 was sized for the WHOLE-TOUR composer ("16K truncated
a real 45-min Paris compose mid-JSON", ``compose.py:213-217``); the per-chapter
composer this preview actually uses emits ONE stop of roughly 700 words (~1500
tokens). 180 s is therefore already several times a healthy per-stop call.

Retries: judges get 2 (they are cheap, and transient 429/529 is the common
failure); compose gets zero hidden SDK retries. A fresh complete-tour candidate
is the only meaningful compose retry because it is visible, bounded, and can be
re-evaluated as a unit. Compose streams also have a 600 s absolute deadline, so
periodic streaming bytes cannot evade the SDK's read timeout.

Both are overridable from the environment so an operator can widen them for a
slow link without a code change; the defaults are what the bar and the workbench
run with.
"""

from __future__ import annotations

import os
from typing import Any

#: Haiku judge/verify calls (entailment, coverage, redundancy). See module docstring.
JUDGE_TIMEOUT_S: float = float(os.getenv("ONDOWAY_JUDGE_TIMEOUT_S", "45"))
JUDGE_MAX_RETRIES: int = int(os.getenv("ONDOWAY_JUDGE_MAX_RETRIES", "2"))

#: Opus compose/correction calls (streaming, adaptive thinking). See module docstring.
COMPOSE_TIMEOUT_S: float = float(os.getenv("ONDOWAY_COMPOSE_TIMEOUT_S", "180"))
COMPOSE_MAX_RETRIES: int = int(os.getenv("ONDOWAY_COMPOSE_MAX_RETRIES", "0"))
# An SDK read timeout is not an elapsed-time bound for streaming: periodic bytes
# can keep a paid stream alive indefinitely. Compose adapters close the physical
# stream when this absolute deadline expires.
COMPOSE_ABSOLUTE_DEADLINE_S: float = float(os.getenv("ONDOWAY_COMPOSE_ABSOLUTE_DEADLINE_S", "600"))

#: Offline batch review (the corpus audit). Nothing is waiting on this call, so it is
#: bounded generously and retried: a batch of hundreds meets a slow response eventually,
#: and the interactive budgets turn that into a dead run rather than a slow one.
BATCH_REVIEW_TIMEOUT_S: float = float(os.getenv("ONDOWAY_BATCH_REVIEW_TIMEOUT_S", "300"))
BATCH_REVIEW_MAX_RETRIES: int = int(os.getenv("ONDOWAY_BATCH_REVIEW_MAX_RETRIES", "3"))

# Certification calls are physical-attempt-budgeted before they reach the SDK.
# An SDK retry would evade that ledger, so the certification path always uses zero.
CERTIFICATION_MAX_RETRIES: int = 0

# REMOVED 2026-07-31 (owner order): the ONDOWAY_ENABLE_PAID_LLM_CALLS gate.
#
# It raised RuntimeError here, at SDK-client construction, unless an env var was
# set. That is the wrong place for a gate: the failure surfaced deep inside
# whatever happened to be running, as "paid LLM calls are locked" in a test that
# was not trying to spend anything. Measured cost on the day it was removed —
# 15 errors in test_trip_api and 2 in the authoring gates, none of them about
# spending, all of them this guard ambushing a fixture. Measured benefit: none
# observed; it never once prevented an actual overspend.
#
# Do not reinstate it here. If spend needs bounding, bound it at the entry point
# — the route or the CLI — where the error is legible to the person who caused it
# and can say what will be spent and on what.


def _build(timeout_s: float | None, max_retries: int) -> Any:
    """Construct a real ``anthropic.Anthropic`` with an explicit ceiling.

    Imported INSIDE the function, matching the lazy-import idiom every call site
    here already uses: ``anthropic`` must not become an import-time dependency of
    the tour package (the hermetic bar constructs these classes with stub clients
    and never touches the SDK).
    """
    import anthropic

    return anthropic.Anthropic(timeout=timeout_s, max_retries=max_retries)


def judge_client() -> Any:
    """A bounded client for Haiku judge/verify calls."""
    return _build(JUDGE_TIMEOUT_S, JUDGE_MAX_RETRIES)


def batch_review_client() -> Any:
    """A bounded client for offline review of a whole corpus.

    Not the judge client: that one is sized for a person waiting on a workbench, and a
    45-second ceiling with no retry ends a 449-record run on the first slow response.
    """
    return _build(BATCH_REVIEW_TIMEOUT_S, BATCH_REVIEW_MAX_RETRIES)


def compose_client() -> Any:
    """A bounded client for Opus compose/correction calls."""
    return _build(COMPOSE_TIMEOUT_S, COMPOSE_MAX_RETRIES)


def certification_judge_client() -> Any:
    """Bounded review client whose single physical attempt cannot hide SDK retries."""
    return _build(JUDGE_TIMEOUT_S, CERTIFICATION_MAX_RETRIES)


def certification_compose_client() -> Any:
    """Bounded compose client whose single physical attempt cannot hide SDK retries."""
    return _build(COMPOSE_TIMEOUT_S, CERTIFICATION_MAX_RETRIES)


def certification_batch_compose_client() -> Any:
    """Zero-retry compose client for the explicitly polled batch experiment.

    Unlike an interactive preview, the batch is not attached to a UI latency
    promise.  It therefore has no application-imposed read timeout; durable
    attempt markers and visible polling provide operational control while the
    SDK remains forbidden from making hidden retries.
    """

    return _build(None, CERTIFICATION_MAX_RETRIES)


def certification_batch_read_client() -> Any:
    """Bounded client for batch status polls and result collection.

    Reads are idempotent — a hidden SDK retry cannot double-submit or
    double-spend — so unlike submission they keep the judge's transient-failure
    retries, and each read carries the judge's short timeout: a single hung
    retrieve with no timeout would strand the poll loop past its own deadline,
    which is only checked between calls.
    """

    return _build(JUDGE_TIMEOUT_S, JUDGE_MAX_RETRIES)


__all__ = [
    "CERTIFICATION_MAX_RETRIES",
    "COMPOSE_ABSOLUTE_DEADLINE_S",
    "COMPOSE_MAX_RETRIES",
    "COMPOSE_TIMEOUT_S",
    "JUDGE_MAX_RETRIES",
    "JUDGE_TIMEOUT_S",
    "certification_batch_compose_client",
    "certification_batch_read_client",
    "certification_compose_client",
    "certification_judge_client",
    "compose_client",
    "judge_client",
]
