"""The ingest front door and review queue — Docs/ingestion/rebuild-spec.md §5.

`POST /ingest/jobs` starts a job (202 + id) that `src.ingest.run.run_job`
drives in a background task on the client `INGEST_PROVIDER` names (the
mock scripted from `INGEST_MOCK_SCRIPT`, or the live client; unset fails
closed — a job never spends by default), writing under `INGEST_DATA_ROOT`
(the repo's data/ when unset). `GET /ingest/jobs/{id}` is the seq-numbered
snapshot with a per-phase summary; `GET /ingest/jobs/{id}/stream` is the
SSE view of the same log. `GET /ingest/review?city=` lists the D13 queue
ranked by content held back; `POST /ingest/review/decision` records a
decision bound to the hash of what was shown (a stale or foreign hash is
refused); `POST /ingest/publish?city=&target=` refuses while any held
item of that city is undecided, and otherwise answers 501: the publisher
converge that makes the graph match the file is slice 8, and a silent
202 here would claim a publish that never happened.

SECURITY: this router reads a caller-named chunk dir and writes
`data/{city}/` — an UNAUTHENTICATED read/write surface — so
`src/api/app.py` mounts it ONLY inside `_workbench_api_enabled()`, the
same gate as the onboard router and the workbench CRUD. The public
deployment sets `WORKBENCH_API_ENABLED=false`.

The job store and queue are `src.ingest.jobs.get_ingest_store()`:
process-local, beside the onboard store, same lifetime rules.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError

from src import city_registry
from src.ingest import run as run_mod
from src.ingest.jobs import PHASES, IngestJob, IngestSource, ReviewItem, get_ingest_store

router = APIRouter(prefix="/ingest", tags=["ingest"])

_TERMINAL_STATUSES = frozenset({"committed", "error"})

#: Wall-clock ceiling for an SSE stream, so a crashed worker can never
#: hang a client connection forever (same valve as the onboard stream).
_SSE_MAX_SECONDS = 600.0


class CreateJobRequest(BaseModel):
    city: str
    source: dict[str, Any]
    as_of: int | str
    rights_basis: str


class DecisionRequest(BaseModel):
    item_id: str
    decision: Literal["accept", "reject"]
    decided_by: str
    shown_hash: str


def _phase_summary(job: IngestJob) -> dict[str, dict[str, Any]]:
    """A small per-phase view for the page: never the phase payloads
    themselves (P6 carries the whole file)."""
    out: dict[str, dict[str, Any]] = {}
    for phase in PHASES:
        output = job.phases.get(phase)
        if output is None:
            out[phase] = {"status": "pending"}
            continue
        summary: dict[str, Any] = {"status": "done"}
        if phase == "P0":
            summary["units"] = len(output.get("units", []))
        elif phase == "P1":
            summary["claims"] = sum(len(v) for v in output.get("claims", {}).values())
        elif phase == "P2":
            summary["stories"] = sum(len(v) for v in output.get("stories", {}).values())
        elif phase == "P3":
            summary["stories"] = sum(len(v) for v in output.get("stories", {}).values())
            summary["reasked_units"] = list(output.get("reasked_units", []))
        elif phase == "P4":
            summary["drafts"] = len(output.get("drafts", {}))
        elif phase == "P5":
            summary["narrations"] = len(output.get("narrations", {}))
        elif phase == "P6":
            summary["new"] = list(output.get("new", []))
            summary["applied"] = list(output.get("applied", []))
            summary["rerun"] = list(output.get("rerun", []))
        elif phase == "P7":
            summary["beats_path"] = output.get("beats_path")
            summary["written"] = output.get("written", 0)
        out[phase] = summary
    return out


def _current_phase(job: IngestJob) -> str | None:
    if job.status in _TERMINAL_STATUSES:
        return None
    return next((p for p in PHASES if p not in job.phases), None)


def _item_view(item: ReviewItem) -> dict[str, Any]:
    view = item.model_dump()
    view["shown_hash"] = item.item_id
    return view


def _run_in_background(job_id: str) -> None:
    """Sync worker (FastAPI runs it in a threadpool). `run_job` never
    raises; a client the env cannot build ends the job in `error`."""
    store = get_ingest_store()

    def sink(kind: str, payload: dict) -> None:
        # The runner logs the estimate itself (it must be the first job
        # event whatever sink the client has); everything else the client
        # says (batch ids on the live path) goes to the job log too.
        if kind != "cost_estimate":
            store.append_event(job_id, "info", kind, data=payload)

    try:
        client = run_mod.client_from_env(sink)
    except Exception as exc:
        store.append_event(job_id, "error", str(exc))
        store.set_status(job_id, "error", error=str(exc))
        return
    run_mod.run_job(job_id, store, client, data_root=run_mod.data_root_from_env())


@router.post("/jobs", status_code=202)
def create_job(body: CreateJobRequest, background_tasks: BackgroundTasks) -> dict:
    """Start an ingest job; 202 + the job id the caller polls or streams.

    Refused 422 up front: a city the registry does not know (the beats
    file it would write is `data/{city}/`), a source that is not the §5
    union, a book source whose chunk dir has no manifest."""
    if body.city not in city_registry.supported_cities():
        raise HTTPException(422, f"unknown city {body.city!r}")
    try:
        source = IngestSource.model_validate(body.source)
    except ValidationError as exc:
        raise HTTPException(422, f"invalid source: {exc.errors()[0]['msg']}") from exc
    if source.kind == "book" and not (Path(source.chunk_dir or "") / "manifest.json").is_file():
        raise HTTPException(422, f"chunk dir {source.chunk_dir!r} has no manifest.json")
    store = get_ingest_store()
    job = store.create(
        city=body.city, source=source, as_of=body.as_of, rights_basis=body.rights_basis
    )
    store.set_status(job.id, "running")
    background_tasks.add_task(_run_in_background, job.id)
    return {"job_id": job.id}


@router.get("/jobs")
def list_jobs() -> dict:
    store = get_ingest_store()
    jobs_ = store.all_jobs()
    return {
        "jobs": [
            {
                "job_id": j.id,
                "city": j.city,
                "source": j.source.model_dump(),
                "status": j.status,
                "error": j.error,
                "created_at": j.created_at,
                "current_phase": _current_phase(j),
            }
            for j in jobs_
        ]
    }


@router.get("/jobs/{job_id}")
def get_job(job_id: str, after_seq: int = 0) -> dict:
    """A seq-numbered snapshot: status/error, the events past `after_seq`,
    the max seq, and a per-phase summary."""
    store = get_ingest_store()
    try:
        snap = store.snapshot(job_id, after_seq)
    except KeyError as exc:
        raise HTTPException(404, f"unknown ingest job {job_id!r}") from exc
    job = store.get(job_id)
    if job is None:  # a concurrent delete between the two reads; nothing deletes today
        raise HTTPException(404, f"unknown ingest job {job_id!r}")
    return {
        "job_id": job_id,
        "city": job.city,
        "source": job.source.model_dump(),
        "as_of": job.as_of,
        "rights_basis": job.rights_basis,
        "status": snap.status,
        "error": snap.error,
        "max_seq": snap.max_seq,
        "events": [e.model_dump() for e in snap.events],
        "phases": _phase_summary(job),
        "current_phase": _current_phase(job),
    }


async def _sse(job_id: str):
    """One SSE `data:` frame per event in seq order; a terminal
    `event: end` frame once the job is done and drained."""
    store = get_ingest_store()
    after = 0
    start = time.monotonic()
    while True:
        try:
            snap = store.snapshot(job_id, after)
        except KeyError:
            yield "event: end\ndata: {}\n\n"
            return
        for event in snap.events:
            yield f"data: {json.dumps(event.model_dump(), default=str)}\n\n"
            after = event.seq
        after = max(after, snap.max_seq)
        if snap.status in _TERMINAL_STATUSES and after >= snap.max_seq:
            yield "event: end\ndata: {}\n\n"
            return
        if time.monotonic() - start > _SSE_MAX_SECONDS:
            yield "event: end\ndata: {}\n\n"
            return
        await asyncio.sleep(0.05)


@router.get("/jobs/{job_id}/stream")
def stream_job(job_id: str) -> StreamingResponse:
    if get_ingest_store().get(job_id) is None:
        raise HTTPException(404, f"unknown ingest job {job_id!r}")
    return StreamingResponse(
        _sse(job_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/review")
def list_review(city: str | None = None) -> dict:
    """The D13 queue (one city's, or all), most content held back first;
    each item carries the hash a decision must be bound to."""
    store = get_ingest_store()
    return {"items": [_item_view(i) for i in store.items(city)]}


@router.post("/review/decision")
def record_decision(body: DecisionRequest) -> dict:
    """Record a decision bound to the hash of what was shown: 404 for an
    unknown item, 409 when the hash is not the item's or it is already
    decided."""
    store = get_ingest_store()
    try:
        item = store.decide(
            body.item_id,
            decision=body.decision,
            decided_by=body.decided_by,
            shown_hash_seen=body.shown_hash,
        )
    except KeyError as exc:
        raise HTTPException(404, f"unknown review item {body.item_id!r}") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"item": _item_view(item)}


@router.post("/publish")
def publish(city: str, target: str = "local") -> dict:
    """Refuse while any held item of `city` is undecided (409, naming
    them). Otherwise 501: the converge (§6) is slice 8."""
    store = get_ingest_store()
    undecided = store.undecided(city)
    if undecided:
        raise HTTPException(
            409,
            detail={
                "error": f"{len(undecided)} held item(s) for {city!r} are undecided",
                "undecided": [i.item_id for i in undecided],
            },
        )
    raise HTTPException(
        501,
        detail={
            "error": "publish is not wired yet: the publisher converge (spec §6) is slice 8",
            "city": city,
            "target": target,
        },
    )
