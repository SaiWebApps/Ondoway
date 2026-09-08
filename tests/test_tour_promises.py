"""Phase 3 promise tests — the harness speaks promises (plan S3.1).

One hand per file: S3.6 (the planner's promise assembly) appends its planner
tests here; this file is created by S3.1 with the parser/printer nodes.

Everything here is HERMETIC: TourInput / Route / Promise / PromiseShape are
constructed by hand, the printer is read through capsys, and pin resolution is
exercised against a stubbed lookup — no graph, no network. The live lookup
ladder itself (`_lookup_place`) is `_resolve_start`'s own machinery, already
exercised by every live harness run.

The RED that proved S3.1 was real: `--pin` and `--weather` were unrecognized
arguments (argparse exited 2 before any assertion ran), and the breakdown
table had no shape column, no queue column, no promises line and no
hours-unverified line.
"""

import importlib
import sys
from types import SimpleNamespace

import pytest

from src.tour.contract import (
    POI,
    ClockExclusion,
    Promise,
    PromiseShape,
    Route,
    TourInput,
    TransitSegment,
)


def _tour_build():
    return importlib.import_module("scripts.tour_build")


def _base_input(**overrides) -> TourInput:
    fields = {
        "start": (48.8568, 2.3414),
        "duration_min": 90,
        "city_slug": "paris",
    }
    fields.update(overrides)
    return TourInput(**fields)


def _poi(poi_id: str, name: str, **overrides) -> POI:
    fields = {
        "id": poi_id,
        "name": name,
        "tier": 4,
        "poi_role": "stop",
        "lat": 48.8566,
        "lng": 2.3450,
        "place_category": "monument",
    }
    fields.update(overrides)
    return POI(**fields)


def _legs(pois) -> tuple[TransitSegment, ...]:
    """One 300-second (5-minute) walk INTO each stop — transits leg i is the
    walk into stop i, the same indexing the table prints."""
    legs = []
    prev = None
    for poi in pois:
        legs.append(
            TransitSegment(
                from_poi_id=prev, to_poi_id=poi.id, distance_m=400.0, walk_seconds=300
            )
        )
        prev = poi.id
    return tuple(legs)


def _route(pois, **overrides) -> Route:
    fields = {
        "pois": tuple(pois),
        "transits": _legs(pois),
        "total_walk_distance_m": 400.0 * len(pois),
        "total_walk_seconds": 300 * len(pois),
    }
    fields.update(overrides)
    return Route(**fields)


def _script_for(route) -> SimpleNamespace:
    """The printer's script stand-in (the test_tour_party.py pattern)."""
    return SimpleNamespace(
        selected_pois=[
            SimpleNamespace(dwell_seconds=600, lat=p.lat, lng=p.lng, name=p.name)
            for p in route.pois
        ]
    )


def _stop_line(out: str, name: str) -> str:
    table = out.split("per-stop", 1)[1]
    lines = [ln for ln in table.splitlines() if name in ln]
    assert lines, f"the per-stop table carries no line for {name!r}"
    return lines[0]


#: Map hours (Docs/adr/0006): a door's OpenStreetMap text, as the map carries it.
_DOOR_HOURS = "Mo 09:00-18:00"


# --- parser: pins and weather ------------------------------------------------


def test_one_pin_lands_in_pinned_poi_ids(monkeypatch):
    """One `--pin` resolves through the shared lookup ladder to a corpus POI id
    and lands in TourInput.pinned_poi_ids (design §3.2 — the visitor's pin is
    a decision, and the planner receives it as an id, not a string)."""
    tour_build = _tour_build()
    parser = tour_build._build_arg_parser()
    args = parser.parse_args(
        ["--start", "X", "--duration", "60", "--canned", "--pin", "Sainte-Chapelle"]
    )
    assert args.pin == ["Sainte-Chapelle"]

    seen: list[dict] = []

    def fake_lookup(driver, arg, city_slug, *, want_poi_id=False):
        seen.append({"arg": arg, "want_poi_id": want_poi_id})
        return "poi-chapelle", (48.8554, 2.3450), arg

    monkeypatch.setattr(tour_build, "_lookup_place", fake_lookup)
    ids = tour_build._resolve_pinned_poi_ids(None, parser, args.pin, "paris")
    assert ids == ("poi-chapelle",)
    assert seen and seen[0]["want_poi_id"] is True, (
        "a pin must ASK the ladder for a POI id — without want_poi_id a "
        "coordinate pin can never snap to the place it names"
    )

    inp = _base_input(pinned_poi_ids=ids)
    assert inp.pinned_poi_ids == ("poi-chapelle",)


