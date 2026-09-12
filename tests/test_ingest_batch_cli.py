"""Tests for scripts/ingest_batch.py — `make ingest-batch BATCH=<id>`, the
$0 reader that recovers a finished Batch API round: what each request cost,
how it stopped, and what it answered. Slice 9's first paid job (2026-09-12)
was truncated at P1 and the runner kept only the exception's message, so
the answer already paid for — and the usage the meter never saw — was only
recoverable from the batch itself. Hermetic: the transport is stubbed.
"""

from __future__ import annotations

import json

import scripts.ingest_batch as ingest_batch
from src.tour.batch_transport import BatchUnitResult
from src.tour.certification_provider import PhysicalProviderResponse


def _result(custom_id: str, text: str, *, stop_reason: str, out: int) -> BatchUnitResult:
    return BatchUnitResult(
        custom_id=custom_id,
        result_type="succeeded",
        batch_id="msgbatch_test",
        response=PhysicalProviderResponse(
            body=text.encode("utf-8"),
            input_tokens=8787,
            output_tokens=out,
            latency_ms=0,
            model="claude-opus-5-20260601",
            provider_request_id="msg_1",
            stop_reason=stop_reason,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        ),
    )


def test_a_batch_is_recovered_with_its_usage_stop_reason_and_parsed_claims(
    tmp_path, monkeypatch, capsys
):
    """One truncated P1 answer and one clean one: the reader prints, per
    request, how it stopped, the model, the tokens, the priced cost (Opus
    at the batch rate), and how many claim items the answer carried before
    it broke off — and writes the whole round to `{out}/{batch_id}.json`
    so nothing bought is lost again."""
    truncated = json.dumps({"claims": [{"text": "a", "kind": "state", "span": "s"}] * 3})[:-10]
    clean = json.dumps({"claims": [{"text": "b", "kind": "event", "span": "t"}]})
    monkeypatch.setattr(
        ingest_batch,
        "collect_results",
        lambda batch_id, **_kw: {
            "u-a1": _result("u-a1", truncated, stop_reason="max_tokens", out=8000),
            "u-a2": _result("u-a2", clean, stop_reason="end_turn", out=40),
        },
    )

    rc = ingest_batch.main(["--batch", "msgbatch_test", "--out", str(tmp_path)])

    out = capsys.readouterr().out
    assert rc == 0
    assert "u-a1 succeeded stop=max_tokens" in out and "in=8787 out=8000" in out
    assert "claims_parsed=2 (truncated JSON: partial)" in out
    assert "u-a2 succeeded stop=end_turn" in out and "claims_parsed=1" in out
    assert "usd=" in out
    saved = json.loads((tmp_path / "msgbatch_test.json").read_text(encoding="utf-8"))
    assert saved["u-a1"]["stop_reason"] == "max_tokens"
    assert saved["u-a1"]["text"] == truncated
    assert saved["u-a2"]["output_tokens"] == 40


def test_text_tokens_split_the_answer_from_the_thinking(tmp_path, monkeypatch, capsys):
    """The judge's condition on the truncation finding: the thinking share
    of a capped answer must be MEASURED, not inferred. `--text-tokens`
    counts the recovered answer text with the provider's free
    count_tokens endpoint (the author's configured model) and prints it
    beside the billed output, so non-text = out - text is exact."""
    clean = json.dumps({"claims": [{"text": "b", "kind": "event", "span": "t"}]})
    monkeypatch.setattr(
        ingest_batch,
        "collect_results",
        lambda batch_id, **_kw: {
            "u-a1": _result("u-a1", clean, stop_reason="max_tokens", out=8000)
        },
    )
    counted: list[tuple[str, str]] = []

    def stub_count(model_id: str, text: str) -> int:
        counted.append((model_id, text))
        return 2900

    monkeypatch.setattr(ingest_batch, "_live_count_tokens", lambda: stub_count)

    rc = ingest_batch.main(["--batch", "msgbatch_test", "--out", str(tmp_path), "--text-tokens"])

    out = capsys.readouterr().out
    assert rc == 0
    assert counted == [("claude-opus-5", clean)]
    assert "text_tokens=2900 nontext_tokens=5100" in out
    saved = json.loads((tmp_path / "msgbatch_test.json").read_text(encoding="utf-8"))
    assert saved["u-a1"]["text_tokens"] == 2900
