"""THE audio placement rule — what plays where.

ONE definition of "at the stop", on the server, put on the wire. The phone SELECTS from
it, holds no policy, and invents no geometry of its own: until
this module the app decided what "at the stop" meant with two literal circles of its own
(10 m to start a piece, 40 m to be "at" a place), the same for a 140 m square and a
doorway, while the corpus had carried a measured footprint per place that nothing read.
Both circles are gone.

What the rule says, per stop and per day:

- ``StopTrigger`` — the place's own FOOTPRINT: a circle sized to the place (the corpus
  ``trigger_radius``). A walker is AT the stop inside it; the piece ARMS the moment the
  footprint is first touched — the edge they arrive by, never the pin. ``kind``
  is "circle" this phase: the corpus holds no line or polygon, so a street's line or a
  cemetery's walls are a CARRIED data row, and the field is what makes adding them an
  addition rather than a rework.
- ``PlacementPolicy`` — the day's rule for WHEN the armed piece starts: at arrival for
  every party but FAMILY, whose piece starts at the first standstill inside the footprint
  (a child crosses any circle again and again); and the radius of
  the person's OWN place — the start they set out from and the finish they named — which
  a round trip's return and the A→B sentinel take instead of a ten-metre dot (the finish
  counts once it is in sight, never at a dot by the water).

Told once is told: leaving and re-entering a footprint never replays; a tap does. That
is the phone's half of the rule, pinned by tests/test_session_seams.py.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.tour.contract import (
    END_B_SENTINEL_PREFIX,
    POI,
    Anchor,
    BeatRef,
    Route,
    Script,
    ScriptPOI,
    Sentence,
    TourInput,
)
from src.tour.render_md import stop_segment_text

#: The footprint of a place whose record was never measured: a DOOR, a plaque, a bench —
#: ten metres, the value the corpus uploader writes when a record carries none. The
#: footprint pass sizes the records still sitting at it by place kind; this is the floor,
#: never the rule.
FOOTPRINT_DEFAULT_M: float = 10.0

#: The radius of the person's OWN place — the start they set out from and the finish they
#: named. ONE definition, read by the contingency set (which promises the day visits
#: the named place, `own_place_ids`), by the finish name on the wire, and by the phone for
#: the start square and the finish. Sixty metres: a square's width, the
#: distance at which a person says "I am at the Orsay" of its forecourt.
OWN_PLACE_RADIUS_M: float = 60.0

#: Place kinds you are IN rather than ENTER (decided by place kind, never by a phone
#: signal): an arcade, a passage, a courtyard gate, a garden, a park, a square,
#: a bridge, a street, a market have no door — a roof is not a door — so a visit that the
#: plan priced "inside" one of them is still outdoors to the audio: the piece plays on.
OUTSIDE_KINDS: frozenset[str] = frozenset(
    {"arcade", "square", "garden", "park", "bridge", "street", "market"}
)


class StopTrigger(BaseModel):
    """Where ONE stop's piece plays: the place's footprint, as a circle this phase."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["circle"] = "circle"
    radius_m: float = Field(..., gt=0)
    #: The seconds of line the planner
    #: priced at this stop's arrival hour — read off THE one map (`Route.planned_queue_
    #: seconds`, priced at selection's one site), never a second reading of the queue
    #: fields. 0 = no line priced here: the piece keeps the arrival rule. > 0 = the line
    #: is the place: the piece waits for the first standstill inside the footprint (or,
    #: under the `tap` policy, for the person's tap).
    queue_seconds: int = Field(default=0, ge=0)
    #: This stop is a DOOR — the plan's own
    #: visit goes inside (`Route.visit_goes_inside`) and the place is a kind you enter,
    #: not one you are in (`OUTSIDE_KINDS`). At a door the piece ends at its current
    #: sentence when `outside_seconds` have run since it started, then the close plays;
    #: inside, nothing auto-plays and the keep-listening tap resumes it.
    door: bool = False
    #: The placed OUTSIDE seconds (`Route.planned_outside_seconds`): how long the person
    #: stands outside before the door — the only threshold a phone can see under a roof.
    outside_seconds: int = Field(default=0, ge=0)


