"""Live Wikipedia extract fetching (``src/onboard/extract.py``) — RED-first bar.

A LIVE onboarding run must ground each assembled POI's beats against that POI's
CURRENT English-Wikipedia revision — not a stale committed fixture. This suite
pins the resolve + fetch + pin contract WITHOUT touching the real network:
``fetch.http_get_json`` is monkeypatched to return canned MediaWiki payloads.

Run one file: make test-file FILE=tests/test_onboard_extract.py
"""

from __future__ import annotations

from src.onboard import extract, fetch, flow
from src.onboard.assemble import WikiExtract

# British Museum article coords (matches the London fixture) — the happy-path POI
# sits right on top of them, so coordinate verification passes.
_BM_LAT, _BM_LON = 51.5194, -0.127


# ---------------------------------------------------------------------------
# Canned MediaWiki payloads + a dispatching fake for ``fetch.http_get_json``.
# ---------------------------------------------------------------------------
def _extract_response(
    *,
    title: str = "British Museum",
    revid: int = 1198800001,
    text: str = "The British Museum is a public museum in London.",
    lat: float | None = _BM_LAT,
    lon: float | None = _BM_LON,
    disambiguation: bool = False,
    missing: bool = False,
    redirect_from: str | None = None,
) -> dict:
    """A ``prop=extracts|revisions|coordinates|pageprops`` response for one page."""
    if missing:
        return {"query": {"pages": {"-1": {"ns": 0, "title": title, "missing": ""}}}}
    page: dict = {
        "pageid": 5060,
        "ns": 0,
        "title": title,
        "extract": text,
        "revisions": [{"revid": revid, "parentid": revid - 1}],
    }
    if lat is not None and lon is not None:
        page["coordinates"] = [{"lat": lat, "lon": lon, "primary": "", "globe": "earth"}]
    if disambiguation:
        page["pageprops"] = {"disambiguation": ""}
    resp: dict = {"query": {"pages": {"5060": page}}}
    if redirect_from:
        resp["query"]["redirects"] = [{"from": redirect_from, "to": title}]
    return resp


def _search_response(title: str = "British Museum") -> dict:
    return {"query": {"search": [{"ns": 0, "title": title, "pageid": 5060}]}}


def _search_empty() -> dict:
    return {"query": {"search": []}}


def _make_fake(extract_resp: dict, search_resp: dict | None = None):
    """A fake ``http_get_json`` that dispatches search vs extract queries and
    records every call for call-count assertions."""
    calls: list[tuple[str, dict]] = []

    def fake(url, params=None, **kw):
        calls.append((url, dict(params or {})))
        if (params or {}).get("list") == "search":
            return search_resp if search_resp is not None else _search_empty()
        return extract_resp

    fake.calls = calls  # type: ignore[attr-defined]
    return fake


def _poi(name="British Museum", lat=_BM_LAT, lon=_BM_LON, source_urls=None) -> dict:
    if source_urls is None:
        source_urls = ["https://en.wikipedia.org/wiki/British_Museum"]
    return {
        "name": name,
        "latitude": lat,
        "longitude": lon,
        "_pipeline": {"discovery_sources": ["wikipedia"], "source_urls": source_urls},
    }


# ---------------------------------------------------------------------------
# _extract_params — LEAD-SECTION-ONLY grounding (exintro).
# ---------------------------------------------------------------------------
def test_extract_params_requests_lead_section_only() -> None:
    """LEAD-ONLY RED-FIRST: ``_extract_params`` must carry ``exintro=1`` so the
    extract is the article's lead section (the coherent definitional summary) and
    the beat drafter never carves deep-article, off-topic spans (e.g. a "The
    British Empire comprised…" sentence surfacing under Trafalgar Square).

    Remove ``exintro`` from the params dict -> this test goes RED; restore -> GREEN.
    The remaining assertions pin the rest of the query shape (plain text, redirect
    following, coordinates + revids) so this fix cannot silently drop them."""
    params = extract._extract_params("Trafalgar Square")

    assert params["exintro"] == 1  # LEAD SECTION ONLY — the whole point of the fix
    assert params["explaintext"] == 1  # still plain text, not wikitext/HTML
    assert params["redirects"] == 1  # still follows redirects
    assert params["prop"] == "extracts|revisions|coordinates|pageprops"
    assert params["rvprop"] == "ids"  # still pins the current revid
    assert params["titles"] == "Trafalgar Square"


