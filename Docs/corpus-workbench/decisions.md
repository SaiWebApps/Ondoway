# Corpus Workbench — pre-plan interview

> **Start with [rebuild-brief.md](rebuild-brief.md).** It records the state of the
> re-author work, what an adversarial panel refuted, and what to build next. It
> supersedes I4 below and the review-queue design.

Decisions taken in the grilling interview that precedes the implementation plan.
Each entry states the decision that holds now and the code fact that forced it.

## Decisions

**I1 — commit enforces the record; expensive gates run before it.**
`beats_io.commit` stays what its docstring says it is: a thin atomicity-plus-validation
layer with no business logic. The gate suite runs as a separate pass that writes a
hash-bound `review` record onto each beat; `validate_beats` refuses any beat lacking a
fresh one. This reuses the pattern `_check_verification_freshness` already proves for
`fact_check.verified_body_hash`. A producer that skips the pass cannot commit, so the
chokepoint of D4 holds without putting a paid LLM call inside an atomic rename.

**I2 — the existing corpus is grandfathered by body hash; London is not.**
Seeded from `data/*/grounding_grandfathered.json`'s mechanism: a set of exempt
`script_body_hash`es, which a beat leaves automatically the moment its body is edited.
Paris (1,562) and New York (2,005) are seeded exempt so the gate lands green. London's
561 are deliberately left out, so D5's quarantine is enforced by the same gate rather
than by a mechanism of its own.

**I3 — quarantine state lives in the files and is carried by the publisher.**
`scripts/upload_paris.py:495` sets `beat.active_status = 'active'` unconditionally on
every publish, so a graph-side flip is undone by the next upload. The publisher instead
derives the status from the review record, so a re-publish converges the graph on the
files (D3) rather than fighting them.

**I4 — SUPERSEDED by [rebuild-brief.md](rebuild-brief.md).** The unit is right and the
reasoning about the tour composer still holds, but feeding the writer the source prose
preserves the source's order and selection in 91% of multi-sentence cases. The writer must
receive a shuffled claim set instead.

**I4 (as originally recorded) — Lane B is a thin beat-level pass, not the tour composer.**
Its unit is `(source_passage -> beat body)`, which `ComposeRequest` is not: that model is
tour-shaped (stops, slots, threads, doors) and its fields are sealed into
`compose_input_sha256`, so changing it re-seals the frozen certification archive on its
next paid run. Lane B instead reuses `HaikuFaithfulnessChecker` (`src/tour/verify.py:100`)
for re-grounding and `score_narration` for the craft floor, taking voice from
`fixtures/tour-quality-standard/01-standard.md` §3. It builds no second entailment gate.

**I5 — the `fact_check` backfill was never a Lane A member; its ordering is superseded
by I8 and I12.** It is neither deterministic nor free, so it does not belong in a lane
defined as machine auto-repair. What survives intact is the order-of-operations argument:
London's 561 are re-authored first and fact-checked once, afterwards, rather than checked
twice. The real remaining scope is the 220 re-checks I12 commits to, plus the 597 Paris
beats that have never been checked at all.

**I6 — SUPERSEDED by I8/I9.** It made v1 the spine plus a Lane C adjudication queue.
The queue serves none of the owner's four goals and the spine serves only the design's own
prevention goal, so both were re-ordered behind the goals.

**I8 — the build order follows the owner's four goals, not the prevention spine.**
The review record, commit enforcement, grandfathering and publisher-carried quarantine
exist to stop a future London. That is worth doing and it is not what was asked for. The
order is now: the coverage and quality dashboard read straight off `beats.json`; then the
re-author pass that makes bodies non-verbatim; then ingestion; then fact-checking whatever
is still unchecked. The prevention spine follows, shaped by what the dashboard shows rather
than guessed at now.

**I9 — the dashboard needs no new persistence.**
Everything the owner asked to see already exists or is computable: `fact_check.status`, body
length and `beat_length_class` are on the beat, and the verbatim ratio comes from
`scripts/dedup_pairs.py`'s existing `shingle_set()` + `_jaccard()` measured between
`script_body` and `source_passage`. No review record is required to show corpus quality.

**I10 — a website is any URL, ingested on the book's terms.**
The same extraction contract, the same rights basis recorded at drop time. Facts are not
copyrightable and the goal-1b re-author is what makes the expression original, so the
re-author pass must ship with website ingestion rather than after it. `/pipeline-chunk`
already runs extract -> POI match/create -> fact-check -> geocode -> gravity ->
export-validate, so what goal 4 needs is a source adapter and a front door, not a pipeline.

