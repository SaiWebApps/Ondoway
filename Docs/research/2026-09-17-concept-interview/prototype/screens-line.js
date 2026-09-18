// PROTOTYPE — throwaway. Screens 5.1 – 5.4 (script.md): in line — the Deep dive, with Echoes.

// The Deep dive keeps playing across 5.2 → 5.3 → 5.4 → 6.1 (those screens set keepAudio).
const DeepDive = {
  i: 0, started: false, done: false, view: null, // view(p, si) is the current screen's painter
  start() { if (!this.started) { this.started = true; this.play(0); } },
  play(i) {
    const chs = C.deepDive.chapters;
    this.i = i; this.done = false;
    Player.play(chs[i], {
      onEnd: () => { if (i + 1 < chs.length) this.play(i + 1); else { this.done = true; this.paint(1, -1); App.record("system", "deep dive finished"); } },
      onTick: (p, si) => this.paint(p, si),
    });
    App.record("system", "deep dive chapter", { n: i + 1, beat: chs[i].beat });
    this.paint(0, 0);
  },
  paint(p, si) { if (this.view) this.view(p, si); },
  toggle() { if (Player.active) App.togglePause(); else if (!this.done) this.play(this.i); else this.play(0); },
};
function miniPlayer() {
  return `<div class="mini" data-log="Mini player">
    <div class="mini-ic">${icon("volume_up")}</div>
    <div style="flex:1;min-width:0"><div class="mini-t">${esc(C.deepDive.title)}</div><div class="mini-s" id="mini-s"></div><div class="mini-bar"><i id="mini-bar"></i></div></div>
    <button class="mini-play" id="mini-play" data-log="Mini player: play/pause"><span class="ms fill" data-playicon>${Player.playing ? "pause" : "play_arrow"}</span></button>
  </div>`;
}
function wireMini(el) {
  const chs = C.deepDive.chapters;
  $("#mini-play", el).onclick = () => DeepDive.toggle();
  DeepDive.view = (p) => {
    const s = $("#mini-s", el); if (!s) return;
    s.textContent = DeepDive.done ? "Chapter finished · quiet until the boat" : `Part ${DeepDive.i + 1} of ${chs.length} · playing out loud`;
    $("#mini-bar", el).style.width = ((DeepDive.i + p) / chs.length) * 100 + "%";
  };
  DeepDive.paint(Player.progress(), Player.sentenceIdx());
}

// ---- 5.1 In line?
App.def("5.1", {
  theme: "dark", time: "1:25",
  render({ maya }) {
    Walk.dropped = true; Walk.pos = PTS.castle.slice();
    const w = walkScaffold(maya, { levers: false }); setWalking(w, true);
    $(".walkbar", w).style.display = "none";
    maya.insertAdjacentHTML("beforeend", `<div class="replan"><div class="card">
      <div class="eyebrow" style="color:var(--accent)">${icon("groups")} Castle Clinton · ferry line</div>
      <h2 class="h2" style="margin-top:8px">Looks like you're in line for the ferry.</h2>
      <p class="body" style="margin:6px 0 14px">Want the story of the statue while you wait? One story for all four of you, out loud.</p>
      <div class="eyebrow">How long's the line?</div>
      <div class="linechips">${["5 min", "15 min", "30+ min"].map((t) => `<button data-len="${t}" data-log="Line length: ${t}">${t}</button>`).join("")}</div>
    </div></div>`);
    maya.querySelectorAll("[data-len]").forEach((b) => (b.onclick = () => { App.st.lineLength = b.dataset.len; App.next(); }));
  },
});

