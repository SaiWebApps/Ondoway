"""Phase 2 — beat_select.py: ordering + length-class fallback.

Phase 3.5 (2026-04-29) added B8-lite claim-dedup tests at the end.
"""

from __future__ import annotations

from src.tour.beat_select import (
    B8_JACCARD_THRESHOLD,
    DEFAULT_FLAT_MAX,
    HOIST_PROXIMITY_M,
    NARRATIVE_FUNCTION_ORDER,
    _enforce_tone_variety,
    _min_year,
    _order_body_chronologically,
    choose_ordering_strategy,
    extra_beat_ids,
    find_area_orientation_beat,
    govern_poi_beats,
    reorder_final_stop_for_closing,
    select_poi_beats,
    select_poi_beats_full,
)
from src.tour.contract import POI, BeatRef, BeatSequence, PhysicalCue, POIBeats, Route, ScriptPOI
from src.tour.fixtures import NOTRE_DAME_SUB_LOCATION_ORDER
from src.tour.generation import _find_orientation_beat
from src.tour.routing import beat_spoken_seconds


def _poi(name: str, tier: int = 5) -> POI:
    return POI(id=f"poi-{name}", name=name, tier=tier, poi_role="stop", lat=48.85, lng=2.35)


def _beat(
    bid: str,
    *,
    sub_location: str | None = None,
    trigger_address: str | None = None,
    narrative_function: str | None = None,
    emotional_register: str | None = "neutral",
    beat_length_class: str | None = None,
    word_count: int = 100,
    lenses: tuple[str, ...] = (),
    entities: tuple[str, ...] = (),
    subject_tag: str | None = None,
    script_body: str | None = None,
    poi_id: str = "poi",
) -> BeatRef:
    return BeatRef(
        id=bid,
        poi_id=poi_id,
        sub_location=sub_location,
        trigger_address=trigger_address,
        narrative_function=narrative_function,
        emotional_register=emotional_register,
        beat_length_class=beat_length_class,
        word_count=word_count,
        lenses=lenses,
        entities=entities,
        subject_tag=subject_tag,
        script_body=script_body,
    )


# ---------------------------------------------------------------------------
# Strategy choice
# ---------------------------------------------------------------------------


def test_strategy_sub_location_when_three_distinct():
    beats = [
        _beat("1", sub_location="parvis"),
        _beat("2", sub_location="nave"),
        _beat("3", sub_location="choir"),
    ]
    assert choose_ordering_strategy(beats) == "sub_location"


def test_strategy_trigger_address_when_five_distinct():
    beats = [_beat(str(i), trigger_address=f"no. {i}") for i in range(1, 6)]
    assert choose_ordering_strategy(beats) == "trigger_address"


def test_strategy_narrative_when_neither_dense():
    beats = [
        _beat("1", narrative_function="hook"),
        _beat("2", narrative_function="establishing"),
    ]
    assert choose_ordering_strategy(beats) == "narrative_function"


def test_strategy_dominant_primitive_wins():
    """Phase 4 calibration: when both primitives meet threshold, the one
    with more distinct values wins. With 3 sub_locs vs 5 trigger_addrs,
    triggers dominate so trigger_address strategy fires (matches the
    empirical PdV walk where 23 trigger_addresses outweigh 6 sub_locs).
    """
    beats = [
        _beat("a", sub_location="parvis", trigger_address="x1"),
        _beat("b", sub_location="nave", trigger_address="x2"),
        _beat("c", sub_location="choir", trigger_address="x3"),
        _beat("d", trigger_address="x4"),
        _beat("e", trigger_address="x5"),
    ]
    assert choose_ordering_strategy(beats) == "trigger_address"


def test_strategy_sub_location_wins_when_dominant():
    """Sub_location wins when it has at least as many distinct values
    as trigger_address (Notre-Dame / Conciergerie shape).
    """
    beats = [
        _beat("a", sub_location="parvis"),
        _beat("b", sub_location="nave"),
        _beat("c", sub_location="choir"),
        _beat("d", sub_location="towers"),
        _beat("e", sub_location="treasury"),
    ]
    assert choose_ordering_strategy(beats) == "sub_location"


def test_strategy_sub_location_when_triggers_below_threshold():
    """Sub_location still wins if trigger_address fails its threshold,
    even if sub_locs < trig_n by a small amount (4 sub_locs, 4 triggers
    where trigger threshold is 5).
    """
    beats = [
        _beat("a", sub_location="parvis", trigger_address="x1"),
        _beat("b", sub_location="nave", trigger_address="x2"),
        _beat("c", sub_location="choir", trigger_address="x3"),
        _beat("d", sub_location="towers", trigger_address="x4"),
    ]
    # 4 sub_locs ≥ 3, 4 triggers < 5 → sub_location.
    assert choose_ordering_strategy(beats) == "sub_location"


# ---------------------------------------------------------------------------
# Notre-Dame fixture ordering
# ---------------------------------------------------------------------------


def test_notre_dame_sub_location_ordering_matches_fixture():
    # One beat per sub_location, intentionally provided in shuffled order.
    seen = ["interior-nave", "facade", "kilometre-zero", "towers", "parvis", "choir"]
    beats = [_beat(f"b-{s}", sub_location=s) for s in seen]
    poi = _poi("Notre-Dame Cathedral")

    plan = select_poi_beats(poi, beats)
    out_order = [b.sub_location for b in plan.beats]

    expected = [s for s in NOTRE_DAME_SUB_LOCATION_ORDER if s in seen]
    assert out_order == expected
    assert plan.ordering_strategy == "sub_location"


def test_notre_dame_picks_highest_scoring_beat_per_sub_location():
    # Two beats for parvis; one carries the matching lens, one doesn't.
    poi = _poi("Notre-Dame Cathedral")
    beats = [
        _beat(
            "low",
            sub_location="parvis",
            lenses=("tourism_culture",),
            word_count=100,
        ),
        _beat(
            "high",
            sub_location="parvis",
            lenses=("hidden_history",),
            word_count=100,
        ),
        _beat("nave", sub_location="nave"),
        _beat("choir", sub_location="choir"),
    ]
    plan = select_poi_beats(poi, beats, interest_lenses=["hidden_history"])
    parvis_beat = next(b for b in plan.beats if b.sub_location == "parvis")
    assert parvis_beat.id == "high"


def test_uncurated_poi_falls_back_to_alphabetical_sub_location_order():
    poi = _poi("Some New Anchor")
    beats = [
        _beat("zz", sub_location="zeta"),
        _beat("aa", sub_location="alpha"),
        _beat("mm", sub_location="mu"),
    ]
    plan = select_poi_beats(poi, beats)
    assert [b.sub_location for b in plan.beats] == ["alpha", "mu", "zeta"]


def test_no_sub_location_beat_appended_last():
    poi = _poi("Notre-Dame Cathedral")
    beats = [
        _beat("nave", sub_location="nave"),
        _beat("choir", sub_location="choir"),
        _beat("parvis", sub_location="parvis"),
        _beat("misc", sub_location=None),
    ]
    plan = select_poi_beats(poi, beats)
    assert plan.beats[-1].id == "misc"
    assert plan.beats[-1].sub_location is None


# ---------------------------------------------------------------------------
# Trigger address ordering
# ---------------------------------------------------------------------------


def test_trigger_address_orders_numerically():
    poi = _poi("Place des Vosges")
    beats = [
        _beat("b13", trigger_address="no. 13 place des Vosges"),
        _beat("b1", trigger_address="no. 1 place des Vosges"),
        _beat("b21", trigger_address="no. 21 place des Vosges"),
        _beat("b2", trigger_address="no. 2 place des Vosges"),
        _beat("b1bis", trigger_address="no. 1 bis place des Vosges"),
    ]
    plan = select_poi_beats(poi, beats)
    addrs = [b.trigger_address for b in plan.beats]
    # Numeric prefix sort: 1 (and 1 bis), 2, 13, 21.
    assert addrs[0].startswith("no. 1 ")
    assert addrs[2] == "no. 2 place des Vosges"
    assert addrs[3] == "no. 13 place des Vosges"
    assert addrs[4] == "no. 21 place des Vosges"
    assert plan.ordering_strategy == "trigger_address"


def test_trigger_address_deterministic_under_shuffle():
    poi = _poi("Place des Vosges")
    template = [_beat(f"b{i}", trigger_address=f"no. {i} place des Vosges") for i in range(1, 11)]

    import random

    rng = random.Random(0)
    seq_a = list(template)
    seq_b = list(template)
    rng.shuffle(seq_a)
    rng.shuffle(seq_b)

    plan_a = select_poi_beats(poi, seq_a)
    plan_b = select_poi_beats(poi, seq_b)
    assert [b.id for b in plan_a.beats] == [b.id for b in plan_b.beats]


# ---------------------------------------------------------------------------
# Narrative-function fallback (length-class)
# ---------------------------------------------------------------------------


def test_narrative_function_ordering_when_no_spatial_primitive():
    poi = _poi("Sainte-Chapelle")
    beats = [
        _beat("b-deepen", narrative_function="deepen"),
        _beat("b-hook", narrative_function="hook"),
        _beat("b-climax", narrative_function="climax"),
        _beat("b-est", narrative_function="establishing"),
    ]
    plan = select_poi_beats(poi, beats)
    fns = [b.narrative_function for b in plan.beats]
    expected = [fn for fn in NARRATIVE_FUNCTION_ORDER if fn in fns]
    assert fns[: len(expected)] == expected


def test_narrative_function_caps_at_default_flat_max_for_anchor():
    """Calibration: DEFAULT_FLAT_MAX bumped 8→12 (2026-07-09, golden-gap R4) so a
    dense marquee voices more of its deepens (Île 42.6%→55.3%, PdV 55.6%→66.7%);
    the C9 governor still bounds per-stop audio downstream.
    """
    poi = _poi("Sainte-Chapelle")  # tier 5
    beats = [_beat(f"b{i}", narrative_function="deepen") for i in range(DEFAULT_FLAT_MAX + 4)]
    plan = select_poi_beats(poi, beats)
    assert len(plan.beats) == DEFAULT_FLAT_MAX


