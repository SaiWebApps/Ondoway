"""Prompt copy and structured-output schemas for P4 (narrate) and P5
(judge narration).

Docs/ingestion/rebuild-spec.md §3 (P4, P5) and CONTEXT.md ("Narration",
"Provenance leak", "Lift") pin the shapes below. The author sees the
story's resolved claim TEXTS only (each with any judgments its sources
make, as "(Also called: ...)", ADR-0004) — never a span, never the unit — so a
narration cannot copy a source it has not read; the narration judge sees
one sentence and the claim texts, and only decides.

Slots are substituted with plain `str.replace`, never `str.format`, for
the same reason prompts/decompose.py gives: each prompt embeds a literal
JSON example whose braces `.format` would misparse as fields.
"""

from __future__ import annotations

_VOICE_RULES = (
    "Rules:\n"
    "- Every sentence states something one of the claims says. Add nothing "
    "the claims do not say, however well known; drop nothing a claim says.\n"
    "- Say it as someone standing at the place would: present tense for what "
    "is there now, past tense for what happened. Never mention a book, a "
    "guide, an author, a page, a route or a walk.\n"
    "- No framing, openers or transitions: never \"imagine\", \"picture\", "
    "\"envision\", \"welcome\", \"as you walk\", \"next\". A tour engine "
    "supplies those; a beat only tells.\n"
    "- Your own wording throughout, except a judgment, which keeps its source's word; "
    "quote only words a named person said or wrote, and say who.\n"
    "- Keep each claim's time: what a claim says was so, say was so.\n"
    "- Plain spoken English, one story, no headings, no lists.\n"
    '- A claim may end with "(Also called: ...)": those are judgments its sources make '
    "about it. Use each as the source's own word, in the sentence that tells that claim "
    '("a seminal work", never a paraphrase that says more), never saying who made them; '
    "when they disagree, say opinion is divided and never pick one."
    ' Never write the words "also called": a judgment is not a name.\n'
    "- When a claim's own wording and its \"(Also called: ...)\" words judge it "
    "differently, that is a disagreement too."
)

_NARRATION_SHAPE = '{"narration": "..."}'

NARRATE_PROMPT = (
    "Write the narration a listener hears at {place} for the story "
    "\"{title}\", using only the claims below.\n\n"
    f"{_VOICE_RULES}\n\n"
    "Answer as JSON only, matching this shape exactly:\n"
    f"{_NARRATION_SHAPE}\n\n"
    "Claims:\n{claims}"
)

#: Claude's structured-output subset of JSON Schema (type/properties/
#: required/additionalProperties only), every object node's `required`
#: equal to its own property names.
P4_NARRATION_SCHEMA: dict = {
    "type": "object",
    "properties": {"narration": {"type": "string"}},
    "required": ["narration"],
    "additionalProperties": False,
}


def _claim_lines(claim_texts: list[str]) -> str:
    return "\n".join(f"- {text}" for text in claim_texts) or "- (none)"


def render_narrate(place: str, title: str, claim_texts: list[str]) -> str:
    """Render NARRATE_PROMPT over the story's claim texts (str.replace)."""
    rendered = NARRATE_PROMPT.replace("{place}", place)
    rendered = rendered.replace("{title}", title)
    return rendered.replace("{claims}", _claim_lines(claim_texts))


NARRATE_REDO_PROMPT = (
    "Your narration for the story \"{title}\" at {place} was refused by a "
    "code check:\n\n"
    "Refused narration:\n{narration}\n\n"
    "Problems:\n{problems}\n\n"
    "Write it again from the start, from the same claims, fixing every "
    "problem listed.\n\n"
    f"{_VOICE_RULES}\n\n"
    "Answer as JSON only, matching this shape exactly:\n"
    f"{_NARRATION_SHAPE}\n\n"
    "Claims:\n{claims}"
)


