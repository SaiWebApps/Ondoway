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
from pathlib import Path
from urllib.parse import urlparse

from scripts.validate_beats import _record_shape
from src import city_registry
from src.api.models.nodes import canonical_name_key
from src.connection import abort_on_connection_error, create_driver, get_database
from src.schema.constraints import apply_all
from src.seed.lenses import seed_lenses

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _assert_upload_target_allowed(allow_cloud: bool) -> None:
    """Refuse an upload against a non-local (Aura) target unless --allow-cloud.

    The upload path plain-SETs live fields (e.g. ``beat.audio_url = ''``), so an
    accidental run while ``.env`` is in cloud mode overwrites production. This
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
            f"This path overwrites live fields (audio_url='' etc.). Point NEO4J_URI at "
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


def _city_paths(city_slug: str, data_root: Path | None = None) -> tuple[Path, Path]:
    # Hermetic-aware data root (``$ONBOARD_DATA_ROOT`` when set, else
    # ``<repo>/data``) so a hermetic onboard's tmp corpus is what the deploy reads;
    # unset → <repo>/data/{slug}, byte-identical to the prior hardcoded path. An
    # explicit `data_root` (the ingest front door's INGEST_DATA_ROOT) wins.
    data_dir = (data_root or city_registry.onboard_data_root()) / city_slug
    return data_dir / "poi-raw.json", data_dir / "beats.json"

# fact_check.status values that must never reach the live database.
_BLOCKED_STATUSES = {"disputed"}


def _load_json(path: Path) -> list[dict]:
    with open(path) as f:
        return json.load(f)


def _in_city_bounds(lat: float, lon: float, bbox: tuple = PARIS_BBOX) -> bool:
    return bbox[0] <= lat <= bbox[1] and bbox[2] <= lon <= bbox[3]


def _beat_blocked(beat: dict) -> bool:
    """True if a beat must NOT be uploaded: missing essentials, a legacy
    `disputed` fact-check status, or — on a new-shape record — `review.held`
    (the new-shape analogue of the fact-check block: a held story waits for
    a decision, it is never served). Neither requires `verified`; uploading
    unverified legacy beats is a launch-policy decision left to the operator."""
    view = _export_view(beat)
    if not view.get("poi_name") or not view.get("script_body"):
        return True
    if _record_shape(beat) == "new":
        # A held story waits for a decision; a practicalities beat (prices,
        # hours, tickets — owner ruling 2026-09-13) waits for an engine
        # channel that never voices it in the bare present. Neither is served.
        if beat.get("beat_type") == "practicalities":
            return True
        return bool((beat.get("review") or {}).get("held"))
    return (beat.get("fact_check") or {}).get("status") in _BLOCKED_STATUSES


def _beat_length_class(duration_sec: int) -> str:
    """§2: `micro` <8 s, `seasoning` <32 s, `mid` <80 s, else `anchor`."""
    if duration_sec < 8:
        return "micro"
    if duration_sec < 32:
        return "seasoning"
    if duration_sec < 80:
        return "mid"
    return "anchor"


def _assert_beats_valid(beats_path: Path) -> None:
    """Run the full validate_beats gate before any DB write (AC-9). Aborts the
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


