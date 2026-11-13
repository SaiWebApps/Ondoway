"""validation.py: source-traceability + forbidden-phrase scan."""

from __future__ import annotations

import datetime as _dt

from src.tour.contract import (
    BeatRef,
    BeatSequence,
    POIBeats,
    Script,
    ScriptPOI,
    Sentence,
    TourInput,
    ValidationReport,
)
from src.tour.generation import (
    GLUE_CLOSING,
    GLUE_NAV,
    GLUE_PACING,
    SYNTHESIZED_OPENER,
)
from src.tour.validation import validate_script

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _beat(
    bid: str, *, body: str, lenses: tuple[str, ...] = (), nf: str | None = None
) -> BeatRef:
    return BeatRef(
        id=bid,
        poi_id="p1",
        word_count=len(body.split()),
        lenses=lenses,
        script_body=body,
        narrative_function=nf,
    )


def _seq(beats: list[BeatRef]) -> BeatSequence:
    return BeatSequence(
        poi_beats=(
            POIBeats(
                poi_id="p1",
                poi_name="Place des Vosges",
                ordering_strategy="trigger_address",
                beats=tuple(beats),
            ),
        )
    )


def _input() -> TourInput:
    return TourInput(
        start=(48.8555, 2.3656),
        duration_min=60,
        city_slug="paris",
        round_trip=True,
    )


def _script(sentences: list[Sentence]) -> Script:
    return Script(
        city_slug="paris",
        generated_at=_dt.datetime(2026, 4, 28, tzinfo=_dt.UTC).isoformat(),
        inputs=_input(),
        total_audio_seconds=0,
        total_walking_seconds=0,
        total_walk_distance_m=0,
        total_planned_seconds=0,
        selected_pois=(
            ScriptPOI(
                id="p1", name="Place des Vosges", tier=5, lat=48.85, lng=2.36, area="Le Marais"
            ),
        ),
        lens_coverage={},
        script=tuple(sentences),
        validation=ValidationReport(),
    )


# ---------------------------------------------------------------------------
# Source-traceability
# ---------------------------------------------------------------------------


def test_traceable_beat_sentence_passes():
    beat = _beat("b1", body="Henri IV built it.")
    seq = _seq([beat])
    s = Sentence(text="Henri IV built it.", source_id=beat.id, source_type="beat", stop_idx=0)
    report = validate_script(_script([s]), seq)
    assert report.untraceable_sentences == ()
    assert report.passed


def test_unknown_beat_id_is_untraceable():
    beat = _beat("real", body="Real beat.")
    seq = _seq([beat])
    bad = Sentence(text="Spurious.", source_id="not-a-real-id", source_type="beat", stop_idx=0)
    report = validate_script(_script([bad]), seq)
    assert any(s.source_id == "not-a-real-id" for s in report.untraceable_sentences)


def test_fused_sentence_with_valid_also_cites_traces():
    a = _beat("A", body="One telling.")
    b = _beat("B", body="Another telling.")
    seq = _seq([a, b])
    fused = Sentence(text="Fused telling.", source_id="A", also_cites=("B",),
                     source_type="beat", stop_idx=0)
    report = validate_script(_script([fused]), seq)
    assert report.untraceable_sentences == ()


def test_fused_sentence_with_an_unknown_also_cites_is_untraceable():
    a = _beat("A", body="One telling.")
    seq = _seq([a])
    fused = Sentence(text="Fused telling.", source_id="A", also_cites=("ghost",),
                     source_type="beat", stop_idx=0)
    report = validate_script(_script([fused]), seq)
    assert fused in report.untraceable_sentences


def test_glue_with_unknown_label_is_untraceable():
    beat = _beat("b1", body="Cite this.")
    seq = _seq([beat])
    bad = Sentence(text="Walk on.", source_id="GLUE_BOGUS", source_type="glue", stop_idx=0)
    report = validate_script(_script([bad]), seq)
    assert any(s.source_id == "GLUE_BOGUS" for s in report.untraceable_sentences)


def test_glue_with_whitelisted_label_passes():
    beat = _beat("b1", body="Cite this.")
    seq = _seq([beat])
    ok = [
        Sentence(text="Walk on.", source_id=GLUE_NAV, source_type="glue", stop_idx=0),
        Sentence(text="Settle in.", source_id=GLUE_PACING, source_type="glue", stop_idx=0),
        Sentence(text="End the walk here.", source_id=GLUE_CLOSING, source_type="glue", stop_idx=0),
    ]
    report = validate_script(_script(ok), seq)
    assert report.untraceable_sentences == ()


# ---------------------------------------------------------------------------
# Forbidden phrases — only scan glue, not beats
# ---------------------------------------------------------------------------


