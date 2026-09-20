---
name: team
description: Use when shaping or delivering an Ondoway phase, feature, bug fix, refactor, migration, UI change, deployment, or dependency probe through Claude or Codex.
---

# Convergent Delivery

Ship one approved outcome through one authoritative implementation, in five
stages, with every rule that once lived in prose enforced by a tool that
refuses. Terms carry their one meaning from
[glossary.md](references/glossary.md).

## The five stages

**1. Shape.** Grill the owner until the story is bounded: criteria as tests
that are red today, a demo script of ten numbered steps or fewer in plain
words, at most six milestones, an expected file set, a commit budget with the
measured rate behind it. Record it:

```sh
python3 .claude/ledger/track.py feature-add --slug <slug> --title "..." --for-whom "..." --tier <n>
python3 .claude/ledger/track.py story-add --feature <slug> --id <S> --text "..." --said-by owner
python3 .claude/ledger/track.py criterion-add --story <S> --id <C> --text "..." --test-command "..."
python3 .claude/ledger/track.py issue-add --story <S> --id <M> --name "..." --test-command "..." --files ...
```

**2. Ground.** Before any code, inspect what exists:

```sh
python3 .agents/team/teamflow.py ownership inspect --query "<behavior>"
codegraph explore "<goal>"
```

Write the plan at `.claude/runs/<date>-<slug>/plan.md`. Every claim cites
`file:line`, exact lines carry their verbatim quote, and every new function or
file names the existing seam it extends or the search that came up empty. The
gate is mechanical and re-runs at every milestone claim:

```sh
python3 .claude/ledger/plan_check.py .claude/runs/<date>-<slug>/plan.md
```

**3. Seal.** Commit the red criterion tests first. Then the owner approves,
which the tracker only accepts when every story carries criteria, and which
stores the seal:

```sh
python3 .claude/ledger/track.py approve --feature <slug> --by owner --budget-commits <n> --rate-minutes <m>
```

After this, adding a milestone or criterion is refused without a single-use
owner change, the guard hook refuses edits to the process files, and the
interrupt budget counts down from three.

**4. Build.** One writer, one milestone per commit. The loop is four steps:
write or update the targeted test, make the change, confirm that test passes,
run the lint gate bare. Claim the milestone and let the tracker run the proof
itself:

```sh
python3 .claude/ledger/track.py step-status --id <M> --status completed
```

Red is red: no failing gate is called flaky, unrelated or pre-existing. A
surprise costs an interrupt or parks the story; it never becomes a new
milestone. A mistake after landed milestones is a defect: two per story, each
opened by a failing regression test.

**5. Close.** Every criterion green, the adversarial review run over the
story's commit range against the sealed criteria with every finding
dispositioned in the same run, the owner's demo held, and the wins stated in
two or three plain sentences. Only then:

```sh
python3 .claude/ledger/track.py story-state --id <S> --state Done
```

## Invariants

- Extend the named behavior owner. Never create a surface-specific copy of
  shared behavior; the clone detector in the lint gate refuses what slips
  through.
- Tests are evidence, not production consumers. New production code with no
  production path is rejected by the reachability check.
- One candidate receives one review, at most five findings, one evidence
  probe, and one repair. A finding without a runnable reproduction is dropped
  and logged, never left hanging.
- A finding is worked in the same run: registered in the tracker and built,
  or dropped with the reason logged. Never handed back to the owner as a
  suggested task — the owner approves shapes and verdicts, not work queues.
- Blame needs a differential. Before naming an environmental culprit or
  asking the owner to touch their machine, run the one-variable experiment
  that separates the suspect from innocence. Any fix that needs nothing from
  the owner is taken first; the owner's hands are the last resort.
- A reviewer may reject a broken promise but may not add a promise.
- Costs are reported, never silently acted on. No gate trades quality for
  spend without the owner deciding it.
- One active story at a time. At session start, reconcile first: release any
  stale lease, finish or revert any dirty tree, and say which was done.
- The pipeline itself changes only between stories, through a recorded
  release window, from a written learnings entry, with the owner approving.

## Communication

Reports use plain words and the four-line shape: outcome in one sentence,
evidence in at most three bullets, remaining obligation in one sentence, a
decision only at a mandatory gate. Parked stories come first. Estimates carry
their measured rate or are not given.
