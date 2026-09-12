"""Tests for scripts/ingest_job.py — `make ingest-job CITY= CHUNK_DIR= [ARGS=]`,
the headless launcher slice 9 runs the first REAL jobs through
(Docs/ingestion/rebuild-spec.md §8 slice 9; owner rulings 2026-09-12).

Every test runs the mock provider (`INGEST_PROVIDER=mock`, the scripted
Guggenheim job in tests/ingest_job_script.py); no live client, no network,
no spend (test_no_live_client_in_this_file). Nothing touches the repo's
data/: every job writes under a per-test data root.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import scripts.ingest_job as ingest_job
from tests import ingest_job_script as script_mod


def _mock_env(tmp_path, monkeypatch) -> dict:
    """A per-test chunk dir, the repo-shaped data dir (real poi-raw.json),
    an empty data-ingest root, and the scripted mock wired into the env
    `run.client_from_env` reads."""
    chunks = script_mod.chunk_dir(tmp_path)
    repo_data = script_mod.data_dir(tmp_path)
    data_root = tmp_path / "data-ingest"
    unit = script_mod.unit(chunks)
    script_path = tmp_path / "script.json"
    script_mod.write_script(script_path, script_mod.script(unit))
    monkeypatch.setenv("INGEST_PROVIDER", "mock")
    monkeypatch.setenv("INGEST_MOCK_SCRIPT", str(script_path))
    monkeypatch.delenv("INGEST_DATA_ROOT", raising=False)
    argv = [
        "--city", script_mod.CITY,
        "--chunk-dir", str(chunks),
        "--chunk", script_mod.LP_CHUNK,
        "--as-of", "2023",
        "--data-root", str(data_root),
        "--repo-data", str(repo_data),
    ]
    return {"chunks": chunks, "repo_data": repo_data, "data_root": data_root, "argv": argv}


def test_the_data_root_is_created_from_the_repos_poi_file_and_never_overwritten(tmp_path):
    """The new-schema beats file lives in a root beside data/ until the
    slice-10 swap (owner ruling): `ensure_root` copies the city's real
    poi-raw.json there and starts an EMPTY new-shape beats.json, so P0's
    legacy-shape gate never sees the real legacy file. A root that already
    holds beats is left exactly as it is — a second job must merge into
    the first job's beats, never restart from nothing."""
    repo_data = script_mod.data_dir(tmp_path)  # the city's real poi-raw.json
    data_root = tmp_path / "data-ingest"

    paths = ingest_job.ensure_root(data_root, repo_data, script_mod.CITY)

    city_dir = data_root / script_mod.CITY
    assert paths == {"poi_raw": city_dir / "poi-raw.json", "beats": city_dir / "beats.json"}
    assert (city_dir / "poi-raw.json").read_bytes() == (
        repo_data / script_mod.CITY / "poi-raw.json"
    ).read_bytes()
    assert json.loads((city_dir / "beats.json").read_text(encoding="utf-8")) == []

    (city_dir / "beats.json").write_text('[{"beat_id": "kept"}]', encoding="utf-8")
    ingest_job.ensure_root(data_root, repo_data, script_mod.CITY)
    assert (city_dir / "beats.json").read_text(encoding="utf-8") == '[{"beat_id": "kept"}]'


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


def test_without_yes_the_estimate_is_printed_and_nothing_runs(tmp_path, monkeypatch, capsys):
    """The owner sees the ceiling before every real job (spend is approved,
    the estimate is still shown): without `--yes` the run prints the
    estimate — total, per-phase rows, the prices' date — names the flag
    that spends, exits 2, and no phase runs: the beats file is still empty
    and no job log line beyond the estimate exists."""
    ws = _mock_env(tmp_path, monkeypatch)

    rc = ingest_job.main(ws["argv"])

    out = capsys.readouterr().out
    assert rc == 2
    assert "estimated spend: $" in out
    assert "first-pass (no re-asks): $" in out  # the number the go decision reads
    assert "P1" in out and "P6" in out
    assert "in/call=" in out  # per-call input, so each model's own token count is visible
    assert "--yes" in out
    beats = ws["data_root"] / script_mod.CITY / "beats.json"
    assert json.loads(beats.read_text(encoding="utf-8")) == []
    assert "phase P1" not in out


