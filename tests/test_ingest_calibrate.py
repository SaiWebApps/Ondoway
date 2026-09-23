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

from src.ingest import calibrate, gates, judge_claims
from src.ingest import unit as unit_mod

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "fixtures" / "ingestion" / "defects.json"
BASELINE = REPO_ROOT / "fixtures" / "ingestion" / "calibration-baseline.json"

#: The defect classes §4 names, in its order (slice 10 added four: the
#: precision and recall sides of the merge judge's `same`, shared_element and
#: same_fact; and of the coverage check's compound flag, stated_relation and
#: compound_claim).
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
    "compound_claim",
    "stated_relation",
    "covered_union",
    "shared_union",
    "adds_a_date",
    "recombined_union",
    "negated_fact",
    "covered_union_elsewhere",
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
        # Slice 10: two records copied claims from others and collided on
        # ids (and pointed the plant at the wrong claim) — every record's
        # ids are unique, its story lists exactly them, and its plant names
        # one of them.
        ids = [claim["claim_id"] for claim in record.claims]
        assert len(ids) == len(set(ids)), (record.defect_class, ids)
        assert sorted(record.story["claim_ids"]) == sorted(ids), record.defect_class
        if "claim_id" in record.planted:
            assert record.planted["claim_id"] in ids, record.defect_class
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
            assert merge.covered(second, beat) < merge.SIGNATURE_MATCH_MIN, record.defect_class
            if "claim_ids" in record.planted:
                # shared_union: no single claim covers it (no hold), but their
                # union does — the lexical trap a careless `covered` falls into.
                union = frozenset().union(*(
                    merge._signature(c["text"]) for c in record.claims
                    if c["claim_id"] in record.planted["claim_ids"]
                ))
                singles = [merge.covered(second, merge._signature(c["text"]))
                           for c in record.claims]
                assert max(singles) < merge.SIGNATURE_MATCH_MIN, record.defect_class
                assert merge.covered(second, union) >= merge.SIGNATURE_MATCH_MIN, (
                    record.defect_class
                )
        elif record.detector == "merge" and record.planted["expected"] == "covered":
            # covered_union: the signature's best match must be INSIDE the
            # planted pair, or the tripwire would hold a correct judge.
            from src.ingest import merge

            second = merge._signature(record.planted["second_source"]["text"])
            scores = {
                c["claim_id"]: merge.covered(second, merge._signature(c["text"]))
                for c in record.claims
            }
            best = max(scores, key=scores.get)
            assert scores[best] >= merge.SIGNATURE_MATCH_MIN, record.defect_class
            assert best in record.planted["claim_ids"], record.defect_class
        elif record.detector == "merge":
            # The planted second source must state the SAME fact as the planted
            # claim by the signature test P6 applies, or the hint would call
            # it new and the harness would measure a hold, not the detector.
            from src.ingest import merge

            planted = next(c for c in record.claims if c["claim_id"] == planted_claim_id)
            beat = merge._signature(planted["text"])
            second = merge._signature(record.planted["second_source"]["text"])
            if record.planted.get("hint") == "silent":
                # Declared exception: this plant recombines the claims' own
                # words, so no single claim reaches the threshold and the
                # tripwire says nothing BY DESIGN — the class measures the
                # judge alone. Pinned, so a plant cannot drift into silence.
                assert merge.covered(second, beat) < merge.SIGNATURE_MATCH_MIN, (
                    record.defect_class
                )
            else:
                assert merge.covered(second, beat) >= merge.SIGNATURE_MATCH_MIN, (
                    record.defect_class
                )
            assert record.planted["expected"] in ("contested", "supersedes"), record.defect_class
            assert set(record.planted["stated_values"]) == {
                unit.source_id,
                record.planted["second_source"]["source_id"],
            }

        if record.detector == "merge" and "guard" in record.planted:
            # What the leftover-words guard (merge.covered_leftovers) would do
            # to this plant if the judge wrongly answered `covered`:
            # "catches" = words are left over, so the story holds; "blind" =
            # the plant only recombines the named claims' own words into a
            # different fact, which the guard cannot see (recorded 2026-09-22,
            # not fixed — a precision gap the plants now measure).
            from src.ingest import merge

            stated = merge._signature(record.story["place"]).union(*(
                merge._signature(c["text"]) for c in record.claims
                if c["claim_id"] in record.planted["claim_ids"]
            ))
            left = merge._signature(record.planted["second_source"]["text"]) - stated
            assert bool(left) == (record.planted["guard"] == "catches"), (
                record.defect_class, sorted(left)
            )


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
            answers[judge_claims.rejudge_custom_id(u, clean_id)] = llm.MockAnswer(
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



def test_a_plant_the_second_look_clears_counts_as_missed():
    """A refused claim is now judged once more as written before any
    restate (job B, 2026-09-21). A planted fabrication the first look
    refuses but the second look CLEARS survives into the corpus, so it is
    MISSED — the harness must never score a first refusal as a catch when
    the claim then went through. The default script refuses it twice."""
    import src.ingest.judge_claims as judge_claims
    import src.ingest.llm as llm

    fixture = _fixture()
    refusal_classes = [
        r.defect_class for r in fixture.records
        if r.detector == "judge_claims" and r.planted.get("expected_kind") is None
    ]
    assert refusal_classes, "the fixture carries claim-judge refusal plants"
    real = calibrate.scripted_answers

    def cleared_on_second_look(record, unit):
        answers = real(record, unit)
        if record.detector == "judge_claims" and record.planted.get("expected_kind") is None:
            answers[judge_claims.rejudge_custom_id(unit, record.planted["claim_id"])] = (
                llm.MockAnswer(
                    text=json.dumps(
                        {"entailed": True, "reason": "the span states it", "kind": "event"}
                    ),
                    model_id=llm.ROLE_MODEL["claim_judge"],
                )
            )
        return answers

    report = calibrate.run(
        fixture, REPO_ROOT / "Books", client="mock", scripted=cleared_on_second_look
    )
    by_class = {row.defect_class: row for row in report.rows}
    for name in refusal_classes:
        assert (by_class[name].caught, by_class[name].total) == (0, 1), name

    clean = calibrate.run(fixture, REPO_ROOT / "Books", client="mock")
    by_class = {row.defect_class: row for row in clean.rows}
    for name in refusal_classes:
        assert (by_class[name].caught, by_class[name].total) == (1, 1), name

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


def test_the_coverage_check_flags_a_compound_claim_and_leaves_a_stated_relation_whole():
    """Slice 10 step 2: the P3 coverage check now names claims that state
    more than one fact. compound_claim plants "designed by Wright AND almost
    overshadows the art" as one claim — caught when the check names it.
    stated_relation plants the passage's own "the cramped elevator makes the
    descent difficult, THEREFORE exhibitions are installed bottom to top" —
    one fact by CONTEXT.md's rule, caught only when the check does NOT name
    it. A check that flags nothing misses the first; one that flags every
    long claim misses the second."""
    import src.ingest.llm as llm

    fixture = _fixture()
    for name, expected in (("compound_claim", "compound"), ("stated_relation", "atomic")):
        record = next(r for r in fixture.records if r.defect_class == name)
        assert (record.detector, record.planted["expected"]) == ("omissions", expected)
    # Pin each plant to the claim it is about, by text, so a plant pointing at
    # the wrong claim cannot score: the relation is superseded_belief's
    # "... therefore installed from bottom to top", the compound is Wright +
    # "almost overshadows".
    def planted_text(name: str) -> str:
        record = next(r for r in fixture.records if r.defect_class == name)
        return next(c["text"] for c in record.claims if c["claim_id"] == record.planted["claim_id"])

    belief = next(r for r in fixture.records if r.defect_class == "superseded_belief")
    assert planted_text("stated_relation") == next(
        c["text"] for c in belief.claims if "bottom to top" in c["text"]
    )
    assert "and almost overshadows" in planted_text("compound_claim")

    report = calibrate.run(fixture, REPO_ROOT / "Books", client="mock")
    rows = {r.defect_class: r for r in report.rows}
    assert (rows["compound_claim"].caught, rows["compound_claim"].total) == (1, 1)
    assert (rows["stated_relation"].caught, rows["stated_relation"].total) == (1, 1)

    def judge_flagging(flag_all: bool):
        def scripted(rec: calibrate.DefectRecord, u) -> dict:
            answers = calibrate.scripted_answers(rec, u)
            if rec.planted.get("expected") in ("compound", "atomic"):
                flagged = (
                    [{"claim_id": c["claim_id"], "facts": ["x", "y"]} for c in rec.claims]
                    if flag_all else []
                )
                answers[judge_claims.omissions_custom_id(u)] = llm.MockAnswer(
                    text=json.dumps({"omitted": [], "compound": flagged}),
                    model_id=llm.ROLE_MODEL["claim_judge"],
                )
            return answers
        return scripted

    rows = {r.defect_class: r for r in calibrate.run(
        fixture, REPO_ROOT / "Books", client="mock", scripted=judge_flagging(False)).rows}
    assert rows["compound_claim"].caught == 0 and rows["stated_relation"].caught == 1
    rows = {r.defect_class: r for r in calibrate.run(
        fixture, REPO_ROOT / "Books", client="mock", scripted=judge_flagging(True)).rows}
    assert rows["compound_claim"].caught == 1 and rows["stated_relation"].caught == 0


def test_every_detector_verdict_reaches_the_run_sink_tagged_with_its_class():
    """The judge's PROVE-FIRST before the step-2 live calibration: the report
    prints a bare caught/total, so a live 0/1 would carry no evidence — no
    flagged ids, no bundled facts, no merge answer — and invite a second
    paid run "to see why". Each detector's evidence events (the coverage
    check's compound_found / compound_unknown / omissions_found /
    omission_ungrounded, the merge's merge_answered / merge_folded) now
    reach the run's event sink, each tagged with the class it belongs to."""
    events: list[tuple[str, dict]] = []
    calibrate.run(
        _fixture(), REPO_ROOT / "Books", client="mock",
        events=lambda kind, payload: events.append((kind, payload)),
    )
    tagged = [(kind, payload["class"]) for kind, payload in events if "class" in payload]
    assert ("compound_found", "compound_claim") in tagged
    assert ("omissions_found", "omitted_fact") in tagged
    assert ("merge_answered", "same_fact") in tagged
    assert ("merge_folded", "same_fact") in tagged
    found = next(p for k, p in events if k == "compound_found" and p["class"] == "compound_claim")
    assert [c["claim_id"] for c in found["claims"]] == ["c01"]
    assert all(c == "compound_claim" for k, c in tagged if k == "compound_found")




def test_a_claim_two_claims_state_together_is_covered_and_one_sharing_their_words_is_not():
    """The `covered` verdict's two sides (owner ruling 2026-09-21): recall —
    a second source stating the Wright claim and the spiral-ramp claim
    together comes out `covered` by exactly that pair; precision — one that
    shares their words but states another fact (sketched for cars) stays
    `new`. A judge that answers `covered` on the precision plant MISSES it."""
    import src.ingest.llm as llm

    fixture = _fixture()
    clean = calibrate.run(fixture, REPO_ROOT / "Books", client="mock")
    by_class = {row.defect_class: row for row in clean.rows}
    assert (by_class["covered_union"].caught, by_class["covered_union"].total) == (1, 1)
    assert by_class["covered_union"].note == "n01 covered c01+c02"
    assert (by_class["shared_union"].caught, by_class["shared_union"].total) == (1, 1)

    def over_folding(rec: calibrate.DefectRecord, u) -> llm.MockAnswer:
        answer = {
            "story": "new", "beat_id": "",
            "claims": [{"claim_id": "n01", "verdict": "covered",
                        "existing_claim_id": "b1.c01+b1.c02", "new_value": "",
                        "existing_value": "", "reason": "together they cover it"}],
        }
        return llm.MockAnswer(text=json.dumps(answer), model_id=llm.ROLE_MODEL["merge_judge"])

    report = calibrate.run(fixture, REPO_ROOT / "Books", client="mock", scripted_merge=over_folding)
    by_class = {row.defect_class: row for row in report.rows}
    assert (by_class["shared_union"].caught, by_class["shared_union"].total) == (0, 1)
    assert (by_class["covered_union"].caught, by_class["covered_union"].total) == (1, 1)


def test_a_judge_that_wrongly_covers_a_precision_plant_always_misses():
    """What the two new precision plants measure (2026-09-22). A judge that
    answers `covered` naming both claims is wrong in each: `adds_a_date`
    adds a year neither claim states, and `recombined_union` recombines
    their own words into a MISATTRIBUTION that cannot coexist with c01 (it
    moves the hippo out of Gallery 111). Both are MISSED, but for different
    reasons, and the notes say which: the leftover-words guard holds the
    first (its words are left over) while the second passes every mechanical
    check and is absorbed — the guard's blind spot, measured rather than
    argued. Their correct answers differ — `new` for `adds_a_date`,
    `supersedes` for the misattribution — and both are caught below."""
    import src.ingest.llm as llm

    fixture = _fixture()

    def covers(rec: calibrate.DefectRecord, u) -> llm.MockAnswer:
        if rec.defect_class not in ("adds_a_date", "recombined_union"):
            return calibrate.scripted_merge_answer(rec, u)  # every other class answered right
        ids = "+".join(f"b1.{cid}" for cid in rec.planted.get("claim_ids", []))
        answer = {
            "story": "new", "beat_id": "",
            "claims": [
                {"claim_id": "n01", "verdict": "covered", "existing_claim_id": ids,
                 "new_value": "", "existing_value": "",
                 "reason": "the two together state it"}
            ],
        }
        return llm.MockAnswer(text=json.dumps(answer), model_id=llm.ROLE_MODEL["merge_judge"])

    report = calibrate.run(fixture, REPO_ROOT / "Books", client="mock", scripted_merge=covers)
    by_class = {row.defect_class: row for row in report.rows}

    adds = by_class["adds_a_date"]
    assert (adds.caught, adds.total) == (0, 1)
    assert adds.note.startswith("held: claim n01: covered leaves words no named claim states")
    assert "1938" in adds.note and "since" in adds.note

    blind = by_class["recombined_union"]
    assert (blind.caught, blind.total) == (0, 1)
    assert blind.note == "n01 covered c01+c02"  # applied: nothing mechanical objected

    clean = calibrate.run(fixture, REPO_ROOT / "Books", client="mock")
    by_class = {row.defect_class: row for row in clean.rows}
    for name in ("adds_a_date", "recombined_union", "negated_fact", "covered_union_elsewhere"):
        assert (by_class[name].caught, by_class[name].total) == (1, 1), name


def test_a_hold_is_reported_apart_from_a_wrong_fold():
    """Registered before the paid run (2026-09-22): the harness counts any
    merge hold as MISSED, which measures the JUDGE — but a hold and a wrong
    fold are not the same event for the corpus. A hold writes nothing and
    queues the story for a human; a wrong fold changes the corpus silently.
    The row now says which happened (`held`), so the paid run's bars can be
    read mechanically instead of by eye: the guard correctly holding a
    wrongly-`covered` story must never be read as a corpus defect."""
    import src.ingest.llm as llm

    fixture = _fixture()

    def covers(rec: calibrate.DefectRecord, u) -> llm.MockAnswer:
        if rec.defect_class not in ("adds_a_date", "recombined_union"):
            return calibrate.scripted_merge_answer(rec, u)
        ids = "+".join(f"b1.{cid}" for cid in rec.planted.get("claim_ids", []))
        answer = {
            "story": "new", "beat_id": "",
            "claims": [
                {"claim_id": "n01", "verdict": "covered", "existing_claim_id": ids,
                 "new_value": "", "existing_value": "", "reason": "the two together state it"}
            ],
        }
        return llm.MockAnswer(text=json.dumps(answer), model_id=llm.ROLE_MODEL["merge_judge"])

    by_class = {
        row.defect_class: row
        for row in calibrate.run(
            fixture, REPO_ROOT / "Books", client="mock", scripted_merge=covers
        ).rows
    }
    # Both missed; only one of them changed the corpus.
    assert (by_class["adds_a_date"].caught, by_class["adds_a_date"].held) == (0, True)
    assert (by_class["recombined_union"].caught, by_class["recombined_union"].held) == (0, False)

    clean = {row.defect_class: row for row in
             calibrate.run(fixture, REPO_ROOT / "Books", client="mock").rows}
    assert [name for name, row in clean.items() if row.held] == []
    assert clean["adds_a_date"].caught == 1


def test_the_printout_marks_a_hold_so_it_is_never_read_as_a_wrong_fold():
    """A 0/1 in the printout means two different things, and the paid run's
    reader must not have to guess: a held class prints HELD and the reason,
    so a working guard is never read as a corpus defect."""
    rows = [
        calibrate.ClassRow("adds_a_date", "merge", 0, 1, False,
                           "held: claim n01: covered leaves words no named claim states: 1938, "
                           "since", held=True),
        calibrate.ClassRow("recombined_union", "merge", 0, 1, False, "n01 covered c01+c02"),
    ]
    out = calibrate.format_report(calibrate.Report(client="live", rows=rows))

    adds = next(line for line in out.splitlines() if line.startswith("adds_a_date"))
    blind = next(line for line in out.splitlines() if line.startswith("recombined_union"))
    assert "HELD" in adds and "HELD" not in blind
    assert "held (nothing written, queued for review):" in out
    assert "adds_a_date: claim n01: covered leaves words" in out


def test_a_wrong_fold_is_named_in_the_row_and_the_printout():
    """Bar (1) of the paid run — zero wrong folds — must be readable from
    the report, not from the raw event stream (judge, 2026-09-22): for
    seven of the ten merge classes a bare 0/1 cannot tell a wrong fold
    (the corpus changed) from a plain miss (the judge said `new`, nothing
    folded). The row says `wrong_fold` and the line says WRONG FOLD."""
    import src.ingest.llm as llm

    fixture = _fixture()

    def folds(rec: calibrate.DefectRecord, u) -> llm.MockAnswer:
        # `same` into the planted claim: wrong for a `new` plant (it folds),
        # and for a `covered` plant (the wrong shape) — both change the
        # corpus. Every other class answers correctly.
        if rec.defect_class not in ("shared_union", "covered_union"):
            return calibrate.scripted_merge_answer(rec, u)
        answer = {
            "story": "new", "beat_id": "",
            "claims": [
                {"claim_id": "n01", "verdict": "same",
                 "existing_claim_id": f"b1.{rec.planted['claim_id']}",
                 "new_value": "", "existing_value": "", "reason": "same fact"}
            ],
        }
        return llm.MockAnswer(text=json.dumps(answer), model_id=llm.ROLE_MODEL["merge_judge"])

    report = calibrate.run(fixture, REPO_ROOT / "Books", client="mock", scripted_merge=folds)
    by_class = {row.defect_class: row for row in report.rows}

    assert (by_class["shared_union"].caught, by_class["shared_union"].wrong_fold) == (0, True)
    assert (by_class["covered_union"].caught, by_class["covered_union"].wrong_fold) == (0, True)
    # A correct run folds nothing wrongly, anywhere.
    clean = calibrate.run(fixture, REPO_ROOT / "Books", client="mock")
    assert [r.defect_class for r in clean.rows if r.wrong_fold] == []

    # And the distinction the bar rests on: a MISS that folded nothing left
    # the corpus untouched, so it is not a wrong fold. `recombined_union` is
    # the plant for it — its signature is silent by design, so a `new`
    # answer is not held by the tripwire and really does reach the corpus
    # check having written nothing.
    def says_new(rec: calibrate.DefectRecord, u) -> llm.MockAnswer:
        if rec.defect_class != "recombined_union":
            return calibrate.scripted_merge_answer(rec, u)
        answer = {
            "story": "new", "beat_id": "",
            "claims": [
                {"claim_id": "n01", "verdict": "new", "existing_claim_id": "",
                 "new_value": "", "existing_value": "", "reason": "nothing states it"}
            ],
        }
        return llm.MockAnswer(text=json.dumps(answer), model_id=llm.ROLE_MODEL["merge_judge"])

    missed = {
        row.defect_class: row
        for row in calibrate.run(
            fixture, REPO_ROOT / "Books", client="mock", scripted_merge=says_new
        ).rows
    }
    row = missed["recombined_union"]
    assert (row.caught, row.wrong_fold, row.held) == (0, False, False)

    out = calibrate.format_report(report)
    assert "WRONG FOLD" in next(
        line for line in out.splitlines() if line.startswith("shared_union")
    )
    assert "wrong folds (the corpus changed):" in out
    assert "shared_union: n01 same" in out


def test_a_unit_held_class_is_marked_held_too():
    """The other hold path (judge, 2026-09-22): a claim-judge class whose
    whole unit is held prints a bare 0/1 like any miss, though nothing was
    judged at all. It is marked HELD with its reason, so bar (3) — every
    held row read — cannot skip it."""
    import src.ingest.llm as llm

    fixture = _fixture()

    def unreadable(rec: calibrate.DefectRecord, u) -> dict[str, llm.MockAnswer]:
        if rec.defect_class != "fabricated_date":
            return calibrate.scripted_answers(rec, u)
        return {
            custom_id: llm.MockAnswer(text="not json", model_id=llm.ROLE_MODEL["claim_judge"])
            for custom_id in calibrate.scripted_answers(rec, u)
        }

    by_class = {
        row.defect_class: row
        for row in calibrate.run(
            fixture, REPO_ROOT / "Books", client="mock", scripted=unreadable
        ).rows
    }

    row = by_class["fabricated_date"]
    assert (row.caught, row.held, row.wrong_fold) == (0, True, False)
    assert row.note.startswith("unit held: ")
    out = calibrate.format_report(calibrate.Report(client="mock", rows=[row]))
    assert "HELD" in out and "held (nothing written, queued for review):" in out
