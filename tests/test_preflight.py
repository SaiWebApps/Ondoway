"""The prerequisite declarations are only worth what they are checked to be.

Three properties are guarded here, each with the mutation that turns it red.
(A fourth, MAKEFILE COVERAGE — every documented target declares its prerequisites
and every declared name is real — was REMOVED 2026-08-18 by owner ruling: it
asserted the Makefile's shape, not the tool's behaviour. Preflight still refuses
at runtime on an unknown requirement name; a target that forgets its preflight
line simply runs without one.)

0.  LANE TABLE INTEGRITY -- the graphs preflight can start are exactly the ones
    docker-compose.yml defines, on the same ports, in the same containers. The
    two files cannot import each other, so only this test stops them drifting.
    RED when: a ``DatabaseSpec`` is added without its compose service, a
    published Bolt port disagrees, or a ``container_name`` is renamed on one
    side. Proven 2026-08-05 by moving dev3 to :7699 with compose untouched.

1.  REGISTRY INTEGRITY -- every ``needs`` name resolves and the graph is acyclic.
    RED when: a requirement names a dependency that does not exist, or two
    requirements depend on each other.

2.  NO SILENT SUCCESS -- the property the old ``db-up`` violated.  A probe that
    cannot reach its dependency must report failure, and ``check`` must return
    non-zero.  A green run on a dead dependency is the exact bug this replaced.
    RED when: ``check`` returns 0 while a requirement's probe returns not-ok, or a
    requirement whose dependency failed is reported as satisfied.

Hermetic: no container, no database, no provider, no network.  Where a test still
reads the Makefile it does so as text; nothing here starts anything.
"""

from __future__ import annotations

import http.server
import importlib.util
import inspect
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MAKEFILE = ROOT / "Makefile"
PREFLIGHT_PATH = ROOT / "scripts" / "preflight.py"


def _load_preflight():
    """Load preflight by path; it lives outside the importable package tree.

    The module must be registered in ``sys.modules`` BEFORE it executes:
    ``@dataclass`` resolves annotations through ``sys.modules[cls.__module__]``,
    which is None for a module that is mid-execution and unregistered.
    """
    name = "ondoway_preflight"
    spec = importlib.util.spec_from_file_location(name, PREFLIGHT_PATH)
    assert spec and spec.loader, "preflight module could not be located"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


preflight = _load_preflight()


# ── 1. registry integrity ────────────────────────────────────────────────────


def test_every_declared_dependency_exists():
    for name, requirement in preflight.REGISTRY.items():
        for dependency in requirement.needs:
            assert dependency in preflight.REGISTRY, (
                f"requirement {name!r} depends on {dependency!r}, which is not defined"
            )


def test_every_requirement_resolves_without_a_cycle():
    for name in preflight.REGISTRY:
        plan = preflight.resolve([name])
        assert plan[-1].name == name, f"{name} must be last in its own plan"
        satisfied: set = set()
        for requirement in plan:
            missing = set(requirement.needs) - satisfied
            assert not missing, f"{requirement.name} is ordered before {sorted(missing)}"
            satisfied.add(requirement.name)


def test_a_port_instruction_names_its_own_port():
    """The reusable variant's instruction read a leaked loop variable, so
    `make workbench` blocked on :8000 told the developer to free :8080."""
    for name, requirement in preflight.REGISTRY.items():
        if not name.startswith("port-"):
            continue
        # LIMIT: this asserts the digits appear, not that they are the port the
        # command actually frees. It catches a leaked variable, not a typo.
        number = name.split("-")[1]
        assert number in requirement.instruction, (
            f"{name}'s instruction does not mention port {number}: {requirement.instruction!r}"
        )


def test_every_requirement_carries_an_instruction():
    """Whatever the repair does, a human must be told what to run if it fails.

    The instruction is the fallback for three real cases: the repair failed, the
    repair could not run unattended, or the developer set PREFLIGHT_AUTOFIX=0.
    """
    for name, requirement in preflight.REGISTRY.items():
        assert requirement.instruction.strip(), (
            f"{name!r} has no instruction, so a developer whose repair fails is "
            "told nothing actionable"
        )


# A requirement may only skip having a repair if a machine genuinely cannot be
# brought to that state without a human decision. The list is the argument: an
# empty one means every requirement can restore itself. Adding a name here is a
# claim that must be justified in the comment beside it.
REQUIREMENTS_THAT_CANNOT_SELF_REPAIR: dict = {
    # Xcode is a multi-gigabyte App Store product tied to an Apple ID. The repair
    # triggers the command-line-tools installer, which is the automatable part,
    # and then reports what remains -- so it has no success path of its own, and
    # claiming otherwise was the third instance of a guard trusting a comment.
    "xcode": "the App Store cannot be driven from a build system",
}


def test_every_requirement_can_restore_itself():
    """Failing with advice is a last resort, not a design.

    Reporting a missing dependency and stopping pushes the work of knowing what
    this project needs onto the developer -- which is the friction the whole
    mechanism exists to remove. A new requirement that cannot fix itself has to
    say why, here.
    """
    missing = [
        name
        for name, requirement in preflight.REGISTRY.items()
        if requirement.repair is None and name not in REQUIREMENTS_THAT_CANNOT_SELF_REPAIR
    ]
    assert not missing, (
        "these requirements only report a failure instead of fixing it: "
        f"{sorted(missing)}. Give each a repair, or justify it in "
        "REQUIREMENTS_THAT_CANNOT_SELF_REPAIR."
    )

    # Having a repair attribute is not having a repair. `_repair_xcode` returned
    # Probe(False, ...) on every one of its paths, so the registry looked fully
    # self-repairing while one entry could never succeed. A repair must be able
    # to report success: either it returns one, or it re-probes.
    # LIMIT: this reads the repair's SOURCE TEXT, so a repair whose comment
    # merely mentions a probe defeats it. It catches the honest mistake (advice
    # only), not a determined one.
    hollow = []
    for name, requirement in preflight.REGISTRY.items():
        if requirement.repair is None or name in REQUIREMENTS_THAT_CANNOT_SELF_REPAIR:
            continue
        body = inspect.getsource(requirement.repair)
        if "Probe(True" not in body and "_probe" not in body and "_after_install" not in body:
            hollow.append(name)
    assert not hollow, (
        f"these repairs can never report success: {sorted(hollow)} -- they only "
        "print advice, so the registry claims a self-repair it does not have"
    )


def test_an_interactive_repair_is_not_attempted_without_a_terminal(monkeypatch):
    """An unattended run must never block on a sign-in nobody can approve."""
    guided = preflight.Requirement(
        name="guided",
        summary="guided",
        probe=lambda: preflight.Probe(False, "absent"),
        repair=lambda: pytest.fail("an interactive repair ran with no terminal attached"),
        interactive=True,
        instruction="do it by hand",
    )
    silent = preflight.Requirement(
        name="silent",
        summary="silent",
        probe=lambda: preflight.Probe(False, "absent"),
        repair=lambda: preflight.Probe(True, "fixed"),
        instruction="do it by hand",
    )

    monkeypatch.setattr(preflight.sys.stdin, "isatty", lambda: False, raising=False)
    assert preflight._repair_is_possible(guided) is False
    assert preflight._repair_is_possible(silent) is True


def test_the_render_credential_accepts_an_environment_key_off_macos(monkeypatch):
    """A Keychain-only credential locks out every Linux and CI machine.

    scripts/dev_env.py reads RENDER_API_KEY as a fallback; the probe must agree,
    or preflight passes a target that then dies on a missing credential.
    """
    monkeypatch.setattr(preflight.sys, "platform", "linux")
    monkeypatch.delenv("RENDER_API_KEY", raising=False)
    assert preflight._probe_render_key().ok is False

    monkeypatch.setenv("RENDER_API_KEY", "not-a-real-key")
    result = preflight._probe_render_key()
    assert result.ok is True
    assert "not-a-real-key" not in result.detail, "a credential value must never be echoed"


