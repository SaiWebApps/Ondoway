"""Turn tours.jsonl into docs/business/tour-cost-measurement.{json,md}."""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

src = Path(sys.argv[1])
out_dir = Path("/Users/sairambkrishnan/git/ondoway/docs/business")
out_dir.mkdir(parents=True, exist_ok=True)

rows = [json.loads(line) for line in src.read_text().splitlines() if line.strip()]

PRICING = {
    "date": "2026-10-09",
    "anthropic": {
        "claude-opus-4-8": {"input_usd_per_mtok": 5.0, "output_usd_per_mtok": 25.0},
        "claude-haiku-4-5": {"input_usd_per_mtok": 1.0, "output_usd_per_mtok": 5.0},
        "cache_write_multiplier": 1.25,
        "cache_read_multiplier": 0.10,
        "note": "Thinking tokens are billed as output and are inside output_tokens.",
    },
    "openai_tts": {
        "model": "gpt-4o-mini-tts",
        "voice": "nova",
        "audio_usd_per_minute": 0.015,
        "text_input_usd_per_mtok": 0.60,
        "text_tokens_estimated_as": "characters / 4",
        "note": (
            "OpenAI lists $0.60/MTok text in and $12/MTok audio out, and estimates "
            "$0.015 per minute. The speech endpoint returns no token counts, so audio "
            "is priced by measured minutes. The code comment '$12 per 1M characters' "
            "is a different unit and is not used."
        ),
    },
}

COMPONENTS = [
    ("opus_day_stop", "Opus writes each stop"),
    ("opus_reroll", "Opus rewrites a stop that failed a check"),
    ("opus_full_telling", "Opus writes the longer telling for a main stop"),
    ("opus_standby", "Opus writes the standby stops"),
    ("haiku_fact_check", "Haiku checks each sentence against the sources"),
    ("haiku_glue", "Haiku writes linking lines"),
    ("tts_stop_narration", "Voice for each stop's narration"),
    ("tts_session_lines", "Voice for closes, links and walking lines"),
]


def comp(r, tag):
    b = r["summary"]["by_tag"].get(tag)
    if not b:
        return {"calls": 0, "usd": 0.0}
    keep = {k: v for k, v in b.items() if v}
    keep["usd"] = round(b["usd"], 4)
    if "audio_seconds" in keep:
        keep["audio_seconds"] = round(keep["audio_seconds"], 1)
    return keep


tours = []
for r in rows:
    served = r.get("compose_status") == 200 and str(r.get("audio_status", "")).startswith("2")
    if served:
        outcome = "served"
    elif r.get("generate_status") == 422:
        outcome = "planner_refused_free"
    elif r.get("compose_status") == 422:
        outcome = "writing_refused_after_spend"
    elif r.get("compose_status") == 502:
        outcome = "provider_timeout_after_spend"
    else:
        outcome = "other_failure"
    by_tag = r["summary"]["by_tag"]
    unknown = sorted(t for t in by_tag if t not in dict(COMPONENTS))
    tours.append(
        {
            "tour": r["index"],
            "start": r["start"],
            "duration_min": r["duration_min"],
            "party": r["party"],
            "lenses": r["lenses"],
            "served": served,
            "outcome": outcome,
            "refusal": None if served else (r.get("error") or "")[:500],
            "compose_attempts": r.get("attempts"),
            "stops_narrated": r.get("stops_narrated", 0),
            "full_tellings_kept": r.get("full_tellings", 0),
            "standbys": (r.get("session_lists") or {}).get("standbys", 0),
            "narration_chars": r.get("narration_chars", 0),
            "on_demand_chars_not_voiced": {
                "full_narration": r.get("full_narration_chars", 0),
                "extra_narration": r.get("extra_narration_chars_on_demand", 0),
            },
            "components": {tag: comp(r, tag) for tag, _ in COMPONENTS},
            "other_components": {t: comp(r, t) for t in unknown},
            "anthropic_usd": round(r["summary"]["anthropic_usd"], 4),
            "tts_usd": round(r["summary"]["tts_usd"], 4),
            "total_usd": round(r["summary"]["total_usd"], 4),
            "compose_seconds": r.get("compose_seconds"),
            "wall_seconds": r.get("wall_seconds"),
        }
    )