def render_narrate_redo(
    place: str, title: str, claim_texts: list[str], narration: str, problems: list[str]
) -> str:
    """Render NARRATE_REDO_PROMPT, quoting the refused text and every
    problem back verbatim.

    Raises ValueError with no problems: a re-ask with nothing to fix is a
    caller bug, not a case worth prompting the model around.
    """
    if not any(problem.strip() for problem in problems):
        raise ValueError("render_narrate_redo requires at least one problem to quote back")
    rendered = NARRATE_REDO_PROMPT.replace("{place}", place)
    rendered = rendered.replace("{title}", title)
    rendered = rendered.replace("{narration}", narration)
    rendered = rendered.replace("{problems}", "\n".join(f"- {problem}" for problem in problems))
    return rendered.replace("{claims}", _claim_lines(claim_texts))


_SENTENCE_RULE = (
    "A sentence is entailed only when everything it states — every name, "
    "date, number, place and causal link — is stated by one of the claims, "
    "or by two claims read together. A sentence that adds a detail no claim "
    "gives, changes a value, or attaches a cause no claim states is not "
    "entailed, even if the detail is true in the world. Wording may differ "
    "freely; only the facts are judged. A claim's \"(Also called: ...)\" words are "
    "judgments its sources make about it: a sentence that uses one of them about that "
    "claim is entailed on that point, and so is a sentence saying opinion on it is divided "
    "when its judgments disagree. A sentence that praises, ranks or dramatizes "
    '("shaped everything that came after", "ever since", "the finest") beyond the words '
    'a claim or its "(Also called: ...)" list gives is not entailed: here a judgment is a '
    "fact, and a stronger one is a different fact."
)

_VERDICT_SHAPE = '{"entailed": true, "reason": "..."}'

JUDGE_SENTENCE_PROMPT = (
    "You are checking one sentence of a narration against the claims it "
    "was written from.\n\n"
    f"{_SENTENCE_RULE}\n\n"
    "Answer as JSON only, matching this shape exactly:\n"
    f"{_VERDICT_SHAPE}\n"
    "The reason is one sentence naming which claim supports it, or what no "
    "claim supports.\n\n"
    "Sentence:\n{sentence}\n\n"
    "Claims:\n{claims}"
)

#: The same verdict shape as P3's — a boolean and a sentence — declared
#: on its own because P5 is a different phase under a different judge.
P5_VERDICT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "entailed": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["entailed", "reason"],
    "additionalProperties": False,
}


def render_judge_sentence(sentence: str, claim_texts: list[str]) -> str:
    """Render JUDGE_SENTENCE_PROMPT for one sentence (str.replace)."""
    rendered = JUDGE_SENTENCE_PROMPT.replace("{sentence}", sentence)
    return rendered.replace("{claims}", _claim_lines(claim_texts))


NARRATE_REVISE_PROMPT = (
    "A checker read your narration for the story \"{title}\" at {place} "
    "sentence by sentence against the claims it was written from, and "
    "refused these sentences:\n\n"
    "{refusals}\n\n"
    "Your narration:\n{narration}\n\n"
    "Write it again from the start, from the same claims, so that every "
    "sentence states only what a claim says — drop or correct whatever no "
    "claim supports.\n\n"
    f"{_VOICE_RULES}\n\n"
    "Answer as JSON only, matching this shape exactly:\n"
    f"{_NARRATION_SHAPE}\n\n"
    "Claims:\n{claims}"
)


def render_narrate_revise(
    place: str,
    title: str,
    claim_texts: list[str],
    narration: str,
    refusals: list[tuple[str, str]],
) -> str:
    """Render NARRATE_REVISE_PROMPT, quoting each refused sentence and the
    judge's reason back verbatim.

    Raises ValueError with no refusals: a re-ask with nothing to fix is a
    caller bug, not a case worth prompting the model around.
    """
    if not refusals:
        raise ValueError("render_narrate_revise requires at least one refused sentence")
    rendered = NARRATE_REVISE_PROMPT.replace("{place}", place)
    rendered = rendered.replace("{title}", title)
    rendered = rendered.replace("{narration}", narration)
    lines = "\n".join(f'- "{sentence}": {reason}' for sentence, reason in refusals)
    rendered = rendered.replace("{refusals}", lines)
    return rendered.replace("{claims}", _claim_lines(claim_texts))