def test_narrative_function_pause_tier3_caps_at_pause_max():
    """Calibration: PAUSE_BEATS_MAX bumped 3→4 (2026-07-09, golden-gap R5) so a
    tier-3 pause stop can carry its 4th deepen (the empirical Vert-Galant miss
    ranked exactly 4th).
    """
    poi = _poi("Some Pause", tier=3)
    beats = [_beat(f"b{i}", narrative_function="deepen") for i in range(6)]
    plan = select_poi_beats(poi, beats)
    assert len(plan.beats) == 4


def test_narrative_function_walkby_tier1_caps_at_one():
    poi = _poi("Walk-by", tier=1)
    beats = [_beat(f"b{i}", narrative_function="hook") for i in range(3)]
    plan = select_poi_beats(poi, beats)
    assert len(plan.beats) == 1


def test_inactive_beats_filtered():
    poi = _poi("Notre-Dame Cathedral")
    beats = [
        _beat("alive", sub_location="parvis"),
        _beat("dead", sub_location="choir").model_copy(update={"active_status": "deprecated"}),
        _beat("alive2", sub_location="nave"),
    ]
    plan = select_poi_beats(poi, beats)
    assert all(b.active_status == "active" for b in plan.beats)
    assert {b.id for b in plan.beats} == {"alive", "alive2"}


def test_length_class_breaks_ties_when_present():
    # Two beats tied on lens-match and word_count → length_class wins.
    poi = _poi("Notre-Dame Cathedral")
    beats = [
        _beat("seasoning", sub_location="parvis", beat_length_class="seasoning", word_count=150),
        _beat("anchor", sub_location="parvis", beat_length_class="anchor", word_count=150),
        _beat("nave", sub_location="nave"),
        _beat("choir", sub_location="choir"),
    ]
    plan = select_poi_beats(poi, beats)
    parvis = next(b for b in plan.beats if b.sub_location == "parvis")
    assert parvis.id == "anchor"


def test_length_class_falls_back_to_word_count_when_missing():
    # No length_class on either beat — wider one wins.
    poi = _poi("Notre-Dame Cathedral")
    beats = [
        _beat("short", sub_location="parvis", beat_length_class=None, word_count=50),
        _beat("long", sub_location="parvis", beat_length_class=None, word_count=300),
        _beat("nave", sub_location="nave"),
        _beat("choir", sub_location="choir"),
    ]
    plan = select_poi_beats(poi, beats)
    parvis = next(b for b in plan.beats if b.sub_location == "parvis")
    assert parvis.id == "long"


def test_tone_variety_breaks_three_somber_run():
    poi = _poi("Sainte-Chapelle")
    # All five share narrative_function so default ordering is by score
    # (all equal). Rely on tone-variety to swap a non-somber in.
    beats = [
        _beat("a", narrative_function="hook", emotional_register="somber"),
        _beat("b", narrative_function="hook", emotional_register="reverent"),
        _beat("c", narrative_function="hook", emotional_register="somber"),
        _beat("d", narrative_function="hook", emotional_register="playful"),
    ]
    plan = select_poi_beats(poi, beats)
    registers = [b.emotional_register for b in plan.beats]
    # No three SOMBER in a row.
    for i in range(2, len(registers)):
        triplet = {registers[i - 2], registers[i - 1], registers[i]}
        assert triplet != {"somber"} | {"reverent"} - {"reverent"}
        assert not (
            registers[i - 2] in {"somber", "reverent"}
            and registers[i - 1] in {"somber", "reverent"}
            and registers[i] in {"somber", "reverent"}
        )


def _has_three_somber_run(registers: list[str | None]) -> bool:
    somber = {"somber", "reverent"}
    return any(
        registers[i - 2] in somber and registers[i - 1] in somber and registers[i] in somber
        for i in range(2, len(registers))
    )


def test_tone_variety_breaks_long_run_with_single_relief():
    """A run of length >= 4 with a single relief beat must NOT leave a
    3-consecutive-somber run: [s,s,s,s,relief] admits [s,s,relief,s,s].

    The old always-swap-into-violation-1 heuristic pushed the relief too far
    left and left the leading `s,s,s` intact — this is the primary defect."""
    beats = [
        _beat("s1", emotional_register="somber"),
        _beat("s2", emotional_register="somber"),
        _beat("s3", emotional_register="somber"),
        _beat("s4", emotional_register="somber"),
        _beat("relief", emotional_register="playful"),
    ]
    out = _enforce_tone_variety(beats)
    registers = [b.emotional_register for b in out]
    assert not _has_three_somber_run(registers), registers
    # relief must actually be present and beats conserved
    assert {b.id for b in out} == {"s1", "s2", "s3", "s4", "relief"}


def test_tone_variety_breaks_run_with_two_reliefs_no_pingpong():
    """A run of four somber beats followed by two relief beats.

    A valid tone-broken arrangement exists ([s,s,r,s,s,r]), but the old
    single-swap heuristic swapped one relief into the middle of the first
    detected window and produced [s,r,s,s,s,r] — the relief landed too far
    left, re-creating a three-somber run it never revisited. The constructive
    interleave forces a relief after every two somber beats while relief
    remains, so no run survives."""
    beats = [
        _beat("s1", emotional_register="somber"),
        _beat("s2", emotional_register="somber"),
        _beat("s3", emotional_register="somber"),
        _beat("s4", emotional_register="somber"),
        _beat("r1", emotional_register="playful"),
        _beat("r2", emotional_register="upbeat"),
    ]
    out = _enforce_tone_variety(beats)
    registers = [b.emotional_register for b in out]
    assert not _has_three_somber_run(registers), registers
    assert {b.id for b in out} == {"s1", "s2", "s3", "s4", "r1", "r2"}


# ---------------------------------------------------------------------------
# B8-lite claim dedup (Phase 3.5)
# ---------------------------------------------------------------------------


def test_b8_lite_drops_overlapping_entities():
    # Two beats sharing 4-of-5 entities (Jaccard 0.8) at the same lens →
    # the longer body wins, the shorter is dropped.
    poi = _poi("Hotel de Sully")
    long_body = "Voltaire was beaten by the Prince de Rohan's lackeys here. " * 6
    short_body = "Voltaire was beaten by Rohan's lackeys."
    long_beat = _beat(
        "long-voltaire",
        sub_location="courtyard",
        narrative_function="deepen",
        lenses=("literary_heritage",),
        entities=("Bastille", "Letters", "Prince de Rohan", "Upon", "Voltaire"),
        script_body=long_body,
        word_count=80,
    )
    short_beat = _beat(
        "short-voltaire",
        sub_location="courtyard",
        narrative_function="deepen",
        lenses=("literary_heritage",),
        entities=("Bastille", "Prince de Rohan", "Upon", "Voltaire"),
        script_body=short_body,
        word_count=8,
    )
    other = _beat(
        "other",
        sub_location="garden",
        narrative_function="establishing",
        lenses=("famous_residents",),
        entities=("Sully",),
        script_body="Independent fact.",
        word_count=3,
    )
    plan = select_poi_beats(poi, [long_beat, short_beat, other])
    ids = [b.id for b in plan.beats]
    assert "long-voltaire" in ids
    assert "short-voltaire" not in ids
    assert "other" in ids


def test_b8_lite_keeps_complementary_when_overlap_below_threshold():
    # Two beats sharing only 1-of-3 entities (Jaccard 0.33) at the same
    # lens are NOT collapsed — complementary detail is preserved.
    poi = _poi("Notre-Dame Cathedral")
    a = _beat(
        "a",
        sub_location="parvis",
        narrative_function="establishing",
        lenses=("hidden_history",),
        entities=("Notre-Dame", "Hugo", "Quasimodo"),
        script_body="Hugo wrote about Quasimodo at Notre-Dame.",
        word_count=10,
    )
    b = _beat(
        "b",
        sub_location="nave",
        narrative_function="establishing",
        lenses=("hidden_history",),
        entities=("Notre-Dame", "Pilier des Nautes", "Pillars"),
        script_body="The Pilier des Nautes was found beneath Notre-Dame.",
        word_count=10,
    )
    plan = select_poi_beats(poi, [a, b])
    ids = {beat.id for beat in plan.beats}
    assert ids == {"a", "b"}


def test_b8_lite_skips_pairs_with_no_lens_overlap():
    # Same entities, but disjoint lenses → no collision.
    poi = _poi("Place des Vosges")
    a = _beat(
        "a",
        trigger_address="no. 6 place des Vosges",
        narrative_function="establishing",
        lenses=("famous_residents",),
        entities=("Hugo", "Place des Vosges"),
        script_body="Hugo lived at no. 6.",
        word_count=5,
    )
    b = _beat(
        "b",
        trigger_address="no. 8 place des Vosges",
        narrative_function="establishing",
        lenses=("literary_heritage",),
        entities=("Hugo", "Place des Vosges"),
        script_body="Hugo's writings name the square repeatedly.",
        word_count=6,
    )
    # Add 3 more addresses so we hit the trigger_address strategy.
    fillers = [
        _beat(
            f"f{i}",
            trigger_address=f"no. {i} place des Vosges",
            narrative_function="establishing",
            lenses=("famous_residents",),
            entities=("Filler",),
            script_body=f"Filler {i}.",
            word_count=2,
        )
        for i in (1, 2, 3)
    ]
    plan = select_poi_beats(poi, [a, b, *fillers])
    ids = {beat.id for beat in plan.beats}
    assert {"a", "b"}.issubset(ids)


