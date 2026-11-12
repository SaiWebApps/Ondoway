# Tour personas — the ground truth the algorithm has to satisfy

Eleven people, each written as a literal list of what they do minute by minute. They exist
because the planner's model of a tourist has drifted away from tourists, and prose about
"quality" could not show the drift. A list of steps can.

**Rule for using these.** A design is not finished because it is elegant. It is finished when
it can represent all eleven of these days. When a proposal cannot hold one of them, say which
one and what it costs — do not quietly redefine the persona to fit the algorithm.

Each persona breaks a different assumption on purpose:

- **[01 — Camille, the architecture pilgrim](01-architecture-pilgrim.md)** — five hours,
  Rue Royale to Notre-Dame. Spends 234 of her 300 minutes standing still. Breaks the idea that
  a tour's time is walking plus narration.
- **[02 — Théo, the dark-history walker](02-dark-history-walker.md)** — identical corridor,
  identical five hours, different interest. Must produce a different route *and* different
  time at the same buildings. Breaks the idea that an interest only changes which stops get
  picked.
- **[03 — Nadia, two hours with two children](03-family-with-children.md)** — needs toilets,
  snacks and somewhere to run. Breaks the idea that a longer stop is a better stop, and that
  a tour is completed rather than abandoned.
- **[04 — Marcus, three hours between trains](04-layover-sprinter.md)** — must be back at
  16:40 or he misses it. Breaks the idea that the requested duration is a target to hit rather
  than a deadline never to exceed.
- **[05 — Rosemary, 78, step-free](05-step-free-visitor.md)** — twelve minutes of walking at a
  time, then a bench. Breaks the idea that a walking budget is one number.
- **[06 — Julien, who lives here](06-resident-novelty-seeker.md)** — has seen Notre-Dame forty
  times. Breaks the idea that importance is what makes a stop worth visiting.
- **[07 — Aiko, a wet Tuesday](07-rainy-tuesday.md)** — same request, different weather and
  different weekday. Breaks the idea that a start point, a duration and a lens are enough
  input to plan with.
- **[08 — Paulo, listening in his second language](08-second-language-listener.md)** —
  comfortable English, not fluent. Slows the audio to 0.9×, plays the best piece twice, and
  reads one stop instead of hearing it. Breaks the idea that a piece of narration costs the
  number of seconds it lasts.
- **[09 — Fiona and Dev, who would rather talk](09-couple-who-would-rather-talk.md)** — three
  hours in which the audio competes with their own conversation and mostly loses. Breaks the
  idea that silence only has to be permitted, rather than deliberately planned.
- **[10 — Greta, day two of five](10-day-two-of-five.md)** — four hours in the Louvre yesterday
  and she cannot face another gallery today, though she will again on Thursday. Breaks the idea
  that a request can be planned without knowing what the visitor did the day before.
- **[11 — Sofia, a winter afternoon that ends in the dark](11-solo-after-dark.md)** — the same
  street at 15:00 and at 16:45 is not the same street. Breaks the idea that a route can be
  scored once for the whole tour.

## What all eleven have in common

1. **Standing still is most of the day.** In every persona here, time spent at places exceeds
   time spent walking between them — Paulo 109 against 41, Sofia 98 against 52, Greta 116
   against 64. The engine prices time at a place as well as walking and talking (`visit_time.py`).
2. **Narration and walking overlap, and for most of them it is free.** They listen on the move;
   several also listen while queueing, and one takes the earbud out entirely inside a church.
   **Two personas complicate this in opposite directions.** Fiona and Dev mute the audio on six of
   their seven legs, because a voice in the ear is in the way of each other — the overlap is not
   free to them, it is unwanted. Paulo listens on every leg and loses content on every one,
   because rewinding on a narrow pavement means stopping and being walked into — so the minutes
   the engine treats as costless are the most expensive listening of his day. **Overlap is a
   default, not a law, and it is not free for everybody.**
3. **Silence is normal and fine.** Camille stays twenty minutes at Concorde for four minutes of
   audio and does not feel short-changed. Fiona and Dev go further: they want the quiet
   *scheduled*, on a named stretch, and that is the harder version of the same requirement.
4. **The same place costs different amounts of time to different people.** The Cour Carrée is 33
   minutes for Camille, 8 for Théo and 16 for a couple who mostly talked through it. Concorde is
   20, 35 and 6. The Palais-Royal is 8 minutes for Sofia and 34 for Greta. **This is the single
   hardest requirement in the set**, and every persona added since has widened the spread rather
   than narrowed it.
5. **Some constraints are not about time at all.** A toilet, a bench, a step, an opening hour, a
   bag that will not be allowed through security, a transcript, a place quiet enough to talk in,
   a lit street at half past four in December.
6. **A queue is a third kind of time, and it is nobody's capacity.** Camille's Sainte-Chapelle is
   38 minutes of chapel plus 28 minutes of line. Marcus refuses queues outright because their
   length is a distribution rather than a duration. Price the wait into the building and neither
   position can be expressed.

## Where two of them contradict each other

This is not a defect in the documents and it must not be resolved by editing one of them.

Aiko (07) wants **covered** legs: in steady rain an arcade is worth a detour and the Galerie
Vivienne turns from a footnote into an anchor. Sofia (11) refuses a covered leg once it is dark
and emptying — gated at both ends, one way in and one way out. **The property an implementation
would naturally store as a boolean on a route segment is a function of the hour**, and any design
that satisfies one of these two by making "covered" a fixed virtue has failed the other.
