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
2. **`physical_cues` and `pronunciation` are NOT exported — deliberately.** `_export_view`
   (`upload_paris.py:445-447`) zeroes both because unjudged author enrichment is not served
   (proof-chunk panel, 2026-09-13). POLICY, not a defect; changing it needs a judging step and
   an owner ruling. The shape also differs (`list[str]` against the engine's
   `{cue, direction, feature_type}`), which is moot while the field is withheld.
3. **`practicalities` beats are fenced at publish — deliberately.** `_beat_blocked`
   (`upload_paris.py:105-111`), owner ruling 2026-09-13: prices and hours wait for an engine
   channel that does not voice them in the bare present. 7 of 42. POLICY, not a defect.
4. **`sub_location` is free prose** — see ADR-0002; it is exported and feeds the trigger.
5. **Minor losses:** claim ids, source passage/chunk and `subject_tag` are not written, which
   leaves the graph-side duplicate lane inert.

## The plan

Rigor: **Tier 1 for $0 code** (test-first, undo-test, `make lint` by exit code, judge consult
before each commit). Full Judge Protocol for anything paid, any write outside the scratch graph,
any deletion, and any claim of proof. Owner go before every paid step.

1. **Close the REAL gaps** ($0, test-first, one commit each): gap 1 (done, `0784a7b`), gap 4.
   Gaps 2 and 3 are recorded policy — measured 2026-09-19, not fixed, not defects.
2. **Stand up the scratch graph** ($0): a spare local Neo4j, POIs seeded from `poi-raw.json`,
   the 42 new beats published as the WHOLE city file — existing publish semantics withdraw
   everything else, so no partial-swap code is needed and the dev graph is never touched.
   The 42 beats already produced carry PROSE `narrative_function` values (the gap-1 fix
   binds future extractions only), so that graph is **not ordering-representative** — do not
   read a tour built from it as evidence about beat order.
3. **The corpus comparison report** ($0): coverage, density, lens diversity, and the trust
   properties, legacy against new, at the same 12 POIs. This is the deliverable.
