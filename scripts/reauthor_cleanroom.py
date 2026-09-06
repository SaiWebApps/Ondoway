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
    verbatim_ratio,
)
from scripts.reauthor_preview import _VOICE_RULES, is_excluded
from src.city_registry import load_registry
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

Use your own wording. Do not reuse the passage's phrasing: a claim that repeats eight
words of the passage in a row is a lifted claim and will be rejected.

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

_WRITE_PROMPT = """Write the body of an audio-tour beat about {poi}, in {city}.

Below is everything you know. It is an unordered set — the order means nothing, and
you decide what to say first, what belongs together, and what closes. Say every claim
marked "fact".

Claims marked "observation" are one writer's impression, not a record. Offer one as
something a person standing here may notice, or leave it out. Never state one as
established fact, and never hand it to somebody else to say: "some visitors find",
"people say", "it is often remarked" invents a source and is the worst thing you can do
here. Do not say "I".

Add nothing. Not a fact, and not a physical detail either — if the claims do not say
the roadway widens, the roadway does not widen, and you cannot tell the listener to
look at it. Never write "imagine" or "picture".

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


def dangling_claims(claims: list[dict[str, str]]) -> list[str]:
    """Claims that point at a neighbour they will not have once the set is shuffled."""
    return [c["claim"] for c in claims if _ANAPHOR.match(c["claim"])]


def uncovered_sentences(claims: list[dict[str, str]], source: str) -> list[str]:
    """Source sentences no claim carries — content that would leave the corpus silently.

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
    uncovered = uncovered_sentences(claims, source) if claims else []
    return {
        "beat_id": beat.get("beat_id", ""),
        "poi_name": beat.get("poi_name", ""),
        "source_passage": source,
        "claims": claims,
        "lifted_claims": lifted,
        "dangling_claims": dangling,
        "uncovered_sentences": uncovered,
        "usable": not (lifted or dangling or uncovered),
        "model": PRODUCER_MODEL,
        "generated_at": datetime.now(UTC).isoformat(),
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
    entry: dict, *, body_before: str, given: list[dict[str, str]], body_after: str
) -> dict[str, Any]:
    """One regenerated candidate, carrying what the writer was actually given.

    `source_passage` and `body_before` are stored because a reviewer and the Stage 0
    gates both need them. They were not in the writer's input, and the test that
    proves it reads `cleanroom_request`, never this record.
    """
    source = entry.get("source_passage", "")
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
    return min((second, record), key=_problem_count)


def _problem_count(record: dict) -> int:
    """How much is wrong with a claim set, for choosing between two attempts."""
    return (
        len(record.get("lifted_claims") or [])
        + len(record.get("dangling_claims") or [])
        + len(record.get("uncovered_sentences") or [])
    )


def _write_one(entry: dict, bodies: dict[str, str], city: str, client: Any) -> dict[str, Any]:
    given = shuffled_claims(entry["claims"], entry["beat_id"])
    response = client.messages.create(
        **cleanroom_request(
            claims=given,
            poi=entry.get("poi_name", ""),
            city=city,
            words=target_words(bodies.get(entry["beat_id"], "")),
        )
    )
    return cleanroom_record(
        entry,
        body_before=bodies.get(entry["beat_id"], ""),
        given=given,
        body_after=_text_of(response),
    )


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
        regraded["generated_at"] = record.get("generated_at", regraded["generated_at"])
        out.append(regraded)
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
        f"{sum(1 for r in refused if r.get('uncovered_sentences'))} miss source content\n"
        f"    {sum(1 for r in records if r.get('attempts') == 2)} needed a second ask"
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
    records = load_json(out)
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
    print(
        f"✓ {len(records)} bodies in {out}\n"
        f"  {still_lifted} still share an {VERBATIM_RUN_BLOCK}+ word run with a source "
        f"the writer never saw"
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
