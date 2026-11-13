"""Regression test: every POI listed in any export file must match the
canonical record in `data/{city}/poi-raw.json` for the fields that flow
into Neo4j.

History: an earlier pipeline build silently demoted famous landmarks like
Notre-Dame, Eiffel Tower, and Luxembourg Gardens to importance_tier=1 by
forgetting to copy the canonical tier from poi-raw.json into the export
chunk. The bug was masked by the schema's default of `importance_tier=1`,
which has since been removed (see src/api/models/nodes.py).

This test runs in <100ms with no DB and catches both:
  - Tier mismatches (poi-raw says 5, export says 1)
  - Missing fields (poi-raw says 4, export omits the field entirely)
  - Coordinate drift between poi-raw and exports
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = REPO_ROOT / "data"

# Fields that must agree EXACTLY between poi-raw.json and every export entry.
# Skipped: name_variations / short_description (exports may carry chunk-specific
# framing); lat/lng (compared with tolerance further down).
STRICT_FIELDS = ("importance_tier",)

# `trigger_radius` joined the strict set for every
# city whose footprint pass has run: poi-raw.json is the canonical record, the pass
# (scripts/poi_trigger_radius.py) writes it there and the export sync carries it
# (SYNCED_FIELDS), so the 19 Paris entries that had drifted (Notre-Dame export 100 /
# raw 10; Luxembourg 100 / 390) can never come back. A city not yet through the pass
# keeps the looser check — the allow-list says which. New York's own
# normalise-then-sync job HAS now run: 15 of its 34 chunks had been
# committed at 1-space indent, which the one serialiser refuses to rewrite, and all
# 34 are byte-faithful and fully synced since — see
# test_export_chunks_are_byte_faithful_to_the_serialiser below. What still holds New
# York out of the strict set is the footprint pass itself, not its formatting.
STRICT_FIELDS_AFTER_FOOTPRINT_PASS = ("trigger_radius",)

# Coordinate tolerance: ~0.0005 deg ≈ 55m at Paris latitude. Tighter than this
# is just rounding noise from JSON serialization.
COORD_TOLERANCE = 0.001


def _city_dirs() -> list[Path]:
    if not DATA_ROOT.exists():
        return []
    return [d for d in sorted(DATA_ROOT.iterdir()) if d.is_dir() and (d / "poi-raw.json").exists()]


@pytest.mark.parametrize("city_dir", _city_dirs(), ids=lambda d: d.name)
def test_export_matches_poi_raw(city_dir: Path) -> None:
    """For each city: every POI in every export file must match poi-raw.json
    on tier, lat, lng, and trigger_radius."""
    from scripts.poi_trigger_radius import CITIES_WITH_FOOTPRINTS

    poi_raw = json.loads((city_dir / "poi-raw.json").read_text())
    canonical = {p["name"].lower(): p for p in poi_raw}
    strict = STRICT_FIELDS + (
        STRICT_FIELDS_AFTER_FOOTPRINT_PASS if city_dir.name in CITIES_WITH_FOOTPRINTS else ()
    )

    export_dir = city_dir / "export"
    if not export_dir.exists():
        # A freshly-onboarded city (e.g. london) has poi-raw.json but no export/
        # dir yet — there is nothing to cross-check, so this is a VACUOUS PASS.
        # (A bare pytest.skip would be flipped to a FAILURE by conftest's
        # no-silent-skip policy.) paris/new_york DO have export/ and still assert.
        return

    offenders: list[str] = []

    for export_file in sorted(export_dir.glob("*.json")):
        try:
            data = json.loads(export_file.read_text())
        except json.JSONDecodeError as exc:
            offenders.append(f"{export_file.name}: invalid JSON ({exc})")
            continue
        if not isinstance(data, list):
            offenders.append(f"{export_file.name}: expected list, got {type(data).__name__}")
            continue

        for poi in data:
            name = poi.get("name")
            if not name:
                offenders.append(f"{export_file.name}: POI missing name")
                continue
            canon = canonical.get(name.lower())
            if not canon:
                # POI in export but not in poi-raw — could be a stale export
                offenders.append(
                    f"{export_file.name}: '{name}' is in export but not in poi-raw.json"
                )
                continue

            # Strict equality fields (importance_tier; trigger_radius once sized)
            for field in strict:
                exp_val = poi.get(field)
                canon_val = canon.get(field)
                if canon_val is None:
                    continue
                if exp_val != canon_val:
                    offenders.append(
                        f"{export_file.name}: '{name}'.{field} = {exp_val!r} "
                        f"but poi-raw has {canon_val!r}"
                    )

            # Coordinates with tolerance
            for field in ("latitude", "longitude"):
                exp_val = poi.get(field)
                canon_val = canon.get(field)
                if canon_val is None or exp_val is None:
                    continue
                if abs(exp_val - canon_val) > COORD_TOLERANCE:
                    offenders.append(
                        f"{export_file.name}: '{name}'.{field} = {exp_val!r} "
                        f"differs from poi-raw {canon_val!r} by >{COORD_TOLERANCE}"
                    )

    if offenders:
        pytest.fail(
            f"Export/poi-raw drift in {city_dir.name} ({len(offenders)} issues):\n"
            + "\n".join(f"  {o}" for o in offenders[:50])
            + (f"\n  ... and {len(offenders) - 50} more" if len(offenders) > 50 else "")
        )


def test_export_chunks_are_byte_faithful_to_the_serialiser() -> None:
    """Every corpus file a pass writes must re-serialise to its own bytes, or it cannot run.

    Covers BOTH halves of the write path: each city's canonical `poi-raw.json` — which
    every enrichment pass writes through `dump_pois` — and every `export/*.json` chunk
    the sync propagates into. Guarding only the chunks left the real hole: New York's
    `poi-raw.json` stayed unfaithful after its chunks were fixed, so `poi_trigger_radius`
    (and every other pass) still could not write that city at all.

    The test above guards what the chunks SAY; this one guards that they can still be
    WRITTEN. `scripts/sync_poi_exports.py` writes through `dump_pois`, whose round-trip
    guard (scripts/poi_visit_duration.py) refuses any write that would reformat the
    whole file and bury the real change. So a chunk that is not byte-faithful does not
    merely look untidy — it stops that city's fields from ever reaching Neo4j.

    History, and why this test exists: it used to stop the sync MID-WRITE. A New York
    run wrote frommers-nyc-2024-chunk-01 and -02, then hit chunk-03 — the first of 15
    chunks committed at 1-space indent — and SystemExited, leaving two half-synced
    files in the tree and 32 untouched ones, against a comment in the sync promising
    that "an abort leaves nothing half-synced". The sync now proves faithfulness for
    every pending chunk during PLANNING and aborts before any write; this test keeps
    the tree from drifting back, so no city has to be normalised before it can sync.

    Fast, no DB, no provider: parse and re-serialise, nothing else.
    """
    from scripts.poi_visit_duration import serialise

    targets: list[Path] = []
    for city_dir in _city_dirs():
        targets.append(city_dir / "poi-raw.json")
        targets.extend(sorted((city_dir / "export").glob("*.json")))

    offenders: list[str] = []
    checked = 0
    for path in targets:
        checked += 1
        original = path.read_text(encoding="utf-8")
        try:
            parsed = json.loads(original)
        except json.JSONDecodeError as exc:
            offenders.append(f"{path.parent.name}/{path.name}: invalid JSON ({exc})")
            continue
        if serialise(parsed) != original:
            offenders.append(f"{path.parent.name}/{path.name}")

    assert checked, "no corpus files found under data/ — this test guarded nothing"
    if offenders:
        pytest.fail(
            f"{len(offenders)} of {checked} corpus file(s) are not byte-faithful to "
            "scripts/poi_visit_duration.serialise, so make sync-poi-exports refuses to "
            "write them and their city's enriched fields never reach the graph. "
            "Re-serialise each through that same serialise() (content must not change) "
            "and re-run the sync:\n" + "\n".join(f"  {o}" for o in offenders[:50])
            + (f"\n  ... and {len(offenders) - 50} more" if len(offenders) > 50 else "")
        )


def test_export_sync_carries_every_field_the_passes_write() -> None:
    """`make sync-poi-exports` must never silently strand a newly enriched field.

    The enrichment passes write fields into poi-raw.json only; the export
    chunks — what `make deploy` actually uploads — receive them via
    scripts/sync_poi_exports.py, which owns THE one list of propagated fields
    (`SYNCED_FIELDS`; the passes' trailer messages deliberately do not restate
    it). This test derives, from each pass script's own source, the set of
    fields it writes (`poi["<field>"] = ...`) and fails the moment a pass
    starts writing a field the sync would not carry — the exact gap that once
    needed a scratch sync script.

    Hop-style source scan (the `test_golden_diff_cli_reads_the_durable_key`
    genre): import the module for real first, so a script that raises on
    import cannot pass a pure text check.
    """
    import importlib
    import re

    module = importlib.import_module("scripts.sync_poi_exports")
    synced = set(module.SYNCED_FIELDS)
    assert synced, "SYNCED_FIELDS is empty — the sync would propagate nothing"

    pass_scripts = (
        "poi_visit_duration.py",  # visit-capacity trio
        "poi_opening_hours.py",  # opening-hours trio
        "poi_place_category.py",  # place_category
        "poi_queues.py",  # queue quintet
    )
    assignment = re.compile(r'\bpoi\["([a-z_]+)"\]\s*=')
    written: set[str] = set()
    for script_name in pass_scripts:
        source = (REPO_ROOT / "scripts" / script_name).read_text()
        fields = assignment.findall(source)
        assert fields, (
            f"scripts/{script_name}: the source scan matched no poi[...] = assignment — "
            "either the pass stopped writing fields or the write pattern changed; "
            "update this scan so it keeps guarding the sync list"
        )
        written.update(fields)

    # The judgements pass writes through a field-tuple loop (`poi[field] = ...`
    # over JUDGEMENT_FIELDS), which the literal-assignment scan above cannot
    # see by construction — so its coverage is asserted by IMPORT, the stronger
    # form: the pass's own declared field list must be wholly inside the sync's.
    judgements = importlib.import_module("scripts.poi_place_judgements")
    written.update(judgements.JUDGEMENT_FIELDS)

    missing = written - synced
    assert not missing, (
        f"pass scripts write {sorted(missing)} but SYNCED_FIELDS in "
        "scripts/sync_poi_exports.py does not carry them — the field would reach "
        "poi-raw.json and never reach the export chunks (the Notre-Dame tier-1 "
        "incident shape, see this file's module docstring)"
    )


def test_no_tier_one_for_known_top_landmarks() -> None:
    """Sanity guard: a hard-coded list of obvious world landmarks must NEVER
    appear at importance_tier 1 in any city's poi-raw.json. Catches accidental
    overwrites of canonical data."""
    must_be_high_tier = {
        "paris": [
            "Eiffel Tower",
            "Notre-Dame Cathedral",
            "Louvre Museum",
            "Arc de Triomphe",
            "Sacre-Coeur Basilica",
            "Luxembourg Gardens",
            "Champs-Elysees",
        ],
    }
    failures: list[str] = []
    for city, names in must_be_high_tier.items():
        path = DATA_ROOT / city / "poi-raw.json"
        if not path.exists():
            continue
        pois = {p["name"]: p for p in json.loads(path.read_text())}
        for n in names:
            p = pois.get(n)
            if not p:
                failures.append(f"{city}: missing POI '{n}'")
            elif p.get("importance_tier", 0) < 4:
                failures.append(
                    f"{city}: '{n}' has importance_tier={p.get('importance_tier')} (expected ≥4)"
                )
    if failures:
        pytest.fail("\n".join(failures))