def test_imagine_in_glue_is_flagged():
    beat = _beat("b1", body="A history beat.")
    seq = _seq([beat])
    bad = Sentence(
        text="Imagine the river flowing here.",
        source_id=GLUE_NAV,
        source_type="glue",
        stop_idx=0,
    )
    report = validate_script(_script([bad]), seq)
    hit_codes = [code for _, code in report.forbidden_phrase_hits]
    assert any(c == "forbidden_phrase:imagine" for c in hit_codes)
    assert not report.passed


def test_picture_this_in_glue_is_flagged():
    beat = _beat("b1", body="A history beat.")
    seq = _seq([beat])
    bad = Sentence(
        text="Picture this scene.",
        source_id=GLUE_NAV,
        source_type="glue",
        stop_idx=0,
    )
    report = validate_script(_script([bad]), seq)
    codes = [code for _, code in report.forbidden_phrase_hits]
    assert any(c == "forbidden_phrase:picture this" for c in codes)


def test_imagine_inside_beat_body_is_not_flagged():
    # Corpus is canonical; only glue is scanned for forbidden phrases.
    beat = _beat("b1", body="Hugo asked the reader to imagine the cathedral aflame.")
    seq = _seq([beat])
    s = Sentence(
        text="Hugo asked the reader to imagine the cathedral aflame.",
        source_id=beat.id,
        source_type="beat",
        stop_idx=0,
    )
    report = validate_script(_script([s]), seq)
    assert report.forbidden_phrase_hits == ()


# ---------------------------------------------------------------------------
# Proper-noun + year leakage in glue
# ---------------------------------------------------------------------------


def test_glue_introducing_proper_noun_not_in_beats_is_flagged():
    # Beats don't mention "Napoleon" — glue tries to. RE-DERIVED: the fixture
    # used the NAV label as a convenient carrier; GLUE_NAV is
    # now exempt from the proper-noun half (the map names places by nature), so
    # the smuggler is a STORY line — the check's teeth are about story glue.
    beat = _beat("b1", body="Henri IV built it.")
    seq = _seq([beat])
    bad = Sentence(
        text="They walked past the statue of Napoleon after the coronation.",
        source_id=GLUE_PACING,
        source_type="glue",
        stop_idx=0,
    )
    report = validate_script(_script([bad]), seq)
    codes = [code for _, code in report.forbidden_phrase_hits]
    assert any(c == "new_proper_noun:Napoleon" for c in codes)


def test_glue_naming_a_proper_noun_already_in_beats_is_ok():
    beat = _beat("b1", body="Hugo lived at no. 6 from 1832.")
    seq = _seq([beat])
    s = Sentence(text="Hugo's house was here.", source_id=GLUE_NAV, source_type="glue", stop_idx=0)
    cited_beat_sentence = Sentence(
        text="Hugo lived at no. 6 from 1832.", source_id=beat.id, source_type="beat", stop_idx=0
    )
    report = validate_script(_script([cited_beat_sentence, s]), seq)
    # No new_proper_noun hits — Hugo is in cited corpus.
    codes = [c for _, c in report.forbidden_phrase_hits]
    assert not any(c == "new_proper_noun:Hugo" for c in codes)


def test_glue_introducing_new_year_is_flagged():
    beat = _beat("b1", body="Henri IV built it in 1612.")
    seq = _seq([beat])
    cited = Sentence(
        text="Henri IV built it in 1612.", source_id=beat.id, source_type="beat", stop_idx=0
    )
    bad = Sentence(text="That was 1789.", source_id=GLUE_NAV, source_type="glue", stop_idx=0)
    report = validate_script(_script([cited, bad]), seq)
    codes = [c for _, c in report.forbidden_phrase_hits]
    assert any(c == "new_year:1789" for c in codes)


def test_glue_referring_to_year_already_in_beats_passes():
    beat = _beat("b1", body="Henri IV built it in 1612.")
    seq = _seq([beat])
    cited = Sentence(
        text="Henri IV built it in 1612.", source_id=beat.id, source_type="beat", stop_idx=0
    )
    glue = Sentence(text="That was 1612.", source_id="ARITH", source_type="arith", stop_idx=0)
    report = validate_script(_script([cited, glue]), seq)
    codes = [c for _, c in report.forbidden_phrase_hits]
    assert not any(c.startswith("new_year:") for c in codes)


# ---------------------------------------------------------------------------
# Realistic mixed cases
# ---------------------------------------------------------------------------


