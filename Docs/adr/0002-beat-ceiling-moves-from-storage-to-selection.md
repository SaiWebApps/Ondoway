# ADR-0002 — the per-POI beat ceiling moves from storage to selection

**Status:** accepted (owner ruling, 2026-09-19). This is the sprint decision entry
NORTHSTAR requires before a locked architectural commitment is re-opened.

## The locked commitment being changed

`specs/NORTHSTAR.md` (April 2026) locks: *"Content primitive: NarrativeBeat — versioned,
lensed, gravity-scored (1–5). Max 1 beat per `(lens, sub_location)` tuple per POI."*

That file does **not** exist on `corpus-workbench` (deleted in `c0f77e1`); it is live on the
branch the app ships from. **Owed at merge:** amend the commitment there to point at this ADR.

## What changed under it

The ingestion rebuild's unit is a per-place STORY with a judged claim set, not a per-lens
slot. The job-A re-run (2026-09-19, sandbox `step2b`) produced ten Guggenheim beats, six of
them tagged `modern_design`, separated only by free-text sub-locations the model invented
("Fifth Ave sidewalk opposite the museum", "admissions desk and The Wright", one `None`).
The tuple therefore no longer binds anything, and enforcing it as written would discard
genuinely distinct stories (the founding, the critics, the tower, the collection, visiting).

The owner's goal is the opposite of a ceiling: **every story a source tells about a POI
becomes a beat, so that no two tours of the same place are the same.**

## Decision

1. **Storage has no per-`(lens, sub_location)` ceiling.** Every judged story is stored.
2. **The ceiling moves to selection** — how many beats a tourist hears at one stop is the
   tour engine's call, per visit, which is where variety is actually produced.
3. **`sub_location` becomes a controlled vocabulary per POI** (e.g. facade, rotunda, ramp,
   tower galleries, entrance), not free prose. It is exported and feeds the trigger, so
   "Fifth Ave sidewalk opposite the museum" is not a value the app can act on.
4. **A duplicate story is preferred over a missing one.** The merge folds what it is sure of,
   queues the doubtful for human review, and never blocks a publish; duplicates are measured
   after publishing, not argued about in advance.

## Consequences

- The corpus is judged on coverage of POIs, beats per POI and lens diversity across those
  beats — not on a per-lens slot being filled exactly once.
- Selection must cap per-stop beats; until it does, a sparse scratch graph is the safe place
  to look at output.
- Cross-book duplicate rate becomes a reported number (`make claim-conflicts`).

## Also stale in NORTHSTAR, found while checking this (not decided here)

- **Beat-level gravity (1–5) and "duration = gravity × 60s"**: `gravity` appears nowhere in
  `src/tour/contract.py` or `src/tour/beat_select.py`; the shipped engine works from POI tier,
  and the new record carries `duration_sec`.
- **"Launch city: Paris"** and **"Active Build Target: Editorial Workbench upload slice"** are
  both superseded (New York, one district; the ingestion rebuild).
