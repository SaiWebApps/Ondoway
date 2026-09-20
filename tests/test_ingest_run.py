"""Tests for src/ingest/run.py and src/ingest/jobs.py — Docs/ingestion/
rebuild-spec.md slice 7 (the job runner: P0 intake through P7 commit,
phase outputs written to the job as they land, resume at the last
completed phase, the D13 review queue).

Every test runs against llm.MockClient over the scripted Guggenheim job in
tests/ingest_job_script.py (the real Lonely Planet chunk-07 unit); no live
client, no network, no spend (test_no_live_client_in_this_file, the same
walker as the slice-3..6 files). Nothing here touches the repo's data/ —
every job writes under a per-test data root.
"""

from __future__ import annotations

import ast
import json
import shutil
from pathlib import Path

import pytest

from src.ingest import jobs, judge_claims, judge_narration, llm, model, narrate, run
from src.ingest import unit as unit_mod
from tests import ingest_job_script as script_mod


def _sink_and_events():
    events: list[tuple[str, dict]] = []

    def sink(kind: str, payload: dict) -> None:
        events.append((kind, payload))

    return events, sink


def _book_job(store: jobs.IngestJobStore, chunks: Path) -> jobs.IngestJob:
    return store.create(
        city=script_mod.CITY,
        source={"kind": "book", "chunk_dir": str(chunks)},
        as_of=2023,
        rights_basis="owned_copy",
    )


def test_job_runs_p0_to_p7_on_the_mock_and_commits_a_valid_file(tmp_path):
    """The spec's shape end to end: a book job over one chunk dir intakes
    the unit (P0), decomposes it (P1), groups one story (P2), judges its
    claims and checks the unit for omissions (P3), narrates (P4), judges
    every sentence (P5), finds no beat at the place so the story is new
    without a merge call (P6, logged), assembles the record — beat_id from
    city/slug(place)/story_slug, the story's enrichment, P5's narration,
    the spoken duration, review not held — and commits it through the
    slice-1 validator with the chunks root (P7). The cost estimate is the
    job's first event; the phases land in order; the file on disk
    validates with its spans grounded in the real chunk; the book log
    records the chunk."""
    chunks = script_mod.chunk_dir(tmp_path)
    data_root = script_mod.data_dir(tmp_path)
    store = jobs.IngestJobStore()
    job = _book_job(store, chunks)
    unit = script_mod.unit(chunks)
    _events, sink = _sink_and_events()
    client = llm.MockClient(sink, **script_mod.script(unit))

    run.run_job(job.id, store, client, data_root=data_root)

    snap = store.snapshot(job.id)
    assert snap.status == "committed", snap.error
    assert snap.events[0].message == "cost_estimate"
    assert {row["phase"] for row in snap.events[0].data["rows"]} == {
        "P1", "P2", "P3", "P4", "P5", "P6"
    }
    assert snap.events[0].data["units"] == 1
    phases = [e.message for e in snap.events if e.kind == "phase"]
    assert phases == ["P0", "P1", "P2", "P3", "P4", "P5", "P6", "P7"]
    assert client.calls == [
        ("author", "P1"),
        ("author", "P2"),
        ("claim_judge", "P3"),
        ("claim_judge", "P3"),
        ("claim_judge", "P3"),
        ("claim_judge", "P3"),
        ("author", "P4"),
        ("narration_judge", "P5"),
        ("narration_judge", "P5"),
        ("narration_judge", "P5"),
    ]
    logged = [e.message for e in snap.events if e.kind == "info"]
    assert "merge_skipped" in logged
    assert "beat_held" not in logged

    beats_path = data_root / script_mod.CITY / "beats.json"
    records = json.loads(beats_path.read_text(encoding="utf-8"))
    assert [r["beat_id"] for r in records] == [script_mod.BEAT_ID]
    beat = records[0]
    assert beat["city_name"] == script_mod.CITY
    assert beat["poi_name"] == script_mod.PLACE
    assert beat["story_slug"] == script_mod.STORY_SLUG
    assert beat["title"] == script_mod.STORY_TITLE
    assert beat["beat_type"] == "anecdote"
    assert beat["lenses"] == ["hidden_history", "visual_art"]
    assert [c["text"] for c in beat["claims"]] == [c["text"] for c in script_mod.CLAIMS]
    assert [c["claim_id"] for c in beat["claims"]] == ["c01", "c02", "c03"]
    assert all(c["status"] == "resolved" for c in beat["claims"])
    assert [(s["source_id"], s["chunk"], s["span"], s["as_of"], s["rights_basis"])
            for c in beat["claims"] for s in c["sources"]] == [
        (script_mod.LP_SOURCE, script_mod.LP_CHUNK, c["span"], 2023, "owned_copy")
        for c in script_mod.CLAIMS
    ]
    assert {c["verdict"]["judge_model"] for c in beat["claims"]} == {
        script_mod.RESPONSE_JUDGE_MODEL
    }
    assert beat["narration"]["text"] == script_mod.NARRATION
    assert beat["narration"]["author_model"] == script_mod.RESPONSE_AUTHOR_MODEL
    assert beat["narration"]["verdict"]["judge_model"] == script_mod.RESPONSE_JUDGE_MODEL
    assert beat["narration"]["verdict"]["sentences_entailed"] == 3
    assert beat["narration"]["verdict"]["sentences_total"] == 3
    assert beat["narration"]["flags"] == []
    assert beat["entities"] == script_mod.ENRICHMENT["entities"]
    assert beat["narrative_function"] == "establishing"
    assert beat["kid_friendly"] == "yes"
    assert beat["duration_sec"] == narrate.duration_sec(script_mod.NARRATION)
    assert beat["review"] == {"held": False, "reason": None}
    assert model.validate(records, chunks_root=chunks.parent) == []

    log = json.loads((data_root / script_mod.CITY / "book-log.json").read_text(encoding="utf-8"))
    entry = next(b for b in log["books_processed"] if b["book_slug"] == script_mod.LP_SOURCE)
    assert entry["chunks_processed"] == [
        {"chunk": script_mod.LP_CHUNK, "beats_extracted": 1, "pois_touched": [script_mod.PLACE]}
    ]


class _RecordingClient:
    """Records every call's (role, phase) in order; forwards to the mock."""

    def __init__(self, client) -> None:
        self._client = client
        self.calls: list[tuple[str, str]] = []
        self.batches: list[tuple[str, list[str], list[str]]] = []  # phase, ids, prompts

    def count_tokens(self, model_id, text):
        return self._client.count_tokens(model_id, text)

    def estimate(self, units, plan, **kwargs):
        return self._client.estimate(units, plan, **kwargs)

    def complete(self, role, prompt, schema, *, phase, max_tokens):
        self.calls.append((role, phase))
        return self._client.complete(role, prompt, schema, phase=phase, max_tokens=max_tokens)

    def complete_batch(self, role, prompts, schema, *, phase, max_tokens):
        self.calls.append((role, phase))
        self.batches.append((phase, [cid for cid, _ in prompts], [p for _, p in prompts]))
        return self._client.complete_batch(
            role, prompts, schema, phase=phase, max_tokens=max_tokens
        )


