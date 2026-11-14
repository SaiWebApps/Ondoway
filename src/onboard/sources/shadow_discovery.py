"""SHADOW-DISCOVERY connector — the shadow-library discovery-ONLY path.

This connector surfaces shadow-library (Anna's Archive / WeLib) search hits as
metadata-only ``DiscoveryPointer``s, so a human can acquire the work LEGALLY and
feed it through the Manual book-drop flow. It NEVER fetches text: ``consult_url``
returns a HUMAN search-page link that is only ever surfaced as an outbound link
(never GET-ed — the shadow hosts are not on the ingest allowlist), and ``parse``
maps an already-provided metadata payload to pointers.

Connector isolation: this module imports ONLY ``src.onboard.models``. It
does NOT import ``src.onboard.fetch`` (the network door), ``src.onboard.sources
.base`` (``run_connector``, the ONE thing that fetches), or any HTTP client
(``httpx`` / ``requests`` / ``urllib.request``). With no network-capable import
there is no code path here that can fetch, so discovery can only ever surface
metadata pointers. Enforced structurally by
``test_shadow_discovery_never_imports_the_fetcher`` (an AST import check) in
tests/test_onboard_boundary.py.
"""

from __future__ import annotations

from src.onboard.models import CityContext, ConnectorResult, DiscoveryPointer


class ShadowDiscoveryConnector:
    """Surfaces shadow-library search hits as metadata-only ``DiscoveryPointer``s.

    Discovery only: it emits NO ``PoiCandidate`` and NO ``SourceDocument`` — and
    ``DiscoveryPointer`` has no field for body text, so no copyrighted text can
    travel with a pointer.
    """

    slug = "shadow_discovery"

    def consult_url(self, ctx: CityContext) -> tuple[str, dict | None]:
        """A human Anna's Archive SEARCH-PAGE link for the city, plus ``None``
        params. PURE — and this URL is only ever SURFACED as an outbound link,
        never GET-ed."""
        query = ctx.display_name.replace(" ", "+")
        return f"https://annas-archive.org/search?q={query}", None

    def parse(self, payload: dict, ctx: CityContext) -> ConnectorResult:
        """Map a metadata payload (a list of ``hits``) to metadata-only
        ``DiscoveryPointer``s. PURE — no network, no I/O. Emits NO
        ``PoiCandidate`` and NO ``SourceDocument``: discovery only."""
        pointers = [
            DiscoveryPointer(
                source="annas_archive",
                title=hit["title"],
                author=hit.get("author"),
                year=hit.get("year"),
                format=hit.get("format"),
                source_url=hit["source_url"],
            )
            for hit in payload.get("hits", [])
        ]
        return ConnectorResult(pointers=pointers, candidates=[], documents=[])