**I11 — "copied" means a 70% overlap of 8-word shingles.**
Measured with `dedup_pairs.shingle_set()` between `script_body` and `source_passage`. An
8-word verbatim run is evidence of copying; a 5-word run is ordinary phrasing. The backlog
this defines is 246 Paris, 278 New York and 553 London, or 1,077 beats.

**I12 — a re-authored beat is re-fact-checked, not carried forward.**
220 of the backlog are already `verified`, and `validate_beats`'s freshness gate voids a
stamp whose body has moved. That gate is honoured rather than worked around: the 220 are
re-checked after re-authoring. The re-check is narrow, since the facts are already
known-good and only the new wording needs confirming against them.

**I7 — `decided_by` is the committing operator's git identity.**
Read from git config at decision time, so the field is real from the first decision and
stays correct without editing when a second reviewer starts adjudicating.

## A latent defect this interview uncovered

**Publish never withdraws.** `scripts/upload_paris.py` only MERGEs and SETs. Its
`_BLOCKED_STATUSES` check (line 83) keeps a `disputed` beat out of the payload, and the
unconditional `active_status = 'active'` at line 495 re-activates everything it does send.
Nothing takes down a beat that was uploaded while clean and disputed afterwards.

**It has not fired.** All 21 Paris and 8 New York disputed beats were checked against the
graph by `beat_id`: none is live. Every one was disputed before its first upload, so the
path that would strand a beat has never been taken. The defect is latent, not an incident.

It is still the same fix as I3 — the publisher writes a withdrawn state rather than merely
declining to send a record — and it is what makes any later quarantine, London's included,
actually stick.


## Corrections to design.md found while grounding the interview

1. **`specs/NORTHSTAR.md`, `specs/2026-07-19-tour-quality-standard` and
   `specs/2026-07-12-new-city-onboarding` do not exist at HEAD.** The spec tree was
   retired. The quality standard lives at `fixtures/tour-quality-standard/01-standard.md`.
   The "manual rights-cleared book drop" decision cited by D6 survives in no file at all —
   only in git history and in this design's own restatement of it.

2. **§9.6 needed no owner ruling.** The ceiling it escalated was locked only in the
   deleted `specs/NORTHSTAR.md`. `Docs/Markdown Docs/NORTHSTAR.md` states the opposite and
   agrees with both the extraction prompt and the corpus.

3. **§5.2's TTS bill is zero today.** 4 of 4,103 graph beats carry an `audio_url`, and all
   four are the demo seeder's `s3://ondoway-audio/placeholder/*.mp3` stubs. The stale-audio
   hazard remains a real requirement on the publish path; it is not a backlog.

4. **POI coordinates are a `location` point and the city key is `city_name`.** There is no
   `city` or `latitude` property on a POI, and no `script_body_hash` on a graph beat. Any
   dashboard query written against those names returns nulls silently.

5. **Files and graph already disagree.** Paris 1,562 file / 1,545 graph, New York 2,005 /
   1,997, London 561 / 561. The 25-beat gap is unexplained and must be reconciled before
   the coverage dashboard can claim to count anything.

6. **The quality standard §6 cites `src/tour/compose_correct.py`, which was deleted.**
   Its logic folded into `authoring.py` in `0f39359`. The process lint does not scan
   `fixtures/`, which is why the reference survived there.

7. **The 25-beat file/graph gap is fully explained, and is not drift.** Paris: 1,562 file
   less 21 `disputed` (which `upload_paris.py:83` blocks) is 1,541 uploaded, plus 4 beats
   the demo seeder wrote straight into the graph, giving 1,545. New York: 2,005 less 8
   disputed is 1,997. London: 561, none blocked.

8. **Four beats live in the dev graph that exist in no file, and they are duplicates.**
   Their bodies are verbatim `src/seed/narratives.py` lines 37, 48, 59 and 69. They carry no
   `beat_id`, so no upload produced them — `upload_paris.py:432` skips records without one.
   They are the only four holding an `audio_url` and the only four with a NULL `fact_status`.
   The same two bodies also exist in `data/paris/beats.json` as `dbsync_5a8874d6` and
   `dbsync_635ec842`, which WERE uploaded, so the graph serves both copies of the same text.
   §5.2's challenger note is right that the seeder is not a publish path and still wrong that
   it is harmless: its output is in the graph, duplicated against real beats.

