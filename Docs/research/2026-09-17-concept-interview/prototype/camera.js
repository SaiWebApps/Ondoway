// PROTOTYPE — throwaway. The camera view for Echoes (script.md, revised 2026-09-18).
// A wide photo pans slowly behind a portrait viewfinder, as if the phone were moving; Echoes
// are pinned to points IN the photo so they drift with the scene. Drag to look around; the
// auto-pan resumes after a few seconds. Opens at any stop (camera button) and in 5.3/5.4/6.1.
// Re-skinned 2026-09-22 to the Ondoway v10 design system (v10.css + skin-line.css): v10 has
// no camera screen, so the chrome borrows the live-guide pills and player and the tour-preview
// chips; the builder borrows the preview sheet's card, chips and quote rule. Copy, ids,
// data-log/data-detail, wiring and timings are untouched — re-skin only.

const castleEchoState = C.echoes.map((e) => ({ ...e, counts: { ...e.counts } }));
const stopEchoState = Object.fromEntries(Object.entries(C.stopEchoes).map(([k, list]) => [k, list.map((e) => ({ ...e, counts: { ...e.counts } }))]));
const echoesAt = (stop) => (stop === "castle" ? castleEchoState : stopEchoState[stop] || []);
const echoCount = (stop) => echoesAt(stop).length + (App.st.echoesLeft[stop] ? 1 : 0);

function reactionsLine(c) {
  return [["found", "Found it"], ["worth", "Worth it"], ["ha", "Ha!"]].filter(([k]) => c[k]).map(([k, l]) => `${l} ${c[k]}`).join(" · ") || "New";
}

// Placeholder scenes until the photos land: the harbor for Castle Clinton, a street for the rest.
function sceneSvg(stop) {
  const name = C.stopNames[stop] || "";
  if (stop === "castle") return `<svg class="pano-img" viewBox="0 0 960 400" preserveAspectRatio="none" aria-label="Camera placeholder">
    <defs><linearGradient id="sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#9DC3E6"/><stop offset="1" stop-color="#DCE9F2"/></linearGradient></defs>
    <rect width="960" height="200" fill="url(#sky)"/><rect y="195" width="960" height="205" fill="#5E7F95"/>
    <g fill="#6E8A7B"><rect x="640" y="176" width="120" height="22"/><rect x="650" y="160" width="6" height="18"/><rect x="680" y="156" width="6" height="22"/><rect x="706" y="156" width="6" height="22"/><rect x="740" y="160" width="6" height="18"/></g>
    <g fill="#6E9C8A"><rect x="410" y="168" width="18" height="30"/><path d="M414 168 L419 120 L424 168Z"/><circle cx="419" cy="117" r="5"/><rect x="425" y="100" width="3" height="22"/></g>
    <path d="M0 400 L0 290 Q480 250 960 290 L960 400Z" fill="#8A7B68"/><path d="M0 300 Q480 262 960 300" stroke="#6D604F" stroke-width="8" fill="none"/>
    <text x="480" y="360" text-anchor="middle" fill="#F6F4F0" font-family="Space Mono, monospace" font-size="12" letter-spacing="2">CAMERA · CASTLE CLINTON · PHOTO TODO</text></svg>`;
  return `<svg class="pano-img" viewBox="0 0 960 400" preserveAspectRatio="none" aria-label="Camera placeholder">
    <rect width="960" height="400" fill="#B9C6D0"/>
    <g fill="#8C8F95">${[0, 1, 2, 3, 4, 5, 6, 7].map((i) => `<rect x="${i * 125}" y="${60 + (i % 3) * 30}" width="${100 + (i % 2) * 20}" height="${340 - (i % 3) * 30}"/>`).join("")}</g>
    <g fill="#A7ABB2">${[0, 1, 2, 3, 4, 5, 6, 7].map((i) => `<rect x="${i * 125 + 12}" y="${100 + (i % 3) * 30}" width="18" height="24"/><rect x="${i * 125 + 48}" y="${100 + (i % 3) * 30}" width="18" height="24"/>`).join("")}</g>
    <rect y="330" width="960" height="70" fill="#6B6E73"/>
    <text x="480" y="372" text-anchor="middle" fill="#F6F4F0" font-family="Space Mono, monospace" font-size="12" letter-spacing="2">CAMERA · ${esc(name.toUpperCase())} · PHOTO TODO</text></svg>`;
}

