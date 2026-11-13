"""Structural guard on the per-POI FOOTPRINT — `trigger_radius` — in `data/{city}/poi-raw.json`
and on the three closed hops that carry it to the planner.

`trigger_radius` (int, metres) is how far from the pin a walker is AT the place: the
audio placement rule (src/tour/placement.py) puts it on the wire and the phone's one
predicate reads it. The corpus has carried it on every Paris record since the first
upload and NOTHING read it: the phone drew one 10 m circle for a 140 m square and a
doorway alike. The field now travels through the loader's two silent hops, and the 177
records still at the uploader's default are sized by PLACE KIND (one table,
`scripts/geocode_pois.py::FOOTPRINT_BY_KIND`), with the marquees and every stop of the
eleven sample visitor days reviewed by name (`scripts/poi_trigger_radius.py::REVIEWED`,
a basis sentence each).

This file checks STRUCTURE and the reviewed precedents, for the reason every sibling
guard records (`tests/test_poi_queues.py`, `tests/test_poi_visit_duration.py`): nobody
in this repo can pace out every square; the kind table plus the named review is the
mechanism for wrong-but-well-formed numbers. The precedents pinned below are the places
a review named in its own words, so a silent re-default of one of them is caught.

ALLOW-LIST MECHANICS: per-city checks loop IN-BODY over the tuple (parametrizing over an
empty tuple skips, and the suite hard-errors on skips). Runs in milliseconds, no database.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.poi_trigger_radius import CITIES_WITH_FOOTPRINTS, UPLOADER_DEFAULT_M

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = REPO_ROOT / "data"

#: The smallest footprint a place of this kind can honestly have: a garden, a park or a
#: bridge is a place you are IN, never a doorway. Kinds absent here may be a door (8 m).
FLOOR_BY_KIND: dict[str, int] = {
    "garden": 30,
    "park": 60,
    "bridge": 50,
    "arcade": 30,
    "market": 30,
    "square": 15,
    "street": 15,
}
FOOTPRINT_MIN_M = 8
FOOTPRINT_MAX_M = 500

#: The places a review named, in its own numbers (the reviewed table's basis
#: sentences). Pinned so a re-run of any pass cannot silently hand
#: them back to the uploader's default.
PANEL_PRECEDENTS: dict[str, int] = {
    "Place des Vosges": 70,  # 140 m across, the arcade seventy from the statue
    "Notre-Dame Cathedral": 100,  # the parvis west of the façade; a pin check
    "Louvre Museum": 120,  # the Cour Napoléon and the Cour Carrée
    "Musee d'Orsay": 60,  # the forecourt and a bench
    # The garden, the columns, no. 177. The pin sits at the palace forecourt and the
    # garden runs 275 m behind it, so a 100 m footprint never reached the garden; it
    # is 300. The footprint places arrival; the reviewed anchors carry "right here",
    # which is earned by standing near the thing, never by crossing the edge of a
    # five-hundred-metre disc. The Père-Lachaise 500 is the precedent.
    "Palais-Royal": 300,
    "Place de la Concorde": 120,  # three positions on a 360 m square
    "Pont Neuf": 120,  # the bridge's half-length from the Vert-Galant
    "Pere Lachaise Cemetery": 500,  # the walls, kept
}


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


REMEDY = "Run the footprint pass: `make poi-trigger-radius SLUG=<city>`, then the export sync."


# ---------------------------------------------------------------------------
# The kind table and the reviewed table — the pass's own contract, pinned.
# ---------------------------------------------------------------------------


def test_the_kind_table_covers_the_closed_vocabulary_with_honest_footprints() -> None:
    """ONE table, keyed by the closed place-category vocabulary, every
    value inside the absurdity bounds and at or above its kind's floor."""
    from scripts.geocode_pois import FOOTPRINT_BY_KIND
    from scripts.poi_place_category import PLACE_CATEGORIES

    assert set(FOOTPRINT_BY_KIND) == set(PLACE_CATEGORIES), "the kind table is not the vocabulary"
    for kind, metres in FOOTPRINT_BY_KIND.items():
        assert isinstance(metres, int), (kind, metres)
        assert FOOTPRINT_MIN_M <= metres <= FOOTPRINT_MAX_M, (kind, metres)
        assert metres >= FLOOR_BY_KIND.get(kind, FOOTPRINT_MIN_M), (kind, metres)


