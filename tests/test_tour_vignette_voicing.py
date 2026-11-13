"""The stitcher VOICES walk-past vignettes inside the leg.

The seam: BeatSequence gains the
ADDITIVE ``vignette_beats`` field (leg_idx -> chosen beats); ``generate()``
emits, in the transit stage of a leg with vignettes, one beat-cited sentence
per vignette (the FIRST sentence of its beat — corpus text). validate_script
derives its known-id set from poi_beats + vignette_beats INTERNALLY (its
signature does not change). ``_build_anchor_block`` never sees vignette
beats — they are not POIBeats entries.

Pure — no Neo4j, MockGlueClient only.
"""

from __future__ import annotations

import datetime as dt

from src.tour.beat_select import select_vignette_beats
from src.tour.contract import (
    POI,
    BeatRef,
    BeatSequence,
    POIBeats,
    Route,
    Script,
    Sentence,
    TourInput,
    TransitSegment,
    ValidationReport,
)
from src.tour.generation import GLUE_NAV, GLUE_REFLECTION, SPOKEN_WPM, generate
from src.tour.glue_client import MockGlueClient
from src.tour.reflection import reflection_slots
from src.tour.render_md import stop_narration_text
from src.tour.validation import validate_script

_NOW = dt.datetime(2026, 7, 2, tzinfo=dt.UTC)

# ---------------------------------------------------------------------------
# Fixture: two dwell stops + one vignette beat on leg 1 (the p1 → p2 walk)
# ---------------------------------------------------------------------------

_VIGNETTE_BODY = "The Médicis fountain dates from 1685. It still trickles today."
_VIGNETTE_ONE_LINER = "The Médicis fountain dates from 1685."


def _poi(pid: str, name: str) -> POI:
    return POI(
        id=pid, name=name, tier=5, poi_role="stop", lat=48.8555, lng=2.3656,
        areas=("Le Marais",),
    )


def _beat(bid: str, poi_id: str, body: str, **kw) -> BeatRef:
    return BeatRef(
        id=bid, poi_id=poi_id, script_body=body, active_status="active",
        word_count=len(body.split()), **kw,
    )


def _vignette_beat(**kw) -> BeatRef:
    # est_spoken_seconds deliberately large: only the FIRST sentence is
    # voiced, so audio must count the flat per-sentence estimate, not 100s.
    return _beat("vb-fountain", "v-fountain", _VIGNETTE_BODY, est_spoken_seconds=100, **kw)


def _seq(vignette_beats: dict[int, tuple[BeatRef, ...]] | None = None) -> BeatSequence:
    p1_plan = POIBeats(
        poi_id="p1", poi_name="Place des Vosges", ordering_strategy="narrative_function",
        beats=(_beat("b1", "p1", "Henri IV built the square. It opened to crowds."),),
    )
    p2_plan = POIBeats(
        poi_id="p2", poi_name="Hotel de Sully", ordering_strategy="narrative_function",
        beats=(_beat("b2", "p2", "The mansion hides a quiet garden. Sully lived here."),),
    )
    kwargs = {"vignette_beats": vignette_beats} if vignette_beats is not None else {}
    return BeatSequence(poi_beats=(p1_plan, p2_plan), **kwargs)


def _route(walk_seconds: int = 60) -> Route:
    pois = (_poi("p1", "Place des Vosges"), _poi("p2", "Hotel de Sully"))
    transits = tuple(
        TransitSegment(
            from_poi_id=None if i == 0 else pois[i - 1].id,
            to_poi_id=p.id,
            distance_m=100.0,
            walk_seconds=walk_seconds,
        )
        for i, p in enumerate(pois)
    )
    return Route(
        pois=pois, transits=transits, total_walk_distance_m=200.0,
        total_walk_seconds=walk_seconds * 2,
        spine_area="Le Marais",
    )


_INPUT = TourInput(start=(48.8555, 2.3656), duration_min=60, city_slug="paris")


def _generate(seq: BeatSequence) -> Script:
    return generate(seq, _route(), _INPUT, glue_client=MockGlueClient(), now=_NOW)


# ---------------------------------------------------------------------------
# The one-liner: emitted in the leg's transit stage, beat-cited, validated
# ---------------------------------------------------------------------------