# ---------------------------------------------------------------------------
# resolve_and_fetch_extract.
# ---------------------------------------------------------------------------
def test_resolve_from_source_url_returns_wikiextract(monkeypatch) -> None:
    """A POI whose ``source_urls`` carry the en.wikipedia article URL resolves via
    that title in a SINGLE API call (no search) and pins revid + lead text."""
    fake = _make_fake(_extract_response())
    monkeypatch.setattr(fetch, "http_get_json", fake)

    ext = extract.resolve_and_fetch_extract(_poi())

    assert ext is not None
    assert ext.poi_name == "British Museum"
    assert ext.article_title == "British Museum"
    assert ext.revid == "1198800001"
    assert "British Museum is a public museum" in ext.text
    assert ext.url == "https://en.wikipedia.org/wiki/British_Museum?oldid=1198800001"
    assert len(fake.calls) == 1
    assert fake.calls[0][1].get("list") != "search"  # resolved WITHOUT a search


def test_missing_page_returns_none(monkeypatch) -> None:
    """A ``missing`` page (and no search hit) -> None, never a junk WikiExtract."""
    monkeypatch.setattr(
        fetch, "http_get_json", _make_fake(_extract_response(missing=True), _search_empty())
    )
    assert extract.resolve_and_fetch_extract(_poi()) is None


def test_empty_extract_returns_none(monkeypatch) -> None:
    """An empty/whitespace extract -> None (empty text would draft junk beats).

    Search returns the same title so removing the empty-text guard would ground
    an empty-text WikiExtract -> the UNDO test goes RED."""
    monkeypatch.setattr(
        fetch,
        "http_get_json",
        _make_fake(_extract_response(text="   \n  "), _search_response()),
    )
    assert extract.resolve_and_fetch_extract(_poi()) is None


def test_disambiguation_returns_none(monkeypatch) -> None:
    """A disambiguation page -> None (never ground against a disambig list)."""
    monkeypatch.setattr(
        fetch,
        "http_get_json",
        _make_fake(_extract_response(disambiguation=True), _search_empty()),
    )
    assert extract.resolve_and_fetch_extract(_poi()) is None


def test_coordinate_mismatch_rejects_wrong_article(monkeypatch) -> None:
    """A name-ambiguous title whose article coords are far (>~500m) from the POI
    is rejected, so it never grounds the wrong place."""
    # Article ~2-3 km away from the POI.
    monkeypatch.setattr(
        fetch,
        "http_get_json",
        _make_fake(_extract_response(lat=51.54, lon=-0.10), _search_empty()),
    )
    assert extract.resolve_and_fetch_extract(_poi(lat=_BM_LAT, lon=_BM_LON)) is None


def test_name_search_fallback_when_no_wiki_source_url(monkeypatch) -> None:
    """A POI with NO en.wikipedia ``source_url`` resolves via a name search: the
    first call is the search, the second the extract fetch."""
    fake = _make_fake(_extract_response(), _search_response("British Museum"))
    monkeypatch.setattr(fetch, "http_get_json", fake)

    ext = extract.resolve_and_fetch_extract(
        _poi(source_urls=["https://www.wikidata.org/wiki/Q6373"])
    )

    assert ext is not None and ext.article_title == "British Museum"
    assert fake.calls[0][1].get("list") == "search"
    assert len(fake.calls) == 2


