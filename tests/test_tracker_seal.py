"""Approval seals a feature: criteria first, a stored hash, and no additions after.

The tracker refuses the moves that grew 16 approved milestones into 44 built:
adding an issue to an approved feature, and editing a sealed criterion. Both
need an owner-change row that only the owner's word creates. The seal is a hash
over the feature's stories, criteria and issues, recomputable at any time, so
tampering by direct SQL is visible even though it cannot be prevented.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TRACK_PATH = REPO / ".claude" / "ledger" / "track.py"


def _load_track():
    spec = importlib.util.spec_from_file_location("ondoway_track", TRACK_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


track = _load_track()


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    return tmp_path / "tracker.db"


def run(db: Path, *argv: str) -> tuple[int, dict]:
    """Run one track command against the temp db and parse its JSON output."""
    import contextlib
    import io

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        # Flags last: the subparser re-applies its own defaults, so a --db
        # before the subcommand is silently overwritten by the real path.
        code = track.main([*argv, "--db", str(db)])
    text = out.getvalue()
    return code, json.loads(text) if text.strip() else {}


def seed(db: Path) -> None:
    assert run(db, "init")[0] == 0
    assert run(db, "feature-add", "--slug", "f", "--title", "T",
               "--for-whom", "owner", "--tier", "1")[0] == 0
    assert run(db, "story-add", "--feature", "f", "--id", "S1",
               "--text", "the story", "--said-by", "owner")[0] == 0
    assert run(db, "issue-add", "--story", "S1", "--id", "S1.M1",
               "--name", "first", "--test-command", "true", "--files", "x")[0] == 0


def approve(db: Path) -> dict:
    code, payload = run(db, "approve", "--feature", "f", "--by", "owner")
    assert code == 0, payload
    return payload


def test_approve_refuses_a_story_with_no_criteria(db: Path) -> None:
    seed(db)
    code, payload = run(db, "approve", "--feature", "f", "--by", "owner")
    assert code == 1
    assert "criteri" in payload["refused"]


def test_approve_stores_a_recomputable_seal_hash(db: Path) -> None:
    seed(db)
    assert run(db, "criterion-add", "--story", "S1", "--id", "S1.C1",
               "--text", "the walker hears it", "--test-command",
               "uv run pytest tests/test_x.py -q")[0] == 0
    payload = approve(db)
    assert payload["seal"], "approval must return the seal hash"
    code, check = run(db, "seal-check", "--feature", "f")
    assert code == 0
    assert check["seal_intact"] is True


def test_direct_tampering_breaks_the_seal(db: Path) -> None:
    seed(db)
    assert run(db, "criterion-add", "--story", "S1", "--id", "S1.C1",
               "--text", "c", "--test-command", "true")[0] == 0
    approve(db)
    import sqlite3

    conn = sqlite3.connect(db)
    conn.execute("UPDATE criteria SET text='softened' WHERE id='S1.C1'")
    conn.commit()
    conn.close()
    code, check = run(db, "seal-check", "--feature", "f")
    assert code == 1
    assert check["seal_intact"] is False


def test_issue_add_after_approval_is_refused(db: Path) -> None:
    seed(db)
    assert run(db, "criterion-add", "--story", "S1", "--id", "S1.C1",
               "--text", "c", "--test-command", "true")[0] == 0
    approve(db)
    code, payload = run(db, "issue-add", "--story", "S1", "--id", "S1.M2",
                        "--name", "an added task", "--test-command", "true",
                        "--files", "x")
    assert code == 1
    assert "approved" in payload["refused"]


def test_issue_add_with_an_owner_change_passes_and_reseals(db: Path) -> None:
    seed(db)
    assert run(db, "criterion-add", "--story", "S1", "--id", "S1.C1",
               "--text", "c", "--test-command", "true")[0] == 0
    approve(db)
    code, change = run(db, "owner-change", "--feature", "f", "--by", "owner",
                       "--why", "the owner asked for one more milestone")
    assert code == 0
    token = change["owner_change"]
    assert run(db, "issue-add", "--story", "S1", "--id", "S1.M2",
               "--name", "the asked milestone", "--test-command", "true",
               "--files", "x", "--owner-change", token)[0] == 0
    code, _check = run(db, "seal-check", "--feature", "f")
    assert code == 0, "an owner change re-seals; the new shape is the approved shape"


def test_criterion_edit_after_approval_is_refused_without_owner_change(db: Path) -> None:
    seed(db)
    assert run(db, "criterion-add", "--story", "S1", "--id", "S1.C1",
               "--text", "c", "--test-command", "true")[0] == 0
    approve(db)
    code, payload = run(db, "criterion-add", "--story", "S1", "--id", "S1.C2",
                        "--text", "another", "--test-command", "true")
    assert code == 1
    assert "approved" in payload["refused"]


def test_owner_change_token_is_single_use(db: Path) -> None:
    seed(db)
    assert run(db, "criterion-add", "--story", "S1", "--id", "S1.C1",
               "--text", "c", "--test-command", "true")[0] == 0
    approve(db)
    _, change = run(db, "owner-change", "--feature", "f", "--by", "owner",
                    "--why", "one more")
    token = change["owner_change"]
    assert run(db, "issue-add", "--story", "S1", "--id", "S1.M2", "--name", "a",
               "--test-command", "true", "--files", "x",
               "--owner-change", token)[0] == 0
    code, _payload = run(db, "issue-add", "--story", "S1", "--id", "S1.M3", "--name", "b",
                         "--test-command", "true", "--files", "x",
                         "--owner-change", token)
    assert code == 1, "a spent token authorizes nothing further"


def _approve_with_criteria(db: Path, **flags: str) -> None:
    seed(db)
    assert run(db, "criterion-add", "--story", "S1", "--id", "S1.C1",
               "--text", "c", "--test-command", "true")[0] == 0
    extra: list[str] = []
    for key, value in flags.items():
        extra += [f"--{key.replace('_', '-')}", value]
    code, payload = run(db, "approve", "--feature", "f", "--by", "owner", *extra)
    assert code == 0, payload


def test_interrupts_are_budgeted_at_three(db: Path) -> None:
    """A story may cost the owner three questions. The fourth parks it: the
    refusal says so, the park is recorded, and the run moves on."""
    _approve_with_criteria(db)
    for n in range(3):
        code, payload = run(db, "interrupt", "--story", "S1",
                            "--question", f"question {n + 1}")
        assert code == 0, payload
        assert payload["interrupts_left"] == 2 - n
    code, payload = run(db, "interrupt", "--story", "S1", "--question", "one too many")
    assert code == 1
    assert "parked" in payload["refused"]
    code, health = run(db, "health")
    assert code == 0
    assert health["replan_required"] is True
    assert "parked" in health["reason"]


def test_the_clock_parks_a_story_at_twice_its_budget(db: Path) -> None:
    """The seal records the commit budget and the measured minutes-per-commit
    rate. A feature twice over its clock is flagged for the owner instead of
    grinding on."""
    _approve_with_criteria(db, budget_commits="2", rate_minutes="18")
    import sqlite3

    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE approvals SET approved_at = datetime('now', '-100 minutes')")
    conn.commit()
    conn.close()
    code, health = run(db, "health")
    assert code == 0
    assert health["replan_required"] is True
    assert "clock" in health["reason"]


def test_a_feature_inside_its_clock_is_not_flagged(db: Path) -> None:
    _approve_with_criteria(db, budget_commits="8", rate_minutes="18")
    code, health = run(db, "health")
    assert code == 0
    assert health["replan_required"] is False


def test_unapproved_features_keep_working_as_before(db: Path) -> None:
    seed(db)
    assert run(db, "issue-add", "--story", "S1", "--id", "S1.M2",
               "--name", "pre-approval growth is shaping, not spinning",
               "--test-command", "true", "--files", "x")[0] == 0