served = [t for t in tours if t["served"]]
by_duration = {}
for d in sorted({t["duration_min"] for t in tours}):
    s = [t for t in served if t["duration_min"] == d]
    a = [t for t in tours if t["duration_min"] == d]
    if not a:
        continue
    usd = [t["total_usd"] for t in s]
    by_duration[str(d)] = {
        "tours_run": len(a),
        "tours_served": len(s),
        "mean_usd": round(statistics.mean(usd), 3) if usd else None,
        "min_usd": round(min(usd), 3) if usd else None,
        "max_usd": round(max(usd), 3) if usd else None,
        "mean_stops_narrated": round(statistics.mean(t["stops_narrated"] for t in s), 1) if s else None,
        "spend_incl_refused_per_served_usd": round(sum(t["total_usd"] for t in a) / len(s), 3) if s else None,
    }

total_spend = round(sum(t["total_usd"] for t in tours), 3)
total_spend_pre = total_spend
stops = sum(t["stops_narrated"] for t in served)
served_usd = sum(t["total_usd"] for t in served)
share = {}
for tag, _ in COMPONENTS:
    share[tag] = round(sum(t["components"][tag]["usd"] for t in served) / served_usd, 3) if served_usd else None

outcomes = {}
for t in tours:
    outcomes[t["outcome"]] = outcomes.get(t["outcome"], 0) + 1
paid_attempts = [t for t in tours if t["outcome"] != "planner_refused_free"]
summary = {
    "outcomes": outcomes,
    "opus_usd_per_served_tour_mean": round(statistics.mean(sum(t["components"][k]["usd"] for k in ("opus_day_stop","opus_reroll","opus_full_telling","opus_standby")) for t in served), 3) if served else None,
    "haiku_fact_check_usd_per_served_tour_mean": round(statistics.mean(t["components"]["haiku_fact_check"]["usd"] for t in served), 3) if served else None,
    "haiku_glue_usd_per_served_tour_mean": round(statistics.mean(t["components"]["haiku_glue"]["usd"] for t in served), 4) if served else None,
    "tts_usd_per_served_tour_mean": round(statistics.mean(t["tts_usd"] for t in served), 3) if served else None,
    "spend_per_served_tour_incl_failed_attempts_usd": round(total_spend_pre / len(served), 3) if served else None,
    "tours_run": len(tours),
    "tours_served": len(served),
    "total_spend_usd": total_spend,
    "usd_per_narrated_stop": round(served_usd / stops, 3) if stops else None,
    "share_of_cost_by_component": share,
    "by_duration_min": by_duration,
}

doc = {
    "title": "Measured provider cost of one walking tour (New York)",
    "measured_on": "2026-10-09",
    "pricing_basis": PRICING,
    "method": (
        "Each tour ran through the product's own endpoints in-process against the local "
        "dev graph and routing engine, with the real paid providers: POST /trips/generate, "
        "POST /trips/{id}/compose (route_id opt1), POST /audio/generate-trip-stops/{id}. "
        "Every Anthropic messages.create call and every OpenAI speech request was recorded "
        "at the SDK and HTTP seam, with the usage the provider reported. No product code "
        "was changed."
    ),
    "summary": summary,
    "tours": tours,
}
(out_dir / "tour-cost-measurement.json").write_text(json.dumps(doc, indent=2) + "\n")

# ── markdown ──
L = []
L.append("# What one walking tour costs us to make\n")
L.append("Measured on 2026-10-09 with real paid calls, New York, "
         f"{len(tours)} tours ({len(served)} served).\n")
