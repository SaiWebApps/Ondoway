"""Phase 1 clock tests — does the planner know what time it is?

One clock-hand per file, on the `tests/test_tour_visit_time.py` model: every
gating test cites the design section or persona line it enforces
(specs/2026-08-07-tour-algorithm-redesign/04-implementation-plan.md §0.1.1).

All Phase 1 clock tests live here (plan step S1.2): the contract fields, the
API-model carry, the persisted-inputs round trip, the harness flags, and the
clock filter.

The RED that proved S1.2 was real: `TourInput` is `extra="forbid"`
(src/tour/contract.py), so constructing it with `start_datetime` /
`end_hardness` raised before the fields existed. That forbid is load-bearing
for this file and its only guard is
`tests/test_tour_contract.py::test_tour_input_rejects_extra_keys` — if that
guard ever dies, the RED here goes vacuous with it.
"""

import pytest
from pydantic import ValidationError

from src.tour.contract import POI, TourInput


def _base_input(**overrides) -> TourInput:
    fields = {
        "start": (48.8568, 2.3414),
        "duration_min": 90,
        "city_slug": "paris",
    }
    fields.update(overrides)
    return TourInput(**fields)


def test_tour_input_carries_the_clock_and_end_hardness():
    """The request carries a real clock and says how hard its end is.

    Cites design §2.2 — the start is soft: "Fiona & Dev stood reading a menu
    for twelve minutes" (docs/personas/09-couple-who-would-rather-talk.md,
    step 1) — and §2.3: Marcus's 16:40 wall
    (docs/personas/04-layover-sprinter.md), Camille's "is 15:00 a wall or a
    wish?" (03-panel-findings.md), and Julien's leavable-blank clock
    (03-panel-findings.md). A planner that cannot be told the date and the
    hardness of its end cannot serve any of the four.
    """
    inp = _base_input(start_datetime="2026-08-11T10:00:00", end_hardness="wall")
    assert inp.start_datetime == "2026-08-11T10:00:00"
    assert inp.end_hardness == "wall"


def test_tour_input_clock_defaults_keep_todays_behaviour():
    """No datetime and `firm` are the defaults, so every existing caller and
    golden stays byte-identical (plan S1.2: "None = today's dateless
    behaviour"; design §2.3: `firm` is byte-identical to today)."""
    inp = _base_input()
    assert inp.start_datetime is None
    assert inp.end_hardness == "firm"


def test_tour_input_rejects_a_malformed_datetime_with_a_plain_message():
    """A malformed datetime is refused in plain words (plan S1.2: "A validator
    rejects a malformed datetime with a plain message"), not with a parser
    traceback — the API surfaces this text to the person who typed it."""
    with pytest.raises(ValidationError, match="not a valid date and time"):
        _base_input(start_datetime="next tuesday-ish")


def test_tour_input_rejects_an_unknown_end_hardness():
    """`end_hardness` is exactly wall | firm | open (design §2.3) — a typo'd
    hardness must not silently plan as `firm`."""
    with pytest.raises(ValidationError):
        _base_input(end_hardness="soft")


def test_api_request_models_carry_the_clock_and_end_hardness():
    """Both API request models CARRY the two fields rather than dropping them.

    Neither `TripGenerateRequest` nor `TripPreviewRequest` declares
    `model_config`, so both inherit pydantic's default `extra="ignore"`: an
    undeclared field is SILENTLY DROPPED and the layer above still looks fine
    (the `max_stop_minutes` comment in src/api/models/trips.py records the
    same trap verbatim). This test is what proves the declaration exists.
    Cites design §2.2/§2.3 via plan step S1.3a.
    """
    from src.api.models.trips import TripGenerateRequest, TripPreviewRequest

    gen = TripGenerateRequest(
        profile_id="p1",
        center_lat=48.8568,
        center_lng=2.3414,
        start_date="2026-08-11",
        end_date="2026-08-11",
        start_datetime="2026-08-11T10:00:00",
        end_hardness="wall",
    )
    assert gen.start_datetime == "2026-08-11T10:00:00"
    assert gen.end_hardness == "wall"

    prev = TripPreviewRequest(
        center_lat=48.8568,
        center_lng=2.3414,
        start_datetime="2026-08-11T10:00:00",
        end_hardness="open",
    )
    assert prev.start_datetime == "2026-08-11T10:00:00"
    assert prev.end_hardness == "open"


def test_api_request_models_reject_a_bad_end_hardness():
    """A typo'd hardness is a 422 at the edge (plan S1.3a: "a bad
    `end_hardness` is a 422"), never a silently-firm plan. ValidationError
    here is exactly what FastAPI surfaces as the 422."""
    from src.api.models.trips import TripGenerateRequest, TripPreviewRequest

    with pytest.raises(ValidationError):
        TripGenerateRequest(
            profile_id="p1",
            center_lat=48.8568,
            center_lng=2.3414,
            start_date="2026-08-11",
            end_date="2026-08-11",
            end_hardness="soft",
        )
    with pytest.raises(ValidationError):
        TripPreviewRequest(center_lat=48.8568, center_lng=2.3414, end_hardness="soft")


def _trips_route_source() -> str:
    from pathlib import Path

    return (
        Path(__file__).resolve().parent.parent / "src" / "api" / "routes" / "trips.py"
    ).read_text()


