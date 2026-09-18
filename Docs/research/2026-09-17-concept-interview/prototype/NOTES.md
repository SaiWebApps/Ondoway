# PROTOTYPE — concept-interview walkthrough (throwaway)

**Question it answers:** can a target Planner experience Ondoway's core value proposition —
lenses, a planned day with Stop reasons and Skip advice, stories that start where you stand
(and differ per person), a re-plan, the in-line Deep dive, Echoes, I Spy, the Day and Trip
recap — **without the founder explaining or operating anything**? Spec: `../script.md`
(authority), `../brief.md`, `../interview-guide.md`. Vocabulary: `Docs/traveller/CONTEXT.md`.

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

## Still owed before the pilot (all slots are in `content.js`)

- ✎ Skip advice verdict (`skipAdvice`; drafts shown to the founder in the interviewer view).
- ✎ Third I Spy find, and a review of all child copy (`ispy`).
- **Real audio** for every story and Deep dive chapter: voice each `text` with the project's
  TTS (`src/audio/provider.py`) and set `audio: "audio/<beat_id>.mp3"`. Browser speech is
  the placeholder until then.
- Photos: `photos.family` (0.1), `photos.castleClintonScene` (5.3 camera), and the recap
  thumbnails (7.2, 7.3) — public-domain or licensed.
- Caveat from the brief stands: NYC beats are commercial-guidebook text — fine for private
  interviews, not for investor demos.

## Verdict

_TODO (founder, after 6–8 sessions): which hypotheses the walkthrough supported, and every
screen where you had to explain something (a prototype defect, not a traveller failure)._

## Delete when done

This folder, plus the three things added outside it for the one-command run:
the `concept-prototype` target (and its `.PHONY` entry) in `Makefile`, `8766` in
`SERVER_PORTS` in `scripts/preflight.py`, and the `concept-prototype` entry in
`.claude/launch.json`.
