"""Faithfulness-judge PROMPT BAKE-OFF: score candidate faithfulness-judge prompts against
the labeled set (fixtures/tour-craft/faithfulness_calibration_set.json) so the
winner is picked by ACCURACY on real grounded-recombination-vs-invention cases, not guessed.

Each item is {facts: [...], claim, supported}. A candidate is a {system, user} template with
{facts} and {claim} placeholders; the judge answers YES (the claim is supported by the
facts) or NO. We report, per candidate:
  - overall accuracy;
  - FALSE-POSITIVE count (says YES when the claim is INVENTED/distorted -> would ship a
    hallucination; this is the DANGEROUS error a faithfulness gate exists to prevent);
  - FALSE-NEGATIVE count (says NO when the claim is grounded recombination -> the
    convergence-killer that throws away good author prose).
Winner is ranked by (FP, FN, -acc): never ship a hallucination first, then converge.

Usage: ONDOWAY_DEMO_APPROVE=1 uv run python scripts/faithfulness_calibrate.py \
    --candidates <candidates.json> [--model claude-haiku-4-5-20251001] --live
Dry run (no --live) prints the plan + call count only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

#: The labeled set this bake-off scores against. A fixture the code OPENS, so it
#: lives under `fixtures/` — the spec tree was deleted 2026-09-02 and is refused by
#: the junk guard.
_SET = Path(__file__).resolve().parents[1] / "fixtures" / "tour-craft" / "faithfulness_calibration_set.json"

# Baseline: the CURRENT strict entailment prompt used for the faithfulness direction today
# (src/tour/verify.py _ENTAILMENT_PROMPT). Included so the bake-off shows its FN gap on
# grounded recombination (the measured convergence blocker).
_BASELINE = {
    "system": "",
    "user": (
        "You are a strict fact-checker. Given a list of KEY CLAIMS and one SENTENCE, answer "
        "with exactly 'YES' if the sentence is fully supported by the claims, or 'NO' if it "
        "adds, contradicts, or overstates anything.\n\nKEY CLAIMS:\n{facts}\n\n"
        "SENTENCE:\n{claim}\n\nAnswer (YES or NO):"
    ),
}


def _judge(client, model: str, system: str, user: str) -> bool:
    kwargs = {"model": model, "max_tokens": 5, "temperature": 0,
              "messages": [{"role": "user", "content": user}]}
    if system:
        kwargs["system"] = system
    resp = client.messages.create(**kwargs)
    text = "".join(getattr(b, "text", "") for b in (getattr(resp, "content", []) or [])).strip().upper()
    return text.startswith("YES")


def _fmt_facts(facts: list[str]) -> str:
    return "\n".join(f"- {f}" for f in facts)


def _score(name, tmpl, items, client, model):
    sys_t, user_t = tmpl.get("system", ""), tmpl["user"]

    def _one(it):
        facts_str = _fmt_facts(it["facts"])
        verdict = _judge(client, model,
                         sys_t.format(facts=facts_str, claim=it["claim"]) if sys_t else "",
                         user_t.format(facts=facts_str, claim=it["claim"]))
        return it, verdict

    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(_one, items))
    correct = sum(1 for it, v in rows if v == it["supported"])
    # FP = claim is INVENTED (supported=false) but judge said YES -> shipped hallucination
    fp = [it for it, v in rows if not it["supported"] and v]
    # FN = claim IS grounded (supported=true) but judge said NO -> the convergence-killer
    fn = [it for it, v in rows if it["supported"] and not v]
    return {"name": name, "acc": correct / len(items), "n": len(items),
            "false_pos": len(fp), "false_neg": len(fn), "fp_items": fp, "fn_items": fn}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", default="", help="JSON {name: {system, user}}; empty = baseline only")
    ap.add_argument("--model", default="claude-haiku-4-5-20251001")
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()

    items = json.loads(_SET.read_text())["items"]
    cands = {"BASELINE_strict_entailment": _BASELINE}
    if args.candidates:
        cands.update(json.loads(Path(args.candidates).read_text()))

    n_true = sum(1 for it in items if it["supported"])
    print(f"calibration set: {len(items)} items ({n_true} supported / {len(items)-n_true} invented); "
          f"{len(cands)} candidates -> {len(items)*len(cands)} Haiku calls")
    if not args.live:
        print("[DRY RUN] --live + ONDOWAY_DEMO_APPROVE=1 to spend (~$0.001/call).")
        return 0
    if os.getenv("ONDOWAY_DEMO_APPROVE") != "1":
        print("REFUSED: --live needs ONDOWAY_DEMO_APPROVE=1.", file=sys.stderr)
        return 3

    import anthropic

    client = anthropic.Anthropic()
    results = [_score(name, tmpl, items, client, args.model) for name, tmpl in cands.items()]
    # Rank: never ship a hallucination (FP) first, then converge (FN), then accuracy.
    results.sort(key=lambda r: (r["false_pos"], r["false_neg"], -r["acc"]))

    print(f"\n{'candidate':42} {'acc':>6} {'FP(ship-halluc)':>16} {'FN(kill-converge)':>18}")
    print("-" * 86)
    for r in results:
        print(f"{r['name'][:42]:42} {r['acc']*100:5.0f}% {r['false_pos']:>16} {r['false_neg']:>18}")
    best = results[0]
    print(f"\nWINNER: {best['name']}  (acc {best['acc']*100:.0f}%, "
          f"{best['false_pos']} FP, {best['false_neg']} FN)")
    for r in results:
        if r["fp_items"] or r["fn_items"]:
            print(f"\n  {r['name']} errors:")
            for it in r["fp_items"]:
                print(f"    FP (said YES, but INVENTED): {it['claim'][:70]}")
            for it in r["fn_items"]:
                print(f"    FN (said NO, but grounded): {it['claim'][:70]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
