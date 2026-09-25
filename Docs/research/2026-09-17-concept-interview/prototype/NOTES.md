# PROTOTYPE — concept-interview walkthrough (throwaway)

**Question it answers:** can a target Planner experience Ondoway's core value proposition —
lenses, a planned day with Stop reasons and Skip advice, stories that start where you stand
(and differ per person), a re-plan, the in-line Deep dive, Echoes, I Spy, the Day and Trip
recap — **without the founder explaining or operating anything**? Spec: `../script.md`
(authority), `../brief.md`, `../interview-guide.md`. Vocabulary: `Docs/traveller/CONTEXT.md`.

## Words: hand-authored, not corpus (2026-09-23)

Every spoken line and every Ask answer in `content.js` was replaced with text written from
public-domain and government sources (NPS, LOC/HABS, NARA, LPC, NYC Parks). Scripts and
citations: `../scripts.md` (all 20 beats, 18:50). Research: `../sources/`.

Each story carries `source:` instead of the old `beat:` id, and the session log records it.
**This audio is not engine output and may never be presented as such.**

To drop a recording in: set that slot's `audio:` from `null` to the filename. The slot ids
are listed in `../scripts.md`.

## Look: the v10 design system

Every phone screen is skinned from **`mobile/design/design-system-v10.html`** (committed
2026-08-30 as the repo's design source of truth), not from the Flutter code. Its component
CSS is ported verbatim into `v10.css`; per-area additions live in `skin-*.css`. The frame is
v10's own 308x648 and is scaled up for the laptop, so the design stays pixel-faithful.

Mapping: opening = `01 Sign in` · lenses = `02 Choose lenses` · plan = `04 Build a tour` ·
Friday = `05 Tour preview` · walk = `06 Live guide` · re-plan = `06 Running late` ·
recap = `07 Tour complete` · price cards = `08 Unlock Paris`. The Deep dive, Echoes, I Spy
and the Ask sheet have no v10 equivalent and are built from its parts.

Two things dropped on purpose in the port: the design-system page's own chrome, and v10's
`prefers-color-scheme: dark` palette — the walkthrough must look the same on any laptop,
and the screens that are dark set their own tokens.

Local CSS and JS are injected at load with `?v=<timestamp>`, so a plain static server can
never serve yesterday's `content.js` after an edit. A new file just needs adding to
`__assets` in `PROTOTYPE-walkthrough.html`.

## Run

```bash
make concept-prototype
```

| View | URL (local) |
|---|---|
| Traveller | `http://127.0.0.1:8766/PROTOTYPE-walkthrough.html?s=<code>` |
| Interviewer (founder only) | `…?view=iv&s=<code>` |
| Close ranking board (founder's shared screen) | `…?view=board&s=<code>` |

Use one fresh `<code>` per session (e.g. `p03`). Same code in all three = same session.
`&reset=1` on the traveller link clears that browser's saved tap log.

**How the pieces talk.** The interviewer view follows the traveller over PeerJS (WebRTC via
the free public PeerJS broker, `0.peerjs.com`) when they are on different machines, and over
BroadcastChannel in the same browser (board → interviewer). The traveller page also keeps
its whole log in its own browser, so a dropped link is re-sent in full on reconnect; as a last
resort, **Ctrl/⌘+Shift+L** on the traveller page copies the log to the clipboard.

**Remote sessions need a public URL.** `make concept-prototype` serves on 127.0.0.1 only. For
a remote traveller, host this folder as static files anywhere (e.g. a Netlify drop of the
folder) — nothing else is needed; everything is relative paths. *Not decided yet — owner's
call.* A claude.ai Artifact does NOT work for the traveller page: its CSP blocks the PeerJS
connection and its live channels only reach viewers in the same org.

## What was verified (2026-09-18, in-app browser, 1440×900)

- Every screen 0.1 → 7.4 renders with no console errors.
- 1.1: toggling War & Conflict swaps the Federal Hall line in view.
- The walk runs itself: the dot walks each leg, the story starts on arrival (real browser
  speech, 180 voices available), transcript highlights in sync, it moves on after the story.
- 3.3: Maya's story plays, then Dan's on his phone, then 4.1's re-plan fires by itself
  (clock 12:10 → 12:55) — no input.
- Ask sheet pauses the story; chips give sourced answers; typed text gets the
  "no sourced answer" reply and is logged.
- Interviewer view followed the traveller tab (PeerJS reported "Traveller connected"),
  showed the step's questions, coded a tap U before "I just asked" and P after.
- Board → interviewer: ranking and price reveal arrive.

Not verified: two physical machines on different networks (PeerJS through NAT), Safari,
Firefox, and the audio-file path (no voiced audio exists yet).

## Revision 2026-09-18 (reviewer feedback, grilled into script.md — see "revised 2026-09-18")

Built and clicked through in the in-app browser, no console errors:
- 1.1 lens explanation dock (Federal Hall sample line dropped); 1.2 how/why copy + pretend invite.
- 2.1 "+ Add" must-see search (8 corpus landmarks, never Friday); 2.3 shows it on its day with why.
- Ask and Echoes buttons on every on-the-ground screen; Ask names the nearest place (between
  stops, the one you're walking to); new sourced chips for 9/11, Bowling Green, Castle/Liberty.
- Echoes at every stop: map badges, a camera view per stop that pans across a real photo with
  echoes pinned in it (drag to look around), "Leave an echo" anywhere.
- 16 royalty-free photos (`img/`, `CREDITS.md`), chosen by the founder from a contact sheet.
- 7.2 photos only (still the calm dinner screen). 7.3 showpiece: photo cards with stickers, a
  share-card editor (filters, sticker tray, drag), pretend "Add music from your library", pretend
  share sheet led by "Send to your party" with a story-frame preview.
- Interviewer view: new step budgets (2/6/7/3/5/2/5) and cut order (echo → I Spy → Trip recap).

## Still owed before the pilot (all slots are in `content.js`)

- ⚑ Founder review of Claude's drafts: 21 lens one-liners (`lensInfo`), per-stop echoes and word
  lists (`stopEchoes`, `echoThingsByStop`), stickers, must-see "why" lines.
- ✎ Skip advice verdict (`skipAdvice`; drafts shown to the founder in the interviewer view).
- ✎ Third I Spy find, and a review of all child copy (`ispy`).
- Audio: DONE as far as it goes — 10 of 20 slots carry the founder's own recordings in
  `audio/`; he ruled the other 10 not needed (2026-09-25), and they play in browser speech if
  a traveller reaches them (Memorial "more" 3-6, Federal Hall's "more", Deep dive 3-7).
- Photo gaps accepted 2026-09-18: no free Charging Bull (echo changed), no wide modern Trinity,
  no Met exterior (Saturday uses Central Park), recap photos are stand-ins, not one family.
- Caveat from the brief stands: NYC beats are commercial-guidebook text — fine for private
  interviews, not for investor demos.

## Verdict

_TODO (founder, after 6–8 sessions): which hypotheses the walkthrough supported, and every
screen where you had to explain something (a prototype defect, not a traveller failure)._

## Delete when done

This folder (including `img/` and `CREDITS.md`), plus the three things added outside it for the one-command run:
the `concept-prototype` target (and its `.PHONY` entry) in `Makefile`, `8766` in
`SERVER_PORTS` in `scripts/preflight.py`, and the `concept-prototype` entry in
`.claude/launch.json`.
