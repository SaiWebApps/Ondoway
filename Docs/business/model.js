// Ondoway money model: every input with its evidence, and the math.
// It models the MVP being built, not today's code. The page (ondoway-financial-model.html) loads
// this file; nothing else holds a copy of the math. No DOM access here.
"use strict";

const CHECKED = "checked 9 Oct 2026";
const MEASURE = "Docs/business/tour-cost-measurement.json";

// Evidence kinds, in the order the page lists them.
const TAGS = {
  measured: "Measured",
  source: "Public source",
  owner: "Your figure",
  guess: "Guess, to prove",
  choice: "Your choice",
};

// One row per input. base/low/high are the three scenarios; a "choice" stays at base in every
// scenario. main: shown in the main sliders. showIf: only relevant for some settings.
const INPUTS = [
  // Price
  {g:"Price",id:"model",label:"How we charge",type:"select",tag:"choice",base:"party",
   options:[["party","A pass per party"],["person","A pass per person"],["tour","Pay per tour"]],
   src:"A party shares one trip, and the app stores audio per trip stop (src/api/routes/audio.py), so one family listens to one set of audio. That makes per-party passes cost the same to serve as per-person ones."},
  {g:"Price",id:"priceSolo",label:"Solo pass",min:4.99,max:99.99,step:1,base:19.99,fmt:"usd2",tag:"choice",main:true,showIf:p=>p.model==="party",
   src:"The deck's price list. Competitors: Routes 7-day $12.99, Autio 14-day $14.99, VoiceMap Tour Pass $24.99 (App Store, "+CHECKED+")."},
  {g:"Price",id:"priceCouple",label:"Couple pass",min:4.99,max:149.99,step:1,base:29.99,fmt:"usd2",tag:"choice",main:true,showIf:p=>p.model==="party",
   src:"The deck's price list."},
  {g:"Price",id:"priceGroup",label:"Group pass (up to 5)",min:4.99,max:199.99,step:1,base:39.99,fmt:"usd2",tag:"choice",main:true,showIf:p=>p.model==="party",
   src:"The deck's price list. Inside the $29–49 household range the interview cards tested (branch concept-interview-prototype, script.md)."},
  {g:"Price",id:"coupleShare",label:"Buyers who pick the couple pass",min:0,max:1,step:.05,base:.4,fmt:"pct",tag:"guess",showIf:p=>p.model==="party",
   src:"No data on how New York parties split. Find out from the first buyers."},
  {g:"Price",id:"groupShare",label:"Buyers who pick the group pass",min:0,max:1,step:.05,base:.2,fmt:"pct",tag:"guess",showIf:p=>p.model==="party",
   src:"No data. The rest buy the solo pass."},
  {g:"Price",id:"pricePerson",label:"Pass price per person",min:2.99,max:79.99,step:1,base:12.99,fmt:"usd2",tag:"choice",main:true,showIf:p=>p.model==="person",
   src:"Not tested with anyone yet."},
  {g:"Price",id:"pricePerTour",label:"Price per tour",min:1.99,max:49.99,step:1,base:7.99,fmt:"usd2",tag:"choice",main:true,showIf:p=>p.model==="tour",
   src:"VoiceMap sells single tours for $4.99–19.99 (App Store, "+CHECKED+")."},
  {g:"Price",id:"toursBought",label:"Tours each buyer pays for",min:1,max:6,step:.5,base:1.5,fmt:"x",tag:"guess",main:true,showIf:p=>p.model==="tour",
   src:"No data."},
  {g:"Price",id:"webShare",label:"Sales through our own web checkout",min:0,max:1,step:.05,base:0,fmt:"pct",tag:"choice",
   src:"In the US, linking out to web checkout costs no Apple fee today, but a court is still setting one (Apple proposed 5% for small developers; MacRumors, 13 Aug 2026)."},
  {g:"Price",id:"webPct",label:"Web card fee, percent",min:0,max:.06,step:.001,base:.029,fmt:"pct1",tag:"source",showIf:p=>p.webShare>0,
   src:"Stripe's US list price: 2.9% + $0.30 per card payment."},
  {g:"Price",id:"webFixed",label:"Web card fee, per payment",min:0,max:1,step:.05,base:.30,fmt:"usd2",tag:"source",showIf:p=>p.webShare>0,
   src:"Stripe's US list price: 2.9% + $0.30 per card payment."},

  // What a tour costs
  {g:"What a tour costs",id:"freshStop",label:"A stop written fresh",min:.1,max:2,step:.01,base:.50,low:.60,high:.40,fmt:"usd2",tag:"measured",
   src:MEASURE+": a New York tour run on 11 Nov 2026. The stop it wrote fresh cost $0.50: writing its short and longer telling (Claude Opus), fact checks (Claude Haiku) and voice (OpenAI). The same tour varies by up to 20% from run to run, which sets the low and high cases."},
  {g:"What a tour costs",id:"recipeStop",label:"A stop told from a recipe",min:0,max:.2,step:.005,base:.015,fmt:"usd3",tag:"measured",
   src:MEASURE+": the same tour's two stops told from stored recipes cost $0.015 each: fact checks and a short opening line. Their stored audio is reused."},
  {g:"What a tour costs",id:"recipeShare",label:"Stops told from a stored recipe",min:0,max:1,step:.05,base:.5,low:.3,high:.67,fmt:"pct",tag:"guess",main:true,
   src:"A stop is told from its recipe when the walker is solo, the tour has one lens or none, the visit stays outside the door, and it is not the day's last stop. The measured tour told 2 of its 3 stops from recipes. Days with museum visits, couples or families run lower."},
  {g:"What a tour costs",id:"stopsPerDay",label:"Narrated stops in a tour day",min:1,max:20,step:1,base:6,fmt:"int",tag:"choice",
   src:"A full day of about six narrated stops, between the places the traveller already planned (museums, meals, rests). Each stop costs the measured price above."},
  {g:"What a tour costs",id:"replanStops",label:"Stops re-written when the day changes",min:0,max:10,step:1,base:1,low:2,high:0,fmt:"int",tag:"guess",
   src:"The day re-plans live. Each re-planned stop is written and voiced at the same price. No data yet on how often days change."},
  {g:"What a tour costs",id:"daysPerPass",label:"Tour days each buyer uses",min:1,max:10,step:.5,base:4,low:5,high:2,fmt:"x",tag:"guess",main:true,showIf:p=>p.model!=="tour",
   src:"The pass covers 5 days from first use. Assumes people tour on 4 of them. Low case: all 5. High case: 2."},

  // Recipes
  {g:"Recipes",id:"recipesPerCity",label:"Recipes a city needs",min:100,max:5000,step:5,base:1455,fmt:"int",tag:"measured",
   src:"New York has 396 places with stories and needs 1,455 recipes: one per place, plus one per lens its stories carry. Other cities are assumed the same size."},
  {g:"Recipes",id:"recipeCost",label:"Cost to build one recipe",min:.05,max:2,step:.01,base:.37,low:.45,high:.30,fmt:"usd2",tag:"measured",
   src:MEASURE+": two New York recipes built for $0.74 on 11 Nov 2026, one with a longer telling and one without. A city's recipes are built once, before it opens, and reused by every tour."},

  // Free first walk
  {g:"Free first walk",id:"freeStops",label:"Free walk length (stops)",min:1,max:10,step:1,base:3,fmt:"int",tag:"choice",
   src:"The free first walk is one shared walk per city, written and voiced once and played to every install. So it costs its stops once per city, not once per install."},

  // Who buys
  {g:"Who buys",id:"nycVisits",label:"New York leisure visits a year",min:5e6,max:70e6,step:1e5,base:52.4e6,fmt:"m",tag:"source",
   src:"NYC Tourism + Conventions, 24 Mar 2026: 65.0M visits in 2025 (52.4M domestic + 12.5M international), minus 12.6M business visits, so 52.4M leisure visits, day trips and overnight."},
  {g:"Who buys",id:"cityVisits",label:"Each later city: visits a year",min:1e6,max:30e6,step:1e5,base:7.0e6,fmt:"m",tag:"source",
   src:"Euromonitor Top 100 City Destinations, 5 Dec 2025: 702M international arrivals across 100 cities, so 7.0M each on average. International visitors only, so this runs low."},
  {g:"Who buys",id:"partySize",label:"People per travel party",min:1,max:4,step:.1,base:2,fmt:"x",tag:"guess",
   src:"No official New York figure exists."},
  {g:"Who buys",id:"categoryUse",label:"Travellers who use tour apps",min:0,max:.5,step:.01,base:.13,fmt:"pct",tag:"source",
   src:"Arival, 4 Aug 2026: 13% of US and European travellers took a self-guided phone tour in the past year."},
  {g:"Who buys",id:"ourShare",label:"Tour-app users who install Ondoway",min:0,max:1,step:.01,base:.10,low:.03,high:.25,fmt:"pct",tag:"guess",main:true,
   src:"The biggest unknown. Prove it in New York."},
  {g:"Who buys",id:"conv",label:"Installs that buy a pass",min:.002,max:.15,step:.001,base:.05,low:.02,high:.10,fmt:"pct1",tag:"guess",main:true,
   src:"A free first walk is a trial of a one-off trip pass, so it should convert better than subscription apps (RevenueCat 2026: 2.1% median, 4.5% top quarter, for apps with a free tier). 5% is the base to prove in New York; low 2%, high 10%."},

  // Cities
  {g:"Cities",id:"maxCities",label:"Cities planned",min:1,max:30,step:1,base:23,fmt:"int",tag:"choice",
   src:"The deck plans up to 23 cities. City two is Paris, already built."},
  {g:"Cities",id:"nycOnly",label:"Months of New York only",min:1,max:24,step:1,base:6,fmt:"int",tag:"choice",
   src:"The deck: six months of New York only."},
  {g:"Cities",id:"cadence",label:"Months between new cities",min:1,max:12,step:.5,base:1.5,fmt:"x",tag:"choice",
   src:"The deck: a new city every six weeks. The cost of building a city is not recorded anywhere."},
  {g:"Cities",id:"ramp",label:"Months for a new city to reach full reach",min:1,max:18,step:1,base:6,low:9,high:4,fmt:"int",tag:"guess",
   src:"No data."},

  // Marketing
  {g:"Marketing",id:"adBudget",label:"Paid ads per month",min:0,max:50000,step:250,base:0,fmt:"usd0",tag:"choice",
   src:"$0 while bootstrapped. Ads add installs at the cost per install below."},
  {g:"Marketing",id:"cpi",label:"Cost per paid install",min:.5,max:10,step:.05,base:3.0,low:3.85,high:2.24,fmt:"usd2",tag:"source",
   src:"No travel-specific US figure found. iPhone app averages run $2.24 (Adjust, worldwide) to $3.85 (North America)."},
  {g:"Marketing",id:"otherMkt",label:"Other marketing per month",min:0,max:20000,step:100,base:0,fmt:"usd0",tag:"choice",
   src:"Creators, hotel QR cards. The installs they bring are inside 'Tour-app users who install us'."},

  // Ticket commission
  {g:"Ticket commission",id:"bookShare",label:"Buyers who book a ticket through us",min:0,max:1,step:.05,base:.3,fmt:"pct",tag:"guess",
   src:"No data. Apple takes no cut of real-world services (App Store guideline 3.1.3(e))."},
  {g:"Ticket commission",id:"ticketsPerBooking",label:"Tickets per booking",min:1,max:5,step:1,base:2,fmt:"int",tag:"guess",
   src:"Matches people per party. No data."},
  {g:"Ticket commission",id:"ticket",label:"Ticket price",min:10,max:80,step:1,base:46,fmt:"usd0",tag:"source",
   src:"Empire State 86th floor from $46 (esbnyc.com); Edge $42 + $2; Summit $43–49. Used for every city."},
  {g:"Ticket commission",id:"commRate",label:"Commission rate",min:0,max:.12,step:.005,base:.08,low:.04,fmt:"pct1",tag:"source",
   src:"Viator direct 8%; Viator through Awin 4%; GetYourGuide about 8% through Travelpayouts."},

  // Running costs
  {g:"Store fees and running costs",id:"feeSmall",label:"App Store fee under $1M a year",min:0,max:.3,step:.01,base:.15,fmt:"pct",tag:"source",
   src:"Apple Small Business Program: 15% while a calendar year's proceeds stay at or under $1M."},
  {g:"Store fees and running costs",id:"feeStd",label:"App Store fee over $1M a year",min:0,max:.3,step:.01,base:.30,fmt:"pct",tag:"source",
   src:"Apple's standard 30%. It applies for the rest of the year in which proceeds pass $1M, and all of the next year."},
  {g:"Store fees and running costs",id:"tools",label:"AI tool subscriptions per month",min:0,max:2000,step:50,base:600,fmt:"usd0",tag:"owner",
   src:"Your figure: Claude Max $200 + Claude API credits $200 + Codex Pro $200. No receipt in the repo."},
  {g:"Store fees and running costs",id:"toolShare",label:"Share of AI tools charged to Ondoway",min:0,max:1,step:.05,base:1,fmt:"pct",tag:"choice",
   src:"The tools are shared with other projects. 100% is the cautious case."},
  {g:"Store fees and running costs",id:"infra",label:"Servers and services per month",min:0,max:3000,step:1,base:83,fmt:"usd0",tag:"source",
   src:"List prices, not checked against bills. From render.yaml: web service on the free plan $0, Valhalla routing on starter $7, its 10 GB disk $2.50. Neo4j Aura Professional about $65 (the tier is not recorded in the repo). Apple developer $99 a year, about $8.25 a month. Cloudflare R2 and Resend on free tiers."},
  {g:"Store fees and running costs",id:"perCity",label:"Extra server cost per live city per month",min:0,max:500,step:5,base:0,low:10,fmt:"usd0",tag:"guess",
   src:"Bigger routing maps and database per city. Not measured."},
  {g:"Store fees and running costs",id:"audioMBPerStop",label:"Audio stored per stop (MB)",min:0,max:10,step:.1,base:2.1,fmt:"x",tag:"measured",
   src:"Stored MP3s run 0.96 MB a minute (audio_store/). The measured tour's stops averaged 132 seconds of voice, about 2.1 MB a stop. Assumes all audio is kept."},
  {g:"Store fees and running costs",id:"storagePrice",label:"Storage price per GB per month",min:0,max:.1,step:.001,base:.015,fmt:"usd3",tag:"source",
   src:"Cloudflare R2 list price. Its 10 GB free tier is not subtracted."},
  {g:"Store fees and running costs",id:"hires",label:"Paid hires",min:0,max:10,step:1,base:0,fmt:"int",tag:"choice",
   src:"Founders are unpaid. No hiring plan yet."},
  {g:"Store fees and running costs",id:"hireCost",label:"Cost per hire per month",min:0,max:25000,step:500,base:10000,fmt:"usd0",tag:"guess",showIf:p=>p.hires>0,
   src:"Fully loaded. No data."},
  {g:"Store fees and running costs",id:"hireStart",label:"Hires start in month",min:1,max:36,step:1,base:7,fmt:"int",tag:"choice",showIf:p=>p.hires>0,
   src:"Month 1 is December 2026."},
  {g:"Store fees and running costs",id:"otherFixed",label:"Other running costs per month",min:0,max:60000,step:100,base:0,fmt:"usd0",tag:"guess",
   src:"Nothing known yet."},
];

