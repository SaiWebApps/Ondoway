# ADR-0004 — a judgment belongs to the source that makes it; a fact stays one claim

**Status:** accepted (owner, grilling session 2026-09-25/27). Supersedes ADR-0003's
decisions 3, 4 and 5 (the `adds_judgment` twin and where it is written). ADR-0003's
decisions 1 and 2 stand: the merge judge lists judgment words verbatim and decides on the
facts alone, and a word is carried only in the same word.

## Context

ADR-0003 kept a judgment the merge would drop by writing the new claim BESIDE its match,
as a twin. The registered live calibration of fd00c13 (2026-09-25) passed every merge class
(the judge listed "iconic" and "beloved"; code caught both) and failed `told_once`: given
a claim and its twin, the narrator told the fact twice ("Construction here was finally
completed in 1959. This acclaimed building reached completion in that year."). The skeptic
panel had already found the twin's other costs: two claims for one fact have no owner, so
a later conflict naming one leaves the other narrating the old value (F2), and a later
restatement can tie the signature between them and hold its story (F4).

The corpus already has the right shape. A second source stating the same fact is appended
to ONE claim, and each source keeps what it states (its stated value). The twin was the
only departure from that.

## Decision

1. **One claim per fact.** A `same` answer appends the new source to the matched claim, as
   it always has. The `adds_judgment` outcome is deleted.
2. **A judgment belongs to its source.** `Source.judgments` holds the source's judgment
   words, verbatim, beside its `stated_value`; each must be a whole word of the source's
   span. The first source's judgment stays in the claim text, as P1 writes it (owner ruling
   2026-09-24, measured); later sources' extra words live on their sources.
3. **A new judgment re-narrates the beat.** A `same` onto a resolved claim that brings a
   word the claim text and its other sources do not carry puts the beat in `rerun`.
4. **The narrator and the narration judge see a judgment on its fact's line:**
   `- <claim text> (Also called: iconic)`. P4 uses it where the fact is told, in the house
   voice, never naming who made it; when a claim's judgments disagree, the narration says
   opinion is divided and never picks one (unmeasured; a calibration plant is deferred).
   The narration hash covers the rendered line, so a claim with no judgments hashes exactly
   as before and a new judgment makes the narration stale.
5. **A covered claim carrying a judgment its named claims lack stays `new`**, with its own
   story (an event names the word). Absorbing it would lose the word, and attaching its
   source to a named claim would claim the source states that claim in full. The cost is a
   duplicate fact across beats, which ADR-0002 prefers to a missing one.
6. **Every source the merge writes carries its judgments**, wherever it lands: on a
   contested or superseded claim the word is recorded and waits there with its source,
   and nothing unresolved is narrated.

## Consequences

- F2 and F4 disappear: no twins are written.
- A superseded claim's judgments leave narration with it (ADR-0003's F5), still out of scope.
- The carried check still reads the word, not what it qualifies (ADR-0003's F3).
- A listed word that is a whole word of the new claim's text but not of its span (P1
  inflected it) is not recorded, since a source's judgments are verbatim; the merge logs it.
