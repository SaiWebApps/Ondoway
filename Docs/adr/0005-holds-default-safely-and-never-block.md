# ADR-0005 — a held story gets a safe default inside the job; review is optional

**Status:** accepted (owner, grilling session 2026-09-29), **not yet implemented** (as of
c23d9f0): `src/ingest/run.py` still only queues holds, and `POST /ingest/publish` still refuses
(409) while any held item is undecided (`src/api/routes/ingest.py`). The decision below is the
target, not the current behaviour. Applies ADR-0002 decision 4 ("a duplicate story is preferred
over a missing one … never blocks a publish") to the review queue, which blocks publishing today.

## Context

The job-B replay of 2026-09-28 held 9 of 54 stories (3 merge, 3 narration, 3 commit). At that
rate a one-district port holds hundreds, each waiting for a person, and an accepted item did
nothing. The owner ruled the proof loop over the same day: gate on safety, not on review.

## Decision

1. **Every hold gets its safe default inside the job that raised it**, so a job's output is final:
   - a **merge hold** (judge and guard disagree) is written as its own story: the claims the
     corpus does not already hold (per its claim disposition), re-narrated over just those
     claims. The worst case is a duplicate beat, never a lost fact;
   - a **narration hold** (P5 still refuses a sentence) is re-narrated once more; still refused,
     it is left out;
   - a **commit hold** (the validator refuses the beat: too few claims for its type, a lift) is
     left out.
   The re-narration cost is part of the job's printed estimate and its actual.
2. **Nothing blocks publishing.** The held file (`held/{job}.jsonl`) stays the audit list of what
   was defaulted and why.
3. **Review is optional and lives in the existing workbench screen** (`frontend/ingest.html`),
   which loads each city's held files so CLI jobs appear, and saves decisions beside them so
   they survive a restart. Its actions are overrides only: fold a possible duplicate into a claim
   the reviewer picks, or restore a left-out beat once fixed.

## Consequences

- The corpus gains labelled possible duplicates, which the tour builder's dedup already handles
  across beats; they are measured and swept after publishing, not argued about before.
- A left-out beat is missing until someone restores it: the safe direction for a refused
  narration or an invalid record, and every one is listed with its reason.
- The overrides (undo a duplicate, restore a left-out beat) are later work, not a gate.
