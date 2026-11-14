"""Unit tests for the INTERNET_ARCHIVE source connector (legal wall).

archive.org is allowlisted BY HOST, but the host also serves in-copyright
Controlled-Digital-Lending scans. The host wall does NOT prove public-domain —
so the connector MUST verify per-item PD status and REFUSE (emit ZERO documents)
for anything that is not provably public-domain. The zero-documents assertion below
(``result.documents == []`` for the in-copyright fixture) is a committed legal
wall: it must stay airtight.

CONSERVATIVE CONTRACT (fail-closed, ONE signal). The wall keyed on the
``licenseurl`` acceptance path THREE times and broke THREE times (substring,
host-spoof, then an OR-override where a genuine-host PD licenseurl overrode an
explicit IN_COPYRIGHT status). It has been SIMPLIFIED: a doc is accepted as
public-domain IFF its ``possible-copyright-status`` normalizes to exactly
``NOT_IN_COPYRIGHT``. ``licenseurl`` and free-text ``rights`` NO LONGER grant
acceptance under any circumstance — the tests below pin that deliberate coverage
change (a PD licenseurl alone is now refused; a PD item lacking the computed
status uses the Manual book-drop instead).

Pure — no Neo4j, no network: ``parse`` is exercised directly against JSON
fixtures. Run with:
    make test-file FILE=tests/test_onboard_source_internet_archive.py
"""

from __future__ import annotations

import json
from pathlib import Path

from src.onboard.models import CityContext
from src.onboard.sources.internet_archive import InternetArchiveConnector

_FIXTURES = Path(__file__).parent / "fixtures" / "onboard" / "london"


def _load(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text())


def _ctx() -> CityContext:
    return CityContext(
        slug="london",
        display_name="London",
        bbox=(51.28, 51.70, -0.51, 0.33),
    )


def test_incopyright_fixture_emits_zero_documents() -> None:
    """KEY assertion (the legal wall): an in-copyright CDL scan hosted on the
    allowlisted host is NOT provably public-domain, so the connector emits ZERO
    documents. If this ever goes green with a document, we would be ingesting
    copyrighted text off archive.org — that must be impossible."""
    payload = _load("internet_archive_incopyright.json")
    result = InternetArchiveConnector().parse(payload, _ctx())
    assert result.documents == []


def test_pd_fixture_emits_exactly_one_public_domain_document() -> None:
    """A provably public-domain item (possible-copyright-status =
    NOT_IN_COPYRIGHT) yields exactly one SourceDocument, license public_domain,
    carrying the fixture excerpt and IA provenance scalars."""
    payload = _load("internet_archive_pd.json")
    result = InternetArchiveConnector().parse(payload, _ctx())

    assert len(result.documents) == 1
    doc = result.documents[0]
    assert doc.source == "internet_archive"
    assert doc.license == "public_domain"
    assert doc.title == "London, past and present"
    assert doc.url == "https://archive.org/details/londonpastpresen00loft"
    assert doc.text  # excerpt carried through
    assert doc.meta["ia_identifier"] == "londonpastpresen00loft"
    assert doc.meta["copyright_status"] == "NOT_IN_COPYRIGHT"


def test_consult_url_is_pure_advancedsearch_query() -> None:
    """consult_url is PURE and names the advancedsearch endpoint + subject query
    scoped to the city slug."""
    url, params = InternetArchiveConnector().consult_url(_ctx())
    assert url == "https://archive.org/advancedsearch.php"
    assert params is not None
    assert params["q"] == "subject:(london)"
    assert params["output"] == "json"


def test_is_public_domain_gate_variants() -> None:
    """The per-item PD gate is FAIL-CLOSED and keys on ONE signal: an exact
    NOT_IN_COPYRIGHT ``possible-copyright-status`` passes; everything else
    (in-copyright, all-rights-reserved, missing status) is refused. Neither a
    ``licenseurl`` nor free-text ``rights`` grants acceptance any longer."""
    gate = InternetArchiveConnector()._is_public_domain
    assert gate({"possible-copyright-status": "NOT_IN_COPYRIGHT"}) is True
    assert gate({"possible-copyright-status": "IN_COPYRIGHT"}) is False
    # licenseurl / rights are no longer acceptance signals under any circumstance
    assert gate({"licenseurl": "http://creativecommons.org/publicdomain/mark/1.0/"}) is False
    assert gate({"licenseurl": "https://creativecommons.org/publicdomain/zero/1.0/"}) is False
    assert gate({"rights": "Public Domain Mark 1.0"}) is False
    assert gate({"rights": "All rights reserved"}) is False
    assert gate({}) is False


