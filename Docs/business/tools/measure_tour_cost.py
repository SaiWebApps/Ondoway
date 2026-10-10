"""Measure the real provider spend of one traveller's tour, end to end.

Drives the product's own endpoints in-process (generate -> compose -> voice) with the
real providers, and records every Anthropic and OpenAI TTS call at the SDK / HTTP seam.
Nothing under src/ is changed. Run under the dev env wrapper:

  uv run python scripts/dev_env.py exec --profile local --render -- \
      env TTS_FALLBACK= AUDIO_STORAGE_PATH=<scratch>/audio_store \
      uv run python <this file> <out.jsonl> <tour indexes...>
"""

from __future__ import annotations

import contextvars
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path

REPO = Path("/Users/sairambkrishnan/git/ondoway")
sys.path.insert(0, str(REPO))
os.chdir(REPO)

import anthropic.resources.messages.messages as _am  # noqa: E402

import src.api.routes.trips as trips_routes  # noqa: E402
import src.audio.provider as audio_provider  # noqa: E402
from src.audio.pipeline import _get_duration  # noqa: E402

# ── pricing basis (USD per million tokens), checked 2026-10-09 ──
PRICES = {
    "claude-opus-4-8": {"in": 5.0, "out": 25.0},
    "claude-haiku-4-5": {"in": 1.0, "out": 5.0},
}
CACHE_WRITE_X, CACHE_READ_X = 1.25, 0.10
TTS_USD_PER_MIN = 0.015  # OpenAI's published estimate for gpt-4o-mini-tts
TTS_TEXT_USD_PER_MTOK = 0.60  # text input; chars/4 as the token estimate
SPEND_CEILING_USD = 23.0


def price_of(model: str) -> dict[str, float]:
    for prefix, p in PRICES.items():
        if model.startswith(prefix):
            return p
    raise KeyError(model)


def anthropic_usd(c: dict) -> float:
    p = price_of(c["model"])
    return (
        c["input_tokens"] * p["in"]
        + c["cache_creation_input_tokens"] * p["in"] * CACHE_WRITE_X
        + c["cache_read_input_tokens"] * p["in"] * CACHE_READ_X
        + c["output_tokens"] * p["out"]
    ) / 1e6


def tts_usd(c: dict) -> float:
    return c["audio_seconds"] / 60 * TTS_USD_PER_MIN + c["chars"] / 4 * TTS_TEXT_USD_PER_MTOK / 1e6


# ── recorders ──
_lock = threading.Lock()
CALLS: list[dict] = []
_opus_phase: contextvars.ContextVar[str | None] = contextvars.ContextVar("opus_phase", default=None)


def _stack_names() -> tuple[set[str], set[str]]:
    f = sys._getframe(2)
    files, funcs = set(), set()
    while f is not None:
        files.add(Path(f.f_code.co_filename).name)
        funcs.add(f.f_code.co_name)
        f = f.f_back
    return files, funcs


def _tag_anthropic(model: str) -> str:
    files, funcs = _stack_names()
    if model.startswith("claude-opus"):
        if "_reroll_stops" in funcs:
            return "opus_reroll"
        return f"opus_{_opus_phase.get() or 'unlabelled'}"
    if "verify.py" in files:
        return "haiku_fact_check"
    if "glue_client.py" in files:
        return "haiku_glue"
    return "haiku_other:" + ",".join(sorted(f for f in files if f.endswith(".py") and "anthropic" not in f))[:200]


_orig_create = _am.Messages.create


def _recording_create(self, *args, **kwargs):
    resp = _orig_create(self, *args, **kwargs)
    u = resp.usage
    call = {
        "provider": "anthropic",
        "model": resp.model,
        "tag": _tag_anthropic(resp.model),
        "input_tokens": u.input_tokens,
        "output_tokens": u.output_tokens,
        "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
        "stop_reason": resp.stop_reason,
        "t": time.time(),
    }
    with _lock:
        CALLS.append(call)
    return resp


