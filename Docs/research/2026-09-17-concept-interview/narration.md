# Narration for voicing — concept-interview walkthrough

Every word a listener hears in the prototype. Written 2026-09-22 for the interviews.
Screens and slot ids: `script.md`. Prototype: `prototype/content.js`.

**What this is, and what it is not.** This narration is **hand-authored to the house
standard** (`specs/2026-07-19-tour-quality-standard/01-standard.md`), not produced by the
ingestion engine, which is still being rebuilt. It exists so the interviews test the
*concept* rather than the engine's current state. Nobody may later present these
recordings as engine output, or as evidence that the engine works.

**Sourcing.** Public-domain and government sources — National Park Service, Library of
Congress (HABS), National Archives, NYC Landmarks Preservation Commission, NYC Parks — plus
the institutions' own histories, **with one documented exception**: the 9/11 Memorial plaza
is a private non-profit with no government record, so its four beats rest on the memorial's
own jury statement and backgrounder, the designers' published words, and 2006 press
reporting. No commercial guidebooks anywhere, so unlike the current NYC corpus this material
carries no "private use only" caveat. Every claim's source is listed under each slot. Fact
sheets, every line cited: `sources/facts-walk.md` (146 facts), `sources/facts-liberty.md`
(443 facts), `sources/facts-memorial-design.md` (54 facts, several of them `[PRESS]` rather
than institutional — the file marks which).

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

## 3.1 · 9/11 Memorial plaza — Maya's lens (Modern & Contemporary Design) · ~0:55
*(prototype slot `memorial`; rewritten 2026-09-23 — the first version described the pools,
which the listener can already see, and told no story)*

> Look down at the names on the parapet. You were never meant to read them here.
>
> The design that won this competition took you underground. Down into the footprints,
> below the street, to read the names with the water falling past you. The jury praised
> exactly that — the descent into the outlines the towers left, and reaching bedrock inside
> the north tower's footprint.
>
> Then the costing came in near a billion dollars, and in 2006 a builder was brought in to
> cut it. The underground galleries went. The names came up here, to your elbow.
>
> Michael Arad, who designed it, said he was afraid that in losing the below-ground
> memorial, he was losing the entire memorial.
>
> What you are standing on is the argument he had left.

Sources: LMDC/9/11 Memorial *Jury Statement for the Winning Design*, 13 January 2004 —
the jury's own words about "our descent to the level below the street, down into the
outlines left by the lost towers" and mourning at bedrock in the north tower's footprint;
NBC News and CBS News, 2006 (contractors' near-$1bn estimate; Frank Sciame's review for
Bloomberg and Pataki cutting more than $285m, removing the galleries around the pools where
the names were to be read, and raising the names to street level); Michael Arad, quoted in
Yale News, 28 November 2012 ("I was afraid that in losing this below-ground memorial, I was
losing the entire memorial").
*Note: the 9/11 Memorial is a private non-profit, not a National Park Service site — there
is no NPS source for this plaza, unlike every other stop on this walk.*
*The pools' dimensions and the tree count are gone: they are visible, and the institution
and the landscape architects disagree on the number of trees anyway.*

---

## 3.2 · "Tell me more" at the 9/11 Memorial — second story · ~50s
*(prototype slot `memorial_more`)*

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

Sources: 9/11 Memorial "About the Memorial" and Institutional Backgrounder — names grouped
by location and circumstance then arranged by "meaningful adjacencies", more than 1,200
requests honoured, the described bonds formed during the response, 2,983 names, the 1993
six.
*No emotive framing added: every sentence is the institution's own fact, spoken plainly.*

---

## 3.2 · Third story at the 9/11 Memorial — the trees · ~0:50
*(prototype slot `memorial_trees`; added 2026-09-23 — a gravity-5 anchor carries a stack)*

> Arad's competition entry was the voids and almost nothing else: seven acres of stone with
> two holes torn in it. No trees.
>
> The jury loved the voids. They disliked the seven acres of stone. So they made winning
> conditional — he had to bring in a landscape architect. He called Peter Walker, in
> California, who was on the point of retiring.
>
> Walker took out about half the stone and planted the rest. Four hundred oaks, spaced
> unevenly, so the light through them changes as you walk.
>
> Walker described the division of labour like this: Arad was dealing with the part of the
> problem that is about death. He took the part that is about the continuation of life.
>
> You're standing in the argument between them.

Sources: UC Berkeley News, 25 February 2004 (the jury requiring "a landscape artist of high
caliber"; Arad contacting Walker; Walker near retirement; his "part of this problem that is
about death" / "continuation of life" framing); SFGate, Peter Walker interview (the jury
"loved the idea of the big voids… But they really disliked seven acres of stone plaza";
halving the stone); Domus, 20 November 2003 (the finalist entry credited to Arad alone).
*"Four hundred oaks" not 413: the institution and the landscape architects publish
different counts.*

---

## 3.2 · Fourth story at the 9/11 Memorial — the Survivor Tree · ~0:45
*(prototype slot `memorial_tree`)*

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
recovered October 2001 "with snapped roots and burned and broken branches", cared for by
the NYC Department of Parks and Recreation, returned in 2010, and the described demarcation
between new smooth limbs and gnarled stumps — paraphrased, not quoted).
*No heights: the institution publishes none, and the "eight feet to thirty feet" figures in
circulation are unsourced.*

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
