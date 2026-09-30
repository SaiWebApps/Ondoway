"""Calibration by defect injection — Docs/ingestion/rebuild-spec.md §4.

`fixtures/ingestion/defects.json` holds hand-read records over one unit,
one planted defect each, one per class §4 names (ten, plus slice 10's
shared_element, same_fact, compound_claim and stated_relation). `run()` sends
each record through the detector its class names — the P1 gates
(`gates.claim_gates`), the P3 claim judge (`judge_claims.judge_claims`),
the omission check (`judge_claims.omissions`) or the P4 narration code
gate (`narrate.narration_gates` over the record's planted narration; the
class is caught when the reason list names the planted gate) or the P6
merge (`merge.merge` of the planted second source against the record's own
claims as the beat the corpus holds; caught when the planted claim reaches
the planted outcome, contested, supersedes or same — or, for shared_element,
when nothing folds at all; for a plant carrying a judgment, only when the
planted word is among the outcome's `new_judgments`, ADR-0004) or the P4+P5
narration of
a beat (`narration`: `narrate.narrate` then `judge_narration.judge_narration`,
as the runner re-narrates a merged beat; caught when exactly one sentence
carries the planted anchor and that sentence carries the planted judgment)
— and tallies caught/missed per class. A class whose detector lands in a later slice (state-as-event
has no detector until the owner rules) is reported `pending`, never
`missed`.

Under `client="mock"` the judged classes are SCRIPTED from each record's
`planted` block (`scripted_answers`, `scripted_merge_answer`): the run proves the harness — routing,
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
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.ingest import gates, judge_claims, judge_narration, llm, merge, model, narrate
from src.ingest.decompose import ClaimDraft, UnitHeld
from src.ingest.group import Enrichment, Story
from src.ingest.run import judged_of_beat
from src.ingest.unit import Unit, load_unit

DETECTORS = (
    "judge_claims", "gates", "omissions", "narration_gates", "merge", "narration", "pending"
)


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
    #: A merge class the pipeline HELD rather than applied. A hold is scored
    #: MISSED — the judge was wrong — but it wrote nothing and queued the
    #: story for a human, so it is not the corpus defect a wrong fold is.
    held: bool = False
    #: A merge class MISSED with something folded: the corpus changed on a
    #: wrong answer. This, not the catch rate, is the paid run's bar (1) —
    #: a bare 0/1 cannot be read without it (a miss where nothing folded
    #: leaves the corpus untouched).
    wrong_fold: bool = False


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
        expected_kind = record.planted.get("expected_kind")
        for claim in record.claims:
            planted = claim["claim_id"] == planted_id
            # A kind plant is caught by RE-KINDING, not refusing: the judge
            # answers entailed with the expected kind; every other claim
            # keeps the kind the author gave it.
            refused = planted and expected_kind is None
            verdict = {
                "entailed": not refused,
                "reason": record.planted["reason"] if refused else "the span states it",
                "kind": expected_kind if planted and expected_kind else claim["kind"],
            }
            answers[judge_claims.judge_custom_id(unit, claim["claim_id"], 1)] = llm.MockAnswer(
                text=json.dumps(verdict), model_id=judge_model
            )
            if refused:  # a correct detector refuses it again on the second look
                answers[judge_claims.rejudge_custom_id(unit, claim["claim_id"])] = llm.MockAnswer(
                    text=json.dumps(verdict), model_id=judge_model
                )
        corrected = record.planted.get("corrected")
        if corrected is not None:
            answers[judge_claims.restate_custom_id(unit, planted_id)] = llm.MockAnswer(
                text=json.dumps(
                    {
                        "text": corrected["text"],
                        "kind": corrected["kind"],
                        "span": corrected["span"],
                    }
                ),
                model_id=llm.ROLE_MODEL["author"],
            )
            answers[judge_claims.judge_custom_id(unit, planted_id, 2)] = llm.MockAnswer(
                text=json.dumps(
                    {
                        "entailed": True,
                        "reason": "the restated claim matches the span",
                        "kind": corrected["kind"],
                    }
                ),
                model_id=judge_model,
            )
    elif record.detector == "omissions" and record.planted.get("expected") == "compound":
        flagged = [{"claim_id": record.planted["claim_id"], "facts": record.planted["facts"]}]
        answers[judge_claims.omissions_custom_id(unit)] = llm.MockAnswer(
            text=json.dumps({"omitted": [], "compound": flagged}), model_id=judge_model
        )
    elif record.detector == "omissions" and record.planted.get("expected") == "atomic":
        answers[judge_claims.omissions_custom_id(unit)] = llm.MockAnswer(
            text=json.dumps({"omitted": [], "compound": []}), model_id=judge_model
        )
    elif record.detector == "omissions":
        finding = {"fact": record.planted["fact"], "span": record.planted["span"]}
        answers[judge_claims.omissions_custom_id(unit)] = llm.MockAnswer(
            text=json.dumps({"omitted": [finding]}), model_id=judge_model
        )
    return answers


def _beat(record: DefectRecord, unit: Unit) -> model.Beat:
    """The record's claims as the beat the corpus would already hold, under
    a placeholder narration: the merge judge reads claims, never prose."""
    story = _story(record)
    claims = [
        {
            "claim_id": claim["claim_id"],
            "text": claim["text"],
            "kind": claim["kind"],
            "status": "resolved",
            "sources": [unit.source(claim["span"]).model_dump()],
            "resolved_value": None,
            "resolution": None,
            "verdict": {
                "judge_model": llm.ROLE_MODEL["claim_judge"],
                "entailed": True,
                "bound_to": model.bind(claim["text"], claim["span"]),
            },
        }
        for claim in record.claims
    ]
    text = "(calibration record: no narration)"
    return model.Beat(
        beat_id=f"{unit.city}/{model.slug(story.place)}/{story.story_slug}",
        city_name=unit.city,
        poi_name=story.place,
        story_slug=story.story_slug,
        title=story.title,
        beat_type=story.beat_type,
        lenses=story.lenses,
        claims=claims,
        narration={
            "text": text,
            "claims_hash": model.claims_hash(claims),
            "author_model": llm.ROLE_MODEL["author"],
            "verdict": {
                "judge_model": llm.ROLE_MODEL["narration_judge"],
                "sentences_entailed": 0,
                "sentences_total": 0,
                "bound_to": model.bind(text),
            },
            "flags": [],
        },
    )


def _second_source(record: DefectRecord, unit: Unit) -> tuple[Story, judge_claims.JudgedClaim]:
    """The planted second source telling the same story: one claim over its
    own one-line unit, judged entailed, dated as the plant says."""
    planted = record.planted["second_source"]
    second = Unit(
        city=unit.city,
        source_id=planted["source_id"],
        chunk=planted["chunk"],
        text=planted["text"],
        as_of=planted["as_of"],
        rights_basis=planted["rights_basis"],
    )
    draft = ClaimDraft(
        claim_id="n01",
        text=planted["text"],
        kind=planted["kind"],
        source=second.source(planted["text"]),
    )
    judged = judge_claims.JudgedClaim(
        draft=draft,
        verdict=model.Verdict(
            judge_model=llm.ROLE_MODEL["claim_judge"],
            entailed=True,
            bound_to=model.bind(draft.text, draft.source.span),
        ),
    )
    return _story(record), judged


def scripted_merge_answer(record: DefectRecord, unit: Unit) -> llm.MockAnswer:
    """What a CORRECT merge judge would answer for a merge record: the same
    story as the record's beat, its one claim in conflict with the planted
    claim, both stated values quoted. What the conflict BECOMES (contested
    or supersedes) is the code's, by kind and date — never the judge's."""
    planted = record.planted
    if "judgment" in planted:
        # adds_a_judgment / covered_adds_a_judgment: the fact is the record's,
        # the judgment is listed, and code keeps it (ADR-0003, ADR-0004).
        covering = "claim_ids" in planted
        answer = {
            "story": "new" if covering else "same",
            "beat_id": "" if covering else "b1",
            "claims": [
                {"claim_id": "n01", "verdict": "covered" if covering else "same",
                 "existing_claim_id": (
                     "+".join(f"b1.{cid}" for cid in planted["claim_ids"]) if covering
                     else f"b1.{planted['claim_id']}"
                 ),
                 "new_value": "", "existing_value": "", "judgments": [planted["judgment"]],
                 "reason": record.note}
            ],
        }
        return llm.MockAnswer(text=json.dumps(answer), model_id=llm.ROLE_MODEL["merge_judge"])
    if planted["expected"] == "new":
        # shared_element: the second source shares an element, not a fact.
        answer = {
            "story": "new",
            "beat_id": "",
            "claims": [
                {"claim_id": "n01", "verdict": "new", "existing_claim_id": "",
                 "new_value": "", "existing_value": "", "judgments": [],
                 "reason": record.note}
            ],
        }
        return llm.MockAnswer(text=json.dumps(answer), model_id=llm.ROLE_MODEL["merge_judge"])
    if planted["expected"] == "covered":
        # covered_union: the record's claims together state the second source.
        answer = {
            "story": "new",
            "beat_id": "",
            "claims": [
                {"claim_id": "n01", "verdict": "covered",
                 "existing_claim_id": "+".join(f"b1.{cid}" for cid in planted["claim_ids"]),
                 "new_value": "", "existing_value": "", "judgments": [],
                 "reason": record.note}
            ],
        }
        return llm.MockAnswer(text=json.dumps(answer), model_id=llm.ROLE_MODEL["merge_judge"])
    if planted["expected"] == "same":
        # same_fact: the second source restates the planted claim.
        answer = {
            "story": "same",
            "beat_id": "b1",
            "claims": [
                {"claim_id": "n01", "verdict": "same",
                 "existing_claim_id": f"b1.{planted['claim_id']}",
                 "new_value": "", "existing_value": "", "judgments": [],
                 "reason": record.note}
            ],
        }
        return llm.MockAnswer(text=json.dumps(answer), model_id=llm.ROLE_MODEL["merge_judge"])
    values = planted["stated_values"]
    # The record's beat is P6's only candidate, so its handle is b1.
    answer = {
        "story": "same",
        "beat_id": "b1",
        "claims": [
            {
                "claim_id": "n01",
                "verdict": "conflict",
                "existing_claim_id": f"b1.{planted['claim_id']}",
                "new_value": str(values[planted["second_source"]["source_id"]]),
                "existing_value": str(values[unit.source_id]),
                "judgments": [],
                "reason": record.note,
            }
        ],
    }
    return llm.MockAnswer(text=json.dumps(answer), model_id=llm.ROLE_MODEL["merge_judge"])


def _judged_claims(record: DefectRecord, unit: Unit) -> list[judge_claims.JudgedClaim]:
    """The record's claims as P3 would pass them on: every one entailed."""
    return [
        judge_claims.JudgedClaim(
            draft=draft,
            verdict=model.Verdict(
                judge_model=llm.ROLE_MODEL["claim_judge"],
                entailed=True,
                bound_to=model.bind(draft.text, draft.source.span),
            ),
        )
        for draft in _drafts(record, unit)
    ]


