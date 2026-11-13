You are a database operations engineer. You upload validated pipeline content into Neo4j via the FastAPI backend.

Your task: upload export data for **$ARGUMENTS**.

Parse the arguments:
- City name is required
- Optionally specify a file: `Paris around-and-about-paris-chunk-01.json`
- **Default: DELTA upload only.** If no file specified, upload only chunks whose content is not already fully represented in the DB. Determine "new" by checking `data/{city_slug}/book-log.json` against the DB: a chunk is considered new if its entry's `processed_at` timestamp is newer than any of its POIs' `created_at` in the DB, OR if any of its POIs are missing from the DB entirely, OR if its beats count doesn't match what's in the DB for that chunk's POIs.
- **`--all` flag:** upload every file in `data/{city_slug}/export/`. Only use when the user explicitly asks (re-upload / recovery scenarios). WARNING: this re-MERGEs every POI and may overwrite manual field edits made directly in the DB.
- Simpler heuristic (preferred when book-log has clear "processed_at" stamps): upload only chunks with `processed_at` after the most recent `created_at` on any POI in the DB. This catches the common case of "I just ran /pipeline-batch, now upload what's new."

When in doubt about which chunks qualify as delta, show the user the proposed file list and ask before proceeding.

## PRE-FLIGHT: Validate beats.json (HARD-BLOCK)

Before anything else, run the dedup validator on the city's beats.json:
```
.venv/bin/python scripts/validate_beats.py data/{city_slug}/beats.json
```
If exit code is non-zero, STOP. Print the validator's output verbatim and do
not proceed to any other step. There is no warn-and-continue mode — duplicate
or hash-colliding beats are a hard block. Override only by fixing
`beats.json` (e.g. via `/beat-wipe` then re-extract, or a manual delete) and
re-running this skill from the top.

## PRE-FLIGHT: Run regression tests

Once the beats validator passes, run:
```
.venv/bin/python -m pytest tests/test_export_consistency.py tests/test_gravity_distribution.py tests/test_lens_drift.py -v
```
ALL must pass. If any fails, STOP and tell the user what's wrong. The most
common cause is that someone updated `poi-raw.json` (e.g. via `/poi-gravity`)
but forgot to sync the export files. The test message will say which file
and which field is out of sync.

## CANONICAL FIELDS COME FROM poi-raw.json, NOT THE EXPORT FILE

When building POI payloads to send to the API, ALWAYS read these fields from
`data/{city_slug}/poi-raw.json` rather than from the export chunk file:

- `importance_tier`
- `latitude`, `longitude`
- `trigger_radius`
- `name_variations`
- `short_description`
- `typical_duration_min`, `visit_seconds_inside`, `visit_basis` — the visit
  capacities written by `/poi-visit-duration`. Same reason as `importance_tier`:
  the capacity pass writes `poi-raw.json` and syncs the exports afterwards, so an
  export chunk written before that pass carries stale or absent capacities.

The export file is the source of truth ONLY for `beats[]`. Defense in depth:
even if a sync step is skipped upstream, this rule prevents stale tier data
from reaching Neo4j.

---

## CRITICAL SCHEMA NOTES

Read these before writing any upload code. These come from inspecting the actual database layer.

**Relationship types — what goes where:**
- `HAS_BEAT` = POI → NarrativeBeat (POI "owns" its beats). This is what we create at upload time.
- `PLAYS_BEAT` = ItineraryItem → NarrativeBeat (a tour stop plays a beat). This is created when tours are assembled — NOT at upload time.
- `TAGGED_WITH` = NarrativeBeat → Lens (beat is tagged with a lens).
- `IS_PARENT_OF` = Lens → Lens (parent-child lens hierarchy). Also POI → POI for parent-child POIs, but this relationship type may not exist yet in the schema — check before attempting.

