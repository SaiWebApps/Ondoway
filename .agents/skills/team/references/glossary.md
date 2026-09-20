# Glossary — the canonical vocabulary

One name per thing. A session's memory points here and never restates these;
a term that drifts from this file is wrong, not this file.

- **Feature** — one owner-visible outcome, held in the tracker with a tier and
  a title in plain words. The unit of approval.
- **Story** — one promise inside a feature, written in the owner's own words,
  with the sayer named. The unit of delivery and of the demo.
- **Criterion** — one acceptance line of a story, carried as a runnable test
  command. Red before the build, green at Done, committed before any
  implementation so softening it is a visible diff.
- **Milestone (issue)** — one commit's worth of work under a story, with the
  narrowest command that proves it. The tracker runs that command itself
  before recording the milestone as proven.
- **Seal** — the hash over a feature's stories, criteria and milestones,
  stored at approval. The approved shape. Changing the shape afterwards
  requires an owner change and re-seals.
- **Owner change** — the owner's recorded word that the approved shape may
  change once. Single use.
- **Interrupt** — one question to the owner between approval and Done. Three
  per story; the fourth parks the story.
- **Parked** — a story set aside with a one-paragraph plain-words state. The
  run moves on; parked stories lead the next session's report.
- **Defect** — a mistake found mid-story after milestones landed. Two per
  story; each begins with a failing regression test. A third parks the story.
- **Red is red** — a failing gate has no other name: not flaky, not
  unrelated, not pre-existing. One evidence rerun is allowed and logged, and
  even a green rerun spends a defect slot and requires a determinism fix.
- **Lease** — the single active writer's claim on a slot of work. One writer
  at a time; a stale lease is released at session start, never inherited.
- **Release window** — the recorded owner's word that process files may
  change, present only between stories. Its absence is the normal state.
- **Demo** — the owner's own run of a story's numbered script, ten steps or
  fewer, on the phone or workbench. A story is not Done until the demo held.
- **Wins statement** — two or three plain sentences saying what the owner
  gained. Required at feature close.
