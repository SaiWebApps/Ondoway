"""Calibration by defect injection — Docs/ingestion/rebuild-spec.md §4.

`fixtures/ingestion/defects.json` holds ten hand-read records over one
unit, one planted defect each, in the ten classes §4 names. `run()` sends
each record through the detector its class names — the P1 gates
(`gates.claim_gates`), the P3 claim judge (`judge_claims.judge_claims`) or
the omission check (`judge_claims.omissions`) — and tallies caught/missed
per class. A class whose detector lands in a later slice (the framing
regex with P4/P5, contest and supersession with P6) is reported `pending`,
never `missed`.

Under `client="mock"` the judged classes are SCRIPTED from each record's
`planted` block (`scripted_answers`): the run proves the harness — routing,
tallying, printing, the baseline rule — not the judge. Only the mechanical
classes are measured. `client="live"` builds the real `llm.AnthropicClient`
and measures; its estimate is printed first and the caller's `confirm`
callback decides whether a paid call follows.

`regressions()` is §4's rule that a judge prompt change lowering a class's
catch rate fails the target: every class below the committed baseline for
that client is a regression; a pending class or one the baseline has never
seen is not.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.ingest import gates, judge_claims, llm, model
from src.ingest.decompose import ClaimDraft, UnitHeld
from src.ingest.group import Enrichment, Story
from src.ingest.unit import Unit, load_unit

DETECTORS = ("judge_claims", "gates", "omissions", "pending")


@dataclass(frozen=True)
class DefectRecord:
    defect_class: str
    detector: str
    note: str
    story: dict[str, Any]
    claims: list[dict[str, Any]]
    planted: dict[str, Any]
    narration: str | None = None


@dataclass(frozen=True)
class Fixture:
    unit: dict[str, Any]
    records: list[DefectRecord]


@dataclass(frozen=True)
class ClassRow:
    """One class's tally. `caught`/`total` are None for a pending class;
    `scripted` says the answers came from the fixture, not a judge."""

    defect_class: str
    detector: str
    caught: int | None
    total: int | None
    scripted: bool
    note: str = ""


@dataclass(frozen=True)
class Report:
    client: str
    rows: list[ClassRow]


def load_fixture(path: Path) -> Fixture:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    records = []
    for item in raw["records"]:
        if item["detector"] not in DETECTORS:
            raise ValueError(
                f"record {item['class']!r} names unknown detector {item['detector']!r}"
            )
        records.append(
            DefectRecord(
                defect_class=item["class"],
                detector=item["detector"],
                note=item.get("note", ""),
                story=item["story"],
                claims=item["claims"],
                planted=item["planted"],
                narration=item.get("narration"),
            )
        )
    return Fixture(unit=raw["unit"], records=records)


def load_fixture_unit(books_root: Path, fixture: Fixture) -> Unit:
    """The fixture's one unit, read verbatim from `books_root/{city}/...`."""
    spec = fixture.unit
    return load_unit(
        Path(books_root) / spec["city"],
        city=spec["city"],
        source_id=spec["source_id"],
        chunk=spec["chunk"],
        as_of=spec["as_of"],
        rights_basis=spec["rights_basis"],
    )


def _drafts(record: DefectRecord, unit: Unit) -> list[ClaimDraft]:
    return [
        ClaimDraft(
            claim_id=claim["claim_id"],
            text=claim["text"],
            kind=claim["kind"],
            source=unit.source(claim["span"]),
        )
        for claim in record.claims
    ]


def _story(record: DefectRecord) -> Story:
    raw = record.story
    return Story(
        title=raw["title"],
        story_slug=model.slug(raw["title"]),
        place=raw["place"],
        new_poi=False,
        lenses=raw["lenses"],
        beat_type=raw["beat_type"],
        enrichment=Enrichment(),
        claim_ids=raw["claim_ids"],
    )


