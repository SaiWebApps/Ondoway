# Tour engine design

This is the design of the tour engine in `src/tour/`. It describes how the engine turns a
walker's request into a walk and a script. Code comments cite its sections by number,
so the section numbers stay fixed.

The engine runs in the app and in the workbench. Both call the same code.

---

## 1. What the engine does

A request names a start, a length of time and, if the walker wants, a destination,
interests and a party. The engine answers with a route and a script.

The pipeline runs in this order:

1. **Density gate** — is there enough to visit near the start? (§3.7)
2. **Selection and routing** — which places to stop at, in what order, and for how long. (§3.2)
3. **Beat selection** — which stored facts ("beats") to tell at each stop, in what order. (§3.3)
4. **Stitching** — turn the beats into a sentence-by-sentence script, with short linking
   lines ("glue"). (§3.4, §3.5)
5. **Writing** — a writer model retells each stop as spoken narration, or the stop is told
   from a stored telling of its place. (§3.4, §3.8)
6. **Checks** — every sentence must trace to a beat or an allowed glue label. (§3.6)

| Module | Job |
|---|---|
| `contract.py` | The data shapes: the request, places, beats, the route, the script, the report. |
| `density.py` | The density gate. |
| `selection.py` | Loads the corpus snapshot and plans the route. |
| `routing.py`, `routing_client.py` | Walking time, the time budget, and street routing. |
| `visit_time.py` | How long a walker spends at a place. |
| `ordering.py` | The visiting order. |
| `beat_select.py`, `fixtures.py` | The beats at each stop, and hand-made walk orders inside buildings. |
| `generation.py`, `glue_client.py` | The stitched script and its glue lines. |
| `claim_dedup.py` | Removes facts the route already told. |
| `authoring.py`, `premium_tour.py`, `reflection.py` | The writer and the reflections on long walks. |
| `place_recipes.py`, `recipe_reuse.py`, `recipe_store.py`, `recipe_build.py` | Stored tellings of a place, written once and reused. |
| `validation.py`, `verify.py`, `compose_gate.py` | The checks on the script. |
| `placement.py` | Where on the ground each piece of audio plays. |
| `contingency.py` | Ready answers for a walker who runs late, runs early or skips a stop. |
| `quality_certification.py`, `certification_provider.py` | The separate fact and enjoyment review used to certify tours. |

---

## 2. Rules every tour keeps

- **Every sentence traces to a source.** A sentence cites a beat, or it carries one of the
  allowed glue labels in §3.5. Nothing else reaches the walker.
- **Glue adds no facts.** It may give directions, stage the walker, set the pace or close a
  stop. It never adds a claim, a new name or a date.
- **Interest dims a place; it never hides a landmark.** A place outside the walker's
  interests scores lower. A major landmark on the path is still at least mentioned.
- **The requested time is a ceiling.** A day longer than the request is refused. A day
  well short of the request is served and says so.
- **A stop lasts as long as the visit needs.** Its length is the longer of the time the
  walker spends there and the time its narration takes.
- **The walk is honest about what it cannot do.** A shut door, a thin area or a dark
  finish is said out loud, never hidden.

---

## 3. The engine

### 3.1 Input contract

`TourInput` in `contract.py` is the request. Every field except the first three is
optional. Each optional field defaults to the plain behaviour.

| Field | Meaning |
|---|---|
| `start` | Where the walk begins, as (lat, lng). |
| `duration_min` | How long the walker has, in minutes (1–600). |
| `city_slug` | The city. Every corpus query is scoped to it. |
| `end` | A destination, as (lat, lng). Cannot be combined with `round_trip`. |
| `round_trip` | The walk returns to the start. |
| `lenses` | The walker's interests, as lens slugs. Empty means no interest stated. |
| `theme_hint`, `start_label` | Words for the opening ("from your hotel near the Sorbonne"). |
| `start_datetime` | When the walk starts, local ISO time. Turns on opening hours, queue times and dusk. |
| `end_hardness` | `wall` (a hard deadline), `firm` (the default) or `open` (a rough wish). See §3.2. |
| `party` | `solo`, `couple`, `family`, `take_it_easy` or `with_luggage`. A preset that fills in the axes below. An axis the caller sets always wins. |
| `walking_pace` | Slows walking (2.0 is half speed). Never faster than the base pace. |
| `max_stop_minutes` | The longest the party will spend at any one stop (at least 5). |
| `max_leg_minutes` | The longest single walk between stops. |
| `rest_cadence_minutes` | How much walking may pass before a bench or toilet is seated. |
| `escape_radius_m` | Every stop must lie within this distance of the start. |
| `route_surface` | `any`, `no_stairs` or `step_free`. Passed to the street router. |
| `narration_register` | `solo`, `warm` or `family`. How the narration speaks. |
| `pinned_poi_ids` | Places the walker insists on. A pin is always seated. |
| `weather` | `dry` or `rain`, fetched by the caller. Rain halves the worth of open-air places. |
| `stop_density` | `fewer` (fewer, longer stops) or `more` (more, shorter stops). |
| `narration_density` | `less` or `more` talking per stop. |
| `avoid_queues` | Places with a peak queue of 10 minutes or more score lower; 20 minutes or more, they are left out. |
| `category_minus` | Kinds of place to leave out today (for example museums). |