def test_each_phase_output_lands_before_the_next_and_a_job_resumes_where_it_died(tmp_path):
    """§3: a phase's output is written to the job before the next phase
    starts, so a failed job resumes at its last completed phase. A client
    with no P3 answers kills the job in P3 (the mock never fabricates):
    the job is `error`, its P0/P1/P2 outputs are on it — the P2 output
    already names the story — and nothing was written. Calling run_job
    again with a fully scripted client makes NO P1 or P2 call: it starts
    at P3 and runs to a committed, valid file."""
    chunks = script_mod.chunk_dir(tmp_path)
    data_root = script_mod.data_dir(tmp_path)
    store = jobs.IngestJobStore()
    job = _book_job(store, chunks)
    unit = script_mod.unit(chunks)
    _events, sink = _sink_and_events()
    full = script_mod.script(unit)
    no_p3 = {
        "answers": full["answers"],
        "batch_answers": {unit.custom_id(1): full["batch_answers"][unit.custom_id(1)]},
    }

    run.run_job(job.id, store, llm.MockClient(sink, **no_p3), data_root=data_root)

    snap = store.snapshot(job.id)
    assert snap.status == "error"
    assert "no scripted batch answer" in (snap.error or "")
    assert [e.message for e in snap.events if e.kind == "phase"] == ["P0", "P1", "P2"]
    assert set(store.get(job.id).phases) == {"P0", "P1", "P2"}
    p2 = store.phase_output(job.id, "P2")
    assert [s["story_slug"] for s in p2["stories"][unit.key]] == [script_mod.STORY_SLUG]
    assert not (data_root / script_mod.CITY / "beats.json").exists()

    # The resumed run skips P2, so the author's sync answers start at P4.
    resumed = {
        "answers": {"author": [script_mod.narration_answer()]},
        "batch_answers": full["batch_answers"],
    }
    recorder = _RecordingClient(llm.MockClient(sink, **resumed))
    run.run_job(job.id, store, recorder, data_root=data_root)

    snap = store.snapshot(job.id)
    assert snap.status == "committed", snap.error
    assert recorder.calls == [
        ("claim_judge", "P3"),
        ("claim_judge", "P3"),
        ("author", "P4"),
        ("narration_judge", "P5"),
    ]
    assert [e.message for e in snap.events if e.kind == "phase"] == [
        "P0", "P1", "P2", "P3", "P4", "P5", "P6", "P7"
    ]
    records = json.loads((data_root / script_mod.CITY / "beats.json").read_text(encoding="utf-8"))
    assert [r["beat_id"] for r in records] == [script_mod.BEAT_ID]
    assert model.validate(records, chunks_root=chunks.parent) == []


def test_an_omission_finding_adds_claims_for_the_omitted_facts_and_keeps_the_first_pass(tmp_path):
    """§3 P3, as amended in slice 10 (owner ruling A, 2026-09-19): job A's one
    omission re-ask regenerated the whole unit and re-grouped it, throwing
    away a good first pass (the Wright claim regrouped into an itinerary and
    held, the Guggenheim renamed, the claim count doubled). The re-ask now
    asks ONLY for the omitted facts — the claims already extracted are shown
    so they are not repeated — and the new claims are numbered after the
    first pass, grouped on their own and judged on their own. The first
    pass's claims and story are untouched: one P1 re-ask, no second P2 or P3
    over the first pass, and both stories reach the file."""
    chunks = script_mod.chunk_dir(tmp_path)
    data_root = script_mod.data_dir(tmp_path)
    store = jobs.IngestJobStore()
    job = _book_job(store, chunks)
    unit = script_mod.unit(chunks)
    _events, sink = _sink_and_events()
    first = [c["text"] for c in script_mod.CLAIMS]
    scripted = {
        "answers": {
            "author": [
                script_mod.stories_answer(),
                script_mod.ticket_story_answer("c04"),
                script_mod.narration_answer(),
                script_mod.narration_answer(script_mod.TICKET_SENTENCE),
            ]
        },
        "batch_answers": {
            unit.custom_id(1): script_mod.claims_answer(),
            unit.custom_id(2): script_mod.claims_answer([script_mod.TICKET]),
            **script_mod.verdicts(unit, ["c01", "c02", "c03", "c04"]),
            **script_mod.omissions_answer(
                unit, [{"fact": script_mod.TICKET["text"], "span": script_mod.TICKET["span"]}]
            ),
            **script_mod.sentence_verdicts(first),
            **script_mod.sentence_verdicts(
                [script_mod.TICKET["text"]], script_mod.TICKET_SENTENCE
            ),
        },
    }
    recorder = _RecordingClient(llm.MockClient(sink, **scripted))

    run.run_job(job.id, store, recorder, data_root=data_root)

    snap = store.snapshot(job.id)
    assert snap.status == "committed", snap.error
    # Exactly one omission check, then one supplement: P1 → P2 → P3 judge →
    # P3 omissions → P1 supplement → P2 over the added claims only → P3 over
    # them only → P4 per story → ONE P5 round for both. A second omission
    # check would show here. (P4 stays per story: it is a sync author call,
    # seconds each. P5 was per story until the round collapse of 2026-09-19 —
    # 49 rounds at ~1.2 min were 1h18 of a 3h41 chunk.)
    assert [phase for _role, phase in recorder.calls] == [
        "P1", "P2", "P3", "P3", "P1", "P2", "P3", "P4", "P4", "P5"
    ]
    omission_checks = [cids for phase, cids, _ in recorder.batches if phase == "P3"
                       and judge_claims.omissions_custom_id(unit) in cids]
    assert len(omission_checks) == 1
    assert [e.message for e in snap.events if e.kind == "info"].count("omissions_found") == 1
    p1_batches = [(cids, prompts) for phase, cids, prompts in recorder.batches if phase == "P1"]
    assert [cids for cids, _ in p1_batches] == [[unit.custom_id(1)], [unit.custom_id(2)]]
    supplement = p1_batches[1][1][0]
    assert script_mod.TICKET["text"] in supplement
    assert all(text in supplement for text in first)  # shown, so not repeated
    assert "Write claims ONLY for" in supplement
    p3_judged = [cids for phase, cids, _ in recorder.batches if phase == "P3"]
    judged_ids = [cid for batch in p3_judged for cid in batch]
    assert judged_ids.count(judge_claims.judge_custom_id(unit, "c01", 1)) == 1  # never re-judged
    assert judge_claims.judge_custom_id(unit, "c04", 1) in judged_ids
    assert store.phase_output(job.id, "P3")["reasked_units"] == [unit.key]

    records = json.loads((data_root / script_mod.CITY / "beats.json").read_text(encoding="utf-8"))
    assert [r["beat_id"] for r in records] == [script_mod.BEAT_ID, script_mod.TICKET_BEAT_ID]
    assert [c["text"] for c in records[0]["claims"]] == first
    assert [(c["claim_id"], c["text"]) for c in records[1]["claims"]] == [
        ("c04", script_mod.TICKET["text"])
    ]
    assert model.validate(records, chunks_root=chunks.parent) == []


def test_an_omission_reask_that_fails_keeps_the_first_pass(tmp_path):
    """Owner ruling A's other half: before slice 10 an omission re-ask whose
    answer could not be read dropped the WHOLE unit (`continue`), so one
    unreadable supplement threw away every good claim of the first pass. It
    now costs only the omitted fact: the first pass's story is committed."""
    chunks = script_mod.chunk_dir(tmp_path)
    data_root = script_mod.data_dir(tmp_path)
    store = jobs.IngestJobStore()
    job = _book_job(store, chunks)
    unit = script_mod.unit(chunks)
    _events, sink = _sink_and_events()
    scripted = script_mod.script(unit)
    scripted["batch_answers"].update(
        script_mod.omissions_answer(
            unit, [{"fact": script_mod.TICKET["text"], "span": script_mod.TICKET["span"]}]
        )
    )
    scripted["batch_answers"][unit.custom_id(2)] = llm.MockAnswer(
        text="not json", model_id=script_mod.RESPONSE_AUTHOR_MODEL
    )

    run.run_job(job.id, store, llm.MockClient(sink, **scripted), data_root=data_root)

    snap = store.snapshot(job.id)
    assert snap.status == "committed", snap.error
    records = json.loads((data_root / script_mod.CITY / "beats.json").read_text(encoding="utf-8"))
    assert [r["beat_id"] for r in records] == [script_mod.BEAT_ID]
    assert [c["text"] for c in records[0]["claims"]] == [c["text"] for c in script_mod.CLAIMS]


def _claim_verdict(claim_id: str, verdict: str, existing_claim_id: str = "",
                   new_value: str = "", existing_value: str = "",
                   reason: str = "the two state the same fact") -> dict:
    return {
        "claim_id": claim_id, "verdict": verdict, "existing_claim_id": existing_claim_id,
        "new_value": new_value, "existing_value": existing_value, "reason": reason,
    }


