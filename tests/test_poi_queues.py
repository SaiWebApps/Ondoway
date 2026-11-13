"""Structural guard on the per-POI queue pricing in `data/{city}/poi-raw.json`.

Five fields price the LINE in front of a place: the queue is a FOURTH kind of
time, "priced separately, by hour and season", belonging to the day rather than
to the building, and "excluded entirely under `wall`" — a visitor's 28-minute
line is priced, and a hard-wall day is never promised an unboundable one.

  `queue_class`           str   none | short | long | unpredictable (closed).
                                `unpredictable` is the catacombs class: a wait
                                that cannot be bounded.
  `queue_minutes_peak`    int   Minutes of line at the worst normal hour.
  `queue_minutes_offpeak` int   Minutes at a quiet hour; never above the peak.
  `queue_peak_hours`      list  Daily bands the peak applies to, as
                                ["HH:MM","HH:MM"] windows — the exact window
                                grammar `opening_hours` uses. [] = no band is
                                worse than the rest.
  `queue_basis`           str   One sentence arguing class and numbers,
                                judgeable by someone who has never been to the
                                city.

This file checks STRUCTURE only, for the reason every sibling guard records
(`tests/test_poi_visit_duration.py`, `tests/test_poi_opening_hours.py`,
`tests/test_poi_place_judgements.py`): nobody in this repo can adjudicate how
long the real line at a particular door runs. The basis sentence plus human
review — standard rate, with the Louvre, Sainte-Chapelle, the Musée d'Orsay and
every `unpredictable` row spot-checked by name — is the mechanism for
wrong-but-well-formed numbers; do not add a test asserting a particular POI's
real wait.

ALLOW-LIST MECHANICS: per-city checks loop IN-BODY over the tuple rather than
`@pytest.mark.parametrize`, because parametrizing over an EMPTY tuple produces a
skip and the suite hard-errors on skips.

Runs in milliseconds with no database.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.poi_queues import QUEUE_CLASSES, QUEUE_INT_FIELDS, QUEUE_MINUTES_MAX, validate

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = REPO_ROOT / "data"

QUEUE_FIELDS = (
    "queue_class",
    "queue_minutes_peak",
    "queue_minutes_offpeak",
    "queue_peak_hours",
    "queue_basis",
)

# Cities whose corpus has been through the queue pass and must therefore satisfy
# every data check below. Explicit allow-list, exactly like
# CITIES_WITH_OPENING_HOURS in the sibling file: listing a city that has not run
# the pass would make this file permanently red and train readers to ignore it.
# EMPTY until the first pass runs (the queue run adds "paris" as the
# declaring act).
#
# ADDING a slug here is the deliberate act of declaring "this city's queues are
# priced". REMOVING one to reach green is forbidden — it deletes the guard
# instead of fixing the data.
CITIES_WITH_QUEUE_PRICING: tuple[str, ...] = ("paris",)


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


REMEDY = "Run the queue pass: `make poi-queues SLUG=<city>`."


# ---------------------------------------------------------------------------
# The validator's own contract, pinned. The script refuses to WRITE anything
# these reject, so the pins double as the one statement of what a well-formed
# queue row is — the `test_pinned_precedents` genre from the category guard.
# ---------------------------------------------------------------------------


def test_the_vocabulary_is_closed_and_exactly_the_design_s_four() -> None:
    """The vocabulary is four classes; a silently widened
    vocabulary would change what a `wall` day may be offered without review.
    Order-insensitive on purpose: the tuple's ORDER is presentation, its SET is
    the contract."""
    assert set(QUEUE_CLASSES) == {"none", "short", "long", "unpredictable"}


@pytest.mark.parametrize(
    "record",
    [
        # The three calibration shapes the prompt pins, in miniature.
        {
            "queue_class": "long",
            "queue_minutes_peak": 30,
            "queue_minutes_offpeak": 10,
            "queue_peak_hours": [["10:30", "16:00"]],
            "queue_basis": "Airport-style security plus a ticket check ahead of a small chapel.",
        },
        {
            "queue_class": "none",
            "queue_minutes_peak": 0,
            "queue_minutes_offpeak": 0,
            "queue_peak_hours": [],
            "queue_basis": "A road bridge; there is no door and no line.",
        },
        {
            "queue_class": "unpredictable",
            "queue_minutes_peak": 120,
            "queue_minutes_offpeak": 45,
            "queue_peak_hours": [["11:00", "17:00"]],
            "queue_basis": "Occupancy-capped underground site; the line moves at a trickle "
            "and cannot be bounded.",
        },
        # A constant line: peak equals off-peak, so no band needs naming.
        {
            "queue_class": "short",
            "queue_minutes_peak": 5,
            "queue_minutes_offpeak": 5,
            "queue_peak_hours": [],
            "queue_basis": "One bag-check table that moves at the same pace all day.",
        },
    ],
    ids=["long", "none", "unpredictable", "constant-short"],
)
def test_validate_accepts_the_calibration_shapes(record: dict) -> None:
    assert validate(record, name="X") is None


@pytest.mark.parametrize(
    ("mutation", "must_mention"),
    [
        ({"queue_class": "medium"}, "queue_class"),
        ({"queue_minutes_peak": "30"}, "not an integer"),
        ({"queue_minutes_peak": True}, "not an integer"),
        ({"queue_minutes_offpeak": -1}, "negative"),
        ({"queue_minutes_peak": QUEUE_MINUTES_MAX + 1}, "cap"),
        # Peak below off-peak inverts the definition of "peak".
        ({"queue_minutes_peak": 5, "queue_minutes_offpeak": 10}, "worse hour"),
        ({"queue_peak_hours": None}, "not a list"),
        ({"queue_peak_hours": [["9:00", "16:00"]]}, "HH:MM"),
        ({"queue_peak_hours": [["16:00", "10:00"]]}, "backwards"),
        # A peak higher than off-peak with no band claims a peak exists but
        # refuses to say when — unusable by an hour-priced planner.
        ({"queue_peak_hours": []}, "WHEN"),
        ({"queue_basis": "  "}, "unauditable"),
    ],
    ids=[
        "unknown-class",
        "string-minutes",
        "bool-minutes",
        "negative-minutes",
        "over-cap",
        "peak-below-offpeak",
        "windows-not-a-list",
        "malformed-window",
        "backwards-window",
        "peaked-but-no-band",
        "empty-basis",
    ],
)
def test_validate_rejects_malformed_records(mutation: dict, must_mention: str) -> None:
    record = {
        "queue_class": "long",
        "queue_minutes_peak": 30,
        "queue_minutes_offpeak": 10,
        "queue_peak_hours": [["10:30", "16:00"]],
        "queue_basis": "Security screening ahead of one small door.",
        **mutation,
    }
    problem = validate(record, name="X")
    assert problem is not None, f"validator accepted {mutation!r}"
    assert must_mention in problem


def test_validate_rejects_a_none_class_that_still_charges() -> None:
    """`none` means no line EXISTS — zero minutes and no peak band are part of
    the class's meaning, not separate facts that may drift from it."""
    charged = {
        "queue_class": "none",
        "queue_minutes_peak": 10,
        "queue_minutes_offpeak": 0,
        "queue_peak_hours": [],
        "queue_basis": "Open square.",
    }
    problem = validate(charged, name="X")
    assert problem is not None and "no line costs no wait" in problem

    banded = {**charged, "queue_minutes_peak": 0, "queue_peak_hours": [["10:00", "12:00"]]}
    problem = validate(banded, name="X")
    assert problem is not None and "has no peak" in problem

    # And the inverse: a real class whose line never costs a minute is `none`.
    freebie = {
        "queue_class": "short",
        "queue_minutes_peak": 0,
        "queue_minutes_offpeak": 0,
        "queue_peak_hours": [],
        "queue_basis": "Ticket desk nobody ever waits at.",
    }
    problem = validate(freebie, name="X")
    assert problem is not None and "is 'none'" in problem


