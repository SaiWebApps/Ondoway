#!/usr/bin/env python3
"""Backfill the canonical `name_key` dedup property onto existing POI nodes (defect 3).

Duplicate POIs were fixed by making POI creation MERGE on a canonical `name_key`
(``src/api/crud/nodes.create_node`` → ``MERGE (n:POI {name_key, city_name})``,
where ``name_key = canonical_name_key(name)``). But POIs created BEFORE that
change have no ``name_key`` property, so a re-upload or edit of an existing POI
won't dedup against them — the ``{name_key, city_name}`` MERGE cannot match a
node that lacks ``name_key`` → a duplicate node is forked. This migration
backfills ``name_key`` onto those existing nodes so future MERGEs land on them.

What it does (and does NOT do):
  - DRY-RUN by default (no writes). Reports, per city_name:
      * total POIs, how many are missing name_key (or have a stale value),
      * COLLISION CLUSTERS — sets of ≥2 existing POIs that would map to the
        same (name_key, city_name). These are pre-existing casing/whitespace
        duplicates the old (name, city_name) MERGE key never caught. They are
        SURFACED for manual review under the project's two-source / user-review
        guardrails — this script NEVER auto-merges or deletes them.
  - ``--apply`` SETs ``n.name_key = canonical_name_key(n.name)`` on every POI
    whose name_key is missing or differs from the canonical form. It is a pure
    property SET: it MERGES nothing and DELETES nothing. Re-running is a no-op
    (idempotent) once every node already carries its canonical name_key.

Uses ``canonical_name_key`` imported from ``src.api.models.nodes`` so the key is
computed identically to ``create_node`` — a single source of truth.

Safety (mirrors src/main._assert_local_target and scripts/upload_paris):
  - Refuses a non-localhost NEO4J_URI unless ``--allow-cloud`` is passed
    explicitly. The guard fires BEFORE any driver is created, so it protects
    even when Aura is paused/unreachable. Safe by default.

Usage:
    python scripts/backfill_name_key.py                    # dry-run (default)
    python scripts/backfill_name_key.py --apply            # write name_key (local)
    python scripts/backfill_name_key.py --apply --allow-cloud   # deliberate cloud run
    python scripts/backfill_name_key.py --city-name paris  # scope to one city
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

# Top-level project import — reuse the canonical dedup-key function and the
# driver factory so this migration computes name_key exactly as create_node does.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.api.models.nodes import canonical_name_key
from src.connection import abort_on_connection_error, create_driver, get_database

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _assert_local_target(allow_cloud: bool) -> None:
    """Refuse a non-local (Aura) target unless --allow-cloud is passed.

    Fires BEFORE any driver/session is opened (identical pattern to
    ``src/main._assert_local_target`` and ``scripts/upload_paris``), so an
    accidental run while ``.env`` is in cloud mode never reaches ``create_driver``
    — the guard protects even when Aura is unreachable/paused. A deliberate cloud
    run is still possible by typing ``--allow-cloud`` explicitly.
    """
    if allow_cloud:
        return
    uri = os.getenv("NEO4J_URI", "")
    host = urlparse(uri).hostname or ""
    if host not in _LOCAL_HOSTS:
        raise SystemExit(
            f"REFUSING a name_key backfill against non-local NEO4J_URI={uri!r} "
            f"(host={host!r}). This migration writes to every POI missing name_key; "
            f"point NEO4J_URI at the local database first (the local Make targets "
            f"do), or pass --allow-cloud to deliberately target Aura."
        )


def _fetch_pois(session, city_name: str | None) -> list[dict]:
    """Return [{id, name, name_key, city_name}] for POIs (optionally one city).

    ``name`` can in principle be missing on a malformed node; such rows are
    surfaced by the caller (they cannot be backfilled without a source name).
    """
    if city_name is not None:
        result = session.run(
            "MATCH (p:POI) WHERE p.city_name = $city_name "
            "RETURN p.id AS id, p.name AS name, p.name_key AS name_key, "
            "p.city_name AS city_name",
            city_name=city_name,
        )
    else:
        result = session.run(
            "MATCH (p:POI) "
            "RETURN p.id AS id, p.name AS name, p.name_key AS name_key, "
            "p.city_name AS city_name"
        )
    return [dict(r) for r in result]


def plan_backfill(pois: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split POIs into (to_set, no_name).

    ``to_set`` — POIs whose name_key is missing or differs from the canonical
    form of their current ``name`` (each carries the target ``name_key``).
    ``no_name`` — POIs with no usable ``name`` (cannot derive a key; reported,
    never written). Nodes already carrying their canonical name_key are omitted
    from both lists (idempotency: nothing to do).
    """
    to_set: list[dict] = []
    no_name: list[dict] = []
    for poi in pois:
        name = poi.get("name")
        if not name or not str(name).strip():
            no_name.append(poi)
            continue
        target = canonical_name_key(str(name))
        if poi.get("name_key") != target:
            to_set.append({**poi, "target_name_key": target})
    return to_set, no_name


