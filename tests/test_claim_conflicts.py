"""Tests for scripts/claim_conflicts.py — Docs/ingestion/rebuild-spec.md
slice 6 (the city-wide conflict report behind `make claim-conflicts CITY=`).

P6 sees one story at one place; this pass finds the same fact stated with
different values ACROSS beats. Everything here runs over synthetic beats
whose chunks live under tmp_path, so grounding is real and no book, DB or
model is touched (test_no_live_client_in_this_file, the ingest walker).
"""

from __future__ import annotations

import ast
import json
from copy import deepcopy
from pathlib import Path

from scripts.claim_conflicts import apply_conflicts, find_conflicts, format_report, main
from src.ingest import model

CITY = "new_york"
JUDGE = "claude-haiku-4-5-20251001"
AUTHOR = "claude-opus-5"

CHUNKS = {
    ("book-a", "ch1"): (
        "The Guggenheim rises on Fifth Avenue. Wright's design was finished in 1959, "
        "and the museum opened that autumn.\n"
    ),
    ("book-b", "ch4"): (
        "Museum Mile runs along Central Park. The Guggenheim was finished in 1960 "
        "and the neighbours never forgave it.\n"
    ),
}


def _claim(claim_id: str, text: str, kind: str, source_id: str, chunk: str, span: str,
           as_of: int) -> dict:
    return {
        "claim_id": claim_id,
        "text": text,
        "kind": kind,
        "status": "resolved",
        "sources": [
            {
                "source_id": source_id,
                "chunk": chunk,
                "span": span,
                "as_of": as_of,
                "rights_basis": "owned_copy",
                "stated_value": None,
            }
        ],
        "resolved_value": None,
        "resolution": None,
        "verdict": {"judge_model": JUDGE, "entailed": True, "bound_to": model.bind(text, span)},
    }


def _beat(poi: str, slug: str, title: str, claims: list[dict], narration: str) -> dict:
    return {
        "beat_id": f"{CITY}/{model.slug(poi)}/{slug}",
        "city_name": CITY,
        "poi_name": poi,
        "story_slug": slug,
        "title": title,
        "beat_type": "anecdote",
        "lenses": ["hidden_history"],
        "sub_location": None,
        "trigger_address": None,
        "claims": claims,
        "narration": {
            "text": narration,
            "claims_hash": model.claims_hash(claims),
            "author_model": AUTHOR,
            "verdict": {
                "judge_model": JUDGE,
                "sentences_entailed": 2,
                "sentences_total": 2,
                "bound_to": model.bind(narration),
            },
            "flags": [],
        },
        "physical_cues": [],
        "entities": [],
        "narrative_function": None,
        "emotional_register": None,
        "sensory_anchor": False,
        "inline_foreign_phrases": [],
        "pronunciation": None,
        "kid_friendly": None,
        "duration_sec": 10,
        "review": {"held": False, "reason": None},
    }


GUGGENHEIM_ID = f"{CITY}/guggenheim-museum/how-it-was-built"
MILE_ID = f"{CITY}/museum-mile/along-the-park"


def _records() -> list[dict]:
    """Two beats at different places that state the completion year
    differently, plus a claim at each that no other beat shares."""
    guggenheim = _beat(
        "Guggenheim Museum",
        "how-it-was-built",
        "How it was built",
        [
            _claim(
                "c01", "The Guggenheim building was finished in 1959.", "event",
                "book-a", "ch1", "finished in 1959", 2023,
            ),
            _claim(
                "c02", "The museum opened in the autumn of its completion year.", "event",
                "book-a", "ch1", "the museum opened that autumn", 2023,
            ),
        ],
        "The building was done in 1959 and opened in the autumn.",
    )
    mile = _beat(
        "Museum Mile",
        "along-the-park",
        "Along the park",
        [
            _claim(
                "c01", "Museum Mile runs along Central Park.", "state",
                "book-b", "ch4", "Museum Mile runs along Central Park", 2024,
            ),
            _claim(
                "c02", "The Guggenheim building was finished in 1960.", "event",
                "book-b", "ch4", "finished in 1960", 2024,
            ),
        ],
        "The mile follows the park. The Guggenheim was done in 1960.",
    )
    return [guggenheim, mile]


def _write(tmp_path: Path, records: list) -> tuple[Path, Path]:
    data_root = tmp_path / "data"
    (data_root / CITY).mkdir(parents=True)
    beats_path = data_root / CITY / "beats.json"
    beats_path.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n")
    books_root = tmp_path / "Books"
    for (source_id, chunk), text in CHUNKS.items():
        (books_root / CITY / source_id).mkdir(parents=True, exist_ok=True)
        (books_root / CITY / source_id / f"{chunk}.txt").write_text(text)
    return data_root, books_root


