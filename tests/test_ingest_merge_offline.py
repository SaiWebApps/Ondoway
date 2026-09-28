"""Tests for scripts/ingest_merge_offline.py — `make ingest-merge-offline`,
the $0 re-measure of a merge change: every story in finished
replay sandboxes is merged again under the CURRENT P6 code, the judge
answering only with the answers each run recorded (`merge_answered`), over
the file the job started from. Nothing is invented: a story the current
code would ask more of than was recorded is reported unmeasured.

Every test runs the mock provider (the scripted Guggenheim job in
tests/ingest_job_script.py); no live client, no network, no spend.
"""

from __future__ import annotations

import json

import scripts.ingest_merge_offline as offline
import src.ingest.merge as merge
from tests import ingest_job_script as script_mod
from tests.test_ingest_merge_replay import _claim_verdict, _source_job


def _log_path(ws: dict):
    return ws["data_root"] / script_mod.CITY / "jobs" / f"{ws['job_id']}.jsonl"


def _run(ws: dict, capsys, start=None) -> tuple[int, dict | str]:
    rc = offline.main([
        "--city", script_mod.CITY, "--job", ws["job_id"],
        "--start", str(start or ws["beats"]), "--run", str(ws["data_root"]),
    ])
    out = capsys.readouterr().out
    return rc, (json.loads(out) if rc == 0 else out)


def test_an_offline_replay_reproduces_the_recorded_decision(tmp_path, monkeypatch, capsys):
    """The source job's judge called every claim new while the completion
    claim's signature matches the sidebar's: P6 held it. Replayed offline
    from its own record under the current code, it is held for the same
    reason, after exactly the one answer that was recorded."""
    ws = _source_job(tmp_path, monkeypatch, capsys)

    rc, report = _run(ws, capsys)

    assert rc == 0, report
    [run] = report["runs"]
    [story] = run["stories"]
    assert story["story_slug"] == script_mod.STORY_SLUG
    assert story["status"] == "held"
    assert "judge and signature disagree" in story["reason"]
    assert (story["recorded"], story["judge_calls"]) == (1, 1)


def test_a_start_file_the_job_did_not_start_from_is_refused(tmp_path, monkeypatch, capsys):
    """A replay over the wrong corpus proves nothing: the start file's sha
    must be the job's recorded beats_sha256, or nothing is measured."""
    ws = _source_job(tmp_path, monkeypatch, capsys)
    other = tmp_path / "other-beats.json"
    other.write_text(json.dumps(script_mod.existing_records()), encoding="utf-8")

    rc, out = _run(ws, capsys, start=other)

    assert rc == 2
    assert "beats_sha256" in out


def _record_answers(ws: dict, answers: list[dict]) -> None:
    """Replace the run's recorded merge answers with `answers`, in order."""
    lines = _log_path(ws).read_text(encoding="utf-8").splitlines()
    kept = [line for line in lines if json.loads(line).get("message") != "merge_answered"]
    for attempt, answer in enumerate(answers, 1):
        kept.append(json.dumps({
            "kind": "info", "message": "merge_answered",
            "data": {"story_slug": script_mod.STORY_SLUG, "attempt": attempt,
                     "model": "claude-sonnet-5", "answer": json.dumps(answer)},
        }))
    _log_path(ws).write_text("\n".join(kept) + "\n", encoding="utf-8")


def test_the_first_recorded_answer_the_current_code_accepts_is_applied(
    tmp_path, monkeypatch, capsys
):
    """The live runs recorded a re-ask the old code demanded; the current
    code accepts the first answer, so the second is never used. The prose
    the judge put on `new` claims is not written; the completion claim's
    equal quoted values ("1959") are, and the report shows both sides."""
    ws = _source_job(tmp_path, monkeypatch, capsys)
    answer = {"story": "new", "beat_id": "", "claims": [
        _claim_verdict("c01", "new", new_value="began collecting in his sixties"),
        _claim_verdict("c02", "new", new_value="opened in 1939"),
        _claim_verdict("c03", "same", "b2.c01", "1959", "1959"),
    ]}
    reask = {**answer, "claims": [*answer["claims"][:2], _claim_verdict("c03", "new")]}
    _record_answers(ws, [answer, reask])

    rc, report = _run(ws, capsys)

    assert rc == 0, report
    [story] = report["runs"][0]["stories"]
    assert story["status"] == "applied", story
    assert (story["recorded"], story["judge_calls"]) == (2, 1)
    assert [(c["claim_id"], c["outcome"]) for c in story["claims"]] == [
        ("c01", "new"), ("c02", "new"), ("c03", "same")
    ]
    assert story["stated_values"] == [
        {"claim_id": "c03", "beat_id": script_mod.COMPLETION_ID, "stated_value": "1959",
         "quoted": ["1959", "1959"]}
    ]


def test_a_story_that_needs_an_answer_nobody_recorded_is_unmeasured(
    tmp_path, monkeypatch, capsys
):
    """One recorded answer naming a claim the place does not have: the
    current code re-asks, and there is no second answer on record. The
    story is reported unmeasured — never guessed, never a crash."""
    ws = _source_job(tmp_path, monkeypatch, capsys)
    _record_answers(ws, [{"story": "new", "beat_id": "", "claims": [
        _claim_verdict("c01", "new"), _claim_verdict("c02", "new"),
        _claim_verdict("c03", "same", "b9.c01", "1959", "1959"),
    ]}])

    rc, report = _run(ws, capsys)

    assert rc == 0, report
    [story] = report["runs"][0]["stories"]
    assert story["status"] == "unmeasured"
    # The mock logs a call once answered: the one recorded answer was used,
    # the re-ask it demanded had nothing to answer it.
    assert (story["recorded"], story["judge_calls"]) == (1, 1)


def test_a_written_source_carrying_judgments_is_still_reported():
    """ADR-0004: merge now writes a source with its `judgments`. The report
    matched written sources to judged ones by their whole serialized form
    with only stated_value cleared, so a judged source dropped out of it
    silently. It now matches by (source_id, chunk, span). Mutant: the old
    whole-dump match."""
    from tests import test_ingest_merge as tm

    unit = tm._second_unit()
    claims = [tm._judged(unit, "c01", tm.ICONIC_1960)]
    story = tm._story("Finished in 1960", ["c01"])
    before = tm._existing()
    answer = tm._answer("same", "b1", [tm._claim_verdict(
        "c01", "conflict", "b1.c02", "1960", "1959", judgments=["finished"])])
    outcome = merge.merge(story, claims, before, tm._armed_judge([answer], claims))
    after = merge.apply(outcome, before)
    assert after[0].claims[1].sources[-1].judgments == ["finished"]

    written = offline._written_values(outcome, before, after)

    assert [(w["claim_id"], w["stated_value"]) for w in written] == [("c01", "1960")]