def scripted_answers(record: DefectRecord, unit: Unit) -> dict[str, llm.MockAnswer]:
    """What a CORRECT detector would answer for `record`, as MockClient
    batch answers — derived from the record's `planted` block. A harness
    script, never a measurement."""
    judge_model = llm.ROLE_MODEL["claim_judge"]
    answers: dict[str, llm.MockAnswer] = {}
    if record.detector == "judge_claims":
        planted_id = record.planted["claim_id"]
        for claim in record.claims:
            refused = claim["claim_id"] == planted_id
            verdict = {
                "entailed": not refused,
                "reason": record.planted["reason"] if refused else "the span states it",
            }
            answers[judge_claims.judge_custom_id(unit, claim["claim_id"], 1)] = llm.MockAnswer(
                text=json.dumps(verdict), model_id=judge_model
            )
        corrected = record.planted["corrected"]
        answers[judge_claims.restate_custom_id(unit, planted_id)] = llm.MockAnswer(
            text=json.dumps(
                {"text": corrected["text"], "kind": corrected["kind"], "span": corrected["span"]}
            ),
            model_id=llm.ROLE_MODEL["author"],
        )
        answers[judge_claims.judge_custom_id(unit, planted_id, 2)] = llm.MockAnswer(
            text=json.dumps({"entailed": True, "reason": "the restated claim matches the span"}),
            model_id=judge_model,
        )
    elif record.detector == "omissions":
        finding = {"fact": record.planted["fact"], "span": record.planted["span"]}
        answers[judge_claims.omissions_custom_id(unit)] = llm.MockAnswer(
            text=json.dumps({"omitted": [finding]}), model_id=judge_model
        )
    return answers


def _arm(client: llm.ModelClient, records: list[DefectRecord], unit: Unit) -> llm.CostEstimate:
    """Print (emit) the cost estimate that gates every completion: the P3
    plan over every judged claim, the omissions plan over the unit once
    per omission record — never an empty estimate, which would arm the
    gate without pricing anything. Returns the LAST estimate emitted."""
    claim_texts = [
        claim["text"]
        for record in records
        if record.detector == "judge_claims"
        for claim in record.claims
    ]
    omission_units = [unit.text for record in records if record.detector == "omissions"]
    if not claim_texts and not omission_units:
        raise ValueError("nothing to estimate: no judged record among those given")
    estimate: llm.CostEstimate | None = None
    if claim_texts:
        estimate = client.estimate(claim_texts, list(judge_claims.P3_PLAN))
    if omission_units:
        estimate = client.estimate(omission_units, list(judge_claims.OMISSIONS_PLAN))
    assert estimate is not None
    return estimate


def _overlaps(found_span: str, planted_span: str) -> bool:
    a = gates.normalize_ws(found_span)
    b = gates.normalize_ws(planted_span)
    return bool(a) and (a in b or b in a)


def _detect(
    record: DefectRecord, unit: Unit, client: llm.ModelClient | None
) -> tuple[bool | None, str]:
    """Run the record's detector; (caught, note). None = pending."""
    if record.detector == "pending":
        return None, record.planted.get("pending_phase", "")

    if record.detector == "gates":
        planted = next(c for c in record.claims if c["claim_id"] == record.planted["claim_id"])
        reasons = gates.claim_gates(planted["text"], planted["span"], unit.text)
        return bool(reasons), "; ".join(reasons)

    assert client is not None
    events: list[tuple[str, dict]] = []

    def record_event(kind: str, payload: dict) -> None:
        events.append((kind, payload))

    try:
        if record.detector == "judge_claims":
            judge_claims.judge_claims(
                _story(record), _drafts(record, unit), unit, client, record_event
            )
            refused = [
                payload
                for kind, payload in events
                if kind == "claim_refused"
                and payload["attempt"] == 1
                and payload["claim_id"] == record.planted["claim_id"]
            ]
            return bool(refused), refused[0]["reason"] if refused else "not refused"
        judge_claims.omissions(unit, _drafts(record, unit), client, record_event)
        spans = [
            span
            for kind, payload in events
            if kind == "omissions_found"
            for span in payload["spans"]
        ]
        hit = [span for span in spans if _overlaps(span, record.planted["span"])]
        return bool(hit), hit[0] if hit else f"{len(spans)} grounded finding(s), none on the span"
    except UnitHeld as held:
        return False, f"unit held: {held.reason}"


