"""Tests for the three extraction helpers landed alongside the
B11/B12 prompt patch (2026-05-01).

Covers:
- scripts/beat_builder.py: deterministic construction, hash + duration_sec
  + beat_id derivation, default fills.
- scripts/extract_validators.py: source-span gate (B12),
  length-class enforcement, fabrication probe (B11).
- scripts/audit_extraction.py: pipeline-report sections + fabrication
  re-probe + new-coverage analysis.

These tests pin the gates' behaviour as committed; the extraction skill
relies on them holding.
"""

from __future__ import annotations

import hashlib

import pytest

from scripts.audit_extraction import audit_chunk
from scripts.beat_builder import (
    BookContext,
    beat_id,
    duration_sec,
    hash_body,
    make_beat,
    slugify,
)
from scripts.extract_validators import (
    check_length_class,
    count_source_sentences,
    fabrication_probe,
    source_span_gate,
    validate_beat,
)

# ─── beat_builder ────────────────────────────────────────────────────


def test_slugify_normalises_to_snake_case():
    # Accents fold to ASCII (Étoile → etoile)
    assert slugify("Place de l'Étoile") == "place_de_l_etoile"
    assert slugify("THE Sorbonne!") == "the_sorbonne"
    assert slugify("  multiple   spaces  ") == "multiple_spaces"
    # Already-lowercase accented chars also fold
    assert slugify("café de la paix") == "cafe_de_la_paix"


def test_hash_body_collapses_whitespace_and_lowercases():
    a = hash_body("Hello   World")
    b = hash_body("hello world")
    c = hash_body("\thello\nworld\n")
    assert a == b == c
    expected = hashlib.sha256(b"hello world").hexdigest()
    assert a == expected


def test_duration_sec_at_2p5_words_per_sec():
    assert duration_sec("one two three four five") == 2  # 5 / 2.5 = 2
    assert duration_sec("a " * 200) == 80  # 200 / 2.5 = 80


def test_beat_id_format():
    ctx = BookContext(
        book_title="Parisians",
        author="Graham Robb",
        book_slug="parisians",
        chunk_slug="chunk-13",
        chapter="A Little Tour of Paris",
        page="213",
    )
    bid = beat_id(ctx=ctx, poi_name="Palais Garnier", lens="war_conflict", topic_slug="staircase")
    assert bid == "paris_palais_garnier_war_conflict_parisians_staircase"


def test_make_beat_fills_required_fields_with_defaults():
    ctx = BookContext(
        book_title="X",
        author="Y",
        book_slug="x",
        chunk_slug="c",
        chapter="ch",
        page="1",
    )
    beat = make_beat(
        ctx=ctx,
        poi_name="Eiffel Tower",
        lens="historic_arch",
        topic_slug="opening",
        script_body="The Eiffel Tower opened in 1889.",
        source_passage="The Eiffel Tower opened in 1889.",
        beat_length_class="seasoning",
        beat_type="factoid",
        narrative_function="establishing",
        emotional_register="neutral",
        subject_tag="opening",
        entities=["Eiffel Tower"],
        sensory_anchor=True,
    )
    assert beat["beat_id"] == "paris_eiffel_tower_historic_arch_x_opening"
    assert beat["script_body_hash"] == hash_body("The Eiffel Tower opened in 1889.")
    assert beat["duration_sec"] == 2  # 6 words / 2.5 ≈ 2
    assert beat["source_attribution"] == {
        "book_title": "X",
        "author": "Y",
        "chapter": "ch",
        "page": "1",
    }
    assert beat["fact_check"]["status"] == "unverified"
    assert beat["fact_check"]["extractor_state"] == "clean"
    assert beat["fact_check"]["flagged_claims"] == []
    assert beat["_meta"]["prompt_version"] == "unified_v2"
    assert beat["sub_location"] is None
    assert beat["trigger_address"] is None
    assert beat["new_poi"] is False


