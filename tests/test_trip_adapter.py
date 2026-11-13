"""Hermetic unit tests for route_script_to_stops (the trip adapter).

Pure-function tests: hand-built engine contract objects, NO database, NO engine
run, NO corpus.
"""

from __future__ import annotations

from src.api.crud.trips import route_script_to_stops
from src.tour.contract import BeatRef, Script, ScriptPOI, Sentence, TourInput, ValidationReport
from src.tour.options import dominant_lens


def _poi(pid: str, name: str, *, tier: int, beat_ids: tuple[str, ...], dwell: int) -> ScriptPOI:
    return ScriptPOI(
        id=pid, name=name, tier=tier, lat=48.85, lng=2.35, dwell_seconds=dwell, beat_ids=beat_ids
    )


def _beat(bid: str, poi_id: str, lenses: tuple[str, ...]) -> BeatRef:
    return BeatRef(id=bid, poi_id=poi_id, lenses=lenses)


def test_stops_preserve_route_order_and_sort_order():
    pois = [
        _poi("p1", "Eiffel Tower", tier=5, beat_ids=("b1",), dwell=600),
        _poi("p2", "Notre-Dame", tier=4, beat_ids=("b2",), dwell=300),
    ]
    stops = route_script_to_stops(pois, {}, {})
    assert [s["poi_id"] for s in stops] == ["p1", "p2"]
    assert [s["sort_order"] for s in stops] == [1, 2]


def test_all_beats_kept_with_primary():
    beats = {
        "b1": _beat("b1", "p1", ("dark_history",)),
        "b2": _beat("b2", "p1", ("dark_history",)),
        "b3": _beat("b3", "p1", ("architecture",)),
    }
    pois = [_poi("p1", "Louvre", tier=5, beat_ids=("b1", "b2", "b3"), dwell=600)]
    stops = route_script_to_stops(pois, beats, {})
    assert stops[0]["beat_ids"] == ["b1", "b2", "b3"]
    assert stops[0]["primary_beat_id"] == "b1"
    assert stops[0]["dwell_seconds"] == 600  # raw engine dwell passes through


def test_dominant_lens_is_most_common():
    beats = {
        "b1": _beat("b1", "p1", ("dark_history",)),
        "b2": _beat("b2", "p1", ("dark_history",)),
        "b3": _beat("b3", "p1", ("architecture",)),
    }
    pois = [_poi("p1", "Louvre", tier=5, beat_ids=("b1", "b2", "b3"), dwell=600)]
    stops = route_script_to_stops(pois, beats, {})
    assert stops[0]["lens_name"] == "dark_history"


def test_dominant_lens_tie_breaks_by_name():
    beats = {
        "b1": _beat("b1", "p1", ("zeta",)),
        "b2": _beat("b2", "p1", ("alpha",)),
    }
    pois = [_poi("p1", "Square", tier=3, beat_ids=("b1", "b2"), dwell=120)]
    stops = route_script_to_stops(pois, beats, {})
    assert stops[0]["lens_name"] == "alpha"  # tie -> lexicographically smallest


def test_no_lensed_beat_gives_none_lens():
    beats = {"b1": _beat("b1", "p1", ())}
    pois = [_poi("p1", "Plaque", tier=2, beat_ids=("b1",), dwell=60)]
    stops = route_script_to_stops(pois, beats, {})
    assert stops[0]["lens_name"] is None


def test_the_adapter_never_spells_a_clock_it_writes_the_one_it_is_handed():
    """The session clock seam: the server has ONE
    re-timing expression — `src.tour.contingency.stop_clocks`, walks and priced
    visits — and the wire's per-stop `start_time` is a view of it. This adapter
    used to run a second, walk-less clock (dwell only) that the phone showed as
    arrival times; it now writes exactly the HH:MM the caller hands it, per stop,
    and "" for a stop nobody clocked. `duration_min` is still its own reading of
    the stop's dwell."""
    pois = [
        _poi("p1", "A", tier=5, beat_ids=("b1",), dwell=3000),  # 50 min
        _poi("p2", "B", tier=4, beat_ids=("b2",), dwell=1800),  # 30 min
    ]
    stops = route_script_to_stops(pois, {}, {"p1": "09:00", "p2": "10:04"})
    assert stops[0]["start_time"] == "09:00"
    assert stops[0]["duration_min"] == 50
    # 09:00 + 50 min dwell would be 09:50 on the old dwell-only clock; the walk
    # in between is the caller's expression's business, and it said 10:04.
    assert stops[1]["start_time"] == "10:04"
    assert stops[1]["duration_min"] == 30
    assert route_script_to_stops(pois, {}, {})[1]["start_time"] == ""


