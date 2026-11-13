You are a pipeline orchestrator for the Ondoway content pipeline. You process multiple book chunks through the full pipeline in parallel, collecting all results into one consolidated review report.

Your task: batch process **$ARGUMENTS** through the complete pipeline.

Parse the arguments:
- City name (required): e.g., `Paris`
- Chunk identifiers (required, space-separated): e.g., `chunk-04 chunk-05 chunk-06 chunk-07 chunk-08`
  - These map to files in `Books/{city}/{book-slug}/chunk-XX-*.txt`
  - If a chunk number is given (e.g., `chunk-04`), glob for the matching file
- Book path (optional): if chunks are from a specific book, specify the book directory
  - Default: detect from `data/{city_slug}/book-log.json` — use the most recently processed book
- `--auto-correct` (optional, default ON): auto-resolve fact-check disputes using the two-source rule
- `--no-export` (optional): skip export file generation

---

## ARCHITECTURE NOTE — WHY RESEARCH-THEN-WRITE

Background agents CANNOT write files (project dir or /tmp). All file I/O must happen
in the main conversation. This pipeline splits work into:
- **Phase A** — parallel background agents do READ-ONLY research (extraction, fact-checking, geocoding, gravity)
- **Phase B** — main conversation collects results, deduplicates, and writes all files

---

## BEFORE STARTING

**Check for an existing pipeline state file:** `data/{city_slug}/.pipeline-state.json`

If this file exists, read it. It means a previous run was interrupted. Resume from the recorded stage:
- If `stage` is `"agents_launched"` or `"agents_running"` — agents were lost. Re-launch only the chunks listed in `chunks_pending`.
- If `stage` is `"collecting"` — skip agent launch, proceed to Phase B with the completed chunks.
- If `stage` is `"dedup_done"` — skip to Step B3 (export writing).
- If `stage` is `"exports_written"` — skip to Step B4 (tracking files).
- If `stage` is `"tracking_updated"` — skip to Step B5 (gravity scoring).
- If `stage` is `"gravity_done"` — skip to Phase C (report).
- If `stage` is `"complete"` — tell the user this batch is already done. Ask if they want to re-run.

If the file does NOT exist, start fresh.

**Then read** these files once in the main conversation:
- `data/{city_slug}/book-log.json` — check which chunks are already processed; skip them
- `src/schema/definitions.py` — get valid lens slugs and schema definitions

**Then write a shared context file** that all agents will read (avoids duplicating large lists across N agent prompts):

`data/{city_slug}/.pipeline-context.txt` — contains:
```
EXISTING_POI_NAMES (one per line, extracted from poi-raw.json):
Eiffel Tower
Louvre Museum
...

EXISTING_BEAT_IDS (one per line, extracted from beats.json):
louvre_museum_war_conflict_fortress_around_and_about_paris
...

VALID_LENS_SLUGS (comma-separated):
hidden_history, war_conflict, dark_history, ...
```

Build this with a small Python script that reads poi-raw.json, beats.json, and definitions.py and writes the txt file. This file is the single source of truth for the agent run; delete it after the batch completes.

Resolve chunk identifiers to full file paths using glob. Skip any chunks already in book-log.json (warn the user).

**Write the initial pipeline state file** — `data/{city_slug}/.pipeline-state.json`:
```json
{
  "stage": "init",
  "city": "{city_slug}",
  "book": "{book_slug}",
  "chunks_total": ["chunk-04", "chunk-05", "chunk-06"],
  "chunks_completed": [],
  "chunks_failed": [],
  "chunks_pending": ["chunk-04", "chunk-05", "chunk-06"],
  "new_pois_created": 0,
  "gravity_scored": false,
  "export_validated": false,
  "started_at": "ISO-8601 timestamp"
}
```

---

## PIPELINE STATE UPDATES

Update `data/{city_slug}/.pipeline-state.json` at every phase transition listed below. This enables resume after crash or context compaction. Only update the fields that changed — don't rewrite the whole file from memory.

