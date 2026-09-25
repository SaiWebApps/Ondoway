# Concept interview + walkthrough prototype — brief

Decided in a grilling session, 2026-09-17. Vocabulary: `Docs/traveller/CONTEXT.md`.
Business source of truth: `~/Documents/Ondoway/Business/Ondoway_Pitch_Deck_v1.pptx`.

## Goal

Get evidence on the concept and value prop from **Planners** (organised a 3+ day trip to
an unfamiliar city for 2–5 people). The deck records 0 primary interviews; this is that
evidence. Test the deck's bets, not a different product.

**Prototype goal (owner, 2026-09-18):** a testable prototype that lets a target traveller
experience the core Ondoway value proposition without the founder having to explain or
manually simulate the product. The test: in part 3 the founder only asks questions; he
never narrates a screen or clicks anything for them.

## Session — 60 min, mostly remote (screen share), some in person

| # | Part | Min | Notes |
|---|---|---|---|
| 0 | Intro + recording consent | 2 | |
| 1 | "Tell me about the last trip you planned" | 10 | Behavioural only. Nothing shown. |
| 2 | "Tell me about the last famous place you visited" + "what did you all talk about at dinner?" | 8 | Record **unprompted** vs **prompted** separately. Ask "ever underwhelmed?" only at the very end if it never surfaced. |
| 3 | Walkthrough, 7 steps (screen by screen: `script.md`) | 30 | 1 Lenses · 2 Planning the day (Stop reasons, Skip advice) · 3 Walk with real audio + text, companion at Federal Hall, Ask · 4 Day changes (timed ferry) · 5 In line: Deep dive + Echoes · 6 I Spy · 7 Day + Trip recap. Questions: `interview-guide.md`. |
| — | Close, **off the phone**, plain shared screen | 7 | Rank the feature cards → "what would you use instead?" → unanchored "what's it worth for your trip?" → show Solo/Pair $12–19 vs Household $29–49 → "who would it need to cover?" → "would the others in your party install it?" |

**Cut order when over time:** Leave an echo → I Spy to one find → Trip recap (revised
2026-09-18: the Trip recap became the showpiece, so it is cut last).
**Never cut:** parts 1–2, the Skip advice, the Federal Hall companion moment, the Day
recap, the close. Cut steps still appear as ranking cards.

## Prototype — what to build

- **Desktop HTML in a phone frame, driven by the traveller, running itself.** A fixed
  path, no engine, no GPS, no real-phone mode, but nothing the founder has to operate.
  - **The traveller holds the mouse.** Remote: send them the link, they open it and share
    their screen. In person: hand them the laptop.
  - **Every screen explains itself** with a one-line in-product caption, as a real app
    would on first use ("Your partner is hearing a different story at this same spot").
    If a screen needs the founder to explain it, the screen is wrong.
  - **The walk runs on its own.** After "Start walking" the blue dot moves along the route,
    each stop's story plays automatically on arrival, and the running-late re-plan fires by
    itself. The traveller can pause, tap a lever or "Ask about this"; otherwise it
    continues.
  - **Every tap is logged** (what, when, prompted or not) so unprompted use is behavioural
    in remote sessions too.
- **Interviewer view, separate from the traveller's:** the same page opened with an
  interviewer flag shows the question script per step, prompted/unprompted tallies and
  the clock, following the traveller's position. The traveller's view never shows it.
- **Fixed party:** Maya (Planner), partner, kids 8 and 12, 4 days in NYC. Demo shows Friday (Day 2).
- **Fixed path (Lower Manhattan, Friday):** 9/11 Memorial → Trinity Church → Federal Hall
  (Maya and Dan hear different stories) → running late for the 2:00 ferry, re-plan moves
  Fraunces Tavern to Sunday → walk past Bowling Green → Castle Clinton ferry line: Deep
  dive for the family, Echoes in the camera view, Ava's I Spy → evening Day recap.
  `script.md` is the authority for every screen.
- **Build first, fill content before the pilot.** Every content slot is a clearly marked
  placeholder in one data block at the top of the file, swappable without touching layout.
- **Story text (revised 2026-09-23):** hand-authored to the house standard from
  public-domain and government sources (NPS, LOC/HABS, NARA, LPC, NYC Parks) — the engine is
  mid-rebuild, so the demo tests the concept, not the engine. Scripts and citations in
  `scripts.md`; research in `sources/`. It is recorded there that this
  audio may never be presented as engine output.
- **Real audio is required before the pilot.** Hearing the story at the place is the core
  value; a placeholder button leaves nothing to experience. Build with placeholders (browser
  speech is acceptable while building), then voice every slot with the project's real TTS.
- **Skip advice, I Spy prompts:** placeholder text marked TODO while building; the founder
  writes the real ones before the pilot (verdicts must be ones he would stand behind). Two
  Liberty beats already support "crown tickets sell out months ahead."
- **Deep dive length:** seven chapters, 12:23, hand-authored from public-domain sources
  (`scripts.md`). Chapters are separate files, so a short queue stops early.
- **Look:** copy the real Flutter app's screens and tokens (details in `script.md`), so
  people see what the product will actually look like.

## Caveats to keep in mind

- ~~Current NYC beats are commercial-guidebook sourced, so private interviews only.~~
  **Resolved 2026-09-23:** every spoken word is now written from public-domain and
  government sources, so the walkthrough can be shown to anyone. The corpus caveat still
  applies to the product, not to this prototype.
- A laptop can let someone hear and see the product work; it cannot let them live the
  deck's central bet (a party hearing different stories on the ground, then trading them
  at dinner). The slide 17 Saturday walk remains the evidence for that.
- Tag every session remote or in-person anyway; screen-share behaviour differs.
