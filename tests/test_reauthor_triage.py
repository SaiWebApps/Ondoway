"""Two free gates that re-sort the re-authored backlog.

The panel that cleared 384 rewrites included the model that wrote them, so what
those approvals mean is unknown. These gates cost nothing and are deterministic,
which is why they run first: whatever they block needs no further spend to be
disqualified.

The tests below pin the two gate contracts and the false-negative channels that
the gates are known to have — a limit that is asserted is a limit that stays
visible, and one nobody can later mistake for coverage.
"""

from __future__ import annotations

from scripts.corpus_report import (
    VERBATIM_RUN_BLOCK,
    attributed_quote_spans,
    max_verbatim_run,
    quoted_char_spans,
    run_outside_quotation,
    shared_run_at,
    verbatim_words,
)
from scripts.reauthor_triage import (
    BLOCKED_BOTH,
    BLOCKED_CONFLICT,
    BLOCKED_RUN,
    CLEAR,
    _content,
    name_conflicts,
    numeric_conflicts,
    numeric_slots,
    order_follows_source,
    proper_names,
    run_shape,
    summarise,
    triage,
)

_LIFT = "the bridge soon became symbolic of the city itself drawing large crowds"


# ── the run measure ──────────────────────────────────────────────────────────


def test_a_lifted_clause_inside_a_long_body_is_found() -> None:
    """The defect verbatim_ratio cannot see: one lift diluted by original prose."""
    source = f"So impressive was it that {_LIFT} to the water."
    body = " ".join(["Nothing here is borrowed at all and every word is new."] * 6)
    body += f" And yet {_LIFT}."
    assert max_verbatim_run(body, source) >= VERBATIM_RUN_BLOCK


def test_ordinary_phrasing_is_not_a_lift() -> None:
    """A short shared run is how English works, not evidence of copying."""
    assert max_verbatim_run("It stands at the edge of the river.", "At the edge of a field.") < 5


def test_an_empty_side_scores_zero() -> None:
    assert max_verbatim_run("", "anything at all") == 0
    assert max_verbatim_run("anything at all", "") == 0


def test_the_run_reports_where_it_starts() -> None:
    """A reader is shown the lift, not a number claiming there was one."""
    length, index = shared_run_at(
        verbatim_words("new words then a stone bridge over the river"),
        verbatim_words("a stone bridge over the river stood here"),
    )
    assert length == 6
    assert index == 3  # 'a stone bridge over the river', after 'new words then'


def test_punctuation_does_not_hide_a_lift() -> None:
    """A moved comma must not read as unshared wording."""
    source = "pedlars, book-sellers, dog-barbers and tooth-pullers set up stalls here"
    body = "pedlars book sellers dog barbers and tooth pullers set up stalls here"
    assert max_verbatim_run(body, source) >= VERBATIM_RUN_BLOCK


# ── the quotation exemption ──────────────────────────────────────────────────


def test_an_attributed_quotation_is_exempt() -> None:
    """Quoting Colette and naming her is not copying the guidebook that quoted her."""
    quote = "the essential drama resides there and not in death which is a banal defeat"
    source = f'Colette said: "{quote}"'
    body = f'Late in life Colette wrote: "{quote}"'
    assert run_outside_quotation(body, source)["length"] < VERBATIM_RUN_BLOCK


def test_a_quotation_attributed_to_nobody_is_not_exempt() -> None:
    """An unattributed quote is the shape of an unmarked lift; it earns nothing."""
    quote = "the essential drama resides there and not in death which is a banal defeat"
    source = f'Colette said: "{quote}"'
    body = f'It is worth remembering: "{quote}"'
    assert run_outside_quotation(body, source)["length"] >= VERBATIM_RUN_BLOCK


