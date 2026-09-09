---
status: accepted
date: 2026-09-08
---

# A beat is a per-place story whose payload is its claim set

The corpus was copied from its guidebooks: 72% of Paris beats and 97% of New York beats
shared an eight-word run with their source outside an attributed quotation. Every fix
tried in the week before this decision held one thing fixed, that a beat is listener-ready
prose authored from one passage of one book, and attacked it from a different side:
rewriting the prose kept the source's order (91% of rewrites); rewriting from shuffled
claims invented details (45% of a sample); re-extracting behind an eight-word gate
produced same-order paraphrases with provenance leaks ("the book says", 5 of 42). We
decided that the beat's identity is (city, place, story), bounded by one narrative arc,
that its payload is a complete claim set in which every claim carries its own sources,
spans, temporal kind and status, and that the narration is derived from the claims with
every sentence traceable to one. The book leaves the beat's identity and lives on each
claim, so a second source's telling merges its new claims into the existing story
instead of creating a sibling.

## Considered options

- **Prose primary, harden the copying gate** (the `corpus-workbench` branch as of
  2026-09-07). Cheapest, and its proof chunk reached zero lifts. Rejected because the
  extractor remained a re-teller of one passage: 28 of 28 multi-sentence beats followed
  the source's order, and the gate's "quote and attribute" escape taught it to attribute
  to the guidebook. Its gate and prompt survive as checks on the derived narration.
- **Claims only, no prose on the beat.** Rejected on measured evidence: quality
  certification hard-requires a narration per evidence record, the faithfulness checker
  recorded a live failure (2026-07-02) when entailing against claims alone, and the
  clean-room re-author was this architecture, run on 449 beats, whose invented and
  omitted details no free check could localise. Narration stays, as a derived field.
- **Sentence-level atoms.** Rejected by the owner from experience: sentences without
  their story were unusable for tour building and made duplicates undetectable.

## Consequences

- Deduplication and enrichment become claim-set operations at one place, decided by a
  judge that is not the model that wrote the claims; prose shingling is retired.
- Status (contested, resolved, superseded) lives on the claim. A story ships whenever its
  narration derives from resolved claims only; nothing is blocked at the beat level.
- Lenses become a list on the story and leave the identity tuple.
- The existing corpus cannot be migrated into this shape without inheriting its per-book
  story boundaries, so it is re-extracted from the chunks on disk. The old beats,
  London, and the 56 untraceable orphans are quarantined under a `_to_be_deleted/`
  directory until the new corpus has served tours, then deleted. The 1,835 existing
  fact-check verdicts are discarded with them.
- The publisher must converge the graph on the files (withdraw what the file no longer
  names) before the new corpus is published, or the old beats stay live beside it.
