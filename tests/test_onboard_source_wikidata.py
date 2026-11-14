"""Unit tests for the WIKIDATA source connector (wikidata.py).

Pure — no Neo4j, no network: the connector's ``parse`` maps a committed WDQS
SPARQL-JSON fixture to a typed ``ConnectorResult``, so the bar never touches the
wire. Run with:
    make test-file FILE=tests/test_onboard_source_wikidata.py

THE GOTCHA under test is COORDINATE ORDER: a Wikidata coordinate is a WKT
``"Point(LON LAT)"`` string with LONGITUDE FIRST, so a naive lon/lat swap would
put London at latitude -0.13 (in the Atlantic) instead of 51.5. The first test
below pins the order so that swap can never ship silently.
"""

from __future__ import annotations

from src.onboard.models import CityContext, ConnectorResult, PoiCandidate
from src.onboard.sources.wikidata import WikidataConnector
from tests.conftest import load_onboard_fixture

# London bbox, mirroring bbox_map order: (min_lat, max_lat, min_lon, max_lon).
_CTX = CityContext(
    slug="london",
    display_name="London",
    bbox=(51.28, 51.70, -0.51, 0.33),
)


def _london(result: ConnectorResult) -> PoiCandidate:
    """The 'London' (Q84) candidate out of a parsed result."""
    hits = [c for c in result.candidates if c.name == "London"]
    assert len(hits) == 1, f"expected exactly one 'London' candidate, got {len(hits)}"
    return hits[0]


def test_coord_order_lon_first_not_swapped() -> None:
    """THE KEY ASSERTION. WKT is ``Point(LON LAT)`` so for London (Q84) the
    ~51 value must land in ``latitude`` and the ~-0.13 value in ``longitude``.

    If ``parse`` swapped lon/lat this goes RED — latitude would be -0.1278 and
    longitude 51.5074. That swap is exactly the undo-test mutation.
    """
    result = WikidataConnector().parse(
        load_onboard_fixture("london", "wikidata_sparql.json"), _CTX
    )
    london = _london(result)
    assert abs(london.latitude - 51.5074) < 0.001, (
        f"latitude should be the ~51 value, got {london.latitude} "
        "(lon/lat swapped?)"
    )
    assert abs(london.longitude - (-0.1278)) < 0.001, (
        f"longitude should be the ~-0.13 value, got {london.longitude} "
        "(lon/lat swapped?)"
    )


def test_qid_extracted_from_entity_uri() -> None:
    """The Q-number is pulled off the entity URI tail into meta['qid'], and
    source_url is the full entity URI."""
    result = WikidataConnector().parse(
        load_onboard_fixture("london", "wikidata_sparql.json"), _CTX
    )
    london = _london(result)
    assert london.meta["qid"] == "Q84"
    assert london.source_url == "http://www.wikidata.org/entity/Q84"


def test_parse_maps_every_binding_with_provenance() -> None:
    """Every WDQS binding becomes one PoiCandidate sourced 'wikidata', and a
    second, distinct entity is also parsed with its own (non-swapped) coords."""
    result = WikidataConnector().parse(
        load_onboard_fixture("london", "wikidata_sparql.json"), _CTX
    )
    assert len(result.candidates) == 3
    assert {c.name for c in result.candidates} == {"London", "Big Ben", "Tower Bridge"}
    assert all(c.source == "wikidata" for c in result.candidates)

    big_ben = next(c for c in result.candidates if c.name == "Big Ben")
    assert big_ben.meta["qid"] == "Q41225"
    assert abs(big_ben.latitude - 51.5007) < 0.001
    assert abs(big_ben.longitude - (-0.1246)) < 0.001


def test_binding_without_placeLabel_is_skipped_not_crash() -> None:  # noqa: N802 (mirrors the WDQS `placeLabel` key name)
    """REAL-DATA CRASH GUARD. WDQS legitimately OMITS the ``placeLabel`` key
    entirely from a binding (absent, not null) when the entity has no bindable
    English label. Bracket access ``binding["placeLabel"]`` raises KeyError on
    such a binding; the fixture never contains one, so the crash hid until a
    real query returned an unlabelled item.

    ``parse`` must NOT crash and must SKIP the nameless binding (a labelless
    candidate is useless for the dedup pass), keeping only the labelled one.
    """
    payload = {
        "results": {
            "bindings": [
                {
                    "place": {
                        "type": "uri",
                        "value": "http://www.wikidata.org/entity/Q84",
                    },
                    "placeLabel": {
                        "xml:lang": "en",
                        "type": "literal",
                        "value": "London",
                    },
                    "coord": {
                        "datatype": "http://www.opengis.net/ont/geosparql#wktLiteral",
                        "type": "literal",
                        "value": "Point(-0.1278 51.5074)",
                    },
                },
                {
                    # No 'placeLabel' key at all — the entity has no bindable label.
                    "place": {
                        "type": "uri",
                        "value": "http://www.wikidata.org/entity/Q99999999",
                    },
                    "coord": {
                        "datatype": "http://www.opengis.net/ont/geosparql#wktLiteral",
                        "type": "literal",
                        "value": "Point(-0.12 51.50)",
                    },
                },
            ]
        }
    }
    result = WikidataConnector().parse(payload, _CTX)
    # Only the labelled binding survives; the nameless one is dropped, no crash.
    assert len(result.candidates) == 1
    assert [c.name for c in result.candidates] == ["London"]
    assert all(c.name for c in result.candidates), "no nameless candidate may be emitted"


