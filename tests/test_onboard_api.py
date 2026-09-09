"""Step-5 onboarding API router (src/api/routes/onboard.py) — TestClient bar.

Hermetic: ``ONBOARD_PROVIDER=mock`` (conftest) + fixture-mode HTTP + a patched
deploy seam, so nothing here spends an Anthropic credit or touches the network /
a real deploy. London bbox = [51.28, 51.7, -0.51, 0.33]; its committed connector
fixtures live under tests/fixtures/onboard/london/.

Run one file: make test-file FILE=tests/test_onboard_api.py
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient

import src.api.routes.onboard as onboard_mod
from src import city_registry
from src.api.app import create_app
from src.onboard.beat_draft import estimate_cost, planned_beat_count

LONDON_BBOX = [51.28, 51.7, -0.51, 0.33]

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client():
    """A fresh TestClient per test (no Neo4j needed — the onboard endpoints don't
    touch the graph, and the deploy subprocess is patched). Overrides the
    conftest module-scoped ``client`` so these tests stay hermetic."""
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture(autouse=True)
def _london_free_registry(monkeypatch):
    """Run these hermetic tests against a registry view that EXCLUDES ``london``.

    ``london`` is a REAL registered city in ``src/cities.json`` (its corpus has since
    been retired to ``_to_be_deleted/``),
    so ``create_job``'s "already onboarded" guard would 422 every happy-path setup
    here — but the fixtures are london-specific (``tests/fixtures/onboard/london/``),
    so the tests must keep using slug ``london``. Surgically drop ONLY london from
    the guard's view; every OTHER registered city (``paris``, ``new_york``) still
    counts as onboarded, so ``test_onboarding_an_existing_city_is_rejected`` (POST
    paris -> 422) still exercises the guard. Touches only the ``create_job`` guard —
    the upload/write path uses its own tmp ``ONBOARD_REGISTRY_PATH``."""
    real = city_registry.supported_cities()
    monkeypatch.setattr(city_registry, "supported_cities", lambda: real - {"london"})


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------
def _create_job(client, modes=("license_clean",)):
    return client.post(
        "/api/v1/onboard/jobs",
        json={
            "slug": "london",
            "display_name": "London",
            "bbox": LONDON_BBOX,
            "modes": list(modes),
        },
    )


def _wait_status(client, job_id, target, timeout=15.0):
    """Poll the snapshot endpoint until the job reaches ``target`` (or errors)."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        r = client.get(f"/api/v1/onboard/jobs/{job_id}")
        assert r.status_code == 200, r.text
        last = r.json()
        if last["status"] == target:
            return last
        if last["status"] == "error":
            raise AssertionError(f"job errored: {last.get('error')!r}")
        time.sleep(0.05)
    raise AssertionError(f"job {job_id!r} never reached {target!r}; last={last}")


def _run_to_assembled(client):
    r = _create_job(client)
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    return job_id, _wait_status(client, job_id, "assembled")


# ---------------------------------------------------------------------------
# 1. Full mock flow -> assembled, >=30 POIs, a source_consult per source.
# ---------------------------------------------------------------------------
def test_full_mock_flow_assembles_with_source_consults(client):
    """POST jobs -> snapshot reaches ``assembled`` with >=30 POIs and >=1
    ``source_consult`` per selected source, each carrying a concrete URL.

    UNDO: drop the source_consult emit in flow.consult_source -> this goes RED
    (a source has no consult event / no url)."""
    _job_id, snap = _run_to_assembled(client)
    assert snap["status"] == "assembled"
    assert snap["result"]["poi_count"] >= 30, snap["result"]["poi_count"]

    consults: dict[str, list[str]] = {}
    for e in snap["events"]:
        if e["kind"] == "source_consult":
            consults.setdefault(e["source"], []).append(e["url"])

    for source in ("wikipedia", "wikivoyage", "wikidata", "osm"):
        assert source in consults, f"no source_consult for {source}: {consults}"
        assert all(
            (u or "").startswith("http") for u in consults[source]
        ), f"{source} consult(s) lack a concrete url: {consults[source]}"


