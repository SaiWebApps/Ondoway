"""The ONLY ingest HTTP door (grey-zone wall 2).

Every source connector calls ``assert_ingestable(url)`` BEFORE any
network I/O. Only registrable domains that are license-clean / public-domain
are allowed; a shadow library (Anna's Archive / WeLib) or any non-allowlisted
host raises ``IngestDomainError``. Shadow libraries are DISCOVERY-ONLY: their
metadata is surfaced as ``DiscoveryPointer`` for the human to acquire legally,
and their text is NEVER fetched or ingested here.

The client itself is deliberately tiny — the value of this module is the guard,
not a feature-rich HTTP layer. It defaults to ``fixture`` mode so the test bar
NEVER touches the network; ``live`` mode (real ``httpx``) is opt-in via
``ONDOWAY_ONBOARD_HTTP=live``.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

# Registrable domains whose content is license-clean / public-domain and may be
# fetched for full-text ingest. Subdomains are allowed (see is_ingest_host_allowed):
# e.g. en.wikipedia.org, query.wikidata.org, www.gutenberg.org.
INGEST_DOMAIN_ALLOWLIST: frozenset[str] = frozenset(
    {
        "wikipedia.org",
        "wikivoyage.org",
        "wikidata.org",
        "wikimedia.org",
        "overpass-api.de",
        "gutendex.com",
        "gutenberg.org",
        "archive.org",
    }
)


class IngestDomainError(Exception):
    """Raised when a URL's host is not on ``INGEST_DOMAIN_ALLOWLIST``."""


def _host_of(url: str) -> str:
    """Return the lowercased hostname of ``url`` (no port), or "" if absent."""
    # urlparse().hostname already strips the port and lowercases the host, and
    # returns None for a URL with no authority (e.g. a bare path).
    return (urlparse(url).hostname or "").lower()


def is_ingest_host_allowed(url: str) -> bool:
    """True iff ``url``'s host equals, or is a subdomain of, an allowlisted
    registrable domain.

    Uses an exact-or-dotted-suffix match (``host == d`` or
    ``host.endswith("." + d)``) so a lookalike like ``wikipedia.org.evil.com``
    is REJECTED — a plain ``endswith(d)`` would wrongly accept it.
    """
    host = _host_of(url)
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in INGEST_DOMAIN_ALLOWLIST)


def assert_ingestable(url: str) -> None:
    """Raise ``IngestDomainError`` (naming the host) if ``url`` is not ingestable.

    Called BEFORE any network I/O by every source connector. Shadow libraries
    (Anna's Archive / WeLib) are discovery-only and never ingested; their text
    must never reach this door.
    """
    if not is_ingest_host_allowed(url):
        host = _host_of(url) or "<no host>"
        raise IngestDomainError(
            f"host {host!r} is not on the ingest allowlist; refusing to fetch. "
            "Shadow libraries (Anna's Archive / WeLib) are discovery-only and are "
            "never ingested — surface a DiscoveryPointer for legal acquisition instead. "
            f"Allowed registrable domains: {sorted(INGEST_DOMAIN_ALLOWLIST)}"
        )


# "fixture" (default) => the bar never hits the network; "live" => real httpx.
HTTP_MODE = os.getenv("ONDOWAY_ONBOARD_HTTP", "fixture")

_FIXTURE_MODE_MESSAGE = (
    "onboard HTTP is in fixture mode; set ONDOWAY_ONBOARD_HTTP=live"
)

# Wikimedia's User-Agent policy REQUIRES a descriptive agent naming the tool and
# a contact — an anonymous UA is 403'd by en.wikipedia.org (and the other MediaWiki
# APIs). Sent by default on every live GET (merged in only if the caller did not
# set its own User-Agent, so an explicit connector header still wins).
ONBOARD_USER_AGENT = "Ondoway-Onboard/1.0 (https://ondoway.com; new-city onboarding pipeline)"


def http_get_json(
    url: str,
    params: dict | None = None,
    *,
    timeout: float = 30,
    headers: dict | None = None,
) -> dict:
    """Guarded JSON GET. Enforces the allowlist FIRST, then honours ``HTTP_MODE``.

    In ``fixture`` mode raises ``RuntimeError`` (connectors read fixtures
    directly); in ``live`` mode performs a real ``httpx`` GET.
    """
    assert_ingestable(url)
    if HTTP_MODE != "live":
        raise RuntimeError(_FIXTURE_MODE_MESSAGE)
    import httpx

    # Default the descriptive UA (Wikimedia policy) but let an explicit caller
    # header win — the caller's headers are spread LAST.
    merged = {"User-Agent": ONBOARD_USER_AGENT, **(headers or {})}
    resp = httpx.get(url, params=params, timeout=timeout, headers=merged)
    resp.raise_for_status()
    return resp.json()


def http_get_text(
    url: str,
    params: dict | None = None,
    *,
    timeout: float = 30,
    headers: dict | None = None,
) -> str:
    """Guarded text GET. Enforces the allowlist FIRST, then honours ``HTTP_MODE``.

    In ``fixture`` mode raises ``RuntimeError``; in ``live`` mode performs a real
    ``httpx`` GET and returns the response body text.
    """
    assert_ingestable(url)
    if HTTP_MODE != "live":
        raise RuntimeError(_FIXTURE_MODE_MESSAGE)
    import httpx

    # Default the descriptive UA (Wikimedia policy) but let an explicit caller
    # header win — the caller's headers are spread LAST.
    merged = {"User-Agent": ONBOARD_USER_AGENT, **(headers or {})}
    resp = httpx.get(url, params=params, timeout=timeout, headers=merged)
    resp.raise_for_status()
    return resp.text
