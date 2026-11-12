"""Turn Docs/business/cost-runs.json into Docs/business/tour-cost-measurement.{json,md}.

The report answers one question: if the app launches as built, what does it cost? It
reads the latest measured tour and recipe build and writes the cost structure.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
runs = json.loads((HERE / "cost-runs.json").read_text())
tour, build = runs["tour"], runs["recipe_build"]

PRICES = {
    "checked": "2026-10-09",
    "opus": "Claude Opus 4.8: $5 per million tokens in, $25 out",
    "haiku": "Claude Haiku 4.5: $1 per million tokens in, $5 out",
    "cache": "Cached input: 1.25 times the input price to write, 0.1 times to read",
    "voice": "OpenAI gpt-4o-mini-tts, voice nova: $0.015 per minute of audio, plus $0.60 per "
    "million text tokens (characters divided by 4)",
}

# ── a stop's cost, from the measured tour ──
tags = {tag: part["usd"] for tag, part in tour["summary"]["by_tag"].items()}
stops = tour["stops"]
fresh = [s for s in stops if not s["from_recipe"]]
reused = [s for s in stops if s["from_recipe"]]
# Fact checks and the short linking lines serve every stop alike: split evenly.
shared = (
    tags.get("haiku_fact_check", 0) + tags.get("tts_session_lines", 0) + tags.get("haiku_glue", 0)
) / len(stops)
# A stop written fresh is voiced whole; a recipe stop voices only its opening line, the
# rest is stored. The longest stop voice call is the fresh stop's.
stop_voice = sorted(
    (c["audio_seconds"] / 60 * 0.015 + c["chars"] / 4 * 0.6 / 1e6, c["chars"])
    for c in tour["voice_calls"]
    if c["tag"] == "tts_stop_narration"
)
fresh_voice = stop_voice[-1][0]
reused_voice = sum(usd for usd, _chars in stop_voice[:-1]) / len(reused)
writing = tags.get("opus_day_stop", 0) + tags.get("opus_full_telling", 0)
fresh_stop = writing / len(fresh) + fresh_voice + shared
recipe_stop = reused_voice + shared
per_recipe = build["summary"]["total_usd"] / len(build["recipes"])
city = runs["new_york_recipes"]
city_build = city["recipes"] * per_recipe

structure = {
    "recipe_usd": round(per_recipe, 3),
    "new_york_recipes": city["recipes"],
    "new_york_places": city["places"],
    "new_york_build_usd": round(city_build),
    "stop_from_recipe_usd": round(recipe_stop, 3),
    "stop_written_fresh_usd": round(fresh_stop, 3),
}
(HERE / "tour-cost-measurement.json").write_text(
    json.dumps(
        {
            "measured_on": runs["measured_on"],
            "prices": PRICES,
            "structure": structure,
            "tour": tour,
            "recipe_build": build,
        },
        indent=2,
    )
    + "\n"
)

minutes, seconds = divmod(round(tour["compose_seconds"]), 60)
names = ", ".join(s["place"] for s in stops)
L = [
    "# What Ondoway costs to run, as built today\n",
    "Measured on 11 November 2026 in New York, with real paid calls.\n",
    "## The cost structure\n",
    "| Cost | When it is paid | Amount |",
    "|---|---|---|",
    f"| Recipes for a city | Once per city, before launch | about ${city_build:,.0f} for New "
    f"York ({city['recipes']:,} recipes at ${per_recipe:.2f} each) |",
    f"| A stop told from a recipe | Each stop a walker hears | ${recipe_stop:.3f} |",
    f"| A stop written fresh | Each stop a walker hears | ${fresh_stop:.2f} |",
    "",
    f"A tour costs ${recipe_stop:.3f} for each stop told from a recipe and ${fresh_stop:.2f} "
    "for each stop written fresh.\n",
    "## Which stops are written fresh\n",
    "A stop is told from its place's recipe when all of these hold:\n",
    "- The walker is solo.",
    "- The tour has one lens or none.",
    "- The visit stays outside the door.",
    "- The stop is not the day's last.",
    "",
    "Every other stop is written fresh. The day's last stop always is, because it closes the "
    "day.\n",
    "## The measured tour\n",
    f"A {tour['duration_min']}-minute solo walk from {tour['start']}, with no lens. It has "
    f"{len(stops)} stops: {names}.\n",
    "| Stop | Told | Cost |",
    "|---|---|---|",
]
for s in stops:
    told = "From its recipe" if s["from_recipe"] else "Written fresh"
    cost = f"${recipe_stop:.3f}" if s["from_recipe"] else f"${fresh_stop:.2f}"
    L.append(f"| {s['place']} | {told} | {cost} |")
L += [
    "",
    f"- The tour cost ${tour['summary']['total_usd']:.2f}.",
    f"- Writing took {minutes} min {seconds} s, and voicing {round(tour['voice_seconds'])} s.",
    "- At a stop told from a recipe, the walker also gets the place's longer telling, already "
    "recorded, when they tap for more.",
    "",
    "## How it was measured\n",
    "- The tour went through the app's own steps: write the words, then record the voice.",
    "- Every paid call was counted where it leaves the machine, from the usage the provider "
    "reported.",
    "- Each stop is charged its own writing and voice. The fact checks and short linking lines "
    f"(${shared * len(stops):.2f} for the tour) are split evenly across the stops.",
    f"- Building the recipes for {' and '.join(r['place'] for r in build['recipes'])} cost "
    f"${build['summary']['total_usd']:.2f}, so ${per_recipe:.2f} each. Only the first has a "
    "longer telling; a place gets one when it has more to tell than its short telling holds.",
    f"- A city needs one recipe per place, plus one per lens its beats carry. New York has "
    f"{city['places']} places and needs {city['recipes']:,} recipes.",
    "- Prices:",
    *(f"  - {PRICES[k]}." for k in ("opus", "haiku", "cache", "voice")),
    "",
    "## Not counted\n",
    "- Voicing the longer telling a walker taps for at a stop written fresh.",
    "- Stops written again when a walker's day is replanned.",
    "- Servers, storage and the database. The money model counts these.",
    "",
    "## Limits\n",
    "- This is one tour and two recipes. The same tour's cost varies by up to 20% from run to "
    "run.",
    "- A recipe's cost depends on how much its place has to tell. Other cities' recipe counts "
    "are not known yet.",
    "",
]
(HERE / "tour-cost-measurement.md").write_text("\n".join(L))
print(json.dumps(structure, indent=2))
