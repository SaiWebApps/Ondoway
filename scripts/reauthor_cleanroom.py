"""Regenerate a copied beat from claims alone — the writer never sees the source.

`reauthor_run.py` gave the writer the guidebook passage and asked for the same
content in different words. That preserves selection and arrangement, which is the
part copyright protects: 91% of its rewrites with a multi-sentence source still
follow that source's sentence order. Detecting it afterwards cannot repair it, so
this removes the writer's ACCESS to it instead.

Two phases, two artifacts, so the expensive half is bought once:

1. **decompose** — the source becomes an unordered set of atomic, self-contained
   claims in `data/{city}/claims.json`. Self-contained is not a style preference:
   the set is shuffled before the writer sees it, and "he died in 1778" means
   nothing once it has moved.
2. **write** — the claim set is shuffled and handed to the writer in
   `data/{city}/reauthored-cleanroom.json`. `cleanroom_request` is built from the
   claims and the voice rules and takes no other argument, so there is no parameter
   through which the source or the old body could reach the model. That structure,
   not a score, is the evidence of non-derivation — and `tests/` asserts it against
   real records.

**A free gate runs before any writer call.** A decomposer that returns lightly
edited source sentences would reintroduce the arrangement at claim granularity and
buy a more expensive version of the same defect, so every claim goes through Stage
0's own `max_verbatim_run` against the source. A beat with a lifted claim is
refused, never written from.

**What shuffling buys, and what it does not.** It removes access to the source's
arrangement; it does not make the output's order differ. Guidebook passages are
often chronological and a writer re-derives chronological order on its own, so
`order_follows_source` is expected to stay high on this output. Chronology is the
world's order, not the guidebook's, and a high reading here is not the fix failing.

Both phases are paid and refuse to run without `ONDOWAY_DEMO_APPROVE=1`. Neither
writes to `beats.json`, and neither touches `data/{city}/reauthored.json` — that
file is the subject of the Stage 0 report and stays as it was.

The old path in `reauthor_run.py` still stands. It is the provenance of the file
Stage 0 reports on and is retired in its own change, once this has produced output.

Run as:
    ONDOWAY_DEMO_APPROVE=1 uv run python scripts/reauthor_cleanroom.py \\
        --city paris --phase decompose
    ONDOWAY_DEMO_APPROVE=1 uv run python scripts/reauthor_cleanroom.py \\
        --city paris --phase write
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.corpus_report import (
    VERBATIM_RUN_BLOCK,
    VERBATIM_THRESHOLD,
    load_city_beats,
    max_verbatim_run,
    quoted_char_spans,
    verbatim_ratio,
    verbatim_word_spans,
    verbatim_words,
)
from scripts.reauthor_preview import _VOICE_RULES, is_excluded
from src.city_registry import load_registry
from src.tour.claim_dedup import (
    _STOPWORDS as STOPWORDS,
)
from src.tour.claim_dedup import (
    COVERAGE_MATCH_MIN,
    _overlap,
    _signature,
)
from src.tour.generation import split_sentences

#: The one model that PRODUCES, in both roles. Decomposing and writing with the same
#: model costs Stage 2 one judge rather than two: a panel must exclude every model
#: that made the artifact, and a decomposer will not flag a claim it invented itself.
#: Recorded per record so the exclusion is read from evidence, not from a constant.
PRODUCER_MODEL = "claude-opus-5"

#: Adaptive thinking is on by default and its tokens count against this ceiling, so a
#: beat-sized limit truncates mid-sentence.
MAX_TOKENS = 8000

#: Concurrent calls, matching the pool the sibling paid scripts already use.
DEFAULT_WORKERS = 8

#: The shortest beat worth writing. A handful of very short copied bodies would
#: otherwise ask for a length no sentence can land in.
MIN_TARGET_WORDS = 40

#: Salient words a source sentence needs before its absence means anything. Below this
#: a "sentence" is a fragment — "Anything wiggling?" — that states no fact for a claim
#: to carry, and reporting it as lost content buys a retry that can fix nothing.
#: `claim_dedup`'s MIN_SHARED_TOKENS is 2, which is the floor for a CLAIM's signature;
#: a whole sentence is held to more.
MIN_SENTENCE_TOKENS = 4

#: Claims about the BOOK rather than the place. A guidebook passage carries its own
#: apparatus — page numbers, numbered walk steps, "the author advises" — and the
#: decomposer read that as fact because it is stated as fact. The writer, told to say
#: every fact, then handed it to a listener who has no guidebook. The clean room keeps
#: the source's prose out and had nothing to say about its furniture.
_APPARATUS = re.compile(
    r"\b(guidebook|guide ?book|this walk|the walk|walking tour|itinerary"
    r"|step \d+|point [A-Z]|page \d+|pp?\. \d+|the author (advises|suggests|recommends|says)"
    r"|numbered (entry|entries|point|step))\b",
    re.I,
)

#: First-person words, checked outside quotations. A Roman numeral and an initial are
#: written exactly like the pronoun ("Napoleon I", "I. M. Pei", "World War I"), so a bare
#: `I` counts only where nothing around it makes it part of a name.
_FIRST_PERSON = {
    "i",
    "i'm",
    "i've",
    "i'd",
    "my",
    "mine",
    "me",
    "we",
    "we're",
    "we've",
    "we'd",
    "us",
    "our",
    "ours",
    "let's",
}

#: Sentence-opening imperatives, and the stem that shows a claim asked for one. A
#: passage of walking directions puts the instruction IN the claims, and a body
#: following them is doing as it was told; the same words with no claim behind them
#: are the writer staging the listener.
# fmt: off
#: Imperative openings that put the listener somewhere, and the words a claim uses when
#: it is the passage that put them there. A guidebook of walking directions states the
#: instruction as fact, and a body following it reports the route rather than staging
#: anybody — but the decomposer is told to reword, so the claim behind "Follow it" may
#: say "continue along". The whole family licenses the whole family for that reason.
_MOVEMENT = frozenset([
    "stand", "stands", "standing", "stood", "sit", "sits", "sitting", "sat",
    "walk", "walks", "walking", "walked", "turn", "turns", "turning", "turned",
    "cross", "crosses", "crossing", "crossed", "follow", "follows", "following",
    "followed", "head", "heads", "heading", "headed", "step", "steps", "stepping",
    "stepped", "stop", "stops", "stopping", "stopped", "continue", "continues",
    "continuing", "continued", "climb", "climbs", "enter", "enters", "leave",
    "leaves", "pass", "passes", "route", "path", "left", "right", "along",
])

#: Imperative openings that direct attention or invent a mental picture. No claim
#: licenses these: a passage states what is there, never what a listener should do
#: about it.
_ATTENTION = frozenset(["look", "notice", "pause", "imagine", "picture", "watch", "listen"])

_STAGE_OPENINGS = sorted(
    {w for w in _MOVEMENT if len(w) > 3} | _ATTENTION,
    key=len,
    reverse=True,
)
# fmt: on

_STAGE_OPENING = re.compile(
    r"(?:\A|(?<=[.!?]\s)|(?<=[.!?]\s\s))\s*(" + "|".join(_STAGE_OPENINGS) + r")\b",
    re.I,
)

#: Concrete things a listener can see, and times of day. These are the words a body
#: reaches for when it lands a beat on a scene rather than on a fact, and they are the
#: ones a person standing there checks with their eyes. Ambiguous words are left out —
#: "step" is usually a verb here and "line" is four different nouns.
# fmt: off
_SCENE_WORDS = frozenset([
    "bench", "benches", "table", "tables", "chair", "chairs", "window", "windows",
    "door", "doors", "gate", "gates", "railing", "railings", "path", "paths",
    "lawn", "grass", "tree", "trees", "flower", "flowers", "fountain",
    "cobbles", "cobblestones", "wood", "wooden", "stone", "brick", "bricks",
    "iron", "glass", "marble", "boat", "boats", "bicycle", "bicycles",
    "car", "cars", "bus", "buses", "crowd", "crowds", "queue",
    "dusk", "dawn", "sunset", "sunrise", "tonight", "midnight", "moonlight",
])
# fmt: on

#: The house voice for offering an impression. It is the right way to say an observation
#: the claims carry, and the only way to say one they do not.
_IMPRESSION = re.compile(
    r"\b(?:may (?:strike|read|feel|register|seem|notice|well|come)|you may|might strike"
    r"|may catch|reads? less as|starts? to feel|feels? like)\b",
    re.I,
)

#: A listing a listener cannot use. Opening hours, a phone number and a web address are
#: the guidebook's practical furniture, and they reach the body through claims that
#: state them as fact about the place.
_LISTING = re.compile(
    r"(?:\b\d{3}[-. ]\d{3}[-. ]?\d{4}\b|\b(?:www\.|https?://)\S+"
    r"|\b[\w-]+\.(?:com|org|net|edu|us|fr|gov)\b)",
    re.I,
)

#: Openings that point at a neighbouring claim. The set is shuffled before the writer
#: sees it, so "That uprising alarmed the kings" has nothing to refer to by the time it
#: is read. A demonstrative followed by a verb ("That is the oldest") is a complete
#: sentence and not an anaphor, so it is left alone.
_ANAPHOR = re.compile(
    r"^\s*(?:(?:He|She|It|They|Him|Her|Them|His|Their|Its)\b"
    r"|(?:That|This|These|Those)\s+(?!is\b|are\b|was\b|were\b)\w"
    r"|(?:Later|Also|Afterwards|Subsequently|The same)\b)"
)

#: What a claim is marked as. Two kinds, not a taxonomy: `reauthor_preview`'s
#: `grounding_claims` records two measured beats where the source carried the
#: author's observation ("the breezes are bracing", a Michelin comparison) and the
#: stored claims did not, so a rewrite covering them was refused as ungrounded. An
#: observation that is dropped loses material; one written as fact is editorialising.
CLAIM_KINDS = ("fact", "observation")

_REPO_ROOT = Path(__file__).resolve().parent.parent

_DECOMPOSE_PROMPT = """Break this guidebook passage into atomic claims.

