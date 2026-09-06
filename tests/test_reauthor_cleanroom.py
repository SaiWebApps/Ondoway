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
    apparatus_claims,
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
    uncovered_sentences,
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


def test_the_second_ask_names_every_kind_of_problem() -> None:
    """A whole beat is worth more than one bad claim, and no threshold moves."""
    from scripts.reauthor_cleanroom import redo_problems

    problems = redo_problems(
        {
            "lifted_claims": ["on the eastern side of the Pont-Neuf"],
            "dangling_claims": ["That uprising alarmed the kings."],
            "uncovered_sentences": ["He read French literature."],
        }
    )
    assert "on the eastern side of the Pont-Neuf" in problems
    assert "That uprising alarmed the kings." in problems
    assert "He read French literature." in problems


def test_the_second_ask_still_shows_the_source() -> None:
    """It is a decomposition, not a paraphrase of a claim in isolation."""
    from scripts.reauthor_cleanroom import redo_request

    assert "A passage." in json.dumps(redo_request(source="A passage.", problems="x"))


def test_a_claim_that_points_at_its_neighbour_is_refused() -> None:
    """The set is shuffled, so "That uprising" has nothing left to refer to."""
    from scripts.reauthor_cleanroom import dangling_claims

    assert dangling_claims([{"claim": "That uprising alarmed the kings.", "kind": "fact"}])
    assert dangling_claims([{"claim": "It was in 1794 that the doors opened.", "kind": "fact"}])


def test_a_demonstrative_that_is_not_an_anaphor_passes() -> None:
    """ "There is a Metro station" and "That is the oldest" name their own subjects."""
    from scripts.reauthor_cleanroom import dangling_claims

    assert (
        dangling_claims([{"claim": "There is a Metro station called Pont Neuf.", "kind": "f"}])
        == []
    )
    assert dangling_claims([{"claim": "That is the oldest bridge in Paris.", "kind": "fact"}]) == []


def test_source_content_no_claim_carries_is_refused() -> None:
    """Omission is the decomposer's dangerous failure: it looks like silence."""
    source = (
        "The gardens honour Pope John XXIII. He was the papal nuncio in Paris. "
        "Parisians remember his long walks through the city with friends."
    )
    claims = [{"claim": "The gardens are a tribute to Pope John XXIII.", "kind": "fact"}]
    missed = uncovered_sentences(claims, source)
    assert any("long walks" in s for s in missed)


def test_a_claim_set_that_carries_every_sentence_is_covered() -> None:
    source = "The gardens honour Pope John XXIII. Parisians remember his long walks."
    claims = [
        {"claim": "The gardens are a tribute to Pope John XXIII.", "kind": "fact"},
        {"claim": "Parisians recall the long walks John XXIII took.", "kind": "fact"},
    ]
    assert uncovered_sentences(claims, source) == []


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


def test_a_sentence_split_across_several_claims_is_covered() -> None:
    """The union, never the best single claim.

    Real strings from `data/paris/claims.json`: one source sentence became three
    claims, each carrying a third of it. The best single claim reaches 0.22 and the
    union reaches 0.40, so taking the maximum refused a decomposition that lost
    nothing — and that shape is exactly what the prompt asks the decomposer for.
    """
    from src.tour.claim_dedup import COVERAGE_MATCH_MIN, _overlap, _signature

    source = (
        "He is always shown indicating a wound on his leg, and with his companion, "
        "a dog who brought him sustenance."
    )
    claims = [
        {
            "claim": "The patron saint of plague victims is invariably depicted pointing "
            "out a wound on his leg.",
            "kind": "fact",
        },
        {
            "claim": "The patron saint of plague victims is invariably depicted "
            "accompanied by a dog.",
            "kind": "fact",
        },
        {
            "claim": "The dog shown with the patron saint of plague victims is said to "
            "have supplied the saint with food.",
            "kind": "fact",
        },
    ]
    sig = _signature(source)
    assert max(_overlap(sig, _signature(c["claim"])) for c in claims) < COVERAGE_MATCH_MIN, (
        "fixture must be one no single claim covers, or it proves nothing"
    )
    assert uncovered_sentences(claims, source) == []


def test_a_fragment_is_not_reported_as_lost_content() -> None:
    """ "Anything wiggling?" states no fact, so its absence buys a retry that fixes nothing."""
    claims = [{"claim": "Fresh fish reaches Paris daily from Channel ports.", "kind": "fact"}]
    assert uncovered_sentences(claims, "Anything wiggling?") == []


