"""RouteOption assembly — the output contract, one per flavour.

Pure functions over engine outputs (Route + Script + the snapshot's beat
refs). The API layer wraps these into responses; a later stage fills
stop_audio and the grounded why_this_works.

Populates the spotlight model on the output. Each stop carries its computed
``spotlight`` score and its collapsed output ``band`` ("dwell" vs
"vignette"); the option carries a per-corridor ``lens_coverage_note`` when
lenses were requested. This is ANNOTATION ONLY -- which stops are selected
and their ORDER are decided upstream in select_route by the existing gates,
and this module leaves them untouched. Selected (on-corridor) stops score at
proximity 1.0 (detour 0), so spotlight == gravity x lens_relevance, always strictly positive.
"""

from __future__ import annotations

from src.audio.tts_normalize import normalize_dashes_for_reading

from .contract import (
    END_B_SENTINEL_PREFIX,
    POI,
    BeatRef,
    BeatSequence,
    Route,
    RouteOption,
    RouteOptionStop,
    Script,
)
from .generation import is_walk_concurrent, transit_class_beat_ids, vignette_one_liner_text
from .routing import leg_walk_seconds, total_walk_seconds
from .selection import (
    LENS_FLOOR,
    CorpusSnapshot,
    band_for_spotlight,
    expanded_interest_lenses,
    gravity,
    is_dwell_band,
    lens_relevance,
    require_materialized_snapshot,
    spotlight,
)
from .visit_time import served_elapsed_seconds


def dominant_lens(
    beat_ids: tuple[str, ...],
    beats_by_id: dict[str, BeatRef],
    *,
    prefer: frozenset[str] | None = None,
) -> str | None:
    """The most common lens across a stop's beats, or None if no beat is lensed.

    Computed from the beats themselves (BeatRef.lenses) — never fabricated.
    Ties break deterministically by lens name so the result is stable across
    runs. (Moved from the API adapter so the engine and API share it.)

    ``prefer`` is the requested subject family, expanded: when any
    voiced beat answers it, the label is the most common ASKED lens — the stop's
    role in the person's own day — never the plain majority of whatever context
    rode along, which is how an architecture request came back wearing
    "Dark History". A stop with nothing on the request keeps the plain majority:
    the label stays true to the words either way.
    """
    counts: dict[str, int] = {}
    for bid in beat_ids:
        ref = beats_by_id.get(bid)
        if ref is None:
            continue
        for lens in ref.lenses:
            counts[lens] = counts.get(lens, 0) + 1
    if not counts:
        return None
    if prefer:
        asked = {name: n for name, n in counts.items() if name.lower() in prefer}
        if asked:
            return sorted(asked, key=lambda lname: (-asked[lname], lname))[0]
    return sorted(counts, key=lambda lname: (-counts[lname], lname))[0]


def option_eta_seconds(route: Route, script: Script) -> int:
    """How long this tour actually takes: every walking leg, plus every stop's dwell.

    THE ONE FORMULA. Generate has to know a flavour's elapsed time BEFORE it can build
    the flavour's option (that builder needs a trip id, which only exists once the trip
    has been saved), so the number is computed here and used by both. Restating the sum
    at the second call site is exactly how the saved tour and the offered tour drift
    into declaring different lengths for the same walk.

    A walk-past sight or a line heard on the move costs no elapsed time — it happens
    during a walk this sum already counts.

    The walking half is ``routing.total_walk_seconds``, the same expression the Route
    was built with. This line used to spell it differently — branching on whether
    ``leg_seconds`` was set rather than on whether Valhalla produced it — and the two
    agreed only because the fallback path happens to compute the same number. A leg
    with a routed duration but a haversine provenance would have made the served
    clock and the planned clock disagree, silently.

    The COMBINING (walk + dwell) delegates to ``served_elapsed_seconds``
    (2026-08-07) — the one rule every elapsed-time caller in the engine now
    shares; this function's own job is only supplying the two honest terms.
    """
    return served_elapsed_seconds(
        total_walk_seconds(route.transits),
        sum(sp.dwell_seconds for sp in script.selected_pois),
    )


