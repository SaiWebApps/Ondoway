"""VERIFY — rapidfuzz provenance + faithfulness entailment. Hermetic.

PROVE: a beat whose source_passage is verbatim in its chunk passes; a
fabricated/wrong-chunk passage fails with its score; beats without
provenance are skipped (corpus not backfilled). Faithfulness: a stub that
rejects a claim flags exactly that beat-cited sentence; the Mock default
trusts the corpus.
"""

from __future__ import annotations

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
from src.tour.verify import (
    PROVENANCE_MATCH_THRESHOLD,
    MockFaithfulnessChecker,
    verify_faithfulness,
    verify_provenance,
)

CHUNK = (
    "The square was commissioned by Henri IV and completed in 1612. "
    "It was the first planned square in Paris, originally the Place Royale, "
    "and Victor Hugo lived at number 6 from 1832 to 1848."
)


def _beat(bid: str, **overrides) -> BeatRef:
    base = {"id": bid, "poi_id": "p1"}
    base.update(overrides)
    return BeatRef(**base)


def _seq(beats: list[BeatRef]) -> BeatSequence:
    return BeatSequence(
        poi_beats=(
            POIBeats(poi_id="p1", poi_name="Place des Vosges",
                     ordering_strategy="trigger_address", beats=tuple(beats)),
        )
    )


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def test_verbatim_passage_passes_provenance():
    beat = _beat("b1", source_passage="completed in 1612", source_chunk_slug="vosges")
    fails = verify_provenance(_seq([beat]), {"vosges": CHUNK})
    assert fails == []


def test_lightly_drifted_passage_still_passes():
    # punctuation/whitespace drift — partial_ratio is substring-tolerant
    beat = _beat("b1", source_passage="completed  in 1612.", source_chunk_slug="vosges")
    fails = verify_provenance(_seq([beat]), {"vosges": CHUNK})
    assert fails == []


def test_fabricated_passage_fails_with_low_score():
    beat = _beat(
        "b1",
        source_passage="Napoleon was crowned emperor on this very spot in 1804",
        source_chunk_slug="vosges",
    )
    fails = verify_provenance(_seq([beat]), {"vosges": CHUNK})
    assert len(fails) == 1
    bid, score = fails[0]
    assert bid == "b1"
    assert score < PROVENANCE_MATCH_THRESHOLD


def test_missing_chunk_fails_at_zero():
    beat = _beat("b1", source_passage="anything", source_chunk_slug="not-loaded")
    fails = verify_provenance(_seq([beat]), {"vosges": CHUNK})
    assert fails == [("b1", 0.0)]


def test_beat_without_provenance_is_skipped():
    # The current corpus state: no source_passage → nothing to check.
    plain = _beat("b1", script_body="Henri IV built it.")
    fails = verify_provenance(_seq([plain]), {"vosges": CHUNK})
    assert fails == []


# ---------------------------------------------------------------------------
# Faithfulness
# ---------------------------------------------------------------------------


def _script(sentences: list[Sentence]) -> Script:
    return Script(
        city_slug="paris", generated_at="2026-06-12T00:00:00Z",
        inputs=TourInput(start=(48.85, 2.36), duration_min=60, city_slug="paris"),
        total_audio_seconds=0, total_walking_seconds=0, total_walk_distance_m=0,
        total_planned_seconds=0,
        selected_pois=(ScriptPOI(id="p1", name="PdV", tier=5, lat=48.85, lng=2.36),),
        lens_coverage={}, script=tuple(sentences), validation=ValidationReport(),
    )


class _RejectingChecker:
    """Rejects any sentence containing a banned token."""

    def __init__(self, banned: str):
        self.banned = banned
        self.calls = 0

    def entails(self, key_claims, sentence_text):
        self.calls += 1
        return self.banned not in sentence_text


def test_unfaithful_sentence_is_flagged():
    beat = _beat("b1", key_claims=("Henri IV built it", "completed 1612"))
    beats_by_id = {"b1": beat}
    good = Sentence(text="Henri IV built the square.", source_id="b1",
                    source_type="beat", stop_idx=0)
    bad = Sentence(text="Aliens built the square.", source_id="b1",
                   source_type="beat", stop_idx=0)
    fails = verify_faithfulness(_script([good, bad]), beats_by_id,
                                _RejectingChecker("Aliens"))
    assert len(fails) == 1
    assert fails[0][0] is bad
    assert fails[0][1] == "unfaithful:b1"


