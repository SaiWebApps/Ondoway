# Narration for voicing — concept-interview walkthrough

Every word a listener hears in the prototype. Written 2026-09-22 for the interviews.
Screens and slot ids: `script.md`. Prototype: `prototype/content.js`.

**What this is, and what it is not.** This narration is **hand-authored to the house
standard** (`specs/2026-07-19-tour-quality-standard/01-standard.md`), not produced by the
ingestion engine, which is still being rebuilt. It exists so the interviews test the
*concept* rather than the engine's current state. Nobody may later present these
recordings as engine output, or as evidence that the engine works.

**Sourcing.** Mostly public-domain and government records — National Park Service, Library of
Congress (HABS), National Archives, NYC Landmarks Preservation Commission, NYC Parks, NYC
Department of Records, the U.S. Reports — plus the institutions' own histories. **The 9/11
Memorial's seven beats are the mixed-provenance exception**, because the memorial is a
private non-profit: they rest on its own jury statement, backgrounder and recorded oral
history, an engineer's account in the National Academy of Engineering's journal, a trade
journal, 2006 news reporting, and government records where they exist (the Radio Row
condemnation case). No commercial guidebooks anywhere, so unlike the current NYC
corpus this material carries no "private use only" caveat. Every claim's source is named
under its own slot below, and the fact sheets behind them are in `sources/`:

| File | Covers |
|---|---|
| `facts-walk.md` | Federal Hall, Trinity, Bowling Green, the 9/11 plaza basics |
| `facts-liberty.md` | Castle Clinton and the Statue of Liberty, for the Deep dive |
| `facts-memorial-design.md` | the competition, the 2006 cost cuts, Arad and Walker |
| `facts-memorial-tour.md` | what a real guide covers at the plaza; Radio Row; the parapets |
| `facts-slurry-wall.md` | the wall, the tiebacks and the PATH plugs — **read directly from the primary PDFs in session, after a judge review found these claims had no paper trail in the repo** |

**Voice.** Second person, present then past. Place first, then the story. Quotes
attributed to whoever said them. Where the record is uncertain, the uncertainty is spoken
— it is a better line than false confidence. Never "imagine". Never a sales line.

**Every slot, and where it goes.** Prototype slot ids are the keys in
`prototype/content.js` (`stories`, `deepDive`, `ask`). Swapping a recording in is one line
per slot: set `audio:` to the filename.

| Slot id | Screen | What | Length |
|---|---|---|---|
| `memorial` | 3.1 | 9/11 plaza, Maya's lens | 0:30 |
| `memorial_more` | 3.2 | 9/11 plaza, "tell me more" — the names | 0:50 |
| `trinity` | 3.1 | Trinity, Maya's lens | 0:55 |
| `trinity_more` | 3.2 | Trinity, "tell me more" — the doors | 0:30 |
| `fh_maya` | 3.3 | Federal Hall, Maya (architecture) | 1:00 |
| `fh_dan` | 3.3 | Federal Hall, Dan (the Revolution) | 1:00 |
| `fh_more` | 3.2 | Federal Hall, "tell me more" — the 27th amendment | 0:40 |
| `bowling` | 4.2 | Bowling Green, walk-past | 0:35 |
| `deepDive` 1–7 | 5.2 | The Statue of Liberty chapters — see `deep-dive.md` | 12:23 |
| — | 3.4 | Ask answers, I Spy reveals, Stop reasons | text only |

Walk stories total about 6 minutes; with the Deep dive, about 18½ minutes of audio.

**Recording notes. Pace ~150 words per minute, unhurried. Pause at the paragraph breaks;
they are written as breaths. One file per slot, named by slot id, mp3 or m4a.

---

## 9/11 Memorial — a gravity-5 anchor, seven beats, about 5:40

**846 of the 850 words a stop may carry** (check C8, the gorging cap). Four words of headroom:
anything added here has to come out of a beat that is already written.
*(rewritten 2026-09-23. The stop was 3:06 and opened on design. The institution's own guided
tour runs **event → site history → design**, and third-party walking tours open with Radio
Row, so the order here follows theirs. Every beat sits in one of Maya's three lenses:
Historic Architecture, Modern & Contemporary Design, Hidden History.)*

### 3.1 · `memorial` — the two pools aren't the same place (Hidden History) · ~0:50