def scripted_narration(
    record: DefectRecord, unit: Unit, narration: str | None = None
) -> tuple[dict[str, list[llm.MockAnswer]], dict[str, llm.MockAnswer]]:
    """What a CORRECT author and narration judge would answer for a
    narration record: the planted narration (or `narration`), every
    sentence entailed. Returns (sync answers, batch answers by custom_id)."""
    text = narration if narration is not None else record.planted["narration"]
    draft = narrate.draft_from(text, _told_once_claims(record, unit), llm.ROLE_MODEL["author"])
    answers = {"author": [llm.MockAnswer(
        text=json.dumps({"narration": text}), model_id=llm.ROLE_MODEL["author"]
    )]}
    batch = {
        judge_narration.sentence_custom_id(draft, index, 1): llm.MockAnswer(
            text=json.dumps({"entailed": True, "reason": "stated by the claims"}),
            model_id=llm.ROLE_MODEL["narration_judge"],
        )
        for index in range(len(judge_narration.sentences(text)))
    }
    return answers, batch


def _arm(client: llm.ModelClient, records: list[DefectRecord], unit: Unit) -> llm.CostEstimate:
    """Print (emit) the cost estimate that gates every completion: the P3
    plan over every judged claim, the omissions plan over the unit once
    per omission record, the P6 plan over each merge record's second
    source — never an empty estimate, which would arm the
    gate without pricing anything. Returns the SUM of every estimate
    emitted: the whole projection an owner is asked to confirm."""
    claim_texts = [
        claim["text"]
        for record in records
        if record.detector == "judge_claims"
        for claim in record.claims
    ]
    omission_units = [unit.text for record in records if record.detector == "omissions"]
    merge_units = [
        record.planted["second_source"]["text"] for record in records if record.detector == "merge"
    ]
    # P4 and P5 priced per claim text, the runner's own proxy for sentences.
    narration_texts = [
        claim["text"]
        for record in records
        if record.detector == "narration"
        for claim in record.claims
    ]
    if not claim_texts and not omission_units and not merge_units and not narration_texts:
        raise ValueError("nothing to estimate: no judged record among those given")
    estimates: list[llm.CostEstimate] = []
    if claim_texts:
        estimates.append(client.estimate(claim_texts, list(judge_claims.P3_PLAN)))
    if omission_units:
        estimates.append(client.estimate(omission_units, list(judge_claims.OMISSIONS_PLAN)))
    if merge_units:
        estimates.append(client.estimate(merge_units, list(merge.P6_PLAN)))
    if narration_texts:
        estimates.append(client.estimate(narration_texts, list(narrate.P4_PLAN)))
        estimates.append(client.estimate(narration_texts, list(judge_narration.P5_PLAN)))
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
    held: bool = False
    wrong_fold: bool = False


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


