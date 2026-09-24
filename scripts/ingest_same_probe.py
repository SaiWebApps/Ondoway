"""The `same` second-look probe: every labeled pair in
fixtures/ingestion/same_second_look.json asked K times of the claim judge
(Haiku) in one batch round, scored against the bars registered in
Docs/ingestion/rebuild-spec.md ("`same` second-look probe").

    make ingest-same-probe ARGS="--samples 3 --out <dir>"        # prints the estimate, stops
    make ingest-same-probe ARGS="--samples 3 --out <dir> --yes"  # spends
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.ingest_job import _CountingClient
from src.ingest import llm, same_probe
from src.ingest.rejudge import EventLog

FIXTURE = ROOT / "fixtures" / "ingestion" / "same_second_look.json"


def main(argv: Sequence[str] | None = None, client: llm.ModelClient | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--yes", action="store_true", help="proceed past the estimate and spend")
    args = parser.parse_args(argv)

    pairs = same_probe.load_pairs(args.fixture)
    inputs = {
        "fixture_sha256": hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
        "prompt_sha256": hashlib.sha256(same_probe.SAME_CHECK_PROMPT.encode("utf-8")).hexdigest(),
        "samples": args.samples,
        "model": llm.ROLE_MODEL[same_probe.ROLE],
        "head": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip(),
    }
    print(f"inputs: {json.dumps(inputs)}")
    events = EventLog()
    if client is None:
        from src.ingest.run import client_from_env

        client = client_from_env(
            lambda kind, payload: events.append({"event": kind, "data": payload})
        )
    meter = _CountingClient(client)
    texts = [same_probe.render(p) for p in pairs for _ in range(args.samples)]
    estimate = meter.estimate(texts, list(same_probe.PLAN))
    print(
        f"estimate: ${estimate.total_usd:.4f} ({len(texts)} asks: {len(pairs)} pairs x "
        f"{args.samples} samples, {llm.ROLE_MODEL[same_probe.ROLE]}, one batch round)"
    )
    if not args.yes:
        print("run refused: re-run with --yes to spend this.")
        return 2
    args.out.mkdir(parents=True, exist_ok=False)
    events.attach(args.out / "events.jsonl")
    try:
        report = same_probe.run(pairs, samples=args.samples, client=meter, arm=False)
    finally:
        unmetered = sum(meter.unmetered.values())
        print(
            f"spend: actual=${meter.usd:.4f} (estimate ${estimate.total_usd:.4f})"
            + (f"; {unmetered} call(s) unmetered: actual is a LOWER BOUND" if unmetered else "")
        )
    scored = same_probe.score(report, pairs)
    (args.out / "same-probe-report.json").write_text(
        json.dumps(
            {"inputs": inputs, "samples": report, "score": scored}, indent=1, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    for row in report:
        print(json.dumps(row, ensure_ascii=False))
    print(json.dumps(scored))
    return 0


if __name__ == "__main__":
    sys.exit(main())
