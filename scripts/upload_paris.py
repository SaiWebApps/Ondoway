"""Bulk upload a city's data to Neo4j (local or Aura).

City-parameterized: pass a city slug (default ``paris``). Reads from
``data/{city_slug}/poi-raw.json`` and ``data/{city_slug}/beats.json`` and
rejects POIs outside the city's bbox in ``CITY_BBOX`` (the out-of-city geofence
guard). Register a new city via the onboarding panel /
``src.city_registry.register_city`` (which writes ``src/cities.json``);
``CITY_BBOX`` below is derived from that registry.

Creates:
  - Schema constraints and indexes
  - Lens nodes (MVP + any additional lenses referenced by beats)
  - POI nodes with spatial points
  - NarrativeBeat nodes with full script bodies
  - HAS_BEAT relationships (POI → Beat)
  - TAGGED_WITH relationships (Beat → Lens)

The full path reads the city's graph state first and sends only the rows whose
written fields, home link or lens tag differ from the repo, in parallel chunks;
a row equal to the graph is never written.

Usage:
    make deploy CITY=paris            # any city in CITY_BBOX
    # or directly:
    python -m scripts.upload_paris new_york
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from src import city_registry
from src.api.models.nodes import canonical_name_key
from src.connection import abort_on_connection_error, create_driver, get_database
from src.schema.constraints import apply_all
from src.seed.lenses import seed_lenses

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _assert_upload_target_allowed(allow_cloud: bool) -> None:
    """Refuse an upload against a non-local (Aura) target unless --allow-cloud.

    The upload path rewrites every live field that differs from the repo (and
    removes one the repo lacks), so an accidental run while ``.env`` is in cloud
    mode overwrites production. This
    fires BEFORE any driver is created, so it protects even when Aura is
    unreachable/paused. A deliberate cloud upload is still possible by typing
    ``--allow-cloud`` explicitly.
    """
    if allow_cloud:
        return
    uri = os.getenv("NEO4J_URI", "")
    host = urlparse(uri).hostname or ""
    if host not in _LOCAL_HOSTS:
        raise SystemExit(
            f"REFUSING to upload against non-local NEO4J_URI={uri!r} (host={host!r}). "
            f"This path overwrites live fields from the repo. Point NEO4J_URI at "
            f"the local database first (the local Make targets do), or pass "
            f"--allow-cloud to deliberately target Aura."
        )


VALIDATOR = Path(__file__).resolve().parent / "validate_beats.py"

# Generous per-city bounding boxes (city + inner edges): reject gross coordinate
# errors (a Boston POI, (0,0), or out-of-city leaks) without clipping legitimate
# edge POIs. (min_lat, max_lat, min_lon, max_lon). Now derived from the city
# registry (src/cities.json) — the single writable registration surface.
CITY_BBOX: dict[str, tuple[float, float, float, float]] = city_registry.bbox_map()
PARIS_BBOX = CITY_BBOX["paris"]  # back-compat default


def _city_paths(city_slug: str) -> tuple[Path, Path]:
    # Hermetic-aware data root (``$ONBOARD_DATA_ROOT`` when set, else
    # ``<repo>/data``) so a hermetic onboard's tmp corpus is what the deploy reads;
    # unset → <repo>/data/{slug}, byte-identical to the prior hardcoded path.
    data_dir = city_registry.onboard_data_root() / city_slug
    return data_dir / "poi-raw.json", data_dir / "beats.json"

# fact_check.status values that must never reach the live database.
_BLOCKED_STATUSES = {"disputed"}


def _load_json(path: Path) -> list[dict]:
    with open(path) as f:
        return json.load(f)


def _in_city_bounds(lat: float, lon: float, bbox: tuple = PARIS_BBOX) -> bool:
    return bbox[0] <= lat <= bbox[1] and bbox[2] <= lon <= bbox[3]


def _beat_blocked(beat: dict) -> bool:
    """True if a beat must NOT be uploaded — currently: missing essentials or a
    `disputed` fact-check status. (Note: this does NOT require `verified`;
    uploading unverified beats is a launch-policy decision left to the operator.)"""
    if not beat.get("poi_name") or not beat.get("script_body"):
        return True
    return (beat.get("fact_check") or {}).get("status") in _BLOCKED_STATUSES


def _assert_beats_valid(beats_path: Path) -> None:
    """Run the full validate_beats gate before any DB write. Aborts the
    upload if the beats file fails — so grounding, verification-freshness,
    uniqueness, and status checks all gate the upload, not just extraction."""
    result = subprocess.run(
        [sys.executable, str(VALIDATOR), str(beats_path)], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Refusing to upload: validate_beats rejected the beats file.\n"
            + (result.stdout or "")
            + (result.stderr or "")
        )


CHUNK_SIZE = 500
WRITE_WORKERS = 4


def write_in_chunks(
    driver,
    rows: list[dict],
    work: Callable[[Any, list[dict]], int],
    workers: int = WRITE_WORKERS,
) -> int:
    """Send ``rows`` in chunks of ``CHUNK_SIZE``, ``workers`` chunks at a time, and
    return the sum of what ``work(tx, chunk)`` returns.

    Each worker opens its own session and sends each chunk through
    ``execute_write``, so a transient error or a deadlock between two chunks is
    retried rather than fatal. ``rows`` must hold each key once (``last_per_key``):
    two chunks merging the same key at once could each create the node.
    """
    chunks = [rows[i : i + CHUNK_SIZE] for i in range(0, len(rows), CHUNK_SIZE)]
    if not chunks:
        return 0
    groups = [chunks[w::workers] for w in range(min(workers, len(chunks)))]

    def _send(group: list[list[dict]]) -> int:
        with driver.session(database=get_database()) as session:
            return sum(session.execute_write(work, chunk) for chunk in group)

    with ThreadPoolExecutor(max_workers=len(groups)) as pool:
        return sum(pool.map(_send, groups))


def last_per_key(rows: Iterable[dict], key: Callable[[dict], Any]) -> list[dict]:
    """One row per key, the last one winning — what one UNWIND over all the rows
    leaves in the graph when a key repeats."""
    return list({key(row): row for row in rows}.values())


def same_value(graph: Any, repo: Any) -> bool:
    """Whether the graph already holds exactly the value a SET would write.

    A missing property reads as None, the value whose SET removes it. The types
    must match as well as the values: ``True == 1`` and ``10 == 10.0`` in Python,
    yet writing either changes what the graph stores.
    """
    if isinstance(graph, list) and isinstance(repo, list):
        return len(graph) == len(repo) and all(map(same_value, graph, repo))
    return type(graph) is type(repo) and graph == repo


def _same_location(props: dict, lat: float, lon: float) -> bool:
    loc = props.get("location")
    return loc is not None and (loc.latitude, loc.longitude) == (lat, lon)


def _changed(nodes: list[dict], row: dict, skip: set[str]) -> bool:
    """Whether writing ``row`` would change any of the graph ``nodes`` it MERGEs
    onto: a new key, or any written field that differs. ``skip`` names the row
    keys the SET list does not copy onto the node under their own name."""
    if not nodes:
        return True
    return any(
        any(not same_value(props.get(k), v) for k, v in row.items() if k not in skip)
        for props in nodes
    )


# The POI row keys the SET lists do not copy onto the node under their own name:
# the MERGE key, and the coordinates that become `p.location`.
_POI_ROW_NOT_WRITTEN = {"name_key", "city_name", "lat", "lon"}


def _graph_pois_by_key(
    session, keys: list[str], city_name: str, fields: list[str]
) -> dict[str, list[dict]]:
    """``fields`` of every POI node each ``(name_key, city_name)`` MERGEs onto.

    Only the compared fields travel: reading whole nodes for every body place
    costs several times as long. ``fields`` are this module's own row keys.
    """
    projection = ", ".join(f".{f}" for f in fields)
    by_key: dict[str, list[dict]] = {}
    for rec in session.run(
        f"""
        UNWIND $keys AS k
        MATCH (p:POI {{name_key: k, city_name: $city}})
        RETURN k, p {{{projection}}} AS props
        """,
        keys=keys,
        city=city_name,
    ):
        by_key.setdefault(rec["k"], []).append(dict(rec["props"]))
    return by_key


def _changed_pois(driver, rows: list[dict], city_name: str) -> list[dict]:
    """The POI rows whose write would change the graph, compared field for field."""
    fields = [*sorted({k for row in rows for k in row} - _POI_ROW_NOT_WRITTEN), "location"]
    with driver.session(database=get_database()) as session:
        graph = _graph_pois_by_key(session, [r["name_key"] for r in rows], city_name, fields)
    return [
        row
        for row in rows
        if _changed(graph.get(row["name_key"], []), row, _POI_ROW_NOT_WRITTEN)
        or not all(
            _same_location(props, row["lat"], row["lon"])
            for props in graph.get(row["name_key"], [])
        )
    ]


def _ensure_lenses(session, lens_slugs: set[str]) -> int:
    """Create any lens nodes that don't already exist. Returns count created."""
    params = [
        {"name": slug, "display_label": slug.replace("_", " ").title()}
        for slug in sorted(lens_slugs)
    ]
    result = session.run(
        """
        UNWIND $lenses AS lens
        MERGE (l:Lens {name: lens.name})
        ON CREATE SET
            l.id = randomUUID(),
            l.display_label = lens.display_label,
            l.is_parent = false
        RETURN count(l) AS total
        """,
        lenses=params,
    )
    return result.single()["total"]


