"""The WIKIDATA source connector.

PURE, like every source connector: ``consult_url`` names the WDQS SPARQL
endpoint + a bounding-box query for the city, and ``parse`` maps the returned
SPARQL-JSON to typed ``PoiCandidate`` hits. Neither touches the network or the
filesystem — the actual GET goes through ``base.run_connector`` ->
``fetch.http_get_json`` (the allowlist door). Do NOT import ``src.onboard.fetch``
here.

COORDINATE ORDER is the load-bearing detail. A Wikidata ``P625`` coordinate
arrives as a WKT string ``"Point(LON LAT)"`` — LONGITUDE FIRST, then latitude.
``_parse_point`` splits it accordingly, so ``longitude`` gets the first token and
``latitude`` the second. Swapping them would place every POI ~90° off (London at
latitude -0.13 instead of 51.5).
"""

from __future__ import annotations

from src.onboard.models import CityContext, ConnectorResult, PoiCandidate

# WDQS SPARQL endpoint. The provider host must be on the ingest allowlist for the
# real fetch (enforced by fetch.http_get_json, not here).
SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"

# DIRECT P31 (instance-of) Q-ids for event / occurrence classes, excluded
# server-side. MIRRORS ``assemble._EVENT_P31_CLASSES`` (kept in sync deliberately;
# a cross-import from the pure connector into assemble would invert the layering).
# The exclusion is a DIRECT ``wdt:P31`` membership test — NOT the transitive
# ``wdt:P31/wdt:P279*`` subclass walk, which is what timed WDQS out (see below).
_EVENT_P31_QIDS: tuple[str, ...] = (
    "Q1656682",   # event
    "Q1190554",   # occurrence
    "Q16510064",  # sporting event
    "Q13406554",  # sports competition
    "Q18608583",  # recurring event
    "Q1079023",   # ceremony
)


def _box_query(ctx: CityContext) -> str:
    """A valid WDQS ``wikibase:box`` query selecting every item with a P625
    coordinate inside ``ctx.bbox``, plus the NOTABILITY + ARTICLE signals the
    assemble filter needs to cut a live city's ~55K-hit firehose down to
    the real landmarks:

    - ``?sitelinks`` — ``wikibase:sitelinks`` count (how many Wikipedias link it).
      This is the DOMINANT ranking signal the assemble cap + tiering sort by, so
      it MUST come back for every hit; the query is kept cheap precisely so it does.
    - ``?enwiki`` — the English Wikipedia article title (via ``schema:about`` /
      ``schema:isPartOf <https://en.wikipedia.org/>`` / ``schema:name``).

    SERVER-SIDE RELIEF so WDQS does not TIME OUT on a big box (the bug this query
    is written against — a Greater-London box timed the prior query out, which the
    flow's error-resilience turned into an EMPTY result: no sitelinks -> every POI
    scored equal -> the cap + tiers collapsed to alphabetical):

    - ``FILTER(?sitelinks >= 3)`` — a sitelink floor drops the long tail of
      un-notable geotagged nodes before they ever leave the server.
    - ``FILTER NOT EXISTS { ?place wdt:P31 ?ec . VALUES ?ec { ... } }`` — excludes
      events/occurrences by a DIRECT ``wdt:P31`` membership test. The prior query
      used ``wdt:P31/wdt:P279*`` (a transitive subclass walk) which, together with
      a ``GROUP_CONCAT`` P31 aggregation, blew past the WDQS timeout on a dense
      box (measured: ~55-65s -> 504/read-timeout). Both are dropped here; the
      direct membership test is cheap, and ``assemble._EVENT_P31_CLASSES`` still
      class-gates events LOCALLY from any older fixture that carries ``meta['p31']``.

    The ``?p31`` GROUP_CONCAT column and its ``GROUP BY`` are gone (they were the
    dominant cost); ``parse`` still reads ``p31`` from a binding defensively, so a
    committed fixture that carries it is unaffected.

    Corners are WKT ``Point(LON LAT)``: SW = (min_lon, min_lat), NE = (max_lon,
    max_lat). The label comes from ``rdfs:label``. WDQS ships the
    wdt/wd/bd/wikibase/geo/rdfs/schema prefixes by default.
    """
    min_lat, max_lat, min_lon, max_lon = ctx.bbox
    sw = f"Point({min_lon} {min_lat})"  # WKT lon-first: south-west corner
    ne = f"Point({max_lon} {max_lat})"  # WKT lon-first: north-east corner
    events = " ".join(f"wd:{q}" for q in _EVENT_P31_QIDS)
    return (
        "SELECT ?place ?placeLabel ?coord ?sitelinks ?enwiki WHERE {\n"
        "  SERVICE wikibase:box {\n"
        "    ?place wdt:P625 ?coord .\n"
        f'    bd:serviceParam wikibase:cornerSouthWest "{sw}"^^geo:wktLiteral .\n'
        f'    bd:serviceParam wikibase:cornerNorthEast "{ne}"^^geo:wktLiteral .\n'
        "  }\n"
        "  ?place wikibase:sitelinks ?sitelinks .\n"
        "  FILTER(?sitelinks >= 3)\n"
        f"  FILTER NOT EXISTS {{ ?place wdt:P31 ?ec . VALUES ?ec {{ {events} }} }}\n"
        '  OPTIONAL { ?place rdfs:label ?placeLabel . FILTER(LANG(?placeLabel) = "en") }\n'
        "  OPTIONAL {\n"
        "    ?article schema:about ?place ;\n"
        "             schema:isPartOf <https://en.wikipedia.org/> ;\n"
        "             schema:name ?enwiki .\n"
        "  }\n"
        "}"
    )