Each claim states ONE checkable thing and stands entirely on its own. The claims will
be SHUFFLED before anyone reads them, so a claim that depends on its neighbours is
useless: never write "he", "it", "there", "the same year", "later" or "also". Name the
subject in every claim, even when that repeats a name many times.

Cover everything the passage carries. A claim you omit is a fact the finished text
will not contain. That includes what the author observed rather than recorded — how a
place feels, looks or smells, and comparisons the author drew — which you mark as an
observation so it is not later written up as established fact.

Where the passage says one thing happened because of, instead of, in return for or as
a condition of another, that relation is itself a fact and belongs INSIDE a claim that
names both sides of it. Splitting the two events into separate claims loses it: shuffled
apart, "he offered to pay" and "he agreed to donate the profits" no longer say which
offer the donation belonged to, and nothing downstream can tell that the passage said.
The same holds for a claim that is only true within one arrangement, one period or one
proposal — the claim names that arrangement itself.

Use your own wording. Do not reuse the passage's phrasing: a claim that repeats eight
words of the passage in a row is a lifted claim and will be rejected.

The passage is a page from a guidebook and carries the book's own furniture: page
numbers, numbered walk steps, "the author advises", directions phrased as instructions
in a printed tour. None of that is a fact about the place, and the listener has no
guidebook. Never write a claim about the book, its route, its pages or its author. Where
such a sentence also states something about the place, keep that part only.

kind is "fact" for anything checkable against the world, "observation" for the
author's own impression, comparison or judgement.

PASSAGE:
{source}

Reply with JSON only, no prose:
{{"claims": [{{"claim": "...", "kind": "fact"}}]}}"""

_REDO_PROMPT = """Your claim set was refused. What is wrong with it:

{problems}

Decompose the passage again, from the start, fixing all of it.

PASSAGE:
{source}

