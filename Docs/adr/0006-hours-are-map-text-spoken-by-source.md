---
status: accepted
supersedes: ADR-0003
---

# Hours are OpenStreetMap text, read by a library, and spoken by their source

No official, machine-readable source of opening hours exists for Paris, so
"verified by two independent sources" cannot be met and is retired. Each door
records one hours source — map, guess, or unknown — and the walker hears the
hours by that source: map hours plainly, a guess as a guess, unknown as
"we could not confirm". Map hours are stored as the raw OpenStreetMap
`opening_hours` text and evaluated at planning time by `opening_hours_py`,
so seasonal, holiday and past-midnight rules are kept instead of being thrown
away by a flat seven-day table. The map search takes every named element
with hours, matched by same name within 150 m. No human review queue exists;
nothing waits on a person.

A guess may say closed but never removes a place from a day. A walker's
closed report replans that day and marks the guess contradicted; one report
never rewrites hours. Each guessed stop gets one standby, chosen with its
audio made at compose time, offered as a one-sentence question with "go" as
the default. A standby always has map hours saying it is open on arrival.

Because map hours mix with guesses, the OpenStreetMap share-alike licence
applies: each city's hours file is published openly and the app carries a
credit line.

## Considered Options

- **The city's heatwave shelter list as an "official" source** — it exists to
  say where to cool off, carries filler hours, and matched wrong buildings.
- **Google or other commercial hours** — their terms forbid storing hours,
  building a place list, and use with text-to-speech.
- **Extending the seven-day table ourselves** — rebuilds a parser that
  already exists and is maintained.
- **A human review floor** — does not scale past one city and leaves the
  product waiting on one person.