#: A detector's own evidence, forwarded to the run's sink tagged with the
#: class, so a live miss can be diagnosed from the log instead of re-paid.
EVIDENCE_EVENTS: frozenset[str] = frozenset({
    "compound_found", "compound_unknown", "omissions_found", "omission_ungrounded",
    "merge_answered", "merge_folded",
})


def _judgment_kept(
    record: DefectRecord, outcome: merge.MergeOutcome, beats: list[model.Beat]
) -> bool:
    """A judgment plant is caught (ADR-0004) when its own word is kept: for
    `same`, the fold lands on the planted claim with the word in
    `new_judgments`, the appended source carries it and the beat reruns;
    for a covered plant, the claim stays `new` with the word in
    `new_judgments` instead of being absorbed."""
    word = record.planted["judgment"].lower()
    for c in outcome.claims:
        if word not in (w.lower() for w in c.new_judgments):
            continue
        if record.planted["expected"] == "new":
            if c.outcome == "new" and sorted(ref[1] for ref in c.covered_by) == sorted(
                record.planted["claim_ids"]
            ):
                return True
            continue
        if c.outcome != "same" or c.existing_claim_id != record.planted["claim_id"]:
            continue
        applied = merge.apply(outcome, beats)
        claim = next(
            x for b in applied if b.beat_id == c.existing_beat_id
            for x in b.claims if x.claim_id == c.existing_claim_id
        )
        carried = any(word in (w.lower() for w in s.judgments) for s in claim.sources)
        if carried and c.existing_beat_id in outcome.rerun:
            return True
    return False