def _upload_pois(session, pois: list[dict], city_name: str, bbox: tuple) -> dict[str, int]:
    """Upload POI nodes with spatial points via batched UNWIND. Returns stats."""
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
            # Canonical dedup key (defect 3), computed identically to create_node
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
            # is the sentence that argues for both. Produced by the capacity pass
            # (.claude/commands/poi-visit-duration.md).
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
            # The planner's clock (redesign 6.1/6.7). `opening_hours` is a week
            # table in poi-raw.json; Neo4j cannot store nested dicts, so it is
            # JSON-encoded here — the `physical_cues` precedent — and decoded by
            # the clock filter. Same no-defaults rule as the visit fields above:
            # absence stays absent (`SET x = null` removes the property), so a
            # POI the pass never reached is indistinguishable from nothing, not
            # from a place with known hours.
            "opening_hours": (
                json.dumps(poi["opening_hours"], ensure_ascii=False)
                if isinstance(poi.get("opening_hours"), dict)
                else None
            ),
            "opening_hours_source": poi.get("opening_hours_source"),
            "opening_hours_basis": (poi.get("opening_hours_basis") or "").strip() or None,
            "place_category": (poi.get("place_category") or "").strip() or None,
            # Place judgements (redesign row 6.4, plan S2.6) — three AI verdicts
            # with no external source, same no-defaults rule as the fields
            # above: absence stays absent, never a made-up affordance.
            "children_can_run": poi.get("children_can_run"),
            "sit_and_talk": poi.get("sit_and_talk"),
            "good_after_dark": poi.get("good_after_dark"),
            "judgement_basis": (poi.get("judgement_basis") or "").strip() or None,
            # The queue (redesign row 6.5, plan S3.4) — the wait BEFORE entering,
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
            # The reviewed anchors (Phase 7 S7.7 B, design §5.6 segments): a list of
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

    result = session.run(
        """
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
            // Visit capacity. This SET list and the param dict above are TWO
            // hardcoded property lists that must agree: a key added to one and
            // not the other reaches the graph as nothing, with no error. If you
            // add a property, add it in both places.
            p.typical_duration_min = poi.typical_duration_min,
            p.visit_seconds_inside = poi.visit_seconds_inside,
            p.visit_basis          = poi.visit_basis,
            p.opening_hours        = poi.opening_hours,
            p.opening_hours_source = poi.opening_hours_source,
            p.opening_hours_basis  = poi.opening_hours_basis,
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
        """,
        pois=params,
    )
    created = result.single()["total"]
    return {"created": created, "skipped": skipped, "out_of_bounds": out_of_bounds}


def _upload_body_places(session, city_slug: str, bbox: tuple) -> dict[str, int]:
    """Upload toilets/benches from ``data/{slug}/body-places.json`` as
    ``poi_role="body"`` POI nodes — the S2.5 OSM pass
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
        return {"created": 0, "skipped": 0, "out_of_bounds": 0}
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
            "lat": float(lat),
            "lon": float(lng),
            "poi_role": "body",
            # A body stop carries ZERO NARRATION but real minutes (plan S2.5:
            # "seated as a zero-narration stop" — narration, not duration).
            # Nadia's toilet stop and Rosemary's bench sits are both real
            # time the day spends; these are conservative fixed minutes, not
            # narration-derived, since a body place has no beats to price by.
            "typical_duration_min": 5 if kind == "toilet" else 8,
        })

    result = session.run(
        """
        UNWIND $places AS place
        MERGE (p:POI {name_key: place.name_key, city_name: place.city_name})
        ON CREATE SET p.id = randomUUID()
        SET p.name              = place.name,
            p.short_description = "",
            p.location           = point({latitude: place.lat, longitude: place.lon, srid: 4326}),
            p.importance_tier    = 1,
            p.trigger_radius     = 10,
            p.kid_friendly       = "yes",
            p.name_variations    = [],
            p.poi_role           = place.poi_role,
            p.typical_duration_min = place.typical_duration_min
        RETURN count(p) AS total
        """,
        places=params,
    )
    created = result.single()["total"]
    skipped = len(records) - len(params) - out_of_bounds
    return {"created": created, "skipped": skipped, "out_of_bounds": out_of_bounds}


def _provenance_fields(beat: dict) -> dict:
    """The three VERIFY provenance/faithfulness fields (M7 / Phase 4 Step 4.0).

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

    The full upload path plain-SETs fields like ``beat.audio_url = ''``, so
    re-running it against a live graph is destructive. Backfilling provenance
    onto an already-uploaded graph must therefore never go through it.
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


