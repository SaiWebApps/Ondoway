"""The batch re-author writes candidates to their own file, never to the corpus.

Lane B rewrites 524 beats across Paris and New York. That is 524 paid calls, so
the run must be resumable — a crash at beat 400 must not buy the first 400 again
— and its output must land somewhere a human reviews before anything reaches
`beats.json`.
"""

from __future__ import annotations

import json

from scripts.reauthor_run import (
    already_done,
    make_record,
    pending_beats,
    review_order,
)


def _beat(beat_id: str, body: str, source: str) -> dict:
    return {
        "beat_id": beat_id,
        "poi_name": "Somewhere",
        "script_body": body,
        "source_passage": source,
    }


def test_record_carries_both_bodies_and_what_the_gate_flagged() -> None:
    """A reviewer needs the old body, the new one, and why it was doubted."""
    lifted = "The tower was completed in 1889 for the World's Fair and stood twenty years."
    beat = _beat("b1", lifted, lifted + " It was meant to come down again.")
    rewritten = "Eiffel raised his iron frame that year, and the city planned to remove it."
    record = make_record(beat, rewritten=rewritten, ungrounded=[rewritten])

    assert record["beat_id"] == "b1"
    assert record["body_before"] == lifted
    assert record["body_after"] == rewritten
    assert record["ungrounded"] == [rewritten]
    assert record["flags"] == 1
    # The reviewer judges the rewrite against the source, so it travels with it.
    assert record["source_passage"].startswith(lifted)
    assert record["ratio_after"] < record["ratio_before"]


def test_a_beat_already_rewritten_is_not_bought_twice() -> None:
    """Resume is the whole point: 524 paid calls must survive one crash."""
    existing = [{"beat_id": "done"}, {"beat_id": "also-done"}]
    assert already_done(existing) == {"done", "also-done"}

    lifted = "One two three four five six seven eight nine ten eleven twelve."
    beats = [
        _beat("done", lifted, lifted),
        _beat("fresh", lifted, lifted),
    ]
    pending = pending_beats(beats, done={"done"})

    assert [b["beat_id"] for b in pending] == ["fresh"]


def test_pending_only_covers_copied_beats_with_a_source() -> None:
    """The run targets the copied backlog, not the whole corpus."""
    lifted = "One two three four five six seven eight nine ten."
    beats = [
        _beat("copied", lifted, lifted),
        _beat("authored", "Entirely different words than the source had.", lifted),
        _beat("untraceable", lifted, ""),
    ]
    assert [b["beat_id"] for b in pending_beats(beats, done=set())] == ["copied"]


def test_review_order_puts_the_most_doubted_first() -> None:
    """The gate cries wolf on paraphrase, so it orders the queue instead of judging it."""
    records = [
        {"beat_id": "clean", "flags": 0, "ratio_after": 0.1},
        {"beat_id": "doubted", "flags": 3, "ratio_after": 0.0},
        {"beat_id": "one-flag", "flags": 1, "ratio_after": 0.0},
    ]
    assert [r["beat_id"] for r in review_order(records)] == ["doubted", "one-flag", "clean"]


def test_the_run_never_names_the_corpus_file(tmp_path) -> None:
    """Candidates land beside the corpus, never in it."""
    from scripts.reauthor_run import output_path

    path = output_path("paris", data_dir=tmp_path)
    assert path.name == "reauthored.json"
    assert path.name != "beats.json"
    assert path.parent.name == "paris"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([]), encoding="utf-8")
    assert already_done(json.loads(path.read_text(encoding="utf-8"))) == set()
