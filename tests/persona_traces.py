"""THE persona trace runner — the eleven days as replayable GPS-and-behaviour
streams through the LIVE session endpoints (design §7.4; plan S8.5).

Design §7.4: "Each persona file is a minute-by-minute script, so each becomes a
replayable GPS-and-behaviour trace through the session loop." Design §11 says
where they run: "persona traces are testable off-device" — so a trace is a
scripted position-and-behaviour stream against `POST /trips/generate`,
`POST /trips/{id}/compose`, `GET /trips/{id}/session` and
`POST /trips/{id}/session/replan`, never the simulator.

**Extends** — W5.12's scripted position-stream mechanism, named precisely
(plan S8.5's own field; §0.4):
`specs/2026-08-07-tour-algorithm-redesign/evidence/phase5-session/w512_setup.py`
(mint an identity on the dev graph -> generate -> compose -> GET the session) and
its sibling `w512_replan.py` (POST `/session/replan` with the phone's position,
its two clocks and `next_stop_index`, then re-read the session). That mechanism
is exactly what a trace needs and it could not be extended where it lives: both
are evidence scripts under `specs/`, they drive a `make api` on :8000, they
spend a real provider call per stop, and §0.9.5 deletes them with the phase — so
nothing in them can GATE anything. This module is the same mechanism, moved into
the test tree: same endpoints, same request shapes, same call order, driven
through a live-corpus `TestClient` at $0 (the money guard in `tests/conftest.py`
hands the compose path `OfflinePremiumExecutor`). No second session client, no
second planner, no second clock — every number a trace asserts on is read off
the wire the server wrote.

THE ELEVEN DAYS are not re-derived here either. `DAYS` below is the request set
`evidence/phase6-narration/w612_days.py` extracted from `docs/personas/*.md` for
W6.12 and W7.11 re-ran unchanged — the same start coordinates, clocks, lenses,
party presets and end-hardness. A twelfth spelling of Camille's request would
make a trace measure a day no panel ever judged. Their extraction notes are in
`evidence/phase6-narration/w612-requests.md`.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any

from src.tour.routing import haversine_m

#: The disposable identity these traces walk as. Its own ids (the W5.12 harness
#: has its own, so does tests/test_trip_api.py): a trace writes Trips and
#: ItineraryItems to the live dev graph and deletes them again.
TRACE_USER_ID = "s85-persona-trace-user"
TRACE_USER_EMAIL = "s85-persona-trace@example.test"
TRACE_PROFILE_ID = "s85-persona-trace-profile"

#: The named places, as the corpus places them — w612_days.py's own table.
PLACES: dict[str, tuple[float, float]] = {
    "Rue Royale": (48.8684, 2.3227),
    "Notre-Dame": (48.852966, 2.349902),
    "Place des Vosges": (48.8555, 2.3656),
    "Gare du Nord": (48.8809, 2.3553),
    "Musée d'Orsay": (48.859962, 2.326561),
    "Père-Lachaise gate": (48.8615, 2.3933),
    "Palais-Royal": (48.8637, 2.3371),
    "Place de la Bastille": (48.8532, 2.3692),
    "Hôtel de Ville": (48.8564, 2.3514),
    "Place Dauphine": (48.856407, 2.342655),
    "Place de la Concorde": (48.8656, 2.3212),
    "Châtelet": (48.8583, 2.3470),
}

#: persona -> the request THEY would type (w612_days.py's `DAYS`, unchanged).
#: Each entry's citation is its persona file; the weekday dates match each
#: file's named weekday, and Sofia's is a December Friday for the dark.
DAYS: dict[str, dict] = {
    # 01-architecture-pilgrim.md: Rue Royale -> Notre-Dame, five hours, architecture.
    "camille": dict(start=PLACES["Rue Royale"], end=PLACES["Notre-Dame"], duration_min=300,
                    start_datetime="2026-08-19T10:00", lenses=["historic_arch"],
                    end_hardness="open"),
    # 02-dark-history-walker.md: the same request, one word changed.
    "theo": dict(start=PLACES["Rue Royale"], end=PLACES["Notre-Dame"], duration_min=300,
                 start_datetime="2026-08-19T10:00", lenses=["dark_history"],
                 end_hardness="open"),
    # 03-family-with-children.md: Place des Vosges, loop, two hours, stories.
    "nadia": dict(start=PLACES["Place des Vosges"], duration_min=120, round_trip=True,
                  start_datetime="2026-08-22T10:30", party="family",
                  lenses=["stories_characters"], end_hardness="open"),
    # 04-layover-sprinter.md: Gare du Nord and back, three hours, the 16:40 wall.
    "marcus": dict(start=PLACES["Gare du Nord"], end=PLACES["Gare du Nord"],
                   duration_min=180, start_datetime="2026-08-20T13:30",
                   party="with_luggage", end_hardness="wall"),
    # 05-step-free-visitor.md: the Orsay and back, three hours, art, step-free.
    #
    # `narration_density="more"` is HER OWN DIAL, and it is on the request for a
    # recorded reason (Phase 8 S8.4, phase8-ledger.md): the serving gate (§7.2)
    # measures her echoed day at ~0.117 audio-per-walking against C3's 0.12
    # floor — a floor whose calibration corpus held no take-it-easy day (the
    # W8.6 owner row). Her own carried ask at W7.13 is MORE narration at the
    # bench, so the dial and the floor agree, and the day stays hers: bench
    # seated, 13-minute legs, step-free. The same re-derivation
    # tests/test_trip_api.py made twice for the same reason.
    "rosemary": dict(start=PLACES["Musée d'Orsay"], duration_min=180, round_trip=True,
                     start_datetime="2026-08-19T14:00", party="take_it_easy",
                     lenses=["visual_art"], end_hardness="open",
                     narration_density="more"),
    # 06-resident-novelty-seeker.md: Père-Lachaise gate, open walk, two hours.
    "julien": dict(start=PLACES["Père-Lachaise gate"], duration_min=120,
                   start_datetime="2026-08-23T09:30", lenses=["hidden_history"],
                   end_hardness="open"),
    # 07-rainy-tuesday.md: Palais-Royal, three hours, art and design, RAIN.
    "aiko": dict(start=PLACES["Palais-Royal"], duration_min=180,
                 start_datetime="2026-08-18T14:00", lenses=["visual_art", "modern_design"],
                 weather="rain", end_hardness="open"),
    # 08-second-language-listener.md: Bastille -> Hôtel de Ville, 150 min, history.
    "paulo": dict(start=PLACES["Place de la Bastille"], end=PLACES["Hôtel de Ville"],
                  duration_min=150, start_datetime="2026-08-21T10:00",
                  lenses=["history"], end_hardness="firm"),
    # 09-couple-who-would-rather-talk.md: Place Dauphine, open walk, three hours.
    "fd": dict(start=PLACES["Place Dauphine"], duration_min=180,
               start_datetime="2026-08-23T15:00", party="couple", weather="dry",
               end_hardness="open"),
    # 10-day-two-of-five.md: Concorde, open walk, three hours, art and history.
    "greta": dict(start=PLACES["Place de la Concorde"], duration_min=180,
                  start_datetime="2026-08-20T10:00", lenses=["visual_art", "history"],
                  end_hardness="open"),
    # 11-solo-after-dark.md: Palais-Royal -> Châtelet, 150 min, a December Friday.
    "sofia": dict(start=PLACES["Palais-Royal"], end=PLACES["Châtelet"], duration_min=150,
                  start_datetime="2026-12-04T14:30", lenses=["history"],
                  end_hardness="firm"),
}


def request_body(name: str, *, profile_id: str = TRACE_PROFILE_ID) -> dict:
    """The persona's own request as the phone sends it — w612_days.py's shape."""
    spec = dict(DAYS[name])
    start = spec.pop("start")
    end = spec.pop("end", None)
    return {
        "profile_id": profile_id,
        "center_lat": start[0],
        "center_lng": start[1],
        "duration_min": spec.pop("duration_min"),
        "start_date": spec["start_datetime"][:10],
        "end_date": spec["start_datetime"][:10],
        "start_time": spec.pop("start_datetime")[11:],
        **({"end_lat": end[0], "end_lng": end[1]} if end else {}),
        **spec,
    }


@dataclass
class Step:
    """ONE step of a scripted stream: where the phone was, what it reported, the
    version it reported AGAINST, and the day it was handed back."""

    label: str
    at: tuple[float, float]
    on_the_leg: bool
    next_stop_index: int
    before: dict
    reply: dict
    #: The door this step reported SHUT, when it reported one (Docs/adr/0006
    #: rule 5). Recorded so an invariant can follow that place through every
    #: later version without re-deriving which stop the step stood at.
    reported_closed: str | None = None

    @property
    def served(self) -> bool:
        return "refusal" not in self.reply

    @property
    def ahead(self) -> list[dict]:
        """The stops still to come in the version this step reported AGAINST —
        `next_stop_index` is the phone's own index into it (design §4.6)."""
        return list(self.before.get("stops", []))[self.next_stop_index :]


