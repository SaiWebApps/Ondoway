"""Read a regenerated body back into claims, and diff those against what it was given.

No free check separates an invented true detail from an invented false one. "A
profusion of coloured marble" became "reds, greens, whites", and every deterministic
gate in `reauthor_cleanroom.py` passes that body: the colours break no voice rule, copy
no source phrasing, and name nothing a listener sees. A model can see it. This is how.

**The audit is asymmetric, and that is the whole design.** A model asked "is this body
supported by these claims?" agrees, because agreement is the fluent answer. So it is
never asked. It reads the body ALONE into claims — it does not see the claims the writer
was given, and cannot be anchored by them — and code then diffs the two sets. The model
does the part it is good at, reading prose into propositions; code does the part it is
good at, comparing two lists. A body claim matching nothing it was given is a sentence
the pipeline cannot account for.

**The auditor is not the producer.** `reauthor_cleanroom.PRODUCER_MODEL` both decomposed
and wrote this corpus, and a model does not flag what it invented itself. The excluded
model is read from the record rather than named here, so the exclusion is evidence.

**It checks traceability, not truth.** A body claim matching nothing means no claim
supports it. Whether it is true of the world is a different question and a different
tool; the pipeline's contract is that every sentence traces to a claim, and an invention
that happens to be true still breaks it.

Paid, and refuses to run without `ONDOWAY_DEMO_APPROVE=1`. Writes nothing to `beats.json`
and nothing to the corpus — the verdicts land beside the bodies they judge.

Run as:
    ONDOWAY_DEMO_APPROVE=1 uv run python scripts/reauthor_audit.py --city paris --limit 12
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from scripts.reauthor_cleanroom import (
    DEFAULT_WORKERS,
    MAX_TOKENS,
    PRODUCER_MODEL,
    city_name,
    cleanroom_path,
    load_json,
    parse_claims,
    save_json,
)
from src.tour.anthropic_client import certification_judge_client

#: The auditing model. It must not be `PRODUCER_MODEL`, and the check that it is not
#: reads the producer from each record rather than trusting this constant.
AUDITOR_MODEL = "claude-sonnet-5"

_AUDIT_PROMPT = """Read this audio-tour beat and list every distinct thing it asserts.

One claim per assertion, in your own words, each standing on its own. Include the small
things: a colour, a material, a number, a time of day, a direction, what a person is
said to be doing. Those are the assertions that carry the most and get read past.

An impression the text offers rather than asserts — "you may find it grand" — is a claim
too. Mark it as an impression. Everything else is an assertion.

Do not judge the text, do not say whether it is good, and do not leave anything out
because it seems obvious.

BEAT (about {poi}, in {city}):
{body}

Reply with JSON only, no prose:
{{"claims": [{{"claim": "...", "kind": "assertion"}}]}}"""


_MATCH_PROMPT = """Below are the FACTS a writer was given, and the STATEMENTS someone
found in the beat it wrote. For each statement, say which facts state it.

This is a matching task, not a judgement. Do not decide whether a statement is good,
fair or reasonable — only whether the facts say it. Several facts may combine to state
one statement; give all their numbers. If nothing in the list says it, give an empty
list, and that is the expected answer for a statement the facts simply do not contain.

Different words for the same thing are the same thing: "erected" matches "built",
"located at" matches "stands at". A detail the facts do not carry is NOT a match even
when the rest of the sentence matches — if the facts say "coloured marble" and the
statement says "red and green marble", the colours are not in the facts.

FACTS:
{given}

STATEMENTS:
{statements}

