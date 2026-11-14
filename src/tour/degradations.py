"""Everything that silently made a tour worse, collected so a human can SEE it.

``HaikuGlueClient`` fails soft — a provider hiccup on one transition sentence
falls back to a template instead of destroying the whole tour. A degrade that
is only logged is invisible: nobody sees it, so it is indistinguishable from
success. Errors are never only logged; they are shown in the workbench.

So: whatever degrades, RECORD it here, and the API hands it to the page.

THE TWO AUDIENCES, both required:

1. **A human** reads ``human`` and knows what they lost, in plain words, with no
   identifiers: "The walking directions between stops were written from a
   template, not by the narrator."
2. **Claude** reads ``detail`` and can act without a conversation: the exception
   type, the message, the component and the operation. The page pastes the whole
   thing as one block.

Request-scoped via ``contextvars`` so a threaded compose fan-out cannot leak one
request's degradations into another's response.
"""

from __future__ import annotations

import contextvars
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import ParamSpec, TypedDict, TypeVar

_P = ParamSpec("_P")
_R = TypeVar("_R")

_ACTIVE: contextvars.ContextVar[list[Degradation] | None] = contextvars.ContextVar(
    "ondoway_degradations", default=None
)


class DegradationFields(TypedDict):
    """One degradation as a plain record (``Degradation.as_dict``)."""

    kind: str
    human: str
    component: str
    error_type: str
    error_message: str
    context: dict[str, str]


class DegradationRow(DegradationFields):
    """One ``summarize`` row: a degradation and how many times its kind happened."""

    count: int


@dataclass(frozen=True)
class Degradation:
    """One thing that quietly went wrong, in both registers."""

    #: Stable machine key, e.g. "glue_call_failed". Groups repeats.
    kind: str
    #: Plain English, no identifiers. What the reader actually lost.
    human: str
    #: What produced it, e.g. "HaikuGlueClient.stitch".
    component: str
    #: Exception class name, when there was one.
    error_type: str = ""
    #: Exception message, untruncated — this is what gets pasted to Claude.
    error_message: str = ""
    #: Anything else worth pasting (category, stop index...).
    context: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> DegradationFields:
        return {
            "kind": self.kind,
            "human": self.human,
            "component": self.component,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "context": dict(self.context),
        }


@contextmanager
def degradation_scope() -> Iterator[list[Degradation]]:
    """Collect degradations recorded inside this block.

    Each request gets its own list. Outside a scope, ``record`` is a no-op — so
    the engine never accumulates unbounded state in a long-lived process, and a
    unit test that does not care is unaffected.
    """
    collected: list[Degradation] = []
    token = _ACTIVE.set(collected)
    try:
        yield collected
    finally:
        _ACTIVE.reset(token)


def in_current_context(fn: Callable[_P, _R]) -> Callable[_P, _R]:
    """Wrap ``fn`` so a worker thread still sees the caller's collection scope.

    MEASURED, and this is why the wrapper is not optional: ``contextvars`` do NOT
    propagate into ``ThreadPoolExecutor`` workers. Recording from a pooled thread
    silently found no active scope and dropped the degradation on the floor —

        recorded kinds: ['same_thread']
        WORKER DEGRADATIONS LOST: True

    An earlier version of this module's docstring asserted "safe to call from
    anywhere, including threads". It was false, in the one module written to stop
    invisible failures. Copying the caller's context carries the scope across, so
    the two compose fan-outs (``premium_tour.execute_premium_plan`` and
    ``authoring``) can report what went wrong inside them.

    ONE ENTERABLE CONTEXT PER CALL, and the ``.copy()`` is the whole point. Both
    fan-outs wrap ONCE and hand the single returned closure to a thread pool, so
    several workers run ``_run`` at the same instant. A ``Context`` object cannot
    be entered by two threads at once — running the wrap-time snapshot directly
    raised ``RuntimeError: cannot enter context: ... is already entered`` on every
    tour with two or more stops, for three days, because real units are slow
    provider calls and the overlap is guaranteed rather than probabilistic.

    The snapshot must still be taken HERE, at wrap time, on the caller's thread:
    that is what carries the caller's collection scope. Calling
    ``contextvars.copy_context()`` inside ``_run`` instead would copy the WORKER's
    empty context and silently drop every degradation — the same invisibility,
    wearing a fix's clothes.
    """
    snapshot = contextvars.copy_context()

    def _run(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        return snapshot.copy().run(fn, *args, **kwargs)

    return _run


def record(
    *,
    kind: str,
    human: str,
    component: str,
    error: BaseException | None = None,
    **context: str,
) -> None:
    """Record one degradation.

    A no-op outside a scope. From a worker thread it only works if the callable
    was wrapped with :func:`in_current_context` — see that function for why.
    """
    active = _ACTIVE.get()
    if active is None:
        return
    active.append(
        Degradation(
            kind=kind,
            human=human,
            component=component,
            error_type=type(error).__name__ if error is not None else "",
            error_message=str(error) if error is not None else "",
            context={k: str(v) for k, v in context.items()},
        )
    )


def summarize(degradations: list[Degradation]) -> list[DegradationRow]:
    """Collapse repeats into one row each, carrying a count and one example.

    A five-stop tour whose narrator is unreachable produces four identical glue
    failures. Showing them four times buries the signal; showing one with
    "happened 4 times" is the same information a person can act on.
    """
    grouped: dict[str, DegradationRow] = {}
    for item in degradations:
        row = grouped.get(item.kind)
        if row is None:
            grouped[item.kind] = {**item.as_dict(), "count": 1}
        else:
            row["count"] += 1
    return list(grouped.values())


__all__ = ["Degradation", "degradation_scope", "in_current_context", "record", "summarize"]
