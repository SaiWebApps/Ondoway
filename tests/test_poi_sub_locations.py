"""ADR-0002 decision 3 (district port gap 4): `sub_location` is a controlled vocabulary
PER POI — an ordered list on poi-raw.json (the walk path through the place), carried to
the graph and onto the engine's `POI`, where it is the order the building walk follows.

Why this is load-bearing, measured 2026-09-29 on the job-B replay (86 new-shape beats):
P2 wrote 56 distinct prose sub-locations ("Rotunda ramp", "2nd-floor European Paintings
galleries"), nearly one per beat. The engine buckets by exact string and switches to
sub_location ordering at 3 distinct values, so 7 of 19 places — every museum that carries
the corpus — would have been ordered alphabetically by prose, ignoring the (now fixed)
narrative_function. The export therefore keeps a new-shape beat's `sub_location` only when
its POI's vocabulary names it; no vocabulary, no value (the safe default).
"""

from __future__ import annotations

from src.connection import get_database
from src.tour.beat_select import select_poi_beats
from src.tour.contract import POI, BeatRef, sub_location_vocabulary
from src.tour.selection import _decode_sub_locations
from tests import ingest_job_script as script_mod
from tests.conftest import needs_neo4j
from tests.ingest_helpers import minimal_beat, stamp


def _poi(name: str, sub_locations: tuple[str, ...] = ()) -> POI:
    return POI(
        id=f"poi-{name}",
        name=name,
        tier=5,
        poi_role="stop",
        lat=40.78,
        lng=-73.96,
        sub_locations=sub_locations,
    )


def _beat(bid: str, sub_location: str | None) -> BeatRef:
    return BeatRef(id=bid, poi_id="poi", sub_location=sub_location, word_count=100)


def test_a_poi_s_own_vocabulary_is_the_walk_order():
    """The list is ORDERED — the path through the place — so a POI carrying one walks it
    in that order, never alphabetically. [undo: drop `poi.sub_locations` from
    _order_by_sub_location → alphabetical (ramp, rotunda, tower-galleries) → RED]"""
    poi = _poi("Solomon R. Guggenheim Museum", ("rotunda", "ramp", "tower-galleries"))
    beats = [_beat("t", "tower-galleries"), _beat("r", "ramp"), _beat("o", "rotunda")]
    plan = select_poi_beats(poi, beats)
    assert plan.ordering_strategy == "sub_location"
    assert [b.sub_location for b in plan.beats] == ["rotunda", "ramp", "tower-galleries"]


def test_the_vocabulary_normalizer_keeps_order_and_drops_junk():
    """One normalizer for the uploader and the loader, so the two hops cannot drift:
    strings only, stripped, non-empty, first occurrence wins; anything else is none."""
    assert sub_location_vocabulary([" rotunda", "ramp", "", "rotunda", 7, None]) == (
        "rotunda",
        "ramp",
    )
    for raw in (None, "", "rotunda", {"a": 1}, 3):
        assert sub_location_vocabulary(raw) == ()


def test_the_loader_carries_the_graph_list_onto_the_poi():
    """The graph stores list[str] natively; the loader fails OPEN like the anchor decoder
    (a missing or malformed value is no vocabulary, never a refusal to load)."""
    assert _decode_sub_locations(["rotunda", "ramp"]) == ("rotunda", "ramp")
    assert _decode_sub_locations(None) == ()
    assert _decode_sub_locations("rotunda") == ()


def _new_shape(sub_location: str | None) -> dict:
    return stamp(minimal_beat(sub_location=sub_location))


def test_the_export_keeps_only_a_value_the_poi_s_vocabulary_names():
    """[undo: return `beat["sub_location"]` unfiltered in _export_view → the prose value
    reaches the graph → RED]"""
    from scripts.upload_paris import _export_view

    vocab = ("rotunda", "ramp")
    assert _export_view(_new_shape("ramp"), vocab)["sub_location"] == "ramp"
    assert _export_view(_new_shape("Rotunda ramp"), vocab)["sub_location"] is None
    # No vocabulary for the place → no value, however plausible the prose.
    assert _export_view(_new_shape("ramp"))["sub_location"] is None
    assert _export_view(_new_shape("ramp"), ())["sub_location"] is None


def test_a_legacy_record_keeps_its_sub_location():
    """Legacy beats carry hand slugs the fixtures order (Notre-Dame); the export returns
    a legacy record as is, so both real cities keep publishing exactly as today."""
    from scripts.upload_paris import _export_view

    legacy = {"beat_id": "x", "poi_name": "Notre-Dame Cathedral", "script_body": "Hi.",
              "sub_location": "parvis"}
    assert _export_view(legacy)["sub_location"] == "parvis"


