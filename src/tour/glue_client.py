"""Haiku glue client — the model that writes the stitched script's glue lines.

Generation calls this only for short, tightly-bounded structural
connector sentences. The per-stop writer and the checkers use other models
(phase-1-design §3.8). The prompt template (``templates/glue_prompt.txt``)
and the whitelist categories in ``generation.py`` enforce no-invention.

Two implementations:

- ``HaikuGlueClient`` — real Anthropic Haiku call. ≤150 output tokens
  per request, capped via ``max_tokens``. Tracks ``input_tokens`` and
  ``output_tokens`` cumulatively so the smoke harness can report cost.
- ``MockGlueClient`` — deterministic test double. Returns the
  pre-canned response for the (category, request) pair, or a
  category-specific default. No network.

The protocol surface is just ``stitch(category, context, request)``,
returning either a single sentence or the sentinel ``NO_GLUE`` (which
the caller treats as "fall back to a templated default").
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

NO_GLUE_SENTINEL = "<<NO_GLUE>>"

GLUE_MAX_OUTPUT_TOKENS = 150  # phase-1-design §3.8

DEFAULT_HAIKU_MODEL = "claude-haiku-4-5-20251001"


def load_glue_prompt() -> str:
    """Load the prompt template from package resources."""
    here = Path(__file__).resolve().parent
    return (here / "templates" / "glue_prompt.txt").read_text(encoding="utf-8")


class GlueClient(Protocol):
    """Protocol for glue stitching — implemented by Haiku and mock clients."""

    def stitch(self, category: str, context: str, request: str) -> str: ...


# ---------------------------------------------------------------------------
# Mock — deterministic, no network
# ---------------------------------------------------------------------------


_DEFAULT_MOCK_RESPONSES: dict[str, str] = {
    # NAV defers to generation's deterministic connective-tissue template (which
    # names the destination + distance), rather than a flat canned line repeated
    # every leg. A test can still inject an explicit GLUE_NAV response.
    "GLUE_NAV": NO_GLUE_SENTINEL,
    "GLUE_STAGING": "Stand here.",
    "GLUE_PACING": "Take a moment.",
    "GLUE_CALLBACK": NO_GLUE_SENTINEL,
    "ARITH": NO_GLUE_SENTINEL,
    "GLUE_CLOSING": "End the walk here.",
}


@dataclass
class MockGlueClient:
    """Deterministic test double. Returns the response keyed by category.

    To override per-call behavior in a test, populate ``responses`` keyed
    by either the category name or a tuple of (category, request_substring).
    """

    responses: dict[str, str] = field(default_factory=dict)
    calls: list[tuple[str, str, str]] = field(default_factory=list)

    def stitch(self, category: str, context: str, request: str) -> str:
        self.calls.append((category, context, request))
        if (key := f"{category}::{request}") in self.responses:
            return self.responses[key]
        if category in self.responses:
            return self.responses[category]
        return _DEFAULT_MOCK_RESPONSES.get(category, NO_GLUE_SENTINEL)


# ---------------------------------------------------------------------------
# Haiku — real Anthropic call
# ---------------------------------------------------------------------------


@dataclass
class HaikuGlueClient:
    """Real Anthropic Haiku client. Tightly prompted; ≤150 tokens output."""

    model: str = DEFAULT_HAIKU_MODEL
    max_output_tokens: int = GLUE_MAX_OUTPUT_TOKENS
    input_tokens: int = 0
    output_tokens: int = 0
    _client: object = field(default=None, repr=False)
    _prompt: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        # Defer importing anthropic so unit tests don't require the SDK.
        from src.tour.anthropic_client import judge_client

        self._client = judge_client()
        self._prompt = load_glue_prompt()

    def stitch(self, category: str, context: str, request: str) -> str:
        rendered = (
            self._prompt.replace("{{CATEGORY}}", category)
            .replace("{{CONTEXT}}", context.strip() or "(no surrounding context)")
            .replace("{{REQUEST}}", request.strip() or "(no specific request)")
        )
        # FAIL SOFT. Glue is one decorative sentence between stops; the tour is
        # the stops. This call had no exception handling at all until 2026-07-31,
        # so ANY provider failure propagated out of generate() and destroyed the
        # whole tour — measured with a blank ANTHROPIC_API_KEY, where the SDK
        # raises TypeError("Could not resolve authentication method") and
        # POST /trips/preview answered HTTP 500 in 3.3s. The route's Basic-lane
        # fallback never ran, because this happens during PLANNING, before the
        # try block that degrades.
        #
        # NO_GLUE_SENTINEL is the engine's existing "no transition available"
        # value: callers already replace it with a deterministic template
        # sentence (_coerce_glue_output(out, default=template_nav)). So a failure
        # here costs one nicety and keeps every stop. Logged with the traceback,
        # never silent — an unnoticed degrade is how canned transitions shipped
        # as the default for months.
        try:
            response = self._client.messages.create(  # type: ignore[attr-defined]
                model=self.model,
                max_tokens=self.max_output_tokens,
                messages=[{"role": "user", "content": rendered}],
            )
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "glue call failed for category=%r; falling back to the template "
                "transition. The tour keeps all its stops.",
                category,
                exc_info=True,
            )
            # A log line is invisible (owner ruling 2026-07-31). Record it so the
            # API can hand it to the workbench and the editor SEES that these
            # transitions were not written by the narrator.
            from src.tour.degradations import record as _record_degradation

            _record_degradation(
                kind="glue_call_failed",
                human=(
                    "The short walking directions between stops were written from "
                    "a fixed template, not by the narrator, because the narration "
                    "model could not be reached."
                ),
                component="HaikuGlueClient.stitch",
                error=exc,
                glue_category=category,
            )
            return NO_GLUE_SENTINEL
        usage = getattr(response, "usage", None)
        if usage is not None:
            self.input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
            self.output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
        text_chunks: list[str] = []
        for block in getattr(response, "content", []) or []:
            text = getattr(block, "text", None)
            if text:
                text_chunks.append(text)
        out = "".join(text_chunks).strip()
        if not out:
            return NO_GLUE_SENTINEL
        # Strip any leading/trailing quotes Haiku occasionally adds.
        return out.strip().strip('"').strip("'").strip()


__all__ = [
    "DEFAULT_HAIKU_MODEL",
    "GLUE_MAX_OUTPUT_TOKENS",
    "NO_GLUE_SENTINEL",
    "GlueClient",
    "HaikuGlueClient",
    "MockGlueClient",
    "load_glue_prompt",
]