`resolve_party_axes` expands a `party` preset into these axes. The preset table
(`_PARTY_AXES`) sits next to it.

### 3.2 Selection and routing

`select_route(input, snapshot)` in `selection.py` plans the route. It is a pure function
over a frozen snapshot of the city's places and beats.

**Time budget.** `route_planning_budget` in `routing.py` is the one home of the time
arithmetic.

- A route aims at the full requested time (nominal fraction 1.00). The repair works inside a
  band of 0.90–1.10 of the request, but the final route may not run more than one minute
  past the request itself (see "Final checks").
- 40% of the aimed time is for walking and 60% is for time at stops.
- `wall` caps the aim at 0.95 of the request, so the walker keeps spare minutes.
- `open` removes the lower bound, so a short honest day is never padded.

**Walking time.** A leg's time comes from the street router (Valhalla) when it answers,
and otherwise from straight-line distance × 1.35 at 3.0 km/h. A slow party divides the
speed. `route_surface` changes the router's costing, and the route is priced and reported
under the same costing.

**Reach.** The candidate area is a walking isochrone from the street router. When the
router is down, it is a circle: walking minutes × 3.0 km/h ÷ 1.35, halved for a round
trip. With a destination, a place qualifies only if walking start → place → destination
fits the walking budget. `escape_radius_m` limits the area further.

**Who can be a stop.** A place needs at least one active beat. Each place gets a
spotlight score: tier × lens relevance × an on-path factor. Lens relevance is 1.0 for a
direct lens match, 0.6 for a parent or child lens, and 0.25 for a miss (1.0 when no lens
was asked for). The score sets the place's band:

| Band | Score | Meaning |
|---|---|---|
| headline, full, short | ≥ 5.0, ≥ 4.0, ≥ 3.0 | A real stop. |
| vignette | ≥ 0.5 | One line as the walker passes. |
| silent | below 0.5 | Left out. A place of tier 4 or 5 is never silent. |

A real stop that would speak for under 90 seconds becomes a vignette, unless it is
tier 4 or 5. Opening hours remove a place that is closed for the whole visit; a closed
place with an outside worth seeing stays and is visited from outside. Places with
`category_minus` kinds are left out and the day says so.

**Spine.** `pick_spine_area` picks the area the walk is about. Each place within 800 m of
the start votes its tier for each of its areas. A neighbourhood, island or corridor beats
a district unless the district has twice its votes.

**Score.** `poi_score` ranks stops:

```
score = tier × log(1 + beat_count) × lens_relevance × area_alignment
        × role_multiplier × penalty × party_factor
```

- `area_alignment` is 1.0 inside the spine, 0.5 in an adjacent area, 0.2 elsewhere.
- `role_multiplier` is 1.0 for a stop, 0.7 for a setting, 0 for a walk-by-only place and
  0 for a bench or toilet.
- `penalty` is a per-place factor. Rain and the queue dial set it.
- `party_factor` is 1.25 for a place children can run in (family) or a place to sit and
  talk (couple, take it easy).

**Greedy.** Pins are seated first. Then the planner adds the stop with the best
`score ÷ (extra walking seconds + 1)`, at its cheapest place in the route. A stop is
skipped if it breaks the walking budget, the time-at-stops budget or the per-leg limit.
The greedy stops when the time-at-stops budget is spent. There is no cap on the number of
stops; the requested time is the only bound.

