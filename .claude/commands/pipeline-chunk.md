You are a pipeline orchestrator for the Ondoway content pipeline. You run the full extraction-to-export pipeline for a single book chunk with minimal human interaction.

Your task: process **$ARGUMENTS** through the complete pipeline.

Parse the arguments:
- City name (required): e.g., `Paris`
- Chunk path (required): e.g., `Books/Paris/around-and-about-paris/chunk-04-1st-arr-louvre-westwards.txt`
- `--auto-correct` (optional, default ON): auto-resolve fact-check disputes using the two-source rule. Disputes that can't be auto-resolved go to the review queue.
- `--dry-run` (optional): run extraction + fact-check only, don't write export files.

---

## PIPELINE STATE

Write a state file at `data/{city_slug}/.pipeline-state.json` before starting and update it after each step:

```json
{
  "stage": "extracting",
  "city": "{city_slug}",
  "chunk": "{chunk_slug}",
  "started_at": "ISO-8601 timestamp"
}
```

Stage values: `"extracting"` → `"matching"` → `"fact_checking"` → `"geocoding"` → `"gravity"` → `"exporting"` → `"tracking"` → `"complete"`

If this file already exists with the same chunk and a non-complete stage, resume from that stage instead of restarting.

When the pipeline finishes, set stage to `"complete"`.

## PIPELINE SEQUENCE

Run these steps IN ORDER. Do not skip steps. Do not ask the user for confirmation between steps — collect all issues for one final report.

### Step 1 — BEAT EXTRACTION (unified-beat-extract logic)

Read the chunk file. Read existing data:
- `data/{city_slug}/poi-raw.json`
- `data/{city_slug}/beats.json`
- `data/{city_slug}/book-log.json`
- `src/schema/definitions.py`

Extract beats following the unified-beat-extract skill rules:
- Zero hallucination policy — every fact traceable to source text
- One story = one beat
- Exhaustive lens scan per POI
- Source passage required on every beat
- Physical cues extracted separately

**After extraction, run the source passage verification pass:**
Re-read the chunk file and verify each beat's `source_passage` appears in the text (approximate match — allow for OCR artifacts). Flag any beat where the passage cannot be found.

### Step 2 — POI MATCHING + CREATION

Match extracted beats to existing POIs. For new locations:
- Web search to determine relationship (alias, child, adjacent, new)
- **Proximity check:** Before creating a new POI, check if any existing POI is within 100m. If so, add to the REVIEW QUEUE rather than auto-creating.
- Create basic POI entries for genuinely new locations

### Step 3 — FACT-CHECK (auto-correct mode)

For each beat, check key claims:
- Dates, names, attributions, superlatives, specific numbers

**Auto-correction rules:**
- ONLY auto-correct when 2+ independent sources agree on the correction
- If sources conflict → DISPUTE → goes to review queue
- NEVER auto-correct: claims about living people, superlative disputes (oldest/first/only where book and web disagree), corrections that would delete a story
- Log every auto-correction with source URLs

### Step 4 — GEOCODE NEW POIs

For any new POIs created in Step 2:
- Nominatim API lookup
- Verify coordinates are at pedestrian approach point
- Assign trigger_radius by POI type
- Flag if placement confidence is LOW

### Step 5 — GRAVITY SCORE NEW POIs

For any new POIs created in Step 2:
- Quick signal gathering (Google trends, Wikipedia depth, guidebook presence)
- Score relative to existing city POIs
- Assign importance_tier 1-5

### Step 6 — EXPORT VALIDATION

Validate all beats from this chunk:
- POI references valid
- Lens slugs valid
- No unresolved disputes (only auto-resolved or queued)
- Build export JSON file

Write to: `data/{city_slug}/export/{book_slug}-{chunk_slug}.json`

### Step 7 — UPDATE TRACKING FILES

- Append new beats to `data/{city_slug}/beats.json`
- Append new POIs to `data/{city_slug}/poi-raw.json`
- Update `data/{city_slug}/book-log.json`

---

## ANOMALY DETECTION

After all steps, flag:
- Chunk produced <3 beats (under-extraction — possible OCR issue?)
- Chunk produced >25 beats (over-extraction — check for merged stories)
- Any beat >200 words (may be merging distinct stories)
- Any beat <30 words (may be too thin — LOW confidence)
- Any auto-correction that changes >20% of the script_body
- Any source_passage that couldn't be verified in the chunk text
- Any new POI within 100m of existing POI

---

## OUTPUT — SINGLE REVIEW REPORT

Do NOT give incremental updates during processing. Produce ONE final report:

```
=== PIPELINE REPORT: {chunk_name} ===

EXTRACTION:
  Beats extracted: X
  POIs touched: X (Y new, Z existing)
  Source passages verified: X/X

AUTO-CORRECTIONS APPLIED: (list each with source URLs)
  1. "1,134 scenes" → "1,113 scenes" (Sainte-Chapelle official site)
  2. ...

REVIEW QUEUE: (items needing human decision)
  1. [DISPUTE] claim X — Source A says Y, Source B says Z. Recommendation: ...
  2. [PROXIMITY] New POI "X" is 80m from existing "Y" — create or merge?
  3. [ANOMALY] Beat Z source_passage not found in text — verify or remove?

EXPORT:
  File: {filename}
  POIs: X, Beats: X, Size: XKB
  Ready for /upload: YES/NO (NO if review queue is non-empty)

COVERAGE:
  Lenses hit: [list]
  Lenses missed: [list]
  Confidence: HIGH X, MEDIUM X, LOW X
```

Wait for the user to resolve any REVIEW QUEUE items, then finalize.

---

## SELF-VERIFICATION

Before delivering the report:

1. **Every beat has a non-empty source_passage** — no beat without textual grounding
2. **Every beat has a valid lens slug** — cross-check against `src/schema/definitions.py`
3. **Every new POI has coordinates** — lat/lng present with 6 decimal places
4. **Export file is valid JSON** — parseable, matches the schema in existing exports
5. **Export POI count matches extraction** — no POIs lost between steps
6. **Book log updated** — this chunk recorded in `book-log.json`
7. **Pipeline state set to "complete"** — `.pipeline-state.json` updated
8. **No unresolved disputes left silent** — every dispute is either in the review queue or auto-resolved with sources

---

## GUARDRAILS

Apply the 5 pipeline guardrails. They are non-negotiable.