def test_narration_lifted_alongside_a_quotation_still_blocks() -> None:
    """The exemption covers the quoted words, never the sentence that introduces them."""
    quote = "the firmament unfolded and Julian rose up into the blue of space"
    source = f'A figure who turns out to be Christ takes Julien in his arms and then "{quote}"'
    body = (
        f'A figure who turns out to be Christ takes Julien in his arms — Flaubert wrote "{quote}"'
    )
    assert run_outside_quotation(body, source)["length"] >= VERBATIM_RUN_BLOCK


def test_an_attribution_interrupting_a_quotation_does_not_manufacture_a_lift() -> None:
    """Cutting at the quote leaves the four-word insert, not one run straddling both."""
    body = '"I love France and I love Paris," he told a friend, "and I hoped to stay longer."'
    source = '"I love France and I love Paris," he told a friend, "and I hoped to stay longer."'
    assert run_outside_quotation(body, source)["length"] < VERBATIM_RUN_BLOCK


def test_a_possessive_apostrophe_does_not_open_a_quotation() -> None:
    """Otherwise one possessive exempts every word after it."""
    assert quoted_char_spans("Colette's garden and Rodin's bronzes stand here.") == []


def test_an_unpaired_quote_mark_exempts_nothing() -> None:
    """A stray opener must not run to the end of the body and clear it."""
    assert quoted_char_spans('She wrote: "and then nothing closed it') == []


def test_attribution_is_read_from_either_side_of_the_quote() -> None:
    text = '"Bill, how high can you make it?" Raskob asked.'
    assert attributed_quote_spans(text)


# ── the run's shape ──────────────────────────────────────────────────────────


def test_a_list_of_names_is_named_as_such() -> None:
    """A blocked run of proper nouns is shared fact; it stays blocked and is labelled."""
    body = "paintings by Monet, Van Gogh and Degas and sculpture by Constantin Brancusi"
    assert run_shape("monet van gogh and degas and sculpture by constantin brancusi", body) == (
        "names-and-numbers"
    )


def test_ordinary_prose_is_named_as_prose() -> None:
    body = "so impressive was the bridge that it soon became a symbol of the city"
    assert run_shape("so impressive was the bridge that it soon became a symbol", body) == "prose"


# ── the conflict gate ────────────────────────────────────────────────────────


def test_a_number_the_source_answers_differently_is_a_conflict() -> None:
    """The shipped pier number the rewrite silently adopted the source's value for."""
    source = "The RMS Titanic was headed to Pier 60 and the Carpathia brought survivors to Pier 54."
    before = "The RMS Titanic was headed to Pier 59 and the Carpathia brought survivors to Pier 54."
    found = numeric_conflicts(source, before)
    # One disagreement can surface under more than one naming word; what matters is
    # that the figure the body asserts and the source denies is reported.
    assert "pier" in [c["slot"] for c in found]
    assert all(c["body_before"] == ["59"] for c in found)


def test_a_trailing_unit_is_a_slot_too() -> None:
    """20,000 sq ft against 50,000 sq ft: the noun follows the number here."""
    source = "The tower provided an extra 50,000 sq ft of exhibition space."
    before = "The tower provided an extra 20,000 sq ft of exhibition space."
    assert numeric_conflicts(source, before)


def test_a_number_the_source_never_mentions_is_not_a_conflict() -> None:
    """Absence is a possible loss, which is a different question and a different gate."""
    source = "The tower was added to the east side of the museum."
    before = "The tower added 20,000 sq ft of exhibition space."
    assert numeric_conflicts(source, before) == []


def test_a_fuller_source_is_not_a_conflict() -> None:
    """One-directional: the source knowing more than the body costs nothing."""
    source = "It opened in 1910 and closed in 1935."
    before = "It opened in 1910."
    assert numeric_conflicts(source, before) == []


def test_common_words_are_not_slot_keys() -> None:
    """'the 1793' would put every date in the passage in one bucket."""
    assert "the" not in numeric_slots("the 1793 decree and the 1804 coronation")


def test_thousands_separators_do_not_split_a_number() -> None:
    assert numeric_slots("50,000 sq ft")["sq"] == {"50000"}