@dataclass
class Trace:
    """One persona's day, replayable: what the wire said at each door.

    `session` is the day the person stands in (GET /session). `refused_at` and
    `refusal` are set INSTEAD when the product refused this day — a refusal is a
    finding to record, never a retry into a different day (the W6.12 discipline),
    and never a silently thinner day (W8.2 R8).
    """

    name: str
    request: dict
    trip_id: str | None = None
    session: dict | None = None
    refused_at: str | None = None
    refusal: Any = None
    walk: list[Step] = field(default_factory=list)
    #: The stored session AFTER the walk — where the full contingency set of the
    #: last version lands (W5.12: the live reply is the day, the set follows).
    final_session: dict | None = None

    @property
    def served(self) -> bool:
        return self.session is not None

    @property
    def stops(self) -> list[dict]:
        return list((self.session or {}).get("stops", []))

    @property
    def promises(self) -> list[dict]:
        return list((self.session or {}).get("promises", []))

    @property
    def contingencies(self) -> list[dict]:
        return list((self.session or {}).get("contingencies", []))


def build_trace(client, name: str, *, profile_id: str = TRACE_PROFILE_ID) -> Trace:
    """W5.12's own call order, at $0: generate -> compose -> GET the session.

    Every non-200 is RECORDED on the trace and returned, never raised: the
    refusal contract is itself part of what a trace measures (§7.2 — a day that
    fails the floor is refused by name, with nothing persisted).
    """
    trace = Trace(name=name, request=request_body(name, profile_id=profile_id))
    gen = client.post("/api/v1/trips/generate", json=trace.request)
    if gen.status_code != 201:
        trace.refused_at, trace.refusal = "generate", _detail(gen)
        return trace
    trace.trip_id = gen.json()["trip_id"]
    composed = client.post(
        f"/api/v1/trips/{trace.trip_id}/compose", json={"route_id": f"{trace.trip_id}-opt1"}
    )
    if composed.status_code != 200:
        trace.refused_at, trace.refusal = "compose", _detail(composed)
        return trace
    got = client.get(f"/api/v1/trips/{trace.trip_id}/session")
    if got.status_code != 200:
        trace.refused_at, trace.refusal = "session", _detail(got)
        return trace
    trace.session = got.json()
    return trace


