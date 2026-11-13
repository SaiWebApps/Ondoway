"""The visible-failure machinery, measured.

The owner asked on 2026-07-31: *"Don't just log errors. Actually show them in the
workbench UI. Otherwise, they're invisible."* This file is the executable half of
that. It shipped once with NO tests at all, which a judge caught — a feature
whose entire purpose is "stop trusting that a problem was noticed" cannot itself
be taken on trust.

The thread test is the load-bearing one. The first version of
``degradations.py`` claimed in its docstring to be "safe to call from anywhere,
including threads". Measured, that was false: ``contextvars`` do not cross into
``ThreadPoolExecutor`` workers, so a degradation recorded inside the compose
fan-out was silently dropped — the exact invisibility this module exists to
prevent, in the module itself.

Its FIRST replacement was itself a lie, and that is the more important lesson.
``test_a_degradation_recorded_in_a_worker_thread_is_not_lost`` used the very
shape production uses — one wrap, three pool workers — but its worker body was
an instant no-op, so the three threads never actually ran at the same time. It
passed in 0.02s for three days while every real tour with two or more stops
crashed 100% of the time with ``RuntimeError: cannot enter context``. A
concurrency test whose workers do not provably overlap tests nothing. Its
replacement below, ``test_overlapping_workers_all_record_without_raising``,
forces the overlap by construction with an Event handshake — never a Barrier,
because under the bug the first worker blocks inside the entered context and a
Barrier simply deadlocks instead of reporting.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from src.tour.degradations import (
    degradation_scope,
    in_current_context,
    record,
    summarize,
)

#: Long enough that a healthy handshake never trips it, short enough that a
#: BROKEN wrapper (where the partner thread dies before shaking hands) reports a
#: failure in seconds instead of hanging the suite.
_HANDSHAKE_TIMEOUT_SECONDS = 3.0


def test_recording_outside_a_scope_is_a_no_op() -> None:
    """No scope, no state. A long-lived server must not accumulate rows forever."""
    record(kind="orphan", human="h", component="c")  # must not raise
    with degradation_scope() as collected:
        pass
    assert collected == []


def test_a_recorded_degradation_carries_both_registers() -> None:
    """One row must serve the human AND the engineer — that is the whole ruling."""
    with degradation_scope() as collected:
        record(
            kind="glue_call_failed",
            human="The walking directions were written from a template.",
            component="HaikuGlueClient.stitch",
            error=TypeError("Could not resolve authentication method"),
            glue_category="GLUE_NAV",
        )
    assert len(collected) == 1
    row = collected[0].as_dict()
    assert row["human"].startswith("The walking directions")
    assert row["component"] == "HaikuGlueClient.stitch"
    assert row["error_type"] == "TypeError"
    assert "authentication" in row["error_message"]
    assert row["context"] == {"glue_category": "GLUE_NAV"}


def test_scopes_do_not_leak_into_each_other() -> None:
    """Two requests in one process must not see each other's problems."""
    with degradation_scope() as first:
        record(kind="a", human="h", component="c")
        with degradation_scope() as second:
            record(kind="b", human="h", component="c")
        assert [d.kind for d in second] == ["b"]
    assert [d.kind for d in first] == ["a"]


def test_repeats_collapse_to_one_row_with_a_count() -> None:
    """A five-stop tour with an unreachable narrator yields four identical glue
    failures. Four rows bury the signal; one row saying "happened 4 times" is the
    same information a person can act on."""
    with degradation_scope() as collected:
        for _ in range(4):
            record(kind="glue_call_failed", human="h", component="c")
        record(kind="other", human="h", component="c")
    rows = {r["kind"]: r for r in summarize(collected)}
    assert rows["glue_call_failed"]["count"] == 4
    assert rows["other"]["count"] == 1


