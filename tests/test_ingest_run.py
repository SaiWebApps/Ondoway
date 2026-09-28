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

from src.ingest import (
    decompose,
    group,
    jobs,
    judge_claims,
    judge_narration,
    llm,
    merge,
    model,
    narrate,
    run,
)
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
        {"chunk": script_mod.LP_CHUNK, "beats_extracted": 1, "pois_touched": [script_mod.PLACE],
         "covered": []}
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
        "new_value": new_value, "existing_value": existing_value, "judgments": [], "reason": reason,
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


def test_a_held_merge_shows_the_reviewer_every_claim_the_judge_named(tmp_path):
    """A held merge used to show only the reason: nothing said which claims
    the judge named or what it quoted, so five correct covered folds
    (2026-09-21) sat in a queue with nothing to review. The item now
    carries the judge's answer per claim — verdict, every named claim WITH
    its text, the values and the reason. Here c01 matches the origins
    beat's c01 and the signature agrees; c03 is called new although the
    signature matches the completion claim, which holds the story."""
    chunks = script_mod.chunk_dir(tmp_path)
    data_root = script_mod.data_dir(tmp_path)
    script_mod.seed_beats(data_root, script_mod.existing_records())
    store = jobs.IngestJobStore()
    job = _book_job(store, chunks)
    _events, sink = _sink_and_events()
    scripted = script_mod.script(script_mod.unit(chunks))
    scripted["answers"]["merge_judge"] = [
        script_mod.merge_answer(
            "new", "",
            [
                _claim_verdict("c01", "same", "b1.c01", "his sixties", "his sixties",
                               reason="both say he began collecting in his sixties"),
                _claim_verdict("c02", "new", reason="no claim mentions 1939"),
                _claim_verdict("c03", "new", reason="nothing like it"),
            ],
        )
    ]

    run.run_job(job.id, store, llm.MockClient(sink, **scripted), data_root=data_root)

    [item] = store.undecided(script_mod.CITY)
    assert item.kind == "merge_held"
    assert item.shown["judge_claims"] == [
        {
            "claim_id": "c01", "verdict": "same",
            "named": [{"beat_id": script_mod.ORIGINS_ID, "claim_id": "c01",
                       "text": script_mod.COLLECTING["text"]}],
            "new_value": "his sixties", "existing_value": "his sixties", "judgments": [],
            "reason": "both say he began collecting in his sixties",
        },
        {"claim_id": "c02", "verdict": "new", "named": [], "new_value": "",
         "existing_value": "", "judgments": [], "reason": "no claim mentions 1939"},
        {"claim_id": "c03", "verdict": "new", "named": [], "new_value": "",
         "existing_value": "", "judgments": [], "reason": "nothing like it"},
    ]


def test_a_covered_claim_is_recorded_in_p6_and_the_book_log(tmp_path):
    """An absorbed claim was recorded only in the job's event stream
    (2026-09-21), which a reader of the city's book log never sees. Here the
    completion claim is covered by the sidebar's two claims together; P6 and
    the book log's chunk entry name it and what covers it. The story keeps
    its other two claims, so the absorption stands (below the arc minimum it
    would be refused), and its beat is narrated over what it kept."""
    chunks = script_mod.chunk_dir(tmp_path)
    data_root = script_mod.data_dir(tmp_path)
    beats_path = script_mod.seed_beats(data_root, script_mod.folding_records())
    store = jobs.IngestJobStore()
    job = _book_job(store, chunks)
    _events, sink = _sink_and_events()
    scripted = script_mod.script(script_mod.unit(chunks))
    scripted["answers"]["author"].append(
        script_mod.narration_answer(script_mod.REMAINDER_NARRATION)
    )
    remainder = [script_mod.COLLECTING["text"], script_mod.OPENED_1939["text"]]
    scripted["batch_answers"].update(
        script_mod.sentence_verdicts(remainder, script_mod.REMAINDER_NARRATION)
    )
    scripted["answers"]["merge_judge"] = [
        script_mod.merge_answer(
            "new", "",
            [
                _claim_verdict("c01", "new", reason="no claim mentions collecting"),
                _claim_verdict("c02", "new", reason="no claim mentions 1939"),
                _claim_verdict("c03", "covered", "b2.c01+b2.c02"),
            ],
        )
    ]

    run.run_job(job.id, store, llm.MockClient(sink, **scripted), data_root=data_root)

    snap = store.snapshot(job.id)
    assert snap.status == "committed", snap.error
    covered = [
        {
            "story_slug": script_mod.STORY_SLUG,
            "claim_id": "c03",
            "text": script_mod.COMPLETED["text"],
            "covered_by": [[script_mod.COMPLETION_ID, "c01"], [script_mod.COMPLETION_ID, "c02"]],
        }
    ]
    assert store.phase_output(job.id, "P6")["covered"] == covered
    log = json.loads((data_root / script_mod.CITY / "book-log.json").read_text(encoding="utf-8"))
    [entry] = log["books_processed"][-1]["chunks_processed"]
    assert entry["covered"] == covered
    records = json.loads(beats_path.read_text(encoding="utf-8"))
    own = records[2]
    assert [c["text"] for c in own["claims"]] == remainder  # the absorbed claim is not written
    assert records[:2] == script_mod.folding_records()  # nothing changed in the sidebar

