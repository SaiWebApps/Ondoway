---
description: Plan a feature until there are zero unknowns, then build it in demoable milestone commits. Walks the real code path (reading every function on it), produces a plan where nothing is a guess, shows you the plan and the dashboard, and waits for "go". Invoke as `/team <feature request or plan>`.
argument-hint: "<the feature request, in plain English>"
---

You are running `$ARGUMENTS` through the Ondoway team process. It has two
halves. The front half produces a plan so grounded in the actual code that
implementation holds **zero surprises** — the plan is never adjusted once
execution starts. The back half, entered only after the human says **go** in
chat, executes that plan in demoable milestone commits with a live dashboard.

A plan built from excerpts and priors collapses mid-implementation; the
process below makes the plan's raw material *discoveries about this codebase*,
so a plan without real reading behind it is visibly empty.

---

# Phase 1 — the goal, in one line

State the goal in product terms, tied straight to user experience. Not "add a
column to the POI table" but "The tour should factor in opening and closing
hours of places." Every milestone in the plan must trace back to this line; a
step that doesn't serve it gets cut.

The goal is met or failed at the surface the end user touches — the
narration heard, the screen read — never in the engine's data. The existing
code's abstractions (a flag, a regex, a map) are how the code models the
goal; they are never allowed to *define* it. Extend-don't-duplicate binds
the implementation, not the meaning of the story.

**Name the personas this goal serves and read their files**
(`docs/personas/` — the eleven people this product is built for). They are
the project's definition of "good for the user": the same change can be a
gift to one of them and noise to another, and a plan that never asks which
is guessing. Every user-experience judgment below — the traces, the
before/after, acceptance — is made through the named personas, not through
a generic "the user".

If the human handed you a plan rather than a request, extract the goal from it
first, then treat their plan as a *draft* subject to the same walk as anything
else. You verify it against the code; you never execute it on trust.

# Phase 2 — walk the code path (yourself — the walk is never delegated)

**First, write the guess down.** Before looking at any code, write three lines
from what you currently believe: where this probably lives, what probably
exists already, what you'd probably have to build. This is not the plan — it
is the thing the walk exists to break. Every place the code turns out to
differ from this guess is a surprise you just prevented from happening
mid-implementation, and those discoveries are the walk's product.

Then walk:

1. **Sync and explore.** `codegraph sync`, then `codegraph explore "<the
   goal>"` — it returns the relevant functions' verbatim, line-numbered
   source plus their call paths in one command, so reading the path costs a
   command, not an afternoon. `codegraph node <symbol>` gives one function
   with its caller/callee trail; `codegraph node --file <path>` reads a whole
   file with line numbers and dependents; `codegraph callers <symbol>` and
   `codegraph impact <symbol>` give the blast radius.
   `Agent(subagent_type:'Explore')` may help locate code across surfaces, but
   it reads excerpts — it points, you read.

2. **Name the path.** Entry point (API route, CLI, screen, pipeline stage) to
   output, every function in call order.

3. **Read every function on the path — plus its callers and the tests that
   pin it.** For each one, record a one-line **contract as read**: what comes
   in, what goes out, and what it fails open (or closed) on. The fail-open
   branches are the part an excerpt never shows and the part that breaks
   plans. Source is read with `Read` or codegraph's verbatim output — never
   summarized from `grep`/`head`/`sed`/`tail` fragments; those tools are for
   logs and data. When a milestone CHANGES a function's signature or calling
   contract, enumerate that symbol's callers across the WHOLE repo
   (`codegraph callers`, plus a grep over `src/`, `scripts/` and `tests/`) —
   never just the file you walked; the caller you didn't list is the
   mid-implementation surprise.

4. **Simulate inputs through the path — all the way to the user's surface.**
   Pick 2–3 concrete inputs (a real POI, a real tour request, a
   thin/degraded case) and trace each one function by function using the
   contracts you just recorded: what comes in, what happens to it today,
   what comes out. Every trace ENDS at the artifact the end user receives,
   **quoted** — the actual narration lines heard, the actual screen text —
   never at a field or a flag, and it is read AS one of the named personas
   standing there (Camille hearing an interior described at a closed door is
   enriched; Marcus with a train to catch is delayed — the same quote, two
   verdicts). The gap between today's quoted artifact and the goal's IS the
   feature, and a gap you cannot see in the quotes is a gap the feature does
   not close.