Reply with JSON only, no prose:
{{"matches": [{{"statement": 1, "facts": [3, 7]}}]}}"""


def match_request(
    *, read_back: list[dict[str, str]], given: list[dict[str, str]]
) -> dict[str, Any]:
    """The request that matches what a body says to what its writer was given.

    This one sees both sides, and that is safe because the EXTRACTION was blind: the
    statements were read out of the body by a model that had never seen the claims, so
    nothing here can put a fact into the body that was not already there. What it can do
    is anchor a judgement, which is why it is not asked for one. "Which of these states
    it, or none" has a wrong answer a reader can check against the numbered list; "is
    this supported?" has only an agreeable one.
    """
    return {
        "model": AUDITOR_MODEL,
        "max_tokens": MAX_TOKENS,
        "messages": [
            {
                "role": "user",
                "content": _MATCH_PROMPT.format(
                    given="\n".join(f"{i + 1}. {c.get('claim', '')}" for i, c in enumerate(given)),
                    statements="\n".join(
                        f"{i + 1}. {c.get('claim', '')}" for i, c in enumerate(read_back)
                    ),
                ),
            }
        ],
    }


def parse_matches(text: str) -> dict[int, list[int]] | None:
    """`{statement index: fact indices}` from the matcher's reply, or None if unreadable."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    rows = payload.get("matches")
    if not isinstance(rows, list):
        return None
    out: dict[int, list[int]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("statement"), int):
            continue
        facts = row.get("facts")
        out[row["statement"]] = (
            [n for n in facts if isinstance(n, int)] if isinstance(facts, list) else []
        )
    return out


def audit_request(*, body: str, poi: str, city: str) -> dict[str, Any]:
    """The request that reads one body back into claims.

    **This function is the asymmetry.** It takes the body, a place and a city. There is
    no parameter through which the claims the writer was given could arrive, so the
    auditor cannot read the body in their light — the same structural argument the clean
    room makes about the source, applied to the check rather than the writer.
    """
    return {
        "model": AUDITOR_MODEL,
        "max_tokens": MAX_TOKENS,
        "messages": [
            {
                "role": "user",
                "content": _AUDIT_PROMPT.format(poi=poi or "this place", city=city, body=body),
            }
        ],
    }


def unsupported_claims(
    read_back: list[dict[str, str]], matches: dict[int, list[int]] | None
) -> list[dict[str, Any]]:
    """Statements the matcher could name no fact for.

    A missing statement number is NOT read as unsupported. The matcher answering about
    nine statements when it was given ten is a matcher that lost one, and reading
    silence as a finding manufactures the defect this exists to detect.
    """
    if matches is None:
        return []
    return [
        {"claim": claim.get("claim", ""), "kind": claim.get("kind", "assertion")}
        for index, claim in enumerate(read_back, start=1)
        if index in matches and not matches[index]
    ]


def unmatched_statements(read_back: list[dict[str, str]], matches: dict | None) -> int:
    """Statements the matcher never answered about, which are not findings."""
    if matches is None:
        return len(read_back)
    return sum(1 for i in range(1, len(read_back) + 1) if i not in matches)


def audit_record(
    record: dict,
    *,
    read_back: list[dict[str, str]] | None,
    matches: dict[int, list[int]] | None,
) -> dict[str, Any]:
    """One body's verdict, carrying what it says, who judged it, and who may not have."""
    unsupported = unsupported_claims(read_back or [], matches)
    return {
        "beat_id": record.get("beat_id", ""),
        "poi_name": record.get("poi_name", ""),
        "body_after": record.get("body_after", ""),
        "claims_given": record.get("claims_given") or [],
        "claims_read_back": read_back,
        "unreadable": read_back is None or matches is None,
        "matches": {str(k): v for k, v in (matches or {}).items()},
        "unanswered_statements": unmatched_statements(read_back or [], matches),
        "unsupported_claims": unsupported,
        "unsupported_count": len(unsupported),
        "written_by": record.get("written_by", ""),
        "decomposed_by": record.get("decomposed_by", ""),
        "audited_by": AUDITOR_MODEL,
        "audited_at": datetime.now(UTC).isoformat(),
    }


def excludes_the_producer(verdict: dict) -> bool:
    """Whether this verdict's judge had no hand in making what it judged.

    Read from the record, not from a constant: a panel that includes the model which
    wrote the text is what refuted the last verification of this corpus, and a constant
    saying otherwise would be the same mistake written down.
    """
    made_it = {verdict.get("written_by"), verdict.get("decomposed_by")} - {""}
    return verdict.get("audited_by") not in made_it


