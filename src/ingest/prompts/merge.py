"""Prompt copy and structured-output schema for P6 (merge).

Docs/ingestion/rebuild-spec.md §3 (P6), §1 D7/D8 and CONTEXT.md ("Stated
value", "Contested claim", "Supersedes") pin the shapes below. The merge
judge sees the new story's claim TEXTS (with kind and as-of year) and every
candidate beat's claims (id, text, kind, status, as-of years, stated
values) — never a span, never the unit — and decides only two things: is
this story one an existing beat already tells, and for each new claim, does
an existing claim state the same fact, the same fact with a different value,
or nothing like it. The deterministic signature hint is never shown to it:
a judge that reads the hint is not independent of it (D7).

Slots are substituted with plain `str.replace`, never `str.format`, for
the same reason prompts/decompose.py gives: the prompt embeds a literal
JSON example whose braces `.format` would misparse as fields.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

#: The three story-level answers and the three claim-level answers the
#: judge may give (Docs/ingestion/rebuild-spec.md §3, P6 row).
STORY_VERDICTS: tuple[str, str, str] = ("same", "new", "supersedes")
CLAIM_VERDICTS: tuple[str, str, str] = ("new", "same", "conflict")

_MERGE_RULES = (
    "Decide:\n"
    '- "story": "same" if an existing beat tells this story (the same episode: '
    'the same who, what, when and why); "supersedes" if it tells the same story '
    "from a newer source whose facts replace what an older source believed or "
    'stated; "new" if no existing beat tells it. "beat_id" names that beat by its '
    "handle (b1, b2, ...), and is empty for a new story.\n"
    '- For every new claim, in order: "same" when a claim of ANY beat at this place '
    "states EVERYTHING the new claim states, with the same values - even when the story "
    "is new, because two books cut the same facts into different stories. A new claim "
    'that says more than the existing claim, or something else, is not "same": sharing '
    "a date, a name, a place or a word is not sharing a fact. \"conflict\" when the two "
    "state the same fact with a different name, date, number, place or cause. \"new\" "
    'when no claim at this place states it. "existing_claim_id" names the matched claim '
    'by its handle (b1.c02), never another claim of the new story (a new claim that '
    'repeats another new claim is judged on its own against the existing beats), and is '
    'empty for "new". "new_value" and "existing_value" quote the value each side states '
    '- a date, a number, a name - as short strings, and are empty for "new".\n'
    "- Worked example, from another city: an existing claim says \"The Eiffel Tower was "
    "completed in 1889.\" A new claim \"Work on the Eiffel Tower ended in 1889.\" is "
    "\"same\"; \"The Eiffel Tower was finished in 1887.\" is \"conflict\" (1887 vs "
    "1889); \"The Eiffel Tower was built for the 1889 World's Fair.\" is \"new\" (it "
    "shares the year, not the fact); \"Gustave Eiffel's company built the tower, "
    "completing it in 1889.\" is \"new\" (it says more than the existing claim).\n"
    "- Wording never matters; only the facts do. A newer source is never right by "
    "default: report the difference, never resolve it."
)

_MERGE_SHAPE = (
    '{"story": "same", "beat_id": "b1", "claims": [{"claim_id": "c01", '
    '"verdict": "same", "existing_claim_id": "b1.c02", "new_value": "1959", '
    '"existing_value": "1959", "reason": "..."}]}'
)

MERGE_PROMPT = (
    "A new story extracted from a second source is being merged into the corpus "
    "at {place}. Below are its claims, then every beat the corpus already holds at "
    "that place with their claims.\n\n"
    f"{_MERGE_RULES}\n\n"
    "Answer as JSON only, matching this shape exactly:\n"
    f"{_MERGE_SHAPE}\n\n"
    'New story "{title}":\n{new_claims}\n\n'
    "Existing beats at {place}:\n{existing}"
)

_CLAIM_ITEM_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "claim_id": {"type": "string"},
        "verdict": {"type": "string", "enum": list(CLAIM_VERDICTS)},
        "existing_claim_id": {"type": "string"},
        "new_value": {"type": "string"},
        "existing_value": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": [
        "claim_id",
        "verdict",
        "existing_claim_id",
        "new_value",
        "existing_value",
        "reason",
    ],
    "additionalProperties": False,
}

#: Claude's structured-output subset of JSON Schema (type/properties/
#: required/additionalProperties/items/enum only), every object node's
#: `required` equal to its own property names. Absent values are empty
#: strings, never null: the subset has no nullable type.
P6_MERGE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "story": {"type": "string", "enum": list(STORY_VERDICTS)},
        "beat_id": {"type": "string"},
        "claims": {"type": "array", "items": _CLAIM_ITEM_SCHEMA},
    },
    "required": ["story", "beat_id", "claims"],
    "additionalProperties": False,
}


def _new_claim_lines(new_claims: Sequence[Mapping[str, object]]) -> str:
    lines = [
        f"- {c['claim_id']} [{c['kind']}, {c['as_of']}]: {c['text']}" for c in new_claims
    ]
    return "\n".join(lines) or "- (none)"


def _existing_lines(existing: Sequence[Mapping[str, object]]) -> str:
    if not existing:
        return "(none - the corpus holds no beat at this place yet)"
    blocks: list[str] = []
    for beat in existing:
        handle = beat["handle"]
        lines = [f'beat {handle} "{beat["title"]}":']
        for c in beat["claims"]:  # type: ignore[union-attr]
            years = ", ".join(str(y) for y in c["as_of"])
            ref = f"{handle}.{c['claim_id']}"
            line = f"  - {ref} [{c['kind']}, {c['status']}, {years}]: {c['text']}"
            values = c.get("stated_values") or []
            if values:
                line += " (stated: " + ", ".join(str(v) for v in values) + ")"
            lines.append(line)
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


def _render_body(
    template: str,
    place: str,
    title: str,
    new_claims: Sequence[Mapping[str, object]],
    existing: Sequence[Mapping[str, object]],
) -> str:
    rendered = template.replace("{place}", place)
    rendered = rendered.replace("{title}", title)
    rendered = rendered.replace("{new_claims}", _new_claim_lines(new_claims))
    return rendered.replace("{existing}", _existing_lines(existing))


def render_merge(
    place: str,
    title: str,
    new_claims: Sequence[Mapping[str, object]],
    existing: Sequence[Mapping[str, object]],
) -> str:
    """Render MERGE_PROMPT (str.replace).

    `new_claims`: `{claim_id, text, kind, as_of}` per new claim. `existing`:
    `{handle, title, claims: [{claim_id, text, kind, status, as_of: [years],
    stated_values: [values]}]}` per candidate beat. A beat is shown by its
    handle (`b1`) and each of its claims as `b1.c02` — never by beat id,
    which the judge retyped wrong in slice 9, and never by a bare claim id,
    which two beats at one place can share. Texts only — the caller never
    passes a span.
    """
    return _render_body(MERGE_PROMPT, place, title, new_claims, existing)


MERGE_REDO_PROMPT = (
    'Your answer for the new story "{title}" at {place} could not be applied:\n\n'
    "Your answer:\n{answer}\n\n"
    "Problems:\n{problems}\n\n"
    "Answer again from the start, fixing every problem: name only the beat and "
    "claim handles listed below, and give both values whenever you report a "
    "conflict.\n\n"
    f"{_MERGE_RULES}\n\n"
    "Answer as JSON only, matching this shape exactly:\n"
    f"{_MERGE_SHAPE}\n\n"
    'New story "{title}":\n{new_claims}\n\n'
    "Existing beats at {place}:\n{existing}"
)


def render_merge_redo(
    place: str,
    title: str,
    new_claims: Sequence[Mapping[str, object]],
    existing: Sequence[Mapping[str, object]],
    answer: str,
    problems: Sequence[str],
) -> str:
    """Render MERGE_REDO_PROMPT, quoting the refused answer and every
    problem back verbatim.

    Raises ValueError with no problems: a re-ask with nothing to fix is a
    caller bug, not a case worth prompting the model around.
    """
    if not any(problem.strip() for problem in problems):
        raise ValueError("render_merge_redo requires at least one problem to quote back")
    rendered = _render_body(MERGE_REDO_PROMPT, place, title, new_claims, existing)
    rendered = rendered.replace("{answer}", answer)
    return rendered.replace("{problems}", "\n".join(f"- {problem}" for problem in problems))