def test_clean_script_passes_both_gates():
    beat = _beat("b1", body="Henri IV built the square. The inauguration was in 1612.")
    seq = _seq([beat])
    sentences = [
        Sentence(text="Settle in.", source_id=GLUE_PACING, source_type="glue", stop_idx=0),
        Sentence(
            text="Henri IV built the square.", source_id=beat.id, source_type="beat", stop_idx=0
        ),
        Sentence(
            text="The inauguration was in 1612.", source_id=beat.id, source_type="beat", stop_idx=0
        ),
        Sentence(
            text="You've now circled the square.",
            source_id=GLUE_CLOSING,
            source_type="glue",
            stop_idx=0,
        ),
    ]
    report = validate_script(_script(sentences), seq)
    assert report.passed


def test_synthesized_opener_pointing_at_poi_name_is_traceable():
    beat = _beat("b1", body="The cathedral is Gothic.")
    seq = _seq([beat])
    syn = Sentence(
        text="You're standing at Place des Vosges.",
        source_id=SYNTHESIZED_OPENER,
        source_type="glue",
        stop_idx=0,
    )
    report = validate_script(_script([syn]), seq)
    # POI name itself is canonical — included via _cited_beat_corpus_text
    # so "Place" / "Vosges" are not flagged.
    codes = [c for _, c in report.forbidden_phrase_hits]
    assert not any("new_proper_noun" in c for c in codes)
    assert syn not in report.untraceable_sentences


# ---------------------------------------------------------------------------
# Cited beats' key_claims join the canonical context
# ---------------------------------------------------------------------------


def _claims_beat(bid: str, *, body: str, key_claims: tuple[str, ...]) -> BeatRef:
    return BeatRef(
        id=bid,
        poi_id="p1",
        word_count=len(body.split()),
        script_body=body,
        key_claims=key_claims,
    )


def test_reflection_may_quote_proper_nouns_from_cited_key_claims():
    """A reflection naming a proper noun/year that appears only in a CITED
    beat's key_claims (not its script_body) is canonical, not invention."""
    beat = _claims_beat(
        "b1",
        body="The king finished the square.",  # no name, no year
        key_claims=("Henri IV completed the square in 1612",),
    )
    seq = _seq([beat])
    sentences = [
        Sentence(text="The king finished the square.", source_id="b1",
                 source_type="beat", stop_idx=0),
        Sentence(text="So it was Henri IV who gave you this view, back in 1612.",
                 source_id="GLUE_REFLECTION", source_type="glue", stop_idx=0),
    ]
    report = validate_script(_script(sentences), seq)
    codes = [c for _, c in report.forbidden_phrase_hits]
    assert not any("new_proper_noun" in c or "new_year" in c for c in codes)


def test_uncited_beat_claims_do_not_join_canonical_context():
    """key_claims of a beat that is NOT cited in the script stay invisible —
    a glue sentence quoting them is still flagged as invention."""
    cited = _claims_beat("b1", body="A plain fact.", key_claims=())
    uncited = _claims_beat(
        "b2", body="Unused.", key_claims=("Napoleon was crowned in 1804",)
    )
    seq = _seq([cited, uncited])
    sentences = [
        Sentence(text="A plain fact.", source_id="b1", source_type="beat", stop_idx=0),
        Sentence(text="Think of Napoleon, crowned in 1804.",
                 source_id="GLUE_REFLECTION", source_type="glue", stop_idx=0),
    ]
    report = validate_script(_script(sentences), seq)
    codes = [c for _, c in report.forbidden_phrase_hits]
    assert any("new_proper_noun:Napoleon" in c for c in codes)
    assert any("new_year:1804" in c for c in codes)


