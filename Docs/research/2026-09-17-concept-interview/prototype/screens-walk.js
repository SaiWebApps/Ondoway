// PROTOTYPE — throwaway. Screens 3.1 – 4.2 (script.md): the walk runs on its own.
// Each stop screen: the dot walks its leg (~6 s), the story starts on arrival, and when it
// ends the walk moves on by itself. Pause, levers, Ask and Next/Back are the traveller's.

const LEG_MS = 6000;
const HOLD_MS = 2500;

// The v10 "Running late" .dropcard carries a thumbnail; use the stop's own NYC photo.
const CHG_IMG = { Fraunces: "fraunces_thumb", "Bowling Green": "bowling_wide", "Castle Clinton": "castle_wide" };
function chgThumb(was) {
  const k = Object.keys(CHG_IMG).find((n) => was.includes(n));
  const src = k && C.img[CHG_IMG[k]];
  return src ? ` style="background-image:url(${src})"` : "";
}

// A standard stop: walk the leg, play the story, then "Tell me more" as many times as this
// stop has stories left. A gravity-5 anchor keeps giving; a walk-by runs out after one.
// (The engine models this as tier: dwell seconds by tier, and an anchor candidate needs
// >= 3 active beats — src/tour/routing.py, src/onboard/beat_draft.py.)
function stopScreen({ leg, story, more, where, meta, time }) {
  const extras = more == null ? [] : Array.isArray(more) ? more.slice() : [more];
  return {
    theme: "dark", walk: true, time,
    render({ maya }) {
      Walk.dropped = false;
      Walk.pos = PATHS[leg][0].slice();
      const w = walkScaffold(maya, { where });
      setWalking(w, true);
      const queue = extras.filter((k) => C.stories[k]);
      let told = 0;
      // If "Tell me more" started during the hold, its own end moves the walk on.
      const finish = () => wait(HOLD_MS, () => { if (!Player.active) App.next(leg); });
      const playMain = () => playOn(w, C.stories[story], meta, () => (pendingMore ? null : finish()));
      let pendingMore = false;
      const playMore = () => {
        if (!queue.length) return toast(maya, "That's every story here in your lenses.");
        const key = queue.shift();
        told += 1;
        pendingMore = true;
        const label = queue.length ? `${meta} · more ${told} of ${told + queue.length}` : `${meta} · more`;
        playOn(w, C.stories[key], label, () => finish());
      };
      wirePlayer(w, { onNext: () => App.next(), onPrev: playMain, onReplay: playMain });
      wireLevers(maya, w, { more: playMore });
      Walk.move(maya, PATHS[leg], LEG_MS, () => { App.record("system", "arrived", { stop: C.stories[story].poi }); playMain(); });
    },
  };
}

App.def("3.1", stopScreen({ leg: "3.1", story: "memorial", more: ["memorial_before", "memorial_dig", "memorial_design", "memorial_names", "memorial_parapet", "memorial_tree"], where: "memorial", meta: "Stop 1 of 6", time: "9:30" }));
App.def("3.1b", stopScreen({ leg: "3.1b", story: "trinity", more: "trinity_more", where: "trinity", meta: "Stop 2 of 6", time: "11:15" }));