def test_two_pins_land_in_order(monkeypatch):
    """Repeatable `--pin`s keep their command-line order into pinned_poi_ids."""
    tour_build = _tour_build()
    parser = tour_build._build_arg_parser()
    args = parser.parse_args(
        [
            "--start", "X", "--duration", "60", "--canned",
            "--pin", "Sainte-Chapelle", "--pin", "Conciergerie",
        ]
    )
    assert args.pin == ["Sainte-Chapelle", "Conciergerie"]

    table = {"Sainte-Chapelle": "poi-chapelle", "Conciergerie": "poi-conciergerie"}

    def fake_lookup(driver, arg, city_slug, *, want_poi_id=False):
        return table[arg], (48.8554, 2.3450), arg

    monkeypatch.setattr(tour_build, "_lookup_place", fake_lookup)
    ids = tour_build._resolve_pinned_poi_ids(None, parser, args.pin, "paris")
    assert ids == ("poi-chapelle", "poi-conciergerie")
    assert _base_input(pinned_poi_ids=ids).pinned_poi_ids == ids


def test_bad_pin_text_is_an_argparse_error_naming_it(monkeypatch, capsys):
    """A pin that resolves to nothing is an argparse-level error NAMING the
    pin text — the person who typed it must see which pin failed."""
    tour_build = _tour_build()
    parser = tour_build._build_arg_parser()

    def fake_lookup(driver, arg, city_slug, *, want_poi_id=False):
        return None

    monkeypatch.setattr(tour_build, "_lookup_place", fake_lookup)
    with pytest.raises(SystemExit) as exc:
        tour_build._resolve_pinned_poi_ids(None, parser, ["Atlantis Metro"], "paris")
    assert exc.value.code == 2
    assert "Atlantis Metro" in capsys.readouterr().err


def test_weather_rain_parses_and_lands_on_tour_input():
    """`--weather rain` parses, and the value TourInput carries is the same
    two-word vocabulary the contract speaks (dry | rain)."""
    tour_build = _tour_build()
    parser = tour_build._build_arg_parser()
    args = parser.parse_args(
        ["--start", "X", "--duration", "60", "--canned", "--weather", "rain"]
    )
    assert args.weather == "rain"
    assert _base_input(weather="rain").weather == "rain"

    flagless = parser.parse_args(["--start", "X", "--duration", "60", "--canned"])
    assert flagless.weather is None, "no flag = no signal = today's request"


def test_weather_auto_without_date_is_an_argparse_error(monkeypatch, capsys):
    """`--weather auto` fetches a forecast, and a forecast is for a DAY: with
    no --date the harness refuses at argparse level, before any driver or
    network work (which is also what keeps this test hermetic)."""
    tour_build = _tour_build()
    monkeypatch.setattr(
        sys,
        "argv",
        ["tour_build.py", "--start", "X", "--duration", "60", "--canned",
         "--weather", "auto"],
    )
    with pytest.raises(SystemExit) as exc:
        tour_build.main()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "--weather auto" in err
    assert "--date" in err
    # Mutation-proof: argparse's own "unrecognized arguments" + usage text can
    # satisfy the two asserts above; only the real refusal explains itself.
    assert "forecast" in err


# --- printer: the promises line ----------------------------------------------


