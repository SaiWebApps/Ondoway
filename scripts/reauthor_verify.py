"""Verify re-authored beats automatically; escalate only what cannot be resolved.

The design's D1 is "throughput + triage, not blanket verification", and its target
was a queue of tens, not hundreds. So the machine decides, and a person sees only
the cases where the machine genuinely cannot.

Three questions, which is what a re-author actually needs asked:

1. does the rewrite ADD a fact the source does not carry (fabrication);
2. does it DROP a fact the original carried (loss);
3. is it still too close to the source (already measured mechanically by
   `verbatim_ratio` — every candidate passes, so it is not re-asked here).

The first two go to **two different models**. One model checking its own work is
not independence, and the entailment gate used for compose is the wrong
instrument here: it was calibrated to judge a sentence built up FROM claims, and
a re-author does the reverse, so it refuses good paraphrase by construction.

Every uncertainty resolves toward the human: an added fact, a dropped fact, a
disagreement between the models, or an answer that could not be parsed all
escalate. A rewrite passes only when both models independently find nothing.

Run as:
    ONDOWAY_DEMO_APPROVE=1 uv run python scripts/reauthor_verify.py --city paris
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from scripts.reauthor_review import decision_summary, load_candidates, save_candidates

#: Two independent judges. Different models, so agreement means something.
VERIFY_MODELS: tuple[str, str] = ("claude-opus-5", "claude-sonnet-5")

#: Thinking tokens count against this ceiling on both models.
VERIFY_MAX_TOKENS = 4000

PASS = "pass"
ESCALATE = "escalate"

_VERIFY_PROMPT = """You are checking whether a rewritten travel-guide passage stayed \
faithful to its source.

KNOWN CONTEXT (not claims — the passage is a tour beat about this place, and may name
it, its city, and that city's inhabitants freely):
  place: {poi}
  city:  {city}

SOURCE (the only facts that may appear):
{source}

ORIGINAL (the earlier wording, for reference on what was being said):
{before}

REWRITE (what you are checking):
{after}

Answer two questions about the REWRITE:

1. added_facts — a specific, checkable claim in the REWRITE that the SOURCE does not
support AND that would MISLEAD a visitor if it were wrong: a date, a number, a name, an
attribution, or a statement about what stands where. Report only material additions.
Do NOT report: naming the place or its city (see KNOWN CONTEXT), rewording a general
term into a natural one ("the city" -> "Paris", "tooth-pullers" -> "men who pulled
teeth"), reordering, compression, or ordinary connective phrasing.
2. dropped_facts — any specific, checkable claim in the ORIGINAL that the REWRITE lost.

Reply with JSON only, no prose:
{{"added_facts": ["..."], "dropped_facts": ["..."]}}
Use empty lists when there is nothing to report."""


def verify_request(
    *, model: str, source: str, before: str, after: str,
    poi: str = "", city: str = "",
) -> dict[str, Any]:
    """One judge's request. No sampling parameters: they are 400s on these models."""
    return {
        "model": model,
        "max_tokens": VERIFY_MAX_TOKENS,
        "messages": [
            {
                "role": "user",
                "content": _VERIFY_PROMPT.format(
                    poi=poi or "unknown", city=city or "unknown",
                    source=source, before=before, after=after,
                ),
            }
        ],
    }


def parse_verdict(text: str) -> dict[str, list[str]] | None:
    """Read a judge's answer, or None when it cannot be read.

    Models fence JSON in prose. That is a formatting habit, not a refusal, so the
    first well-formed object carrying both fields is accepted. Anything else
    returns None and escalates — an unparsed judge has cleared nothing.
    """
    for candidate in re.findall(r"\{.*?\}", text or "", flags=re.S):
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if not isinstance(parsed, dict):
            continue
        if "added_facts" in parsed and "dropped_facts" in parsed:
            return {
                "added_facts": [str(x) for x in (parsed.get("added_facts") or [])],
                "dropped_facts": [str(x) for x in (parsed.get("dropped_facts") or [])],
            }
    return None


def reconcile(verdicts: list[dict[str, list[str]] | None]) -> dict[str, Any]:
    """Turn the judges' answers into a decision, resolving every doubt upward.

    A rewrite passes only when every judge was readable and every one of them
    found nothing. One judge reporting an added or dropped fact is enough to
    escalate: the cost of a person reading a good rewrite is a minute, and the
    cost of shipping a fabricated one is the product's credibility.
    """
    if any(v is None for v in verdicts):
        return {"status": ESCALATE, "reason": "a judge's answer could not be read"}

    added = [f for v in verdicts for f in v["added_facts"]]
    dropped = [f for v in verdicts for f in v["dropped_facts"]]
    if not added and not dropped:
        return {"status": PASS, "reason": ""}

    parts = []
    if added:
        parts.append("adds: " + "; ".join(dict.fromkeys(added)))
    if dropped:
        parts.append("drops: " + "; ".join(dict.fromkeys(dropped)))
    return {"status": ESCALATE, "reason": " | ".join(parts)}


def _verify_one(row: dict, client: Any, city: str = "") -> dict[str, Any]:
    verdicts: list[dict[str, list[str]] | None] = []
    for model in VERIFY_MODELS:
        response = client.messages.create(
            **verify_request(
                model=model,
                source=row.get("source_passage", ""),
                before=row.get("body_before", ""),
                after=row.get("body_after", ""),
                poi=row.get("poi_name", ""),
                city=city,
            )
        )
        text = "".join(
            getattr(b, "text", "") for b in (getattr(response, "content", []) or [])
        )
        verdicts.append(parse_verdict(text))

    outcome = reconcile(verdicts)
    row["verified"] = {
        "status": outcome["status"],
        "reason": outcome["reason"],
        "models": list(VERIFY_MODELS),
        "verified_at": datetime.now(UTC).isoformat(),
    }
    if outcome["status"] == PASS:
        # The machine resolved it. Recorded as such, and attributed to the machine
        # rather than to a person who never saw it.
        row["decision"] = "approve"
        row["decided_by"] = f"auto:{'+'.join(VERIFY_MODELS)}"
        row["decided_at"] = row["verified"]["verified_at"]
        from scripts.reauthor_review import body_hash

        row["decided_body_hash"] = body_hash(row.get("body_after") or "")
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--city", default="paris")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args(argv)

    if os.getenv("ONDOWAY_DEMO_APPROVE") != "1":
        print("REFUSED: this run spends money; set ONDOWAY_DEMO_APPROVE=1.", file=sys.stderr)
        return 3

    from dotenv import load_dotenv

    from src.tour.anthropic_client import compose_client

    load_dotenv()

    records = load_candidates(args.city)
    pending = [r for r in records if not r.get("verified")]
    if args.limit:
        pending = pending[: args.limit]
    print(f"{args.city}: {len(records)} candidates, {len(pending)} to verify now.")
    if not pending:
        return 0

    client = compose_client()
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for _ in pool.map(lambda r: _verify_one(r, client, args.city), pending):
            done += 1
            save_candidates(args.city, records)
            if done % 25 == 0 or done == len(pending):
                print(f"  {done}/{len(pending)}")

    escalated = [r for r in records if (r.get("verified") or {}).get("status") == ESCALATE]
    summary = decision_summary(records)
    print(
        f"✓ {summary['approved']} auto-approved, {len(escalated)} escalated for a person\n"
        f"  the human queue is now {len(escalated)} of {len(records)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
