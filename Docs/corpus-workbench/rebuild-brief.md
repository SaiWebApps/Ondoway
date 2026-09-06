# Lane B re-author — state, findings, and what to build next

Read this before touching the re-author pipeline. It records what exists, what an
adversarial panel proved wrong about it, and the architecture that replaces it.
The decisions that still hold are in [decisions.md](decisions.md); this document
supersedes the parts of it named below.

## State of the corpus, in one line

**Nothing has shipped.** All 524 affected beats in `data/paris/beats.json` and
`data/new_york/beats.json` still carry their original guidebook-copied bodies.
The rewrites live in `data/{city}/reauthored.json`, which is gitignored, and no
step exists that carries them into the corpus. The damage from what follows is
wasted spend, not a corrupted corpus.

## What exists

| Module | Does |
|---|---|
| `scripts/corpus_report.py` | Corpus quality + coverage, read from files. Sound; unaffected by the findings below. |
| `scripts/reauthor_preview.py` | Selects the copied backlog, holds the rewrite prompt and model constants. |
| `scripts/reauthor_run.py` | Batch rewrite into `data/{city}/reauthored.json`. Resumable. |
| `scripts/reauthor_verify.py` | The two-judge verification. **Its design is refuted — see below.** |
| `scripts/reauthor_review.py` | Human decision records, hash-bound. Sound. |
| `frontend/corpus.html`, `frontend/rewrites.html` | The dashboard and the review queue, served by `src/server.py`. |

London is excluded from all of it and stays excluded: its beats are junk from the
automated drafter, not a backlog worth repairing.

## What an adversarial panel proved, and what was verified by hand

Three adversaries on three models reviewed the pipeline. Every number below was
re-measured directly against the files, not taken from an agent's report.

**F1 — The rewriter was one of its own two judges.** `REAUTHOR_MODEL` is
`claude-opus-5`; `VERIFY_MODELS` was `("claude-opus-5", "claude-sonnet-5")`, and
the reconcile rule required every judge to find nothing. So all 384 approvals
meant "Sonnet found nothing, and the author declined to flag its own work". The
module's own docstring states the rule this breaks. **A judge may never include
the model that produced the text.**

**F2 — The rewrites are structurally derivative, and the pipeline could not see
it.** Of the 306 rewrites whose source has three or more sentences, **278 (91%)
follow the source's sentence order exactly**. The rewrite prompt asked for the
same content with none of the phrasing, which preserves selection and
arrangement — the part copyright actually protects — and the verification prompt
explicitly told the judge not to report reordering. A 0% word-overlap rewrite
that reproduces seven vignettes in the source's order is still derivative.

**F3 — The plagiarism metric contradicted its own stated criterion.**
`corpus_report.py` says an 8-word run is evidence a sentence was lifted, yet
**81 auto-approved rewrites contain an 8+ word verbatim run**, because
`verbatim_ratio` divides matched shingles by body length — one lifted clause in a
long body scores near zero. Many of those 81 are legitimate (attributed
quotations, dialogue, factual name lists), which is the deeper problem: the
metric cannot separate shared expression from shared fact, so it answers neither
question. Report `max_verbatim_run` for the gate; keep the ratio for reporting.

**F4 — A rewrite can silently change a shipped fact, unseen.** Where
`source_passage` and `body_before` disagree, the rewrite adopts the source and
the judge cannot object, because the prompt defines the source as ground truth.
Two confirmed: a pier number and a floor area, both auto-approved with no
reported drop. 29 records carry that conflict shape.

**F5 — There is no audit trail on a pass.** All 384 passing records carry an
empty `reason`, and the `models` field is a constant written unconditionally
rather than per-call evidence. The 140 escalations do carry differentiated judge
text. A decision that ships unread must store what was actually answered.

**F6 — The calibration optimised the wrong number.** It tuned escalation rate
from 47% to 20% on 15 beats with no held-out set. Escalation rate moves
monotonically with leniency, so it measures fit and nothing else, and the design
cannot measure its own miss rate at all — no beat with a known defect was ever
put through it.

**What survived.** An adversary read 38 auto-approved records in full and diffed
every number across all 384: no fabrications, no material losses. The
added-fact judge itself is good — the 140 escalations are subtle and largely
correct. The gate is aimed wrong and graded by a conflicted panel; it is not
theatre. Note the bound: 0 defects in 38 puts the true rate at roughly 8% or
below, so around 30 bad approvals could still be present.

**Not a rate.** "27% drift" was wrong as stated. Per-book pass rates span 63-89%
and escalation tracks source length, so the figure is a property of the corpus
mix and the reconcile rule, not of the rewriter.

## Supersedes

- **I4** in [decisions.md](decisions.md) — Lane B as a thin beat-level pass over
  the source passage. The unit is right; feeding the writer the source prose is
  not (F2).
- **The review queue as built.** A queue of everything contradicts D1's triage
  framing. Filtering it to escalations was correct; the escalations themselves
  came from a refuted gate.

## What to build

**Stage 0 — free, deterministic, first.** Re-triage the existing files with two
gates that cost nothing: `max_verbatim_run` at 8 words outside an attributed
quotation, and a conflict gate refusing auto-approval wherever `source_passage`
and `body_before` disagree on a number, date or name. This says how much of the
existing output is salvageable before any further spend.

**Stage 1 — clean-room regeneration.** Decompose the source into an unordered set
of atomic claims, shuffle it, and give the writer only the claim set — never the
source prose, never the old body. A writer cannot copy an order it was never
shown, which fixes F2 at the root instead of detecting it afterwards. Record the
writer's exact input, so the evidence of non-derivation is an audit trail rather
than a score.

**Stage 2 — verification with real asymmetry.** Extract claims from the source
and from the rewrite in two calls that never see each other's input, then diff
the two lists both ways. A judge that has not seen the source cannot anchor on
its surface. Exclude the writing model from the panel. Store the raw answers.

**Calibrate by defect injection.** Build a labelled set by planting known defects
— a fabricated date, a deleted claim, a six-word lift — and score candidate
prompts on false-negative rate. `scripts/coverage_calibrate.py` and
`fixtures/tour-craft/coverage_calibration_set.json` hold the bake-off method to
copy; their `{fact, narration}` contract does not fit and should be replaced.

**Before any publish step.** `src/tour/routing.py` prefers a beat's stored
`est_spoken_seconds` over recomputing it from word count, and rewrites change
length materially. A publish that copies a new body without recomputing duration
silently stale-dates tour timing. That step is unbuilt, so this is free to get
right now.

## The limit no checker removes

Every gate here measures fidelity to one guidebook passage. A rewrite that is
perfectly faithful and shares no wording is still derived from that passage's
selection and arrangement, and a faithful rewrite of a wrong source ships a wrong
fact confidently. The structural answer is to write each beat from two or more
independent sources, which is already the project's own two-source rule. That is
an authoring change, not a verification change, and no architecture here
substitutes for it.
