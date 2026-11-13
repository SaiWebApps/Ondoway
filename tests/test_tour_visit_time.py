"""How long a visitor spends AT a place — the third hand on the tour clock.

Time at a stop is `min(what the place absorbs, what this visitor's interest
justifies, the party's ceiling)`, floored at the stop's own narration.

WHY ALL THREE ARGUMENTS ARE NEEDED. The first two are both MAXIMISING, so neither
can express a family for whom six minutes is the ceiling here, not the floor —
hence the party ceiling. And a single per-place number cannot express an
architecture lover and a dark-history walker giving the same Cour Carree 33 minutes
and 8 — hence the interest term.

These tests are hermetic: constructed POIs, a constructed lens graph, no database.
"""

from __future__ import annotations

import pytest

from src.tour.contract import POI, BeatRef
from tests.test_tour_selection import _snap

PARIS = (48.8566, 2.3522)


def _place(
    pid: str,
    *,
    outside_min: int,
    inside_s: int | None,
    basis: str = "a sentence that argues for the numbers",
) -> POI:
    return POI(
        id=pid,
        name=pid,
        tier=4,
        poi_role="stop",
        lat=PARIS[0],
        lng=PARIS[1],
        areas=("Paris",),
        beat_count=3,
        typical_duration_min=outside_min,
        visit_seconds_inside=inside_s,
        visit_basis=basis,
    )


def _beat(poi_id: str, lens: str) -> BeatRef:
    return BeatRef(
        id=f"{poi_id}-{lens}",
        poi_id=poi_id,
        est_spoken_seconds=90,
        active_status="active",
        lenses=(lens,),
    )


#: A lens graph where `historic_arch` and `dark_history` are unrelated — neither is
#: a parent or child of the other — so a POI whose beats are tagged `historic_arch`
#: is a DIRECT hit for an architecture lover and a MISS for a dark-history walker.
LENS_NEIGHBOURS = {
    "historic_arch": frozenset({"architecture"}),
    "dark_history": frozenset({"history"}),
}


def _corpus(poi: POI, *, beat_lens: str):
    return _snap(
        [poi],
        beats_by_poi={poi.id: [_beat(poi.id, beat_lens)]},
        lens_neighbors=LENS_NEIGHBOURS,
    )


def test_the_same_place_costs_two_visitors_different_amounts_of_time():
    """The two-visitor experiment, on one interior-capable place.

    This is the hardest requirement and the one nothing in the current model can
    express: an interest must change how long you stay, not only where you go. The
    Cour Carree is 33 minutes for an architecture lover and 8 for a dark-history
    walker; Sainte-Chapelle is 66 and 15.

    A single per-place number cannot produce both, so a visit time that ignores the
    visitor is a visit time that has failed.
    """
    from src.tour.visit_time import visit_seconds

    place = _place("cour-carree", outside_min=8, inside_s=2400)
    snapshot = _corpus(place, beat_lens="historic_arch")

    camille = visit_seconds(place, frozenset({"historic_arch"}), snapshot)
    theo = visit_seconds(place, frozenset({"dark_history"}), snapshot)

    assert camille > theo, (
        f"the same place cost both visitors the same time ({camille}s): the "
        f"declared interest is not reaching the clock, so the lens is decoration."
    )
    # Not merely different — MATERIALLY different. One goes in; the other does not.
    assert camille == 2400
    assert theo == 8 * 60


def test_a_place_with_no_interior_never_exceeds_its_outside_capacity():
    """A street, a bridge or a square has nothing to enter, whatever the interest.

    Pont Neuf is a destination rather than a connection — but it is still 12
    minutes standing on a bridge, not an interior visit. `visit_seconds_inside`
    is None for these, and no amount of matching interest may invent time inside a
    place that has no inside.
    """
    from src.tour.visit_time import visit_seconds

    bridge = _place("pont-neuf", outside_min=12, inside_s=None)
    snapshot = _corpus(bridge, beat_lens="historic_arch")

    for interest in (
        frozenset({"historic_arch"}),  # direct hit
        frozenset({"dark_history"}),  # miss
        frozenset(),  # declared nothing
    ):
        assert visit_seconds(bridge, interest, snapshot) == 12 * 60


