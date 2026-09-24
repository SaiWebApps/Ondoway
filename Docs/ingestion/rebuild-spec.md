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
| D7 | Extraction is corpus-blind; merge is a separate per-place pass with a non-author judge plus a deterministic signature hint; the signature's positive evidence against the judge goes to a person (amended in slice 10: a signature that matches nothing is no evidence, so it no longer vetoes a judge's match; amended 2026-09-21: a signature matches only when the existing claim covers at least 0.7 of the NEW claim's tokens — the merge contract's direction — never by the overlap coefficient, under which a short existing claim inside a longer new one "matched") | Judge may never include the author; small candidate sets per place. Slice 10: across job 2's 12 judged stories the lexical signature matched 1 claim while the judge named 4 correct matches, and hold-on-any-disagreement held or re-asked every one of them away |
| D8 | Status lives on the claim: resolved / contested / superseded. Contested claims are not voiced; the story still ships | 29 stories dark today for one claim each |
| D9 | Every source has an as-of date; claims have a temporal kind (event / state / belief). Recency resolves state claims only. Supersession is a merge outcome, not a contest. Fact-check becomes a staleness pass | Richard III: both sources right as of their date |
| D10 | Re-extract everything; do not migrate. 1,835 verdicts discarded | Proof chunk found 42 stories where migration would keep 24 |
| D11 | Engine in code; all model calls via the API; model-client seam kept for a subscription worker later | One place to upload and watch; nothing depends on a chat window |
| D12 | Books are chunked once by `/book-prep` in the Claude interface; the chunk folder plus manifest (with as-of and rights basis) is the job input. Websites are one unit, split mechanically at headings only when over the unit ceiling | Simple, inspectable, one-time per book |
| D13 | Review queue blocks only: merge disagreement (new story held; since slice 10 also a new story left below its arc minimum once its matched claims fold away), narration flagged after second ask (beat held), new place (its beats held). Contested claims queue without blocking. Everything else is a log line | Workbench design: 30-second reads cannot see the defects |
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
- No lift between `narration.text` and any span of the beat, outside attributed
  quotation: the P4 gate refuses a run of seven words (`narrate.NARRATION_LIFT_GATE_RUN`;
  six was ruled first and refused 40% of job 1's narrations on ordinary phrasing);
  the validator's floor is eight. Lift is a NARRATION rule only (owner ruling 2026-09-13
  after the proof chunk: the claim-level test deleted 139 facts) — a claim is provenance,
  never spoken. Formerly: no lift ≥8 between `narration.text` and any span, outside attributed
  quotation. No provenance leak (regex, slice 5).
- `beat_type ∈ {stop_orientation, transit, sidebar, practicalities}` beats skip the arc rule;
  every other beat has ≥2 claims. `practicalities` (prices, hours, tickets, transport — owner
  ruling 2026-09-13) is never voiced in the bare present and is not published to the graph
  until the engine has a channel for it; `stop_orientation` means SPATIAL (where the listener
  stands, what they face, where the entrance is), never a listing.

**Graph export** (slice 8) keeps the tour engine's contract unchanged: `script_body` =
`narration.text`; `key_claims` = texts of resolved claims; `beat_length_class` computed from
`duration_sec` (`micro` <8 s, `seasoning` <32 s, `mid` <80 s, else `anchor`); `lenses`
exported as the existing `lens` relationship, one per entry. A new-shape record exports NO
`pronunciation` and NO `physical_cues` (unjudged author free text the engine would voice —
proof-chunk panel, 2026-09-13) until those fields are judged. No engine file changes.

## 3. The engine: phases and gates

Per chunk job. A phase's output is written to the job before the next phase starts, so a
failed job resumes at its last completed phase.

| Phase | Model | Output | Code gates before the next phase |
|---|---|---|---|
| P0 intake | none | units of text with `source_id`, `as_of`, rights basis | manifest fields present; a website unit over the ceiling is split at headings and the job logs it |
| P1 decompose | author | claims `{text, kind, span}` per unit, one fact each (slice 10: a relation the source states is one claim naming both sides; a list is one claim per item) | span verbatim in unit; NO lift test on claims (owner ruling 2026-09-13 — a claim is never spoken; the lift gate is P4's); no book furniture (leak regex); self-contained (no bare pronoun subject); `kind` present, default `state` when the judge marks it ambiguous |
| P2 group | author, shown the unit's candidate places (slice 10: every `poi-raw.json` name whose distinctive words all occur in the passage, to be written exactly) | stories `{title, place, lenses, beat_type, enrichment, claim_ids}`; a room, gallery, floor or court inside a place is its `sub_location`, never a place (owner ruling B, 2026-09-19) | every claim in exactly one story; place resolves to `poi-raw.json` or is flagged `new_poi`; structural types allowed 1 claim, others ≥2 |
| P3 judge claims | judge | per claim: entailed yes/no and the temporal kind read from the span (slice 9: a wrongly kinded claim is re-kinded, never refused); per unit: facts no claim carries, and (slice 10) claims that state more than one fact — logged as `compound_found`, no re-ask yet | a claim refused once is re-asked with the judge's reason quoted back; refused twice is dropped and logged; an omission finding that no claim already states (a finding whose signature equals a claim's is dropped, `omission_already_carried`) re-asks P1 once for that unit — since slice 10 (owner ruling A, 2026-09-19) for the omitted facts ONLY, the first pass's claims shown; the new claims are appended, grouped and judged on their own, and a re-ask that fails keeps the first pass |
| P4 narrate | author, sees claims only | narration text | no lift vs any span; no leak; no framing regex (`imagine`, `picture`, `envision`); duration computed |
| P5 judge narration | judge | per sentence entailed yes/no | a failing sentence re-asks P4 once with the sentence quoted back; still failing → `narration.flags` set, `review.held = true` |
| P6 merge | merge judge + signature | per new story: same / new / supersedes; per claim, against ANY claim at the place whatever the story verdict: new / same / conflict — candidate beats and claims named by handle (`b1`, `b1.c02`), never by id | the signature is a one-way tripwire, measured in the contract's direction (`merge.covered`: the existing claim covers ≥0.7 of the new claim's tokens): a signature match on a claim the judge called `new`, or on a different claim than the judge named, holds the new story (queue item); a judge match the signature cannot see applies. Every raw answer is logged (`merge_answered`), every matched claim logged with both texts and the new span (`merge_folded`). A matched claim FOLDS into the claim that holds it: conflict → the D9 kind rules (`contested`, or supersedes → old claim `belief`, dated); same → one claim, sources appended. A `new` story's own beat keeps only its unmatched claims and is narrated again; left below its arc minimum it is a queue item; fully folded it writes nothing (`merge_absorbed`). Any claim change → P4/P5 rerun for that beat |
| P7 commit | none | `beats_io.commit` | the slice-1 validator, extended; nothing reaches disk otherwise |

Refusal loop: exactly one re-ask per phase per item, then drop or hold. No item is padded,
re-classed or synonym-swapped to pass.

Cost estimate: before P1, `messages.count_tokens` over every unit, multiplied by the
per-phase call shape, printed as the job's first event. Batch API is used for P1, P3 and P5
(no latency need, half price) — SUPERSEDED BY OWNER RULING 2026-09-14, NOT YET BUILT: P1
decompose (the first ask, its re-ask and the omission re-decompose) stays batched; the
per-story rounds move to synchronous calls — the P3 and P5 judge rounds and the P3 claim
restates (one batch round per story made a job 71 sequential rounds, and slow batch days cost
two paid attempts of job 2); see `Docs/ingestion/2026-09-14-ingest-transport-and-resume-design.md`. Amend this
paragraph to the built behaviour when that design lands.

## 4. Calibration by defect injection

`./fixtures/ingestion/defects.json`: ten known-good beats (from the slice-9 proof chunk,
hand-read) with planted defects, one per record: a fabricated date, a deleted claim, a
wrongly attached cause, an 8-word lift, a guidebook attribution, a framing sentence, a
state claim kinded as event, a contested value, a superseded belief, an omitted fact in
the unit — and, since slice 10, two merge classes that pin the judge's `same` from both sides:
a second source sharing only an element (a date) with a claim, which must stay `new`
(`shared_element`), and a true paraphrase no signature can see, which must fold as `same`
(`same_fact`); and two for the coverage check's compound flag: a claim bundling two facts,
which must be named (`compound_claim`), and a relation the passage states, which must not be
(`stated_relation`). The `ingest-calibrate` target runs the judge phases over them and prints caught /
missed per class. A judge prompt change that lowers a class's catch rate fails the target.

## 5. Front door and review queue

`POST /ingest/jobs` with `{city, source: {kind: "book", chunk_dir} | {kind: "url", url},
as_of, rights_basis}` → 202 + job id. `GET /ingest/jobs/{id}` snapshot; `GET
/ingest/jobs/{id}/stream` SSE of phase events. `GET /ingest/review?city=` lists held items
ranked by content held back; `POST /ingest/review/decision` records `{item_id, decision,
decided_by}` bound to the hash of what was shown; a merge held after the judge answered shows
that answer per claim (`shown.judge_claims`: verdict, every named claim with its text, the values,
the reason). `POST /ingest/publish?city=&target=`
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
- [x] Publisher converge merged (slice 8, 2026-09-12) and proven with a before/after count on
      the lane-2 dev graph (7692; 7687 belongs to the main checkout — the same files, the
      same seeded beats).
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
*(Slice 10 amended D7 and rebuilt P6's matching; the text above is slice 6 as built. The
first node now proves the tripwire — a signature match the judge calls `new`, or matches
elsewhere, is held — and a judge match the signature cannot see applies; the judge answers
by handle and a claim may match in any beat at the place. See §3's P6 row and slice 10.)*

### Slice 7: Job runner, front door, review queue

**Files:** create `./src/ingest/jobs.py` (`IngestJobStore`, a subclass of `JobStore` from
`src/onboard/jobs.py` — the onboard store assumes only `.events`/`.status`/`.error` of a job —
carrying the per-phase outputs and the D13 review queue keyed by the hash of what is
shown), `./src/ingest/run.py` (phase sequencing, resume at last completed phase, record
assembly for a `new` story, the P4/P5 rerun for `MergeOutcome.rerun`, the P1 omission
re-ask through `decompose(..., omitted=)`), `./src/api/routes/ingest.py` (§5 routes),
`./frontend/ingest.html`; modify `src/api/app.py` to include the router inside the
workbench gate (the FastAPI app lives there; `src/server.py` is the stdlib dashboard),
`scripts/beats_io.py` (`commit(..., chunks_root=)` so P7 grounds spans under a data root
other than `data/`), `tests/test_workbench_ui.py` (the browser proof, on the mock scripted
from a file the test writes, writing only under a tmp `INGEST_DATA_ROOT`).
**Produces:** `run_job(job_id, store, client, *, data_root=None) -> None` (never raises: a
failed job is `error` with its completed phases kept; a held item is a queue item, never a
failed job); the routes in §5, with two honest gaps this slice left: `POST /ingest/publish`
answered 501 naming slice 8 once nothing was held (until slice 8 wired the converge; a 202
would have claimed a publish that never happened), and a `url` source is accepted by the route and
refused at P0 (no pinned-revision reader exists; slice 10 owns the Wikipedia terms). P6 is
skipped with a `merge_skipped` log line when no beat exists at the place. The client comes
from `INGEST_PROVIDER` (`mock` + `INGEST_MOCK_SCRIPT`, or `anthropic`; unset fails closed).
**Proves:** `tests/test_ingest_routes.py::test_publish_refuses_while_an_item_is_held`,
`::test_decision_is_bound_to_shown_hash`; `make test-workbench` shows a job reaching P7 on
the mock client with a screenshot (`TestIngestPanel::test_job_reaches_p7_on_the_mock`).
**Known gap:** `data/{city}/beats.json` is still legacy-shape for both cities, so a job
against the real data root stops at P0 (`legacy-shape` refusal, nothing spent, nothing
written) until the slice-10 swap re-homes the city; where the new-schema file lives before
then is the owner's call.

### Slice 8: Publisher converge and graph export

**Files:** modify `scripts/upload_paris.py` (export mapping §2, withdraw step §6);
`scripts/db_parity.py` reports withdrawn counts.
**Proves:** `tests/test_upload_paris.py::test_publish_withdraws_beats_absent_from_file` on
the lane's pytest graph (7688; 7690 on lane 2); `::test_new_shape_export_mapping` (the §2
mapping: script_body, resolved-only key_claims, the length-class thresholds, one lens
relationship per entry, a held beat not published); `tests/test_db_parity.py::
test_withdrawn_beats_are_reported_not_drift`; `tests/test_ingest_routes.py::
test_publish_converges_the_graph_on_the_file` (`POST /ingest/publish` runs the converge for
`target=local`, refuses `cloud`); a before/after count on the dev graph pasted into the
slice report (built in the lane-2 worktree, so 7692 stood in for 7687).
**Design calls (2026-09-12):** the withdraw set is "not MERGEd by this publish", so a beat the
file holds but does not publish (legacy `disputed`, new-shape `review.held`) is withdrawn
rather than left live with stale content; a city's beats are reached through its POIs'
`HAS_BEAT` (a NarrativeBeat carries no city); `db-parity` compares ACTIVE beats only and
prints withdrawn as its own line on both sides (repo: blocked/held, db: withdrawn). The
converge is not one transaction: the MERGE and the withdraw are separate implicit
transactions, so a crash between them leaves the graph merged-but-not-withdrawn; the
re-publish is idempotent and repairs it.
The withdraw step also retires the four seeded beats that exist in the dev graph and in no
file — verbatim bodies from `src/seed/narratives.py`, uploaded without a `beat_id` — since
they are absent from every file by construction; the before/after count names them.