def test_the_request_sites_thread_the_clock_and_the_restorer_forwards_every_persisted_key():
    """All construction sites pass the clock through — never one or two.

    RE-DERIVED at Phase 5 S5.8 (a written decision, phase5-ledger.md): the former
    scan counted FOUR explicit `_build_tour_input(...)` sites and required
    `start_datetime=` / `end_hardness=` at each. Compose (and both session
    endpoints) now restore the persisted request through ONE door,
    `_restore_tour_input`, which forwards EVERY persisted key by construction — so
    the request-driven sites (generate, preview, author) still carry the clock
    explicitly (the AST half below, plan S1.3b's sabotage), and the restorer is
    proven BEHAVIOURALLY: a stored record with a clock restores that clock and its
    hardness, together with the axes Phase 5 began persisting (pins, party, pace).
    """
    import ast

    from src.api.routes.trips import _restore_tour_input

    tree = ast.parse(_trips_route_source())
    call_sites = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_build_tour_input"
    ]
    # A request site names its arguments (and splats the dials); the restorer only
    # forwards the persisted record (`**fields`) and names nothing.
    explicit = [c for c in call_sites if any(kw.arg is not None for kw in c.keywords)]
    forwarding = [c for c in call_sites if all(kw.arg is None for kw in c.keywords)]
    assert len(explicit) == 3, (
        "expected the generate/preview/author _build_tour_input sites, "
        f"found {len(explicit)}"
    )
    assert len(forwarding) == 1, "exactly ONE restorer forwards the persisted record"
    for call in explicit:
        keywords = {kw.arg for kw in call.keywords}
        assert "start_datetime" in keywords, f"line {call.lineno} drops start_datetime"
        assert "end_hardness" in keywords, f"line {call.lineno} drops end_hardness"

    restored = _restore_tour_input(
        {
            "start": [48.8568, 2.3414],
            "end": None,
            "duration_min": 90,
            "city_slug": "paris",
            "lenses": None,
            "round_trip": False,
            "start_datetime": "2026-08-19T14:00",
            "end_hardness": "wall",
            "pinned_poi_ids": ["poi-pinned"],
            "party": "take_it_easy",
            "walking_pace": 1.2,
        }
    )
    assert restored.start_datetime == "2026-08-19T14:00"
    assert restored.end_hardness == "wall"
    assert restored.pinned_poi_ids == ("poi-pinned",)
    assert restored.party == "take_it_easy" and restored.walking_pace == 1.2


def test_generate_persists_the_clock_and_the_restorer_reads_it_fail_open():
    """The clock survives the save: persisted at generate, restored fail-open.

    Persist: the `tour_input_json` dict in `generate_trip` carries both keys (a
    source read — the record's spelling IS the contract older trips were written
    in). Restore: RE-DERIVED at Phase 5 S5.8 — the former `.get(...)` literal scan
    is replaced by the behaviour it guarded: a record saved before the clock
    existed lands on the identity defaults, dateless and `firm`, so it composes
    exactly as it always did (plan S1.3b's sabotage list: a direct key access
    would 500 every legacy trip).
    """
    from src.api.routes.trips import _restore_tour_input

    source = _trips_route_source()
    assert '"start_datetime": tour_input.start_datetime' in source, (
        "generate_trip's tour_input_json does not persist start_datetime"
    )
    assert '"end_hardness": tour_input.end_hardness' in source, (
        "generate_trip's tour_input_json does not persist end_hardness"
    )
    legacy = _restore_tour_input(
        {
            "start": [48.8568, 2.3414],
            "end": None,
            "duration_min": 90,
            "city_slug": "paris",
            "lenses": None,
            "round_trip": False,
        }
    )
    assert legacy.start_datetime is None
    assert legacy.end_hardness == "firm"
    assert legacy.pinned_poi_ids == () and legacy.party is None


def test_a_trip_saved_before_the_clock_existed_composes_exactly_as_it_always_did():
    """The fail-open restore, exercised: a legacy stored dict (no clock keys)
    lands on the identity defaults — dateless, `firm` — so a trip saved before
    this key existed composes exactly as it always did (plan S1.3b)."""
    legacy_stored = {
        "start": [48.8568, 2.3414],
        "end": None,
        "duration_min": 90,
        "city_slug": "paris",
        "lenses": None,
        "round_trip": False,
    }
    restored = TourInput(
        start=tuple(legacy_stored["start"]),
        duration_min=legacy_stored["duration_min"],
        city_slug=legacy_stored["city_slug"],
        lenses=legacy_stored["lenses"],
        round_trip=legacy_stored["round_trip"],
        start_datetime=legacy_stored.get("start_datetime"),
        end_hardness=legacy_stored.get("end_hardness") or "firm",
    )
    assert restored.start_datetime is None
    assert restored.end_hardness == "firm"


def test_harness_flags_parse_and_a_dateless_run_stays_dateless():
    """`scripts/tour_build.py` accepts --date/--time/--end-hardness, and
    omitting them leaves the run exactly as today (plan S1.3c: "a dateless run
    is byte-identical to today's" — the flags default to no clock and `firm`,
    so the TourInput built is byte-identical to the pre-clock one)."""
    import importlib

    tour_build = importlib.import_module("scripts.tour_build")
    parser = tour_build._build_arg_parser()

    dated = parser.parse_args(
        ["--start", "X", "--duration", "60", "--canned",
         "--date", "2026-08-11", "--time", "10:00", "--end-hardness", "wall"]
    )
    assert dated.date == "2026-08-11"
    assert dated.time == "10:00"
    assert dated.end_hardness == "wall"

    dateless = parser.parse_args(["--start", "X", "--duration", "60", "--canned"])
    assert dateless.date is None
    assert dateless.end_hardness == "firm"


def test_breakdown_prints_clock_exclusions_only_on_a_dated_run(capsys):
    """The clock-exclusions section appears iff the run has a clock.

    Dateless → NO section, so today's output (and W1.1's before-picture) stays
    byte-identical. Dated with no exclusions → the section prints "none", so a
    Monday and a Tuesday run keep the same shape and read side by side (plan
    S1.3c sabotage list: omitting the empty section makes the D1 pair
    incomparable)."""
    import importlib

    tour_build = importlib.import_module("scripts.tour_build")

    dateless_input = _base_input()
    tour_build._print_breakdown(tour_input=dateless_input, wall_clock_s=1.0)
    dateless_out = capsys.readouterr().out
    assert "clock exclusions" not in dateless_out

    dated_input = _base_input(start_datetime="2026-08-11T10:00:00")
    tour_build._print_breakdown(tour_input=dated_input, wall_clock_s=1.0)
    dated_out = capsys.readouterr().out
    assert "clock exclusions" in dated_out
    assert "none" in dated_out.split("clock exclusions", 1)[1]


