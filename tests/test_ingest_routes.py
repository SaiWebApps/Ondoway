"""Tests for src/api/routes/ingest.py — Docs/ingestion/rebuild-spec.md §5
(the front door and the review queue), slice 7.

Hermetic: the router runs jobs on `llm.MockClient` scripted from a JSON
file (`INGEST_PROVIDER=mock` + `INGEST_MOCK_SCRIPT`, written per test from
tests/ingest_job_script.py) and writes only under a per-test
`INGEST_DATA_ROOT`; no live client, no network, no spend
(test_no_live_client_in_this_file, the same walker as the slice-3..6
files). Nothing here touches the repo's data/.

Run one file: make test-file FILE=tests/test_ingest_routes.py
"""

from __future__ import annotations

import ast
import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import src.api.routes.ingest as ingest_routes
from src.api.app import create_app
from src.connection import get_database
from src.ingest import jobs, model
from tests import ingest_job_script as script_mod
from tests.conftest import needs_neo4j


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A per-test chunk dir, data root and mock script, wired into the
    env the router reads; a fresh process-local store for the test."""
    chunks = script_mod.chunk_dir(tmp_path)
    data_root = script_mod.data_dir(tmp_path)
    unit = script_mod.unit(chunks)
    script_path = tmp_path / "script.json"
    script_mod.write_script(script_path, script_mod.script(unit))
    monkeypatch.setenv("INGEST_PROVIDER", "mock")
    monkeypatch.setenv("INGEST_MOCK_SCRIPT", str(script_path))
    monkeypatch.setenv("INGEST_DATA_ROOT", str(data_root))
    monkeypatch.setattr(jobs, "_STORE", jobs.IngestJobStore())
    return {"chunks": chunks, "data_root": data_root, "unit": unit, "script": script_path}


@pytest.fixture
def client(workspace):
    """A fresh TestClient per test; no Neo4j needed — the ingest endpoints
    never touch the graph. Overrides the conftest module-scoped client."""
    with TestClient(create_app()) as c:
        yield c


def _post_job(client, chunks: Path, city: str = script_mod.CITY):
    return client.post(
        "/api/v1/ingest/jobs",
        json={
            "city": city,
            "source": {"kind": "book", "chunk_dir": str(chunks)},
            "as_of": 2023,
            "rights_basis": "owned_copy",
        },
    )


def _wait_terminal(client, job_id: str, timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        r = client.get(f"/api/v1/ingest/jobs/{job_id}")
        assert r.status_code == 200, r.text
        last = r.json()
        if last["status"] in ("committed", "error"):
            return last
        time.sleep(0.05)
    raise AssertionError(f"job {job_id!r} never finished; last={last}")


def _run_job(client, chunks: Path) -> tuple[str, dict]:
    r = _post_job(client, chunks)
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    return job_id, _wait_terminal(client, job_id)


def _hold_a_merge(workspace) -> None:
    """Re-script the job so its merge is held: two beats already at the
    place, a judge that calls the story new while the hint matches it."""
    script_mod.seed_beats(workspace["data_root"], script_mod.existing_records())
    scripted = script_mod.script(workspace["unit"])
    scripted["answers"]["merge_judge"] = [
        script_mod.merge_answer(
            "new", "",
            [{"claim_id": c, "verdict": "new", "existing_claim_id": "", "new_value": "",
              "existing_value": "", "reason": "nothing like it"} for c in ("c01", "c02", "c03")],
        )
    ]
    script_mod.write_script(workspace["script"], scripted)


def test_post_job_returns_202_and_the_snapshot_reaches_p7(client, workspace):
    """§5: POST /ingest/jobs → 202 + a job id; GET /ingest/jobs/{id} is a
    seq-numbered snapshot whose phases reach P7 on the mock, with the cost
    estimate as the first event and the committed file on disk under the
    data root — valid, spans grounded."""
    job_id, snap = _run_job(client, workspace["chunks"])

    assert snap["status"] == "committed", snap["error"]
    assert snap["job_id"] == job_id
    assert snap["city"] == script_mod.CITY
    assert snap["source"] == {"kind": "book", "chunk_dir": str(workspace["chunks"]), "url": None}
    assert snap["events"][0]["message"] == "cost_estimate"
    assert [e["message"] for e in snap["events"] if e["kind"] == "phase"] == [
        "P0", "P1", "P2", "P3", "P4", "P5", "P6", "P7"
    ]
    assert snap["max_seq"] == len(snap["events"])
    assert [p for p, s in snap["phases"].items() if s["status"] == "done"] == list(jobs.PHASES)
    assert snap["phases"]["P7"]["written"] == 1
    assert snap["current_phase"] is None

    records = json.loads(
        (workspace["data_root"] / script_mod.CITY / "beats.json").read_text(encoding="utf-8")
    )
    assert [r["beat_id"] for r in records] == [script_mod.BEAT_ID]
    assert model.validate(records, chunks_root=workspace["chunks"].parent) == []

    # after_seq replays only what is newer.
    r = client.get(f"/api/v1/ingest/jobs/{job_id}", params={"after_seq": snap["max_seq"] - 1})
    assert [e["seq"] for e in r.json()["events"]] == [snap["max_seq"]]
    assert client.get("/api/v1/ingest/jobs/nope").status_code == 404


def test_stream_is_event_stream_and_terminates(client, workspace, monkeypatch):
    """§5: GET /ingest/jobs/{id}/stream is `text/event-stream`, carries one
    `data:` frame per event in seq order and closes after exactly one
    `event: end` frame once the job is done. The valve is tightened so a
    stream that never terminated would fail here in seconds."""
    monkeypatch.setattr(ingest_routes, "_SSE_MAX_SECONDS", 2.0)
    job_id, snap = _run_job(client, workspace["chunks"])
    assert snap["status"] == "committed", snap["error"]

    frames: list[str] = []
    errors: list[Exception] = []

    def _read() -> None:
        try:
            with client.stream("GET", f"/api/v1/ingest/jobs/{job_id}/stream") as resp:
                assert resp.status_code == 200
                assert resp.headers["content-type"].startswith("text/event-stream")
                for line in resp.iter_lines():
                    frames.append(line)
        except Exception as exc:  # surface any read/assert failure to the main thread
            errors.append(exc)

    t = threading.Thread(target=_read, daemon=True)
    t.start()
    t.join(timeout=6.0)
    assert not t.is_alive(), "stream read never completed"
    assert not errors, f"stream read raised: {errors!r}"
    data = [
        json.loads(f[len("data:"):]) for f in frames if f.startswith("data:") and f != "data: {}"
    ]
    assert [d["seq"] for d in data] == list(range(1, snap["max_seq"] + 1))
    assert [d["message"] for d in data if d["kind"] == "phase"] == list(jobs.PHASES)
    assert len([f for f in frames if f.startswith("event: end")]) == 1
    assert client.get("/api/v1/ingest/jobs/nope/stream").status_code == 404


@needs_neo4j
def test_publish_refuses_while_an_item_is_held(client, workspace, clean_driver):
    """§5's proving node: a job whose merge is held leaves an undecided
    queue item, GET /ingest/review?city= lists it (ranked, with the hash
    a decision binds to), and POST /ingest/publish refuses 409 naming it
    until it is decided. Once decided, publish converges the graph on the
    file (slice 8, §6): the two beats the file holds are linked."""
    _hold_a_merge(workspace)
    job_id, snap = _run_job(client, workspace["chunks"])
    assert snap["status"] == "committed", snap["error"]

    r = client.get("/api/v1/ingest/review", params={"city": script_mod.CITY})
    assert r.status_code == 200
    items = r.json()["items"]
    assert [(i["kind"], i["story_slug"], i["job_id"], i["decision"]) for i in items] == [
        ("merge_held", script_mod.STORY_SLUG, job_id, None)
    ]
    item = items[0]
    assert item["shown_hash"] == item["item_id"] == jobs.shown_hash(item["shown"])
    assert item["held_back"]["claims"] == 3
    assert client.get("/api/v1/ingest/review", params={"city": "paris"}).json()["items"] == []

    r = client.post("/api/v1/ingest/publish", params={"city": script_mod.CITY, "target": "local"})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["undecided"] == [item["item_id"]]
    assert "undecided" in r.json()["detail"]["error"]

    r = client.post(
        "/api/v1/ingest/review/decision",
        json={"item_id": item["item_id"], "decision": "reject", "decided_by": "adam",
              "shown_hash": item["shown_hash"]},
    )
    assert r.status_code == 200, r.text
    r = client.post("/api/v1/ingest/publish", params={"city": script_mod.CITY, "target": "local"})
    assert r.status_code == 200, r.text
    assert r.json()["beats"]["linked"] == 2


def _beat_status(driver, beat_id: str) -> str | None:
    with driver.session(database=get_database()) as s:
        rec = s.run(
            "MATCH (b:NarrativeBeat {beat_id: $bid}) RETURN b.active_status AS st", bid=beat_id
        ).single()
    return rec["st"] if rec else None


@needs_neo4j
def test_publish_converges_the_graph_on_the_file(client, workspace, clean_driver):
    """Slice 8 (§6) behind the front door: POST /ingest/publish?target=local
    validates the city's new-shape file under its chunks root and makes the
    graph match it — both beats active; a re-publish of a file that dropped
    one beat withdraws exactly that beat; and a cloud target is refused
    here (D14: the cloud publish is a human at the keyboard, never an
    unauthenticated route). [undo: restore the 501 → RED]"""
    origins, visiting = script_mod.existing_records()
    script_mod.seed_beats(workspace["data_root"], [origins, visiting])

    r = client.post("/api/v1/ingest/publish", params={"city": script_mod.CITY, "target": "local"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["city"] == script_mod.CITY and body["target"] == "local"
    assert body["beats"]["linked"] == 2 and body["beats"]["withdrawn"] == 0
    assert body["pois"]["created"] > 0
    assert _beat_status(clean_driver, script_mod.ORIGINS_ID) == "active"
    assert _beat_status(clean_driver, script_mod.VISITING_ID) == "active"

    script_mod.seed_beats(workspace["data_root"], [origins])
    r = client.post("/api/v1/ingest/publish", params={"city": script_mod.CITY, "target": "local"})
    assert r.status_code == 200, r.text
    assert r.json()["beats"]["linked"] == 1 and r.json()["beats"]["withdrawn"] == 1
    assert _beat_status(clean_driver, script_mod.ORIGINS_ID) == "active"
    assert _beat_status(clean_driver, script_mod.VISITING_ID) == "withdrawn"

    r = client.post("/api/v1/ingest/publish", params={"city": script_mod.CITY, "target": "cloud"})
    assert r.status_code == 400, r.text
    assert "cloud" in r.json()["detail"]["error"]
    r = client.post("/api/v1/ingest/publish", params={"city": "atlantis", "target": "local"})
    assert r.status_code == 404, r.text
    assert _beat_status(clean_driver, script_mod.VISITING_ID) == "withdrawn", "nothing changed"


def test_decision_is_bound_to_shown_hash(client, workspace):
    """§5's proving node: POST /ingest/review/decision records
    {item_id, decision, decided_by} bound to the hash of what was shown.
    A decision carrying any other hash — the reviewer saw something else,
    or the item changed under them — is refused 409 and the item stays
    undecided; the right hash is recorded once, with the hash it was
    bound to and who decided; a second decision is refused; an unknown
    item is 404."""
    _hold_a_merge(workspace)
    _job_id, snap = _run_job(client, workspace["chunks"])
    assert snap["status"] == "committed", snap["error"]
    item = client.get("/api/v1/ingest/review", params={"city": script_mod.CITY}).json()["items"][0]
    other = jobs.shown_hash({**item["shown"], "narration": "something the reviewer never saw"})
    assert other != item["shown_hash"]

    def decide(shown_hash: str, item_id: str = item["item_id"]):
        return client.post(
            "/api/v1/ingest/review/decision",
            json={"item_id": item_id, "decision": "accept", "decided_by": "adam",
                  "shown_hash": shown_hash},
        )

    r = decide(other)
    assert r.status_code == 409, r.text
    assert "bound to hash" in r.json()["detail"]
    listed = client.get("/api/v1/ingest/review", params={"city": script_mod.CITY}).json()["items"]
    assert listed[0]["decision"] is None

    r = decide(item["shown_hash"])
    assert r.status_code == 200, r.text
    decision = r.json()["item"]["decision"]
    assert decision["decision"] == "accept"
    assert decision["decided_by"] == "adam"
    assert decision["bound_to"] == item["shown_hash"]
    assert decision["decided_at"]
    listed = client.get("/api/v1/ingest/review", params={"city": script_mod.CITY}).json()["items"]
    assert listed[0]["decision"] == decision

    assert decide(item["shown_hash"]).status_code == 409
    assert decide(item["shown_hash"], item_id="nope").status_code == 404


def test_create_job_refuses_bad_input_up_front(client, workspace):
    """An unknown city, a source outside the §5 union and a chunk dir
    without a manifest are 422 before any job exists."""
    assert _post_job(client, workspace["chunks"], city="atlantis").status_code == 422
    r = client.post(
        "/api/v1/ingest/jobs",
        json={"city": script_mod.CITY, "source": {"kind": "book"}, "as_of": 2023,
              "rights_basis": "owned_copy"},
    )
    assert r.status_code == 422
    assert _post_job(client, workspace["chunks"].parent).status_code == 422
    assert client.get("/api/v1/ingest/jobs").json()["jobs"] == []


def test_no_live_client_in_this_file():
    """This $0-spend test file never names a live LLM client or reads its
    API key. Its own body is exempt from the walk below — this docstring
    and the assert messages name those things on purpose to describe the
    rule, which is not the violation the rule guards against.
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    self_name = "test_no_live_client_in_this_file"
    forbidden_names = {"AnthropicClient", "anthropic"}
    forbidden_env_var = "ANTHROPIC_API_KEY"

    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == self_name:
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name):
                assert sub.id not in forbidden_names, f"{sub.id!r} must not appear in this file"
            elif isinstance(sub, ast.Attribute):
                assert sub.attr not in forbidden_names, (
                    f"{sub.attr!r} must not appear in this file"
                )
            elif isinstance(sub, ast.ImportFrom) and sub.module:
                assert sub.module.split(".")[0] not in forbidden_names, (
                    f"from-import of {sub.module!r} must not appear in this file"
                )
            elif isinstance(sub, ast.alias):
                top_level_name = sub.name.split(".")[0]
                assert top_level_name not in forbidden_names, (
                    f"import of {sub.name!r} must not appear in this file"
                )
                assert sub.asname not in forbidden_names, (
                    f"import alias {sub.asname!r} must not appear in this file"
                )
            elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                assert forbidden_env_var not in sub.value, (
                    f"{forbidden_env_var!r} must not appear in this file"
                )
