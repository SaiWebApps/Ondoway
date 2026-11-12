# 05 — Rosemary, 78, step-free

**Who.** 78, sharp as anything, one replaced knee and one that needs replacing. Walks with a
stick. Has been coming to Paris since 1968 and is not here to be patronised.

**The request.** Start Musée d'Orsay, end Musée d'Orsay, three hours, interest: art and the
19th century. **Wednesday**, 14:00.

> **Corrected 2026-08-06.** This persona originally said Tuesday, and then sent her into the
> Musée de l'Orangerie — which closes every Tuesday. The document committed the exact failure its
> own closing section warns about, which is a fair measure of how easily this class of bug hides.
> The day is now Wednesday and the walkthrough is coherent. **Had she asked for Tuesday, this same
> route would be broken**, and that is the point the last bullet makes.

**What she needs and the form does not ask.** No steps. Nowhere more than about twelve minutes'
walk from a bench. A toilet she can plan around. She would rather stand and look at one thing for
forty minutes than see four things.

## The three hours

1. 14:00–14:10 — Ten minutes on the Orsay forecourt before going anywhere, looking at the old
   station clock face from outside.
2. 14:10–14:22 — **Twelve minutes of walking, and that is her limit in one go.** Along the quay
   toward Pont Royal.
3. 14:22–14:35 — **Sits on a bench for thirteen minutes.** She listens to a five-minute piece
   about the Seine's quays while resting. **The bench is a stop. It has a location, a duration,
   and content — it simply is not a sight.**
4. 14:35–14:44 — Nine minutes' walk across Pont Royal. Flat, no steps. **The riverside stairs
   down to the water, which any shortest-path router would love, are unusable to her.**
5. 14:44–15:05 — Twenty-one minutes in the Tuileries' western end. Two more sits inside that
   stretch, on chairs by the round pond.
6. 15:05–15:14 — Nine minutes' walk.
7. 15:14–15:22 — **Toilet.** Eight minutes, and she chose this tour partly because she knew where
   one was.
8. 15:22–15:34 — Twelve minutes' walk toward the Musée de l'Orangerie.
9. 15:34–16:20 — **Forty-six minutes inside the Orangerie**, most of it sitting on the central
   bench in front of the Nymphéas. She listens to eleven minutes of audio spread across it and
   is quiet for the rest.
10. 16:20–16:32 — Twelve minutes' walk back east along the terrace.
11. 16:32–16:45 — Sits. Thirteen minutes.
12. 16:45–17:00 — Final fifteen minutes back to Orsay, arriving satisfied and tired.

**Walking: 54 minutes across six legs, none longer than twelve. Sitting, resting and inside:
126 minutes.**

## What this persona breaks

- **A walking budget is not one number.** Rosemary's total (54 minutes) is unremarkable. Her
  *per-leg* limit (12 minutes) is the binding constraint, and the request carries it as a cap.
  A route with one 25-minute leg and one 5-minute leg has the same total and is unusable.
- **Rest is a first-class stop.** It has a place, a length and content. The corpus holds benches
  and toilets as rest places, and the planner seats one as a stop with no beats.
- **The route surface matters as much as the route length.** Steps, kerbs and cobbles. The
  planner asks Valhalla for a step-free route when the request says so.
- **Fewer, longer stops is a legitimate shape.** One 46-minute stop out of a three-hour tour.
  Any rule that spreads time evenly, or that requires a minimum number of stops, produces a
  worse tour for her.
- **The day of the week matters.** The Orangerie and the Louvre both close on Tuesdays. Run this
  identical request one day earlier and step 9 spends 24 of her 54 total walking minutes reaching
  a locked door, and burns one of her handful of rest windows standing outside it. A plan that
  does that has failed completely regardless of how good its arithmetic is.