// ---- 5.2 Deep dive — full-screen player, out loud
App.def("5.2", {
  theme: "dark", walk: true, keepAudio: true, time: "1:27",
  render({ maya }) {
    const dd = C.deepDive, chs = dd.chapters;
    maya.insertAdjacentHTML("beforeend", `<div class="scr dd fade-in">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <span class="pill loud">${icon("volume_up", "fill")}Playing out loud · 4 listening</span>
        <button class="pill look" id="look" data-log="Look around (Echoes)">${icon("photo_camera")}Look around</button>
      </div>
      <div class="dd-art">${icon("sailing")}</div>
      <div class="eyebrow">Deep dive · Statue of Liberty</div>
      <h1 class="h1" style="color:var(--dInk)">${esc(dd.title)}</h1>
      <p class="body" style="margin:6px 0 0">${esc(dd.note)}${App.st.lineLength ? ` <span class="muted">(line: ${esc(App.st.lineLength)})</span>` : ""}</p>
      <div class="dots">${chs.map((_, i) => `<i data-d="${i}"></i>`).join("")}</div>
      <div class="dd-now"><span id="dd-lens" class="np-lens"></span> <span id="dd-poi" class="muted"></span></div>
      <div class="scrub"><i id="dd-bar"></i></div>
      <div class="np-ctl" style="margin-top:10px">
        <button id="dd-prev" data-log="Deep dive: previous part">${icon("skip_previous")}</button>
        <button class="np-play" id="dd-play" data-log="Deep dive: play/pause"><span class="ms fill" data-playicon>pause</span></button>
        <button id="dd-next" data-log="Deep dive: next part">${icon("skip_next")}</button>
      </div>
      <button class="np-text" id="dd-text" data-log="Text (transcript) toggle" style="margin:6px auto 0;display:flex">${icon("notes")}Text</button>
      <div class="np-tx" id="dd-tx" hidden></div>
    </div>`);
    const tx = $("#dd-tx", maya);
    let shown = -1, lastS = -1;
    DeepDive.view = (p, si) => {
      const ch = chs[DeepDive.i];
      maya.querySelectorAll(".dots i").forEach((d, i) => d.className = i < DeepDive.i || DeepDive.done ? "done" : i === DeepDive.i ? "on" : "");
      $("#dd-lens", maya).textContent = lensOf(ch.lens).label;
      $("#dd-poi", maya).textContent = `· ${ch.poi} · part ${DeepDive.i + 1} of ${chs.length}`;
      $("#dd-bar", maya).style.width = p * 100 + "%";
      if (shown !== DeepDive.i) { tx.innerHTML = Player.split(ch.text).map((s, k) => `<span data-s="${k}">${esc(s)} </span>`).join(""); shown = DeepDive.i; lastS = -1; }
      if (si !== lastS) { tx.querySelectorAll("span").forEach((s) => s.classList.toggle("hl", +s.dataset.s === si)); lastS = si; }
      if (DeepDive.done) $("#dd-poi", maya).textContent = "· that's the chapter — quiet until the boat";
    };
    $("#dd-text", maya).onclick = () => { tx.hidden = !tx.hidden; $("#dd-text", maya).classList.toggle("on", !tx.hidden); };
    $("#dd-play", maya).onclick = () => DeepDive.toggle();
    $("#dd-prev", maya).onclick = () => DeepDive.play(Math.max(0, DeepDive.i - 1));
    $("#dd-next", maya).onclick = () => DeepDive.i + 1 < chs.length && DeepDive.play(DeepDive.i + 1);
    $("#look", maya).onclick = () => App.next();
    DeepDive.start();
    DeepDive.paint(Player.progress(), Player.sentenceIdx());
    App.onPlayState();
  },
  leave() { DeepDive.view = null; },
});

