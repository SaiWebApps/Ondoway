"""P4 narrate: the author writes a story's narration from its claim texts.

Docs/ingestion/rebuild-spec.md §3 (P4) and CONTEXT.md ("Narration") pin
the shapes below.

`narrate(story, claims, client)` renders `prompts.render_narrate` over
the story's judged claims' TEXTS — never a span, never the unit — and
makes one sync `client.complete()` call under the `author` role (P4 is
not one of `llm.BATCH_PHASES`). It returns a `NarrationDraft`: the text,
`model.claims_hash` over those texts (every P3 survivor is resolved until
P6 says otherwise), the author model id the RESPONSE reported, and the
spoken duration at `SPOKEN_WPM`.

A draft, not a `model.Narration`: that record requires a judge verdict,
which only P5 (`src/ingest/judge_narration.py`) can supply. Fabricating a
placeholder verdict here would be exactly the unbound verdict the slice-1
validator refuses.

The code gate (`narration_gates`) runs on every answer: no
`NARRATION_LIFT_GATE_RUN`-word (seven) run with ANY claim span outside an
attributed quotation or a proper name (owner ruling 2026-09-13; the validator's
floor is eight) (`gates.lift` per span — the author never saw the spans, so a
run is a memorised source, which is the same defect), no provenance leak
(`gates.provenance_leak`, with the publisher names the caller passes from
the source's manifest), no framing verb (`gates.framing`). A failing
answer emits `narration_refused` and buys exactly ONE re-ask
(`prompts.render_narrate_redo`, the refused text and every reason quoted
back); a second failure emits `beat_held` and raises `BeatHeld` with
phase 'P4'. A third call never happens.

`BeatHeld` subclasses `decompose.UnitHeld` so a runner that stops on a
held unit stops on a held beat too; its key is the story's slug, since a
narration is per beat, not per chunk.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any, NoReturn

from pydantic import BaseModel, ConfigDict

from src.ingest import gates, llm, model, prompts
from src.ingest.decompose import UnitHeld
from src.ingest.group import Story
from src.ingest.judge_claims import JudgedClaim

#: Output tokens requested for one narration.
#: The narration lift run: OWNER RULINGS 2026-09-13 — seven words, not the
#: validator's eight-word floor, because on the first real job the book's own
#: seven-word phrase ("an eccentric German baroness named Hilla Rebay") reached
#: the listener under eight. Six was ruled first and refused 40% of job 1's
#: first-attempt narrations on ordinary phrasing; seven refused 20%.
NARRATION_LIFT_GATE_RUN: int = 7

P4_MAX_TOKENS: int = 16_000  # thinking-inclusive; tests/test_ingest_output_caps.py

#: What the estimate prices per narration — a projection: ~150 words of
#: prose plus thinking at ~1x (Opus).
P4_EXPECTED_OUTPUT_TOKENS: int = 1_000  # job 1: P2+P4 sync output 24,503 over 27 calls

#: Words a minute of narrated speech. The same 150 the tour engine's
#: audio clock uses (`src.tour.routing.beat_spoken_seconds`'s fallback and
#: `src.tour.generation.SPOKEN_WPM`), spelled here rather than imported
#: because the corpus side never depends on the engine that consumes it.
SPOKEN_WPM: int = 150

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.S)


class BeatHeld(UnitHeld):
    """A beat could not produce a usable narration after the single re-ask.

    `unit_key` (inherited) and `story_slug` are the same value: the beat is
    the item P4/P5 hold, and the story slug is its identity within a job.
    """

    def __init__(self, story_slug: str, phase: str, reason: str) -> None:
        super().__init__(story_slug, phase, reason)
        self.story_slug = story_slug


class NarrationDraft(BaseModel):
    """What P4 produced, before P5 has judged a sentence of it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    claims_hash: str
    author_model: str
    duration_sec: int