def _live_client(events: llm.EventSink) -> llm.ModelClient:
    return llm.AnthropicClient(events)


def run(
    fixture: Fixture,
    books_root: Path,
    client: str = "mock",
    events: llm.EventSink | None = None,
    confirm: Callable[[llm.CostEstimate], bool] | None = None,
) -> Report:
    """Run every record through its detector and tally per class.

    `client="mock"`: one scripted MockClient per record (records share
    claim ids, so their batch custom_ids would collide on one mock).
    `client="live"`: one AnthropicClient, its estimate printed before any
    call; `confirm(estimate)` returning False stops the run with every
    judged class reported as not run (caught None, detector unchanged).
    """
    if client not in ("mock", "live"):
        raise ValueError(f"client must be 'mock' or 'live', got {client!r}")
    sink: llm.EventSink = events or (lambda _kind, _payload: None)
    unit = load_fixture_unit(books_root, fixture)

    live: llm.ModelClient | None = None
    declined = False
    if client == "live":
        live = _live_client(sink)
        estimate = _arm(live, fixture.records, unit)
        if confirm is not None and not confirm(estimate):
            declined = True

    rows: list[ClassRow] = []
    for record in fixture.records:
        scripted = client == "mock" and record.detector in ("judge_claims", "omissions")
        if record.detector == "pending":
            rows.append(ClassRow(record.defect_class, "pending", None, None, False, record.note))
            continue
        if declined and record.detector != "gates":
            rows.append(
                ClassRow(record.defect_class, record.detector, None, None, False, "not run")
            )
            continue
        per_record_client: llm.ModelClient | None = live
        if client == "mock" and record.detector != "gates":
            mock = llm.MockClient(sink, batch_answers=scripted_answers(record, unit))
            _arm(mock, [record], unit)
            per_record_client = mock
        caught, note = _detect(record, unit, per_record_client)
        rows.append(
            ClassRow(record.defect_class, record.detector, int(bool(caught)), 1, scripted, note)
        )
    return Report(client=client, rows=rows)


def _rate(row: ClassRow) -> str:
    if row.total is None:
        return "pending"
    return f"{100 * row.caught // row.total}%"


def format_report(report: Report) -> str:
    lines = [
        f"ingest-calibrate  client={report.client.upper()}",
        f"{'class':<24}{'detector':<16}{'caught/total':<14}rate",
    ]
    for row in report.rows:
        tally = "-" if row.total is None else f"{row.caught}/{row.total}"
        lines.append(f"{row.defect_class:<24}{row.detector:<16}{tally:<14}{_rate(row)}")
    if report.client == "mock":
        lines.append(
            "MOCK: the judge_claims and omissions classes are scripted from the "
            "fixture's planted block — a harness check, not a measurement; only "
            "the gates classes are measured. Run `make ingest-calibrate-live` to measure."
        )
    return "\n".join(lines)


def baseline_from(report: Report) -> dict[str, dict[str, int] | None]:
    return {
        row.defect_class: (
            None if row.total is None else {"caught": row.caught, "total": row.total}
        )
        for row in report.rows
    }


def regressions(report: Report, baseline: dict[str, dict[str, int] | None]) -> list[str]:
    """Every class whose catch rate fell below the baseline's."""
    found: list[str] = []
    for row in report.rows:
        if row.total is None:
            continue
        expected = baseline.get(row.defect_class)
        if expected is None:
            continue
        if row.caught / row.total < expected["caught"] / expected["total"]:
            found.append(
                f"{row.defect_class}: {row.caught}/{row.total} is below the baseline "
                f"{expected['caught']}/{expected['total']}"
            )
    return found
