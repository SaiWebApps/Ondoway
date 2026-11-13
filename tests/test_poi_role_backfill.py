"""Pure (no-DB) regression test for the poi_role backfill.

Asserts the canonical poi-raw.json has no null poi_role after the backfill, that
the reviewed artifact was applied faithfully, that the over-weighting bug is
corrected (nulls were coerced to full-weight "stop" at runtime — selection.py:334
`poi_role = r.get("poi_role") or "stop"`), and that the original 102 walk_by_only
POIs were never touched. Also checks the applier is idempotent.
"""

from __future__ import annotations

import importlib.util
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POI_RAW = ROOT / "data" / "paris" / "poi-raw.json"
ARTIFACT = ROOT / "data" / "paris" / "poi_role_backfill.json"
VALID_ROLES = {"stop", "setting", "walk_by_only"}
ORIGINAL_WALK_BY_ONLY = 102  # the pre-existing walk_by_only count (never touched by the backfill)


def _load(path: Path):
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _backfill_module():
    spec = importlib.util.spec_from_file_location(
        "backfill_poi_role", ROOT / "scripts" / "backfill_poi_role.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_no_null_poi_role():
    pois = _load(POI_RAW)
    nulls = [p["name"] for p in pois if p.get("poi_role") is None]
    assert nulls == [], f"{len(nulls)} POIs still have a null poi_role: {nulls[:5]}"


def test_all_roles_valid():
    pois = _load(POI_RAW)
    bad = {p.get("poi_role") for p in pois} - VALID_ROLES
    assert not bad, f"invalid poi_role values present: {bad}"


def test_artifact_applied_faithfully():
    pois = _load(POI_RAW)
    artifact = _load(ARTIFACT)
    for entry in artifact:
        poi = pois[entry["idx"]]
        assert poi["name"] == entry["name"], f"idx {entry['idx']} name drift"
        assert poi.get("poi_role") == entry["poi_role"], f"{entry['name']} role not applied"
        assert poi.get("_poi_role_reasoning"), f"{entry['name']} missing _poi_role_reasoning"


def test_overweighting_correction_is_nonzero():
    # The 128 nulls were coerced to full-weight "stop" (role_mult 1.0) at runtime.
    # The backfill must move a positive number of them OFF "stop" (to setting=0.7 or
    # walk_by_only=0.0); otherwise it corrected nothing.
    artifact = _load(ARTIFACT)
    moved_down = sum(1 for e in artifact if e["poi_role"] != "stop")
    assert moved_down > 0, "backfill did not move any POI off the coerced 'stop' weight"


def test_original_walk_by_only_untouched():
    pois = _load(POI_RAW)
    artifact = _load(ARTIFACT)
    backfilled_idx = {e["idx"] for e in artifact}
    original_walk_by = sum(
        1
        for i, p in enumerate(pois)
        if i not in backfilled_idx and p.get("poi_role") == "walk_by_only"
    )
    assert original_walk_by == ORIGINAL_WALK_BY_ONLY, (
        f"expected {ORIGINAL_WALK_BY_ONLY} pre-existing walk_by_only POIs untouched, "
        f"found {original_walk_by}"
    )


def test_backfill_is_idempotent():
    # Re-planning against the already-applied data yields zero changes and zero errors.
    mod = _backfill_module()
    pois = _load(POI_RAW)
    artifact = _load(ARTIFACT)
    changes, errors = mod.plan_changes(pois, artifact)
    assert errors == [], f"plan_changes reported errors on applied data: {errors[:3]}"
    assert changes == [], f"backfill not idempotent — {len(changes)} changes still pending"


def test_distribution_is_sane():
    artifact = _load(ARTIFACT)
    dist = Counter(e["poi_role"] for e in artifact)
    assert len(artifact) == 128, f"expected 128 backfilled POIs, got {len(artifact)}"
    assert all(dist[r] > 0 for r in VALID_ROLES), f"role missing from backfill: {dict(dist)}"
