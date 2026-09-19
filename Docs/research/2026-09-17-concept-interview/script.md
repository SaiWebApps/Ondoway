# Prototype script — screen by screen

What the traveller sees, hears and can tap, in order. The prototype builds exactly this and
decides nothing on its own. Questions the founder asks live in `interview-guide.md`;
session plan in `brief.md`; terms in `Docs/traveller/CONTEXT.md`.

**Markers.** ⚑ = proposed by Claude and **accepted by the founder 2026-09-18** — build as
written. ✎ = founder writes it (verdicts and child prompts he must stand behind); build
with a visible TODO placeholder. Everything unmarked is decided or is real corpus text.

**Story text** is real `script_body` from `data/new_york/beats.json`, chosen and never
edited. **Captions** (the one line under each screen that makes it self-explaining) are
product copy and are written here in full.

---

## Look and feel — the real app

Every screen copies the Flutter app (`mobile/lib`), so people see what it will look like.

- **Tokens** (`theme/tokens.dart`): accent #2C6CC0, accentDeep #1E4F92, accentLight #7BB2F5,
  bg #E9E5DB, card #FFFFFF, panel #F6F4F0, ink #20242C, inkMute #5B6069, line #DED8CB,
  spark #E8934A. Walk screen is dark: bg #101218, card #20242C.
- **Type:** Fraunces for display/titles, Space Grotesk for body/labels, Space Mono for
  uppercase eyebrows, stats and times (letter-spacing 2).
- **Shape:** cards radius 20 (hero 28), pills 999, shadow `0 4px 14px rgba(32,36,44,.08)`.
- **Lens chips** (`widgets/lens_chip.dart`): pill, icon + label; unselected = lens colour at
  16% fill / 60% border; selected = solid lens colour, white text, check, slight pop.
- **Bottom nav** (`widgets/app_shell.dart`): floating white pill, tabs Explore · Trips ·
  Profile, selected tab = cobalt pill with label.
- **Caption strip** (new, prototype only): a slim bar under the phone frame, Space Grotesk
  15, ink on panel. One line per screen. It is the only explanation the traveller gets.

**Frame:** one phone centred on the laptop screen. For the companion moment (screen 3.3)
the frame splits into two phones side by side, and for I Spy (6.1) a third, smaller phone
slides in.

**Controls the traveller always has:** Next (→ key or button under the caption),
Back, and inside the walk: pause. Nothing else is required to get through.

---

## The party

Shown once on screen 0.1, used throughout.