def test_a_covered_fold_is_refused_when_it_would_orphan_the_remainder(tmp_path):
    """Owner ruling 2026-09-22: absorbing a claim writes it nowhere, so a
    story whose remainder would fall below its beat type's arc minimum
    keeps its covered claims instead — the story stays whole and nothing is
    lost. Here two of three claims come back `covered`, leaving one claim
    (the 1939 opening), which alone would be queued and never written. The
    absorption is refused for this story: its beat carries all three
    claims, nothing is queued, and P6 records no covered claim (none was
    absorbed). The cost is a claim the corpus already states, visible in
    the beat, against a fact lost to a queue nobody applies."""
    chunks = script_mod.chunk_dir(tmp_path)
    data_root = script_mod.data_dir(tmp_path)
    beats_path = script_mod.seed_beats(data_root, script_mod.existing_records())
    store = jobs.IngestJobStore()
    job = _book_job(store, chunks)
    _events, sink = _sink_and_events()
    scripted = script_mod.script(script_mod.unit(chunks))
    title = "Rebay's museum before the spiral"  # its own slug, not the origins beat's
    scripted["answers"]["author"][0] = script_mod._author(
        {"stories": [{"title": title, "place": script_mod.PLACE, "beat_type": "anecdote",
                      "lenses": ["hidden_history", "visual_art"],
                      "claim_ids": ["c01", "c02", "c03"],
                      "enrichment": script_mod.ENRICHMENT}]}
    )
    scripted["answers"]["merge_judge"] = [
        script_mod.merge_answer(
            "new", "",
            [
                _claim_verdict("c01", "covered", "b1.c01+b1.c02"),
                _claim_verdict("c02", "new", reason="no claim mentions 1939"),
                _claim_verdict("c03", "covered", "b1.c01+b1.c02"),
            ],
        )
    ]

    run.run_job(job.id, store, llm.MockClient(sink, **scripted), data_root=data_root)

    snap = store.snapshot(job.id)
    assert snap.status == "committed", snap.error
    records = json.loads(beats_path.read_text(encoding="utf-8"))
    own_id = f"{script_mod.CITY}/{model.slug(script_mod.PLACE)}/{model.slug(title)}"
    assert [r["beat_id"] for r in records] == [
        script_mod.ORIGINS_ID, script_mod.VISITING_ID, own_id
    ]
    own = records[2]
    assert [c["text"] for c in own["claims"]] == [c["text"] for c in script_mod.CLAIMS]
    assert store.undecided(script_mod.CITY) == []
    assert store.phase_output(job.id, "P6")["covered"] == []
    assert [e.data for e in snap.events if e.message == "merge_covered_refused"] == [
        {"story_slug": model.slug(title), "place": script_mod.PLACE,
         "claim_ids": ["c01", "c03"], "kept": 1, "beat_type": "anecdote"}
    ]
    assert own["narration"]["text"] == script_mod.NARRATION  # not narrated again
    assert model.validate(records, chunks_root=chunks.parent) == []

