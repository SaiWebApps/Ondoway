#!/usr/bin/env python3
"""Backfill poi_role for the POIs that have a null role in poi-raw.json.

The roles were re-classified from each POI's own source text/beats, independently
reviewed, reconciled, and
recorded in a committed artifact (data/paris/poi_role_backfill.json). This script
APPLIES that reviewed artifact to the canonical poi-raw.json (and, with --neo4j,
to the dev graph). It never invents a role and never touches a POI that already
has one.

Safety:
- ROOT is resolved from this file's repo, never a hardcoded user path.
- Each artifact entry is matched by BOTH list index and exact name; a mismatch
  aborts (guards against poi-raw.json being reordered).
- Only POIs whose current poi_role is null are changed. Re-running is idempotent:
  a POI already set to its target role is skipped; a conflicting non-null role
  aborts (so the script can never silently overwrite a real classification).

Usage:
    python scripts/backfill_poi_role.py             # dry-run (default): report only
    python scripts/backfill_poi_role.py --apply      # write poi-raw.json
    python scripts/backfill_poi_role.py --apply --neo4j   # also SET poi_role in Neo4j
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POI_RAW = ROOT / "data" / "paris" / "poi-raw.json"
ARTIFACT = ROOT / "data" / "paris" / "poi_role_backfill.json"
VALID_ROLES = {"stop", "setting", "walk_by_only"}


def load_json(path: Path):
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def plan_changes(pois: list[dict], artifact: list[dict]) -> tuple[list[dict], list[str]]:
    """Return (changes, errors). A change is {idx, name, role, reasoning}."""
    changes: list[dict] = []
    errors: list[str] = []
    for entry in artifact:
        idx = entry["idx"]
        name = entry["name"]
        role = entry["poi_role"]
        reasoning = entry.get("_poi_role_reasoning", "")
        if role not in VALID_ROLES:
            errors.append(f"idx {idx} ({name}): invalid role {role!r}")
            continue
        if not (0 <= idx < len(pois)):
            errors.append(f"idx {idx} ({name}): out of range")
            continue
        poi = pois[idx]
        if poi.get("name") != name:
            errors.append(
                f"idx {idx}: name mismatch — artifact {name!r} vs poi-raw {poi.get('name')!r}"
            )
            continue
        current = poi.get("poi_role")
        if current == role:
            continue  # idempotent: already applied
        if current is not None:
            errors.append(
                f"idx {idx} ({name}): refusing to overwrite existing role {current!r} with {role!r}"
            )
            continue
        changes.append({"idx": idx, "name": name, "role": role, "reasoning": reasoning})
    return changes, errors


def apply_to_pois(pois: list[dict], changes: list[dict]) -> None:
    for ch in changes:
        poi = pois[ch["idx"]]
        poi["poi_role"] = ch["role"]
        poi["_poi_role_reasoning"] = ch["reasoning"]


def apply_to_neo4j(changes: list[dict]) -> int:
    """SET poi_role on existing Paris POI nodes in the dev graph, matched by name.
    Self-diagnosing: prints total POIs, null poi_role before/after, and how many of
    the requested changes matched a node. Returns the post-apply null count."""
    from src.connection import create_driver, get_database  # local import; DB optional

    driver = create_driver()
    try:
        with driver.session(database=get_database()) as session:
            def null_count() -> int:
                rec = session.run(
                    "MATCH (p:POI) WHERE coalesce(p.city_slug, 'paris') = 'paris' "
                    "AND p.poi_role IS NULL RETURN count(p) AS n"
                ).single()
                return rec["n"] if rec else 0

            total = session.run(
                "MATCH (p:POI) WHERE coalesce(p.city_slug, 'paris') = 'paris' RETURN count(p) AS n"
            ).single()
            total_n = total["n"] if total else 0
            before = null_count()
            matched = 0
            for ch in changes:
                rec = session.run(
                    """
                    MATCH (p:POI {name: $name})
                    WHERE coalesce(p.city_slug, 'paris') = 'paris'
                    SET p.poi_role = $role
                    RETURN count(p) AS n
                    """,
                    name=ch["name"],
                    role=ch["role"],
                ).single()
                matched += rec["n"] if rec else 0
            after = null_count()
            print(
                f"Neo4j: {total_n} Paris POIs; null poi_role {before} -> {after}; "
                f"{matched} node(s) matched/SET from the {len(changes)} requested changes"
            )
            return after
    finally:
        driver.close()


def clear_neo4j(artifact: list[dict]) -> int:
    """Revert: SET poi_role = null for the artifact POIs in the dev graph (by name).
    Returns the number of nodes cleared. Undoes a --neo4j apply that polluted the dev
    DB (e.g. when a golden test reads live poi_role and a re-baseline is still pending)."""
    from src.connection import create_driver, get_database

    cleared = 0
    driver = create_driver()
    try:
        with driver.session(database=get_database()) as session:
            for entry in artifact:
                rec = session.run(
                    "MATCH (p:POI {name: $name}) "
                    "WHERE coalesce(p.city_slug, 'paris') = 'paris' "
                    "SET p.poi_role = null RETURN count(p) AS n",
                    name=entry["name"],
                ).single()
                cleared += rec["n"] if rec else 0
    finally:
        driver.close()
    return cleared


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write poi-raw.json (default: dry-run)")
    ap.add_argument("--neo4j", action="store_true", help="also SET poi_role in the dev graph")
    ap.add_argument(
        "--neo4j-clear",
        action="store_true",
        help="revert: SET poi_role=null in the dev graph for the artifact POIs (undo --neo4j)",
    )
    args = ap.parse_args()

    pois = load_json(POI_RAW)
    artifact = load_json(ARTIFACT)

    if args.neo4j_clear:
        n = clear_neo4j(artifact)
        print(f"Neo4j: cleared poi_role on {n} artifact POI node(s)")
        return 0

    changes, errors = plan_changes(pois, artifact)

    if errors:
        print(f"ABORT — {len(errors)} error(s); no changes written:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    dist = Counter(c["role"] for c in changes)
    nulls_before = sum(1 for p in pois if p.get("poi_role") is None)
    print(
        f"poi-raw.json: {len(pois)} POIs, {nulls_before} null before; "
        f"{len(changes)} to backfill -> {dict(dist)}"
    )

    if not args.apply:
        print("DRY-RUN — pass --apply to write. Sample:")
        for c in changes[:5]:
            print(f"  idx {c['idx']:>3} {c['name'][:40]:<40} -> {c['role']}")
        return 0

    apply_to_pois(pois, changes)
    nulls_after = sum(1 for p in pois if p.get("poi_role") is None)
    with POI_RAW.open("w", encoding="utf-8") as fh:
        json.dump(pois, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"WROTE {POI_RAW} — null poi_role now {nulls_after}")

    if args.neo4j:
        # Neo4j is a separate store from poi-raw.json, so drive it from the artifact
        # directly (all entries, idempotent SET) rather than the poi-raw diff — which
        # is empty once poi-raw.json has already been backfilled.
        neo4j_targets = [{"name": e["name"], "role": e["poi_role"]} for e in artifact]
        remaining = apply_to_neo4j(neo4j_targets)
        if remaining:
            print(
                f"WARNING: {remaining} Paris POI(s) still have a null poi_role in Neo4j",
                file=sys.stderr,
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
