// PROTOTYPE — throwaway. Close: the ranking board on the founder's shared screen
// (?view=board). Seven cards, one per step; drag into the ranked column. Price cards stay
// hidden until the founder reveals them after the open price question. The order goes to
// the interviewer view in the same browser (BroadcastChannel).

const BOARD_CARDS = [
  ["Picks what fits your family", "your interests shape every story."],
  ["Plans the day between your big sites", "and tells you what to skip."],
  ["Stories that start where you're standing", "different ones for each of you."],
  ["Changes the plan when your day changes.", ""],
  ["The big story while you wait in line", "together, out loud."],
  ["Notes left by other travellers,", "and leaving your own."],
  ["Your day told back at dinner,", "and the trip when you're home."],
];

const Board = {
  ranked: [], prices: false,
  boot() {
    document.title = "Ondoway Ranking Board";
    this.render();
  },
  send() { Sync.send({ type: "ranking", order: this.ranked.map((i) => BOARD_CARDS[i][0]), prices: this.prices }); },
  card(i) { const [h, b] = BOARD_CARDS[i]; return `<div class="bcard" draggable="true" data-i="${i}"><b>${esc(h)}</b>${b ? ` <span>${esc(b)}</span>` : ""}</div>`; },
  render() {
    const pool = BOARD_CARDS.map((_, i) => i).filter((i) => !this.ranked.includes(i));
    document.getElementById("root").innerHTML = `<div class="board">
      <header><div class="eyebrow">Ondoway</div><h1 class="h1">If it could only do one of these, which one?</h1></header>
      <div class="bcols">
        <section class="bcol" data-zone="pool"><div class="eyebrow">What it does</div>${pool.map((i) => this.card(i)).join("") || '<p class="muted">All ranked.</p>'}</section>
        <section class="bcol ranked" data-zone="ranked"><div class="eyebrow">Your ranking</div>
          ${this.ranked.map((i, n) => `<div class="rrow"><span class="rn">${n + 1}</span>${this.card(i)}</div>`).join("") || '<p class="muted drop">Drag cards here, most important first.</p>'}</section>
      </div>
      <div class="prices paywall"${this.prices ? "" : " hidden"}>
        <div class="pgrid">
          <div class="plan"><span class="g"><span class="peye">Trip pass</span><span class="t">Solo / Pair</span></span><span class="price">$12–19</span></div>
          <div class="plan alt"><span class="g"><span class="peye">Trip pass</span><span class="t">Household (up to 5)</span></span><span class="price">$29–49</span></div>
        </div>
        <p class="psub pnote">One city, one trip. Paid once.</p>
      </div>
      <div class="bfoot"><button id="reveal" class="btn quiet">${this.prices ? "Hide prices" : "Reveal prices"}</button><button id="breset" class="btn quiet">Reset ranking</button></div>
    </div>`;
    let drag = null;
    document.querySelectorAll(".bcard").forEach((c) => {
      c.ondragstart = () => { drag = +c.dataset.i; c.classList.add("dragging"); };
      c.ondragend = () => c.classList.remove("dragging");
      c.onclick = () => { // click also works: pool → end of ranking; ranked → back to pool
        const i = +c.dataset.i;
        this.ranked = this.ranked.includes(i) ? this.ranked.filter((x) => x !== i) : [...this.ranked, i];
        this.send(); this.render();
      };
    });
    document.querySelectorAll("[data-zone]").forEach((z) => {
      z.ondragover = (e) => e.preventDefault();
      z.ondrop = (e) => {
        e.preventDefault(); if (drag == null) return;
        const rest = this.ranked.filter((x) => x !== drag);
        if (z.dataset.zone === "ranked") {
          const rows = [...z.querySelectorAll(".rrow")].filter((r) => +r.querySelector(".bcard").dataset.i !== drag);
          let at = rows.findIndex((r) => e.clientY < r.getBoundingClientRect().top + r.offsetHeight / 2);
          if (at < 0) at = rest.length;
          rest.splice(at, 0, drag);
        }
        this.ranked = rest; drag = null; this.send(); this.render();
      };
    });
    document.getElementById("reveal").onclick = () => { this.prices = !this.prices; this.send(); this.render(); };
    document.getElementById("breset").onclick = () => { this.ranked = []; this.send(); this.render(); };
  },
};
