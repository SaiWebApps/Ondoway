"""Prompt copy and structured-output schema for the P2 group phase.

Docs/ingestion/rebuild-spec.md and CONTEXT.md pin the shapes below.

Step 11 landed `GROUP_PROMPT_TEMPLATE` (the first-ask prompt: one story per
place, every claim assigned exactly once, and the three ambiguity classes
a tie-break rule must resolve), `GROUP_AMBIGUITY_CLASSES` (those three
classes as their own tuple, so the template and the tuple never drift
apart), `GROUP_REDO_TEMPLATE` (the re-ask prompt, quoting the prior
attempt's story-refusal reasons back), `P2_RESPONSE_SCHEMA` (the
structured-output subset for the grouping answer), `TieBreakMissing`, and
the two render functions — with `TIE_BREAK` left at `''`, the deliberate
NOT-SUPPLIED state, so every render call refused loudly with
TieBreakMissing rather than running the model on made-up guidance.

Step 12 replaces `TIE_BREAK` with the owner's real Rule-A
text, pasted verbatim from decisions.rule_a_tie_break_value (the owner
said "go, tie-break: (A) the arc wins" in chat on 2026-09-10 after judge
pass 3). `TIE_BREAK` is the module's single owner-supplied constant: the
sentence the model applies when a claim could plausibly land in more than
one story.

`render_group` and `render_group_redo` accept `tie_break: str | None =
None`. `None` (the default, and every call group() makes) resolves to
the module attribute `TIE_BREAK` looked up AT CALL TIME — `resolved =
tie_break if tie_break is not None else TIE_BREAK`, never a def-time
default `tie_break=TIE_BREAK` in the signature, which would bind '' at
import time and stay stale under a later monkeypatch or a later step's
replacement of the module attribute (judge finding 3, pass-3 N8).

Both templates carry a `{tie_break}` and `{claims}` slot (the redo
template also `{problems}`), substituted with plain `str.replace`, never
`str.format`, for the same reason prompts/decompose.py gives: a literal
JSON example embedded elsewhere in the prompt would otherwise be
misparsed as a format field.
"""

from __future__ import annotations

from collections.abc import Sequence

from src.ingest.model import BEAT_TYPE_VALUES
from src.schema.definitions import TAGGABLE_LENSES


class TieBreakMissing(ValueError):  # noqa: N818 — pinned name is the spec/test contract
    """Raised when a grouping prompt has no non-empty tie-break rule to
    render — TIE_BREAK is still '' (not yet supplied) or the caller passed
    an empty or blank override.
    """


#: The owner's Rule-A tie-break sentence, pasted verbatim from
#: decisions.rule_a_tie_break_value (owner said "go, tie-break: (A) the
#: arc wins" in chat on 2026-09-10 after judge pass 3), clarified by owner
#: ruling B (2026-09-19): "the more specific place wins" had been read as
#: licence to write a museum's galleries as places of their own.
TIE_BREAK: str = (
    "The arc wins: the claim goes to the story whose cause-and-consequence "
    "it advances; the more specific place wins over its parent only between "
    "places that are places in their own right — a room, gallery, floor or "
    "court inside a place is that place's sub_location, never a place of its "
    "own; a person claim stays at the place the passage sets it in."
)

#: Pinned ambiguity-class descriptions (see AC-22) — kept as their own
#: constants so GROUP_PROMPT_TEMPLATE and GROUP_AMBIGUITY_CLASSES below
#: never drift apart.
_AMBIGUITY_TWO_ARCS = "two arcs share one place"
_AMBIGUITY_SUBPLACE = "a sub-place sits inside a parent place"
_AMBIGUITY_PERSON_VS_PLACE = "a person's claim could belong to the person or to the place"

#: The three ambiguity classes a {tie_break} rule must resolve.
GROUP_AMBIGUITY_CLASSES = (
    _AMBIGUITY_TWO_ARCS,
    _AMBIGUITY_SUBPLACE,
    _AMBIGUITY_PERSON_VS_PLACE,
)

GROUP_PROMPT_TEMPLATE = (
    "You are grouping factual claims about one guidebook passage into "
    "stories.\n\n"
    "Each story is one story at one place: a single arc anchored to a "
    "single location. Assign every claim to its story exactly once — "
    "never leave one out, never put it in two stories.\n\n"
    "Three kinds of ambiguity come up when a claim could plausibly belong "
    "to more than one story:\n"
    f"- {_AMBIGUITY_TWO_ARCS}\n"
    f"- {_AMBIGUITY_SUBPLACE}\n"
    f"- {_AMBIGUITY_PERSON_VS_PLACE}\n\n"
    "When one of these comes up, apply this rule:\n{tie_break}\n\n"
    "{places}"
    "Give each story a beat_type. stop_orientation is SPATIAL: where the "
    "listener stands, what they face, where the entrance, the ramp or the "
    "fountain is, which way to walk — never a listing. practicalities holds "
    "prices, opening hours, tickets, tours and transport; it is not a story "
    "and one claim is enough. transit moves the listener between places; "
    "sidebar digresses. Every other type is a told story of at least two "
    "claims.\n\n"
    "Claims:\n{claims}\n\n"
    "Answer as JSON only."
)

