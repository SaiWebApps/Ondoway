// PROTOTYPE — throwaway. The walk's parts (= tour_walk_page.dart, dark): a simple SVG map of
// Lower Manhattan, the moving blue dot, the now-playing card with transcript, the lever bank
// and the Ask sheet. Screen definitions that use these live in screens-walk.js.

// Map coordinates (viewBox 368×822, north up). Schematic, not to scale.
const PTS = {
  start: [150, 96], memorial: [118, 168], trinity: [172, 292], federal: [236, 280],
  fraunces: [262, 452], bowling: [178, 492], castle: [112, 584], liberty: [26, 668],
};
const PATHS = {
  "3.1": [PTS.start, [132, 128], PTS.memorial],
  "3.1b": [PTS.memorial, [140, 214], [160, 262], PTS.trinity],
  "3.3": [PTS.trinity, [204, 286], PTS.federal],
  "4.2a": [PTS.federal, [240, 350], [214, 430], PTS.bowling],
  "4.2b": [PTS.bowling, [150, 530], PTS.castle],
};

const Walk = {
  pos: PTS.start.slice(),
  dropped: false,
  mapSvg() {
    const pin = (k, label, n, opts = {}) => {
      const [x, y] = PTS[k];
      const ec = C.stopNames[k] ? echoCount(k) : 0;
      return `<g class="pin${opts.dim ? " dim" : ""}${opts.must ? " must" : ""}" transform="translate(${x},${y})">
        <circle r="${opts.must ? 11 : 9}"/>${n ? `<text y="4" text-anchor="middle">${n}</text>` : ""}
        <text class="plabel" x="${opts.left ? -15 : 15}" y="4" text-anchor="${opts.left ? "end" : "start"}">${label}</text>
        ${opts.dim ? `<line x1="-10" y1="-10" x2="10" y2="10"/>` : ""}
        ${ec ? `<g class="ebadge" transform="translate(${opts.left ? 6 : -30},-26)"><rect width="26" height="16" rx="8"/><text x="13" y="12" text-anchor="middle">◉ ${ec}</text></g>` : ""}</g>`;
    };
    const route = [PTS.start, [132, 128], PTS.memorial, [140, 214], [160, 262], PTS.trinity, [204, 286], PTS.federal,
      ...(this.dropped ? [[240, 350], [214, 430], PTS.bowling] : [[256, 360], PTS.fraunces, [214, 480], PTS.bowling]),
      [150, 530], PTS.castle].map((p) => p.join(",")).join(" ");
    const grid = [];
    for (let i = 0; i < 16; i++) grid.push(`<line x1="${40 + i * 22}" y1="0" x2="${i * 22 - 60}" y2="822"/>`);
    for (let i = 0; i < 18; i++) grid.push(`<line x1="0" y1="${i * 44}" x2="368" y2="${i * 44 + 70}"/>`);
    return `<svg class="map" viewBox="0 0 368 822" preserveAspectRatio="xMidYMin slice" aria-label="Map of Lower Manhattan">
      <rect width="368" height="822" class="water"/>
      <path class="land" d="M58 0 L66 180 L62 400 L74 520 L96 600 L136 640 L190 628 L262 560 L322 470 L368 400 L368 0 Z"/>
      <clipPath id="landclip"><path d="M58 0 L66 180 L62 400 L74 520 L96 600 L136 640 L190 628 L262 560 L322 470 L368 400 L368 0 Z"/></clipPath>
      <g class="streets" clip-path="url(#landclip)">${grid.join("")}</g>
      <path class="park" d="M92 560 L150 600 L182 596 L150 540 L96 530 Z"/>
      <text class="water-label" x="18" y="300" transform="rotate(-86 18 300)">HUDSON RIVER</text>
      <text class="water-label" x="228" y="700">UPPER BAY</text>
      <polyline class="route glow" points="${route}"/>
      <polyline class="route" points="${route}"/>
      ${pin("memorial", "9/11 Memorial", 1, { must: true, left: false })}
      ${pin("trinity", "Trinity Church", 2, { left: true })}
      ${pin("federal", "Federal Hall", 3)}
      ${pin("fraunces", this.dropped ? "Fraunces Tavern → Sun" : "Fraunces Tavern", this.dropped ? "" : 4, { dim: this.dropped })}
      ${pin("bowling", "Bowling Green", "", {})}
      ${pin("castle", "Castle Clinton", 5, {})}
      ${pin("liberty", "Statue of Liberty · ferry", "", { must: true })}
      <g class="dot" transform="translate(${this.pos[0]},${this.pos[1]})"><circle class="halo" r="18"/><circle r="8"/></g>
    </svg>`;
  },
  // Move the dot along `path` over `ms`, respecting pause. Stops if the screen changes.
  move(screen, path, ms, done) {
    const id = App.id;
    const lens = []; let total = 0;
    for (let i = 1; i < path.length; i++) { const d = Math.hypot(path[i][0] - path[i - 1][0], path[i][1] - path[i - 1][1]); lens.push(d); total += d; }
    let elapsed = 0, last = performance.now();
    const step = (now) => {
      if (App.id !== id) return;
      if (!App.st.paused) elapsed += now - last;
      last = now;
      let d = Math.min(1, elapsed / ms) * total, i = 0;
      while (i < lens.length - 1 && d > lens[i]) { d -= lens[i]; i++; }
      const f = lens[i] ? Math.min(1, d / lens[i]) : 1;
      this.pos = [path[i][0] + (path[i + 1][0] - path[i][0]) * f, path[i][1] + (path[i + 1][1] - path[i][1]) * f];
      document.querySelectorAll(".map .dot").forEach((g) => g.setAttribute("transform", `translate(${this.pos[0]},${this.pos[1]})`));
      if (elapsed < ms) requestAnimationFrame(step); else done && done();
    };
    requestAnimationFrame(step);
  },
};