def test_overlapping_workers_all_record_without_raising() -> None:
    """THE regression, and the reason its predecessor was deleted rather than kept.

    Production wraps a callable ONCE and hands that single closure to a thread
    pool (``premium_tour.execute_premium_plan`` and ``authoring``). Real units are
    slow provider calls, so two workers are inside the wrapper at the same moment
    on every tour with two or more stops. A ``contextvars.Context`` cannot be
    entered twice at once, so that was a guaranteed ``RuntimeError: cannot enter
    context`` — every premium tour, every time, for three days.

    The overlap here is forced by construction, not hoped for: the first worker to
    arrive parks INSIDE the wrapper until the second has entered too. An Event
    handshake, deliberately not a Barrier — under the bug the parked worker never
    gets released, and a Barrier would deadlock the suite instead of reporting.

    UNDO TESTS, all three EXECUTED — a guard nobody has turned red does not exist:
      * restore the single wrap-time context in ``in_current_context`` (``snapshot.run``
        in place of ``snapshot.copy().run``) → RED with "cannot enter context";
      * move the copy inside ``_run`` (``contextvars.copy_context().run``) → the two
        propagation assertions go RED, because that copies the WORKER's empty
        context and silently drops the caller's collector;
      * drop this test's pool to ``max_workers=1`` so the two workers cannot
        overlap → RED on the handshake assertion, which is what stops this test
        passing vacuously the way its predecessor did.
    """
    first_inside = threading.Event()
    second_inside = threading.Event()
    #: Whether each worker's wait was RELEASED by its partner rather than timing
    #: out. Distinct keys written from distinct threads; read only after join.
    shook_hands: dict[int, bool] = {}

    def _work(index: int) -> str:
        if index == 0:
            first_inside.set()
            # Hold this worker INSIDE the wrapper until its partner has entered.
            shook_hands[index] = second_inside.wait(timeout=_HANDSHAKE_TIMEOUT_SECONDS)
        else:
            shook_hands[index] = first_inside.wait(timeout=_HANDSHAKE_TIMEOUT_SECONDS)
            second_inside.set()
        record(kind=f"worker_{index}", human="h", component="c")
        return f"done_{index}"

    with degradation_scope() as collected:
        record(kind="caller", human="h", component="c")
        wrapped = in_current_context(_work)  # wrapped ONCE, exactly like production
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(wrapped, range(2)))

    # FIRST, and the reason this is not the deleted no-op test wearing a handshake:
    # if the two workers never actually overlapped — a serialised pool, one free
    # thread, a loaded machine — every later assertion below still passes while
    # proving NOTHING about concurrency. A timed-out wait is a failed run, not a
    # green one.
    assert shook_hands == {0: True, 1: True}, (
        f"the workers never overlapped (handshake result {shook_hands}); one of "
        f"them timed out after {_HANDSHAKE_TIMEOUT_SECONDS}s instead of being "
        f"released by its partner, so this run exercised no concurrency at all "
        f"and must not be read as proof the wrapper is re-enterable"
    )
    assert results == ["done_0", "done_1"], (
        f"a wrapped callable run by two overlapping workers did not complete "
        f"(saw {results}); every premium tour with 2+ stops goes through this shape"
    )
    kinds = sorted({d.kind for d in collected})
    assert "worker_0" in kinds, (
        f"the first worker's degradation was DROPPED (saw {kinds}); the fan-out "
        f"would report a clean tour while failing inside it"
    )
    assert "worker_1" in kinds, (
        f"the second worker's degradation was DROPPED (saw {kinds}); the fan-out "
        f"would report a clean tour while failing inside it"
    )
    assert summarize(collected)  # and they survive the summariser


def test_an_unwrapped_worker_still_loses_it_so_the_wrapper_is_load_bearing() -> None:
    """Proves the wrapper is what fixes it, not something incidental.

    Without this, someone could delete ``in_current_context`` from the fan-out
    sites, the test above would still pass (it wraps explicitly), and the product
    would silently regress.
    """
    with degradation_scope() as collected:

        def _work(_: int) -> None:
            record(kind="unwrapped", human="h", component="c")

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(_work, range(2)))

    assert "unwrapped" not in {d.kind for d in collected}, (
        "an UNWRAPPED worker recorded successfully — contextvars now propagate "
        "into pool workers on their own, so in_current_context is dead weight "
        "and the fan-out sites can drop it"
    )