def test_one_substituted_token_in_a_name_is_a_conflict() -> None:
    source = "A bust of Charles Darwin stands in the hall."
    before = "A bust of Charles Dickens stands in the hall."
    assert name_conflicts(source, before) == [
        {"body_before": "charles dickens", "source": "charles darwin"}
    ]


def test_a_name_absent_from_the_source_is_not_a_conflict() -> None:
    """Only a disagreement blocks. An unmentioned name is a loss question."""
    source = "A bust stands in the hall."
    before = "A bust of Charles Dickens stands in the hall."
    assert name_conflicts(source, before) == []


def test_a_sentence_leading_capital_is_not_a_name() -> None:
    """Otherwise every sentence's first word joins the name set and collides."""
    assert ("stands",) not in proper_names("Stands here a bust of Darwin.")


def test_a_wholly_renamed_thing_is_a_known_false_negative() -> None:
    """The one-substitution rule buys explainability with recall, and this is the cost."""
    source = "The ship was the RMS Carpathia."
    before = "The ship was the SS Nomadic."
    assert name_conflicts(source, before) == []


# ── the reported diagnostic ──────────────────────────────────────────────────


def test_a_rewrite_in_the_sources_order_is_reported_as_such() -> None:
    source = "The bridge opened in 1607. Crowds gathered on it. Pedlars set up stalls."
    body = "It opened in 1607. Crowds came to it. Pedlars worked stalls there."
    assert order_follows_source(source, body) is True


def test_a_reordered_rewrite_is_reported_as_such() -> None:
    source = "The bridge opened in 1607. Crowds gathered on it. Pedlars set up stalls."
    body = "Pedlars worked stalls there. Crowds came to it. It opened in 1607."
    assert order_follows_source(source, body) is False


def test_a_short_source_has_no_order_to_follow() -> None:
    assert order_follows_source("One sentence only.", "Anything at all.") is None


def test_order_is_reported_and_never_gated() -> None:
    """A rewrite that follows the source's order is still CLEAR; Stage 1 owns this."""
    record = {
        "beat_id": "b",
        "source_passage": "The bridge opened in 1607. Crowds gathered. Pedlars set up stalls.",
        "body_before": "The bridge opened in 1607. Crowds gathered. Pedlars set up stalls.",
        "body_after": "It opened in 1607. People came. Pedlars worked stalls.",
    }
    row = triage(record)
    assert row["order_follows_source"] is True
    assert row["verdict"] == CLEAR


# ── the verdicts ─────────────────────────────────────────────────────────────


def _record(**over: object) -> dict:
    base = {
        "beat_id": "b",
        "source_passage": "A quiet passage about a bridge over a river in a city.",
        "body_before": "A quiet passage about a bridge over a river in a city.",
        "body_after": "Wholly different words describing a crossing above water downtown.",
    }
    base.update(over)  # type: ignore[arg-type]
    return base


def test_a_clean_rewrite_clears_both_gates() -> None:
    assert triage(_record())["verdict"] == CLEAR


def test_a_lift_blocks() -> None:
    row = triage(_record(body_after="A quiet passage about a bridge over a river in a city."))
    assert row["verdict"] == BLOCKED_RUN


def test_a_conflict_blocks_even_with_no_lift() -> None:
    row = triage(
        _record(
            source_passage="The Carpathia landed survivors at Pier 54.",
            body_before="The Carpathia landed survivors at Pier 59.",
            body_after="Survivors came ashore from that ship at berth fifty-four.",
        )
    )
    assert row["verdict"] == BLOCKED_CONFLICT


def test_both_failures_are_reported_together() -> None:
    row = triage(
        _record(
            source_passage="The Carpathia landed its survivors at Pier 54 in the complex here.",
            body_before="The Carpathia landed its survivors at Pier 59 in the complex here.",
            body_after="The Carpathia landed its survivors at Pier 54 in the complex here.",
        )
    )
    assert row["verdict"] == BLOCKED_BOTH