#: The P4 cost-estimate plan, priced PER CLAIM TEXT (estimate it over the
#: story's claim texts): the first ask, and the one re-ask — a CEILING,
#: every story priced as if refused once, until slice 9 measures live
#: refusal rates.
P4_PLAN: tuple[llm.PhaseCall, ...] = (
    llm.PhaseCall(
        phase="P4",
        role="author",
        calls_per_unit=1,
        overhead_tokens=max(1, len(prompts.NARRATE_PROMPT) // 4),
        expected_output_tokens=P4_EXPECTED_OUTPUT_TOKENS,
    ),
    llm.PhaseCall(
        phase="P4",
        role="author",
        calls_per_unit=1,
        overhead_tokens=max(1, len(prompts.NARRATE_REDO_PROMPT) // 4),
        expected_output_tokens=P4_EXPECTED_OUTPUT_TOKENS,
    ),
)


def duration_sec(text: str) -> int:
    """Spoken seconds of `text` at `SPOKEN_WPM` — the engine's own formula."""
    words = len(text.split())
    return round(words / SPOKEN_WPM * 60)


def _extract_json_object(text: str) -> Any | None:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    match = _JSON_OBJECT_RE.search(text)
    if match is None:
        return None
    try:
        return json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return None


def parse_narration(text: str) -> str | None:
    """Turn one raw P4 answer into its narration text, or None."""
    parsed = _extract_json_object(text)
    if not isinstance(parsed, dict) or set(parsed.keys()) != {"narration"}:
        return None
    if not isinstance(parsed["narration"], str) or not parsed["narration"].strip():
        return None
    return parsed["narration"]


def resolved_claim_texts(claims: Sequence[JudgedClaim]) -> list[str]:
    """The claim texts P4 writes from, in claim order."""
    return [claim.draft.text for claim in claims]


def claim_spans(claims: Sequence[JudgedClaim]) -> list[str]:
    """Every span the story's claims cite, in claim order."""
    return [claim.draft.source.span for claim in claims]


_PARENTHESISED_RE = re.compile(r"\s*\(([^)]*)\)\s*")


def publishers_from_manifest(manifest: Mapping[str, Any]) -> list[str]:
    """The publisher names a source's manifest gives, for the leak gate.

    Reads the `publisher` field; a parenthesised imprint ("Rough Guides
    (Penguin)") is split out as a name of its own. A manifest without the
    field yields [] — the fixed phrases still guard the narration.
    """
    raw = manifest.get("publisher")
    if not isinstance(raw, str) or not raw.strip():
        return []
    names = [part.strip() for part in _PARENTHESISED_RE.split(raw)]
    return [name for name in names if name]


def narration_gates(
    text: str, spans: Sequence[str], publishers: Sequence[str] = ()
) -> list[str]:
    """The P4 code gate over one narration; never short-circuits.

    Returns one reason per failing gate in the fixed order lift (the first
    span whose run clears `gates.LIFT_RUN_LENGTH`), provenance_leak (every
    match, quoted), framing (every match, quoted). [] for a clean narration.
    """
    reasons: list[str] = []
    for span in spans:
        lift_reason = gates.lift(text, span, run_length=NARRATION_LIFT_GATE_RUN)
        if lift_reason is not None:
            reasons.append(lift_reason.replace("from the unit", "from a claim's span"))
            break
    leaks = gates.provenance_leak(text, publishers)
    if leaks:
        quoted = ", ".join(repr(leak) for leak in leaks)
        reasons.append(f"provenance_leak: {quoted} reveals the narration was read from a book")
    framed = gates.framing(text)
    if framed:
        quoted = ", ".join(repr(verb) for verb in framed)
        reasons.append(f"framing: {quoted} frames the listener; a beat only tells")
    return reasons


Emit = Callable[[str, dict], None]


def emitter(events: llm.EventSink | None) -> Emit:
    """An Emit that forwards to `events` when there is one."""
    def emit(kind: str, payload: dict) -> None:
        if events is not None:
            events(kind, payload)

    return emit


def hold(
    emit: Emit, story: Story, phase: str, reason: str, cause: BaseException | None = None
) -> NoReturn:
    """Emit `beat_held` and raise `BeatHeld` for `phase`."""
    emit("beat_held", {"story_slug": story.story_slug, "phase": phase, "reason": reason})
    raise BeatHeld(story.story_slug, phase, reason) from cause


def ask_author(
    emit: Emit, story: Story, client: llm.ModelClient, prompt: str, *, phase: str = "P4"
) -> tuple[str, str]:
    """One sync P4 call; returns (narration text, response model id).

    The same failure contract as decompose()/group(): an
    `llm.EmptyCompletion`/`llm.TruncatedCompletion`, or a raw
    (non-LlmError) `ValueError` from a duck-typed transport, holds the
    beat in `phase` — P4 for narrate's own asks, P5 when the ask is the
    narration judge's re-ask — as does an unreadable answer; any other
    `llm.LlmError` (EstimateNotPrinted, MockScriptExhausted, ...) is a
    programming error and propagates as itself.
    """
    try:
        completion = client.complete(
            "author", prompt, prompts.P4_NARRATION_SCHEMA, phase="P4", max_tokens=P4_MAX_TOKENS
        )
    except (llm.EmptyCompletion, llm.TruncatedCompletion) as exc:
        hold(emit, story, phase, f"transport: {exc}", exc)
    except llm.LlmError:
        raise
    except ValueError as exc:
        hold(emit, story, phase, f"transport: {exc}", exc)
    text = parse_narration(completion.text)
    if text is None:
        hold(
            emit,
            story,
            phase,
            "schema: the author's answer was not valid JSON matching the P4 narration schema",
        )
    return text, completion.model_id


def draft_from(text: str, claims: Sequence[JudgedClaim], author_model: str) -> NarrationDraft:
    """A NarrationDraft over `text`, hashed over the claims' resolved texts."""
    texts = resolved_claim_texts(claims)
    return NarrationDraft(
        text=text,
        claims_hash=model.claims_hash([{"text": t, "status": "resolved"} for t in texts]),
        author_model=author_model,
        duration_sec=duration_sec(text),
    )


def narrate(
    story: Story,
    claims: Sequence[JudgedClaim],
    client: llm.ModelClient,
    events: llm.EventSink | None = None,
    publishers: Sequence[str] = (),
) -> NarrationDraft:
    """Write the story's narration from its claims; see the module docstring.

    `publishers` are the source's publisher names (from its manifest), so
    "Lonely Planet calls it" is a leak when the source is Lonely Planet.
    """
    emit = emitter(events)
    texts = resolved_claim_texts(claims)
    spans = claim_spans(claims)

    text, author_model = ask_author(
        emit, story, client, prompts.render_narrate(story.place, story.title, texts)
    )
    reasons = narration_gates(text, spans, publishers)
    if not reasons:
        return draft_from(text, claims, author_model)
    emit(
        "narration_refused",
        {"story_slug": story.story_slug, "attempt": 1, "reasons": list(reasons)},
    )

    text, author_model = ask_author(
        emit,
        story,
        client,
        prompts.render_narrate_redo(story.place, story.title, texts, text, reasons),
    )
    reasons = narration_gates(text, spans, publishers)
    if not reasons:
        return draft_from(text, claims, author_model)
    emit(
        "narration_refused",
        {"story_slug": story.story_slug, "attempt": 2, "reasons": list(reasons)},
    )
    hold(emit, story, "P4", "; ".join(reasons))
