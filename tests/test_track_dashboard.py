"""The tracker dashboard.

Boxes move by ARITHMETIC only: a story's displayed state is derived from its
machine-verified step rows (a step is `completed` only because `track` re-ran
its test itself), so no agent's word — sprint leader included — can move a box
or a percentage. The page scopes to the NEWEST feature, so dead runs stop
diluting the picture. A sprint leader writes short, attributed NOTES beside
the facts: narrative and warnings, clearly commentary, structurally unable to
touch state.

`track.py` deliberately lives outside the product tree (`.claude/ledger/`), so
it is loaded here by path.
"""

from __future__ import annotations

import argparse
import importlib.util
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

_TRACK_PATH = (
    Path(__file__).resolve().parent.parent / ".claude" / "ledger" / "track.py"
)
_spec = importlib.util.spec_from_file_location("ondoway_track", _TRACK_PATH)
track = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(track)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@pytest.fixture()
def db(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(track.SCHEMA)
    yield conn
    conn.close()


def _feature(conn, slug, created):
    conn.execute(
        "INSERT INTO features (slug, title, for_whom, tier, created) VALUES (?,?,?,?,?)",
        (slug, f"title of {slug}", "someone", 1, created),
    )


def _story(conn, sid, feature, state="PM"):
    conn.execute(
        "INSERT INTO stories (id, feature, text, who, state, sent_back, created) "
        "VALUES (?,?,?,?,?,0,?)",
        (sid, feature, f"text of {sid}", "owner", state, _now()),
    )


def _issue(conn, iid, story, status):
    conn.execute(
        "INSERT INTO issues (id, story, name, status, test_command, files, depends_on, "
        "attempts, created) VALUES (?,?,?,?,?,'','',0,?)",
        (iid, story, f"name of {iid}", status, "true", _now()),
    )


class TestDerivedState:
    """The box is arithmetic over the step rows — never a hand-set label."""

    def test_the_ladder(self, db):
        _feature(db, "f", _now())
        _story(db, "S1", "f")
        assert track.derived_story_state(db, "S1", "PM") == "PM"  # no steps yet
        _issue(db, "I1", "S1", "pending")
        _issue(db, "I2", "S1", "pending")
        assert track.derived_story_state(db, "S1", "PM") == "Planner"
        db.execute("UPDATE issues SET status='in_progress' WHERE id='I1'")
        assert track.derived_story_state(db, "S1", "PM") == "Implementer"
        db.execute("UPDATE issues SET status='completed' WHERE id='I1'")
        assert track.derived_story_state(db, "S1", "PM") == "Implementer"  # half done
        db.execute("UPDATE issues SET status='completed' WHERE id='I2'")
        assert track.derived_story_state(db, "S1", "PM") == "Verifier"

    def test_done_is_the_owners_and_only_the_owners(self, db):
        _feature(db, "f", _now())
        _story(db, "S1", "f", state="Done")
        _issue(db, "I1", "S1", "completed")
        assert track.derived_story_state(db, "S1", "Done") == "Done"
        # A hand-set state that is NOT Done is ignored: arithmetic wins.
        _story(db, "S2", "f", state="Verifier")
        _issue(db, "I2", "S2", "pending")
        assert track.derived_story_state(db, "S2", "Verifier") == "Planner"

    def test_a_no_op_row_is_settled_not_outstanding(self, db):
        """A no-op is a written decision that a milestone deliberately does not
        happen — its history kept, its work retired. A settled row: a story
        whose rows are all completed-or-no-op sits at Verifier, the owner can
        call it Done, and the retired row neither drags the percentage down
        nor pads it up. Otherwise any story that ever re-planned a row away
        could never be Done — a box lying about finished work."""
        _feature(db, "f", _now())
        _story(db, "S1", "f")
        _issue(db, "I1", "S1", "completed")
        _issue(db, "I2", "S1", "no-op")
        assert track.derived_story_state(db, "S1", "PM") == "Verifier"
        args = argparse.Namespace(
            id="S1", state="Done", who="owner", why=None, sent_back=False,
            db=None, json=False,
        )
        track.cmd_story_state(db, args)
        row = db.execute("SELECT state FROM stories WHERE id='S1'").fetchone()
        assert row["state"] == "Done"
        health = track.health_of(db, feature="f")
        assert health["progress"] == 100
        assert health["issues_total"] == 1, "a retired row is not counted work"

    def test_state_of_reports_the_derived_state(self, db):
        _feature(db, "f", _now())
        _story(db, "S1", "f", state="PM")  # stale hand label
        _issue(db, "I1", "S1", "completed")
        payload = track.state_of(db)
        assert payload["stories"][0]["state"] == "Verifier"


class TestNewestFeatureScope:
    def test_dashboard_scopes_stories_and_progress_to_the_newest_feature(self, db):
        _feature(db, "old", "2026-01-01T00:00:00+00:00")
        _story(db, "OLD-S", "old")
        _issue(db, "OLD-I", "OLD-S", "pending")
        _feature(db, "new", "2026-09-01T00:00:00+00:00")
        _story(db, "NEW-S", "new")
        _issue(db, "NEW-I1", "NEW-S", "completed")
        _issue(db, "NEW-I2", "NEW-S", "completed")

        health = track.health_of(db, feature="new")
        assert health["progress"] == 100, "dead runs must not dilute the number"
        assert health["issues_total"] == 2

        html = track.render_active(db)
        assert "title of new" in html
        assert "NEW-S" in html
        assert "OLD-S" not in html, "a dead run's stories must not render"
        assert "<b>100%</b>" in html


class TestSprintNotes:
    def test_a_note_lands_attributed_and_renders_as_commentary(self, db):
        _feature(db, "f", _now())
        _story(db, "S1", "f")
        args = argparse.Namespace(
            story="S1", text="A step hit a missed caller; re-walking it.",
            who="sprint-leader", db=None, json=False,
        )
        track.cmd_note(db, args)
        row = db.execute(
            "SELECT kind, who, detail, story FROM events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row["kind"] == "sprint_note"
        assert row["who"] == "sprint-leader"
        assert "missed caller" in row["detail"]
        html = track.render_active(db)
        assert "missed caller" in html
        assert "sprint-leader" in html

    def test_a_storyless_event_renders_a_real_dash_not_escaped_entity_text(self, db):
        """The log's no-story placeholder is markup, not data: passing it
        through the escaper prints the literal text "&mdash;" on the page. The
        entity must survive escaping."""
        _feature(db, "f", _now())
        args = argparse.Namespace(
            story=None, text="a note with no story", who="sprint-leader",
            db=None, json=False,
        )
        track.cmd_note(db, args)
        html = track.render_active(db)
        assert "&amp;mdash;" not in html

    def test_a_note_moves_no_box_and_no_percentage(self, db):
        _feature(db, "f", _now())
        _story(db, "S1", "f")
        _issue(db, "I1", "S1", "pending")
        before_state = track.derived_story_state(db, "S1", "PM")
        before_health = track.health_of(db, feature="f")
        args = argparse.Namespace(
            story="S1", text="Everything is done, honestly!", who="a-liar",
            db=None, json=False,
        )
        track.cmd_note(db, args)
        assert track.derived_story_state(db, "S1", "PM") == before_state
        assert track.health_of(db, feature="f") == before_health