4. **Then throughput** — DONE 2026-09-19: the round collapse, built and
   measured (section below). It SUPERSEDES the parked transport design
   (`Docs/ingestion/2026-09-14-ingest-transport-and-resume-design.md`), which is not needed and
   is not to be re-opened. Bulk-run wall time, recomputed from what was measured: 386 chunks x
   ~10 rounds is about 3,860 rounds, so on the order of 120 hours at the 1.9-minute median this
   run saw — but per-round latency is the unknown (the control's slowest round was 20.7 min), so
   plan in rounds, not hours. The ~$1,000-2,300 money range is unchanged by this.
5. **Then the second book** (Frommer's `chunk-05-ch05-uptown`), which is also the merge test:
   fold when confident, queue the doubtful for human review, never block a publish, and report
   the duplicate rate with `make claim-conflicts`.

## Throughput: the round collapse (built and measured 2026-09-19)

**Diagnosis, from job `32d5c8de…`'s own log:** 3h41 for one chunk, ~97% of it WAITING on 112
sequential batch rounds at a median 1.2 min (P1 11, P3 52 / 2h12, P5 49 / 1h18). The cause was a
LOOP, not the transport: `run._judge_stories` and `run.p5` judged stories one at a time and each
bought its own round. Fix (`31860c1`, `c1d738e`): `judge_claims.judge_unit` and
`judge_narration.judge_narration_unit` share the rounds across ONE unit's stories — claim ids are
unique per unit (`gates.every_claim_once`) and `sentence_custom_id` is the draft's claims_hash, so
custom ids never collide. P4 rewrites stay per story: sync author calls of seconds, not rounds.
**This supersedes `2026-09-14-ingest-transport-and-resume-design.md`** (sync judge rounds): that
gives up the Batch discount for +$270-660 across the slice and does not reduce round count.

**PROVEN (job `c7c39a79…`, same chunk, fresh sandbox `step2c`, HEAD c1d738e, criteria registered
before the run):** rounds P1 11→**4**, P3 52→**4**, P5 49→**2**, total 112→**10**. The primary
metric is deterministic — round counts come from the code path, not the model.
**Observed once, not proven:** 3h41 → **34m46** (6.4x). The two runs sampled different Batch queue
windows (13:22-17:03 UTC against 01:28-02:03), and per-round latency moved with them (median
1.2→1.9 min, max 20.7→8.4). What survives the confound is a BOUND: round count fell 11.2x while
per-round latency rose ~1.6x, so a large win is robust; the multiplier is n=1 at night. Plan the
bulk run as rounds-per-chunk times an unknown per-round latency, never as 6.4x.
**Cost:** at least $2.6072 (the run printed `unmetered_calls=1`, usage unknown, so it is a lower
bound) against $2.9507 expected and the control's $2.6840 — the same requests at the same discount.
**Trade, visible and growing with chunk size:** P1 3:57 → 8:46, one first-pass round now returning
214 claims in a single longer answer.
**Integrity:** 47 beats, 214 first-pass claims, 0 `place_unresolved`, 5 `claim_dropped`, R0 PASS
(live sha 556830a5342e178c unchanged). Held beats 5→3 and narration refusals 19→9 are VARIANCE, not
the collapse: attempt-1 refusals are emitted in the untouched P4 path, the reason classes are the
same, and the two runs share only 3 story slugs of 42 and 47 — P2's grouping is stochastic, so they
narrate different stories. That also bounds what the output comparison can show: no new defect
class, which is weaker than "the same output by a different route".
**First live exercise of `0784a7b`:** `narrative_function` is in the engine's vocabulary for 47/47
beats, against 0/42 in the control.
**TWO-unit scoping PROVEN 2026-09-20 — at two units; more units untested** (job `ac320e5d…`, sandbox `step2d`, two Lonely Planet
chunks: chunk-05 157 claims, chunk-08 186). $0 first: `test_two_units_get_their_own_rounds_never_one_shared_round` (commit f18af9b) drives a two-unit job through a recording client — 2 P3 and 2 P5
rounds, never 1 shared; undo-tested by flattening `run.p5`, which goes RED printing the same
sentence id twice in one batch. Then live, to the registered bar: all 16 rounds recovered with
`make ingest-batch`, **635 of 635 ids resolved (100%), 0 rounds carrying two units** — P3 4 rounds
per unit (157/6/1/3 and 186/10/1/2), P5 2 per unit (121/15 and 104/9), inside the bars of 5 and 3.
A job-wide design would have shown one 343-claim round; it never appeared. Both omission checks
were additive (1 fact → 1 supplement request; 2 facts → 1), so the reroll case was exercised, not
assumed. Committed: 60 beats over 27 POIs, `narrative_function` 60/60 in vocabulary, 5 held,
`stories_held (P3)=0`, R0 PASS (live sha 556830a5342e178c).
**COST, and the incident behind it (owner-overridden 2026-09-20):** expected $4.4051, registered
gate $6.61, ACTUAL ~$7.2 across THREE attempts. The owner was shown the projected
overrun (~$7.5, past the gate) BEFORE deciding, and answered "finish it, override the gate". Attempts 1 and 2 were killed by MY OWN watcher,
never by a real fault. Root cause, a fact about this system rather than a mis-set number: **the job
log is cumulative across attempts and `batch_submitted` carries no unit key**, so any threshold read
from it must be split at the `resumed` marker and cannot be attributed to a unit. Kill 1 read the
next unit's ordinary 186-request round as the previous unit's supplement (188 > 80); kill 2 read
attempt 1's 5 P3 rounds plus the resume's 6 as 11 > 10. The automated killer was then REMOVED and
the run watched by hand. **New carried defect: P3 persists only on completion**, so every kill
re-pays the whole phase for every unit — that is what turned two mistakes into ~$4.8 of the total.
Per-unit or per-round P3 persistence is the fix; recorded, not built.
**Still NOT proven:** transfer to the 386-chunk slice, and the per-story isolation path
(`story_held` has never fired live).

## The branch's full bar is RED BY DESIGN — read this before running `make test`

`corpus-workbench` is pushed with `make test` knowingly red, and has been since slice 0. Every
commit on it was gated on the SMALLER bar instead: the touched files green by exit code
(`make test-file FILE=…`) plus `make lint` exit 0. That is deliberate — the rebuild replaces the
pipeline the old suite pins — but it means a fetcher cannot tell recorded debt from a new break.

**Which half of this is verified:** the GREEN list below was verified by RUNNING each shard on
2026-09-20 at commit 9b78c0a — 23 files, all passed. The KNOWN-RED list is reconstructed from what
this branch changed, NOT from a run, and stays unverified until someone runs the full bar.

Known-red (reconstructed, unverified), with the slice that closes each:
- The golden-tour and persona shards read the 7687 dev corpus, which still holds LEGACY beats;
  they go green when a city is swapped to the new shape (slice 10's swap, owner ruling 2026-09-19:
  every legacy beat is to be replaced).
- The tour-grade and invariant shards score that same legacy corpus.
- GREEN, verified by running them at 9b78c0a on 2026-09-20 (23 files): `tests/test_preflight.py`
  (57), `tests/test_validate_beats_shape.py` (4), the three `tests/test_upload_*.py` (27, 17, 3)
  and all eighteen `tests/test_ingest_*.py`. These are the shards this rebuild owns — run them to
  check the rebuild itself.

**NEXT WORK ITEM (registered 2026-09-20, at the push):** run the full `make test`, triage it
against this list, and either fix or record every shard that is red for a reason NOT named above.
Until that is done, nobody should read a red suite on this branch as evidence about these commits.

## Owner decision owed: the manifests' `source_file` provenance

`Books/**/manifest.json` records each PDF's origin, and several name **Anna's Archive** — a pirate
library — while the ingestion records `rights_basis: owned_copy` for the same book. Both statements
are in the repo and they contradict each other. This predates these commits and is already on the
remote; no commit here adds one. It is an owner call (correct the field forward, or accept what it
says), not something to fix quietly inside a slice.

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
