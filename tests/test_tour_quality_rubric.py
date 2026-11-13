"""Pins the mechanical FLOOR of the tour quality standard.

Module under test: ``src/tour/quality_rubric.py``.

Every test here is a DISCRIMINATOR: delete the check it names from
``quality_rubric.py`` and the test must go RED. Each docstring states the defect it
guards and the exact UNDO that reddens it.

Hermetic: no Neo4j, no network, no LLM. Fixtures are the real pydantic contracts.
"""

from __future__ import annotations

import functools
import json
import math
from pathlib import Path

import pytest

from src.tour.contract import (
    POI,
    BeatRef,
    BeatSequence,
    Route,
    Script,
    ScriptPOI,
    Sentence,
    TourInput,
    TransitSegment,
    ValidationReport,
)
from src.tour.generation import _END_B_SENTINEL_PREFIX, GLUE_NAV, GLUE_REFLECTION
from src.tour.narration_quality import score_narration
from src.tour.quality_rubric import (
    BALANCE_MAX_SHARE,
    GORGE_MAX_WORDS_PER_STOP,
    MAX_SENTENCE_WORDS,
    MIN_STOP_SEPARATION_M,
    OUTLIER_YEAR_DENSITY_MULTIPLE,
    STARVE_MIN_BEATS,
    STARVE_MIN_WORDS_PER_BEAT,
    WORDS_PER_MINUTE,
    Finding,
    Severity,
    StopMaterial,
    c3_audio_floor_seconds,
    compose_fixable,
    score_tour,
)
from src.tour.routing import haversine_m

# ---------------------------------------------------------------------------
# Helpers — house style mirrors tests/test_tour_selection.py (_poi / _snap).
# ---------------------------------------------------------------------------


def _words(n: int, *, prefix: str) -> str:
    """``n`` GLOBALLY-unique words, so a length fixture never trips C5 by accident."""
    return " ".join(f"{prefix}{i}" for i in range(n))


def _well_formed(n: int, *, prefix: str) -> str:
    """``n`` GLOBALLY-unique words, broken into <=10-word sentences and opening
    with a look-cue — for a fixture that must stay silent on C9 (long sentences)
    and C10 (missing look-cue) as well as C5, e.g. isolating some OTHER check's
    BLOCKER from the WARN checks added alongside it.

    Each sentence's first word is capitalised: ``split_sentences``' boundary regex
    (generation.py's ``_SPLIT_RE``) only splits before an UPPERCASE next word, so an
    all-lowercase ``"q9. q10"`` never splits and silently stays one long sentence.
    """
    words = [f"{prefix}{i}" for i in range(n)]
    if words:
        words[0] = "Look"
    chunks = [words[i : i + 10] for i in range(0, len(words), 10)]
    sentences = []
    for chunk in chunks:
        chunk = [*chunk]
        chunk[0] = chunk[0].capitalize()
        sentences.append(" ".join(chunk) + ".")
    return " ".join(sentences)


_POI_SEQ: dict[str, int] = {}


def _coords(pid: str) -> tuple[float, float]:
    """Distinct, geographically PLAUSIBLE coords per POI id.

    C12 flags ADJACENT stops closer than MIN_STOP_SEPARATION_M (100 m), so fixtures
    must place POIs at real walking separations — otherwise every multi-stop fixture
    trips C12 at 0 m and the test is measuring the fixture, not the check. ~0.003 deg
    of latitude is ~333 m, comfortably a real leg. Deterministic per id (first-seen
    order), so a given fixture always yields the same geometry.
    """
    idx = _POI_SEQ.setdefault(pid, len(_POI_SEQ))
    return 48.85 + idx * 0.003, 2.35


def _spoi(
    pid: str, *, tier: int = 5, name: str | None = None, dwell_seconds: int = 0
) -> ScriptPOI:
    # ``dwell_seconds`` is HOW LONG THE TRAVELLER STANDS HERE, and since 2026-08-06
    # it is what C7 counts as the tour's non-walking time. It used to be inferred
    # from the script's stationary AUDIO, which said a stop lasts as long as it
    # talks — the model this release replaced. It defaults to 0 so every fixture
    # that does not care is unaffected.
    lat, lng = _coords(pid)
    return ScriptPOI(
        id=pid, name=name or pid, tier=tier, lat=lat, lng=lng, dwell_seconds=dwell_seconds
    )


def _poi(pid: str, *, tier: int = 5, name: str | None = None) -> POI:
    lat, lng = _coords(pid)
    return POI(
        id=pid,
        name=name or pid,
        tier=tier,
        poi_role="stop",
        lat=lat,
        lng=lng,
    )


def _beats(poi_id: str, n: int) -> list[BeatRef]:
    """``n`` real BeatRefs — the rubric only measures len(), but the corpus map is
    typed as beats, so the fixture uses the real model rather than a stand-in."""
    return [BeatRef(id=f"{poi_id}-b{i}", poi_id=poi_id) for i in range(n)]


def _sentence(text: str, stop_idx: int, *, source_type: str = "beat") -> Sentence:
    return Sentence(
        text=text,
        source_id=f"src-{stop_idx}-{abs(hash(text)) % 10_000}",
        source_type=source_type,
        stop_idx=stop_idx,
    )


def _script(
    sentences: list[Sentence],
    pois: list[ScriptPOI],
    *,
    # Default ABOVE the C3-thin floor (MIN_AUDIO_FRAC_OF_WALKING x 60 min = 11.4 min)
    # so a fixture aimed at some OTHER check does not trip C3 as a side effect. A test
    # about C3 passes a deliberately low value. Was 23.9 min while C3 derived its floor
    # from the retired disjoint walk/audio split.
    total_audio_seconds: int = 1800,
) -> Script:
    return Script(
        city_slug="paris",
        generated_at="2026-07-19T00:00:00Z",
        inputs=TourInput(start=(48.85, 2.35), duration_min=60, city_slug="paris"),
        total_audio_seconds=total_audio_seconds,
        total_walking_seconds=300,
        total_walk_distance_m=400,
        total_planned_seconds=900,
        selected_pois=tuple(pois),
        lens_coverage={},
        script=tuple(sentences),
        validation=ValidationReport(),
    )


def _route(
    pois: list[POI] | None = None,
    *,
    vignettes: dict[int, tuple[POI, ...]] | None = None,
    total_walk_seconds: int = 300,
    err_short_total_seconds: int = 0,
) -> Route:
    return Route(
        pois=tuple(pois or ()),
        transits=(),
        total_walk_distance_m=400.0,
        total_walk_seconds=total_walk_seconds,
        vignettes=vignettes or {},
        err_short_total_seconds=err_short_total_seconds,
    )


def _checks(report) -> list[str]:
    return [f.check for f in report.findings]


# ---------------------------------------------------------------------------
# C1 — starvation (BLOCKER). The Sainte-Chapelle defect.
# ---------------------------------------------------------------------------


def test_c1_starved_fires_on_the_measured_sainte_chapelle_case() -> None:
    """GUARDS: a rich POI reduced to a one-liner (C1).

    The REAL measured case: on the Ile de la Cite 60-min tour, Sainte-Chapelle
    (tier 5, 12 corpus beats) rendered as 9 words = 0.75 words/beat, against a floor
    of 12 x 12 = 144. UNDO: delete the ``if available >= STARVE_MIN_BEATS`` block in
    ``score_tour`` (or raise STARVE_MIN_WORDS_PER_BEAT's effect by making the
    comparison ``words < 0``) and this goes RED.
    """
    poi = _spoi("sainte-chapelle", tier=5, name="Sainte-Chapelle")
    nine_words = "Sainte-Chapelle stands here with its famous windows above you"
    assert len(nine_words.split()) == 9

    report = score_tour(
        _script([_sentence(nine_words, 0)], [poi]),
        _route([_poi("sainte-chapelle", tier=5)]),
        {"sainte-chapelle": _beats("sainte-chapelle", 12)},
    )

    starved = [f for f in report.findings if f.check == "C1-starved"]
    assert len(starved) == 1, _checks(report)
    assert starved[0].severity is Severity.BLOCKER
    assert starved[0].poi_name == "Sainte-Chapelle"
    assert starved[0].stop_idx == 0
    # The floor it was measured against is the standard's, not an ad-hoc number.
    assert f"{STARVE_MIN_WORDS_PER_BEAT * 12:.0f}" in starved[0].message
    assert not report.passed


def test_c1_does_not_fire_for_a_legitimately_thin_poi() -> None:
    """GUARDS: false positives on genuinely thin POIs (measured: the thin POIs
    around Sainte-Chapelle had 3 beats and were correctly short).

    3 corpus beats is below STARVE_MIN_BEATS=5, so a short render is an editorial
    outcome, not starvation. UNDO: lower STARVE_MIN_BEATS to 1 (or drop the
    ``available >= STARVE_MIN_BEATS`` guard) and this goes RED.
    """
    assert STARVE_MIN_BEATS == 5  # the fixture's 3 beats must stay under the bar

    poi = _spoi("palais-de-justice", tier=4, name="Palais de Justice")
    report = score_tour(
        _script([_sentence(_words(20, prefix="p"), 0)], [poi]),
        _route([_poi("palais-de-justice", tier=4)]),
        {"palais-de-justice": _beats("palais-de-justice", 3)},
    )

    assert "C1-starved" not in _checks(report)
    assert report.passed


# ---------------------------------------------------------------------------
# C8 — gorging (BLOCKER). The Notre-Dame defect.
# ---------------------------------------------------------------------------


def test_c8_gorged_fires_on_the_measured_over_cap_stop() -> None:
    """GUARDS: an unbroken wall of narration at one stop (C8).

    The REAL measured shape: Notre-Dame rendered ~1022-1038 words in a single
    stop, over even the walking-tour ADAPTATION of the museum sector's 250-word
    station ceiling (750 words ~ 5 min at 150 wpm). UNDO: delete the
    ``if words > GORGE_MAX_WORDS_PER_STOP`` block and this goes RED.
    """
    poi = _spoi("notre-dame", tier=5, name="Notre-Dame")
    report = score_tour(
        _script([_sentence(_words(1038, prefix="n"), 0)], [poi]),
        _route([_poi("notre-dame", tier=5)]),
        {"notre-dame": _beats("notre-dame", 59)},  # 1038 > 59*12, so C1 stays silent
    )

    gorged = [f for f in report.findings if f.check == "C8-gorged"]
    assert len(gorged) == 1, _checks(report)
    assert gorged[0].severity is Severity.BLOCKER
    assert gorged[0].poi_name == "Notre-Dame"
    assert "1038" in gorged[0].message
    assert "C1-starved" not in _checks(report)
    assert not report.passed


def test_c8_does_not_fire_at_exactly_the_cap() -> None:
    """GUARDS: the C8 boundary — the cap is inclusive (``>``, not ``>=``).

    A stop of exactly GORGE_MAX_WORDS_PER_STOP words is at budget, not over it.
    UNDO: change the comparison to ``words >= GORGE_MAX_WORDS_PER_STOP`` and this
    goes RED (an off-by-one that would reject a legal 5-minute anchor).
    """
    poi = _spoi("notre-dame", tier=5, name="Notre-Dame")
    at_cap = _words(GORGE_MAX_WORDS_PER_STOP, prefix="c")
    assert len(at_cap.split()) == GORGE_MAX_WORDS_PER_STOP

    report = score_tour(
        _script([_sentence(at_cap, 0)], [poi]),
        _route([_poi("notre-dame", tier=5)]),
        {"notre-dame": _beats("notre-dame", 59)},
    )

    assert "C8-gorged" not in _checks(report)
    assert report.passed


# ---------------------------------------------------------------------------
# C2 — tier inversion (BLOCKER).
# ---------------------------------------------------------------------------