def test_ids_in_rerun_get_p4_and_p5_again_before_p7(tmp_path):
    """§3 P6: a fold that changes a beat's resolved texts → P4/P5 rerun for
    that beat before P7. The incoming completion claim conflicts with the
    sidebar's (event vs event → contested, both values kept), so the
    sidebar must be narrated again; the story's two unmatched claims become
    its own beat, narrated over them alone (owner ruling 2026-09-22: the
    story never joins an existing beat, so a rerun now comes from the fold,
    not from a claim moving). Both beats' claims_hash are fresh and the
    validator is clean."""
    chunks = script_mod.chunk_dir(tmp_path)
    data_root = script_mod.data_dir(tmp_path)
    beats_path = script_mod.seed_beats(data_root, script_mod.folding_records())
    store = jobs.IngestJobStore()
    job = _book_job(store, chunks)
    unit = script_mod.unit(chunks)
    _events, sink = _sink_and_events()
    scripted = script_mod.script(unit)
    # A contested claim is not `resolved`, so the rerun narrates the
    # sidebar's other claim alone (judged_of_beat, run.py).
    contested_narration = "A ticket cost fifty cents when the doors opened in October 1959."
    scripted["answers"]["author"].extend([
        script_mod.narration_answer(script_mod.REMAINDER_NARRATION),
        script_mod.narration_answer(contested_narration),
    ])
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
    scripted["batch_answers"].update(
        script_mod.sentence_verdicts([script_mod.TICKET["text"]], contested_narration)
    )
    recorder = _RecordingClient(llm.MockClient(sink, **scripted))

    run.run_job(job.id, store, recorder, data_root=data_root)

    snap = store.snapshot(job.id)
    assert snap.status == "committed", snap.error
    assert [e.data for e in snap.events if e.message == "rerun"] == [
        {"beat_ids": [script_mod.COMPLETION_ID]}
    ]
    # P4/P5 ran three times: the story's first narration, its remainder
    # after the fold, and the sidebar's rerun.
    assert [c for c in recorder.calls if c[1] == "P4"].count(("author", "P4")) == 3
    assert store.undecided(script_mod.CITY) == []

    records = json.loads(beats_path.read_text(encoding="utf-8"))
    assert [r["beat_id"] for r in records] == [
        script_mod.VISITING_ID, script_mod.COMPLETION_ID, script_mod.BEAT_ID
    ]
    completion = records[1]
    contested = completion["claims"][0]
    assert contested["status"] == "contested"
    assert [s["stated_value"] for s in contested["sources"]] == ["1960", "1959"]
    assert completion["narration"]["text"] == contested_narration
    assert completion["narration"]["claims_hash"] == model.claims_hash(completion["claims"])
    own = records[2]
    assert [c["claim_id"] for c in own["claims"]] == ["c01", "c02"]
    assert own["narration"]["text"] == script_mod.REMAINDER_NARRATION
    assert model.validate(records, chunks_root=chunks.parent) == []

    log = json.loads((data_root / script_mod.CITY / "book-log.json").read_text(encoding="utf-8"))
    assert log["books_processed"][-1]["chunks_processed"] == [
        {"chunk": script_mod.LP_CHUNK, "beats_extracted": 1, "pois_touched": [script_mod.PLACE],
         "covered": []}
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
    # P6-rerun(-P4): the incoming completion claim CONFLICTS with the
    # sidebar's, so that beat is re-narrated (a story never joins an
    # existing beat, owner ruling 2026-09-22) — and its re-narration is
    # unreadable to the sentence judge (or copies the source twice).
    script_mod.seed_beats(data_root, script_mod.folding_records())
    answers["merge_judge"] = [
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
    answers["author"].append(script_mod.narration_answer(script_mod.REMAINDER_NARRATION))
    batch.update(script_mod.sentence_verdicts(remainder, script_mod.REMAINDER_NARRATION))
    # The contested claim is no longer `resolved`, so the rerun narrates the
    # sidebar's ticket claim alone.
    if case == "P6-rerun-P4":
        # Both attempts copy a run of the TICKET claim's own span.
        answers["author"].extend([
            script_mod.narration_answer(
                "Its doors in October 1959, the ticket price was 50\u00a2, the sidebar notes."
            ),
            script_mod.narration_answer(
                "When it opened its doors in October 1959, the ticket price was 50\u00a2."
            ),
        ])
        return "P4"
    answers["author"].append(script_mod.narration_answer(script_mod.TICKET_SENTENCE))
    rerun_verdicts = script_mod.sentence_verdicts(
        [script_mod.TICKET["text"]], script_mod.TICKET_SENTENCE
    )
    batch.update(rerun_verdicts)
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
        # The beat that could not be re-narrated is kept exactly as it was;
        # the story's own beat (its unmatched claims) is still written.
        assert records[:2] == script_mod.folding_records()
        assert [c["claim_id"] for c in records[2]["claims"]] == ["c01", "c02"]


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
            "new", "",
            [
                # A conflict re-narrates the origins beat (a story never
                # joins one, owner ruling 2026-09-22), and that re-narration
                # is what the sentence judge cannot answer below.
                _claim_verdict("c01", "conflict", "b1.c01", "his fifties", "his sixties"),
                _claim_verdict("c02", "new", reason="no claim mentions 1939"),
                _claim_verdict("c03", "same", "b1.c02", "1959", "1959"),
            ],
        )
    ]
    # The contested claim is no longer `resolved`: the rerun narrates the
    # origins beat's completion claim alone.
    rerun_verdicts = script_mod.sentence_verdicts(
        [script_mod.COMPLETED["text"]], script_mod.RERUN_NARRATION
    )
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
    # ONE item for the story (owner ruling 2026-09-22), carrying BOTH
    # reasons — its remainder is one claim, below the anecdote's arc
    # minimum, AND its folds were dropped when the re-narration was held —
    # and a per-claim disposition, so whatever applies this later writes
    # only what the corpus is missing. Two items with overlapping claims
    # would have written facts the corpus already holds.
    assert [(i.kind, i.story_slug) for i in items] == [("merge_held", incoming_slug)]
    [item] = items
    assert "arc" in item.reason and "dropped" in item.reason
    kept = [judge_claims.JudgedClaim.model_validate(c) for c in item.shown["judged_claims"]]
    assert sorted(c.draft.text for c in kept) == sorted(c["text"] for c in script_mod.CLAIMS)
    assert item.shown["claim_disposition"] == [
        # its fold into the origins beat was reverted with the beat
        {"claim_id": "c01", "status": "dropped", "beat_id": script_mod.ORIGINS_ID,
         "existing_claim_id": "c01", "applied_claim_id": "c01", "covered_by": []},
        # never written: the story's own beat was never assembled
        {"claim_id": "c02", "status": "unmatched", "beat_id": None, "existing_claim_id": None,
         "applied_claim_id": "c02", "covered_by": []},
        {"claim_id": "c03", "status": "dropped", "beat_id": script_mod.ORIGINS_ID,
         "existing_claim_id": "c02", "applied_claim_id": "c02", "covered_by": []},
    ]
    p6 = store.phase_output(job.id, "P6")
    assert p6["applied"] == []
    assert p6["changed"] is False
    assert p6["per_unit"][unit.key]["pois_touched"] == []
    assert p6["per_unit"][unit.key]["covered"] == []
    assert p6["covered"] == []
    assert beats_path.read_bytes() == before
    assert "commit_skipped" in [e.message for e in snap.events]




