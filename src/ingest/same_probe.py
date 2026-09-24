"""A second look at the merge judge's `same` folds, sampled K times per
labeled pair — Docs/ingestion/rebuild-spec.md, "`same` second-look probe".

A `same` fold keeps the existing claim's words and drops the new claim's.
When the new claim carries something the existing one lacks, that is lost
from every claim text the narrator reads: job B folded Frommer's "seminal
work by El Greco" into "displays works by El Greco", and replay cov2-r8
folded "a huge spiral ramp" at the atrium into "has a spiral ramp". No
lexical rule separates those from true rewordings (every true fold leaves
words over too), so this measures whether a model other than the merge
judge (Sonnet) and the author (Opus) can: Haiku, on the claim judge's role
and batch transport. The owner's rule (2026-09-24) is part of the question:
a judgment of importance, size, quality or reputation is an addition.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from src.ingest import llm

ROLE = "claim_judge"
PHASE = "P3"
MAX_TOKENS = 400

SAME_CHECK_PROMPT = """\
Two claims about the same place, {place}. The EXISTING claim is already in
the corpus. The INCOMING claim comes from another book. A merge judge decided the
INCOMING claim says the same thing as the EXISTING claim, so only the EXISTING
wording would be kept and the INCOMING wording would be dropped.

Decide whether anything would be lost. List every piece of the INCOMING claim
that the EXISTING claim does not state:
- a further fact, detail, date, number, name or qualifier;
- a judgment of importance, size, quality or reputation, such as
  "celebrated", "vast", "the finest", "much-loved", "panned" — these tell
  a listener what matters, so they count as additions.

These are NOT additions: different wording of the same fact, a different
word form, a different but equivalent way of giving an address, a time or a
number ("four" / "4", "6pm" / "six in the evening"), or a different name for
the same place.

EXISTING: {existing}
INCOMING: {incoming}

Answer with JSON only: {{"adds": [<each piece the INCOMING claim adds, quoted
from it>], "verdict": "adds" or "same"}}. verdict is "adds" whenever the
list is not empty.
"""

RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "adds": {"type": "array", "items": {"type": "string"}},
        "verdict": {"type": "string", "enum": ["adds", "same"]},
    },
    "required": ["adds", "verdict"],
    "additionalProperties": False,
}

PLAN: tuple[llm.PhaseCall, ...] = (
    llm.PhaseCall(
        phase=PHASE,
        role=ROLE,
        calls_per_unit=1,
        overhead_tokens=max(1, len(SAME_CHECK_PROMPT) // 4),
        expected_output_tokens=MAX_TOKENS,
    ),
)

#: S2: fold pairs may be answered `adds` (or fail) in at most this many samples.
FOLD_MISSES_ALLOWED = 2


def load_pairs(path: Path) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["pairs"]


def render(pair: dict) -> str:
    return SAME_CHECK_PROMPT.format(
        place=pair["place"], existing=pair["existing"], incoming=pair["new"]
    )


def custom_id(pair_id: str, sample: int) -> str:
    return f"{pair_id}-s{sample}"


def _parse(text: str) -> dict | None:
    try:
        answer = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(answer, dict) or answer.get("verdict") not in ("adds", "same"):
        return None
    adds = answer.get("adds")
    if not isinstance(adds, list):
        return None
    return {"verdict": answer["verdict"], "adds": [str(a) for a in adds]}


def run(
    pairs: Sequence[dict], *, samples: int, client: llm.ModelClient, arm: bool = True
) -> list[dict]:
    """Every pair asked `samples` times in ONE batch round; per sample the
    verdict (`adds`, `same`, or FAILED for an answer that did not parse or
    a batch failure — never dropped) and what it says the new claim adds."""
    prompts = [(custom_id(p["id"], k), render(p)) for p in pairs for k in range(1, samples + 1)]
    if arm:
        client.estimate([text for _, text in prompts], list(PLAN))
    results = client.complete_batch(
        ROLE, prompts, RESPONSE_SCHEMA, phase=PHASE, max_tokens=MAX_TOKENS
    )
    report = []
    for pair in pairs:
        for k in range(1, samples + 1):
            answer = results.get(custom_id(pair["id"], k))
            parsed = None if isinstance(answer, llm.BatchFailure) or answer is None else (
                _parse(answer.text)
            )
            report.append(
                {
                    "id": pair["id"],
                    "label": pair["label"],
                    "sample": k,
                    "verdict": parsed["verdict"] if parsed else "FAILED",
                    "adds": parsed["adds"] if parsed else [],
                }
            )
    return report


def score(report: Sequence[dict], pairs: Sequence[dict]) -> dict:
    """S1: every `hold` pair answered `adds` in every sample. S2: `fold`
    pairs answered `same` in all but at most FOLD_MISSES_ALLOWED of their
    samples together. A FAILED sample counts against its pair's bar.
    `either` pairs are reported, never scored."""
    label = {p["id"]: p["label"] for p in pairs}
    hold = [r for r in report if label[r["id"]] == "hold"]
    fold = [r for r in report if label[r["id"]] == "fold"]
    s1_caught = sum(r["verdict"] == "adds" for r in hold)
    s2_folded = sum(r["verdict"] == "same" for r in fold)
    s1 = {"caught": s1_caught, "of": len(hold), "pass": bool(hold) and s1_caught == len(hold)}
    s2 = {
        "folded": s2_folded,
        "of": len(fold),
        "pass": bool(fold) and len(fold) - s2_folded <= FOLD_MISSES_ALLOWED,
    }
    either = {}
    for r in report:
        if label[r["id"]] == "either":
            either.setdefault(r["id"], []).append(r["verdict"])
    return {"S1": s1, "S2": s2, "either": either, "pass": s1["pass"] and s2["pass"]}
