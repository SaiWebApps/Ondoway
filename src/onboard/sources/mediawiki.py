"""Shared MediaWiki URL/param builders for the wikipedia + wikivoyage connectors.

PURE helpers only — NO network and NO ``src.onboard.fetch`` import: these just
build the URL/params and unwrap a response, mirroring the query shape in
``scripts/wiki_fetch.py``. Both MediaWiki connectors build their GET via these so
neither re-implements the query shape; the actual fetch goes through
``base.run_connector`` -> ``fetch.http_get_json`` (the allowlist door).

Do NOT call ``scripts/wiki_fetch.fetch_article`` from a connector: it uses
``urllib`` straight to wikipedia and BYPASSES the ``fetch.py`` allowlist door.
``wiki_fetch`` is reused later for its pinned-revision save path only.
"""

from __future__ import annotations


def api_url(host: str) -> str:
    """The MediaWiki API endpoint for ``host`` (e.g. ``en.wikipedia.org``)."""
    return f"https://{host}/w/api.php"


def geosearch_params(
    lat: float, lon: float, radius_m: int = 10000, limit: int = 200
) -> dict:
    """Params for a MediaWiki ``list=geosearch`` query centred on ``(lat, lon)``."""
    return {
        "action": "query",
        "list": "geosearch",
        "gscoord": f"{lat}|{lon}",
        "gsradius": radius_m,
        "gslimit": limit,
        "format": "json",
    }


def extract_params(title: str) -> dict:
    """Params to pull a page's plain-text extract + coordinates by ``title``."""
    return {
        "action": "query",
        "prop": "extracts|coordinates",
        "explaintext": 1,
        "titles": title,
        "format": "json",
    }


def pages(payload: dict) -> list[dict]:
    """The list of page dicts from a MediaWiki ``query`` response."""
    return list(payload.get("query", {}).get("pages", {}).values())