def test_a_held_merge_is_a_queue_item_and_nothing_of_it_reaches_disk(tmp_path):
    """D13: a merge the judge and the signature hint disagree on holds the
    new story as a review-queue item — bound to the hash of what the
    reviewer is shown, ranked by the content held back — and applies
    nothing: the city's file is byte-identical afterwards, and the job
    still completes (a held item is never a failed job)."""
    chunks = script_mod.chunk_dir(tmp_path)
    data_root = script_mod.data_dir(tmp_path)
    beats_path = script_mod.seed_beats(data_root, script_mod.existing_records())
    before = beats_path.read_bytes()
    store = jobs.IngestJobStore()
    job = _book_job(store, chunks)
    unit = script_mod.unit(chunks)
    _events, sink = _sink_and_events()
    scripted = script_mod.script(unit)
    # The hint matches the story to the origins beat (two of three claims
    # are already there); a judge calling it new disagrees.
    scripted["answers"]["merge_judge"] = [
        script_mod.merge_answer(
            "new", "", [_claim_verdict(c, "new", reason="nothing like it") for c in
                        ("c01", "c02", "c03")]
        )
    ]
    client = llm.MockClient(sink, **scripted)

    run.run_job(job.id, store, client, data_root=data_root)

    snap = store.snapshot(job.id)
    assert snap.status == "committed", snap.error
    assert ("merge_judge", "P6") in client.calls
    held = [e.data for e in snap.events if e.message == "beat_held"]
    assert [h["phase"] for h in held] == ["P6"]
    assert beats_path.read_bytes() == before
    assert "commit_skipped" in [e.message for e in snap.events if e.kind == "info"]

    items = store.undecided(script_mod.CITY)
    assert [(i.kind, i.story_slug, i.place) for i in items] == [
        ("merge_held", script_mod.STORY_SLUG, script_mod.PLACE)
    ]
    item = items[0]
    assert item.job_id == job.id
    assert item.item_id == jobs.shown_hash(item.shown)
    assert item.shown["claims"] == [c["text"] for c in script_mod.CLAIMS]
    kept = [judge_claims.JudgedClaim.model_validate(c) for c in item.shown["judged_claims"]]
    assert [c.draft.source.span for c in kept] == [c["span"] for c in script_mod.CLAIMS]
    assert item.shown["narration"] == script_mod.NARRATION
    assert item.shown["judge_story"] == "new"
    assert "disagree" in item.reason
    assert item.held_back == {
        "claims": 3, "duration_sec": narrate.duration_sec(script_mod.NARRATION)
    }
    assert item.decision is None


def test_ids_in_rerun_get_p4_and_p5_again_before_p7(tmp_path):
    """§3 P6: any claim change → P4/P5 rerun for that beat. The judge and
    the hint agree the story is the origins beat's: two claims are the
    same (a second source appended), the 1939 claim is new and joins the
    beat — so its resolved texts changed and P6 names it for a rerun. The
    runner narrates the beat again from its resolved claims, judges every
    sentence, and only then commits: the file holds the same two beats,
    the origins beat carries three claims and the new narration, and its
    claims_hash is fresh (the validator is clean). No new record; the
    book log records the chunk touching the place."""
    chunks = script_mod.chunk_dir(tmp_path)
    data_root = script_mod.data_dir(tmp_path)
    beats_path = script_mod.seed_beats(data_root, script_mod.existing_records())
    store = jobs.IngestJobStore()
    job = _book_job(store, chunks)
    unit = script_mod.unit(chunks)
    _events, sink = _sink_and_events()
    scripted = script_mod.script(unit)
    scripted["answers"]["author"].append(script_mod.narration_answer(script_mod.RERUN_NARRATION))
    scripted["answers"]["merge_judge"] = [
        script_mod.merge_answer(
            "same",
            "b1",
            [
                _claim_verdict("c01", "same", "b1.c01", "his sixties", "his sixties"),
                _claim_verdict("c02", "new", reason="no claim mentions 1939"),
                _claim_verdict("c03", "same", "b1.c02", "1959", "1959"),
            ],
        )
    ]
    merged_texts = [
        script_mod.COLLECTING["text"], script_mod.COMPLETED["text"], script_mod.OPENED_1939["text"]
    ]
    scripted["batch_answers"].update(
        script_mod.sentence_verdicts(merged_texts, script_mod.RERUN_NARRATION)
    )
    recorder = _RecordingClient(llm.MockClient(sink, **scripted))

    run.run_job(job.id, store, recorder, data_root=data_root)

    snap = store.snapshot(job.id)
    assert snap.status == "committed", snap.error
    assert recorder.calls[-3:] == [
        ("merge_judge", "P6"), ("author", "P4"), ("narration_judge", "P5")
    ]
    rerun = [e.data for e in snap.events if e.message == "rerun"]
    assert rerun == [{"beat_ids": [script_mod.ORIGINS_ID]}]
    assert store.undecided(script_mod.CITY) == []

    records = json.loads(beats_path.read_text(encoding="utf-8"))
    assert [r["beat_id"] for r in records] == [script_mod.ORIGINS_ID, script_mod.VISITING_ID]
    origins = records[0]
    assert [(c["claim_id"], c["text"]) for c in origins["claims"]] == [
        ("c01", script_mod.COLLECTING["text"]),
        ("c02", script_mod.COMPLETED["text"]),
        ("c03", script_mod.OPENED_1939["text"]),
    ]
    assert len(origins["claims"][0]["sources"]) == 2
    assert origins["narration"]["text"] == script_mod.RERUN_NARRATION
    assert origins["narration"]["claims_hash"] == model.claims_hash(origins["claims"])
    assert origins["narration"]["verdict"]["sentences_total"] == 3
    assert origins["duration_sec"] == narrate.duration_sec(script_mod.RERUN_NARRATION)
    assert origins["review"] == {"held": False, "reason": None}
    assert records[1] == script_mod.existing_records()[1]
    assert model.validate(records, chunks_root=chunks.parent) == []

    log = json.loads((data_root / script_mod.CITY / "book-log.json").read_text(encoding="utf-8"))
    assert log["books_processed"][-1]["chunks_processed"] == [
        {"chunk": script_mod.LP_CHUNK, "beats_extracted": 0, "pois_touched": [script_mod.PLACE]}
    ]


def _unreadable(model_id: str) -> llm.MockAnswer:
    return llm.MockAnswer(text="not json", model_id=model_id)


_LIFTED = (
    "Solomon Guggenheim bought abstract art at the behest of his art adviser, "
    "an eccentric German baroness named Hilla Rebay."
)
_LIFTED_AGAIN = (
    "A mining magnate began acquiring abstract art in his 60s at the behest of "
    "his adviser, Hilla Rebay."
)


def _inject_one_item_failure(case: str, unit, scripted: dict, data_root: Path) -> str:
    """Break exactly one item of the scripted job in `case`'s phase; return
    the phase whose hold the job must log."""
    author, judge = script_mod.RESPONSE_AUTHOR_MODEL, script_mod.RESPONSE_JUDGE_MODEL
    batch, answers = scripted["batch_answers"], scripted["answers"]
    texts = [c["text"] for c in script_mod.CLAIMS]
    if case == "P1":
        batch[unit.custom_id(1)] = _unreadable(author)
        batch[unit.custom_id(2)] = _unreadable(author)
        return "P1"
    if case == "P2":
        answers["author"] = [_unreadable(author), _unreadable(author)]
        return "P2"
    if case == "P3":
        batch[judge_claims.judge_custom_id(unit, "c01", 1)] = _unreadable(judge)
        return "P3"
    if case == "P4":
        answers["author"] = [
            answers["author"][0],
            script_mod.narration_answer(_LIFTED),
            script_mod.narration_answer(_LIFTED_AGAIN),
        ]
        return "P4"
    if case == "P5":
        first = next(iter(script_mod.sentence_verdicts(texts)))
        batch[first] = _unreadable(judge)
        return "P5"
    script_mod.seed_beats(data_root, script_mod.existing_records())
    if case == "P6-merge":
        # Invalid under the slice-10 contract: a claim handle the place lacks.
        invalid = script_mod.merge_answer(
            "same", "b1",
            [
                _claim_verdict("c01", "conflict", "b9.c01", "his sixties", "his fifties"),
                _claim_verdict("c02", "new", reason="no claim mentions 1939"),
                _claim_verdict("c03", "same", "b1.c02", "1959", "1959"),
            ],
        )
        answers["merge_judge"] = [invalid, invalid]
        return "P6"
    # P6-rerun(-P4): the merge joins the origins beat, and its re-narration
    # is unreadable to the sentence judge (or copies the source twice).
    if case == "P6-rerun-P4":
        answers["author"].extend(
            [script_mod.narration_answer(_LIFTED), script_mod.narration_answer(_LIFTED_AGAIN)]
        )
    else:
        answers["author"].append(script_mod.narration_answer(script_mod.RERUN_NARRATION))
    answers["merge_judge"] = [
        script_mod.merge_answer(
            "same", "b1",
            [
                _claim_verdict("c01", "same", "b1.c01", "his sixties", "his sixties"),
                _claim_verdict("c02", "new", reason="no claim mentions 1939"),
                _claim_verdict("c03", "same", "b1.c02", "1959", "1959"),
            ],
        )
    ]
    merged = [
        script_mod.COLLECTING["text"], script_mod.COMPLETED["text"], script_mod.OPENED_1939["text"]
    ]
    rerun_verdicts = script_mod.sentence_verdicts(merged, script_mod.RERUN_NARRATION)
    batch.update(rerun_verdicts)
    if case == "P6-rerun-P4":
        return "P4"
    batch[next(iter(rerun_verdicts))] = _unreadable(judge)
    return "P5"


