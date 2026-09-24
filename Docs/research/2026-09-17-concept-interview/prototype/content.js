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
      source: "hand-authored 2026-09-23 — see narration.md",
      text: "Two pools, the same size, side by side, marking where the towers stood. You are at the north one. They are not the same place. When the first plane hit the North Tower, it severed every stairwell above the impact. Nobody above that floor had a way down. In the South Tower, one stairway survived the strike, and a small number of people who were above it walked out. Same shape, same water, same acre. One of them had a way out of it.",
    },
    memorial_before: {
      poi: "9/11 Memorial", lens: "hidden_history", secs: 55, audio: null,
      source: "hand-authored 2026-09-23 — see narration.md",
      text: "Before the towers, this was Radio Row: thirteen blocks of electronics shops, war-surplus dealers and parts bins on the pavement, centred on Cortlandt and Greenwich. Over four hundred merchants. They were condemned, for a reason you would not guess. New Jersey would only agree to the trade centre if the Port Authority took over a failing commuter railway. It did. That railway is the PATH line under your feet. Radio Row was cleared, in part, to rescue it. The merchants fought. On the fourteenth of July, 1962, they carried a black-draped coffin down Cortlandt Street with a sign on it: \"Here lies Mr Small Businessman.\" They lost in the New York Court of Appeals, and the Supreme Court threw out their appeal. The case is named for one of them — a lunch counter called the Courtesy Sandwich Shop.",
    },
    memorial_dig: {
      poi: "9/11 Memorial", lens: "hidden_history", secs: 70, audio: null,
      source: "hand-authored 2026-09-23 — see narration.md",
      text: "To build the towers they dug a hole in the riverbank and kept the Hudson out of it. The wall that does that is three feet thick and seventy feet deep, still there a few metres below you, and visible from inside the museum. More than a million cubic yards came out of this hole, went into the river behind a steel dam, and became Battery Park City. The neighbourhood next door is the hole under your feet. While they dug, fifteen hundred steel anchors held that wall back against the river. Then the basement floors went in and took over the job — and every one of those anchors was cut. On the eleventh of September the floors were destroyed, which left the wall holding back the Hudson with nothing bracing it. The engineer who built it said it should have collapsed. It didn't. But in the days afterwards they poured concrete plugs into the PATH tunnels under the river, rated to hold back eighty feet of water. In case it did.",
    },
    memorial_design: {
      poi: "9/11 Memorial", lens: "modern_design", secs: 45, audio: null,
      source: "hand-authored 2026-09-23 — see narration.md",
      text: "Look down at the names on the parapet. You were never meant to read them here. The design that won this competition took you underground — down into the footprints, below the street, to read the names with the water falling past you. The jury praised exactly that: the descent, and reaching bedrock inside the north tower's footprint. Then the costing came in near a billion dollars, a builder was brought in to cut it, and the galleries went. The names came up here, to your elbow. Michael Arad, who designed it, said he feared that in losing the below-ground memorial he was losing the whole thing. What you are standing on is the argument he had left.",
    },
    memorial_names: {
      poi: "9/11 Memorial", lens: "hidden_history", secs: 50, audio: null,
      source: "hand-authored 2026-09-23 — see narration.md",
      text: "Walk along the parapet and you'll notice the names aren't in alphabetical order. They're grouped by where people were that morning. Which tower. Which floor. Which company. Which flight. Which firehouse. And inside those groups, they were placed by request. Families were asked whether they wanted a name to sit beside particular others. More than twelve hundred of those requests were honoured. Some were for a husband, a sister, a colleague of thirty years. Others were for someone the person had barely known — met that morning, on a stairway, and stayed with. There are two thousand nine hundred and eighty-three names here. Everyone killed on the eleventh of September, 2001, and the six people killed when the same buildings were bombed in February 1993.",
    },
    memorial_parapet: {
      poi: "9/11 Memorial", lens: "modern_design", secs: 45, audio: null,
      source: "hand-authored 2026-09-23 — see narration.md",
      text: "Put your hand on the bronze. In August it should be hot enough to hurt; in February it should take the skin off your fingers. It does neither. Behind those panels, in a crawl space you will never see, there are fourteen thousand feet of copper pipe carrying glycol, heating and cooling the metal to hold it between forty and seventy degrees, all year. The man who ran the memorial's construction explained why they bothered. The bronze is the first thing people touch, he said, and the memorial almost becomes like a baptismal setting: you can wet your hand in the water and leave a print on the name. Somebody engineered that.",
    },
    memorial_tree: {
      poi: "9/11 Memorial", lens: "hidden_history", secs: 40, audio: null,
      source: "hand-authored 2026-09-23 — see narration.md",
      text: "There's one tree here that isn't an oak. It's a Callery pear, and it was already on this site before any of this. They pulled it out of the rubble in October 2001 with its roots snapped and its branches burned and broken, and handed it to the city's parks department, who kept it alive. It came back in 2010. If you find it, look where the trunk changes. The old stumps are gnarled; the limbs growing out of them are smooth. The tree carries the line between before and after in its own wood.",
    },
    trinity: {
      poi: "Trinity Church", lens: "historic_arch", secs: 65, audio: null,
      source: "hand-authored 2026-09-23 from public-domain sources — see narration.md",
      text: "Trinity's spire stands about two hundred and eighty feet above Broadway. When it was finished, in 1846, nothing in New York stood higher. This is the third church on this ground. The first burned in the great fire of September 1776, days after the British took the city — a newspaper that week described the burning steeple as \"a vast pyramid of fire.\" The second lost its roof supports to heavy snow, in 1839. The parish brought in Richard Upjohn to repair that one. He talked them into pulling it down and starting again — and then he did something that got him into trouble. Upjohn was a High Churchman. He gave this church a deep chancel and carved choir stalls, and to a great many New Yorkers in the 1840s that looked like Rome creeping back in. The feeling ran high enough that at first the stalls went unused. He was building an argument, not just a church. And it is the building that made his name.",
    },
    trinity_more: {
      poi: "Trinity Church", lens: "historic_arch", secs: 25, audio: null,
      source: "hand-authored 2026-09-23 from public-domain sources — see narration.md",
      text: "Before you go in, look at the doors. Three pairs of bronze, given by William Waldorf Astor as a memorial to his father, and designed by Richard Morris Hunt — who modelled them on Ghiberti's doors for the Baptistery in Florence. Remember his name. In a few hours you'll be standing on his other New York commission: he designed the pedestal the Statue of Liberty is standing on.",
    },
    fh_maya: {
      poi: "Federal Hall", lens: "historic_arch", secs: 70, audio: null,
      source: "hand-authored 2026-09-23 from public-domain sources — see narration.md",
      text: "Nassau Street and Wall. The white building with the colonnade opened in 1841 as the Custom House — and who designed it is still not a simple question. Ithiel Town and Alexander Jackson Davis won the competition. Then the commissioners decided they disliked the interior, and brought in an English architect, William Ross, to redo it. And John Frazee superintended the entire build and drew most of the working details himself — which is why the record generally calls Frazee the architect. What all of them agreed on was fire. There is no structural wood anywhere in this building. Marble, limestone, granite, brick, iron. Even the roof is marble — overlapping slabs, lapped eight inches, each with a lip above and below, so the stone can move in the heat without letting the rain in. And they built a Greek temple on purpose. Americans in the 1830s had decided they were the heirs of Athens, and they put columns on whatever they wanted taken seriously — banks, churches, custom houses. The democracy was the argument. The marble was the proof.",
    },
    fh_dan: {
      poi: "Federal Hall", lens: "war_conflict", secs: 65, audio: null,
      source: "hand-authored 2026-09-23 from public-domain sources — see narration.md",
      text: "Same steps. Different building, though. The one that matters here was pulled down in 1812. On this site, in 1735, a printer named John Peter Zenger stood trial for seditious libel. He had printed attacks on the royal governor. From the bench, the Chief Justice told the court: \"The laws in my opinion are very clear; they cannot be admitted to justify a libel.\" The jury acquitted him anyway. Thirty years later, delegates from nine colonies met here as the Stamp Act Congress, and wrote down the argument that became the Revolution: that no taxes should be imposed on them \"but with their own consent, given personally or by their representatives.\" And on the thirtieth of April, 1789, from a balcony on that older building, George Washington took the oath as the first president, in a dark brown suit made in America. The balcony's stone floor survived the demolition. It is inside, a few feet from where you are standing.",
    },
    fh_more: {
      poi: "Federal Hall", lens: "historic_arch", secs: 35, audio: null,
      source: "hand-authored 2026-09-23 from public-domain sources — see narration.md",
      text: "One more thing about the first Congress, which met upstairs. In September 1789 they passed twelve amendments to the new Constitution and sent them out to the states. Ten came back ratified two years later, and we call those ten the Bill of Rights. One of the other two — about when Congress may raise its own pay — was never rejected. It simply stayed open. It was finally ratified in 1992, as the twenty-seventh amendment: two hundred and three years after it left this building.",
    },
    bowling: {
      poi: "Bowling Green", lens: "hidden_history", secs: 55, audio: null,
      source: "hand-authored 2026-09-23 from public-domain sources — see narration.md",
      text: "On your right, Bowling Green. The city leased it in 1733 for a rent of one peppercorn a year, to be improved — in the words of the lease — \"for the Recreation & Delight of the Inhabitants of this City.\" The iron fence is the original, from 1771. Look at the tops of the posts: something has been broken off every one of them. The Landmarks Commission says the caps were \"variously described as royal crowns or iron balls.\" The city's own plaque says patriots \"are said to have\" removed them. Nobody can prove it either way. What is certain is the ninth of July, 1776. The Declaration was read to the troops a few blocks north, and the crowd came down Broadway and pulled down the gilded statue of George the Third that stood on this green.",
    },
  },

  // 3.4 Ask about this — sourced answers only (real beat text). The two Washington chips are
  // EXCERPTS of their beats, cut exactly as script.md 3.4 specifies; the rest are verbatim.
  ask: {
    trinity: {
      place: "Trinity Church",
      chips: [
        { q: "Where's Hamilton buried?", source: "public-domain sources — see narration.md",
          a: "In the churchyard on the Broadway side, near the southern fence. Eliza is beside him — she outlived him by fifty years and died at ninety-seven." },
      ],
    },
    federal: {
      place: "Federal Hall",
      chips: [
        { q: "Where did Washington stand?", source: "public-domain sources — see narration.md",
          a: "Not on these steps. The building he stood on was demolished in 1812 — but the balcony's brownstone floor survived, and it's displayed inside, in the lower rotunda." },
        { q: "Can we go inside?", source: "public-domain sources — see narration.md",
          a: "Yes. It's a national memorial, run by the Park Service, and free to enter." },
        { q: "What's inside?", source: "public-domain sources — see narration.md",
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
      { poi: "Castle Clinton", lens: "war_conflict", secs: 90, audio: null, title: "You are standing on water",
        source: "hand-authored 2026-09-23 from public-domain sources — see deep-dive.md",
        text: "Before the boat comes — look down. You're standing on water. Or you would have been. When this fort was built, between 1808 and 1811, it stood on rocks about two hundred feet offshore, and you reached it by a wooden causeway and a drawbridge. The harbour around it was filled in during the 1850s. Everyone who came here before that crossed a bridge to get in. It was called the Southwest Battery. Twenty-eight guns, behind walls eight feet thick — thick enough to carry two more storeys that were never built. Each gun could throw a thirty-two-pound ball about a mile and a half. It never fired one in anger. Neither did any other fort in this harbour, in the whole War of 1812. The Park Service is careful about why: the city was never attacked, it says, whether because the defences deterred the British, or because the British were content to blockade the port instead. They decline to give the fort the credit. Now look out at the water, because this fort was one of a ring of four. Castle Williams, on Governors Island. Fort Gibson, on Oyster Island. And Fort Wood, on Bedloe's Island — the eleven-pointed star fort the Statue of Liberty is standing inside right now. Oyster Island is Ellis Island. Every place you are going today started as a gun position." },
      { poi: "Castle Clinton", lens: "social_change", secs: 125, audio: null, title: "What this room used to be",
        source: "hand-authored 2026-09-23 from public-domain sources — see deep-dive.md",
        text: "The army left in 1821, and the city turned the fort into a pleasure garden. Roofed over, it became the largest hall in New York. Lafayette was received here in 1824. Samuel Morse ran a telegraph line from this building to Governors Island in 1842. And on the eleventh of September, 1850, Jenny Lind — the Swedish soprano P. T. Barnum had shipped to America without ever hearing her sing — gave her first American concert here, to about five thousand people. Most of the tickets were auctioned. The first went for two hundred and twenty-five dollars, to a hatter named John Genin. Barnum wrote afterwards that men across the country \"involuntarily took off their hats to see if they had a 'Genin' hat on their heads.\" He bought a concert ticket and made himself a household name. Barnum had put a hundred and eighty-seven thousand dollars with London bankers before she sang a note. Lind gave her entire share of that first night away. Five years later the singing stopped. On the first of August, 1855, this became an immigrant landing depot — as far as anyone knows, the first place in America built to protect arriving immigrants rather than simply to process them. Inside: railway tickets, currency exchange, baggage storage, letter writing, baths, doctors, a labour exchange, and a list of boarding houses that weren't a swindle. Over the next thirty-five years, eight and a quarter million people walked in through that gate. Three out of every four immigrants to the United States in those years came through this room. Roughly one American in five today can trace an ancestor through it. It closed on a Friday — the eighteenth of April, 1890. Two ships, the Bohemia and the State of Indiana, landed four hundred and sixty-five people. Then the door shut here, and the work moved across the water to Ellis Island." },
      { poi: "Statue of Liberty", lens: "social_change", secs: 80, audio: null, title: "An argument before it was an object",
        source: "hand-authored 2026-09-23 from public-domain sources — see deep-dive.md",
        text: "The statue you're waiting for was an argument before it was an object. In 1865, at a dinner outside Paris, a French legal scholar named Édouard de Laboulaye — president of the French Anti-Slavery Society — proposed a monument to mark the Union's victory in the Civil War and the end of American slavery. He called it Liberty Enlightening the World. That is still her legal name. One of his guests was a young sculptor from Alsace, Frédéric-Auguste Bartholdi. Bartholdi had tried something like this before: he'd proposed a colossal robed woman holding a light at the mouth of the Suez Canal — \"Egypt Carrying the Light to Asia\". The khedive turned him down. He sailed for New York in 1871, and by his own account the idea only became real when he saw this harbour. Within three months he had chosen his island. The abolition argument is still on her, in a detail almost nobody sees — not from the ferry, not from the ground, not from the crown. A broken shackle and a chain lie at her feet, mostly hidden under the drapery, the last link modelled as snapped. And her right foot is raised. She isn't standing. She's walking." },
      { poi: "Statue of Liberty", lens: "science_tech", secs: 100, audio: null, title: "Hammered, not cast",
        source: "hand-authored 2026-09-23 from public-domain sources — see deep-dive.md",
        text: "She is hammered, not cast. More than three hundred sheets of copper, each one beaten by hand over its own wooden mould. The technique is called repoussé. The skin is three thirty-seconds of an inch thick. That's two pennies, face to face. Three hundred and five feet tall, and two pennies thick. Which only works if something inside is holding her. The engineer who designed that frame died before it was built, and the work passed to Gustave Eiffel — eighteen years before his tower. Eiffel put a ninety-two-foot iron pylon up the middle of her, and then did the clever thing. Instead of hanging the copper rigidly off the frame, he connected each piece with flat iron bars that behave like springs. Every sheet of her carries its own weight, and every sheet can move. In a high wind she sways up to three inches. The torch moves six. You would have met her in pieces. The arm and torch were finished first and sent to Philadelphia in 1876, then stood for years in Madison Square, here in New York — a forearm the height of a house, standing on the grass. The head was shown in Paris in 1878. She was finally assembled whole, standing above the rooftops of Paris; then taken apart again into three hundred and fifty pieces, packed into two hundred and fourteen crates, and carried across on a French navy frigate, the Isère. She arrived in June 1885, and she had nowhere to stand." },
      { poi: "Statue of Liberty", lens: "hidden_history", secs: 105, audio: null, title: "The pedestal, and the shame",
        source: "hand-authored 2026-09-23 from public-domain sources — see deep-dive.md",
        text: "The arrangement was simple. France would pay for the statue. America would pay for the thing to stand on. France paid. About two hundred and fifty thousand dollars, raised by public subscription, a national lottery, and a piece Gounod wrote for a benefit at the Paris Opera. Every penny of it from the French public. America's half was costed at a hundred and twenty-five thousand — half as much — and America could not raise it. Appeals went to the public, to the New York legislature and to Congress, and all three said no. By January 1885 the committee had nothing in its treasury. The pedestal had stopped fifteen feet above the ground. The statue was lying in crates on the island. Then Joseph Pulitzer, who had just bought the New York World, started printing. His argument was that this was a gift from the whole people of France to the whole people of America, and that it was shameful to stand waiting for millionaires to pay for it. And he made one promise that changed everything: every single person who gave, however little, would have their name printed in the paper. Schoolchildren sent what they had. In five months, more than a hundred and twenty thousand people sent in a hundred thousand dollars. Do the arithmetic: that is well under a dollar each. The pedestal under the most famous statue in the world was bought in small change. The last stone went on in April 1886, and the workmen threw silver coins out of their own pockets into the wet mortar." },
      { poi: "Statue of Liberty", lens: "literary_heritage", secs: 105, audio: null, title: "The poem that arrived late",
        source: "hand-authored 2026-09-23 from public-domain sources — see deep-dive.md",
        text: "One of the fundraisers, in 1883, was an art auction. The organisers asked a New York poet named Emma Lazarus to write something they could sell. She was thirty-four. She came from a wealthy Sephardic Jewish family, and she had spent that year working with Jewish refugees held on Ward's Island, people who had fled the pogroms in Russia. She had met the people in the poem. She wrote it in two days. Not like the brazen giant of Greek fame… Here at our sea-washed, sunset gates shall stand a mighty woman with a torch, whose flame is the imprisoned lightning, and her name Mother of Exiles. The imprisoned lightning is electric light, which in 1883 was new enough to be a wonder. And \"Mother of Exiles\" is the moment this statue starts being about immigrants at all. You know the rest of it: give me your tired, your poor. What you might not know is that the statue was dedicated without a word of it. Lazarus died in 1887, at thirty-eight, and her poem was forgotten. A friend, Georgina Schuyler, came across it again in 1901. In 1903 it was cast onto a bronze plaque and hung inside the pedestal — seventeen years after the dedication, twenty years after it was written, and sixteen years after the woman who wrote it had died. The statue France sent was about the end of slavery and the friendship of two republics. The statue you are queuing for — the one about immigrants — was made by a poem, and by a plaque hung when almost everyone involved was dead." },
      { poi: "Statue of Liberty", lens: "social_change", secs: 120, audio: null, title: "What they actually saw",
        source: "hand-authored 2026-09-23 from public-domain sources — see deep-dive.md",
        text: "So what did they see, the fourteen million people who came into New York past her? Not a green statue. When she was unveiled she was the colour of a new penny, and the copper took twenty-five or thirty years to turn. The great years of immigration watched her change colour, blotch by blotch. And they did not all go to Ellis Island. First- and second-class passengers were inspected on board and walked off onto the piers of Manhattan. Ellis was for steerage. Everyone saw the statue. Only the poor got the inspection. Edward Steiner crossed in steerage in 1906 to write about it. He said the steerage stayed silent as the ship came up the harbour — and then, as it passed under her shadow, \"a thousand hands are outstretched in greeting to this new divinity.\" Stephen Graham made the same crossing in 1914. He saw her \"far away and diminutive at first, but later on, a celestial figure in a blaze of sunlight.\" Some passengers cheered. Some cried without saying anything. And plenty had no idea what they were looking at. Graham wrote: \"I heard one Russian telling another that it was the tombstone of Columbus.\" Two last things, while you wait. For sixteen years she was an official lighthouse. She was supposed to be visible fifty miles out to sea. She wasn't — and in 1901 the Treasury quietly took her off the list. The most famous lighthouse in the world, retired for not being any use. And nobody has stood in her torch since 1916 — since the night German saboteurs blew up a munitions depot across the water at Black Tom, and the blast drove her arm against her crown. It isn't a safety rule. It's a war wound. Her light stayed on all night." }
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
    "2.1": "Tell it what you already want to see. It plans everything around that.",
    "2.2": "Tell it what you already want to see. It plans everything around that.",
    "2.3": "Four days, built around your must-sees. Open Friday.",
    "2.4": "The big two are yours. Everything in between was picked for you — and it tells you why.",
    "3.1": "You just walk. Stories start on their own when you get there.",
    "3.1b": "You just walk. Stories start on their own when you get there.",
    "3.3": "Dan is hearing a different story at the same moment. Tap his phone to listen in.",
    "4.1": "Your day didn't go to plan. It noticed, and fixed it.",
    "4.2": "Your day didn't go to plan. It noticed, and fixed it.",
    "5.1": "Waiting in line is the best time for the big story.",
    "5.2": "The whole family hears this together, from one phone.",
    "5.3": "Other travellers left these right here. Tap one.",
    "5.4": "Leave something for the next family. Only these words — no free text.",
    "6.1": "Ava's in the same line, playing her own game about the same view.",
    "7.1": "This part uses your own photos — if you let it.",
    "7.2": "Tonight at dinner: what everyone heard, side by side.",
    "7.3": "And when you're home, the whole trip.",
    "7.4": "",
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
  partyStatus: { dan: "Joined · picked 3 lenses", leo: "Joined · picked 2 lenses", ava: "Joined · kids' view with I Spy" },
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
