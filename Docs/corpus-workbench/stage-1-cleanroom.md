# Stage 1 — regenerating the backlog from claims the writer could not trace

Read [rebuild-brief.md](rebuild-brief.md) first, then [stage-0-triage.md](stage-0-triage.md).

**Nothing has shipped.** The output lives in `data/{city}/reauthored-cleanroom.json`,
which is gitignored. Every beat in `beats.json` still carries its original copied body,
as it did before Stage 0.

**The headline is not the seam. It is that this output is not shippable, and no free
check can tell you which parts are.** An acceptance read of 69 records found **45%
carrying statements the claims do not support, 23% materially** — invented benches at
Columbus Park, invented boats at Flushing Meadows, "nothing marks the building" at 1040
5th Avenue, "four musicians from Liverpool" with no support in the claims *or* the
source.

That read was of the corpus **before** the regeneration below, and nothing changed since
targets that class, so the current bodies are not better on it — they are simply unread.
That is a weaker position to ship from, not a stronger one. The free check for the class
— content words in a body that no claim carried — flags **434 of the 449 bodies that now
exist**, so it ranks a review queue and gates nothing. Stage 2 is therefore a
precondition for shipping, not the next item on a list.

## The numbers

| | Paris | New York |
|---|---|---|
| usable claim sets | 208 / 246 | 241 / 278 |
| bodies written | 208 | 241 |
| flagged for a person | 6 | 5 |
| share an 8+ word run with a source never seen | 27 | 46 |
| scene signals, recorded not flagged | 14 | 13 |

**449 of the 524 backlog were regenerated**, 86% of it. Across both cities: **11 bodies
carry a flag**, 16% share an 8+ word run, length is 1.13× the body replaced as a median
of per-record ratios, and **1.5% close on a hedged impression, down from 27%**. Bodies
follow their source's sentence order **47%** of the time by this repo's
`order_follows_source`, against **91%** for the pipeline this replaces on the same
measure — an independent lexical alignment reads both ends higher, so the drop is the
finding and the percentage is not — the remainder is chronology the writer re-derived on its own,
which is the world's order rather than the guidebook's.

None of those numbers say the output is good. They say what is left after every check
that costs nothing, and the acceptance read above is what a person found in the part
they cannot see.

## What it does

Two phases. The source becomes an unordered set of atomic, self-contained claims; the
set is shuffled and handed to the writer **alone**. A writer cannot reproduce an
arrangement it was never shown, which is F2 removed at the root rather than detected
afterwards.

`cleanroom_request` takes claims, a place, a city and a word count. There is no parameter
through which the passage or the copied body could arrive.

## What this version corrects

Three claims in the previous version of this file were refuted and are withdrawn.

- **"Across all 453 finished bodies: 0 invented sources, 0 first person, 0 stage
  direction."** Refuted by a replication skeptic and by this repo's own gates. The
  attribution half held; the other two did not. Two bodies spoke in the first person and
  thirteen staged the listener. The gates that now measure this live in the code, and the
  number is re-derived rather than asserted.
- **"Only 23 of 51 Paris re-asks improved on the first."** Not reproducible from the
  artifact and withdrawn. The run reports the figure now.
- **"1.14× / 1.13× length."** True as a ratio of summed word counts; the median of
  per-record ratios is 1.09 / 1.08. The definition was not stated and the two differ
  enough to matter.

A fourth was mine, made in this session and refuted in it: **"47 of 84 shared runs split
entirely into claim-internal fragments"** counted single-word coincidences as fragments.
At a two-word floor it is 23 of 84.

## The seam, and the evidence that actually carries it

The writer payload for every record was rebuilt from its stored claim set and measured
against the passage it came from: **0** passages and **0** original bodies appear in any
payload, the longest run any payload shares with its source is **7 words**, and every
body's stored hash matches the claims it carries.

Some bodies still share an 8+ word run with a passage the writer never saw. Two
independent adversaries treated that as possibly falsifying the stage. It is not, and the
strongest evidence is structural rather than statistical:

- **No claim in any flagged record shares 8+ words with the source. The ceiling across
  all of them is exactly 7**, because the claim gate blocks at 8 before a writer is ever
  called. A run of 8 in a body is therefore *structurally impossible* to produce by
  copying one over-literal claim — it requires stitching two, which is what adjacency
  means.
- **The flagged records are not a distinct population.** Content words shared with the
  source but absent from every claim, as a rate: **0.0370** for bodies with a run of 8 or
  more, **0.0386** for the rest. Memorised source prose would put the flagged group
  above the others; it sits fractionally below.
- **The measure detects something real.** Across 2000 body/source pairs from *unrelated*
  beats, the longest shared run never reaches 8 and tops out at 4.

What remains open is honest to state: a universal negative cannot be established from
these files. Whether the writer has this published guidebook in pretraining is not
testable from an artifact that records only what it was given and what it produced.

**The writer cannot be iterated out of the remaining runs**, because telling it which
words to avoid means showing it the run — which is source prose, and the clean room
exists to prevent exactly that. The seam that makes non-derivation provable is the same
seam that forbids correcting the output against the source.

## What running it found that designing it did not

**The copied body was the only thing telling the writer how long a beat is.** Removing
it, the first sample came back 1.81× longer — which eats the silence the tour is built
around and stale-dates every stored `est_spoken_seconds`. The request now carries a word
count: one integer, no expression, no arrangement.

**A writer given freedom invents.** Stage direction for a widening no claim mentions,
first-person narration, and — worst — "the author compares it to butterflies" rendered as
"**Some visitors describe it as**", fabricating a source for one writer's impression.

**Claims pointed at their neighbours** — "That uprising alarmed the kings" resolves to
nothing once the set is shuffled, which is the single property the design rests on.

