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
    `scripted` says the answers came from the fixture, not a judge.
    `false_refusals` (clean claims the judge refused on attempt one) and
    `dropped` (claims that never came out of P3) are the precision side of
    the record — catch rate alone rewards a judge that refuses everything;
    `details` carries every refusal and drop reason for the printout."""

    defect_class: str
    detector: str
    caught: int | None
    total: int | None
    scripted: bool
    note: str = ""
    false_refusals: int | None = None
    dropped: int | None = None
    details: tuple[str, ...] = ()


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
    gate without pricing anything. Returns the SUM of every estimate
    emitted: the whole ceiling an owner is asked to confirm."""
    claim_texts = [
        claim["text"]
        for record in records
        if record.detector == "judge_claims"
        for claim in record.claims
    ]
    omission_units = [unit.text for record in records if record.detector == "omissions"]
    if not claim_texts and not omission_units:
        raise ValueError("nothing to estimate: no judged record among those given")
    estimates: list[llm.CostEstimate] = []
    if claim_texts:
        estimates.append(client.estimate(claim_texts, list(judge_claims.P3_PLAN)))
    if omission_units:
        estimates.append(client.estimate(omission_units, list(judge_claims.OMISSIONS_PLAN)))
    return llm.CostEstimate(
        units=sum(e.units for e in estimates),
        rows=[row for e in estimates for row in e.rows],
        total_input_tokens=sum(e.total_input_tokens for e in estimates),
        total_output_tokens=sum(e.total_output_tokens for e in estimates),
        total_usd=sum(e.total_usd for e in estimates),
        prices_cached_on=estimates[0].prices_cached_on,
    )


def _overlaps(found_span: str, planted_span: str) -> bool:
    a = gates.normalize_ws(found_span)
    b = gates.normalize_ws(planted_span)
    return bool(a) and (a in b or b in a)


@dataclass(frozen=True)
class _Detection:
    caught: bool | None
    note: str
    false_refusals: int | None = None
    dropped: int | None = None
    details: tuple[str, ...] = ()


def _precision(events: list[tuple[str, dict]], planted_id: str | None) -> tuple[int, int, tuple]:
    """False refusals (clean claims refused on attempt one), drops, and
    every refusal/drop reason in event order."""
    false_refusals = 0
    dropped = 0
    details: list[str] = []
    for kind, payload in events:
        if kind == "claim_refused":
            if payload["attempt"] == 1 and payload["claim_id"] != planted_id:
                false_refusals += 1
            details.append(
                f"refused {payload['claim_id']} (attempt {payload['attempt']}): "
                f"{payload['reason']}"
            )
        elif kind == "claim_dropped":
            dropped += 1
            details.append(f"dropped {payload['claim_id']}: {payload['reason']}")
        elif kind == "omission_ungrounded":
            details.append(f"ungrounded finding discarded: {payload['reason']}")
    return false_refusals, dropped, tuple(details)


def _detect(record: DefectRecord, unit: Unit, client: llm.ModelClient | None) -> _Detection:
    """Run the record's detector. `caught` None = pending."""
    if record.detector == "pending":
        return _Detection(None, record.planted.get("pending_phase", ""))

    if record.detector == "gates":
        planted = next(c for c in record.claims if c["claim_id"] == record.planted["claim_id"])
        reasons = gates.claim_gates(planted["text"], planted["span"], unit.text)
        return _Detection(bool(reasons), "; ".join(reasons))

    assert client is not None
    events: list[tuple[str, dict]] = []

    def record_event(kind: str, payload: dict) -> None:
        events.append((kind, payload))

    planted_id = record.planted.get("claim_id")
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
                and payload["claim_id"] == planted_id
            ]
            caught, note = bool(refused), refused[0]["reason"] if refused else "not refused"
        else:
            judge_claims.omissions(unit, _drafts(record, unit), client, record_event)
            spans = [
                span
                for kind, payload in events
                if kind == "omissions_found"
                for span in payload["spans"]
            ]
            hit = [span for span in spans if _overlaps(span, record.planted["span"])]
            caught = bool(hit)
            note = hit[0] if hit else f"{len(spans)} grounded finding(s), none on the span"
    except UnitHeld as held:
        caught, note = False, f"unit held: {held.reason}"
    false_refusals, dropped, details = _precision(events, planted_id)
    return _Detection(caught, note, false_refusals, dropped, details)