def test_c2_tier_inversion_fires_when_a_rich_tier5_poi_is_only_a_vignette() -> None:
    """GUARDS: the headline POI demoted to a walk-past line while a lesser POI gets
    the full stop (C2).

    Fixture: tier-5 Sainte-Chapelle with 12 beats sits in ``route.vignettes`` while
    tier-4 Palais de Justice is the anchor. UNDO: delete the ``for vig in
    vignette_pois`` loop and this goes RED.
    """
    anchor = _spoi("palais-de-justice", tier=4, name="Palais de Justice")
    vignette = _poi("sainte-chapelle", tier=5, name="Sainte-Chapelle")

    report = score_tour(
        _script([_sentence(_words(200, prefix="a"), 0)], [anchor]),
        _route([_poi("palais-de-justice", tier=4)], vignettes={0: (vignette,)}),
        {"sainte-chapelle": _beats("sainte-chapelle", 12)},
    )

    inversions = [f for f in report.findings if f.check == "C2-tier-inversion"]
    assert len(inversions) == 1, _checks(report)
    assert inversions[0].severity is Severity.BLOCKER
    assert inversions[0].poi_name == "Sainte-Chapelle"
    assert not report.passed


def test_c2_does_not_fire_when_the_vignette_is_also_an_anchor() -> None:
    """GUARDS: false positive on a POI that is BOTH an anchor and listed as a
    vignette on its own approach leg — it did get a full stop, so it is no inversion.

    UNDO: delete the ``if vig.id in anchor_ids: continue`` line and this goes RED.
    """
    anchor = _spoi("sainte-chapelle", tier=5, name="Sainte-Chapelle")
    other = _spoi("palais-de-justice", tier=4, name="Palais de Justice")
    vignette = _poi("sainte-chapelle", tier=5, name="Sainte-Chapelle")

    report = score_tour(
        _script(
            [_sentence(_words(200, prefix="a"), 0), _sentence(_words(200, prefix="b"), 1)],
            [anchor, other],
        ),
        _route(
            [_poi("sainte-chapelle", tier=5), _poi("palais-de-justice", tier=4)],
            vignettes={0: (vignette,)},
        ),
        {"sainte-chapelle": _beats("sainte-chapelle", 12)},
    )

    assert "C2-tier-inversion" not in _checks(report)


# ---------------------------------------------------------------------------
# C12 — stops too close (WARN, demoted from BLOCKER 2026-07-19).
# ---------------------------------------------------------------------------


def test_c12_close_stops_is_a_warn_not_a_blocker() -> None:
    """GUARDS: the C12 severity demotion. MEASURED, 2026-07-19,
    across the full real Paris/New York corpora: distance alone cannot separate a
    true duplicate (0.0-1.7 m, a corpus geocoding defect) from a genuinely distinct,
    adjacent landmark — the product owner's OWN gold-text stops, Hotel Le Meurice
    and Angelina, sit **8.4 m** apart. A BLOCKER at 50 m would refuse
    to serve the gold-standard tour, which is worse than the defect C12 was written
    to catch (see tests/test_tour_selection.py::
    test_selection_does_not_filter_close_but_distinct_pois for the corpus
    measurement). This fixture places two distinct anchors 20 m apart -- inside
    MIN_STOP_SEPARATION_M (50 m) -- and asserts the finding surfaces but never
    blocks serving. UNDO: change C12's severity back to ``Severity.BLOCKER`` and
    this goes RED (``report.passed`` flips to False).
    """
    # ~20 m separation (1 deg latitude =~ 111_320 m; 0.00018 deg =~ 20 m).
    le_meurice = ScriptPOI(
        id="le-meurice", name="Hotel Le Meurice", tier=4, lat=48.8656, lng=2.3285
    )
    angelina = ScriptPOI(id="angelina", name="Angelina", tier=4, lat=48.86578, lng=2.3285)
    poi_a = POI(id="le-meurice", name="Hotel Le Meurice", tier=4, poi_role="stop",
                lat=48.8656, lng=2.3285)
    poi_b = POI(id="angelina", name="Angelina", tier=4, poi_role="stop",
                lat=48.86578, lng=2.3285)
    metres = haversine_m(le_meurice.lat, le_meurice.lng, angelina.lat, angelina.lng)
    assert metres < MIN_STOP_SEPARATION_M
    assert metres > 10  # a real, if short, walk -- not a duplicate-coordinate glitch

    report = score_tour(
        _script(
            [
                _sentence(_well_formed(60, prefix="m"), 0),
                _sentence(_well_formed(60, prefix="n"), 1),
            ],
            [le_meurice, angelina],
        ),
        _route([poi_a, poi_b]),
        {},
    )

    close = [f for f in report.findings if f.check == "C12-stops-too-close"]
    assert len(close) == 1, _checks(report)
    assert close[0].severity is Severity.WARN
    assert close[0].poi_name == "Angelina"
    assert report.blockers == []
    assert report.passed is True


def test_c12_does_not_fire_on_a_real_walking_separation() -> None:
    """GUARDS: false positives on POIs a genuine walking distance apart (the
    default fixture spacing, ~333 m). UNDO: raise MIN_STOP_SEPARATION_M above 333
    and this goes RED.
    """
    report = score_tour(
        _script(
            [_sentence(_words(30, prefix="p"), 0), _sentence(_words(30, prefix="q"), 1)],
            [_spoi("a", tier=3), _spoi("b", tier=3)],
        ),
        _route([_poi("a", tier=3), _poi("b", tier=3)]),
        {},
    )

    assert "C12-stops-too-close" not in _checks(report)


# ---------------------------------------------------------------------------
# C5 — verbatim repetition (BLOCKER).
# ---------------------------------------------------------------------------


def test_c5_verbatim_repeat_fires_on_a_repeated_sentence() -> None:
    """GUARDS: the same sentence spoken twice in one tour (C5, "say it once").

    Case-and-whitespace normalised, so a re-cased echo still counts. UNDO: delete the
    C5 loop (or the ``if key in seen`` branch) and this goes RED.
    """
    line = "The rose windows were assembled here in the thirteenth century"
    assert len(line.split()) >= 5

    report = score_tour(
        _script(
            [
                _sentence(line, 0),
                _sentence(_words(30, prefix="x"), 0),
                _sentence("  the ROSE windows were assembled here in the  thirteenth century ", 1),
            ],
            [_spoi("a", tier=3), _spoi("b", tier=3)],
        ),
        _route([_poi("a", tier=3), _poi("b", tier=3)]),
        {},
    )

    repeats = [f for f in report.findings if f.check == "C5-verbatim-repeat"]
    assert len(repeats) == 1, _checks(report)
    assert repeats[0].severity is Severity.BLOCKER
    assert repeats[0].stop_idx == 1
    assert "also at stop 0" in repeats[0].message
    assert not report.passed


def test_c5_does_not_fire_on_short_glue_lines_that_legitimately_recur() -> None:
    """GUARDS: false positives on navigational glue. "Walk on." / "Turn left here."
    recur by design across a tour and must not blocker-fail it.

    The module exempts keys under 5 words. UNDO: delete the
    ``if len(key.split()) < 5: continue`` guard and this goes RED — every tour with
    repeated nav glue would be refused.
    """
    report = score_tour(
        _script(
            [
                _sentence("Walk on.", 0, source_type="glue"),
                _sentence(_words(30, prefix="y"), 0),
                _sentence("Walk on.", 1, source_type="glue"),
                _sentence("Turn left here.", 1, source_type="glue"),
                _sentence(_words(30, prefix="z"), 1),
                _sentence("Turn left here.", 2, source_type="glue"),
                _sentence(_words(30, prefix="w"), 2),
            ],
            [_spoi("a", tier=3), _spoi("b", tier=3), _spoi("c", tier=3)],
        ),
        _route([_poi("a", tier=3), _poi("b", tier=3), _poi("c", tier=3)]),
        {},
    )

    assert "C5-verbatim-repeat" not in _checks(report)
    assert report.passed


# ---------------------------------------------------------------------------
# C6 — empty stop (BLOCKER).
# ---------------------------------------------------------------------------


def test_c6_empty_stop_fires_on_a_stop_with_zero_words() -> None:
    """GUARDS: a stop the tourist walks to and hears nothing at (C6).

    UNDO: delete the ``if words == 0`` loop and this goes RED.
    """
    report = score_tour(
        _script(
            [_sentence(_words(40, prefix="e"), 0), _sentence("", 1)],
            [_spoi("a", tier=3), _spoi("b", tier=3)],
        ),
        _route([_poi("a", tier=3), _poi("b", tier=3)]),
        {},
    )

    empties = [f for f in report.findings if f.check == "C6-empty-stop"]
    assert len(empties) == 1, _checks(report)
    assert empties[0].severity is Severity.BLOCKER
    assert empties[0].stop_idx == 1
    assert not report.passed


def test_c6_fires_when_the_stop_rendered_no_sentence_at_all() -> None:
    """GUARDS: the check being UNREACHABLE for the exact condition it names.

    The fixture above renders an EMPTY-TEXT Sentence for stop 1, which is the only
    shape the old loop could see: it iterated ``words_by_stop``, a dict keyed by the
    stop_idx of sentences that EXIST. A stop that rendered NO Sentence at all has no
    key there, so a completely silent stop — the walker routed somewhere and hearing
    nothing — scored PASS and was served.

    This fixture OMITS stop 1's Sentence entirely (not ``_sentence("", 1)``) and
    gives that POI 3 corpus beats, below STARVE_MIN_BEATS, so C1 cannot mask the
    hole by firing in C6's place.

    UNDO: revert the C6 loop to ``for stop_idx, words in sorted(words_by_stop.items())``
    and this goes RED (zero findings, report.passed True).
    """
    assert STARVE_MIN_BEATS == 5  # the fixture's 3 beats must stay under C1's bar

    silent = _spoi("silent-stop", tier=3, name="Silent Stop")
    report = score_tour(
        _script(
            [_sentence(_well_formed(40, prefix="sil"), 0)],  # nothing for stop 1
            [_spoi("heard", tier=3), silent],
        ),
        _route([_poi("heard", tier=3), _poi("silent-stop", tier=3)]),
        {"heard": _beats("heard", 3), "silent-stop": _beats("silent-stop", 3)},
    )

    empties = [f for f in report.findings if f.check == "C6-empty-stop"]
    assert len(empties) == 1, _checks(report)
    assert empties[0].severity is Severity.BLOCKER
    assert empties[0].stop_idx == 1
    assert empties[0].poi_name == "Silent Stop"
    assert "C1-starved" not in _checks(report), "C1 must not be what caught this"
    assert not report.passed


def test_c6_does_not_fire_on_the_content_free_pinned_endpoint_sentinel() -> None:
    """GUARDS: a false BLOCKER on the pinned-endpoint sentinel.

    ``selection._materialize_fixed_end_b`` materialises a user-pinned endpoint that
    has no POI of its own as ``__end_b__<lat>_<lng>`` (generation.py's
    ``_END_B_SENTINEL_PREFIX``). It is content-free BY DESIGN, so driving C6 off the
    roster must not turn every pinned-endpoint tour into a BLOCKER.

    UNDO: delete the ``poi.id.startswith(_END_B_SENTINEL_PREFIX)`` skip and this
    goes RED.
    """
    sentinel = _spoi(f"{_END_B_SENTINEL_PREFIX}48.86_2.36", tier=3, name="Destination")
    report = score_tour(
        _script(
            [_sentence(_well_formed(40, prefix="pin"), 0)],  # nothing for the sentinel
            [_spoi("real-stop", tier=3), sentinel],
        ),
        _route([_poi("real-stop", tier=3)]),
        {},
    )

    assert "C6-empty-stop" not in _checks(report)
    assert report.passed


def test_stats_n_stops_counts_the_roster_not_the_rendered_stops() -> None:
    """GUARDS: the editor's only signal that a stop went missing.

    ``n_stops`` was ``len(words_by_stop)``, which under-reports by exactly the stops
    that rendered nothing — so the one tour where the count matters is the one where
    it lies. A 2-stop tour with a silent stop must still report 2.

    UNDO: set ``stats["n_stops"] = len(words_by_stop)`` again and this goes RED.
    """
    report = score_tour(
        _script(
            [_sentence(_well_formed(40, prefix="ns"), 0)],
            [_spoi("ns-a", tier=3), _spoi("ns-b", tier=3)],
        ),
        _route([_poi("ns-a", tier=3), _poi("ns-b", tier=3)]),
        {},
    )

    assert report.stats["n_stops"] == 2
    assert report.stats["words_by_stop"] == {0: 40}