Reply with JSON only, no prose:
{{"claims": [{{"claim": "...", "kind": "fact"}}]}}"""

_BODY_REDO_PROMPT = """That draft breaks a rule. What is wrong with it:

{problems}

Write it again from the same claims. Everything else about the brief still holds."""


_WRITE_PROMPT = """Write the body of an audio-tour beat about {poi}, in {city}.

Below is everything you know. It is an unordered set — the order means nothing, and
you decide what to say first, what belongs together, and what closes. Say every claim
marked "fact".

Claims marked "observation" are one writer's impression, not a record. Offer one as
something a person standing here may notice, or leave it out. At most one of these in a
beat, and never as the closing sentence: a listener walks past a dozen stops, and a
dozen beats that all end by hedging an impression is one voice with one move. Never state one as
established fact, and never hand it to somebody else to say: "some visitors find",
"people say", "it is often remarked" invents a source and is the worst thing you can do
here. Do not say "I".

Add nothing. Not a cause and not a sequence — if no claim says one thing led to,
followed from or was a condition of another, they are separate things that happened, and
you say them separately. Not a fact, and not a physical detail either — if the claims do not say
the roadway widens, the roadway does not widen, and you cannot tell the listener to
look at it. Never write "imagine" or "picture", and never tell the listener to sit,
stand, walk or take a minute unless a claim says so.

Write as the tour, not as a person. Never say "I", and never say "we" or "us" — there is
no guide walking beside the listener.

Aim for about {words} words. This is spoken aloud between stops on a walk, and a beat
that runs long eats the silence the tour is built around.

House voice:
{voice}

WHAT YOU KNOW:
{claims}

Write the body only. No preamble, no title, no quotes around it."""


# ── phase 1: the source becomes claims ───────────────────────────────────────


def decompose_request(*, source: str) -> dict[str, Any]:
    """The request that turns one passage into claims, shaped here so a test can pin it.

    No sampling parameters: they are removed on this model and sending one is a 400.
    """
    return {
        "model": PRODUCER_MODEL,
        "max_tokens": MAX_TOKENS,
        "messages": [{"role": "user", "content": _DECOMPOSE_PROMPT.format(source=source)}],
    }


def redo_problems(record: dict) -> str:
    """What to tell the decomposer its claim set got wrong, in its own terms."""
    parts: list[str] = []
    if record.get("lifted_claims"):
        parts.append(
            "These claims still repeat the passage's wording. A claim sharing eight\n"
            "consecutive words with the passage is refused even when those words are an\n"
            "ordinary way to state the fact — find another way to say it:\n"
            + "\n".join(f"- {c}" for c in record["lifted_claims"])
        )
    if record.get("dangling_claims"):
        parts.append(
            "These claims point at another claim. The set is SHUFFLED before anyone reads\n"
            "it, so each one must name its own subject:\n"
            + "\n".join(f"- {c}" for c in record["dangling_claims"])
        )
    if record.get("apparatus_claims"):
        parts.append(
            "These claims are about the guidebook, not about the place. The listener has\n"
            "no guidebook and no page or step numbers. Drop them, and state any fact about\n"
            "the place itself that they were wrapped around:\n"
            + "\n".join(f"- {c}" for c in record["apparatus_claims"])
        )
    if record.get("uncovered_sentences"):
        parts.append(
            "Nothing in your claim set carries what these sentences say. Every one is a\n"
            "fact that would leave the corpus silently:\n"
            + "\n".join(f"- {s}" for s in record["uncovered_sentences"])
        )
    return "\n\n".join(parts)


def redo_request(*, source: str, problems: str) -> dict[str, Any]:
    """The second and final ask, naming exactly what the first set got wrong.

    A whole beat is worth more than one stubborn claim. "On the eastern side of the
    Pont-Neuf" is eight shared words and also the plainest way to state where a square
    is, so refusing the beat outright would discard thirteen good claims to punish an
    unavoidable locution. No threshold moves — the decomposer is asked again.
    """
    return {
        "model": PRODUCER_MODEL,
        "max_tokens": MAX_TOKENS,
        "messages": [
            {
                "role": "user",
                "content": _REDO_PROMPT.format(problems=problems, source=source),
            }
        ],
    }


def parse_claims(text: str) -> list[dict[str, str]] | None:
    """Read the decomposer's answer, or None when it cannot be read.

    An unreadable answer yields nothing rather than a partial set: a claim list that
    silently lost half its claims is how a fact leaves the corpus unnoticed.
    """
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except ValueError:
        return None
    raw = parsed.get("claims") if isinstance(parsed, dict) else None
    if not isinstance(raw, list) or not raw:
        return None
    claims = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        claim = str(item.get("claim", "")).strip()
        kind = str(item.get("kind", "")).strip().lower()
        if not claim:
            return None
        claims.append({"claim": claim, "kind": kind if kind in CLAIM_KINDS else "fact"})
    return claims


def lifted_claims(claims: list[dict[str, str]], source: str) -> list[str]:
    """Claims that carry a run of the source long enough to be a lift.

    Stage 0's measure, applied before a single writer call. Shuffling a set of
    lightly edited source sentences preserves the arrangement inside each one and
    hands the writer the same prose in a different container.
    """
    return [
        c["claim"] for c in claims if max_verbatim_run(c["claim"], source) >= VERBATIM_RUN_BLOCK
    ]


def apparatus_claims(claims: list[dict[str, str]]) -> list[str]:
    """Claims about the guidebook rather than about the place.

    A listener has no guidebook, no page 139 and no step nine. These are facts about
    the source document, true and useless, and voicing one tells a stranger about a
    book they cannot open.
    """
    return [c["claim"] for c in claims if _APPARATUS.search(c["claim"])]


def dangling_claims(claims: list[dict[str, str]]) -> list[str]:
    """Claims that point at a neighbour they will not have once the set is shuffled."""
    return [c["claim"] for c in claims if _ANAPHOR.match(c["claim"])]


def coverage_recall(records: list[dict], *, seed: int = 11) -> dict[str, Any]:
    """What share of planted deletions `uncovered_sentences` actually catches.

    LEARNINGS #24: a gate scored on its own refusal rate measures leniency and nothing
    else. This one was changed until refusals fell from 34% to 26% and that was briefly
    reported as an improvement, which is the same error. The measurement that means
    something is recall against known-missing content, and it is free: drop a claim from
    a set the gate passes and ask whether the gate now objects.
    """
    rng = random.Random(seed)
    usable = [r for r in records if r.get("usable") and len(r.get("claims") or []) > 2]
    one_caught = whole_third_caught = 0
    for record in usable:
        claims, source = record["claims"], record["source_passage"]
        without_one = [c for i, c in enumerate(claims) if i != rng.randrange(len(claims))]
        if uncovered_sentences(without_one, source):
            one_caught += 1
        kept = rng.sample(claims, max(1, int(len(claims) * 0.7)))
        if uncovered_sentences(kept, source):
            whole_third_caught += 1
    total = len(usable) or 1
    return {
        "sets": len(usable),
        "one_claim_deleted_caught": one_caught,
        "one_claim_deleted_recall_pct": round(100 * one_caught / total, 1),
        "third_deleted_caught": whole_third_caught,
        "third_deleted_recall_pct": round(100 * whole_third_caught / total, 1),
    }


def uncovered_sentences(claims: list[dict[str, str]], source: str) -> list[str]:
    """Source sentences the claim set does not carry — GROSS omission only.

    **Measured, not assumed.** `coverage_recall` plants deletions in sets this gate
    passes: it catches 12% of single dropped claims and 39% of a dropped third. It is a
    detector of a passage that was barely decomposed at all — it caught Square Jean
    XXIII, 4 claims from 213 words — and it is NOT a guarantee that nothing was lost.
    Token overlap cannot see a missing proposition when the surviving claims share the
    vocabulary, and no variant tried does better: best-1 reaches 21% recall at 13% false
    refusals, and a claims-per-sentence floor reaches 3%. Omission is Stage 2's problem,
    where claims are extracted from the source and from the output in calls that never
    see each other's input and the two lists are diffed both ways.

    Omission is the decomposer's dangerous failure: a claim never written is a fact the
    finished body cannot contain, and nothing downstream can tell that apart from a
    source that never said it. Measured with `claim_dedup`'s own coverage primitives at
    `COVERAGE_MATCH_MIN`, the threshold that module already documents as catching gross
    deletion while tolerating rewording. A sentence too short to have a signature is
    skipped rather than reported, exactly as `claims_realized_by` skips such claims.

    A sentence is checked against the UNION of the claims, never the best single one.
    One sentence is deliberately split into several claims and each is deliberately
    reworded, so "Located in room 13 on the first floor, open daily except Tues, 9:15"
    is carried between four claims and matched by none of them alone.
    """
    covered: set[str] = set()
    for claim in claims:
        covered |= _signature(claim["claim"])
    union = frozenset(covered)
    missed = []
    for sentence in split_sentences(source):
        sig = _signature(sentence)
        if len(sig) < MIN_SENTENCE_TOKENS:
            continue
        if _overlap(sig, union) < COVERAGE_MATCH_MIN:
            missed.append(sentence)
    return missed


def claims_record(beat: dict, *, claims: list[dict[str, str]]) -> dict[str, Any]:
    """One beat's claim set, with what the free gate found in it."""
    source = (beat.get("source_passage") or "").strip()
    lifted = lifted_claims(claims, source)
    dangling = dangling_claims(claims)
    apparatus = apparatus_claims(claims)
    uncovered = uncovered_sentences(claims, source) if claims else []
    return {
        "beat_id": beat.get("beat_id", ""),
        "poi_name": beat.get("poi_name", ""),
        "source_passage": source,
        "claims": claims,
        "lifted_claims": lifted,
        "dangling_claims": dangling,
        "apparatus_claims": apparatus,
        "uncovered_sentences": uncovered,
        "usable": not (lifted or dangling or apparatus or uncovered),
        "model": PRODUCER_MODEL,
        "generated_at": datetime.now(UTC).isoformat(),
    }


