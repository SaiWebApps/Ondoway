// PROTOTYPE — throwaway. Concept-interview walkthrough (brief.md / script.md, 2026-09-17).
// THE CONTENT BLOCK. Every content slot lives here; layout never hard-codes copy.
// Swap text, audio and photos here without touching app.js.
//
// Markers carried over from script.md:
//   TODO ✎  = founder writes it before the pilot (shown on screen as a visible TODO).
//   ⚑       = proposed by Claude, accepted by the founder 2026-09-18.
// Story text is real `script_body` from data/new_york/beats.json — chosen, never edited.
// `audio: null` falls back to browser speech. Before the pilot, voice every story with the
// project's real TTS (src/audio/provider.py) and set `audio: "audio/<beat_id>.mp3"`.
// `secs` is the target length from script.md (what the scrubber shows).

window.CONTENT = {
  party: [
    { id: "maya", name: "Maya", role: "You · the Planner", lenses: ["historic_arch", "modern_design", "hidden_history"] },
    { id: "dan", name: "Dan", role: "Partner", lenses: ["war_conflict", "local_legends", "historic_cuisine"], joined: true },
    { id: "leo", name: "Leo", age: 12, role: "12 · own phone", lenses: ["science_tech", "film_tv"], joined: true },
    { id: "ava", name: "Ava", age: 8, role: "8 · own phone", lenses: [], ispy: true },
  ],

  // All 21 lenses, labels from src/schema/definitions.py, colours from
  // mobile/lib/theme/lens_palette.dart, glyphs = the Material icon the app paints.
  lenses: [
    ["hidden_history", "Hidden History", "#4A4F86", "history_edu"],
    ["war_conflict", "War & Conflict", "#A74C4C", "shield"],
    ["dark_history", "Dark History", "#643B96", "bedtime"],
    ["social_change", "Social Change", "#B08041", "campaign"],
    ["historic_arch", "Historic Architecture", "#369289", "account_balance"],
    ["modern_design", "Modern & Contemporary Design", "#3D81A5", "architecture"],
    ["music_heritage", "Music Heritage", "#A63F67", "music_note"],
    ["visual_art", "Visual Art", "#AE7840", "palette"],
    ["street_art", "Street Art", "#709252", "brush"],
    ["film_tv", "Film & TV Locations", "#804399", "movie"],
    ["historic_cuisine", "Historic Cuisine", "#B05C41", "restaurant"],
    ["markets_street_food", "Markets & Street Food", "#BD674C", "storefront"],
    ["local_legends", "Local Legends & Folklore", "#4E8751", "auto_stories"],
    ["literary_heritage", "Literary Heritage", "#5F6E75", "menu_book"],
    ["famous_residents", "Famous Residents", "#B08741", "person_pin_circle"],
    ["historic_worship", "Historic Houses of Worship", "#338A7F", "church"],
    ["sacred_traditions", "Sacred Traditions", "#89469B", "self_improvement"],
    ["parks_gardens", "Parks & Gardens", "#538B56", "park"],
    ["waterways_views", "Waterways & Views", "#3B7AA0", "sailing"],
    ["historic_markets", "Historic Markets & Shopping", "#AA653F", "store"],
    ["science_tech", "Science & Technology", "#3D6EA6", "science"],
  ],

  // 2.1
  plan: {
    city: "New York",
    dates: "Thu Oct 8 – Sun Oct 11 · 4 days",
    mustSees: ["9/11 Memorial", "Statue of Liberty", "The Met", "Central Park"],
    booked: "Statue of Liberty ferry · Fri 2:00 PM",
  },
  // 2.2
  building: ["Reading 4 people's interests…", "Fitting your must-sees…", "Finding what's worth your time between them…", "Checking what's worth skipping…"],
  // 2.3 ⚑
  days: [
    { day: "Thu", area: "Midtown arrival", stars: [], note: "Top of the Rock at sunset", stops: 3 },
    { day: "Fri", area: "Lower Manhattan", stars: ["9/11 Memorial", "Statue of Liberty"], stops: 6, open: true },
    { day: "Sat", area: "Central Park & the Met", stars: ["Central Park", "The Met"], stops: 5 },
    { day: "Sun", area: "Brooklyn Bridge & DUMBO", stars: [], stops: 5 },
  ],
  // 2.4
  friday: {
    stats: [["Stops", "6"], ["Walk", "3.1 mi"], ["Must-sees", "2"], ["Stories", "14"]],
    stops: [
      { time: "9:30", name: "9/11 Memorial", must: true, lens: "modern_design", mins: 90 },
      { time: "11:15", name: "Trinity Church", lens: "historic_arch", mins: 20, reason: "The church that was once the tallest thing in New York — and Hamilton's grave." },
      { time: "11:45", name: "Federal Hall", lens: "historic_arch", mins: 25, reason: "The ‘Parthenon of Wall Street’, on the spot where Washington became president." },
      { time: "12:30", name: "Fraunces Tavern", lens: "hidden_history", mins: 50, reason: "Lunch where Washington said farewell to his officers in 1783." },
      { time: "1:30", name: "Castle Clinton", lens: "hidden_history", mins: 25, reason: "Where you buy ferry tickets: a fort, a concert hall, then the gate for 8 million immigrants." },
      { skipAdvice: true },
      { time: "2:00", name: "Statue of Liberty", must: true, ferry: true, lens: "social_change", mins: 150 },
    ],
  },
  // 2.4 Skip advice — TODO ✎ founder picks/writes the verdict. Drafts shown to him in the
  // interviewer view; the traveller sees `current`. Draft A is grounded in the Liberty
  // local_legends beat (crown_162_steps); draft B still needs a source or founder verdict.
  skipAdvice: {
    heading: "We'd skip: the crown",
    body: "The crown. Tickets go up to six months ahead, four per person, and kids must be 42 in tall. The pedestal view is the one worth having.",
    todo: true,
    drafts: [
      "The crown. Tickets go up to six months ahead, four per person, and kids must be 42 in tall. The pedestal view is the one worth having.",
      "The New York Stock Exchange. You can't go in — see it from Federal Hall's steps, it's across the street. (needs a source)",
    ],
  },

  // Stories. Keyed by slot id. Real beats, never edited.
  stories: {
    memorial: {
      poi: "9/11 Memorial", lens: "hidden_history", secs: 35, audio: null,
      source: "owner revision 2026-09-24 — see scripts.md",
      text: "These two pools mark the places where the Twin Towers stood. Each is nearly an acre, with water falling down all four sides. Follow it toward the middle. It drops again, into a smaller opening whose bottom you can't see from here. That was central to architect Michael Arad's idea: water keeps entering the space, but the space never appears to fill. He called it “absence made visible.” The names around the edges bring that enormous loss back to individual people. We'll take a closer look at how they were placed.",
    },
    memorial_before: {
      poi: "9/11 Memorial", lens: "hidden_history", secs: 55, audio: null,
      source: "owner revision 2026-09-24 — see scripts.md",
      text: "Before the towers, this was a neighborhood of small shops known as Radio Row. People came here for radios, televisions, and electronic parts they couldn't find elsewhere. At first, the shopkeepers welcomed the idea of a World Trade Center. It was supposed to go on the other side of Lower Manhattan, near the East River. More business nearby sounded promising. Then the project moved here. The change was tied to a deal between New York and New Jersey: the Port Authority would build the trade center and take over the struggling Hudson and Manhattan Railroad, now PATH. For the merchants, that meant losing their premises. They protested and challenged the project in court, but the clearance went ahead. So this site had already changed completely once before the towers became the New York skyline people remembered.",
    },
    memorial_dig: {
      poi: "9/11 Memorial", lens: "hidden_history", secs: 65, audio: null,
      source: "owner revision 2026-09-24 — see scripts.md",
      text: "Building the World Trade Center meant digging down to bedrock beside the Hudson River. Before workers could excavate the foundations, they had to keep the surrounding groundwater out. They built a concrete wall underground, roughly three feet thick and seventy feet deep. It enclosed the excavation like the sides of a bathtub. The method sounds back to front. Workers first dug a narrow trench and filled it with a liquid clay mixture to stop the sides collapsing. Then they lowered in reinforcing steel and pumped concrete into the bottom, pushing the clay mixture out. The earth removed from inside that enclosure helped create Battery Park City, across West Street. After the towers collapsed, engineers feared the damaged wall could fail and allow flooding into the site and nearby tunnels. It held, and crews worked to stabilize it during the recovery. A preserved section is inside the museum. What looks like a rough concrete wall was one of the things that made building here possible.",
    },
    memorial_design: {
      poi: "9/11 Memorial", lens: "modern_design", secs: 45, audio: null,
      source: "owner revision 2026-09-24 — see scripts.md",
      text: "The names weren't always going to be here at the edge of the plaza. Michael Arad's early design included galleries below ground. Visitors would descend toward the pools and read the names beside the falling water. That part of the plan was dropped as the project changed. Arad later said he worried that losing those galleries meant losing the memorial itself. The names moved to the bronze panels you see now, within reach of people walking through the plaza. It's a substantial change in how you encounter them. You can stop beside a name without first entering a separate room or going below ground. Remembering happens here, in the open, with the city around you.",
    },
    memorial_names: {
      poi: "9/11 Memorial", lens: "hidden_history", secs: 50, audio: null,
      source: "owner revision 2026-09-24 — see scripts.md",
      text: "The names around these pools aren't alphabetical. Their arrangement also preserves relationships. Families were invited to ask for particular names to be placed near one another. More than twelve hundred requests were honored. One example is here at the North Pool. Richard Barry Ross was a passenger on Flight 11. Stacey Leigh Sanders worked in the North Tower. She was his daughter's best friend. Their names appear together across panels N-2 and N-3. That connection isn't something you could work out just by reading the bronze. You need to know a little about their lives. The Memorial calls this arrangement “meaningful adjacencies.” It gives families a way to preserve who someone was connected to, as well as their name.",
    },
    memorial_parapet: {
      poi: "9/11 Memorial", lens: "modern_design", secs: 35, audio: null,
      source: "owner revision 2026-09-24 — see scripts.md",
      text: "The bronze panels were designed for people to touch. That presented a practical problem: metal outdoors can become very hot in summer and bitterly cold in winter. Beneath the panels is a network of copper pipes. Fluid circulates through them to warm or cool the bronze, helping keep the surface comfortable. There are about fourteen thousand feet of piping involved. You won't see it from the plaza, but it supports one of the simplest things someone might come here to do: rest a hand on a name.",
    },
    memorial_tree: {
      poi: "9/11 Memorial", lens: "hidden_history", secs: 35, audio: null,
      source: "owner revision 2026-09-24 — see scripts.md",
      text: "Among the oaks is a Callery pear known as the Survivor Tree. Recovery workers found it in the rubble in October 2001. Its roots were damaged, and its branches had been burned and broken. The city's Parks Department took it away to care for it. It returned to the site in 2010. Look at the contrast between the older, rough wood and the smoother branches growing from it. Those newer limbs developed after the damage. The tree's recovery took years. You can see some of that history in the way it has grown.",
    },
    trinity: {
      poi: "Trinity Church", lens: "historic_arch", secs: 65, audio: null,
      source: "owner revision 2026-09-24 — see scripts.md",
      text: "Look up at Trinity's spire, then at the buildings around it. When this church was completed in 1846, its spire was the highest point on New York's skyline. Ships used it as a landmark. This is the third Trinity Church on the site. The first burned in 1776. The second developed serious structural problems after heavy snow, and architect Richard Upjohn was brought in to assess it. He recommended replacing it. The pointed arches and tall spire of his new church drew on medieval English architecture. But one of his choices caused a very contemporary disagreement. He provided a deep space for the altar and choir, with choir stalls. Some people thought that arrangement looked too Roman Catholic for an Episcopal church. The stalls initially went unused. It's easy to see this as a settled piece of old New York. When it was new, people were still arguing over how a service should be conducted inside it.",
    },
    trinity_more: {
      poi: "Trinity Church", lens: "historic_arch", secs: 30, audio: null,
      source: "owner revision 2026-09-24 — see scripts.md",
      text: "Take a moment with the bronze doors at the Broadway entrance. The figures are modeled in relief, so the surface has depth as well as detail. These doors brought together architect Richard Morris Hunt and sculptor Karl Bitter. William Waldorf Astor commissioned them in memory of his father. Hunt connects this stop to the harbor: he also designed the Statue of Liberty's pedestal. When you see the statue, take a look at the building beneath her as well.",
    },
    fh_maya: {
      poi: "Federal Hall", lens: "historic_arch", secs: 60, audio: null,
      source: "owner revision 2026-09-24 — see scripts.md",
      text: "With Washington standing outside, you might expect this to be the building where he became president. That happened on this site, but in an earlier building. This one opened in 1842 as a customs house, where duties on imported goods were collected. Look at the columns and the triangular shape above them. The exterior takes its cue from the Parthenon in Athens. Inside, a domed rotunda draws on Roman architecture. Those references gave the business of collecting revenue a public face: a young country presenting itself through the architecture of the ancient world. The materials had a practical purpose too. Marble walls, brick vaults, and a roof of marble slabs helped protect the building from fire. Ithiel Town and Alexander Jackson Davis supplied the winning design; John Frazee supervised construction as it evolved. The result is a building whose appearance tells you about the government's ambitions, while its fabric tells you what it needed to protect.",
    },
    fh_dan: {
      poi: "Federal Hall", lens: "war_conflict", secs: 60, audio: null,
      source: "owner revision 2026-09-24 — see scripts.md",
      text: "George Washington took his first presidential oath here on April thirtieth, 1789. But the building behind his statue came later. The original Federal Hall had a balcony overlooking Wall Street. Washington stepped onto it wearing a dark brown suit made in America, and took the oath in front of the crowd below. That building was demolished in 1812. A large piece of the balcony floor was saved. For years, it was displayed on the grounds of Bellevue Hospital. In 1889, a hundred years after the inauguration, it came back here. Federal Hall preserves that stone today. It even has a later coating of cement, applied so an inscription could be added. So the most direct physical link to the inauguration is a piece of flooring that was moved across the city and brought home again. The grand columns came more than fifty years after Washington's oath.",
    },
    fh_more: {
      poi: "Federal Hall", lens: "historic_arch", secs: 40, audio: null,
      source: "owner revision 2026-09-24 — see scripts.md",
      text: "The first Congress, meeting in the earlier Federal Hall, sent twelve proposed amendments to the states in 1789. Ten became the Bill of Rights. Another proposed that a change to congressional pay should have to wait until after an election for the House of Representatives. It didn't win enough support then. Nearly two centuries later, a college student named Gregory Watson began campaigning for states to ratify it. In 1992, it finally became the Twenty-seventh Amendment. That means one proposal from the Congress that met here was still unfinished business when people were using personal computers.",
    },
    bowling: {
      poi: "Bowling Green", lens: "hidden_history", secs: 50, audio: null,
      source: "owner revision 2026-09-24 — see scripts.md",
      text: "This little park is Bowling Green. The iron fence was put up in 1771, partly to protect a statue of King George the Third standing inside. Five years later, on July ninth, the Declaration of Independence was read to Washington's troops in New York. Soldiers and civilians came here and pulled the king's statue down. The fence survived, though it was damaged and later repaired. Look at the tops of the posts. Their original ornaments are gone. Accounts differ about whether those ornaments were crowns or balls, so the familiar story about cutting off royal crowns needs that qualification. The statue's destruction is well documented. This was a place where the break with Britain became something people could see happening in the street.",
    },
  },

  // 3.4 Ask about this — sourced answers only (real beat text). The two Washington chips are
  // EXCERPTS of their beats, cut exactly as script.md 3.4 specifies; the rest are verbatim.
  ask: {
    trinity: {
      place: "Trinity Church",
      chips: [
        { q: "Where's Hamilton buried?", source: "public-domain sources — see scripts.md",
          a: "In the churchyard on the Broadway side, near the southern fence. Eliza is beside him — she outlived him by fifty years and died at ninety-seven." },
      ],
    },
    federal: {
      place: "Federal Hall",
      chips: [
        { q: "Where did Washington stand?", source: "public-domain sources — see scripts.md",
          a: "Not on these steps. The building he stood on was demolished in 1812 — but the balcony's brownstone floor survived, and it's displayed inside, in the lower rotunda." },
        { q: "Can we go inside?", source: "public-domain sources — see scripts.md",
          a: "Yes. It's a national memorial, run by the Park Service, and free to enter." },
        { q: "What's inside?", source: "public-domain sources — see scripts.md",
          a: "The rotunda — sixty feet across, sixteen Corinthian columns, each a single piece of marble — and the inauguration stone." },
      ],
    },
    noAnswer: "I don't have a sourced answer for that here. I only tell you what we can back up.",
  },

  // 4.1 ⚑
  replan: {
    title: "You lingered at Federal Hall.",
    body: "To make your 2:00 ferry, we've changed the rest of today.",
    changes: [
      { was: "12:30 Fraunces Tavern", now: "moved to Sunday", why: "it's on your way to the bridge", struck: true },
      { was: "Bowling Green", now: "walk past", why: "1 story on the way" },
      { was: "1:30 Castle Clinton", now: "1:25", why: "ferry still on time ✓" },
    ],
    keepWarning: "You'd miss the 2:00 ferry.",
  },
  shorterDay: "Want to finish earlier? We'd drop Fraunces Tavern and walk straight to the ferry.",

  // 5.2 Deep dive — "The copper woman". Real beats; crown-step beats excluded (they disagree).
  deepDive: {
    title: "The copper woman",
    note: "Seven chapters, about twelve minutes — then it's quiet until the boat.",
    chapters: [
      { poi: "Castle Clinton", lens: "war_conflict", secs: 75, audio: null, title: "When Castle Clinton stood offshore",
        source: "owner revision 2026-09-24 — see scripts.md",
        text: "Castle Clinton looks firmly attached to Manhattan now. When it was built, you had to cross a wooden causeway and a drawbridge to reach it. The fort stood on rocks offshore. The connection to land was about two hundred feet long. Later landfill joined it to the Battery. Its original name was the Southwest Battery. Built between 1808 and 1811, it was part of New York's preparations for another possible war with Britain. The curved brownstone walls were eight feet thick, with openings for twenty-eight cannon. Each could fire a thirty-two-pound ball roughly a mile and a half across the water. None was fired in combat during the War of 1812. The British never attacked the city. There's another piece of that harbor defense beneath the Statue of Liberty. Her pedestal sits inside Fort Wood, an eleven-pointed fort completed in the same period. When you reach Liberty Island, look for those low, angled walls around the pedestal. They're older than the statue, and they help explain why that island was already in federal hands when a French sculptor came looking for a place to build." },
      { poi: "Castle Clinton", lens: "social_change", secs: 85, audio: null, title: "A concert hall becomes an arrival hall",
        source: "owner revision 2026-09-24 — see scripts.md",
        text: "This fort has held some very different crowds. After its military use ended, it became Castle Garden, an entertainment venue. In 1850, more than five thousand people attended the American debut of the Swedish singer Jenny Lind here. P. T. Barnum had promoted her arrival with advertisements and ticket auctions. By the time she stepped onto the stage, the concert was an event in its own right. Then, in 1855, Castle Garden became an immigrant receiving station. Some features of the concert hall survived the change, including decorative details and a balcony. Beneath them, arrivals registered their names, exchanged money, and bought railway tickets for the next part of their journey. That last step could be complicated. One historical account preserved by the Park Service describes a Swedish traveler asking for a ticket to Farmington. There were numerous places with that name. Only after more questions, and checking letters in his luggage, did they establish that he meant Farmington in Minnesota. Over eight million immigrants passed through Castle Garden during its years as a depot. When processing ended here in 1890, it moved temporarily to the nearby Barge Office. Ellis Island opened in 1892. So the immigration history associated with the islands also belongs here, in this small building at the edge of Manhattan." },
      { poi: "Statue of Liberty", lens: "social_change", secs: 60, audio: null, title: "The detail at Liberty's feet",
        source: "owner revision 2026-09-24 — see scripts.md",
        text: "The torch is the part of the Statue of Liberty you can recognize from far away. A much less visible detail explains another part of her meaning. At her feet are a broken shackle and chain. Her right heel is lifted as she moves forward. That imagery belongs to the statue's connection with the abolition of slavery. One of the people behind the project was Édouard de Laboulaye, a French legal scholar and abolitionist who admired American democracy. For him, the end of slavery was essential to the country's claim to liberty. The monument also celebrated American independence and the relationship between France and the United States. Its sculptor, Frédéric-Auguste Bartholdi, chose this harbor as its setting after visiting America in 1871. From the ferry, the chain will be difficult to make out. Knowing it's there changes how you read the figure: she is carrying her torch forward, with a broken restraint at her feet." },
      { poi: "Statue of Liberty", lens: "science_tech", secs: 75, audio: null, title: "How a copper statue moves",
        source: "owner revision 2026-09-24 — see scripts.md",
        text: "The Statue of Liberty's copper surface is only about two and a half millimeters thick—roughly the thickness of two American pennies. Craftspeople shaped the sheets by hammering them over forms, a technique called repoussé. The folds of the robe and the features of the face are made from that thin metal skin. Inside, a framework carries it. Gustave Eiffel took over the structural design after the project's first internal designer died in 1879, several years before he built his Paris tower. His solution allowed movement. Flexible metal bars connected the copper to the supporting structure, helping it respond to wind and changes in temperature. There was another practical challenge: getting the finished figure across the Atlantic. The statue was assembled in Paris, then taken apart for shipping. It arrived in New York in June 1885 aboard the French vessel Isère. The pedestal was still unfinished, so reassembly had to wait. When you look at her from the boat, the robe appears heavy. What you're seeing is carefully shaped sheet metal, supported by a structure designed to give a little in the wind." },
      { poi: "Statue of Liberty", lens: "hidden_history", secs: 75, audio: null, title: "The readers who helped finish the pedestal",
        source: "owner revision 2026-09-24 — see scripts.md",
        text: "France would fund the Statue of Liberty. Americans would fund her pedestal. That was the arrangement, but the two projects didn't finish together. By 1884, the American committee had run out of money. The following year, the statue arrived in New York with its base still incomplete. Joseph Pulitzer used his newspaper, the New York World, to ask readers to help close the gap. He made participation visible. The paper would print contributors' names, however small their donations. It also published stories that came with the money, including accounts of children raising funds. By August 1885, the campaign had collected more than a hundred thousand dollars. Most contributions were a dollar or less. This was good business for Pulitzer too: the campaign helped sell newspapers. But it also let people with very little money take a documented part in completing a major public monument. The pedestal was designed by Richard Morris Hunt, who also worked on Trinity's bronze doors. The statue was ready for its dedication in October 1886. When you look at the base, remember that many of the people who helped finish it were newspaper readers sending in small amounts." },
      { poi: "Statue of Liberty", lens: "literary_heritage", secs: 65, audio: null, title: "How the poem became part of the statue",
        source: "owner revision 2026-09-24 — see scripts.md",
        text: "“Give me your tired, your poor” is so closely associated with the Statue of Liberty that it can seem like part of the original commission. It came from a fundraiser. In 1883, organizers of an auction for the pedestal asked the New York poet Emma Lazarus to contribute a poem. She wrote “The New Colossus.” Lazarus had worked with Jewish refugees on Ward's Island, including people fleeing persecution in Russia. That experience informed the welcome she imagined the statue offering. Her name for the figure was “Mother of Exiles.” The poem was published, then gradually slipped from public attention. Lazarus died in 1887, a year after the statue's dedication. Her friend Georgina Schuyler later campaigned to bring the poem back into view. In 1903, its words were placed on a bronze plaque inside the pedestal. That was seventeen years after the statue was unveiled. The words helped shape the meaning many people now associate with Liberty. The monument's story continued to develop after the building work was finished." },
      { poi: "Statue of Liberty", lens: "social_change", secs: 65, audio: null, title: "Seeing Liberty, then entering America",
        source: "owner revision 2026-09-24 — see scripts.md",
        text: "When the Statue of Liberty was unveiled in 1886, her copper surface was brown. The familiar green developed gradually as the metal reacted with its surroundings. By 1906, she had acquired her green patina. For immigrants arriving through New York, the view changed over those years. What happened after they passed the statue also depended on how they had traveled. First- and second-class passengers were generally inspected aboard their ships. Steerage passengers—the people traveling in the least expensive accommodation—usually went to Ellis Island for inspection. So passing Liberty and being admitted to the country were separate moments. The statue couldn't tell a passenger how the inspection would go. Ellis Island processed roughly twelve million immigrants during its years of operation, from 1892 to 1954. Their individual journeys, circumstances, and experiences varied enormously. As you cross the harbor, keep the two islands distinct. Liberty Island holds the monument and its promise. Ellis Island tells the story of the people who arrived, and the system they had to pass through." }
    ],
  },

  // 5.3 Echoes (traveller-made, never voiced). Counts ⚑. x/y are % positions in the panorama.
  echoes: [
    { kind: "Look right →", text: "Ellis Island — the four little spires", x: 84, y: 58, counts: { found: 214, worth: 0, ha: 0 } },
    { kind: "Worth it:", text: "the round walls — this was a fort in 1812", x: 38, y: 60, counts: { found: 0, worth: 88, ha: 0 } },
    { kind: "Best photo:", text: "the statue, from the railing", x: 35, y: 30, counts: { found: 0, worth: 140, ha: 3 } },
  ],
  // 5.4
  echoTemplates: ["Look ___ at ___", "Worth it: ___", "Skip: ___", "Best photo: ___", "Don't miss ___"],
  echoDirections: ["up", "left", "right", "behind you"],
  echoThings: ["the statue", "the fort", "the harbor", "Ellis Island", "the ferry", "the view"],

  // 6.1 I Spy — TODO ✎ founder reviews ALL child copy before the pilot.
  ispy: {
    title: "I Spy — the harbor",
    finds: [
      { prompt: "I spy a big green lady. Find her crown — how many points does it have?", answer: "7 — one for each of the seven seas!", todo: true },
      { prompt: "I spy a building on an island with four little pointy towers.", answer: "That's Ellis Island, where millions of families arrived!", todo: true },
      { prompt: "TODO ✎ — founder writes the third find.", answer: "TODO ✎ — answer.", todo: true, placeholder: true },
    ],
  },

  // 7.2 Day recap
  dayRecap: {
    header: "Friday · Lower Manhattan",
    stats: ["3.1 mi", "6 stops", "14 stories"],
    photos: [
      { stop: "9/11 Memorial", time: "9:48" }, { stop: "Trinity Church", time: "11:21" },
      { stop: "Federal Hall", time: "11:52" }, { stop: "Bowling Green", time: "1:08" },
      { stop: "Castle Clinton", time: "1:31" }, { stop: "Statue of Liberty", time: "2:40" },
    ],
    sameSpot: [
      { who: "Maya", lens: "historic_arch", line: "…dubbed the “Parthenon of Wall Street.”" },
      { who: "Dan", lens: "war_conflict", line: "…the British headquarters during the Revolutionary War." },
      { who: "Leo", lens: null, line: "Leo skipped this one" }, // no Federal Hall beat in science_tech / film_tv
      { who: "Ava", lens: null, line: "Found the crown — 7 points!", ispy: true },
    ],
    askPrompt: ["Ask Dan", "what happened here in 1765."],
    echoFound: "found by 3 people so far",
  },

  // 7.3 Trip recap ⚑
  tripRecap: [
    { big: "4 days · 11.4 miles · 41 stories" },
    { big: "You're The Architect.", small: "61% of what you heard was about buildings." },
    { big: "The family's favourite stop: Federal Hall.", small: "Replayed 3 times." },
    { big: "Your echo at Castle Clinton was found 27 times." },
    { share: true, big: "New York · Ondoway" },
  ],

  // Photo slots — TODO before the pilot: public-domain / licensed images. `null` draws a
  // labelled placeholder. Paths are relative to this folder (e.g. "img/family.jpg").
  photos: { family: null, castleClintonScene: null },

  // Captions — the one line under each screen. Product copy from script.md, in full.
  captions: {
    "0.1": "Click through as if this were your phone. Think out loud.",
    "1.1": "Pick what you're into. Tap any lens to see what it covers.",
    "1.2": "Everyone brings their own phone and their own interests.",
    "2.1": "Maya's trip: four days, and the places she already wants to see.",
    "2.2": "Building the four days.",
    "2.3": "Four days. Friday is the one you'll walk.",
    "2.4": "Friday, stop by stop.",
    "3.1": "9:30, walking the first leg. The story starts when you arrive.",
    "3.1b": "Second stop: Trinity Church.",
    "3.3": "Dan is hearing a different story at the same moment. Tap his phone to listen in.",
    "4.1": "Something has changed in the day.",
    "4.2": "Walking again. The ferry is at two.",
    "5.1": "You've reached the ferry line at Castle Clinton.",
    "5.2": "The whole family hears this together, from one phone.",
    "5.3": "Hold the phone up at the harbour. Tap an echo.",
    "5.4": "Leave something for the next family. Only these words — no free text.",
    "6.1": "Ava's in the same line, playing her own game about the same view.",
    "7.1": "This part uses your own photos — if you let it.",
    "7.2": "Tonight at dinner: what everyone heard, side by side.",
    "7.3": "And when you're home, the whole trip.",
    "7.4": "That's everything. Back to the conversation.",
  },
};

