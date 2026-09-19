"""Tests for src/ingest/group.py — specs/2026-09-10-ingest-slice-3, step 13.

Step 13 (this step) lands the types `group()` will run on: AC-41
(`parse_stories` turns a raw P2 answer into its list of story dicts, or
`None` for anything unreadable — a single invalid item makes the WHOLE
answer unreadable, never a partial list of only the good ones), AC-42
(`Enrichment` maps the schema's forced-string sentinels — `''` and
`kid_friendly`'s `'unknown'` — back to `None`, with defaults identical to
`model.Beat`'s own enrichment fields; `Story` is frozen, carries a
`new_poi: bool` flag, and its `lenses` field is refused the same way
`model.Beat.lenses` is), and AC-43 (`P2_PLAN`'s two rows price the real
`GROUP_PROMPT_TEMPLATE`/`GROUP_REDO_TEMPLATE` lengths, never a made-up
overhead number, and — because `P2` is not in `llm.BATCH_PHASES` — price
as sync calls, unlike `P1_PLAN`'s batch rows).

AC-55 (test_ingest_group is one of the five ingest test files with a
self-contained test_no_live_client_in_this_file).

This step writes no test against `group()` itself (that lands in step
14) — only the standalone types above, plus their own $0-spend pricing
arithmetic through a MockClient.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import src.ingest.decompose as decompose
import src.ingest.group as group
import src.ingest.llm as llm
import src.ingest.prompts as prompts
from src.ingest import gates, model

REPO_ROOT = Path(__file__).resolve().parent.parent
BOOKS_ROOT = REPO_ROOT / "Books" / "new_york"
LP_SOURCE = "lonely-planet-new-york-city"
LP_CHUNK = "chunk-07-upper-east-side"


def _sink_and_events():
    """A recording EventSink: events.append((kind, payload)) per call."""
    events: list[tuple[str, dict]] = []

    def sink(kind: str, payload: dict) -> None:
        events.append((kind, payload))

    return events, sink


def _real_unit():
    from src.ingest import unit as unit_mod

    return unit_mod.load_unit(
        BOOKS_ROOT,
        city="new_york",
        source_id=LP_SOURCE,
        chunk=LP_CHUNK,
        as_of=2023,
        rights_basis="owned_copy",
    )


#: A schema-clean enrichment dict — every one of the ten required keys
#: present, using the sentinels the LLM sends for "nothing to say"
#: ('' for optional strings, 'unknown' for kid_friendly).
_CLEAN_ENRICHMENT: dict = {
    "physical_cues": [],
    "entities": [],
    "narrative_function": "",
    "emotional_register": "",
    "sensory_anchor": False,
    "inline_foreign_phrases": [],
    "pronunciation": "",
    "kid_friendly": "unknown",
    "sub_location": "",
    "trigger_address": "",
}

#: A schema-clean story dict — every one of the six required keys present.
_CLEAN_STORY: dict = {
    "title": "The Guggenheim's Rise",
    "place": "Solomon R. Guggenheim Museum",
    "beat_type": "anecdote",
    "lenses": ["historic_arch"],
    "claim_ids": ["c01", "c02"],
    "enrichment": _CLEAN_ENRICHMENT,
}

#: AC-13's inline pois fixture (tests/test_ingest_gates.py), reused
#: verbatim here so a place-resolution test in this file pins the exact
#: same fixture gates.py's own test proves — never a second copy that
#: could quietly drift from it.
_AC13_POIS: list[dict] = [
    {
        "name": "Solomon R. Guggenheim Museum",
        "name_variations": ["The Guggenheim"],
        "parent_poi": None,
    },
    {
        "name": "Metropolitan Museum of Art",
        "name_variations": ["The Met"],
        "parent_poi": None,
    },
    {
        "name": "Great Hall",
        "name_variations": [],
        "parent_poi": "Ellis Island",
    },
]

#: AC-59 fixture pins (decisions.fixture_pins): the only "clean" P2 story
#: answers this file scripts. Every entry here is proven assertion-free
#: against the real gates by test_fixture_pins_are_clean below (a clean
#: partition over {c01, c02, c03}), so a story set that step 15's refusal
#: loop would reject fails THIS node, never a later step's. The first
#: story cites a name_variation ('The Guggenheim') so
#: test_group_returns_stories_with_resolved_places_and_new_poi_flags
#: exercises real variation resolution, not an already-canonical name;
#: the second cites a place absent from _AC13_POIS (Neue Galerie), the
#: new_poi leg.
CLEAN_STORY_ANSWERS: list[dict] = [
    {
        "title": "The Guggenheim's Rise",
        "place": "The Guggenheim",
        "beat_type": "anecdote",
        "lenses": ["historic_arch"],
        "claim_ids": ["c01", "c02"],
        "enrichment": _CLEAN_ENRICHMENT,
    },
    {
        "title": "Arriving at Neue Galerie",
        "place": "Neue Galerie",
        "beat_type": "stop_orientation",
        "lenses": ["visual_art"],
        "claim_ids": ["c03"],
        "enrichment": _CLEAN_ENRICHMENT,
    },
]


def _claim_drafts(unit) -> list[decompose.ClaimDraft]:
    """Three ClaimDraft bound to the real unit's own Source factory.

    group() only ever reads `.claim_id`/`.text` off each draft, so unlike
    decompose.py's own CLEAN_ANSWERS fixture (which must pass
    gates.claim_gates), the text/span content here is incidental.
    """
    return [
        decompose.ClaimDraft(
            claim_id="c01",
            text="The Guggenheim opened its doors in October 1959.",
            kind="event",
            source=unit.source("s1"),
        ),
        decompose.ClaimDraft(
            claim_id="c02",
            text="Admission cost fifty cents at the Guggenheim's opening.",
            kind="state",
            source=unit.source("s2"),
        ),
        decompose.ClaimDraft(
            claim_id="c03",
            text="Neue Galerie sits across Fifth Avenue from the Guggenheim.",
            kind="state",
            source=unit.source("s3"),
        ),
    ]


class _RecordingClient:
    """A duck-typed proxy around a real llm.ModelClient (usually a
    MockClient) that records every complete()/complete_batch() call's
    exact keyword arguments, so a test can assert group() called the
    transport with the pinned shape (AC-44) — including that it never
    calls complete_batch() at all — without MockClient itself needing to
    track more than (role, phase) per call.
    """

    def __init__(self, client) -> None:
        self._client = client
        self.calls: list[dict] = []
        self.batch_calls: list[dict] = []

    def complete(self, *, role, prompt, schema, phase, max_tokens):
        self.calls.append(
            {
                "role": role,
                "prompt": prompt,
                "schema": schema,
                "phase": phase,
                "max_tokens": max_tokens,
            }
        )
        return self._client.complete(
            role=role, prompt=prompt, schema=schema, phase=phase, max_tokens=max_tokens
        )

    def complete_batch(self, *, role, prompts, schema, phase, max_tokens):
        self.batch_calls.append(
            {
                "role": role,
                "prompts": list(prompts),
                "schema": schema,
                "phase": phase,
                "max_tokens": max_tokens,
            }
        )
        return self._client.complete_batch(
            role=role, prompts=prompts, schema=schema, phase=phase, max_tokens=max_tokens
        )


def _armed_group_mock(stories: list[dict]) -> llm.MockClient:
    """A MockClient scripted with one P2 `stories` answer, its estimate
    gate already armed against the real chunk text and the real
    P2_PLAN — the shape every group() happy-path test below needs."""
    _mock_events, mock_sink = _sink_and_events()
    answer = llm.MockAnswer(text=json.dumps({"stories": stories}), model_id="claude-opus-5")
    mock = llm.MockClient(mock_sink, answers={"author": [answer]})
    mock.estimate([_real_unit().text], list(group.P2_PLAN))
    return mock


def _armed_group_mock_sequence(*story_answers: list[dict]) -> llm.MockClient:
    """A MockClient scripted with ONE P2 answer per positional argument,
    drawn in order, its estimate gate already armed against the real chunk
    text and the real P2_PLAN.

    The multi-answer form the refusal-loop tests need: `_armed_group_mock`
    scripts a single answer, so a test that expects a re-ask would draw
    MockScriptExhausted instead of a second answer.
    """
    _mock_events, mock_sink = _sink_and_events()
    answers = [
        llm.MockAnswer(text=json.dumps({"stories": stories}), model_id="claude-opus-5")
        for stories in story_answers
    ]
    mock = llm.MockClient(mock_sink, answers={"author": answers})
    mock.estimate([_real_unit().text], list(group.P2_PLAN))
    return mock


def _refuse_then_clean(unit, claims, first_answer: list[dict]):
    """Run group() over a bad first answer followed by CLEAN_STORY_ANSWERS.

    Returns (result, events, mock, proxy) so a caller can assert on the
    refusal event, the single re-ask and attempt 2's returned stories.
    """
    mock = _armed_group_mock_sequence(first_answer, CLEAN_STORY_ANSWERS)
    proxy = _RecordingClient(mock)
    events, sink = _sink_and_events()
    result = group.group(claims, unit, _AC13_POIS, proxy, events=sink)
    return result, events, mock, proxy


def _refusals(events) -> list[dict]:
    """Every stories_refused payload in an event log, in order."""
    return [payload for kind, payload in events if kind == "stories_refused"]


def _assert_one_re_ask_then_clean(result, events, mock, proxy) -> dict:
    """The shape every AC-49..AC-52 refusal leg shares: exactly ONE
    stories_refused event, for attempt 1; exactly two P2 calls (never a
    third); and attempt 2's clean partition of {c01, c02, c03} — every
    claim in exactly one story — is what group() returned.

    Returns the refusal payload so the caller can assert on its reasons.
    """
    refusals = _refusals(events)
    assert len(refusals) == 1, refusals
    assert refusals[0]["attempt"] == 1
    assert mock.calls == [("author", "P2"), ("author", "P2")]
    assert len(proxy.calls) == 2
    assigned = [claim_id for story in result for claim_id in story.claim_ids]
    assert sorted(assigned) == ["c01", "c02", "c03"], assigned
    return refusals[0]


def test_parse_stories_refuses_any_unreadable_answer_never_partial():
    unreadable = [
        "not json",
        '{"stories": []}',
        # missing claim_ids
        json.dumps({"stories": [{k: v for k, v in _CLEAN_STORY.items() if k != "claim_ids"}]}),
        # beat_type outside BEAT_TYPE_VALUES
        json.dumps({"stories": [{**_CLEAN_STORY, "beat_type": "story"}]}),
        # a lens outside TAGGABLE_LENSES
        json.dumps({"stories": [{**_CLEAN_STORY, "lenses": ["not_a_real_lens"]}]}),
        # kid_friendly outside the schema's own enum
        json.dumps(
            {
                "stories": [
                    {**_CLEAN_STORY, "enrichment": {**_CLEAN_ENRICHMENT, "kid_friendly": "maybe"}}
                ]
            }
        ),
        # an extra key on the story item
        json.dumps({"stories": [{**_CLEAN_STORY, "extra": 1}]}),
        # not a list
        json.dumps({"stories": "not-a-list"}),
    ]
    for answer in unreadable:
        assert group.parse_stories(answer) is None, answer

    good_answer = json.dumps({"stories": [_CLEAN_STORY]})
    assert group.parse_stories(good_answer) == [_CLEAN_STORY]

    # A partially valid list (one good item, one bad) is refused whole —
    # never a partial list of only the good story.
    partially_valid = json.dumps({"stories": [_CLEAN_STORY, {"title": "bad"}]})
    assert group.parse_stories(partially_valid) is None

    # Surrounding prose is stripped: the JSON object is still found and parsed.
    wrapped = f"Sure, here is the answer:\n{good_answer}\nLet me know if you need more."
    assert group.parse_stories(wrapped) == [_CLEAN_STORY]


def test_story_and_enrichment_shapes():
    # Enrichment() defaults are pulled LIVE from model.Beat, so this stays
    # true even if Beat's own defaults ever change.
    enrichment = group.Enrichment()
    for field in (
        "physical_cues",
        "entities",
        "narrative_function",
        "emotional_register",
        "sensory_anchor",
        "inline_foreign_phrases",
        "pronunciation",
        "kid_friendly",
        "sub_location",
        "trigger_address",
    ):
        beat_default = model.Beat.model_fields[field].get_default(call_default_factory=True)
        assert getattr(enrichment, field) == beat_default, field

    # The schema's forced-string sentinels map back to None.
    sentinel_enrichment = group.Enrichment(
        kid_friendly="unknown", pronunciation="", narrative_function=""
    )
    assert sentinel_enrichment.kid_friendly is None
    assert sentinel_enrichment.pronunciation is None
    assert sentinel_enrichment.narrative_function is None

    # A real yes/no answer passes through unchanged.
    assert group.Enrichment(kid_friendly="yes").kid_friendly == "yes"
    assert group.Enrichment(kid_friendly="no").kid_friendly == "no"

    with pytest.raises(ValidationError):
        group.Enrichment(kid_friendly="maybe")

    # story_slug is derived from the raw title via model.slug — never a
    # slug the model might have invented itself (the schema does not even
    # offer it a slug field).
    accented_story = group._story_from_raw(
        {**_CLEAN_STORY, "title": "Café on the ramp"},
        place=_CLEAN_STORY["place"],
        new_poi=True,
    )
    assert accented_story.story_slug == model.slug("Café on the ramp")
    assert accented_story.new_poi is True
    assert isinstance(accented_story.new_poi, bool)

    other_story = group._story_from_raw(_CLEAN_STORY, place=_CLEAN_STORY["place"], new_poi=False)
    assert other_story.new_poi is False

    # Both models are frozen — the same guarantee tests/test_ingest_decompose.py
    # pins for ClaimDraft. A later step must rebuild a Story (or its
    # Enrichment) rather than mutate one in place, so nothing downstream can
    # edit a story after its gates have already passed on it.
    with pytest.raises(ValidationError):
        other_story.title = "x"
    with pytest.raises(ValidationError):
        enrichment.sensory_anchor = True

    with pytest.raises(ValidationError):
        group.Story(
            title="x",
            story_slug="x",
            place="x",
            new_poi=False,
            lenses=["x"],
            beat_type="anecdote",
            enrichment=group.Enrichment(),
            claim_ids=["c01"],
        )


def test_p2_plan_rows_and_max_tokens():
    assert group.P2_MAX_TOKENS == 64_000  # tests/test_ingest_output_caps.py sizes it
    assert isinstance(group.P2_PLAN, tuple)
    assert len(group.P2_PLAN) == 2

    expected_overheads = [
        max(1, len(prompts.GROUP_PROMPT_TEMPLATE) // 4),
        max(1, len(prompts.GROUP_REDO_TEMPLATE) // 4),
    ]
    for row, expected_overhead in zip(group.P2_PLAN, expected_overheads, strict=True):
        assert row.phase == "P2"
        assert row.role == "author"
        assert row.calls_per_unit == 1
        assert row.overhead_tokens == expected_overhead
        assert row.overhead_tokens > 0
        assert row.expected_output_tokens == group.P2_EXPECTED_OUTPUT_TOKENS

    events, sink = _sink_and_events()
    mock = llm.MockClient(sink)
    unit = _real_unit()
    combined_plan = [*decompose.P1_PLAN, *group.P2_PLAN]
    estimate = mock.estimate([unit.text], combined_plan)
    assert len(estimate.rows) == 4

    p1_rows = [row for row in estimate.rows if row.phase == "P1"]
    p2_rows = [row for row in estimate.rows if row.phase == "P2"]
    assert len(p1_rows) == 2
    assert len(p2_rows) == 2
    for cost_row in p1_rows:
        assert cost_row.batch is True
    for cost_row in p2_rows:
        assert cost_row.model_id == "claude-opus-5"
        assert cost_row.batch is False
    assert events and events[0][0] == "cost_estimate"


def test_fixture_pins_are_clean():
    """AC-59: CLEAN_STORY_ANSWERS partitions {c01, c02, c03} cleanly
    against the real gates — a story set that step 15's refusal loop
    would reject must fail HERE, never a later step's own test."""
    claim_ids = ["c01", "c02", "c03"]
    assert gates.story_problems(CLEAN_STORY_ANSWERS, claim_ids) == []