// Rules built into the math rather than set by a slider.
const FIXED_RULES = [
  {tag:"choice",text:"The model runs 36 months, from launch in December 2026."},
  {tag:"guess",text:"A new city grows in a straight line to full reach over its ramp months."},
  {tag:"choice",text:"The free first walk is shared: each city pays for it once, when it opens."},
  {tag:"source",text:"The App Store fee switches from 15% to 30% once a calendar year's proceeds pass $1M (Apple Small Business Program)."},
];

const NOT_COUNTED = [
  "Voicing the longer telling a listener taps for at a stop written fresh. At a stop told from a recipe it is already recorded.",
  "The cost of building each new city.",
  "Sales tax, EU VAT and refunds.",
  "Founders' time. Founders are unpaid.",
];

const MONTHS = 36, START_YEAR = 2026, START_MONTH = 11; // month index 11 = December
const byId = Object.fromEntries(INPUTS.map(i => [i.id, i]));

function preset(name) {
  const p = {};
  for (const i of INPUTS) {
    p[i.id] = i.tag === "choice" ? i.base
      : name === "low" && i.low != null ? i.low
      : name === "high" && i.high != null ? i.high
      : i.base;
  }
  return p;
}

/* ---------- Per-buyer pieces ---------- */
function stopsPerTour(p) { return p.stopsPerDay + p.replanStops; }
// The average stop: some told from a stored recipe, the rest written fresh.
function stopCost(p) { return p.recipeShare * p.recipeStop + (1 - p.recipeShare) * p.freshStop; }
function tourCost(p) { return stopsPerTour(p) * stopCost(p); }
function toursPerBuyer(p) { return p.model === "tour" ? p.toursBought : p.daysPerPass; }
function freeWalkCost(p) { return p.freeStops * stopCost(p); }
// Building every recipe a city needs, paid once when it opens.
function cityRecipeCost(p) { return p.recipesPerCity * p.recipeCost; }
function averagePrice(p) {
  if (p.model === "person") return p.pricePerson * p.partySize;
  if (p.model === "tour") return p.pricePerTour * p.toursBought;
  return (1 - p.coupleShare - p.groupShare) * p.priceSolo + p.coupleShare * p.priceCouple
    + p.groupShare * p.priceGroup;
}
function commissionPerBuyer(p) {
  return p.bookShare * p.ticketsPerBooking * p.ticket * p.commRate;
}
// Share of each sale kept after the store or card fee (percent part only), at App Store rate r.
function keptShare(p, r) { return 1 - (1 - p.webShare) * r - p.webShare * p.webPct; }