| When | Set `stage` to | Also update |
|------|----------------|-------------|
| After launching agents | `"agents_running"` | — |
| As each agent completes | (keep `"agents_running"`) | Move chunk from `chunks_pending` → `chunks_completed` (or `chunks_failed`) |
| All agents done | `"collecting"` | — |
| After cross-chunk dedup | `"dedup_done"` | — |
| After writing export files | `"exports_written"` | `export_validated: true` |
| After updating tracking files | `"tracking_updated"` | `new_pois_created: N` |
| After gravity scoring + tests pass | `"gravity_done"` | `gravity_scored: true` |
| After report delivered | `"complete"` | — |

---

## PHASE A — PARALLEL RESEARCH (background agents)

**Cleaned-source dispatch:** When the orchestrator (Phase B / wave launcher) sends a chunk path to a background agent, it must check for `<chunk>.cleaned.txt` and pass that path if it exists; fall back to the original `<chunk>.txt` otherwise. Do NOT modify the agent prompt template to do this check — keep that responsibility in the main conversation so cleanup logic stays centralized. The agent's `source_chunk_slug` field still uses the canonical chunk identity (e.g. `chunk-06-marais`), not the cleaned suffix. See `scripts/strip_pdf_artifacts.py` for the cleaning step that produces the `.cleaned.txt` files; clean inputs are durable and produced once per chunk.

