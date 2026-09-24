"""Tests for the `same` second-look probe (src/ingest/same_probe.py,
scripts/ingest_same_probe.py) — Docs/ingestion/rebuild-spec.md, "`same`
second-look probe". Scripted clients only: no live client, no network, $0."""

from __future__ import annotations

import json
from pathlib import Path

import scripts.ingest_same_probe as script
import src.ingest.llm as llm
from src.ingest import same_probe

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "fixtures" / "ingestion" / "same_second_look.json"


def _answer(verdict: str, adds: list[str] | None = None) -> llm.MockAnswer:
    return llm.MockAnswer(
        text=json.dumps({"adds": adds or [], "verdict": verdict}),
        model_id=llm.ROLE_MODEL["claim_judge"],
        input_tokens=400,
        output_tokens=60,
    )


def _scripted(pairs, samples: int, verdict_of) -> dict[str, llm.MockAnswer]:
    return {
        same_probe.custom_id(pair["id"], k): verdict_of(pair, k)
        for pair in pairs
        for k in range(1, samples + 1)
    }


def test_the_fixture_holds_the_labeled_pairs_and_the_bars_name_real_ids():
    """Eleven pairs: the two false `same` folds the blind labels caught
    (hold), six pure rewordings (fold) and three the owner's rule leaves
    open (either). Every id is unique and every label is one of the three."""
    pairs = same_probe.load_pairs(FIXTURE)
    labels = {pair["id"]: pair["label"] for pair in pairs}
    assert len(pairs) == 11 and len(labels) == 11
    assert set(labels.values()) == {"hold", "fold", "either"}
    assert sorted(i for i, lab in labels.items() if lab == "hold") == [
        "frick-el-greco-seminal", "gugg-c162-huge-atrium"
    ]
    assert sum(lab == "fold" for lab in labels.values()) == 6


def test_the_prompt_counts_a_judgment_of_significance_as_an_addition():
    """The owner's rule (2026-09-24): a judgment of importance, size,
    quality or reputation the existing claim lacks is an addition, so the
    narrator never loses the book's sense of what matters."""
    prompt = same_probe.render({"new": "N-TEXT", "existing": "E-TEXT", "place": "P"})
    assert "N-TEXT" in prompt and "E-TEXT" in prompt
    for word in ("importance", "size", "quality", "reputation"):
        assert word in prompt


def test_run_reads_every_sample_and_a_broken_answer_is_failed_not_dropped():
    pairs = same_probe.load_pairs(FIXTURE)
    batch = _scripted(
        pairs, 2, lambda pair, k: _answer("adds" if pair["label"] == "hold" else "same")
    )
    broken = same_probe.custom_id(pairs[0]["id"], 2)
    batch[broken] = llm.MockAnswer(text="not json", model_id=llm.ROLE_MODEL["claim_judge"])
    client = llm.MockClient(lambda *_: None, batch_answers=batch)

    report = same_probe.run(pairs, samples=2, client=client)

    assert len(report) == 22
    row = next(r for r in report if r["id"] == pairs[0]["id"] and r["sample"] == 2)
    assert row["verdict"] == "FAILED"
    assert all(r["verdict"] in ("same", "adds") for r in report if r is not row)