def test_same_fact_with_different_values_across_beats_makes_both_claims_contested(tmp_path):
    """Two beats at different places state the completion year as 1959 and
    1960: the same fact by P6's own signature test, different number
    tokens. Both claims become contested — each gaining the other's source
    with the value it states, the shape the validator demands — with their
    texts and judges untouched and their verdicts re-bound. The claims that
    share no fact are left alone, and the two changed beats are the ones
    whose narration hash is now stale by design."""
    records = _records()
    _data_root, books_root = _write(tmp_path, records)
    assert model.validate(records, chunks_root=books_root / CITY) == []

    conflicts = find_conflicts(records)

    assert [(c.beat_a, c.claim_a, c.beat_b, c.claim_b) for c in conflicts] == [
        (GUGGENHEIM_ID, "c01", MILE_ID, "c02")
    ]
    assert (conflicts[0].values_a, conflicts[0].values_b) == ("1959", "1960")
    assert "1959" in format_report(conflicts) and "1960" in format_report(conflicts)
    assert GUGGENHEIM_ID in format_report(conflicts)

    before = deepcopy(records)
    applied, rerun = apply_conflicts(records, conflicts)

    assert records == before  # pure
    assert rerun == [GUGGENHEIM_ID, MILE_ID]
    g_claim = applied[0]["claims"][0]
    m_claim = applied[1]["claims"][1]
    assert g_claim["status"] == "contested" and m_claim["status"] == "contested"
    assert g_claim["text"] == before[0]["claims"][0]["text"]
    assert [(s["source_id"], s["span"], s["stated_value"]) for s in g_claim["sources"]] == [
        ("book-a", "finished in 1959", "1959"),
        ("book-b", "finished in 1960", "1960"),
    ]
    assert [(s["source_id"], s["span"], s["stated_value"]) for s in m_claim["sources"]] == [
        ("book-b", "finished in 1960", "1960"),
        ("book-a", "finished in 1959", "1959"),
    ]
    assert g_claim["verdict"]["judge_model"] == JUDGE
    assert g_claim["verdict"]["bound_to"] == model.bind(
        g_claim["text"], "finished in 1959\nfinished in 1960"
    )
    assert g_claim["resolution"] is None and g_claim["resolved_value"] is None
    assert applied[0]["claims"][1] == before[0]["claims"][1]
    assert applied[1]["claims"][0] == before[1]["claims"][0]
    assert applied[0]["narration"] == before[0]["narration"]

    errors = model.validate(applied, chunks_root=books_root / CITY)
    assert errors == [
        f"NARRATION_HASH_STALE {GUGGENHEIM_ID}: narration.claims_hash does not match "
        "a fresh recomputation over this beat's claims",
        f"NARRATION_HASH_STALE {MILE_ID}: narration.claims_hash does not match "
        "a fresh recomputation over this beat's claims",
    ]

    # Already contested, or agreeing values, is not a conflict.
    assert find_conflicts(applied) == []
    agreeing = deepcopy(records)
    agreeing[1]["claims"][1]["text"] = "The Guggenheim building was finished in 1959."
    assert find_conflicts(agreeing) == []


def test_report_mode_writes_nothing_and_apply_rewrites_only_a_valid_file(tmp_path, capsys):
    """`make claim-conflicts CITY=` reports and exits 0 without touching the
    file; `ARGS=--apply` rewrites it with both claims contested and says
    which beats need a P4/P5 rerun. A legacy-shape record is counted,
    skipped and written back unchanged. A file that would not validate
    after the change is never written."""
    legacy = {"beat_id": f"{CITY}/old/legacy", "script_body": "old prose", "key_claims": []}
    records = [*_records(), legacy]
    data_root, books_root = _write(tmp_path, records)
    beats_path = data_root / CITY / "beats.json"
    original = beats_path.read_text()

    argv = ["--city", CITY, "--data-root", str(data_root), "--books-root", str(books_root)]
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert "1 conflict" in out
    assert "1959" in out and "1960" in out
    assert "1 legacy-shape record skipped" in out
    assert beats_path.read_text() == original

    assert main([*argv, "--apply"]) == 0
    out = capsys.readouterr().out
    assert f"rerun P4/P5: {GUGGENHEIM_ID}, {MILE_ID}" in out
    written = json.loads(beats_path.read_text())
    assert written[2] == legacy
    assert written[0]["claims"][0]["status"] == "contested"
    assert written[1]["claims"][1]["status"] == "contested"
    assert len(written[0]["claims"][0]["sources"]) == 2

    # A second apply finds nothing and leaves the file alone.
    after_apply = beats_path.read_text()
    assert main([*argv, "--apply"]) == 0
    assert "0 conflicts" in capsys.readouterr().out
    assert beats_path.read_text() == after_apply

    # A change that would break validation is refused before any write.
    broken = _records()
    broken[1]["claims"][1]["sources"][0]["span"] = "not in the chunk"
    beats_path.write_text(json.dumps(broken, indent=2) + "\n")
    untouched = beats_path.read_text()
    assert main([*argv, "--apply"]) == 1
    assert "SPAN_NOT_VERBATIM" in capsys.readouterr().out
    assert beats_path.read_text() == untouched


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
