# 02 — Théo, the dark-history walker

**Who.** 29. Reads about revolutions, executions and plague pits for fun. Bored by cornices.

**The request.** Identical to Camille's in every respect except one word: start Rue Royale, end
Notre-Dame, five hours, interest: dark history. Same Wednesday, same 10:00, same weather.

**Why this persona exists.** It is the controlled experiment. One variable changes. If the
algorithm cannot make these two days genuinely different, the lens is decoration.

## The five hours

1. 10:00–10:10 — Walks Rue Royale. The narration he wants is not about the shopfronts: it is
   that this street was the route to the scaffold, and that in 1770 a fireworks crush here killed
   over a hundred people at the Dauphin's wedding.
2. 10:10–10:45 — **Thirty-five minutes at Place de la Concorde.** He wants to stand on the spot.
   Where exactly was the guillotine? Which way did Louis XVI face? He walks between three
   different positions on the square working it out. Camille gave this twenty minutes; he gives
   it thirty-five, and he wants twelve minutes of audio, not four.
3. 10:45–10:55 — Cuts through the Tuileries without stopping. **The garden is a corridor to
   him.** Camille sat here for twenty minutes.
4. 10:55–11:03 — Pauses briefly where the Tuileries Palace stood before the Commune burned it.
   Eight minutes. A place with no building left is still a stop.
5. 11:03–11:11 — Walks to the Louvre.
6. 11:11–11:19 — **Eight minutes at the Cour Carrée**, and most of that is one story: the
   St Bartholomew's Day massacre began with the bell of the church across the road. Camille spent
   thirty-three minutes here.
7. 11:19–11:22 — Crosses the Place du Louvre to the church. Three minutes. Camille takes five over
   the same crossing because she stops in the middle of it to look back.
8. 11:22–11:38 — Saint-Germain-l'Auxerrois. Sixteen minutes, and he wants to be shown *the bell*,
   not the tracery.
9. 11:38–11:50 — **Walks** east along the Rue de Rivoli to the Hôtel de Ville. Twelve minutes,
   listening the whole way.
10. 11:50–12:10 — **Twenty minutes standing on Place de Grève**, in front of the Hôtel de Ville —
    Paris's execution ground for five centuries. Nothing is left to look at, so the twenty minutes
    are him being told where things stood. **This stop is not on Camille's route at all.**
11. 12:10–12:50 — Lunch. Forty minutes.
12. 12:50–13:00 — Walks to the Île de la Cité.
13. 13:00–13:15 — Sainte-Chapelle from the outside only. Fifteen minutes. **He does not go in and
    does not queue.** Camille gave the chapel thirty-eight minutes inside and paid twenty-eight
    more to get in.
14. 13:15–14:20 — **The Conciergerie, sixty-five minutes inside.** The medieval hall, the
    revolutionary tribunal, Marie-Antoinette's cell. This is the anchor of his whole day.
    Camille skipped it entirely.
15. 14:20–14:31 — Walks the length of the island, east along the quay past Notre-Dame's flank.
    Eleven minutes.
16. 14:31–14:40 — Nine minutes at the deportation memorial, almost all of it silent.
17. 14:40–14:45 — Five minutes back west round the apse to the parvis.
18. 14:45–15:00 — Notre-Dame, fifteen minutes, and the story he wants is the Revolution stripping
    it and rededicating it to the Cult of Reason. **It is his last stop because the request named
    it, not because he wanted it** — Camille, who chose it, gives it twenty-two.

## The arithmetic

**Walking: 69 minutes across eight legs. Standing and inside: 191 minutes. Lunch: 40.** They sum to
the five hours he asked for. Camille's five hours split 66 walking, 163 at places, 43 of lunch and
28 of queue. **Both of them spend roughly four fifths of the day not walking, and the two days look
nothing alike** — which is the whole reason this file exists.

> **Corrected 2026-08-06.** Three lines here previously reported a walk and a stop as one number.
> "Thirty-five minutes at Place de Grève" was really twelve minutes of the Rue de Rivoli and twenty
> minutes of standing; the trip to the deportation memorial hid an eleven-minute walk inside a
> fifteen-minute stop; and Théo crossed the Place du Louvre in zero minutes, a crossing Camille is
> given five for. Every leg above is now a leg and every stop is a stop, and no stated duration
> moved except the three the folds were concealing. **In a document whose only argument is that
> standing still is most of the day, a walk filed as dwell is not a rounding error — it is the
> claim being quietly reversed.**

## Camille and Théo compared

Same start, same end, same five hours. Between them they stop at eleven distinct places, counting
lunch as neither. **They share five of the eleven, and at three of those five the time they spend
is wildly different.**

- **Concorde** — 20 minutes for her, 35 for him.
- **The Cour Carrée** — 33 and 8.
- **Sainte-Chapelle** — she spends 38 minutes inside and 28 more in the line to get in; he spends
  15 on the pavement and never joins the queue. **Her 28 minutes are not a property of the chapel**
  (see `01-architecture-pilgrim.md`, the last bullet), so the pair to compare is 38 against 15.
- **Saint-Germain-l'Auxerrois** — 18 and 16, near enough identical, for completely different
  reasons.
- **Notre-Dame** — 22 and 15. She chose it; he only ends there because the request said so.

The other six places belong to exactly one of them, and the largest single number in either day is
one of those: **the Conciergerie is 0 for Camille and 65 for Théo.**

## What this persona breaks

- **An interest must change how long you stay, not only where you go.** This is the hardest
  requirement in the whole set, and the engine prices it in `visit_time.py`. Visit time is a
  property of the place *and* the visitor together.
- **An interest must change the amount of narration a place gets.** Concorde is four minutes for
  Camille and twelve for Théo.
- **A place with nothing left standing can be a stop** (step 4). Importance tier and beat count
  will both under-rate it.
- **An interest can make a landmark not worth entering** (step 13). Any rule of the form "a
  tier-5 anchor always earns a long visit" is wrong here.
- **A fixed end point is a constraint, not a stop worth planning around** (step 18). Notre-Dame is
  where the request says he finishes, and it gets fifteen minutes — less than the walk out to the
  memorial and back, and less than a quarter of the Conciergerie. A planner that treats the named
  endpoint as the climax gets his whole shape wrong.