def _export_view(beat: dict) -> dict:
    """The §2 graph export: a new-shape record (`claims` + `narration`, the
    same rule as scripts/validate_beats.py — never keyed on `script_body`)
    seen through the legacy field contract the tour engine reads. A legacy
    record is returned as is, so both real cities keep publishing exactly
    as today until the slice-10 swap. The engine changes nothing.
    """
    if _record_shape(beat) != "new":
        return beat
    narration = beat.get("narration") or {}
    view = dict(beat)
    view["script_body"] = narration.get("text") or ""
    view["key_claims"] = [
        c["text"] for c in beat.get("claims") or [] if c.get("status") == "resolved"
    ]
    view["beat_length_class"] = _beat_length_class(int(beat.get("duration_sec") or 0))
    view["lenses"] = list(beat.get("lenses") or [])
    # The author's enrichment is unjudged free text and the engine VOICES
    # pronunciation and physical cues (the proof-chunk panel, 2026-09-13):
    # a new-shape record exports neither until they are judged.
    view["pronunciation"] = None
    view["physical_cues"] = []
    return view


def _lenses_of(beat: dict) -> list[str]:
    """The lens slugs a record tags: a new-shape `lenses` list, or the legacy
    single `lens` as a one-item list (spec §2: one relationship per entry)."""
    lenses = beat.get("lenses")
    if lenses:
        return list(lenses)
    return [beat["lens"]] if beat.get("lens") else []


def _withdraw_absent(session, city_name: str, published_ids: set[str]) -> int:
    """§6 converge: every beat of `city_name` this publish did not MERGE —
    a beat_id the file no longer names, or a beat with no beat_id at all
    (the four seeded narratives of src/seed/narratives.py) — becomes
    `active_status = 'withdrawn'`. HAS_BEAT is kept, so a re-publish that
    names the beat again restores it through the ordinary MERGE + SET
    'active'. Returns how many beats this call withdrew (already-withdrawn
    beats are not counted again). The city is the POI's: a NarrativeBeat
    carries no city of its own, and a beat no POI links is one the engine
    never loads.
    """
    result = session.run(
        """
        MATCH (:POI {city_name: $city})-[:HAS_BEAT]->(b:NarrativeBeat)
        WHERE (b.beat_id IS NULL OR NOT b.beat_id IN $ids)
          AND coalesce(b.active_status, 'active') <> 'withdrawn'
        SET b.active_status = 'withdrawn'
        RETURN count(DISTINCT b) AS withdrawn
        """,
        city=city_name,
        ids=sorted(published_ids),
    )
    return result.single()["withdrawn"]


def publish_beats(session, beats: list[dict], city_name: str) -> dict:
    """Make the graph's beats for `city_name` match `beats` (spec §6): MERGE
    every publishable beat, then withdraw every beat of the city this call
    did not publish. The stats are `_upload_beats`' plus `withdrawn`."""
    stats = _upload_beats(session, beats, city_name)
    stats["withdrawn"] = _withdraw_absent(session, city_name, set(stats["published_ids"]))
    return stats


