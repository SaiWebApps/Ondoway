# Stage 1 — regenerating 453 beats from claims the writer could not trace

Read [rebuild-brief.md](rebuild-brief.md) first, then
[stage-0-triage.md](stage-0-triage.md). This records what the clean-room rebuild
produced, what it fixed, and the one thing it demonstrably does not.

**Nothing has shipped.** The output lives in
`data/{city}/reauthored-cleanroom.json`, which is gitignored. Every beat in
`beats.json` still carries its original copied body, as it did before Stage 0.

## What it does

Two phases. The source becomes an unordered set of atomic, self-contained claims;
the set is shuffled and handed to the writer **alone**. A writer cannot reproduce
an arrangement it was never shown, which is F2 removed at the root rather than
detected afterwards.

`cleanroom_request` takes claims, a place, a city and a word count. There is no
parameter through which the passage or the copied body could arrive.

## The headline

| | Paris | New York |
|---|---|---|
| usable claim sets | 216 / 246 | 237 / 278 |
| bodies written | 216 | 237 |
| **follows the source's sentence order** | **36%** | **32%** |
| median verbatim ratio | 0.000 | 0.000 |
| length vs the body replaced | 1.14× | 1.13× |
| clears both Stage 0 gates | 170 | 184 |

**453 of the 524 backlog were regenerated.** The old pipeline's rewrites followed
their source's order 91% of the time. These follow it about a third of the time,
and the remainder is chronology the writer re-derived on its own — the world's
order, not the guidebook's.

## The seam, checked on production records rather than a fixture

The writer payload for **every one of the 453 records** was rebuilt from its stored
claim set and measured against the passage it came from:

- passages appearing verbatim in a writer payload: **0**
- original bodies appearing verbatim in a writer payload: **0**
- longest run any payload shares with its source: **7 words** (the gate blocks at 8)
- bodies whose stored hash matches the claims they carry: **453 / 453**

That is the audit trail the brief asked for: evidence of non-derivation that is a
record of what the writer was given, not a score computed afterwards.

## What running it found that designing it did not

Every defect below came from output, not from review. Each is fixed and asserted.

**The copied body was the only thing telling the writer how long a beat is.**
Removing it, the first sample came back **1.81× longer** — which eats the silence
the tour is built around and stale-dates every stored `est_spoken_seconds`. The
request now carries a word count: one integer, no expression, no arrangement.
Result: 1.13–1.14×.

**A writer given freedom invents.** The same sample produced stage direction for a
widening no claim mentions, first-person narration, and — worst — turned "the
author compares it to butterflies" into "**Some visitors describe it as**",
fabricating a source for one writer's impression. All three are named in the prompt
and asserted in tests. Across all 453 finished bodies: **0 invented sources, 0
first person, 0 stage direction.**

**47 claims across 23 beats pointed at their neighbours** — "That uprising alarmed
the kings" resolves to nothing once the set is shuffled, which is the single
property the design rests on.

**Square Jean XXIII came back as 4 claims from 213 words**, dropping the birth
dates, both quotations and the closing quote. A beat written from it would have
lost most of its passage, and nothing downstream could tell that from a source that
never said those things. Omission is the decomposer's dangerous failure because it
looks exactly like silence.

Both now gate for free before any writer call, and feed one second ask.

## A gate I built wrong, and how it showed

The first coverage check asked whether any *single* claim carried each source
sentence. It refused **34% of the corpus** — including decompositions that lost
nothing, because the prompt asks the decomposer to split one sentence into several
claims and reword each of them, and shared-word coverage measures rewording. Two
instructions pulling against each other.

Measuring against the **union** of the claims took refusals to 26%. The test
fixture is now a real corpus case: one Saint-Roch sentence became three claims,
best single 0.22, union 0.40. A sentence under four salient words ("Anything
wiggling?") states no fact and is no longer held against a set.

Two further corrections came from the same direction. A second ask can come back
worse, so the attempt with fewer problems is kept — **and only 23 of 51 Paris
re-asks improved on the first**. And the gates are re-run over stored claim sets
rather than trusted from a stored flag, because a set graded by a weaker gate would
otherwise stay usable forever, which is how a refuted check survives its
replacement.

## What the clean room does not fix

**38 Paris and 45 New York bodies still share an 8+ word run with a passage the
writer never saw.** This is the honest finding of the stage and it is not a bug in
the writer.

Every one of the 38 has a run **longer than the longest run in any claim it was
given**. Median body run 9 words, median best-claim run 6. **Zero of 38** have a
body run at or under their best claim's. The claims each stay under the threshold;
placing two of them adjacently restores more of the source's wording than either
carries alone.

No claim-level cap bounds this, because the run comes from adjacency rather than
from any single claim. Measured on the Paris output:

| claim cap | claims needing a re-ask | of the 38 bodies touched |
|---|---|---|
| 7 words | 67 / 2103 | 15 |
| 6 words | 179 / 2103 | 26 |
| 5 words | 442 / 2103 | 36 |

**And the writer cannot be iterated out of it**, because telling it which words to
avoid means showing it the run — which is source prose, and the clean room exists
to prevent exactly that. This is a real architectural consequence, not an oversight:
the seam that makes non-derivation provable is the same seam that forbids
correcting the output against the source.

## Reading the Stage 0 gates on this output

`blocked-run` does not mean here what it meant in the Stage 0 report. There it was
evidence of copying. Here the writer provably never saw the passage, so a shared
run is evidence the phrasing is hard to avoid. **Same gate, different question** —
the counts are not comparable and should not be put in adjacent columns.

The conflict gate is unchanged in meaning: it compares `source_passage` against
`body_before`, both untouched by Stage 1. The 16 blocks are the same inherited
disagreements Stage 0 found, and they still need a person, because the claims came
from the source and the new body therefore ships the source's value.

## The 71 that were not regenerated

30 Paris and 41 New York claim sets are still refused after a second ask. Those
beats **keep their original copied bodies** — the status quo, so nothing regresses,
but they remain in the backlog Stage 0 measured and are not progress. "453 of 524"
is regeneration of 86% of the backlog, not of all of it.

## What is still owed

- **Stage 2**, verification with real asymmetry. The panel must exclude
  `claude-opus-5`, which both decomposed and wrote, and the record names both roles
  so that exclusion is read from evidence rather than from a constant.
- `verbatim_summary` in `corpus_report.py` still reports only the ratio F3 refuted,
  so the dashboard does not show `max_verbatim_run`.
- `reauthor_run.py`, the refuted path, is still live pending its own removal.
- Nothing carries a regenerated body into `beats.json`, and a publish step must
  recompute `est_spoken_seconds` rather than copying it.

```
ONDOWAY_DEMO_APPROVE=1 uv run python scripts/reauthor_cleanroom.py --city paris --phase decompose
ONDOWAY_DEMO_APPROVE=1 uv run python scripts/reauthor_cleanroom.py --city paris --phase write
uv run python scripts/reauthor_cleanroom.py --city paris --phase write --dry-run
```