GROUP_REDO_TEMPLATE = (
    "Your previous grouping had problems:\n{problems}\n\n"
    "Fix every one of them and answer again from the start — do not just "
    "patch the flagged stories.\n\n"
    "Apply this rule when ambiguity comes up:\n{tie_break}\n\n"
    "{places}"
    "Claims:\n{claims}\n\n"
    "Answer as JSON only."
)

#: Claude's structured-output subset of JSON Schema, restricted to
#: type/properties/required/additionalProperties/items/enum. Every object
#: node's `required` list equals its own `properties` keys — same rule as
#: prompts/decompose.py's P1_RESPONSE_SCHEMA.
P2_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "stories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "place": {"type": "string"},
                    "beat_type": {
                        "type": "string",
                        "enum": list(BEAT_TYPE_VALUES),
                    },
                    "lenses": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": list(TAGGABLE_LENSES),
                        },
                    },
                    "claim_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "enrichment": {
                        "type": "object",
                        "properties": {
                            "physical_cues": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "entities": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "narrative_function": {"type": "string"},
                            "emotional_register": {"type": "string"},
                            "sensory_anchor": {"type": "boolean"},
                            "inline_foreign_phrases": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "pronunciation": {"type": "string"},
                            "kid_friendly": {
                                "type": "string",
                                "enum": ["yes", "no", "unknown"],
                            },
                            "sub_location": {"type": "string"},
                            "trigger_address": {"type": "string"},
                        },
                        "required": [
                            "physical_cues",
                            "entities",
                            "narrative_function",
                            "emotional_register",
                            "sensory_anchor",
                            "inline_foreign_phrases",
                            "pronunciation",
                            "kid_friendly",
                            "sub_location",
                            "trigger_address",
                        ],
                        "additionalProperties": False,
                    },
                },
                "required": [
                    "title",
                    "place",
                    "beat_type",
                    "lenses",
                    "claim_ids",
                    "enrichment",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["stories"],
    "additionalProperties": False,
}


def _places_block(places: Sequence[str]) -> str:
    """The candidate places a story's `place` must be written as — empty
    when there are none (slice 10: job A's stories named the Guggenheim
    "Guggenheim Museum" and were held as a new place)."""
    if not places:
        return ""
    lines = "\n".join(f"- {name}" for name in places)
    return (
        "Known places this passage names — when a story is at one of them, "
        f"write its name exactly as listed:\n{lines}\n\n"
    )


def render_group(
    claims: list[tuple[str, str]],
    *,
    tie_break: str | None = None,
    places: Sequence[str] = (),
) -> str:
    """Render GROUP_PROMPT_TEMPLATE with the claim lines and tie-break rule
    substituted in.

    `claims` is a sequence of `(claim_id, text)` pairs, rendered one per
    line as `"claim_id: text"`. `tie_break=None` (the default) resolves to
    the module attribute `TIE_BREAK`, looked up AT CALL TIME — never bound
    as a def-time default — so a later monkeypatch or module-attribute
    replacement of TIE_BREAK is always picked up. Raises TieBreakMissing
    when the resolved rule is empty or blank: a grouping prompt with no
    tie-break rule is a caller bug, never a case worth prompting the model
    around.
    """
    resolved = tie_break if tie_break is not None else TIE_BREAK
    if not resolved or not resolved.strip():
        raise TieBreakMissing("render_group requires a non-empty tie-break rule")
    claim_lines = "\n".join(f"{claim_id}: {text}" for claim_id, text in claims)
    rendered = GROUP_PROMPT_TEMPLATE.replace("{tie_break}", resolved)
    rendered = rendered.replace("{places}", _places_block(places))
    return rendered.replace("{claims}", claim_lines)


def render_group_redo(
    claims: list[tuple[str, str]],
    problems: list[str],
    *,
    tie_break: str | None = None,
    places: Sequence[str] = (),
) -> str:
    """Render GROUP_REDO_TEMPLATE, quoting every story-refusal reason back
    verbatim.

    Same call-time TIE_BREAK resolution and TieBreakMissing rule as
    render_group. Raises ValueError when `problems` is empty: a re-ask
    with nothing to fix is a caller bug.
    """
    if not problems:
        raise ValueError("render_group_redo requires at least one problem to quote back")
    resolved = tie_break if tie_break is not None else TIE_BREAK
    if not resolved or not resolved.strip():
        raise TieBreakMissing("render_group_redo requires a non-empty tie-break rule")
    problem_lines = "\n".join(f"- {problem}" for problem in problems)
    claim_lines = "\n".join(f"{claim_id}: {text}" for claim_id, text in claims)
    rendered = GROUP_REDO_TEMPLATE.replace("{problems}", problem_lines)
    rendered = rendered.replace("{tie_break}", resolved)
    rendered = rendered.replace("{places}", _places_block(places))
    return rendered.replace("{claims}", claim_lines)