// ---- 3.3 Federal Hall — same spot, same moment, two different stories.
// Both stories run on ONE clock from the moment they arrive: Maya's plays in your ears and
// Dan's runs on in parallel, and tapping his phone moves your listening across to wherever
// his story has got to. Nobody has to sit through one story to reach the other, and the
// simultaneity is the whole point of the screen.
App.def("3.3", {
  theme: "dark", walk: true, layout: "split", time: "11:45",
  render({ maya, dan }) {
    Walk.dropped = false;
    Walk.pos = PATHS["3.3"][0].slice();
    const wm = walkScaffold(maya, { where: "federal" });
    const wd = walkScaffold(dan, { levers: false, closeX: false });
    setWalking(wm, true); setWalking(wd, true);
    wd.insertAdjacentHTML("afterbegin", `<div class="whose g-next">${icon("person")}Dan's phone</div>`);
    const fm = C.stories.fh_maya, fd = C.stories.fh_dan;
    const phones = () => document.querySelectorAll(".phone-wrap");
    const card = { maya: wm, dan: wd };
    const story = { maya: fm, dan: fd };
    let t0 = 0, listening = null, ghostTimer = null, lastGhost = -1, ended = false;
    const elapsed = () => (t0 ? (Date.now() - t0) / 1000 : 0);

    // The phone you are NOT listening to keeps moving, so both are visibly running.
    const ghost = (who) => {
      clearInterval(ghostTimer); lastGhost = -1;
      const w = card[who], s = story[who];
      // Re-dress the card: the phone you stopped listening to must still show ITS story
      // running, not fall back to the walking bar.
      mountStory(w, s, who === "maya" ? "Stop 3 of 6" : "Playing on Dan's phone");
      (w.closest(".phone-wrap") || w).classList.add("overhearing");
      ghostTimer = setInterval(() => {
        const p = Math.min(1, elapsed() / s.secs);
        lastGhost = paintProgress(w, s, p, lastGhost);
        if (p >= 1) { clearInterval(ghostTimer); $(".np-stop", w).textContent = "Stop 3 of 6 · finished"; finishIfDone(); }
      }, 150);
    };

    const listen = (who, { silent = false } = {}) => {
      if (listening === who) return;
      const other = who === "maya" ? "dan" : "maya";
      listening = who;
      clearInterval(ghostTimer);
      (card[who].closest(".phone-wrap") || card[who]).classList.remove("overhearing");
      phones().forEach((p) => p.classList.toggle("quiet", p.dataset.phone !== who));
      const at = elapsed();
      if (at >= story[who].secs) { mountStory(card[who], story[who], "Stop 3 of 6 · finished"); paintProgress(card[who], story[who], 1, -1); }
      else playOn(card[who], story[who], who === "maya" ? "Stop 3 of 6" : "Stop 3 of 6 · Dan", () => finishIfDone(), { from: at });
      ghost(other);
      if (!silent) App.record("tap", "switched phones", { to: who, at: Math.round(at) });
    };

    const finishIfDone = () => {
      if (ended) return;
      if (elapsed() < Math.max(fm.secs, fd.secs) || Player.active) return;
      ended = true; clearInterval(ghostTimer);
      App.setClock("12:10"); wait(4000, () => App.next("3.3"));
    };

    // Tapping anywhere on the other phone moves your ears across — not only its play button.
    const wrapOf = (w) => w.closest(".phone-wrap") || w;
    wrapOf(card.dan).onclick = (e) => { if (listening !== "dan" && !e.target.closest(".np-text")) listen("dan"); };
    wrapOf(card.maya).onclick = (e) => { if (listening !== "maya" && !e.target.closest(".np-text")) listen("maya"); };
    wirePlayer(wm, { onNext: () => App.next(), onPrev: () => listen("maya"), onReplay: () => listen("maya") });
    wirePlayer(wd, { onNext: () => App.next(), onPrev: () => listen("dan"), onReplay: () => listen("dan") });
    $(".np-play", wd).onclick = (e) => { e.stopPropagation(); if (listening === "dan" && Player.active) App.togglePause(); else listen("dan"); };
    $(".np-play", wm).onclick = (e) => { e.stopPropagation(); if (listening === "maya" && Player.active) App.togglePause(); else listen("maya"); };
    wireLevers(maya, wm, {
      more: () => { listen("maya", { silent: true }); playOn(wm, C.stories.fh_more, "Stop 3 of 6 · more", () => finishIfDone()); },
    });
    Walk.move(maya, PATHS["3.3"], LEG_MS, () => {
      App.record("system", "arrived", { stop: "Federal Hall" });
      t0 = Date.now();
      mountStory(wd, fd, "Playing on Dan's phone");
      listen("maya", { silent: true });
    });
  },
  leave() { document.querySelectorAll(".phone-wrap").forEach((p) => p.classList.remove("quiet", "overhearing")); },
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
        // v10 "06 · Running late": scrim over the live map, one .sheet, a .dropcard per change.
        maya.insertAdjacentHTML("beforeend", `<div class="replan late"><div class="scrim"></div><div class="sheet">
          <div class="tag"><span class="d"></span>Your day changed</div>
          <h3>${esc(r.title)}</h3>
          <p>${esc(r.body)}</p>
          ${r.changes.map((c) => `<div class="dropcard"><span class="th"${chgThumb(c.was)}></span><div><div class="nm"><span class="was${c.struck ? " struck" : ""}">${esc(c.was)}</span>${icon("arrow_forward")}<b>${esc(c.now)}</b></div><div class="mt">${esc(c.why)}</div></div></div>`).join("")}
          <div id="rp-warn"></div>
          <button class="lp" id="rp-ok" data-log="Re-plan: Sounds good">Sounds good</button>
          <button class="lg" id="rp-keep" data-log="Re-plan: Keep original">Keep original</button>
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