# ---------------------------------------------------------------------------
# Verdict semantics — passed is driven by BLOCKERs only.
# ---------------------------------------------------------------------------


def test_passed_is_false_with_a_blocker_and_true_with_only_warnings() -> None:
    """GUARDS: the serve/regenerate decision itself (a BLOCKER verdict regenerates
    the script).

    A WARN must never refuse a tour, and a BLOCKER must always refuse it. UNDO:
    change ``RubricReport.passed`` to ``not self.findings`` and the WARN half goes
    RED; change it to ``True`` and the BLOCKER half goes RED.
    """
    # BLOCKER half — an empty third stop. The two full stops split 50/50 (C4 stays
    # silent) and use _well_formed (short sentences, look-cue opener) so C9/C10
    # also stay silent — this half isolates the BLOCKER path from every WARN check.
    blocking = score_tour(
        _script(
            [
                _sentence(_well_formed(40, prefix="q"), 0),
                _sentence(_well_formed(40, prefix="k"), 1),
                _sentence("", 2),
            ],
            [_spoi("a", tier=3), _spoi("b", tier=3), _spoi("c", tier=3)],
        ),
        _route([_poi("a", tier=3), _poi("b", tier=3), _poi("c", tier=3)]),
        {},
    )
    assert blocking.blockers and not blocking.warnings
    assert blocking.passed is False
    assert blocking.summary().startswith("FAIL")

    # WARN half — a lopsided but otherwise legal tour.
    warning = score_tour(
        _script(
            [_sentence(_words(200, prefix="r"), 0), _sentence(_words(20, prefix="s"), 1)],
            [_spoi("a", tier=3), _spoi("b", tier=3)],
        ),
        _route([_poi("a", tier=3), _poi("b", tier=3)]),
        {},
    )
    assert warning.warnings and not warning.blockers
    assert warning.passed is True
    assert warning.summary().startswith("PASS")


def test_c4_imbalance_is_a_warn_and_does_not_flip_passed() -> None:
    """GUARDS: severity misclassification of C4 (C4 is WARN, surfaced to the editor,
    NOT a regeneration trigger).

    200/20 words = 91% in one stop, over BALANCE_MAX_SHARE=0.60. UNDO: change C4's
    severity to ``Severity.BLOCKER`` and this goes RED. Deleting the C4 block
    entirely also reddens it (the finding disappears).
    """
    assert BALANCE_MAX_SHARE == 0.60

    report = score_tour(
        _script(
            [_sentence(_words(200, prefix="t"), 0), _sentence(_words(20, prefix="u"), 1)],
            [_spoi("a", tier=3), _spoi("b", tier=3)],
        ),
        _route([_poi("a", tier=3), _poi("b", tier=3)]),
        {},
    )

    imbalances = [f for f in report.findings if f.check == "C4-imbalance"]
    assert len(imbalances) == 1, _checks(report)
    assert imbalances[0].severity is Severity.WARN
    assert imbalances[0].stop_idx == 0
    assert report.blockers == []
    assert report.passed is True


def test_c4_imbalance_does_not_fire_on_a_single_stop_tour() -> None:
    """GUARDS: false positive on a one-stop tour, where one stop necessarily holds
    100% of the words and an imbalance warning would be noise.

    UNDO: delete the ``and len(words_by_stop) > 1`` clause and this goes RED.
    """
    report = score_tour(
        _script([_sentence(_words(200, prefix="v"), 0)], [_spoi("a", tier=3)]),
        _route([_poi("a", tier=3)]),
        {},
    )

    assert "C4-imbalance" not in _checks(report)
    assert report.passed


# ---------------------------------------------------------------------------
# Stats — the numbers the workbench surfaces.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("audio_seconds,expected_minutes", [(600, 10.0), (930, 15.5)])
def test_stats_report_word_and_audio_totals(audio_seconds: int, expected_minutes: float) -> None:
    """GUARDS: the report's stats block, which the editor reads to triage a WARN.

    UNDO: drop ``total_words`` from ``report.stats`` (or stop summing per-stop words)
    and this goes RED.
    """
    report = score_tour(
        _script(
            [_sentence(_words(40, prefix="g"), 0), _sentence(_words(60, prefix="h"), 1)],
            [_spoi("a", tier=3), _spoi("b", tier=3)],
            total_audio_seconds=audio_seconds,
        ),
        _route([_poi("a", tier=3), _poi("b", tier=3)]),
        {},
    )

    assert report.stats["total_words"] == 100
    assert report.stats["n_stops"] == 2
    assert report.stats["words_by_stop"] == {0: 40, 1: 60}
    assert report.stats["audio_minutes"] == expected_minutes


# ---------------------------------------------------------------------------
# C7 — time-budget overrun (BLOCKER). INHERITED from routing.py.
# ---------------------------------------------------------------------------


def test_c7_fires_when_walk_plus_listening_exceeds_the_err_short_total() -> None:
    """GUARDS: the tour exceeding the ENGINE'S OWN err-short planning ceiling —
    ``duration_min * ERR_SHORT`` (83% of the request), a deliberately-short PLAN,
    not the tourist's promised duration. The check and the number
    are correct (they mirror the engine's own move ceiling, commit 6f6bc39); only
    the message must not claim the tourist "runs over the time they asked for" —
    a 41.7-min tour against this ceiling can still be comfortably UNDER a 60-min
    promise. INHERITED, not invented: routing.py:94-95 defines
    ``err_short_total_seconds = duration_min * ERR_SHORT * 60``; ``summarise_route``
    (routing.py:290-299) stamps that onto ``Route.err_short_total_seconds``, and
    score_tour reads it straight off the Route rather than recomputing anything.

    2000s standing at the stop + 500s walk = 2500s > the 2400s budget. UNDO: delete
    the ``if actual_total_s > route.err_short_total_seconds`` block and this goes RED.

    REWRITTEN 2026-08-06 against the new currency, not weakened. The arithmetic is
    unchanged — 2000 + 500 = 2500 against 2400 — but the 2000 is now the seconds the
    traveller SPENDS at the stop rather than the seconds it speaks. Under the old
    model those were the same number by definition, which is exactly the assumption
    this release removed: a visitor can stand twenty minutes at Concorde for four
    minutes of audio.
    """
    poi = _spoi("a", tier=3, dwell_seconds=2000)
    report = score_tour(
        _script([_sentence(_words(40, prefix="a"), 0)], [poi], total_audio_seconds=2000),
        _route([_poi("a", tier=3)], total_walk_seconds=500, err_short_total_seconds=2400),
        {},
    )

    overruns = [f for f in report.findings if f.check == "C7-time-budget"]
    assert len(overruns) == 1, _checks(report)
    assert overruns[0].severity is Severity.BLOCKER
    assert "2500" in overruns[0].message
    # ARITHMETICALLY FALSE claim a hostile review caught: the ceiling is the
    # engine's own err-short PLAN (duration * ERR_SHORT), not the tourist's
    # promised duration, so the message must not claim the tourist "runs over
    # the time they asked for". UNDO: put that phrase back in the C7 message and
    # this assertion goes RED.
    assert "asked for" not in overruns[0].message
    assert "err-short planning ceiling" in overruns[0].message
    assert not report.passed


def test_c7_does_not_fire_at_exactly_the_budget() -> None:
    """GUARDS: the C7 boundary — the budget is inclusive (``>``, not ``>=``).

    1900s audio + 500s walk = 2400s == the 2400s budget: on budget, not over it.
    UNDO: change the comparison to ``actual_total_s >= route.err_short_total_seconds``
    and this goes RED (an off-by-one that would refuse a tour that finishes on time).
    """
    poi = _spoi("a", tier=3)
    report = score_tour(
        _script([_sentence(_words(40, prefix="a"), 0)], [poi], total_audio_seconds=1900),
        _route([_poi("a", tier=3)], total_walk_seconds=500, err_short_total_seconds=2400),
        {},
    )

    assert "C7-time-budget" not in _checks(report)
    assert report.passed


def test_c7_skips_when_the_route_carries_no_budget() -> None:
    """GUARDS: false positives on a Route built without a duration (the field's own
    unset sentinel, ``0`` — e.g. a bare fixture in another test that never asked for
    a duration). A huge actual total must not spuriously blocker-fail such a Route.

    UNDO: delete the ``if route.err_short_total_seconds > 0`` guard and this goes RED.
    """
    poi = _spoi("a", tier=3)
    report = score_tour(
        _script([_sentence(_words(40, prefix="a"), 0)], [poi], total_audio_seconds=100_000),
        _route([_poi("a", tier=3)], total_walk_seconds=100_000, err_short_total_seconds=0),
        {},
    )

    assert "C7-time-budget" not in _checks(report)
    assert report.passed


# ---------------------------------------------------------------------------
# C9 — sentence length for the ear (WARN). CITED (Nubart).
# ---------------------------------------------------------------------------


def test_c9_long_mean_sentence_length_is_a_warn() -> None:
    """GUARDS: sentences too long to follow by ear (C9). REUSES
    narration_quality.score_narration — does not recompute mean sentence length itself.

    A single 25-word sentence has mean_sentence_words=25, over the 20-word cap.
    UNDO: delete the ``if nq.mean_sentence_words > MAX_SENTENCE_WORDS`` block and
    this goes RED.

    The cap was 15.0, CITED to Nubart's museum-station range, until 2026-07-27. It is
    now 20.0, a JUDGEMENT anchored on the gold text's measured 19.11 — see
    ``test_c9_cap_admits_the_owners_own_gold_text`` for why, and the constant's own
    docstring for the measurement.
    """
    assert MAX_SENTENCE_WORDS == 20.0
    poi = _spoi("a", tier=3)
    long_sentence = _words(25, prefix="w") + "."

    report = score_tour(
        _script([_sentence(long_sentence, 0)], [poi]),
        _route([_poi("a", tier=3)]),
        {},
    )

    warns = [f for f in report.findings if f.check == "C9-long-sentences"]
    assert len(warns) == 1, _checks(report)
    assert warns[0].severity is Severity.WARN
    assert report.blockers == []
    assert report.passed  # WARN never blocks serving


def test_c9_cap_admits_the_owners_own_gold_text() -> None:
    """THE CALIBRATION, made executable: the north star must pass its own bar.

    The gold text is the passage the owner hand-wrote and from which the standard's
    rules were derived. At the old cap of 15 it FAILED C9 —
    the standard's own gold text could not pass the standard's own check, and neither
    could any of 191 real tours.

    This is the guard against re-tightening the cap on cited authority that describes
    something other than a narrative walking tour. If it goes RED, either the cap moved
    below the gold or the gold changed; in both cases a human decides, and lowering the
    cap to silence this test is the wrong answer.

    NOT a tautology: the gold's measured 19.11 sits BELOW the cap of 20.0 with real
    margin, and 32 of 38 measured machine stops still fail — so the cap discriminates.
    ``test_c9_long_mean_sentence_length_is_a_warn`` covers the firing side.

    "C9 did not fire" is vacuously true in several states, so the guards are split.
    MEASURED, not assumed — each row below was reproduced by mutating the real constant:

    ==================================== ==========================================
    failure                              what catches it
    ==================================== ==========================================
    cap lowered below the gold           THIS test's C9 assertion (verified at 15.0)
    gold replaced with short text        THIS test's band, lower bound (a 3.5-word
                                         mean fails 18.0)
    cap widened until C9 cannot fire     the companion test (verified at 999.0)
    C9 deleted from the rubric           the companion test
    ==================================== ==========================================

    The band's upper bound is the cap itself, so it deliberately does NOT catch a widened
    cap — that is the companion's job, and it does it. Do not add coverage here that
    already exists there; do remove nothing.
    """
    from scripts.score_gold_text import extract_gold_text

    gold = extract_gold_text()
    poi = _spoi("a", tier=3)

    measured = score_narration(gold).mean_sentence_words
    assert 18.0 <= measured < MAX_SENTENCE_WORDS, (
        f"the gold measures {measured} words/sentence, outside the [18.0, "
        f"{MAX_SENTENCE_WORDS}) band this calibration assumes. Either §1 changed or the "
        "cap did. This test is only meaningful while the gold is long prose sitting just "
        "under the cap — re-derive the cap from the gold, do not widen this band to fit."
    )

    report = score_tour(
        _script([_sentence(gold, 0)], [poi]),
        _route([_poi("a", tier=3)]),
        {},
    )

    assert "C9-long-sentences" not in _checks(report), (
        "the owner's gold text now FAILS the sentence-length cap "
        f"(MAX_SENTENCE_WORDS={MAX_SENTENCE_WORDS}). A bar the north star cannot clear "
        "measures nothing — it fired on 100% of 191 real tours when this last happened. "
        "Do not lower the gold to fit; re-derive the cap from it."
    )


