"""Tests for src/ingest/calibrate.py — Docs/ingestion/rebuild-spec.md §4.

Calibration by defect injection: ten hand-read records over the Lonely
Planet Guggenheim passage, one planted defect each, run through the
judge phases that exist so far. Under llm.MockClient the judged classes
are SCRIPTED from the fixture's `planted` block — the run proves the
harness (routing, tallying, printing, the baseline rule), not the judge —
while the mechanical classes (lift, guidebook attribution) are measured by
the real gates. The live numbers are the owner's to produce
(`make ingest-calibrate-live`); nothing here spends.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from src.ingest import calibrate, gates
from src.ingest import unit as unit_mod

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "fixtures" / "ingestion" / "defects.json"
BASELINE = REPO_ROOT / "fixtures" / "ingestion" / "calibration-baseline.json"

#: The defect classes §4 names, in its order (the eleventh and twelfth,
#: shared_element and same_fact, added in slice 10: the precision and the
#: recall side of the merge judge's `same`).
CLASSES = (
    "fabricated_date",
    "deleted_claim",
    "wrong_cause",
    "lift",
    "guidebook_attribution",
    "framing_sentence",
    "state_as_event",
    "contested_value",
    "superseded_belief",
    "omitted_fact",
    "shared_element",
    "same_fact",
)


def _fixture() -> calibrate.Fixture:
    return calibrate.load_fixture(FIXTURE)


def _unit(fixture: calibrate.Fixture) -> unit_mod.Unit:
    return calibrate.load_fixture_unit(REPO_ROOT / "Books", fixture)


def test_fixture_has_one_record_per_class_and_only_the_planted_defect():
    """Fixture pins: exactly the §4 classes, one record each; every
    unplanted claim passes the real P1 gates against the real unit; every
    planted span is verbatim in the unit; a mechanical class's planted
    claim trips exactly the gate it names and nothing else."""
    fixture = _fixture()
    unit = _unit(fixture)

    assert [record.defect_class for record in fixture.records] == list(CLASSES)
    assert {record.detector for record in fixture.records} <= {
        "judge_claims",
        "gates",
        "omissions",
        "narration_gates",
        "merge",
        "pending",
    }

    for record in fixture.records:
        planted_claim_id = record.planted.get("claim_id")
        for claim in record.claims:
            assert gates.span_in_unit(claim["span"], unit.text) is None, (
                record.defect_class,
                claim["claim_id"],
            )
            if claim["claim_id"] == planted_claim_id and record.detector == "gates":
                reasons = gates.claim_gates(claim["text"], claim["span"], unit.text)
                assert len(reasons) == 1, (record.defect_class, reasons)
                assert reasons[0].startswith(record.planted["gate"]), (record.defect_class, reasons)
                continue
            assert gates.claim_gates(claim["text"], claim["span"], unit.text) == [], (
                record.defect_class,
                claim["claim_id"],
            )
        if record.detector == "narration_gates":
            from src.ingest import narrate

            reasons = narrate.narration_gates(
                record.narration, [claim["span"] for claim in record.claims]
            )
            assert len(reasons) == 1, (record.defect_class, reasons)
            assert reasons[0].startswith(record.planted["gate"]), (record.defect_class, reasons)
        if "span" in record.planted:
            assert gates.span_in_unit(record.planted["span"], unit.text) is None, (
                record.defect_class
            )
        if "corrected" in record.planted:
            corrected = record.planted["corrected"]
            assert gates.claim_gates(corrected["text"], corrected["span"], unit.text) == []
        if record.detector == "merge" and record.planted["expected"] in ("new", "same"):
            # shared_element: the plant shares an element, not a fact, so the
            # signature must NOT match it either — else the tripwire would hold
            # a correct judge's `new` and the harness would measure a hold.
            # same_fact: a paraphrase the signature cannot see, so the class
            # measures the judge's recall and nothing else.
            from src.ingest import merge

            planted = next(c for c in record.claims if c["claim_id"] == planted_claim_id)
            beat = merge._signature(planted["text"])
            second = merge._signature(record.planted["second_source"]["text"])
            assert merge._overlap(beat, second) < merge.SIGNATURE_MATCH_MIN, record.defect_class
        elif record.detector == "merge":
            # The planted second source must state the SAME fact as the planted
            # claim by the signature test P6 applies, or the hint would call
            # it new and the harness would measure a hold, not the detector.
            from src.ingest import merge

            planted = next(c for c in record.claims if c["claim_id"] == planted_claim_id)
            beat = merge._signature(planted["text"])
            second = merge._signature(record.planted["second_source"]["text"])
            assert merge._overlap(beat, second) >= merge.SIGNATURE_MATCH_MIN, record.defect_class
            assert record.planted["expected"] in ("contested", "supersedes"), record.defect_class
            assert set(record.planted["stated_values"]) == {
                unit.source_id,
                record.planted["second_source"]["source_id"],
            }


def test_mock_run_reports_a_rate_per_class_with_pending_detectors_marked():
    """The harness under MockClient: every class with a detector reports
    caught/total (fabricated_date and deleted_claim at 100%, the spec's
    proving line); a class whose detector lands in a later slice reports
    pending, never missed; the printout says MOCK and that judged classes
    are scripted."""
    fixture = _fixture()
    report = calibrate.run(fixture, REPO_ROOT / "Books", client="mock")

    by_class = {row.defect_class: row for row in report.rows}
    assert list(by_class) == list(CLASSES)

    for name in (
        "fabricated_date",
        "deleted_claim",
        "wrong_cause",
        "omitted_fact",
        "contested_value",
        "superseded_belief",
    ):
        assert (by_class[name].caught, by_class[name].total) == (1, 1), name
        assert by_class[name].scripted is True, name
    for name in ("contested_value", "superseded_belief"):
        assert by_class[name].detector == "merge", name
    assert "contested" in by_class["contested_value"].note
    assert "supersedes" in by_class["superseded_belief"].note
    for name in ("lift", "guidebook_attribution", "framing_sentence"):
        assert (by_class[name].caught, by_class[name].total) == (1, 1), name
        assert by_class[name].scripted is False, name
    assert by_class["framing_sentence"].detector == "narration_gates"
    assert "framing" in by_class["framing_sentence"].note
    # Slice 9 (slice-6 ruling 5): the P3 call carries the KIND question, so a
    # state claim kinded as event is caught by RE-KINDING — scripted on the
    # mock like every judged class, no longer pending.
    assert by_class["state_as_event"].detector == "judge_claims"
    assert (by_class["state_as_event"].caught, by_class["state_as_event"].total) == (1, 1)
    assert by_class["state_as_event"].scripted is True
    assert "re-kinded" in by_class["state_as_event"].note
    assert (by_class["state_as_event"].false_refusals, by_class["state_as_event"].dropped) == (0, 0)

    text = calibrate.format_report(report)
    assert "MOCK" in text
    assert "scripted" in text
    for name in CLASSES:
        assert name in text
    assert "fabricated_date" in text and "100%" in text
    assert "pending" not in text  # every class has a detector now


def test_a_class_below_its_baseline_fails_and_pending_never_does():
    """§4: a judge prompt change that lowers a class's catch rate fails
    the target. A class below the baseline is a regression; equal or
    above is not; a pending class (null baseline) and a class the
    baseline has never seen are never regressions."""
    baseline = {
        "fabricated_date": {"caught": 1, "total": 1},
        "lift": {"caught": 1, "total": 1},
        "framing_sentence": None,
    }
    rows = [
        calibrate.ClassRow("fabricated_date", "judge_claims", 0, 1, scripted=True),
        calibrate.ClassRow("lift", "gates", 1, 1, scripted=False),
        calibrate.ClassRow("framing_sentence", "pending", None, None, scripted=False),
        calibrate.ClassRow("brand_new", "gates", 0, 1, scripted=False),
    ]
    report = calibrate.Report(client="mock", rows=rows)

    regressions = calibrate.regressions(report, baseline)

    assert regressions == ["fabricated_date: 0/1 is below the baseline 1/1"]

    # The committed baseline for the mock client is what the mock run
    # produces today, so the target exits 0 on a clean checkout.
    baselines = json.loads(BASELINE.read_text())
    committed = baselines["mock"]
    live_report = calibrate.run(_fixture(), REPO_ROOT / "Books", client="mock")
    assert calibrate.regressions(live_report, committed) == []
    assert calibrate.baseline_from(live_report) == committed

    # Every recorded client carries its run provenance, so a future reader
    # can tell a measured block from a hand-copied one.
    import src.ingest.llm as llm

    # The mock block is regenerable for $0, so it must match the current
    # roles table. The live block is a historical record of a paid run:
    # it must carry its models and say how it was stamped, and is never
    # pinned to current config (that would invite rewriting history).
    assert baselines["_runs"]["mock"]["models"] == dict(llm.ROLE_MODEL)
    assert baselines["_runs"]["mock"]["recorded_at"] >= "2026-09-11"
    live_run = baselines["_runs"]["live"]
    assert set(live_run["models"]) == set(llm.ROLE_MODEL)
    assert live_run["recorded_at"] == "2026-09-11"
    assert "stamped after the fact" in live_run["note"]


def test_no_live_client_in_this_file():
    """This $0-spend test file never names a live LLM client or reads its
    API key. Its own body is exempt from the walk below — this docstring
    and the assert messages name those things on purpose to describe the
    rule, which is not the violation the rule guards against.
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    self_name = "test_no_live_client_in_this_file"
    forbidden_names = {"AnthropicClient", "anthropic"}
    forbidden_env_var = "ANTHROPIC_API_KEY"

    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == self_name:
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name):
                assert sub.id not in forbidden_names, f"{sub.id!r} must not appear in this file"
            elif isinstance(sub, ast.Attribute):
                assert sub.attr not in forbidden_names, (
                    f"{sub.attr!r} must not appear in this file"
                )
            elif isinstance(sub, ast.ImportFrom) and sub.module:
                assert sub.module.split(".")[0] not in forbidden_names, (
                    f"from-import of {sub.module!r} must not appear in this file"
                )
            elif isinstance(sub, ast.alias):
                top_level_name = sub.name.split(".")[0]
                assert top_level_name not in forbidden_names, (
                    f"import of {sub.name!r} must not appear in this file"
                )
                assert sub.asname not in forbidden_names, (
                    f"import alias {sub.asname!r} must not appear in this file"
                )
            elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                assert forbidden_env_var not in sub.value, (
                    f"{forbidden_env_var!r} must not appear in this file"
                )


