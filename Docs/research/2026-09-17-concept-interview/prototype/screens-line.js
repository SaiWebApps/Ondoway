// PROTOTYPE — throwaway. Screens 5.1 – 5.4 (script.md): in line — the Deep dive, with Echoes.
// Re-skinned 2026-09-22 to the Ondoway v10 design system (v10.css + skin-line.css):
// 5.2 is built on v10's live-guide player (.g-player / .gp-top / .gp-art / .gp-lens /
// .gp-title / .gp-meta / .gp-prog / .gp-scrub / .gp-track / .gp-ctrls / .play), sized up
// because this one plays out loud for four people; 5.1 uses v10's dark over-map sheet.
// Copy, ids, data-log/data-detail, wiring and timings are untouched — re-skin only.

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
    App.record("system", "deep dive chapter", { n: i + 1, source: chs[i].source });
    this.paint(0, 0);
  },
  paint(p, si) { if (this.view) this.view(p, si); },
  toggle() { if (Player.active) App.togglePause(); else if (!this.done) this.play(this.i); else this.play(0); },
};
// The mini player: the same v10 player parts (.gp-title / .gp-prog / .gp-track) at sheet size.
function miniPlayer() {
  return `<div class="mini" data-log="Mini player">
    <div class="mini-ic">${icon("volume_up")}</div>
    <div class="mini-meta"><div class="mini-t gp-title">${esc(C.deepDive.title)}</div><div class="mini-s gp-prog" id="mini-s"></div><div class="mini-bar gp-track"><i id="mini-bar"></i></div></div>
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

// ---- 5.1 In line?  (v10 idiom: the dark sheet that sits over a live map)
App.def("5.1", {
  theme: "dark", time: "1:25",
  render({ maya }) {
    Walk.dropped = true; Walk.pos = PTS.castle.slice();
    const w = walkScaffold(maya, { levers: false, where: "castle" }); setWalking(w, true);
    $(".walkbar", w).style.display = "none";
    maya.insertAdjacentHTML("beforeend", `<div class="replan"><div class="card linecard">
      <div class="eyebrow line-eye">${icon("groups")} Castle Clinton · ferry line</div>
      <h2 class="h2 h-serif line-h">Looks like you're in line for the ferry.</h2>
      <p class="body line-p">Want the story of the statue while you wait? One story for all four of you, out loud.</p>
      <div class="eyebrow line-q">How long's the line?</div>
      <div class="linechips">${["5 min", "15 min", "30+ min"].map((t) => `<button data-len="${t}" data-log="Line length: ${t}">${t}</button>`).join("")}</div>
    </div></div>`);
    maya.querySelectorAll("[data-len]").forEach((b) => (b.onclick = () => { App.st.lineLength = b.dataset.len; App.next(); }));
  },
});

// ---- 5.2 Deep dive — the v10 player, out loud for four
App.def("5.2", {
  theme: "dark", walk: true, keepAudio: true, time: "1:27",
  render({ maya }) {
    const dd = C.deepDive, chs = dd.chapters;
    maya.insertAdjacentHTML("beforeend", `<div class="scr dd fade-in">
      <div class="dd-top">
        <span class="pill loud">${icon("volume_up", "fill")}Playing out loud · 4 listening</span>
      </div>
      <div class="gp-art dd-art">${C.img.liberty ? pic("liberty", "The Statue of Liberty") : icon("sailing")}
        <span class="dd-acts"><button class="pill look" id="dd-ask" data-log="Ask button" data-detail='{"where":"castle"}'>${icon("forum", "fill")}Ask</button><button class="pill look" id="look" data-log="Look around (Echoes)">${icon("photo_camera")}Look around</button></span>
      </div>
      <div class="eyebrow dd-eye">Deep dive · Statue of Liberty</div>
      <h1 class="h1 gp-title dd-h">${esc(dd.title)}</h1>
      <p class="body dd-note">${esc(dd.note)}${App.st.lineLength ? ` <span class="dd-line">(line: ${esc(App.st.lineLength)})</span>` : ""}</p>
      <div class="dd-dots">${chs.map((_, i) => `<i data-d="${i}"></i>`).join("")}</div>
      <div class="g-player dd-player">
        <div class="gp-top">
          <div class="gp-meta"><div class="dd-now gp-prog"><span id="dd-lens" class="gp-lens"></span> <span id="dd-poi"></span></div></div>
          <button class="np-text gp-txt" id="dd-text" data-log="Text (transcript) toggle">${icon("notes")}Text</button>
        </div>
        <div class="gp-scrub"><span class="gp-track"><i id="dd-bar"></i></span></div>
        <div class="gp-ctrls">
          <button class="c" id="dd-prev" data-log="Deep dive: previous part">${icon("skip_previous")}</button>
          <button class="play" id="dd-play" data-log="Deep dive: play/pause"><span class="ms fill" data-playicon>pause</span></button>
          <button class="c" id="dd-next" data-log="Deep dive: next part">${icon("skip_next")}</button>
        </div>
        <div class="np-tx" id="dd-tx" hidden></div>
      </div>
    </div>`);
    const tx = $("#dd-tx", maya);
    let shown = -1, lastS = -1;
    DeepDive.view = (p, si) => {
      const ch = chs[DeepDive.i];
      maya.querySelectorAll(".dd-dots i").forEach((d, i) => d.className = i < DeepDive.i || DeepDive.done ? "done" : i === DeepDive.i ? "on" : "");
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
    $("#dd-ask", maya).onclick = () => askSheet(maya, "castle");
    DeepDive.start();
    DeepDive.paint(Player.progress(), Player.sentenceIdx());
    App.onPlayState();
  },
  leave() { DeepDive.view = null; },
});

// ---- 5.3 Look around — Echoes (the scripted camera moment; camera.js does the work)
App.def("5.3", {
  walk: true, keepAudio: true, time: "1:31",
  render({ maya }) { cameraView(maya, "castle", { mini: true, ask: "castle" }); },
  leave() { DeepDive.view = null; },
});

// ---- 5.4 Leave an echo — templates and word lists only; pins into the camera scene after
App.def("5.4", {
  walk: true, keepAudio: true, time: "1:33",
  render({ maya }) {
    const cam = cameraView(maya, "castle", { mini: true, ask: "castle", leaveBtn: false });
    echoBuilder(cam, "castle", () => { cam.remove(); cameraView(maya, "castle", { mini: true, ask: "castle", leaveBtn: false, fresh: true }); });
  },
  leave() { DeepDive.view = null; },
});
