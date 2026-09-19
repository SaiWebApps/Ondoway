// PROTOTYPE — throwaway. Screens 0.1 – 2.4 (script.md): opening, Lenses, party, planning.
// Revised 2026-09-18: lens explanations, party how/why + invite, add a must-see, photos.

function bottomNav(tab) {
  const tabs = [["explore", "Explore"], ["luggage", "Trips"], ["person", "Profile"]];
  return `<nav class="bnav">${tabs.map(([ic, l]) => `<span class="tab${l === tab ? " on" : ""}" data-log="Bottom nav: ${l}">${icon(ic, l === tab ? "fill" : "")}${l === tab ? l : ""}</span>`).join("")}</nav>`;
}
const STOP_IMG = { "9/11 Memorial": "memorial_wide", "Trinity Church": "trinity_wide", "Federal Hall": "federal_wide", "Fraunces Tavern": "fraunces_thumb", "Castle Clinton": "castle_wide", "Statue of Liberty": "liberty" };
const DAY_IMG = { Thu: "day_thu", Fri: "federal_wide", Sat: "day_sat", Sun: "day_sun" };

// ---- 0.1 Meet Maya
App.def("0.1", {
  time: "8:12",
  render({ maya }) {
    maya.insertAdjacentHTML("beforeend", `<div class="scr fade-in" style="display:flex;flex-direction:column;gap:20px;padding-top:66px">
      ${pic("hero_family", "A family on a city street", { cls: "hero" })}
      <div class="eyebrow">Ondoway</div>
      <h1 class="h1" style="font-size:38px;margin-top:-10px">Four days in New York.</h1>
      <p class="body" style="margin:0;font-size:16.5px">You're Maya. You're taking Dan, Leo (12) and Ava (8) to New York for four days. You've heard about Ondoway. Let's see what it does.</p>
      <div style="margin-top:auto"><button class="btn block" id="start" data-log="Start">Start</button></div>
    </div>`);
    $("#start", maya).onclick = () => App.next();
  },
});

// ---- 1.1 What are you curious about? (= lens_selection_page.dart) + lens explanation dock
App.def("1.1", {
  time: "8:13",
  render({ maya }) {
    maya.insertAdjacentHTML("beforeend", `<div class="scr fade-in" id="lens-scr" style="padding-bottom:210px">
      <div class="eyebrow">Welcome, Maya</div>
      <h1 class="h1">What are you curious about?</h1>
      <div class="progress" id="lprog"></div>
      <p class="body" style="margin:10px 0 16px">Pick at least three. Every story you hear leans toward these.</p>
      <div class="chips" id="lchips"></div>
    </div>
    <div class="lens-dock">
      <div class="card fh-preview" id="linfo"></div>
      <div class="lens-foot"><span id="lcount" class="mono" style="font-size:12px;color:var(--inkMute)"></span><button class="btn" id="lcont" data-log="Continue (lenses)">Continue</button></div>
    </div>`);
    const draw = () => {
      const sel = App.st.lenses;
      $("#lchips", maya).innerHTML = C.lenses.map(([id]) => chipHtml(id, { on: sel.has(id), attrs: `data-lens="${id}" data-log="Lens chip" data-detail='{"lens":"${id}","to":"${sel.has(id) ? "off" : "on"}"}'` })).join("");
      $("#lprog", maya).innerHTML = [0, 1, 2].map((i) => `<i class="${i < sel.size ? "on" : ""}"></i>`).join("");
      $("#lcount", maya).textContent = `${sel.size} selected`;
      const cont = $("#lcont", maya); cont.disabled = sel.size < 3; cont.style.opacity = sel.size < 3 ? 0.4 : 1;
      // The dock explains the most recently tapped lens, whether it was turned on or off.
      const l = lensOf(App.st.lastLens);
      const box = $("#linfo", maya);
      box.innerHTML = `<div style="display:flex;align-items:center;gap:8px">${chipHtml(l.id, { sm: true, on: sel.has(l.id) })}<span class="mono" style="font-size:10px;color:var(--inkMute)">${sel.has(l.id) ? "selected" : "not selected"}</span></div>
        <p class="lens-info">${esc(C.lensInfo[l.id])}</p>`;
      box.classList.remove("swap"); void box.offsetWidth; box.classList.add("swap");
      maya.querySelectorAll("[data-lens]").forEach((b) => (b.onclick = () => {
        const id = b.dataset.lens;
        if (sel.has(id)) sel.delete(id); else sel.add(id);
        App.st.lastLens = id;
        const y = $("#lens-scr", maya).scrollTop; draw(); $("#lens-scr", maya).scrollTop = y;
      }));
    };
    draw();
    $("#lcont", maya).onclick = () => App.st.lenses.size >= 3 && App.next();
  },
});

