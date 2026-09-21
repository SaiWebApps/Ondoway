"""Tests for src/ingest/p1probe.py — P1's first ask sampled K times on one
unit, each sample classified by how it states the Guggenheim's Wright claim
(Docs/ingestion/rebuild-spec.md, "P1-split probe"). MockClient only; no spend."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import src.ingest.llm as llm
from src.ingest import p1probe
from src.ingest import unit as unit_mod

REPO_ROOT = Path(__file__).resolve().parent.parent
AUTHOR_MODEL = "claude-opus-5-20260601"
SPAN = "delirious spiral of a museum sits among the towers of Fifth Avenue"


def _unit() -> unit_mod.Unit:
    return unit_mod.load_unit(
        REPO_ROOT / "Books" / "new_york",
        city="new_york",
        source_id="frommers-nyc-2024",
        chunk="chunk-05-ch05-uptown",
        as_of=2024,
        rights_basis="owned_copy",
    )


def _claims(*texts: str) -> llm.MockAnswer:
    return llm.MockAnswer(
        text=json.dumps({"claims": [{"text": t, "kind": "state", "span": SPAN} for t in texts]}),
        model_id=AUTHOR_MODEL,
    )


def test_classify_names_split_bundled_and_neither():
    assert p1probe.classify([
        "Frank Lloyd Wright designed the Solomon R. Guggenheim Museum.",
        "The Solomon R. Guggenheim Museum has a spiral shape.",
    ]) == "SPLIT"
    assert p1probe.classify([
        "Frank Lloyd Wright designed the spiral Solomon R. Guggenheim Museum.",
    ]) == "BUNDLED"
    # the source says "Wright's ... spiral", never "designed": a faithful
    # claim may name Wright another way — neither class, counted separately
    assert p1probe.classify([
        "The Guggenheim is Frank Lloyd Wright's museum.",
        "Visitors climb the ramps.",
    ]) == "NEITHER"


def test_k_samples_run_in_one_round_with_unique_ids_and_are_classified():
    unit = _unit()
    answers = {
        p1probe.sample_id(1): _claims(
            "Frank Lloyd Wright designed the Solomon R. Guggenheim Museum.",
            "The Solomon R. Guggenheim Museum is spiral-shaped.",
        ),
        p1probe.sample_id(2): _claims(
            "Frank Lloyd Wright designed the spiral Solomon R. Guggenheim Museum.",
        ),
    }
    assert len(set(answers)) == 2
    calls: list = []

    class Recording:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def complete_batch(self, **kwargs):
            calls.append([custom_id for custom_id, _p in kwargs["prompts"]])
            return self._inner.complete_batch(**kwargs)

    client = Recording(llm.MockClient(lambda k, p: None, batch_answers=answers))

    report = p1probe.run(unit, samples=2, client=client)

    assert calls == [[p1probe.sample_id(1), p1probe.sample_id(2)]]
    assert [s["class"] for s in report] == ["SPLIT", "BUNDLED"]
    assert report[1]["wright_claims"] == [
        "Frank Lloyd Wright designed the spiral Solomon R. Guggenheim Museum."
    ]
    assert [s["claims"] for s in report] == [2, 1]


def test_run_refuses_to_spend_before_its_estimate_is_printed():
    client = llm.MockClient(lambda k, p: None, batch_answers={})
    with pytest.raises(llm.EstimateNotPrinted):
        p1probe.run(_unit(), samples=1, client=client, arm=False)


def _job(tmp_path: Path) -> Path:
    unit = _unit()
    job = tmp_path / "job"
    job.mkdir()
    u = {"city": unit.city, "source_id": unit.source_id, "chunk": unit.chunk, "text": unit.text,
         "as_of": unit.as_of, "rights_basis": unit.rights_basis, "path": str(unit.path)}
    (job / "P0.json").write_text(json.dumps({"units": [u]}))
    return job


def test_cli_prints_its_estimate_and_spends_nothing_without_yes(tmp_path, capsys):
    job = _job(tmp_path)
    out = tmp_path / "out"
    client = llm.MockClient(lambda k, p: None, batch_answers={})

    code = p1probe.main(["--job-dir", str(job), "--samples", "3", "--out", str(out)], client=client)

    assert code == 2
    printed = capsys.readouterr().out
    assert "estimate" in printed and "--yes" in printed
    assert client.calls == []
    assert not out.exists()


def test_cli_writes_only_under_out_and_prints_actual_spend(tmp_path, capsys):
    from src.ingest.rejudge import EventLog

    job = _job(tmp_path)
    out = tmp_path / "out"
    log = EventLog()

    class Metered:
        usd = 0.2468

        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

    client = Metered(llm.MockClient(
        lambda kind, payload: log.append({"event": kind, "data": payload}),
        batch_answers={p1probe.sample_id(1): _claims(
            "Frank Lloyd Wright designed the spiral Solomon R. Guggenheim Museum."
        )},
    ))
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))

    code = p1probe.main(
        ["--job-dir", str(job), "--samples", "1", "--out", str(out), "--yes"],
        client=client, events=log,
    )

    assert code == 0
    report = json.loads((out / "p1probe-report.json").read_text())
    assert [s["class"] for s in report] == ["BUNDLED"]
    after = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    assert [p for p in after if p not in before] == [
        Path("out"), Path("out/events.jsonl"), Path("out/p1probe-report.json"),
    ]
    assert "actual=$0.2468" in capsys.readouterr().out


def test_a_job_with_more_than_one_unit_is_refused_not_probed_on_its_first(tmp_path):
    """A multi-unit job must name its unit — probing units[0] silently would
    measure a different passage than the one that produced the claim."""
    job = _job(tmp_path)
    p0 = json.loads((job / "P0.json").read_text())
    p0["units"].append(dict(p0["units"][0], chunk="another-chunk"))
    (job / "P0.json").write_text(json.dumps(p0))
    client = llm.MockClient(lambda k, p: None, batch_answers={})

    with pytest.raises(SystemExit):
        p1probe.main(["--job-dir", str(job), "--out", str(tmp_path / "out")], client=client)
    assert client.calls == []
