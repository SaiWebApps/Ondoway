"""Business logic for trip generation — POI search, beat matching, and scheduling."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from src.tour.contract import END_B_SENTINEL_PREFIX
from src.tour.options import dominant_lens
from src.tour.placement import anchor_of, place_segments
from src.tour.render_md import stop_close_text, stop_leg_text, stop_narration_text

if TYPE_CHECKING:
    from neo4j import ManagedTransaction as Transaction
    from neo4j import Session

    from src.tour.contract import Anchor, BeatRef, Script, ScriptPOI
    from src.tour.placement import StopTrigger


def route_script_to_stops(
    selected_pois: list[ScriptPOI] | tuple[ScriptPOI, ...],
    beats_by_id: dict[str, BeatRef],
    clocks: Mapping[str, str],
    *,
    script: Script | None = None,
    extra_by_poi: dict[str, tuple[str, ...]] | None = None,
    triggers: Mapping[str, StopTrigger] | None = None,
    anchors: Mapping[str, tuple[Anchor, ...]] | None = None,
    prefer_lenses: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """Adapt the engine's ordered ScriptPOIs into the stop dicts create_trip_with_stops expects.

    Pure function — no DB, no engine run. Each ScriptPOI becomes one stop in route
    order; ALL of its beats are kept (`beat_ids`) with `primary_beat_id` = the first
    (so single-beat read paths still work); the per-stop lens is the dominant lens of
    its beats (computed, not fabricated). Per the M0b design (its spec tree is
    retired — git history).

    THE CLOCK IS NOT SPELLED HERE (Phase 5 S5.10). ``clocks`` is poi id -> "HH:MM"
    from THE one expression (`src.tour.contingency.stop_clocks`, walks and priced
    visits); until then this adapter ran a second, walk-less clock (dwell only)
    that the phone showed as arrival times. A stop the caller did not clock (a
    dateless request with no start) reads "".

    Phase 1 (Step 1.2): when ``script`` is passed, each stop also carries its
    ``narration`` — the stitched per-stop text (cold-open, transit glue, beats,
    closing) from ``stop_narration_text``, the text Phase 1 hands to TTS. Omitted
    (``""``) when no script is given, so existing callers stay back-compatible.

    THE FOOTPRINT IS NOT SPELLED HERE EITHER (Phase 7 S7.3; design §5.6). ``triggers``
    is poi id -> the stop's placed trigger geometry from THE one rule
    (`src.tour.placement.place_stops`, the same shape as ``clocks`` above); each stop
    carries it as a plain dict (``trigger``) so the item stores it and the wire model
    validates it. A stop the caller did not place reads None — no geometry.
    """
    # Phase 7 S7.7 (B) (design §5.6 "segments"; W7.2 R4): a marquee's reviewed anchors
    # — ``anchors``, poi id -> its anchors from THE one rule (`place_anchors(route)`,
    # the `triggers` shape) — cut its STORY into chapters: the resolver says which
    # anchor a sentence is told at, the story keeps the rest, and each chapter rides
    # the stop as its own piece (``segments``). No anchors: one uncut story, None.
    resolve = (
        anchor_of(selected_pois, beats_by_id, anchors) if script is not None and anchors else None
    )
    narration_by_stop = (
        stop_narration_text(script, anchor_of=resolve) if script is not None else {}
    )
    segments_by_stop = (
        place_segments(script, resolve) if script is not None and resolve is not None else {}
    )
    # Phase 6 S6.4: the stop's CLOSE travels beside its narration (design §5.3).
    close_by_stop = stop_close_text(script) if script is not None else {}
    # Phase 7 S7.7 (design §5.6 C7; plan defect 7): the stop's LEG piece — its walking
    # line, a reflection on that leg, the walk-past one-liners — travels beside the
    # STORY, its own file played on the leg; the story opens on the stop's own first
    # sentence. None when the leg carries nothing. The SAME resolver the story is cut
    # by is passed here (W7.11 defect 17): a sentence belonging to a reviewed anchor is
    # told AT that anchor and never on the walk toward it.
    leg_by_stop = stop_leg_text(script, anchor_of=resolve) if script is not None else {}
    extras = extra_by_poi or {}
    placed = triggers or {}

    stops: list[dict[str, Any]] = []
    for idx, sp in enumerate(selected_pois):
        beat_ids = list(sp.beat_ids)
        duration_min = max(1, round(sp.dwell_seconds / 60)) if sp.dwell_seconds else 1
        time_str = clocks.get(sp.id, "")

        stops.append(
            {
                "sort_order": idx + 1,
                "poi_id": sp.id,
                "poi_name": sp.name,
                "lat": sp.lat,
                "lng": sp.lng,
                "beat_ids": beat_ids,
                "extra_beat_ids": list(extras.get(sp.id, ())),
                "primary_beat_id": beat_ids[0] if beat_ids else None,
                "lens_name": dominant_lens(sp.beat_ids, beats_by_id, prefer=prefer_lenses),
                "duration_min": duration_min,
                "importance_tier": sp.tier,
                "start_time": time_str,
                "area": sp.area,
                "dwell_seconds": sp.dwell_seconds,
                "narration": narration_by_stop.get(idx, ""),
                "close_text": close_by_stop.get(idx),
                "leg_narration": leg_by_stop.get(idx),
                # S1.M4: the stop this leg was written FROM. A leg line names both its
                # ends, so it is true of one pair only; a replan re-orders the same
                # stops, and this is what lets it tell a line that still fits from one
                # describing a walk nobody is taking. Recorded for every stop, whether
                # or not its leg carries words today.
                "leg_from_poi_id": selected_pois[idx - 1].id if idx else None,
                "trigger": placed[sp.id].model_dump() if sp.id in placed else None,
                "segments": (
                    [seg.model_dump() for seg in segments_by_stop[idx]]
                    if idx in segments_by_stop
                    else None
                ),
            }
        )

    return stops


def create_trip_with_stops(
    session: Session,
    trip_name: str,
    profile_id: str,
    start_date: str,
    end_date: str,
    stops: list[dict[str, Any]],
    tour_input_json: str | None = None,
    options_json: str | None = None,
) -> dict[str, Any]:
    """Create Trip node and ItineraryItem nodes in a single write transaction.

    The Trip CREATE and every ItineraryItem CREATE run inside one
    ``session.execute_write`` unit of work, so a mid-loop failure (e.g. a stop
    citing a beat that is not in the graph) rolls the WHOLE thing back — the
    profile is never left with a phantom Trip whose stop list is silently
    truncated.

    Creates:
    - Trip node with UUID, name, dates, status='planning'
    - Profile -[:IS_CAPTAIN_OF]-> Trip
    - Profile -[:IS_CREW_OF]-> Trip for every family co-member of the captain
      (ADR 0005: a trip's crew derives from family membership; the captain is
      never their own crew)
    - For each stop: ItineraryItem with HAS_STOP, ASSIGNED_TO, AT_POI, and one
      PLAYS_BEAT edge per beat in the stop's `beat_ids`. The item stores
      `beat_ids` (engine narration order), `primary_beat_id` (= beat_ids[0],
      for single-beat read paths), the stop's dominant `lens_name` (nullable),
      and the stitched per-stop `narration` (Phase 1, Step 1.2; nullable — a
      null/absent value is simply not stored). Per the M0b design (its spec
      tree is retired — git history).

    Phase 4 (Step 4.6): ``tour_input_json`` (the RESOLVED engine input incl.
    lenses + start_time) and ``options_json`` (per flavour, the ORDERED poi id
    list; index+1 = the option number in ``route_id``) persist on the Trip so
    POST /trips/{id}/compose can rebuild the user's PICK without re-running
    selection — corpus/routing drift between generate and compose must never
    swap the picked route. Nulls are simply not stored.
    """
    trip_id = str(uuid.uuid4())

    # Build the Cypher for creating the trip and linking to profile
    create_query = """
        MATCH (profile:Profile {id: $profile_id})
        CREATE (trip:Trip {
            id: $trip_id,
            name: $trip_name,
            start_date: $start_date,
            end_date: $end_date,
            status: 'planning',
            tour_input_json: $tour_input_json,
            options_json: $options_json,
            created_at: datetime()
        })
        MERGE (profile)-[:IS_CAPTAIN_OF]->(trip)
        RETURN trip.id AS trip_id
    """
    # The crew derives from the captain's FAMILY at creation time (ADR 0005:
    # `IS_CREW_OF` finally read and written by production). Same transaction as
    # the trip CREATE, so a trip never exists half-crewed.
    crew_query = """
        MATCH (captain:Profile {id: $profile_id})-[:MEMBER_OF]->(:Family)
              <-[:MEMBER_OF]-(crew:Profile)
        WHERE crew.id <> $profile_id
        MATCH (trip:Trip {id: $trip_id})
        MERGE (crew)-[:IS_CREW_OF]->(trip)
    """

    def _create(tx: Transaction) -> None:
        tx.run(
            create_query,
            trip_id=trip_id,
            trip_name=trip_name,
            profile_id=profile_id,
            start_date=start_date,
            end_date=end_date,
            tour_input_json=tour_input_json,
            options_json=options_json,
        )
        tx.run(crew_query, trip_id=trip_id, profile_id=profile_id)
        _create_itinerary_items(tx, trip_id, profile_id, stops)

    session.execute_write(_create)

    return {"trip_id": trip_id, "trip_name": trip_name}


def _create_itinerary_items(
    tx: Transaction,
    trip_id: str,
    profile_id: str,
    stops: list[dict[str, Any]],
) -> list[str]:
    """Create one ItineraryItem (+ edges) per stop; item ids in stop order.

    A null $lens_name is simply not stored (Cypher drops null map entries).

    W7.13 (Sofia; the closing panel): the POI match is OPTIONAL. The old
    ``MATCH (poi:POI {id: $poi_id})`` made the whole CREATE silently match
    nothing for the A→B finish SENTINEL (whose id names no POI node), so the
    finish stop — the day's goodbye — was never stored, never under HAS_STOP,
    and therefore invisible to the voicing pass forever, while its minted item
    id was returned and saved into the session as a phantom. The sentinel's
    item is now real (stored, voiced); a NON-sentinel stop whose POI is
    genuinely absent RAISES instead of vanishing (the same silent-no-op class).
    """
    item_query = """
        MATCH (trip:Trip {id: $trip_id})
        MATCH (profile:Profile {id: $profile_id})
        OPTIONAL MATCH (poi:POI {id: $poi_id})
        CREATE (item:ItineraryItem {
            id: $item_id,
            sort_order: $sort_order,
            duration_min: $duration_min,
            start_time: $start_time,
            beat_ids: $beat_ids,
            extra_beat_ids: $extra_beat_ids,
            primary_beat_id: $primary_beat_id,
            lens_name: $lens_name,
            narration: $narration,
            extra_narration: $extra_narration,
            close_text: $close_text,
            thread_lines: $thread_lines,
            full_narration: $full_narration,
            full_close_text: $full_close_text,
            trigger_json: $trigger_json,
            leg_narration: $leg_narration,
            leg_from_poi_id: $leg_from_poi_id,
            segments_json: $segments_json,
            created_at: datetime()
        })
        CREATE (trip)-[:HAS_STOP]->(item)
        CREATE (item)-[:ASSIGNED_TO]->(profile)
        FOREACH (p IN CASE WHEN poi IS NULL THEN [] ELSE [poi] END |
            CREATE (item)-[:AT_POI]->(p))
        WITH item, poi
        UNWIND (CASE WHEN size($beat_ids) = 0 THEN [null] ELSE $beat_ids END) AS bid
        OPTIONAL MATCH (beat:NarrativeBeat {id: bid})
        FOREACH (b IN CASE WHEN beat IS NULL THEN [] ELSE [beat] END |
            CREATE (item)-[:PLAYS_BEAT]->(b))
        RETURN poi IS NOT NULL AS poi_found, count(beat) AS edges
    """
    item_ids: list[str] = []
    for stop in stops:
        item_id = str(uuid.uuid4())
        record = tx.run(
            item_query,
            trip_id=trip_id,
            poi_id=stop["poi_id"],
            profile_id=profile_id,
            item_id=item_id,
            sort_order=stop["sort_order"],
            duration_min=stop["duration_min"],
            start_time=stop["start_time"],
            beat_ids=stop["beat_ids"],
            extra_beat_ids=stop.get("extra_beat_ids", []),
            primary_beat_id=stop["primary_beat_id"],
            lens_name=stop["lens_name"],
            narration=stop.get("narration"),
            extra_narration=stop.get("extra_narration"),
            close_text=stop.get("close_text"),
            # Neo4j properties cannot hold maps: the THREADS travel as ONE JSON
            # string; GeneratedStop's validator decodes rows back (Phase 6 S6.5).
            thread_lines=(
                json.dumps(stop["thread_lines"], ensure_ascii=False)
                if stop.get("thread_lines")
                else None
            ),
            full_narration=stop.get("full_narration"),
            full_close_text=stop.get("full_close_text"),
            # Phase 7 S7.3: the stop's placed trigger geometry, ONE JSON string (the
            # thread_lines precedent) — the saved-trips reader hands it back so a
            # walk started from a saved trip has the same geometry the session has.
            trigger_json=(
                json.dumps(stop["trigger"], ensure_ascii=False) if stop.get("trigger") else None
            ),
            # Phase 7 S7.7: the stop's leg piece text; its file lands on the item when
            # the voicing pass runs (leg_audio_url / leg_audio_duration_sec).
            leg_narration=stop.get("leg_narration"),
            leg_from_poi_id=stop.get("leg_from_poi_id"),
            # Phase 7 S7.7 (B): the chapters as a JSON list (the thread_lines precedent);
            # the voicing pass writes each chapter's file back INTO this list.
            segments_json=(
                json.dumps(stop["segments"], ensure_ascii=False) if stop.get("segments") else None
            ),
        ).single()
        # The RETURN now always yields one row (the sentinel-safe UNWIND above), so a
        # missing row is a broken query, never a shrug.
        if record is None:
            raise ValueError(f"Stop {stop['poi_id']!r}: the item CREATE returned no row")
        # A stop naming a POI the graph does not hold fails LOUDLY — the sentinel (a
        # waypoint at the person's own finish, not a place) is the one id allowed to
        # have none (W7.13, Sofia: the silent no-op class).
        if not record["poi_found"] and not stop["poi_id"].startswith(END_B_SENTINEL_PREFIX):
            raise ValueError(
                f"Stop {stop['poi_id']!r}: no POI with that id in the graph — the item "
                "would have vanished silently (W7.13)"
            )
        # The mid-query MATCH silently drops absent beat ids; fail loudly
        # rather than persist an item whose stored beat_ids cite beats it
        # has no PLAYS_BEAT edge to (corpus changed mid-request?).
        if record["edges"] != len(stop["beat_ids"]):
            raise ValueError(
                f"Stop {stop['poi_id']!r}: created {record['edges']} PLAYS_BEAT edges "
                f"for {len(stop['beat_ids'])} beat_ids — beats missing from graph"
            )
        item_ids.append(item_id)
    return item_ids


def replace_trip_stops(
    session: Session, trip_id: str, stops: list[dict[str, Any]]
) -> list[str]:
    """Delete a trip's ItineraryItems and recreate them from ``stops``.

    /compose persists the picked flavour through this: item ids are FRESH
    (clients rebuild their stop lists from the response) and the new items
    carry no audio fields, so stale per-stop audio of the pre-compose
    narration can never be served as composed. Returns the new item ids in
    stop order.

    The captain lookup, the DETACH DELETE and every item CREATE run inside ONE
    explicit write transaction, so a mid-loop failure (e.g. a stop citing a
    beat the corpus no longer has) rolls the delete back too: the trip keeps
    its ORIGINAL itinerary rather than being left permanently truncated and
    uncomposed. Callers pass a Session; the transaction is opened internally.
    """

    def _replace(tx: Transaction) -> list[str]:
        record = tx.run(
            "MATCH (p:Profile)-[:IS_CAPTAIN_OF]->(t:Trip {id: $tid}) RETURN p.id AS pid",
            tid=trip_id,
        ).single()
        if record is None:
            raise ValueError(f"Trip {trip_id!r} not found (or has no captain profile)")
        tx.run(
            "MATCH (:Trip {id: $tid})-[:HAS_STOP]->(i:ItineraryItem) DETACH DELETE i",
            tid=trip_id,
        )
        return _create_itinerary_items(tx, trip_id, record["pid"], stops)

    return session.execute_write(_replace)


def write_trip_session(
    session: Session, trip_id: str, *, plan_version: int, session_json: str
) -> None:
    """Persist the living session's current version (Phase 5 S5.8, design §8.2).

    Replaces `mark_trip_composed` and its 409: a write is never a conflict, it is
    version N+1. The trip's ItineraryItems are the composed day's stops (rewritten
    only when narration changes — /compose); a replan mints a version OVER them.
    """
    session.run(
        "MATCH (t:Trip {id: $tid}) SET t.plan_version = $pv, t.session_json = $sj",
        tid=trip_id,
        pv=plan_version,
        sj=session_json,
    )


def get_trip_compose_inputs(session: Session, trip_id: str) -> dict[str, Any] | None:
    """The persisted compose inputs and session state for a trip; None if no such trip.

    Returns ``{"tour_input": dict | None, "options": list | None,
    "composed_route_id": str | None, "plan_version": int, "session": dict | None}``.
    ``composed_route_id`` is READ for trips written before Phase 5 and ignored —
    the frozen trip's one-compose lock is deleted (design §8.2); nothing writes it
    any more. ``plan_version`` is 0 for a trip that has never been composed.
    """
    record = session.run(
        "MATCH (t:Trip {id: $tid}) "
        "RETURN t.tour_input_json AS ti, t.options_json AS oj, "
        "       t.composed_route_id AS composed, t.plan_version AS pv, "
        "       t.session_json AS sj",
        tid=trip_id,
    ).single()
    if record is None:
        return None
    return {
        "tour_input": json.loads(record["ti"]) if record["ti"] else None,
        "options": json.loads(record["oj"]) if record["oj"] else None,
        "composed_route_id": record["composed"],
        "plan_version": int(record["pv"] or 0),
        "session": json.loads(record["sj"]) if record["sj"] else None,
    }


def list_trips_for_profile(
    session: Session,
    profile_id: str,
) -> list[dict[str, Any]]:
    """Return all trips for a profile with their stops.

    For each trip, collects stops via HAS_STOP → ItineraryItem → AT_POI → POI.
    One row per item: its PLAYS_BEAT beats are collected, with stored
    `beat_ids`/`primary_beat_id`/`lens_name` item properties taking precedence
    and edge-derived values as the fallback for legacy single-beat items
    (e.g. seeded trips) that predate M0b's multi-beat persistence.
    """
    # First verify profile exists
    check = session.run(
        "MATCH (p:Profile {id: $pid}) RETURN p.id AS id",
        pid=profile_id,
    ).single()
    if check is None:
        return None  # type: ignore[return-value]

    # Every trip this profile can READ: the days it captains and the days it
    # crews (the family's shared days — ADR 0005). DISTINCT because a profile
    # could hold both edges to one trip.
    trips_query = """
        MATCH (p:Profile {id: $pid})-[:IS_CAPTAIN_OF|IS_CREW_OF]->(t:Trip)
        RETURN DISTINCT t.id AS trip_id,
               t.name AS trip_name,
               t.start_date AS start_date,
               t.end_date AS end_date,
               t.status AS status,
               t.created_at AS _created_at
        ORDER BY _created_at DESC
    """
    trip_records = session.run(trips_query, pid=profile_id)
    trips = [dict(r) for r in trip_records]

    results: list[dict[str, Any]] = []
    for trip in trips:
        # Get stops for each trip
        stops_query = """
            MATCH (t:Trip {id: $tid})-[:HAS_STOP]->(item:ItineraryItem)
            MATCH (item)-[:AT_POI]->(poi:POI)
            OPTIONAL MATCH (item)-[:PLAYS_BEAT]->(beat:NarrativeBeat)
            OPTIONAL MATCH (beat)-[:TAGGED_WITH]->(bl:Lens)
            WITH item, poi, beat, min(bl.name) AS beat_lens
            WITH item, poi, collect(CASE WHEN beat IS NULL THEN NULL
                                     ELSE {id: beat.id, script_body: beat.script_body,
                                     audio_url: beat.audio_url,
                                     duration_sec: beat.duration_sec,
                                     lens: beat_lens} END) AS beats_raw
            WITH item, poi, [b IN beats_raw WHERE b IS NOT NULL] AS beats
            WITH item, poi, beats,
                 coalesce(item.primary_beat_id, beats[0].id) AS primary_id
            WITH item, poi, beats, primary_id,
                 [b IN beats WHERE b.id = primary_id][0] AS pb
            WITH item, poi, beats, primary_id, pb,
                 coalesce(item.lens_name, pb.lens) AS lens_name
            OPTIONAL MATCH (lens:Lens {name: lens_name})
            RETURN item.sort_order AS sort_order,
                   item.id AS stop_id,
                   poi.id AS poi_id,
                   poi.name AS poi_name,
                   poi.location.latitude AS lat,
                   poi.location.longitude AS lng,
                   primary_id AS beat_id,
                   coalesce(item.beat_ids, [b IN beats | b.id]) AS beat_ids,
                   coalesce(item.extra_beat_ids, []) AS extra_beat_ids,
                   item.extra_narration AS extra_narration,
                   lens_name AS lens_name,
                   CASE WHEN lens IS NOT NULL
                        THEN coalesce(lens.display_label, lens.name)
                        ELSE lens_name END AS lens_display,
                   item.duration_min AS duration_min,
                   coalesce(poi.importance_tier, 3) AS importance_tier,
                   CASE WHEN item.start_time IS NOT NULL
                        THEN substring(toString(item.start_time), 0, 5)
                        ELSE '09:00' END AS start_time,
                   pb.script_body AS script_body,
                   item.narration AS narration,
                   item.close_text AS close_text,
                   item.thread_lines AS thread_lines,
                   item.full_narration AS full_narration,
                   item.full_close_text AS full_close_text,
                   item.close_audio_url AS close_audio_url,
                   item.thread_audio_urls AS thread_audio_urls,
                   item.full_close_audio_url AS full_close_audio_url,
                   item.trigger_json AS trigger_json,
                   item.leg_narration AS leg_narration,
                   item.leg_from_poi_id AS leg_from_poi_id,
                   item.leg_audio_url AS leg_audio_url,
                   item.leg_audio_duration_sec AS leg_audio_duration_sec,
                   item.segments_json AS segments_json,
                   coalesce(item.audio_url, pb.audio_url) AS audio_url,
                   coalesce(item.audio_duration_sec, pb.duration_sec) AS audio_duration_sec
            ORDER BY item.sort_order
        """
        stop_records = session.run(stops_query, tid=trip["trip_id"])
        stops = [dict(r) for r in stop_records]
        for s in stops:
            # Phase 7 S7.3: the placed geometry back as the dict the wire model reads.
            raw = s.pop("trigger_json", None)
            s["trigger"] = json.loads(raw) if raw else None
            raw_segments = s.pop("segments_json", None)
            s["segments"] = json.loads(raw_segments) if raw_segments else []

        total_duration = sum(s["duration_min"] for s in stops)
        anchor_count = sum(1 for s in stops if s["importance_tier"] == 5)
        flavour_count = len(stops) - anchor_count

        results.append(
            {
                "trip_id": trip["trip_id"],
                "trip_name": trip["trip_name"],
                "profile_id": profile_id,
                "total_stops": len(stops),
                "total_duration_min": total_duration,
                "anchor_count": anchor_count,
                "flavour_count": flavour_count,
                "stops": stops,
            }
        )

    return results
