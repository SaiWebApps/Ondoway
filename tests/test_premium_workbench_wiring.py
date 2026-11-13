"""Pins that preview and batch generation cannot drift into separate algorithms."""

from __future__ import annotations

import importlib.util
import inspect
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import ClassVar

from src.tour import premium_tour
from src.tour.contract import (
    POI,
    BeatRef,
    BeatSequence,
    POIBeats,
    Route,
    Script,
    ScriptPOI,
    Sentence,
    TourInput,
    TransitSegment,
    ValidationReport,
)
from src.tour.glue_client import NO_GLUE_SENTINEL
from src.tour.premium_tour import (
    PREMIUM_MODULE_VERSION,
    plan_premium_tour,
)

ROOT = Path(__file__).resolve().parents[1]


def _declared_requirements(target: str):
    """What a Make target declares, read by the code preflight itself uses.

    preflight lives outside the importable package tree and must stay runnable on
    the system interpreter, so it is loaded by path.  It must be registered in
    sys.modules BEFORE it executes: @dataclass resolves annotations through
    sys.modules[cls.__module__].
    """
    name = "ondoway_preflight"
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "preflight.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module.declared_requirements(target)


_LISTENER_SRC = """
import socket, sys, time
port = int(sys.argv[1])
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", port))
# Backlog 16, not 1. Nothing ever calls accept(), so connections stay queued and the
# readiness probe below occupies a slot before the client arrives. PRECAUTIONARY — see
# the honest note on the wait loop; this was NOT shown to be the cause.
s.listen(16)
while True:
    time.sleep(1)
"""

_CLIENT_SRC = """
import pathlib, socket, sys, time
port = int(sys.argv[1])
ready = pathlib.Path(sys.argv[2])
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect(("127.0.0.1", port))
ready.write_text("established")  # written ONLY once the socket is ESTABLISHED
while True:
    time.sleep(1)
"""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _alive(proc: subprocess.Popen) -> bool:
    # `ps -p <pid>` still finds a just-killed, unreaped child (it shows as
    # <defunct>) — Popen.poll() reaps it via waitpid(WNOHANG) and reports the
    # real state.
    return proc.poll() is None


def _extract_port_free_snippet(script_text: str) -> str:
    """The bash block between the healthz-reuse `else` and the `cd "$ROOT"` that
    follows it — this is the launcher's port-freeing step for scripts/workbench.sh,
    regardless of exactly how that step is implemented."""
    else_marker = "\nelse\n"
    cd_marker = '\n  cd "$ROOT"\n'
    else_start = script_text.index(else_marker)
    cd_start = script_text.index(cd_marker, else_start)
    return script_text[else_start + len(else_marker) : cd_start]