def test_c9_does_not_fire_on_short_punchy_sentences() -> None:
    """GUARDS: false positives on well-formed short sentences.

    UNDO: flip the comparison to ``<`` and this goes RED.
    """
    poi = _spoi("a", tier=3)
    short_sentences = "Look up. The towers rise high. Builders broke ground long ago."

    report = score_tour(
        _script([_sentence(short_sentences, 0)], [poi]),
        _route([_poi("a", tier=3)]),
        {},
    )

    assert "C9-long-sentences" not in _checks(report)


# ---------------------------------------------------------------------------
# OPENERS — UNCHECKED. C10 was deleted 2026-07-27; G1 was never built.
# ---------------------------------------------------------------------------


def test_openers_are_unchecked_since_c10_was_deleted() -> None:
    """PINS AN ACCEPTED GAP so it cannot be quietly forgotten: nothing in the rubric
    judges how a stop opens.

    C10 asked a MEANING question with a word list. ``narration_quality._LOOK_INITIAL``
    is a regex over 18 sentence-initial verbs, and the owner's own gold text — "Here, at
    the corner of rue de Castiglione and rue de Rivoli, stands the Hotel Le Meurice",
    the clearest orientation-before-history line in the standard and the sentence S1 was
    derived FROM — was rated "a bare fact, not a look-cue". It fired on 100% of 191 saved
    tours and on the north star itself, so it separated nothing. Extending the word list
    is the same shortcut with more words; the standard's answer is G1, judged
    semantically, and G1 does not exist.

    HOW THIS TEST WORKS. It scores two stops that differ ONLY in their opener — one bare
    fact, one look-cue — and asserts the rubric returns the SAME findings for both. That
    is precisely the statement "the rubric cannot tell them apart".

    WHEN G1 IS BUILT THIS TEST FAILS, because the bare-fact stop will pick up a finding
    the oriented one does not. That failure is the alarm and the whole point. Delete this
    test then — do not weaken the comparison to keep it green.
    """
    body = _words(30, prefix="x")
    poi = _spoi("a", tier=3)

    def _findings(opener: str) -> list[str]:
        # _checks returns a LIST, and the comparison below is on lists — stricter than
        # sets (it also catches a duplicate finding) and deliberately kept that way.
        report = score_tour(
            _script([_sentence(opener, 0), _sentence(body, 0)], [poi]),
            _route([_poi("a", tier=3)]),
            {},
        )
        return _checks(report)

    bare_fact = _findings("Notre-Dame was built in the twelfth century.")
    look_cue = _findings("Look up at the towers above you.")

    assert bare_fact == look_cue, (
        "the rubric now distinguishes a bare-fact opener from an oriented one — "
        f"bare-only findings: {sorted(set(bare_fact) - set(look_cue))}. If G1 (or any "
        "successor opener check) has been built, the gap this test pins is CLOSED: "
        "delete this test rather than relaxing it."
    )
    assert "C10-no-look-cue" not in bare_fact, "C10 was deleted; it must not return"


# ---------------------------------------------------------------------------
# C11 — date density for the ear (WARN, RELATIVE to the tour's own mean).
# ---------------------------------------------------------------------------


def test_c11_reports_a_stat_but_emits_no_finding() -> None:
    """C11 IS REPORT-ONLY, ruled 2026-07-30. It records outliers; it accuses nobody.

    The detector still works and this test still proves it: stop 2 packs four years
    into 18 words (~22.2/100w) while stops 0/1 have none, so the tour mean is
    ~7.4/100w and stop 2 is over 2x it. What changed is the OUTPUT — the outlier
    lands in ``stats["year_density_outliers"]`` and NOT in ``findings``.

    WHY, measured: C11 fired on 100% of the human-vetted reference tours (5 of 25
    Place des Vosges positions, 6 of 29 Ile) and on only 8-11% of machine tours. The
    multiple needed to admit the human references is 3.82/4.19, above the machine
    maximum of 3.11 — so no threshold clears the humans and still bites. A check that
    accuses this project's own north-star text while passing the output it polices is
    pointing the wrong way. Kept as a stat because the signal is real when you are
    diagnosing one stop; demoted because as a WARN it was pure noise.

    UNDO (two ways, both must go RED): delete the
    ``stats["year_density_outliers"]`` assignment, or re-add a
    ``report.findings.append`` for the outlier.
    """
    poi_a, poi_b, poi_c = _spoi("a", tier=3), _spoi("b", tier=3), _spoi("c", tier=3)
    no_dates_1 = "The garden sits beside the river and draws crowds every summer weekend here"
    no_dates_2 = "The market opened near the square and sold bread every single morning here"
    dense_dates = (
        "The chapel opened in 1248, was restored in 1345, and renovated again in "
        "1862, then again in 1920."
    )

    report = score_tour(
        _script(
            [
                _sentence(no_dates_1, 0),
                _sentence(no_dates_2, 1),
                _sentence(dense_dates, 2),
            ],
            [poi_a, poi_b, poi_c],
        ),
        _route([_poi("a", tier=3), _poi("b", tier=3), _poi("c", tier=3)]),
        {},
    )

    # The stat is present and names the right stop...
    assert report.stats["year_density_outliers"] == {2: 22.2}, report.stats
    # ...and NOTHING is emitted as a finding, at any severity.
    assert [f for f in report.findings if f.check == "C11-year-density-outlier"] == [], (
        "C11 was demoted to a report-only stat on 2026-07-30 and must emit no Finding; "
        + _checks(report)
    )
    assert report.passed and report.warnings == []


def test_c11_does_not_fire_when_date_density_is_even_across_stops() -> None:
    """GUARDS: false positives on a tour that legitimately speaks a date at every
    stop, evenly — the measured-good pattern ("born in Malaga in 1881"), not a
    date-list defect.

    Since the 2026-07-30 demotion this checks the STAT stays empty rather than that
    no finding fires — an evenly-dated tour must not even be reported as an outlier.

    UNDO: drop the ``* OUTLIER_YEAR_DENSITY_MULTIPLE`` (i.e. compare density to the
    bare mean) and this goes RED — an evenly-dated tour would self-flag.
    """
    assert OUTLIER_YEAR_DENSITY_MULTIPLE == 2.0
    poi_a, poi_b, poi_c = _spoi("a", tier=3), _spoi("b", tier=3), _spoi("c", tier=3)
    even_1 = "The chapel was consecrated in 1345 after decades of careful construction work nearby."
    even_2 = "The tower was completed in 1420 after decades of careful construction work nearby."
    even_3 = "The bridge was finished in 1503 after decades of careful construction work nearby."

    report = score_tour(
        _script([_sentence(even_1, 0), _sentence(even_2, 1), _sentence(even_3, 2)],
                [poi_a, poi_b, poi_c]),
        _route([_poi("a", tier=3), _poi("b", tier=3), _poi("c", tier=3)]),
        {},
    )

    assert "C11-year-density-outlier" not in _checks(report)
    assert report.stats.get("year_density_outliers") == {}, (
        "an evenly-dated tour must not even be REPORTED as an outlier: "
        f"{report.stats.get('year_density_outliers')}"
    )


def test_c8_cannot_fire_on_engine_output_and_the_unit_ruling_is_recorded() -> None:
    """C8's cap stays 850, and the reason a clean C8 proves nothing is pinned here.

    THE UNIT RULING (owner, 2026-07-30). C8's calibration unit is the STANDING
    POSITION, not the declared stop. Measured on the two human-vetted reference
    tours: no single position exceeds 367 words, so 850 is generous and every human
    position passes. The declared-stop aggregates that DO exceed it — Conciergerie
    901, Notre-Dame 1595 — are eye-prose spans covering many positions with walking
    in between, which is not a stop this engine builds. That closes the open
    C8-unit question; the cap does not move and the engine is not changed.

    THE TRAP THIS PINS. ``selection.MAX_DWELL_AUDIO_SECONDS`` caps one stop's audio,
    so at ``SPOKEN_WPM`` the widest stop the engine can render is far below 850 —
    C8 CANNOT FIRE on anything built today. Every gorged stop in the saved-tour
    corpus predates that ceiling. So "C8 is clean" is evidence about the dwell cap,
    never evidence that quality improved, and selection.py's own comment warns about
    exactly this misreading. Computed from the live constants, so if either moves
    this test re-derives instead of going stale.

    UNDO: raise MAX_DWELL_AUDIO_SECONDS above ~340s (or drop the cap) and the
    headroom assertion goes RED, correctly reporting that C8 became reachable.
    """
    from src.tour.generation import SPOKEN_WPM
    from src.tour.selection import MAX_DWELL_AUDIO_SECONDS

    assert GORGE_MAX_WORDS_PER_STOP == 850, (
        "the C8 cap was ruled UNCHANGED at 850 on 2026-07-30; 750 was rejected on "
        "measurement because the certified-good corpus carries stops at 757/761/808"
    )

    # The most words a single stop's dwell audio can carry, plus the documented
    # ~75-word glue reserve that rides along with it.
    renderable_words = MAX_DWELL_AUDIO_SECONDS * SPOKEN_WPM / 60
    widest_possible_stop = renderable_words + 75

    assert widest_possible_stop < GORGE_MAX_WORDS_PER_STOP, (
        f"C8 has become REACHABLE: a stop can now render up to "
        f"{widest_possible_stop:.0f} words ({MAX_DWELL_AUDIO_SECONDS}s at {SPOKEN_WPM} "
        f"wpm + 75 glue) against a cap of {GORGE_MAX_WORDS_PER_STOP}. That is not a "
        "failure — but the cap's unreachability was load-bearing for how C8 results "
        "are read, so re-measure the gorged population before trusting a clean C8."
    )


# ---------------------------------------------------------------------------
# compose_fixable — the loop-eligibility classifier. Exhaustive per check id.
#
# This is a SEPARATE predicate from severity/``passed``: it answers whether a
# targeted recompose of ONE stop could plausibly fix a given BLOCKER finding, not
# whether the tour may be served.
# ---------------------------------------------------------------------------


def _finding(
    check: str, *, severity: Severity = Severity.BLOCKER, context: dict | None = None
) -> Finding:
    return Finding(check=check, severity=severity, message="fixture", context=context)


def test_c6_empty_stop_is_always_fixable() -> None:
    """GUARDS: C6 rule — ALWAYS True (the model emitted nothing; recompose is the
    fix), regardless of material. UNDO: change the C6 branch to ``return False``
    and this goes RED.
    """
    assert compose_fixable(_finding("C6-empty-stop"), None) is True
    assert compose_fixable(_finding("C6-empty-stop"), StopMaterial(0, 0)) is True


def test_c5_verbatim_repeat_is_always_fixable() -> None:
    """GUARDS: C5 rule — True (same-stop dupes already die in compose's own
    _dedup_composed; a survivor is cross-stop and only a recompose removes it).
    UNDO: change the C5 branch to ``return False`` and this goes RED.
    """
    assert compose_fixable(_finding("C5-verbatim-repeat"), None) is True


def test_c8_gorged_is_fixable_only_when_the_composer_expanded() -> None:
    """GUARDS: the C8 split — True ONLY IF composed_words > seated_body_words (the
    COMPOSER inflated it). UNDO (true branch): flip the comparison to ``<`` and this
    goes RED.
    """
    expanded = StopMaterial(seated_body_words=200, composed_words=900)
    assert compose_fixable(_finding("C8-gorged"), expanded) is True


