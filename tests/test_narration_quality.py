"""The narration quality LINT: it must (a) discriminate obvious stilted prose from
good spoken narration, (b) NOT fire on the false-positive classes a hostile review
found, and (c) be HONEST about what it cannot do (it is content-blind). $0.
"""

from __future__ import annotations

from src.tour.narration_quality import (
    _CAUSAL_CHAIN,
    _MOTIVATED_TRANSITION,
    craft_score,
    score_narration,
)

# Rick-Steves-style: short varied sentences, second person, sentence-initial look
# prompt, ends on an image.
_GOOD = (
    "You're standing where Paris began. Look up. Those bell towers are two hundred "
    "feet tall. Builders broke ground in 1163, hoping their great-great-grandchildren "
    "might see it done. The dedication mass came two centuries later. Notre-Dame took "
    "the long view."
)

# Essayistic/AI-sounding: significance-close, puffery, AI vocab, empty transition,
# negative parallelism, uniform long sentences, no "you".
_STILTED = (
    "Notre-Dame Cathedral stands as a testament to the vibrant spirit of medieval "
    "faith and devotion. Furthermore, this iconic monument, constructed between 1163 "
    "and 1345, boasts an intricate tapestry of Gothic architecture that continues to "
    "underscore its enduring significance across the passing centuries of history. It "
    "is not just a building, but a symbol of a rich cultural heritage that reminds us "
    "of humanity's timeless aspiration toward something greater than itself."
)


def test_scorer_separates_good_from_stilted_on_both_axes():
    good, stilted = score_narration(_GOOD), score_narration(_STILTED)
    assert good.stilted_score < stilted.stilted_score
    assert good.engagement_score > stilted.engagement_score
    assert stilted.stilted_score - good.stilted_score >= 0.25


def test_stilted_tells_are_caught_and_attributed():
    q = score_narration(_STILTED)
    assert "moralizing_closer" in q.hits  # "stands as a testament" / "reminds us of"
    assert "puffery" in q.hits
    assert "ai_vocab" in q.hits
    assert "empty_transition" in q.hits  # Furthermore
    assert "negative_parallelism" in q.hits


def test_good_narration_has_no_penalty_hits():
    q = score_narration(_GOOD)
    assert q.hits == {}, q.hits
    assert q.second_person_rate > 0
    assert q.look_prompt_rate > 0  # "Look up." opens a sentence


# ---- false-positive regressions (each is a verified hostile-review finding) ----


def test_factual_serves_as_is_not_moralizing():
    """'serves as a museum/archive/stables' is FACTUAL, the most common tour
    construction — it must NOT be flagged as a moralizing closer (was FP, weight 1.6)."""
    for t in [
        "Today the palace serves as the city archive.",
        "The building now serves as a museum.",
        "It served as the king's stables for a century.",
    ]:
        assert "moralizing_closer" not in score_narration(t).hits, t


def test_moralizing_still_fires_on_a_significance_noun():
    for t in ["The dome endures as a symbol of hope.", "It stands as a testament to their faith."]:
        assert "moralizing_closer" in score_narration(t).hits, t


def test_abbreviations_do_not_corrupt_sentence_count():
    """Reuses the real sentence splitter, so 'St.' / 'Mr.' don't split a sentence
    (which would corrupt burstiness — London narration is full of 'St.')."""
    q = score_narration("St. Paul's dominates the skyline that Mr. Wren gave the city.")
    assert q.n_sentences == 1, q.n_sentences


def test_measurements_are_not_counted_as_dates_but_real_years_are():
    measured = score_narration("The tower is 1063 feet tall and 2000 workers built it.")
    assert measured.per_100w["year_density"] == 0.0
    assert score_narration("The dedication mass came in 1345.").per_100w["year_density"] > 0


def test_em_dash_counts_typographic_not_a_numeric_range():
    assert score_narration("Walk 100 - 200 metres to the gate.").per_100w["em_dash"] == 0.0
    assert score_narration("The reign — long and cruel — ended here.").per_100w["em_dash"] > 0


def test_negative_parallelism_catches_the_contraction_form():
    assert "negative_parallelism" in score_narration("It isn't just a bridge, it's a stage.").hits