// Money one buyer brings in or costs, before marketing, at App Store rate r (default 15%).
function perPass(p, r = p.feeSmall) {
  const price = averagePrice(p);
  const fee = price * (1 - keptShare(p, r)) + p.webShare * p.webFixed;
  const tours = toursPerBuyer(p) * tourCost(p);
  const comm = commissionPerBuyer(p);
  return {price, fee, tours, comm, total: price - fee - tours + comm};
}

// The lowest average price at which one buyer stops losing money, before marketing.
function priceFloor(p, r = p.feeSmall) {
  const u = perPass(p, r);
  return (p.webShare * p.webFixed + u.tours - u.comm) / keptShare(p, r);
}

// A copy of p with every price scaled so the average price paid equals x.
function withAveragePrice(p, x) {
  if (p.model === "person") return {...p, pricePerson: x / p.partySize};
  if (p.model === "tour") return {...p, pricePerTour: x / p.toursBought};
  const avg = averagePrice(p) || 1;
  return {...p, priceSolo: p.priceSolo * x / avg, priceCouple: p.priceCouple * x / avg,
    priceGroup: p.priceGroup * x / avg};
}

/* ---------- Month by month ---------- */
function cityStart(k, p) { return k === 0 ? 0 : p.nycOnly + (k - 1) * p.cadence; }
function citiesLiveBy(p, month) {
  let n = 0;
  for (let k = 0; k < p.maxCities; k++) if (cityStart(k, p) <= month) n++;
  return n;
}