# ---------------------------------------------------------------------------
# Per-city data checks, live once a city enters the allow-list above.
# ---------------------------------------------------------------------------


def test_every_poi_carries_a_queue_class_from_the_closed_vocabulary() -> None:
    """Presence check — the pass ran, and every class is a vocabulary value.
    A missing key means the pass never reached this POI; on an unpriced corpus
    this is the check that goes red, which is what stops the bars below reading
    vacuously green."""
    for city in CITIES_WITH_QUEUE_PRICING:
        offenders = [
            f"{_name(p)}: queue_class={p.get('queue_class')!r}"
            for p in _pois(city)
            if p.get("queue_class") not in QUEUE_CLASSES
        ]
        if offenders:
            _fail(city, "POI(s) without a valid queue_class", offenders, REMEDY)


def test_queue_minutes_are_real_integers_and_coherent() -> None:
    """Both minute fields are real ints (never bools, never strings), inside
    the absurdity cap, peak >= off-peak, and a `none` place charges nothing."""
    for city in CITIES_WITH_QUEUE_PRICING:
        offenders = []
        for poi in _pois(city):
            values = {}
            for field in QUEUE_INT_FIELDS:
                value = poi.get(field)
                if not isinstance(value, int) or isinstance(value, bool):
                    offenders.append(f"{_name(poi)}: {field}={value!r}")
                elif not 0 <= value <= QUEUE_MINUTES_MAX:
                    offenders.append(f"{_name(poi)}: {field}={value} out of range")
                else:
                    values[field] = value
            if len(values) != 2:
                continue
            peak, offpeak = values["queue_minutes_peak"], values["queue_minutes_offpeak"]
            if peak < offpeak:
                offenders.append(f"{_name(poi)}: peak {peak} below off-peak {offpeak}")
            if poi.get("queue_class") == "none" and (peak or offpeak):
                offenders.append(f"{_name(poi)}: class 'none' yet charges {peak}/{offpeak} min")
        if offenders:
            _fail(city, "POI(s) with malformed or incoherent queue minutes", offenders, REMEDY)