// Pause-aware wait that dies with the screen.
function wait(ms, fn) {
  const id = App.id; let left = ms, last = Date.now();
  const t = setInterval(() => {
    if (App.id !== id) return clearInterval(t);
    const now = Date.now(); if (!App.st.paused) left -= now - last; last = now;
    if (left <= 0) { clearInterval(t); fn(); }
  }, 100);
}

// ---- Walk screen scaffold: map + walking bar + (levers + now-playing card)
const POI_IMG = { "9/11 Memorial": "memorial_wide", "Trinity Church": "trinity_wide", "Federal Hall": "federal_wide", "Bowling Green": "bowling_wide", "Castle Clinton": "castle_wide", "Statue of Liberty": "liberty" };
// `where` = the stop Ask and the camera are about: the stop you're at or walking to.
function walkScaffold(el, { levers = true, closeX = true, where = null } = {}) {
  // v10 "06 · Live guide": .g-map under a .g-banner, .g-dots and the .g-player card.
  el.insertAdjacentHTML("beforeend", `<div class="walk guide">
    <div class="g-map">${Walk.mapSvg()}</div>
    ${closeX ? `<button class="walk-x" data-log="Close walk (X)">${icon("close")}</button>` : ""}
    <div class="walkbar g-banner"><div class="ar">${icon("directions_walk")}</div><div class="bx"><div class="bt"><span>Walk to the next stop — audio starts on arrival</span></div></div></div>
    <div class="tooldock">
    ${(levers || where) ? `<div class="toolrow" role="toolbar" aria-label="What you can do here">
        ${where ? `<button class="ask-btn" data-log="Ask button" data-detail='{"where":"${where}"}'>${icon("forum")}Ask</button>` : ""}
        ${where ? `<button class="cam-btn" data-log="Camera button" data-detail='{"where":"${where}"}'>${icon("photo_camera")}Echoes</button>` : ""}
        ${levers ? `<button data-lever="more" data-log="Lever: Tell me more">${icon("add_circle")}Tell me more</button>
        <button data-lever="skip" data-log="Lever: Skip this stop">${icon("skip_next")}Skip stop</button>
        <button data-lever="short" data-log="Lever: Shorter day">${icon("schedule")}Shorter day</button>` : ""}
      </div>` : ""}
    </div>
    <div class="np-wrap">
      <div class="g-dots"></div>
      <div class="np g-player">
        <div class="np-top gp-top">
          <div class="np-tile gp-art">${icon("headphones")}</div>
          <div class="gp-meta"><div class="np-lens gp-lens"></div><div class="np-name gp-title"></div><div class="np-stop gp-prog"></div></div>
          <button class="np-text gp-txt" data-log="Text (transcript) toggle">${icon("notes")}Text</button>
        </div>
        <div class="np-tx" hidden></div>
        <div class="gp-scrub"><span class="t0 t">0:00</span><span class="scrub gp-track"><i></i><b></b></span><span class="t1 t">0:00</span></div>
        <div class="np-ctl gp-ctrls">
          <button class="np-prev c" data-log="Player: previous">${icon("skip_previous")}</button>
          <button class="np-play play" data-log="Player: play/pause">${icon("pause", "fill").replace('class="ms fill"', 'class="ms fill" data-playicon')}</button>
          <button class="np-next c" data-log="Player: next">${icon("skip_next")}</button>
        </div>
      </div>
    </div>
  </div>`);
  const w = $(".walk", el);
  const tx = $(".np-tx", w);
  $(".np-text", w).onclick = () => { tx.hidden = !tx.hidden; $(".np-text", w).classList.toggle("on", !tx.hidden); };
  $(".walk-x", w) && ($(".walk-x", w).onclick = () => toast(el, "The walk keeps going — this is the only route in the prototype."));
  if (where) wireTools(el, w, where);
  return w;
}
function setWalking(w, walking) { w.classList.toggle("walking", walking); }
// v10 .g-dots — the stop counter drawn as dots. Reads the same "Stop N of M" the card
// already shows; anything else (a walk-past, Dan's copy) leaves the row empty.
function setStopDots(w, meta) {
  const gd = $(".g-dots", w);
  if (!gd) return;
  const m = /Stop (\d+) of (\d+)/.exec(meta || "");
  gd.innerHTML = m ? Array.from({ length: +m[2] }, (_, i) => `<i class="${i + 1 === +m[1] ? "on" : i + 1 < +m[1] ? "done" : ""}"></i>`).join("") : "";
}
// Fill the card for a story and play it. Returns nothing; onEnd fires when it finishes.
// Dress a player card with a story, without starting it. Split out of playOn so a second
// phone can show the same story running on a shared clock while you listen to the other.
function mountStory(w, story, meta) {
  const l = lensOf(story.lens);
  const slot = POI_IMG[story.poi];
  $(".np-tile", w).innerHTML = slot && C.img[slot] ? pic(slot, story.poi) : icon("headphones");
  $(".np-lens", w).textContent = l.label;
  $(".np-name", w).textContent = story.poi;
  $(".np-stop", w).textContent = meta;
  setStopDots(w, meta);
  $(".t1", w).textContent = fmtSecs(story.secs);
  $(".np-tx", w).innerHTML = Player.split(story.text).map((s, i) => `<span data-s="${i}">${esc(s)} </span>`).join("");
  setWalking(w, false);
}