def audit_path(city_slug: str, *, data_dir: Path | None = None) -> Path:
    root = data_dir if data_dir is not None else Path(__file__).resolve().parent.parent / "data"
    return root / city_slug / "reauthored-audit.json"


def summarise(verdicts: list[dict]) -> dict[str, Any]:
    """What the audit found, in the shape a decision needs."""
    audited = [v for v in verdicts if not v["unreadable"]]
    carrying = [v for v in audited if v["unsupported_count"]]
    return {
        "audited": len(audited),
        "unreadable": len(verdicts) - len(audited),
        "bodies_carrying_an_unsupported_claim": len(carrying),
        "pct": round(100 * len(carrying) / len(audited), 1) if audited else 0.0,
        "unsupported_claims": sum(v["unsupported_count"] for v in carrying),
        "judge_excludes_the_producer": all(excludes_the_producer(v) for v in audited),
    }


def render(verdicts: list[dict], *, limit: int = 12) -> str:
    """The findings a person reads, each next to the claim that came closest."""
    lines: list[str] = []
    for verdict in sorted(verdicts, key=lambda v: -v["unsupported_count"])[:limit]:
        if not verdict["unsupported_count"]:
            continue
        lines.append(f"\n{verdict['poi_name']}  ({verdict['unsupported_count']} unsupported)")
        lines.append(f"  body: {' '.join(verdict['body_after'].split())[:150]}")
        for finding in verdict["unsupported_claims"]:
            lines.append(f"  ✖ [{finding['kind']}] {finding['claim']}")
    return "\n".join(lines) or "\nNothing unsupported in the bodies audited."


def _text_of(response: Any) -> str:
    return "".join(getattr(block, "text", "") for block in response.content)


def _audit_one(record: dict, city: str, client: Any) -> dict[str, Any]:
    """Read the body blind, then match what it says to what its writer was given."""
    read_back = parse_claims(
        _text_of(
            client.messages.create(
                **audit_request(
                    body=record.get("body_after", ""),
                    poi=record.get("poi_name", ""),
                    city=city,
                )
            )
        )
    )
    if not read_back:
        return audit_record(record, read_back=read_back, matches=None)
    matches = parse_matches(
        _text_of(
            client.messages.create(
                **match_request(read_back=read_back, given=record.get("claims_given") or [])
            )
        )
    )
    return audit_record(record, read_back=read_back, matches=matches)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--city", default="paris", help="City slug under data/.")
    parser.add_argument("--limit", type=int, default=0, help="Audit at most this many bodies.")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    args = parser.parse_args(argv)

    if AUDITOR_MODEL == PRODUCER_MODEL:
        print("✗ the auditor is the producer; a model does not flag what it wrote.")
        return 1
    if not os.getenv("ONDOWAY_DEMO_APPROVE"):
        print("✗ this run is paid. Set ONDOWAY_DEMO_APPROVE=1 to allow it.")
        return 1

    bodies = load_json(cleanroom_path(args.city))
    if not bodies:
        print(f"✗ no regenerated bodies for {args.city}.")
        return 1

    out = audit_path(args.city)
    verdicts = load_json(out)
    seen = {v.get("beat_id") for v in verdicts}
    pending = [b for b in bodies if b.get("beat_id") not in seen]
    if args.limit:
        pending = pending[: args.limit]
    print(f"{args.city}: {len(verdicts)} already audited, {len(pending)} to audit now.")
    if not pending:
        print(render(verdicts))
        return 0

    load_dotenv()
    client = certification_judge_client()
    city = city_name(args.city)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for verdict in pool.map(lambda r: _audit_one(r, city, client), pending):
            verdicts.append(verdict)
            save_json(out, verdicts)

    found = summarise(verdicts)
    print(render(verdicts))
    print(f"\n✓ {out}\n  {json.dumps(found, indent=2)}")
    if not found["judge_excludes_the_producer"]:
        print("✗ a verdict was written by a model that made what it judged.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
