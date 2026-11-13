# Ondoway — Product Context Reference

This file captures the stable product knowledge that informs all 5 PM modes. Load this when you need deep context beyond what SKILL.md provides.

## North Star

Ondoway is a multi-city audio tour platform that transforms books and travel guides into GPS-triggered, narrative-driven walking experiences — making every city feel like it has a local storyteller in your pocket.

## 12-Month Bets

1. **Prove the Book-to-Street pipeline:** Ship Editorial Workbench + AI ingestion engine → 100 live GPS-tagged story beats for a single city, passing the Boredom Test and 3-minute editorial efficiency target.
2. **Deliver the Wanderer experience end-to-end:** Mobile app with automatic narrative beat triggers within 10m of each POI, using Golden Ratio algorithm (20% Anchors / 80% Flavour).
3. **Scale to multi-city:** Neo4j graph schema + ElevenLabs audio pipeline + S3/Postgres storage to onboard cities without rebuilding the content pipeline.

## Two Core Product Modes

- **The Planner (Structured Discovery):** Trip → ItineraryItems → POIs → Beats → Lenses
- **The Wanderer (Spontaneous Exploration):** Profile → preferred Lenses → matching Beats → nearby POIs + spatial filter

## Scoreboard (MVP Metrics)

| Metric | Target |
|--------|--------|
| Synthesis Density | AI extracts ≥70% of viable POIs from source text |
| Editorial Efficiency | Editor verifies one AI beat in <3 min |
| Narrative Quality | Beat passes internal Boredom Test |
| Trigger Reliability | Audio triggers within 10m radius during live walk |
| GPS Accuracy Threshold | Warn user if accuracy >25m |

## Resolved Technical Decisions (Locked for MVP)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Audio engine | ElevenLabs Conversational AI | ~$15–20 per 60-min tour, best narrative quality |
| Audio chunking | AI timestamps method | S3 storage + Postgres index |
| Audio format | MP3, 256kbps, Stereo, 44.1kHz | Stored on AWS S3 |
| Geocoding (MVP) | OpenStreetMap (free) | Switch to Google Maps at launch |
| Geocoding confidence | Auto-flag <70% | Show confidence to editors |
| GPS precision | 6 decimal places (~10cm) | GeoPoint on Neo4j POI node |
| GPS warning | >25m accuracy | Default; revisit after field test |
| GPS strategy | Adaptive polling | High-freq near POIs, low between. 10–20% battery/60–90 min |
| Editorial map | OpenStreetMap/Leaflet | Editor pin drag-and-drop |
| Database | Neo4j graph (Schema v3) | 3 domains: Vault, Atlas, Bridge |
| Geofence radius | 10m fixed | Consistent across all POIs for MVP |
| Gravity Score | Quantitative pipeline | 5 signals: visitor stats, Google reviews, Google Trends, Wikipedia, guidebook. POI-level, relative to city |
| Lens system | 8 parents, 21 children | Parents are universal genres. 21 universal children. No city-specific lenses for MVP |
| Beat duration | Gravity × 60 seconds | Gravity 5 = 300 sec max |
| Beats per lens | Multiple allowed | Tour builder selects best fit at runtime. No cap on extraction |
| MVP scope | Single city, 100 POIs | Content library — extract all beats, tour builder curates |

## Current Stack

| Layer | Technology |
|-------|------------|
| Database | Neo4j Graph Database (Schema v3) |
| Audio generation | ElevenLabs Conversational AI |
| Audio storage | AWS S3 + Postgres (timestamp index) |
| Geocoding (MVP) | OpenStreetMap |
| Geocoding (launch) | Google Maps API |
| Editorial map | OpenStreetMap / Leaflet |
| Mobile framework | Expo (TypeScript) |
| Backend | Supabase (Postgres + Auth + Edge Functions) |
| Cache | Upstash Redis |
| AI | Gemini API |
| File uploads | Max 50MB, PDF 1.4+, EPUB 2.0/3.0 |