def test_leg_narration_contains_beat_cited_vignette_one_liner():
    script = _generate(_seq({1: (_vignette_beat(),)}))

    v_sents = [s for s in script.script if s.source_id == "vb-fountain"]
    assert len(v_sents) == 1, "exactly one one-liner, never the full beat"
    v = v_sents[0]
    assert v.text == _VIGNETTE_ONE_LINER  # FIRST sentence only
    assert v.source_type == "beat"
    assert v.stop_idx == 1  # the leg INTO stop 1


def test_vignette_sits_after_transit_glue_before_anchor_beats():
    script = _generate(_seq({1: (_vignette_beat(),)}))
    ids = [s.source_id for s in script.script]
    v_pos = ids.index("vb-fountain")
    assert ids[v_pos - 1] == GLUE_NAV, "one-liner lands right after the leg's nav glue"
    assert "b2" in ids[v_pos + 1 :], "stop 1's anchor beats follow the one-liner"


def test_vignette_validation_passes():
    script = _generate(_seq({1: (_vignette_beat(),)}))
    assert script.validation.passed, (
        f"untraceable={[s.text for s in script.validation.untraceable_sentences]} "
        f"forbidden={script.validation.forbidden_phrase_hits}"
    )


def test_vignette_beat_never_appears_as_anchor_block():
    """The vignette beat is not a POIBeats entry: its SECOND sentence (an
    anchor block would stream the whole body) must never surface."""
    script = _generate(_seq({1: (_vignette_beat(),)}))
    all_text = " ".join(s.text for s in script.script)
    assert _VIGNETTE_ONE_LINER in all_text
    assert "It still trickles today." not in all_text


def test_two_vignettes_on_one_leg_voiced_in_order():
    vb2 = _beat("vb-gate", "v-gate", "An iron gate guards the passage. Few notice it.")
    script = _generate(_seq({1: (_vignette_beat(), vb2)}))
    ids = [s.source_id for s in script.script]
    assert ids.index("vb-fountain") < ids.index("vb-gate")
    assert [s.text for s in script.script if s.source_id == "vb-gate"] == [
        "An iron gate guards the passage."
    ]
    assert script.validation.passed


def test_stop_narration_text_places_one_liner_in_the_legs_piece_not_the_stops():
    """The walk-past one-liner is heard ON THE LEG into stop 1, so it rides stop 1's
    LEG piece (`stop_leg_text`), never either stop's STORY piece. It was once folded
    into stop 1's narration and played at arrival."""
    from src.tour.render_md import stop_leg_text

    script = _generate(_seq({1: (_vignette_beat(),)}))
    narration = stop_narration_text(script)
    assert _VIGNETTE_ONE_LINER not in narration.get(1, "")
    assert _VIGNETTE_ONE_LINER not in narration.get(0, "")
    assert _VIGNETTE_ONE_LINER in stop_leg_text(script)[1]


def test_empty_vignette_beats_is_todays_script():
    """Additive seam: the default ({}) and an explicit empty mapping produce a
    Script byte-identical to the stitcher output shape without vignettes."""
    plain = _generate(_seq())
    explicit_empty = _generate(_seq({}))
    assert plain == explicit_empty
    assert all(s.source_id != "vb-fountain" for s in plain.script)


def test_vignette_line_counts_only_the_sentence_it_actually_voices():
    """THE GUARANTEE (unchanged across the 2026-07-19 audio-clock rewrite): a
    walk-past vignette voices ONE sentence, so the tour's audio may grow only by
    that sentence — never by the vignette beat's whole duration (100 s here).
    Counting the whole beat would inflate every tour that passes a landmark.

    The arithmetic changed: audio is now the voiced words at SPOKEN_WPM rather
    than a flat 4 s per non-beat sentence, so the one-liner is credited its real
    length. The upper bound — well under the whole beat — is what matters and is
    asserted directly.
    """
    plain = _generate(_seq())
    with_v = _generate(_seq({1: (_vignette_beat(),)}))
    added = with_v.total_audio_seconds - plain.total_audio_seconds

    voiced = [s for s in with_v.script if s.source_id == "vb-fountain"]
    assert len(voiced) == 1, "a vignette must voice exactly one sentence"
    assert added == round(len(voiced[0].text.split()) / SPOKEN_WPM * 60)
    assert 0 < added < 100, (
        f"vignette added {added}s; must be its one line, never the whole 100s beat"
    )


