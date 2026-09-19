# Corpus comparison — the new pipeline against the legacy corpus, Upper East Side

**Measured 2026-09-19.** The deliverable of `2026-09-19-district-port-plan.md`. Scope: the 12
Upper East Side POIs the job-A re-run covers (sandbox `data-ingest/sandboxes/step2b`, job
`32d5c8de…`, $2.68), against `data/new_york/beats.json` at the SAME 12 POIs.

**The question this answers** (owner, 2026-09-19): is the new corpus trustworthy — free of
direct copies from the books — and how does it compare on coverage of POIs, density of beats
per POI, and diversity of lenses? It does NOT judge tour quality; the owner's position is that
the tour engine is already good and this work is the plumbing that feeds it.

## Headline: the copying is gone, measured two ways

Measured with the repo's OWN implementation, `scripts/verbatim.py` (`verbatim_ratio` and
`max_verbatim_run`, 8-word shingles), each beat's body against its own source chunk. Two different
questions, reported separately because they do not agree:

| Source | Beats | Any 8-word run copied | 10%+ of shingles shared | 70%+ (`VERBATIM_THRESHOLD`) |
|---|---|---|---|---|
| Big Onion | 27 | **27** | 21 | 0 |
| National Geographic | 37 | **37** | 28 | 0 |
| Frommer's | 24 | **24** | 18 | 0 |
| Moon | 5 | **5** | 5 | 3 |
| Lonely Planet (legacy) | 47 | 7 | 7 | 1 |
| Legacy TOTAL | 140 | **100** | 79 | 4 |
| **New pipeline (Lonely Planet chunk-07)** | **42** | **1** | **0** | **0** |

**100 of 140 legacy beats contain a verbatim 8-word run from their own source. The new corpus has
one.** That one is `a-mining-magnate-and-a-german-baroness`, whose run is the name string "Solomon
R Guggenheim, a New York mining magnate" — eight words, of which six are a person's name and a
city's; its shingle ratio is 0.000. What is established: the narration passed the P5 lift gate,
whose run length is SEVEN (`narrate.NARRATION_LIFT_GATE_RUN`), and the gate's proper-name masking
(`gates.lift`, `gates.is_proper_name`) is the only mechanism that explains an 8-word run surviving
a 7-word gate — mask "Solomon R Guggenheim" and "New York" and the residue is far under seven.
That is a reading of the code, NOT a gate trace: no test asserts it. Quoted here so nobody finds
it later and distrusts the rest.

**Read this as proof the gate ran end to end, not as a neutral corpus measurement.** The new side's
narration is produced under a gate that refuses 7-word runs (it fired 19 times in this run and the
narration was rewritten); the legacy side had no such gate. The comparison is between a gated and
an ungated process. The gate also compares against the claim's own span, while this metric compares
against the whole chunk, so the two are not the same test.

An earlier draft of this report used an ad-hoc shingle metric of my own and reported 86 (arithmetic
error; its own rows summed to 93) beats at a 10% threshold, and said the new corpus "carries none".
Both were wrong: the repo's implementation gives 79 at that threshold, and "none" is false under
the any-run reading. Use the table above.

Provenance, the second half of trust: every one of the new corpus's **191 claims carries a cited
span** into the source chunk (100%). The legacy side has 429 `key_claims` at these POIs, which are
plain strings with no per-claim source; provenance exists only per beat, as a `source_passage`.

## Coverage, density, lens diversity

| | Beats | POIs | Per POI | Distinct lenses | Lenses per POI |
|---|---|---|---|---|---|
| Legacy, all 5 books | 140 | 12 | 11.7 | 19 | 4.8 |
| Legacy, Lonely Planet chunk-07 only | 39 | 12 | 3.2 | 15 | 2.4 |
| New, the file | 42 | 12 | 3.5 | 17 | 4.8 |
| **New, published to the graph** | **32** | **11** | **2.9** | **17** | **4.6** |

Read the like-for-like row (same book, same chunk): **39 legacy against 42 new, +8%** — parity on
volume, with twice the lens diversity per POI (2.4 → 4.8). Note the lens rows are asymmetric by
convention: a legacy beat carries ONE `lens` string, a new beat a `lenses` list averaging 2.36.
The 5-book row measures coverage only; closing that gap means running more sources, not extracting
more per source.

Mean narration length: 56 words (new) against 47 (legacy Lonely Planet chunk-07).

## What published, and what did not

Published to the scratch graph at :7693 (`make ingest-publish-scratch CITY=new_york
DATA_ROOT=data-ingest/sandboxes/step2b`): **32 beats over 11 POIs**, 81 lens tags, 0 orphaned.
Verified by Cypher: Met 9, Guggenheim 7, Frick 4, Upper East Side 3, Neue Galerie 3, and six POIs
with 1 each.

The 10 beats the file holds that the graph does not, both by deliberate policy:
- **7 practicalities beats** — fenced at publish (owner ruling 2026-09-13): prices, hours and
  tickets wait for an engine channel that does not voice them in the bare present.
- **3 held beats** — `what-hung-on-the-ramps-in-1959`, `five-centuries-on-the-second-floor`,
  `the-mayor-s-house-that-was-a-country-retreat`, each held by the narration judge for a sentence
  the claims do not entail. A held story waits for a human, and is never served.

**Count coverage as the 11 POIs that have beats.** The graph also holds 402 POI nodes, seeded from
`poi-raw.json` by the publisher; those are not coverage.

## Caveats, stated rather than buried

- **Not ordering-representative.** These 42 beats carry free-prose `narrative_function` values, so
  the engine sorts them all last. The fix (commit `0784a7b`) binds future extractions only. Do not
  read beat order off this graph.
- **One chunk, one book.** The new side is a single Lonely Planet chunk; the 5-book legacy row is
  there for coverage context, not as a like-for-like.
- **Lift is measured against the beat's own chunk only.** A beat copying from a DIFFERENT source is
  not detected by this metric.
- **`physical_cues` and `pronunciation` are not exported** for new-shape beats (unjudged author
  enrichment, proof-chunk panel 2026-09-13), so the engine's synthesized openers lose that input.
- Reproduce: every figure here was produced by an UNCOMMITTED ad-hoc harness (the two beats files,
  `scripts/verbatim.py` under `uv run`, and Cypher against :7693) — there is no `make` target that
  re-derives them, so the next person cannot reproduce this report from the repo alone. A committed comparison script and its own target are worth adding when the second
  book lands and this has to be re-run.

## What this settles, and what it does not

**Settles:** the rebuild's core promise. The new pipeline produces beats that are not copied out of
the books, whose every claim is traceable to a span, at parity volume and better lens diversity than
the same book's legacy beats.

**Does not settle:** whether the merge deduplicates two books at one POI (no second book has been
ingested — P6 completed 0 merges here because the graph started empty), whether a tour built from
these beats is good, and coverage, which needs more sources per POI and is a throughput question —
the next step in the plan.
