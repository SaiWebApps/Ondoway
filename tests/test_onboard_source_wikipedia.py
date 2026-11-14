"""Unit tests for the Wikipedia source connector.

Pure — no Neo4j, no network. The connector's ``parse`` maps a raw MediaWiki
``list=geosearch`` payload to a typed ``ConnectorResult``, so the bar drives it
from a committed fixture. Run with:
make test-file FILE=tests/test_onboard_source_wikipedia.py
"""

from __future__ import annotations

from src.onboard.models import CityContext
from src.onboard.sources.mediawiki import geosearch_params
from src.onboard.sources.wikipedia import WikipediaConnector
from tests.conftest import load_onboard_fixture

# London frame: (min_lat, max_lat, min_lon, max_lon). The city is NOT yet in the
# registry during onboarding, so the bbox comes from the request, not bbox_map().
LONDON = CityContext(
    slug="london",
    display_name="London",
    bbox=(51.28, 51.70, -0.51, 0.33),
)


def _parse_london() -> list:
    payload = load_onboard_fixture("london", "wikipedia_geosearch.json")
    result = WikipediaConnector().parse(payload, LONDON)
    return result.candidates


def test_every_candidate_has_coordinates() -> None:
    """KEY assertion (red-first): a geosearch hit ALWAYS carries lat + lon, and
    the connector MUST surface them on every candidate — a coordinate-less
    Wikipedia candidate is useless to the assemble/geofence pass.
    """
    candidates = _parse_london()

    assert candidates, "expected at least one candidate from the geosearch fixture"
    for c in candidates:
        assert c.latitude is not None, f"{c.name!r} is missing latitude"
        assert c.longitude is not None, f"{c.name!r} is missing longitude"


def test_candidate_count_matches_geosearch_list() -> None:
    payload = load_onboard_fixture("london", "wikipedia_geosearch.json")
    expected = len(payload["query"]["geosearch"])
    candidates = WikipediaConnector().parse(payload, LONDON).candidates
    assert len(candidates) == expected == 5


def test_spot_check_tower_of_london() -> None:
    """Name, wiki URL (spaces -> underscores), coords and pageid provenance."""
    candidates = _parse_london()
    by_name = {c.name: c for c in candidates}

    tower = by_name["Tower of London"]
    assert tower.source == "wikipedia"
    assert tower.source_url == "https://en.wikipedia.org/wiki/Tower_of_London"
    assert tower.latitude == 51.5081
    assert tower.longitude == -0.0759
    assert tower.meta == {"pageid": 70890}


def test_url_encodes_spaces_as_underscores() -> None:
    candidates = _parse_london()
    by_name = {c.name: c for c in candidates}
    assert by_name["British Museum"].source_url == (
        "https://en.wikipedia.org/wiki/British_Museum"
    )


def test_consult_url_targets_en_wikipedia_geosearch() -> None:
    """PURE: names the en.wikipedia geosearch GET centred on the bbox centre."""
    url, params = WikipediaConnector().consult_url(LONDON)
    assert url == "https://en.wikipedia.org/w/api.php"
    assert params["list"] == "geosearch"
    # geosearch centred on the bbox centre as (lat, lon), via the frozen helper.
    assert params == geosearch_params(*LONDON.centre)
