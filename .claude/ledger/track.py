#!/usr/bin/env python3
"""The shared tracker. One source of truth every agent reads and writes.

THE PROBLEM THIS EXISTS FOR. Every record of what an agent did was written by
that same agent, in prose, into a file it could reformat at will: the `scribe`
in `.claude/team-engine.js` spawns a haiku agent and asks it to edit
`state.json`. Nothing checked whether the status it wrote was true, and there
was no place the owner could look that an agent had not authored.

WHAT MAKES THIS DIFFERENT FROM A PRETTIER state.json. `step-status --status
completed` does not accept the claim. It runs the issue's own test command
itself, reads the exit code with its own eyes, and refuses the row unless that
code is 0. An agent's word never becomes a row.

WHAT CANNOT BE ENFORCED, STATED PLAINLY. `.claude/team-engine.js:65-66` records
a measured fact: PreToolUse hooks do NOT fire inside the Workflow runtime. No
hook can stop a workflow-spawned agent writing to this file with sqlite3
directly. Three layers, strongest first: (1) this command re-derives instead of
trusting, which is what makes lying useless; (2) a main-session hook blocks
direct writes, which covers the interactive session only; (3) prompts ask agents
to use it, which is persuasion. Layer 3 refuses to pay attention; it never
refuses to run.

WHY THE STORY IS THE UNIT. Owner ruling, 2026-09-01: "Organize in state machines
and divide by feature / story, to clarify the connection to bigger picture." A
feature holds stories, a story holds criteria and issues, and the dashboard is
organised by story at every level. A step id tells the owner nothing about what
is being built or why.

Stdlib only, on purpose: this is agent tooling under `.claude/`, and it must
never appear in the product's dependency graph or in `make test`.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent / "tracker.db"

#: The states a story moves through, in the owner's own role names. The
#: dashboard draws one machine per story out of exactly this list, so adding a
#: state here changes what the owner sees — do not add one casually.
STORY_STATES = ("PM", "Planner", "QA", "Implementer", "Verifier", "Done")

#: The only status that ASSERTS something was proved, and therefore the only one
#: that has to be earned by running the command. Marking a step in_progress or
#: blocked is bookkeeping; re-running a suite for it would be pure waste.
PROVEN = "completed"

#: The statuses that leave a row SETTLED: proved, or retired by a written replan
#: decision (`no-op` — the milestone deliberately does not happen; its history
#: stays). A settled row is not outstanding work, so it neither blocks a story's
#: Done nor counts in the progress arithmetic's denominator.
SETTLED = (PROVEN, "no-op")

#: How long a single step's command may run before this gives up on it. A step
#: is meant to be atomic — one command, seconds — so a command still going after
#: this is a step that was never atomic, and saying so is more useful than
#: waiting.
COMMAND_TIMEOUT_S = 900

SCHEMA = """
CREATE TABLE IF NOT EXISTS features (
    slug      TEXT PRIMARY KEY,
    title     TEXT NOT NULL,
    for_whom  TEXT NOT NULL,
    tier      INTEGER NOT NULL,
    created   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stories (
    id        TEXT PRIMARY KEY,
    feature   TEXT NOT NULL REFERENCES features(slug),
    text      TEXT NOT NULL,
    who       TEXT NOT NULL,
    state     TEXT NOT NULL DEFAULT 'PM',
    sent_back INTEGER NOT NULL DEFAULT 0,
    created   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS criteria (
    id           TEXT PRIMARY KEY,
    story        TEXT NOT NULL REFERENCES stories(id),
    text         TEXT NOT NULL,
    negative     INTEGER NOT NULL DEFAULT 0,
    test_command TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS issues (
    id           TEXT PRIMARY KEY,
    story        TEXT NOT NULL REFERENCES stories(id),
    name         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    test_command TEXT NOT NULL,
    files        TEXT NOT NULL,
    depends_on   TEXT NOT NULL DEFAULT '',
    attempts     INTEGER NOT NULL DEFAULT 0,
    created      TEXT NOT NULL
);

-- Append-only. This is what draws the state machine on the dashboard, and the
-- only thing `health` reads. The triggers below are the append-only part: an
-- agent with a sqlite3 shell can INSERT here, which is the point, but it cannot
-- go back and change what it already said.
CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT NOT NULL,
    kind    TEXT NOT NULL,
    story   TEXT,
    issue   TEXT,
    who     TEXT NOT NULL,
    detail  TEXT NOT NULL DEFAULT ''
);

CREATE TRIGGER IF NOT EXISTS events_are_append_only_update
BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'events are append-only: the log of what happened is not editable by what happened');
END;

CREATE TRIGGER IF NOT EXISTS events_are_append_only_delete
BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'events are append-only: the log of what happened is not editable by what happened');
END;

