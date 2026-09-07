"""Declared prerequisites for every Make target: probe, repair, or refuse.

Each supported target names the exact capabilities it needs.  This module turns
that list into a dependency-ordered plan, PROBES every capability for real -- a
live Cypher query, a health endpoint, an installed executable, a parsed JSON
status document -- repairs the ones it is allowed to repair, and exits non-zero
naming the exact command for the ones it cannot.

Two properties are load-bearing.

First, a probe never infers success from another command's silence.  The recipe
this replaced printed "neo4j ready at bolt://localhost:7687" and exited 0 with
the Docker daemon down, because its success message was simply the last
statement in a chain that ignored three failures.  Every probe here reports the
evidence it actually observed, and a repair re-probes before claiming anything.

Second, this file runs on the SYSTEM interpreter and imports only the standard
library, because the first thing it may have to report is that the project
interpreter does not exist yet.  Keep it runnable on Python 3.9: annotations are
postponed via ``__future__`` (so ``X | None`` is only ever a string here) and no
3.10+ runtime syntax may appear.  Nested same-quote f-strings are 3.12+; avoid
them.

Structural parsing only -- ``json.loads`` on ``--json`` output, ``Path.exists``,
exit codes.  Never match text out of another tool's human-readable output.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent
PROFILE_DIR = ROOT / "config" / "profiles"
COMPOSE_FILE = ROOT / "docker-compose.yml"
MAKEFILE = ROOT / "Makefile"

KEYCHAIN_SERVICE = "ondoway-render-api-key"
RENDER_API_KEY_URL = "https://dashboard.render.com/u/settings?add-api-key="
VALHALLA_STATUS_URL = "http://localhost:8002/status"
PLAYWRIGHT_CACHE = Path.home() / "Library" / "Caches" / "ms-playwright"

# The Colima VM is shared by three Neo4j services and Valhalla's tile load.
# Measured 2026-07-02: a smaller VM let dockerd die mid-gate.  Below this figure
# preflight warns; it never fails, because the sizing may be a deliberate call.
MIN_COLIMA_MEMORY_BYTES = 8 * 1024**3
COLIMA_MEMORY_GIB = 8
COLIMA_CPUS = 4

# Ports this project's own servers bind, per lane: the API/workbench, the
# dashboard, and the Flutter web dev server. The Makefile derives the same
# numbers from LANE=; tests/test_preflight.py pins the table disjoint from
# every shared service (Valhalla :8002, the tracker dashboards :8010-:8019,
# the Bolt ports).
LANE_SERVER_PORTS = {
    "": {"api": 8000, "dashboard": 8080, "flutter_web": 3000},
    "2": {"api": 8020, "dashboard": 8082, "flutter_web": 3002},
    "3": {"api": 8030, "dashboard": 8083, "flutter_web": 3003},
    "4": {"api": 8040, "dashboard": 8084, "flutter_web": 3004},
}

# Every server port, flat: the lane table above plus the workbench test
# shard's managed server (:8001).
SERVER_PORTS = tuple(
    sorted({8001} | {port for lane in LANE_SERVER_PORTS.values() for port in lane.values()})
)

DAEMON_WAIT_SECONDS = 180
DATABASE_WAIT_SECONDS = 180
VALHALLA_WAIT_SECONDS = 600


class Probe(NamedTuple):
    """One observation: did the capability answer, and what proved it."""

    ok: bool
    detail: str


@dataclass(frozen=True)
class Requirement:
    """A capability a Make target can declare, with how to verify and restore it.

    ``repair`` is expected to exist wherever a machine can honestly be brought to
    the required state without a human decision.  Reporting a missing dependency
    and stopping is a last resort, not a design: it makes the developer research
    what the build already knows.

    ``announce`` marks a repair that costs real time or disk (a large download, a
    toolchain install).  It is still performed automatically -- it just says what
    it is about to spend first, so a long wait is never a mystery.

    ``interactive`` marks a repair that needs a human at the keyboard: pasting a
    credential, approving a browser sign-in.  With no terminal attached the
    repair is skipped and the instruction is printed instead, so an automated
    run can never hang waiting for input nobody can give.
    """

    name: str
    summary: str
    probe: Callable[[], Probe]
    needs: tuple = ()
    repair: Callable[[], Probe] | None = None
    instruction: str = ""
    announce: str = ""
    interactive: bool = False


class DatabaseSpec(NamedTuple):
    key: str
    profile: str
    service: str
    container: str
    port: int


# Keyed by the DB= value the Makefile accepts.  The password is deliberately not
# duplicated here: it is read from the committed profile the target will execute
# under, so a profile edit cannot silently desynchronise from the probe.
DATABASES = (
    DatabaseSpec("dev", "local", "neo4j", "ondoway-neo4j", 7687),
    DatabaseSpec("test", "test", "neo4j-test", "ondoway-neo4j-test", 7688),
    DatabaseSpec("workbench", "workbench", "neo4j-workbench", "ondoway-neo4j-workbench", 7689),
    # One complete set of graphs per concurrent lane; see docker-compose.yml.
    # A lane writes only its own, so two tracks cannot wipe each other's data.
    # Adding lane 4 is one row per graph here plus its compose service.
    DatabaseSpec("dev2", "local2", "neo4j-dev2", "ondoway-neo4j-dev2", 7692),
    DatabaseSpec("test2", "test2", "neo4j-test2", "ondoway-neo4j-test2", 7690),
    DatabaseSpec("workbench2", "workbench2", "neo4j-workbench2", "ondoway-neo4j-workbench2", 7694),
    DatabaseSpec("dev3", "local3", "neo4j-dev3", "ondoway-neo4j-dev3", 7693),
    DatabaseSpec("test3", "test3", "neo4j-test3", "ondoway-neo4j-test3", 7691),
    DatabaseSpec("workbench3", "workbench3", "neo4j-workbench3", "ondoway-neo4j-workbench3", 7695),
    # Lane 4 is the multi-track sandbox lane (docs/adr/0002): the lane a second
    # worktree owns outright, never consumed as a shard by the definitive bar.
    DatabaseSpec("dev4", "local4", "neo4j-dev4", "ondoway-neo4j-dev4", 7696),
    DatabaseSpec("test4", "test4", "neo4j-test4", "ondoway-neo4j-test4", 7697),
    DatabaseSpec("workbench4", "workbench4", "neo4j-workbench4", "ondoway-neo4j-workbench4", 7698),
)
DATABASE_BY_KEY = {spec.key: spec for spec in DATABASES}

# Which lane this process is running in: "" for the main checkout, "2"/"3" for a
# concurrent worktree. The Makefile exports it from LANE=; nothing below needs to
# know more than the suffix, so a new lane costs no branching here.
LANE = os.environ.get("ONDOWAY_LANE", "").strip()


def _lane_database(role: str) -> DatabaseSpec:
    """This lane's graph for a role -- `dev` -> dev2 on lane 2, dev on main."""
    return DATABASE_BY_KEY[f"{role}{LANE}"]


# ── process helpers ──────────────────────────────────────────────────────────