def test_a_possessive_of_a_licensed_name_is_not_an_invention():
    """The invention scan licenses the names the cited beats carry; an INFLECTED form of
    a licensed name — "Ravaillac's knife" for a beat that says "Francis Ravaillac" — is
    the same name, not a new one.

    The fact-gates stay (traceability, entailment, claim-coverage, provenance), and the
    gate must not punish naming what the listener would be told. MEASURED 2026-08-19 (a
    day composed over the wire): the reflection "…died under Francis Ravaillac's knife" was
    refused with `new_proper_noun:Ravaillac's` although "Francis Ravaillac assassinated
    Henri IV" is a key claim of a voiced beat at the stop before — a deterministic 422 on
    the phone's compose whenever the composer writes the possessive. The tokenizer keeps
    the apostrophe-s inside the token, so "Ravaillac's" ≠ "Ravaillac"; the same day's
    next attempt refused "André Maurois" against a corpus that spells "Andre". UNDO:
    compare the raw token only -> RED. A genuinely new name in the same sentence is
    still flagged.
    """
    body = "The fanatical Catholic who stabbed him, Francis Ravaillac, was not persuaded."
    beat = _beat("b1", body=body)
    seq = _seq([beat])
    cited = Sentence(
        text="The fanatical Catholic who stabbed him, Francis Ravaillac, was not persuaded.",
        source_id=beat.id,
        source_type="beat",
        stop_idx=0,
    )
    # (Carriers RE-DERIVED from GLUE_NAV to a story label —
    # NAV is now exempt from the proper-noun scan; these lines are story glue.)
    possessive = Sentence(
        text="He gave Paris its oldest bridge and then died under Ravaillac's knife.",
        source_id=GLUE_PACING,
        source_type="glue",
        stop_idx=0,
    )
    report = validate_script(_script([cited, possessive]), seq)
    codes = [c for _, c in report.forbidden_phrase_hits]
    assert not any("new_proper_noun" in c for c in codes), codes

    # The curly apostrophe the composer also emits, and the plural possessive.
    for text in (
        "He died under Ravaillac\u2019s knife.",
        "Walk on past the Ravaillacs' street.",
    ):
        s = Sentence(text=text, source_id=GLUE_PACING, source_type="glue", stop_idx=0)
        codes = [c for _, c in validate_script(_script([cited, s]), seq).forbidden_phrase_hits]
        assert not any("new_proper_noun" in c for c in codes), (text, codes)

    # A re-accented form is the same name: the corpus spells "Andre Maurois", the
    # composer writes "André" (measured, the same day: `new_proper_noun:André`).
    andre = _beat("b2", body="Andre Maurois places Henri IV among France's heroes.")
    seq2 = _seq([beat, andre])
    cited2 = Sentence(
        text="Andre Maurois places Henri IV among France's heroes.",
        source_id=andre.id,
        source_type="beat",
        stop_idx=0,
    )
    accented = Sentence(
        text="But don't mistake him for a lightweight; André Maurois ranks him among the heroes.",
        source_id=GLUE_PACING,
        source_type="glue",
        stop_idx=0,
    )
    report = validate_script(_script([cited, cited2, accented]), seq2)
    codes = [c for _, c in report.forbidden_phrase_hits]
    assert not any("new_proper_noun" in c for c in codes), codes

    # A name the beats never carry is still an invention, possessive or not.
    invented = Sentence(
        text="The king died under Clément's knife, as the story goes.",
        source_id=GLUE_PACING,
        source_type="glue",
        stop_idx=0,
    )
    codes = [c for _, c in validate_script(_script([cited, invented]), seq).forbidden_phrase_hits]
    assert any(c.startswith("new_proper_noun:Cl") for c in codes), codes


# ---------------------------------------------------------------------------
# The forward-promise check: a check refuses a forward-promise phrase pointing
# past the stop.
# ---------------------------------------------------------------------------


def _two_stop_script(sentences: list[Sentence]) -> Script:
    base = _script(sentences)
    return base.model_copy(
        update={
            "selected_pois": (
                *base.selected_pois,
                ScriptPOI(
                    id="p2", name="Hôtel de Sully", tier=4, lat=48.854, lng=2.362,
                    area="Le Marais",
                ),
            )
        }
    )


def test_a_forward_promise_in_story_glue_is_refused_and_navigation_is_not():
    """A stop's text may NAME its neighbour as a fact but never
    PROMISE it — the session may trade the next stop away, and a promise to a place the
    walker never reaches is a small shut door (measured: 'That's a
    question for our next stop'). The check refuses the inherently forward phrases in
    story glue, and 'you'll see' only when it points PAST the stop (names a later
    stop) — 'you'll see' at the thing in front of you is direction-giving, and a
    validation failure on a correct tour is worse than no check. GLUE_NAV is the map
    speaking and is exempt; the corpus is canonical (beat text is never scanned).
    UNDO: drop the forward-promise scan from ``_forbidden_phrase_hits`` -> RED."""
    beat = _beat("b1", body="Henri IV built the square. Sixty years later it was renamed.")
    seq = _seq([beat])

    def glue(text: str, *, label: str = GLUE_PACING, stop: int = 0) -> Sentence:
        return Sentence(text=text, source_id=label, source_type="glue", stop_idx=stop)

    # Refused: promises pointing forward, in any story glue (a close included).
    for text in (
        "We'll go inside at our next stop.",
        "More on him at the next stop.",
        "In a minute you'll be standing where he died.",
        "And that's the square — wait until you see what comes next.",
    ):
        report = validate_script(_two_stop_script([glue(text)]), seq)
        assert any(
            code.startswith("forward_promise:") for _, code in report.forbidden_phrase_hits
        ), f"not refused: {text!r}"
        assert not report.passed

    # Refused: "you'll see" pointing past the stop — it names a LATER stop.
    report = validate_script(
        _two_stop_script([glue("You'll see the Hôtel de Sully in all its glory.")]), seq
    )
    assert any(code.startswith("forward_promise:") for _, code in report.forbidden_phrase_hits)

    # NOT refused: "you'll see" at the thing in front of you (no later stop named).
    report = validate_script(
        _two_stop_script([glue("Look up and you'll see the carved initials.")]), seq
    )
    assert report.forbidden_phrase_hits == ()

    # NOT refused: the map speaking (GLUE_NAV names the destination; that is its job) —
    # and the corpus is canonical ("Sixty years later" in beat text is history).
    nav = glue("Next, walk two minutes to the Hôtel de Sully.", label=GLUE_NAV)
    story = Sentence(
        text="Henri IV built the square. Sixty years later it was renamed.",
        source_id="b1", source_type="beat", stop_idx=0,
    )
    report = validate_script(_two_stop_script([story, nav]), seq)
    assert report.forbidden_phrase_hits == ()