// Paint progress on a card from an outside clock (0..1) — no audio, no Player.
function paintProgress(w, story, p, lastSeen) {
  $(".scrub i", w).style.width = p * 100 + "%";
  const knob = $(".scrub b", w); if (knob) knob.style.left = p * 100 + "%";
  $(".t0", w).textContent = fmtSecs(p * story.secs);
  const sents = w.querySelectorAll(".np-tx span");
  if (!sents.length) return lastSeen;
  const si = Math.min(sents.length - 1, Math.floor(p * sents.length));
  if (si !== lastSeen) sents.forEach((s) => s.classList.toggle("hl", +s.dataset.s === si));
  return si;
}

function playOn(w, story, meta, onEnd, { from = 0 } = {}) {
  mountStory(w, story, meta);
  let lastS = -1;
  Player.play(story, {
    onEnd,
    from,
    onTick(p, si) {
      $(".scrub i", w).style.width = p * 100 + "%";
      const knob = $(".scrub b", w); if (knob) knob.style.left = p * 100 + "%"; // v10 .gp-track handle
      $(".t0", w).textContent = fmtSecs(p * story.secs);
      if (si !== lastS) {
        w.querySelectorAll(".np-tx span").forEach((s) => s.classList.toggle("hl", +s.dataset.s === si));
        const cur = $(`.np-tx span[data-s="${si}"]`, w); if (cur) cur.scrollIntoView({ block: "nearest", behavior: "smooth" });
        lastS = si;
      }
    },
  });
  App.record("system", "story started", { source: story.source, poi: story.poi, lens: story.lens, from: from || undefined });
}
function wirePlayer(w, { onNext, onPrev, onReplay }) {
  $(".np-play", w).onclick = () => { if (Player.active) App.togglePause(); else onReplay && onReplay(); };
  $(".np-next", w).onclick = () => onNext && onNext();
  $(".np-prev", w).onclick = () => onPrev && onPrev();
}
function toast(el, text) {
  const t = document.createElement("div"); t.className = "toast"; t.textContent = text; el.appendChild(t);
  setTimeout(() => t.remove(), 2600);
}