_POI_WRITE = """
UNWIND $pois AS poi
MERGE (p:POI {name_key: poi.name_key, city_name: poi.city_name})
ON CREATE SET p.id = randomUUID()
SET p.name                = poi.name,
    p.short_description   = poi.short_description,
    p.location            = point({latitude: poi.lat, longitude: poi.lon, srid: 4326}),
    p.importance_tier     = poi.importance_tier,
    p.trigger_radius      = poi.trigger_radius,
    p.kid_friendly        = poi.kid_friendly,
    p.name_variations     = poi.name_variations,
    p.poi_role            = poi.poi_role,
    // Every key of the row dict except the MERGE key and the coordinates is
    // SET here from the row key of the same name, and the change check
    // compares exactly those keys. A key added to the row and not here is
    // never written; one added here and not to the row is cleared.
    p.typical_duration_min = poi.typical_duration_min,
    p.visit_seconds_inside = poi.visit_seconds_inside,
    p.visit_basis          = poi.visit_basis,
    p.opening_hours        = poi.opening_hours,
    p.opening_hours_source = poi.opening_hours_source,
    p.opening_hours_basis  = poi.opening_hours_basis,
    p.gated                = poi.gated,
    p.place_category       = poi.place_category,
    p.children_can_run     = poi.children_can_run,
    p.sit_and_talk         = poi.sit_and_talk,
    p.good_after_dark      = poi.good_after_dark,
    p.judgement_basis      = poi.judgement_basis,
    p.queue_class          = poi.queue_class,
    p.queue_minutes_peak   = poi.queue_minutes_peak,
    p.queue_minutes_offpeak = poi.queue_minutes_offpeak,
    p.queue_peak_hours     = poi.queue_peak_hours,
    p.queue_basis          = poi.queue_basis,
    p.anchors              = poi.anchors
RETURN count(p) AS total
"""


def _write_poi_chunk(tx, chunk: list[dict]) -> int:
    return tx.run(_POI_WRITE, pois=chunk).single()["total"]