def test_group_returns_stories_with_resolved_places_and_new_poi_flags():
    """AC-44: group() makes exactly one sync P2 call (never
    complete_batch), resolves each story's `place` via
    gates.resolve_place, and flags an unresolved place with a
    place_unresolved event — without refusing the story."""
    unit = _real_unit()
    claims = _claim_drafts(unit)
    mock = _armed_group_mock(CLEAN_STORY_ANSWERS)
    proxy = _RecordingClient(mock)
    events, sink = _sink_and_events()

    result = group.group(claims, unit, _AC13_POIS, proxy, events=sink)

    assert len(result) == 2
    first, second = result

    # The Guggenheim resolves through its name_variation to the canonical
    # POI name — not the variation text — and is not a new POI.
    assert first.place == "Solomon R. Guggenheim Museum"
    assert first.new_poi is False
    assert isinstance(first.new_poi, bool)

    # Neue Galerie matches nothing in _AC13_POIS: flagged new_poi, kept as
    # given, and the story is NOT refused for it.
    assert second.place == "Neue Galerie"
    assert second.new_poi is True
    assert isinstance(second.new_poi, bool)

    assert (
        "place_unresolved",
        {"unit_key": unit.key, "story_slug": second.story_slug, "place": "Neue Galerie"},
    ) in events
    assert not any(kind == "stories_refused" for kind, _ in events)
    assert not any(kind == "story_dropped" for kind, _ in events)

    assert mock.calls == [("author", "P2")]
    assert proxy.batch_calls == []
    assert len(proxy.calls) == 1
    call = proxy.calls[0]
    assert call["role"] == "author"
    assert call["phase"] == "P2"
    assert call["schema"] == prompts.P2_RESPONSE_SCHEMA
    assert call["max_tokens"] == group.P2_MAX_TOKENS
    for claim in claims:
        assert f"{claim.claim_id}: {claim.text}" in call["prompt"]
    assert prompts.TIE_BREAK in call["prompt"]