def build_route_option(
    route: Route,
    script: Script,
    beats_by_id: dict[str, BeatRef],
    *,
    route_id: str,
    snapshot: CorpusSnapshot,
    sequence: BeatSequence,
) -> RouteOption:
    """Assemble one flavour's RouteOption from its Route + Script + BeatSequence.

    THE ONE INTERLEAVE. This absorbed the fourth copy, the private preview builder
    that lived in the API layer (``src/api/routes/trips.py``, deleted 2026-08-04):
    the preview and the flavour cards were two implementations of the same ordering,
    and they had already drifted — the preview split walk-concurrent narration onto
    its own card and the option builder did not, so the workbench and the phone
    disagreed about what a "stop" is.

    THREE KINDS OF CARD, in walking order:
      leg      — what the tourist hears WHILE WALKING into the next dwell stop. Emitted
                 only when the walk actually carries narration; an empty card would
                 imply content that does not exist. ``minutes`` is the WALK.
      vignette — a walk-past one-liner on that leg, voiced through the SAME helper the
                 audio path uses, so the printed line and the spoken line cannot drift.
                 A vignette POI with no voiceable beat is not shown, because it is not
                 heard either.
      dwell    — the stop itself, with the stationary narration and the deeper-dive flag.

    ``sequence`` is required and has no default on purpose: an optional narration
    source is exactly how a caller silently gets a card list with no narration and
    nobody notices.

    eta_seconds is unchanged and still counts routed legs (or the pace-corrected
    haversine) plus every dwell: a vignette or leg card costs no elapsed time.

    Each stop is annotated with its ``spotlight`` score and
    collapsed output ``band``, and the option carries a per-corridor
    ``lens_coverage_note``. ANNOTATION ONLY -- the stop set and order come from
    select_route's gates upstream; this only describes them. ``snapshot``
    supplies each POI's tier (gravity) and beat lenses (lens_relevance);
    ``script.inputs.lenses`` is the requested genre. Selected stops are
    on-corridor, so proximity is the on-path default (detour 0 -> 1.0) and
    spotlight == gravity x lens_relevance, which is strictly positive.

    ``route.vignettes`` (leg_idx -> walk-past POIs, leg i
    = the walk INTO stop i) are interleaved as ``band="vignette"`` stops right
    after their leg-origin stop — the vignette on leg i sits between stop i-1
    and stop i (a leg-0 vignette would precede stop 0; a round trip's closing
    leg, index len(stops), would follow the last stop). Dwell stops, their
    order, and eta_seconds are unchanged: a vignette is a walk-past one-liner
    (minutes=0), not a stop with dwell time.
    """
    require_materialized_snapshot(snapshot, operation="route-option assembly")
    roles = {p.id: p.poi_role for p in route.pois}
    pois_by_id: dict[str, POI] = {p.id: p for p in route.pois}
    lenses_fs = frozenset(script.inputs.lenses or ())
    # The label rule (dominant_lens's ``prefer``): a card on a lensed day is
    # labelled by the asked family, the same expansion the session stops use —
    # one stop never wears two labels on one wire.
    prefer = expanded_interest_lenses(script.inputs.lenses, snapshot)

    # --- narration, split the way the tourist experiences it -------------------
    # A vignette's line is voiced by its OWN card below, so it is stripped from the
    # dwell card it was folded into (_build_transit emits it at the ARRIVAL stop's
    # stop_idx). ``is_walk_concurrent`` is the SHARED predicate quality_rubric's time
    # model uses, so a sentence shown on a leg card is exactly a sentence the rubric
    # scored as costing no elapsed time.
    vignette_beat_ids = {b.id for beats in sequence.vignette_beats.values() for b in beats}
    # The walk's own corpus narration: transit-beat sentences ride the
    # leg card, exactly where the phone plays them.
    transit_ids = transit_class_beat_ids(beats_by_id.values())
    dwell_sents: dict[int, list[str]] = {}
    leg_sents: dict[int, list[str]] = {}
    for sentence in script.script:
        if sentence.source_type == "beat" and sentence.source_id in vignette_beat_ids:
            continue
        bucket = (
            leg_sents
            if is_walk_concurrent(sentence, vignette_beat_ids, transit_ids)
            else dwell_sents
        )
        bucket.setdefault(sentence.stop_idx, []).append(sentence.text)
    # Display-normalize dashes so the text READS the way the audio SOUNDS (a comma
    # pause, not a dangling stroke).
    per_stop = {idx: normalize_dashes_for_reading(" ".join(t)) for idx, t in dwell_sents.items()}
    per_leg = {idx: normalize_dashes_for_reading(" ".join(t)) for idx, t in leg_sents.items()}
    # The walk shown on each leg card. One expression, shared with the route
    # total and the announced leg in generation, so the card the traveller reads
    # cannot claim a different walk from the one the tour was planned around.
    walk_by_leg = {i: leg_walk_seconds(t) for i, t in enumerate(route.transits)}
    # The clause cap inside vignette_one_liner_text keeps the POI's own name in a
    # shortened line, and vignette POIs are walk-past (route.vignettes), NOT seated
    # route.pois — so the name map must cover both or the cap falls back to the run-on.
    poi_name_by_id = {p.id: p.name for p in route.pois}
    for vignette_pois in route.vignettes.values():
        for vignette_poi in vignette_pois:
            poi_name_by_id.setdefault(vignette_poi.id, vignette_poi.name)
    one_liner_by_poi: dict[str, str] = {}
    for beats in sequence.vignette_beats.values():
        for beat in beats:
            text = vignette_one_liner_text(beat.script_body, poi_name_by_id.get(beat.poi_id, ""))
            if text:
                one_liner_by_poi[beat.poi_id] = normalize_dashes_for_reading(text)

    # --- the interleave --------------------------------------------------------
    interleaved: list[RouteOptionStop] = []
    for i, sp in enumerate(script.selected_pois):
        leg_text = per_leg.get(i, "").strip()
        if leg_text:
            interleaved.append(
                _leg_stop(sp, narration=leg_text, walk_seconds=walk_by_leg.get(i, 0))
            )
        interleaved.extend(
            _vignette_stop(
                vp,
                lenses=lenses_fs,
                prefer=prefer,
                snapshot=snapshot,
                narration=one_liner_by_poi[vp.id],
            )
            for vp in route.vignettes.get(i, ())
            if vp.id in one_liner_by_poi
        )
        interleaved.append(
            _build_stop(
                sp,
                poi=pois_by_id.get(sp.id),
                role=roles.get(sp.id, "stop"),
                beats_by_id=beats_by_id,
                lenses=lenses_fs,
                prefer=prefer,
                snapshot=snapshot,
                narration=per_stop.get(i, ""),
                has_deeper_dive=bool(sequence.overflow_by_poi.get(sp.id)),
                # The honesty surface, straight off the
                # Route the planner priced — never recomputed here.
                queue_minutes=round(route.planned_queue_seconds.get(sp.id, 0) / 60),
                goes_inside=route.visit_goes_inside.get(sp.id),
            )
        )
    interleaved.extend(
        _vignette_stop(
            vp,
            lenses=lenses_fs,
            prefer=prefer,
            snapshot=snapshot,
            narration=one_liner_by_poi[vp.id],
        )
        for vp in route.vignettes.get(len(script.selected_pois), ())
        if vp.id in one_liner_by_poi
    )
    stops = tuple(interleaved)

    return RouteOption(
        route_id=route_id,
        stops=stops,
        route_polyline=route.route_polyline,
        eta_seconds=option_eta_seconds(route, script),
        # Carried straight off the Route the planner gated. Recomputing it here
        # would be a second opinion about the same tour's length.
        elapsed_shortfall_seconds=route.elapsed_shortfall_seconds,
        lens_summary=dict(script.lens_coverage),
        flow_score=route.flow_score,
        backtrack_ratio=route.backtrack_ratio,
        degraded=route.reach.degraded if route.reach is not None else False,
        lens_coverage_note=_lens_coverage_note(route.pois, lenses=lenses_fs, snapshot=snapshot),
    )


