// PROTOTYPE — throwaway. Screens 6.1 – 7.4 (script.md): I Spy, the Day recap, the Trip recap.
// Revised 2026-09-18: 7.2 gets photos (still the calm dinner screen); 7.3 is the showpiece —
// photo cards, stickers, a share-card editor with filters, pretend music and a pretend share.

// ---- 6.1 Ava's phone slides in beside Maya's
App.def("6.1", {
  layout: "kid", walk: true, keepAudio: true, time: "1:40",
  render({ maya, ava }) {
    cameraView(maya, "castle", { leaveBtn: false, mini: true, ask: "castle" });
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
    maya.insertAdjacentHTML("beforeend", `<div class="scr completion behind">
      <div class="photo"></div><div class="sc"></div>
      <div class="htop"><div class="cp-eye">Tonight</div><h1 class="cp-h">Friday, told back</h1></div>
      <div class="cp-sheet"><div class="cp-handle"></div></div></div>
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

// Stand-ins for Maya's own photos, matched to stops (7.2 strip).
const RECAP_SHOTS = ["recap_family_street", "trinity_wide", "federal_wide", "bowling_wide", "recap_family_ferry", "recap_kid_looking"];
function thumb(p, i, allowed) {
  if (allowed) return `<div class="thumb">${pic(RECAP_SHOTS[i], p.stop)}<span>${esc(p.stop)} · ${esc(p.time)}</span></div>`;
  return `<div class="thumb mapthumb"><svg viewBox="0 0 60 60"><rect width="60" height="60" fill="#1A1E26"/><path d="M0 40 L60 22" stroke="#2C6CC0" stroke-width="3"/><circle cx="30" cy="31" r="5" fill="#7BB2F5" stroke="#fff" stroke-width="2"/></svg><span>${esc(p.stop)} · ${esc(p.time)}</span></div>`;
}

// A header stat ("3.1 mi") as a v10 .cp-stat tile: the number, then its unit. Same words.
function statTile(s) {
  const m = String(s).match(/^(\S+)\s+([\s\S]+)$/);
  return `<div class="cp-stat"><div class="v">${esc(m ? m[1] : s)}</div><div class="l">${esc(m ? m[2] : "")}</div></div>`;
}

// ---- 7.2 Friday, told back — the Day recap (calm; the dinner screen)
App.def("7.2", {
  theme: "evening", time: "8:06",
  render({ maya }) {
    const r = C.dayRecap, allowed = App.st.photos !== "denied";
    const asked = App.st.asked.length ? App.st.asked.map((a) => ({ q: a.q, place: a.place }))
      : C.ask.federal.chips.slice(0, 2).map((c) => ({ q: c.q, place: "Federal Hall" }));
    const ispyDone = Object.keys(App.st.ispyFound).length > 0;
    maya.insertAdjacentHTML("beforeend", `<div class="scr completion recap fade-in">
      <div class="photo">${allowed ? pic("recap_family_landmark", "Your day") : ""}</div><div class="sc"></div>
      <div class="htop">
        <div class="cp-eye">Tonight · Day recap</div>
        <h1 class="cp-h">${esc(r.header)}</h1></div>
      <div class="cp-sheet">
        <div class="cp-handle"></div>
        <div class="cp-stats">${r.stats.map(statTile).join("")}</div>
        <div class="strip">${r.photos.map((p, i) => thumb(p, i, allowed)).join("")}</div>
        <section class="same">
          <div class="cp-eye spark">Same spot, four stories</div>
          <h2 class="cp-q">Federal Hall</h2>
          ${r.sameSpot.map((s) => `<div class="who" data-log="Recap row: ${s.who}">
            <div class="avatar sm">${s.who[0]}</div>
            <div style="flex:1;min-width:0"><div class="who-n">${esc(s.who)} ${s.lens ? chipHtml(s.lens, { sm: true, on: true }) : s.ispy ? `<span class="pill" style="background:var(--spark);color:#fff">I Spy</span>` : ""}</div>
            <div class="who-l${s.lens ? "" : " quiet"}">${esc(s.ispy && !ispyDone ? "Ava's I Spy is waiting at the harbor" : s.line)}</div></div></div>`).join("")}
          <div class="askdan">${icon("forum")} <span><b>${esc(r.askPrompt[0])}</b> ${esc(r.askPrompt[1])}</span></div>
        </section>
        <section><div class="eyebrow">You asked</div>
          ${asked.map((a) => `<div class="asked"><span class="muted">${esc(a.place)}</span>“${esc(a.q)}”</div>`).join("")}</section>
        <section><div class="eyebrow">Your echo</div>
          ${App.st.echoLeft ? `<div class="asked">“${esc(App.st.echoLeft)}”<span class="muted">${esc(C.stopNames[App.st.echoLeftAt] || "Castle Clinton")} · ${esc(r.echoFound)}</span></div>` : `<div class="asked muted">You didn't leave one today.</div>`}</section>
        <div class="cp-done"><button class="btn block" id="trip" data-log="See the whole trip">See the whole trip</button></div>
      </div>
    </div>`);
    $("#trip", maya).onclick = () => App.next();
  },
});

// ---- 7.3 Your trip, the whole thing — the showpiece
const TRIP_BG = ["recap_family_street", "recap_family_landmark", "federal_wide", "castle_wide"];
function stickerHtml(s, extra = "") {
  const text = s.fromEcho ? (App.st.echoLeft ? `My echo: ${App.st.echoLeft}` : null) : s.text;
  return text ? `<span class="sticker s-${s.id}" data-sid="${s.id}" ${extra}>${esc(text)}</span>` : "";
}
App.def("7.3", {
  theme: "evening", time: "Sun 9:14",
  render({ maya }) {
    const cards = C.tripRecap;
    const ed = { filter: "original", placed: [], music: null, since: 0 };
    let i = 0;
    const draw = () => {
      maya.querySelector(".tr")?.remove();
      const c = cards[i];
      if (c.share) return drawEditor();
      const stickers = C.stickers.filter((s) => s.card === i + 1).map((s, k) => stickerHtml(s, `style="animation-delay:${0.35 + k * 0.25}s"`)).join("");
      maya.insertAdjacentHTML("beforeend", `<div class="tr tr${i}" data-log="Trip recap card ${i + 1}">
        <div class="tr-bg">${pic(TRIP_BG[i], "Trip photo")}</div>
        <div class="tr-dots">${cards.map((_, k) => `<i class="${k <= i ? "on" : ""}"></i>`).join("")}</div>
        <div class="tr-stickers">${stickers}</div>
        <div class="tr-text"><div class="tr-big">${esc(c.big)}</div>${c.small ? `<div class="tr-small">${esc(c.small)}</div>` : ""}</div>
        <div class="tr-tap mono">Tap to continue</div>
      </div>`);
      maya.querySelector(".tr").onclick = () => { i++; draw(); };
    };
    // Card 5: the share card as a small editor.
    const drawEditor = () => {
      if (!ed.since) { ed.since = Date.now(); if (!ed.placed.length) ed.placed = [{ id: "parthenon", x: 12, y: 8 }, { id: "miles", x: 58, y: 70 }]; }
      const f = C.filters.find((x) => x.id === ed.filter);
      maya.insertAdjacentHTML("beforeend", `<div class="tr tr4 editor" data-log="Trip recap card 5">
        <div class="tr-dots">${cards.map(() => `<i class="on"></i>`).join("")}</div>
        <div class="collage-wrap">
          <div class="collage" style="filter:${f.css}">${["recap_family_ferry", "recap_family_landmark", "recap_kid_looking", "recap_family_street"].map((sl) => `<div>${pic(sl, "Trip photo")}</div>`).join("")}</div>
          <div class="collage-title">New York · Ondoway</div>
          ${ed.placed.map((p, k) => { const s = C.stickers.find((x) => x.id === p.id); return stickerHtml(s, `data-k="${k}" style="left:${p.x}%;top:${p.y}%"`); }).join("")}
          ${ed.music ? `<span class="music-chip">${icon("music_note", "fill")}Your song</span>` : ""}
        </div>
        <div class="filters">${C.filters.map((x) => `<button class="fchip${x.id === ed.filter ? " on" : ""}" data-f="${x.id}" data-log="Filter: ${x.label}">${esc(x.label)}</button>`).join("")}</div>
        <div class="tray">${C.stickers.map((s) => stickerHtml(s, `data-add="${s.id}" data-log="Sticker added" data-detail='{"sticker":"${s.id}"}'`)).join("")}<button class="music-btn" data-log="Add music">${icon("music_note")}Add music</button></div>
        <div class="tr-act"><button class="btn" id="share" data-log="Trip recap: Share">${icon("ios_share")}Share</button><button class="btn quiet" id="keep" data-log="Trip recap: Keep it (keepsake)">${icon("bookmark")}Keep it</button></div>
        <div class="tr-small">Tap a sticker to add it · drag to move · tap it again to remove · Keep it: make it a keepsake</div>
      </div>`);
      const card = maya.querySelector(".tr");
      card.querySelectorAll("[data-f]").forEach((b) => (b.onclick = () => { ed.filter = b.dataset.f; redraw(); }));
      card.querySelectorAll("[data-add]").forEach((b) => (b.onclick = () => { ed.placed.push({ id: b.dataset.add, x: 18 + Math.random() * 40, y: 20 + Math.random() * 45 }); redraw(); }));
      card.querySelector(".music-btn").onclick = () => musicSheet(maya, () => { ed.music = true; redraw(); });
      $("#share", card).onclick = () => { App.record("system", "recap editing time", { secs: Math.round((Date.now() - ed.since) / 1000), stickers: ed.placed.map((p) => p.id), filter: ed.filter }); shareSheet(maya, ed); };
      $("#keep", card).onclick = () => toast(maya, "Kept. In the real app this becomes a keepsake.");
      dragStickers(card, ed, redraw);
    };
    const redraw = () => { maya.querySelector(".tr")?.remove(); drawEditor(); };
    draw();
  },
});

// Drag placed stickers inside the collage; a tap without a drag removes one.
function dragStickers(card, ed, redraw) {
  const wrap = card.querySelector(".collage-wrap");
  wrap.querySelectorAll(".sticker[data-k]").forEach((el) => {
    el.onpointerdown = (e) => {
      e.preventDefault();
      const k = +el.dataset.k, r = wrap.getBoundingClientRect(), start = { x: e.clientX, y: e.clientY }; let moved = false;
      const move = (ev) => {
        if (Math.hypot(ev.clientX - start.x, ev.clientY - start.y) > 4) moved = true;
        ed.placed[k].x = Math.max(0, Math.min(80, ((ev.clientX - r.left) / r.width) * 100 - 10));
        ed.placed[k].y = Math.max(0, Math.min(90, ((ev.clientY - r.top) / r.height) * 100 - 4));
        el.style.left = ed.placed[k].x + "%"; el.style.top = ed.placed[k].y + "%";
      };
      const up = () => {
        window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up);
        if (!moved) { App.record("tap", "Sticker removed", { sticker: ed.placed[k].id }); ed.placed.splice(k, 1); redraw(); }
        else App.record("tap", "Sticker moved", { sticker: ed.placed[k].id });
      };
      window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
    };
  });
}
function musicSheet(el, onPick) {
  const s = sheet(el, `<div class="late-sheet"><h2 class="h2">Add a song from your library</h2>
    <p class="body">${esc(C.musicNote)}</p>
    <div class="sharegrid">${C.musicServices.map((m) => `<button class="shareopt" data-m="${esc(m)}" data-log="Music from: ${esc(m)}">${icon("library_music")}${esc(m)}</button>`).join("")}</div></div>`);
  s.scrim.querySelectorAll("[data-m]").forEach((b) => (b.onclick = () => { s.close(); onPick(); }));
}
function shareSheet(el, ed) {
  const s = sheet(el, `<div class="late-sheet"><h2 class="h2">Share your trip</h2>
    <div class="sharegrid">${C.shareOptions.map((o, i) => `<button class="shareopt${i === 0 ? " party" : ""}" data-o="${esc(o)}" data-log="Share to: ${esc(o)}">${icon(i === 0 ? "group" : o === "Copy link" ? "link" : "send")}${esc(o)}</button>`).join("")}</div></div>`);
  s.scrim.querySelectorAll("[data-o]").forEach((b) => (b.onclick = () => {
    const party = b.dataset.o === "Send to your party";
    const f = C.filters.find((x) => x.id === ed.filter);
    s.close();
    el.insertAdjacentHTML("beforeend", `<div class="story-scrim"><div class="story">
      <div class="story-top">${esc(party ? "Your party" : b.dataset.o)}</div>
      <div class="collage" style="filter:${f.css}">${["recap_family_ferry", "recap_family_landmark", "recap_kid_looking", "recap_family_street"].map((sl) => `<div>${pic(sl, "Trip photo")}</div>`).join("")}</div>
      ${ed.placed.map((p) => { const st = C.stickers.find((x) => x.id === p.id); return stickerHtml(st, `style="left:${p.x}%;top:${p.y * 0.6 + 8}%"`); }).join("")}
      <div class="story-mark">made with Ondoway</div>
      ${party ? `<div class="story-party">${["D", "L", "A"].map((n, k) => `<span class="av" style="animation-delay:${0.3 + k * 0.35}s">${n}<i>✓</i></span>`).join("")}</div>` : ""}
      <div class="story-done">${esc(party ? C.sharePartyDone : C.shareDone)}</div>
      <button class="btn block" id="story-ok" data-log="Share preview: Done">Done</button>
    </div></div>`);
    $("#story-ok", el).onclick = () => el.querySelector(".story-scrim").remove();
  }));
}

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