def test_route_records_clock_exclusions_additively():
    """The Route carries WHO the clock excluded and WHY, on the Route itself.

    Cites plan S1.6a and the `elapsed_shortfall_seconds` lesson recorded in
    src/tour/contract.py: a disclosure that rides a channel which is null in
    its own case discloses nothing — `tourability` is None on a GREEN route,
    which is exactly the route that must still say "the museum is closed
    today". Additive metadata in the `vignettes` mould: default empty, so
    every existing Route is byte-identical.
    """
    from src.tour.contract import ClockExclusion, Route

    excl = ClockExclusion(
        poi_id="musee-x",
        name="Musée X",
        reason="closed all day Tuesday (hours: OSM); would otherwise have been seated",
    )
    bare = Route(pois=(), transits=(), total_walk_distance_m=0.0, total_walk_seconds=0)
    assert bare.clock_exclusions == ()
    carrying = bare.model_copy(update={"clock_exclusions": (excl,)})
    assert carrying.clock_exclusions[0].name == "Musée X"
    assert "Tuesday" in carrying.clock_exclusions[0].reason


# --- the clock filter (S1.6b), on a hermetic constructed snapshot -----------

#: 2026-08-11 is a Tuesday; 2026-08-10 the Monday before it.
_TUESDAY_10AM = "2026-08-11T10:00:00"
_MONDAY_10AM = "2026-08-10T10:00:00"

_CLOSED_TUESDAY_TABLE = {
    "mon": [["09:00", "18:00"]],
    "tue": [],
    "wed": [["09:00", "18:00"]],
    "thu": [["09:00", "18:00"]],
    "fri": [["09:00", "18:00"]],
    "sat": [["09:00", "18:00"]],
    "sun": [["09:00", "18:00"]],
}


def _tuesday_closed_museum():
    import json as _json

    from tests.test_tour_selection import PDV

    return POI(
        id="musee-ferme-mardi",
        name="Musée Fermé le Mardi",
        tier=5,
        poi_role="stop",
        lat=PDV[0],
        lng=PDV[1],
        areas=("Paris",),
        beat_count=5,
        opening_hours=_json.dumps(_CLOSED_TUESDAY_TABLE),
        opening_hours_source="osm",
        opening_hours_basis="Museum; OSM tag 'Mo,We-Su 09:00-18:00; Tu off'.",
    )


def _clock_corpus(museum):
    from tests.test_tour_selection import PDV, _density_fillers, _snap

    return _snap([museum, *_density_fillers(PDV)])


def _clock_request(start_datetime):
    from tests.test_tour_selection import PDV

    return TourInput(
        start=PDV,
        duration_min=60,
        city_slug="paris",
        start_datetime=start_datetime,
    )




def test_the_same_poi_is_seated_on_a_day_it_is_open():
    """The identical request on Monday seats the museum — the exclusion is the
    clock's, not the place's (design 6.1; 07-rainy-tuesday.md step 6)."""
    from src.tour.selection import select_route

    museum = _tuesday_closed_museum()
    route = select_route(_clock_request(_MONDAY_10AM), _clock_corpus(museum))

    assert museum.id in {p.id for p in route.pois}
    assert route.clock_exclusions == ()


def test_no_datetime_means_no_filtering_and_a_byte_identical_pool():
    """No clock → no filtering: the dateless pool is byte-identical to today's,
    which is the identity default that keeps every existing test and golden
    green (plan S1.6b: "No datetime → no filtering, byte-identical pool")."""
    from src.tour.selection import select_route

    museum = _tuesday_closed_museum()
    dateless = select_route(_clock_request(None), _clock_corpus(museum))
    monday = select_route(_clock_request(_MONDAY_10AM), _clock_corpus(museum))

    assert museum.id in {p.id for p in dateless.pois}
    assert dateless.clock_exclusions == ()
    assert [p.id for p in dateless.pois] == [p.id for p in monday.pois]


# --- closed means outside-only, not gone (S3.5; W1.9 dissent 1) ---------------


def _tuesday_closed_museum_with_an_exterior():
    """The closed museum, but with a facade worth twenty minutes — the shape
    the Phase 1 panel ruled on. Interior 45 min when open."""
    museum = _tuesday_closed_museum()
    return museum.model_copy(
        update={"typical_duration_min": 20, "visit_seconds_inside": 2700}
    )


