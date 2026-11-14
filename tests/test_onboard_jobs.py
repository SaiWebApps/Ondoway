"""Unit tests for the in-memory onboarding job store (src/onboard/jobs.py).

Pure — no Neo4j, no network. Run with: make test-file FILE=tests/test_onboard_jobs.py
"""

from __future__ import annotations

import threading
from itertools import pairwise

import pytest

from src.onboard.jobs import JobStore, get_store
from src.onboard.models import OnboardEvent, OnboardJob


def test_create_returns_created_job_with_empty_events() -> None:
    store = JobStore()
    job = store.create("london", ["license_clean"])
    assert isinstance(job, OnboardJob)
    assert job.status == "created"
    assert job.events == []
    assert job.slug == "london"
    assert job.modes == ["license_clean"]
    assert job.id  # non-empty id
    assert job.created_at  # ISO timestamp stamped


def test_create_ids_are_distinct() -> None:
    store = JobStore()
    a = store.create("london", [])
    b = store.create("berlin", [])
    assert a.id != b.id


def test_append_event_assigns_monotonic_seq() -> None:
    store = JobStore()
    job = store.create("london", [])
    e1 = store.append_event(job.id, "info", "one")
    e2 = store.append_event(job.id, "source_consult", "two", source="wikipedia")
    e3 = store.append_event(job.id, "phase", "three")
    assert [e1.seq, e2.seq, e3.seq] == [1, 2, 3]
    # The returned event carries the seq, and the job's events list grows.
    assert isinstance(e1, OnboardEvent)
    assert [e.seq for e in job.events] == [1, 2, 3]
    assert job.events[1].source == "wikipedia"


def test_append_event_seq_strictly_increasing() -> None:
    store = JobStore()
    job = store.create("london", [])
    seqs = [store.append_event(job.id, "info", f"m{i}").seq for i in range(5)]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)  # all unique
    assert all(b - a == 1 for a, b in pairwise(seqs))  # gap-free


def test_events_since_returns_only_after_seq() -> None:
    store = JobStore()
    job = store.create("london", [])
    for i in range(4):
        store.append_event(job.id, "info", f"m{i}")
    after_two = store.events_since(job.id, 2)
    assert [e.seq for e in after_two] == [3, 4]
    # after_seq=0 yields everything; after_seq at/above the max yields nothing.
    assert [e.seq for e in store.events_since(job.id, 0)] == [1, 2, 3, 4]
    assert store.events_since(job.id, 4) == []


def test_get_unknown_returns_none() -> None:
    store = JobStore()
    assert store.get("does-not-exist") is None


def test_append_event_unknown_job_raises_keyerror() -> None:
    store = JobStore()
    with pytest.raises(KeyError):
        store.append_event("nope", "info", "x")


def test_events_since_unknown_job_raises_keyerror() -> None:
    store = JobStore()
    with pytest.raises(KeyError):
        store.events_since("nope", 0)


def test_set_status_updates_status_and_error() -> None:
    store = JobStore()
    job = store.create("london", [])
    store.set_status(job.id, "running")
    assert store.get(job.id).status == "running"
    store.set_status(job.id, "error", error="boom")
    got = store.get(job.id)
    assert got.status == "error"
    assert got.error == "boom"


def test_get_store_is_singleton() -> None:
    assert get_store() is get_store()


def test_concurrent_appends_yield_unique_contiguous_seqs() -> None:
    """~8 threads each appending many events must never collide on a seq or
    leave a gap — proves the lock serialises seq assignment."""
    store = JobStore()
    job = store.create("london", [])
    per_thread = 50
    n_threads = 8

    def worker() -> None:
        for _ in range(per_thread):
            store.append_event(job.id, "info", "x")

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    seqs = sorted(e.seq for e in job.events)
    expected = list(range(1, n_threads * per_thread + 1))
    assert seqs == expected  # unique AND contiguous 1..N


class _TrackingLock:
    """A minimal context manager standing in for the store's ``threading.Lock``,
    recording enter/exit counts so a test can prove ``append_event`` acquires it."""

    def __init__(self) -> None:
        self.enters = 0
        self.exits = 0

    def __enter__(self) -> _TrackingLock:
        self.enters += 1
        return self

    def __exit__(self, *exc: object) -> bool:
        self.exits += 1
        return False


def test_append_event_acquires_lock() -> None:
    """append_event MUST take the store lock. Deleting `with self._lock:` from
    append_event makes this go RED deterministically (the tracking lock is never
    entered) — unlike the 8x50 concurrency test, which the GIL can let pass even
    without the lock."""
    store = JobStore()
    job = store.create("london", [])  # uses the real lock
    tracker = _TrackingLock()
    store._lock = tracker  # inject BEFORE the call under test
    store.append_event(job.id, "info", "x")
    assert tracker.enters >= 1
    assert tracker.exits >= 1


def test_seq_is_store_assigned_and_equals_position() -> None:
    """The STORE is the seq authority: after N appends the events carry seqs
    1..N in order, and each returned event's seq equals its 1-based call
    position — the caller never supplies a seq."""
    store = JobStore()
    job = store.create("london", [])
    n = 6
    returned = [store.append_event(job.id, "info", f"m{i}") for i in range(n)]
    assert [e.seq for e in job.events] == list(range(1, n + 1))
    for position, event in enumerate(returned, start=1):
        assert event.seq == position


def test_snapshot_returns_status_and_event_copy() -> None:
    """snapshot() is the sanctioned copy-based read path for the
    SSE/snapshot endpoint: status/error, a FRESH list of events with
    seq > after_seq, and the current max seq — and mutating that list must NOT
    touch the store's live job."""
    store = JobStore()
    job = store.create("london", [])
    for i in range(3):
        store.append_event(job.id, "info", f"m{i}")
    store.set_status(job.id, "running")

    snap = store.snapshot(job.id, after_seq=1)
    assert snap.status == "running"
    assert snap.error is None
    assert [e.seq for e in snap.events] == [2, 3]
    assert snap.max_seq == 3

    # The returned events are a COPY: clearing it must not affect the store.
    snap.events.clear()
    assert [e.seq for e in store.get(job.id).events] == [1, 2, 3]


def test_snapshot_unknown_job_raises_keyerror() -> None:
    store = JobStore()
    with pytest.raises(KeyError):
        store.snapshot("nope")