def test_the_citys_own_vocabulary_and_the_maps_voice_are_not_inventions():
    """Measured on one test day (three 422s, the day DEAD): the
    invention scan refused "the height of Parisian luxury" in an authored close
    (new_proper_noun:Parisian — the tour's own CITY as an invention) and the nav
    line's "Seine" (new_proper_noun:Seine — the MAP naming the river it routes
    along). The city's name and demonym are the walk's own vocabulary; GLUE_NAV
    is navigation, not story (the forward-promise scan already exempts it) — its
    proper nouns are places by nature. STORY glue naming an unlicensed place is
    still refused: the teeth stay. UNDO: drop either exemption -> RED.

    RE-DERIVED: the ORIGINAL nav line
    ("Walk northwest along the Seine") is itself no longer legal — a leg line
    never speaks compass — so the licensed-proper-noun clause now rides a
    compass-free wording of the same sentence."""
    beat = _beat("b1", body="A shop rose here in 1855. It sold silk.")
    seq = _seq([beat])

    def glue(text: str, *, label: str = GLUE_CLOSING) -> Sentence:
        return Sentence(text=text, source_id=label, source_type="glue", stop_idx=0)

    # The city's own name and demonym, in an authored close: licensed.
    report = validate_script(
        _script([glue("That's the shop — the height of Parisian luxury, in Paris.")]), seq
    )
    assert report.forbidden_phrase_hits == (), report.forbidden_phrase_hits

    # The map naming the river it walks along: navigation, not invention.
    report = validate_script(
        _script([glue("Follow the Seine for about ten minutes.",
                      label=GLUE_NAV)]), seq
    )
    assert report.forbidden_phrase_hits == (), report.forbidden_phrase_hits

    # The teeth stay: STORY glue inventing the same river is still refused.
    report = validate_script(
        _script([glue("The Seine froze solid that winter.", label=GLUE_PACING)]), seq
    )
    assert any(code.startswith("new_proper_noun") for _s, code in report.forbidden_phrase_hits)

    # A hyphenated compound whose head the corpus carries is the same name in
    # adjectival dress (measured: "Roman-style" refused against
    # a corpus saying "Roman" freely); an unlicensed head still flags.
    roman = _beat("b2", body="A Roman triumphal arch honoured the emperor.")
    seq2 = _seq([roman])
    cited = Sentence(text="A Roman triumphal arch honoured the emperor.",
                     source_id=roman.id, source_type="beat", stop_idx=0)
    ok = glue("From a Roman-style arch to a steel store.", label=GLUE_PACING)
    report = validate_script(_script([cited, ok]), seq2)
    assert report.forbidden_phrase_hits == (), report.forbidden_phrase_hits
    # ("See" fills the sentence-start slot the scanner deliberately skips.)
    bad = glue("See the Gothic-era doorway that survives.", label=GLUE_PACING)
    report = validate_script(_script([cited, bad]), seq2)
    assert any(code.startswith("new_proper_noun") for _s, code in report.forbidden_phrase_hits)


# ---------------------------------------------------------------------------
# The writer's words match the geometry they play in.
# The nav-voice compass ban is part of the always-on scan (source-derivable);
# the placement floors need route/placement context and ride
# ``placement_floor_hits``, wired by the authoring finalizer's closure.
# ---------------------------------------------------------------------------


def _nav(text: str, stop: int = 1) -> Sentence:
    return Sentence(text=text, source_id=GLUE_NAV, source_type="glue", stop_idx=stop)


