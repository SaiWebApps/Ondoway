# 01 — Camille, the architecture pilgrim

**Who.** 34, first long trip to Paris alone, studied architecture for two years before switching
to something sensible. Fit, patient, happy to stand still for a long time.

**The request.** Start Rue Royale, end Notre-Dame, five hours, interest: architecture.
Wednesday, 10:00, dry and bright.

**The geography.** The two points are 2.5 km apart — about 35 real walking minutes.

## The five hours

1. 10:00 — Stands on Rue Royale, the Madeleine behind her. Opens the app, puts in one earbud,
   phone into a jacket pocket.
2. 10:00–10:12 — Walks south. **Listening while walking.** Maxim's at no. 3, the 18th-century
   shopfronts, Gabriel's twin palaces growing at the end of the street.
3. 10:12 — Reaches Place de la Concorde. **The audio keeps playing after she stops moving.**
4. 10:12–10:32 — Twenty minutes standing. Circles the obelisk, crosses to see the Hôtel de la
   Marine colonnade properly, takes photographs. Traffic is loud; she rewinds thirty seconds she
   missed.
5. The narration for Concorde ran four minutes. She stayed twenty. **Sixteen minutes of silence,
   and she was content.**
6. 10:32–10:40 — Walks into the Tuileries.
7. 10:40–11:00 — Sits on a green metal chair with a coffee from the kiosk. Not looking at
   anything in particular. **Resting is part of the tour, not a gap in it.**
8. 11:00–11:12 — Walks the length of the garden toward the Louvre.
9. 11:12–11:45 — Thirty-three minutes at the Cour Carrée. Walks *into* the courtyard, all the
   way round it, out to the east side for Perrault's colonnade, then stands with her head back
   for a long time. **This is the declared interest spending itself.**
10. 11:45–11:50 — Crosses the road.
11. 11:50–12:08 — **Goes inside** Saint-Germain-l'Auxerrois. Cool and dark. Takes the earbud out
    for a few minutes entirely.
12. 12:08–12:20 — Down to the river and onto Pont Neuf.
13. 12:20–12:32 — Stands on the bridge. **The bridge is a destination, not a connection.**
14. 12:32–13:15 — Lunch on Place Dauphine. Forty-three minutes. The tour is paused in every
    sense except that it is still her afternoon.
15. 13:15–13:22 — Walks to Sainte-Chapelle.
16. 13:22–13:50 — **Queues.** Twenty-eight minutes of security line on the Boulevard du Palais,
    then buys a ticket. Listens to the Sainte-Chapelle narration here, *before* going in, because
    that is when it is useful. **This is not time at Sainte-Chapelle.** It is the price of half
    past one on a bright Wednesday — a different number at opening time, a different one again in
    February — and it belongs to the day, not to the building.
17. 13:50–14:28 — Inside. Thirty-eight minutes under the glass, ten of them sitting down in the
    upper chapel. **Thirty-eight is what the chapel itself is worth to her.** Going in cost her
    sixty-six minutes of her afternoon, because she had to buy the thirty-eight with the
    twenty-eight. Both facts are true and they are not the same fact.
18. 14:28–14:38 — Walks east along the quay.
19. 14:38–15:00 — Notre-Dame. Twenty-two minutes on the parvis and the west front. Leaves.

## The arithmetic

**Walking: 66 minutes. Everything else: 234 minutes.** The two sum to the five hours she asked
for. Audio she actually wants across the whole day: roughly 35 minutes.

The 234 is three different kinds of time, and the split matters more than the total. **163 minutes
at places** — Concorde 20, the Tuileries chair 20, the Cour Carrée 33, Saint-Germain-l'Auxerrois
18, Pont Neuf 12, Sainte-Chapelle 38, Notre-Dame 22. **43 minutes of lunch. 28 minutes of queue.**
Only the first of the three is a property of the places she went to.

**Two different 66s appear in this document and they are not the same number.** Her walking totals
66 minutes. Her Sainte-Chapelle queue plus her time inside also totals 66. Nothing is
double-counted — the walking 66 and the chapel's 28 + 38 sit on opposite sides of the same 300 —
but anyone quoting "66" as Sainte-Chapelle's cost is quoting a wait and a visit added together,
and those two costs behave nothing alike. See the last bullet below.

## What this persona breaks

- **A tour's time is not walking plus narration.** Four fifths of Camille's day is neither. With
  only two ways to spend time, a planner asked for five hours between two close points must
  invent four hours of walking — which is how a Rue Royale tour ends up at Parc de la Villette.
- **A stop is not as long as its audio.** A stop lasts *the longer of* its visit time and its
  narration (`visit_time.py`). Camille needs the Cour Carrée to last as long as it deserves.
- **Silence is not a defect.** Any rule that fills dwell time with speech would ruin step 5.
- **Narration is heard on the move, and sometimes deliberately before arrival** (step 16).
- **Lunch and a queue are real time.** Neither is walking, and neither is sized by narration — she
  listens through part of the queue and through none of the lunch.
- **A queue is not visit time, and it must not become a place's capacity.** Camille's
  Sainte-Chapelle is 38 minutes of chapel and 28 minutes of line. Price the building at a single
  66 and the line becomes permanent: it cannot shrink at opening time, it cannot be skipped by
  someone who only wants the outside, and Marcus — who refuses queues outright because a queue is
  a distribution rather than a duration (`04-layover-sprinter.md`, step 7) — cannot decline the
  wait without also declining the interior. **Three numbers, not one: outside, inside, and a wait
  that belongs to the hour and the season.** A planner that budgets only the 38 overruns Camille's
  afternoon by 28 minutes, so the wait cannot simply be dropped either.