def test_the_uploader_builds_the_vocabulary_by_poi_name():
    """Beats link to their POI by name, so the publish looks each beat's vocabulary up
    by `poi_name`; a POI without a list is absent (no value for its beats)."""
    from scripts.upload_paris import _vocabulary_by_poi

    pois = [
        {"name": "Solomon R. Guggenheim Museum", "sub_locations": ["rotunda", "ramp"]},
        {"name": "Frick Collection"},
        {"name": "Neue Galerie New York", "sub_locations": []},
    ]
    assert _vocabulary_by_poi(pois) == {"Solomon R. Guggenheim Museum": ("rotunda", "ramp")}


@needs_neo4j
def test_the_vocabulary_and_the_filtered_value_reach_the_graph(clean_driver):
    """End to end through the real uploader: the POI node carries its list, a beat whose
    value the list names keeps it, a prose value lands as null. [undo: drop
    `p.sub_locations = poi.sub_locations` from the POI SET → the node has no list → RED]"""
    from scripts.upload_paris import CITY_BBOX, _upload_pois, publish_beats

    city = script_mod.CITY
    pois = [{
        "name": script_mod.PLACE,
        "latitude": 40.7830,
        "longitude": -73.9590,
        "sub_locations": ["rotunda", "ramp"],
    }]
    kept = stamp(minimal_beat(poi_name=script_mod.PLACE, story_slug="kept",
                              beat_id=f"{city}/guggenheim/kept", sub_location="ramp"))
    prose = stamp(minimal_beat(poi_name=script_mod.PLACE, story_slug="prose",
                               beat_id=f"{city}/guggenheim/prose", sub_location="Rotunda ramp"))
    from scripts.upload_paris import _vocabulary_by_poi

    with clean_driver.session(database=get_database()) as s:
        _upload_pois(s, pois, city, CITY_BBOX[city])
        stats = publish_beats(s, [kept, prose], city, _vocabulary_by_poi(pois))
    assert stats["linked"] == 2
    with clean_driver.session(database=get_database()) as s:
        node = s.run(
            "MATCH (p:POI {name: $n, city_name: $c}) RETURN p.sub_locations AS v",
            n=script_mod.PLACE, c=city,
        ).single()
        rows = {
            r["id"]: r["sub"]
            for r in s.run(
                "MATCH (b:NarrativeBeat) WHERE b.beat_id IN $ids "
                "RETURN b.beat_id AS id, b.sub_location AS sub",
                ids=[kept["beat_id"], prose["beat_id"]],
            )
        }
    assert list(node["v"]) == ["rotunda", "ramp"]
    assert rows == {kept["beat_id"]: "ramp", prose["beat_id"]: None}


@needs_neo4j
def test_converge_carries_the_poi_raw_list_to_the_engine(tmp_path, clean_driver):
    """THE publish path, file to engine: `converge` reads poi-raw.json's list, the
    graph stores it, and the corpus loader hands the engine the POI's walk order and the
    one beat value it names (the prose value is null). This is the only test that runs
    the `converge` -> `publish_beats` wiring (the judge, 2026-09-29).
    [undo: drop `_vocabulary_by_poi(pois)` from converge's publish_beats call → the kept
    value lands null → RED; drop `p.sub_locations AS sub_locations` from the loader's
    Cypher → the engine's POI has no list → RED]"""
    import json

    from scripts.upload_paris import converge
    from src.tour.selection import load_paris_corpus

    script_mod.chunk_dir(tmp_path)
    data_root = script_mod.data_dir(tmp_path)
    poi_raw = data_root / script_mod.CITY / "poi-raw.json"
    pois = json.loads(poi_raw.read_text())
    for poi in pois:
        if poi["name"] == script_mod.PLACE:
            poi["sub_locations"] = ["rotunda", "ramp"]
    poi_raw.write_text(json.dumps(pois))
    origins, visiting = script_mod.existing_records()
    origins["sub_location"] = "ramp"
    visiting["sub_location"] = "Ticket desk"
    script_mod.seed_beats(data_root, [stamp(origins), stamp(visiting)])

    stats = converge(clean_driver, script_mod.CITY, data_root=data_root)
    assert stats["beats"]["linked"] == 2

    snap = load_paris_corpus(clean_driver, city_slug=script_mod.CITY)
    poi = next(p for p in snap.pois if p.name == script_mod.PLACE)
    assert poi.sub_locations == ("rotunda", "ramp")
    subs = {b.stable_beat_id: b.sub_location for b in snap.beats_by_poi[poi.id]}
    assert subs == {script_mod.ORIGINS_ID: "ramp", script_mod.VISITING_ID: None}
