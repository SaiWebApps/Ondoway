"""Unit tests for route-level claim-repetition suppression.

Fixtures mirror the live Île de la Cité case: three beats from three source books
each narrate the Parisii founding; a fourth (the Nanterre debate) merely mentions
the Parisii and must survive. Only a PURE restatement is dropped — a compound
sentence carrying a novel fact alongside a repeated one is kept whole (the
content-safety property the skeptic panel demanded).
"""

from __future__ import annotations

from src.tour.claim_dedup import (
    _signature,
    claims_realized_by,
    suppress_exact_repeats,
    suppress_repeated_claims,
    suppress_same_beat_near_duplicates,
    verify_claim_coverage,
)
from src.tour.contract import (
    BeatRef,
    BeatSequence,
    POIBeats,
    Script,
    Sentence,
    TourInput,
    ValidationReport,
)


def _beat(bid: str, claims: tuple[str, ...]) -> BeatRef:
    return BeatRef(id=bid, poi_id="ile", key_claims=claims)


def _seq(*beats: BeatRef) -> BeatSequence:
    return BeatSequence(
        poi_beats=(POIBeats(poi_id="ile", poi_name="Ile", ordering_strategy="narrative_function",
                            beats=tuple(beats)),)
    )


def _s(text: str, source_id: str, source_type: str = "beat") -> Sentence:
    return Sentence(text=text, source_id=source_id, source_type=source_type, stop_idx=0)


def test_coverage_gate_works_for_keyless_beats_via_script_body():
    """Silent-fact-loss regression (red-team): a KEYLESS beat (key_claims=(), the
    entire London corpus) must still get a coverage baseline from its script_body
    sentences — else the deletion gate is a no-op and a fusion can silently DROP a
    fact there. UNDO: revert _claims_for_coverage to key_claims-only -> the keyless
    beat contributes no baseline -> the dropped fact is NOT flagged -> RED."""
    from src.tour.claim_dedup import _claims_for_coverage, claims_realized_by, verify_claim_coverage
    from src.tour.contract import Script

    keyed = BeatRef(id="K", poi_id="ile", key_claims=("Napoleon crowned himself in 1804",),
                    script_body="Body.")
    assert _claims_for_coverage(keyed) == ("Napoleon crowned himself in 1804",)  # keyed unchanged

    keyless = BeatRef(
        id="L", poi_id="ile", key_claims=(),
        script_body=(
            "The Great Fire began in Pudding Lane in 1666. "
            "Christopher Wren later rebuilt Saint Paul's Cathedral."
        ),
    )
    beats = {"L": keyless}
    stitched = Script.model_construct(script=(
        _s("The Great Fire began in Pudding Lane in 1666.", "L"),
        _s("Christopher Wren later rebuilt Saint Paul's Cathedral.", "L"),
    ))
    composed = Script.model_construct(script=(  # the Wren/Cathedral fact was dropped by a fusion
        _s("The Great Fire began in Pudding Lane in 1666.", "L"),
    ))
    expected = claims_realized_by(stitched, beats)
    assert len(expected) == 2, expected  # both body sentences' hard facts form the baseline
    uncovered = verify_claim_coverage(composed, expected, beats)
    assert any("Wren" in claim or "Cathedral" in claim for _bid, claim in uncovered), uncovered