def test_make_beat_imported_context_path():
    ctx = BookContext(
        book_title="X",
        author="Y",
        book_slug="x",
        chunk_slug="c",
        chapter="ch",
        page="1",
    )
    beat = make_beat(
        ctx=ctx,
        poi_name="Place Vendome",
        lens="war_conflict",
        topic_slug="vendome",
        script_body="Hitler praised the column the Communards had pulled down.",
        source_passage="Hitler praised the Place Vendôme.",
        beat_length_class="seasoning",
        beat_type="factoid",
        narrative_function="deepen",
        emotional_register="dramatic",
        subject_tag="vendome",
        entities=["Adolf Hitler"],
        sensory_anchor=True,
        extractor_state="imported_context",
        flagged_claims=["the Communard column-pulldown is not in source"],
        fact_check_notes="Communard reference imported by extractor",
    )
    assert beat["fact_check"]["extractor_state"] == "imported_context"
    assert beat["fact_check"]["flagged_claims"] == [
        "the Communard column-pulldown is not in source"
    ]
    assert beat["fact_check"]["notes"] == "Communard reference imported by extractor"


# ─── source-span gate (B12) ───────────────────────────────────────────


def test_count_source_sentences_two_periods():
    src = "The square has an obelisk. It is twenty-three metres tall."
    assert count_source_sentences(src) == 2


def test_count_source_sentences_semicolon_clauses_count_separately():
    """Per the patch: 'a semicolon-joined clause carrying an independent
    factual claim counts as a sentence for this gate.'"""
    src = "The dome is grey; the windows were blacked out; sandbags lined the floor."
    assert count_source_sentences(src) == 3


def test_count_source_sentences_skips_short_fragments():
    """Fragments without ≥3 real words shouldn't be counted (filters
    'I.A.i.' numbering, ellipses, headings)."""
    src = "I. A. i. Yes. The cathedral was built in the twelfth century."
    # 'I.', 'A.', 'i.', 'Yes.' all have <3 real words; only the long sentence counts.
    assert count_source_sentences(src) == 1


def test_count_source_sentences_empty():
    assert count_source_sentences("") == 0
    assert count_source_sentences("   ") == 0


@pytest.mark.parametrize(
    "src,expected",
    [
        # 1 factual sentence → seasoning ceiling
        ("The cathedral was built in the twelfth century.", "seasoning"),
        # 2 factual sentences → seasoning ceiling
        (
            "The cathedral was built in the twelfth century. "
            "Construction lasted two hundred years.",
            "seasoning",
        ),
        # 3 factual sentences → mid ceiling
        (
            "The cathedral was built in the twelfth century. "
            "Construction lasted two hundred years. "
            "The towers were never finished.",
            "mid",
        ),
        # 6 factual sentences → anchor in play
        (
            "The cathedral was built in the twelfth century. "
            "Construction lasted two hundred years. "
            "The towers were never finished. "
            "Notre-Dame survived the Revolution. "
            "Viollet-le-Duc restored it in the nineteenth century. "
            "A fire damaged the spire in 2019.",
            "anchor",
        ),
    ],
)
def test_source_span_gate_thresholds(src, expected):
    assert source_span_gate(src) == expected


# ─── length-class enforcer ────────────────────────────────────────────


@pytest.mark.parametrize(
    "wc,cls,in_range",
    [
        (50, "seasoning", True),  # 20-80
        (15, "micro", True),  # 0-20
        (150, "mid", True),  # 80-200
        (300, "anchor", True),  # 200-400
        (85, "seasoning", False),  # over → mid
        (70, "mid", False),  # under → seasoning
        (190, "anchor", False),  # under → mid
        (5, "seasoning", False),  # under → micro
    ],
)
def test_check_length_class(wc, cls, in_range):
    body = "x " * wc
    ok, suggested = check_length_class(body, cls)
    assert ok is in_range
    if not in_range:
        assert suggested in {"micro", "seasoning", "mid", "anchor"}


