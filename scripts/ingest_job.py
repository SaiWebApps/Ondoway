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
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.ingest import jobs, llm, run


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


def _print_event(kind: str, payload: dict) -> None:
    print(f"event {kind}: {json.dumps(payload, ensure_ascii=False)}")


def _first_pass_usd(estimate: llm.CostEstimate) -> float:
    """The rows that are each phase's FIRST ask: the expected spend when
    nothing is refused. The whole plan is the ceiling."""
    return sum(row.usd for row, first in zip(estimate.rows, run.FIRST_PASS, strict=True) if first)


def _print_estimate(estimate: llm.CostEstimate) -> None:
    print(f"estimated spend: ${estimate.total_usd:.4f} (a ceiling; prices of "
          f"{estimate.prices_cached_on}; {estimate.units} unit(s))")
    print(f"first-pass (no re-asks): ${_first_pass_usd(estimate):.4f}")
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

    def count_tokens(self, model_id: str, text: str) -> int:
        return self._inner.count_tokens(model_id, text)

    def estimate(self, units, plan, prices=None):
        if prices is None:
            return self._inner.estimate(units, plan)
        return self._inner.estimate(units, plan, prices=prices)

    def complete(self, role, prompt, schema, *, phase, max_tokens):
        completion = self._inner.complete(role, prompt, schema, phase=phase, max_tokens=max_tokens)
        self.calls[phase] = self.calls.get(phase, 0) + 1
        self._meter(phase, role, completion)
        return completion

    def complete_batch(self, role, prompts, schema, *, phase, max_tokens):
        results = self._inner.complete_batch(
            role, prompts, schema, phase=phase, max_tokens=max_tokens
        )
        self.calls[phase] = self.calls.get(phase, 0) + len(prompts)
        for completion in results.values():
            self._meter(phase, role, completion)
        return results


def _spend_lines(estimate: llm.CostEstimate, meter: _CountingClient) -> list[str]:
    """Estimate (ceiling and first-pass) beside what the run actually made."""
    first_pass = _first_pass_usd(estimate)
    est_calls: dict[str, int] = {}
    for row in estimate.rows:
        est_calls[row.phase] = est_calls.get(row.phase, 0) + row.calls
    phases = sorted(set(est_calls) | set(meter.calls))
    ratio = f"{meter.usd / estimate.total_usd:.2f}" if estimate.total_usd else "n/a"
    return [
        f"spend: estimated=${estimate.total_usd:.4f} (ceiling) first_pass=${first_pass:.4f} "
        f"actual=${meter.usd:.4f} actual/ceiling={ratio} "
        f"tokens_in={meter.tokens['input']} tokens_out={meter.tokens['output']}",
        "calls: "
        + " | ".join(
            f"{p} est={est_calls.get(p, 0)} act={meter.calls.get(p, 0)}" for p in phases
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


def summary(snap: jobs.IngestJob, log_path: Path) -> str:
    """The slice-9 measurements, read off the job log: the P6 hold rate
    with every `beat_held` reason, and the P3 precision counters."""
    events = [(e.message, e.data) for e in snap.events if e.kind == "info"]
    p6 = snap.phases.get("P6") or {}
    held = [d for m, d in events if m == "beat_held"]
    held_p6 = [d for d in held if d.get("phase") == "P6"]
    skipped = sum(1 for m, _d in events if m == "merge_skipped")
    stories = len(p6.get("new", [])) + len(p6.get("applied", [])) + len(held_p6)
    judged = stories - skipped
    rate = f"{len(held_p6) / judged:.2f}" if judged > 0 else "n/a"
    refused1 = [d for m, d in events if m == "claim_refused" and d.get("attempt") == 1]
    dropped = [d for m, d in events if m == "claim_dropped"]
    leak_drops = [d for d in dropped if str(d.get("reason", "")).startswith("leak")]
    written = (snap.phases.get("P7") or {}).get("written", 0)
    lines = [
        f"summary: status={snap.status} beats_written={written} job_log={log_path}",
        f"p6: stories={stories} merge_judged={judged} held={len(held_p6)} "
        f"skipped_no_beat={skipped} hold_rate={rate}",
        "holds (beat_held): " + ("none" if not held else ""),
        *(f"  [{d.get('phase')}] {d.get('story_slug')}: {d.get('reason')}" for d in held),
        f"claims: refused_attempt1={len(refused1)} dropped={len(dropped)} "
        f"leak_gate_drops={len(leak_drops)}",
        *(f"  refused: {d.get('claim_id')}: {d.get('reason')}" for d in refused1),
        *(f"  dropped: {d.get('claim_id')}: {d.get('reason')}" for d in dropped),
    ]
    if snap.error:
        lines.append(f"error: {snap.error}")
    return "\n".join(lines)


def _as_of(value: str) -> int | str:
    return int(value) if value.isdigit() else value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--city", required=True)
    parser.add_argument("--chunk-dir", required=True, type=Path)
    parser.add_argument(
        "--chunk", action="append", default=None,
        help="a chunk stem from the manifest; repeatable; absent = the whole book",
    )
    parser.add_argument("--as-of", required=True, help="the source's as-of year (or ISO date)")
    parser.add_argument("--rights-basis", default="owned_copy")
    parser.add_argument("--data-root", type=Path, default=None,
                        help="defaults to INGEST_DATA_ROOT, then the repo's data-ingest/")
    parser.add_argument("--repo-data", type=Path, default=ROOT / "data")
    parser.add_argument("--yes", action="store_true", help="proceed past the estimate and spend")
    args = parser.parse_args(argv)

    data_root = args.data_root or Path(os.environ.get("INGEST_DATA_ROOT") or ROOT / "data-ingest")
    paths = ensure_root(data_root, args.repo_data, args.city)
    print(f"data root: {data_root} (beats: {paths['beats']})")

    store = _PrintingStore(data_root / args.city / "jobs")
    job = store.create(
        city=args.city,
        source={"kind": "book", "chunk_dir": str(args.chunk_dir), "chunks": args.chunk},
        as_of=_as_of(args.as_of),
        rights_basis=args.rights_basis,
    )
    client = _CountingClient(run.client_from_env(_print_event))

    try:
        units, _manifest = run.intake(job)
        run.load_beats(paths["beats"])  # P0's shape gate, before any token is counted
    except run.JobRefused as refused:
        print(f"job refused: {refused}")
        return 2
    print(f"job {job.id}: {args.city} <- {args.chunk_dir.name} "
          f"[{', '.join(u.chunk for u in units)}] as_of={job.as_of} rights={job.rights_basis}")
    estimate = run.estimate_job(client, units)
    _print_estimate(estimate)
    if not args.yes:
        print("run refused: re-run with ARGS=\"... --yes\" to spend this.")
        return 2

    run.run_job(job.id, store, client, data_root=data_root)
    finished = store.get(job.id)
    if finished is None:  # the store never forgets a job it created
        raise KeyError(job.id)
    log_path = store.log_path(job.id)
    print(summary(finished, log_path))
    print("\n".join(_spend_lines(estimate, client)))
    return 0 if finished.status == "committed" else 1


if __name__ == "__main__":
    sys.exit(main())