def test_promises_line_prints_kind_stop_and_window(capsys):
    """A Route carrying a pinned Promise prints a promises line above the
    table with the promise's kind, stop name and arrive-depart window, from
    the SAME cumulative arithmetic as the table's legs and stop times:
    5m walk + 10m stop + 5m walk lands the pinned stop at 10:20, and its
    shape (0 outside + 2400 inside + 600 queue = 50m) departs it at 11:10."""
    tour_build = _tour_build()
    a = _poi("poi-a", "Conciergerie")
    b = _poi("poi-b", "Sainte-Chapelle", place_category="church")
    route = _route(
        [a, b],
        planned_visit_seconds={"poi-a": 600},
        promises=(
            Promise(
                kind="pinned",
                poi_id="poi-b",
                shape=PromiseShape(
                    outside_seconds=0,
                    inside_seconds=2400,
                    queue_seconds=600,
                    goes_inside=True,
                    closed_today=False,
                ),
            ),
        ),
    )
    tour_build._print_breakdown(
        tour_input=_base_input(start_datetime="2026-08-11T10:00"),
        wall_clock_s=1.0,
        route=route,
        script=_script_for(route),
    )
    out = capsys.readouterr().out
    assert "promises:" in out
    promise_lines = [ln for ln in out.splitlines() if "pinned" in ln]
    assert promise_lines, "the pinned promise must be named on the promises line"
    assert "Sainte-Chapelle" in promise_lines[0]
    assert "10:20-11:10" in promise_lines[0]
    assert out.index("promises:") < out.index("per-stop"), (
        "the promises line sits ABOVE the per-stop table"
    )


def test_empty_promises_route_prints_no_promises_line(capsys):
    """A pre-S3.6 Route (empty promises tuple) prints NO promises line — the
    identity default that keeps today's output shape for unpromised routes."""
    tour_build = _tour_build()
    route = _route([_poi("poi-a", "Conciergerie")], planned_visit_seconds={"poi-a": 600})
    tour_build._print_breakdown(
        tour_input=_base_input(),
        wall_clock_s=1.0,
        route=route,
        script=_script_for(route),
    )
    out = capsys.readouterr().out
    assert "promises:" not in out
    assert "per-stop" in out, "the table itself still prints"


# --- printer: the shape and queue columns ------------------------------------


def test_shape_column_renders_in_out_and_closed_out(capsys):
    """Every stop's row fills the shape column (the sabotage list forbids a
    column that only fills on promise stops): a goes-inside promise renders
    `44m in` (240s outside + 2400s inside; the queue is NOT folded in —
    design §3.3, 'folding them into one 66 makes the wait permanent'), an
    outside promise renders `15 min out`, an outside-only clock exclusion
    renders `closed — outside only` (units spelled out and the phrasal-verb
    collision removed by the second-language panelist's ruling, W3.4), and
    promise-less stops fall back to the route's priced minutes with in/out
    read off the POI's own capacity numbers."""
    tour_build = _tour_build()
    a = _poi("poi-a", "Musee d'Orsay", place_category="museum")
    b = _poi("poi-b", "Pont Neuf", place_category="bridge")
    c = _poi("poi-c", "Musee de Cluny", place_category="museum")
    d = _poi("poi-d", "Place Dauphine", place_category="square")
    e = _poi(
        "poi-e",
        "Pantheon",
        place_category="monument",
        visit_seconds_inside=1800,
        typical_duration_min=10,
    )
    route = _route(
        [a, b, c, d, e],
        planned_visit_seconds={"poi-d": 600, "poi-e": 900},
        promises=(
            Promise(
                kind="anchor",
                poi_id="poi-a",
                shape=PromiseShape(
                    outside_seconds=240,
                    inside_seconds=2400,
                    queue_seconds=600,
                    goes_inside=True,
                    closed_today=False,
                ),
            ),
            Promise(
                kind="finish",
                poi_id="poi-b",
                shape=PromiseShape(
                    outside_seconds=900,
                    inside_seconds=0,
                    queue_seconds=0,
                    goes_inside=False,
                    closed_today=False,
                ),
            ),
        ),
        clock_exclusions=(
            # W4.12: the DECISION is the `kept_outside` flag, not words in the
            # reason — the harness had string-matched "outside only" and went
            # silently blank the day the sentence was made plain (Paulo's ruling
            # against "seated outside only" / "(hours: OSM)").
            ClockExclusion(
                poi_id="poi-c",
                name="Musee de Cluny",
                reason="closed Tuesday",
                kept_outside=True,
            ),
        ),
    )
    tour_build._print_breakdown(
        tour_input=_base_input(),
        wall_clock_s=1.0,
        route=route,
        script=_script_for(route),
    )
    out = capsys.readouterr().out
    assert "44 min in" in _stop_line(out, "Musee d'Orsay")
    assert "15 min out" in _stop_line(out, "Pont Neuf")
    assert "closed — outside only" in _stop_line(out, "Musee de Cluny")
    # Pre-promise fallback: minutes from planned_visit_seconds, side from the
    # POI's own numbers (no interior -> out; 1800s inside > 10min outside -> in).
    assert "10 min out" in _stop_line(out, "Place Dauphine")
    assert "15 min in" in _stop_line(out, "Pantheon")


