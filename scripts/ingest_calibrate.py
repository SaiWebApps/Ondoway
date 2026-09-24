"""Calibration by defect injection — `make ingest-calibrate` (spec §4).

Runs the judge phases that exist so far over `fixtures/ingestion/defects.json`
and prints caught/missed per defect class, then compares each class against
the committed baseline for the chosen client: any class below it exits 1.

    make ingest-calibrate                          # mock client: $0, harness check
    make ingest-calibrate-live                     # prints the estimate, then STOPS
    make ingest-calibrate-live ARGS=--yes          # spends
    make ingest-calibrate-live ARGS="--yes --update-baseline"

Under mock the judged classes are scripted from the fixture (see
src/ingest/calibrate.py); only a live run measures the judge.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.ingest_job import _CountingClient
from src.ingest import calibrate, llm

FIXTURE = ROOT / "fixtures" / "ingestion" / "defects.json"
BASELINE = ROOT / "fixtures" / "ingestion" / "calibration-baseline.json"


def _print_event(kind: str, payload: dict) -> None:
    if kind == "cost_estimate":
        print(f"cost_estimate: {json.dumps(payload)}")
    else:
        print(f"event {kind}: {json.dumps(payload, ensure_ascii=False)}")


def _spend_line(meter: _CountingClient) -> str:
    """What the run actually spent: the usage every completion reported,
    priced as the job runner prices it. A call the client raised on was
    billed but reported no usage, so it makes the figure a lower bound."""
    line = (
        f"spend: actual=${meter.usd:.4f} tokens_in={meter.tokens['input']} "
        f"tokens_out={meter.tokens['output']} calls: "
        + " ".join(f"{phase}={count}" for phase, count in sorted(meter.calls.items()))
    )
    if meter.unmetered:
        line += (
            f" unmetered_calls={sum(meter.unmetered.values())} (billed, usage unknown: "
            "actual is a LOWER BOUND)"
        )
    return line


def _live_key_present() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--client", choices=("mock", "live"), default="mock")
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--books-root", type=Path, default=ROOT / "Books")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="live only: proceed past the printed cost estimate and spend",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="write this run's rates as the baseline for its client",
    )
    args = parser.parse_args(argv)

    def confirm(estimate: llm.CostEstimate) -> bool:
        print(f"estimated spend: ${estimate.total_usd:.4f} (prices of {estimate.prices_cached_on})")
        if not args.yes:
            print("live run refused: re-run with ARGS=--yes to spend this.")
        return args.yes

    if args.client == "live" and not _live_key_present():
        print(
            "live run refused: ANTHROPIC_API_KEY is not in the environment "
            "(run through `make ingest-calibrate-live`, which fetches it from Render)."
        )
        return 2

    meters: list[_CountingClient] = []

    def metered(client: llm.ModelClient) -> llm.ModelClient:
        meters.append(_CountingClient(client))
        return meters[-1]

    fixture = calibrate.load_fixture(args.fixture)
    report = calibrate.run(
        fixture,
        args.books_root,
        client=args.client,
        events=_print_event,
        confirm=confirm,
        wrap_live=metered,
    )
    print(calibrate.format_report(report))
    if args.client == "live" and args.yes:
        # Never raise here: the money is already spent and the baseline gate is still to run.
        print(_spend_line(meters[0]) if meters else "spend: actual=UNKNOWN (no meter on it)")

    if args.client == "live" and not args.yes:
        return 2

    baselines = json.loads(args.baseline.read_text(encoding="utf-8"))
    if args.update_baseline:
        baselines[args.client] = calibrate.baseline_from(report)
        baselines.setdefault("_runs", {})[args.client] = {
            "recorded_at": datetime.now(UTC).date().isoformat(),
            "models": dict(llm.ROLE_MODEL),
            "fixture": str(args.fixture.relative_to(ROOT)),
        }
        args.baseline.write_text(
            json.dumps(baselines, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"baseline[{args.client}] written to {args.baseline}")
        return 0

    baseline = baselines.get(args.client)
    if baseline is None:
        print(f"no baseline for client {args.client!r} in {args.baseline}; nothing to compare")
        return 0
    regressions = calibrate.regressions(report, baseline)
    for line in regressions:
        print(f"REGRESSION {line}")
    if regressions:
        return 1
    print(f"baseline[{args.client}]: no class below it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