// Render the camera into `el`. opts: overlay (adds a close button, sits over a walk screen),
// leaveBtn, fresh (animate the traveller's own echo in), mini (Deep dive mini player), onClose.
function cameraView(el, stop, { overlay = false, leaveBtn = true, fresh = false, mini = false, ask = null, onClose } = {}) {
  const slot = stop === "castle" ? "castle_camera" : stop + "_wide";
  const aspect = C.imgAspect[slot] || 2.4;
  const mine = App.st.echoesLeft[stop];
  const echoes = echoesAt(stop);
  el.insertAdjacentHTML("beforeend", `<div class="cam${overlay ? " overlay" : ""}">
    <div class="pano" style="width:calc(100% * 0 + ${aspect} * var(--camh))">
      ${C.img[slot] ? `<img class="pano-img" src="${esc(C.img[slot])}" alt="${esc(C.stopNames[stop] || "")}" draggable="false">` : sceneSvg(stop)}
      ${echoes.map((e, i) => `<button class="echo" data-e="${i}" style="left:${e.x}%;top:${e.y}%" data-log="Echo: open" data-detail='${JSON.stringify({ stop, echo: e.kind + " " + e.text }).replace(/'/g, "&#39;")}'>
        <b>${esc(e.kind)}</b> ${esc(e.text)}<span class="ecount">${reactionsLine(e.counts)}</span></button>`).join("")}
      ${mine ? `<div class="echo mine${fresh ? " fresh" : ""}" style="left:40%;top:54%"><b>Your echo</b> ${esc(mine)}<span class="ecount">0 found — yet</span></div>` : ""}
    </div>
    <div class="vf"><i></i><i></i><i></i><i></i></div>
    <div class="cam-top">${overlay ? `<button class="cam-x" data-log="Camera: close">${icon("close")}</button>` : ""}<span class="pill cam-pill"><i class="bd"></i>${icon("photo_camera", "fill")}Echoes at ${esc(C.stopNames[stop] || "")} · ${echoes.length + (mine ? 1 : 0)}</span>${ask ? `<button class="tool ask-btn cam-ask" data-log="Ask button" data-detail='{"where":"${ask}"}'>${icon("forum", "fill")}<span>Ask</span></button>` : ""}</div>
    <div class="cam-hint">${icon("swipe")}Drag to look around</div>
    <div class="cam-bottom">${leaveBtn ? `<button class="btn block cam-leave" data-log="Leave an echo" data-detail='{"stop":"${stop}"}'>${icon("add_location_alt")}Leave an echo</button>` : ""}${mini ? miniPlayer() : ""}</div>
  </div>`);
  const cam = el.lastElementChild;
  if (mini) wireMini(cam);
  panCamera(cam);
  cam.querySelectorAll(".echo[data-e]").forEach((b) => (b.onclick = (ev) => {
    if (ev.target.closest(".react") || cam.dataset.dragged === "1") return;
    const open = b.classList.contains("open");
    cam.querySelectorAll(".echo.open").forEach((x) => { x.classList.remove("open"); x.querySelector(".reacts")?.remove(); });
    if (open) return;
    const e = echoes[+b.dataset.e];
    b.classList.add("open");
    b.insertAdjacentHTML("beforeend", `<span class="reacts pv-meta">${[["found", "Found it"], ["worth", "Worth it"], ["ha", "Ha!"]].map(([k, l]) => `<span class="react chip" data-k="${k}" data-log="Echo reaction: ${l}" data-detail='${JSON.stringify({ stop, echo: e.kind + " " + e.text }).replace(/'/g, "&#39;")}'>${l}</span>`).join("")}</span>`);
    b.querySelectorAll(".react").forEach((r) => (r.onclick = () => { e.counts[r.dataset.k]++; r.classList.add("done"); b.querySelector(".ecount").innerHTML = reactionsLine(e.counts); }));
  }));
  const lb = $(".cam-leave", cam);
  if (lb) lb.onclick = () => echoBuilder(cam, stop, () => { cam.remove(); cameraView(el, stop, { overlay, leaveBtn: false, fresh: true, mini, ask, onClose }); });
  const ab = $(".cam-ask", cam);
  if (ab) ab.onclick = () => askSheet(el, ask);
  const x = $(".cam-x", cam);
  if (x) x.onclick = () => { cam.remove(); onClose && onClose(); };
  return cam;
}

