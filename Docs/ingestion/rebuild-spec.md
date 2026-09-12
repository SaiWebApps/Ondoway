# Ingestion rebuild — spec and slice plan

> **For agentic workers:** each slice below is a defined change sized for `/team <slice>`.
> Run them in order in fresh conversations; every slice states the one test that proves it.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Date:** 2026-09-08 · **Base branch:** `corpus-workbench` (fast-forward of `main`) ·
**Tier:** 3 (data + publish path + live graph) · **Decision record:**
[ADR-0001](../adr/0001-beat-is-a-per-place-story-with-a-claim-set.md) ·
**Glossary:** [CONTEXT.md](../../CONTEXT.md)

> **Path convention:** a path written with a leading `./` does not exist yet; a slice
> creates it. Bare paths exist on the branch today. The process lint checks only the
> bare ones, by design.

**Goal:** an ingestion engine that turns a rights-cleared book or website into per-place
stories carrying complete, sourced, judged claim sets and derived narration, with every
gate in code, a workbench front door, and the existing corpus re-extracted through it.

**Architecture:** a Python engine under `./src/ingest/` (planned) with seven phases per chunk
(decompose, group, judge claims, narrate, judge narration, merge, commit), an author
model and a judge model that are never the same, deterministic gates between phases, a job
store with an event stream, a hash-bound review queue, and a publisher that converges the
graph on the files.

**Tech stack:** Python 3.12, `anthropic` SDK (API billing), FastAPI routes on the existing
server, Neo4j, the existing `scripts/verbatim.py`, `scripts/beats_io.py` and
`src/onboard/jobs.py` patterns.

**Supersedes** on the branch: `Docs/ingestion/re-extraction-scope.md` (its four rulings
survive, its prose-primary model does not), `Docs/corpus-workbench/rebuild-brief.md`,
`stage-0-triage.md`, `stage-1-cleanroom.md` and the re-author pipeline. Slice 11 deletes
them.

## Global constraints

- Every command runs through a Makefile target with a preflight line (CLAUDE.md).
- `make lint` is zero errors before any commit. Node-id tests only, never `-k`.
- Author model: `claude-opus-5`. Claim and narration judge: `claude-haiku-4-5`. Merge
  judge: `claude-sonnet-5`. The engine refuses to run a judge whose model id equals the
  author's, and records the model id the API returned on every verdict.
- Lift = 8+ consecutive words shared with a span outside an attributed quotation
  (`scripts/verbatim.py`, unchanged). Attribution to a guidebook is not attribution.
- Every claim's span must appear verbatim in its chunk. Every narration sentence must be
  entailed by the story's resolved claims. Every verdict is bound by SHA-256 to the exact
  text it judged.
- No paid call before the job prints a token-based cost estimate; no cloud write without
  `TARGET=cloud CONFIRM_CLOUD_WRITE=1`.
- London and the 137 chunkless orphans (every beat whose `source_chunk_slug` is
  `legacy_ambiguous`; an earlier audit table counted only the 56 lifted ones) are
  quarantined (slice 0) and never re-extracted; slice 11
  deletes the quarantine after the swap has held. Wikipedia is ingested on the same terms
  as a book.

---

## Rights and licensing

What the engine may take from a source, and what it owes back. The rules that are enforced
in code are stated as rules; the two positions marked **default** are the owner's to change,
and stand until changed.

**Facts, not expression.** A source is used for the facts it states, never for its wording.
Claims (`P1`) carry a verbatim `span` only as evidence of where the fact came from; narration
(`P4`) is written from claims alone and never sees a span. The mechanical proof is the lift
gate: an 8+ consecutive-word run shared with any span, outside an attributed quotation, is
copying and is refused (`scripts/verbatim.py`); a 5-word run is ordinary phrasing. A quotation
is attributed only when the narration names its speaker or author; naming a guidebook is not
attribution and does not license the run.

**Every source carries a rights basis and an as-of date, recorded at intake.** The manifest
(`D12`) and the `POST /ingest/jobs` body carry `rights_basis`; `P0` refuses a unit without
one, and the value is copied onto every `Source` the unit yields. The vocabulary
(**default**):