function simulate(p) {
  const price = averagePrice(p), tc = tourCost(p), tpb = toursPerBuyer(p);
  const walk = freeWalkCost(p), comm = commissionPerBuyer(p);
  let overThis = false, overPrev = false, yearProceeds = 0, storedGB = 0, cum = 0, liveBefore = 0;
  const rows = [];
  for (let m = 0; m < MONTHS; m++) {
    const d = new Date(START_YEAR, START_MONTH + m, 1);
    if (d.getMonth() === 0 && m > 0) { overPrev = overThis; overThis = false; yearProceeds = 0; }
    let parties = 0, nycParties = 0, live = 0;
    for (let k = 0; k < p.maxCities; k++) {
      const s = cityStart(k, p);
      if (m < s) continue;
      live++;
      const reach = Math.min(1, (m - s + 1) / p.ramp);
      const mp = (k === 0 ? p.nycVisits : p.cityVisits) / p.partySize / 12 * reach;
      parties += mp;
      if (k === 0) nycParties = mp;
    }
    const newCities = live - liveBefore; liveBefore = live;
    const installs = parties * p.categoryUse * p.ourShare + p.adBudget / p.cpi;
    const buyers = installs * p.conv;
    const gross = buyers * price;
    const rate = (overThis || overPrev) ? p.feeStd : p.feeSmall;
    const storeGross = gross * (1 - p.webShare);
    const storeFee = storeGross * rate;
    const webFee = gross * p.webShare * p.webPct + buyers * p.webShare * p.webFixed;
    yearProceeds += storeGross - storeFee;
    if (yearProceeds > 1e6) overThis = true;
    const tourAI = buyers * tpb * tc;
    const freeWalks = newCities;
    const freeAI = freeWalks * walk;
    const recipes = newCities * cityRecipeCost(p);
    storedGB += (buyers * tpb * stopsPerTour(p) + freeWalks * p.freeStops) * p.audioMBPerStop / 1024;
    const storage = storedGB * p.storagePrice;
    const commission = buyers * comm;
    const marketing = p.adBudget + p.otherMkt;
    const running = p.tools * p.toolShare + p.infra + p.perCity * live
      + (m + 1 >= p.hireStart ? p.hires * p.hireCost : 0) + p.otherFixed;
    const revenue = gross + commission;
    const cost = storeFee + webFee + tourAI + freeAI + recipes + storage + marketing + running;
    const profit = revenue - cost;
    cum += profit;
    rows.push({m, d, live, parties, nycParties, installs, buyers, gross, commission, revenue,
      fees: storeFee + webFee, tourAI, freeAI, recipes, storage, marketing, running, cost, profit, cum,
      rate});
  }
  return {rows, price, tc, tpb};
}