@pytest.mark.parametrize(
    "case", ["P1", "P2", "P3", "P4", "P5", "P6-merge", "P6-rerun", "P6-rerun-P4"]
)
def test_one_failing_item_never_ends_the_job(tmp_path, case):
    """Slice 9 job 2 attempt 3 (2026-09-15) ran ten hours and died at P6: one
    story's merge answer broke the contract twice, `merge.hold` raised, and
    the runner caught holds in P1-P4 but not in P5 or P6 — so one bad item
    ended the job and, with no resume, lost everything. In every phase, one
    item that cannot be completed (an unreadable answer, a narration refused
    twice, an invalid merge answer, a re-narration the judge cannot answer)
    is held and logged for THAT phase, and the job still reaches P7. From P4
    on the held story is a review-queue item, so it reaches the sidecar."""
    chunks = script_mod.chunk_dir(tmp_path)
    data_root = script_mod.data_dir(tmp_path)
    store = jobs.IngestJobStore()
    job = _book_job(store, chunks)
    unit = script_mod.unit(chunks)
    _events, sink = _sink_and_events()
    scripted = script_mod.script(unit)
    phase = _inject_one_item_failure(case, unit, scripted, data_root)

    run.run_job(job.id, store, llm.MockClient(sink, **scripted), data_root=data_root)

    snap = store.snapshot(job.id)
    assert snap.status == "committed", snap.error
    assert [e.message for e in snap.events if e.kind == "phase"][-1] == "P7"
    held = [
        e.data for e in snap.events
        if e.message in ("beat_held", "unit_held") and e.data.get("phase") == phase
    ]
    assert held, [e.message for e in snap.events]
    if case == "P6-merge":
        assert held[0]["reason"].startswith("answer: claim c01: existing_claim_id 'b9.c01'")
    if case not in ("P1", "P2", "P3"):
        assert script_mod.STORY_SLUG in [i.story_slug for i in store.undecided(script_mod.CITY)]
    if case.startswith("P6-rerun"):
        # The hold came from P6's re-narration, not first-pass P4/P5, and the
        # merged beat that could not be re-narrated stays exactly as it was.
        assert "rerun" in [e.message for e in snap.events]
        records = json.loads(
            (data_root / script_mod.CITY / "beats.json").read_text(encoding="utf-8")
        )
        assert records == script_mod.existing_records()


def test_a_reverted_merge_is_queued_as_the_incoming_story_and_counts_as_nothing(tmp_path):
    """Judge, checkpoint 14: when a merged beat's re-narration is held the beat
    stays as it was on disk — but the review item named the EXISTING beat's
    story (built from the beat), not the book's story whose merge was
    dropped, and `applied` / the book log's pois_touched still counted the
    merge. Here the incoming story's slug differs from the origins beat's:
    the item is the incoming story, carrying the claims the merge judged;
    nothing counts as applied or touched; the unchanged file is not
    rewritten."""
    chunks = script_mod.chunk_dir(tmp_path)
    data_root = script_mod.data_dir(tmp_path)
    beats_path = script_mod.seed_beats(data_root, script_mod.existing_records())
    before = beats_path.read_bytes()
    store = jobs.IngestJobStore()
    job = _book_job(store, chunks)
    unit = script_mod.unit(chunks)
    _events, sink = _sink_and_events()
    scripted = script_mod.script(unit)
    incoming_title = "Rebay's museum before the spiral"
    incoming_slug = model.slug(incoming_title)
    assert incoming_slug != model.slug(script_mod.STORY_TITLE)
    scripted["answers"]["author"] = [
        script_mod._author(
            {
                "stories": [
                    {
                        "title": incoming_title,
                        "place": script_mod.PLACE,
                        "beat_type": "anecdote",
                        "lenses": ["hidden_history", "visual_art"],
                        "claim_ids": ["c01", "c02", "c03"],
                        "enrichment": script_mod.ENRICHMENT,
                    }
                ]
            }
        ),
        script_mod.narration_answer(),
        script_mod.narration_answer(script_mod.RERUN_NARRATION),
    ]
    scripted["answers"]["merge_judge"] = [
        script_mod.merge_answer(
            "same", "b1",
            [
                _claim_verdict("c01", "same", "b1.c01", "his sixties", "his sixties"),
                _claim_verdict("c02", "new", reason="no claim mentions 1939"),
                _claim_verdict("c03", "same", "b1.c02", "1959", "1959"),
            ],
        )
    ]
    merged = [
        script_mod.COLLECTING["text"], script_mod.COMPLETED["text"], script_mod.OPENED_1939["text"]
    ]
    rerun_verdicts = script_mod.sentence_verdicts(merged, script_mod.RERUN_NARRATION)
    scripted["batch_answers"].update(rerun_verdicts)
    scripted["batch_answers"][next(iter(rerun_verdicts))] = _unreadable(
        script_mod.RESPONSE_JUDGE_MODEL
    )

    run.run_job(job.id, store, llm.MockClient(sink, **scripted), data_root=data_root)

    snap = store.snapshot(job.id)
    assert snap.status == "committed", snap.error
    assert [e.data for e in snap.events if e.message == "rerun_reverted"] == [
        {"beat_ids": [script_mod.ORIGINS_ID]}
    ]
    items = store.undecided(script_mod.CITY)
    assert [(i.kind, i.story_slug) for i in items] == [("merge_held", incoming_slug)]
    kept = [judge_claims.JudgedClaim.model_validate(c) for c in items[0].shown["judged_claims"]]
    assert sorted(c.draft.text for c in kept) == sorted(c["text"] for c in script_mod.CLAIMS)
    p6 = store.phase_output(job.id, "P6")
    assert p6["applied"] == []
    assert p6["changed"] is False
    assert p6["per_unit"][unit.key]["pois_touched"] == []
    assert beats_path.read_bytes() == before
    assert "commit_skipped" in [e.message for e in snap.events]