def _upload_pois(driver, pois: list[dict], city_name: str, bbox: tuple) -> dict[str, int]:
    """Upload the POIs whose graph record differs from the repo. Returns stats:
    ``created`` counts every in-bounds POI the graph now holds, ``changed`` the
    ones this run wrote."""
    params = []
    skipped = 0
    out_of_bounds = 0
    for poi in pois:
        lat = poi.get("latitude")
        lon = poi.get("longitude")
        if lat is None or lon is None:
            skipped += 1
            continue
        if not _in_city_bounds(float(lat), float(lon), bbox):
            # Coordinate hygiene: never upload a POI outside the city geofence
            # (catches the Boston-POI class, (0,0), and stray coords).
            print(f"         ! skipping out-of-bounds POI {poi.get('name')!r} @ ({lat}, {lon})")
            out_of_bounds += 1
            continue

        name_variations = poi.get("name_variations") or []
        if isinstance(name_variations, str):
            name_variations = [name_variations]

        params.append({
            "name": poi["name"],
            # Canonical dedup key, computed identically to create_node
            # so a later API create/edit MERGEs onto this node instead of forking.
            "name_key": canonical_name_key(poi["name"]),
            "city_name": city_name,
            "short_description": poi.get("short_description", ""),
            "lat": float(lat),
            "lon": float(lon),
            "importance_tier": poi.get("importance_tier", 1),
            "trigger_radius": poi.get("trigger_radius", 10),
            "kid_friendly": poi.get("kid_friendly", "yes"),
            "name_variations": name_variations,
            "poi_role": poi.get("poi_role"),
            # Visit capacity: how long a visitor usefully spends AT the place.
            # `typical_duration_min` is MINUTES outside, `visit_seconds_inside`
            # is SECONDS inside (None where there is no interior), `visit_basis`
            # is the sentence that argues for both. Produced by the visit-duration
            # capacity pass.
            #
            # NO DEFAULTS HERE, deliberately, and note this differs from
            # POICreate.typical_duration_min = 30 in src/api/models/nodes.py.
            # Absence stays absent, exactly as _provenance_fields below does it:
            # `SET x = null` REMOVES the property in Neo4j, so an unpriced POI
            # carries nothing and the reader's own default applies. This path
            # re-syncs an entire city's corpus on every deploy, so substituting a
            # plausible 30 would stamp a made-up half-hour visit onto every POI
            # that has not been through the pass, indistinguishable afterwards
            # from a measured one. The API model may default because it creates
            # one POI at a time from a caller who omitted the field on purpose.
            "typical_duration_min": poi.get("typical_duration_min"),
            "visit_seconds_inside": poi.get("visit_seconds_inside"),
            "visit_basis": (poi.get("visit_basis") or "").strip() or None,
            # The planner's clock. `opening_hours` is
            # OpenStreetMap text in poi-raw.json and travels as that text; the
            # clock rule reads it through the hours library. Same no-defaults
            # rule as the visit fields above: absence stays absent (`SET x =
            # null` removes the property), so a POI the pass never reached is
            # indistinguishable from nothing, not from a place with known hours.
            "opening_hours": (
                poi["opening_hours"].strip()
                if isinstance(poi.get("opening_hours"), str) and poi["opening_hours"].strip()
                else None
            ),
            "opening_hours_source": poi.get("opening_hours_source"),
            "opening_hours_basis": (poi.get("opening_hours_basis") or "").strip() or None,
            # The door verdict, same no-defaults rule — absence stays absent, so
            # a corpus the pass never reached claims nothing.
            "gated": poi.get("gated") if isinstance(poi.get("gated"), bool) else None,
            "place_category": (poi.get("place_category") or "").strip() or None,
            # Place judgements — three AI verdicts
            # with no external source, same no-defaults rule as the fields
            # above: absence stays absent, never a made-up affordance.
            "children_can_run": poi.get("children_can_run"),
            "sit_and_talk": poi.get("sit_and_talk"),
            "good_after_dark": poi.get("good_after_dark"),
            "judgement_basis": (poi.get("judgement_basis") or "").strip() or None,
            # The queue — the wait BEFORE entering,
            # priced separately from visit time. `queue_peak_hours` is a list of
            # [start, end] hour bands in poi-raw.json; JSON-encoded here exactly
            # like `opening_hours` above and decoded by the pricing in
            # src/tour/visit_time.py. Same no-defaults rule: absence stays
            # absent, so a corpus the queue pass never reached prices no queue
            # anywhere rather than a made-up zero-minute one.
            "queue_class": (poi.get("queue_class") or "").strip() or None,
            "queue_minutes_peak": poi.get("queue_minutes_peak"),
            "queue_minutes_offpeak": poi.get("queue_minutes_offpeak"),
            "queue_peak_hours": (
                json.dumps(poi["queue_peak_hours"], ensure_ascii=False)
                if isinstance(poi.get("queue_peak_hours"), list)
                else None
            ),
            "queue_basis": (poi.get("queue_basis") or "").strip() or None,
            # The reviewed anchors (a place's story segments): a list of
            # dicts in poi-raw.json, JSON-encoded here exactly like `opening_hours`
            # and decoded by the corpus loader (src/tour/selection.py). Same
            # no-defaults rule: absence stays absent, so a place nobody reviewed has
            # no chapters rather than made-up ones.
            "anchors": (
                json.dumps(poi["anchors"], ensure_ascii=False)
                if isinstance(poi.get("anchors"), list) and poi["anchors"]
                else None
            ),
        })

    rows = last_per_key(params, lambda r: r["name_key"])
    changed = _changed_pois(driver, rows, city_name)
    write_in_chunks(driver, changed, _write_poi_chunk)
    return {
        "created": len(rows),
        "changed": len(changed),
        "skipped": skipped,
        "out_of_bounds": out_of_bounds,
    }


_BODY_PLACE_WRITE = """
UNWIND $places AS place
MERGE (p:POI {name_key: place.name_key, city_name: place.city_name})
ON CREATE SET p.id = randomUUID()
SET p.name                 = place.name,
    p.short_description    = place.short_description,
    p.location             = point({latitude: place.lat, longitude: place.lon, srid: 4326}),
    p.importance_tier      = place.importance_tier,
    p.trigger_radius       = place.trigger_radius,
    p.kid_friendly         = place.kid_friendly,
    p.name_variations      = place.name_variations,
    p.poi_role             = place.poi_role,
    p.typical_duration_min = place.typical_duration_min
RETURN count(p) AS total
"""


