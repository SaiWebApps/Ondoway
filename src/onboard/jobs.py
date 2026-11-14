"""In-memory job store for onboarding runs.

The store is PROCESS-LOCAL: it lives in a module-level singleton and dies with
the process. That is intentional and sufficient for the local single-user
workbench (the API and the CLI share one store per process via ``get_store()``).
There is no persistence — a restart drops all jobs, and that is fine because an
onboarding run is a short live session the user watches to completion.

All mutation goes through a ``threading.Lock`` so the SSE streamer, the snapshot
poller, and the background onboarding worker can touch one job concurrently
without racing the ``seq`` counter or the ``events`` list.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from src.onboard.models import (
    OnboardEvent,
    OnboardEventKind,
    OnboardJob,
    OnboardStatus,
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class JobSnapshot:
    """An immutable-ish, copy-based view of a job for the SSE/snapshot
    endpoint. ``events`` is a FRESH list (mutating it does not affect the store);
    ``max_seq`` is the job's current highest seq (0 if it has no events yet)."""

    status: OnboardStatus
    error: str | None
    events: list[OnboardEvent]
    max_seq: int


class JobStore:
    """A dict of ``{job_id: OnboardJob}`` guarded by a single lock.

    ``seq`` numbers are assigned here (not on ``OnboardEvent`` directly) so the
    monotonic, strictly-increasing, gap-free-per-job invariant holds even under
    concurrent appends.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, OnboardJob] = {}
        self._lock = threading.Lock()

    def create(self, slug: str, modes: list[str]) -> OnboardJob:
        """Create a new job (status ``created``) and return it."""
        job = OnboardJob(
            id=uuid4().hex,
            slug=slug,
            modes=list(modes),
            status="created",
            created_at=_now_iso(),
        )
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> OnboardJob | None:
        """Return the LIVE job (for status/lifecycle inspection), or ``None`` if
        unknown.

        The returned job is the store's mutable instance, so do NOT read
        ``job.events`` for streaming — event reads for the SSE/snapshot endpoint
        MUST go through ``snapshot()`` / ``events_since()``, which hand back a
        copied list and cannot lose events to a concurrent append."""
        with self._lock:
            return self._jobs.get(job_id)

    def append_event(
        self,
        job_id: str,
        kind: OnboardEventKind,
        message: str,
        *,
        source: str | None = None,
        url: str | None = None,
        data: dict | None = None,
    ) -> OnboardEvent:
        """Assign the next ``seq`` and append an event to the job.

        The ``seq`` is ``len(events) + 1`` computed under the lock, so it is
        monotonic and strictly increasing per job even with concurrent writers.
        Raises ``KeyError`` if the job is unknown.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            event = OnboardEvent(
                seq=len(job.events) + 1,
                kind=kind,
                message=message,
                source=source,
                url=url,
                data=data or {},
                at=_now_iso(),
            )
            job.events.append(event)
            return event

    def events_since(self, job_id: str, after_seq: int) -> list[OnboardEvent]:
        """Return events with ``seq > after_seq`` (snapshot polling + SSE replay).

        Raises ``KeyError`` if the job is unknown.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            return [e for e in job.events if e.seq > after_seq]

    def snapshot(self, job_id: str, after_seq: int = 0) -> JobSnapshot:
        """Return a COPY-based ``JobSnapshot`` for the SSE/snapshot read
        path: the job's current ``status``/``error``, a FRESH list of events with
        ``seq > after_seq``, and the current max seq — all read under the lock so
        a concurrent writer cannot tear the view. Callers streaming events use
        this instead of ``get().events`` so they never touch the live mutable
        list. Raises ``KeyError`` if the job is unknown."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            events = [e for e in job.events if e.seq > after_seq]
            max_seq = job.events[-1].seq if job.events else 0
            return JobSnapshot(
                status=job.status,
                error=job.error,
                events=events,
                max_seq=max_seq,
            )

    def set_status(
        self,
        job_id: str,
        status: OnboardStatus,
        *,
        error: str | None = None,
    ) -> None:
        """Update the job's status (and optional error). Raises ``KeyError`` if
        the job is unknown."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            job.status = status
            if error is not None:
                job.error = error


# Process-local singleton. The API and CLI share ONE store per process; it holds
# no state across a restart (see module docstring).
_STORE: JobStore | None = None
_STORE_LOCK = threading.Lock()


def get_store() -> JobStore:
    """Return the process-wide ``JobStore`` singleton (lazily created)."""
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                _STORE = JobStore()
    return _STORE