-- What THIS command observed running THAT command. Never what an agent said it
-- observed. `health` reads green->red transitions out of here.
CREATE TABLE IF NOT EXISTS test_runs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        TEXT NOT NULL,
    issue     TEXT,
    command   TEXT NOT NULL,
    exit_code INTEGER NOT NULL,
    excerpt   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS approvals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    feature     TEXT NOT NULL REFERENCES features(slug),
    approved_by TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    seal        TEXT NOT NULL DEFAULT '',
    budget_commits INTEGER NOT NULL DEFAULT 0,
    rate_minutes   INTEGER NOT NULL DEFAULT 0
);
"""

#: Columns added after tables already existed in real databases. CREATE TABLE
#: IF NOT EXISTS never alters, so each is applied by `_migrate` when absent.
MIGRATIONS = (
    ("criteria", "test_command", "TEXT NOT NULL DEFAULT ''"),
    ("approvals", "seal", "TEXT NOT NULL DEFAULT ''"),
    ("approvals", "budget_commits", "INTEGER NOT NULL DEFAULT 0"),
    ("approvals", "rate_minutes", "INTEGER NOT NULL DEFAULT 0"),
)

#: Questions a story may cost the owner between approval and Done. The next
#: one parks the story instead of asking.
INTERRUPT_BUDGET = 3


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, spec in MIGRATIONS:
        present = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if present and column not in present:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {spec}")


class Refused(Exception):
    """A claim this command declines to record. Carries what to say about it,
    and optionally structured fields for the caller (`extra`)."""

    def __init__(self, message: str, extra: dict | None = None):
        super().__init__(message)
        self.extra = extra or {}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # WAL so `track serve` can read the whole time agents are writing. Without
    # it the dashboard blocks writers, and a dashboard that slows the run is a
    # dashboard nobody leaves open.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def record(conn: sqlite3.Connection, kind: str, *, who: str,
           story: str | None = None, issue: str | None = None,
           detail: str = "") -> None:
    conn.execute(
        "INSERT INTO events (at, kind, story, issue, who, detail) VALUES (?,?,?,?,?,?)",
        (now(), kind, story, issue, who, detail),
    )


# --------------------------------------------------------------- reading out


def derived_story_state(conn: sqlite3.Connection, story_id: str, persisted: str) -> str:
    """The box is ARITHMETIC over the step rows — never a hand-set label
    (owner ruling, option B, 2026-09-03). The rows themselves are already
    machine-verified: a step is `completed` only because this command re-ran
    its test and saw the exit code, so no agent's word is anywhere in the
    chain from a passing test to a moved box. The one exception is "Done",
    which only the owner means — it is kept from the persisted state, and
    `cmd_story_state` already refuses it while any step is unproved."""
    if persisted == "Done":
        return "Done"
    statuses = [r["status"] for r in conn.execute(
        "SELECT status FROM issues WHERE story=?", (story_id,))]
    if not statuses:
        return "PM"
    if all(s in SETTLED for s in statuses):
        return "Verifier"
    if all(s == "pending" for s in statuses):
        return "Planner"
    return "Implementer"


def state_of(conn: sqlite3.Connection) -> dict:
    """The full current picture. Printed by EVERY write, so no caller ever needs
    to keep its own copy — the engine's in-memory mirror went stale against the
    file and stranded 8 of 10 steps on a real run. Story states are DERIVED
    (see `derived_story_state`), so every reader of this payload — the JSON api
    and the page alike — sees the arithmetic, never a stale hand label."""
    features = [dict(r) for r in conn.execute(
        "SELECT slug, title, for_whom, tier, created FROM features ORDER BY created")]
    stories = [dict(r) for r in conn.execute(
        "SELECT id, feature, text, who, state, sent_back FROM stories ORDER BY id")]
    for story in stories:
        story["state"] = derived_story_state(conn, story["id"], story["state"])
    issues = [dict(r) for r in conn.execute(
        "SELECT id, story, name, status, test_command, files, depends_on, attempts "
        "FROM issues ORDER BY id")]
    approved = conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] > 0
    return {"features": features, "stories": stories,
            "issues": issues, "approved": approved}


def emit(conn: sqlite3.Connection, extra: dict | None = None) -> None:
    payload = state_of(conn)
    if extra:
        payload.update(extra)
    print(json.dumps(payload, indent=2))


# ----------------------------------------------------------------- the seal
#
# Approval freezes a feature's shape. 16 approved milestones became 44 built
# because nothing refused the 28 additions; now the tracker itself does. The
# seal is a hash over the feature's stories, criteria and issues. It cannot
# stop a direct sqlite3 write (nothing here can), but `seal-check` recomputes
# it, so tampering is visible the next time anything is claimed.


def feature_shape(conn: sqlite3.Connection, feature: str) -> str:
    """The approved shape: every story, criterion and issue id, text and proof
    command under the feature, hashed canonically."""
    import hashlib

    shape: dict = {"stories": [], "criteria": [], "issues": []}
    for row in conn.execute(
        "SELECT id, text FROM stories WHERE feature=? ORDER BY id", (feature,)
    ):
        shape["stories"].append([row["id"], row["text"]])
    for row in conn.execute(
        "SELECT c.id, c.text, c.test_command FROM criteria c "
        "JOIN stories s ON c.story = s.id WHERE s.feature=? ORDER BY c.id",
        (feature,),
    ):
        shape["criteria"].append([row["id"], row["text"], row["test_command"]])
    for row in conn.execute(
        "SELECT i.id, i.name, i.test_command FROM issues i "
        "JOIN stories s ON i.story = s.id WHERE s.feature=? ORDER BY i.id",
        (feature,),
    ):
        shape["issues"].append([row["id"], row["name"], row["test_command"]])
    payload = json.dumps(shape, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def approval_of(conn: sqlite3.Connection, feature: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, seal, approved_by FROM approvals WHERE feature=? ORDER BY id DESC",
        (feature,),
    ).fetchone()


def feature_of_story(conn: sqlite3.Connection, story_id: str) -> str | None:
    row = conn.execute("SELECT feature FROM stories WHERE id=?", (story_id,)).fetchone()
    return row["feature"] if row else None


def consume_owner_change(conn: sqlite3.Connection, feature: str, token: str | None,
                         *, who: str, action: str) -> None:
    """Refuse a post-approval shape change unless `token` names an unspent
    owner-change row for this feature; spend it and re-seal afterwards is the
    caller's job via `reseal`."""
    if token is None:
        raise Refused(
            f"feature {feature!r} is approved and sealed. {action} changes the approved "
            "shape, and only the owner changes an approved shape: record one with "
            "`owner-change --feature ... --by owner --why ...` and pass its id "
            "as --owner-change."
        )
    row = conn.execute(
        "SELECT id, detail FROM events WHERE id=? AND kind='owner_change'", (token,)
    ).fetchone()
    if not row or not row["detail"].startswith(f"{feature}:"):
        raise Refused(f"--owner-change {token!r} names no owner change for {feature!r}")
    spent = conn.execute(
        "SELECT 1 FROM events WHERE kind='owner_change_used' AND detail=?", (token,)
    ).fetchone()
    if spent:
        raise Refused(
            f"owner change {token} is already spent. One change, one token: "
            "ask the owner again."
        )
    record(conn, "owner_change_used", who=who, detail=token)