def test_c8_gorged_is_not_fixable_when_selection_seated_the_overshoot() -> None:
    """GUARDS: the C8 split's OTHER branch — False when composed_words <=
    seated_body_words: govern_poi_beats (beat_select.py:260-288) always keeps the
    first seated beat even when it alone blows the allowance
    (beat_select.py:269-271), so trimming in compose only triggers
    repair_composed_surgical (compose_gate.py:125) splicing the stitch straight
    back — a PROVEN oscillation. UNDO: delete the ``composed_words >
    seated_body_words`` comparison (make it unconditional True) and this goes RED.
    """
    selection_seated_it_all = StopMaterial(seated_body_words=900, composed_words=900)
    assert compose_fixable(_finding("C8-gorged"), selection_seated_it_all) is False
    assert compose_fixable(_finding("C8-gorged"), None) is False


def test_c1_starved_is_fixable_when_seated_material_already_cleared_the_floor() -> None:
    """GUARDS: the C1 split — True ONLY IF seated_body_words >= the SAME floor
    score_tour computed for this finding (carried in ``finding.context["floor"]``,
    since compose_fixable's fixed signature has no other channel to a per-POI
    number). UNDO (true branch): flip the comparison to ``<`` and this goes RED.
    """
    finding = _finding("C1-starved", context={"floor": 144.0})
    composer_under_used_it = StopMaterial(seated_body_words=200, composed_words=9)
    assert compose_fixable(finding, composer_under_used_it) is True


def test_c1_starved_is_not_fixable_when_the_material_was_never_seated() -> None:
    """GUARDS: the C1 split's OTHER branch — False when seated_body_words < the
    floor: the rest of the POI's beats sit in overflow_by_poi, never seated, so
    "write more" invites fabrication the entailment gate then rejects. UNDO: delete
    the ``material.seated_body_words >= floor`` comparison (make it unconditional
    True) and this goes RED.
    """
    finding = _finding("C1-starved", context={"floor": 144.0})
    never_seated_enough = StopMaterial(seated_body_words=40, composed_words=9)
    assert compose_fixable(finding, never_seated_enough) is False


def test_c1_starved_fails_closed_without_material_or_floor() -> None:
    """GUARDS: C1 must fail CLOSED — never guess — when it is missing the data it
    needs: no material, no context at all, or a context without a ``floor`` key.
    UNDO: replace any of these guards with a default floor of ``0`` and this goes
    RED (0 as a floor makes every seated_body_words >= 0, i.e. always True).
    """
    finding_with_floor = _finding("C1-starved", context={"floor": 144.0})
    assert compose_fixable(finding_with_floor, None) is False

    finding_no_context = _finding("C1-starved", context=None)
    assert compose_fixable(finding_no_context, StopMaterial(200, 9)) is False

    finding_empty_context = _finding("C1-starved", context={})
    assert compose_fixable(finding_empty_context, StopMaterial(200, 9)) is False


@pytest.mark.parametrize(
    "check",
    ["C3-thin", "C2-tier-inversion", "C12-stops-too-close", "C7-time-budget"],
)
def test_route_and_selection_defects_are_never_loop_eligible(check: str) -> None:
    """GUARDS: C3/C2/C12/C7 — all False, even with generous material. C3 is
    PROVEN unfixable (generation.py:1207: total_audio_seconds is scaled SEATED-beat
    voiced seconds via ``min(1.0, voiced/body)`` — more composed words cannot push
    it past 1.0). C2/C12 are route/selection decisions a stop-level recompose
    cannot change. C7 is a routing/selection budget total a recompose cannot move.
    UNDO: change any one of these branches to ``return True`` and that parametrized
    case goes RED.
    """
    generous_material = StopMaterial(seated_body_words=1000, composed_words=1000)
    assert compose_fixable(_finding(check), generous_material) is False
    assert compose_fixable(_finding(check), None) is False


def test_any_warn_severity_is_never_loop_eligible() -> None:
    """GUARDS: the blanket WARN rule — even a check id that IS BLOCKER-fixable
    elsewhere (e.g. C6) must return False when severity is WARN. Checked first, so
    it overrides every check-specific branch. UNDO: delete the
    ``if finding.severity is not Severity.BLOCKER: return False`` guard and this
    goes RED.
    """
    warn_but_normally_fixable_id = _finding("C6-empty-stop", severity=Severity.WARN)
    assert compose_fixable(warn_but_normally_fixable_id, StopMaterial(0, 0)) is False

    warn_c4 = _finding("C4-imbalance", severity=Severity.WARN)
    assert compose_fixable(warn_c4, StopMaterial(500, 500)) is False


def test_unknown_check_id_fails_closed() -> None:
    """GUARDS: an unclassified BLOCKER check id must return False, never guess.
    Money is never spent looping on something this classifier has no evidence
    about — including a plausible-looking but non-existent id and an empty string.
    UNDO: change the trailing ``return False`` to ``return True`` and this goes RED.
    """
    assert compose_fixable(_finding("C99-not-a-real-check"), StopMaterial(1000, 1000)) is False
    assert compose_fixable(_finding(""), StopMaterial(1000, 1000)) is False


# ---------------------------------------------------------------------------
# C7 / C7b — audio spoken WHILE WALKING is free (product ruling 2026-07-19)
# ---------------------------------------------------------------------------
#
# "Audio overlaps the walking. It is a part of the tour experience." — the owner.
#
# Before this ruling C7 added ALL audio to ALL walking, modelling a tourist who
# walks in silence and then stands in silence to listen. Under that model, filling
# a walk with narration APPEARS to consume the time budget even though it costs the
# tourist no extra minutes, so leg content can never be scored fairly.
#
# CORRECTION: an earlier revision of this comment claimed the old model produced a
# real deadlock on the founding tour ("1650s walk + 1434s audio = 3084s against a
# 2988s ceiling — BLOCKER for BLOCKER"). THAT WAS WRONG. The 1650s came from a route
# state produced by a since-reverted selection experiment; the real walk is 1087s,
# giving 2521s against 2988s — 467s of HEADROOM. No measured tour deadlocked. An
# adversarial review caught it, not the author.
#
# The fixtures below use DELIBERATELY EXTREME synthetic values to exercise the
# boundary; they are not measurements of any real tour and must not be read as such.


def _leg_sentence(text: str, stop_idx: int, source_id: str) -> Sentence:
    """A sentence spoken on the move (nav glue or a reflection)."""
    return Sentence(text=text, source_id=source_id, source_type="glue", stop_idx=stop_idx)


def _transit(walk_seconds: int, i: int) -> TransitSegment:
    return TransitSegment(
        from_poi_id=None if i == 0 else f"p{i - 1}",
        to_poi_id=f"p{i}",
        distance_m=100.0,
        walk_seconds=walk_seconds,
    )


def test_c7_excludes_reflection_audio_spoken_while_walking() -> None:
    """GUARDS the ruling: a reflection narrated on a leg costs NO elapsed time.

    UNDO TEST: revert C7 to ``route.total_walk_seconds + script.total_audio_seconds``
    and this goes RED — the tour trips C7 purely because its walking was filled.

    REWRITTEN 2026-08-06. The ruling it guards is unchanged and so is the shape of
    the proof; what moved is that the tour's non-walking time is now each stop's
    DWELL rather than its stationary audio. A reflection spoken on a leg still costs
    nothing, and now it costs nothing BY CONSTRUCTION — it is not a stop, so it has
    no dwell to contribute — instead of by subtracting concurrent seconds from a
    total. The concurrent/stationary split is still computed and still reported,
    because C7b judges it; it simply no longer decides C7.
    """
    pois = [_poi("p0"), _poi("p1")]
    # 300 words of reflection = 120s at 150 wpm, all spoken while walking.
    reflection = _leg_sentence(" ".join(f"w{i}" for i in range(300)), 1, GLUE_REFLECTION)
    stop_words = [_sentence("A grounded fact about this place.", 0)]
    script = _script(
        [*stop_words, reflection],
        [_spoi("p0", dwell_seconds=1680)],
        total_audio_seconds=1800,
    )
    route = Route(
        pois=tuple(pois),
        transits=(_transit(0, 0), _transit(900, 1)),
        total_walk_distance_m=400.0,
        total_walk_seconds=1650,
        err_short_total_seconds=2988,
    )
    report = score_tour(script, route, {}, beat_sequence=BeatSequence(poi_beats=()))

    assert report.stats["audio_concurrent_s"] == 120
    assert report.stats["audio_stationary_s"] == 1800 - 120
    # walk 1650 + the 1680s the traveller stands at p0 = 3330 > 2988. The 120s of
    # reflection is nowhere in that sum, which is the whole point.
    assert report.stats["time_budget_actual_s"] == 1650 + 1680


def test_c7_passes_when_the_deficit_is_closed_on_the_legs() -> None:
    """A tour whose audio all rides the legs must pass C7.

    SYNTHETIC, not a measurement: 1650s of walking is chosen so that walk + audio
    (1650 + 1434 = 3084) exceeds the 2988s err-short ceiling, which is the condition
    this check must handle. No real measured tour hit that condition — see the
    correction in the section comment above. The point here is the SEMANTICS: put
    the audio on the legs and the tourist's elapsed time is unchanged, so C7 passes
    however much narration the legs carry.
    """
    # 1434s of audio at 150 wpm = 3585 words, ALL on walking legs.
    leg_audio = _leg_sentence(" ".join(f"w{i}" for i in range(3585)), 1, GLUE_REFLECTION)
    script = _script([leg_audio], [], total_audio_seconds=1434)
    route = Route(
        pois=(_poi("p0"), _poi("p1")),
        transits=(_transit(0, 0), _transit(1650, 1)),
        total_walk_distance_m=400.0,
        total_walk_seconds=1650,
        err_short_total_seconds=2988,
    )
    report = score_tour(script, route, {}, beat_sequence=BeatSequence(poi_beats=()))

    assert "C7-time-budget" not in _checks(report), (
        "audio spoken while walking must not consume the time budget"
    )
    assert report.stats["time_budget_actual_s"] == 1650


def test_c7b_fires_when_leg_narration_outruns_the_walk() -> None:
    """Concurrent audio is free ONLY while the tourist is still walking.

    Narration longer than its leg either cuts off on arrival or holds the tourist on
    the pavement, so the free airtime is bounded by the walk itself.
    """
    # 1500 words = 600s of leg narration, but only 200s of walking exists.
    leg_audio = _leg_sentence(" ".join(f"w{i}" for i in range(1500)), 1, GLUE_REFLECTION)
    script = _script([leg_audio], [], total_audio_seconds=600)
    route = Route(
        pois=(_poi("p0"), _poi("p1")),
        transits=(_transit(0, 0), _transit(200, 1)),
        total_walk_distance_m=400.0,
        total_walk_seconds=200,
        err_short_total_seconds=2988,
    )
    report = score_tour(script, route, {}, beat_sequence=BeatSequence(poi_beats=()))
    assert "C7b-leg-audio-overruns-walk" in _checks(report)


def test_c7b_silent_when_leg_narration_fits_the_walk() -> None:
    leg_audio = _leg_sentence(" ".join(f"w{i}" for i in range(300)), 1, GLUE_REFLECTION)
    script = _script([leg_audio], [], total_audio_seconds=120)
    route = Route(
        pois=(_poi("p0"), _poi("p1")),
        transits=(_transit(0, 0), _transit(600, 1)),
        total_walk_distance_m=400.0,
        total_walk_seconds=600,
        err_short_total_seconds=2988,
    )
    report = score_tour(script, route, {}, beat_sequence=BeatSequence(poi_beats=()))
    assert "C7b-leg-audio-overruns-walk" not in _checks(report)


def test_stop_audio_still_counts_against_the_time_budget() -> None:
    """The ruling frees LEG audio only. Standing at a stop listening still costs
    the tourist minutes, and C7 must keep saying so.

    REWRITTEN 2026-08-06 against the new currency. A stop's length is now
    ``max(what the place is worth, what it says)``, so a stop with 1200s of
    narration lasts AT LEAST 1200s — the floor at the narration is what carries
    this test's claim, and it is the same claim.
    """
    stop_audio = [_sentence(" ".join(f"w{i}" for i in range(3000)), 0)]
    script = _script(stop_audio, [_spoi("p0", dwell_seconds=1200)], total_audio_seconds=1200)
    route = Route(
        pois=(_poi("p0"),),
        transits=(_transit(0, 0),),
        total_walk_distance_m=400.0,
        total_walk_seconds=1900,
        err_short_total_seconds=2988,
    )
    report = score_tour(script, route, {}, beat_sequence=BeatSequence(poi_beats=()))
    assert report.stats["audio_concurrent_s"] == 0
    assert "C7-time-budget" in _checks(report), (
        "1900s walk + 1200s of STANDING and listening exceeds 2988s and must block"
    )


