"""Shared onboarding flow — the connector resolution + per-source consult +
Wikipedia-extract build that BOTH the CLI (``src/onboard/cli.py``) and the API
router (``src/api/routes/workbench/onboard.py``) drive.

These are the PURE flow primitives, decoupled from any CLI concern (no argparse,
no printing): given a ``JobStore`` + ``job_id`` they emit the SAME live progress
events a real run would, so the API's SSE stream and the CLI's summary observe an
identical event log. ``consult_source`` is the one seam that branches on the
network mode:

- ``fetch.HTTP_MODE == "live"`` → go through ``base.run_connector`` (the ingest
  allowlist door) which actually fetches; or
- the default ``fixture`` mode → load the connector's committed run fixture from
  ``tests/fixtures/onboard/{slug}/`` and call its PURE ``parse`` directly, while
  emitting the SAME ``source_consult`` + scalar-only ``candidate_batch`` events
  ``run_connector`` emits live — so the flow mirrors a real run without touching
  the network.

Splitting these out of ``cli.py`` keeps the API router from importing the CLI's
privates: both callers depend on this module's PUBLIC surface, nothing more.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from urllib.parse import urlencode

from scripts.beat_builder import slugify
from src.onboard import fetch
from src.onboard.assemble import WikiExtract
from src.onboard.extract import build_live_extracts
from src.onboard.jobs import JobStore
from src.onboard.models import CityContext, ConnectorResult
from src.onboard.sources.base import SourceConnector, run_connector
from src.onboard.sources.osm import OsmSource
from src.onboard.sources.wikidata import WikidataConnector
from src.onboard.sources.wikipedia import WikipediaConnector
from src.onboard.sources.wikivoyage import WikivoyageConnector

# Repo root: src/onboard/flow.py -> src/onboard -> src -> repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES_ROOT = REPO_ROOT / "tests" / "fixtures" / "onboard"

logger = logging.getLogger(__name__)

# The connectors an onboarding MODE consults. ``license_clean`` drives the four
# license-clean POI-discovery sources (order sets NAME/COORD precedence ties
# deterministically, matching test_onboard_assemble's _base_results).
MODE_CONNECTORS: dict[str, tuple[str, ...]] = {
    "license_clean": ("wikipedia", "wikivoyage", "wikidata", "osm"),
}

# Slug -> the PURE connector instance whose ``consult_url``/``parse`` this flow
# drives (no network — see module docstring).
CONNECTORS: dict[str, SourceConnector] = {
    "wikipedia": WikipediaConnector(),
    "wikivoyage": WikivoyageConnector(),
    "wikidata": WikidataConnector(),
    "osm": OsmSource(),
}

# Slug -> the committed run fixture the fixture-mode consult feeds that
# connector's ``parse``. Wikipedia uses the fuller ``run/`` geosearch; the others
# use the flat cross-source fixtures (mirrors test_onboard_assemble._base_results).
CONNECTOR_FIXTURE: dict[str, str] = {
    "wikipedia": "run/wikipedia_geosearch.json",
    "wikivoyage": "wikivoyage_page.json",
    "wikidata": "wikidata_sparql.json",
    "osm": "osm_overpass.json",
}


class OnboardError(Exception):
    """A user-facing onboarding failure (the CLI prints it to stderr with a
    non-zero exit; the API converts it into an ``error`` event + status)."""


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def env_path(name: str) -> Path | None:
    """The ``Path`` in env var ``name``, or ``None`` when unset or empty, so
    ``write_city`` falls back to the real ``data/`` and ``src/cities.json``."""
    value = os.getenv(name)
    return Path(value) if value else None


def resolve_connectors(modes: list[str]) -> list[str]:
    """The ordered, de-duplicated connector slugs for the requested modes.

    Raises ``OnboardError`` on an unknown mode — a silent no-op would ship a
    thin/empty city.
    """
    ordered: list[str] = []
    for mode in modes:
        if mode not in MODE_CONNECTORS:
            raise OnboardError(
                f"unknown mode {mode!r}; supported modes are {sorted(MODE_CONNECTORS)}"
            )
        for slug in MODE_CONNECTORS[mode]:
            if slug not in ordered:
                ordered.append(slug)
    return ordered


def consult_source(
    slug: str, ctx: CityContext, store: JobStore, job_id: str
) -> ConnectorResult:
    """Consult ONE source, emitting the same live events either mode would.

    In ``live`` mode this delegates to ``base.run_connector`` (the network door).
    In the default ``fixture`` mode it emits a ``source_consult`` event naming the
    concrete URL the connector WOULD GET live, then — instead of fetching, which
    the fixture-mode door forbids — parses the committed fixture and emits a
    scalar-only ``candidate_batch`` event (never body text in ``data``).

    RESILIENCE: a per-connector RUN failure (an HTTP 403/timeout, a malformed
    payload, any parse error) must NOT abort the whole onboarding — several
    sources are consulted and a human reviews the output, so one missing source
    should simply drop cross-source agreement, not tank the run. On such a failure
    this emits a VISIBLE ``error`` event naming the source (never swallowed
    silently) and returns an EMPTY ``ConnectorResult`` so ``assemble`` proceeds
    with the OTHER sources' candidates. A CONFIG error (``OnboardError`` — e.g. a
    missing fixture) is NOT a source failure and still propagates loudly.
    """
    connector = CONNECTORS[slug]
    try:
        if fetch.HTTP_MODE == "live":
            return run_connector(connector, ctx, store, job_id)

        fixture = FIXTURES_ROOT / ctx.slug / CONNECTOR_FIXTURE[slug]
        if not fixture.exists():
            raise OnboardError(f"no {slug} fixture for {ctx.slug!r}: {fixture} not found")

        url, params = connector.consult_url(ctx)
        full = f"{url}?{urlencode(params)}" if params else url
        store.append_event(job_id, "source_consult", f"Consulting {slug}", source=slug, url=full)

        result = connector.parse(load_json(fixture), ctx)
        store.append_event(
            job_id,
            "candidate_batch",
            f"{slug}: {len(result.candidates)} candidates, {len(result.documents)} documents",
            source=slug,
            data={"candidates": len(result.candidates), "documents": len(result.documents)},
        )
        return result
    except OnboardError:
        # A config/setup error (missing fixture) is not a degradable source
        # failure — surface it loudly.
        raise
    except Exception as exc:
        # Any source-run failure (HTTP/timeout/parse) degrades gracefully: a
        # VISIBLE error event, and an empty result so assemble uses the rest.
        store.append_event(job_id, "error", f"{slug} failed: {exc}", source=slug)
        return ConnectorResult()


def _log_extract_coverage(slug: str, report: dict) -> None:
    """Surface a LIVE extract-coverage report LOUDLY (never silent thinness): an
    INFO summary always, WARNINGs for the miss list, any empty-slug skips, and a
    below-floor coverage."""
    resolved = report["resolved"]
    missed = report["missed"]
    skipped = report.get("skipped_no_slug", [])
    duplicates = report.get("duplicates", [])
    total = resolved + len(missed)
    logger.info(
        "build_extracts(%s): live Wikipedia coverage %.1f%% — %d/%d POIs grounded",
        slug,
        report["coverage_pct"],
        resolved,
        total,
    )
    if missed:
        logger.warning(
            "build_extracts(%s): %d POI(s) got NO Wikipedia extract (ungrounded): %s",
            slug,
            len(missed),
            ", ".join(missed),
        )
    if skipped:
        logger.warning(
            "build_extracts(%s): %d POI(s) skipped (empty slug, unkeyable): %s",
            slug,
            len(skipped),
            ", ".join(skipped),
        )
    if duplicates:
        logger.warning(
            "build_extracts(%s): %d POI(s) dropped as same-article duplicates "
            "(twin of an already-grounded POI, no beats): %s",
            slug,
            len(duplicates),
            ", ".join(duplicates),
        )
    if report.get("below_floor"):
        logger.warning(
            "build_extracts(%s): live coverage %.1f%% is BELOW the %.1f%% floor — thin city",
            slug,
            report["coverage_pct"],
            report.get("floor_pct", 0.0),
        )


def build_extracts(slug: str, pois: list[dict]) -> list[WikiExtract]:
    """Pinned ``WikiExtract``s for a city, keyed later by ``slugify(poi_name)``.

    LIVE (``fetch.HTTP_MODE == "live"``): resolve + fetch each FINAL assembled
    POI's CURRENT en.wikipedia revision via ``extract.build_live_extracts(pois)``,
    then log the coverage report loudly (no silent thinness) and return the list.
    Because the live path needs the merged/filtered POI NAMES, callers must pass
    the assembled POIs here (fetched AFTER ``assemble``).

    FIXTURE (default): read the committed ``run/wikipedia_extracts.json`` (``pois``
    is ignored). Each entry is ``{title: {"revid": ..., "extract": ...}}``;
    ``write_city`` saves each as ``wikipedia/{slug}-rev-{revid}.txt`` verbatim,
    which the beat drafter quotes and ``validate_beats`` grounds against.
    """
    if fetch.HTTP_MODE == "live":
        extracts, report = build_live_extracts(pois)
        _log_extract_coverage(slug, report)
        return extracts

    raw = load_json(FIXTURES_ROOT / slug / "run" / "wikipedia_extracts.json")
    return [
        WikiExtract(
            poi_name=title,
            revid=str(entry["revid"]),
            text=entry["extract"],
            article_title=title,
            url=f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}?oldid={entry['revid']}",
        )
        for title, entry in raw.items()
    ]


def build_extract_index(slug: str, pois: list[dict]) -> dict[str, WikiExtract]:
    """``build_extracts`` keyed by ``slugify(poi_name)`` — the SAME key ``assemble``
    uses for ``AssembledCity.wiki_extracts``. Callers assemble the city with EMPTY
    extracts (so the final POI names exist), then attach this index — which fetches
    live extracts for those POIs (or reads the fixture) and keys them identically."""
    return {slugify(e.poi_name): e for e in build_extracts(slug, pois)}
