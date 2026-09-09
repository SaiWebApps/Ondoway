# Stage 0 — what two free gates say about the 524 re-authored beats

> **Retired in slice 0 of the ingestion rebuild.** This document describes the Lane B
> re-author pipeline, which was retired: its scripts, tests and review page were moved
> under `_to_be_deleted/` and its dashboard routes removed, so nothing described here runs.
> It is kept as a record until slice 11 deletes it; the live plan is
> `Docs/ingestion/rebuild-spec.md`.

Read [rebuild-brief.md](rebuild-brief.md) first. This is the answer to the Stage 0
question it asks: how much of the existing re-author output survives a re-triage
that costs nothing. No model was called to produce any number here.

Every claim below was attacked by three adversaries on three models before it was
written. Four of them landed and the code changed; what they refuted is recorded in
place rather than removed.

## The answer in three lines

- **294 of the 384 auto-approvals survive both gates. 90 (23%) are demoted.**
- **100 of the 140 escalations are clean on both gates**, so a person can still
  repair them; the other 40 carry a lift or a conflict as well and are regeneration
  work, not review work.
- **Surviving is not verification.** Every rewrite here was graded by a panel
  containing the model that wrote it (F1), and neither gate touches that. `clear`
  means *eligible for real verification*.

| | total | clear | blocked: run | conflict | both |
|---|---|---|---|---|---|
| all candidates | 524 | 394 | 103 | 20 | 7 |
| was auto-approved | 384 | 294 | 68 | 16 | 6 |
| was escalated | 140 | 100 | 35 | 4 | 1 |

An independent reimplementation, written without reading this code, reached
**294 / 90** on the approvals and **100** clear escalations — the same figures.

Paris 246, New York 278. London is excluded and stays excluded.

## Gate 1 — longest shared run, outside an attributed quotation

`scripts/corpus_report.py` gains `max_verbatim_run`; `verbatim_ratio` is untouched
and still reports corpus-wide copying. The gate blocks at 8 words, the same width
the module's docstring already called evidence of a lift.

**The tokenizer is the loosest of the candidates, not the correct one.** An earlier
draft of this document claimed it was "calibrated" because it alone reproduces F3's
81. That claim was refuted and is withdrawn. Reproducing 81 shows only that this
tokenizer matches the one the previous session used — and **27 approvals sit at
exactly 8 words**, so tokenization alone decides them. The disputed ones are
possessives and hyphenated names split into filler: `in the streets of paris the
king s`, `on the left bank s quai de conti`, `nurse on v j day in 1945` (V-J),
`4 000 years of jewish art` (a split thousands separator). All of those are under
8 words to a human reader.

**Honest count: 75–84 approvals carry an 8+ word run, depending on tokenization** —
across two independent implementations the counts spanned 51 to 84. The
contract here yields 81 and is the most blocking of the reasonable options, which is
the safe direction to be wrong in — a wrongly blocked beat costs a person's minute.
The whole spread moves the demoted count by about 4 records out of 384.

Words inside an **attributed** quotation are cut out of the body before the run is
measured. Cutting rather than deleting matters: a quotation split by "he told a
friend" leaves those four words behind instead of one apparent lift straddling both
halves. Among the approvals this takes 81 down to **72 still run-blocked — the
exemption clears 9**, not 15. (An earlier draft said "81 to 66", which wrongly
excluded the 6 records blocked by both gates.)

**A pointer is not an attribution.** The cue list originally admitted `words`,
`line`, `sign`, `reading` and `put`. Those identify quoted text without saying whose
it is, and one of them exempted a real approval: a wall inscription reading "I love
you", where the lift was the narration `the words … in 311 languages` around it.
They are gone. Only acts of saying and named sources of words remain, which now
blocks a genuine "a line from the Bible" as well — the safe direction again.

