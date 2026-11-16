# Persona acceptance rubric

A tour is accepted for a persona when it passes every **must** criterion of that persona and every
shared must criterion. A **should** criterion is reported but does not decide acceptance. A day the
product refuses fails every must criterion of its persona.

Status: draft for review.

- *(proposed)* marks a threshold the persona's day does not state.
- *(source not checked)* marks a rule whose input I have not yet found in the code.

## How each criterion is checked

There are three kinds of criterion.

- **Rule.** Computed without AI, for free, on every `make test`. It reads one of three things:
  - the session reply (`GET /trips/{id}/session`)
  - the preview reply (`POST /trips/preview`)
  - the stop's place record in the city graph, looked up by `poi_id`
- **Judge.** Needs an AI to read narration that the real writer composed. It is paid and runs on
  demand. Free test runs replace the writer with the source sentences, so a judge reading a free
  run would be judging the wrong text.
- **Gap.** Neither the request nor the corpus can carry what the persona needs. The criterion stays
  listed and is reported as a gap, never as a pass.

### Rules every judged criterion follows

1. **One criterion per judge call.** The call holds only that criterion's pass and fail text.
2. **The verdict is `PASS`, `FAIL` or `UNKNOWN`.** There are no scales. `UNKNOWN` is for missing
   evidence, such as a stop with no narration.
3. **The judge writes in this order:** quotes from the narration, then its reasoning, then the
   verdict. The output schema puts the fields in that order.
4. **The judge sees only:**
   - the persona's day as written
   - the request
   - for each stop, its name, planned times and full narration
5. **The judge is a different model from the writer.**
6. **The judge runs three times per tour.** A must criterion passes only if all three runs pass.
   The report shows all three.
7. **A judged criterion is calibrated before it can block acceptance.**
   - It is checked against tours Sairam labelled by hand. The set must hold both passes and
     failures.
   - The report gives two numbers: the share of his failures it catches, and the share of his
     passes it passes.
   - The pass mark for those two numbers is not set. No source read sets one; Sairam decides it.
   - Until then, it is reported and does not block.
8. **Every verdict stores** the rubric version, the criterion version, the judge model and the
   tour's hash.

## Shared criteria (every persona)

| ID | Criterion | Kind | Weight |
|---|---|---|---|
| X1 | The day is served. | Rule | Must |
| X2 | A refused day names the rule that refused it. | Rule | Must |
| X3 | No promise is dropped without the reply saying so. | Rule | Must |
| X4 | No replan that drops a stop creates a leg longer than the day's leg cap. | Rule | Must |
| X5 | Rests, meals, toilets and the finish are never cut automatically. | Rule | Must |
| X6 | Ending early at any stop leaves a tour with a closing piece. | Rule | Must |
| X7 | A stop reported shut never returns to the day. | Rule | Must |
| X8 | No stop is labelled with a subject the person did not ask for. | Rule | Must |
| X9 | The rule-based quality floor finds no blocker. | Rule | Must |
| X10 | No sentence fails fact review. | Judge | Must |

- **X3 to X8** are the existing persona replay checks.
- **X9** is the existing quality floor.
- **X10** is the existing fact review. It breaks rules 5 and 6: Claude Opus 4.8 both writes and
  reviews, and it reviews once. X10 is reported, and it does not block until it follows both
  rules.

The existing five-axis enjoyment score is not a criterion here. No code turns a real tour's scores
into a pass or a fail. The score's threshold is applied only to the three calibration examples.

## 01 — Camille, architecture, 5 hours

| ID | Criterion | Kind | Pass when | Weight |
|---|---|---|---|---|
| CA1 | Walking does not fill the day. | Rule | Total walking is at most 30% of the asked time *(proposed; hers is 22%)* | Must |
| CA2 | Silence at a stop is allowed. | Rule | At least one stop's audio is under a third of its planned time (Concorde: 4 of 20 minutes) | Should |
| CA3 | The declared interest can earn a long stop. | Rule | At least one stop is planned for 30 minutes or more (Cour Carrée: 33) | Must |
| CA4 | A queue is planned apart from the visit. | Rule | In the preview, a promised place with a queue shows `queue_minutes` apart from its visit. This covers promised places only. | Must |
| CA5 | The narration points at what she can see on the building. | Judge | Each architecture stop names at least one visible feature and says where to look for it | Must |

## 02 — Théo, dark history, same request as Camille

