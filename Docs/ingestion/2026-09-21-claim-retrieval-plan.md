# Claim retrieval and identity — making the second book (and the tenth) merge cheaply

**Written 2026-09-21** from a grilling session between the owner and Claude evaluating Sai's
proposal ("identify the schema + primary key for books and beats, then store them in a vector DB
so new content is checked against it and merged"). It is one part of the district plan
(`2026-09-19-district-port-plan.md`), not a new direction: the end goal is unchanged — Upper East
Side beats built from several books, merged, trustworthy, published, then bulk.

## Problem Statement

Ingesting ONE book works: stories at places, each a set of sourced, judged claims, narrated.
Ingesting a SECOND book for the same area is where the engine has looped for two weeks. Every new
story must be checked against what the corpus already holds at its place — is each claim the same
fact, a conflicting value, covered by several existing claims, or new? — and the owner needs that
done correctly and in the most efficient way possible, because it gets harder with every book:

- **The search space grows with every book.** The merge judge is shown EVERY claim at the place
  on every call. After two books the Met holds 67 claims and the Guggenheim 56; after ten books a
  busy POI could hold hundreds. Tokens grow with it, and worse, a judge picking one match out of
  hundreds is less accurate than one picking out of a handful — and accuracy is the exact thing
  the loop has been fighting.
- **A story that lands at the wrong place never merges at all.** Place matching is exact-name
  only. "Guggenheim Museum" did not match "Solomon R. Guggenheim Museum", so a whole book's
  Guggenheim stories were filed as a new POI (job A). Neue Galerie and Cooper Hewitt missed the
  same way.
- **Claims have no identity that survives a merge.** `claim_id` is a per-beat ordinal (`c01`…),
  it collides across books and after folds, and every audit this month had to diff by content.
  A merge that attaches a second source needs a permanent thing to attach to.

What this plan does NOT fix, stated so nobody expects it: the merge judge deciding wrongly on a
pair it was shown (false `same` on a shared date, compound claims flipping verdicts). That is the
`covered`-verdict work already in flight (handoff `2026-09-21-covered-verdict-next-session.md`),
and it stays first in the order below. Retrieval makes that judge's job smaller; it does not do
that job. An embedding scores "opened in 1959" and "Wright died in 1959" as close — similarity is
not sameness.

## Solution

Three layers decide whether new content already exists, cheapest first:

1. **Exact re-ingest — a key, free.** A Mention (one span of one source stating a claim) is
   identified by source + chunk + span. Ingesting text we already hold is recognised with no model
   call.
2. **Likely matches — vectors, near-free.** Every claim and every POI gets an embedding (a list of
   numbers placing similar meanings near each other). A new claim retrieves its few nearest
   existing claims at its place; a story's place name retrieves its nearest POI. Milliseconds, no
   LLM.
3. **The decision — the merge judge, paid, small.** The judge sees only the retrieved candidates
   and rules same / covered / conflict / new, exactly as today.

A permanent **claim identity** makes the result stick: once the judge says `same`, the new source
hangs off an existing claim forever, so book 10 compares against one claim, not ten near-copies.

**Where the vectors live — two consumers, one set of vectors, no new database** (decided
2026-09-21 with Sai):

- **At ingest: a vector file next to `beats.json`, searched in memory.** The ingestion engine
  never reads Neo4j — it reads and writes beats files; the graph appears only at publish — and
  that separation stays. Brute-force search over tens of thousands of claims is milliseconds.
- **In the graph: a Neo4j vector index on beats and POIs, written at publish.** Sai's goal is
  "similar to" queries and semantic search for the product. Neo4j 5 (we run `neo4j:5`; vector
  indexes exist from 5.11) indexes one vector per node. Claims are NOT graph nodes — the publisher
  flattens a beat's resolved claim texts into its `key_claims` string list — so the graph gets a
  vector per `NarrativeBeat` and per POI, and claim-level vectors stay in the ingest file.
  Promoting claims to graph nodes is a separate, later decision, taken only if the app needs
  fact-level search.
- **Not pgvector** (Sai raised it 2026-09-21). pgvector is Postgres's vector extension; the
  project runs no Postgres (Neo4j and Valhalla only). It would add a whole database — container,
  preflight requirement, Render hosting, and syncing beats between two stores — to do what Neo4j's
  built-in index already does at our scale. The last extra database OOMed dockerd. It becomes
  worth revisiting only if product data ever moves to Postgres.

What the owner sees change: a second book's stories land on the right POI; merge calls stay the
same size no matter how many books are in; a claim's id means the same claim in every file,
report and review item; the job log says how many candidates each merge call was shown.

## User Stories

1. As the owner, I want a second book's story about "the Guggenheim" to land on the Solomon R. Guggenheim Museum, so that it merges with the first book's beats instead of becoming a duplicate POI.
2. As the owner, I want a place name that does not match any POI confidently to still be held as `new_poi` for review, so that a wrong snap never silently files a story at the wrong place.
3. As the owner, I want a close-but-ambiguous place match (two POIs nearly tied) to go to review with the suggestion shown, so that I decide the Jewish Museum vs Museum of Jewish Heritage case, not the model.
4. As the owner, I want place matching limited to the city's POIs and accepted only on a clear winner, so that a similarly named POI elsewhere in the city is held for review rather than silently capturing a story.
5. As the owner, I want every merge call to show the judge a bounded number of candidate claims, so that the cost per call stays flat as books are added.
6. As the owner, I want a place that still holds few claims to behave exactly as it does today (every claim shown), so that the covered-verdict baselines measured on full-place prompts stay valid.
7. As the owner, I want to see, before the pre-filter is ever switched on, proof that for every fold we have recorded the true matching claim(s) were inside the retrieved set, so that the filter cannot silently turn merges into duplicates.
8. As the owner, I want a `covered` match (one new claim stated by several existing claims together) to have ALL of its existing claims retrieved, so that the Guggenheim c148 → c01 + c03 fold still happens under the filter.
9. As the owner, I want the embedder failing or being unavailable to fall back to showing the whole place, logged, so that retrieval never blocks or holds a job.
10. As the owner, I want every embedding cached by model and text, so that re-running a job, a replay, or a resume never pays to embed the same text twice.
11. As the owner, I want embedding spend printed as an estimate before any paid call, like every other phase, so that no step spends without my go.
12. As the owner, I want the job log to record, per merge call, how many candidates were shown out of how many the place holds, so that I can see the efficiency gain and spot a place where retrieval degraded.
13. As the owner, I want each claim to carry a permanent id that never changes through folds, supersessions, re-narration or renumbering, so that reports, review items and audits can refer to a claim by id.
14. As the owner, I want a fold to keep the existing claim's id and add the new source to it, so that corroboration accumulates on one claim.
15. As the owner, I want a genuinely new claim to receive a fresh id at the moment it is written into a beat, so that dropped or held draft claims never consume ids.
16. As the owner, I want the validator to refuse two claims sharing an id anywhere in a beats file, so that the cross-book collisions seen after job B cannot recur.
17. As the owner, I want a superseded claim and the claim that supersedes it to keep their own ids, so that the history of what a source once said stays addressable.
18. As the owner, I want existing sandbox corpora to receive ids by a one-shot backfill run on copies, so that the frozen control corpora keep working and the live `beats.json` (R0 sha) is never touched.
19. As the owner, I want re-ingesting a chunk whose spans are already held to be recognised as a duplicate mention with no model call, so that an accidental re-run costs nothing and adds nothing.
20. As a reviewer, I want held merge items to show the retrieved candidates the judge saw, so that I can tell a judge mistake from a retrieval miss.
21. As a reviewer, I want a review item to name claims by their permanent id, so that accepting one resolves exactly the claim I read.
22. As Sai (developer), I want the embedder behind one small interface with a deterministic fake, so that every test is hermetic and $0 and the provider can be swapped.
23. As Sai (developer), I want retrieval to live inside the merge step's existing interface, so that the job runner, the replay tool and the calibration harness all get it without changes of their own.
24. As Sai (developer), I want the pre-filter's `k` and the place-size threshold to be named constants chosen from the recall measurement, so that they are evidence-backed rather than guessed.
25. As Sai (developer), I want every published beat and POI to carry a vector under a Neo4j vector index, so that "stories similar to this one" and "which place is this" can be answered in the graph.
26. As the owner, I want the graph's vectors written by the same publish that writes the beats, so that a withdrawn or re-narrated beat never leaves a stale vector behind.
27. As the owner, I want the first vector-index build to happen on the scratch graph after a memory check, so that the local containers are not pushed into the OOM seen before.
28. As a future tourist, I want each POI's stories to combine every book's facts without repeats, so that a tour does not tell me the same thing twice from two guidebooks.

## Implementation Decisions

**Order (one plan, one end goal).** Each step is its own test-first commit with a judge consult;
paid steps need the owner's go at the printed price.

1. **Finish the `covered` merge work** per the 2026-09-21 handoff (correct the staged spec
   paragraph first). Unchanged by this plan, and first because it is mid-stream. Its replay bars
   are registered on full-place prompts; step 5's threshold keeps them valid.
2. **Claim identity** ($0). Replaces the handoff's band-aid ("number claim ids from the place-wide
   max") — do not build the band-aid.
3. **The embedder seam** ($0 code; provider decision owed here).
4. **Place matching by meaning** ($0 code + pennies of embeddings; before the next multi-book job,
   because a missed place means the merge never runs).
5. **Merge candidate pre-filter** — a $0 recall measurement first; switched on only if it passes.
6. **Vectors into the graph** ($0 code; scratch graph first) — the publisher writes beat and POI
   vectors and creates the two vector indexes.
7. **Book 2 full job, then bulk** — as the district plan already says.

**Claim identity.**
- New field `claim_uid` on every claim: opaque, immutable, time-sortable, generated from the
  standard library (no new dependency). `claim_id` stays as the per-beat display ordinal the
  prompts and handles use; identity is `claim_uid`.
- Minted when a claim is first written into a beat record (commit time), not at decomposition —
  draft claims that are dropped or held never consume one.
- A `same` fold and a `covered` absorb keep the existing claim's uid; a conflict resolved as
  contested keeps the one claim's uid; a supersession keeps both claims' uids; a `new` claim
  appended to a matched story gets a fresh uid.
- Validator: `claim_uid` required on new-shape records; a duplicate uid anywhere in the file is a
  new error code (`CLAIM_UID_COLLISION`). The per-beat `claim_id` collision check stays.
- One-shot backfill command for existing new-shape sandbox corpora, run on copies; never on the
  live `beats.json`.

**The Mention key (exact re-ingest).**
- A mention is identified by source id + chunk + a hash of the normalised span. Before a story is
  merged, a new claim whose mention key is already held by an existing claim is recognised as a
  re-ingest of that claim: recorded, no judge call. This rides on data every claim already
  carries (`sources[].source_id/chunk/span`); no schema change.

**The embedder seam (the one new seam).**
- An interface: a list of texts in, a list of vectors out, carrying its model name. A real
  provider for live runs; a deterministic fake for tests (e.g. vectors derived from word overlap,
  so tests can plant near and far texts on purpose).
- A cache keyed by (embedding model, hash of the exact text) stored as a file beside the beats
  file. A text already embedded is never sent again; changing the model re-embeds.
- Spend goes through the same estimate-before-spend discipline as every model phase.
- **Provider: Voyage** (decided 2026-09-21; Sai's recommendation, also Anthropic's — Anthropic
  offers no embedding model). Voyage's pricing page, read 2026-09-21: the voyage-4 family at
  $0.02–$0.12 per million tokens, 200M free tokens per model. The whole corpus's claims, even after
  the bulk run, are single-digit millions of tokens, so spend is effectively zero; the estimate is
  still printed. `voyage-4-lite` vs `voyage-4` is chosen by the step-5 recall measurement, not by
  preference. The API key is a provider secret in the Render service environment like the
  others, fetched by the existing credential path; the target that needs it declares it in its
  preflight.
- The one embedding model is used for claims, POIs and beats alike, so ingest-time vectors and
  graph vectors are comparable.

**Place matching by meaning.**
- `resolve_place` keeps its exact name/name_variations match first, unchanged.
- On an exact miss, the place name is embedded and compared with EVERY POI of the city (city
  scoped, per the data conventions). Accept only if the best score clears a threshold AND beats
  the runner-up by a margin; accept means the canonical POI name, `new_poi=False`, and a
  `place_matched_by_meaning` event naming both strings and the score.
- The chunk's candidate-places set (`gates.candidate_places`) is NOT the search scope: it keeps a
  POI only when every distinctive word of its name appears in the passage, so "the Guggenheim
  Museum" drops "Solomon R. Guggenheim Museum" (no name variations; "solomon" absent) — the very
  miss this step exists to fix. Membership in that set may raise confidence (a tie-break between
  close POIs); absence never excludes a POI. (Judge STOP 2026-09-21 caught the first draft
  scoping to that set.)
- Below threshold → today's `new_poi` path. Close runner-up → `new_poi` hold with the suggestion
  shown in the review item.
- Honest scope: this fixes NAME misses (Guggenheim, Neue Galerie, Cooper Hewitt). It does NOT fix
  the grouper choosing the wrong place outright (MCNY filed at "Upper East Side", the Met Breuer
  snapped to the Met) — those are P2 decisions, carried defects, out of scope.

**Merge candidate pre-filter.**
- Inside the merge step, before the judge's prompt is built: for each new claim, take its top-`k`
  nearest existing claims at the place; the judge is shown the union, grouped by beat as today
  (handles unchanged).
- Only when the place holds more than a threshold number of claims. At or below it, behaviour and
  prompt are byte-identical to today.
- The lexical signature tripwire keeps looking at ALL claims at the place, not only the retrieved
  ones — it is the independent check that the filter did not hide a match. A tripwire match
  outside the retrieved set is logged as a retrieval miss.
- Embedder failure → whole place shown, `retrieval_fallback` logged; never a hold.
- `k` and the threshold are chosen from the recall measurement below, with margin, and recorded
  in the spec with the measurement.
- **Not built: a vector tripwire replacing the lexical one.** Vectors score shared-element pairs
  as close, which would restore the false holds the tripwire-precision fix just removed.

**Vectors into the graph (step 6).**
- The publisher's graph export gives each `NarrativeBeat` an embedding of its title and
  narration, and each POI an embedding of its name and name variations, from the same cache (a
  text already embedded at ingest is not paid for again).
- Two Neo4j vector indexes (cosine), one on each label, created idempotently by the publisher.
- A beat whose `claims_hash` changed is re-embedded at the same publish; a withdrawn beat takes
  its vector with it (it is a property of the node).
- Scratch graph first, after a memory check of the local containers; never 7687 until the
  district publish; Aura only through the existing cloud-write gate.
- What the app does with the index (a "similar stories" feature, place search) is a product
  decision outside this plan; this step makes it possible.

**Schema summary.** Claim gains `claim_uid`. Nothing else in the beat record changes. The ingest
vector cache is a sidecar file, not part of the record. Neo4j gains a vector property on
`NarrativeBeat` and POI nodes and two vector indexes; no new labels or relationships.

## Testing Decisions

- Good tests here assert what the owner can observe through an existing interface — which POI a
  story lands on, which claims the judge's prompt contains, which uid a folded claim carries, which
  error the validator reports — never the internals of the search.
- **Place matching** — tested through `resolve_place` / the grouper with the fake embedder, using
  the real misses from our jobs as fixtures ("Guggenheim Museum", "Cooper-Hewitt", "Neue Galerie")
  plus a planted near-tie (two Jewish-heritage museums) that must hold, the Guggenheim case with a
  passage lacking "Solomon" (the POI is outside the chunk's candidate set and must still be
  found), and a POI of another city that must never be chosen. Prior art: the existing grouper
  and resolve_place tests.
- **Pre-filter** — tested through `merge()` with the mock model client, asserting the recorded
  prompt: below threshold, identical to today's prompt; above it, only the planted near claims
  appear; covered's several claims all appear; embedder failure shows the whole place. Prior art:
  the merge tests that already assert on recorded prompts and the Guggenheim fixtures.
- **Claim identity** — validator tests for the new error code and the required field; merge/apply
  tests that a fold keeps the uid, a new claim gets a fresh one, supersession keeps both. Prior
  art: the validator shape tests and the merge apply tests.
- **Embedder** — cache hit makes no call; model change re-embeds; estimate printed before a paid
  call. Prior art: the model-client seam tests.
- **Graph vectors** — through the publisher against the scratch graph: after a publish, every
  published beat and POI carries a vector of the model's dimension, both indexes exist and
  answer a nearest-neighbour query, a re-narrated beat's vector changed, a withdrawn beat is gone
  from the index. Prior art: the publisher/converge tests (the `test_upload_*` family).
- Every new test is undo-tested (revert the change → RED) before its commit, per Tier 1.
- **Recall measurement ($0 except embedding the recorded texts, pennies, owner go):** over every
  panel-upheld fold on record — job B's folds and supersessions, the two additive joins, the
  Guggenheim c151 → c21 same and c148 → c01 + c03 covered — for each new claim, is every existing
  claim the judge matched inside its top-`k`? Registered bar BEFORE running: 100% of upheld pairs
  retrieved at the chosen `k`, which is then doubled for margin. The panel-refuted Frick El Greco
  fold is excluded. If the bar fails, the pre-filter is not switched on and the result is brought
  to the owner.
- Step 7's book-2 job reports, per merge call, candidates shown vs claims held, and the retrieval
  misses the tripwire logged.

## Out of Scope

- The merge judge's precision (false `same`, compound claims, the covered value rules) — the
  in-flight covered work, step 1.
- A vector tripwire.
- App features built on the graph index (the index is in scope; what the app does with it is not).
- Claims as graph nodes with their own vectors (option b, 2026-09-21) — later, only on a product
  need for fact-level search.
- A new database of any kind, including Postgres + pgvector.
- The grouper choosing the wrong place outright (MCNY, Met Breuer) and the P1 compound-claim probe
  (parked by the district plan).
- Cross-place claim matching (a claim filed at one POI matching a claim at another). Retrieval is
  scoped to the story's place, as merging is today.
- The live `beats.json` and the 7687 dev graph: untouched throughout.

## Further Notes

- Sai's framing, corrected in one respect: no primary key can decide that two differently worded
  claims are one fact. The key gives permanent identity; vectors find likely matches; the judge
  decides. The deterministic "same content collides on the key" idea holds only for exact
  re-ingest, which the Mention key covers for free.
- Efficiency today is small (a merge call is a few thousand tokens); the gain is that it stays
  flat as books are added, and that the judge chooses among a handful instead of hundreds.
- Merge quality and search-space growth are coupled: every correct fold keeps a place's claim
  count from growing, and every missed one adds a near-duplicate the next book must also face.