def test_check_length_class_mid_under_floor_suggests_seasoning():
    body = "x " * 70
    ok, suggested = check_length_class(body, "mid")
    assert ok is False
    assert suggested == "seasoning"


def test_check_length_class_anchor_under_floor_suggests_mid():
    body = "x " * 190
    ok, suggested = check_length_class(body, "anchor")
    assert ok is False
    assert suggested == "mid"


# ─── fabrication probe (B11) ───────────────────────────────────────────


def test_fabrication_probe_clean_body_returns_no_findings():
    src = "The cathedral was built in 1163. Construction lasted two centuries."
    chunk = src + " Many Parisians attended the dedication."
    body = "The cathedral was built in 1163."
    verdict = fabrication_probe(
        script_body=body,
        physical_cues=[],
        source_passage=src,
        chunk_text=chunk,
    )
    assert verdict.has_fabrication is False


def test_fabrication_probe_unsourced_year_in_body():
    src = "Hitler praised the Place Vendôme."
    chunk = src
    body = "Hitler praised the Place Vendôme. The Communards had toppled the column in 1871."
    verdict = fabrication_probe(
        script_body=body,
        physical_cues=[],
        source_passage=src,
        chunk_text=chunk,
    )
    assert verdict.has_fabrication is True
    # 1871 is the unsourced year
    assert any("1871" in c for c in verdict.unsourced_claims)


def test_fabrication_probe_unsourced_claim_in_cue():
    src = "Carpeaux's La Danse retained Hitler's full attention for a moment."
    chunk = src
    body = "Hitler stopped at La Danse."
    cues = [
        {
            "cue": (
                "La Danse sculpture group; the original is now in Musée d'Orsay; "
                "the façade carries a 1964 replica"
            ),
            "direction": "here",
            "feature_type": "architectural_detail",
        }
    ]
    verdict = fabrication_probe(
        script_body=body,
        physical_cues=cues,
        source_passage=src,
        chunk_text=chunk,
    )
    assert verdict.has_fabrication is True
    # Should flag at least one cue
    assert len(verdict.cue_unsourced) >= 1
    # 1964 should be flagged (red-flag year, not in source/chunk)
    assert any("1964" in c[1] for c in verdict.cue_unsourced)


def test_fabrication_probe_finds_year_in_chunk_passes():
    """If a year IS in the broader chunk_text but not in the cited
    source_passage, that's not fabrication — the extractor cited a
    narrow span. The probe must allow this."""
    src = "Hitler stopped at La Danse."
    chunk = "Carpeaux's La Danse, unveiled in 1869, retained Hitler's attention. " + src
    body = "Hitler stopped at La Danse, the 1869 Carpeaux sculpture."
    verdict = fabrication_probe(
        script_body=body,
        physical_cues=[],
        source_passage=src,
        chunk_text=chunk,
    )
    # 1869 is in chunk but not source — should NOT be flagged.
    assert verdict.has_fabrication is False


def test_fabrication_probe_strips_quotes_around_entity_in_source():
    """Robb's prose: `the 'Excavation' team`. Body: `the Excavation team`.
    Identical content; the probe must not flag it as fabrication."""
    src = "The 'Excavation' team, composed of migrant workers, was to clear rubble."
    chunk = src
    body = "The Excavation team — migrant workers — cleared the rubble."
    verdict = fabrication_probe(
        script_body=body,
        physical_cues=[],
        source_passage=src,
        chunk_text=chunk,
    )
    # 'Excavation team' is in source after stripping quotes; should NOT flag.
    flagged_phrases = " ".join(verdict.unsourced_claims)
    assert "Excavation team" not in flagged_phrases