**Stop length.** `visit_time.py` prices the time at a place as a shape: time outside,
time inside, and a queue that depends on the hour of arrival. Interest decides how much
of the inside a walker gets. `max_stop_minutes` caps it. Rain halves the worth of an open
place. A `wall` deadline refuses any stop with a known queue. A stop lasts
`max(visit time, narration time)` (`stop_seconds`).

**Finish.** On a one-way walk with no destination, the planner tries to end at a strong
place in the far half of the area (endpoint pull). If the walk ends after civil dusk, a
place that is good after dark is preferred as the finish (`good_after_dark`,
`civil_dusk_local`). A dark finish nothing could fix is said out loud.

**Filling and repair.** If the day is still short, a fill pass adds more stops. Every
added stop must earn its walk: at most 2 seconds of walking per second it gives the
walker, and at most 30 minutes of pure detour (`stop_earns_its_walk`). A timebox repair
then adds, exchanges or drops stops, or keeps a stop outside its door, until the day fits
its band. Dropping a stop merges its two legs, and the merged leg must still respect the
per-leg limit. Pins, rests and the finish are protected from the repair. "Fewer stops"
then drops the weakest unprotected stops, never below three stops, and gives their minutes
to the stops that remain.

**Clean-up and order.** Two stops at the same spot merge into one. Two records of the
same place collapse into one. `order_stops` in `ordering.py` then finds the visiting
order: exact (Held-Karp) up to 16 points, cheapest insertion above that. A round trip is
turned so the main stop comes late when that costs no walking.

**Rests.** With `rest_cadence_minutes` set, a bench or toilet within a 150 m detour is
seated wherever walking would otherwise run longer than the cadence. A rest has no
narration but does have a duration.

**Final checks.** The planner re-times the final route at each stop's real arrival hour.
A door that is shut at arrival leaves the day, or keeps only its outside.

- Over the request by more than one minute: refused, with alternatives.
- Under half the request: refused, naming what binds. An `open` end is never refused for
  being short.
- Under 0.90 of the request: served, and the shortfall is recorded so the day can say so.
- With `max_leg_minutes` set, the longest street leg is checked. On a breach the
  planner retries once with a tighter limit, then refuses.
- With lenses set and no day possible, the planner retries once without the lens gate,
  and the day says so.

**Walk-past lines.** Vignette places within 50 m of a leg get one line each, at most two
per leg.

**Promises.** The day names up to three promises: the walker's pins, the main stop, the
finish and any rests.

**Replans.** A replan calls the same planner with a `ReplanContext` that holds the day
already walked. `contingency.py` precomputes replans for running late, running early and
skipping a stop.

### 3.3 Beat selection within a stop

`select_poi_beats` in `beat_select.py` orders and trims one stop's beats.

**Ordering.** `choose_ordering_strategy` picks one of three:

- **sub_location** (a building walk) when the beats carry at least 3 distinct
  sub-locations. Groups are ordered by the hand-made table in `fixtures.py` when one
  exists, otherwise alphabetically.
- **trigger_address** (a walk around a square) when the beats carry at least 5 distinct
  addresses. Addresses are ordered by house number.
- **narrative_function** otherwise: hook → establishing → deepen → climax → callback.

When both spatial tests pass, the one with more distinct values wins. Beats without a
sub-location or address follow, best first. A `stop_orientation` beat moves to the front.

**Dedup.** Two beats that share a lens (or both have none) are duplicates when any of
these holds:

- their entities overlap by Jaccard ≥ 0.8;
- their subject tags overlap by Jaccard ≥ 0.8;
- their text overlaps by character 5-gram Jaccard ≥ 0.30;
- they share a year and at least two normalised entities.

The weaker beat is dropped. The front orientation beat is never dropped.

**Caps.** A tier-4 or tier-5 stop voices at most 12 beats, a tier-3 stop 4, a tier-1 or
tier-2 stop 1. Spatial stops keep one beat per group first, then the best of the rest, in
walk order. The flat strategy keeps the front of its arc.

**Tone variety.** No three somber or reverent beats in a row, when lighter beats exist.
For spatial stops this applies within each group, so the walk never doubles back.

**Order by date.** A flat stop's beats then run from the earliest year to the latest.