def test_a_closed_museum_with_an_exterior_becomes_an_outside_only_stop():
    """Closed for the whole window ≠ gone: the stop survives at its EXTERIOR
    price, and the day says so.

    BY PANEL ORDER, not audit order (W1.9 dissent 1 — Camille, Théo, Greta,
    and Fiona & Dev all ruled a clock-closed building should become an
    OUTSIDE-ONLY stop rather than leave the pool; "the strongest recurring
    finding", re-confirmed by the D2 panel). The D1 demo itself made the
    point: its Monday run STARTS at a Monday-closed Orsay, and deleting the
    closed anchor deletes the walk's reason to exist.

    On Tuesday the museum is seated, priced at its outside minutes only
    (20 min, never the 45-min interior or the open-day blend), and the
    disclosure channel names it with an "outside only" reason. On Monday the
    same request prices the full open-day blend — the exclusion was the
    clock's, and so is the demotion.
    """
    from src.tour.selection import select_route

    museum = _tuesday_closed_museum_with_an_exterior()
    tuesday = select_route(_clock_request(_TUESDAY_10AM), _clock_corpus(museum))

    assert museum.id in {p.id for p in tuesday.pois}, (
        "a closed building with an exterior left the pool — the delete-vs-demote "
        "fix is not wired"
    )
    demoted = [e for e in tuesday.clock_exclusions if e.poi_id == museum.id]
    assert len(demoted) == 1, "the outside-only demotion must be disclosed"
    # Re-derived at W4.12: the DECISION is a flag, not words in the sentence. The
    # panel ruled "seated outside only" out; the harness then string-matched the
    # reason and went blank; and the traveller's sentence ("we will see it from
    # the outside" vs "not in your day") depends on route membership only the
    # wire knows. The DISCLOSURE this test defends is unchanged.
    assert demoted[0].kept_outside is True
    assert "closed" in demoted[0].reason and "Tuesday" in demoted[0].reason
    assert tuesday.planned_visit_seconds[museum.id] == 20 * 60, (
        "a closed interior (and any queue) must price at ZERO — the visitor "
        "stands outside for the exterior minutes and nothing else"
    )

    monday = select_route(_clock_request(_MONDAY_10AM), _clock_corpus(museum))
    assert monday.clock_exclusions == ()
    # Open-day blend (no lenses → outside + 0.6·gap): 1200 + 0.6·1500 = 2100.
    assert monday.planned_visit_seconds[museum.id] == 2100


def test_a_closed_poi_with_nothing_to_stand_and_see_is_still_excluded():
    """The ONE honest removal the panel's demotion keeps: a closed place with
    no outside value (typical_duration_min == 0) leaves the pool and is
    recorded — there is nothing to stand and see (plan S3.5). This is the
    only closure keying allowed: never tier, never score (the sabotage
    list)."""
    from src.tour.selection import select_route

    museum = _tuesday_closed_museum()  # typical_duration_min = 0
    tuesday = select_route(_clock_request(_TUESDAY_10AM), _clock_corpus(museum))

    assert museum.id not in {p.id for p in tuesday.pois}
    recorded = [e for e in tuesday.clock_exclusions if e.poi_id == museum.id]
    assert len(recorded) == 1, "the honest removal must still be recorded"
    assert "closed" in recorded[0].reason
    assert recorded[0].kept_outside is False, "nothing to see from the street = left the pool"


def test_a_closure_is_disclosed_in_plain_words_and_keeps_its_doubt():
    """W4.12 (design deviation v; Paulo's wording rulings from the W4.2 panel).

    This sentence goes on screen to a traveller, and the live Paris corpus was
    printing it as:

        "Marché Bastille — closed all day Wednesday (hours: OSM); closed today
         — seated outside only"

    which breaks two explicit rulings at once. "(hours: OSM)" is a provenance
    tag no traveller can read. "seated" is the engine's own word for putting a
    stop in the day, and on a MARKET the phrase reads as if a shut market had
    tables outside.

    The rulings did NOT ask for the doubt to be dropped with the tag. So the
    doubt is now carried in words: an unverified table says so in the sentence,
    a verified one simply states the closure. That is the pair this test pins —
    forbidden vocabulary out, honesty in.
    """
    import datetime as dt
    import json as _json

    from src.tour.selection import _clock_exclusion_reason

    closed_wed = _json.dumps({
        "mon": [["09:00", "18:00"]], "tue": [["09:00", "18:00"]], "wed": [],
        "thu": [["09:00", "18:00"]], "fri": [["09:00", "18:00"]],
        "sat": [["09:00", "18:00"]], "sun": [["09:00", "18:00"]],
    })
    wednesday = dt.datetime(2026, 8, 12, 10, 0)

    trust = '{"tier": 2, "approver": "owner", "evidence": "reviewed", "at": "2026-09-07"}'
    verified = _clock_exclusion_reason(closed_wed, trust, wednesday, 180)
    guessed = _clock_exclusion_reason(closed_wed, None, wednesday, 180)
    unsourced = _clock_exclusion_reason(closed_wed, None, wednesday, 180)

    for sentence in (verified, guessed, unsourced):
        assert sentence is not None and "Wednesday" in sentence, sentence
        # The ruled-out vocabulary, in one place so a re-introduction is loud.
        for banned in ("hours:", "OSM", "AI", "seated", "gated", "err-short"):
            assert banned not in sentence, f"{banned!r} is back in {sentence!r}"

    # A table we trust asserts the closure and nothing more.
    assert verified == "closed all day Wednesday", verified
    # A table we guessed says we guessed, in words a person reads.
    assert "could not confirm" in guessed, guessed
    assert "could not confirm" in unsourced, unsourced


# --- the door is checked at each stop's own ARRIVAL window --------------------
#
# The pool rule above judges a door against the WHOLE tour window, so on a long
# day a place open for any slice of it counts open — even when the walk reaches
# it hours outside that slice. These tests pin the arrival-window check: the
# served order's own clock decides whether the visitor can actually go in.

#: 2026-09-07 is a Monday.
_MONDAY_0930 = "2026-09-07T09:30:00"


def _cathedral_hours(monday_windows):
    import json as _json

    return _json.dumps(
        {
            "mon": monday_windows,
            "tue": [], "wed": [], "thu": [], "fri": [], "sat": [], "sun": [],
        }
    )


def _dated_pont_neuf_run(monday_windows, start_datetime=_MONDAY_0930):
    """The 100-minute Pont Neuf → cathedral day (the fits-the-interior fixture
    from tests.test_tour_selection), with opening hours on the cathedral and a
    clock on the request. The walk reaches the cathedral ~10:00: 720 s on the
    bridge, ~1067 s of walking."""
    from tests.test_tour_selection import _ND_END, _ND_START, _pont_neuf_to_cathedral

    bridge, cathedral, snap = _pont_neuf_to_cathedral()
    cathedral = cathedral.model_copy(
        update={
            "opening_hours": _cathedral_hours(monday_windows),
            "opening_hours_source": "osm",
        }
    )
    import dataclasses

    snap = dataclasses.replace(snap, pois=(bridge, cathedral))
    from src.tour.selection import select_route

    inp = TourInput(
        start=_ND_START,
        end=_ND_END,
        duration_min=100,
        city_slug="paris",
        lenses=["historic_arch"],
        start_datetime=start_datetime,
    )
    return bridge, cathedral, select_route(inp, snap)