def _run(
    argv: Sequence[str],
    *,
    timeout: int | None = 60,
    env: dict | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess:
    """Run a command, capture everything, and never raise on a non-zero exit."""
    try:
        return subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
            cwd=str(cwd) if cwd else None,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(list(argv), returncode=127, stdout="", stderr=str(exc))


def _stream(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: int = 1800,
    env: dict | None = None,
) -> int:
    """Run a command with its output visible; a repair is never silent.

    Always bounded. A repair with no timeout is a build that hangs forever with
    one line printed and no way to tell progress from a stall -- `render login`
    waiting on a browser approval nobody is there to give, or a curl against a
    network that blackholes rather than refuses.
    """
    try:
        return subprocess.run(
            list(argv),
            check=False,
            cwd=str(cwd) if cwd else None,
            timeout=timeout,
            env=env,
        ).returncode
    except subprocess.TimeoutExpired:
        print(f"    timed out after {timeout}s: {' '.join(argv[:3])}", file=sys.stderr)
        return 124
    except OSError as exc:
        print(f"    {exc}", file=sys.stderr)
        return 127


def _venv_python() -> Path:
    return ROOT / ".venv" / "bin" / "python"


def _compose(*args: str) -> list:
    return ["docker", "compose", "-f", str(COMPOSE_FILE), "--project-directory", str(ROOT), *args]


def _valhalla_root() -> Path:
    """The main checkout, even from a worktree -- where the tiles physically live.

    Mirrors the Makefile's VALHALLA_ROOT so a worktree can never start a second
    routing engine against an empty tile directory.
    """
    result = _run(["git", "rev-parse", "--git-common-dir"], cwd=ROOT, timeout=15)
    if result.returncode != 0:
        return ROOT
    common = Path(result.stdout.strip())
    if not common.is_absolute():
        common = (ROOT / common).resolve()
    return common.parent


def _read_profile(name: str) -> dict:
    """Load one committed non-secret profile as KEY=VALUE pairs."""
    path = PROFILE_DIR / name
    values: dict = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


# Every probe here targets localhost. urllib would still send it to a configured
# HTTP proxy and resolve the PROXY's hostname, so on a proxied machine a healthy
# local service reports dead. An explicit empty ProxyHandler opts out.
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _http_ok(url: str, timeout: int = 3) -> bool:
    try:
        with _DIRECT.open(url, timeout=timeout) as response:  # localhost, never proxied
            return 200 <= response.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _wait_for(check_fn: Callable[[], bool], seconds: int, *, interval: float = 2.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check_fn():
            return True
        time.sleep(interval)
    return check_fn()


# ── toolchain ────────────────────────────────────────────────────────────────


def _brew() -> str | None:
    return shutil.which("brew")


# Where installers drop binaries.  Used ONLY to explain an install that landed
# somewhere the developer's PATH does not reach -- never to widen the PATH a
# probe searches.  A probe must see exactly what the Make recipe will see: if it
# searched extra directories, it could report a tool present that the recipe then
# fails to run, which is the same false green this file exists to eliminate.
INSTALL_DIRECTORIES = (
    Path.home() / ".local" / "bin",
    Path.home() / ".cargo" / "bin",
    Path("/opt/homebrew/bin"),
    Path("/usr/local/bin"),
)


def _installed_off_path(tool: str) -> Path | None:
    """Find a tool that exists on disk but is not reachable through PATH."""
    if shutil.which(tool):
        return None
    for directory in INSTALL_DIRECTORIES:
        candidate = directory / tool
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def _after_install(tool: str, probe: Callable[[], Probe]) -> Probe:
    """Re-probe once an installer has run, and be precise about a PATH miss."""
    result = probe()
    if result.ok:
        return result
    stray = _installed_off_path(tool)
    if stray is not None:
        return Probe(
            False,
            f"installed to {stray.parent}, which is not on your PATH -- add it "
            "(or open a new shell) and repeat the command",
        )
    return result


def _probe_uv() -> Probe:
    if not shutil.which("uv"):
        return Probe(False, "not on PATH")
    result = _run(["uv", "--version"], timeout=20)
    if result.returncode != 0:
        return Probe(False, "installed but not runnable")
    return Probe(True, result.stdout.strip() or "installed")


def _repair_uv() -> Probe:
    if _brew():
        print("    -> brew install uv")
        _stream(["brew", "install", "uv"])
    else:
        print("    -> curl -LsSf https://astral.sh/uv/install.sh | sh")
        _stream(["/bin/sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"])
    return _after_install("uv", _probe_uv)


REQUIRED_MODULES = ("neo4j", "fastapi", "httpx", "anthropic", "pytest", "playwright")


# Locate the distributions rather than importing them.  `make lint` runs in a
# tight edit loop, and importing this set costs seconds; find_spec answers the
# only question that matters -- is it installed -- in one interpreter start.
_DEPENDENCY_SCRIPT = """
import importlib.util, platform, sys
missing = [m for m in {modules!r} if importlib.util.find_spec(m) is None]
if missing:
    sys.stdout.write("MISSING:" + ",".join(missing))
else:
    sys.stdout.write("OK:" + platform.python_version())
"""


def _probe_python_deps() -> Probe:
    python = _venv_python()
    if not python.is_file():
        return Probe(False, "no .venv")
    script = _DEPENDENCY_SCRIPT.format(modules=list(REQUIRED_MODULES))
    result = _run([str(python), "-c", script], timeout=120)
    answer = result.stdout.strip()
    if result.returncode != 0 or not answer:
        return Probe(False, "the project interpreter could not be queried")
    label, _, payload = answer.partition(":")
    if label != "OK":
        return Probe(False, f"packages not installed: {payload}")
    if not (ROOT / ".venv" / "bin" / "ruff").is_file():
        return Probe(False, "ruff missing (the dev extra is not installed)")
    return Probe(True, f"Python {payload}")


def _repair_python_deps() -> Probe:
    print("    -> uv sync --extra test --extra dev --extra aws")
    if _stream(["uv", "sync", "--extra", "test", "--extra", "dev", "--extra", "aws"], cwd=ROOT):
        return Probe(False, "uv sync failed -- see the output above")
    return _probe_python_deps()


def _probe_docker_cli() -> Probe:
    if not shutil.which("docker"):
        return Probe(False, "not on PATH")
    result = _run(["docker", "compose", "version", "--short"], timeout=30)
    if result.returncode != 0:
        return Probe(False, "docker is present but Compose v2 is missing")
    return Probe(True, "compose " + (result.stdout.strip() or "v2"))


def _repair_docker_cli() -> Probe:
    if not _brew():
        return Probe(
            False,
            "Homebrew is not installed, so this cannot be installed for you -- "
            'install it first: /bin/bash -c "$(curl -fsSL '
            'https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"',
        )
    print("    -> brew install colima docker docker-compose")
    _stream(["brew", "install", "colima", "docker", "docker-compose"])
    return _after_install("docker", _probe_docker_cli)


def _probe_docker_daemon() -> Probe:
    result = _run(["docker", "info", "--format", "{{json .ServerVersion}}"], timeout=30)
    if result.returncode != 0:
        return Probe(False, "daemon not responding")
    try:
        version = json.loads(result.stdout.strip() or '""')
    except json.JSONDecodeError:
        version = ""
    return Probe(True, f"engine {version}" if version else "responding")


def _colima_profiles() -> list:
    """Parse ``colima list --json`` -- one JSON document per line."""
    if not shutil.which("colima"):
        return []
    result = _run(["colima", "list", "--json"], timeout=30)
    if result.returncode != 0:
        return []
    profiles = []
    for line in result.stdout.splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            entry = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            profiles.append(entry)
    return profiles


def _repair_docker_daemon() -> Probe:
    profiles = _colima_profiles()
    if profiles:
        stopped = [p for p in profiles if p.get("status") != "Running"]
        target = stopped[0] if stopped else profiles[0]
        name = str(target.get("name", "default"))
        print(f"    -> colima start {name}   (existing VM; its own sizing is reused)")
        _stream(["colima", "start", name])
    elif shutil.which("colima"):
        # No VM exists yet.  The hazard was never "create a VM", it was Colima's
        # small DEFAULT sizing: three Neo4j services plus a Valhalla tile load
        # killed dockerd once at that size (2026-07-02).  Naming the sizing
        # removes the hazard, so create it rather than sending the developer away
        # to find a number this file already knows.
        print(f"    -> colima start --cpu {COLIMA_CPUS} --memory {COLIMA_MEMORY_GIB}")
        print("       (no Colima VM existed; creating one sized for this project)")
        _stream(
            [
                "colima",
                "start",
                "--cpu",
                str(COLIMA_CPUS),
                "--memory",
                str(COLIMA_MEMORY_GIB),
            ]
        )
    elif sys.platform == "darwin" and Path("/Applications/Docker.app").exists():
        print("    -> open -a Docker")
        _stream(["open", "-a", "Docker"])
    else:
        return Probe(False, "no Docker Desktop or Colima installation found")

    print("    waiting for the Docker daemon...")
    if not _wait_for(lambda: _probe_docker_daemon().ok, DAEMON_WAIT_SECONDS):
        return Probe(False, f"daemon did not come up within {DAEMON_WAIT_SECONDS}s")
    return _probe_docker_daemon()


def _colima_memory_warnings() -> list:
    warnings = []
    for profile in _colima_profiles():
        memory = profile.get("memory")
        if isinstance(memory, int) and memory < MIN_COLIMA_MEMORY_BYTES:
            name = profile.get("name", "default")
            gib = memory / 1024**3
            warnings.append(
                f"Colima VM {name} has {gib:.1f} GiB; this project assumes 8 GiB "
                "(three Neo4j services plus Valhalla). Resize with: "
                "colima stop && colima start --memory 8"
            )
    return warnings


# ── databases ────────────────────────────────────────────────────────────────


def _container_running(container: str, *, attempts: int = 3) -> bool | None:
    """Is this exact container running?  None means Docker could not be asked.

    The three-way answer is the point.  Collapsing a failed query into False
    reports a healthy container as missing -- the mirror of the bug this file
    exists to prevent, and just as misleading. Measured: under a burst of rapid
    checks a single `docker ps` failed while the container had been up 36 hours,
    and a two-state answer called that a stopped database.
    """
    for attempt in range(attempts):
        result = _run(["docker", "ps", "--format", "{{json .}}"], timeout=30)
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                text = line.strip()
                if not text:
                    continue
                try:
                    entry = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(entry, dict) and entry.get("Names") == container:
                    return True
            return False
        if attempt + 1 < attempts:
            time.sleep(1.0)
    return None


def _cypher_answers(spec: DatabaseSpec) -> bool:
    """Run a real query inside the container -- the only honest readiness test."""
    password = _read_profile(spec.profile).get("NEO4J_PASSWORD", "")
    if not password:
        return False
    result = _run(
        _compose(
            "exec",
            "-T",
            spec.service,
            "cypher-shell",
            "-u",
            "neo4j",
            "-p",
            password,
            "RETURN 1",
        ),
        timeout=30,
    )
    return result.returncode == 0


def _probe_database(spec: DatabaseSpec) -> Callable[[], Probe]:
    def probe() -> Probe:
        profile = _read_profile(spec.profile)
        expected = f"bolt://localhost:{spec.port}"
        configured = profile.get("NEO4J_URI", "")
        if configured != expected:
            return Probe(
                False,
                f"profile {spec.profile} points at {configured!r}, not {expected}",
            )
        # Ask the database itself first: a successful query is proof, and it
        # cannot be undone by a flaky `docker ps`. The container state is only
        # consulted to explain a failure, never to declare one.
        if _cypher_answers(spec):
            return Probe(True, f"{expected} answering")
        running = _container_running(spec.container)
        if running is None:
            return Probe(False, "Docker did not answer, so the graph's state is unknown")
        if not running:
            return Probe(False, f"container {spec.container} is not running")
        return Probe(False, "container is up but Neo4j is not answering queries")

    return probe


def _repair_database(spec: DatabaseSpec) -> Callable[[], Probe]:
    def repair() -> Probe:
        print(f"    -> docker compose up -d {spec.service}")
        if _stream(_compose("up", "-d", spec.service), cwd=ROOT):
            return Probe(False, f"compose could not start {spec.service}")
        print(f"    waiting for Neo4j on port {spec.port} to answer a query...")
        if not _wait_for(lambda: _cypher_answers(spec), DATABASE_WAIT_SECONDS):
            return Probe(
                False,
                f"{spec.service} never answered within {DATABASE_WAIT_SECONDS}s -- "
                f"check: docker compose logs {spec.service}",
            )
        return _probe_database(spec)()

    return repair


# ── dev data ─────────────────────────────────────────────────────────────────


def _local_profile_env() -> dict:
    env = dict(os.environ)
    # A leaked ONBOARD_DATA_ROOT from an earlier hermetic run redirects the parity
    # check at an empty tree, where it compares nothing and reports OK.
    for leaked in ("ONBOARD_DATA_ROOT", "ONBOARD_REGISTRY_PATH"):
        env.pop(leaked, None)
    # This lane's dev graph, not the canonical one: provisioning on lane 2 must
    # write :7692, or the isolation the lane exists for is silently undone.
    env.update(_read_profile(_lane_database("dev").profile))
    return env


def _committed_cities() -> list:
    """City directories the parity check is supposed to compare."""
    data = ROOT / "data"
    if not data.is_dir():
        return []
    return sorted(d.name for d in data.iterdir() if d.is_dir() and (d / "poi-raw.json").is_file())


def _probe_dev_data() -> Probe:
    python = _venv_python()
    if not python.is_file():
        return Probe(False, "no .venv")
    # The parity check loops over cities and reports OK when the loop is empty,
    # so a zero-city run "passes" having compared nothing. Establish there is
    # something to compare before believing the result.
    cities = _committed_cities()
    if not cities:
        return Probe(False, "no committed city data found under data/ -- nothing to compare")
    result = _run(
        [str(python), "-m", "scripts.db_parity"],
        timeout=300,
        env=_local_profile_env(),
        cwd=ROOT,
    )
    if result.returncode != 0:
        return Probe(False, "dev graph does not match the committed city data")
    return Probe(True, f"dev graph matches {len(cities)} committed cities")


def _repair_dev_data() -> Probe:
    dev = _lane_database("dev")
    print(f"    -> scripts/ensure_dev_data.py   (WRITES to the {dev.key} graph on :{dev.port})")
    # The same environment the probe uses. Without it the script refuses outright.
    if _stream(
        [str(_venv_python()), "scripts/ensure_dev_data.py"],
        cwd=ROOT,
        env=_local_profile_env(),
    ):
        return Probe(False, "provisioning failed -- see the output above")
    return _probe_dev_data()


# ── routing ──────────────────────────────────────────────────────────────────


def _tile_directory() -> Path:
    return _valhalla_root() / "valhalla" / "custom_files"


def _probe_valhalla_tiles() -> Probe:
    tiles = _tile_directory()
    if (tiles / "valhalla_tiles.tar").is_file():
        return Probe(True, "valhalla_tiles.tar present")
    built = tiles / "valhalla_tiles"
    if built.is_dir():
        # Count real tiles, not "any file": a .DS_Store dropped in by Finder is
        # enough to make an empty directory look populated.
        real = [entry for entry in built.iterdir() if not entry.name.startswith(".")]
        if real:
            return Probe(True, f"valhalla_tiles/ has {len(real)} entries")
    extracts = sorted(tiles.glob("*.osm.pbf")) if tiles.is_dir() else []
    if extracts:
        return Probe(True, f"{len(extracts)} OSM extract(s); tiles build on first start")
    return Probe(False, f"no map data in {tiles}")


OSM_EXTRACTS = (
    (
        "ile-de-france-latest.osm.pbf",
        "https://download.geofabrik.de/europe/france/ile-de-france-latest.osm.pbf",
    ),
    (
        "new-york.osm.pbf",
        "https://download.bbbike.org/osm/bbbike/NewYork/NewYork.osm.pbf",
    ),
)


def _repair_valhalla_tiles() -> Probe:
    """Download the OSM extracts the routing engine builds its tiles from.

    Written to a temporary name and moved into place only on success, so an
    interrupted download can never leave a truncated extract that the tile build
    would then happily consume.
    """
    tiles = _tile_directory()
    tiles.mkdir(parents=True, exist_ok=True)
    for filename, url in OSM_EXTRACTS:
        target = tiles / filename
        if target.is_file() and target.stat().st_size > 0:
            print(f"    {filename} already present")
            continue
        partial = tiles / (filename + ".partial")
        print(f"    -> downloading {filename}")
        if _stream(
            ["curl", "-L", "--fail", "--connect-timeout", "20", "-o", str(partial), url],
            timeout=3600,
        ):
            partial.unlink(missing_ok=True)
            return Probe(False, f"could not download {filename}")
        partial.replace(target)
    return _probe_valhalla_tiles()


def _probe_valhalla() -> Probe:
    if not _http_ok(VALHALLA_STATUS_URL):
        return Probe(False, "no healthy routing endpoint on :8002")
    return Probe(True, "healthy on :8002")


def _repair_valhalla() -> Probe:
    root = _valhalla_root()
    print("    -> docker compose up -d valhalla")
    started = _stream(
        [
            "docker",
            "compose",
            "-f",
            str(root / "docker-compose.yml"),
            "--project-directory",
            str(root),
            "up",
            "-d",
            "valhalla",
        ],
        cwd=root,
    )
    if started:
        return Probe(False, "compose could not start valhalla")
    print("    waiting for the routing endpoint (a first start builds tiles)...")
    if not _wait_for(lambda: _http_ok(VALHALLA_STATUS_URL), VALHALLA_WAIT_SECONDS):
        # Still building is not the same as dead, and saying "failed" for the
        # former sends the developer to read container logs that show healthy
        # progress. The announcement says this takes over an hour; the wait
        # cannot, so distinguish the two states instead of guessing.
        if _container_running("ondoway-valhalla"):
            return Probe(
                False,
                "still building map tiles after "
                f"{VALHALLA_WAIT_SECONDS // 60} min. This takes over an hour on a "
                "first run and is proceeding normally -- leave it running and "
                "repeat this command later (watch: make valhalla-status)",
            )
        return Probe(
            False,
            "the routing container is not running -- check: docker compose logs valhalla",
        )
    return _probe_valhalla()


# ── credentials ──────────────────────────────────────────────────────────────


def _probe_render_key() -> Probe:
    """Confirm a credential EXISTS.  Its value is never read, logged, or printed.

    Must stay in step with ``keychain_api_key`` in scripts/dev_env.py: Keychain
    first, then RENDER_API_KEY.  If these two disagree, preflight passes a target
    that then dies on a missing credential -- the precise failure mode this whole
    mechanism exists to remove.
    """
    if sys.platform == "darwin":
        account = os.environ.get("USER") or getpass.getuser()
        result = _run(
            ["security", "find-generic-password", "-a", account, "-s", KEYCHAIN_SERVICE],
            timeout=30,
        )
        if result.returncode == 0:
            return Probe(True, "Keychain item present (value not read)")
    if os.environ.get("RENDER_API_KEY", "").strip():
        return Probe(True, "RENDER_API_KEY is set (value not read)")
    if sys.platform != "darwin":
        return Probe(False, "no RENDER_API_KEY, and this platform has no Keychain")
    return Probe(False, "no Render API key in the login Keychain")


def _repair_render_key() -> Probe:
    """Hand the developer the interactive credential flow; never type the secret.

    Opens the page the key is created on and runs the same prompt
    `make render-auth-setup` runs. The developer pastes their own key into the
    OS prompt; nothing here reads, stores, or transmits it.
    """
    if sys.platform != "darwin":
        return Probe(False, "no Keychain on this platform; export RENDER_API_KEY")
    print("    A Render API key is needed. Opening the page that creates one.")
    print(f"    {RENDER_API_KEY_URL}")
    if shutil.which("open"):
        _run(["open", RENDER_API_KEY_URL], timeout=20)
    print("    Keychain will now prompt you to paste the key (it prompts twice).")
    interpreter = _venv_python() if _venv_python().is_file() else Path("python3")
    if _stream([str(interpreter), "scripts/dev_env.py", "auth-setup"], cwd=ROOT):
        return Probe(False, "the key was not stored, or it cannot read ondoway-api")
    return _probe_render_key()


# ── Render CLI (deploy watching only) ────────────────────────────────────────
# Distinct from the API key above, and easy to confuse with it.  Secrets are
# fetched over the REST API and need no CLI; the CLI is used by exactly one
# thing -- watching a deploy to a terminal state -- and it carries its OWN
# browser-based login, stored in ~/.render/.


def _probe_render_cli() -> Probe:
    if not shutil.which("render"):
        return Probe(False, "not on PATH")
    result = _run(["render", "--version"], timeout=30)
    if result.returncode != 0:
        return Probe(False, "installed but not runnable")
    return Probe(True, result.stdout.strip().splitlines()[0] if result.stdout.strip() else "ok")


def _repair_render_cli() -> Probe:
    if not _brew():
        return Probe(
            False,
            "Homebrew is not installed, so this cannot be installed for you -- "
            'install it first: /bin/bash -c "$(curl -fsSL '
            'https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"',
        )
    print("    -> brew install render")
    _stream(["brew", "install", "render"])
    return _after_install("render", _probe_render_cli)


def _probe_render_cli_auth() -> Probe:
    config = Path.home() / ".render" / "cli.yaml"
    if not config.is_file() or config.stat().st_size == 0:
        return Probe(False, "the Render CLI has not been signed in")
    return Probe(True, "CLI config present (session validity not checked)")


def _repair_render_cli_auth() -> Probe:
    """Start the CLI's own sign-in.  The browser approval is the developer's.

    Interactive by declaration, so an unattended run prints the instruction
    instead of blocking on a sign-in nobody is present to approve.
    """
    print("    -> render login   (opens your browser; approve it there)")
    if _stream(["render", "login"]):
        return Probe(False, "sign-in did not complete")
    return _probe_render_cli_auth()


# ── ports ────────────────────────────────────────────────────────────────────
# A server target cannot start if something else already holds its port, and the
# failure ("address already in use") says nothing about what to do. So the port
# is a declared requirement like any other.
#
# "Satisfied" means free OR already serving THIS project -- `make workbench`
# deliberately reuses a healthy API on :8000 rather than restarting it, so a
# blanket "must be free" would break the reuse it was designed around.
#
# The repair frees only a listener it can identify as this project's own server.
# A foreign process is named and left alone: killing an unidentified process to
# claim a port is not self-sufficiency, it is collateral damage. Measured
# precedent in this repo: a broader match once killed the user's desktop app,
# which merely held client sockets to the same port.

SERVER_MARKERS = ("uvicorn", "src.api.app", "src.server")


def _is_our_server(command: str) -> bool:
    """Is this command line one of THIS checkout's servers?

    The generic marker is necessary but nowhere near sufficient: `uvicorn` and
    `src.server` are conventions shared across the Python ecosystem, so another
    project's dev server on the same port matched and was killed as "our stale
    one". The command must also name this checkout.
    """
    if not any(marker in command for marker in SERVER_MARKERS):
        return False
    # Full path with a trailing separator. A bare directory-name match makes
    # "ondoway" a substring of "ondoway-wt1", so the main checkout would identify
    # a sibling worktree's server as its own and kill it.
    return f"{ROOT}/" in command


class PortHolder(NamedTuple):
    pid: int
    command: str


UNKNOWN = "unknown"  # the query could not be answered -- never "nothing there"


def _port_listeners(port: int) -> list | str:
    """Every process LISTENing on this port.

    Three answers, not two: a list of holders (possibly empty, meaning the port
    is free) and UNKNOWN for "lsof could not be asked". lsof exits 1 legitimately
    when the port is free and _run reports 127 when the command timed out or is
    missing -- collapsing those certified a port FREE that something was holding,
    and the server then failed to bind.

    Every PID, not the first: `lsof -t` prints one per line, and a reloading
    uvicorn (which is what `make api` runs) listens from parent AND child. Taking
    only the first signalled half the server, then blamed the PID that had
    already exited.
    """
    for attempt in range(3):
        result = _run(["lsof", "-tiTCP:" + str(port), "-sTCP:LISTEN"], timeout=30)
        if result.returncode == 0:
            pids = sorted({int(tok) for tok in result.stdout.split() if tok.isdigit()})
            return [
                PortHolder(
                    pid, _run(["ps", "-p", str(pid), "-o", "command="], timeout=30).stdout.strip()
                )
                for pid in pids
            ]
        if result.returncode == 1:  # lsof's documented "no match"
            return []
        if attempt < 2:
            time.sleep(1.0)
    return UNKNOWN


DEV_GRAPH_PORT = 7687


def _responding(port: int, *, attempts: int = 3) -> bool:
    """Is ANYTHING alive on this port?  Liveness, never identity.

    Dead means one thing only: every attempt was actively REFUSED. Connection
    refused is the kernel saying nothing is bound. Any other outcome -- a
    timeout, a dropped SYN, the checker running out of file descriptors -- is
    the question failing, not an answer, and it must never be read as "nothing
    is there". Measured: with fds exhausted against a live, accepting server,
    a bare `except OSError -> False` classified it stale and sent it SIGKILL.

    Asking one FastAPI route was not liveness either: `make dashboard`
    (src/server.py) serves only /api/status and has no healthz, and a healthy
    API whose healthz does a graph round-trip answers in ~3s under load. Both
    read as dead and were killed.

    This mirrors `_port_listeners`, which retries and returns UNKNOWN rather
    than guess -- for the same reason, biased the same way. The dangerous
    direction must never have less rigour than the safe one.

    ASSUMES every server here binds 127.0.0.1: a ``::1``-only listener is seen
    by lsof but refuses on IPv4, so it would read as dead. True at all three
    call sites today (Makefile's uvicorn `--host 127.0.0.1`, src/server.py), and
    nothing enforces it -- so if a server ever binds v6-only, fix this first.
    """
    refusals = 0
    for attempt in range(attempts):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=3):
                return True
        except ConnectionRefusedError:
            refusals += 1
        except OSError:
            return True  # the question failed; that is not evidence of absence
        if attempt + 1 < attempts:
            time.sleep(0.5)
    return refusals < attempts


def _serves_this_project(port: int, *, graph_port: int = DEV_GRAPH_PORT) -> bool:
    """Is the server on this port OURS, on the graph we expect?

    A 2xx on /healthz only proves something answered. The body names the graph,
    which is the whole reason the endpoint reports it -- without checking it,
    another project's server on the same convention, or ours pointed at the
    wrong database, passed as a reusable instance.
    """
    try:
        with _DIRECT.open(f"http://localhost:{port}/api/v1/healthz", timeout=2) as response:
            if not 200 <= response.status < 300:
                return False
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(body, dict) or not body.get("neo4j_connected"):
        return False
    return f":{graph_port}" in str(body.get("neo4j_uri", ""))


def _probe_port(
    port: int, *, reuse_ok: bool = False, graph_port: int = DEV_GRAPH_PORT
) -> Callable[[], Probe]:
    """`reuse_ok` is opt-in, and only `workbench` opts in.

    An occupied port that answers our health check is fine for the workbench,
    which deliberately reuses a running API. Every other server target BINDS the
    port, so treating "something of ours is already there" as satisfied certified
    a port that cannot be bound -- and the failure then surfaced late, which is
    exactly what declaring the port up front was meant to prevent.

    ``graph_port`` is the dev graph a reusable server must be connected to --
    each lane's own, so a lane 4 workbench can never satisfy itself with a
    server reading another lane's corpus.
    """

    def probe() -> Probe:
        holders = _port_listeners(port)
        if holders == UNKNOWN:
            return Probe(False, f"could not determine what is using port {port}")
        if not holders:
            return Probe(True, f"port {port} is free")
        if reuse_ok and _serves_this_project(port, graph_port=graph_port):
            return Probe(True, f"port {port} already serving this project")
        first = holders[0]
        summary = first.command.split()[0] if first.command else "unknown"
        extra = f" and {len(holders) - 1} more" if len(holders) > 1 else ""
        return Probe(False, f"port {port} held by PID {first.pid} ({summary}){extra}")

    return probe


def _repair_port(
    port: int, *, reuse_ok: bool = False, graph_port: int = DEV_GRAPH_PORT
) -> Callable[[], Probe]:
    def repair() -> Probe:
        holders = _port_listeners(port)
        if holders == UNKNOWN:
            return Probe(False, f"could not determine what is using port {port}")
        if not holders:
            return _probe_port(port, reuse_ok=reuse_ok, graph_port=graph_port)()
        strangers = [h for h in holders if not _is_our_server(h.command)]
        if strangers:
            held = strangers[0]
            return Probe(
                False,
                f"port {port} is held by PID {held.pid}, which is not one of this "
                f"checkout's servers -- stop it yourself: {held.command[:60]}",
            )
        # Ours is not the same as idle. A server that still answers is a server
        # somebody is using -- a sibling session's `make test-workbench` on :8001,
        # or a workbench the owner has open. Killing it because the command line
        # matches is the incident family CLAUDE.md records (a live container
        # destroyed by a checkout that merely had the right to). Only a server
        # that has stopped answering is stale enough to stop.
        if _responding(port):
            return Probe(
                False,
                f"port {port} is held by a LIVE server of this checkout (PID "
                f"{holders[0].pid}). It is accepting connections, so another "
                "session is probably using it -- stop it yourself if it is yours: "
                f"kill {holders[0].pid}",
            )
        for holder in holders:
            print(f"    -> stopping this checkout's stale server on :{port} (PID {holder.pid})")
            _run(["kill", str(holder.pid)], timeout=30)
        if not _wait_for(lambda: _port_listeners(port) == [], 20, interval=1.0):
            # Escalation needs a FRESH look, taken once and used for everything
            # below. Three distinct answers, and only one of them is permission:
            #   UNKNOWN -> the question failed; refuse, as everywhere else here.
            #   []      -> it went free during the wait; there is nothing to kill.
            #   holders -> still held; SIGKILL exactly the PIDs still listed.
            # Signalling the ORIGINAL snapshot instead re-signals processes that
            # have already exited, and the OS may have reissued those PIDs to
            # something else by then -- a SIGKILL to an innocent process, silent
            # and untraceable to this code.
            remaining = _port_listeners(port)
            if remaining == UNKNOWN:
                return Probe(
                    False,
                    f"could not confirm port {port} was released -- refusing to "
                    "escalate on an unanswered query",
                )
            if remaining:
                # BOTH gates again, on the CURRENT holders. They ran on the first
                # snapshot only, so whatever took the port during the 20s wait was
                # force-killed unexamined -- and binding a just-freed port is
                # ordinary, not rare. Measured: a sibling worktree's live server
                # bound :8000 mid-wait and was SIGKILLed, though this code's own
                # rule classifies it a stranger and never asked if it was alive.
                strangers_now = [h for h in remaining if not _is_our_server(h.command)]
                if strangers_now:
                    held = strangers_now[0]
                    return Probe(
                        False,
                        f"port {port} was taken during the wait by PID {held.pid}, "
                        f"which is not this checkout's -- leaving it alone: "
                        f"{held.command[:60]}",
                    )
                if _responding(port):
                    return Probe(
                        False,
                        f"port {port} was taken during the wait by a LIVE server "
                        f"(PID {remaining[0].pid}) -- leaving it alone",
                    )
                for holder in remaining:
                    print(f"    -> forcing PID {holder.pid} off :{port}")
                    _run(["kill", "-9", str(holder.pid)], timeout=30)
                # SIGTERM gets 20s to take effect; SIGKILL got none, so a
                # successful force-kill could report "still held" simply because
                # the kernel had not torn the socket down yet -- a phantom failure.
                _wait_for(lambda: _port_listeners(port) == [], 10, interval=0.5)
                still = _port_listeners(port)
                if still == UNKNOWN or still:
                    pids = ", ".join(str(h.pid) for h in remaining)
                    return Probe(False, f"port {port} still held after stopping PID(s) {pids}")
        return _probe_port(port, reuse_ok=reuse_ok, graph_port=graph_port)()

    return repair


# ── mobile toolchain ─────────────────────────────────────────────────────────


def _probe_flutter() -> Probe:
    if not shutil.which("flutter"):
        return Probe(False, "not on PATH")
    return Probe(True, "installed")


def _repair_flutter() -> Probe:
    if not _brew():
        return Probe(
            False,
            "Homebrew is not installed, so this cannot be installed for you -- "
            'install it first: /bin/bash -c "$(curl -fsSL '
            'https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"',
        )
    print("    -> brew install --cask flutter")
    _stream(["brew", "install", "--cask", "flutter"])
    return _after_install("flutter", _probe_flutter)


def _probe_flutter_deps() -> Probe:
    if (ROOT / "mobile" / ".dart_tool" / "package_config.json").is_file():
        return Probe(True, "packages resolved")
    return Probe(False, "mobile packages are not resolved")


def _repair_flutter_deps() -> Probe:
    print("    -> flutter pub get")
    env = dict(os.environ)
    env["NO_PROXY"] = "pub.dev,*.pub.dev"
    env["no_proxy"] = "pub.dev,*.pub.dev"
    if _stream(["flutter", "pub", "get"], cwd=ROOT / "mobile", env=env, timeout=900):
        return Probe(False, "flutter pub get failed")
    return _probe_flutter_deps()


def _probe_cocoapods() -> Probe:
    # Only the iOS-build targets declare this, and those are macOS-only anyway;
    # off macOS it is a no-op so a Linux dart-test run is never blocked by it.
    if sys.platform != "darwin":
        return Probe(True, "not needed off macOS")
    if not shutil.which("pod"):
        return Probe(False, "not on PATH")
    result = _run(["pod", "--version"], timeout=60)
    if result.returncode != 0:
        return Probe(False, "pod is present but does not run")
    return Probe(True, f"cocoapods {result.stdout.strip()}")


def _repair_cocoapods() -> Probe:
    if not _brew():
        return Probe(
            False,
            "Homebrew is not installed, so this cannot be installed for you -- "
            'install it first: /bin/bash -c "$(curl -fsSL '
            'https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"',
        )
    # The Homebrew cask ships its own Ruby, sidestepping the ancient macOS system
    # Ruby (2.6) that breaks a plain `gem install cocoapods`.
    print("    -> brew install cocoapods")
    _stream(["brew", "install", "cocoapods"])
    return _after_install("pod", _probe_cocoapods)


def _probe_xcode() -> Probe:
    if sys.platform != "darwin":
        return Probe(False, "macOS only")
    if not shutil.which("xcrun"):
        return Probe(False, "xcrun is missing")
    if _run(["xcrun", "simctl", "help"], timeout=60).returncode != 0:
        return Probe(False, "simctl is unavailable -- the full Xcode app is needed")
    return Probe(True, "simctl available")


def _repair_xcode() -> Probe:
    """Trigger the command-line-tools installer.

    Xcode proper is a multi-gigabyte App Store product tied to an Apple ID; it
    cannot honestly be installed from here, and pretending otherwise would be
    worse than saying so. This does the part that IS automatable and then reports
    precisely what remains.
    """
    if sys.platform != "darwin":
        return Probe(False, "macOS only")
    if not shutil.which("xcrun"):
        print("    -> xcode-select --install   (complete the dialog macOS opens)")
        _stream(["xcode-select", "--install"])
        return Probe(False, "finish the macOS installer dialog, then re-run")
    return Probe(False, "install Xcode from the App Store, then: xcode-select --install")


# ── browser ──────────────────────────────────────────────────────────────────


def _chromium_installed() -> bool:
    if not PLAYWRIGHT_CACHE.is_dir():
        return False
    for entry in PLAYWRIGHT_CACHE.iterdir():
        if (
            entry.is_dir()
            and entry.name.startswith("chromium-")
            and (entry / "INSTALLATION_COMPLETE").exists()
        ):
            return True
    return False


def _probe_playwright_browser() -> Probe:
    if _chromium_installed():
        return Probe(True, "Chromium present in the Playwright cache")
    return Probe(False, f"no Chromium in {PLAYWRIGHT_CACHE}")


def _repair_playwright_browser() -> Probe:
    print("    -> playwright install chromium")
    if _stream([str(_venv_python()), "-m", "playwright", "install", "chromium"], cwd=ROOT):
        return Probe(False, "browser download failed")
    return _probe_playwright_browser()


# ── registry ─────────────────────────────────────────────────────────────────


def _build_registry() -> dict:
    registry: dict = {}

    def add(requirement: Requirement) -> None:
        registry[requirement.name] = requirement

    add(
        Requirement(
            name="uv",
            summary="Python toolchain",
            probe=_probe_uv,
            repair=_repair_uv,
            instruction="install uv:  curl -LsSf https://astral.sh/uv/install.sh | sh",
        )
    )
    add(
        Requirement(
            name="python-deps",
            summary="Python packages",
            probe=_probe_python_deps,
            needs=("uv",),
            repair=_repair_python_deps,
            instruction="make sync   (on a network that blocks public PyPI: make sync-apple)",
        )
    )
    add(
        Requirement(
            name="docker",
            summary="Docker CLI",
            probe=_probe_docker_cli,
            repair=_repair_docker_cli,
            announce="installing the Docker CLI and Colima through Homebrew",
            instruction="install Docker Desktop, or:  brew install colima docker docker-compose",
        )
    )
    add(
        Requirement(
            name="docker-daemon",
            summary="Docker daemon",
            probe=_probe_docker_daemon,
            needs=("docker",),
            repair=_repair_docker_daemon,
            instruction=(
                "start the container runtime.  Colima, sized for this project:  "
                "colima start --cpu 4 --memory 8      -- or open Docker Desktop"
            ),
        )
    )

    for spec in DATABASES:
        add(
            Requirement(
                name=f"db-{spec.key}",
                summary=f"Neo4j {spec.key} graph (:{spec.port})",
                probe=_probe_database(spec),
                needs=("docker-daemon",),
                repair=_repair_database(spec),
                instruction=f"make db-up DB={spec.key}",
            )
        )

    add(
        Requirement(
            name="dev-data",
            summary=f"committed city data in the {_lane_database('dev').key} graph",
            probe=_probe_dev_data,
            # This lane's dev graph. Naming `db-dev` here would drag every lane
            # back onto the canonical :7687 corpus and undo the isolation.
            needs=("python-deps", f"db-{_lane_database('dev').key}"),
            repair=_repair_dev_data,
            instruction=(
                "run scripts/ensure_dev_data.py under the "
                f"{_lane_database('dev').profile} profile"
            ),
        )
    )
    add(
        Requirement(
            name="valhalla-tiles",
            summary="routing map data",
            probe=_probe_valhalla_tiles,
            repair=_repair_valhalla_tiles,
            announce=(
                "downloading the Ile-de-France and New York OSM extracts -- several hundred "
                "MB. The first routing start then builds tiles from them, which can take well "
                "over an hour. Set PREFLIGHT_AUTOFIX=0 to skip and do this later"
            ),
            instruction="make valhalla-build-tiles",
        )
    )
    add(
        Requirement(
            name="valhalla",
            summary="routing engine",
            probe=_probe_valhalla,
            needs=("docker-daemon", "valhalla-tiles"),
            repair=_repair_valhalla,
            instruction="make valhalla-up",
        )
    )
    add(
        Requirement(
            name="render-key",
            summary="Render credential",
            probe=_probe_render_key,
            repair=_repair_render_key,
            interactive=True,
            instruction=(
                "make render-auth-setup        -- needs a Render API key whose account can read "
                "the ondoway-api service.  Provider secrets (Anthropic, OpenAI, Resend) are "
                "fetched from Render at run time and are never stored in this repo, so no .env "
                "file can substitute for this.  Off macOS, or in CI: export RENDER_API_KEY"
            ),
        )
    )
    add(
        Requirement(
            name="render-cli",
            summary="Render CLI",
            probe=_probe_render_cli,
            repair=_repair_render_cli,
            instruction="brew install render        -- or see https://render.com/docs/cli",
        )
    )
    add(
        Requirement(
            name="render-cli-auth",
            summary="Render CLI sign-in",
            probe=_probe_render_cli_auth,
            needs=("render-cli",),
            repair=_repair_render_cli_auth,
            interactive=True,
            instruction=("render login   -- separate from the API key; approve it in the browser"),
        )
    )
    add(
        Requirement(
            name="flutter",
            summary="Flutter SDK",
            probe=_probe_flutter,
            repair=_repair_flutter,
            announce="installing the Flutter SDK through Homebrew -- this is a large download",
            instruction="install the Flutter SDK:  https://docs.flutter.dev/get-started/install",
        )
    )
    add(
        Requirement(
            name="flutter-deps",
            summary="Flutter packages",
            probe=_probe_flutter_deps,
            needs=("flutter",),
            repair=_repair_flutter_deps,
            instruction="make flutter-pub-get",
        )
    )
    add(
        Requirement(
            name="cocoapods",
            summary="CocoaPods (iOS pods)",
            probe=_probe_cocoapods,
            repair=_repair_cocoapods,
            announce="installing CocoaPods through Homebrew",
            instruction="install CocoaPods:  brew install cocoapods",
        )
    )
    add(
        Requirement(
            name="xcode",
            summary="iOS toolchain",
            probe=_probe_xcode,
            repair=_repair_xcode,
            # The repair triggers the command-line-tools installer, which opens a
            # macOS dialog someone has to accept; Xcode proper is an App Store
            # product tied to an Apple ID and cannot be installed from here at all.
            interactive=True,
            instruction="install Xcode and its command-line tools:  xcode-select --install",
        )
    )
    for port in SERVER_PORTS:
        add(
            Requirement(
                name=f"port-{port}",
                summary=f"TCP port {port}",
                probe=_probe_port(port),
                repair=_repair_port(port),
                instruction=f"free port {port}:  lsof -tiTCP:{port} -sTCP:LISTEN | xargs kill",
            )
        )
    # The workbench reuses a healthy API rather than restarting it, so for that
    # one target an occupied-but-ours port is satisfied -- one reusable row per
    # lane, each expecting THAT lane's dev graph behind the server it reuses.
    for lane, ports in LANE_SERVER_PORTS.items():
        api_port = ports["api"]
        lane_dev = DATABASE_BY_KEY[f"dev{lane}"].port
        add(
            Requirement(
                name=f"port-{api_port}-reusable",
                summary=f"TCP port {api_port} (reusable)",
                probe=_probe_port(api_port, reuse_ok=True, graph_port=lane_dev),
                repair=_repair_port(api_port, reuse_ok=True, graph_port=lane_dev),
                instruction=(
                    f"free port {api_port}:  lsof -tiTCP:{api_port} -sTCP:LISTEN | xargs kill"
                ),
            )
        )

    add(
        Requirement(
            name="playwright-browser",
            summary="Chromium for Playwright",
            probe=_probe_playwright_browser,
            needs=("python-deps",),
            repair=_repair_playwright_browser,
            instruction="uv run playwright install chromium",
        )
    )
    return registry


REGISTRY = _build_registry()


# ── reading a target's declaration back out of the Makefile ──────────────────
# The Makefile is the single place a target's needs are written down.  Anything
# that wants to know what a target requires -- this tool's --target mode, and the
# tests that guard the wiring -- reads it from here, so there is never a second
# copy of that answer to drift.


def _makefile_lines(makefile: Path | None = None) -> list:
    return (makefile or MAKEFILE).read_text(encoding="utf-8").splitlines()


def _prerequisite_sets(lines: list) -> dict:
    """Read the reusable ``PRE_*`` definitions, joining backslash continuations.

    Also reads plain ``NAME ?= value`` overridable defaults (``TEST_PROFILE``,
    ``DB``, ``TARGET``, ``GLUE``). A ``PRE_*`` set may be written in terms of one
    -- ``PRE_PYTEST`` names ``db-$(TEST_PROFILE)`` so that a worktree running on
    its own pytest graph preflights THAT graph rather than the canonical one --
    and without the default in hand the expansion below leaves a literal
    ``db-$(TEST_PROFILE)``, which is not a requirement name and would read as a
    target declaring something that does not exist. Only literal defaults are
    taken (no ``$(`` on the right), so this cannot start a substitution chain.
    """
    variables: dict = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("PRE_") and ":=" in line:
            name, _, value = line.partition(":=")
            collected = value.strip()
            while collected.endswith("\\") and index + 1 < len(lines):
                index += 1
                collected = collected[:-1] + " " + lines[index].strip()
            variables[name.strip()] = collected.strip()
        elif "?=" in line and not line.startswith(("\t", " ", "#")):
            name, _, value = line.partition("?=")
            if name.strip().isidentifier() and "$(" not in value:
                variables.setdefault(name.strip(), value.strip())
        index += 1
    return variables


def _expand(text: str, variables: dict) -> str:
    for _ in range(5):
        before = text
        for name, value in variables.items():
            text = text.replace(f"$({name})", value)
        if text == before:
            break
    return text


class Declaration(NamedTuple):
    """What a target's recipe says about its prerequisites.

    ``names`` alone is not enough to trust a declaration. A call running in
    report mode returns 0 whatever it finds, so the target proceeds regardless;
    and a call nested in a shell conditional can be skipped entirely. Both are
    one-token edits that silently disable the mechanism for that target, so both
    are reported here and asserted against in tests/test_preflight.py.
    """

    names: list
    enforcing: bool  # blocks the recipe when a requirement is missing
    unconditional: bool  # the call is the recipe's own line, not nested in a conditional


REPORT_ONLY_FLAGS = ("--report", "--no-fix")


def declare(target: str, *, makefile: Path | None = None) -> Declaration | None:
    """Read a target's full declaration, or None if it never invokes preflight."""
    lines = _makefile_lines(makefile)
    variables = _prerequisite_sets(lines)

    # A target may appear more than once (prerequisite-only lines, or an
    # accidental redefinition). Take the rule that carries the recipe, and refuse
    # to guess when several do -- picking the first silently read the WRONG rule.
    starts = [n for n, line in enumerate(lines) if line.startswith(target + ":")]
    if not starts:
        raise KeyError(f"no target {target!r} in the Makefile")
    with_recipe = [n for n in starts if n + 1 < len(lines) and lines[n + 1].startswith("\t")]
    if len(with_recipe) > 1:
        raise KeyError(f"target {target!r} is defined with a recipe more than once")
    start = (with_recipe[0] if with_recipe else starts[-1]) + 1

    body = []
    for line in lines[start:]:
        if line.startswith("\t"):
            body.append(line)
        elif line.strip() and not line.startswith("#"):
            break

    # Join backslash continuations, or a requirement named on a continuation
    # line is invisible here -- which would silently exempt multi-line
    # declarations such as bootstrap's from every check built on this.
    recipe = []
    pending = ""
    for line in body:
        pending += line.rstrip()
        if pending.endswith("\\"):
            pending = pending[:-1] + " "
            continue
        recipe.append(pending)
        pending = ""
    if pending:
        recipe.append(pending)

    if not any("$(PREFLIGHT)" in line for line in recipe):
        return None

    enforcing = True
    unconditional = False
    names: list = []
    for line in recipe:
        if "$(PREFLIGHT)" not in line:
            continue
        stripped = line.strip()
        # The call owns its recipe line: not wrapped in if/case/&&, and not
        # prefixed with `-` (which tells Make to ignore a non-zero exit).
        if stripped.startswith(("@$(PREFLIGHT)", "$(PREFLIGHT)")):
            unconditional = True
        if any(flag in line for flag in REPORT_ONLY_FLAGS):
            enforcing = False
        # A call whose failure is swallowed enforces nothing. Measured in real
        # make: `$(PREFLIGHT) ... || true` let the recipe run with a requirement
        # missing, and both flags stayed True.
        after_call = line.split("$(PREFLIGHT)", 1)[1].split(";", 1)[0]
        if any(swallow in after_call for swallow in ("|| true", "|| exit 0", "|| :")):
            enforcing = False
        if stripped.startswith("-@$(PREFLIGHT)") or stripped.startswith("-$(PREFLIGHT)"):
            enforcing = False  # Make's `-` prefix ignores the exit status
        # EVERY occurrence, not just the first.  A `case` over TARGET=local|cloud
        # collapses to one logical line with a preflight call per branch, and
        # reading only the first silently drops the cloud branch's `render-key`.
        for segment in line.split("$(PREFLIGHT)")[1:]:
            for terminator in ("&&", "||", ";"):
                segment = segment.split(terminator, 1)[0]
            segment = _expand(segment, variables).replace("$(DB)", "dev")
            skip_next = False
            for token in shlex.split(segment, posix=True):
                if skip_next:
                    skip_next = False
                    continue
                if token == "--label":
                    skip_next = True
                    continue
                if token.startswith("-"):
                    continue
                names.append(token)
    return Declaration(names=names, enforcing=enforcing, unconditional=unconditional)


def declared_requirements(target: str, *, makefile: Path | None = None) -> list | None:
    """Just the requirement names, for callers that do not care how they are run."""
    declaration = declare(target, makefile=makefile)
    return None if declaration is None else declaration.names


def _machine_wide(include_ports: bool = False) -> list:
    """Everything --all should check: prerequisites of the machine, not of a run.

    Port availability is a property of this moment and this target, not of the
    installation. Including it made `make preflight` and `make doctor` report a
    perfectly good machine as broken because something unrelated held :8080.
    """
    if include_ports:
        return sorted(REGISTRY)
    return sorted(name for name in REGISTRY if not name.startswith("port-"))


def resolve(names: Iterable[str]) -> list:
    """Expand the requested names into a dependency-ordered, de-duplicated plan."""
    ordered: list = []
    seen: set = set()
    visiting: set = set()

    def visit(name: str, trail: tuple) -> None:
        if name in seen:
            return
        if name in visiting:
            path = " -> ".join(trail)
            raise SystemExit(f"preflight: dependency cycle at {name!r} via {path}")
        requirement = REGISTRY.get(name)
        if requirement is None:
            known = ", ".join(sorted(REGISTRY))
            raise SystemExit(f"preflight: unknown requirement {name!r}.  Known: {known}")
        visiting.add(name)
        for dependency in requirement.needs:
            visit(dependency, (*trail, name))
        visiting.discard(name)
        seen.add(name)
        ordered.append(requirement)

    for name in names:
        visit(name, ())
    return ordered


# ── reporting ────────────────────────────────────────────────────────────────


def _colour(enabled: bool, code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if enabled else text


class Reporter:
    """Fixed-width status lines so a failure is findable in a long build log."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def _line(self, code: str, mark: str, name: str, detail: str) -> None:
        print(f"  {_colour(self.enabled, code, mark)} {name:<22} {detail}")

    def ok(self, name: str, detail: str) -> None:
        self._line("32", "OK ", name, detail)

    def fixed(self, name: str, detail: str) -> None:
        self._line("33", "FIX", name, detail)

    def failed(self, name: str, detail: str) -> None:
        self._line("31", "!! ", name, detail)

    def note(self, text: str) -> None:
        print(f"  {_colour(self.enabled, '33', '  ?')} {text}")


def _repair_is_possible(requirement: Requirement) -> bool:
    """An interactive repair needs a human; without a terminal, do not attempt it.

    Running `render login` or a Keychain paste prompt from an unattended build
    would block forever on input nobody can supply.
    """
    if not requirement.interactive:
        return True
    return sys.stdin.isatty() and sys.stdout.isatty()


def check(
    names: Sequence[str],
    *,
    autofix: bool,
    label: str,
    colour: bool,
    report_only: bool = False,
) -> int:
    """Verify (and usually restore) a set of requirements.

    ``report_only`` is for a pure diagnostic: it lists what is missing and exits
    0. A fresh clone is MEANT to be missing things, so a report that exits
    non-zero reads as "this tool is broken" the first time anyone runs it.
    """
    plan = resolve(names)
    reporter = Reporter(colour)
    header = f"Preflight: {len(plan)} prerequisite(s)"
    if label:
        header += f" for {label}"
    print(header)

    blocked: list = []
    tried_and_failed: set = set()
    unsatisfied: set = set()

    for requirement in plan:
        missing = [dep for dep in requirement.needs if dep in unsatisfied]
        if missing:
            reporter.failed(requirement.name, "skipped -- needs " + ", ".join(missing))
            unsatisfied.add(requirement.name)
            continue

        result = requirement.probe()
        if result.ok:
            reporter.ok(requirement.name, result.detail)
        elif autofix and requirement.repair is not None and _repair_is_possible(requirement):
            print(f"  .. {requirement.name}: {result.detail} -- fixing")
            if requirement.announce:
                print(f"     {requirement.announce}")
            claimed = requirement.repair()
            # The repair does not get to grade itself.
            result = requirement.probe() if claimed.ok else claimed
            if claimed.ok and not result.ok:
                result = Probe(False, f"repair reported success but {result.detail}")
            if result.ok:
                reporter.fixed(requirement.name, result.detail)
            else:
                reporter.failed(requirement.name, result.detail)
                blocked.append(requirement)
                tried_and_failed.add(requirement.name)
                unsatisfied.add(requirement.name)
        else:
            if requirement.repair is not None and not _repair_is_possible(requirement):
                result = Probe(result.ok, result.detail + " (needs a terminal to fix)")
            reporter.failed(requirement.name, result.detail)
            blocked.append(requirement)
            unsatisfied.add(requirement.name)

        if requirement.name == "docker-daemon" and result.ok:
            for warning in _colima_memory_warnings():
                reporter.note(warning)

    if not blocked:
        if report_only:
            print("")
            print("Everything this project needs is present.")
        return 0

    # Only offer `make setup` for things it would genuinely fix. A repair that ran
    # and failed will fail again there, and a requirement needing a human never
    # runs unattended. Promising a command that cannot help is worse than silence:
    # it costs the developer another wasted run to find that out.
    setup_would_fix = [
        r
        for r in blocked
        if r.repair is not None and not r.interactive and r.name not in tried_and_failed
    ]
    needs_you = [r for r in blocked if r.name not in {x.name for x in setup_would_fix}]

    print("")
    if report_only:
        print(f"{len(blocked)} of {len(plan)} prerequisites are missing:")
    else:
        print("Preflight stopped -- the command was not run. Missing:")
    for requirement in blocked:
        print(f"  * {requirement.summary}  [{requirement.name}]")
        print(f"      {requirement.instruction or 'no automatic remedy is available'}")
    print("")
    if setup_would_fix and not needs_you:
        print("`make setup` installs and starts all of this for you.")
    elif setup_would_fix:
        names = ", ".join(r.name for r in needs_you)
        print(f"`make setup` handles the rest; these need you: {names}")
    else:
        print("The above need you -- `make setup` cannot fix them.")
    print("")
    return 0 if report_only else 1


def repair_kind(requirement: Requirement) -> str:
    """How a missing requirement gets restored, in the developer's terms."""
    if requirement.repair is None:
        return "manual"
    if requirement.interactive:
        return "guided"
    if requirement.announce:
        return "auto (slow)"
    return "auto"


def _describe() -> int:
    print("Requirements a Make target can declare:")
    print(
        "  auto = fixed silently | auto (slow) = fixed, announced first | "
        "guided = needs you at the keyboard"
    )
    print("")
    for name in sorted(REGISTRY):
        requirement = REGISTRY[name]
        needs = ", ".join(requirement.needs) if requirement.needs else "-"
        print(
            f"  {name:<20} {requirement.summary:<28} needs: {needs:<28} {repair_kind(requirement)}"
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify (and where possible restore) the prerequisites a Make target declares."
    )
    parser.add_argument("requirements", nargs="*", help="requirement names to satisfy")
    parser.add_argument("--label", default="", help="target name, for the report header")
    parser.add_argument("--list", action="store_true", help="describe every known requirement")
    parser.add_argument("--all", action="store_true", help="check every known requirement")
    parser.add_argument(
        "--target",
        default="",
        help="check whatever this Make target declares, read from the Makefile",
    )
    parser.add_argument(
        "--no-fix", action="store_true", help="report only; never start or install anything"
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="pure diagnostic: check nothing-changing, list what is missing, exit 0",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.list:
        return _describe()

    # In report mode nothing can be blocked, so the ports can be surfaced without
    # the false red that took them out of --all in the first place.
    include_ports = bool(args.report)

    label = args.label
    if args.target:
        try:
            declared = declared_requirements(args.target)
        except KeyError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        if declared is None:
            print(f"make {args.target} declares no prerequisites.")
            return 0
        names = declared or _machine_wide(include_ports)
        label = label or f"make {args.target}"
    else:
        names = _machine_wide(include_ports) if args.all else args.requirements

    if not names:
        parser.error("name at least one requirement, or pass --all/--list/--target")

    autofix = (
        not args.no_fix and not args.report and os.environ.get("PREFLIGHT_AUTOFIX", "1") != "0"
    )
    colour = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    return check(names, autofix=autofix, label=label, colour=colour, report_only=args.report)


if __name__ == "__main__":
    raise SystemExit(main())
