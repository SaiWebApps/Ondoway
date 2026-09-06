"""Regenerating a beat from claims the writer cannot trace back to a passage.

The refuted pipeline handed the writer the guidebook passage and asked for other
words, which kept the source's selection and arrangement in 91% of multi-sentence
cases. This one removes the writer's access to the passage, so the evidence of
non-derivation is the shape of the request rather than a score measured afterwards.

The load-bearing test here is `test_the_writer_request_contains_no_source_prose`,
run against REAL records from the corpus. Everything else guards the claim set that
reaches it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.corpus_report import (
    VERBATIM_RUN_BLOCK,
    load_city_beats,
    max_verbatim_run,
    verbatim_words,
)
from scripts.reauthor_cleanroom import (
    CLAIM_KINDS,
    PRODUCER_MODEL,
    claims_record,
    cleanroom_record,
    cleanroom_request,
    copied_backlog,
    decompose_request,
    input_hash,
    lifted_claims,
    parse_claims,
    render_claims,
    shuffle_seed,
    shuffled_claims,
    target_words,
)

_CLAIMS = [
    {"claim": "The Pont Neuf became a symbol of Paris.", "kind": "fact"},
    {"claim": "Pedlars traded from stalls on the Pont Neuf.", "kind": "fact"},
    {"claim": "The Pont Neuf drew large crowds.", "kind": "fact"},
    {"claim": "The bridge feels unusually open to the sky.", "kind": "observation"},
]


# ── the clean room ───────────────────────────────────────────────────────────


def _real_records(limit: int = 8) -> list[dict]:
    """A few copied beats from the corpus, so the seam is tested on real prose."""
    beats = copied_backlog(load_city_beats("paris"))[:limit]
    if not beats:
        pytest.skip("no copied Paris beats to test the seam against")
    return beats


def test_the_writer_request_contains_no_source_prose() -> None:
    """The point of Stage 1: the writer cannot copy an arrangement it never saw.

    Checked as overlap, not as substring equality — a request that shares a long run
    with the source passage has the source in it however it arrived.
    """
    for beat in _real_records():
        source = beat["source_passage"]
        payload = json.dumps(
            cleanroom_request(claims=_CLAIMS, poi=beat.get("poi_name", ""), city="Paris", words=90)
        )
        assert source not in payload
        assert (beat.get("script_body") or "") not in payload
        assert max_verbatim_run(payload, source) < VERBATIM_RUN_BLOCK


def test_the_writer_request_takes_no_parameter_that_could_carry_the_source() -> None:
    """Structure, not vigilance: there is no argument to pass a passage through."""
    with pytest.raises(TypeError):
        cleanroom_request(  # type: ignore[call-arg]
            claims=_CLAIMS, poi="Pont Neuf", city="Paris", words=90, source="the passage"
        )


def test_the_decomposer_is_the_only_call_that_sees_the_source() -> None:
    """One call reads the passage; the one that writes prose does not."""
    assert "PASSAGE" in json.dumps(decompose_request(source="A passage."))


def test_the_writer_is_told_it_has_nothing_else() -> None:
    """A writer that fills a gap from world knowledge is fabricating, not authoring."""
    payload = json.dumps(cleanroom_request(claims=_CLAIMS, poi="Pont Neuf", city="Paris", words=90))
    assert "Add nothing." in payload


# ── the claim set that reaches it ────────────────────────────────────────────


def test_every_claim_reaches_the_writer() -> None:
    """A claim left out of the block is a fact the finished body cannot contain."""
    block = render_claims(_CLAIMS)
    for claim in _CLAIMS:
        assert claim["claim"] in block


def test_an_observation_is_marked_as_one() -> None:
    """Written up as fact it is editorialising; dropped it loses what the author saw."""
    assert "[observation] The bridge feels unusually open to the sky." in render_claims(_CLAIMS)


def test_the_order_the_writer_saw_can_be_rebuilt_from_the_record() -> None:
    """A shuffle nobody can reproduce is not an audit trail."""
    assert shuffled_claims(_CLAIMS, "beat_a") == shuffled_claims(_CLAIMS, "beat_a")
    assert shuffle_seed("beat_a") != shuffle_seed("beat_b")


def test_shuffling_keeps_every_claim() -> None:
    shuffled = shuffled_claims(_CLAIMS, "beat_a")
    assert sorted(c["claim"] for c in shuffled) == sorted(c["claim"] for c in _CLAIMS)


def test_the_hash_binds_the_body_to_the_claims_it_was_written_from() -> None:
    """A body whose claim block hashes differently was written from something else."""
    other = [*_CLAIMS, {"claim": "An extra claim.", "kind": "fact"}]
    assert input_hash(_CLAIMS) != input_hash(other)
    assert input_hash(_CLAIMS) == input_hash(list(_CLAIMS))


def test_reordering_the_claims_changes_the_recorded_input() -> None:
    """The record stores what the writer saw, which includes the order it saw it in."""
    assert input_hash(_CLAIMS) != input_hash(list(reversed(_CLAIMS)))


# ── the free gate that runs before any writer call ───────────────────────────


def test_a_claim_that_is_a_lifted_sentence_is_refused() -> None:
    """Shuffling lightly edited source sentences buys the same defect at claim size."""
    source = (
        "So impressive was the bridge in scale and length that it soon became "
        "symbolic of the city itself, drawing large crowds."
    )
    claims = [
        {
            "claim": "So impressive was the bridge in scale and length that it soon became "
            "symbolic of the city itself",
            "kind": "fact",
        },
    ]
    assert lifted_claims(claims, source)


def test_claims_in_their_own_words_pass_the_gate() -> None:
    source = (
        "So impressive was the bridge in scale and length that it soon became "
        "symbolic of the city itself, drawing large crowds."
    )
    assert lifted_claims(_CLAIMS, source) == []


def test_a_beat_with_a_lifted_claim_is_not_usable() -> None:
    """The gate refuses the beat rather than reporting it, so no writer call is bought."""
    beat = {"beat_id": "b", "source_passage": "The tower rises above the old quarter of town."}
    record = claims_record(
        beat, claims=[{"claim": "The tower rises above the old quarter of town.", "kind": "fact"}]
    )
    assert record["usable"] is False
    assert record["lifted_claims"]


def test_a_clean_decomposition_is_usable() -> None:
    beat = {"beat_id": "b", "source_passage": "The tower rises above the old quarter of town."}
    record = claims_record(
        beat, claims=[{"claim": "A tower stands over the old town.", "kind": "fact"}]
    )
    assert record["usable"] is True


# ── reading the decomposer ───────────────────────────────────────────────────


def test_a_readable_claim_list_is_read() -> None:
    parsed = parse_claims('{"claims": [{"claim": "A bridge stands here.", "kind": "fact"}]}')
    assert parsed == [{"claim": "A bridge stands here.", "kind": "fact"}]


def test_json_fenced_in_prose_is_still_read() -> None:
    """Models fence JSON. That is a formatting habit, not a refusal."""
    text = 'Here you go:\n```json\n{"claims": [{"claim": "A bridge.", "kind": "fact"}]}\n```'
    assert parse_claims(text) == [{"claim": "A bridge.", "kind": "fact"}]


def test_an_unknown_kind_becomes_fact() -> None:
    """An unrecognised mark must not silently drop the claim it was attached to."""
    parsed = parse_claims('{"claims": [{"claim": "A bridge.", "kind": "vibes"}]}')
    assert parsed == [{"claim": "A bridge.", "kind": "fact"}]
    assert "fact" in CLAIM_KINDS


def test_an_unreadable_answer_yields_nothing_rather_than_a_partial_set() -> None:
    """Half a claim list is how a fact leaves the corpus without anyone seeing."""
    assert parse_claims("I could not do that.") is None
    assert parse_claims('{"claims": []}') is None
    assert parse_claims('{"claims": [{"claim": "", "kind": "fact"}]}') is None


# ── the record ───────────────────────────────────────────────────────────────


def test_the_record_names_both_producers() -> None:
    """A panel must exclude every model that made the artifact, read from evidence."""
    entry = {"beat_id": "b", "poi_name": "P", "source_passage": "S.", "model": PRODUCER_MODEL}
    record = cleanroom_record(entry, body_before="old", given=_CLAIMS, body_after="new")
    assert record["decomposed_by"] == PRODUCER_MODEL
    assert record["written_by"] == PRODUCER_MODEL


def test_the_record_carries_the_exact_claims_the_writer_was_given() -> None:
    entry = {"beat_id": "b", "poi_name": "P", "source_passage": "S.", "model": PRODUCER_MODEL}
    given = shuffled_claims(_CLAIMS, "b")
    record = cleanroom_record(entry, body_before="old", given=given, body_after="new")
    assert record["claims_given"] == given
    assert record["claims_sha256"] == input_hash(given)


def test_the_record_keeps_the_source_for_the_reviewer_and_the_gates() -> None:
    """Stored on the record is not the same as shown to the writer."""
    entry = {"beat_id": "b", "poi_name": "P", "source_passage": "A source.", "model": "m"}
    record = cleanroom_record(entry, body_before="old", given=_CLAIMS, body_after="new")
    assert record["source_passage"] == "A source."
    assert record["body_before"] == "old"


# ── the backlog ──────────────────────────────────────────────────────────────


def test_the_backlog_is_the_same_one_the_old_path_addressed() -> None:
    """Two pipelines over two different backlogs would be impossible to reconcile."""
    assert len(copied_backlog(load_city_beats("paris"))) == 246
    assert len(copied_backlog(load_city_beats("new_york"))) == 278


def test_a_beat_citing_no_source_is_not_in_the_backlog() -> None:
    """Untraceable is a different defect with a different remedy."""
    assert copied_backlog([{"beat_id": "b", "script_body": "x", "source_passage": ""}]) == []


def test_the_artifacts_never_collide_with_the_stage_0_input() -> None:
    """data/{city}/reauthored.json is what the Stage 0 report describes."""
    from scripts.reauthor_cleanroom import claims_path, cleanroom_path

    for path in (claims_path("paris"), cleanroom_path("paris")):
        assert path.name != "reauthored.json"
        assert Path(path).parent.name == "paris"


def test_no_words_of_the_voice_rules_are_lost_to_formatting() -> None:
    """The voice comes from the quality standard; a truncated copy is a silent drift."""
    payload = json.dumps(cleanroom_request(claims=_CLAIMS, poi="P", city="Paris", words=90))
    assert "Say it; don't circle it" in json.loads(payload)["messages"][0]["content"]
    assert len(verbatim_words(json.loads(payload)["messages"][0]["content"])) > 100


def test_the_prompt_names_the_city_the_way_the_product_does() -> None:
    """A slug reaching the model reads like a database row, not a place."""
    from scripts.reauthor_cleanroom import city_name

    assert city_name("new_york") == "New York"
    assert city_name("paris") == "Paris"
    assert city_name("nowhere") == "nowhere"


def test_the_rephrase_ask_names_the_claims_that_were_refused() -> None:
    """A whole beat is worth more than one stubborn claim, and the threshold holds."""
    from scripts.reauthor_cleanroom import rephrase_request

    payload = json.dumps(
        rephrase_request(source="A passage.", lifted=["on the eastern side of the Pont-Neuf"])
    )
    assert "on the eastern side of the Pont-Neuf" in payload
    assert "eight consecutive words" in payload


def test_the_rephrase_ask_still_shows_the_source() -> None:
    """It is a decomposition, not a paraphrase of a claim in isolation."""
    from scripts.reauthor_cleanroom import rephrase_request

    assert "A passage." in json.dumps(rephrase_request(source="A passage.", lifted=["x"]))


def test_the_length_target_comes_from_the_beat_being_replaced() -> None:
    """Removing the old body removed the only anchor on length; one integer restores it."""
    from scripts.reauthor_cleanroom import MIN_TARGET_WORDS, target_words

    assert target_words(" ".join(["word"] * 94)) == 90
    assert target_words("short") == MIN_TARGET_WORDS


def test_the_length_target_carries_no_wording() -> None:
    """An integer is not expression: the seam still holds with it in the request."""
    for beat in _real_records():
        payload = json.dumps(
            cleanroom_request(
                claims=_CLAIMS,
                poi=beat.get("poi_name", ""),
                city="Paris",
                words=target_words(beat.get("script_body") or ""),
            )
        )
        assert max_verbatim_run(payload, beat["source_passage"]) < VERBATIM_RUN_BLOCK


def test_the_writer_may_not_hand_an_observation_to_an_invented_source() -> None:
    """ "Some visitors find" fabricates a source for one writer's impression."""
    payload = json.dumps(cleanroom_request(claims=_CLAIMS, poi="P", city="Paris", words=90))
    assert "some visitors find" in payload
    assert "invents a source" in payload


def test_the_writer_may_not_invent_a_physical_detail() -> None:
    """Freed from the old body, the first sample told listeners to look at a widening."""
    payload = json.dumps(cleanroom_request(claims=_CLAIMS, poi="P", city="Paris", words=90))
    assert "the roadway does not widen" in payload