def _refuse_stream(self, *args, **kwargs):
    raise RuntimeError("measurement assumes no streaming Anthropic calls; one was made")


_am.Messages.create = _recording_create
_am.Messages.stream = _refuse_stream

_orig_execute = trips_routes.execute_premium_plan


def _labelled_execute(plan, **kwargs):
    caller = sys._getframe(1).f_code.co_name
    if caller == "compose_trip":
        label = "full_telling" if plan.units[0].authorized_request.already_told else "day_stop"
    elif "standby" in caller:
        label = "standby"
    else:
        label = caller
    token = _opus_phase.set(label)
    try:
        return _orig_execute(plan, **kwargs)
    finally:
        _opus_phase.reset(token)


trips_routes.execute_premium_plan = _labelled_execute

_orig_post = audio_provider._post_with_retry


def _recording_post(url, *, headers, json, timeout=60.0):  # noqa: A002
    resp = _orig_post(url, headers=headers, json=json, timeout=timeout)
    _files, funcs = _stack_names()
    call = {
        "provider": "tts",
        "url": url,
        "model": json.get("model"),
        "voice": json.get("voice"),
        "status": resp.status_code,
        "tag": "tts_session_lines" if "_voice_session_lines" in funcs else "tts_stop_narration",
        "chars": len(json.get("input") or json.get("text") or ""),
        "audio_bytes": len(resp.content) if resp.status_code == 200 else 0,
        "audio_seconds": round(_get_duration(resp.content), 2) if resp.status_code == 200 else 0.0,
        "t": time.time(),
    }
    with _lock:
        CALLS.append(call)
    return resp


audio_provider._post_with_retry = _recording_post

# ── the tour matrix ──
PLACES = {
    "Washington Square Park": (40.730823, -73.997332),
    "Bryant Park": (40.753751, -73.983543),
    "Federal Hall": (40.707224, -74.010259),
    "Union Square": (40.736072, -73.990189),
    "Rockefeller Center": (40.759258, -73.979957),
    "City Hall": (40.713282, -74.006978),
    "Grand Central Terminal": (40.752694, -73.977253),
    "Bowling Green Park": (40.705, -74.013611),
    "Flatiron Building": (40.741059, -73.989642),
    "Times Square": (40.758, -73.9855),
    "Wall Street": (40.7064, -74.0094),
    "St. Paul's Chapel": (40.7114, -74.0094),
    "Central Park Bethesda": (40.7740, -73.9712),
    "South Street Seaport": (40.7065, -74.0036),
}
TOURS = [
    ("Washington Square Park", 60, "solo", ["historic_arch", "social_change"]),
    ("Bryant Park", 60, "family", ["local_legends", "hidden_history"]),
    ("Federal Hall", 60, "solo", ["hidden_history", "dark_history", "social_change"]),
    ("Union Square", 90, "solo", ["social_change", "music_heritage"]),
    ("Rockefeller Center", 90, "family", ["visual_art", "local_legends"]),
    ("City Hall", 90, "solo", ["historic_arch", "hidden_history", "dark_history"]),
    ("Flatiron Building", 90, "family", ["historic_arch", "local_legends"]),
    ("Grand Central Terminal", 180, "solo", ["historic_arch", "modern_design"]),
    ("Washington Square Park", 180, "family", ["local_legends", "visual_art", "hidden_history"]),
    ("Bowling Green Park", 180, "solo", ["dark_history", "social_change"]),
    # Second batch: solo only (the planner refused every New York family request
    # probed), starts the free planner accepts.
    ("Grand Central Terminal", 90, "solo", ["historic_arch", "social_change"]),
    ("Wall Street", 90, "solo", ["local_legends", "hidden_history"]),
    ("Union Square", 90, "solo", ["social_change", "music_heritage"]),
    ("Times Square", 90, "solo", ["hidden_history", "dark_history", "social_change"]),
    ("Flatiron Building", 180, "solo", ["historic_arch", "social_change"]),
    ("Central Park Bethesda", 180, "solo", ["historic_arch", "social_change", "local_legends"]),
    ("South Street Seaport", 180, "solo", ["local_legends", "hidden_history"]),
    ("St. Paul's Chapel", 180, "solo", ["historic_arch", "social_change"]),
    ("Wall Street", 60, "solo", ["historic_arch", "hidden_history"]),
]