// ---- 1.2 Who's coming? — how people join, why it matters, a pretend invite
App.def("1.2", {
  time: "8:15",
  render({ maya }) {
    const rows = C.party.map((p) => {
      const lenses = p.id === "maya" ? [...App.st.lenses] : p.lenses;
      const status = p.id === "maya" ? "You · the Planner" : C.partyStatus[p.id];
      return `<div class="card person" data-log="Party row: ${p.name}">
        <div class="avatar">${p.name[0]}</div>
        <div style="flex:1;min-width:0">
          <div style="display:flex;align-items:baseline;gap:8px;flex-wrap:wrap"><span class="h2" style="font-size:19px">${esc(p.name)}</span>${p.age ? `<span class="muted" style="font-size:13px">${p.age}</span>` : ""}</div>
          <div class="pstatus${p.id === "maya" ? "" : " ok"}">${p.id === "maya" ? "" : icon("check_circle", "fill")}${esc(status)}</div>
          <div class="chips" style="gap:5px;margin-top:8px">${lenses.map((l) => chipHtml(l, { sm: true })).join("")}${p.ispy ? `<span class="pill" style="background:var(--spark);color:#fff">${icon("visibility", "fill")} I Spy</span>` : ""}</div>
        </div>
      </div>`;
    }).join("");
    maya.insertAdjacentHTML("beforeend", `<div class="scr fade-in" style="display:flex;flex-direction:column;gap:10px">
      <div class="eyebrow">Your party</div>
      <h1 class="h1">Who's coming?</h1>
      <p class="body" style="margin:0 0 4px">${esc(C.partyHow)}</p>
      ${rows}
      <button class="invite" id="invite" data-log="+ Invite someone">${icon("person_add")}Invite someone</button>
      <div class="why">${icon("headphones")}<span>${esc(C.partyWhy)}</span></div>
      <div style="margin-top:auto;padding-top:8px"><button class="btn block" id="pcont" data-log="Continue (party)">Continue</button></div>
    </div>`);
    $("#pcont", maya).onclick = () => App.next();
    $("#invite", maya).onclick = () => {
      const s = sheet(maya, `<h2 class="h2">Invite to your New York trip</h2>
        <p class="body">They'll get a link, join on their own phone, and pick their own lenses.</p>
        <div class="sharegrid">${C.inviteVia.map((v) => `<button class="shareopt" data-via="${esc(v)}" data-log="Invite via: ${esc(v)}">${icon(v === "Copy link" ? "link" : "chat")}${esc(v)}</button>`).join("")}</div>
        <div id="invdone"></div>`);
      s.scrim.querySelectorAll("[data-via]").forEach((b) => (b.onclick = () => {
        $("#invdone", s.scrim).innerHTML = `<div class="done-note">${icon("check_circle", "fill")}${esc(C.inviteDone)}</div>`;
        setTimeout(() => s.close(), 1800);
      }));
    };
  },
});