def test_a_door_shut_at_the_stops_own_arrival_prices_the_stop_outside_only():
    """A door open for a slice of the DAY but shut when the walk ARRIVES is a
    shut door: the stop survives at its exterior price and the day says why.

    The cathedral opens Monday 06:00-09:45. The 09:30 + 100-minute request's
    whole window (09:30-11:10) overlaps that, so the pool rule keeps the full
    interior — but the walk reaches the door ~10:00, fifteen minutes after it
    shut. Pricing the interior and licensing "step inside" there is the exact
    fault the roadmap names: the plan knows the door is shut; the voice says
    go inside. The arrival-window check prices the stop through the same
    exterior collapse a whole-window closure uses, at the same one site."""
    bridge, cathedral, route = _dated_pont_neuf_run([["06:00", "09:45"]])

    assert [p.id for p in route.pois] == [bridge.id, cathedral.id]
    assert route.visit_goes_inside[cathedral.id] is False, (
        "the door is shut at this stop's own arrival — the interior must not "
        "be priced, and door lines must not be licensed"
    )
    assert route.planned_queue_seconds[cathedral.id] == 0
    assert route.planned_visit_seconds[cathedral.id] == 25 * 60
    said = [e for e in route.clock_exclusions if e.poi_id == cathedral.id]
    assert len(said) == 1, route.clock_exclusions
    assert said[0].kept_outside is True
    assert "closed Monday" in said[0].reason, said[0].reason
    finish = next(p for p in route.promises if p.poi_id == cathedral.id)
    assert finish.shape.goes_inside is False
    assert finish.shape.queue_seconds == 0


def test_a_door_open_at_arrival_keeps_the_interior_untouched():
    """The same day against a door that is open when the walk arrives changes
    nothing: full interior, full queue, no disclosure — the arrival check may
    only ever CLOSE a door the arrival clock proves shut."""
    _bridge, cathedral, route = _dated_pont_neuf_run([["06:00", "20:00"]])

    assert route.visit_goes_inside[cathedral.id] is True
    assert route.planned_visit_seconds[cathedral.id] == 50 * 60 + 15 * 60
    assert route.clock_exclusions == ()


def test_a_dateless_day_never_consults_the_arrival_clock():
    """No clock = no arrival check = today's behaviour, byte-identical — the
    same identity default every other clock rule keeps (plan S1.6b)."""
    _bridge, cathedral, route = _dated_pont_neuf_run(
        [["06:00", "09:45"]], start_datetime=None
    )

    assert route.visit_goes_inside[cathedral.id] is True
    assert route.planned_visit_seconds[cathedral.id] == 50 * 60 + 15 * 60
    assert route.clock_exclusions == ()


# --- a dead door leaves the day (Docs/adr/0004) -------------------------------
#
# The arrival check above demotes a shut door to its exterior. A shut door with
# NOTHING outside (typical_duration_min == 0) used to stay anyway — a zero-value
# stand the walker is still routed to. It now leaves the day at planning time,
# disclosed as not-in-your-day, with four carve-outs: pinned stops, replan-
# protected stops and the fixed end are never dropped, and a drop that would
# push the day under the underfill refusal keeps the demotion instead — a
# padded day beats no day (refill is Phase 11's).


def _seam_poi(pid: str, typical: int):
    from tests.test_tour_selection import PDV, _poi

    return _poi(pid, tier=5, lat=PDV[0], lng=PDV[1], areas=("Paris",), beat_count=5).model_copy(
        update={"typical_duration_min": typical}
    )


def _run_drop(pois, *, dead_ids, undroppable=frozenset(), minimum=0, nominal=6000):
    """Drive the drop rule at its own seam with deterministic doubles: every
    leg 300 s, every visit 600 s, a door is 'shut at arrival' exactly for the
    ids the test names — no greedy, no band, no repair in the way."""
    import datetime as dt

    from src.tour.contract import PromiseShape
    from src.tour.routing import DEFAULT_ROUTE_PLANNING_POLICY, route_planning_budget
    from src.tour.selection import _drop_dead_doors, _full_route_leg_seconds, _walk_arrivals

    clock_start = dt.datetime(2026, 8, 10, 9, 30)
    price = lambda poi, hour, clock=None: 600  # noqa: E731
    legs_fn = lambda a, b, c, d: 300  # noqa: E731
    legs = _full_route_leg_seconds(
        pois, start_lat=pois[0].lat, start_lng=pois[0].lng, round_trip=False,
        leg_seconds_fn=legs_fn,
    )
    arrivals = _walk_arrivals(pois, legs, clock_start=clock_start, price_visit=price)
    budget = route_planning_budget(60, DEFAULT_ROUTE_PLANNING_POLICY)
    if minimum == 0:
        import dataclasses as _dc

        budget = _dc.replace(budget, minimum_elapsed_seconds=0)
    else:
        import dataclasses as _dc

        budget = _dc.replace(
            budget, minimum_elapsed_seconds=minimum, nominal_elapsed_seconds=nominal
        )
    exclusions: list = []
    shape = PromiseShape(
        outside_seconds=0, inside_seconds=0, queue_seconds=0,
        goes_inside=False, closed_today=True,
    )
    kept, kept_arrivals = _drop_dead_doors(
        list(pois),
        arrivals,
        undroppable=set(undroppable),
        arrival_door_reason=lambda poi, hour, clock: (
            "closed Monday 09:30-10:30" if poi.id in dead_ids else None
        ),
        shape_visit=lambda poi, hour, clock=None: shape,
        price_visit=price,
        leg_seconds_fn=legs_fn,
        start_lat=pois[0].lat,
        start_lng=pois[0].lng,
        round_trip=False,
        clock_start=clock_start,
        planning_budget=budget,
        clock_exclusions=exclusions,
    )
    return kept, kept_arrivals, exclusions


