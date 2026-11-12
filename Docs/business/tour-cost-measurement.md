# What Ondoway costs to run, as built today

Measured on 11 November 2026 in New York, with real paid calls.

## The cost structure

| Cost | When it is paid | Amount |
|---|---|---|
| Recipes for a city | Once per city, before launch | about $537 for New York (1,455 recipes at $0.37 each) |
| A stop told from a recipe | Each stop a walker hears | $0.015 |
| A stop written fresh | Each stop a walker hears | $0.50 |

A tour costs $0.015 for each stop told from a recipe and $0.50 for each stop written fresh.

## Which stops are written fresh

A stop is told from its place's recipe when all of these hold:

- The walker is solo.
- The tour has one lens or none.
- The visit stays outside the door.
- The stop is not the day's last.

Every other stop is written fresh. The day's last stop always is, because it closes the day.

## The measured tour

A 150-minute solo walk from Bethesda Terrace, Central Park, with no lens. It has 3 stops: Bethesda Terrace, Grand Army Plaza (Manhattan), Radio City Music Hall.

| Stop | Told | Cost |
|---|---|---|
| Bethesda Terrace | From its recipe | $0.015 |
| Grand Army Plaza (Manhattan) | From its recipe | $0.015 |
| Radio City Music Hall | Written fresh | $0.50 |

- The tour cost $0.53.
- Writing took 3 min 27 s, and voicing 30 s.
- At a stop told from a recipe, the walker also gets the place's longer telling, already recorded, when they tap for more.

## How it was measured

- The tour went through the app's own steps: write the words, then record the voice.
- Every paid call was counted where it leaves the machine, from the usage the provider reported.
- Each stop is charged its own writing and voice. The fact checks and short linking lines ($0.04 for the tour) are split evenly across the stops.
- Building the recipes for Bethesda Terrace and Grand Army Plaza (Manhattan) cost $0.74, so $0.37 each. Only the first has a longer telling; a place gets one when it has more to tell than its short telling holds.
- A city needs one recipe per place, plus one per lens its beats carry. New York has 396 places and needs 1,455 recipes.
- Prices:
  - Claude Opus 4.8: $5 per million tokens in, $25 out.
  - Claude Haiku 4.5: $1 per million tokens in, $5 out.
  - Cached input: 1.25 times the input price to write, 0.1 times to read.
  - OpenAI gpt-4o-mini-tts, voice nova: $0.015 per minute of audio, plus $0.60 per million text tokens (characters divided by 4).

## Not counted

- Voicing the longer telling a walker taps for at a stop written fresh.
- Stops written again when a walker's day is replanned.
- Servers, storage and the database. The money model counts these.

## Limits

- This is one tour and two recipes. The same tour's cost varies by up to 20% from run to run.
- A recipe's cost depends on how much its place has to tell. Other cities' recipe counts are not known yet.