def test_empty_selection_yields_no_stops():
    assert route_script_to_stops([], {}, {}) == []


def _script(pois: list[ScriptPOI], sentences: list[Sentence]) -> Script:
    return Script(
        city_slug="paris",
        generated_at="2026-06-13T00:00:00+00:00",
        inputs=TourInput(start=(48.85, 2.35), duration_min=60, city_slug="paris"),
        total_audio_seconds=0,
        total_walking_seconds=0,
        total_walk_distance_m=0,
        total_planned_seconds=0,
        selected_pois=tuple(pois),
        lens_coverage={},
        script=tuple(sentences),
        validation=ValidationReport(),
    )


def test_narration_attached_per_stop_when_script_passed():
    pois = [
        _poi("p1", "Eiffel Tower", tier=5, beat_ids=("b1",), dwell=600),
        _poi("p2", "Notre-Dame", tier=4, beat_ids=("b2",), dwell=300),
    ]
    script = _script(
        pois,
        [
            Sentence(text="Settle in.", source_id="GLUE_PACING", source_type="glue", stop_idx=0),
            Sentence(
                text="The tower opened in 1889.", source_id="b1", source_type="beat", stop_idx=0
            ),
            Sentence(
                text="The cathedral began in 1160.", source_id="b2", source_type="beat", stop_idx=1
            ),
        ],
    )
    stops = route_script_to_stops(pois, {}, {}, script=script)
    assert stops[0]["narration"] == "Settle in. The tower opened in 1889."
    assert stops[1]["narration"] == "The cathedral began in 1160."


def test_the_leg_line_rides_the_stop_as_its_own_leg_narration():
    """The stop's walk-concurrent sentences
    (the nav line into it) are its LEG piece — `leg_narration`, voiced as its own file and
    played on the leg — and `narration` is the STORY alone, opening on the stop's own first
    story sentence. Split by THE one rule (`generation.is_walk_concurrent`); a stop whose leg
    carries nothing has no leg piece (None — an empty card would imply content)."""
    pois = [
        _poi("p1", "Eiffel Tower", tier=5, beat_ids=("b1",), dwell=600),
        _poi("p2", "Notre-Dame", tier=4, beat_ids=("b2",), dwell=300),
    ]
    script = _script(
        pois,
        [
            Sentence(text="Settle in.", source_id="GLUE_PACING", source_type="glue", stop_idx=0),
            Sentence(text="The tower opened in 1889.", source_id="b1", source_type="beat",
                     stop_idx=0),
            Sentence(text="Walk southeast along the river for about ten minutes.",
                     source_id="GLUE_NAV", source_type="glue", stop_idx=1),
            Sentence(text="The cathedral began in 1160.", source_id="b2", source_type="beat",
                     stop_idx=1),
            Sentence(text="And that's Notre-Dame.", source_id="GLUE_CLOSING", source_type="glue",
                     stop_idx=1),
        ],
    )
    stops = route_script_to_stops(pois, {}, {}, script=script)
    assert stops[0]["narration"] == "Settle in. The tower opened in 1889."
    assert stops[0]["leg_narration"] is None
    assert stops[1]["narration"] == "The cathedral began in 1160. And that's Notre-Dame."
    assert stops[1]["leg_narration"] == "Walk southeast along the river for about ten minutes."
    assert route_script_to_stops(pois, {}, {})[1]["leg_narration"] is None


def test_narration_empty_when_no_script():
    pois = [_poi("p1", "A", tier=5, beat_ids=("b1",), dwell=600)]
    stops = route_script_to_stops(pois, {}, {})
    assert stops[0]["narration"] == ""


def test_stops_carry_extra_beat_ids_when_provided():
    pois = [
        _poi("p1", "Eiffel Tower", tier=5, beat_ids=("b1", "b2"), dwell=600),
        _poi("p2", "Notre-Dame", tier=4, beat_ids=("b3",), dwell=300),
    ]
    stops = route_script_to_stops(pois, {}, {}, extra_by_poi={"p1": ("x1", "x2"), "p2": ()})
    assert stops[0]["extra_beat_ids"] == ["x1", "x2"]
    assert stops[1]["extra_beat_ids"] == []