def test_nav_glue_counts_as_walked_audio() -> None:
    nav = _leg_sentence(" ".join(f"w{i}" for i in range(150)), 1, GLUE_NAV)
    script = _script([nav], [], total_audio_seconds=60)
    route = _route(total_walk_seconds=600, err_short_total_seconds=2988)
    report = score_tour(script, route, {}, beat_sequence=BeatSequence(poi_beats=()))
    assert report.stats["audio_concurrent_s"] == 60


def test_missing_beat_sequence_is_conservative_about_vignettes() -> None:
    """Without a BeatSequence the vignette ids are unknown. Those sentences must
    count as STATIONARY — the strict direction, which can only make C7 fire more
    readily, never wrongly pass a tour."""
    script = _script([_sentence("A walk-past line.", 1)], [], total_audio_seconds=600)
    route = _route(total_walk_seconds=600, err_short_total_seconds=2988)
    report = score_tour(script, route, {}, beat_sequence=None)
    assert report.stats["audio_concurrent_s"] == 0
    assert report.stats["audio_stationary_s"] == 600


def test_c7b_uses_the_smaller_of_the_two_disagreeing_walk_measures() -> None:
    """C7b must never OVERSTATE the airtime a leg provides.

    route.total_walk_seconds and sum(transits.leg_seconds) are different numbers —
    measured across six real Paris tours they differ by -119s to +499s, because
    leg_seconds carries routed road-network values while total_walk_seconds is the
    pace-corrected haversine the budget math uses. C7 is denominated in
    total_walk_seconds; C7b is a BLOCKER about whether narration FITS, so it takes
    the conservative minimum. An adversarial review caught the two checks silently
    reasoning about different walks.

    UNDO TEST: change C7b back to the bare sum of leg_seconds and this goes RED —
    narration that overruns the shorter (real budget) walk would be waved through.
    """
    # legs claim 600s of walking; the budget currency says only 200s.
    leg_audio = _leg_sentence(" ".join(f"w{i}" for i in range(750)), 1, GLUE_REFLECTION)
    script = _script([leg_audio], [], total_audio_seconds=300)
    route = Route(
        pois=(_poi("p0"), _poi("p1")),
        transits=(_transit(0, 0), _transit(600, 1)),
        total_walk_distance_m=400.0,
        total_walk_seconds=200,
        err_short_total_seconds=2988,
    )
    report = score_tour(script, route, {}, beat_sequence=BeatSequence(poi_beats=()))
    assert report.stats["leg_walk_capacity_s"] == 200, (
        "C7b must use the SMALLER walk measure, not the optimistic one"
    )
    assert "C7b-leg-audio-overruns-walk" in _checks(report), (
        "300s of narration over a 200s real walk must block"
    )


# ---------------------------------------------------------------------------
# CALIBRATION ANCHORS — the tracked, real-Opus certification batch.
#
# Every threshold test above this line uses SYNTHETIC fixtures built to sit either
# side of a constant, which proves a check's LOGIC but never its THRESHOLD. These
# anchors close that gap: they are the eight authored tours already committed under
# data/certification/tour-batch-v1/ (model claude-opus-4-8, provenance
# provider_response, self-verifying artifact/response/customer_text sha256s), so a
# threshold can be calibrated against tours this project actually accepted instead
# of against another engine constant.
#
# Deliberately NOT data/*/tours/ — .gitignore:301 marks that "machine-local
# reproduction output, never committed", so an anchor there would pass here and
# vanish on a fresh clone.
# ---------------------------------------------------------------------------

_CERTIFIED_BATCH = (
    Path(__file__).resolve().parents[1] / "data" / "certification" / "tour-batch-v1"
)

#: MEASURED 2026-08-29 off the tracked artifacts at SPOKEN_WPM=150 — the re-seal:
#: the control arm is now the batch authored by the redesigned
#: engine (honest visit pricing, 180 s per-stop audio cap, the placement
#: floors and NEVER-STRENGTHEN in the writer's policy), so every day is SHORT
#: against the v1 corpus's 5-31 minutes. Pinned as EVIDENCE, independent of any
#: threshold: if a loader change moves these numbers, every calibration below is
#: measuring something else and must be re-derived.
_EXPECTED_AUDIO_MIN: dict[str, float] = {
    "nyc-central-park-open-90": 5.0,
    "nyc-grand-central-times-square-60": 2.1,
    "nyc-lower-manhattan-90": 5.2,
    "nyc-village-loop-60": 4.8,
    "paris-ile-open-90": 5.8,
    "paris-marais-loop-60": 2.2,
    "paris-pont-neuf-notre-dame-60": 6.5,
    "paris-west-axis-90": 3.0,
}

#: The corpus's own negative poles, and they are the SAME two days a blind
#: enjoyment judging scored below the old batch (both iterations):
#: west-axis at audio/walking 0.057 and grand-central at 0.081, against C3's 0.12
#: floor — thin starts whose corpus holds one or two content stops, so most of the
#: day is silent pavement. The rubric and the blind judge agree about which days
#: are thin, which is the calibration working. A thinness floor that passes these
#: is broken in the other direction. (The batch lane never runs score_tour — the
#: serving gate would refuse these two days live.)
_STARVED_CASES = frozenset({"paris-west-axis-90", "nyc-grand-central-times-square-60"})


def _certified_cases() -> list[tuple[str, Script, Route]]:
    """Reconstruct each tracked certified tour as a scoreable (case_id, Script, Route).

    ``beats_by_poi`` is passed EMPTY by callers and every POI is seated at tier 5, so
    C1-starved and C2-tier-inversion are inert here — these anchors calibrate the
    audio-volume checks (C3, C8), not the corpus-shape ones.

    ``err_short_total_seconds=0`` so C7 is skipped: the artifact does not record the
    planning budget its route was stamped with, and guessing one would make C7's
    verdict a property of this loader.
    """
    cases: list[tuple[str, Script, Route]] = []
    for tour_path in sorted(_CERTIFIED_BATCH.glob("*/*/tour.json")):
        payload = json.loads(tour_path.read_text(encoding="utf-8"))
        duration_min = payload["tour_input"]["duration_min"]
        raw_pois = payload["route"]["pois"]

        sentences: list[Sentence] = []
        words = 0
        for stop in payload["stops"]:
            stop_idx = stop["stop_index"]
            for order, text in enumerate(stop["sentences"]):
                words += len(text.split())
                sentences.append(
                    Sentence(
                        text=text,
                        source_id=f"{tour_path.parent.name}-{stop_idx}-{order}",
                        source_type="beat",
                        stop_idx=stop_idx,
                    )
                )

        script = Script(
            city_slug=payload["tour_input"]["city_slug"],
            generated_at="2026-07-26T00:00:00Z",
            inputs=TourInput(
                start=tuple(payload["tour_input"]["start"]),
                duration_min=duration_min,
                city_slug=payload["tour_input"]["city_slug"],
            ),
            total_audio_seconds=round(words / WORDS_PER_MINUTE * 60),
            total_walking_seconds=payload["route"]["total_walk_seconds"],
            total_walk_distance_m=payload["route"]["total_walk_distance_m"],
            total_planned_seconds=duration_min * 60,
            selected_pois=tuple(
                ScriptPOI(id=p["id"], name=p["name"], tier=5, lat=p["lat"], lng=p["lng"])
                for p in raw_pois
            ),
            lens_coverage={},
            script=tuple(sentences),
            validation=ValidationReport(),
        )
        route = Route(
            pois=tuple(
                POI(
                    id=p["id"],
                    name=p["name"],
                    tier=5,
                    poi_role="stop",
                    lat=p["lat"],
                    lng=p["lng"],
                )
                for p in raw_pois
            ),
            transits=(),
            total_walk_distance_m=payload["route"]["total_walk_distance_m"],
            total_walk_seconds=payload["route"]["total_walk_seconds"],
            vignettes={},
            err_short_total_seconds=0,
        )
        cases.append((payload["case_id"], script, route))
    return cases


def test_certification_corpus_reconstructs_as_scoreable_tours() -> None:
    """GUARDS the EVIDENCE the calibrations below rest on, not any threshold itself.

    UNDO: change the word-count or the WPM in ``_certified_cases`` and this goes RED,
    which is the point — every threshold below is derived from these exact minutes.
    """
    cases = _certified_cases()
    assert len(cases) == len(_EXPECTED_AUDIO_MIN), [c for c, _, _ in cases]

    measured = {
        case_id: round(script.total_audio_seconds / 60, 1) for case_id, script, _ in cases
    }
    assert measured == _EXPECTED_AUDIO_MIN

    # Anchors must live in a git-tracked location, never machine-local output.
    assert _CERTIFIED_BATCH.is_dir()
    for tour_path in _CERTIFIED_BATCH.glob("*/*/tour.json"):
        assert "tours" not in tour_path.parts, f"{tour_path} is machine-local output"

    # Every anchor must actually carry narration and stops, or it proves nothing.
    for case_id, script, route in cases:
        assert script.script, case_id
        assert route.pois, case_id


def test_c8_clears_the_widest_stop_in_the_certification_corpus() -> None:
    """GUARDS: the gorge cap must not brand real accepted anchors as bloated.

    RE-MEASURED 2026-08-29 on the re-sealed corpus: widest-stop
    population 297-511 words — every stop sits under the cap with real margin,
    because the redesigned engine's 180 s per-stop audio ceiling bounds a stop
    near ~525 words with glue. The corpus therefore no longer discriminates 850
    from the old 750 (the superseded v1 batch carried 757/761/808 stops and did);
    the synthetic 1038-word case below is what keeps the cap's bite proven.
    """
    for case_id, script, route in _certified_cases():
        report = score_tour(script, route, {})
        gorged = [f for f in report.findings if f.check == "C8-gorged"]
        assert gorged == [], f"{case_id}: {[f.message for f in gorged]}"

    # And the defect the cap exists for is STILL caught — this is what stops the
    # recalibration becoming a blanket amnesty.
    poi = _spoi("notre-dame", tier=5, name="Notre-Dame")
    report = score_tour(
        _script([_sentence(_words(1038, prefix="n"), 0)], [poi]),
        _route([_poi("notre-dame", tier=5)]),
        {"notre-dame": _beats("notre-dame", 59)},
    )
    assert [f.check for f in report.findings if f.check == "C8-gorged"] == ["C8-gorged"]