def _write_body_place_chunk(tx, chunk: list[dict]) -> int:
    return tx.run(_BODY_PLACE_WRITE, places=chunk).single()["total"]


def _upload_body_places(driver, city_slug: str, bbox: tuple) -> dict[str, int]:
    """Upload toilets/benches from ``data/{slug}/body-places.json`` as
    ``poi_role="body"`` POI nodes — the OSM pass
    (``scripts/poi_body_places.py``). Absent file = this city has not run the
    pass yet; skipped, not an error, so every other city's upload is
    unaffected by this function's existence.

    Uploaded as ordinary ``:POI`` nodes, not a separate label, so body places
    flow through the SAME loader (``LOAD_PARIS_POIS_CYPHER``) the planner
    already reads. ``POI_ROLE_MULTIPLIER["body"] = 0.0`` (src/tour/selection.py)
    is what then keeps them out of the story ranking; only the rest-cadence
    axis (``_seat_body_stops``) may ever schedule one. `name_key` is the
    record's own stable id ("body-toilet-<osmid>") rather than a canonicalized
    name — a body place has no name to canonicalize, and the id is already the
    MERGE-safe, re-upload-stable key the fetch pass produces.
    """
    path = city_registry.onboard_data_root() / city_slug / "body-places.json"
    if not path.exists():
        return {"created": 0, "changed": 0, "skipped": 0, "out_of_bounds": 0}
    records = _load_json(path)

    params = []
    out_of_bounds = 0
    for rec in records:
        lat, lng = rec.get("lat"), rec.get("lng")
        if lat is None or lng is None:
            continue
        if not _in_city_bounds(float(lat), float(lng), bbox):
            out_of_bounds += 1
            continue
        kind = rec.get("kind")
        params.append({
            "name_key": rec["id"],
            "city_name": city_slug,
            "name": "Public toilet" if kind == "toilet" else "Bench",
            "short_description": "",
            "lat": float(lat),
            "lon": float(lng),
            "importance_tier": 1,
            "trigger_radius": 10,
            "kid_friendly": "yes",
            "name_variations": [],
            "poi_role": "body",
            # A body stop carries ZERO NARRATION but real minutes (it is
            # seated as a zero-narration stop — narration, not duration).
            # A toilet stop and a bench sit are both real
            # time the day spends; these are conservative fixed minutes, not
            # narration-derived, since a body place has no beats to price by.
            "typical_duration_min": 5 if kind == "toilet" else 8,
        })

    rows = last_per_key(params, lambda r: r["name_key"])
    changed = _changed_pois(driver, rows, city_slug)
    write_in_chunks(driver, changed, _write_body_place_chunk)
    skipped = len(records) - len(params) - out_of_bounds
    return {
        "created": len(rows),
        "changed": len(changed),
        "skipped": skipped,
        "out_of_bounds": out_of_bounds,
    }


def _provenance_fields(beat: dict) -> dict:
    """The three VERIFY provenance/faithfulness fields.

    Normalized so absence stays absent: `SET x = null` in Neo4j REMOVES the
    property, so a beat without provenance never gains a null-valued key.
    """
    passage = (beat.get("source_passage") or "").strip() or None
    chunk_slug = (beat.get("source_chunk_slug") or "").strip() or None
    claims = [s.strip() for s in (beat.get("key_claims") or []) if isinstance(s, str) and s.strip()]
    return {
        "source_passage": passage,
        "source_chunk_slug": chunk_slug,
        "key_claims": claims or None,
    }


def _backfill_provenance(session, beats: list[dict]) -> dict[str, int]:
    """Set ONLY the three provenance fields on existing beats (match by beat_id).

    The full upload path rewrites every beat field that differs from the repo, so
    backfilling provenance onto an already-uploaded graph never goes through it.
    """
    params = []
    for beat in beats:
        beat_id = beat.get("beat_id", "")
        if not beat_id:
            continue
        fields = _provenance_fields(beat)
        if not any(fields.values()):
            continue
        params.append({"beat_id": beat_id, **fields})
    result = session.run(
        """
        UNWIND $beats AS b
        MATCH (beat:NarrativeBeat {beat_id: b.beat_id})
        SET beat.source_passage    = b.source_passage,
            beat.source_chunk_slug = b.source_chunk_slug,
            beat.key_claims        = b.key_claims
        RETURN count(beat) AS updated
        """,
        beats=params,
    )
    updated = result.single()["updated"]
    return {"updated": updated, "candidates": len(params)}


def _backfill_anchors(session, pois: list[dict], city_name: str) -> dict[str, int]:
    """Set ONLY ``p.anchors`` on existing POIs (match by the canonical name key).

    The reviewed anchors are what cuts a big place's story into chapters told where a
    person placed them; a POI whose graph record carries none is silently uncut, and
    every sentence written for a named spot inside it plays at the edge of the whole
    footprint instead. The full upload path rewrites every field that differs from
    the repo, so bringing reviewed anchors to an already-uploaded graph never goes
    through it — the ``--provenance-only`` precedent, applied to POI data.
    """
    params = [
        {
            "name_key": canonical_name_key(poi["name"]),
            "city_name": city_name,
            "anchors": json.dumps(poi["anchors"], ensure_ascii=False),
            # The anchored place's own modelling rides with its chapters: the
            # kind-aware standing rule (tests/test_poi_anchors.py) binds the
            # anchors to the place's role and footprint, so the three fields
            # are one reviewed record and sync together.
            "poi_role": poi.get("poi_role"),
            "trigger_radius": poi.get("trigger_radius"),
        }
        for poi in pois
        if isinstance(poi.get("anchors"), list) and poi["anchors"]
    ]
    if not params:
        return {"updated": 0, "candidates": 0}
    result = session.run(
        """
        UNWIND $pois AS poi
        MATCH (p:POI {name_key: poi.name_key, city_name: poi.city_name})
        SET p.anchors        = poi.anchors,
            p.poi_role       = poi.poi_role,
            p.trigger_radius = poi.trigger_radius
        RETURN count(p) AS updated
        """,
        pois=params,
    )
    return {"updated": result.single()["updated"], "candidates": len(params)}