def test_trigger_radius_for_reads_the_kind_first_and_the_name_only_without_one() -> None:
    """The one footprint function EXTENDED: with a place kind it answers from
    the kind table; without one it keeps its name-token answer (the New York corpus has no
    kinds yet). RED by mutation: ignore `place_category` -> the garden answers 15."""
    from scripts.geocode_pois import FOOTPRINT_BY_KIND, trigger_radius_for

    metres, basis = trigger_radius_for("Square du Temple", 10, place_category="garden")
    assert metres == FOOTPRINT_BY_KIND["garden"] and basis
    metres, _ = trigger_radius_for("Somewhere", 10, place_category="other")
    assert metres == FOOTPRINT_BY_KIND["other"]
    # No kind: the New York name-token table, unchanged.
    assert trigger_radius_for("Bryant Park", 15)[0] == 120
    assert trigger_radius_for("Brooklyn Bridge", 15)[0] == 75
    assert trigger_radius_for("Some Building", 15)[0] == 15


def test_the_reviewed_table_argues_every_row_and_holds_the_panel_s_places() -> None:
    """Every reviewed footprint ships with the sentence that argues it (the `*_basis`
    precedent), sits inside the bounds, and the places a review named are in it."""
    from scripts.poi_trigger_radius import REVIEWED

    for name, (metres, basis) in REVIEWED.items():
        assert isinstance(metres, int) and FOOTPRINT_MIN_M <= metres <= FOOTPRINT_MAX_M, name
        assert isinstance(basis, str) and len(basis.split()) >= 6, f"{name}: no argument"
    for name, metres in PANEL_PRECEDENTS.items():
        assert name in REVIEWED, f"the panel named {name!r}; the review does not"
        assert REVIEWED[name][0] == metres, (name, REVIEWED[name][0], metres)


# ---------------------------------------------------------------------------
# Per-city data checks, live once a city enters the allow-list above.
# ---------------------------------------------------------------------------


def test_every_poi_carries_an_integer_footprint_inside_the_bounds() -> None:
    for city in CITIES_WITH_FOOTPRINTS:
        offenders = [
            f"{_name(p)}: trigger_radius={p.get('trigger_radius')!r}"
            for p in _pois(city)
            if not isinstance(p.get("trigger_radius"), int)
            or isinstance(p.get("trigger_radius"), bool)
            or not FOOTPRINT_MIN_M <= p["trigger_radius"] <= FOOTPRINT_MAX_M
        ]
        if offenders:
            _fail(city, "POI(s) without an honest integer footprint", offenders, REMEDY)


def test_no_poi_sits_at_the_uploader_s_default_after_the_pass() -> None:
    """Presence check — the pass ran: the uploader's 10 m default marks an unreviewed
    record, and 177 of 370 Paris records sat at it before the pass (the Louvre among them)."""
    for city in CITIES_WITH_FOOTPRINTS:
        offenders = [
            f"{_name(p)} ({p.get('place_category') or 'uncategorised'})"
            for p in _pois(city)
            if p.get("trigger_radius") == UPLOADER_DEFAULT_M
        ]
        if offenders:
            _fail(city, "POI(s) still at the uploader's default footprint", offenders, REMEDY)


def test_a_place_you_are_in_is_never_a_doorway() -> None:
    """A garden, a park, a bridge, an arcade, a market, a square or a street has a
    footprint at or above its kind's floor — the Place des Vosges at 10 m is the defect
    this guards against."""
    for city in CITIES_WITH_FOOTPRINTS:
        offenders = []
        for poi in _pois(city):
            floor = FLOOR_BY_KIND.get(poi.get("place_category") or "")
            radius = poi.get("trigger_radius")
            if floor is not None and isinstance(radius, int) and radius < floor:
                offenders.append(
                    f"{_name(poi)} ({poi['place_category']}): {radius} m < floor {floor} m"
                )
        if offenders:
            _fail(city, "place(s) sized like a doorway", offenders, REMEDY)


def test_the_reviewed_footprints_are_what_the_data_carries() -> None:
    """The review is the audit trail; the data carries its numbers, by name."""
    from scripts.poi_trigger_radius import REVIEWED

    for city in CITIES_WITH_FOOTPRINTS:
        by_name = {_name(p): p for p in _pois(city)}
        offenders = []
        for name, (metres, _basis) in REVIEWED.items():
            poi = by_name.get(name)
            if poi is None:
                offenders.append(f"{name}: not in poi-raw.json (a renamed record?)")
            elif poi.get("trigger_radius") != metres:
                offenders.append(f"{name}: data {poi.get('trigger_radius')!r} != reviewed {metres}")
        if offenders:
            _fail(city, "reviewed footprint(s) the data does not carry", offenders, REMEDY)