> Two pools, the same size, side by side, marking where the towers stood. You are at the
> north one.
>
> They are not the same place.
>
> When the first plane hit the North Tower, it severed every stairwell above the impact.
> Nobody above that floor had a way down. In the South Tower, one stairway survived the
> strike, and a small number of people who were above it walked out.
>
> Same shape, same water, same acre. One of them had a way out of it.

Sources: 9/11 Memorial & Museum guided-tour account (the two impact zones as the tour's own
fourth beat: the North Tower strike severing every escape route, some above the South
Tower's impact getting out).
*Says the pools "mark where the towers stood", never that they are exactly the footprints —
the pools are smaller and square-cornered where the towers were bevelled.*

---

### 3.2 · `memorial_before` — Radio Row (Hidden History) · ~1:00

> Before the towers, this was Radio Row: thirteen blocks of electronics shops, war-surplus
> dealers and parts bins on the pavement, centred on Cortlandt and Greenwich. Over four
> hundred merchants.
>
> They were condemned, for a reason you would not guess. New Jersey would only agree to the
> trade centre if the Port Authority took over a failing commuter railway. It did. That
> railway is the PATH line under your feet. Radio Row was cleared, in part, to rescue it.
>
> The merchants fought. On the fourteenth of July, 1962, they carried a black-draped coffin
> down Cortlandt Street with a sign on it: "Here lies Mr Small Businessman."
>
> They lost in the New York Court of Appeals, and the Supreme Court threw out their appeal.
> The case is named for one of them — a lunch counter called the Courtesy Sandwich Shop.

Sources: NYC Department of Records & Information Services, "Radio Row and the Fight for
Lower Manhattan", 2024 (the district's bounds, Cortlandt and Greenwich, "over 400
merchants"); 9/11 Memorial WTC History exhibition (the 16-acre superblock over ~13 blocks;
the Port Authority taking over the Hudson & Manhattan railway as New Jersey's condition);
New York Preservation Archive Project (the 14 July 1962 coffin and its sign); *Courtesy
Sandwich Shop, Inc. v. Port of New York Authority*, 12 N.Y.2d 379 (1963), appeal dismissed
375 U.S. 78.
*Avoids "325 businesses" (unsourced) and "30,000 workers" (the merchants' own advocacy
figure for all 1,600 businesses in the area, not a count of Radio Row).*

---

### 3.2 · `memorial_dig` — the wall that should have collapsed (Hidden History) · ~1:10

> To build the towers they dug a hole in the riverbank and kept the Hudson out of it. The
> wall that does that is three feet thick and seventy feet deep, still there a few metres
> below you, and visible from inside the museum.
>
> More than a million cubic yards came out of this hole, went into the river behind a steel
> dam, and became Battery Park City. The neighbourhood next door is the hole under your feet.
>
> While they dug, fifteen hundred steel anchors held that wall back against the river. Then
> the basement floors went in and took over the job — and every one of those anchors was
> cut.
>
> On the eleventh of September the floors were destroyed, which left the wall holding back
> the Hudson with nothing bracing it. The engineer who built it said it should have
> collapsed.
>
> It didn't. But in the days afterwards they poured concrete plugs into the PATH tunnels
> under the river, rated to hold back eighty feet of water. In case it did.

Sources: George J. Tamaro (the Port Authority engineer on the original wall, later of Mueser
Rutledge), "World Trade Center 'Bathtub': From Genesis to Armageddon", *The Bridge*, National
Academy of Engineering, Spring 2002 — the 3 ft × 70 ft wall socketed into rock, 1,500 tieback
anchors detensioned once the permanent floors could brace the wall, more than a million cubic
yards of spoil becoming the Battery Park City landfill, and the 16-foot concrete plugs poured
into both PATH tubes rated for an 80-foot head of water. Arturo Ressi, who worked on the
original construction of the wall, in the Memorial's own recorded oral history: the anchors
"were all cut", and with the floor system gone the wall "should have collapsed". Both
documents were fetched and read in session — see `sources/facts-slurry-wall.md`.
*The wall's inward movement after the collapse is reported as 10 inches, 2 feet and over 4
feet by three credible sources, so no figure is spoken.*

### 3.2 · `memorial_design` — the memorial that was cut (Modern & Contemporary Design) · ~0:50

> Look down at the names on the parapet. You were never meant to read them here.
>
> The design that won this competition took you underground — down into the footprints,
> below the street, to read the names with the water falling past you. The jury praised
> exactly that: the descent, and reaching bedrock inside the north tower's footprint.
>
> Then the costing came in near a billion dollars, a builder was brought in to cut it, and
> the galleries went. The names came up here, to your elbow. Michael Arad, who designed it,
> said he feared that in losing the below-ground memorial he was losing the whole thing.
>
> What you are standing on is the argument he had left.

Sources: LMDC/9/11 Memorial *Jury Statement for the Winning Design*, 13 January 2004 (the
descent below street level, bedrock in the north tower's footprint); NBC and CBS News, 2006
(near-$1bn estimate; Sciame's review removing the galleries and raising the names); Arad in
Yale News, 2012; UC Berkeley News, 2004 and SFGate (the landscape architect as a condition of
winning; Walker halving the stone; "the part of this problem that is about death" /
"continuation of life").

---

### 3.2 · `memorial_names` — the order of the names (Hidden History) · ~0:50

> Walk along the parapet and you'll notice the names aren't in alphabetical order.
>
> They're grouped by where people were that morning. Which tower. Which floor. Which
> company. Which flight. Which firehouse.
>
> And inside those groups, they were placed by request. Families were asked whether they
> wanted a name to sit beside particular others. More than twelve hundred of those requests
> were honoured. Some were for a husband, a sister, a colleague of thirty years. Others were
> for someone the person had barely known — met that morning, on a stairway, and stayed
> with.
>
> There are two thousand nine hundred and eighty-three names here. Everyone killed on the
> eleventh of September, 2001, and the six people killed when the same buildings were bombed
> in February 1993.

Sources: 9/11 Memorial "About the Memorial" and Institutional Backgrounder (grouping by
location and circumstance, "meaningful adjacencies", more than 1,200 requests honoured, the
bonds formed during the response, 2,983 names, the 1993 six).

---

### 3.2 · `memorial_parapet` — why the bronze never burns your hand (Modern & Contemporary Design) · ~0:45

> Put your hand on the bronze. In August it should be hot enough to hurt; in February it
> should take the skin off your fingers. It does neither.
>
> Behind those panels, in a crawl space you will never see, there are fourteen thousand feet
> of copper pipe carrying glycol, heating and cooling the metal to hold it between forty and
> seventy degrees, all year.
>
> The man who ran the memorial's construction explained why they bothered. The bronze is the
> first thing people touch, he said, and the memorial almost becomes like a baptismal
> setting: you can wet your hand in the water and leave a print on the name.
>
> Somebody engineered that.

Sources: Copper Development Association, *Building & Architecture News*, June 2012 —
Christopher Powers of KC Fabrications, who built the system with Jaros Baum & Bolles:
glycol through ~14,000 ft of half-inch copper pipe and 12,000 ft of brackets, panels held
between 40 and 70 °F because "if the panels are too hot or too cold, the Memorial loses the
ability for people to interact with it"; Ronaldo Vega, the Memorial's Director of Design and
Construction, on the bronze being the first thing people touch and the memorial becoming
"almost like a baptismal setting".

---

### 3.2 · `memorial_tree` — the Survivor Tree (Hidden History) · ~0:40

> There's one tree here that isn't an oak.
>
> It's a Callery pear, and it was already on this site before any of this. They pulled it
> out of the rubble in October 2001 with its roots snapped and its branches burned and
> broken, and handed it to the city's parks department, who kept it alive.
>
> It came back in 2010.
>
> If you find it, look where the trunk changes. The old stumps are gnarled; the limbs
> growing out of them are smooth. The tree carries the line between before and after in its
> own wood.

Sources: 9/11 Memorial Institutional Backgrounder (the Callery pear original to the site,
recovered October 2001 "with snapped roots and burned and broken branches", cared for by the
NYC Department of Parks and Recreation, returned 2010, the demarcation between new smooth
limbs and gnarled stumps — paraphrased, not quoted).
*No heights: the institution publishes none.*

---


## 3.2 · "Tell me more" at Trinity Church — second story · ~30s
*(prototype slot `trinity_more`)*

> Before you go in, look at the doors.
>
> Three pairs of bronze, given by William Waldorf Astor as a memorial to his father, and
> designed by Richard Morris Hunt — who modelled them on Ghiberti's doors for the Baptistery
> in Florence.
>
> Remember his name. In a few hours you'll be standing on his other New York commission: he
> designed the pedestal the Statue of Liberty is standing on.

Sources: WPA Guide 1939 (the three pairs of bronze doors, the Astor gift, the Ghiberti
model, Hunt as designer — stated as fact, not quoted, since the WPA Guide's public-domain
status rests on non-renewal); NPS "Richard Morris Hunt" (pedestal architect, AIA founder).
*Deliberately plants Hunt so the Deep dive's pedestal chapter pays him off.*

---

## 3.1 · Trinity Church — Maya's lens (Historic Architecture) · ~1:10
*(prototype slot `trinity`; rewritten 2026-09-23 — the first version was chronology)*

> Trinity's spire stands about two hundred and eighty feet above Broadway. When it was
> finished, in 1846, nothing in New York stood higher.
>
> This is the third church on this ground. The first burned in the great fire of September
> 1776, days after the British took the city — a newspaper that week described the burning
> steeple as "a vast pyramid of fire." The second lost its roof supports to heavy snow, in
> 1839.
>
> The parish brought in Richard Upjohn to repair that one. He talked them into pulling it
> down and starting again — and then he did something that got him into trouble.
>
> Upjohn was a High Churchman. He gave this church a deep chancel and carved choir stalls,
> and to a great many New Yorkers in the 1840s that looked like Rome creeping back in. The
> feeling ran high enough that at first the stalls went unused.
>
> He was building an argument, not just a church. And it is the building that made his name.

Sources: Trinity Church parish history (1697 charter, the 1776 fire and the period-newspaper
quote, the 1839 snow, the 1846 consecration); NHL nomination 76001252 quoting John
Coolidge's 1935 study (Upjohn as an ardent High Churchman, the chancel and choir stalls,
the anti-papist feeling that left them unused at first, the commission that made his
national reputation); WPA 1939 (280 ft above the steps).
*"About 280 feet": Trinity says 281, the WPA Guide 280.*

---

## 3.3 · Federal Hall — Maya's phone (Historic Architecture) · ~1:10
*(prototype slot `fh_maya`; rewritten 2026-09-23 — the first version was a materials list)*

> Nassau Street and Wall. The white building with the colonnade opened in 1841 as the
> Custom House — and who designed it is still not a simple question.
>
> Ithiel Town and Alexander Jackson Davis won the competition. Then the commissioners
> decided they disliked the interior, and brought in an English architect, William Ross, to
> redo it. And John Frazee superintended the entire build and drew most of the working
> details himself — which is why the record generally calls Frazee the architect.
>
> What all of them agreed on was fire. There is no structural wood anywhere in this
> building. Marble, limestone, granite, brick, iron. Even the roof is marble — overlapping
> slabs, lapped eight inches, each with a lip above and below, so the stone can move in the
> heat without letting the rain in.
>
> And they built a Greek temple on purpose. Americans in the 1830s had decided they were
> the heirs of Athens, and they put columns on whatever they wanted taken seriously — banks,
> churches, custom houses. The democracy was the argument. The marble was the proof.

Sources: HABS NY-470, 1935 (the 1833 competition won by Town & Davis; the commissioners
disliking their interior and engaging William Ross; Frazee superintending, drawing most of
the working drawings and being "generally known as the architect"; no structural wood; the
lapped marble roof with a lip above and below to allow for expansion); NRHP 66000095
(fireproof construction of marble, limestone, granite, brick and iron); NPS "Greek Revival
Style 1830s–1860s" (Americans as "the natural heirs to the ancient Greeks, who invented
democracy"; banks, offices and churches built as Greek temples).
*The rotunda and its sixteen single-block Corinthian columns are dropped: they are inside
the building, and this beat is heard on the steps. If you ever want them, they belong in an
inside-the-museum beat, not this one.*

---

## 3.3 · Federal Hall — Dan's phone (War & Conflict) · ~60s

> Same steps. Different building, though. The one that matters here was pulled down in
> 1812.
>
> On this site, in 1735, a printer named John Peter Zenger stood trial for seditious
> libel. He had printed attacks on the royal governor. From the bench, the Chief Justice
> told the court: "The laws in my opinion are very clear; they cannot be admitted to
> justify a libel." The jury acquitted him anyway.
>
> Thirty years later, delegates from nine colonies met here as the Stamp Act Congress, and
> wrote down the argument that became the Revolution: that no taxes should be imposed on
> them "but with their own consent, given personally or by their representatives."
>
> And on the thirtieth of April, 1789, from a balcony on that older building, George
> Washington took the oath as the first president, in a dark brown suit made in America.
>
> The balcony's stone floor survived the demolition. It is inside, a few feet from where
> you are standing.

Sources: NPS "The Trial of John Peter Zenger" (1735 trial, the DeLancey quotation); Library
of Congress *Magna Carta: Muse and Mentor* (the 1765 Declaration of Rights wording); NPS
"Inaugural Balcony" (30 April 1789, the American-made brown suit, the surviving balcony
stone now in the lower rotunda).
*"So help me God" is deliberately absent: contemporary accounts indicate Washington did not
say it, and the Senate Historical Office no longer attributes it to him.*
*Says "the site", not "the spot" — the 1789 building is gone.*

---

## 3.2 · "Tell me more" at Federal Hall — second story · ~40s

> One more thing about the first Congress, which met upstairs.
>
> In September 1789 they passed twelve amendments to the new Constitution and sent them
> out to the states. Ten came back ratified two years later, and we call those ten the
> Bill of Rights.
>
> One of the other two — about when Congress may raise its own pay — was never rejected.
> It simply stayed open. It was finally ratified in 1992, as the twenty-seventh amendment:
> two hundred and three years after it left this building.

Sources: U.S. Senate "Congress Submits the First Constitutional Amendments to the States"
(twelve amendments, 25 September 1789); National Archives "Bill of Rights (1791)" (ten
ratified 15 December 1791; Article 2 ratified 1992 as the 27th Amendment).

---

## 4.2 · Bowling Green — walk-past · ~35s

> On your right, Bowling Green. The city leased it in 1733 for a rent of one peppercorn a
> year, to be improved — in the words of the lease — "for the Recreation & Delight of the
> Inhabitants of this City."
>
> The iron fence is the original, from 1771. Look at the tops of the posts: something has
> been broken off every one of them. The Landmarks Commission says the caps were
> "variously described as royal crowns or iron balls." The city's own plaque says patriots
> "are said to have" removed them. Nobody can prove it either way.
>
> What is certain is the ninth of July, 1776. The Declaration was read to the troops a few
> blocks north, and the crowd came down Broadway and pulled down the gilded statue of
> George the Third that stood on this green.

Sources: NYC Parks "The Oldest Parks" (1733 Common Council resolution, peppercorn rent, the
lease wording); LPC LP-0548 (1771 fence, the contract, 9 July 1776, the hedge on the caps);
NYC Parks plaque text; NPS "Bowling Green".
*The musket-ball figure is left out of the spoken line; if you want it, it must be
attributed: "the Park Service puts it at 42,088 musket balls."*

---

## Text-only — no voicing needed

### 3.4 · "Ask about this" answers (Federal Hall)

| Question chip | Answer shown |
|---|---|
| Where did Washington stand? | Not on these steps. The building he stood on was demolished in 1812 — but the balcony's brownstone floor survived, and it's displayed inside, in the lower rotunda. |
| Can we go inside? | Yes. It's a national memorial, run by the Park Service, and free to enter. |
| What's in there? | The rotunda — sixty feet across, sixteen Corinthian columns, each a single piece of marble — and the inauguration stone. |

Trinity chip: **Where's Hamilton buried?** → "In the churchyard on the Broadway side, near
the southern fence. Eliza is beside him. She outlived him by fifty years and died at
ninety-seven."

Anything typed: "I don't have a sourced answer for that here. I only tell you what we can
back up."

### 2.4 · Stop reasons (unchanged from `script.md`, restated for the voice files' context)

Not spoken. They appear on the plan screen only.

---

## Statue of Liberty Deep dive — see `deep-dive.md`

Seven chapters, 12:23 — about 9 minutes if chapter 6 is cut. Written 2026-09-23 with its own
sources and voice notes. Each chapter is a separate file, so a short queue stops early.
