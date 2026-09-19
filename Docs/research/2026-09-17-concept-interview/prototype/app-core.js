// PROTOTYPE — throwaway. Core of the traveller view: screen sequence, phone frame, caption
// bar, tap log, and the story player. Screens themselves live in screens-*.js.

const C = window.CONTENT;
const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => String(s).replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));
const lensOf = (id) => { const l = C.lenses.find((x) => x[0] === id); return l ? { id: l[0], label: l[1], color: l[2], icon: l[3] } : null; };
const icon = (name, cls = "") => `<span class="ms ${cls}" aria-hidden="true">${name}</span>`;
function chipHtml(id, { on = false, sm = false, attrs = "" } = {}) {
  const l = lensOf(id);
  return `<button class="lchip${on ? " on" : ""}${sm ? " sm static" : ""}" style="--c:${l.color}" ${attrs}>${icon(l.icon)}${esc(l.label)}${on && !sm ? icon("check") : ""}</button>`;
}
// A photo slot from CONTENT.img, or a labelled placeholder until the photos are approved.
// `fill` makes it cover its box (the box sets the size).
function pic(slot, label, { cls = "", style = "" } = {}) {
  const src = C.img[slot];
  return src ? `<img class="pic ${cls}" src="${esc(src)}" alt="${esc(label)}" style="${style}" draggable="false">`
    : `<div class="pic placeholder-img ${cls}" style="${style}"><span>${esc(label)}</span></div>`;
}
const fmtSecs = (s) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;

