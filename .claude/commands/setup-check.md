Check whether this machine has the prerequisites to run the Ondoway project, and help
install anything missing.

## Run the real check; do not hand-roll one

```bash
make doctor
```

`make doctor` reports every declared prerequisite and changes nothing. It is the single
source of truth: the same requirement definitions (`scripts/preflight.py`) gate every
Make target, so anything it reports green is exactly what those targets will find. It
also runs on the system interpreter, so it still answers on a machine with no project
environment yet.

To check AND repair everything repairable in one pass:

```bash
make preflight
```

To see what each requirement means and how it is restored:

```bash
make preflight-list
```

## Reading the result

Each line is `OK` (satisfied), `FIX` (was missing, preflight restored it), or `!!`
(blocked). Every blocked line prints the exact command to run. Relay those commands
verbatim; do not invent alternatives.

Three blockers deserve explanation rather than relay, because their fix is not obvious:

- **`render-key`** — provider secrets (Anthropic, OpenAI, Resend) are NOT stored in this
  repo and are NOT read from any `.env` file. They are fetched from the Render service
  environment at run time, using an API key held in the macOS Keychain.
  `make render-auth-setup` stores that key, and the key's Render account must be able to
  read the `ondoway-api` service. A developer outside that account cannot satisfy this
  and has to be added to it — no local file substitutes. Editing `.env` to fix a
  "missing API key" is wasted effort; that is the single most common false lead here.
- **`valhalla-tiles`** — routing map data is git-ignored, so a fresh clone has none.
  `make valhalla-build-tiles` downloads several hundred MB of OSM extracts, and the first
  routing start then builds tiles, which can take well over an hour. State the size and
  the duration before starting it.
- **`docker-daemon` with no Colima VM** — preflight refuses to create one silently,
  because Colima's default sizing has already killed dockerd on this project (three Neo4j
  services plus a Valhalla tile load share the VM). Use
  `colima start --cpu 4 --memory 8`.

## After everything is green

```bash
make bootstrap
```

Then `make workbench` for the editorial workbench, or `make test` for the definitive
suite. Both additionally need `render-key`.

## Rules

- Never suggest `pip install`, a bare `pytest`, or any other direct tool invocation.
  Every command goes through a Make target. If no target exists, add one.
- Never tell the user to put a secret in `.env`. Nothing in the Make flow reads it.
