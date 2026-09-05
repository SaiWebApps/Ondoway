"""Show what Lane B would rewrite, before anything is rewritten.

23% of the New York corpus and 99% of London's are bodies lifted out of their
source rather than authored from it. Fixing that means rewriting roughly a
thousand beats, which costs money and changes the corpus, so this exists to let
the owner READ the work first: which beats, how copied, against what source.

**It never writes.** The dry run is free and touches no provider. `--live`
re-authors a small sample and is gated on `ONDOWAY_DEMO_APPROVE=1`, the same
approval this repo already puts in front of paid runs — and even then it prints
the rewrite rather than committing it.

Re-grounding reuses `HaikuFaithfulnessChecker` (`src/tour/verify.py`), the gate
calibrated to zero fabricating acceptances. The standard is explicit that a
second entailment gate must not be built.

Run as `uv run python scripts/reauthor_preview.py --city london`.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Any

from scripts.corpus_report import (
    VERBATIM_THRESHOLD,
    load_city_beats,
    verbatim_ratio,
)

#: How many beats a preview shows. A sample, never the backlog.
DEFAULT_LIMIT = 5

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
    """What a rewrite of this beat must still entail.

    `key_claims` is the right answer where the extractor recorded it. London's
    553 copied beats carry none, so the source passage is split into sentences
    and used instead — the calibrated gate takes claims, and skipping the beats
    that have none would exempt the entire city Lane B exists for.
    """
    claims = tuple(c for c in (beat.get("key_claims") or []) if str(c).strip())
    if claims:
        return claims
    source = (beat.get("source_passage") or "").strip()
    if not source:
        return ()
    return tuple(s.strip() for s in re.split(r"(?<=[.!?])\s+", source) if s.strip())


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


def _wrap(text: str, width: int = 88) -> str:
    """One-line-per-field with a hard cap, so two bodies stay comparable by eye."""
    flat = re.sub(r"\s+", " ", text).strip()
    return flat if len(flat) <= width else flat[: width - 1] + "…"


def _reauthor_live(rows: list[dict[str, Any]]) -> str:
    """Re-author the sample and re-ground it. Paid; never writes to data/."""
    from src.tour.anthropic_client import judge_client
    from src.tour.verify import FAITHFULNESS_MODEL, HaikuFaithfulnessChecker

    client = judge_client()
    checker = HaikuFaithfulnessChecker()
    out: list[str] = []
    for row in rows:
        prompt = _REAUTHOR_PROMPT.format(
            voice=_VOICE_RULES, source=row["source_passage"], body=row["script_body"]
        )
        response = client.messages.create(
            model=FAITHFULNESS_MODEL,
            max_tokens=600,
            temperature=1,
            messages=[{"role": "user", "content": prompt}],
        )
        rewritten = "".join(
            getattr(b, "text", "") for b in (getattr(response, "content", []) or [])
        ).strip()
        copied_now = verbatim_ratio(rewritten, row["source_passage"])
        entailed = checker.entails(row["claims"], rewritten) if row["claims"] else None
        out.append("")
        out.append(f"  {row['beat_id']}  ·  {row['poi_name']}")
        out.append(f"    BEFORE  {_wrap(row['script_body'])}  [{row['ratio'] * 100:.0f}% copied]")
        out.append(f"    AFTER   {_wrap(rewritten)}  [{copied_now * 100:.0f}% copied]")
        if entailed is None:
            verdict = "unchecked (no claims to ground against)"
        else:
            verdict = "yes" if entailed else "NO — this rewrite would be refused"
        out.append(f"    GROUNDED {verdict}")
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
