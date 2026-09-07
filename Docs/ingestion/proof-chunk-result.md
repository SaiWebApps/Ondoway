# Proof chunk — the result

`Books/new_york/lonely-planet-new-york-city/chunk-07-upper-east-side.txt`, wiped and re-extracted through the gated extractor.

| | before | after |
|---|---|---|
| beats | 24 | **42** |
| **lifted (8+ word run)** | **24 (100%)** | **0 (0%)** |
| median run | 50 words | **4 words** |
| longest run | 151 words | **7 words** |
| median verbatim ratio | 0.71 | **0.00** |
| distinct POIs | 11 | 14 |
| distinct lenses | 11 | 15 |
| sensory-anchored | 18 | 29 |
| total words of prose | 1700 | 1985 |
| length classes | {'mid': 18, 'seasoning': 4, 'anchor': 2} | {'seasoning': 38, 'mid': 2, 'micro': 2} |

POIs gained: Carl Schurz Park, Central Park, Madison Avenue
POIs lost: none

## The chunk's own copying audit

```json
{
  "block_at": 8,
  "blocked": 0,
  "blocked_beats": [],
  "median_run": 4,
  "longest_run": 7,
  "runs_within_two_of_the_block": 8,
  "median_ratio": 0.0
}
```

## Verdict

**The gate changes extractor behaviour, and the corpus did not shrink.** Copying went
to zero — the longest run in the new chunk is 7 words, one under the block, against a
151-word paragraph before. Coverage went up rather than down: 42 beats where there
were 24, three POIs gained and none lost, more lenses, more sensory anchors, and 285
more words of prose.

The risk the scope named did not materialise here. Beat COUNT rose while beat LENGTH
fell — 38 of 42 land as `seasoning` where 18 of 24 were `mid`. That is the source-span
gate doing its job rather than the copying gate starving the output: a two-sentence
entry in a guidebook honestly yields a short beat, and the old corpus was reaching
`mid` by keeping the author's sentences. The same material, told in its own words,
breaks into more and smaller pieces.

## What the run cost, and what it caught

Eleven of 42 beats were refused on the first pass: five for copying, five for
declaring a length class the source span could not support, one for a `source_passage`
that did not match the chunk (an accent and a dropped sentence). Every copying refusal
was a list of names in the book's order or a fixed phrase — the honest fixes were to
reorder the list, or to quote and attribute.

One rewrite introduced a NEW violation: changing "fifty" to "50" to satisfy the
fabrication probe created a 9-word run with the source. The gate caught its own
repair, which is the argument for running it per beat rather than once at the end.

The fabrication probe flagged eight beats, seven of them artefacts of paraphrase
("Second World War" for "WWII", spelled-out numerals). The eighth was real: the body
called Gilbert Stuart American and the book never does. That one was dropped.