The exemption is the only mechanism that disables Gate 1, so all 9 records it saves
were read under the final cue list. Every one is a real person or document being
quoted and named — Mme de Sévigné, Colette, Rodin's letter, Raskob, Sadowsky's
statement on Corona — reached through `wrote`, `letter`, `called`, `asked`, `said`
or `describing`. No exemption rests on a weak cue and none runs away.

**What the 110 blocked runs are made of**: **63 are prose** and **47 are names and
numbers**. An earlier draft said 51/57; `run_shape` was counting sentence-initial
`The` and `In` as proper nouns, which biased the split toward "shared fact" and
understated real lifts. It now reuses `proper_names`, which already drops them.
A name list carries no protectable expression and the gate blocks it anyway, because
the brief specifies one exemption and this is not it. If a carve-out is wanted, that
is a decision to take deliberately; three of the 47:

- `paintings by Monet, Van Gogh and Degas, sculpture by Constantin Brancusi`
- `Jerry Seinfeld … Bono, Amy Poehler and Tina Fey, Dan and Phil, Trevor Noah`
- `The Burghers of Calais and The Gates of Hell … Michelangelo's Last Judgment`

The longest run surviving the exemption is 16 words. Gate 1 measures only against
`source_passage`; 3 records that clear it carry an 8+ run against `body_before`.

## Gate 2 — the source and the shipped body disagree

27 records disagree with their source on a number, a date or a proper name, so they
can never auto-approve. **The brief's F4 says 29 carry "that conflict shape" and
states no criterion for it, so 27 is not a reproduction of 29 — it is a different
measurement, reported beside it rather than tuned toward it.**

**Five conflicts among the approvals shipped the source's value.** Checked in
`body_after`, not inferred:

| beat | body_before | source | rewrite shipped |
|---|---|---|---|
| Chelsea Piers | Pier 59 | Pier 60 | Pier 60 |
| Guggenheim | 20,000 sq ft | 50,000 sq ft | 50,000 sq ft |
| Radio City | December 27, 1932 | December 23, 1932 | December 23, 1932 |
| Chinatown | mid-19th century | mid-18th century | "dates to the mid-1700s" |
| Conciergerie | built 1301 to 1315 | built 1301–15 | (same fact; see below) |

The first two are F4's known cases. **Radio City and Chinatown are defects the brief
did not have**, and Chinatown is the worst of them: it moves a founding date by a
hundred years, and the body's "mid-19th century" is the historically recorded one,
so the rewrite laundered the guidebook's error. Pier 59 and December 27 are likewise
the externally recorded values. **These approvals did not merely change a shipped
fact — they replaced a correct one with a wrong one**, each with an empty `reason`.

Chinatown was missed by the first version of this gate and was found by an
adversary. Two fixes followed, both of which the corpus forced rather than
suggested:

- **Dates hide behind prepositions.** `opened in 1932` puts a function word between
  the figure and the noun that says which fact it is, and **61% of the corpus's
  four-digit years sit in that shape**. The slot key is now the nearest naming word
  within three steps, not the adjacent one.
- **An ordinal is a figure.** `str.isdigit` sees no number in `mid-18th century`.

The 20 name conflicts are mostly of one kind: the scanned source is corrupted and
the body is right — `Charles Gamier`, `river Seme`, `Edith Piai`, `fluctuat nec
mergitor`. Blocking them is correct, because the rewrite is told the source is the
only truth and would launder the corruption. **One is substantive:** `Romanesque
Revival` in the body against `Renaissance Revival` in the source.

## What the gates miss, measured rather than asserted

`_to_be_deleted/scripts/reauthor_triage_calibrate.py` builds a labelled set by planting one changed
value in bodies that currently agree with their source, and scores recall per
channel. Catching the conflicts the gate was designed around proves nothing; these
are defects it never saw.

| channel | Paris | New York |
|---|---|---|
| digit changed, context kept | 98% | 98% |
| figure changed, its noun deleted | 95% | 94% |
| proper-name token substituted | 90% | 97% |
| figure spelled out (`8` → `eight`) | **0%** | **0%** |

