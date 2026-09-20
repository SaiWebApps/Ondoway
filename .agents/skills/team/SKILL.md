---
name: team
description: Use when shaping or delivering an Ondoway phase, feature, bug fix, refactor, migration, UI change, deployment, or dependency probe through Claude or Codex.
---

# Convergent Delivery

Ship one approved outcome through one authoritative implementation. The workflow ends in a truthful state; review never creates scope.

## Start

1. Run `python3 .agents/team/teamflow.py ownership inspect --query "<behavior>"` before proposing code.
2. If no approved run exists, read [charter.md](references/charter.md), shape the charter, and stop for its required approval.
3. Read the reference matching the work:
   - Feature or UI: [delivery.md](references/delivery.md)
   - Bug: use systematic debugging, then [delivery.md](references/delivery.md)
   - Refactor, replacement, or migration: [ownership.md](references/ownership.md) and [delivery.md](references/delivery.md)
   - Review: [review.md](references/review.md)
   - Deployment or external mutation: [release.md](references/release.md)

## Invariants

- Freeze outcomes, invariants, evidence, ownership, risk, and budgets. Implementation notes may adapt.
- Extend the named behavior owner. Never create a surface-specific copy of shared behavior.
- Acquire the durable lease before mutation. Only one active writer exists.
- Tests are evidence, not production consumers. New production code with no production path is rejected.
- Replacement includes retirement. Temporary overlap needs an activation/removal slot and expiry.
- One candidate receives one review, at most five findings, one evidence probe, and one repair.
- Findings are `BLOCK`, `EVIDENCE_REQUIRED`, or `OUTSIDE_CONTRACT`. The last is discarded, not backlogged.
- Never call a commit, merge, accepted slice, or milestone released. Release requires aggregate evidence against the named target.
- Use the transition CLI for state. Do not edit run state by hand.

## Communication

Report only:

- Outcome: one sentence.
- Evidence: at most three bullets or links.
- Remaining obligation: one sentence.
- Decision needed: only at a mandatory gate.

Stop when the CLI reports `RELEASED`, `BLOCKED_EXTERNAL`, `CONTRACT_INVALID`, `ROLLED_BACK`, or `ABORTED`.
