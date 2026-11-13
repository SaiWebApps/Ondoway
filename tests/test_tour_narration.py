"""stop_narration_text: group a Script's sentences into per-stop text.

Pure-function unit tests (no DB, no audio). Verifies that the narration the
engine already produces (cold-open + transit glue + beats + closing) can be
grouped per stop and joined in order — the text handed to TTS.
"""

from __future__ import annotations

import datetime as _dt

from src.tour.contract import Script, ScriptPOI, Sentence, TourInput, ValidationReport
from src.tour.render_md import stop_leg_text, stop_narration_text


def _script(sentences: list[Sentence], *, seated: tuple[str, ...] = ("b1", "b2")) -> Script:
    return Script(
        city_slug="paris",
        generated_at=_dt.datetime(2026, 6, 13, tzinfo=_dt.UTC).isoformat(),
        inputs=TourInput(start=(48.8555, 2.3656), duration_min=60, city_slug="paris"),
        total_audio_seconds=0,
        total_walking_seconds=0,
        total_walk_distance_m=0,
        total_planned_seconds=0,
        selected_pois=(
            ScriptPOI(
                id="p1", name="Place des Vosges", tier=5, lat=48.85, lng=2.36, beat_ids=seated
            ),
        ),
        lens_coverage={},
        script=tuple(sentences),
        validation=ValidationReport(),
    )


def test_groups_by_stop_and_the_leg_line_is_the_leg_piece_not_the_stop_s() -> None:
    """The stop's piece must not open with the walking line the visitor has already walked.
    A stop's sentences split by THE one leg-vs-stop rule
    (`generation.is_walk_concurrent`, the rule the workbench's leg cards and the blueprint's
    playback assignments already use): the walk-concurrent sentences are the stop's LEG
    piece, spoken on the leg; the rest are its STORY, the file placed at the footprint.
    This test used to pin `{0: "s0a s0b"}` — the nav line folded into the stop."""
    script = _script(
        [
            Sentence(text="s0a", source_id="b1", source_type="beat", stop_idx=0),
            Sentence(text="s0b", source_id="GLUE_NAV", source_type="glue", stop_idx=0),
            Sentence(text="s1a", source_id="b2", source_type="beat", stop_idx=1),
        ]
    )
    assert stop_narration_text(script) == {0: "s0a", 1: "s1a"}
    assert stop_leg_text(script) == {0: "s0b"}


def test_the_leg_piece_holds_the_walking_line_the_reflection_and_the_one_liner() -> None:
    """The leg INTO stop 1 carries its nav line, a reflection and a walk-past one-liner
    (a beat the plan did NOT seat — a vignette); the story opens on the stop's own first
    story sentence and still ends on its close. Nothing is voiced twice."""
    script = _script(
        [
            Sentence(text="Settle in.", source_id="GLUE_PACING", source_type="glue", stop_idx=0),
            Sentence(text="Story zero.", source_id="b1", source_type="beat", stop_idx=0),
            Sentence(text="Walk north for five minutes.", source_id="GLUE_NAV",
                     source_type="glue", stop_idx=1),
            Sentence(text="Think back on the square.", source_id="GLUE_REFLECTION",
                     source_type="glue", stop_idx=1),
            Sentence(text="The fountain dates from 1685.", source_id="vb-fountain",
                     source_type="beat", stop_idx=1),
            Sentence(text="Story one.", source_id="b2", source_type="beat", stop_idx=1),
            Sentence(text="And that's stop one.", source_id="GLUE_CLOSING",
                     source_type="glue", stop_idx=1),
        ]
    )
    assert stop_narration_text(script) == {
        0: "Settle in. Story zero.",
        1: "Story one. And that's stop one.",
    }
    assert stop_leg_text(script) == {
        1: "Walk north for five minutes. Think back on the square. The fountain dates from 1685."
    }


def test_empty_script_returns_empty_dict() -> None:
    assert stop_narration_text(_script([])) == {}


