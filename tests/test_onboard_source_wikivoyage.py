"""Red-first unit tests for the Wikivoyage source connector.

Pure — no network, no Neo4j: ``parse`` runs against a committed fixture (a
MediaWiki ``revisions`` response whose main-slot WIKITEXT — not a plain extract —
carries the page intro plus exactly two ``{{listing}}`` templates, one WITH
lat/long and one WITHOUT ``long``). Run with:
    make test-file FILE=tests/test_onboard_source_wikivoyage.py
"""

from __future__ import annotations

import json
from pathlib import Path

from src.onboard.models import CityContext, SourceDocument
from src.onboard.sources.wikivoyage import WikivoyageConnector

_FIXTURE = (
    Path(__file__).parent / "fixtures" / "onboard" / "london" / "wikivoyage_page.json"
)


def _ctx() -> CityContext:
    return CityContext(
        slug="london",
        display_name="London",
        bbox=(51.28, 51.70, -0.51, 0.33),
    )


def _payload() -> dict:
    return json.loads(_FIXTURE.read_text())


def test_consult_url_queries_revisions_wikitext() -> None:
    """consult_url is PURE and asks for the raw wikitext of the main slot (where
    the {{listing}} templates live) — NOT the plain-text extract."""
    url, params = WikivoyageConnector().consult_url(_ctx())
    assert url == "https://en.wikivoyage.org/w/api.php"
    assert params is not None
    assert params["prop"] == "revisions"
    assert params["rvprop"] == "content"
    assert params["rvslots"] == "main"
    assert params["titles"] == "London"
    assert params["format"] == "json"


def test_listing_with_coords_yields_exact_latlong() -> None:
    """KEY: the {{listing}} carrying lat/long becomes a candidate with those
    EXACT coordinates and wikivoyage provenance."""
    result = WikivoyageConnector().parse(_payload(), _ctx())
    by_name = {c.name: c for c in result.candidates}

    tower = by_name["Tower of London"]
    assert tower.source == "wikivoyage"
    assert tower.source_url == "https://en.wikivoyage.org/wiki/London"
    assert tower.latitude == 51.5081
    assert tower.longitude == -0.0759


def test_listing_without_long_has_longitude_none() -> None:
    """A {{listing}} missing ``long`` is STILL a candidate; its longitude is
    None (a missing coordinate is not a dropped listing)."""
    result = WikivoyageConnector().parse(_payload(), _ctx())
    by_name = {c.name: c for c in result.candidates}

    museum = by_name["British Museum"]
    assert museum.longitude is None


def test_emits_exactly_one_cc_by_sa_document() -> None:
    """KEY: exactly one SourceDocument, licensed cc_by_sa, carrying the RAW
    wikitext (so the {{listing}} markup survives for downstream extraction)."""
    result = WikivoyageConnector().parse(_payload(), _ctx())

    assert len(result.documents) == 1
    doc = result.documents[0]
    assert isinstance(doc, SourceDocument)
    assert doc.license == "cc_by_sa"
    assert doc.source == "wikivoyage"
    assert doc.title == "London"
    assert doc.url == "https://en.wikivoyage.org/wiki/London"
    assert "{{listing" in doc.text  # raw wikitext, not a stripped plain extract


def test_parses_exactly_two_candidates() -> None:
    """The fixture holds exactly two {{listing}} templates → two candidates."""
    result = WikivoyageConnector().parse(_payload(), _ctx())
    assert len(result.candidates) == 2


def _wikitext_payload(wikitext: str) -> dict:
    """Wrap raw wikitext in the minimal revisions-response shape ``parse`` reads."""
    return {
        "query": {
            "pages": {
                "1": {"revisions": [{"slots": {"main": {"*": wikitext}}}]},
            },
        },
    }


def test_listing_with_nested_template_keeps_coords() -> None:
    """A {{listing}} whose body contains a NESTED {{...}} template before its
    lat/long must STILL yield the real coordinates. A non-brace-balanced
    extractor stops at the nested ``}}``, truncating the body before lat/long
    and silently emitting latitude=None/longitude=None — real coords lost."""
    wikitext = (
        "{{listing\n"
        "| name=St Pauls Cathedral\n"
        "| content=Free entry {{dead link|date=2020}} on Sundays\n"
        "| lat=51.5138 | long=-0.0984\n"
        "}}\n"
    )
    result = WikivoyageConnector().parse(_wikitext_payload(wikitext), _ctx())
    by_name = {c.name: c for c in result.candidates}

    cathedral = by_name["St Pauls Cathedral"]
    assert cathedral.latitude == 51.5138
    assert cathedral.longitude == -0.0984


def test_listing_template_stamps_listing_type() -> None:
    """CURATION VOTE: a ``{{listing}}`` candidate now carries its template name in
    ``meta['listing_type']``. Pre-change wikivoyage set no meta, so this KeyErrors
    — the wikivoyage undo-test."""
    result = WikivoyageConnector().parse(_payload(), _ctx())
    by_name = {c.name: c for c in result.candidates}
    assert by_name["Tower of London"].meta["listing_type"] == "listing"


def test_see_and_do_templates_become_candidates_with_type() -> None:
    """The regex must widen from ``{{listing`` to ``{{(listing|see|do)`` so a
    ``{{see}}``/``{{do}}`` template is now mined into a candidate whose
    ``meta['listing_type']`` records which template voted for it.

    Pre-change only ``{{listing`` matched, so neither name is found → KeyError."""
    wikitext = (
        "==See==\n"
        "{{see\n"
        "| name=Trafalgar Square | lat=51.508 | long=-0.128\n"
        "| content=A famous public square in central London.\n"
        "}}\n"
        "==Do==\n"
        "{{do\n"
        "| name=Thames River Cruise | lat=51.507 | long=-0.122\n"
        "| content=A sightseeing boat tour along the river.\n"
        "}}\n"
    )
    result = WikivoyageConnector().parse(_wikitext_payload(wikitext), _ctx())
    by_name = {c.name: c for c in result.candidates}

    assert by_name["Trafalgar Square"].meta["listing_type"] == "see"
    assert by_name["Trafalgar Square"].latitude == 51.508
    assert by_name["Thames River Cruise"].meta["listing_type"] == "do"
