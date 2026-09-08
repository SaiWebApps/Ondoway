"""Structural guard on per-door opening hours in `data/{city}/poi-raw.json`.

Four fields say whether a place has a door and when it can be entered
(Docs/adr/0006; CONTEXT.md "Doors and hours"):

  `gated`                 bool         The door verdict.
  `opening_hours`         str | None   OpenStreetMap `opening_hours` text, read
                                       at planning time by the hours library.
                                       None = no hours held.
  `opening_hours_source`  str | None   "map" (the place's own tag) or "guess"
                                       (a model wrote it); None with None hours.
  `opening_hours_basis`   str          One sentence arguing for the value.

WHAT THIS FILE CHECKS, AND WHAT IT DELIBERATELY DOES NOT. Every assertion is
STRUCTURAL — shape, presence, grammar. None is knowledge-based: nobody in this
repo can adjudicate whether a particular museum really closes on Tuesday
(`tests/test_poi_visit_duration.py` records the same rule). Do not add a test
asserting a particular POI's real hours.

The PHASE GATE lives here too (the `test_gate_*` block): the sentences the
phase is judged by, written before anything was built.

Mirrors `tests/test_poi_visit_duration.py`. Runs in milliseconds with no
database.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = REPO_ROOT / "data"

# Cities whose corpus has been through the opening-hours pass and must therefore
# satisfy every data check below. Explicit allow-list, exactly like
# CITIES_WITH_VISIT_CAPACITY in the sibling file: listing a city that has not
# run the pass would make this file permanently red and train readers to ignore
# it.
#
# ADDING a slug here is the deliberate act of declaring "this city's hours are
# in". REMOVING one to reach green is forbidden — it deletes the guard instead
# of fixing the data.
CITIES_WITH_OPENING_HOURS: tuple[str, ...] = ("paris",)

#: The only vocabulary a door's hours may carry (CONTEXT.md "Hours source").
HOURS_SOURCES = ("map", "guess")


def _pois(city: str) -> list[dict]:
    return json.loads((DATA_ROOT / city / "poi-raw.json").read_text())


def _name(poi: dict) -> str:
    return str(poi.get("name", "<unnamed POI>"))


def _fail(city: str, headline: str, offenders: list[str], remedy: str) -> None:
    shown = offenders[:20]
    more = f"\n  ... and {len(offenders) - len(shown)} more" if len(offenders) > len(shown) else ""
    pytest.fail(
        f"{city}: {headline} ({len(offenders)} of them)\n"
        + "\n".join(f"  {line}" for line in shown)
        + more
        + f"\n{remedy}"
    )


REMEDY = "Run the opening-hours pass: `make poi-opening-hours SLUG=<city>`."


def test_every_poi_records_whether_it_is_gated() -> None:
    """Presence check #1 — the pass ran. Every POI carries the `opening_hours`
    KEY (null is a legitimate value; a missing key means the pass never
    reached this POI) and an explicit boolean `gated`. On an unpriced corpus
    this is the check that goes red, which is what stops the other bars
    reading vacuously green.
    """
    for city in CITIES_WITH_OPENING_HOURS:
        offenders = [
            f"{_name(p)}: gated={p.get('gated')!r}"
            for p in _pois(city)
            if "opening_hours" not in p or not isinstance(p.get("gated"), bool)
        ]
        if offenders:
            _fail(city, "POI(s) the opening-hours pass never reached", offenders, REMEDY)


def test_every_poi_explains_its_opening_hours() -> None:
    """Presence check #2 — every value AND every null ships with the sentence
    that argues for it. A bare null cannot be told from a shrug without it."""
    for city in CITIES_WITH_OPENING_HOURS:
        offenders = [
            f"{_name(p)}: opening_hours_basis={p.get('opening_hours_basis')!r}"
            for p in _pois(city)
            if not isinstance(p.get("opening_hours_basis"), str)
            or not p.get("opening_hours_basis", "").strip()
        ]
        if offenders:
            _fail(city, "POI(s) whose opening hours carry no reasoning", offenders, REMEDY)


# ---------------------------------------------------------------------------
# THE PHASE GATE (Docs/adr/0006): a door carries ONE hours source — map, guess,
# or unknown — as OpenStreetMap text the library can read; no verified badge,
# no ladder, no review command exists anywhere; parity compares hours on every
# target and never merely warns. These are the sentences the phase is judged
# by, written before anything was built; the milestone row that carries them
# cannot flip until every one is green.
# ---------------------------------------------------------------------------

#: The heatwave shelter file's own words — the source the decision record
#: forbids. Never a bare "heat": eight théâtres carry it.
HEATWAVE_WORDS_RE = re.compile(r"heatwave|îlot|fraîcheur|canicule", re.IGNORECASE)

#: Names the ladder left behind. Their absence from the product, the scripts,
#: the other tests, the Makefile and the make docs is the gate; this file is
#: excluded from its own grep because it names them here.
LADDER_NAMES = (
    "opening_hours_verified",
    "corroborate(",
    "review_queue",
    "poi-hours-review",
    "_verified_record",
)


def _every_paris_record() -> list[tuple[str, dict]]:
    """Every POI record in the repo's Paris data: poi-raw.json and every export
    chunk, each labelled by file — the graph is loaded from the chunks."""
    out: list[tuple[str, dict]] = []
    for city in CITIES_WITH_OPENING_HOURS:
        out += [("poi-raw.json", p) for p in _pois(city)]
        for chunk in sorted((DATA_ROOT / city / "export").glob("*.json")):
            out += [(chunk.name, p) for p in json.loads(chunk.read_text())]
    return out


def test_gate_every_door_carries_one_hours_source() -> None:
    """A door's hours come from the map or from a guess, never from anywhere
    else; a place with no hours has no source; only a door carries hours."""
    offenders = []
    for city in CITIES_WITH_OPENING_HOURS:
        for poi in _pois(city):
            hours, source = poi.get("opening_hours"), poi.get("opening_hours_source")
            if hours is None and source is not None:
                offenders.append(f"{_name(poi)}: no hours but source={source!r}")
            if hours is not None and source not in HOURS_SOURCES:
                offenders.append(f"{_name(poi)}: source={source!r} is not one of {HOURS_SOURCES}")
            if hours is not None and poi.get("gated") is not True:
                offenders.append(f"{_name(poi)}: carries hours but is not a door")
    assert not offenders, "\n".join(offenders)


def test_gate_hours_are_map_text_the_library_reads() -> None:
    """Every stored hours value is OpenStreetMap text: a string the library
    parses, carrying no quoted comment (a comment reads as OPEN to the
    library, so it is never stored — unknown is the honest answer)."""
    from opening_hours import OpeningHours

    offenders = []
    for city in CITIES_WITH_OPENING_HOURS:
        for poi in _pois(city):
            hours = poi.get("opening_hours")
            if hours is None:
                continue
            if not isinstance(hours, str):
                offenders.append(f"{_name(poi)}: hours are {type(hours).__name__}, not text")
                continue
            if '"' in hours:
                offenders.append(f"{_name(poi)}: hours carry a comment: {hours!r}")
                continue
            try:
                OpeningHours(hours)
            except Exception as exc:  # the library's own parser error
                offenders.append(f"{_name(poi)}: {hours!r} does not parse ({exc})")
    assert not offenders, "\n".join(offenders)


def test_gate_no_record_carries_a_verified_badge() -> None:
    """The word is retired: no Paris record, raw or export, carries the key."""
    offenders = [
        f"{file}: {_name(poi)}"
        for file, poi in _every_paris_record()
        if "opening_hours_verified" in poi
    ]
    assert not offenders, f"{len(offenders)} record(s) still carry opening_hours_verified"


def test_gate_no_basis_names_the_heatwave_file() -> None:
    offenders = [
        f"{file}: {_name(poi)}"
        for file, poi in _every_paris_record()
        if HEATWAVE_WORDS_RE.search(str(poi.get("opening_hours_basis") or ""))
    ]
    assert not offenders, "\n".join(offenders)


def test_gate_the_ladder_is_gone() -> None:
    """No product code, script, other test, make target or make doc names the
    verification ladder or the review command."""
    roots = [REPO_ROOT / "src", REPO_ROOT / "scripts", REPO_ROOT / "tests"]
    files = [p for root in roots for p in root.rglob("*.py")]
    files += [REPO_ROOT / "Makefile", REPO_ROOT / "docs" / "MAKE_TARGETS.md"]
    this_file = Path(__file__).resolve()
    offenders = []
    for path in files:
        if path.resolve() == this_file:
            continue
        text = path.read_text(errors="replace")
        for name in LADDER_NAMES:
            if name in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {name}")
    assert not offenders, "\n".join(offenders)


def test_gate_parity_compares_hours_by_source_on_every_target() -> None:
    """scripts/db_parity.py compares (key, gated, source, text) for every door
    and never downgrades a lane to a warning: production carries the same
    hours as dev, or parity fails."""
    import importlib

    parity = importlib.import_module("scripts.db_parity")
    expected = parity._expected("paris")
    assert "hours" in expected, "db_parity's expected set carries no hours lane"
    rows = expected["hours"]
    assert rows and all(len(row) == 4 for row in rows), (
        "the hours lane is (key, gated, source, text)"
    )
    source = (REPO_ROOT / "scripts" / "db_parity.py").read_text()
    assert "warn_only" not in source, "a parity lane still merely warns on the cloud"


# ---------------------------------------------------------------------------
# The pass's own rules, on fixtures (no network, no model, no DB).
# ---------------------------------------------------------------------------


def test_only_text_the_library_reads_is_kept_and_a_comment_is_unknown() -> None:
    from scripts.poi_opening_hours import readable_hours

    orsay = "Tu-Su 09:30-18:00; Th 09:30-21:45"
    assert readable_hours(orsay) == orsay
    assert readable_hours("Mo-Su 10:00-18:00; Jan 1,May 1,Dec 25: off") is not None
    assert readable_hours("24/7") == "24/7"
    assert readable_hours('"Fermé pour travaux jusqu\'à novembre 2028"') is None
    assert readable_hours('Mo-Su 10:00-18:00; "closed for renovation"') is None
    assert readable_hours("Palais du Luxembourg Mo-Fr 08:00-19:00") is None
    assert readable_hours(None) is None
    assert readable_hours({"mon": []}) is None


def test_the_scoped_classes_match_first_and_the_wide_net_needs_an_equal_name() -> None:
    """A scoped element claims a door by containment (today's rule); the wide
    set — every shop in the city — claims one only by an EQUAL name, so the
    Luxembourg Gardens never take the Luxembourg Museum's hours and a café
    named after the cathedral never claims the cathedral."""
    from scripts.poi_opening_hours import match_osm

    gardens = {"name": "Jardin du Luxembourg", "latitude": 48.8462, "longitude": 2.3372}
    cathedral = {"name": "Notre-Dame Cathedral", "latitude": 48.8530, "longitude": 2.3499}
    def element(name: str, lat: float, lng: float, hours: str) -> dict:
        return {"name": name, "lat": lat, "lng": lng, "opening_hours": hours}

    scoped = [element("Jardin du Luxembourg", 48.8463, 2.3371, "07:30-21:00")]
    wide = [
        element("Musée du Luxembourg", 48.8463, 2.3371, "10:30-19:00"),
        element("Café Notre-Dame", 48.8531, 2.3498, "08:00-02:00"),
        element("Notre Dame Cathedral", 48.8531, 2.3498, "08:00-19:00"),
    ]
    matched = match_osm([gardens, cathedral], scoped, wide)
    assert matched == {
        "Jardin du Luxembourg": "07:30-21:00",
        "Notre-Dame Cathedral": "08:00-19:00",
    }


def test_the_map_writes_only_onto_doors_and_over_any_guess() -> None:
    """The map's tag lands on a door (over any guess), never on a place with
    no door; an unreadable tag leaves the door unknown."""
    from scripts.poi_opening_hours import apply_map_hours

    door = {"name": "Museum", "gated": True, "opening_hours": None, "opening_hours_source": None,
            "opening_hours_basis": "gated, hours not confidently known; left unfiltered."}
    guessed = {"name": "Chapel", "gated": True, "opening_hours": "Mo-Su 10:00-17:00",
               "opening_hours_source": "guess", "opening_hours_basis": "a guess"}
    square = {"name": "Square", "gated": False, "opening_hours": None, "opening_hours_source": None,
              "opening_hours_basis": "open square"}
    unreadable = {"name": "Works", "gated": True, "opening_hours": None,
                  "opening_hours_source": None, "opening_hours_basis": "gated, unknown"}
    counts = apply_map_hours(
        [door, guessed, square, unreadable],
        {
            "Museum": "Tu-Su 10:00-18:00",
            "Chapel": "Mo-Sa 09:00-12:00",
            "Square": "24/7",
            "Works": '"Fermé pour travaux"',
        },
    )
    assert (door["opening_hours"], door["opening_hours_source"]) == ("Tu-Su 10:00-18:00", "map")
    assert (guessed["opening_hours"], guessed["opening_hours_source"]) == (
        "Mo-Sa 09:00-12:00", "map",
    )
    assert square["opening_hours"] is None and square["opening_hours_source"] is None
    assert unreadable["opening_hours"] is None and unreadable["opening_hours_source"] is None
    assert counts == {"map": 2, "unreadable": 1}


def test_a_model_record_is_validated_through_the_library() -> None:
    from scripts.poi_opening_hours import validate

    good = {"gated": True, "opening_hours": "Mo-Su 09:00-18:00", "opening_hours_basis": "site"}
    assert validate(good, name="x") is None
    prose = {"gated": True, "opening_hours": "open most mornings", "opening_hours_basis": "site"}
    assert "not OpenStreetMap text" in (validate(prose, name="x") or "")
    ungated = {"gated": False, "opening_hours": "Mo-Su 09:00-18:00", "opening_hours_basis": "b"}
    assert "gated=false" in (validate(ungated, name="x") or "")
    verdict_only = {"gated": "yes"}
    assert "explicit true/false" in (validate(verdict_only, name="x", gated_only=True) or "")


# ---------------------------------------------------------------------------
# The plumbing: the same three hops the visit-capacity fields travel, tested
# ONE AT A TIME so a failure names the hop that ate the field
# (`tests/test_poi_visit_duration.py` is the pattern). `place_category`
# (data row 6.7) rides the identical plumbing and is asserted alongside.
# ---------------------------------------------------------------------------

CLOCK_FIELDS = (
    "gated",
    "opening_hours",
    "opening_hours_source",
    "opening_hours_basis",
    "place_category",
)


def test_hop_1_the_corpus_query_asks_the_graph_for_the_clock_fields() -> None:
    """HOP 1 — `LOAD_PARIS_POIS_CYPHER` returns ONLY what it names; a property
    absent from its RETURN list reaches nothing downstream, with no error."""
    from src.tour.selection import LOAD_PARIS_POIS_CYPHER

    for field in CLOCK_FIELDS:
        assert f"p.{field}" in LOAD_PARIS_POIS_CYPHER, (
            f"HOP 1: the corpus query never asks the graph for `{field}`, so it can "
            f"never reach the planner however well the rest of the chain works."
        )


def test_hop_2_the_snapshot_builder_carries_the_clock_fields_onto_the_poi() -> None:
    """HOP 2/3 — `_snapshot_from_records` sets fields one by one, onto a `POI`
    that declares them. `POI` is `extra="ignore"` (src/tour/contract.py), so an
    unknown keyword is DISCARDED WITHOUT ERROR — which is why this asserts
    VALUES on the built object, never merely that construction succeeded.

    `opening_hours` arrives from the graph as the OpenStreetMap text the
    upload wrote; the text is what the POI carries.
    """
    from src.tour.selection import _snapshot_from_records

    text = "Mo,We-Su 09:00-18:00; Tu off"
    record = {
        "id": "musee-test",
        "name": "Musée Test",
        "tier": 4,
        "poi_role": "stop",
        "lat": 48.86,
        "lng": 2.33,
        "areas": [],
        "gated": True,
        "opening_hours": text,
        "opening_hours_source": "map",
        "opening_hours_basis": "Museum; the place's own OpenStreetMap tag.",
        "place_category": "museum",
    }
    poi = _snapshot_from_records([record], [], [], []).pois[0]

    assert poi.gated is True, "HOP 2/3: the record carried gated but the POI does not"
    assert poi.opening_hours == text, (
        "HOP 2/3: the record carried opening_hours but the POI does not. Either "
        "_snapshot_from_records does not pass it, or POI does not declare it."
    )
    assert poi.opening_hours_source == "map", (
        "HOP 2/3: the record carried opening_hours_source but the POI does not."
    )
    assert poi.opening_hours_basis.startswith("Museum;"), (
        "HOP 2/3: the record carried opening_hours_basis but the POI does not."
    )
    assert poi.place_category == "museum", (
        "HOP 2/3: the record carried place_category but the POI does not."
    )


def test_hop_the_closed_report_count_rides_the_corpus_query_and_the_loader() -> None:
    """Docs/adr/0006: a walker's closed report on a guessed door is counted on
    the place (``hours_closed_reports``) so the next walker hears a stronger
    warning. The count is written at run time, never by the upload — so it is
    NOT one of ``CLOCK_FIELDS`` (that tuple drives the upload's SET list, and an
    upload must never reset a runtime counter). Its own three hops: the corpus
    query asks for it, the loader carries it, and a record without it lands on
    zero."""
    from src.tour.selection import LOAD_PARIS_POIS_CYPHER, _snapshot_from_records

    assert "p.hours_closed_reports" in LOAD_PARIS_POIS_CYPHER
    base = {
        "id": "door",
        "name": "A door",
        "tier": 4,
        "poi_role": "stop",
        "lat": 48.86,
        "lng": 2.33,
        "areas": [],
    }
    reported = _snapshot_from_records([{**base, "hours_closed_reports": 2}], [], [], []).pois[0]
    assert reported.hours_closed_reports == 2
    fresh = _snapshot_from_records([{**base, "hours_closed_reports": None}], [], [], []).pois[0]
    assert fresh.hours_closed_reports == 0


def test_hop_2_an_unpriced_record_lands_on_the_safe_defaults() -> None:
    """A corpus written before the opening-hours pass must still load: every
    field arrives as None from Neo4j and must land on None / None / "" / "" —
    None hours means NEVER CLOCK-EXCLUDED, the safe direction (a place is
    included on a day it is closed, never excluded on a day it is open), and
    a gated of None is NO CLAIM, never a claim of no door."""
    from src.tour.selection import _snapshot_from_records

    record = {
        "id": "unpriced",
        "name": "A place with no hours yet",
        "tier": 3,
        "poi_role": "stop",
        "lat": 48.8566,
        "lng": 2.3522,
        "areas": [],
        "gated": None,
        "opening_hours": None,
        "opening_hours_source": None,
        "opening_hours_basis": None,
        "place_category": None,
    }
    poi = _snapshot_from_records([record], [], [], []).pois[0]
    assert poi.gated is None
    assert poi.opening_hours is None
    assert poi.opening_hours_source is None
    assert poi.opening_hours_basis == ""
    assert poi.place_category == ""


def test_the_upload_carries_the_clock_fields_in_both_property_lists() -> None:
    """S1.4d's hop — `scripts/upload_paris.py` keeps TWO hardcoded property
    lists (the param dict and the Cypher SET list) whose own comment warns they
    must agree or a field silently never reaches the graph. Both must carry all
    the clock fields (the `test_golden_diff_cli_reads_the_durable_key` genre:
    read the script source, assert on both lists)."""
    source = (REPO_ROOT / "scripts" / "upload_paris.py").read_text()
    for field in CLOCK_FIELDS:
        assert f'"{field}"' in source, (
            f"upload param dict never carries `{field}` — it will never reach the graph"
        )
        assert f"p.{field}" in source, (
            f"upload Cypher SET list never writes `p.{field}` — the param is carried "
            "and then silently dropped at the write"
        )