function years(sim) {
  return [0, 1, 2].map(i => {
    const r = sim.rows.slice(i * 12, i * 12 + 12);
    const s = k => r.reduce((a, x) => a + x[k], 0);
    return {live: r[11].live, parties: s("parties"), installs: s("installs"), buyers: s("buyers"),
      revenue: s("revenue"), fees: s("fees"), tourAI: s("tourAI"), freeAI: s("freeAI"),
      recipes: s("recipes"),
      storage: s("storage"), marketing: s("marketing"), running: s("running"),
      cost: s("cost"), profit: s("profit")};
  });
}

// The lowest share of installs that must pay for year 3 to break even at average price x.
// null when no share up to 50% gets there. Profit is not always rising with the share: past
// $1M a year the App Store fee doubles, so this scans upward first and then narrows.
function payShareNeeded(p, x) {
  const q = withAveragePrice(p, x);
  const y3 = c => years(simulate({...q, conv: c}))[2].profit;
  const steps = 48, first = .0005, last = .5, k = Math.pow(last / first, 1 / steps);
  let lo = 0, hi = null;
  for (let c = first, i = 0; i <= steps; i++, c *= k) {
    if (y3(c) >= 0) { hi = c; break; }
    lo = c;
  }
  if (hi === null || lo === 0) return hi;
  for (let i = 0; i < 20; i++) { const mid = (lo + hi) / 2; if (y3(mid) < 0) lo = mid; else hi = mid; }
  return hi;
}