// ---------------------------------------------------------------- Revision 2026-09-18
// Decisions from the 2026-09-18 grilling (script.md, dated 'revised 2026-09-18').
// ⚑ = drafted by Claude for founder review. Ask answers below are generated verbatim from
// data/new_york/beats.json by beat id.
Object.assign(window.CONTENT.ask, {
 "memorial": {
  "place": "the 9/11 Memorial",
  "chips": [
   {
    "q": "What's in the museum?",
    "beat": "newyork_9_11_memorial_and_museum_hidden_history_frommers-nyc-2024_ground_zero_cross",
    "a": "Among the artifacts museumgoers encounter is the famous Ground Zero cross — a severed chunk of metal beams that accidentally formed this Christian symbol — alongside a squashed fire truck. A thought-provoking section explores the rise of Al Qaeda; you can even see a brick from Bin Laden's Abbottabad compound. The Memorial Room tells the story and shows the face of every person who died on 9/11."
   },
   {
    "q": "What's the white building?",
    "beat": "new_york_9_11_memorial_and_museum_modern_design_lonely-planet-new-york-city_calatrava_oculus",
    "a": "The image of a flying dove allegedly inspired Santiago Calatrava's dramatic white Oculus above the new WTC Transportation Hub. Made from 36,500 tons of steel, the arresting structure streams natural light into the $3.9-billion transit center, which serves 250,000 train commuters daily. A whopping 2.5 times bigger than Grand Central Terminal, it also features multiple levels of retail and dining space. Every year on September 11, the central skylight is opened for 102 minutes, the length of time from the first attack to the collapse of the second tower."
   }
  ]
 },
 "bowling": {
  "place": "Bowling Green",
  "chips": [
   {
    "q": "What's the bull about?",
    "beat": "newyork_bowling_green_park_visual_art_frommers-nyc-2024_charging_bull_guerrilla",
    "a": "At the uptown end of the park stands Charging Bull. In 1989, a recession inspired artist Arturo Di Modica to create the sculpture as a symbol of bull markets to come. The city hadn't asked for this work of boosterism: Di Modica trucked it over to Wall Street in the dead of night and dumped it below a giant Christmas tree in front of the New York Stock Exchange. The seven-thousand-pound statue proved so popular that the city decided to let the 'gift' stay — though not in its original spot."
   },
   {
    "q": "Why is it called Bowling Green?",
    "beat": "newyork_bowling_green_park_parks_gardens_frommers-nyc-2024_first_park_fort_george",
    "a": "This was the city's first official park, established in 1733. Had you been a soldier during the British Colonial era, you'd have been stationed at the fort that once stood here, Fort George, and likely passed your leisure time lawn bowling where the park now stands — hence the name, Bowling Green Park."
   }
  ]
 },
 "castle": {
  "place": "Castle Clinton and the Statue of Liberty",
  "chips": [
   {
    "q": "Can we climb to the crown?",
    "beat": "new_york_statue_of_liberty_local_legends_lonely-planet-new-york-city_crown_162_steps",
    "a": "Visitors who reserve in advance can climb the steep 162 steps from the pedestal level up to Lady Liberty's crown, where the city and harbor views are breathtaking. Crown access is extremely limited, with up to a six-month lead time, a maximum of four tickets per customer, and children required to be at least 42in (3.5ft) tall."
   },
   {
    "q": "What was this fort for?",
    "beat": "newyork_castle_clinton_war_conflict_frommers-nyc-2024_west_battery_fort_1807",
    "a": "This circular stone structure — taller in some earlier incarnations — has been at the center of New York life over the years. In 1807, the West Battery, as it was then called, was built as a fort on a landfill island in the water off Manhattan, to ward off British invasions. It never saw action, however: during the War of 1812 the British attacked Washington, D.C., instead. In 1817 it was renamed Castle Clinton in honor of Mayor De Witt Clinton, and in 1823 the federal government ceded the site to the city."
   }
  ]
 }
});

