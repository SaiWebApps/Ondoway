// PROTOTYPE — throwaway. Screens 0.1 – 2.4 (script.md): opening, Lenses, party, planning.

function bottomNav(tab) {
  const tabs = [["explore", "Explore"], ["luggage", "Trips"], ["person", "Profile"]];
  return `<nav class="bnav">${tabs.map(([ic, l]) => `<span class="tab${l === tab ? " on" : ""}" data-log="Bottom nav: ${l}">${icon(ic, l === tab ? "fill" : "")}${l === tab ? l : ""}</span>`).join("")}</nav>`;
}
function photo(src, label, h) {
  return src ? `<img src="${esc(src)}" alt="${esc(label)}" style="width:100%;height:${h}px;object-fit:cover;border-radius:28px">`
    : `<div class="placeholder-img" style="height:${h}px">Photo · ${esc(label)}<br>TODO before pilot</div>`;
}

// ---- 0.1 Meet Maya
App.def("0.1", {
  time: "8:12",
  render({ maya }) {
    maya.insertAdjacentHTML("beforeend", `<div class="scr fade-in" style="display:flex;flex-direction:column;gap:20px;padding-top:66px">
      ${photo(C.photos.family, "a family on a city street", 330)}
      <div class="eyebrow">Ondoway</div>
      <h1 class="h1" style="font-size:38px;margin-top:-10px">Four days in New York.</h1>
      <p class="body" style="margin:0;font-size:16.5px">You're Maya. You're taking Dan, Leo (12) and Ava (8) to New York for four days. You've heard about Ondoway. Let's see what it does.</p>
      <div style="margin-top:auto"><button class="btn block" id="start" data-log="Start">Start</button></div>
    </div>`);
    $("#start", maya).onclick = () => App.next();
  },
});

// ---- 1.1 What are you curious about? (= lens_selection_page.dart)
App.def("1.1", {
  time: "8:13",
  render({ maya }) {
    maya.insertAdjacentHTML("beforeend", `<div class="scr fade-in" id="lens-scr" style="padding-bottom:250px">
      <div class="eyebrow">Welcome, Maya</div>
      <h1 class="h1">What are you curious about?</h1>
      <div class="progress" id="lprog"></div>
      <p class="body" style="margin:10px 0 16px">Pick at least three. Every story you hear leans toward these.</p>
      <div class="chips" id="lchips"></div>
    </div>
    <div class="lens-dock">
      <div class="card fh-preview">
        <div class="eyebrow" style="display:flex;align-items:center;gap:6px">${icon("hearing")}What you'd hear at Federal Hall</div>
        <div id="fhlens" style="margin-top:8px"></div>
        <p id="fhline" class="fh-line"></p>
      </div>
      <div class="lens-foot"><span id="lcount" class="mono" style="font-size:12px;color:var(--inkMute)"></span><button class="btn" id="lcont" data-log="Continue (lenses)">Continue</button></div>
    </div>`);
    const draw = () => {
      const sel = App.st.lenses;
      $("#lchips", maya).innerHTML = C.lenses.map(([id]) => chipHtml(id, { on: sel.has(id), attrs: `data-lens="${id}" data-log="Lens chip" data-detail='{"lens":"${id}","to":"${sel.has(id) ? "off" : "on"}"}'` })).join("");
      $("#lprog", maya).innerHTML = [0, 1, 2].map((i) => `<i class="${i < sel.size ? "on" : ""}"></i>`).join("");
      $("#lcount", maya).textContent = `${sel.size} selected`;
      const cont = $("#lcont", maya); cont.disabled = sel.size < 3; cont.style.opacity = sel.size < 3 ? 0.4 : 1;
      const lens = App.st.lastLens && sel.has(App.st.lastLens) ? App.st.lastLens : [...sel].pop();
      const l = lens ? lensOf(lens) : null;
      $("#fhlens", maya).innerHTML = l ? chipHtml(lens, { sm: true, on: true }) : "";
      const line = !l ? "Pick a lens to hear this place your way." : C.federalHallPreview[lens] || C.federalHallPreview.other.replace("{lens}", l.label);
      const p = $("#fhline", maya); p.textContent = line; p.classList.remove("swap"); void p.offsetWidth; p.classList.add("swap");
      maya.querySelectorAll("[data-lens]").forEach((b) => (b.onclick = () => {
        const id = b.dataset.lens;
        if (sel.has(id)) sel.delete(id); else { sel.add(id); App.st.lastLens = id; }
        const y = $("#lens-scr", maya).scrollTop; draw(); $("#lens-scr", maya).scrollTop = y;
      }));
    };
    draw();
    $("#lcont", maya).onclick = () => App.st.lenses.size >= 3 && App.next();
  },
});

