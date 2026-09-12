"""Tests for src/ingest/narrate.py and src/ingest/judge_narration.py —
Docs/ingestion/rebuild-spec.md slice 5 (P4 narrate, P5 judge narration).

Every test runs against llm.MockClient over the real Lonely Planet
chunk-07 unit; no live client, no network, no spend
(test_no_live_client_in_this_file, same walker as the slice-3/4 files).
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import src.ingest.judge_narration as judge_narration
import src.ingest.llm as llm
import src.ingest.narrate as narrate
from src.ingest import decompose, group, judge_claims, model
from src.ingest import unit as unit_mod

REPO_ROOT = Path(__file__).resolve().parent.parent
BOOKS_ROOT = REPO_ROOT / "Books" / "new_york"
LP_SOURCE = "lonely-planet-new-york-city"
LP_CHUNK = "chunk-07-upper-east-side"

#: Model ids as the RESPONSE reports them — distinct from the configured
#: ROLE_MODEL entries so a test can tell a stamp from a config copy.
RESPONSE_AUTHOR_MODEL = "claude-opus-5-20260601"
RESPONSE_JUDGE_MODEL = "claude-haiku-4-5-20251001"

#: Clean P1 claims verified against the real chunk text (the same pins
#: tests/test_ingest_judge.py uses).
ADDRESS_CLAIM: dict = {
    "text": (
        "The Solomon R. Guggenheim Museum stands at 1071 Fifth Avenue "
        "on the corner of East 89th Street."
    ),
    "kind": "state",
    "span": "1071 Fifth Ave, at E 89th St",
}
COMPLETED_CLAIM: dict = {
    "text": (
        "The Guggenheim building was finished in 1959, by which time "
        "both Frank Lloyd Wright and Solomon Guggenheim were dead."
    ),
    "kind": "event",
    "span": (
        "Construction was finally completed in 1959 – after both "  # noqa: RUF001
        "Wright and Guggenheim had passed away."
    ),
}

#: A clean narration over the two claims: thirty words, no span run of
#: eight, no book furniture, no framing verb.
CLEAN_NARRATION = (
    "Solomon Guggenheim's museum stands at 1071 Fifth Avenue, on the corner "
    "of East 89th Street. The building was finished in 1959, by which time "
    "both Wright and Guggenheim had died."
)


def _real_unit() -> unit_mod.Unit:
    return unit_mod.load_unit(
        BOOKS_ROOT,
        city="new_york",
        source_id=LP_SOURCE,
        chunk=LP_CHUNK,
        as_of=2023,
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
            judge_model=RESPONSE_JUDGE_MODEL,
            entailed=True,
            bound_to=model.bind(draft.text, draft.source.span),
        ),
    )


def _story(claim_ids: list[str]) -> group.Story:
    return group.Story(
        title="How the museum came to be",
        story_slug="how-the-museum-came-to-be",
        place="Guggenheim Museum",
        new_poi=False,
        lenses=["hidden_history"],
        beat_type="anecdote",
        enrichment=group.Enrichment(),
        claim_ids=claim_ids,
    )


def _sink_and_events():
    events: list[tuple[str, dict]] = []

    def sink(kind: str, payload: dict) -> None:
        events.append((kind, payload))

    return events, sink


def _authored(text: str) -> llm.MockAnswer:
    return llm.MockAnswer(text=json.dumps({"narration": text}), model_id=RESPONSE_AUTHOR_MODEL)


def _armed_author(answers: list[llm.MockAnswer], claims) -> llm.MockClient:
    """A MockClient scripted with the author's sync answers, its estimate
    gate armed against the claim texts and the real P4_PLAN."""
    _events, sink = _sink_and_events()
    mock = llm.MockClient(sink, answers={"author": answers})
    mock.estimate([c.draft.text for c in claims], list(narrate.P4_PLAN))
    return mock


class _RecordingClient:
    """Records every complete()/complete_batch() call's exact arguments."""

    def __init__(self, client) -> None:
        self._client = client
        self.sync_calls: list[dict] = []
        self.batch_calls: list[dict] = []

    def complete(self, role, prompt, schema, *, phase, max_tokens):
        self.sync_calls.append(
            {"role": role, "prompt": prompt, "schema": schema, "phase": phase,
             "max_tokens": max_tokens}
        )
        return self._client.complete(role, prompt, schema, phase=phase, max_tokens=max_tokens)

    def complete_batch(self, *, role, prompts, schema, phase, max_tokens):
        self.batch_calls.append(
            {"role": role, "prompts": list(prompts), "schema": schema, "phase": phase,
             "max_tokens": max_tokens}
        )
        return self._client.complete_batch(
            role=role, prompts=prompts, schema=schema, phase=phase, max_tokens=max_tokens
        )


