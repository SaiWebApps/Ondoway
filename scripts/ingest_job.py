"""One headless ingest job — `make ingest-job CITY= CHUNK_DIR= [ARGS=]`.

Docs/ingestion/rebuild-spec.md §8 slice 9 runs the first REAL jobs (the
Lonely Planet Upper East Side chunk, then the Frommer's one so the merge
fires on the Guggenheim). Owner rulings 2026-09-12: the job launches
through this target and nothing else; the live client takes its key from
the Render environment `make` fetches (`render-key`), never from a hand
export; the new-schema beats file lives in a gitignored `data-ingest/`
root beside `data/` until the slice-10 swap, so the real legacy
`data/{city}/beats.json` stays untouched and P0's shape gate never sees it.

    make ingest-job CITY=new_york \\
        CHUNK_DIR=Books/new_york/lonely-planet-new-york-city \\
        ARGS="--chunk chunk-07-upper-east-side --as-of 2023"        # estimate, STOP
    ... ARGS="--chunk chunk-07-upper-east-side --as-of 2023 --yes"  # spend

The estimate is printed and the run refused unless `--yes` (the same
shape as `make ingest-calibrate-live`). The whole job log is written to
`{data_root}/{city}/jobs/{job_id}.jsonl` — the store is process-local —
and a measurement summary closes the run: the P6 hold rate with every
`beat_held` reason, the leak gate's drops, and the attempt-one refusals
(the precision side of P3).

Each phase's output is also written to `{data_root}/{city}/jobs/{job_id}/`
as it lands, so a job that dies continues from its last finished phase:

    make ingest-job CITY=new_york CHUNK_DIR=<the job's chunk dir> \\
        ARGS="--resume <job_id> --yes"

A resume is refused, before any call, for an unknown or already committed
job, a gap in its phase files, changed chunk text, a beats file changed
since the job started, or a flag that contradicts the job.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.ingest import jobs, llm, model, run


def ensure_root(data_root: Path, repo_data: Path, city: str) -> dict[str, Path]:
    """Make `{data_root}/{city}/` a home for the new-schema file: the city's
    real `poi-raw.json` copied in (place resolution reads it) and an EMPTY
    new-shape `beats.json`. A file already there is never touched — the
    second job of a proof must merge into the first's beats."""
    city_dir = Path(data_root) / city
    city_dir.mkdir(parents=True, exist_ok=True)
    poi_raw = city_dir / "poi-raw.json"
    beats = city_dir / "beats.json"
    if not poi_raw.is_file():
        shutil.copyfile(Path(repo_data) / city / "poi-raw.json", poi_raw)
    if not beats.is_file():
        beats.write_text(json.dumps([]) + "\n", encoding="utf-8")
    return {"poi_raw": poi_raw, "beats": beats}


def _sha256(data: bytes | str) -> str:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()


def _write_json_atomic(path: Path, payload: object) -> None:
    """Write to a temporary name and rename, so a crash never leaves a
    half-written phase file that a resume would trust."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _print_event(kind: str, payload: dict) -> None:
    print(f"event {kind}: {json.dumps(payload, ensure_ascii=False)}")


def client_event_sink(store: jobs.IngestJobStore, holder: dict[str, str]):
    """The model client's event sink: once `holder["job_id"]` is set, every
    client event (batch_submitted, the batch_polling heartbeats, ...) is
    appended to the job log through the store, which also prints it;
    before that it is printed. `cost_estimate` is only printed — the
    runner logs its own copy as the job's first event."""

    def sink(kind: str, payload: dict) -> None:
        job_id = holder.get("job_id")
        if kind == "cost_estimate" or job_id is None:
            _print_event(kind, payload)
            return
        store.append_event(job_id, "info", kind, data=payload)

    return sink


def _first_pass_usd(estimate: llm.CostEstimate) -> float:
    """The rows that are each phase's FIRST ask: the projected spend when
    nothing is refused. The whole plan is the projection with every re-ask;
    `run.cap_bound_usd` bounds every planned call, but not ADR-0005's
    re-narration of held stories (one P4 and P5 each), which only the
    metered actual shows."""
    return sum(row.usd for row, first in zip(estimate.rows, run.FIRST_PASS, strict=True) if first)