# ---------------------------------------------------------------------------
# 2. Stream is SSE, has >=4 data frames, and TERMINATES with one end frame.
# ---------------------------------------------------------------------------
def test_stream_is_event_stream_and_terminates(client, monkeypatch):
    """The stream is ``text/event-stream``, carries >=4 ``data:`` frames, and
    CLOSES after exactly one trailing ``event: end`` frame — and a NON-terminating
    stream fails FAST, never waiting on the server's 60 s valve.

    Why not an httpx read-timeout or a bare thread timeout: Starlette's test
    transport BUFFERS a streaming response (it delivers no bytes until the ASGI
    generator finishes), so an httpx ``read`` timeout never fires AND even
    ``TestClient`` teardown blocks on an in-flight non-terminating stream (both
    verified). The only bound that actually holds is the server's OWN wall-clock
    valve, so we tighten it to 2 s for this test: a broken generator is
    force-closed in ~2 s (not 60), while a healthy one still returns immediately
    after its single terminal ``end``. A worker thread + join is the client-side
    guard that keeps the assertions from ever blocking on the read.

    UNDO: remove the ``return`` after the terminal end yield -> the generator
    re-emits ``event: end`` until the (tightened) 2 s valve; the buffered body
    then carries MANY end frames -> ``len(end_frames) == 1`` goes RED in ~2 s."""
    monkeypatch.setattr(onboard_mod, "_SSE_MAX_SECONDS", 2.0)
    job_id, _ = _run_to_assembled(client)

    frames: list[str] = []
    errors: list[Exception] = []

    def _read() -> None:
        try:
            with client.stream("GET", f"/api/v1/onboard/jobs/{job_id}/stream") as resp:
                assert resp.status_code == 200
                assert resp.headers["content-type"].startswith("text/event-stream")
                for line in resp.iter_lines():
                    frames.append(line)
        except Exception as exc:  # surface any read/assert failure to the main thread
            errors.append(exc)

    t = threading.Thread(target=_read, daemon=True)
    t.start()
    t.join(timeout=6.0)  # > the 2 s valve: a bounded stream always finishes first

    assert not t.is_alive(), "stream read never completed — the stream did not terminate"
    assert not errors, f"stream read raised: {errors!r}"

    data_frames = [f for f in frames if f.startswith("data:")]
    end_frames = [f for f in frames if f.startswith("event: end")]
    assert len(data_frames) >= 4, frames
    assert len(end_frames) == 1, f"stream did not terminate cleanly: {len(end_frames)} end frame(s)"


# ---------------------------------------------------------------------------
# 3. draft-beats cost gate: 409 + estimate with ZERO LLM calls; confirm -> 200.
# ---------------------------------------------------------------------------
def test_draft_beats_cost_gate_is_zero_llm(client, monkeypatch):
    """confirm_cost=false returns 409 + a dollar estimate and makes ZERO LLM
    calls even with ONBOARD_PROVIDER=anthropic (the guard returns before any
    drafter/client is built); confirm_cost=true on mock returns 200 with the
    beat count.

    UNDO: remove the ``if not confirm_cost`` guard -> with anthropic selected the
    fake client's ``messages.create`` fires (counter > 0) and status is 200 not
    409 -> RED."""
    job_id, snap = _run_to_assembled(client)
    n = snap["result"]["poi_count"]

    # The estimate AND the drafted count are now MULTI-beat (~3 beats/POI), sized
    # off the assembled artifacts (each POI-with-extract yields several verbatim
    # spans), NOT the POI count. Compute the exact expectation from the same
    # artifacts the endpoint sees, and assert the multi-beat PROPERTY, not a literal.
    art = onboard_mod._get_artifacts(job_id)
    expected_beats = planned_beat_count(art.assembled.pois, art.assembled.wiki_extracts)
    assert expected_beats > n, f"estimate must be multi-beat (> {n} POIs), got {expected_beats}"
    assert expected_beats >= 3 * 30, f"multi-beat did not happen: {expected_beats}"

    calls = {"n": 0}

    class _FakeMessage:
        content: ClassVar[list] = []

    class _FakeMessages:
        def create(self, **kwargs):
            calls["n"] += 1
            return _FakeMessage()

    class _FakeAnthropic:
        def __init__(self, *args, **kwargs):
            self.messages = _FakeMessages()

    monkeypatch.setattr("anthropic.Anthropic", _FakeAnthropic)

    monkeypatch.setenv("ONBOARD_PROVIDER", "anthropic")
    r = client.post(f"/api/v1/onboard/jobs/{job_id}/draft-beats", json={"confirm_cost": False})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["estimate"]["beats"] == expected_beats, detail
    assert detail["estimate"]["est_usd"] == estimate_cost(expected_beats)["est_usd"]
    assert calls["n"] == 0, "cost gate must be structurally zero-LLM"

    monkeypatch.setenv("ONBOARD_PROVIDER", "mock")
    r2 = client.post(f"/api/v1/onboard/jobs/{job_id}/draft-beats", json={"confirm_cost": True})
    assert r2.status_code == 200, r2.text
    assert r2.json()["beats_count"] == expected_beats
    assert r2.json()["beats_count"] >= n, "every POI-with-extract yields >= 1 beat"
    assert calls["n"] == 0, "mock drafting must never touch the anthropic client"