def test_the_party_ceiling_clamps_even_a_perfect_interest_match():
    """A family's rule: six minutes is the CEILING here, not the floor.

    For a family with a five-year-old, a 38-minute stop is a disaster. The other
    two terms are both maximising, so without an explicit ceiling nothing in the
    model can shorten a stop for that family. The flat tier cap was protecting them
    by accident.
    """
    from src.tour.visit_time import visit_seconds

    cathedral = _place("big-interior", outside_min=10, inside_s=2700)
    snapshot = _corpus(cathedral, beat_lens="historic_arch")

    # Without the ceiling, a direct interest hit unlocks the whole 45 minutes.
    assert visit_seconds(cathedral, frozenset({"historic_arch"}), snapshot) == 2700

    # With it, six minutes means six minutes.
    clamped = visit_seconds(
        cathedral,
        frozenset({"historic_arch"}),
        snapshot,
        party_ceiling_seconds=6 * 60,
    )
    assert clamped == 360, (
        f"a party ceiling of six minutes returned {clamped}s. The ceiling is the "
        f"only term that can SHORTEN a stop; the other two both maximise."
    )


def test_declaring_no_interest_does_not_hand_over_the_maximum_interior():
    """Someone who chose nothing is not someone who chose everything.

    This is the one deliberate divergence from `_lens_adjacency`, which scores an
    empty interest as a uniform 1.0 so that nobody is penalised for not choosing.
    That is right for SELECTION and wrong for DWELL: handing a visitor who declared
    nothing the maximum interior at every place is what puts a family with a
    five-year-old inside a 45-minute cathedral. Same classifier, two questions,
    two answers — and a family's stated interest may be "stories", which may not
    resolve to a lens at all.
    """
    from src.tour.visit_time import visit_seconds

    cathedral = _place("big-interior", outside_min=10, inside_s=2700)
    snapshot = _corpus(cathedral, beat_lens="historic_arch")

    declared_nothing = visit_seconds(cathedral, frozenset(), snapshot)
    direct_hit = visit_seconds(cathedral, frozenset({"historic_arch"}), snapshot)

    assert declared_nothing < direct_hit
    # 60% of the gap between outside and inside, mirroring LENS_ADJACENCY_ONE_HOP
    # rather than inventing a second scale.
    assert declared_nothing == round(600 + (2700 - 600) * 0.6)


def test_an_interior_shorter_than_the_forecourt_never_shrinks_the_stop():
    """A badly priced pair must not make a stop shorter than its outside number.

    The capacity pass is structurally guarded against this
    (`tests/test_poi_visit_duration.py`), but the guard is on the DATA file and
    a POI can also reach selection from the API, where nothing checks it. Falling
    back to the outside number is the safe direction: it never invents time.
    """
    from src.tour.visit_time import visit_seconds

    odd = _place("badly-priced", outside_min=30, inside_s=600)
    snapshot = _corpus(odd, beat_lens="historic_arch")

    assert visit_seconds(odd, frozenset({"historic_arch"}), snapshot) == 30 * 60


@pytest.mark.parametrize("ceiling", [0, 1, 59])
def test_a_ceiling_below_a_minute_is_still_honoured_by_this_function(ceiling: int):
    """`visit_seconds` clamps to whatever it is given; the FLOOR lives elsewhere.

    Generation floors a stop at `max(planned_visit, planned_audio)`, so a ceiling
    below a stop's narration is overridden there and the tour never truncates
    speech. That is a product decision — Release 1 never cuts narration short —
    and the API is what refuses a ceiling under five minutes. This function stays
    a pure `min`, so the two responsibilities cannot drift into each other.
    """
    from src.tour.visit_time import visit_seconds

    place = _place("anywhere", outside_min=20, inside_s=2400)
    snapshot = _corpus(place, beat_lens="historic_arch")
    assert (
        visit_seconds(
            place,
            frozenset({"historic_arch"}),
            snapshot,
            party_ceiling_seconds=ceiling,
        )
        == ceiling
    )


def test_a_party_ceiling_under_five_minutes_is_refused_not_silently_dropped():
    """A ceiling the tour could not honour must be a refusal, not a promise.

    Generation floors every stop at its own narration, and a stop may speak up to
    MAX_DWELL_AUDIO_SECONDS = 270 s. So a ceiling below 300 s could be overridden
    on any beat-rich stop, and accepting one would be promising a bound the tour
    then breaks. Release 1 never truncates narration; trimming speech to fit a
    shorter ceiling is Release 2.

    Enforced on BOTH request models and on the contract. Neither request model
    declares `model_config`, so both inherit pydantic's default `extra="ignore"` —
    an undeclared field would be silently DROPPED and the caller would receive a
    tour with no ceiling and no error explaining why.
    """
    import pytest as _pytest
    from pydantic import ValidationError

    from src.api.models.trips import TripGenerateRequest, TripPreviewRequest
    from src.tour.contract import TourInput

    with _pytest.raises(ValidationError):
        TourInput(
            start=PARIS, duration_min=120, city_slug="paris", max_stop_minutes=2
        )

    with _pytest.raises(ValidationError):
        TripPreviewRequest(center_lat=48.8566, center_lng=2.3522, max_stop_minutes=2)

    with _pytest.raises(ValidationError):
        TripGenerateRequest(
            profile_id="p",
            center_lat=48.8566,
            center_lng=2.3522,
            start_date="2026-08-06",
            max_stop_minutes=2,
        )

    # Five minutes is accepted, and carried rather than dropped.
    assert (
        TripPreviewRequest(
            center_lat=48.8566, center_lng=2.3522, max_stop_minutes=6
        ).max_stop_minutes
        == 6
    )
    assert (
        TourInput(
            start=PARIS, duration_min=120, city_slug="paris", max_stop_minutes=6
        ).max_stop_minutes
        == 6
    )