def test_the_better_of_two_attempts_is_the_one_kept() -> None:
    """A second ask can come back worse; paying for that and keeping it is the trap."""
    from scripts.reauthor_cleanroom import _problem_count

    first = {"lifted_claims": ["a"], "dangling_claims": [], "uncovered_sentences": []}
    worse = {"lifted_claims": [], "dangling_claims": ["b"], "uncovered_sentences": ["c"]}
    assert _problem_count(first) < _problem_count(worse)
    assert min((worse, first), key=_problem_count) is first


def test_a_body_whose_claims_changed_is_not_kept() -> None:
    """A second ask changes the claim block; the body written before it is stale."""
    given = shuffled_claims(_CLAIMS, "b")
    entry = {"beat_id": "b", "poi_name": "P", "source_passage": "S.", "model": "m"}
    record = cleanroom_record(entry, body_before="old", given=given, body_after="new")
    reasked = [*_CLAIMS, {"claim": "A claim the second ask added.", "kind": "fact"}]
    assert record["claims_sha256"] != input_hash(shuffled_claims(reasked, "b"))


def test_a_reverted_second_ask_is_still_recorded_as_having_happened() -> None:
    """Otherwise a set re-asked and reverted looks identical to one never re-asked."""
    from scripts.reauthor_cleanroom import _problem_count

    first = {"lifted_claims": ["a"], "dangling_claims": [], "uncovered_sentences": []}
    worse = {"lifted_claims": ["a"], "dangling_claims": ["b"], "uncovered_sentences": []}
    kept = min((worse, first), key=_problem_count)
    kept["second_ask"] = True
    kept["second_ask_improved"] = kept is worse
    assert kept is first
    assert kept["second_ask"] is True
    assert kept["second_ask_improved"] is False


# ── what the pipeline PRODUCED, not what it was told ─────────────────────────
#
# The prompt-text assertions above prove an instruction was sent. They would all pass
# against a model that ignored every one of them, which is how "0 first person" was
# reported while four finished bodies said "before we go on". These read the output.


def _written_bodies() -> list[dict]:
    from scripts.reauthor_cleanroom import cleanroom_path

    out = []
    for city in ("paris", "new_york"):
        path = cleanroom_path(city)
        if path.is_file():
            out += json.loads(path.read_text(encoding="utf-8"))
    if not out:
        pytest.skip("no regenerated bodies on disk to check")
    return out


def _shipped_bodies() -> list[dict]:
    """The bodies that would reach the corpus — a refused body is not one of them."""
    from scripts.reauthor_cleanroom import regrade_bodies

    return [r for r in regrade_bodies(_written_bodies()) if r["usable"]]


def test_a_body_that_speaks_as_a_person_is_refused() -> None:
    """ "sit down for a moment before we go on" is a guide the listener does not have."""
    from scripts.reauthor_cleanroom import body_problems

    problems = body_problems(
        "That's the stop: sit down for a moment before we go on.",
        [{"claim": "There are benches.", "kind": "fact"}],
    )
    assert problems["first_person"]


def test_a_roman_numeral_is_not_first_person() -> None:
    """ "Napoleon I", "World War I" and "I. M. Pei" are names, and refusing them
    would throw away correct bodies to catch a pronoun that is not there."""
    from scripts.reauthor_cleanroom import first_person_sentences

    for body in (
        "The eagle was the symbol of Empire for Napoleon I and his nephew.",
        "I. M. Pei received the commission for the pyramid.",
        "By the peak of World War I the area had fallen out of fashion.",
    ):
        assert first_person_sentences(body) == [], body


def test_a_quoted_speaker_may_say_i() -> None:
    """A person quoted in a beat is entitled to the first person; the tour is not."""
    from scripts.reauthor_cleanroom import first_person_sentences

    quoted = 'Lamb recalled it plainly: "I told him it would stand."'
    assert first_person_sentences(quoted) == []


def test_an_imperative_a_claim_asked_for_is_kept() -> None:
    """A passage of walking directions puts the instruction in the claims, and a body
    following them reports the route rather than staging the listener."""
    from scripts.reauthor_cleanroom import stage_directions

    directions = [{"claim": "From the gate you turn left into rue de Harlay.", "kind": "fact"}]
    assert stage_directions("Turn left into rue de Harlay.", directions) == []
    assert stage_directions("Stand here long enough and the park shifts.", directions)


def test_an_impression_is_refused_where_no_claim_holds_one() -> None:
    """With no observation in the set there is no impression to offer."""
    from scripts.reauthor_cleanroom import invented_impressions

    facts = [{"claim": "The square was laid out in 1830.", "kind": "fact"}]
    assert invented_impressions("You may find the square quiet.", facts)
    seen = [*facts, {"claim": "The author finds the square quiet.", "kind": "observation"}]
    assert invented_impressions("You may find the square quiet.", seen) == []