// ---------------------------------------------------------------- Story player
// One story at a time. Real audio when `story.audio` is set; otherwise browser speech,
// spoken sentence by sentence (Chrome drops long utterances, and sentence steps give the
// transcript its sync). With no speech engine it runs silently on a timer, so the walk
// still advances.
const Player = (function () {
  let cur = null, gen = 0, timer = null, audioEl = null, voice = null;
  const synth = window.speechSynthesis || null;
  function pickVoice() {
    if (!synth) return null;
    const vs = synth.getVoices().filter((v) => /^en[-_]US/i.test(v.lang));
    return vs.find((v) => /Samantha|Google US English|Ava|Allison|Aaron/i.test(v.name)) || vs[0] || null;
  }
  if (synth) { voice = pickVoice(); synth.onvoiceschanged = () => { voice = pickVoice(); }; }
  const split = (t) => t.match(/[^.!?]+[.!?]+["”'’)]*\s*|[^.!?]+$/g).map((s) => s.trim()).filter(Boolean);

  function progress() {
    if (!cur) return 0;
    if (cur.mode === "audio" && audioEl && audioEl.duration) return audioEl.currentTime / audioEl.duration;
    const within = cur.playing ? Math.min(cur.sent[cur.idx]?.length || 0, ((Date.now() - cur.sentStart) / 1000) * cur.cps) : cur.within || 0;
    return Math.min(1, (cur.before[cur.idx] + within) / cur.total);
  }
  function sentenceIdx() {
    if (!cur) return -1;
    if (cur.mode !== "audio") return cur.idx;
    const p = progress() * cur.total;
    let i = 0; while (i < cur.before.length - 1 && cur.before[i + 1] <= p) i++; return i;
  }
  function tick() { if (cur && cur.onTick) cur.onTick(progress(), sentenceIdx()); }

  function speakFrom(i) {
    const my = gen;
    cur.idx = i; cur.sentStart = Date.now(); cur.within = 0;
    if (i >= cur.sent.length) return finish();
    if (cur.mode === "silent") {
      cur.silentTimer = setTimeout(() => { if (my === gen && cur.playing) speakFrom(i + 1); }, (cur.sent[i].length / cur.cps) * 1000);
      return;
    }
    const u = new SpeechSynthesisUtterance(cur.sent[i]);
    if (voice) u.voice = voice;
    u.rate = 1.0;
    u.onend = () => { if (my === gen && cur && cur.playing) speakFrom(i + 1); };
    u.onerror = () => { if (my === gen && cur && cur.playing) speakFrom(i + 1); };
    synth.speak(u);
  }
  function finish() {
    const done = cur && cur.onEnd;
    if (cur && cur.onTick) cur.onTick(1, cur.sent.length - 1);
    stop();
    if (done) done();
  }
  function stop() {
    gen++;
    if (synth) synth.cancel();
    if (audioEl) { audioEl.pause(); audioEl = null; }
    if (cur && cur.silentTimer) clearTimeout(cur.silentTimer);
    clearInterval(timer); timer = null;
    cur = null;
  }
  function play(story, { onEnd, onTick } = {}) {
    stop();
    const sent = split(story.text);
    const before = []; let acc = 0; sent.forEach((s) => { before.push(acc); acc += s.length; });
    const mode = story.audio ? "audio" : synth && synth.getVoices().length ? "speech" : "silent";
    cur = { story, sent, before, total: acc, cps: acc / (story.secs || acc / 15), mode, idx: 0, playing: true, onEnd, onTick };
    if (mode === "audio") {
      audioEl = new Audio(story.audio);
      audioEl.onended = () => finish();
      audioEl.onerror = () => { cur.mode = synth ? "speech" : "silent"; audioEl = null; speakFrom(0); };
      audioEl.play().catch(() => {});
    } else speakFrom(0);
    timer = setInterval(tick, 150);
    App.onPlayState();
  }
  function pause() {
    if (!cur || !cur.playing) return;
    cur.within = Math.min(cur.sent[cur.idx]?.length || 0, ((Date.now() - cur.sentStart) / 1000) * cur.cps);
    cur.playing = false; gen++;
    if (cur.mode === "audio" && audioEl) audioEl.pause();
    else { if (synth) synth.cancel(); clearTimeout(cur.silentTimer); }
    App.onPlayState();
  }
  function resume() {
    if (!cur || cur.playing) return;
    cur.playing = true;
    if (cur.mode === "audio" && audioEl) audioEl.play().catch(() => {});
    else speakFrom(cur.idx); // restarts the current sentence
    App.onPlayState();
  }
  return {
    play, pause, resume, stop, progress, sentenceIdx, split,
    get story() { return cur && cur.story; },
    get playing() { return !!(cur && cur.playing); },
    get active() { return !!cur; },
    toggle() { if (!cur) return; cur.playing ? pause() : resume(); },
  };
})();

// ---------------------------------------------------------------- App shell
const ORDER = ["0.1", "1.1", "1.2", "2.1", "2.2", "2.3", "2.4", "3.1", "3.1b", "3.3", "4.1", "4.2", "5.1", "5.2", "5.3", "5.4", "6.1", "7.1", "7.2", "7.3", "7.4"];

const App = {
  screens: {},
  def(id, spec) { this.screens[id] = spec; },
  st: {
    idx: 0,
    lenses: new Set(C.party[0].lenses),
    lastLens: C.party[0].lenses[0],
    paused: false,
    lineLength: null,
    photos: null,
    echoLeft: null,
    asked: [],
    ispyFound: {},
    cuts: {},
    replanChoice: null,
    addedMustSees: [],   // [{name, day, why}] from the 2.1 add sheet
    echoesLeft: {},      // stop key → echo text
  },
  log: [],
  t0: Date.now(),
  get id() { return ORDER[this.st.idx]; },

  boot() {
    const key = "proto-log-" + (Sync.code || "local");
    if (new URLSearchParams(location.search).get("reset") !== "1") {
      const saved = Store.get(key, null);
      if (saved && saved.log) { this.log = saved.log; this.t0 = saved.t0; }
    }
    this.storeKey = key;
    Sync.on((m) => this.onSync(m));
    Sync.start("traveller");
    document.addEventListener("keydown", (e) => this.onKey(e));
    document.addEventListener("click", (e) => this.onClick(e), true);
    window.addEventListener("resize", () => this.fit());
    this.record("system", "session", { event: "open", ua: navigator.userAgent.slice(0, 80) });
    this.go(0, "boot");
  },

  // ---- logging -------------------------------------------------------------
  record(kind, target, detail = {}) {
    const e = { t: Date.now(), at: Date.now() - this.t0, screen: this.id, kind, target, detail };
    this.log.push(e);
    Store.set(this.storeKey, { t0: this.t0, log: this.log });
    Sync.send({ type: "log", entry: e });
    return e;
  },
  sendPos() { Sync.send({ type: "pos", screen: this.id, idx: this.st.idx, paused: this.st.paused, at: Date.now() - this.t0 }); },
  onSync(m) {
    if (m.type === "hello" || m.type === "peer-open") { Sync.send({ type: "full", log: this.log, t0: this.t0 }); this.sendPos(); }
    if (m.type === "cuts") this.st.cuts = m.cuts || {};
  },
  // Every tap is logged. Elements may carry data-log="name" and data-detail='{"k":1}'.
  onClick(e) {
    const el = e.target.closest("button, [data-log]");
    if (!el || !el.closest(".stage")) return;
    let detail = {};
    try { detail = el.dataset.detail ? JSON.parse(el.dataset.detail) : {}; } catch (x) {}
    const name = el.dataset.log || (el.textContent || "").trim().replace(/\s+/g, " ").slice(0, 60) || el.className;
    this.record("tap", name, detail);
  },
  onKey(e) {
    if (e.target.matches("input, textarea")) return;
    if (e.key === "ArrowRight") { this.record("key", "Next (→)"); this.next(); }
    if (e.key === "ArrowLeft") { this.record("key", "Back (←)"); this.back(); }
    if (e.key === " " && this.screens[this.id].walk) { e.preventDefault(); this.record("key", "Pause (space)"); this.togglePause(); }
    if (e.key === "L" && e.shiftKey && (e.ctrlKey || e.metaKey)) { // hidden: founder's fallback export
      e.preventDefault();
      navigator.clipboard?.writeText(JSON.stringify({ t0: this.t0, log: this.log }, null, 1));
    }
  },

  // ---- navigation ------------------------------------------------------------
  skipped(id) {
    const c = this.st.cuts;
    return (c.echo && id === "5.4") || (c.trip && id === "7.3");
  },
  next(from) {
    if (from && from !== this.id) return; // stale auto-advance from a screen already left
    let i = this.st.idx + 1;
    while (i < ORDER.length && this.skipped(ORDER[i])) i++;
    if (i < ORDER.length) this.go(i, "next");
  },
  back() {
    let i = this.st.idx - 1;
    while (i > 0 && this.skipped(ORDER[i])) i--;
    if (i >= 0) this.go(i, "back");
  },
  go(i, how) {
    const prev = this.screens[this.id];
    if (prev && prev.leave && how !== "boot") prev.leave();
    const keepAudio = this.screens[ORDER[i]].keepAudio;
    if (!keepAudio) Player.stop();
    this.st.idx = i;
    this.st.paused = false;
    this.record("screen", this.id, { how });
    this.sendPos();
    this.render();
  },

  // ---- rendering -------------------------------------------------------------
  // spec.layout: "one" (Maya), "split" (Maya + Dan), "kid" (Maya + Ava, smaller)
  render() {
    const id = this.id, spec = this.screens[id];
    const root = $("#root");
    const phones = spec.layout === "split" ? [["maya", "Maya · you"], ["dan", "Dan"]] : spec.layout === "kid" ? [["maya", "Maya · you"], ["ava", "Ava · 8"]] : [["maya", ""]];
    root.innerHTML = `<div class="stage">
      <div class="phones">${phones.map(([p, label], n) => `
        <div class="phone-wrap${spec.layout === "kid" && p === "ava" ? " kid enter" : ""}${spec.layout === "split" && p === "dan" ? " enter" : ""}" data-phone="${p}">
          ${label ? `<div class="phone-label">${esc(label)}</div>` : ""}
          <div class="phone"><div class="screen ${spec.theme || ""}" id="screen-${p}">
            <div class="statusbar"><span class="clock">${esc(typeof spec.time === "function" ? spec.time() : spec.time || "8:12")}</span><span class="isl"></span><span class="icons">${icon("signal_cellular_alt")}${icon("wifi")}${icon("battery_full")}</span></div>
            <div class="homebar"></div>
          </div></div>
        </div>`).join("")}
      </div>
      <div class="capbar">
        <div class="cap" id="cap">${esc(C.captions[id] || "")}</div>
        <div class="ctl">
          <button class="back" data-log="Back" ${this.st.idx === 0 ? "disabled" : ""}>${icon("arrow_back")}Back</button>
          ${spec.walk ? `<button class="pause" id="pausebtn" data-log="Pause (bar)">${icon(this.st.paused ? "play_arrow" : "pause")}${this.st.paused ? "Resume" : "Pause"}</button>` : ""}
          <button class="next" data-log="Next" ${this.st.idx === ORDER.length - 1 ? "disabled" : ""}>Next${icon("arrow_forward")}</button>
        </div>
      </div></div>`;
    $(".capbar .back").onclick = () => this.back();
    $(".capbar .next").onclick = () => this.next();
    const pb = $("#pausebtn"); if (pb) pb.onclick = () => this.togglePause();
    const screens = {}; phones.forEach(([p]) => (screens[p] = $("#screen-" + p)));
    spec.render(screens);
    this.fit();
    setTimeout(() => document.querySelectorAll(".phone-wrap.enter").forEach((w) => w.classList.remove("enter")), 80);
    if (spec.enter) spec.enter(screens);
  },
  // Scale the phone(s) to the laptop screen.
  fit() {
    const wraps = [...document.querySelectorAll(".phone-wrap")];
    if (!wraps.length) return;
    const box = $(".phones").getBoundingClientRect();
    const avH = window.innerHeight - $(".capbar").offsetHeight - 70;
    const units = wraps.reduce((a, w) => a + (w.classList.contains("kid") ? 0.8 : 1), 0);
    const s = Math.min(1, avH / 844, (box.width - 40 * (wraps.length - 1) - 8) / (390 * units));
    wraps.forEach((w) => {
      const k = s * (w.classList.contains("kid") ? 0.8 : 1);
      const ph = $(".phone", w);
      ph.style.transform = `scale(${k})`;
      w.style.width = 390 * k + "px"; w.style.height = 844 * k + "px";
    });
  },
  setClock(t, jump) {
    document.querySelectorAll(".clock").forEach((c) => { c.textContent = t; if (jump) { c.classList.remove("jump"); void c.offsetWidth; c.classList.add("jump"); } });
  },
  togglePause() {
    // A story paused on an earlier screen keeps playing state across screens (Deep dive),
    // so the player's own state decides when one is loaded.
    this.st.paused = Player.active ? Player.playing : !this.st.paused;
    if (this.st.paused) Player.pause(); else Player.resume();
    const spec = this.screens[this.id];
    if (spec.onPause) spec.onPause(this.st.paused);
    this.sendPos();
    this.onPlayState();
  },
  onPlayState() {
    const pb = $("#pausebtn");
    const paused = this.st.paused || (Player.active && !Player.playing);
    if (pb) pb.innerHTML = `${icon(paused ? "play_arrow" : "pause")}${paused ? "Resume" : "Pause"}`;
    document.querySelectorAll("[data-playicon]").forEach((el) => (el.textContent = paused ? "play_arrow" : "pause"));
  },
};
