// PROTOTYPE — throwaway. Screens 3.1 – 4.2 (script.md): the walk runs on its own.
// Each stop screen: the dot walks its leg (~6 s), the story starts on arrival, and when it
// ends the walk moves on by itself. Pause, levers, Ask and Next/Back are the traveller's.

const LEG_MS = 6000;
const HOLD_MS = 2500;

// A standard stop: walk the leg, play the story, optionally one "Tell me more", move on.
function stopScreen({ leg, story, more, where, meta, time }) {
  return {
    theme: "dark", walk: true, time,
    render({ maya }) {
      Walk.dropped = false;
      Walk.pos = PATHS[leg][0].slice();
      const w = walkScaffold(maya, { where });
      setWalking(w, true);
      let moreUsed = false;
      // If "Tell me more" started during the hold, its own end moves the walk on.
      const finish = () => wait(HOLD_MS, () => { if (!Player.active) App.next(leg); });
      const playMain = () => playOn(w, C.stories[story], meta, () => (pendingMore ? null : finish()));
      let pendingMore = false;
      const playMore = () => {
        if (moreUsed || !C.stories[more]) return toast(maya, "That's every story here in your lenses.");
        moreUsed = true; pendingMore = true;
        playOn(w, C.stories[more], meta + " · more", () => finish());
      };
      wirePlayer(w, { onNext: () => App.next(), onPrev: playMain, onReplay: playMain });
      wireLevers(maya, w, { more: playMore });
      Walk.move(maya, PATHS[leg], LEG_MS, () => { App.record("system", "arrived", { stop: C.stories[story].poi }); playMain(); });
    },
  };
}

App.def("3.1", stopScreen({ leg: "3.1", story: "memorial", more: "memorial_more", where: "memorial", meta: "Stop 1 of 6", time: "9:30" }));
App.def("3.1b", stopScreen({ leg: "3.1b", story: "trinity", more: "trinity_more", where: "trinity", meta: "Stop 2 of 6", time: "11:15" }));

// ---- 3.3 Federal Hall — same spot, different story (two phones)
App.def("3.3", {
  theme: "dark", walk: true, layout: "split", time: "11:45",
  render({ maya, dan }) {
    Walk.dropped = false;
    Walk.pos = PATHS["3.3"][0].slice();
    const wm = walkScaffold(maya, { where: "federal" });
    const wd = walkScaffold(dan, { levers: false, closeX: false });
    setWalking(wm, true); setWalking(wd, true);
    wd.insertAdjacentHTML("afterbegin", `<div class="whose">${icon("person")}Dan's phone</div>`);
    const fm = C.stories.fh_maya, fd = C.stories.fh_dan;
    const phones = () => document.querySelectorAll(".phone-wrap");
    const focus = (who) => phones().forEach((p) => p.classList.toggle("quiet", p.dataset.phone !== who));
    // Dan's card shows what's queued on his phone until it plays.
    const queue = (w, s, label) => {
      $(".np-lens", w).textContent = lensOf(s.lens).label; $(".np-name", w).textContent = s.poi;
      $(".np-stop", w).textContent = label; $(".t1", w).textContent = fmtSecs(s.secs); setWalking(w, false);
    };
    let played = { maya: false, dan: false };
    const done = () => { if (played.maya && played.dan) { App.setClock("12:10"); wait(4000, () => App.next("3.3")); } };
    const playMaya = () => { focus("maya"); queue(wd, fd, "Up next on Dan's phone"); playOn(wm, fm, "Stop 3 of 6", () => { played.maya = true; playDan(); }); };
    const playDan = () => { focus("dan"); queue(wm, fm, "Stop 3 of 6 · played"); playOn(wd, fd, "Stop 3 of 6 · Dan", () => { played.dan = true; focus(null); done(); }); };
    wirePlayer(wm, { onNext: () => App.next(), onPrev: playMaya, onReplay: playMaya });
    wirePlayer(wd, { onNext: () => App.next(), onPrev: playDan, onReplay: playDan });
    // Each phone's own play button starts that phone's story.
    $(".np-play", wd).onclick = () => { if (Player.active && Player.story === fd) App.togglePause(); else playDan(); };
    $(".np-play", wm).onclick = () => { if (Player.active && Player.story === fm) App.togglePause(); else playMaya(); };
    wireLevers(maya, wm, {
      more: () => { focus("maya"); playOn(wm, C.stories.fh_more, "Stop 3 of 6 · more", () => { played.maya = true; if (!played.dan) playDan(); else done(); }); },
    });
    Walk.move(maya, PATHS["3.3"], LEG_MS, () => { App.record("system", "arrived", { stop: "Federal Hall" }); playMaya(); });
  },
  leave() { document.querySelectorAll(".phone-wrap").forEach((p) => p.classList.remove("quiet")); },
});