| `rights_basis` | Meaning | What the engine does with it |
|---|---|---|
| `owned_copy` | A guidebook or other work the owner holds a copy of, used as a factual source | Facts only; the lift gate is the proof that no expression was taken. Attribution is recorded on the beat, never voiced. |
| `cc_by_sa` | Text under CC BY-SA (Wikipedia) | Facts only, same gate. Attribution is owed: the source record carries `article_title`, the `?oldid=` URL, `revision_id`, `section` and `retrieved_at` (§2 validator rules), and the app shows them with the licence name wherever the beat is served (a "Sources" line on the stop). |
| `public_domain` | Out of copyright or released to the public domain | Facts and, where wanted, wording; the lift gate still applies so narration stays in the house voice. |
| `own_work` | Text written for Ondoway by its own people | No restriction; recorded so provenance is never blank. |
| `permission` | Written permission from the rights holder, reference kept in the manifest | As the permission states; the manifest names the reference. |

**Wikipedia (default).** Wikipedia is ingested on a book's terms for the pipeline (chunked,
revision-pinned, gated the same way) and on `cc_by_sa` terms for what is owed. The project's
working position is that narration composed from extracted claims is original expression
built on uncopyrightable facts, so it is not a derivative of the article's text and carries
no share-alike obligation; the attribution obligation is met by the source record and the
"Sources" line. This is a position the owner has taken for the product, not legal advice; if
counsel rules differently, `cc_by_sa` beats are the ones the graph can withdraw by
`rights_basis` in one query.

**What today's corpus already records.** Every beat carries a `source_attribution` dict —
for a book its title, and where the extractor recorded them its author and chapter (90% of
book beats) and page (24%); for Wikipedia, on every one of its 192 beats, `article_title`,
the `?oldid=` URL, `revision_id`, `section` and `retrieved_at`. The new record (§2) carries
`as_of` and `rights_basis` on every `Source` and the five Wikipedia fields on a `cc_by_sa`
source; slice 10 sets them for every re-extracted source from its manifest.

**Why this is written down.** The rights-cleared-drop ruling this rebuild inherits survived in
no file after the spec tree was retired; the lift threshold and the "facts are not
copyrightable" reasoning lived only in the Lane B interview notes. Both now live here, beside
the gates that enforce them.

## 1. Decisions from the interview (2026-09-08)

| # | Decision | Forced by |
|---|---|---|
| D1 | Build on `corpus-workbench`; its copying gate is a symptom fix and survives as a narration check | Proof chunk: 0 lifts but 28/28 same-order, 5/42 provenance leaks, "the Second WWII" shipped |
| D2 | Beat = one story at one place, bounded by one arc (Rule A). Identity (city, place, story). Book leaves identity | Owner: sentence atoms were unusable and undetectable as duplicates |
| D3 | Payload = complete claim set; each claim carries sources, span, as-of, kind, status | "Add new detail" and "track both sources" are claim-set operations |
| D4 | Narration derived from claims, every sentence traceable, no framing (engine owns glue) | `generation.py`: "Glue is the only place generation invents text" |
| D5 | Lenses are a list on the story | Same reception story tagged `historic_arch` in one book, `local_legends` in another |
| D6 | Structural beats (orientation, transit, sidebar) exempt from the arc rule | Practical guidance has no arc; the types already exist |
| D7 | Extraction is corpus-blind; merge is a separate per-place pass with a non-author judge plus a deterministic signature hint; disagreement goes to a person | Judge may never include the author; small candidate sets per place |
| D8 | Status lives on the claim: resolved / contested / superseded. Contested claims are not voiced; the story still ships | 29 stories dark today for one claim each |
| D9 | Every source has an as-of date; claims have a temporal kind (event / state / belief). Recency resolves state claims only. Supersession is a merge outcome, not a contest. Fact-check becomes a staleness pass | Richard III: both sources right as of their date |
| D10 | Re-extract everything; do not migrate. 1,835 verdicts discarded | Proof chunk found 42 stories where migration would keep 24 |
| D11 | Engine in code; all model calls via the API; model-client seam kept for a subscription worker later | One place to upload and watch; nothing depends on a chat window |
| D12 | Books are chunked once by `/book-prep` in the Claude interface; the chunk folder plus manifest (with as-of and rights basis) is the job input. Websites are one unit, split mechanically at headings only when over the unit ceiling | Simple, inspectable, one-time per book |
| D13 | Review queue blocks only: merge disagreement (new story held), narration flagged after second ask (beat held), new place (its beats held). Contested claims queue without blocking. Everything else is a log line | Workbench design: 30-second reads cannot see the defects |
| D14 | Graph swap is files first, publisher converge second, tour bar third, cloud last, all at once per city | Publisher never withdraws; mixed old/new would seat one story twice |

