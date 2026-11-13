"""INTERNET_ARCHIVE source connector — with the F6 per-item public-domain wall.

archive.org is allowlisted BY HOST (grey-zone wall 2), but the SAME host also
serves in-copyright Controlled-Digital-Lending scans. Being reachable through
the allowlist door therefore does NOT prove an item is public-domain. This
connector closes that gap: ``_is_public_domain`` verifies per-item PD status and
``parse`` emits a ``SourceDocument`` ONLY for items that pass — anything not
provably PD emits NOTHING. That refusal (asserted by the F6 zero-documents test)
is a committed legal wall.

CONSERVATIVE CONTRACT (fail-closed, ONE authoritative signal). This wall keyed
on the ``licenseurl`` acceptance path THREE times and it broke THREE times — a
substring bug, then a host-spoof, then an OR-override where a genuine-host PD
``licenseurl`` overrode an explicit ``possible-copyright-status: IN_COPYRIGHT``.
So auto-ingest now keys SOLELY on IA's own computed
``possible-copyright-status`` being EXACTLY ``NOT_IN_COPYRIGHT``. There is no
``licenseurl`` path and no free-text ``rights`` path: neither grants acceptance
under any circumstance. A genuinely public-domain item that LACKS that computed
status is intentionally NOT auto-ingested here — reach for the Manual book-drop
instead. This is deliberately fail-closed: it is a copyright wall, and refusing
a real PD item is cheap while ingesting one in-copyright scan is not.

PURE, like every source connector: ``consult_url`` names the advancedsearch URL
+ params, ``parse`` maps the raw JSON to a typed result. Neither touches the
network or the filesystem, and this module does NOT import ``src.onboard.fetch``
— the single fetch happens in ``base.run_connector``. Live full-text download is
deferred; the fixture/search response supplies an ``excerpt``.
"""

from __future__ import annotations

from src.onboard.models import CityContext, ConnectorResult, SourceDocument

# The ONLY copyright status that proves an item is public-domain. Compared as an
# EXACT token (case-insensitive), never as a substring — a substring match would
# also accept "NOT_IN_COPYRIGHT_..." style values and, worse, could be smuggled
# via free text. IA returns this field as EITHER a string or a list.
_PD_STATUS = "NOT_IN_COPYRIGHT"


def _status_is_public_domain(status: object) -> bool:
    """True IFF ``possible-copyright-status`` (a string OR a list — real IA
    returns either) is UNAMBIGUOUSLY public-domain: at least one token is
    present and EVERY present token, stripped and upper-cased, exactly equals
    ``NOT_IN_COPYRIGHT``. Compared as an exact token, never a substring.

    FAIL-CLOSED on contradiction/ambiguity: a list carrying any other value
    (e.g. ``["IN_COPYRIGHT", "NOT_IN_COPYRIGHT"]``) or any non-string element is
    refused — a claim contradicted by another claim is not proof. A missing
    status (``None``), an empty list, or an empty/other string all refuse."""
    candidates = status if isinstance(status, list) else [status]
    if not candidates:
        return False
    normalized: list[str] = []
    for s in candidates:
        if not isinstance(s, str):
            return False  # a non-string token is ambiguous → refuse
        normalized.append(s.strip().upper())
    return all(token == _PD_STATUS for token in normalized)


class InternetArchiveConnector:
    """Consults archive.org advancedsearch and returns ONLY provably-PD docs."""

    slug = "internet_archive"

    def consult_url(self, ctx: CityContext) -> tuple[str, dict | None]:
        """The advancedsearch endpoint + subject query scoped to the city slug.
        PURE (no network/I/O)."""
        return (
            "https://archive.org/advancedsearch.php",
            {
                "q": f"subject:({ctx.slug})",
                "fl[]": [
                    "identifier",
                    "title",
                    "possible-copyright-status",
                    "licenseurl",
                    "rights",
                ],
                "output": "json",
                "rows": 50,
            },
        )

    def _is_public_domain(self, doc: dict) -> bool:
        """The F6 legal wall, per item. A SINGLE authoritative signal, fail-
        closed: True IFF ``possible-copyright-status`` normalizes to exactly
        ``NOT_IN_COPYRIGHT`` (string or list; see ``_status_is_public_domain``).

        There is DELIBERATELY no ``licenseurl`` path and no free-text ``rights``
        path — both were repeated exploit surfaces (substring, host-spoof, and an
        OR-override that let a PD licenseurl beat an explicit IN_COPYRIGHT
        status). An item lacking the computed PD status — in-copyright,
        all-rights-reserved, unknown, or missing all copyright metadata — is
        refused, even if it carries a genuine-host PD licenseurl."""
        return _status_is_public_domain(doc.get("possible-copyright-status"))

    def parse(self, payload: dict, ctx: CityContext) -> ConnectorResult:
        """Map an advancedsearch JSON payload to license-clean documents. Each
        PD-passing doc becomes one ``SourceDocument``; docs that fail the F6 gate
        emit NOTHING. Defensive against real IA shapes: a body with no
        ``response``/``docs`` yields no documents, and a doc missing an essential
        field (``title`` or ``identifier``) is skipped rather than crashing or
        emitting a broken document. PURE — no network/I/O."""
        docs = payload.get("response", {}).get("docs", [])
        documents: list[SourceDocument] = []
        for doc in docs:
            if not self._is_public_domain(doc):
                continue
            title = doc.get("title")
            identifier = doc.get("identifier")
            if not title or not identifier:
                # Essential provenance missing — cannot emit a usable, citable
                # SourceDocument; skip rather than crash on bracket access.
                continue
            documents.append(
                SourceDocument(
                    source="internet_archive",
                    license="public_domain",
                    title=title,
                    url=f"https://archive.org/details/{identifier}",
                    text=doc.get("excerpt", ""),
                    meta={
                        "ia_identifier": identifier,
                        "copyright_status": doc.get("possible-copyright-status"),
                    },
                )
            )
        return ConnectorResult(documents=documents)