def test_queue_column_prints_minutes_and_dash(capsys):
    """The queue column prints the promise's queue minutes, and an em-dash
    when the stop carries no queue — never a zero that reads as a
    measurement."""
    tour_build = _tour_build()
    a = _poi("poi-a", "Sainte-Chapelle", place_category="church")
    b = _poi("poi-b", "Place Dauphine", place_category="square")
    route = _route(
        [a, b],
        planned_visit_seconds={"poi-b": 600},
        promises=(
            Promise(
                kind="anchor",
                poi_id="poi-a",
                shape=PromiseShape(
                    outside_seconds=0,
                    inside_seconds=2280,
                    queue_seconds=1680,
                    goes_inside=True,
                    closed_today=False,
                ),
            ),
        ),
    )
    tour_build._print_breakdown(
        tour_input=_base_input(),
        wall_clock_s=1.0,
        route=route,
        script=_script_for(route),
    )
    out = capsys.readouterr().out
    assert "28m" in _stop_line(out, "Sainte-Chapelle"), "1680s of line = 28m in the queue column"
    dauphine = _stop_line(out, "Place Dauphine")
    assert dauphine.count("—") == 1, (
        "a priced, promise-less stop dashes exactly ONE cell — the queue; its "
        "visit, shape and walk-in cells all carry real values"
    )


# --- printer: Aiko's honesty line --------------------------------------------


def _hours_route() -> Route:
    """Five stops (Docs/adr/0006): map hours, a guess, hours with no source, a
    DOOR with no hours at all (gated=True, opening_hours=None — the least-known
    door must still count), and not gated at all -> M=4 doors, N=3 not from
    the map."""
    from_map = _poi(
        "poi-map", "Musee d'Orsay",
        opening_hours=_DOOR_HOURS, opening_hours_source="map",
    )
    guessed = _poi(
        "poi-guess", "Musee de Cluny",
        opening_hours=_DOOR_HOURS, opening_hours_source="guess",
    )
    sourceless = _poi(
        "poi-none", "Conciergerie",
        opening_hours=_DOOR_HOURS, opening_hours_source=None,
    )
    unknown_door = _poi("poi-door", "Notre-Dame Cathedral", gated=True)
    ungated = _poi("poi-open", "Pont Neuf", place_category="bridge")
    return _route([from_map, guessed, sourceless, unknown_door, ungated])


def test_dated_run_prints_the_hours_by_source_line(capsys):
    """A dated run says where its doors' hours come from (Docs/adr/0006):
    doors = `gated=True`, or hours on record (hours imply a door on a legacy
    row); from the map = source "map"; guessed = hours with any other source,
    or none; unknown = a door with no hours at all. Aiko's finding (design
    §6): clock-native planning is a promise without hours under it, so the
    harness must SAY what the hours under it are.
    UNDO: key the door count on the hours text -> Notre-Dame leaves the numbers -> RED.
    """
    tour_build = _tour_build()
    route = _hours_route()
    tour_build._print_breakdown(
        tour_input=_base_input(start_datetime="2026-08-11T10:00"),
        wall_clock_s=1.0,
        route=route,
        script=_script_for(route),
    )
    out = capsys.readouterr().out
    assert "hours: 1 from the map, 2 guessed, 1 unknown of 4 doors on this route" in out
    assert "unverified" not in out
    # The traveller's own sentences, from the one writer the wire uses: the
    # guessed doors are shut on this Tuesday (their guess says Monday only), so
    # they are named as unconfirmed; the door with no hours is named as such.
    assert "    • We could not confirm opening times for Musee de Cluny." in out
    assert "    • We could not confirm opening times for Conciergerie." in out
    assert "    • No opening times on record for Notre-Dame Cathedral." in out


def test_undated_run_prints_no_hours_line(capsys):
    """No clock, no gate, no line — the dateless output keeps today's shape."""
    tour_build = _tour_build()
    route = _hours_route()
    tour_build._print_breakdown(
        tour_input=_base_input(),
        wall_clock_s=1.0,
        route=route,
        script=_script_for(route),
    )
    assert "from the map" not in capsys.readouterr().out