def test_database_specs_agree_with_the_committed_profiles():
    """The probe's port must match the profile the target actually executes under."""
    for spec in preflight.DATABASES:
        profile = preflight._read_profile(spec.profile)
        assert profile, f"profile {spec.profile!r} is missing or empty"
        assert profile.get("NEO4J_URI") == f"bolt://localhost:{spec.port}", (
            f"profile {spec.profile!r} does not point at the port preflight probes"
        )
        assert profile.get("NEO4J_PASSWORD"), (
            f"profile {spec.profile!r} has no password, so the readiness query cannot run"
        )


def test_database_specs_agree_with_the_compose_services():
    """Every graph preflight starts must exist in compose, and vice versa.

    These two files cannot import each other, so nothing but this test stops
    them drifting: a spec with no service fails at runtime as an opaque docker
    error, and a service with no spec is a graph the build cannot start, probe
    or reset. RED when: a DatabaseSpec is added without its compose service, a
    published Bolt port disagrees, or a container_name is renamed on one side.
    """
    import yaml

    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    graphs = {name: body for name, body in services.items() if name.startswith("neo4j")}

    assert {spec.service for spec in preflight.DATABASES} == set(graphs), (
        "the graphs preflight knows about and the ones compose defines have drifted"
    )

    for spec in preflight.DATABASES:
        body = graphs[spec.service]
        assert body.get("container_name") == spec.container, (
            f"{spec.service}: compose names the container {body.get('container_name')!r}, "
            f"preflight probes {spec.container!r}"
        )
        published = {port.split(":", 1)[0] for port in body.get("ports", [])}
        assert str(spec.port) in published, (
            f"{spec.service}: compose publishes {sorted(published)}, "
            f"preflight probes :{spec.port}"
        )

    volumes = set(compose.get("volumes") or {})
    for spec in preflight.DATABASES:
        declared = {
            mount.split(":", 1)[0]
            for mount in graphs[spec.service].get("volumes", [])
            if not mount.startswith(".")
        }
        assert declared <= volumes, (
            f"{spec.service} mounts {declared - volumes}, which the volumes block omits"
        )


# ── 3. no silent success ─────────────────────────────────────────────────────


def _requirement(name, *, ok, needs=(), repair=None):
    return preflight.Requirement(
        name=name,
        summary=name,
        probe=lambda: preflight.Probe(ok, "probed"),
        needs=needs,
        repair=repair,
        instruction="do the thing",
    )


@pytest.fixture
def isolated_registry(monkeypatch):
    registry: dict = {}
    monkeypatch.setattr(preflight, "REGISTRY", registry)
    return registry


def test_a_failed_probe_makes_check_fail(isolated_registry, capsys):
    isolated_registry["thing"] = _requirement("thing", ok=False)
    code = preflight.check(["thing"], autofix=False, label="unit", colour=False)
    output = capsys.readouterr().out

    assert code != 0, "a requirement that did not answer must not report success"
    # Assert the promise, not the banner's wording: name what is missing, and
    # tell the developer what to do about it. Pinning the exact headline made
    # this test fail on a pure rewording, which taught nobody anything.
    assert "thing" in output, "the failing requirement was not named"
    assert "do the thing" in output, "its instruction was not shown"


def test_a_satisfied_probe_makes_check_pass(isolated_registry):
    isolated_registry["thing"] = _requirement("thing", ok=True)
    assert preflight.check(["thing"], autofix=False, label="unit", colour=False) == 0


def test_a_dependent_is_never_reported_satisfied_when_its_dependency_failed(
    isolated_registry, capsys
):
    """The exact old bug: the daemon is down, yet the database reports ready."""
    isolated_registry["daemon"] = _requirement("daemon", ok=False)
    isolated_registry["database"] = _requirement("database", ok=True, needs=("daemon",))

    code = preflight.check(["database"], autofix=False, label="unit", colour=False)
    output = capsys.readouterr().out

    assert code != 0
    assert "skipped" in output, "a dependent must be skipped, not probed, after a failed dependency"
    assert "OK  database" not in output, "the database must never be reported ready here"


def test_a_repair_that_does_not_work_is_reported_as_failure(isolated_registry):
    """A repair claims nothing on its own -- only its re-probe can pass the check."""
    isolated_registry["thing"] = _requirement(
        "thing", ok=False, repair=lambda: preflight.Probe(False, "still broken")
    )
    assert preflight.check(["thing"], autofix=True, label="unit", colour=False) != 0


def test_a_repair_that_works_passes(isolated_registry):
    """A real repair changes what the probe observes, so the stub must too.

    check() re-probes after every repair, so a fixture whose probe is frozen at
    False is asserting that a repair can override the evidence -- which is
    exactly what must not happen (see the lying-repair test below).
    """
    state = {"fixed": False}
    isolated_registry["thing"] = preflight.Requirement(
        name="thing",
        summary="thing",
        probe=lambda: preflight.Probe(state["fixed"], "started" if state["fixed"] else "absent"),
        repair=lambda: (state.update(fixed=True), preflight.Probe(True, "started"))[1],
        instruction="do the thing",
    )
    assert preflight.check(["thing"], autofix=True, label="unit", colour=False) == 0


def test_the_diagnostic_reports_gaps_without_failing(isolated_registry, capsys):
    """`make doctor` on a fresh clone must not look like a broken tool.

    A clone is MEANT to be missing things. If the first command a new developer
    runs exits non-zero and prints FAILED, the honest report reads as breakage --
    friction created by the thing meant to remove it.
    """
    isolated_registry["thing"] = _requirement("thing", ok=False)
    code = preflight.check(
        ["thing"], autofix=False, label="this machine", colour=False, report_only=True
    )
    output = capsys.readouterr().out

    assert code == 0, "a diagnostic must not fail merely because something is missing"
    assert "1 of 1 prerequisites are missing" in output
    assert "make setup" in output, "the report must name the command that fixes it"
    assert "FAILED" not in output


def test_a_real_target_still_refuses_when_something_is_missing(isolated_registry):
    """The diagnostic's leniency must not leak into targets that do work."""
    isolated_registry["thing"] = _requirement("thing", ok=False)
    assert preflight.check(["thing"], autofix=False, label="workbench", colour=False) == 1


def test_an_interrupted_map_download_leaves_no_usable_looking_file(monkeypatch, tmp_path):
    """A truncated extract must never be mistaken for a complete one.

    The tile build would consume a half-downloaded file happily and produce a
    quietly wrong road network. So the download goes to a temporary name and is
    moved into place only on success -- and a failure cleans up after itself.
    """
    tiles = tmp_path / "custom_files"
    tiles.mkdir()
    monkeypatch.setattr(preflight, "_tile_directory", lambda: tiles)

    def failing_curl(argv, **kwargs):
        destination = Path(argv[argv.index("-o") + 1])
        destination.write_bytes(b"half a file")  # what curl leaves behind
        return 1

    monkeypatch.setattr(preflight, "_stream", failing_curl)
    result = preflight._repair_valhalla_tiles()

    assert result.ok is False
    assert not list(tiles.glob("*.osm.pbf")), "a failed download left an extract behind"
    assert not list(tiles.glob("*.partial")), "the temporary file was not cleaned up"


def test_a_completed_map_download_is_moved_into_place(monkeypatch, tmp_path):
    tiles = tmp_path / "custom_files"
    tiles.mkdir()
    monkeypatch.setattr(preflight, "_tile_directory", lambda: tiles)

    def working_curl(argv, **kwargs):
        Path(argv[argv.index("-o") + 1]).write_bytes(b"a whole extract")
        return 0

    monkeypatch.setattr(preflight, "_stream", working_curl)
    result = preflight._repair_valhalla_tiles()

    assert result.ok is True, result.detail
    names = sorted(p.name for p in tiles.glob("*.osm.pbf"))
    assert names == sorted(name for name, _ in preflight.OSM_EXTRACTS)
    assert not list(tiles.glob("*.partial"))


