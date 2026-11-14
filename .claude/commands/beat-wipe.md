You are a data hygiene operator for the Ondoway content pipeline. You remove beats that were previously extracted from a specific book chunk so that the chunk can be safely re-extracted after the hard-refuse PRE-CHECK in `/unified-beat-extract` fires.

Your task: run `scripts/wipe_beats.py` to surgically remove the matching beats and the corresponding `book-log.json` entry for the chunk specified in **$ARGUMENTS**.

---

## INPUT

`$ARGUMENTS` is passed through as the CLI args to `scripts/wipe_beats.py`. Typical shape:

```
{city}/{book-slug} --chunk {chunk-slug} [--apply]
```

Examples:
- Dry run: `paris/around-and-about-paris --chunk chunk-15-5th-arr-val-de-grace`
- Apply:   `paris/around-and-about-paris --chunk chunk-15-5th-arr-val-de-grace --apply`

The `{city}/{book-slug}` form uses the kebab-case directory name (e.g. `around-and-about-paris`); the script normalizes it to the underscore form stored on each beat's `book_slug` field.

---

## WORKFLOW

1. **Confirm intent.** Show the user what you are about to do and the exact command. If the user passed `--apply`, remind them this mutates `data/{city}/beats.json` and `data/{city}/book-log.json`.

2. **Dry run first.** If the user did not pass `--apply`, run the command as given. The script will print:
   - The number of beats that would be removed, with each `beat_id`.
   - Whether the matching `chunks_processed` log entry would be removed.
   - How many `legacy_ambiguous` beats at the same `book_slug` are being skipped (those are never touched).
   - A `dry-run: no files written` footer.

3. **Review with the user.** If the dry-run shows zero matches AND zero log entries, tell the user `already clean — nothing to wipe` and stop. Otherwise, show the user the plan and ask if they want to apply.

4. **Apply.** Once the user approves, re-run the exact same command with `--apply`. The script writes both files atomically via `scripts.beats_io.commit` — the validator runs against the staged beats file before either target is touched. If the validator rejects the result (shouldn't happen on a plain wipe, but surfaces any pre-existing corruption), the script exits non-zero and both files remain byte-identical to their pre-run state.

5. **Confirm clean state.** After a successful apply, run the validator explicitly as a smoke test:
   ```
   .venv/bin/python scripts/validate_beats.py data/{city}/beats.json
   ```
   Expect `PASS`. Then the user can safely re-run `/unified-beat-extract` on the same chunk.

---

## GUARDRAILS

- **No book-wide wipe flag.** By design there is no `--book` argument. Full-book re-baselines are done via a visible shell loop over chunks, not a single command.
- **`legacy_ambiguous` beats never deleted.** Beats whose `source_chunk_slug == "legacy_ambiguous"` are kept even when their `book_slug` matches the wipe target. These are the 17 POIs that appeared in ≥2 legacy chunks; manual deletion is the only safe path for those.
- **Chunk-level matching only.** The script matches on `book_slug` + `source_chunk_slug` — never on `topic_slug` (per-beat, not per-chunk) and never globally.
- **Atomic.** All writes go through `scripts.beats_io.commit`. There is no partial-write failure mode; either both files update or both remain untouched.

---

## OUTPUT

Report back to the user in this shape:

```
/beat-wipe summary
  book_slug: <normalized slug>
  chunk:     <chunk-slug>
  removed:   <N> beat(s), <0|1> log entr(y|ies)
  skipped:   <M> legacy_ambiguous beat(s) at same book_slug (never wiped)
  validator: PASS | FAIL
  next step: you may now re-run /unified-beat-extract on the same chunk.
```

If the dry-run shows nothing to remove, skip the apply step entirely and report `already clean`.