def test_a_new_storys_beat_holds_only_its_unmatched_claims_renarrated(tmp_path):
    """Slice 10: a claim of a NEW story that the judge matches in an
    existing beat folds into that beat (merge.apply), so the new story's own
    beat must not voice it a second time. The runner assembles the new beat
    from its unmatched claims only and narrates it again (P4/P5) over them,
    so its claims_hash is fresh; the sidebar that took the folded claim
    carries both sources. Nothing is queued."""
    chunks = script_mod.chunk_dir(tmp_path)
    data_root = script_mod.data_dir(tmp_path)
    beats_path = script_mod.seed_beats(data_root, script_mod.folding_records())
    store = jobs.IngestJobStore()
    job = _book_job(store, chunks)
    unit = script_mod.unit(chunks)
    _events, sink = _sink_and_events()
    scripted = script_mod.script(unit)
    scripted["answers"]["author"].append(
        script_mod.narration_answer(script_mod.REMAINDER_NARRATION)
    )
    scripted["answers"]["merge_judge"] = [
        script_mod.merge_answer(
            "new", "",
            [
                _claim_verdict("c01", "new", reason="no claim mentions collecting"),
                _claim_verdict("c02", "new", reason="no claim mentions 1939"),
                _claim_verdict("c03", "same", "b2.c01", "1959", "1959"),
            ],
        )
    ]
    remainder = [script_mod.COLLECTING["text"], script_mod.OPENED_1939["text"]]
    scripted["batch_answers"].update(
        script_mod.sentence_verdicts(remainder, script_mod.REMAINDER_NARRATION)
    )
    recorder = _RecordingClient(llm.MockClient(sink, **scripted))

    run.run_job(job.id, store, recorder, data_root=data_root)

    snap = store.snapshot(job.id)
    assert snap.status == "committed", snap.error
    assert recorder.calls[-3:] == [
        ("merge_judge", "P6"), ("author", "P4"), ("narration_judge", "P5")
    ]
    assert store.undecided(script_mod.CITY) == []
    records = json.loads(beats_path.read_text(encoding="utf-8"))
    assert [r["beat_id"] for r in records] == [
        script_mod.VISITING_ID, script_mod.COMPLETION_ID, script_mod.BEAT_ID
    ]
    assert records[0] == script_mod.folding_records()[0]
    completion = records[1]["claims"][0]
    assert [s["span"] for s in completion["sources"]] == [script_mod.COMPLETED["span"]] * 2
    new = records[2]
    assert [(c["claim_id"], c["text"]) for c in new["claims"]] == [
        ("c01", script_mod.COLLECTING["text"]), ("c02", script_mod.OPENED_1939["text"])
    ]
    assert new["narration"]["text"] == script_mod.REMAINDER_NARRATION
    assert new["narration"]["claims_hash"] == model.claims_hash(new["claims"])
    assert new["duration_sec"] == narrate.duration_sec(script_mod.REMAINDER_NARRATION)
    assert model.validate(records, chunks_root=chunks.parent) == []


def test_a_new_story_too_small_after_folding_is_queued_and_a_fully_folded_one_vanishes(
    tmp_path,
):
    """A new story whose matched claims fold away can be left below its
    beat type's arc minimum (an anecdote needs two claims): writing it
    would make P7's validator refuse the WHOLE file, so the remainder is a
    merge queue item carrying its unmatched judged claims, while the folds
    still land. A story whose every claim folds writes no beat and queues
    nothing — all of it is already told — and is logged `merge_absorbed`."""
    def run_with(verdicts: list[dict]):
        root = tmp_path / str(len(list(tmp_path.iterdir())))
        root.mkdir()
        chunks = script_mod.chunk_dir(root)
        data_root = script_mod.data_dir(root)
        beats_path = script_mod.seed_beats(data_root, script_mod.existing_records())
        store = jobs.IngestJobStore()
        job = _book_job(store, chunks)
        _events, sink = _sink_and_events()
        scripted = script_mod.script(script_mod.unit(chunks))
        scripted["answers"]["merge_judge"] = [script_mod.merge_answer("new", "", verdicts)]
        client = llm.MockClient(sink, **scripted)
        run.run_job(job.id, store, client, data_root=data_root)
        records = json.loads(beats_path.read_text(encoding="utf-8"))
        assert model.validate(records, chunks_root=chunks.parent) == []
        return store.snapshot(job.id), store, records, client

    folded_collecting = _claim_verdict("c01", "same", "b1.c01", "his sixties", "his sixties")
    folded_completed = _claim_verdict("c03", "same", "b1.c02", "1959", "1959")
    snap, store, records, client = run_with(
        [folded_collecting, _claim_verdict("c02", "new", reason="no claim mentions 1939"),
         folded_completed]
    )
    assert snap.status == "committed", snap.error
    assert client.calls[-1] == ("merge_judge", "P6")  # nothing re-narrated
    assert [r["beat_id"] for r in records] == [script_mod.ORIGINS_ID, script_mod.VISITING_ID]
    assert [len(c["sources"]) for c in records[0]["claims"]] == [2, 2]
    items = store.undecided(script_mod.CITY)
    assert [(i.kind, i.story_slug) for i in items] == [("merge_held", script_mod.STORY_SLUG)]
    assert items[0].shown["claims"] == [script_mod.OPENED_1939["text"]]
    assert "arc" in items[0].reason

    snap, store, records, client = run_with(
        [folded_collecting,
         _claim_verdict("c02", "same", "b1.c02", "1939", "1939"),
         folded_completed]
    )
    assert snap.status == "committed", snap.error
    assert [r["beat_id"] for r in records] == [script_mod.ORIGINS_ID, script_mod.VISITING_ID]
    assert store.undecided(script_mod.CITY) == []
    absorbed = [e.data for e in snap.events if e.message == "merge_absorbed"]
    assert absorbed == [{"story_slug": script_mod.STORY_SLUG, "place": script_mod.PLACE}]


def test_a_fold_dropped_by_a_held_rerun_is_queued_with_the_incoming_claim(tmp_path):
    """The 9568574 path, now for folds out of a NEW story: its completion
    claim conflicts with the sidebar's (event vs event → contested), so the
    sidebar must be narrated again; that re-narration copies the source
    twice and is held at P4, so the sidebar is kept exactly as it was on
    disk. The folded claim must not vanish: the incoming story is queued as
    a merge item carrying its judged completion claim, while its own beat
    (the two unmatched claims, re-narrated) is still written."""
    chunks = script_mod.chunk_dir(tmp_path)
    data_root = script_mod.data_dir(tmp_path)
    beats_path = script_mod.seed_beats(data_root, script_mod.folding_records())
    store = jobs.IngestJobStore()
    job = _book_job(store, chunks)
    unit = script_mod.unit(chunks)
    _events, sink = _sink_and_events()
    scripted = script_mod.script(unit)
    lifted = [
        "When the Guggenheim opened its doors in October 1959, the ticket price was 50\u00a2.",
        "The ticket price was 50\u00a2 when the Guggenheim opened its doors in October 1959.",
    ]
    scripted["answers"]["author"].extend(
        [script_mod.narration_answer(script_mod.REMAINDER_NARRATION)]
        + [script_mod.narration_answer(text) for text in lifted]
    )
    scripted["answers"]["merge_judge"] = [
        script_mod.merge_answer(
            "new", "",
            [
                _claim_verdict("c01", "new", reason="no claim mentions collecting"),
                _claim_verdict("c02", "new", reason="no claim mentions 1939"),
                _claim_verdict("c03", "conflict", "b2.c01", "1959", "1960"),
            ],
        )
    ]
    remainder = [script_mod.COLLECTING["text"], script_mod.OPENED_1939["text"]]
    scripted["batch_answers"].update(
        script_mod.sentence_verdicts(remainder, script_mod.REMAINDER_NARRATION)
    )

    run.run_job(job.id, store, llm.MockClient(sink, **scripted), data_root=data_root)

    snap = store.snapshot(job.id)
    assert snap.status == "committed", snap.error
    assert [e.data for e in snap.events if e.message == "rerun_reverted"] == [
        {"beat_ids": [script_mod.COMPLETION_ID]}
    ]
    records = json.loads(beats_path.read_text(encoding="utf-8"))
    assert records[:2] == script_mod.folding_records()
    assert [c["claim_id"] for c in records[2]["claims"]] == ["c01", "c02"]
    items = store.undecided(script_mod.CITY)
    assert [(i.kind, i.story_slug) for i in items] == [("merge_held", script_mod.STORY_SLUG)]
    assert "dropped" in items[0].reason
    queued = [judge_claims.JudgedClaim.model_validate(c) for c in items[0].shown["judged_claims"]]
    assert script_mod.COMPLETED["text"] in [c.draft.text for c in queued]
    assert model.validate(records, chunks_root=chunks.parent) == []