# ── what refuses a written body ───────────────────────────────────


def _sentences_matching(body: str, matches: Any) -> list[str]:
    """The sentences a scan hit, so a re-ask names prose rather than a rule number."""
    out: list[str] = []
    for sentence in split_sentences(body):
        if matches(sentence) and sentence not in out:
            out.append(sentence.strip())
    return out


def first_person_sentences(body: str) -> list[str]:
    """Sentences where the tour speaks as a person.

    There is no guide walking beside the listener, so "before we go on" describes a
    companion the product does not have. Quotations are exempt, because a person quoted
    in a beat is entitled to say "I" — and so is a bare `I` with a name around it, which
    is how "Napoleon I", "World War I" and "I. M. Pei" are written.
    """
    quoted = quoted_char_spans(body)
    hits: list[tuple[int, int]] = []
    for word, start, end in verbatim_word_spans(body):
        if word not in _FIRST_PERSON:
            continue
        if any(qs <= start and end <= qe for qs, qe in quoted):
            continue
        if not _reads_as_a_pronoun(body, word, start, end):
            continue
        hits.append((start, end))
    if not hits:
        return []
    return _sentences_matching(body, lambda sentence: _covers(body, sentence, hits))


def _reads_as_a_pronoun(body: str, word: str, start: int, end: int) -> bool:
    """Whether a first-person word here is the pronoun rather than part of a name.

    Every one of these words is also a name or an abbreviation somewhere — a US Coast
    Guard cutter, the play All My Sons, Our Lady, Napoleon I, I. M. Pei. The pronoun is
    written in lower case unless it opens a sentence, and a numeral or an initial never
    is, so case and position settle it without a list of exceptions to maintain.
    """
    before = body[:start].rstrip()
    opens_a_sentence = not before or before[-1] in ".!?:\u2014"
    if word == "i":
        if body[end : end + 1] == ".":
            return False
        return opens_a_sentence or not before.split()[-1][:1].isupper()
    if body[start:end].islower():
        return True
    if not opens_a_sentence:
        return False
    # "Our Lady" opens a sentence in the same shape the pronoun does, and case alone
    # cannot separate them. A name carries on into another capital; a pronoun does not.
    following = body[end:].lstrip()
    return not following[:1].isupper()