def test_a_scene_the_claims_do_not_mention_is_flagged_not_refused() -> None:
    """Roughly a third of these are the body's own word for something a claim names,
    so this reaches a reviewer rather than throwing a correct body away."""
    from scripts.reauthor_cleanroom import cleanroom_record, scene_details

    given = [{"claim": "People play card games outdoors in the park.", "kind": "fact"}]
    assert scene_details("Cards are dealt on its benches and tables.", given) == [
        "benches",
        "tables",
    ]
    record = cleanroom_record(
        {"beat_id": "b", "poi_name": "P", "source_passage": "A park."},
        body_before="old body",
        given=given,
        body_after="Cards are dealt on its benches and tables.",
    )
    assert record["scene_details"]
    assert record["usable"] is True


def test_a_refused_body_is_marked_unusable_not_shipped() -> None:
    """A body carries its own refusal, the way a claim set does."""
    from scripts.reauthor_cleanroom import cleanroom_record

    record = cleanroom_record(
        {"beat_id": "b", "poi_name": "P", "source_passage": "A square."},
        body_before="old body here",
        given=[{"claim": "The square was laid out in 1830.", "kind": "fact"}],
        body_after="Stand here and we can look at the square together.",
    )
    assert record["usable"] is False
    assert record["body_problems"]["first_person"]


def test_no_shipped_body_breaks_a_voice_rule() -> None:
    """Read on the corpus rather than on a fixture: what ships is what the gates pass."""
    from scripts.reauthor_cleanroom import body_problems

    shipped = _shipped_bodies()
    offenders = [
        (r["poi_name"], kind)
        for r in shipped
        for kind, lines in body_problems(r["body_after"], r["claims_given"]).items()
        if lines
    ]
    assert not offenders, f"{len(offenders)} shipped bodies break a rule: {offenders[:5]}"
    # The refusal is what makes the assertion above true, so a gate that refused nearly
    # everything would satisfy it while producing no corpus. Both halves have to hold.
    written = _written_bodies()
    assert len(shipped) > len(written) * 0.8, f"{len(shipped)} of {len(written)} bodies survive"


def test_the_seam_holds_on_the_bodies_actually_written() -> None:
    """Rebuild the real writer payload from each stored record and measure it.

    The synthetic-claims test above proves the prompt scaffolding is clean. This proves
    the 453 payloads that were actually sent were.
    """
    from scripts.reauthor_cleanroom import city_name

    for record in _written_bodies():
        source = record.get("source_passage") or ""
        payload = json.dumps(
            cleanroom_request(
                claims=record["claims_given"],
                poi=record.get("poi_name", ""),
                city=city_name("paris"),
                words=target_words(record.get("body_before") or ""),
            ),
            ensure_ascii=False,
        )
        assert source not in payload
        assert (record.get("body_before") or "") not in payload
        assert max_verbatim_run(payload, source) < VERBATIM_RUN_BLOCK


def test_every_written_body_is_bound_to_the_claims_it_carries() -> None:
    for record in _written_bodies():
        assert input_hash(record["claims_given"]) == record["claims_sha256"]


def test_the_coverage_gates_recall_is_measured_not_assumed() -> None:
    """A gate whose miss rate is unknown is the failure LEARNINGS #24 records."""
    from scripts.reauthor_cleanroom import coverage_recall

    sets = [
        {
            "usable": True,
            "source_passage": (
                "A bridge crosses the river. Pedlars sold books here. Acrobats performed."
            ),
            "claims": [
                {"claim": "A bridge spans the river.", "kind": "fact"},
                {"claim": "Pedlars sold books on the bridge.", "kind": "fact"},
                {"claim": "Acrobats performed on the bridge.", "kind": "fact"},
            ],
        }
    ]
    result = coverage_recall(sets)
    assert result["sets"] == 1
    assert "one_claim_deleted_recall_pct" in result


def test_a_claim_about_the_guidebook_is_refused() -> None:
    """The listener has no guidebook, no page 139 and no step nine."""
    from scripts.reauthor_cleanroom import apparatus_claims

    assert apparatus_claims([{"claim": "Riverside Park is covered on page 139.", "kind": "fact"}])
    assert apparatus_claims([{"claim": "The directions form step 9 of the walk.", "kind": "fact"}])
    assert apparatus_claims([{"claim": "The author advises lingering here.", "kind": "fact"}])


def test_a_claim_about_the_place_is_not_apparatus() -> None:
    assert (
        apparatus_claims([{"claim": "The bridge became a symbol of Paris.", "kind": "fact"}]) == []
    )