def test_fabrication_probe_drops_sentence_start_function_words():
    """`Once Guillaumot...` and `In April...` shouldn't be captured as named
    entities — the leading word is a connective."""
    src = "Guillaumot acted in April. The committee approved the plan."
    chunk = src
    body = (
        "Once Guillaumot had established himself, the work continued. "
        "In April, students saw the collapse."
    )
    verdict = fabrication_probe(
        script_body=body,
        physical_cues=[],
        source_passage=src,
        chunk_text=chunk,
    )
    flagged = " ".join(verdict.unsourced_claims)
    assert "Once Guillaumot" not in flagged
    assert "In April" not in flagged


def test_fabrication_probe_strips_verb_suffix_at_end():
    """`Saint Denis Christianised` — regex over-captures the trailing verb.
    The probe should strip it and check whether `Saint Denis` is in source."""
    src = "men and women who had died before Saint Denis had Christianized the city."
    chunk = src
    body = "men and women who lived in Paris before Saint Denis Christianised the city."
    verdict = fabrication_probe(
        script_body=body,
        physical_cues=[],
        source_passage=src,
        chunk_text=chunk,
    )
    flagged = " ".join(verdict.unsourced_claims)
    assert "Saint Denis" not in flagged


def test_fabrication_probe_dialect_fold_british_american():
    """`Christianised` (British) in body should match `Christianized` (US) in source."""
    src = "Saint Denis Christianized the city in the third century."
    chunk = src
    body = "Saint Denis Christianised the city."
    verdict = fabrication_probe(
        script_body=body,
        physical_cues=[],
        source_passage=src,
        chunk_text=chunk,
    )
    # Saint Denis is in source — dialect fold means the entity match passes.
    flagged = " ".join(verdict.unsourced_claims)
    assert "Saint Denis" not in flagged


def test_fabrication_probe_red_flag_phrase():
    src = "The statue stood on its pedestal."
    chunk = src
    body = "The statue stood on its pedestal. What stands here today is a 1957 replacement."
    verdict = fabrication_probe(
        script_body=body,
        physical_cues=[],
        source_passage=src,
        chunk_text=chunk,
    )
    assert verdict.has_fabrication is True
    # 1957 should be flagged AND the "replacement" red-flag phrase
    assert any("1957" in c for c in verdict.unsourced_claims)


# ─── orchestrating validate_beat ───────────────────────────────────────


def _minimal_beat(**overrides):
    base = {
        "beat_id": "paris_x_war_conflict_y_t",
        "poi_name": "X",
        "lens": "war_conflict",
        "script_body": "x " * 100,
        "source_passage": (
            "First sentence here. Second one too. Third one as well. Fourth one. Fifth one."
        ),
        "beat_length_class": "mid",
        "physical_cues": [],
        "fact_check": {"extractor_state": "clean", "flagged_claims": [], "status": "unverified"},
    }
    base.update(overrides)
    return base


def test_validate_beat_passes_clean_mid():
    beat = _minimal_beat()
    verdict = validate_beat(beat, chunk_text=beat["source_passage"])
    assert verdict.ok
    assert not verdict.errors


def test_validate_beat_b12_violation_when_class_exceeds_span():
    """Source span = 1 sentence → seasoning ceiling, but declared anchor."""
    beat = _minimal_beat(
        source_passage="Just one sentence with enough real words to count.",
        beat_length_class="anchor",
        script_body="x " * 250,
    )
    verdict = validate_beat(beat, chunk_text=beat["source_passage"])
    assert not verdict.ok
    assert any("B12 violation" in e for e in verdict.errors)


def test_validate_beat_warns_on_length_drift():
    beat = _minimal_beat(
        beat_length_class="mid",
        script_body="x " * 70,  # under mid floor
    )
    verdict = validate_beat(beat, chunk_text=beat["source_passage"])
    # Length drift is a warning, not an error (caller may expand or re-class)
    assert verdict.suggested_class == "seasoning"
    assert any("length-class drift" in w for w in verdict.warnings)