def _covers(body: str, sentence: str, hits: list[tuple[int, int]]) -> bool:
    """Whether any hit falls inside this sentence's span of the body."""
    start = body.find(sentence)
    if start < 0:
        return False
    end = start + len(sentence)
    return any(start <= hit_start and hit_end <= end for hit_start, hit_end in hits)


def apparatus_sentences(body: str) -> list[str]:
    """Sentences that hand the listener the guidebook's own furniture."""
    return _sentences_matching(body, lambda s: bool(_APPARATUS.search(s) or _LISTING.search(s)))


def stage_directions(body: str, claims: list[dict[str, str]]) -> list[str]:
    """Imperatives no claim asked for.

    A source of walking directions puts the instruction in the claims, and a body that
    follows them is reporting a fact about the route. The same opening with no claim
    behind it is the writer inventing what the listener is doing.
    """
    said = {w for c in claims for w in verbatim_words(c.get("claim", ""))}
    licensed = bool(said & _MOVEMENT)
    return _sentences_matching(
        body,
        lambda sentence: any(
            m.group(1).lower() in _ATTENTION or not licensed
            for m in _STAGE_OPENING.finditer(sentence)
        ),
    )


def invented_impressions(body: str, claims: list[dict[str, str]]) -> list[str]:
    """Impressions offered to the listener where the claim set holds none.

    An observation reaches a body as something a person standing here may notice. A set
    with no observation in it gives the writer nothing to offer, so a hedged impression
    in that body is the writer's own — the same defect as stating one as fact, wearing
    the house voice.
    """
    if any(c.get("kind") == "observation" for c in claims):
        return []
    return _sentences_matching(body, lambda sentence: bool(_IMPRESSION.search(sentence)))


def unsupported_words(
    body: str, claims: list[dict[str, str]], *, poi: str = "", city: str = ""
) -> list[str]:
    """Content words in a body that no claim it was given carried.

    This is a READING AID, not a check. Nearly every body has some — a writer told to
    say a fact in its own words must reach for words the claim did not use — so the
    count means nothing and the list means a great deal: it is where "a profusion of
    coloured marble" became "reds, greens, whites". A reviewer cannot hold a dozen
    claims in their head while reading a paragraph, and this is the part of that job a
    computer can do.

    A word is not counted when a claim carries something it plainly came from, so a
    plural, a tense or a suffix does not read as invention, nor is the name of the place
    the beat is about.
    """
    said = {w for c in claims for w in verbatim_words(c.get("claim", ""))}
    # The place and the city are in the writer's request, so naming them is not
    # invention even when no claim happens to repeat the name.
    said |= set(verbatim_words(poi)) | set(verbatim_words(city))
    stems = {w[:5] for w in said if len(w) > 4}
    seen, out = set(), []
    for word in verbatim_words(body):
        if word in said or word in STOPWORDS or word in seen or len(word) < 4:
            continue
        if word.isdigit() or (len(word) > 4 and word[:5] in stems):
            continue
        seen.add(word)
        out.append(word)
    return out


def scene_details(body: str, claims: list[dict[str, str]]) -> list[str]:
    """The unsupported words that name a thing a listener can see, or a time of day.

    The narrow slice of `unsupported_words` worth recording on the record itself: these
    are what a person standing at the place checks with their eyes, so an invented one
    is caught by the listener rather than by a reader.
    """
    return sorted(set(unsupported_words(body, claims)) & _SCENE_WORDS)


def body_problems(body: str, claims: list[dict[str, str]]) -> dict[str, list[str]]:
    """Every free refusal a written body can earn, keyed by what it broke."""
    return {
        "first_person": first_person_sentences(body),
        "apparatus": apparatus_sentences(body),
        "stage_direction": stage_directions(body, claims),
        "invented_impression": invented_impressions(body, claims),
    }


# ── phase 2: the claims become a body ────────────────────────────────────────


def shuffle_seed(beat_id: str) -> int:
    """A per-beat seed, so the order the writer saw can be rebuilt from the record."""
    return int(hashlib.sha256(beat_id.encode("utf-8")).hexdigest()[:8], 16)


def shuffled_claims(claims: list[dict[str, str]], beat_id: str) -> list[dict[str, str]]:
    """The claim set in the order the writer will receive it."""
    out = list(claims)
    random.Random(shuffle_seed(beat_id)).shuffle(out)
    return out


def render_claims(claims: list[dict[str, str]]) -> str:
    """The claim block exactly as the writer receives it."""
    return "\n".join(f"- [{c['kind']}] {c['claim']}" for c in claims)


def cleanroom_request(
    *, claims: list[dict[str, str]], poi: str, city: str, words: int
) -> dict[str, Any]:
    """The request that writes one body.

    **This function is the clean room.** It takes claims, a place, a city and a length,
    and there is no parameter through which `source_passage` or the copied body could
    be passed in. Nothing downstream can reintroduce them by accident, and a test reads
    the rendered payload back to prove it for real records.

    `words` is a single integer, and it is why the clean room is a room rather than a
    vacuum. Removing the old body removed the only thing telling the writer how long a
    beat is, and the first sample came back 1.8x longer — which eats the silence the
    tour is built around and stale-dates every stored duration. A word count carries no
    expression and no arrangement, so it crosses nothing the seam exists to stop.
    """
    return {
        "model": PRODUCER_MODEL,
        "max_tokens": MAX_TOKENS,
        "messages": [
            {
                "role": "user",
                "content": _WRITE_PROMPT.format(
                    poi=poi or "this place",
                    city=city or "the city",
                    voice=_VOICE_RULES,
                    claims=render_claims(claims),
                    words=words,
                ),
            }
        ],
    }