def test_a_story_whose_every_claim_is_absorbed_stays_absorbed(tmp_path):
    """The refusal exists to save a story's REMAINDER, so a story with no
    remainder at all is not refused into existence: every claim is stated
    by the corpus already, nothing is lost, and the story vanishes as
    `merge_absorbed` — no beat, no queue item. (Judge, 2026-09-22: written
    as `kept + absorbed >= the minimum` the refusal would fire here and
    write back claims the corpus already holds.)"""
    chunks = script_mod.chunk_dir(tmp_path)
    data_root = script_mod.data_dir(tmp_path)
    fifty_fourth = script_mod._beat(
        f"{script_mod.CITY}/{model.slug(script_mod.PLACE)}/the-54th-street-museum",
        "the-54th-street-museum", "The 54th Street museum", "sidebar",
        [script_mod._lp_claim("c01", script_mod.OPENED_1939)],
        script_mod.OPENED_1939["text"],
    )
    beats_path = script_mod.seed_beats(
        data_root, [*script_mod.existing_records(), fifty_fourth]
    )
    before = beats_path.read_bytes()
    store = jobs.IngestJobStore()
    job = _book_job(store, chunks)
    _events, sink = _sink_and_events()
    scripted = script_mod.script(script_mod.unit(chunks))
    scripted["answers"]["merge_judge"] = [
        script_mod.merge_answer(
            "new", "",
            [
                _claim_verdict("c01", "covered", "b1.c01+b1.c02"),
                _claim_verdict("c02", "covered", "b3.c01+b1.c01"),
                _claim_verdict("c03", "covered", "b1.c01+b1.c02"),
            ],
        )
    ]

    run.run_job(job.id, store, llm.MockClient(sink, **scripted), data_root=data_root)

    snap = store.snapshot(job.id)
    assert snap.status == "committed", snap.error
    assert [e.data for e in snap.events if e.message == "merge_absorbed"] == [
        {"story_slug": script_mod.STORY_SLUG, "place": script_mod.PLACE}
    ]
    assert [e.message for e in snap.events if e.message == "merge_covered_refused"] == []
    assert beats_path.read_bytes() == before
    assert store.undecided(script_mod.CITY) == []

def test_a_covered_claim_of_a_reverted_merge_is_not_recorded(tmp_path):
    """The other half of the record: a merge dropped because its
    re-narration was held absorbed nothing, so P6 must not report its
    covered claim. The story is a `sidebar` (a structural beat type, no arc
    minimum), so one kept claim is legal and the absorption is not refused;
    its completion claim conflicts with the origins beat's, that beat's
    rerun is unreadable to the sentence judge, and the merge is reverted."""
    chunks = script_mod.chunk_dir(tmp_path)
    data_root = script_mod.data_dir(tmp_path)
    script_mod.seed_beats(data_root, script_mod.existing_records())
    store = jobs.IngestJobStore()
    job = _book_job(store, chunks)
    _events, sink = _sink_and_events()
    scripted = script_mod.script(script_mod.unit(chunks))
    title = "Rebay's museum before the spiral"
    scripted["answers"]["author"][0] = script_mod._author(
        {"stories": [{"title": title, "place": script_mod.PLACE, "beat_type": "sidebar",
                      "lenses": ["hidden_history", "visual_art"],
                      "claim_ids": ["c01", "c02", "c03"],
                      "enrichment": script_mod.ENRICHMENT}]}
    )
    one_claim = (
        "In 1939 Hilla Rebay ran a temporary museum for him on 54th Street, named for "
        "non-objective painting."
    )
    scripted["answers"]["author"].extend([
        script_mod.narration_answer(one_claim),
        script_mod.narration_answer(script_mod.RERUN_NARRATION),
    ])
    scripted["batch_answers"].update(
        script_mod.sentence_verdicts([script_mod.OPENED_1939["text"]], one_claim)
    )
    rerun_verdicts = script_mod.sentence_verdicts(
        [script_mod.COMPLETED["text"]], script_mod.RERUN_NARRATION
    )
    scripted["batch_answers"].update(rerun_verdicts)
    scripted["batch_answers"][next(iter(rerun_verdicts))] = _unreadable(
        script_mod.RESPONSE_JUDGE_MODEL
    )
    scripted["answers"]["merge_judge"] = [
        script_mod.merge_answer(
            "new", "",
            [
                _claim_verdict("c01", "conflict", "b1.c01", "his fifties", "his sixties"),
                _claim_verdict("c02", "new", reason="no claim mentions 1939"),
                _claim_verdict("c03", "covered", "b1.c01+b1.c02"),
            ],
        )
    ]

    run.run_job(job.id, store, llm.MockClient(sink, **scripted), data_root=data_root)

    snap = store.snapshot(job.id)
    assert snap.status == "committed", snap.error
    assert [e.data for e in snap.events if e.message == "rerun_reverted"] == [
        {"beat_ids": [script_mod.ORIGINS_ID]}
    ]
    p6 = store.phase_output(job.id, "P6")
    assert p6["applied"] == [] and p6["covered"] == []
    assert p6["per_unit"][script_mod.unit(chunks).key]["covered"] == []
    assert [i.kind for i in store.undecided(script_mod.CITY)] == ["merge_held"]