def test_single_stop_single_sentence() -> None:
    script = _script(
        [Sentence(text="only", source_id="b1", source_type="beat", stop_idx=0)]
    )
    assert stop_narration_text(script) == {0: "only"}


def _yellow_assessment(on_lens_fill_ratio):
    from src.tour.contract import TourabilityAssessment

    return TourabilityAssessment(
        status="YELLOW", walk_radius_m=369.0, fill_ratio=0.57,
        dwell_capacity_seconds=1000, target_dwell_seconds=1793,
        reachable_poi_count=3, reachable_beat_count=10, anchor_candidate_count=2,
        cluster_compactness=0.5, duration_min=60, round_trip=True,
        on_lens_fill_ratio=on_lens_fill_ratio,
    )


def test_yellow_banner_discloses_lens_specific_fill():
    from src.tour.render_md import _yellow_banner

    # Same overall 57% fill, but the on-lens fill (20%) is disclosed for the interest.
    banner = _yellow_banner(_yellow_assessment(0.20), ["dark_history"])
    assert "57% audio fill" in banner
    assert "dark_history" in banner
    assert "20%" in banner


def test_yellow_banner_no_lens_line_without_lenses():
    from src.tour.render_md import _yellow_banner

    banner = _yellow_banner(_yellow_assessment(None), None)
    assert "57% audio fill" in banner
    assert "for your interest" not in banner.lower()


def _surplus_fill_assessment(anchor_candidate_count: int = 2, cluster_compactness: float = 0.5):
    """YELLOW where fill is already at/above target — the Louvre 45-min repro.

    fill_ratio=1.00 (100% fill) but anchor_candidate_count=2 is below
    GREEN_ANCHOR_CANDIDATES_MIN (4), so density._status returns YELLOW via
    the yellow_by_surplus_fill clause, not because fill is low.
    """
    from src.tour.contract import TourabilityAssessment

    return TourabilityAssessment(
        status="YELLOW", walk_radius_m=369.0, fill_ratio=1.00,
        dwell_capacity_seconds=1793, target_dwell_seconds=1793,
        reachable_poi_count=2, reachable_beat_count=11,
        anchor_candidate_count=anchor_candidate_count,
        cluster_compactness=cluster_compactness, duration_min=45, round_trip=True,
    )


def test_yellow_banner_near_green_fill_does_not_claim_below_70_80_bar():
    """Fill between the empirical 70-80% bar and GREEN (e.g. 99%)
    must not claim "ran below the empirical 70-80% bar" (still false at 99%),
    but should disclose the near-miss honestly. Live repro: the same Louvre
    45-min tour at fill_ratio=0.99 post-corpus-edit."""
    from src.tour.render_md import _yellow_banner

    a = _surplus_fill_assessment()
    if hasattr(a, "__dict__"):
        a = a.__class__(**{**a.__dict__, "fill_ratio": 0.99})
    else:
        a = a._replace(fill_ratio=0.99)
    banner = _yellow_banner(a, None)
    assert "ran below the empirical" not in banner
    assert "99%" in banner


def test_yellow_banner_surplus_fill_does_not_claim_below_bar():
    """Fill=100% YELLOW-for-anchor-scarcity must not claim below-bar fill.

    Live repro: a Louvre 45-min round-trip rendered
    "100% audio fill" alongside "ran below the empirical 70-80% audio-fill
    bar" — self-contradictory. The banner must state the TRUE limiting
    factor (thin anchor pool) instead.
    """
    from src.tour.render_md import _yellow_banner

    banner = _yellow_banner(_surplus_fill_assessment(), None)
    assert "100% audio fill" in banner
    assert "ran below the empirical" not in banner
    assert "substantial stop" in banner


def test_yellow_banner_spread_out_cluster_states_layout_reason():
    """Surplus fill + spread-out (not thin-anchor) cluster also gets an honest reason."""
    from src.tour.render_md import _yellow_banner

    banner = _yellow_banner(
        _surplus_fill_assessment(anchor_candidate_count=5, cluster_compactness=0.9), None
    )
    assert "ran below the empirical" not in banner
    assert "spread out" in banner


