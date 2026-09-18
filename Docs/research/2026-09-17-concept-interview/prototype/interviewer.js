// PROTOTYPE — throwaway. Interviewer view (?view=iv&s=<code>). Founder only; the traveller's
// view never loads this UI. Question text is from ../interview-guide.md.

const SCREEN_NAMES = {
  "0.1": "Meet Maya", "1.1": "Lens picker", "1.2": "Who's coming?", "2.1": "Plan your trip", "2.2": "Building",
  "2.3": "Your trip", "2.4": "Friday + Skip advice", "3.1": "Walk · 9/11 Memorial", "3.1b": "Walk · Trinity Church",
  "3.3": "Federal Hall · Maya + Dan", "4.1": "Re-plan heads-up", "4.2": "Walk-past · Bowling Green", "5.1": "In line?",
  "5.2": "Deep dive", "5.3": "Echoes (camera)", "5.4": "Leave an echo", "6.1": "I Spy", "7.1": "Photos permission",
  "7.2": "Day recap", "7.3": "Trip recap", "7.4": "End",
};
const stepOf = (id) => (id === "0.1" ? 0 : +id[0]);

const PARTS = [
  { k: "p0", name: "0 · Intro + consent", min: 2, qs: ["Thanks for doing this… no right answers… tell me when something's confusing or pointless. OK if I record this?", "Confirm: who they travelled with on their last city trip, and roughly when."] },
  { k: "p1", name: "1 · How they plan today", min: 10, h: "H1", qs: ["LEAD: Tell me about the last trip you planned to a city you didn't know. Start from the moment you decided to go.", "What did you do first? → And then?", "Where did the list of things to see come from?", "How did you decide what made the cut, and what didn't?", "Anything you wanted to do and dropped? Why?", "Who else weighed in? Who made the final call?", "How long did the planning take, all in?", "Was there a moment it felt like too much? (ONLY if already hinted)", "What did you have in hand when you got there?"], note: "Don't mention AI, audio, tours, or Ondoway." },
  { k: "p2", name: "2 · On the ground", min: 8, h: "H2 · H3", qs: ["LEAD: Think of the last famous place you visited on a trip. Walk me through it.", "What do you remember about it now?", "What did you do while you were there? Read, listen, look anything up?", "What did you do right after?", "Did you learn anything there that stuck?", "DINNER: That evening, what did you all talk about?", "What were the others doing while you were looking at things?", "LAST, only if nothing surfaced: Ever been somewhere famous and felt a bit underwhelmed? → code P"] },
  { k: "p3", name: "3 · Walkthrough", min: 30, qs: ["Transition: send the link, ask them to share their screen and think out loud. From here, do not click or narrate."] },
  { k: "close", name: "Close · off the phone", min: 7, h: "H11", qs: ["Open ?view=board and share YOUR screen.", "Rank: If it could only do one of these, which one? Then the next?", "For your top one — what would you use for that instead today?", "OPEN PRICE: For a trip like the one you told me about, what would this be worth to you? (no numbers — wait)", "Reveal the tiers on the board. Which would you pick?", "Who in your group would it need to cover for that to feel fair?", "Would the others in your group actually install it?", "What's the one thing you'd change?", "Who else should I talk to?"] },
];
const STEPS = {
  0: { name: "Opening screen", min: 0, qs: ["Say nothing. Let them read."] },
  1: { name: "Step 1 · Lenses", min: 3, qs: ["After they finish picking: What do you think those choices will change?", "Were any of these obviously not you?"] },
  2: { name: "Step 2 · Planning the day", min: 6, h: "H4", qs: ["On the Friday plan, wait. Let them read.", "What's your reaction to this?", "Where do you think these stops came from? (listen for 'ads' / 'someone paid')", "On the Skip advice card: How do you feel about it telling you to skip that?", "Would you have found these in-between stops yourself?", "COMFORT: Would you be comfortable letting something plan your day like this? What would you need to see to trust it?"] },
  3: { name: "Step 3 · The walk", min: 7, h: "H5 · H6", qs: ["Say nothing while the story plays. Watch: listen or skip?", "After the Federal Hall pair: What do you make of that — the two of them standing in the same spot?", "→ Would that be good or bad for your group? Why?", "Do NOT mention Ask or the levers. If never touched by end of step 4: Was there anything you wanted to know that it didn't tell you? → P"] },
  4: { name: "Step 4 · The day changes", min: 3, qs: ["After the re-plan: What just happened?", "Is that what you'd want it to do? What would you have done without it?"] },
  5: { name: "Step 5 · Deep dive + Echoes", min: 6, h: "H7 · H8", qs: ["After a minute of the Deep dive: Would you play this out loud, standing in the Statue of Liberty ferry line?", "After Echoes: Would you leave one?", "If these were only on a map, not in the camera, would you still bother?", "Would you want to write anything you like here? Would you want your kids seeing what strangers wrote?"] },
  6: { name: "Step 6 · I Spy", min: 2, h: "H9", qs: ["On your last trip, what did the kids do while you were looking at things? (skip if answered in part 2)", "Would this change that?"] },
  7: { name: "Step 7 · Day + Trip recap", min: 3, h: "H3 · H10", qs: ["Photo prompt: note what they chose and what they said before choosing.", "What would you do with this?", "Is this something you'd look at at dinner, or later, or never?", "Would you share it? Where?"] },
};
const HYP = [
  ["H1", "Planning overwhelm", "overwhelm / ranking pain raised U in part 1", "no planning pain unprompted"],
  ["H2", "Underwhelmed during", "disappointment raised U in part 2", "only a P yes"],
  ["H3", "Dinner conversation", "places come up U at dinner; recap valued", "food and feet; recap 'never'"],
  ["H4", "Skip advice trusted", "positive/curious; mentioned again at close", "nobody repeats a negative rec"],
  ["H5", "Different stories per person", "'we'd compare' / wants it for group", "'each on our phones' / lenses arbitrary"],
  ["H6", "Sourced Q&A", "Ask tapped U", "never tapped, no questions when prompted"],
  ["H7", "Deep dive out loud", "would play it aloud", "would not; earbuds only"],
  ["H8", "Echoes", "would leave one; camera matters", "wouldn't bother; map-only fine"],
  ["H9", "Kid is the veto", "kids' boredom U; I Spy would change it", "'kids wouldn't care'"],
  ["H10", "Recap + photos", "grants permission; would share", "refuses; 'never'"],
  ["H11", "Willingness to pay", "open price ≥ Solo/Pair; household fair", "near zero; won't cover party"],
];

