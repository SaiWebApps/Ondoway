"""Tests for src/ingest/merge.py — Docs/ingestion/rebuild-spec.md slice 6
(P6 merge and the conflict report).

Every test runs against llm.MockClient over records built from the real
Lonely Planet chunk-07 and Frommer's chunk-05 Guggenheim passages; no live
client, no network, no spend (test_no_live_client_in_this_file, the same
walker as the slice-3/4/5 files). Spans are quoted verbatim from those
chunks so `model.validate(..., chunks_root=...)` can ground them.
"""

from __future__ import annotations

import ast
import json
import shutil
from pathlib import Path

import pytest

import src.ingest.llm as llm
import src.ingest.merge as merge
import src.ingest.narrate as narrate
import src.ingest.prompts as prompts
from src.ingest import decompose, group, judge_claims, model
from src.ingest import unit as unit_mod

REPO_ROOT = Path(__file__).resolve().parent.parent
BOOKS_ROOT = REPO_ROOT / "Books" / "new_york"
LP_SOURCE = "lonely-planet-new-york-city"
LP_CHUNK = "chunk-07-upper-east-side"
FR_SOURCE = "frommers-nyc-2024"
FR_CHUNK = "chunk-05-ch05-uptown"
PLACE = "Guggenheim Museum"
ORIGINS_ID = "new_york/guggenheim-museum/how-the-museum-came-to-be"
VISITING_ID = "new_york/guggenheim-museum/visiting-the-guggenheim"

#: Model ids as the RESPONSE reports them — distinct from the configured
#: ROLE_MODEL entries so a test can tell a stamp from a config copy.
RESPONSE_MERGE_MODEL = "claude-sonnet-5-20260601"
RESPONSE_CLAIM_JUDGE_MODEL = "claude-haiku-4-5-20251001"
AUTHOR_MODEL = "claude-opus-5"

# ── Lonely Planet (2023) claims, spans verbatim in chunk-07 ─────────────────
COLLECTING: dict = {
    "text": (
        "Solomon R. Guggenheim began collecting abstract art in his sixties, "
        "urged on by his adviser, the German baroness Hilla Rebay."
    ),
    "kind": "event",
    "span": (
        "a New York mining magnate who began acquiring abstract art in his 60s "
        "at the behest of his art adviser, an eccentric German baroness named Hilla Rebay"
    ),
}
COMPLETED: dict = {
    "text": (
        "The Guggenheim building was finished in 1959, by which time both "
        "Frank Lloyd Wright and Solomon Guggenheim were dead."
    ),
    "kind": "event",
    "span": (
        "Construction was finally completed in 1959 – after both "  # noqa: RUF001
        "Wright and Guggenheim had passed away."
    ),
}
ADDRESS: dict = {
    "text": (
        "The Solomon R. Guggenheim Museum stands at 1071 Fifth Avenue on the "
        "corner of East 89th Street."
    ),
    "kind": "state",
    "span": "1071 Fifth Ave, at E 89th St",
}
HOURS_2023: dict = {
    "text": "Pay-what-you-wish admission at the Guggenheim runs from 5pm to 8pm on Saturdays.",
    "kind": "state",
    "span": "pay-what-you-wish 5-8pm Sat",
}

# ── Frommer's (2024) claims, spans verbatim in chunk-05 ─────────────────────
COMPLETED_FR: dict = {
    "text": "The Guggenheim building was finished in 1959.",
    "kind": "event",
    "span": "Visiting this 1959 masterpiece",
}
MUSCHAMP: dict = {
    "text": "The architectural critic Herbert Muschamp praised the building's look.",
    "kind": "belief",
    "span": "Architectural critic Herbert Muschamp described the look best",
}
HOURS_2024: dict = {
    "text": "Pay-what-you-wish admission at the Guggenheim runs from 6pm to 8pm on Saturdays.",
    "kind": "state",
    "span": "pay-what-you-wish Sat 6–8pm",  # noqa: RUF001
}


def _source(item: dict, source_id: str, chunk: str, as_of: int, stated_value=None) -> dict:
    return {
        "source_id": source_id,
        "chunk": chunk,
        "span": item["span"],
        "as_of": as_of,
        "rights_basis": "owned_copy",
        "stated_value": stated_value,
    }


def _lp_claim(claim_id: str, item: dict) -> dict:
    """A resolved, single-source Lonely Planet claim as it sits on disk."""
    return {
        "claim_id": claim_id,
        "text": item["text"],
        "kind": item["kind"],
        "status": "resolved",
        "sources": [_source(item, LP_SOURCE, LP_CHUNK, 2023)],
        "resolved_value": None,
        "resolution": None,
        "verdict": {
            "judge_model": RESPONSE_CLAIM_JUDGE_MODEL,
            "entailed": True,
            "bound_to": model.bind(item["text"], item["span"]),
        },
    }


