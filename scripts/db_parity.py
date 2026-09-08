"""Environment parity: does the connected Neo4j match the repo source of truth?

The repo files (``data/{slug}/{poi-raw,beats,areas,within_edges}.json``) are the
single source of truth; every graph (local dev, test, Aura) is DERIVED from them.
Because those graphs are loaded by hand at different times, they drift silently —
this is the gate that makes drift LOUD instead of discovered-by-accident.

For every city it compares, against the profile injected by Make:
  - POIs        : name_key set (in-bbox, from poi-raw.json) vs POI nodes
  - footprints  : (name_key, trigger_radius, poi_role) vs the POI nodes' own
  - anchors     : (name_key, canonical anchors JSON) vs the POI nodes' own
  - beats       : beat_id set (uploadable AND linkable) vs POI-reachable beats
  - beat bodies : (beat_id, sha256 of normalized script_body) vs the graph's text
  - placement   : (beat_id, sub_location, trigger_address) vs the graph's own
  - areas       : name set (areas.json) vs Area nodes
  - POI->Area   : resolvable within_edges vs WITHIN edges

Prints a per-city report and exits NON-ZERO on any drift (missing/extra), so it
can gate a deploy. Repo beats that are blocked (disputed/no-poi) or unlinkable
(poi_name matches no POI) are reported as WARNINGS, not drift — they are
correctly absent from the graph.

Usage:
    make db-parity                 # local dev graph
    make db-parity CITY=new_york   # one city
    make db-parity TARGET=cloud    # read-only Aura session
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from neo4j import READ_ACCESS

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.upload_paris import CITY_BBOX, _beat_blocked, _in_city_bounds
from src.api.models.nodes import _normalized_script_body_hash, canonical_name_key
from src.city_registry import load_registry, onboard_data_root
from src.connection import create_driver, get_database


def _data_root() -> Path:
    """Hermetic-aware data root (``$ONBOARD_DATA_ROOT`` when set, else
    ``<repo>/data``). Resolved at CALL time so the deploy subprocess (env set at
    launch) and the tests both see the right root; unset → ``ROOT/data`` exactly."""
    return onboard_data_root()


def _cities() -> list[str]:
    return [
        d.name
        for d in sorted(_data_root().iterdir())
        if d.is_dir() and (d / "poi-raw.json").exists()
    ]


def _load(path: Path) -> list | dict:
    return json.loads(path.read_text()) if path.exists() else []


def _expected(slug: str) -> dict:
    """Repo-derived sets that SHOULD be in the graph for this city."""
    ddir = _data_root() / slug
    bbox = CITY_BBOX.get(slug)
    pois = _load(ddir / "poi-raw.json")
    # In-bbox POIs are the ones the uploader keeps.
    in_poi = [
        p
        for p in pois
        if p.get("latitude") is not None
        and p.get("longitude") is not None
        and (bbox is None or _in_city_bounds(float(p["latitude"]), float(p["longitude"]), bbox))
    ]
    poi_names = {p["name"] for p in in_poi}
    poi_keys = {canonical_name_key(p["name"]) for p in in_poi}

    beats = _load(ddir / "beats.json")
    uploadable = [b for b in beats if b.get("beat_id") and not _beat_blocked(b)]
    linkable = {b["beat_id"] for b in uploadable if b.get("poi_name") in poi_names}
    unlinkable = sorted(b["beat_id"] for b in uploadable if b.get("poi_name") not in poi_names)
    blocked = sum(1 for b in beats if b.get("beat_id") and _beat_blocked(b))

    areas = _load(ddir / "areas.json")
    area_names = {a["name"] for a in areas}
    within = _load(ddir / "within_edges.json")
    # Resolvable POI->Area edges: poi_name is an in-bbox POI, area_name is a known area.
    p2a = {
        (e["poi_name"], e["area_name"])
        for e in (within.get("poi_to_area", []) if isinstance(within, dict) else [])
        if e["poi_name"] in poi_names and e["area_name"] in area_names
    }
    # The fields the graph decides ARRIVAL and PLACEMENT from, and the words it
    # voices: id-set parity alone let a footprint or a body drift silently.
    footprints = {
        (canonical_name_key(p["name"]), p.get("trigger_radius", 10), p.get("poi_role"))
        for p in in_poi
    }

    def _canon_anchors(raw) -> str | None:
        if not isinstance(raw, list) or not raw:
            return None
        return json.dumps(raw, ensure_ascii=False, sort_keys=True)

    anchors = {
        (canonical_name_key(p["name"]), _canon_anchors(p.get("anchors")))
        for p in in_poi
    }

    def _canon_verified(raw) -> str | None:
        if not isinstance(raw, dict) or not raw:
            return None
        return json.dumps(raw, ensure_ascii=False, sort_keys=True)

    # The trust half of the clock (Docs/adr/0003): the door verdict and the
    # verification record. In the parity set so the upload hop is mechanically
    # enforced — a field that reaches poi-raw.json and never the graph turns
    # the lane's dev-data preflight red instead of silently serving days that
    # hedge on hours a human already confirmed.
    clock_trust = {
        (
            canonical_name_key(p["name"]),
            p.get("gated") if isinstance(p.get("gated"), bool) else None,
            _canon_verified(p.get("opening_hours_verified")),
        )
        for p in in_poi
    }
    body_hashes = {
        (b["beat_id"], _normalized_script_body_hash(b.get("script_body") or ""))
        for b in uploadable
        if b.get("poi_name") in poi_names
    }
    placement = {
        (b["beat_id"], b.get("sub_location") or None, b.get("trigger_address") or None)
        for b in uploadable
        if b.get("poi_name") in poi_names
    }
    return {
        "poi_keys": poi_keys,
        "footprints": footprints,
        "anchors": anchors,
        "clock_trust": clock_trust,
        "beat_ids": linkable,
        "body_hashes": body_hashes,
        "placement": placement,
        "area_names": area_names,
        "p2a": p2a,
        "unlinkable": unlinkable,
        "blocked": blocked,
    }


def _actual(session, slug: str) -> dict:
    q = lambda c, **kw: session.run(c, city=slug, **kw)  # noqa: E731
    # Body places (toilets/benches, redesign row 6.3, plan S2.5) are uploaded
    # as poi_role="body" :POI nodes — deliberately, so they flow through the
    # SAME planner loader — but they come from data/{slug}/body-places.json,
    # never poi-raw.json, so they are NOT part of the repo-vs-db POI
    # comparison below. Same shape as the blocked/unlinkable beat exclusion
    # a few lines down: a real, named, non-drift category, not a fold-in.
    poi_keys = {
        r["k"]
        for r in q(
            "MATCH (p:POI {city_name:$city}) "
            "WHERE p.poi_role IS NULL OR p.poi_role <> 'body' "
            "RETURN p.name_key AS k"
        )
        if r["k"]
    }
    beat_ids = {
        r["b"]
        for r in q(
            "MATCH (:POI {city_name:$city})-[:HAS_BEAT]->(b:NarrativeBeat) "
            "WHERE b.beat_id IS NOT NULL RETURN DISTINCT b.beat_id AS b"
        )
    }
    footprints = {
        (r["k"], r["r"], r["role"])
        for r in q(
            "MATCH (p:POI {city_name:$city}) "
            "WHERE p.poi_role IS NULL OR p.poi_role <> 'body' "
            "RETURN p.name_key AS k, p.trigger_radius AS r, p.poi_role AS role"
        )
        if r["k"]
    }
    def _canon_anchors_str(raw) -> str | None:
        if not raw:
            return None
        return json.dumps(json.loads(raw), ensure_ascii=False, sort_keys=True)

    anchors = {
        (r["k"], _canon_anchors_str(r["a"]))
        for r in q(
            "MATCH (p:POI {city_name:$city}) "
            "WHERE p.poi_role IS NULL OR p.poi_role <> 'body' "
            "RETURN p.name_key AS k, p.anchors AS a"
        )
        if r["k"]
    }
    def _canon_verified_str(raw) -> str | None:
        if not raw:
            return None
        return json.dumps(json.loads(raw), ensure_ascii=False, sort_keys=True)

    clock_trust = {
        (r["k"], r["g"], _canon_verified_str(r["v"]))
        for r in q(
            "MATCH (p:POI {city_name:$city}) "
            "WHERE p.poi_role IS NULL OR p.poi_role <> 'body' "
            "RETURN p.name_key AS k, p.gated AS g, p.opening_hours_verified AS v"
        )
        if r["k"]
    }
    body_hashes = {
        (r["b"], _normalized_script_body_hash(r["body"] or ""))
        for r in q(
            "MATCH (:POI {city_name:$city})-[:HAS_BEAT]->(b:NarrativeBeat) "
            "WHERE b.beat_id IS NOT NULL "
            "RETURN DISTINCT b.beat_id AS b, b.script_body AS body"
        )
    }
    placement = {
        (r["b"], r["sub"], r["trig"])
        for r in q(
            "MATCH (:POI {city_name:$city})-[:HAS_BEAT]->(b:NarrativeBeat) "
            "WHERE b.beat_id IS NOT NULL "
            "RETURN DISTINCT b.beat_id AS b, b.sub_location AS sub, "
            "b.trigger_address AS trig"
        )
    }
    area_names = {r["n"] for r in q("MATCH (a:Area {city_name:$city}) RETURN a.name AS n")}
    p2a = {
        (r["p"], r["a"])
        for r in q(
            "MATCH (p:POI {city_name:$city})-[:WITHIN]->(a:Area {city_name:$city}) "
            "RETURN p.name AS p, a.name AS a"
        )
    }
    return {
        "poi_keys": poi_keys,
        "footprints": footprints,
        "anchors": anchors,
        "clock_trust": clock_trust,
        "beat_ids": beat_ids,
        "body_hashes": body_hashes,
        "placement": placement,
        "area_names": area_names,
        "p2a": p2a,
    }


def _cmp(
    label: str, exp: set, act: set, drift: list, sample=lambda x: x, *, warn_only=False
) -> None:
    """``warn_only`` (the cloud property lanes): the divergence is printed in
    full but does not fail parity. The cloud graph legitimately LAGS the repo
    between deliberate deploys, so a property behind on Aura is staleness on a
    documented cadence, not silent drift — while an ID-set divergence is
    corruption on any target and always fails."""
    missing, extra = exp - act, act - exp
    if not (missing or extra):
        print(f"    [OK  ] {label}: repo={len(exp)} db={len(act)}")
        return
    status = "STALE" if warn_only else "DRIFT"
    print(f"    [{status}] {label}: repo={len(exp)} db={len(act)}", end="")
    print(f"  (missing {len(missing)}, extra {len(extra)})")
    for m in sorted(missing, key=lambda x: str(sample(x)))[:5]:
        print(f"        - missing: {sample(m)}")
    for e in sorted(extra, key=lambda x: str(sample(x)))[:5]:
        print(f"        + extra:   {sample(e)}")
    if warn_only:
        print("        (cloud lags the repo until the next deliberate deploy)")
    else:
        drift.append(f"{label}: -{len(missing)}/+{len(extra)}")


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else None
    cities = [only] if only else _cities()
    d = create_driver()
    db = get_database()
    # Label by the real URI host, not get_database() truthiness (NEO4J_DATABASE is
    # set to 'neo4j' locally too, so it can't distinguish local vs cloud).
    host = urlparse(os.getenv("NEO4J_URI", "")).hostname or ""
    is_local = host in ("localhost", "127.0.0.1", "::1")
    label = "local" if is_local else f"cloud ({host})"
    print(f"\n{'=' * 66}\n  DB PARITY — repo (source of truth) vs [{label}]\n{'=' * 66}")
    registry = load_registry()
    total_drift: dict[str, list] = {}
    session_kwargs = {"database": db}
    if not is_local:
        # Aura parity is structurally read-only even if the configured service
        # account has broader privileges. No pytest fixture ever receives Aura
        # credentials, and this session is routed in READ mode.
        session_kwargs["default_access_mode"] = READ_ACCESS
    with d.session(**session_kwargs) as s:
        for slug in cities:
            # On a CLOUD target, a city onboarded + uploaded LOCALLY but not yet
            # deployed to Aura (cloud_deployed:false) is legitimately absent from
            # the cloud graph — skip it so it does not read as drift and turn
            # cloud parity RED. Local targets check every city as before.
            if not is_local and not registry.get(slug, {}).get("cloud_deployed", True):
                print(f"\n  {slug}:")
                print("    [N/A ] local-only (cloud_deployed:false)—outside the Aura contract")
                continue
            exp, act = _expected(slug), _actual(s, slug)
            print(f"\n  {slug}:")
            drift: list = []
            _cmp("POIs (name_key)", exp["poi_keys"], act["poi_keys"], drift)
            _cmp(
                "footprints (key, radius, role)",
                exp["footprints"],
                act["footprints"],
                drift,
                sample=lambda t: f"{t[0]} r={t[1]} role={t[2]}",
                warn_only=not is_local,
            )
            _cmp(
                "anchors (key, reviewed set)",
                exp["anchors"],
                act["anchors"],
                drift,
                sample=lambda t: t[0],
                warn_only=not is_local,
            )
            _cmp(
                "clock trust (key, gated, verified)",
                exp["clock_trust"],
                act["clock_trust"],
                drift,
                sample=lambda t: f"{t[0]} gated={t[1]} verified={'yes' if t[2] else 'no'}",
                warn_only=not is_local,
            )
            _cmp("beats (beat_id)", exp["beat_ids"], act["beat_ids"], drift)
            _cmp(
                "beat bodies (id, body hash)",
                exp["body_hashes"],
                act["body_hashes"],
                drift,
                sample=lambda t: t[0],
                warn_only=not is_local,
            )
            _cmp(
                "beat placement (id, sub, trigger)",
                exp["placement"],
                act["placement"],
                drift,
                sample=lambda t: t[0],
                warn_only=not is_local,
            )
            _cmp("areas (name)", exp["area_names"], act["area_names"], drift)
            _cmp(
                "POI->Area edges",
                exp["p2a"],
                act["p2a"],
                drift,
                sample=lambda t: f"{t[0]} -> {t[1]}",
            )
            if exp["blocked"] or exp["unlinkable"]:
                print(
                    f"    [warn] repo has {exp['blocked']} blocked + {len(exp['unlinkable'])} "
                    f"unlinkable beats (correctly absent from the graph)"
                )
            if drift:
                total_drift[slug] = drift
    d.close()
    print(f"\n{'=' * 66}")
    if total_drift:
        print("  PARITY FAIL — drift detected:")
        for slug, ds in total_drift.items():
            print(f"    {slug}: {', '.join(ds)}")
        print(f"{'=' * 66}\n")
        return 1
    print("  PARITY OK — every city matches the repo source of truth.")
    print(f"{'=' * 66}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