USER_ID = "cost-measure-user"
USER_EMAIL = "cost-measure@example.test"
PROFILE_ID = "cost-measure-profile"


def _cleanup(driver) -> None:
    with driver.session() as s:
        s.run(
            "MATCH (p:Profile {id: $pid}) OPTIONAL MATCH (p)-[*1..2]-(t:Trip) "
            "OPTIONAL MATCH (t)--(i:ItineraryItem) DETACH DELETE t, i",
            pid=PROFILE_ID,
        )
        s.run("MATCH (p:Profile {id: $pid}) DETACH DELETE p", pid=PROFILE_ID)
        s.run("MATCH (u:User {id: $uid}) DETACH DELETE u", uid=USER_ID)


def _summarise(calls: list[dict]) -> dict:
    out: dict = {"by_tag": {}, "anthropic_usd": 0.0, "tts_usd": 0.0}
    for c in calls:
        b = out["by_tag"].setdefault(
            c["tag"],
            {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0,
             "cache_read_input_tokens": 0, "chars": 0, "audio_seconds": 0.0, "usd": 0.0},
        )
        b["calls"] += 1
        if c["provider"] == "anthropic":
            usd = anthropic_usd(c)
            out["anthropic_usd"] += usd
            for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
                b[k] += c[k]
        else:
            usd = tts_usd(c)
            out["tts_usd"] += usd
            b["chars"] += c["chars"]
            b["audio_seconds"] += c["audio_seconds"]
        b["usd"] += usd
    out["total_usd"] = out["anthropic_usd"] + out["tts_usd"]
    return out