def test_b8_lite_subject_tag_overlap_triggers_dedup():
    # Empty entities, but identical subject_tag → collide.
    poi = _poi("Conciergerie")
    a = _beat(
        "long",
        sub_location="salle-des-gens-darmes",
        narrative_function="deepen",
        lenses=("dark_history",),
        entities=(),
        subject_tag="marie antoinette cell mockup",
        script_body="A long account of the cell mockup, " * 10,
        word_count=80,
    )
    b = _beat(
        "short",
        sub_location="salle-des-gens-darmes",
        narrative_function="deepen",
        lenses=("dark_history",),
        entities=(),
        subject_tag="marie antoinette cell mockup",
        script_body="Short version.",
        word_count=2,
    )
    other = _beat(
        "other",
        sub_location="tour-bonbec",
        narrative_function="establishing",
        lenses=("dark_history",),
        entities=("Tour Bonbec",),
        subject_tag="tower torture screams",
        script_body="The tower's name comes from screams.",
        word_count=7,
    )
    one_more = _beat(
        "more",
        sub_location="rue-de-paris-cells",
        narrative_function="establishing",
        lenses=("dark_history",),
        entities=("Sansons",),
        subject_tag="executioner dynasty",
        script_body="The Sanson family held the post for generations.",
        word_count=8,
    )
    plan = select_poi_beats(poi, [a, b, other, one_more])
    ids = {beat.id for beat in plan.beats}
    assert "long" in ids
    assert "short" not in ids


def test_b8_lite_threshold_constant_matches_design_doc():
    # Phase-1-design.md §3.3 specifies 0.8 — guard against silent drift.
    assert B8_JACCARD_THRESHOLD == 0.8


# ---------------------------------------------------------------------------
# Phase 7 — B8-lite recalibration: catch the 3 known phase-6-rerun dups
# ---------------------------------------------------------------------------


# Real script bodies pulled from the live Paris corpus 2026-04-29 — these
# are the exact pairs the phase-6-rerun outputs failed to dedup.

_MESME_DE = (
    "The Hotel de Sully was built in 1624 for Mesme Gallet, a notorious "
    "gambler who squandered his fortune on a single night's losses and had "
    "to sell up. The aged Duc de Sully bought it next, but his young wife's "
    "infidelities were legendary. The pragmatic duke installed a separate "
    "staircase so her lovers could come and go without crossing his path."
)
_MESME_BF = (
    "Built in 1624 for gambler Mesme Gallet who lost his fortune in one "
    "night. The aged Duc de Sully installed a separate staircase so his "
    "wife's lovers could come and go."
)
_HUGO_B41 = (
    "Adele carried on an affair with Sainte-Beuve. Hugo began a liaison "
    "with Juliette Drouet. A secret back staircase allowed discreet "
    "comings and goings for fifty years."
)
_HUGO_A18 = (
    "Hugo's domestic life at Place des Vosges was turbulent. His wife "
    "Adele carried on an affair with their close friend, the critic "
    "Sainte-Beuve. Hugo himself began a passionate liaison with the "
    "actress Juliette Drouet, who lived nearby. A secret back staircase "
    "allowed discreet comings and goings — the arrangement lasted for "
    "fifty years until Drouet's death."
)
_PANTHEON_EE = (
    "In 1744, Louis XV fell gravely ill and vowed to build a new church "
    "for Saint Genevieve if he recovered, recalling how in the 5th century "
    "she had healed her countrymen. The clergy, seeing divine punishment "
    "for his debauchery, urged him to also end his affair with the "
    "Duchesse de Chateauroux, the last of four sisters he had seduced. "
    "The king recovered, promptly resumed his liaison, and would have "
    "forgotten the church entirely, but the monks of the Abbey of "
    "Sainte-Genevieve held him to his word. Architect Soufflot was "
    "commissioned to build the colossal edifice — 110 metres long, 84 "
    "metres wide, and 83 metres tall — crowning the levelled hilltop."
)
_PANTHEON_142 = (
    "In 1744, an ailing King Louis XV was miraculously healed by St. "
    "Geneviève, the city's patron saint, and he thanked her by replacing "
    "her ruined church with a more fitting tribute. By the time the "
    "church was completed in 1791, however, the secular-minded Revolution "
    "was in full swing, and the church was converted into a nonreligious "
    "mausoleum honoring the 'Champions of French liberty': Voltaire, "
    "Rousseau, and others."
)


def test_phase7_b8_lite_catches_mesme_gallet_paraphrase():
    """phase-6-rerun Tour 1 Stop 1 emitted both beats; Phase 7 must collapse."""
    poi = _poi("Hotel de Sully")
    de = _beat(
        "de1377b4",
        sub_location="hotel-de-sully",
        narrative_function="establishing",
        lenses=("hidden_history",),
        entities=("Mesme Gallet",),
        script_body=_MESME_DE,
        word_count=60,
    )
    bf = _beat(
        "bf668074",
        sub_location="hotel-de-sully",
        narrative_function="establishing",
        lenses=("hidden_history",),
        entities=("Built", "Mesme Gallet"),
        script_body=_MESME_BF,
        word_count=33,
    )
    plan = select_poi_beats(poi, [de, bf])
    ids = {b.id for b in plan.beats}
    assert len(ids) == 1, f"Expected dedup; got both {ids}"


def test_phase7_b8_lite_catches_hugo_affair_paraphrase():
    """phase-6-rerun Tour 1 Stop 4 emitted both beats; Phase 7 must collapse."""
    poi = _poi("Musee Victor Hugo", tier=4)
    b41 = _beat(
        "b41d4c77",
        narrative_function="deepen",
        lenses=("famous_residents",),
        entities=("Adele", "Juliette Drouet"),
        script_body=_HUGO_B41,
        word_count=29,
    )
    a18 = _beat(
        "a1833622",
        narrative_function="climax",
        lenses=("famous_residents",),
        entities=("Adele", "Juliette Drouet", "Place des Vosges"),
        script_body=_HUGO_A18,
        word_count=60,
    )
    plan = select_poi_beats(poi, [b41, a18])
    ids = {b.id for b in plan.beats}
    assert len(ids) == 1, f"Expected dedup; got both {ids}"


def test_phase7_b8_lite_catches_pantheon_geneviève_pair():
    """phase-6-rerun Tour 5 Stop 3 emitted both Pantheon-vow beats.

    Entities-Jaccard = 0.125 because supporting cast diverges
    (Soufflot/Chateauroux vs Voltaire/Rousseau) and "Saint Genevieve" /
    "St. Geneviève" don't string-match. Phase 7 catches via
    canonical-entity-overlap ≥ 2 + shared 1744 year token.
    """
    poi = _poi("Pantheon")
    ee = _beat(
        "ee115ca8",
        narrative_function="establishing",
        lenses=("hidden_history",),
        entities=(
            "Abbey",
            "Architect Soufflot",
            "Duchesse de Chateauroux",
            "Louis XV",
            "Saint Genevieve",
        ),
        script_body=_PANTHEON_EE,
        word_count=130,
    )
    one_four = _beat(
        "142060a7",
        narrative_function="hook",
        lenses=("hidden_history",),
        entities=("Louis XV", "St. Geneviève", "Voltaire", "Rousseau"),
        subject_tag="vow conversion mausoleum",
        script_body=_PANTHEON_142,
        word_count=70,
    )
    plan = select_poi_beats(poi, [ee, one_four])
    ids = {b.id for b in plan.beats}
    assert len(ids) == 1, f"Expected dedup; got both {ids}"


# ---------------------------------------------------------------------------
# Phase 7 — Area-level orientation hoist (Fix 2)
# ---------------------------------------------------------------------------


def _route_for(pois: list[POI]) -> Route:
    """Synthetic Route shell — distance/budget fields are irrelevant for hoist tests."""
    return Route(
        pois=tuple(pois),
        transits=(),
        total_walk_distance_m=0.0,
        total_walk_seconds=0,
        spine_area=None,
        target_dwell_seconds=0,
        err_short_total_seconds=0,
    )


def test_area_level_orientation_hoist_picks_up_sibling_orientation():
    """Start POI has no stop_orientation beat; sibling in same Area does → hoist.

    Mirrors the Tour 1 PdV scenario: Hotel de Sully (start) has none,
    Place des Vosges (5th stop, same Le Marais) carries the canonical
    Pariswalks "find a bench" opener.
    """
    start_poi = POI(
        id="hotel-de-sully",
        name="Hotel de Sully",
        tier=4,
        poi_role="stop",
        lat=48.8538,
        lng=2.3651,
        areas=("Le Marais",),
    )
    sibling_poi = POI(
        id="place-des-vosges",
        name="Place des Vosges",
        tier=5,
        poi_role="stop",
        lat=48.8553,
        lng=2.3656,
        areas=("Le Marais",),
    )
    other_poi = POI(
        id="restaurant-bofinger",
        name="Restaurant Bofinger",
        tier=3,
        poi_role="stop",
        lat=48.8530,
        lng=2.3686,
        areas=("Le Marais",),
    )
    orient = BeatRef(
        id="ebeab682",
        poi_id=sibling_poi.id,
        beat_type="stop_orientation",
        narrative_function="establishing",
        word_count=40,
        active_status="active",
    )
    start_beats = POIBeats(
        poi_id=start_poi.id,
        poi_name=start_poi.name,
        ordering_strategy="narrative_function",
        beats=(BeatRef(id="hds-est", poi_id=start_poi.id, narrative_function="establishing"),),
    )
    sibling_beats = POIBeats(
        poi_id=sibling_poi.id,
        poi_name=sibling_poi.name,
        ordering_strategy="trigger_address",
        beats=(orient, BeatRef(id="pdv-no1", poi_id=sibling_poi.id)),
    )
    other_beats = POIBeats(
        poi_id=other_poi.id,
        poi_name=other_poi.name,
        ordering_strategy="narrative_function",
        beats=(BeatRef(id="bof-1", poi_id=other_poi.id),),
    )
    seq = BeatSequence(poi_beats=(start_beats, other_beats, sibling_beats))
    route = _route_for([start_poi, other_poi, sibling_poi])

    found = find_area_orientation_beat(seq, route, start_idx=0)
    assert found is not None
    assert found.id == "ebeab682"


