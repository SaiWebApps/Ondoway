"""Prompt copy and structured-output schemas for the P3 claim-judge phase.

Docs/ingestion/rebuild-spec.md §3 (P3 judge claims) and CONTEXT.md pin the
shapes below. The judge is a different model from the author (llm.ROLE_MODEL);
its prompts never ask it to rewrite anything, only to decide.

`JUDGE_CLAIM_PROMPT` asks one yes/no question — is this claim entailed by
its cited span, read in the context of the passage — and a one-sentence
reason the author can be re-asked with. OWNER RULING 2026-09-13 (after the
proof chunk, where at least six of nine attempt-one refusals were false): the
passage as a whole is the evidence, plain arithmetic and reference count as
stated, and refusal is only for a fact the passage does not state or contradicts.
Since slice 9 (owner ruling
2026-09-12, slice-6 #5) the same call carries a KIND question: the judge
reads the claim's temporal kind (event / state / belief, CONTEXT.md) from
the span, and `judge_claims` RE-KINDS a claim the author kinded wrongly —
never refuses it — so `state_as_event` (spec §4) is caught without a
second call. Slots are substituted with plain
`str.replace`, never `str.format`, for the same reason prompts/decompose.py
gives: each prompt embeds a literal JSON example whose braces `.format`
would misparse as fields.
"""

from __future__ import annotations

from collections.abc import Sequence

_ENTAILED_RULE = (
    "Read the passage as a whole: the cited span is where the claim comes "
    "from, and the sentences around it are part of the evidence. A claim "
    "is entailed when every fact in it — every name, date, number, place "
    "and causal link — is stated by the passage, including what the "
    "passage makes plain without spelling out: arithmetic (\"in 1939 … four "
    "years later\" states 1943), a pronoun or a place named a clause "
    "earlier, a list given in the same sentence. Refuse only when the "
    "claim states a fact the passage does not state, or contradicts it: a "
    "changed value, an added detail, a cause the passage does not give. "
    "Different wording is never a reason to refuse."
)

_KIND_RULE = (
    "Also read the claim's temporal kind from the span: \"event\" — something "
    "that happened at a time and cannot change afterwards (opened in 1939, "
    "was completed, died); \"state\" — true as of the source's date and able "
    "to stop being true (houses, holds, stands at, is the tallest, is open); "
    "\"belief\" — what was held to be so as of the source's date. When the "
    "kind is unclear, answer \"state\". Answer the kind whatever the "
    "entailment verdict is; a wrong kind is never a reason to refuse."
)

_VERDICT_SHAPE = '{"entailed": true, "reason": "...", "kind": "event"}'

JUDGE_CLAIM_PROMPT = (
    "You are checking one factual claim extracted from a guidebook passage "
    "against the source it cites.\n\n"
    f"{_ENTAILED_RULE}\n\n"
    f"{_KIND_RULE}\n\n"
    "Answer as JSON only, matching this shape exactly:\n"
    f"{_VERDICT_SHAPE}\n"
    "The reason is one sentence naming what the span does or does not "
    "support; kind is one of \"event\", \"state\", \"belief\".\n\n"
    "Claim:\n{claim}\n\n"
    "Cited span:\n{span}\n\n"
    "Passage:\n{source}"
)

#: Claude's structured-output subset of JSON Schema (type/properties/
#: required/additionalProperties only), every object node's `required`
#: equal to its own property names.
P3_VERDICT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "entailed": {"type": "boolean"},
        "reason": {"type": "string"},
        "kind": {"type": "string", "enum": ["event", "state", "belief"]},
    },
    "required": ["entailed", "reason", "kind"],
    "additionalProperties": False,
}


def render_judge_claim(claim_text: str, span: str, unit_text: str) -> str:
    """Render JUDGE_CLAIM_PROMPT for one claim (str.replace, see module doc)."""
    rendered = JUDGE_CLAIM_PROMPT.replace("{claim}", claim_text)
    rendered = rendered.replace("{span}", span)
    return rendered.replace("{source}", unit_text)


_RESTATE_SHAPE = '{"text": "...", "kind": "...", "span": "..."}'