def _backfill_beat_texts(session, beats: list[dict], city_name: str) -> dict[str, int]:
    """Re-sync the beats whose WORDS or PLACEMENT changed (match by beat_id),
    clear audio for exactly the word changes, and move a re-homed beat's link.

    A beat whose graph text differs from the repo's is a beat whose recorded
    audio says the OLD words — serving that file under the new text plays
    words the beat no longer says — so ``audio_url`` is cleared for the
    word-changed beats alone; a placement-only change keeps its paid audio.
    The link pass below touches ONLY beats whose graph home differs from the
    repo's ``poi_name`` (its WHERE), and refuses a ``poi_name`` matching more
    than one POI rather than double-linking. The full upload path rewrites every
    field that differs from the repo; a targeted fix never goes through it — the
    ``--provenance-only`` / ``--anchors-only`` precedent.
    """
    params = []
    for beat in beats:
        beat_id = beat.get("beat_id", "")
        script_body = beat.get("script_body", "")
        if not beat_id or not script_body or _beat_blocked(beat):
            continue
        word_count = len(script_body.split())
        params.append({
            "beat_id": beat_id,
            "poi_name": beat.get("poi_name"),
            "script_body": script_body,
            "duration_sec": beat.get("duration_sec") or max(30, int(word_count / 2.5)),
            "est_spoken_seconds": beat.get("est_spoken_seconds"),
            "sub_location": beat.get("sub_location"),
            "trigger_address": beat.get("trigger_address"),
        })
    result = session.run(
        """
        UNWIND $beats AS b
        MATCH (beat:NarrativeBeat {beat_id: b.beat_id})
        WITH b, beat,
             beat.script_body <> b.script_body AS words_changed,
             coalesce(beat.sub_location, '') <> coalesce(b.sub_location, '')
               OR coalesce(beat.trigger_address, '') <> coalesce(b.trigger_address, '')
               AS placement_changed
        WHERE words_changed OR placement_changed
        SET beat.script_body        = b.script_body,
            beat.duration_sec       = b.duration_sec,
            beat.est_spoken_seconds = b.est_spoken_seconds,
            beat.sub_location       = b.sub_location,
            beat.trigger_address    = b.trigger_address
        // The words are what the audio says: cleared ONLY when they changed.
        // A placement fix keeps the paid file — the sentence is still true.
        FOREACH (_ IN CASE WHEN words_changed THEN [1] ELSE [] END |
            SET beat.audio_url = '')
        RETURN count(beat) AS updated
        """,
        beats=params,
    )
    updated = result.single()["updated"]
    # A beat whose reviewed HOME changed moves its HAS_BEAT link — the story is
    # told at the place it is ABOUT, and a link left behind narrates it from the
    # wrong pin. Same-city only, matched by the beat's own recorded poi_name.
    rehomed = session.run(
        """
        UNWIND $beats AS b
        MATCH (beat:NarrativeBeat {beat_id: b.beat_id})
        MATCH (new:POI {name: b.poi_name, city_name: $city})
        // Body places legitimately share names ("Bench"); a beat never homes there.
        WHERE new.poi_role IS NULL OR new.poi_role <> 'body'
        WITH beat, collect(new) AS homes
        // A poi_name matching two POIs would double-link; refuse, never guess.
        WHERE size(homes) = 1
        WITH beat, homes[0] AS new
        OPTIONAL MATCH (old:POI)-[r:HAS_BEAT]->(beat)
        WITH beat, new, old, r WHERE old IS NULL OR old.id <> new.id
        DELETE r
        MERGE (new)-[:HAS_BEAT]->(beat)
        RETURN count(beat) AS rehomed
        """,
        beats=params,
        city=city_name,
    ).single()["rehomed"]
    return {"updated": updated, "rehomed": rehomed, "candidates": len(params)}


def _backfill_footprints(session, pois: list[dict], city_name: str, bbox: tuple) -> dict[str, int]:
    """Set ONLY ``trigger_radius`` and ``poi_role`` on existing POIs (match by the
    canonical name key). The footprint pass and the role review write poi-raw.json;
    a value changed there without a full upload leaves the graph deciding arrival
    from a footprint nobody reviewed — the drift class ``make db-parity`` names.
    """
    params = [
        {
            "name_key": canonical_name_key(poi["name"]),
            "city_name": city_name,
            "trigger_radius": poi.get("trigger_radius", 10),
            "poi_role": poi.get("poi_role"),
        }
        for poi in pois
        if poi.get("latitude") is not None
        and _in_city_bounds(float(poi["latitude"]), float(poi["longitude"]), bbox)
    ]
    result = session.run(
        """
        UNWIND $pois AS poi
        MATCH (p:POI {name_key: poi.name_key, city_name: poi.city_name})
        WHERE p.trigger_radius <> poi.trigger_radius
           OR coalesce(p.poi_role, '') <> coalesce(poi.poi_role, '')
        SET p.trigger_radius = poi.trigger_radius,
            p.poi_role       = poi.poi_role
        RETURN count(p) AS updated
        """,
        pois=params,
    )
    return {"updated": result.single()["updated"], "candidates": len(params)}