# ── THE point: the OR-override is dead (genuine-host PD licenseurl cannot win) ─


def test_in_copyright_status_with_pd_licenseurl_is_refused() -> None:
    """An explicit IN_COPYRIGHT status must NEVER be overridden by a genuine-host
    PD licenseurl. Under the removed OR code this emitted a document; under the
    single-signal contract it emits ZERO. This is the regression that proves the
    OR-override is gone."""
    payload = {
        "response": {
            "docs": [
                {
                    "identifier": "x",
                    "title": "t",
                    "possible-copyright-status": "IN_COPYRIGHT",
                    "licenseurl": "https://creativecommons.org/publicdomain/mark/1.0/",
                    "excerpt": "COPYRIGHTED",
                }
            ]
        }
    }
    assert InternetArchiveConnector().parse(payload, _ctx()).documents == []


def test_pd_status_accepted() -> None:
    """A doc whose ONLY PD signal is possible-copyright-status = NOT_IN_COPYRIGHT
    (no licenseurl at all) is accepted, license public_domain — the sole
    authoritative acceptance path."""
    payload = {
        "response": {
            "docs": [
                {
                    "identifier": "pdstatus00",
                    "title": "A doc accepted purely on its NOT_IN_COPYRIGHT status",
                    "possible-copyright-status": "NOT_IN_COPYRIGHT",
                    "excerpt": "public-domain text",
                }
            ]
        }
    }
    docs = InternetArchiveConnector().parse(payload, _ctx()).documents
    assert len(docs) == 1
    assert docs[0].license == "public_domain"
    assert docs[0].meta["ia_identifier"] == "pdstatus00"


def test_pd_licenseurl_without_pd_status_is_now_refused() -> None:
    """Deliberate coverage change: a genuine, authoritative-host PD licenseurl is
    NO LONGER sufficient on its own. With no NOT_IN_COPYRIGHT status, the item is
    refused (ZERO documents) — a real PD item without the computed status uses
    the Manual book-drop, not auto-ingest."""
    gate = InternetArchiveConnector()._is_public_domain
    assert gate({"licenseurl": "https://creativecommons.org/publicdomain/mark/1.0/"}) is False

    payload = {
        "response": {
            "docs": [
                {
                    "identifier": "pdlicenseonly00",
                    "title": "An authoritative PD-Mark doc with NO status field",
                    "licenseurl": "https://creativecommons.org/publicdomain/mark/1.0/",
                    "excerpt": "public-domain text",
                }
            ]
        }
    }
    assert InternetArchiveConnector().parse(payload, _ctx()).documents == []


# ── Status normalization: string OR list, fail-closed on ambiguity ──────────


def test_negated_public_domain_phrase_is_refused() -> None:
    """A negated 'public domain' phrase in free-text ``rights`` must NEVER be
    read as a PD dedication. ``rights`` is not an acceptance signal at all now,
    so both an IN_COPYRIGHT-status doc and a rights-only doc emit ZERO."""
    ctx = _ctx()
    conn = InternetArchiveConnector()

    negated_status = {
        "response": {
            "docs": [
                {
                    "identifier": "negated00status",
                    "title": "A negated status doc",
                    "possible-copyright-status": "IN_COPYRIGHT",
                    "rights": "This work is not in the public domain.",
                    "excerpt": "irrelevant",
                }
            ]
        }
    }
    assert conn.parse(negated_status, ctx).documents == []

    negated_rights_only = {
        "response": {
            "docs": [
                {
                    "identifier": "negated00rights",
                    "title": "A negated rights-only doc",
                    "rights": "Not public domain, all rights reserved",
                    "excerpt": "irrelevant",
                }
            ]
        }
    }
    assert conn.parse(negated_rights_only, ctx).documents == []