Launch one Agent per chunk. **ALL agents in a single message.** Use:
- `subagent_type: "general-purpose"`
- **Hybrid model strategy:** Opus for the **anchor wave** (tier-5 POI chunks — Notre-Dame/Sainte-Chapelle/Conciergerie/Place des Vosges/Marais/Quartier Latin/Historic Paris); **Sonnet + Phase B fixup pass** for **filler waves** (everything else). Sonnet smoke-tested at 56% tier-3+ cue coverage, 76% Pydantic acceptance, 48% length-class self-consistency, and zero trigger_address beats on v2 — acceptable only for non-anchor content where the Phase B fixup recovers schema/length/subject_tag failures and queues genuine misses for review (do NOT auto-fill empty physical_cues — that's invention). Opus matches hand-extracted Vallois quality (chunks 01–05) on the anchor cases. Revisit Sonnet for anchor work only if a future Sonnet release passes a fresh smoke test of Rough Guide chunk-01 (≥95% Pydantic acceptance, ≥95% tier-3+ cue coverage, ≥90% length-class consistency, ≥1 trigger_address beat).
- `run_in_background: true`

### Agent prompt template

Each agent gets this prompt (fill in the variables):

```
You are a content extraction and fact-checking agent for the Ondoway pipeline.
Your job is READ-ONLY research. Do NOT write any files. Return all results as
structured JSON in your final response.

## INPUT
- City: {city_name}
- Chunk file: {chunk_file_path}
- Shared context file (READ THIS ONCE): `data/{city_slug}/.pipeline-context.txt` — contains the existing POI names, existing beat IDs, and valid lens slugs. Do NOT read poi-raw.json or beats.json directly; the context file is authoritative and far smaller.
- Auto-correct mode: ON

## TASK — Run these steps in order:

### Step 1 — BEAT EXTRACTION

**Follow the full extraction contract in `.claude/commands/unified-beat-extract.md`** — read that file once before extracting. It is the authoritative spec; this prompt is a thin agent harness around it.

That contract includes (non-exhaustive):
- All four phases of the unified_v2 pipeline
- Every B-rule (B2 multi-granularity, B3 preserve-don't-paraphrase, B4 address-recognition for seasoning, B5 length discipline with asymmetric re-class, B6 sidebar detection, B7 verbatim source_passage, B9 location-anchored poi_name)
- The 5 v2 fields on every beat: `sub_location`, `trigger_address`, `beat_length_class` (one of `anchor`/`mid`/`seasoning`/`micro`), `inline_foreign_phrases` (list of `{phrase, gloss}`), `pronunciation`
- The 3 new beat_type values: `stop_orientation`, `transit`, `sidebar` (in addition to the 7 narrative types)
- **Fix 1**: a single source sentence grounds at most one beat; `source_passage` carries the minimum span; no two beats share the first real sentence of their source_passage
- **Fix 2**: every beat with non-null `trigger_address` must have non-empty `physical_cues` — at minimum a façade/door/plaque cue at that address
- Tier-3+ POIs: `physical_cues` populated whenever the source passage cites a visible feature
- `subject_tag` ≤ 3 space-separated words (use kebab-hyphenation for proper-noun French compounds like `poule-au-pot`, `pavillon-de-la-reine`)
- `_meta.prompt_version` = `"unified_v2"` on every beat

Other extraction principles still apply:
- Zero hallucination — every fact traceable to source text
- One story = one beat
- Exhaustive lens scan per POI (all 21 child lenses)
- `source_passage` required on every beat (full verbatim sentence(s), not a snippet)

After extraction, re-read the chunk and verify each `source_passage` can be found in the text (approximate match, allow for OCR artifacts). Flag any that can't.

### Step 2 — POI MATCHING
Match extracted beats to the existing POI names provided above (case-insensitive,
check name variations). For locations not in the existing list:
- Web search to determine: alias of existing POI, child POI, or genuinely new
- For genuinely new POIs: geocode via Nominatim API, do a quick gravity assessment
  via web search (Wikipedia depth, guidebook presence, visitor estimates)
- Assign importance_tier 1-5 and trigger_radius by POI type

### Step 3 — FACT-CHECK (auto-correct mode)
For each beat, web search to verify key claims (dates, names, numbers, attributions).
- Auto-correct ONLY when 2+ independent sources agree
- If sources conflict → mark as DISPUTE
- NEVER auto-correct: living people claims, superlative disputes, story deletions
- Log every correction with source URLs

### Step 4 — ANOMALY CHECK
Flag: <3 beats (under-extraction), >25 beats (over-extraction), beats >200 words
or <30 words, source passages not found, new POIs within 100m of existing POIs.

## OUTPUT FORMAT — Return EXACTLY this JSON structure:

{
  "chunk_id": "chunk-XX-...",
  "chunk_file": "path/to/file.txt",
  "pois": [
    {
      "name": "POI Name",
      "is_new": true,
      "matched_existing": null,
      "short_description": "...",
      "name_variations": ["..."],
      "kid_friendly": "yes",
      "latitude": 48.XXX,
      "longitude": 2.XXX,
      "trigger_radius": 10,
      "importance_tier": 3,
      "geocode_source": "Nominatim OSM",
      "geocode_confidence": "HIGH",
      "gravity_reasoning": "..."
    }
  ],
  "beats": [
    {
      "beat_id": "city_poi_slug_lens_slug_book_slug_topic_slug",
      "city_name": "paris",
      "poi_name": "POI Name",
      "lens": "hidden_history",
      "topic_slug": "...",
      "book_slug": "...",
      "source_chunk_slug": "chunk-XX-...",
      "sub_location": null,
      "trigger_address": null,
      "beat_length_class": "anchor|mid|seasoning|micro",
      "script_body": "...",
      "duration_sec": 45,
      "kid_friendly": "yes",
      "entities": ["..."],
      "sensory_anchor": true,
      "narrative_function": "establishing|hook|deepen|climax|scene_setter|transition|callback",
      "beat_type": "anecdote|character_story|event|architectural_detail|sensory_observation|factoid|establishing|stop_orientation|transit|sidebar",
      "emotional_register": "reverent|somber|playful|dramatic|wry|neutral",
      "subject_tag": "1-3 words or kebab-hyphenated French",
      "physical_cues": [
        {"cue": "...", "direction": "up|down|north|south|east|west|here", "feature_type": "architectural_detail|plaque|view|interior|adjacent_landmark"}
      ],
      "inline_foreign_phrases": [{"phrase": "...", "gloss": "..."}],
      "pronunciation": null,
      "key_claims": ["..."],
      "source_passage": "verbatim sentence(s) from source",
      "source_passage_verified": true,
      "source_attribution": {"book_title": "...", "author": "...", "chapter": "..."},
      "confidence": "HIGH",
      "fact_check_status": "verified|corrected|disputed",
      "corrections": [
        {
          "original_text": "...",
          "corrected_text": "...",
          "source_urls": ["..."],
          "impact": "LOW|MEDIUM|HIGH"
        }
      ],
      "disputes": [],
      "_meta": {"prompt_version": "unified_v2", "generated_at": "ISO 8601", "city_name": "paris"}
    }
  ],
  "review_queue": [
    {
      "type": "DISPUTE|PROXIMITY|ANOMALY",
      "description": "...",
      "recommendation": "..."
    }
  ],
  "summary": {
    "beats_extracted": 12,
    "pois_touched": 6,
    "new_pois": 2,
    "corrections_applied": 3,
    "disputes": 0,
    "anomalies": 0,
    "lenses_hit": ["hidden_history", "dark_history"],
    "lenses_missed": ["..."],
    "confidence_counts": {"HIGH": 10, "MEDIUM": 2, "LOW": 0}
  }
}

Do NOT write any files. Do NOT use the Write, Edit, or Bash-with-redirect tools.

## OUTPUT RULES — STRICT
- Your final message must contain ONLY the JSON code fence. Nothing else.
- No preamble ("Now I have enough information..."), no postscript, no narration of
  corrections, no list of sources, no summary prose.
- All correction reasoning, source URLs, and dispute notes go INSIDE the JSON
  (in the `corrections` array and `review_queue` entries). Do not repeat them
  outside the fence.
- Every sentence outside the JSON fence costs the user tokens. Be ruthless.
```

### Handling the existing data problem

The shared context file `data/{city_slug}/.pipeline-context.txt` (written once by the
main conversation before agents launch) is the single source of truth for existing
POI names, existing beat IDs, and valid lens slugs. Agents read it once.

Agents MUST NOT read `poi-raw.json` or `beats.json` directly — these files are large
and the context file contains everything they need in a compact form.

---

## PHASE B — COLLECT + WRITE (main conversation)

Wait for ALL agents to complete. As each finishes, parse its JSON response.

If an agent fails or returns malformed output, log the failure and continue with
the others. Report failed chunks prominently so the user can re-run them with
`/pipeline-chunk`.

### Step B1 — Parse all results

For each completed agent, extract:
- The POI list (new + matched)
- The beat list (with fact-check results)
- The review queue items

### Step B2 — Cross-chunk dedup

1. **POI dedup:** If two chunks created the same new POI (by name or name_variations),
   merge them — keep the entry with more beats / richer description.
2. **Beat dedup:** If two chunks produced beats with >50% key_claim overlap at the
   same POI, flag as duplicate in the review queue.
3. **POI-existing proximity:** If two new POIs from different chunks are within 100m
   of each other, flag for review.

### Step B3 — Build export files

For each chunk, build the export JSON in the format matching existing exports in
`data/{city_slug}/export/` (POIs with nested beats array). Write one file per chunk:

`data/{city_slug}/export/{book_slug}-{chunk_slug}.json`

### Step B4 — Update tracking files

After ALL exports are written:

1. **Append new POIs** to `data/{city_slug}/poi-raw.json`
   - Include full `_pipeline` and `_meta` blocks
   - **DO NOT include `importance_tier`** for new POIs. Tiers must come from the
     formal `/poi-gravity` scoring pass, not agent guesses. The Phase B5 step
     below will fail loudly if any POI in poi-raw.json lacks a `gravity_audit`.
   - Skip POIs that already exist (match by name, case-insensitive)

2. **Append new beats** to `data/{city_slug}/beats.json`
   - Include full beat objects with `fact_check` block
   - Skip beats with duplicate `beat_id`

3. **Update `data/{city_slug}/book-log.json`**
   - Add one entry per chunk with: beats_extracted, pois_touched, pois_created, fact_check_flags

**IMPORTANT:** Process chunks in order (by chunk number) so that earlier chunks'
new POIs are visible to later chunks' matching logic.

### Step B5 — Mandatory gravity scoring (NEW POIs only)

If this batch added any new POIs to `poi-raw.json`, you MUST run
`/poi-gravity {city} --rescore` before the batch is considered complete.
Reason: agents are not allowed to assign `importance_tier` themselves —
that comes from the formal scoring pass with quantitative signals
(visitor counts, Google reviews, Trends, Wikipedia, guidebook presence)
plus the forced-distribution rule.

After the rescore completes, run:
```
.venv/bin/python -m pytest tests/test_gravity_distribution.py tests/test_export_consistency.py -v
```
Both must pass before exporting tier data anywhere downstream. If either fails,
the batch is NOT done and the user must be told what went wrong.

**Why this is non-negotiable:** an earlier pipeline build let agents guess
tiers, which produced a bell-curved distribution clustered at tier 3 with
famous landmarks (Notre-Dame, Eiffel Tower) sitting at tier 1. The schema's
silent default of `importance_tier=1` masked the demotions. Both bugs are
fixed in `src/api/models/nodes.py` and the tests above, but only if the
pipeline actually runs the scoring pass.

---

## PHASE C — CONSOLIDATED REPORT

Present ONE report covering ALL chunks:

```
=== BATCH PIPELINE REPORT ===
Chunks processed: 5 (chunk-04 through chunk-08)

EXTRACTION SUMMARY:
  | Chunk    | Beats | POIs touched | New POIs | Corrections | Anomalies |
  |----------|-------|-------------|----------|-------------|-----------|
  | chunk-04 | 12    | 6           | 1        | 2           | 0         |
  | chunk-05 | 8     | 4           | 0        | 1           | 1         |
  | ...      |       |             |          |             |           |
  | TOTAL    | 67    | 22          | 3        | 8           | 1         |

AUTO-CORRECTIONS APPLIED: X total
  (grouped by chunk, each with source URLs)

CROSS-CHUNK DEDUP: X items resolved, Y flagged for review

REVIEW QUEUE: X items
  (numbered, grouped by type: DISPUTE / PROXIMITY / ANOMALY / DEDUP)

EXPORT FILES:
  data/paris/export/around-and-about-paris-chunk-04-....json (6 POIs, 12 beats, 15KB)
  data/paris/export/around-and-about-paris-chunk-05-....json (4 POIs, 8 beats, 10KB)
  ...

FAILED CHUNKS: (list any that need re-running with /pipeline-chunk)

READY FOR UPLOAD: YES/NO
```

---

## AFTER USER REVIEW

Once the user resolves all REVIEW QUEUE items:
1. Apply their decisions (corrections, merges, removals)
2. Regenerate affected export files
3. Update pipeline state to `"complete"`
4. Confirm: "X export files ready. Run `/upload Paris` to push to Neo4j."

**Cleanup:** Delete `data/{city_slug}/.pipeline-context.txt` after the batch completes. Keep `.pipeline-state.json` with `stage: "complete"` — it serves as a record and prevents accidental re-runs.

---

## GUARDRAILS

Apply the 5 pipeline guardrails (they are baked into the agent prompt template above).

**Additional batch guardrails:**
6. Cross-chunk dedup check before export
7. If any chunk produces 0 beats, flag prominently (don't silently skip)
8. If total anomalies exceed 20% of beats, pause and alert user before exporting
9. If an agent fails entirely, report it — don't silently drop a chunk
10. Process chunk file writes in order to maintain data consistency

---

## SELF-VERIFICATION

Before delivering the Phase C report:

1. **All chunks accounted for** — chunks_completed + chunks_failed = chunks_total (none silently dropped)
2. **Cross-chunk dedup ran** — no duplicate POIs or >50% overlapping beats across chunks
3. **Every export file is valid JSON** — parseable, matches schema of existing exports
4. **poi-raw.json has no importance_tier on new POIs** — tiers come from `/poi-gravity`, not agents
5. **Gravity scoring completed (if new POIs)** — `test_gravity_distribution.py` and `test_export_consistency.py` both pass
6. **book-log.json updated** — every processed chunk has an entry
7. **Pipeline state set to "complete"** or appropriate stage — `.pipeline-state.json` reflects reality
8. **Report totals are consistent** — sum of per-chunk beats/POIs matches the TOTAL row
9. **Failed chunks listed prominently** — not buried in the report
