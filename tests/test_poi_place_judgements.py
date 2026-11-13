"""Structural guard on the per-POI place judgements in `data/{city}/poi-raw.json`.

Three booleans and one sentence say what a place AFFORDS a party:

  `children_can_run`  bool  Children can be loud, fast and free-range here. A
                            family's place-minutes are most valuable where the
                            kids can run, and the corpus had no channel for it.
  `sit_and_talk`      bool  Two people can sit somewhere quiet enough to hear each
                            other — a couple who would rather talk picks the
                            Palais-Royal garden on seating, enclosure and quiet,
                            three properties no POI in the corpus carried.
  `good_after_dark`   bool  Good to be LEFT STANDING there in the dark. On a walk
                            that finishes after dusk, the planner prefers a
                            finishing place marked true here.
  `judgement_basis`   str   One sentence arguing all three, judgeable by someone
                            who has never been to the city.

These are the ONLY data fields with no external source — the
model's verdict is the whole provenance — so review samples these rows at twice the
normal rate. This file checks STRUCTURE only: presence, real booleans, a
non-empty basis. None of it is knowledge-based, for the reason its two siblings
(`tests/test_poi_visit_duration.py`, `tests/test_poi_opening_hours.py`) record:
nobody in this repo can adjudicate whether children really run in a particular
square. The basis sentence plus human review is the mechanism for
wrong-but-well-formed verdicts; do not add a test asserting a particular POI's
judgement.

ALLOW-LIST MECHANICS: per-city checks loop IN-BODY over the tuple rather than
`@pytest.mark.parametrize`, because parametrizing over an EMPTY tuple produces a
skip and the suite hard-errors on skips.

Runs in milliseconds with no database.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = REPO_ROOT / "data"

JUDGEMENT_BOOLS = ("children_can_run", "sit_and_talk", "good_after_dark")
JUDGEMENT_FIELDS = (*JUDGEMENT_BOOLS, "judgement_basis")

# Cities whose corpus has been through the place-judgements pass and must therefore
# satisfy every data check below. Explicit allow-list, exactly like
# CITIES_WITH_OPENING_HOURS in the sibling file: listing a city that has not run
# the pass would make this file permanently red and train readers to ignore it.
# EMPTY until the first pass runs (adding "paris" is the declaring act).
#
# ADDING a slug here is the deliberate act of declaring "this city's judgements are
# in". REMOVING one to reach green is forbidden — it deletes the guard instead of
# fixing the data.
CITIES_WITH_PLACE_JUDGEMENTS: tuple[str, ...] = ("paris",)


def _pois(city: str) -> list[dict]:
    return json.loads((DATA_ROOT / city / "poi-raw.json").read_text())


def _name(poi: dict) -> str:
    return str(poi.get("name", "<unnamed POI>"))


def _fail(city: str, headline: str, offenders: list[str], remedy: str) -> None:
    shown = offenders[:20]
    more = f"\n  ... and {len(offenders) - len(shown)} more" if len(offenders) > len(shown) else ""
    pytest.fail(
        f"{city}: {headline} ({len(offenders)} of them)\n"
        + "\n".join(f"  {line}" for line in shown)
        + more
        + f"\n{remedy}"
    )


REMEDY = "Run the judgements pass: `make poi-place-judgements SLUG=<city>`."


def test_every_poi_carries_all_three_judgements() -> None:
    """Presence check — the pass ran, and every verdict is a REAL boolean.

    A missing key means the pass never reached this POI; a string "true" is a
    verdict nothing downstream can read. On an unjudged
    corpus this is the check that goes red, which is what stops the basis check
    below reading vacuously green.
    """
    for city in CITIES_WITH_PLACE_JUDGEMENTS:
        offenders = []
        for poi in _pois(city):
            for field in JUDGEMENT_BOOLS:
                if not isinstance(poi.get(field), bool):
                    offenders.append(f"{_name(poi)}: {field}={poi.get(field)!r}")
        if offenders:
            _fail(city, "POI(s) with a missing or non-boolean judgement", offenders, REMEDY)


def test_every_poi_argues_its_judgements() -> None:
    """Every trio of verdicts ships with the sentence that argues it.

    These fields have NO external source: the basis sentence is
    the only audit trail a reviewer gets, so a POI without one is unauditable by
    construction — the whole reason review samples these rows at twice the rate.
    """
    for city in CITIES_WITH_PLACE_JUDGEMENTS:
        offenders = [
            f"{_name(p)}: judgement_basis={p.get('judgement_basis')!r}"
            for p in _pois(city)
            if not isinstance(p.get("judgement_basis"), str)
            or not p.get("judgement_basis", "").strip()
        ]
        if offenders:
            _fail(city, "POI(s) whose judgements carry no reasoning", offenders, REMEDY)


def test_every_export_chunk_carries_the_judgements() -> None:
    """The export sync propagated the four fields into every export chunk.

    `data/{slug}/export/*.json` is what the deploy uploads, so a field written
    only to poi-raw.json never reaches the graph — the exact gap the pass's
    "NEXT, AND MANDATORY" trailer warns about and the sync step closes.
    Presence and shape only; value
    agreement with poi-raw.json is `tests/test_export_consistency.py`'s job.
    """
    for city in CITIES_WITH_PLACE_JUDGEMENTS:
        offenders = []
        for export_file in sorted((DATA_ROOT / city / "export").glob("*.json")):
            for entry in json.loads(export_file.read_text()):
                bad = [
                    field
                    for field in JUDGEMENT_BOOLS
                    if not isinstance(entry.get(field), bool)
                ]
                basis = entry.get("judgement_basis")
                if not isinstance(basis, str) or not basis.strip():
                    bad.append("judgement_basis")
                if bad:
                    offenders.append(
                        f"{export_file.name}: {entry.get('name', '<unnamed>')!r} "
                        f"missing/malformed {', '.join(bad)}"
                    )
        if offenders:
            _fail(
                city,
                "export entr(ies) the judgement sync never reached",
                offenders,
                f"{REMEDY} Then sync data/{city}/export/*.json.",
            )


# ---------------------------------------------------------------------------
# The plumbing: the same three hops the visit-capacity and clock fields travel
# (`tests/test_poi_visit_duration.py`, `tests/test_poi_opening_hours.py` are the
# pattern). All three are EXPECTED RED until the contract/loader/upload half
# lands — they exist first precisely so that landing turns them green.
# ---------------------------------------------------------------------------


def test_the_contract_declares_the_judgement_fields() -> None:
    """RED until the contract/loader/upload half lands.

    `POI` must declare the three booleans (defaulting False — an unjudged corpus
    affords nothing rather than everything) and `judgement_basis` (default "").
    `POI` is `extra="ignore"` (src/tour/contract.py), so constructing with
    unknown fields SILENTLY DROPS them — construction succeeding proves nothing,
    which is why this reads every value back off the built object.
    """
    from src.tour.contract import POI

    poi = POI(
        id="palais-royal-garden",
        name="Palais-Royal garden",
        tier=4,
        poi_role="stop",
        lat=48.865,
        lng=2.337,
        children_can_run=True,
        sit_and_talk=True,
        good_after_dark=False,
        judgement_basis="Enclosed traffic-free garden with movable chairs; gates lock at dusk.",
    )
    assert poi.children_can_run is True, (
        "POI dropped children_can_run — either it is undeclared (extra='ignore' "
        "discards unknown keywords without error) or it does not round-trip."
    )
    assert poi.sit_and_talk is True, "POI dropped sit_and_talk"
    assert poi.good_after_dark is False, "POI dropped good_after_dark"
    assert poi.judgement_basis.startswith("Enclosed"), "POI dropped judgement_basis"

    # The defaults are load-bearing: a corpus the pass has not reached must
    # afford NOTHING (False/""), the same safe-direction rule the clock fields
    # follow (a place is never granted an affordance nobody judged).
    bare = POI(id="x", name="X", tier=3, poi_role="stop", lat=48.85, lng=2.35)
    assert bare.children_can_run is False
    assert bare.sit_and_talk is False
    assert bare.good_after_dark is False
    assert bare.judgement_basis == ""


def test_the_corpus_loader_returns_and_constructs_the_judgement_fields() -> None:
    """RED until the contract/loader/upload half lands.

    Source-scan over the two silent hops in `src/tour/selection.py` (the
    `test_golden_diff_cli` genre): HOP 1 — `LOAD_PARIS_POIS_CYPHER` returns
    ONLY what its RETURN list names, so an unnamed property reaches nothing
    downstream with no error; HOP 2 — `_snapshot_from_records` sets constructor
    keywords one by one, so a name absent there is simply unused. Both halves
    must carry all four fields.
    """
    from src.tour.selection import LOAD_PARIS_POIS_CYPHER

    source = (REPO_ROOT / "src" / "tour" / "selection.py").read_text()
    for field in JUDGEMENT_FIELDS:
        assert f"p.{field}" in LOAD_PARIS_POIS_CYPHER, (
            f"HOP 1: the corpus query never asks the graph for `{field}`, so it can "
            f"never reach the planner however well the rest of the chain works."
        )
        assert f"{field}=" in source, (
            f"HOP 2: nothing in selection.py passes `{field}=` — "
            f"`_snapshot_from_records` never constructs the POI with it, so the "
            f"column hop 1 returns is dropped unread."
        )


def test_the_upload_carries_the_judgement_fields_in_both_property_lists() -> None:
    """RED until the contract/loader/upload half lands.

    `scripts/upload_paris.py` keeps TWO hardcoded property lists — the param
    dict and the Cypher SET list of `_upload_pois` — whose own comment warns
    they must agree or a field silently never reaches the graph. Both must
    carry all four judgement fields (the same both-lists scan
    `tests/test_poi_opening_hours.py` runs for the clock fields).
    """
    source = (REPO_ROOT / "scripts" / "upload_paris.py").read_text()
    for field in JUDGEMENT_FIELDS:
        assert f'"{field}"' in source, (
            f"upload param dict never carries `{field}` — it will never reach the graph"
        )
        assert f"p.{field}" in source, (
            f"upload Cypher SET list never writes `p.{field}` — the param is carried "
            "and then silently dropped at the write"
        )