def test_keyless_coverage_gates_a_lowercase_only_fact_deletion():
    """Round-4 (judge-mandated): a keyless body sentence whose facts are ALL lowercase
    (no year, no proper noun) — 'duels', 'the nobility' — must STILL get a coverage
    baseline, so dropping the whole sentence is flagged. This pins the reason the
    coverage pseudo-claim is the FULL body sentence and NOT a 'hard facts' (years +
    proper-nouns) extract: on the live London corpus that extract collapsed 85/562
    keyless beats (incl. this pattern) to '<2 tokens' -> empty baseline -> a silent
    deletion the gate no longer caught. UNDO: key _claims_for_coverage on
    `_hard_facts` (years/proper-nouns only) -> this sentence -> pseudo-claim 'The' ->
    below MIN_SHARED_TOKENS -> no baseline -> deletion NOT flagged -> RED."""
    from src.tour.claim_dedup import claims_realized_by, verify_claim_coverage
    from src.tour.contract import Script

    keyless = BeatRef(
        id="L", poi_id="ile", key_claims=(),
        script_body=(
            "The park later became a place for secret duels. "
            "Many of them involved quarrelling members of the landed nobility."
        ),
    )
    beats = {"L": keyless}
    stitched = Script.model_construct(script=(
        _s("The park later became a place for secret duels.", "L"),
        _s("Many of them involved quarrelling members of the landed nobility.", "L"),
    ))
    composed = Script.model_construct(script=(  # the whole duels/nobility fact was dropped
        _s("The park is a broad green space beside the river.", "L"),
    ))
    expected = claims_realized_by(stitched, beats)
    assert len(expected) == 2, expected  # lowercase-fact sentences STILL form a baseline
    uncovered = verify_claim_coverage(composed, expected, beats)
    assert any("duels" in claim or "nobility" in claim for _bid, claim in uncovered), uncovered


def test_dedup_passes_never_resurrect_a_duplicate():
    """Round-4 (Defect A): the also_cites never-empty restore in suppress_exact_repeats
    / suppress_same_beat_near_duplicates was resurrecting a byte-identical / near-dup
    sentence (the exact shipped-duplicate bug). Neither pass may keep a text duplicate.
    UNDO: re-add the cited_beat_ids restore to either pass -> the duplicate ships ->
    RED."""
    seq = _seq(_beat("X", ("Fact X here about the tower",)), _beat("Z", ("Fact Z distinct",)))
    txt = "The chapel was consecrated in the year twelve forty by the bishop here."
    s1 = _s(txt, "X")
    # a fused duplicate whose ALSO-cited beat Z has no other carrier
    dup = Sentence(text=txt, source_id="X", source_type="beat", stop_idx=0, also_cites=("Z",))
    exact_out = suppress_exact_repeats([s1, dup], seq)
    assert [s.text for s in exact_out].count(txt) == 1, "byte-identical duplicate resurrected"
    near = Sentence(text=txt + " It still stands today.", source_id="X", source_type="beat",
                    stop_idx=0, also_cites=("Z",))
    near_out = suppress_same_beat_near_duplicates([s1, near])
    assert len(near_out) == 1, "near-verbatim duplicate resurrected"


def test_suppress_repeated_claims_keeps_a_fused_also_cites_sentence():
    """Silent-fact-loss regression (red-team): a FUSED sentence (primary A, also_cites
    B) carrying B's DISTINCT fact must NOT be dropped as a pure restatement of A alone
    when A's fact was already voiced — that silently EMPTIES beat B. The pass judges a
    sentence by the UNION of its cited beats' claims. UNDO: judge by source_id only
    (drop the also_cites union) -> the fused sentence is dropped, B emptied -> RED."""
    seq = _seq(
        _beat("A", ("Napoleon crowned himself emperor in 1804",)),
        _beat("B", ("Marie Gredeler was the only woman executed at this prison",)),
    )
    s1 = _s("Napoleon crowned himself emperor here in 1804.", "A")  # voices A's fact first
    fused = Sentence(
        text="Napoleon crowned himself emperor in 1804, and Marie Gredeler was "
        "the only woman executed at this prison.",
        source_id="A", source_type="beat", stop_idx=0, also_cites=("B",),
    )
    out = suppress_repeated_claims([s1, fused], seq, include_same_beat=True)
    assert fused in out, [x.text for x in out]  # kept — it carries B's novel fact
    # beat B is still voiced by some surviving sentence (not emptied)
    assert any("B" in x.cited_beat_ids for x in out if x.source_type == "beat")