The first two channels read 66% and 23–37% before the preposition-walk fix; that fix
is why they moved, and it is the same fix that catches Chinatown. **One structural
blind spot remains and will not close by tuning: the gate compares figures, so a
spelled-out number is invisible to it.** Decade shorthand (`the 1930s`) is invisible
for the same reason — it is neither a plain figure nor an ordinal. The name channel's 90–97% is recall on the
detector's own shape — the injector plants the two-token substitution the detector
accepts — so it is not evidence about names renamed wholesale, which is an
acknowledged miss.

Gate 2 is one-directional by design: a number in the source that the body never
mentions is a possible loss, which is a different question.

**This script measures misses and cannot measure false alarms**: its baseline is
defined as "what the gate passes", so counting alarms there would always answer
zero. Precision came from reading every flagged row instead. **5 of 7 numeric
conflicts are real.** The two false positives are both alignment artifacts worth
naming rather than fixing, because fixing them would be fitting to these 524
records:

- **Range shorthand.** Source `built in 1301–15`, body `built in 1301 to 1315` —
  the same fact written two ways.
- **A clause boundary that is a semicolon on one side and a full stop on the
  other**, which puts two unrelated figures in one sentence on one side only.

Roughly 5 of the 20 name conflicts are likewise benign (`Village St-Paul` against
`Village Saint-Paul`, `Edgecombe Ave` against `Edgecombe Avenue`, and a body naming
a second real place the source does not mention).

Three earlier false positives were fixed with regression tests: a pronoun keying a
slot (Voltaire's 1694 against 1778), a slot reaching across a sentence boundary
(both texts said €6), and a one-word name matching any other one-word name (Molière
against Aragon). The last was a design error, not a tuning one — with a single
token, "differs in exactly one position" is true of every unequal pair.

## The finding neither gate touches

F2 — the rewrites follow their source's sentence order — is reported and
deliberately **not** gated. It is a property Stage 1 removes by never showing the
writer the source's order; no checker repairs it.

The implementation now reads **276 of 302 scoreable, 91%** — the brief's own figure.
It read 84% until an adversary found that the sentence bags included function words,
so a rewrite sentence could match a source sentence it shared nothing but "the" and
"of" with. The brief's *population* of 306 is still not reproducible: no splitter
tried yields it (they give 302–326).

**Do not size Stage 1 against a single number here.** An earlier draft said "20 of
384 approvals are free of all three defects" and called it the sizing input. That
was refuted twice over. It now reads 8 rather than 20 after the stopword fix, an
independent reimplementation spanned 0–16 across four defensible matching rules, and
the count silently treated 152 approvals whose source is under three sentences as
carrying F2 — but a two-sentence source has no arrangement to copy. The defensible
statement is a range:

> Of the 294 approvals clearing both gates: **134 follow the source's order, 8
> diverge from it, and 152 have a source too short for order to be a property.**
> So between **8 and 160** are free of all three known defects, and the order metric
> is the least robust number in this document.

## What this does not say

`clear` is not shippable. F1 applies to all 384 approvals and no free gate reaches
it. The corpus dashboard also still reports only `verbatim_ratio` — the metric F3
refuted — because `verbatim_summary` was deliberately left alone to keep the blast
radius at zero; wiring `max_verbatim_run` into it is outstanding.

The gates were free and deterministic, so they were re-run rather than stored;
`data/{city}/reauthored.json` was gitignored and nothing wrote back to it. Both
`beats.json` files were untouched, as the brief describes.

The commands as they ran before the retirement (the scripts now live under
`_to_be_deleted/` and are not on the import path):

```
uv run python _to_be_deleted/scripts/reauthor_triage.py --city new_york
uv run python _to_be_deleted/scripts/reauthor_triage.py --city paris --show blocked-conflict
uv run python _to_be_deleted/scripts/reauthor_triage_calibrate.py --city paris
```
