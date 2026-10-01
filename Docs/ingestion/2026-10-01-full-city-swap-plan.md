# New York full-city swap — plan (drafted 2026-10-01, NOT approved)

The owner intends to replace every legacy New York beat ("I cant trust what is out there",
2026-09-19). This is how, in the order that keeps each step cheap to stop.

## What is being replaced

- Legacy: `data/new_york/beats.json`, 2,023 beats over 397 places, five books plus the movie
  guide, plain-string `key_claims`, 149 of 192 beats at the UES places carrying an 8-word run
  from their own chunk.
- New: one new-shape file built book by book from the same six sources, every claim with a cited
  span and a judged verdict, narration gated against the source chapter.
- The publish path already works: a full-city `converge` makes the graph match the file and
  withdraws every beat the file no longer names (district-port plan, owner decision 2).

## The source, measured 2026-10-01

| Book | Chunks | Words | Median chunk | Largest chunk |
|---|---|---|---|---|
| Big Onion (ten historic tours) | 20 | 190,442 | 8,374 | 13,427 |
| Frommer's NYC 2024 | 12 | 59,744 | 4,457 | 9,822 |
| Lonely Planet NYC | 15 | 70,402 | 3,863 | 10,082 |
| Moon NYC Walks | 7 | 24,000 | 3,754 | 3,972 |
| National Geographic Walking NYC | 18 | 62,214 | 3,502 | 4,065 |
| New York Movie Lover's Guide | 26 | 243,254 | 8,163 | 18,129 |
| **Total** | **98** | **~650,000** | | |

Every job so far ran on ~3,400-word chunks. P1 answers a whole chunk in one call (214 claims from
3,377 words); an 18,000-word chunk would ask for ~1,100 claims in one answer, near the 64k
`P1_MAX_TOKENS` cap. **Chunks above ~5,000 words are re-split first** (`/book-prep`, $0 API):
54 of the 98 chunks (measured), giving ~213 chunks of ~3,500 words, the size every measurement
comes from.

## Cost and time, from what was measured (projections, not promises)

- Cost: full chunk from scratch $2.61 (LP chunk-07) and ~$3.91 (Frommer's chunk-05), both
  ~3,400 words → **$0.80-1.15 per 1,000 words → ~$520-750 for 650k words**. P6 grows with the
  corpus (the Met already has 15+ beats to judge against), so plan **$600-1,000**.
- Time: one chunk took 35 min to 3 h 41 m; the Batch queue sets it (r5 waited 33 min on one
  round). Jobs into one city file must run one after another. ~213 chunks at 35 min-2 h each is
  **~5-18 days of unattended wall clock**; plan in rounds, not hours (district-port plan). The
  pilot measures the real per-chunk figure at book scale.

## The plan

**Step 0 ($0).** Re-split the oversized chunks; record each book's chunk list in its manifest.
Decide the book order (below). `make test` green, or the known-RED list re-checked, before any
publish to 7687.

**Step 0.5, what the rules lose today (measured 2026-10-01, `make ingest-job-summary`).** Job-B
replay r5 (Frommer's chunk-05 into the UES corpus): **50 stories produced, 41 written, 9 left out
(18%)** — claim check (P3) 4, copying gate 3, arc minimum 2. Two of the four claim-check losses are
`practicalities` (never published), so the publishable loss is about 7 in 46 (15%). The claim
check's refusals read as over-strict: "a famed dollhouse" refused as "adds that it is noted for
its fame"; "$25 for adults" refused as "adult admission"; "1071 Fifth Ave." refused for "adding
the street name Fifth". Before the pilot: read P3's refusals across the jobs on record ($0) and
put 15-20 of them in front of the owner; the owner rules on the claim check with that evidence.

**Step 1, the pilot (~$20-30, owner go on the printed estimate).** One whole small book — Moon
(7 chunks, 24k words) — into an EMPTY new-shape city file in a sandbox. It proves what nothing has
yet: a 7-unit job end to end (only 2 units ever ran), cost per 1,000 words at book scale, wall
time per chunk, and the story-fate line (`stories: … left_out=… by_rule:`). **Stop condition:**
left_out above ~5% of produced stories, or one rule (likely the copying gate on names) causing
most of the losses — then the owner rules on that rule with real numbers before any more spend.

**Step 2, the books, one job each (owner go per book on its printed estimate).** Into the same
sandbox file, in order. Recommended order: Frommer's 2024 (newest, broad), Lonely Planet,
National Geographic, Moon (already in from the pilot), Big Onion (history depth), the Movie
Lover's Guide last (niche; heaviest). The first book to state a fact gives its wording; later
books fold into it, so the broad modern guides go first. After each book: story fates,
`make ingest-validate`, `make ingest-address-report`, `make b8-dedup-report`,
`make claim-conflicts`, R0 sha check and rsync, exactly as for every paid run so far.

**Step 3, the scratch publish ($0).** The whole new file to the scratch graph (dev3, :7693), then
`make ingest-corpus-compare` city-wide against legacy (coverage, beats per place, copying), and a
real tour built from it on a few districts. The owner looks at the tours before anything moves.

**Step 4, the swap (owner sign-off).** Publish to the dev graph (7687), run the NYC tour shards
that are RED by design until now, then — a separate decision — cloud (`TARGET=cloud
CONFIRM_CLOUD_WRITE=1`).

## Decisions owed by the owner before step 2

1. Book order and inclusion (is the Movie Lover's Guide in?).
2. **Source provenance.** Parked on 2026-09-30, but the swap publishes these books' content into
   the product: 12 of the manifests name Anna's Archive or z-library while the records say
   `owned_copy`. Worth settling before the cloud publish in step 4, not before the pilot.
3. D9 "the newer book wins on a present-tense fact" (Frick beaux-arts → neoclassical; Met 1,700 →
   700 canvases): keep, or keep both and narrate that sources differ.
4. The copying gate on name-heavy phrases — decided on the pilot's numbers, not before.

## Not in this plan

Paris (a separate swap). The `claim_uid` work (plan step 4a), which waits for a real need at scale.
Re-narrating the six legacy beats that carry literal `\u` escapes (they are replaced by the swap).