def test_confirm_sees_the_whole_estimate_and_declined_rows_say_not_run():
    """The estimate handed to `confirm` is the SUM of every plan armed (the
    judge rows, the omissions row and the merge row), never only the last one — an owner
    deciding whether to spend must see the whole ceiling. A judged row a
    declined confirm left unrun prints 'not run', never 'pending' (which
    means 'no detector exists yet')."""
    import src.ingest.llm as llm

    fixture = _fixture()
    unit = _unit(fixture)
    events: list[tuple[str, dict]] = []
    mock = llm.MockClient(lambda kind, payload: events.append((kind, payload)))

    estimate = calibrate._arm(mock, fixture.records, unit)

    printed = [payload for kind, payload in events if kind == "cost_estimate"]
    assert len(printed) == 3  # the P3 plan, the omissions plan, the P6 plan (slice 6)
    assert estimate.total_usd == sum(p["total_usd"] for p in printed)
    assert estimate.total_input_tokens == sum(p["total_input_tokens"] for p in printed)
    assert len(estimate.rows) == sum(len(p["rows"]) for p in printed)

    rows = [
        calibrate.ClassRow("fabricated_date", "judge_claims", None, None, False, "not run"),
        calibrate.ClassRow("framing_sentence", "pending", None, None, False),
    ]
    text = calibrate.format_report(calibrate.Report(client="live", rows=rows))
    fabricated_line = next(line for line in text.splitlines() if line.startswith("fabricated_date"))
    assert "not run" in fabricated_line and "pending" not in fabricated_line
    framing_line = next(line for line in text.splitlines() if line.startswith("framing_sentence"))
    assert "pending" in framing_line