def test_area_level_orientation_returns_none_when_no_shared_area():
    """Sibling POI in a DIFFERENT Area → no hoist."""
    start_poi = POI(
        id="hotel-de-sully",
        name="Hotel de Sully",
        tier=4,
        poi_role="stop",
        lat=48.8538,
        lng=2.3651,
        areas=("Le Marais",),
    )
    other_area_poi = POI(
        id="pantheon",
        name="Pantheon",
        tier=5,
        poi_role="stop",
        lat=48.8462,
        lng=2.3464,
        areas=("Latin Quarter",),
    )
    orient = BeatRef(
        id="some-orient",
        poi_id=other_area_poi.id,
        beat_type="stop_orientation",
        word_count=40,
    )
    seq = BeatSequence(
        poi_beats=(
            POIBeats(
                poi_id=start_poi.id,
                poi_name=start_poi.name,
                ordering_strategy="narrative_function",
                beats=(BeatRef(id="x", poi_id=start_poi.id),),
            ),
            POIBeats(
                poi_id=other_area_poi.id,
                poi_name=other_area_poi.name,
                ordering_strategy="narrative_function",
                beats=(orient,),
            ),
        )
    )
    route = _route_for([start_poi, other_area_poi])
    assert find_area_orientation_beat(seq, route, start_idx=0) is None


def test_area_level_orientation_returns_none_when_no_orientation_beat_exists():
    """Sibling shares Area but has no orientation beat → no hoist."""
    start = POI(
        id="a", name="A", tier=4, poi_role="stop", lat=48.85, lng=2.36, areas=("Le Marais",)
    )
    sibling = POI(
        id="b", name="B", tier=4, poi_role="stop", lat=48.852, lng=2.366, areas=("Le Marais",)
    )
    seq = BeatSequence(
        poi_beats=(
            POIBeats(
                poi_id=start.id,
                poi_name=start.name,
                ordering_strategy="narrative_function",
                beats=(BeatRef(id="x", poi_id=start.id),),
            ),
            POIBeats(
                poi_id=sibling.id,
                poi_name=sibling.name,
                ordering_strategy="narrative_function",
                beats=(BeatRef(id="y", poi_id=sibling.id, narrative_function="establishing"),),
            ),
        )
    )
    route = _route_for([start, sibling])
    assert find_area_orientation_beat(seq, route, start_idx=0) is None


# ---------------------------------------------------------------------------
# Phase 7 — closing-friendly beat reservation (Fix 5)
# ---------------------------------------------------------------------------


def _seq_with_final_beats(beats: tuple[BeatRef, ...]) -> BeatSequence:
    return BeatSequence(
        poi_beats=(
            POIBeats(
                poi_id="final",
                poi_name="Final",
                ordering_strategy="narrative_function",
                beats=beats,
            ),
        )
    )


def test_phase7_closing_reorder_promotes_callback_to_last():
    """Callback beat at any position → moves to last; spec preference order top tier."""
    beats = (
        _beat("c", narrative_function="callback", script_body="A wrap-up sentence."),
        _beat("a", narrative_function="establishing", script_body="A factoid."),
        _beat("b", narrative_function="deepen", script_body="Another factoid."),
    )
    seq = _seq_with_final_beats(beats)
    out = reorder_final_stop_for_closing(seq)
    assert tuple(b.id for b in out.poi_beats[-1].beats) == ("a", "b", "c")


def test_phase7_closing_reorder_falls_back_to_climax_when_no_callback():
    beats = (
        _beat("a", narrative_function="establishing", script_body="X."),
        _beat("c", narrative_function="climax", script_body="Climactic sentence."),
        _beat("b", narrative_function="deepen", script_body="Y."),
    )
    seq = _seq_with_final_beats(beats)
    out = reorder_final_stop_for_closing(seq)
    assert out.poi_beats[-1].beats[-1].id == "c"


def test_phase7_closing_reorder_falls_back_to_longest_when_no_callback_or_climax():
    """No callback, no climax → longest body wins. Avoids closing on factoids."""
    beats = (
        _beat("short", narrative_function="establishing", script_body="A."),
        _beat(
            "long",
            narrative_function="deepen",
            script_body=(
                "A much longer wrap-up paragraph that gives "
                "the close some narrative weight "
            )
            * 3,
        ),
        _beat("med", narrative_function="hook", script_body="A medium one."),
    )
    seq = _seq_with_final_beats(beats)
    out = reorder_final_stop_for_closing(seq)
    assert out.poi_beats[-1].beats[-1].id == "long"


def test_phase7_closing_reorder_no_op_when_already_closing_friendly():
    beats = (
        _beat("a", narrative_function="establishing", script_body="X."),
        _beat("b", narrative_function="deepen", script_body="Y."),
        _beat("c", narrative_function="callback", script_body="Z."),
    )
    seq = _seq_with_final_beats(beats)
    out = reorder_final_stop_for_closing(seq)
    assert tuple(b.id for b in out.poi_beats[-1].beats) == ("a", "b", "c")


def test_phase7_closing_reorder_no_op_for_short_stops():
    """Stops with <2 beats are untouched."""
    beats = (_beat("only", narrative_function="establishing", script_body="X."),)
    seq = _seq_with_final_beats(beats)
    out = reorder_final_stop_for_closing(seq)
    assert tuple(b.id for b in out.poi_beats[-1].beats) == ("only",)


def test_phase7_closing_reorder_skips_beatless_ab_destination_sentinel():
    """A→B routes append a beatless synthesized-destination sentinel as the LAST
    plan. Reordering must target the last stop WITH beats (the real terminal),
    and must preserve the sentinel — otherwise the tour ends mid-fact, the exact
    Phase-7 defect this function exists to prevent."""
    real_stop = POIBeats(
        poi_id="final",
        poi_name="Final",
        ordering_strategy="narrative_function",
        beats=(
            _beat("c", narrative_function="callback", script_body="A wrap-up sentence."),
            _beat("a", narrative_function="establishing", script_body="A factoid."),
            _beat("b", narrative_function="deepen", script_body="Another factoid."),
        ),
    )
    sentinel = POIBeats(
        poi_id="__end_b__48.860000_2.340000",
        poi_name="Destination",
        ordering_strategy="narrative_function",
        beats=(),
    )
    seq = BeatSequence(poi_beats=(real_stop, sentinel))
    out = reorder_final_stop_for_closing(seq)
    assert tuple(b.id for b in out.poi_beats[0].beats) == ("a", "b", "c")
    # The sentinel survives, still last and still beatless.
    assert len(out.poi_beats) == 2
    assert out.poi_beats[-1].poi_id == "__end_b__48.860000_2.340000"
    assert out.poi_beats[-1].beats == ()


def test_phase7_b8_lite_does_not_overcollapse_complementary_pdv_addresses():
    """Two PdV addresses with the same lens but distinct content must NOT merge.

    Pinning behaviour for the golden corpus: the empirical PdV walk has
    22+ distinct address vignettes, many sharing 'famous_residents' or
    'hidden_history' lens tags. Char-5-gram Jaccard between distinct
    vignettes runs well below 0.30 in the corpus; this test guards
    against accidental over-collapse from threshold drift.
    """
    poi = _poi("Place des Vosges")
    a = _beat(
        "no6-hugo",
        trigger_address="no. 6 place des Vosges",
        narrative_function="establishing",
        lenses=("famous_residents",),
        entities=("Hugo",),
        script_body=(
            "Victor Hugo moved to this apartment at 6 Place des Vosges in "
            "1832 and lived here for sixteen years. In these rooms he wrote "
            "the drama Ruy Blas and sketched out the early plans for his "
            "masterwork Les Miserables."
        ),
        word_count=40,
    )
    b = _beat(
        "no21-richelieu",
        trigger_address="no. 21 place des Vosges",
        narrative_function="establishing",
        lenses=("famous_residents",),
        entities=("Richelieu",),
        script_body=(
            "Cardinal Richelieu lived at no. 21 from 1615, before his rise "
            "to power. The arcaded façade and slate-blue mansard date from "
            "the original construction completed under Henri IV."
        ),
        word_count=37,
    )
    plan = select_poi_beats(poi, [a, b])
    ids = {b.id for b in plan.beats}
    assert ids == {"no6-hugo", "no21-richelieu"}


# ---------------------------------------------------------------------------
# Phase 3.5 — stop_orientation hoist (so cold-open can find it)
# ---------------------------------------------------------------------------


def test_orientation_beat_hoisted_to_head_under_sub_location_strategy():
    poi = _poi("Notre-Dame Cathedral")
    orient = BeatRef(
        id="orient",
        poi_id="poi",
        sub_location=None,  # orientation beats lack sub_location
        beat_type="stop_orientation",
        narrative_function="establishing",
        word_count=20,
    )
    parvis = _beat("parvis", sub_location="parvis", narrative_function="hook")
    nave = _beat("nave", sub_location="nave", narrative_function="deepen")
    choir = _beat("choir", sub_location="choir", narrative_function="climax")
    plan = select_poi_beats(poi, [parvis, nave, choir, orient])
    assert plan.beats[0].id == "orient"
    # Head + 3 sub_locations; orientation isn't double-counted as a closer.
    assert len(plan.beats) == 4


def test_orientation_beat_hoisted_under_trigger_address_strategy():
    poi = _poi("Place des Vosges")
    orient = BeatRef(
        id="orient",
        poi_id="poi",
        trigger_address=None,
        beat_type="stop_orientation",
        narrative_function="establishing",
        word_count=20,
    )
    addrs = [_beat(f"a{i}", trigger_address=f"no. {i} place des Vosges") for i in range(1, 6)]
    plan = select_poi_beats(poi, [*addrs, orient])
    assert plan.beats[0].id == "orient"
    assert plan.ordering_strategy == "trigger_address"