def test_score_passes_only_when_both_bars_hold():
    """S1: every hold pair answered `adds` in every sample. S2: fold pairs
    answered `same` in all but at most two of their samples. A FAILED
    sample counts against the bar of its pair."""
    pairs = same_probe.load_pairs(FIXTURE)

    def rows(verdict_of):
        return [
            {"id": p["id"], "sample": k, "verdict": verdict_of(p, k), "adds": []}
            for p in pairs for k in (1, 2, 3)
        ]

    right = rows(lambda p, k: "adds" if p["label"] == "hold" else "same")
    assert same_probe.score(right, pairs)["pass"] is True

    one_miss = rows(lambda p, k: "same" if (p["id"] == "gugg-c162-huge-atrium" and k == 3)
                    else ("adds" if p["label"] == "hold" else "same"))
    scored = same_probe.score(one_miss, pairs)
    assert (scored["S1"]["pass"], scored["pass"]) == (False, False)

    two_holds = rows(lambda p, k: "adds" if p["label"] == "hold" or
                     (p["id"] in ("frick-admission", "met-address") and k == 1) else "same")
    assert same_probe.score(two_holds, pairs)["S2"]["pass"] is True

    three_holds = rows(lambda p, k: "adds" if p["label"] == "hold" or
                       (p["id"] in ("frick-admission", "met-address", "gugg-1959") and k == 1)
                       else "same")
    assert same_probe.score(three_holds, pairs)["S2"]["pass"] is False

    failed_fold = rows(lambda p, k: "FAILED" if (p["label"] == "fold" and k <= 3
                                                 and p["id"] == "frick-admission")
                       else ("adds" if p["label"] == "hold" else "same"))
    assert same_probe.score(failed_fold, pairs)["S2"]["pass"] is False


def test_without_yes_it_prints_the_estimate_and_spends_nothing(tmp_path, capsys):
    client = llm.MockClient(lambda *_: None, batch_answers={})
    rc = script.main(["--samples", "3", "--out", str(tmp_path / "out")], client=client)
    out = capsys.readouterr().out
    assert rc == 2
    assert "estimate: $" in out and "run refused" in out
    assert client.calls == [] and not (tmp_path / "out").exists()


def test_with_yes_it_prints_the_metered_actual_and_writes_the_report(tmp_path, capsys):
    pairs = same_probe.load_pairs(FIXTURE)
    batch = _scripted(pairs, 3, lambda p, k: _answer("adds" if p["label"] == "hold" else "same"))
    client = llm.MockClient(lambda *_: None, batch_answers=batch)

    rc = script.main(["--samples", "3", "--out", str(tmp_path / "out"), "--yes"], client=client)

    out = capsys.readouterr().out
    assert rc == 0
    price_in, price_out = llm.PRICES_USD_PER_MTOK[llm.ROLE_MODEL["claim_judge"]]
    expected = 33 * (400 * price_in + 60 * price_out) / 1_000_000 * llm.BATCH_DISCOUNT
    assert f"spend: actual=${expected:.4f}" in out
    report = json.loads((tmp_path / "out" / "same-probe-report.json").read_text())
    assert len(report["samples"]) == 33 and report["score"]["pass"] is True
    # The report names what it measured, so it is never scored against another setup.
    import hashlib

    inputs = report["inputs"]
    assert inputs["fixture_sha256"] == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert inputs["prompt_sha256"] == hashlib.sha256(
        same_probe.SAME_CHECK_PROMPT.encode("utf-8")
    ).hexdigest()
    assert inputs["samples"] == 3 and inputs["model"] == llm.ROLE_MODEL["claim_judge"]
    assert len(inputs["head"]) == 40


def test_the_prompt_never_names_what_a_pair_adds():
    """The prompt's examples must not be the scored pairs' own words, or the
    probe measures word-matching, not the rule: no word a pair's new claim
    has and its existing claim lacks (the place name aside) may appear in
    the prompt, nor any number a pair states."""
    import re

    from src.ingest.merge import _signature

    # Digits and letters split apart, so "10am" still leaks "10".
    prompt_words = set(re.findall(r"\d+|[a-z]+", same_probe.SAME_CHECK_PROMPT.lower()))
    for pair in same_probe.load_pairs(FIXTURE):
        added = _signature(pair["new"]) - _signature(pair["existing"]) - _signature(pair["place"])
        numbers = set(re.findall(r"\d+", pair["new"] + " " + pair["existing"]))
        leaked = sorted((added | numbers) & prompt_words)
        assert not leaked, f"{pair['id']}: the prompt names {leaked}"
