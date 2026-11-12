"""Measure the real provider spend of the place-recipe build job, recipe by recipe.

Runs ``scripts/build_place_recipes.py`` in-process with the cost recorders from
``measure_tour_cost.py`` installed, writes one JSON line per recipe with every paid
call it made, and stops the job between recipes once the spend reaches the ceiling.
Nothing under src/ is changed. Run under the dev env wrapper:

  uv run python scripts/dev_env.py exec --profile local --render -- \
      env TTS_FALLBACK= uv run python <this file> <out.jsonl> <ceiling_usd> <build args...>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import measure_tour_cost as mtc

from scripts.build_place_recipes import parse_args, run


def main() -> None:
    out_path = Path(sys.argv[1])
    ceiling = float(sys.argv[2])
    args = parse_args(sys.argv[3:])
    spent = 0.0
    mark = 0

    def stop_when(outcome) -> bool:
        nonlocal spent, mark
        calls = mtc.CALLS[mark:]
        mark = len(mtc.CALLS)
        summary = mtc._summarise(calls)
        spent += summary["total_usd"]
        with out_path.open("a") as fh:
            fh.write(
                json.dumps({"outcome": outcome.__dict__, "summary": summary, "calls": calls}) + "\n"
            )
        print(f"   ${summary['total_usd']:.3f} for this recipe, ${spent:.2f} so far", flush=True)
        if spent >= ceiling:
            print(f"STOP: spend ${spent:.2f} reached the ${ceiling:.2f} ceiling", flush=True)
            return True
        return False

    run(args, stop_when=stop_when)
    print(f"TOTAL ${spent:.2f}", flush=True)


if __name__ == "__main__":
    main()
