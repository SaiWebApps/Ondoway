"""Deploy READ-CHAIN honors the hermetic env vars, BACKWARD-COMPATIBLE.

Covers the parts of the ``repo -> graph`` deploy path that a hermetic onboard
must connect to:
  - ``scripts.deploy._upload_areas_step`` SKIPS the transient :8000 areas server
    when the city has no areas (a fresh onboarded city writes ``areas.json == []``);
  - ``scripts.upload_areas._city_data_dir`` and ``scripts.db_parity._data_root``
    resolve the corpus root via the shared ``onboard_data_root()`` helper.

Every assertion checks BOTH: env set → the tmp/hermetic path; env unset → the
committed ``<repo>/data`` default (real paris/new_york deploys unchanged).

Pure — no Neo4j, no real deploy: the skip test patches the subprocess/health
seams so nothing is spawned or POSTed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import scripts.db_parity as db_parity
import scripts.deploy as deploy
import scripts.upload_areas as upload_areas

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Data-root resolution (upload_areas + db_parity).
# ---------------------------------------------------------------------------
def test_upload_areas_city_data_dir_defaults_when_unset(monkeypatch) -> None:
    """Unset → <repo>/data/{slug}. [undo: n/a; guards default preserved]"""
    monkeypatch.delenv("ONBOARD_DATA_ROOT", raising=False)
    assert upload_areas._city_data_dir("london") == REPO_ROOT / "data" / "london"


def test_upload_areas_city_data_dir_honors_env(monkeypatch, tmp_path) -> None:
    """ONBOARD_DATA_ROOT=tmp → {tmp}/{slug}. [undo: hardcode PROJECT/data → RED]"""
    monkeypatch.setenv("ONBOARD_DATA_ROOT", str(tmp_path))
    assert upload_areas._city_data_dir("london") == tmp_path / "london"


def test_db_parity_data_root_defaults_when_unset(monkeypatch) -> None:
    """Unset → <repo>/data. [undo: n/a; guards default preserved]"""
    monkeypatch.delenv("ONBOARD_DATA_ROOT", raising=False)
    assert db_parity._data_root() == REPO_ROOT / "data"


def test_db_parity_data_root_honors_env(monkeypatch, tmp_path) -> None:
    """ONBOARD_DATA_ROOT=tmp → tmp. [undo: hardcode ROOT/data → RED]"""
    monkeypatch.setenv("ONBOARD_DATA_ROOT", str(tmp_path))
    assert db_parity._data_root() == tmp_path


# ---------------------------------------------------------------------------
# deploy._areas_for — [] for empty/absent, the list when present.
# ---------------------------------------------------------------------------
def test_areas_for_empty_or_absent_returns_empty(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ONBOARD_DATA_ROOT", str(tmp_path))
    # absent file
    assert deploy._areas_for("london") == []
    # empty list (a fresh onboarded city)
    (tmp_path / "london").mkdir()
    (tmp_path / "london" / "areas.json").write_text("[]\n")
    assert deploy._areas_for("london") == []


def test_areas_for_present_returns_list(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ONBOARD_DATA_ROOT", str(tmp_path))
    (tmp_path / "paris").mkdir()
    areas = [{"name": "Le Marais", "area_type": "neighborhood", "city_name": "paris"}]
    (tmp_path / "paris" / "areas.json").write_text(json.dumps(areas))
    assert deploy._areas_for("paris") == areas


# ---------------------------------------------------------------------------
# deploy._upload_areas_step — SKIP-EMPTY-AREAS: no transient :8000 server spawned
# for a city with no areas; still spawns exactly one when there ARE areas.
# ---------------------------------------------------------------------------
class _FakeProc:
    """A stand-in for the transient uvicorn Popen handle (records nothing; just
    satisfies the try/finally lifecycle the step drives)."""

    def poll(self):
        return None

    def send_signal(self, sig):
        pass

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


def _patch_spawn_seams(monkeypatch) -> list:
    """Patch every seam the areas step would touch so it neither spawns a real
    server nor POSTs; return the list that records Popen spawn attempts."""
    spawns: list = []

    def _fake_popen(*args, **kwargs):
        spawns.append((args, kwargs))
        return _FakeProc()

    monkeypatch.setattr(deploy.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(deploy, "_api_healthy", lambda *a, **k: True)
    monkeypatch.setattr(deploy, "_run", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "_port_busy", lambda *a, **k: False)
    return spawns


def test_deploy_skips_area_server_when_no_areas(monkeypatch, tmp_path) -> None:
    """A city whose areas.json == [] must NOT spawn a transient areas-upload
    server — no :8000 uvicorn, so a hermetic deploy never hard-fails on a busy
    port for a city with nothing to upload.
    UNDO: drop the ``if not _areas_for(slug): return`` guard in _upload_areas_step
    → Popen fires → ``spawns`` is non-empty → RED."""
    (tmp_path / "london").mkdir()
    (tmp_path / "london" / "areas.json").write_text("[]\n")
    monkeypatch.setenv("ONBOARD_DATA_ROOT", str(tmp_path))
    spawns = _patch_spawn_seams(monkeypatch)

    deploy._upload_areas_step("london", sys.executable)

    assert spawns == [], "a city with no areas must NOT spawn a transient area-upload server"


def test_deploy_spawns_area_server_when_areas_present(monkeypatch, tmp_path) -> None:
    """The skip is CONDITIONAL: a city WITH areas still spawns exactly one
    transient API (so real cities with areas keep deploying as before)."""
    (tmp_path / "paris").mkdir()
    (tmp_path / "paris" / "areas.json").write_text(
        json.dumps([{"name": "Le Marais", "area_type": "neighborhood", "city_name": "paris"}])
    )
    monkeypatch.setenv("ONBOARD_DATA_ROOT", str(tmp_path))
    spawns = _patch_spawn_seams(monkeypatch)

    deploy._upload_areas_step("paris", sys.executable)

    assert len(spawns) == 1, "a city WITH areas must spawn exactly one transient API"


# ---------------------------------------------------------------------------
# deploy.deploy_api_port — default 8000, overridable via ONBOARD_DEPLOY_API_PORT.
# PUBLIC on purpose: scripts/upload_areas.py imports it, so the server the deploy
# starts and the client that POSTs to it read ONE value (see its docstring).
# ---------------------------------------------------------------------------
def test_deploy_api_port_default_and_override(monkeypatch) -> None:
    monkeypatch.delenv("ONBOARD_DEPLOY_API_PORT", raising=False)
    assert deploy.deploy_api_port() == 8000
    monkeypatch.setenv("ONBOARD_DEPLOY_API_PORT", "8123")
    assert deploy.deploy_api_port() == 8123
    monkeypatch.setenv("ONBOARD_DEPLOY_API_PORT", "")  # empty → default
    assert deploy.deploy_api_port() == 8000


def test_transient_api_env_opts_into_workbench_routes() -> None:
    """The areas upload talks to /nodes/Area, which app.py mounts fail-closed
    behind WORKBENCH_API_ENABLED — the deploy's own localhost uvicorn must opt
    in explicitly or the health probe 404s forever."""
    env = deploy._transient_api_env()
    assert env["WORKBENCH_API_ENABLED"] == "1"
    assert "localhost" in env["NO_PROXY"]