def _print_estimate(estimate: llm.CostEstimate) -> None:
    print(f"estimated spend: ${estimate.total_usd:.4f} (a projection: every row at its expected "
          f"output; prices of {estimate.prices_cached_on}; {estimate.units} unit(s))")
    print(f"first-pass (no re-asks): ${_first_pass_usd(estimate):.4f}")
    print(f"expected (re-asks at measured rates): ${run.expected_usd(estimate):.4f}")
    print(f"cap-bound (every row at its max_tokens): ${run.cap_bound_usd(estimate):.4f}")
    for row in estimate.rows:
        per_call = row.input_tokens // row.calls if row.calls else 0
        print(
            f"  {row.phase:<3} {row.role:<16} {row.model_id:<28} calls={row.calls:<4} "
            f"in={row.input_tokens:<8} in/call={per_call:<6} out={row.output_tokens:<7} "
            f"${row.usd:.4f}"
        )


class _CountingClient:
    """The model client with a meter on it: every call the run actually
    makes, per phase (a batch counts each prompt), and the usage the
    completions report, priced at the same table the estimate used. The
    protocol methods are forwarded; everything else too."""

    def __init__(self, inner: llm.ModelClient) -> None:
        self._inner = inner
        self.calls: dict[str, int] = {}
        self.usd: float = 0.0
        self.tokens: dict[str, int] = {"input": 0, "output": 0}
        #: Per phase, so the sync phases (never recoverable from a batch) are measured too.
        self.phase_tokens: dict[str, dict[str, int]] = {}
        #: Calls the client raised on (truncated, empty, transport): billed,
        #: but their usage never reached the meter — spend is a lower bound.
        self.unmetered: dict[str, int] = {}

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    def _meter(self, phase: str, role: str, completion) -> None:
        """Failure-isolated: a metering error loses a number (printed as a
        warning), never the paid run it sits on the call path of."""
        try:
            self._meter_unsafe(phase, role, completion)
        except Exception as exc:  # a meter must never end a paid run
            print(f"meter warning: {type(exc).__name__}: {exc} (phase {phase}, role {role})")

    def _meter_unsafe(self, phase: str, role: str, completion) -> None:
        usage = getattr(completion, "usage", None)
        if usage is None:  # a BatchFailure carries no usage
            return
        # Priced by the ROLE's configured model (the estimate's key); the
        # response reports a dated id the price table does not carry.
        price_in, price_out = llm.PRICES_USD_PER_MTOK[self._inner.roles[role]]
        in_tokens = (
            usage.input_tokens + usage.cache_creation_input_tokens + usage.cache_read_input_tokens
        )
        usd = (in_tokens / 1_000_000) * price_in + (usage.output_tokens / 1_000_000) * price_out
        if phase in llm.BATCH_PHASES:
            usd *= llm.BATCH_DISCOUNT
        self.usd += usd
        self.tokens["input"] += in_tokens
        self.tokens["output"] += usage.output_tokens
        per_phase = self.phase_tokens.setdefault(phase, {"in": 0, "out": 0})
        per_phase["in"] += in_tokens
        per_phase["out"] += usage.output_tokens

    def count_tokens(self, model_id: str, text: str) -> int:
        return self._inner.count_tokens(model_id, text)

    def estimate(self, units, plan, prices=None):
        if prices is None:
            return self._inner.estimate(units, plan)
        return self._inner.estimate(units, plan, prices=prices)

    def complete(self, role, prompt, schema, *, phase, max_tokens):
        self.calls[phase] = self.calls.get(phase, 0) + 1
        metered = False
        try:
            completion = self._inner.complete(
                role, prompt, schema, phase=phase, max_tokens=max_tokens
            )
            self._meter(phase, role, completion)
            metered = True
            return completion
        finally:
            if not metered:  # a call the client raised on was still billed
                self.unmetered[phase] = self.unmetered.get(phase, 0) + 1

    def complete_batch(self, role, prompts, schema, *, phase, max_tokens):
        self.calls[phase] = self.calls.get(phase, 0) + len(prompts)
        metered = False
        try:
            results = self._inner.complete_batch(
                role, prompts, schema, phase=phase, max_tokens=max_tokens
            )
            for completion in results.values():
                self._meter(phase, role, completion)
            metered = True
            return results
        finally:
            if not metered:  # a submitted batch the client raised on was still billed
                self.unmetered[phase] = self.unmetered.get(phase, 0) + len(prompts)