L.append("## Outcome\n")
L.append("| Tour length | Tours served | Average cost | Lowest | Highest | Average stops |")
L.append("|---|---|---|---|---|---|")
for d, v in by_duration.items():
    if v["mean_usd"] is None:
        L.append(f"| {d} min | 0 of {v['tours_run']} | – | – | – | – |")
    else:
        L.append(f"| {d} min | {v['tours_served']} of {v['tours_run']} | ${v['mean_usd']:.2f} | "
                 f"${v['min_usd']:.2f} | ${v['max_usd']:.2f} | {v['mean_stops_narrated']} |")
L.append("")
if summary["usd_per_narrated_stop"] is not None:
    L.append(f"- Cost per narrated stop: **${summary['usd_per_narrated_stop']:.2f}**.")
L.append(f"- Total spent on this measurement: **${total_spend:.2f}**, including tours that were refused.")
refused = [t for t in tours if not t["served"]]
if refused:
    L.append(f"- {len(refused)} tour(s) were refused after money was spent. "
             "That money is counted in the total and in the 'with refusals' column of the JSON.")
L.append("")
L.append("## Where the money goes\n")
L.append("| Part of the work | Share of cost |")
L.append("|---|---|")
for tag, label in COMPONENTS:
    if share.get(tag):
        L.append(f"| {label} | {share[tag]*100:.0f}% |")
L.append("")
L.append("## Each tour\n")
L.append("| # | Start | Length | Party | Stops | Longer tellings | Standbys | Rewrites | Writing $ | Voice $ | Total $ |")
L.append("|---|---|---|---|---|---|---|---|---|---|---|")
for t in tours:
    rew = t["components"]["opus_reroll"].get("calls", 0)
    status = "" if t["served"] else " (refused)"
    L.append(f"| {t['tour']} | {t['start']}{status} | {t['duration_min']} min | {t['party']} | "
             f"{t['stops_narrated']} | {t['full_tellings_kept']} | {t['standbys']} | {rew} | "
             f"{t['anthropic_usd']:.2f} | {t['tts_usd']:.2f} | {t['total_usd']:.2f} |")
L.append("")
L.append("Token counts, call counts, characters and audio seconds for every part of every "
         "tour are in `tour-cost-measurement.json`.\n")
L.append("## Evidence\n")
L.append("- Each tour went through the same three steps the app uses: plan the route, write "
         "the words, record the voice.")
L.append("- The plan step made no paid calls on any tour. The script checks this.")
L.append("- Every paid call was counted where it leaves the machine, using the usage numbers "
         "the provider sent back. Nothing was estimated except the voice text tokens.")
L.append("- Prices used (checked 2026-10-09):")
L.append("  - Opus 4.8: $5 per million tokens in, $25 out. Haiku 4.5: $1 in, $5 out.")
L.append("  - Cached input: 1.25 times the input price to write, 0.1 times to read.")
L.append("  - Voice (gpt-4o-mini-tts, voice nova): $0.015 per minute of audio, plus $0.60 per "
         "million text tokens (characters divided by 4).")
L.append("- Opus thinking is billed as output and is already inside the output counts.")
L.append("")
L.append("## Not measured\n")
L.append("- Audio made only when a listener taps for more: the longer telling of a main stop "
         "and the 'keep exploring' extras. Their text sizes are in the JSON, so their cost "
         "can be worked out if they are voiced.")
L.append("- New stops written when a walker replans mid-walk.")
L.append("- Servers, database, storage and bandwidth.")
L.append("- The half-price batch option. The app writes tours live, so it does not apply.")
L.append("- Cities other than New York.")
L.append("")
L.append("## Limits of this sample\n")
L.append("- Every served tour was solo, 60 or 90 minutes, with 1 or 2 stops. Six tours is a small sample.")
L.append("- A day in a 5-day pass may be longer. Scale by the cost per narrated stop.")
L.append("- Every family tour was refused at the plan step: no start tried could fill 60 minutes or more.")
L.append("- Every 180-minute tour was refused while writing, after money was spent.")
L.append("- The runs used a working copy with uncommitted changes in src/tour/contingency.py.")
L.append("")
(out_dir / "tour-cost-measurement.md").write_text("\n".join(L))
print(json.dumps(summary, indent=2))
