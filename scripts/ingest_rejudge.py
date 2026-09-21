"""Re-run P3's two judge looks on a finished job's labeled claims (spends).

    make ingest-rejudge ARGS="--job-dir <jobs/id> --labels-dir <dir> --events <jobs/id.jsonl> \\
        --samples 3 --out data-ingest/sandboxes/<new dir> [--accepted] [--yes]"

Prints the estimate and stops unless --yes. See src/ingest/rejudge.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.ingest import rejudge

if __name__ == "__main__":
    sys.exit(rejudge.main())
