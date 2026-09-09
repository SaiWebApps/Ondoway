"""Show what Lane B would rewrite, before anything is rewritten.

246 Paris beats and 278 New York ones are bodies lifted out of their source
rather than authored from it. Fixing that costs money and changes the corpus, so
this exists to let the owner READ the work first: which beats, how copied,
against what source. London is excluded — see `EXCLUDED_CITIES`.

**It never writes.** The dry run is free and touches no provider. `--live`
re-authors a small sample and is gated on `ONDOWAY_DEMO_APPROVE=1`, the same
approval this repo already puts in front of paid runs — and even then it prints
the rewrite rather than committing it.

Re-grounding reuses `HaikuFaithfulnessChecker` (`src/tour/verify.py`), the gate
calibrated to zero fabricating acceptances. The standard is explicit that a
second entailment gate must not be built.

Run as `uv run python scripts/reauthor_preview.py --city paris`.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Any

from scripts.corpus_report import (
    load_city_beats,
)
from scripts.verbatim import (
    VERBATIM_THRESHOLD,
    verbatim_ratio,
)

#: How many beats a preview shows. A sample, never the backlog.
DEFAULT_LIMIT = 5

#: Cities whose beats are junk rather than a backlog. London was produced by the
#: automated onboarding drafter, not the book pipeline: 561 single-sentence spans
#: lifted verbatim from Wikipedia, every one tagged the same lens and the same
#: length class, none fact-checked. Rewriting it would launder junk into
#: plausible-sounding junk, so it is excluded from the work rather than queued.
EXCLUDED_CITIES = frozenset({"london"})

#: The model that WRITES. Accuracy and non-plagiarism are the requirement, and the
#: whole remaining backlog costs about $11 to rewrite, so there is no cost argument
#: for a weaker one. Note this is deliberately NOT the model that JUDGES: the
#: entailment gate is calibrated on Haiku to zero fabricating acceptances, and
#: moving it to another model would throw that calibration away.
REAUTHOR_MODEL = "claude-opus-5"

#: Adaptive thinking is on by default on this model and its tokens count against
#: max_tokens, so a beat-sized ceiling would truncate the rewrite mid-sentence.
REAUTHOR_MAX_TOKENS = 8000

#: The house voice the rewrite must land in, quoted from
#: `fixtures/tour-quality-standard/01-standard.md` §3 so the prompt and the
#: standard cannot drift apart silently.
_VOICE_RULES = """P1 Say it; don't circle it — state the fact, no portentous staging.
P2 Name what the listener would be told — never "a famous queen" when the source says
   Marie-Antoinette.
P3 Say it once — no restatement, no summary that repackages what just landed.
P4 No metadata laundering — "historians say" to make an unsourced claim feel earned is
   fabrication.
P5 Not everything is symbolic — a detail given as a detail stays a detail.
P6 Concrete over summary; short declaratives; one metaphor at most, then move on."""

_REAUTHOR_PROMPT = """Rewrite this audio-tour beat in your own words.

Every fact must come from the SOURCE below. Add nothing. Drop nothing that matters.
The rewrite must say the same things as the ORIGINAL while sharing none of its phrasing —
it is currently lifted from a copyrighted guidebook, and that is what you are fixing.

House voice:
{voice}

SOURCE (the only facts you may use):
{source}

ORIGINAL (copied; do not reuse its wording):
{body}

Write the replacement body only. No preamble, no quotes around it."""


def is_excluded(city_slug: str) -> bool:
    """Whether this city's beats are junk that must not be rewritten."""
    return city_slug.lower() in EXCLUDED_CITIES


def reauthor_request(*, source: str, body: str) -> dict[str, Any]:
    """The request that rewrites one beat, built here so a test can pin its shape.

    Carries no `temperature`, `top_p` or `top_k`: sampling parameters are removed
    on this model and sending one returns a 400, which would otherwise be
    discovered by a failed paid call rather than by the suite.
    """
    return {
        "model": REAUTHOR_MODEL,
        "max_tokens": REAUTHOR_MAX_TOKENS,
        "messages": [
            {
                "role": "user",
                "content": _REAUTHOR_PROMPT.format(voice=_VOICE_RULES, source=source, body=body),
            }
        ],
    }