def test_launchers_kill_only_listening_sockets(tmp_path) -> None:
    script_text = (ROOT / "scripts" / "workbench.sh").read_text()
    snippet = _extract_port_free_snippet(script_text)

    port = _free_port()

    listener_file = tmp_path / "listener.py"
    listener_file.write_text(_LISTENER_SRC)
    client_file = tmp_path / "client.py"
    client_file.write_text(_CLIENT_SRC)

    listener = subprocess.Popen([sys.executable, str(listener_file), str(port)])
    client = None
    try:
        # Wait for the listener to actually be bound before connecting a client.
        for _ in range(50):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.2)
                try:
                    probe.connect(("127.0.0.1", port))
                    break
                except OSError:
                    time.sleep(0.1)
        else:
            raise RuntimeError("listener fixture never came up")

        # WAIT ON THE CONDITION, NEVER ON THE CLOCK. This was `time.sleep(0.5)`.
        #
        # HONEST STATUS (2026-07-27): this test WAS flaky — it failed 1 of 3 runs in
        # isolation, with the client exiting rc=1 on ConnectionRefusedError, which made
        # the assertion below report "a client/ESTABLISHED socket must never be
        # signalled" and blame the launcher for a fixture fault. After this change it
        # passed 8 of 8. But the ROOT CAUSE IS NOT ESTABLISHED, and two hypotheses were
        # tested and REFUTED rather than assumed:
        #   - "0.5 s is too short for interpreter startup" — measured: the client reaches
        #     ESTABLISHED in 23-32 ms over 10 runs, 0/10 anywhere near the budget.
        #   - "listen(1)'s backlog is exhausted by the readiness probe" — measured:
        #     0/15 connect failures at backlog 1, and 0/15 at 16.
        # So waiting on the ready file is a genuine robustness improvement and the right
        # shape regardless, but do NOT record this flake as diagnosed. If it recurs,
        # start from the port-freeing snippet itself — the one part neither experiment
        # exercised — and capture the client's stderr, which is currently discarded.
        ready_file = tmp_path / "client-established"
        client = subprocess.Popen(
            [sys.executable, str(client_file), str(port), str(ready_file)]
        )
        for _ in range(100):  # up to 10s
            if ready_file.exists():
                break
            if not _alive(client):
                raise RuntimeError(
                    f"client exited (rc={client.returncode}) before connecting — a "
                    "fixture fault, not a failure of the launcher under test"
                )
            time.sleep(0.1)
        else:
            raise RuntimeError("client never reached ESTABLISHED within 10s")

        assert _alive(listener)
        assert _alive(client)

        try:
            expected_cmd = subprocess.run(
                ["ps", "-p", str(listener.pid), "-o", "comm="],
                capture_output=True,
                text=True,
            ).stdout.strip()
        except PermissionError:
            lsof_out = subprocess.run(
                ["lsof", "-p", str(listener.pid), "-Fc"],
                capture_output=True,
                text=True,
            ).stdout
            expected_cmd = next(
                (line[1:] for line in lsof_out.splitlines() if line.startswith("c")),
                "",
            )

        result = subprocess.run(
            ["bash", "-c", f"PORT={port}\n{snippet}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        output = result.stdout + result.stderr

        # The listener PID is in the selected set and gets terminated.
        deadline = time.time() + 5
        while time.time() < deadline and _alive(listener):
            time.sleep(0.1)
        assert not _alive(listener), "the LISTEN-state process must be killed"

        # A process whose only socket on the port is client-side
        # (ESTABLISHED here — the same defect class as the CLOSED sockets a real
        # Claude-desktop client left behind) is never signalled.
        assert _alive(client), (
            "a client/ESTABLISHED socket on the port must never be signalled"
        )

        # The PID and command name are printed before signalling — not a
        # silent kill. The script's own `ps` may fail (macOS Developer Tool TCC)
        # and fall back to "(unknown)", which is acceptable — the kill still works.
        assert str(listener.pid) in output, "must print the killed PID"
        if expected_cmd:
            assert expected_cmd in output or "(unknown)" in output, (
                "must print the killed process's command name or (unknown)"
            )
        else:
            assert "(unknown)" in output, (
                "when ps is unavailable the script must still print a label"
            )
    finally:
        for proc in (client, listener):
            if proc is not None and proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)

    # Same defect class in the flutter-ios launcher. It no longer frees the port
    # itself: it declares the `port-8000` requirement, and preflight both scopes
    # the query to LISTEN sockets AND refuses to signal a process it cannot
    # identify as this project's own server -- neither of which the inlined shell
    # kill ever did. So the requirement here is that flutter-ios does not
    # hand-roll a kill at all; if it ever does again, it must be LISTEN-scoped.
    makefile = (ROOT / "Makefile").read_text()
    flutter_ios_target = makefile.split("\nflutter-ios:", 1)[1].split(
        "\n\nflutter-device:", 1
    )[0]
    if "lsof" in flutter_ios_target:
        assert "-sTCP:LISTEN" in flutter_ios_target, (
            "flutter-ios re-introduced a port kill that is not LISTEN-scoped"
        )
    else:
        assert "port-8000" in _declared_requirements("flutter-ios"), (
            "flutter-ios neither frees :8000 itself nor declares the requirement "
            "that does it safely"
        )
    assert "-ti:8000" not in flutter_ios_target.replace("-tiTCP:8000", ""), (
        "no unscoped lsof -ti:PORT kill should remain"
    )


# --- A persisted-trip-shaped fixture, deliberately local to this file. -------------
# It is NOT imported from tests/test_tour_authoring_from_route.py even though that
# module has an equivalent one today: that module exists to exercise
# ``author_prebuilt_route``/``plan_prebuilt_route_authoring``, which are due for deletion, so
# importing across would tie this pin's life to a module scheduled for removal.


def _prebuilt(
    stop_count: int, *, sentences_per_stop: int = 1
) -> tuple[Script, BeatSequence, Route]:
    """A (stitched script, beat sequence, route) triple shaped like a persisted trip.

    ``sentences_per_stop > 1`` gives a stop several sentences, which is what makes
    "one unit per DWELL STOP" a different claim from "one unit per sentence".
    The route is a round trip, so it has one more transit leg than it has stops.
    """
    pois = tuple(
        POI(
            id=f"p{index}",
            name=f"Stop {index}",
            tier=3,
            poi_role="anchor",
            lat=48.85 + index / 1000,
            lng=2.35 + index / 1000,
        )
        for index in range(stop_count)
    )
    leg_count = stop_count + 1
    transits = tuple(
        TransitSegment(
            from_poi_id=None if index == 0 else pois[index - 1].id,
            to_poi_id=pois[index].id if index < stop_count else pois[0].id,
            distance_m=120.0,
            walk_seconds=100,
            leg_seconds=110,
            leg_distance_m=130.0,
            source="haversine",
        )
        for index in range(leg_count)
    )
    route = Route(
        pois=pois,
        transits=transits,
        total_walk_distance_m=120.0 * leg_count,
        total_walk_seconds=100 * leg_count,
    )
    beats_by_stop = {
        index: tuple(
            BeatRef(
                id=f"b{index}_{part}",
                poi_id=f"p{index}",
                script_body=(
                    f"The stone here was cut in the year of the flood, "
                    f"stop {index} part {part}."
                ),
                key_claims=(f"stop {index} part {part} has cut stone",),
            )
            for part in range(sentences_per_stop)
        )
        for index in range(stop_count)
    }
    sequence = BeatSequence(
        poi_beats=tuple(
            POIBeats(
                poi_id=f"p{index}",
                poi_name=f"Stop {index}",
                ordering_strategy="narrative_function",
                beats=beats_by_stop[index],
            )
            for index in range(stop_count)
        )
    )
    stitched = Script(
        city_slug="paris",
        generated_at="2026-07-30T00:00:00Z",
        inputs=TourInput(start=(48.85, 2.35), duration_min=60, city_slug="paris"),
        total_audio_seconds=stop_count * 12,
        total_walking_seconds=100 * leg_count,
        total_walk_distance_m=120.0 * leg_count,
        total_planned_seconds=3600,
        selected_pois=tuple(
            ScriptPOI(
                id=poi.id,
                name=poi.name,
                tier=poi.tier,
                lat=poi.lat,
                lng=poi.lng,
                beat_ids=tuple(beat.id for beat in beats_by_stop[index]),
            )
            for index, poi in enumerate(pois)
        ),
        lens_coverage={},
        script=tuple(
            Sentence(
                text=beat.script_body or "",
                source_id=beat.id,
                source_type="beat",
                stop_idx=index,
            )
            for index in range(stop_count)
            for beat in beats_by_stop[index]
        ),
        validation=ValidationReport(),
    )
    return stitched, sequence, route


def _drop_stop(stitched: Script, stop_index: int) -> Script:
    """The stitched script a dwell stop fell out of — no sentence carries it."""
    return stitched.model_copy(
        update={
            "script": tuple(
                sentence for sentence in stitched.script if sentence.stop_idx != stop_index
            )
        }
    )


def _renumber_stop(stitched: Script, stop_index: int, new_index: int) -> Script:
    """The stitched script a stop was mis-numbered in — it names a stop off the route.

    This is reachable in production, not a synthetic shape: the compose path derives
    its stop list from raw ``sentence.stop_idx`` values and never range-checks them
    against ``route.pois`` (``src/tour/authoring.py`` ``_certification_compose_requests``),
    so a script naming stop 7 on a four-stop route arrives at the plan builder intact.
    Kept the same LENGTH as the route's stop count on purpose, so it trips the
    out-of-range refusal and not the one-unit-per-dwell-stop count refusal.
    """
    return stitched.model_copy(
        update={
            "script": tuple(
                sentence.model_copy(update={"stop_idx": new_index})
                if sentence.stop_idx == stop_index
                else sentence
                for sentence in stitched.script
            )
        }
    )


def _plan_kwargs() -> dict[str, object]:
    return {
        "snapshot": None,
        "snapshot_sha256": "0" * 64,
        "routing_version": "offline-test",
        "policy_version": "offline-test",
    }


# --------------------------------------------------------------------------
# BLOCK 1 plans ONE day, and planning is genuinely free
# --------------------------------------------------------------------------

_BLOCK_ONE_BASE = (48.8568, 2.3414)


class _Poison:
    """Any construction is a failure. Stands in for a PAID client."""

    def __init__(self, *args, **kwargs):
        raise AssertionError(
            "Block 1 constructed a paid provider client. Planning shows places, order "
            "and walking times only (OWNER RULING 1); every word arrives in AUTHOR, "
            "after the traveller has picked one option."
        )


class _RecordingSilentGlue:
    """The silent client, instrumented: records what it was asked, writes nothing."""

    asked: ClassVar[list[str]] = []

    def stitch(self, category: str, context: str, request: str) -> str:
        _RecordingSilentGlue.asked.append(category)
        del context, request
        return NO_GLUE_SENTINEL


def _receipted_transit(from_id, to_id, a_lat, a_lng, b_lat, b_lng) -> TransitSegment:
    from tests.test_trip_preview_contract import _FakeRoutingClient

    seconds, distance_m, polyline, receipt = _FakeRoutingClient().route_with_receipt(
        a_lat, a_lng, b_lat, b_lng
    )
    return TransitSegment(
        from_poi_id=from_id,
        to_poi_id=to_id,
        distance_m=distance_m,
        walk_seconds=seconds,
        leg_seconds=seconds,
        leg_distance_m=distance_m,
        polyline=polyline,
        source="valhalla",
        valhalla_receipt=receipt,
    )


def _one_route():
    """One receipt-backed route over three disjoint POIs."""
    from tests.test_tour_selection import _poi

    pois = tuple(
        _poi(
            f"day-p{i}",
            lat=_BLOCK_ONE_BASE[0] + 0.0006,
            lng=_BLOCK_ONE_BASE[1] + 0.0008 * (i + 1),
        )
        for i in range(3)
    )
    prev = _BLOCK_ONE_BASE
    transits = []
    for poi in pois:
        transits.append(_receipted_transit(None, poi.id, prev[0], prev[1], poi.lat, poi.lng))
        prev = (poi.lat, poi.lng)
    return Route(
        pois=pois,
        transits=tuple(transits),
        total_walk_distance_m=sum(t.distance_m for t in transits),
        total_walk_seconds=sum(t.walk_seconds for t in transits),
        routed=True,
    )


def _snapshot_for(routes):
    from tests.test_tour_selection import _snap

    pois = [p for route in routes for p in route.pois]
    return _snap(
        pois,
        beats_by_poi={
            p.id: [
                BeatRef(
                    id=f"{p.id}-b{i}",
                    poi_id=p.id,
                    est_spoken_seconds=120,
                    active_status="active",
                    script_body=f"Story {i} at {p.name}. It runs on a little.",
                )
                for i in range(3)
            ]
            for p in pois
        },
    )


def test_block_one_plans_one_priced_free_day(monkeypatch) -> None:
    """ONE day, no provider, no prose.

    The product stops offering three routes: no visitor wants to compare
    routes. The product plans THE day, and the dials on the
    request are how a person changes it. Block 1 is therefore ``plan_premium_tour``
    itself: ONE ``select_route`` call, the container-identity refusal kept, and
    no K-flavour machinery anywhere in the module.

    The free half is asserted ON THE DEFAULT PATH, with no glue client passed,
    because that is the only version of the claim that survives the next
    refactor: a path that is free only when a caller remembers to ask is one
    edit away from being paid again.

    UNDO (each turns this RED):
      * route the entry back through a K-selector (any ``select_k_routes``
        reference in the module) -> assertion 1;
      * drop the ``SilentGlueClient()`` substitution from ``plan_premium_tour``
        -> assertion 4;
      * author anything at plan time (construct a paid client) -> the _Poison
        arms raise.
    """
    from tests.test_trip_preview_contract import _FakeRoutingClient

    # 1. STRUCTURAL: the one-day entry plans via select_route, and the K-flavour
    #    selector is gone from the module entirely (this module may not even
    #    name it).
    module_source = inspect.getsource(premium_tour)
    entry = inspect.getsource(premium_tour.plan_premium_tour)
    assert "select_route(" in entry, "plan_premium_tour is not the one-day planner"
    assert "select_k_routes" not in module_source, (
        "the K-flavour selector is still referenced by premium_tour; §8.1 deleted "
        "pick-one-of-three"
    )

    route = _one_route()
    snapshot = _snapshot_for([route])
    tour_input = TourInput(start=_BLOCK_ONE_BASE, duration_min=60, city_slug="paris")
    monkeypatch.setattr(premium_tour, "select_route", lambda *a, **k: route)
    # THE PROVIDER THAT RAISES ON ANY CALL, on both doors it could come through.
    monkeypatch.setattr("src.tour.generation.HaikuGlueClient", _Poison)
    monkeypatch.setattr(premium_tour, "AnthropicPremiumExecutor", _Poison)

    plan = plan_premium_tour(
        tour_input,
        snapshot,
        routing_client=_FakeRoutingClient(),
    )

    # 2. THE day: the plan is built on exactly the selected route.
    assert [p.id for p in plan.route.pois] == [p.id for p in route.pois]

    # 3. A complete, authorable plan — one compose unit per dwell stop.
    assert len(plan.units) == len(plan.route.pois)
    assert plan.policy_version == PREMIUM_MODULE_VERSION

    # 4. THE FREE CLAIM: nothing was authored and no paid client was ever built.
    #    _Poison raises in __init__, so reaching here at all is the proof — but assert
    #    the DEFAULT client is the silent one rather than trusting the poison fired.
    assert isinstance(premium_tour.SilentGlueClient().stitch("GLUE_NAV", "", ""), str)
    assert premium_tour.SilentGlueClient().stitch("GLUE_STAGING", "", "").startswith("<<NO_GLUE")
    glue_default = inspect.signature(plan_premium_tour).parameters["glue_client"].default
    assert glue_default is None, "the parameter still exists for AUTHOR to pass a real one"
    assert "SilentGlueClient()" in entry, (
        "plan_premium_tour no longer substitutes the silent client for None, so a "
        "default-path plan falls through to generate's PAID Haiku client"
    )

    # 4b. BEHAVIOURAL: on the default path every stitch request really does go to the
    #     silent client and comes back as the NO_GLUE sentinel, so nothing an LLM could
    #     have written is in the plan. ``generate`` DOES ask for connective text — the
    #     claim is not that nobody asks, it is that nobody bills.
    _RecordingSilentGlue.asked = []
    monkeypatch.setattr(premium_tour, "SilentGlueClient", _RecordingSilentGlue)
    quiet = plan_premium_tour(
        tour_input,
        snapshot,
        routing_client=_FakeRoutingClient(),
    )
    assert [p.id for p in quiet.route.pois] == [p.id for p in route.pois]
    assert _RecordingSilentGlue.asked, (
        "no stitch request reached the silent client on the default path, so this "
        "assertion is not watching the thing it claims to watch"
    )

    # 5. A start with no end plans through without a 4xx-shaped failure.
    assert tour_input.end is None

    # 6. A ROUND TRIP plans through the premium gate (a live finding:
    #    the leg-per-stop sanity check refused every round trip, because the
    #    loop-home leg makes legs == stops + 1 — the shape _prebuilt in this
    #    very file has always declared).
    loop = route.model_copy(
        update={
            "transits": (
                *route.transits,
                _receipted_transit(
                    route.pois[-1].id,
                    None,
                    route.pois[-1].lat,
                    route.pois[-1].lng,
                    _BLOCK_ONE_BASE[0],
                    _BLOCK_ONE_BASE[1],
                ),
            )
        }
    )
    monkeypatch.setattr(premium_tour, "select_route", lambda *a, **k: loop)
    round_trip_plan = plan_premium_tour(
        TourInput(
            start=_BLOCK_ONE_BASE, duration_min=60, city_slug="paris", round_trip=True
        ),
        snapshot,
        routing_client=_FakeRoutingClient(),
    )
    assert len(round_trip_plan.units) == len(loop.pois)
