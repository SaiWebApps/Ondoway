"""Tests for src/ingest/rejudge.py — P3's two judge looks re-run on a
finished job's claims, scored against labels (Docs/ingestion/rebuild-spec.md,
"P3 step 3"). Every test runs on llm.MockClient over the real Lonely Planet
chunk-07 unit; no live client, no spend."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import src.ingest.llm as llm
from src.ingest import rejudge
from src.ingest import unit as unit_mod

REPO_ROOT = Path(__file__).resolve().parent.parent
JUDGE_MODEL = "claude-haiku-4-5-20251001"

ADDRESS = {
    "claim_id": "c05",
    "text": "The Guggenheim Museum stands at 1071 Fifth Ave at E 89th St.",
    "span": "1071 Fifth Ave, at E 89th St",
    "kind": "state",
}
RAMP = {
    "claim_id": "c03",
    "text": "The Guggenheim Museum has a spiral ramp.",
    "span": "the Guggenheim Museum has a spiral ramp",
    "kind": "state",
}


def _unit() -> unit_mod.Unit:
    return unit_mod.load_unit(
        REPO_ROOT / "Books" / "new_york",
        city="new_york",
        source_id="lonely-planet-new-york-city",
        chunk="chunk-07-upper-east-side",
        as_of=2022,
        rights_basis="owned_copy",
    )


def _verdict(entailed: bool, reason: str = "r") -> llm.MockAnswer:
    return llm.MockAnswer(
        text=json.dumps({"reason": reason, "entailed": entailed, "kind": "state"}),
        model_id=JUDGE_MODEL,
    )


def test_plant_negative_changes_the_first_number_only():
    assert rejudge.plant_negative("It opened in 1959 and cost 50 cents.") == (
        "It opened in 1962 and cost 50 cents."
    )
    assert rejudge.plant_negative("Adult admission costs $25.") == "Adult admission costs $32."
    assert rejudge.plant_negative("The museum has a spiral ramp.") is None


def test_two_samples_score_survival_and_catches_with_unique_ids():
    """A SUPPORTED claim refused on the first look and cleared on the
    second SURVIVES; a planted negative refused on both looks is CAUGHT;
    one cleared on the second look is a FALSE CLEAR. Every request id is
    unique across samples and looks, and a claim cleared on the first look
    gets no second look."""
    unit = _unit()
    items = [
        rejudge.Item(ADDRESS["claim_id"], ADDRESS["text"], ADDRESS["span"], "SUPPORTED"),
        rejudge.Item(RAMP["claim_id"], RAMP["text"], RAMP["span"], "SUPPORTED"),
    ]
    negatives = rejudge.negatives_of(items)
    assert [(n.claim_id, n.text, n.label) for n in negatives] == [
        ("c05n", "The Guggenheim Museum stands at 1074 Fifth Ave at E 89th St.", "NEGATIVE"),
    ]
    ids = rejudge.custom_ids
    answers = {
        # sample 1: c05 refused then cleared (survives); c03 cleared at once;
        # the negative refused twice (caught)
        ids(1, "c05", 1): _verdict(False), ids(1, "c05", 2): _verdict(True),
        ids(1, "c03", 1): _verdict(True),
        ids(1, "c05n", 1): _verdict(False), ids(1, "c05n", 2): _verdict(False),
        # sample 2: c05 refused twice (lost); the negative cleared (false clear)
        ids(2, "c05", 1): _verdict(False), ids(2, "c05", 2): _verdict(False),
        ids(2, "c03", 1): _verdict(True),
        ids(2, "c05n", 1): _verdict(False), ids(2, "c05n", 2): _verdict(True),
    }
    assert len(set(answers)) == len(answers)
    events: list = []
    client = llm.MockClient(lambda k, p: events.append((k, p)), batch_answers=answers)

    report = rejudge.run(unit, [*items, *negatives], samples=2, client=client)

    zero = {"accepted": 0, "accepted_refused_first": 0, "accepted_lost": 0}
    assert report.per_sample == [
        {"supported_survived": 2, "supported": 2, "negatives_caught": 1, "negatives": 1, **zero},
        {"supported_survived": 1, "supported": 2, "negatives_caught": 0, "negatives": 1, **zero},
    ]
    assert report.outcomes[("c05", 1)] == ("refused", "cleared")
    assert report.outcomes[("c03", 1)] == ("cleared", None)
    assert report.outcomes[("c05n", 2)] == ("refused", "cleared")


def test_run_refuses_to_spend_before_its_estimate_is_printed():
    unit = _unit()
    items = [rejudge.Item(RAMP["claim_id"], RAMP["text"], RAMP["span"], "SUPPORTED")]
    client = llm.MockClient(lambda k, p: None, batch_answers={})
    with pytest.raises(llm.EstimateNotPrinted):
        rejudge.run(unit, items, samples=1, client=client, arm=False)


def test_accepted_claims_count_new_first_look_refusals_and_losses():
    """The claims job B's first look ACCEPTED are judged once under the
    current schema: how many it now refuses at look 1, and how many it
    loses after both looks (reason-before-verdict must not buy precision
    by refusing claims that used to pass)."""
    unit = _unit()
    items = [
        rejudge.Item("c05", ADDRESS["text"], ADDRESS["span"], "ACCEPTED"),
        rejudge.Item("c03", RAMP["text"], RAMP["span"], "ACCEPTED"),
    ]
    ids = rejudge.custom_ids
    answers = {
        ids(1, "c05", 1): _verdict(False), ids(1, "c05", 2): _verdict(True),
        ids(1, "c03", 1): _verdict(False), ids(1, "c03", 2): _verdict(False),
    }
    client = llm.MockClient(lambda k, p: None, batch_answers=answers)

    report = rejudge.run(unit, items, samples=1, client=client)

    assert report.per_sample[0]["accepted"] == 2
    assert report.per_sample[0]["accepted_refused_first"] == 2
    assert report.per_sample[0]["accepted_lost"] == 1


def _job(tmp_path: Path) -> tuple[Path, Path]:
    """A finished job's P0/P1 and a labels dir, from the real LP unit."""
    unit = _unit()
    job = tmp_path / "job"
    job.mkdir()
    u = {"city": unit.city, "source_id": unit.source_id, "chunk": unit.chunk, "text": unit.text,
         "as_of": unit.as_of, "rights_basis": unit.rights_basis, "path": str(unit.path)}
    (job / "P0.json").write_text(json.dumps({"units": [u]}))
    claims = [
        {"claim_id": c["claim_id"], "text": c["text"], "kind": c["kind"],
         "source": {"span": c["span"]}}
        for c in (ADDRESS, RAMP)
    ]
    (job / "P1.json").write_text(json.dumps({"claims": {"k": claims}}))
    labels = tmp_path / "labels"
    labels.mkdir()
    (labels / "label_key.json").write_text(json.dumps([{"id": "L01", "claim_id": "c05"}]))
    for name in ("labels_fable.json", "labels_sonnet.json"):
        (labels / name).write_text(json.dumps({"claims": {"L01": "SUPPORTED"}}))
    return job, labels