# ---------------------------------------------------------------------------
# Behaviour of the shared rules. The guard that no SECOND copy of them exists is
# `make dedup-review`, which reasons about what code is FOR — a pattern scan
# cannot see a duplicate that shares no text, and that is the duplicate that has
# actually cost this project (two tour algorithms, no common lines).
# ---------------------------------------------------------------------------


def test_the_two_clocks_use_one_walk_expression_and_it_reads_provenance():
    """The planned route and the served tour must total legs the same way.

    Until 2026-08-06 `summarise_route` branched on `source == "valhalla"` and the
    served clock in options.py branched on `leg_seconds is not None`. Those are
    different expressions. They agreed numerically ONLY because the routing
    client's fallback and `_transit` both compute the pace-corrected walk of the
    same haversine distance — a coincidence, not a construction.

    This test builds the leg that breaks the coincidence: a routed DURATION with
    a haversine PROVENANCE, which is what a mid-route client degradation
    produces. Under the old pair of expressions the two clocks disagree by 500
    seconds on a single leg.
    """
    from src.tour.contract import TransitSegment
    from src.tour.routing import total_walk_seconds

    honest_fallback = TransitSegment(
        from_poi_id=None,
        to_poi_id="a",
        distance_m=500.0,
        walk_seconds=810,
        leg_seconds=310,  # a stale routed number the client could not vouch for
        source="haversine",
    )
    routed = TransitSegment(
        from_poi_id="a",
        to_poi_id="b",
        distance_m=400.0,
        walk_seconds=648,
        leg_seconds=300,
        source="valhalla",
    )

    # Provenance decides. The fallback leg is worth its haversine walk, not the
    # routed number nobody stands behind.
    assert total_walk_seconds([honest_fallback, routed]) == 810 + 300

    # And the expression the served clock would have used disagrees, which is the
    # whole reason there is now only one of them.
    nullness_based = sum(
        (t.leg_seconds if t.leg_seconds is not None else t.walk_seconds)
        for t in (honest_fallback, routed)
    )
    assert nullness_based == 310 + 300
    assert nullness_based != total_walk_seconds([honest_fallback, routed])

    # And no module still spells it. The assertion above proves the shared
    # function is right; this proves everyone calls it. Without this half the
    # test passes while the served clock keeps its own copy — which is exactly
    # the state the engine was in until 2026-08-06.
    import re as _re
    from pathlib import Path as _Path

    engine = _Path(__file__).resolve().parent.parent / "src" / "tour"
    nullness_expression = _re.compile(r"leg_seconds\s+is\s+not\s+None\s+else")
    offenders = [
        path.relative_to(engine.parent.parent).as_posix()
        for path in sorted(engine.rglob("*.py"))
        if path.name != "routing.py" and nullness_expression.search(path.read_text())
    ]
    assert not offenders, (
        f"{offenders} still total a route's walking by asking whether leg_seconds "
        f"is set rather than whether Valhalla produced it. That is a second "
        f"expression of the walk clock, and it agrees with the first only by "
        f"coincidence."
    )


def test_a_stop_is_never_shorter_than_what_it_says_or_what_it_is_worth():
    """`stop_seconds` is the longer of the two, in both directions.

    The floor at narration is why a tour never cuts itself off mid-sentence. The
    floor at the visit is why an architecture lover's twenty minutes at Concorde
    survive four minutes of audio. Neither is a tie-break; both are the rule.
    """
    from src.tour.visit_time import stop_seconds

    assert stop_seconds(1200, 240) == 1200  # twenty minutes at Concorde: silence is fine
    assert stop_seconds(0, 270) == 270  # an unpriced stop still says its piece
    assert stop_seconds(2700, 0) == 2700  # a silent interior is still 45 minutes
    assert stop_seconds(0, 0) == 0
