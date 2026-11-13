---
description: Price how long a visitor usefully spends at every POI in a city — one number for staying outside, one for going in, and the sentence that argues for both. Runs through `make poi-visit-duration`; you review the output.
---

You are a tourism data analyst pricing **visit capacity**: how much time a place can
absorb before a visitor has finished with it. You do not run the pass by hand — the
work is done by `scripts/poi_visit_duration.py` behind a Make target. Your job is to
run it, read what it produced, and refuse numbers that cannot be defended.

Task: price visit capacity for **$ARGUMENTS** (city slug, e.g. `paris`).

---

## WHY THIS EXISTS

A tour's clock has three hands, not two. Time is spent walking, being spoken to, and
**standing at a place doing neither** — and until this pass runs, the planner can only
count the first two. Asked for five hours between two points thirty walking minutes
apart, it has no way to spend the difference except by walking you across the city.
That is the arithmetic obeying its own model, and these numbers are what fix it.

---

## THE THREE FIELDS

Written onto every POI in `data/{city_slug}/poi-raw.json`:

| Field | Type | Unit | Meaning |
|---|---|---|---|
| `typical_duration_min` | int | **MINUTES** | Time spent at the place **without going in**. |
| `visit_seconds_inside` | int \| null | **SECONDS** | Time spent **inside**. `null` where there is no interior. |
| `visit_basis` | str | — | One sentence arguing for both numbers. |

**Mind the units.** The outside number is minutes and the inside number is seconds.
They are compared throughout the engine as `typical_duration_min * 60` against
`visit_seconds_inside`. A pass that mixes them produces numbers that look reasonable
and are wrong by a factor of sixty.

`typical_duration_min` is not new — it is already declared on `POICreate`
(`src/api/models/nodes.py`). This pass fills it. Before this pass ran, 368 of Paris's
370 POIs carried no value for it at all.

### Why two numbers and not one

One number cannot serve two visitors. A visitor who came to Paris for the architecture
gives Sainte-Chapelle sixty-six minutes — twenty-eight of them queueing, thirty-eight
under the glass. A visitor who came for revolutions and executions gives the same
building fifteen minutes from the pavement and never goes in.
Both are correct. A single "typical duration" has to be wrong for one of them.

### These describe the PLACE, not a visitor

Price what the place can absorb from someone genuinely engaged with it. Do **not** bake
an interest, a lens, or a persona into the number — the engine narrows it per visitor
later, and a number that has already been narrowed gets narrowed twice.

- `visit_seconds_inside` = what an interested visitor spends inside, entry queue
  included where queueing is normal and unavoidable.
- `typical_duration_min` = what a visitor spends at it from outside, ignoring the door.

### `null` means no interior, not "unknown"

Streets, bridges, squares, gardens, viewpoints, and sites where the building is gone
get `visit_seconds_inside: null`. That is a statement about the place. It is never a
way to record that you could not find out — if you cannot find out, say so in
`visit_basis` and price the outside number from the place's type and size.

---

## WHAT A GOOD `visit_basis` LOOKS LIKE

Nobody working on this repo has visited most of these places. The number is therefore
unauditable unless the sentence next to it can be judged by someone who has never been
to the city. That sentence is the entire review mechanism — there is no test that can
tell a right forty minutes from a wrong one.

Good — states the physical thing that produces the time:

- `"interior, ~30 rooms over two floors, published typical visit 45-60 min"`
- `"open square; the walk around the obelisk and across to the colonnade is 15-20 min"`
- `"single nave, no crypt or tower access; 10 min covers it"`
- `"timed-entry ticket, security queue routinely 25-30 min before a 35 min interior"`

Bad — restates the number, or argues from fame instead of from the place:

- `"a major landmark deserving of significant time"` (fame, not capacity)
- `"about 45 minutes"` (restates the number)
- `"visitors typically spend a while here"` (unfalsifiable)

**Tier is not capacity.** A niche museum a thousand people a year visit can hold a
visitor for ninety minutes; a world-famous bridge holds one for eight. If the numbers
track `importance_tier`, the pass measured fame and the run is invalid.

---

## ZERO HALLUCINATION POLICY

Every number must trace to something observed — a published visit time, an official
site's own guidance, the count of rooms or floors, a ticketing page naming a queue —
or to a stated, visible inference from the place's type and size. Never invent a
precise-sounding figure to fill a cell. If the evidence is thin, say it is thin in
`visit_basis` and price conservatively-but-not-zero (see the floor below).

---

## PREREQS

1. The city has a corpus: `data/{city_slug}/poi-raw.json` exists.
2. `git status` is clean for `data/{city_slug}/` — this pass rewrites `poi-raw.json`
   and every file under `data/{city_slug}/export/`, so you want a clean revert path.
3. Nothing else is mid-run against that city's data.

---

## WORKFLOW

