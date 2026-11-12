# 04 — Marcus, three hours between trains

**Who.** 46, in Paris for 190 minutes between a Eurostar arrival and a TGV south. Wheeled cabin
bag. Has done this in four cities and knows exactly how it goes wrong.

**The request.** Start Gare du Nord, end Gare du Nord, three hours, interest: whatever is
genuinely worth it. Thursday, 13:30.

**The constraint that dominates everything.** He must be standing on the platform at 16:40. Not
"about three hours" — **16:40.** Being twelve minutes early costs him nothing. Being four minutes
late costs him £180 and a night in a hotel.

## The three hours

1. 13:30 — Leaves the station. Immediately checks the app's finish time against his watch.
   **The single number he cares about is when this ends.**
2. 13:30–13:40 — Walks with the bag. **Slower than his normal pace, and the wheels make cobbles
   genuinely unpleasant** — a route over Montmartre's setts would be a bad tour even if the
   distance were right.
3. 13:40–13:55 — Fifteen minutes outside Saint-Vincent-de-Paul. He does go in for four of them.
4. 13:55–14:10 — Walks south.
5. 14:10–14:30 — Twenty minutes around the Passage Brady and the covered arcades. Good density,
   short walks, plenty to hear.
6. 14:30–14:45 — Walks to Porte Saint-Denis and Porte Saint-Martin. Fifteen minutes.
7. 14:45–14:52 — **Considers a museum, checks the queue, and does not join it.** A twenty-minute
   posted wait is an unbounded risk to him — it could be forty. **He will not accept any stop
   whose duration he cannot predict.**
8. 14:52–15:20 — Walks a longer leg to the Musée des Arts et Métiers area, listening throughout.
   **This is his best twenty-eight minutes and he never stops moving.**
9. 15:20–15:45 — Twenty-five minutes in the Marais's northern edge.
10. 15:45–16:05 — Starts walking back. **He has already stopped listening properly; he is
    watching the clock.**
11. 16:05–16:20 — Coffee near the station, bought so he can sit within sight of the departure
    board.
12. 16:20 — At the station, twenty minutes early, exactly as he intended.

## What this persona breaks

- **A duration is not a target to hit — it is a ceiling never to cross.** For Marcus, 10% over
  is a missed train. Under-running is nearly free; over-running is catastrophic. **The cost of
  error is asymmetric.** The engine refuses a day longer than the asked time, and a `wall` end
  aims at 95% of it so the slack is planned in (`routing.py`).
- **A plan needs a margin, and the margin should be visible.** He would rather be offered "2h 40
  of tour with 20 minutes of slack" than "3h 00 exactly".
- **Unpredictable stops must be refusable.** A queue is not a duration, it is a distribution. Any
  place whose time cost has a long tail should be excluded when the deadline is hard — and
  included freely for Camille, who does not care.
- **Walking is the product here, not the connector.** Step 8 is the best part of his tour and it
  is a single long leg with continuous narration. A planner that treats walking purely as a cost
  to minimise will never produce it.
- **Luggage changes the route surface.** Cobbles, stairs and bag-check policies all bite.
- **Start and end are the same point, and it is not a sight.** A station is a hard anchor with
  zero content.