def _told_once_claims(record: DefectRecord, unit: Unit) -> list[judge_claims.JudgedClaim]:
    """The told_once beat as the merge writes it (ADR-0004): the record's
    claim with the planted second source appended, carrying its judgment,
    read back through `run.judged_of_beat` — the production rerun path, so
    P4 and P5 see exactly what they would in a job."""
    beat = _beat(record, unit)
    planted = record.planted["second_source"]
    second = Unit(
        city=unit.city, source_id=planted["source_id"], chunk=planted["chunk"],
        text=planted["text"], as_of=planted["as_of"], rights_basis=planted["rights_basis"],
    )
    source = second.source(planted["text"]).model_copy(
        update={"judgments": [record.planted["judgment"]]}
    )
    claim = beat.claims[0]
    sources = [*claim.sources, source]
    claim = claim.model_copy(update={
        "sources": sources,
        "resolution": model.Resolution(by="corroborated"),
        "verdict": claim.verdict.model_copy(update={
            "bound_to": model.bind(claim.text, "\n".join(s.span for s in sources))
        }),
    })
    beat = beat.model_copy(update={"claims": [claim, *beat.claims[1:]]})
    return judged_of_beat(beat)


def _told_once(
    record: DefectRecord, unit: Unit, client: llm.ModelClient, record_event: llm.EventSink
) -> _Detection:
    """`told_once`: the record's beat narrated as the runner re-narrates a
    merged beat — P4, then P5, scoring the text P5 returns (it may rewrite
    it). Caught iff exactly one sentence carries the anchor and that one
    carries the judgment as a whole word; a P5 hold is a miss, and so is
    "also called" anywhere in it (the judgment misread as a name). The judged
    narration is always in the details, for the eye: a second telling
    without the anchor is not scored (spec, `told_once`'s blind spot)."""
    story = _story(record)
    claims = _told_once_claims(record, unit)
    # The chapters the claims cite, as the runner passes them: the lift gate
    # reads the chapter, not only the (often truncated) span.
    passages = [unit.text, record.planted["second_source"]["text"]]
    try:
        draft = narrate.narrate(story, claims, client, record_event, passages=passages)
        judged = judge_narration.judge_narration(
            story, draft, claims, client, record_event, passages=passages
        )
    except narrate.BeatHeld as held:
        return _Detection(False, f"beat held: {held.reason}", held=True)
    text = judged.narration.text
    anchor, judgment = record.planted["anchor"], record.planted["judgment"]
    carrying = [s for s in judge_narration.sentences(text) if anchor in s]
    note = f"{len(carrying)} sentence(s) with {anchor!r}"
    caught = len(carrying) == 1
    if caught and not re.search(rf"(?<!\w){re.escape(judgment)}(?!\w)", carrying[0], re.I):
        caught = False
        note += f"; {judgment!r} missing from it"
    if re.search(r"\balso called\b", text, re.I):
        # The judgment read as a name ("the building, also called acclaimed"):
        # every gate and P5 pass it, so the scorer must not (skeptic panel).
        caught = False
        note += "; 'also called' in it"
    if judged.review.held:
        caught = False
        note += f"; held at P5: {judged.review.reason}"
    return _Detection(caught, note, details=(f"narration: {text}",), held=judged.review.held)