def test_group_never_arms_the_estimate_itself():
    """AC-45: group() never calls client.estimate() itself — an unarmed
    client's complete() raises EstimateNotPrinted here, uncaught, with
    zero calls and zero events."""
    unit = _real_unit()
    claims = _claim_drafts(unit)
    _mock_events, mock_sink = _sink_and_events()
    answer = llm.MockAnswer(
        text=json.dumps({"stories": CLEAN_STORY_ANSWERS}), model_id="claude-opus-5"
    )
    unarmed_mock = llm.MockClient(mock_sink, answers={"author": [answer]})

    events, sink = _sink_and_events()
    with pytest.raises(llm.EstimateNotPrinted):
        group.group(claims, unit, _AC13_POIS, unarmed_mock, events=sink)
    assert unarmed_mock.calls == []
    assert events == []


def test_group_of_no_claims_makes_no_call():
    """AC-46: group() of zero claims returns [] and never calls the
    client at all — checked before render_group or complete() — even
    though an author answer is scripted."""
    unit = _real_unit()
    mock = _armed_group_mock(CLEAN_STORY_ANSWERS)

    events, sink = _sink_and_events()
    result = group.group([], unit, _AC13_POIS, mock, events=sink)

    assert result == []
    assert mock.calls == []
    assert events == []