def _build_stop(
    sp,
    *,
    poi: POI | None,
    role: str,
    beats_by_id: dict[str, BeatRef],
    lenses: frozenset[str],
    prefer: frozenset[str] | None,
    snapshot: CorpusSnapshot,
    narration: str,
    has_deeper_dive: bool,
    queue_minutes: int = 0,
    goes_inside: bool | None = None,
) -> RouteOptionStop:
    """One RouteOptionStop with its spotlight + band annotation.

    Falls back to gravity-only scoring (no lens dimming) when the POI is not in
    ``route.pois`` -- e.g. a synthesized fixed-end B sentinel that carries no
    beats. The sentinel still gets a positive spotlight from its tier so the
    "every selected stop has a band" invariant holds.
    """
    if poi is not None:
        score = spotlight(poi, lenses=lenses, snapshot=snapshot)
        tier = poi.tier
    else:
        # No corpus POI behind this stop (sentinel). Score on tier alone with a
        # uniform lens factor -- no beats means no lens dimming to apply.
        tier = sp.tier
        score = gravity(tier)
    band = "dwell" if is_dwell_band(band_for_spotlight(score, tier=tier)) else "vignette"
    return RouteOptionStop(
        poi_id=sp.id,
        name=sp.name,
        lat=sp.lat,
        lng=sp.lng,
        lens=dominant_lens(sp.beat_ids, beats_by_id, prefer=prefer),
        visit_or_walk_past=("walk_past" if role == "walk_by_only" else "visit"),
        minutes=round(sp.dwell_seconds / 60),
        band=band,
        spotlight=score,
        narration=narration,
        has_deeper_dive=has_deeper_dive,
        queue_minutes=queue_minutes,
        goes_inside=goes_inside,
        # The A→B waypoint is not a place: flagged so a screen ends the day
        # at "your finish point" instead of counting a zero-minute stop.
        is_finish_point=sp.id.startswith(END_B_SENTINEL_PREFIX),
    )


