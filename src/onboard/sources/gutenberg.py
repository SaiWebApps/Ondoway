"""Project Gutenberg (public-domain books) — source connector.

Gutenberg is the license-clean full-text well: every book Project Gutenberg
distributes is public domain, and gutendex (the JSON API in front of it) tags
each result with a ``copyright`` flag. This connector GATES on ``copyright is
False`` — a book whose ``copyright`` is ``True`` (still in copyright), missing,
or ``None`` is EXCLUDED — so nothing that isn't demonstrably public domain can
enter the pipeline as an ingestable ``SourceDocument``.

PURE, like every source connector: ``consult_url`` names the gutendex query and
``parse`` maps the JSON payload to a typed ``ConnectorResult`` — neither touches
the network or filesystem, and this module never imports ``src.onboard.fetch``.

The real full-text DOWNLOAD is deferred; here the gutendex payload
carries a short ``excerpt`` per book which stands in as the document ``text`` so
the emitted ``SourceDocument`` is complete.
"""

from __future__ import annotations

from src.onboard.models import CityContext, ConnectorResult, SourceDocument


def _plain_text_url(formats: dict) -> str | None:
    """The first ``text/plain...`` download URL in a gutendex ``formats`` map
    (e.g. the ``text/plain; charset=utf-8`` gutenberg.org link), or ``None`` if
    the book exposes no plain-text format."""
    for mime, url in formats.items():
        if "text/plain" in mime:
            return url
    return None


class GutenbergConnector:
    """Consults gutendex for public-domain books matching the city name and
    emits one ``SourceDocument`` per ``copyright is False`` book."""

    slug = "gutenberg"

    def consult_url(self, ctx: CityContext) -> tuple[str, dict | None]:
        """The gutendex search URL + params for this city. PURE."""
        return ("https://gutendex.com/books", {"search": ctx.display_name})

    def parse(self, payload: dict, ctx: CityContext) -> ConnectorResult:
        """Map a gutendex payload to public-domain ``SourceDocument``s. PURE.

        A book is emitted ONLY when ``book.get("copyright") is False`` — the
        public-domain gate. ``True`` / missing / ``None`` copyright is excluded,
        as is any book that exposes no plain-text download format.
        """
        documents: list[SourceDocument] = []
        for book in payload.get("results", []):
            # The public-domain gate: drop anything not explicitly public domain.
            if book.get("copyright") is not False:
                continue
            url = _plain_text_url(book.get("formats", {}))
            if url is None:
                continue
            documents.append(
                SourceDocument(
                    source="gutenberg",
                    license="public_domain",
                    title=book["title"],
                    url=url,
                    text=book.get("excerpt", ""),
                    meta={"gutenberg_id": book.get("id")},
                )
            )
        return ConnectorResult(documents=documents)