def test_a_nav_line_never_speaks_compass():
    """A leg line names direction ONLY as left/right/straight-ahead
    or a visible landmark — NEVER compass. Measured instances: "Walk
    northwest along the Seine"; "written northwest, five minutes; measured
    south-south-east, nine". UNDO: drop the compass scan from
    ``_forbidden_phrase_hits`` -> RED."""
    beat = _beat("b1", body="A history beat.")
    seq = _seq([beat])
    for text in (
        "Walk northwest along the Seine for about ten minutes.",
        "Head north on the quai.",
        "The gate is to the south-east.",
        "Continue west, then east again.",
    ):
        report = validate_script(_script([_nav(text, stop=0)]), seq)
        codes = [c for _, c in report.forbidden_phrase_hits]
        assert any(c.startswith("leg_voice_compass:") for c in codes), (text, codes)
        assert not report.passed

    # The legal vocabulary is untouched: relative direction and landmarks.
    for text in (
        "Turn left at the corner and follow the quai.",
        "Cross the bridge ahead, straight on to the tower.",
        "Follow the Seine for about ten minutes.",
    ):
        report = validate_script(_script([_nav(text, stop=0)]), seq)
        codes = [c for _, c in report.forbidden_phrase_hits]
        assert not any(c.startswith("leg_voice_compass:") for c in codes), (text, codes)

    # Compass words INSIDE story prose are the corpus's own business (beat text is
    # never scanned), and story glue may say "the north tower" — the ban is the
    # NAV VOICE's alone.
    story = Sentence(
        text="The north tower held the bell.", source_id=GLUE_PACING,
        source_type="glue", stop_idx=0,
    )
    report = validate_script(_script([story]), seq)
    codes = [c for _, c in report.forbidden_phrase_hits]
    assert not any(c.startswith("leg_voice_compass:") for c in codes), codes


def _floor_script(sentences: list[Sentence]) -> Script:
    return _script(sentences)


def test_placement_floor_flags_wrong_minutes_in_the_nav_line():
    """A leg line speaks minutes ONLY as the routed leg's own priced
    number (the measured fault: "written northwest, five minutes; measured
    south-south-east, nine"). A nav line naming any other minute count is a hit;
    the routed number itself, or no number at all, is clean; a leg with no
    routed minutes known is skipped, never guessed."""
    from src.tour.validation import placement_floor_hits

    script = _floor_script([
        _nav("Leaving the square behind, head for the tower, about a five-minute walk away."),
    ])
    hits = placement_floor_hits(
        script, vignette_beat_ids=frozenset(), leg_minutes_by_stop={1: 9},
        goes_inside_by_stop={},
    )
    assert any(code.startswith("leg_voice_minutes:") for _, code in hits), hits

    for text, minutes in (
        ("Leaving the square behind, head for the tower, about a 9-minute walk away.", {1: 9}),
        ("Carry on to the tower, just ahead.", {1: 9}),
        ("Head for the tower, about a five-minute walk.", {}),  # unknown leg: skipped
    ):
        clean = placement_floor_hits(
            _floor_script([_nav(text)]), vignette_beat_ids=frozenset(),
            leg_minutes_by_stop=minutes, goes_inside_by_stop={},
        )
        assert not any(code.startswith("leg_voice_minutes:") for _, code in clean), (text, clean)


def test_placement_floor_flags_arrived_words_on_leg_pieces():
    """No standing verb or arrived deictic — here, this, you're
    standing, look up — in ANY leg piece, checked sentence by sentence, never
    the opening alone. The measured class: 4 of 27 leg sentences were
    standing lines (at the Louvre, on the Rivoli, on a quai, +1). Applies to
    every sentence that PLAYS on a leg: nav glue, the thread, and a walk-past
    vignette one-liner (beat-sourced, identified by id)."""
    from src.tour.validation import placement_floor_hits

    vignette = Sentence(
        text="Here stood the fortress that held the crown.",
        source_id="vig-1", source_type="beat", stop_idx=1,
    )
    thread = Sentence(
        text="You're standing where the tribunal sat.",
        source_id="GLUE_REFLECTION", source_type="glue", stop_idx=1,
    )
    later_sentence_nav = _nav("Carry on past the gate. Look up at the rose window.")
    for sentence in (vignette, thread, later_sentence_nav):
        hits = placement_floor_hits(
            _floor_script([sentence]), vignette_beat_ids=frozenset({"vig-1"}),
            leg_minutes_by_stop={}, goes_inside_by_stop={},
        )
        assert any(code.startswith("arrived_word_on_leg:") for _, code in hits), (
            sentence.text, hits,
        )

    # "From here" at the leg's start is true where it plays (the final-destination
    # template's own words), and the SAME arrived words at a STOP piece are fine.
    ok_nav = _nav("From here, make your way to your final destination.")
    stop_piece = Sentence(
        text="You're standing where the tribunal sat.", source_id="b1",
        source_type="beat", stop_idx=1,
    )
    for sentence in (ok_nav, stop_piece):
        hits = placement_floor_hits(
            _floor_script([sentence]), vignette_beat_ids=frozenset(),
            leg_minutes_by_stop={}, goes_inside_by_stop={},
        )
        assert not any(code.startswith("arrived_word_on_leg:") for _, code in hits), (
            sentence.text, hits,
        )


