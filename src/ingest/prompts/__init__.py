"""Prompt templates and structured-output schemas for the ingest LLM phases.

This package holds the prompt copy the LLM-facing phases (`decompose`,
`group`) send to the model, plus each phase's structured-output schema.
The facade re-exports every phase submodule's public names so callers write
`import src.ingest.prompts as prompts` and reach `prompts.DECOMPOSE_PROMPT`,
`prompts.render_decompose`, etc. without knowing which submodule owns them.

Step 4 landed `prompts.decompose`'s names. Step 11 added
`prompts.group`'s. Slice 4 added `prompts.judge`'s (P3). Slice 5 added
`prompts.narrate`'s (P4, P5). Slice 6 added `prompts.merge`'s (P6).
"""

from __future__ import annotations

from src.ingest.prompts.decompose import (
    DECOMPOSE_PROMPT,
    P1_RESPONSE_SCHEMA,
    REDO_PROMPT,
    render_decompose,
    render_redo,
    render_supplement,
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
from src.ingest.prompts.judge import (
    JUDGE_CLAIM_PROMPT,
    OMISSIONS_PROMPT,
    P3_OMISSIONS_SCHEMA,
    P3_RESTATE_SCHEMA,
    P3_VERDICT_SCHEMA,
    RESTATE_PROMPT,
    render_judge_claim,
    render_omissions,
    render_restate,
)
from src.ingest.prompts.merge import (
    CLAIM_VERDICTS,
    MERGE_PROMPT,
    MERGE_REDO_PROMPT,
    P6_MERGE_SCHEMA,
    STORY_VERDICTS,
    render_merge,
    render_merge_redo,
)
from src.ingest.prompts.narrate import (
    JUDGE_SENTENCE_PROMPT,
    NARRATE_PROMPT,
    NARRATE_REDO_PROMPT,
    NARRATE_REVISE_PROMPT,
    P4_NARRATION_SCHEMA,
    P5_VERDICT_SCHEMA,
    render_judge_sentence,
    render_narrate,
    render_narrate_redo,
    render_narrate_revise,
)

__all__ = [
    "CLAIM_VERDICTS",
    "DECOMPOSE_PROMPT",
    "GROUP_AMBIGUITY_CLASSES",
    "GROUP_PROMPT_TEMPLATE",
    "GROUP_REDO_TEMPLATE",
    "JUDGE_CLAIM_PROMPT",
    "JUDGE_SENTENCE_PROMPT",
    "MERGE_PROMPT",
    "MERGE_REDO_PROMPT",
    "NARRATE_PROMPT",
    "NARRATE_REDO_PROMPT",
    "NARRATE_REVISE_PROMPT",
    "OMISSIONS_PROMPT",
    "P1_RESPONSE_SCHEMA",
    "P2_RESPONSE_SCHEMA",
    "P3_OMISSIONS_SCHEMA",
    "P3_RESTATE_SCHEMA",
    "P3_VERDICT_SCHEMA",
    "P4_NARRATION_SCHEMA",
    "P5_VERDICT_SCHEMA",
    "P6_MERGE_SCHEMA",
    "REDO_PROMPT",
    "RESTATE_PROMPT",
    "STORY_VERDICTS",
    "TIE_BREAK",
    "TieBreakMissing",
    "render_decompose",
    "render_group",
    "render_group_redo",
    "render_judge_claim",
    "render_judge_sentence",
    "render_merge",
    "render_merge_redo",
    "render_narrate",
    "render_narrate_redo",
    "render_narrate_revise",
    "render_omissions",
    "render_redo",
    "render_restate",
    "render_supplement",
]