def test_precision_counts_clean_claims_refused_and_claims_dropped_with_reasons():
    """Catch rate alone rewards a judge that refuses everything. Every
    judged record also reports how many CLEAN claims the judge refused on
    attempt one (false refusals) and how many claims were dropped, and the
    report prints each reason. The fixture's own script has none of
    either; a script that also refuses a clean claim whose restate then
    trips the lift gate shows one of each."""
    import src.ingest.llm as llm
    from src.ingest import judge_claims

    fixture = _fixture()
    clean = calibrate.run(fixture, REPO_ROOT / "Books", client="mock")
    for row in clean.rows:
        if row.detector in ("judge_claims", "omissions"):
            assert (row.false_refusals, row.dropped) == (0, 0), row.defect_class
        else:
            assert (row.false_refusals, row.dropped) == (None, None), row.defect_class

    record = next(r for r in fixture.records if r.defect_class == "fabricated_date")
    clean_id = record.claims[0]["claim_id"]
    leaking_restate = {
        "text": (
            "According to the guidebook, Solomon R Guggenheim began collecting abstract art late."
        ),
        "kind": "event",
        "span": record.claims[0]["span"],
    }

    def over_strict(rec: calibrate.DefectRecord, u) -> dict[str, llm.MockAnswer]:
        answers = calibrate.scripted_answers(rec, u)
        if rec is record:
            answers[judge_claims.judge_custom_id(u, clean_id, 1)] = llm.MockAnswer(
                text=(
                    '{"entailed": false, "reason": "the span does not say sixties",'
                    ' "kind": "event"}'
                ),
                model_id=llm.ROLE_MODEL["claim_judge"],
            )
            answers[judge_claims.restate_custom_id(u, clean_id)] = llm.MockAnswer(
                text=json.dumps(leaking_restate), model_id=llm.ROLE_MODEL["author"]
            )
        return answers

    report = calibrate.run(fixture, REPO_ROOT / "Books", client="mock", scripted=over_strict)
    row = next(r for r in report.rows if r.defect_class == "fabricated_date")
    assert (row.caught, row.total) == (1, 1)
    assert (row.false_refusals, row.dropped) == (1, 1)
    assert any(clean_id in d and "the span does not say sixties" in d for d in row.details)
    assert any(clean_id in d and d.startswith("dropped") and "leak" in d for d in row.details)

    text = calibrate.format_report(report)
    assert "false_ref" in text and "dropped" in text
    assert "the span does not say sixties" in text

    # Precision regresses the baseline too: more false refusals or drops
    # than recorded is a regression; a baseline without those keys (the
    # first live run) compares catch rate only.
    strict_baseline = {
        "fabricated_date": {"caught": 1, "total": 1, "false_refusals": 0, "dropped": 0}
    }
    assert calibrate.regressions(report, strict_baseline) == [
        "fabricated_date: 1 false refusal(s) above the baseline 0",
        "fabricated_date: 1 dropped claim(s) above the baseline 0",
    ]
    assert calibrate.regressions(report, {"fabricated_date": {"caught": 1, "total": 1}}) == []
    assert calibrate.baseline_from(report)["fabricated_date"] == {
        "caught": 1,
        "total": 1,
        "false_refusals": 1,
        "dropped": 1,
    }
    assert calibrate.baseline_from(report)["lift"] == {"caught": 1, "total": 1}