def test_placement_floor_flags_moving_lines_in_auto_played_stop_pieces():
    """No imperative of motion in any AUTO-PLAYED stop
    piece — moving sentences are tap-only or leg-only. The measured instances:
    a story sentence "Step around the corner to 31 rue de Bellechasse",
    route directions inside story fields. A tap-only stop (the full telling) is exempt; the nav line
    is exempt (navigation is its job)."""
    from src.tour.validation import placement_floor_hits

    moving_story = Sentence(
        text="Step around the corner to 31 rue de Bellechasse.",
        source_id="b1", source_type="beat", stop_idx=0,
    )
    hits = placement_floor_hits(
        _floor_script([moving_story]), vignette_beat_ids=frozenset(),
        leg_minutes_by_stop={}, goes_inside_by_stop={},
    )
    assert any(code.startswith("moving_line_auto_played:") for _, code in hits), hits

    # Tap-only stop: the same sentence is legal (moving lines are tap-only or leg-only).
    hits = placement_floor_hits(
        _floor_script([moving_story]), vignette_beat_ids=frozenset(),
        leg_minutes_by_stop={}, goes_inside_by_stop={},
        tap_only_stops=frozenset({0}),
    )
    assert hits == [], hits

    # The nav line commands movement by design; a still stop piece is clean.
    ok_nav = _nav("Walk on to the tower, just ahead.")
    still_story = Sentence(
        text="The corner house at 31 rue de Bellechasse was hers.",
        source_id="b1", source_type="beat", stop_idx=0,
    )
    for sentence in (ok_nav, still_story):
        hits = placement_floor_hits(
            _floor_script([sentence]), vignette_beat_ids=frozenset(),
            leg_minutes_by_stop={}, goes_inside_by_stop={},
        )
        assert not any(code.startswith("moving_line_auto_played:") for _, code in hits), (
            sentence.text, hits,
        )


def test_the_walks_own_transit_beat_plays_on_the_leg_not_the_stop():
    """A Sébastopol transit line was refused `moving_line_auto_played`
    because the floor read the walk's own corpus narration as stationary stop
    content: `_build_transit` picks a transit beat FOR the leg, but the
    placement rule knew only glue labels and vignette ids. With the transit ids
    supplied the sentence takes the LEG branch — motion imperatives are true
    where they play — and the leg branch's own floors still apply (arrived
    deixis stays refused). Omitting the ids keeps the conservative stationary
    default. UNDO: drop `transits` from the floor's is_walk_concurrent call
    -> the moving line is flagged again -> RED."""
    from src.tour.validation import placement_floor_hits

    walk_line = Sentence(
        text="Walk up the boulevard and turn right into the square.",
        source_id="t-1", source_type="beat", stop_idx=1,
    )
    hits = placement_floor_hits(
        _floor_script([walk_line]), vignette_beat_ids=frozenset(),
        leg_minutes_by_stop={}, goes_inside_by_stop={},
        transit_beat_ids=frozenset({"t-1"}),
    )
    assert hits == [], hits
    hits = placement_floor_hits(
        _floor_script([walk_line]), vignette_beat_ids=frozenset(),
        leg_minutes_by_stop={}, goes_inside_by_stop={},
    )
    assert any(code.startswith("moving_line_auto_played:") for _, code in hits), hits
    arrived = Sentence(
        text="You're standing before the tower now.",
        source_id="t-1", source_type="beat", stop_idx=1,
    )
    hits = placement_floor_hits(
        _floor_script([arrived]), vignette_beat_ids=frozenset(),
        leg_minutes_by_stop={}, goes_inside_by_stop={},
        transit_beat_ids=frozenset({"t-1"}),
    )
    assert any(code.startswith("arrived_word_on_leg:") for _, code in hits), hits


