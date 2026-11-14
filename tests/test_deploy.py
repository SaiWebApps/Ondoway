"""Deploy READ-CHAIN honors the hermetic env vars, BACKWARD-COMPATIBLE.

Covers the parts of the ``repo -> graph`` deploy path that a hermetic onboard
must connect to:
  - ``scripts.deploy._upload_areas_step`` SKIPS the areas upload when the city
    has no areas (a fresh onboarded city writes ``areas.json == []``);
  - ``scripts.upload_areas._city_data_dir`` and ``scripts.db_parity._data_root``
    resolve the corpus root via the shared ``onboard_data_root()`` helper.

Every assertion checks BOTH: env set → the tmp/hermetic path; env unset → the
committed ``<repo>/data`` default (real paris/new_york deploys unchanged).

Pure — no Neo4j, no real deploy: the step tests record the commands instead of
running them.
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
# deploy._upload_areas_step — no areas, no upload; areas present, one upload that
# carries the cloud opt-in through.
# ---------------------------------------------------------------------------
def _record_runs(monkeypatch) -> list:
    runs: list = []
    monkeypatch.setattr(deploy, "_run", lambda cmd: runs.append(cmd))
    return runs


def test_deploy_skips_areas_upload_when_no_areas(monkeypatch, tmp_path) -> None:
    (tmp_path / "london").mkdir()
    (tmp_path / "london" / "areas.json").write_text("[]\n")
    monkeypatch.setenv("ONBOARD_DATA_ROOT", str(tmp_path))
    runs = _record_runs(monkeypatch)

    deploy._upload_areas_step("london", sys.executable, allow_cloud=False)

    assert runs == []


def _paris_with_areas(monkeypatch, tmp_path) -> None:
    (tmp_path / "paris").mkdir()
    (tmp_path / "paris" / "areas.json").write_text(
        json.dumps([{"name": "Le Marais", "area_type": "neighborhood", "city_name": "paris"}])
    )
    monkeypatch.setenv("ONBOARD_DATA_ROOT", str(tmp_path))


def test_deploy_uploads_areas_when_present(monkeypatch, tmp_path) -> None:
    _paris_with_areas(monkeypatch, tmp_path)
    runs = _record_runs(monkeypatch)

    deploy._upload_areas_step("paris", sys.executable, allow_cloud=False)

    assert runs == [[sys.executable, "scripts/upload_areas.py", "--slug", "paris"]]


def test_deploy_passes_the_cloud_opt_in_to_the_areas_upload(monkeypatch, tmp_path) -> None:
    _paris_with_areas(monkeypatch, tmp_path)
    runs = _record_runs(monkeypatch)

    deploy._upload_areas_step("paris", sys.executable, allow_cloud=True)

    assert runs[0][-1] == "--allow-cloud"


def test_every_step_runs_with_unbuffered_output() -> None:
    assert deploy.child_env()["PYTHONUNBUFFERED"] == "1"