// ---- Sheets: Shorter day, Ask about this
function sheet(el, html, onClose) {
  el.insertAdjacentHTML("beforeend", `<div class="sheet-scrim"><div class="sheet"><div class="grab"></div>${html}</div></div>`);
  const scrim = el.lastElementChild;
  const close = () => { scrim.remove(); onClose && onClose(); };
  scrim.addEventListener("click", (e) => { if (e.target === scrim) { App.record("tap", "Sheet dismissed (outside)"); close(); } });
  return { scrim, close };
}
function withStoryHeld(fn) {
  const wasPlaying = Player.playing; if (wasPlaying) Player.pause();
  fn(() => { if (wasPlaying && !App.st.paused) Player.resume(); });
}
function shorterDaySheet(el) {
  withStoryHeld((resume) => {
    const s = sheet(el, `<div class="walksheet"><h2 class="h2">Finish earlier?</h2><p class="body">${esc(C.shorterDay)}</p>
      <div style="display:flex;gap:10px;margin-top:16px"><button class="btn" data-log="Shorter day: Do it" id="sd-yes">Do it</button><button class="btn quiet" data-log="Shorter day: Not now" id="sd-no">Not now</button></div></div>`, resume);
    $("#sd-yes", s.scrim).onclick = () => s.close();
    $("#sd-no", s.scrim).onclick = () => s.close();
  });
}
function askSheet(el, where) {
  const a = C.ask[where] || { place: Player.story ? Player.story.poi : "this place", chips: [] };
  withStoryHeld((resume) => {
    const s = sheet(el, `<div class="walksheet"><div class="eyebrow">Ask about this</div>
      <h2 class="h2" style="margin-top:4px">${esc(a.place)}</h2>
      <div class="chips" style="margin-top:12px">${a.chips.map((c, i) => `<button class="askchip" data-i="${i}" data-log="Ask chip" data-detail='${JSON.stringify({ q: c.q }).replace(/'/g, "&#39;")}'>${esc(c.q)}</button>`).join("")}</div>
      <form class="askf" id="askf"><input id="askq" autocomplete="off" placeholder="Or ask your own about ${esc(a.place)}…"><button class="askgo" data-log="Ask: send typed">${icon("arrow_upward")}</button></form>
      <div id="askans"></div></div>`, resume);
    const ans = $("#askans", s.scrim);
    const show = (q, text, sourced) => {
      App.st.asked.push({ q, place: a.place, sourced });
      ans.insertAdjacentHTML("beforeend", `<div class="qa"><div class="q">${esc(q)}</div>
        <div class="a${sourced ? "" : " none"}">${sourced ? `<div class="src">${icon("menu_book")}From our notes on ${esc(a.place)}</div>` : ""}${esc(text)}</div></div>`);
      ans.lastElementChild.scrollIntoView({ block: "nearest" });
    };
    s.scrim.querySelectorAll(".askchip").forEach((b) => (b.onclick = () => { const c = a.chips[+b.dataset.i]; show(c.q, c.a, true); }));
    $("#askf", s.scrim).onsubmit = (e) => {
      e.preventDefault();
      const q = $("#askq", s.scrim).value.trim(); if (!q) return;
      App.record("ask-typed", q, { place: a.place });
      $("#askq", s.scrim).value = "";
      show(q, C.ask.noAnswer, false);
    };
  });
}
function wireLevers(el, w, { more, where }) {
  w.querySelectorAll("[data-lever]").forEach((b) => (b.onclick = () => {
    const k = b.dataset.lever;
    if (k === "more") more();
    if (k === "skip") { Player.stop(); App.next(); }
    if (k === "short") shorterDaySheet(el);
  }));
}

// Ask + camera buttons on the map (revised 2026-09-18): always visible on the ground.
function wireTools(el, w, where) {
  $(".ask-btn", w).onclick = () => askSheet(el, where);
  $(".cam-btn", w).onclick = () => openStopCamera(el, where);
}
function openStopCamera(el, where) {
  const stop = where === "memorial" || where === "trinity" || where === "federal" || where === "bowling" || where === "castle" ? where : "castle";
  App.record("system", "camera opened", { stop });
  cameraView(el, stop, { overlay: true, onClose: () => {
    // Echo counts on the map may have changed.
    document.querySelectorAll(".map").forEach((m) => { const pos = Walk.pos; m.outerHTML = Walk.mapSvg(); Walk.pos = pos; });
  } });
}