Object.assign(window.CONTENT, {
  // 1.1 — ⚑ one line per lens, rewritten for travellers from the extraction definitions in
  // .claude/commands/beat-from-book.md. They say only what that lens's stories are about.
  lensInfo: {
    hidden_history: "The surprising stories most visitors never hear — the “I had no idea” details.",
    war_conflict: "Battles, sieges, occupations and resistance: what was fought over here.",
    dark_history: "Crime, scandal, disaster and tragedy: the unsettling side of a place.",
    social_change: "Protests, movements and power shifts: where people changed the rules.",
    historic_arch: "How buildings were designed and built: the architects, materials and styles.",
    modern_design: "Architecture and city design from the 20th century on, and the thinking behind it.",
    music_heritage: "The musicians, performances and sounds tied to a place.",
    visual_art: "Paintings, sculpture and the artists behind them.",
    street_art: "Murals, graffiti and the art out on the street itself.",
    film_tv: "The films and shows shot right here: the scene, the director, the story behind it.",
    historic_cuisine: "Legendary restaurants and chefs, and where famous dishes began.",
    markets_street_food: "Markets and street food, and the people who made them.",
    local_legends: "Folklore, myths, ghost stories and the tales locals love to tell.",
    literary_heritage: "Writers, and the books, poems and plays tied to a place.",
    famous_residents: "The people who lived and worked here, and what happened to them.",
    historic_worship: "The history of houses of worship and the communities around them.",
    sacred_traditions: "Rituals, pilgrimages and spiritual practices tied to a place.",
    parks_gardens: "The history and character of parks and green spaces.",
    waterways_views: "Rivers, harbors, bridges — and the best views.",
    historic_markets: "Shopping streets, trade and the business of the city.",
    science_tech: "Inventions, discoveries and engineering firsts.",
  },

  // 1.2 — party copy
  partyHow: "Invite your party with a link. Each person joins on their own phone and picks their own lenses.",
  partyWhy: "Why invite them? At the same stop, each of you hears the story that fits you.",
  partyStatus: { dan: "Joined · picked 3 lenses", leo: "Joined · picked 2 lenses · kid-safe stories", ava: "Joined · kids' view — stories written for her, plus I Spy" },
  // Grounded: beats carry a kid_friendly flag. At the 9/11 Memorial 11 of the 17 stories in
  // the NYC corpus are marked not for children — this is a filter the product already has,
  // not a promise. (Added 2026-09-23 on owner feedback: the party screen should say that
  // children get a kid-friendly tour, not only a game.)
  partyKids: "Children get their own version of the day, not just a game. Every story is marked for whether it suits a child — at the 9/11 Memorial, most of ours are not. Ava hears the ones that are, told for an eight-year-old, and plays I Spy where there is nothing we would tell her.",
  inviteVia: ["Messages", "WhatsApp", "Copy link"],
  inviteDone: "Invite ready — in the real app this sends a link.",

  // 2.1 — ⚑ must-sees that can be added; each lands on a day other than Friday.
  mustSeeOptions: [
    { name: "Empire State Building", day: "Thu", why: "Added to Thursday — it's near your Midtown arrival." },
    { name: "Grand Central Terminal", day: "Thu", why: "Added to Thursday — it's near your Midtown arrival." },
    { name: "Top of the Rock", day: "Thu", why: "Already on Thursday — it's your sunset.", already: true },
    { name: "The High Line", day: "Thu", why: "Added to Thursday — its north end is a short walk from Midtown." },
    { name: "Solomon R. Guggenheim Museum", day: "Sat", why: "Added to Saturday — it's on Fifth Avenue, by the Park and the Met." },
    { name: "American Museum of Natural History", day: "Sat", why: "Added to Saturday — it's just across Central Park." },
    { name: "Brooklyn Bridge", day: "Sun", why: "Already on Sunday — you walk it on the way to DUMBO.", already: true },
    { name: "Brooklyn Bridge Park", day: "Sun", why: "Added to Sunday — it's right under the bridge." },
  ],
  mustSeeNotFound: "Try one of these for the demo.",

  // Echoes at every stop ⚑ — templates + word lists only, grounded in each stop's beats.
  // x/y are % positions IN THE PHOTO (the camera pans across it).
  stopNames: { memorial: "9/11 Memorial", trinity: "Trinity Church", federal: "Federal Hall", bowling: "Bowling Green", castle: "Castle Clinton" },
  stopEchoes: {
    memorial: [
      { kind: "Don't miss", text: "the names", x: 18, y: 62, counts: { found: 0, worth: 312, ha: 0 } },
      { kind: "Look left", text: "at the Oculus", x: 4, y: 18, counts: { found: 96, worth: 0, ha: 0 } },
    ],
    trinity: [
      { kind: "Don't miss", text: "the bronze doors", x: 50, y: 82, counts: { found: 0, worth: 131, ha: 0 } },
      { kind: "Look behind you", text: "at Hamilton's grave", x: 10, y: 50, counts: { found: 203, worth: 0, ha: 0 } },
    ],
    federal: [
      { kind: "Worth it:", text: "the rotunda", x: 50, y: 62, counts: { found: 0, worth: 177, ha: 0 } },
      { kind: "Look behind you", text: "at the J. P. Morgan building", x: 70, y: 20, counts: { found: 88, worth: 0, ha: 4 } },
    ],
    bowling: [
      { kind: "Don't miss", text: "the fence", x: 22, y: 38, counts: { found: 0, worth: 64, ha: 0 } },
      { kind: "Look up", text: "at the old Standard Oil building", x: 58, y: 12, counts: { found: 97, worth: 0, ha: 0 } }, // founder 2026-09-18: the Charging Bull has no free photo
    ],
  },
  echoThingsByStop: {
    memorial: ["the pools", "the names", "the oak trees", "the Oculus", "the museum"],
    trinity: ["the spire", "the bronze doors", "Hamilton's grave", "the churchyard", "the ship-shaped tomb"],
    federal: ["Washington's statue", "the steps", "the rotunda", "the J. P. Morgan building", "the columns"],
    bowling: ["the bull", "the fence", "the park", "the old Standard Oil building"],
    castle: ["the statue", "the fort", "the harbor", "Ellis Island", "the ferry", "the view"],
  },

  // 7.3 — ⚑ stickers: only things that happened on this trip.
  stickers: [
    { id: "parthenon", text: "Parthenon of Wall Street", card: 2 },
    { id: "crown", text: "7 points!", card: 3 },
    { id: "hamilton", text: "Hamilton's grave ✓", card: 3 },
    { id: "miles", text: "11.4 mi 👟", card: 1 },
    { id: "stories", text: "41 stories", card: 1 },
    { id: "architect", text: "The Architect", card: 2 },
    { id: "echo", text: "My echo", card: 4, fromEcho: true },
  ],
  filters: [
    { id: "original", label: "Original", css: "none" },
    { id: "golden", label: "Golden hour", css: "sepia(.35) saturate(1.35) brightness(1.05) hue-rotate(-8deg)" },
    { id: "harbor", label: "Harbor", css: "saturate(.85) hue-rotate(12deg) brightness(1.02) contrast(1.05)" },
    { id: "mono", label: "Mono", css: "grayscale(1) contrast(1.1)" },
  ],
  musicServices: ["Apple Music", "Spotify"],
  musicNote: "In the real app, this plays a song from your library under your recap.",
  shareOptions: ["Send to your party", "Instagram Story", "TikTok", "WhatsApp", "Messages", "Copy link"],
  shareDone: "Ready to post — in this demo nothing is shared.",
  sharePartyDone: "Sent to Dan, Leo & Ava (demo)",

  // Photo slots — approved by the founder 2026-09-18 from the contact sheet; sources and
  // licences in CREDITS.md. null draws a labelled placeholder. *_wide and castle_camera are
  // panned in the camera view (castle_camera = the harbor view from Castle Clinton).
  img: {
    hero_family: "img/hero_family.jpg",
    memorial_wide: "img/memorial_wide.jpg",
    trinity_wide: "img/trinity_wide.jpg",
    federal_wide: "img/federal_wide.jpg",
    fraunces_thumb: "img/fraunces_thumb.jpg",
    bowling_wide: "img/bowling_wide.jpg",
    castle_wide: "img/castle_wide.jpg",
    liberty: "img/liberty.jpg",
    day_thu: "img/day_thu.jpg",
    day_sat: "img/day_sat.jpg",
    day_sun: "img/day_sun.jpg",
    recap_family_ferry: "img/recap_family_ferry.jpg",
    recap_family_landmark: "img/recap_family_landmark.jpg",
    recap_family_street: "img/recap_family_street.jpg",
    recap_kid_looking: "img/recap_kid_looking.jpg",
    castle_camera: "img/castle_camera.jpg",
  },
  imgAspect: {memorial_wide: 2.222, trinity_wide: 0.75, federal_wide: 1.333, bowling_wide: 1.333, castle_wide: 1.5, castle_camera: 2.292}, // width/height of each camera photo
});