def test_narration_never_sees_the_span():
    """The spec's proving node: the author writes from the resolved claim
    TEXTS alone. The P4 prompt carries every claim text and neither a
    span nor the unit; the draft's claims_hash is the hash of those texts
    in order, its author_model is what the RESPONSE reported, and
    duration_sec is the tour engine's clock (150 words a minute)."""
    import src.ingest.prompts as prompts

    unit = _real_unit()
    claims = [_judged(unit, "c01", ADDRESS_CLAIM), _judged(unit, "c02", COMPLETED_CLAIM)]
    recorder = _RecordingClient(_armed_author([_authored(CLEAN_NARRATION)], claims))

    draft = narrate.narrate(_story(["c01", "c02"]), claims, recorder)

    assert [(c["role"], c["phase"]) for c in recorder.sync_calls] == [("author", "P4")]
    assert recorder.batch_calls == []
    call = recorder.sync_calls[0]
    assert call["schema"] == prompts.P4_NARRATION_SCHEMA
    assert ADDRESS_CLAIM["text"] in call["prompt"]
    assert COMPLETED_CLAIM["text"] in call["prompt"]
    assert "Guggenheim Museum" in call["prompt"]
    assert ADDRESS_CLAIM["span"] not in call["prompt"]
    assert "passed away" not in call["prompt"]
    assert "Solomon R Guggenheim" not in call["prompt"]  # the unit's own opening line
    assert unit.text[:200] not in call["prompt"]

    assert isinstance(draft, narrate.NarrationDraft)
    assert draft.text == CLEAN_NARRATION
    # Independent literal: sha256 of the two texts '\n'-joined, the value
    # the slice-1 validator recomputes as NARRATION_HASH_STALE.
    expected_hash = hashlib.sha256(
        f"{ADDRESS_CLAIM['text']}\n{COMPLETED_CLAIM['text']}".encode()
    ).hexdigest()
    assert draft.claims_hash == expected_hash
    assert draft.author_model == RESPONSE_AUTHOR_MODEL
    assert draft.author_model != llm.ROLE_MODEL["author"]
    assert draft.duration_sec == 12  # thirty words at 150 wpm


LEAKING_NARRATION = (
    "The guide calls the museum the city's finest room. It stands at 1071 Fifth "
    "Avenue, on the corner of East 89th Street, and was finished in 1959, by "
    "which time both Wright and Guggenheim had died."
)


def test_leak_is_refused():
    """The spec's proving node: a narration that reveals it was read from
    a book is refused by the code gate, the author is re-asked ONCE with
    the leaked phrase and the refused text quoted back, and the clean
    rewrite is what comes out. A second leak holds the beat (`beat_held`,
    then narrate.BeatHeld with phase 'P4') with no third call."""
    import pytest

    unit = _real_unit()
    claims = [_judged(unit, "c01", ADDRESS_CLAIM), _judged(unit, "c02", COMPLETED_CLAIM)]
    story = _story(["c01", "c02"])
    recorder = _RecordingClient(
        _armed_author([_authored(LEAKING_NARRATION), _authored(CLEAN_NARRATION)], claims)
    )
    events, sink = _sink_and_events()

    draft = narrate.narrate(story, claims, recorder, events=sink)

    assert draft.text == CLEAN_NARRATION
    assert [(c["role"], c["phase"]) for c in recorder.sync_calls] == [
        ("author", "P4"),
        ("author", "P4"),
    ]
    redo_prompt = recorder.sync_calls[1]["prompt"]
    assert LEAKING_NARRATION in redo_prompt
    assert "The guide" in redo_prompt
    assert ADDRESS_CLAIM["span"] not in redo_prompt
    refused = [payload for kind, payload in events if kind == "narration_refused"]
    assert len(refused) == 1
    assert refused[0]["story_slug"] == story.story_slug
    assert refused[0]["attempt"] == 1
    assert refused[0]["reasons"] == [
        "provenance_leak: 'The guide' reveals the narration was read from a book"
    ]

    # Still leaking after the one re-ask: held, never a third call.
    still_leaking = LEAKING_NARRATION.replace("The guide calls", "The book calls")
    twice = _armed_author([_authored(LEAKING_NARRATION), _authored(still_leaking)], claims)
    events, sink = _sink_and_events()
    with pytest.raises(narrate.BeatHeld) as held:
        narrate.narrate(story, claims, twice, events=sink)
    assert held.value.phase == "P4"
    assert held.value.story_slug == story.story_slug
    assert "The book" in held.value.reason
    assert twice.calls == [("author", "P4"), ("author", "P4")]
    holds = [payload for kind, payload in events if kind == "beat_held"]
    assert len(holds) == 1
    assert holds[0] == {"story_slug": story.story_slug, "phase": "P4", "reason": held.value.reason}
    assert [kind for kind, _ in events if kind == "narration_refused"] == [
        "narration_refused",
        "narration_refused",
    ]