def test_c3_clears_the_certification_corpus_and_still_blocks_the_starved_tour() -> None:
    """GUARDS the thinness floor from BOTH directions — the whole point of C3.

    RE-DERIVED 2026-08-06 against WALKING time, on this same tracked batch. The
    question moved from "does this tour talk for a fifth of its length?" to "are the
    walks silent?", because once a stop lasts as long as the place is worth, a
    five-hour tour can spend three and a half hours standing still — and the old
    form demanded 57 minutes of continuous narration to approve it.

    MEASURED audio/WALKING on the tracked batch: healthy population 0.327-0.738
    (nyc-lower-manhattan-90 the thinnest good tour at 0.327), starved pole 0.059.
    The two poles are 5.5x apart under this denominator against 1.5x under the old
    one, which is itself evidence that walking is the quantity the check was
    reaching for.

    UNDO (pass side): put the denominator back to ``duration_min * 60`` and this
    goes RED on the flagship-shaped case below.
    UNDO (block side): delete the C3 block entirely and this goes RED on
    paris-west-axis-90 and on the silent-corridor run below.
    """
    for case_id, script, route in _certified_cases():
        report = score_tour(script, route, {})
        thin = [f for f in report.findings if f.check == "C3-thin"]
        if case_id in _STARVED_CASES:
            assert len(thin) == 1, f"{case_id} is starved and MUST be blocked"
            assert thin[0].severity is Severity.BLOCKER
        else:
            assert thin == [], f"{case_id}: {[f.message for f in thin]}"

    # THE BLOCK SIDE, restated for the new question. The old fixture here was "2
    # stops, 9.4 min of audio for a 60-min request" — thin against the REQUEST, but
    # its walking was never recorded, so it says nothing about whether its walks
    # were silent. Replaced with a case that is unambiguous under either reading and
    # was MEASURED off this repository's own tour files: 2.1 minutes of audio across
    # 76 minutes of walking. One minute of narration every thirty-six minutes on
    # foot is the tourist walking in silence by any definition.
    poi = _spoi("a", tier=5, name="A")
    starved = score_tour(
        _script([_sentence(_words(60, prefix="s"), 0)], [poi], total_audio_seconds=126),
        _route([_poi("a", tier=5)], total_walk_seconds=4548),
        {},
    )
    thin = [f for f in starved.findings if f.check == "C3-thin"]
    assert len(thin) == 1 and thin[0].severity is Severity.BLOCKER, _checks(starved)

    # THE PASS SIDE, and the reason the denominator moved at all: the tour this
    # release exists to produce. 22.6 min of audio across 85.8 min of walking, with
    # 213 minutes of that five hours spent standing in and around places. Judged
    # against the REQUEST it needs 57 minutes of narration and is refused; judged
    # against its walking it passes with a 2.2x margin.
    flagship = score_tour(
        _script(
            [_sentence(_words(60, prefix="f"), 0)],
            [_spoi("f", tier=5, name="F", dwell_seconds=12816)],
            total_audio_seconds=1356,
        ),
        _route([_poi("f", tier=5)], total_walk_seconds=5151),
        {},
    )
    assert [f for f in flagship.findings if f.check == "C3-thin"] == [], (
        "the 300-minute Rue Royale -> Notre-Dame tour was refused for being quiet "
        "while standing still, which is the tour working, not failing"
    )


def test_c3_floor_never_demands_more_words_than_c8_allows_at_any_offered_duration() -> None:
    """GUARDS satisfiability: C3 and C8 must never contradict each other.

    REDERIVED 2026-08-04, when the planner's stop ceiling was deleted.
    The old proof compared the floor against a CONSTANT capacity — what an eight-stop
    tour could hold — and the C3 floor had to be capped to stay under it. There is no
    constant any more: duration is the only bound on stop count, so a longer tour holds
    proportionally more stops, and the floor is linear again.

    The replacement proof is duration-INDEPENDENT, which is why it is stronger than the
    one it replaces. A tour meeting the C3 floor needs at least
    ``floor_seconds / MAX_DWELL_AUDIO_SECONDS`` stops, because the engine caps ONE
    stop's audio at ``MAX_DWELL_AUDIO_SECONDS``. Spread over that many stops, the words
    per stop come to ``MAX_DWELL_AUDIO_SECONDS / 60 x WORDS_PER_MINUTE`` — 675 — and
    that is under ``GORGE_MAX_WORDS_PER_STOP`` (850) at EVERY duration, so C8 can never
    be forced to fire by C3.

    UNDO: raise ``MAX_DWELL_AUDIO_SECONDS`` above ``GORGE_MAX_WORDS_PER_STOP /
    WORDS_PER_MINUTE x 60`` (= 340 s) and this goes RED at every duration — which is
    the real contradiction to guard now that neither side is capped by a stop count.
    """
    from src.tour.selection import MAX_DWELL_AUDIO_SECONDS

    for duration_min in range(1, 601):
        floor_s = c3_audio_floor_seconds(duration_min)
        stops_needed = math.ceil(floor_s / MAX_DWELL_AUDIO_SECONDS)
        assert stops_needed >= 1
        words_per_stop = floor_s / stops_needed / 60 * WORDS_PER_MINUTE
        assert words_per_stop <= GORGE_MAX_WORDS_PER_STOP, (
            f"{duration_min} min: meeting the C3 floor needs {words_per_stop:.0f} words "
            f"at each of {stops_needed} stops, which C8 blocks at "
            f"{GORGE_MAX_WORDS_PER_STOP}"
        )

    # And the floor really is linear again — the whole point of deleting the cap.
    assert c3_audio_floor_seconds(600) == pytest.approx(
        4 * c3_audio_floor_seconds(150)
    ), "the C3 thinness floor is still flattened by a fixed stop-count capacity"


def test_the_saved_tour_scorer_reconstructs_the_current_planning_ceiling() -> None:
    """On the SCORER — the offline scorer must not keep a ceiling the engine dropped.

    ``scripts/score_saved_tours.py`` rebuilds a ``Route`` from a persisted Script so the
    rubric can score tours that were saved months ago. The only value it has to
    reconstruct is C7's time ceiling, and its own docstring promises that ceiling
    "cannot drift from the engine's" because it CALLS the engine's helper rather than
    restating the arithmetic.

    That promise had a deadline. The helper it called, ``err_short_total_seconds``,
    returned a flat 0.83 of the requested duration; the engine's nominal fraction became
    1.00 when the legacy planning policy was deleted on 2026-08-04. Left alone the
    scorer would have judged every historical tour against a ceiling 20 percent tighter
    than the one those tours are now planned to, and the docstring would have been a
    lie. ``scripts/`` is outside the src-tree scan that guards the engine against a
    dropped ceiling, which is exactly why this needs its own node id.

    UNDO: point the scorer back at a 0.83 reconstruction and the equality below goes RED
    (2988 != 3600).
    """
    import importlib.util
    import pathlib as _pathlib

    from src.tour.routing import planned_total_seconds, route_planning_budget

    repo_root = _pathlib.Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "_score_saved_tours_under_test", repo_root / "scripts" / "score_saved_tours.py"
    )
    assert spec is not None and spec.loader is not None
    scorer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scorer)

    poi = _spoi("a", tier=5, name="A")
    script = _script([_sentence(_words(40, prefix="w"), 0)], [poi], total_audio_seconds=3000)
    assert script.inputs.duration_min == 60

    route = scorer._route_from_script(script)
    assert route.err_short_total_seconds == route_planning_budget(60).nominal_elapsed_seconds
    assert route.err_short_total_seconds == planned_total_seconds(60) == 3600, (
        "the saved-tour scorer rebuilt a 60-minute tour's C7 ceiling as "
        f"{route.err_short_total_seconds}s. The engine plans a 60-minute request to "
        "3600s of active time, so any other value means the scorer is judging the "
        "historical corpus against a ceiling the engine no longer uses"
    )


def test_c7_message_states_the_ceiling_the_route_was_actually_planned_to() -> None:
    """GUARDS: C7's message must not misreport the budget it measured against.

    It interpolated ``ERR_SHORT`` (83%) as a literal, but the premium/certification
    lane plans at a nominal fraction of 1.00 — so for every premium tour the message
    was wrong by 20 points while the ARITHMETIC used the route's own stamped total.
    UNDO: re-hardcode the percentage from ERR_SHORT and this goes RED.
    """
    poi = _spoi("a", tier=5, name="A", dwell_seconds=3000)
    report = score_tour(
        _script([_sentence(_words(40, prefix="w"), 0)], [poi], total_audio_seconds=3000),
        _route(
            [_poi("a", tier=5)],
            total_walk_seconds=3000,
            err_short_total_seconds=3600,  # a 60-min request planned at 1.00
        ),
        {},
    )
    over = [f for f in report.findings if f.check == "C7-time-budget"]
    assert len(over) == 1, _checks(report)
    assert "100%" in over[0].message, over[0].message
    assert "83%" not in over[0].message


# ---------------------------------------------------------------------------
# CALIBRATION AGAINST THE HUMAN-AUTHORED REFERENCE TOURS
#
# ``test_c9_cap_admits_the_owners_own_gold_text`` above calibrates ONE check
# against ONE 509-word passage. ``fixtures/reference-tours/`` holds two
# COMPLETE human-written tours; ``scripts/human_reference_tours.py`` loads them
# at BOTH granularities the documents support (declared stop / standing
# position) and these tests pin what the real rubric measures on them.
#
# Honesty about coverage: these tours exercise C3, C5, C8 (blockers) and C4,
# C9, C11 (warns). C1/C2/C6/C7/C7b/C12 are STRUCTURALLY SILENT here — the
# loader's docstring says exactly why each one cannot fire. Nothing in this
# section calibrates them.
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _reference_tours() -> tuple:
    from scripts.human_reference_tours import load_human_reference_tours

    return tuple(load_human_reference_tours())


def test_the_blocking_floor_admits_the_human_reference_at_position_granularity() -> None:
    """THE CALIBRATION, at the unit where a tourist actually stands and listens.

    A position is the deepest heading that owns narration — one standing spot.
    MEASURED 2026-07-29: the human reference never puts more than 367 words at a
    single position (PdV widest 239, Ile widest 367) against a C8 cap of 850, and
    the blocking floor admits both tours in full.

    If this goes RED, either a threshold was tightened past human-approved
    practice, or the parser collapsed (the scale guards catch that separately).
    Re-derive the threshold from the reference; do not edit the reference.

    NOT the whole story: at DECLARED-STOP granularity two Ile stops exceed the
    cap — the companion test below pins that contradiction. Read both.

    UNDO: lower ``GORGE_MAX_WORDS_PER_STOP`` below 367 and this goes RED.
    """
    tours = _reference_tours()
    assert len(tours) == 2, "expected both reference tours"

    for tour in tours:
        positions = tour.positions
        # Scale guards FIRST: a broken parser must fail loudly, never pass by
        # scoring an empty tour. Bands, not exact counts, so an editorial tweak
        # to a document does not redden the suite — but a parse collapse does.
        assert 20 <= len(positions) <= 40, (
            f"{tour.key} parsed {len(positions)} positions from "
            f"{tour.document.name}; expected 20-40. Either the document was "
            "restructured or the parser broke — do not widen this band to fit."
        )
        assert 2_500 <= tour.narration_words <= 6_000, (
            f"{tour.key} parsed {tour.narration_words} narration words; expected "
            "2500-6000. A collapsed parse scores an empty tour and passes "
            "everything vacuously."
        )
        widest = max(p.words for p in positions)
        assert 150 <= widest <= 500, (
            f"{tour.key}'s widest position is {widest} words; measured 239/367 "
            "on 2026-07-29. Outside [150, 500] the parser is grouping "
            "differently and every claim below is about different objects."
        )

        script, route = tour.script_by_position()
        report = score_tour(script, route, beats_by_poi={})

        assert report.blockers == [], (
            f"the human reference {tour.key} FAILS the blocking floor at "
            f"position granularity: {[f.check for f in report.blockers]}. A bar "
            "the reference cannot clear measures nothing. Re-derive the "
            "threshold from the reference; do not edit the reference."
        )
        assert report.passed, f"{tour.key} did not pass despite zero blockers"