def _spend_lines(estimate: llm.CostEstimate, meter: _CountingClient) -> list[str]:
    """Projection, first-pass and cap-bound beside what the run actually made."""
    first_pass = _first_pass_usd(estimate)
    est_calls: dict[str, int] = {}
    for row in estimate.rows:
        est_calls[row.phase] = est_calls.get(row.phase, 0) + row.calls
    phases = sorted(set(est_calls) | set(meter.calls))
    ratio = f"{meter.usd / estimate.total_usd:.2f}" if estimate.total_usd else "n/a"
    def per_call(phase: str) -> str:
        made = meter.calls.get(phase, 0)
        toks = meter.phase_tokens.get(phase)
        if not made or toks is None:
            return ""
        return f" in/call={toks['in'] // made} out/call={toks['out'] // made}"

    return [
        f"spend: projected=${estimate.total_usd:.4f} first_pass=${first_pass:.4f} "
        f"expected=${run.expected_usd(estimate):.4f} "
        f"cap_bound=${run.cap_bound_usd(estimate):.4f} "
        f"actual=${meter.usd:.4f} actual/projected={ratio} "
        f"tokens_in={meter.tokens['input']} tokens_out={meter.tokens['output']}"
        + (
            f" unmetered_calls={sum(meter.unmetered.values())} (billed, usage unknown: "
            "actual is a LOWER BOUND)"
            if meter.unmetered
            else ""
        ),
        "calls: "
        + " | ".join(
            f"{p} est={est_calls.get(p, 0)} act={meter.calls.get(p, 0)}{per_call(p)}"
            for p in phases
        ),
    ]