def test_notability_meta_sitelinks_int_enwiki_and_p31() -> None:
    """NOTABILITY SIGNALS the assemble filter consumes. Each candidate must carry
    ``meta['sitelinks']`` as an INT, ``meta['enwiki']`` as the article title, and
    ``meta['p31']`` as the pipe-joined Q-ids — pulled from the WDQS binding.

    Pre-change ``parse`` emits only ``{'qid'}`` in meta, so every assertion below
    KeyErrors — this doubles as the wikidata undo-test.
    """
    result = WikidataConnector().parse(
        load_onboard_fixture("london", "wikidata_sparql.json"), _CTX
    )
    london = _london(result)
    assert london.meta["sitelinks"] == 250
    assert isinstance(london.meta["sitelinks"], int)
    assert london.meta["enwiki"] == "London"
    assert london.meta["p31"] == "Q515|Q5119"

    big_ben = next(c for c in result.candidates if c.name == "Big Ben")
    assert big_ben.meta["sitelinks"] == 83
    assert big_ben.meta["enwiki"] == "Big Ben"
    assert big_ben.meta["p31"] == "Q200334"


def test_missing_sitelinks_and_enwiki_default_to_zero_and_none() -> None:
    """GUARD: a binding that OMITS ``sitelinks``/``enwiki`` (real WDQS leaves an
    OPTIONAL/aggregate unbound) must NOT crash — sitelinks defaults to int 0 and
    enwiki to None, per the .get contract."""
    payload = {
        "results": {
            "bindings": [
                {
                    "place": {
                        "type": "uri",
                        "value": "http://www.wikidata.org/entity/Q84",
                    },
                    "placeLabel": {"type": "literal", "value": "London"},
                    "coord": {
                        "type": "literal",
                        "value": "Point(-0.1278 51.5074)",
                    },
                    # No 'sitelinks', 'enwiki', or 'p31' bindings at all.
                }
            ]
        }
    }
    result = WikidataConnector().parse(payload, _CTX)
    london = _london(result)
    assert london.meta["sitelinks"] == 0
    assert isinstance(london.meta["sitelinks"], int)
    assert london.meta["enwiki"] is None


def test_box_query_adds_sitelink_floor_and_event_exclusion() -> None:
    """SERVER-SIDE RELIEF so WDQS doesn't time out at 55K: the box query must
    request sitelinks + the enwiki article + P31, and carry the sitelink floor
    (``>= 3``) and the event/occurrence exclusion (Q1190554)."""
    _, params = WikidataConnector().consult_url(_CTX)
    query = params["query"]
    assert "wikibase:sitelinks" in query
    assert "schema:isPartOf" in query and "en.wikipedia.org" in query
    assert "wdt:P31" in query
    assert ">= 3" in query
    assert "Q1190554" in query  # occurrences/events excluded server-side


def test_box_query_avoids_the_timeout_prone_transitive_walk() -> None:
    """REGRESSION GUARD for the live-London ranking bug. The prior box query used
    a transitive ``wdt:P31/wdt:P279*`` subclass walk PLUS a ``GROUP_CONCAT`` P31
    aggregation; together they blew past the WDQS timeout on a Greater-London box,
    so the flow's error-resilience turned the source into an EMPTY result — every
    POI then scored equal (no sitelinks) and the cap + tiers collapsed to
    ALPHABETICAL (British Museum last, tiers inverted).

    The query must therefore NOT contain the transitive ``P279*`` walk or the P31
    ``GROUP_CONCAT`` aggregation; events are excluded by a CHEAP DIRECT ``wdt:P31``
    membership test instead (still naming Q1190554), so sitelinks come back fast.
    UNDO: restore ``FILTER NOT EXISTS { ?place wdt:P31/wdt:P279* wd:Q1190554 }`` or
    the ``GROUP_CONCAT(... AS ?p31)`` column -> this goes RED.
    """
    _, params = WikidataConnector().consult_url(_CTX)
    query = params["query"]
    assert "P279" not in query, "the transitive P279* subclass walk timed WDQS out"
    assert "GROUP_CONCAT" not in query, "the P31 GROUP_CONCAT aggregation timed WDQS out"
    # Events are STILL excluded server-side — by the cheap direct-P31 membership test.
    assert "FILTER NOT EXISTS" in query and "wdt:P31" in query
    assert "Q1190554" in query


def test_consult_url_is_wdqs_box_query() -> None:
    """consult_url is PURE: it names the WDQS endpoint + a JSON-format box query
    whose SW/NE corners come from the ctx bbox (WKT 'Point(LON LAT)' corners)."""
    url, params = WikidataConnector().consult_url(_CTX)
    assert url == "https://query.wikidata.org/sparql"
    assert params is not None
    assert params["format"] == "json"
    query = params["query"]
    assert "wikibase:box" in query
    assert "wdt:P625" in query
    # SW corner is (min_lon, min_lat); NE corner is (max_lon, max_lat) — lon first.
    assert "Point(-0.51 51.28)" in query  # SW
    assert "Point(0.33 51.7)" in query  # NE
