"""Re-measure P6 on finished replays with the answers they recorded, for $0.

    make ingest-merge-offline CITY=new_york JOB=<job_id> START=<beats.json> \
        RUNS="<sandbox root> <sandbox root> ..."

A merge change is proven live by paid replays; this measures what the
CURRENT code decides on answers the judge ALREADY gave, before any spend.
For each sandbox root (a `make ingest-merge-replay` sandbox that was run),
every story in the sandbox (a `--place` replay keeps one place) is
merged again under today's `merge.merge` +
`merge.apply`, one after another as P6 does, over the file the job started
from (`--start`, refused unless its sha is the job's `beats_sha256`). The
judge is a MockClient scripted with that story's `merge_answered` answers
from the run's event log, in order. Nothing is invented: a story the
current code asks more of than was recorded is `unmeasured`.

What it does NOT measure: whatever runs after the decision — P4/P5
re-narration of a changed or remainder beat and P7's validator.

Prints one JSON report: per run, per story — status (applied | held |
unmeasured), reason, answers recorded vs judge calls made, the judge's
story verdict and per-claim outcomes, and every stated_value the apply
wrote on this job's sources, with the values the judge quoted.
`judge_calls` counts the recorded answers USED (the mock logs a call once
it answers), so an `unmeasured` row shows one fewer than the code asked.
A story with no candidate beat or no judged claim is skipped unreported.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ingest import llm, merge, model  # noqa: E402
from src.ingest.group import Story  # noqa: E402
from src.ingest.judge_claims import JudgedClaim  # noqa: E402
from src.ingest.narrate import BeatHeld  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _recorded_answers(log: Path) -> dict[str, list[llm.MockAnswer]]:
    """The judge's raw answers per story slug, in the order they came."""
    answers: dict[str, list[llm.MockAnswer]] = {}
    for line in log.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("message") != "merge_answered":
            continue
        data = event["data"]
        answers.setdefault(data["story_slug"], []).append(
            llm.MockAnswer(text=data["answer"], model_id=data["model"])
        )
    return answers


def _written_values(
    outcome: merge.MergeOutcome, before: list[model.Beat], after: list[model.Beat]
) -> list[dict[str, Any]]:
    """Every source this story's apply added that carries a stated_value."""
    sources = {c.draft.source.model_dump_json() for c in outcome.judged}
    old = {
        (b.beat_id, c.claim_id): len(c.sources) for b in before for c in b.claims
    }
    quoted = {o.claim_id: (o.new_value, o.existing_value) for o in outcome.claims}
    by_span = {c.draft.source.span: c.draft.claim_id for c in outcome.judged}
    written = []
    for beat in after:
        for claim in beat.claims:
            for source in claim.sources[old.get((beat.beat_id, claim.claim_id), 0):]:
                bare = source.model_copy(update={"stated_value": None}).model_dump_json()
                if source.stated_value is None or bare not in sources:
                    continue
                new_id = by_span.get(source.span)
                written.append({
                    "claim_id": new_id, "beat_id": beat.beat_id,
                    "stated_value": source.stated_value,
                    "quoted": list(quoted.get(new_id, (None, None))),
                })
    return written


def replay_run(root: Path, city: str, job_id: str, start: list[model.Beat]) -> dict[str, Any]:
    job_dir = root / city / "jobs" / job_id
    saved = json.loads((job_dir / "P3.json").read_text(encoding="utf-8"))
    recorded = _recorded_answers(root / city / "jobs" / f"{job_id}.jsonl")
    existing = list(start)
    stories: list[dict[str, Any]] = []
    for unit_key, items in saved["stories"].items():
        for raw in items:
            story = Story.model_validate(raw)
            claims = [
                JudgedClaim.model_validate(j)
                for j in saved["judged"][unit_key].get(story.story_slug, [])
            ]
            candidates = [b for b in existing if b.poi_name == story.place]
            answers = recorded.get(story.story_slug, [])
            if not candidates or not claims:
                continue
            client = llm.MockClient(lambda *_: None, answers={"merge_judge": list(answers)})
            client.estimate([c.draft.text for c in claims], list(merge.P6_PLAN))
            row: dict[str, Any] = {"story_slug": story.story_slug, "recorded": len(answers)}
            try:
                outcome = merge.merge(story, claims, candidates, client)
            except BeatHeld as held:
                row.update(status="held", reason=held.reason)
            except llm.MockScriptExhausted:
                row.update(status="unmeasured",
                           reason="the current code asks more than was recorded")
            else:
                if outcome.held:
                    row.update(status="held", reason=outcome.reason,
                               judge_story=outcome.judge_story)
                else:
                    before = existing
                    folds = [o for o in outcome.claims if o.existing_beat_id is not None]
                    if outcome.story != "new" or folds:
                        existing = merge.apply(outcome, existing)
                    row.update(
                        status="applied", reason=None, judge_story=outcome.judge_story,
                        story=outcome.story, beat_id=outcome.beat_id,
                        claims=[
                            {"claim_id": o.claim_id, "outcome": o.outcome,
                             "named": [list(r) for r in o.covered_by]
                             or ([[o.existing_beat_id, o.existing_claim_id]]
                                 if o.existing_claim_id else [])}
                            for o in outcome.claims
                        ],
                        stated_values=_written_values(outcome, before, existing),
                    )
            row["judge_calls"] = sum(1 for role, _ in client.calls if role == "merge_judge")
            stories.append(row)
    return {"root": str(root), "stories": stories}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--city", required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--start", required=True, type=Path,
                        help="the beats file the job started from")
    parser.add_argument("--run", action="append", required=True, type=Path,
                        help="a replay sandbox root (repeat for each run)")
    args = parser.parse_args(argv)

    start_sha = _sha256(args.start)
    for root in args.run:
        meta = json.loads(
            (root / args.city / "jobs" / args.job / "job.json").read_text(encoding="utf-8")
        )
        if start_sha != meta["beats_sha256"]:
            print(f"refused: {args.start} (sha {start_sha[:16]}) is not the file job "
                  f"{args.job} started from (beats_sha256 {meta['beats_sha256'][:16]}) "
                  f"in {root}")
            return 2
    records = json.loads(args.start.read_text(encoding="utf-8"))
    start = [model.Beat.model_validate(r) for r in records]
    report = {
        "job": args.job, "start_sha256": start_sha,
        "runs": [replay_run(root, args.city, args.job, start) for root in args.run],
    }
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
