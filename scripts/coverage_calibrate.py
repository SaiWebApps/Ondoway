"""Coverage-judge PROMPT BAKE-OFF: score candidate coverage-judge prompts against the
labeled calibration set (fixtures/tour-craft/coverage_calibration_set.json) so the
winner is picked by ACCURACY on real paraphrase-vs-drop cases, not by guessing.

Each candidate is a {system, user} template with {fact} and {narration} placeholders; the
judge must answer YES (the narration conveys the fact) or NO. We report, per candidate:
overall accuracy, and — the two that matter — the FALSE-NEGATIVE rate on paraphrases (says
NO when the fact IS conveyed; this is what broke author-engine convergence) and the
FALSE-POSITIVE rate on real drops (says YES when the fact is absent; this would ship a
hallucination-adjacent miss).

Usage: ONDOWAY_DEMO_APPROVE=1 uv run python scripts/coverage_calibrate.py \
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
_SET = Path(__file__).resolve().parents[1] / "fixtures" / "tour-craft" / "coverage_calibration_set.json"

# Baseline: the CURRENT strict faithfulness entailment prompt used for coverage today
# (the one that false-negatives on paraphrase). Included so the bake-off shows the gap.
_BASELINE = {
    "system": "",
    "user": (
        "You are a strict fact-checker. Given a list of KEY CLAIMS and one SENTENCE, answer "
        "with exactly 'YES' if the sentence is fully supported by the claims, or 'NO' if it "
        "adds, contradicts, or overstates anything.\n\nKEY CLAIMS:\n- {narration}\n\n"
        "SENTENCE:\n{fact}\n\nAnswer (YES or NO):"
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


def _score(name, tmpl, items, client, model):
    sys_t, user_t = tmpl.get("system", ""), tmpl["user"]

    def _one(it):
        verdict = _judge(client, model, sys_t.format(**it) if sys_t else "",
                         user_t.format(fact=it["fact"], narration=it["narration"]))
        return it, verdict

    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(_one, items))
    correct = sum(1 for it, v in rows if v == it["conveyed"])
    # FN = fact IS conveyed (label true) but judge said NO -> the convergence-killer
    fn = [it for it, v in rows if it["conveyed"] and not v]
    # FP = fact is a real DROP (label false) but judge said YES -> a missed drop
    fp = [it for it, v in rows if not it["conveyed"] and v]
    return {"name": name, "acc": correct / len(items), "n": len(items),
            "false_neg": len(fn), "false_pos": len(fp), "fn_items": fn, "fp_items": fp}


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

    n_true = sum(1 for it in items if it["conveyed"])
    print(f"calibration set: {len(items)} items ({n_true} conveyed / {len(items)-n_true} drops); "
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
    results.sort(key=lambda r: (r["false_neg"] + r["false_pos"], -r["acc"]))

    print(f"\n{'candidate':42} {'acc':>6} {'FN(miss-conveyed)':>18} {'FP(miss-drop)':>15}")
    print("-" * 84)
    for r in results:
        print(f"{r['name'][:42]:42} {r['acc']*100:5.0f}% {r['false_neg']:>18} {r['false_pos']:>15}")
    best = results[0]
    print(f"\nWINNER: {best['name']}  (acc {best['acc']*100:.0f}%, "
          f"{best['false_neg']} FN, {best['false_pos']} FP)")
    for r in results:
        if r["fn_items"] or r["fp_items"]:
            print(f"\n  {r['name']} errors:")
            for it in r["fn_items"]:
                print(f"    FN (said NO, but conveyed): {it['fact'][:70]}")
            for it in r["fp_items"]:
                print(f"    FP (said YES, but dropped): {it['fact'][:70]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