def test_placement_floor_allows_a_door_line_only_where_the_wire_says_door():
    """The door rule: a through-the-door sentence plays only where
    the wire says door=true. At a stop the plan prices INSIDE, "step inside" is
    staging; anywhere else it is an invitation through a door that does not
    exist (measured at the Bourse: the words invited a visitor into a closed rotunda)."""
    from src.tour.validation import placement_floor_hits

    door_line = Sentence(
        text="Step inside and let your eyes adjust.",
        source_id="GLUE_STAGING", source_type="glue", stop_idx=0,
    )
    hits = placement_floor_hits(
        _floor_script([door_line]), vignette_beat_ids=frozenset(),
        leg_minutes_by_stop={}, goes_inside_by_stop={0: False},
    )
    assert any(code.startswith("door_line_without_door:") for _, code in hits), hits

    hits = placement_floor_hits(
        _floor_script([door_line]), vignette_beat_ids=frozenset(),
        leg_minutes_by_stop={}, goes_inside_by_stop={0: True},
    )
    assert not any(code.startswith("door_line_without_door:") for _, code in hits), hits


def test_placement_floor_flags_a_fusion_that_crosses_playback_contexts():
    """One measured mechanism of wrong placement: the writer's
    FUSE-REPEATS rule merged a stop STORY sentence with a walk-past VIGNETTE
    beat's fact, producing a citation that cannot be placed —
    ``partition_final_script`` then exploded with a bare ValueError and the
    phone saw an unnamed refusal, which is never allowed. The floor NAMES it
    at verification time, so the bounded retry tells the writer which sentence
    to unfuse."""
    from src.tour.validation import placement_floor_hits

    fused = Sentence(
        text="The square was royal, and the fortress on your way in held the crown.",
        source_id="b1", source_type="beat", stop_idx=1, also_cites=("vig-1",),
    )
    hits = placement_floor_hits(
        _floor_script([fused]), vignette_beat_ids=frozenset({"vig-1"}),
        leg_minutes_by_stop={}, goes_inside_by_stop={},
    )
    assert any(code == "fused_across_playback_contexts" for _, code in hits), hits

    # A pure vignette citation and a pure story citation are both clean.
    pure_vignette = Sentence(
        text="The fortress held the crown.", source_id="vig-1",
        source_type="beat", stop_idx=1,
    )
    pure_story = Sentence(
        text="The square was royal.", source_id="b1",
        source_type="beat", stop_idx=1, also_cites=("b2",),
    )
    for sentence in (pure_vignette, pure_story):
        hits = placement_floor_hits(
            _floor_script([sentence]), vignette_beat_ids=frozenset({"vig-1"}),
            leg_minutes_by_stop={}, goes_inside_by_stop={},
        )
        assert not any(code == "fused_across_playback_contexts" for _, code in hits), (
            sentence.text, hits,
        )


# ---------------------------------------------------------------------------
# A transit beat's bearing is checked where it plays. The corpus is
# canonical about what a place IS; it is not canonical about which way THIS
# route runs. A guidebook's walking directions encode the walk the guidebook
# took, and reused on a different segment they point the other way.
# ---------------------------------------------------------------------------


def test_a_transit_beat_that_speaks_compass_is_refused_like_any_other_leg_line():
    """The measured shape: 15 of the corpus's 99 transit beats carry an absolute
    bearing ("exit the square at the northwest corner"). They reach the walker as stop
    text, so the glue-only compass scan never saw them and the ban that protects every
    other leg line did not apply."""
    beat = _beat("t1", body="Leave the square by its north-east corner.", nf="transition")
    sentence = Sentence(
        text="Leave the square by its north-east corner.",
        source_id="t1",
        source_type="beat",
        stop_idx=1,
    )
    report = validate_script(_script([sentence]), _seq([beat]))
    assert any(
        code.startswith("leg_voice_compass:") for _s, code in report.forbidden_phrase_hits
    ), report.forbidden_phrase_hits


def test_a_story_beat_may_still_say_north_because_the_corpus_owns_its_facts():
    """The exemption narrows to bearings on transit beats and nothing else: a beat that
    tells you the north portal is where the kings stand is a fact about the building,
    checked by editorial review, and never a direction to walk in."""
    beat = _beat("s1", body="The north portal carries the Virgin.", nf="deepen")
    sentence = Sentence(
        text="The north portal carries the Virgin.",
        source_id="s1",
        source_type="beat",
        stop_idx=1,
    )
    report = validate_script(_script([sentence]), _seq([beat]))
    assert report.forbidden_phrase_hits == ()


def test_a_transit_beat_without_a_bearing_is_left_alone():
    beat = _beat("t2", body="Cross the road and carry on past the church.", nf="transition")
    sentence = Sentence(
        text="Cross the road and carry on past the church.",
        source_id="t2",
        source_type="beat",
        stop_idx=1,
    )
    report = validate_script(_script([sentence]), _seq([beat]))
    assert report.forbidden_phrase_hits == ()