def test_a_dead_door_at_arrival_leaves_the_day_and_says_so():
    """Shut at its own arrival, nothing outside: the stop is gone from the
    served order — the walker is never routed to it — the day says it is not
    in the day (kept_outside False), the order of the survivors holds, and
    their arrivals are re-timed on the shorter walk."""
    a, dead, c = _seam_poi("a", 12), _seam_poi("dead-door", 0), _seam_poi("c", 15)
    kept, kept_arrivals, said = _run_drop([a, dead, c], dead_ids={"dead-door"})

    assert [p.id for p in kept] == ["a", "c"], "the dead door is still on the route"
    assert [p.id for p, _h, _s, _c in kept_arrivals] == ["a", "c"]
    assert len(said) == 1
    assert said[0].poi_id == "dead-door"
    assert said[0].kept_outside is False
    assert "closed" in said[0].reason


def test_a_dead_door_with_an_exterior_keeps_todays_demotion():
    """The same shut arrival at a stop WITH an exterior drops nothing: the
    demote-and-disclose behaviour above this seam owns it."""
    a, facade = _seam_poi("a", 12), _seam_poi("facade", 20)
    kept, _arrivals, said = _run_drop([a, facade], dead_ids={"facade"})

    assert [p.id for p in kept] == ["a", "facade"]
    assert said == []


def test_an_undroppable_dead_door_stays():
    """Pinned, protected and the fixed end ride the same carve-out: named
    undroppable, the zero-value stand stays (demoted upstream) rather than
    being auto-cut."""
    a, dead = _seam_poi("a", 12), _seam_poi("dead-pin", 0)
    kept, _arrivals, said = _run_drop(
        [a, dead], dead_ids={"dead-pin"}, undroppable={"dead-pin"}
    )
    assert [p.id for p in kept] == ["a", "dead-pin"]
    assert said == []


def test_a_drop_that_would_underfill_keeps_the_demotion():
    """A padded day beats no day: when removing the dead stops would push the
    day's experience under the underfill refusal, the drop stands down and
    the demotion (upstream) serves the day instead — refill is Phase 11's."""
    a, dead = _seam_poi("a", 12), _seam_poi("dead-door", 0)
    # Remaining after a drop: 2 legs x 300 + 1 visit x 600 = 1200 s, under a
    # 3000 s floor (0.5 x 6000) -> the guard refuses the drop.
    kept, _arrivals, said = _run_drop(
        [a, dead], dead_ids={"dead-door"}, minimum=5400, nominal=6000
    )
    assert [p.id for p in kept] == ["a", "dead-door"]
    assert said == []


def _dated_ab_run(*, dead: bool, pinned=(), start_datetime="2026-08-10T09:30:00"):
    """The seam tests above pin the rule's logic; these two drive the WIRING
    inside the planner — a two-candidate A->B day ending at a sentinel finish
    point, with a mid-course museum open for one minute of the request's
    window (pool keeps it; any arrival finds it shut)."""
    import dataclasses
    import json as _json

    from tests.test_tour_selection import _ND_END, _ND_START, _poi, _pont_neuf_to_cathedral

    bridge, _cathedral, snap = _pont_neuf_to_cathedral()
    bridge = bridge.model_copy(update={"typical_duration_min": 22})
    windows = {
        d: [["06:00", "09:31"]]
        for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    }
    museum = _poi(
        "mid-museum",
        tier=5,
        lat=(_ND_START[0] + _ND_END[0]) / 2,
        lng=(_ND_START[1] + _ND_END[1]) / 2,
        areas=("Île de la Cité",),
        beat_count=8,
    ).model_copy(
        update={
            "typical_duration_min": 0 if dead else 20,
            "visit_seconds_inside": 40 * 60,
            "visit_basis": "forty minutes inside the mid-course museum",
            "opening_hours": _json.dumps(windows),
            "opening_hours_source": "osm",
        }
    )
    snap = dataclasses.replace(snap, pois=(bridge, museum))
    from src.tour.selection import select_route

    inp = TourInput(
        start=_ND_START,
        end=(_ND_END[0] + 0.002, _ND_END[1]),
        duration_min=78,
        city_slug="paris",
        start_datetime=start_datetime,
        pinned_poi_ids=tuple(pinned),
    )
    return museum, select_route(inp, snap)


def test_a_pinned_dead_door_is_never_dropped():
    """An overridden pin is the one outcome worse than a refusal (the pin
    block's own ruling): a pinned stop stays, demoted and disclosed, however
    dead its door."""
    museum, route = _dated_ab_run(dead=True, pinned=["mid-museum"])

    assert museum.id in [p.id for p in route.pois], (
        "a pinned stop was auto-dropped — the promise machinery exists to make "
        "exactly this impossible"
    )
    said = [e for e in route.clock_exclusions if e.poi_id == museum.id]
    assert said and said[0].kept_outside is True


def test_a_dateless_day_never_drops_a_door():
    """No clock, no drop — the identity default every clock rule keeps."""
    _museum, route = _dated_ab_run(dead=True, start_datetime=None)
    assert route.pois, "the dateless day serves"
    assert route.clock_exclusions == (), (
        "a dateless day consulted the clock — the identity default broke"
    )


# --- the all_day flag: a decision in a field, never recovered from words ------