def worst_copied(beats: list[dict], limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """The most-copied beats first, capped at `limit`.

    A beat with no `source_passage` is skipped rather than ranked: nothing can be
    said about how copied it is, and Lane B has no source to re-ground it against.
    That is the untraceable defect, which is a different queue.
    """
    rows: list[dict[str, Any]] = []
    for beat in beats:
        source = (beat.get("source_passage") or "").strip()
        if not source:
            continue
        ratio = verbatim_ratio(beat.get("script_body") or "", source)
        if ratio < VERBATIM_THRESHOLD:
            continue
        rows.append(
            {
                "beat_id": beat.get("beat_id", ""),
                "poi_name": beat.get("poi_name", ""),
                "ratio": ratio,
                "script_body": beat.get("script_body") or "",
                "source_passage": source,
                "claims": grounding_claims(beat),
            }
        )
    rows.sort(key=lambda r: (-r["ratio"], r["beat_id"]))
    return rows[:limit]


def grounding_claims(beat: dict) -> tuple[str, ...]:
    """The fact set a rewrite is allowed to draw on: the source passage, then key_claims.

    The source passage leads because it is what the prompt licenses — "every fact
    must come from the SOURCE" — and because `key_claims` is a lossy summary of
    it. Measured: Bois de Vincennes' source says "the breezes are bracing" and its
    single claim does not, and Rue Cler's source makes a Michelin comparison its
    two claims omit. Grounding on claims alone refused both faithful rewrites for
    covering material that was in the source all along.

    `key_claims` is still appended: it carries framing the passage does not, such
    as a claim marked as the author's observation rather than a fact.
    """
    source = (beat.get("source_passage") or "").strip()
    sentences = tuple(s.strip() for s in re.split(r"(?<=[.!?])\s+", source) if s.strip())
    claims = tuple(str(c).strip() for c in (beat.get("key_claims") or []) if str(c).strip())
    seen: set[str] = set()
    return tuple(c for c in sentences + claims if not (c in seen or seen.add(c)))


def render_preview(city_slug: str, rows: list[dict[str, Any]]) -> str:
    """The dry preview: what would be rewritten, and against what."""
    if not rows:
        return f"{city_slug}: no beat is at or above the copied threshold."
    out = [
        f"{city_slug} — {len(rows)} beats that would be re-authored "
        f"(most copied first; nothing is written)"
    ]
    for row in rows:
        out.append("")
        out.append(f"  {row['beat_id']}  ·  {row['poi_name']}  ·  {row['ratio'] * 100:.0f}% copied")
        out.append(f"    NOW    {_wrap(row['script_body'])}")
        out.append(f"    SOURCE {_wrap(row['source_passage'])}")
        out.append(f"    GROUND {len(row['claims'])} claim(s) the rewrite must still entail")
    return "\n".join(out)


#: Abbreviations whose trailing period does not end a sentence. Guidebook prose is
#: full of them — "No. 46", "St. Antoine" — and splitting there hands the gate a
#: fragment that cannot entail, which reads as a refusal the writer never earned.
_ABBREVIATIONS = ("No", "no", "St", "Ste", "Mt", "Ave", "Blvd", "Rd", "vs", "etc", "cf")

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\u00C0-\u00DC\u201C\"])")


def sentences(text: str) -> list[str]:
    """Split a rewrite into the units the entailment gate was calibrated on."""
    guarded = text.strip()
    for abbr in _ABBREVIATIONS:
        guarded = guarded.replace(f"{abbr}. ", f"{abbr}\u0000 ")
    parts = _SENTENCE_SPLIT.split(guarded)
    return [p.replace("\u0000", ".").strip() for p in parts if p.strip()]