def test_narration_gates_name_lift_leak_and_framing_once_each():
    """The P4 code gate: no eight-word run with ANY claim span outside an
    attributed quotation, no provenance leak (publisher names included
    when passed), no framing verb — one reason per failing gate, in that
    order, never short-circuiting."""
    spans = [ADDRESS_CLAIM["span"], COMPLETED_CLAIM["span"]]
    lifted = (
        "Imagine it: construction was finally completed in 1959 after both Wright "
        "and Guggenheim had passed away, as Lonely Planet notes."
    )

    reasons = narrate.narration_gates(lifted, spans, publishers=("Lonely Planet",))

    assert [reason.split(":")[0] for reason in reasons] == ["lift", "provenance_leak", "framing"]
    assert "completed in 1959 after both wright and guggenheim had passed away" in reasons[0]
    assert "Lonely Planet" in reasons[1]
    assert "Imagine" in reasons[2]
    assert narrate.narration_gates(CLEAN_NARRATION, spans, publishers=("Lonely Planet",)) == []
    # The publisher is only a leak when the caller names it.
    assert narrate.narration_gates(lifted.replace("Imagine it: ", "").replace(
        "Construction", "construction"), spans)[1:] == []


CLEAN_SENTENCES = [
    "Solomon Guggenheim's museum stands at 1071 Fifth Avenue, on the corner of East 89th Street.",
    "The building was finished in 1959, by which time both Wright and Guggenheim had died.",
]


def _draft(text: str, claims) -> narrate.NarrationDraft:
    return narrate.NarrationDraft(
        text=text,
        claims_hash=model.claims_hash(
            [{"text": c.draft.text, "status": "resolved"} for c in claims]
        ),
        author_model=RESPONSE_AUTHOR_MODEL,
        duration_sec=narrate.duration_sec(text),
    )


def _sentence_verdict(entailed: bool, reason: str) -> llm.MockAnswer:
    return llm.MockAnswer(
        text=json.dumps({"entailed": entailed, "reason": reason}), model_id=RESPONSE_JUDGE_MODEL
    )


def _armed_judge(
    batch_answers: dict, author_answers: list[llm.MockAnswer], sentences: list[str]
) -> llm.MockClient:
    """A MockClient scripted with the judge's batch answers and the
    author's sync answers, armed against the sentences and the real P5_PLAN."""
    _events, sink = _sink_and_events()
    mock = llm.MockClient(sink, answers={"author": author_answers}, batch_answers=batch_answers)
    mock.estimate(sentences, list(judge_narration.P5_PLAN))
    return mock


def _beat_record(claims, judged: judge_narration.JudgedNarration) -> dict:
    """The §2 record a job would write from P3's claims and P5's narration."""
    return {
        "beat_id": "new_york/guggenheim-museum/how-the-museum-came-to-be",
        "city_name": "new_york",
        "poi_name": "Guggenheim Museum",
        "story_slug": "how-the-museum-came-to-be",
        "title": "How the museum came to be",
        "beat_type": "anecdote",
        "lenses": ["hidden_history"],
        "claims": [
            {
                "claim_id": c.draft.claim_id,
                "text": c.draft.text,
                "kind": c.draft.kind,
                "status": "resolved",
                "sources": [c.draft.source.model_dump()],
                "verdict": c.verdict.model_dump(),
            }
            for c in claims
        ],
        "narration": judged.narration.model_dump(),
        "duration_sec": judged.duration_sec,
        "review": judged.review.model_dump(),
    }