def test_with_yes_the_job_commits_writes_its_log_and_prints_the_measurements(
    tmp_path, monkeypatch, capsys
):
    """With `--yes` the job runs P0-P7 on the provider the env names and
    commits into the data root's beats file. Because the store is
    process-local, the WHOLE job log is written to
    `{data_root}/{city}/jobs/{job_id}.jsonl` (one event per line, the
    estimate first, P7 last) so the slice-9 measurements survive the
    process; the run closes with the summary the owner reads: status and
    beats written, the P6 hold rate with its denominator spelled out
    (here: one story, skipped because no beat was at the place, so no
    merge was judged and the rate is n/a), and the P3 precision counters
    (attempt-one refusals, drops, leak-gate drops)."""
    ws = _mock_env(tmp_path, monkeypatch)

    rc = ingest_job.main([*ws["argv"], "--yes"])

    out = capsys.readouterr().out
    assert rc == 0, out
    beats = ws["data_root"] / script_mod.CITY / "beats.json"
    records = json.loads(beats.read_text(encoding="utf-8"))
    assert [r["beat_id"] for r in records] == [script_mod.BEAT_ID]

    logs = list((ws["data_root"] / script_mod.CITY / "jobs").glob("*.jsonl"))
    assert len(logs) == 1
    lines = [json.loads(line) for line in logs[0].read_text(encoding="utf-8").splitlines()]
    assert lines[0]["message"] == "cost_estimate"
    assert lines[-1]["kind"] == "phase" and lines[-1]["message"] == "P7"
    assert [line["seq"] for line in lines] == list(range(1, len(lines) + 1))

    assert "status=committed" in out and "beats_written=1" in out
    assert "stories=1" in out and "merge_judged=0" in out and "held=0" in out
    assert "skipped_no_beat=1" in out and "hold_rate=n/a" in out
    assert "refused_attempt1=0" in out and "dropped=0" in out and "leak_gate_drops=0" in out


def test_the_summary_measures_real_calls_and_spend_against_the_estimate(
    tmp_path, monkeypatch, capsys
):
    """Slice 9's bar is "cost within 25% of the estimate", so the run must
    measure itself: the CLI counts every call the client actually made,
    per phase (a batch counts each prompt), prices the usage the
    completions report at the same table the estimate used, and prints
    both beside the estimate. On the scripted mock the Guggenheim job
    makes one P1, one P2, four P3 (three verdicts + the omission check),
    one P4, one P5 batch of the narration's sentences and no P6 call, and
    the mock reports zero usage, so actual spend is $0."""
    ws = _mock_env(tmp_path, monkeypatch)

    rc = ingest_job.main([*ws["argv"], "--yes"])

    out = capsys.readouterr().out
    assert rc == 0, out
    assert "spend: estimated=$" in out and "actual=$0.0000" in out
    calls_line = next(line for line in out.splitlines() if line.startswith("calls:"))
    assert "P2 est=2 act=1" in calls_line  # group + its re-ask row, no fan-out; one real call
    assert "P3 est=" in calls_line and "act=4" in calls_line
    assert "P6 est=" in calls_line and "act=0" in calls_line


def test_the_job_log_survives_a_run_that_dies_mid_way(tmp_path, monkeypatch):
    """The judge on the first paid job: the store is in-memory, so a
    SIGINT, a crash or an OOM during a paid run must not lose what was
    already bought. Every event is written to the JSONL the moment it
    lands, not when the run ends: here the runner is replaced by one that
    logs an event and then dies with KeyboardInterrupt (which `run_job`
    does not catch), and the line is on disk when `main` propagates it."""
    ws = _mock_env(tmp_path, monkeypatch)

    def dying_run_job(job_id, store, client, *, data_root=None):
        store.append_event(job_id, "info", "batch_submitted", data={"phase": "P1"})
        raise KeyboardInterrupt

    monkeypatch.setattr(ingest_job.run, "run_job", dying_run_job)
    with pytest.raises(KeyboardInterrupt):
        ingest_job.main([*ws["argv"], "--yes"])

    logs = list((ws["data_root"] / script_mod.CITY / "jobs").glob("*.jsonl"))
    assert len(logs) == 1
    lines = [json.loads(line) for line in logs[0].read_text(encoding="utf-8").splitlines()]
    assert [line["message"] for line in lines] == ["batch_submitted"]


class _Usage:
    input_tokens = 10
    output_tokens = 5
    cache_creation_input_tokens = 0
    cache_read_input_tokens = 0


class _Completion:
    model_id = "claude-opus-5-20260601"
    usage = _Usage()


class _RolelessClient:
    """A client whose `roles` lacks the role it answers for — the meter's
    price lookup raises, and that must never reach the run."""

    def __init__(self) -> None:
        self.roles: dict[str, str] = {}

    def complete(self, role, prompt, schema, *, phase, max_tokens):
        return _Completion()


def test_the_meter_never_alters_or_ends_a_paid_run(capsys):
    """The judge on the first paid job: the meter sits on the call path, so
    an exception inside it would end a paid run in `error` after the
    money is spent. A metering failure loses a number, printed as a
    warning; the completion still comes back and the call is still
    counted."""
    meter = ingest_job._CountingClient(_RolelessClient())

    completion = meter.complete("author", "p", None, phase="P2", max_tokens=1)

    assert isinstance(completion, _Completion)
    assert meter.calls == {"P2": 1}
    assert meter.usd == 0.0
    assert "meter warning" in capsys.readouterr().out
