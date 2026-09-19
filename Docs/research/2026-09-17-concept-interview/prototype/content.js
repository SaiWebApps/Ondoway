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
    { id: "maya", name: "Maya", role: "You · the Planner", lenses: ["historic_arch", "hidden_history", "visual_art"] },
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
      { skipAdvice: true },
      { time: "12:30", name: "Fraunces Tavern", lens: "hidden_history", mins: 50, reason: "Lunch where Washington said farewell to his officers in 1783." },
      { time: "1:30", name: "Castle Clinton", lens: "hidden_history", mins: 25, reason: "Where you buy ferry tickets: a fort, a concert hall, then the gate for 8 million immigrants." },
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
      poi: "9/11 Memorial", lens: "modern_design", secs: 30, audio: null,
      beat: "newyork_9_11_memorial_and_museum_modern_design_frommers-nyc-2024_reflecting_pools_footprints",
      text: "Surrounding the museum is the eight-acre Memorial Plaza, with two reflecting pools and waterfalls set in the one-acre footprints of the individual towers. Each pool is ringed by a brass parapet engraved with the names of victims of both the 9/11 and the February 1993 bombings — arranged not alphabetically but according to where the victims worked, close to their co-workers and friends, or wherever their families felt they belonged.",
    },
    memorial_more: {
      poi: "9/11 Memorial", lens: "modern_design", secs: 50, audio: null,
      beat: "new_york_9_11_memorial_and_museum_modern_design_lonely-planet-new-york-city_reflecting_absence_water",
      text: "Surrounded by a plaza planted with more than 400 white oak trees, the 9/11 Memorial's reflecting pools occupy the footprints of the ill-fated Twin Towers. From their rim, a cascade of water pours 30ft down toward a central void. The flow is richly symbolic, beginning as thousands of smaller streams, merging into a massive torrent of collective confusion, and ending with a slow journey toward an abyss. Bronze panels frame the pools, inscribed with the names of those who died in the attacks of September 11, 2001, and in the World Trade Center car bombing on February 26, 1993. Designed by Michael Arad and Peter Walker, the pools are titled Reflecting Absence.",
    },
    trinity: {
      poi: "Trinity Church", lens: "historic_arch", secs: 57, audio: null,
      beat: "newyork_trinity_church_historic_arch_frommers-nyc-2024_third_church_gothic_revival",
      text: "This is actually the third Trinity Church on the site. The first was destroyed in the fire set by fleeing colonists in 1776 to thwart British occupiers — a blaze that razed a third of Manhattan's structures — and the second was so poorly built its roof collapsed in a snowstorm. The third, consecrated in 1846 and designed by Richard Upjohn, is considered by many the finest Gothic Revival building in the United States. Upjohn used self-consciously medieval features to underscore the sacred, even raising the chancel a few feet rather than railing it off, since formal barriers were controversial in Democratic New York. Among its Gothic features: stained glass, flying buttresses, and a 280-foot spire that was the tallest structure in the city until the Brooklyn Bridge's piers rose.",
    },
    trinity_more: {
      poi: "Trinity Church", lens: "visual_art", secs: 30, audio: null,
      beat: "newyork_trinity_church_visual_art_frommers-nyc-2024_ghiberti_doors_self_portrait",
      text: "The church's bronze doors, modeled after Ghiberti's famous doors for the Baptistery of Florence, were designed by Richard Morris Hunt — the same architect who designed the base of the Statue of Liberty — with their sculptures executed by Austrian immigrant Karl Bitter. Look for Bitter's own self-portrait, the knoblike head poking from the lower right corner of the door; above him is Richard Upjohn, and above that, Richard Morris Hunt.",
    },
    fh_maya: {
      poi: "Federal Hall", lens: "historic_arch", secs: 58, audio: null,
      beat: "newyork_federal_hall_historic_arch_big-onion-ten-historic-tours_parthenon_lenfant_demolished",
      text: "On the northeast corner of Nassau and Wall stands Federal Hall National Memorial, dubbed the \"Parthenon of Wall Street.\" The site has a vigorous past as the city's second city hall, later the Custom House and subtreasury, and now a federal museum. The current Greek-revival structure dates from 1842; the earlier building on the site, where Washington took his oath and the new government operated for its first eighteen months, was designed by Pierre L'Enfant — the master planner of Washington, D.C. — and was demolished in 1790 when the capital moved to Philadelphia. The Greek-revival style suited an age when Athenian democracy and the Greek independence movement produced a bumper crop of Doric-templed civic buildings; later, as abolitionism gained steam, Greece became associated with slavery rather than the demos, and those classical buildings lost their place in the sun.",
    },
    fh_dan: {
      poi: "Federal Hall", lens: "war_conflict", secs: 30, audio: null,
      beat: "newyork_federal_hall_war_conflict_big-onion-ten-historic-tours_british_hq_declaration_read",
      text: "Before its federal function, the building on this site teemed with the birth pangs of the republic. The famed Stamp Act Congress met here in 1765 to draft the Declaration of Grievances against \"taxation without representation.\" The Declaration of Independence was read here on July 9 — it took some days to travel from Philadelphia to New York. And the site even served as the British headquarters during the Revolutionary War.",
    },
    fh_more: {
      poi: "Federal Hall", lens: "historic_arch", secs: 35, audio: null,
      beat: "newyork_federal_hall_historic_arch_national-geographic-walking-nyc_greek_revival_firsts",
      text: "Federal Hall is all about George Washington and \"firsts.\" The bronze statue on the front steps marks the spot where Washington was inaugurated as president in 1789. This was the first capitol building of the new nation, where the first U.S. Congress met, and it was also the original City Hall of New York. The current building — an early example of Greek Revival architecture — was finished in 1842, and the broad steps outside still offer a great view of the narrow historic streets of the nation's first capital.",
    },
    bowling: {
      poi: "Bowling Green", lens: "hidden_history", secs: 31, audio: null,
      beat: "new_york_bowling_green_park_hidden_history_lonely-planet-new-york-city_minuit_purchase_charging_bull",
      text: "New York's oldest public park is purportedly the spot where Dutch settler Peter Minuit paid Native Americans the equivalent of $24 to purchase Manhattan Island. At its northern edge stands Arturo Di Modica's 7000lb bronze Charging Bull, placed here permanently after it mysteriously appeared in front of the New York Stock Exchange in 1989, two years after a market crash.",
    },
  },

  // 3.4 Ask about this — sourced answers only (real beat text). The two Washington chips are
  // EXCERPTS of their beats, cut exactly as script.md 3.4 specifies; the rest are verbatim.
  ask: {
    trinity: {
      place: "Trinity Church",
      chips: [
        { q: "Where's Hamilton buried?", beat: "newyork_trinity_church_famous_residents_frommers-nyc-2024_lawrence_hamilton_graves",
          a: "The graveyard holds many Revolutionary War–era New Yorkers. On the southern side, Captain James Lawrence — who gave the famous War of 1812 command \"Don't give up the ship\" — lies in a tomb shaped like a ship, ringed by a fence made from captured British cannons. Behind him and a little to the right is the cemetery's most famous grave, that of Alexander Hamilton, and beside Hamilton rests steamboat designer Robert Fulton." },
      ],
    },
    federal: {
      place: "Federal Hall",
      chips: [
        { q: "Where did Washington stand?", beat: "newyork_federal_hall_war_conflict_frommers-nyc-2024_washington_oath_office",
          a: "It was here, perhaps on the very spot where the statue now stands, that Washington took the oath of office to become president, though not in this actual building but in an earlier one on this site." },
        { q: "Can we go inside?", beat: "newyork_federal_hall_war_conflict_moon-nyc-walks_washington_oath_first_capitol",
          a: "Today it is a museum about Washington's inauguration, freedom of the press, and Federal Hall — the first capitol, which stood on this spot." },
        { q: "What's inside?", beat: "newyork_federal_hall_hidden_history_national-geographic-walking-nyc_washington_bible_railing",
          a: "Inside, visitors can see the Bible that Washington used for his oath of office, the railing against which he leaned, and the floor upon which he walked — the surviving fragments of the day a new nation swore in its first president." },
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
    note: "A 4-minute chapter — then it's quiet until the boat.",
    chapters: [
      { poi: "Castle Clinton", lens: "social_change", secs: 13, audio: null, beat: "newyork_castle_clinton_social_change_lonely-planet-new-york-city_castle_garden_immigrant_depot",
        text: "Before Ellis Island, the immigrant landing station at Castle Garden — here at the foot of Manhattan — was one of the two gateways through which arriving newcomers passed on their way straight to the Lower East Side." },
      { poi: "Statue of Liberty", lens: "hidden_history", secs: 30, audio: null, beat: "newyork_statue_of_liberty_hidden_history_frommers-nyc-2024_french_dinner_party_origin",
        text: "The statue was dreamed up at a dinner party of French intellectuals in 1865, first proposed as a hundredth-birthday present from France to the United States — and as a not-so-subtle jab at France's own authoritarian Second Empire. Fundraising woes kept it from being finished in time for that anniversary, but after more than a decade of begging for money — a lottery finally did the trick — sculptor Frédéric-Auguste Bartholdi completed the massive work in 1884." },
      { poi: "Statue of Liberty", lens: "social_change", secs: 29, audio: null, beat: "newyork_statue_of_liberty_social_change_frommers-nyc-2024_pulitzer_pedestal_dimes",
        text: "Though the statue was finished, it took another two years for Americans to hold up their half of the bargain and build a pedestal. Newspaperman Joseph Pulitzer finally stepped in, and through a series of angry editorials condemning the wealthy for not contributing, he convinced thousands of lower-income Americans to send in what they could. Thanks to their dimes and nickels the pedestal — designed by Richard Morris Hunt — was at last built, and the statue was dedicated on October 28, 1886." },
      { poi: "Statue of Liberty", lens: "hidden_history", secs: 33, audio: null, beat: "newyork_statue_of_liberty_hidden_history_frommers-nyc-2024_repousse_thin_skin",
        text: "Lady Liberty was created using repoussé, a technique of hammering and shaping thin strips of copper. Though the statue is massive — over 151 feet from base to torch — the \"skin\" is just three thirty-seconds of an inch thick. It's thought the ancient Colossus of Rhodes was built the same way. Every detail carries meaning: the seven rays in the crown stand for the seven seas, the 25 windows nod to 25 gemstones found on Earth, and the tablet bears the Roman numerals for July 4, 1776." },
      { poi: "Statue of Liberty", lens: "science_tech", secs: 50, audio: null, beat: "new_york_statue_of_liberty_science_tech_lonely-planet-new-york-city_eiffel_framework",
        text: "Bartholdi spent almost 20 years turning his dream into reality: a hollow copper colossus mounted in New York Harbor. The project was hindered by serious financial problems but helped by the fund-raising efforts of newspaper publisher Joseph Pulitzer, and by poet Emma Lazarus, whose ode to Lady Liberty was part of a campaign for the pedestal, designed by American architect Richard Morris Hunt. Bartholdi's work was also delayed by structural challenges, a problem resolved by the metal-framework mastery of railway engineer Gustave Eiffel, yes, of the famous tower. Completed in France in 1884, the statue was shipped to NYC as 350 pieces packed into 214 crates and reassembled over four months on the US-made granite pedestal." },
      { poi: "Statue of Liberty", lens: "social_change", secs: 27, audio: null, beat: "new_york_statue_of_liberty_social_change_lonely-planet-new-york-city_mother_of_exiles_lazarus",
        text: "Lady Liberty has been gazing sternly toward 'unenlightened Europe' since 1886. Dubbed the 'Mother of Exiles,' she's often read as a symbolic admonishment to an unjust old world. Emma Lazarus' 1883 poem 'The New Colossus' articulates the challenge: 'Give me your tired, your poor, your huddled masses yearning to breathe free, the wretched refuse of your teeming shore.'" },
      { poi: "Statue of Liberty", lens: "social_change", secs: 36, audio: null, beat: "newyork_statue_of_liberty_social_change_national-geographic-walking-nyc_lazarus_huddled_masses",
        text: "For the more than twelve million immigrants who crossed the Atlantic between 1892 and 1954, the first glimpse of their new home was a one-hundred-and-sixty-five-foot copper woman, green from oxidation, holding a torch of liberty. Today the descendants of those immigrants account for nearly half the current population of the United States. Etched on the pedestal are the famous lines from Emma Lazarus's poem: \"Give me your tired, your poor, / Your huddled masses yearning to breathe free.\"" },
    ],
  },

  // 5.3 Echoes (traveller-made, never voiced). Counts ⚑. x/y are % positions in the panorama.
  echoes: [
    { kind: "Look right →", text: "Ellis Island — the four little spires", x: 84, y: 58, counts: { found: 214, worth: 0, ha: 0 } },
    { kind: "Worth it:", text: "the round walls — this was a fort in 1812", x: 38, y: 80, counts: { found: 0, worth: 88, ha: 0 } },
    { kind: "Best photo:", text: "the statue, from the railing", x: 30, y: 30, counts: { found: 0, worth: 140, ha: 3 } },
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
    "3.3": "Dan is standing right next to you. He's hearing a completely different story.",
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