# ---------------------------------------------------------------------------
# validate_script derives known ids from poi_beats + vignette_beats
# ---------------------------------------------------------------------------


def _mini_script(sentences: tuple[Sentence, ...]) -> Script:
    return Script(
        city_slug="paris", generated_at=_NOW.isoformat(), inputs=_INPUT,
        total_audio_seconds=0, total_walking_seconds=0, total_walk_distance_m=0,
        total_planned_seconds=0, selected_pois=(), lens_coverage={},
        script=sentences, validation=ValidationReport(),
    )


def test_validate_script_knows_vignette_beat_ids_internally():
    one_liner = Sentence(
        text=_VIGNETTE_ONE_LINER, source_id="vb-fountain", source_type="beat", stop_idx=1
    )
    script = _mini_script((one_liner,))

    with_field = validate_script(script, _seq({1: (_vignette_beat(),)}))
    assert with_field.untraceable_sentences == ()

    without_field = validate_script(script, _seq())
    assert one_liner in without_field.untraceable_sentences


def test_cited_vignette_body_joins_canonical_context_for_glue_scan():
    """A glue sentence naming a proper noun that only the cited vignette beat
    voices ("Médicis") passes the leakage scan — cited corpus is canonical."""
    one_liner = Sentence(
        text=_VIGNETTE_ONE_LINER, source_id="vb-fountain", source_type="beat", stop_idx=1
    )
    glue = Sentence(
        text="Walk past the Médicis fountain now.",
        source_id=GLUE_NAV, source_type="glue", stop_idx=1,
    )
    script = _mini_script((one_liner, glue))

    report = validate_script(script, _seq({1: (_vignette_beat(),)}))
    assert report.forbidden_phrase_hits == ()

    # Without the vignette beat on the sequence, the noun is runtime invention —
    # in STORY glue. A navigation line is the map
    # speaking and its nouns are places by nature, so GLUE_NAV is exempt from the
    # proper-noun half of the scan (measured: "Walk northwest along the Seine"
    # was refused as new_proper_noun:Seine and a real day 422'd three times);
    # the same sentence as a story line keeps the full scan.
    story = Sentence(
        text="Walk past the Médicis fountain now.",
        source_id=GLUE_REFLECTION, source_type="glue", stop_idx=1,
    )
    bare = validate_script(_mini_script((story,)), _seq())
    assert any("Médicis" in reason for _s, reason in bare.forbidden_phrase_hits)
    nav_only = validate_script(_mini_script((glue,)), _seq())
    assert not any("Médicis" in reason for _s, reason in nav_only.forbidden_phrase_hits)


# ---------------------------------------------------------------------------
# Reflection placement is untouched by vignette audio
# ---------------------------------------------------------------------------


def test_reflection_slots_unchanged_by_vignette_beats():
    """reflection_slots reads poi_beats + the leg's transit-beat audio only;
    vignette one-liners neither create nor destroy a slot."""
    route = _route(walk_seconds=300)  # deficit 300 - 4 = 296 >= 90 -> slot 1
    assert reflection_slots(route, _seq()) == (1,)
    assert reflection_slots(route, _seq({1: (_vignette_beat(),)})) == (1,)


# ---------------------------------------------------------------------------
# select_vignette_beats — the caller-side helper (one best beat per POI)
# ---------------------------------------------------------------------------


def _v_poi(pid: str) -> POI:
    return POI(id=pid, name=pid, tier=2, poi_role="stop", lat=48.85, lng=2.35)


def test_select_vignette_beats_first_active_with_body():
    bodyless = BeatRef(id="nb", poi_id="v1", active_status="active")
    retired = _beat("rb", "v1", "Old text.", ).model_copy(update={"active_status": "retired"})
    good = _beat("gb", "v1", "A fine line. And more.")
    later = _beat("lb", "v1", "A later line.")
    beats = {"v1": (bodyless, retired, good, later)}
    out = select_vignette_beats({1: (_v_poi("v1"),)}, beats)
    assert out == {1: (good,)}


def test_select_vignette_beats_prefers_requested_lens():
    plain = _beat("pb", "v1", "A plain line.")
    lensed = _beat("lb", "v1", "A lensed line.", lenses=("hidden_history",))
    beats = {"v1": (plain, lensed)}
    assert select_vignette_beats({1: (_v_poi("v1"),)}, beats) == {1: (plain,)}
    assert select_vignette_beats(
        {1: (_v_poi("v1"),)}, beats, lenses=frozenset({"hidden_history"})
    ) == {1: (lensed,)}