def test_mock_checker_trusts_corpus_and_records_calls():
    beat = _beat("b1", key_claims=("a claim",))
    checker = MockFaithfulnessChecker()
    s = Sentence(text="A beat sentence.", source_id="b1", source_type="beat", stop_idx=0)
    fails = verify_faithfulness(_script([s]), {"b1": beat}, checker)
    assert fails == []
    assert len(checker.calls) == 1  # one entailment call for the one beat sentence


def test_glue_sentences_and_claimless_beats_skip_entailment():
    beat_no_claims = _beat("b1")  # no key_claims
    checker = MockFaithfulnessChecker()
    sentences = [
        Sentence(text="Walk on.", source_id="GLUE_NAV", source_type="glue", stop_idx=0),
        Sentence(text="A beat sentence.", source_id="b1", source_type="beat", stop_idx=0),
    ]
    fails = verify_faithfulness(_script(sentences), {"b1": beat_no_claims}, checker)
    assert fails == []
    assert checker.calls == []  # glue skipped, claimless beat skipped


def test_keyless_beat_with_body_is_gated_against_its_script_body():
    """Keyless-corpus faithfulness (round-3 red-team): a beat with key_claims=() but a
    script_body (the entire London corpus) must be entailed against that BODY — not
    skipped — so invention on a keyless corpus is caught. UNDO: revert verify.py's
    skip to key_claims-only -> no entailment call -> RED."""
    beat = _beat("b1", key_claims=(), script_body="The Great Fire began in Pudding Lane in 1666.")
    ok = MockFaithfulnessChecker()
    s_ok = Sentence(text="In 1666 the Great Fire started at Pudding Lane.",
                    source_id="b1", source_type="beat", stop_idx=0)  # reworded, not verbatim
    assert verify_faithfulness(_script([s_ok]), {"b1": beat}, ok) == []
    assert len(ok.calls) == 1  # the keyless beat WAS entailed (against its body), not skipped
    assert "Pudding Lane" in ok.calls[0][0][0]  # support tuple = the script_body
    reject = _RejectingChecker("Aliens")
    s_bad = Sentence(text="Aliens landed at Pudding Lane in 1666.",
                     source_id="b1", source_type="beat", stop_idx=0)
    assert verify_faithfulness(_script([s_bad]), {"b1": beat}, reject)  # invention flagged


# ---------------------------------------------------------------------------
# Reflections — fail-closed against VISITED key_claims
# ---------------------------------------------------------------------------


def _reflection(text: str, stop_idx: int) -> Sentence:
    return Sentence(text=text, source_id="GLUE_REFLECTION", source_type="glue", stop_idx=stop_idx)


def _beat_sentence(bid: str, stop_idx: int, text: str = "A beat sentence.") -> Sentence:
    return Sentence(text=text, source_id=bid, source_type="beat", stop_idx=stop_idx)


def test_the_thread_entails_against_visited_claims_plus_its_own_stops_beats():
    """The GLUE_REFLECTION slot holds THE THREAD — one sentence binding THIS stop to
    the walk through ONE fact of this stop, never a recap. Its support is therefore
    what the walker has heard PLUS the arriving stop's own beats; under a visited-only
    window the thread's defining fact would be inadmissible by construction. UNDO: drop
    the own-stop half of the support -> RED (the union below loses b3's claim and body)."""
    b1 = _beat("b1", key_claims=("Henri IV built it", "completed 1612"))
    b2 = _beat("b2", key_claims=("Hugo lived at number 6",))
    b3 = _beat("b3", key_claims=("the same tribunal sat here",), script_body="Body three.")
    checker = MockFaithfulnessChecker()
    sentences = [
        _beat_sentence("b1", 0),
        _beat_sentence("b2", 1),
        _reflection("The tribunal you heard about sat here too.", 2),
        _beat_sentence("b3", 2),
    ]
    fails = verify_faithfulness(_script(sentences), {"b1": b1, "b2": b2, "b3": b3}, checker)
    assert fails == []
    union = (
        "Henri IV built it",
        "completed 1612",
        "Hugo lived at number 6",
        "the same tribunal sat here",
        "Body three.",
    )
    thread_calls = [c for c in checker.calls if c[0] == union]
    assert len(thread_calls) == 1


def test_unfaithful_reflection_is_flagged():
    b1 = _beat("b1", key_claims=("Henri IV built it",))
    bad = _reflection("Aliens built everything you have seen.", 1)
    fails = verify_faithfulness(
        _script([_beat_sentence("b1", 0), bad]), {"b1": b1}, _RejectingChecker("Aliens")
    )
    assert fails == [(bad, "unfaithful_reflection")]