def _parse_point(wkt: str) -> tuple[float, float]:
    """Parse a WKT ``"Point(LON LAT)"`` into ``(longitude, latitude)`` floats.

    LON IS FIRST: strip the ``Point(`` prefix and trailing ``)``, split on
    whitespace to ``[lon_str, lat_str]``.
    """
    inner = wkt.strip().removeprefix("Point(").removesuffix(")")
    lon_str, lat_str = inner.split()
    return float(lon_str), float(lat_str)


class WikidataConnector:
    """Consults the WDQS bounding-box SPARQL query for a city and yields one
    ``PoiCandidate`` per Wikidata item found inside the bbox."""

    slug = "wikidata"

    def consult_url(self, ctx: CityContext) -> tuple[str, dict | None]:
        """The WDQS endpoint + JSON-format box query for ``ctx``. PURE."""
        return (SPARQL_ENDPOINT, {"format": "json", "query": _box_query(ctx)})

    def parse(self, payload: dict, ctx: CityContext) -> ConnectorResult:
        """Map a WDQS SPARQL-JSON ``payload`` to typed candidates. PURE — no I/O.

        Each binding carries an entity URI (``place``), a label (``placeLabel``),
        a WKT coordinate (``coord``), and the notability/article signals the
        assemble filter consumes: ``sitelinks`` (int), ``enwiki`` (the
        article title), and ``p31`` (pipe-joined class Q-ids). The Q-number is the
        URI tail, kept as a provenance scalar in ``meta['qid']``.
        """
        candidates: list[PoiCandidate] = []
        for binding in payload.get("results", {}).get("bindings", []):
            # Read every field defensively: real WDQS legitimately OMITS a key
            # (absent, not null) — e.g. no bindable English label, or an entity
            # without a P625 coordinate — and bracket access would KeyError.
            place_uri = binding.get("place", {}).get("value")
            name = binding.get("placeLabel", {}).get("value")
            coord_wkt = binding.get("coord", {}).get("value")
            # A candidate with no usable name is useless for assemble dedup; one
            # with no coordinate can't be placed. Skip either rather than crash
            # or emit a degenerate candidate.
            if not name or not coord_wkt or not place_uri:
                continue
            lon, lat = _parse_point(coord_wkt)
            # NOTABILITY + ARTICLE signals, each guarded with .get so an omitted
            # binding degrades (sitelinks -> 0, enwiki -> None, p31 -> "") rather
            # than KeyErrors. These meta scalars are exactly what the assemble
            # filter reads to cut the 55K firehose to real landmarks.
            sitelinks_raw = binding.get("sitelinks", {}).get("value")
            candidates.append(
                PoiCandidate(
                    name=name,
                    source="wikidata",
                    source_url=place_uri,
                    latitude=lat,
                    longitude=lon,
                    meta={
                        "qid": place_uri.rsplit("/", 1)[-1],
                        "sitelinks": int(sitelinks_raw) if sitelinks_raw else 0,
                        "p31": binding.get("p31", {}).get("value") or "",
                        "enwiki": binding.get("enwiki", {}).get("value"),
                    },
                )
            )
        return ConnectorResult(candidates=candidates)
