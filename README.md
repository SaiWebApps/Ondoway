# Ondoway — Neo4j Graph API & Editor

Graph database schema, CRUD API, seed data, and interactive editor for the Ondoway audio tour app.

Implements **Schema_v3**: 9 node types and 14 relationship types, in 3 domains (Traveler's Vault, Global Atlas, Execution Bridge).

## Prerequisites

| Tool           | Purpose                          | Install (macOS)                          |
|----------------|----------------------------------|------------------------------------------|
| uv             | Python toolchain and virtualenv  | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Docker + daemon| the three Neo4j services, Valhalla| `brew install --cask docker`, or `brew install colima docker docker-compose` |
| Flutter        | the mobile app and its tests     | https://docs.flutter.dev/get-started/install |
| Xcode          | iOS simulator builds             | Mac App Store, then `xcode-select --install` |

Do not check these by hand, and do not install them by hand either. One command
reports every prerequisite the build system actually declares, and changes
nothing:

```bash
make doctor
```

It never fails — a fresh clone is *meant* to be missing things. It lists what is
missing, names the command that fixes each one, and exits cleanly. The same
requirement definitions gate every Make target, so what it reports green is what
those targets will find.

Then `make setup` installs and starts all of it.

> **Claude Code users**: `/setup-check` wraps the same command.

## New Developer Setup

Four steps from a fresh clone to a running app. Do them in order — step 3 needs
step 2.

### 1. Clone, then check the machine

```bash
git clone <repo-url> && cd ondoway && make doctor
```

### 2. Set everything up

```bash
make setup
```

One command. It installs whatever is missing — uv, the Docker CLI, Flutter —
creates a correctly sized Colima VM if you have no container runtime yet, starts
the three isolated local Neo4j services, downloads the routing map data, resolves
Flutter packages, fetches the Playwright browser, and provisions the committed
city data. It never touches Aura.

Setup does not start the routing engine. The first target that needs routing
(`test`, `api`, `workbench`, `tour-build`) starts it and waits. To start it
early, in the background, run `make valhalla-up`.

You do not need to know this list. That is the point: the build knows what it
needs, so it sets it up rather than telling you to go and read about it.

Two steps announce themselves before spending your time, because they are large:
the map data is several hundred megabytes, and the first routing start builds
tiles from it, which can take well over an hour. To skip the automatic setup and
just see what is missing, use `PREFLIGHT_AUTOFIX=0 make setup`, or `make doctor`.

**One step needs you at the keyboard: the Render credential.** Provider secrets
(Anthropic, OpenAI, Resend) are not stored in this repo and are not read from any
`.env` file — every command that needs one fetches the live Render service
environment over the API at run time. Setup opens the page that creates the key
and prompts you to paste it into your Keychain. Nothing types it for you.

The Render account behind that key must be able to read the `ondoway-api`
service. **If you are not on that account, ask the project owner to add you** —
no local file substitutes, and filling in `.env` will not help.

On Linux or in CI there is no Keychain, so export the key instead:

```bash
export RENDER_API_KEY=<a key that can read ondoway-api>
```

Targets that need it: `api`, `workbench`, `test`, `audit`, `flutter-ios`,
`test-live`, `setup-audio`, `testflight`, and everything cloud-facing. Targets
that do not: `lint`, `dashboard`, `tour-build` (unless you pass `GLUE=--haiku`),
the scoring targets, and `test-file` (unless you pass `LIVE=1`).

If any step fails, see [Troubleshooting](Docs/Markdown%20Docs/TROUBLESHOOTING.md).

### 3. Start the app

```bash
make api
```

Then open:

- **Graph editor**: http://localhost:8000/editor — create, edit, delete nodes and edges visually
- **API docs (Swagger)**: http://localhost:8000/docs — interactive API explorer
- **Read-only dashboard**: run `make dashboard` for a dashboard at http://localhost:8080
- **Editorial workbench**: run `make workbench`

## How prerequisites work

Every Make target opens by naming the capabilities it needs — a running daemon, a
specific database, map data, a credential. `scripts/preflight.py` resolves that
list in dependency order, probes each one for real (a live Cypher query, a health
endpoint, an installed executable), and **sets up whatever is missing**.

Failing with advice is the last resort, not the design. Reporting a missing
dependency and stopping just moves the work of knowing what this project needs
onto you. So every requirement can restore itself, and a test enforces that: add
one that cannot, and the suite fails until you justify it.

Three exceptions need a human, and they are the only three: pasting the Render
credential, approving the Render CLI's browser sign-in, and installing Xcode
itself. Each is a decision or a login that software should not make for you. With
no terminal attached — CI, a hook — these are skipped rather than left hanging on
input nobody can supply, and the instruction is printed instead.

A target therefore never begins work it cannot finish, and never reports a
dependency healthy without evidence.

```bash
make preflight                      # check everything, set up everything missing
make preflight-list                 # what each requirement is, and how it is restored
make doctor                         # check only; start and install nothing
PREFLIGHT_AUTOFIX=0 make <target>   # report only, for one command
```

To ask what a single target needs without running it:

```bash
python3 scripts/preflight.py --target workbench --no-fix
```

## Documentation

| Document | Description |
|----------|-------------|
| [Graph Editor Guide](Docs/Markdown%20Docs/GRAPH_EDITOR.md) | How to use the interactive graph editor — UI layout, creating nodes/edges, keyboard shortcuts, working with test data |
| API reference | Generated by the server itself: run `make api`, then open http://localhost:8000/docs |
| [Test Strategy](Docs/Markdown%20Docs/TEST_STRATEGY.md) | The five test tracks, the day-to-day loop, fixtures, adding new tests |
| [Troubleshooting](Docs/Markdown%20Docs/TROUBLESHOOTING.md) | Docker, Neo4j, Python venv, API, and editor issues with fixes |
| [Mobile design system](mobile/design/README.md) | The design source of truth and the core tokens for the Flutter theme |

## Make Commands

| Command                 | Description                                      |
|-------------------------|--------------------------------------------------|
| `make doctor`           | Report every prerequisite; change nothing        |
| `make preflight`        | Check every prerequisite and repair what it can  |
| `make preflight-list`   | What each requirement means and how it is fixed  |
| `make bootstrap`        | Provision a complete local development environment |
| `make render-auth-setup`| Store the Render API key that unlocks provider secrets |
| `make sync`             | Install Python dependencies                      |
| `make db-up DB=dev`     | Start dev Neo4j on 7687                          |
| `make db-up DB=test`    | Start test Neo4j on 7688                         |
| `make db-up DB=workbench` | Start workbench Neo4j on 7689                 |
| `make db-down DB=...`   | Stop one selected local Neo4j                    |
| `make db-status`        | Check Neo4j container status                     |
| `make db-reset DB=...`  | Delete only one selected local database volume   |
| `make test`             | Run the whole suite as five tracks (the definitive run) |
| `make test-file FILE=...` | Run one focused test file safely               |
| `make test-live`        | Run every test that calls a real provider        |
| `make api`              | Start FastAPI on port 8000                       |
| `make dashboard`        | Start read-only dashboard on port 8080           |
| `make lint`             | Run ruff, the process-file lint, and the duplicate-code check |
| `make format`           | Auto-format code                                 |
| `make help`             | Show all available commands                      |

## Project Structure

```
src/
├── connection.py              # Neo4j driver factory
├── city_registry.py           # Registered cities, backed by src/cities.json
├── main.py                    # CLI entry point (setup/verify/clean)
├── server.py                  # Dashboard HTTP server
├── schema/                    # Constraints, indexes and lens definitions, and applying them
├── seed/                      # Seed data: 8 parent lenses + 21 child lenses, a test user, Paris POIs, beats, a trip
├── verify/                    # Node/relationship counts, traversal checks, report output
├── tour/                      # Tour builder: POI selection, routing, beat ordering, script writing, quality checks
├── audio/                     # Text-to-speech providers, audio storage, per-stop voicing
├── onboard/                   # New-city onboarding: fetch sources, extract and draft beats
└── api/
    ├── app.py                 # FastAPI app factory
    ├── dependencies.py        # Neo4j driver injection
    ├── auth/                  # Sign-in (Apple, Google, email) and tokens
    ├── models/                # Pydantic models
    ├── crud/                  # Cypher operations, schema introspection, trip generation
    └── routes/                # API endpoint handlers
frontend/
├── index.html                 # Neo4j Schema Dashboard (read-only)
├── editor/index.html          # Interactive graph editor
├── review.html                # Editorial review workbench
├── onboard.html               # Onboard a new city
├── viewer.html                # Content viewer
├── auth.html                  # Sign in
└── join-family.html           # Join a family group
mobile/                        # The Flutter app (design system in mobile/design/)
tests/                         # The Python suite — see the Test Strategy doc
```