def test_select_vignette_beats_prefers_self_naming_beat():
    """Among a POI's voiceable beats, the one whose FIRST sentence names the POI
    wins — so the walk-past one-liner reads as unmistakably about that POI, never
    mis-attributed to the seated stop it lands under (the Nelson's-Column-under-
    the-National-Gallery bug a hostile tour-adversary panel caught).
    UNDO: drop the self-naming preference (fall back to ``voiceable[0]``) -> the
    non-naming 'The statue…' beat is chosen -> RED."""
    poi = _v_poi("Nelson's Column")  # name == id
    # Corpus order puts the NON-naming beat first, so a naive voiceable[0] picks the
    # unattributed one; the self-naming preference must reach past it.
    unnamed = _beat(
        "nc-statue", "Nelson's Column",
        "The statue at the top was carved from Craigleith sandstone.",
    )
    naming = _beat(
        "nc-def", "Nelson's Column",
        "Nelson's Column is a monument in Trafalgar Square.",
    )
    beats = {"Nelson's Column": (unnamed, naming)}
    assert select_vignette_beats({1: (poi,)}, beats) == {1: (naming,)}
    # A requested lens is still honoured WITHIN the self-naming pool.
    naming_lensed = _beat(
        "nc-def2", "Nelson's Column",
        "Nelson's Column also anchors the whole square.", lenses=("hidden_history",),
    )
    beats2 = {"Nelson's Column": (unnamed, naming, naming_lensed)}
    assert select_vignette_beats(
        {1: (poi,)}, beats2, lenses=frozenset({"hidden_history"})
    ) == {1: (naming_lensed,)}


def test_select_vignette_beats_prefers_shortest_self_naming_beat():
    """Among ≥2 SELF-NAMING beats, the one with the shortest first sentence wins —
    a tighter walk-past line — with a requested lens still primary. UNDO: drop the
    length sort of the naming pool -> the corpus-order (longer) beat is chosen -> RED."""
    poi = _v_poi("Nelson's Column")
    long_first = _beat(
        "nc-long", "Nelson's Column",
        "Nelson's Column is a granite monument raised in the square to honour the "
        "admiral who fell at Trafalgar.",
    )  # long first sentence, listed FIRST (corpus order)
    short_first = _beat(
        "nc-short", "Nelson's Column", "Nelson's Column dominates the square.",
    )  # short first sentence, listed SECOND
    beats = {"Nelson's Column": (long_first, short_first)}
    assert select_vignette_beats({1: (poi,)}, beats) == {1: (short_first,)}


def test_vignette_one_liners_apply_the_run_on_cap():
    """Generation-path parity: _vignette_one_liners voices the CAPPED one-liner (via
    the shared vignette_one_liner_text helper), so the audio/script line matches the
    workbench preview line. A 50-word Nelson beat is voiced as its short first clause."""
    from src.tour.generation import _vignette_one_liners

    beat = _beat("nc", "Nelson's Column", _NELSON_50W_VOICING)
    names = {"Nelson's Column": "Nelson's Column"}
    sents = _vignette_one_liners((beat,), stop_idx=1, poi_names=names)
    assert len(sents) == 1
    assert len(sents[0].text.split()) <= 24, sents[0].text
    assert "nelson's column" in sents[0].text.casefold()
    assert sents[0].source_id == "nc" and sents[0].source_type == "beat"


def test_generated_vignette_one_liner_is_capped_end_to_end():
    """Integration/name-map guard: the AUDIO/script path must voice the CAPPED line,
    not just the preview. The name map that lets the cap keep the POI's name must be
    built from route.VIGNETTES (walk-past POIs), NOT route.pois (seated) — a vignette
    beat's poi_id is never in route.pois. UNDO: build _build_transit's poi_names from
    route.pois only -> empty name -> guard falls back to the full 50-word run-on ->
    the voiced line is 50 words -> RED. (This is the exact bug the live $0 preview
    caught before any paid re-validation.)"""
    nelson_poi = _poi("v-nelson", "Nelson's Column")
    vbeat = _beat("vb-nelson", "v-nelson", _NELSON_50W_VOICING)
    seq = _seq(vignette_beats={1: (vbeat,)})
    route = _route().model_copy(update={"vignettes": {1: (nelson_poi,)}})
    script = generate(seq, route, _INPUT, glue_client=MockGlueClient(), now=_NOW)
    voiced = [s.text for s in script.script if s.source_id == "vb-nelson"]
    assert voiced, "vignette one-liner was not voiced"
    assert len(voiced[0].split()) <= 24, voiced[0]
    assert "nelson's column" in voiced[0].casefold()