def test_a_story_whose_beat_id_already_exists_is_queued_never_dropped(tmp_path):
    """A story whose slug already names a beat at its place cannot be
    written — beat ids are city/place/slug. It used to be dropped with a
    `beat_id_collision` event and NO review item, so its judged, narrated
    claims reached neither disk nor queue; the 2026-09-22 ruling (a story
    never joins an existing beat) made that the default path for every
    re-ingest producing the same slug. It is now a review item like every
    other thing P6 cannot apply. Here the place already holds a beat with
    the incoming story's slug (a prior job's) whose claims are unrelated,
    so nothing folds."""
    chunks = script_mod.chunk_dir(tmp_path)
    data_root = script_mod.data_dir(tmp_path)
    twin = script_mod._beat(
        f"{script_mod.CITY}/{model.slug(script_mod.PLACE)}/{script_mod.STORY_SLUG}",
        script_mod.STORY_SLUG, script_mod.STORY_TITLE, "anecdote",
        [script_mod._lp_claim("c01", script_mod.ADDRESS),
         script_mod._lp_claim("c02", script_mod.HOURS_2023)],
        script_mod.VISITING_NARRATION,
    )
    beats_path = script_mod.seed_beats(data_root, [twin])
    before = beats_path.read_bytes()
    store = jobs.IngestJobStore()
    job = _book_job(store, chunks)
    _events, sink = _sink_and_events()
    scripted = script_mod.script(script_mod.unit(chunks))
    scripted["answers"]["merge_judge"] = [
        script_mod.merge_answer(
            "new", "",
            [_claim_verdict(c, "new", reason="nothing like it") for c in ("c01", "c02", "c03")],
        )
    ]

    run.run_job(job.id, store, llm.MockClient(sink, **scripted), data_root=data_root)

    snap = store.snapshot(job.id)
    assert snap.status == "committed", snap.error
    assert [e.data for e in snap.events if e.message == "beat_id_collision"] == [
        {"story_slug": script_mod.STORY_SLUG, "beat_id": twin["beat_id"]}
    ]
    assert beats_path.read_bytes() == before  # nothing written
    [item] = store.undecided(script_mod.CITY)
    assert (item.kind, item.story_slug) == ("merge_held", script_mod.STORY_SLUG)
    assert "already holds" in item.reason and twin["beat_id"] in item.reason
    kept = [judge_claims.JudgedClaim.model_validate(c) for c in item.shown["judged_claims"]]
    assert sorted(c.draft.text for c in kept) == sorted(c["text"] for c in script_mod.CLAIMS)

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
    # The item carries the WHOLE story with a per-claim disposition (owner
    # ruling 2026-09-22), so an applier writes only the claim the corpus is
    # missing — the two that folded are already in it.
    assert items[0].shown["claims"] == [c["text"] for c in script_mod.CLAIMS]
    assert [(r["claim_id"], r["status"]) for r in items[0].shown["claim_disposition"]] == [
        ("c01", "written"), ("c02", "unmatched"), ("c03", "written")
    ]
    assert items[0].held_back["claims"] == 1
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


def _finished_job_then_p7_with(tmp_path, extra_records: list[dict], on_disk=()):
    """A committed mock job's P0-P6 outputs copied onto a second job, with
    `extra_records` appended to P6's records, then run_job again: every
    phase is already done, so only P7 runs — the shape of `--resume` on
    job B (75b8f292…), whose P6 records carried three beats P7 refused."""
    first_root = tmp_path / "first"
    first_root.mkdir()
    chunks = script_mod.chunk_dir(first_root)
    store = jobs.IngestJobStore()
    first = _book_job(store, chunks)
    _events, sink = _sink_and_events()
    client = llm.MockClient(sink, **script_mod.script(script_mod.unit(chunks)))
    run.run_job(first.id, store, client, data_root=script_mod.data_dir(first_root))
    assert store.snapshot(first.id).status == "committed"

    data_root = script_mod.data_dir(tmp_path)
    if on_disk:
        script_mod.seed_beats(data_root, list(on_disk))
    second = _book_job(store, chunks)
    for phase in ("P0", "P1", "P2", "P3", "P4", "P5"):
        store.set_phase(second.id, phase, store.phase_output(first.id, phase))
    p6 = dict(store.phase_output(first.id, "P6"))
    p6["records"] = [*p6["records"], *extra_records]
    store.set_phase(second.id, "P6", p6)
    run.run_job(second.id, store, llm.MockClient(sink), data_root=data_root)
    beats_path = data_root / script_mod.CITY / "beats.json"
    written = json.loads(beats_path.read_text(encoding="utf-8")) if beats_path.is_file() else None
    return store, store.snapshot(second.id), written, chunks


def test_a_new_beat_below_its_arc_minimum_at_p7_is_held_and_the_rest_commits(tmp_path):
    """Job B's P7 refused the WHOLE file for one anecdote left with a single
    claim after P3 dropped its partner — and nine merge folds were lost with
    it. Owner ruling 2026-09-21: a new beat failing ARC_TOO_FEW_CLAIMS is held
    to the review queue (kind commit_held, carrying the record and the
    validator's error) and every other beat commits, validated."""
    lonely_id = f"{script_mod.CITY}/{model.slug(script_mod.PLACE)}/a-lonely-anecdote"
    lonely = script_mod._beat(
        lonely_id, "a-lonely-anecdote", "A lonely anecdote", "anecdote",
        [script_mod._lp_claim("c01", script_mod.COLLECTING)],
        "Solomon Guggenheim started buying abstract art late in life on the advice "
        "of Hilla Rebay.",
    )

    store, snap, written, chunks = _finished_job_then_p7_with(tmp_path, [lonely])

    assert snap.status == "committed", snap.error
    assert [r["beat_id"] for r in written] == [script_mod.BEAT_ID]
    assert model.validate(written, chunks_root=chunks.parent) == []
    items = store.undecided(script_mod.CITY)
    assert [(i.kind, i.story_slug) for i in items] == [("commit_held", "a-lonely-anecdote")]
    assert items[0].reason.startswith("ARC_TOO_FEW_CLAIMS")
    assert items[0].shown["beat"]["beat_id"] == lonely_id