## 2. The record

One beat in `data/{city}/beats.json`. Field names are final; slice 1 pins them.

```json
{
  "beat_id": "new_york/guggenheim-museum/how-the-museum-came-to-be",
  "city_name": "new_york",
  "poi_name": "Guggenheim Museum",
  "story_slug": "how-the-museum-came-to-be",
  "title": "How the museum came to be",
  "beat_type": "anecdote",
  "lenses": ["hidden_history", "visual_art"],
  "sub_location": null,
  "trigger_address": null,
  "claims": [
    {
      "claim_id": "c07",
      "text": "The building was completed in 1959, after both Wright and Guggenheim had died.",
      "kind": "event",
      "status": "resolved",
      "sources": [
        {"source_id": "lonely-planet-new-york-city", "chunk": "chunk-07-upper-east-side",
         "span": "Construction was finally completed in 1959 – after both Wright and Guggenheim had passed away.",
         "as_of": 2023, "rights_basis": "owned_copy", "stated_value": null},
        {"source_id": "frommers-nyc-2024", "chunk": "chunk-05-ch05-uptown",
         "span": "Visiting this 1959 masterpiece ...", "as_of": 2024, "rights_basis": "owned_copy",
         "stated_value": null}
      ],
      "resolved_value": null,
      "resolution": {"by": "corroborated", "decided_by": null, "decided_at": null},
      "verdict": {"judge_model": "claude-haiku-4-5", "entailed": true,
                  "bound_to": "<sha256 of text + span>"}
    }
  ],
  "narration": {
    "text": "Solomon Guggenheim was a mining millionaire ...",
    "claims_hash": "<sha256 of the resolved claim texts in order>",
    "author_model": "claude-opus-5",
    "verdict": {"judge_model": "claude-haiku-4-5", "sentences_entailed": 9,
                "sentences_total": 9, "bound_to": "<sha256 of text>"},
    "flags": []
  },
  "physical_cues": [], "entities": ["Solomon R. Guggenheim", "Hilla Rebay", "Frank Lloyd Wright"],
  "narrative_function": "establishing", "emotional_register": "neutral",
  "sensory_anchor": false, "inline_foreign_phrases": [], "pronunciation": null,
  "kid_friendly": "yes",
  "duration_sec": 46,
  "review": {"held": false, "reason": null}
}
```

Rules the validator enforces on this shape (slice 1):

- `beat_id == f"{city_name}/{slug(poi_name)}/{story_slug}"`, unique per file.
- Every claim has ≥1 source; every source has `as_of` and a `rights_basis` from the
  vocabulary in Rights and licensing, and a `cc_by_sa` source also carries `article_title`,
  `url` (with `?oldid=`), `revision_id`, `section` and `retrieved_at`; every `span` is a verbatim
  substring of the named chunk file; every claim has a `verdict` whose `judge_model` differs
  from `narration.author_model`.
- `kind ∈ {event, state, belief}`; `status ∈ {resolved, contested, superseded}`; a
  contested claim has ≥2 sources with differing `stated_value`.
- `narration.claims_hash` equals the hash of the resolved claims' texts, in order. A
  narration whose hash is stale is refused (the same rule as `verified_body_hash` today).
- No lift ≥8 between `narration.text` and any span of the beat, outside attributed
  quotation. No provenance leak (regex, slice 5).
- `beat_type ∈ {stop_orientation, transit, sidebar}` beats skip the arc rule; every other
  beat has ≥2 claims.

