# Scope — the corpus is copied, and the fix is in the extractor

## The finding

Measured with the longest shared word run outside an attributed quotation, which is the
measure that survived adversarial review:

| | beats citing a source | share an 8+ word run | median run |
|---|---|---|---|
| Paris | 1,425 | **1,124 (78%)** | 17 words |
| New York | 2,005 | **1,949 (97%)** | 24 words |
| London | 561 | **552 (98%)** | 22 words |

**1,336 beats carry a 30+ word verbatim run.** In roughly a third of Paris and New York
bodies the copied run is half or more of the entire beat; in London it is 99%.

A 24-word median is a copied sentence, not an overlapping phrase. For calibration,
clean-room bodies written from claims — which provably never saw their source — sit at
16% under the same measure, so this is the corpus and not the instrument.

The previously tracked backlog of 524 came from `verbatim_ratio`, which divides matched
shingles by body length and so scores a lifted clause inside a long body near zero. It
undercounted by about six times.

## The cause, which is one line and one absence

`.claude/commands/unified-beat-extract.md` instructs the extractor:

> "DO use the source text's own vivid language and narrative details."

And no extraction-time validator measures copying. `scripts/extract_validators.py` gates
fabrication, source span and length class; it has no verbatim check, and neither does
`scripts/audit_extraction.py` or `scripts/beats_io.py`. The extractor was told to copy
and nothing looked.

This is why the corpus is in this state, and it is why re-extracting today would
reproduce it exactly.

## The approach — re-extract, do not rewrite

Every beat names the chunk it came from (1,562 of 1,562 in Paris; 2,005 of 2,005 in New
York), and the sources are on disk.

| source | lifted beats | units to re-run |
|---|---|---|
| book chunks under `Books/` | 2,560 | **105 chunks** |
| pinned Wikipedia revisions under `data/{city}/wikipedia/` | 457 | ~57 revisions |
| orphaned (`legacy_ambiguous`, missing chunk) | 56 | not re-extractable |

So 3,017 of 3,073 lifted beats are reachable by re-running the extractor over text that
is already local, in about 162 runs rather than 3,073 rewrites.

Re-extraction rather than rewriting, for three reasons:

1. **It runs the whole engine.** The fabrication probe, the source-span gate, the
   enrichment fields, the atomic validator-gated commit. A rewrite replaces prose and
   leaves every other field as the copying extractor left it.
2. **It fixes both defects in one pass.** Copying and any imported context are decided by
   the same run.
3. **The loop already exists.** `/beat-wipe` removes a chunk's beats and its log entry
   precisely so the hard refuse in `/unified-beat-extract` can be satisfied, and
   `/pipeline-batch` drives chunks in parallel with resume state.

The clean-room re-author pipeline (`scripts/reauthor_cleanroom.py`) stops being the
remedy and becomes the fallback for the 56 orphans, which is the right size for it.

## The changes

**1. A verbatim gate at extraction.** `max_verbatim_run` and the attributed-quotation
exemption live in `scripts/corpus_report.py` with tests in
`tests/test_reauthor_triage.py`. Wire them into `extract_validators.validate_beat` so a
beat sharing 8+ consecutive words with its source outside a quotation is a hard error,
next to the existing fabrication verdict. One implementation, called from extraction.

**2. The prompt line.** Replace the instruction to reuse the source's language with the
distinction that matters: take the source's facts, specificity and named detail; write
the sentences. B3's preserve-verbatim rule for inline foreign phrases stays — a term of
art quoted with its gloss is not the defect, and the quotation exemption already carves
it out.

**3. The chunk report.** `audit_chunk` gains a copying section beside its fabrication
audit: run-length distribution and any beat over the block, so a chunk's copying is
visible per run rather than discovered a corpus later.

## The proof gate — one chunk before anything else

Wipe one chunk, re-extract it with the gate on, and measure the run distribution against
the same chunk's current beats. The gate changes extractor behaviour or it does not, and
one chunk is what it costs to find out. Nothing else in this scope proceeds until that
comparison is on paper.

A pass means the re-extracted chunk's runs fall below the block with no loss of beat
count or enrichment coverage. A fail means the prompt change is insufficient and the
design needs the gate to feed a retry, which is a different scope.

## Out of scope

- **London.** 98% lifted, no books on disk to re-extract from, already excluded from the
  re-author work. It is deleted rather than repaired, in its own change.
- **The 56 orphans.** They keep their copied bodies until the clean-room fallback runs on
  them, and they stay counted as copied until then.
- **General website ingestion.** Wikipedia is pinned and re-extractable; arbitrary web
  pages have no ingestion path today and adding one is separate work.
- **Whether re-extraction improves tour quality.** This scope restores traceability and
  removes copying. Whether the resulting beats make better tours is measured elsewhere.

## What is not yet decided

These need a human ruling before the batch runs, and each changes the work:

- Whether the gate refuses a beat outright or refuses and re-asks the extractor once.
- Whether a re-extracted chunk replaces its predecessor unconditionally, or only when it
  produces at least as many beats.
- Whether the fact-check verdicts already recorded on existing beats survive
  re-extraction or are re-earned.
- Whether the 56 orphans are worth the clean-room run at all, or are simply dropped.