def test_queue_peak_hours_parse() -> None:
    """Every peak-hours value is a list of well-formed ["HH:MM","HH:MM"]
    windows — the exact window grammar the opening-hours guard enforces — with
    no zero-length or backwards band, and a `none` place has none at all."""
    for city in CITIES_WITH_QUEUE_PRICING:
        offenders = []
        for poi in _pois(city):
            windows = poi.get("queue_peak_hours")
            if not isinstance(windows, list):
                offenders.append(f"{_name(poi)}: queue_peak_hours={windows!r}")
                continue
            if poi.get("queue_class") == "none" and windows:
                offenders.append(f"{_name(poi)}: class 'none' yet peaks at {windows!r}")
            for window in windows:
                if (
                    not isinstance(window, list)
                    or len(window) != 2
                    or not all(isinstance(t, str) and len(t) == 5 and t[2] == ":" for t in window)
                ):
                    offenders.append(f"{_name(poi)}: window {window!r}")
                elif window[0] >= window[1]:
                    offenders.append(f"{_name(poi)}: zero-length or backwards {window!r}")
        if offenders:
            _fail(city, "POI(s) whose queue peak hours do not parse", offenders, REMEDY)


def test_every_poi_argues_its_queue() -> None:
    """Every priced queue ships with the sentence that argues it. The review
    protocol (standard rate, named spot checks) reads exactly this sentence, so
    a POI without one is unauditable by construction."""
    for city in CITIES_WITH_QUEUE_PRICING:
        offenders = [
            f"{_name(p)}: queue_basis={p.get('queue_basis')!r}"
            for p in _pois(city)
            if not isinstance(p.get("queue_basis"), str) or not p.get("queue_basis", "").strip()
        ]
        if offenders:
            _fail(city, "POI(s) whose queue pricing carries no reasoning", offenders, REMEDY)


def test_every_export_chunk_carries_the_queue_fields() -> None:
    """The export sync propagated all five fields into every export chunk.

    `data/{slug}/export/*.json` is what the deploy uploads, so a field written
    only to poi-raw.json never reaches the graph — the Notre-Dame tier-1
    incident shape. Presence and shape only; value agreement with poi-raw.json
    is `tests/test_export_consistency.py`'s job.
    """
    for city in CITIES_WITH_QUEUE_PRICING:
        offenders = []
        for export_file in sorted((DATA_ROOT / city / "export").glob("*.json")):
            for entry in json.loads(export_file.read_text()):
                bad = [
                    field
                    for field in QUEUE_FIELDS
                    if field != "queue_basis" and field not in entry
                ]
                basis = entry.get("queue_basis")
                if not isinstance(basis, str) or not basis.strip():
                    bad.append("queue_basis")
                if bad:
                    offenders.append(
                        f"{export_file.name}: {entry.get('name', '<unnamed>')!r} "
                        f"missing/malformed {', '.join(bad)}"
                    )
        if offenders:
            _fail(
                city,
                "export entr(ies) the queue sync never reached",
                offenders,
                f"{REMEDY} Then run: make sync-poi-exports SLUG=<city>.",
            )