def _detail(response):
    try:
        return response.json().get("detail", response.json())
    except ValueError:  # pragma: no cover - a body that is not JSON is itself the finding
        return {"status": response.status_code, "text": response.text[:400]}


def replan(
    client,
    trace: Trace,
    *,
    label: str,
    at: tuple[float, float],
    before: dict,
    wall_elapsed_seconds: int,
    next_stop_index: int,
    tour_elapsed_seconds: int | None = None,
    **observations,
) -> Step:
    """ONE step of the scripted stream — w512_replan.py's own body, at $0.

    The phone reports where it is and its two clocks; the reply is the day it is
    standing in. The step is recorded on the trace and returned. A refusal (422)
    rides the reply under `"refusal"`, never raised — what the product answers a
    walker with IS what a trace measures.
    """
    body = {
        "lat": at[0],
        "lng": at[1],
        "wall_elapsed_seconds": int(wall_elapsed_seconds),
        "tour_elapsed_seconds": int(
            wall_elapsed_seconds if tour_elapsed_seconds is None else tour_elapsed_seconds
        ),
        "next_stop_index": int(next_stop_index),
        **observations,
    }
    resp = client.post(f"/api/v1/trips/{trace.trip_id}/session/replan", json=body)
    step = Step(
        label=label,
        at=at,
        on_the_leg=is_on_the_leg(at, list(before.get("stops", []))),
        next_stop_index=int(next_stop_index),
        before=before,
        reply=resp.json() if resp.status_code == 200 else {"refusal": _detail(resp)},
        reported_closed=observations.get("closed_stop_id"),
    )
    trace.walk.append(step)
    return step


def refetch(client, trace: Trace) -> dict:
    """The stored session as it stands now — the phone's next fetch. A live
    replan answers with the day and computes the FULL contingency set right
    after the reply (W5.12), so this is where that set is read."""
    resp = client.get(f"/api/v1/trips/{trace.trip_id}/session")
    return resp.json() if resp.status_code == 200 else {"refusal": _detail(resp)}


