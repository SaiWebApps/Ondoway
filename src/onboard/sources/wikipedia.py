"""Wikipedia source connector.

PURE (no network, no I/O): ``consult_url`` names the MediaWiki
``list=geosearch`` GET centred on the city's bbox centre, and ``parse`` maps
that raw payload into a typed ``ConnectorResult`` of ``PoiCandidate`` hits. Only
``base.run_connector`` ever fetches — this module never imports
``src.onboard.fetch``. The URL/param shape is built via the shared ``mediawiki``
helpers so this connector never re-implements the query shape.
"""

from __future__ import annotations

from src.onboard.models import CityContext, ConnectorResult, PoiCandidate
from src.onboard.sources.mediawiki import api_url, geosearch_params


class WikipediaConnector:
    """Geosearch en.wikipedia.org for named places near the city centre.

    Each geosearch hit already carries coordinates, so every emitted candidate
    is guaranteed a ``latitude``/``longitude`` — that is the whole point of using
    geosearch for POI discovery.
    """

    slug = "wikipedia"

    def consult_url(self, ctx: CityContext) -> tuple[str, dict | None]:
        """The en.wikipedia geosearch GET centred on the bbox centre. PURE."""
        return api_url("en.wikipedia.org"), geosearch_params(*ctx.centre)

    def parse(self, payload: dict, ctx: CityContext) -> ConnectorResult:
        """Map a ``list=geosearch`` payload to coordinate-bearing candidates.

        PURE — no network/I/O. ``payload["query"]["geosearch"]`` is a list where
        each item carries ``pageid``, ``title``, ``lat`` and ``lon``.
        """
        hits = payload.get("query", {}).get("geosearch", [])
        candidates = [
            PoiCandidate(
                name=hit["title"],
                source="wikipedia",
                source_url=(
                    f"https://en.wikipedia.org/wiki/{hit['title'].replace(' ', '_')}"
                ),
                latitude=hit["lat"],
                longitude=hit["lon"],
                meta={"pageid": hit["pageid"]},
            )
            for hit in hits
        ]
        return ConnectorResult(candidates=candidates)