def test_a_threads_own_stops_facts_are_admissible_support():
    """An earlier version pinned the OPPOSITE here ("claims at the reflection's own
    stop are not yet heard" — the recap's window). The thread binds the ARRIVING stop
    through one of ITS facts, so a slot whose only claims
    live at its own stop is now verifiable, entailed against them."""
    b1 = _beat("b1", key_claims=("only claim, at the same stop",))
    checker = MockFaithfulnessChecker()
    thread = _reflection("Consider the one claim of this stop.", 1)
    fails = verify_faithfulness(
        _script([thread, _beat_sentence("b1", 1)]), {"b1": b1}, checker
    )
    assert fails == []
    thread_calls = [c for c in checker.calls if "Consider" in c[1]]
    assert len(thread_calls) == 1


def test_a_thread_with_no_support_anywhere_fails_closed():
    """A claimless, bodiless walk gives the thread nothing to entail from — it fails
    BEFORE the checker (an unverifiable line never ships, under the union window
    as under the old visited-only one)."""
    b1 = _beat("b1")  # visited, but claimless and bodiless
    checker = MockFaithfulnessChecker()
    thread = _reflection("A synthesis of nothing.", 1)
    fails = verify_faithfulness(
        _script([_beat_sentence("b1", 0), thread]), {"b1": b1}, checker
    )
    assert fails == [(thread, "unverifiable_reflection:no_support")]
    assert checker.calls == []  # claimless beat skipped; the thread failed pre-checker


def test_validation_report_passed_gates_on_new_teeth():
    """Provenance BLOCKS; the entailment miss RECORDS.

    Both teeth used to block. The entailment one was demoted to advisory by owner
    ruling on 2026-08-03 (it fired on ~a fifth of a good tour, so it could not decide
    whether to ship one) — see ``ValidationReport.passed`` and
    test_tour_authoring_gates.py::test_faithfulness_and_dropped_facts_are_advisory_not_blocking.
    The demotion is not a deletion, so the report must still CARRY the finding.
    """
    base = ValidationReport()
    assert base.passed
    assert not ValidationReport(provenance_failures=(("b1", 12.0),)).passed
    s = Sentence(text="x", source_id="b1", source_type="beat", stop_idx=0)
    unfaithful = ValidationReport(faithfulness_failures=((s, "unfaithful:b1"),))
    assert unfaithful.passed, "the entailment check advises, it does not block"
    assert unfaithful.faithfulness_failures == ((s, "unfaithful:b1"),), (
        "advisory must still mean recorded — the finding vanished from the report"
    )


# ---------------------------------------------------------------------------
# Corpus-is-canonical faithfulness (live-gate calibration, 2026-07-02)
# ---------------------------------------------------------------------------


def test_verbatim_corpus_sentence_skips_entailment():
    """A beat sentence appearing verbatim in its beat's script_body is
    trivially faithful — no checker call (the corpus is canonical)."""
    beat = _beat(
        "b1",
        script_body="Henri IV built the square. It opened in 1612 to great crowds.",
        key_claims=("Henri IV built it",),
    )
    checker = MockFaithfulnessChecker()
    s = Sentence(
        text="It opened in 1612 to great crowds.",
        source_id="b1",
        source_type="beat",
        stop_idx=0,
    )
    fails = verify_faithfulness(_script([s]), {"b1": beat}, checker)
    assert fails == []
    assert checker.calls == []


def test_attribution_strip_fragment_is_not_shortcut_but_entailment_checked():
    """REGRESSION: the verbatim shortcut must require WHOLE sentence-units, not
    substring containment. Stripping the hedge off a qualified corpus claim
    ("According to lore, X" -> "X") is a strict substring of script_body but
    turns a hedged claim into an unqualified assertion — it MUST be checked."""
    beat = _beat(
        "b1",
        script_body="According to lore, the architect died on the day it opened.",
        key_claims=("lore says the architect died on opening day",),
    )
    checker = MockFaithfulnessChecker()
    stripped = Sentence(
        text="the architect died on the day it opened.",
        source_id="b1",
        source_type="beat",
        stop_idx=0,
    )
    verify_faithfulness(_script([stripped]), {"b1": beat}, checker)
    assert len(checker.calls) == 1, "hedge-stripped fragment must reach the entailment gate"


def test_negation_truncation_fragment_is_entailment_checked():
    """REGRESSION: a truncation that drops the negation ("He never named the
    hotel after himself" -> "named the hotel after himself") inverts the claim
    while remaining a substring — the gate must not short-circuit it."""
    beat = _beat(
        "b1",
        script_body="He never named the hotel after himself.",
        key_claims=("he did not name the hotel after himself",),
    )
    truncated = Sentence(
        text="named the hotel after himself.",
        source_id="b1",
        source_type="beat",
        stop_idx=0,
    )
    checker = MockFaithfulnessChecker()
    verify_faithfulness(_script([truncated]), {"b1": beat}, checker)
    assert len(checker.calls) == 1

    rejecting = _RejectingChecker("named")
    fails = verify_faithfulness(_script([truncated]), {"b1": beat}, rejecting)
    assert fails == [(truncated, "unfaithful:b1")]