def test_every_sentence_is_judged_and_the_verdict_is_bound():
    """P5 judges every sentence of the narration in one batch under the
    narration judge (P5 is a batch phase), each prompt carrying that one
    sentence and the claim texts — never a span. The verdict counts the
    entailed sentences, is bound by SHA-256 to exactly the text judged,
    and carries the model id the RESPONSE reported. The beat a job would
    assemble from P3's claims and this narration passes the slice-1
    validator, span grounding included."""
    unit = _real_unit()
    claims = [_judged(unit, "c01", ADDRESS_CLAIM), _judged(unit, "c02", COMPLETED_CLAIM)]
    story = _story(["c01", "c02"])
    draft = _draft(CLEAN_NARRATION, claims)
    assert judge_narration.sentences(CLEAN_NARRATION) == CLEAN_SENTENCES
    mock = _armed_judge(
        {
            judge_narration.sentence_custom_id(draft, 0, 1): _sentence_verdict(True, "c01"),
            judge_narration.sentence_custom_id(draft, 1, 1): _sentence_verdict(True, "c02"),
        },
        [],
        CLEAN_SENTENCES,
    )
    recorder = _RecordingClient(mock)

    judged = judge_narration.judge_narration(story, draft, claims, recorder)

    assert [(c["role"], c["phase"]) for c in recorder.batch_calls] == [("narration_judge", "P5")]
    assert recorder.sync_calls == []
    prompts_sent = recorder.batch_calls[0]["prompts"]
    assert [custom_id for custom_id, _ in prompts_sent] == [
        judge_narration.sentence_custom_id(draft, 0, 1),
        judge_narration.sentence_custom_id(draft, 1, 1),
    ]
    for (_, prompt), sentence in zip(prompts_sent, CLEAN_SENTENCES, strict=True):
        assert sentence in prompt
        assert ADDRESS_CLAIM["text"] in prompt
        assert COMPLETED_CLAIM["text"] in prompt
        assert ADDRESS_CLAIM["span"] not in prompt
        assert "passed away" not in prompt

    assert isinstance(judged, judge_narration.JudgedNarration)
    narration = judged.narration
    assert isinstance(narration, model.Narration)
    assert narration.text == CLEAN_NARRATION
    assert narration.claims_hash == draft.claims_hash
    assert narration.author_model == RESPONSE_AUTHOR_MODEL
    assert narration.flags == []
    verdict = narration.verdict
    assert (verdict.sentences_entailed, verdict.sentences_total) == (2, 2)
    assert verdict.judge_model == RESPONSE_JUDGE_MODEL
    # Independent literal: sha256 of the text alone, the binding the
    # slice-1 validator recomputes for the narration (bind with no span).
    assert verdict.bound_to == hashlib.sha256(CLEAN_NARRATION.encode()).hexdigest()
    assert judged.duration_sec == 12
    assert judged.review == model.Review(held=False, reason=None)

    assert model.validate([_beat_record(claims, judged)], chunks_root=BOOKS_ROOT) == []


#: The same story with one fact no claim states (a fifty-cent ticket).
EMBELLISHED_NARRATION = (
    "Solomon Guggenheim's museum stands at 1071 Fifth Avenue, on the corner of "
    "East 89th Street. The building was finished in 1959, when a ticket cost "
    "fifty cents."
)
EMBELLISHED_SECOND = "The building was finished in 1959, when a ticket cost fifty cents."


