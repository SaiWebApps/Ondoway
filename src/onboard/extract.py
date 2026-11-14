"""LIVE English-Wikipedia extract fetching for onboarding beats.

In a LIVE onboarding run each assembled POI's beats must be grounded against that
POI's CURRENT Wikipedia revision — not a stale committed fixture (which is what
``flow.build_extracts`` reads in the default fixture mode, and which would ground
a live city's beats against fake revisions while leaving most POIs with no beats
at all). This module is the live path.

Two public surfaces:

- ``resolve_and_fetch_extract(poi)`` — resolve ONE POI's en.wikipedia article
  (from its discovery ``source_urls``, an explicit wiki title/tag, or a name
  search), verify the article's own coordinates sit near the POI (so a
  name-ambiguous title never grounds the wrong place), then fetch + pin the
  current revision's plain-text lead extract. Returns ``None`` — never a junk
  ``WikiExtract`` — for a missing page, a disambiguation page, or an empty extract.
- ``build_live_extracts(pois)`` — map that over the final POI list and return
  ``(extracts, report)`` where ``report`` accounts for EVERY POI: how many
  resolved, which names missed, which were skipped (empty slug), and the coverage
  percentage, so ``flow`` can surface thinness LOUDLY (no silent under-grounding).

Every network hop goes through ``fetch.http_get_json`` — the ingest allowlist door
(``en.wikipedia.org`` is allowlisted, the descriptive User-Agent is already set).
This module NEVER shells out to ``scripts/wiki_fetch.py`` (that writes with the
platform text encoding and hardcodes the ``data/`` path); it reuses only the
MediaWiki query SHAPE.
"""

from __future__ import annotations

import logging
import math
import re
from urllib.parse import unquote, urlparse

from scripts.beat_builder import slugify
from src.onboard import fetch
from src.onboard.assemble import WikiExtract
from src.onboard.sources.mediawiki import api_url

logger = logging.getLogger(__name__)

# The en.wikipedia MediaWiki API endpoint (reuses the shared builder).
_WIKI_API = api_url("en.wikipedia.org")

# A resolved article's own coordinates must sit within this of the POI, or the
# title is treated as a wrong (name-ambiguous) match and dropped.
_COORD_TOLERANCE_M = 500.0

# Below this live-coverage floor, ``build_live_extracts`` flags the run as thin so
# the caller can shout about it (a human reviews the output — never ship silently).
_COVERAGE_FLOOR_PCT = 50.0


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points, in metres."""
    r = 6371000.0  # mean Earth radius (m)
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _enwiki_title_from_url(url: str) -> str | None:
    """The article title from an ``en.wikipedia.org/wiki/{Title}`` URL, else None.

    The ``?oldid=`` / ``#section`` parts live in the query/fragment (not the path),
    so the ``/wiki/`` path segment is the clean, percent-encoded title."""
    parsed = urlparse(url)
    if (parsed.hostname or "").lower() != "en.wikipedia.org":
        return None
    match = re.match(r"^/wiki/(.+)$", parsed.path)
    if not match:
        return None
    title = unquote(match.group(1)).replace("_", " ").strip()
    return title or None


def _candidate_titles(poi: dict) -> list[str]:
    """The ordered, de-duplicated title candidates to resolve for ``poi``:

    (1) the en.wikipedia article title parsed from its discovery ``source_urls``;
    (2) an explicit wiki title / OSM-style ``en:Title`` tag the POI may carry
        (defensive — the assembled POI shape does not currently emit these, but a
        richer future candidate would, and reading them costs nothing).
    A name search is a separate LAST resort handled by the caller, not listed here.
    """
    titles: list[str] = []
    pipeline = poi.get("_pipeline", {}) or {}
    for url in pipeline.get("source_urls", []) or []:
        title = _enwiki_title_from_url(url)
        if title:
            titles.append(title)
    for key in ("wikipedia_title", "en_wikipedia_title"):
        value = poi.get(key) or pipeline.get(key)
        if isinstance(value, str) and value.strip():
            titles.append(value.strip())
    tag = poi.get("wikipedia") or pipeline.get("wikipedia_tag")
    if isinstance(tag, str) and tag.startswith("en:") and tag[3:].strip():
        titles.append(tag[3:].strip())

    seen: set[str] = set()
    unique: list[str] = []
    for title in titles:
        if title not in seen:
            seen.add(title)
            unique.append(title)
    return unique


def _extract_params(title: str) -> dict:
    """MediaWiki params for the LEAD-SECTION plain-text extract + current revid +
    coordinates of ``title``. ``exintro=1`` restricts the extract to the lead
    section (the coherent definitional summary) so the beat drafter never carves
    deep-article, off-topic spans; the rest is the ``scripts/wiki_fetch.py`` shape
    plus ``coordinates`` (for the wrong-article geo check) and ``redirects``."""
    return {
        "action": "query",
        "format": "json",
        "redirects": 1,
        "titles": title,
        "prop": "extracts|revisions|coordinates|pageprops",
        "explaintext": 1,
        "exintro": 1,
        "exsectionformat": "plain",
        "exlimit": 1,
        "rvprop": "ids",
    }


def _search_title(name: str) -> str | None:
    """Resolve a POI name to an article title via a namespace-0 title search
    (last-resort resolution). Returns the top hit's title, or None."""
    params = {
        "action": "query",
        "format": "json",
        "list": "search",
        "srsearch": name,
        "srnamespace": 0,
        "srlimit": 1,
    }
    data = fetch.http_get_json(_WIKI_API, params)
    hits = data.get("query", {}).get("search", []) or []
    return hits[0]["title"] if hits else None


