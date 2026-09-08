"""Structural guard on per-POI opening hours in `data/{city}/poi-raw.json`.

Three fields say WHEN a place can be entered (redesign data row 6.1 — "Aiko's
locked door, Rosemary's Tuesday", specs/2026-08-07-tour-algorithm-redesign):

  `opening_hours`         dict | None  Week table: all seven keys `mon`..`sun`,
                                       each a list of ["HH:MM","HH:MM"] open
                                       windows; [] = closed that whole day.
                                       None = NOT GATED (a street, a square, a
                                       bridge) — the same load-bearing null
                                       `visit_seconds_inside` uses.
  `opening_hours_source`  str | None   "osm" | "ai" when hours exist; None when
                                       the place is not gated.
  `opening_hours_basis`   str          One sentence arguing for the table (or
                                       for the null), judgeable by someone who
                                       has never been to the city.

WHAT THIS FILE CHECKS, AND WHAT IT DELIBERATELY DOES NOT. Every assertion is
STRUCTURAL — shape, presence, window sanity. None is knowledge-based: nobody in
this repo can adjudicate whether a particular museum really closes on Tuesday
(`tests/test_poi_visit_duration.py` records the same rule, and its command doc
forbids knowledge tests in writing). The basis sentence plus human review is the
mechanism for wrong-but-well-formed tables; do not add a test asserting a
particular POI's real hours.

Mirrors `tests/test_poi_visit_duration.py` (whose header invites the sibling).
Runs in milliseconds with no database.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = REPO_ROOT / "data"

DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# Cities whose corpus has been through the opening-hours pass and must therefore
# satisfy every data check below. Explicit allow-list, exactly like
# CITIES_WITH_VISIT_CAPACITY in the sibling file: listing a city that has not
# run the pass would make this file permanently red and train readers to ignore
# it. EMPTY until the first pass runs (Phase 1's W1.8 adds "paris").
#
# ADDING a slug here is the deliberate act of declaring "this city's hours are
# in". REMOVING one to reach green is forbidden — it deletes the guard instead
# of fixing the data.
CITIES_WITH_OPENING_HOURS: tuple[str, ...] = ("paris",)


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
    KEY (null is a legitimate value meaning "not gated"; a missing key means
    the pass never reached this POI). On an unpriced corpus this is the check
    that goes red, which is what stops the other bars reading vacuously green.
    """
    for city in CITIES_WITH_OPENING_HOURS:
        offenders = [_name(p) for p in _pois(city) if "opening_hours" not in p]
        if offenders:
            _fail(city, "POI(s) the opening-hours pass never reached", offenders, REMEDY)