def test_no_double_count_of_testament_or_nestled():
    """'testament'/'nestled' are scored once (moralizing/puffery), NOT also ai_vocab."""
    q = score_narration("It stands as a testament to the village nestled below.")
    assert "ai_vocab" not in q.hits, q.hits
    assert "moralizing_closer" in q.hits and "puffery" in q.hits


def test_look_prompt_requires_a_sentence_initial_imperative():
    """Nouns/auxiliaries ('the picture hangs', 'trade would stop', 'the find') are NOT
    look-prompts; only a sentence-opening imperative counts."""
    assert score_narration(
        "The picture hangs in the Louvre. Trade would stop here. The find made news."
    ).look_prompt_rate == 0.0
    assert score_narration("Look at the tower. Notice the carving.").look_prompt_rate == 1.0


def test_burstiness_low_for_uniform_sentences():
    uniform = "The cat sat down. The dog ran fast. The bird flew high. The fish swam deep."
    varied = "Stop. The cathedral you see took two centuries to build, stone by stone. Look."
    assert score_narration(uniform).burstiness < score_narration(varied).burstiness


# ---- honesty: what the lint CANNOT do (documented, not hidden) ----


def test_composite_is_content_blind_a_known_limitation():
    """DOCUMENTED LIMITATION: the composite reads only SURFACE features, so vacuous
    'you'-stuffed, length-varied drivel is NOT flagged. This test pins that we KNOW
    this — the composite must never be presented as a content-quality oracle; real
    quality is judged by a human/acceptance read of the narration."""
    drivel = (
        "You look. You see the old big thing that people made long ago because they "
        "wanted a big old nice thing for people to look at. You walk. You stop. It is "
        "very good and very old and you like it a lot."
    )
    q = score_narration(drivel)
    assert q.stilted_score < 0.2 and q.engagement_score > 0.5  # scores 'great' though empty


def test_short_texts_are_flagged_unreliable_for_the_composite():
    assert score_narration("Look up. The tower is old.").reliable is False
    long_text = " ".join(["The cathedral you see took two centuries to build."] * 40)
    assert score_narration(long_text).reliable is True


def test_scorer_is_deterministic():
    a, b = score_narration(_STILTED), score_narration(_STILTED)
    assert a.stilted_score == b.stilted_score and a.engagement_score == b.engagement_score


# ---- craft_score: the reliability ranker for best-of-N (writing-craft rules) ----

def test_craft_score_ranks_wellwritten_above_flat_and_repetitive():
    """The core property best-of-N relies on: among candidate composes of one stop, a
    well-written one out-scores a flat monotone one AND a repetitive one. This is what
    converts run-to-run LLM variance into a reliable pick. UNDO: make craft_score return
    a constant -> the ordering collapses -> RED."""
    good = ("Look up. That bell has only tolled for the darkest days — one of them was the "
            "morning after 9/11. Forged in 1685, Emmanuel was spared in the Revolution when "
            "its sister bell was melted for cannon.")
    flat = ("The bell is called Emmanuel. The bell was forged in 1685. The bell is in the "
            "south tower. The bell was spared in the Revolution. The bell is very large.")
    repet = ("Prisoners were tortured in this tower. In this tower prisoners were tortured "
             "and you could hear them. The tower is where prisoners were tortured, their "
             "screams carrying across the river.")
    assert craft_score(good) > craft_score(flat)
    assert craft_score(good) > craft_score(repet)


def test_craft_score_rewards_motivated_transition_over_bare_adjacency():
    """G2 proxy (motivated transitions): a transition that STATES why the
    next idea follows ("To understand why...") must outrank the same facts joined by
    bare-adjacency filler ("Also,"). The two fixtures are IDENTICAL except for that one
    connective — isolating the G2 signal from confounds (second-person, percussion,
    burstiness) rather than relying on a large weight to overcome them (a hostile
    review found the previous 1.5 weight both double-counted with G3 and swamped
    unrelated axes; G2/G3 are now bounded per-sentence-fraction terms at weight 0.2 —
    see craft_score's docstring)."""
    motivated = (
        "Merchants traded here for centuries. To understand why, picture the noble "
        "ladies who shopped nearby. Their patronage drew a loyal following who wanted "
        "the same luxuries at home."
    )
    bare = (
        "Merchants traded here for centuries. Also, picture the noble ladies who "
        "shopped nearby. Their patronage drew a loyal following who wanted the same "
        "luxuries at home."
    )
    assert craft_score(motivated) > craft_score(bare)