// Slow ping-pong pan; drag to look around; auto resumes after 3 s.
function panCamera(cam) {
  const pano = $(".pano", cam);
  const setH = () => cam.style.setProperty("--camh", cam.clientHeight + "px");
  setH();
  let pos = 0.35, dir = 1, last = performance.now(), holdUntil = 0, drag = null;
  const maxShift = () => Math.max(0, pano.offsetWidth - cam.clientWidth);
  const apply = () => (pano.style.transform = `translateX(${-pos * maxShift()}px)`);
  const step = (now) => {
    if (!cam.isConnected) return;
    const dt = Math.min(0.1, (now - last) / 1000); last = now;
    if (!drag && now > holdUntil) {
      const edge = Math.min(pos, 1 - pos);
      pos += dir * dt * 0.07 * Math.min(1, 0.25 + edge * 5);
      if (pos >= 1) { pos = 1; dir = -1; } if (pos <= 0) { pos = 0; dir = 1; }
    }
    apply();
    requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
  cam.addEventListener("pointerdown", (e) => {
    if (e.target.closest("button:not(.echo), .react, .mini, .echo-builder")) return;
    drag = { x: e.clientX, pos, moved: false }; cam.dataset.dragged = "0";
  });
  window.addEventListener("pointermove", (e) => {
    if (!drag || !cam.isConnected) return;
    const scale = cam.getBoundingClientRect().width / cam.clientWidth || 1;
    const dx = (e.clientX - drag.x) / scale;
    if (Math.abs(dx) > 4) { drag.moved = true; cam.dataset.dragged = "1"; $(".cam-hint", cam)?.remove(); }
    pos = Math.max(0, Math.min(1, drag.pos - dx / (maxShift() || 1)));
  });
  window.addEventListener("pointerup", () => {
    if (!drag) return;
    if (drag.moved) { App.record("tap", "Camera: dragged to look around"); holdUntil = performance.now() + 3000; }
    drag = null; setTimeout(() => (cam.dataset.dragged = "0"), 0);
  });
  setTimeout(() => $(".cam-hint", cam)?.classList.add("gone"), 4000);
}

// Leave an echo — templates and word lists only (5.4), at any stop.
function echoBuilder(host, stop, onLeft) {
  const things = C.echoThingsByStop[stop] || C.echoThings;
  const st = { tpl: null, dir: null, thing: null };
  host.insertAdjacentHTML("beforeend", `<div class="echo-builder"></div>`);
  const box = host.lastElementChild;
  const draw = () => {
    const needsDir = st.tpl && st.tpl.startsWith("Look");
    const text = !st.tpl ? "" : needsDir ? `Look ${st.dir || "___"} at ${st.thing || "___"}` : st.tpl.replace("___", st.thing || "___");
    const ready = st.tpl && st.thing && (!needsDir || st.dir);
    box.innerHTML = `<div class="scr eb">
      <button class="cam-x eb-x" data-log="Leave an echo: cancel">${icon("close")}</button>
      <div class="eyebrow eb-eye">Leave an echo · ${esc(C.stopNames[stop] || "")}</div>
      <h1 class="h1 h-serif eb-h">What should the next family know?</h1>
      <div class="eyebrow eb-step">1 · Pick one</div>
      <div class="chips">${C.echoTemplates.map((t) => `<button class="wchip${st.tpl === t ? " on" : ""}" data-tpl="${esc(t)}" data-log="Echo template: ${esc(t)}">${esc(t)}</button>`).join("")}</div>
      ${st.tpl ? `<div class="eyebrow eb-step">2 · Pick the words</div>
        ${needsDir ? `<div class="chips">${C.echoDirections.map((d) => `<button class="wchip${st.dir === d ? " on" : ""}" data-dir="${esc(d)}" data-log="Echo word: ${esc(d)}">${esc(d)}</button>`).join("")}</div>` : ""}
        <div class="chips">${things.map((d) => `<button class="wchip${st.thing === d ? " on" : ""}" data-thing="${esc(d)}" data-log="Echo word: ${esc(d)}">${esc(d)}</button>`).join("")}</div>` : ""}
      ${st.tpl ? `<div class="card lenspreview"><div class="eyebrow">Preview</div><div class="ptext">${esc(text)}</div></div>` : ""}
      <button class="btn block eb-pin" id="pin" style="${ready ? "" : "opacity:.4"}" ${ready ? "" : "disabled"} data-log="Leave it here" data-detail='${JSON.stringify({ stop, echo: text }).replace(/'/g, "&#39;")}'>${icon("push_pin")}Leave it here</button>
      <p class="body eb-fine">Only these words — no free text.</p>
    </div>`;
    box.querySelectorAll("[data-tpl]").forEach((b) => (b.onclick = () => { st.tpl = b.dataset.tpl; st.dir = null; draw(); }));
    box.querySelectorAll("[data-dir]").forEach((b) => (b.onclick = () => { st.dir = b.dataset.dir; draw(); }));
    box.querySelectorAll("[data-thing]").forEach((b) => (b.onclick = () => { st.thing = b.dataset.thing; draw(); }));
    $(".eb-x", box).onclick = () => box.remove();
    $("#pin", box).onclick = () => {
      if (!ready) return;
      App.st.echoesLeft[stop] = text;
      App.st.echoLeft = text; App.st.echoLeftAt = stop;
      App.record("echo-left", text, { stop });
      box.remove(); onLeft && onLeft();
    };
  };
  draw();
  return box;
}