# =============================================================================
# S3.6 — the planner's promise assembly, the protected set, and pins
# (appended per this file's one-hand-per-file header)
# =============================================================================


def _pin_corpus(*, with_pin_seated_naturally: bool = False):
    """A rich tier-5 cluster and a poor tier-2 chapel ~300 m off it — a place
    the greedy has no reason to choose, which is what makes pinning it a real
    test: the pin, not the score, is why it is in the day."""
    from tests.test_tour_selection import PDV, _density_fillers, _poi, _snap

    chapel = _poi("chapelle-obscure", tier=2, lat=48.8555, lng=2.3697, beat_count=2)
    # Filler COUNT derived by the helper from the planner's dwell target and stop
    # ceiling (re-derived at the Phase 6 close: hand-pinned at five for the 270 s
    # ceiling, the pool could build 25 of the asked 60 minutes once S6.6 made
    # tellings three minutes; the pin and promise claims measured identical at
    # the derived count — the chapel is never chosen on merit, always seated
    # when pinned, an unknown pin still refuses by name).
    fillers = _density_fillers(PDV, duration_min=60, radius_m=80)
    return _snap([chapel, *fillers])


def _pin_request(pins: tuple[str, ...] = ()):
    from tests.test_tour_selection import PDV

    return TourInput(
        start=PDV,
        duration_min=60,
        city_slug="paris",
        round_trip=True,
        pinned_poi_ids=pins,
    )


def test_a_pinned_stop_is_seated_and_survives_every_repair():
    """Théo pins one thing absolutely; Julien pins nothing (design §3.2; plan
    S3.6). A pin is a CERTAINTY: force-seated before the greedy, in the
    repair's protected set (no drop, no exchange may remove it — §4.5.2/4),
    never traded by the pull, never folded into a co-located host. The
    precondition run proves the greedy would NOT have chosen this place on
    its own — the pin, not the score, is why it is in the day."""
    from src.tour.selection import select_route

    unpinned = select_route(_pin_request(), _pin_corpus())
    assert "chapelle-obscure" not in {p.id for p in unpinned.pois}, (
        "precondition broke: the chapel was chosen on merit, so pinning it "
        "would prove nothing — weaken the chapel or enrich the cluster"
    )

    pinned = select_route(_pin_request(("chapelle-obscure",)), _pin_corpus())
    assert "chapelle-obscure" in {p.id for p in pinned.pois}
    pin_promises = [p for p in pinned.promises if p.kind == "pinned"]
    assert [p.poi_id for p in pin_promises] == ["chapelle-obscure"]


def test_an_unknown_pin_is_an_honest_refusal_naming_it():
    """An unseatable pin is an honest refusal naming the pin (plan S3.6) —
    never a day quietly served without it."""
    from src.tour.density import TourabilityRefusedError
    from src.tour.selection import select_route

    with pytest.raises(TourabilityRefusedError) as refusal:
        select_route(_pin_request(("no-such-place",)), _pin_corpus())
    assert "no-such-place" in str(refusal.value)


def test_rests_and_the_finish_are_never_auto_cut():
    """§4.5.2/§4.5.4: rests and the finish are promise-grade. Structurally a
    rest CANNOT be auto-cut — body stops seat AFTER the repair, on the final
    walking order — and the finish survives the repair through the protected
    set (the dusk swap tests prove that behaviourally: the lit endpoint out-
    ranks and out-lives a richer exchange). This test pins the NAMING half
    directly on `_assemble_promises`: the served order's rest and finish are
    promised, with shapes priced at their arrival hours, deduped one promise
    per stop, pins outranking everything, the list capped at five without
    unseating anything."""
    from src.tour.selection import _assemble_promises, _walk_arrivals
    from src.tour.visit_time import visit_shape
    from tests.test_tour_selection import _poi, _snap

    near = _poi("a-near", tier=4, lat=48.8555, lng=2.3670)
    marquee = _poi("m-marquee", tier=5, lat=48.8555, lng=2.3683)
    bench = _poi(
        "body-bench", role="body", tier=1, lat=48.8556, lng=2.3676, beat_count=0
    ).model_copy(update={"typical_duration_min": 5})
    finish = _poi("z-finish", tier=4, lat=48.8555, lng=2.3690)
    ordered = [near, marquee, bench, finish]
    snapshot = _snap(ordered)

    def shape(poi, clock_hour):
        return visit_shape(poi, frozenset(), snapshot, clock_hour=clock_hour)

    arrivals = _walk_arrivals(
        ordered,
        [120, 120, 60, 60],
        clock_start=None,
        price_visit=lambda poi, hour: poi.typical_duration_min * 60,
    )
    promises = _assemble_promises(
        arrivals,
        snapshot=snapshot,
        interest=frozenset(),
        shape_visit=shape,
        pinned_ids=frozenset(),
    )
    kinds = {p.kind: p for p in promises}
    assert kinds["rest"].poi_id == "body-bench"
    assert kinds["rest"].shape.goes_inside is False
    assert kinds["anchor"].poi_id == "m-marquee"
    assert kinds["finish"].poi_id == "z-finish"
    assert len(promises) <= 5

    # One promise per stop, the stronger claim winning: a pinned marquee is
    # promised as PINNED, never twice.
    pinned_promises = _assemble_promises(
        arrivals,
        snapshot=snapshot,
        interest=frozenset(),
        shape_visit=shape,
        pinned_ids=frozenset({"m-marquee"}),
    )
    marquee_claims = [p for p in pinned_promises if p.poi_id == "m-marquee"]
    assert [p.kind for p in marquee_claims] == ["pinned"]


