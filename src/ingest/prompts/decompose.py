"""Prompt copy and structured-output schema for the P1 decompose phase.

Docs/ingestion/rebuild-spec.md and CONTEXT.md pin the shapes below.

Step 4 landed `DECOMPOSE_PROMPT` (the first-ask prompt, carrying
every pinned extraction rule from the owner's Rule-A copy — self-
containment, no lifted runs, no book apparatus, the four `kind` values),
`REDO_PROMPT` (the re-ask prompt, quoting the prior attempt's refusal
reasons back and asking for a fresh answer "from the start" rather than a
patch), `P1_RESPONSE_SCHEMA` (Claude's structured-output subset of JSON
Schema — only `type`/`properties`/`required`/`additionalProperties`/
`items`/`enum`, every object node's `required` list equal to its own
property names), and the two render functions.

Both prompts carry a `{source}` (and, for the re-ask, a `{problems}`) slot
substituted with plain `str.replace`, never `str.format`: each prompt
embeds a literal JSON example — `{"claims": [...]}"` — whose own braces
`str.format` would misparse as fields. `str.replace` only ever touches the
exact `{source}` / `{problems}` substrings, so the JSON example's braces
survive untouched.
"""

from __future__ import annotations

#: Pinned rule sentences (Rule-A copy) — see AC-19. Kept as their own
#: constants so each survives string assembly as one contiguous phrase.
_STANDS_ALONE = "stands entirely on its own"
_NO_DANGLING_REFERENCE = 'never write "he", "it", "there", "the same year", "later" or "also"'
_NAMES_BOTH_SIDES = "belongs INSIDE a claim that names both sides"
_OWN_WORDING = "Use your own wording"
_EIGHT_WORD_RUN = "eight words of the passage in a row"
_NO_BOOK_CLAIMS = "Never write a claim about the book, its route, its pages or its author"
_KIND_SENTENCE = (
    'Set kind to "event", "state", "belief" or "ambiguous" for every claim '
    "— never anything else."
)
_SPAN_SENTENCE = (
    "The span must be copied exactly from the passage below, word for "
    "word, so it can be checked against the source."
)

#: Slice 10 step 2 — one fact per claim. P1 had emitted compound claims
#: (a relative clause stacking three facts, two facts joined by "and", a list
#: beside its own items) against CONTEXT.md's "one atomic factual
#: statement", and the merge could neither fold nor keep them. The carve-out
#: is CONTEXT.md's own: a relation the source STATES is one claim naming
#: both sides — never two events that merely follow each other. The worked
#: example is from Rome, a place no calibration record or replay uses.
_ONE_FACT = (
    "Every claim states exactly one fact about one subject. Split \"X is A and "
    "B\", \"X, which did A, is B\" and \"X did A; Y did B\" into separate claims, "
    "each naming its subject. Split a list into one claim per item — and "
    "never also a claim that lists them together. A relation the passage itself "
    "states between two facts (one caused the other, one contrasts with the "
    f"other) is one fact: it {_NAMES_BOTH_SIDES} — never two events that merely "
    "follow each other, which are two claims.\n"
    "Worked example. Passage: \"The Pantheon, rebuilt by Hadrian around AD 125, "
    "has a dome with an open oculus, and Raphael and two Italian kings are "
    "buried inside; because it became a church in 609, it escaped the "
    "stripping of Rome's other temples.\" Claims: \"Hadrian rebuilt the Pantheon "
    "around AD 125.\" / \"The Pantheon's dome has an open oculus.\" / \"Raphael "
    "is buried in the Pantheon.\" / \"Two Italian kings are buried in the "
    "Pantheon.\" / \"Because the Pantheon became a church in 609, it escaped "
    "the stripping of Rome's other temples.\" (the last is a relation the "
    "passage states, so it stays one claim)."
)

#: The JSON shape both prompts ask for. A plain literal (never an f-string
#: or `.format` target) so its braces are inert until `str.replace` swaps
#: in the `{source}` / `{problems}` slots elsewhere in the prompt.
_ANSWER_SHAPE = '{"claims": [{"text": "...", "kind": "...", "span": "..."}]}'

DECOMPOSE_PROMPT = (
    "You are extracting standalone factual claims from a guidebook passage "
    "about a place.\n\n"
    f"Every claim {_STANDS_ALONE}: a reader who has never seen the passage "
    "must be able to understand it with no other claim beside it, so "
    f"{_NO_DANGLING_REFERENCE}. If a claim needs a pronoun, name the actual "
    "person or place instead.\n\n"
    f"{_ONE_FACT}\n\n"
    f"{_OWN_WORDING}: the claim's own sentence must never repeat "
    f"{_EIGHT_WORD_RUN} outside a quotation you attribute to a named "
    f"speaker. {_SPAN_SENTENCE}\n\n"
    f"{_NO_BOOK_CLAIMS} — no page numbers, no walk directions, no "
    '"the guide recommends".\n\n'
    f"{_KIND_SENTENCE}\n\n"
    "Answer as JSON only, matching this shape exactly:\n"
    f"{_ANSWER_SHAPE}\n\n"
    "Passage:\n{source}"
)

REDO_PROMPT = (
    "Your previous answer had problems:\n{problems}\n\n"
    "Fix every one of them and answer again from the start — do not just "
    "patch the flagged claims.\n\n"
    f"{_ONE_FACT}\n\n"
    "Answer as JSON only, matching this shape exactly:\n"
    f"{_ANSWER_SHAPE}\n\n"
    "Passage:\n{source}"
)

#: Claude's structured-output subset of JSON Schema, restricted to
#: type/properties/required/additionalProperties/items/enum. Every object
#: node's `required` list equals its own `properties` keys.
P1_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": ["event", "state", "belief", "ambiguous"],
                    },
                    "span": {"type": "string"},
                },
                "required": ["text", "kind", "span"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["claims"],
    "additionalProperties": False,
}


def render_decompose(unit_text: str) -> str:
    """Render DECOMPOSE_PROMPT with the unit's own text substituted in.

    Uses `str.replace`, not `str.format` — the prompt's own JSON example
    carries literal braces `.format` would misparse as fields.
    """
    return DECOMPOSE_PROMPT.replace("{source}", unit_text)


def render_redo(unit_text: str, problems: list[str]) -> str:
    """Render REDO_PROMPT, quoting every refusal reason back verbatim.

    Raises ValueError when `problems` is empty: a re-ask with nothing to
    fix is a caller bug, not a case worth prompting the model around.
    """
    if not problems:
        raise ValueError("render_redo requires at least one problem to quote back")
    problem_lines = "\n".join(f"- {problem}" for problem in problems)
    rendered = REDO_PROMPT.replace("{problems}", problem_lines)
    return rendered.replace("{source}", unit_text)