# Three founding beats + the distinct Nanterre-debate beat, faithful to the corpus.
B1 = _beat("f052", (
    "Parisii settled 3rd century BC",
    "Louis IX built Sainte-Chapelle 13th century for Crown of Thorns",
))
B2 = _beat("7492", (
    "Parisii Celtic tribe first settled the Île de la Cité in the 3rd century BC",
    "Romans invaded in 52 BC and renamed the city Lutetia, meaning born of the waters",
))
B3 = _beat("ab08", (
    "Settled c. 300 BC by Celtic Parisii",
    "Romans took it 52 BC and built a palace-fortress at the western end",
    "Frankish kings rebuilt palace in 10th century",
))
B4 = _beat("2b7c", (
    "Caesar described Lutetia as fortress of the Parisii situated on an island",
    "Some historians proposed Nanterre as the true Lutetia",
))


def _founding_narration() -> list[Sentence]:
    return [
        _s("Settle in near the Seine.", "GLUE_PACING", "glue"),
        # B1 — first to voice the founding.
        _s("The Ile de la Cite was the first part of Paris to be settled, occupied by the "
           "Celtic tribe of the Parisii in the 3rd century BC, making it the original nucleus.",
           "f052"),
        _s("Louis IX built the Sainte-Chapelle in the 13th century to house the Crown of Thorns.",
           "f052"),
        # B2 — a PURE founding restatement (dropped), then Roman + Lutetia (novel, kept).
        _s("Archaeological evidence suggests that a Celtic tribe called the Parisii first "
           "settled on the Île de la Cité during the 3rd century BC.", "7492"),
        _s("The island was a secure settlement, but no defence could protect the Celts from the "
           "Roman invasion in 52 BC.", "7492"),
        _s("The city, renamed Lutetia, meaning born of the waters, prospered.", "7492"),
        # B3 — a COMPOUND sentence: repeated founding clause + NOVEL palace-fortress fact.
        _s("It was settled around 300 BC by a Celtic tribe called the Parisii, and in 52 BC was "
           "overrun by the Romans, who built a palace-fortress at the western end.", "ab08"),
        _s("Frankish kings rebuilt the palace in the 10th century.", "ab08"),
        # B4 — Nanterre debate; mentions Parisii but is a distinct fact -> survives.
        _s("Others point to Caesar's description: Lutetia, fortress of the Parisii, on an island.",
           "2b7c"),
    ]


def test_pure_founding_restatement_dropped() -> None:
    out = suppress_repeated_claims(_founding_narration(), _seq(B1, B2, B3, B4))
    texts = [s.text for s in out]
    # B1's founding (first occurrence) survives; B2's PURE restatement is dropped.
    assert any("original nucleus" in t for t in texts)
    assert not any("Archaeological evidence" in t for t in texts)


def test_same_beat_paraphrase_echo_dropped_only_with_flag() -> None:
    """Over a big multi-beat stop the composer can echo ONE beat as a polished
    paraphrase AND a near-verbatim retelling — same source_id, same claim, different
    wording — which is the dominant repetition in real composed tours. The default
    (cross-beat only) leaves both; ``include_same_beat=True`` (used on the composed
    output in compose_script_per_chapter._assemble) drops the echo, keeping the
    first (polished) telling.
    UNDO: drop the ``include_same_beat or`` branch in ``_already_voiced`` -> the echo
    survives even with the flag -> RED."""
    beat = _beat(
        "nd01", ("Napoleon crowned himself emperor at Notre-Dame in 1804 with the walls draped",)
    )
    seq = _seq(beat)
    sents = [
        _s(
            "Napoleon crowned himself emperor here at Notre-Dame in 1804, the dilapidated "
            "walls draped to hide their state.",
            "nd01",
        ),
        _s(
            "Napoleon crowned himself emperor at Notre-Dame in 1804 with the walls draped.",
            "nd01",
        ),
    ]
    # Default (cross-beat only) = today's stitch behaviour, unchanged: both survive.
    assert len(suppress_repeated_claims(sents, seq)) == 2
    # Same-beat aware: the echo is dropped, the first (polished) telling kept.
    deduped = suppress_repeated_claims(sents, seq, include_same_beat=True)
    assert len(deduped) == 1
    assert deduped[0].text.startswith("Napoleon crowned himself emperor here")