### Slice 9: Proof chunk under the new model

**Files (as built, 2026-09-12):** `scripts/ingest_job.py` + `make ingest-job CITY= CHUNK_DIR=
[ARGS=]` (declared with `render-key`; sets `INGEST_PROVIDER=anthropic` and
`INGEST_DATA_ROOT=data-ingest`, the gitignored root the new-schema file lives in until the
slice-10 swap; prints the estimate and stops unless `--yes`; writes the job log to
`data-ingest/{city}/jobs/{job_id}.jsonl` and closes with the P6 hold rate and the P3
precision counters); `IngestSource.chunks` so one job ingests one chunk of a book;
`tests/live_graph.py` honours every lane's dev port so `_test-golden` counts on a lane; the
P3 KIND question (`prompts.judge`, `judge_claims._rekind`, slice-6 ruling 5) with the
`state_as_event` calibration detector. Run one job on
`Books/new_york/lonely-planet-new-york-city/chunk-07-upper-east-side.txt`, then a second on
the Frommer's Upper East Side chunk (`chunk-05-ch05-uptown`) so the merge fires on the
Guggenheim.
**Proves:** the stories match §2's shape by inspection; an `acceptance` agent and a
`tour-adversary` panel read every Guggenheim beat; the two chunks' cost matches the
estimate within 25%. The ten hand-read beats seed `./fixtures/ingestion/defects.json`.
**Stops the line if:** the merge holds more than a third of stories, or the tour bar
(`_test-golden` on 7687 with only this chunk swapped) regresses.
**Job 1 (2026-09-12, LP chunk-07, `--as-of 2022`):** 20 beats committed to `data-ingest/new_york/beats.json`
for $1.81 (job 1's first attempt truncated at the old 8,000-token P1 cap: thinking is on
by default for claude-opus-5 and the client sets none; every cap is now sized for it,
`tests/test_ingest_output_caps.py`). Measured: hold rate n/a (no beat at any place — job 2
fires the merge); leak-gate drops 1 of 148; 139 drops were the LIFT gate on claim text,
partly because the empty first P1 answer consumed the unit's one gate re-ask; P3 precision:
9 attempt-one refusals over ~100 first judgements, at least 6 false by hand-read, including
"four years after 1939"; 20 re-kinds (belief→state). Cost: the estimator priced every row
over the passage and every output at its cap; re-anchored on the run (input basis per row,
measured fan-out and outputs, re-ask rates), job 1's expected without P6 is $2.03 against
$1.81 — inside 25%, unvalidated until job 2. Panel (acceptance + three adversaries) REJECTS
the Guggenheim stop: the origin story did not survive the lift gate; decisions owed to the
owner are in the session report. The tour bar on lane 2 (7692): `_test-golden` 9 passed,
`_test-invariants` 13 passed, baseline only — "only this chunk swapped in" is not executable
yet (the converge is whole-city and the validator refuses a mixed file; slice 10).

**Job 1, runs 2-4 (2026-09-12..14, LP chunk-07):** the owner's rulings after run 2 moved the
lift gate off claims onto narration (seven words, proper names exempt) and the run was
repeated. Run 3: 35 beats, $2.88 — 34 claims were dropped because the author's re-asks
straightened the passage's curly quotes, so `gates.ground_span` now stores the passage's own
text for a citation only its typography differs from (4986889), and the place resolver
ignores full stops ("Solomon R Guggenheim Museum" is the POI, 0809ecc). Run 4 (the one in the
file): **29 beats, $1.77**, 1 span drop, 0 place holds that name a known POI, the Guggenheim
under its canonical name. The narration lift gate at seven refused 21 of 33 first narrations
and held 4 stories outright — held stories used to die with the process, so every review-queue
item is now written to `data-ingest/{city}/held/{job_id}.jsonl` as it is queued (0b99999).