def test_a_merge_judge_the_signature_contradicts_is_held_and_counts_as_missed():
    """The merge classes measure P6 end to end: a scripted judge that calls
    the second source a new story while the signature matches the planted
    claim is a disagreement, so the story is held (D7) and the class is
    MISSED with the hold spelled out — the harness never counts a hold as
    a catch. The default script catches both classes."""
    import src.ingest.llm as llm

    fixture = _fixture()

    def contrary(rec: calibrate.DefectRecord, u) -> llm.MockAnswer:
        answer = {
            "story": "new",
            "beat_id": "",
            "claims": [
                {"claim_id": "n01", "verdict": "new", "existing_claim_id": "",
                 "new_value": "", "existing_value": "", "reason": "nothing like it"}
            ],
        }
        return llm.MockAnswer(text=json.dumps(answer), model_id=llm.ROLE_MODEL["merge_judge"])

    report = calibrate.run(fixture, REPO_ROOT / "Books", client="mock", scripted_merge=contrary)
    by_class = {row.defect_class: row for row in report.rows}
    for name in ("contested_value", "superseded_belief"):
        assert (by_class[name].caught, by_class[name].total) == (0, 1), name
        assert by_class[name].note.startswith("held: judge and signature disagree"), name
        assert (by_class[name].false_refusals, by_class[name].dropped) == (None, None), name

    clean = calibrate.run(fixture, REPO_ROOT / "Books", client="mock")
    by_class = {row.defect_class: row for row in clean.rows}
    assert by_class["contested_value"].note == "n01 contested c01"
    assert by_class["superseded_belief"].note == "n01 supersedes c01"