def test_same_beat_near_duplicate_retelling_dropped() -> None:
    """The composer echoes ONE beat as two full re-tellings (near-verbatim, same
    source_id) — the real Île de la Cité tour repeated the whole Abelard story (the
    two tellings measured 98-100 rapidfuzz). This text-similarity pass drops the
    echo where the claim-based pass can't (the re-telling drifts from the claim
    tokens). A DISTINCT fact of the same beat, and a similar-reading DIFFERENT beat,
    both survive.
    UNDO: remove the ``suppress_same_beat_near_duplicates`` call in _assemble (or the
    same-source_id restriction) -> the echo survives, or a distinct fact is lost."""
    ab = "ab"
    sents = [
        _s("Around the year 1200 one of the teachers was Peter Abelard, a philosophical "
           "whizz kid and cocker of snooks at establishment intellectuals.", ab),
        _s("Around the year 1200, one of its teachers was Peter Abelard, a philosophical "
           "whizz kid and cocker of snooks at establishment intellectuals.", ab),  # echo
        _s("She gave birth to a baby, her uncle had Abelard castrated, and the story ended "
           "in convents and lifelong separation.", ab),  # DISTINCT fact of same beat -> kept
    ]
    out = suppress_same_beat_near_duplicates(sents)
    assert len(out) == 2, "the near-verbatim echo must drop, the distinct fact must stay"
    assert any("castrated" in s.text for s in out), "a novel fact of the same beat is never lost"

    # Two DIFFERENT beats that read near-identically are NOT touched (only same beat).
    diff = [
        _s("The great bell was forged in 1685 and spared during the Revolution.", "b1"),
        _s("The great bell was forged in 1685 and spared during the Revolution.", "b2"),
    ]
    assert len(suppress_same_beat_near_duplicates(diff)) == 2


def test_compound_sentence_keeps_novel_fact() -> None:
    # Skeptic-A regression: B3 fuses the repeated founding clause with the NOVEL
    # palace-fortress fact (voiced nowhere else) -> the whole sentence is KEPT.
    out = suppress_repeated_claims(_founding_narration(), _seq(B1, B2, B3, B4))
    texts = [s.text for s in out]
    assert any("palace-fortress" in t for t in texts), "novel compound fact must not be lost"


def test_all_distinct_content_preserved() -> None:
    out = suppress_repeated_claims(_founding_narration(), _seq(B1, B2, B3, B4))
    texts = [s.text for s in out]
    assert any("Crown of Thorns" in t for t in texts)          # B1 Louis IX
    assert any("renamed Lutetia" in t for t in texts)          # B2 Lutetia naming
    assert any("Roman invasion in 52 BC" in t for t in texts)  # B2 Roman conquest
    assert any("Frankish kings" in t for t in texts)           # B3 Frankish palace
    assert any("Caesar's description" in t for t in texts)     # B4 Nanterre debate
    assert any(s.source_type == "glue" for s in out)           # glue untouched


def test_no_beat_is_emptied() -> None:
    out = suppress_repeated_claims(_founding_narration(), _seq(B1, B2, B3, B4))
    emitted = {s.source_id for s in out if s.source_type == "beat"}
    assert emitted == {"f052", "7492", "ab08", "2b7c"}


def test_single_sentence_repeat_beat_is_restored() -> None:
    # A beat whose ONLY sentence is a pure repeat must NOT vanish (first restored).
    b5 = _beat("dupe", ("Parisii settled 3rd century BC",))
    seq = _seq(B1, b5)
    sents = [
        _s("Parisii settled the Île de la Cité in the 3rd century BC as the city's nucleus.",
           "f052"),
        _s("The Parisii settled here in the 3rd century BC.", "dupe"),
    ]
    out = suppress_repeated_claims(sents, seq)
    assert {s.source_id for s in out if s.source_type == "beat"} == {"f052", "dupe"}


def test_distinct_claims_untouched() -> None:
    # No repeats -> the list is returned unchanged (same object identity).
    seq = _seq(B1, B4)
    sents = [
        _s("Louis IX built the Sainte-Chapelle for the Crown of Thorns.", "f052"),
        _s("Caesar called Lutetia a fortress of the Parisii on an island.", "2b7c"),
    ]
    out = suppress_repeated_claims(sents, seq)
    assert out is sents


