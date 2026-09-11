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

#: The ten defect classes §4 names, in its order.
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
)


def _fixture() -> calibrate.Fixture:
    return calibrate.load_fixture(FIXTURE)


def _unit(fixture: calibrate.Fixture) -> unit_mod.Unit:
    return calibrate.load_fixture_unit(REPO_ROOT / "Books", fixture)


def test_fixture_has_one_record_per_class_and_only_the_planted_defect():
    """Fixture pins: exactly the ten §4 classes, one record each; every
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
        if "span" in record.planted:
            assert gates.span_in_unit(record.planted["span"], unit.text) is None, (
                record.defect_class
            )
        if "corrected" in record.planted:
            corrected = record.planted["corrected"]
            assert gates.claim_gates(corrected["text"], corrected["span"], unit.text) == []


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

    for name in ("fabricated_date", "deleted_claim", "wrong_cause", "omitted_fact"):
        assert (by_class[name].caught, by_class[name].total) == (1, 1), name
        assert by_class[name].scripted is True, name
    for name in ("lift", "guidebook_attribution"):
        assert (by_class[name].caught, by_class[name].total) == (1, 1), name
        assert by_class[name].scripted is False, name
    for name in ("framing_sentence", "state_as_event", "contested_value", "superseded_belief"):
        assert by_class[name].detector == "pending", name
        assert by_class[name].caught is None and by_class[name].total is None, name

    text = calibrate.format_report(report)
    assert "MOCK" in text
    assert "scripted" in text
    for name in CLASSES:
        assert name in text
    assert "fabricated_date" in text and "100%" in text
    assert "pending" in text


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
    committed = json.loads(BASELINE.read_text())["mock"]
    live_report = calibrate.run(_fixture(), REPO_ROOT / "Books", client="mock")
    assert calibrate.regressions(live_report, committed) == []
    assert calibrate.baseline_from(live_report) == committed


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
    judge rows and the omissions row), never only the last one — an owner
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
    assert len(printed) == 2
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