def test_cli_prints_its_estimate_and_spends_nothing_without_yes(tmp_path, capsys):
    job, labels = _job(tmp_path)
    out = tmp_path / "out"
    client = llm.MockClient(lambda k, p: None, batch_answers={})

    code = rejudge.main(
        ["--job-dir", str(job), "--labels-dir", str(labels), "--samples", "3",
         "--out", str(out)],
        client=client,
    )

    assert code == 2
    printed = capsys.readouterr().out
    assert "estimate" in printed and "--yes" in printed
    assert client.calls == []
    assert not out.exists()


def test_cli_scores_agreed_labels_plus_negatives_and_writes_only_under_out(tmp_path):
    """Items are the claims BOTH labelers labeled the same (here c05,
    SUPPORTED) plus one planted negative per numbered SUPPORTED claim; the
    unlabeled P1 claim (c03) is an ACCEPTED item only with --accepted. The
    report lands under --out and nowhere else."""
    job, labels = _job(tmp_path)
    out = tmp_path / "out"
    ids = rejudge.custom_ids
    answers = {
        ids(1, "c05", 1): _verdict(True),
        ids(1, "c05n", 1): _verdict(False), ids(1, "c05n", 2): _verdict(False),
    }
    client = llm.MockClient(lambda k, p: None, batch_answers=answers)
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))

    code = rejudge.main(
        ["--job-dir", str(job), "--labels-dir", str(labels), "--samples", "1",
         "--out", str(out), "--yes"],
        client=client,
    )

    assert code == 0
    report = json.loads((out / "rejudge-report.json").read_text())
    assert report["per_sample"] == [
        {"supported_survived": 1, "supported": 1, "negatives_caught": 1, "negatives": 1,
         "accepted": 0, "accepted_refused_first": 0, "accepted_lost": 0}
    ]
    after = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    assert [p for p in after if p not in before] == [
        Path("out"), Path("out/events.jsonl"), Path("out/rejudge-report.json"),
    ]