def _detect(
    record: DefectRecord,
    unit: Unit,
    client: llm.ModelClient | None,
    evidence: llm.EventSink | None = None,
) -> _Detection:
    """Run the record's detector. `caught` None = pending."""
    if record.detector == "pending":
        return _Detection(None, record.planted.get("pending_phase", ""))

    if record.detector == "gates":
        planted = next(c for c in record.claims if c["claim_id"] == record.planted["claim_id"])
        reasons = gates.claim_gates(planted["text"], planted["span"], unit.text)
        return _Detection(bool(reasons), "; ".join(reasons))

    if record.detector == "narration_gates":
        assert record.narration is not None
        reasons = narrate.narration_gates(
            record.narration, [claim["span"] for claim in record.claims]
        )
        caught = any(reason.startswith(record.planted["gate"]) for reason in reasons)
        return _Detection(caught, "; ".join(reasons) or "no gate tripped")

    assert client is not None
    events: list[tuple[str, dict]] = []

    def record_event(kind: str, payload: dict) -> None:
        events.append((kind, payload))
        if evidence is not None and kind in EVIDENCE_EVENTS:
            evidence(kind, {"class": record.defect_class, **payload})

    planted_id = record.planted.get("claim_id")
    if record.detector == "narration":
        return _told_once(record, unit, client, record_event)
    if record.detector == "merge":
        story, judged = _second_source(record, unit)
        try:
            outcome = merge.merge(story, [judged], [_beat(record, unit)], client, record_event)
        except narrate.BeatHeld as held:
            return _Detection(False, f"beat held: {held.reason}", held=True)
        if outcome.held:
            return _Detection(False, f"held: {outcome.reason}", held=True)
        expected = record.planted["expected"]
        if "judgment" in record.planted:
            hit = _judgment_kept(record, outcome, [_beat(record, unit)])
        elif expected == "new":  # shared_element: caught only when nothing folds
            hit = bool(outcome.claims) and all(c.outcome == "new" for c in outcome.claims)
        elif expected == "covered":  # covered_union: covered by exactly the planted claims
            hit = any(
                c.outcome == "covered"
                and sorted(ref[1] for ref in c.covered_by) == sorted(record.planted["claim_ids"])
                for c in outcome.claims
            )
        else:
            hit = any(
                c.existing_claim_id == planted_id and c.outcome == expected
                for c in outcome.claims
            )
        note = "; ".join(
            f"{c.claim_id} {c.outcome} "
            + ("+".join(ref[1] for ref in c.covered_by) or c.existing_claim_id or "-")
            + (f" [{', '.join(c.new_judgments)}]" if c.new_judgments else "")
            for c in outcome.claims
        )
        # A miss that folded something CHANGED the corpus; a miss where every
        # claim came out `new` left it exactly as it was. Sound only while a
        # record plants ONE claim (`_second_source` builds one, asserted
        # below): with two, a caught row could hide a wrong fold on the other.
        assert len(outcome.claims) <= 1, "wrong_fold assumes one judged claim per record"
        folded = any(c.outcome != "new" for c in outcome.claims)
        return _Detection(hit, note, wrong_fold=not hit and folded)
    try:
        if record.detector == "judge_claims":
            judge_claims.judge_claims(
                _story(record), _drafts(record, unit), unit, client, record_event
            )
            expected_kind = record.planted.get("expected_kind")
            if expected_kind is not None:
                # A kind plant (state_as_event) is caught by RE-KINDING: the
                # judge read the expected kind and the claim came out with it.
                rekinds = [
                    payload
                    for kind, payload in events
                    if kind == "claim_rekinded" and payload["claim_id"] == planted_id
                ]
                caught = any(p["to"] == expected_kind for p in rekinds)
                note = (
                    f"re-kinded {rekinds[0]['from']}→{rekinds[0]['to']}"
                    if rekinds
                    else f"not re-kinded (expected {expected_kind})"
                )
            else:
                refused = [
                    payload
                    for kind, payload in events
                    if kind == "claim_refused"
                    and payload["attempt"] == 1
                    and payload["claim_id"] == planted_id
                ]
                # A plant the second look clears survives as written: missed.
                cleared = [
                    payload
                    for kind, payload in events
                    if kind == "claim_rejudged"
                    and payload["claim_id"] == planted_id
                    and payload["entailed"]
                ]
                caught = bool(refused) and not cleared
                note = (
                    f"cleared on the second look: {cleared[0]['reason']}"
                    if cleared
                    else refused[0]["reason"] if refused else "not refused"
                )
        else:
            judge_claims.omissions(unit, _drafts(record, unit), client, record_event)
            expected = record.planted.get("expected")
            if expected in ("compound", "atomic"):
                # compound_claim / stated_relation: the coverage check's
                # compound flag, recall and precision.
                flagged = [
                    claim["claim_id"]
                    for kind, payload in events
                    if kind == "compound_found"
                    for claim in payload["claims"]
                ]
                named = planted_id in flagged
                caught = named if expected == "compound" else not named
                note = f"flagged {flagged}" if flagged else "nothing flagged"
                false_refusals, dropped, details = _precision(events, planted_id)
                return _Detection(caught, note, false_refusals, dropped, details)
            spans = [
                span
                for kind, payload in events
                if kind == "omissions_found"
                for span in payload["spans"]
            ]
            hit = [span for span in spans if _overlaps(span, record.planted["span"])]
            caught = bool(hit)
            note = hit[0] if hit else f"{len(spans)} grounded finding(s), none on the span"
        unit_held = False
    except UnitHeld as held:
        caught, note, unit_held = False, f"unit held: {held.reason}", True
    false_refusals, dropped, details = _precision(events, planted_id)
    return _Detection(caught, note, false_refusals, dropped, details, held=unit_held)


