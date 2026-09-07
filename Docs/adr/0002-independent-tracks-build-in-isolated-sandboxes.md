# A phase's independent tracks build concurrently in lane-isolated sandboxes

A phase whose workstreams share no code path is built as concurrent tracks,
one per sandbox. A sandbox is a git worktree at an explicit path plus its own
lane: its own Neo4j graphs, API port, workbench port and simulator, selected
by the same `LANE=` variable that names every other lane. All tracks are
planned in one session that produces every track's plan, a conflict map
naming an owner for each coordinated file region, and each track's budget;
one "go" launches the set, and after it a track never idles waiting for human
input that is not a pause-budget item.

Each checkout carries its own ledger and dashboard, because a completion
claim is verified by re-running the milestone's test in the checkout that
made it. Only the main checkout runs the definitive bar. A sandbox never
starts or stops Valhalla, never deploys, and never touches another lane's
ports. Verdict agents that only read — acceptance and QA, and the members of
an adversarial panel — run concurrently; milestones inside a track stay
serial.

Tracks exit by merging: the track that owns the phase gate merges first, the
others rebase onto it, and one full bar plus one adversarial panel on the
merged result is the phase exit.

## Considered Options

- **A shared checkout with a file-ownership map** — ownership on paper does
  not make trampling impossible; isolation does.
- **Continuous merges to main after every milestone** — serializes the bar
  and multiplies per-milestone ceremony, which is the cost being removed.
- **Duplicating the compose stack per worktree** — rebuilds what `LANE=`
  already provides and forfeits the guards the lanes carry: the pinned
  compose project name, heap caps, the shared read-only Valhalla root.
- **Pipelining a track's next milestone under the previous one's QA** — same
  files, same sandbox; the overlap is where a false green hides.