# ---------------------------------------------------------------------------
# 4. SECURITY-CRITICAL: router absent when the workbench is disabled.
# ---------------------------------------------------------------------------
def test_onboard_router_absent_when_workbench_disabled(monkeypatch):
    """WORKBENCH_API_ENABLED=false -> the onboard router is NOT mounted: no
    /api/v1/onboard/jobs route in the table AND a POST 404s, while the
    mobile-facing /api/v1/healthz stays up.

    UNDO: move the include_router(onboard...) OUTSIDE the _workbench_api_enabled()
    gate -> the route is present -> this goes RED."""
    monkeypatch.setenv("WORKBENCH_API_ENABLED", "false")
    app = create_app()
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/api/v1/onboard/jobs" not in paths, "onboard router must be gated OFF in prod"
    with TestClient(app) as c:
        r = c.post(
            "/api/v1/onboard/jobs",
            json={
                "slug": "london",
                "display_name": "London",
                "bbox": LONDON_BBOX,
                "modes": ["license_clean"],
            },
        )
        assert r.status_code == 404
        assert c.get("/api/v1/healthz").status_code == 200


# ---------------------------------------------------------------------------
# 5. upload: writes the city hermetically + pins the API's OWN NEO4J_* env.
# ---------------------------------------------------------------------------
def test_upload_writes_city_and_pins_neo4j_env(client, monkeypatch, tmp_path):
    """upload writes the london corpus under a tmp root and calls the deploy seam once
    with the deploy cmd and a COPY of the API's own env (NEO4J_URI carried
    through).

    UNDO: build the deploy env WITHOUT os.environ (e.g. env={}) -> the child
    loses NEO4J_URI -> ``env['NEO4J_URI'] == sentinel`` goes RED."""
    job_id, _ = _run_to_assembled(client)
    r = client.post(f"/api/v1/onboard/jobs/{job_id}/draft-beats", json={"confirm_cost": True})
    assert r.status_code == 200, r.text

    sentinel = "neo4j+s://sentinel-onboard.databases.neo4j.io:7687"
    monkeypatch.setenv("NEO4J_URI", sentinel)
    monkeypatch.setenv("ONBOARD_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("ONBOARD_REGISTRY_PATH", str(tmp_path / "cities.json"))

    calls: list[tuple[list[str], dict]] = []

    def _fake_deploy(cmd, env):
        calls.append((list(cmd), dict(env)))
        return subprocess.CompletedProcess(cmd, 0, stdout="deployed", stderr="")

    monkeypatch.setattr(onboard_mod, "_run_deploy", _fake_deploy)

    r2 = client.post(f"/api/v1/onboard/jobs/{job_id}/upload", json={"target": "local"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "uploaded"

    assert len(calls) == 1, "deploy must be invoked exactly once"
    cmd, env = calls[0]
    assert cmd == [sys.executable, "-m", "scripts.deploy", "--slug", "london"], cmd
    assert env["NEO4J_URI"] == sentinel, "the deploy env must inherit the API's own NEO4J_URI"
    assert (tmp_path / "london" / "poi-raw.json").exists()
    assert (tmp_path / "london" / "beats.json").exists()


# ---------------------------------------------------------------------------
# 6. upload rejects a cloud target (cloud is deferred to Step 8).
# ---------------------------------------------------------------------------
def test_upload_rejects_cloud_target(client):
    job_id, _ = _run_to_assembled(client)
    r = client.post(f"/api/v1/onboard/jobs/{job_id}/upload", json={"target": "cloud"})
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# 7. an unknown job id is 404 on every endpoint.
# ---------------------------------------------------------------------------
def test_unknown_job_is_404_everywhere(client):
    jid = "does-not-exist-00000000"
    assert client.get(f"/api/v1/onboard/jobs/{jid}").status_code == 404
    assert client.get(f"/api/v1/onboard/jobs/{jid}/stream").status_code == 404
    assert (
        client.post(
            f"/api/v1/onboard/jobs/{jid}/draft-beats", json={"confirm_cost": True}
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/onboard/jobs/{jid}/upload", json={"target": "local"}
        ).status_code
        == 404
    )


# ---------------------------------------------------------------------------
# FIX 1 (SECURITY): a path-traversal slug + re-onboarding an existing city are
# both refused at job creation (422), BEFORE any disk write can escape data_root.
# ---------------------------------------------------------------------------
def test_traversal_slug_is_rejected_and_writes_nothing(client, tmp_path, monkeypatch):
    """A hostile ``slug='../../fixtures/onboard/london'`` is refused 422 at job
    creation — before the background flow or any ``write_city`` runs, so nothing
    is written and the traversal target is never touched.

    UNDO: drop the slug validation in create_job (and the CityContext validator)
    -> the job is accepted 202 -> ``r.status_code == 422`` goes RED."""
    monkeypatch.setenv("ONBOARD_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("ONBOARD_REGISTRY_PATH", str(tmp_path / "cities.json"))

    evil_slug = "../../fixtures/onboard/london"
    r = client.post(
        "/api/v1/onboard/jobs",
        json={
            "slug": evil_slug,
            "display_name": "Evil",
            "bbox": LONDON_BBOX,
            "modes": ["license_clean"],
        },
    )
    assert r.status_code == 422, r.text

    # The rejection happens before the flow even starts: no data root dir, and
    # the committed fixtures the traversal would target are untouched.
    assert not (tmp_path / evil_slug).exists()
    assert not any(tmp_path.iterdir()) if tmp_path.exists() else True
    # The real fixtures dir the traversal resolves to keeps only its committed
    # tree (no poi-raw.json / beats.json written into it by an onboarding run).
    fixtures_london = REPO_ROOT / "tests" / "fixtures" / "onboard" / "london"
    assert not (fixtures_london / "beats.json").exists()


def test_onboarding_an_existing_city_is_rejected(client):
    """Re-onboarding an ALREADY-registered city (``paris``) — a format-valid slug
    that would CLOBBER committed data/paris/ + flip its registry entry — is
    refused 422, and the committed data/paris/poi-raw.json is byte-unchanged.

    UNDO: drop the ``supported_cities()`` guard in create_job -> paris is accepted
    202 -> ``r.status_code == 422`` goes RED."""
    paris_poi = REPO_ROOT / "data" / "paris" / "poi-raw.json"
    before = paris_poi.read_bytes()

    r = client.post(
        "/api/v1/onboard/jobs",
        json={
            "slug": "paris",
            "display_name": "Paris",
            "bbox": [48.7, 49.0, 2.1, 2.6],
            "modes": ["license_clean"],
        },
    )
    assert r.status_code == 422, r.text
    assert "already" in r.json()["detail"].lower()
    assert paris_poi.read_bytes() == before, "committed data/paris/poi-raw.json was mutated"


# ---------------------------------------------------------------------------
# FIX 2 (robustness): a deploy that RAISES (spawn failure) must mark the job
# 'error' and 500 — never leave it stuck at 'assembled'. A non-zero returncode
# also 500s + marks error + tails stderr.
# ---------------------------------------------------------------------------
def _run_to_drafted(client, monkeypatch, tmp_path):
    """Drive a job to assembled + beats-drafted under a tmp data root."""
    monkeypatch.setenv("ONBOARD_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("ONBOARD_REGISTRY_PATH", str(tmp_path / "cities.json"))
    job_id, _ = _run_to_assembled(client)
    r = client.post(f"/api/v1/onboard/jobs/{job_id}/draft-beats", json={"confirm_cost": True})
    assert r.status_code == 200, r.text
    return job_id


def test_upload_deploy_raise_sets_error_status(client, monkeypatch, tmp_path):
    """When the deploy seam RAISES (e.g. a spawn failure), upload returns 500 AND
    the job is moved to 'error' — never left stuck at 'assembled'.

    UNDO: remove the try/except wrapping _run_deploy in upload_job -> the raise
    propagates, the job stays 'assembled' -> the 500 + status=='error' assertions
    go RED."""
    job_id = _run_to_drafted(client, monkeypatch, tmp_path)

    def _boom(cmd, env):
        raise OSError("could not spawn deploy subprocess")

    monkeypatch.setattr(onboard_mod, "_run_deploy", _boom)

    r = client.post(f"/api/v1/onboard/jobs/{job_id}/upload", json={"target": "local"})
    assert r.status_code == 500, r.text

    snap = client.get(f"/api/v1/onboard/jobs/{job_id}").json()
    assert snap["status"] == "error", f"job stuck at {snap['status']!r} after a deploy raise"


def test_upload_deploy_nonzero_returns_500(client, monkeypatch, tmp_path):
    """A deploy that returns a non-zero exit code 500s, marks the job 'error', and
    tails the deploy stderr into the response body.

    UNDO: n/a for the returncode branch (already present) — this locks the
    error-status + stderr-tail contract so a later refactor can't drop it."""
    job_id = _run_to_drafted(client, monkeypatch, tmp_path)

    def _fail(cmd, env):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom-deploy-stderr")

    monkeypatch.setattr(onboard_mod, "_run_deploy", _fail)

    r = client.post(f"/api/v1/onboard/jobs/{job_id}/upload", json={"target": "local"})
    assert r.status_code == 500, r.text
    assert "boom-deploy-stderr" in r.json()["detail"]["stderr"]

    snap = client.get(f"/api/v1/onboard/jobs/{job_id}").json()
    assert snap["status"] == "error"


# ---------------------------------------------------------------------------
# FIX 3 (coverage): negative request/lifecycle paths.
# ---------------------------------------------------------------------------
def test_create_job_empty_modes_is_422(client):
    r = client.post(
        "/api/v1/onboard/jobs",
        json={"slug": "london", "display_name": "London", "bbox": LONDON_BBOX, "modes": []},
    )
    assert r.status_code == 422, r.text


def test_create_job_unknown_mode_is_422(client):
    r = client.post(
        "/api/v1/onboard/jobs",
        json={
            "slug": "london",
            "display_name": "London",
            "bbox": LONDON_BBOX,
            "modes": ["not_a_real_mode"],
        },
    )
    assert r.status_code == 422, r.text


def test_create_job_missing_bbox_is_422(client):
    r = client.post(
        "/api/v1/onboard/jobs",
        json={"slug": "london", "display_name": "London", "modes": ["license_clean"]},
    )
    assert r.status_code == 422, r.text


def test_draft_beats_before_assembled_is_409(client, monkeypatch):
    """draft-beats on a job that exists but never reached 'assembled' (its flow
    errored) is 409 — nothing to draft.

    UNDO: drop the ``job.status != 'assembled'`` guard in draft_beats -> RED."""

    def _boom(*args, **kwargs):
        raise RuntimeError("assemble blew up")

    monkeypatch.setattr(onboard_mod, "assemble", _boom)

    r = _create_job(client)
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]

    snap = client.get(f"/api/v1/onboard/jobs/{job_id}").json()
    assert snap["status"] == "error", snap

    r2 = client.post(f"/api/v1/onboard/jobs/{job_id}/draft-beats", json={"confirm_cost": True})
    assert r2.status_code == 409, r2.text


def test_upload_before_beats_drafted_is_409(client):
    """upload on an assembled-but-not-drafted job is 409 — beats must be drafted
    first.

    UNDO: drop the ``art.beats is None`` guard in upload_job -> RED."""
    job_id, _ = _run_to_assembled(client)
    r = client.post(f"/api/v1/onboard/jobs/{job_id}/upload", json={"target": "local"})
    assert r.status_code == 409, r.text