def test_group_prompt_does_not_carry_the_poi_list():
    """AC-47 (D7): the rendered P2 prompt never carries the poi list —
    place resolution happens locally, after the model answers, never by
    handing it the candidate names up front."""
    unit = _real_unit()
    claims = _claim_drafts(unit)
    sentinel_pois = [
        *_AC13_POIS,
        {"name": "Zzyzx Sentinel Hall", "name_variations": [], "parent_poi": None},
    ]
    mock = _armed_group_mock(CLEAN_STORY_ANSWERS)
    proxy = _RecordingClient(mock)

    _events, sink = _sink_and_events()
    group.group(claims, unit, sentinel_pois, proxy, events=sink)

    assert len(proxy.calls) == 1
    assert "Zzyzx" not in proxy.calls[0]["prompt"]


def test_group_refuses_to_run_without_the_tie_break(monkeypatch):
    """AC-48: with prompts.group.TIE_BREAK monkeypatched to '', group()
    raises TieBreakMissing before making any call."""
    import src.ingest.prompts.group as p_group

    unit = _real_unit()
    claims = _claim_drafts(unit)
    mock = _armed_group_mock(CLEAN_STORY_ANSWERS)
    monkeypatch.setattr(p_group, "TIE_BREAK", "")

    _events, sink = _sink_and_events()
    with pytest.raises(prompts.TieBreakMissing):
        group.group(claims, unit, _AC13_POIS, mock, events=sink)
    assert mock.calls == []