def test_a_foreign_process_holding_a_port_is_named_not_killed(monkeypatch):
    """Claiming a port by killing an unidentified process is collateral damage.

    This repo has the precedent: a broader match once killed the user's desktop
    app, which merely held client sockets to the same port. A repair may only
    stop a listener it can identify as this project's own server.
    """
    monkeypatch.setattr(
        preflight,
        "_port_listeners",
        lambda port: [preflight.PortHolder(4242, "/usr/bin/postgres -D /db")],
    )

    def refuse_to_kill(argv, **kwargs):
        assert argv[0] != "kill", "preflight tried to kill a process it did not recognise"
        return subprocess.CompletedProcess([], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(preflight, "_run", refuse_to_kill)
    result = preflight._repair_port(8080)()

    assert result.ok is False
    assert "4242" in result.detail, "the holding process must be named so it can be dealt with"


def test_this_projects_own_stale_server_is_stopped(monkeypatch):
    """A leftover server from a previous run is ours to clean up.

    ``_responding`` must be stubbed along with ``_port_listeners``: the repair asks
    "ours, but is it STALE?" by opening a real socket to 127.0.0.1:<port>. With a
    developer's own ``make api`` listening on 8000 the real probe answers True, the
    repair takes the leave-a-live-server-alone branch, and this test reads the
    machine it runs on instead of the code it is meant to pin.
    """
    ours = f"{preflight.ROOT}/.venv/bin/python -m uvicorn src.api.app:app --port 8000"
    holders = [preflight.PortHolder(99, ours)]
    killed = []

    def listeners(port):
        return list(holders)

    def run(argv, **kwargs):
        if argv[0] == "kill":
            killed.append(argv[1])
            holders.clear()
        return subprocess.CompletedProcess([], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(preflight, "_port_listeners", listeners)
    monkeypatch.setattr(preflight, "_responding", lambda port, **k: False)
    monkeypatch.setattr(preflight, "_run", run)
    monkeypatch.setattr(preflight, "_wait_for", lambda fn, *a, **k: fn())

    result = preflight._repair_port(8000)()
    assert killed == ["99"], "our own stale server should have been stopped"
    assert result.ok is True


def test_a_port_already_serving_this_project_counts_as_satisfied(monkeypatch):
    """`make workbench` reuses a healthy API rather than restarting it."""
    monkeypatch.setattr(
        preflight,
        "_port_listeners",
        lambda port: [preflight.PortHolder(7, f"{preflight.ROOT}/ uvicorn src.api.app:app")],
    )
    monkeypatch.setattr(preflight, "_serves_this_project", lambda port, **kwargs: True)
    assert preflight._probe_port(8000, reuse_ok=True)().ok is True, "workbench must reuse"
    assert preflight._probe_port(8000)().ok is False, (
        "a target that BINDS the port must not treat an occupied port as satisfied"
    )


def test_a_failed_repair_does_not_promise_that_setup_will_fix_it(isolated_registry, capsys):
    """Naming a command that cannot help costs another wasted run to find out."""
    isolated_registry["thing"] = _requirement(
        "thing", ok=False, repair=lambda: preflight.Probe(False, "held by someone else")
    )
    preflight.check(["thing"], autofix=True, label="unit", colour=False)
    output = capsys.readouterr().out
    assert "`make setup` installs and starts all of this" not in output
    assert "cannot fix them" in output


def test_the_diagnostic_says_so_when_nothing_is_missing(isolated_registry, capsys):
    isolated_registry["thing"] = _requirement("thing", ok=True)
    code = preflight.check(
        ["thing"], autofix=False, label="this machine", colour=False, report_only=True
    )
    assert code == 0
    assert "Everything this project needs is present." in capsys.readouterr().out


def test_an_unknown_requirement_is_rejected_rather_than_ignored(isolated_registry):
    with pytest.raises(SystemExit):
        preflight.resolve(["no-such-requirement"])


def test_an_unanswerable_docker_query_is_not_reported_as_a_missing_container(monkeypatch):
    """ "I could not ask" and "it is not there" are different answers.

    Measured 2026-07-31: under a burst of rapid checks one `docker ps` failed
    while the container had been up 36 hours. Collapsing that into False called a
    healthy database missing -- which would refuse a target, or restart
    containers that were fine.
    """
    monkeypatch.setattr(
        preflight,
        "_run",
        lambda *a, **k: subprocess.CompletedProcess([], returncode=1, stdout="", stderr="boom"),
    )
    monkeypatch.setattr(preflight.time, "sleep", lambda _: None)
    assert preflight._container_running("ondoway-neo4j") is None


def test_a_present_container_is_still_reported_present(monkeypatch):
    """The retry must not mask a genuine answer in either direction."""
    # Only the -test container runs. A substring match would report the DEV
    # container present too, which is the direction that actually hurts.
    listing = '{"Names":"ondoway-neo4j-test","State":"running"}\n'
    monkeypatch.setattr(
        preflight,
        "_run",
        lambda *a, **k: subprocess.CompletedProcess([], returncode=0, stdout=listing, stderr=""),
    )
    assert preflight._container_running("ondoway-neo4j-test") is True
    assert preflight._container_running("ondoway-neo4j") is False, (
        "a running -test container must not satisfy the dev container's probe"
    )


# ── the module must stay runnable on the system interpreter ──────────────────


def test_preflight_runs_on_the_system_interpreter():
    """It must report a missing toolchain on a machine that has nothing set up.

    That means it cannot depend on the project's own virtual environment.  This
    executes it with the interpreter Make actually invokes.
    """
    result = subprocess.run(
        ["python3", str(PREFLIGHT_PATH), "--list"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, f"preflight failed under python3:\n{result.stderr}"
    assert "docker-daemon" in result.stdout, "the requirement table did not render"


# ── 4. the mechanism cannot be switched off ─────────────────────────────────
# A hostile review ran 24 mutations against the guards above; 21 passed. These
# close the holes it found. Each names the mutation it exists to catch.


# The reusable sets are the declaration for dozens of targets at once. Gutting
# `PRE_FULL_SUITE := uv` passed every guard, including the claim in CLAUDE.md
# that `make test` checks the whole union up front.
REQUIRED_IN_SETS = {
    "PRE_PY": {"uv", "python-deps"},
    "PRE_LOCAL_GRAPH": {"uv", "python-deps", "db-dev", "dev-data"},
    "PRE_TOUR": {"uv", "python-deps", "db-dev", "dev-data", "valhalla"},
    "PRE_PYTEST": {"uv", "python-deps", "db-test", "db-dev", "dev-data", "valhalla"},
    "PRE_FLUTTER": {"flutter", "flutter-deps"},
    "PRE_FULL_SUITE": {
        "uv",
        "python-deps",
        "db-test",
        "db-dev",
        "db-workbench",
        "dev-data",
        "valhalla",
        "playwright-browser",
        "flutter",
        "flutter-deps",
        "render-key",
    },
}


def test_lane4_is_a_complete_lane():
    """A lane is complete when preflight, compose and the committed profiles all
    carry its three graphs — dev, test and workbench. Lane 4 is the sandbox lane
    multi-track runs hand to a second worktree (docs/adr/0002), so a missing
    half here is a sandbox that silently reads someone else's data.
    The spec/profile/compose agreement itself is guarded by the drift tests
    above; this pins the lane's existence and its pytest port's place in the
    wipe allowlist.
    """
    for key in ("dev4", "test4", "workbench4"):
        assert key in preflight.DATABASE_BY_KEY, f"lane 4 has no {key} DatabaseSpec"
    from tests.conftest import _TEST_PORT_ALLOWLIST

    test4_port = preflight.DATABASE_BY_KEY["test4"].port
    assert test4_port in _TEST_PORT_ALLOWLIST, (
        f"lane 4's pytest graph (:{test4_port}) is not wipe-allowlisted, so its "
        "destructive fixtures refuse to run"
    )


def test_the_dev_half_of_a_lane_is_lane_aware():
    """`make api LANE=4` must serve lane 4's dev graph, never the canonical one.

    Behavioural, per the ruling against shape assertions: the recipe `make`
    itself would run is read off a dry-run and must name the lane's profile,
    the lane's port and the lane's dev-graph requirement — and the same
    dry-run per lane pins the Makefile's port arithmetic to preflight's
    LANE_SERVER_PORTS table, so the two spellings cannot drift apart.
    """
    for lane in ("2", "3", "4"):
        api_port = preflight.LANE_SERVER_PORTS[lane]["api"]
        result = subprocess.run(
            ["make", "-n", "api", f"LANE={lane}"],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            check=False,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert f"--profile local{lane}" in result.stdout, (
            f"LANE={lane}: the api recipe does not exec under the lane's dev "
            f"profile:\n{result.stdout}"
        )
        assert f"--port {api_port}" in result.stdout, (
            f"LANE={lane}: the api recipe binds a different port than "
            f"preflight's lane table ({api_port}):\n{result.stdout}"
        )
        assert f"port-{api_port}" in result.stdout, (
            f"LANE={lane}: the api recipe preflights a different port "
            f"requirement than the lane table's:\n{result.stdout}"
        )
        assert f"db-dev{lane}" in result.stdout, (
            f"LANE={lane}: the api recipe preflights another lane's dev graph:"
            f"\n{result.stdout}"
        )


def test_a_lanes_live_shard_execs_under_its_own_test_profile():
    """`make test-file LANE=4 LIVE=1` used to preflight lane 4's graphs and
    then exec the destructive live fixtures against the canonical :7688 shard
    through a literal `--profile test`. The live exec must follow the lane."""
    result = subprocess.run(
        ["make", "-n", "test-live", "LANE=4"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "--profile test4" in result.stdout, (
        f"the live shard's exec does not follow the lane:\n{result.stdout}"
    )


def test_a_reusable_lane_port_expects_that_lanes_own_dev_graph(monkeypatch):
    """Each lane's reusable API-port row must hand `_serves_this_project` that
    lane's dev graph — a lane-4 workbench must never satisfy itself with a
    server reading another lane's corpus. Regression for the graph_port
    plumbing, which every name-existence check would miss."""
    ours = f"{preflight.ROOT}/.venv/bin/python -m uvicorn src.api.app:app"
    seen: dict[int, int] = {}

    def capture(port, *, graph_port=preflight.DEV_GRAPH_PORT):
        seen[port] = graph_port
        return True

    monkeypatch.setattr(preflight, "_serves_this_project", capture)
    for lane, ports in preflight.LANE_SERVER_PORTS.items():
        api_port = ports["api"]
        monkeypatch.setattr(
            preflight, "_port_listeners", lambda p: [preflight.PortHolder(1, ours)]
        )
        assert preflight.REGISTRY[f"port-{api_port}-reusable"].probe().ok is True
        expected = preflight.DATABASE_BY_KEY[f"dev{lane}"].port
        assert seen.get(api_port) == expected, (
            f"port-{api_port}-reusable would reuse a server on graph "
            f":{seen.get(api_port)}; lane {lane or 'main'}'s dev graph is :{expected}"
        )


def test_the_canonical_xdist_shards_never_include_the_sandbox_lane():
    """The bar's three workers map to 7688/7690/7691 and no more; lane 4's
    graph joining that map would hand the sandbox's fixtures to the bar."""
    from tests.conftest import _XDIST_WORKER_DB

    assert set(_XDIST_WORKER_DB) == {0, 1, 2}
    mapped_ports = {row["port"] for row in _XDIST_WORKER_DB.values()}
    assert mapped_ports == {7688, 7690, 7691}
    sandbox_port = preflight.DATABASE_BY_KEY["test4"].port
    assert sandbox_port not in mapped_ports


def test_live_corpus_ports_track_the_lane_dev_graphs():
    """tests/live_graph.py may open exactly the localhost dev graphs the lane
    table defines — no more (Aura stays unreachable), no fewer (a lane's
    live-corpus tests must reach that lane's own dev graph)."""
    from tests.live_graph import DEV_GRAPH_PORTS

    lane_dev_ports = {
        spec.port for spec in preflight.DATABASES if spec.key.startswith("dev") or spec.key == "dev"
    }
    assert lane_dev_ports == DEV_GRAPH_PORTS, (
        f"live_graph admits {sorted(DEV_GRAPH_PORTS)} but the lane table defines "
        f"{sorted(lane_dev_ports)} — the two have drifted"
    )


def test_lane_ports_are_disjoint_and_registered():
    """Every port a lane binds is unique across lanes, avoids the shared
    services (Valhalla :8002, the tracker dashboards :8010-:8019, every Bolt
    port), and is a registered requirement — plus each lane's API port carries
    a reusable row, so `make workbench LANE=n` can reuse only a server on its
    own lane's port and graph."""
    table = preflight.LANE_SERVER_PORTS
    assert set(table) == {"", "2", "3", "4"}, "one row per lane, main included"
    all_ports = [port for lane in table.values() for port in lane.values()]
    assert len(all_ports) == len(set(all_ports)), f"lane ports collide: {sorted(all_ports)}"
    reserved = {8002} | set(range(8010, 8020)) | {spec.port for spec in preflight.DATABASES}
    clashes = set(all_ports) & reserved
    assert not clashes, f"lane ports collide with shared services: {sorted(clashes)}"
    for port in all_ports:
        assert f"port-{port}" in preflight.REGISTRY, f"port-{port} is not a requirement"
    for lane, ports in table.items():
        name = f"port-{ports['api']}-reusable"
        assert name in preflight.REGISTRY, (
            f"lane {lane or 'main'} has no reusable API-port requirement {name}"
        )


def _make_in(directory: Path, *goals: str) -> subprocess.CompletedProcess:
    """`make -n` against the copied Makefile in ``directory`` — parse-time only,
    so the checkout-identity guards fire without any recipe running.

    Runs with make's own inherited state scrubbed: under the definitive bar
    this test itself executes beneath `make`, and the inherited MAKEFLAGS
    carries the bar's `LANE=` command-line override into the child — a
    command-line assignment outranks the pin's file assignment, which is not
    the shell a developer types into.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MAKE", "MFLAGS"))}
    env.pop("LANE", None)
    return subprocess.run(
        ["make", "-n", *goals],
        capture_output=True,
        text=True,
        cwd=str(directory),
        check=False,
        timeout=60,
        env=env,
    )


@pytest.fixture
def guard_probe(tmp_path):
    """A throwaway git repo carrying only this repo's Makefile, plus a worktree
    of it — the two checkout shapes the lane guard must tell apart. Hermetic:
    nothing touches this repo's own git metadata."""
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    main = tmp_path / "main"
    main.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=main, check=True)
    (main / "Makefile").write_text(MAKEFILE.read_text(encoding="utf-8"), encoding="utf-8")
    subprocess.run([*git, "add", "Makefile"], cwd=main, check=True)
    subprocess.run([*git, "commit", "-qm", "makefile"], cwd=main, check=True)
    worktree = tmp_path / "wt"
    subprocess.run(["git", "worktree", "add", "-q", str(worktree)], cwd=main, check=True)
    return main, worktree


def test_a_worktree_refuses_to_run_without_a_lane(guard_probe):
    """The trampling trap: a worktree with LANE forgotten used to resolve every
    target against the MAIN checkout's graphs, every guard green. A worktree
    must know its lane — from LANE= or its own .ondoway-lane pin — or refuse."""
    main, worktree = guard_probe

    unpinned = _make_in(worktree, "help")
    assert unpinned.returncode != 0, "a worktree with no lane ran anyway"
    assert ".ondoway-lane" in unpinned.stderr, (
        f"the refusal does not name the pin file: {unpinned.stderr!r}"
    )

    assert _make_in(worktree, "help", "LANE=4").returncode == 0, (
        "an explicit LANE=4 must satisfy the worktree guard"
    )
    (worktree / ".ondoway-lane").write_text("4\n", encoding="utf-8")
    assert _make_in(worktree, "help").returncode == 0, (
        "the .ondoway-lane pin must satisfy the worktree guard"
    )
    assert _make_in(main, "help").returncode == 0, (
        "the main checkout needs no lane and must be untouched by the guard"
    )


def test_the_definitive_bar_refuses_to_run_from_a_worktree(guard_probe):
    """team.md's multi-track rule, enforced rather than remembered: only the
    main checkout runs `make test`/`make audit` — from a worktree the bar
    would run the canonical shards against the canonical graphs."""
    _main, worktree = guard_probe
    (worktree / ".ondoway-lane").write_text("4\n", encoding="utf-8")
    for goal in ("test", "audit"):
        result = _make_in(worktree, goal)
        assert result.returncode != 0, f"`make {goal}` ran from a worktree"
        assert "main checkout" in result.stderr, (
            f"the refusal does not say where the bar belongs: {result.stderr!r}"
        )


def test_a_worktree_touches_only_its_own_lanes_graphs(guard_probe):
    """`make db-reset DB=dev` from a worktree used to delete MAIN's dev volume
    (one pinned compose project across checkouts). A worktree's db targets
    accept only its own lane's graphs."""
    _main, worktree = guard_probe
    (worktree / ".ondoway-lane").write_text("4\n", encoding="utf-8")
    blocked = _make_in(worktree, "db-reset", "DB=dev")
    assert blocked.returncode != 0, "a worktree reached another lane's graph"
    assert "dev4" in blocked.stderr, (
        f"the refusal does not name the lane's own graphs: {blocked.stderr!r}"
    )
    assert _make_in(worktree, "db-status", "DB=dev4").returncode == 0
    assert _make_in(worktree, "db-reset", "DB=dev4").returncode == 0, (
        "-n on the worktree's own lane graph must parse clean"
    )


def test_db_up_resolves_every_database_not_just_the_default():
    """Only DB=dev was ever exercised; DB=test and DB=workbench went unchecked."""
    text = MAKEFILE.read_text(encoding="utf-8")
    assert "db-$(DB)" in text, "db-up no longer resolves its requirement from DB="
    for spec in preflight.DATABASES:
        assert f"db-{spec.key}" in preflight.REGISTRY, (
            f"DB={spec.key} would resolve to a requirement that does not exist"
        )


def test_the_container_query_is_scoped_to_listening_sockets(monkeypatch):
    """Dropping -sTCP:LISTEN is the exact regression the port docstring cites.

    A bare `lsof -i:PORT` also matches CLIENT sockets, so an unrelated process
    merely talking to the port from the other side gets killed. Measured here
    once already.
    """
    seen: list = []

    def capture(argv, **kwargs):
        seen.append(list(argv))
        return subprocess.CompletedProcess([], returncode=1, stdout="", stderr="")

    monkeypatch.setattr(preflight, "_run", capture)
    preflight._port_listeners(8000)
    assert seen, "the port was never queried"
    assert "-sTCP:LISTEN" in seen[0], (
        "the port query is not scoped to LISTEN, so it can match client sockets "
        "belonging to unrelated processes"
    )


def test_only_this_projects_own_servers_match_the_kill_filter():
    """Widening the marker list to 'python' made any python script killable."""
    assert "python" not in preflight.SERVER_MARKERS, (
        "'python' matches any script, which defeats the identity check entirely"
    )
    innocent = "/usr/bin/python3 /Users/someone/their-own-server.py"
    assert not any(marker in innocent for marker in preflight.SERVER_MARKERS)
    ours = "python -m uvicorn src.api.app:app --port 8000"
    assert any(marker in ours for marker in preflight.SERVER_MARKERS)


def _stub_port_repair(monkeypatch, calls_until_free):
    """A holder that releases the port after `calls_until_free` observations.

    ``_responding`` is stubbed dead alongside the listener table. The repair only
    signals a holder it has established is STALE, and it establishes that by opening
    a real socket to 127.0.0.1:<port>. Leaving it live makes every caller of this
    helper depend on whether the developer happens to have ``make api`` running.
    """
    state = {"n": 0}
    ours = f"{preflight.ROOT}/.venv/bin/python -m uvicorn src.api.app:app"

    def listeners(port):
        state["n"] += 1
        return [] if state["n"] > calls_until_free else [preflight.PortHolder(99, ours)]

    monkeypatch.setattr(preflight, "_port_listeners", listeners)
    monkeypatch.setattr(preflight, "_responding", lambda port, **k: False)
    monkeypatch.setattr(preflight, "_serves_this_project", lambda port, **kwargs: False)
    monkeypatch.setattr(
        preflight,
        "_run",
        lambda *a, **k: subprocess.CompletedProcess([], returncode=0, stdout="", stderr=""),
    )


def test_a_process_given_a_moment_to_exit_is_waited_for(monkeypatch):
    """Signalled processes do not die instantly.

    Without the release wait the repair re-probes immediately, still sees the
    dying process, and reports failure for a port that was about to be free.
    """
    _stub_port_repair(monkeypatch, calls_until_free=2)
    assert preflight._repair_port(8000)().ok is True, (
        "the repair did not wait for the process it signalled to actually exit"
    )


def test_a_process_that_ignores_the_stop_is_reported_not_assumed_gone(monkeypatch):
    """A port that never frees must never be reported as claimed."""
    _stub_port_repair(monkeypatch, calls_until_free=10**6)
    assert preflight._repair_port(8000)().ok is False, (
        "the port was never released, so this must not report success"
    )


def test_an_interrupted_download_is_caught_by_the_temporary_name(monkeypatch, tmp_path):
    """The previous test made curl RETURN 1, which runs the cleanup line.

    A real interrupt never returns to Python at all, and only the write-to-temp
    then rename saves you. Writing straight to the final path passed both of the
    earlier download tests.
    """
    tiles = tmp_path / "custom_files"
    tiles.mkdir()
    monkeypatch.setattr(preflight, "_tile_directory", lambda: tiles)

    def interrupted(argv, **kwargs):
        Path(argv[argv.index("-o") + 1]).write_bytes(b"truncated")
        raise KeyboardInterrupt

    monkeypatch.setattr(preflight, "_stream", interrupted)
    with pytest.raises(KeyboardInterrupt):
        preflight._repair_valhalla_tiles()

    assert preflight._probe_valhalla_tiles().ok is False, (
        "a truncated extract was reported as usable map data"
    )


def test_check_never_runs_an_interactive_repair_without_a_terminal(monkeypatch, isolated_registry):
    """The old test called the helper directly and never drove check().

    Removing the guard from check() itself left it green while an unattended
    build would block forever on a browser sign-in.
    """
    ran: list = []
    isolated_registry["guided"] = preflight.Requirement(
        name="guided",
        summary="guided",
        probe=lambda: preflight.Probe(False, "absent"),
        repair=lambda: (ran.append(1), preflight.Probe(True, "done"))[1],
        interactive=True,
        instruction="do it yourself",
    )
    monkeypatch.setattr(preflight.sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(preflight.sys.stdout, "isatty", lambda: False, raising=False)

    code = preflight.check(["guided"], autofix=True, label="unit", colour=False)
    assert not ran, "an interactive repair ran with no terminal attached"
    assert code != 0


def test_a_repair_that_claims_success_without_fixing_anything_is_caught(isolated_registry, capsys):
    """A repair does not get to grade itself.

    check() used to trust the repair's return value, so `lambda: Probe(True,
    "trust me")` on a permanently broken requirement printed FIX and exited 0 --
    the exact silent success this module exists to prevent.
    """
    isolated_registry["liar"] = preflight.Requirement(
        name="liar",
        summary="liar",
        probe=lambda: preflight.Probe(False, "still broken"),
        repair=lambda: preflight.Probe(True, "trust me"),
        instruction="fix it by hand",
    )
    code = preflight.check(["liar"], autofix=True, label="unit", colour=False)
    output = capsys.readouterr().out
    assert code != 0, "a repair that fixed nothing reported success"
    assert "still broken" in output, "the probe's real verdict was not shown"


# ── 5. false greens a second panel found ────────────────────────────────────
# All three were the SAME family as the bug this module exists to kill, just
# relocated: an unanswerable query read as "nothing there".


def test_an_unanswerable_port_query_is_not_reported_as_a_free_port(monkeypatch):
    """`lsof` exits 1 when the port is free and 127 when it could not run.

    Collapsing those certified a port FREE that something was holding, and the
    server then failed to bind with "address already in use" -- the error the
    port requirement exists to prevent.
    """
    monkeypatch.setattr(
        preflight,
        "_run",
        lambda *a, **k: subprocess.CompletedProcess(
            [], returncode=127, stdout="", stderr="timeout"
        ),
    )
    monkeypatch.setattr(preflight.time, "sleep", lambda _: None)
    assert preflight._port_listeners(8000) == preflight.UNKNOWN
    assert preflight._probe_port(8000)().ok is False, (
        "an unanswerable query certified the port free"
    )


def test_a_genuinely_free_port_is_still_reported_free(monkeypatch):
    """The three-way answer must not turn lsof's normal 'no match' into a failure."""
    monkeypatch.setattr(
        preflight,
        "_run",
        lambda *a, **k: subprocess.CompletedProcess([], returncode=1, stdout="", stderr=""),
    )
    assert preflight._port_listeners(8000) == []
    assert preflight._probe_port(8000)().ok is True


def test_another_projects_server_is_not_mistaken_for_ours():
    """`uvicorn` and `src.server` are ecosystem conventions, not identity.

    Another repo's dev server on the same port matched the marker list and was
    killed as "our stale one".
    """
    strangers = [
        "/Users/dev/other-startup/.venv/bin/python -m uvicorn app.main:app --port 8000",
        "/opt/homebrew/bin/python3.11 -m src.server --port 8080",
        "python -m uvicorn src.api.app:app",  # right shape, no path to this checkout
        # A sibling worktree: its path CONTAINS this checkout's directory name,
        # which is why a bare name match let the main checkout kill it. This repo
        # has a recorded incident of a session destroying a sibling worktree.
        f"{preflight.ROOT}-wt1/.venv/bin/python -m uvicorn src.api.app:app",
        f"{preflight.ROOT}-scope2/.venv/bin/python -m uvicorn src.api.app:app",
    ]
    for command in strangers:
        assert not preflight._is_our_server(command), (
            f"another project's server would be killed: {command}"
        )

    ours = f"{preflight.ROOT}/.venv/bin/python -m uvicorn src.api.app:app --port 8000"
    assert preflight._is_our_server(ours), "this checkout's own server is no longer recognised"


def test_a_stray_dotfile_does_not_make_an_empty_tile_directory_look_populated(
    monkeypatch, tmp_path
):
    """Opening the folder in Finder drops a .DS_Store into it.

    `any(iterdir())` was true for that alone, so an empty tile directory
    certified the routing engine's map data and the failure resurfaced later as
    an obscure runtime error.
    """
    tiles = tmp_path / "custom_files"
    (tiles / "valhalla_tiles").mkdir(parents=True)
    (tiles / "valhalla_tiles" / ".DS_Store").write_bytes(b"")
    monkeypatch.setattr(preflight, "_tile_directory", lambda: tiles)

    assert preflight._probe_valhalla_tiles().ok is False, "a .DS_Store passed as map data"

    (tiles / "valhalla_tiles" / "2").mkdir()
    assert preflight._probe_valhalla_tiles().ok is True, "real tiles are no longer recognised"


def test_dev_data_refuses_to_certify_a_comparison_it_never_made(monkeypatch, tmp_path):
    """The parity check reports OK when its city loop is empty.

    A leaked ONBOARD_DATA_ROOT from an earlier hermetic run pointed it at an
    empty tree, so it compared nothing and passed.
    """
    monkeypatch.setattr(preflight, "_venv_python", lambda: Path(__file__))  # any real file
    monkeypatch.setattr(preflight, "_committed_cities", lambda: [])
    monkeypatch.setattr(
        preflight,
        "_run",
        lambda *a, **k: pytest.fail("parity was run despite there being nothing to compare"),
    )
    result = preflight._probe_dev_data()
    assert result.ok is False
    assert "nothing to compare" in result.detail


def test_a_leaked_data_root_cannot_redirect_the_parity_check(monkeypatch):
    monkeypatch.setenv("ONBOARD_DATA_ROOT", "/tmp/somewhere-else")
    monkeypatch.setenv("ONBOARD_REGISTRY_PATH", "/tmp/somewhere-else/cities.json")
    env = preflight._local_profile_env()
    assert "ONBOARD_DATA_ROOT" not in env, "a leaked data root would redirect the comparison"
    assert "ONBOARD_REGISTRY_PATH" not in env


def test_the_dev_data_repair_is_given_the_environment_it_requires():
    """It refuses outright without NEO4J_URI, so a bare invocation never worked.

    The probe passed the local profile and the repair did not -- so the most
    load-bearing self-repair in the system was dead on every machine, and it
    looked fine only because an already-provisioned graph means it never runs.
    """
    source = PREFLIGHT_PATH.read_text(encoding="utf-8")
    repair = source.split("def _repair_dev_data", 1)[1].split("\ndef ", 1)[0]
    assert "_local_profile_env()" in repair, (
        "the dev-data repair runs ensure_dev_data.py without the profile, which "
        "makes it refuse: 'REFUSING local data provisioning ... got \\'\\''"
    )


def test_every_listening_process_is_stopped_not_just_the_first(monkeypatch):
    """`lsof -t` prints one PID per line; a reloading uvicorn listens from two.

    Reading only the first signalled half the server, then blamed the PID that
    had already exited.
    """
    ours = f"{preflight.ROOT}/.venv/bin/python -m uvicorn src.api.app:app"
    listing = "4001\n4002\n"
    monkeypatch.setattr(
        preflight,
        "_run",
        lambda argv, **k: subprocess.CompletedProcess(
            [], returncode=0, stdout=(listing if argv[0] == "lsof" else ours), stderr=""
        ),
    )
    holders = preflight._port_listeners(8000)
    assert [h.pid for h in holders] == [4001, 4002], "only one of two listening processes was seen"


# ── 6. the mechanism itself cannot be swapped out ───────────────────────────


def test_a_dependency_cycle_is_refused(isolated_registry):
    """resolve()'s cycle branch had never once been proven to fire."""
    for name, needs in (("a", ("b",)), ("b", ("a",))):
        isolated_registry[name] = preflight.Requirement(
            name=name,
            summary=name,
            probe=lambda: preflight.Probe(True, "ok"),
            needs=needs,
            instruction="x",
        )
    with pytest.raises(SystemExit) as raised:
        preflight.resolve(["a"])
    assert "cycle" in str(raised.value)


def test_the_command_line_resolves_a_target_and_honours_the_autofix_switch(monkeypatch):
    """--target and PREFLIGHT_AUTOFIX were never driven through main()."""
    seen = {}

    def fake_check(names, *, autofix, label, colour, report_only=False):
        seen.update(names=list(names), autofix=autofix, label=label)
        return 0

    monkeypatch.setattr(preflight, "check", fake_check)

    monkeypatch.delenv("PREFLIGHT_AUTOFIX", raising=False)
    assert preflight.main(["--target", "lint"]) == 0
    assert seen["names"] == ["uv", "python-deps"], seen["names"]
    assert seen["autofix"] is True
    assert "lint" in seen["label"]

    monkeypatch.setenv("PREFLIGHT_AUTOFIX", "0")
    preflight.main(["--target", "lint"])
    assert seen["autofix"] is False, "PREFLIGHT_AUTOFIX=0 did not disable repairs"


def test_an_unknown_target_on_the_command_line_fails_cleanly(monkeypatch):
    monkeypatch.setattr(preflight, "check", lambda *a, **k: 0)
    assert preflight.main(["--target", "no-such-target"]) == 2


class _FakeResponse:
    """Just enough of an HTTP response for the healthz check."""

    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._body = body.encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeOpener:
    def __init__(self, response) -> None:
        self._response = response

    def open(self, url, timeout=None):
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def test_a_reusable_port_must_be_our_server_on_the_expected_graph(monkeypatch):
    """A 2xx on /healthz only proves SOMETHING answered.

    The endpoint reports which graph it is connected to precisely so callers can
    tell "a server" from "the right server". Accepting the status code alone let
    another project's server on the same convention, or ours pointed at the wrong
    database, count as a reusable instance. The earlier test stubbed this whole
    function out, so the real parsing was never exercised.
    """

    def use(body: str, status: int = 200):
        monkeypatch.setattr(preflight, "_DIRECT", _FakeOpener(_FakeResponse(status, body)))

    use('{"status":"ok","neo4j_uri":"bolt://localhost:7687","neo4j_connected":true}')
    assert preflight._serves_this_project(8000) is True

    # Ours, but pointed at the workbench graph -- not the dev graph a reusing
    # target expects.
    use('{"status":"ok","neo4j_uri":"bolt://localhost:7689","neo4j_connected":true}')
    assert preflight._serves_this_project(8000) is False, "the wrong graph was accepted"

    # Answers 200 on the path, but is not us.
    use('{"hello":"i am a different service"}')
    assert preflight._serves_this_project(8000) is False, "a foreign 2xx was accepted as ours"

    use('{"neo4j_uri":"bolt://localhost:7687","neo4j_connected":false}')
    assert preflight._serves_this_project(8000) is False, "a disconnected server was accepted"

    use("not json at all")
    assert preflight._serves_this_project(8000) is False

    monkeypatch.setattr(preflight, "_DIRECT", _FakeOpener(OSError("refused")))
    assert preflight._serves_this_project(8000) is False


def _scratch_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class _OnlyStatus(http.server.BaseHTTPRequestHandler):
    """The dashboard's shape: one route, and it is not healthz."""

    def do_GET(self):
        self.send_response(200 if self.path == "/api/status" else 404)
        self.end_headers()

    def log_message(self, *args):
        pass


class _SlowHealthz(http.server.BaseHTTPRequestHandler):
    """A healthy API whose healthz does a graph round-trip on a loaded machine."""

    def do_GET(self):
        time.sleep(3)
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


def _serving(handler):
    port = _scratch_port()
    server = http.server.HTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return port, server


def test_liveness_does_not_depend_on_one_route_answering_fast():
    """Drives REAL listeners. The previous version stubbed `_responding` to a
    constant, so it proved the branch and never the predicate -- and the
    predicate was the thing that was wrong.

    Two live servers were being read as dead and killed: `make dashboard`
    (src/server.py serves only /api/status, it has no healthz at all), and a
    healthy API whose healthz answers in 3s under load.
    """
    for label, handler in (("no healthz route", _OnlyStatus), ("slow healthz", _SlowHealthz)):
        port, server = _serving(handler)
        try:
            assert preflight._responding(port) is True, (
                f"a live server ({label}) was read as dead, which permits killing it"
            )
        finally:
            server.shutdown()

    assert preflight._responding(_scratch_port()) is False, (
        "a port with nothing on it must read as stale, or nothing is ever cleaned up"
    )


def test_a_live_server_of_this_checkout_is_not_stopped(monkeypatch):
    """Ownership is not disuse.

    The kill filter proves a server belongs to this checkout. It cannot prove
    nobody is using it -- a live server on :8001 is a sibling session's browser
    suite mid-run, and this repo's incident history is exactly that: a resource
    destroyed by a session that merely had the right to.
    """
    port, server = _serving(_OnlyStatus)
    ours = f"{preflight.ROOT}/.venv/bin/python -m uvicorn src.api.app:app --port {port}"
    monkeypatch.setattr(preflight, "_port_listeners", lambda p: [preflight.PortHolder(555, ours)])
    killed: list = []
    monkeypatch.setattr(
        preflight,
        "_run",
        lambda argv, **k: (
            killed.append(argv)
            or subprocess.CompletedProcess([], returncode=0, stdout="", stderr="")
        ),
    )
    try:
        result = preflight._repair_port(port)()
    finally:
        server.shutdown()

    assert result.ok is False
    assert not any("kill" in str(a) for a in killed), (
        "a live server was stopped -- that is a sibling session's suite, not a stale process"
    )
    assert "LIVE server" in result.detail


def test_a_port_whose_process_has_gone_is_still_cleaned_up(monkeypatch):
    """The conservative rule must not make the repair inert."""
    port = _scratch_port()  # nothing listening
    ours = f"{preflight.ROOT}/.venv/bin/python -m uvicorn src.api.app:app"
    monkeypatch.setattr(preflight, "_port_listeners", lambda p: [preflight.PortHolder(555, ours)])
    killed: list = []
    monkeypatch.setattr(
        preflight,
        "_run",
        lambda argv, **k: (
            killed.append(argv)
            or subprocess.CompletedProcess([], returncode=0, stdout="", stderr="")
        ),
    )
    monkeypatch.setattr(preflight, "_wait_for", lambda fn, *a, **k: True)
    monkeypatch.setattr(
        preflight, "_probe_port", lambda *a, **k: lambda: preflight.Probe(True, "free")
    )
    preflight._repair_port(port)()
    assert any("kill" in str(a) for a in killed), (
        "a genuinely dead listener was left in place, so nothing is ever cleaned up"
    )


def test_a_failed_question_is_never_read_as_a_dead_server(monkeypatch):
    """The checker's own failure is not evidence about the server.

    Only an active refusal -- the kernel saying nothing is bound -- may count as
    dead. A timeout, a dropped SYN, or a local resource failure is the question
    failing, and this is the KILL path, so ambiguity must spare.

    THE ERRORS HERE ARE INJECTED, not provoked: `socket.create_connection` is
    replaced with a stub that raises. No socket is opened and no listener is
    bound. That is deliberate -- genuinely exhausting file descriptors takes
    ~245,000 open files, and this suite shares a machine with sibling sessions,
    so provoking it in-process would be reckless.

    It was measured out of band instead, against a real listening socket with
    file descriptors really exhausted (245,754 opens, errno 24): the old
    `except OSError -> False` reported the LIVE port dead and would have sent it
    SIGKILL, while this rule reports it alive. This test pins that rule; it does
    not reproduce the measurement.
    """
    for error in (
        OSError(24, "Too many open files"),
        TimeoutError(),
        OSError(65, "No route to host"),
        PermissionError(1, "Operation not permitted"),
    ):

        def refuse_to_answer(*args, _error=error, **kwargs):
            raise _error

        monkeypatch.setattr(preflight.socket, "create_connection", refuse_to_answer)
        assert preflight._responding(9999) is True, (
            f"{type(error).__name__} was read as 'nothing is there', which permits a kill"
        )


def test_only_an_actual_refusal_counts_as_a_dead_port(monkeypatch):
    """...and the conservative rule must still let a dead port be cleaned up."""

    def refused(*args, **kwargs):
        raise ConnectionRefusedError(61, "Connection refused")

    monkeypatch.setattr(preflight.socket, "create_connection", refused)
    monkeypatch.setattr(preflight.time, "sleep", lambda _: None)
    assert preflight._responding(9999) is False

    # One refusal among answers that did not refuse is still not proof of death.
    calls = {"n": 0}

    def mixed(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionRefusedError(61, "Connection refused")
        raise TimeoutError()

    monkeypatch.setattr(preflight.socket, "create_connection", mixed)
    assert preflight._responding(9999) is True


def test_an_unanswerable_release_check_does_not_escalate_to_sigkill(monkeypatch):
    """`UNKNOWN != []` also fails the release wait.

    Letting a failed question drive a SIGKILL is the same mistake as reading it
    as "the port is free", pointed at the more destructive outcome.
    """
    ours = f"{preflight.ROOT}/.venv/bin/python -m uvicorn src.api.app:app"
    # First call (identity) sees the holder; the release check cannot be answered.
    state = {"n": 0}

    def listeners(port):
        state["n"] += 1
        if state["n"] == 1:
            return [preflight.PortHolder(555, ours)]
        return preflight.UNKNOWN

    monkeypatch.setattr(preflight, "_port_listeners", listeners)
    monkeypatch.setattr(preflight, "_responding", lambda port, **k: False)
    signals: list = []
    monkeypatch.setattr(
        preflight,
        "_run",
        lambda argv, **k: (
            signals.append(list(argv))
            or subprocess.CompletedProcess([], returncode=0, stdout="", stderr="")
        ),
    )
    monkeypatch.setattr(preflight, "_wait_for", lambda fn, *a, **k: fn())

    result = preflight._repair_port(8001)()
    assert result.ok is False
    assert not any("-9" in str(s) for s in signals), (
        "an unanswerable release check escalated to SIGKILL"
    )
    assert "refusing to escalate" in result.detail


def test_a_port_that_frees_itself_is_never_force_killed(monkeypatch):
    """SIGKILL requires a FRESH look that still shows the port held.

    The release wait can time out at the exact moment the server exits. Escalating
    on "not UNKNOWN" then treated an observed-FREE port as permission, and signalled
    the ORIGINAL snapshot -- PIDs that had already exited, which the OS may have
    reissued to something else. A SIGKILL to an innocent process, silent and
    untraceable to this code.
    """
    ours = f"{preflight.ROOT}/.venv/bin/python -m uvicorn src.api.app:app"
    looks = {"n": 0}

    def listeners(port):
        looks["n"] += 1
        # 1: identity. 2: the release wait, still held (so the wait fails).
        # 3: the fresh look -- it went free in the meantime.
        return [preflight.PortHolder(555, ours)] if looks["n"] <= 2 else []

    signals: list = []
    monkeypatch.setattr(preflight, "_port_listeners", listeners)
    monkeypatch.setattr(preflight, "_responding", lambda port, **k: False)
    monkeypatch.setattr(
        preflight,
        "_run",
        lambda argv, **k: (
            signals.append(list(argv))
            or subprocess.CompletedProcess([], returncode=0, stdout="", stderr="")
        ),
    )
    monkeypatch.setattr(preflight, "_wait_for", lambda fn, *a, **k: fn())
    monkeypatch.setattr(
        preflight, "_probe_port", lambda *a, **k: lambda: preflight.Probe(True, "free")
    )

    preflight._repair_port(8001)()
    assert not any("-9" in str(s) for s in signals), (
        "a port observed FREE was force-killed anyway, using a stale PID snapshot"
    )


def test_force_kill_targets_only_the_processes_still_holding_the_port(monkeypatch):
    """And when it does escalate, it signals the fresh list, not the snapshot."""
    ours = f"{preflight.ROOT}/.venv/bin/python -m uvicorn src.api.app:app"
    original = [preflight.PortHolder(101, ours), preflight.PortHolder(102, ours)]
    survivor = [preflight.PortHolder(102, ours)]
    looks = {"n": 0}

    def listeners(port):
        looks["n"] += 1
        if looks["n"] <= 2:
            return original  # identity, then the failed release wait
        if looks["n"] == 3:
            return survivor  # the fresh look: only 102 is left
        return []

    signals: list = []
    monkeypatch.setattr(preflight, "_port_listeners", listeners)
    monkeypatch.setattr(preflight, "_responding", lambda port, **k: False)
    monkeypatch.setattr(
        preflight,
        "_run",
        lambda argv, **k: (
            signals.append(list(argv))
            or subprocess.CompletedProcess([], returncode=0, stdout="", stderr="")
        ),
    )
    monkeypatch.setattr(preflight, "_wait_for", lambda fn, *a, **k: fn())
    monkeypatch.setattr(
        preflight, "_probe_port", lambda *a, **k: lambda: preflight.Probe(True, "free")
    )

    preflight._repair_port(8001)()
    forced = [s[-1] for s in signals if "-9" in s]
    assert forced == ["102"], (
        f"SIGKILL went to {forced}; PID 101 had already exited and its number may "
        "have been reissued to an unrelated process"
    )


def test_a_process_that_took_the_port_during_the_wait_is_never_force_killed(monkeypatch):
    """The two gates must run again on the CURRENT holders, not the first look.

    Ownership and liveness were checked on the opening snapshot only. Between
    SIGTERM and the escalation there is a 20-second window, and binding a
    just-freed port is ordinary. Measured: a sibling worktree's live server took
    :8000 mid-wait and was SIGKILLed -- a process this code's own rule calls a
    stranger, that it never asked whether anything was using.
    """
    ours = f"{preflight.ROOT}/.venv/bin/python -m uvicorn src.api.app:app"
    stranger = f"{preflight.ROOT}-wt1/.venv/bin/python -m uvicorn src.api.app:app"
    looks = {"n": 0}

    def listeners(port):
        looks["n"] += 1
        if looks["n"] <= 2:  # identity, then the release wait
            return [preflight.PortHolder(100, ours)]
        return [preflight.PortHolder(999, stranger)]  # someone else took it

    signals: list = []
    monkeypatch.setattr(preflight, "_port_listeners", listeners)
    monkeypatch.setattr(preflight, "_responding", lambda port, **k: False)
    monkeypatch.setattr(
        preflight,
        "_run",
        lambda argv, **k: (
            signals.append(list(argv))
            or subprocess.CompletedProcess([], returncode=0, stdout="", stderr="")
        ),
    )
    monkeypatch.setattr(preflight, "_wait_for", lambda fn, *a, **k: fn())

    result = preflight._repair_port(8000)()
    assert result.ok is False
    assert not any("-9" in str(s) for s in signals), (
        "a stranger that took the port during the wait was force-killed"
    )
    assert "999" in result.detail, "the new holder was not named"


def test_a_live_server_that_took_the_port_during_the_wait_is_never_force_killed(monkeypatch):
    """Same window, the liveness gate: ours, but now in use by another session."""
    ours = f"{preflight.ROOT}/.venv/bin/python -m uvicorn src.api.app:app"
    looks = {"n": 0}

    def listeners(port):
        looks["n"] += 1
        return [preflight.PortHolder(100 if looks["n"] <= 2 else 777, ours)]

    alive = {"n": 0}

    def responding(port, **kwargs):
        alive["n"] += 1
        return alive["n"] > 1  # dead at the opening look, alive by the escalation

    signals: list = []
    monkeypatch.setattr(preflight, "_port_listeners", listeners)
    monkeypatch.setattr(preflight, "_responding", responding)
    monkeypatch.setattr(
        preflight,
        "_run",
        lambda argv, **k: (
            signals.append(list(argv))
            or subprocess.CompletedProcess([], returncode=0, stdout="", stderr="")
        ),
    )
    monkeypatch.setattr(preflight, "_wait_for", lambda fn, *a, **k: fn())

    result = preflight._repair_port(8001)()
    assert result.ok is False
    assert not any("-9" in str(s) for s in signals), (
        "a live server that took the port during the wait was force-killed"
    )
