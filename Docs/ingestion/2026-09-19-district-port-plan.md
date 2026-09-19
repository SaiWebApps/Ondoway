# Porting the new corpus into the product — the Upper East Side district

**Written 2026-09-19 after the owner stopped the proof loop.** Supersedes the P1-atomicity
probe plan (parked, not built). Decisions below are the owner's, taken in one sitting; the
rationale for the ceiling change is `Docs/adr/0002-beat-ceiling-moves-from-storage-to-selection.md`.

## Why this exists

Seven days (2026-09-12 → 09-19) went into proving the ingestion engine against itself: slice 9's
two paid chunks, then slice 10's merge redesign, replays, atomic-claim work and two paid job-A
runs — about $28, no merge FOLD in a real job (merges were judged — 8 and 9 decided in the two slice-9 jobs — and folds happened only in the step-1 replays), and nothing reaching the app. Each run
cleared its target defect and failed a stricter gate on the next one. The gates were proxies
(R1 atomicity ≈ 96% compliance from a stochastic model) for a question nobody had yet asked of
the output: **is this corpus more trustworthy and richer than what we have?**

Meanwhile three pieces between the new beats and the app had never been built, so even a perfect
merge would have shipped nothing.

## What "done" means here

**A scratch graph holding only new-shape Upper East Side beats, and a report comparing that
corpus against the legacy one at the same POIs.** Not a merge proof, not a tour-quality verdict.

The measures (owner, 2026-09-19): **coverage of POIs, density of beats per POI, diversity of
lenses across those beats** — plus the trust properties the rebuild exists for (claims grounded
in a cited span, no lifted prose, no one fact scattered across beats).

Baseline measured 2026-09-19 over the same 12 POIs:

| | Legacy (5 books) | New (Lonely Planet chunk-07 only) |
|---|---|---|
| Beats | 140 | 42 |
| Beats per POI | 11.7 mean (2–44) | 3.5 mean (1–11) |
| Distinct lenses | 19 | 17 |
| Lens diversity per POI | 4.8 | 4.8 |

That table compares a five-book corpus against one chunk, so it measures coverage, nothing
more. The like-for-like reading — same book, same chunk, same POIs — is:

| | Legacy LP chunk-07 | New LP chunk-07 |
|---|---|---|
| Beats | 39 | 42 |
| Beats per POI | 3.2 | 3.5 |
| Distinct lenses | 15 | 17 |
| Guggenheim beats | 8 | 10 |

**Volume is at rough parity (+8%), not a density win.** An earlier draft of this plan claimed
the new pipeline was "denser per book" from a 28-per-book average; that was wrong — the 140
legacy beats split 47/37/27/24/5 across the five books, and Lonely Planet's 47 span five
chunks, not one. Read the lens rows with the tagging convention in mind: a legacy beat carries
ONE `lens` string, a new beat carries a `lenses` list averaging 2.36 entries, so per-POI
diversity parity comes from 11.7 single-lens beats against 3.5 multi-lens ones. What the new
corpus adds at parity volume is the trust: every claim carries a cited span, the narration
gate refuses lifted runs (19 refusals and rewrites in this run), and one fact is not scattered
across beats.

## The gaps between a new beat and the app (measured 2026-09-19)

Narration text already maps to what the app plays (`narration.text` → `script_body`, read by
`mobile/lib/models/trip.dart`), and `converge()` in `scripts/upload_paris.py` is a working
publish path. What does not carry:

1. **`narrative_function` is free prose** ("Sets the geography and social texture…") where
   `src/tour/beat_select.py:46-52` expects hook|establishing|deepen|climax|callback. All 42 beats sort
   last. (Kickoff defect #4, open since slice 9.)
2. **`physical_cues` changed shape** — `list[str]` against the engine's `{cue, direction,
   feature_type}`; the exporter zeroes them, so the tour's openers lose their input.
3. **`practicalities` beats are refused by the uploader** — 7 of 42 never reach the graph.
4. **`sub_location` is free prose** — see ADR-0002; it is exported and feeds the trigger.
5. **Minor losses:** claim ids, source passage/chunk and `subject_tag` are not written, which
   leaves the graph-side duplicate lane inert.

## The plan

Rigor: **Tier 1 for $0 code** (test-first, undo-test, `make lint` by exit code, judge consult
before each commit). Full Judge Protocol for anything paid, any write outside the scratch graph,
any deletion, and any claim of proof. Owner go before every paid step.

1. **Close gaps 1–4** ($0, test-first, one commit each).
2. **Stand up the scratch graph** ($0): a spare local Neo4j, POIs seeded from `poi-raw.json`,
   the 42 new beats published as the WHOLE city file — existing publish semantics withdraw
   everything else, so no partial-swap code is needed and the dev graph is never touched.
3. **The corpus comparison report** ($0): coverage, density, lens diversity, and the trust
   properties, legacy against new, at the same 12 POIs. This is the deliverable.
4. **Then throughput** — the parked transport design
   (`Docs/ingestion/2026-09-14-ingest-transport-and-resume-design.md`), taken up BEFORE any bulk
   run. At the current shape the full 386 chunks are ~$1,000–2,300 and on the order of 1,000
   hours of sequential batch waiting; wall-clock, not money, is the binding constraint.
5. **Then the second book** (Frommer's `chunk-05-ch05-uptown`), which is also the merge test:
   fold when confident, queue the doubtful for human review, never block a publish, and report
   the duplicate rate with `make claim-conflicts`.

## What this plan deliberately does not do

- It does not gate on the Guggenheim merge. R0–R7 stay recorded as the registration they were;
  R1's failure stands (`rebuild-spec.md`, job A re-run entry).
- It does not chase P1 compound claims. The probe plan is parked; compounds become review-queue
  material and a measured rate.
- It does not judge tour quality. The owner's position: the tour engine is already good; this
  work is the plumbing that feeds it trustworthy data.
- It does not build a partial-city swap. The owner intends to replace every legacy beat, so the
  eventual path is a full-city publish, which already works.

## Carried defects (measured, not fixed)

False omission findings producing duplicate claims; the claim re-ask writing "According to the
passage" past the leak gate; the Museum of the City of New York story placed at "Upper East
Side" although that POI exists; the Met Breuer snapped to the Met; `’`/`–` span corruption;
claim-id collisions across jobs; literal `\u` escapes in narration; `kid_friendly`/`entities`
defects. All are review-queue or later-slice work under this plan.