**Built-in idempotency:**
- POI creation uses `MERGE (n:POI {name: $name, city_name: $city_name})` — if a POI with this name+city exists, it UPDATES its fields rather than creating a duplicate. Multi-city safe (Notre-Dame Paris vs Notre-Dame Reims don't collide). Re-uploading the same chunk overwrites POI fields.
- NarrativeBeat creation uses `MERGE (n:NarrativeBeat {script_body: $script_body})` — exact duplicate beats are prevented at the database level. No need for manual dedup.

**Seed code pattern (src/seed/narratives.py):**
The existing seed code creates beats like this:
```cypher
MATCH (p:POI {name: $poi_name, city_name: $city_name})
MERGE (p)-[:HAS_BEAT]->(b:NarrativeBeat {script_body: $script_body})
SET b.id = coalesce(b.id, randomUUID()), ...
```
This atomically creates the beat AND the HAS_BEAT relationship in one query. If the beat already exists at this POI, it's a no-op.

---

## PREREQUISITES

Run these checks before any upload:

1. **Backend running** — `GET http://localhost:8000/api/v1/nodes/Lens?limit=1` returns 200
2. **Lens nodes seeded** — `GET /api/nodes/Lens?limit=200` returns 29+ nodes. If not: "Run `python -m src.seed.runner` to seed lenses."
3. **Export file(s) exist** — verify file is valid JSON array
4. **Validate lens slugs** — parse every beat's `lens` field from the export. Query all Lens nodes. Every lens slug must have a matching Lens node. If ANY is missing, STOP.
5. **No empty script_body** — every beat has non-empty `script_body`

---

## UPLOAD STRATEGY

### Approach: Use the API endpoints, matching the seed code patterns

The FastAPI `POST /api/nodes/POI` already uses MERGE on name. `POST /api/nodes/NarrativeBeat` already uses MERGE on script_body. We leverage this built-in idempotency.

### Step 1 — Load all existing data

Fetch all POIs from Neo4j (paginate to get all):
```python
all_db_pois = []
skip = 0
while True:
    resp = requests.get(f"{API}/nodes/POI?skip={skip}&limit=200")
    data = resp.json()
    all_db_pois.extend(data['items'])
    if skip + 200 >= data['total']:
        break
    skip += 200
```

Build lookup: `{name.lower(): poi}` and `{variation.lower(): poi}` for matching.

Fetch all Lens nodes and build: `{name: lens}`.

### Step 2 — Match/create POIs

For each POI in the export file:

1. **Match by exact name** against DB POIs (case-insensitive)
2. **Match by name_variations** — check incoming name against DB name_variations, and incoming variations against DB names
3. **If matched:** Record the DB POI's `id`. Do NOT call the API (avoids MERGE overwriting fields).
4. **If new:** Call `POST /api/nodes/POI` with the fields below (canonical values from `poi-raw.json` per the rule above). The MERGE creates it. Record the returned `id`.

**FORWARD (tour-essential):**
- `name` — required
- `city_name` — required, part of MERGE key
- `latitude`, `longitude` — required
- `importance_tier` — required, no default
- `short_description`
- `trigger_radius`, `typical_duration_min`
- `visit_seconds_inside` — seconds spent INSIDE; `null` where there is no
  interior (street, bridge, square). Note the unit split: `typical_duration_min`
  is MINUTES outside, this one is SECONDS inside.
- `visit_basis` — the sentence arguing for both capacities. Unlike the POI
  `source_passage` below, this is our own generated reasoning, not book text, so
  it is safe to forward — and it is the only audit trail a capacity has.
- `kid_friendly`
- `name_variations`
- `poi_role` — stop | setting | walk_by_only
- `parent_poi` — sub-POI parent name (creates IS_PARENT_OF in Step 5)
- `establishing_not_applicable` — tour-builder flag

**DO NOT FORWARD (provenance / copyright-sensitive):**
- `source_passage` — verbatim book quote (the `POICreate` model accepts it,
  but we deliberately strip it at upload to keep DB free of source text)
- `_meta`, `_pipeline`, `_poi_role_reasoning`, `source_chunk` — pipeline metadata

### Step 3 — Create beats + HAS_BEAT relationships

For each beat at each POI, call `POST /api/nodes/NarrativeBeat` with the
**tour-essential** fields below. Read every field from the city's
`beats.json` (canonical source) — the export file is fine for new beats not
yet merged in, but `beats.json` wins on conflict.

**FORWARD (tour-essential):**
- `script_body` — required
- `duration_sec` — from export, or calculate: word_count / 2.5
- `est_spoken_seconds` — pacing
- `kid_friendly` — default "yes"
- `entities` — list[str]
- `sensory_anchor` — bool
- `narrative_function` — str
- `beat_type` — str
- `emotional_register` — str
- `subject_tag` — theme picker
- `sub_location` — within-POI spatial tag (façade, crypt, nave, ...)
- `trigger_address` — address-level GPS trigger
- `beat_length_class` — anchor | mid | seasoning | micro
- `physical_cues` — list of `{cue, direction, feature_type}` objects
- `pronunciation` — phonetic approximation for TTS
- `inline_foreign_phrases` — list of `{phrase, gloss}` objects

`physical_cues` and `inline_foreign_phrases` arrive as list-of-dict; the API
JSON-encodes them on write and decodes on read — pass them through verbatim.

**DO NOT FORWARD (provenance / copyright-sensitive — keep in pipeline files only):**
- `source_passage` — verbatim book quote
- `source_attribution` — book/author citation
- `book_slug`, `source_chunk_slug` — beat-to-book linkage
- `key_claims`, `fact_check`, `source_passage_verified`, `related_beats`, `confidence` — internal QA
- `script_body_hash` — pipeline dedup only; DB has its own MERGE key on `script_body`
- `_meta`, `_enrichment`, `_fixup_notes` — internal pipeline metadata

These fields stay in `data/{city_slug}/beats.json` for traceability. They
must never reach Neo4j.

Record the returned beat `id`.

Then create the `HAS_BEAT` relationship:
`POST /api/edges/HAS_BEAT` with:
- `source: {label: "POI", id: poi_id}`
- `target: {label: "NarrativeBeat", id: beat_id}`

**Note:** If the beat already exists (MERGE matched on script_body), the API returns the existing beat's ID. Creating the HAS_BEAT relationship again is idempotent if Neo4j uses MERGE for edges too. If the API uses CREATE for edges, check if the relationship exists first to avoid duplicates.

### Step 4 — Create TAGGED_WITH relationships

For each beat created/matched in Step 3:

Look up the Lens node ID from the lens lookup.
`POST /api/edges/TAGGED_WITH` with:
- `source: {label: "NarrativeBeat", id: beat_id}`
- `target: {label: "Lens", id: lens_id}`

Same idempotency consideration as HAS_BEAT.

### Step 5 — Parent-child POI relationships (if supported)

Check if the schema supports POI → POI relationships. Look for `IS_PARENT_OF` or `CONTAINS_POI` in the RelType enum.

For each POI with `parent_poi` set:
- Find parent POI by name in the lookup
- If parent exists AND relationship type exists: create the relationship
- If parent not found: log warning
- If relationship type doesn't exist: log warning and skip all parent-child. Tell the user the schema needs updating.

### Step 6 — Areas + WITHIN edges + Area-attached beats

Areas describe the spatial container hierarchy (city → district → neighborhood/island/corridor). They are uploaded once per city and re-uploaded only when boundaries change. Run this step *before* Steps 1–5 on a cold city, or *after* Step 5 on subsequent runs (MERGE-based, idempotent either way).

**6a. Upload Area nodes** — read `data/{city_slug}/areas.json`. For each entry:
- Build the WKT POLYGON via `src.utils.spatial.simplify_polygon` + `coords_to_wkt`. OSM-sourced areas read their cached boundary from `data/{city_slug}/boundaries/<osm_relation_id>.json`; manual ones use the embedded `manual_boundary`.
- POST `/api/v1/nodes/Area` with `{name, area_type, city_name, boundary, centroid_lat, centroid_lng, short_description}`. The API MERGEs on `(name, area_type, city_name)` — multi-city safe.
- The reference orchestrator is `scripts/create_paris_areas.py`; reuse it for any city by switching the `AREAS_FILE` constant.

**6b. Upload WITHIN edges** — read `data/{city_slug}/within_edges.json` (produced by `scripts/generate_within_edges.py`). Two kinds:
- **POI→Area:** for each `poi_to_area` entry, look up POI by `(name, city_name)` and Area by `(name, area_type, city_name)`, then POST `/api/v1/edges/WITHIN`. The API MERGEs WITHIN edges automatically.
- **Area→Area:** for each `area_to_area` entry (parent_area chain), POST `/api/v1/edges/WITHIN` from child Area to parent Area. Same MERGE behavior.

**6c. Area-attached beats** — beats whose source content is genuinely district-level (etymology, neighborhood typology) attach to the Area, not a POI. In `data/{city_slug}/beats.json`, these have `poi_name` set to the Area name (e.g. `"Marais"`). Process:
- Look up the corresponding Area in Neo4j by `(name, area_type, city_name)`. Confirm the canonical Area name matches (e.g. beats reference `"Marais"`, but the Area is `"Le Marais"` — pipeline output may use a non-canonical alias; resolve via your areas.json metadata or a per-city alias map).
- MERGE the NarrativeBeat (POST `/api/v1/nodes/NarrativeBeat`).
- POST `/api/v1/edges/HAS_BEAT` from the **Area** to the NarrativeBeat. The HAS_BEAT relationship type accepts any source label per Schema v3.
- POST `/api/v1/edges/TAGGED_WITH` from the NarrativeBeat to the Lens.

Track Area-attached beats separately in the report so reviewers can confirm they were not orphaned to a phantom POI.

After 6a–6c, audit via Cypher: every Area has ≥1 inbound WITHIN (POI or sub-Area), every POI in the city has ≥1 outbound WITHIN edge, and the Area→Area graph is acyclic with `Paris` (or the launch city) as the only root.

---

## CONFIRM BEFORE PROCEEDING

After prerequisites pass, show:

```
Ready to upload: around-and-about-paris-chunk-01.json

  POIs in file: 8
    New (will create): 3 — Les Halles, Pavillon de Flore, Arenes de Lutece
    Existing (will skip POI update): 5 — Louvre Museum, Saint-Eustache, ...

  Beats in file: 26
    (DB dedup via MERGE — duplicates auto-skipped)

  Relationships to create:
    HAS_BEAT: up to 26
    TAGGED_WITH: up to 26

Proceed?
```

---

## ERROR HANDLING

- If a beat creation fails: log error, continue with next beat
- If a relationship creation fails: log error, continue
- After all steps: report failures and orphans
- The MERGE-based approach means partial failures are recoverable — re-running the upload will pick up where it left off

---

## REPORT

```
=== UPLOAD COMPLETE ===
File: around-and-about-paris-chunk-01.json

POIs:
  Created: 3 (Les Halles, Pavillon de Flore, Arenes de Lutece)
  Matched (skipped): 5 (Louvre Museum, Saint-Eustache, ...)

Beats:
  Created: 24
  Already existed (MERGE matched): 2
  Failed: 0

Relationships:
  HAS_BEAT: 26 (24 new, 2 already existed)
  TAGGED_WITH: 26 (24 new, 2 already existed)
  Parent-child: skipped (relationship type not in schema)

Warnings: (none)
Errors: (none)
```

---

## SAFETY RULES

- **No destructive operations** — only creates, never deletes or updates
- **Skip POI updates for existing POIs** — do not call the API for matched POIs (avoids MERGE overwriting their fields)
- **MERGE handles beat dedup** — the database prevents exact script_body duplicates automatically
- **Confirm before proceeding** — always show the plan and wait for approval
- **Re-runnable** — running twice on the same file is safe due to MERGE idempotency

---

## SELF-VERIFICATION

Before reporting completion:

1. **Pre-flight tests passed** — `test_export_consistency.py`, `test_gravity_distribution.py`, and `test_lens_drift.py` all green
2. **Every POI in the export was sent** — count of API calls matches count of POIs in the file
3. **Every beat was sent** — count of HAS_BEAT relationships matches total beats in the file
4. **No HTTP errors** — all API responses were 2xx
5. **MERGE didn't overwrite** — existing POIs were skipped, not updated
6. **Report matches reality** — created/skipped/failed counts in the report add up to the total
