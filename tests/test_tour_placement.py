"""THE audio placement rule.

ONE definition of "at the stop", on the server, carried on the wire: each stop's piece
plays inside the place's OWN footprint (the corpus radius), the A→B finish takes the
person's own-place radius, and the day's policy says who starts at the footprint's edge
and who (the family) at the first standstill inside it. Free tier — no provider, no graph.

The seam that proves no SECOND definition exists anywhere (server or phone) is
tests/test_session_seams.py::test_the_audio_placement_rule_has_one_definition; the phone's
behavioural half is mobile/test/services/tour_playback_service_test.dart, group "the
footprint is the place". RED by mutation: return a constant from `place_stop` ->
the courtyard/door test goes RED; make `place_day` ignore the party -> the family test
goes RED; drop `triggers` from the adapter -> the wire test goes RED.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.api.crud.trips import route_script_to_stops
from src.api.models.trips import GeneratedStop, SessionPlan
from src.tour import contingency, placement
from src.tour.contract import END_B_SENTINEL_PREFIX, POI, BeatRef, Route, ScriptPOI, TourInput
from src.tour.placement import (
    FOOTPRINT_DEFAULT_M,
    OWN_PLACE_RADIUS_M,
    PlacementPolicy,
    StopTrigger,
    place_day,
    place_stop,
    place_stops,
)


def _poi(pid: str, radius: float | None = None) -> POI:
    return POI(
        id=pid, name=pid, tier=3, poi_role="spine", lat=48.85, lng=2.35, trigger_radius=radius
    )


def _route(*pois: POI) -> Route:
    return Route(pois=pois, transits=(), total_walk_distance_m=0.0, total_walk_seconds=0)


def _input(party: str | None) -> TourInput:
    return TourInput(start=(48.85, 2.35), duration_min=60, city_slug="paris", party=party)


def test_a_place_is_at_its_own_footprint_never_a_one_size_circle():
    """The courtyard is 140 m across and the doorway is 10: two places, two radii, read
    off the corpus — the one circle the phone drew for both is the defect."""
    courtyard = place_stop(_poi("courtyard", 140.0))
    door = place_stop(_poi("door", 10.0))
    unmeasured = place_stop(_poi("plaque"))
    assert courtyard.radius_m == 140.0 and courtyard.kind == "circle"
    assert door.radius_m == 10.0
    # A record never measured falls to the door-sized default — a floor, not a rule.
    assert unmeasured.radius_m == FOOTPRINT_DEFAULT_M == 10.0
    assert courtyard != door


def test_the_finish_sentinel_takes_the_own_place_radius_and_it_is_defined_once():
    """An A→B day's finish is a waypoint at the person's named end, not a place: it
    takes the own-place radius (the finish is within sight of the named end, never a
    dot near it), and that radius has ONE definition, which the
    contingency set reads rather than restates."""
    sentinel = _poi(f"{END_B_SENTINEL_PREFIX}48.85_2.35")
    assert place_stop(sentinel).radius_m == OWN_PLACE_RADIUS_M == 60.0
    assert contingency.OWN_PLACE_RADIUS_M is placement.OWN_PLACE_RADIUS_M


def test_place_stops_places_every_stop_of_the_route_once():
    route = _route(_poi("a", 140.0), _poi("b"), _poi("c", 25.0))
    placed = place_stops(route)
    assert list(placed) == ["a", "b", "c"]
    assert [t.radius_m for t in placed.values()] == [140.0, FOOTPRINT_DEFAULT_M, 25.0]
    assert all(isinstance(t, StopTrigger) for t in placed.values())


def test_the_family_day_starts_at_the_first_standstill_everyone_else_at_the_edge():
    """The piece ARMS at the footprint's edge for everyone; it STARTS at arrival
    for every party but the family, whose piece waits for the first standstill inside
    the footprint."""
    assert place_day(_input("family")).start_at == "standstill"
    for party in (None, "solo", "couple", "take_it_easy", "with_luggage"):
        assert place_day(_input(party)).start_at == "arrival", party
    assert place_day(_input(None)).own_place_m == OWN_PLACE_RADIUS_M
    assert isinstance(place_day(_input("family")), PlacementPolicy)


def test_the_trigger_rides_the_stop_record_and_the_wire():
    """The adapter carries the placed geometry exactly as it carries the clocks (a map
    keyed by poi id from THE one rule), the stop dict holds it as plain data the item
    stores, and the wire model validates it back into the rule's own shape."""
    sp = ScriptPOI(id="poi-1", name="Courtyard", tier=3, lat=48.85, lng=2.35, beat_ids=("b1",))
    beats = {"b1": BeatRef(id="b1", poi_id="poi-1")}
    placed = place_stops(_route(_poi("poi-1", 140.0)))

    with_geometry = route_script_to_stops([sp], beats, {}, triggers=placed)
    assert with_geometry[0]["trigger"] == {
        "kind": "circle", "radius_m": 140.0, "queue_seconds": 0,
        "door": False, "outside_seconds": 0,
    }
    without = route_script_to_stops([sp], beats, {})
    assert without[0]["trigger"] is None

    base = dict(
        sort_order=1, poi_id="poi-1", poi_name="Courtyard", lat=48.85, lng=2.35,
        duration_min=5, importance_tier=3, start_time="10:00",
    )
    stop = GeneratedStop(**base, trigger=with_geometry[0]["trigger"])
    assert stop.trigger == StopTrigger(radius_m=140.0)
    assert stop.trigger.queue_seconds == 0
    assert GeneratedStop.model_validate(stop.model_dump()).trigger == stop.trigger
    # A legacy item: no geometry, nothing auto-plays there (the phone's half).
    assert GeneratedStop(**base).trigger is None

    plan = SessionPlan(
        trip_id="t", plan_version=1, stops=[stop], retime_tolerance_seconds=180,
        placement=place_day(_input("family")),
    )
    dumped = plan.model_dump()
    # RE-DERIVED: the policy grew the sentence cap and the resume rule that were
    # still living as phone branches — the wire now carries them.
    assert dumped["placement"] == {
        "start_at": "standstill", "own_place_m": 60.0, "queue_piece": "tap",
        "sentence_cap_s": 0.0, "interruption_resume": "auto",
    }
    assert SessionPlan.model_validate(dumped).placement == plan.placement
    bare = SessionPlan(trip_id="t", plan_version=1, stops=[], retime_tolerance_seconds=180)
    assert bare.placement is None


