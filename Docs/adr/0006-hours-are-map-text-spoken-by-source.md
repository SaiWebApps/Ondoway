---
status: accepted
---

# Hours are OpenStreetMap text, read by a library, and spoken by their source

No official, machine-readable source of opening hours exists for Paris, so no
hours are called verified. Code cites the numbered rules below by number.

1. **One source per door.** Each door records one hours source: map, guess,
   or unknown. The walker hears the hours by that source: map hours plainly,
   a guess as a guess, unknown as "we could not confirm".
2. **Map hours are the raw text, read at planning time.** Map hours are
   stored as the raw OpenStreetMap `opening_hours` text. `opening_hours_py`
   evaluates them at planning time, in the city's own country, so seasonal,
   public-holiday and past-midnight rules are kept instead of being flattened
   into a seven-day table.
3. **The map search, and no review queue.** The map search takes the tourist,
   historic, worship, market, park and garden places matched by name within
   150 m, then every named element with hours matched by the same name within
   150 m. No human review queue exists; nothing waits on a person.
4. **A guess never removes a place.** A guess may say closed but never
   removes a place from a day; only map hours can take a place out. Each
   guessed stop gets one standby, chosen with its audio made at compose time,
   offered as a one-sentence question with "go" as the default. A standby
   always has map hours saying it is open on arrival.
5. **A walker's closed report.** A walker's closed report replans that day
   and marks the guess contradicted for the next walker. One report never
   rewrites hours.
6. **Share-alike.** Because map hours mix with guesses, the OpenStreetMap
   share-alike licence applies: each city's hours file is published openly
   and the app carries a credit line.

## Considered Options

- **The city's heatwave shelter list as an "official" source** — it exists to
  say where to cool off, carries filler hours, and matched wrong buildings.
- **Google or other commercial hours** — their terms forbid storing hours,
  building a place list, and use with text-to-speech.
- **Extending the seven-day table ourselves** — rebuilds a parser that
  already exists and is maintained.
- **A human review floor** — does not scale past one city and leaves the
  product waiting on one person.