def test_a_new_beat_whose_narration_lifts_its_span_at_p7_is_held_and_the_rest_commits(
    tmp_path,
):
    """Job B's third refusal: a narration sharing an 8-word run with its span
    (a proper name P4's lift gate exempts and P7's check does not). Held the
    same way, kind commit_held, the rest committed."""
    lifted_id = f"{script_mod.CITY}/{model.slug(script_mod.PLACE)}/a-lifted-narration"
    lifted = script_mod._beat(
        lifted_id, "a-lifted-narration", "A lifted narration", "anecdote",
        [script_mod._lp_claim("c01", script_mod.COLLECTING),
         script_mod._lp_claim("c02", script_mod.OPENED_1939)],
        script_mod.OPENED_1939["span"],
    )

    store, snap, written, chunks = _finished_job_then_p7_with(tmp_path, [lifted])

    assert snap.status == "committed", snap.error
    assert [r["beat_id"] for r in written] == [script_mod.BEAT_ID]
    assert model.validate(written, chunks_root=chunks.parent) == []
    items = store.undecided(script_mod.CITY)
    assert [(i.kind, i.story_slug) for i in items] == [("commit_held", "a-lifted-narration")]
    assert items[0].reason.startswith("NARRATION_LIFT")


def test_any_other_validator_error_at_p7_still_refuses_the_whole_file(tmp_path):
    """The hold is exactly two classes (owner ruling 2026-09-21). A stale
    narration hash beside a holdable beat is still a refused file: nothing
    written, nothing queued — the hold must never quietly swallow an error
    it was not approved for."""
    lonely = script_mod._beat(
        f"{script_mod.CITY}/{model.slug(script_mod.PLACE)}/a-lonely-anecdote",
        "a-lonely-anecdote", "A lonely anecdote", "anecdote",
        [script_mod._lp_claim("c01", script_mod.COLLECTING)],
        "Solomon Guggenheim started buying abstract art late in life on the advice "
        "of Hilla Rebay.",
    )
    stale = script_mod._beat(
        f"{script_mod.CITY}/{model.slug(script_mod.PLACE)}/a-stale-narration",
        "a-stale-narration", "A stale narration", "anecdote",
        [script_mod._lp_claim("c01", script_mod.COLLECTING),
         script_mod._lp_claim("c02", script_mod.COMPLETED)],
        script_mod.ORIGINS_NARRATION,
    )
    stale["narration"]["claims_hash"] = "0" * 64

    store, snap, written, _chunks = _finished_job_then_p7_with(tmp_path, [lonely, stale])

    assert snap.status == "error"
    assert "NARRATION_HASH_STALE" in (snap.error or "")
    assert written is None
    assert store.undecided(script_mod.CITY) == []


def test_a_failing_beat_already_on_disk_is_never_held_away_the_file_is_refused(tmp_path):
    """Holding means "not written yet", never "deleted": a beat already in
    beats.json that comes back failing a holdable check (e.g. a re-run that
    left it short) cannot be dropped from the file, so the whole file is
    refused and the file on disk is left as it was."""
    old_id = f"{script_mod.CITY}/{model.slug(script_mod.PLACE)}/an-old-anecdote"
    on_disk = script_mod._beat(
        old_id, "an-old-anecdote", "An old anecdote", "anecdote",
        [script_mod._lp_claim("c01", script_mod.COLLECTING),
         script_mod._lp_claim("c02", script_mod.COMPLETED)],
        script_mod.ORIGINS_NARRATION,
    )
    shrunk = script_mod._beat(
        old_id, "an-old-anecdote", "An old anecdote", "anecdote",
        [script_mod._lp_claim("c01", script_mod.COLLECTING)],
        "Solomon Guggenheim started buying abstract art late in life on the advice "
        "of Hilla Rebay.",
    )

    store, snap, written, _chunks = _finished_job_then_p7_with(
        tmp_path, [shrunk], on_disk=[on_disk]
    )

    assert snap.status == "error"
    assert "ARC_TOO_FEW_CLAIMS" in (snap.error or "")
    assert "COLLISION" not in (snap.error or "")
    assert written == [on_disk]
    assert store.undecided(script_mod.CITY) == []


def test_held_beats_are_queued_only_once_the_rest_of_the_file_validates(tmp_path, monkeypatch):
    """Queue AFTER the re-validation, never before: if the file without the
    held beats is still refused, the review queue must not claim a beat was
    "held" from a commit that never happened. The second validate() is made
    to fail (today's per-record validator cannot surface a new error by
    removing records, so the wrapper stands in for a future cross-beat
    check)."""
    real_validate = model.validate
    lonely_id = f"{script_mod.CITY}/{model.slug(script_mod.PLACE)}/a-lonely-anecdote"
    seen: list[bool] = []

    def validate_then_refuse(records, chunks_root=None):
        errors = real_validate(records, chunks_root=chunks_root)
        ids = {r.get("beat_id") for r in records}
        if lonely_id in ids:
            seen.append(True)
        elif seen:  # the file again, with the held beat taken out
            errors = [*errors, "SOME_CROSS_BEAT_CHECK #0: refused after the hold"]
        return errors

    lonely = script_mod._beat(
        lonely_id,
        "a-lonely-anecdote", "A lonely anecdote", "anecdote",
        [script_mod._lp_claim("c01", script_mod.COLLECTING)],
        "Solomon Guggenheim started buying abstract art late in life on the advice "
        "of Hilla Rebay.",
    )
    monkeypatch.setattr(run.model, "validate", validate_then_refuse)

    store, snap, written, _chunks = _finished_job_then_p7_with(tmp_path, [lonely])

    assert snap.status == "error"
    assert "SOME_CROSS_BEAT_CHECK" in (snap.error or "")
    assert written is None
    assert store.undecided(script_mod.CITY) == []
    assert "commit_held" not in [e.message for e in snap.events]


