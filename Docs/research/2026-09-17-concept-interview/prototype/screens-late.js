// PROTOTYPE — throwaway. Screens 6.1 – 7.4 (script.md): I Spy, the Day recap, the Trip recap.

// ---- 6.1 Ava's phone slides in beside Maya's
App.def("6.1", {
  layout: "kid", walk: true, keepAudio: true, time: "1:40",
  render({ maya, ava }) {
    cameraView(maya, { leaveBtn: false });
    const is = C.ispy;
    const finds = App.st.cuts.ispy1 ? is.finds.slice(0, 1) : is.finds;
    ava.classList.add("kid-screen");
    ava.insertAdjacentHTML("beforeend", `<div class="scr fade-in" style="display:flex;flex-direction:column;gap:14px">
      <div class="kid-head">${icon("visibility", "fill")}<span>I Spy</span></div>
      <h1 class="h1 kid-title">${esc(is.title.replace(/^I Spy — /, ""))}</h1>
      ${finds.map((f, i) => `<div class="card find${App.st.ispyFound[i] ? " found" : ""}" data-f="${i}">
        ${f.todo ? `<span class="todo" style="align-self:flex-start">TODO ✎ ${f.placeholder ? "write" : "review"}</span>` : ""}
        <p class="kid-q">${esc(f.prompt)}</p>
        <div class="kid-a">${esc(f.answer)}</div>
        <button class="kid-btn" data-log="I Spy: Found it! (${i + 1})">${icon("check_circle", "fill")}Found it!</button>
        <div class="stamp">FOUND</div>
      </div>`).join("")}
    </div>`);
    ava.querySelectorAll(".find").forEach((card) => ($(".kid-btn", card).onclick = () => {
      App.st.ispyFound[+card.dataset.f] = true; card.classList.add("found");
    }));
  },
  leave() { DeepDive.view = null; },
});

// ---- 7.1 Photos? (iOS-style permission)
App.def("7.1", {
  theme: "evening", time: "8:05",
  render({ maya }) {
    maya.insertAdjacentHTML("beforeend", `<div class="scr" style="filter:blur(3px);opacity:.5">
      <div class="eyebrow">Tonight</div><h1 class="h1" style="color:var(--dInk)">Friday, told back</h1></div>
      <div class="ios-scrim"><div class="ios-alert">
        <div class="ios-body"><div class="ios-ic">${icon("photo_library", "fill")}</div>
          <b>“Ondoway” would like to look at photos from today</b><p>Ondoway would like to look at photos from today to build your recap.</p></div>
        <button id="ph-yes" data-log="Photos: Allow photos from today">Allow photos from today</button>
        <button id="ph-no" data-log="Photos: Don't allow">Don't allow</button>
      </div></div>`);
    $("#ph-yes", maya).onclick = () => { App.st.photos = "allowed"; App.next(); };
    $("#ph-no", maya).onclick = () => { App.st.photos = "denied"; App.next(); };
  },
});

