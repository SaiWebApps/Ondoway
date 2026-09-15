# Ingest transport and resume — design

Status: approved in chat 2026-09-14 (owner), not yet built.
Scope: slice 9 of `Docs/ingestion/rebuild-spec.md`, built so slice 10 does not need a redesign.

## Why

Slice 9 job 2 (Frommer's `chunk-05-ch05-uptown`) failed twice without writing a beat:

1. A P3 claim-judge batch was still `in_progress` after the live client's one-hour poll
   ceiling (job `ed0f2a449a214106aa0417b576e26f5f`, spend ≥ $0.60). Fixed by
   `llm.BATCH_MAX_POLL_S` (971f48e), a transient-error guard (913d651) and error backoff
   (21a44a9).
2. The first status check on a batch, 169 ms after submission, returned `404 not_found`
   (job `e4572c530d27464981f6e52b7bb43dfc`, spend ≥ $1.98, P3 nearly finished).

Both failures cost the whole job because phase outputs live only in the CLI process.
The underlying shape is the real problem: one batch round per story. Run 4 of job 1
made 71 sequential batch rounds (P1 5, P3 36, P5 30) for one 3,400-word chunk. On a
normal day a round takes about two minutes; on 2026-09-14 the slowest rounds took 9 to 77
minutes (attempt 2's 72 rounds had a median of 2.9 minutes).
Slice 10 re-extracts 386 chunks (98 New York, 288 Paris, about two million words); at
the current shape that is on the order of 1,000 hours of sequential rounds.

## Decisions (owner, 2026-09-14)

- Optimise for job 2 now, in a shape slice 10 can keep.
- The per-story judge loop runs synchronously: the Haiku P3 and P5 judge rounds AND the
  Opus claim restates. The Batch API's 50% discount on them is given up. Measured at
  `llm.PRICES_USD_PER_MTOK`: about +$0.45 on job 1 run 4 (3 restates) and at least +$1.10 on
  job 2 attempt 2 (about 29 restates; it died in P3, so no P5) per proof-size chunk, so roughly
  +$270 to +$660 or more across
  slice 10. (The owner approved this direction on an earlier +$200 figure that left out the
  Opus restates; the corrected range is flagged for re-confirmation.)
- Batch stays for P1 decompose (Opus): the first ask and its re-ask, plus the omission
  re-decompose inside P3 when the judge finds omitted facts — two or three rounds per job.
- A small resume from disk is in scope.
- The transport is chosen at the call site (approach A), not by a client-side phase policy
  (claim restates are tagged P1/author, the same as decompose) and not by rewriting the
  judge modules around `complete()`.
- Out of scope: the span corruption where the model writes a newline for `’` or `–`
  (5 of 6 span drops in job 2 attempt 2) — a separate $0 investigation.

## 1. Round transport chosen by the caller

`ModelClient.complete_batch(role, prompts, schema, *, phase, max_tokens,
transport="batch")`, where `transport` is `"batch"` or `"sync"`. Both return
`{custom_id: Completion | BatchFailure}`.

| Caller | Rounds | Transport |
|---|---|---|
| `decompose` | P1 first ask and re-ask, omission re-decompose | `"batch"` |
| `judge_claims._batch` | P3 judge round 1, restates (P1/author), judge round 2, omissions | `"sync"` |
| `judge_narration` | P5 sentence verdicts | `"sync"` |

Live client, `transport="sync"`: the same role, phase, estimate and phase-role checks as
today, then one sequential `messages.create` per prompt with the existing sync request
shape (`llm._message_kwargs`) through the sync SDK object (`batch_review_client`: 300 s
timeout, 3 retries), and the same post-response refusals (empty, truncated at
max_tokens, judge-is-author). A request that raises becomes a `BatchFailure` for that
custom_id — never an exception — so the existing rule "a failed unit drops that story,
not the job" applies unchanged. Usage records `batch=False`.

`llm.BATCH_PHASES` means phases that MAY batch (P1, P3, P5). `transport="batch"` on any
other phase still raises `WrongTransport`; `complete()` is unchanged.

`MockClient.complete_batch` accepts `transport`, draws from the same `batch_answers`
scripts, and records `(role, phase, transport)`. No existing mock script changes.

The estimate prices each plan row by the transport its round uses, not by its phase tag: P3
judge rounds, restates (tagged P1/author) and omissions, and P5, at sync rates (about twice
their batch cost); P1 decompose rows (including the omission re-decompose) at batch rates. `_CountingClient` in `scripts/ingest_job.py`
forwards `transport` and meters sync rounds per call.

## 2. Resume from disk

The CLI's `_PrintingStore` persists each job under `data-ingest/{city}/jobs/{job_id}/`,
every file written to a temporary name and renamed into place:

- `job.json` at creation: id, city, source (chunk dir and chunks), `as_of`,
  `rights_basis`, and the sha256 of each source chunk's text.
- `P0.json` … `P7.json` from `set_phase`, written BEFORE the `phase` event is appended,
  so a phase the log calls done is always on disk.
- The job log `{job_id}.jsonl` and the `held/{job_id}.jsonl` sidecar stay where they are.

`make ingest-job CITY=… ARGS="--resume JOB_ID [--yes]"` rebuilds the job from
`job.json` under the same id, loads its phase files into the store, appends a `resumed`
event naming the phases loaded, prints the estimate (stopping without `--yes`), and calls
the existing `run.run_job`, whose skip-completed-phases logic resumes at the first
missing phase. New events continue in the same job log.

A resume is refused — non-zero exit, a message naming the reason, nothing spent — when:
the id is unknown; the job already committed, known by EITHER `P7.json` existing OR the
city's `book-log.json` `books_processed` naming this `job_id` (P7 commits the beats and the
book-log entry before `set_phase("P7")`, so a crash between the two leaves no `P7.json`
and a resume would otherwise commit twice); the phase files have a gap (a later phase
present without an earlier one); a source chunk's sha256 differs from `job.json`; or
`--chunk`, `--chunk-dir` or `--as-of` conflicts with the job.

Resume is per phase. A crash inside P3 redoes P3 — quick and cheap, EXCEPT when P3 had
already run an omission re-decompose, which is a batched Opus round (it took 77 minutes on a
slow day) and is resubmitted. A crash while waiting on a P1 batch resubmits P1; re-attaching to an in-flight batch is out of scope,
and `make ingest-batch BATCH=` still recovers one by hand. The API front door keeps its
in-memory store.

## 3. Error handling on the batch path, and tracing

`batch_transport.poll_batch` gains `not_found_grace_s: float = 0`. A `NotFoundError`
raised less than `not_found_grace_s` seconds after polling started counts as a tolerated
error: the same consecutive-error budget, `on_poll_error` report and backoff as a
transient error. After the window a 404 raises. The default leaves the tour
certification caller unchanged; the ingest live client passes
`llm.BATCH_NOT_FOUND_GRACE_S = 120`.

A failed unit's `error_message` — batch or sync — gains the prompt's length and a
12-character sha256 prefix, e.g. `(prompt 38211 chars, sha256 3f9a0c1b2d4e)`, so the
`unit_held` line for a failure such as `invalid_request_error: Invalid request data` can
be matched to the prompt that produced it. The prompt text is not logged.

Unchanged: one re-ask per phase per item, then drop or hold; a failed unit drops its
story, not the job; programming errors (`EstimateNotPrinted`, `JudgeIsAuthor`, …)
propagate. A sync 429, 5xx or connection error that outlasts the SDK's own retries
becomes a `BatchFailure` for that story.

## 4. Testing and proof

Each proving node is written red first, undo-tested (revert that piece, the node goes
red), and run as `make test-file FILE=<node id>`.

1. `tests/test_ingest_llm.py`: `complete_batch(transport="sync")` makes one
   `messages.create` per prompt in order and never touches `batches.*`; Completions carry
   `usage.batch=False`; a raising request becomes a `BatchFailure` whose message carries
   the prompt length and hash; `transport="batch"` on P2, P4 or P6 raises
   `WrongTransport`.
2. `tests/test_ingest_judge.py`, `tests/test_ingest_narrate.py`,
   `tests/test_ingest_decompose.py`: a recording client shows P3 rounds (judge, restate,
   omissions) and P5 call `transport="sync"` and decompose calls `"batch"`.
3. `tests/test_ingest_run.py`: plan rows for sync rounds (P3 judge, restate, omissions, P5)
   are priced at sync rates; P1 decompose rows at batch rates.
4. `tests/test_ingest_job_cli.py`, through `ingest_job.main`: a mock job killed in P3,
   resumed with `--resume JOB_ID --yes`, makes no P1 or P2 call, commits, and writes the
   same beats as an uninterrupted run; one node per refusal (unknown id; committed via `P7.json`;
   committed via a `book-log.json` entry naming the job with no `P7.json` — the crash between
   commit and `set_phase("P7")`; gap; changed chunk hash; conflicting flag); a node that a phase file exists when its
   `phase` event is logged.
5. `tests/test_batch_transport.py` (fake clock): a 404 inside the grace window is
   tolerated and the batch ends; after the window it raises; the default raises at once.
6. Regression bar: every touched and dependent file one by one — including
   `tests/test_batch_transport.py` and `tests/test_tour_batch_candidate_runner.py` for
   the shared transport — then `make ingest-calibrate` and `make lint`, each judged by
   exit code. A judge consult before each commit. `make test` is not run: the branch is
   known red until slice 10.
7. Live proof, owner go required: job 2 attempt 3. Pass = `summary: status=committed`, and
   every `batch_submitted` in the job log has role `author` and belongs to a decompose round —
   logged before the `phase P1` event, or directly after an `omissions_found` event (a
   restate is also tagged P1/author, so "batch rounds only in P1" would not catch a restate
   that is still batched). Report wall time, spend against a fresh
   estimate, and the P6 hold rate on the Guggenheim and Frick merges. Stop and report if
   spend exceeds 1.5× the expected figure or any story is dropped for a transport failure.

## Build order

Three checkpoints, each committed on its own:

1. Sync transport, call sites and estimate (§1). On job 1 run 4's log that removes 69 of
   its 71 batch rounds (the 2 left are decompose; its other 3 P1-tagged rounds were
   restates).
2. Resume (§2).
3. 404 grace and failure tracing (§3).