def _beat(beat_id: str, story_slug: str, title: str, beat_type: str, claims: list[dict],
          narration_text: str) -> dict:
    return {
        "beat_id": beat_id,
        "city_name": "new_york",
        "poi_name": PLACE,
        "story_slug": story_slug,
        "title": title,
        "beat_type": beat_type,
        "lenses": ["hidden_history"],
        "sub_location": None,
        "trigger_address": None,
        "claims": claims,
        "narration": {
            "text": narration_text,
            "claims_hash": model.claims_hash(claims),
            "author_model": AUTHOR_MODEL,
            "verdict": {
                "judge_model": RESPONSE_CLAIM_JUDGE_MODEL,
                "sentences_entailed": 2,
                "sentences_total": 2,
                "bound_to": model.bind(narration_text),
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
        "duration_sec": 20,
        "review": {"held": False, "reason": None},
    }


def _existing() -> list[model.Beat]:
    """Two Lonely Planet beats at the place, both valid on disk."""
    origins = _beat(
        ORIGINS_ID,
        "how-the-museum-came-to-be",
        "How the museum came to be",
        "anecdote",
        [_lp_claim("c01", COLLECTING), _lp_claim("c02", COMPLETED)],
        "Solomon Guggenheim started buying abstract art late in life on the advice "
        "of Hilla Rebay. The building was finished in 1959, by which time both "
        "Wright and Guggenheim had died.",
    )
    visiting = _beat(
        VISITING_ID,
        "visiting-the-guggenheim",
        "Visiting the Guggenheim",
        "stop_orientation",
        [_lp_claim("c01", ADDRESS), _lp_claim("c02", HOURS_2023)],
        "The museum stands at 1071 Fifth Avenue, on the corner of East 89th Street. "
        "On Saturday evenings from five to eight you pay what you wish.",
    )
    records = [origins, visiting]
    assert model.validate(records) == []
    return [model.Beat.model_validate(record) for record in records]


def _fr_unit() -> unit_mod.Unit:
    return unit_mod.load_unit(
        BOOKS_ROOT,
        city="new_york",
        source_id=FR_SOURCE,
        chunk=FR_CHUNK,
        as_of=2024,
        rights_basis="owned_copy",
    )


def _judged(unit: unit_mod.Unit, claim_id: str, item: dict) -> judge_claims.JudgedClaim:
    draft = decompose.ClaimDraft(
        claim_id=claim_id,
        text=item["text"],
        kind=item["kind"],
        source=unit.source(item["span"]),
    )
    return judge_claims.JudgedClaim(
        draft=draft,
        verdict=model.Verdict(
            judge_model=RESPONSE_CLAIM_JUDGE_MODEL,
            entailed=True,
            bound_to=model.bind(draft.text, draft.source.span),
        ),
    )


def _story(title: str, claim_ids: list[str], beat_type: str = "anecdote") -> group.Story:
    return group.Story(
        title=title,
        story_slug=model.slug(title),
        place=PLACE,
        new_poi=False,
        lenses=["hidden_history"],
        beat_type=beat_type,
        enrichment=group.Enrichment(),
        claim_ids=claim_ids,
    )


def _sink_and_events(*, answers: bool = False):
    """A sink and the events it received; `merge_answered` (the raw judge
    answer, asserted by its own test) is kept only when `answers`."""
    events: list[tuple[str, dict]] = []

    def sink(kind: str, payload: dict) -> None:
        if answers or kind != "merge_answered":
            events.append((kind, payload))

    return events, sink


def _claim_verdict(
    claim_id: str,
    verdict: str,
    existing_claim_id: str = "",
    new_value: str = "",
    existing_value: str = "",
    reason: str = "the two state the same fact",
) -> dict:
    return {
        "claim_id": claim_id,
        "verdict": verdict,
        "existing_claim_id": existing_claim_id,
        "new_value": new_value,
        "existing_value": existing_value,
        "reason": reason,
    }


def _answer(story: str, beat_id: str, claims: list[dict]) -> llm.MockAnswer:
    return llm.MockAnswer(
        text=json.dumps({"story": story, "beat_id": beat_id, "claims": claims}),
        model_id=RESPONSE_MERGE_MODEL,
    )


def _armed_judge(answers: list[llm.MockAnswer], claims) -> llm.MockClient:
    """A MockClient scripted with the merge judge's sync answers, its
    estimate gate armed against the claim texts and the real P6_PLAN."""
    _events, sink = _sink_and_events()
    mock = llm.MockClient(sink, answers={"merge_judge": answers})
    mock.estimate([c.draft.text for c in claims], list(merge.P6_PLAN))
    return mock


class _RecordingClient:
    """Records every complete() call's exact arguments; refuses batch calls."""

    def __init__(self, client) -> None:
        self._client = client
        self.sync_calls: list[dict] = []

    def complete(self, role, prompt, schema, *, phase, max_tokens):
        self.sync_calls.append(
            {"role": role, "prompt": prompt, "schema": schema, "phase": phase,
             "max_tokens": max_tokens}
        )
        return self._client.complete(role, prompt, schema, phase=phase, max_tokens=max_tokens)

    def complete_batch(self, *, role, prompts, schema, phase, max_tokens):
        raise AssertionError("P6 is a sync phase; complete_batch must never be called")


def _chunks_root(tmp_path: Path) -> Path:
    """The two real chunks copied under one root, the shape validate() reads."""
    root = tmp_path / "chunks"
    for source_id, chunk in ((LP_SOURCE, LP_CHUNK), (FR_SOURCE, FR_CHUNK)):
        (root / source_id).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(BOOKS_ROOT / source_id / f"{chunk}.txt", root / source_id / f"{chunk}.txt")
    return root


def _records(beats: list[model.Beat]) -> list[dict]:
    return [beat.model_dump() for beat in beats]


def _decided(outcome: merge.MergeOutcome) -> list[tuple]:
    """(new claim id, outcome, matched existing id, id after apply) per claim."""
    return [
        (c.claim_id, c.outcome, c.existing_claim_id, c.applied_claim_id) for c in outcome.claims
    ]


def test_same_claim_appends_source_not_beat(tmp_path):
    """The spec's proving node: a second source telling a claim the corpus
    already holds becomes ONE claim with the second source appended — never
    a sibling beat, never a sibling claim. The merge judge runs once, sync,
    under merge_judge/P6 with the new claims and every candidate beat's
    claims in its prompt; the deterministic signature hint agrees, so the
    outcome applies. The merged claim keeps its text and kind, gains the
    Frommer's source with the value it states, becomes corroborated, and its
    verdict is re-bound over both spans (the binding the slice-1 validator
    recomputes). The beat's narration is untouched and nothing is marked
    for a rerun: the resolved texts did not change. The merged file
    validates end to end with its spans grounded in the real chunks."""
    unit = _fr_unit()
    claims = [_judged(unit, "c01", COMPLETED_FR)]
    story = _story("The 1959 building", ["c01"])
    existing = _existing()
    answer = _answer("same", "b1", [_claim_verdict("c01", "same", "b1.c02", "1959", "1959")])
    recorder = _RecordingClient(_armed_judge([answer], claims))
    events, sink = _sink_and_events()

    outcome = merge.merge(story, claims, existing, recorder, events=sink)

    assert [(c["role"], c["phase"]) for c in recorder.sync_calls] == [("merge_judge", "P6")]
    call = recorder.sync_calls[0]
    assert call["schema"] == prompts.P6_MERGE_SCHEMA
    assert COMPLETED_FR["text"] in call["prompt"]
    assert COMPLETED["text"] in call["prompt"]
    assert HOURS_2023["text"] in call["prompt"]
    assert 'beat b1 "' in call["prompt"] and 'beat b2 "' in call["prompt"]  # by handle
    assert COMPLETED_FR["span"] not in call["prompt"]  # the judge reads claims, not spans

    assert outcome.held is False and outcome.reason is None
    assert outcome.story == "same"
    assert outcome.beat_id == ORIGINS_ID
    assert outcome.judge_model == RESPONSE_MERGE_MODEL
    assert [(c.claim_id, c.outcome, c.existing_claim_id) for c in outcome.claims] == [
        ("c01", "same", "c02")
    ]
    assert outcome.rerun == []
    assert [kind for kind, _ in events] == ["merge_folded", "merge_decided"]
    assert events[1][1]["story_slug"] == story.story_slug
    assert events[1][1]["story"] == "same"

    merged = merge.apply(outcome, existing)

    assert [b.beat_id for b in merged] == [ORIGINS_ID, VISITING_ID]
    beat = merged[0]
    assert [c.claim_id for c in beat.claims] == ["c01", "c02"]
    claim = beat.claims[1]
    assert claim.text == COMPLETED["text"]
    assert claim.kind == "event"
    assert claim.status == "resolved"
    assert [(s.source_id, s.chunk, s.span, s.as_of, s.stated_value) for s in claim.sources] == [
        (LP_SOURCE, LP_CHUNK, COMPLETED["span"], 2023, None),
        (FR_SOURCE, FR_CHUNK, COMPLETED_FR["span"], 2024, "1959"),
    ]
    assert claim.resolution == model.Resolution(by="corroborated")
    assert claim.verdict.judge_model == RESPONSE_CLAIM_JUDGE_MODEL
    assert claim.verdict.entailed is True
    assert claim.verdict.bound_to == model.bind(
        COMPLETED["text"], COMPLETED["span"] + "\n" + COMPLETED_FR["span"]
    )
    assert beat.narration == existing[0].narration
    assert merged[1] == existing[1]
    # Pure: the caller's beats are not mutated.
    assert len(existing[0].claims[1].sources) == 1

    assert model.validate(_records(merged), chunks_root=_chunks_root(tmp_path)) == []


def test_new_claim_joins_the_matched_beat_and_marks_it_for_rerun(tmp_path):
    """A claim of the same story that no existing claim states is appended
    to the matched beat under a fresh id (the new story's own ids would
    collide), resolved, carrying its own P3 verdict. The beat's resolved
    texts changed, so the outcome names it for a P4/P5 rerun and its
    narration.claims_hash is stale by design: validate reports exactly
    that line and nothing else — the runner, not P6, re-narrates."""
    unit = _fr_unit()
    claims = [_judged(unit, "c01", COMPLETED_FR), _judged(unit, "c02", MUSCHAMP)]
    story = _story("The 1959 building and its critic", ["c01", "c02"])
    existing = _existing()
    answer = _answer(
        "same",
        "b1",
        [
            _claim_verdict("c01", "same", "b1.c02", "1959", "1959"),
            _claim_verdict("c02", "new", reason="no claim mentions Muschamp"),
        ],
    )
    mock = _armed_judge([answer], claims)

    outcome = merge.merge(story, claims, existing, mock)

    assert outcome.held is False
    assert outcome.story == "same"
    assert _decided(outcome) == [
        ("c01", "same", "c02", "c02"),
        ("c02", "new", None, "c03"),
    ]
    assert outcome.rerun == [ORIGINS_ID]

    merged = merge.apply(outcome, existing)

    assert [b.beat_id for b in merged] == [ORIGINS_ID, VISITING_ID]
    beat = merged[0]
    assert [c.claim_id for c in beat.claims] == ["c01", "c02", "c03"]
    added = beat.claims[2]
    assert added.text == MUSCHAMP["text"]
    assert added.kind == "belief" and added.status == "resolved"
    assert [(s.source_id, s.span, s.as_of, s.stated_value) for s in added.sources] == [
        (FR_SOURCE, MUSCHAMP["span"], 2024, None)
    ]
    assert added.verdict == claims[1].verdict
    assert added.resolution is None
    assert beat.narration == existing[0].narration  # untouched: stale by design

    errors = model.validate(_records(merged), chunks_root=_chunks_root(tmp_path))
    assert errors == [
        f"NARRATION_HASH_STALE {ORIGINS_ID}: narration.claims_hash does not match "
        "a fresh recomputation over this beat's claims"
    ]


def test_judge_and_signature_disagree_holds_new_story():
    """The spec's proving node (D7, amended in slice 10): the signature hint
    is a one-way tripwire. It holds a story only on POSITIVE evidence
    against the judge — here the completion claim's signature matches LP's
    c02 while the judge calls it `new`. The story is held as a review queue
    item — `held`, the disagreement spelled out, `beat_held` emitted for P6,
    no second judge call (the judge is never re-asked toward the hint), and
    apply() refuses it. The corpus is untouched. A judge naming a DIFFERENT
    claim than the one the signature matches is held the same way."""
    unit = _fr_unit()
    claims = [_judged(unit, "c01", COMPLETED_FR)]
    story = _story("The 1959 building", ["c01"])
    existing = _existing()
    answer = _answer("new", "", [_claim_verdict("c01", "new", reason="nothing like it")])
    recorder = _RecordingClient(_armed_judge([answer], claims))
    events, sink = _sink_and_events()

    outcome = merge.merge(story, claims, existing, recorder, events=sink)

    assert len(recorder.sync_calls) == 1
    assert outcome.held is True
    assert outcome.story is None and outcome.beat_id is None
    assert outcome.judge_story == "new"
    assert outcome.claims == []
    assert outcome.rerun == []
    assert outcome.reason == (
        f"judge and signature disagree: claim c01: judge says new; signature matches c02 of "
        f"{ORIGINS_ID}"
    )
    assert events == [
        ("beat_held", {"story_slug": story.story_slug, "phase": "P6", "reason": outcome.reason})
    ]
    with pytest.raises(ValueError, match="held outcome"):
        merge.apply(outcome, existing)
    assert [len(b.claims) for b in existing] == [2, 2]

    answer = _answer(
        "same", "b2", [_claim_verdict("c01", "same", "b2.c02", "1959", "1959")]
    )
    outcome = merge.merge(story, claims, existing, _armed_judge([answer], claims))
    assert outcome.held is True
    assert outcome.reason == (
        f"judge and signature disagree: claim c01: judge says same with c02 of {VISITING_ID}; "
        f"signature matches c02 of {ORIGINS_ID}"
    )


RECEPTION_ID = "new_york/guggenheim-museum/derided-and-hailed-the-ziggurat-debate"
# Job 2's real "critics rejected it" pair: LP c03 and Frommer's c148. The
# signatures share only "critics" and "museum" — invisible to the hint.
DERIDED: dict = {
    "text": (
        "Even before the Guggenheim Museum opened, its inverted ziggurat structure "
        "was derided by some critics but hailed by others."
    ),
    "kind": "event",
    "span": (
        "Even before it opened, the inverted ziggurat structure was derided by\n"
        "some critics but hailed by others"
    ),
}
SAVAGED: dict = {
    "text": "The Guggenheim Museum structure was savaged by the New York Times.",
    "kind": "event",
    "span": "The structure was savaged by the New York Times",
}
CRITICS_FR: dict = {
    "text": (
        "The museum's earliest critics rejected it, and Newsweek ran a review under "
        'the headline "Museum or Cupcake?"'
    ),
    "kind": "event",
    "span": (
        "Early critics dismissed the museum (Newsweek\u2019s insipid review was "
        "headlined \u201cMuseum or Cupcake?\u201d)"
    ),
}


def test_a_judge_match_the_signature_cannot_see_applies(tmp_path):
    """Slice 10, the merge trio's second and third defects: across job 2's
    12 judged stories the lexical signature matched ONE claim, while the
    judge named four correct matches — and hold-on-disagreement held or
    re-asked every one away. A signature finding nothing is not evidence
    against the judge (a paraphrase shares no tokens), so the judge's match
    applies: Frommer's "earliest critics rejected it" corroborates LP's
    "derided by some critics", one claim with both sources."""
    unit = _fr_unit()
    claims = [_judged(unit, "c01", CRITICS_FR)]
    story = _story("Museum or cupcake", ["c01"])
    reception = _beat(
        RECEPTION_ID,
        "derided-and-hailed-the-ziggurat-debate",
        "Derided and hailed: the ziggurat debate",
        "anecdote",
        [_lp_claim("c01", DERIDED), _lp_claim("c02", SAVAGED)],
        "Even before it opened, critics derided the ziggurat and others hailed it. "
        "The New York Times savaged it.",
    )
    assert model.validate([reception]) == []
    existing = [*_existing(), model.Beat.model_validate(reception)]
    answer = _answer(
        "same",
        "b3",
        [_claim_verdict("c01", "same", "b3.c01", "rejected by critics", "derided by critics")],
    )
    assert merge.signature_hint(claims, existing).matches == {"c01": None}

    outcome = merge.merge(story, claims, existing, _armed_judge([answer], claims))

    assert outcome.held is False
    assert outcome.story == "same" and outcome.beat_id == RECEPTION_ID
    assert _decided(outcome) == [("c01", "same", "c01", "c01")]
    merged = merge.apply(outcome, existing)
    assert [s.source_id for s in merged[2].claims[0].sources] == [LP_SOURCE, FR_SOURCE]
    assert model.validate(_records(merged), chunks_root=_chunks_root(tmp_path)) == []


SECOND_SOURCE = "second-source"
SECOND_CHUNK = "guggenheim-notes"
SECOND_TEXT = (
    "Notes on the Guggenheim. The critics were divided; the building was finished "
    "in 1960, a year after the architect's death, and opened that autumn.\n"
)
FINISHED_1960: dict = {
    "text": "The Guggenheim building was finished in 1960.",
    "kind": "event",
    "span": "the building was finished in 1960",
}


def _second_unit() -> unit_mod.Unit:
    """A synthetic second source stating a different completion year — the
    contest fixtures/ingestion/defects.json plants; no real book says 1960."""
    return unit_mod.Unit(
        city="new_york",
        source_id=SECOND_SOURCE,
        chunk=SECOND_CHUNK,
        text=SECOND_TEXT,
        as_of=2024,
        rights_basis="own_work",
    )


def _chunks_root_with_second(tmp_path: Path) -> Path:
    root = _chunks_root(tmp_path)
    (root / SECOND_SOURCE).mkdir()
    (root / SECOND_SOURCE / f"{SECOND_CHUNK}.txt").write_text(SECOND_TEXT, encoding="utf-8")
    return root


def test_conflict_between_event_claims_is_contested_never_superseded(tmp_path):
    """D9: two event claims that disagree are contested; a newer source is
    never right by default. The judge reports the conflict with both
    values; the code makes it ONE claim, status contested, kind untouched,
    the second source appended and every source carrying the value it
    states — the shape the validator's CONTESTED_NEEDS_DIFFERING_VALUES
    demands (proved both ways). The verdict is re-bound over both spans
    with its judge unchanged; the beat's resolved texts shrank, so it is
    named for a rerun. The full D9 table is pinned alongside."""
    unit = _second_unit()
    claims = [_judged(unit, "c01", FINISHED_1960)]
    story = _story("Finished in 1960", ["c01"])
    existing = _existing()
    answer = _answer(
        "same", "b1", [_claim_verdict("c01", "conflict", "b1.c02", "1960", "1959")]
    )

    outcome = merge.merge(story, claims, existing, _armed_judge([answer], claims))

    assert outcome.held is False
    assert outcome.story == "same"
    assert _decided(outcome) == [
        ("c01", "contested", "c02", "c02")
    ]
    assert outcome.rerun == [ORIGINS_ID]

    merged = merge.apply(outcome, existing)

    beat = merged[0]
    assert [c.claim_id for c in beat.claims] == ["c01", "c02"]  # no sibling claim
    claim = beat.claims[1]
    assert claim.text == COMPLETED["text"]
    assert claim.kind == "event"
    assert claim.status == "contested"
    assert claim.resolved_value is None and claim.resolution is None
    assert [(s.source_id, s.span, s.as_of, s.stated_value) for s in claim.sources] == [
        (LP_SOURCE, COMPLETED["span"], 2023, "1959"),
        (SECOND_SOURCE, FINISHED_1960["span"], 2024, "1960"),
    ]
    assert claim.verdict.judge_model == RESPONSE_CLAIM_JUDGE_MODEL
    assert claim.verdict.bound_to == model.bind(
        COMPLETED["text"], COMPLETED["span"] + "\n" + FINISHED_1960["span"]
    )

    root = _chunks_root_with_second(tmp_path)
    records = _records(merged)
    assert model.validate(records, chunks_root=root) == [
        f"NARRATION_HASH_STALE {ORIGINS_ID}: narration.claims_hash does not match "
        "a fresh recomputation over this beat's claims"
    ]
    # The validator really does demand the two values: strip one and it refuses.
    records[0]["claims"][1]["sources"][1]["stated_value"] = None
    assert any(
        err.startswith(f"CONTESTED_NEEDS_DIFFERING_VALUES {ORIGINS_ID}: claim c02")
        for err in model.validate(records, chunks_root=root)
    )

    # The D9 table the code applies to a judged conflict, by kind and year.
    assert merge.conflict_outcome("event", 2024, "event", 2023) == "contested"
    assert merge.conflict_outcome("event", 2024, "belief", 2023) == "supersedes"
    assert merge.conflict_outcome("state", 2024, "belief", 2023) == "supersedes"
    assert merge.conflict_outcome("state", 2024, "state", 2023) == "supersedes"
    assert merge.conflict_outcome("state", 2023, "state", 2024) == "superseded"
    assert merge.conflict_outcome("state", 2024, "state", 2024) == "contested"
    assert merge.conflict_outcome("belief", 2024, "belief", 2023) == "supersedes"
    assert merge.conflict_outcome("belief", 2024, "event", 2023) == "contested"
    assert merge.conflict_outcome("state", 2024, "event", 2023) == "contested"
    assert merge.conflict_outcome("event", 2024, "state", 2023) == "contested"


def test_supersedes_rekinds_old_claim_as_dated_belief(tmp_path):
    """The spec's proving node: the two real guidebooks disagree on the
    Guggenheim's pay-what-you-wish hours — 5-8pm on Saturdays in Lonely
    Planet (2023), 6-8pm in Frommer's (2024). Two state claims: the newer
    as_of resolves it (D9). The old claim is re-kinded `belief`, status
    `superseded`, dated by its 2023 source, with its text, sources and
    verdict byte-for-byte unchanged; the new claim joins the beat resolved
    under a fresh id, carrying its own verdict; the story outcome is
    `supersedes`; the beat is named for a rerun. The other beat is
    untouched and the file validates with spans grounded in both chunks."""
    unit = _fr_unit()
    claims = [_judged(unit, "c01", HOURS_2024)]
    story = _story("Saturday hours", ["c01"], beat_type="stop_orientation")
    existing = _existing()
    answer = _answer(
        "supersedes",
        "b2",
        [_claim_verdict("c01", "conflict", "b2.c02", "6pm to 8pm", "5pm to 8pm")],
    )

    outcome = merge.merge(story, claims, existing, _armed_judge([answer], claims))

    assert outcome.held is False
    assert outcome.story == "supersedes"
    assert outcome.judge_story == "supersedes"
    assert outcome.beat_id == VISITING_ID
    assert _decided(outcome) == [
        ("c01", "supersedes", "c02", "c03")
    ]
    assert outcome.rerun == [VISITING_ID]

    merged = merge.apply(outcome, existing)

    assert merged[0] == existing[0]
    beat = merged[1]
    assert [c.claim_id for c in beat.claims] == ["c01", "c02", "c03"]
    old = beat.claims[1]
    before = existing[1].claims[1]
    assert old.kind == "belief" and before.kind == "state"
    assert old.status == "superseded"
    assert old == before.model_copy(update={"kind": "belief", "status": "superseded"})
    assert old.verdict == before.verdict
    assert [s.as_of for s in old.sources] == [2023]
    new = beat.claims[2]
    assert new.text == HOURS_2024["text"]
    assert new.kind == "state" and new.status == "resolved"
    assert [(s.source_id, s.span, s.as_of, s.stated_value) for s in new.sources] == [
        (FR_SOURCE, HOURS_2024["span"], 2024, "6pm to 8pm")
    ]
    assert new.verdict == claims[0].verdict

    assert model.validate(_records(merged), chunks_root=_chunks_root(tmp_path)) == [
        f"NARRATION_HASH_STALE {VISITING_ID}: narration.claims_hash does not match "
        "a fresh recomputation over this beat's claims"
    ]


def test_new_story_is_reported_not_applied():
    """A story no existing beat tells (judge and signature agree) is `new`:
    no claim is matched, the outcome names no beat and no rerun, and
    apply() returns the beats unchanged — assembling the new record needs
    the narration the job runner holds, so P6 only reports. With no beat at
    the place at all the prompt says so and the answer is the same."""
    unit = _fr_unit()
    claims = [_judged(unit, "c01", MUSCHAMP)]
    story = _story("The critic", ["c01"])
    existing = _existing()
    answer = _answer("new", "", [_claim_verdict("c01", "new", reason="no claim mentions Muschamp")])
    events, sink = _sink_and_events()

    outcome = merge.merge(story, claims, existing, _armed_judge([answer], claims), events=sink)

    assert outcome.held is False
    assert outcome.story == "new" and outcome.judge_story == "new"
    assert outcome.beat_id is None
    assert _decided(outcome) == [("c01", "new", None, "c01")]
    assert outcome.rerun == []
    assert events == [
        (
            "merge_decided",
            {
                "story_slug": story.story_slug,
                "story": "new",
                "beat_id": None,
                "claims": [{"claim_id": "c01", "outcome": "new", "existing_beat_id": None,
                            "existing_claim_id": None}],
                "rerun": [],
            },
        )
    ]
    assert merge.apply(outcome, existing) == existing

    recorder = _RecordingClient(_armed_judge([answer], claims))
    outcome = merge.merge(story, claims, [], recorder)
    assert "holds no beat at this place" in recorder.sync_calls[0]["prompt"]
    assert outcome.story == "new"
    assert merge.apply(outcome, []) == []


def test_merge_judge_may_never_be_the_author():
    """llm's judge-independence gate covers the merge judge and this module
    does not re-implement it: a client configured with merge_judge on the
    author's model refuses at construction, and a merge_judge RESPONSE
    reporting the author's model id raises JudgeIsAuthor out of merge() —
    a programming error, never a held story."""
    unit = _fr_unit()
    claims = [_judged(unit, "c01", COMPLETED_FR)]
    story = _story("The 1959 building", ["c01"])
    existing = _existing()
    _events, sink = _sink_and_events()
    with pytest.raises(llm.JudgeIsAuthor):
        llm.MockClient(sink, roles={**llm.ROLE_MODEL, "merge_judge": llm.ROLE_MODEL["author"]})

    text = _answer("same", "b1", [_claim_verdict("c01", "same", "b1.c02", "1959", "1959")]).text
    authored = llm.MockAnswer(text=text, model_id="claude-opus-5-20260601")
    events, sink = _sink_and_events()
    with pytest.raises(llm.JudgeIsAuthor):
        merge.merge(story, claims, existing, _armed_judge([authored], claims), events=sink)
    assert events == []


def test_transport_failure_and_unreadable_answer_hold_the_beat():
    """The same failure contract as P3/P4: an empty or truncated completion,
    or an answer that is not the P6 JSON, holds the beat (`beat_held`, then
    narrate.BeatHeld for phase P6) after exactly one call — no re-ask, the
    judge or the transport is broken, not the story. Any other LlmError is a
    programming error and propagates."""
    unit = _fr_unit()
    claims = [_judged(unit, "c01", COMPLETED_FR)]
    story = _story("The 1959 building", ["c01"])
    existing = _existing()
    bad_answers = [
        llm.MockAnswer(text="   ", model_id=RESPONSE_MERGE_MODEL),
        llm.MockAnswer(
            text='{"story": "same"', model_id=RESPONSE_MERGE_MODEL, stop_reason="max_tokens"
        ),
        llm.MockAnswer(text="I cannot decide between these.", model_id=RESPONSE_MERGE_MODEL),
    ]
    reasons: list[str] = []
    for bad in bad_answers:
        events, sink = _sink_and_events()
        mock = _armed_judge([bad], claims)
        with pytest.raises(narrate.BeatHeld) as held:
            merge.merge(story, claims, existing, mock, events=sink)
        assert held.value.phase == "P6"
        assert held.value.story_slug == story.story_slug
        assert mock.calls == [("merge_judge", "P6")]
        payload = {"story_slug": story.story_slug, "phase": "P6", "reason": held.value.reason}
        assert events == [("beat_held", payload)]
        reasons.append(held.value.reason)
    assert reasons[0].startswith("transport: ") and "empty" in reasons[0]
    assert reasons[1].startswith("transport: ") and "truncated" in reasons[1]
    assert reasons[2] == (
        "schema: the merge judge's answer was not valid JSON matching the P6 merge schema"
    )

    good = _answer("same", "b1", [_claim_verdict("c01", "same", "b1.c02", "1959", "1959")])
    _events, sink = _sink_and_events()
    unarmed = llm.MockClient(sink, answers={"merge_judge": [good]})
    with pytest.raises(llm.EstimateNotPrinted):
        merge.merge(story, claims, existing, unarmed)


def test_an_answer_naming_unknown_ids_is_reasked_once_then_held():
    """JSON that fits the schema but not the record — an existing_claim_id
    the beat does not have, a conflict missing a value — is re-asked ONCE
    with the answer and every problem quoted back (`merge_reasked`); a
    usable second answer is applied like a first; a second unusable one
    holds the beat with the problems named, and a third call never
    happens."""
    unit = _fr_unit()
    claims = [_judged(unit, "c01", COMPLETED_FR)]
    story = _story("The 1959 building", ["c01"])
    existing = _existing()
    bad = _answer("same", "b1", [_claim_verdict("c01", "same", "b1.c09", "1959", "1959")])
    good = _answer("same", "b1", [_claim_verdict("c01", "same", "b1.c02", "1959", "1959")])
    recorder = _RecordingClient(_armed_judge([bad, good], claims))
    events, sink = _sink_and_events()

    outcome = merge.merge(story, claims, existing, recorder, events=sink)

    assert outcome.held is False
    assert _decided(outcome) == [("c01", "same", "c02", "c02")]
    assert len(recorder.sync_calls) == 2
    problem = "claim c01: existing_claim_id 'b1.c09' is not a claim at this place"
    redo = recorder.sync_calls[1]["prompt"]
    assert bad.text in redo
    assert f"- {problem}" in redo
    assert COMPLETED_FR["span"] not in redo
    assert [kind for kind, _ in events] == ["merge_reasked", "merge_folded", "merge_decided"]
    assert events[0][1] == {"story_slug": story.story_slug, "problems": [problem]}

    still_bad = _answer("same", "b1", [_claim_verdict("c01", "conflict", "b1.c02", "1960", "")])
    mock = _armed_judge([bad, still_bad], claims)
    events, sink = _sink_and_events()
    with pytest.raises(narrate.BeatHeld) as held:
        merge.merge(story, claims, existing, mock, events=sink)
    assert held.value.phase == "P6"
    assert held.value.reason == "answer: claim c01: a conflict must quote both values"
    assert mock.calls == [("merge_judge", "P6"), ("merge_judge", "P6")]
    assert [kind for kind, _ in events] == ["merge_reasked", "beat_held"]


def test_every_merge_judge_answer_is_logged_raw():
    """Slice 10: job 2 held or rewrote every match the judge found and no
    raw P6 answer reached the job log, so a false `new` could not be told
    apart from a prompt, id or model cause. Every answer the merge judge
    gives — first ask and re-ask, usable or not — is emitted verbatim as
    `merge_answered` the moment it arrives, before anything judges it."""
    unit = _fr_unit()
    claims = [_judged(unit, "c01", COMPLETED_FR)]
    story = _story("The 1959 building", ["c01"])
    existing = _existing()
    bad = _answer("same", "b1", [_claim_verdict("c01", "same", "b1.c09", "1959", "1959")])
    good = _answer("same", "b1", [_claim_verdict("c01", "same", "b1.c02", "1959", "1959")])
    events, sink = _sink_and_events(answers=True)

    merge.merge(story, claims, existing, _armed_judge([bad, good], claims), events=sink)

    answered = [payload for kind, payload in events if kind == "merge_answered"]
    assert answered == [
        {"story_slug": story.story_slug, "attempt": 1, "model": RESPONSE_MERGE_MODEL,
         "answer": bad.text},
        {"story_slug": story.story_slug, "attempt": 2, "model": RESPONSE_MERGE_MODEL,
         "answer": good.text},
    ]

    unreadable = llm.MockAnswer(text="the same, I think", model_id=RESPONSE_MERGE_MODEL)
    events, sink = _sink_and_events(answers=True)
    with pytest.raises(narrate.BeatHeld):
        merge.merge(story, claims, existing, _armed_judge([unreadable], claims), events=sink)
    assert events[0] == (
        "merge_answered",
        {"story_slug": story.story_slug, "attempt": 1, "model": RESPONSE_MERGE_MODEL,
         "answer": "the same, I think"},
    )


def test_the_judge_names_beats_and_claims_by_handle():
    """Slice 10: the judge retyped `new-york/` for `new_york/` in a beat id
    (job 2's Met story), and two beats at one place both hold `c01`, so a
    bare claim id cannot say which beat's claim it means. The prompt shows
    each candidate beat as `b1`, `b2`, ... and each of its claims as
    `b1.c01` — never a beat id — and the answer names them the same way;
    the outcome carries the real ids back. A handle the place does not
    have is re-asked once, like any unknown id."""
    unit = _fr_unit()
    claims = [_judged(unit, "c01", COMPLETED_FR)]
    story = _story("The 1959 building", ["c01"])
    existing = _existing()
    bad = _answer("same", "b3", [_claim_verdict("c01", "same", "b1.c02", "1959", "1959")])
    good = _answer("same", "b1", [_claim_verdict("c01", "same", "b1.c02", "1959", "1959")])
    recorder = _RecordingClient(_armed_judge([bad, good], claims))
    events, sink = _sink_and_events()

    outcome = merge.merge(story, claims, existing, recorder, events=sink)

    prompt = recorder.sync_calls[0]["prompt"]
    assert 'beat b1 "How the museum came to be":' in prompt
    assert 'beat b2 "Visiting the Guggenheim":' in prompt
    assert f"- b1.c02 [event, resolved, 2023]: {COMPLETED['text']}" in prompt
    assert f"- b2.c01 [state, resolved, 2023]: {ADDRESS['text']}" in prompt
    assert ORIGINS_ID not in prompt and VISITING_ID not in prompt
    assert events[0] == (
        "merge_reasked",
        {"story_slug": story.story_slug, "problems": ["beat_id 'b3' is not a beat at this place"]},
    )
    assert outcome.held is False
    assert outcome.beat_id == ORIGINS_ID
    assert _decided(outcome) == [("c01", "same", "c02", "c02")]


BUILDING_ID = "new_york/guggenheim-museum/the-building-that-upstages-its-art"
# Job 2's real pair at the Guggenheim: LP's c01 and Frommer's c141.
WRIGHT: dict = {
    "text": "The Guggenheim Museum building was designed by architect Frank Lloyd Wright.",
    "kind": "state",
    "span": (
        "this building by architect Frank Lloyd Wright almost overshadows the "
        "collection of 20th-century art inside"
    ),
}
OVERSHADOWS: dict = {
    "text": (
        "The Guggenheim Museum building almost overshadows the collection of "
        "20th-century art inside it."
    ),
    "kind": "state",
    "span": "almost overshadows the collection of 20th-century art inside",
}
SPIRAL_FR: dict = {
    "text": (
        "The Guggenheim is a spiral-shaped building designed by Frank Lloyd Wright "
        "that stands amid the tall buildings lining Fifth Avenue."
    ),
    "kind": "state",
    "span": (
        "Until you get to the Guggenheim, that is. Frank Lloyd Wright\u2019s delirious "
        "spiral of a museum sits among the towers of Fifth Avenue"
    ),
}


def _existing_with_building() -> list[model.Beat]:
    """`_existing()` plus a third LP beat whose claim ids are ALSO `c01`, `c02`."""
    building = _beat(
        BUILDING_ID,
        "the-building-that-upstages-its-art",
        "The building that upstages its art",
        "establishing",
        [_lp_claim("c01", WRIGHT), _lp_claim("c02", OVERSHADOWS)],
        "Frank Lloyd Wright designed the Guggenheim Museum building, and it almost "
        "overshadows the art inside.",
    )
    assert model.validate([building]) == []
    return [*_existing(), model.Beat.model_validate(building)]


def test_a_new_storys_matched_claim_folds_into_the_beat_that_holds_it(tmp_path):
    """Slice 10, the merge trio's first defect: job 2's judge was only
    allowed a claim match inside the ONE beat the whole story matched, so a
    new story sharing a fact with a differently cut existing story came out
    all-`new` — the Guggenheim ended up voicing "designed by Frank Lloyd
    Wright" twice. Now each claim may match any claim at the place, whatever
    the story verdict: the story is `new` (its own beat, assembled by the
    runner from its unmatched claims), while its Wright claim FOLDS into the
    third beat's `c01` — one claim, both sources, corroborated — and the
    visiting beat's own `c01` is untouched. Every fold is logged with both
    texts and the new span so a wrong `same` can be audited."""
    unit = _fr_unit()
    claims = [_judged(unit, "c01", SPIRAL_FR), _judged(unit, "c02", MUSCHAMP)]
    story = _story("A spiral among the boxes", ["c01", "c02"])
    existing = _existing_with_building()
    answer = _answer(
        "new",
        "",
        [
            _claim_verdict("c01", "same", "b3.c01", "Frank Lloyd Wright", "Frank Lloyd Wright"),
            _claim_verdict("c02", "new", reason="no claim mentions Muschamp"),
        ],
    )
    recorder = _RecordingClient(_armed_judge([answer], claims))
    events, sink = _sink_and_events()

    outcome = merge.merge(story, claims, existing, recorder, events=sink)

    assert len(recorder.sync_calls) == 1  # no re-ask
    assert "a claim of ANY beat at this place" in recorder.sync_calls[0]["prompt"]
    assert "even when the story is new" in recorder.sync_calls[0]["prompt"]
    assert outcome.held is False
    assert outcome.story == "new" and outcome.beat_id is None
    assert [
        (c.claim_id, c.outcome, c.existing_beat_id, c.existing_claim_id, c.applied_claim_id)
        for c in outcome.claims
    ] == [("c01", "same", BUILDING_ID, "c01", "c01"), ("c02", "new", None, None, "c02")]
    assert outcome.rerun == []  # a corroboration changes no resolved text
    assert ("merge_folded", {
        "story_slug": story.story_slug,
        "claim_id": "c01",
        "text": SPIRAL_FR["text"],
        "span": SPIRAL_FR["span"],
        "outcome": "same",
        "existing_beat_id": BUILDING_ID,
        "existing_claim_id": "c01",
        "existing_text": WRIGHT["text"],
    }) in events

    merged = merge.apply(outcome, existing)

    assert merged[:2] == existing[:2]  # b2.c01 (the address) is not the c01 meant
    wright = merged[2].claims[0]
    assert [(s.source_id, s.span, s.stated_value) for s in wright.sources] == [
        (LP_SOURCE, WRIGHT["span"], None),
        (FR_SOURCE, SPIRAL_FR["span"], "Frank Lloyd Wright"),
    ]
    assert wright.resolution == model.Resolution(by="corroborated")
    assert model.validate(_records(merged), chunks_root=_chunks_root(tmp_path)) == []


def _fr_claim(claim_id: str, item: dict) -> dict:
    """A resolved, single-source Frommer's (2024) claim as it sits on disk."""
    claim = _lp_claim(claim_id, item)
    claim["sources"] = [_source(item, FR_SOURCE, FR_CHUNK, 2024)]
    return claim


def _lp_unit() -> unit_mod.Unit:
    return unit_mod.load_unit(
        BOOKS_ROOT,
        city="new_york",
        source_id=LP_SOURCE,
        chunk=LP_CHUNK,
        as_of=2023,
        rights_basis="owned_copy",
    )


def test_an_older_source_arriving_second_is_appended_as_a_superseded_belief(tmp_path):
    """The mirror of supersession: the corpus already holds the 2024 hours
    and the 2023 book arrives second. The newer as_of still wins (D9): the
    existing claim is untouched and the new claim joins the beat as a dated
    belief, status superseded, under a fresh id, carrying its own verdict.
    The resolved texts did not change, so nothing is marked for a rerun and
    the file validates clean."""
    record = _beat(
        VISITING_ID,
        "visiting-the-guggenheim",
        "Visiting the Guggenheim",
        "stop_orientation",
        [_fr_claim("c01", HOURS_2024)],
        "On Saturday evenings from six to eight you pay what you wish.",
    )
    assert model.validate([record]) == []
    existing = [model.Beat.model_validate(record)]
    claims = [_judged(_lp_unit(), "c01", HOURS_2023)]
    story = _story("Saturday hours", ["c01"], beat_type="stop_orientation")
    answer = _answer(
        "same", "b1", [_claim_verdict("c01", "conflict", "b1.c01", "5pm to 8pm", "6pm to 8pm")]
    )

    outcome = merge.merge(story, claims, existing, _armed_judge([answer], claims))

    assert outcome.story == "same"
    assert _decided(outcome) == [("c01", "superseded", "c01", "c02")]
    assert outcome.rerun == []

    merged = merge.apply(outcome, existing)

    beat = merged[0]
    assert [c.claim_id for c in beat.claims] == ["c01", "c02"]
    assert beat.claims[0] == existing[0].claims[0]
    added = beat.claims[1]
    assert added.text == HOURS_2023["text"]
    assert added.kind == "belief" and added.status == "superseded"
    assert [(s.source_id, s.span, s.as_of, s.stated_value) for s in added.sources] == [
        (LP_SOURCE, HOURS_2023["span"], 2023, "5pm to 8pm")
    ]
    assert added.verdict == claims[0].verdict
    assert model.validate(_records(merged), chunks_root=_chunks_root(tmp_path)) == []


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