def test_a_priced_queue_rides_the_stop_s_trigger():
    """The seconds of line the planner priced at this stop's
    arrival hour ride its trigger, read off THE one map (`Route.planned_queue_seconds`,
    priced at selection's one site) — never a second reading of the queue fields. A stop
    the map does not price carries 0: no line, no queue piece."""
    route = _route(_poi("chapelle", 30.0), _poi("bridge", 80.0)).model_copy(
        update={"planned_queue_seconds": {"chapelle": 28 * 60}}
    )
    placed = place_stops(route)
    assert placed["chapelle"].queue_seconds == 28 * 60
    assert placed["bridge"].queue_seconds == 0
    assert place_stop(_poi("x", 10.0)).queue_seconds == 0


def test_the_queue_piece_policy_follows_the_party_and_the_wall():
    """Auto at the first standstill inside the footprint for a solo walker; a screen
    offer and a TAP for the couple and the family, whose still phone is a conversation or
    a sandpit; under `wall` no line is priced, so no piece — `none` by construction."""
    for party in (None, "solo", "take_it_easy", "with_luggage"):
        assert place_day(_input(party)).queue_piece == "auto", party
    for party in ("couple", "family"):
        assert place_day(_input(party)).queue_piece == "tap", party
    wall = TourInput(start=(48.85, 2.35), duration_min=60, city_slug="paris", end_hardness="wall")
    assert place_day(wall).queue_piece == "none"
    assert place_day(wall.model_copy(update={"party": "couple"})).queue_piece == "none"


