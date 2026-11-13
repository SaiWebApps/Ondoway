"""Structural guard on a city's body places in `data/{city}/body-places.json`.

Body places are the toilets and benches a day is physically built around.
Body stops are promises: a rest window with no bench under it is just standing.
A family with small children needs a toilet stop with no story in it, and that
stop cannot be skipped. A visitor who cannot manage steps needs a bench that IS
a stop, with a location and a duration, and may choose a tour for a toilet
they know about.

`scripts/poi_body_places.py` collects them from OSM at $0 into a file that is
deliberately SEPARATE from `poi-raw.json`: a body place is not a narrated place
and must never enter the beat pipeline. One record per OSM node:

  `id`        str    "body-toilet-<osmid>" | "body-bench-<osmid>", unique
  `kind`      str    "toilet" | "bench" — nothing else
  `lat`/`lng` float  inside the city's registry bbox
  `poi_role`  str    always "body"

WHAT THIS FILE CHECKS. Every per-city assertion is STRUCTURAL — presence,
vocabulary, bbox, uniqueness — over an allow-list of cities whose pass has run
(the `tests/test_poi_opening_hours.py` pattern, including its in-body loop: the
suite hard-errors on skips, so nothing here parametrizes over a possibly-empty
tuple). The three HOP tests at the bottom guard the road a record travels to the
planner; the ones marked RED stay red until the planner/upload half lands.
Runs in milliseconds with no database.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = REPO_ROOT / "data"

BODY_KINDS = ("toilet", "bench")

# Cities whose body-places pass has run and must therefore satisfy every data
# check below. Explicit allow-list, exactly like CITIES_WITH_OPENING_HOURS in
# the sibling file: listing a city that has not run the pass would make this
# file permanently red and train readers to ignore it. EMPTY until the first
# pass runs (adding "paris" is the declaring act).
#
# ADDING a slug here is the deliberate act of declaring "this city's body
# places are in". REMOVING one to reach green is forbidden — it deletes the
# guard instead of fixing the data.
CITIES_WITH_BODY_PLACES: tuple[str, ...] = ("paris",)


def _records(city: str) -> list[dict]:
    return json.loads((DATA_ROOT / city / "body-places.json").read_text())


def _fail(city: str, headline: str, offenders: list[str], remedy: str) -> None:
    shown = offenders[:20]
    more = f"\n  ... and {len(offenders) - len(shown)} more" if len(offenders) > len(shown) else ""
    pytest.fail(
        f"{city}: {headline} ({len(offenders)} of them)\n"
        + "\n".join(f"  {line}" for line in shown)
        + more
        + f"\n{remedy}"
    )


REMEDY = "Run the body-places pass: `uv run python -m scripts.poi_body_places --slug <city>`."


def test_body_places_file_exists_and_is_a_list() -> None:
    """The pass ran: the file exists and parses as a JSON list. On a city whose
    pass never ran this is the check that goes red, which is what stops every
    other bar below reading vacuously green (body stops are promises, so the
    data behind them is load-bearing)."""
    for city in CITIES_WITH_BODY_PLACES:
        path = DATA_ROOT / city / "body-places.json"
        assert path.exists(), f"{city}: no body-places.json — {REMEDY}"
        assert isinstance(json.loads(path.read_text()), list), (
            f"{city}: body-places.json is not a JSON list — {REMEDY}"
        )


def test_every_body_place_carries_the_full_record() -> None:
    """Every record carries id, kind, numeric lat/lng, and poi_role "body" —
    the shape the upload maps onto the graph. A half-formed record would
    surface far downstream as a bench with no location, which for a visitor
    who needs to sit is exactly no bench."""
    for city in CITIES_WITH_BODY_PLACES:
        offenders = []
        for i, rec in enumerate(_records(city)):
            if not isinstance(rec.get("id"), str) or not rec.get("id"):
                offenders.append(f"record {i}: id={rec.get('id')!r}")
            if "kind" not in rec:
                offenders.append(f"record {i}: no kind")
            if not isinstance(rec.get("lat"), int | float) or not isinstance(
                rec.get("lng"), int | float
            ):
                offenders.append(f"{rec.get('id', i)}: lat/lng not numeric")
            if rec.get("poi_role") != "body":
                offenders.append(f"{rec.get('id', i)}: poi_role={rec.get('poi_role')!r}")
        if offenders:
            _fail(city, "half-formed body-place record(s)", offenders, REMEDY)


def test_kind_is_only_toilet_or_bench() -> None:
    """The kind vocabulary is closed: "toilet" | "bench", nothing else. The two
    kinds are the two body needs visitors have — a toilet and a bench to rest
    on — and a third value would reach the cadence axis untyped."""
    for city in CITIES_WITH_BODY_PLACES:
        offenders = [
            f"{rec.get('id', i)}: kind={rec.get('kind')!r}"
            for i, rec in enumerate(_records(city))
            if rec.get("kind") not in BODY_KINDS
        ]
        if offenders:
            _fail(city, "body place(s) of an unknown kind", offenders, REMEDY)


def test_coordinates_sit_inside_the_city_bbox() -> None:
    """Every record sits inside its own city's registry bbox (the one bbox
    table, `src.city_registry.bbox_map()`). A bench outside the geofence can
    never be walked to inside the tour envelope, and its presence would mean
    the fetch queried the wrong box (data conventions: all queries scoped to
    the city geofence, never global)."""
    from src.city_registry import bbox_map

    for city in CITIES_WITH_BODY_PLACES:
        min_lat, max_lat, min_lng, max_lng = bbox_map()[city]
        offenders = [
            f"{rec.get('id', i)}: ({rec.get('lat')}, {rec.get('lng')})"
            for i, rec in enumerate(_records(city))
            if not (
                isinstance(rec.get("lat"), int | float)
                and isinstance(rec.get("lng"), int | float)
                and min_lat <= rec["lat"] <= max_lat
                and min_lng <= rec["lng"] <= max_lng
            )
        ]
        if offenders:
            _fail(city, "body place(s) outside the city bbox", offenders, REMEDY)


def test_ids_are_unique() -> None:
    """No id appears twice. The id is the stable key the upload MERGEs on, so a
    duplicate would silently collapse two physical places into one graph node
    (data conventions: MERGE keys are multi-city safe and collision-free)."""
    for city in CITIES_WITH_BODY_PLACES:
        seen: set[str] = set()
        offenders = []
        for rec in _records(city):
            rid = rec.get("id")
            if rid in seen:
                offenders.append(f"{rid}")
            seen.add(rid)
        if offenders:
            _fail(city, "duplicate body-place id(s)", offenders, REMEDY)


# ---------------------------------------------------------------------------
# The hops: the road a body place travels from file to planner, one guard per
# hop so a failure names the hop that ate it (the
# `tests/test_poi_opening_hours.py` hop-test pattern).
# ---------------------------------------------------------------------------


def test_hop_upload_carries_body_places_with_the_body_role() -> None:
    """HOP (upload) — RED until the planner/upload half lands.

    `scripts/upload_paris.py` must read `body-places.json` and bind poi_role
    "body" in its body-places section, or no record in the file ever reaches
    the graph and the planner's "body" pricing guards nothing. Source-scanning
    (the `test_the_upload_carries_the_clock_fields_in_both_property_lists`
    genre) because an upload gap is silent by construction. Body stops are
    promises, and a promise the graph cannot see cannot be kept."""
    source = (REPO_ROOT / "scripts" / "upload_paris.py").read_text()
    assert "body-places.json" in source, (
        "upload never reads body-places.json — no body place can reach the graph"
    )
    assert re.search(r"""poi_role["']?\s*[:=]\s*["']body["']""", source), (
        "upload never binds poi_role \"body\" — body places would land in the graph "
        "role-less and be priced like narrated stops"
    )


def test_hop_planner_a_body_place_never_seats_as_a_narrated_stop() -> None:
    """HOP (planner) — may already hold via the zero-beat gate, in which case
    it stands as the invariant's guard.

    A hermetic POI with `poi_role="body"` and no beats seats NOWHERE without
    the cadence axis: today's planner must never spend a dwell slot on a
    toilet, even a tier-5 one beside the start. The whole point of the body
    role is that these places are scheduled by the body clock, not the story
    ranking — a toilet stop has no story in it, so as a narrated stop it would
    be a contentless hole in the tour."""
    from src.tour.contract import POI, TourInput
    from src.tour.selection import select_route
    from tests.test_tour_selection import PDV, _density_fillers, _snap

    body = POI(
        id="body-toilet-osm1",
        name="Public toilet",
        tier=5,  # even at the top tier a body place must not out-rank a story
        poi_role="body",
        lat=PDV[0],
        lng=PDV[1],
        areas=("Paris",),
        beat_count=0,
    )
    corpus = _snap([body, *_density_fillers(PDV)])
    request = TourInput(start=PDV, duration_min=60, city_slug="paris")

    route = select_route(request, corpus)

    assert body.id not in {p.id for p in route.pois}, (
        "a poi_role='body' POI was seated as a narrated stop — body places may only "
        "ever be scheduled by the cadence axis, never by the story ranking"
    )


def test_rest_cadence_seats_the_nearest_bench_on_a_long_stretch() -> None:
    """With `rest_cadence_minutes` set, a stretch of walking longer than the
    cadence gets the nearest body place seated as a stop — the seating rule
    (`src.tour.selection._seat_body_stops`).

    Fixture: a tight cluster near the start plus one PINNED far anchor forces a
    single ~15-minute leg (over an 8-minute cadence); a bench sits at that
    leg's midpoint. The bench IS a stop: a rest window with no bench under it
    is just standing.

    The far anchor used to be taken on merit
    because eight 270 s fillers could not fill ninety minutes without it; since
    tellings became three minutes the cluster either cannot build the day
    (≤16 fillers) or fills it alone (≥17) and the far leg never happens. A pin
    is a certainty — it is the honest way to GUARANTEE the long
    stretch at any ceiling — and the filler count is the helper's own, derived
    from the planner's dwell target and stop ceiling.
    """
    from src.tour.contract import POI, TourInput
    from src.tour.selection import select_route
    from tests.test_tour_selection import PDV, _density_fillers, _poi, _snap

    fillers = _density_fillers(PDV, duration_min=90, round_trip=True, radius_m=70.0)
    far_anchor = _poi("far-anchor", lat=PDV[0] + 0.0055, lng=PDV[1], beat_count=15)
    by_lat_lng = {p.id: (p.lat, p.lng) for p in [far_anchor, *fillers]}
    f4 = by_lat_lng["filler-4"]
    mid = ((f4[0] + far_anchor.lat) / 2.0, (f4[1] + far_anchor.lng) / 2.0)
    bench = POI(
        id="bench-1", name="Bench", tier=1, poi_role="body",
        lat=mid[0], lng=mid[1], beat_count=0,
    )
    corpus = _snap([far_anchor, bench, *fillers])

    no_cadence = select_route(
        TourInput(
            start=PDV, duration_min=90, city_slug="paris", round_trip=True,
            pinned_poi_ids=("far-anchor",),
        ),
        corpus,
    )
    assert "bench-1" not in {p.id for p in no_cadence.pois}, (
        "fixture premise: with no cadence axis the bench must not seat on its own "
        "(POI_ROLE_MULTIPLIER prices 'body' at 0.0)"
    )

    cadenced = select_route(
        TourInput(
            start=PDV, duration_min=90, city_slug="paris", round_trip=True,
            pinned_poi_ids=("far-anchor",), rest_cadence_minutes=8,
        ),
        corpus,
    )
    ids = [p.id for p in cadenced.pois]
    assert "bench-1" in ids, "the long stretch must get the nearest bench seated"
    assert ids.index("bench-1") < ids.index("far-anchor"), (
        "the bench sits BEFORE the far anchor on the leg that crossed the cadence"
    )


def test_hop_score_poi_role_multiplier_prices_body_at_zero() -> None:
    """HOP (score) — RED until the planner/upload half lands.

    `POI_ROLE_MULTIPLIER` must map "body" to exactly 0.0. The zero is the
    contract body places hang on: once the upload puts body places in
    the graph, this one number is what keeps the story ranking from ever
    scoring them, while the cadence axis (a later step) seats them by need.
    A bench is a scheduled item, not a scored sight."""
    from src.tour.selection import POI_ROLE_MULTIPLIER

    assert POI_ROLE_MULTIPLIER.get("body") == 0.0, (
        f"POI_ROLE_MULTIPLIER has no 0.0 'body' entry (got "
        f"{POI_ROLE_MULTIPLIER.get('body')!r}) — uploaded body places would be "
        "scoreable by the story ranking"
    )
