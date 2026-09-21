"""Re-run P3's two judge looks on a finished job's labeled claims (spends).

    make ingest-rejudge ARGS="--job-dir <jobs/id> --labels-dir <dir> --events <jobs/id.jsonl> \\
        --samples 3 --out data-ingest/sandboxes/<new dir> [--accepted] [--yes]"

Prints the estimate and stops unless --yes. The live client is metered (the
same meter `make ingest-job` uses) so the run prints its actual spend, and
its events — every batch id — land in <out>/events.jsonl. See
src/ingest/rejudge.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.ingest_job import _CountingClient
from src.ingest import rejudge, run

if __name__ == "__main__":
    events = rejudge.EventLog()
    client = _CountingClient(
        run.client_from_env(lambda kind, payload: events.append({"event": kind, "data": payload}))
    )
    sys.exit(rejudge.main(client=client, events=events))
