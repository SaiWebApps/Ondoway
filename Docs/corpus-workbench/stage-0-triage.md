# Stage 0 — what two free gates say about the 524 re-authored beats

Read [rebuild-brief.md](rebuild-brief.md) first. This is the answer to the Stage 0
question it asks: how much of the existing re-author output survives a re-triage
that costs nothing. No model was called to produce any number here.

## The answer in three lines

- **298 of the 384 auto-approvals survive both gates. 86 (22%) are demoted.**
- **100 of the 140 escalations are clean on both gates**, so a person can still
  repair them; the other 40 carry a lift or a conflict as well and are regeneration
  work, not review work.
- **Surviving is not verification.** Layering in the arrangement finding (F2), only
  **20 of the 384 approvals** are free of all three known defects. That is the number
  Stage 1 should be sized against.

| | total | clear | blocked: run | conflict | both |
|---|---|---|---|---|---|
| all candidates | 524 | 398 | 102 | 18 | 6 |
| was auto-approved | 384 | 298 | 66 | 14 | 6 |
| was escalated | 140 | 100 | 36 | 4 | 0 |

Paris 246 (194 clear), New York 278 (204 clear). London is excluded and stays
excluded.

## Gate 1 — longest shared run, outside an attributed quotation

`scripts/corpus_report.py` gains `max_verbatim_run`; `verbatim_ratio` is untouched
and still reports corpus-wide copying. The gate blocks at 8 words, the same width
the module's docstring already called evidence of a lift.

**The tokenizer is calibrated against the hand-verified figure, not chosen.** F3
states that 81 of the 384 approvals carry an 8+ word run. Three plausible
tokenizations give 51, 75 and 81. Only one — digits and letters, case-folded,
everything else a boundary — reproduces **exactly 81**, so that is the contract, and
every run number below is on the same footing as the number the brief verified by
hand. Raw counts: 81 approvals and 38 escalations, 119 of 524.

Words inside an **attributed** quotation are cut out of the body before the run is
measured, which drops the blocked approvals from 81 to 66. Cutting rather than
deleting matters: a quotation split by "he told a friend" leaves those four words
behind instead of one apparent lift straddling both halves. A quotation naming
nobody is not exempt — that is the shape of an unmarked lift.

**What the 108 blocked runs are made of**, because the headline is misleading
without it: **51 are prose** and **57 are names and numbers** — a museum's holdings,
a list of signatories, a street address. A name list carries no protectable
expression, and the gate blocks it anyway, because the brief specifies one exemption
and this is not it. If a carve-out is wanted, that is a decision to take
deliberately; here are three of the 57:

- `paintings by Monet, Van Gogh and Degas, sculpture by Constantin Brancusi`
- `Jerry Seinfeld … Bono, Amy Poehler and Tina Fey, Dan and Phil, Trevor Noah`
- `The Burghers of Calais and The Gates of Hell … Michelangelo's Last Judgment`

The longest run surviving the exemption is 16 words.

## Gate 2 — the source and the shipped body disagree

24 records disagree with their source on a number, a date or a proper name, so they
can never auto-approve. **The brief's F4 says 29 carry "that conflict shape" and
states no criterion for it, so 24 is not a reproduction of 29 — it is a different
measurement, reported beside it rather than tuned toward it.**

**All three numeric conflicts among the approvals shipped the source's value.**
Checked in `body_after`, not inferred:

| beat | body_before | source | rewrite shipped |
|---|---|---|---|
| Chelsea Piers | Pier 59 | Pier 60 | Pier 60 |
| Guggenheim | 20,000 sq ft | 50,000 sq ft | 50,000 sq ft |
| Radio City | December 27, 1932 | December 23, 1932 | December 23, 1932 |

The first two are F4's known cases; **Radio City is a third the brief did not
have**. Every one was auto-approved with an empty `reason`. The hazard is
demonstrated, not theoretical.

The 20 name conflicts are mostly of one kind: the scanned source is corrupted and
the body is right — `Charles Gamier`, `river Seme`, `Edith Piai`, `fluctuat nec
mergitor`. Blocking them is correct, because the rewrite is told the source is the
only truth and would launder the corruption, but they are not the "shipped fact
changed" defect. **One is substantive: `Romanesque Revival` in the body against
`Renaissance Revival` in the source.**

## What the gates miss, measured rather than asserted

`scripts/reauthor_triage_calibrate.py` builds a labelled set by planting one changed
value in bodies that currently agree with their source, and scores recall per
channel. Catching the two conflicts the gate was designed around proves nothing;
these are defects it never saw.

| channel | Paris | New York |
|---|---|---|
| digit changed, context kept | 66% | 64% |
| proper-name token substituted | 90% | 97% |
| figure changed, its noun deleted | 23% | 37% |
| figure spelled out (`8` → `eight`) | **0%** | **0%** |

Recall is a third to a half on the two weakest channels. **Read as a floor, not a
score.** Two limits are structural: the gate compares digits, so a spelled-out
number is invisible to it, and a number whose neighbouring noun is gone has no slot
to be compared on. It is also one-directional by design — a number in the source
that the body never mentions is a possible loss, which is a different question.

This script measures misses and **cannot** measure false alarms: its baseline is
defined as "what the gate passes", so counting alarms there would always answer
zero. Precision came from reading every flagged row instead. **4 of 4 numeric
conflicts are real; roughly 5 of 20 name conflicts are benign** (`Village St-Paul`
against `Village Saint-Paul`, `Edgecombe Ave` against `Edgecombe Avenue`).

Three false positives found that way were fixed, each with a regression test: a
pronoun keying a slot (Voltaire's 1694 against 1778), a slot reaching across a
sentence boundary (both texts said €6), and a one-word name matching any other
one-word name (Molière against Aragon). The last was a design error, not a tuning
one — with a single token, "differs in exactly one position" is true of every
unequal pair.

## The finding neither gate touches

F2 — the rewrites follow their source's sentence order — is reported and
deliberately **not** gated. It is a property Stage 1 removes by never showing the
writer the source's order; no checker repairs it.

The brief's 278 of 306 (91%) is **not exactly reproducible**: no sentence splitter
tried yields a population of 306 (they give 304–326). The finding itself is robust.
Across three splitters the rate is 90–91%; the implementation here reads 84%. So:
**84–91% depending on measurement choices, and the substance stands.**

Of the 298 approvals clearing both gates, 129 follow the source's order, 20 diverge,
and 149 have a source too short for order to be a property. **20 of 384 approvals
are free of all three known defects.**

## What this does not say

Every rewrite here was graded by a panel containing the model that wrote it (F1),
and neither gate touches that. `clear` means *eligible for real verification*. It
does not mean verified, and nothing here is shippable on this evidence.

The gates are free and deterministic, so they are re-run rather than stored;
`data/{city}/reauthored.json` is gitignored and nothing writes back to it.

```
uv run python scripts/reauthor_triage.py --city new_york
uv run python scripts/reauthor_triage.py --city paris --show blocked-conflict
uv run python scripts/reauthor_triage_calibrate.py --city paris
```
