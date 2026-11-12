# Independent tracks build concurrently in lane-isolated sandboxes

Workstreams that share no code path are built as concurrent tracks, one per
sandbox. A sandbox is a git worktree plus its own lane. A lane owns its own
Neo4j graphs (dev, test and workbench) and its own API, dashboard and
Flutter web ports. The `LANE=` variable selects the whole set, or the
worktree's `.ondoway-lane` file names it. A worktree with no lane refuses
every target, so it can never fall through to the main checkout's graphs.

Lane 4 is the sandbox lane: the lane a second worktree owns outright. Lanes 2
and 3 are shards of the full test suite, so a sandbox never takes them.

A worktree touches only its own lane's graphs: `db-up`, `db-down` and
`db-reset` refuse another lane's database. Routing is shared. It is a
read-only lookup, so every lane uses the one routing engine and the map tiles
in the main checkout. A worktree never starts a second engine. The full suite
(`make test`, `make audit`) runs only from the main checkout, because it uses
the main graphs and lanes 2 and 3 as its shards.

## Considered Options

- **A shared checkout with a file-ownership map** — ownership on paper does
  not make trampling impossible; isolation does.
- **Duplicating the compose stack per worktree** — rebuilds what `LANE=`
  already provides and forfeits the guards the lanes carry, such as the
  shared read-only routing tiles.