**A 213-word passage came back as 4 claims**, dropping the birth dates, both quotations
and the closing quote. Omission is the decomposer's dangerous failure because it looks
exactly like silence.

**Shuffling separates two events and loses the relation between them.** A body attached a
charity pledge to the offer that was *rejected*, because "he agreed to donate the
profits" and "the mayor declined the arrangement" arrived as two unordered facts. A
relation the passage states — because of, instead of, in return for, as a condition of —
is itself a fact, and belongs inside one claim naming both sides. That is a decomposer
rule, not a writer rule: causation is a fact, not an arrangement, so stating it crosses
nothing the seam exists to stop.

**The guidebook's own furniture reached the listener.** "That's the route the guidebook
gives", "the guidebook simply says to walk up it". The decomposer read a page number as
fact because the page states it as fact. Both phases now refuse it — and the gate that
was supposed to catch it could not match "page 139", because `page \d` with a trailing
word boundary stops at the first digit.

**The corpus closed the same way over and over.** 27% of Paris bodies ended on a hedged
impression and 41% contained one; a listener walking a dozen stops hears one voice with
one move. This is a corpus-level uniformity, invisible to any per-body check, so it is
fixed where it is caused — the writer is told at most one, never as the close.

## What a written body earns, and what it does not

Claim sets were gated and refused; bodies were only measured, so a body that broke a
voice rule was written into the corpus and counted. Four free gates now run on every
one — first person outside a quotation, the guidebook's furniture, an imperative no claim
asked for, an impression offered where the claim set holds no observation — and a body
that breaks one is asked again with **its own draft quoted back**. Naming a defect in the
writer's own prose shows it nothing of the source, which is why a voice rule can be
corrected here and an eight-word run cannot.

**A flawed body is flagged, never refused.** Refusing one keeps the beat's original
copied guidebook body — the single unfixable defect this stage exists to remove — in
order to avoid a defect a second ask fixes. That trade is backwards, and the refusal
mechanism's only observed effect before it was reversed was discarding two correct New
York bodies to false positives: "a **US** Coast Guard Cutter" and "Arthur Miller wrote
**All My** Sons" both read as the tour speaking in the first person.

Bodies carry `flags`, and `reauthor_review.review_order` already ranks a queue by that
field. Its candidate path named only the rewrite artifact, so routing there was a
sentence and not a path. `/api/reauthored?source=cleanroom` now serves the clean-room
bodies to the same review page, ranked the same way and decided through the same POST,
which writes back to the file the record came from. The two artifacts are asked different
questions: a rewrite carries a panel's verdict, so a person sees an escalation or a beat
nothing judged; a clean-room body carries no verdict, so a person sees what a gate
flagged.

A fifth measure is recorded and deliberately does **not** flag: things a body puts in
front of the listener that no claim mentions. About a third of what it finds is the
body's own word for something a claim does name — "lawn" for a named meadow, "door" for a
portal — and refusing those would trade a correct body for a different one.

## A gate I built wrong, and one that does not work

The first coverage check asked whether any *single* claim carried each source sentence.
It refused 34% of the corpus, including decompositions that lost nothing, because the
prompt asks the decomposer to split one sentence into several claims and reword each of
them. Measuring against the **union** took refusals to 26%.

Then I measured what it catches, by deleting claims from sets it had passed. Deleting one
claim: **13% caught in Paris, 6% in New York.** Deleting 30% of a set: **39% and 37%.**
No variant tried does better — best-single-claim reaches 21% at the cost of 13% false
refusals, and a claims-per-sentence floor catches 0–3%.

**It is a gross under-decomposition detector, not an omission gate**, and it is kept for
that and reported as that. Omission is Stage 2's.

## The 71 that were not regenerated

Claim sets still refused after a second ask keep their **original copied bodies** — the
status quo, so nothing regresses, but they remain in the backlog Stage 0 measured and are
not progress. A refused set is re-bought on the next run rather than kept, which is why
one decompose pass reports more refusals than two.

## Reading the Stage 0 gates on this output

`blocked-run` does not mean here what it meant in the Stage 0 report. There it was
evidence of copying. Here the writer provably never saw the passage, so a shared run is
evidence the phrasing is hard to avoid. **Same gate, different question** — the counts are
not comparable and should not be put in adjacent columns.

The conflict gate is unchanged in meaning: it compares `source_passage` against
`body_before`, both untouched by Stage 1. Those blocks are the same inherited
disagreements Stage 0 found, and they still need a person, because the claims came from
the source and the new body therefore ships the source's value. An acceptance read
rediscovered one of them from the output alone: Chinatown's founding century is faithful
to the guidebook and wrong in the world.

## What is still owed

- **Stage 2**, verification with real asymmetry, and it gates shipping rather than
  following it. The panel must exclude `claude-opus-5`, which both decomposed and wrote,
  and the record names both roles so that exclusion is read from evidence rather than
  from a constant. The class it must catch is the one no free check reaches: a true
  detail the claims never carried, and a false one, are indistinguishable to every gate
  in this file.
- **The publish step, with a contract.** Nothing carries a regenerated body into
  `beats.json`. When something does, it must refuse to carry a flagged body without a
  person clearing it — flags nothing reads are decoration — and it must recompute
  `est_spoken_seconds` rather than copying it.
- `reauthor_run.py`, the refuted path, is still live pending its own removal.

```
ONDOWAY_DEMO_APPROVE=1 uv run python scripts/reauthor_cleanroom.py --city paris --phase decompose
ONDOWAY_DEMO_APPROVE=1 uv run python scripts/reauthor_cleanroom.py --city paris --phase write
uv run python scripts/reauthor_cleanroom.py --city paris --phase write --dry-run
```