def _flat(text: str) -> str:
    """Whitespace-collapsed but UNtruncated — the live output is read, not skimmed."""
    return re.sub(r"\s+", " ", text).strip()


def _wrap(text: str, width: int = 88) -> str:
    """One-line-per-field with a hard cap, so two bodies stay comparable by eye."""
    flat = re.sub(r"\s+", " ", text).strip()
    return flat if len(flat) <= width else flat[: width - 1] + "…"


def _reauthor_live(rows: list[dict[str, Any]]) -> str:
    """Re-author the sample and re-ground it. Paid; never writes to data/."""
    from dotenv import load_dotenv

    from src.tour.anthropic_client import compose_client
    from src.tour.verify import HaikuFaithfulnessChecker

    # The credential lives in .env, as it does for the sibling paid script
    # scripts/coverage_calibrate.py. Loaded here rather than at import time so the
    # dry run stays free of any environment expectation at all.
    load_dotenv()

    # compose_client, not judge_client: the judge ceiling is 45s, sized for a
    # one-token Haiku verdict, and this is an Opus write with adaptive thinking.
    # It also retries zero times, which is what a paid authoring call wants.
    client = compose_client()
    checker = HaikuFaithfulnessChecker()
    out: list[str] = []
    for row in rows:
        response = client.messages.create(
            **reauthor_request(source=row["source_passage"], body=row["script_body"])
        )
        rewritten = "".join(
            getattr(b, "text", "") for b in (getattr(response, "content", []) or [])
        ).strip()
        copied_now = verbatim_ratio(rewritten, row["source_passage"])
        # The gate is one-call-per-SENTENCE by construction (see its docstring).
        # Handing it a whole paragraph asks a strict checker one question about
        # three clauses at once, and it answers NO on prose that traces fully.
        ungrounded = [
            sentence
            for sentence in sentences(rewritten)
            if not checker.entails(row["claims"], sentence)
        ]
        entailed = (not ungrounded) if row["claims"] else None
        out.append("")
        out.append(f"  {row['poi_name']}  ({row['beat_id']})")
        out.append(f"    BEFORE [{row['ratio'] * 100:.0f}% copied]  {_flat(row['script_body'])}")
        out.append(f"    AFTER  [{copied_now * 100:.0f}% copied]  {_flat(rewritten)}")
        for claim in row["claims"]:
            out.append(f"    CLAIM  {_flat(claim)}")
        if entailed is None:
            verdict = "unchecked (no claims to ground against)"
        elif entailed:
            verdict = "yes"
        else:
            verdict = f"NO — {len(ungrounded)} sentence(s) not supported:"
        out.append(f"    GROUNDED {verdict}")
        for sentence in ungrounded:
            out.append(f"      ungrounded: {_flat(sentence)}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--city", default="paris", help="City slug under data/ (default paris).")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Beats to preview.")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Actually re-author the sample (paid). Needs ONDOWAY_DEMO_APPROVE=1.",
    )
    args = parser.parse_args(argv)

    if is_excluded(args.city):
        print(
            f"✗ {args.city} is excluded: its beats are junk data, not a backlog. "
            "Rewriting them would launder junk into plausible-sounding junk."
        )
        return 2

    try:
        beats = load_city_beats(args.city)
    except FileNotFoundError as exc:
        print(f"✗ {exc}")
        return 1

    rows = worst_copied(beats, limit=args.limit)
    print(render_preview(args.city, rows))

    if not args.live:
        print("\n[DRY RUN] --live with ONDOWAY_DEMO_APPROVE=1 re-authors this sample.")
        return 0
    if os.getenv("ONDOWAY_DEMO_APPROVE") != "1":
        print("REFUSED: --live needs ONDOWAY_DEMO_APPROVE=1.", file=sys.stderr)
        return 3
    if not rows:
        return 0
    print("\nre-authored (printed, never written):")
    print(_reauthor_live(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