def test_failing_sentence_reasks_p4_once_with_the_sentence_quoted_back():
    """P5's one re-ask: the judge refuses a sentence, the AUTHOR is asked
    once (a sync P4 call) to rewrite the narration with that sentence and
    the judge's reason quoted back — still from the claim texts, never a
    span — the rewrite passes the P4 code gate, and every sentence of it
    is judged again; the round-two verdict is what comes out, bound to
    the NEW text."""
    unit = _real_unit()
    claims = [_judged(unit, "c01", ADDRESS_CLAIM), _judged(unit, "c02", COMPLETED_CLAIM)]
    story = _story(["c01", "c02"])
    draft = _draft(EMBELLISHED_NARRATION, claims)
    reason = "no claim states a ticket price"
    mock = _armed_judge(
        {
            judge_narration.sentence_custom_id(draft, 0, 1): _sentence_verdict(True, "c01"),
            judge_narration.sentence_custom_id(draft, 1, 1): _sentence_verdict(False, reason),
            judge_narration.sentence_custom_id(draft, 0, 2): _sentence_verdict(True, "c01"),
            judge_narration.sentence_custom_id(draft, 1, 2): _sentence_verdict(True, "c02"),
        },
        [_authored(CLEAN_NARRATION)],
        judge_narration.sentences(EMBELLISHED_NARRATION),
    )
    recorder = _RecordingClient(mock)
    events, sink = _sink_and_events()

    judged = judge_narration.judge_narration(story, draft, claims, recorder, events=sink)

    assert mock.calls == [
        ("narration_judge", "P5"),
        ("narration_judge", "P5"),
        ("author", "P4"),
        ("narration_judge", "P5"),
        ("narration_judge", "P5"),
    ]
    revise_prompt = recorder.sync_calls[0]["prompt"]
    assert EMBELLISHED_SECOND in revise_prompt
    assert reason in revise_prompt
    assert EMBELLISHED_NARRATION in revise_prompt
    assert ADDRESS_CLAIM["text"] in revise_prompt
    assert ADDRESS_CLAIM["span"] not in revise_prompt
    assert "passed away" not in revise_prompt
    assert [(c["role"], c["phase"]) for c in recorder.batch_calls] == [
        ("narration_judge", "P5"),
        ("narration_judge", "P5"),
    ]
    round_two = [custom_id for custom_id, _ in recorder.batch_calls[1]["prompts"]]
    assert round_two == [
        judge_narration.sentence_custom_id(draft, 0, 2),
        judge_narration.sentence_custom_id(draft, 1, 2),
    ]
    assert (
        "sentence_refused",
        {"story_slug": story.story_slug, "attempt": 1, "sentence": EMBELLISHED_SECOND,
         "reason": reason},
    ) in events

    narration = judged.narration
    assert narration.text == CLEAN_NARRATION
    assert (narration.verdict.sentences_entailed, narration.verdict.sentences_total) == (2, 2)
    assert narration.verdict.bound_to == model.bind(CLEAN_NARRATION)
    assert narration.author_model == RESPONSE_AUTHOR_MODEL
    assert narration.flags == []
    assert judged.duration_sec == 12
    assert judged.review.held is False


STILL_EMBELLISHED = (
    "Solomon Guggenheim's museum stands at 1071 Fifth Avenue, on the corner of "
    "East 89th Street. The building was finished in 1959, and its first ticket "
    "cost half a dollar."
)
STILL_EMBELLISHED_SECOND = (
    "The building was finished in 1959, and its first ticket cost half a dollar."
)