def body_redo_request(
    *, claims: list[dict[str, str]], poi: str, city: str, words: int, body: str, problems: str
) -> dict[str, Any]:
    """A second ask for a body that broke a voice rule.

    The clean room holds through the retry: the only thing added to the first request
    is the writer's OWN draft and the rule it broke. Naming a defect in prose the
    writer wrote is not showing it the source, which is why a voice rule can be
    corrected here and an eight-word run with the passage cannot.
    """
    base = cleanroom_request(claims=claims, poi=poi, city=city, words=words)
    return {
        **base,
        "messages": [
            base["messages"][0],
            {"role": "assistant", "content": body},
            {"role": "user", "content": _BODY_REDO_PROMPT.format(problems=problems)},
        ],
    }


def body_redo_problems(problems: dict[str, list[str]]) -> str:
    """What to tell the writer its draft got wrong, quoting the draft back to it."""
    said = {
        "first_person": (
            "These speak as a person. There is no guide walking beside the listener, so\n"
            'there is no "we" and no "I":'
        ),
        "apparatus": (
            "These hand the listener the guidebook's own furniture — a page, a step, a\n"
            "phone number, a web address. The listener has none of that:"
        ),
        "stage_direction": (
            "These tell the listener what to do, and no claim says so. If the claims do not\n"
            "say where the listener is standing or walking, you do not know:"
        ),
        "invented_impression": (
            "These offer the listener an impression, and not one claim is marked as an\n"
            "observation. There is no impression here to offer:"
        ),
    }
    parts = [
        said[kind] + "\n" + "\n".join(f"- {line}" for line in lines)
        for kind, lines in problems.items()
        if lines
    ]
    return "\n\n".join(parts)


def target_words(body_before: str) -> int:
    """How long the new body should be, from how long the old one was.

    The beat this replaces is the only honest statement of how much a listener is given
    at this stop; `beat_length_class` is a claim the extractor wrote and nothing
    re-checks, and Stage 0 found bodies outside their declared band. Rounded to the
    nearest ten, because a writer handed 137 will try to hit 137.
    """
    return max(MIN_TARGET_WORDS, round(len(body_before.split()) / 10) * 10)


def input_hash(claims: list[dict[str, str]]) -> str:
    """SHA-256 of the exact claim block the writer was given.

    The audit trail the brief asks for: a body whose claim block hashes differently
    from the one recorded was written from something else.
    """
    return hashlib.sha256(render_claims(claims).encode("utf-8")).hexdigest()


def cleanroom_record(
    entry: dict,
    *,
    body_before: str,
    given: list[dict[str, str]],
    body_after: str,
    attempts: int = 1,
) -> dict[str, Any]:
    """One regenerated candidate, carrying what the writer was actually given.

    `source_passage` and `body_before` are stored because a reviewer and the Stage 0
    gates both need them. They were not in the writer's input, and the test that
    proves it reads `cleanroom_request`, never this record.

    A body that breaks a voice rule carries `flags` and reaches a person; it is not
    refused. Refusing it would keep the copied guidebook body this stage exists to
    replace — trading an unfixable derivation defect for a fixable voice one — and
    `reauthor_review.review_order` already ranks a queue by that field.
    """
    source = entry.get("source_passage", "")
    problems = body_problems(body_after, given)
    return {
        "beat_id": entry.get("beat_id", ""),
        "poi_name": entry.get("poi_name", ""),
        "source_passage": source,
        "body_before": body_before,
        "body_after": body_after,
        "claims_given": given,
        "claims_sha256": input_hash(given),
        "shuffle_seed": shuffle_seed(entry.get("beat_id", "")),
        "ratio_before": round(verbatim_ratio(body_before, source), 3),
        "ratio_after": round(verbatim_ratio(body_after, source), 3),
        "run_after": max_verbatim_run(body_after, source),
        "body_problems": problems,
        "flags": sum(len(lines) for lines in problems.values()),
        "scene_details": scene_details(body_after, given),
        "attempts": attempts,
        "decomposed_by": entry.get("model", ""),
        "written_by": PRODUCER_MODEL,
        "generated_at": datetime.now(UTC).isoformat(),
    }


# ── files ────────────────────────────────────────────────────────────────────


def city_name(city_slug: str) -> str:
    """The city as a person writes it — "New York", not "new_york".

    Read from the registry that already owns the mapping rather than title-cased
    here, so the prompt says what the product says. A slug reaching the model as a
    slug is a prompt that reads like a database row.
    """
    return (load_registry().get(city_slug) or {}).get("display_name") or city_slug


def claims_path(city_slug: str, *, data_dir: Path | None = None) -> Path:
    root = data_dir if data_dir is not None else _REPO_ROOT / "data"
    return root / city_slug / "claims.json"


def cleanroom_path(city_slug: str, *, data_dir: Path | None = None) -> Path:
    root = data_dir if data_dir is not None else _REPO_ROOT / "data"
    return root / city_slug / "reauthored-cleanroom.json"


