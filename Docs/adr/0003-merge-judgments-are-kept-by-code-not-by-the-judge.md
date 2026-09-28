# ADR-0003 — a judgment a merge would drop is kept by code, not by the merge judge

**Status:** accepted (owner, grilling session 2026-09-25); decisions 3, 4 and 5 SUPERSEDED by
ADR-0004 (2026-09-27): the twin told the fact twice live, and a judgment now belongs to its source. Amends the owner ruling of
2026-09-22 ("a new claim stays with its story") for one case only, and moves the owner ruling
of 2026-09-24 (a claim that adds a judgment "is not `same`") from the judge's verdict to the
merge's outcome: the judge now answers `same` on the facts, and code makes the outcome
`adds_judgment`, never a `same` fold.

## Context

A source's judgment ("seminal", "iconic", "huge") is part of what a claim states
(CONTEXT.md, **Judgment**; owner ruling 2026-09-24). A `same` fold keeps the existing
claim's text and only appends the new source, so a new claim that adds a judgment loses
it from everything a listener hears. Job B folded "a seminal work by El Greco" this way,
twice.

The first fix was a prompt rule: a claim that adds a judgment is `new`, never `same` or
`covered`, with a worked example. The registered live calibration (b4a741d, 2026-09-24)
FAILED on it: Sonnet 5 folded "…the museum's iconic building finished." `same`, and its
own restatement of the claim silently dropped "iconic" — with the rule and the example
in the prompt it was sent.

## Decision

1. **The judge lists; code decides.** Every claim in a merge answer carries `judgments`:
   the new claim's judgment words, verbatim. The verdict is judged on facts alone. A
   listed word missing from the new claim's text is a malformed answer (one re-ask).
2. **Carried means the same word.** A judgment counts as carried only when the same word
   (case-insensitive, whole word) is in the matched claim's text — for `covered`, in any
   named claim. A differently worded judgment ("beloved" / "much-loved") is a different
   one; the cost is a redundant claim in one beat. The check reads the word, not what it
   qualifies: "the famous Guggenheim" against "Wright, America's most famous architect, …"
   counts as carried, and that judgment is lost. Accepted, unmeasured.
3. **A dropped judgment becomes `adds_judgment`.** A `same` whose listed judgment the
   matched claim lacks, or a `covered` whose named claims all sit in one beat and lack it,
   writes the new claim beside its match in that beat, and the beat is re-narrated. This
   is the amendment: a `new` claim would stay with its story and the same fact would be
   told twice at one place. A `covered` whose named claims span beats stays `new`, logged.
4. **A twin is only ever written beside a RESOLVED match.** A `same` onto a superseded or
   contested claim keeps the old fold (a source for that claim) and `judgment_on_unresolved`
   names the word: a resolved twin there would narrate an outdated or disputed value (skeptic
   panel, 2026-09-25).
5. **A contested value takes no twin; a superseded one needs none.** The model requires a contested claim to carry
   two differing stated values, and a twin copying the other book's source would credit
   the judgment to a book that never made it. The contest is recorded as before and
   `judgment_on_contested` names the word; it waits in the new source's span until a
   person resolves the contest. Nothing contested is narrated before that. A new claim
   that comes out `superseded` is already written with its own text, judgment included; it
   is not narrated because its fact is superseded, not because the word was dropped.

## Alternatives rejected

- **The prompt rule** — measured to fail (above).
- **A lexicon of judgment words, or a word diff** — the lexicon is never complete; a
  word diff turns every true paraphrase `new` (the `same_fact` plant shares almost no
  words with its match).
- **Judgments tagged at P1, on the Claim record** — durable, but every existing claim
  is untagged and needs a re-extraction.
- **Re-ask on a dropped judgment** — the re-ask would teach the judge to stop listing
  the word, the one leak code cannot see.

## Consequences

- The judge can still leave a judgment off its list; code cannot see that. Calibration
  measures detection separately: the `adds_a_judgment` and `covered_adds_a_judgment`
  plants pass only when the planted word itself is among the words code found dropped
  and the claim lands beside its match.
- A factual word the judge wrongly lists as a judgment ("1938") makes the claim a twin in
  the matched beat rather than a claim of its own story: told, never lost. `adds_a_date`
  would show it as a WRONG FOLD.
- A beat can hold two claims stating one fact. The narrator must tell it once, with the
  judgment; the `told_once` plant measures that live.
- **Open (skeptic panel, 2026-09-25), owed a ruling before the P6 gate opens** (the first real
  P6 run writes twins into the corpus): twins are not
  linked. A later book whose conflict names one twin supersedes or contests that one alone; the
  other stays resolved and keeps narrating the old value. A later restatement can also tie the
  signature between the twins and hold its story for review (a growing queue, never a loss).
- **Out of scope, recorded:** when an OLD claim that carries a judgment is superseded or
  contested, its judgment leaves narration with it. This ADR keeps only the new claim's.