// ---- 2.1 Plan your trip (= trip_duration_page.dart) + add a must-see
App.def("2.1", {
  time: "8:17",
  render({ maya }) {
    const p = C.plan;
    const drawMust = () => {
      $("#musts", maya).innerHTML = [...p.mustSees.map((m) => ({ name: m })), ...App.st.addedMustSees].map((m) =>
        `<span class="must${m.fresh ? " fresh" : ""}">${icon("star", "fill")}${esc(m.name)}</span>`).join("") +
        `<button class="must add" id="addmust" data-log="+ Add must-see">${icon("add")}Add</button>`;
      App.st.addedMustSees.forEach((m) => (m.fresh = false));
      $("#addmust", maya).onclick = openAdd;
    };
    const openAdd = () => {
      const s = sheet(maya, `<h2 class="h2">Add a must-see</h2>
        <p class="body" style="margin:4px 0 10px">Tell us what you already want to see. We'll fit it into the right day.</p>
        <form class="askf" id="msf"><input id="msq" autocomplete="off" placeholder="Search New York…"></form>
        <div id="msl" class="mslist"></div>`);
      const list = $("#msl", s.scrim), q = $("#msq", s.scrim);
      const draw = () => {
        const t = q.value.trim().toLowerCase();
        const taken = new Set(App.st.addedMustSees.map((m) => m.name));
        const hits = C.mustSeeOptions.filter((o) => !taken.has(o.name) && (!t || o.name.toLowerCase().includes(t)));
        list.innerHTML = hits.length ? hits.map((o) => `<button class="msrow" data-name="${esc(o.name)}" data-log="Must-see added" data-detail='${JSON.stringify({ name: o.name }).replace(/'/g, "&#39;")}'>${icon("location_on")}<span>${esc(o.name)}</span>${icon("add_circle")}</button>`).join("")
          : `<p class="body muted">${esc(C.mustSeeNotFound)}</p>` + C.mustSeeOptions.filter((o) => !taken.has(o.name)).slice(0, 3).map((o) => `<button class="msrow" data-name="${esc(o.name)}" data-log="Must-see added" data-detail='${JSON.stringify({ name: o.name }).replace(/'/g, "&#39;")}'>${icon("location_on")}<span>${esc(o.name)}</span>${icon("add_circle")}</button>`).join("");
        list.querySelectorAll("[data-name]").forEach((b) => (b.onclick = () => {
          const o = C.mustSeeOptions.find((x) => x.name === b.dataset.name);
          App.st.addedMustSees.push({ ...o, fresh: true });
          s.close(); drawMust();
        }));
      };
      let typing;
      q.oninput = () => { draw(); clearTimeout(typing); typing = setTimeout(() => q.value.trim() && App.record("must-see-search", q.value.trim()), 700); };
      $("#msf", s.scrim).onsubmit = (e) => e.preventDefault();
      draw();
    };
    maya.insertAdjacentHTML("beforeend", `<div class="scr fade-in" style="padding-bottom:110px;display:flex;flex-direction:column;gap:14px">
      <div class="eyebrow">Plan your trip</div>
      <h1 class="h1" style="margin-bottom:6px">${esc(p.city)}</h1>
      <div class="card row-card"><div class="rlabel">${icon("calendar_month")}Dates</div><div class="rval">${esc(p.dates)}</div></div>
      <div class="card row-card"><div class="rlabel">${icon("star", "fill")}Must-sees</div>
        <div class="chips" style="gap:6px;margin-top:10px" id="musts"></div></div>
      <div class="card row-card"><div class="rlabel">${icon("confirmation_number")}Booked</div>
        <div class="ticket">${icon("directions_boat")}<span>${esc(p.booked)}</span></div></div>
      <div style="margin-top:auto"><button class="btn block" id="build" data-log="Build my trip">${icon("auto_awesome")}Build my trip</button></div>
    </div>${bottomNav("Trips")}`);
    drawMust();
    $("#build", maya).onclick = () => App.next();
  },
});

// ---- 2.2 Building (auto-advances)
App.def("2.2", {
  time: "8:17",
  render({ maya }) {
    maya.insertAdjacentHTML("beforeend", `<div class="scr" style="display:flex;align-items:center">
      <div class="card" style="width:100%;padding:26px 22px">
        <div class="spinner"></div>
        <div id="blines" style="display:flex;flex-direction:column;gap:12px;margin-top:22px"></div>
      </div></div>`);
    const box = $("#blines", maya), id = App.id;
    C.building.forEach((line, i) => setTimeout(() => {
      if (App.id !== id) return;
      box.insertAdjacentHTML("beforeend", `<div class="bline">${icon("check_circle", "fill")}<span>${esc(line)}</span></div>`);
    }, 350 + i * 480));
    setTimeout(() => App.next(id), 2600);
  },
});