def test_list_status_is_accepted() -> None:
    """Real IA returns possible-copyright-status as EITHER a string OR a list.
    A list whose element is NOT_IN_COPYRIGHT is provably public-domain and must
    be accepted; a list whose element is IN_COPYRIGHT is refused."""
    gate = InternetArchiveConnector()._is_public_domain
    assert gate({"possible-copyright-status": ["NOT_IN_COPYRIGHT"]}) is True
    assert gate({"possible-copyright-status": ["IN_COPYRIGHT"]}) is False


def test_mixed_status_list_is_refused() -> None:
    """A contradictory status list ``["IN_COPYRIGHT","NOT_IN_COPYRIGHT"]`` is
    ambiguous, not proof. Fail-CLOSED: any present token that is not exactly
    NOT_IN_COPYRIGHT refuses the item — gate False and the full pipeline emits
    ZERO documents. Order must not matter, and an empty list is refused too."""
    gate = InternetArchiveConnector()._is_public_domain
    assert gate({"possible-copyright-status": ["IN_COPYRIGHT", "NOT_IN_COPYRIGHT"]}) is False
    assert gate({"possible-copyright-status": ["NOT_IN_COPYRIGHT", "IN_COPYRIGHT"]}) is False
    assert gate({"possible-copyright-status": ["NOT_IN_COPYRIGHT", "UNKNOWN"]}) is False
    assert gate({"possible-copyright-status": []}) is False
    # A non-string element in the list is ambiguous → refuse (no crash).
    assert gate({"possible-copyright-status": ["NOT_IN_COPYRIGHT", None]}) is False

    payload = {
        "response": {
            "docs": [
                {
                    "identifier": "mixedstatus00",
                    "title": "A doc with a contradictory copyright-status list",
                    "possible-copyright-status": ["IN_COPYRIGHT", "NOT_IN_COPYRIGHT"],
                    "excerpt": "COPYRIGHTED",
                }
            ]
        }
    }
    assert InternetArchiveConnector().parse(payload, _ctx()).documents == []


def test_malformed_licenseurl_does_not_crash() -> None:
    """A malformed ``licenseurl`` (``https://[creativecommons.org]/x`` — an
    invalid-host string that raised a ValueError when the old code called
    ``urlparse`` on it) must NOT be parsed at all now. The doc is accepted purely
    on its NOT_IN_COPYRIGHT status, with no crash — proving licenseurl is never
    touched."""
    payload = {
        "response": {
            "docs": [
                {
                    "identifier": "malformed00url",
                    "title": "A PD-status doc carrying a malformed licenseurl",
                    "possible-copyright-status": "NOT_IN_COPYRIGHT",
                    "licenseurl": "https://[creativecommons.org]/x",
                    "excerpt": "public-domain text",
                }
            ]
        }
    }
    docs = InternetArchiveConnector().parse(payload, _ctx()).documents
    assert len(docs) == 1
    assert docs[0].license == "public_domain"
    assert docs[0].meta["ia_identifier"] == "malformed00url"


def test_hostile_licenseurl_fixture_emits_zero_documents() -> None:
    """The committed hostile fixture (an IN_COPYRIGHT scan carrying a forged PD
    licenseurl on an uploader-controlled host) must emit ZERO documents. Under
    the single-signal contract it is refused on its IN_COPYRIGHT status; the
    licenseurl is never even considered."""
    ctx = _ctx()
    conn = InternetArchiveConnector()
    assert conn.parse(_load("internet_archive_hostile_licenseurl.json"), ctx).documents == []


# ── Unguarded bracket access on real-shaped data ────────────────────────────


def test_doc_missing_title_is_skipped_not_crash() -> None:
    """A PD-eligible doc with no 'title' must be SKIPPED (not emitted, not a
    KeyError). Real IA can omit fields the search schema requested."""
    payload = {
        "response": {
            "docs": [
                {
                    "identifier": "notitle00doc",
                    "possible-copyright-status": "NOT_IN_COPYRIGHT",
                    "excerpt": "has an excerpt but no title",
                }
            ]
        }
    }
    result = InternetArchiveConnector().parse(payload, _ctx())
    assert result.documents == []


def test_malformed_payload_no_response_key() -> None:
    """A body that is not response-wrapped must yield no documents, not crash."""
    result = InternetArchiveConnector().parse({}, _ctx())
    assert result.documents == []
