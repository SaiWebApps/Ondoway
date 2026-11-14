"""Deploy a city's repo data to the active Neo4j graph, then verify parity.

The repo files are the single source of truth; this is the ONE 'repo -> graph'
path, so no graph is ever hand-maintained. It is:
  - additive + idempotent (all MERGE-based), and sends only what differs,
  - audio-safe (beat.audio_url is stamped ON CREATE only — a re-deploy never
    wipes generated TTS), and
  - self-verifying (fails non-zero if parity drifts afterward).

Steps:
  1. POIs + beats   <- scripts.upload_paris   (bbox-guarded MERGE)
  2. Areas + WITHIN <- scripts.upload_areas   (skipped for a city with no areas)
  3. Parity         <- scripts.db_parity      (repo vs graph; non-zero on drift)

Every step prints as it runs, then its elapsed seconds; the total closes the run.

Target selection is owned by Make:
  make deploy CITY=x
  make deploy CITY=x TARGET=cloud CONFIRM_CLOUD_WRITE=1
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.city_registry import onboard_data_root


def child_env() -> dict[str, str]:
    """The environment of every step: this one, with Python output unbuffered so
    each step's lines reach the terminal as they are printed."""
    return {**os.environ, "PYTHONUNBUFFERED": "1"}


def _run(cmd: list[str]) -> None:
    print(f"\n$ {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, check=True, cwd=str(ROOT), env=child_env())


def _areas_for(slug: str) -> list:
    """The city's Area records (``data/{slug}/areas.json`` under the hermetic-aware
    data root), or ``[]`` when the file is absent, empty, or not a JSON list.
    A fresh onboarded city writes ``areas.json == []`` — no areas to deploy."""
    path = onboard_data_root() / slug / "areas.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def _upload_areas_step(slug: str, py: str, allow_cloud: bool) -> None:
    """Step 2: Areas + WITHIN edges, skipped when the city has no areas."""
    if not _areas_for(slug):
        print(f"  [2/3] {slug}: no areas (areas.json empty/absent) — skipping areas upload")
        return
    cmd = [py, "scripts/upload_areas.py", "--slug", slug]
    if allow_cloud:
        cmd.append("--allow-cloud")
    _run(cmd)


def _timed(label: str, step, timings: list[tuple[str, float]]) -> None:
    t0 = time.time()
    step()
    seconds = time.time() - t0
    timings.append((label, seconds))
    print(f"  -- {label}: {seconds:.1f}s", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slug", required=True, help="city slug, e.g. new_york")
    ap.add_argument("--allow-cloud", action="store_true", help="permit a non-local target")
    args = ap.parse_args()
    py = sys.executable
    started = time.time()
    timings: list[tuple[str, float]] = []

    print(
        f"\n{'='*66}\n  DEPLOY {args.slug} -> active graph (repo = source of truth)\n{'='*66}",
        flush=True,
    )

    # 1. POIs + beats (upload_paris enforces the localhost/--allow-cloud guard itself).
    upload = [py, "-m", "scripts.upload_paris", args.slug]
    if args.allow_cloud:
        upload.append("--allow-cloud")
    _timed("step 1 POIs + beats", lambda: _run(upload), timings)

    # 2. Areas + WITHIN edges (upload_areas enforces the same guard).
    _timed(
        "step 2 areas",
        lambda: _upload_areas_step(args.slug, py, args.allow_cloud),
        timings,
    )

    # 3. Verify parity — the deploy is not 'done' until the graph matches the repo.
    _timed("step 3 parity", lambda: _run([py, "-m", "scripts.db_parity", args.slug]), timings)

    bar = "=" * 66
    print(f"\n{bar}\n  ✓ DEPLOY COMPLETE — {args.slug} matches repo (parity clean)")
    for label, seconds in timings:
        print(f"    {label:<22} {seconds:6.1f}s")
    print(f"    {'total':<22} {time.time() - started:6.1f}s\n{bar}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