def _live_client(events: llm.EventSink) -> llm.ModelClient:
    return llm.AnthropicClient(events)


def run(
    fixture: Fixture,
    books_root: Path,
    client: str = "mock",
    events: llm.EventSink | None = None,
    confirm: Callable[[llm.CostEstimate], bool] | None = None,
    scripted: Callable[[DefectRecord, Unit], dict[str, llm.MockAnswer]] = scripted_answers,
    scripted_merge: Callable[[DefectRecord, Unit], llm.MockAnswer] = scripted_merge_answer,
    scripted_narration: Callable[
        [DefectRecord, Unit],
        tuple[dict[str, list[llm.MockAnswer]], dict[str, llm.MockAnswer]],
    ] = scripted_narration,
    wrap_live: Callable[[llm.ModelClient], llm.ModelClient] | None = None,
) -> Report:
    """Run every record through its detector and tally per class.

    `client="mock"`: one MockClient per record (records share claim ids,
    so their batch custom_ids would collide on one mock), scripted by
    `scripted(record, unit)` — `scripted_answers` by default — and, for a
    merge record, by `scripted_merge(record, unit)`; a test passes its own
    to exercise the harness on a misbehaving judge.
    `client="live"`: one AnthropicClient, its estimate printed before any
    call; `confirm(estimate)` returning False stops the run with every
    judged class reported as not run (caught None, detector unchanged).
    `wrap_live(client)`, when given, replaces the live client before any
    call — the caller's meter, so a paid run can print what it spent.
    """
    if client not in ("mock", "live"):
        raise ValueError(f"client must be 'mock' or 'live', got {client!r}")
    sink: llm.EventSink = events or (lambda _kind, _payload: None)
    unit = load_fixture_unit(books_root, fixture)

    live: llm.ModelClient | None = None
    declined = False
    if client == "live":
        live = _live_client(sink)
        if wrap_live is not None:
            live = wrap_live(live)
        estimate = _arm(live, fixture.records, unit)
        if confirm is not None and not confirm(estimate):
            declined = True

    rows: list[ClassRow] = []
    for record in fixture.records:
        is_scripted = client == "mock" and record.detector in (
            "judge_claims",
            "omissions",
            "merge",
            "narration",
        )
        if record.detector == "pending":
            rows.append(ClassRow(record.defect_class, "pending", None, None, False, record.note))
            continue
        if declined and record.detector not in ("gates", "narration_gates"):
            rows.append(
                ClassRow(record.defect_class, record.detector, None, None, False, "not run")
            )
            continue
        per_record_client: llm.ModelClient | None = live
        if client == "mock" and record.detector in ("judge_claims", "omissions"):
            mock = llm.MockClient(sink, batch_answers=scripted(record, unit))
            _arm(mock, [record], unit)
            per_record_client = mock
        elif client == "mock" and record.detector == "merge":
            mock = llm.MockClient(sink, answers={"merge_judge": [scripted_merge(record, unit)]})
            _arm(mock, [record], unit)
            per_record_client = mock
        elif client == "mock" and record.detector == "narration":
            answers, batch = scripted_narration(record, unit)
            mock = llm.MockClient(sink, answers=answers, batch_answers=batch)
            _arm(mock, [record], unit)
            per_record_client = mock
        found = _detect(record, unit, per_record_client, sink)
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
                found.held,
                found.wrong_fold,
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
            + ("   HELD" if row.held else "")
            + ("   WRONG FOLD" if row.wrong_fold else "")
        )
    # A 0/1 means two different things: the pipeline HELD (nothing written,
    # a review item) or it APPLIED a wrong answer (the corpus changed).
    holds = [(row.defect_class, row.note) for row in report.rows if row.held]
    if holds:
        lines.append("held (nothing written, queued for review):")
        lines.extend(
            f"  {name}: {note.split('held: ', 1)[-1]}" for name, note in holds
        )
    folds = [(row.defect_class, row.note) for row in report.rows if row.wrong_fold]
    if folds:
        lines.append("wrong folds (the corpus changed):")
        lines.extend(f"  {name}: {note}" for name, note in folds)
    details = [(row.defect_class, d) for row in report.rows for d in row.details]
    if details:
        lines.append("details (every refusal and drop, in order):")
        lines.extend(f"  {defect_class}: {detail}" for defect_class, detail in details)
    if report.client == "mock":
        lines.append(
            "MOCK: the judge_claims, omissions, merge and narration classes are scripted from the "
            "fixture's planted block — a harness check, not a measurement; only "
            "the gates and narration_gates classes are measured. Run "
            "`make ingest-calibrate-live` to measure."
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