def test_dedup_never_drops_hoisted_orientation_cold_open():
    """B8-lite dedup must not evict the hoisted stop_orientation cold-open head.

    The orientation beat (micro, low score) shares a lens with an anchor-class
    paraphrase carrying the SAME entities (Jaccard 1.0), so the pair collides.
    Without head-protection, _pick_dedup_loser picks the low-scoring orientation
    as the loser and drops it — undoing _hoist_orientation and forcing the
    cold-open to fall through to the SYNTHESIZED_OPENER. The fix drops the
    OTHER (paraphrase) beat instead, so a stop_orientation beat still survives
    and generation._find_orientation_beat finds it.
    """
    poi = _poi("Notre-Dame Cathedral")
    shared_entities = ("Notre-Dame", "Maurice de Sully", "Gothic")
    orient = BeatRef(
        id="orient",
        poi_id="poi",
        sub_location=None,  # orientation beats lack sub_location
        beat_type="stop_orientation",
        narrative_function="establishing",
        emotional_register="neutral",
        beat_length_class="micro",  # lowest score -> the dedup loser without protection
        lenses=("hidden_history",),
        entities=shared_entities,
        script_body="Face the cathedral; Maurice de Sully began this Gothic pile.",
        word_count=20,
    )
    paraphrase = _beat(
        "paraphrase",
        sub_location="parvis",
        narrative_function="deepen",
        emotional_register="neutral",
        beat_length_class="anchor",  # highest score -> would win the collision
        lenses=("hidden_history",),
        entities=shared_entities,
        script_body="Maurice de Sully began this Gothic Notre-Dame, a landmark work.",
        word_count=200,
    )
    nave = _beat("nave", sub_location="nave", narrative_function="hook")
    choir = _beat("choir", sub_location="choir", narrative_function="climax")

    plan = select_poi_beats(poi, [orient, paraphrase, nave, choir])
    ids = [b.id for b in plan.beats]
    # The cold-open orientation beat must survive the dedup...
    assert "orient" in ids, f"hoisted orientation was dropped by dedup: {ids}"
    assert plan.beats[0].id == "orient", "orientation must remain the hoisted head"
    # ...and its paraphrase is the one collapsed (paraphrase removal still happened).
    assert "paraphrase" not in ids
    # generation's cold-open lookup must find a stop_orientation beat for this stop.
    assert _find_orientation_beat(plan) is not None
    assert _find_orientation_beat(plan).id == "orient"


def test_no_orientation_beat_means_no_change():
    poi = _poi("Notre-Dame Cathedral")
    parvis = _beat("parvis", sub_location="parvis")
    nave = _beat("nave", sub_location="nave")
    choir = _beat("choir", sub_location="choir")
    plan = select_poi_beats(poi, [parvis, nave, choir])
    assert {b.id for b in plan.beats} == {"parvis", "nave", "choir"}


# ---------------------------------------------------------------------------
# Phase 7.5 Fix 1 — geographically-honest cold-open hoist
# ---------------------------------------------------------------------------


def _hoist_route(start_poi: POI, sibling_poi: POI) -> Route:
    """Two-stop synthetic Route with start at index 0, sibling at index 1."""
    return Route(
        pois=(start_poi, sibling_poi),
        transits=(),
        total_walk_distance_m=0.0,
        total_walk_seconds=0,
        spine_area=None,
        target_dwell_seconds=0,
        err_short_total_seconds=0,
    )


def _hoist_seq(
    start_poi: POI,
    sibling_poi: POI,
    sibling_orient: BeatRef,
    *,
    start_orient: BeatRef | None = None,
) -> BeatSequence:
    start_beats: tuple[BeatRef, ...]
    if start_orient is not None:
        start_beats = (start_orient,)
    else:
        start_beats = (
            BeatRef(id="start-est", poi_id=start_poi.id, narrative_function="establishing"),
        )
    sibling_beats = (sibling_orient,)
    return BeatSequence(
        poi_beats=(
            POIBeats(
                poi_id=start_poi.id,
                poi_name=start_poi.name,
                ordering_strategy="narrative_function",
                beats=start_beats,
            ),
            POIBeats(
                poi_id=sibling_poi.id,
                poi_name=sibling_poi.name,
                ordering_strategy="narrative_function",
                beats=sibling_beats,
            ),
        )
    )


def test_hoist_rejected_when_beat_describes_distant_features():
    """Phase 7.5 Fix 1 — Tour 1 PdV scenario.

    Hotel de Sully (start, tier 4) shares Le Marais with Place des Vosges
    (sibling, tier 5). PdV's orientation beat carries physical_cues
    referencing the children's play area + Café Ma Bourgogne — features
    that don't exist at Hotel de Sully. With the cues present and the
    siblings ~270m apart (well past 100m), the hoist must reject and
    let the cold-open fall through to SYNTHESIZED_OPENER.
    """
    hotel_de_sully = POI(
        id="hotel-de-sully",
        name="Hotel de Sully",
        tier=4,
        poi_role="stop",
        lat=48.85389,
        lng=2.36514,
        areas=("Le Marais",),
    )
    pdv = POI(
        id="place-des-vosges",
        name="Place des Vosges",
        tier=5,
        poi_role="stop",
        lat=48.85553,
        lng=2.36560,
        areas=("Le Marais",),  # ~190m NE of Hotel de Sully
    )
    pdv_orient = BeatRef(
        id="ebeab682",
        poi_id=pdv.id,
        beat_type="stop_orientation",
        narrative_function="establishing",
        word_count=40,
        active_status="active",
        physical_cues=(
            PhysicalCue(cue="children's play area", direction="here", feature_type="view"),
            PhysicalCue(
                cue="Café Ma Bourgogne at the northwest corner",
                direction="north",
                feature_type="adjacent_landmark",
            ),
        ),
    )
    seq = _hoist_seq(hotel_de_sully, pdv, pdv_orient)
    route = _hoist_route(hotel_de_sully, pdv)
    assert find_area_orientation_beat(seq, route, start_idx=0) is None


def test_hoist_accepted_when_beat_is_area_generic():
    """No physical_cues → beat is treated as Area-generic; hoist proceeds."""
    start = POI(
        id="start",
        name="Start",
        tier=4,
        poi_role="stop",
        lat=48.85389,
        lng=2.36514,
        areas=("Le Marais",),
    )
    sibling = POI(
        id="sib",
        name="Sibling",
        tier=5,
        poi_role="stop",
        lat=48.85553,
        lng=2.36560,
        areas=("Le Marais",),
    )
    orient = BeatRef(
        id="orient",
        poi_id=sibling.id,
        beat_type="stop_orientation",
        word_count=30,
        active_status="active",
        physical_cues=(),  # no cues — Area-generic
    )
    seq = _hoist_seq(start, sibling, orient)
    route = _hoist_route(start, sibling)
    out = find_area_orientation_beat(seq, route, start_idx=0)
    assert out is not None
    assert out.id == "orient"


def test_hoist_accepted_when_beat_has_no_cues():
    """Empty physical_cues tuple is treated identically to no cues."""
    start = POI(
        id="start",
        name="Start",
        tier=4,
        poi_role="stop",
        lat=48.85389,
        lng=2.36514,
        areas=("Le Marais",),
    )
    sibling = POI(
        id="sib",
        name="Sibling",
        tier=5,
        poi_role="stop",
        lat=48.85553,
        lng=2.36560,
        areas=("Le Marais",),
    )
    orient = BeatRef(
        id="orient",
        poi_id=sibling.id,
        beat_type="stop_orientation",
        word_count=30,
        active_status="active",
        # physical_cues defaults to ()
    )
    seq = _hoist_seq(start, sibling, orient)
    route = _hoist_route(start, sibling)
    out = find_area_orientation_beat(seq, route, start_idx=0)
    assert out is not None
    assert out.id == "orient"


def test_hoist_accepted_when_sibling_within_proximity():
    """Beat carries cues but its source POI sits within HOIST_PROXIMITY_M.

    100m is the v3 schema's geofence radius; sibling POIs that close are
    treated as part of the same physical place and the cues describe
    features the listener can actually see from the start.
    """
    start = POI(
        id="start",
        name="Start",
        tier=4,
        poi_role="stop",
        lat=48.85553,
        lng=2.36560,
        areas=("Le Marais",),
    )
    # ~30m offset — well inside the proximity gate
    sibling = POI(
        id="sib",
        name="Sibling",
        tier=5,
        poi_role="stop",
        lat=48.85580,
        lng=2.36560,
        areas=("Le Marais",),
    )
    orient = BeatRef(
        id="orient",
        poi_id=sibling.id,
        beat_type="stop_orientation",
        word_count=30,
        active_status="active",
        physical_cues=(PhysicalCue(cue="the arcades", direction="here", feature_type="view"),),
    )
    seq = _hoist_seq(start, sibling, orient)
    route = _hoist_route(start, sibling)
    out = find_area_orientation_beat(seq, route, start_idx=0)
    assert out is not None
    assert out.id == "orient"


def test_hoist_rejected_when_beat_describes_distant_features_constant_check():
    """Pin the proximity constant so threshold drift fails loudly."""
    assert HOIST_PROXIMITY_M == 100.0


# ---------------------------------------------------------------------------
# C9 governor — govern_poi_beats truncation + additive contract fields.
# _beat's word_count=250 -> beat_spoken_seconds round(250/150*60)=100s;
# word_count=750 -> 300s.
# ---------------------------------------------------------------------------


def _plan(*beats: BeatRef) -> POIBeats:
    return POIBeats(
        poi_id="p", poi_name="P", ordering_strategy="narrative_function", beats=beats
    )