def test_date_canonicalization_unifies_bc_forms() -> None:
    # "300 BC" and "3rd century BC" must land the same century token.
    assert "c3bc" in _signature("settled around 300 BC")
    assert "c3bc" in _signature("the 3rd century BC")
    assert "c1bc" in _signature("Romans invaded in 52 BC")  # 52 BC is the 1st century BC


def test_vignette_pure_restatement_is_dropped():
    """A walk-past VIGNETTE one-liner that only restates an
    earlier dwell beat's claim must be dropped (not voiced verbatim). Vignettes are
    additive annotations, so a pure repeat drops whole — exempt from the
    never-empty-a-beat guard that (correctly) protects seated dwell beats."""
    dwell = _beat("dwell1", ("Parisii settled the island in the 3rd century BC",))
    vig = _beat("vig1", ("Parisii settled the island in the 3rd century BC",))  # same fact
    seq = BeatSequence(
        poi_beats=(
            POIBeats(poi_id="ile", poi_name="Ile", ordering_strategy="narrative_function",
                     beats=(dwell,)),
        ),
        vignette_beats={0: (vig,)},
    )
    sentences = [
        _s("The Parisii, a Celtic tribe, settled the island in the 3rd century BC.", "dwell1"),
        _s("The Parisii settled the island in the 3rd century BC.", "vig1"),  # pure repeat
    ]
    out = suppress_repeated_claims(sentences, seq)
    ids = [s.source_id for s in out]
    assert "dwell1" in ids
    assert "vig1" not in ids, "a pure-restatement vignette must be dropped, not heard twice"


def test_vignette_with_novel_fact_survives():
    """A vignette that carries a NOVEL claim is kept — only pure repeats drop."""
    dwell = _beat("dwell1", ("Parisii settled the island in the 3rd century BC",))
    vig = _beat("vig1", ("The bouquinistes have sold books on these quais since the 1600s",))
    seq = BeatSequence(
        poi_beats=(
            POIBeats(poi_id="ile", poi_name="Ile", ordering_strategy="narrative_function",
                     beats=(dwell,)),
        ),
        vignette_beats={0: (vig,)},
    )
    sentences = [
        _s("The Parisii settled the island in the 3rd century BC.", "dwell1"),
        _s("The bouquinistes have sold books on these quais since the 1600s.", "vig1"),
    ]
    out = suppress_repeated_claims(sentences, seq)
    assert {"dwell1", "vig1"} <= {s.source_id for s in out}


# ---------------------------------------------------------------------------
# Exact-repeat suppression (the byte-identical restatements the claim pass
# leaves behind — two beats at one stop sharing an identical sentence).
# ---------------------------------------------------------------------------

def _ss(text: str, source_id: str, stop_idx: int, source_type: str = "beat") -> Sentence:
    return Sentence(text=text, source_id=source_id, source_type=source_type, stop_idx=stop_idx)


def _seq2(*beats: BeatRef) -> BeatSequence:
    return BeatSequence(
        poi_beats=(POIBeats(poi_id="p", poi_name="P", ordering_strategy="narrative_function",
                            beats=tuple(beats)),)
    )


_DUP = "On the right as you exit is the special entrance designed for the imperial carriages."


def test_exact_duplicate_sentence_in_stop_is_dropped():
    """Two beats at one stop share an identical sentence; each also has unique
    text (the real Palais Garnier case). The restatement is heard once."""
    seq = _seq2(_beat("b1", ()), _beat("b2", ()))
    sents = [
        _ss("Charles Garnier won the competition in 1861 with a bold new style.", "b1", 0),
        _ss(_DUP, "b1", 0),
        _ss(_DUP, "b2", 0),  # byte-identical restatement from a different beat
        _ss("The grand staircase is the theatrical heart of the whole building.", "b2", 0),
    ]
    out = suppress_exact_repeats(sents, seq)
    assert [s.text for s in out].count(_DUP) == 1, "the exact restatement must be dropped once"
    assert out[0].text.startswith("Charles Garnier")  # unique text untouched
    assert {"b1", "b2"} == {s.source_id for s in out}  # both beats keep their unique content