const Interviewer = {
  s: null,
  boot() {
    document.title = "Ondoway Interviewer";
    const key = "proto-iv-" + (Sync.code || "local");
    this.key = key;
    this.s = Store.get(key, null) || {
      participant: "", mode: "remote", startedAt: null, part: null, partTimes: {}, log: [], prompts: [], defects: [], notes: "",
      tally: Object.fromEntries(HYP.map(([h]) => [h, { U: 0, P: 0, S: 0, F: 0 }])), cuts: {}, pos: null, ranking: null, offset: null,
    };
    Sync.on((m) => this.onMsg(m));
    Sync.start("interviewer");
    Sync.send({ type: "hello" });
    this.render();
    setInterval(() => this.tick(), 1000);
    Sync.onStatus(() => this.paintStatus());
  },
  save() { Store.set(this.key, this.s); },
  now() { return Date.now(); },

  onMsg(m) {
    const s = this.s;
    if (m.type === "log") { s.offset = Date.now() - m.entry.t; this.add(m.entry, Date.now()); }
    if (m.type === "full") (m.log || []).forEach((e) => this.add(e, e.t + (s.offset ?? 0)));
    if (m.type === "pos") {
      s.pos = m;
      if (!s.part || s.part === "p0" || s.part === "p1" || s.part === "p2") this.setPart("p3");
    }
    if (m.type === "ranking") s.ranking = m;
    if (m.type === "done") this.flash("Traveller reached the end.");
    if (m.type === "peer-open") Sync.send({ type: "cuts", cuts: s.cuts });
    this.save();
    // Don't yank focus from the founder mid-typing; the next message repaints.
    if (!document.activeElement || !document.activeElement.matches("input, textarea")) this.render();
  },
  add(e, rx) {
    const s = this.s;
    if (s.log.some((x) => x.t === e.t && x.target === e.target && x.kind === e.kind)) return;
    const step = stepOf(e.screen);
    const prompted = s.prompts.some((p) => p.step === step && p.at <= rx);
    s.log.push({ ...e, rx, code: e.kind === "tap" || e.kind === "key" || e.kind === "ask-typed" || e.kind === "echo-left" ? (prompted ? "P" : "U") : "" });
    s.log.sort((a, b) => a.t - b.t);
  },
  setPart(k) {
    const s = this.s;
    if (!s.startedAt) s.startedAt = Date.now();
    if (s.part) (s.partTimes[s.part] ||= { ms: 0 }).ms += Date.now() - (s.partTimes[s.part].since || Date.now());
    s.part = k; (s.partTimes[k] ||= { ms: 0 }).since = Date.now();
    this.save(); this.render();
  },
  partMs(k) { const p = this.s.partTimes[k]; if (!p) return 0; return p.ms + (this.s.part === k && p.since ? Date.now() - p.since : 0); },
  stepMs() { // time on each walkthrough step, from the traveller's own screen log
    const out = {}; const sc = this.s.log.filter((e) => e.kind === "screen");
    sc.forEach((e, i) => { const end = sc[i + 1] ? sc[i + 1].t : this.s.pos ? Date.now() - (this.s.offset ?? 0) : e.t; out[stepOf(e.screen)] = (out[stepOf(e.screen)] || 0) + (end - e.t); });
    return out;
  },
  tick() {
    const clk = document.getElementById("iv-clock"); if (!clk) return;
    const s = this.s;
    clk.textContent = s.startedAt ? fmtSecs((Date.now() - s.startedAt) / 1000) : "not started";
    PARTS.forEach((p) => { const el = document.getElementById("pt-" + p.k); if (el) el.textContent = fmtSecs(this.partMs(p.k) / 1000) + " / " + p.min + ":00"; });
    const sm = this.stepMs();
    Object.keys(STEPS).forEach((k) => { const el = document.getElementById("st-" + k); if (el) el.textContent = fmtSecs((sm[k] || 0) / 1000) + (STEPS[k].min ? " / " + STEPS[k].min + ":00" : ""); });
    const cutHint = document.getElementById("cut-hint");
    if (cutHint) {
      const over = this.partMs("p3") / 60000 - 30 * (this.progress3());
      cutHint.textContent = over > 2 ? `Walkthrough running ~${Math.round(over)} min behind plan. Next cut: ${!s.cuts.echo ? "Leave an echo" : !s.cuts.trip ? "Trip recap" : !s.cuts.ispy1 ? "I Spy → first find only" : "nothing left to cut"}.` : "On time.";
    }
  },
  progress3() { // share of the 30-minute walkthrough the traveller has reached, by step budget
    const step = this.s.pos ? stepOf(this.s.pos.screen) : 0; let done = 0;
    for (let k = 1; k < step; k++) done += STEPS[k].min; return done / 30;
  },
  flash(t) { const f = document.getElementById("iv-flash"); if (f) { f.textContent = t; f.hidden = false; setTimeout(() => (f.hidden = true), 4000); } },
  paintStatus() {
    const el = document.getElementById("iv-conn"); if (!el) return;
    const st = Sync.status;
    el.textContent = !Sync.code ? "No session code — same-browser only" : st.peer === "connected" ? "Traveller connected" : st.peer === "waiting" ? "Waiting for traveller…" : "Link: " + st.peer;
    el.className = "conn " + (st.peer === "connected" ? "ok" : "");
  },

  render() {
    const s = this.s, pos = s.pos, step = pos ? stepOf(pos.screen) : null;
    const partQ = s.part && s.part !== "p3" ? PARTS.find((p) => p.k === s.part) : null;
    const q = partQ || (step != null ? STEPS[step] : PARTS[0]);
    const base = location.href.split("?")[0];
    const tLink = Sync.code ? `${base}?s=${Sync.code}` : `${base}`;
    const taps = s.log.filter((e) => e.code);
    document.getElementById("root").innerHTML = `<div class="iv">
      <header class="iv-head">
        <div><div class="eyebrow">Ondoway · interviewer view — never share this screen</div><h1 class="h2">Concept interview</h1></div>
        <div class="iv-meta">
          <input id="iv-part" placeholder="Participant ID" value="${esc(s.participant)}">
          <button class="seg${s.mode === "remote" ? " on" : ""}" data-mode="remote">Remote</button><button class="seg${s.mode === "in-person" ? " on" : ""}" data-mode="in-person">In person</button>
          <span class="iv-clock mono" id="iv-clock"></span>
        </div>
      </header>
      <div class="iv-link"><span id="iv-conn" class="conn"></span> Traveller link: <code>${esc(tLink)}</code> <button id="copy-link">Copy</button> · Board: <code>${esc(base)}?view=board${Sync.code ? "&s=" + Sync.code : ""}</code></div>
      <div id="iv-flash" class="iv-flash" hidden></div>
      <div class="iv-grid">
        <aside class="iv-col">
          <div class="eyebrow">Session parts</div>
          ${PARTS.map((p) => `<button class="part${s.part === p.k ? " on" : ""}" data-part="${p.k}"><span>${esc(p.name)}</span><span class="mono" id="pt-${p.k}"></span></button>`).join("")}
          <div class="eyebrow" style="margin-top:14px">Walkthrough steps</div>
          ${Object.entries(STEPS).filter(([k]) => +k > 0).map(([k, st]) => `<div class="steprow${step === +k ? " on" : ""}"><span>${esc(st.name)}</span><span class="mono" id="st-${k}"></span></div>`).join("")}
          <div class="eyebrow" style="margin-top:14px">Cuts (in order)</div>
          <p id="cut-hint" class="small"></p>
          ${[["echo", "Cut 5.4 Leave an echo"], ["trip", "Cut 7.3 Trip recap"], ["ispy1", "I Spy → first find only"]].map(([k, l]) => `<label class="cut"><input type="checkbox" data-cut="${k}" ${s.cuts[k] ? "checked" : ""}> ${l}</label>`).join("")}
          <p class="small">Never cut: parts 1–2, Skip advice, the Federal Hall moment, the Day recap, the close.</p>
        </aside>
        <main class="iv-col wide">
          <div class="now">
            <div class="eyebrow">Traveller is on</div>
            <div class="now-scr">${pos ? `<b>${esc(pos.screen)}</b> ${esc(SCREEN_NAMES[pos.screen] || "")}${pos.paused ? ' <span class="pill" style="background:#FFF2C7">paused</span>' : ""}` : "— not connected yet —"}</div>
          </div>
          <div class="qs">
            <div class="eyebrow">${esc(q.name)}${q.h ? " · tests " + esc(q.h) : ""}</div>
            <ol>${q.qs.map((x) => `<li>${esc(x)}</li>`).join("")}</ol>
            ${q.note ? `<p class="small"><b>Don't:</b> ${esc(q.note)}</p>` : ""}
          </div>
          <div class="iv-actions">
            <button id="mark-p" class="btn">I just asked — code taps P${step != null ? ` (step ${step})` : ""}</button>
            <button id="mark-d" class="btn quiet">I had to explain this screen</button>
          </div>
          ${step === 2 ? `<div class="small drafts"><b>Skip advice drafts (✎ founder picks):</b><br>${C.skipAdvice.drafts.map((d) => "• " + esc(d)).join("<br>")}</div>` : ""}
          <div class="eyebrow" style="margin-top:16px">Tap log · ${taps.filter((e) => e.code === "U").length} U · ${taps.filter((e) => e.code === "P").length} P</div>
          <div class="feed">${s.log.slice(-60).reverse().map((e) => `<div class="fe ${e.kind}"><span class="mono">${fmtSecs(e.at / 1000)}</span><span class="mono">${esc(e.screen)}</span><span class="code ${e.code}">${e.code}</span><span>${esc(e.kind === "screen" ? "→ " + (SCREEN_NAMES[e.target] || e.target) : e.target)}${e.detail && Object.keys(e.detail).length ? ` <span class="muted">${esc(JSON.stringify(e.detail))}</span>` : ""}</span></div>`).join("")}</div>
        </main>
        <aside class="iv-col">
          <div class="eyebrow">Hypotheses — what they SAID</div>
          <table class="tally"><tr><th></th><th>U</th><th>P</th><th title="supports">✓</th><th title="hit falsifier">✗</th></tr>
          ${HYP.map(([h, n, sup, fal]) => `<tr title="Supports if: ${esc(sup)} · Falsifier: ${esc(fal)}"><td><b>${h}</b> ${esc(n)}</td>${["U", "P", "S", "F"].map((c) => `<td><button class="tc" data-h="${h}" data-c="${c}">${s.tally[h][c]}</button></td>`).join("")}</tr>`).join("")}</table>
          <p class="small">Click +1, shift-click −1. Hover a row for its falsifier.</p>
          ${s.ranking ? `<div class="eyebrow" style="margin-top:12px">Ranking (from board)</div><ol class="small">${s.ranking.order.map((x) => `<li>${esc(x)}</li>`).join("")}</ol>` : ""}
          <div class="eyebrow" style="margin-top:12px">Defects (had to explain)</div>
          <ul class="small">${s.defects.map((d) => `<li>${esc(d.screen)} ${esc(SCREEN_NAMES[d.screen] || "")}</li>`).join("") || "<li>none</li>"}</ul>
          <textarea id="iv-notes" placeholder="Notes">${esc(s.notes)}</textarea>
          <div class="iv-actions"><button id="exp-json" class="btn quiet">Export JSON</button><button id="exp-csv" class="btn quiet">Export CSV</button><button id="copy-json" class="btn quiet">Copy JSON</button></div>
          <button id="iv-reset" class="linkish">Start a new session (clears this view)</button>
        </aside>
      </div></div>`;
    this.wire(); this.tick(); this.paintStatus();
  },
  wire() {
    const s = this.s, root = document.getElementById("root");
    root.querySelectorAll("[data-part]").forEach((b) => (b.onclick = () => this.setPart(b.dataset.part)));
    root.querySelectorAll("[data-mode]").forEach((b) => (b.onclick = () => { s.mode = b.dataset.mode; this.save(); this.render(); }));
    document.getElementById("iv-part").onchange = (e) => { s.participant = e.target.value; this.save(); };
    document.getElementById("iv-notes").oninput = (e) => { s.notes = e.target.value; this.save(); };
    root.querySelectorAll("[data-cut]").forEach((c) => (c.onchange = () => { s.cuts[c.dataset.cut] = c.checked; Sync.send({ type: "cuts", cuts: s.cuts }); this.save(); }));
    root.querySelectorAll(".tc").forEach((b) => (b.onclick = (e) => { const t = s.tally[b.dataset.h]; t[b.dataset.c] = Math.max(0, t[b.dataset.c] + (e.shiftKey ? -1 : 1)); this.save(); this.render(); }));
    document.getElementById("mark-p").onclick = () => {
      const step = s.pos ? stepOf(s.pos.screen) : null; if (step == null) return this.flash("No traveller position yet.");
      s.prompts.push({ step, at: Date.now() }); this.save(); this.flash(`From now on, taps in step ${step} are coded P.`);
    };
    document.getElementById("mark-d").onclick = () => { if (!s.pos) return; s.defects.push({ screen: s.pos.screen, at: Date.now() }); this.save(); this.render(); };
    document.getElementById("copy-link").onclick = () => navigator.clipboard?.writeText(location.href.split("?")[0] + (Sync.code ? "?s=" + Sync.code : ""));
    document.getElementById("exp-json").onclick = () => this.download("json");
    document.getElementById("exp-csv").onclick = () => this.download("csv");
    document.getElementById("copy-json").onclick = () => { navigator.clipboard?.writeText(JSON.stringify(this.exportObj(), null, 1)); this.flash("Copied."); };
    document.getElementById("iv-reset").onclick = () => { if (confirm("Clear this session's interviewer data?")) { Store.set(this.key, null); location.reload(); } };
  },
  exportObj() {
    const s = this.s;
    return { participant: s.participant, mode: s.mode, code: Sync.code, exportedAt: new Date().toISOString(),
      partMinutes: Object.fromEntries(PARTS.map((p) => [p.k, +(this.partMs(p.k) / 60000).toFixed(1)])),
      stepMinutes: Object.fromEntries(Object.entries(this.stepMs()).map(([k, v]) => [k, +(v / 60000).toFixed(1)])),
      tally: s.tally, defects: s.defects, cuts: s.cuts, ranking: s.ranking, prompts: s.prompts, notes: s.notes, log: s.log };
  },
  download(kind) {
    const o = this.exportObj(), name = `concept-${o.participant || "session"}-${o.exportedAt.slice(0, 16).replace(/[:T]/g, "")}`;
    const text = kind === "json" ? JSON.stringify(o, null, 1)
      : ["at_s,screen,kind,target,code,detail", ...o.log.map((e) => [Math.round(e.at / 1000), e.screen, e.kind, e.target, e.code, JSON.stringify(e.detail || {})].map((v) => `"${String(v).replace(/"/g, '""')}"`).join(","))].join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: kind === "json" ? "application/json" : "text/csv" }));
    a.download = `${name}.${kind}`; a.click();
  },
};