_BEAT_WRITE = """
UNWIND $beats AS b
OPTIONAL MATCH (p:POI {name: b.poi_name, city_name: $city})
// The same two guards as the re-home pass: a beat never homes on a
// body place (they legitimately share names), and a poi_name matching
// more than one POI is refused rather than fanned out.
WHERE p IS NULL OR p.poi_role IS NULL OR p.poi_role <> 'body'
WITH b, collect(p) AS homes
WITH b, [h IN homes WHERE h IS NOT NULL] AS homes
WITH b, homes WHERE size(homes) = 1
WITH b, homes[0] AS p
MERGE (beat:NarrativeBeat {beat_id: b.beat_id})
// audio_url is stamped ONCE on create; a re-deploy must never wipe live
// audio (expensive TTS output). All other fields re-sync from the repo.
ON CREATE SET beat.id = randomUUID(), beat.audio_url = ''
// Every row key except beat_id, poi_name and lens is SET here from the row
// key of the same name, and the change check compares exactly those keys.
SET beat.script_body        = b.script_body,
    beat.duration_sec       = b.duration_sec,
    beat.kid_friendly       = b.kid_friendly,
    beat.confidence         = b.confidence,
    beat.fact_status        = b.fact_status,
    beat.version            = b.version,
    beat.active_status      = b.active_status,
    beat.sub_location       = b.sub_location,
    beat.trigger_address    = b.trigger_address,
    beat.narrative_function = b.narrative_function,
    beat.beat_type          = b.beat_type,
    beat.emotional_register = b.emotional_register,
    beat.beat_length_class  = b.beat_length_class,
    beat.est_spoken_seconds = b.est_spoken_seconds,
    beat.entities           = b.entities,
    beat.subject_tag        = b.subject_tag,
    beat.physical_cues      = b.physical_cues,
    beat.pronunciation      = b.pronunciation,
    beat.source_passage     = b.source_passage,
    beat.source_chunk_slug  = b.source_chunk_slug,
    beat.key_claims         = b.key_claims
MERGE (p)-[:HAS_BEAT]->(beat)
RETURN count(beat) AS linked
"""

_TAG_WRITE = """
UNWIND $beats AS b
MATCH (beat:NarrativeBeat {beat_id: b.beat_id})
MATCH (l:Lens {name: b.lens})
MERGE (beat)-[:TAGGED_WITH]->(l)
RETURN count(*) AS tagged
"""

# The beat row keys _BEAT_WRITE does not copy onto the node: the MERGE key, the
# home it links from and the lens it is tagged with.
_BEAT_ROW_NOT_WRITTEN = {"beat_id", "poi_name", "lens"}


def _write_beat_chunk(tx, chunk: list[dict], city_name: str) -> int:
    return tx.run(_BEAT_WRITE, beats=chunk, city=city_name).single()["linked"]


def _write_tag_chunk(tx, chunk: list[dict]) -> int:
    return tx.run(_TAG_WRITE, beats=chunk).single()["tagged"]


def _graph_homes(session, city_name: str) -> dict[str, list[str]]:
    """Every name a beat can home on in this city: name -> ids of the non-body
    POIs that carry it, read the way _BEAT_WRITE resolves ``poi_name``."""
    homes: dict[str, list[str]] = {}
    for rec in session.run(
        """
        MATCH (p:POI {city_name: $city})
        WHERE p.poi_role IS NULL OR p.poi_role <> 'body'
        RETURN p.name AS name, p.id AS id
        """,
        city=city_name,
    ):
        homes.setdefault(rec["name"], []).append(rec["id"])
    return homes


def _graph_beats(session, beat_ids: list[str]) -> dict[str, list[dict]]:
    """beat_id -> each graph node with that id: its properties, the ids of the
    POIs it hangs from, and the lenses it is tagged with."""
    beats: dict[str, list[dict]] = {}
    for rec in session.run(
        """
        UNWIND $ids AS id
        MATCH (b:NarrativeBeat {beat_id: id})
        OPTIONAL MATCH (home:POI)-[:HAS_BEAT]->(b)
        WITH id, b, collect(DISTINCT home.id) AS homes
        OPTIONAL MATCH (b)-[:TAGGED_WITH]->(l:Lens)
        RETURN id, properties(b) AS props, homes, collect(DISTINCT l.name) AS lenses
        """,
        ids=beat_ids,
    ):
        beats.setdefault(rec["id"], []).append(
            {"props": dict(rec["props"]), "homes": set(rec["homes"]), "lenses": set(rec["lenses"])}
        )
    return beats


def _beat_changed(row: dict, nodes: list[dict], home_id: str) -> bool:
    """Whether writing ``row`` would change the graph: a new beat, a written field
    that differs, or a beat not yet hanging from the POI it resolves to."""
    return _changed([n["props"] for n in nodes], row, _BEAT_ROW_NOT_WRITTEN) or not all(
        home_id in n["homes"] for n in nodes
    )


