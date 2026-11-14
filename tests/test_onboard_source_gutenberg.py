"""Unit tests for the Gutenberg (public-domain books) source connector.

Pure — no Neo4j, no network: the connector's ``parse`` is exercised against a
saved gutendex fixture, never the live wire. Run with:
    make test-file FILE=tests/test_onboard_source_gutenberg.py
"""

from __future__ import annotations

import json
from pathlib import Path

from src.onboard.models import CityContext, ConnectorResult, SourceDocument
from src.onboard.sources.base import SourceConnector
from src.onboard.sources.gutenberg import GutenbergConnector

_FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "onboard"
    / "london"
    / "gutenberg_gutendex.json"
)


def _ctx() -> CityContext:
    return CityContext(
        slug="london",
        display_name="London",
        bbox=(51.28, 51.70, -0.51, 0.33),
    )


def _payload() -> dict:
    return json.loads(_FIXTURE.read_text())


def test_in_copyright_book_is_excluded() -> None:
    """THE public-domain gate: parse emits EXACTLY ONE document — the
    ``copyright: false`` London book — and DROPS the ``copyright: true`` book
    entirely.

    This is the wall Gutenberg exists to enforce: only demonstrably
    public-domain text becomes an ingestable ``SourceDocument``. If the
    ``copyright is False`` gate is removed the in-copyright book leaks through,
    the count becomes 2, and this assertion goes RED — which is exactly what the
    undo-test relies on.
    """
    result = GutenbergConnector().parse(_payload(), _ctx())
    assert isinstance(result, ConnectorResult)

    # Exactly one document survives the gate...
    assert len(result.documents) == 1

    titles = [d.title for d in result.documents]
    # ...and it is the copyright:false book, never the copyright:true one.
    assert "The Adventures of Sherlock Holmes" in titles
    assert "A Modern London Memoir" not in titles


def test_surviving_document_is_a_complete_public_domain_source() -> None:
    """The single surviving document is a fully-formed ``SourceDocument``:
    Gutenberg-sourced, licensed ``public_domain``, pointing at a gutenberg.org
    plain-text URL, carrying the fixture excerpt as non-empty ``text`` and the
    gutenberg id in ``meta``."""
    doc = GutenbergConnector().parse(_payload(), _ctx()).documents[0]

    assert isinstance(doc, SourceDocument)
    assert doc.source == "gutenberg"
    assert doc.license == "public_domain"
    assert doc.title == "The Adventures of Sherlock Holmes"
    assert doc.url.startswith("https://")
    assert "gutenberg.org" in doc.url
    assert doc.url.endswith(".txt")  # the text/plain format, not html/epub/rdf
    assert doc.text  # non-empty: the fixture excerpt stands in as full text
    assert doc.meta["gutenberg_id"] == 1661


def test_consult_url_is_gutendex_search_by_display_name() -> None:
    """consult_url is PURE and names the gutendex search endpoint keyed on the
    city's display name."""
    url, params = GutenbergConnector().consult_url(_ctx())
    assert url == "https://gutendex.com/books"
    assert params == {"search": "London"}


def test_connector_conforms_to_source_connector_protocol() -> None:
    """GutenbergConnector structurally satisfies the frozen SourceConnector
    Protocol (slug + consult_url + parse) without inheriting from it."""
    assert isinstance(GutenbergConnector(), SourceConnector)
    assert GutenbergConnector().slug == "gutenberg"