def test_the_day_s_policy_carries_the_cap_and_the_resume_rule_not_the_phone():
    """Per-day policy fields ride the session, decided on the server from the party and
    the hardness, never a branch the phone invents. The built PlacementPolicy once carried
    only start_at / own_place_m / queue_piece, so the sentence cap and the resume rule
    stayed phone branches on `party`. The policy block now carries both: the cap (8 s;
    family 0, cut at once; a `wall` day 5) and how an interrupted piece resumes (by
    itself; the COUPLE by their tap, since a couple restarts when the conversation
    pauses)."""
    solo = place_day(_input(None))
    assert (solo.sentence_cap_s, solo.interruption_resume) == (8.0, "auto")
    family = place_day(_input("family"))
    assert (family.sentence_cap_s, family.interruption_resume) == (0.0, "auto")
    wall = TourInput(start=(48.85, 2.35), duration_min=60, city_slug="paris", end_hardness="wall")
    assert place_day(wall).sentence_cap_s == 5.0
    assert place_day(_input("couple")).interruption_resume == "tap"


def test_a_door_is_the_plan_s_own_goes_inside_and_an_arcade_is_a_roof():
    """Threshold silence: a stop is a DOOR when the
    plan's own visit goes inside (`Route.visit_goes_inside`, the promise shape) and the
    place is not a kind you are IN rather than ENTER — an arcade, a passage, a courtyard
    gate, a garden, a park, a square, a bridge, a street, a market are OUTSIDE, by
    category never by GPS loss. The placed OUTSIDE seconds ride beside it: the
    only threshold the phone can see under a roof."""
    chapelle = _poi("chapelle", 30.0).model_copy(update={"place_category": "church"})
    arcade = _poi("arcade", 60.0).model_copy(update={"place_category": "arcade"})
    square = _poi("square", 70.0).model_copy(update={"place_category": "square"})
    outside_only = _poi("facade", 25.0).model_copy(update={"place_category": "museum"})
    route = _route(chapelle, arcade, square, outside_only).model_copy(
        update={
            "visit_goes_inside": {
                "chapelle": True, "arcade": True, "square": False, "facade": False,
            },
            "planned_outside_seconds": {
                "chapelle": 15 * 60, "arcade": 10 * 60, "square": 20 * 60, "facade": 8 * 60,
            },
        }
    )
    placed = place_stops(route)
    assert placed["chapelle"].door is True and placed["chapelle"].outside_seconds == 15 * 60
    assert placed["arcade"].door is False, "a roof is not a door (R3, 11/11)"
    assert placed["arcade"].outside_seconds == 10 * 60
    assert placed["square"].door is False
    assert placed["facade"].door is False, "an outside-only visit never reaches the door"
    # An unpriced route: no door anywhere, no outside seconds — nothing is cut.
    bare = place_stops(_route(chapelle))
    assert bare["chapelle"].door is False and bare["chapelle"].outside_seconds == 0


def test_a_trigger_is_a_closed_shape_this_phase():
    """`kind` is reserved for the CARRIED line/polygon row: a circle needs a
    positive radius, nothing else rides on it, and no other kind exists yet."""
    with pytest.raises(ValidationError):
        StopTrigger(radius_m=0)
    with pytest.raises(ValidationError):
        StopTrigger(radius_m=10, kind="line")
    with pytest.raises(ValidationError):
        StopTrigger(radius_m=10, polygon=[])
    with pytest.raises(ValidationError):
        POI(id="x", name="x", tier=3, poi_role="spine", lat=48.85, lng=2.35, trigger_radius=0)


# ---------------------------------------------------------------------------
# THE CHAPTERS of a marquee stop (segments only where a person placed the
# coordinates, marquee anchors only — Notre-Dame first — outdoor auto at a
# standstill, interior by tap, couple/family all taps, never re-triggered). The
# corpus carries each anchor on the POI (`POI.anchors`, reviewed by hand, with its
# argument); a stop's STORY is cut at those anchors by THE one rule: a beat whose
# `sub_location` an anchor names is told AT that anchor, as the anchor's own piece;
# everything else stays the arrival story. Glue stays with the story. A stop with no
# anchors is uncut.
# ---------------------------------------------------------------------------

