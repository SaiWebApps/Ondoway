"""End-to-end /tour-build CLI harness.

Resolves a named or coordinate start point against Paris Neo4j, runs
``selection → beat_select → generation → validation``, writes the
JSON Script + Markdown render to ``data/{city_slug}/tours/{id}.json``
+ ``.md``, and prints a one-line summary.

Defaults to the deterministic ``MockGlueClient`` so this works without
``ANTHROPIC_API_KEY``. Pass ``--haiku`` to switch on the real Haiku
glue stitch (requires the env var).

Exit codes:
- 0 — validation passed
- 1 — validation failed (untraceable sentence(s) or forbidden phrase hit)
- 2 — input/resolution error (e.g. unknown start name, no POIs reachable)
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from src.connection import create_driver
from src.tour.beat_select import select_vignette_beats
from src.tour.compose_gate import build_full_verifier
from src.tour.contract import BeatSequence, TourInput, resolve_party_axes
from src.tour.density import TourabilityRefusedError, assess_snapshot
from src.tour.generation import generate
from src.tour.glue_client import HaikuGlueClient, MockGlueClient
from src.tour.render_md import render_markdown
from src.tour.routing import haversine_m
from src.tour.routing_client import RoutingClient
from src.tour.selection import (
    B_SNAP_PROXIMITY_M,
    VALHALLA_MAX_CONTOUR_MINUTES,
    CertificationPlanningInfeasibleError,
    build_poi_beat_plans_capped,
    load_paris_corpus,
    reach_envelope_searched,
    select_route,
)
from src.tour.visit_time import (
    poi_has_interior,
    shape_at_place_seconds,
    shape_total_seconds,
)
from src.tour.weather import fetch_rain_likelihood

HAIKU_INPUT_USD_PER_MTOK = 1.00  # 2026 Haiku 4.5 list (per 1M tokens)
HAIKU_OUTPUT_USD_PER_MTOK = 5.00


def _lookup_place(
    driver, arg: str, city_slug: str, *, want_poi_id: bool = False
) -> tuple[str | None, tuple[float, float], str] | None:
    """THE one place-lookup ladder: coordinate string or POI/Area name →
    ``(poi_id, (lat, lng), label)``, or None when nothing matches.

    ``--start``, ``--end`` and ``--pin`` all resolve HERE (plan S3.1's
    sabotage list forbids a second lookup), so a name means the same place
    at every flag. Tries (in order):
    1. ``"lat,lng"`` literal.
    2. POI exact name (case-insensitive).
    3. POI substring match (case-insensitive contains).
    4. Area exact name → use the centroid of POIs WITHIN that area.

    ``poi_id`` is None when no single corpus POI names the spot: a raw
    coordinate, or an Area centroid. A pin needs an id, so ``want_poi_id``
    additionally snaps a coordinate onto the nearest corpus POI within
    ``B_SNAP_PROXIMITY_M`` — the established "the destination IS this
    place" threshold from B-materialization — and only then, because the
    extra query would be dead weight on every ordinary --start resolution.
    """
    if "," in arg:
        try:
            lat_s, lng_s = arg.split(",", 1)
            coords = (float(lat_s.strip()), float(lng_s.strip()))
        except ValueError:
            pass  # a name containing a comma falls through to the name arms
        else:
            if not want_poi_id:
                return None, coords, arg
            with driver.session() as session:
                record = session.run(
                    "MATCH (p:POI {city_name: $city}) "
                    "WHERE p.location IS NOT NULL "
                    "WITH p, point.distance(p.location, "
                    "point({latitude: $lat, longitude: $lng, srid: 4326})) AS d "
                    "WHERE d <= $radius "
                    "RETURN p.id AS id ORDER BY d LIMIT 1",
                    city=city_slug,
                    lat=coords[0],
                    lng=coords[1],
                    radius=B_SNAP_PROXIMITY_M,
                ).single()
            return (record["id"] if record else None), coords, arg

    # Build a list of name candidates to try, full name first then
    # progressively shorter leading-word slices ("Pont Neuf metro" →
    # "Pont Neuf metro", "Pont Neuf", "Pont"). Stops at 1 word.
    tokens = arg.split()
    candidates: list[str] = []
    for n_words in range(len(tokens), 0, -1):
        candidates.append(" ".join(tokens[:n_words]))

    with driver.session() as session:
        for cand in candidates:
            record = session.run(
                "MATCH (p:POI {city_name: $city}) "
                "WHERE toLower(p.name) = toLower($name) "
                "RETURN p.id AS id, p.name AS name, "
                "p.location.y AS lat, p.location.x AS lng "
                "LIMIT 1",
                city=city_slug,
                name=cand,
            ).single()
            if record:
                return (
                    record["id"],
                    (float(record["lat"]), float(record["lng"])),
                    record["name"],
                )

        for cand in candidates:
            record = session.run(
                "MATCH (p:POI {city_name: $city}) "
                "WHERE toLower(p.name) CONTAINS toLower($needle) "
                "WITH p ORDER BY p.importance_tier DESC, p.name "
                "RETURN p.id AS id, p.name AS name, "
                "p.location.y AS lat, p.location.x AS lng "
                "LIMIT 1",
                city=city_slug,
                needle=cand,
            ).single()
            if record:
                return (
                    record["id"],
                    (float(record["lat"]), float(record["lng"])),
                    record["name"],
                )

        for cand in candidates:
            record = session.run(
                "MATCH (p:POI {city_name: $city})-[:WITHIN]->(a:Area) "
                "WHERE toLower(a.name) = toLower($name) AND p.location IS NOT NULL "
                "RETURN avg(p.location.y) AS lat, avg(p.location.x) AS lng, "
                "       a.name AS area_name "
                "LIMIT 1",
                city=city_slug,
                name=cand,
            ).single()
            if record and record["lat"] is not None:
                return (
                    None,
                    (float(record["lat"]), float(record["lng"])),
                    f"{record['area_name']} (centroid)",
                )

    return None


def _resolve_start(driver, start_arg: str, city_slug: str) -> tuple[tuple[float, float], str]:
    """Coordinate string or POI/Area name \u2192 (lat, lng) + display label.

    A thin wrapper over ``_lookup_place`` \u2014 the ladder lives THERE so --pin
    can share it (plan S3.1); the raise and its message are unchanged from
    before the extraction.
    """
    found = _lookup_place(driver, start_arg, city_slug)
    if found is None:
        raise SystemExit(
            f"\u2717 Could not resolve start point: {start_arg!r}. "
            f"Pass coordinates as 'lat,lng', an exact POI name, or an Area name."
        )
    _, coords, label = found
    return coords, label


def _resolve_pinned_poi_ids(
    driver, parser: argparse.ArgumentParser, pins: list[str] | None, city_slug: str
) -> tuple[str, ...]:
    """Each ``--pin`` through the one lookup ladder \u2192 a corpus POI id, in
    command-line order (design \u00a73.2: a pin is the visitor's decision on a
    SPECIFIC place, so it lands as an id, never a string). A pin that
    resolves to nothing \u2014 unknown text, or a coordinate with no corpus POI
    within snapping range \u2014 is an argparse-level error naming the pin text.
    """
    ids: list[str] = []
    for pin_text in pins or ():
        found = _lookup_place(driver, pin_text, city_slug, want_poi_id=True)
        poi_id = found[0] if found is not None else None
        if poi_id is None:
            parser.error(
                f"--pin {pin_text!r} does not resolve to a corpus POI. Pass a POI "
                f"name, or 'lat,lng' within {B_SNAP_PROXIMITY_M:.0f} m of one \u2014 a "
                "pin is a promise on a specific place."
            )
        ids.append(poi_id)
    return tuple(ids)


def _build_beat_sequence(route, snapshot, lenses) -> BeatSequence:
    capped = build_poi_beat_plans_capped(route, snapshot, lenses=lenses, end_is_none=True)
    # Render the walk-past vignettes too (Track B + filler-stub demotions):
    # select_route already populated route.vignettes; without this the CLI
    # markdown silently drops every walk-by one-liner, including a thin stop that
    # the governor demoted from a dedicated stop to a vignette. Mirrors the
    # /trips API build sites so the CLI tour matches what the app voices.
    vignette_beats = select_vignette_beats(route.vignettes, snapshot.beats_by_poi, lenses=lenses)
    return BeatSequence(
        poi_beats=tuple(pb for pb, _ in capped),
        vignette_beats=vignette_beats,
        overflow_by_poi={pb.poi_id: ov for pb, ov in capped if ov},
    )


def _generated_id(start_label: str, duration_min: int) -> str:
    safe = "".join(ch if ch.isalnum() else "-" for ch in start_label.lower())
    safe = "-".join(p for p in safe.split("-") if p)
    suffix = uuid.uuid4().hex[:6]
    return f"{safe[:40]}-{duration_min}min-{suffix}"


def _project_cost_upper_bound(
    beat_sequence: BeatSequence, route, tour_input: TourInput
) -> tuple[int, int, float]:
    """Approx Haiku cost ceiling without making a network call.

    Counts glue invocations the same way generation.py would, multiplies
    by max output tokens (150) and Haiku list price; estimates input
    tokens from the rendered prompt template (~4 chars per token).
    """
    from src.tour.glue_client import load_glue_prompt

    template = load_glue_prompt()
    glue_calls: list[tuple[str, str, str]] = []

    class _Counting:
        def stitch(self, category, context, request):
            glue_calls.append((category, context, request))
            return "Walk to the next stop."

    generate(beat_sequence, route, tour_input, glue_client=_Counting())

    proj_in = 0
    proj_out_max = 150 * len(glue_calls)
    for cat, ctx, req in glue_calls:
        rendered = (
            template.replace("{{CATEGORY}}", cat)
            .replace("{{CONTEXT}}", ctx or "(no surrounding context)")
            .replace("{{REQUEST}}", req or "(no specific request)")
        )
        proj_in += max(1, len(rendered) // 4)
    usd = (
        proj_in / 1_000_000 * HAIKU_INPUT_USD_PER_MTOK
        + proj_out_max / 1_000_000 * HAIKU_OUTPUT_USD_PER_MTOK
    )
    return proj_in, proj_out_max, usd


def _perp_distance_m(
    lat: float, lng: float, a: tuple[float, float], b: tuple[float, float]
) -> float:
    """Perpendicular distance from a point to the straight A->B line, in metres.

    Equirectangular projection, which is exact enough across a city: the error
    over 10 km is under a metre, and this number is read to the nearest 10 m.
    Clamped to the segment, so a stop beyond either end measures to that end
    rather than to an imaginary extension of the line.
    """
    lat0 = math.radians((a[0] + b[0]) / 2.0)
    to_x = 111_320.0 * math.cos(lat0)
    to_y = 110_540.0
    ax, ay = a[1] * to_x, a[0] * to_y
    bx, by = b[1] * to_x, b[0] * to_y
    px, py = lng * to_x, lat * to_y
    dx, dy = bx - ax, by - ay
    span = dx * dx + dy * dy
    if span == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / span))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _print_breakdown(
    *,
    tour_input: TourInput,
    wall_clock_s: float,
    route=None,
    script=None,
    best_elapsed_s: int | None = None,
) -> None:
    """The seven numbers, printed on BOTH the success and the refusal paths.

    ONE printer for both, deliberately, so the two can never drift into reporting
    different things. Four of the seven need a route that a refusal never
    produced; those say so in words rather than printing a zero, because a zero
    reads as a measurement and "no route exists" is not one.

    The reach radius comes from ``reach_envelope_searched`` -- the SAME function
    the planner uses -- so this reports the circle actually searched. Deriving it
    here from the requested duration would print ~4,444 m at 300 minutes while the
    fixed-destination planner searched ~12,259 m, and the harness built to expose
    that bug would have hidden it.

    Plan S3.1 adds the promise view: a ``promises:`` line above the per-stop
    table (kind, stop name, arrive-depart window), a SHAPE column (``44m in``
    / ``15m out`` / ``closed—out``) and a QUEUE column. A stop no Promise has
    shaped falls back to the route's priced minutes, with in/out read off the
    POI's own capacity numbers — a pre-promise value: the planner has not
    shaped this stop, and the fallback reports what today's planner actually
    committed to rather than pretending a promise exists. On a dated run the
    table is followed by Aiko's honesty line, ``hours unverified for N of the
    M gated stops on this route`` (plan deviation ii).
    """
    radius_m, iso_minutes = reach_envelope_searched(tour_input)
    absent = "— no route was produced, so this cannot be measured"

    print("  ── breakdown ──────────────────────────────────────────────")
    # WHO THIS DAY IS FOR (plan S2.1/D2) — printed even when every mobility
    # axis happens to tie two runs (e.g. a preset pair whose only new axis is
    # the register, which touches no other number in this table): the day
    # must be visibly different in the OUTPUT, not just in the code path that
    # produced it. Prints "none" only when truly nothing was asked for.
    axis_bits = []
    if tour_input.party:
        axis_bits.append(f"preset={tour_input.party}")
    if tour_input.walking_pace is not None:
        axis_bits.append(f"pace-x{tour_input.walking_pace:g}")
    if tour_input.max_leg_minutes is not None:
        axis_bits.append(f"leg-cap={tour_input.max_leg_minutes}min")
    if tour_input.max_stop_minutes is not None:
        axis_bits.append(f"stop-ceiling={tour_input.max_stop_minutes}min")
    if tour_input.rest_cadence_minutes is not None:
        axis_bits.append(f"rest-cadence={tour_input.rest_cadence_minutes}min")
    if tour_input.escape_radius_m is not None:
        axis_bits.append(f"escape-radius={tour_input.escape_radius_m}m")
    if tour_input.route_surface != "any":
        axis_bits.append(f"surface={tour_input.route_surface}")
    if tour_input.narration_register is not None:
        axis_bits.append(f"register={tour_input.narration_register}")
    print(f"  party:              {', '.join(axis_bits) if axis_bits else 'none'}")
    if route is not None and script is not None:
        walk_s = route.total_walk_seconds
        dwell_s = sum(p.dwell_seconds for p in script.selected_pois)
        legs = [
            int(t.leg_seconds) if getattr(t, "leg_seconds", None) is not None else t.walk_seconds
            for t in route.transits
        ]
        longest = max(legs) if legs else 0
        print(f"  walk:               {walk_s:6d} s ({walk_s // 60} min)")
        print(f"  dwell:              {dwell_s:6d} s ({dwell_s // 60} min)")
        print(f"  total (walk+dwell): {walk_s + dwell_s:6d} s ({(walk_s + dwell_s) // 60} min)")
        print(f"  longest leg:        {longest:6d} s ({longest // 60} min)")
        if tour_input.end is not None:
            worst = max(
                (
                    (_perp_distance_m(p.lat, p.lng, tour_input.start, tour_input.end), p.name)
                    for p in script.selected_pois
                ),
                default=(0.0, "—"),
            )
            print(f"  furthest off A→B:   {worst[0]:6.0f} m  ({worst[1]})")
        else:
            worst_open = max(
                (
                    (haversine_m(tour_input.start[0], tour_input.start[1], p.lat, p.lng), p.name)
                    for p in script.selected_pois
                ),
                default=(0.0, "—"),
            )
            print(
                f"  furthest off A→B:   {worst_open[0]:6.0f} m  ({worst_open[1]}) "
                "— measured from the start; this walk has no B"
            )
    else:
        best = "unknown" if best_elapsed_s is None else f"{best_elapsed_s} s"
        print(f"  walk:               {absent}")
        print(f"  dwell:              {absent}")
        print(
            f"  total (walk+dwell): {best} — the best bounded route the planner "
            "reached, which it then refused"
        )
        print(f"  longest leg:        {absent}")
        print(f"  furthest off A→B:   {absent}")
    # NAME WHICH MECHANISM WAS OPERATIVE, or this line is worse than no line.
    # `radius_m` is the analytic FALLBACK circle. The planner's primary admission
    # test is the Valhalla road polygon, used whenever the contour it asks for is
    # within the engine's limit. Reporting the circle without saying which of the
    # two applied invites a phase to record a shrink that never happened: clamping
    # the contour makes the polygon available again, so the circle can fall while
    # the region the planner actually admits GROWS.
    if iso_minutes > VALHALLA_MAX_CONTOUR_MINUTES:
        note = (
            f"isochrone {iso_minutes} min REFUSED (>{VALHALLA_MAX_CONTOUR_MINUTES} "
            "max), so this circle IS the reach test"
        )
    else:
        note = (
            f"isochrone {iso_minutes} min accepted, so the ROAD POLYGON is the reach "
            "test and this circle is only its fallback"
        )
    print(f"  reach radius:       {radius_m:6.0f} m circle — {note}")
    print(f"  wall clock:         {wall_clock_s:6.2f} s")
    # CLOCK EXCLUSIONS — printed on every DATED run, including when empty, so a
    # Monday and a Tuesday breakdown keep the same shape and read side by side
    # (D1 demo). Omitted entirely on a dateless run, which is what keeps that
    # output byte-identical to the pre-clock harness. Reads the field
    # defensively: the section lands (printing "none") before Route learns to
    # record exclusions in S1.6, and lists them once it has.
    if tour_input.start_datetime is not None:
        exclusions = getattr(route, "clock_exclusions", ()) if route is not None else ()
        print("  clock exclusions:")
        if exclusions:
            # The record carries the closure FACT and the pool DECISION as a flag
            # (ClockExclusion.kept_outside, W4.12); the harness spells the
            # decision out so a breakdown reads the same as the wire's day notes.
            on_route = {p.id for p in route.pois} if route is not None else set()
            for excl in exclusions:
                if excl.poi_id in on_route and excl.kept_outside:
                    tail = " — kept, from the outside"
                elif excl.poi_id in on_route:
                    tail = ""  # a disclosure about a stop that stayed (dusk)
                else:
                    tail = " — not in this day"
                print(f"    • {excl.name} — {excl.reason}{tail}")
        else:
            print("    none")
    # PER-STOP TABLE (plan S2.1; promise columns plan S3.1) — the DAY, not the
    # total: one line per stop with the place category (the carried Phase 1
    # gap: S1.7 promised category labels in the harness printout), the priced
    # stand/visit minutes ("—" when the route carries no pricing — the four
    # legacy harnesses' shape, never a zero that reads as a measurement), the
    # promise SHAPE and QUEUE, and the walking leg INTO that stop
    # (route.transits leg i is the walk into stop i). Printed on EVERY run
    # that has a route, same shape priced or not; W2.11's side-by-side demo
    # table is assembled from these lines verbatim. Sits BELOW the
    # seven-number block so the D1 evidence stays readable above it.
    if route is not None and route.pois:
        # THE PROMISE VIEW (plan S3.1; design §3.1 "each promise carries a
        # clock window"). One cumulative clock feeds BOTH the promises line
        # and the table rows — the same legs in, the same time at each stop —
        # so the printed window and the printed table can never disagree.
        promises_by_poi = {p.poi_id: p for p in route.promises}
        # A stop the clock voided but the day KEEPS, from the outside — the
        # promise-native planner records it as a clock exclusion flagged
        # `kept_outside` (W4.12: this used to string-match the reason for
        # "outside only", and went silently blank the day the wording was made
        # plain — a decision belongs in a field, not in a sentence). Distinct
        # from an excluded-and-dropped POI, which never appears in route.pois
        # and so never reaches this table.
        outside_only = {e.poi_id for e in route.clock_exclusions if e.kept_outside}
        priced = route.planned_visit_seconds
        start_dt = (
            datetime.fromisoformat(tour_input.start_datetime)
            if tour_input.start_datetime is not None
            else None
        )

        def _at_stop_seconds(poi) -> int:
            promise = promises_by_poi.get(poi.id)
            if promise is not None:
                return shape_total_seconds(promise.shape)
            return (priced.get(poi.id) or 0) if priced else 0

        def _clock(seconds: int) -> str:
            if start_dt is not None:
                return (start_dt + timedelta(seconds=seconds)).strftime("%H:%M")
            return f"+{seconds // 60}m"  # undated run: minutes from the start

        windows: dict[str, tuple[int, int]] = {}
        elapsed = 0
        for i, poi in enumerate(route.pois):
            if i < len(route.transits):
                seg = route.transits[i]
                elapsed += seg.leg_seconds if seg.leg_seconds is not None else seg.walk_seconds
            arrive = elapsed
            elapsed += _at_stop_seconds(poi)
            windows[poi.id] = (arrive, elapsed)

        # The promises line, ABOVE the table: kind, stop name, arrive-depart.
        # An empty tuple (every pre-S3.6 route) prints no line at all — the
        # identity default.
        if route.promises:
            names = {p.id: p.name for p in route.pois}
            print("  promises:")
            for promise in route.promises:
                window = windows.get(promise.poi_id)
                span = (
                    f"{_clock(window[0])}-{_clock(window[1])}"
                    if window is not None
                    else "no window — not seated on this route"
                )
                stop_name = names.get(promise.poi_id, promise.poi_id)
                print(f"    • {promise.kind:<6} {stop_name:<34} {span}")

        print("  per-stop:")
        print(
            f"     {'#':>2}  {'stop':<34} {'category':<10} {'visit':>7}  "
            f"{'shape':>10}  {'queue':>5}  {'walk-in':>7}"
        )
        for i, poi in enumerate(route.pois):
            if i < len(route.transits):
                seg = route.transits[i]
                leg_s = seg.leg_seconds if seg.leg_seconds is not None else seg.walk_seconds
                walk_in = f"{round(leg_s / 60)} min"
            else:
                walk_in = "—"
            visit_s = priced.get(poi.id) if priced else None
            visit = f"{round(visit_s / 60)} min" if visit_s is not None else "—"
            promise = promises_by_poi.get(poi.id)
            if poi.id in outside_only:
                shape = "closed — outside only"
            elif promise is not None:
                # The at-place minutes are outside + inside, NEVER the queue —
                # design §3.3: "folding them into one 66 makes the wait
                # permanent". The queue has its own column; the sum is the
                # pricer's own named quantity.
                at_place = shape_at_place_seconds(promise.shape)
                side = "in" if promise.shape.goes_inside else "out"
                shape = f"{round(at_place / 60)} min {side}"
            else:
                # Pre-promise fallback — the planner named no promise for this
                # stop (the list caps at MAX_PROMISES). The minutes come from the
                # route's own pricing, and so does the SIDE OF THE DOOR whenever
                # the route carries one: `visit_goes_inside` is what the planner
                # DECIDED at this stop's arrival hour, while `poi_has_interior`
                # only says the building HAS an interior. They disagree exactly
                # when the planner collapsed a stop to its exterior — a party
                # ceiling, a `wall` end, or the timebox repair's step outside —
                # and the raw test then prints "in" over a visit that never goes
                # in. The unpriced legacy harnesses carry no such map and keep
                # the raw test, which is all they ever had.
                inside = route.visit_goes_inside.get(poi.id)
                if inside is None:
                    inside = poi_has_interior(poi)
                side = "in" if inside else "out"
                shape = f"{round(visit_s / 60)} min {side}" if visit_s is not None else f"— {side}"
            queue_s = promise.shape.queue_seconds if promise is not None else 0
            queue = f"{round(queue_s / 60)}m" if queue_s else "—"
            print(
                f"     {i + 1:>2}  {poi.name:<34} {poi.place_category:<10} "
                f"{visit:>7}  {shape:>10}  {queue:>5}  {walk_in:>7}"
            )
        # AIKO'S HONESTY LINE (plan S3.1 deviation ii; design §6: clock-native
        # planning is "a promise without hours under it"): on a dated run, say
        # how many of the gated stops rest on hours that are not the map's.
        # GATED = a DOOR — `gated is True`, or hours on record (hours imply a
        # door on a legacy row). COUNTED = every door whose source is not
        # "map": a guess, no source, or no hours at all (Docs/adr/0006: the
        # map is the one source spoken plainly; unknown hours fail open WITH
        # that disclosure). Printed even at 0 so a clean run SAYS it is clean;
        # omitted on undated runs (no clock, no gate) and when no stop on the
        # route has a door.
        if start_dt is not None:
            gated = [
                p for p in route.pois
                if p.gated is True or p.opening_hours is not None
            ]
            if gated:
                unverified = sum(
                    1 for p in gated
                    if p.opening_hours_source != "map" or p.opening_hours is None
                )
                print(
                    f"  hours unverified for {unverified} of the {len(gated)} "
                    "gated stops on this route"
                )
    print("  ───────────────────────────────────────────────────────────")


def _print_refusal(
    a,
    *,
    header: str,
    duration_min: int,
    round_trip: bool,
) -> None:
    """Structured stdout for a refusal — a Phase 6 RED density refusal OR an empty
    delivery (density passed but no stop survived selection, e.g. a lensed thin
    area where every reachable POI fell to a walk-by).

    Includes fill_ratio + anchor count, max_supportable_duration, and
    one_way_alternative_destination when applicable. Prints actionable
    next steps so the harness's caller (the skill) can relay them.
    """
    print(header)
    print(f"  fill_ratio:        {a.fill_ratio:.2f} (target ≥ 1.0)")
    print(f"  anchor_candidates: {a.anchor_candidate_count} (target ≥ 4)")
    print(f"  cluster_compactness: {a.cluster_compactness:.2f} (target ≤ 0.6)")
    print(f"  reachable_pois:    {a.reachable_poi_count}")
    print(f"  reachable_beats:   {a.reachable_beat_count}")
    print(f"  walk_radius_m:     {a.walk_radius_m:.0f}")
    print()
    print("  Recommendations:")
    if a.max_supportable_duration_min and a.max_supportable_duration_min < duration_min:
        print(
            f"    • Try a {a.max_supportable_duration_min}-min "
            f"{'round-trip' if round_trip else 'one-way'} instead "
            f"(corpus capacity supports this length)."
        )
    if round_trip and a.one_way_alternative_destination:
        print(
            f"    • Try one-way ending at {a.one_way_alternative_destination!r} "
            f"(denser corpus reachable on a one-way path)."
        )
    if not (
        (a.max_supportable_duration_min and a.max_supportable_duration_min < duration_min)
        or (round_trip and a.one_way_alternative_destination)
    ):
        print(
            "    • Try a different starting area; this start point is too sparse "
            "for the requested duration. Consult "
            "`data/{city}/tourability_summary.md` for nearest GREEN starts."
        )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start", required=True, help="'lat,lng' or POI/Area name")
    parser.add_argument(
        "--end",
        default=None,
        help=(
            "'lat,lng' or POI/Area name for a fixed destination B. Omit for an open "
            "walk. Mutually exclusive with --round-trip."
        ),
    )
    parser.add_argument("--duration", type=int, required=True, help="Minutes")
    parser.add_argument("--lenses", default="", help="Comma-separated child lenses")
    parser.add_argument("--round-trip", action="store_true")
    parser.add_argument("--theme", default="", help="Optional theme hint")
    parser.add_argument("--city-slug", default="paris")
    # THE PLANNER'S CLOCK (redesign §2.2/§2.3). No --date = dateless planning,
    # byte-identical to before the clock existed — which is also what keeps the
    # W1.1 before-picture comparable to a post-clock dateless run.
    parser.add_argument(
        "--date",
        default=None,
        help="Walk date, YYYY-MM-DD. Omit for dateless planning (today's behaviour).",
    )
    parser.add_argument(
        "--time",
        default="09:00",
        help="Walk start time, HH:MM (used only with --date; default 09:00).",
    )
    parser.add_argument(
        "--end-hardness",
        choices=["wall", "firm", "open"],
        default="firm",
        help="How hard the end is: wall keeps visible slack, firm is today's "
        "behaviour, open never pads toward the requested length.",
    )
    # WHO IS WALKING (redesign §2.4, plan S2.1). A preset is a SHORTCUT over
    # the axis flags below — main() feeds both onto TourInput and through
    # resolve_party_axes, where an explicitly-passed axis always wins. No
    # flags = today's behaviour, byte-identical.
    parser.add_argument(
        "--party",
        choices=["solo", "couple", "family", "take-it-easy", "with-luggage"],
        default=None,
        help="Party preset — a shortcut over the axis flags (design §2.4).",
    )
    parser.add_argument(
        "--max-stop-minutes",
        type=int,
        default=None,
        help="Ceiling on any ONE stop, minutes (Nadia's six-minute ceiling).",
    )
    parser.add_argument(
        "--max-leg-minutes",
        type=int,
        default=None,
        help="Hard cap on any single walking leg, minutes (Rosemary's twelve).",
    )
    parser.add_argument(
        "--walking-pace",
        type=float,
        default=None,
        help="Pace multiplier ≥ 1.0 on walking time; 2.0 = half speed.",
    )
    parser.add_argument(
        "--rest-cadence-minutes",
        type=int,
        default=None,
        help="Accumulated walking minutes before a bench/toilet stop is seated.",
    )
    parser.add_argument(
        "--escape-radius-m",
        type=int,
        default=None,
        help="Every stop must sit within this distance of the start, metres.",
    )
    parser.add_argument(
        "--route-surface",
        choices=["any", "no_stairs", "step_free"],
        default=None,
        help="What the route may be made of; no_stairs/step_free ride Valhalla "
        "costing (W2.1-proven).",
    )
    parser.add_argument(
        "--narration-register",
        choices=["solo", "warm", "family"],
        default=None,
        help="Explicit narration register — an axis flag like the others, so "
        "a preset PAIR (e.g. take-it-easy + solo, design §2.4) can be built "
        "without a party value for the pair itself.",
    )
    # PROMISES AND THE SKY (plan S3.1 — the harness speaks promises before
    # the planner emits them). --pin resolves through the SAME lookup ladder
    # as --start (design §3.2: Théo pins one thing absolutely; Julien pins
    # nothing and wants an open walk). --weather hands the planner the sky's
    # decision (design §2.5: fetched, never asked). Both omitted = today's
    # request, byte-identical.
    parser.add_argument(
        "--pin",
        action="append",
        metavar="NAME_OR_LATLNG",
        help="Pin this stop into a promise — a POI name or 'lat,lng'; "
        "repeatable. Must land on a corpus POI.",
    )
    parser.add_argument(
        "--weather",
        choices=["dry", "rain", "auto"],
        default=None,
        help="Plan for this sky: dry/rain pass straight through; auto "
        "fetches the forecast for --date at the start point (fail-open). "
        "Omit for no signal — today's behaviour.",
    )
    parser.add_argument(
        "--haiku",
        action="store_true",
        help="Use real Haiku for glue (requires ANTHROPIC_API_KEY)",
    )
    parser.add_argument(
        "--canned",
        action="store_true",
        help=(
            "Use the deterministic MockGlueClient for glue: transitions are fixed "
            "strings, not written by a model. Free, and must be asked for."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Override output dir (default: data/{city_slug}/tours/)",
    )
    parser.add_argument(
        "--no-markdown",
        action="store_true",
        help="JSON only (skip the markdown render).",
    )
    return parser


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()

    # WHO WRITES THE TRANSITIONS — decided here, before any work, because a
    # refusal that arrives after selection and routing has already spent the
    # user's time. Fails closed like get_provider / get_drafter /
    # build_full_verifier: MockGlueClient used to be the SILENT default, so a
    # tour the owner read could be part fixed-string prose under a
    # "validation: PASS" header with nothing naming it.
    if args.haiku and args.canned:
        parser.error(
            "--haiku and --canned contradict: one uses a real model for the "
            "transitions and the other uses fixed strings. Pass exactly one."
        )
    if not (args.haiku or args.canned):
        parser.error(
            "refusing to write a tour without saying who wrote the transitions. "
            "Pass --haiku for real Haiku glue (spends money), or --canned for "
            "the deterministic fixed-string glue (free). There is no default: "
            "canned transitions presented as a finished tour is the defect this "
            "replaces."
        )
    if args.weather == "auto" and not args.date:
        parser.error(
            "--weather auto fetches a forecast, and a forecast is for a day: "
            "pass --date too, or state the sky yourself with --weather dry/rain."
        )

    project_root = Path(__file__).resolve().parent.parent
    out_dir = (
        Path(args.output_dir)
        if args.output_dir
        else project_root / "data" / args.city_slug / "tours"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    driver = create_driver()
    try:
        start_coords, start_label = _resolve_start(driver, args.start, args.city_slug)
        # Resolved through the SAME resolver as --start so a name, a substring and a
        # bare "lat,lng" all behave identically at both ends. The label is discarded:
        # TourInput carries start_label and has no end_label field.
        end_coords: tuple[float, float] | None = None
        if args.end:
            end_coords, _ = _resolve_start(driver, args.end, args.city_slug)
        # Pins resolve BEFORE the corpus load: a mistyped pin is an input
        # error (exit 2, naming the pin), not something to discover after
        # seconds of snapshot work.
        pinned_poi_ids = _resolve_pinned_poi_ids(driver, parser, args.pin, args.city_slug)
        # THE SKY (design §2.5, plan S3.1): dry/rain are the caller's own
        # statement; auto asks open-meteo for the walk's date at the start
        # point. A None answer is "no signal" — say so in one honest line and
        # plan a plain day, never block and never guess.
        weather = args.weather if args.weather in ("dry", "rain") else None
        if args.weather == "auto":
            weather = fetch_rain_likelihood(args.date, start_coords[0], start_coords[1])
            if weather is None:
                print("  forecast unavailable — planning a plain day")
        snapshot = load_paris_corpus(driver, city_slug=args.city_slug)

        lenses = [s.strip() for s in args.lenses.split(",") if s.strip()] or None
        # PARTY FLAGS RIDE CONDITIONALLY (plan S2.1): an omitted flag must stay
        # OUT of the constructor kwargs, because resolve_party_axes reads
        # `model_fields_set` to tell an explicit `--route-surface any` (wins
        # over the preset) from the untouched default (preset may fill it).
        party_fields: dict[str, object] = {}
        if args.party:
            party_fields["party"] = args.party.replace("-", "_")
        for axis_flag in (
            "max_stop_minutes",
            "max_leg_minutes",
            "walking_pace",
            "rest_cadence_minutes",
            "escape_radius_m",
            "route_surface",
            "narration_register",
        ):
            axis_value = getattr(args, axis_flag)
            if axis_value is not None:
                party_fields[axis_flag] = axis_value
        # Same conditional-kwarg rule as the axis flags above: an omitted
        # --pin/--weather stays OUT of the constructor call, so a flagless
        # request builds a TourInput byte-identical to a pre-promise one
        # (fields at their defaults, model_fields_set untouched).
        promise_fields: dict[str, object] = {}
        if pinned_poi_ids:
            promise_fields["pinned_poi_ids"] = pinned_poi_ids
        if weather is not None:
            promise_fields["weather"] = weather
        tour_input = resolve_party_axes(
            TourInput(
                start=start_coords,
                duration_min=args.duration,
                city_slug=args.city_slug,
                round_trip=bool(args.round_trip),
                end=end_coords,
                lenses=lenses,
                theme_hint=args.theme.strip() or None,
                start_label=start_label,
                start_datetime=f"{args.date}T{args.time}" if args.date else None,
                end_hardness=args.end_hardness,
                **party_fields,
                **promise_fields,
            )
        )

        t_select = time.perf_counter()
        try:
            # M3: routed leg costs when the local Valhalla is up; total
            # haversine fallback (sticky-degraded after one refusal) when not.
            with RoutingClient() as routing_client:
                route = select_route(tour_input, snapshot, routing_client=routing_client)
        except TourabilityRefusedError as exc:
            rt = bool(args.round_trip)
            _print_refusal(
                exc.assessment,
                header=(
                    f"\u2717 TOURABILITY REFUSED (RED) \u2014 {start_label} "
                    f"{args.duration}-min {'round-trip' if rt else 'one-way'}"
                ),
                duration_min=args.duration,
                round_trip=rt,
            )
            return 3
        except CertificationPlanningInfeasibleError as exc:
            # CAUGHT, not allowed to escape. Until this clause existed, a request
            # that merely did not fit its certification band ended the run in a
            # Python traceback — the harness printed none of the numbers that
            # explain WHY, and the one command meant to demonstrate the reported
            # bug produced a stack trace instead of evidence. Everything printed
            # here already lives on the exception (src/tour/selection.py:456-487);
            # nothing is recomputed, so this cannot disagree with the planner.
            rt = bool(args.round_trip)
            lo, hi = exc.minimum_elapsed_seconds, exc.maximum_elapsed_seconds
            best = exc.best_elapsed_seconds
            print(
                f"✗ CERTIFICATION PLANNING INFEASIBLE — {start_label} "
                f"{args.duration}-min {'round-trip' if rt else 'one-way'}"
            )
            print(f"  message:     {exc}")
            print(f"  policy:      {exc.policy_id}")
            print(f"  reason:      {exc.reason}")
            print(f"  required:    {lo}-{hi}s ({lo // 60}-{hi // 60} min)")
            print(
                "  best found:  "
                + ("none" if best is None else f"{best}s ({best // 60} min)")
            )
            if exc.gap_minutes is not None:
                print(f"  gap:         {exc.gap_minutes} min")
            print()
            print("  Alternatives:")
            if not exc.alternatives:
                print("    • none offered by the planner")
            for alt in exc.alternatives:
                if alt.kind == "closer_b" and alt.poi_id:
                    print(
                        f"    • closer_b: end at {alt.poi_id} "
                        f"({alt.lat:.5f}, {alt.lng:.5f}) at {alt.duration_min} min"
                    )
                elif alt.kind == "extend":
                    print(
                        f"    • extend: keep this destination, ask for "
                        f"{alt.duration_min} min"
                    )
                elif alt.kind == "loop":
                    print(
                        f"    • loop: drop the destination, walk {alt.duration_min} "
                        f"min from the start"
                    )
                else:
                    print(
                        f"    • {alt.kind}: {alt.duration_min} min, "
                        f"drop_end={alt.drop_end}"
                    )
            print()
            _print_breakdown(
                tour_input=tour_input,
                wall_clock_s=time.perf_counter() - t_select,
                best_elapsed_s=exc.best_elapsed_seconds,
            )
            return 3
        t_select = time.perf_counter() - t_select

        if not route.pois:
            # Density passed but no stop survived selection \u2014 e.g. a lensed thin
            # area where every reachable POI fell to a walk-by. Route through the
            # SAME structured refusal (actionable alternatives) instead of a bare
            # "no POIs reachable". Reuse the attached assessment, else recompute.
            rt = bool(args.round_trip)
            assessment = route.tourability or assess_snapshot(tour_input, snapshot)
            lens_note = " for your interest" if lenses else ""
            _print_refusal(
                assessment,
                header=(
                    f"\u2717 EMPTY TOUR \u2014 {start_label} {args.duration}-min "
                    f"{'round-trip' if rt else 'one-way'}: no stop survived "
                    f"selection (every reachable POI fell to a walk-by{lens_note})."
                ),
                duration_min=args.duration,
                round_trip=rt,
            )
            return 3

        t_beats = time.perf_counter()
        beat_sequence = _build_beat_sequence(route, snapshot, lenses)
        t_beats = time.perf_counter() - t_beats

        if args.haiku:
            client = HaikuGlueClient()
            t_gen = time.perf_counter()
            script = generate(beat_sequence, route, tour_input, glue_client=client)
            t_gen = time.perf_counter() - t_gen
            in_tok = client.input_tokens
            out_tok = client.output_tokens
            cost_usd = (
                in_tok / 1_000_000 * HAIKU_INPUT_USD_PER_MTOK
                + out_tok / 1_000_000 * HAIKU_OUTPUT_USD_PER_MTOK
            )
            cost_kind = "measured"
        else:
            # CANNED GLUE, and it must be ASKED FOR. Every transition sentence in
            # the tour this prints comes from MockGlueClient's fixed table
            # (src/tour/glue_client.py), not from a model. This used to be the
            # silent default, so a tour the owner read could be part canned prose
            # under a "validation: PASS" header with nothing naming it — the same
            # shape of lie as reporting a faithfulness pass that never ran.
            #
            # Reached only with an explicit --canned; the guard after
            # parse_args() has already refused the no-flag case. The summary
            # below still prints which implementation produced the prose.
            t_gen = time.perf_counter()
            script = generate(beat_sequence, route, tour_input, glue_client=MockGlueClient())
            t_gen = time.perf_counter() - t_gen
            # Project cost upper bound from a one-shot counting pass.
            in_tok, out_tok, cost_usd = _project_cost_upper_bound(
                beat_sequence, route, tour_input
            )
            cost_kind = "projected"

        # VERIFY the deterministic stitch (traceability + forbidden + rapidfuzz
        # provenance + faithfulness) and attach the merged report. There is no
        # recompose ladder here any more: this harness renders the $0 stitch, and
        # the whole-tour composer it used to re-run died with compose.py. A failing
        # report is reported and exits 1 rather than re-rolling the same
        # deterministic stitch. Chunk text isn't loaded here, so provenance is a
        # no-op until the corpus backfills source_passage.
        #
        # FAITHFULNESS IS NOT CHECKED HERE, and that is now stated rather than
        # implied. This call used to pass no checker at all, and the gate quietly
        # substituted the trusting Mock — so the "validation: PASS" line below
        # announced a pass on a check that never ran, on the very script the
        # owner reads. The opt-out is explicit, and the printed summary says
        # UNVERIFIED. Wiring the real Haiku checker here is a deliberate spend
        # decision, not something this harness should do by default.
        beats_by_id = {b.id: b for plan in beat_sequence.poi_beats for b in plan.beats}
        verify = build_full_verifier(
            beat_sequence,
            beats_by_id,
            allow_unverified_faithfulness=True,
            spine_area=route.spine_area,
            disclosed_place_names=tuple(e.name for e in route.clock_exclusions),
        )
        script = script.model_copy(update={"validation": verify(script)})

        wall_clock = t_select + t_beats + t_gen

        gen_id = _generated_id(start_label, args.duration)
        json_path = out_dir / f"{gen_id}.json"
        md_path = out_dir / f"{gen_id}.md"

        json_path.write_text(json.dumps(script.model_dump(mode="json"), indent=2) + "\n")
        if not args.no_markdown:
            md = render_markdown(
                script,
                cost_usd=cost_usd,
                haiku_input_tokens=in_tok,
                haiku_output_tokens=out_tok,
                wall_clock_seconds=wall_clock,
                beat_sequence=beat_sequence,
                tourability=route.tourability,
            )
            md_path.write_text(md)

        # ----- summary -----
        passed = script.validation.passed
        print(f"\u2713 generated_id={gen_id}")
        print(f"  json:        {json_path}")
        if not args.no_markdown:
            print(f"  markdown:    {md_path}")
        print(f"  start:       {start_label} ({start_coords[0]:.5f}, {start_coords[1]:.5f})")
        print(f"  spine:       {route.spine_area}")
        print(f"  POIs:        {len(route.pois)}  → {[p.name for p in route.pois]}")
        print(
            f"  walk:        {script.total_walking_seconds // 60} min "
            f"({script.total_walk_distance_m} m)"
        )
        print(f"  audio:       {script.total_audio_seconds // 60} min")
        print(
            f"  planned:     {script.total_planned_seconds // 60} min "
            f"(err-short of {args.duration})"
        )
        print(f"  cost:        ${cost_usd:.5f} ({cost_kind}: {in_tok} in + {out_tok} out)")
        print(f"  wall_clock:  {wall_clock:.2f} s")
        _print_breakdown(
            tour_input=tour_input,
            wall_clock_s=wall_clock,
            route=route,
            script=script,
        )
        print(
            f"  validation:  {'PASS' if passed else 'FAIL'} "
            f"(untraceable={len(script.validation.untraceable_sentences)}, "
            f"forbidden={len(script.validation.forbidden_phrase_hits)})"
        )
        # Say plainly which checks actually ran, and what wrote the prose. "PASS"
        # above covers traceability and forbidden phrases; it has never covered
        # faithfulness on this path, and printing only PASS is what made an
        # unverified tour read as verified. The same applies to the transitions:
        # without --haiku they are canned strings, and a reader judging the tour
        # deserves to know which sentences no model wrote.
        print(
            "  faithfulness: "
            + (
                "CHECKED"
                if script.validation.faithfulness_checked
                else "NOT CHECKED — no entailment pass ran on this tour"
            )
        )
        print(
            "  glue:        "
            + (
                "REAL (Haiku)"
                if args.haiku
                else "CANNED — transitions are fixed strings from MockGlueClient, "
                "not written by a model; pass --haiku for real ones"
            )
        )
        if route.tourability is not None and route.tourability.status == "YELLOW":
            tb = route.tourability
            hints: list[str] = []
            if tb.max_supportable_duration_min:
                hints.append(f"consider {tb.max_supportable_duration_min}-min")
            if tb.round_trip and tb.one_way_alternative_destination:
                hints.append(
                    f"or one-way ending at {tb.one_way_alternative_destination!r}"
                )
            hint_str = "; ".join(hints) if hints else "thin tour"
            print(
                f"  tourability: YELLOW — fill_ratio={tb.fill_ratio:.2f} ({hint_str})"
            )
        else:
            print("  tourability: GREEN")

        return 0 if passed else 1
    finally:
        driver.close()


if __name__ == "__main__":
    sys.exit(main())