**Graph export** (slice 8) keeps the tour engine's contract unchanged: `script_body` =
`narration.text`; `key_claims` = texts of resolved claims; `beat_length_class` computed from
`duration_sec` (`micro` <8 s, `seasoning` <32 s, `mid` <80 s, else `anchor`); `lenses`
exported as the existing `lens` relationship, one per entry. No engine file changes.

## 3. The engine: phases and gates

Per chunk job. A phase's output is written to the job before the next phase starts, so a
failed job resumes at its last completed phase.

| Phase | Model | Output | Code gates before the next phase |
|---|---|---|---|
| P0 intake | none | units of text with `source_id`, `as_of`, rights basis | manifest fields present; a website unit over the ceiling is split at headings and the job logs it |
| P1 decompose | author | claims `{text, kind, span}` per unit | span verbatim in unit; no lift ≥8 claim-vs-unit (the span is inside the unit; measured with `scripts/verbatim.py`'s `run_outside_quotation`, attributed quotations exempt); no book furniture (leak regex); self-contained (no bare pronoun subject); `kind` present, default `state` when the judge marks it ambiguous |
| P2 group | author | stories `{title, place, lenses, beat_type, enrichment, claim_ids}` | every claim in exactly one story; place resolves to `poi-raw.json` or is flagged `new_poi`; structural types allowed 1 claim, others ≥2 |
| P3 judge claims | judge | per claim: entailed yes/no; per unit: facts no claim carries | a claim refused once is re-asked with the judge's reason quoted back; refused twice is dropped and logged; an omission finding re-asks P1 once for that unit |
| P4 narrate | author, sees claims only | narration text | no lift vs any span; no leak; no framing regex (`imagine`, `picture`, `envision`); duration computed |
| P5 judge narration | judge | per sentence entailed yes/no | a failing sentence re-asks P4 once with the sentence quoted back; still failing → `narration.flags` set, `review.held = true` |
| P6 merge | merge judge + signature | per new story: same / new / supersedes; per claim: new / same / conflict | judge and signature agree → apply; disagree → hold the new story, queue item; conflict → both claims `contested`; same → one claim, sources appended; supersedes → old claim `belief`, dated; any claim change → P4/P5 rerun for that beat |
| P7 commit | none | `beats_io.commit` | the slice-1 validator, extended; nothing reaches disk otherwise |

Refusal loop: exactly one re-ask per phase per item, then drop or hold. No item is padded,
re-classed or synonym-swapped to pass.

Cost estimate: before P1, `messages.count_tokens` over every unit, multiplied by the
per-phase call shape, printed as the job's first event. Batch API is used for P1, P3 and P5
(no latency need, half price).

## 4. Calibration by defect injection

`./fixtures/ingestion/defects.json`: ten known-good beats (from the slice-9 proof chunk,
hand-read) with planted defects, one per record: a fabricated date, a deleted claim, a
wrongly attached cause, an 8-word lift, a guidebook attribution, a framing sentence, a
state claim kinded as event, a contested value, a superseded belief, an omitted fact in
the unit. The `ingest-calibrate` target runs the judge phases over them and prints caught /
missed per class. A judge prompt change that lowers a class's catch rate fails the target.

## 5. Front door and review queue

`POST /ingest/jobs` with `{city, source: {kind: "book", chunk_dir} | {kind: "url", url},
as_of, rights_basis}` → 202 + job id. `GET /ingest/jobs/{id}` snapshot; `GET
/ingest/jobs/{id}/stream` SSE of phase events. `GET /ingest/review?city=` lists held items
ranked by content held back; `POST /ingest/review/decision` records `{item_id, decision,
decided_by}` bound to the hash of what was shown. `POST /ingest/publish?city=&target=`
refuses while any held item is undecided. All served by the existing FastAPI app beside
`/onboard/*`, reusing `JobStore`. The workbench page shows jobs, phases, the queue and the
publish button; nothing else.

## 6. Publisher converge

`scripts/upload_paris.py` becomes "make the graph match the file": MERGE every beat in the
file, then withdraw every `NarrativeBeat` of that city whose `beat_id` is not in the file
(`active_status = 'withdrawn'`, `HAS_BEAT` kept so a re-publish can restore). Proven on
7687 with a before/after count; `db-parity` reports withdrawn counts.

## 7. Batch order (D14)

- [x] Quarantine London, the 137 orphans, the re-author pipeline under `_to_be_deleted/` (slice 0).
- [ ] Re-extract Paris and New York into the new-schema files offline; the 137 orphans leave
      `beats.json` here, with the swap that re-establishes parity (slice 10).
- [ ] Publisher converge merged and proven on 7687 (slice 8).
- [ ] Publish to 7687; run `_test-golden`, tour grade, invariants (slice 10).
- [ ] `make deploy TARGET=cloud CONFIRM_CLOUD_WRITE=1` per city (slice 10, human at the keyboard).
- [ ] Delete `_to_be_deleted/`, the `beats.legacy.json` copies and every `*.bak-*` file (slice 11), once the cloud publish has served tours for a week without a rollback.

---

## 8. Slices

Each slice is one `/team` run. **Files** and **Interfaces** are what the next slice relies
on; the proving test is a pytest node id.

### Slice 0: Branch, worktree, quarantine

Nothing is deleted in this slice. Everything the rebuild retires is MOVED under a
top-level `_to_be_deleted/` directory, keeping its relative path, so it can be restored
with one `git mv` until slice 11 removes the directory after the graph swap succeeds.

**Files (done):** the retired `scripts/reauthor_*.py`, `tests/test_reauthor_*.py`, `frontend/rewrites.html` and `data/london/` were moved
under `_to_be_deleted/` keeping their paths (`_to_be_deleted/frontend/rewrites.html`,
`_to_be_deleted/data/london/`, 167 renames); the `/api/reauthored` routes that imported the
scripts left `src/server.py` with their six tests; the reauthor entries left `LINT_PATHS` in
`Makefile`; `_to_be_deleted/README.md` names what is there, why, the one-command restore and
the slice that deletes it. The four gitignored `data/*/reauthored*.json` and
`data/*/claims.json` per city are machine-local model output that exists only in a
developer's main checkout, never in a worktree, so git cannot move them: the owner moves
them by hand into `_to_be_deleted/data/{city}/`, and `_to_be_deleted/data/` is ignored so
they stay ignored there. The 137 `legacy_ambiguous` beats are written to
`_to_be_deleted/data/paris/orphans.json` (force-added, since that directory is ignored) and
STAY in `beats.json` until slice 10: they are in the 7687 and Aura graphs, and
`scripts/db_parity.py` — which every `make test-file` runs through the `dev-data`
preflight — counts a graph record the file no longer names as drift. They leave
`beats.json` with the slice-10 swap, which re-establishes parity by publishing.

**Also in this slice, the planned-path rule.** This spec names the files later slices
create with a leading `./` (see the path convention at the top). The process lint in
`scripts/lint_process_files.py` passes them today only as a side effect of its
top-directory prefix test; nothing names the convention, and a natural cleanup (stripping
a leading `.` alongside the `([` it already strips) would turn every planned path red at
once. Make it a named rule: a `./`-prefixed token is a planned artifact named by a spec,
never a claim that it exists, with a test in `tests/test_lint_process_files.py` that pins
both halves (a `./` path is skipped; the same path bare is checked).

**The invariant every moving or deleting slice keeps:** this spec's own references to
what a slice moves or deletes are rewritten in that slice's commit, so the lint that
refuses dangling references stays green at the spec itself. For this slice that was the retired `frontend/rewrites.html` and `data/london/` references above, now written as their `_to_be_deleted/` paths.
**Proves:** `make lint` zero errors (the process lint treats a line that narrates a
removal as exempt, and `_to_be_deleted/` is not a scanned root) and
`make test-file FILE=tests/test_beat_validation.py` green on the branch. Judge consult
before the moves.

### Slice 1: The record and its validator

**Files:** create `./src/ingest/model.py` (pydantic `Beat`, `Claim`, `Source`, `Verdict`,
`Narration`), `./fixtures/ingestion/guggenheim-example.json` (the §2 record, four beats);
modify `scripts/validate_beats.py` to validate the new shape and refuse the old one.
**Produces:** `validate(beats: list[dict], chunks_root: Path) -> list[str]` (errors);
`claims_hash(claims) -> str`; `bind(text: str, span: str) -> str`.
**Proves:** `tests/test_ingest_model.py::test_stale_narration_hash_is_refused` and
`::test_judge_equal_to_author_is_refused`.

### Slice 2: Model-client seam and cost estimate

**Files:** create `./src/ingest/llm.py` with `class ModelClient(Protocol): complete(role,
prompt, schema) -> Completion` where `Completion` carries `text`, `model_id` (from the API
response, never from config) and `usage`; `AnthropicClient` (SDK, Batch for bulk roles),
`MockClient` (scripted answers); `estimate(units, plan) -> CostEstimate` via
`count_tokens`.
**Proves:** `tests/test_ingest_llm.py::test_judge_role_refuses_author_model_id` and
`::test_estimate_is_printed_before_any_completion`.

### Slice 3: Decompose and group (P1, P2)

**Files:** create `./src/ingest/unit.py` (the Unit a chunk becomes), `./src/ingest/decompose.py`,
`./src/ingest/group.py`, `./src/ingest/prompts/` (the decomposer prompt carried from the branch's `_DECOMPOSE_PROMPT`, the Rule-A grouping
prompt with the tie-break from the interview); `./src/ingest/gates.py` (span-in-unit, leak
regex, self-contained regex, kind default).
**Produces:** `decompose(unit, client) -> list[ClaimDraft]` (a claim without P3's verdict and
status — `model.Claim` requires both); `group(claims, unit, pois, client) -> list[Story]`. The
refusal loop's second failure drops items: a double-booked claim is removed from every story that
listed it, a story under its claim floor is dropped, and the answer is never re-seated.
**Proves:** `tests/test_ingest_decompose.py::test_span_not_in_unit_is_refused`,
`tests/test_ingest_group.py::test_every_claim_lands_in_exactly_one_story`.

### Slice 4: Claim judge, omission, calibration (P3, §4)

**Files:** create `./src/ingest/judge_claims.py`, `./src/ingest/prompts/judge.py` (the
entailment, restate and omissions prompts), `./src/ingest/calibrate.py` and
`./scripts/ingest_calibrate.py` (the §4 harness), `./fixtures/ingestion/defects.json`,
`./fixtures/ingestion/calibration-baseline.json`, Makefile target `ingest-calibrate` with
preflight `$(PRE_PY)`; extend `src/ingest/model.py` with `VERDICT_NOT_ENTAILED` (a refused
claim never reaches disk).
**Produces:** `judge_claims(story, claims, unit, client) -> list[JudgedClaim]` (the
`ClaimDraft` it judged plus its `Verdict` — a restated claim's text changes, so a bare
`Verdict` could not carry it); `omissions(unit, claims, client) -> list[str]` (findings whose
span is not verbatim in the unit are discarded and logged; the P1 re-ask for the unit is the
job runner's decision, slice 7). The refusal loop: refused once → the author restates that
claim with the judge's reason quoted back, gated by the P1 gates; refused twice, or failing a
gate, → dropped and logged; ids never renumbered (stories reference them).
**Proves:** `tests/test_ingest_judge.py::test_verdict_is_bound_to_claim_and_span_hash`;
the `ingest-calibrate` target prints a catch rate per class, with fabricated-date and deleted-claim
at 100% on the fixture. The ten records are hand-read from the Lonely Planet Guggenheim passage
(slice 9's proof chunk did not exist yet; it may refresh them). `make ingest-calibrate` runs the
mock client: the judged classes are scripted from the fixture's `planted` block — a harness
check — and only the gates classes are measured; `make ingest-calibrate-live` (declares
`render-key`) measures, prints its estimate first and stops without `ARGS=--yes`. Classes whose detector lands later (framing sentence P4/P5, state-as-event, contested
value and superseded belief P6) print as pending, never missed.

### Slice 5: Narrate and judge narration (P4, P5)

**Files:** create `./src/ingest/narrate.py`, `./src/ingest/judge_narration.py`; extend
`./src/ingest/gates.py` with `provenance_leak(text) -> list[str]` (`the book`, `described
here`, `the guide`, `the author`, publisher names from the manifest) and
`framing(text) -> list[str]`; reuse `scripts.verbatim.run_outside_quotation`.
**Produces:** `narrate(story, claims, client, publishers=()) -> NarrationDraft` (the
judged claims come in as a parameter — a `Story` carries only claim ids; the author is
shown their TEXTS alone, never a span or the unit; the draft carries the text,
`claims_hash` over those texts, the author model the response reported, and
`duration_sec` at the tour engine's 150 wpm — a draft, not a `Narration`, because that
record requires a verdict only P5 can supply). The P4 code gate (`narration_gates`: lift
against every claim span, `provenance_leak` with the manifest's publisher names via
`publishers_from_manifest`, `framing`) refuses once with every reason quoted back; a
second failure raises `BeatHeld` (a `UnitHeld` keyed by story slug) for P4.
`judge_narration(story, draft, claims, client, publishers=()) -> JudgedNarration`
(`narration: model.Narration` with the per-sentence verdict bound to the text,
`duration_sec`, `review: model.Review`): one P5 batch per sentence under
`narration_judge`; a refused sentence re-asks P4 ONCE with the sentence and reason
quoted back, the rewrite passes the code gate and is re-judged in full; still refused
→ `not_entailed` flags and `review.held`, returned (not raised) so the reviewer sees a
judged record; a rewrite failing the gate is never judged and holds with the round-one
text. The claims are never touched. The framing calibration class runs under the new
`narration_gates` detector (mock and live alike, $0).
**Proves:** `tests/test_ingest_narrate.py::test_narration_never_sees_the_span`,
`::test_leak_is_refused`, `::test_second_failure_holds_the_beat_not_the_claims`.

### Slice 6: Merge (P6) and the conflict report

**Files:** create `./src/ingest/merge.py` (signature hint importing `_signature`, `_overlap`
and the dedup thresholds from `src/tour/claim_dedup.py`, outcome application, supersession
re-kinding), `./src/ingest/prompts/merge.py` (the merge-judge prompt and `P6_MERGE_SCHEMA`,
re-exported from the facade), `./scripts/claim_conflicts.py` (city-wide signature match with
differing number tokens → both claims contested, each gaining the other's sources), Makefile
target `claim-conflicts CITY=` (report only; `ARGS=--apply` writes, and only if the city still
validates).
**Produces:** `merge(new_story, claims, existing: list[Beat], client, events=None) ->
MergeOutcome` (the judged claims come in as a parameter — a `Story` carries only claim ids; one
sync call under `merge_judge`/P6 over claim texts, never spans; the judge answers story
same/new/supersedes and per claim new/same/conflict with both stated values; the signature
hint is computed locally, never shown to the judge, and compared — any disagreement returns
`held` with the reason, a queue item with `beat_held` emitted for P6, never re-asked toward
the hint; an answer naming ids the record lacks is re-asked once then held; a transport
failure or an unreadable answer raises `BeatHeld` for P6). What a conflict BECOMES is the
code's by kind (D9), never the judge's: event vs event → contested; event or state over a
belief, and the newer as_of of two states or two beliefs → supersedes (the older re-kinded
`belief`, status `superseded`, its text, sources and verdict untouched; the newer appended
resolved); the same year or any other pairing → contested (one claim, the second source
appended, every source carrying its stated value). `same` appends the source to ONE claim
(`corroborated`, verdict re-bound over both spans); `new` appends the claim under a fresh id;
a `new` STORY is reported, not assembled (its record needs the narration the runner holds).
`MergeOutcome.rerun` names every beat whose resolved texts changed: its `narration.claims_hash`
is stale by design and P4/P5 must rerun (slice 7's runner). `apply(outcome, beats) ->
list[Beat]` is pure and refuses a held outcome. The calibration classes `contested_value` and
`superseded_belief` run under the new `merge` detector (scripted on the mock, $0);
`state_as_event` stays pending.

**Proves:** `tests/test_ingest_merge.py::test_judge_and_signature_disagree_holds_new_story`,
`::test_supersedes_rekinds_old_claim_as_dated_belief`,
`::test_same_claim_appends_source_not_beat`.

### Slice 7: Job runner, front door, review queue

**Files:** create `./src/ingest/jobs.py` (reuse `JobStore` from `src/onboard/jobs.py`), `./src/ingest/run.py`
(phase sequencing, resume at last completed phase), `./src/api/routes/ingest.py` (§5 routes),
`./frontend/ingest.html`; modify `src/server.py` to include the router.
**Produces:** `run_job(job_id, store, client) -> None`; the routes in §5.
**Proves:** `tests/test_ingest_routes.py::test_publish_refuses_while_an_item_is_held`,
`::test_decision_is_bound_to_shown_hash`; `make test-workbench` shows a job reaching P7 on
the mock client with a screenshot.

### Slice 8: Publisher converge and graph export

**Files:** modify `scripts/upload_paris.py` (export mapping §2, withdraw step §6);
`scripts/db_parity.py` reports withdrawn counts.
**Proves:** `tests/test_upload_paris.py::test_publish_withdraws_beats_absent_from_file` on
7688; a before/after count on 7687 pasted into the slice report.
The withdraw step also retires the four seeded beats that exist in the dev graph and in no
file — verbatim bodies from `src/seed/narratives.py`, uploaded without a `beat_id` — since
they are absent from every file by construction; the before/after count names them.

### Slice 9: Proof chunk under the new model

**Files:** none new. Run one job on
`Books/new_york/lonely-planet-new-york-city/chunk-07-upper-east-side.txt`, then a second on
the Frommer's Upper East Side chunk so the merge fires on the Guggenheim.
**Proves:** the stories match §2's shape by inspection; an `acceptance` agent and a
`tour-adversary` panel read every Guggenheim beat; the two chunks' cost matches the
estimate within 25%. The ten hand-read beats seed `./fixtures/ingestion/defects.json`.
**Stops the line if:** the merge holds more than a third of stories, or the tour bar
(`_test-golden` on 7687 with only this chunk swapped) regresses.

### Slice 10: Batch re-extraction and swap

**Files:** Makefile target `ingest-batch CITY=` (every chunk folder under `Books/{city}`
and every pinned revision under `data/{city}/wikipedia`, resumable).
**Proves:** per-city coverage report (beats, places, lenses, sensory anchors) against the
old file; `_test-golden`, tour grade and invariants green on 7687; one cloud publish per
city with `CONFIRM_CLOUD_WRITE=1`, human at the keyboard.

### Slice 11: Cleanup

**Files:** delete `.claude/commands/unified-beat-extract.md`, `pipeline-chunk.md`,
`pipeline-batch.md`, `beat-dedup.md`, `beat-wipe.md`, `beat-enrich.md`, `beat-from-book.md`,
`vallois-reextract.md`, `fact-check.md` (replaced by the staleness pass, its own later
slice); replace `src/onboard/beat_draft.py` with a call into the engine; delete the
superseded docs named at the top; delete `_to_be_deleted/` in full, every `data/*/*.bak-*`
and `beats.legacy.json`. This is the only slice that deletes, and it runs only after the
cloud publish has served tours for a week without a rollback. The same invariant as slice
0 applies: every reference in this spec to a file this slice deletes, including the skill
and drafter paths named above, is rewritten in this slice's commit as a removal narration,
so the spec never claims a file that is gone.
**Proves:** `make lint` (process lint refuses dangling doc refs) and `make test` green.

---

## 9. Self-review against the interview

- D1–D14 each map to a slice (D1→0, D2/D3/D5/D6→1, D4→5, D7→6, D8/D9→1+6, D10/D14→10,
  D11→2+7, D12→7 intake, D13→7).
- Not covered here, deliberately: the staleness pass (D9's fact-check replacement) is its
  own spec once the corpus exists; a subscription-billed worker behind the seam is not
  built until wanted.
- Names used across slices: `Beat`, `Claim`, `Source`, `Verdict`, `Narration`, `Story`,
  `ModelClient`, `Completion`, `CostEstimate`, `MergeOutcome`; functions `validate`,
  `claims_hash`, `bind`, `decompose`, `group`, `judge_claims`, `omissions`, `narrate`,
  `judge_narration`, `merge`, `apply`, `run_job`. A later slice that renames one edits this
  file in the same commit.