def _vignette_stop(
    poi: POI,
    *,
    lenses: frozenset[str],
    prefer: frozenset[str] | None,
    snapshot: CorpusSnapshot,
    narration: str,
) -> RouteOptionStop:
    """One walk-past vignette as a RouteOptionStop.

    ``band="vignette"``, ``visit_or_walk_past="walk_past"``, ``minutes=0`` —
    a one-liner as you pass, never dwell time. ``spotlight`` is the POI's
    on-path score (the same call select_vignettes banded it with) and
    ``lens`` the dominant lens of its own beats from the snapshot.
    ``has_deeper_dive`` stays False: a walk-past has no "keep exploring here".
    """
    beats = {b.id: b for b in snapshot.beats_for(poi.id)}
    return RouteOptionStop(
        poi_id=poi.id,
        name=poi.name,
        lat=poi.lat,
        lng=poi.lng,
        lens=dominant_lens(tuple(beats), beats, prefer=prefer),
        visit_or_walk_past="walk_past",
        minutes=0,
        band="vignette",
        spotlight=spotlight(poi, lenses=lenses, snapshot=snapshot),
        narration=narration,
    )


def _leg_stop(sp, *, narration: str, walk_seconds: int) -> RouteOptionStop:
    """The walk INTO ``sp``, as its own card.

    Product ruling 2026-07-19: "Audio overlaps the walking. It is a part of the tour
    experience." ``minutes`` is the WALK's duration, not the narration's, so a
    six-minute walk carrying seven seconds of narration shows the gap instead of
    averaging it away. The card borrows the arrival stop's identity and coordinates
    rather than inventing a POI id, so a consumer matching POI ids must filter to
    ``band == "dwell"``.
    """
    return RouteOptionStop(
        poi_id=sp.id,
        name=f"Walk to {sp.name}",
        lat=sp.lat,
        lng=sp.lng,
        lens=None,
        visit_or_walk_past="walk_past",
        minutes=round(walk_seconds / 60),
        band="leg",
        spotlight=0.0,
        narration=narration,
    )


def _lens_coverage_note(
    pois: tuple[POI, ...],
    *,
    lenses: frozenset[str],
    snapshot: CorpusSnapshot,
) -> str | None:
    """Per-corridor lens density: how many route POIs speak to the lens.

    Returns None when no lens was requested (nothing to surface). Otherwise
    counts the route POIs whose beats hit the requested lenses -- a hit is any
    ``lens_relevance`` above the floor (a direct or one-hop match; a miss sits
    at LENS_FLOOR). The note states "N of M places on this route speak to the
    chosen lens(es)" so the user can judge whether to broaden the genre before
    composing (REACH measures and surfaces coverage, never silently ships
    an off-tone tour).
    """
    if not lenses:
        return None
    total = len(pois)
    if total == 0:
        return "No places on this route speak to the chosen lens(es)."
    hits = sum(
        1 for p in pois if lens_relevance(p, lenses=lenses, snapshot=snapshot) > LENS_FLOOR
    )
    return f"{hits} of {total} places on this route speak to the chosen lens(es)."