class PlacementPolicy(BaseModel):
    """The day's placement policy — decided from the party, never a phone branch."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: "arrival": the armed piece starts as the footprint is entered. "standstill": it
    #: starts at the first standstill inside the footprint (the family day).
    start_at: Literal["arrival", "standstill"] = "arrival"
    #: The radius of the person's own place (start square, named finish).
    own_place_m: float = Field(default=OWN_PLACE_RADIUS_M, gt=0)
    #: How a QUEUED stop's piece starts. "auto": at the first standstill inside the
    #: footprint, for every other party; "tap": the screen offers and the person's tap
    #: starts it — the couple and the family, whose still phone is a conversation or a
    #: sandpit, never "in the line"; "none": a `wall` end prices no line, so no queue piece
    #: exists.
    queue_piece: Literal["auto", "tap", "none"] = "auto"
    #: The per-day policy is decided on the server from the party and the hardness, never
    #: a branch the phone invents — these two once rode the phone's `party` branch. The
    #: SENTENCE CAP: seconds a tapped or door-cut piece may run to reach its sentence end —
    #: 8, the family 0 (cut at once), a `wall` day 5.
    sentence_cap_s: float = Field(default=8.0, ge=0)
    #: The RESUME rule: when an interruption ends inside the stop's footprint the
    #: piece resumes by itself ("auto") — the COUPLE by their tap ("tap"; a couple
    #: restarts when their conversation pauses).
    interruption_resume: Literal["auto", "tap"] = "auto"


def place_stop(
    poi: POI,
    *,
    queue_seconds: int = 0,
    goes_inside: bool = False,
    outside_seconds: int = 0,
) -> StopTrigger:
    """The footprint of one stop: the corpus radius; the own-place radius for the A→B
    finish sentinel (a waypoint at the person's named end, not a place); the door-sized
    default for a record never measured. ``queue_seconds`` is the planner's priced line
    at this stop, 0 when none was priced; ``goes_inside`` and ``outside_seconds``
    are the planner's own shape — a door only where the place is a kind you enter."""
    if poi.id.startswith(END_B_SENTINEL_PREFIX):
        return StopTrigger(radius_m=OWN_PLACE_RADIUS_M)
    return StopTrigger(
        radius_m=poi.trigger_radius or FOOTPRINT_DEFAULT_M,
        queue_seconds=int(queue_seconds),
        door=bool(goes_inside) and poi.place_category not in OUTSIDE_KINDS,
        outside_seconds=int(outside_seconds),
    )


def place_stops(route: Route) -> dict[str, StopTrigger]:
    """Every stop of a route placed, keyed by poi id — THE call generate and compose
    make once, whose answer rides each stop onto the wire and into the item. The queue,
    the door and the outside seconds come off the Route's three maps —
    priced once, at selection's one site."""
    return {
        poi.id: place_stop(
            poi,
            queue_seconds=int(route.planned_queue_seconds.get(poi.id, 0)),
            goes_inside=bool(route.visit_goes_inside.get(poi.id, False)),
            outside_seconds=int(route.planned_outside_seconds.get(poi.id, 0)),
        )
        for poi in route.pois
    }


def place_day(tour_input: TourInput) -> PlacementPolicy:
    """The day's policy from the party and the hardness: the family piece waits for the
    first standstill; everyone else starts at the edge.
    A queued stop's piece is automatic at the standstill, a tap for the couple and the
    family, and nothing under a wall. The sentence cap (8 s; family 0; wall 5)
    and the resume rule (auto; the couple by tap) ride here too: the
    policy is the server's, never a phone branch on the party."""
    if tour_input.end_hardness == "wall":
        queue_piece = "none"
    elif tour_input.party in ("couple", "family"):
        queue_piece = "tap"
    else:
        queue_piece = "auto"
    if tour_input.party == "family":
        sentence_cap_s = 0.0
    elif tour_input.end_hardness == "wall":
        sentence_cap_s = 5.0
    else:
        sentence_cap_s = 8.0
    return PlacementPolicy(
        start_at="standstill" if tour_input.party == "family" else "arrival",
        queue_piece=queue_piece,
        sentence_cap_s=sentence_cap_s,
        interruption_resume="tap" if tour_input.party == "couple" else "auto",
    )


# ---------------------------------------------------------------------------
# THE CHAPTERS of a marquee stop: segments ONLY where a person placed the coordinates,
# marquee anchors only; outdoor auto at a standstill, interior by tap, couple and
# family all taps, never re-triggered. The reviewed anchors ride the corpus record
# (`POI.anchors`); THE one rule here cuts a stop's STORY at them: a beat whose
# `sub_location` an anchor names is told AT that anchor as the anchor's own piece,
# everything else stays the arrival story, in order. Glue stays with the story.
# ---------------------------------------------------------------------------


class StopSegment(BaseModel):
    """ONE CHAPTER of a stop, on the wire: the reviewed anchor's place and radius (the
    phone's one `_within` reads them), whether it is under a roof, its text, and — once
    the voicing pass has run — its own file and measured length (the leg-piece
    precedent). `audio_hash` is the voicing pass's skip guard, stored beside the url."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str = Field(..., min_length=1)
    lat: float
    lng: float
    radius_m: float = Field(..., gt=0)
    indoor: bool = False
    narration: str = Field(..., min_length=1)
    audio_url: str | None = None
    audio_duration_sec: float | None = None
    audio_hash: str | None = None


def place_anchors(route: Route) -> dict[str, tuple[Anchor, ...]]:
    """poi id -> its reviewed anchors, for every stop of the route that carries any —
    the map the adapter takes beside `place_stops(route)`. A stop without anchors is
    absent: its story stays whole."""
    return {poi.id: poi.anchors for poi in route.pois if poi.anchors}


def anchor_of(
    selected_pois: Sequence[ScriptPOI],
    beats_by_id: Mapping[str, BeatRef],
    anchors_by_poi: Mapping[str, tuple[Anchor, ...]],
) -> Callable[[Sentence], Anchor | None]:
    """THE resolver: the reviewed anchor a sentence is told AT, or None. A beat sentence
    whose beat carries a `sub_location` one of its stop's anchors names resolves to that
    anchor; glue, an unlabelled beat, a label no anchor claims, and a stop with no
    anchors resolve to None (the story). The first anchor naming a label wins."""
    by_stop: dict[int, dict[str, Anchor]] = {}
    for idx, sp in enumerate(selected_pois):
        table: dict[str, Anchor] = {}
        for anchor in anchors_by_poi.get(sp.id, ()):
            for sub in anchor.sub_locations:
                table.setdefault(sub, anchor)
        by_stop[idx] = table

    def resolve(sentence: Sentence) -> Anchor | None:
        if sentence.source_type != "beat":
            return None
        table = by_stop.get(sentence.stop_idx)
        if not table:
            return None
        ref = beats_by_id.get(sentence.source_id)
        sub = ref.sub_location if ref is not None else None
        return table.get(sub) if sub else None

    return resolve


def place_segments(
    script: Script, resolve: Callable[[Sentence], Anchor | None]
) -> dict[int, tuple[StopSegment, ...]]:
    """stop index -> its chapters, in first-appearance order, each the anchor's place,
    radius and roof flag with the sentences told there (through the one text cutter,
    `render_md.stop_segment_text`). Empty for a day with no anchored sentence."""
    return {
        idx: tuple(
            StopSegment(
                label=anchor.label,
                lat=anchor.lat,
                lng=anchor.lng,
                radius_m=anchor.radius_m,
                indoor=anchor.indoor,
                narration=text,
            )
            for anchor, text in chapters
        )
        for idx, chapters in stop_segment_text(script, resolve).items()
    }