# ---------------------------------------------------------------------------
# The plumbing: the same hops every enriched field travels
# (`tests/test_poi_opening_hours.py` and `tests/test_poi_place_judgements.py`
# are the pattern). The contract, loader and upload tests are EXPECTED RED
# until the selection/upload half lands — they exist first precisely so that
# landing turns them green. The export-sync test is green from birth: the sync
# registration ships with the pass script itself.
# ---------------------------------------------------------------------------


def test_the_contract_declares_the_queue_fields() -> None:
    """RED until the selection/upload half lands.

    `POI` must declare all five queue fields. `POI` is `extra="ignore"`
    (src/tour/contract.py), so constructing with unknown fields SILENTLY DROPS
    them — construction succeeding proves nothing, which is why this reads
    every value back off the built object.

    `queue_peak_hours` is carried as the JSON-encoded STRING the graph stores
    (Neo4j cannot hold nested lists — the `opening_hours`/`physical_cues`
    precedent); the consumer decodes it.
    """
    from src.tour.contract import POI

    poi = POI(
        id="sainte-chapelle",
        name="Sainte-Chapelle",
        tier=5,
        poi_role="stop",
        lat=48.8554,
        lng=2.345,
        queue_class="long",
        queue_minutes_peak=30,
        queue_minutes_offpeak=10,
        queue_peak_hours='[["10:30", "16:00"]]',
        queue_basis="Airport-style security plus a ticket check ahead of a small chapel.",
    )
    assert poi.queue_class == "long", (
        "POI dropped queue_class — either it is undeclared (extra='ignore' discards "
        "unknown keywords without error) or it does not round-trip."
    )
    assert poi.queue_minutes_peak == 30, "POI dropped queue_minutes_peak"
    assert poi.queue_minutes_offpeak == 10, "POI dropped queue_minutes_offpeak"
    assert poi.queue_peak_hours == '[["10:30", "16:00"]]', "POI dropped queue_peak_hours"
    assert poi.queue_basis.startswith("Airport"), "POI dropped queue_basis"

    # The defaults are load-bearing: a corpus the pass has not reached must
    # price NOTHING. The contract's shape: `queue_class = None` is THE
    # never-priced switch — None means "nobody judged this", distinct from
    # "none" ("judged: no line") — and while it is None the int/str fields are INERT (nothing may
    # read them), so their 0/"" defaults claim nothing. The pricing seam
    # (src/tour/visit_time.py) prices a queue only off a set class.
    bare = POI(id="x", name="X", tier=3, poi_role="stop", lat=48.85, lng=2.35)
    assert bare.queue_class is None
    assert bare.queue_minutes_peak == 0
    assert bare.queue_minutes_offpeak == 0
    assert bare.queue_peak_hours == ""
    assert bare.queue_basis == ""


def test_the_corpus_loader_returns_and_constructs_the_queue_fields() -> None:
    """RED until the selection/upload half lands.

    Source-scan over the two silent hops in `src/tour/selection.py` (the
    `test_golden_diff_cli` genre): HOP 1 — `LOAD_PARIS_POIS_CYPHER` returns
    ONLY what its RETURN list names, so an unnamed property reaches nothing
    downstream with no error; HOP 2 — `_snapshot_from_records` sets constructor
    keywords one by one, so a name absent there is simply unused. Both halves
    must carry all five fields.
    """
    from src.tour.selection import LOAD_PARIS_POIS_CYPHER

    source = (REPO_ROOT / "src" / "tour" / "selection.py").read_text()
    for field in QUEUE_FIELDS:
        assert f"p.{field}" in LOAD_PARIS_POIS_CYPHER, (
            f"HOP 1: the corpus query never asks the graph for `{field}`, so it can "
            f"never reach the planner however well the rest of the chain works."
        )
        assert f"{field}=" in source, (
            f"HOP 2: nothing in selection.py passes `{field}=` — "
            f"`_snapshot_from_records` never constructs the POI with it, so the "
            f"column hop 1 returns is dropped unread."
        )


