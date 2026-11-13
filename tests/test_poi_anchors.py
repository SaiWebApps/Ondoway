"""THE REVIEWED ANCHORS of the marquee: segments ONLY where a person placed the
coordinates, marquee anchors only, Notre-Dame first. The data guard: Notre-Dame's anchors
in ``data/paris/poi-raw.json`` are inside the place's own footprint, each names the
sub-locations it stands for and carries its argument, the labels on its beats are all
claimed, the interior is marked as such, and the export chunks (what the workbench upload
reads) carry the same anchors — the ``trigger_radius`` precedent. Free tier: files only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.sync_poi_exports import SYNCED_FIELDS
from src.api.models.nodes import canonical_name_key
from src.tour.contract import Anchor
from src.tour.routing import haversine_m
from src.tour.selection import _decode_anchors
from tests.live_graph import open_dev_driver

ROOT = Path(__file__).resolve().parent.parent
POI_RAW = ROOT / "data" / "paris" / "poi-raw.json"
BEATS = ROOT / "data" / "paris" / "beats.json"
EXPORT = ROOT / "data" / "paris" / "export"
MARQUEE = "Notre-Dame Cathedral"

#: The smallest circle a phone can reliably resolve outdoors in a city. Below this an
#: anchor is not a tight trigger, it is one that never fires. Used only for a place you
#: walk IN, where the look-at rule (half the footprint) makes no sense.
GPS_FLOOR_M: float = 25.0


def _record(name: str) -> dict:
    return next(p for p in json.loads(POI_RAW.read_text()) if p["name"] == name)


def _anchored_records() -> list[dict]:
    """Every Paris POI carrying reviewed anchors — not just the marquee.

    Pere Lachaise Cemetery has three, for a visitor who wants the Mur des Federes
    before Piaf and Wilde. This file used to be hardcoded to Notre-Dame, so those three
    shipped unguarded. The rules below split into the ones that bind EVERY anchored place
    and the ones that are the marquee's own.
    """
    return [p for p in json.loads(POI_RAW.read_text()) if p.get("anchors")]


def _beat_sub_locations(poi_name: str) -> set[str]:
    return {
        b["sub_location"]
        for b in json.loads(BEATS.read_text())
        if b.get("poi_name") == poi_name and b.get("sub_location")
    }


@pytest.fixture(scope="module")
def live_neo4j():
    driver = open_dev_driver()
    if driver is None:
        pytest.skip(
            "The local Paris dev graph (localhost:7687) is unreachable. The anchor "
            "guard reads the graph the app reads; run it through "
            "`make test-file FILE=tests/test_poi_anchors.py`."
        )
    yield driver
    driver.close()


@pytest.mark.parametrize("poi", _anchored_records(), ids=lambda p: p["name"])
def test_the_graph_carries_every_reviewed_anchor(poi: dict, live_neo4j) -> None:
    """The reviewed anchors reach the graph the app actually reads.

    The rules above bind the reviewed file and the export chunks. Neither is what
    a tour reads: `place_anchors` takes `POI.anchors` off the corpus snapshot, and a
    POI whose graph record carries no anchors is silently uncut — every sentence
    written for a named spot inside it joins the arrival story and plays at the edge
    of the whole footprint, which for a 500 m cemetery is half a kilometre from the
    thing. There is no error to read: absence is the safe default everywhere else in
    this rule, so only a guard can tell absence-by-review from absence-by-omission.
    """
    name_key = canonical_name_key(poi["name"])
    with live_neo4j.session() as session:
        record = session.run(
            "MATCH (p:POI {name_key: $name_key, city_name: $city}) RETURN p.anchors AS anchors",
            name_key=name_key,
            city="paris",
        ).single()
    assert record is not None, f"{poi['name']} ({name_key}) is not in the graph at all"

    graph = _decode_anchors(record["anchors"])
    reviewed = [Anchor.model_validate(a) for a in poi["anchors"]]
    assert [a.label for a in graph] == [a.label for a in reviewed], (
        f"{poi['name']}: the graph carries {[a.label for a in graph]}, the reviewed "
        f"record carries {[a.label for a in reviewed]} — a chapter can only play at an "
        "anchor the graph knows about"
    )
    for got, want in zip(graph, reviewed, strict=True):
        assert (got.lat, got.lng, got.radius_m, got.indoor) == (
            want.lat,
            want.lng,
            want.radius_m,
            want.indoor,
        ), f"{poi['name']}/{want.label}: the graph's placement is not the reviewed one"
        assert got.sub_locations == want.sub_locations, f"{poi['name']}/{want.label}"


def test_notre_dame_s_anchors_are_placed_reviewed_and_inside_its_footprint():
    poi = _record(MARQUEE)
    raw = poi.get("anchors")
    assert isinstance(raw, list) and len(raw) >= 3, "the marquee carries no reviewed anchors"
    anchors = [Anchor.model_validate(a) for a in raw]
    labels = [a.label for a in anchors]
    assert len(set(labels)) == len(labels), "two anchors share a label"
    claimed: list[str] = []
    for a in anchors:
        assert a.basis.strip(), f"{a.label}: no argument"
        assert 10 <= a.radius_m <= float(poi["trigger_radius"]), a.label
        d = haversine_m(poi["latitude"], poi["longitude"], a.lat, a.lng)
        assert d <= float(poi["trigger_radius"]), f"{a.label} sits {d:.0f} m off the pin"
        assert a.sub_locations, f"{a.label} stands for nothing"
        claimed.extend(a.sub_locations)
    assert len(set(claimed)) == len(claimed), "a sub-location is claimed twice"
    # Every labelled Notre-Dame beat belongs to exactly one reviewed anchor.
    labelled = {
        b["sub_location"]
        for b in json.loads(BEATS.read_text())
        if b.get("poi_name") == MARQUEE and b.get("sub_location")
    }
    assert labelled <= set(claimed), f"unclaimed sub-locations: {sorted(labelled - set(claimed))}"
    # Under the roof is marked, and only under the roof.
    indoor = {a.label for a in anchors if a.indoor}
    assert indoor, "no interior anchor"
    for a in anchors:
        assert a.indoor == any(s.startswith(("interior", "choir")) for s in a.sub_locations), (
            a.label
        )


def test_the_anchors_reach_the_export_chunks_and_the_loader_reads_them():
    assert "anchors" in SYNCED_FIELDS
    poi = _record(MARQUEE)
    for chunk in sorted(EXPORT.glob("*.json")):
        for rec in json.loads(chunk.read_text()):
            if rec.get("name") == MARQUEE:
                assert rec.get("anchors") == poi["anchors"], chunk.name
                break
        else:
            continue
        break
    else:
        raise AssertionError("the marquee is in no export chunk")
    # The graph stores the list JSON-encoded (the opening_hours precedent); the
    # loader reads either form and tolerates the unloaded shapes.
    decoded = _decode_anchors(json.dumps(poi["anchors"]))
    assert [a.label for a in decoded] == [a["label"] for a in poi["anchors"]]
    assert _decode_anchors(poi["anchors"]) == decoded
    assert _decode_anchors(None) == () and _decode_anchors("") == () and _decode_anchors([]) == ()


@pytest.mark.parametrize(
    "poi", _anchored_records(), ids=lambda p: p["name"]
)
def test_every_reviewed_anchor_is_placed_argued_and_backed_by_real_beats(poi: dict) -> None:
    """The rules that bind EVERY anchored place, marquee or not.

    Added when Pere Lachaise gained three anchors and nothing in the
    tree checked them: this file asserted only ``MARQUEE``. An anchor that claims a
    sub-location no beat carries is dead weight — it can never play — so that is checked
    here rather than left to a reader.
    """
    name = poi["name"]
    footprint = float(poi["trigger_radius"])
    anchors = [Anchor.model_validate(a) for a in poi["anchors"]]

    labels = [a.label for a in anchors]
    assert len(set(labels)) == len(labels), f"{name}: two anchors share a label"

    real = _beat_sub_locations(name)
    claimed: list[str] = []
    for a in anchors:
        assert a.basis.strip(), f"{name}/{a.label}: no argument"
        assert 10 <= a.radius_m <= footprint, (
            f"{name}/{a.label}: {a.radius_m:.0f} m against a {footprint:.0f} m footprint"
        )
        d = haversine_m(poi["latitude"], poi["longitude"], a.lat, a.lng)
        assert d <= footprint, f"{name}/{a.label} sits {d:.0f} m off the pin"
        assert a.sub_locations, f"{name}/{a.label} stands for nothing"
        dead = [s for s in a.sub_locations if s not in real]
        assert not dead, (
            f"{name}/{a.label} claims sub-location(s) no beat carries: {dead} — "
            "an anchor that stands for nothing the corpus can say never plays"
        )
        claimed.extend(a.sub_locations)
    assert len(set(claimed)) == len(claimed), f"{name}: a sub-location is claimed twice"


@pytest.mark.parametrize(
    "poi", _anchored_records(), ids=lambda p: p["name"]
)
def test_an_outdoor_anchor_is_sized_to_where_people_stand(poi: dict) -> None:
    """An outdoor anchor's circle is sized to where people stand to look, not to the doorstep.

    The anchors were first placed at arm's length from the thing they name. That is the
    wrong distance, because you do not look at a cathedral front from under it — you walk
    backwards until it fits. A 45 m circle puts the visitor almost under the doors,
    craning at stonework two feet away.

    THE RULE IS KIND-AWARE. Stated as one sentence: **you stand
    BACK from a place you look at, and you walk INTO a place you move around in.**

    - A place you LOOK AT (`poi_role != "setting"` — a facade, a monument, one face you
      take in from a distance): an outdoor anchor's circle covers at least HALF the
      place's own footprint. This is the original rule, unchanged, and Notre-Dame's
      45 m circles still fail it.
    - A place you WALK IN (`poi_role == "setting"` — a cemetery, a park, a district):
      an anchor is a DESTINATION inside the place, not a viewpoint of the whole of it.
      Half of Pere Lachaise's 500 m footprint would be a 250 m circle covering most of
      forty-four hectares, and three of them would be one indistinguishable blur — the
      opposite of what an anchor is for. So the rule that bites here is that each
      anchor is a DISTINCT standing position: the circles may not overlap, and none may
      be so small that GPS cannot find it.
    - An INDOOR anchor is exempt from both: it is offered on the screen and tapped,
      because GPS is useless under a roof, so its radius decides nothing.
    """
    name = poi["name"]
    footprint = float(poi["trigger_radius"])
    anchors = [Anchor.model_validate(a) for a in poi["anchors"]]
    outdoor = [a for a in anchors if not a.indoor]
    assert outdoor, f"{name} has no outdoor anchor to stand at"

    for a in outdoor:
        assert "stand" in a.basis.lower() or "look" in a.basis.lower(), (
            f"{name}/{a.label}: the basis must say where a person STANDS"
        )

    if poi.get("poi_role") == "setting":
        # A place you walk IN: each anchor is its own position.
        for a in outdoor:
            assert a.radius_m >= GPS_FLOOR_M, (
                f"{name}/{a.label}: {a.radius_m:.0f} m is under the {GPS_FLOOR_M:.0f} m "
                "a phone can reliably resolve, so the anchor would never fire"
            )
        for i, a in enumerate(outdoor):
            for b in outdoor[i + 1 :]:
                gap = haversine_m(a.lat, a.lng, b.lat, b.lng) - a.radius_m - b.radius_m
                assert gap >= 0, (
                    f"{name}: '{a.label}' and '{b.label}' overlap by {-gap:.0f} m — "
                    "inside a place you walk around, two anchors that overlap are one "
                    "blurred position, not two destinations"
                )
        return

    # A place you LOOK AT: stand back far enough to take it in.
    for a in outdoor:
        assert a.radius_m >= footprint / 2, (
            f"{name}/{a.label}: {a.radius_m:.0f} m round a {footprint:.0f} m place is the "
            "doorstep, not where a person stands to look at it (W7.11, 11/11)"
        )
