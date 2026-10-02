"""P3's two judge looks, re-run on a finished job's claims and scored
against labels — Docs/ingestion/rebuild-spec.md, "P3 step 3".

After job B (75b8f292) P3 judges a refused claim a second time AS WRITTEN
before any restate. A second look is a second roll of the dice for every
refused claim, so it raises survival whether or not the judge got better.
This harness therefore measures both directions at once, over K samples:
SUPPORTED claims (labels from a blind panel) that survive both looks, and
planted NEGATIVES — a supported claim with its first number changed, cited
by the same span — that are refused on both looks (caught) or cleared (a
false clear). Only the judge runs: never the author's restate.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from src.ingest import llm
from src.ingest.decompose import ClaimDraft
from src.ingest.judge_claims import P3_PLAN, _judge_round
from src.ingest.narrate import emitter
from src.ingest.unit import Unit

_NUMBER_RE = re.compile(r"\d+")


def plant_negative(text: str) -> str | None:
    """`text` with its FIRST number changed — a year-like number (1000-2100)
    by 3, any other by 7 — or None when it states no number."""
    match = _NUMBER_RE.search(text)
    if match is None:
        return None
    value = int(match.group(0))
    changed = value + (3 if 1000 <= value <= 2100 else 7)
    return f"{text[: match.start()]}{changed}{text[match.end():]}"


@dataclass(frozen=True)
class Item:
    claim_id: str
    text: str
    span: str
    label: str  # SUPPORTED, PARTLY, NEGATIVE, or ACCEPTED (passed job's first look)


def negatives_of(items: Sequence[Item]) -> list[Item]:
    """One planted negative per SUPPORTED item that states a number."""
    planted = []
    for item in items:
        if item.label != "SUPPORTED":
            continue
        changed = plant_negative(item.text)
        if changed is not None:
            planted.append(Item(f"{item.claim_id}n", changed, item.span, "NEGATIVE"))
    return planted


def custom_ids(sample: int, claim_id: str, look: int) -> str:
    """One request id per sample, claim and look — unique across all three."""
    return f"rj{sample}-{claim_id}-l{look}"


@dataclass
class Report:
    per_sample: list[dict[str, int]] = field(default_factory=list)
    outcomes: dict[tuple[str, int], tuple[str, str | None]] = field(default_factory=dict)


def _look(verdict: dict) -> str:
    return "cleared" if verdict["entailed"] else "refused"


def run(
    unit: Unit,
    items: Sequence[Item],
    *,
    samples: int,
    client: llm.ModelClient,
    events: llm.EventSink | None = None,
    arm: bool = True,
) -> Report:
    """Judge every item up to twice per sample (the second look only when the
    first refused) and score it. With `arm`, the estimate over the upper
    bound — every item judged twice in every sample — is printed first."""
    emit = emitter(events)
    if arm:
        client.estimate([unit.text] * (2 * samples * len(items)), [P3_PLAN[0]])
    drafts = {
        item.claim_id: ClaimDraft(
            claim_id=item.claim_id, text=item.text, kind="state", source=unit.source(item.span)
        )
        for item in items
    }
    report = Report()
    for sample in range(1, samples + 1):
        first = _judge_round(
            emit, list(drafts.values()), unit, client, attempt=1,
            custom_id=lambda cid, s=sample: custom_ids(s, cid, 1),
        )
        refused = [drafts[cid] for cid, v in first.items() if not v["entailed"]]
        second = (
            _judge_round(
                emit, refused, unit, client, attempt=1,
                custom_id=lambda cid, s=sample: custom_ids(s, cid, 2),
            )
            if refused
            else {}
        )
        tally = {
            "supported_survived": 0, "supported": 0, "negatives_caught": 0, "negatives": 0,
            "accepted": 0, "accepted_refused_first": 0, "accepted_lost": 0,
        }
        for item in items:
            look1 = _look(first[item.claim_id])
            look2 = _look(second[item.claim_id]) if item.claim_id in second else None
            report.outcomes[(item.claim_id, sample)] = (look1, look2)
            survived = look1 == "cleared" or look2 == "cleared"
            if item.label == "SUPPORTED":
                tally["supported"] += 1
                tally["supported_survived"] += int(survived)
            elif item.label == "NEGATIVE":
                tally["negatives"] += 1
                tally["negatives_caught"] += int(not survived)
            elif item.label == "ACCEPTED":
                tally["accepted"] += 1
                tally["accepted_refused_first"] += int(look1 == "refused")
                tally["accepted_lost"] += int(not survived)
        report.per_sample.append(tally)
    return report


class EventLog(list):
    """The client's events: buffered until `attach`, then also appended to
    that file AS THEY ARRIVE — a run that dies mid-batch (poll ceiling, 404,
    invalid request) still leaves every batch id it submitted on disk."""

    _path: Path | None = None

    def attach(self, path: Path) -> None:
        self._path = path
        with path.open("a", encoding="utf-8") as handle:
            for event in self:
                handle.write(json.dumps(event) + "\n")

    def append(self, event: dict) -> None:
        super().append(event)
        if self._path is not None:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event) + "\n")


def _items(job_dir: Path, labels_dir: Path, events_path: Path | None, accepted: bool) -> list[Item]:
    """The claims BOTH labelers labeled alike (texts from P1.json, or from a
    refusal event for a claim P1 never emitted) and a planted negative per
    numbered SUPPORTED claim — or, with `accepted`, ONLY every unlabeled
    claim (its own pass, never pooled with the labeled pass's samples)."""
    p1 = json.loads((job_dir / "P1.json").read_text(encoding="utf-8"))["claims"]
    claims = {c["claim_id"]: (c["text"], c["source"]["span"]) for v in p1.values() for c in v}
    if events_path is not None:
        for line in events_path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            data = event.get("data") or {}
            if event.get("message") == "claim_refused" and data.get("attempt") == 1:
                claims.setdefault(data["claim_id"], (data["claim_text"], data["span"]))
    key = {k["id"]: k["claim_id"] for k in json.loads((labels_dir / "label_key.json").read_text())}
    panel = [
        json.loads((labels_dir / name).read_text())["claims"]
        for name in ("labels_fable.json", "labels_sonnet.json")
    ]
    agreed = {
        key[item_id]: label
        for item_id, label in panel[0].items()
        if all(other.get(item_id) == label for other in panel[1:])
    }
    if accepted:
        return [
            Item(cid, text, span, "ACCEPTED")
            for cid, (text, span) in claims.items()
            if cid not in agreed
        ]
    items = [Item(cid, *claims[cid], label) for cid, label in agreed.items()]
    return items + negatives_of(items)


def main(
    argv: Sequence[str] | None = None,
    client: llm.ModelClient | None = None,
    events: EventLog | None = None,
) -> int:
    """`events` is the log the client's sink appends to; after --yes it is
    attached to <out>/events.jsonl (created before any call) and every event
    lands there as it arrives. A client carrying a meter (`usd`) has its
    actual spend printed even when the run dies."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--job-dir", type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--labels-dir", type=Path)
    source.add_argument(
        "--items", type=Path,
        help="a JSON list of {claim_id, text, span, label} judged as given (e.g. a job's "
        "dropped restates, which no P1-keyed label reaches), plus planted negatives",
    )
    parser.add_argument("--events", type=Path, default=None, help="the job's .jsonl log")
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--accepted", action="store_true",
                        help="judge ONLY every unlabeled claim (label ACCEPTED), its own pass")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--yes", action="store_true", help="proceed past the estimate and spend")
    args = parser.parse_args(argv)

    raw = json.loads((args.job_dir / "P0.json").read_text(encoding="utf-8"))["units"][0]
    unit = Unit(**{**raw, "path": Path(raw["path"])})
    if args.items is not None:
        given = [
            Item(i["claim_id"], i["text"], i["span"], i["label"])
            for i in json.loads(args.items.read_text(encoding="utf-8"))
        ]
        items = given + negatives_of(given)
    else:
        items = _items(args.job_dir, args.labels_dir, args.events, args.accepted)
    if events is None:
        events = EventLog()
    if client is None:
        from src.ingest.run import client_from_env

        client = client_from_env(
            lambda kind, payload: events.append({"event": kind, "data": payload})
        )
    estimate = client.estimate([unit.text] * (2 * args.samples * len(items)), [P3_PLAN[0]])
    labels = sorted({item.label for item in items})
    print(
        f"estimate: ${estimate.total_usd:.4f} at most ({len(items)} items x {args.samples} "
        f"sample(s) x 2 looks, labels {labels}; the second look runs only on refusals)"
    )
    if not args.yes:
        print("run refused: re-run with --yes to spend this.")
        return 2
    args.out.mkdir(parents=True, exist_ok=False)
    events.attach(args.out / "events.jsonl")
    try:
        report = run(unit, items, samples=args.samples, client=client, arm=False)
    finally:
        usd = getattr(client, "usd", None)
        if usd is not None:
            unmetered = sum(getattr(client, "unmetered", {}).values())
            print(
                f"spend: actual=${usd:.4f} (estimate ${estimate.total_usd:.4f} at most)"
                + (f"; {unmetered} call(s) unmetered: actual is a LOWER BOUND" if unmetered else "")
            )
    (args.out / "rejudge-report.json").write_text(
        json.dumps(
            {
                "per_sample": report.per_sample,
                "outcomes": {f"{cid}@{s}": list(o) for (cid, s), o in report.outcomes.items()},
                "labels": {item.claim_id: item.label for item in items},
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    for tally in report.per_sample:
        print(json.dumps(tally))
    return 0