def test_full_verbatim_sentence_still_short_circuits():
    """The shortcut must still hold for a COMPLETE corpus sentence, and for a
    contiguous run of complete sentences — zero failures, zero checker calls."""
    beat = _beat(
        "b1",
        script_body="Henri IV built the square. It opened in 1612 to great crowds.",
        key_claims=("Henri IV built it",),
    )
    for text in (
        "It opened in 1612 to great crowds.",
        "Henri IV built the square. It opened in 1612 to great crowds.",
    ):
        checker = MockFaithfulnessChecker()
        s = Sentence(text=text, source_id="b1", source_type="beat", stop_idx=0)
        assert verify_faithfulness(_script([s]), {"b1": beat}, checker) == []
        assert checker.calls == []


def test_rewritten_sentence_entails_against_claims_plus_body():
    """An LLM-rewritten beat sentence is checked against key_claims AND the
    beat's script_body — the claims alone are a summary subset."""
    beat = _beat(
        "b1",
        script_body="Henri IV built the square. It opened in 1612 to great crowds.",
        key_claims=("Henri IV built it",),
    )
    checker = MockFaithfulnessChecker()
    rewritten = Sentence(
        text="Crowds poured in when the square opened in 1612.",
        source_id="b1",
        source_type="beat",
        stop_idx=0,
    )
    fails = verify_faithfulness(_script([rewritten]), {"b1": beat}, checker)
    assert fails == []
    (support, _text) = checker.calls[0]
    assert "Henri IV built it" in support
    assert any("opened in 1612" in c for c in support)  # the body rides along


def test_invented_fact_still_fails():
    beat = _beat(
        "b1",
        script_body="Henri IV built the square.",
        key_claims=("Henri IV built it",),
    )
    bad = Sentence(
        text="Aliens landed here in 1613.", source_id="b1", source_type="beat", stop_idx=0
    )
    fails = verify_faithfulness(_script([bad]), {"b1": beat}, _RejectingChecker("Aliens"))
    assert fails == [(bad, "unfaithful:b1")]


def test_faithfulness_entailment_runs_concurrently():
    """The independent per-sentence entailment calls run in PARALLEL: 16 beat
    sentences against a 50ms checker finish well under the ~800ms a sequential
    run would take — this is the fix for the ~15-min live compose-gate latency."""
    import time

    class _SlowChecker:
        def entails(self, key_claims, sentence_text):
            time.sleep(0.05)
            return True

    beats = {f"b{i}": _beat(f"b{i}", key_claims=(f"claim number {i}",)) for i in range(16)}
    sentences = [_beat_sentence(f"b{i}", i) for i in range(16)]
    t0 = time.perf_counter()
    fails = verify_faithfulness(_script(sentences), beats, _SlowChecker())
    elapsed = time.perf_counter() - t0
    assert fails == []
    assert elapsed < 0.4, f"concurrent verify should be < 0.4s, got {elapsed:.2f}s (~0.8s serial)"


def test_multibeat_fused_sentence_entails_against_the_union_of_cited_beats():
    """A fused sentence combining two beats' facts is faithful only when it CITES
    both (also_cites) — the entailment support is their UNION, not the primary
    beat alone. This is what lets a cross-book merge pass instead of reverting."""
    import re

    a = _beat("A", key_claims=("the square was renamed in 1800",))
    b = _beat("B", key_claims=("Napoleon promised naming rights to the district",))
    bbi = {"A": a, "B": b}

    class _Containment:
        def entails(self, key_claims, sentence_text):
            support = " ".join(key_claims).lower()
            return all(w in support for w in re.findall(r"[a-z]{4,}", sentence_text.lower()))

    txt = "In 1800 Napoleon promised naming rights to the district"
    solo = Sentence(text=txt, source_id="A", source_type="beat", stop_idx=0)
    fused = Sentence(text=txt, source_id="A", also_cites=("B",), source_type="beat", stop_idx=0)
    # cited to A alone -> B's content is unsupported -> a faithfulness failure
    assert verify_faithfulness(_script([solo]), bbi, _Containment())
    # cited to A + B -> entailed by the union -> clean
    assert verify_faithfulness(_script([fused]), bbi, _Containment()) == []