**Job 2 (2026-09-15, Frommer's `chunk-05-ch05-uptown`, `--as-of 2024`): 61 beats in the file
(29 + 32), $3.9126 against an expected $3.41 — inside 25%, so the estimate is validated on a
second chunk.** It took five attempts and four transport or control-flow defects, each fixed
test-first: the live client's one-hour batch poll ceiling (24 h now, 971f48e) with a
transient-error guard and backoff (913d651, 21a44a9); a 404 on a batch 169 ms after
submission (a grace window, 0d60d17); an invalid merge answer that raised in P6 where P1-P4
caught (`test_one_failing_item_never_ends_the_job` covers every phase now, 0a4cc7d, plus the
reverted-merge queue fix 9568574); and one $0 no-op where the API refused a valid P1 request
as `invalid_request_error`. Because a dead job repaid everything, phase outputs are now
written to `data-ingest/{city}/jobs/{job_id}/P*.json` before each phase event and
`--resume JOB_ID` continues from the first missing phase (6aeecd6).

**P6 measured on real data: stories 35, merge_judged 12, held 3 — a hold rate of 0.25 that
measures nothing, because P6 completed no merge at all (see the panel below).** 4 stories skipped (no beat at the place), 19 held as new
places (Cooper Hewitt and Neue Galerie are `name_variations` gaps, the Met's galleries and
"Frick Madison" are sub-locations the author invents as places — both carry into slice 10).
Every judged story came back `new` with every claim new: the two books' uptown chapters do not
overlap at claim level, so the Guggenheim holds 8 beats side by side (5 LP + 3 Frommer's) and
no claim-level merge has yet been exercised on live data. Two of the three holds are the judge-vs-signature
disagreement — and the panel below shows that rule holding the only two matches the judge
found; the third is the invalid answer above.
Other measurements: 43 attempt-one claim refusals, 31 drops (9 of them the model writing a
newline for the passage's `’` — the span corruption is still open), 3 leak-gate drops, 37
omissions found (a full second P1/P2/P3 pass, which is why the run took 9.4 h), and zero
transport errors across the run.

**Panel on the merged Guggenheim stop (acceptance + three adversaries on opus/sonnet/fable,
2026-09-16): UNANIMOUSLY REJECTED.** What they could not break, each re-derived
independently: all 33 claim spans are in the two chapters byte-for-byte after whitespace
folding; `gates.lift` is clean on all 8 narrations at run 7 and 8; no provenance leak, no
framing verb, no apparatus; held and `practicalities` beats really are fenced at publish and
`pronunciation`/`physical_cues` really are stripped at export. The grounding substrate holds.
What they broke:
- **P6 completed 0 merges out of 12 judged stories, by two independent routes.** 9
  `merge_decided` are `story: new` with every claim `new` — including two same-fact pairs at
  this stop ("designed by Frank Lloyd Wright" and "critics rejected it"), so the judge emits
  false `new`. The other 3 were HELD: the judge did return `same` once and `supersedes` once
  (plus `conflict` at claim level), and each time the signature hint matched no beat, so the
  arbitration rule held the story — the hint is 0 for 3 on exactly the cases the judge got
  right. **The number to move in slice 10 is 0 merges completed / 12 judged (3 of 3
  judge-found matches held), not the 0.25 hold rate**, and three components are indicted: the
  judge's false `new`, the signature hint, and the hold-on-disagreement arbitration. §2's
  worked example (LP and Frommer's corroborating "1959" into one claim with two sources) did
  not happen.
- **Claim ids collide across jobs in one corpus**: `c01` in `beats.json` is both the
  Guggenheim's "designed by architect Frank Lloyd Wright" (job 1) and Cooper Hewitt's "the
  design division of the Smithsonian" (job 2), so every merge reference by claim id — the
  events, the holds, the pairs above — is ambiguous outside its own job.
- The stop is two guidebooks side by side: the hostile-reception story told twice (beats 2 and
  7), the ramp twice (4 and 6), Frank Lloyd Wright introduced cold four times, two
  pronunciations of "Guggenheim", and a beat that ends "The architectural critic Herbert
  Muschamp wrote a description of this building" without ever giving it (the quote itself was
  refused at P3 and its fragments kept as bare assertions).
- Defects a listener would hear: "almost thirteen years passed between the design and the day
  it stood finished" (the source says construction was DELAYED 13 years; 1943→1959 is 16), one
  beat saying the building "begins at the top floor" against another's "installed from the
  bottom to the top", the same exhibition list voiced twice in consecutive sentences
  (c155 ⊂ c156 ⊃ c157, three overlapping claims from one source sentence), and literal `\u`
  escapes inside `narration.text` that a TTS pass would read aloud — `\u2014` ×5, `\u00e8` ×2,
  `\u00f1` ×1 across 6 beats corpus-wide.
- The honest size of the stop is **6 beats / 435 words / 174 s**, not 8/512/205: the
  `practicalities` beat and the held beat are not servable.
- The `’`/`–`-to-newline span corruption is 5 of the 13 span drops across both jobs, and it is
  load-bearing: it deleted Frommer's 2024 admission claim (c160), so P6 never saw a conflict
  and the stop voices LP's 2022 "$25, children free, pay-what-you-wish 5-8pm Sat, cash only"
  against the newer book's "$18 seniors/students, free for kids 11 and under, Sat 6-8pm".
- Record fields the engine reads are wrong: `narrative_function` is free prose on all 8 beats
  while `src/tour/beat_select.py` orders by `hook|establishing|deepen|climax|callback`, so
  every beat sorts last (the P2 schema types it as a bare string); `sub_location` is invented
  ("annex galleries"), case-split ("Rotunda ramp" / "rotunda ramp") and IS exported, unlike the
  other enrichment; `kid_friendly: yes` sits on the beat that mentions nobody jumping to their
  death; `entities` keeps names (Mondrian, Miró) from a claim P3 dropped.
**Both halves of this slice's stop condition are VOID, not passed.** "The merge holds more than
a third of stories" cannot be read off a P6 that completed no merge at all, and the tour-bar
half ("`_test-golden` on 7687 with only this chunk swapped") is still not executable — the
lane-2 run was a baseline that never saw these beats, which are gitignored and cannot be
swapped in until slice 10's converge tooling. Slice 9's CODE is committed and its cost estimate
is validated on two chunks; the corpus it produced is not shippable; the suite bar is deferred
to slice 10. Carried forward, in order: the merge trio (judge false `new`, signature hint 0/3,
the arbitration rule), the `’`/`–`-to-newline span corruption, the claim-id collision,
`narrative_function` typed against `src/tour/beat_select.py`'s vocabulary, the literal `\u`
escapes, and the `sub_location`/`kid_friendly`/`entities` defects above.

### Slice 10: Batch re-extraction and swap

**Files:** Makefile target `ingest-batch CITY=` (every chunk folder under `Books/{city}`
and every pinned revision under `data/{city}/wikipedia`, resumable).
**Proves:** per-city coverage report (beats, places, lenses, sensory anchors) against the
old file; `_test-golden`, tour grade and invariants green on 7687; one cloud publish per
city with `CONFIRM_CLOUD_WRITE=1`, human at the keyboard.

**Slice 10, part 1: the merge trio (2026-09-17; owner's bar: a merge that actually merges,
proven on the Guggenheim, before any bulk run).** Diagnosis from job 2's log ($0): 3 of the 4
`merge_reasked` were the judge naming a claim match inside a story it called new, which the
answer contract forbade (one re-ask talked it out of a correct match); the lexical signature
matched 1 claim across all 12 judged stories and its at-least-half-of-the-story vote discarded
even that; hold-on-any-disagreement held the rest. Built test-first (869103d): every raw judge
answer logged (`merge_answered`); beats and claims answered by handle; a claim may match any
claim at the place whatever the story verdict and folds into the beat that holds it; the
signature is a one-way tripwire (D7 amended); the runner writes a new story's beat from its
unmatched claims only and narrates it again. `make ingest-merge-replay CITY= JOB= [PLACE=]`
($0) rebuilds the beats file a finished job started from (refused unless its sha matches the
job's) and copies its P0-P5 into a sandbox root; the printed `--resume ... --data-root` run
replays P6 onward there, never touching the city's file.

*Guggenheim replay — pass criteria, registered before the paid run (amended at the judge's
PROVE-FIRST so every one is decidable without re-argument).* Input: job `ed4b6064…`
(Frommer's chunk-05), place "Solomon R. Guggenheim Museum": three narrated stories
(`a-spiral-among-the-boxes` 7 judged claims, `museum-or-cupcake` 3 — its first-pass narration
is P5-held but it still merges — `blockbusters-along-the-ramp` 7; `guggenheim-visiting-details`
has no narration and is skipped) against the 5 LP beats of the 29-beat file (sha
`63a805933ac8c6dc`). Registered verdicts, from the texts, not from a run: c141 (Wright's spiral)
is `same` as LP c01 ("designed by architect Frank Lloyd Wright") and c148 ("the earliest critics
rejected it") is `same` as LP c03 ("derided by some critics but hailed by others") — the two
same-fact pairs slice 9's panel named; a `conflict` on c148↔c03 is false (the texts agree).
c146 ("this 1959 masterpiece" — a claim about touring the ramps) folding into LP c13 is a FAIL
(it would take the ramp content out of the story), and so is c145 ("begins on the top floor",
the building) folding into LP c23 (Wright's intended route) or c25 (the hanging order). Every
blockbusters claim, c149 and c151 are `new`. A fold of any other claim (c140, c142, c144,
c147, ...) is decided by the PC2 panel alone.
- PC0 isolation: after each run the real `data-ingest/new_york/beats.json` sha is still
  `556830a5342e178c`, and no file outside the run's sandbox is newer than a marker touched just
  before the run.
- PC1 a merge merges: c141→LP c01 and c148→LP c03 both fold as `same`, AND the PC2 panel
  upholds both; no `rerun_reverted` names an LP beat (a fold dropped because its target's
  re-narration was held is a FAIL, not infra).
- PC2 no false fold: every `merge_folded` pair judged by a panel of three on opus, fable and
  haiku (never the merge judge's model, Sonnet), majority rules, each member given the pair's
  two texts and the merge prompt's own contract text for `same` and `conflict`, and NOT shown
  these registered verdicts; 0 false `same` or `conflict` (a false conflict marks a true LP
  event `contested`, which D8 silences).
- PC3 no tripwire hold at the place (only c141 has a signature match; a hold there is a FAIL,
  diagnosed from `merge_answered`).
- PC4 integrity: the job commits in the sandbox; P7's validator passes; every Guggenheim beat
  whose claims changed carries a fresh `claims_hash`; each of the 17 judged claims has EXACTLY
  one terminal disposition — folded, a claim of a written beat, `merge_absorbed`, or a claim of
  a queue item P6 made — the `narration_held` item P5's hold queued before the merge does not
  count.
- PC5 stability: a second run from a fresh prep; PC0-PC4 must hold in both; the two fold sets
  are reported side by side and differing sets are not by themselves a FAIL.
Operating rules: back up `data-ingest/new_york` and the sandbox under `$HOME` first; a run that
dies gets a FRESH prep, never a re-resume (P6 writes events and `held/` before its phase file);
nothing mechanical caps a run's spend (the estimate gate only refuses an unprinted estimate),
so the stop rule — actual over $2 — decides only whether the second run starts.
Known cost of the design, for the panel: folding a compound claim keeps the EXISTING text, so
c141's "spiral among the towers of Fifth Avenue" leaves the Frommer's story when it folds; c148
is compound too, which is benign only because c149 carries its Newsweek headline. Out
of scope here and still carried: the `’`/`–` span corruption (it deleted Frommer's 2024
admission claim), claim-id collisions across jobs, `narrative_function` vocabulary, literal
`\u` escapes, `sub_location`/`kid_friendly`/`entities`; the judge's step-E carry-forwards (a
reverted fold queues the whole story although its remainder beat was written; a
`beat_id_collision` drops a remainder after its folds landed; a remainder queue item carries
the first-pass narration; a P5-held story that then merges leaves its pre-merge
`narration_held` item in the queue, stale). The stop's shippability is the panel's separate verdict.

*Guggenheim replay result (2026-09-18 UTC, owner go; two runs, $0.053 + $0.061): FAIL.* PC0 held in
both (the live file untouched) and PC4 in run 1 (17 claims, one disposition each), but PC1,
PC2 and PC3 failed. The now-logged raw answers show two causes. Precision: in BOTH runs the
judge folded c145 into LP c23 and c146 into LP c13 ("both reference the same 1959 date") —
`same` on a shared element. Granularity: P1 emits compound claims against CONTEXT.md's "one
atomic factual statement" (c141 = Wright + spiral + the towers of Fifth Avenue; c148 = critics
+ Newsweek; c149 is inside c148, c155 inside c156), so for them `same` drops content and `new`
trips the tripwire, and the verdict flipped between runs: c141 folded in run 1 and was held in
run 2; c148 folded in run 2, and in run 1 the judge named c148 as c149's match — a claim of the
new story, which the contract cannot express — and the story was held. Fix, owner-approved in
two steps: (1) the merge prompt defines `same` as containment (the existing claim states
EVERYTHING the new claim states; sharing a date, name, place or word is not sharing a fact),
names only listed claims, carries a worked example from another city, and calibration gains
`shared_element` (precision) and `same_fact` (recall — a stricter `same` must not bring back
slice 9's false `new` on real duplicates); (2) atomic claims at P1, with a re-extraction of both chunks (owner go on the
cost owed). Step-1 confirmation replay criteria, registered before it runs (two fresh
sandboxes, each claim judged on its FINAL answer after any re-ask): S1 c145 and c146 come out
`new` in both runs; S2 no P6 answer-contract hold; S3 PC0 and PC4 hold; S4 recall, which the
replay cannot measure (its only same-fact pairs are compound claims): one live calibration run
under the new prompt catches `same_fact`, `shared_element`, `contested_value` and
`superseded_belief` (owner go owed; the latter two have never been measured live). The
`same_fact` plant (a restatement of "both Wright and Guggenheim had died before it was
completed" with no shared name) does not copy the worked example's shape; `shared_element`
shares it on purpose — it replicates c146's shared-year failure — so its catch is not evidence
of precision beyond that shape. If S1-S4
pass, the claim is "c145/c146 no longer fold and a true paraphrase still does", not "precision
fixed" — the worked example shares c146's shape. Expected and accepted until step 2: compound c141 and c148 come out `new`, so c141's
signature match holds its story (the tripwire doing its job on a compound claim), and no
Guggenheim merge completes — PC1 waits for atomic claims.

*Step-1 confirmation (2026-09-18 UTC, owner go, on AC): PASS on S1-S4.* One live calibration
run under the new prompt caught all twelve classes, the four merge classes each 1/1 — the
first live measurement of `contested_value` and `superseded_belief` (projection $0.63; the
harness prints no actual). Two replays from fresh preps ($0.039 + $0.053): c145 and c146 came
out `new` in both (the judge: c146 "combines the 1959 completion date with a description of
touring via curving ramps, more than" LP c13); no answer-contract hold; the live file untouched
and all 17 claims placed exactly once in both. The claim is exactly that — "c145/c146 no
longer fold and a true paraphrase still does" — not "precision fixed". As registered, no
Guggenheim merge completed: compound c141 came out `new` and its signature match held its
story in both runs, and compound c148 came out `new`. One residue: in run 2's FIRST answer the
judge again named c148 as c149's match despite the rule; the re-ask fixed it. PC1 now waits on
step 2 — atomic claims at P1 and a re-extraction of both chunks.

*Step 2, code (2026-09-18 UTC, owner go on the code only; judge PROCEED with five amendments):*
P1's prompt requires one fact per claim, with a worked example from Rome splitting all three
compound shapes seen here (a relative clause, two facts joined by "and", a list beside its own
items); the carve-out is only a relation the passage itself states — never two events that
merely follow each other — and the re-ask prompt carries the rule too. The P3 coverage call
(the omission check: the claim judge, one batch call per unit, no new round) numbers the claims
and also names each claim that states more than one fact with the facts it bundles; findings
naming a real claim are logged `compound_found`, and one naming an id the unit lacks is dropped
and logged `compound_unknown`. It is LOG-ONLY by the
judge's smaller-path ruling: a compound-triggered P1 re-ask would re-decompose a COMPLETE set
under attempt-2 semantics, dropping split fragments that fail `self_contained` with no coverage
check after — switched on only if compounds survive the prompt rule in a measured run.
Calibration gains `compound_claim` (recall) and `stated_relation` (precision). Owed before any
paid extraction: (1) one live calibration run with owner go — changing the coverage prompt
voids `omitted_fact`'s live measurement, and the two new classes have none; (2) pass criteria
registered BY CONTENT (re-extraction renumbers every claim id), in a sandbox so the live file's
sha stays the PC0 baseline; (3) `P1_EXPECTED_OUTPUT_TOKENS` (9,000, measured before atomic
claims) re-measured on that run — the 64k cap has the headroom. Also recorded: the
`omitted_fact` record's own c05 is a list claim, a shape the new P1 rule forbids (its score is
unaffected — the detector reads only `omissions_found`), and its earlier live figure is void
because the check now asks two questions. The parser still reads an answer without `compound`
as "nothing compound" so the pre-slice-10 mock answers stay valid; live calls always send the
schema, which requires the key, so this matters only if a path ever loses `output_config`.

*Live calibration after step 2 (2026-09-18 UTC, owner go, HEAD 36320cb, projected $0.6471):
FAIL on the registered reading.* `compound_claim` 1/1 (the Wright + "almost overshadows" claim
named and split) and `omitted_fact` 1/1, every other class 1/1 — but `stated_relation` 0/1: the
coverage judge split the planted claim "The Guggenheim's cramped single elevator makes
descending from the top difficult, so exhibitions are installed from bottom to top" (the
passage: "…the cramped single elevator makes this difficult. Exhibitions, therefore, are
installed from bottom to top") into cause and effect, ignoring the carve-out. It also
flagged a list claim and "both Wright and Guggenheim had died" (two subjects) — both correct by
the rule. Not retried. The compound flag over-counts stated relations, so it stays log-only; a
re-ask on it waits for a precision fix and a passing live `stated_relation`. The owner chose to
proceed to the re-extraction with a hand audit in place of the flag.

*Re-extraction — pass criteria, registered before the paid run, by CONTENT (every claim id
renumbers; amended at the judge's PROVE-FIRST).* A fresh sandbox data root
`data-ingest/sandboxes/step2/` (its `poi-raw.json` the repo's, sha `5965554e90d5e1e0` — the one
both slice-9 jobs used — and an empty `beats.json`; every console log written inside it). Job A:
Lonely Planet `chunk-07-upper-east-side`, as-of 2022 (the 12th edition; the Makefile's "2023"
is a stale example), `owned_copy`. Job B: Frommer's `chunk-05-ch05-uptown`, as-of 2024,
`owned_copy`, merging into job A's beats — its own P6 is the merge proof, no replay. Job B
starts only if (i) job A's actual spend is within 1.5x its printed expected (nothing mechanical
caps a running job) and (ii) a $0 audit of job A's output passes the Lonely Planet half of R1
and finds an atomic "Wright designed the building" claim and at least one critics claim at the
Guggenheim — without them job B buys an R2 nobody can read.

Expected pairs at the Guggenheim, Lonely Planet (existing) against Frommer's (new), from the
two passages. `same`: Wright designed the building; the early critical reception (LP's
before-opening "derided by some critics" OR its after-opening "savaged by the New York Times" —
a fold of Frommer's "early critics dismissed the museum" into either passes); the building's
1959 date (only if Frommer's yields a claim stating just that); the collection includes
Kandinsky, and includes Brancusi (only if Frommer's yields a claim stating just that — "given
permanent gallery space" says more and may stay `new`); the address 1071 Fifth Avenue at 89th
Street; adult admission $25; subway 4/5/6 to 86th Street. `conflict`, the newer superseding
(state claims, 2024 over 2022): pay-what-you-wish on Saturdays (5-8pm against 6-8pm); the
opening hours. Registered FALSE in advance: Frommer's Mapplethorpe RETROSPECTIVE folding into
LP's Mapplethorpe photographs in the holdings; a claim about walking the ramps folding into a
completion-date claim; "begins on the top floor" (the building) folding into Wright's intended
route or the hanging order. A pair is MEASURED only when both sides exist as claims that
reached P6 (a practicalities story with no narration skips P6, as in job 2); a pair whose
Frommer's side was dropped upstream is "unproven: upstream drop" with the drop reason — not a
merge failure — and the next step is a $0 diagnosis of that drop, not another paid run.
- R0 isolation: the live `data-ingest/new_york/beats.json` stays sha `556830a5342e178c`; `find`
  over the worktree and `$HOME/ondoway-ingest-backup-2026-09-13` for files newer than a marker
  touched after the backup rsync, excluding the sandbox, `__pycache__`, `.ruff_cache` and
  `.pytest_cache`, finds nothing.
- R1 one fact per claim, by hand audit of every Guggenheim claim of both books against the P1
  rule: Frommer's "Frank Lloyd Wright's delirious spiral of a museum sits among the towers of
  Fifth Avenue" yields a claim stating only that Wright designed the museum; "Early critics
  dismissed the museum (Newsweek's insipid review was headlined 'Museum or Cupcake?')" yields
  the dismissal and the headline as separate claims; the blockbuster sentence yields one claim
  per retrospective or show and no list claim; LP's "the cramped single elevator makes this
  difficult. Exhibitions, therefore, are installed from bottom to top" stays ONE claim; at most
  two Guggenheim claims across both books are compound by the rule.
- R2 a merge merges: both books' Guggenheim beats resolve to the one `poi-raw` place (no
  new-place queue item); every MEASURED expected pair comes out as registered (`same` folds;
  the two conflicts become supersessions); the Wright and critics pairs are both measured; no
  `rerun_reverted` names an LP Guggenheim beat; the R3 panel upholds every fold.
- R3 no false fold: every `merge_folded` at the Guggenheim judged by three panel members on
  opus, fable and haiku (never Sonnet, the merge judge), blind to these criteria, majority rules,
  each given the pair's texts and the merge prompt's own `same`/`conflict` contract; 0 false,
  the three registered-false shapes included.
- R4 no P6 hold at the Guggenheim, tripwire or answer contract.
- R5 integrity: both jobs `status=committed`, P7's validator passes, and each of job B's judged
  Guggenheim claims has exactly one disposition (folded, a claim of a written beat, or a claim
  of a queue item P6 made).
- R6 facts not lost, as a RATE (one fact per claim raises the count): `claim_dropped` over the
  claims P1 emitted, per job, not above slice 9's for that chunk — Frommer's 31 of 76 (16 judge
  refusals, 3 leak drops, 12 spans not in the unit, of which 4 the `'`/newline defect); Lonely
  Planet 1 drop in run 4 (its claim count was not recorded; an earlier LP P1 parsed 82,
  batch msgbatch_01Egvftd…, so about 1.2%), read — loosely, registered in advance — as at most 3%.
- R7 measured, not gated: P1 output tokens per call against `P1_EXPECTED_OUTPUT_TOKENS` (9,000);
  claims P1 emits against slice 9 (Frommer's 76); every `compound_found` hand-classified correct
  or false (the flag's precision on real output).
The stop's shippability is a separate panel verdict, not part of this pass.

*Re-extraction job A (2026-09-18 17:16 → 2026-09-19 05:02 UTC, owner go, sandbox
`data-ingest/sandboxes/step2`, job `bd214b83…`): committed, but the gate for job B FAILED and job B
was not run.* Spend $6.07 against an expected $2.95 (2.06x); 424 judged claims in 44 stories;
every Guggenheim story held as a new place; no Guggenheim beat for a merge to reach. R0 passed
(the live file untouched). R6 failed as registered (31 drops of 190: 16 phone numbers and
websites, 4 other listings, 7 judge refusals, 2 spans, 1 claim twice). Root cause, $0 from the
job's own files and corrected at the judge's STOP: the FIRST pass was good — 190 one-fact claims
including "The Guggenheim Museum building was designed by architect Frank Lloyd Wright", nine
stories resolved to "Solomon R. Guggenheim Museum", the Met not split. One FALSE omission ("The
Guggenheim Museum has a spiral ramp", a listed claim word for word) triggered the runner's
omission re-ask, which regenerated the whole unit under a re-ask prompt carrying none of the
first ask's rules but the one-fact paragraph, then re-grouped it: 424 claims, the listings, the
Wright claim filed under an itinerary story at a new place "Museum Mile" and held at P4, the
Guggenheim renamed "Guggenheim Museum", Met galleries invented as places — the same signature as
slice 9 job 2's 9.4-hour second pass. Neue Galerie and Cooper Hewitt were name mismatches
("Neue Galerie New York", "Cooper Hewitt National Design Museum"); only the Met Breuer is absent
from `poi-raw.json`. Fixed test-first ($0): the re-ask prompt carries every first-ask rule; an
omission finding whose signature EQUALS a listed claim's is dropped (equality, never the overlap
coefficient, which would call a longer finding "carried" by any claim inside it); the omission
re-ask asks for the omitted facts only and appends (ruling A); P2 is shown the unit's candidate
places and the tie-break makes a room inside a place its sub_location (ruling B). Candidate places
are broad by design (73 for the Lonely Planet chunk — "Wall Street" matches "wall") but hold
every name job A missed and exclude a place the passage never names. A re-run of job A needs a
fresh sandbox and an owner go; R0-R7 and the gate for job B stand as registered. Known estimate
gaps, recorded not fixed: P1's re-ask row prices `REDO_PROMPT`'s length, not the supplement's
(which lists every existing claim, ~4.7k tokens for 190) — offset by pricing 9,000 output tokens
for an answer that now covers a few facts; P2's cost rows do not include the candidate-places
block (a few hundred tokens a call). Advisory for the re-run: `omission_already_carried` is not
among calibration's forwarded evidence events, nothing in code stops a supplement restating an
existing claim (only the prompt forbids it), and a noisy candidate list can snap a story to a
sibling place ("Museum of Jewish Heritage" beside "The Jewish Museum") — silently, since it now
resolves — so sample story places against their spans.

*Re-extraction job A, re-run (2026-09-19 13:22 → 17:03 UTC, owner go "go, let it finish", fresh
sandbox `data-ingest/sandboxes/step2b`, job `32d5c8de…`, HEAD 37f782e): committed; the gate for job
B FAILED on R1 and job B was not run.* Stop conditions registered with the owner before launch
(judge-ruled; none fired, per the session's watcher): kill if the first pass exceeded 260 claims, if requests after
`omissions_found` exceeded max(40, 8n) at P3 or max(4, 2+n) at P1, or if the batch-request floor
reached $4.28. Result: 42 beats, spend $2.6840 against an expected $2.9504 (0.91x, gate (i) passes).
The fixes held where job A broke: P1's first pass gave 207 one-fact claims; the omission re-ask added
2 claims (1 P1 and 2 P3 requests, against job A's 26 and 454), and the equality discard dropped one
real duplicate (the Neue Galerie address); every story resolved (0 `place_unresolved`, against 31),
all ten Guggenheim stories at "Solomon R. Guggenheim Museum", and Neue Galerie, Cooper Hewitt and The
Jewish Museum by their `poi-raw` names. Gate (ii)'s content is present in written, unheld beats: "The
Guggenheim Museum building was designed by architect Frank Lloyd Wright" and two critics claims ("some
critics derided its design while others welcomed it"; "The New York Times harshly criticized the
Guggenheim Museum building"). R0 passed (run before the post-run backup rsync); R6 passed at 5 drops
of 209 (2.4%: two one-claim factoid stories, one `’`→newline span, one "guidebook" leak, one judge
refusal). **R1's Lonely Planet half FAILED, which fails the gate:** the registered elevator sentence
was split ("…makes this difficult." and "Exhibitions, therefore, are installed from bottom to top"
became two claims, the "therefore" lost; the same sentence the coverage judge split at calibration),
and seven Guggenheim claims are compound by the P1 rule against a cap of two for both books — two
lists (postcards/TV/films; the velour walls, music and incense), a relative clause (Rebay "who served
as" adviser), an appositive (Peggy, "niece of…"), adult and child prices in one claim, a sequence of
two events (completed 1959 "after both … had passed away"), and the renovation claim (below). The
contrast "derided by some critics but hailed by others" is ONE fact by the rule, not compound. R7
(measured): P1 output 18,586 tokens (15,714 text + 2,872 thinking, `make ingest-batch
BATCH=msgbatch_01Mir62…`) against `P1_EXPECTED_OUTPUT_TOKENS` 9,000; 207 claims against ~82; the
compound flag named 21 claims of which about 5 are compound by the rule and missed four (the lists and
prices, the relative clause), so it stays log-only. P6 was not exercised (0 merges judged, 42 skipped
with no beat at the place — expected for the first job; it proves nothing about the merge).
Found, not fixed: (1) both omission findings were FALSE again (the 1939 opening and the 1943
commission, stated by listed claims in other words, so equality could not drop them), and the
supplement restated them as two claims in a new Guggenheim story — contained, but a duplicate the
prompt forbids and nothing in code prevents; (2) the claim re-ask after a P3 refusal rewrote the
renovation claim as "According to the passage, a renovation … which included building an eight-story
tower …, yielded 50,000 additional square feet" — apparatus plus a relative clause restating another
claim — and the leak gate passed it, as it passed "Lonely Planet's tip describes the Upper East Side
as…"; (3) the Museum of the City of New York story was placed at "Upper East Side" although that
museum is in `poi-raw.json` (ruling B's more-specific-wins, violated), and the Met Breuer (945 Madison
Ave, absent from `poi-raw.json`) at the Metropolitan Museum of Art; (4) one Guggenheim beat held at P5
("this museum" not entailed). Per the registration job B does not run on this gate. Owner decision
owed: fix P1's surviving compound shapes first (a prompt change is provable only live: a red-first test
on a held-out passage, a paid calibration re-run, a fresh job A with owner go), or override the
registered FAIL and run job B with R1 recorded failed (both anchor pairs have atomic Lonely Planet sides,
so R2 stays readable for them; a Frommer's-side compound would leave a fold failure unattributable).
**Owner ruling (2026-09-19): fix P1 first.** No override; job B waits for a fresh job A.

*Job B on step2c — registered before the run (2026-09-20, amended at the judge's PROVE-FIRST).*
Job B merges into `data-ingest/sandboxes/step2c/` (job `c7c39a79…`, the round-collapse run of
the same Lonely Planet chunk; 47 beats, sha `fa549e1db77b8463`, frozen copy
`$HOME/ondoway-ingest-backup-2026-09-13/step2c-pre-jobB-2026-09-20`), not a fresh job A. That run
was a throughput proof, never scored against gate (ii) or R1, and P1 is unchanged since the
2026-09-19 ruling above, so running job B on it is an owner OVERRIDE of that ruling: shown the
ruling verbatim, that supersession is untestable here, that R1 stands failed and that step2c's
cost is a floor, the owner answered "go" (2026-09-20). R1 stays failed as registered; its Frommer's half is
hand-audited BEFORE R2 is scored, because a compound on the Frommer's side leaves a failed fold
unattributable. step2c's gate-(ii) content is present in nine unheld, narrated Guggenheim beats:
the atomic Wright claim, two critics claims (the contrast "derided … while others welcomed it" and
the Times "savaged … but others quickly celebrated"), Kandinsky, Brancusi, the 1959 completion and
the October 1959 opening, Mapplethorpe's photographs in the holdings, adult admission $25.
Job A upstream drops, found $0 in c7c39a79's own files: the passage's practicalities block (in
the unit, P0) states the Saturday pay-what-you-wish 5-8pm, the opening hours and the 4/5/6 to
86th St, and P1 emitted none of them — its admission claim cites the whole line as its span and
states only "$25". No `claim_dropped` names them and the omission check did not find them; its
one finding was the address, already claim c05, whose supplement became duplicate c215 (the
carried false-omission defect). Consequences, registered: the two conflict pairs and the subway
pair are "unproven: LP upstream drop (never emitted at P1)"; **supersession is therefore NOT
tested by this run** — at most the `same` folds, the additive new-fact path and re-narration
are. The address pair passes if Frommer's address folds into c05 or c215; into neither is a
FAIL; a Frommer's address claim folding into both (it cannot by construction — one match per
claim) would be a code defect. The carried `’`/`–` span corruption threatens Frommer's Wright
("Wright’s delirious spiral") and practicalities ("Sat 6–8pm") spans; a pair lost to it is
"unproven: upstream drop (span corruption)", not a merge failure. Estimate at HEAD b9a8750:
expected $3.4288, first-pass $2.4040, cap-bound $95.9443; registered gate 1.5x = $5.14. Reaching
the gate mid-run means the owner is asked, not an automatic kill: P3 persists only on
completion, so a kill re-pays the phase. The step2c run's own cost ($2.6072) is a floor
(`unmetered_calls=1`). No automated kill-switch; hand-watching at phase boundaries only.

*Job B result (2026-09-21 02:30 → 11:25 UTC, job `75b8f292…`, HEAD 8c72e30; corrected at the
judge's STOP): no Guggenheim fold, and nothing committed.* The job ended `status=error`,
`beats_written=0`: P7's validator refused the whole file for three beats none of which is a merge
beat — two new Frommer's stories left below their `beat_type`'s claim minimum after P3 dropped
claims (`the-servant-velazquez-freed`, anecdote with 1; `self-taught-genius-and-the-quilts`,
factoid with 1), and one narration (`a-431-million-addition`) sharing an 8-word proper-name run
with its span, which P4's lift gate exempts (`gates.py`) and P7's check (`model.py`) does not.
step2c's beats are unchanged (`fa549e1db77b8463`); P0-P6 are persisted, so a `--resume` runs P7
alone ($0). R0 PASS. Spend $3.3887 against $3.4288 expected (0.99x). Wall clock ~9 h, ~8 of it
one 37-request P1 re-ask batch queued at 0/37 for 28,535 s (API status operational).
Upstream: P1 gave 188 claims; Frommer's "early critics dismissed the museum" was never emitted
(only the Newsweek headline), nor the hours, subway or senior price. P3 refused 38 claims on the
first attempt (several with reasons that say the span supports the claim) and dropped 22, all
three blockbuster retrospectives among them; re-ask spans carried literal escapes (`\ru2019`,
`/u2013`) and re-ask texts apparatus ("In the author's rundown…"). **`story_held` fired live for
the first time**, on `guggenheim-admission-and-pay-what-you-wish`, for a transport
`invalid_request_error` on one request — isolation held the rest of the unit.
- R1 FAIL (Frommer's half, audited before R2): the dismissal never emitted; compounds c148
  (Wright designed the *spiral* museum — by the letter), c149 (a re-ask rewrite: Wright + spiral
  + among the towers) and c154 (no suicides and no accidental deaths); the blockbuster sentence
  split one claim per show as registered (the P3 re-ask then turned c156 into a list claim of the
  same class as c149's before it was dropped).
- R2 FAIL: of five Frommer's Guggenheim stories, three reached P6 and resolved to the `poi-raw`
  place; two never did (the retrospectives story lost every claim at P3; the admission story was
  `story_held`). No pair folded. The judge matched both registered pairs it saw correctly — c148 Wright `same` LP c01
  and c151 1959 `same` LP c21 — and the tripwire then held the story (c149 and c162 judged new,
  signature matched LP c03 "has a spiral ramp"). Critics: upstream P1 omission. Address and $25:
  their story was held at P3 on the transport error; by the letter a FAIL (the address folded into
  neither c05 nor c215), and stays FAIL by the owner's ruling below.
  Conflicts and subway: unproven, LP upstream drop (registered). Kandinsky and Brancusi stayed
  `new` (registered acceptable); the top-floor and walking-the-ramps traps did not fold.
- R3: no Guggenheim fold to panel. R4 FAIL: a P6 tripwire hold at the Guggenheim. R5 FAIL: not
  committed.
Elsewhere, unregistered and unverified — decided in P6 (four LP beats re-narrated), never written: 9
`merge_folded` (7 `same` → corroborated, sources 1→2; 2 `supersedes`: the Met's "more than 3
million works" over "more than two million individual objects", the MCNY Tucci 30-minute film over
"Timescapes lasts 28 minutes"), two additive joins (`a-collection-in-exile` into
`the-2020-restoration`: the closure and the Whitney-building move; `too-racy-for-children` into
`visiting-the-neue-galerie`), and `rerun` on four LP beats, each re-narrated with a fresh
`claims_hash` speaking the new or superseding fact, no `rerun_reverted`. Defects in that output:
the Neue join added c181 ("children 11 and under are barred") beside LP c178 ("does not admit
children younger than 12") — a missed `same`, the fact now twice in one beat; 8 P6 tripwire holds,
one of them (`neue-galerie-admission-and-hours`) matching a claim this same job had appended
moments earlier; claim ids collide file-wide after the merge (the job's c01… against Lonely
Planet's), e.g. the restoration beat's appended c150 beside `visiting-the-frick`'s c150.
**Owner rulings (2026-09-21, "I will follow all of your recommendations"):** this record is
committed without `make test` (docs only; the branch's bar is red by design and includes paid
shards); a blind exploratory panel (opus, fable, haiku; never Sonnet, the merge judge) scores the 9
folds, the 2 joins and the judge's `new` verdicts on the claims the 8 holds tripped on; P7 is
changed test-first to hold a beat failing exactly two validator classes, `ARC_TOO_FEW_CLAIMS`
and `NARRATION_LIFT`, into the review queue instead of refusing the whole file (every other
validator error still refuses the file), then job B is resumed ($0, P7 only, R0 repeated around it) —
which rescues the output but cannot retroactively pass R5 as registered; the address/$25 pair
stays FAIL by the letter.

*Job B, panel and rescue (2026-09-21).* The blind exploratory panel (opus, fable, haiku; Sonnet,
the merge judge, excluded; opus is also P1's author, so it is less independent on claim wording)
scored 26 items — the 9 folds, the 4 claims the two joins appended, and the 13 claims the 8 P6
holds tripped on — and its majority agreed with the merge judge on 24. Folds: 8 of 9 upheld,
both supersessions included (MCNY 30 against 28 minutes, 2-1 conflict); one false `same`, the
Frick's "seminal work by El Greco" folded into "displays works by El Greco" (2-1 new). Joins: all
four appended claims upheld as `new`, Neue c181 2-1 (its stated cause says more than LP's c178).
Holds: on 7 of the 8 held stories the panel sides with the judge's `new` — the lexical signature,
not the judge, held them; only Cooper Hewitt ("design division of the Smithsonian") was a real
missed `same` (2-1). The Guggenheim hold rests on c149 (3-0 new; c162 2-1), so the Wright and
1959 folds were blocked by a signature false positive. Then 68b87ec (the two-class P7 hold, five
tests each undo-tested, plus a guard beyond the ruling's words: a failing beat already on disk is
never held, since holding it would delete it) and a `--resume` that ran P7 alone: actual $0.0000,
0 calls in every phase, `status=committed`, 79 beats written (47 existing + 32 new), exactly three
`commit_held` items (the two short new stories and the proper-name lift), the written file valid
with spans grounded, 7 claims corroborated and 2 superseded on disk, the four re-narrated LP beats
carrying their fresh `claims_hash`. R0 PASS around the resume (live sha `556830a5342e178c`; nothing
outside the sandbox newer than the marker); step2c now `5558f031f66bec71`, pre-resume copies kept
beside it as `*.bak-pre-resume`. What this does and does not show: a second book's facts folded,
superseded and joined existing beats and the beats were re-narrated, live, for the first time —
at the Frick, the Met, the MCNY, the Jewish Museum and the Neue Galerie, not the Guggenheim. R2, R4
and R5 stay failed as registered (a resume under a changed contract cannot pass R5 retroactively).
Owed: nothing acts on accepting a `commit_held` item; the book log's per-chunk counts include the
held beats; the tripwire's precision (7 false holds of 8) is now the thing blocking merges.

*Tripwire precision (2026-09-21, owner: "go with option 1, build it and run the replay").* Root
cause: the signature scored pairs by the tour engine's overlap coefficient (|a∩b|/min), which also
fires when a SHORT existing claim sits inside a longer new one — a new claim that says more, `new`
by the contract. Measured $0 on job B's 36 judged stories (the real tokenizer; a reconstruction
that reproduces the run's 8 holds exactly): the contract's direction (existing covers ≥0.7 of the
new claim's tokens) holds 3 — the one real catch (Cooper Hewitt, panel 2-1 same) and two the panel
called false — and changes no judge-named match; the Guggenheim story is no longer held. Limits,
stated to the owner before the ruling: recall rests on ONE true positive; measured on the chunk it
was chosen on (no independent held-out data exists — slice 9 job 2 logged no per-claim answers and
the replays are the same Guggenheim job); a longer paraphrase of an existing fact now passes
unheld, and costs a duplicate claim. Built test-first in `merge.covered` (the tour engine's
`claim_dedup` untouched): the two Guggenheim pairs match nothing, the Cooper Hewitt pair still
matches, the slice-6 tripwire node still holds; undo-tested (overlap coefficient, reversed
direction, tripwire off).

*Guggenheim replay under the contract-direction tripwire (2026-09-21, owner go; HEAD 99990d9;
sandbox `data-ingest/sandboxes/jobB-replay-gugg`, prepared from the frozen pre-job copy whose sha
equals job B's `beats_sha256`, the replay tool's own check passing; one paid run, $0.0660, three P6
calls).* Registered beforehand, split as the judge required: deterministic — c149 and c162 no
longer match, so they cannot hold the story; model-dependent — c148 folds into LP c01 and c151 into
LP c21. Result: the deterministic half held (the hold names neither). The judge, resampled, again
matched c151 `same` LP c21 (1959), but this time called c148 ("designed the *spiral* … Museum")
`new` — "combines design attribution with a shape descriptor"; that fits the per-claim contract,
since no single existing claim covers c148, though c01 (Wright) and c03 (spiral) together do — and
the signature, now in the contract's direction, found LP c01 covering 6 of its 8 tokens (0.75), so
the story was held for a person. Committed 49 beats (the two unmatched Frommer's
Guggenheim stories new); R0 PASS. No Guggenheim fold completed. What now blocks it is c148 being
compound — most likely the R1 defect, on the evidence of one claim in two runs — which left the
judge's verdict to chance (`same` in job B, `new` here): that instability is the merge's too. A
case the per-claim contract cannot express going to a person is D7 working, not a false positive of
the kind fixed above. Across job B the new measure holds 3 stories where the old one held 8; the
panel called 2 of the 3 false (P05, P08) — better, not solved.

*P3 refusals and restates — blind labels (2026-09-21, owner: "yes, start steps 1 and 2"; $0).*
After two TDD-grounded plan reviews (opus, fable) the owner approved a revised order: ground truth
first, then deterministic fixes, then any paid measurement. Two labelers — Sonnet and fable, never
Haiku (P3's judge) nor Opus (P1's and the restate's author) — read only the Frommer's chunk and
51 shuffled items: the 38 claims P3 refused on the first attempt (text and span from P1.json; c189,
a supplement claim, from its refusal event's claim_text and span) and the 13 restates it then kept
(original from P1.json, rewrite from P3.json). No item carried a refusal reason. Each labeler
labeled every item; both outputs and the item key are kept under
`$HOME/ondoway-ingest-backup-2026-09-13/labels-p3-2026-09-21/`. **They
agreed on all 38: 37 SUPPORTED as written, 1 PARTLY (c149, "among taller towers" — the passage says
"among the towers").** So 37 of Haiku's 38 first-attempt refusals were false, and the restate round
they triggered is where job B lost 22 claims and gained its compounds. Of the 13 kept restates the
two agree on every compound call — 9 compound (c45, c69, c75, c89, c119, c122, c143, c149, c154) —
on c75 no longer stating its original fact (one-hour tours → docents, free, five times a day), on
c75, c124 ("ground floor") and c126 (fable: the museum's name; Sonnet: "twin") losing facts, and on c131 as apparatus
("the address given for"); c126 as apparatus is disputed (fable yes, Sonnet no); only c152 is
clean. Among the 38 originals both flag c154 compound; Sonnet alone flags c82. Consequence for the bars the owner
will be shown before any spend: a fix is judged by whether SUPPORTED claims survive with their P1
text and the PARTLY one does not pass unchanged — never by drop counts alone.

*P3 step 2, built test-first ($0, 2026-09-21).* Four deterministic changes, each with a red node
built from job B's own logged text and an undo test (mutant red, restored green):
(a) a restate whose span does not ground keeps the ORIGINAL claim's span — which P1's gates already
grounded — and is re-judged, logged `restate_span_replaced` (job B lost c46, c48, c147, c157, c172
this way; c46's span was garbled rather than escaped, which only this fallback recovers); every
other gate failure on a restate still drops it without a re-judge. (c) `APPARATUS_RE` also matches
the listing apparatus job B's restates wrote ("the address given for", "this listing", "the
guide's author", "In the author's rundown", "listed hours/admission", "visitor information for");
"the author" stays clean before "of". Over 1,113 real claims (step2b-d beats, every sandbox P1,
the calibration fixture) it newly matches exactly one — c131, the kept apparatus restate. (d) A
claim refused on the first look is judged once more AS WRITTEN (its own custom id, `-k1`) before
any restate; cleared, it keeps its P1 text with a real entailed verdict (`claim_rejudged`), and
only a claim refused twice as written is restated, with the latest reason quoted back.
`claim_refused` keeps its meaning (attempt 1 = the first look, attempt 2 = the restated text).
P3's cost plan prices the second look (fire rate 0.09, the measured rate; job B saw 0.20), and
calibration now scores a planted fabrication the second look clears as MISSED, never caught. (e)
The claim judge's schema and example shape put `reason` before `entailed`, so the verdict is
written after the reasoning — which makes the slice-4 live calibration baseline no longer
comparable. Not built: (b), a gate rejecting a restate that adds a number, date, price or name the
original lacked — owner decision owed on its fallback (dropping the claim loses a fact the labels
say was true; keeping the original needs a verdict). None of this is measured live yet; step 3's
bars come to the owner before any spend.

*P3 step 3 — measurement registered before any spend (2026-09-21).* `make ingest-rejudge` re-runs
only P3's two judge looks (first look; second look as written; never the author's restate) on job
B's claims under the current code, K samples, and prints its own estimate, refusing without `--yes`.
Items: the 38 claims both labelers labeled alike (37 SUPPORTED, c149 PARTLY), plus 20 planted
NEGATIVES — each SUPPORTED claim that states a number, with its first number changed (a year by 3,
anything else by 7) and the original span — because a second look is a second roll of the dice for
every refused claim and raises survival whether or not the judge improved; and, in a separate
one-sample pass (`--accepted`, which judges ONLY those claims, never the labeled items or the
negatives again, and is never pooled into the K=3 spread), the 151 claims job B's first look
ACCEPTED, so the reason-first
schema cannot buy precision by refusing claims that used to pass. Labels are model-made (Sonnet,
fable), not the owner's. Job B's own figures are not a baseline for the 38: they were selected
because they were refused once, so some clear on any resample (regression to the mean).
Bars, per sample, reported as the spread across K=3, never a single number:
- S1 survival: ≥ 30 of the 37 SUPPORTED survive both looks with their P1 text.
- S2 false clears: ≥ 18 of the 20 NEGATIVES are refused on both looks (a per-look catch rate near
  0.95, squared) — a SUPPORTED gain bought by clearing negatives fails here. Its ceiling, stated
  in advance: every negative is a NUMBER swapped, the easiest fabrication to catch; a pass says
  nothing about a wrong cause, a wrong kind or an altered name, so it is never a general recall
  claim.
- S3 accepted: ≤ 3 of the 151 ACCEPTED are lost after both looks (one sample); first-look refusals
  among them reported.
- c149 (PARTLY, n=1): reported, never gated.
Estimates as printed ($0 dry runs at HEAD plus the harness): K=3 labeled pass $1.3852 at most (58
items x 3 x 2 looks); accepted pass $1.2021 at most (151 items x 1 x 2 looks); both upper bounds —
the second look runs only on refusals. Owner go owed before `--yes`; R0 around each run; outputs
only under new `data-ingest/sandboxes/rejudge-*` dirs.

*P3 step 3 — result (2026-09-21, owner go "yes to both, commit and run them"; HEAD e461a05; outputs
`data-ingest/sandboxes/rejudge-k3/`, `rejudge-accepted/`; R0 PASS — live `556830a5342e178c`, step2c
`5558f031f66bec71`, nothing outside those dirs newer than the marker).* Every registered bar passed.
S1: 37, 36 and 34 of the 37 SUPPORTED survived both looks with their P1 text (bar 30). S2: 20 of the
20 planted negatives were refused on both looks in every sample (bar 18) — number swaps only, per
S2's stated ceiling; at 20/20 the bar cannot tell a good judge from an excellent one. S3: 2 of the 151 ACCEPTED were lost after both looks (c153, c30; bar 3), and 6
were refused at look 1. c149 (PARTLY) was refused on both looks in all three samples (reported).
SUPPORTED claims lost: c117, c147, c155, c82 — each in exactly one sample (4 losses = 0 + 1 + 3). At
look 1 alone the judge refused 2, 2 and 6 of the 37 — against 38 of 38 in job B, but that set was
selected for having been refused, so the change cannot be split between reason-first, the second
look and regression to the mean. Not measured: actual spend (the harness logs no batch ids; upper
bounds $1.3852 + $1.2021), recall on anything but a number swap, and any of this inside a full job
(the restate still runs for a claim refused twice: this shows fewer claims are sent to it, not
that it became safe). A paid harness that cannot report its own spend is a defect to fix before
the next paid run.
The rejudge harness now writes its client's events — every live batch id — to
`<out>/events.jsonl` and, through the same meter `make ingest-job` uses, prints its actual spend.

*Merge step 4 — c148 flip rate, registered before any spend (2026-09-21, owner: "yes to both, commit
it and go ahead"; amended at the judge's PROVE-FIRST).* The only thing between job B's Frommer's story
`a-building-brought-back-from-a-dream` and a Guggenheim fold is the merge judge's verdict on c148
("Frank Lloyd Wright designed the spiral Solomon R. Guggenheim Museum"). Measurement: ten more
replays of that place, run one after another from fresh `jobB-replay-gugg-rN` sandboxes prepared
($0) off the frozen pre-job copy, R0 around the whole set. A resume prints the WHOLE job's estimate
($3.4288), not a replay's; the replay at 99990d9 measured $0.0660 for its three Sonnet merge calls,
so ten cost about $0.66, and each run prints its actual. Counted: c148's and c151's verdicts in the
replay at 99990d9 (c148 `new`, c151 `same`) and the ten new runs — eleven answers under current
code. Job B's `same` came from code before the direction fix (8c72e30) and is history, never
counted. Decision rule, fixed now, first match wins:
- if c151 — the control, matched `same` both times so far — is ever not `same`, the merge judge
  itself is unstable, whatever c148 does: the next step is merge-judge stability (several judged
  samples per story, majority), not the compound;
- if c148 comes out both `same` and `new` among the eleven, the per-claim contract leaves it to
  chance: the next step is a merge verdict for a claim covered by several existing claims together
  (c01 + c03), with the `same` rate reported;
- if c148 is `new` in all eleven, the fold can only come from P1 splitting the claim, and that is
  the next step — a lean, not proof: at a true `same` rate of 20%, eleven `new` in a row happens by
  chance about 9% of the time.
Stop rule, per run, because nothing caps a replay's spend mechanically: after each run its printed
actual is read before the next starts. A run with no fold above $0.13 (twice the measured $0.066),
or one with a fold above $0.25 (a fold re-narrates the LP target beat: one Opus P4 draft and a P5
judge round on top), stops the set; so does a set total above $2.00 — the hard ceiling the owner's
go is asked against.

*Merge step 4 — result (2026-09-21, owner go "yes to both, commit and run them"; HEAD b0e6869;
sandboxes `data-ingest/sandboxes/jobB-replay-gugg-r1` … `-r10`, log `flip-runs.log`; R0 PASS — live
`556830a5342e178c`, step2c `5558f031f66bec71`, the source copy `fa549e1db77b8463`, nothing outside
those sandboxes newer than the marker).* Ten runs, $0.5833 in all (per run $0.0526-$0.0647, no fold,
none near a stop limit). c148 came out `new` in all ten — eleven of eleven under current code — and
the control c151 `same` in all eleven; the tripwire held the story in every run on c148 (LP c01
covers 0.75 of it). By the rule fixed beforehand, the third branch: the judge was stable on the two
registered claims, so c148's verdict is not chance, and the rule picks P1 splitting "designed the
spiral … Museum" into its two facts — a lean, not proof (at a true `same` rate of 20%, eleven `new`
in a row happens about 9% of the time). The judge's own reason each run ("no single existing claim
combines design authorship with the spiral shape (b1.c01 + b1.c03)") supports the covered-by-several
verdict equally; the data does not exclude it — the registered rule chose. Not registered, and
reported: the story-level answer varied (`same` 7, `new` 3) while every claim-level verdict held. Process defect, recorded: the run loop's stop check mis-parsed a
zero fold count, so on no-fold runs the $0.13 per-run and $2.00 set limits were checked by hand
after each run instead of by the loop (no run exceeded $0.0647).
**Correction (same day, found at the judge's review of the P1 probe, before any commit of this
record): the rule's exclusion of job B's answer rested on a false premise.** Between job B's code
(8c72e30) and the replays the merge judge's prompt and inputs are identical — `git diff 8c72e30
HEAD -- src/ingest/prompts/merge.py` is empty, and the only change to `src/ingest/merge.py` is the
signature measure, which runs after the judge answers; the replays reused job B's own P0-P5 and its
starting beats. So job B's answer (seq 3197: c148 `same` b1.c01, "Both state Wright designed the
museum building") is a valid twelfth sample of the same judge on the same input: c148 is `same` in
1 of 12 and `new` in 11 — rare, not never. With the premise corrected the evidence falls in the
rule's second branch (both verdicts seen: the per-claim contract leaves c148 to chance), and the
next step it names is the `covered` merge verdict; the P1-split lean above does not stand.

*P1-split probe — registered before any spend (2026-09-21, owner: "go ahead").* `make ingest-p1probe`
runs ONLY P1's first ask (the unchanged DECOMPOSE_PROMPT) K=10 times in one batch round on job B's
own unit — the whole Frommer's chunk P1 saw, since c148 came from that first ask (its single-request
round; the supplement came later as c189) and a smaller passage would make a split easier than it
was. Each sample is classified SPLIT (a Guggenheim claim naming Wright and "design…" without
"spiral", plus a separate Guggenheim claim stating the spiral), BUNDLED (Wright + design + spiral in
one claim) or NEITHER (the source says "Wright's … spiral", never "designed", so a faithful claim may
name Wright another way); the keyword classes are a first pass and every sample's Wright claims are
hand-audited, which decides. Printed estimate $1.3343 (output priced thinking-inclusive); metered,
batch ids logged as they arrive. Decision rule over the ten, fixed now: BUNDLED ≥ 8 → the current
prompt reliably bundles this shape and an attributive-adjective rule is worth probing against this
baseline (another K=10); SPLIT ≥ 8 → c148 was an unlucky sample of a prompt that usually splits, and
a split still has to earn a P6 `same` before this lever counts; anything else, or NEITHER ≥ 3 → the
owner chooses. In every branch the `covered` merge verdict (design note
`Docs/ingestion/2026-09-21-covered-verdict-design.md`, not built) stays an equal candidate: the
judge's own reason names c01 + c03 together.

*P6 `covered` — built test-first ($0, 2026-09-21; owner: "yes to 3" — build it, apply rather than
hold; design note `Docs/ingestion/2026-09-21-covered-verdict-design.md`).* The merge judge may
answer `covered` for a new claim no single existing claim states but two or more at the place state
TOGETHER with the same values, naming every one (`b1.c01+b1.c02`); sharing words is not sharing a
fact and a differing value is `conflict`. A malformed `covered` (one claim, a duplicate, an unknown
handle, quoted values) earns the one re-ask, then holds. Applying it absorbs the new claim: no
existing claim changes and no source is appended — the new source never states every named claim
in full — so nothing is re-narrated, and the runner no longer re-serialises the named beats (a
covered fold had come back as a changed record while no claim changed; fixed test-first). The
tripwire accepts a signature match anywhere inside the named set and still holds on one outside
it. Each is logged `merge_covered` with the new claim's text and span and every named claim's text.
The prompt defines it with an Eiffel example, never the Guggenheim. Calibration gains
`covered_union` (recall: the LP Wright and spiral-ramp claims, a second source stating both) and
`shared_union` (precision: a second source sharing 0.7 of its words with the pair's union — 0.5
and 0.4 against each alone — but stating another fact, which must stay `new`); a judge answering
`covered` there MISSES it. Every node red first or undo-tested (mutants: `covered` not a verdict,
hint skipped, two-claim check, value check, apply writes it, runner re-dumps named beats, logged as
`merge_folded`, prompt rule removed, scripted judge never covers, precision scorer accepts covered).
Nothing is measured live yet.
*Live measurement — registered before any spend.* (1) Ten Guggenheim replays of job B from fresh
`jobB-replay-cov-rN` sandboxes (the frozen pre-job copy, as in step 4), one after another, R0 around
the set: c148 comes out `covered` by LP c01 + c03 (the Wright and spiral claims) in at least 8 of 10;
c151 stays `same` LP c21 in 10 of 10; no other Guggenheim claim comes out `covered`; the story is
not held on c148; each fold completes (committed, the story's remainder beat re-narrated, every LP
beat byte-identical except the `same` source on c21). (2) One `make ingest-calibrate-live` run
(price printed first): `covered_union` caught and `shared_union` NOT folded, with every other class
at or above its live baseline. Stop rules: a replay without a fold above $0.13, with one above
$0.30 (the remainder beat's P4/P5 included), or the set above $2.50, stops the set — checked by the
loop and by hand after each run.

*P6 `covered` — live replay result (2026-09-21, owner go "yes to both, commit and run them"; HEAD
aa08f10; sandboxes `data-ingest/sandboxes/jobB-replay-cov-r1` … `-r10`, log `cov-runs.log`; R0 PASS —
live `556830a5342e178c`, step2c `5558f031f66bec71`, the source copy `fa549e1db77b8463`, nothing outside
those sandboxes newer than the marker; corrected at the judge's PROVE-FIRST).* Ten runs, $0.7645.
The judge ANSWERED c148 `covered` by LP c01 + c03 and c151 `same` LP c21 in all ten, but the answers
were APPLIED in only five: the story committed in r1, r3, r7, r9 and r10 and was held in r2, r4, r5,
r6 and r8, every hold by our own contract ("covered quotes no values"). The judge put values on
`covered` in its FIRST answer in 9 of the 10 runs — 14 of 19 attempts broke the instruction — and 4
of the 5 folds landed only on the re-ask; the fold is fragile, passing on 4 of the 9
re-asks. Scored as registered: c148 `covered` ≥ 8/10 FAIL (applied 5/10, answered 10/10); c151 `same`
10/10 FAIL (applied 5/10, answered 10/10); no other Guggenheim claim `covered` PASS (0 in every
run); story not held FAIL (5/10); each fold completes FAIL (5/10); LP beats byte-identical except
c21's source FAIL (r3). Observation for the owner, not scored: the judge called the story `same` as
the ziggurat beat (b1) in 4 of the 10 runs — r3, r5, r6 and r8 — on both attempts each time; only
r3 was applied, because the value rule held the other three. In r3 the story's three other claims
(c149 "Frank Lloyd Wright's spiral-shaped Guggenheim Museum is located among the tower buildings of
Fifth Avenue.", c152, c162) joined that beat, which was re-narrated with a fresh hash, and c162
"The Solomon R. Guggenheim Museum's atrium centers on a huge spiral ramp." now sits beside c03
"The Guggenheim Museum has a spiral ramp." In r6 the judge
also answered c162 `same` LP c03 on both attempts, with unequal values. Every `merge_covered` line
was read: c148 is absorbed against c01 (Wright's design) and c03 (the spiral ramp). That no fact of
c148 is lost is the author's reading only, not blind-labelled: c03 ("The Guggenheim Museum has a
spiral ramp.") names an interior feature and does not strictly state that the museum itself is
spiral. This is the first time a Guggenheim fold has completed in any run. The values carried a fact signal in 0 of the 14
value-carrying attempts: every `new_value` restates c148 and every `existing_value` restates c01
and c03 (twice with `(b1.c01)` annotations). In these runs the judge echoed the claims into the
values, so the values gave a rule nothing to catch. All 14 attempts concern one claim in one story;
this shows the values added no signal here, not that a rule on the values could never catch a new
claim that adds a fact. `make ingest-calibrate-live` printed
$0.8267, above the $0.70 stated to the owner — not run; owner decision owed.

*P6 `covered` made safe — built test-first ($0, 2026-09-22; handoff
`$HOME/ondoway-handoffs/2026-09-21-covered-verdict-next-session.md` Part 1 steps 2-3).* (1) The
judge's value fields are never written as prose: a `new` claim's values are ignored, and a `same`
writes `stated_value` only when both values are given and equal — unequal or one-sided values fold
with none and are NOT re-asked (the literal "must be equal, re-ask" rule held a live panel-upheld
fold whose values were paraphrases). (2) The covered value hold is gone (values go to
`merge_covered` for audit); in its place a covered claim's signature words, less the place name's
and every named claim's, must leave nothing — any word left holds the story and is named in the
reason. Blind to a recombination of existing words; its false holds (queued, never a false fold)
are word-form variants and a place name shorter than the claim's (c148 folds because P2's place is
"Solomon R. Guggenheim Museum"). (3) A held merge's review item shows the judge's per-claim answer
(§5). (4) Each chunk entry of the book log, and P6's output, carries `covered`: the absorbed claims
(story, claim id, text, the claims that cover it), empty when none and dropped when the story's
merge was reverted; `scripts/rebuild_book_log.py` carries the field, since no beat records it.
Every proving node red first or undo-tested. Not built: the empty-value prompt example (a prompt
change gets its own calibration); a held item from an answer that never passed its checks still
shows no answer. Nothing measured live.

*Offline re-measure — registered before measuring ($0, 2026-09-22; handoff step 4).*
`make ingest-merge-offline` (scripts/ingest_merge_offline.py, test-first) merges every story in the
ten `jobB-replay-cov-r1` … `-r10` sandboxes again under the code at 14c3de0 plus this tool, the judge
answering ONLY with that run's recorded `merge_answered` answers, in order, over the file job B
started from (`$HOME/ondoway-ingest-backup-2026-09-13/step2c-pre-jobB-2026-09-20/new_york/beats.json`,
sha `fa549e1db77b8463`, equal to every run's recorded `beats_sha256`). A story the code asks more of
than was recorded is `unmeasured`; stories applied one after another as P6 does. It measures the
merge DECISION only — not P4/P5 re-narration or P7. Scored on the Guggenheim story
`a-building-brought-back-from-a-dream` (c148), exactly as follows, "answered" (the recorded answers,
known: c148 `covered` c01+c03 and c151 `same` c21 in 10/10) reported apart from "applied" (this
run): (B1) `applied` in at least 9 of 10 runs — `unmeasured` counts as not applied; (B2) one judge
call in each of the 10 runs — no re-ask at all; (B3) across every applied story at the place in all
ten runs, every `stated_value` written equals both values the judge quoted for that claim — zero
exceptions. Reported, not scored: c148's and c151's outcomes per run, the story-level verdict
(expected `same` b1 in r3, r5, r6, r8), every hold and its reason (a leftover-words hold on c148
would be a registered FAIL of B1, not re-scored), and the two stories with no recorded answer (never
judged live) as unmeasured.
*Offline re-measure — result (2026-09-22, $0; report
`$HOME/ondoway-ingest-backup-2026-09-13/offline-remeasure-2026-09-22.json`, reproducible by the
registered command).* Scored as registered: (B1) applied 10/10 PASS — c148 `covered` c01+c03 and c151
`same` c21 in every run; (B2) one judge call in 10/10 PASS — the nine recorded re-asks go unused;
(B3) 0 exceptions PASS — the only `stated_value` written at the place in all ten runs is c151's
"1959", quoted "1959" on both sides. Answered 10/10 (recorded) vs applied 10/10 here, against 5/10
applied live under the old contract. Reported, not scored: story-level `same` b1 in r3, r5, r6 and
r8, now APPLIED in all four — the Frommer's story's c149, c152 and c162 join the LP ziggurat beat in
40% of runs and stay their own beat in 60% (the owner's ruling on that shape is owed); in r6 c162
folds `same` into LP c03 with no value written (the near-duplicate the reviewers doubted); the
leftover guard held nothing; `museum-or-cupcake` and `kandinsky-s-permanent-home` applied on their one
recorded answer in every run. Registration error, not re-scored: the two stories said to be
"reported as unmeasured" are absent — P3 kept 0 claims for each, so they never reached P6 live and
the tool skips a story with no claims. What this shows: the first answer the judge gave in
each of the 10 runs now applies; the 9 re-ask answers were not exercised. What it does not: the
judge's behaviour on new samples, anything after the decision (P4/P5, P7), or the precision of
`covered` — still one claim at one place.
*Blind labels on the two folds (2026-09-22, $0, two agents: opus and fable — never Sonnet, the merge
judge).* Five items, each a new claim against the existing claims at the place, no hint of which the
pipeline answered or how; three were controls. Both labellers got all three controls right (Wright +
1959 COVERED; the car-sketch text ADDS; "rises six storeys" ADDS), so the task reads as intended.
**c148 vs c01 + c03: SPLIT 1-1**, both high confidence — fable COVERED ("calling the museum 'spiral'
adds no fact beyond B"), opus ADDS ("neither states that the spiral itself was Wright's design; the
new claim links the designer to the spiral"). The Guggenheim fold this slice made reliable is
therefore NOT confirmed correct; the author's reading and the merge judge agree with one labeller
only. **c162 vs c03: ADDS, 2-0, both high confidence** — "huge" and the atrium relation are not in
"has a spiral ramp". So r6's `same` fold of c162 into c03 is a false fold, and nothing catches it:
the leftover-words guard runs on `covered` only. Extending it to `same` as it stands would hold a
known-correct fold — job 2's panel-upheld "earliest critics rejected it" / "derided by some critics"
pair leaves "earliest" and "rejected" over — so the `same` precision gap is recorded, not patched.
Owner decisions owed: the `covered` fold under a split label, the false `same` fold, and the story
shape (join the ziggurat beat in ~40% of runs, own beat in ~60%).
*Owner rulings on the merge shape — built test-first ($0, 2026-09-22).* (1) **Where a story lives is
ours, not the judge's.** Its story-level verdict is recorded as `judge_story` and nothing else: the
incoming story's unmatched claims always become its OWN beat, so the same book against the same
corpus always gives the same shape (live, the judge joined an existing beat in 4 of 10 replays of
one story and kept it separate in 6). Claim-level folds are untouched — a repeated fact still folds,
a conflict still contests — and a story that loses claims is still re-narrated, while one that keeps
them all is not (a paid call saved). The join path (`apply` appending a `new` claim to the matched
beat, fresh ids for it) became unreachable and is deleted; `MergeOutcome.story` is always `new` and
`beat_id` always None. (2) **`covered` keeps applying** under the split blind label, because every
absorption is recorded (`merge_covered`, P6, the book log) and reviewable; precision evidence comes
from the plants next. (3) **An absorption that would orphan the story's remainder is refused**: if
the claims left after folding fall below the beat type's arc minimum, the covered claims stay in the
story (`merge_covered_refused`, and no covered row is recorded). A claim the corpus already states,
visible in a beat, beats a fact lost to a queue nothing applies. (4) The false `same` fold the blind
labels caught (c162 into c03) is RECORDED, not patched: the leftover-words guard extended to `same`
would hold job 2's panel-upheld "earliest critics rejected it" fold. Measure first, in the plants.
(5) A `covered` answer naming a `superseded` or `contested` claim earns the one re-ask, then holds:
absorbing a current claim into what the corpus no longer holds, or into a disputed value, loses a
live fact. **Two consequences found while building.** FIXED at the judge's STOP: a story whose slug
already names a beat at its place was dropped with a `beat_id_collision` event and NO queue item —
its judged, narrated claims reached neither disk nor queue — and the ruling made that the default
path for every re-ingest producing the same slug, since such a story used to join the beat it
collides with; it is now a review item like everything else P6 cannot apply. RECORDED, owed a
decision: a reverted merge can queue the SAME story twice (once as a remainder below the arc
minimum, once as a dropped merge) — a duplicate item is visible and recoverable, a dropped claim is
not.
*Precision plants beyond one place — built test-first ($0, 2026-09-22; handoff step 6).* Every
`covered` measurement so far was one claim at one museum. Four plants added to
`fixtures/ingestion/defects.json`, each with its existing claims' spans verbatim in the LP chunk:
`adds_a_date` (the Frick — the second source states both claims AND "since 1938"; must stay `new`),
`recombined_union` (the Met — the second source uses only the two claims' own words, recombined into
a MISATTRIBUTION: the shop displays the hippo, when one claim puts it in Gallery 111 and the other
has the shop selling figurines. It cannot coexist with that claim, so the correct answer is a
conflict on WHERE, which by kind and date supersedes the 2022 state — expecting `new` here, as the
first draft did, would have scored a CORRECT judge as a regression on the first paid run, caught by
the judge before it cost anything. Its signature is deliberately silent, declared `hint: silent` and
pinned both ways), `negated_fact` (the Neue Galerie — the second source NEGATES a state claim it
shares nearly every word with; correct is a conflict, which by kind and date supersedes) and
`covered_union_elsewhere` (the Met — two claims together state the second source, so `covered` is
right; recall is not a one-museum trick). A plant now declares what the leftover-words guard does to
it (`planted.guard`), asserted mechanically: `catches` means words are left over, `blind` means none
are. Proven, not argued: a judge that wrongly answers `covered` on `adds_a_date` is HELD by the
guard naming "since" and "1938", while the same wrong answer on `recombined_union` is APPLIED (wrong
under either reading of that plant) — every mechanical check passes and only the judge could have
caught it. The correct answers catch all four — `new`, `supersedes`, `supersedes` and `covered`
respectively. Mutants, each red and each restored byte-identical: the blind plant declared
`catches`; the date removed from the plant; the guard switched off; `hint: silent` deleted from the
plant whose signature really is silent; and `hint: silent` claimed for a plant whose signature
fires. Baseline rows added for the four classes (mock measured, live never run). Nothing measured
live yet; the paid calibration run must first register how a HOLD is scored, since `calibrate`
counts any merge hold as MISSED — which is exactly what the guard produces on `adds_a_date`.
*How a HOLD is scored, and the paid calibration run's bars — registered before any spend ($0,
2026-09-22, corrected at the judge's PROVE-FIRST).* `calibrate` counts a merge class the pipeline
HELD as MISSED, which is right for measuring the JUDGE — it answered wrongly — but a hold and a
wrong fold are not the same event for the corpus: a hold writes nothing and queues the story for a
human, a wrong fold changes the corpus silently, and a miss where every claim came out `new` changes
nothing at all. A bare 0/1 cannot tell them apart, and for seven of the ten merge classes it never
could. The row and the printout now say which: `ClassRow.held` → `HELD` plus a "held (nothing
written, queued for review)" block, `ClassRow.wrong_fold` (a miss that folded something) → `WRONG
FOLD` plus a "wrong folds (the corpus changed)" block quoting the outcome per claim. Both hold paths
are marked, the merge hold and the unit hold. **The bars, fixed now.** (1) CORPUS, the bar that must
pass: no row is marked WRONG FOLD. Read mechanically off the report, not from the event stream. A
HOLD never fails this bar. (2) JUDGE: `regressions()` IS a gate — `scripts/ingest_calibrate.py`
exits 1 on any class below its committed live baseline, and that stays. Measured 2026-09-22, it
covers exactly six classes: fabricated_date, deleted_claim, wrong_cause, lift,
guidebook_attribution, omitted_fact. Ten live rows are null and four classes (shared_element,
same_fact, compound_claim, stated_relation) are absent, so NO merge class is gated by it — this run
records the first live numbers for every merge class, including all four added today, and they
cannot regress because nothing exists to regress against. Say that plainly rather than reading an
ungated class as a pass. (3) Every HELD row is read and its reason quoted in the result, and a 0/1 with NEITHER marker is
named in the result too — it means the judge missed and nothing was written (a bar (1) pass), which
is also the shape of a judge that answered nothing, so it is never left unremarked. **The
command is `make ingest-calibrate-live ARGS="--yes"`. NEVER `--update-baseline` on this run**: it
short-circuits the comparison, exits 0 and overwrites the live baseline with whatever the run
produced — which would also pin `adds_a_date` at 0/1 and make a future wrong fold there
unregressable. Baselining is a separate step, after the bars pass, with its own judge consult.
Registered consequence, so it is not re-litigated afterwards: `adds_a_date` is EXPECTED to report
0/1 HELD whenever the judge answers `covered` there, because the leftover-words guard is doing its
job; scoring that as a regression would be the same error as expecting `new` on `recombined_union` —
reading a working guard as a defect.
*One review item per story, with a per-claim disposition — built test-first ($0, 2026-09-22; owner
ruling on the double-queue).* A reverted merge queued the SAME story twice — once as a remainder
below the arc minimum, once as a dropped merge — with OVERLAPPING claim sets, and the dropped-merge
item listed every judged claim including folds that landed in OTHER beats and are still in the
corpus. Nothing applies an accepted item yet, so nothing was duplicated; the moment an applier
exists, accepting those writes facts the corpus already states — duplicate claims at one place,
which is what the claim-level merge exists to prevent and what the narration engine then has to
dedupe at runtime. Now: at most ONE `merge_held` item per story per UNIT (slugs are unique within a
unit only — keyed by slug alone, two chunks holding the same slug would collapse into one item and
the second story's claims would vanish, which the judge caught), however many times P6 could not
place it, carrying every reason joined, the story's WHOLE judged claim set, and
`shown.claim_disposition` — per claim one of `written` (its fold landed and survived: an applier
must never write it again), `absorbed` (`covered`: stated in full by claims already there, written
nowhere by design), `dropped` (its fold was given back when a beat's rerun was held) or `unmatched`
(never written) with the beat and claim it names. `held_back.claims` now ranks by what the corpus is
MISSING (unmatched + dropped), not by how many claims the story has. The six queue points in P6
funnel into one accumulator flushed after the reverts settle, so a revert re-marks its folds
`dropped` even when an earlier hold already described them; the two old helpers are deleted.
Mutants, each red and restored byte-identical: only the last reason kept; `absorbed` reported as
`unmatched`; a revert that never marks dropped; the disposition built over the subset a path carried
rather than the whole story. The disposition carries `applied_claim_id` beside `existing_claim_id`,
because a supersedes/superseded fold demotes the MATCHED claim and writes the new fact under a fresh
id — an applier keying on the matched id would read the wrong claim. An `absorbed` row carries
`covered_by`, EVERY claim that states it together, because a covered answer always names at least
two and the row's single id is only the first: a reviewer checking that one claim would not find the
fact and might "correct" the absorption by writing a duplicate (the judge caught that).
`held_back.claims` now means "missing from the corpus" for this kind alone, so `jobs.held_back_rank`
and the workbench queue line say so rather than contradicting it. What is proven: one item per
story-slug per unit, a disposition truthful for every fold kind the runner produces. What is NOT
built: the applier — accepted items still do nothing, which stays owed.
*The calibration run prints what it spent ($0, 2026-09-23).* `make ingest-calibrate-live` printed
an estimate and no actual, so a paid run's cost could not be read. The live client is now wrapped in
the job runner's meter (`calibrate.run(wrap_live=)`, `_CountingClient`): the run prints `spend:
actual=$… tokens_in= tokens_out= calls:` per phase, priced as the job runner prices, marked a LOWER
BOUND when a billed call reported no usage, and prints `actual=UNKNOWN` rather than raise when no
meter is present, because by then the money is spent and the baseline gate has still to run. Its
printed estimate is now $1.1593 (P3 $0.2956, the omissions check $0.0324, P6 $0.8314 for ten merge
classes, each a first answer and one re-ask), not the $0.8267 quoted before the four precision plants.
*Live re-measure of the Guggenheim fold — registered before any spend (2026-09-23).* The offline
re-measure replayed recorded answers. This one samples the judge again and runs P4/P5/P7. Ten
replays of job B (`75b8f292b34e40ff90e5049392976335`) at the Guggenheim, run one after another from
fresh sandboxes `data-ingest/sandboxes/jobB-replay-cov2-r1` … `-r10`, each prepared ($0) by `make
ingest-merge-replay CITY=new_york JOB=75b8f292… PLACE="Solomon R. Guggenheim Museum" ARGS="--out
<sandbox>"` off the frozen pre-job copy (sha `fa549e1db77b8463`, refused otherwise), under the
commit that records this paragraph. R0 around the set: live `data-ingest/new_york/beats.json` sha
`556830a5342e178c` before and after, and nothing outside the new sandboxes and their logs newer than
a marker. Run and scored by one pinned loop outside the repo,
`$HOME/ondoway-ingest-backup-2026-09-13/tools/cov2_loop.py`, sha256
`8330ed5f47a5adafe340d7bc82a3c2ec182d6348e3d58fe0627be294f2b266bf` (`run` spends, `score` is $0). Its
scorer reproduced the recorded 2026-09-21 set exactly (applied r1, r3, r7, r9, r10; costs to the
cent; r3's join into the ziggurat beat flagged). A result scored by any other file, or by this one
after its sha changes, is not a result of this registration. Scored on the story `a-building-brought-back-from-a-dream`,
with "answered" (the judge's final answer) reported apart from "applied" (what reached disk):
(L1) c148 APPLIED, meaning a `merge_covered` event for c148 naming exactly LP c01 + c03 of the
ziggurat beat, the story not held, and the job `committed`, in at least 9 of 10. Any hold counts as
not applied, and so does a c148 answered other than `covered`. The two are reported separately.
(L2) one judge call for the story in 10 of 10, meaning exactly one `merge_answered` event carrying the
story's slug and no `merge_reasked`. Any re-ask's problems are quoted.
(L3) across every beat at the place in all ten runs, every non-null `stated_value` on a source whose
`source_id` is `frommers-nyc-2024` equals both the `new_value` and `existing_value` of the judge's final answer for that claim:
zero exceptions.
(L4) the corpus changes only as decided: in every run, every beat of the start file is present and
byte-identical, except that a claim named by a `same` fold may change in `resolution`, `sources` and
`verdict` and nowhere else: zero exceptions. Reported, not scored: the story-level verdict per run
(recorded as `judge_story` only); c151's and c162's answers per run, with every `same` fold of c162
into LP c03 named, because that is the false fold the blind labels caught; every hold event quoted in full; each run's actual. Stop rules, run by the loop and not by hand. BEFORE each run: no run starts if
the set's total so far plus $0.15 exceeds $1.00. AFTER each run, any one of these stops the set: a
status other than `committed`, a non-zero exit, an actual that is unreadable or a lower bound, a run
above $0.15 (the dearest of the last ten was $0.0901, a fold's remainder narration included), the
set above $1.00 (the last ten cost $0.7645), any L4 exception, or a change to the live sha. What
bounds the spend, honestly: the replays end at or below $1.00 unless one run by itself spends more
than $0.15, which nothing caps while it runs. The calibration run has no cap at all. Its $1.1593 is
the printed estimate, not a ceiling. Expected total about $0.8 + $1.16, and the go asked of the
owner is against $1.00 + $1.1593 = $2.16 with the in-flight overrun stated. A set that stops before
r10 fails every bar it had not already met (L1 needs 9 applied, L2, L3 and L4 need 10 clean runs). It is
never topped up, extended or re-run under this registration, and its runs are reported as they
stand. Then the calibration run, bars as registered above, R0 around it too. What this cannot settle: whether the
Guggenheim fold is CORRECT. The blind labels split 1-1 on it; ten runs measure only how reliably the
judge gives it.
*Amendment, before any spend (2026-09-23).* The first start of the loop at e77d805 stopped at $0
in the prep for r1: `make ingest-merge-replay` defaults to `--source-root data-ingest`, where job B
does not live, so it found no `job.json`. No run started and nothing was written except that prep's
log, kept as `jobB-replay-cov2-r1.prep-aborted.log`. R0 held (live `556830a5342e178c`). Job B and the
frozen pre-job copy live in `data-ingest/sandboxes/jobB-replay-src` (beats sha `fa549e1db77b8463`).
The prep command above therefore gains `--source-root data-ingest/sandboxes/jobB-replay-src`, and
the loop gains the same argument plus a start check that that root's beats sha is
`fa549e1db77b8463`. A $0 throwaway prep with it built a sandbox at exactly that sha. The pinned loop's
sha256 is now `55882988bbc014939a822205f4bfeee7feab67d2643632f5cf70d2dffaa600b2`, which REPLACES the
sha above. Its scorer is unchanged and re-reproduced the 2026-09-21 set. Bars, stop rules and scoring
are unchanged. The replays run under the commit recording this amendment.
*Live re-measure and calibration — result (2026-09-23, owner go "I waive make test for the amendment
commit and confirm go"; HEAD 17681c8; loop sha256 `55882988…`; sandboxes
`data-ingest/sandboxes/jobB-replay-cov2-r1` … `-r10`, log `cov2-runs.log`, full score
`$HOME/ondoway-ingest-backup-2026-09-13/cov2-score-2026-09-23.json`; R0 PASS around both — live
`556830a5342e178c` before and after, nothing outside the new sandboxes and their logs newer than the
markers, the calibration baseline file unchanged at `ffc6224e96c0bf9a`; backup rsynced after each).*
Replays: ten runs, all `committed`, $0.6344 in all ($0.0570-$0.0706 each), no stop rule fired.
Scored as registered: (L1) c148 answered `covered` LP c01 + c03 in 10/10 and APPLIED in 10/10 — PASS
(was 5/10 applied live on 2026-09-21); (L2) one `merge_answered`, no re-ask, in 10/10 — PASS; (L3) 0
exceptions over the ten `stated_value`s written at the place (nine "1959" on LP c21, one "spiral ramp"
on LP c03) — PASS; (L4) 0 corpus exceptions in 10/10 — PASS as registered; the bar admits r8's false `same` fold by construction (below). Reported, not scored: c151 `same` LP c21
in 10/10 (r7 wrote no value: the judge's two values differed); the story-level answer `same` in r2, r3,
r7, r8 and `new` in the rest, recorded only; no hold of any kind in any run; **c162 folded `same` into
LP c03 in 1 of 10 (r8), applied, writing "spiral ramp" as its value** — the false fold both blind
labellers called ADDS ("huge" and the atrium are not in "has a spiral ramp"), now seen live. L4 does
not catch it by construction (a `same` fold may add a source) and neither does anything else.
Calibration (`make ingest-calibrate-live ARGS="--yes"`, never `--update-baseline`; printed estimate
$1.1593, metered actual $0.1735, calls P1=2 P3=17 P6=10, exit 0, "baseline[live]: no class below it").
Scored as registered: (1) no row marked WRONG FOLD — PASS; (2) `regressions()` passed; it gates the six
classes named above and no merge class, so the merge rows below are first live numbers, not passes of
a gate; (3) no row marked HELD. Every merge class caught 1/1 on its first answer with no re-ask:
contested_value and superseded_belief `conflict`, shared_element and shared_union `new`, same_fact
`same`, covered_union and covered_union_elsewhere `covered` c01+c02, **adds_a_date `new`** (so the
guard had nothing to hold; the registered 0/1 HELD did not arise), recombined_union and negated_fact
`conflict` → `supersedes`. The one 0/1 carries neither marker and is named as registered:
`stated_relation` (omissions detector, not a merge class, absent from the live baseline so ungated) —
the coverage check split c02, "cramped single elevator makes descending from the top difficult, so
exhibitions are installed from bottom to top", into three facts, a false compound flag on a stated
relation; in the calibration nothing is written, so it is a detector miss, not a corpus change. Both
`omitted_fact` and `stated_relation` also discarded an ungrounded finding ("Other key additions have
included paintings by Monet, Van Gogh and Degas" is not in the unit). What this shows: under the
current code the Guggenheim `covered` fold is given and applied in 10 of 10 runs on the first answer (the
registered bar was 9 of 10; ten runs cannot show the failure rate is below about 26%), and the merge judge answered all ten merge plants correctly once each. What it does
NOT show: that the fold is CORRECT (blind labels still split 1-1), precision beyond one sample per
plant, or any guard against a false `same` — r8 shows it reaching the corpus in 1 of 10 runs (it was also
answered, and held, in 1 of 10 on 2026-09-21); the rate is not measured.
Spend this session: $0.6344 + $0.1735 = $0.8079.
*A false `same` loses what the book thought mattered — owner ruling and the evidence ($0,
2026-09-24).* A `same` fold keeps the existing claim's words and drops the new claim's, and the
narrator reads claim texts only, never spans (`src/ingest/prompts/narrate.py`). Job B's fold of
Frommer's "The Frick collection includes a seminal work by El Greco." into LP's "The Frick Collection
displays works by El Greco." left "seminal" in a source span alone, while Bellini, Holbein, Constable
and Turner, from the same Frommer's sentence, kept "seminal work" as claims of their own: one sentence
of the book now tells the narrator four painters matter and a fifth does not. r8's fold of "a huge
spiral ramp" at the atrium into "has a spiral ramp" is the same loss. **Owner ruling:** sourced
evaluative language — importance, size, quality, reputation — is narrative signal; a merge may never
drop it silently; a `same` whose new claim carries such a judgment the existing claim lacks is not
`same`. No lexical rule can enforce that: over the eleven labeled `same` pairs below, `merge._signature`
leaves words over on all nine non-hold pairs as well as on both false ones ("seminal", "huge" sit beside
"late", "mansion", "earliest", "located"), and the narrower "new contains all of existing plus more"
catches r8, misses El Greco and holds both true Frick Madison folds.
*`same` second-look probe — registered before any spend (2026-09-24).* Whether a model other than
the merge judge (Sonnet) and the author (Opus) can tell a true rewording from a `same` that drops
something, the owner's judgment rule included. Haiku, on the claim judge's role and P3 batch
transport (a probe only; a built second look gets its own phase), asked `SAME_CHECK_PROMPT`
(`src/ingest/same_probe.py` at the commit recording this paragraph; prompt sha256 prefix `933a3b4a22087199`) for each pair in
`fixtures/ingestion/same_second_look.json` (sha256 prefix `0ad16cc80b391a29`) three times, one batch round:
`make ingest-same-probe ARGS="--samples 3 --out data-ingest/sandboxes/same-probe-1 --yes"`, printed
estimate $0.0431, metered actual printed. Eleven pairs, texts verbatim from the job logs: two `hold`
(the El Greco and c162 folds, both labelled false by blind review), six `fold` (Frick admission and
children, the Jewish Museum and Met addresses, c151's 1959, the calibration paraphrase plant) and
three `either` (Frick "late 2020", the Neue cafe, and job 2's critics fold, whose folded claim is the
compound "…earliest critics rejected it, and Newsweek ran a review under the headline 'Museum or
Cupcake?'" — the spec records that pair above from the shortened text, and the headline is a fact the
existing claim lacks; `hold` covers only blind-labelled false folds, so that pair stays unscored even
though the prompt's own first bullet makes `adds` its right answer). The prompt's examples are words
no pair uses — a guard test fails if any word or number a pair adds appears in it — so the probe
measures the rule, not word-matching (the first draft named "seminal" and "huge", caught by the
judge before anything was frozen). Two of the six `fold` pairs, the Frick Madison ones, rest on the
prompt's "a different name for the same place is not an addition": the fact being folded is the
price and the age rule. Scored exactly as follows, by `same_probe.score`: (S1) both `hold` pairs
answered `adds` in 3 of 3 samples each, 6 of 6 — must pass; (S2) the six `fold` pairs answered `same`
in at least 16 of their 18 samples. A sample that fails to parse or fails in the batch counts against
its pair's bar and is reported, never dropped. Reported, not scored: each `either` pair's three
verdicts, and every `adds` list quoted. The run writes `same-probe-report.json` with its inputs
(fixture and prompt sha256, samples, model, HEAD); a result whose inputs differ from those registered
here — three samples, the shas above, `claude-haiku-4-5` — is not a result of this registration. No
re-run and no re-score under it; a batch that raises writes no report, prints its metered spend, and
is recorded as a failed run. What a pass decides: that a Haiku second look is worth
building and measuring on a real job, test-first, under its own registration — not that it is
correct; eleven pairs is a small set and its two positives are the only false folds ever labelled.
What a fail decides: the second look as worded does not separate them; a prompt change or a
stronger model is a new registration, never a re-run of this one.
*`same` second-look probe — result (2026-09-24, owner go "I waive make test for the probe commit and
confirm go"; HEAD d6b556b; report `data-ingest/sandboxes/same-probe-1/same-probe-report.json`, its
inputs matching the registration — fixture `0ad16cc80b391a29`, prompt `933a3b4a22087199`, 3 samples,
`claude-haiku-4-5`; batch `msgbatch_01SKuGjvvi48Ujt1PhVnKuCe`, estimate $0.0431, metered actual
$0.0101; R0 PASS, live `556830a5342e178c`, nothing outside the new sandbox newer than the marker;
backup rsynced).* Scored as registered: (S1) 6 of 6 PASS — El Greco `adds` ["seminal"] and c162
`adds` ["huge", "atrium centers on"] in every sample; (S2) 12 of 18 FAIL (bar 16) — the Jewish Museum
and Met addresses, c151's 1959 and the paraphrase plant `same` in 3 of 3 each, both Frick Madison pairs
`adds` ["Frick Madison"] in 3 of 3 each. **The probe FAILS as registered**, and by its own terms the
second look as worded is not built; no re-score. Reported, not scored: Frick "late 2020" `adds`
["mansion"] twice and `same` once; the Neue cafe `same` 3 of 3; the critics compound `adds` the Newsweek
headline 3 of 3. Every miss sits on the two pairs the registration flagged as resting on "a different
name for the same place is not an addition". The source bears on that: Frommer's entry is headed
"Frick Madison", says the renovation "will shutter the mansion for at least 3 years, with the
collection moved temporarily a few blocks north", and lists "945 Madison Ave." with "Admission $22
adults" under it. Two readings, not decided here: (i) the `fold` label is wrong, because the source
places Frick Madison as a temporary building, so its price is that building's; (ii) Haiku did not
follow the prompt's name exemption — it answered the bare name, never a building or a move. The
labels are the author's own, so the author does not decide (i): blind labels do, before any new
registration, and nothing here is re-scored. Job B's panel-upheld folds of these two pairs are
disputed on the same reading and need a blind label; if false, what they lose is a venue or dating
fact, not the evaluative language of the 2026-09-24 ruling — a different defect class.

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