def reseal(conn: sqlite3.Connection, feature: str, *, who: str) -> None:
    approval = approval_of(conn, feature)
    if approval is None:
        return
    seal = feature_shape(conn, feature)
    conn.execute("UPDATE approvals SET seal=? WHERE id=?", (seal, approval["id"]))
    record(conn, "resealed", who=who, detail=f"{feature}: {seal[:16]}")


# ------------------------------------------------ the part that cannot be faked


def observe(conn: sqlite3.Connection, command: str, issue: str | None) -> int:
    """Run the command and record the exit code THIS process saw.

    The whole design is one line long: the row is written from `result.returncode`,
    not from anything a caller passed in. There is deliberately no flag to supply
    an exit code, because a flag to supply one is a flag to lie with.
    """
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            cwd=str(Path(__file__).resolve().parents[2]),
            timeout=COMMAND_TIMEOUT_S, check=False,
        )
        code = result.returncode
        excerpt = (result.stdout + result.stderr)[-2000:]
    except subprocess.TimeoutExpired:
        code = 124
        excerpt = f"timed out after {COMMAND_TIMEOUT_S}s — the step was not atomic"

    conn.execute(
        "INSERT INTO test_runs (at, issue, command, exit_code, excerpt) VALUES (?,?,?,?,?)",
        (now(), issue, command, code, excerpt),
    )
    return code


# ----------------------------------------------------------------- subcommands