def test_hop_round_trip_a_graph_record_lands_on_the_poi() -> None:
    """RED until the selection/upload half lands. Cypher → snapshot, run for
    real: a record shaped like the graph's answer must surface every queue
    value on the built POI, and an unpriced record must land on the safe
    defaults (the `tests/test_poi_opening_hours.py` functional-hop genre).
    """
    from src.tour.selection import _snapshot_from_records

    bands = '[["10:30", "16:00"]]'
    record = {
        "id": "sainte-chapelle",
        "name": "Sainte-Chapelle",
        "tier": 5,
        "poi_role": "stop",
        "lat": 48.8554,
        "lng": 2.345,
        "areas": [],
        "queue_class": "long",
        "queue_minutes_peak": 30,
        "queue_minutes_offpeak": 10,
        "queue_peak_hours": bands,
        "queue_basis": "Airport-style security plus a ticket check ahead of a small chapel.",
    }
    poi = _snapshot_from_records([record], [], [], []).pois[0]
    assert poi.queue_class == "long", (
        "cypher → snapshot: the record carried queue_class but the POI does not. "
        "Either _snapshot_from_records does not pass it, or POI does not declare it."
    )
    assert poi.queue_minutes_peak == 30
    assert poi.queue_minutes_offpeak == 10
    assert poi.queue_peak_hours == bands
    assert poi.queue_basis.startswith("Airport")

    unpriced = {
        "id": "unpriced",
        "name": "A place with no queue pricing yet",
        "tier": 3,
        "poi_role": "stop",
        "lat": 48.8566,
        "lng": 2.3522,
        "areas": [],
        "queue_class": None,
        "queue_minutes_peak": None,
        "queue_minutes_offpeak": None,
        "queue_peak_hours": None,
        "queue_basis": None,
    }
    poi = _snapshot_from_records([unpriced], [], [], []).pois[0]
    # The loader lands an unpriced record on the CONTRACT defaults (the
    # closed-hop rule the visit/clock/judgement fields already follow):
    # queue_class None = the never-priced switch; the inert ints/strings take
    # their 0/"" defaults, which nothing may read while the class is None.
    assert poi.queue_class is None
    assert poi.queue_minutes_peak == 0
    assert poi.queue_minutes_offpeak == 0
    assert poi.queue_peak_hours == ""
    assert poi.queue_basis == ""


def test_the_upload_carries_the_queue_fields_in_both_property_lists() -> None:
    """RED until the selection/upload half lands. Upload → cypher:
    `scripts/upload_paris.py` keeps TWO hardcoded property lists — the param
    dict and the Cypher SET list of `_upload_pois` — whose own comment warns
    they must agree or a field silently never reaches the graph. Both must
    carry all five queue fields (the same both-lists scan the clock and
    judgement fields run).
    """
    source = (REPO_ROOT / "scripts" / "upload_paris.py").read_text()
    for field in QUEUE_FIELDS:
        assert f'"{field}"' in source, (
            f"upload param dict never carries `{field}` — it will never reach the graph"
        )
        assert f"p.{field}" in source, (
            f"upload Cypher SET list never writes `p.{field}` — the param is carried "
            "and then silently dropped at the write"
        )


def test_the_export_sync_carries_the_queue_fields() -> None:
    """Exports-sync coverage, green from birth: `SYNCED_FIELDS` in
    `scripts/sync_poi_exports.py` — THE one list of fields that flow
    poi-raw → export chunks — must carry all five queue fields, registered
    with the pass so the sync never lags it. Without
    this, the pass writes poi-raw.json and the deploy uploads chunks that
    never heard of it — the Notre-Dame tier-1 incident shape.
    """
    from scripts.sync_poi_exports import SYNCED_FIELDS

    missing = [field for field in QUEUE_FIELDS if field not in SYNCED_FIELDS]
    assert not missing, (
        f"scripts/sync_poi_exports.py SYNCED_FIELDS is missing {missing} — the queue "
        "pass would write poi-raw.json and the export chunks would never receive it"
    )