_NELSON_50W_VOICING = (
    "Nelson's Column is a monument in Trafalgar Square in the City of Westminster, "
    "Central London, England, United Kingdom, built to commemorate British Royal Navy "
    "officer Horatio Nelson's decisive victory at the Battle of Trafalgar over the "
    "combined French and Spanish navies, during which he was killed by a French sniper."
)


def test_vignette_self_naming_detects_abbreviation_leading_name():
    """A POI whose definitional sentence STARTS with an abbreviation ("St. Paul's
    Cathedral is…") must still be detected as self-naming. The one-liner is voiced
    with ``generation.split_sentences`` (which re-glues "St."), so the naming check
    must use the SAME splitter — a naive ". " split truncates at "St." and misses
    the name. Guards the ~25 real St./numbered POIs across the live corpora the
    opus skeptic enumerated (St. Paul's Chapel, St. Patrick's Cathedral, …).
    UNDO: revert ``_vignette_beat_names_poi`` to a naive ``re.split(". ")[0]`` ->
    "St." is the first fragment, the name isn't found, the non-naming beat wins,
    so the wrong beat is selected -> RED."""
    poi = _v_poi("St. Paul's Cathedral")
    # Corpus order: NON-naming beat first (a naive voiceable[0] would take it).
    unnamed = _beat(
        "sp-dome", "St. Paul's Cathedral",
        "The dome is among the highest in the world.",
    )
    naming = _beat(
        "sp-def", "St. Paul's Cathedral",
        "St. Paul's Cathedral is an Anglican cathedral in London. It sits on Ludgate Hill.",
    )
    beats = {"St. Paul's Cathedral": (unnamed, naming)}
    assert select_vignette_beats({1: (poi,)}, beats) == {1: (naming,)}


def test_vignette_self_naming_outranks_lens_match():
    """Naming CLARITY outranks genre: a self-naming beat with NO requested lens
    beats a lensed beat that does NOT name the POI. A mis-attributed walk-past is a
    real defect; a lens miss on a brief one-liner is a soft preference.
    UNDO: make the lens pool win over the naming pool (select lensed across all
    voiceable before restricting to self-naming) -> the non-naming lensed beat is
    chosen -> RED."""
    poi = _v_poi("Nelson's Column")
    naming_plain = _beat(
        "nc-def", "Nelson's Column",
        "Nelson's Column is a monument in Trafalgar Square.",
    )
    unnamed_lensed = _beat(
        "nc-lens", "Nelson's Column",
        "The statue at the top honours a naval victory.", lenses=("hidden_history",),
    )
    beats = {"Nelson's Column": (naming_plain, unnamed_lensed)}
    assert select_vignette_beats(
        {1: (poi,)}, beats, lenses=frozenset({"hidden_history"})
    ) == {1: (naming_plain,)}


def test_select_vignette_beats_skips_unvoiceable_and_drops_empty_legs():
    voiced = _beat("gb", "v1", "A fine line.")
    beats = {"v1": (voiced,), "v2": (BeatRef(id="nb", poi_id="v2"),)}
    out = select_vignette_beats(
        {1: (_v_poi("v1"), _v_poi("v2")), 2: (_v_poi("v2"),)}, beats
    )
    assert out == {1: (voiced,)}  # v2 has no voiceable beat; leg 2 dropped


def test_select_vignette_beats_one_beat_per_poi_in_poi_order():
    b1 = _beat("b-v1", "v1", "Line one.")
    b2 = _beat("b-v2", "v2", "Line two.")
    beats = {"v1": (b1,), "v2": (b2,)}
    out = select_vignette_beats({3: (_v_poi("v2"), _v_poi("v1"))}, beats)
    assert out == {3: (b2, b1)}  # follows the vignette POI order