// ---- 1.2 Who's coming?
App.def("1.2", {
  time: "8:15",
  render({ maya }) {
    const rows = C.party.map((p) => {
      const lenses = p.id === "maya" ? [...App.st.lenses] : p.lenses;
      return `<div class="card person" data-log="Party row: ${p.name}">
        <div class="avatar">${p.name[0]}</div>
        <div style="flex:1;min-width:0">
          <div style="display:flex;align-items:baseline;gap:8px;flex-wrap:wrap"><span class="h2" style="font-size:19px">${esc(p.name)}</span><span class="muted" style="font-size:13px">${esc(p.role)}</span></div>
          <div class="chips" style="gap:5px;margin-top:8px">${lenses.map((l) => chipHtml(l, { sm: true })).join("")}${p.ispy ? `<span class="pill" style="background:var(--spark);color:#fff">${icon("visibility", "fill")} I Spy</span>` : ""}</div>
        </div>
        ${p.joined ? `<span class="pill" style="background:#E3EEDF;color:#2F6B34;flex:none">Joined ✓</span>` : ""}
      </div>`;
    }).join("");
    maya.insertAdjacentHTML("beforeend", `<div class="scr fade-in" style="display:flex;flex-direction:column;gap:12px">
      <div class="eyebrow">Your party</div>
      <h1 class="h1" style="margin-bottom:8px">Who's coming?</h1>
      ${rows}
      <div style="margin-top:auto;padding-top:12px"><button class="btn block" id="pcont" data-log="Continue (party)">Continue</button></div>
    </div>`);
    $("#pcont", maya).onclick = () => App.next();
  },
});

// ---- 2.1 Plan your trip (= trip_duration_page.dart, brand tokens)
App.def("2.1", {
  time: "8:17",
  render({ maya }) {
    const p = C.plan;
    maya.insertAdjacentHTML("beforeend", `<div class="scr fade-in" style="padding-bottom:110px;display:flex;flex-direction:column;gap:14px">
      <div class="eyebrow">Plan your trip</div>
      <h1 class="h1" style="margin-bottom:6px">${esc(p.city)}</h1>
      <div class="card row-card"><div class="rlabel">${icon("calendar_month")}Dates</div><div class="rval">${esc(p.dates)}</div></div>
      <div class="card row-card"><div class="rlabel">${icon("star", "fill")}Must-sees</div>
        <div class="chips" style="gap:6px;margin-top:10px">${p.mustSees.map((m) => `<span class="must">${icon("star", "fill")}${esc(m)}</span>`).join("")}<span class="must add" data-log="+ Add must-see">+ Add</span></div></div>
      <div class="card row-card"><div class="rlabel">${icon("confirmation_number")}Booked</div>
        <div class="ticket">${icon("directions_boat")}<span>${esc(p.booked)}</span></div></div>
      <div style="margin-top:auto"><button class="btn block" id="build" data-log="Build my trip">${icon("auto_awesome")}Build my trip</button></div>
    </div>${bottomNav("Trips")}`);
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

// ---- 2.3 Your trip
App.def("2.3", {
  time: "8:18",
  render({ maya }) {
    const cards = C.days.map((d) => `<div class="card day${d.open ? " open" : " locked"}" data-log="Day card: ${d.day}" ${d.open ? 'id="friday"' : ""}>
      <div class="dday">${esc(d.day)}</div>
      <div style="flex:1;min-width:0">
        <div class="h2" style="font-size:19px">${esc(d.area)}</div>
        <div class="dmeta">${d.stars.map((s) => `<span class="star">${icon("star", "fill")}${esc(s)}</span>`).join("")}${d.note ? `<span>${esc(d.note)}</span>` : ""}</div>
        <div class="mono" style="font-size:11px;color:var(--inkMute);margin-top:6px">${d.stops} stops</div>
      </div>
      ${d.open ? icon("chevron_right") : ""}
    </div>`).join("");
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
        <div class="num">${n}</div>
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