def test_exact_repeat_across_different_stops_is_kept():
    """Within-stop only — an identical framing that recurs at a DISTANT stop is
    left for the claim pass, not force-dropped here."""
    seq = _seq2(_beat("b1", ()), _beat("b2", ()))
    sents = [_ss(_DUP, "b1", 0), _ss(_DUP, "b2", 3)]
    out = suppress_exact_repeats(sents, seq)
    assert len(out) == 2


def test_near_but_not_exact_is_kept():
    """No false positives: two similar-but-not-identical sentences both survive
    (semantic dedup is the claim pass's job, not this exact-match guard)."""
    seq = _seq2(_beat("b1", ()))
    sents = [
        _ss("Napoleon placed the four horses from St Mark's on top of the arch.", "b1", 0),
        _ss("The horses of Saint Mark were taken from Venice by Napoleon in 1798.", "b1", 0),
    ]
    out = suppress_exact_repeats(sents, seq)
    assert len(out) == 2


def test_exact_repeat_never_empties_a_seated_beat():
    """If every one of a seated beat's sentences duplicates an earlier beat, its
    first sentence is restored so the emitted beat-id set stays stable."""
    seq = _seq2(_beat("b1", ()), _beat("b2", ()))
    sents = [_ss(_DUP, "b1", 0), _ss(_DUP, "b2", 0)]
    out = suppress_exact_repeats(sents, seq)
    assert {"b1", "b2"} == {s.source_id for s in out}, "b2 must keep one sentence, not vanish"


def test_short_identical_fragments_are_left_alone():
    seq = _seq2(_beat("b1", ()), _beat("b2", ()))
    short = "It opened in 1875."
    sents = [_ss(short, "b1", 0), _ss(short, "b2", 0)]
    out = suppress_exact_repeats(sents, seq)
    assert len(out) == 2  # under the length floor — not treated as a restatement


def test_glue_sentences_are_never_deduped_by_exact_pass():
    seq = _seq2(_beat("b1", ()))
    nav = "Walk to the next stop."
    sents = [
        _ss(nav, "GLUE_NAV", 0, "glue"),
        _ss(nav, "GLUE_NAV", 1, "glue"),
        _ss("A long unique sentence about the opera house and its grand staircase.", "b1", 0),
    ]
    out = suppress_exact_repeats(sents, seq)
    assert [s.text for s in out].count(nav) == 2  # glue is the nav-glue pass's concern


# ---------------------------------------------------------------------------
# Content-loss / coverage gate — compose may merge or reword a fact but never
# silently DELETE one (the deletion blind spot the invention checks all shared).
# ---------------------------------------------------------------------------

def _script_of(*sentences: Sentence) -> Script:
    return Script(
        city_slug="paris",
        generated_at="2026-07-11T00:00:00+00:00",
        inputs=TourInput(start=(48.85, 2.35), duration_min=60, city_slug="paris"),
        total_audio_seconds=60, total_walking_seconds=60,
        total_walk_distance_m=100, total_planned_seconds=120,
        selected_pois=(), lens_coverage={}, script=tuple(sentences),
        validation=ValidationReport(),
    )


def _bbi(*beats: BeatRef) -> dict[str, BeatRef]:
    return {b.id: b for b in beats}


def test_claims_realized_by_finds_voiced_and_misses_unvoiced():
    beat = _beat("A", ("Napoleon placed four horses from Saint Mark on the arch",
                       "The arch was built between 1806 and 1808"))
    script = _script_of(
        _s("Napoleon placed four bronze horses from Saint Mark on the arch.", "A"),
    )  # claim 1 (the 1806-1808 date) is voiced by no sentence
    realized = claims_realized_by(script, _bbi(beat))
    assert ("A", 0) in realized
    assert ("A", 1) not in realized