5. **Find the extension point.** For everything missing, find the existing
   function/module that should grow to cover it (CLAUDE.md invariant 1: never
   build it twice; workbench and app share one implementation). Record the
   search you ran to prove no sibling already does this. A new file or new
   function is the *last* resort and needs a sentence justifying why no
   existing one could be extended.

6. **Resolve every unknown NOW.** If you don't know what a function returns
   for some input — run it, run its test, or query the DB. If you don't know
   whether a Make target exists — grep the live Makefile. Nothing is deferred
   to "verify during implementation". An unknown left in the plan is a
   surprise scheduled for later.

**Close the walk with "What the walk found":** the diff between your
three-line guess and reality — what already exists that you'd have rebuilt,
what works differently than assumed, where the real gap turned out to be.
This section *leads* the plan artifact and is what the human reads first. A
walk that surprised you nowhere means either you already knew this code — or
you didn't actually read it; on code you haven't worked in this session,
treat zero surprises as a sign to walk again, wider.

# Phase 3 — the plan artifact

Write the plan to `.claude/runs/{YYYY-MM-DD}-{slug}/plan.md` (gitignored by
the `.claude/*` rule — it is this run's scratch, never product). It opens with
**What the walk found**, then has one section per **milestone**, where a
milestone is a demoable slice: after it lands, the human can be shown
something real that works. Each milestone becomes exactly one commit.

Every milestone section must contain, in this order:

1. **The user's artifact, quoted, before and after.** Not a description
   ("the stop flips to outside-only" is engine language) — the predicted
   lines the end user actually hears or reads once this milestone lands,
   next to what they get today. The demo then reproduces the prediction. If
   the predicted "after" still contains the failure — an indoor scene at a
   shut door — the plan shows it before anything is built.
2. **Code path table** — every function this milestone touches or extends:
   `function — file:line-range — contract as read` (from Phase 2 step 3; a
   row you cannot fill from your own reading is a row you cannot plan on).
3. **Input traces** — the 2–3 concrete inputs from Phase 2: today's behavior
   at each step, and the exact line where the new behavior attaches.
4. **Extension point** — the ONE existing function/module being extended, and
   the duplication check that proved nothing else already does this.
5. **Unit tests** — pytest node ids (or `mobile/test/` / workbench
   equivalents), what each asserts, and why it is red before this milestone
   and green after.
6. **Integration tests** — what proves the extended path works end-to-end
   with its real neighbors (DB, Valhalla, providers per project policy).
7. **Functional test / demo command** — the command (or workbench/app action)
   that shows the milestone working, runnable by the human.
8. **Commit message** — written now, not after.

**The finished-plan discriminator.** A plan is done when nothing in it is a
guess. Scan your own plan for `TBD`, `likely`, `should`, `probably`, `verify
later`, `assuming` — any of these means Phase 2 isn't finished; go back and
resolve it against the running code. Every factual claim about this repo in
the plan carries a `file:line` you actually read this session. And **one
mechanism per extension point**: an "or", "either", or "whichever" between
design alternatives means you haven't decided, and an undecided seam is
exactly where mid-implementation surprises come from — decide now, against
the code.

Two completeness checks, done yourself:

- A milestone that doesn't trace to the Phase 1 goal → cut it.
- A part of the goal covered by no milestone → gap: add a milestone or move it
  explicitly out of scope.

# Phase 4 — alignment check, record, present, and WAIT

First re-read the goal line against the **quoted predicted artifacts** — the
after-state lines the user will hear or read once the last milestone lands.
The check is never "do the milestone names cover the goal" (prose agrees
with prose for free); it is "do these exact quoted lines deliver the goal's
sentence?" A predicted after-state that still shows the failure means the
plan is not finished. Fix mismatches before showing anything.

Then record the plan in the tracker (it is the one record no agent can
reformat, and it feeds the dashboard):

```
python3 .claude/ledger/track.py feature-add --slug {slug} --title "…" --for-whom "…" --tier 1
python3 .claude/ledger/track.py story-add --feature {slug} --id S1 --text "…" --said-by owner
python3 .claude/ledger/track.py issue-add --story S1 --id M1 --name "…" --test-command "…" --files src/… tests/…
```

One `story-add` per user story in the human's own words; one `issue-add` per
milestone, carrying its demo/test command and files.

**A milestone's `--test-command` is the NARROWEST command that proves THAT
milestone, and it names something that already runs** — one test file, or one
pytest node id. Never `make test` or `make audit`: the tracker RE-RUNS this
command to verify a completion claim, so a whole-bar command makes the box lag
the finished work by minutes and lets infrastructure the milestone never
touched refuse it. Never a test that is not written yet either — a planned name
can never flip a box. When the milestone's test lands under a different name
than the plan guessed, re-point the row with `issue-set --id M{n}
--test-command "…"` before claiming the step. "Is the bar green" and "is this
milestone done" are different questions; the full bar answers the first, once,
at the end of the run (Phase 5).

Tier is one line, by the paths touched (highest wins): **1** = `src/`,
`tests/`, `scripts/`; **2** = tour content, `mobile/`, `src/api/routes/`,
`frontend/`; **3** = `Makefile`, `.claude/`, deploy/infra, DB/data.

**Start the dashboard now** — run it, don't mention it — in the background:

```
python3 .claude/ledger/track.py serve --port 8010
```

It prints its URL as JSON on the first line. Paste that URL as the FIRST line
of your reply. The human watches the feature build up piece by piece there;
that is the dashboard's entire point.

Present, in one screen:

1. The goal (one line) and what is explicitly out of scope.
2. **What the walk found** — the discoveries, briefly.
3. The stories, each in the human's own words.
4. **The user's before and after, quoted, judged as the named personas** —
   what they get today and the predicted lines after the last milestone,
   with a sentence on how each named persona receives the change. The human
   approves an experience, never a list of milestone names.
5. The milestones: name, what the demo shows, the test command. (Full traces
   stay in `plan.md` — link it, don't paste it.)
6. One plain question:

> Say **go** to build this, or tell me what to change. Nothing has been built
> yet.

**End your turn and wait.** No approval exists until the human says so in
chat. If they ask for changes, amend the plan and the tracker rows and
re-present.

**What "go" approves is the GOAL, not a milestone list.** Approval is spent
once, at this boundary, and it covers every milestone in the plan AND every
finding-derived story that traces to the goal line, for the life of the run.
Name the run's BUDGET here too — wall-clock, spend, or cycle count — because
the budget is one of the loop's two exits (Phase 6). Emergent work that
cannot trace to the goal is an escalation, never quietly adopted and never
quietly parked.

# Phase 5 — execute, one milestone at a time (after "go")

Record the approval first — you are transcribing their decision, never making
it:

```
python3 .claude/ledger/track.py approve --feature {slug} --by owner
```

Then for each milestone, in dependency order:

1. `step-status --id M{n} --status in_progress`.
2. Write the failing test from the plan; see it red.
3. Make the change the plan specified — at the exact attachment points the
   plan named. Before modifying any source file, read the ENTIRE file
   (CLAUDE.md rule 5) — Phase 2 read the path; modification needs the whole
   file's context, current as of now.
4. Test green, then `make lint`. (Per-file test runs: `make test-file
   FILE=tests/test_x.py` — foreground, read the result; never poll a log.)
5. **Demo it**: run the milestone's demo command and show the human the
   output (screenshot, transcript, or the command itself to click). Every
   milestone shows the feature further along — that's what the dashboard and
   the demo are for.
6. Commit — the message written in Phase 3. `step-status --status completed`
   (the tracker re-runs the step's test itself and refuses a false claim).
7. Post a sprint note — `track note --story {id} --text "…" --who
   sprint-leader` — one plain sentence of what just happened. Also post one
   at every divergence and every verdict (QA, acceptance). Notes are the
   dashboard's narrative channel: attributed commentary beside the facts,
   structurally unable to move a box or the percentage.

**A milestone that removes something runs `/cleanup` inside itself.**
Deleting or renaming a file, symbol, make target, command, or concept
invokes `Skill(cleanup)` before that milestone's commit, so one commit
carries the removal AND every reference to the removed thing — its manifest,
sweep, trap checks and receipt. No dead code, no hanging fragments, no
string-anchored traps left for the next reader to step on.

**If reality diverges from the plan — stop the line, not the run.** A wrong
signature, a test that was supposed to be red but is green, a function that
doesn't behave as the plan traced: that is a *planning* failure, and
improvising a fix on the spot is how half-baked implementations happen. Say
plainly what the plan got wrong, re-walk the affected path (Phase 2 rules),
amend the plan and tracker for the remaining milestones, and post the
amendment as a sprint note — the written trail replaces a pause. An
amendment that changes PRODUCT behaviour or an EXISTING test's assertions
must cite the binding decision that arbitrates it; no citation, no
proceeding — that is an escalation (the pause budget below). A divergence
hidden is worse than a divergence found.

**Gate commands run bare.** A gate command — `make lint`, a pytest run,
`make audit`, any command whose exit code decides a claim — runs as the SOLE
command of its Bash invocation: never piped through `tail`/`grep`, never
chained with `&&` or `;`, never followed by anything. The tool's own exit
code for that bare call is the only admissible evidence the gate passed; an
`exit=$?` echoed after a pipeline reports the pipe's tail, not the gate.
Every `git commit` is immediately preceded by a bare `make lint` call of its
own — a commit whose lint ran inside a chain is unproven.

Run the full bar (`make audit`) once per CYCLE — a story plus the findings
it consumed — after its last milestone, not between steps.

# Phase 6 — verdicts, and the cycle boundary

This phase is a boundary the run crosses, not an exit. Run the whole feature
the way the human would use it — the real tour, the real screen, the real
workbench page. Not "tests pass": *it works*, visibly. For tier 2+ work,
run `Agent(subagent_type:'acceptance')` on the produced artifact — told
which personas the goal serves, so it judges as those people and not as a
generic critic — and `Agent(subagent_type:'qa')` for the undo test on the
new tests, CONCURRENTLY: both are read-only judges, each returns its findings
to the sprint-leader, and the sprint-leader alone writes them onward. Post
their verdicts as sprint notes.

**Findings are fuel.** Every actionable acceptance or QA finding is promoted
to a tracker row — never a UI chip, never prose alone — walked at Phase 2
rigor, adversarially plan-reviewed, and BUILT: the run re-enters Phase 5
with the findings ordered by user impact. "Owner-scheduled" exists only for
a finding that conflicts with a binding decision or exceeds the goal line —
and that is an escalation to the human, never a drawer. A finding parked
anywhere else is a deferral wearing a costume.

**The run has exactly two exits.** (1) A PANEL — 2–3 adversarial agents on
different models (skeptic / tour-adversary / acceptance) — unanimously
judges the goal's personas served; one agent's SHIP is not the bar. (2) The
budget named at "go" exhausts first. The exit report states which one
fired, and when it was the budget, the honest gap — what the personas still
experience that the goal's sentence forbids.

---

## Multi-track mode

A phase whose stories share no code path runs as concurrent TRACKS, one per
sandbox — `docs/adr/0002-independent-tracks-build-in-isolated-sandboxes.md`
is the decision record. Multi-track changes the run's mechanics in exactly
the ways below; everything else in this file applies per track, unchanged.

- **One planning session for the set.** Phases 1–4 run once and produce every
  track's milestones, ONE conflict map naming an owner per shared file
  region (in `plan.md`), and a per-track budget. "go" is given once for the
  set; after it, a track never idles waiting for human input that is not a
  pause-budget item.
- **A sandbox is an explicit worktree plus its own lane.** Created with
  `git worktree add <path> -b <branch>` — never a harness-managed worktree —
  then `uv sync` and `codegraph init` inside it. `LANE=` selects its graphs
  and ports. The infra milestones that create a lane land on main BEFORE the
  worktree is cut, so every sandbox is born already isolated.
- **Ledgers are per checkout.** A completion claim re-runs its test in the
  checkout that made it, so each track records its rows in its own
  `.claude/ledger/tracker.db` and serves its own dashboard (main :8010,
  lane N :801N). `plan.md` and `assets.md` are placed in each checkout's run
  dir at launch, and the conflict map binds every track identically.
- **Only the main checkout runs the definitive bar.** `make test` and
  `make audit` always run the canonical set and consume lanes 2 and 3 as
  shards. A sandbox runs `make lint`, its milestones' narrow tests and
  `make test-file LANE=<n>`; it never runs xdist against the canonical
  shards, never starts or stops Valhalla, never deploys, and never touches
  another lane's ports or graphs.
- **Exit is an end-merge.** The track owning the phase gate merges to main
  first; the others rebase onto it; ONE full bar and ONE adversarial panel
  on merged main are the set's exit. Each track's own story still closes
  through its per-cycle floor before its merge.
- **No intra-track pipelining.** Milestones inside a track stay serial; the
  parallelism lives BETWEEN sandboxes, where the isolation is physical.

## The pause budget, and the per-cycle floor

Exactly three things interrupt the run and reach the human in chat:

1. **A binding-decision conflict** — presented as a crisp either/or with a
   recommendation.
2. **Real money above the named threshold** — paid compose or TTS beyond the
   test tiers.
3. **An irreversible or destructive operation.**

Everything else — divergences, re-walks, amendments, verdicts — is tracker
notes and dashboard state. Chat carries escalations and the exit report;
the dashboard is the narrative.

Every cycle clears the same quality floor, because fewer pauses is never
fewer checks:

- **Adversarial plan review before building** — the reviewer attacks the
  predicted artifacts, not the milestone names, and its objections are
  resolved in the plan before the first test is written.
- Red-first tests, the QA undo pass, and both verdict agents — concurrent,
  because each only reads; the sprint-leader is the one writer of their
  findings.
- The full bar once per cycle.
- **A skeptic pass over the previous cycle's claims after every context
  compaction, and every third cycle regardless** — whichever comes first; a
  refuted claim reopens its row.

## Shared assets — the manifest is the meeting

Sub-agents cannot talk to each other mid-flight; the sprint-leader is the
only meeting point, and the meeting's minutes are written BEFORE the spawn.
The run directory carries `assets.md` beside `plan.md`: one row per asset a
spawned agent may touch — path, owner, allowed operations, release state.
No agent is spawned before its rows exist, and every agent prompt quotes its
rows verbatim and states: write only these paths; everything else is
read-only.

- **One writer at a time.** Concurrent access to a shared mutable asset is
  forbidden. Two parties needing the same asset run sequentially; the
  manifest row names the order and the handoff state. Findings are such an
  asset: verdict agents RETURN theirs and the sprint-leader alone writes
  them to the tracker — which is exactly what makes concurrent read-only
  verdict agents safe.
- **Agents never delete.** Every spawned-agent prompt carries: "Delete
  nothing, anywhere — including files you believe are scratch. Report
  deletion candidates in your findings instead." Deletion is executed only
  by the main session, through `/cleanup` when it is a real removal.

## Rules

- **The walk is yours.** Locating code may be delegated; reading it may not.
- **Read functions whole, through tools that show them whole** — `Read` and
  codegraph's verbatim output. Excerpt-planning is the root failure this
  command exists to prevent; before *modifying* a file, CLAUDE.md rule 5
  (entire file) applies.
- **Extend, never duplicate** (CLAUDE.md invariant 1). Every milestone names
  its extension point and the search that cleared it.
- **No guesses survive Phase 3.** Unknowns are resolved by running things
  now, never deferred.
- **The plan never changes silently.** After "go", any divergence stops the
  line, is re-walked, and lands as a written plan amendment with a sprint
  note; product-behaviour and existing-test amendments cite the binding
  decision that arbitrates them or escalate.
- **You never bless your own plan.** The human approves the goal in chat
  (`track approve` records, it never decides); every cycle's plan then faces
  an adversarial review that is not you.
- **The run ends at the persona bar or the budget — never at the bottom of
  a milestone list.** Findings are consumed, not filed.
- **A gate's evidence is its own exit code.** Gate commands run as the sole
  command of their invocation; a green claim cites that bare call's exit,
  never decorated output.
- **Assets are claimed in writing before any spawn.** `assets.md` rows
  precede every agent; one writer at a time; agents never delete.
- Genuine product trade-offs are escalated as a crisp either/or with a
  recommendation — never guessed silently, never buried.
- Open-ended discovery ("find and fix whatever's wrong") is the wrong task
  for this command — use `Skill(proactive-audit)`.