def test_the_accepted_pass_judges_only_the_unlabeled_claims(tmp_path):
    """`--accepted` is its own pass over the claims the job's first look
    accepted — never the labeled claims or the planted negatives again, so
    its one sample is never pooled into the labeled pass's K-sample spread."""
    job, labels = _job(tmp_path)
    out = tmp_path / "out"
    ids = rejudge.custom_ids
    client = llm.MockClient(
        lambda k, p: None, batch_answers={ids(1, "c03", 1): _verdict(True)}
    )

    code = rejudge.main(
        ["--job-dir", str(job), "--labels-dir", str(labels), "--samples", "1",
         "--accepted", "--out", str(out), "--yes"],
        client=client,
    )

    assert code == 0
    report = json.loads((out / "rejudge-report.json").read_text())
    assert report["labels"] == {"c03": "ACCEPTED"}
    assert report["per_sample"][0]["accepted"] == 1
    assert report["per_sample"][0]["supported"] == report["per_sample"][0]["negatives"] == 0


def test_a_paid_run_keeps_its_client_events_and_prints_actual_spend(tmp_path, capsys):
    """The first live rejudge (2026-09-21) could not report its own spend:
    its client's events — where the live client puts every batch id — went
    to a no-op sink. The events a run's client emitted land in
    <out>/events.jsonl (batch ids included on a live client, so every round
    is recoverable with `make ingest-batch`), and a metered client's actual
    spend is printed."""
    job, labels = _job(tmp_path)
    out = tmp_path / "out"
    ids = rejudge.custom_ids
    events = rejudge.EventLog()

    class Metered:
        usd = 0.0123

        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

    client = Metered(llm.MockClient(
        lambda kind, payload: events.append({"event": kind, "data": payload}),
        batch_answers={
            ids(1, "c05", 1): _verdict(True),
            ids(1, "c05n", 1): _verdict(False), ids(1, "c05n", 2): _verdict(False),
        },
    ))

    code = rejudge.main(
        ["--job-dir", str(job), "--labels-dir", str(labels), "--samples", "1",
         "--out", str(out), "--yes"],
        client=client, events=events,
    )

    assert code == 0
    logged = [json.loads(line) for line in (out / "events.jsonl").read_text().splitlines()]
    assert [e["event"] for e in logged][:1] == ["cost_estimate"]
    assert "actual=$0.0123" in capsys.readouterr().out


def test_a_run_that_dies_mid_batch_still_leaves_its_batch_ids_on_disk(tmp_path):
    """Batch ids matter most when a run DIES (poll ceiling, 404, invalid
    request — all on record): each event is written to <out>/events.jsonl as
    it arrives, and --out exists before the first call, so a run that raises
    after submitting still leaves the batch id `make ingest-batch` needs.
    The error still propagates — nothing is swallowed."""
    job, labels = _job(tmp_path)
    out = tmp_path / "out"
    log = rejudge.EventLog()

    class DiesAfterSubmitting:
        def __init__(self):
            self._inner = llm.MockClient(
                lambda kind, payload: log.append({"event": kind, "data": payload}),
                batch_answers={},
            )

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def complete_batch(self, *args, **kwargs):
            log.append({"event": "batch_submitted", "data": {"batch_id": "msgbatch_dies"}})
            raise RuntimeError("poll ceiling reached")

    with pytest.raises(RuntimeError, match="poll ceiling"):
        rejudge.main(
            ["--job-dir", str(job), "--labels-dir", str(labels), "--samples", "1",
             "--out", str(out), "--yes"],
            client=DiesAfterSubmitting(), events=log,
        )

    logged = [json.loads(line) for line in (out / "events.jsonl").read_text().splitlines()]
    assert {"event": "batch_submitted", "data": {"batch_id": "msgbatch_dies"}} in logged