class _PrintingStore(jobs.IngestJobStore):
    """The process-local store with two side effects per event: it prints
    the event as it lands (a long live job — batch rounds poll for minutes
    — shows its progress) and appends it to `{log_dir}/{job_id}.jsonl`
    THE MOMENT IT LANDS, so a SIGINT, crash or OOM during a paid run
    loses nothing already bought (the judge on the first paid job)."""

    def __init__(self, log_dir: Path) -> None:
        super().__init__()
        self.log_dir = Path(log_dir)

    def log_path(self, job_id: str) -> Path:
        return self.log_dir / f"{job_id}.jsonl"

    def append_event(self, job_id, kind, message, *, source=None, url=None, data=None):
        event = super().append_event(
            job_id, kind, message, source=source, url=url, data=data
        )
        self.log_dir.mkdir(parents=True, exist_ok=True)
        with self.log_path(job_id).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.model_dump(), ensure_ascii=False) + "\n")
        if kind == "phase":
            print(f"phase {message} done")
        elif kind == "error":
            print(f"ERROR: {message}")
        elif message != "cost_estimate":  # the estimate is printed in full above
            print(f"event {message}: {json.dumps(event.data, ensure_ascii=False)}")
        return event

    def job_dir(self, job_id: str) -> Path:
        return self.log_dir / job_id

    def set_phase(self, job_id: str, phase: str, output: dict) -> None:
        """The phase's output lands on disk BEFORE its `phase` event: a phase
        the log calls done is always resumable (slice 9 job 2 lost ten hours
        three times with its outputs only in this process)."""
        if phase not in jobs.PHASES:
            raise ValueError(f"unknown phase {phase!r}")
        _write_json_atomic(self.job_dir(job_id) / f"{phase}.json", output)
        super().set_phase(job_id, phase, output)

    def write_job_file(self, job: jobs.IngestJob, units, beats_path: Path) -> None:
        """What a resume needs to rebuild the job and to refuse a changed
        world: the job's own fields, each source chunk's text hash, and the
        beats file's hash when the job started."""
        _write_json_atomic(
            self.job_dir(job.id) / "job.json",
            {
                "id": job.id,
                "city": job.city,
                "source": job.source.model_dump(mode="json"),
                "as_of": job.as_of,
                "rights_basis": job.rights_basis,
                "created_at": job.created_at,
                "chunk_sha256": {u.chunk: _sha256(u.text) for u in units},
                "beats_sha256": _sha256(Path(beats_path).read_bytes()),
            },
        )

    def restore(self, job_id: str) -> tuple[jobs.IngestJob, dict]:
        """Rebuild a job from its directory: fields from job.json, every phase
        file present, and the events already in its log (so seq numbering and
        the summary's counters continue)."""
        job_dir = self.job_dir(job_id)
        meta = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
        job = jobs.IngestJob(
            id=meta["id"], city=meta["city"], source=meta["source"], as_of=meta["as_of"],
            rights_basis=meta["rights_basis"], created_at=meta.get("created_at"),
        )
        for phase in jobs.PHASES:
            phase_file = job_dir / f"{phase}.json"
            if phase_file.is_file():
                job.phases[phase] = json.loads(phase_file.read_text(encoding="utf-8"))
        log = self.log_path(job_id)
        if log.is_file():
            job.events = [
                jobs.OnboardEvent.model_validate(json.loads(line))
                for line in log.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        with self._lock:
            self._jobs[job.id] = job
        return job, meta

    def held_path(self, job_id: str) -> Path:
        return self.log_dir.parent / "held" / f"{job_id}.jsonl"

    def queue(self, **kwargs):
        """The review queue is process-local; slice 9 job 1 run 4 lost four
        held stories when the CLI exited. Each NEW item is appended, whole,
        to `held_path` the moment it is queued (identical content is one
        item by hash and writes nothing more)."""
        item_id = jobs.shown_hash(kwargs["shown"])
        is_new = self.item(item_id) is None
        item = super().queue(**kwargs)
        if is_new:
            path = self.held_path(item.job_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(item.model_dump(mode="json"), ensure_ascii=False) + "\n")
            print(f"queued {item.kind} {item.story_slug} -> {path}")
        return item


#: The rule a left-out story's last hold names, from its reason's first word.
_RULE_OF_REASON: tuple[tuple[str, str], ...] = (
    ("lift", "lift"),
    ("NARRATION_LIFT", "lift"),
    ("not_entailed", "narration_check"),
    ("ARC_TOO_FEW_CLAIMS", "arc_minimum"),
    ("judge and signature disagree", "merge_hold"),
    ("transport", "transport"),
    ("schema", "transport"),
    ("provenance_leak", "book_mention"),
)


def _rule(reason: str) -> str:
    text = re.sub(r"^(?:P\d\s+|gate:\s*)", "", reason.strip())
    for prefix, rule in _RULE_OF_REASON:
        if text.startswith(prefix):
            return rule
    return text.split(":", 1)[0].split(" ", 1)[0] or "unknown"


def story_fates(snap: jobs.IngestJob, events: list[tuple[str, dict]]) -> list[str]:
    """What became of every story P2 produced (owner, 2026-10-01: rules must not kill good
    stories unseen): written (and how many of those are held), folded into a beat the
    corpus already had, or left out — each left-out story with the rule its last hold
    names. Written = a beat the job made new (P6 `new`) that is in the final file. Each
    produced story claims ONE such beat id — its own `city/place/slug`, else the
    `-2`, `-3` ... the job gives a second same-slug story at a place (`run._free_slug`) — so
    two stories never share a fate, and the fates always add up to `produced`."""
    # P3's story list is the final one (an omission re-ask adds stories P2 never had).
    source = snap.phases.get("P3") if (snap.phases.get("P3") or {}).get("stories") else None
    source = source or snap.phases.get("P2") or {}
    stories = [s for listed in (source.get("stories") or {}).values() for s in listed]
    if not stories:
        return []
    # A story P3 left with no surviving claim never reaches P4: its claims were refused,
    # possibly in an earlier run of the job (a replay resumes after P3, so no event).
    no_claims = {
        slug
        for unit in ((snap.phases.get("P3") or {}).get("judged") or {}).values()
        for slug, judged in unit.items()
        if not judged
    }
    beats_path = (snap.phases.get("P7") or {}).get("beats_path")
    records = (
        json.loads(Path(beats_path).read_text(encoding="utf-8"))
        if beats_path and Path(beats_path).is_file()
        else []
    )
    by_id = {r.get("beat_id"): r for r in records}
    available = {
        bid for bid in (snap.phases.get("P6") or {}).get("new") or [] if bid in by_id
    }

    def beat_id(story: dict, n: int) -> str:
        suffix = "" if n == 1 else f"-{n}"
        return f"{snap.city}/{model.slug(story.get('place', ''))}/{story.get('story_slug')}{suffix}"

    # Exact ids first, so a story whose own slug ends in "-2" keeps its beat.
    taken: dict[int, str] = {}
    for index, story in enumerate(stories):
        if beat_id(story, 1) in available:
            taken[index] = beat_id(story, 1)
            available.discard(taken[index])
    for index, story in enumerate(stories):
        if index in taken:
            continue
        for n in range(2, 100):
            if beat_id(story, n) in available:
                taken[index] = beat_id(story, n)
                available.discard(taken[index])
                break
    written = {
        index: bool((by_id[bid].get("review") or {}).get("held")) for index, bid in taken.items()
    }
    absorbed = {d.get("story_slug") for m, d in events if m == "merge_absorbed"}
    last_reason: dict[str, str] = {}
    for message, data in events:
        if message == "beat_held":
            last_reason[data.get("story_slug", "")] = str(data.get("reason", ""))
        elif message == "commit_held":
            slug = str(data.get("beat_id", "")).rsplit("/", 1)[-1]
            last_reason[slug] = "; ".join(data.get("errors") or [])
        elif message == "hold_default" and data.get("applied") == "left_out":
            last_reason.setdefault(data.get("story_slug", ""), str(data.get("why", "")))
    left_out: list[tuple[str, str, str]] = []
    folded = 0
    for index, story in enumerate(stories):
        slug = story.get("story_slug", "")
        if index in written:
            continue
        if slug in absorbed:
            folded += 1
            continue
        if slug in no_claims and slug not in last_reason:
            left_out.append(
                ("claim_check", slug, "every claim refused at the claim check (P3)")
            )
            continue
        reason = last_reason.get(slug, "")
        left_out.append((_rule(reason) if reason else "unknown", slug, reason))
    by_rule = Counter(rule for rule, _slug, _reason in left_out)
    held = sum(written.values())
    accounted = len(written) + folded + len(left_out)
    return [
        *(
            [f"stories: MISMATCH produced={len(stories)} accounted={accounted}"]
            if accounted != len(stories)
            else []
        ),
        *(
            [f"stories: {len(available)} new beat(s) matched no produced story"]
            if available
            else []
        ),
        f"stories: produced={len(stories)} written={len(written)} (held={held}) "
        f"folded={folded} left_out={len(left_out)} by_rule: "
        + (" ".join(f"{rule}={n}" for rule, n in sorted(by_rule.items())) or "none"),
        *(f"  left out [{rule}] {slug}: {reason}" for rule, slug, reason in left_out),
    ]


def summary(snap: jobs.IngestJob, log_path: Path) -> str:
    """The slice-9 measurements, read off the job log: the P6 hold rate
    with every `beat_held` reason, the stories P3 held on their own
    (`story_held` — an isolated transport failure inside the unit round,
    which does not stop the job), and the P3 precision counters."""
    events = [(e.message, e.data) for e in snap.events if e.kind == "info"]
    p6 = snap.phases.get("P6") or {}
    held = [d for m, d in events if m == "beat_held"]
    story_held = [d for m, d in events if m == "story_held"]
    held_p6 = [d for d in held if d.get("phase") == "P6"]
    skipped = sum(1 for m, _d in events if m == "merge_skipped")
    decided = sum(1 for m, _d in events if m == "merge_decided")
    stories = len(p6.get("new", [])) + len(p6.get("applied", [])) + len(held_p6)
    # A story is judged only when a merge decided or held it; a story at a
    # NEW place skips the merge without a merge_skipped event (job 1 printed
    # merge_judged=12 on a run whose P6 made zero calls).
    judged = decided + len(held_p6)
    new_place = max(0, stories - skipped - judged)
    rate = f"{len(held_p6) / judged:.2f}" if judged > 0 else "n/a"
    refused1 = [d for m, d in events if m == "claim_refused" and d.get("attempt") == 1]
    dropped = [d for m, d in events if m == "claim_dropped"]
    leak_drops = [d for d in dropped if str(d.get("reason", "")).startswith("leak")]
    written = (snap.phases.get("P7") or {}).get("written", 0)
    lines = [
        f"summary: status={snap.status} beats_written={written} job_log={log_path}",
        f"p6: stories={stories} merge_judged={judged} held={len(held_p6)} "
        f"skipped_no_beat={skipped} new_place={new_place} hold_rate={rate}",
        "holds (beat_held): " + ("none" if not held else ""),
        *(f"  [{d.get('phase')}] {d.get('story_slug')}: {d.get('reason')}" for d in held),
        # A story whose P3 round lost one request is held on its own, and the
        # job still commits every other story — so without this line a missing
        # story leaves no trace in the summary a reader actually reads.
        f"stories_held (P3)={len(story_held)}",
        *(f"  {d.get('story_slug')}: {d.get('reason')}" for d in story_held),
        f"claims: refused_attempt1={len(refused1)} dropped={len(dropped)} "
        f"leak_gate_drops={len(leak_drops)}",
        *(f"  refused: {d.get('claim_id')}: {d.get('reason')}" for d in refused1),
        *(f"  dropped: {d.get('claim_id')}: {d.get('reason')}" for d in dropped),
        *story_fates(snap, events),
    ]
    if snap.error:
        lines.append(f"error: {snap.error}")
    return "\n".join(lines)


def _as_of(value: str) -> int | str:
    return int(value) if value.isdigit() else value


def _resume_refusal(store: _PrintingStore, args, beats_path: Path) -> str | None:
    """Why `--resume` must not run, or None. Checked before any call."""
    job_dir = store.job_dir(args.resume)
    if not (job_dir / "job.json").is_file():
        return f"unknown job {args.resume!r} (no {job_dir / 'job.json'})"
    meta = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    book_log = beats_path.parent / "book-log.json"
    logged = []
    if book_log.is_file():
        logged = json.loads(book_log.read_text(encoding="utf-8")).get("books_processed", [])
    if (job_dir / "P7.json").is_file() or any(b.get("job_id") == args.resume for b in logged):
        return "already committed (P7 ran: a resume would commit twice)"
    present = [phase for phase in jobs.PHASES if (job_dir / f"{phase}.json").is_file()]
    if present != list(jobs.PHASES[: len(present)]):
        return f"gap in the phase files: {present}"
    source = meta["source"]
    if args.chunk_dir is not None and str(args.chunk_dir) != source.get("chunk_dir"):
        return f"--chunk-dir {args.chunk_dir} conflicts with the job's {source.get('chunk_dir')}"
    if args.chunk is not None and args.chunk != source.get("chunks"):
        return f"--chunk {args.chunk} conflicts with the job's {source.get('chunks')}"
    if args.as_of is not None and _as_of(args.as_of) != meta["as_of"]:
        return f"--as-of {args.as_of} conflicts with the job's {meta['as_of']}"
    if _sha256(beats_path.read_bytes()) != meta["beats_sha256"]:
        return "beats.json changed since the job started (a saved P6 holds the whole file)"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--city", required=True)
    parser.add_argument("--chunk-dir", type=Path, default=None,
                        help="required for a new job; taken from the job on --resume")
    parser.add_argument(
        "--chunk", action="append", default=None,
        help="a chunk stem from the manifest; repeatable; absent = the whole book",
    )
    parser.add_argument("--as-of", default=None,
                        help="the source's as-of year (or ISO date); required for a new job")
    parser.add_argument("--rights-basis", default="owned_copy")
    parser.add_argument("--data-root", type=Path, default=None,
                        help="defaults to INGEST_DATA_ROOT, then the repo's data-ingest/")
    parser.add_argument("--repo-data", type=Path, default=ROOT / "data")
    parser.add_argument("--resume", default=None, metavar="JOB_ID",
                        help="continue a job from its phase files instead of starting one")
    parser.add_argument("--yes", action="store_true", help="proceed past the estimate and spend")
    parser.add_argument("--summary-of", default=None, metavar="JOB_ID",
                        help="print a finished job's summary from its directory ($0, writes "
                        "nothing) and exit")
    parser.add_argument("--stop-after", choices=sorted(run.STOP_AFTER_ROWS), default=None,
                        help="end the job after this phase: nothing written, its output kept")
    args = parser.parse_args(argv)
    data_root = args.data_root or Path(os.environ.get("INGEST_DATA_ROOT") or ROOT / "data-ingest")
    if args.summary_of is not None:
        # Read-only: no ensure_root, no client. A past job is read the way the next one is.
        jobs_dir = data_root / args.city / "jobs"
        if not (jobs_dir / args.summary_of / "job.json").is_file():
            print(f"unknown job {args.summary_of!r} under {jobs_dir}")
            return 2
        reader = _PrintingStore(jobs_dir)
        job, _meta = reader.restore(args.summary_of)
        print(
            f"read from disk: phases {' '.join(p for p in jobs.PHASES if p in job.phases)} "
            "(a job's status is not stored; a P7 phase means it committed)"
        )
        print(summary(reader.get(args.summary_of), reader.log_path(args.summary_of)))
        return 0
    if args.resume is None and (args.chunk_dir is None or args.as_of is None):
        parser.error("a new job needs --chunk-dir and --as-of")

    paths = ensure_root(data_root, args.repo_data, args.city)
    print(f"data root: {data_root} (beats: {paths['beats']})")

    store = _PrintingStore(data_root / args.city / "jobs")
    holder: dict[str, str] = {}
    resumed_phases: list[str] = []
    if args.resume is not None:
        refusal = _resume_refusal(store, args, paths["beats"])
        if refusal is not None:
            print(f"resume refused: {refusal}")
            return 2
        job, meta = store.restore(args.resume)
        resumed_phases = [phase for phase in jobs.PHASES if phase in job.phases]
    else:
        job = store.create(
            city=args.city,
            source={"kind": "book", "chunk_dir": str(args.chunk_dir), "chunks": args.chunk},
            as_of=_as_of(args.as_of),
            rights_basis=args.rights_basis,
        )
        meta = None
    client = _CountingClient(run.client_from_env(client_event_sink(store, holder)))
    holder["job_id"] = job.id

    try:
        units, _manifest = run.intake(job)
        run.load_beats(paths["beats"])  # P0's shape gate, before any token is counted
    except run.JobRefused as refused:
        print(f"job refused: {refused}")
        return 2
    if meta is not None:
        changed = [u.chunk for u in units if meta["chunk_sha256"].get(u.chunk) != _sha256(u.text)]
        if changed:
            print(f"resume refused: source chunk text changed since the job started: {changed}")
            return 2
    chunk_dir_name = Path(job.source.chunk_dir or "").name
    print(f"job {job.id}: {args.city} <- {chunk_dir_name} "
          f"[{', '.join(u.chunk for u in units)}] as_of={job.as_of} rights={job.rights_basis}")
    estimate = run.estimate_job(client, units)
    _print_estimate(estimate)
    if args.stop_after is not None:
        stage = run.stage_spend(estimate, args.stop_after)
        print(
            f"stop-after {args.stop_after}: projected=${stage['projected']:.4f} "
            f"first_pass=${stage['first_pass']:.4f} expected=${stage['expected']:.4f} "
            f"cap_bound=${stage['cap_bound']:.4f} (only the rows this run can reach)"
        )
    if not args.yes:
        print("run refused: re-run with ARGS=\"... --yes\" to spend this.")
        return 2

    if args.resume is not None:
        store.append_event(job.id, "info", "resumed", data={"phases": resumed_phases})
    else:
        store.write_job_file(job, units, paths["beats"])
    run.run_job(job.id, store, client, data_root=data_root, stop_after=args.stop_after)
    finished = store.get(job.id)
    if finished is None:  # the store never forgets a job it created
        raise KeyError(job.id)
    log_path = store.log_path(job.id)
    print(summary(finished, log_path))
    print("\n".join(_spend_lines(estimate, client)))
    done = "stopped" if args.stop_after is not None else "committed"
    return 0 if finished.status == done else 1


if __name__ == "__main__":
    sys.exit(main())