def test_a_covered_claim_is_absorbed_and_its_story_keeps_the_rest(tmp_path):
    """Owner ruling 2026-09-21 (design note 2026-09-21-covered-verdict-
    design.md): the new story's completion claim is COVERED by the sidebar's
    two claims together. It is absorbed — the corpus beats come back exactly
    as they were on disk, nothing is re-narrated, nothing is queued — and
    the story's own beat is its two unmatched claims, narrated again."""
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
                _claim_verdict("c03", "covered", "b2.c01+b2.c02",
                               reason="b2.c01 and b2.c02 together state it"),
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
    assert [e for e in snap.events if e.message in ("rerun", "rerun_reverted")] == []
    records = json.loads(beats_path.read_text(encoding="utf-8"))
    assert records[:2] == script_mod.folding_records()
    assert [c["text"] for c in records[2]["claims"]] == remainder
    assert store.undecided(script_mod.CITY) == []
    assert model.validate(records, chunks_root=chunks.parent) == []


def test_a_held_story_says_which_claims_the_corpus_already_holds(tmp_path):
    """The disposition an applier needs (owner ruling 2026-09-22). This
    story's completion claim is ABSORBED as covered — stated in full by the
    sidebar's two claims, written nowhere — while its other two claims are
    unmatched, and the story cannot be written because its slug already
    names a beat at the place. Accepting the item must add only what the
    corpus is missing: writing an absorbed claim would put a fact the
    corpus already states into a second beat, which is exactly the
    duplication the merge exists to prevent."""
    chunks = script_mod.chunk_dir(tmp_path)
    data_root = script_mod.data_dir(tmp_path)
    twin = script_mod._beat(
        f"{script_mod.CITY}/{model.slug(script_mod.PLACE)}/{script_mod.STORY_SLUG}",
        script_mod.STORY_SLUG, script_mod.STORY_TITLE, "anecdote",
        [script_mod._lp_claim("c01", script_mod.ADDRESS),
         script_mod._lp_claim("c02", script_mod.HOURS_2023)],
        script_mod.VISITING_NARRATION,
    )
    script_mod.seed_beats(data_root, [*script_mod.folding_records(), twin])
    store = jobs.IngestJobStore()
    job = _book_job(store, chunks)
    _events, sink = _sink_and_events()
    scripted = script_mod.script(script_mod.unit(chunks))
    # The story lost its completion claim to the absorption, so its beat is
    # narrated again over the two it kept — before the slug collision stops
    # it being written at all.
    remainder = [script_mod.COLLECTING["text"], script_mod.OPENED_1939["text"]]
    scripted["answers"]["author"].append(
        script_mod.narration_answer(script_mod.REMAINDER_NARRATION)
    )
    scripted["batch_answers"].update(
        script_mod.sentence_verdicts(remainder, script_mod.REMAINDER_NARRATION)
    )
    scripted["answers"]["merge_judge"] = [
        script_mod.merge_answer(
            "new", "",
            [
                _claim_verdict("c01", "new", reason="no claim mentions collecting"),
                _claim_verdict("c02", "new", reason="no claim mentions 1939"),
                _claim_verdict("c03", "covered", "b2.c01+b2.c02"),
            ],
        )
    ]

    run.run_job(job.id, store, llm.MockClient(sink, **scripted), data_root=data_root)

    [item] = store.undecided(script_mod.CITY)
    assert "already holds a beat with this story's slug" in item.reason
    assert item.shown["claim_disposition"] == [
        {"claim_id": "c01", "status": "unmatched", "beat_id": None, "existing_claim_id": None,
         "applied_claim_id": "c01", "covered_by": []},
        {"claim_id": "c02", "status": "unmatched", "beat_id": None, "existing_claim_id": None,
         "applied_claim_id": "c02", "covered_by": []},
        # BOTH claims that state it together, not just the first: checking
        # c01 alone would not find the fact.
        {"claim_id": "c03", "status": "absorbed", "beat_id": script_mod.COMPLETION_ID,
         "existing_claim_id": "c01", "applied_claim_id": "c01",
         "covered_by": [[script_mod.COMPLETION_ID, "c01"], [script_mod.COMPLETION_ID, "c02"]]},
    ]
    # Ranked by what is actually missing, not by how many claims it has.
    assert item.held_back["claims"] == 2