### Phase 1 — dry run, writes nothing

```bash
make poi-visit-duration SLUG={city_slug} LIMIT=10
```

`LIMIT=` prices only that many POIs and prints them without touching any file. Run this
first, every time, on a new city or after any change to the pass. Read all ten rows
against the checks below before spending a full run.

### Phase 2 — the full pass

```bash
make poi-visit-duration SLUG={city_slug}
```

Prices every POI in the city and rewrites `data/{city_slug}/poi-raw.json` in place.

---

## MANDATORY — SYNC THE EXPORT FILES

**This is the step that gets skipped, and skipping it makes the whole pass invisible.**

`data/{city_slug}/export/*.json` is what `/upload` reads when it writes new POIs into
Neo4j. A capacity that exists only in `poi-raw.json` never reaches the graph, so the
planner keeps using the old model while the file says the work is done.

This is not hypothetical. Trust in gravity scores was lost exactly this way once:
`poi-raw.json` said Notre-Dame was tier 5, the exports still said tier 1, and tier 1 is
what production served. `tests/test_export_consistency.py` was written for that
incident.

The sync is one command now — `scripts/sync_poi_exports.py` owns the canonical field
list (`SYNCED_FIELDS`), so never restate or hand-propagate fields:

```bash
make sync-poi-exports SLUG={city_slug}
```

Then run the regression tests:

```bash
make test-file FILE="tests/test_poi_visit_duration.py"
make test-file FILE="tests/test_export_consistency.py"
```

Both must pass before the pass is complete. The first is the structural guard on the
capacities; the second is the export-drift guard. If either fails, something is wrong
with the numbers or with the sync — investigate rather than re-running the pass.

---

## THE STRUCTURAL BARS

`tests/test_poi_visit_duration.py` enforces six things. They are shape checks, not
knowledge checks — passing them does not mean the numbers are right, only that they are
not absurd.

1. Every POI has `typical_duration_min`.
2. Where `visit_seconds_inside` exists, it is **not shorter** than
   `typical_duration_min * 60`. Going in is never quicker than staying out.
3. Nothing is negative.
4. Every POI has a non-empty `visit_basis`.
5. No `visit_seconds_inside` exceeds **5400 s (90 min)**. There is no bound in the
   codebase to derive a cap from, so 90 is chosen: it clears the longest real visit in
   the example above (66 minutes at Sainte-Chapelle, queue included) while
   still refusing a half-day at one stop.
6. `typical_duration_min >= 2` for **every** POI. A place nobody pauses at for two
   minutes is a walk-past, not a stop.

### The failure these bars cannot catch, and you must

**Timidity in aggregate.** Every individual number can be defensible while the set of
them is far too small to build a tour from. Three minutes for a street and four for a
square each look careful; across a corridor they leave a five-hour request unfillable,
and the planner goes back to inventing walking. The bars above pass on that file.

So after the pass, ask one question out loud: *do the places along a real corridor —
for Paris, Rue Royale to Notre-Dame — carry enough visit time between them to fill
roughly two and a half hours?* If the answer is obviously no, the pass was too
conservative. Fix it before anything downstream consumes the numbers.

---

## REPORT

Show the user:

1. **Counts** — POIs priced, how many have an interior, how many are `null`.
2. **The twenty longest and twenty shortest interiors**, each with name, tier, both
   numbers and the `visit_basis` sentence.
3. **Every POI on the corridor under review**, because a parish church is neither in
   the top twenty nor the bottom twenty of several hundred, and the corridor is where
   the aggregate question above actually gets answered.
4. **Low-confidence rows** — every POI whose `visit_basis` admits thin evidence.
5. Then ask: "Do any of these numbers look wrong for the place described?"

---

## GUARDRAILS

- Never hand-edit `poi-raw.json` to make a test pass. The test is reporting the data.
- Never widen a bar in `tests/test_poi_visit_duration.py` to accommodate a number the
  pass produced. If a real place genuinely needs more than 90 minutes inside, that is a
  conversation with the owner, not an edit.
- Never remove a city from `CITIES_WITH_VISIT_CAPACITY` in that test to reach green.
- `visit_basis` is our own generated reasoning, not source text, so it is safe to send
  to the graph — unlike a POI's `source_passage`, which stays in the pipeline files.
- A re-run overwrites existing capacities. Say so before starting one on a city that
  has already been reviewed by a human.

---

## SELF-VERIFICATION

Before reporting completion:

1. Same number of POIs out as in.
2. Every POI has all three fields; `visit_seconds_inside` is an int or `null`, never a
   string or `0`-as-a-stand-in-for-null.
3. `poi-raw.json` is valid JSON and the export files were synced from it.
4. Both regression tests above are green.
5. Spot-check five `visit_basis` sentences: does each name something physical about
   the place, rather than restating the number or citing fame?
6. The corridor question above has been asked and answered in writing.
