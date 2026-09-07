# A plan obeys every door at the stop's own arrival time

After ordering, the plan simulates each stop's arrival clock and judges every
door against it. A gated stop that is shut at its own arrival and has nothing
to see outside is swapped or dropped at planning time — the walker is never
routed to a shut door that the plan could already see was shut. A replan
re-checks every kept door against the new arrivals the same way. The check
extends the one arrival-window mechanism the clock already has; there is no
second clock.

Ordering itself stays clock-blind: the order is computed first, then judged.
Content of already-served days moves when this lands, so their golden
fixtures are re-baselined under adversarial judgment as named work, not
discovered drift.

## Considered Options

- **Disclosure only** — the day politely announces the shut door the walker
  was still marched to; the failure is the walk, not the wording.
- **Time-window-aware ordering** — teaches the route solver opening windows,
  at a cost and risk the post-ordering simulation captures for a fraction of
  either.