def test_validate_beat_warns_on_unflagged_fabrication():
    beat = _minimal_beat(
        script_body="The Communards toppled the column in 1871.",
        source_passage="Hitler praised the Place Vendôme.",
    )
    verdict = validate_beat(beat, chunk_text=beat["source_passage"])
    assert verdict.fabrication is not None
    assert verdict.fabrication.has_fabrication
    assert any("fabrication probe" in w for w in verdict.warnings)


def test_validate_beat_grounding_violation_when_passage_absent():
    """The memory-fabrication failure: a source_passage reconstructed from
    memory whose sentences don't appear in the pinned chunk is a HARD error."""
    beat = _minimal_beat(
        source_passage=(
            "The arch is three times the size of its sibling. The eight statues "
            "were carved by Ramey and Cartellier. The horses came from Berlin."
        ),
        beat_length_class="mid",
        script_body="x " * 100,
    )
    chunk = "The monument is built of marble. It stands in a public square. Visitors pass it daily."
    verdict = validate_beat(beat, chunk_text=chunk)
    assert not verdict.ok
    assert any("source-grounding violation" in e for e in verdict.errors)


def test_validate_beat_grounding_passes_when_passage_in_larger_chunk():
    """A faithful passage that appears verbatim inside a longer chunk passes."""
    passage = "It was built between 1806 and 1808. The arch stands at the Place du Carrousel."
    beat = _minimal_beat(
        source_passage=passage, beat_length_class="seasoning", script_body="x " * 40
    )
    chunk = (
        "The Arc de Triomphe du Carrousel is a triumphal arch. It was built between 1806 and "
        "1808. The arch stands at the Place du Carrousel, near the Louvre."
    )
    verdict = validate_beat(beat, chunk_text=chunk)
    assert verdict.ok
    assert not any("source-grounding" in e for e in verdict.errors)


def test_validate_beat_grounding_tolerates_single_broken_fragment():
    """One absent fragment in a short passage (e.g. an OCR/page-break artifact)
    must NOT hard-block — the gate fires only on >=2 ungrounded fragments."""
    beat = _minimal_beat(
        source_passage=(
            "The arch was built between 1806 and 1808. "
            "The statues were sculpted by aliens from Mars."
        ),
        beat_length_class="seasoning",
        script_body="x " * 40,
    )
    chunk = (
        "The arch was built between 1806 and 1808 by Napoleon. It stands at the Place du Carrousel."
    )
    verdict = validate_beat(beat, chunk_text=chunk)
    assert verdict.ok
    assert not any("source-grounding" in e for e in verdict.errors)


# ─── audit_chunk ─────────────────────────────────────────────────────


def _audit_beat(
    *,
    beat_id="b1",
    poi="Eiffel Tower",
    lens="war_conflict",
    body="x " * 90,
    source="One sentence here. Two more sentences here. Three real sentences here.",
    cls="mid",
    sub_loc=None,
    trigger_addr=None,
    state="clean",
    flagged=None,
    new_poi=False,
    nf="deepen",
    bt="event",
    er="neutral",
    sensory=False,
    cues=None,
    inline_foreign=None,
):
    return {
        "beat_id": beat_id,
        "city_name": "paris",
        "poi_name": poi,
        "lens": lens,
        "book_slug": "test",
        "topic_slug": beat_id,
        "script_body": body,
        "source_passage": source,
        "beat_length_class": cls,
        "sub_location": sub_loc,
        "trigger_address": trigger_addr,
        "narrative_function": nf,
        "beat_type": bt,
        "emotional_register": er,
        "sensory_anchor": sensory,
        "physical_cues": cues or [],
        "inline_foreign_phrases": inline_foreign or [],
        "fact_check": {"extractor_state": state, "flagged_claims": flagged or []},
        "new_poi": new_poi,
    }


