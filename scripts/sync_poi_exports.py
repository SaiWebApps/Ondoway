"""Propagate enriched POI fields from poi-raw.json into every export chunk — one command.

EXTENDS, per CLAUDE.md §1.7 (the third manual copy becomes the template). This same
block was performed by hand three times before it became a script:

  1. the hand-sync `.claude/commands/poi-visit-duration.md` mandates under
     "MANDATORY — SYNC THE EXPORT FILES" ("the step that gets skipped, and skipping
     it makes the whole pass invisible");
  2. the scratch script W1.8 wrote to push the opening-hours trio and
     ``place_category`` into the 48 Paris chunks (1,384 fields), deleted with its
     spec folder;
  3. the identical "NEXT, AND MANDATORY: sync data/{city}/export/*.json" warning all
     three pass scripts printed — each now names ``make sync-poi-exports`` instead.

WHY THE SYNC EXISTS AT ALL. ``data/{slug}/poi-raw.json`` is the canonical POI source
per city, and it is what the enrichment passes write. But ``data/{slug}/export/*.json``
is what `/upload` reads into Neo4j — a field that exists only in poi-raw never reaches
the graph (the Notre-Dame tier-1 incident; ``tests/test_export_consistency.py`` guards
the RESULT, and this script is the one way the fields travel).

THE SCRIPT OWNS THE FIELD LIST. ``SYNCED_FIELDS`` below is the single statement of
which enriched fields flow poi-raw -> export; the pass trailers deliberately do not
restate it, and ``tests/test_export_consistency.py`` fails when a pass starts writing
a field this list does not carry.

NULL IS A STATEMENT (W1.8's ledger: "nulls propagated deliberately"). A null
``visit_seconds_inside`` means "no interior"; a null ``opening_hours`` means "not
gated". So a null — and an absent field, for a pass that has not run on this city
yet — propagates as an explicit null rather than being skipped.

BYTE-SAFE, three ways. A chunk whose parsed content the sync would not change is never
rewritten, so a formatting-only difference can never masquerade as a data change. A
chunk that does change is written through ``dump_pois`` — imported from
``scripts/poi_visit_duration.py``, never copied — whose round-trip guard refuses any
write that would reformat the whole file and bury the real diff. And that same guard is
asked during PLANNING, of every chunk that would be written, so an unfaithful file
stops the run before the first write instead of after the third — the New York abort
that left two chunks half-synced.

POIs are matched canonical -> chunk by lower-cased ``name``, the same key
``tests/test_export_consistency.py`` has always matched on (there is no id field in
either file). An export POI with no canonical match aborts the run before any write:
that is stale-export drift, the RESULT guard's finding, not something to sync around.

``--check`` writes nothing and exits nonzero when anything would change — the proof,
before `make deploy`, that the chunks carry what the passes produced.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from scripts.poi_visit_duration import dump_pois, load_pois, serialise

ROOT = Path(__file__).resolve().parent.parent

#: THE one list of enriched fields that flow poi-raw -> export chunks. One fact, one
#: place (CLAUDE.md §1.7): the pass scripts' trailer messages name the make target and
#: never restate this list. Ordered as the passes landed.
SYNCED_FIELDS: tuple[str, ...] = (
    # Visit-capacity trio — scripts/poi_visit_duration.py.
    "visit_seconds_inside",
    "visit_basis",
    "typical_duration_min",
    # Opening-hours trio — scripts/poi_opening_hours.py.
    "opening_hours",
    "opening_hours_source",
    "opening_hours_basis",
    # The trust half (Docs/adr/0003): the explicit door verdict from the same
    # pass, and the verification record the ladder's review writes.
    "gated",
    "opening_hours_verified",
    # Place category — scripts/poi_place_category.py.
    "place_category",
    # Place judgements — redesign S2.6's pass, landing this same phase; listed ahead
    # of it so the sync never lags the pass (plan step S2.9).
    "children_can_run",
    "sit_and_talk",
    "good_after_dark",
    "judgement_basis",
    # Queue pass — scripts/poi_queues.py (redesign data row 6.5, plan W3.1);
    # registered with the pass so the sync never lags it, the S2.9 precedent.
    "queue_class",
    "queue_minutes_peak",
    "queue_minutes_offpeak",
    "queue_peak_hours",
    "queue_basis",
    # The footprint — scripts/poi_trigger_radius.py (Phase 7 S7.4, design §5.6 C7).
    # Registered with the pass, the S2.9 precedent; this is the field the
    # tests/test_export_consistency.py TODO had recorded as drifting (19 entries).
    "trigger_radius",
    # The reviewed anchors of a marquee (Phase 7 S7.7 B, design §5.6 segments) —
    # placed by hand on poi-raw.json; the graph reads them through the upload.
    "anchors",
)


def sync_chunk(
    chunk: list[dict[str, Any]],
    canonical: dict[str, dict[str, Any]],
    chunk_name: str,
    unknown: list[str],
) -> int:
    """Copy every synced field canonical -> chunk POI, in place. Returns the number
    of field-level changes; unmatched POI names are appended to ``unknown``."""
    changes = 0
    for poi in chunk:
        name = poi.get("name")
        canon = canonical.get(str(name).lower()) if name else None
        if canon is None:
            unknown.append(f"{chunk_name}: {name!r} is not in poi-raw.json")
            continue
        for field in SYNCED_FIELDS:
            value = canon.get(field)  # absent propagates as null: null is a statement
            if field not in poi or poi[field] != value:
                poi[field] = value
                changes += 1
    return changes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--slug", default="paris", help="City slug (default: paris)")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report what would change and write nothing; exit nonzero on any pending change.",
    )
    args = parser.parse_args(argv)

    raw_path = ROOT / "data" / args.slug / "poi-raw.json"
    if not raw_path.exists():
        raise SystemExit(f"✗ no POI file at {raw_path}")
    export_dir = ROOT / "data" / args.slug / "export"
    chunk_paths = sorted(export_dir.glob("*.json")) if export_dir.exists() else []
    if not chunk_paths:
        raise SystemExit(f"✗ no export chunks under {export_dir} — nothing to sync into")

    pois, _ = load_pois(raw_path)
    canonical = {p["name"].lower(): p for p in pois}

    # Plan every chunk before writing any, so an abort leaves nothing half-synced.
    # That means EVERY reason to abort has to be found here, in the planning phase.
    # ``dump_pois``'s round-trip guard is one of them, and it used to be discovered
    # mid-write instead: a New York run wrote chunks 01 and 02, then SystemExited on
    # chunk 03 — the first one committed at a different indent — and left two
    # half-synced files behind. So faithfulness is proven here, for every chunk that
    # would be written, using the same ``serialise`` ``dump_pois`` writes through.
    pending: list[tuple[Path, list[dict[str, Any]], str, int]] = []
    unknown: list[str] = []
    unfaithful: list[str] = []
    for chunk_path in chunk_paths:
        chunk, original = load_pois(chunk_path)
        # Before sync_chunk mutates it, `chunk` IS the file's own parse — so this is
        # exactly dump_pois's guard, asked early enough to stop the whole run.
        round_trips = serialise(chunk) == original
        changes = sync_chunk(chunk, canonical, chunk_path.name, unknown)
        if changes:
            pending.append((chunk_path, chunk, original, changes))
            if not round_trips:
                unfaithful.append(chunk_path.name)

    if unknown:
        print(
            f"✗ {len(unknown)} export POI(s) have no match in {raw_path.relative_to(ROOT)}; "
            "syncing nothing — fix the stale export first "
            "(tests/test_export_consistency.py fails on exactly this):",
            file=sys.stderr,
        )
        for line in unknown:
            print(f"    {line}", file=sys.stderr)
        return 1

    if unfaithful:
        print(
            f"✗ {len(unfaithful)} of the {len(pending)} chunk(s) that need syncing are not "
            "byte-faithful to the serialiser, so writing them would reformat the whole file "
            "and bury the real change; syncing nothing — normalise those files first by "
            "re-serialising each through scripts/poi_visit_duration.serialise, which changes "
            "formatting only (tests/test_export_consistency.py fails on exactly this):",
            file=sys.stderr,
        )
        for name in unfaithful:
            print(f"    {name}", file=sys.stderr)
        return 1

    total = sum(changes for _, _, _, changes in pending)
    if not pending:
        print(
            f"✓ {len(chunk_paths)} chunk(s) under {export_dir.relative_to(ROOT)} already "
            f"carry poi-raw.json's values for all {len(SYNCED_FIELDS)} synced fields."
        )
        return 0

    verb = "would update" if args.check else "updating"
    for chunk_path, _, _, changes in pending:
        print(f"  {verb} {changes:4d} field(s) in {chunk_path.name}")

    if args.check:
        print(
            f"✗ --check: {total} field(s) across {len(pending)} of {len(chunk_paths)} chunks "
            f"would change. Run: make sync-poi-exports SLUG={args.slug}"
        )
        return 1

    for chunk_path, chunk, original, _ in pending:
        dump_pois(chunk_path, chunk, original)
    print(
        f"✓ wrote {total} field(s) across {len(pending)} of {len(chunk_paths)} chunks "
        f"in {export_dir.relative_to(ROOT)}"
    )
    print("  Prove it: make test-file FILE=tests/test_export_consistency.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