def test_a_new_place_holds_its_beat_and_queues_it(tmp_path):
    """D13: a story at a place the city's POI file does not name is held —
    its record commits with `review.held` and the reason naming the
    place, so a reviewer can see it and publish refuses it — and becomes
    a `new_place` queue item. No merge call: there is no beat at a place
    the corpus has never seen."""
    chunks = script_mod.chunk_dir(tmp_path)
    data_root = script_mod.data_dir(tmp_path)
    store = jobs.IngestJobStore()
    job = _book_job(store, chunks)
    unit = script_mod.unit(chunks)
    _events, sink = _sink_and_events()
    client = llm.MockClient(sink, **script_mod.script(unit, place="Guggenheim Museum"))

    run.run_job(job.id, store, client, data_root=data_root)

    snap = store.snapshot(job.id)
    assert snap.status == "committed", snap.error
    assert ("merge_judge", "P6") not in client.calls
    assert "place_unresolved" in [e.message for e in snap.events if e.kind == "info"]

    records = json.loads((data_root / script_mod.CITY / "beats.json").read_text(encoding="utf-8"))
    assert [r["beat_id"] for r in records] == [
        f"{script_mod.CITY}/guggenheim-museum/{script_mod.STORY_SLUG}"
    ]
    assert records[0]["poi_name"] == "Guggenheim Museum"
    assert records[0]["review"] == {"held": True, "reason": "new_poi: Guggenheim Museum"}
    assert model.validate(records, chunks_root=chunks.parent) == []

    items = store.undecided(script_mod.CITY)
    assert [(i.kind, i.place, i.reason) for i in items] == [
        ("new_place", "Guggenheim Museum", "new_poi: Guggenheim Museum")
    ]
    assert items[0].shown["beat"]["beat_id"] == records[0]["beat_id"]
    assert items[0].held_back["claims"] == 3


def test_a_narration_still_refused_after_the_revise_is_held_and_queued(tmp_path):
    """D13: a narration flagged after the second ask holds the beat. The
    judge refuses the 1939 sentence, the author revises once, the judge
    refuses it again: the record commits held with the `not_entailed`
    flag, and the queue holds a `narration_held` item showing the
    narration the reviewer decides on."""
    chunks = script_mod.chunk_dir(tmp_path)
    data_root = script_mod.data_dir(tmp_path)
    store = jobs.IngestJobStore()
    job = _book_job(store, chunks)
    unit = script_mod.unit(chunks)
    _events, sink = _sink_and_events()
    texts = [c["text"] for c in script_mod.CLAIMS]
    revised = script_mod.NARRATION.replace("ran a temporary museum", "opened a temporary museum")
    scripted = script_mod.script(unit)
    scripted["answers"]["author"].append(script_mod.narration_answer(revised))
    scripted["batch_answers"].update(
        script_mod.sentence_verdicts(texts, refused={1: "no claim says she ran it"})
    )
    scripted["batch_answers"].update(
        script_mod.sentence_verdicts(
            texts, revised, attempt=2, refused={1: "no claim says she opened it"}
        )
    )
    client = llm.MockClient(sink, **scripted)

    run.run_job(job.id, store, client, data_root=data_root)

    snap = store.snapshot(job.id)
    assert snap.status == "committed", snap.error
    held = [e.data for e in snap.events if e.message == "beat_held"]
    assert [h["phase"] for h in held] == ["P5"]

    records = json.loads((data_root / script_mod.CITY / "beats.json").read_text(encoding="utf-8"))
    assert [r["beat_id"] for r in records] == [script_mod.BEAT_ID]
    beat = records[0]
    assert beat["narration"]["text"] == revised
    assert beat["narration"]["verdict"]["sentences_entailed"] == 2
    assert [f.split(":")[0] for f in beat["narration"]["flags"]] == ["not_entailed"]
    assert beat["review"]["held"] is True
    assert model.validate(records, chunks_root=chunks.parent) == []

    items = store.undecided(script_mod.CITY)
    assert [(i.kind, i.story_slug) for i in items] == [("narration_held", script_mod.STORY_SLUG)]
    assert items[0].shown["narration"] == revised
    assert items[0].shown["phase"] == "P5"
    assert items[0].held_back == {"claims": 3, "duration_sec": narrate.duration_sec(revised)}


def test_a_story_held_at_p4_keeps_its_judged_claims_in_the_queue_item(tmp_path):
    """Slice 9 job 1 run 4 (2026-09-14): four stories — the Guggenheim origin
    story among them — failed the narration lift gate twice, were held at P4,
    and vanished: the queue item kept only the claim TEXTS, and nothing else
    of the story's judged claims survived the process. A held narration's
    item now carries every judged claim whole (text, span, verdict), so the
    story can be re-narrated later without paying for P1-P3 again."""
    chunks = script_mod.chunk_dir(tmp_path)
    data_root = script_mod.data_dir(tmp_path)
    store = jobs.IngestJobStore()
    job = _book_job(store, chunks)
    unit = script_mod.unit(chunks)
    _events, sink = _sink_and_events()
    lifted = (
        "Solomon Guggenheim bought abstract art at the behest of his art adviser, "
        "an eccentric German baroness named Hilla Rebay."
    )
    lifted_again = (
        "A mining magnate began acquiring abstract art in his 60s at the behest of "
        "his adviser, Hilla Rebay."
    )
    scripted = script_mod.script(unit)
    scripted["answers"]["author"] = [
        scripted["answers"]["author"][0],
        script_mod.narration_answer(lifted),
        script_mod.narration_answer(lifted_again),
    ]
    client = llm.MockClient(sink, **scripted)

    run.run_job(job.id, store, client, data_root=data_root)

    snap = store.snapshot(job.id)
    assert snap.status == "committed", snap.error
    held = [e.data for e in snap.events if e.message == "beat_held"]
    assert [h["phase"] for h in held] == ["P4"]
    items = store.undecided(script_mod.CITY)
    assert [(i.kind, i.story_slug) for i in items] == [("narration_held", script_mod.STORY_SLUG)]
    kept = [judge_claims.JudgedClaim.model_validate(c) for c in items[0].shown["judged_claims"]]
    assert [c.draft.text for c in kept] == [c["text"] for c in script_mod.CLAIMS]
    assert [c.draft.source.span for c in kept] == [c["span"] for c in script_mod.CLAIMS]
    assert all(c.verdict.entailed for c in kept)


def test_p0_refuses_a_legacy_file_and_a_url_source_before_any_call(tmp_path):
    """P0's gates stop a job before a token is counted: a city whose
    beats file is still legacy-shape cannot take a new-shape record (the
    validator would refuse the mixed file at P7, so nothing is spent
    getting there), and a url source has no reader yet. Both end the job
    in `error` with the reason, no estimate printed, no call made."""
    chunks = script_mod.chunk_dir(tmp_path)
    data_root = script_mod.data_dir(tmp_path)
    legacy = [{"beat_id": "new_york/x/y", "script_body": "old", "poi_name": "X"}]
    script_mod.seed_beats(data_root, legacy)
    store = jobs.IngestJobStore()
    unit = script_mod.unit(chunks)
    _events, sink = _sink_and_events()

    job = _book_job(store, chunks)
    client = llm.MockClient(sink, **script_mod.script(unit))
    run.run_job(job.id, store, client, data_root=data_root)
    snap = store.snapshot(job.id)
    assert snap.status == "error"
    assert "legacy-shape" in (snap.error or "")
    assert client.calls == [] and client.count_calls == []
    assert [e.kind for e in snap.events] == ["error"]

    url_job = store.create(
        city=script_mod.CITY, source={"kind": "url", "url": "https://en.wikipedia.org/wiki/X"},
        as_of="2026-09-01", rights_basis="cc_by_sa",
    )
    client = llm.MockClient(sink, **script_mod.script(unit))
    run.run_job(url_job.id, store, client, data_root=data_root)
    snap = store.snapshot(url_job.id)
    assert snap.status == "error"
    assert "url sources are not ingested yet" in (snap.error or "")
    assert client.calls == [] and client.count_calls == []


