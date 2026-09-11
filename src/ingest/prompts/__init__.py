"""Prompt templates and structured-output schemas for the ingest LLM phases.

This package holds the prompt copy the LLM-facing phases (`decompose`,
`group`) send to the model, plus each phase's structured-output schema.
The facade re-exports every phase submodule's public names so callers write
`import src.ingest.prompts as prompts` and reach `prompts.DECOMPOSE_PROMPT`,
`prompts.render_decompose`, etc. without knowing which submodule owns them.

Step 4 landed `prompts.decompose`'s names. Step 11 added
`prompts.group`'s.
"""

from __future__ import annotations

from src.ingest.prompts.decompose import (
    DECOMPOSE_PROMPT,
    P1_RESPONSE_SCHEMA,
    REDO_PROMPT,
    render_decompose,
    render_redo,
)
from src.ingest.prompts.group import (
    GROUP_AMBIGUITY_CLASSES,
    GROUP_PROMPT_TEMPLATE,
    GROUP_REDO_TEMPLATE,
    P2_RESPONSE_SCHEMA,
    TIE_BREAK,
    TieBreakMissing,
    render_group,
    render_group_redo,
)

__all__ = [
    "DECOMPOSE_PROMPT",
    "GROUP_AMBIGUITY_CLASSES",
    "GROUP_PROMPT_TEMPLATE",
    "GROUP_REDO_TEMPLATE",
    "P1_RESPONSE_SCHEMA",
    "P2_RESPONSE_SCHEMA",
    "REDO_PROMPT",
    "TIE_BREAK",
    "TieBreakMissing",
    "render_decompose",
    "render_group",
    "render_group_redo",
    "render_redo",
]