def test_every_claim_lands_in_exactly_one_story():
    """AC-49: an answer that leaves a claim out — or books one into two
    stories — is refused WHOLE, re-asked exactly once with the reason
    quoted back, and attempt 2's clean partition is what group() returns.
    """
    unit = _real_unit()
    claims = _claim_drafts(unit)

    # --- orphan: attempt 1 drops c03 on the floor entirely ---
    orphan_answer = [CLEAN_STORY_ANSWERS[0]]
    result, events, mock, proxy = _refuse_then_clean(unit, claims, orphan_answer)

    payload = _assert_one_re_ask_then_clean(result, events, mock, proxy)
    assert payload["unit_key"] == unit.key
    orphan_reasons = [r for r in payload["reasons"] if r.startswith("orphan_claim:c03")]
    assert len(orphan_reasons) == 1, payload["reasons"]
    # the re-ask quotes attempt 1's reason back verbatim
    assert orphan_reasons[0] in proxy.calls[1]["prompt"]

    # --- double-booked: attempt 1 puts c02 in two stories ---
    double_booked_answer = [
        {**CLEAN_STORY_ANSWERS[0], "claim_ids": ["c01", "c02"]},
        {**CLEAN_STORY_ANSWERS[1], "claim_ids": ["c02", "c03"]},
    ]
    result, events, mock, proxy = _refuse_then_clean(unit, claims, double_booked_answer)

    payload = _assert_one_re_ask_then_clean(result, events, mock, proxy)
    assert payload["unit_key"] == unit.key
    double_reasons = [r for r in payload["reasons"] if r.startswith("double_booked:c02")]
    assert len(double_reasons) == 1, payload["reasons"]
    assert double_reasons[0] in proxy.calls[1]["prompt"]


def test_structural_story_may_carry_one_claim_others_need_two():
    """AC-50: the structural beat types clear the claim floor at one claim;
    an anecdote does not, and a story with no claims never does."""
    unit = _real_unit()
    claims = _claim_drafts(unit)

    # CLEAN_STORY_ANSWERS' second story is a stop_orientation carrying the
    # single claim c03 while the anecdote holds c01+c02: accepted, one call.
    mock = _armed_group_mock_sequence(CLEAN_STORY_ANSWERS)
    proxy = _RecordingClient(mock)
    events, sink = _sink_and_events()
    result = group.group(claims, unit, _AC13_POIS, proxy, events=sink)
    assert len(result) == 2
    assert mock.calls == [("author", "P2")]
    assert _refusals(events) == []

    # The same one-claim story typed as an anecdote is under its floor.
    thin_anecdote = [
        {**CLEAN_STORY_ANSWERS[0], "claim_ids": ["c01"]},
        {**CLEAN_STORY_ANSWERS[1], "claim_ids": ["c02", "c03"]},
    ]
    result, events, mock, proxy = _refuse_then_clean(unit, claims, thin_anecdote)
    payload = _assert_one_re_ask_then_clean(result, events, mock, proxy)
    thin_slug = model.slug(CLEAN_STORY_ANSWERS[0]["title"])
    assert any(r.startswith(f"too_few_claims:{thin_slug}") for r in payload["reasons"]), payload

    # A story carrying zero claims is refused whatever its beat_type — the
    # structural floor is one claim, not none.
    empty_story = [
        {**CLEAN_STORY_ANSWERS[0], "claim_ids": ["c01", "c02", "c03"]},
        {**CLEAN_STORY_ANSWERS[1], "claim_ids": []},
    ]
    result, events, mock, proxy = _refuse_then_clean(unit, claims, empty_story)
    payload = _assert_one_re_ask_then_clean(result, events, mock, proxy)
    empty_slug = model.slug(CLEAN_STORY_ANSWERS[1]["title"])
    assert any(r.startswith(f"too_few_claims:{empty_slug}") for r in payload["reasons"]), payload


