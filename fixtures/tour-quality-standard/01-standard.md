# The Tour Quality Standard

**Status:** LIVE. This document is the reference for what a good Ondoway tour is.
**Created:** 2026-07-19. **Owner:** the product owner (the gold text below is theirs).

## Why this exists

Five separate tour-quality root-cause memories were written in eight days
(`tour-gen-overhaul` Jul 12, `tour-quality-root-cause` Jul 17, `tour-qa-campaign`
Jul 18, `spatial-claims-unverified` Jul 18, `tour-repetition-root-causes` Jul 19).
Each session re-derived "what good looks like" from first principles, fixed a local
defect, and wrote a new memory. That is a loop, not progress.

The loop's cause: **the definition of good lived in a context window, never in the
repo.** This document ends that. It is the fixed reference. Every session reads it
before touching tour quality, and `src/tour/quality_rubric.py` enforces the
mechanically checkable parts on every generated tour.

Two layers, borrowed from a discipline that already works in this house
(the `writing-craft` skill's `validate.sh` + adversarial-read model):

- **The FLOOR** — mechanical, deterministic, $0. Counts, ratios, structural
  properties. Runs on every tour. A BLOCKER failure regenerates the script.
- **The GATE** — semantic, model-judged. Meaning-level properties a regex can
  never see. Per `feedback-no-lexical-shortcuts`, word-matching for MEANING is a
  banned shortcut here: narrative quality is judged semantically or not at all.

A tour that has passed only the floor is a first draft, not a deliverable.

---

## 1. The North Star — the human gold

Written by the product owner on 2026-07-15 by hand-rewriting the machine output
they had just ruled NO-GO. Source of record:
`origin/scope2-gate-calibration:specs/2026-07-13-compose-correct-dont-reject/01e-human-gold-rewrite.md`.

This is the quality bar made concrete. Reproduced in full because paraphrase is
exactly how it gets lost.

> Here, at the corner of rue de Castiglione and rue de Rivoli, stands the Hotel Le
> Meurice. Its discreet entrance is at 228 rue de Rivoli.
>
> To understand how a hotel like this came to belong here, we have to imagine the
> neighbourhood when the nearby Tuileries Palace was still the seat of power.
>
> The ladies of the Napoleonic nobility helped make this stretch of rue de Rivoli
> fashionable, drawing an English clientele to the area. At number 224, the English
> bookshop Galignani kept the daily newspapers from home. At number 248, W. H. Smith
> opened its Paris branch for the same clientele.
>
> Nearby, the jewellery and clothing shops of rue de Castiglione and Place Vendôme
> served this fashionable society. It was here, supplying Empress Eugénie and the
> other ladies of the Napoleonic court, that French haute couture was born in the
> second half of the nineteenth century.
>
> One of its pioneers was Charles Worth, the Empress's English couturier. A century
> later, the centre of Parisian fashion would drift westward to the 8th arrondissement.
>
> Le Meurice was one of the grand palace hotels that came to dominate this quarter.
> The English writers Thackeray and Charles Dickens both stayed here during the
> nineteenth century. Dickens stayed while researching A Tale of Two Cities, his
> novel about the years leading up to the French Revolution.
>
> The hotel is also linked to another English writer: George Orwell.
>
> Long before he became famous, a penniless Orwell worked in a grand Paris hotel as
> a plongeur. In Down and Out in Paris and London, he described the plongeur as "the
> slave of the slave," worked to the bone behind the splendour of the hotel.
>
> But did Orwell really work here?
>
> He never named the hotel. His biographers have placed it elsewhere in these
> streets—at the Lotti, just around the corner on rue de Castiglione, or at the
> Crillon. Paris lore, however, has long pinned the story on Le Meurice.
>
> What Orwell described could certainly fit the great hotels of this entire quarter.
>
> From the outside, there was "a vast, grandiose place with a classical façade," and,
> at one side, "a little, dark doorway like a rat hole, which was the service entrance."
>
> Behind that grand façade, the kitchens were a kingdom of hell: a "stifling
> low-ceilinged inferno of a cellar," red-lit by the fires and deafening with oaths
> and the clanging of pots and pans.
>
> Only a double door separated the squalid scullery from the dining room.
>
> On one side sat the customers "in all their splendour," with spotless tablecloths,
> bowls of flowers, mirrors, gilt cornices and painted cherubs.
>
> And on the other side, only a few feet away, Orwell and the other workers stood, as
> he put it, "in our disgusting filth."
>
> A generation later, the German High Command of the Paris garrison took up residence
> in the lavish Le Meurice. General von Choltitz was also quartered here when he saved
> Paris from destruction at the end of the Second World War.
>
> One wonders whether any of them had read Down and Out.

### Two corrections the owner already flagged on this text

Carried here so they are never reintroduced:

1. **"we have to imagine" violates the locked house voice** — the narrator never says
   "imagine". (See also `feedback-no-lexical-shortcuts`: the goal is anti-hallucination,
   and the word-ban itself was called out as the wrong instrument. The rule stands as a
   *voice* rule, enforced semantically, not as a regex on the word.)
2. **"William Makepeace" and "English writer(s)" are world knowledge** absent from the
   beat bodies. The faithfulness gate correctly rejects them. The grounded equivalents
   are "Thackeray and Charles Dickens" / "another writer". The gold text above is
   reproduced with that correction applied.

---

## 2. What makes the gold good

Derived from the text above and the owner's own analysis in `01e`. These are the
properties to reproduce — each one is a check in §4.

**S1. Orientation before history.** The stop opens by placing the listener in
physical space. *"Here, at the corner of rue de Castiglione and rue de Rivoli, stands
the Hotel Le Meurice. Its discreet entrance is at 228 rue de Rivoli."* The listener
knows where to stand and what to look at before a single date arrives.

**S2. Motivated transitions.** Movement between ideas is caused, not sequential.
*"To understand how a hotel like this came to belong here…"* — the transition states
why the next thing follows. Contrast with "Also," / "In addition," / bare adjacency.

**S3. A causal chain, not a list.** ladies → made the street fashionable → drew an
English clientele → Galignani and W. H. Smith served them. Each fact earns the next.
The machine version had the same facts with the causal middle missing.

**S4. Compound material unpacked and placed.** A single dense source beat is broken
into its component ideas and each is placed where the story needs it — not dumped
where it happened to sit in the corpus.

**S5. Every fact exactly once.** No restatement in new words. (Identical to the
`writing-craft` rule "Say it once": no mic-drop line that repackages the paragraph
above it.)

**S6. A theme, surfaced through transitions.** Englishness runs through the whole
Le Meurice stop and is carried by the connective tissue, never announced.

**S7. A dramatic pivot.** *"But did Orwell really work here?"* A single-line turn that
reframes everything after it. The stop has a shape: setup → deepening → turn → payoff.

**S8. Scene staging.** The Orwell quotes are staged spatially — outside → behind the
façade → one side of the door → the other side. The listener is walked through a
space, not read a passage.

**S9. Disputes kept whole and used.** The Orwell placement dispute is not flattened
into false certainty, nor dropped for being messy. It becomes the pivot. Hedges are
content, not noise.

**S10. A wry, non-moralising close.** *"One wonders whether any of them had read Down
and Out."* It lands the theme without stating a lesson. No "and so we see that…".

---

## 3. Prose discipline (from the house `writing-craft` skill)

These rules are already enforced elsewhere in this project for creative prose. They
transfer to tour narration unchanged.

**P1. Say it; don't circle it.** State the fact directly. Do not bury a point under
three sentences of atmosphere and simile before letting it land. Weight belongs in
the content, never in portentous staging.

**P2. Name what the listener would be told.** Atmospheric periphrasis used as a dodge
— "a famous queen", "a monster of a building" — is a flinch and a hard error. If the
source names Marie-Antoinette, the narration says Marie-Antoinette. Epithets are
legitimate only to *rotate* reference to something already named.

**P3. Say it once.** No restatement, no summary paragraph that repackages what was
just said, no examining the same idea from three angles that land in the same place.

**P4. No metadata laundering.** "Historians say", "records show", "it is said that"
used to make an unsourced claim feel earned is fabrication. Name the concrete source
or cut the claim. **This is the hallucination failure mode in its commonest costume.**

**P5. Not everything is symbolic.** A detail given as a detail stays a detail. Do not
upgrade it into theme or metaphor. This is the moralising-closer generator.

**P6. Register.** Concrete over summary. Short declaratives for impact. One metaphor,
landed, then moved on — never a chain. Avoid reverent description ("terrifyingly
precise"), moral hand-wringing, and "the kind of X that Y" (≤1 per stop).

**P7. The substance floor.** From `writing-craft`, and it is the rule this project
keeps breaking: *"short has always meant under-developed, never tight."* A stop that
"feels done" quickly has usually skipped the setup, the consequence, or the turn.
Judge depth by reading, never by hitting a number with water — but a stop rendered far
below the material available for it is under-developed by definition. See C1.

---

## 4. The checks

Severity: **BLOCKER** = the tour is not served; regenerate. **WARN** = surface to the
editor in the workbench.

### FLOOR — mechanical, deterministic, $0

| id | check | measurement | pass | severity |
|---|---|---|---|---|
| **C1** | **Starvation** — a POI with plenty of material is not reduced to a line | for each stop: `words_rendered` vs `beats_available` for that POI in the corpus. A stop whose POI has ≥`STARVE_MIN_BEATS` beats must render ≥`STARVE_MIN_WORDS_PER_BEAT × beats` words, unless deliberately a walk-past vignette AND the POI is below tier 4 | see §5 | **BLOCKER** |
| C2 | Tier inversion | no tier-5 POI is rendered as a vignette while a lower-tier POI is a full anchor | zero inversions | **BLOCKER** |
| **C3** | **Thin tour** | delivered audio vs requested duration | ≥ `c3_audio_floor_seconds(duration)` = **min**(`MIN_AUDIO_FRAC_OF_REQUESTED` (0.19) × requested, a CAP at what a tour can physically hold). **Corrected 2026-07-27** — was stated as the engine's `FILL_PASS_AUDIO_FLOOR_FRAC × target`, i.e. 0.398 × duration, which commit a069efd replaced. The `min` is load-bearing: term 1 is LINEAR in duration while capacity is CONSTANT, so an uncapped floor is unsatisfiable on long tours | **BLOCKER** — a 13-min tour for a 60-min request is not deliverable, so it blocks SERVING; it is loop-INELIGIBLE for compose (§7) because recomposing a stop cannot move `total_audio_seconds` past the seated material's own voiced/body ratio |
| C4 | Stop balance | no single stop holds a disproportionate share of total words | max stop share ≤ `BALANCE_MAX_SHARE` | WARN |
| C5 | Verbatim repetition | no sentence repeated within the tour | zero exact dupes | **BLOCKER** |
| C6 | Empty/glue-only stop | every stop has ≥1 substantive (non-glue) sentence | zero | **BLOCKER** |
| C7 | Time-budget overrun | walk + **stationary** listening ≤ the err-short total | within budget | **BLOCKER** |
| **C7b** | **Leg audio outruns its walk** | walk-concurrent narration vs the walk it rides | ≤ the walk (the smaller of `total_walk_seconds` / `Σ leg_seconds`) | **BLOCKER** |
| **C8** | **Gorging** — the inverse of C1 | words per stop | ≤ `GORGE_MAX_WORDS_PER_STOP` (**850**, corrected 2026-07-27 — see §5) | **BLOCKER** — the number is the enforced threshold; the principle it approximates is below |
| C9 | Sentence length for the ear | `mean_sentence_words` (reuse `narration_quality`) | ≤ 20 (anchored on the §1 gold, see §5) | WARN |
| ~~C10~~ | ~~Opens with a look-cue~~ | **DELETED 2026-07-27, NOT REPLACED — opener quality is currently UNCHECKED.** It asked a MEANING question with a word list (`narration_quality._LOOK_INITIAL`, a regex over 18 sentence-initial verbs). The §1 gold opens *"Here, at the corner of rue de Castiglione and rue de Rivoli, stands the Hotel Le Meurice"* — the clearest orientation-before-history line in this document, the sentence S1 was derived FROM — and C10 rated it "a bare fact, not a look-cue". It fired on 100% of 191 saved tours AND on the north star, so it separated nothing. Extending the word list is the same shortcut with more words. The replacement is G1, below, and **G1 is not built** | — | none |
| C11 | Date density for the ear | `year_density` per 100w (reuse `narration_quality`) | **REPORT ONLY since 2026-07-30 — emits NO finding.** Outliers land in `stats["year_density_outliers"]`. Demoted on measurement, not taste: it fired on 100% of the human-vetted reference tours (5/25 Place des Vosges positions, 6/29 Ile) and only 8-11% of machine tours, and the multiple needed to admit the human references (3.82/4.19) sits ABOVE the machine maximum (3.11) — so no threshold clears the humans and still bites. Kept as a stat because the signal is real when diagnosing one date-list stop; demoted because as a WARN it was noise pointed at this project's own north star | none (was WARN) |
| C12 | Stops close enough to deserve a human glance | haversine distance between consecutive anchors | ≥ `MIN_STOP_SEPARATION_M` | WARN — demoted from BLOCKER 2026-07-19; see §5, distance alone cannot distinguish "the same place told twice" from two genuinely distinct, adjacent landmarks (the gold-text stops, §1, sit 8.4 m apart). The check that actually catches the former is semantic, G4 |

#### C8's number vs. the principle it approximates (2026-07-20)

**The cap is 850 as of 2026-07-26 (this section was written when it was 750, and its "not changing here" was true only of that day's slice — see §5 for the widest-stop measurement that moved it). The principle below is unaffected by the number.** But
a live-run finding (`GENERATION-SAMPLES-2026-07-20.md` §3, NYC-A / Washington
Square, 1340 words in a single stop) surfaced what the number is a proxy
*for*. Two independent end-user-advocate reviewers did a full listening
walkthrough of that stop, reconciled by a judge who re-verified every quote
against the source file. Their finding: word count is the symptom, not the
disease. The stop's first ~600 words (the park and arch, everything visible
from where the listener is standing) would pass C8 comfortably on their own;
what pushes it to 1340 is seven further vignettes, each anchored to a
different off-site address the listener cannot see and will never walk to,
narrated from the same fixed GPS point. Trimming prose to fit under 750
would satisfy the check while leaving that defect intact — the listener
would still be pointed at unseeable addresses, just in fewer words.

The principle C8's word cap approximates (750 at the time of writing, 850 today —
the principle does not depend on the number): **one stop should
correspond to one place the walker can actually see, narrated while their
gaze stays employed** — not multiple physically distant addresses narrated
from a single fixed point. Word count is a cheap, mechanical stand-in for
that; it is a real and useful stand-in (it caught this exact stop), but a
fix aimed only at the number (prose-trimming) is not the same as a fix aimed
at the principle (graph re-packaging: splitting an over-stuffed stop into a
short walking route with real legs between the places it covers, per
`GENERATION-SAMPLES-2026-07-20.md`'s root-cause refinement). Do not read
this as a reason to raise, lower, or waive the threshold on the strength of
this argument; this is a note on what future work should fix *toward* when a
stop trips it. (The cap did later move, 750 -> 850, but on a widest-stop
MEASUREMENT — see §5 — not on the reasoning in this section.)

### GATE — semantic, model-judged

| id | check | judged on | severity |
|---|---|---|---|
| G1 | Orientation before history (S1) | does the stop open by placing the listener physically? | WARN — **NOT BUILT. Since C10's deletion on 2026-07-27 this is the ONLY opener coverage specified, so openers are unchecked until it ships.** Pinned by `test_openers_are_unchecked_since_c10_was_deleted`, which fails the moment any opener check returns |
| G2 | Motivated transitions (S2) | are transitions caused, or bare adjacency? | WARN |
| G3 | Causal chain not list (S3) | do facts earn each other? | WARN |
| G4 | Semantic repetition (S5/P3) | same fact restated in new words anywhere in the tour | **BLOCKER** |
| G5 | Metadata laundering (P4) | unsourced authority appeals | **BLOCKER** |
| G6 | Periphrasis dodge (P2) | a named entity referred to only obliquely | WARN |
| G7 | Moralising close (P5/S10) | does any stop end by stating a lesson? | WARN |
| G8 | Fabrication | every checkable fact traces to a beat body | **BLOCKER** — already enforced by the calibrated entailment gate; see §6 |

---

## 5. Thresholds, and their provenance

**Every number here is either measured, inherited, or explicitly a judgement call.
Nothing is invented and dressed as evidence.**

| constant | value | provenance |
|---|---|---|
| `STARVE_MIN_BEATS` | 5 | **judgement call.** Measured context: on the Île de la Cité 60-min tour, Sainte-Chapelle (tier 5, **12 beats**) rendered as 9 words while Palais de Justice (tier 4, **3 beats**) was a full anchor. 5 sits above the 3-beat thin POIs and below the 12-beat starved one. Revisit with more data. |
| `STARVE_MIN_WORDS_PER_BEAT` | 12 | **judgement call**, anchored on measurement: the gold Le Meurice stop runs ~560 words; healthy measured stops ran 1022 words / 59 beats ≈ 17 w/b and 915 / 23 ≈ 40 w/b. 12 is deliberately permissive — it catches 9-words-for-12-beats (0.75 w/b), not merely terse stops. |
| `BALANCE_MAX_SHARE` | 0.60 | **judgement call.** Measured: the 2-stop tour split 1022/915 words (53%/47%) — fine. The failure shape is one stop at 90%+. |
| audio floor | `MIN_AUDIO_FRAC_OF_REQUESTED = 0.19`, under a capacity cap | **MEASURED, replacing the inherited 0.8, 2026-07-27 doc correction of commit a069efd.** The old row said the floor was `FILL_PASS_AUDIO_FLOOR_FRAC = 0.8` inherited from selection.py — that produced `ERR_SHORT(0.83) × AUDIO_FRACTION(0.60) × 0.8 = 0.398 × duration`, which **7 of this project's own 8 certified tours failed**, and which no choice of fraction can fix above ~100 min because the floor is linear while word capacity is bounded. The rubric no longer derives a serving verdict from the engine's planning constants at all. |
| time ceiling | `walk_budget / WALK_FRACTION` | **inherited** — the engine's own err-short total, `src/tour/routing.py:42-43`. |
| words/sec for audio | `SPOKEN_WPM = 150` | **measured + inherited, REWRITTEN 2026-07-19.** `_sum_audio` now returns `voiced_words / 150 * 60` — the words actually spoken, at one documented rate. The old model was **not a measure of audio**: it credited every glue sentence a flat **4 s regardless of length** (a 60-word reflection counted as 4 s, which made walk-leg narration invisible to the tour's own clock) and capped beat credit at the corpus estimate via `min(1.0, voiced/body)`, so richer prose could never raise the number — only dedup could lower it. It measured SEATING VOLUME. 150 is not a new judgement call: `routing.beat_spoken_seconds` already falls back to `word_count / 150 * 60`, density's `word_count / 2.5` is the same figure, and the live Paris corpus's 486 beats with populated `est_spoken_seconds` imply p10 **147** / median **150** / p90 **153**. |
| `MIN_STOP_SEPARATION_M` | 50 m, **C12 demoted BLOCKER → WARN 2026-07-19** | **judgement call, from a MEASURED absurdity, then demoted by a SECOND measurement.** An acceptance pass on a real Île de la Cité tour found Palais de Justice and Conciergerie seated as separate stops **17 metres apart** — the same building complex — where the second stop opens by describing the spot the listener stood on seconds earlier. RECALIBRATED from 100 m to 50 m: at 100 m this also flagged Sainte-Chapelle at 86 m from the Conciergerie, a FALSE POSITIVE (a genuinely distinct attraction, a real walk in a dense historic quarter). **Then, measured across the full real corpora** (`tests/test_tour_selection.py::test_selection_does_not_filter_close_but_distinct_pois`): Paris' 370 POIs give 48 pairs under 50 m, New York's 402 give 46 — two different populations, not one. True duplicates (corpus geocoding defects) cluster at 0.0–1.7 m; genuinely distinct, walkable landmarks start at ~8 m, including **Hôtel Le Meurice ↔ Angelina at 8.4 m** — the two POIs in the owner's own gold text (§1). Distance cannot tell these apart; a BLOCKER at 50 m makes the gold-standard tour structurally unbuildable. A companion selection-side filter at this same distance was tried and reverted for the identical reason. C12 is therefore WARN: still surfaced to the editor (a short gap deserves a glance), never refuses serving. The real tool for "same place told twice" is semantic, G4. See `src/tour/quality_rubric.py`'s `MIN_STOP_SEPARATION_M` comment for the full measurement. |
| `OUTLIER_YEAR_DENSITY_MULTIPLE` | 2.0 | **measured, 2026-07-19**, over every composed tour in `data/*/tours/` — **195 tour files: paris 191, london 4. `data/new_york/tours/` does not exist and contributed nothing**, so this is a Paris-dominated sample (paris 485 of 501 stop/tour samples with a nonzero tour mean), NOT a three-city one. Each stop's `year_density` against its own tour's mean has median 0.97, p90 1.67, p95 2.00, max 3.11; 3.4% of samples exceed 2.0×. The constant sits at the measured p95 — a genuine outlier cut, not an invented number. **Provenance caveat:** an earlier revision of this row claimed "paris, london, new_york — 487 samples". That was wrong on both the city list and the count, and it was caught by an adversarial re-read, not by the author. The distribution reproduces exactly; only the stated evidence base was inflated. Re-measure across cities before treating 2.0 as generalising beyond Paris. |

### The time model — audio overlaps walking (product ruling, 2026-07-19)

**The owner's ruling, verbatim: "Audio overlaps the walking. It is a part of the
tour experience."** This is the reference for C7/C7b and for any future work on
walk-leg content.

Two kinds of listening, and only one of them costs the tourist minutes:

- **Stationary** — standing at a stop, listening. ADDS to elapsed time. C7 counts it.
- **Concurrent** — listening while walking a leg. Costs NO elapsed time; it rides a
  walk that was happening anyway. C7 excludes it; C7b bounds it by the walk's length.

Before this ruling, `C7` summed walk + *all* audio, which models a tourist who walks
in silence and then stands in silence to listen. That made walk-leg narration
unscoreable: adding content to a walk appeared to consume the time budget even
though the tourist finishes at the same moment.

`src/tour/generation.py::is_walk_concurrent` is the single shared predicate — nav
glue, reflections, and vignette one-liners are walk-concurrent; anchor beats are
stationary. The rubric and the workbench's `band="leg"` cards both use it, so what
an editor sees on a leg card is exactly what the rubric scored as free.

**A correction worth recording, because the process caught it and the author did
not.** An earlier revision of this section justified the change with a worked
example: "1650 s walk + 1434 s audio = 3084 s against a 2988 s ceiling — closing C3
necessarily trips C7, BLOCKER for BLOCKER." That arithmetic was wrong. The 1650 s
was measured while an unrelated, since-reverted selection experiment was applied;
the real walk on that route is 1087 s, giving 2521 s against 2988 s — 467 s of
headroom. **No measured tour ever deadlocked.** An adversarial review found this in
shipped source comments. The ruling stands on its own as a statement about what the
product IS; it never needed the arithmetic.

**Open, and deliberately not resolved here:** `AUDIO_FRACTION = 0.60` is cited below
against a Nubart figure that actually says **0.50**, and the 0.8 C3 floor is derived
from it. Both were set in a walk-in-silence world. Now that leg audio is free, the
floor should be re-derived — a stop-audio floor and a leg-fill target are arguably
two different numbers. Do not treat 0.60/0.80 as settled.

### Externally sourced thresholds (professional audio-guide practice)

Researched 2026-07-19. These are **cited**, not invented.

| constant | value | source |
|---|---|---|
| `WORDS_PER_MINUTE` | 130–150 | [Musa Guide](https://www.musa.guide/en/resources/how-to-write-audio-guide-script), [Nubart](https://www.nubart.eu/audio-guides/content-production/writing-museum-guide-scripts.html) ("one minute of speaking time in English corresponds to about 130 words") |
| `MAX_SENTENCE_WORDS` | **20** | **JUDGEMENT anchored on a MEASUREMENT of §1, revised 2026-07-27.** Was 15, cited to Nubart ("keep sentences ≤10–15 words… sentences difficult to read aloud become three times more difficult for the visitor to hear"). That range describes museum-station copy and **the §1 gold text fails it**: the gold measures `mean_sentence_words` **19.11**, as did all 191 saved tours. The check itself is sound and was nearly deleted by mistake: measured PER STOP (which is how C9 fires), the gold's 19.11 sits against 38 machine stops at min 16.9 / median 23.2 / max 34.3, so the gold reads shorter than **34 of 38** — C9 ranks the hand-written text above machine output correctly, and only the constant was wrong. **What the retune did and did not fix:** stop-level passes go 0/38 → 6/38 and the gold is admitted, but at TOUR level the rate barely moves (9/9 of cohort A, 176/182 of cohort B still trip it) because one over-long stop flags a whole tour — most real tours genuinely do ramble. The number is the **smallest round number strictly above the anchor**: not 19.11 itself, since `score_narration` rounds to 2dp and one word added to a 509-word passage would flip it, and since the gold's mean is taken over 27 sentences while a stop's is taken over 5-6 — a noisier statistic than the one it was calibrated on. Nothing hinges on the exact value (19.5 fails 33/38, 20 fails 32, 21 fails 31); the rule matters more. Guarded by `test_c9_cap_admits_the_owners_own_gold_text`. Re-derive from §1 if §1 changes; never lower §1 to fit the cap. |
| `MAX_WORDS_PER_STATION` | 250 (museum station) | Nubart: "Maximum 250 words per station (approximately 2 minutes of audio)" |
| `GORGE_MAX_WORDS_PER_STOP` | **850** (was 750; corrected 2026-07-27) | **adapted, stated as such.** A walking-tour anchor with a 5-min dwell is not a museum station; 750 words ≈ 5 min at 150 wpm, which is the engine's own tier-5 `DWELL_SECONDS_BY_TIER` of 300 s plus listening slack. The museum 250 is the floor of the evidence, 750 the walking-tour adaptation. **Measured violation:** Notre-Dame rendered **1022 words** in one stop — over even the adapted cap. |
| audio ≈ half total visit | 0.5 | Nubart: "Total guide length should be roughly half the average visit time." **Corroborates the engine's own `AUDIO_FRACTION = 0.60`** — the existing target was never wrong. |
| attention ceiling | ~30 min | Nubart: concentration degrades after ~30 minutes. |

### Craft rules (same sources)

- **Never open a stop with bare object identification.** "The first sentence should prompt
  the visitor to observe, compare or reflect — not merely state a fact." Good: *"Look at the
  small figure on the right-hand edge…"* This is S1 (orientation before history),
  independently confirmed.
- **Second person.** "Did you notice…" beats a passive construction.
- **Reduce numeric data to essentials.** Dimensions and dense dates suit a wall label, not
  the ear. Speak dates ("born in Malaga in 1881"), don't list them ("1881–1973").
- **One anecdote or surprising fact per stop** aids concentration and retention.
- **No jargon**; define a term when it is unavoidable.
- **Story, not catalogue.** [Detour](https://techcrunch.com/2014/07/30/detour/) — the
  best-regarded product in this category — staffed **journalists and radio producers**,
  targeting "an episode of This American Life or Radiolab" rather than a guide reciting
  facts. This is the same instinct as the human gold's pivot-and-payoff structure.

`src/tour/narration_quality.py` already computes `mean_sentence_words`,
`long_sentence_rate`, `year_density`, `burstiness` and `second_person_rate` — the rubric
**reuses** those and now has sourced thresholds to judge them against.

---

## 6. What already exists — do not reimplement

- **`src/tour/narration_quality.py`** — `score_narration` / `craft_score`. Computes
  stilted/engagement composites, burstiness, mean sentence words, long-sentence rate,
  second-person rate, look-prompt rate, and per-100w "tells". The rubric **reuses**
  these; it does not recompute them.
- **The calibrated entailment gate** — `HaikuFaithfulnessChecker`, calibrated in
  `scope2-gate-calibration` over five runs to **zero fabricating acceptances**
  (`02b-calibration-scorecard.md`). G8 is that gate. Do not build a second one.
  Its governing ruling, from the owner: *"we are not making up facts, but the composer
  gets room to sound natural"* — the gate rejects checkable-fact violations; interpretive
  colour is composer licence.
- **The correct-don't-reject corrector** — `src/tour/compose_correct.py`, ported to
  main in `a1bb982`. A flagged sentence is repaired (trim, then rewrite) before it is
  allowed to degrade to raw stitch.

---

## 6b. CLOSED 2026-07-31 — the app and the workbench now share one composer

**This section used to record a known divergence. It is closed: the two surfaces run
the SAME engine.** The whole-tour composer it described — `compose_script`,
`compose_script_per_chapter` and the whole of `src/tour/compose.py` — has been deleted.

| surface | endpoint | composer | on gate failure |
|---|---|---|---|
| workbench and mobile app | `POST /trips/{id}/compose` | per-stop authoring (`src/tour/authoring.py`) | raises → **HTTP 422, trip left UNMUTATED** |

**The persistence argument that forced the 2026-07-19 revert still holds, and is
honoured rather than overridden.** `/trips/{id}/compose` writes to Neo4j
(`replace_trip_stops` + `mark_trip_composed`) and the audio flow voices whatever is
stored, so that path still **refuses loudly** with HTTP 422
(`reason: compose_verification_failed`) instead of persisting a degraded tour. The
guard that protects it,
`tests/test_trip_api.py::test_refused_flavour_is_422_and_leaves_trip_untouched`, is
still green. What changed is the ENGINE underneath, not the refusal contract: the
2026-07-19 attempt failed because swapping in a composer that never refuses would have
persisted degraded content silently. This consolidation kept the refusal.

The two surfaces still differ deliberately in gate depth, which is the honest remaining
asymmetry: the persisted path runs the real faithfulness checker, the coverage baseline
and the full `validate_script` forbidden-phrase scan; `/trips/preview` runs the
structural validator only, so an interactive editor preview stays fast and offline.

The old "to close it properly" note — add `compose_status` to `TripComposeResponse` —
was NOT done and is NOT needed for the unification: the app distinguishes outcomes by
status code (200 vs 422), which is the contract `mobile/lib/services/trip_service.dart`
already branches on. It remains available if a future product ruling wants per-stop
provenance on the phone.

---

## 7. How this runs

`src/tour/quality_rubric.py` exposes:

```python
def score_tour(script, route, beats_by_poi, *, beat_sequence=None) -> RubricReport
```

(The signature above was corrected 2026-08-23 — it had drifted to a
`snapshot`/`tour_input` shape this module never actually had.)

`RubricReport` carries per-check results, the blocker list, and an overall verdict.
The compose path calls it after composition.

**Two separate predicates, not one.** The naive reading — "a BLOCKER verdict
regenerates the script" — conflates two different questions:

1. **Is the tour fit to serve?** `RubricReport.passed` — BLOCKER means the tour
   *should not* be served as-is.

   **As of 2026-07-19 nothing enforces this, and the gap is deliberate to record
   rather than paper over.** The only production caller is
   `src/api/routes/trips.py` (`preview_trip`), which serialises `rubric.passed`
   and the finding lists into the response payload and returns 200. It does not
   raise, refuse, or regenerate; `src/api/dependencies.py` says so in as many
   words. `POST /trips/{id}/compose` — the path that actually persists to Neo4j —
   never calls `score_tour` at all. **So today the rubric is an ADVISORY report
   surfaced to the editor in the workbench, not a gate.** Any statement that a
   BLOCKER "is not served" describes intent, not implemented behaviour.

   Closing it is a real change with a real cost, and it needs §7.2 below first:
   without `compose_fixable` a naive retry loop burns Opus calls on blockers that
   provably cannot converge. Do not write enforcement language here until a caller
   actually honours `passed`.

   **CLOSED 2026-08-23 (Phase 8 S8.4; design §7.2).** A caller now honours
   `passed`: `POST /trips/{id}/compose` runs `score_tour` after composition —
   BEFORE the full tellings' spend and before any write — and refuses a
   blocker-carrying day `422 tour_quality_blocked`, naming each blocker's check,
   stop and message on the wire (the rubric's own prose, never the provider's).
   The refusal comes only after at most ONE `compose_fixable`-authorized
   targeted recompose of the failing stops (same request, fresh roll), and the
   VERIFY layer independently spends at most one targeted re-roll of the stops
   its report names before ITS refusal stands. `/trips/preview` stays the
   editor's advisory surface (200 + report). Caller-side proof:
   `tests/test_trip_api.py::test_a_day_failing_the_rubric_floor_is_refused_and_the_trip_untouched`.
   (The `src/api/dependencies.py` comment this section used to cite had already
   been removed; the gap it described is now closed in
   `src/api/routes/trips.py::compose_trip`.)
2. **Is looping the compose step, for one stop, worth the spend?** This is the
   separate question `src/tour/quality_rubric.py::compose_fixable(finding,
   material)` answers. A BLOCKER whose defect is upstream of compose — selection
   picked the wrong POI, ordering chose a bad route, or the audio-seconds formula
   structurally cannot move — reproduces IDENTICALLY on a recompose. That is
   spend with zero chance of convergence, not a retry.

   The clearest proof is **C3 (thin tour)**: `src/tour/generation.py:1203-1207`
   computes the beat-derived share of `total_audio_seconds` from SEATED beats scaled
   by the voiced-word fraction, `min(1.0, voiced_words / body_words)` — a recompose
   can only push that ratio TOWARD 1.0, never past it, so writing more BODY words
   cannot close a C3 gap. This is not literally zero extra audio in every sense:
   `generation.py:1208` adds `glue_count * 4` seconds on top, uncapped by that
   ratio, so more glue (navigation/transition) sentences do add a few real seconds
   — a reward-hacking vector worth naming, not a route to closing a serious gap.
   C3 stays a BLOCKER for the serving verdict (§4); it is simply never
   loop-eligible. **C1 (starvation)** is the sharper case: if the seated material
   for a stop never reached the corpus-derived floor, the rest of the POI's beats
   are sitting in `overflow_by_poi`, never handed to the composer — telling
   compose to "write more" invites fabrication, the entailment gate rejects it,
   and the stop floors to grounded stitch, which is **worse** than the original
   terse render. Looping there doesn't just waste money, it degrades the tour.

   `compose_fixable` is therefore consulted BEFORE any retry loop spends a
   recompose call on a BLOCKER, fails closed on every check it has no positive
   evidence about (an unrecognised id, a WARN, or missing structured context),
   and is exhaustively unit-tested per check id in `tests/test_tour_quality_rubric.py`.

WARN results never gate serving; they surface in the workbench so an editor sees
them without the tour being blocked.

The floor runs on every tour, always, at $0. The gate runs where a model call is
already being made, so it adds no new spend tier.

---

## 8. Provenance of this document

- §1 gold text — `origin/scope2-gate-calibration`, `01e-human-gold-rewrite.md`, written
  by the product owner 2026-07-15.
- §2 properties — the owner's own analysis in `01e`, restated as checks.
- §3 prose discipline — the house `writing-craft` skill, applied unchanged.
- §5 measured values — a $0 engine run on the live Paris graph (370 POIs / 1543 beats),
  Île de la Cité, 60 min, `dark_history`, 2026-07-19.
- §6 — `02b-calibration-scorecard.md` and the modules cited.

**External professional practice (Rick Steves, Detour, museum audio-guide style
guides, broadcast writing-for-the-ear) already exists, in a sibling folder never
cross-linked from here until now: `specs/2026-07-16-tour-craft/`.** It was written
2026-07-17 — three days before this document — and contains a sourced literature
review (`GOOD-TOURS-RESEARCH.md`: VoiceMap, Tilden, NAI, Ira Glass, Method Writing,
and others, each claim attributed to a live-fetched source URL), six full,
attributed, sentence-by-sentence breakdowns of real Rick Steves transcript stops
(`GREAT-STOP-EXAMPLES.md`), and a 10-point reviewer checklist with a kill-switch
(`RUBRIC.md`). §5's externally-sourced-thresholds table already draws numbers from
this research (Musa Guide, Nubart). This document is honest about its own
provenance (§8) rather than filling gaps with plausible-sounding numbers; the gap
was in cross-linking, not in the research existing.
`GENERATION-SAMPLES-2026-07-20.md` (this folder) is a live-generation calibration
point measured against both this standard and that research — four real, good,
validated tours (accurate, engaging, zero fabrication found by any reviewer)
that are the working baseline reference set, with named structural issues
(not content-quality issues) being iterated on, and short of this document's
own stricter ≥8/10 bar on both.
