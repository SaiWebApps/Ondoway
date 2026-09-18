// PROTOTYPE — throwaway. Moves the traveller's position + tap log to the interviewer view.
//
// Two transports, both optional, both used when available:
//  1. BroadcastChannel — same browser (rehearsal, or board → interviewer on the founder's machine).
//  2. PeerJS (WebRTC, public PeerJS broker) — traveller and founder on different machines.
//     The traveller's link carries ?s=<code>; the interviewer view with the same code listens.
// The traveller page also keeps its own log in localStorage, so nothing is lost if the
// connection drops: the interviewer view asks for the full log on (re)connect.

window.Sync = (function () {
  const params = new URLSearchParams(location.search);
  const code = (params.get("s") || "").replace(/[^a-z0-9]/gi, "").toLowerCase();
  const handlers = [];
  let bc = null;
  let conns = [];
  try { bc = new BroadcastChannel("ondoway-proto-" + (code || "local")); } catch (e) { bc = null; }
  if (bc) bc.onmessage = (ev) => handlers.forEach((h) => h(ev.data));

  const status = { peer: "off", peers: 0 };
  const statusHandlers = [];
  function setStatus(s) { Object.assign(status, s); statusHandlers.forEach((h) => h(status)); }

  function send(msg) {
    if (bc) { try { bc.postMessage(msg); } catch (e) {} }
    conns.forEach((c) => { try { if (c.open) c.send(msg); } catch (e) {} });
  }

  function wire(conn) {
    conn.on("open", () => { conns.push(conn); setStatus({ peer: "connected", peers: conns.length }); handlers.forEach((h) => h({ type: "peer-open" })); });
    conn.on("data", (d) => handlers.forEach((h) => h(d)));
    conn.on("close", () => { conns = conns.filter((c) => c !== conn); setStatus({ peer: conns.length ? "connected" : "waiting", peers: conns.length }); });
    conn.on("error", () => {});
  }

  // role: "traveller" dials the interviewer; "interviewer" listens. Board uses BroadcastChannel only.
  function start(role) {
    if (!code || !window.Peer) { setStatus({ peer: code ? "unavailable" : "off" }); return; }
    const hostId = "ondoway-proto-" + code + "-iv";
    if (role === "interviewer") {
      const peer = new Peer(hostId);
      peer.on("open", () => setStatus({ peer: "waiting" }));
      peer.on("connection", wire);
      peer.on("error", (e) => setStatus({ peer: "error: " + (e.type || e) }));
    } else {
      const peer = new Peer();
      let tries = 0;
      const dial = () => {
        const c = peer.connect(hostId, { reliable: true });
        wire(c);
        c.on("close", () => setTimeout(dial, 3000));
        setTimeout(() => { if (!c.open && tries++ < 200) { try { c.close(); } catch (e) {} dial(); } }, 5000);
      };
      peer.on("open", dial);
      peer.on("error", () => setStatus({ peer: "retrying" }));
    }
  }

  return {
    code, send, start, status,
    on: (h) => handlers.push(h),
    onStatus: (h) => { statusHandlers.push(h); h(status); },
  };
})();

// Tiny storage helpers — storage can throw (private windows, blocked site data).
window.Store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
};