def _upload_beats(driver, beats: list[dict], city_name: str) -> dict[str, int]:
    """Upload the NarrativeBeats whose graph record, home link or lens tag differs
    from the repo, in parallel chunks.

    ``city_name`` is the slug of the city being deployed (POI ``city_name`` in the
    graph). The beat→POI link is scoped to it so a beat only ever attaches to a
    POI in the SAME city: many POI names recur across cities (Chinatown, SoHo,
    Greenwich Village, Chelsea, Cleopatra's Needle all exist in both London and
    New York), and a name-only match would MERGE, say, a London beat onto the
    New York POI — seating London beats on NYC tours and breaking db_parity.
    """
    params = []
    pre_skipped = 0
    blocked = 0
    no_beat_id = 0

    for beat in beats:
        poi_name = beat.get("poi_name", "")
        script_body = beat.get("script_body", "")
        beat_id = beat.get("beat_id", "")

        if not beat_id:
            # MERGE (beat:NarrativeBeat {beat_id: ""}) has no uniqueness
            # constraint behind it, so a second empty-beat_id beat would MATCH
            # the first node and SET-overwrite it — silently collapsing distinct
            # beats into one. Refuse rather than corrupt: an empty beat_id is a
            # data defect the validate_beats gate should have caught upstream.
            no_beat_id += 1
            continue
        if not poi_name or not script_body:
            pre_skipped += 1
            continue
        if (beat.get("fact_check") or {}).get("status") in _BLOCKED_STATUSES:
            blocked += 1  # disputed beats never go live
            continue

        word_count = len(script_body.split())
        duration_sec = beat.get("duration_sec") or max(30, int(word_count / 2.5))
        kid_friendly = beat.get("kid_friendly", "yes")
        confidence = beat.get("confidence", "")
        fact_status = ""
        if isinstance(beat.get("fact_check"), dict):
            fact_status = beat["fact_check"].get("status", "")

        # Neo4j cannot store list[dict]; JSON-encode physical_cues (matches the engine's
        # _decode_physical_cues and the API's _encode_complex_props). entities is list[str]
        # and stores natively. Both are read back by src/tour/selection.py.
        raw_cues = beat.get("physical_cues")
        _cues_ok = (
            isinstance(raw_cues, list)
            and raw_cues
            and all(isinstance(c, dict) for c in raw_cues)
        )
        physical_cues = json.dumps(raw_cues) if _cues_ok else None

        params.append({
            "poi_name": poi_name,
            "beat_id": beat_id,
            "script_body": script_body,
            "duration_sec": duration_sec,
            "kid_friendly": kid_friendly,
            "confidence": confidence,
            "fact_status": fact_status,
            "lens": beat.get("lens", ""),
            "sub_location": beat.get("sub_location"),
            "trigger_address": beat.get("trigger_address"),
            "narrative_function": beat.get("narrative_function"),
            "beat_type": beat.get("beat_type"),
            "emotional_register": beat.get("emotional_register"),
            "beat_length_class": beat.get("beat_length_class"),
            "est_spoken_seconds": beat.get("est_spoken_seconds"),
            "entities": beat.get("entities") or [],
            "subject_tag": beat.get("subject_tag"),
            "physical_cues": physical_cues,
            "pronunciation": beat.get("pronunciation"),
            "version": 1,
            "active_status": "active",
            **_provenance_fields(beat),
        })

    rows = sorted(last_per_key(params, lambda r: r["beat_id"]), key=lambda r: r["poi_name"])
    with driver.session(database=get_database()) as session:
        homes = _graph_homes(session, city_name)
        graph = _graph_beats(session, [r["beat_id"] for r in rows])

    # A beat is written only where the guards in _BEAT_WRITE would link it: its
    # poi_name names exactly one non-body POI of this city in the graph.
    linkable = [r for r in rows if len(homes.get(r["poi_name"], [])) == 1]
    changed = [
        r
        for r in linkable
        if _beat_changed(r, graph.get(r["beat_id"], []), homes[r["poi_name"]][0])
    ]
    t0 = time.time()
    write_in_chunks(driver, changed, partial(_write_beat_chunk, city_name=city_name))
    beats_seconds = time.time() - t0

    # The tag pass reaches every beat node that exists once the beats are
    # written, linked this run or not, and sends only the missing tags.
    linked_ids = {r["beat_id"] for r in linkable}
    taggable = [
        r for r in rows if r["lens"] and (r["beat_id"] in graph or r["beat_id"] in linked_ids)
    ]
    untagged = [
        r
        for r in taggable
        if not all(r["lens"] in n["lenses"] for n in graph.get(r["beat_id"], [{"lenses": ()}]))
    ]
    t0 = time.time()
    # Every tag chunk links onto the same few Lens nodes, so chunks sent at once
    # only wait on each other's locks; they go one at a time.
    write_in_chunks(driver, untagged, _write_tag_chunk, workers=1)
    tags_seconds = time.time() - t0

    return {
        "linked": len(linkable),
        "changed": len(changed),
        "orphaned": len(params) - len(linkable) + pre_skipped,
        "tagged": len(taggable),
        "tags_changed": len(untagged),
        "blocked": blocked,
        "no_beat_id": no_beat_id,
        "beats_seconds": beats_seconds,
        "tags_seconds": tags_seconds,
    }