def test_govern_exempt_when_allowance_none_passes_plan_through():
    plan = _plan(_beat("a", word_count=750), _beat("b", word_count=750))
    capped, overflow = govern_poi_beats(plan, None)
    assert capped is plan
    assert overflow == ()


def test_govern_all_fit_no_overflow():
    plan = _plan(_beat("a", word_count=250), _beat("b", word_count=250))  # 100 + 100
    capped, overflow = govern_poi_beats(plan, 300)  # allowance 300 >= 200
    assert capped is plan
    assert overflow == ()


def test_govern_prefix_cut_overflow_is_ordered_remainder():
    # 100 + 100 + 100; allowance 250 -> keep a,b (200); c overflows.
    plan = _plan(
        _beat("a", word_count=250), _beat("b", word_count=250), _beat("c", word_count=250)
    )
    capped, overflow = govern_poi_beats(plan, 250)
    assert tuple(b.id for b in capped.beats) == ("a", "b")
    assert overflow == ("c",)


def test_govern_always_keeps_first_beat_even_when_it_alone_exceeds_allowance():
    # beat[0] alone (300s) > 100s allowance -> still kept (bounded overshoot); rest overflow.
    plan = _plan(_beat("a", word_count=750), _beat("b", word_count=250))
    capped, overflow = govern_poi_beats(plan, 100)
    assert tuple(b.id for b in capped.beats) == ("a",)
    assert overflow == ("b",)


def test_govern_empty_plan_is_noop():
    plan = _plan()
    capped, overflow = govern_poi_beats(plan, 100)
    assert capped is plan
    assert overflow == ()


def test_c9c_additive_contract_field_defaults():
    sp = ScriptPOI(id="x", name="X", tier=5, lat=48.0, lng=2.0)
    assert sp.overflow_beat_ids == ()
    route = _route_for([_poi("A")])
    assert route.fixed_end_poi_id is None


# ---------------------------------------------------------------------------
# KE0 — uncapped beat plan (keep-exploring foundation)
# ---------------------------------------------------------------------------


def _flat_beats(n: int) -> list[BeatRef]:
    # Distinct word_counts (desc) => deterministic score order; distinct bodies
    # so B8-lite never dedups them; all 'establishing' => the flat cap path.
    # No body + a unique entity per beat => B8-lite finds no near-duplicate,
    # so every beat survives to exercise the cap vs. no-cap difference.
    return [
        _beat(
            f"b{i:02d}",
            narrative_function="establishing",
            word_count=300 - i,
            entities=(f"Entity{i}",),
        )
        for i in range(n)
    ]


def test_full_uncaps_beyond_flat_max():
    poi = _poi("rich", tier=5)  # tier-5 flat cap = DEFAULT_FLAT_MAX (8)
    beats = _flat_beats(12)
    capped = select_poi_beats(poi, beats)
    full = select_poi_beats_full(poi, beats)
    assert len(capped.beats) == DEFAULT_FLAT_MAX
    assert len(full.beats) == 12, "full plan keeps every active beat"


def test_capped_is_a_prefix_of_full():
    poi = _poi("rich", tier=5)
    beats = _flat_beats(12)
    capped = select_poi_beats(poi, beats)
    full = select_poi_beats_full(poi, beats)
    capped_ids = [b.id for b in capped.beats]
    full_ids = [b.id for b in full.beats]
    assert full_ids[: len(capped_ids)] == capped_ids, "capped is a prefix of full (same order)"
    # The keep-exploring extras are exactly the tail beyond the cap.
    assert full_ids[len(capped_ids):] == full_ids[DEFAULT_FLAT_MAX:]


def test_full_equals_capped_when_under_cap():
    poi = _poi("thin", tier=5)
    beats = _flat_beats(5)  # under the flat cap
    capped = select_poi_beats(poi, beats)
    full = select_poi_beats_full(poi, beats)
    assert [b.id for b in full.beats] == [b.id for b in capped.beats]


def test_extra_beat_ids_are_full_minus_voiced_in_order():
    poi = _poi("rich", tier=5)
    beats = _flat_beats(12)
    voiced = [b.id for b in select_poi_beats(poi, beats).beats]  # the 8 the tour voiced
    full = [b.id for b in select_poi_beats_full(poi, beats).beats]
    extras = extra_beat_ids(poi, beats, voiced)
    # Exactly the tail beyond the cap, in the full plan's priority order.
    assert list(extras) == full[len(voiced):]
    assert len(extras) == 12 - DEFAULT_FLAT_MAX
    assert set(extras).isdisjoint(voiced), "an extra is never something already voiced"


def test_a_refused_walk_line_never_falls_through_to_keep_exploring():
    """P9R-S1.M6 — Paulo's re-judge: the origin-verified pick rightly refuses a
    transit beat for a walker arriving another way, and the extras pool then
    swept the UNVOICED beat into "keep exploring here" — where it plays
    ungated, standing in the square, telling him to walk up a boulevard he is
    not on. Extras play STANDING at the stop, so the walk's own narration
    (TRANSIT_NARRATIVE_FUNCTIONS — one definition with the pick and the
    anchor-block filter) never enters the pool. UNDO: drop the transit filter
    from extra_beat_ids -> the Frommer's line is an extra again -> RED."""
    poi = _poi("tour-saint-jacques", tier=5)
    frommers = _beat(
        "t-seb",
        narrative_function="transition",
        script_body="Walk up boulevard de Sébastopol and turn right into the square.",
        poi_id=poi.id,
    )
    story = _flat_beats(3)
    beats = [*story, frommers]
    voiced = [b.id for b in story]
    extras = extra_beat_ids(poi, beats, voiced)
    assert "t-seb" not in extras, extras


def test_extra_beat_ids_empty_when_all_voiced():
    poi = _poi("thin", tier=5)
    beats = _flat_beats(5)  # under the cap -> the tour voiced them all
    voiced = [b.id for b in select_poi_beats(poi, beats).beats]
    assert extra_beat_ids(poi, beats, voiced) == ()


def test_spatial_cap_keeps_best_by_score_not_first_in_walk_order():
    """R1: a dense square is sequenced address-by-address, but the per-tier ceiling
    keeps the BEST DEFAULT_FLAT_MAX beats by score — NOT the first ones in walk
    order. An anchor beat at the LAST address must survive the cap; the old prefix
    truncation seated the low-value early addresses and dropped it (measured: that
    bug cost PdV 6/18 golden overlap vs 11/18 with best-by-score selection)."""
    poi = _poi("Place des Vosges")  # tier 5
    beats = [
        _beat(
            f"micro-{i}",
            trigger_address=f"no. {i}",
            beat_length_class="micro",
            word_count=40,
            narrative_function="establishing",
        )
        for i in range(1, 10)  # nine low-value beats at the early addresses
    ]
    anchor = _beat(
        "anchor-late",
        trigger_address="no. 10",  # sorts LAST in the walk
        beat_length_class="anchor",  # highest length-class → highest _beat_score
        word_count=300,
        narrative_function="establishing",
    )
    beats.append(anchor)

    plan = select_poi_beats(poi, beats)
    ids = [b.id for b in plan.beats]

    assert plan.ordering_strategy == "trigger_address"
    assert len(plan.beats) <= DEFAULT_FLAT_MAX, "per-tier ceiling — no over-emission"
    assert "anchor-late" in ids, "best-by-score survives; prefix truncation would drop it"
    # and the survivors are still in walk order (address-sorted), anchor last
    kept_addrs = [b.trigger_address for b in plan.beats]
    assert kept_addrs == sorted(kept_addrs, key=lambda a: int(a.split()[-1]))


def test_spatial_capped_is_subset_of_full_even_with_dedup_cluster():
    """R1 KE-invariant (skeptic-refuted, now fixed): the capped plan must be a
    SUBSET of the uncapped (KE0) plan even when a paraphrase cluster straddles the
    cap. The cap runs LAST, over the SAME dedup'd ordering the full plan returns —
    so keep-exploring extras (full minus voiced) can never omit a voiced beat or
    surface a paraphrase of one. (The old cap-before-dedup ran two independent
    dedup passes over different populations and kept different cluster survivors.)"""
    poi = _poi("Place des Vosges")  # tier 5, trigger_address strategy
    dup_body = "The great revolution began right here in this very square in 1789."
    # Eight high-score fillers at addresses 2..9 fill the 8-beat cap.
    beats = [
        _beat(
            f"fill-{i}",
            trigger_address=f"no. {i}",
            beat_length_class="anchor",  # highest length-class -> outranks the cluster
            word_count=200,
            narrative_function="establishing",
            script_body=f"Distinct filler fact number {i} about this address, unique wording.",
        )
        for i in range(2, 10)  # eight fillers
    ]
    # The paraphrase cluster that would break the old cap-before-dedup order:
    #  - dup-lo sits at the FIRST address (index 0, head-protected by the spatial
    #    cap) but scores lowest, so the OLD cap kept it while its higher-score
    #    sibling was capped out -> the capped-side dedup then kept dup-lo.
    #  - dup-hi (the sibling) outscores dup-lo but is outranked by the 8 fillers,
    #    so it's capped out; the FULL-side dedup keeps dup-hi and drops dup-lo.
    # => under the old order dup-lo was VOICED yet absent from `full`. Cap-last
    #    (single shared dedup) makes that impossible.
    beats.append(
        _beat("dup-lo-head", trigger_address="no. 1", beat_length_class="micro",
              word_count=20, narrative_function="establishing", script_body=dup_body)
    )
    beats.append(
        _beat("dup-hi", trigger_address="no. 30", beat_length_class="seasoning",
              word_count=60, narrative_function="establishing",
              script_body=dup_body + " It truly changed everything forever.")
    )

    voiced = {b.id for b in select_poi_beats(poi, beats).beats}
    full = {b.id for b in select_poi_beats_full(poi, beats).beats}
    assert voiced <= full, f"capped must be a subset of full; leaked {voiced - full}"
    # keep-exploring extras never re-offer a voiced beat
    extras = set(extra_beat_ids(poi, beats, voiced))
    assert extras.isdisjoint(voiced)
    assert extras == full - voiced