**Audio limits.** No stop voices more than 180 seconds of beats (`MAX_DWELL_AUDIO_SECONDS`
in `selection.py`). "Less talking" halves this and "more talking" adds half. On an open
walk, an ordinary stop also gets a share of the day's time at stops (`govern_poi_beats`);
the first stop and the finish are exempt. A stop chosen for a lens keeps at least one beat
on that lens.

**Keep exploring.** Beats that do not fit are kept as the stop's "keep exploring" extras
(`extra_beat_ids`). They are never lost.

**Last stop.** The last stop's most closing beat (callback, then climax, then the longest)
moves to the end.

**Walk-past lines.** Each vignette place gets one beat, preferring one whose first sentence
names the place and matches the walker's lens.

### 3.4 Generation

`generate(beat_sequence, route, input)` in `generation.py` builds the stitched script.

1. **Opening.** "Settle in." Then the first stop's `stop_orientation` beat; most major places
   have none. If it has none,
   an orientation beat from another stop in the same area is used, but only if it has no
   physical cues or its place is within 100 m. Otherwise the engine builds a
   `SYNTHESIZED_OPENER` from corpus fields: the area, the walk to the first stop, a
   pronunciation, and one physical cue.
2. **Stop.** The stop's beats are streamed in order, one sentence each. Walking beats are
   kept for the leg. A stop the clock has shut says so first.
3. **Leg.** A corpus walking beat is used only when its direction can be checked against
   this leg, and it speaks no compass points. Otherwise one `GLUE_NAV` line is written
   by the glue model (§3.8) or taken from a template. Its minutes are the routed leg's own.
   Walk-past lines follow.
4. **Close.** Every stop ends on one `GLUE_CLOSING` line. The last stop's close is the
   day's.

After stitching, `claim_dedup.py` removes sentences whose every fact the route already
told, and exact repeats. At a stop visited only from outside, lines that invite the walker
inside are removed.

**Writing.** The stitched script is the input to the writer. The writer
(`authoring.py`, `premium_tour.py`) retells each stop as one spoken piece. It may fuse two
beats' tellings of one fact; such a sentence lists every beat it draws on. On long legs a
reflection (`GLUE_REFLECTION`) recalls what the walker has seen; `reflection.py` decides
which legs get one. A major stop can also have a longer "full telling" the walker taps for.

A stop may instead be told from its place's stored telling (`recipe_reuse.py`). That
happens only when the telling covers exactly what the stop would be asked: the same beats,
the same door, the same lens, a solo walker, and a stop that is not the day's last.

Each written stop is checked (§3.6). A stop whose writing fails keeps the sentences that
pass and falls back to the stitched text for the rest.

### 3.5 Glue rules — whitelist

Glue is the only text the engine writes without a beat behind it. Every glue sentence
carries one of these labels (`GLUE_LABELS` in `generation.py`):

| Label | Use |
|---|---|
| `GLUE_NAV` | Directions for a leg: "From X, carry on to Y, about a 6-minute walk away." |
| `GLUE_STAGING` | Physical staging: "Look up at…", "Notice…", a shut-door line. |
| `GLUE_PACING` | Pacing: "Settle in.", "Take a moment to take it in." |
| `GLUE_CALLBACK` | A pointer back to a place already visited. |
| `GLUE_CLOSING` | A stop's or the day's close. |
| `GLUE_REFLECTION` | A recap of places already visited, spoken on a long leg. |
| `ARITH` | Date arithmetic from cited beats ("four hundred years later"). |
| `SYNTHESIZED_OPENER` | An opening built from corpus fields (§3.4). |

**Glue may not:**

- say "imagine", "picture this", "envision" or "visualize";
- name a person or place, or a year, that no cited beat carries. The tour's own stops,
  areas and city are allowed. Directions (`GLUE_NAV`) may name places;
- promise a later stop ("at the next stop", "we'll come back");
- give compass directions in a leg line, or speak minutes other than the routed leg's.

### 3.6 Output format

`Script` in `contract.py` is the output:

| Field | Meaning |
|---|---|
| `city_slug`, `generated_at`, `inputs` | The city, the time it was built, and the request. |
| `total_audio_seconds` | Voiced words at 150 words a minute. |
| `total_walking_seconds`, `total_walk_distance_m` | The route's walking. |
| `total_planned_seconds` | The time the planner aimed at. |
| `selected_pois` | Each stop: id, name, tier, position, area, time at the stop, the beats voiced, and the "keep exploring" extras. |
| `lens_coverage` | How many voiced beats carry each lens. |
| `script` | The sentences, in order. |
| `validation` | The check report. |
| `verify_report` | For written stops: kept as written, partly reverted, or reverted to the stitched text. |