def test_stops_extra_beat_ids_default_empty_without_map():
    pois = [_poi("p1", "Eiffel Tower", tier=5, beat_ids=("b1",), dwell=600)]
    stops = route_script_to_stops(pois, {}, {})
    assert stops[0]["extra_beat_ids"] == []


def test_a_leg_line_records_the_stop_it_was_written_from():
    """A leg line names both its ends — "From X, make your way on to Y" — so it is
    true of ONE pair and no other. A replan keeps the day's stops and re-orders them, and
    without knowing which stop a line was written from there is no way to tell a line that
    still fits from one now describing a walk nobody is taking.

    The `thread_lines` precedent, applied to the leg: the adapter already walks the stops
    in route order, so the predecessor is the previous ScriptPOI. The first stop has none.
    """
    pois = [
        _poi("p1", "Eiffel Tower", tier=5, beat_ids=("b1",), dwell=600),
        _poi("p2", "Notre-Dame", tier=4, beat_ids=("b2",), dwell=300),
        _poi("p3", "Pont Neuf", tier=4, beat_ids=("b3",), dwell=300),
    ]
    script = _script(
        pois,
        [
            Sentence(text="Settle in.", source_id="GLUE_PACING", source_type="glue", stop_idx=0),
            Sentence(text="From the Eiffel Tower, make your way on to Notre-Dame.",
                     source_id="GLUE_NAV", source_type="glue", stop_idx=1),
            Sentence(text="The cathedral began in 1160.", source_id="b2", source_type="beat",
                     stop_idx=1),
        ],
    )
    stops = route_script_to_stops(pois, {}, {}, script=script)
    assert stops[0]["leg_from_poi_id"] is None, "the first stop is walked to from nowhere"
    assert stops[1]["leg_from_poi_id"] == "p1"
    assert stops[2]["leg_from_poi_id"] == "p2", (
        "recorded for every stop, not only the ones whose leg happens to carry words — "
        "a stop that gains a line later still knows which walk it describes"
    )


def test_the_label_answers_the_request_when_any_voiced_beat_does():
    """The measured fault: an architecture day's Notre-Dame voiced two architecture
    beats yet wore `hidden_history`, because the label was the plain majority of
    whatever else rode along. When the person asked for subjects, the label is the
    most common ASKED-family lens among the voiced beats — the stop's role in the
    person's day — and only a stop with nothing on the request keeps the plain majority.
    No request: unchanged, ties alphabetical as ever."""
    beats = {
        "a1": BeatRef(id="a1", poi_id="p1", lenses=("historic_arch",)),
        "a2": BeatRef(id="a2", poi_id="p1", lenses=("historic_arch",)),
        "h1": BeatRef(id="h1", poi_id="p1", lenses=("hidden_history",)),
        "h2": BeatRef(id="h2", poi_id="p1", lenses=("hidden_history",)),
        "h3": BeatRef(id="h3", poi_id="p1", lenses=("dark_history",)),
    }
    ids = list(beats)
    assert dominant_lens(ids, beats) == "hidden_history"
    assert dominant_lens(
        ids, beats, prefer=frozenset({"historic_arch", "arch_design"})
    ) == "historic_arch"
    # Nothing on the request: the honest plain majority stands.
    assert dominant_lens(
        ids, beats, prefer=frozenset({"visual_art"})
    ) == "hidden_history"


def test_the_adapter_threads_the_preferred_family_into_every_stop_label():
    pois = [_poi("p1", "Notre-Dame", tier=5, beat_ids=("a1", "h1", "h2"), dwell=600)]
    beats = {
        "a1": BeatRef(id="a1", poi_id="p1", lenses=("historic_arch",)),
        "h1": BeatRef(id="h1", poi_id="p1", lenses=("hidden_history",)),
        "h2": BeatRef(id="h2", poi_id="p1", lenses=("hidden_history",)),
    }
    plain = route_script_to_stops(pois, beats, {})
    assert plain[0]["lens_name"] == "hidden_history"
    preferred = route_script_to_stops(
        pois, beats, {}, prefer_lenses=frozenset({"historic_arch"})
    )
    assert preferred[0]["lens_name"] == "historic_arch"
