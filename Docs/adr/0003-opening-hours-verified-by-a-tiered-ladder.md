---
status: superseded by ADR-0006
---

# Opening hours are verified by a tiered ladder with a permanent human floor

Whether a place has a door is its own field: `gated` is explicit, and hours
being unknown is never spelled the same as hours not applying. A verified
hours table records who or what verified it, at which tier, on what cited
evidence, and when — trust is inspectable per place, forever.

The ladder has three tiers. Tier 0: two independent sources agree, and the
row auto-passes with both pieces of evidence recorded. Tier 1: a model reads
the official source and extracts hours with a citation and a confidence; a
high-confidence cited row auto-passes, and a fixed sample of these is
human-audited. Tier 2: a human reviews every conflict, every low-confidence
row, every place above the gravity floor, and every place on a persona or
golden day. The floor is a dial per city, not a rewrite: a new or denser city
inherits the same queue.

Hours are re-fetched on a schedule. A re-fetch that disagrees with a verified
row demotes it to unverified on the spot — the voice falls back to the honest
could-not-confirm disclosure — and the row re-enters the queue at its tier. A
gated place without verified hours always fails open with that disclosure,
never closed.

The ladder is built from the pipeline's own primitives: audited model passes
in scripts, an interactive approve-loop that records its approver, gravity
scores that already price where a wrong "open" costs most.

## Considered Options

- **Human review of everything** — the highest floor, but it does not scale
  past one city or survive a denser corpus.
- **Trusting fetched hours as-is** — quietly redefines "verified" downward to
  "fetched".
- **Failing closed on unknown hours** — silently shrinks days on missing
  data; an honest disclosure beats a lost stop.
- **An orchestration framework (LangChain/LangGraph)** — solves stateful
  multi-actor graphs this pipeline does not have, and adds a second way to
  call a model beside the one every script already uses.