| Person | Role | Lenses |
|---|---|---|
| **Maya** | the Planner — "you" | Historic Architecture · Hidden History · Visual Art |
| **Dan** | her partner | War & Conflict · Local Legends · Historic Cuisine |
| **Leo**, 12 | own phone | Science & Technology · Film & TV Locations |
| **Ava**, 8 | own phone, I Spy | — (kids' view) |

⚑ Names and lenses. Maya's and Dan's are chosen so their Federal Hall stories differ
sharply (architecture vs. the Revolution).

---

## 0 · Opening

**0.1 Meet Maya** — *new screen, bone bg, Fraunces headline*
- Photo placeholder of a family on a city street. Headline: "Four days in New York."
- Body: "You're Maya. You're taking Dan, Leo (12) and Ava (8) to New York for four days.
  You've heard about Ondoway. Let's see what it does."
- Button: **Start**.
- Caption: *"Click through as if this were your phone. Think out loud."*

---

## Step 1 · Lenses — personalised to you

**1.1 What are you curious about?** — *= Lens picker (`lens_selection_page.dart`)*
- Eyebrow "WELCOME, MAYA". Title "What are you curious about?". Progress bar to 3.
- All 21 lens chips as in the app. Maya's three are pre-selected.
- **Lens explanation (revised 2026-09-18, reviewer feedback: "explain what each lens means"):**
  a docked card below the chips explains the most recently tapped lens (on or off) in one
  plain line, e.g. "Historic Architecture — how buildings were designed and built: the
  architects, materials and styles." ⚑ 21 one-liners, rewritten for travellers from the
  extraction definitions in `.claude/commands/beat-from-book.md`; they say only what that
  lens's stories are about.
- ~~"What you'd hear at Federal Hall" opening-line card~~ — **dropped 2026-09-18 (founder).**
  3.3 carries "same place, different story" with real audio, and without the sample the
  step 1 question "What do you think those choices will change?" actually tests understanding.
- Footer "3 selected" · **Continue**.
- Caption: *"Pick what you're into. Tap any lens to see what it covers."* (revised 2026-09-18)
- Log: every chip toggled.

**1.2 Who's coming?** — *new screen in the app's style (from the journey spec's "Profile &
people")*
- Title "Who's coming?". Four rows: avatar circle (accent, initial), name, their lens chips
  (small), and for Ava a pill "I Spy". Dan and Leo show "Joined ✓".
- Caption: *"Everyone brings their own phone and their own interests."*
- **How and why (revised 2026-09-18, reviewer feedback: "make it clearer how users are added
  and why it matters"):**
  - Under the title: "Invite your party with a link. Each person joins on their own phone and
    picks their own lenses."
  - Row status in words: Dan "Joined · picked 3 lenses", Leo "Joined · picked 2 lenses",
    Ava "Joined · kids' view with I Spy".
  - **+ Invite someone** → pretend share sheet (Messages · WhatsApp · Copy link); nothing is
    sent; closes with "Invite ready — in the real app this sends a link." Logged.
  - Card: "Why invite them? At the same stop, each of you hears the story that fits you."
  - Deliberately absent: price or the household pass (would anchor H11's "who would it need to
    cover?") and comparing stories at dinner (tested by 7.2 and the step 7 questions, H3).

---

## Step 2 · Planning the day — filling in between the big sites

**2.1 Plan your trip** — *= Plan trip (`trip_duration_page.dart`), restyled to brand tokens*
- Title "New York". Rows: **Dates** "Thu Oct 8 – Sun Oct 11 · 4 days".
- **Must-sees** (Maya's, pre-filled chips): 9/11 Memorial · Statue of Liberty · The Met ·
  Central Park. A faint "+ Add" chip.
- **Booked:** "Statue of Liberty ferry · Fri 2:00 PM" (a ticket row).
- **Adding a must-see (revised 2026-09-18, reviewer feedback: "let the user see what adding a
  must-see looks like"):** "+ Add" opens a search sheet ("Search New York…") over a fixed list
  of ~8 corpus landmarks; typing filters it; tapping one adds a chip with a small pop.
  Every option lands on a day other than Friday (Friday stays scripted) ⚑:
  Empire State Building, Grand Central Terminal, The High Line → Thu; Solomon R. Guggenheim
  Museum, American Museum of Natural History → Sat; Brooklyn Bridge Park → Sun. Top of the Rock
  and the Brooklyn Bridge are already planned, so they answer "Already on Thursday/Sunday"
  instead of being added twice. Anything typed that is not listed: "Try one of these for the
  demo." The four pre-filled must-sees cannot be removed. On 2.3 the added place
  shows as a ★ on its day card with a one-line why, e.g. "Added to Thursday — it's near your
  Midtown arrival." Log: sheet opened, search text, place added.
- Button **Build my trip**.
- Caption: *"Tell it what you already want to see. It plans everything around that."*

**2.2 Building** — *spinner card, 2 seconds, auto-advances*
- Lines tick in: "Reading 4 people's interests…" · "Fitting your must-sees…" · "Finding
  what's worth your time between them…" · "Checking what's worth skipping…"

**2.3 Your trip** — *new, in itinerary card style*
- Four day cards, each: day, area, must-see stars, stop count.
  - Thu · Midtown arrival — Top of the Rock at sunset ⚑
  - **Fri · Lower Manhattan** — ★ 9/11 Memorial ★ Statue of Liberty — 6 stops
  - Sat · Central Park & the Met — ★ ★ ⚑
  - Sun · Brooklyn Bridge & DUMBO ⚑
- Friday card is highlighted; only it opens.
- Caption: *"Four days, built around your must-sees. Open Friday."*

**2.4 Friday** — *= Itinerary (`trip_itinerary_page.dart`)*
- Eyebrow "YOUR TOUR". Title "Friday · Lower Manhattan".
- Summary stats: **Stops** 6 · **Walk** 3.1 mi ⚑ · **Must-sees** 2 · **Stories** 14 ⚑.
- Stop cards (app style: number circle, name, lens pill, minutes, start time). Must-sees
  get the accent border and spark star. **Every added stop carries its Stop reason** in
  one line of inkSoft italic under the name:

  | Time | Stop | Kind | Stop reason (⚑ wording) |
  |---|---|---|---|
  | 9:30 | 9/11 Memorial | ★ must-see | — |
  | 11:15 | Trinity Church | added · Historic Architecture | "The church that was once the tallest thing in New York — and Hamilton's grave." |
  | 11:45 | Federal Hall | added · Historic Architecture | "The 'Parthenon of Wall Street', on the spot where Washington became president." |
  | 12:30 | Fraunces Tavern | added · Hidden History | "Lunch where Washington said farewell to his officers in 1783." (the corpus disagrees on how much of the building is original, so the reason avoids it) |
  | 1:30 | Castle Clinton | added · Hidden History | "Where you buy ferry tickets: a fort, a concert hall, then the gate for 8 million immigrants." |
  | 2:00 | Statue of Liberty | ★ must-see · ferry | — |

- **Skip advice card** (spark left border, between Federal Hall and Fraunces):
  - Heading "We'd skip: ✎"
  - Body ✎ — founder writes. Two drafts grounded in the corpus for him to accept or replace:
    - "The crown. Tickets go up to six months ahead, four per person, and kids must be
      42 in tall. The pedestal view is the one worth having." (Liberty `local_legends` beat)
    - "The New York Stock Exchange. You can't go in — see it from Federal Hall's steps,
      it's across the street." ⚑ needs a source or founder verdict.
- Button **Start walking** (the app's FAB label).
- Caption: *"The big two are yours. Everything in between was picked for you — and it tells
  you why."*
- Log: time on screen, taps on stop cards, taps on the Skip advice card.

---

## Step 3 · The day of — real audio and text

**3.1 Walk** — *= Walk (`tour_walk_page.dart`), dark theme*
- Full-bleed map (static Lower Manhattan map image or simple SVG ⚑), route line in cobalt,
  stop pins, a blue dot. Top-left close X.
- The dot **moves by itself** from stop to stop (≈6 s per leg). On arrival the now-playing
  card rises and **audio starts automatically**.
- **Now-playing card** (the app's): headphones tile, lens name in spark Space Mono, stop
  name in Fraunces, "Stop n of 6", scrubber, prev / big cobalt play-pause / next.
- **New: transcript** — a "Text" toggle on the card opens the story text, highlighted in
  sync with the audio.
- Between stops: the app's walking bar, "Walk to the next stop — audio starts on arrival".
- Stories on this leg (Maya's lenses):
  1. **9/11 Memorial** — `modern_design`, 30 s: "Surrounding the museum is the eight-acre
     Memorial Plaza, with two reflecting pools…"
  2. **Trinity Church** — `historic_arch`, 57 s: "This is actually the third Trinity Church
     on the site…"
  3. **Federal Hall** — see 3.3.
- Caption at 3.1: *"You just walk. Stories start on their own when you get there."*

**3.2 While walking — the lever bank** — *a row of pills above the now-playing card*
- **Tell me more** → plays the next story for this stop in one of Maya's lenses.
- **Skip this stop** → the dot walks on to the next stop.
- **Shorter day** → a sheet: "Want to finish earlier? We'd drop Fraunces Tavern and walk
  straight to the ferry." with **Do it** / **Not now** (either returns to the walk; the
  path continues as scripted) ⚑.
- ~~**Ask about this**~~ → no longer a lever; it is its own always-visible button (3.4). *(Revised 2026-09-18, reviewer feedback: Ask vanished between stops and read as "waiting to be prompted".)*
- None are announced. Log every tap with the stop and time.
- ⚑ "Find me coffee" was dropped: the corpus has no café data and the prototype must not
  invent any.

**3.3 Federal Hall — same spot, different story** — *frame splits into two phones*
- Left phone **Maya**, right phone **Dan**, same map position. Maya's plays first, then
  Dan's (each has its own play button; auto-plays Maya's).
  - **Maya · Historic Architecture**, 58 s: "On the northeast corner of Nassau and Wall
    stands Federal Hall National Memorial, dubbed the 'Parthenon of Wall Street.' …those
    classical buildings lost their place in the sun."
  - **Dan · War & Conflict**, 30 s: "Before its federal function, the building on this
    site teemed with the birth pangs of the republic. The famed Stamp Act Congress met here
    in 1765… And the site even served as the British headquarters during the
    Revolutionary War."
- Caption: *"Dan is standing right next to you. He's hearing a completely different story."*

**3.4 Ask about this** — *a persistent round Ask button, top right of the walk map (opposite
the close X), opening a sheet* — **revised 2026-09-18**
- Visible on every on-the-ground screen: walking between stops, at a stop, the walk-past, the
  Deep dive (5.2), the Echoes camera (5.3–5.4) and Maya's phone in 6.1. Not on the planning
  screens (steps 1–2): trip-level Q&A would be a new feature.
- A Sourced answer is always about a place, so the sheet names the nearest one: at a stop, that
  stop; between stops, the stop being walked to; in the ferry line, Castle Clinton and the Statue
  of Liberty. Suggested chips stay; the field reads "or ask your own".
- Never announced (interview guide rule unchanged): H6 still measures whether they use it,
  but not whether they can find it.
- Field "Ask about Federal Hall…" plus three suggested chips. Each answer is real corpus
  text, shown with "From our notes on Federal Hall":

  | Chip | Answer (real beat text) |
  |---|---|
  | "Where did Washington stand?" | "It was here, perhaps on the very spot where the statue now stands, that Washington took the oath of office… though not in this actual building but in an earlier one on this site." |
  | "Can we go inside?" | "Today it is a museum about Washington's inauguration, freedom of the press, and Federal Hall — the first capitol, which stood on this spot." |
  | "What's inside?" | "Inside, visitors can see the Bible that Washington used for his oath of office…" |

- **Anything typed:** "I don't have a sourced answer for that here. I only tell you what
  we can back up." — plus the typed text is logged.
- Same pattern at Trinity: "Where's Hamilton buried?" → the `famous_residents` beat.
- Log: sheet opened, chip or free text, text content.

---

## Step 4 · The day changes

**4.1 Heads-up** — *after Federal Hall, the clock in the status bar jumps from 12:10 to
12:55*
- A card slides over the walk: "**You lingered at Federal Hall.** To make your 2:00 ferry,
  we've changed the rest of today."
- **Before → after** list:
  - ~~12:30 Fraunces Tavern~~ → **moved to Sunday**, "it's on your way to the bridge" ⚑
  - Bowling Green → **walk past**, 1 story on the way
  - 1:30 Castle Clinton → **1:25**, ferry still on time ✓
- Buttons **Sounds good** (continues) · **Keep original** (shows "You'd miss the 2:00 ferry"
  then continues anyway).
- Caption: *"Your day didn't go to plan. It noticed, and fixed it."*
- ⚑ The trigger is a timed ticket, not "you're tired": it is concrete, and it is the
  thing a fixed audio tour can't do.

**4.2 Walk-past** — the dot passes Bowling Green; a short story plays without stopping:
Bowling Green `hidden_history`, 31 s — "New York's oldest public park is purportedly the
spot where Dutch settler Peter Minuit paid… $24… At its northern edge stands Arturo Di
Modica's 7000lb bronze Charging Bull…"

---

## Step 5 · In line — the Deep dive, with Echoes around you

**5.1 In line?** — *on arrival at Castle Clinton*
- Card: "**Looks like you're in line for the ferry.** Want the story of the statue while
  you wait? One story for all four of you, out loud."
- "How long's the line?" chips: **5 min** · **15 min** · **30+ min**.
- Caption: *"Waiting in line is the best time for the big story."*

**5.2 Deep dive — "The copper woman"** — *full-screen player, same brand, speaker icon
and "Playing out loud · 4 listening"*
- Whatever line length is chosen: "A 4-minute chapter — then it's quiet until the boat."
  (The corpus holds under 6 minutes of Liberty story; the prototype does not pretend
  otherwise.)
- Chapter, in order (real beats; crown-step beats excluded because they disagree):
  1. Castle Clinton `social_change`, 13 s — "Before Ellis Island, the immigrant landing
     station at Castle Garden — here at the foot of Manhattan…"
  2. Liberty `hidden_history` (hook), 30 s — "The statue was dreamed up at a dinner party…"
  3. Liberty `social_change`, 29 s — "…Joseph Pulitzer finally stepped in…"
  4. Liberty `hidden_history`, 33 s — repoussé, the seven rays, the tablet
  5. Liberty `science_tech`, 50 s — Eiffel's frame, 350 pieces in 214 crates
  6. Liberty `social_change`, 27 s — "Mother of Exiles", Lazarus
  7. Liberty `social_change` (climax), 36 s — twelve million immigrants
- Chapter progress as 7 dots; transcript toggle as in 3.1.
- A pill at the top: **Look around** (opens 5.3; the chapter keeps playing in a mini
  player).
- Caption: *"The whole family hears this together, from one phone."*

**Echoes everywhere (revised 2026-09-18, reviewer feedback: "why can I only add or see an
echo at that one point")**
- The walk map (3.1–4.2) shows an echo count badge at every stop.
- A camera button sits beside the Ask button on every walk screen. At any stop it opens that
  stop's camera view: a royalty-free photo of the place with 1–2 Echoes over it, the same
  reactions, and **Leave an echo** (5.4). Logged: camera opened, stop, reactions.
- ⚑ About 2 Echoes per stop, built only from the templates and word lists and grounded in
  that stop's beats; drafted by Claude for founder review.
- 5.3 stays the scripted moment at Castle Clinton (it opens as part of the flow) and is where
  the H8 questions are asked.
- **Camera simulation (founder, 2026-09-18):** each camera view is a wide landscape photo
  behind a portrait viewfinder that pans slowly left and right, as if the phone were moving.
  Echoes are pinned to points in the photo, so they drift with the scene and slide in and
  out at the edges like AR labels. The traveller can drag to look around; the auto-pan
  resumes after a few seconds. Viewfinder chrome (corner brackets) marks it as the camera.

**5.3 Look around — Echoes** — *camera view: a real photo of the Castle Clinton courtyard
and harbor as the "camera feed" ⚑ source: public-domain / Wikimedia*
- Three Echoes float over the scene, each a small card with its reaction counts:
  - "**Look right →** Ellis Island — the four little spires" · Found it 214 (grounded in
    the Castle Clinton beat)
  - "**Worth it:** the round walls — this was a fort in 1812" · Worth it 88
  - "**Best photo:** the statue, from the railing" · Ha! 3 · Worth it 140 ⚑
- Tap an Echo → it expands; three fixed reactions: **Found it** · **Worth it** · **Ha!**
  (tapping one increments it).
- Button **Leave an echo** → 5.4.
- Mini player at the bottom keeps the chapter going.
- Caption: *"Other travellers left these right here. Tap one."*

**5.4 Leave an echo** — *template builder*
- Step 1, pick a template: "Look ___ at ___" · "Worth it: ___" · "Skip: ___" ·
  "Best photo: ___" · "Don't miss ___".
- Step 2, pick words from lists: direction (up / left / right / behind you) and thing (the
  statue / the fort / the harbor / Ellis Island / the ferry / the view).
- Preview → **Leave it here**. It pins into the camera scene with "0 found — yet".
- Caption: *"Leave something for the next family. Only these words — no free text."*

---

## Step 6 · The kid in line — I Spy

**6.1 Ava's phone** — *a third, smaller phone slides in beside Maya's*
- Kid view, same brand but big type and big tap targets. Title "I Spy — the harbor".
- Three finds, each a card with a big **Found it!** button and a stamp when done:
  1. "I spy a big green lady. Find her crown — how many points does it have?" → answer
     reveal: "7 — one for each of the seven seas!" (Liberty `hidden_history` beat)
  2. "I spy a building on an island with four little pointy towers." → "That's Ellis
     Island, where millions of families arrived!" (Castle Clinton beat)
  3. ✎ founder writes one more
- ✎ Founder reviews all child prompts before the pilot (child-facing copy).
- Caption: *"Ava's in the same line, playing her own game about the same view."*

---

## Step 7 · The day, told back

**7.1 Photos?** — *iOS-style permission sheet*
- "Ondoway would like to look at photos from today to build your recap." · **Allow
  photos from today** · **Don't allow**.
- Either choice continues; with "Don't allow" the recap shows map thumbnails instead of
  photos. Log the choice.
- Caption: *"This part uses your own photos — if you let it."*

**7.2 Friday, told back** — *revised 2026-09-18: real photos in the strip and a photo behind
the header stats; otherwise unchanged and calm — it is the dinner screen (H3). No stickers or music.*

**7.2 (original spec)** — *the Day recap, one scrolling screen, evening colours*
- Header "Friday · Lower Manhattan" · 3.1 mi · 6 stops · 14 stories ⚑.
- Photo strip matched to stops (placeholder family-trip photos, labelled by stop and time).
- **Same spot, four stories** (the main block) — Federal Hall:
  - Maya: "…dubbed the 'Parthenon of Wall Street.'"
  - Dan: "…the British headquarters during the Revolutionary War."
  - Leo: ⚑ one line from a Federal Hall beat in his lenses, else "Leo skipped this one"
  - Ava: "Found the crown — 7 points!" (her I Spy stamp)
  - Prompt line: "**Ask Dan** what happened here in 1765."
- **You asked:** the questions asked today (from 3.4 if they asked; otherwise the two
  suggested ones, shown as Maya's).
- **Your echo:** the one left in 5.4, "found by 3 people so far" ⚑.
- Caption: *"Tonight at dinner: what everyone heard, side by side."*

**7.3 is the showpiece (decided 2026-09-18, reviewer feedback: "make the wrap-up pop"):**
story-style full-screen cards with photo backgrounds behind the stats, stickers, a tap-to-change
filter, music underneath and a pretend share. Details below as decided.
- **Stickers and filters (2026-09-18):** cards 1–4 each get a full-bleed photo and one or two
  stickers that pop in. Sticker text comes only from this trip, never invented ⚑: "Parthenon of
  Wall Street" (Maya's Federal Hall story), "7 points!" (Ava's I Spy), "Hamilton's grave ✓"
  (Trinity), "11.4 mi 👟", "41 stories", and their own echo if they left one. Card 5 (share)
  is a small editor: a filter row under the collage (Original · Golden hour · Harbor · Mono,
  applied live) and a sticker tray from the same set (tap to add, drag to place, tap again to
  remove). Logged: filter chosen, stickers added and removed, time spent editing (H10 signal).
- **Music (founder, 2026-09-18): no audio track for now.** The share-card editor has an
  **♪ Add music** button → a pretend picker, "Add a song from your library" (Apple Music ·
  Spotify); choosing adds a "♪ Your song" chip with the note "In the real app, this plays a
  song from your library under your recap." No sound, no track names, nothing to license.
  Logged.
- **Share (2026-09-18):** opens a share sheet of plain labelled options, never a copy of any
  platform's interface: **Send to your party** (Dan, Leo & Ava) first, then Instagram Story ·
  TikTok · WhatsApp · Messages · Copy link. Choosing one shows the edited card in a neutral
  9:16 story frame with a small "made with Ondoway" mark and "Ready to post — in this demo
  nothing is shared." Party share shows "Sent to Dan, Leo & Ava (demo)" with three avatars
  ticking. **Keep it** ("Make it a keepsake", no price, no format) stays beside Share. Logged:
  Share tapped, option chosen; the interviewer's "I just asked" mark codes it U or P for H10.

**7.3 Your trip, the whole thing** — *the Trip recap, flourish: 5 full-screen cards, tap
to advance*
1. "4 days · 11.4 miles · 41 stories" ⚑
2. "You're **The Architect**. 61% of what you heard was about buildings." ⚑
3. "The family's favourite stop: **Federal Hall**. Replayed 3 times."
4. "Your echo at Castle Clinton was found **27 times**."
5. Share card (photo collage + route + "New York · Ondoway") with **Share** and
   **Keep it** ("Make it a keepsake" — no price, no format).
- Caption: *"And when you're home, the whole trip."*

**7.4 End** — "That's the whole thing." Button **Back to the conversation**.

---

## Close — ranking board (founder's screen, not the phone)

Seven cards, draggable into a ranked column. Plain board in the brand, no phone frame.

1. **Picks what fits your family** — your interests shape every story.
2. **Plans the day between your big sites** — and tells you what to skip.
3. **Stories that start where you're standing** — different ones for each of you.
4. **Changes the plan when your day changes.**
5. **The big story while you wait in line** — together, out loud.
6. **Notes left by other travellers**, and leaving your own.
7. **Your day told back at dinner**, and the trip when you're home.

Plus two price cards, revealed by the founder after the open price question:
**Solo / Pair — $12–19** · **Household (up to 5) — $29–49** · "One city, one trip. Paid
once."

---

## Timing and cuts

| Step | Min |
|---|---|
| 1 Lenses | 2 |
| 2 Planning | 6 |
| 3 Walk + companion + Ask | 7 |
| 4 Day changes | 3 |
| 5 Deep dive + Echoes | 5 |
| 6 I Spy | 2 |
| 7 Recap | 5 |
| **Total** | **30** |

*Revised 2026-09-18: step 1 −1 (no sample line to read), step 5 −1 (echoes met on the walk),
step 7 +2 (the 7.3 editor and share). The interview guide matches these numbers.*

Cut order when over time: **5.4 Leave an echo** → **step 6 to its first find only** →
**7.3 Trip recap** (revised 2026-09-18: 7.3 is now the showpiece, so it is cut last). Never cut
2.4 (Skip advice), 3.3 (companion) or 7.2 (Day recap).

## What gets logged

Per session, exportable from the interviewer view: every tap with screen id and
timestamp; lens toggles (1.1); lever taps and Ask use with text (3.2, 3.4); re-plan
choice (4.1); line length (5.1); Echo reactions and the echo left (5.3–5.4); I Spy finds
(6.1); photo permission (7.1); time on each screen; the final ranking order.

## Photos (decided 2026-09-18, reviewer feedback: "royalty-free photos throughout")

- **Slots:** 0.1 hero (family on a city street); 2.3 day cards and 2.4 stop cards (small
  thumbnail of each place); the now-playing card (the stop's photo replaces the headphones
  tile); every stop's camera view (wide landscape, see Echoes everywhere); 5.2 Deep dive art
  (the statue); 7.2 and 7.3 stand-ins for Maya's own photos (family-at-landmark, not postcards).
- **Sources:** landmarks from Wikimedia Commons, public domain or CC0 only; people and family
  shots from Unsplash or Pexels. Files stored in `prototype/img/`, never hot-linked;
  `prototype/CREDITS.md` records source URL and licence per image.
- **Process:** Claude searches and shows a contact sheet (thumbnail, source, licence, size);
  the founder approves before anything is downloaded.
- **Chosen 2026-09-18** (founder: "all recommended, plus both suggestions"): 16 photos in
  `prototype/img/`, credited in `prototype/CREDITS.md`. The Castle Clinton camera uses a CC0
  Statue of Liberty harbor panorama (no free photo shows the fort with the harbor). The
  Charging Bull has no free photo, so the Bowling Green echo "Best photo: the bull" became
  "Look up at the old Standard Oil building" (grounded in the Bowling Green beats).

## Content still owed before the pilot

- ✎ Skip advice verdicts (2.4), the third I Spy find (6.1), review of all child copy.
- Voiced audio for every story slot, with the project's real TTS.
- Photos: family placeholders (0.1, 7.2, 7.3) and the Castle Clinton "camera" scene
  (5.3), public-domain or licensed.
