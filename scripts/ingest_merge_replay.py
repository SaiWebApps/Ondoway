"""Prepare a sandbox to replay a finished job's P6 under the current merge code.

    make ingest-merge-replay CITY=new_york JOB=<job_id> [PLACE="Solomon R. Guggenheim Museum"]

$0 — no model call. Slice 10 (Docs/ingestion/rebuild-spec.md §8) rebuilt
P6's matching; proving it on a real job must not repay P1-P5 (a Frommer's
chunk cost $3.91) and must not touch the city's file. So this builds a
sandbox data root holding exactly what the job saw when P6 began:

- `{out}/{city}/beats.json` — the file the job STARTED from: the city's
  current file minus the job's own new records (its P6.json `new`),
  written the way `scripts/beats_io.py` writes it. Refused unless its sha
  is the `beats_sha256` the job recorded — a job that changed existing
  beats, or a file edited since, cannot be rebuilt, and a replay against the
  wrong corpus proves nothing.
- `{out}/{city}/poi-raw.json`, and the job's `job.json` and P0-P5 phase
  files (never P6/P7), with `--place` keeping only that place's stories.

It then prints the ordinary resume that runs P6 onward (and P7) in the
sandbox — the live spend, which needs the owner's go:

    make ingest-job CITY=... CHUNK_DIR=<the job's> ARGS="--resume <id> --data-root <out> --yes"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.beats_io import _write_json  # noqa: E402 — the one serializer P7 writes with
from src.ingest import model  # noqa: E402

#: The phases whose saved output a replay keeps: everything before P6.
KEPT_PHASES: tuple[str, ...] = ("P0", "P1", "P2", "P3", "P4", "P5")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _only_place(stories: dict[str, list[dict]], place: str) -> dict[str, list[dict]]:
    return {unit: [s for s in listed if s["place"] == place] for unit, listed in stories.items()}


def prepare(city: str, job_id: str, source_root: Path, out: Path, place: str | None) -> str:
    """Build the sandbox; returns the resume command. Raises ValueError
    (nothing created) when the job's starting file cannot be rebuilt."""
    city_dir = source_root / city
    job_dir = city_dir / "jobs" / job_id
    meta = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    p6 = job_dir / "P6.json"
    own = set(json.loads(p6.read_text(encoding="utf-8"))["new"]) if p6.is_file() else set()
    records = json.loads((city_dir / "beats.json").read_text(encoding="utf-8"))
    started = [r for r in records if r["beat_id"] not in own]
    if out.exists():
        raise ValueError(f"{out} already exists; a replay never writes over another")

    staging = out.with_name(out.name + ".staging")
    shutil.rmtree(staging, ignore_errors=True)
    sandbox_city = staging / city
    (sandbox_city / "jobs" / job_id).mkdir(parents=True)
    _write_json(started, sandbox_city / "beats.json")
    if _sha256(sandbox_city / "beats.json") != meta["beats_sha256"]:
        shutil.rmtree(staging)
        raise ValueError(
            f"{city_dir / 'beats.json'} minus the job's {len(own)} new record(s) is not the "
            f"file the job started from (sha {meta['beats_sha256'][:16]}): it changed "
            "some other way since, or the job changed existing beats"
        )
    shutil.copyfile(city_dir / "poi-raw.json", sandbox_city / "poi-raw.json")
    shutil.copyfile(job_dir / "job.json", sandbox_city / "jobs" / job_id / "job.json")
    for phase in KEPT_PHASES:
        output = json.loads((job_dir / f"{phase}.json").read_text(encoding="utf-8"))
        if place is not None and phase in ("P2", "P3"):
            output["stories"] = _only_place(output["stories"], place)
        (sandbox_city / "jobs" / job_id / f"{phase}.json").write_text(
            json.dumps(output, ensure_ascii=False), encoding="utf-8"
        )
    staging.rename(out)
    chunk_dir = meta["source"]["chunk_dir"]
    return (
        f'make ingest-job CITY={city} CHUNK_DIR={chunk_dir} '
        f'ARGS="--resume {job_id} --data-root {out} --yes"'
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--city", required=True)
    parser.add_argument("--job", required=True, metavar="JOB_ID")
    parser.add_argument("--source-root", type=Path, default=ROOT / "data-ingest")
    parser.add_argument("--out", type=Path, default=None,
                        help="defaults to {source-root}/{city}/replays/{job}-{place or all}")
    parser.add_argument("--place", default=None, help="keep only this place's stories")
    args = parser.parse_args(argv)
    out = args.out or (
        args.source_root / args.city / "replays"
        / f"{args.job}-{model.slug(args.place) if args.place else 'all'}"
    )
    try:
        command = prepare(args.city, args.job, args.source_root, out, args.place)
    except ValueError as refused:
        print(f"replay refused: {refused}")
        return 2
    print(f"sandbox: {out}")
    print(f"replay (spends; owner go first): {command}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