def test_every_poi_explains_its_opening_hours() -> None:
    """Presence check #2 — every table AND every null ships with the sentence
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


def test_every_gated_poi_carries_a_source_and_a_basis() -> None:
    """A non-null table must say where it came from ("osm" | "ai") and why.

    The basis clause deliberately duplicates the presence check above: a guard
    that goes quiet when its data disappears is not a guard
    (`tests/test_poi_visit_duration.py` records the same duplication rule).
    """
    for city in CITIES_WITH_OPENING_HOURS:
        offenders = []
        for poi in _pois(city):
            if poi.get("opening_hours") is None:
                continue
            source = poi.get("opening_hours_source")
            basis = poi.get("opening_hours_basis")
            if source not in ("osm", "ai"):
                offenders.append(f"{_name(poi)}: opening_hours_source={source!r}")
            if not isinstance(basis, str) or not basis.strip():
                offenders.append(f"{_name(poi)}: opening_hours_basis={basis!r}")
        if offenders:
            _fail(city, "gated POI(s) with no source or no basis", offenders, REMEDY)


def test_opening_tables_parse() -> None:
    """Every non-null table has exactly the seven day keys and well-formed
    ["HH:MM","HH:MM"] windows — the shape the clock filter (S1.6) parses."""
    for city in CITIES_WITH_OPENING_HOURS:
        offenders = []
        for poi in _pois(city):
            hours = poi.get("opening_hours")
            if hours is None or "opening_hours" not in poi:
                continue
            if not isinstance(hours, dict) or set(hours) != set(DAY_KEYS):
                offenders.append(
                    f"{_name(poi)}: keys="
                    f"{sorted(hours) if isinstance(hours, dict) else hours!r}"
                )
                continue
            for day in DAY_KEYS:
                windows = hours[day]
                if not isinstance(windows, list):
                    offenders.append(f"{_name(poi)}: {day} is not a list")
                    continue
                for window in windows:
                    if (
                        not isinstance(window, list)
                        or len(window) != 2
                        or not all(
                            isinstance(t, str) and len(t) == 5 and t[2] == ":" for t in window
                        )
                    ):
                        offenders.append(f"{_name(poi)}: {day} window {window!r}")
        if offenders:
            _fail(city, "POI(s) whose opening table does not parse", offenders, REMEDY)


def test_no_zero_length_open_window() -> None:
    """No open window may be zero-length or backwards: a window that ends when
    it starts admits nobody, and the clock filter would read it as 'open'."""
    for city in CITIES_WITH_OPENING_HOURS:
        offenders = []
        for poi in _pois(city):
            hours = poi.get("opening_hours")
            if not isinstance(hours, dict):
                continue
            for day, windows in hours.items():
                if not isinstance(windows, list):
                    continue
                for window in windows:
                    if (
                        isinstance(window, list)
                        and len(window) == 2
                        and all(isinstance(t, str) for t in window)
                        and window[0] >= window[1]
                    ):
                        offenders.append(f"{_name(poi)}: {day} {window!r}")
        if offenders:
            _fail(city, "POI(s) with a zero-length or backwards open window", offenders, REMEDY)


# ---------------------------------------------------------------------------
# The plumbing: the same three hops the visit-capacity fields travel, tested
# ONE AT A TIME so a failure names the hop that ate the field
# (`tests/test_poi_visit_duration.py` is the pattern). `place_category`
# (data row 6.7) rides the identical plumbing and is asserted alongside.
# ---------------------------------------------------------------------------

CLOCK_FIELDS = (
    "opening_hours",
    "opening_hours_source",
    "opening_hours_basis",
    "place_category",
)

#: The trust half of the clock (Docs/adr/0003): whether the place has a door at
#: all, and who verified its hours on what evidence. Ride the same three hops.
TRUST_FIELDS = (
    "gated",
    "opening_hours_verified",
)

#: Cities whose corpus has been through the gated-verdict half of the pass and
#: must therefore satisfy the presence checks below. Same allowlist discipline
#: as CITIES_WITH_OPENING_HOURS above: empty until the pass runs; adding a slug
#: is the deliberate declaration, removing one to reach green is forbidden.
CITIES_WITH_GATED_VERDICTS: tuple[str, ...] = ("paris",)

#: Cities whose hours REVIEW QUEUE has been drained by a human sitting: every
#: gated, table-carrying POI carries `opening_hours_verified` (the gated
#: places whose hours nobody can confirm stay honest nulls — fail-open with
#: the spoken disclosure, per Docs/adr/0003). Same allowlist discipline:
#: adding a slug is the deliberate declaration the sitting happened; the
#: operator adds it after `make poi-hours-review` reports an empty queue.
CITIES_WITH_DRAINED_HOURS_QUEUES: tuple[str, ...] = ()


def test_a_drained_city_carries_a_verdict_on_every_table() -> None:
    """The queue-drained guard — Phase 10's gate criterion, mechanized: in a
    declared city, no gated table remains unreviewed."""
    for city in CITIES_WITH_DRAINED_HOURS_QUEUES:
        offenders = [
            _name(p)
            for p in _pois(city)
            if p.get("gated") is True
            and p.get("opening_hours") is not None
            and p.get("opening_hours_verified") is None
        ]
        if offenders:
            _fail(
                city,
                "gated table(s) still awaiting review in a drained-declared city",
                offenders,
                "Run the sitting: `make poi-hours-review SLUG=<city> APPROVER=<you>`.",
            )


def test_every_poi_records_a_gated_verdict() -> None:
    """Presence check for the trust half — every POI in a declared city carries
    an explicit boolean `gated`, ending the null-means-two-things overload."""
    for city in CITIES_WITH_GATED_VERDICTS:
        offenders = [
            f"{_name(p)}: gated={p.get('gated')!r}"
            for p in _pois(city)
            if not isinstance(p.get("gated"), bool)
        ]
        if offenders:
            _fail(city, "POI(s) with no boolean gated verdict", offenders, REMEDY)


def test_a_verified_row_is_structurally_complete() -> None:
    """`opening_hours_verified`, wherever it appears, carries the whole trust
    record: tier (0-2), approver, evidence, at. A verified badge with no
    inspectable trail is the thing the field exists to prevent."""
    for city in CITIES_WITH_OPENING_HOURS:
        offenders = []
        for poi in _pois(city):
            verified = poi.get("opening_hours_verified")
            if verified is None:
                continue
            if not isinstance(verified, dict):
                offenders.append(f"{_name(poi)}: opening_hours_verified={verified!r}")
                continue
            if verified.get("tier") not in (0, 1, 2):
                offenders.append(f"{_name(poi)}: tier={verified.get('tier')!r}")
            for key in ("approver", "evidence", "at"):
                value = verified.get(key)
                if not isinstance(value, str) or not value.strip():
                    offenders.append(f"{_name(poi)}: {key}={value!r}")
            if poi.get("opening_hours") is None:
                offenders.append(f"{_name(poi)}: verified but carries no hours table")
        if offenders:
            _fail(city, "verified row(s) missing their trust record", offenders, REMEDY)


def _gated_row(name: str, *, tier: int = 3, source: str = "osm", basis: str | None = None,
               verified: dict | None = None) -> dict:
    return {
        "name": name,
        "importance_tier": tier,
        "gated": True,
        "opening_hours": {d: [["09:00", "18:00"]] for d in DAY_KEYS},
        "opening_hours_source": source,
        "opening_hours_basis": basis
        or 'Gated museum; transcribed from OSM tag "Mo-Su 09:00-18:00".',
        "opening_hours_verified": verified,
    }


def test_tier0_corroboration_verifies_demotes_and_flags_conflicts() -> None:
    """The ladder's deterministic rung: a live tag equal to the quoted one
    tier-0-verifies; a differing live tag is a conflict AND demotes an already
    verified row (the auto-demote rule); no live tag leaves the row queued."""
    from scripts.poi_opening_hours import corroborate

    agree = _gated_row("Agree Museum")
    drifted = _gated_row(
        "Drifted Museum",
        verified={"tier": 2, "approver": "owner", "evidence": "x", "at": "2026-09-01"},
    )
    silent = _gated_row("Silent Chapel", source="ai",
                        basis="Gated chapel; published pattern from the diocese site.")
    live = {
        "Agree Museum": "Mo-Su 09:00-18:00",
        "Drifted Museum": "Mo-Su 10:00-17:00",
    }
    verified, conflicts, demoted = corroborate([agree, drifted, silent], live)
    assert verified == ["Agree Museum"]
    assert agree["opening_hours_verified"]["tier"] == 0
    assert agree["opening_hours_verified"]["approver"] == "corroboration"
    assert conflicts == ["Drifted Museum"]
    assert demoted == ["Drifted Museum"]
    assert drifted["opening_hours_verified"] is None, (
        "a re-fetch that disagrees must demote — verified means currently believed true"
    )
    assert silent["opening_hours_verified"] is None, "no live tag decides nothing"


def test_the_review_queue_orders_conflicts_then_gravity() -> None:
    """The owner's minutes land where a wrong 'open' costs the most: conflicts
    first, then importance_tier descending; verified rows are not queued."""
    from scripts.poi_opening_hours import review_queue

    small = _gated_row("A Small Place", tier=2)
    marquee = _gated_row("The Marquee", tier=5)
    conflicted = _gated_row("Conflicted", tier=1)
    done = _gated_row(
        "Already Verified",
        verified={"tier": 0, "approver": "corroboration", "evidence": "x", "at": "2026-09-07"},
    )
    queue = review_queue([small, marquee, done, conflicted], ["Conflicted"])
    assert [p["name"] for p in queue] == ["Conflicted", "The Marquee", "A Small Place"]


def test_hop_1_the_corpus_query_asks_the_graph_for_the_trust_fields() -> None:
    """HOP 1 for the trust half — a property absent from the RETURN list
    reaches nothing downstream, with no error."""
    from src.tour.selection import LOAD_PARIS_POIS_CYPHER

    for field in TRUST_FIELDS:
        assert f"p.{field}" in LOAD_PARIS_POIS_CYPHER, (
            f"HOP 1: the corpus query never asks the graph for `{field}`."
        )


def test_hop_2_the_snapshot_builder_carries_the_trust_fields_onto_the_poi() -> None:
    """HOP 2/3 for the trust half — asserted on VALUES, since POI is
    extra='ignore' and discards unknown keywords silently."""
    from src.tour.selection import _snapshot_from_records

    verified = '{"tier": 2, "approver": "owner", "evidence": "official site", "at": "2026-09-07"}'
    record = {
        "id": "musee-trust",
        "name": "Musée Trust",
        "tier": 4,
        "poi_role": "stop",
        "lat": 48.86,
        "lng": 2.33,
        "areas": [],
        "gated": True,
        "opening_hours_verified": verified,
    }
    poi = _snapshot_from_records([record], [], [], []).pois[0]
    assert poi.gated is True, "HOP 2/3: the record carried gated but the POI does not"
    assert poi.opening_hours_verified == verified, (
        "HOP 2/3: the record carried opening_hours_verified but the POI does not"
    )

    bare = _snapshot_from_records(
        [{**record, "id": "bare", "gated": None, "opening_hours_verified": None}], [], [], []
    ).pois[0]
    assert bare.gated is None, (
        "a corpus the gated pass never reached must load as None (no claim), "
        "never as False (a claim of no door)"
    )
    assert bare.opening_hours_verified is None


def test_the_upload_carries_the_trust_fields_in_both_property_lists() -> None:
    """The upload's two hardcoded property lists must both carry the trust
    fields, or they reach the graph as nothing with no error — the exact hop
    the S1 adversarial review caught missing from the plan."""
    source = (REPO_ROOT / "scripts" / "upload_paris.py").read_text()
    for field in TRUST_FIELDS:
        assert f'"{field}"' in source, (
            f"upload param dict never carries `{field}` — it will never reach the graph"
        )
        assert f"p.{field}" in source, (
            f"upload Cypher SET list never writes `p.{field}`"
        )


def test_parity_compares_the_trust_fields() -> None:
    """scripts/db_parity.py is the mechanical enforcement that the trust fields
    really reach whichever graph is connected — the lane's dev-data preflight
    runs it, so a missed upload hop turns the sandbox red instead of silently
    serving unverified days."""
    import importlib

    parity = importlib.import_module("scripts.db_parity")
    expected = parity._expected("paris")
    assert "clock_trust" in expected, (
        "db_parity's expected set no longer carries the clock-trust lane"
    )
    keys = {k for (k, _g, _v) in expected["clock_trust"]}
    assert keys, "the clock-trust lane compared nothing — vacuous"


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

    `opening_hours` arrives from the graph as a JSON-encoded STRING (the
    `physical_cues` upload precedent); the string is what the POI carries.
    """
    from src.tour.selection import _snapshot_from_records

    table = '{"mon": [["09:00", "18:00"]], "tue": [], "wed": [["09:00", "18:00"]], ' \
        '"thu": [["09:00", "18:00"]], "fri": [["09:00", "18:00"]], ' \
        '"sat": [["09:00", "18:00"]], "sun": [["09:00", "18:00"]]}'
    record = {
        "id": "musee-test",
        "name": "Musée Test",
        "tier": 4,
        "poi_role": "stop",
        "lat": 48.86,
        "lng": 2.33,
        "areas": [],
        "opening_hours": table,
        "opening_hours_source": "osm",
        "opening_hours_basis": "Museum; OSM tag 'Mo,We-Su 09:00-18:00; Tu off'.",
        "place_category": "museum",
    }
    poi = _snapshot_from_records([record], [], [], []).pois[0]

    assert poi.opening_hours == table, (
        "HOP 2/3: the record carried opening_hours but the POI does not. Either "
        "_snapshot_from_records does not pass it, or POI does not declare it."
    )
    assert poi.opening_hours_source == "osm", (
        "HOP 2/3: the record carried opening_hours_source but the POI does not."
    )
    assert poi.opening_hours_basis.startswith("Museum;"), (
        "HOP 2/3: the record carried opening_hours_basis but the POI does not."
    )
    assert poi.place_category == "museum", (
        "HOP 2/3: the record carried place_category but the POI does not."
    )