def cmd_init(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    conn.executescript(SCHEMA)
    emit(conn, {"initialised": True})


def cmd_feature_add(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    conn.execute(
        "INSERT INTO features (slug, title, for_whom, tier, created) VALUES (?,?,?,?,?)",
        (args.slug, args.title, args.for_whom, args.tier, now()),
    )
    record(conn, "feature_added", who=args.who, detail=args.title)
    emit(conn)


def cmd_story_add(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    if not conn.execute("SELECT 1 FROM features WHERE slug=?", (args.feature,)).fetchone():
        raise Refused(f"no feature {args.feature!r}; add the feature before its stories")
    conn.execute(
        "INSERT INTO stories (id, feature, text, who, state, sent_back, created) "
        "VALUES (?,?,?,?,'PM',0,?)",
        (args.id, args.feature, args.text, args.who_said, now()),
    )
    record(conn, "story_added", who=args.who, story=args.id, detail=args.text)
    emit(conn)


def cmd_issue_add(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    # Refused rather than orphaned: the dashboard is organised by story, so a
    # step with no story is a step the owner cannot see.
    if not conn.execute("SELECT 1 FROM stories WHERE id=?", (args.story,)).fetchone():
        raise Refused(
            f"no story {args.story!r}. Every issue belongs to a story — the owner ruled "
            "the story is the unit at every level, and an issue without one is invisible "
            "on the dashboard."
        )
    feature = feature_of_story(conn, args.story)
    if feature and approval_of(conn, feature) is not None:
        consume_owner_change(conn, feature, args.owner_change, who=args.who,
                             action=f"adding issue {args.id!r}")
    conn.execute(
        "INSERT INTO issues (id, story, name, status, test_command, files, depends_on, "
        "attempts, created) VALUES (?,?,?,'pending',?,?,?,0,?)",
        (args.id, args.story, args.name, args.test_command,
         ",".join(args.files), ",".join(args.depends_on or []), now()),
    )
    record(conn, "issue_added", who=args.who, story=args.story, issue=args.id,
           detail=args.name)
    if feature:
        reseal(conn, feature, who=args.who)
    emit(conn)


def cmd_criterion_add(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    """A criterion is the owner's acceptance line as a runnable proof. Sealed
    with the feature at approval; adding one afterwards is an owner change."""
    if not conn.execute("SELECT 1 FROM stories WHERE id=?", (args.story,)).fetchone():
        raise Refused(f"no story {args.story!r}; add the story before its criteria")
    feature = feature_of_story(conn, args.story)
    if feature and approval_of(conn, feature) is not None:
        consume_owner_change(conn, feature, args.owner_change, who=args.who,
                             action=f"adding criterion {args.id!r}")
    conn.execute(
        "INSERT INTO criteria (id, story, text, negative, test_command) VALUES (?,?,?,?,?)",
        (args.id, args.story, args.text, 1 if args.negative else 0, args.test_command),
    )
    record(conn, "criterion_added", who=args.who, story=args.story, detail=args.text)
    if feature:
        reseal(conn, feature, who=args.who)
    emit(conn)


def cmd_owner_change(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    """The owner's recorded word that the approved shape may change once."""
    if approval_of(conn, args.feature) is None:
        raise Refused(
            f"feature {args.feature!r} has no approval to change; before approval "
            "the shape is simply edited"
        )
    record(conn, "owner_change", who=args.by, detail=f"{args.feature}: {args.why}")
    token = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    print(json.dumps({"owner_change": str(token), "why": args.why}))


def cmd_seal_check(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    """Recompute the approved shape and compare with the stored seal."""
    approval = approval_of(conn, args.feature)
    if approval is None:
        raise Refused(f"feature {args.feature!r} is not approved, so it has no seal")
    intact = feature_shape(conn, args.feature) == approval["seal"]
    if not intact:
        raise Refused(
            f"the shape of {args.feature!r} no longer matches its seal: something "
            "changed the stories, criteria or issues without an owner change",
            extra={"seal_intact": False},
        )
    print(json.dumps({"seal_intact": True, "seal": approval["seal"]}))


def cmd_issue_set(conn: sqlite3.Namespace, args: argparse.Namespace) -> None:
    """Change an issue's command. Exists so a replan can re-point a step without
    deleting its history — the events and test_runs behind it stay."""
    if not conn.execute("SELECT 1 FROM issues WHERE id=?", (args.id,)).fetchone():
        raise Refused(f"no issue {args.id!r}")
    conn.execute("UPDATE issues SET test_command=? WHERE id=?", (args.test_command, args.id))
    record(conn, "issue_retargeted", who=args.who, issue=args.id, detail=args.test_command)
    emit(conn)


def plan_for(conn: sqlite3.Connection, story_id: str) -> Path | None:
    """The run's plan for this story's feature — `.claude/runs/<date>-<slug>/plan.md`."""
    row = conn.execute("SELECT feature FROM stories WHERE id=?", (story_id,)).fetchone()
    if not row:
        return None
    root = Path(__file__).resolve().parents[2]
    plans = sorted((root / ".claude" / "runs").glob(f"*-{row['feature']}/plan.md"))
    return plans[-1] if plans else None


def cmd_step_status(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    row = conn.execute(
        "SELECT id, story, status, test_command, attempts FROM issues WHERE id=?",
        (args.id,)).fetchone()
    if not row:
        raise Refused(f"no issue {args.id!r}")

    # THE REFUSAL. Only a pass claim has to be earned, and it is earned by this
    # process running the command, not by the caller saying it did.
    if args.status == PROVEN:
        # THE PLAN IS RE-CHECKED AT EVERY MILESTONE, not once before the build.
        # A plan is a set of claims about code that the build is busy moving, so
        # its citations rot as the run proceeds: a milestone shifts a file, and
        # every later milestone's plan silently describes lines that have moved
        # out from under it. Checked once, that drift is invisible until someone
        # builds against a stale line. Checked here, the milestone that caused
        # the drift is the one that cannot close until the plan is re-read.
        plan = plan_for(conn, row["story"])
        if plan is not None:
            gate = subprocess.run(
                [sys.executable, str(Path(__file__).resolve().parent / "plan_check.py"),
                 str(plan)],
                capture_output=True, text=True, check=False,
            )
            if gate.returncode != 0:
                record(conn, "claim_refused", who=args.who, story=row["story"], issue=args.id,
                       detail=f"{plan.name} no longer resolves against the code")
                conn.commit()
                raise Refused(
                    f"{plan} does not pass its own citation gate, so nothing can be claimed "
                    f"against it:\n{gate.stdout.strip()[-1500:]}\n\n"
                    "A citation that stopped resolving is a plan that has drifted from the "
                    "code. Re-read what moved, re-point the plan, then claim the step."
                )
        code = observe(conn, row["test_command"], args.id)
        if code != 0:
            conn.execute("UPDATE issues SET attempts=attempts+1 WHERE id=?", (args.id,))
            record(conn, "claim_refused", who=args.who, story=row["story"], issue=args.id,
                   detail=f"`{row['test_command']}` exited {code}")
            conn.commit()
            raise Refused(
                f"`{row['test_command']}` exited {code}. This command ran it and saw that "
                f"itself, so issue {args.id} stays {row['status']!r}. Fix the code, not the "
                "status."
            )

    conn.execute("UPDATE issues SET status=? WHERE id=?", (args.status, args.id))
    record(conn, "status_changed", who=args.who, story=row["story"], issue=args.id,
           detail=f"{row['status']} -> {args.status}")
    emit(conn)


def cmd_story_state(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    row = conn.execute("SELECT id, state, sent_back FROM stories WHERE id=?",
                       (args.id,)).fetchone()
    if not row:
        raise Refused(f"no story {args.id!r}")
    if args.state not in STORY_STATES:
        raise Refused(f"{args.state!r} is not a story state; one of {list(STORY_STATES)}")

    # A story is Done only when every issue under it is settled — proved, or
    # retired by a written no-op. Otherwise "Done" would be exactly the
    # agent's-word claim this whole command exists to refuse.
    if args.state == "Done":
        outstanding = conn.execute(
            "SELECT id FROM issues WHERE story=? AND status NOT IN (?, ?)",
            (args.id, *SETTLED)).fetchall()
        if outstanding:
            raise Refused(
                f"story {args.id} has {len(outstanding)} issue(s) not settled "
                f"({', '.join(r['id'] for r in outstanding)}). A story is finished when its "
                "work is, not when someone says so."
            )

    if args.sent_back:
        conn.execute("UPDATE stories SET sent_back=sent_back+1 WHERE id=?", (args.id,))
    conn.execute("UPDATE stories SET state=? WHERE id=?", (args.state, args.id))
    record(conn, "sent_back" if args.sent_back else "story_moved", who=args.who,
           story=args.id, detail=f"{row['state']} -> {args.state}"
           + (f": {args.why}" if args.why else ""))
    emit(conn)


def cmd_note(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    """A sprint note: attributed commentary beside the facts (option B, owner
    ruling 2026-09-03). It lands in the append-only event log and renders in
    the page's notes strip. By construction it can move nothing: the boxes are
    arithmetic over step rows and the percentage over issue counts, and this
    writes to neither table."""
    if args.story and not conn.execute(
        "SELECT 1 FROM stories WHERE id=?", (args.story,)
    ).fetchone():
        raise Refused(f"no story {args.story!r} to note on")
    record(conn, "sprint_note", who=args.who, story=args.story or None,
           detail=args.text)
    print(json.dumps({"noted": args.text, "who": args.who}))


def cmd_approve(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    if not conn.execute("SELECT 1 FROM features WHERE slug=?", (args.feature,)).fetchone():
        raise Refused(f"no feature {args.feature!r}")
    # Approval without criteria is a signature on a blank page: the owner would
    # be sealing a shape with no runnable definition of done.
    bare = [
        row["id"]
        for row in conn.execute(
            "SELECT id FROM stories WHERE feature=? AND id NOT IN "
            "(SELECT story FROM criteria)",
            (args.feature,),
        )
    ]
    if bare:
        raise Refused(
            f"stories without criteria: {', '.join(bare)}. Every story carries at "
            "least one criterion with a test command before the owner approves it."
        )
    seal = feature_shape(conn, args.feature)
    conn.execute(
        "INSERT INTO approvals (feature, approved_by, approved_at, seal, "
        "budget_commits, rate_minutes) VALUES (?,?,?,?,?,?)",
        (args.feature, args.by, now(), seal, args.budget_commits, args.rate_minutes),
    )
    record(conn, "approved", who=args.by, detail=f"{args.feature}: {seal[:16]}")
    emit(conn, {"seal": seal})


def cmd_interrupt(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    """A question to the owner, counted. The budget is three per story; the
    fourth parks the story so the run moves on instead of queueing on a human."""
    if not conn.execute("SELECT 1 FROM stories WHERE id=?", (args.story,)).fetchone():
        raise Refused(f"no story {args.story!r} to interrupt on")
    spent = conn.execute(
        "SELECT COUNT(*) FROM events WHERE kind='interrupt' AND story=?", (args.story,)
    ).fetchone()[0]
    if spent >= INTERRUPT_BUDGET:
        record(conn, "story_parked", who=args.who, story=args.story,
               detail=f"interrupt budget spent; last question: {args.question}")
        raise Refused(
            f"story {args.story} has spent its {INTERRUPT_BUDGET} interrupts and is "
            "parked. Write the one-paragraph state for the owner and move to the "
            "next approved story."
        )
    record(conn, "interrupt", who=args.who, story=args.story, detail=args.question)
    print(json.dumps({
        "story": args.story,
        "question": args.question,
        "interrupts_left": INTERRUPT_BUDGET - spent - 1,
    }))


def cmd_show(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    emit(conn)


def health_of(conn: sqlite3.Connection, feature: str | None = None) -> dict:
    """Progress and the replan decision, computed from the log. Never reported.

    All three triggers are arithmetic. The REPLAN is an agent; the DECISION to
    replan never is — that separation is the whole point of the manager. Shared
    with the dashboard so the page and the engine can never disagree about whether
    a run is in trouble.

    ``feature`` scopes everything to one feature's stories. The page passes the
    NEWEST feature, so a dead run's unproved steps stop diluting the number —
    the owner read "50% proved" over a feature that was 4-for-4 (2026-09-03).
    None keeps the global view for the JSON api.
    """
    scope_sql = (
        " AND story IN (SELECT id FROM stories WHERE feature=?)" if feature else ""
    )
    scope_args: tuple = (feature,) if feature else ()
    # A no-op row is retired work, not work: counting it in the denominator
    # would report a feature under 100% forever after any replan retired a row.
    total = conn.execute(
        "SELECT COUNT(*) FROM issues WHERE status != 'no-op'" + scope_sql, scope_args
    ).fetchone()[0]
    done = conn.execute(
        "SELECT COUNT(*) FROM issues WHERE status=?" + scope_sql,
        (PROVEN, *scope_args)).fetchone()[0]
    progress = round(100 * done / total) if total else 0

    reason = None
    story = None
    story_scope_sql = " AND feature=?" if feature else ""

    # 1. A story bounced back twice without moving.
    row = conn.execute(
        "SELECT id, sent_back FROM stories WHERE sent_back >= 2" + story_scope_sql +
        " ORDER BY sent_back DESC", scope_args
    ).fetchone()
    if row:
        reason = f"it was sent back {row['sent_back']} times without moving"
        story = row["id"]

    # 2. Something already proved has broken: an issue whose runs went 0 then non-0.
    if not reason:
        for issue in conn.execute(
            "SELECT id, story FROM issues WHERE 1=1" + scope_sql, scope_args
        ):
            codes = [r["exit_code"] for r in conn.execute(
                "SELECT exit_code FROM test_runs WHERE issue=? ORDER BY id", (issue["id"],))]
            if any(codes[i] == 0 and codes[i + 1] != 0 for i in range(len(codes) - 1)):
                reason = f"issue {issue['id']} went green then red"
                story = issue["story"]
                break

    # 3. Attempts piling up on one issue with nothing changing.
    if not reason:
        row = conn.execute(
            "SELECT id, story, attempts FROM issues WHERE attempts >= 3" + scope_sql +
            " ORDER BY attempts DESC", scope_args).fetchone()
        if row:
            reason = f"issue {row['id']} has {row['attempts']} attempts and no state change"
            story = row["story"]

    # 4. A story parked on a spent interrupt budget, still not Done.
    if not reason:
        row = conn.execute(
            "SELECT e.story FROM events e JOIN stories s ON e.story = s.id "
            "WHERE e.kind='story_parked' AND s.state != 'Done'" +
            (" AND s.feature=?" if feature else "") + " ORDER BY e.id DESC",
            scope_args).fetchone()
        if row:
            reason = f"story {row['story']} is parked and waits on the owner"
            story = row["story"]

    # 5. A feature twice over its clock. The seal recorded the commit budget and
    # the measured minutes-per-commit rate; past 2x, grinding on is the failure.
    if not reason:
        for approval in conn.execute(
            "SELECT feature, approved_at, budget_commits, rate_minutes FROM approvals "
            "WHERE budget_commits > 0 AND rate_minutes > 0"
            + (" AND feature=?" if feature else "") + " ORDER BY id DESC",
            scope_args,
        ):
            unfinished = conn.execute(
                "SELECT 1 FROM stories WHERE feature=? AND state != 'Done'",
                (approval["feature"],)).fetchone()
            if not unfinished:
                continue
            approved_at = datetime.fromisoformat(approval["approved_at"])
            if approved_at.tzinfo is None:
                approved_at = approved_at.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - approved_at).total_seconds() / 60
            budget = approval["budget_commits"] * approval["rate_minutes"]
            if elapsed > 2 * budget:
                reason = (
                    f"feature {approval['feature']} is over twice its clock "
                    f"({round(elapsed)}m spent, {budget}m budgeted) — park it "
                    "for the owner"
                )
                break

    return {
        "progress": progress,
        "issues_total": total,
        "issues_completed": done,
        "replan_required": reason is not None,
        "reason": reason,
        "story": story,
    }


def cmd_health(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    print(json.dumps(health_of(conn), indent=2))



# ------------------------------------------------------------------ the view


#: Ports that already belong to something. 8000 is the tour editor's API
#: (scripts/workbench.sh), 8080 the Neo4j graph dashboard (src/server.py), 7687 and
#: 7688 the dev and test databases. Binding one takes a running service down, and
#: the failure would look like the dashboard working.
TAKEN_PORTS = {8000: "the tour editor's API", 8080: "the graph dashboard",
               8001: "the workbench sidecar", 7687: "the dev database",
               7688: "the test database"}
DEFAULT_PORT = 8010

#: The six states a story machine draws, in the owner's role names. Kept beside
#: STORY_STATES rather than derived from it so the page cannot silently gain a
#: state nobody designed a box for.
MACHINE = STORY_STATES


def read_only(path: Path) -> sqlite3.Connection:
    """Belt and braces. The URI mode makes a write impossible at the driver, so a
    bug in a handler cannot turn the dashboard into another surface an agent could
    use to look finished."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def esc(text: object) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def active_payload(conn: sqlite3.Connection) -> dict:
    """The page's picture: everything scoped to the NEWEST feature — the one
    being built — so dead runs neither clutter the stories nor dilute the
    percentage. Sprint notes ride beside the facts, attributed."""
    payload = state_of(conn)
    active = payload["features"][-1] if payload["features"] else None
    payload["active_feature"] = active
    if active is not None:
        payload["stories"] = [
            s for s in payload["stories"] if s["feature"] == active["slug"]
        ]
        story_ids = {s["id"] for s in payload["stories"]}
        payload["issues"] = [i for i in payload["issues"] if i["story"] in story_ids]
    payload["events"] = [dict(r) for r in conn.execute(
        "SELECT id, at, kind, story, issue, who, detail FROM events "
        "ORDER BY id DESC LIMIT 50")]
    payload["notes"] = [dict(r) for r in conn.execute(
        "SELECT at, who, story, detail FROM events WHERE kind='sprint_note' "
        "ORDER BY id DESC LIMIT 5")]
    payload["health"] = health_of(
        conn, feature=active["slug"] if active is not None else None
    )
    return payload


def render_active(conn: sqlite3.Connection) -> str:
    return render(active_payload(conn))


def dashboard_state(path: Path) -> dict:
    conn = read_only(path)
    try:
        return active_payload(conn)
    finally:
        conn.close()


def machine_svg(story: dict) -> str:
    """One story, one machine. The stuck one is what the owner is looking for, so
    it is the one that must be unmistakable at a glance."""
    here = story["state"]
    reached = MACHINE.index(here) if here in MACHINE else 0
    stuck = story["sent_back"] >= 2
    boxes, edges = [], []
    for i, state in enumerate(MACHINE):
        x = 6 + i * 62
        done = i < reached
        cls = "done" if done else ("bad" if (i == reached and stuck)
                                   else ("here" if i == reached else ""))
        boxes.append(
            f'<rect class="m-node {cls}" x="{x}" y="42" width="46" height="36" rx="7"/>'
            f'<text class="m-lab" x="{x + 23}" y="59" text-anchor="middle">{esc(state)}</text>'
            + (f'<text class="m-tick" x="{x + 23}" y="72" text-anchor="middle">&#10003;</text>'
               if done else ""))
        if i:
            edges.append(f'<path class="m-edge {"on" if done else ""}" '
                         f'd="M{x - 10},60 H{x - 4}" marker-end="url(#a1)"/>')
    back = ""
    if stuck:
        back = ('<path class="m-edge back" marker-end="url(#a1)" '
                'd="M277,42 V26 Q277,16 267,16 H225 Q215,16 215,26 V38"/>'
                f'<text class="m-back-txt" x="246" y="11" text-anchor="middle">'
                f'sent back {esc(story["sent_back"])} times</text>')
    return (f'<svg data-story-machine="{esc(story["id"])}" viewBox="0 0 400 96" '
            f'role="img" aria-label="Story {esc(story["id"])} is at {esc(here)}">'
            '<defs><marker id="a1" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5.5" '
            'markerHeight="5.5" orient="auto-start-reverse">'
            '<path d="M0,0 L10,5 L0,10 z" fill="context-stroke"/></marker></defs>'
            + "".join(edges) + "".join(boxes) + back + "</svg>")


def render(payload: dict) -> str:
    health = payload["health"]
    features = payload["features"]
    if not features:
        body = "<p class=\"empty\">Nothing planned yet. The PM has not written a story.</p>"
    else:
        # The NEWEST feature — the one being built. Older features stay in the
        # database and the JSON api; the page shows the work in front of you.
        feature = payload.get("active_feature") or features[-1]
        chips, cards = [], []
        for story in payload["stories"]:
            issues = [i for i in payload["issues"] if i["story"] == story["id"]]
            stuck = story["sent_back"] >= 2
            chips.append(f'<span class="chip{" here" if stuck else ""}">'
                         f'{esc(story["id"])} &middot; {esc(story["state"])}</span>')
            rows = "".join(
                f'<tr><td>{esc(i["name"])}</td><td>{esc(i["status"])}</td>'
                f'<td class="mono">{esc(i["test_command"])}</td></tr>' for i in issues)
            cards.append(
                f'<div class="card story{" stuck" if stuck else ""}">'
                f'<div><p class="quote">&ldquo;{esc(story["text"])}&rdquo;</p>'
                f'<div class="said">{esc(story["who"])} &middot; story {esc(story["id"])}</div>'
                + (f'<details class="steps"><summary>The code steps under this story</summary>'
                   f'<table><thead><tr><th>What</th><th>Status</th><th>Proved by</th></tr></thead>'
                   f'<tbody>{rows}</tbody></table></details>' if issues else "")
                + f'</div><div>{machine_svg(story)}</div></div>')
        risk = (f'<p class="risknote">This feature is at risk. Story '
                f'<b>{esc(health["story"])}</b> &mdash; {esc(health["reason"])}.</p>'
                if health["replan_required"] else "")
        body = (f'<div class="card feature{" risk" if health["replan_required"] else ""}">'
                f'<h2>{esc(feature["title"])}</h2>'
                f'<p class="why">{esc(feature["for_whom"])}</p>'
                f'<div class="chips">{"".join(chips)}</div>{risk}</div>'
                f'<div class="eyebrow">The stories inside it &mdash; one machine each</div>'
                + "".join(cards))

    log = "".join(
        f'<tr><td class="t">{esc(e["at"][11:16])}</td>'
        f'<td class="sref">{esc(e["story"]) if e["story"] else "&mdash;"}</td>'
        f'<td>{esc(e["kind"])}</td><td class="mono">{esc(e["detail"])}</td></tr>'
        for e in payload["events"])

    notes = payload.get("notes") or []
    notes_html = ""
    if notes:
        rows = "".join(
            f'<div class="note"><span class="t">{esc(n["at"][11:16])}</span> '
            f'<b>{esc(n["who"])}</b>'
            + (f' <span class="sref">on {esc(n["story"])}</span>' if n["story"] else "")
            + f' &mdash; {esc(n["detail"])}</div>'
            for n in notes)
        notes_html = (
            '<div class="eyebrow">Sprint notes &mdash; an agent\'s commentary, '
            "never the facts (notes cannot move a box or the percentage)</div>"
            f'<div class="card">{rows}</div>')

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="5">
<title>Ondoway Agent Tracker</title>
<style>
:root {{ color-scheme: light; --plane:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b;
  --ink-2:#52514e; --muted:#898781; --grid:#e1e0d9; --axis:#c3c2b7;
  --ring:rgba(11,11,11,.10); --blue:#2a78d6; --good:#0ca30c; --critical:#d03b3b; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  color-scheme: dark; --plane:#0d0d0d; --surface:#1a1a19; --ink:#fff; --ink-2:#c3c2b7;
  --grid:#2c2c2a; --axis:#383835; --ring:rgba(255,255,255,.10); --blue:#3987e5; }} }}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--plane);color:var(--ink);
  font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;padding:0 0 52px}}
.wrap{{max-width:1120px;margin:0 auto;padding:0 20px}}
header.top{{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;padding:26px 0 4px}}
h1{{font-size:20px;margin:0;font-weight:650}}
.sub{{color:var(--ink-2);font-size:13px}}
.pct{{margin-left:auto;font-size:13px;color:var(--ink-2)}}
.pct b{{color:var(--ink);font-size:20px}}
.card{{background:var(--surface);border:1px solid var(--ring);border-radius:10px;
  padding:16px 18px 18px;margin-bottom:12px}}
.feature{{border-left:3px solid var(--blue)}} .feature.risk{{border-left-color:var(--critical)}}
.feature h2{{font-size:17px;margin:0 0 3px;font-weight:650}}
.why{{color:var(--ink-2);font-size:13px;margin:0 0 14px}}
.eyebrow{{font-size:11px;text-transform:uppercase;letter-spacing:.07em;
  color:var(--muted);font-weight:600;margin:26px 0 10px}}
.chips{{display:flex;gap:6px;flex-wrap:wrap}}
.chip{{font-size:12px;border:1px solid var(--ring);border-radius:999px;
  padding:3px 11px;color:var(--ink-2);background:var(--plane)}}
.chip.here{{border-color:var(--critical);color:var(--critical);font-weight:600}}
.risknote{{margin-top:14px;font-size:13px;color:var(--critical);
  border-top:1px solid var(--grid);padding-top:12px}}
.story{{display:grid;grid-template-columns:1fr 400px;gap:20px;align-items:start}}
@media (max-width:860px){{.story{{grid-template-columns:1fr}}}}
.story.stuck{{border-color:var(--critical)}}
.quote{{font-size:15.5px;line-height:1.45;margin:0 0 6px;font-weight:500}}
.said{{font-size:12px;color:var(--muted);margin-bottom:12px}}
details.steps summary{{cursor:pointer;font-size:12px;color:var(--muted);
  text-transform:uppercase;letter-spacing:.05em;font-weight:600}}
table{{border-collapse:collapse;width:100%;font-size:13px;margin-top:10px}}
th{{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.05em;
  color:var(--muted);padding:0 10px 6px 0;border-bottom:1px solid var(--grid)}}
td{{padding:7px 10px 7px 0;border-bottom:1px solid var(--grid);vertical-align:top}}
.mono{{font-family:ui-monospace,Menlo,monospace;font-size:11.5px;color:var(--ink-2)}}
.t{{color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap}}
.sref{{color:var(--ink-2)}}
.empty{{color:var(--muted);padding:40px 0;text-align:center}}
.note{{font-size:13px;padding:5px 0;border-bottom:1px solid var(--grid)}}
.note:last-child{{border-bottom:none}}
svg{{display:block;max-width:100%}}
.m-node{{fill:var(--plane);stroke:var(--axis);stroke-width:1.5}}
.m-node.done{{fill:var(--surface);stroke:var(--good);stroke-width:2}}
.m-node.here{{fill:var(--surface);stroke:var(--blue);stroke-width:3}}
.m-node.bad{{fill:var(--surface);stroke:var(--critical);stroke-width:3}}
.m-lab{{fill:var(--ink);font:600 11px system-ui,sans-serif}}
.m-tick{{fill:var(--good);font:11px system-ui,sans-serif}}
.m-edge{{stroke:var(--axis);stroke-width:1.75;fill:none}}
.m-edge.on{{stroke:var(--good)}}
.m-edge.back{{stroke:var(--critical);stroke-dasharray:4 3;stroke-width:2}}
.m-back-txt{{fill:var(--critical);font:650 10.5px system-ui,sans-serif}}
footer{{margin-top:24px;font-size:12.5px;color:var(--ink-2);
  border-top:1px solid var(--grid);padding-top:14px}}
</style></head><body><div class="wrap">
<header class="top"><h1>Agent tracker</h1>
<div class="sub">what the team is building right now</div>
<div class="pct"><b>{health["progress"]}%</b> of this feature proved</div></header>
{body}
{notes_html}
<div class="eyebrow">Every change, and who proved it</div>
<div class="card"><table><thead><tr><th>Time</th><th>Story</th><th>What happened</th>
<th>What track saw itself</th></tr></thead><tbody>{log}</tbody></table></div>
<footer><b>Read-only.</b> No agent writes to this page. Every row comes from the
tracker database, and the only thing that writes to that is <code>track</code> &mdash;
which refuses to mark a step done unless it re-ran that step's command itself and
saw a clean exit.</footer>
</div></body></html>"""


def cmd_serve(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    from http.server import BaseHTTPRequestHandler, HTTPServer

    if args.port in TAKEN_PORTS:
        raise Refused(
            f"port {args.port} is {TAKEN_PORTS[args.port]}. Binding it would take a "
            f"running service down, and the failure would look like this dashboard "
            f"working. Pick another; {DEFAULT_PORT} is the default."
        )

    db_path = args.db
    conn.close()  # everything below opens its own read-only handle

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, kind: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - stdlib's name
            if self.path == "/":
                self._send(200, render(dashboard_state(db_path)).encode(),
                           "text/html; charset=utf-8")
            elif self.path == "/api/state":
                self._send(200, json.dumps(dashboard_state(db_path), indent=2).encode(),
                           "application/json")
            else:
                self._send(404, b"not the dashboard", "text/plain")

        def log_message(self, *_: object) -> None:
            """Silent. The owner is watching the page, not this terminal."""

    server = HTTPServer(("127.0.0.1", args.port), Handler)
    # Announce the port actually bound. With --port 0 the OS picks one, and a caller
    # that had to guess would be racing.
    print(json.dumps({"port": server.server_port,
                      "url": f"http://127.0.0.1:{server.server_port}"}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


HANDLERS = {
    "init": cmd_init,
    "feature-add": cmd_feature_add,
    "story-add": cmd_story_add,
    "issue-add": cmd_issue_add,
    "issue-set": cmd_issue_set,
    "criterion-add": cmd_criterion_add,
    "owner-change": cmd_owner_change,
    "seal-check": cmd_seal_check,
    "interrupt": cmd_interrupt,
    "step-status": cmd_step_status,
    "story-state": cmd_story_state,
    "approve": cmd_approve,
    "note": cmd_note,
    "show": cmd_show,
    "health": cmd_health,
    "serve": cmd_serve,
}


def build_parser() -> argparse.ArgumentParser:
    # The common flags live on a PARENT parser rather than only at the top level,
    # so `track init --db X` works as well as `track --db X init`. Callers here are
    # agents writing a command line from a prompt, and they put the flags where
    # they read naturally — last. A parser that only accepted the other order
    # would fail on the shape everybody actually writes.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", type=Path, default=DEFAULT_DB)
    common.add_argument("--who", default="agent",
                        help="who is writing this row; appears in the event log")
    common.add_argument("--json", action="store_true",
                        help="accepted everywhere; output is always JSON")

    parser = argparse.ArgumentParser(
        prog="track", parents=[common],
        description="The shared tracker every agent reads and writes.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", parents=[common])

    p = sub.add_parser("feature-add", parents=[common])
    p.add_argument("--slug", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--for-whom", required=True, dest="for_whom")
    p.add_argument("--tier", type=int, required=True)

    p = sub.add_parser("story-add", parents=[common])
    p.add_argument("--feature", required=True)
    p.add_argument("--id", required=True)
    p.add_argument("--text", required=True, help="the story in the user's own words")
    p.add_argument("--said-by", required=True, dest="who_said",
                   help="whose words these are — the person the story quotes")

    p = sub.add_parser("issue-add", parents=[common])
    p.add_argument("--story", required=True)
    p.add_argument("--id", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--test-command", required=True, dest="test_command")
    p.add_argument("--files", required=True, nargs="+")
    p.add_argument("--depends-on", nargs="*", dest="depends_on")
    p.add_argument("--owner-change", default=None, dest="owner_change",
                   help="an owner-change id; required once the feature is approved")

    p = sub.add_parser("criterion-add", parents=[common])
    p.add_argument("--story", required=True)
    p.add_argument("--id", required=True)
    p.add_argument("--text", required=True,
                   help="the acceptance line in the owner's words")
    p.add_argument("--test-command", required=True, dest="test_command",
                   help="the runnable proof; red before the build, green at Done")
    p.add_argument("--negative", action="store_true")
    p.add_argument("--owner-change", default=None, dest="owner_change")

    p = sub.add_parser("owner-change", parents=[common])
    p.add_argument("--feature", required=True)
    p.add_argument("--by", required=True)
    p.add_argument("--why", required=True,
                   help="the owner's reason, in the owner's words")

    p = sub.add_parser("seal-check", parents=[common])
    p.add_argument("--feature", required=True)

    p = sub.add_parser("issue-set", parents=[common])
    p.add_argument("--id", required=True)
    p.add_argument("--test-command", required=True, dest="test_command")

    p = sub.add_parser("step-status", parents=[common])
    p.add_argument("--id", required=True)
    p.add_argument("--status", required=True,
                   choices=["pending", "in_progress", "completed", "blocked", "no-op", "skipped"])

    p = sub.add_parser("story-state", parents=[common])
    p.add_argument("--id", required=True)
    p.add_argument("--state", required=True)
    p.add_argument("--sent-back", action="store_true", dest="sent_back")
    p.add_argument("--why", default="")

    p = sub.add_parser("approve", parents=[common])
    p.add_argument("--feature", required=True)
    p.add_argument("--by", required=True)
    p.add_argument("--budget-commits", type=int, default=0, dest="budget_commits",
                   help="how many commits the seal budgets; 0 leaves the clock off")
    p.add_argument("--rate-minutes", type=int, default=0, dest="rate_minutes",
                   help="the measured minutes per commit behind the budget")

    p = sub.add_parser("interrupt", parents=[common])
    p.add_argument("--story", required=True)
    p.add_argument("--question", required=True,
                   help="the question in plain words; it lands in the event log")

    p = sub.add_parser("note", parents=[common])
    p.add_argument("--text", required=True,
                   help="one short sentence of commentary; shown attributed on the page")
    p.add_argument("--story", default=None)

    sub.add_parser("show", parents=[common])
    sub.add_parser("health", parents=[common])

    p = sub.add_parser("serve", parents=[common])
    p.add_argument("--port", type=int, default=DEFAULT_PORT,
                   help="0 lets the OS pick; the bound port is printed")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    conn = connect(args.db)
    try:
        if args.command != "init":
            conn.executescript(SCHEMA)
        _migrate(conn)
        HANDLERS[args.command](conn, args)
        conn.commit()
        return 0
    except Refused as refusal:
        conn.commit()  # the evidence of a refusal is kept, not rolled back
        payload = state_of(conn)
        payload["refused"] = str(refusal)
        payload.update(refusal.extra)
        print(json.dumps(payload, indent=2))
        return 1
    except sqlite3.DatabaseError as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 2
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