def test_second_failure_holds_the_beat_not_the_claims():
    """The spec's proving node: a sentence still refused after the one
    P4 re-ask sets `narration.flags` and `review.held`, with the judge's
    second reason. The claims are never touched — no P3 or P1 call, the
    same JudgedClaim objects, the same claims_hash — and no third
    narration call happens. The held narration is the rewrite, judged,
    with its verdict bound to that text."""
    unit = _real_unit()
    claims = [_judged(unit, "c01", ADDRESS_CLAIM), _judged(unit, "c02", COMPLETED_CLAIM)]
    before = [claim.model_copy(deep=True) for claim in claims]
    story = _story(["c01", "c02"])
    draft = _draft(EMBELLISHED_NARRATION, claims)
    first_reason = "no claim states a ticket price"
    second_reason = "no claim states what a ticket cost"
    mock = _armed_judge(
        {
            judge_narration.sentence_custom_id(draft, 0, 1): _sentence_verdict(True, "c01"),
            judge_narration.sentence_custom_id(draft, 1, 1): _sentence_verdict(
                False, first_reason
            ),
            judge_narration.sentence_custom_id(draft, 0, 2): _sentence_verdict(True, "c01"),
            judge_narration.sentence_custom_id(draft, 1, 2): _sentence_verdict(
                False, second_reason
            ),
        },
        [_authored(STILL_EMBELLISHED)],
        judge_narration.sentences(EMBELLISHED_NARRATION),
    )
    events, sink = _sink_and_events()

    judged = judge_narration.judge_narration(story, draft, claims, mock, events=sink)

    assert mock.calls == [
        ("narration_judge", "P5"),
        ("narration_judge", "P5"),
        ("author", "P4"),
        ("narration_judge", "P5"),
        ("narration_judge", "P5"),
    ]
    assert claims == before
    assert not any(kind in ("claim_refused", "claim_dropped") for kind, _ in events)
    assert [payload for kind, payload in events if kind == "sentence_refused"] == [
        {"story_slug": story.story_slug, "attempt": 1, "sentence": EMBELLISHED_SECOND,
         "reason": first_reason},
        {"story_slug": story.story_slug, "attempt": 2, "sentence": STILL_EMBELLISHED_SECOND,
         "reason": second_reason},
    ]
    held = [payload for kind, payload in events if kind == "beat_held"]
    assert len(held) == 1
    assert held[0]["story_slug"] == story.story_slug
    assert held[0]["phase"] == "P5"

    narration = judged.narration
    assert narration.text == STILL_EMBELLISHED
    assert narration.claims_hash == draft.claims_hash
    assert (narration.verdict.sentences_entailed, narration.verdict.sentences_total) == (1, 2)
    assert narration.verdict.bound_to == model.bind(STILL_EMBELLISHED)
    assert narration.flags == [f"not_entailed: {STILL_EMBELLISHED_SECOND!r}: {second_reason}"]
    assert judged.review.held is True
    assert judged.review.reason == narration.flags[0]
    assert held[0]["reason"] == judged.review.reason
    # A held beat is still a VALID record — the review queue, not the
    # validator, is what keeps it off the graph.
    assert model.validate([_beat_record(claims, judged)], chunks_root=BOOKS_ROOT) == []


def test_rewrite_failing_the_code_gate_holds_with_the_first_narration_judged():
    """A P5 rewrite that trips the P4 code gate (here: a provenance leak)
    is never judged — the gates stand between the author and the judge on
    the re-ask exactly as they do on the first ask. The beat is held with
    a `gate` flag beside the round-one `not_entailed` flag, and the
    narration that comes out is the round-one text with its round-one
    verdict, the only text a judge actually saw."""
    unit = _real_unit()
    claims = [_judged(unit, "c01", ADDRESS_CLAIM), _judged(unit, "c02", COMPLETED_CLAIM)]
    story = _story(["c01", "c02"])
    draft = _draft(EMBELLISHED_NARRATION, claims)
    reason = "no claim states a ticket price"
    mock = _armed_judge(
        {
            judge_narration.sentence_custom_id(draft, 0, 1): _sentence_verdict(True, "c01"),
            judge_narration.sentence_custom_id(draft, 1, 1): _sentence_verdict(False, reason),
            # No round-2 answers scripted: reaching them would raise
            # MockScriptExhausted, so the pass proves the judge was not asked.
        },
        [_authored(LEAKING_NARRATION)],
        judge_narration.sentences(EMBELLISHED_NARRATION),
    )
    events, sink = _sink_and_events()

    judged = judge_narration.judge_narration(story, draft, claims, mock, events=sink)

    assert mock.calls == [("narration_judge", "P5"), ("narration_judge", "P5"), ("author", "P4")]
    narration = judged.narration
    assert narration.text == EMBELLISHED_NARRATION
    assert (narration.verdict.sentences_entailed, narration.verdict.sentences_total) == (1, 2)
    assert narration.verdict.bound_to == model.bind(EMBELLISHED_NARRATION)
    assert narration.flags == [
        f"not_entailed: {EMBELLISHED_SECOND!r}: {reason}",
        "gate: provenance_leak: 'The guide' reveals the narration was read from a book",
    ]
    assert judged.review.held is True
    assert judged.review.reason == "; ".join(narration.flags)
    refused = [payload for kind, payload in events if kind == "narration_refused"]
    assert refused == [
        {"story_slug": story.story_slug, "attempt": 2, "reasons": [narration.flags[1][6:]]}
    ]
    held = [payload for kind, payload in events if kind == "beat_held"]
    assert held == [{"story_slug": story.story_slug, "phase": "P5",
                     "reason": judged.review.reason}]


