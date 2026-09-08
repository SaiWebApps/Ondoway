"""THE contingency set — one server-side builder (Phase 5 S5.7; design §4.6).

"The server is the only place a plan decision is made. At plan time and after every
replan it emits a CONTINGENCY SET alongside the plan: precomputed answers to the
divergences that matter — running late or early by bands, a stop skipped, a promise
at risk, wrap-up from here — each with its alternates and their audio. The phone
selects from that set. It holds no scoring, no candidate pool, no policy." (§4.6)

Every entry here is THE planner called with a `ReplanContext` — a contingency IS a
replan taken early — never a second decision procedure (plan §10.8). Called only by
the API, only after the day has been planned. What the W5.2 panel locked (phase5-
ledger.md, "LOCKED RULINGS", 2026-08-18) shapes the set:

- R1.1 wrap-up from EVERY stop, floor zero, one leg home a legal answer (11/11).
- R1.2 a skip for every stop; its answer never adds a building or new narration
  (every skip entry's pool is the PLANNED day — structural, so a skip's authoring
  spend is zero; `ContingencySet.authoring_units` prints the total before billing).
  A door_closed entry (M6) names a standby NOT on the day — that IS authoring spend.
- R1.3 late bands at 10 / 20 / 30-40 minutes for days with a finish clock; OPEN days
  band by wall-clock MINUTES LEFT (Fiona & Dev); nothing under 10 opens the set.
- R1.4 early bands 10-20 / 20-40 LENGTHEN what is there (the freed minutes granted
  through the one grant rule), never a new place.
- R1.5 a promise is a thing the PERSON asked for: the caller passes the person's
  protected set; the planner's own anchor on an open unpinned walk never asks.
- R2 the ONE question: one sentence, TWO arms, EACH naming the place kept and a
  CLOCK TIME; the safe arm first under a wall; none of the banned words; a protected
  rest is shortened or kept, never an arm to be dropped; the default is the kept arm
  (the safe arm under a wall); everything spoken is on screen (§4.4.2).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from src.city_registry import country_code

from .contract import POI, ReplanContext, Route, TourInput

# A story stop this close to where the person said the day begins or ends IS that
# place (W5.14: Rosemary's "start Musée d'Orsay, end Musée d'Orsay"). ONE definition,
# in the audio placement rule (Phase 7 S7.3; W7.2 R1 — the finish and the start square
# take the same radius on the phone): read here, never restated.
from .placement import OWN_PLACE_RADIUS_M
from .premium_tour import plan_premium_tour
from .routing import haversine_m, leg_walk_seconds
from .selection import (
    HOURS_SOURCE_MAP,
    _clock_exclusion_reason,
    _walk_arrivals,
    grant_freed_seconds,
    hours_are_guessed,
)
from .visit_time import listened_seconds, stop_seconds, visit_ceiling_seconds

#: Words the panel ruled fail plain language ON SCREEN AND ALOUD (W4.2 Paulo; W5.2 R2.2:
#: every panellist's additions). A question or screen line carrying one is a defect.
BANNED_WORDS: tuple[str, ...] = (
    "gated",
    "anchor",
    "40m",
    "err-short",
    "wall",
    "hard finish",
    "margin",
    "promise",
    "fabric",
    "replan",
    "re-time",
    "retime",
    "band",
    "contingency",
    "contingencies",
    "highlight",
    "famous",
    "must-see",
    "you may know",
    "tier",
    "late",
    "behind",
    "on time",
    "left the route",
    "dark",
)

#: R1.3 — late bands (minutes) for a day with a finish clock; nothing under 10.
LATE_BANDS: tuple[tuple[int, int], ...] = ((10, 20), (20, 30), (30, 40))
#: R1.4 — early bands.
EARLY_BANDS: tuple[tuple[int, int], ...] = ((10, 20), (20, 40))
#: R1.3 (Fiona & Dev) — an OPEN day bands by wall-clock minutes LEFT.
MINUTES_LEFT_BANDS: tuple[tuple[int, int], ...] = ((0, 20), (20, 40), (40, 60), (60, 90), (90, 600))
#: §4.6 — a phone-vs-server clock divergence beyond this is a REPORTED defect (S5.10).
#: Three minutes: under the panel's smallest band (10) and above GPS/pace jitter; the
#: design names the mechanism, not the number, so the number is stated here once.
RETIME_TOLERANCE_SECONDS: int = 180
#: A rest shortened below this is a rest removed (R2.3): three minutes is the shortest
#: sit Rosemary's file records as a rest (05: "two more sits inside that stretch").
SHORTEST_REST_SECONDS: int = 180
#: The screen's line when an OPEN walk is wrapped up and nothing is left but the day
#: itself (Phase 6 S6.4, W6.2 R2/R8): what the day was, handed back where they stand —
#: never "Straight to your start". One sentence, no clock, no banned word.
OPEN_WALK_DAY_LINE: str = "That's the walk — the rest of the day is yours from here."
#: The screen's line when the wrap-up is from the place the day ends at: done.
DAY_DONE_LINE: str = "That's the walk."


@dataclass(frozen=True)
class Contingency:
    """One precomputed answer. `trigger` is a MATCHER, never a policy."""

    contingency_id: str
    trigger: dict
    from_stop_index: int
    stop_ids: tuple[str, ...]
    route: Route | None
    screen_text: str
    question: str | None = None
    default_arm: str | None = None  # "keep" | "shorten" when a question exists
    alternate_stop_ids: tuple[str, ...] = ()
    alternate_route: Route | None = None
    at_risk_stop_id: str | None = None
    #: When this entry's day ends at the finish (the phone shows it; "" if unclocked).
    finish_hhmm: str = ""


@dataclass(frozen=True)
class ContingencySet:
    plan_version: int
    retime_tolerance_seconds: int
    entries: tuple[Contingency, ...]
    #: Stops across every entry that were NOT on the planned day — the alternate-
    #: authoring spend (one provider call each, deviation iii). Printed before it is
    #: billed; zero when every entry is drawn from the planned day.
    authoring_units: int
    notes: tuple[str, ...] = field(default_factory=tuple)


_BANNED_RE = re.compile(
    r"(?<![A-Za-z])(?:" + "|".join(re.escape(w) for w in BANNED_WORDS) + r")(?![A-Za-z])",
    re.IGNORECASE,
)


def plain(text: str) -> str:
    """Refuse a sentence a person should never read (R2.2). Returns it unchanged.

    WORDS, not letters (Phase 6 W6.2, Théo and Camille): the first version matched
    substrings, so "wallpaper" was a wall, "husband" a band, "compromise" a promise,
    "later" late and "darkness" dark — and a close (narrator content, through this
    same door) could not say true things. A banned word now matches only at word
    boundaries; "contingenc" still catches contingency/contingencies, "re-time" and
    "retime" their forms, "on time" and "hard finish" as phrases."""
    hit = _BANNED_RE.search(text)
    if hit is not None:
        raise ValueError(
            f"the panel banned {hit.group(0).lower()!r} from anything spoken or shown: {text!r}"
        )
    return text


def hhmm(clock: datetime | None) -> str:
    return "" if clock is None else clock.strftime("%H:%M")


_SENTENCE_BREAK = re.compile(r"[.!?](?:\s|$)")


def one_sentence(text: str) -> str:
    """Refuse anything the session would show or say that is more than ONE sentence
    (design §4.4.4 "One sentence, maximum. Over a screaming five-year-old, mute beats
    graceful"; W5.2 R2.1 the question is one sentence with two arms). A sentence
    break is a full stop, question or exclamation mark followed by a space or the
    end; a clock ("15:09") or a separator ("·") is not one. Returns it unchanged."""
    stripped = text.strip()
    breaks = [m.start() for m in _SENTENCE_BREAK.finditer(stripped)]
    if len(breaks) > 1 or (breaks and breaks[0] != len(stripped) - 1):
        raise ValueError(f"the panel allows ONE sentence on screen or aloud, not: {text!r}")
    return text


def stop_clocks(
    route: Route,
    tour_input: TourInput,
    *,
    clock_start: datetime | None = None,
    listening_rate: float = 1.0,
    audio_seconds_by_id: Mapping[str, int] | None = None,
) -> list[tuple[POI, datetime | None, datetime | None]]:
    """(stop, arrival, departure) per stop — THE one arrival walk (`_walk_arrivals`),
    read off the FINAL route's street legs and its priced visits. THE SERVER'S ONE
    RE-TIMING EXPRESSION (Phase 5 S5.10's seam): the phone's own re-timing is
    compared against this and a divergence is REPORTED, never corrected — so
    nothing else may spell the server's clock; `finish_clock` and the wire's
    per-stop `start_time` are views of it.

    A stop costs the longer of its priced visit and what it SAYS at this
    person's learned listening rate — `stop_seconds(visit, listened_seconds(audio,
    rate))`, the final gate's own rule (`served_dwell_seconds`) — when the caller
    hands the audio map (`selection.planned_audio_by_poi`); with no map the visit
    alone, byte-identical to before. ``clock_start`` overrides the input's start
    (a tail replanned from HERE at NOW)."""
    start = clock_start
    if start is None and tour_input.start_datetime is not None:
        start = datetime.fromisoformat(tour_input.start_datetime)
    audio = audio_seconds_by_id or {}
    legs = [leg_walk_seconds(t) for t in route.transits]
    out = []
    for poi, _hour, seconds, arrival in _walk_arrivals(
        list(route.pois),
        legs,
        clock_start=start,
        # A dated walk hands its arrival clock as a third argument (the
        # arrival-window door check). This pricer reads
        # `route.planned_visit_seconds`, which selection already priced at
        # each stop's arrival — doors included — so the clock is accepted
        # and unused: re-checking here would be a second spelling of a
        # decision the route already carries.
        price_visit=lambda p, _h, _clock=None: stop_seconds(
            int(route.planned_visit_seconds.get(p.id, 0)),
            listened_seconds(int(audio.get(p.id, 0)), listening_rate),
        ),
    ):
        departure = arrival + timedelta(seconds=seconds) if arrival is not None else None
        out.append((poi, arrival, departure))
    return out


def finish_clock(
    route: Route | None,
    tour_input: TourInput,
    *,
    clock_start: datetime | None,
    listening_rate: float = 1.0,
    audio_seconds_by_id: Mapping[str, int] | None = None,
) -> datetime | None:
    """When this day ENDS at its finish: the one expression's last departure plus
    the closing leg — a VIEW of `stop_clocks`, never a second sum of walks and
    visits (the "two expressions that agree today by coincidence" the plan's S5.10
    names as sabotage). ``None`` when there is no clock or no route."""
    if route is None or clock_start is None:
        return None
    clocks = stop_clocks(
        route,
        tour_input,
        clock_start=clock_start,
        listening_rate=listening_rate,
        audio_seconds_by_id=audio_seconds_by_id,
    )
    into_stops = sum(leg_walk_seconds(t) for t in route.transits[: len(route.pois)])
    closing_leg = max(0, int(route.total_walk_seconds) - into_stops)
    last_departure = clocks[-1][2] if clocks and clocks[-1][2] is not None else clock_start
    return last_departure + timedelta(seconds=closing_leg)


#: The degradation row the server writes when the phone's clock and its own
#: disagree beyond the session's tolerance (S5.10's seam): reported on the
#: reply's `degradations` channel, never corrected in either direction.
SESSION_CLOCK_DIVERGENCE = "session_clock_divergence"


def clock_divergence_seconds(phone_hhmm: str, server_hhmm: str) -> int:
    """Signed seconds the PHONE's clock runs ahead (+) or behind (-) the SERVER's for
    the same stop, both as HH:MM on the same day, wrapped to the nearer half-day."""
    ph, pm = (int(x) for x in phone_hhmm.split(":"))
    sh, sm = (int(x) for x in server_hhmm.split(":"))
    diff = (ph * 60 + pm) - (sh * 60 + sm)
    if diff > 12 * 60:
        diff -= 24 * 60
    elif diff < -12 * 60:
        diff += 24 * 60
    return diff * 60


def _is_story_stop(poi: POI) -> bool:
    return poi.poi_role != "body" and not poi.id.startswith("__end_b")


def _finish_of(tour_input: TourInput) -> tuple[float, float] | None:
    if tour_input.end is not None:
        return tour_input.end
    if tour_input.round_trip:
        return tour_input.start
    return None


def _minutes_left(planned_end: datetime | None, clock: datetime | None) -> int | None:
    if planned_end is None or clock is None:
        return None
    return max(1, int((planned_end - clock).total_seconds() // 60))


def _tail_input(
    tour_input: TourInput,
    *,
    position: tuple[float, float],
    clock: datetime | None,
    minutes: int,
) -> TourInput:
    """The remainder as a request: from HERE, to the day's finish, with the minutes
    left — every other axis (party, pace, cap, lenses, weather, hardness) as asked."""
    finish = _finish_of(tour_input)
    return tour_input.model_copy(
        update={
            "start": position,
            "end": finish,
            "round_trip": False,
            "duration_min": max(1, minutes),
            "start_datetime": clock.isoformat(timespec="minutes") if clock else None,
            "pinned_poi_ids": (),
            "rest_cadence_minutes": None,  # rests already seated ride as protected stops
        }
    )


def own_place_ids(route: Route, tour_input: TourInput) -> tuple[str, ...]:
    """The place the PERSON named as where the day begins or ends, when the day visits it
    (W5.14, all eleven: an art-lens day AT the Orsay lost the Orsay to keep a bench).
    R1.5 stays as locked — the planner's anchor on an open unpinned walk is not a
    promise — but a start or a declared end that is itself a stop of the day was
    named by the person, and a session never drops it silently. Body stops (a bench
    at the start) are already promises by their role."""
    anchors: list[tuple[float, float]] = [tour_input.start]
    if tour_input.end is not None:
        anchors.append(tour_input.end)
    out: list[str] = []
    for poi in route.pois:
        if poi.poi_role == "body" or poi.id.startswith("__end_b"):
            continue
        for lat, lng in anchors:
            if haversine_m(lat, lng, poi.lat, poi.lng) <= OWN_PLACE_RADIUS_M:
                out.append(poi.id)
                break
    return tuple(out)


def at_risk_choice(tail: Route, protected_in_order: list[str]) -> POI | None:
    """Which protected thing the question is about: a REST first — it is the thing
    that can be shortened rather than lost (R2.3), so its arm is a shorter sit —
    else the first protected stop still on the day (its arm is "go straight on")."""
    on_route = {p.id: p for p in tail.pois}
    rests = [
        on_route[pid]
        for pid in protected_in_order
        if pid in on_route and on_route[pid].poi_role == "body"
    ]
    if rests:
        return rests[0]
    for pid in protected_in_order:
        if pid in on_route:
            return on_route[pid]
    return None


def question_text(
    *,
    tail: Route,
    alt: Route | None,
    at_risk_poi: POI,
    rest_seconds: int,
    keep_clock: datetime | None,
    alt_clock: datetime | None,
    finish_name: str,
    end_hardness: str,
) -> tuple[str | None, str | None]:
    """R2 — the ONE question's text and its default, from the two planned arms.
    ``tail`` keeps the protected thing (and overruns); ``alt`` is the day with every
    fabric stop shed and only the protected kept. A rest is SHORTENED, never removed
    (R2.3): the second arm is a shorter sit when that fits, else "go straight on";
    the safe arm comes first under a wall (Marcus). Used by the precomputed set AND
    the live path (W5.14: the live path used to shed silently). Returns (None, None)
    when there is nothing to ask — the two arms are the same day."""
    if keep_clock is None:
        return None, None
    if alt is not None and alt.overrun_seconds > 0 and at_risk_poi.poi_role == "body":
        shortened = rest_seconds - alt.overrun_seconds
        if shortened < SHORTEST_REST_SECONDS or alt_clock is None:
            return None, None
        short_clock = alt_clock - timedelta(seconds=alt.overrun_seconds)
        arm_keep = f"keep your full rest and be at {finish_name} about {hhmm(keep_clock)}"
        arm_short = (
            f"sit {round(shortened / 60)} minutes and be at {finish_name} by {hhmm(short_clock)}"
        )
        safe_first = end_hardness == "wall"
        q = (
            f"{arm_short[0].upper()}{arm_short[1:]}, or {arm_keep}?"
            if safe_first
            else f"{arm_keep[0].upper()}{arm_keep[1:]}, or {arm_short}?"
        )
        return q, ("shorten" if safe_first else "keep")
    if alt is None or alt_clock is None or {p.id for p in alt.pois} == {p.id for p in tail.pois}:
        return None, None
    thing = "your rest" if at_risk_poi.poi_role == "body" else at_risk_poi.name
    arm_keep = f"keep {thing} and be at {finish_name} about {hhmm(keep_clock)}"
    arm_leave = f"go straight on and be at {finish_name} by {hhmm(alt_clock)}"
    safe_first = end_hardness == "wall"
    q = (
        f"{arm_leave[0].upper()}{arm_leave[1:]}, or {arm_keep}?"
        if safe_first
        else f"{arm_keep[0].upper()}{arm_keep[1:]}, or {arm_leave}?"
    )
    return q, ("shorten" if safe_first else "keep")


def build_contingency_set(
    route: Route,
    tour_input: TourInput,
    snapshot,
    *,
    routing_client,
    person: ReplanContext,
    plan_version: int = 1,
    finish_name: str = "your finish",
    plan: Callable[[TourInput, ReplanContext], Route] | None = None,
    audio_seconds_by_id: Mapping[str, int] | None = None,
    clock_start: datetime | None = None,
) -> ContingencySet:
    """Compute the set ONCE, on the server, from the planned day.

    ``person`` carries the person's protected set (R1.5) and anything spent;
    ``plan`` is THE planner (`plan_premium_tour` on the API path; `select_route`
    when no routing client exists — the same planner, without the premium
    wrapper) — never a second decision procedure.
    """
    from .selection import select_route

    if plan is None:
        if routing_client is not None:

            def plan(inp: TourInput, ctx: ReplanContext) -> Route:
                return plan_premium_tour(
                    inp, snapshot, routing_client=routing_client, replan=ctx
                ).route

        else:

            def plan(inp: TourInput, ctx: ReplanContext) -> Route:
                return select_route(inp, snapshot, replan=ctx)

    clocks = stop_clocks(
        route,
        tour_input,
        clock_start=clock_start,
        listening_rate=person.listening_rate,
        audio_seconds_by_id=audio_seconds_by_id,
    )
    story = [(i, poi, arr, dep) for i, (poi, arr, dep) in enumerate(clocks) if _is_story_stop(poi)]
    planned_ids = [p.id for p in route.pois]
    start_clock = (
        clocks[0][1] - timedelta(seconds=leg_walk_seconds(route.transits[0]))
        if clocks and clocks[0][1] and route.transits
        else None
    )
    planned_end = start_clock + timedelta(minutes=tour_input.duration_min) if start_clock else None
    has_finish_clock = planned_end is not None and (
        tour_input.end_hardness in ("wall", "firm") or tour_input.end is not None
    )
    longest = max((leg_walk_seconds(t) for t in route.transits), default=None)
    protected = tuple(pid for pid in person.protected_poi_ids if pid in planned_ids)
    rests = {p.id for p in route.pois if p.poi_role == "body"}
    own = own_place_ids(route, tour_input)
    protected_all = tuple(dict.fromkeys(list(protected) + list(own) + sorted(rests)))
    ceiling_of = lambda p: visit_ceiling_seconds(  # noqa: E731
        p,
        party_ceiling_seconds=(
            tour_input.max_stop_minutes * 60 if tour_input.max_stop_minutes is not None else None
        ),
    )
    visit_of = lambda p: int(route.planned_visit_seconds.get(p.id, 0))  # noqa: E731

    entries: list[Contingency] = []
    notes: list[str] = []
    new_stop_ids: set[str] = set()

    def ctx_from(k_pos: int, *, keep: tuple[str, ...], visited: tuple[str, ...], grants=None):
        return ReplanContext(
            protected_poi_ids=tuple(pid for pid in protected_all if pid in keep),
            longest_leg_ceiling_seconds=longest,
            keep_to_poi_ids=keep,
            visited_poi_ids=visited,
            spent_categories=person.spent_categories,
            floor_zero=True,
            listening_rate=person.listening_rate,
            visit_extension_seconds=dict(grants or {}),
        )

    def replan(inp: TourInput, ctx: ReplanContext) -> Route | None:
        try:
            return plan(inp, ctx)
        except Exception as exc:  # an entry that cannot be planned is recorded, never a crash
            notes.append(f"{type(exc).__name__}: {exc}")
            return None

    def add(
        trigger: dict,
        k: int,
        tail: Route | None,
        *,
        question=None,
        screen_text: str,
        default_arm=None,
        alt: Route | None = None,
        at_risk: str | None = None,
        clock: datetime | None = None,
    ):
        ids = tuple(p.id for p in tail.pois) if tail is not None else ()
        alt_ids = tuple(p.id for p in alt.pois) if alt is not None else ()
        for pid in ids + alt_ids:
            if pid not in planned_ids and not pid.startswith("__end_b"):
                new_stop_ids.add(pid)
        entries.append(
            Contingency(
                contingency_id=f"v{plan_version}-{len(entries) + 1}",
                trigger=trigger,
                from_stop_index=k,
                stop_ids=ids,
                route=tail,
                screen_text=plain(one_sentence(screen_text)),
                question=plain(one_sentence(question)) if question else None,
                default_arm=default_arm,
                alternate_stop_ids=alt_ids,
                alternate_route=alt,
                at_risk_stop_id=at_risk,
                finish_hhmm=hhmm(finish_clock_of(tail, clock)),
            )
        )

    def finish_clock_of(tail: Route | None, clock: datetime | None) -> datetime | None:
        # A view of THE one expression (S5.10): never a second sum here.
        return finish_clock(
            tail,
            tour_input,
            clock_start=clock,
            listening_rate=person.listening_rate,
            audio_seconds_by_id=audio_seconds_by_id,
        )

    def question_for(
        k: int, position, clock, minutes: int, tail: Route | None, keep: tuple[str, ...]
    ):
        """R2 — when the protected remainder overruns, the ONE question (see
        `question_text`). Returns (question, default_arm, alternate, at_risk)."""
        if tail is None or tail.overrun_seconds <= 0 or clock is None:
            return None, None, None, None
        at_risk_poi = at_risk_choice(tail, [pid for pid in protected_all if pid in keep])
        if at_risk_poi is None:
            return None, None, None, None
        at_risk = at_risk_poi.id
        # Arm 2: shed every remaining fabric stop; if it still overruns, shorten the rest.
        alt = replan(
            _tail_input(tour_input, position=position, clock=clock, minutes=minutes),
            ctx_from(k, keep=tuple(pid for pid in keep if pid in protected_all), visited=()),
        )
        q, default_arm = question_text(
            tail=tail,
            alt=alt,
            at_risk_poi=at_risk_poi,
            rest_seconds=visit_of(at_risk_poi),
            keep_clock=finish_clock_of(tail, clock),
            alt_clock=finish_clock_of(alt, clock) if alt is not None else None,
            finish_name=finish_name,
            end_hardness=tour_input.end_hardness,
        )
        if q is None:
            return None, None, alt, at_risk
        return q, default_arm, alt, at_risk

    def screen_for(tail: Route | None, clock: datetime | None, *, next_name: str | None) -> str:
        # The SHAPE only — no clock (W5.13). A precomputed line fires at a time the
        # server did not know; the phone re-times the finish itself (its one
        # expression, §4.1) and puts THAT clock beside this line. The entry still
        # carries `finish_hhmm` (the server's clock at the entry's assumed time)
        # for the record; the question's arms keep their clocks (R2.1).
        del clock
        if tail is None:
            return "Carry on — nothing changes from here."
        if next_name:
            return f"Next: {next_name}"
        # Nothing left but the way home. On an OPEN walk there is no home to head for
        # (Phase 6 W6.2 R2/R8, 11/11: "Straight to your start" is a direction to
        # nowhere — Fiona & Dev end at dinner, wherever that is): the day is handed
        # back, where they stand. With a finish, the place (S5.19).
        if _finish_of(tour_input) is None:
            return OPEN_WALK_DAY_LINE
        return "Straight to " + finish_name

    # R1.1 — WRAP-UP FROM HERE, from EVERY stop the person can be at — rests included
    # (Phase 6 W6.2, Rosemary: "the one place I am most likely to decide to go home is
    # where the button does nothing"): one leg home, no stop, floor zero.
    finish = _finish_of(tour_input)
    for k, (poi, _arrival, departure) in enumerate(clocks):
        if poi.id.startswith("__end_b"):
            continue
        left = _minutes_left(planned_end, departure) or 15
        tail = replan(
            _tail_input(tour_input, position=(poi.lat, poi.lng), clock=departure, minutes=left),
            ctx_from(k, keep=(), visited=tuple(planned_ids[: k + 1])),
        )
        # Standing at the place the day ends at, a wrap-up has nowhere to send you
        # (W6.2, Rosemary: "'Straight to Musée d'Orsay' to a woman standing in the
        # Orsay"): the walk is done — the phone plays the day's close.
        at_finish = (
            finish is not None
            and haversine_m(finish[0], finish[1], poi.lat, poi.lng) <= OWN_PLACE_RADIUS_M
        )
        add(
            {"kind": "wrap_up_from", "stop_id": poi.id},
            k,
            tail,
            screen_text=(
                DAY_DONE_LINE if at_finish else screen_for(tail, departure, next_name=None)
            ),
            clock=departure,
        )

    for k, poi, arrival, departure in story:
        position = (poi.lat, poi.lng)
        after = tuple(planned_ids[k + 1 :])
        after_story = [p for p in route.pois[k + 1 :] if _is_story_stop(p)]
        next_name = after_story[0].name if after_story else None

        # R1.2 — STOP SKIPPED: the remainder from this stop's DOOR, its minutes granted to
        # the survivors through the one grant rule; the category walked away from spent.
        grants = grant_freed_seconds(
            [p for p in route.pois[k + 1 :]],
            visit_of(poi),
            visit_of=visit_of,
            ceiling_of=ceiling_of,
            protected_ids=set(protected_all),
        )
        left = _minutes_left(planned_end, arrival) or (
            sum(visit_of(p) for p in route.pois[k + 1 :]) // 60 + 30
        )
        skip_ctx = ctx_from(k, keep=after, visited=tuple(planned_ids[: k + 1]), grants=grants)
        # THE ABSENCE OF A CATEGORY IS NOT A CATEGORY (Phase 6 W6.11, measured on
        # the F&D day): most of the corpus carries the default 'other', so
        # spending it after ONE uncategorised skip marked every other stop spent
        # and the tail seated NOTHING — the entry shipped stop_ids [] while its
        # own screen text promised the next stop. Greta's satiation is per REAL
        # kind ("galleries spent, churches down"); 'other' never spends.
        if poi.place_category and poi.place_category != "other":
            skip_ctx = skip_ctx.model_copy(
                update={"spent_categories": (*skip_ctx.spent_categories, poi.place_category)}
            )
        tail = replan(
            _tail_input(tour_input, position=position, clock=arrival, minutes=left), skip_ctx
        )
        add(
            {"kind": "stop_skipped", "stop_id": poi.id},
            k,
            tail,
            screen_text=screen_for(tail, arrival, next_name=next_name),
            clock=arrival,
        )

        if after:
            if has_finish_clock:
                # R1.3 — LATE BANDS, planned at each band's far edge (an answer that holds
                # across the band): keep every stop and move the finish; a wall sheds
                # fabric; a protected thing at risk asks the ONE question.
                for lo, hi in LATE_BANDS:
                    late_clock = departure + timedelta(minutes=hi) if departure else None
                    left = _minutes_left(planned_end, late_clock) or 1
                    tail = replan(
                        _tail_input(tour_input, position=position, clock=late_clock, minutes=left),
                        ctx_from(k, keep=after, visited=tuple(planned_ids[: k + 1])),
                    )
                    q, default_arm, alt, at_risk = question_for(
                        k, position, late_clock, left, tail, after
                    )
                    add(
                        {"kind": "running_late", "stop_id": poi.id, "band_minutes": [lo, hi]},
                        k,
                        tail,
                        question=q,
                        default_arm=default_arm,
                        alt=alt,
                        at_risk=at_risk,
                        screen_text=(q if q else screen_for(tail, late_clock, next_name=next_name)),
                        clock=late_clock,
                    )
                # R1.4 — EARLY BANDS lengthen what is there; never a new place.
                for lo, hi in EARLY_BANDS:
                    early_clock = departure - timedelta(minutes=lo) if departure else None
                    grants = grant_freed_seconds(
                        [p for p in route.pois[k + 1 :]],
                        lo * 60,
                        visit_of=visit_of,
                        ceiling_of=ceiling_of,
                        protected_ids=set(protected_all),
                    )
                    left = _minutes_left(planned_end, early_clock) or 1
                    tail = replan(
                        _tail_input(tour_input, position=position, clock=early_clock, minutes=left),
                        ctx_from(k, keep=after, visited=tuple(planned_ids[: k + 1]), grants=grants),
                    )
                    add(
                        {"kind": "running_early", "stop_id": poi.id, "band_minutes": [lo, hi]},
                        k,
                        tail,
                        screen_text=screen_for(tail, early_clock, next_name=next_name),
                        clock=early_clock,
                    )
            else:
                # An OPEN day (no clock to be late against): band by MINUTES LEFT.
                for lo, hi in MINUTES_LEFT_BANDS:
                    tail = replan(
                        _tail_input(
                            tour_input, position=position, clock=departure, minutes=max(lo, 1)
                        ),
                        ctx_from(k, keep=after, visited=tuple(planned_ids[: k + 1])),
                    )
                    add(
                        {"kind": "minutes_left", "stop_id": poi.id, "band_minutes": [lo, hi]},
                        k,
                        tail,
                        screen_text=screen_for(tail, departure, next_name=next_name),
                        clock=departure,
                    )

    # R1.5 — PROMISE AT RISK, for the person's own promises only: the answer from the
    # stop before it, at the widest late edge, so a promise missed for a reason no band
    # measured (a long queue, a shut door) still has its question ready.
    for j, poi, _arrival, _departure in story:
        if poi.id not in protected or planned_end is None:
            continue
        prev = next(((i, p, a, d) for (i, p, a, d) in reversed(story) if i < j), None)
        if prev is None:
            continue
        i, prev_poi, _a, prev_dep = prev
        late_clock = prev_dep + timedelta(minutes=LATE_BANDS[-1][1]) if prev_dep else None
        keep = tuple(planned_ids[i + 1 :])
        left = _minutes_left(planned_end, late_clock) or 1
        tail = replan(
            _tail_input(
                tour_input, position=(prev_poi.lat, prev_poi.lng), clock=late_clock, minutes=left
            ),
            ctx_from(i, keep=keep, visited=tuple(planned_ids[: i + 1])),
        )
        q, default_arm, alt, at_risk = question_for(
            i, (prev_poi.lat, prev_poi.lng), late_clock, left, tail, keep
        )
        add(
            {"kind": "promise_at_risk", "stop_id": poi.id},
            i,
            tail,
            question=q,
            default_arm=default_arm,
            alt=alt,
            at_risk=at_risk or poi.id,
            screen_text=(q if q else screen_for(tail, late_clock, next_name=poi.name)),
            clock=late_clock,
        )

    # M6 — DOOR CLOSED: every story stop whose hours are a GUESS and whose visit
    # goes inside carries a standby — a map-sourced place not on the day, open at
    # arrival (pre-filtered by _clock_exclusion_reason, THE one definition), nearest
    # to the closed door. The planner force-seats it (protected_poi_ids, not the R1.5
    # "person asked" meaning — at_risk names the guessed stop explicitly) alongside
    # the remaining stops in a tail that skips the guessed stop.
    planned_set = set(planned_ids)
    country = country_code(tour_input.city_slug)
    for k, poi, arrival, _departure in story:
        if not hours_are_guessed(poi):
            continue
        if not route.visit_goes_inside.get(poi.id, False):
            continue
        if arrival is None:
            continue
        position = (poi.lat, poi.lng)
        best_standby = min(
            (
                c
                for c in snapshot.pois
                if c.id not in planned_set
                and c.opening_hours_source == HOURS_SOURCE_MAP
                and _is_story_stop(c)
                and _clock_exclusion_reason(
                    c.opening_hours, c.opening_hours_source, arrival, 1, country=country
                )
                is None
            ),
            key=lambda c: haversine_m(position[0], position[1], c.lat, c.lng),
            default=None,
        )
        if best_standby is None:
            continue
        after = tuple(planned_ids[k + 1 :])
        left = _minutes_left(planned_end, arrival) or (
            sum(visit_of(p) for p in route.pois[k + 1 :]) // 60 + 30
        )
        keep = (*after, best_standby.id)
        ctx = ctx_from(k, keep=keep, visited=tuple(planned_ids[: k + 1]))
        ctx = ctx.model_copy(
            update={"protected_poi_ids": (*ctx.protected_poi_ids, best_standby.id)}
        )
        alt = replan(
            _tail_input(tour_input, position=position, clock=arrival, minutes=left),
            ctx,
        )
        if alt is None or best_standby.id not in {p.id for p in alt.pois}:
            continue
        q = f"{poi.name} might be closed — carry to {best_standby.name} instead?"
        add(
            {"kind": "door_closed", "stop_id": poi.id},
            k,
            tail=None,
            question=q,
            default_arm="keep",
            alt=alt,
            at_risk=poi.id,
            screen_text=q,
            clock=arrival,
        )

    return ContingencySet(
        plan_version=plan_version,
        retime_tolerance_seconds=RETIME_TOLERANCE_SECONDS,
        entries=tuple(entries),
        authoring_units=len(new_stop_ids),
        notes=tuple(notes),
    )