def test_two_units_holding_the_same_slug_get_an_item_each(tmp_path):
    """Slugs are unique within a UNIT, not a job: two chunks of one book can
    both hold a story called the same thing (the slug-collision path exists
    because that happens). Keyed by slug alone the second story would
    vanish into the first one's item — its claims, place and narration
    discarded with no event — so the hold is keyed by unit AND slug
    (judge, 2026-09-22)."""
    chunks = script_mod.chunk_dir(tmp_path)
    data_root = script_mod.data_dir(tmp_path)
    store = jobs.IngestJobStore()
    job = _book_job(store, chunks)
    unit = script_mod.unit(chunks)
    _events, sink = _sink_and_events()
    running = run._Run(job, store, llm.MockClient(sink), data_root=data_root)
    story = group.Story(
        title=script_mod.STORY_TITLE, story_slug=script_mod.STORY_SLUG,
        place=script_mod.PLACE, new_poi=False, lenses=["hidden_history"],
        beat_type="anecdote", enrichment=group.Enrichment(),
        claim_ids=["c01"],
    )
    claims = [
        judge_claims.JudgedClaim(
            draft=decompose.ClaimDraft(
                claim_id="c01", text=script_mod.COLLECTING["text"], kind="event",
                source=unit.source(script_mod.COLLECTING["span"]),
            ),
            verdict=model.Verdict(judge_model="m", entailed=True, bound_to="x" * 64),
        )
    ]
    narration = judge_narration.JudgedNarration(
        narration=model.Narration(
            text=script_mod.NARRATION, claims_hash="h" * 64, author_model="a",
            verdict=model.NarrationVerdict(
                judge_model="m", sentences_entailed=1, sentences_total=1, bound_to="b" * 64
            ),
        ),
        duration_sec=10, review=model.Review(held=False, reason=None),
    )

    running._hold_story("chunk-07", story, claims, "first chunk", narration)
    running._hold_story("chunk-08", story, claims, "second chunk", narration)
    running._flush_held_stories()

    items = store.undecided(script_mod.CITY)
    assert [(i.story_slug, i.reason) for i in items] == [
        (story.story_slug, "first chunk"), (story.story_slug, "second chunk")
    ]


def test_a_superseding_fold_says_where_the_fact_lives_now(tmp_path):
    """`existing_claim_id` names the claim that was MATCHED; for a
    supersedes or superseded fold that claim is the one demoted to a dated
    belief, and the new fact lands under a fresh id in the same beat. The
    row carries both, so an applier never reads the wrong claim (judge,
    2026-09-22)."""
    unit = script_mod.unit(script_mod.chunk_dir(tmp_path))
    claims = [
        judge_claims.JudgedClaim(
            draft=decompose.ClaimDraft(
                claim_id="c01", text=script_mod.COMPLETED["text"], kind="event",
                source=unit.source(script_mod.COMPLETED["span"]),
            ),
            verdict=model.Verdict(judge_model="m", entailed=True, bound_to="x" * 64),
        )
    ]

    def outcome_with(kind: str, applied: str) -> merge.MergeOutcome:
        return merge.MergeOutcome(
            story_slug="s", place=script_mod.PLACE, judge_story="new", judge_model="m",
            story="new", beat_id=None, judged=list(claims),
            claims=[
                merge.ClaimOutcome(
                    claim_id="c01", verdict="conflict",
                    existing_beat_id=script_mod.ORIGINS_ID, existing_claim_id="c02",
                    outcome=kind, applied_claim_id=applied, new_value="1960",
                    existing_value="1959", reason="different year",
                )
            ],
        )

    rows = {
        kind: run._disposition(claims, outcome_with(kind, applied))[0]
        for kind, applied in (
            ("contested", "c02"), ("supersedes", "c03"), ("superseded", "c03")
        )
    }
    assert rows["contested"]["status"] == "written"
    assert (rows["contested"]["existing_claim_id"], rows["contested"]["applied_claim_id"]) == (
        "c02", "c02"
    )
    for kind in ("supersedes", "superseded"):
        assert rows[kind]["status"] == "written", kind
        # the matched claim is NOT where the fact lives now
        assert rows[kind]["existing_claim_id"] == "c02", kind
        assert rows[kind]["applied_claim_id"] == "c03", kind


def test_a_split_covered_claim_that_stayed_new_is_unmatched_with_no_covered_refs():
    """Skeptic panel 2026-09-25 (F6): a covered claim carrying a judgment its
    named claims lack stays `new` (ADR-0004) but keeps its refs on the
    outcome for its event. Its row must read plainly unmatched — `covered_by` is for
    absorbed rows only, or a reviewer mistakes it for absorbed."""
    from types import SimpleNamespace

    split = merge.ClaimOutcome(
        claim_id="c01", verdict="covered", existing_beat_id=None, existing_claim_id=None,
        outcome="new", applied_claim_id="c01", new_value=None, existing_value=None,
        reason="kept for its judgment", covered_by=(("b/a", "c01"), ("b/b", "c01")),
        new_judgments=("beloved",),
    )
    outcome = merge.MergeOutcome(
        story_slug="s", place="p", judge_story="new", judge_model="m", story="new",
        beat_id=None, claims=[split],
    )
    claims = [SimpleNamespace(draft=SimpleNamespace(claim_id="c01"))]

    rows = run._disposition(claims, outcome)

    assert [(r["status"], r["covered_by"]) for r in rows] == [("unmatched", [])]


def test_a_rerun_shows_the_narrator_its_sources_judgments_and_hashes_as_p7_does():
    """ADR-0004: a merged beat's claim carries a second source that judges
    it. `judged_of_beat` hands P4 that word, and the hash P4's draft carries
    equals the one P7's validator recomputes — else the first beat with a
    judgment is refused NARRATION_HASH_STALE. Mutants: judged_of_beat
    ignoring judgments; draft_from hashing bare texts."""
    beat = model.Beat.model_validate(script_mod.merged_beat_with_judgment())
    claims = run.judged_of_beat(beat)

    assert [c.judgments for c in claims] == [(), ("masterpiece",)]
    draft = narrate.draft_from("Some narration.", claims, "claude-opus-5")
    assert draft.claims_hash == model.claims_hash([c.model_dump() for c in beat.claims])
