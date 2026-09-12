"""Ingest jobs and the review queue — Docs/ingestion/rebuild-spec.md §3, §5, D13.

`IngestJobStore` reuses `src.onboard.jobs.JobStore` for what it already
does well: a lock-guarded dict of jobs, gap-free per-job `seq` numbers on
appended events, copy-based snapshots the SSE streamer and the poller can
read while a worker appends. The onboard store assumes only three things
of a job — `.events` (a list of `OnboardEvent`), `.status` and `.error` —
so an `IngestJob` satisfies it without pretending to be an `OnboardJob`;
`create()` is the one method rebuilt, because the onboard signature
(`slug`, `modes`) is not an ingest job's. Events reuse `OnboardEvent`
verbatim: an engine event (`cost_estimate`, `claims_refused`,
`beat_held`, ...) is kind `info` with the engine's event name as the
message and its payload as `data`; a completed phase is kind `phase` with
the phase name as the message.

Phase outputs live on the job (`IngestJob.phases`, JSON-safe dicts) and
are written under the lock before the next phase starts, so a job that
dies mid-run resumes at its last completed phase (`src.ingest.run`).

The review queue (D13) is the same store: an item per held merge, held
narration or new place, keyed by `item_id` = the SHA-256 of what the
reviewer is shown (`shown_hash`). A decision is recorded only against
that hash — a caller deciding on something other than what the queue
holds is refused — and only once. `undecided(city)` is what
`POST /ingest/publish` checks. Process-local like the onboard store: a
restart drops jobs, items and decisions together.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, model_validator

from src.onboard.jobs import JobStore
from src.onboard.models import OnboardEvent

#: The phases in run order; `IngestJob.phases` holds a key per completed one.
PHASES: tuple[str, ...] = ("P0", "P1", "P2", "P3", "P4", "P5", "P6", "P7")

#: Lifecycle: created → running → committed | error. A held item never
#: stops a job (D13): the rest of the chunk still commits.
IngestStatus = Literal["created", "running", "committed", "error"]

#: What the queue holds (D13): a merge the judge and the signature hint
#: disagreed on, a narration still refused after its one revise (or one
#: that failed the code gate twice), or a story at a place the city's
#: POI file does not name.
ReviewKind = Literal["merge_held", "narration_held", "new_place"]

Decision = Literal["accept", "reject"]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def shown_hash(shown: dict[str, Any]) -> str:
    """SHA-256 of the canonical JSON of what a reviewer is shown."""
    canonical = json.dumps(shown, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class IngestSource(BaseModel):
    """The §5 source union: a chunked book (a chunk dir with its manifest)
    or one website page. A book source may name `chunks` — a subset of
    the manifest's filenames (stems accepted) — so one job ingests one
    chunk of a book (slice 9's proof chunk); absent, every manifest chunk
    is a unit."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["book", "url"]
    chunk_dir: str | None = None
    chunks: list[str] | None = None
    url: str | None = None

    @model_validator(mode="after")
    def _shape_matches_kind(self) -> IngestSource:
        if self.kind == "book" and not self.chunk_dir:
            raise ValueError("a book source needs chunk_dir")
        if self.kind == "url" and not self.url:
            raise ValueError("a url source needs url")
        return self


class IngestJob(BaseModel):
    """One ingest job: one source into one city. Mutable by design (the
    store appends events, writes phase outputs and advances status)."""

    id: str
    city: str
    source: IngestSource
    as_of: int | str
    rights_basis: str
    status: IngestStatus = "created"
    events: list[OnboardEvent] = []
    phases: dict[str, Any] = {}
    error: str | None = None
    created_at: str | None = None


class ReviewItem(BaseModel):
    """One D13 queue item. `shown` is exactly what the reviewer sees;
    `item_id` is its hash, so a decision binds to that content."""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    job_id: str
    city: str
    kind: ReviewKind
    story_slug: str
    place: str
    reason: str
    shown: dict[str, Any]
    held_back: dict[str, int]
    decision: dict[str, Any] | None = None
    queued_at: str | None = None


def held_back_rank(item: ReviewItem) -> tuple[int, int]:
    """Content held back, most first: claims, then seconds of narration."""
    return (item.held_back.get("claims", 0), item.held_back.get("duration_sec", 0))


class IngestJobStore(JobStore):
    """`JobStore` with ingest jobs, per-phase outputs and the review queue."""

    def __init__(self) -> None:
        super().__init__()
        self._items: dict[str, ReviewItem] = {}

    def create(  # type: ignore[override]  # the onboard signature is not ours
        self,
        *,
        city: str,
        source: dict[str, Any] | IngestSource,
        as_of: int | str,
        rights_basis: str,
    ) -> IngestJob:
        job = IngestJob(
            id=uuid4().hex,
            city=city,
            source=IngestSource.model_validate(source),
            as_of=as_of,
            rights_basis=rights_basis,
            created_at=_now_iso(),
        )
        with self._lock:
            self._jobs[job.id] = job  # type: ignore[assignment]  # duck-typed job
        return job

    def get(self, job_id: str) -> IngestJob | None:  # type: ignore[override]
        with self._lock:
            return self._jobs.get(job_id)  # type: ignore[return-value]

    def all_jobs(self) -> list[IngestJob]:
        """Every job, in creation order (a list copy; the jobs are live)."""
        with self._lock:
            return list(self._jobs.values())  # type: ignore[arg-type]

    def set_phase(self, job_id: str, phase: str, output: dict[str, Any]) -> None:
        """Record `phase`'s JSON-safe output on the job, under the lock,
        and append the `phase` event that says it landed."""
        if phase not in PHASES:
            raise ValueError(f"unknown phase {phase!r}")
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            job.phases[phase] = output  # type: ignore[attr-defined]
        self.append_event(job_id, "phase", phase, data={"status": "done"})

    def phase_output(self, job_id: str, phase: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            return job.phases.get(phase)  # type: ignore[attr-defined]

    # ── The review queue (D13) ──────────────────────────────────────────

    def queue(
        self,
        *,
        job_id: str,
        city: str,
        kind: ReviewKind,
        story_slug: str,
        place: str,
        reason: str,
        shown: dict[str, Any],
        held_back: dict[str, int],
    ) -> ReviewItem:
        """Add an item keyed by the hash of `shown`; re-queueing identical
        content returns the item already held."""
        item_id = shown_hash(shown)
        with self._lock:
            existing = self._items.get(item_id)
            if existing is not None:
                return existing
            item = ReviewItem(
                item_id=item_id,
                job_id=job_id,
                city=city,
                kind=kind,
                story_slug=story_slug,
                place=place,
                reason=reason,
                shown=shown,
                held_back=held_back,
                queued_at=_now_iso(),
            )
            self._items[item_id] = item
            return item

    def item(self, item_id: str) -> ReviewItem | None:
        with self._lock:
            return self._items.get(item_id)

    def items(self, city: str | None = None) -> list[ReviewItem]:
        """Every item (optionally one city's), most content held back first."""
        with self._lock:
            found = [i for i in self._items.values() if city is None or i.city == city]
        return sorted(found, key=held_back_rank, reverse=True)

    def undecided(self, city: str | None = None) -> list[ReviewItem]:
        return [i for i in self.items(city) if i.decision is None]

    def decide(
        self,
        item_id: str,
        *,
        decision: Decision,
        decided_by: str,
        shown_hash_seen: str,
    ) -> ReviewItem:
        """Record a decision bound to the hash of what was shown.

        Refuses (ValueError) a hash that is not the item's — the caller
        decided on something other than what the queue holds — and a
        second decision on an item already decided. KeyError for an
        unknown item.
        """
        with self._lock:
            item = self._items.get(item_id)
            if item is None:
                raise KeyError(item_id)
            if shown_hash_seen != item.item_id:
                raise ValueError(
                    f"decision for item {item_id!r} is bound to hash {shown_hash_seen!r}, "
                    f"not the hash of what the queue shows ({item.item_id!r})"
                )
            if item.decision is not None:
                raise ValueError(f"item {item_id!r} is already decided")
            item.decision = {
                "decision": decision,
                "decided_by": decided_by,
                "decided_at": _now_iso(),
                "bound_to": shown_hash_seen,
            }
            return item


# Process-local singleton, beside the onboard store's; same lifetime rules.
_STORE: IngestJobStore | None = None
_STORE_LOCK = threading.Lock()


def get_ingest_store() -> IngestJobStore:
    """Return the process-wide `IngestJobStore` singleton (lazily created)."""
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                _STORE = IngestJobStore()
    return _STORE