def test_the_planner_flags_an_all_day_closure_and_an_arrival_window_one_apart():
    """The voice's "closed today" vs "closed at the moment" branch is picked by
    `ClockExclusion.all_day` — set by the planner from the day's own empty
    window list, never by string-matching the reason sentence (the W4.12
    lesson: a decision belongs in a field). The whole-window Tuesday museum is
    all-day; the arrival-window cathedral (open 06:00-09:45, reached ~10:00)
    is not."""
    from src.tour.selection import select_route

    museum = _tuesday_closed_museum_with_an_exterior()
    tuesday = select_route(_clock_request(_TUESDAY_10AM), _clock_corpus(museum))
    demoted = [e for e in tuesday.clock_exclusions if e.poi_id == museum.id]
    assert demoted[0].all_day is True

    _bridge, cathedral, route = _dated_pont_neuf_run([["06:00", "09:45"]])
    said = [e for e in route.clock_exclusions if e.poi_id == cathedral.id]
    assert said[0].all_day is False


def test_the_planner_flags_a_closed_place_at_the_walks_own_start():
    """`ClockExclusion.at_start` — the walker can SEE the shut door they are
    standing beside, so the fact rides the record as a field set from
    coordinates the planner alone holds, never recovered from words. The
    Tuesday-closed museum AT the request's start is flagged; the same museum
    a kilometre away is not."""
    from src.tour.selection import select_route

    near = _tuesday_closed_museum_with_an_exterior()
    route = select_route(_clock_request(_TUESDAY_10AM), _clock_corpus(near))
    said = [e for e in route.clock_exclusions if e.poi_id == near.id]
    assert said[0].at_start is True

    # ~220 m up the street: past the 50 m footprint floor, still in the pool.
    afar = near.model_copy(update={"lat": near.lat + 0.002})
    route = select_route(_clock_request(_TUESDAY_10AM), _clock_corpus(afar))
    said = [e for e in route.clock_exclusions if e.poi_id == afar.id]
    assert said[0].at_start is False


def test_a_dropped_door_at_the_walks_own_start_is_flagged_at_start():
    """The honest-removal seam carries the same fact: a dead door dropped
    right where the walk begins is flagged `at_start`, one dropped a
    kilometre into the day is not — so the voice can name the door the
    walker is looking at and stay silent about the one they never see."""
    a, near = _seam_poi("a", 12), _seam_poi("near-dead", 0)
    far = _seam_poi("far-dead", 0).model_copy(update={"lat": near.lat + 0.01})
    _kept, _arrivals, said = _run_drop(
        [a, near, far], dead_ids={"near-dead", "far-dead"}
    )
    flags = {e.poi_id: e.at_start for e in said}
    assert flags == {"near-dead": True, "far-dead": False}


# --- dusk, and the after-dark finish (S3.7; design §4.3; Sofia's swap rule) ---

# December early evening: a 17:00 + 60-min one-way plans to finish ~18:00,
# past Paris civil dusk (~17:31 CET on the 15th); the 09:00 sibling finishes
# at 10:00, hours before it. Same corpus, same request — only the clock moves.
_DEC_EVENING = "2026-12-15T17:00:00"
_DEC_MORNING = "2026-12-15T09:00:00"


def _dusk_corpus(*, with_lit_finisher: bool):
    """A near-start filler cluster plus far-envelope finisher(s) ~470 m east.

    Just past the 60-min one-way far-half line (~444 m of an 889 m reach), and
    near enough that the pull can AFFORD either finisher inside the walk
    budget (farther placements made the pull fall back to a cheaper far-half
    filler). The rich finisher (8 beats) outranks the lit one (4 beats) on
    score, so WITHOUT the dusk rule the endpoint pull always takes the rich
    one — which is exactly what the evening test must see flipped.
    """
    from tests.test_tour_selection import PDV, _density_fillers, _poi, _snap

    rich_dark_bad = _poi(
        "far-rich-dark-bad", tier=5, lat=48.8555, lng=2.3720, beat_count=8
    )
    lit = _poi("far-lit-finisher", tier=5, lat=48.8560, lng=2.3718, beat_count=4)
    lit = lit.model_copy(update={"good_after_dark": True})
    # Five fillers on a TIGHT 80 m spiral, not the duration-scaled default:
    # the default spread's zig-zag chain alone cost ~1,027 s of the 1,440 s
    # walk budget (probed), so with a ~674 s finisher leg the pull abandoned
    # BOTH finishers and the dusk rank was never exercised — the far stop
    # that then appeared last was the dusk-blind fill pass's, not the pull's.
    # A tight cluster keeps the chain cheap so BOTH finishers are comfortably
    # affordable and rank alone — score undated, dusk-then-score after dusk —
    # decides the finish. The COUNT is the helper's own (derived from the
    # planner's dwell target and stop ceiling): it was hand-pinned at five for
    # the 270 s ceiling, and Phase 6 S6.6's three-minute tellings (ceiling 180 s)
    # left that pool at fill 0.50 — density RED, the dusk rule never reached.
    # Re-derived at the Phase 6 close; the three dusk assertions measured
    # identical at the derived count (dark-only evening ends at the dark
    # finisher with one dusk note; lit evening ends lit; undated ends dark).
    fillers = _density_fillers(PDV, duration_min=60, radius_m=80)
    pois = [rich_dark_bad, lit] if with_lit_finisher else [rich_dark_bad]
    return _snap([*pois, *fillers])


def _dusk_request(start_datetime):
    from tests.test_tour_selection import PDV

    return TourInput(
        start=PDV,
        duration_min=60,
        city_slug="paris",
        round_trip=False,
        start_datetime=start_datetime,
    )