// ---- Camera scene shared by 5.3, 5.4 and 6.1 (Maya's phone)
function harborScene() {
  if (C.photos.castleClintonScene) return `<img class="cam-img" src="${esc(C.photos.castleClintonScene)}" alt="Castle Clinton and the harbor">`;
  return `<svg class="cam-img" viewBox="0 0 368 822" preserveAspectRatio="xMidYMid slice" aria-label="Camera view placeholder">
    <defs><linearGradient id="sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#9DC3E6"/><stop offset="1" stop-color="#DCE9F2"/></linearGradient></defs>
    <rect width="368" height="420" fill="url(#sky)"/><rect y="400" width="368" height="422" fill="#5E7F95"/>
    <path d="M0 420 Q184 404 368 420 L368 440 L0 440Z" fill="#6F8FA3"/>
    <g fill="#6E8A7B"><rect x="228" y="376" width="80" height="28"/><rect x="236" y="354" width="6" height="24"/><rect x="258" y="350" width="6" height="28"/><rect x="274" y="350" width="6" height="28"/><rect x="296" y="354" width="6" height="24"/></g>
    <g fill="#6E9C8A"><rect x="146" y="360" width="22" height="44"/><path d="M151 360 L157 300 L163 360Z"/><circle cx="157" cy="296" r="6"/><rect x="165" y="276" width="3" height="26"/></g>
    <path d="M-10 822 L-10 560 Q184 500 378 560 L378 822Z" fill="#8A7B68"/>
    <path d="M-10 600 Q184 540 378 600" stroke="#6D604F" stroke-width="10" fill="none"/>
    <text x="184" y="720" text-anchor="middle" fill="#F6F4F0" font-family="Space Mono, monospace" font-size="11" letter-spacing="2">CAMERA · CASTLE CLINTON</text>
    <text x="184" y="738" text-anchor="middle" fill="#F6F4F0" font-family="Space Mono, monospace" font-size="10" letter-spacing="2">TODO: REAL PHOTO BEFORE PILOT</text>
  </svg>`;
}
const echoState = C.echoes.map((e) => ({ ...e, counts: { ...e.counts } }));
function cameraView(el, { leaveBtn = true, fresh = false } = {}) {
  const mine = App.st.echoLeft;
  el.insertAdjacentHTML("beforeend", `<div class="cam">${harborScene()}
    <div class="cam-top"><span class="pill cam-pill">${icon("photo_camera", "fill")}Echoes here · ${echoState.length + (mine ? 1 : 0)}</span></div>
    ${echoState.map((e, i) => `<button class="echo" data-e="${i}" style="left:${e.x}%;top:${e.y}%" data-log="Echo: open" data-detail='${JSON.stringify({ echo: e.kind + " " + e.text }).replace(/'/g, "&#39;")}'>
      <b>${esc(e.kind)}</b> ${esc(e.text)}<span class="ecount">${reactionsLine(e.counts)}</span></button>`).join("")}
    ${mine ? `<div class="echo mine${fresh ? " fresh" : ""}" style="left:30%;top:46%"><b>Your echo</b> ${esc(mine)}<span class="ecount">0 found — yet</span></div>` : ""}
    <div class="cam-bottom">${leaveBtn ? `<button class="btn block" id="leave" data-log="Leave an echo">${icon("add_location_alt")}Leave an echo</button>` : ""}${miniPlayer()}</div>
  </div>`);
  wireMini(el);
  el.querySelectorAll(".echo[data-e]").forEach((b) => (b.onclick = (ev) => {
    if (ev.target.closest(".react")) return;
    const open = b.classList.contains("open");
    el.querySelectorAll(".echo.open").forEach((x) => { x.classList.remove("open"); x.querySelector(".reacts")?.remove(); });
    if (open) return;
    const e = echoState[+b.dataset.e];
    b.classList.add("open");
    b.insertAdjacentHTML("beforeend", `<span class="reacts">${[["found", "Found it"], ["worth", "Worth it"], ["ha", "Ha!"]].map(([k, l]) => `<span class="react" data-k="${k}" data-log="Echo reaction: ${l}" data-detail='${JSON.stringify({ echo: e.kind + " " + e.text }).replace(/'/g, "&#39;")}'>${l}</span>`).join("")}</span>`);
    b.querySelectorAll(".react").forEach((r) => (r.onclick = () => {
      e.counts[r.dataset.k]++; r.classList.add("done");
      b.querySelector(".ecount").innerHTML = reactionsLine(e.counts);
    }));
  }));
  const lb = $("#leave", el); if (lb) lb.onclick = () => App.next();
}
function reactionsLine(c) {
  return [["found", "Found it"], ["worth", "Worth it"], ["ha", "Ha!"]].filter(([k]) => c[k]).map(([k, l]) => `${l} ${c[k]}`).join(" · ");
}

