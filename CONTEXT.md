# Ondoway Corpus

The content that GPS-triggered audio tours are built from: places in a city, and the
stories told about them, extracted from rights-cleared sources. This glossary is the
language of the ingestion side; the tour engine consumes what it defines.

## Language

**Beat**:
One story about one place, carried as a complete claim set plus a narration derived
from it. The unit of extraction, deduplication and tour selection.
_Avoid_: excerpt, snippet, passage, fact (a beat is never a single fact)

**Story**:
A self-contained episode a listener could follow on its own: the who, what, when and
why of one thing that happened at, or is true of, a place.
_Avoid_: anecdote (too narrow), entry (that is the guidebook's unit, not ours)

**Claim**:
One atomic factual statement inside a beat, self-contained, traceable to a source
span. A relation the source states (X caused Y, X rejected Y's offer) is one claim
naming both sides, never two.
_Avoid_: key claim, fact, detail

**Stated value**:
What one source says a claim's value is, kept with that source even after the claim is
resolved differently. A claim with two stated values that differ is contested.
_Avoid_: original, before

**Resolved value**:
The value the corpus uses for a claim: corroborated by agreeing sources, established by
independent sources under the two-source rule, or decided by a person. Only resolved
claims reach a narration or the tour engine.
_Avoid_: correction, fix, override

**As-of date**:
When a source's knowledge was current: a book's publication year, a Wikipedia
revision's date. Required on every source; every claim inherits it.
_Avoid_: retrieved at (that is when we fetched it, not when it was true)

**Event claim**:
A claim about something that happened at a time and cannot change afterwards. Two
event claims that disagree are contested; a newer source is never right by default.

**State claim**:
A claim that is true as of its source's date and can stop being true: tallest, open,
houses, closed since. Disagreeing state claims resolve to the newer source. When the
kind is unclear, a claim is a state claim.

**Belief claim**:
What was held to be so as of its source's date. A newer source may supersede it; the
superseded belief stays as a claim of its own, dated, and is not contested.

**Supersedes**:
The merge outcome in which a newer source's event or state replaces an older belief.
Distinct from a contest: both claims stay, both may be voiced.

**Staleness pass**:
The fact-check job under trusted sources: has this changed. State claims are rechecked
against a current source, belief claims for supersession, event claims only when
contested.
_Avoid_: fact-check (the old name for re-verifying everything)

**Contested claim**:
A claim whose stated values disagree, within a place or across places in one city, and
which has no resolved value yet. It is not voiced; the rest of its story still ships.
_Avoid_: disputed beat (status lives on the claim, never the beat)

**Claim set**:
Every claim the sources state about a beat's story. It is the beat's payload: what
gets deduplicated, extended by a second source, and checked for accuracy.
_Avoid_: key_claims (the field name; a summary subset today)

**Narration**:
The prose a listener may hear, derived from a beat's claim set in the corpus's own
words. Every sentence traces to a claim; framing, openers and transitions belong to
the tour engine, never to a beat. Never the source's sentences.
_Avoid_: script body (the field name), body, text, prose

**Structural beat**:
A beat that stages, moves or digresses rather than tells: stop orientation, transit,
sidebar. Carries claims and sources like any beat but is not bound by the one-arc rule.
_Avoid_: guidance, tip, directions

**Source**:
A rights-cleared text the corpus extracts from: a book or a website page. Every source
has an as-of date and a rights basis recorded when it is dropped.
_Avoid_: book (only one kind of source), reference

**Chunk**:
The unit of extraction input. For a book, one structurally bounded section produced
once at prep time, with its siblings and a manifest; for a website, the page itself,
split mechanically at headings only when too long to read in one pass.
_Avoid_: section, page (a book page is not a chunk)

**Source passage**:
The verbatim span of a source that grounds a beat. Provenance only; it is never
spoken and never product text.
_Avoid_: quote, excerpt

**Lift**:
A run of eight or more consecutive words a narration shares with its source outside
an attributed quotation. The corpus's operational test for copying, not a legal one.
_Avoid_: verbatim ratio (a report figure, never a gate), plagiarism

**Attributed quotation**:
Words in a narration credited to the person who originally said or wrote them. The
only exemption from the lift test. Crediting the guidebook is not attribution.
_Avoid_: citation

**Provenance leak**:
A narration that reveals it was read from a book ("the book says", "described here
as", "Lonely Planet calls it"). A defect: the listener is at the place, not in the
book.
