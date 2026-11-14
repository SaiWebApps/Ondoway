"""The source-connector Protocol and the single network door for sources.

Every connector is PURE: ``consult_url`` names the URL + params it wants GET-ed,
and ``parse`` maps a raw JSON payload to a typed ``ConnectorResult`` — neither
touches the network or the filesystem. ``run_connector`` below is the ONE place
that actually fetches, and this module is the SINGLE caller of
``src.onboard.fetch`` in the ``sources`` package (which enforces the ingest
allowlist before any I/O).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from urllib.parse import urlencode

from src.onboard.fetch import http_get_json
from src.onboard.jobs import JobStore
from src.onboard.models import CityContext, ConnectorResult


@runtime_checkable
class SourceConnector(Protocol):
    """What every source connector implements.

    ``consult_url`` and ``parse`` are BOTH pure (no network, no I/O):
    ``consult_url`` names the URL + optional query params the connector wants
    GET-ed, and ``parse`` turns the raw JSON payload into a typed
    ``ConnectorResult``. ``parse`` is what the test bar exercises with a fixture.
    Only ``run_connector`` below touches the network.
    """

    slug: str

    def consult_url(self, ctx: CityContext) -> tuple[str, dict | None]:
        """The URL and optional query params this connector will GET. PURE."""
        ...

    def parse(self, payload: dict, ctx: CityContext) -> ConnectorResult:
        """Map a raw JSON ``payload`` to a typed result. PURE — no network/I/O."""
        ...


def run_connector(
    c: SourceConnector, ctx: CityContext, store: JobStore, job_id: str
) -> ConnectorResult:
    """Consult one connector through the network door — the ONLY code here that
    fetches.

    It builds the full URL (params urlencoded when present), emits a
    ``source_consult`` event naming the concrete URL, GETs the payload via
    ``http_get_json`` (which enforces the ingest allowlist FIRST), parses it,
    emits a ``candidate_batch`` event carrying SCALAR counts only (never body
    text in ``data``), and returns the typed result.
    """
    url, params = c.consult_url(ctx)
    full = f"{url}?{urlencode(params)}" if params else url
    store.append_event(
        job_id, "source_consult", f"Consulting {c.slug}", source=c.slug, url=full
    )
    payload = http_get_json(url, params=params)
    result = c.parse(payload, ctx)
    store.append_event(
        job_id,
        "candidate_batch",
        f"{c.slug}: {len(result.candidates)} candidates, {len(result.documents)} documents",
        source=c.slug,
        data={"candidates": len(result.candidates), "documents": len(result.documents)},
    )
    return result


def run_discovery(
    c: SourceConnector,
    payload: dict,
    ctx: CityContext,
    store: JobStore,
    job_id: str,
) -> ConnectorResult:
    """Run a shadow-discovery connector on an ALREADY-provided metadata payload.

    Unlike ``run_connector`` this does NOT fetch — the shadow-library hosts are
    never on the ingest allowlist, so keeping discovery network-free is what lets
    ``shadow_discovery`` surface pointers without ever touching a blocked door.
    It parses the payload, emits one ``discovery_pointer`` event per surfaced
    pointer (message + source + outbound url from the pointer, NO text in
    ``data``), and returns the typed result.
    """
    result = c.parse(payload, ctx)
    for pointer in result.pointers:
        store.append_event(
            job_id,
            "discovery_pointer",
            pointer.title,
            source=pointer.source,
            url=pointer.source_url,
        )
    return result
