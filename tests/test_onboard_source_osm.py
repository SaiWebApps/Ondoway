"""Unit tests for the OSM (Overpass) source connector (osm.py).

Pure — no Neo4j, no network: ``parse`` is fed a saved Overpass JSON fixture and
``consult_url`` is exercised for its shape only. THE GOTCHA under test: a ``node``
element carries its coords top-level (``lat``/``lon``) while a ``way``/``relation``
element carries them under ``center`` — the connector must read each from the
right place, and a named POI from either must land with non-None coords. Run with:
    make test-file FILE=tests/test_onboard_source_osm.py
"""

from __future__ import annotations

import json
from pathlib import Path

from src.onboard.models import CityContext, ConnectorResult
from src.onboard.sources.osm import OsmSource

_FIXTURE = (
    Path(__file__).parent / "fixtures" / "onboard" / "london" / "osm_overpass.json"
)


def _ctx() -> CityContext:
    return CityContext(
        slug="london", display_name="London", bbox=(51.28, 51.70, -0.51, 0.33)
    )


def _payload() -> dict:
    return json.loads(_FIXTURE.read_text())


def test_consult_url_targets_overpass_with_bbox_query() -> None:
    """consult_url returns the Overpass interpreter endpoint and a ``data`` param
    carrying an Overpass QL string. The bbox is emitted in Overpass order
    ``(south,west,north,east)`` = ``(min_lat, min_lon, max_lat, max_lon)`` and the
    query ends with ``out center;`` so ways/relations gain a ``center``."""
    url, params = OsmSource().consult_url(_ctx())
    assert url == "https://overpass-api.de/api/interpreter"
    assert params is not None and "data" in params
    ql = params["data"]
    # Overpass bbox order is (south,west,north,east) = (min_lat,min_lon,max_lat,max_lon).
    assert "51.28,-0.51,51.7,0.33" in ql
    assert "out center;" in ql
    assert '"tourism"' in ql
    assert '"historic"' in ql


def test_parse_reads_way_coords_from_center_and_node_from_toplevel() -> None:
    """KEY assertion (the gotcha): the WAY candidate's coords come from
    ``center.lat/lon`` and the NODE candidate's from top-level ``lat/lon`` — BOTH
    non-None — and the un-named element is skipped entirely."""
    result = OsmSource().parse(_payload(), _ctx())
    assert isinstance(result, ConnectorResult)

    # Only the two NAMED elements survive; the un-named node is skipped.
    assert len(result.candidates) == 2
    names = {c.name for c in result.candidates}
    assert names == {"Trafalgar Square", "British Museum"}

    by_name = {c.name: c for c in result.candidates}

    node_cand = by_name["Trafalgar Square"]  # a "node" element
    assert node_cand.meta["osm_type"] == "node"
    # NODE coords come from the element's TOP-LEVEL lat/lon.
    assert node_cand.latitude == 51.508039
    assert node_cand.longitude == -0.128069
    assert node_cand.latitude is not None and node_cand.longitude is not None

    way_cand = by_name["British Museum"]  # a "way" element
    assert way_cand.meta["osm_type"] == "way"
    # WAY coords come from the ``center`` block, NOT top-level (there is none).
    assert way_cand.latitude == 51.519459
    assert way_cand.longitude == -0.126931
    assert way_cand.latitude is not None and way_cand.longitude is not None


def test_parse_stamps_source_and_osm_provenance() -> None:
    """Every candidate is stamped source="osm", an openstreetmap.org source_url
    built from type/id, and meta provenance scalars (osm_type + osm_id)."""
    result = OsmSource().parse(_payload(), _ctx())
    by_name = {c.name: c for c in result.candidates}

    node_cand = by_name["Trafalgar Square"]
    assert node_cand.source == "osm"
    assert node_cand.source_url == "https://www.openstreetmap.org/node/12345678"
    assert node_cand.meta["osm_id"] == 12345678

    way_cand = by_name["British Museum"]
    assert way_cand.source == "osm"
    assert way_cand.source_url == "https://www.openstreetmap.org/way/987654"
    assert way_cand.meta["osm_id"] == 987654


def test_parse_captures_notability_tags_into_meta() -> None:
    """CURATION SIGNALS the assemble filter consumes: parse must STOP discarding
    tags and stamp ``tourism``/``historic``/``osm_wikidata``/``osm_wikipedia`` into
    meta (None when the tag is absent).

    Pre-change ``parse`` emits only ``osm_type``/``osm_id``, so these KeyError —
    this doubles as the osm undo-test."""
    result = OsmSource().parse(_payload(), _ctx())
    by_name = {c.name: c for c in result.candidates}

    # Trafalgar Square (node): tourism only, no historic / wiki tags → None.
    tra = by_name["Trafalgar Square"]
    assert tra.meta["tourism"] == "attraction"
    assert tra.meta["historic"] is None
    assert tra.meta["osm_wikidata"] is None
    assert tra.meta["osm_wikipedia"] is None

    # British Museum (way): full house of curation signals.
    bm = by_name["British Museum"]
    assert bm.meta["tourism"] == "museum"
    assert bm.meta["historic"] == "building"
    assert bm.meta["osm_wikidata"] == "Q6373"
    assert bm.meta["osm_wikipedia"] == "en:British Museum"