def test_a_blocked_beat_can_never_auto_approve() -> None:
    """The gate's whole job: no verdict but CLEAR is eligible to skip a person."""
    assert BLOCKED_RUN != CLEAR and BLOCKED_CONFLICT != CLEAR and BLOCKED_BOTH != CLEAR


def test_the_summary_splits_by_the_refuted_panels_verdict() -> None:
    """384 approvals and 140 escalations are different populations with different fates."""
    rows = [
        {**triage(_record()), "was": "pass"},
        {
            **triage(_record(body_after="A quiet passage about a bridge over a river in a city.")),
            "was": "escalate",
        },
    ]
    summary = summarise(rows)
    assert summary["was_auto_approved"]["total"] == 1
    assert summary["was_auto_approved"][CLEAR] == 1
    assert summary["was_escalated"][BLOCKED_RUN] == 1


# ── precision, found by hand-auditing what the gate flagged on the real files ──


def test_a_pronoun_is_not_a_slot_key() -> None:
    """Voltaire: source "(1694-1778), he", body "in 1694, he". Both say born 1694."""
    source = 'Born François-Marie Arouet (1694-1778), he took up "Voltaire" as a pen name.'
    before = 'Born François-Marie Arouet in 1694, he took up "Voltaire" as a pen name.'
    assert numeric_conflicts(source, before) == []


def test_a_slot_does_not_reach_across_a_sentence_boundary() -> None:
    """Both texts say €6; only a dropped sentence put a different number near 'Wines'."""
    source = (
        "Several sell for under €6. You can get a good bottle for €12. "
        "Wines of the month are cheap."
    )
    before = "Several sell for under €6. Wines of the month are cheap."
    assert numeric_conflicts(source, before) == []


def test_a_one_word_name_is_never_a_conflict() -> None:
    """With one token, 'differs in exactly one position' is true of every pair."""
    source = "A plaque remembers Aragon."
    before = "A plaque remembers Molière."
    assert name_conflicts(source, before) == []


def test_a_restored_accent_is_not_a_renaming() -> None:
    """The scan lost the circumflex; the body has it. One building, one name."""
    source = "The Hotel Chenizot stands on the island."
    before = "The Hôtel Chenizot stands on the island."
    assert name_conflicts(source, before) == []


def test_a_scanning_error_in_the_source_still_conflicts() -> None:
    """'Charles Gamier' is what the rewrite would adopt, so it must reach a person."""
    source = "The opera house was designed by Charles Gamier."
    before = "The opera house was designed by Charles Garnier."
    assert name_conflicts(source, before)


def test_a_date_behind_a_preposition_is_still_compared() -> None:
    """61% of the corpus's years sit as "in YYYY"; an adjacent-word rule sees none."""
    assert numeric_conflicts("The theatre opened in 1934.", "The theatre opened in 1932.")


def test_an_ordinal_century_is_a_figure() -> None:
    """mid-18th against mid-19th moves a fact a hundred years; str.isdigit sees neither."""
    source = "Chinatown was founded in the mid-18th century by immigrants."
    before = "Chinatown was founded in the mid-19th century by immigrants."
    assert numeric_conflicts(source, before)


def test_an_ordinal_and_a_plain_number_are_different_values() -> None:
    """Otherwise "18th" and the year 18 would be read as agreeing."""
    assert numeric_slots("the 18th century")["century"] == {"18th"}


def test_a_quotation_pointed_at_but_not_attributed_is_not_exempt() -> None:
    """ "the words ..." names quoted text without saying whose it is."""
    quote = "I love you"
    body = f'A wall here carries the words "{quote}" in 311 languages for you to find.'
    source = f'The wall carries the words "{quote}" in 311 languages for you to find.'
    assert run_outside_quotation(body, source)["length"] >= VERBATIM_RUN_BLOCK


def test_function_words_do_not_match_two_unrelated_sentences() -> None:
    """A bag of stopwords must not count as content shared with a source sentence."""
    assert _content("The one of the that it was") == set()