def test_transport_failure_or_unreadable_answer_holds_the_beat():
    """The same failure contract as P3: a truncated or empty completion,
    a batch failure, or an unreadable answer holds the beat (`beat_held`,
    then narrate.BeatHeld with the phase in progress) with no further
    call; a programming error (unarmed estimate gate, missing script)
    propagates as itself, never wrapped as a hold."""
    import pytest

    unit = _real_unit()
    claims = [_judged(unit, "c01", ADDRESS_CLAIM), _judged(unit, "c02", COMPLETED_CLAIM)]
    story = _story(["c01", "c02"])

    # P4: narrate()'s own asks.
    p4_scripts = {
        "truncated": [
            llm.MockAnswer(text='{"narration": "Solo', model_id=RESPONSE_AUTHOR_MODEL,
                           stop_reason="max_tokens")
        ],
        "empty": [llm.MockAnswer(text="   ", model_id=RESPONSE_AUTHOR_MODEL)],
        "unreadable": [llm.MockAnswer(text="Here you go: a lovely story.",
                                      model_id=RESPONSE_AUTHOR_MODEL)],
        "unreadable_redo": [_authored(LEAKING_NARRATION),
                            llm.MockAnswer(text='{"text": "..."}', model_id=RESPONSE_AUTHOR_MODEL)],
    }
    for label, answers in p4_scripts.items():
        events, sink = _sink_and_events()
        mock = _armed_author(answers, claims)
        with pytest.raises(narrate.BeatHeld) as held:
            narrate.narrate(story, claims, mock, events=sink)
        assert held.value.phase == "P4", label
        assert held.value.story_slug == story.story_slug, label
        holds = [payload for kind, payload in events if kind == "beat_held"]
        assert holds == [{"story_slug": story.story_slug, "phase": "P4",
                          "reason": held.value.reason}], label
        assert len(mock.calls) == len(answers), label

    _events, sink = _sink_and_events()
    unarmed = llm.MockClient(sink, answers={"author": [_authored(CLEAN_NARRATION)]})
    with pytest.raises(llm.EstimateNotPrinted):
        narrate.narrate(story, claims, unarmed)
    assert unarmed.calls == []
    with pytest.raises(llm.MockScriptExhausted):
        narrate.narrate(story, claims, _armed_author([], claims))

    # P5: the judge round and the re-ask.
    draft = _draft(EMBELLISHED_NARRATION, claims)
    s0, s1 = (judge_narration.sentence_custom_id(draft, i, 1) for i in (0, 1))
    p5_scripts = {
        "batch_failure": ({s0: _sentence_verdict(True, "c01"),
                           s1: llm.MockFailure(result_type="expired")}, []),
        "truncated": ({s0: llm.MockAnswer(text='{"entailed": tr', model_id=RESPONSE_JUDGE_MODEL,
                                           stop_reason="max_tokens"),
                       s1: _sentence_verdict(True, "c02")}, []),
        "unreadable_verdict": ({s0: llm.MockAnswer(text='{"verdict": "yes"}',
                                                   model_id=RESPONSE_JUDGE_MODEL),
                                s1: _sentence_verdict(True, "c02")}, []),
        "unreadable_revise": ({s0: _sentence_verdict(True, "c01"),
                               s1: _sentence_verdict(False, "no claim states a price")},
                              [llm.MockAnswer(text="Sure!", model_id=RESPONSE_AUTHOR_MODEL)]),
        "truncated_revise": ({s0: _sentence_verdict(True, "c01"),
                              s1: _sentence_verdict(False, "no claim states a price")},
                             [llm.MockAnswer(text='{"narration": "So', stop_reason="max_tokens",
                                             model_id=RESPONSE_AUTHOR_MODEL)]),
    }
    for label, (batch_answers, author_answers) in p5_scripts.items():
        events, sink = _sink_and_events()
        mock = _armed_judge(batch_answers, author_answers,
                            judge_narration.sentences(EMBELLISHED_NARRATION))
        with pytest.raises(narrate.BeatHeld) as held:
            judge_narration.judge_narration(story, draft, claims, mock, events=sink)
        assert held.value.phase == "P5", label
        assert held.value.story_slug == story.story_slug, label
        holds = [payload for kind, payload in events if kind == "beat_held"]
        assert holds == [{"story_slug": story.story_slug, "phase": "P5",
                          "reason": held.value.reason}], label
        assert "transport" in held.value.reason or "schema" in held.value.reason, label

    _events, sink = _sink_and_events()
    unarmed = llm.MockClient(sink, batch_answers={s0: _sentence_verdict(True, "c01"),
                                                  s1: _sentence_verdict(True, "c02")})
    with pytest.raises(llm.EstimateNotPrinted):
        judge_narration.judge_narration(story, draft, claims, unarmed)
    assert unarmed.calls == []
    with pytest.raises(llm.MockScriptExhausted):
        judge_narration.judge_narration(
            story, draft, claims, _armed_judge({}, [], judge_narration.sentences(draft.text))
        )


