"""Sample P1's first ask K times on a finished job's unit (spends).

    make ingest-p1probe ARGS="--job-dir <jobs/id> --samples 10 \
        --out data-ingest/sandboxes/<new> [--yes]"

Prints the estimate and stops unless --yes; metered like `make ingest-job`, events (batch ids) to
<out>/events.jsonl as they arrive. See src/ingest/p1probe.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.ingest_job import _CountingClient
from src.ingest import p1probe, rejudge, run

if __name__ == "__main__":
    events = rejudge.EventLog()
    client = _CountingClient(
        run.client_from_env(lambda kind, payload: events.append({"event": kind, "data": payload}))
    )
    sys.exit(p1probe.main(client=client, events=events))
