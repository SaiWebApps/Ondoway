"""Tests for the /beat-dedup pipeline.

Covers:
- MinHash surfaces known near-duplicates; ignores distant beats.
- Haiku judge happy path, retry, and fallback on parse failure.
- Apply semantics (SKIP / COMBINE / KEEP_BOTH) + 1:1 audit log.
- API key never appears in the rendered report or the jsonl log.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

from scripts import beat_dedup
from scripts.beat_dedup_judge import classify_pair
from scripts.dedup_pairs import find_pairs


def _hash(body: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", body.lower().strip()).encode("utf-8")).hexdigest()


def _beat(
    bid: str,
    body: str,
    *,
    poi: str = "Val-de-Grace",
    lens: str = "hidden_history",
    topic: str = "royal_vow",
    book: str = "around_and_about_paris",
    chunk: str = "chunk-15-5th-arr-val-de-grace",
    generated_at: str = "2026-04-22T12:00:00Z",
) -> dict:
    return {
        "beat_id": bid,
        "poi_name": poi,
        "lens": lens,
        "city_name": "paris",
        "book_slug": book,
        "topic_slug": topic,
        "source_chunk_slug": chunk,
        "script_body": body,
        "script_body_hash": _hash(body),
        "_meta": {"prompt_version": "test_v1", "generated_at": generated_at},
    }


def _log_skeleton() -> dict:
    return {
        "city": "paris",
        "book_title": "Around and About Paris",
        "author": "Thirza Vallois",
        "chunks_processed": [],
    }


# ---------------------------- MinHash behavior ----------------------------


def test_minhash_surfaces_known_pair():
    body_a = (
        "Val-de-Grace was founded in 1645 by Anne of Austria in fulfilment of her vow "
        "after the birth of Louis XIV. Its dome, painted by Mignard, is decorated with "
        "over two hundred figures including portraits of the royal family of France."
    )
    body_b = (
        "Val-de-Grace was founded in 1645 by Anne of Austria in fulfilment of her vow "
        "after the birth of Louis XIV. Its dome, painted by Mignard, is decorated with "
        "over two hundred figures drawn from sacred and royal history of France."
    )
    pairs = find_pairs(
        [_beat("a1", body_a), _beat("a2", body_b, topic="anne_vow")],
        threshold=0.5,
    )
    assert len(pairs) == 1
    assert {pairs[0]["beat_a"], pairs[0]["beat_b"]} == {"a1", "a2"}
    assert pairs[0]["jaccard"] >= 0.5


def test_minhash_ignores_distant_beats():
    body_a = "The Louvre began as a fortress under Philippe-Auguste in 1190."
    body_b = "Notre-Dame's gargoyles were added during Viollet-le-Duc's 19th-century restoration."
    pairs = find_pairs(
        [_beat("x1", body_a), _beat("x2", body_b, topic="gargoyles")],
        threshold=0.5,
    )
    assert pairs == []


# --------------------------- Haiku judge behavior ---------------------------


def _fake_tool_block(classification: str, reasoning: str):
    return SimpleNamespace(
        type="tool_use",
        name="record_classification",
        input={"classification": classification, "reasoning": reasoning},
    )


def _fake_response(*blocks) -> SimpleNamespace:
    return SimpleNamespace(content=list(blocks))


class _FakeMessages:
    def __init__(self, responses: list):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("no fake responses left")
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


class _FakeClient:
    def __init__(self, responses):
        self.messages = _FakeMessages(responses)


def test_judge_happy_path():
    client = _FakeClient(
        [_fake_response(_fake_tool_block("same_story_added_detail", "both cite 1645"))]
    )
    out = classify_pair(_beat("a", "x"), _beat("b", "y"), client=client)
    assert out["classification"] == "same_story_added_detail"
    assert out["_parse_failed"] is False
    assert len(client.messages.calls) == 1


def test_haiku_parse_fail_falls_back():
    # First response: garbage classification → invalid. Second response: valid.
    client_retry_ok = _FakeClient(
        [
            _fake_response(_fake_tool_block("not_a_valid_label", "noise")),
            _fake_response(_fake_tool_block("different_story", "distinct facts")),
        ]
    )
    out = classify_pair(_beat("a", "x"), _beat("b", "y"), client=client_retry_ok)
    assert out["classification"] == "different_story"
    assert out["_parse_failed"] is False
    assert len(client_retry_ok.messages.calls) == 2

    # Both responses invalid → fallback.
    client_both_bad = _FakeClient(
        [
            _fake_response(_fake_tool_block("also_wrong", "noise")),
            _fake_response(_fake_tool_block("still_wrong", "more noise")),
        ]
    )
    out = classify_pair(_beat("a", "x"), _beat("b", "y"), client=client_both_bad)
    assert out["classification"] == "different_story"
    assert out["_parse_failed"] is True
    assert "parse failed" in out["reasoning"]


# ------------------------ apply semantics + audit log ------------------------


def _seed_files(tmp_path: Path, beats: list[dict]) -> tuple[Path, Path]:
    bp = tmp_path / "beats.json"
    lp = tmp_path / "book-log.json"
    bp.write_text(json.dumps(beats, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lp.write_text(json.dumps(_log_skeleton(), indent=2) + "\n", encoding="utf-8")
    return bp, lp


def test_apply_skip_removes_newer(tmp_path):
    older = _beat("older", "Val-de-Grace was founded in 1645.", generated_at="2025-01-01T00:00:00Z")
    newer = _beat(
        "newer",
        "The abbey of Val-de-Grace dates from the 17th century.",
        topic="new_angle",
        generated_at="2026-04-22T12:00:00Z",
    )
    bp, lp = _seed_files(tmp_path, [older, newer])
    decisions = [
        {
            "beat_a": "older",
            "beat_b": "newer",
            "jaccard": 0.6,
            "classification": "same_story_same_wording",
            "action": "SKIP",
        }
    ]
    result = beat_dedup.apply_decisions(
        decisions, beats_path=bp, log_path=lp, city="paris", out_dir=tmp_path / "_dedup_review"
    )
    remaining = json.loads(bp.read_text(encoding="utf-8"))
    remaining_ids = {b["beat_id"] for b in remaining}
    assert remaining_ids == {"older"}
    assert result["mutations"] == 1


def test_apply_combine_replaces_both(tmp_path):
    a = _beat("a", "First phrasing about the vow.", topic="vow_a")
    b = _beat("b", "Second phrasing about the vow.", topic="vow_b")
    bp, lp = _seed_files(tmp_path, [a, b])
    merged = "The merged account of Anne of Austria's vow and Val-de-Grace's founding."
    decisions = [
        {
            "beat_a": "a",
            "beat_b": "b",
            "jaccard": 0.55,
            "classification": "same_story_enhanced_content",
            "action": "COMBINE",
            "merged_text": merged,
        }
    ]
    beat_dedup.apply_decisions(
        decisions, beats_path=bp, log_path=lp, city="paris", out_dir=tmp_path / "_dedup_review"
    )
    remaining = json.loads(bp.read_text(encoding="utf-8"))
    assert len(remaining) == 1
    merged_beat = remaining[0]
    assert merged_beat["script_body"] == merged
    assert merged_beat["script_body_hash"] == _hash(merged)
    assert merged_beat["merged_from"] == ["a", "b"]
    assert merged_beat["_meta"]["prompt_version"] == "dedup_merge_v1"


def test_apply_combine_resets_verification(tmp_path):
    """A COMBINE that rewrites the body must drop an inherited verified badge —
    otherwise commit blocks it (VERIFICATION_STALE) and a 'verified' status would
    sit on text no human checked."""
    a = _beat("a", "First phrasing about the vow.", topic="vow_a")
    a["fact_check"] = {"status": "verified", "verified_body_hash": a["script_body_hash"]}
    b = _beat("b", "Second phrasing about the vow.", topic="vow_b")
    bp, lp = _seed_files(tmp_path, [a, b])
    decisions = [
        {
            "beat_a": "a",
            "beat_b": "b",
            "jaccard": 0.55,
            "classification": "same_story_enhanced_content",
            "action": "COMBINE",
            "merged_text": "The merged account of the royal vow and the abbey's founding.",
        }
    ]
    beat_dedup.apply_decisions(
        decisions, beats_path=bp, log_path=lp, city="paris", out_dir=tmp_path / "_dedup_review"
    )
    merged = json.loads(bp.read_text(encoding="utf-8"))[0]
    assert merged["fact_check"]["status"] == "unverified"
    assert "verified_body_hash" not in merged["fact_check"]


def test_apply_skip_keeps_verified_over_unverified(tmp_path):
    """SKIP must not drop a verified beat for an unverified duplicate, even when
    the verified one is 'newer' (which the timestamp tie-break would otherwise drop)."""
    older = _beat("older", "Older unverified phrasing.", generated_at="2025-01-01T00:00:00Z")
    newer = _beat(
        "newer", "Newer verified phrasing.", topic="new_angle", generated_at="2026-04-22T12:00:00Z"
    )
    newer["fact_check"] = {"status": "verified", "verified_body_hash": newer["script_body_hash"]}
    bp, lp = _seed_files(tmp_path, [older, newer])
    decisions = [
        {
            "beat_a": "older",
            "beat_b": "newer",
            "jaccard": 0.6,
            "classification": "same_story_same_wording",
            "action": "SKIP",
        }
    ]
    beat_dedup.apply_decisions(
        decisions, beats_path=bp, log_path=lp, city="paris", out_dir=tmp_path / "_dedup_review"
    )
    remaining_ids = {b["beat_id"] for b in json.loads(bp.read_text(encoding="utf-8"))}
    assert remaining_ids == {"newer"}


def test_apply_keep_both_flags_both(tmp_path):
    a = _beat("a", "Story one.", topic="one")
    b = _beat("b", "Story two.", topic="two", lens="visual_art")
    bp, lp = _seed_files(tmp_path, [a, b])
    decisions = [
        {
            "beat_a": "a",
            "beat_b": "b",
            "jaccard": 0.82,
            "classification": "different_story",
            "action": "KEEP_BOTH",
        }
    ]
    beat_dedup.apply_decisions(
        decisions, beats_path=bp, log_path=lp, city="paris", out_dir=tmp_path / "_dedup_review"
    )
    remaining = {b["beat_id"]: b for b in json.loads(bp.read_text(encoding="utf-8"))}
    assert remaining["a"]["dedup_reviewed"] is True
    assert remaining["b"]["dedup_reviewed"] is True


def test_audit_log_matches_mutation(tmp_path):
    a = _beat("a", "Story one.", topic="one")
    b = _beat("b", "Story two.", topic="two", lens="visual_art")
    c = _beat("c", "Story three.", topic="three", lens="literary_heritage")
    d = _beat("d", "Story four.", topic="four", lens="faith_spirituality")
    bp, lp = _seed_files(tmp_path, [a, b, c, d])
    decisions = [
        {
            "beat_a": "a",
            "beat_b": "b",
            "jaccard": 0.5,
            "classification": "different_story",
            "action": "KEEP_BOTH",
        },
        {
            "beat_a": "c",
            "beat_b": "d",
            "jaccard": 0.9,
            "classification": "same_story_same_wording",
            "action": "SKIP",
        },
    ]
    out_dir = tmp_path / "_dedup_review"
    beat_dedup.apply_decisions(decisions, beats_path=bp, log_path=lp, city="paris", out_dir=out_dir)
    log_lines = (out_dir / "_log.jsonl").read_text(encoding="utf-8").splitlines()
    entries = [json.loads(line) for line in log_lines if line.strip()]
    assert len(entries) == 2
    required = {"ts", "pair", "jaccard", "classification", "action"}
    for e in entries:
        assert required <= set(e)
    assert entries[0]["action"] == "KEEP_BOTH"
    assert entries[1]["action"] == "SKIP"
    assert entries[0]["pair"] == ["a", "b"]
    assert entries[1]["pair"] == ["c", "d"]


def test_report_contains_no_api_key(tmp_path, monkeypatch):
    """A rendered report + audit log must not contain any ANTHROPIC_API_KEY value."""
    leak = "sk-ant-TEST-KEY-DO-NOT-LEAK"
    monkeypatch.setenv("ANTHROPIC_API_KEY", leak)

    body_a = (
        "Val-de-Grace was founded in 1645 by Anne of Austria in fulfilment of her vow "
        "after the birth of Louis XIV. Its dome, painted by Mignard, is decorated with "
        "over two hundred figures including portraits of the royal family of France."
    )
    body_b = (
        "Val-de-Grace was founded in 1645 by Anne of Austria in fulfilment of her vow "
        "after the birth of Louis XIV. Its dome, painted by Mignard, is decorated with "
        "over two hundred figures drawn from sacred and royal history of France."
    )
    beats = [_beat("p1", body_a, topic="vow_1"), _beat("p2", body_b, topic="vow_2")]
    bp, lp = _seed_files(tmp_path, beats)

    def fake_classifier(_a, _b):
        return {
            "classification": "same_story_added_detail",
            "reasoning": "both describe Anne's vow",
            "_parse_failed": False,
        }

    out_dir = tmp_path / "_dedup_review"
    report = beat_dedup.run_report(
        "paris",
        beats_path=bp,
        log_path=lp,
        threshold=0.3,
        classifier=fake_classifier,
        out_dir=out_dir,
    )
    md_content = Path(report["report_path"]).read_text(encoding="utf-8")
    assert leak not in md_content
    assert "sk-ant" not in md_content

    # Apply with a decision and re-check the audit log
    decisions = [
        {
            "beat_a": report["pairs"][0]["beat_a"],
            "beat_b": report["pairs"][0]["beat_b"],
            "jaccard": report["pairs"][0]["jaccard"],
            "classification": "same_story_added_detail",
            "action": "INSERT",
        }
    ]
    beat_dedup.apply_decisions(decisions, beats_path=bp, log_path=lp, city="paris", out_dir=out_dir)
    log_content = (out_dir / "_log.jsonl").read_text(encoding="utf-8")
    assert leak not in log_content
    assert "sk-ant" not in log_content