def test_p4_and_p5_plans_price_the_real_prompts():
    """P4_PLAN (per story: the ask and the one redo, a ceiling) and P5_PLAN
    (per sentence: judge, the author's revise ceiling, re-judge ceiling)
    carry the real prompt lengths, never a made-up overhead number; P4
    rows price as sync calls, P5 rows as batch."""
    import src.ingest.prompts as prompts

    p4 = list(narrate.P4_PLAN)
    assert [(r.phase, r.role) for r in p4] == [("P4", "author"), ("P4", "author")]
    assert p4[0].overhead_tokens == max(1, len(prompts.NARRATE_PROMPT) // 4)
    assert p4[1].overhead_tokens == max(1, len(prompts.NARRATE_REDO_PROMPT) // 4)

    p5 = list(judge_narration.P5_PLAN)
    assert [(r.phase, r.role) for r in p5] == [
        ("P5", "narration_judge"),
        ("P4", "author"),
        ("P5", "narration_judge"),
    ]
    assert p5[0].overhead_tokens == max(1, len(prompts.JUDGE_SENTENCE_PROMPT) // 4)
    assert p5[1].overhead_tokens == max(1, len(prompts.NARRATE_REVISE_PROMPT) // 4)
    assert p5[2].overhead_tokens == p5[0].overhead_tokens
    assert all(r.calls_per_unit == 1 for r in [*p4, *p5])

    _events, sink = _sink_and_events()
    estimate = llm.MockClient(sink).estimate(["a sentence"], [*p4, *p5])
    assert [r.model_id for r in estimate.rows] == [
        "claude-opus-5",
        "claude-opus-5",
        "claude-haiku-4-5",
        "claude-opus-5",
        "claude-haiku-4-5",
    ]
    assert [r.batch for r in estimate.rows] == [False, False, True, False, True]


def test_publishers_come_from_the_manifest():
    """The leak gate's publisher names are read from a source's
    manifest: the `publisher` field, with a parenthesised imprint split
    out; a manifest without one yields no names (and the fixed phrases
    still guard the narration)."""
    assert narrate.publishers_from_manifest({"publisher": "Rough Guides (Penguin)"}) == [
        "Rough Guides",
        "Penguin",
    ]
    assert narrate.publishers_from_manifest({"publisher": "St. Martin's Griffin"}) == [
        "St. Martin's Griffin"
    ]
    assert narrate.publishers_from_manifest({"book_title": "Lonely Planet New York City"}) == []


def test_sentences_split_on_terminal_marks_and_keep_abbreviations():
    """P5 judges one spoken sentence at a time: a split after . ! ? (a
    closing quote or bracket may follow), never inside an abbreviation
    or a quotation, and never on a lowercase continuation."""
    text = (
        "Wright called it \u201ca temple of the spirit.\u201d St. Patrick's stands "
        "nearby! Was it finished? By 1959 (the year he died) it was. no. 6 is next."
    )
    assert judge_narration.sentences(text) == [
        "Wright called it \u201ca temple of the spirit.\u201d",
        "St. Patrick's stands nearby!",
        "Was it finished?",
        "By 1959 (the year he died) it was. no. 6 is next.",
    ]
    assert judge_narration.sentences("   ") == []


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


def test_every_book_manifest_names_its_publisher():
    """Owner ruling 2026-09-12: every Books/*/*/manifest.json carries a
    non-empty `publisher`, so the provenance-leak gate can catch "Lonely
    Planet calls it" for every source, not only the fixed phrases. Parsing
    each file also proves the manifests are still valid JSON."""
    manifests = sorted((REPO_ROOT / "Books").glob("*/*/manifest.json"))
    assert len(manifests) >= 15
    for path in manifests:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(manifest.get("publisher"), str) and manifest["publisher"].strip(), path
        assert narrate.publishers_from_manifest(manifest), path