def test_full_keeps_every_active_beat_for_sub_location_multi_beat_bucket():
    """R1 KE-invariant (regression): select_poi_beats_full's 'every active beat'
    contract must hold for the sub_location strategy too — NOT just the flat
    strategy (test_full_uncaps_beyond_flat_max). A multi-beat sub_location bucket
    used to drop its non-best members from the UNCAPPED plan (only _pick_best was
    appended), so a bucket-loser was voiced nowhere AND surfaced as no
    keep-exploring extra (extras = full minus voiced, and it was missing from
    full). Every active beat must reach `full`, and the bucket-loser must be a
    keep-exploring extra."""
    poi = _poi("Conciergerie")  # tier 5, sub_location strategy
    beats = [
        _beat("choir", sub_location="choir", beat_length_class="anchor", word_count=200),
        _beat("nave", sub_location="nave", beat_length_class="anchor", word_count=200),
        _beat("parvis-A", sub_location="parvis", beat_length_class="anchor", word_count=200),
        _beat("parvis-B", sub_location="parvis", beat_length_class="mid", word_count=150),
    ]
    full = select_poi_beats_full(poi, beats)
    assert full.ordering_strategy == "sub_location"
    full_ids = {b.id for b in full.beats}
    active_ids = {b.id for b in beats}
    assert full_ids == active_ids, (
        f"full plan must keep every active beat; lost {active_ids - full_ids}"
    )
    # extra_beat_ids = full minus voiced; before the fix the bucket-loser was
    # missing from `full` entirely, so it could be neither voiced nor a KE extra.
    voiced = [b.id for b in select_poi_beats(poi, beats).beats]
    assert set(voiced) | set(extra_beat_ids(poi, beats, voiced)) == active_ids, (
        "every active beat is either voiced or a keep-exploring extra — none silently lost"
    )


def test_full_keeps_every_active_beat_for_trigger_address_multi_beat_bucket():
    """R1 KE-invariant (regression): the trigger_address strategy had the same
    bucket-loser loss as sub_location — a second beat sharing a trigger_address
    was dropped from the UNCAPPED full plan. Every active beat must reach `full`."""
    poi = _poi("Place des Vosges")  # tier 5, trigger_address strategy
    # Five distinct addresses -> trigger_address strategy; one address has 2 beats.
    beats = [
        _beat(f"no{i}", trigger_address=f"no. {i}", beat_length_class="anchor", word_count=200)
        for i in range(1, 6)
    ]
    beats.append(
        _beat("no1-second", trigger_address="no. 1", beat_length_class="mid", word_count=150)
    )
    full = select_poi_beats_full(poi, beats)
    assert full.ordering_strategy == "trigger_address"
    full_ids = {b.id for b in full.beats}
    active_ids = {b.id for b in beats}
    assert full_ids == active_ids, (
        f"full plan must keep every active beat; lost {active_ids - full_ids}"
    )
    voiced = [b.id for b in select_poi_beats(poi, beats).beats]
    assert set(voiced) | set(extra_beat_ids(poi, beats, voiced)) == active_ids, (
        "every active beat is either voiced or a keep-exploring extra — none silently lost"
    )


def test_spatial_cap_never_drops_the_cold_open_orientation():
    """R1 skeptic finding (fixed): _enforce_tone_variety runs after the hoist and can
    swap the orientation beat off index 0 (it's the only non-somber donor). If the
    per-tier cap then protected index 0 by POSITION and scored the displaced
    orientation against high-value fillers, the cold-open could be dropped entirely —
    generation would silently fall through to a synthesized opener. The cap must keep
    the stop_orientation beat BY TYPE, and run before tone-variety."""
    poi = _poi("Place des Vosges")  # tier 5 -> DEFAULT_FLAT_MAX = 8
    orient = BeatRef(
        id="orient",
        poi_id="poi",
        trigger_address=None,
        beat_type="stop_orientation",
        narrative_function="establishing",
        emotional_register="neutral",  # the lone non-somber donor
        beat_length_class="micro",  # lowest score -> a naive score-cap would drop it
        word_count=15,
    )
    # Ten high-score SOMBER address beats: they trigger tone-variety (3 in a row) and
    # out-score the orientation, so an unprotected cap would evict it.
    addrs = [
        _beat(
            f"a{i}",
            trigger_address=f"no. {i} place des Vosges",
            emotional_register="somber",
            beat_length_class="anchor",
            word_count=200,
            narrative_function="deepen",
        )
        for i in range(1, 11)
    ]

    plan = select_poi_beats(poi, [*addrs, orient])
    ids = [b.id for b in plan.beats]
    assert len(plan.beats) <= DEFAULT_FLAT_MAX
    assert "orient" in ids, "the cold-open orientation must survive the per-tier cap"
    # and it survives in the FULL plan too, so keep-exploring never re-offers it
    full_ids = {b.id for b in select_poi_beats_full(poi, [*addrs, orient]).beats}
    assert "orient" in full_ids
    assert set(ids) <= full_ids


def test_spatial_cap_covers_groups_and_no_key_before_complementary_fill():
    """A dense spatial stop remains a coherent *tour*, not a global top-N list.

    Strong secondary beats at one location may fill spare capacity, but cannot
    erase every primary from quieter physical groups or all POI-wide context.
    The ordinary tier cap remains the hard upper bound.
    """
    poi = _poi("dense spatial stop")
    beats = [
        _beat(
            f"alpha-{i}",
            sub_location="alpha",
            beat_length_class="anchor",
            word_count=200 + i,
        )
        for i in range(12)
    ]
    beats.extend(
        [
            _beat(
                "beta-primary",
                sub_location="beta",
                beat_length_class="micro",
                word_count=20,
            ),
            _beat(
                "gamma-primary",
                sub_location="gamma",
                beat_length_class="micro",
                word_count=20,
            ),
            _beat(
                "poi-wide-context",
                beat_length_class="seasoning",
                word_count=60,
            ),
            _beat(
                "poi-wide-runner-up",
                beat_length_class="micro",
                word_count=10,
            ),
        ]
    )

    plan = select_poi_beats(poi, beats)
    ids = {beat.id for beat in plan.beats}

    assert plan.ordering_strategy == "sub_location"
    assert len(plan.beats) == DEFAULT_FLAT_MAX
    assert {"beta-primary", "gamma-primary", "poi-wide-context"} <= ids
    assert len([beat_id for beat_id in ids if beat_id.startswith("alpha-")]) > 1, (
        "remaining capacity should still admit complementary same-location beats"
    )
    assert ids <= {beat.id for beat in select_poi_beats_full(poi, beats).beats}


def _yr_beat(bid: str, body: str, *, beat_type: str | None = None) -> BeatRef:
    return BeatRef(id=bid, poi_id="p", script_body=body, beat_type=beat_type,
                   narrative_function="establishing", active_status="active")


def test_min_year_extracts_earliest_year_or_sentinel():
    assert _min_year(_yr_beat("a", "Built in 1875, restored in 2019.")) == 1875
    assert _min_year(_yr_beat("b", "A timeless colonnade of marble.")) == 9999


def test_order_body_chronologically_flows_oldest_to_newest():
    """A history-dense stop's beats read oldest→newest; a hoisted orientation
    head stays put; undated beats keep order and trail (the Louvre-jumble fix)."""
    orient = _yr_beat("o", "You're standing in the great courtyard.", beat_type="stop_orientation")
    b1983 = _yr_beat("b1983", "In 1983 the glass pyramid work began under Pei.")
    b1190 = _yr_beat("b1190", "Philippe-Auguste raised the first fortress here in 1190.")
    b2025 = _yr_beat("b2025", "On 19 October 2025, thieves entered the gallery.")
    undated = _yr_beat("bx", "The galleries stretch for kilometres of corridors.")
    out = _order_body_chronologically([orient, b1983, b1190, b2025, undated])
    assert out[0].id == "o", "hoisted orientation head must stay at index 0"
    assert [b.id for b in out[1:]] == ["b1190", "b1983", "b2025", "bx"]


def test_order_body_chronologically_is_pure_reorder_no_drops():
    beats = [_yr_beat(f"b{y}", f"An event in {y}.") for y in (2001, 1500, 1800)]
    out = _order_body_chronologically(beats)
    assert {b.id for b in out} == {b.id for b in beats}  # same set, just reordered
    assert [b.id for b in out] == ["b1500", "b1800", "b2001"]


# --- tone variety must not outrank spatial coherence (2026-07-19) -----------


def _groups_in_order(beats: list[BeatRef]) -> list[str | None]:
    """The sub_location sequence with consecutive repeats collapsed.

    A sub-anchor visited once contiguously appears exactly once here; a
    sub-anchor the ordering split into two blocks appears twice.
    """
    out: list[str | None] = []
    for beat in beats:
        if not out or out[-1] != beat.sub_location:
            out.append(beat.sub_location)
    return out


def _somber_split_case() -> list[BeatRef]:
    """A somber-dense stop, already sequenced address-by-address.

    Modelled on the Conciergerie (a prison, so somber-dense, and it uses the
    sub_location strategy per choose_ordering_strategy's own docstring).
    """
    return [
        _beat("a1", sub_location="rue-de-paris-cells", emotional_register="somber"),
        _beat("a2", sub_location="rue-de-paris-cells", emotional_register="somber"),
        _beat("a3", sub_location="rue-de-paris-cells", emotional_register="somber"),
        _beat("b1", sub_location="kitchen-pavilion", emotional_register="neutral"),
        _beat("b2", sub_location="kitchen-pavilion", emotional_register="neutral"),
    ]