from src.tour.contract import Anchor, Script, Sentence, ValidationReport
from src.tour.placement import (
    StopSegment,
    anchor_of,
    place_anchors,
    place_segments,
)

_WEST = Anchor(
    label="The west front", sub_locations=("facade", "central-portal", "kilometre-zero"),
    lat=48.85325, lng=2.34875, radius_m=45.0, indoor=False, basis="the parvis",
)
_INSIDE = Anchor(
    label="Inside", sub_locations=("interior-nave", "choir"),
    lat=48.853, lng=2.34975, radius_m=60.0, indoor=True, basis="under the roof",
)


def _nd_script(sentences, *, seated):
    import datetime as _dt

    return Script(
        city_slug="paris",
        generated_at=_dt.datetime(2026, 8, 22, tzinfo=_dt.UTC).isoformat(),
        inputs=_input(None),
        total_audio_seconds=0, total_walking_seconds=0, total_walk_distance_m=0,
        total_planned_seconds=0,
        selected_pois=(
            ScriptPOI(id="nd", name="Notre-Dame Cathedral", tier=5, lat=48.852966,
                      lng=2.349902, beat_ids=seated),
        ),
        lens_coverage={}, script=tuple(sentences), validation=ValidationReport(),
    )


def test_an_anchor_is_a_reviewed_place_on_the_poi_with_its_argument():
    """Only human-placed coordinates; each anchor names the sub-locations it
    stands for, its own radius, whether it is under a roof, and the sentence that
    argues it. A POI without the data has no anchors — the safe default."""
    assert _WEST.radius_m == 45.0 and _WEST.indoor is False and _WEST.basis
    poi = _poi("nd", 100.0).model_copy(update={"anchors": (_WEST, _INSIDE)})
    assert _poi("x").anchors == ()
    assert place_anchors(_route(poi, _poi("x"))) == {"nd": (_WEST, _INSIDE)}
    with pytest.raises(ValidationError):
        Anchor(label="x", sub_locations=(), lat=48.85, lng=2.35, radius_m=0)
    with pytest.raises(ValidationError):
        Anchor(label="x", sub_locations=("a",), lat=48.85, lng=2.35, radius_m=10, kind="line")