def test_a_claim_sharing_only_a_date_is_new_and_a_judge_that_folds_it_misses():
    """Slice 10's Guggenheim replay folded "this 1959 masterpiece" (a claim
    about walking the ramps) into "completed in 1959" in both runs. The
    shared_element class plants exactly that shape: a second source sharing
    the record's date but stating another fact. A correct judge answers
    `new` and nothing folds — caught; a judge that answers `same` folds a
    different fact into the record's claim — MISSED, with the fold named."""
    import src.ingest.llm as llm

    fixture = _fixture()
    record = next(r for r in fixture.records if r.defect_class == "shared_element")
    assert record.detector == "merge"
    assert record.planted["expected"] == "new"

    report = calibrate.run(fixture, REPO_ROOT / "Books", client="mock")
    row = next(r for r in report.rows if r.defect_class == "shared_element")
    assert (row.caught, row.total) == (1, 1)
    assert row.note == "n01 new -"

    def folding(rec: calibrate.DefectRecord, u) -> llm.MockAnswer:
        if rec.defect_class != "shared_element":
            return calibrate.scripted_merge_answer(rec, u)
        planted = rec.planted
        answer = {
            "story": "new",
            "beat_id": "",
            "claims": [
                {"claim_id": "n01", "verdict": "same",
                 "existing_claim_id": f"b1.{planted['claim_id']}",
                 "new_value": "1959", "existing_value": "1959",
                 "reason": "both reference the same 1959 date"}
            ],
        }
        return llm.MockAnswer(text=json.dumps(answer), model_id=llm.ROLE_MODEL["merge_judge"])

    report = calibrate.run(fixture, REPO_ROOT / "Books", client="mock", scripted_merge=folding)
    row = next(r for r in report.rows if r.defect_class == "shared_element")
    assert (row.caught, row.total) == (0, 1)
    assert row.note == f"n01 same {record.planted['claim_id']}"


def test_a_true_paraphrase_folds_and_a_judge_that_calls_it_new_misses():
    """The recall side of slice 10's containment rule: a stricter `same` must
    not bring back slice 9's false `new` on real duplicates (0 merges of 12).
    same_fact plants a second source that restates the record's claim in
    other words — a signature cannot see it, so only the judge can fold it.
    A correct judge answers `same` and the claim gains the second source —
    caught; a judge that answers `new` leaves it unmerged — MISSED."""
    import src.ingest.llm as llm

    fixture = _fixture()
    record = next(r for r in fixture.records if r.defect_class == "same_fact")
    assert record.detector == "merge"
    assert record.planted["expected"] == "same"

    report = calibrate.run(fixture, REPO_ROOT / "Books", client="mock")
    row = next(r for r in report.rows if r.defect_class == "same_fact")
    assert (row.caught, row.total) == (1, 1)
    assert row.note == f"n01 same {record.planted['claim_id']}"

    def splitting(rec: calibrate.DefectRecord, u) -> llm.MockAnswer:
        if rec.defect_class != "same_fact":
            return calibrate.scripted_merge_answer(rec, u)
        answer = {
            "story": "new",
            "beat_id": "",
            "claims": [
                {"claim_id": "n01", "verdict": "new", "existing_claim_id": "",
                 "new_value": "", "existing_value": "", "reason": "worded differently"}
            ],
        }
        return llm.MockAnswer(text=json.dumps(answer), model_id=llm.ROLE_MODEL["merge_judge"])

    report = calibrate.run(fixture, REPO_ROOT / "Books", client="mock", scripted_merge=splitting)
    row = next(r for r in report.rows if r.defect_class == "same_fact")
    assert (row.caught, row.total) == (0, 1)