def _first_page(data: dict) -> dict | None:
    """The single page dict from a MediaWiki ``query.pages`` response, or None."""
    pages = data.get("query", {}).get("pages", {}) or {}
    return next(iter(pages.values()), None)


def _fetch_and_verify(
    title: str, poi_name: str, poi_lat: float | None, poi_lon: float | None
) -> WikiExtract | None:
    """Fetch ``title``'s current revision and return a pinned ``WikiExtract`` — or
    None if the page is missing/invalid, is a disambiguation page, has an empty
    extract, carries no revid, or sits too far from the POI's coordinates."""
    data = fetch.http_get_json(_WIKI_API, _extract_params(title))
    page = _first_page(data)
    if page is None or "missing" in page or "invalid" in page:
        return None
    if "disambiguation" in (page.get("pageprops") or {}):
        return None

    text = (page.get("extract") or "").strip()
    if not text:
        return None  # empty extract -> would draft junk beats

    revisions = page.get("revisions") or []
    revid = revisions[0].get("revid") if revisions else None
    if revid is None:
        return None

    resolved_title = page.get("title") or title

    # Wrong-article guard: when BOTH the POI and the article are placeable, the
    # article must sit near the POI. An article with no coordinates can't be
    # checked, so it is accepted (many valid articles carry no coords).
    if poi_lat is not None and poi_lon is not None:
        for coord in page.get("coordinates") or []:
            clat, clon = coord.get("lat"), coord.get("lon")
            if clat is not None and clon is not None:
                if _haversine_m(poi_lat, poi_lon, clat, clon) > _COORD_TOLERANCE_M:
                    return None
                break  # verified against the primary coordinate

    url_title = resolved_title.replace(" ", "_")
    return WikiExtract(
        poi_name=poi_name,
        revid=str(revid),
        text=text,
        article_title=resolved_title,
        url=f"https://en.wikipedia.org/wiki/{url_title}?oldid={revid}",
    )


def resolve_and_fetch_extract(poi: dict) -> WikiExtract | None:
    """Resolve ``poi``'s en.wikipedia article and pin its current revision.

    Resolution order: (1) a title parsed from the POI's ``source_urls`` / carried
    wiki tag; (2) a name search. Each candidate is fetched + verified (coords near
    the POI, non-empty extract, real revid, not a disambiguation/missing page); the
    first that verifies wins. Returns None when nothing resolves cleanly.
    """
    poi_lat = poi.get("latitude")
    poi_lon = poi.get("longitude")
    poi_name = poi["name"]

    for title in _candidate_titles(poi):
        extract = _fetch_and_verify(title, poi_name, poi_lat, poi_lon)
        if extract is not None:
            return extract

    searched = _search_title(poi_name)
    if searched:
        return _fetch_and_verify(searched, poi_name, poi_lat, poi_lon)
    return None


def build_live_extracts(pois: list[dict]) -> tuple[list[WikiExtract], dict]:
    """Fetch a live ``WikiExtract`` per POI and account for EVERY POI.

    Returns ``(extracts, report)``. ``report`` = ``{"resolved": int, "missed":
    [name, ...], "skipped_no_slug": [name, ...], "duplicates": [name, ...],
    "coverage_pct": float, "floor_pct": float, "below_floor": bool}``. A POI whose
    ``slugify(name) == ""`` (e.g. a wholly non-Latin name that ``assemble`` should
    already have filtered) is SKIPPED and recorded — never fetched — because it
    cannot be keyed to disk without colliding with every other empty-slug POI.

    SAME-ARTICLE DEDUP: in a dense city two DIFFERENT POIs can resolve to the SAME
    en.wikipedia article — most often a geo-split "(2)" twin of a big landmark
    ("University of London" + "University of London (2)"). Both would pin the SAME
    revision, so both would draft IDENTICAL beats and ``validate_beats`` would
    HARD-FAIL with ``HASH_COLLISION``. So the FIRST POI to resolve to a given
    article (by resolved title OR revid) keeps the extract; any later POI resolving
    to the same article is recorded in ``duplicates`` and gets NO extract — hence no
    beats — dropping the pointless twin from the corpus. ``coverage_pct`` is over the
    SLUGGABLE, NON-DUPLICATE POIs (resolved / (resolved + missed)); a below-floor run
    is flagged so the caller can shout about the thinness rather than ship it silently.
    """
    extracts: list[WikiExtract] = []
    missed: list[str] = []
    skipped: list[str] = []
    duplicates: list[str] = []
    seen_titles: set[str] = set()
    seen_revids: set[str] = set()

    for poi in pois:
        name = poi["name"]
        if slugify(name) == "":
            skipped.append(name)
            continue
        extract = resolve_and_fetch_extract(poi)
        if extract is None:
            missed.append(name)
            continue
        if extract.article_title in seen_titles or extract.revid in seen_revids:
            # Same article as an already-kept POI — a redundant twin. Drop it (no
            # extract -> no beats) so its identical beats never collide file-wide.
            duplicates.append(name)
            continue
        seen_titles.add(extract.article_title)
        seen_revids.add(extract.revid)
        extracts.append(extract)

    sluggable = len(extracts) + len(missed)
    coverage_pct = round(100.0 * len(extracts) / sluggable, 1) if sluggable else 0.0
    report = {
        "resolved": len(extracts),
        "missed": missed,
        "skipped_no_slug": skipped,
        "duplicates": duplicates,
        "coverage_pct": coverage_pct,
        "floor_pct": _COVERAGE_FLOOR_PCT,
        "below_floor": coverage_pct < _COVERAGE_FLOOR_PCT,
    }
    return extracts, report