def test_audit_chunk_basic_distributions():
    beats = [
        _audit_beat(beat_id="b1", poi="Eiffel Tower", lens="war_conflict"),
        _audit_beat(beat_id="b2", poi="Eiffel Tower", lens="historic_arch"),
        _audit_beat(beat_id="b3", poi="Trocadero", lens="war_conflict"),
    ]
    poi_index = {
        "Eiffel Tower": {"importance_tier": 5, "poi_role": "stop"},
        "Trocadero": {"importance_tier": 5, "poi_role": "stop"},
    }
    report = audit_chunk(
        beats=beats,
        chunk_text="One sentence here. Two more sentences here. Three real sentences here.",
        poi_index=poi_index,
    )
    assert report["extraction_summary"]["total_beats"] == 3
    assert report["extraction_summary"]["beats_per_lens"]["war_conflict"] == 2
    assert report["poi_matching"]["existing_count"] == 2
    assert report["yield_per_1k_words"] > 0


def test_audit_chunk_flags_self_flag_failures():
    """A beat with extractor_state='clean' but unsourced claims → audit
    flags self_flag_failure."""
    beats = [
        _audit_beat(
            body="The statue from 1871 was destroyed.",
            source="The statue stood on its pedestal.",
            state="clean",
        )
    ]
    report = audit_chunk(
        beats=beats,
        chunk_text="The statue stood on its pedestal.",
        poi_index={"Eiffel Tower": {"importance_tier": 5, "poi_role": "stop"}},
    )
    failures = report["fabrication_audit"]["self_flag_failures"]
    assert len(failures) == 1
    assert failures[0]["beat_id"] == "b1"


def test_audit_chunk_extractor_state_ratio_and_ceiling():
    beats = [
        _audit_beat(beat_id=f"b{i}", state="imported_context", flagged=["x"]) for i in range(5)
    ] + [_audit_beat(beat_id="b6", state="clean")]
    poi_index = {"Eiffel Tower": {"importance_tier": 5, "poi_role": "stop"}}
    report = audit_chunk(
        beats=beats,
        chunk_text="One sentence here. Two more sentences here. Three real sentences here.",
        poi_index=poi_index,
    )
    summary = report["extractor_state_summary"]
    assert summary["counts"]["imported_context"] == 5
    assert summary["counts"]["clean"] == 1
    # 5/6 ≈ 0.83 > 0.4
    assert summary["over_40pct_ceiling"]


def test_audit_chunk_new_coverage_against_live_corpus():
    beats = [
        _audit_beat(beat_id="b1", poi="Eiffel Tower", lens="war_conflict"),  # already in live
        _audit_beat(beat_id="b2", poi="Eiffel Tower", lens="dark_history"),  # new combo
        _audit_beat(beat_id="b3", poi="New Place", new_poi=True),  # new POI
    ]
    live_beats = [{"poi_name": "Eiffel Tower", "lens": "war_conflict"}]
    poi_index = {"Eiffel Tower": {"importance_tier": 5, "poi_role": "stop"}}
    report = audit_chunk(
        beats=beats,
        chunk_text="One sentence here. Two more sentences here. Three real sentences here.",
        poi_index=poi_index,
        live_beats=live_beats,
    )
    assert "new_coverage" in report
    assert report["new_coverage"]["new_combinations"] == 2
    reasons = {d["reason"] for d in report["new_coverage"]["details"]}
    assert reasons == {"new_combo", "new_poi"}


def test_audit_chunk_length_class_out_of_range():
    beats = [
        _audit_beat(beat_id="b1", body="x " * 300, cls="seasoning"),  # way over
    ]
    poi_index = {"Eiffel Tower": {"importance_tier": 5, "poi_role": "stop"}}
    report = audit_chunk(
        beats=beats,
        chunk_text="One sentence here. Two more sentences here. Three real sentences here.",
        poi_index=poi_index,
    )
    assert len(report["length_class_distribution"]["out_of_range"]) == 1
    assert report["length_class_distribution"]["out_of_range"][0]["beat_id"] == "b1"


# ─── B13 copying gate ────────────────────────────────────────────────────


