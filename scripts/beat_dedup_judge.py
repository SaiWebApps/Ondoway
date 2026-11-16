#!/usr/bin/env python
"""Haiku-backed 4-way classifier for candidate duplicate beat pairs.

Uses the Anthropic Messages API with a tool-call schema enforcing the 4-value
`classification` enum server-side. On parse fail: one retry with a stricter
prompt; final fallback returns `{"classification": "different_story",
"reasoning": "parse failed: ...", "_parse_failed": True}` and the caller
surfaces such pairs at the top of the human-review report.

API key is read only from `ANTHROPIC_API_KEY`. It is never logged, never
echoed into reasoning text, and never written to disk by this module.
"""

from __future__ import annotations

import os
from typing import Any

from src.model_registry import model_for

try:
    from anthropic import Anthropic
except ImportError:  # pragma: no cover
    Anthropic = None  # type: ignore[assignment]

MODEL = model_for("beat_dedup_judge")
CLASSIFICATIONS = (
    "same_story_same_wording",
    "same_story_added_detail",
    "same_story_enhanced_content",
    "different_story",
)

_CLASSIFY_TOOL = {
    "name": "record_classification",
    "description": (
        "Record the classification of a candidate duplicate beat pair. "
        "Use exactly one of the four allowed classification values."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "classification": {
                "type": "string",
                "enum": list(CLASSIFICATIONS),
                "description": (
                    "same_story_same_wording: near-identical prose. "
                    "same_story_added_detail: one adds verifiable facts the other lacks. "
                    "same_story_enhanced_content: one is substantively rewritten/better. "
                    "different_story: distinct facts or angle."
                ),
            },
            "reasoning": {
                "type": "string",
                "description": "One-to-two sentence justification grounded in both beats.",
            },
        },
        "required": ["classification", "reasoning"],
    },
}


_BASE_PROMPT = """You compare two narrative beats about the same POI and decide whether they tell the same story.

Beat A ({a_lens}):
\"\"\"{a_body}\"\"\"

Beat B ({b_lens}):
\"\"\"{b_body}\"\"\"

Call the `record_classification` tool exactly once with:
- `classification`: one of {classifications}
- `reasoning`: one or two sentences citing specific facts from both beats.

Definitions:
- same_story_same_wording: near-identical prose (paraphrase-level).
- same_story_added_detail: both tell the same story, but one adds a verifiable detail (date, name, number) the other lacks.
- same_story_enhanced_content: same core story, but one is substantively rewritten and clearly better.
- different_story: distinct events, entities, or angles — do not merge.

Do not answer in prose. Only call the tool."""


_STRICT_PROMPT_SUFFIX = "\n\nSTRICT: Your previous response was not a valid tool call. You MUST call `record_classification` with exactly one of these four values for classification: same_story_same_wording, same_story_added_detail, same_story_enhanced_content, different_story. No other values are acceptable."


def _build_prompt(beat_a: dict, beat_b: dict, strict: bool = False) -> str:
    prompt = _BASE_PROMPT.format(
        a_lens=beat_a.get("lens", ""),
        a_body=beat_a.get("script_body", ""),
        b_lens=beat_b.get("lens", ""),
        b_body=beat_b.get("script_body", ""),
        classifications=", ".join(CLASSIFICATIONS),
    )
    if strict:
        prompt += _STRICT_PROMPT_SUFFIX
    return prompt


def _extract_tool_use(response: Any) -> dict | None:
    """Pull the first `record_classification` tool_use block, or None."""
    for block in getattr(response, "content", []) or []:
        block_type = getattr(block, "type", None)
        block_name = getattr(block, "name", None)
        if block_type == "tool_use" and block_name == "record_classification":
            return dict(getattr(block, "input", {}) or {})
    return None


def _validate_payload(payload: dict | None) -> dict | None:
    if not payload:
        return None
    classification = payload.get("classification")
    reasoning = payload.get("reasoning")
    if classification not in CLASSIFICATIONS:
        return None
    if not isinstance(reasoning, str) or not reasoning.strip():
        return None
    return {"classification": classification, "reasoning": reasoning.strip()}


def classify_pair(
    beat_a: dict,
    beat_b: dict,
    *,
    client: Any = None,
    model: str = MODEL,
) -> dict:
    """Return `{classification, reasoning, _parse_failed}` for the pair.

    `client` lets tests inject a stubbed Anthropic client. Production callers
    pass None and a real client is constructed from `ANTHROPIC_API_KEY`.
    """
    if client is None:
        if Anthropic is None:
            raise RuntimeError("anthropic SDK not installed")
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        client = Anthropic(api_key=api_key)

    for strict in (False, True):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=512,
                tools=[_CLASSIFY_TOOL],
                tool_choice={"type": "tool", "name": "record_classification"},
                messages=[
                    {
                        "role": "user",
                        "content": _build_prompt(beat_a, beat_b, strict=strict),
                    }
                ],
            )
        except Exception as exc:
            # Scrub any accidental API-key leakage from the error string.
            safe = _scrub_key(str(exc))
            return {
                "classification": "different_story",
                "reasoning": f"parse failed: {safe}",
                "_parse_failed": True,
            }

        payload = _validate_payload(_extract_tool_use(response))
        if payload:
            payload["_parse_failed"] = False
            return payload

    return {
        "classification": "different_story",
        "reasoning": "parse failed: Haiku did not return a valid classification after one retry",
        "_parse_failed": True,
    }


def _scrub_key(s: str) -> str:
    """Strip anything that looks like an API key from error text."""
    import re

    return re.sub(r"sk-ant-[A-Za-z0-9_\-]+", "sk-ant-REDACTED", s)