def load_json(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []


def save_json(path: Path, records: list[dict]) -> None:
    """Write atomically, so an interrupted flush cannot truncate a paid run."""
    staging = path.with_suffix(path.suffix + ".staging")
    staging.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(staging, path)


def copied_backlog(beats: list[dict]) -> list[dict]:
    """The beats this pipeline is for: copied, and with a source to decompose.

    The same selection `reauthor_run.pending_beats` makes, so both paths address one
    backlog rather than two different ones.
    """
    out = []
    for beat in beats:
        source = (beat.get("source_passage") or "").strip()
        if not source:
            continue
        if verbatim_ratio(beat.get("script_body") or "", source) < VERBATIM_THRESHOLD:
            continue
        out.append(beat)
    return out


# ── the run ──────────────────────────────────────────────────────────────────


def _text_of(response: Any) -> str:
    return "".join(getattr(b, "text", "") for b in (getattr(response, "content", []) or [])).strip()


def _decompose_one(beat: dict, client: Any) -> dict[str, Any]:
    """Decompose one passage, asking a second time when the gate refuses the first."""
    source = (beat.get("source_passage") or "").strip()
    response = client.messages.create(**decompose_request(source=source))
    claims = parse_claims(_text_of(response))
    if claims is None:
        record = claims_record(beat, claims=[])
        record["usable"] = False
        record["unreadable"] = True
        record["attempts"] = 1
        return record

    record = claims_record(beat, claims=claims)
    record["attempts"] = 1
    if record["usable"]:
        return record

    retried = client.messages.create(**redo_request(source=source, problems=redo_problems(record)))
    reworded = parse_claims(_text_of(retried))
    if reworded is None:
        return record
    second = claims_record(beat, claims=reworded)
    second["attempts"] = 2
    # A second ask can come back worse. Keeping it unconditionally would let a retry
    # spend money to degrade a set, so the one with fewer problems wins.
    kept = min((second, record), key=_problem_count)
    # Recorded rather than inferred from `attempts`: a set that was re-asked and whose
    # FIRST attempt won is indistinguishable from one never re-asked otherwise, and how
    # often the second ask actually helps is the thing worth knowing about it.
    kept["second_ask"] = True
    kept["second_ask_improved"] = kept is second
    return kept


def _problem_count(record: dict) -> int:
    """How much is wrong with a claim set, for choosing between two attempts."""
    return (
        len(record.get("lifted_claims") or [])
        + len(record.get("dangling_claims") or [])
        + len(record.get("apparatus_claims") or [])
        + len(record.get("uncovered_sentences") or [])
    )


def _write_one(entry: dict, bodies: dict[str, str], city: str, client: Any) -> dict[str, Any]:
    given = shuffled_claims(entry["claims"], entry["beat_id"])
    body_before = bodies.get(entry["beat_id"], "")
    ask = {
        "claims": given,
        "poi": entry.get("poi_name", ""),
        "city": city,
        "words": target_words(body_before),
    }
    first = cleanroom_record(
        entry,
        body_before=body_before,
        given=given,
        body_after=_text_of(client.messages.create(**cleanroom_request(**ask))),
    )
    if not first["flags"]:
        return first

    retried = client.messages.create(
        **body_redo_request(
            **ask,
            body=first["body_after"],
            problems=body_redo_problems(first["body_problems"]),
        )
    )
    second = cleanroom_record(
        entry,
        body_before=body_before,
        given=given,
        body_after=_text_of(retried),
        attempts=2,
    )
    kept = min((second, first), key=_body_problem_count)
    kept["attempts"] = 2
    kept["second_ask_improved"] = _body_problem_count(second) < _body_problem_count(first)
    return kept


def _body_problem_count(record: dict) -> int:
    """How many voice rules a written body broke, so the better of two drafts wins."""
    return record.get("flags", 0)


def _run(pool_size: int, work: list, task: Any, out: Path, records: list[dict]) -> None:
    """Drive a paid phase, flushing every record so a crash resumes rather than repays."""
    done = 0
    with ThreadPoolExecutor(max_workers=pool_size) as pool:
        for record in pool.map(task, work):
            records.append(record)
            done += 1
            save_json(out, records)
            if done % 25 == 0 or done == len(work):
                print(f"  {done}/{len(work)}")


def regrade(records: list[dict]) -> list[dict]:
    """Re-run the free gates over stored claim sets, so the gate is what decides.

    `usable` is written when a set is bought, and the gates have gained members since
    the first sets were. A stored flag would let a set graded by a weaker gate stay
    usable forever, which is how a refuted check survives its own replacement. The
    gates cost nothing, so they are re-run rather than trusted.
    """
    out = []
    for record in records:
        if not record.get("claims"):
            out.append(record)
            continue
        regraded = claims_record(
            {
                "beat_id": record.get("beat_id", ""),
                "poi_name": record.get("poi_name", ""),
                "source_passage": record.get("source_passage", ""),
            },
            claims=record["claims"],
        )
        regraded["attempts"] = record.get("attempts", 1)
        for field in ("second_ask", "second_ask_improved"):
            if field in record:
                regraded[field] = record[field]
        regraded["generated_at"] = record.get("generated_at", regraded["generated_at"])
        out.append(regraded)
    return out


def regrade_bodies(records: list[dict]) -> list[dict]:
    """Re-run the body gates over stored bodies, for the reason `regrade` re-runs the others.

    A body flagged before a gate existed would otherwise carry that gate's count forever.
    """
    out = []
    for record in records:
        body, given = record.get("body_after", ""), record.get("claims_given", [])
        problems = body_problems(body, given)
        out.append(
            {
                **record,
                "body_problems": problems,
                "flags": sum(len(lines) for lines in problems.values()),
                "scene_details": scene_details(body, given),
            }
        )
    return out


def _phase_decompose(city: str, limit: int, workers: int, client: Any) -> int:
    out = claims_path(city)
    records = regrade(load_json(out))
    save_json(out, records)
    # A set the gates now refuse is bought again, not kept: the alternative is writing
    # a beat from claims a current gate rejects.
    settled = {r["beat_id"] for r in records if r.get("beat_id") and r.get("usable")}
    records = [r for r in records if r.get("usable")]
    pending = [b for b in copied_backlog(load_city_beats(city)) if b.get("beat_id") not in settled]
    if limit:
        pending = pending[:limit]
    print(f"{city}: {len(records)} claim sets already bought, {len(pending)} to decompose now.")
    if not pending:
        return 0

    _run(workers, pending, lambda b: _decompose_one(b, client), out, records)

    refused = [r for r in records if not r.get("usable")]
    print(
        f"✓ {len(records)} claim sets in {out}\n"
        f"  {len(records) - len(refused)} usable, {len(refused)} refused\n"
        f"    {sum(1 for r in refused if r.get('unreadable'))} unreadable, "
        f"{sum(1 for r in refused if r.get('lifted_claims'))} lifted, "
        f"{sum(1 for r in refused if r.get('dangling_claims'))} dangling, "
        f"{sum(1 for r in refused if r.get('apparatus_claims'))} about the guidebook, "
        f"{sum(1 for r in refused if r.get('uncovered_sentences'))} miss source content\n"
        f"    {sum(1 for r in records if r.get('second_ask'))} were asked a second time, "
        f"of which {sum(1 for r in records if r.get('second_ask_improved'))} came back better"
    )
    return 0


def _phase_write(city: str, limit: int, workers: int, client: Any) -> int:
    # Re-graded here too: a write run started without a decompose run would otherwise
    # write from claim sets a current gate refuses.
    claims = [r for r in regrade(load_json(claims_path(city))) if r.get("usable")]
    if not claims:
        print(f"✗ no usable claim sets for {city} — run --phase decompose first.")
        return 1

    out = cleanroom_path(city)
    records = regrade_bodies(load_json(out))
    # A body is bound to the claim block it was written from. A second ask changes that
    # block, so a body written before it is prose nobody's current claims support — the
    # hash is what makes that visible instead of silently shipping the stale one.
    fresh = {input_hash(shuffled_claims(c["claims"], c["beat_id"])): c["beat_id"] for c in claims}
    kept, stale = [], 0
    for record in records:
        if fresh.get(record.get("claims_sha256", "")) == record.get("beat_id"):
            kept.append(record)
        else:
            stale += 1
    records = kept
    if stale:
        print(f"  {stale} stored bodies no longer match their claims and are rewritten")
    seen = {r["beat_id"] for r in records if r.get("beat_id")}
    pending = [c for c in claims if c["beat_id"] not in seen]
    if limit:
        pending = pending[:limit]
    bodies = {b.get("beat_id", ""): b.get("script_body") or "" for b in load_city_beats(city)}
    print(f"{city}: {len(records)} bodies already written, {len(pending)} to write now.")
    if not pending:
        return 0

    _run(
        workers,
        pending,
        lambda c: _write_one(c, bodies, city_name(city), client),
        out,
        records,
    )

    still_lifted = sum(1 for r in records if r.get("run_after", 0) >= VERBATIM_RUN_BLOCK)
    flagged = [r for r in records if r["flags"]]

    def broke(kind: str) -> int:
        return sum(1 for r in records if r["body_problems"][kind])

    print(
        f"✓ {len(records)} bodies in {out}, {len(flagged)} flagged for a person\n"
        f"    {broke('first_person')} speak as a person, "
        f"{broke('apparatus')} carry the guidebook, "
        f"{broke('stage_direction')} stage the listener, "
        f"{broke('invented_impression')} offer an impression no claim holds\n"
        f"    {sum(1 for r in records if r.get('attempts', 1) > 1)} were asked a second time, "
        f"of which {sum(1 for r in records if r.get('second_ask_improved'))} came back better\n"
        f"  {still_lifted} still share an {VERBATIM_RUN_BLOCK}+ word run with a source "
        f"the writer never saw\n"
        f"  {sum(1 for r in records if r.get('scene_details'))} put a thing in front of the "
        f"listener that no claim mentions — for review, not refused"
    )
    return 0


def render_dry_run(beats: list[dict], city: str) -> str:
    """Both payloads for real beats, bought with nothing.

    The claim set is not known until phase 1 has been paid for, so the writer payload
    is rendered from each beat's stored `key_claims` as a stand-in. That is the wrong
    claim set — it is the lossy one this pipeline exists to replace — and it is the
    right shape, which is what a reader needs to see before approving a paid run.
    """
    out: list[str] = []
    for beat in beats:
        stand_in = [
            {"claim": str(c).strip(), "kind": "fact"}
            for c in (beat.get("key_claims") or [])
            if str(c).strip()
        ]
        out.append("=" * 78)
        out.append(f"{beat.get('beat_id', '')}  ·  {beat.get('poi_name', '')}")
        out.append("")
        out.append("-- SOURCE (phase 1 reads this; the writer never does) " + "-" * 24)
        out.append(beat.get("source_passage", ""))
        out.append("")
        out.append("-- PHASE 1 REQUEST " + "-" * 59)
        out.append(
            decompose_request(source=beat.get("source_passage", ""))["messages"][0]["content"]
        )
        out.append("")
        out.append(
            f"-- PHASE 2 REQUEST (claims stood in from key_claims: {len(stand_in)}) " + "-" * 20
        )
        if not stand_in:
            out.append("  this beat stores no key_claims, so there is nothing to stand in")
        else:
            given = shuffled_claims(stand_in, beat.get("beat_id", ""))
            out.append(
                cleanroom_request(
                    claims=given,
                    poi=beat.get("poi_name", ""),
                    city=city,
                    words=target_words(beat.get("script_body") or ""),
                )["messages"][0]["content"]
            )
        out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--city", default="paris", help="City slug under data/.")
    parser.add_argument("--phase", choices=("decompose", "write"), required=True)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print both payloads for real beats and spend nothing.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Stop after N beats (0 = all).")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    args = parser.parse_args(argv)

    if is_excluded(args.city):
        print(f"✗ {args.city} is excluded: its beats are junk data, not a backlog.")
        return 2
    if args.dry_run:
        backlog = copied_backlog(load_city_beats(args.city))
        print(render_dry_run(backlog[: args.limit or 2], city_name(args.city)))
        print(f"[DRY RUN] nothing was bought; {len(backlog)} beats are in the backlog.")
        return 0
    if os.getenv("ONDOWAY_DEMO_APPROVE") != "1":
        print("REFUSED: this run spends money; set ONDOWAY_DEMO_APPROVE=1.", file=sys.stderr)
        return 3

    from dotenv import load_dotenv

    from src.tour.anthropic_client import compose_client

    load_dotenv()
    client = compose_client()
    if args.phase == "decompose":
        return _phase_decompose(args.city, args.limit, args.workers, client)
    return _phase_write(args.city, args.limit, args.workers, client)


if __name__ == "__main__":
    raise SystemExit(main())