| ID | Criterion | Kind | Pass when | Weight |
|---|---|---|---|---|
| TH1 | His day differs from Camille's in places. | Rule | At least two stops are not on Camille's day *(proposed; his has six)* | Must |
| TH2 | His day differs from Camille's in time spent. | Rule | At two or more shared places, his planned time differs from hers by at least half *(proposed)* | Must |
| TH3 | The named end is not treated as the climax. | Rule | The finish stop is not the longest stop of the day | Should |
| TH4 | Shared places are told through his interest. | Judge | At each place both days visit, his narration tells a dark-history story about it, not its architecture | Must |

## 03 — Nadia, two children, 2 hours

| ID | Criterion | Kind | Pass when | Weight |
|---|---|---|---|---|
| NA1 | No story stop holds the children too long. | Rule | No stop with a story is planned over 10 minutes *(proposed; her story stops are 6 and 10)* | Must |
| NA2 | A toilet comes early. | Rule | A rest promise starts within the first 45 minutes (hers is at 40), and the place is a toilet *(source not checked)* | Must |
| NA3 | Every early ending is a decent tour. | Rule | X6 passes on her day | Must |
| NA4 | Walking is planned at a child's pace. | Rule | `walking_pace_kmh` on the reply is the family preset's pace, not the default | Should |
| NA5 | No long queue. | Rule | No preview promise has `queue_minutes` over 5. This covers promised places only. | Must |
| NA6 | The narration works aloud for an 8- and a 5-year-old. | Judge | Every stop is concrete, speaks to a group, points at something the children can see or do, and describes no violence in detail | Must |
| NA7 | The best story is not saved for the end. | Judge | The stop the judge rates most engaging is in the first half of the day | Should |

## 04 — Marcus, train at 16:40, 3 hours

| ID | Criterion | Kind | Pass when | Weight |
|---|---|---|---|---|
| MA1 | The plan ends early, with the margin shown. | Rule | `planned_end_hhmm` is 16:21 or earlier (a hard end aims at 95% of the 180 minutes from 13:30), and the spare minutes are shown *(source not checked)* | Must |
| MA2 | No replan breaks the deadline. | Rule | Every replan step keeps the finish at or before 16:40, or asks the question | Must |
| MA3 | It starts and ends at the station. | Rule | The first stop and `finish_lat`/`finish_lng` are within 150 metres of Gare du Nord *(proposed distance)* | Must |
| MA4 | No stop has an unpredictable wait. | Rule | No preview promise has `queue_minutes` above 0. This covers promised places only. | Must |
| MA5 | One long walk carries the tour. | Rule | At least one leg of 15 minutes or more has leg narration *(proposed; his is 28)* | Should |
| MA6 | The route suits a wheeled bag. | Gap | Nothing records cobbles on a leg. The luggage preset avoids stairs only. | — |

## 05 — Rosemary, 78, step-free, 3 hours

| ID | Criterion | Kind | Pass when | Weight |
|---|---|---|---|---|
| RO1 | No single walk is too long. | Rule | Every leg is 12 minutes or less | Must |
| RO2 | Rest is planned. | Rule | A rest stop at least once in every 30 minutes of walking *(proposed)* | Must |
| RO3 | A toilet is in the plan. | Rule | At least one stop's place is a toilet *(source not checked)* | Must |
| RO4 | The route has no steps. | Rule | The day was routed with the take-it-easy preset's step-free surface | Must |
| RO5 | Nothing is shut when she arrives. | Rule | Every stop's place is open at its planned time on that weekday *(source not checked)* | Must |
| RO6 | One long stop is allowed. | Rule | At least one stop is planned for 30 minutes or more (Orangerie: 46) | Should |
| RO7 | The day returns to the Orsay. | Rule | The finish is within 150 metres of the start | Must |

RO1 says 12 minutes because her day says so. The take-it-easy preset caps a leg at 13 minutes. One
of the two needs to change.

## 06 — Julien, resident, 2 hours

| ID | Criterion | Kind | Pass when | Weight |
|---|---|---|---|---|
| JU1 | The famous places stay out. | Rule | No stop has `importance_tier` 5 | Must |
| JU2 | A day of lesser places can be served. | Rule | The day serves with no stop above tier 3 *(proposed)* | Must |
| JU3 | One long walk with continuous narration. | Rule | At least one leg of 15 minutes or more has leg narration *(proposed)* | Should |
| JU4 | Every stop tells a resident something new. | Judge | No stop's narration rests on the facts a guidebook leads with; each gives at least one specific, little-known detail | Must |
| JU5 | It knows what he has already heard. | Gap | Nothing records what a person has been told | — |

