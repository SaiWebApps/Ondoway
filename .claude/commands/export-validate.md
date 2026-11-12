You are a data engineer preparing content for database upload. You validate pipeline data, combine POIs with their beats, and produce a single upload-ready JSON file per book chunk.

Your task: validate and export content for **$ARGUMENTS**.

Parse the arguments:
- City name is required
- Optionally specify a chunk: `Paris chunk-01` exports only beats from that chunk
- If no chunk specified, export ALL beats grouped by source chunk into separate files

---

## PURPOSE

The pipeline produces two separate files — `poi-raw.json` (POIs) and `beats.json` (beats). This skill combines them into upload-ready files where beats are nested under their POIs, in the export format shown in Phase 3. Each export file corresponds to one book chunk, keeping file sizes manageable.

---

## INPUT

1. POIs from: `data/{city_slug}/poi-raw.json`
2. Beats from: `data/{city_slug}/beats.json`
3. Schema definitions from: `src/schema/definitions.py`
4. Book log from: `data/{city_slug}/book-log.json`

---

## PHASE 1 — VALIDATION

Before exporting, validate everything.

### POI validation
For each POI that has beats, verify:
- `name` is non-empty
- `latitude` and `longitude` are present and in range
- `importance_tier` is 1-5
- `kid_friendly` is "yes" or "no"

### Beat validation
For each beat, verify:
- `script_body` is non-empty
- `poi_name` matches an existing POI
- `lens` is a valid slug from `TAGGABLE_LENSES` in definitions.py
- `fact_check.status` is not `disputed` (must be resolved before export)

### Estimate missing fields
- If `duration_sec` is missing on a beat, calculate: `word_count / 2.5`, rounded to integer
- If `kid_friendly` is missing on a beat, default to "yes"
- If `typical_duration_min` is missing on a POI, estimate by type:
  - Major museum (importance_tier 4-5): 90 min
  - Small museum: 45 min
  - Church/cathedral: 20 min
  - Park/garden: 45 min
  - Monument/bridge/square: 10 min
  - Cafe/restaurant/shop: 5 min
  - Cemetery: 45 min
  - Street/area: 20 min
  - Default: 30 min

**If any blocking errors exist, STOP and report them. Do not export.**

---

## PHASE 2 — GROUP BY CHUNK

Read `book-log.json` to determine which beats came from which book/chunk. Group beats by their `source_attribution.book_title` + chunk.

If beats don't have source attribution (e.g., beats created by `poi-generate` or other non-book skills), group them into a separate "pipeline-generated" export.

---

## PHASE 3 — BUILD EXPORT FILES

For each chunk, build a combined JSON file. Only include POIs that have beats from this chunk. Each POI includes ALL of its schema fields plus its beats nested inside.

### Export format

```json
[
  {
    "name": "Louvre Museum",
    "short_description": "World's largest art museum...",
    "latitude": 48.860900,
    "longitude": 2.335800,
    "importance_tier": 5,
    "trigger_radius": 10,
    "typical_duration_min": 90,
    "kid_friendly": "yes",
    "name_variations": ["Musee du Louvre", "Le Louvre"],
    "parent_poi": null,
    "beats": [
      {
        "script_body": "The Louvre began not as a museum but as a military fortress...",
        "lens": "war_conflict",
        "duration_sec": 56,
        "kid_friendly": "yes",
        "physical_cues": [],
        "source_passage": "he erected the Louvre on the right bank...",
        "source_attribution": {
          "book_title": "Around and About Paris",
          "author": "Thirza Vallois",
          "chapter": "The 1st Arrondissement",
          "page": "Overview"
        },
        "fact_check_status": "verified"
      }
    ],
    "_meta": {
      "exported_at": "ISO 8601",
      "source_chunk": "chunk-01-1st-arr-overview-and-les-halles.txt"
    }
  }
]
```

### What to include on each beat:
- `script_body` — the content (goes into NarrativeBeat node)
- `lens` — which lens to create TAGGED_WITH relationship
- `duration_sec` — estimated speaking time
- `kid_friendly` — per-beat override
- `physical_cues` — kept for the tour builder (stored separately from Neo4j if needed)
- `source_passage` — kept for audit/verification
- `source_attribution` — kept for provenance tracking
- `fact_check_status` — simple status string (verified/corrected/unverified)
- `entities` (list[str] — from enrichment)
- `sensory_anchor` (bool — from enrichment)
- `duration_sec` (int — from enrichment, computed as word_count / 2.5)
- `narrative_function` (str — from enrichment)
- `beat_type` (str — from enrichment)
- `emotional_register` (str — from enrichment)

### What to include on each POI:
- All `POICreate` schema fields (name, short_description, lat, lng, importance_tier, trigger_radius, typical_duration_min, kid_friendly, name_variations)
- `poi_role` (str — stop/setting/walk_by_only)
- `parent_poi` — for CONTAINS_POI relationship
- Nested `beats` array
- `_meta` with export timestamp and source chunk

### What to strip:
- `_pipeline` block (gravity_audit, geocode_audit, discovery_sources, etc.)
- `beat_id` (pipeline identifier)
- `key_claims` (pipeline dedup field)
- `confidence` (pipeline quality signal)
- `related_beats` (pipeline cross-reference)
- Full `fact_check` object (replaced by simple `fact_check_status` string)

---

## PHASE 4 — WRITE FILES

Write each chunk's export to: `data/{city_slug}/export/`

Naming: `{book_slug}-{chunk_slug}.json`
Example: `around-and-about-paris-chunk-01-1st-arr-overview-and-les-halles.json`

---

## REPORT

Present to the user:

1. **Validation summary:** POIs valid, beats valid, errors, warnings
2. **Export files created:** One line per file with POI count, beat count, file size
3. **POIs with no beats:** List any POIs that exist in poi-raw.json but have zero beats (not exported — they need content)
4. **Upload readiness:** READY or BLOCKED

---

## SELF-VERIFICATION

Before writing:

1. **Every exported POI has at least one beat** — empty POIs are not exported
2. **Every beat references a valid lens** from TAGGABLE_LENSES
3. **No unresolved disputes** in fact_check
4. **Beats are nested under the correct POI** — poi_name matches
5. **All schema fields present** on POIs and beats
6. **Valid JSON** — properly formatted and escaped
7. **File sizes reasonable** — flag if any export file exceeds 1MB