## Neo4j Domain Architecture

### Domain 1: The Traveler's Vault
- **User** — auth & account basics
- **Profile** — persona "bubbles" linked to devices (enables family sharing)
- **Trip** — master trip container with owner + members

### Domain 2: The Global Atlas
- **POI** — physical locations with geofencing and metadata
- **NarrativeBeat** — versioned story content with audio
- **Lens** — thematic layers for interest-based filtering (12 lenses for MVP)

### Domain 3: The Execution Bridge
- **ItineraryItem** — per-device content assignments with scheduling

### Domain 4: Gamification & Rewards (Post-MVP)
- Badges, achievements, challenge completions

## Lens Hierarchy

**8 Parent Lenses (universal genres — never change):**

| Parent | What it covers |
|--------|---------------|
| History | Events, conflicts, political turning points, social movements |
| Architecture & Design | Built environment — ancient to modern |
| Arts & Culture | Visual art, music, performance, street art, cinema |
| Food & Drink | Culinary traditions, markets, historic restaurants |
| Stories & Characters | Folklore, literary connections, film locations, famous residents |
| Faith & Spirituality | Sacred sites, religious traditions, pilgrimage |
| Nature & Landscape | Parks, gardens, waterways, viewpoints |
| Commerce & Innovation | Markets, shopping, science, technology, trade |

**21 Universal Children** (exist in every city — see `definitions.py` for full list)

**City-specific lenses:** Deferred post-MVP. Thematic tour grouping (e.g., "French Revolution tour") will be handled by the tour builder using semantic content matching, not pre-assigned lens labels.

## Golden Ratio Algorithm

- 20% Anchors (Gravity 5): Major sights as waypoints
- 80% Flavour (Gravity 1–4): Local connective tissue between anchors
- Start at highest-Gravity POI in radius
- End with a "bang" at a high-impact location
- Linear tour (not circular)
- Dynamic resequencing if user deviates from route

## Content Pipeline (Book-to-Street)

1. **Source Ingestion** — PDF/EPUB upload
2. **AI Deconstruction** — entity extraction, gravity scoring, lens mapping, story beat generation, narrative styling
3. **Editorial Workbench** — staging area, split-screen editor, map pin validation, gravity/lens confirmation
4. **Commit to Live** — staging → production database
5. **Audio Generation** — fallback TTS at ingestion; NotebookLM-style tour audio on-demand when user starts tour

## MVP Scope Boundaries

**IN:** Single city, 100 POIs, Book-to-Street pipeline, Wanderer mode, email/password auth, graceful degradation, Golden Ratio algorithm

**OUT:** Social/sharing, payments, turn-by-turn nav, multi-language, POI photos, full offline packs, haptic feedback, gamification nodes

## Content Pipeline — Skill Execution Order

Skills must run in this order. Each step depends on the output of the previous.

```
Phase 1 — Discovery (run once per city)
  1. /poi-generate {city}          → creates data/{city}/poi-raw.json
  2. /poi-dedup {city}             → deduplicates + tags parent-child
  3. /poi-gravity {city}           → assigns importance_tier 1-5
  4. /poi-geocode {city}           → adds lat/lng + trigger_radius

Phase 2 — Content (run per book)
  5. /book-prep                    → chunks book into processable sections
  6. /unified-beat-extract {city}  → extracts + classifies beats per chunk (repeat per chunk)

Phase 3 — QA (run after all content is extracted)
  7. /fact-check {city}            → verifies beats + POI status

Phase 4 — Export (run when ready to upload)
  8. /export-validate {city}       → validates against Neo4j schema, strips _pipeline fields
```

Phase 2 can be repeated for additional books. Phase 3 should re-run after new content is added. Phase 1 skills can be re-run to add new POIs (poi-generate merges, poi-gravity supports --rescore).

## Team

- **Adam** — Backend/AI
- **Sairam** — Mobile/Frontend

## Timeline

10 weeks to MVP launch. Paris first, then NYC and London post-MVP.