def test_story_gates_refuse_bad_lenses_ids_slugs_and_types():
    """AC-51: bad lenses, an unknown claim id and a duplicated story_slug
    each refuse the whole answer and buy exactly one re-ask.

    lenses ['x'] and beat_type 'story' are refused one layer earlier: the
    P2 response schema's own enums make parse_stories return None (AC-41),
    so their refusal reason is a 'schema:' one rather than a gate code.
    """
    unit = _real_unit()
    claims = _claim_drafts(unit)
    first_slug = model.slug(CLEAN_STORY_ANSWERS[0]["title"])

    gate_legs = [
        # lenses empty, then duplicated: both schema-legal, both gate-illegal
        (
            [{**CLEAN_STORY_ANSWERS[0], "lenses": []}, CLEAN_STORY_ANSWERS[1]],
            f"bad_lenses:{first_slug}",
        ),
        (
            [
                {**CLEAN_STORY_ANSWERS[0], "lenses": ["historic_arch", "historic_arch"]},
                CLEAN_STORY_ANSWERS[1],
            ],
            f"bad_lenses:{first_slug}",
        ),
        # an id no claim in the unit carries
        (
            [CLEAN_STORY_ANSWERS[0], {**CLEAN_STORY_ANSWERS[1], "claim_ids": ["c03", "c99"]}],
            "unknown_claim:c99",
        ),
        # two titles slugging to one story_slug
        (
            [
                CLEAN_STORY_ANSWERS[0],
                {**CLEAN_STORY_ANSWERS[1], "title": CLEAN_STORY_ANSWERS[0]["title"]},
            ],
            f"duplicate_slug:{first_slug}",
        ),
    ]
    for first_answer, code in gate_legs:
        result, events, mock, proxy = _refuse_then_clean(unit, claims, first_answer)
        payload = _assert_one_re_ask_then_clean(result, events, mock, proxy)
        matching = [r for r in payload["reasons"] if r.startswith(code)]
        assert len(matching) == 1, (code, payload["reasons"])
        assert matching[0] in proxy.calls[1]["prompt"]

    schema_legs = [
        [{**CLEAN_STORY_ANSWERS[0], "lenses": ["x"]}, CLEAN_STORY_ANSWERS[1]],
        [{**CLEAN_STORY_ANSWERS[0], "beat_type": "story"}, CLEAN_STORY_ANSWERS[1]],
    ]
    for first_answer in schema_legs:
        result, events, mock, proxy = _refuse_then_clean(unit, claims, first_answer)
        payload = _assert_one_re_ask_then_clean(result, events, mock, proxy)
        assert payload["reasons"], payload
        assert all(r.startswith("schema:") for r in payload["reasons"]), payload["reasons"]
        assert payload["reasons"][0] in proxy.calls[1]["prompt"]


def test_re_ask_prompt_quotes_the_story_problems_back():
    """AC-52: every reason from attempt 1 appears verbatim in the single
    re-ask, which asks for a whole fresh answer ('from the start') rather
    than a patch of only the flagged stories."""
    unit = _real_unit()
    claims = _claim_drafts(unit)

    three_defects = [
        {**CLEAN_STORY_ANSWERS[0], "claim_ids": ["c01", "c02"]},
        # zero claims (so c03 is orphaned AND this story is under its
        # floor) and no lenses: orphan + too_few_claims + bad_lenses
        {**CLEAN_STORY_ANSWERS[1], "claim_ids": [], "lenses": []},
    ]
    result, events, mock, proxy = _refuse_then_clean(unit, claims, three_defects)
    payload = _assert_one_re_ask_then_clean(result, events, mock, proxy)

    second_slug = model.slug(CLEAN_STORY_ANSWERS[1]["title"])
    reasons = payload["reasons"]
    assert len(reasons) == 3, reasons
    assert any(r.startswith("orphan_claim:c03") for r in reasons), reasons
    assert any(r.startswith(f"too_few_claims:{second_slug}") for r in reasons), reasons
    assert any(r.startswith(f"bad_lenses:{second_slug}") for r in reasons), reasons

    re_ask = proxy.calls[1]["prompt"]
    for reason in reasons:
        assert reason in re_ask, reason
    assert "from the start" in re_ask


