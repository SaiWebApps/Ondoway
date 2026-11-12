# 11 — Sofia, a winter afternoon that ends in the dark

**Who.** 27, travelling alone, third day of four. Confident, well-travelled and not frightened of
Paris. She has one rule she applies without thinking about it: **she does not walk alone down a
street she cannot see the end of, and she does not stand still anywhere she would not be noticed
if something went wrong.** She would find being asked about this patronising and she would still
apply the rule.

This is about light, sightlines, footfall and how easily she can leave — properties of a street at
an hour, not claims about the people on it. She applies the same rule in her own city.

**The request.** Start Palais-Royal, end Châtelet, two and a half hours, interest: history. Friday
in early December, 14:30, cold and dry. **The sun sets a few minutes before five, and under this
cloud the useful light is gone by half past four.**

**The constraint the form cannot hold.** Her tour is planned once, at 14:30, for a set of streets
that are one thing at the start of it and a different thing by the end.

## The two and a half hours

1. 14:30–14:38 — Eight minutes among the Buren columns in the Palais-Royal courtyard. Still
   bright. **She checks the finish time — 17:00 — and then checks it against sunset
   herself.**
2. 14:38–14:46 — Walks north to the Galerie Vivienne.
3. 14:46–15:06 — Twenty minutes in the Galerie Vivienne and the Galerie Colbert. Covered, lit,
   busy. **The same three properties that made these right for Aiko in the rain make them right for
   Sofia at dusk, and for a completely unrelated reason.**
4. 15:06–15:18 — Twelve minutes up the Rue Vivienne to the Passage des Panoramas. There is a
   shorter way through smaller streets. She takes the wide one without registering that she made a
   choice. **Three of those twelve minutes are a cost she pays and never reports.**
5. 15:18–15:40 — Twenty-two minutes in the Passage des Panoramas and the Passage Jouffroy.
6. 15:40–15:56 — Sixteen minutes walking south down the Rue Montmartre. The light is visibly going
   and she notices she has started walking faster.
7. 15:56–16:14 — Eighteen minutes at the Bourse de Commerce and the Colonne Médicis.
8. 16:14–16:22 — Eight minutes to Saint-Eustache. It is four minutes if she cuts across the open
   ground; she goes round it on the lit side. **The behaviour has already started and it is not
   yet properly dark.**
9. 16:22–16:44 — Twenty-two minutes at Saint-Eustache, seventeen of them inside. **She is inside
   partly because the building is worth it and partly because it is lit, warm, has other people in
   it and it is now dark outside.** Its closing time has quietly become the hardest number in her
   plan, and the app does not have it.
10. 16:44–16:52 — The next leg cuts behind the church across the open ground toward Les Halles.
    She looks at it, sees unlit paving with nobody on it, and walks round on the Rue Rambuteau
    instead. Eight minutes where the plan said five. **She does not tell the app why, and from here
    its finish time is three minutes wrong.**
11. 16:52–17:00 — Eight minutes at the Tour Saint-Jacques. Lit, on a corner with traffic, a bus
    stop and a métro entrance she can see down. **The last stop of a winter tour has to be
    somewhere she is content to stand still in the dark**, and that is not a property any place in
    the corpus carries.

## The arithmetic

**Walking: 52 minutes. At places: 98 minutes.** Six stops, and the last two hours of the tour are
governed by a variable — the light — that appears nowhere in the request, the corpus or the route.

## What this persona breaks

- **The clock changes the map, mid-tour.** Aiko's rain makes walking more expensive everywhere and
  all afternoon; the planner could in principle price that once. Sofia's constraint **switches on
  partway through her own tour**, so the same route is correctly scored at 15:00 and wrongly scored
  at 16:45. A leg has to be priced at the time she will actually walk it.
- **Light and footfall are route properties and nothing records them.** Valhalla can be asked for
  step-free on Rosemary's behalf. It cannot be asked for lit-and-populated. **A shortest path
  across dark open ground is the wrong path even when it is shorter, flat, legal and perfectly
  safe by every measurable standard.**
- **"Covered" is not a fixed virtue, and this directly contradicts persona 07.** Aiko wants covered
  legs and the arcades are her best hour. The same arcades near closing time — gated, emptying,
  one way in and one way out — are legs Sofia declines. **The property the algorithm would store as
  a boolean is a function of the hour.**
- **She reroutes silently and every downstream estimate is wrong** (step 10). She adds three
  minutes and reports nothing. Marcus's finish time would now be a lie he is relying on. Hers
  happens to survive because she has slack; that is luck, not design.
- **Indoors becomes a resource rather than a preference** (step 9). Seventeen minutes inside a
  church is partly her buying somewhere to be. Rosemary needs a bench, Nadia needs a toilet, Sofia
  needs a lit room with people in it — **three personas asking for the same missing idea, which is
  that some stops are for the visitor's body and not for their interest.**
- **The end point needs a different rating after dark than before it.** A station, a floodlit
  monument on a main road, a busy square: these are good places to be left standing at 17:00 in
  December and unremarkable at 17:00 in June. After dusk the planner prefers a finish rated good
  to be left standing in the dark.
- **She will never enter any of this, and unlike Nadia she would probably not answer a direct
  question about it either.** It has to be inferred from the date, the latitude and the finish
  time — three things the system already has, and uses to work out when dusk falls.
