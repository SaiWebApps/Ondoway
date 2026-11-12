# Troubleshooting

Common issues and fixes for the Ondoway local development environment.

Every make target checks and starts what it needs before it runs: Docker, the
databases, the dev data, routing. When something is missing, the target stops and
names the fix. `make doctor` reports every requirement without changing anything.

---

## Docker & Neo4j

### "Cannot connect to the Docker daemon"

Docker Desktop is not running.

```bash
open -a Docker          # Start Docker Desktop (macOS)
```

Wait 10-15 seconds for it to finish starting, then retry.

### "Forbidden" error from Docker commands

The Docker CLI is pointing to the wrong context (likely `vessel`, an Apple-internal system).

```bash
docker context ls       # Check which context has the *
docker context use desktop-linux
```

### Neo4j container won't start

```bash
make db-status              # Check every local database's state
docker logs ondoway-neo4j   # View the dev database's Neo4j logs
```

Common causes:
- Port 7474 or 7687 already in use: `lsof -i :7687` to find the process
- The data volume is corrupted: delete it and start again

```bash
make db-reset DB=dev    # Delete the local dev graph (never cloud)
make db-up DB=dev       # Start it again, empty
```

`DB=` names one local database: `dev`, `test` or `workbench`, or the same three for
lanes 2, 3 and 4 (`dev2` to `workbench4`). It defaults to `dev`.

### The app can't connect to Neo4j, or authentication fails

Make targets take the Neo4j address and password from `config/profiles/`, not from a
`.env` file. No make target reads `.env`, so editing it changes nothing. The dev
settings are in `config/profiles/local`; the container's port and password are in
`docker-compose.yml`. Compare them:

```bash
grep NEO4J config/profiles/local                      # What the app uses
grep -A12 "^  neo4j:" docker-compose.yml | grep -E "7687|NEO4J_AUTH"   # What the container uses
```

`make config-status` also checks that each profile points at the right port and that
the Render environment loads.

If the passwords match but authentication still fails, the container's volume was
made with another password. Delete it and start again:

```bash
make db-reset DB=dev
make db-up DB=dev
```

---

## Python & Dependencies

### "command not found: python3" or Python version too old

```bash
python3 --version       # Need 3.11+
brew install python@3.11
```

### "pip: command not found" or wrong pip

Dependencies are managed by uv, never pip. If the environment is missing or
broken:

```bash
make sync               # Resolves uv.lock into .venv/
```

### Import errors after pulling new code

Dependencies may have changed. Reinstall:

```bash
make sync
```

### "No module named 'dotenv'" (or similar)

You're running system Python instead of the project environment. Run the script
through uv:

```bash
uv run python your_script.py
```

---

## API & Editor

### API returns 500 errors

Check the terminal where `make api` is running for the full traceback. Common causes:

- Neo4j stopped after the API started: stop the API and run `make api` again; it starts the database
- Missing schema or data: `make setup`
- Wrong Neo4j settings: see "The app can't connect to Neo4j" above

### Editor shows "connection failed"

The API server isn't running or isn't reachable:

```bash
make api                # Start the API server
curl http://localhost:8000/api/v1/schema/nodes  # Test connectivity
```

### Editor shows empty canvas (no nodes)

The database has no data. Provision it:

```bash
make setup              # Apply schema, seed data and the city corpus
```

### Port 8000 already in use

```bash
lsof -i :8000           # Find what's using the port
kill <PID>              # Kill it
make api                # Restart
```

---

## Tests

### Tests fail with "Neo4j not available"

Test targets start the databases they need. Run a test that needs the database
through make; bare `uv run pytest` does not start anything:

```bash
make test-file FILE=tests/test_x.py   # One test file
make test                             # The full suite
```

### Tests fail after schema changes

The test database may hold stale schema or data:

```bash
make db-reset DB=test   # Delete the local test graph (never cloud)
make test-file FILE=tests/test_x.py   # Starts it again and runs the file
```

---

## Full Reset

If nothing else works, a complete reset of the local development graph:

```bash
make db-reset DB=dev    # Stop the dev Neo4j and delete its data volume
make setup              # Full bootstrap from scratch
```