def test_craft_score_rewards_causal_chain_over_flat_list():
    """G3 proxy (causal chain not list): facts that EARN each other
    ("in turn brought...") must outrank the same facts as a flat list. The two
    fixtures are IDENTICAL except for the connective — isolating the G3 signal from
    confounds rather than relying on an oversized weight (see the G2 test above for
    why: the previous 1.5 weight both double-counted with G2's "however" and had to
    be that large only to swamp unrelated axes)."""
    chain = (
        "Jewellers opened here first. Merchants drawing an English clientele set up "
        "shops nearby. That demand in turn brought the tailors who followed soon "
        "after."
    )
    flat = (
        "Jewellers opened here first. Also, an English clientele filled these shops. "
        "Also, the tailors who followed opened soon after."
    )
    assert craft_score(chain) > craft_score(flat)


def test_however_is_not_double_counted():
    """DEFECT (hostile review, 2026-07-19): 'however' appeared in BOTH
    _MOTIVATED_TRANSITION (sentence-initial) and _CAUSAL_CHAIN, so a single
    sentence-initial 'However,' was scored twice. MEASURED before the fix: adding one
    'However,' to a 49-word text moved craft_score by +5.99 (base 0.204 -> 6.2) —
    a reward-hacking vector where best-of-N would favour whichever candidate sprinkles
    more connectives. MEASURED after the fix: the swing is +0.196 (0.204 -> 0.4).
    UNDO: reintroduce '|however\\b' into _CAUSAL_CHAIN (double-counting it with
    _MOTIVATED_TRANSITION again) -> this assertion goes RED."""
    text = (
        "Merchants traded in this square for three centuries, selling wool and spice "
        "to travellers who arrived from every corner of the kingdom, and their stalls "
        "lined the cobbles where you now stand looking up at the old stone tower that "
        "still bears their guild mark carved above the door."
    )
    base = craft_score(text)
    with_however = craft_score("However, " + text)
    swing = round(with_however - base, 3)
    # Bounded: at most one sentence's worth of motivated_rate (weight 0.2, one sentence
    # among several) should move the score. A double-counted 'however' swings ~4-6.
    assert swing < 1.0, swing
    # Disjointness itself, independent of the bound: a bare "However," sentence must
    # match ONLY the G2 pattern, never also the G3 pattern (the actual double-count).
    # UNDO: add '|however\\b' back into _CAUSAL_CHAIN -> this assertion goes RED.
    assert _MOTIVATED_TRANSITION.search("However, the crowd cheered.")
    assert not _CAUSAL_CHAIN.search("However, the crowd cheered.")


def test_cleft_causal_matches_the_gold_sentence():
    """DEFECT (hostile review, 2026-07-19): the cleft-causal branch of
    _MOTIVATED_TRANSITION was documented as 'modelled on the standard's own gold
    example' but capped the gap between 'here' and 'that' at 60 chars, while the gold
    sentence needs 74:
    'It was here, supplying Empress Eugenie and the other ladies of the Napoleonic
    court, that French haute couture was born...'. Widened to 90. UNDO: shrink the cap
    back to 60 -> this assertion goes RED."""
    gold_sentence = (
        "It was here, supplying Empress Eugenie and the other ladies of the "
        "Napoleonic court, that French haute couture was born in the second half of "
        "the nineteenth century."
    )
    assert _MOTIVATED_TRANSITION.search(gold_sentence) is not None


def test_craft_score_penalizes_restatement():
    """'The same point twice in new words is padding' (writing-craft). Two versions with
    identical facts differ only in whether a fact is restated — the non-repeating one wins."""
    once = ("The Tour Bonbec held the torture chamber. Restorers later found two oubliettes "
            "spiked with iron below it. Say no more about who went in.")
    twice = ("The Tour Bonbec held the torture chamber. The Tour Bonbec is where the torture "
             "chamber was. Restorers found two oubliettes spiked with iron below it.")
    assert craft_score(once) > craft_score(twice)