def test_a_body_that_reuses_the_source_sentence_is_an_error() -> None:
    """The defect the corpus was built out of: 78% of Paris beats shared an 8+ word
    run with the book they came from, because the prompt asked for the source's own
    language and nothing measured whether it got it."""
    from scripts.extract_validators import validate_beat

    chunk = (
        "The bridge soon became symbolic of the city itself, drawing large crowds "
        "every day of the week."
    )
    verdict = validate_beat(
        {
            "script_body": "The bridge soon became symbolic of the city itself, drawing crowds.",
            "source_passage": chunk,
            "beat_length_class": "seasoning",
        },
        chunk,
    )
    assert not verdict.ok
    assert any("copying violation" in e for e in verdict.errors)
    assert verdict.copied_run >= 8


def test_the_same_fact_in_the_extractors_own_words_passes() -> None:
    """The gate blocks reused expression, not the fact. A beat carrying the same
    specificity in different sentences is the output this pipeline wants."""
    from scripts.extract_validators import validate_beat

    chunk = (
        "The bridge soon became symbolic of the city itself, drawing large crowds "
        "every day of the week."
    )
    verdict = validate_beat(
        {
            "script_body": "Crowds came daily, and the span turned into a shorthand for Paris.",
            "source_passage": chunk,
            "beat_length_class": "seasoning",
        },
        chunk,
    )
    assert verdict.ok
    assert verdict.copied_run < 8


def test_an_attributed_quotation_is_exempt_and_an_unattributed_one_is_not() -> None:
    """Quoting a named speaker and saying who they are is not copying the book that
    also quoted them. A quotation attributed to nobody is what an unmarked lift looks
    like, so it earns no exemption."""
    from scripts.extract_validators import copying_gate

    chunk = "The bridge soon became symbolic of the city itself, drawing large crowds."
    quoted = (
        'As Hugo wrote, "the bridge soon became symbolic of the city itself, drawing large crowds".'
    )
    bare = '"The bridge soon became symbolic of the city itself, drawing large crowds."'
    assert copying_gate(quoted, chunk)[0] < 8
    assert copying_gate(bare, chunk)[0] >= 8


def test_the_gate_names_the_run_so_a_rewrite_has_something_to_avoid() -> None:
    """A refusal that only says "you copied" cannot be acted on. The extractor still
    holds the source, so showing the run costs nothing and is what the retry needs."""
    from scripts.extract_validators import validate_beat

    chunk = "The bridge soon became symbolic of the city itself, drawing large crowds."
    verdict = validate_beat(
        {"script_body": chunk, "source_passage": chunk, "beat_length_class": "seasoning"},
        chunk,
    )
    assert "symbolic of the city itself" in verdict.copied_text
    assert verdict.copied_text in [e.split("The run: ")[-1].strip("'\"") for e in verdict.errors]


def test_a_chunk_report_carries_the_run_distribution_not_only_the_failures() -> None:
    """A chunk sitting just under the block is one book away from crossing it, and a
    count of zero blocked says nothing about that."""
    from scripts.audit_extraction import audit_chunk

    chunk = "The bridge soon became symbolic of the city itself, drawing large crowds."
    beats = [
        {
            "beat_id": "a",
            "poi_name": "P",
            "lens": "l",
            "script_body": "The bridge soon became symbolic of the city itself, drawing crowds.",
            "source_passage": chunk,
            "beat_length_class": "seasoning",
        },
        {
            "beat_id": "b",
            "poi_name": "P",
            "lens": "l",
            "script_body": "Crowds came daily and the span became a shorthand.",
            "source_passage": chunk,
            "beat_length_class": "seasoning",
        },
    ]
    audit = audit_chunk(beats=beats, chunk_text=chunk, poi_index={})["copying_audit"]
    assert audit["blocked"] == 1
    assert audit["blocked_beats"][0]["beat_id"] == "a"
    assert audit["longest_run"] >= 8
    assert "median_run" in audit and "median_ratio" in audit