def test_hop_2_an_unpriced_record_lands_on_the_safe_defaults() -> None:
    """A corpus written before the opening-hours pass must still load: every
    field arrives as None from Neo4j and must land on None / None / "" / "" —
    and None hours means NEVER CLOCK-EXCLUDED, which is the safe direction
    (a place is included on a day it is closed, never excluded on a day it is
    open)."""
    from src.tour.selection import _snapshot_from_records

    record = {
        "id": "unpriced",
        "name": "A place with no hours yet",
        "tier": 3,
        "poi_role": "stop",
        "lat": 48.8566,
        "lng": 2.3522,
        "areas": [],
        "opening_hours": None,
        "opening_hours_source": None,
        "opening_hours_basis": None,
        "place_category": None,
    }
    poi = _snapshot_from_records([record], [], [], []).pois[0]
    assert poi.opening_hours is None
    assert poi.opening_hours_source is None
    assert poi.opening_hours_basis == ""
    assert poi.place_category == ""


def test_the_upload_carries_the_clock_fields_in_both_property_lists() -> None:
    """S1.4d's hop — `scripts/upload_paris.py` keeps TWO hardcoded property
    lists (the param dict and the Cypher SET list) whose own comment warns they
    must agree or a field silently never reaches the graph. Both must carry all
    four clock fields (the `test_golden_diff_cli_reads_the_durable_key` genre:
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