## 07 — Aiko, rainy Tuesday, 3 hours

| ID | Criterion | Kind | Pass when | Weight |
|---|---|---|---|---|
| AI1 | Nothing is shut when she arrives. | Rule | Every stop's place is open at its planned time on Tuesday *(source not checked)* | Must |
| AI2 | Walks are short in the rain. | Rule | No leg is 10 minutes or more *(proposed; her day says a ten-minute leg in rain is worse than fifteen in sun)* | Must |
| AI3 | Covered places are used. | Rule | At least one stop's place is covered, by the engine's one covered-place test (`place_is_covered`) | Should |
| AI4 | A closure found on the day is handled. | Rule | X7 passes on her day, and the reply offers a replacement or asks the question | Must |
| AI5 | Shelter on the walk itself counts. | Gap | Nothing records whether a leg is covered | — |

## 08 — Paulo, second-language listener, 2.5 hours

| ID | Criterion | Kind | Pass when | Weight |
|---|---|---|---|---|
| PA1 | Every piece has a transcript. | Rule | Every stop with audio also carries its `narration` text on the reply | Must |
| PA2 | There is time to hear it twice. | Rule | Every stop's planned time is at least 1.5 times its audio length | Must |
| PA3 | Foreign and specialist words are explained. | Judge | Every French term and specialist word ("faubourg", "Jesuit facade") is explained where it first appears | Must |
| PA4 | Sentences are easy to follow by ear. | Judge | No sentence names three or more people, and no idiom is left unexplained | Must |
| PA5 | Dense material is not on the walk. | Judge | Runs of dates and names sit in stop narration, not leg narration | Should |
| PA6 | The densest piece is not last. | Judge | The densest stop is in the first half of the day | Should |

## 09 — Fiona and Dev, would rather talk, 3 hours

| ID | Criterion | Kind | Pass when | Weight |
|---|---|---|---|---|
| FD1 | Pieces are short. | Rule | No stop's audio is over 4 minutes *(proposed; they want four where the corpus has nine)*. The engine already caps a stop's narration at 4.5 minutes. | Must |
| FD2 | There is a place to sit and talk. | Rule | At least one stop's place has `sit_and_talk` set | Must |
| FD3 | Each piece makes its point early. | Judge | In every piece, the main point comes in the first half | Must |
| FD4 | A quiet stretch is planned on purpose. | Gap | A request cannot mark a leg as silent *(source not checked)* | — |
| FD5 | Two people who disagree. | Gap | A request has one decision-maker | — |

## 10 — Greta, day two of five, 3 hours

| ID | Criterion | Kind | Pass when | Weight |
|---|---|---|---|---|
| GR1 | Lunch is planned. | Rule | A sit-down meal of at least 30 minutes is a stop (hers is 40) *(source not checked)* | Must |
| GR2 | Walking is not squeezed out to fit more places. | Rule | Walking is at least 30% of the day *(proposed; hers is 36%)* | Should |
| GR3 | Yesterday is taken into account. | Gap | A request cannot say what she did yesterday | — |
| GR4 | Tiredness with one kind of place fades. | Gap | Nothing records tiredness with a category of place | — |

## 11 — Sofia, December, ends in the dark, 2.5 hours

| ID | Criterion | Kind | Pass when | Weight |
|---|---|---|---|---|
| SO1 | No covered passage after dark. | Rule | No stop whose place is a covered passage starts after 16:30, when the useful light is gone | Must |
| SO2 | Indoor stops are open when she needs them. | Rule | Every indoor stop after 16:00 is open for its whole planned visit *(source not checked)* | Must |
| SO3 | It ends where she asked. | Rule | The finish is within 150 metres of Châtelet, and `planned_end_hhmm` is within the firm end | Must |
| SO4 | Walks after dark use lit, busy streets. | Gap | Nothing records light or footfall on a street | — |
| SO5 | The last stop suits standing in the dark. | Gap | No place is rated for standing alone after dark | — |

Aiko (AI3) and Sofia (SO1) disagree about covered passages on purpose. Both criteria stay. A design
that passes one by failing the other is not accepted.