// ---- 5.3 Look around — Echoes
App.def("5.3", {
  walk: true, keepAudio: true, time: "1:31",
  render({ maya }) { cameraView(maya); },
  leave() { DeepDive.view = null; },
});

// ---- 5.4 Leave an echo — templates and word lists only
App.def("5.4", {
  walk: true, keepAudio: true, time: "1:33",
  render({ maya }) {
    const st = { tpl: null, dir: null, thing: null };
    const draw = () => {
      const needsDir = st.tpl && st.tpl.startsWith("Look");
      const text = !st.tpl ? "" : needsDir ? `Look ${st.dir || "___"} at ${st.thing || "___"}` : st.tpl.replace("___", st.thing || "___");
      const ready = st.tpl && st.thing && (!needsDir || st.dir);
      maya.querySelector(".scr")?.remove();
      maya.insertAdjacentHTML("beforeend", `<div class="scr" style="padding-bottom:130px">
        <div class="eyebrow">Leave an echo · Castle Clinton</div>
        <h1 class="h1" style="font-size:28px">What should the next family know?</h1>
        <div class="eyebrow" style="margin-top:18px">1 · Pick one</div>
        <div class="chips" style="margin-top:8px">${C.echoTemplates.map((t) => `<button class="wchip${st.tpl === t ? " on" : ""}" data-tpl="${esc(t)}" data-log="Echo template: ${esc(t)}">${esc(t)}</button>`).join("")}</div>
        ${st.tpl ? `<div class="eyebrow" style="margin-top:18px">2 · Pick the words</div>
          ${needsDir ? `<div class="chips" style="margin-top:8px">${C.echoDirections.map((d) => `<button class="wchip${st.dir === d ? " on" : ""}" data-dir="${esc(d)}" data-log="Echo word: ${esc(d)}">${esc(d)}</button>`).join("")}</div>` : ""}
          <div class="chips" style="margin-top:8px">${C.echoThings.map((d) => `<button class="wchip${st.thing === d ? " on" : ""}" data-thing="${esc(d)}" data-log="Echo word: ${esc(d)}">${esc(d)}</button>`).join("")}</div>` : ""}
        ${st.tpl ? `<div class="card preview"><div class="eyebrow">Preview</div><div class="ptext">${esc(text)}</div></div>` : ""}
        <button class="btn block" id="pin" style="margin-top:16px;${ready ? "" : "opacity:.4"}" ${ready ? "" : "disabled"} data-log="Leave it here" data-detail='${JSON.stringify({ echo: text }).replace(/'/g, "&#39;")}'>${icon("push_pin")}Leave it here</button>
        <div style="margin-top:14px">${miniPlayer()}</div>
      </div>`);
      wireMini(maya);
      maya.querySelectorAll("[data-tpl]").forEach((b) => (b.onclick = () => { st.tpl = b.dataset.tpl; st.dir = null; draw(); }));
      maya.querySelectorAll("[data-dir]").forEach((b) => (b.onclick = () => { st.dir = b.dataset.dir; draw(); }));
      maya.querySelectorAll("[data-thing]").forEach((b) => (b.onclick = () => { st.thing = b.dataset.thing; draw(); }));
      $("#pin", maya).onclick = () => {
        if (!ready) return;
        App.st.echoLeft = text;
        App.record("echo-left", text);
        maya.querySelector(".scr").remove();
        cameraView(maya, { leaveBtn: false, fresh: true });
      };
    };
    draw();
  },
  leave() { DeepDive.view = null; },
});
