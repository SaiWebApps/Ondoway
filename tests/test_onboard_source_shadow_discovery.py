"""Unit tests for the SHADOW-DISCOVERY source connector.

Discovery-ONLY (grey-zone): ``parse`` maps an already-provided metadata payload
to metadata-only ``DiscoveryPointer``s — NEVER a ``PoiCandidate``, a
``SourceDocument``, or any body text. The connector surfaces where a work can be
acquired LEGALLY; it never fetches the work. Pure — no Neo4j, no network. Run:
    make test-file FILE=tests/test_onboard_source_shadow_discovery.py
"""

from __future__ import annotations

from src.onboard.models import CityContext, DiscoveryPointer
from src.onboard.sources.shadow_discovery import ShadowDiscoveryConnector
from tests.conftest import load_onboard_fixture

# London frame: (min_lat, max_lat, min_lon, max_lon). The city is NOT yet in the
# registry during onboarding, so the bbox comes from the request, not bbox_map().
LONDON = CityContext(
    slug="london",
    display_name="London",
    bbox=(51.28, 51.70, -0.51, 0.33),
)

# Any of these on a pointer would be a grey-zone wall breach: a DiscoveryPointer
# carries metadata only, with no field for copyrighted body text to live in.
TEXT_BEARING_ATTRS = ("text", "content", "body", "full_text")


def _parse_london():
    payload = load_onboard_fixture("london", "shadow_discovery_metadata.json")
    return ShadowDiscoveryConnector().parse(payload, LONDON)


def test_result_is_pointers_only() -> None:
    """KEY assertion (red-first): discovery surfaces ONLY metadata pointers — it
    emits NO POI candidates and NO full-text documents. This is the grey-zone
    discovery-only contract: no candidate, no document, at least one pointer.
    """
    result = _parse_london()

    assert result.candidates == []
    assert result.documents == []
    assert len(result.pointers) >= 1


def test_every_pointer_is_a_metadata_only_discovery_pointer() -> None:
    """Every surfaced pointer is a ``DiscoveryPointer`` with NO text-bearing
    attribute — the absence of a field for body text is the structural wall that
    makes automated ingest of copyrighted text impossible."""
    result = _parse_london()

    assert result.pointers, "expected at least one pointer from the metadata fixture"
    for p in result.pointers:
        assert isinstance(p, DiscoveryPointer)
        for attr in TEXT_BEARING_ATTRS:
            assert not hasattr(p, attr), f"pointer {p.title!r} leaked a {attr!r} attribute"


def test_pointer_carries_metadata_from_the_hit() -> None:
    """Spot-check: title/author/year/format/source_url are surfaced verbatim, and
    the source slug is the shadow-library provider ``annas_archive``."""
    pointers = _parse_london().pointers
    by_title = {p.title: p for p in pointers}

    p = by_title["Around and About London: An Illustrated Guide"]
    assert p.source == "annas_archive"
    assert p.author == "Herbert Fry"
    assert p.year == 1889
    assert p.format == "pdf"
    assert p.source_url == "https://annas-archive.org/md5/1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d"


def test_pointer_count_matches_metadata_hits() -> None:
    payload = load_onboard_fixture("london", "shadow_discovery_metadata.json")
    expected = len(payload["hits"])
    pointers = ShadowDiscoveryConnector().parse(payload, LONDON).pointers
    assert len(pointers) == expected == 3


def test_consult_url_is_a_surfaced_search_page_never_fetched() -> None:
    """PURE: consult_url returns a HUMAN search-page link + ``None`` params. This
    URL is only ever SURFACED as an outbound link, never GET-ed — the shadow
    hosts are not on the ingest allowlist, so discovery stays network-free."""
    url, params = ShadowDiscoveryConnector().consult_url(LONDON)
    assert params is None
    assert url.startswith("https://annas-archive.org/search")
    assert "London" in url
