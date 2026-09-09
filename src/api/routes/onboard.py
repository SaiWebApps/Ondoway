"""New-city onboarding HTTP surface (Step 5).

A small live-progress API the workbench onboarding panel drives: create a run,
watch every source being consulted (snapshot poll or SSE), review the merged
POIs, then draft beats (behind a cost gate) and upload the city locally.

SECURITY: this router writes ``data/{slug}/`` and shells out to ``scripts.deploy``
— an UNAUTHENTICATED write + deploy surface — so ``src/api/app.py`` mounts it
ONLY inside ``_workbench_api_enabled()`` (the same gate as the workbench CRUD
routers). The public Render deployment sets ``WORKBENCH_API_ENABLED=false`` so it
is never reachable over the internet.

The event log + job lifecycle live in the process-local ``JobStore``
(``src/onboard/jobs.py``); the non-JSON run artifacts (the ``AssembledCity`` +
``CityContext`` + drafted beats) live here in ``_ARTIFACTS``, keyed by job id and
guarded by a lock. The flow itself (connector resolution, per-source consult,
Wikipedia extracts) is the shared, CLI-free ``src/onboard/flow.py``.

Everything uses **onboard** (city onboarding), never **onboarding** — the USER
``/auth/onboarding/*`` surface is unrelated and must not be collided with.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError

from src import city_registry
from src.onboard.assemble import AssembledCity, assemble, write_city
from src.onboard.beat_draft import (
    CostNotConfirmed,
    draft_all,
    estimate_cost,
    planned_beat_count,
)
from src.onboard.flow import (
    MODE_CONNECTORS,
    build_extract_index,
    consult_source,
    resolve_connectors,
)
from src.onboard.jobs import get_store
from src.onboard.models import CityContext

router = APIRouter(prefix="/onboard", tags=["onboard"])

# Repo root: src/api/routes/onboard.py -> src/api/routes -> src/api -> src -> root.
REPO_ROOT = Path(__file__).resolve().parents[3]

# The lifecycle statuses at which an SSE stream is done (the worker will append
# no further events once it reaches one of these).
_TERMINAL_STATUSES = frozenset({"assembled", "uploaded", "error"})

# Wall-clock ceiling for an SSE stream, so a crashed worker (a status that never
# reaches terminal) can never hang a client connection forever.
_SSE_MAX_SECONDS = 60.0


# ---------------------------------------------------------------------------
# Process-local run artifacts (the non-JSON objects the JobStore can't hold).
# ---------------------------------------------------------------------------
@dataclass
class _RunArtifacts:
    """The live objects a run threads across endpoints — never serialised into
    the JSON job store: the request ``ctx``, the pure ``AssembledCity`` (POIs +
    pinned Wikipedia extracts), and the drafted ``beats``."""

    ctx: CityContext
    modes: list[str]
    assembled: AssembledCity | None = None
    beats: list[dict] | None = None


_ARTIFACTS: dict[str, _RunArtifacts] = {}
_ARTIFACTS_LOCK = threading.Lock()


def _put_artifacts(job_id: str, art: _RunArtifacts) -> None:
    with _ARTIFACTS_LOCK:
        _ARTIFACTS[job_id] = art


def _get_artifacts(job_id: str) -> _RunArtifacts | None:
    with _ARTIFACTS_LOCK:
        return _ARTIFACTS.get(job_id)


# ---------------------------------------------------------------------------
# Deploy seam — a patchable module-level wrapper so the upload test can spy on it
# WITHOUT shelling out to a real deploy. Kept tiny on purpose.
# ---------------------------------------------------------------------------
def _run_deploy(cmd: list[str], env: dict) -> subprocess.CompletedProcess:
    """Run the deploy subprocess from the repo root, capturing its output. The
    env is passed in by the caller (a COPY of the API's own environment, so the
    child inherits the SAME NEO4J_* the API is connected to)."""
    return subprocess.run(
        cmd, cwd=str(REPO_ROOT), capture_output=True, text=True, check=False, env=env
    )


def _env_path(name: str) -> Path | None:
    """The ``Path`` in env var ``name``, or ``None`` when unset/empty (so
    ``write_city`` falls back to the real ``data/`` + ``src/cities.json``)."""
    value = os.getenv(name)
    return Path(value) if value else None


# ---------------------------------------------------------------------------
# Request bodies.
# ---------------------------------------------------------------------------
class CreateJobRequest(BaseModel):
    slug: str
    display_name: str
    bbox: tuple[float, float, float, float] | None = None  # (min_lat,max_lat,min_lon,max_lon)
    modes: list[str] = []
    #: The two-letter country whose public holidays this city's opening hours are
    #: read against (Docs/adr/0006). Asked for here, at the one door a new city
    #: comes in through, because a city registered without one cannot plan a
    #: dated day — and the upload refuses rather than discovering that later.
    country: str | None = None


class DraftBeatsRequest(BaseModel):
    confirm_cost: bool = False


class UploadRequest(BaseModel):
    target: str = "local"


# ---------------------------------------------------------------------------
# The background worker: consult every source -> assemble -> stash + record.
# ---------------------------------------------------------------------------
def _run_onboard(job_id: str, ctx: CityContext, modes: list[str]) -> None:
    """Sync worker (FastAPI runs it in a threadpool): drive the shared flow to an
    assembled city, stashing the artifacts + recording the result on the job.

    Wrapped end-to-end in try/except so a failure ALWAYS terminates the stream:
    it appends an ``error`` event and sets status ``error`` — never leaving a
    live SSE stream waiting on a status that will never go terminal.
    """
    store = get_store()
    try:
        slugs = resolve_connectors(modes)
        results = [consult_source(slug, ctx, store, job_id) for slug in slugs]
        # Assemble FIRST so the merged/filtered POI names are final, THEN fetch each
        # POI's Wikipedia extract keyed to those names (a live run grounds against the
        # real POIs, not the raw candidates). Fixture mode ignores the POIs arg.
        assembled = assemble(results, ctx, [])
        assembled.wiki_extracts = build_extract_index(ctx.slug, assembled.pois)

        art = _get_artifacts(job_id)
        if art is not None:
            art.assembled = assembled

        pointers = [p.model_dump() for result in results for p in result.pointers]
        job = store.get(job_id)
        if job is not None:
            job.result = {
                "poi_count": len(assembled.pois),
                "pois": assembled.pois,
                "sources": slugs,
                "pointers": pointers,
            }
        store.append_event(job_id, "phase", "assembled", data={"poi_count": len(assembled.pois)})
        store.set_status(job_id, "assembled")
    except Exception as exc:  # any failure MUST terminate the stream cleanly
        store.append_event(job_id, "error", str(exc))
        store.set_status(job_id, "error", error=str(exc))


# ---------------------------------------------------------------------------
# Endpoints.
# ---------------------------------------------------------------------------
@router.post("/jobs", status_code=202)
def create_job(body: CreateJobRequest, background_tasks: BackgroundTasks) -> dict:
    """Start an onboarding run. Validates the request UP FRONT (unknown/empty
    modes and a missing bbox are rejected 422 — geocode-only onboarding is a
    later live feature), then kicks the assemble flow onto a background task and
    returns 202 with the ``job_id`` the caller polls/streams.

    SECURITY: the ``slug`` is attacker-controlled, and it names the on-disk
    ``data/{slug}/`` corpus dir the upload step writes. Two guards reject a bad
    slug HERE, before any background work or disk write:
    - a malformed / path-traversal slug (``../../x``) fails ``CityContext``
      construction -> 422 (the write path can never escape the data root);
    - a slug that is ALREADY a registered city -> 422 ("already onboarded"),
      because onboarding is for NEW cities and re-onboarding would clobber the
      committed ``data/{slug}/`` corpus and flip its registry entry.
    """
    if not body.modes:
        raise HTTPException(422, "at least one sourcing mode is required")
    unknown = [m for m in body.modes if m not in MODE_CONNECTORS]
    if unknown:
        raise HTTPException(422, f"unknown mode(s) {unknown}; supported: {sorted(MODE_CONNECTORS)}")
    if body.bbox is None:
        raise HTTPException(422, "bbox is required (geocode-only onboarding is a later feature)")

    try:
        ctx = CityContext(
            slug=body.slug,
            display_name=body.display_name,
            bbox=body.bbox,
            country=body.country,
        )
    except ValidationError as exc:
        raise HTTPException(
            422,
            f"invalid city slug {body.slug!r} or country {body.country!r}: the slug must be "
            "lowercase letters/digits/underscore, letter-initial (no path separators or "
            "traversal), and the country a two-letter code (FR, GB, US)",
        ) from exc
    if ctx.slug in city_registry.supported_cities():
        raise HTTPException(
            422, f"city {ctx.slug!r} is already onboarded; onboarding is for NEW cities only"
        )

    store = get_store()
    job = store.create(ctx.slug, list(body.modes))
    store.set_status(job.id, "running")
    _put_artifacts(job.id, _RunArtifacts(ctx=ctx, modes=list(body.modes)))

    background_tasks.add_task(_run_onboard, job.id, ctx, list(body.modes))
    return {"job_id": job.id}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, after_seq: int = 0) -> dict:
    """A seq-numbered snapshot of a run: status/error, the events past
    ``after_seq``, the current max seq, and the accumulated result."""
    store = get_store()
    try:
        snap = store.snapshot(job_id, after_seq)
    except KeyError as exc:
        raise HTTPException(404, f"unknown onboard job {job_id!r}") from exc
    job = store.get(job_id)
    return {
        "job_id": job_id,
        "status": snap.status,
        "error": snap.error,
        "max_seq": snap.max_seq,
        "events": [e.model_dump() for e in snap.events],
        "result": job.result if job is not None else {},
    }


async def _sse(job_id: str):
    """Poll the job store and yield one SSE ``data:`` frame per event in seq
    order; when the run reaches a terminal status AND the stream has drained all
    its events, yield a terminal ``event: end`` frame and stop. A wall-clock
    safety valve stops the stream even if a worker crashed without a status."""
    store = get_store()
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
    """Server-Sent-Events view of a run's live event log (the workbench panel's
    live consult feed)."""
    if get_store().get(job_id) is None:
        raise HTTPException(404, f"unknown onboard job {job_id!r}")
    return StreamingResponse(
        _sse(job_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/jobs/{job_id}/draft-beats")
def draft_beats(job_id: str, body: DraftBeatsRequest) -> dict:
    """Draft SEVERAL beats per assembled POI (~3 verbatim spans each), behind a
    COST GATE.

    Without ``confirm_cost`` this returns 409 + a dollar estimate and calls NO
    drafter at all (structurally zero LLM — the guard returns before any drafter
    is selected or constructed). With ``confirm_cost`` it drafts (mock in the bar
    → free) and records the beat count.

    The estimate is sized off ``planned_beat_count`` — the TRUE multi-beat count
    ``draft_all`` will produce — not the POI count: the extracts were already
    fetched during assembly (``build_extract_index`` in ``_run_onboard``), so the
    user is never shown a ~3x-low (one-beat-era) number."""
    store = get_store()
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, f"unknown onboard job {job_id!r}")
    art = _get_artifacts(job_id)
    if art is None or art.assembled is None or job.status != "assembled":
        raise HTTPException(409, "job is not assembled yet; nothing to draft")

    assembled = art.assembled
    planned = planned_beat_count(assembled.pois, assembled.wiki_extracts)
    if not body.confirm_cost:
        detail = {
            "error": "beat drafting requires cost confirmation",
            "estimate": estimate_cost(planned),
        }
        raise HTTPException(409, detail=detail)

    try:
        beats = draft_all(assembled.pois, assembled.wiki_extracts, confirm=True)
    except CostNotConfirmed as exc:  # defensive — confirm=True never raises this
        raise HTTPException(409, detail={"error": str(exc), "estimate": exc.estimate}) from exc
    except ValueError as exc:
        # get_drafter() now fails closed on an unset/unknown ONBOARD_PROVIDER
        # rather than silently drafting with the free mock. Surface that as a
        # specific, actionable 409 — a 500 would be loud but tell the operator
        # nothing, and the whole point of failing closed is that the human
        # LEARNS the pin is missing instead of receiving fake beats.
        raise HTTPException(409, detail={"error": str(exc)}) from exc

    art.beats = beats
    job.result = {**job.result, "beats_count": len(beats)}
    return {"status": "drafted", "beats_count": len(beats)}


@router.post("/jobs/{job_id}/upload")
def upload_job(job_id: str, body: UploadRequest) -> dict:
    """Write the assembled city to ``data/{slug}/`` (+ its beats) and deploy it to
    the API's OWN active graph via ``scripts.deploy``.

    Only ``target="local"`` is supported here (cloud/Aura is deferred to Step 8).
    The deploy subprocess inherits a COPY of this process's environment, so it
    talks to the SAME NEO4J_* the API is connected to."""
    store = get_store()
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, f"unknown onboard job {job_id!r}")
    if body.target != "local":
        raise HTTPException(422, f"unsupported upload target {body.target!r} (only 'local')")
    art = _get_artifacts(job_id)
    if art is None or art.assembled is None or art.beats is None:
        raise HTTPException(409, "beats not drafted yet; draft before uploading")

    assembled, ctx = art.assembled, art.ctx
    city_dir = write_city(
        assembled,
        ctx,
        data_root=_env_path("ONBOARD_DATA_ROOT"),
        registry_path=_env_path("ONBOARD_REGISTRY_PATH"),
    )
    beats_path = city_dir / "beats.json"
    tmp = beats_path.with_name(beats_path.name + ".tmp")
    tmp.write_text(json.dumps(art.beats, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, beats_path)

    cmd = [sys.executable, "-m", "scripts.deploy", "--slug", ctx.slug]
    env = {**os.environ}  # inherit the API's OWN NEO4J_* etc.
    # A deploy that RAISES (e.g. a spawn failure) must move the job to 'error' —
    # never leave it stuck at 'assembled' with a bare 500. The returncode!=0 path
    # below covers a deploy that ran but failed.
    try:
        res = _run_deploy(cmd, env)
    except Exception as exc:
        store.set_status(job_id, "error", error=f"deploy failed to run: {exc}")
        raise HTTPException(
            500, detail={"error": "deploy failed to run", "exception": str(exc)}
        ) from exc
    if res.returncode != 0:
        store.set_status(job_id, "error", error="deploy failed")
        stderr_tail = (res.stderr or "")[-2000:]
        raise HTTPException(500, detail={"error": "deploy failed", "stderr": stderr_tail})

    store.set_status(job_id, "uploaded")
    return {"status": "uploaded", "city_dir": str(city_dir)}
