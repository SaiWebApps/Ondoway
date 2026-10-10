# What one walking tour costs us to make

Measured on 2026-10-09 with real paid calls, New York, 16 tours (6 served).

## Outcome

| Tour length | Tours served | Average cost | Lowest | Highest | Average stops |
|---|---|---|---|---|---|
| 60 min | 2 of 3 | $0.52 | $0.49 | $0.55 | 1 |
| 90 min | 4 of 8 | $1.02 | $0.72 | $1.45 | 2 |
| 180 min | 0 of 5 | – | – | – | – |

- Cost per narrated stop: **$0.51**.
- Total spent on this measurement: **$9.49**, including tours that were refused.
- 10 tour(s) were refused after money was spent. That money is counted in the total and in the 'with refusals' column of the JSON.

## Where the money goes

| Part of the work | Share of cost |
|---|---|
| Opus writes each stop | 50% |
| Opus writes the longer telling for a main stop | 40% |
| Haiku checks each sentence against the sources | 3% |
| Haiku writes linking lines | 0% |
| Voice for each stop's narration | 7% |
| Voice for closes, links and walking lines | 1% |

## Each tour

| # | Start | Length | Party | Stops | Longer tellings | Standbys | Rewrites | Writing $ | Voice $ | Total $ |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | Washington Square Park | 60 min | solo | 1 | 1 | 0 | 0 | 0.44 | 0.05 | 0.49 |
| 1 | Bryant Park (refused) | 60 min | family | 0 | 0 | 0 | 0 | 0.00 | 0.00 | 0.00 |
| 2 | Federal Hall | 60 min | solo | 1 | 1 | 0 | 0 | 0.51 | 0.04 | 0.55 |
| 3 | Union Square (refused) | 90 min | solo | 0 | 0 | 0 | 0 | 0.21 | 0.00 | 0.21 |
| 4 | Rockefeller Center (refused) | 90 min | family | 0 | 0 | 0 | 0 | 0.00 | 0.00 | 0.00 |
| 5 | City Hall (refused) | 90 min | solo | 0 | 0 | 0 | 0 | 0.00 | 0.00 | 0.00 |
| 6 | Flatiron Building (refused) | 90 min | family | 0 | 0 | 0 | 0 | 0.00 | 0.00 | 0.00 |
| 7 | Grand Central Terminal (refused) | 180 min | solo | 0 | 0 | 0 | 2 | 1.31 | 0.00 | 1.31 |
| 8 | Washington Square Park (refused) | 180 min | family | 0 | 0 | 0 | 0 | 0.00 | 0.00 | 0.00 |
| 9 | Bowling Green Park (refused) | 180 min | solo | 0 | 0 | 0 | 1 | 0.96 | 0.00 | 0.96 |
| 10 | Grand Central Terminal | 90 min | solo | 2 | 2 | 0 | 0 | 1.37 | 0.08 | 1.45 |
| 11 | Wall Street | 90 min | solo | 2 | 1 | 0 | 0 | 0.72 | 0.07 | 0.79 |
| 12 | Union Square | 90 min | solo | 2 | 1 | 0 | 0 | 0.65 | 0.08 | 0.72 |
| 13 | Times Square | 90 min | solo | 2 | 2 | 0 | 0 | 1.06 | 0.08 | 1.14 |
| 14 | Flatiron Building (refused) | 180 min | solo | 0 | 0 | 0 | 1 | 1.21 | 0.00 | 1.21 |
| 15 | Central Park Bethesda (refused) | 180 min | solo | 0 | 0 | 0 | 0 | 0.66 | 0.00 | 0.66 |

Token counts, call counts, characters and audio seconds for every part of every tour are in `tour-cost-measurement.json`.

## Evidence

- Each tour went through the same three steps the app uses: plan the route, write the words, record the voice.
- The plan step made no paid calls on any tour. The script checks this.
- Every paid call was counted where it leaves the machine, using the usage numbers the provider sent back. Nothing was estimated except the voice text tokens.
- Prices used (checked 2026-10-09):
  - Opus 4.8: $5 per million tokens in, $25 out. Haiku 4.5: $1 in, $5 out.
  - Cached input: 1.25 times the input price to write, 0.1 times to read.
  - Voice (gpt-4o-mini-tts, voice nova): $0.015 per minute of audio, plus $0.60 per million text tokens (characters divided by 4).
- Opus thinking is billed as output and is already inside the output counts.

## Not measured

- Audio made only when a listener taps for more: the longer telling of a main stop and the 'keep exploring' extras. Their text sizes are in the JSON, so their cost can be worked out if they are voiced.
- New stops written when a walker replans mid-walk.
- Servers, database, storage and bandwidth.
- The half-price batch option. The app writes tours live, so it does not apply.
- Cities other than New York.

## Limits of this sample

- Every served tour was solo, 60 or 90 minutes, with 1 or 2 stops. Six tours is a small sample.
- A day in a 5-day pass may be longer. Scale by the cost per narrated stop.
- Every family tour was refused at the plan step: no start tried could fill 60 minutes or more.
- Every 180-minute tour was refused while writing, after money was spent.
- The runs used a working copy with uncommitted changes in src/tour/contingency.py.
