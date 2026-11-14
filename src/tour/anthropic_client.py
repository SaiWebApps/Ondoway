"""ONE bounded Anthropic client factory for every model call the tour engine makes.

Every Anthropic client in the tour engine is built here, never bare. A bare
``anthropic.Anthropic()`` inherits the SDK defaults: a **600 s timeout with 2
retries**, i.e. **up to 30 minutes for a single stalled call**, multiplied by every
call a request fans out to.

The bound does not make a call faster; it makes a stalled call FAIL, visibly and
soon, instead of hanging. A bounded client turns a silent hang into a prompt,
catchable error that the caller degrades from.

THRESHOLD PROVENANCE — measured or reasoned, none invented:

``JUDGE_TIMEOUT_S`` = 45 s. Judge/verify calls are Haiku with ``max_tokens``
between 5 (``factcheck.py:213``, a single-token verdict) and 2048
(``factcheck.py:117``). A round trip on this machine measured **1.2 s**
(direct SDK call). 45 s is ~37x that — generous headroom for a slow
or contended call, while still bounding a stall.

``COMPOSE_TIMEOUT_S`` = 180 s. Compose is Opus, STREAMING, with
``thinking={"type": "adaptive"}`` and
``max_tokens=CERTIFICATION_COMPOSE_MAX_OUTPUT_TOKENS``. The per-chapter composer
this preview actually uses emits ONE stop of roughly 700 words (~1500 tokens).
180 s is therefore already several times a healthy per-stop call.

Retries: judges get 2 (they are cheap, and transient 429/529 is the common
failure); compose gets zero hidden SDK retries. A fresh complete-tour candidate
is the only meaningful compose retry because it is visible, bounded, and can be
re-evaluated as a unit.

Both are overridable from the environment so an operator can widen them for a
slow link without a code change; the defaults are what the bar and the workbench
run with.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import anthropic

#: Haiku judge/verify calls (entailment, coverage, redundancy). See module docstring.
JUDGE_TIMEOUT_S: float = float(os.getenv("ONDOWAY_JUDGE_TIMEOUT_S", "45"))
JUDGE_MAX_RETRIES: int = int(os.getenv("ONDOWAY_JUDGE_MAX_RETRIES", "2"))

#: Opus compose/correction calls (streaming, adaptive thinking). See module docstring.
COMPOSE_TIMEOUT_S: float = float(os.getenv("ONDOWAY_COMPOSE_TIMEOUT_S", "180"))
COMPOSE_MAX_RETRIES: int = int(os.getenv("ONDOWAY_COMPOSE_MAX_RETRIES", "0"))

# Certification calls are physical-attempt-budgeted before they reach the SDK.
# An SDK retry would evade that ledger, so the certification path always uses zero.
CERTIFICATION_MAX_RETRIES: int = 0

# No paid-call gate lives here, at SDK-client construction. A gate here fails
# deep inside whatever happens to be running, including tests that spend
# nothing. If spend needs bounding, bound it at the entry point — the route or
# the CLI — where the error is legible to the person who caused it and can say
# what will be spent and on what.


def _build(timeout_s: float | None, max_retries: int) -> anthropic.Anthropic:
    """Construct a real ``anthropic.Anthropic`` with an explicit ceiling.

    Imported INSIDE the function, matching the lazy-import idiom every call site
    here already uses: ``anthropic`` must not become an import-time dependency of
    the tour package (the hermetic bar constructs these classes with stub clients
    and never touches the SDK).
    """
    import anthropic

    return anthropic.Anthropic(timeout=timeout_s, max_retries=max_retries)


def judge_client() -> anthropic.Anthropic:
    """A bounded client for Haiku judge/verify calls."""
    return _build(JUDGE_TIMEOUT_S, JUDGE_MAX_RETRIES)


def compose_client() -> anthropic.Anthropic:
    """A bounded client for Opus compose/correction calls."""
    return _build(COMPOSE_TIMEOUT_S, COMPOSE_MAX_RETRIES)


def certification_judge_client() -> anthropic.Anthropic:
    """Bounded review client whose single physical attempt cannot hide SDK retries."""
    return _build(JUDGE_TIMEOUT_S, CERTIFICATION_MAX_RETRIES)


def certification_compose_client() -> anthropic.Anthropic:
    """Bounded compose client whose single physical attempt cannot hide SDK retries."""
    return _build(COMPOSE_TIMEOUT_S, CERTIFICATION_MAX_RETRIES)


def certification_batch_compose_client() -> anthropic.Anthropic:
    """Zero-retry compose client for the explicitly polled batch experiment.

    Unlike an interactive preview, the batch is not attached to a UI latency
    promise.  It therefore has no application-imposed read timeout; durable
    attempt markers and visible polling provide operational control while the
    SDK remains forbidden from making hidden retries.
    """

    return _build(None, CERTIFICATION_MAX_RETRIES)


def certification_batch_read_client() -> anthropic.Anthropic:
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