def _upload_beats(session, beats: list[dict], city_name: str) -> dict:
    """Upload NarrativeBeat nodes and link to POIs + Lenses via batched UNWIND.

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

    for beat in map(_export_view, beats):
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
        if _beat_blocked(beat):
            blocked += 1  # disputed (legacy) or held (new-shape) beats never go live
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
            "lenses": _lenses_of(beat),
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
            **_provenance_fields(beat),
        })

    result = session.run(
        """
        UNWIND $beats AS b
        OPTIONAL MATCH (p:POI {name: b.poi_name, city_name: $city})
        WITH b, p WHERE p IS NOT NULL
        MERGE (beat:NarrativeBeat {beat_id: b.beat_id})
        // audio_url is stamped ONCE on create; a re-deploy must never wipe live
        // audio (expensive TTS output). All other fields re-sync from the repo.
        ON CREATE SET beat.id = randomUUID(), beat.audio_url = ''
        SET beat.script_body    = b.script_body,
            beat.duration_sec   = b.duration_sec,
            beat.kid_friendly   = b.kid_friendly,
            beat.confidence     = b.confidence,
            beat.fact_status    = b.fact_status,
            beat.version        = 1,
            beat.active_status  = 'active',
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
        RETURN collect(beat.beat_id) AS ids
        """,
        beats=params,
        city=city_name,
    )
    published_ids = list(result.single()["ids"])
    linked = len(published_ids)
    orphaned = len(params) - linked + pre_skipped

    taggable = [b for b in params if b["lenses"]]
    if taggable:
        # One TAGGED_WITH per entry: a legacy `lens` is a one-item list, a
        # new-shape `lenses` list tags each (spec §2).
        tag_result = session.run(
            """
            UNWIND $beats AS b
            MATCH (beat:NarrativeBeat {beat_id: b.beat_id})
            UNWIND b.lenses AS lens_name
            MATCH (l:Lens {name: lens_name})
            MERGE (beat)-[:TAGGED_WITH]->(l)
            RETURN count(*) AS tagged
            """,
            beats=taggable,
        )
        tagged = tag_result.single()["tagged"]
    else:
        tagged = 0

    return {
        "linked": linked,
        "orphaned": orphaned,
        "tagged": tagged,
        "blocked": blocked,
        "no_beat_id": no_beat_id,
        "published_ids": published_ids,
    }


def converge(driver, city_slug: str, *, data_root: Path | None = None) -> dict:
    """Make the graph match the city's files (spec §6): validate the beats
    file, ensure schema + lenses, MERGE the POIs and body places, then
    `publish_beats` — MERGE every publishable beat and withdraw every beat
    of the city the file no longer names. `data_root` overrides the
    hermetic-aware default (`_city_paths`) for the front door, which
    publishes from `INGEST_DATA_ROOT`. Returns the per-step stats (the
    published id list is left out: a city has thousands). Prints progress
    the way the CLI always has; the caller owns the driver.
    """
    if city_slug not in CITY_BBOX:
        raise KeyError(city_slug)
    poi_file, beats_file = _city_paths(city_slug, data_root)
    bbox = CITY_BBOX[city_slug]
    db = get_database()

    # AC-9: every integrity gate (grounding, verification-freshness, uniqueness,
    # status vocab) must pass before we touch the database. Fail fast, pre-connect.
    print("  [0/5] Validating beats (validate_beats gate)...")
    _assert_beats_valid(beats_file)
    print("         OK")

    pois = _load_json(poi_file)
    beats = _load_json(beats_file)
    print(f"  Source: {len(pois)} POIs, {len(beats)} beats\n")

    beat_lenses = {lens for b in beats for lens in _lenses_of(_export_view(b))}
    print(f"  Lenses referenced by beats: {len(beat_lenses)}")

    # 1. Schema
    print("\n  [1/5] Applying schema constraints & indexes...")
    t0 = time.time()
    apply_all(driver)
    print(f"         Done ({time.time()-t0:.1f}s)")

    with driver.session(database=db) as session:
        # 2. Lenses
        print("  [2/5] Seeding lenses...")
        t0 = time.time()
        seed_lenses(driver)
        lens_count = _ensure_lenses(session, beat_lenses)
        print(f"         {lens_count} lenses ensured ({time.time()-t0:.1f}s)")

        # 3. POIs
        print(f"  [3/5] Uploading {len(pois)} POIs...")
        t0 = time.time()
        poi_stats = _upload_pois(session, pois, city_slug, bbox)
        print(
            f"         {poi_stats['created']} created, {poi_stats['skipped']} skipped "
            f"(null coords), {poi_stats['out_of_bounds']} skipped (out of bounds) "
            f"({time.time()-t0:.1f}s)"
        )
        body_stats = _upload_body_places(session, city_slug, bbox)
        if body_stats["created"] or body_stats["out_of_bounds"]:
            print(
                f"         + {body_stats['created']} body places (toilets/benches) "
                f"created, {body_stats['out_of_bounds']} skipped (out of bounds)"
            )

        # 4. Beats + relationships, then the §6 withdraw step
        print(f"  [4/5] Publishing {len(beats)} beats + linking...")
        t0 = time.time()
        beat_stats = publish_beats(session, beats, city_slug)
        print(
            f"         {beat_stats['linked']} linked, {beat_stats['orphaned']} orphaned, "
            f"{beat_stats['tagged']} tagged, {beat_stats['blocked']} blocked (disputed/held), "
            f"{beat_stats['no_beat_id']} skipped (no beat_id), "
            f"{beat_stats['withdrawn']} withdrawn (absent from the file) "
            f"({time.time()-t0:.1f}s)"
        )

        # 5. Summary
        print("  [5/5] Verifying counts...")
        nodes = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        rels = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
        poi_count = session.run("MATCH (n:POI) RETURN count(n) AS c").single()["c"]
        beat_count = session.run("MATCH (n:NarrativeBeat) RETURN count(n) AS c").single()["c"]
        lens_count = session.run("MATCH (n:Lens) RETURN count(n) AS c").single()["c"]
        withdrawn_total = session.run(
            "MATCH (:POI {city_name: $city})-[:HAS_BEAT]->(b:NarrativeBeat) "
            "WHERE b.active_status = 'withdrawn' RETURN count(DISTINCT b) AS c",
            city=city_slug,
        ).single()["c"]

    print(f"\n{'='*60}")
    print("  UPLOAD COMPLETE")
    print(f"  Nodes: {nodes} ({poi_count} POIs, {beat_count} beats, {lens_count} lenses)")
    print(f"  Relationships: {rels}")
    print(f"  Withdrawn beats in {city_slug}: {withdrawn_total}")
    print(f"{'='*60}\n")

    return {
        "pois": poi_stats,
        "body_places": body_stats,
        "beats": {k: v for k, v in beat_stats.items() if k != "published_ids"},
        "withdrawn_total": withdrawn_total,
        "graph": {
            "nodes": nodes,
            "relationships": rels,
            "pois": poi_count,
            "beats": beat_count,
            "lenses": lens_count,
        },
    }


@abort_on_connection_error
def main() -> None:
    allow_cloud = "--allow-cloud" in sys.argv
    # Guard BEFORE any driver/session is opened: an accidental cloud-mode run
    # must not reach create_driver() (protects even when Aura is paused).
    _assert_upload_target_allowed(allow_cloud)

    # First non-flag arg is the city slug (flags like --allow-cloud/--provenance-only
    # must never be misread as a city).
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    city_slug = positional[0] if positional else "paris"
    if city_slug not in CITY_BBOX:
        sys.exit(
            f"Unknown city '{city_slug}'. Known: {', '.join(sorted(CITY_BBOX))}. "
            f"Register it first (onboarding panel / src.city_registry.register_city → "
            f"src/cities.json)."
        )

    db = get_database()
    db_label = f"cloud ({db})" if db else "local"
    provenance_only = "--provenance-only" in sys.argv
    mode = "PROVENANCE BACKFILL" if provenance_only else f"{city_slug.upper()} DATA UPLOAD"
    print(f"\n{'='*60}")
    print(f"  {mode} → Neo4j [{db_label}]")
    print(f"{'='*60}\n")

    if provenance_only:
        # Step 4.0 backfill: ONLY the three provenance fields, matched by
        # beat_id. Never the full upload path (it plain-SETs audio_url='').
        _poi_file, beats_file = _city_paths(city_slug)
        print("  [0/5] Validating beats (validate_beats gate)...")
        _assert_beats_valid(beats_file)
        print("         OK")
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

    driver = create_driver()
    try:
        converge(driver, city_slug)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