def test_second_refusal_drops_stories_and_never_asks_a_third_time():
    """AC-53: on attempt 2, a story-level defect (bad lenses) drops that
    whole story; a claim double-booked across two (otherwise-surviving)
    stories is removed from both; a story that falls under its claim floor
    once its double-booked claim is gone is dropped too; and a claim left
    with no home at all (because its own story was the bad-lens one) is
    logged as dropped, exactly once each — never moved, never re-typed, and
    never re-asked a third time even though a third answer is scripted."""
    unit = _real_unit()
    claims = [
        decompose.ClaimDraft(
            claim_id=f"c0{i}", text=f"c0{i} text", kind="state", source=unit.source(f"s{i}")
        )
        for i in (1, 2, 3, 4)
    ]

    # Attempt 1: one defect (bad lenses) is enough to buy the single re-ask;
    # its own claim partition is otherwise clean so it exercises no other
    # gate.
    attempt1_answer = [
        {
            "title": "Placeholder First Attempt",
            "place": "Solomon R. Guggenheim Museum",
            "beat_type": "anecdote",
            "lenses": [],
            "claim_ids": ["c01", "c02", "c03", "c04"],
            "enrichment": _CLEAN_ENRICHMENT,
        }
    ]

    bad_lens_story = {
        "title": "Bad Lens Story",
        "place": "Solomon R. Guggenheim Museum",
        "beat_type": "stop_orientation",
        "lenses": [],  # dropped for this, independent of claim membership
        "claim_ids": ["c01"],
        "enrichment": _CLEAN_ENRICHMENT,
    }
    thinning_story = {
        "title": "Guggenheim Story",
        "place": "Solomon R. Guggenheim Museum",
        "beat_type": "anecdote",  # floor 2
        "lenses": ["historic_arch"],
        "claim_ids": ["c02", "c04"],  # c04 double-booked with the next story
        "enrichment": _CLEAN_ENRICHMENT,
    }
    surviving_story = {
        "title": "Neue Galerie Story",
        "place": "Neue Galerie",
        "beat_type": "stop_orientation",  # floor 1 — survives losing c04
        "lenses": ["visual_art"],
        "claim_ids": ["c03", "c04"],
        "enrichment": _CLEAN_ENRICHMENT,
    }
    attempt2_answer = [bad_lens_story, thinning_story, surviving_story]

    # Scripted but must never be drawn: a third call would be a bug.
    third_answer = CLEAN_STORY_ANSWERS

    mock = _armed_group_mock_sequence(attempt1_answer, attempt2_answer, third_answer)
    proxy = _RecordingClient(mock)
    events, sink = _sink_and_events()

    result = group.group(claims, unit, _AC13_POIS, proxy, events=sink)

    assert mock.calls == [("author", "P2"), ("author", "P2")]
    assert len(proxy.calls) == 2
    assert len(mock._answers["author"]) == 1  # third_answer still unscripted

    # Only the Neue Galerie story survives, with its double-booked claim
    # stripped and nothing else touched.
    assert len(result) == 1
    assert result[0].story_slug == model.slug(surviving_story["title"])
    assert result[0].claim_ids == ["c03"]

    story_dropped = [payload for kind, payload in events if kind == "story_dropped"]
    dropped_slugs = {payload["story_slug"] for payload in story_dropped}
    assert dropped_slugs == {
        model.slug(bad_lens_story["title"]),
        model.slug(thinning_story["title"]),
    }
    bad_lens_drop = next(
        p for p in story_dropped if p["story_slug"] == model.slug(bad_lens_story["title"])
    )
    assert bad_lens_drop["reason"].startswith("bad_lenses")
    thin_drop = next(
        p for p in story_dropped if p["story_slug"] == model.slug(thinning_story["title"])
    )
    assert thin_drop["reason"].startswith("too_few_claims")

    claim_dropped = [payload for kind, payload in events if kind == "claim_dropped"]
    dropped_ids = {payload["claim_id"] for payload in claim_dropped}
    # c01 (only ever in the bad-lens story) ends up with no home at all;
    # c04 is the double-booked claim; c02 goes down with the thinning story
    # once c04 is stripped from it — every claim is accounted for exactly
    # once, none of them moved into the surviving story.
    assert dropped_ids == {"c01", "c02", "c04"}
    orphan_drop = next(p for p in claim_dropped if p["claim_id"] == "c01")
    assert orphan_drop["reason"].startswith("orphan_claim:c01")
    double_drop = next(p for p in claim_dropped if p["claim_id"] == "c04")
    assert double_drop["reason"].startswith("double_booked:c04")

    # No claim was moved into the surviving story, and no story was re-typed.
    assert "c02" not in result[0].claim_ids
    assert result[0].beat_type == "stop_orientation"