def detect_collisions(pois: list[dict]) -> list[list[dict]]:
    """Return clusters of ≥2 POIs that map to the same (name_key, city_name).

    Uses the canonical key derived from each POI's ``name`` (not the stored
    name_key, which may be missing/stale pre-backfill) so it catches the exact
    duplicates the old (name, city_name) MERGE key let through. Each cluster is
    a list of the colliding POI dicts. POIs without a usable name are ignored
    (they are reported separately). Clusters are sorted by (city, key) and the
    members within a cluster by id for stable, reviewable output.
    """
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for poi in pois:
        name = poi.get("name")
        if not name or not str(name).strip():
            continue
        key = (canonical_name_key(str(name)), poi.get("city_name") or "")
        buckets[key].append(poi)
    clusters = [
        sorted(members, key=lambda p: p.get("id") or "")
        for members in buckets.values()
        if len(members) > 1
    ]
    clusters.sort(
        key=lambda members: (
            members[0].get("city_name") or "",
            canonical_name_key(str(members[0]["name"])),
        )
    )
    return clusters


def apply_backfill(session, to_set: list[dict]) -> int:
    """SET p.name_key on the planned nodes, matched by id. Returns count set.

    Pure property write: MERGES nothing, DELETES nothing. Matching by ``id``
    (the immutable node identity) means it can never touch a different node than
    the one planned, and re-running with an empty plan is a no-op.
    """
    if not to_set:
        return 0
    params = [{"id": p["id"], "name_key": p["target_name_key"]} for p in to_set]
    result = session.run(
        """
        UNWIND $rows AS row
        MATCH (p:POI {id: row.id})
        SET p.name_key = row.name_key
        RETURN count(p) AS updated
        """,
        rows=params,
    ).single()
    return result["updated"]


def _report_dry_run(pois: list[dict], to_set: list[dict], no_name: list[dict],
                    clusters: list[list[dict]], scope_label: str) -> None:
    """Print the per-city counts, the no-name rows, and the collision clusters."""
    print(f"\nname_key backfill — DRY-RUN ({scope_label})")
    print(f"  POIs scanned:            {len(pois)}")
    print(f"  need name_key set:       {len(to_set)}")
    print(f"  already canonical:       {len(pois) - len(to_set) - len(no_name)}")
    if no_name:
        print(f"  ! POIs with no usable name (cannot backfill): {len(no_name)}")
        for poi in no_name:
            print(f"      id={poi.get('id')!r} city_name={poi.get('city_name')!r}")

    # Per-city breakdown of the pending sets.
    per_city: dict[str, int] = defaultdict(int)
    for poi in to_set:
        per_city[poi.get("city_name") or "<none>"] += 1
    if per_city:
        print("  need-set by city_name:")
        for city, n in sorted(per_city.items()):
            print(f"      {city:<20} {n:>4}")

    if clusters:
        total_dupes = sum(len(c) for c in clusters)
        print(
            f"\n  COLLISION CLUSTERS: {len(clusters)} cluster(s), {total_dupes} POIs "
            f"map to a shared (name_key, city_name)."
        )
        print("  These are pre-existing casing/whitespace duplicates. NOT auto-merged —")
        print("  surfaced for manual review (two-source / user-review guardrails):")
        for cluster in clusters:
            key = canonical_name_key(str(cluster[0]["name"]))
            city = cluster[0].get("city_name") or "<none>"
            print(f"    -- (name_key={key!r}, city_name={city!r}) x {len(cluster)}:")
            for poi in cluster:
                print(f"        id={poi.get('id')!r}  name={poi.get('name')!r}")
    else:
        print("\n  COLLISION CLUSTERS: none — no two POIs share a canonical key.")

    print("\n  DRY-RUN — pass --apply to write name_key (only SETs the property).")


@abort_on_connection_error
def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply", action="store_true",
        help="write name_key (default: dry-run report only)",
    )
    parser.add_argument(
        "--allow-cloud", action="store_true",
        help="permit a non-localhost NEO4J_URI (deliberate Aura run)",
    )
    parser.add_argument(
        "--city-name", default=None,
        help="scope to a single city_name (default: all cities)",
    )
    args = parser.parse_args(argv)

    # Guard fires PRE-CONNECT — before create_driver() touches the network.
    _assert_local_target(args.allow_cloud)

    scope_label = f"city_name={args.city_name!r}" if args.city_name else "all cities"

    driver = create_driver()
    try:
        with driver.session(database=get_database()) as session:
            pois = _fetch_pois(session, args.city_name)
            to_set, no_name = plan_backfill(pois)
            clusters = detect_collisions(pois)

            if not args.apply:
                _report_dry_run(pois, to_set, no_name, clusters, scope_label)
                return 0

            # --apply: SET name_key on the planned nodes only. Never merges/deletes.
            print(f"\nname_key backfill — APPLY ({scope_label})")
            print(f"  POIs scanned:      {len(pois)}")
            print(f"  planning to set:   {len(to_set)}")
            if clusters:
                print(
                    f"  NOTE: {len(clusters)} collision cluster(s) detected. name_key is "
                    f"still set on each member (a pure property write); the duplicates "
                    f"themselves are NOT merged — review them separately (re-run dry-run "
                    f"to list)."
                )
            updated = apply_backfill(session, to_set)
            print(f"  SET name_key on:   {updated} POI node(s)")

            # Verify idempotency in-process: re-plan against the now-written graph.
            after = _fetch_pois(session, args.city_name)
            remaining, _ = plan_backfill(after)
            if remaining:
                print(
                    f"  FAIL: {len(remaining)} POI(s) still missing/stale name_key "
                    f"after apply.",
                    file=sys.stderr,
                )
                return 1
            print("  verified: 0 POIs missing/stale name_key (idempotent).")
            return 0
    finally:
        driver.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
