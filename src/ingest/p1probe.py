"""P1's first ask sampled K times on one unit, each sample classified by how
it states the Guggenheim's Wright claim — Docs/ingestion/rebuild-spec.md,
"P1-split probe".

Job B's P1 wrote c148 "Frank Lloyd Wright designed the spiral Solomon R.
Guggenheim Museum." — two facts in one claim — and the merge judge called it
`new` in 11 of 11 answers, so the Guggenheim fold never landed. This probe
measures how often the UNCHANGED first-ask prompt splits that claim on the
same unit job B saw: SPLIT (a Wright-designed claim without "spiral" and a
separate spiral claim), BUNDLED (both in one claim), or NEITHER (the source
says "Wright's ... spiral", never "designed", so a faithful claim may name
Wright another way). The keyword classifier is a first pass; every sample's
Wright claims are reported for a hand audit, which is the real classifier.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Sequence
from pathlib import Path

from src.ingest import llm, prompts
from src.ingest.decompose import P1_MAX_TOKENS, P1_PLAN, parse_claims
from src.ingest.rejudge import EventLog
from src.ingest.unit import Unit

_GUGGENHEIM = re.compile(r"\bGuggenheim\b")
_WRIGHT = re.compile(r"\bWright\b")
_DESIGN = re.compile(r"\bdesign(?:ed|s|er)?\b", re.I)
_SPIRAL = re.compile(r"\bspiral", re.I)


def classify(texts: Sequence[str]) -> str:
    """SPLIT, BUNDLED or NEITHER for one sample's claim texts (Guggenheim
    claims only — the unit names Wright at other places too)."""
    at_place = [t for t in texts if _GUGGENHEIM.search(t)]
    designed = [t for t in at_place if _WRIGHT.search(t) and _DESIGN.search(t)]
    if any(_SPIRAL.search(t) for t in designed):
        return "BUNDLED"
    if designed and any(_SPIRAL.search(t) for t in at_place):
        return "SPLIT"
    return "NEITHER"


def sample_id(sample: int) -> str:
    return f"p1probe-s{sample}"


def run(
    unit: Unit, *, samples: int, client: llm.ModelClient, arm: bool = True
) -> list[dict]:
    """K identical first asks in ONE batch round; per sample its class, claim
    count and every Guggenheim claim naming Wright."""
    if arm:
        client.estimate([unit.text] * samples, [P1_PLAN[0]])
    prompt = prompts.render_decompose(unit.text)
    results = client.complete_batch(
        role="author",
        prompts=[(sample_id(k), prompt) for k in range(1, samples + 1)],
        schema=prompts.P1_RESPONSE_SCHEMA,
        phase="P1",
        max_tokens=P1_MAX_TOKENS,
    )
    report = []
    for k in range(1, samples + 1):
        answer = results[sample_id(k)]
        items = None if isinstance(answer, llm.BatchFailure) else parse_claims(answer.text)
        if items is None:
            report.append({"sample": k, "class": "FAILED", "claims": 0, "wright_claims": []})
            continue
        texts = [item["text"] for item in items]
        report.append(
            {
                "sample": k,
                "class": classify(texts),
                "claims": len(texts),
                "wright_claims": [
                    t for t in texts if _GUGGENHEIM.search(t) and _WRIGHT.search(t)
                ],
            }
        )
    return report


def main(
    argv: Sequence[str] | None = None,
    client: llm.ModelClient | None = None,
    events: EventLog | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--job-dir", type=Path, required=True, help="a job dir with P0.json")
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--yes", action="store_true", help="proceed past the estimate and spend")
    args = parser.parse_args(argv)

    units = json.loads((args.job_dir / "P0.json").read_text(encoding="utf-8"))["units"]
    if len(units) != 1:
        parser.error(f"the job has {len(units)} units; the probe needs exactly one")
    raw = units[0]
    unit = Unit(**{**raw, "path": Path(raw["path"])})
    if events is None:
        events = EventLog()
    if client is None:
        from src.ingest.run import client_from_env

        client = client_from_env(
            lambda kind, payload: events.append({"event": kind, "data": payload})
        )
    estimate = client.estimate([unit.text] * args.samples, [P1_PLAN[0]])
    print(
        f"estimate: ${estimate.total_usd:.4f} ({args.samples} first ask(s) of P1 on "
        f"{unit.source_id}/{unit.chunk}, one batch round; output priced at the "
        "thinking-inclusive expected tokens)"
    )
    if not args.yes:
        print("run refused: re-run with --yes to spend this.")
        return 2
    args.out.mkdir(parents=True, exist_ok=False)
    events.attach(args.out / "events.jsonl")
    try:
        report = run(unit, samples=args.samples, client=client, arm=False)
    finally:
        usd = getattr(client, "usd", None)
        if usd is not None:
            unmetered = sum(getattr(client, "unmetered", {}).values())
            print(
                f"spend: actual=${usd:.4f} (estimate ${estimate.total_usd:.4f})"
                + (f"; {unmetered} call(s) unmetered: actual is a LOWER BOUND" if unmetered else "")
            )
    (args.out / "p1probe-report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    for sample in report:
        print(json.dumps(sample))
    return 0
