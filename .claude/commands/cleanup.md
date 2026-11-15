---
description: Remove a thing completely — enumerate every reference, registration, and description of it, sweep until two full passes find nothing, and prove the absence with a receipt. Invoke as `/cleanup <what was, or is being, removed>` — a feature name, a file list, a commit range, or "the deletion in this diff". To find what needs cleaning in the first place, use `/code-cleanup`.
argument-hint: "<the thing being removed>"
---

You are removing `$ARGUMENTS` — not just its files: every reference,
registration, and description of it anywhere in the repository. A removal is
finished when a stranger reading the repo cannot tell the thing ever existed,
except in git history and `.claude/LEARNINGS.md`. A reference left behind is
not clutter; it is a minefield — a doc that teaches a dead command, a test
silently measuring the wrong thing, an agent prompt instructing the use of
deleted machinery.

## Step 1 — the removal manifest, before anything is deleted

Write the thing's full surface as a list in
`.claude/runs/{YYYY-MM-DD}-cleanup-{slug}/manifest.md`. If the deletion
already happened, reconstruct the list from `git show <commit> --stat` and
the deleted files' contents in history. Nothing sweeps well that was never
listed.

1. **Files** to delete (or already deleted).
2. **Symbols** those files define — functions, classes, constants, CLI
   subcommands. For each: `codegraph callers <symbol>` and
   `codegraph impact <symbol>`; the caller list IS the work list.
3. **String identities** — the names prose and strings know it by: file
   basenames; make targets it provided; commands and flags; env vars; ports;
   API routes; settings keys; DB tables and event kinds; and its concept
   names (the words a sentence would use for it — "the engine",
   "the advisor gate").

## Step 2 — the registries, a fixed checklist every time

These places register things and never appear in a symbol graph. Check every
row against the manifest and record hit-or-clean per row — an unchecked row
is an unswept row:

- `Makefile` — recipes, the `.PHONY` list, AND comments
- `.gitignore` — negations (`!path`), ignore rules, comments
- `.claude/settings.json` — and tell the human to check their machine-local
  `settings.local.json`, which no pull can fix
- `pyproject.toml` — testpaths, markers, dependencies
- package `__init__.py` / `__all__` exports; registry dicts (`HANDLERS`-style)
- `README.md` and `Docs/**` — commands taught, paths cited, claims made
- `.claude/commands|agents|rules|workflows` — prompts that instruct using it
- CI and deploy configs — `.github/`, `render.yaml`, `docker-compose.yml`

## Step 3 — sweep loops, until two consecutive passes are dry

For every manifest identity, search the WHOLE tracked repo — every file
type, case-insensitive (`git grep -il`). Every hit gets exactly one of two
dispositions, written down:

- **removed** — deleted, or repointed to what exists now;
- **kept, with the reason** — the only allowed keeps are git history,
  `.claude/LEARNINGS.md`, and a line whose job is to narrate the removal
  itself ("X is deleted; Y replaced it") where a reader needs that fact.

Findings feed the manifest: a dead reference often names a second identity
you didn't list. Loop. The sweep ends only when a FULL pass over the whole
manifest yields zero unhandled hits — and then one more full pass agrees.
Two dry passes end it; one never does.

## Step 4 — the traps a name-grep cannot see

Check each deliberately; these are the ways a removal stays green while
lying:

1. **String-anchored code.** Search each identity inside string literals fed
   to `split`, `partition`, `startswith`, regexes, and slicing.
   `str.split` on a vanished needle silently returns the whole input instead
   of failing — a test keeps passing while measuring something else.
   Re-anchor to something live, and add an assertion that goes RED if the
   new anchor ever vanishes too.
2. **Changed contracts.** If the removal changes a surviving function's
   signature or calling convention, enumerate that symbol's callers across
   the WHOLE repo — `src/`, `scripts/`, `tests/`, `mobile/`, `web/` —
   never just the file being edited.
3. **Fail-open consumers.** Code that quietly no-ops when the thing is
   absent: `.get(...)` with a default, matchers that match nothing,
   `try/except ImportError`. The absence of an error is not evidence of a
   clean removal — read each consumer and decide it.
4. **Machine-local residue.** State the thing wrote outside git: caches,
   state directories, logs, databases, local settings. Delete yours; write
   the list into the manifest for every other machine, because a pull
   removes none of it.

## Step 5 — prove the absence, with a receipt

- `make lint` — the process-file lint refuses tracked references to
  nonexistent paths.
- The targeted test suite of every file the sweep touched.
- The final table, pasted into the manifest: every manifest identity → hit
  count outside the allowed keeps, and every count is 0. That table is the
  removal's receipt; a removal without one is a claim.
- If the removal deserves memory, ONE entry in `.claude/LEARNINGS.md` — the
  constraint it taught, never the story.

## What this cannot make foolproof, said plainly

A dependency that never uses any of the thing's names — behavior that
silently relied on it existing — no grep can find. Steps 4.2 and 4.3 are the
mitigation, and after a large removal the full bar (`make audit`) is the
backstop. Report "swept clean, receipt attached", never "provably gone".