def main() -> None:
    out_path = Path(sys.argv[1])
    indexes = [int(a) for a in sys.argv[2:]]
    assert os.environ.get("TTS_PROVIDER") == "openai", os.environ.get("TTS_PROVIDER")
    assert not os.environ.get("TTS_FALLBACK"), "a failover voice would be invisible"

    from fastapi.testclient import TestClient

    from src.api.app import create_app
    from src.api.auth.tokens import create_access_token
    from src.api.dependencies import get_driver, get_session
    from neo4j import GraphDatabase

    uri = os.environ["NEO4J_URI"]
    assert uri.startswith("bolt://localhost:"), uri
    driver = GraphDatabase.driver(uri, auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]))
    driver.verify_connectivity()
    _cleanup(driver)
    with driver.session() as s:
        s.run("MERGE (u:User {id: $uid}) SET u.email = $email", uid=USER_ID, email=USER_EMAIL)
        s.run(
            "MERGE (p:Profile {id: $pid}) SET p.display_name = 'cost measurement' "
            "WITH p MATCH (u:User {id: $uid}) MERGE (u)-[:HAS_PROFILE]->(p)",
            pid=PROFILE_ID, uid=USER_ID,
        )

    app = create_app()

    def _live_session():
        with driver.session() as s:
            yield s

    app.dependency_overrides[get_session] = _live_session
    app.dependency_overrides[get_driver] = lambda: driver

    def _bearer(request):
        request.headers["Authorization"] = f"Bearer {create_access_token(USER_ID, USER_EMAIL)}"

    prior = []
    if out_path.exists():
        prior = [json.loads(line) for line in out_path.read_text().splitlines() if line.strip()]
    spent = sum(r["summary"]["total_usd"] for r in prior)

    try:
        with TestClient(app) as client:
            client.event_hooks["request"] = [_bearer]
            for idx in indexes:
                if spent >= SPEND_CEILING_USD:
                    print(f"STOP: cumulative spend ${spent:.2f} >= ceiling", flush=True)
                    break
                place, minutes, party, lenses = TOURS[idx]
                lat, lng = PLACES[place]
                rec: dict = {"index": idx, "start": place, "duration_min": minutes, "party": party,
                             "lenses": lenses, "city_slug": "new_york"}
                CALLS.clear()
                t0 = time.time()
                try:
                    body = {
                        "profile_id": PROFILE_ID, "center_lat": lat, "center_lng": lng,
                        "duration_min": minutes, "start_date": "2026-10-14", "end_date": "2026-10-14",
                        "start_time": "10:00", "start_datetime": "2026-10-14T10:00",
                        "party": party, "lenses": lenses, "city_slug": "new_york",
                    }
                    g = client.post("/api/v1/trips/generate", json=body)
                    rec["generate_status"] = g.status_code
                    assert not CALLS, f"generate made {len(CALLS)} paid calls"
                    if g.status_code // 100 != 2:
                        rec["error"] = g.text[:2000]
                        raise RuntimeError("generate failed")
                    gj = g.json()
                    trip_id = gj["trip_id"]
                    rec["trip_id"] = trip_id
                    rec["planned_stops"] = gj["total_stops"]
                    t1 = time.time()
                    c = client.post(f"/api/v1/trips/{trip_id}/compose", json={"route_id": f"{trip_id}-opt1"})
                    rec["compose_status"] = c.status_code
                    rec["compose_seconds"] = round(time.time() - t1)
                    rec["compose_calls_end"] = len(CALLS)
                    if c.status_code // 100 != 2:
                        rec["error"] = c.text[:2000]
                        raise RuntimeError("compose refused")
                    cj = c.json()
                    rec["attempts"] = cj["attempts"]
                    stops = cj["stops"]
                    rec["stops_narrated"] = sum(1 for s in stops if s.get("narration"))
                    rec["full_tellings"] = sum(1 for s in stops if s.get("full_narration"))
                    rec["thread_lines"] = sum(len(s.get("thread_lines") or {}) for s in stops)
                    rec["narration_chars"] = sum(len(s.get("narration") or "") for s in stops)
                    rec["full_narration_chars"] = sum(len(s.get("full_narration") or "") for s in stops)
                    rec["extra_narration_chars_on_demand"] = sum(
                        len(s.get("extra_narration") or "") for s in stops
                    )
                    t2 = time.time()
                    a = client.post(f"/api/v1/audio/generate-trip-stops/{trip_id}", json={})
                    rec["audio_status"] = a.status_code
                    rec["audio_seconds_wall"] = round(time.time() - t2)
                    if a.status_code // 100 == 2:
                        aj = a.json()
                        rec["audio_items"] = {k: aj[k] for k in ("generated", "skipped", "failed")}
                    else:
                        rec["error"] = a.text[:2000]
                    sess = client.get(f"/api/v1/trips/{trip_id}/session")
                    if sess.status_code == 200:
                        sj = sess.json()
                        rec["session_lists"] = {
                            k: len(v) for k, v in sj.items() if isinstance(v, list)
                        }
                except Exception as exc:  # record the spend whatever happened
                    rec.setdefault("error", f"{type(exc).__name__}: {exc}")
                    rec["traceback"] = traceback.format_exc()[-3000:]
                rec["wall_seconds"] = round(time.time() - t0)
                rec["calls"] = list(CALLS)
                rec["summary"] = _summarise(CALLS)
                spent += rec["summary"]["total_usd"]
                with out_path.open("a") as fh:
                    fh.write(json.dumps(rec) + "\n")
                print(
                    f"tour {idx} {place} {minutes}m {party}: compose={rec.get('compose_status')} "
                    f"audio={rec.get('audio_status')} ${rec['summary']['total_usd']:.3f} "
                    f"(cumulative ${spent:.2f}) {json.dumps({k: (v['calls'], round(v['usd'], 3)) for k, v in rec['summary']['by_tag'].items()})}",
                    flush=True,
                )
    finally:
        _cleanup(driver)
        driver.close()


if __name__ == "__main__":
    main()
