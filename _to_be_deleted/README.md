# `_to_be_deleted/`

Quarantine, not a trash can. Everything here is retired by the ingestion rebuild
(ADR-0001: `Docs/adr/0001-beat-is-a-per-place-story-with-a-claim-set.md`) but kept —
moved with `git mv` rather than deleted — so any one piece is restorable with a
single command until the slice that removes this directory outright.

## What is here, and why

- **The Lane B re-author scripts** (`scripts/reauthor_*.py`, 8 files) — the prose
  rewrite pass ADR-0001 rejected: 91% of its rewrites kept the source's sentence
  order. Superseded by the claim-set model; no other code imports them.
- **Their tests** (`tests/test_reauthor_*.py`, 8 files) — moved with their scripts so
  `pyproject.toml`'s `testpaths = ["tests"]` no longer collects them.
- **`frontend/rewrites.html`** — the review page served in front of the re-author
  pipeline. Its API routes (`/api/reauthored`) are removed from `src/server.py`;
  this page has nothing left to call.
- **`data/london/`** (150 tracked files: `poi-raw.json`, `beats.json`,
  `areas.json`, `within_edges.json`, `book-log.json`, and the Wikipedia source
  chunks) plus **`data/{city}/orphans.json`** — London was an onboarding proof,
  never a launch city (`src/cities.json` keeps the `london` key; only the
  corpus data moves). `orphans.json` holds the `legacy_ambiguous` beats that
  carry no source chunk — 137 for Paris — quarantined here because the old
  corpus cannot be migrated into the new claim-set shape.

## Restoring one piece

Every path here mirrors its original location. Put anything back with one command:

```
git mv _to_be_deleted/<path> <path>
```

For example: `git mv _to_be_deleted/frontend/rewrites.html frontend/rewrites.html`,
or `git mv _to_be_deleted/scripts/reauthor_run.py scripts/reauthor_run.py`.

## `orphans.json` is force-tracked

`_to_be_deleted/data/` is gitignored (it is otherwise machine-local, paid-model
output regenerable from source), so `_to_be_deleted/data/{city}/orphans.json` is
force-added (`git add -f`) to keep it in the repo despite that rule. Its beats
stay live in `data/{city}/beats.json` for parity with 7687/Aura — they are not
removed from `beats.json` until **slice 10**'s graph swap.

## When this directory goes away

**Slice 11** deletes `_to_be_deleted/` outright, along with the `beats.legacy.json`
copies and every `*.bak-*` file, once the new corpus has published to the cloud
graph and served tours for a week without a rollback.
