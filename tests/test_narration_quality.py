"""The narration quality LINT: it must (a) discriminate obvious stilted prose from
good spoken narration, (b) NOT fire on the known false-positive classes, and (c) be
HONEST about what it cannot do (it is content-blind). $0.
"""

from __future__ import annotations

from src.tour.narration_quality import score_narration

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


# ---- false-positive regressions ----


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