def _live_client(events: llm.EventSink) -> llm.ModelClient:
    return llm.AnthropicClient(events)


def run(
    fixture: Fixture,
    books_root: Path,
    client: str = "mock",
    events: llm.EventSink | None = None,
    confirm: Callable[[llm.CostEstimate], bool] | None = None,
    scripted: Callable[[DefectRecord, Unit], dict[str, llm.MockAnswer]] = scripted_answers,
) -> Report:
    """Run every record through its detector and tally per class.

    `client="mock"`: one MockClient per record (records share claim ids,
    so their batch custom_ids would collide on one mock), scripted by
    `scripted(record, unit)` — `scripted_answers` by default; a test
    passes its own to exercise the harness on a misbehaving judge.
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
        is_scripted = client == "mock" and record.detector in ("judge_claims", "omissions")
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
            mock = llm.MockClient(sink, batch_answers=scripted(record, unit))
            _arm(mock, [record], unit)
            per_record_client = mock
        found = _detect(record, unit, per_record_client)
        rows.append(
            ClassRow(
                record.defect_class,
                record.detector,
                int(bool(found.caught)),
                1,
                is_scripted,
                found.note,
                found.false_refusals,
                found.dropped,
                found.details,
            )
        )
    return Report(client=client, rows=rows)


def _rate(row: ClassRow) -> str:
    if row.total is None:
        return "pending" if row.detector == "pending" else "not run"
    return f"{100 * row.caught // row.total}%"


def format_report(report: Report) -> str:
    lines = [
        f"ingest-calibrate  client={report.client.upper()}",
        f"{'class':<24}{'detector':<16}{'caught/total':<14}{'rate':<10}{'false_ref':<11}dropped",
    ]
    for row in report.rows:
        tally = "-" if row.total is None else f"{row.caught}/{row.total}"
        false_ref = "-" if row.false_refusals is None else str(row.false_refusals)
        dropped = "-" if row.dropped is None else str(row.dropped)
        lines.append(
            f"{row.defect_class:<24}{row.detector:<16}{tally:<14}{_rate(row):<10}"
            f"{false_ref:<11}{dropped}"
        )
    details = [(row.defect_class, d) for row in report.rows for d in row.details]
    if details:
        lines.append("details (every refusal and drop, in order):")
        lines.extend(f"  {defect_class}: {detail}" for defect_class, detail in details)
    if report.client == "mock":
        lines.append(
            "MOCK: the judge_claims and omissions classes are scripted from the "
            "fixture's planted block — a harness check, not a measurement; only "
            "the gates classes are measured. Run `make ingest-calibrate-live` to measure."
        )
    return "\n".join(lines)


def baseline_from(report: Report) -> dict[str, dict[str, int] | None]:
    """Per class: catch tally, plus the precision counts where a judge ran."""
    baseline: dict[str, dict[str, int] | None] = {}
    for row in report.rows:
        if row.total is None:
            baseline[row.defect_class] = None
            continue
        entry = {"caught": row.caught, "total": row.total}
        if row.false_refusals is not None:
            entry["false_refusals"] = row.false_refusals
            entry["dropped"] = row.dropped
        baseline[row.defect_class] = entry
    return baseline


def regressions(report: Report, baseline: dict[str, dict[str, int] | None]) -> list[str]:
    """Every class whose catch rate fell below the baseline's, or whose
    false refusals or drops rose above it (only where the baseline
    recorded them — a baseline from before precision was measured
    compares catch rate alone)."""
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
        if "false_refusals" in expected and (row.false_refusals or 0) > expected["false_refusals"]:
            found.append(
                f"{row.defect_class}: {row.false_refusals} false refusal(s) above the "
                f"baseline {expected['false_refusals']}"
            )
        if "dropped" in expected and (row.dropped or 0) > expected["dropped"]:
            found.append(
                f"{row.defect_class}: {row.dropped} dropped claim(s) above the baseline "
                f"{expected['dropped']}"
            )
    return found