def test_the_day_names_its_promises_with_priced_shapes():
    """Design §3.1: the plan becomes 2-5 promises. Every promise's kind is
    from the closed vocabulary, the list caps at five, and each shape is a
    real priced PromiseShape (outside/inside split from THE one pricer)."""
    from src.tour.selection import select_route

    route = select_route(_pin_request(), _pin_corpus())
    assert 1 <= len(route.promises) <= 5
    for promise in route.promises:
        assert promise.kind in {"anchor", "pinned", "rest", "finish"}
        assert promise.shape.outside_seconds >= 0
        assert promise.poi_id in {p.id for p in route.pois}
    assert any(p.kind == "anchor" for p in route.promises)


def test_a_day_with_rests_still_fits_its_own_ceiling():
    """A rest is part of the day, not an overdraft (plan S3.6; §4.5.2 rests
    are promise-grade; design §2.3 the ceiling is hard).

    Body stops seat AFTER the repair, so before the rest reserve existed the
    repair filled the day to the nominal and the bench's minutes burst the
    hard ceiling: THIS exact fixture measured 3,849 s against 3,600 and the
    whole request refused — a family asking for rest breaks on a full day got
    no day at all. With the reserve, the repair aims lower by the expected
    rest time, the bench seats inside the promise, and the day ships with a
    rest promised and the ceiling intact.
    """
    from src.tour.selection import select_route
    from tests.test_tour_selection import PDV, _density_fillers, _poi, _snap

    near = _poi("a-near", tier=4, lat=48.8555, lng=2.3676)
    marquee = _poi("m-marquee", tier=5, lat=48.8555, lng=2.3694)
    far = _poi("z-far", tier=4, lat=48.8555, lng=2.3710)
    bench = _poi(
        "body-bench", role="body", tier=1, lat=48.8556, lng=2.3672, beat_count=0
    ).model_copy(update={"typical_duration_min": 5})
    fillers = _density_fillers(PDV, duration_min=60, n=5, radius_m=80, tier=4)
    snapshot = _snap([near, marquee, far, bench, *fillers])

    route = select_route(
        TourInput(
            start=PDV,
            duration_min=60,
            city_slug="paris",
            round_trip=True,
            rest_cadence_minutes=3,
        ),
        snapshot,
    )
    # select_route RETURNING is the load-bearing assertion — before the
    # reserve, this exact request raised CertificationPlanningInfeasibleError.
    # The bound below re-states the ceiling in the engine's own currency
    # (walking plus served dwell, the final gate's arithmetic).
    from src.tour.selection import served_dwell_seconds
    from src.tour.visit_time import served_elapsed_seconds

    served = served_elapsed_seconds(
        route.total_walk_seconds,
        served_dwell_seconds(route, snapshot, interest=None, end_is_none=True),
    )
    assert served <= 3600, f"the rested day still bursts its ceiling: {served}s"
    rest_promises = [p for p in route.promises if p.kind == "rest"]
    assert [p.poi_id for p in rest_promises] == ["body-bench"]
