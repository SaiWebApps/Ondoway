"""Tests for scripts/ingest_merge_replay.py — `make ingest-merge-replay`, the
slice-10 tool that re-runs P6 (and whatever it reruns, then P7) for a
finished job in a SANDBOX data root, so a merge change can be proven on a
real job's judged stories without repaying P1-P5 and without touching the
city's file (Docs/ingestion/rebuild-spec.md §8 slice 10).

Every test runs the mock provider (`INGEST_PROVIDER=mock`, the scripted
Guggenheim job in tests/ingest_job_script.py); no live client, no network,
no spend (test_no_live_client_in_this_file).
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import scripts.ingest_job as ingest_job
import scripts.ingest_merge_replay as replay
from tests import ingest_job_script as script_mod


def _claim_verdict(claim_id: str, verdict: str, existing_claim_id: str = "",
                   new_value: str = "", existing_value: str = "",
                   reason: str = "the two state the same fact") -> dict:
    return {
        "claim_id": claim_id, "verdict": verdict, "existing_claim_id": existing_claim_id,
        "new_value": new_value, "existing_value": existing_value, "reason": reason,
    }


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_job(tmp_path, monkeypatch, capsys) -> dict:
    """A committed job whose story the slice-9 contract could not merge: the
    judge called it new while the completion claim's signature matches the
    sidebar's, so P6 held it and nothing of it reached the file."""
    chunks = script_mod.chunk_dir(tmp_path)
    repo_data = script_mod.data_dir(tmp_path)
    data_root = tmp_path / "data-ingest"
    city_dir = data_root / script_mod.CITY
    city_dir.mkdir(parents=True)
    (city_dir / "poi-raw.json").write_bytes((repo_data / script_mod.CITY / "poi-raw.json")
                                            .read_bytes())
    beats = script_mod.seed_beats(data_root, script_mod.folding_records())
    scripted = script_mod.script(script_mod.unit(chunks))
    scripted["answers"]["merge_judge"] = [
        script_mod.merge_answer(
            "new", "", [_claim_verdict(c, "new", reason="nothing like it")
                        for c in ("c01", "c02", "c03")]
        )
    ]
    script_path = tmp_path / "script.json"
    script_mod.write_script(script_path, scripted)
    monkeypatch.setenv("INGEST_PROVIDER", "mock")
    monkeypatch.setenv("INGEST_MOCK_SCRIPT", str(script_path))
    monkeypatch.delenv("INGEST_DATA_ROOT", raising=False)
    rc = ingest_job.main([
        "--city", script_mod.CITY, "--chunk-dir", str(chunks), "--chunk", script_mod.LP_CHUNK,
        "--as-of", "2023", "--data-root", str(data_root), "--repo-data", str(repo_data), "--yes",
    ])
    out = capsys.readouterr().out
    assert rc == 0, out
    job_ids = [p.name for p in (city_dir / "jobs").iterdir() if p.is_dir()]
    assert len(job_ids) == 1
    return {"chunks": chunks, "repo_data": repo_data, "data_root": data_root,
            "beats": beats, "job_id": job_ids[0], "script_path": script_path}


def test_a_replay_reruns_p6_in_a_sandbox_from_the_jobs_own_phase_files(
    tmp_path, monkeypatch, capsys
):
    """The replay rebuilds the file the job STARTED from (the city's file
    minus the job's own new records, serialized as beats_io writes it) and
    refuses unless its sha is the one the job recorded; it copies the job's
    P0-P5 phase files (not P6/P7) and poi-raw.json into a sandbox root,
    optionally keeping only one place's stories. Resuming the job in the
    sandbox then runs P6 onward under the current merge code — here the
    completion claim folds into the sidebar and the story's other two claims
    become its own re-narrated beat — with no P1-P5 answer scripted, while
    the city's real file is never touched."""
    ws = _source_job(tmp_path, monkeypatch, capsys)
    before = ws["beats"].read_bytes()
    sandbox = tmp_path / "replay"

    rc = replay.main(["--city", script_mod.CITY, "--job", ws["job_id"],
                      "--source-root", str(ws["data_root"]), "--out", str(sandbox),
                      "--place", script_mod.PLACE])

    out = capsys.readouterr().out
    assert rc == 0, out
    job_dir = sandbox / script_mod.CITY / "jobs" / ws["job_id"]
    meta = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert _sha(sandbox / script_mod.CITY / "beats.json") == meta["beats_sha256"]
    assert sorted(p.name for p in job_dir.glob("P*.json")) == [
        f"P{i}.json" for i in range(6)
    ]
    assert (sandbox / script_mod.CITY / "poi-raw.json").is_file()
    assert f"--resume {ws['job_id']} --data-root {sandbox}" in out

    remainder = [script_mod.COLLECTING["text"], script_mod.OPENED_1939["text"]]
    script_mod.write_script(ws["script_path"], {
        "answers": {
            "merge_judge": [script_mod.merge_answer("new", "", [
                _claim_verdict("c01", "new", reason="no claim mentions collecting"),
                _claim_verdict("c02", "new", reason="no claim mentions 1939"),
                _claim_verdict("c03", "same", "b2.c01", "1959", "1959"),
            ])],
            "author": [script_mod.narration_answer(script_mod.REMAINDER_NARRATION)],
        },
        "batch_answers": script_mod.sentence_verdicts(remainder, script_mod.REMAINDER_NARRATION),
    })
    rc = ingest_job.main(["--city", script_mod.CITY, "--data-root", str(sandbox),
                          "--repo-data", str(ws["repo_data"]), "--resume", ws["job_id"], "--yes"])

    out = capsys.readouterr().out
    assert rc == 0, out
    assert "status=committed" in out
    records = json.loads((sandbox / script_mod.CITY / "beats.json").read_text("utf-8"))
    assert [r["beat_id"] for r in records] == [
        script_mod.VISITING_ID, script_mod.COMPLETION_ID, script_mod.BEAT_ID
    ]
    assert len(records[1]["claims"][0]["sources"]) == 2
    assert [c["claim_id"] for c in records[2]["claims"]] == ["c01", "c02"]
    log = (sandbox / script_mod.CITY / "jobs" / f"{ws['job_id']}.jsonl").read_text("utf-8")
    assert '"merge_folded"' in log and '"merge_answered"' in log
    assert ws["beats"].read_bytes() == before


def test_a_replay_refuses_a_city_file_it_cannot_rebuild_the_jobs_start_from(
    tmp_path, monkeypatch, capsys
):
    """If the city's file changed in any other way since the job (another
    job's records, a hand edit), removing the job's own records does not
    give back the file it started from: the sha differs, the replay says so
    and creates nothing — a replay against the wrong corpus proves nothing."""
    ws = _source_job(tmp_path, monkeypatch, capsys)
    records = json.loads(ws["beats"].read_text(encoding="utf-8"))
    records[0]["title"] = "Visiting the museum"
    ws["beats"].write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", "utf-8")
    sandbox = tmp_path / "replay"

    rc = replay.main(["--city", script_mod.CITY, "--job", ws["job_id"],
                      "--source-root", str(ws["data_root"]), "--out", str(sandbox)])

    out = capsys.readouterr().out
    assert rc == 2
    assert "not the file the job started from" in out
    assert not sandbox.exists()


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