# ---------------------------------------------------------------------------
# build_live_extracts — coverage accounting.
# ---------------------------------------------------------------------------
def test_build_live_extracts_reports_coverage_and_misses(monkeypatch) -> None:
    """Map over POIs: collect resolved WikiExtracts, count misses by name, and
    SKIP an empty-slug POI (``slugify('東京') == ''``) into ``skipped_no_slug``."""

    def fake(url, params=None, **kw):
        p = dict(params or {})
        if p.get("list") == "search":
            return _search_empty()
        if p.get("titles") == "British Museum":
            return _extract_response(title="British Museum")
        return _extract_response(missing=True)

    monkeypatch.setattr(fetch, "http_get_json", fake)
    pois = [
        _poi("British Museum", source_urls=["https://en.wikipedia.org/wiki/British_Museum"]),
        _poi("Nowhere Place", source_urls=["https://en.wikipedia.org/wiki/Nowhere_Place"]),
        _poi("東京", source_urls=[]),  # slugify -> "" -> skipped, not fetched
    ]

    extracts, report = extract.build_live_extracts(pois)

    assert [e.poi_name for e in extracts] == ["British Museum"]
    assert report["resolved"] == 1
    assert report["missed"] == ["Nowhere Place"]
    assert "東京" in report["skipped_no_slug"]
    assert report["coverage_pct"] == 50.0  # 1 of 2 sluggable POIs grounded


def test_build_live_extracts_dedups_same_article_twin(monkeypatch) -> None:
    """Two DIFFERENT POIs that resolve to the SAME article (a
    geo-split ``"(2)"`` twin, e.g. "University of London" + "University of London
    (2)") must not both pin the same revision. The FIRST keeps the extract; the twin
    is recorded in ``duplicates`` and gets NO extract — hence no beats — so their
    byte-identical beats can never HASH_COLLISION downstream.

    Undo the same-article guard in ``build_live_extracts`` -> two extracts come back
    and ``duplicates`` is empty -> this test goes RED."""
    lat, lon = _BM_LAT, _BM_LON

    def fake(url, params=None, **kw):
        p = dict(params or {})
        if p.get("list") == "search":
            return _search_empty()
        # BOTH POIs' titles resolve to the SAME article + revision.
        return _extract_response(title="University of London", revid=555, lat=lat, lon=lon)

    monkeypatch.setattr(fetch, "http_get_json", fake)
    url = "https://en.wikipedia.org/wiki/University_of_London"
    pois = [
        _poi("University of London", lat=lat, lon=lon, source_urls=[url]),
        _poi("University of London (2)", lat=lat, lon=lon, source_urls=[url]),
    ]

    extracts, report = extract.build_live_extracts(pois)

    assert [e.poi_name for e in extracts] == ["University of London"]  # only the first
    assert report["resolved"] == 1
    assert report["duplicates"] == ["University of London (2)"]
    assert "University of London (2)" not in report["missed"]  # a dup is not a miss
    assert report["coverage_pct"] == 100.0  # the twin is excluded from coverage


# ---------------------------------------------------------------------------
# flow.build_extracts — live vs fixture branch.
# ---------------------------------------------------------------------------
def test_flow_build_extracts_live_branch(monkeypatch) -> None:
    """HTTP_MODE=='live' routes ``build_extracts`` to ``build_live_extracts`` with
    the FINAL assembled POIs, and returns just the extract list."""
    monkeypatch.setattr(fetch, "HTTP_MODE", "live")
    seen: dict = {}

    def fake_live(pois):
        seen["pois"] = pois
        ext = WikiExtract(poi_name="X", revid="1", text="t", article_title="X", url="u")
        return ([ext], {"resolved": 1, "missed": [], "coverage_pct": 100.0, "skipped_no_slug": []})

    monkeypatch.setattr(flow, "build_live_extracts", fake_live)

    out = flow.build_extracts("london", [{"name": "X"}])

    assert [e.poi_name for e in out] == ["X"]
    assert seen["pois"] == [{"name": "X"}]


def test_flow_build_extracts_fixture_branch_unchanged(monkeypatch) -> None:
    """Default (fixture) mode still reads the committed extracts fixture and
    IGNORES the pois arg — the ``make onboard-city CITY=london`` path."""
    monkeypatch.setattr(fetch, "HTTP_MODE", "fixture")

    out = flow.build_extracts("london", [])

    names = {e.poi_name for e in out}
    assert "British Museum" in names
    assert all(e.text for e in out)