@abort_on_connection_error
def main() -> None:
    allow_cloud = "--allow-cloud" in sys.argv
    # Guard BEFORE any driver/session is opened: an accidental cloud-mode run
    # must not reach create_driver() (protects even when Aura is paused).
    _assert_upload_target_allowed(allow_cloud)

    # First non-flag arg is the city slug (flags like --allow-cloud/--provenance-only/
    # --anchors-only must never be misread as a city).
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    city_slug = positional[0] if positional else "paris"
    if city_slug not in CITY_BBOX:
        sys.exit(
            f"Unknown city '{city_slug}'. Known: {', '.join(sorted(CITY_BBOX))}. "
            f"Register it first (onboarding panel / src.city_registry.register_city → "
            f"src/cities.json)."
        )
    poi_file, beats_file = _city_paths(city_slug)
    bbox = CITY_BBOX[city_slug]

    db = get_database()
    # WHERE THE WRITE LANDS, from the same signal the cloud guard refuses on — the URI's
    # host. `get_database()` returns the database NAME, which a local instance sets too
    # ("neo4j"), so reading it made every local run announce itself as cloud.
    host = urlparse(os.getenv("NEO4J_URI", "")).hostname or ""
    db_label = "local" if host in _LOCAL_HOSTS else f"cloud ({db or host})"
    provenance_only = "--provenance-only" in sys.argv
    anchors_only = "--anchors-only" in sys.argv
    beats_only = "--beats-only" in sys.argv
    footprints_only = "--footprints-only" in sys.argv
    if provenance_only:
        mode = "PROVENANCE BACKFILL"
    elif anchors_only:
        mode = "ANCHOR BACKFILL"
    elif beats_only:
        mode = "BEAT TEXT BACKFILL"
    elif footprints_only:
        mode = "FOOTPRINT BACKFILL"
    else:
        mode = f"{city_slug.upper()} DATA UPLOAD"
    print(f"\n{'='*60}")
    print(f"  {mode} → Neo4j [{db_label}]")
    print(f"{'='*60}\n")

    if footprints_only:
        pois = _load_json(poi_file)
        driver = create_driver()
        try:
            with driver.session(database=db) as session:
                stats = _backfill_footprints(session, pois, city_slug, bbox)
            print(
                f"  {stats['updated']} of {stats['candidates']} places re-synced their "
                f"footprint and role"
            )
        finally:
            driver.close()
        return

    if anchors_only:
        # POI data: the beats gate below has nothing to say about it, and coupling
        # the two would let an unrelated beats defect block an anchor backfill.
        pois = _load_json(poi_file)
        driver = create_driver()
        try:
            with driver.session(database=db) as session:
                stats = _backfill_anchors(session, pois, city_slug)
            print(
                f"  {stats['updated']} of {stats['candidates']} reviewed places now carry "
                f"their anchors in the graph"
            )
        finally:
            driver.close()
        return

    # Every integrity gate (grounding, verification-freshness, uniqueness,
    # status vocab) must pass before we touch the database. Fail fast, pre-connect.
    print("  [0/5] Validating beats (validate_beats gate)...")
    _assert_beats_valid(beats_file)
    print("         OK")

    if beats_only:
        # Text fixes onto a live graph: only the changed beats, only their audio
        # cleared. Runs AFTER the validate gate — changed words are still words.
        beats = _load_json(beats_file)
        driver = create_driver()
        try:
            with driver.session(database=db) as session:
                stats = _backfill_beat_texts(session, beats, city_slug)
            print(
                f"  {stats['updated']} beat(s) re-synced (of {stats['candidates']} "
                f"uploadable), {stats['rehomed']} re-homed; changed words' audio is "
                f"cleared for re-voicing"
            )
        finally:
            driver.close()
        return

    if provenance_only:
        # Provenance backfill: ONLY the three provenance fields, matched by
        # beat_id. Never the full upload path (it rewrites every differing field).
        beats = _load_json(beats_file)
        driver = create_driver()
        try:
            with driver.session(database=db) as session:
                print(f"  Backfilling provenance onto {len(beats)} source beats...")
                stats = _backfill_provenance(session, beats)
                kc = session.run(
                    "MATCH (b:NarrativeBeat) WHERE b.key_claims IS NOT NULL "
                    "RETURN count(b) AS c"
                ).single()["c"]
                total = session.run(
                    "MATCH (b:NarrativeBeat) RETURN count(b) AS c"
                ).single()["c"]
            print(
                f"  {stats['updated']} updated of {stats['candidates']} candidates; "
                f"{kc}/{total} beats in the graph now carry key_claims"
            )
        finally:
            driver.close()
        return

    pois = _load_json(poi_file)
    beats = _load_json(beats_file)
    print(f"  Source: {len(pois)} POIs, {len(beats)} beats\n")

    beat_lenses = {b["lens"] for b in beats if b.get("lens")}
    print(f"  Lenses referenced by beats: {len(beat_lenses)}")

    driver = create_driver()
    try:
        # 1. Schema
        print("\n  [1/5] Applying schema constraints & indexes...")
        t0 = time.time()
        apply_all(driver)
        print(f"         Done ({time.time()-t0:.1f}s)")

        # 2. Lenses
        print("  [2/5] Seeding lenses...")
        t0 = time.time()
        seed_lenses(driver)
        with driver.session(database=db) as session:
            lens_count = _ensure_lenses(session, beat_lenses)
        print(f"         {lens_count} lenses ensured ({time.time()-t0:.1f}s)")

        # 3. POIs, then body places
        print(f"  [3/5] Uploading {len(pois)} POIs...")
        t0 = time.time()
        poi_stats = _upload_pois(driver, pois, city_slug, bbox)
        print(
            f"         {poi_stats['changed']} of {poi_stats['created']} POIs changed, "
            f"{poi_stats['skipped']} skipped (null coords), {poi_stats['out_of_bounds']} "
            f"skipped (out of bounds) ({time.time()-t0:.1f}s)"
        )
        t0 = time.time()
        body_stats = _upload_body_places(driver, city_slug, bbox)
        print(
            f"         {body_stats['changed']} of {body_stats['created']} body places "
            f"(toilets/benches) changed, {body_stats['out_of_bounds']} skipped "
            f"(out of bounds) ({time.time()-t0:.1f}s)"
        )

        # 4. Beats + relationships
        print(f"  [4/5] Uploading {len(beats)} beats + linking...")
        t0 = time.time()
        beat_stats = _upload_beats(driver, beats, city_slug)
        print(
            f"         {beat_stats['changed']} of {beat_stats['linked']} linked beats changed, "
            f"{beat_stats['orphaned']} orphaned, {beat_stats['blocked']} blocked (disputed), "
            f"{beat_stats['no_beat_id']} skipped (no beat_id) "
            f"(write {beat_stats['beats_seconds']:.1f}s)"
        )
        print(
            f"         {beat_stats['tags_changed']} of {beat_stats['tagged']} lens tags changed "
            f"({beat_stats['tags_seconds']:.1f}s); beats step {time.time()-t0:.1f}s"
        )

        with driver.session(database=db) as session:
            # 5. Summary
            print("  [5/5] Verifying counts...")
            nodes = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            rels = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
            poi_count = session.run("MATCH (n:POI) RETURN count(n) AS c").single()["c"]
            beat_count = session.run("MATCH (n:NarrativeBeat) RETURN count(n) AS c").single()["c"]
            lens_count = session.run("MATCH (n:Lens) RETURN count(n) AS c").single()["c"]

        print(f"\n{'='*60}")
        print("  UPLOAD COMPLETE")
        print(f"  Nodes: {nodes} ({poi_count} POIs, {beat_count} beats, {lens_count} lenses)")
        print(f"  Relationships: {rels}")
        print(f"{'='*60}\n")

    finally:
        driver.close()


if __name__ == "__main__":
    main()