def elapsed_at(day: dict, stop: dict, *, extra_minutes: int = 0) -> int:
    """Seconds since the day's clock started, standing at `stop` with its visit
    finished — the phone's `wall_elapsed_seconds`, read off the wire's own
    `day_start_hhmm`, `start_time` and `dwell_seconds`."""
    started = minutes_of(day["day_start_hhmm"])
    here = minutes_of(stop["start_time"])
    return (here - started) * 60 + int(stop.get("dwell_seconds") or 0) + extra_minutes * 60


#: The linger, in minutes: past the widest precomputed late band (30-40), so the
#: answer cannot be a band's — it is the live path's own (W5.14's shape, where a
#: 46-minute linger dropped Rosemary's Orsay in silence).
LINGER_MINUTES = 46

#: Minutes left on the clock at the stream's last step, so that what the day still
#: promises CANNOT fit (design §7.4.1's actual case; §4.2's promise tier). Twenty:
#: under the shortest priced visit of any persona day, so a promised place is
#: genuinely at risk rather than merely late.
MINUTES_LEFT_AT_RISK = 20


def walk_the_day(client, trace: Trace) -> Trace:
    """REPLAY the persona's day as a scripted position-and-behaviour stream.

    Five steps, and the bound is stated rather than silent (§0.9.2(a) — only the
    relevant calls run; a trace is not a sweep): leaving the first stop on time;
    REPORTING A SHUT DOOR at the first door still ahead (Docs/adr/0006 rule 5);
    on the LEG between two footprints; a LINGER of 46 minutes at the next stop;
    and the TAIL from the last stop still ahead. Each step reports against the
    version the previous one handed back, because that is the day the person is
    now standing in (design §8.2 — the session has versions).
    """
    if not trace.served:
        return trace
    current = trace.session
    stops = list(current.get("stops", []))
    if len(stops) < 2:
        # A one-stop day has no leg to walk and no tail to replan: the stream is
        # the wrap-up alone, which the prefix invariant reads off the set.
        return trace

    here = stops[0]
    step = replan(
        client,
        trace,
        label="leaving the first stop",
        at=(here["lat"], here["lng"]),
        before=current,
        wall_elapsed_seconds=elapsed_at(current, here),
        next_stop_index=1,
    )
    if not step.served:
        return trace
    current = step.reply

    # THE SHUT DOOR (Docs/adr/0006 rule 5). A walker standing at a door the plan
    # sent them to can find it locked, and the day must answer: the stop leaves
    # the day, the report is counted on the place, and a guessed door with a
    # standby offers it. Reported against the version just handed back, from the
    # door itself, with every stop of that version still ahead.
    ahead = list(current.get("stops", []))
    shut = next((s for s in ahead if (s.get("trigger") or {}).get("door")), None)
    if shut is not None:
        step = replan(
            client,
            trace,
            label=f"reporting {shut.get('poi_name', 'a door')} shut",
            at=(shut["lat"], shut["lng"]),
            before=current,
            wall_elapsed_seconds=elapsed_at(trace.session, here),
            next_stop_index=0,
            closed_stop_id=shut["poi_id"],
        )
        if not step.served:
            return trace
        current = step.reply

    ahead = list(current.get("stops", []))
    if ahead:
        point = on_the_leg(here, ahead[0])
        step = replan(
            client,
            trace,
            label="on the leg",
            at=point,
            before=current,
            wall_elapsed_seconds=elapsed_at(trace.session, here) + 3 * 60,
            next_stop_index=0,
        )
        if not step.served:
            return trace
        current = step.reply

    ahead = list(current.get("stops", []))
    if ahead:
        lingered = ahead[0]
        step = replan(
            client,
            trace,
            label=f"lingering {LINGER_MINUTES} minutes",
            at=(lingered["lat"], lingered["lng"]),
            before=current,
            wall_elapsed_seconds=elapsed_at(
                trace.session, lingered, extra_minutes=LINGER_MINUTES
            ),
            next_stop_index=1 if len(ahead) > 1 else 0,
        )
        if not step.served:
            return trace
        current = step.reply

    ahead = list(current.get("stops", []))
    if ahead:
        last = ahead[-1]
        step = replan(
            client,
            trace,
            label="the tail",
            at=(last["lat"], last["lng"]),
            before=current,
            wall_elapsed_seconds=elapsed_at(trace.session, last),
            next_stop_index=len(ahead) - 1,
        )
        if step.served:
            current = step.reply

    ahead = list(current.get("stops", []))
    if ahead:
        # THE PROMISE AT RISK. The steps above never put one there: a lingering
        # walker still has hours, and the planner keeps a promised place because
        # it fits. §7.4.1 is about the case where it does NOT fit — the whole
        # point of "no promise is ever silently dropped" is a day that can no
        # longer hold everything. So the last step of the stream leaves
        # `MINUTES_LEFT_AT_RISK` on the clock: the remainder cannot hold what is
        # promised, and the server must either keep it and say the day runs over,
        # or ask the one question. What it may not do is drop it in silence.
        here = ahead[0]
        replan(
            client,
            trace,
            label="almost out of time",
            at=(here["lat"], here["lng"]),
            before=current,
            wall_elapsed_seconds=max(
                0, trace.request["duration_min"] * 60 - MINUTES_LEFT_AT_RISK * 60
            ),
            next_stop_index=0,
        )
    trace.final_session = refetch(client, trace)
    return trace