def test_c8_contradicts_the_human_reference_at_declared_stop_granularity() -> None:
    """PINS A MEASURED CONTRADICTION so it cannot be resolved silently.

    The Ile document declares 8 ``## STOP`` sections (its golden fixture records
    the same 8 POIs). Grouped the document's own way, two stops exceed the C8
    cap: Conciergerie at 901 words and Notre-Dame at 1595 against a cap of 850.
    Yet no single POSITION inside them exceeds 367 — the tourist walks between
    rooms and portals while listening.

    So C8's verdict depends entirely on its unit. On machine output a stop is a
    POI at one GPS trigger, and C8's founding case was 1038 words at ONE spot;
    the human reference shows a POI legitimately carrying 1595 words ACROSS
    spots.

    RULED 2026-07-30 (owner): the calibration unit is the STANDING POSITION. These
    two declared-stop hits are therefore EXPECTED and correct — a declared stop in a
    guidebook walk is an eye-prose span over many positions, not a thing the engine
    builds — and the cap stays 850. This test no longer forces a decision; it pins
    the measurement the decision rests on, and still goes RED if C8 stops firing
    here, which would mean the loader is shredding stops again or the cap drifted.
    See test_c8_cannot_fire_on_engine_output_and_the_unit_ruling_is_recorded for the
    other half: on ENGINE output the cap is unreachable anyway.

    This is also the LOADER's regression guard: a parser that shreds stops back
    into positions (the first version's defect, caught by a judge consult)
    produces zero C8 findings here and goes RED.

    UNDO: raise ``GORGE_MAX_WORDS_PER_STOP`` above 1595 and this goes RED.
    """
    tour = next(t for t in _reference_tours() if t.key == "ile_oneway_90min")
    assert tour.has_stop_markers, "the Ile document lost its STOP markers"
    assert 10 <= len(tour.stops) <= 14, (
        f"parsed {len(tour.stops)} declared stops; the document has 12 level-2 "
        "sections (8 STOP + OPEN/ANCHOR/TRANSIT/CLOSE)."
    )

    script, route = tour.script_by_stop()
    report = score_tour(script, route, beats_by_poi={})
    gorged = sorted(f.poi_name for f in report.blockers if f.check == "C8-gorged")

    assert len(gorged) == 2 and "Conciergerie" in gorged[0] and "Notre-Dame" in gorged[1], (
        f"C8 fired on {gorged or 'nothing'} at declared-stop granularity; "
        "measured 2026-07-29 it fires on exactly Conciergerie (901 words) and "
        "Notre-Dame (1595), and the 2026-07-30 ruling keeps that expected (the unit "
        "is the standing position; a declared stop is an eye-prose span). So this "
        "means the loader is shredding stops again, or the cap drifted."
    )


def test_pdv_has_no_declared_stops_so_each_position_is_its_stop() -> None:
    """PINS THE GRANULARITY RULE for the document that does not declare stops.

    The Ile document marks stops explicitly and is scored at level-2. The PdV
    document does not: it is a circumnavigation of one square, and its level-2
    sections are narrative phases, not places — read at level-2 it has a
    2744-word ``CIRCUMNAVIGATION`` section that would be a third C8 blocker.
    That read is wrong (22 house-by-house pauses of ~125 words each, nobody
    stands at a section heading), so the loader treats each position as the
    stop. This test pins that branch, because it hangs on one boolean.

    If someone later adds ``## STOP`` headings to the PdV document — a plausible
    tidy-up, since the Ile document has them — the boolean flips, PdV starts
    scoring at level-2, and the 2744-word section becomes a blocker no other
    test watches. This goes RED first, and the human doing the tidy-up decides
    with eyes open.

    UNDO: force ``has_markers = True`` in the loader and this goes RED.
    """
    by_key = {t.key: t for t in _reference_tours()}

    assert by_key["ile_oneway_90min"].has_stop_markers, (
        "the Ile document lost its STOP markers; the declared-stop tests above "
        "are now scoring a different segmentation."
    )
    pdv = by_key["pdv_round_trip_60min"]
    assert not pdv.has_stop_markers, (
        "the PdV document now declares STOP headings, so the loader scores it "
        "at level-2 — where its 2744-word CIRCUMNAVIGATION section is a C8 "
        "blocker nothing else pins. If the headings were added deliberately, "
        "decide what its stops are and update this test in the same change."
    )
    assert len(pdv.stops) == len(pdv.positions), (
        "PdV's two granularities diverged; the no-markers branch is supposed "
        "to make them identical."
    )


def test_the_blocking_floor_still_fires_when_a_reference_tour_is_degraded() -> None:
    """GUARDS the position-granularity test against going vacuous.

    "No blocker fired" is trivially true if the floor is broken or disabled.
    This takes the SAME human prose, collapses all 3,265 words of the Place des
    Vosges tour onto a single stop, and requires C8 to catch it. The band
    between the widest real position (367) and the cap (850) is probed by the
    declared-stop test above, whose real values 901 and 1595 straddle the cap.

    UNDO: raise ``GORGE_MAX_WORDS_PER_STOP`` above 3265 and this goes RED.
    """
    tour = next(t for t in _reference_tours() if t.key == "pdv_round_trip_60min")
    script, _ = tour.script_by_position()
    every_word = " ".join(s.text for s in script.script)

    report = score_tour(
        _script([_sentence(every_word, 0)], [_spoi("a", tier=3)]),
        _route([_poi("a", tier=3)]),
        {},
    )

    assert "C8-gorged" in _checks(report), (
        f"{len(every_word.split())} words on one stop did not trip C8 "
        f"(cap={GORGE_MAX_WORDS_PER_STOP}). The blocking floor cannot fire, so "
        "the calibration above is vacuous."
    )


def test_c9_fires_on_most_stops_of_the_human_authored_reference_tours() -> None:
    """PINS WHY C9 FIRES ON THE HUMAN REFERENCES **BY DESIGN**, ruled 2026-07-30.

    C9 is a WARN, so it blocks nothing — and at position granularity it fires on 24
    of 25 positions (96%) of one human-approved tour and 19 of 29 (66%) of the other.
    That reads like a miscalibration and was very nearly "fixed" as one. It is not.

    THE RULING (owner, 2026-07-30): C9 stays exactly as it is, cap 20, unchanged.
    Two measurements decide it. First, C9 ORDERS CORRECTLY on the thing this project
    actually calls its north star: the owner's hand-written gold measures 19.11
    words/sentence and PASSES, against a machine median of 23.2 that does not — the
    gold reads shorter than 34 of 38 machine stops. Second, the cap matches the
    external ear-prose guidance (12-18 words) that the online reference set
    contributes. The two empirical walk documents fire it because they are
    transcribed GUIDEBOOK prose — text for the EYE, medians 25.7 and 23.2 — while
    C9 measures fitness for the EAR. Firing on eye-prose is the check working.

    So this test is no longer a decision forcer; it records a settled reading. It
    still goes RED on any recalibration, which is what keeps the reading honest:
    anyone who widens the cap must also break
    ``test_c9_cap_admits_the_owners_own_gold_text`` and say why the gold no longer
    anchors the number.

    UNDO: recalibrate ``MAX_SENTENCE_WORDS`` upward from the reference tours and
    this goes RED, on purpose.
    """
    for tour in _reference_tours():
        script, route = tour.script_by_position()
        report = score_tour(script, route, beats_by_poi={})
        hits = [f for f in report.findings if f.check == "C9-long-sentences"]
        share = len(hits) / len(tour.positions)

        assert share >= 0.5, (
            f"C9 now fires on only {share:.0%} of {tour.key}'s positions (was "
            "96% and 66% when measured 2026-07-29). If C9 was recalibrated from "
            "the human reference this is the intended outcome — delete this "
            "test in the same change and say so. If it moved for another "
            "reason, that is a regression in the calibration."
        )
        assert all(f.severity is Severity.WARN for f in hits), (
            "C9 escalated above WARN while still firing on most positions of a "
            "human-approved tour — that would refuse to serve the reference."
        )


# ---------------------------------------------------------------------------
# C13 — POINT FIRST (WARN). Every piece is written with its point in the FIRST minute:
# a visitor may walk off at minute eight of nine, routinely. The point is the turn with
# its stakes, inside the stop's first ~100 words, counted from the stop's OWN first
# sentence about the place — never from the nav line or the recap (a listener should
# not sit through a recap of what they heard five minutes earlier; count in words, not
# seconds, from the first sentence about the place they stand in). What a
# $0 check can see of that: (i) the STORY starts early — the words of glue before the
# stop's first beat sentence (the recap, the walking line) stay under
# POINT_FIRST_PREAMBLE_MAX_WORDS; (ii) the stop's PRIMARY beat — the first of its capped
# plan, `ScriptPOI.beat_ids[0]` — is cited inside the first POINT_FIRST_STORY_WORDS of
# the story. Whether that sentence IS the point is a reading, judged by people, never
# gated here.
# ---------------------------------------------------------------------------


def _c13_stop(
    *,
    preamble_words: int,
    primary_after_words: int,
    pid: str = "c13",
    stop_idx: int = 0,
) -> tuple[list[Sentence], ScriptPOI]:
    """One stop: ``preamble_words`` of glue (a recap), then ``primary_after_words`` of
    OTHER beats' text, then the primary beat's sentence, then a tail. The primary beat
    is ``beat_ids[0]`` of the ScriptPOI — the first of the capped plan."""
    primary = f"{pid}-primary"
    other = f"{pid}-other"
    sents: list[Sentence] = []
    if preamble_words:
        sents.append(
            Sentence(
                text=_words(preamble_words, prefix=f"{pid}recap"),
                source_id="GLUE_REFLECTION",
                source_type="glue",
                stop_idx=stop_idx,
            )
        )
    if primary_after_words:
        sents.append(
            Sentence(
                text=_words(primary_after_words, prefix=f"{pid}other"),
                source_id=other,
                source_type="beat",
                stop_idx=stop_idx,
            )
        )
    sents.append(
        Sentence(
            text=_words(12, prefix=f"{pid}point"),
            source_id=primary,
            source_type="beat",
            stop_idx=stop_idx,
        )
    )
    sents.append(
        Sentence(
            text=_words(40, prefix=f"{pid}tail"),
            source_id=other,
            source_type="beat",
            stop_idx=stop_idx,
        )
    )
    lat, lng = _coords(pid)
    spoi = ScriptPOI(
        id=pid, name=pid, tier=5, lat=lat, lng=lng, beat_ids=(primary, other)
    )
    return sents, spoi


def test_c13_point_first_warns_when_the_story_starts_late_or_the_primary_beat_lands_late():
    """A stop that opens with a 60-word recap (a measured family day's stop 4: 44 s
    of "think back over where we've walked") WARNs on its preamble; a stop
    whose primary beat is first cited 150 words into the story WARNs on its point; a
    stop that places the listener in one sentence and lands its primary beat inside the
    first hundred words is silent. UNDO: drop C13 from score_tour -> RED."""
    from src.tour.quality_rubric import (
        POINT_FIRST_PREAMBLE_MAX_WORDS,
        POINT_FIRST_STORY_WORDS,
    )

    # (i) the recap preamble
    sents, spoi = _c13_stop(preamble_words=60, primary_after_words=0)
    report = score_tour(_script(sents, [spoi]), _route([_poi("c13")]), {})
    c13 = [f for f in report.findings if f.check == "C13-point-late"]
    assert c13 and c13[0].severity is Severity.WARN, _checks(report)
    assert "recap" in c13[0].message or "before the story" in c13[0].message, c13[0].message
    assert POINT_FIRST_PREAMBLE_MAX_WORDS < 60

    # (ii) the primary beat lands late
    sents, spoi = _c13_stop(preamble_words=8, primary_after_words=150, pid="c13b")
    report = score_tour(_script(sents, [spoi]), _route([_poi("c13b")]), {})
    c13 = [f for f in report.findings if f.check == "C13-point-late"]
    assert c13 and c13[0].severity is Severity.WARN, _checks(report)
    assert "primary" in c13[0].message, c13[0].message
    assert POINT_FIRST_STORY_WORDS < 150

    # point first: one placing sentence, the primary beat at word 30 -> silent
    sents, spoi = _c13_stop(preamble_words=8, primary_after_words=30, pid="c13c")
    report = score_tour(_script(sents, [spoi]), _route([_poi("c13c")]), {})
    assert "C13-point-late" not in _checks(report), _checks(report)


def test_c13_skips_stops_with_no_story_and_the_pinned_end_sentinel():
    """A rest (no beats) and the content-free pinned endpoint have no point to land;
    C13 says nothing about them (C6 owns the empty stop)."""
    from src.tour.generation import _END_B_SENTINEL_PREFIX

    rest = ScriptPOI(id="bench", name="Bench", tier=1, lat=48.851, lng=2.35, beat_ids=())
    end = ScriptPOI(
        id=f"{_END_B_SENTINEL_PREFIX}x", name="Your finish point", tier=3, lat=48.852, lng=2.35
    )
    sents = [
        Sentence(
            text="Walk on for a minute.", source_id="GLUE_NAV", source_type="glue", stop_idx=0
        ),
        Sentence(
            text="From here, make your way to your final destination.",
            source_id="GLUE_NAV",
            source_type="glue",
            stop_idx=1,
        ),
    ]
    report = score_tour(_script(sents, [rest, end]), _route([_poi("bench"), _poi("end")]), {})
    assert "C13-point-late" not in _checks(report), _checks(report)