def test_tone_variety_ungrouped_splits_a_sub_anchor():
    """MUTATION PROOF: the pre-fix whole-stop interleave DOES split a sub-anchor.

    This pins the defect itself, so the grouped test below cannot pass vacuously.
    Observed in a real tour (data/paris/tours/pont-neuf-60min-5afc2e.md): the
    Conciergerie emitted `### rue-de-paris-cells` at two non-contiguous points,
    separated by marie-antoinette-cell and kitchen-pavilion.
    """
    out = _enforce_tone_variety(_somber_split_case())
    groups = _groups_in_order(out)
    assert groups.count("rue-de-paris-cells") == 2, (
        f"expected the ungrouped interleave to split the sub-anchor, got {groups}"
    )


def test_tone_variety_grouped_keeps_each_sub_anchor_contiguous():
    """Tone variety confined to one sub-anchor at a time: no teleporting the listener."""
    out = _enforce_tone_variety(_somber_split_case(), group_of=lambda b: b.sub_location)
    groups = _groups_in_order(out)
    assert groups == ["rue-de-paris-cells", "kitchen-pavilion"], groups
    # Reorder-only: no beat invented, dropped, or duplicated.
    assert {b.id for b in out} == {"a1", "a2", "a3", "b1", "b2"}
    assert len(out) == 5


def test_tone_variety_grouped_still_breaks_somber_runs_inside_a_group():
    """Grouping must not neuter tone variety where relief exists within the group."""
    beats = [
        _beat("s1", sub_location="hall", emotional_register="somber"),
        _beat("s2", sub_location="hall", emotional_register="somber"),
        _beat("s3", sub_location="hall", emotional_register="somber"),
        _beat("r1", sub_location="hall", emotional_register="wry"),
    ]
    out = _enforce_tone_variety(beats, group_of=lambda b: b.sub_location)
    assert not _has_three_somber_run([b.emotional_register for b in out])
    assert _groups_in_order(out) == ["hall"]


def test_tone_variety_flat_strategy_behaviour_is_unchanged():
    """group_of=None is the pre-existing whole-stop interleave, byte-for-byte."""
    beats = _somber_split_case()
    assert _enforce_tone_variety(beats, group_of=None) == _enforce_tone_variety(beats)


def test_select_poi_beats_keeps_sub_anchors_contiguous_end_to_end():
    """The integration path: a sub_location-strategy stop emits each anchor once."""
    poi = POI(
        id="conciergerie", name="Conciergerie", tier=5, poi_role="stop", lat=48.8560, lng=2.3455
    )
    beats = [
        _beat("c1", sub_location="tour-bonbec", emotional_register="somber"),
        _beat("c2", sub_location="tour-bonbec", emotional_register="somber"),
        _beat("c3", sub_location="rue-de-paris-cells", emotional_register="somber"),
        _beat("c4", sub_location="rue-de-paris-cells", emotional_register="somber"),
        _beat("c5", sub_location="kitchen-pavilion", emotional_register="neutral"),
        _beat("c6", sub_location="kitchen-pavilion", emotional_register="neutral"),
    ]
    plan = select_poi_beats_full(poi, beats)
    assert plan.ordering_strategy == "sub_location", plan.ordering_strategy
    groups = _groups_in_order(list(plan.beats))
    assert len(groups) == len(set(groups)), f"a sub-anchor was split: {groups}"


def test_governor_seam_preserves_every_beat_and_voiced_contiguity():
    """The governor seam is where a reorder can silently DEMOTE a voiced beat.

    Regression guard (2026-07-19): the sub-anchor grouping fix reordered the
    Conciergerie's beats, which moved beat 3e99f26f past govern_poi_beats' audio
    cut — voiced 9->8, overflow 3->4. That is acceptable (the beat survives as
    keep-exploring content) but it must never happen SILENTLY, and the prefix cut
    must not reintroduce a split sub-anchor in the part the listener actually hears.

    Two invariants, both cheap and both currently unguarded by `make test`
    (pyproject deselects golden/grade/invariants, and those are the only other
    gates that watch the voiced beat set):
      1. voiced + overflow == the full plan. Nothing vanishes at the seam.
      2. the VOICED prefix visits each sub-anchor at most once, contiguously.
    """
    poi = POI(
        id="conciergerie", name="Conciergerie", tier=5, poi_role="stop", lat=48.8560, lng=2.3455
    )
    # 3 sub-anchors x 3 beats. The fixture size and allowance are load-bearing:
    # with only 6 beats there is NO allowance that discriminates — below 240 the
    # cut truncates the split away before it can be observed, and at 240+ the cut
    # stops firing at all, so invariant 2 passes even with the fix reverted. At
    # 9 beats / 280s the cut still fires (keeping invariant 1 meaningful) AND the
    # mutated ordering's split lands inside the voiced prefix.
    beats = [
        _beat("c1", sub_location="tour-bonbec", emotional_register="somber"),
        _beat("c2", sub_location="tour-bonbec", emotional_register="somber"),
        _beat("c3", sub_location="tour-bonbec", emotional_register="somber"),
        _beat("c4", sub_location="rue-de-paris-cells", emotional_register="somber"),
        _beat("c5", sub_location="rue-de-paris-cells", emotional_register="somber"),
        _beat("c6", sub_location="rue-de-paris-cells", emotional_register="somber"),
        _beat("c7", sub_location="kitchen-pavilion", emotional_register="neutral"),
        _beat("c8", sub_location="kitchen-pavilion", emotional_register="neutral"),
        _beat("c9", sub_location="kitchen-pavilion", emotional_register="neutral"),
    ]
    plan = select_poi_beats_full(poi, beats)
    full_ids = [b.id for b in plan.beats]

    # Tight enough that the governor actually cuts, wide enough that a split
    # would be observable inside the voiced prefix. Both halves matter.
    capped, overflow = govern_poi_beats(plan, 280)
    voiced_ids = [b.id for b in capped.beats]

    # 1. Conservation across the seam — demotion is allowed, disappearance is not.
    assert voiced_ids + list(overflow) == full_ids, (
        f"beats lost at the governor seam: full={full_ids} "
        f"voiced={voiced_ids} overflow={list(overflow)}"
    )
    # 2. What the listener HEARS must not teleport between sub-anchors.
    voiced_groups = _groups_in_order(list(capped.beats))
    assert len(voiced_groups) == len(set(voiced_groups)), (
        f"the voiced prefix splits a sub-anchor: {voiced_groups}"
    )


# ---------------------------------------------------------------------------
# S3.M4 — the asked subject survives the cap.
# ---------------------------------------------------------------------------


def test_the_asked_subject_survives_the_cap_on_both_strategies():
    """A stop's per-tier ceiling keeps the best beats by score; when the person
    asked for a subject, an on-family beat must never be displaced by off-family
    padding — otherwise the day the matcher steered is undone at the cap, which
    is how a stop full of asked-for stories still voiced everything else.
    Interest arrives pre-expanded (the matcher's own one hop), so a bare leaf
    set here stands for any request."""
    interest = ["dark_history"]
    # FLAT strategy: 13 beats in one function bucket against a 12-beat ceiling;
    # the lone on-family beat is the LIGHTEST, so without the lens key it is the
    # one the ceiling sheds.
    flat = [
        _beat(f"pad{i}", narrative_function="deepen", word_count=200,
              lenses=("waterways_views",))
        for i in range(12)
    ] + [_beat("asked", narrative_function="deepen", word_count=10,
               lenses=("dark_history",))]
    plan = select_poi_beats(_poi("flat"), flat, interest_lenses=interest)
    assert "asked" in {b.id for b in plan.beats}, "the asked beat outranks the padding"

    # SPATIAL strategy: sub-located beats under the same ceiling.
    spatial = [
        _beat(f"pad{i}", sub_location=f"spot-{i}", word_count=200,
              lenses=("waterways_views",))
        for i in range(12)
    ] + [_beat("asked", sub_location="spot-asked", word_count=10,
               lenses=("dark_history",))]
    plan = select_poi_beats(_poi("spatial"), spatial, interest_lenses=interest)
    assert "asked" in {b.id for b in plan.beats}


def test_the_governor_never_cuts_the_day_down_to_none_of_the_asked_subject():
    """S3, measured through the API: Camille's Tuileries voiced dark_history and
    parks while its architecture beat sat in the overflow — the narrative arc
    orders the buckets, so an off-family hook outranks an on-family deepen, and
    the allowance prefix cut everything after it. When the person asked for
    subjects and the prefix keeps none of the family the plan holds, the governor
    swaps ONE in — the bounded one-beat overshoot it already ratifies — so a
    served stop is never mute on the very subject that seated it."""
    beats = (
        _beat("hook-off", narrative_function="hook", word_count=150,
              lenses=("dark_history",)),
        _beat("open-off", narrative_function="establishing", word_count=150,
              lenses=("parks_gardens",)),
        _beat("asked", narrative_function="deepen", word_count=150,
              lenses=("historic_arch",)),
    )
    plan = POIBeats(poi_id="p", poi_name="P", ordering_strategy="narrative_function",
                    beats=beats)
    seconds = beat_spoken_seconds(beats[0])
    kept, overflow = govern_poi_beats(
        plan, seconds * 2, interest=frozenset({"historic_arch", "arch_design"})
    )
    kept_ids = [b.id for b in kept.beats]
    assert "asked" in kept_ids, f"the asked subject was governed away: {kept_ids}"
    assert kept_ids[0] == "hook-off", "the cold-open is never swapped out"
    assert "open-off" in overflow

    # No interest, or the family already kept: byte-identical to the plain prefix.
    plain, plain_over = govern_poi_beats(plan, seconds * 2)
    assert [b.id for b in plain.beats] == ["hook-off", "open-off"]
    assert plain_over == ("asked",)