Each `Sentence` has its text, a `source_id`, a `source_type` (`beat`, `glue` or `arith`)
and its stop index. A beat sentence's `source_id` is a beat id, and `also_cites` lists any
other beats it fuses. A glue sentence's `source_id` is a label from §3.5.

**The gate.** `ValidationReport.passed` decides whether a script may be served. It fails
when any of these is non-empty:

- `untraceable_sentences` — a sentence cites no known beat and no allowed label;
- `forbidden_phrase_hits` — a glue sentence breaks a rule in §3.5;
- `provenance_failures` — a beat's stored source passage does not match its source text.

Two more checks run and are recorded, but do not block: `faithfulness_failures` (a
sentence the entailment check finds unsupported by its beat's claims) and
`coverage_failures` (a fact the stitched script told that the written script dropped).
`faithfulness_checked` says whether the entailment check ran at all.

`placement_floor_hits` in `validation.py` checks that each sentence is true where it plays:
no "you're standing here" on a leg, no walking instruction at a stop, and no invitation
inside a door that stays shut.

### 3.7 Density gating

`density.assess_snapshot` runs before selection. It asks whether the area around the
start holds enough to fill the request.

```
reach_radius   = the walking circle from §3.2 (pace-adjusted; halved for a round trip)
reachable      = places within reach_radius, role stop or setting, with ≥ 1 active beat
capacity_s     = Σ over reachable places of stop_seconds(visit time, all its beat audio)
target_s       = the time-at-stops budget from §3.2 (60% of the aimed time)
fill_ratio     = capacity_s / target_s

anchor_candidates   = reachable places with tier ≥ 3 and ≥ 3 active beats
cluster_compactness = mean distance between anchor candidates / reach_radius
                      (0 = all in one spot, 1 = spread to the edge)
```

With lenses set, `on_lens_fill_ratio` repeats the fill using only beats on those lenses.

**Status:**

```
GREEN  if (fill ≥ 1.0 AND anchors ≥ 4 AND compactness ≤ 0.6)
    OR (fill ≥ 1.5 AND anchors ≥ 6)
YELLOW if 0.5 ≤ fill < 1.0
    OR (anchors ≥ 3 AND compactness ≤ 0.7 AND fill ≥ 0.5)
    OR fill ≥ 1.0
RED    otherwise
```

**What each status does:**

- **GREEN** — plan as normal.
- **YELLOW** — plan, and attach the assessment to the route (`route.tourability`) so the
  surface can warn. The assessment suggests the longest duration the area supports and,
  for a round trip, a denser one-way destination.
- **RED** — selection raises `TourabilityRefusedError` with the same suggestions. No tour
  is built. A request with a destination skips the start-area gate and is judged on the
  start → destination corridor instead.

A GREEN area can still yield a thin route. When the delivered route falls well short, the
route carries the assessment with `delivered_thin` set, so the surface can say so.

`scripts/tourability_map.py` runs the gate over a grid of start points for planning and
heat maps. The engine does not depend on it.

### 3.8 Runtime cost estimate

Density, selection, ordering, beat selection, stitching and the checks in `validation.py`
call no model. The models are:

| Job | Where | Model |
|---|---|---|
| Writing each stop, and each stored place telling | `authoring.py` | `COMPOSE_MODEL` |
| Directions for a leg | `glue_client.py` (`HaikuGlueClient`) | `DEFAULT_HAIKU_MODEL`, at most `GLUE_MAX_OUTPUT_TOKENS` = 150 tokens out |
| Checking each written sentence against its beat | `verify.py` (`HaikuFaithfulnessChecker`) | `FAITHFULNESS_MODEL` |
| Fact review when a tour is certified | `quality_certification.py`, `certification_provider.py` | `FACT_REVIEW_MODEL` |
| Voice | the audio pipeline | the text-to-speech provider |

A failed glue call falls back to the template line and records the fallback. A stop told
from a stored place telling skips the writer, so it costs a small part of a written one.

The measured cost of a tour, and of building a city's stored tellings, is in
`Docs/business/tour-cost-measurement.md`.