def test_every_export_chunk_carries_the_footprint() -> None:
    """`data/{slug}/export/*.json` is what the API upload reads; value agreement with
    poi-raw.json is `tests/test_export_consistency.py`'s job (STRICT_FIELDS)."""
    for city in CITIES_WITH_FOOTPRINTS:
        offenders = []
        for export_file in sorted((DATA_ROOT / city / "export").glob("*.json")):
            for entry in json.loads(export_file.read_text()):
                radius = entry.get("trigger_radius")
                if not isinstance(radius, int) or isinstance(radius, bool):
                    offenders.append(f"{export_file.name}: {entry.get('name', '<unnamed>')!r}")
        if offenders:
            _fail(city, "export entr(ies) without an integer footprint", offenders, REMEDY)


# ---------------------------------------------------------------------------
# The plumbing: the same closed hops every enriched field travels.
# ---------------------------------------------------------------------------


def test_the_contract_declares_the_footprint() -> None:
    """`POI` is extra="ignore": an undeclared keyword vanishes silently, so the value is
    read back. None is the load-bearing default (the placement rule falls to its
    door-sized default); a non-positive radius is refused, never stored."""
    from pydantic import ValidationError

    from src.tour.contract import POI

    poi = POI(
        id="v", name="Place des Vosges", tier=5, poi_role="stop", lat=48.85, lng=2.36,
        trigger_radius=70,
    )
    assert poi.trigger_radius == 70.0, "POI dropped trigger_radius"
    bare = POI(id="x", name="X", tier=3, poi_role="stop", lat=48.85, lng=2.35)
    assert bare.trigger_radius is None
    with pytest.raises(ValidationError):
        POI(id="x", name="X", tier=3, poi_role="stop", lat=48.85, lng=2.35, trigger_radius=0)


def test_the_corpus_loader_returns_and_constructs_the_footprint() -> None:
    """HOP 1 — `LOAD_PARIS_POIS_CYPHER` returns ONLY what its RETURN list names; HOP 2 —
    `_snapshot_from_records` sets constructor keywords one by one (source scan, the
    `test_poi_queues.py` genre)."""
    from src.tour.selection import LOAD_PARIS_POIS_CYPHER

    source = (REPO_ROOT / "src" / "tour" / "selection.py").read_text()
    assert "p.trigger_radius" in LOAD_PARIS_POIS_CYPHER, (
        "HOP 1: the corpus query never asks the graph for `trigger_radius`"
    )
    assert "trigger_radius=" in source, (
        "HOP 2: nothing in selection.py passes `trigger_radius=` to the POI"
    )


def test_hop_round_trip_a_graph_record_lands_on_the_poi() -> None:
    """Cypher → snapshot, run for real: the graph's number surfaces on the built POI; a
    record without one (or with the graph's 0/None) lands on the contract's None."""
    from src.tour.selection import _snapshot_from_records

    base = {"id": "v", "name": "Place des Vosges", "tier": 5, "poi_role": "stop",
            "lat": 48.8556, "lng": 2.3655, "areas": []}

    def built(record: dict) -> float | None:
        return _snapshot_from_records([record], [], [], []).pois[0].trigger_radius

    assert built({**base, "trigger_radius": 70}) == 70.0, "cypher → snapshot dropped it"
    assert built({**base, "trigger_radius": None}) is None
    assert built({**base, "trigger_radius": 0}) is None
    assert built(base) is None


def test_the_upload_carries_the_footprint_in_both_property_lists() -> None:
    """`scripts/upload_paris.py` keeps TWO hardcoded property lists that must agree."""
    source = (REPO_ROOT / "scripts" / "upload_paris.py").read_text()
    assert '"trigger_radius"' in source
    assert "p.trigger_radius" in source


def test_the_export_sync_carries_the_footprint() -> None:
    """`SYNCED_FIELDS` in `scripts/sync_poi_exports.py` — THE one list of fields that flow
    poi-raw → export chunks — carries the footprint, so the 19 drifted export entries the
    TODO in tests/test_export_consistency.py recorded can never come back."""
    from scripts.sync_poi_exports import SYNCED_FIELDS

    assert "trigger_radius" in SYNCED_FIELDS