class _RawValueErrorGroupClient:
    """A duck-typed ModelClient whose `complete` raises a bare ValueError —
    never an `llm.LlmError` — mirroring decompose.py's own
    `_RawValueErrorClient` (slice-2 carry-forward 3), but for the sync P2
    transport rather than the batch P1 one."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *, role, prompt, schema, phase, max_tokens):
        self.calls += 1
        raise ValueError("no text")


def _armed_client_with_raw_answers(*answers: llm.MockAnswer) -> llm.MockClient:
    """A MockClient scripted with raw MockAnswer objects (rather than JSON
    story-list answers), armed exactly like `_armed_group_mock` — the shape
    the AC-54 transport/unreadable-answer legs need."""
    _mock_events, mock_sink = _sink_and_events()
    mock = llm.MockClient(mock_sink, answers={"author": list(answers)})
    mock.estimate([_real_unit().text], list(group.P2_PLAN))
    return mock


def test_p2_transport_and_unreadable_answers_hold():
    """AC-54: EmptyCompletion/TruncatedCompletion from the transport hold
    the unit in exactly one call; twice-unreadable text holds it in two
    calls; a bare (non-llm.LlmError) ValueError from a duck-typed transport
    holds it in exactly one call too."""
    unit = _real_unit()
    claims = _claim_drafts(unit)

    empty_mock = _armed_client_with_raw_answers(
        llm.MockAnswer(text="   ", model_id="claude-opus-5")
    )
    events, sink = _sink_and_events()
    with pytest.raises(group.UnitHeld) as raised:
        group.group(claims, unit, _AC13_POIS, empty_mock, events=sink)
    assert raised.value.phase == "P2"
    assert raised.value.unit_key == unit.key
    assert empty_mock.calls == [("author", "P2")]
    assert [kind for kind, _ in events] == ["unit_held"]

    truncated_mock = _armed_client_with_raw_answers(
        llm.MockAnswer(
            text=json.dumps({"stories": CLEAN_STORY_ANSWERS}),
            model_id="claude-opus-5",
            stop_reason="max_tokens",
        )
    )
    events2, sink2 = _sink_and_events()
    with pytest.raises(group.UnitHeld) as raised2:
        group.group(claims, unit, _AC13_POIS, truncated_mock, events=sink2)
    assert raised2.value.phase == "P2"
    assert raised2.value.unit_key == unit.key
    assert truncated_mock.calls == [("author", "P2")]
    assert [kind for kind, _ in events2] == ["unit_held"]

    unreadable_mock = _armed_client_with_raw_answers(
        llm.MockAnswer(text="not json", model_id="claude-opus-5"),
        llm.MockAnswer(text='{"stories": []}', model_id="claude-opus-5"),
    )
    events3, sink3 = _sink_and_events()
    with pytest.raises(group.UnitHeld) as raised3:
        group.group(claims, unit, _AC13_POIS, unreadable_mock, events=sink3)
    assert raised3.value.phase == "P2"
    assert raised3.value.unit_key == unit.key
    assert unreadable_mock.calls == [("author", "P2"), ("author", "P2")]
    assert [kind for kind, _ in events3] == ["stories_refused", "stories_refused", "unit_held"]

    client = _RawValueErrorGroupClient()
    events4, sink4 = _sink_and_events()
    with pytest.raises(group.UnitHeld) as raised4:
        group.group(claims, unit, _AC13_POIS, client, events=sink4)
    assert raised4.value.phase == "P2"
    assert raised4.value.unit_key == unit.key
    assert "no text" in raised4.value.reason
    assert client.calls == 1
    assert [kind for kind, _ in events4] == ["unit_held"]


def test_no_live_client_in_this_file():
    """AC-55: this $0-spend test file never names a live LLM client or
    reads its API key. Its own body is exempt from the walk below — this
    docstring and the assert messages name those things on purpose to
    describe the rule, which is not the violation the rule guards against.
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
                    "this file must never read ANTHROPIC_API_KEY"
                )


def _real_pois() -> list[dict]:
    raw = json.loads((REPO_ROOT / "data" / "new_york" / "poi-raw.json").read_text("utf-8"))
    return raw["pois"] if isinstance(raw, dict) else raw


def test_candidate_places_finds_every_listed_place_the_passage_names():
    """Slice 10 job A named the Guggenheim "Guggenheim Museum", Neue Galerie
    "Neue Galerie" and Cooper Hewitt "Cooper-Hewitt" — each held as a new
    place though poi-raw holds all three under longer names. The candidates
    shown to P2 are every POI whose distinctive name words (hyphens split,
    generic words like "museum" or "new york" ignored) occur in the passage:
    on the real Upper East Side chunk and the real city file, all five
    listed places it names are found, and a place it never names is not."""
    unit = _real_unit()
    found = gates.candidate_places(_real_pois(), unit.text)
    for name in (
        "Solomon R. Guggenheim Museum",
        "Metropolitan Museum of Art",
        "Neue Galerie New York",
        "Cooper Hewitt National Design Museum",
        "Frick Collection",
    ):
        assert name in found, name
    assert "Statue of Liberty" not in found


def test_group_shows_the_candidate_places_and_asks_for_the_exact_name():
    """The P2 prompt — first ask and re-ask alike — lists the unit's
    candidate places and requires a story at one of them to carry its name
    exactly as listed, so resolve_place finds it."""
    unit = _real_unit()
    claims = _claim_drafts(unit)
    proxy = _RecordingClient(_armed_group_mock(CLEAN_STORY_ANSWERS))

    group.group(claims, unit, _real_pois(), proxy)

    prompt = proxy.calls[0]["prompt"]
    assert "- Solomon R. Guggenheim Museum" in prompt
    assert "write its name exactly as listed" in prompt
    redo = prompts.render_group_redo(
        [("c01", "t")], ["x"], places=["Solomon R. Guggenheim Museum"]
    )
    assert "- Solomon R. Guggenheim Museum" in redo
    assert "write its name exactly as listed" in redo