def test_the_story_is_cut_at_the_reviewed_anchors_and_only_there():
    """The beat tagged `facade` is told at the west front, the beat tagged `interior-nave`
    inside; the untagged beats and the cold-open stay the arrival story, in order. One
    chapter per anchor, sentences in script order; a stop whose POI carries no anchors is
    uncut (its narration is the whole stationary text).

    RE-DERIVED, reason written: this test used to assert that the close ALSO stayed in
    the arrival story of a chaptered stop — `"… The crypt keeps a Roman pillar. And
    that's Notre-Dame."`. That shape is the defect: the goodbye was then said on arrival
    and the chapter spoke after it, so a listener stopped listening too early. The
    goodbye is the last thing said at the last place. The invariant this test exists
    for — the story loses exactly the chapters' sentences and keeps the rest IN ORDER —
    is unchanged and still asserted; the close clause is re-derived to the new rule and
    the uncut case below still pins the uncut shape."""
    beats = {
        "b-open": BeatRef(id="b-open", poi_id="nd"),
        "b-facade": BeatRef(id="b-facade", poi_id="nd", sub_location="facade"),
        "b-portal": BeatRef(id="b-portal", poi_id="nd", sub_location="central-portal"),
        "b-nave": BeatRef(id="b-nave", poi_id="nd", sub_location="interior-nave"),
        "b-fire": BeatRef(id="b-fire", poi_id="nd", sub_location=None),
        "b-crypt": BeatRef(id="b-crypt", poi_id="nd", sub_location="crypt"),  # no anchor
    }
    sentences = [
        Sentence(text="Settle in.", source_id="GLUE_PACING", source_type="glue", stop_idx=0),
        Sentence(text="Two towers, one island.", source_id="b-open", source_type="beat",
                 stop_idx=0),
        Sentence(text="The facade is lighter than it looks.", source_id="b-facade",
                 source_type="beat", stop_idx=0),
        Sentence(text="Over the door, the Last Judgement.", source_id="b-portal",
                 source_type="beat", stop_idx=0),
        Sentence(text="Inside, the nave goes dark then gold.", source_id="b-nave",
                 source_type="beat", stop_idx=0),
        Sentence(text="In 2019 the roof burned.", source_id="b-fire", source_type="beat",
                 stop_idx=0),
        Sentence(text="The crypt keeps a Roman pillar.", source_id="b-crypt",
                 source_type="beat", stop_idx=0),
        Sentence(text="And that's Notre-Dame.", source_id="GLUE_CLOSING",
                 source_type="glue", stop_idx=0),
    ]
    seated = tuple(beats)
    script = _nd_script(sentences, seated=seated)
    resolve = anchor_of(script.selected_pois, beats, {"nd": (_WEST, _INSIDE)})
    segments = place_segments(script, resolve)
    assert [s.label for s in segments[0]] == ["The west front", "Inside"]
    west, inside = segments[0]
    assert west.narration == (
        "The facade is lighter than it looks. Over the door, the Last Judgement."
    )
    assert inside.narration == "Inside, the nave goes dark then gold."
    assert (west.lat, west.lng, west.radius_m, west.indoor) == (48.85325, 2.34875, 45.0, False)
    assert inside.indoor is True
    assert all(isinstance(s, StopSegment) for s in segments[0])
    # The adapter: the story LOSES the chapters' sentences and keeps the rest in order.
    sp = script.selected_pois
    stops = route_script_to_stops(sp, beats, {}, script=script, anchors={"nd": (_WEST, _INSIDE)})
    assert stops[0]["narration"] == (
        "Settle in. Two towers, one island. In 2019 the roof burned. "
        "The crypt keeps a Roman pillar."
    ), "a chaptered stop's story ends on its last story sentence, never on its goodbye"
    # The goodbye is not lost — it travels as the stop's own line, and the phone plays it
    # when the last chapter has been told.
    assert stops[0]["close_text"] == "And that's Notre-Dame."
    assert [s["label"] for s in stops[0]["segments"]] == ["The west front", "Inside"]
    assert stops[0]["segments"][0]["narration"] == west.narration
    # No anchors: uncut, and no chapter list at all (None, never an empty card).
    uncut = route_script_to_stops(sp, beats, {}, script=script)
    assert uncut[0]["segments"] is None
    assert "The facade is lighter than it looks." in uncut[0]["narration"]
    assert place_segments(script, anchor_of(sp, beats, {})) == {}


def test_a_chapter_rides_the_wire_with_its_own_file_fields():
    """`GeneratedStop.segments` validates the adapter's dicts back into the rule's shape;
    the file fields are null until the voicing pass fills them (the leg-piece precedent);
    a legacy item has none."""
    seg = StopSegment(label="The west front", lat=48.85325, lng=2.34875, radius_m=45.0,
                      indoor=False, narration="The facade is lighter than it looks.")
    base = dict(
        sort_order=1, poi_id="nd", poi_name="Notre-Dame Cathedral", lat=48.852966,
        lng=2.349902, duration_min=5, importance_tier=5, start_time="10:00",
    )
    stop = GeneratedStop(**base, segments=[seg.model_dump()])
    assert stop.segments[0] == seg and stop.segments[0].audio_url is None
    assert GeneratedStop.model_validate(stop.model_dump()).segments == [seg]
    assert GeneratedStop(**base).segments == []
    voiced = seg.model_copy(update={"audio_url": "file:///seg.mp3", "audio_duration_sec": 9.5})
    assert voiced.audio_url == "file:///seg.mp3"
    with pytest.raises(ValidationError):
        StopSegment(label="x", lat=1, lng=1, radius_m=0, indoor=False, narration="t")