// ---- 7.2 Friday, told back — the Day recap
function thumb(p, allowed) {
  if (allowed) return `<div class="thumb placeholder-img">${esc(p.stop)}<br>${esc(p.time)}<br><span style="opacity:.7">photo TODO</span></div>`;
  return `<div class="thumb mapthumb"><svg viewBox="0 0 60 60"><rect width="60" height="60" fill="#1A1E26"/><path d="M0 40 L60 22" stroke="#2C6CC0" stroke-width="3"/><circle cx="30" cy="31" r="5" fill="#7BB2F5" stroke="#fff" stroke-width="2"/></svg><span>${esc(p.stop)} · ${esc(p.time)}</span></div>`;
}
App.def("7.2", {
  theme: "evening", time: "8:06",
  render({ maya }) {
    const r = C.dayRecap, allowed = App.st.photos !== "denied";
    const asked = App.st.asked.length ? App.st.asked.map((a) => ({ q: a.q, place: a.place }))
      : C.ask.federal.chips.slice(0, 2).map((c) => ({ q: c.q, place: "Federal Hall" }));
    const ispyDone = Object.keys(App.st.ispyFound).length > 0;
    maya.insertAdjacentHTML("beforeend", `<div class="scr recap fade-in">
      <div class="eyebrow">Tonight · Day recap</div>
      <h1 class="h1" style="color:var(--dInk)">${esc(r.header)}</h1>
      <div class="rstats mono">${r.stats.map((s) => `<span>${esc(s)}</span>`).join("")}</div>
      <div class="strip">${r.photos.map((p) => thumb(p, allowed)).join("")}</div>
      <section class="same">
        <div class="eyebrow" style="color:var(--spark)">Same spot, four stories</div>
        <h2 class="h2" style="color:var(--dInk);margin-top:4px">Federal Hall</h2>
        ${r.sameSpot.map((s) => `<div class="who" data-log="Recap row: ${s.who}">
          <div class="avatar sm">${s.who[0]}</div>
          <div style="flex:1;min-width:0"><div class="who-n">${esc(s.who)} ${s.lens ? chipHtml(s.lens, { sm: true, on: true }) : s.ispy ? `<span class="pill" style="background:var(--spark);color:#fff">I Spy</span>` : ""}</div>
          <div class="who-l${s.lens ? "" : " quiet"}">${esc(s.ispy && !ispyDone ? "Ava's I Spy is waiting at the harbor" : s.line)}</div></div></div>`).join("")}
        <div class="askdan">${icon("forum")} <span><b>${esc(r.askPrompt[0])}</b> ${esc(r.askPrompt[1])}</span></div>
      </section>
      <section><div class="eyebrow">You asked</div>
        ${asked.map((a) => `<div class="asked"><span class="muted">${esc(a.place)}</span>“${esc(a.q)}”</div>`).join("")}</section>
      <section><div class="eyebrow">Your echo</div>
        ${App.st.echoLeft ? `<div class="asked">“${esc(App.st.echoLeft)}”<span class="muted">Castle Clinton · ${esc(r.echoFound)}</span></div>` : `<div class="asked muted">You didn't leave one today.</div>`}</section>
      <button class="btn block" id="trip" data-log="See the whole trip" style="margin-top:8px">See the whole trip</button>
    </div>`);
    $("#trip", maya).onclick = () => App.next();
  },
});

// ---- 7.3 Your trip, the whole thing — five full-screen cards, tap to advance
App.def("7.3", {
  theme: "evening", time: "Sun 9:14",
  render({ maya }) {
    const cards = C.tripRecap;
    let i = 0;
    const draw = () => {
      maya.querySelector(".tr")?.remove();
      const c = cards[i];
      maya.insertAdjacentHTML("beforeend", `<div class="tr tr${i}" data-log="Trip recap card ${i + 1}">
        <div class="tr-dots">${cards.map((_, k) => `<i class="${k <= i ? "on" : ""}"></i>`).join("")}</div>
        ${c.share ? `<div class="collage">${C.dayRecap.photos.slice(0, 4).map((p) => `<div class="placeholder-img">${esc(p.stop)}</div>`).join("")}</div>
          <div class="tr-big">${esc(c.big)}</div>
          <div class="tr-act"><button class="btn" data-log="Trip recap: Share">${icon("ios_share")}Share</button><button class="btn quiet" data-log="Trip recap: Keep it (keepsake)">${icon("bookmark")}Keep it</button></div>
          <div class="tr-small">Make it a keepsake</div>`
        : `<div class="tr-big">${esc(c.big)}</div>${c.small ? `<div class="tr-small">${esc(c.small)}</div>` : ""}<div class="tr-tap mono">Tap to continue</div>`}
      </div>`);
      const el = maya.querySelector(".tr");
      el.onclick = (e) => { if (e.target.closest("button")) return; if (i < cards.length - 1) { i++; draw(); } };
    };
    draw();
  },
});

// ---- 7.4 End
App.def("7.4", {
  time: "Sun 9:15",
  render({ maya }) {
    maya.insertAdjacentHTML("beforeend", `<div class="scr fade-in" style="display:flex;flex-direction:column;justify-content:center;gap:18px">
      <div class="eyebrow">Ondoway</div>
      <h1 class="h1" style="font-size:40px">That's the whole thing.</h1>
      <button class="btn block" id="bye" data-log="Back to the conversation">Back to the conversation</button>
      <p class="body" id="byemsg" hidden>Thanks. You can stop sharing your screen now.</p>
    </div>`);
    $("#bye", maya).onclick = () => { $("#byemsg", maya).hidden = false; Sync.send({ type: "done" }); };
  },
});