// ---- 2.3 Your trip — added must-sees appear on their day, with why
App.def("2.3", {
  time: "8:18",
  render({ maya }) {
    const cards = C.days.map((d) => {
      const added = App.st.addedMustSees.filter((m) => m.day === d.day);
      return `<div class="card day${d.open ? " open" : " locked"}" data-log="Day card: ${d.day}" ${d.open ? 'id="friday"' : ""}>
        <div class="dthumb">${pic(DAY_IMG[d.day], d.area)}<span class="dday">${esc(d.day)}</span></div>
        <div style="flex:1;min-width:0">
          <div class="h2" style="font-size:18px">${esc(d.area)}</div>
          <div class="dmeta">${d.stars.map((s) => `<span class="star">${icon("star", "fill")}${esc(s)}</span>`).join("")}${added.map((m) => `<span class="star added">${icon("star", "fill")}${esc(m.name)}</span>`).join("")}${d.note ? `<span>${esc(d.note)}</span>` : ""}</div>
          ${added.map((m) => `<div class="addwhy">${esc(m.why)}</div>`).join("")}
          <div class="mono" style="font-size:11px;color:var(--inkMute);margin-top:6px">${d.stops + added.filter((m) => !m.already).length} stops</div>
        </div>
        ${d.open ? icon("chevron_right") : ""}
      </div>`;
    }).join("");
    maya.insertAdjacentHTML("beforeend", `<div class="scr fade-in" style="padding-bottom:110px;display:flex;flex-direction:column;gap:12px">
      <div class="eyebrow">New York · 4 days</div>
      <h1 class="h1" style="margin-bottom:8px">Your trip</h1>
      ${cards}
    </div>${bottomNav("Trips")}`);
    $("#friday", maya).onclick = () => App.next();
    maya.querySelectorAll(".day.locked").forEach((c) => (c.onclick = () => { c.classList.remove("nudge"); void c.offsetWidth; c.classList.add("nudge"); }));
  },
});

// ---- 2.4 Friday (= trip_itinerary_page.dart) with Stop reasons and Skip advice
App.def("2.4", {
  time: "8:18",
  render({ maya }) {
    const f = C.friday, sk = C.skipAdvice;
    let n = 0;
    const stops = f.stops.map((s) => {
      if (s.skipAdvice) return `<div class="skip" data-log="Skip advice card">
        <div class="h2" style="font-size:18px;display:flex;align-items:center;gap:8px">${icon("do_not_disturb_on")}${esc(sk.heading)} ${sk.todo ? '<span class="todo">TODO ✎</span>' : ""}</div>
        <p class="body" style="margin:8px 0 0;font-size:14.5px">${esc(sk.body)}</p></div>`;
      n++;
      const l = lensOf(s.lens);
      return `<div class="card stop${s.must ? " must-stop" : ""}" data-log="Stop card: ${s.name}">
        <div class="sthumb">${pic(STOP_IMG[s.name], s.name)}<span class="num">${n}</span></div>
        <div style="flex:1;min-width:0">
          <div style="display:flex;align-items:center;gap:6px"><span class="sname">${esc(s.name)}</span>${s.must ? `<span class="ms fill" style="color:var(--spark);font-size:18px">star</span>` : ""}</div>
          ${s.reason ? `<div class="reason">${esc(s.reason)}</div>` : s.must ? `<div class="reason plain">Your must-see${s.ferry ? " · ferry booked" : ""}</div>` : ""}
          <div class="smeta"><span class="pill" style="background:color-mix(in srgb, ${l.color} 16%, transparent);color:${l.color}">${icon(l.icon)}${esc(l.label)}</span><span>${s.mins} min</span></div>
        </div>
        <div class="stime mono">${esc(s.time)}</div>
      </div>`;
    }).join("");
    maya.insertAdjacentHTML("beforeend", `<div class="scr fade-in" style="padding-bottom:120px;display:flex;flex-direction:column;gap:10px">
      <div class="eyebrow">Your tour</div>
      <h1 class="h1" style="margin-bottom:6px">Friday · Lower Manhattan</h1>
      <div class="stats">${f.stats.map(([k, v]) => `<div><div class="sv">${esc(v)}</div><div class="sk mono">${esc(k)}</div></div>`).join("")}</div>
      ${stops}
    </div>
    <button class="btn fab" id="walk" data-log="Start walking">${icon("directions_walk")}Start walking</button>`);
    $("#walk", maya).onclick = () => App.next();
  },
});