def test_coverage_flags_a_dropped_distinct_fact():
    beat = _beat("A", ("Napoleon placed four horses from Saint Mark on the arch",
                       "The arch was built between 1806 and 1808"))
    bbi = _bbi(beat)
    stitched = _script_of(
        _s("Napoleon placed four bronze horses from Saint Mark on the arch.", "A"),
        _s("The arch was built between 1806 and 1808 by Napoleon.", "A"),
    )
    expected = claims_realized_by(stitched, bbi)
    # compose keeps the horses but DROPS the 1806-1808 date
    composed = _script_of(_s("Napoleon set four horses from Saint Mark atop the arch.", "A"))
    fails = verify_claim_coverage(composed, expected, bbi)
    assert fails and fails[0][0] == "A"
    assert "1806" in fails[0][1]


def test_coverage_passes_when_all_facts_kept():
    beat = _beat("A", ("Napoleon placed four horses from Saint Mark on the arch",
                       "The arch was built between 1806 and 1808"))
    bbi = _bbi(beat)
    stitched = _script_of(
        _s("Napoleon placed four bronze horses from Saint Mark on the arch.", "A"),
        _s("The arch was built between 1806 and 1808 by Napoleon.", "A"),
    )
    expected = claims_realized_by(stitched, bbi)
    assert verify_claim_coverage(stitched, expected, bbi) == ()  # passthrough loses nothing


def test_coverage_allows_paraphrase_merge_but_catches_dropped_distinct_fact():
    a = _beat("A", ("The Parisii settled the island in the third century BC",))
    b = _beat("B", ("Celtic Parisii first settled the island in the third century BC",
                    "The Romans invaded and took the island in 52 BC"))
    bbi = _bbi(a, b)
    stitched = _script_of(
        _s("The Parisii settled the island in the third century BC.", "A"),
        _s("The Celtic Parisii first settled the island in the third century BC.", "B"),
        _s("The Romans invaded and took the island in 52 BC.", "B"),
    )
    expected = claims_realized_by(stitched, bbi)
    # MERGE the two founding paraphrases into one telling; keep the Roman fact.
    merged = _script_of(
        _s("The Celtic Parisii settled the island in the third century BC.", "B"),
        _s("The Romans took the island in 52 BC.", "B"),
    )
    assert verify_claim_coverage(merged, expected, bbi) == (), "a legal merge must pass"
    # but DROPPING the distinct Roman fact is caught.
    lossy = _script_of(_s("The Celtic Parisii settled the island in the third century BC.", "B"))
    assert verify_claim_coverage(lossy, expected, bbi), "dropping a distinct fact must fail"


def test_full_verifier_coverage_gate_reports_deletion():
    """The dropped-fact check must NAME the beat whose fact compose deleted.

    It reports rather than refuses: the coverage check counts shared WORDS, so a
    paraphrase reads to it as a deletion, and it was demoted to advisory by owner
    ruling on 2026-08-03 (``ValidationReport.passed``; pinned by
    test_tour_authoring_gates.py::test_faithfulness_and_dropped_facts_are_advisory_not_blocking).
    What still has teeth is detection: a faithful passthrough is clean, and a real
    deletion appears on the report against beat "A".
    """
    from src.tour.compose_gate import build_full_verifier
    beat = _beat("A", ("Napoleon placed four horses from Saint Mark on the arch",
                       "The arch was built between 1806 and 1808"))
    seq = _seq(beat)
    bbi = _bbi(beat)
    stitched = _script_of(
        _s("Napoleon placed four bronze horses from Saint Mark on the arch.", "A"),
        _s("The arch was built between 1806 and 1808 by Napoleon.", "A"),
    )
    expected = claims_realized_by(stitched, bbi)
    verify = build_full_verifier(
        seq, bbi, expected_claim_ids=expected, allow_unverified_faithfulness=True
    )
    # a faithful passthrough is clean; a deletion is caught and named.
    passthrough = verify(stitched)
    assert passthrough.passed
    assert passthrough.coverage_failures == (), "a legal passthrough reported a dropped fact"
    lossy = _script_of(_s("Napoleon set four horses from Saint Mark atop the arch.", "A"))
    report = verify(lossy)
    assert report.coverage_failures and report.coverage_failures[0][0] == "A"