def test_yellow_banner_genuine_low_fill_keeps_below_bar_message():
    """Regression: genuinely low fill (0.5 <= fill < 1.0) must keep the below-bar line."""
    from src.tour.render_md import _yellow_banner

    banner = _yellow_banner(_yellow_assessment(None), None)
    assert "ran below the empirical" in banner


# ── The anchor cut: the goodbye and the leg ──


def _west_front():
    """The reviewed anchor of a marquee's front, as the corpus carries it."""
    from src.tour.contract import Anchor

    return Anchor(
        label="The west front",
        sub_locations=["facade"],
        lat=48.85325,
        lng=2.34875,
        radius_m=80.0,
    )


def _chaptered_script():
    """One stop whose story has an ANCHORED sentence, a plain story sentence, its close,
    and a leg that carries BOTH a walking line and an anchored walk-past one-liner."""
    return _script(
        [
            Sentence(text="Walk north for five minutes.", source_id="GLUE_NAV",
                     source_type="glue", stop_idx=1),
            Sentence(text="The Revolution felled these facade kings.",
                     source_id="vb-facade", source_type="beat", stop_idx=1),
            Sentence(text="Story one.", source_id="b2", source_type="beat", stop_idx=1),
            Sentence(text="Look at the row of twenty-seven kings.",
                     source_id="b-facade", source_type="beat", stop_idx=1),
            Sentence(text="That's the walk — the symbolic heart of France.",
                     source_id="GLUE_CLOSING", source_type="glue", stop_idx=1),
        ],
        seated=("b1", "b2", "b-facade"),
    )


def _resolve_facade():
    west = _west_front()
    return lambda s: west if s.source_id in ("b-facade", "vb-facade") else None


def test_a_chaptered_stop_does_not_say_goodbye_before_its_chapters() -> None:
    """When the story piece ended with the stop's GOODBYE, the farewell was said on
    arrival and the chapter played after it. A visitor who hears a goodbye stops
    listening, and never hears the chapter still owed. The goodbye is the last thing said
    at the last place.

    So: a stop CUT INTO CHAPTERS does not carry its close in the story piece. The close is
    already its own hash-guarded artifact (`stop_close_text` → `close_audio_url`)
    and the phone plays it when the LAST chapter has been told. A stop with no chapters is
    untouched — its story still ends on its close.
    """
    script = _chaptered_script()
    story = stop_narration_text(script, anchor_of=_resolve_facade())
    assert story[1] == "Story one.", (
        "a chaptered stop's story must not end with its goodbye — the chapter comes after it"
    )
    # The close is still recoverable as its own line, and the chapter still holds its text.
    from src.tour.render_md import stop_close_text, stop_segment_text

    assert stop_close_text(script)[1] == "That's the walk — the symbolic heart of France."
    chapters = stop_segment_text(script, _resolve_facade())[1]
    assert [a.label for a, _ in chapters] == ["The west front"]

    # A stop with NO anchors keeps its plain shape: the close ends the story.
    assert stop_narration_text(script)[1].endswith("the symbolic heart of France.")


def test_an_anchored_sentence_never_rides_the_leg() -> None:
    """The anchor cut applies to the LEG as well as the STORY, so a sentence belonging to a
    reviewed anchor is never spoken while walking toward it. On the leg, "these facade
    kings" points at a facade the visitor cannot see yet, and it spends the reveal early.
    An anchored sentence belongs AT its anchor, wherever the leg-vs-stop rule would
    otherwise put it."""
    script = _chaptered_script()
    assert stop_leg_text(script, anchor_of=_resolve_facade()) == {
        1: "Walk north for five minutes."
    }, "the leg must carry the walking line only — the anchored one-liner belongs at its anchor"
    # Without a resolver the leg is unchanged (no anchors on this day).
    assert stop_leg_text(script)[1].startswith("Walk north for five minutes.")