def test_an_after_dusk_day_finishes_at_a_lit_place_instead_of_the_richer_dark_one():
    """Sofia's swap rule, scoped to what plan-time knows (plan S3.7; design
    §4.3's dusk trigger; docs/personas/11-solo-after-dark; row 6.4 consumed):
    a dated December day that runs past civil dusk must not END at the
    highest-scoring finisher when that place is bad after dark — the pull
    prefers the lit alternative, WITHOUT asking the user anything (Sofia's
    never-ask rule). The morning sibling proves the preference is
    dusk-gated: same corpus, 09:00 start, and the richer finisher is back.
    """
    from src.tour.selection import select_route

    evening = select_route(_dusk_request(_DEC_EVENING), _dusk_corpus(with_lit_finisher=True))
    assert evening.pois[-1].id == "far-lit-finisher", (
        f"evening day ended at {evening.pois[-1].id} — the dusk preference did not "
        "reach the endpoint pull"
    )

    morning = select_route(_dusk_request(_DEC_MORNING), _dusk_corpus(with_lit_finisher=True))
    assert morning.pois[-1].id == "far-rich-dark-bad", (
        "the morning sibling must keep today's score ranking — the dusk rule may "
        "only bind after dusk"
    )


def test_a_dark_finish_nothing_could_fix_is_disclosed():
    """When NO lit finisher exists, the dark one still serves — a rank
    preference is not a gate — and the day SAYS SO (plan S3.7: "DISCLOSED
    when no passing finisher exists"). Four confident stops beside an
    unflagged dark ending would imply the ending was judged fine."""
    from src.tour.selection import select_route

    evening = select_route(_dusk_request(_DEC_EVENING), _dusk_corpus(with_lit_finisher=False))
    assert evening.pois[-1].id == "far-rich-dark-bad"
    dusk_notes = [
        e for e in evening.clock_exclusions if "after civil dusk" in e.reason
    ]
    assert len(dusk_notes) == 1
    assert dusk_notes[0].poi_id == "far-rich-dark-bad"


def test_an_undated_run_never_consults_dusk():
    """No clock = no dusk = today's behaviour, byte-identical (the S3.7
    sabotage line: consuming dark-finish on UNDATED runs is forbidden)."""
    from src.tour.selection import select_route

    undated = select_route(_dusk_request(None), _dusk_corpus(with_lit_finisher=True))
    assert undated.pois[-1].id == "far-rich-dark-bad"
    assert undated.clock_exclusions == ()


# --- end hardness reaches the budget (S1.6c) ---------------------------------


def test_firm_hardness_is_byte_identical_to_today():
    """`firm` is the default and must change NOTHING (design §2.3: "the
    default. Honest planning to the clock; the ceiling behaviour the in-flight
    work already built")."""
    from src.tour.routing import route_planning_budget

    assert route_planning_budget(60, end_hardness="firm") == route_planning_budget(60)


def test_open_hardness_drops_the_minimum_elapsed_floor_to_zero():
    """Under `open`, a two-ish-hours request is never padded toward a number
    nobody defended: the minimum-elapsed floor goes to zero and nothing else
    moves. Cites design §2.3 (Julien's two-ish hours; Camille's "is 15:00 a
    wall or a wish?" — "a hard end time is a preference enforced as a fact")
    and design 8.3 (this finishes the fill-the-requested-time deletion)."""
    from src.tour.routing import route_planning_budget

    firm = route_planning_budget(120)
    open_ended = route_planning_budget(120, end_hardness="open")

    assert open_ended.minimum_elapsed_seconds == 0
    assert open_ended.nominal_elapsed_seconds == firm.nominal_elapsed_seconds
    assert open_ended.maximum_elapsed_seconds == firm.maximum_elapsed_seconds
    assert open_ended.walk_budget_seconds == firm.walk_budget_seconds
    assert open_ended.dwell_target_seconds == firm.dwell_target_seconds


def test_wall_hardness_plans_to_a_095_ceiling_with_visible_slack():
    """Under `wall` the plan aims at 95% of the asked time and may not exceed
    it, so the plan carries VISIBLE spare minutes — never a hard truncation at
    the asked number. Cites docs/personas/04-layover-sprinter.md bullet 2
    (Marcus: "2h40 of tour with 20 minutes of slack" beats "3h00 exactly") and
    design §2.3 (his 16:40 train; he currently lies about his end time to
    manufacture margin)."""
    from src.tour.routing import (
        DWELL_FRACTION,
        WALK_FRACTION,
        route_planning_budget,
    )

    firm = route_planning_budget(180)
    wall = route_planning_budget(180, end_hardness="wall")
    requested = 180 * 60

    assert wall.maximum_elapsed_seconds == round(requested * 0.95)
    assert wall.nominal_elapsed_seconds == round(requested * 0.95)
    assert wall.minimum_elapsed_seconds == firm.minimum_elapsed_seconds
    assert wall.walk_budget_seconds == round(requested * 0.95 * WALK_FRACTION)
    assert wall.dwell_target_seconds == round(requested * 0.95 * DWELL_FRACTION)
    assert wall.maximum_elapsed_seconds < firm.maximum_elapsed_seconds


def test_select_route_threads_the_hardness_into_its_budget():
    """The one budget `select_route` derives for the request carries the
    request's own hardness (plan S1.6c: "Threading from `TourInput` via
    `select_route`'s existing `planning_policy` derivation"). Source-scanning,
    because every helper call site legitimately keeps the firm default and a
    behaviour test cannot tell which call built the budget the repair used."""
    import ast
    from pathlib import Path

    source = (
        Path(__file__).resolve().parent.parent / "src" / "tour" / "selection.py"
    ).read_text()
    tree = ast.parse(source)
    threaded = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "route_planning_budget"
        and any(kw.arg == "end_hardness" for kw in node.keywords)
    ]
    assert threaded, (
        "no route_planning_budget call in selection.py passes end_hardness — "
        "the request's hardness never reaches the time arithmetic"
    )


def test_tour_input_clock_round_trips_through_model_dump():
    """`start_datetime` is an ISO STRING, not a datetime object:
    `tests/test_tour_contract.py::test_tour_input_end_round_trips_through_model_dump`
    does `TourInput(**inp.model_dump())`, and only a string round-trips
    (plan S1.2 sabotage list)."""
    inp = _base_input(start_datetime="2026-08-11T10:00:00", end_hardness="open")
    again = TourInput(**inp.model_dump())
    assert again == inp