RESTATE_PROMPT = (
    "A checker read one of your extracted claims against the passage it "
    "cites and refused it:\n\n"
    "Claim:\n{claim}\n\n"
    "Cited span:\n{span}\n\n"
    "Refusal:\n{reason}\n\n"
    "Restate the claim so that every fact in it is stated by the passage — "
    "drop or correct whatever the source does not support. Keep it "
    "standing entirely on its own, in your own wording (never eight words "
    "of the passage in a row outside an attributed quotation), with the "
    "span copied exactly from the passage word for word. Set kind to "
    '"event", "state", "belief" or "ambiguous".\n\n'
    "Answer as JSON only, matching this shape exactly:\n"
    f"{_RESTATE_SHAPE}\n\n"
    "Passage:\n{source}"
)

#: One P1 claim item — the same item shape as `P1_RESPONSE_SCHEMA`'s
#: `claims[]`, asked for on its own because the author restates ONE claim.
P3_RESTATE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "kind": {"type": "string", "enum": ["event", "state", "belief", "ambiguous"]},
        "span": {"type": "string"},
    },
    "required": ["text", "kind", "span"],
    "additionalProperties": False,
}


def render_restate(claim_text: str, span: str, reason: str, unit_text: str) -> str:
    """Render RESTATE_PROMPT, quoting the judge's refusal back verbatim.

    Raises ValueError on an empty reason: a re-ask with nothing to fix is
    a caller bug, not a case worth prompting the model around.
    """
    if not reason.strip():
        raise ValueError("render_restate requires the judge's reason to quote back")
    rendered = RESTATE_PROMPT.replace("{claim}", claim_text)
    rendered = rendered.replace("{span}", span)
    rendered = rendered.replace("{reason}", reason)
    return rendered.replace("{source}", unit_text)


_OMISSIONS_SHAPE = (
    '{"omitted": [{"fact": "...", "span": "..."}], '
    '"compound": [{"claim_id": "c01", "facts": ["...", "..."]}]}'
)

OMISSIONS_PROMPT = (
    "You are checking whether a set of claims extracted from a guidebook "
    "passage about a place has left anything out, and whether each claim "
    "states only one fact.\n\n"
    "List every fact the passage states about the place — a name, a date, "
    "a number, something that happened there, something that is true of "
    "it — that NO claim below carries. Ignore practicalities (opening "
    "hours, prices, phone numbers, addresses, transport), the book's own "
    "directions and recommendations, and anything a claim already states "
    "in other words. For each omitted fact, copy the span of the passage "
    "that states it exactly, word for word.\n\n"
    "Then list, by its id, every claim below that states more than one fact — "
    "two things about one subject (\"X is A and B\", \"X, which did A, is B\"), "
    "facts about two subjects, or a list together with its items — and write "
    "out the separate facts it bundles. A relation the passage itself states "
    "between two facts (one caused the other, one contrasts with the other) is "
    "one fact: never list such a claim.\n\n"
    "Answer as JSON only, matching this shape exactly, with an empty list "
    "for either when there is nothing to report:\n"
    f"{_OMISSIONS_SHAPE}\n\n"
    "Claims:\n{claims}\n\n"
    "Passage:\n{source}"
)

P3_OMISSIONS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "omitted": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "fact": {"type": "string"},
                    "span": {"type": "string"},
                },
                "required": ["fact", "span"],
                "additionalProperties": False,
            },
        },
        "compound": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim_id": {"type": "string"},
                    "facts": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["claim_id", "facts"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["omitted", "compound"],
    "additionalProperties": False,
}


def render_omissions(claims: Sequence[tuple[str, str]], unit_text: str) -> str:
    """Render OMISSIONS_PROMPT over the unit's `(claim_id, text)` claims, one
    per line and numbered, so a compound finding can name the claim."""
    claim_lines = "\n".join(f"- {cid}: {text}" for cid, text in claims) or "- (none)"
    rendered = OMISSIONS_PROMPT.replace("{claims}", claim_lines)
    return rendered.replace("{source}", unit_text)