def resolved_cap_minutes(name: str) -> int | None:
    """This persona's LONGEST-SINGLE-WALK cap, in minutes, read from THE one
    preset table (`contract.resolve_party_axes`) — never restated here.

    design §2.4 and §4.5.3: the cap is the number a drop must re-check, and only
    the parties whose §2.4 row carries one have it (the family's "short" cell and
    with-luggage's "medium" cell carry no number in the design, deliberately —
    see `_PARTY_AXES`). An explicit `max_leg_minutes` on the request wins.
    """
    from src.tour.contract import TourInput, resolve_party_axes

    spec = DAYS[name]
    probe = TourInput(
        start=spec["start"],
        duration_min=spec["duration_min"],
        city_slug="paris",
        party=spec.get("party"),
        max_leg_minutes=spec.get("max_leg_minutes"),
    )
    return resolve_party_axes(probe).max_leg_minutes


# ---------------------------------------------------------------------------
# Reading the day off the wire. Nothing here computes a clock or a walk of its
# own (plan S5.10's sabotage line): every number is the server's, read back.
# ---------------------------------------------------------------------------


def minutes_of(hhmm: str) -> int:
    """"14:32" -> 872. The wire's own HH:MM, as minutes since midnight."""
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def leg_minutes(stops: list[dict]) -> list[tuple[str, str, int]]:
    """(from, to, minutes) for every walking leg BETWEEN the stops of a day, read
    off the wire's own clocks: a leg is the gap between one stop's departure
    (its `start_time` plus its `dwell_seconds`) and the next stop's arrival.

    THE SERVER'S CLOCK, never a second walking model — `stop_clocks` is the one
    re-timing expression (Phase 5 S5.10) and `start_time` is its view, so this
    reads the same walk the planner certified the leg cap against.
    """
    out: list[tuple[str, str, int]] = []
    for here, nxt in itertools.pairwise(stops):
        if not here.get("start_time") or not nxt.get("start_time"):
            continue
        departs = minutes_of(here["start_time"]) + int(here.get("dwell_seconds") or 0) // 60
        arrives = minutes_of(nxt["start_time"])
        gap = arrives - departs
        if gap < -12 * 60:  # the day crossed midnight
            gap += 24 * 60
        out.append((here.get("poi_name", ""), nxt.get("poi_name", ""), gap))
    return out


def footprint_m(stop: dict) -> float:
    """The stop's own footprint radius, off the wire (Phase 7 S7.3). A stop
    written before the placement rule carries none: treat it as a point."""
    trigger = stop.get("trigger") or {}
    return float(trigger.get("radius_m") or 0.0)


def on_the_leg(here: dict, there: dict) -> tuple[float, float]:
    """A scripted position that is genuinely ON A WALKING LEG: the midpoint
    between two stops. `assert_on_the_leg` proves it is outside both footprints
    before a trace claims anything about mid-leg behaviour."""
    return ((here["lat"] + there["lat"]) / 2.0, (here["lng"] + there["lng"]) / 2.0)


def is_on_the_leg(point: tuple[float, float], stops: list[dict]) -> bool:
    """True when this position is inside NO stop's footprint — the walker is
    between places, which is where §4.4.1 says speech may never land."""
    return all(
        haversine_m(point[0], point[1], stop["lat"], stop["lng"]) > footprint_m(stop)
        for stop in stops
    )