def test_a_book_source_may_name_the_chunks_it_ingests(tmp_path):
    """Slice 9 runs ONE chunk of a fifteen-chunk book. A book source may
    carry `chunks`, a subset of the manifest's filenames (stems accepted);
    P0 then intakes only those, in manifest order. A name the manifest
    does not carry refuses the job before any call — a typo must never
    quietly ingest the whole book. Without `chunks` every manifest chunk
    is a unit, as before."""
    chunks = script_mod.chunk_dir(tmp_path)
    manifest_path = chunks / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    (chunks / "chunk-08-upper-west-side.txt").write_text(
        "Central Park\n\nThe park opened in 1858.\n", encoding="utf-8"
    )
    manifest["chunks"].append(
        {"chunk_number": 8, "filename": "chunk-08-upper-west-side.txt", "section_title": "UWS"}
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    store = jobs.IngestJobStore()

    whole_book = _book_job(store, chunks)
    units, _manifest = run.intake(whole_book)
    assert [u.chunk for u in units] == [script_mod.LP_CHUNK, "chunk-08-upper-west-side"]

    one_chunk = store.create(
        city=script_mod.CITY,
        source={"kind": "book", "chunk_dir": str(chunks), "chunks": [script_mod.LP_CHUNK]},
        as_of=2023,
        rights_basis="owned_copy",
    )
    units, _manifest = run.intake(one_chunk)
    assert [u.chunk for u in units] == [script_mod.LP_CHUNK]

    typo = store.create(
        city=script_mod.CITY,
        source={"kind": "book", "chunk_dir": str(chunks), "chunks": ["chunk-07-upper-east"]},
        as_of=2023,
        rights_basis="owned_copy",
    )
    _events, sink = _sink_and_events()
    client = llm.MockClient(sink, **script_mod.script(script_mod.unit(chunks)))
    run.run_job(typo.id, store, client, data_root=script_mod.data_dir(tmp_path))
    snap = store.snapshot(typo.id)
    assert snap.status == "error"
    assert "chunk-07-upper-east" in (snap.error or "") and "manifest" in (snap.error or "")
    assert client.calls == [] and client.count_calls == []


def test_the_estimate_prices_the_per_claim_phases_by_fan_out(tmp_path):
    """The judge on the first paid job (2026-09-12): the phases' plans are
    priced PER CLAIM (P3), PER STORY (P4, P6) and PER SENTENCE (P5), but the
    runner priced every row as ONE call per unit, so the printed "ceiling"
    was a floor — and slice 9's bar is "cost within 25% of the estimate".
    `estimate_job` applies a fan-out per unit from its own sentence count
    and the ratios MEASURED on the first real job (0.63 claims per source
    sentence, one story per four claims, 1.3 narration sentences per claim).
    Each P3 call carries the whole passage, so the per-call input stays the
    unit's text plus the prompt. The event payload names the fan-out so
    the run can be measured against it."""
    chunks = script_mod.chunk_dir(tmp_path)
    unit = script_mod.unit(chunks)
    eight = " ".join(f"Sentence number {i} states one fact about the place." for i in range(8))
    unit = unit.model_copy(update={"text": eight})
    _events, sink = _sink_and_events()
    client = llm.MockClient(sink, count_tokens_fn=lambda _m, text: len(text.split()))

    estimate = run.estimate_job(client, [unit])

    calls = {}
    for row in estimate.rows:
        calls.setdefault((row.phase, row.role), []).append(row.calls)
    assert calls[("P1", "author")][0] == 1  # decompose: once per unit
    assert calls[("P2", "author")][0] == 1  # group: once per unit
    assert calls[("P3", "claim_judge")][0] == 5  # one judge call per claim: round(8 x 0.63)
    assert calls[("P4", "author")][0] == 1  # one narration per story (5 // 4)
    # one judge call per narration sentence: round(5 x 1.3) = 6 (6.5 rounds to even)
    assert calls[("P5", "narration_judge")][0] == 6
    assert calls[("P6", "merge_judge")][0] == 1  # one merge per story
    p3 = next(r for r in estimate.rows if r.phase == "P3" and r.role == "claim_judge")
    words = len(eight.split())
    assert p3.input_tokens >= 5 * words  # every P3 call carries the passage
    assert run.fanout(unit) == {"claims": 5, "stories": 1, "sentences": 6}


def test_the_cap_bound_is_the_true_upper_limit_and_the_projection_sits_below_it(tmp_path):
    """The judge on job 1's re-run: once plan rows price EXPECTED output,
    the printed total is a projection, not a bound — a run can
    legitimately bill past it. `cap_bound_usd` prices every row at the
    max_tokens its call actually asks for (the same input, the same
    prices, the same batch discount): the true upper limit. It sits
    above the projection by exactly the output headroom, and collapses
    onto the projection when every expectation equals its cap."""
    chunks = script_mod.chunk_dir(tmp_path)
    unit = script_mod.unit(chunks)
    _events, sink = _sink_and_events()
    client = llm.MockClient(sink, count_tokens_fn=lambda _m, text: len(text.split()))

    estimate = run.estimate_job(client, [unit])
    bound = run.cap_bound_usd(estimate)

    assert bound > estimate.total_usd
    assert len(run.CAPS) == len(estimate.rows)
    assert all(
        cap * row.calls >= row.output_tokens
        for cap, row in zip(run.CAPS, estimate.rows, strict=True)
    )
    # Every row at its cap: an estimate built from the caps themselves.
    at_cap = llm.CostEstimate(
        units=estimate.units,
        rows=[
            llm.PhaseCost(
                phase=r.phase, role=r.role, model_id=r.model_id, batch=r.batch, calls=r.calls,
                input_tokens=r.input_tokens, output_tokens=r.calls * cap, usd=0.0,
            )
            for r, cap in zip(estimate.rows, run.CAPS, strict=True)
        ],
        total_input_tokens=estimate.total_input_tokens,
        total_output_tokens=0, total_usd=0.0, prices_cached_on=estimate.prices_cached_on,
    )
    assert run.cap_bound_usd(at_cap) == pytest.approx(bound)
    # And a projection priced at the caps IS the bound (the two formulas agree).
    assert run.projection_usd(at_cap) == pytest.approx(bound)


def test_only_the_passage_carrying_phases_are_priced_over_the_unit_text(tmp_path):
    """Measured on slice 9's first real job: a P5 request carried 418 input
    tokens, a P3 request 6,410 — because only the P1 and P3 prompts embed
    the passage; P2, P4, P5 and P6 see claim texts. Pricing every row over
    the unit text overstated the projection several times over. Rows are
    priced on their INPUT BASIS: `unit` rows over the unit's real token
    count; `claims_story` rows (P4, P5, P6) over a story's claims,
    CLAIMS_PER_STORY x CLAIM_TOKENS; the `claims_unit` rows (P2) over every
    claim of the unit, fan-out claims x CLAIM_TOKENS — measured constants,
    plus each row's prompt overhead as before."""
    chunks = script_mod.chunk_dir(tmp_path)
    unit = script_mod.unit(chunks)
    eight = " ".join(f"Sentence number {i} states one fact about the place." for i in range(8))
    unit = unit.model_copy(update={"text": eight})
    _events, sink = _sink_and_events()
    client = llm.MockClient(sink, count_tokens_fn=lambda _m, text: len(text.split()))

    estimate = run.estimate_job(client, [unit])

    words = len(eight.split())
    by_index = list(zip(estimate.rows, run.PLAN, run.INPUT_BASIS, strict=True))
    p1 = next(
        (row, call) for row, call, basis in by_index if row.phase == "P1" and basis == "unit"
    )
    assert p1[0].input_tokens == words + p1[1].overhead_tokens
    p5 = next((row, call) for row, call, basis in by_index if row.phase == "P5")
    per_call = run.CLAIMS_PER_STORY * run.CLAIM_TOKENS + p5[1].overhead_tokens
    assert p5[0].input_tokens == 6 * per_call
    p2 = next((row, call) for row, call, basis in by_index if row.phase == "P2")
    assert p2[0].input_tokens == 5 * run.CLAIM_TOKENS + p2[1].overhead_tokens
    assert {b for b in run.INPUT_BASIS} == {"unit", "claims_unit", "claims_story"}
    assert run.CLAIM_TOKENS == 70 and run.CLAIMS_PER_STORY == 3  # measured 2026-09-12


def test_expected_prices_reask_rows_at_the_measured_rates(tmp_path):
    """The first pass prices no re-ask; the projection prices every item as
    refused once. Neither is what a run costs. `expected_usd` weights each
    plan row by the rate its re-ask actually fired on the first real job
    (REASK_RATES, aligned with PLAN): first asks at 1.0; the omission
    re-ask and the P2 redo at 1.0 (they fired on the only unit); the P3
    restate and re-judge at 0.09 (9 of about 100 claims refused once); the
    P5 revise and re-judge at 0.07 (5 of 71 sentences); the P4 redo at 0.0
    (no narration failed its gate); the P6 re-ask at 0.1, a stated
    projection until a merge runs. So first-pass <= expected <= projection,
    and the P3 restate row contributes exactly 0.09 of its price."""
    chunks = script_mod.chunk_dir(tmp_path)
    unit = script_mod.unit(chunks)
    _events, sink = _sink_and_events()
    client = llm.MockClient(sink, count_tokens_fn=lambda _m, text: len(text.split()))

    estimate = run.estimate_job(client, [unit])

    first_pass = sum(
        r.usd for r, first in zip(estimate.rows, run.FIRST_PASS, strict=True) if first
    )
    expected = run.expected_usd(estimate)
    assert first_pass < expected < estimate.total_usd
    assert len(run.REASK_RATES) == len(estimate.rows)
    assert all(
        rate == 1.0
        for rate, first in zip(run.REASK_RATES, run.FIRST_PASS, strict=True)
        if first
    )
    restate_index = next(
        i
        for i, (call, first) in enumerate(zip(run.PLAN, run.FIRST_PASS, strict=True))
        if call.phase == "P1" and call.role == "author" and not first and i > 3
    )
    assert run.REASK_RATES[restate_index] == 0.09
    weighted = sum(
        r.usd * rate for r, rate in zip(estimate.rows, run.REASK_RATES, strict=True)
    )
    assert expected == pytest.approx(weighted)



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


def test_a_unit_with_two_stories_judges_them_in_one_p3_and_one_p5_round(tmp_path):
    """Throughput (2026-09-19): `_judge_stories` judged story by story, so a
    43-story chunk bought 52 P3 batch rounds at a median 1.2 minutes each —
    2h12 of a 3h41 run (job 32d5c8de…). The rounds carry the whole unit now:
    two stories, ONE P3 round holding both stories' claim ids.
    """
    chunks = script_mod.chunk_dir(tmp_path)
    data_root = script_mod.data_dir(tmp_path)
    store = jobs.IngestJobStore()
    job = _book_job(store, chunks)
    unit = script_mod.unit(chunks)
    two_stories = script_mod._author(
        {
            "stories": [
                {
                    "title": script_mod.STORY_TITLE,
                    "place": script_mod.PLACE,
                    "beat_type": "anecdote",
                    "lenses": ["hidden_history", "visual_art"],
                    "claim_ids": ["c01", "c02"],
                    "enrichment": script_mod.ENRICHMENT,
                },
                {
                    "title": "The doors open",
                    "place": script_mod.PLACE,
                    "beat_type": "sidebar",
                    "lenses": ["hidden_history"],
                    "claim_ids": ["c03"],
                    "enrichment": script_mod.ENRICHMENT,
                },
            ]
        }
    )
    first_two = [script_mod.CLAIMS[0]["text"], script_mod.CLAIMS[1]["text"]]
    third = [script_mod.CLAIMS[2]["text"]]
    scripted = {
        "answers": {
            "author": [
                two_stories,
                script_mod.narration_answer(),
                script_mod.narration_answer(),
            ]
        },
        "batch_answers": {
            unit.custom_id(1): script_mod.claims_answer(),
            **script_mod.verdicts(unit, ["c01", "c02", "c03"]),
            **script_mod.omissions_answer(unit),
            **script_mod.sentence_verdicts(first_two),
            **script_mod.sentence_verdicts(third),
        },
    }
    _events, sink = _sink_and_events()
    recorder = _RecordingClient(llm.MockClient(sink, **scripted))

    run.run_job(job.id, store, recorder, data_root=data_root)

    p3_rounds = [ids for phase, ids, _prompts in recorder.batches if phase == "P3"]
    judging = [ids for ids in p3_rounds if all(cid.endswith(("-j1", "-j2")) for cid in ids)]
    assert len(judging) == 1, p3_rounds
    assert len(judging[0]) == 3, judging
    # P5 the same: one round for both stories' sentences, not one per story.
    p5_rounds = [ids for phase, ids, _prompts in recorder.batches if phase == "P5"]
    assert len(p5_rounds) == 1, p5_rounds
    assert len(p5_rounds[0]) == len(
        judge_narration.sentences(script_mod.NARRATION)
    ) * 2, p5_rounds


def test_two_units_get_their_own_rounds_never_one_shared_round(tmp_path):
    """The judge's gap on the round collapse: P5 was flattened across UNITS,
    so a 386-chunk job would have submitted every chunk's sentences in one
    batch and one failed round would have held every narration in the job.
    The rounds are per unit now, and a second chunk whose text matches the
    first proves it deterministically: `sentence_custom_id` is content-derived
    (`n{claims_hash}-s{i}-j{a}`, no unit key), so two units that narrate the
    same claims produce the SAME sentence ids — a shared round would raise the
    Batch API's duplicate-custom_id refusal, while per-unit rounds are legal.
    """
    chunks = script_mod.chunk_dir(tmp_path)
    second = "chunk-08-upper-west-side-central-park"
    shutil.copyfile(chunks / f"{script_mod.LP_CHUNK}.txt", chunks / f"{second}.txt")
    manifest = json.loads((chunks / "manifest.json").read_text())
    manifest["chunks"].append(
        {"chunk_number": 8, "filename": f"{second}.txt", "section_title": "Upper West Side"}
    )
    (chunks / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    data_root = script_mod.data_dir(tmp_path)
    store = jobs.IngestJobStore()
    job = store.create(
        city=script_mod.CITY,
        source={"kind": "book", "chunk_dir": str(chunks), "chunks": None},
        as_of=2022,
        rights_basis="owned_copy",
    )
    unit_a = script_mod.unit(chunks, as_of=2022)
    unit_b = unit_mod.load_unit(
        chunks.parent, city=script_mod.CITY, source_id=script_mod.LP_SOURCE, chunk=second,
        as_of=2022, rights_basis="owned_copy",
    )
    claim_ids = [f"c{i:02d}" for i in range(1, len(script_mod.CLAIMS) + 1)]
    scripted = {
        "answers": {
            # P2 runs for BOTH units before P4 does, so the author's answers
            # are drawn stories, stories, narration, narration.
            "author": [
                script_mod.stories_answer(), script_mod.stories_answer(),
                script_mod.narration_answer(), script_mod.narration_answer(),
            ]
        },
        "batch_answers": {
            unit_a.custom_id(1): script_mod.claims_answer(),
            unit_b.custom_id(1): script_mod.claims_answer(),
            **script_mod.verdicts(unit_a, claim_ids),
            **script_mod.verdicts(unit_b, claim_ids),
            **script_mod.omissions_answer(unit_a),
            **script_mod.omissions_answer(unit_b),
            **script_mod.sentence_verdicts([c["text"] for c in script_mod.CLAIMS]),
        },
    }
    _events, sink = _sink_and_events()
    recorder = _RecordingClient(llm.MockClient(sink, **scripted))

    run.run_job(job.id, store, recorder, data_root=data_root)

    snap = store.snapshot(job.id)
    assert snap.status == "committed", snap.error
    # Two units, so two of every per-unit round — never one shared round.
    p3_judging = [
        ids for phase, ids, _p in recorder.batches
        if phase == "P3" and all(cid.endswith(("-j1", "-j2")) for cid in ids)
    ]
    p5_rounds = [ids for phase, ids, _p in recorder.batches if phase == "P5"]
    assert len(p3_judging) == 2, p3_judging
    assert len(p5_rounds) == 2, p5_rounds
    # Each P3 round carries ONE unit's claims: its ids share one unit prefix.
    for ids in p3_judging:
        prefixes = {cid.rsplit("-c", 1)[0] for cid in ids}
        assert len(prefixes) == 1, ids
    # The identical sentence ids across the two units are the discriminator.
    assert p5_rounds[0] == p5_rounds[1], (p5_rounds[0], p5_rounds[1])