// ---- 4.1 Heads-up: the re-plan fires by itself
App.def("4.1", {
  theme: "dark", walk: true, time: "12:10",
  render({ maya }) {
    Walk.dropped = false;
    Walk.pos = PTS.federal.slice();
    const w = walkScaffold(maya, { levers: false, where: "federal" });
    setWalking(w, true);
    $(".walkbar span:not(.ms)", w).textContent = "Still at Federal Hall";
    const r = C.replan;
    wait(1400, () => {
      App.setClock("12:55", true);
      App.record("system", "re-plan fired", { clock: "12:10 → 12:55" });
      wait(1100, () => {
        maya.insertAdjacentHTML("beforeend", `<div class="replan"><div class="card">
          <div class="eyebrow" style="color:var(--spark)">${icon("update")} Your day changed</div>
          <h2 class="h2" style="margin-top:8px">${esc(r.title)}</h2>
          <p class="body" style="margin:6px 0 12px">${esc(r.body)}</p>
          ${r.changes.map((c) => `<div class="chg"><span class="was${c.struck ? " struck" : ""}">${esc(c.was)}</span>${icon("arrow_forward")}<span class="chg-now"><b>${esc(c.now)}</b><br><span class="why">${esc(c.why)}</span></span></div>`).join("")}
          <div id="rp-warn"></div>
          <div style="display:flex;gap:10px;margin-top:14px"><button class="btn" style="flex:1" id="rp-ok" data-log="Re-plan: Sounds good">Sounds good</button><button class="btn quiet" id="rp-keep" data-log="Re-plan: Keep original">Keep original</button></div>
        </div></div>`);
        $("#rp-ok", maya).onclick = () => { App.st.replanChoice = "sounds good"; App.next(); };
        $("#rp-keep", maya).onclick = () => {
          App.st.replanChoice = "keep original";
          $("#rp-warn", maya).innerHTML = `<div class="warn">${icon("warning", "fill")}${esc(r.keepWarning)}</div>`;
          wait(2600, () => App.next("4.1"));
        };
      });
    });
  },
});

// ---- 4.2 Walk-past: Bowling Green plays without stopping, then on to Castle Clinton
App.def("4.2", {
  theme: "dark", walk: true, time: "1:05",
  render({ maya }) {
    Walk.dropped = true;
    Walk.pos = PTS.federal.slice();
    const w = walkScaffold(maya, { where: "bowling" });
    setWalking(w, true);
    let storyDone = false, arrived = false;
    const maybeNext = () => storyDone && arrived && wait(1500, () => App.next("4.2"));
    const s = C.stories.bowling;
    wirePlayer(w, { onNext: () => App.next(), onPrev: () => playOn(w, s, "On the way · walking past", null), onReplay: () => playOn(w, s, "On the way · walking past", null) });
    wireLevers(maya, w, { more: () => toast(maya, "That's every story here in your lenses.") });
    Walk.move(maya, PATHS["4.2a"], 5000, () => {
      playOn(w, s, "On the way · walking past", () => { storyDone = true; maybeNext(); });
      w.classList.add("passing");
      Walk.move(maya, PATHS["4.2b"], Math.max(12000, s.secs * 900), () => { arrived = true; App.record("system", "arrived", { stop: "Castle Clinton" }); maybeNext(); });
    });
  },
});
