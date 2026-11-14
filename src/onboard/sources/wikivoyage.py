"""The Wikivoyage source connector.

Wikivoyage's POI listings live in the raw WIKITEXT as ``{{listing ...}}``
templates (``name``/``lat``/``long``/... params), NOT in the plain-text extract
a normal ``prop=extracts`` query returns — the extractor strips exactly that
markup. So this connector asks for the main-slot revision *content*
(``prop=revisions&rvprop=content&rvslots=main``) and regex-mines the templates.

Like ``mediawiki``, this module is PURE: ``consult_url`` names the URL+params to
GET and ``parse`` maps a raw payload to a typed ``ConnectorResult`` — neither
touches the network or the filesystem (it does NOT import ``src.onboard.fetch``).
Only ``base.run_connector`` fetches.
"""

from __future__ import annotations

import re

from src.onboard.models import (
    CityContext,
    ConnectorResult,
    PoiCandidate,
    SourceDocument,
)
from src.onboard.sources.mediawiki import api_url, pages

# The OPENING of a POI-listing template. Wikivoyage curates POIs under THREE
# template names — the generic ``{{listing}}`` plus the section-specific
# ``{{see}}`` and ``{{do}}`` — so matching all three turns each into a curation
# VOTE (the template name is captured into ``meta['listing_type']`` so the
# assemble filter can weigh a hand-curated see/do sight over a raw geo-hit).
#
# We deliberately do NOT match the closing ``}}`` with a regex: a non-greedy
# ``(.*?)\}\}`` stops at the FIRST ``}}`` — including one that closes a NESTED
# template ({{dead link|...}}, {{cite web|...}}, {{convert|...}}), routine in real
# Wikivoyage wikitext — truncating the body before the real ``lat=``/``long=``
# fields and silently dropping coordinates. Instead ``_listings`` walks the braces
# to find the balanced end. The ``\b`` keeps ``{{listings``/``{{doable`` from
# matching (only whole template names count).
_LISTING_START_RE = re.compile(r"\{\{(listing|see|do)\b", re.IGNORECASE)


def _listings(wikitext: str) -> list[tuple[str, str]]:
    """Every listing template as ``(listing_type, body)``, BRACE-BALANCED.

    ``listing_type`` is the lower-cased template name (``listing``/``see``/``do``)
    that opened it. For each opener we walk forward tracking nesting depth (+1 on
    each ``{{``, -1 on each ``}}``) and take the body up to the ``}}`` that returns
    depth to 0, so nested templates inside the body do NOT terminate it early. An
    unclosed template (depth never returns to 0) is skipped, as the old regex also
    emitted nothing without a closing ``}}``.
    """
    listings: list[tuple[str, str]] = []
    n = len(wikitext)
    for start in _LISTING_START_RE.finditer(wikitext):
        listing_type = start.group(1).lower()  # the template name that voted
        body_start = start.end()  # just past ``{{<name>``
        depth = 1  # the ``{{`` we just matched is open
        i = body_start
        while i < n:
            pair = wikitext[i : i + 2]
            if pair == "{{":
                depth += 1
                i += 2
            elif pair == "}}":
                depth -= 1
                i += 2
                if depth == 0:
                    listings.append((listing_type, wikitext[body_start : i - 2]))
                    break
            else:
                i += 1
    return listings


def _field(body: str, key: str) -> str | None:
    """The value of a ``key=...`` param inside a template body, or None/"" -> None.

    Values run to the next pipe, closing brace or newline (the template param
    delimiters). ``\\b`` before the key stops ``long`` from matching inside
    ``longitude`` etc. and stops ``lat`` from matching a longer word.
    """
    m = re.search(rf"\b{key}\s*=\s*([^|}}\n]*)", body, re.IGNORECASE)
    if not m:
        return None
    return m.group(1).strip() or None


def _coord(raw: str | None) -> float | None:
    """Parse a coordinate param to float; a missing/blank/garbage value -> None."""
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


class WikivoyageConnector:
    """Consults ``en.wikivoyage.org`` for a city's page and mines its
    ``{{listing}}`` templates into POI candidates plus one cc-by-sa full-text
    document (the raw wikitext, kept intact for downstream extraction)."""

    slug = "wikivoyage"

    def consult_url(self, ctx: CityContext) -> tuple[str, dict | None]:
        """The revisions query whose main slot returns the raw wikitext. PURE."""
        return (
            api_url("en.wikivoyage.org"),
            {
                "action": "query",
                "prop": "revisions",
                "rvprop": "content",
                "rvslots": "main",
                "titles": ctx.display_name,
                "format": "json",
            },
        )

    def parse(self, payload: dict, ctx: CityContext) -> ConnectorResult:
        """Map the revisions payload to candidates + one cc-by-sa document. PURE."""
        page_list = pages(payload)
        if not page_list:
            return ConnectorResult()

        page = page_list[0]
        revisions = page.get("revisions") or []
        if not revisions:
            return ConnectorResult()

        main = revisions[0].get("slots", {}).get("main", {})
        wikitext = main.get("*") or main.get("content") or ""
        if not wikitext:
            return ConnectorResult()

        page_url = (
            f"https://en.wikivoyage.org/wiki/{ctx.display_name.replace(' ', '_')}"
        )

        candidates: list[PoiCandidate] = []
        for listing_type, body in _listings(wikitext):
            name = _field(body, "name")
            if not name:
                continue  # a listing with no name is not an identifiable POI
            candidates.append(
                PoiCandidate(
                    name=name,
                    source="wikivoyage",
                    source_url=page_url,
                    latitude=_coord(_field(body, "lat")),
                    longitude=_coord(_field(body, "long")),
                    # Which template curated this POI — a see/do sight is a
                    # stronger curation vote than a bare {{listing}} the filter
                    # can weigh.
                    meta={"listing_type": listing_type},
                )
            )

        document = SourceDocument(
            source="wikivoyage",
            license="cc_by_sa",
            title=ctx.display_name,
            url=page_url,
            text=wikitext,
        )

        return ConnectorResult(candidates=candidates, documents=[document])
